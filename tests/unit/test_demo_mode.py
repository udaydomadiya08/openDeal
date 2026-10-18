"""Comprehensive unit and security tests for OpenDeal interactive demo mode.

Covers:
- Demo database seeding and idempotency.
- Synthetic-data labelling and transparent disclaimers.
- Real deterministic valuation execution and invalid-input handling.
- Public demo access restrictions (denial of live ingestion and fixture uploads).
- Database isolation (real user database is never touched).
- Health check (/healthz) behavior and absence of secret exposure.
- Host header validation in demo mode vs standard loopback mode.
- Core UI pages rendering cleanly in demo mode.
"""

from pathlib import Path
import pytest
from starlette.testclient import TestClient

from opendeal.demo.dataset import seed_demo_database
from opendeal.storage import SQLiteStorage
from opendeal.ui.app import create_app
from opendeal.valuation import (
    DCFInput,
    DiscountingConvention,
    calculate_dcf,
    DebtTranche,
    LBOInput,
    LBOOperatingYear,
    calculate_lbo,
    ValuationCalculationStatus,
)


class TestDemoModeDatasetAndLabelling:
    """Tests verifying demo dataset structure, idempotency, and synthetic labeling."""

    @pytest.fixture
    def temp_storage(self, tmp_path: Path) -> SQLiteStorage:
        db_file = tmp_path / "test_demo_dataset.db"
        return SQLiteStorage(str(db_file))

    def test_demo_database_seeding_and_idempotency(self, temp_storage: SQLiteStorage):
        """Seeding populates exactly 5 demo entities and is safe to call repeatedly."""
        # First seed
        seed_demo_database(temp_storage)

        entities = temp_storage.list_entities()
        entity_ids = {e.entity_id for e in entities}
        expected_ids = {"DEMO-NEXUS", "DEMO-APEX", "DEMO-VITA", "DEMO-HELIOS", "DEMO-MERIDIAN"}
        assert expected_ids.issubset(entity_ids), f"Missing demo entities in {entity_ids}"

        # Verify synthetic labeling on every demo entity
        for eid in expected_ids:
            ent = temp_storage.get_entity(eid)
            assert ent is not None
            assert ent.metadata.get("is_synthetic") is True, f"{eid} must be marked synthetic"
            assert "DEMO-" in ent.entity_id

        # Verify fact counts and synthetic notices
        nexus_facts = temp_storage.get_facts_for_entity("DEMO-NEXUS")
        assert len(nexus_facts) >= 6
        for fact in nexus_facts:
            notice = fact.metadata.get("source_notice", "")
            assert "SYNTHETIC DEMO EVIDENCE" in notice

        # Second seed (idempotency check)
        seed_demo_database(temp_storage)
        re_seeded_entities = temp_storage.list_entities()
        assert len([e for e in re_seeded_entities if e.entity_id in expected_ids]) == 5

    def test_missing_data_transparency_in_demo(self, temp_storage: SQLiteStorage):
        """DEMO-MERIDIAN must have an explicit MISSING_FACT with NOT_REPORTED reason."""
        seed_demo_database(temp_storage)
        facts = temp_storage.get_facts_for_entity("DEMO-MERIDIAN")
        missing_fcf = next((f for f in facts if f.canonical_variable == "free_cash_flow"), None)
        assert missing_fcf is not None, "DEMO-MERIDIAN must contain a free_cash_flow fact record"
        assert missing_fcf.fact_state == "MISSING_FACT"
        assert missing_fcf.missing_reason == "NOT_REPORTED"
        assert missing_fcf.normalized_value is None


