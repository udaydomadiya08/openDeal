"""Unit tests for UK Companies House API client and parser."""

import json
from pathlib import Path
import httpx
import pytest

from opendeal.config.settings import CompaniesHouseConfig
from opendeal.connectors.companies_house.client import (
    CompaniesHouseAuthError,
    CompaniesHouseClient,
    CompaniesHouseClientError,
    CompaniesHouseNotFoundError,
    CompaniesHouseRateLimitError,
)
from opendeal.connectors.companies_house.parser import CompaniesHouseParser

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "companies_house"


@pytest.fixture
def mock_profile_data():
    with open(FIXTURES_DIR / "company_profile_sample.json", "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def mock_filing_history_data():
    with open(FIXTURES_DIR / "filing_history_sample.json", "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def mock_officers_data():
    with open(FIXTURES_DIR / "officers_sample.json", "r", encoding="utf-8") as f:
        return json.load(f)


class TestCompaniesHouseClient:
    """Test client offline HTTP operations via MockTransport."""

    def test_get_company_profile_200_success(self, mock_profile_data):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/company/01234567"
            assert "Basic " in request.headers["authorization"]
            return httpx.Response(200, json=mock_profile_data)

        config = CompaniesHouseConfig(api_key="test_api_key_secret", rate_limit_per_second=100.0)
        transport = httpx.MockTransport(handler)

        with CompaniesHouseClient(config=config, transport=transport) as client:
            data, url, payload_hash = client.get_company_profile("1234567")

            assert data["company_name"] == "ACME BRITISH HOLDINGS LIMITED"
            assert "01234567" in url
            assert len(payload_hash) == 64

    def test_get_filing_history_200_success(self, mock_filing_history_data):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/company/01234567/filing-history"
            return httpx.Response(200, json=mock_filing_history_data)

        config = CompaniesHouseConfig(api_key="test_key", rate_limit_per_second=100.0)
        transport = httpx.MockTransport(handler)

        with CompaniesHouseClient(config=config, transport=transport) as client:
            data, url, _ = client.get_filing_history("01234567")
            assert len(data["items"]) == 3
            assert data["items"][0]["barcode"] == "X9ABC123"

    def test_get_officers_200_success(self, mock_officers_data):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/company/01234567/officers"
            return httpx.Response(200, json=mock_officers_data)

        config = CompaniesHouseConfig(api_key="test_key", rate_limit_per_second=100.0)
        transport = httpx.MockTransport(handler)

        with CompaniesHouseClient(config=config, transport=transport) as client:
            data, url, _ = client.get_officers("01234567")
            assert len(data["items"]) == 2
            assert data["items"][0]["name"] == "SMITH, John Alexander"

    def test_client_404_not_found(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="Not Found")

        config = CompaniesHouseConfig(api_key="test_key", rate_limit_per_second=100.0)
        transport = httpx.MockTransport(handler)

        with CompaniesHouseClient(config=config, transport=transport) as client:
            with pytest.raises(CompaniesHouseNotFoundError) as exc:
                client.get_company_profile("99999999")
            assert "resource not found" in str(exc.value)

    def test_client_401_auth_failure(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="Unauthorized")

        config = CompaniesHouseConfig(api_key="invalid_key", rate_limit_per_second=100.0)
        transport = httpx.MockTransport(handler)

        with CompaniesHouseClient(config=config, transport=transport) as client:
            with pytest.raises(CompaniesHouseAuthError) as exc:
                client.get_company_profile("01234567")
            assert "authentication failed" in str(exc.value)

    def test_client_429_rate_limit_exceeded(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="Rate limit exceeded")

        config = CompaniesHouseConfig(api_key="test_key", rate_limit_per_second=100.0, max_retries=1)
        transport = httpx.MockTransport(handler)

        with CompaniesHouseClient(config=config, transport=transport) as client:
            with pytest.raises(CompaniesHouseRateLimitError) as exc:
                client.get_company_profile("01234567")
            assert "rate limit exceeded" in str(exc.value)


class TestCompaniesHouseParser:
    """Test parsing logic and metadata classification."""

    def test_parse_company_profile(self, mock_profile_data):
        entity = CompaniesHouseParser.parse_company_profile(mock_profile_data)

        assert entity.legal_name == "ACME BRITISH HOLDINGS LIMITED"
        assert entity.sic_code == "62012"

        # CRN identifier
        crn = entity.get_identifier("COMPANIES_HOUSE")
        assert crn == "01234567"
        assert entity.metadata["crn"] == "01234567"

        # Accounts classification
        assert entity.metadata["account_type"] == "total-exemption-small"
        assert "SMALL_COMPANY" in entity.metadata["public_filing_statement_availability"]
        assert "Crown copyright" in entity.metadata["attribution"]

    def test_statement_availability_classification(self):
        parser = CompaniesHouseParser
        assert "MICRO_ENTITY" in parser._determine_statement_availability("micro-entity")
        assert "SMALL_COMPANY" in parser._determine_statement_availability("total-exemption-small")
        assert "DORMANT" in parser._determine_statement_availability("dormant")
        assert "FULL_ACCOUNTS" in parser._determine_statement_availability("full")

    def test_parse_filing_history(self, mock_filing_history_data):
        filings = CompaniesHouseParser.parse_filing_history(mock_filing_history_data, entity_id="ent_uk_acme")

        assert len(filings) == 3
        first = filings[0]
        assert first["entity_id"] == "ent_uk_acme"
        assert first["category"] == "accounts"
        assert first["type"] == "AA"
        assert first["filing_date"] == "2024-05-10"
        assert first["pages"] == 8
        assert "doc_998877" in first["document_metadata_link"]
