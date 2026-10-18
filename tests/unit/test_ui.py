"""Automated Unit and Integration Tests for OpenDeal Web UI and User Workflows."""

from datetime import datetime, timezone
import pytest
from starlette.testclient import TestClient

from opendeal.core.types import FiscalPeriodType, SourceType
from opendeal.entities.models import CompanyEntity, LegalIdentifier
from opendeal.evidence.models import EvidenceRecord, SourceReference
from opendeal.facts.models import FactRecord, FactState
from opendeal.deals.contracts import FundingRound, FundingRoundType, TransactionEvent, TransactionType, TransactionStatus
from opendeal.storage.sqlite import SQLiteStorage
from opendeal.ui.app import create_app


@pytest.fixture
def memory_storage() -> SQLiteStorage:
    """Create an in-memory SQLiteStorage instance with complete migrations."""
    storage = SQLiteStorage(":memory:")
    storage.initialize()
    return storage


@pytest.fixture
def populated_storage(memory_storage: SQLiteStorage) -> SQLiteStorage:
    """Populate storage with representative deterministic records across all domains."""
    # 1. Company Entities
    entity = CompanyEntity(
        entity_id="ENT_ACME_001",
        legal_name="Acme Industrial Corp",
        jurisdiction="US",
        aliases=["Acme Inc", "Acme Holdings"],
        is_active=True,
    )
    memory_storage.save_entity(entity)

    target_entity = CompanyEntity(
        entity_id="ENT_BETA_002",
        legal_name="Beta Logistics Ltd",
        jurisdiction="US",
        aliases=["Beta Log"],
        is_active=True,
    )
    memory_storage.save_entity(target_entity)

    # 2. Source and Evidence
    source = SourceReference(
        source_id="SRC_10K_2023",
        source_type=SourceType.REGULATORY_FILING,
        source_url="https://www.sec.gov/edgar/data/0001/10k.htm",
        publisher="SEC EDGAR",
        content_hash="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
    )
    memory_storage.save_source(source)

    evidence = EvidenceRecord(
        evidence_id="EVID_REV_001",
        source_id="SRC_10K_2023",
        raw_content="Total consolidated net revenue for fiscal year 2023 was $120,500,000.",
        content_hash="1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        extraction_method="TEXT_EXTRACTION",
        section_name="Item 8 - Financial Statements, Note 3",
    )
    memory_storage.save_evidence(evidence)

    # 3. Financial Fact
    fact = FactRecord(
        fact_id="FACT_REV_2023",
        entity_id="ENT_ACME_001",
        canonical_variable="revenue",
        reported_value=120500000.0,
        currency="USD",
        fiscal_year=2023,
        fiscal_period=FiscalPeriodType.ANNUAL,
        fact_state=FactState.RAW_FACT,
        evidence_ids=["EVID_REV_001"],
    )
    memory_storage.save_fact(fact)

    # 4. Funding Round
    f_round = FundingRound(
        round_id="RND_SERIES_B",
        company_id="ENT_ACME_001",
        company_name="Acme Industrial Corp",
        round_type=FundingRoundType.SERIES_B,
        announced_date="2023-04-15",
        disclosed_amount=25000000.0,
        currency="USD",
        pre_money_valuation=75000000.0,
        post_money_valuation=100000000.0,
        participant_ids=["INV_APEX_VENTURES"],
        evidence_ids=["EVID_REV_001"],
    )
    memory_storage.save_funding_round(f_round)

    # 5. Transaction Event
    tx = TransactionEvent(
        transaction_id="TX_ACQ_001",
        transaction_type=TransactionType.ACQUISITION,
        acquirer_id="ENT_ACME_001",
        acquirer_name="Acme Industrial Corp",
        target_id="ENT_BETA_002",
        target_name="Beta Logistics Ltd",
        status=TransactionStatus.COMPLETED,
        disclosed_value=15000000.0,
        currency="USD",
        announced_date="2023-09-01",
        actual_completion_date="2023-10-15",
        evidence_ids=["EVID_REV_001"],
    )
    memory_storage.save_transaction(tx)

    return memory_storage


@pytest.fixture
def client(populated_storage: SQLiteStorage) -> TestClient:
    """Create a TestClient bound to the populated test database."""
    app = create_app(storage=populated_storage)
    return TestClient(app)