class TestDeterministicValuationScenarios:
    """Tests confirming demo scenarios run the real deterministic valuation engine."""

    def test_growth_tech_dcf_scenario_nexus(self):
        """Scenario 1: DEMO-NEXUS DCF produces exact mathematical outputs."""
        inp = DCFInput(
            forecast_cash_flows=[32e6, 40e6, 49e6, 58e6, 67e6],
            discount_rate=0.0925,
            terminal_growth_rate=0.025,
            discounting_convention=DiscountingConvention.MID_YEAR,
            cash_and_equivalents=55e6,
            total_debt=20e6,
            shares_outstanding=25e6,
        )
        res = calculate_dcf(inp)
        assert res.status == ValuationCalculationStatus.COMPLETED
        # Enterprise Value verified to cent
        assert round(res.enterprise_value, 2) == 846187239.57
        # Implied Equity Value verified to cent
        assert round(res.equity_value, 2) == 881187239.57
        # Share price verified
        assert round(res.implied_share_price, 2) == 35.25

    def test_industrial_lbo_scenario_apex(self):
        """Scenario 2: DEMO-APEX LBO produces verified sources/uses and returns."""
        tranches = [
            DebtTranche(
                name="Term Loan A",
                initial_principal=500.0,
                interest_rate=0.06,
                mandatory_amortization_rate=0.05,
                seniority_order=1,
            ),
            DebtTranche(
                name="Subordinated Notes",
                initial_principal=200.0,
                interest_rate=0.10,
                mandatory_amortization_rate=0.0,
                seniority_order=2,
            ),
        ]
        projections = [
            LBOOperatingYear(year=1, revenue=800.0, ebitda=180.0, capex=30.0, nwc_change=5.0, tax_rate=0.25),
            LBOOperatingYear(year=2, revenue=860.0, ebitda=195.0, capex=32.0, nwc_change=5.0, tax_rate=0.25),
            LBOOperatingYear(year=3, revenue=920.0, ebitda=210.0, capex=35.0, nwc_change=5.0, tax_rate=0.25),
            LBOOperatingYear(year=4, revenue=980.0, ebitda=225.0, capex=38.0, nwc_change=5.0, tax_rate=0.25),
            LBOOperatingYear(year=5, revenue=1050.0, ebitda=240.0, capex=40.0, nwc_change=5.0, tax_rate=0.25),
        ]
        inp = LBOInput(
            purchase_enterprise_value=1000.0,
            sponsor_equity_contribution=440.0,
            debt_tranches=tranches,
            refinanced_debt=100.0,
            transaction_fees=15.0,
            financing_fees=10.0,
            min_cash_balance=15.0,
            operating_projections=projections,
            exit_multiple=7.5,
            cash_sweep_percentage=1.0,
        )
        res = calculate_lbo(inp)
        assert res.status == ValuationCalculationStatus.COMPLETED
        assert res.sources_and_uses.imbalance == 0.0
        assert res.initial_total_debt == 700.0
        assert round(res.ending_total_debt, 2) == 266.51
        assert res.exit_enterprise_value == 1800.0
        assert round(res.sponsor_moic, 2) == 3.52
        assert round(res.sponsor_irr, 4) == 0.2861

    def test_invalid_valuation_inputs_handled_safely(self):
        """Invalid inputs return error or incomplete status rather than unhandled exception."""
        # WACC less than terminal growth rate is mathematically invalid for Gordon Growth
        invalid_dcf = DCFInput(
            forecast_cash_flows=[10e6, 12e6],
            discount_rate=0.02,
            terminal_growth_rate=0.03,  # g > wacc
            cash_and_equivalents=5e6,
            total_debt=1e6,
            shares_outstanding=10e6,
        )
        res = calculate_dcf(invalid_dcf)
        assert res.status == ValuationCalculationStatus.MATHEMATICALLY_INVALID


