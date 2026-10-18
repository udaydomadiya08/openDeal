"""Unit tests for official SEC EDGAR client and XBRL fact parser."""

import json
from pathlib import Path
import httpx
import pytest

from opendeal.config.settings import SECConfig
from opendeal.connectors.sec.client import (
    SECClient,
    SECClientError,
    SECRateLimitError,
    SECNotFoundError,
)
from opendeal.connectors.sec.parser import SECParser
from opendeal.core.types import (
    Currency,
    FiscalPeriodType,
    Sign,
    ValidationStatus,
)
from opendeal.entities.models import CompanyEntity

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "sec"


class MockTransport(httpx.BaseTransport):
    """Custom HTTP transport to return deterministically mocked responses."""

    def __init__(self, status_code: int = 200, json_data: dict = None, raise_network_err: bool = False):
        self.status_code = status_code
        self.json_data = json_data or {}
        self.raise_network_err = raise_network_err
        self.call_count = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.call_count += 1
        if self.raise_network_err:
            raise httpx.ConnectError("Simulated connection failure", request=request)
        return httpx.Response(
            status_code=self.status_code,
            json=self.json_data,
            request=request,
        )


class TestSECConfigAndClient:
    """Test SEC EDGAR Fair-Access policy, User-Agent enforcement, and retry behavior."""

    def test_user_agent_validation(self):
        """User-Agent must include contact email to comply with SEC rules."""
        valid_cfg = SECConfig(user_agent="OpenDeal Platform contact@opendeal.org")
        assert "contact@opendeal.org" in valid_cfg.user_agent

        with pytest.raises(ValueError):
            SECConfig(user_agent="AnonymousScraperNoEmail")

    def test_cik_normalization(self):
        """CIK is normalized to 10-digit zero-padded string."""
        assert SECClient.normalize_cik("320193") == "0000320193"
        assert SECClient.normalize_cik("0000320193") == "0000320193"
        assert SECClient.normalize_cik("CIK 320193") == "0000320193"

        with pytest.raises(ValueError):
            SECClient.normalize_cik("NoDigitsHere")

    def test_client_mock_200_success(self):
        """Successful request returns parsed json, url, and payload sha256 hash."""
        transport = MockTransport(status_code=200, json_data={"cik": "0000320193", "name": "Apple Inc."})
        client = SECClient(
            config=SECConfig(user_agent="TestAgent test@example.com", rate_limit_per_second=10.0),
            transport=transport,
        )

        data, url, sha = client.get_submissions("320193")
        assert data["name"] == "Apple Inc."
        assert "submissions/CIK0000320193.json" in url
        assert len(sha) == 64  # SHA-256 length
        assert transport.call_count == 1
        client.close()

    def test_client_mock_404_not_found(self):
        """404 response raises SECNotFoundError."""
        transport = MockTransport(status_code=404)
        client = SECClient(
            config=SECConfig(user_agent="TestAgent test@example.com", rate_limit_per_second=10.0),
            transport=transport,
        )

        with pytest.raises(SECNotFoundError):
            client.get_submissions("9999999999")
        client.close()

    def test_client_mock_429_rate_limit(self):
        """429 response backs off and retries up to max_retries."""
        transport = MockTransport(status_code=429)
        client = SECClient(
            config=SECConfig(user_agent="TestAgent test@example.com", max_retries=1, rate_limit_per_second=10.0),
            transport=transport,
        )

        with pytest.raises(SECRateLimitError):
            client.get_company_facts("320193")
        assert transport.call_count == 2  # 1 initial + 1 retry
        client.close()

    def test_client_mock_network_error(self):
        """Network connection failure retries and raises SECClientError."""
        transport = MockTransport(raise_network_err=True)
        client = SECClient(
            config=SECConfig(user_agent="TestAgent test@example.com", max_retries=1, rate_limit_per_second=10.0),
            transport=transport,
        )

        with pytest.raises(SECClientError):
            client.get_company_facts("320193")
        assert transport.call_count == 2
        client.close()