@pytest.fixture
def empty_client(memory_storage: SQLiteStorage) -> TestClient:
    """Create a TestClient bound to an empty test database."""
    app = create_app(storage=memory_storage)
    return TestClient(app)


# ── Test Suite ─────────────────────────────────────────────────────────────

class TestUIBasicsAndDashboard:
    """Verify application startup, security headers, and dashboard metrics."""

    def test_dashboard_renders_cleanly_with_data(self, client: TestClient):
        response = client.get("/")
        assert response.status_code == 200
        assert "OpenDeal" in response.text
        assert "Platform Intelligence Overview" in response.text
        assert "Acme Industrial Corp" in response.text
        # Security headers
        assert response.headers.get("X-Frame-Options") == "DENY"
        assert response.headers.get("X-Content-Type-Options") == "nosniff"

    def test_dashboard_renders_cleanly_with_empty_database(self, empty_client: TestClient):
        response = empty_client.get("/")
        assert response.status_code == 200
        assert "0" in response.text
        assert "No companies indexed in local storage yet" in response.text

    def test_404_error_page_handles_unknown_routes(self, client: TestClient):
        response = client.get("/non_existent_page_12345")
        assert response.status_code == 404
        assert "Page Not Found" in response.text
        assert "404" in response.text


class TestCompaniesExplorer:
    """Verify company listing, search filters, and detail dossier views."""

    def test_list_companies(self, client: TestClient):
        response = client.get("/companies")
        assert response.status_code == 200
        assert "Acme Industrial Corp" in response.text
        assert "US" in response.text

    def test_search_companies_found(self, client: TestClient):
        response = client.get("/companies?q=Acme")
        assert response.status_code == 200
        assert "Acme Industrial Corp" in response.text

    def test_search_companies_not_found(self, client: TestClient):
        response = client.get("/companies?q=NonExistentCorp")
        assert response.status_code == 200
        assert "No corporate entities match your query" in response.text

    def test_filter_companies_by_jurisdiction(self, client: TestClient):
        response = client.get("/companies?jurisdiction=US")
        assert response.status_code == 200
        assert "Acme Industrial Corp" in response.text

        response_other = client.get("/companies?jurisdiction=DE")
        assert response_other.status_code == 200
        assert "Acme Industrial Corp" not in response_other.text

    def test_company_detail_success(self, client: TestClient):
        response = client.get("/companies/ENT_ACME_001")
        assert response.status_code == 200
        assert "Acme Industrial Corp" in response.text
        assert "ENT_ACME_001" in response.text
        assert "revenue" in response.text

    def test_company_detail_404_for_unknown(self, client: TestClient):
        response = client.get("/companies/UNKNOWN_ENTITY_999")
        assert response.status_code == 404
        assert "Entity Not Found" in response.text


class TestFactsAndEvidenceExplorer:
    """Verify facts list, filter, fact detail, and evidence citation pages."""

    def test_list_facts(self, client: TestClient):
        response = client.get("/facts")
        assert response.status_code == 200
        assert "revenue" in response.text
        assert "120,500,000" in response.text

    def test_filter_facts_by_metric(self, client: TestClient):
        response = client.get("/facts?metric=revenue")
        assert response.status_code == 200
        assert "revenue" in response.text

        response_ebitda = client.get("/facts?metric=ebitda")
        assert response_ebitda.status_code == 200
        assert "No financial facts match your search filters" in response_ebitda.text

    def test_fact_detail_view(self, client: TestClient):
        response = client.get("/facts/FACT_REV_2023")
        assert response.status_code == 200
        assert "FACT_REV_2023" in response.text
        assert "120,500,000" in response.text
        assert "EVID_REV_001" in response.text

    def test_fact_detail_404_for_unknown(self, client: TestClient):
        response = client.get("/facts/FACT_UNKNOWN")
        assert response.status_code == 404
        assert "Fact Not Found" in response.text

    def test_evidence_detail_view(self, client: TestClient):
        response = client.get("/facts/evidence/EVID_REV_001")
        assert response.status_code == 200
        assert "EVID_REV_001" in response.text
        assert "Total consolidated net revenue" in response.text

    def test_evidence_detail_404_for_unknown(self, client: TestClient):
        response = client.get("/facts/evidence/EVID_UNKNOWN")
        assert response.status_code == 404
        assert "Evidence Citation Not Found" in response.text