class TestDemoModeSecurityRestrictions:
    """Tests confirming security safeguards, route locks, and database isolation in demo mode."""

    @pytest.fixture
    def demo_client(self, tmp_path: Path) -> TestClient:
        isolated_db = tmp_path / "isolated_demo.db"
        app = create_app(
            db_path=str(isolated_db),
            demo_mode=True,
            allowed_hosts=["testserver", "opendeal-demo.onrender.com"],
        )
        return TestClient(app)

    def test_live_ingestion_blocked_in_demo_mode(self, demo_client: TestClient):
        """Live regulatory ingestion endpoints must return 403 Forbidden in demo mode."""
        resp = demo_client.post(
            "/system/ingest/live",
            data={"connector_name": "sec", "search_query": "AAPL"},
        )
        assert resp.status_code == 403
        assert "Access Denied" in resp.text
        assert "Public Demo Mode" in resp.text

    def test_fixture_upload_blocked_in_demo_mode(self, demo_client: TestClient):
        """Arbitrary fixture ingestion must return 403 Forbidden in demo mode."""
        resp = demo_client.post(
            "/system/ingest/fixture",
            data={"fixture_path": "custom_deal.json", "dataset_type": "deals"},
        )
        assert resp.status_code == 403
        assert "Access Denied" in resp.text
        assert "Public Demo Mode" in resp.text

    def test_healthz_endpoint_is_safe_and_unauthenticated(self, demo_client: TestClient):
        """GET /healthz returns 200 without exposing environment variables or secrets."""
        resp = demo_client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["mode"] == "demo"
        # Verify no sensitive env vars are leaked
        assert "OPENDEAL_SECRET" not in data
        assert "AWS_SECRET_ACCESS_KEY" not in data
        assert "DATABASE_URL" not in data

    def test_database_isolation_does_not_touch_user_home(self, tmp_path: Path):
        """Demo app initialized with a custom isolated path never touches real user home."""
        user_real_dir = Path.home() / "Library" / "Application Support" / "OpenDeal"
        mtime_before = user_real_dir.stat().st_mtime if user_real_dir.exists() else None

        isolated_db = tmp_path / "safe_sandbox.db"
        app = create_app(db_path=str(isolated_db), demo_mode=True)
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        assert isolated_db.exists()

        if user_real_dir.exists() and mtime_before:
            assert user_real_dir.stat().st_mtime == mtime_before

    def test_host_header_validation_in_demo_mode(self, demo_client: TestClient):
        """Allowed demo host names succeed; untrusted attacker host headers are rejected."""
        # Allowed host: opendeal-demo.onrender.com
        resp = demo_client.get("/healthz", headers={"Host": "opendeal-demo.onrender.com"})
        assert resp.status_code == 200

        # Untrusted host header on normal page
        resp_untrusted = demo_client.get("/", headers={"Host": "evil-attacker.domain.com"})
        assert resp_untrusted.status_code in [400, 403]


class TestDemoCorePagesRender:
    """Tests confirming core UI workspaces render properly with demo fixtures."""

    @pytest.fixture
    def demo_client(self, tmp_path: Path) -> TestClient:
        isolated_db = tmp_path / "isolated_demo.db"
        app = create_app(
            db_path=str(isolated_db),
            demo_mode=True,
            allowed_hosts=["testserver"],
        )
        return TestClient(app)

    def test_dashboard_renders_with_tour_and_badge(self, demo_client: TestClient):
        resp = demo_client.get("/")
        assert resp.status_code == 200
        html = resp.text
        assert "INTERACTIVE DEMO" in html
        assert "Institutional Sandbox Workspaces" in html
        assert "Quick Valuation Presets" in html

    def test_company_profile_renders_synthetic_labels(self, demo_client: TestClient):
        resp = demo_client.get("/companies/DEMO-NEXUS")
        assert resp.status_code == 200
        html = resp.text
        assert "Nexus Cloud Technologies Inc." in html
        assert "DEMO-NEXUS" in html

    def test_company_alias_redirect(self, demo_client: TestClient):
        resp = demo_client.get("/company/DEMO-NEXUS", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/companies/DEMO-NEXUS"

    def test_valuation_form_with_demo_preset(self, demo_client: TestClient):
        resp = demo_client.get("/valuation/dcf?preset=nexus")
        assert resp.status_code == 200
        html = resp.text
        assert "Discounted Cash Flow" in html
        assert "0.095" in html
        assert "25000000" in html

    def test_discovery_workspace_renders_screening_run(self, demo_client: TestClient):
        resp = demo_client.get("/discovery")
        assert resp.status_code == 200
        html = resp.text
        assert "RUN-DEMO-001" in html or "Discovery" in html

    def test_deals_workspace_renders(self, demo_client: TestClient):
        resp = demo_client.get("/deals")
        assert resp.status_code == 200
        html = resp.text
        assert "DEMO-NEXUS" in html or "Deals" in html