class TestSECParser:
    """Test parsing of SEC submissions and XBRL facts from recorded fixtures."""

    @pytest.fixture
    def apple_submissions_data(self) -> dict:
        fixture_path = FIXTURES_DIR / "apple_submissions.json"
        with open(fixture_path, "r", encoding="utf-8") as f:
            return json.load(f)

    @pytest.fixture
    def apple_companyfacts_data(self) -> dict:
        fixture_path = FIXTURES_DIR / "apple_companyfacts_sample.json"
        with open(fixture_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_parse_submissions_to_entity(self, apple_submissions_data):
        """Submissions payload is parsed into CompanyEntity with exact metadata."""
        entity = SECParser.parse_submissions_to_entity(apple_submissions_data)

        assert entity.legal_name == "Apple Inc."
        assert entity.jurisdiction == "US-CA"
        assert entity.ticker == "AAPL"
        assert entity.primary_exchange == "Nasdaq"
        assert entity.get_identifier("CIK") == "0000320193"
        assert entity.get_identifier("TICKER") == "AAPL"
        assert entity.get_identifier("IRS_EIN") == "942404110"
        assert entity.sic_code == "3571"
        assert entity.sic_description == "ELECTRONIC COMPUTERS"

    def test_parse_company_facts_comprehensive(self, apple_companyfacts_data, apple_submissions_data):
        """Company facts payload parses into evidence and facts with duration/instant accuracy."""
        entity = SECParser.parse_submissions_to_entity(apple_submissions_data)
        source_url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"

        source, evidence_records, facts = SECParser.parse_company_facts_to_records(
            facts_data=apple_companyfacts_data,
            entity=entity,
            source_url=source_url,
            payload_hash="sample_hash_123",
        )

        assert source.document_id == "CIK0000320193"
        assert source.publisher == "US Securities and Exchange Commission (SEC EDGAR)"
        assert len(evidence_records) > 0
        assert len(facts) > 0

        # Group facts by canonical variable and period
        rev_facts = [f for f in facts if f.canonical_variable == "revenue"]
        assert len(rev_facts) == 4

        # 1. Annual Revenue (10-K, FY 2023)
        annual_rev = next(f for f in rev_facts if f.fiscal_period == FiscalPeriodType.ANNUAL)
        assert annual_rev.normalized_value == 383285000000.0
        assert annual_rev.reported_value == 383285000000.0
        assert annual_rev.currency == Currency.USD
        assert annual_rev.sign == Sign.POSITIVE
        assert annual_rev.is_instant is False
        assert len(annual_rev.evidence_ids) == 1

        # 2. Single Quarter Q1 Revenue
        q1_rev = next(f for f in rev_facts if f.fiscal_period == FiscalPeriodType.Q1)
        assert q1_rev.normalized_value == 117154000000.0

        # 3. Single Quarter Q2 Revenue (start 2023-01-01 to 2023-04-01, ~90 days)
        q2_single = next(f for f in rev_facts if f.fiscal_period == FiscalPeriodType.Q2)
        assert q2_single.normalized_value == 94836000000.0

        # 4. Cumulative Year-to-Date Q2 Revenue (start 2022-10-01 to 2023-04-01, ~182 days)
        q2_ytd = next(f for f in rev_facts if f.fiscal_period == FiscalPeriodType.YTD_Q2)
        assert q2_ytd.normalized_value == 211990000000.0

        # 5. Instant Balance Sheet Fact (Total Assets)
        asset_facts = [f for f in facts if f.canonical_variable == "total_assets"]
        assert len(asset_facts) == 1
        assert asset_facts[0].is_instant is True
        assert asset_facts[0].fiscal_period == FiscalPeriodType.INSTANT
        assert asset_facts[0].normalized_value == 352583000000.0

        # 6. Explicit Reported Zero (Operating Income = 0)
        op_inc_facts = [f for f in facts if f.canonical_variable == "operating_income"]
        assert len(op_inc_facts) == 1
        assert op_inc_facts[0].reported_value == 0.0
        assert op_inc_facts[0].normalized_value == 0.0
        assert op_inc_facts[0].sign == Sign.ZERO
        assert op_inc_facts[0].validation_status == ValidationStatus.VALID

    def test_parse_malformed_facts_handled_gracefully(self, apple_submissions_data):
        """Malformed company facts fixture does not crash the parser."""
        fixture_path = FIXTURES_DIR / "malformed_companyfacts.json"
        with open(fixture_path, "r", encoding="utf-8") as f:
            malformed_data = json.load(f)

        entity = SECParser.parse_submissions_to_entity(apple_submissions_data)
        source, evidence, facts = SECParser.parse_company_facts_to_records(
            facts_data=malformed_data,
            entity=entity,
            source_url="https://example.com",
        )

        assert len(facts) == 0
        assert len(evidence) == 0
        assert source is not None