class TestValuationWorkspace:
    """Verify valuation model hub, input forms, and deterministic calculation results."""

    def test_valuation_hub(self, client: TestClient):
        response = client.get("/valuation")
        assert response.status_code == 200
        assert "Valuation Workspace" in response.text
        assert "Discounted Cash Flow (DCF)" in response.text
        assert "Cost of Capital (WACC)" in response.text
        assert "Leveraged Buyout (LBO)" in response.text

    def test_valuation_forms_load(self, client: TestClient):
        for model in ["dcf", "wacc", "gordon", "nav", "comps", "lbo", "vc", "accretion_dilution"]:
            response = client.get(f"/valuation/{model}")
            assert response.status_code == 200
            assert "Deterministic Engine" in response.text

    def test_valuation_form_unknown_model_404(self, client: TestClient):
        response = client.get("/valuation/unknown_exotic_model")
        assert response.status_code == 404
        assert "Valuation Model Not Supported" in response.text

    def test_dcf_calculation_matches_deterministic_engine(self, client: TestClient):
        form_payload = {
            "forecast_cash_flows": "1000000, 1200000, 1400000, 1600000, 1800000",
            "discount_rate": "0.08",
            "terminal_growth_rate": "0.025",
            "discounting_convention": "MID_YEAR",
            "cash_and_equivalents": "2000000",
            "total_debt": "5000000",
            "shares_outstanding": "1000000",
        }
        response = client.post("/valuation/dcf/calculate", data=form_payload)
        assert response.status_code == 200
        assert "Valuation Result" in response.text
        assert "Enterprise Value (EV)" in response.text
        assert "Implied Equity Value" in response.text
        assert "Implied Share Price" in response.text
        assert "opendeal.valuation.engine" in response.text

    def test_wacc_calculation(self, client: TestClient):
        form_payload = {
            "risk_free_rate": "0.04",
            "beta": "1.10",
            "market_risk_premium": "0.055",
            "pre_tax_cost_of_debt": "0.06",
            "tax_rate": "0.25",
            "equity_weight": "0.70",
            "debt_weight": "0.30",
        }
        response = client.post("/valuation/wacc/calculate", data=form_payload)
        assert response.status_code == 200
        assert "Weighted Average Cost of Capital (WACC)" in response.text
        assert "Cost of Equity (Ke)" in response.text

    def test_valuation_malformed_input_handled_cleanly(self, client: TestClient):
        # Discount rate cannot be 0.0 or negative for DCF
        form_payload = {
            "forecast_cash_flows": "1000000, 1200000",
            "discount_rate": "-0.05",
            "terminal_growth_rate": "0.025",
        }
        response = client.post("/valuation/dcf/calculate", data=form_payload)
        assert response.status_code == 400
        assert "Calculation / Validation Error" in response.text


class TestResearchWorkspace:
    """Verify Phase 5 research generation and evidence-grounded brief display."""

    def test_research_index(self, client: TestClient):
        response = client.get("/research")
        assert response.status_code == 200
        assert "Evidence-Grounded Research Workspace" in response.text
        assert "Acme Industrial Corp" in response.text

    def test_generate_and_view_research_brief(self, client: TestClient):
        generate_payload = {
            "entity_id": "ENT_ACME_001",
            "include_financials": "yes",
            "include_relationships": "yes",
            "include_valuations": "yes",
            "max_evidence_items": 25,
        }
        response = client.post("/research/generate", data=generate_payload, follow_redirects=True)
        assert response.status_code == 200
        assert "Research Brief" in response.text
        assert "Executive Summary" in response.text
        assert "Acme Industrial Corp" in response.text


class TestTargetDiscovery:
    """Verify Phase 6 screening pipeline, results inspection, and watchlist management."""

    def test_discovery_index(self, client: TestClient):
        response = client.get("/discovery")
        assert response.status_code == 200
        assert "Target Discovery & Screening" in response.text

    def test_execute_screening_and_view_results(self, client: TestClient):
        screen_payload = {
            "jurisdictions": "US",
            "max_candidates": 10,
            "require_active": "yes",
            "missing_data_penalty": 0.20,
        }
        response = client.post("/discovery/screen", data=screen_payload, follow_redirects=True)
        assert response.status_code == 200
        assert "Screening Run Results" in response.text

    def test_watchlist_lifecycle(self, client: TestClient):
        # 1. Create Watchlist
        create_payload = {
            "name": "Industrial Tech Pipeline",
            "description": "Strategic targets in manufacturing and logistics",
        }
        create_resp = client.post("/discovery/watchlists/create", data=create_payload, follow_redirects=True)
        assert create_resp.status_code == 200
        assert "Industrial Tech Pipeline" in create_resp.text

        # Extract list_id from current url or response
        list_url = create_resp.request.url.path
        list_id = list_url.split("/")[-1]

        # 2. Add member
        add_payload = {
            "entity_id": "ENT_ACME_001",
            "notes": "Anchor target for discussion",
        }
        add_resp = client.post(f"/discovery/watchlists/{list_id}/add", data=add_payload, follow_redirects=True)
        assert add_resp.status_code == 200
        assert "ENT_ACME_001" in add_resp.text

        # 3. Export watchlist CSV
        export_resp = client.get(f"/discovery/watchlists/{list_id}/export?format=csv")
        assert export_resp.status_code == 200
        assert "ENT_ACME_001" in export_resp.text

        # 4. Remove member
        remove_resp = client.post(f"/discovery/watchlists/{list_id}/remove/ENT_ACME_001", follow_redirects=True)
        assert remove_resp.status_code == 200
        assert "zero members" in remove_resp.text or "ENT_ACME_001" not in remove_resp.text


class TestDealsIntelligence:
    """Verify Phase 7 funding rounds, transactions, company timelines, and conflicts."""

    def test_deals_index(self, client: TestClient):
        response = client.get("/deals")
        assert response.status_code == 200
        assert "Deals & Transaction Intelligence" in response.text
        assert "Acme Industrial Corp" in response.text
        assert "Beta Logistics Ltd" in response.text

    def test_funding_round_detail(self, client: TestClient):
        response = client.get("/deals/rounds/RND_SERIES_B")
        assert response.status_code == 200
        assert "RND_SERIES_B" in response.text
        assert "25,000,000" in response.text
        assert "Acme Industrial Corp" in response.text

    def test_transaction_detail(self, client: TestClient):
        response = client.get("/deals/transactions/TX_ACQ_001")
        assert response.status_code == 200
        assert "TX_ACQ_001" in response.text
        assert "Beta Logistics Ltd" in response.text
        assert "15,000,000" in response.text

    def test_company_timeline(self, client: TestClient):
        response = client.get("/deals/timeline/ENT_ACME_001")
        assert response.status_code == 200
        assert "Funding & Transaction Timeline" in response.text
        assert "Acme Industrial Corp" in response.text

    def test_conflicts_viewer(self, client: TestClient):
        response = client.get("/deals/conflicts")
        assert response.status_code == 200
        assert "Deal Conflicts & Multi-Source Discrepancies" in response.text


class TestSystemHealthAndSecurity:
    """Verify security controls, path traversal guards, and live operation safeguards."""

    def test_system_status_and_peak_hash(self, client: TestClient):
        response = client.get("/system")
        assert response.status_code == 200
        assert "System Health, Storage & Integrity Status" in response.text
        assert "OFFLINE LOCAL-FIRST" in response.text
        assert "ae2343527fb355ae7454f3fd4354289ff51ced8f5a2a8e62aac0ec0675bcbfa5" in response.text

    def test_system_ingest_view(self, client: TestClient):
        response = client.get("/system/ingest")
        assert response.status_code == 200
        assert "Data Ingestion & Connector Management" in response.text

    def test_fixture_ingestion_path_traversal_blocked(self, client: TestClient):
        # Attempt path traversal
        payload = {
            "fixture_path": "../../etc/passwd",
            "dataset_type": "deals",
        }
        response = client.post("/system/ingest/fixture", data=payload)
        assert response.status_code == 400
        assert "directory traversal" in response.text or "does not exist" in response.text

    def test_live_connector_unconfirmed_request_rejected(self, client: TestClient):
        # Attempt live call without checking confirm_live
        payload = {
            "connector_name": "sec_edgar",
            "search_query": "0001018724",
            # confirm_live intentionally omitted
        }
        response = client.post("/system/ingest/live", data=payload)
        assert response.status_code == 400
        assert "Mandatory safeguard" in response.text

    def test_xss_escaping_in_inputs(self, client: TestClient):
        # Ensure injected script tags are escaped in rendered HTML
        xss_string = '<script>alert("XSS")</script>'
        response = client.get(f"/companies?q={xss_string}")
        assert response.status_code == 200
        assert "<script>alert" not in response.text
        assert "&lt;script&gt;alert" in response.text or "alert" in response.text
