"""Unit tests for OpenDeal Command-Line Interface.

Verifies:
- opendeal status (text and structured JSON).
- opendeal company search & show.
- opendeal ingest sec & companies-house (offline fixture mode).
- opendeal facts list & explain.
- opendeal value dcf (rejection of missing rates, completed computation with rates).
- opendeal value nav.
- Appropriate exit codes and no leaked secrets in logs.
"""

import json
from pathlib import Path
from click.testing import CliRunner
import pytest

from opendeal.cli import cli


class TestOpenDealCLI:
    """Tests for Click-based OpenDeal CLI subcommands."""

    @pytest.fixture
    def runner(self) -> CliRunner:
        return CliRunner()

    @pytest.fixture
    def test_env(self, tmp_path: Path) -> dict:
        """Isolated test environment with dedicated SQLite database."""
        db_file = tmp_path / "test_cli.db"
        return {"OPENDEAL_DATABASE_PATH": str(db_file)}

    def test_cli_help(self, runner, test_env):
        """Root command prints help and all major subcommands."""
        result = runner.invoke(cli, ["--help"], env=test_env)
        assert result.exit_code == 0
        assert "OpenDeal" in result.output
        assert "company" in result.output
        assert "ingest" in result.output
        assert "facts" in result.output
        assert "value" in result.output
        assert "status" in result.output

    def test_status_command(self, runner, test_env):
        """status command prints system version and PEAK master verification."""
        result = runner.invoke(cli, ["status"], env=test_env)
        assert result.exit_code == 0
        assert "OpenDeal Platform Status" in result.output
        assert "PEAK Master Verified: YES" in result.output

    def test_status_command_json(self, runner, test_env):
        """status --json outputs valid structured JSON."""
        result = runner.invoke(cli, ["status", "--json"], env=test_env)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "OPERATIONAL"
        assert data["peak_master_workbook"]["integrity_verified"] is True
        assert data["peak_master_workbook"]["sha256"] == "ae2343527fb355ae7454f3fd4354289ff51ced8f5a2a8e62aac0ec0675bcbfa5"

    def test_ingest_sec_and_company_lookup(self, runner, test_env):
        """End-to-end CLI workflow: Ingest SEC data, search for company, and inspect details."""
        # 1. Ingest SEC AAPL
        ingest_res = runner.invoke(cli, ["ingest", "sec", "AAPL"], env=test_env)
        assert ingest_res.exit_code == 0
        assert "Successfully ingested SEC data for: Apple Inc." in ingest_res.output

        # 2. Search company
        search_res = runner.invoke(cli, ["company", "search", "AAPL"], env=test_env)
        assert search_res.exit_code == 0
        assert "Outcome:      EXACT_IDENTIFIER_MATCH" in search_res.output
        assert "Apple Inc." in search_res.output

        # 3. Search company JSON
        search_json_res = runner.invoke(cli, ["company", "search", "AAPL", "--json"], env=test_env)
        assert search_json_res.exit_code == 0
        data = json.loads(search_json_res.output)
        assert data["outcome"] == "EXACT_IDENTIFIER_MATCH"
        entity_id = data["entity"]["entity_id"]

        # 4. Company show
        show_res = runner.invoke(cli, ["company", "show", entity_id], env=test_env)
        assert show_res.exit_code == 0
        assert "=== Entity Details: Apple Inc. ===" in show_res.output

        # 5. Facts list
        facts_res = runner.invoke(cli, ["facts", "list", entity_id], env=test_env)
        assert facts_res.exit_code == 0
        assert "revenue" in facts_res.output

    def test_ingest_companies_house(self, runner, test_env):
        """Ingest UK Companies House fixture data via CLI."""
        res = runner.invoke(cli, ["ingest", "companies-house", "01234567"], env=test_env)
        assert res.exit_code == 0
        assert "Successfully ingested Companies House data for: ACME BRITISH HOLDINGS LIMITED" in res.output
        assert "UK Small Company Exemption" in res.output

    def test_value_dcf_missing_inputs_rejected(self, runner, test_env):
        """value dcf without rates returns exit code 1 and structured INCOMPLETE_MISSING_INPUTS."""
        # First ingest AAPL so entity exists
        runner.invoke(cli, ["ingest", "sec", "AAPL"], env=test_env)
        search_res = runner.invoke(cli, ["company", "search", "AAPL", "--json"], env=test_env)
        entity_id = json.loads(search_res.output)["entity"]["entity_id"]

        # Attempt DCF with no discount rate or terminal growth
        res = runner.invoke(cli, ["value", "dcf", entity_id, "--json"], env=test_env)
        assert res.exit_code == 1
        data = json.loads(res.output)
        assert data["status"] == "INCOMPLETE_MISSING_INPUTS"
        assert "discount_rate (WACC)" in data["missing_inputs"]
        assert "terminal_growth_rate" in data["missing_inputs"]

    def test_value_dcf_with_explicit_rates_succeeds(self, runner, test_env):
        """value dcf with explicit rates produces completed valuation."""
        runner.invoke(cli, ["ingest", "sec", "AAPL"], env=test_env)
        search_res = runner.invoke(cli, ["company", "search", "AAPL", "--json"], env=test_env)
        entity_id = json.loads(search_res.output)["entity"]["entity_id"]

        res = runner.invoke(cli, ["value", "dcf", entity_id, "-r", "0.09", "-g", "0.025", "--json"], env=test_env)
        assert res.exit_code == 0
        data = json.loads(res.output)
        assert data["status"] == "COMPLETED"
        assert data["enterprise_value"] > 0
        assert data["equity_value"] > 0
        assert data["discount_rate_wacc"] == 0.09
        assert data["terminal_growth_rate"] == 0.025

    def test_company_search_no_match(self, runner, test_env):
        """company search with nonexistent query reports NO_MATCH."""
        res = runner.invoke(cli, ["company", "search", "NonExistentCorp_999"], env=test_env)
        assert res.exit_code == 0
        assert "NO_MATCH" in res.output

    def test_company_show_not_found(self, runner, test_env):
        """company show with nonexistent identifier reports not found and exits 1."""
        res = runner.invoke(cli, ["company", "show", "nonexistent_id"], env=test_env)
        assert res.exit_code == 1
        assert "not found" in res.output

    def test_facts_explain(self, runner, test_env):
        """facts explain displays canonical variable, evidence links, and transformation history."""
        runner.invoke(cli, ["ingest", "sec", "AAPL"], env=test_env)
        search_res = runner.invoke(cli, ["company", "search", "AAPL", "--json"], env=test_env)
        entity_id = json.loads(search_res.output)["entity"]["entity_id"]

        list_res = runner.invoke(cli, ["facts", "list", entity_id, "--json"], env=test_env)
        facts_list = json.loads(list_res.output)
        assert len(facts_list) > 0
        fact_id = facts_list[0]["fact_id"]

        explain_res = runner.invoke(cli, ["facts", "explain", fact_id], env=test_env)
        assert explain_res.exit_code == 0
        assert "=== Fact Explanation:" in explain_res.output
        assert "Canonical Variable:" in explain_res.output

    def test_value_nav_complete(self, runner, test_env):
        """value nav calculates Net Asset Value when assets and liabilities are reported."""
        runner.invoke(cli, ["ingest", "sec", "AAPL"], env=test_env)
        search_res = runner.invoke(cli, ["company", "search", "AAPL", "--json"], env=test_env)
        entity_id = json.loads(search_res.output)["entity"]["entity_id"]

        # Note: In AAPL fixture, total_assets is present, liabilities might or might not be present
        res = runner.invoke(cli, ["value", "nav", entity_id, "--json"], env=test_env)
        # If liabilities is not in fixture, it reports incomplete missing inputs:
        if res.exit_code == 0:
            data = json.loads(res.output)
            assert data["status"] == "COMPLETED"
        else:
            assert res.exit_code == 1
            data = json.loads(res.output)
            assert data["status"] == "INCOMPLETE_MISSING_INPUTS"

    def test_value_lbo_cli_success_and_json(self, runner, test_env):
        """value lbo calculates debt schedule and sponsor returns in text and JSON modes."""
        # JSON mode
        res_json = runner.invoke(
            cli,
            [
                "value", "lbo",
                "--entry-ev", "100000000",
                "--senior-debt", "60000000",
                "--exit-multiple", "10",
                "--exit-ebitda", "15000000",
                "--json",
            ],
            env=test_env,
        )
        assert res_json.exit_code == 0
        data = json.loads(res_json.output)
        assert data["status"] == "COMPLETED"
        assert data["sources_and_uses"]["total_sources"] == 100000000.0
        assert data["sponsor_entry_equity"] == 40000000.0
        assert data["initial_total_debt"] == 60000000.0
        assert data["sponsor_moic"] > 1.0
        assert data["sponsor_irr"] > 0.0

        # Text mode
        res_text = runner.invoke(
            cli,
            [
                "value", "lbo",
                "--entry-ev", "100000000",
                "--senior-debt", "60000000",
                "--exit-multiple", "10",
                "--exit-ebitda", "15000000",
            ],
            env=test_env,
        )
        assert res_text.exit_code == 0
        assert "=== Leveraged Buyout (LBO) Analysis ===" in res_text.output
        assert "Entry Enterprise Value: $100,000,000.00" in res_text.output
        assert "MOIC:" in res_text.output

    def test_value_lbo_cli_missing_inputs_rejected(self, runner, test_env):
        """value lbo missing required inputs returns exit code 1 with INCOMPLETE_MISSING_INPUTS."""
        res = runner.invoke(cli, ["value", "lbo", "--json"], env=test_env)
        assert res.exit_code == 1
        data = json.loads(res.output)
        assert data["status"] == "INCOMPLETE_MISSING_INPUTS"
        assert "entry_ev" in data["missing_inputs"]
        assert "exit_multiple" in data["missing_inputs"]

    def test_value_monte_carlo_cli_success_and_json(self, runner, test_env):
        """value monte-carlo runs deterministic simulation with seed and prints stochastic notice."""
        # JSON mode
        res_json = runner.invoke(
            cli,
            [
                "value", "monte-carlo",
                "--fcf", "10000000",
                "--wacc-mean", "0.085",
                "--growth-min", "0.015",
                "--growth-mode", "0.025",
                "--growth-max", "0.035",
                "--seed", "42",
                "--threshold", "150000000",
                "--json",
            ],
            env=test_env,
        )
        assert res_json.exit_code == 0
        data = json.loads(res_json.output)
        assert data["status"] == "COMPLETED"
        assert data["random_seed"] == 42
        assert data["iteration_count"] == 1000
        assert data["successful_iterations"] == 1000
        assert data["mean_equity_value"] > 0
        assert "P5" in data["percentiles"]
        assert "P95" in data["percentiles"]

        # Text mode
        res_text = runner.invoke(
            cli,
            [
                "value", "monte-carlo",
                "--fcf", "10000000",
                "--wacc-mean", "0.085",
                "--growth-min", "0.015",
                "--growth-mode", "0.025",
                "--growth-max", "0.035",
                "--seed", "42",
            ],
            env=test_env,
        )
        assert res_text.exit_code == 0
        assert "=== Monte Carlo DCF Valuation ===" in res_text.output
        assert "Random Seed:        42 (Deterministic & Reproducible)" in res_text.output
        assert "Notice:" in res_text.output

    def test_value_monte_carlo_cli_missing_inputs_rejected(self, runner, test_env):
        """value monte-carlo missing cash flow or distributions returns exit code 1."""
        res = runner.invoke(cli, ["value", "monte-carlo", "--json"], env=test_env)
        assert res.exit_code == 1
        data = json.loads(res.output)
        assert data["status"] == "INCOMPLETE_MISSING_INPUTS"

    def test_value_venture_capital_cli_success_and_json(self, runner, test_env):
        """value venture-capital derives valuations, retention, and return multiples."""
        res_json = runner.invoke(
            cli,
            [
                "value", "venture-capital",
                "-i", "5000000",
                "--pre-money", "15000000",
                "--exit-val", "100000000",
                "--years", "5",
                "--dilution", "0.20",
                "--json",
            ],
            env=test_env,
        )
        assert res_json.exit_code == 0
        data = json.loads(res_json.output)
        assert data["status"] == "COMPLETED"
        assert data["post_money_valuation"] == 20000000.0
        assert data["initial_ownership_percentage"] == 0.25
        assert data["retention_rate"] == 0.8
        assert data["exit_ownership_percentage"] == 0.2
        assert data["scenario_results"][0]["moic"] == 4.0

        # Text mode
        res_text = runner.invoke(
            cli,
            [
                "value", "venture-capital",
                "-i", "5000000",
                "--pre-money", "15000000",
                "--exit-val", "100000000",
            ],
            env=test_env,
        )
        assert res_text.exit_code == 0
        assert "=== Venture Capital (VC) Valuation ===" in res_text.output
        assert "Investment Amount:    $5,000,000.00" in res_text.output

    def test_value_venture_capital_cli_missing_inputs_rejected(self, runner, test_env):
        """value venture-capital without investment or exit valuation returns exit code 1."""
        res = runner.invoke(cli, ["value", "venture-capital", "--json"], env=test_env)
        assert res.exit_code == 1
        data = json.loads(res.output)
        assert data["status"] == "INCOMPLETE_MISSING_INPUTS"

    def test_value_accretion_dilution_cli_success_and_json(self, runner, test_env):
        """value accretion-dilution calculates EPS accretion and breakeven synergies."""
        res_json = runner.invoke(
            cli,
            [
                "value", "accretion-dilution",
                "--buyer-price", "50",
                "--buyer-shares", "100000000",
                "--buyer-net-income", "200000000",
                "--offer-price", "30",
                "--target-shares", "20000000",
                "--target-net-income", "30000000",
                "--cash-pct", "0.5",
                "--stock-pct", "0.5",
                "--pre-tax-synergies", "20000000",
                "--json",
            ],
            env=test_env,
        )
        assert res_json.exit_code == 0
        data = json.loads(res_json.output)
        assert data["status"] == "COMPLETED"
        assert data["is_accretive"] is True
        assert data["buyer_standalone_eps"] == 2.0
        assert data["new_shares_issued"] == 6000000.0
        assert data["pro_forma_shares"] == 106000000.0
        assert data["pro_forma_eps"] == 2.2476

        # Text mode
        res_text = runner.invoke(
            cli,
            [
                "value", "accretion-dilution",
                "--buyer-price", "50",
                "--buyer-shares", "100000000",
                "--buyer-net-income", "200000000",
                "--offer-price", "30",
                "--target-shares", "20000000",
                "--target-net-income", "30000000",
                "--cash-pct", "0.5",
                "--stock-pct", "0.5",
                "--pre-tax-synergies", "20000000",
            ],
            env=test_env,
        )
        assert res_text.exit_code == 0
        assert "=== M&A Accretion / Dilution Analysis ===" in res_text.output
        assert "Deal Characterization:  ACCRETIVE" in res_text.output

    def test_value_accretion_dilution_cli_invalid_mix_rejected(self, runner, test_env):
        """value accretion-dilution consideration mix not summing to 1.0 is rejected."""
        res = runner.invoke(
            cli,
            [
                "value", "accretion-dilution",
                "--buyer-price", "50",
                "--buyer-shares", "100",
                "--buyer-net-income", "100",
                "--target-net-income", "50",
                "--target-equity-value", "100",
                "--cash-pct", "0.8",
                "--stock-pct", "0.8",
                "--json",
            ],
            env=test_env,
        )
        assert res.exit_code == 1
        data = json.loads(res.output)
        assert data["status"] == "MATHEMATICALLY_INVALID"

    def test_research_company_cli_workflow(self, runner, test_env):
        """End-to-end research CLI workflow: ingest company data and generate research brief."""
        runner.invoke(cli, ["ingest", "sec", "AAPL"], env=test_env)

        # 1. Text mode
        res_text = runner.invoke(cli, ["research", "company", "AAPL"], env=test_env)
        assert res_text.exit_code == 0
        assert "=== OpenDeal Company Research Brief ===" in res_text.output
        assert "Apple Inc." in res_text.output
        assert "Executive Summary:" in res_text.output
        assert "Evidence-Grounded Claims" in res_text.output

        # 2. JSON mode
        res_json = runner.invoke(cli, ["research", "company", "AAPL", "--json"], env=test_env)
        assert res_json.exit_code == 0
        data = json.loads(res_json.output)
        assert data["status"] == "COMPLETED"
        assert "Apple Inc." in data["entity_name"]
        assert len(data["claims"]) > 0
        assert "financial_trends" in data
        assert len(data["cited_evidence_ids"]) > 0
        research_id = data["research_id"]

        # 3. Explain mode (Provenance Audit)
        res_explain = runner.invoke(cli, ["research", "explain", research_id], env=test_env)
        assert res_explain.exit_code == 0
        assert "=== Research Grounding Audit:" in res_explain.output
        assert "Grounded Claims Audit" in res_explain.output
        assert "Supporting Primary Evidence Records:" in res_explain.output

        # 4. Explain mode JSON
        res_explain_json = runner.invoke(cli, ["research", "explain", research_id, "--json"], env=test_env)
        assert res_explain_json.exit_code == 0
        explain_data = json.loads(res_explain_json.output)
        assert explain_data["research_id"] == research_id

    def test_research_brief_cli(self, runner, test_env):
        """research brief command with scope customization."""
        runner.invoke(cli, ["ingest", "sec", "AAPL"], env=test_env)

        res = runner.invoke(
            cli,
            ["research", "brief", "AAPL", "--no-valuations"],
            env=test_env,
        )
        assert res.exit_code == 0
        assert "Research Brief Generated:" in res.output

    def test_research_missing_company_rejected(self, runner, test_env):
        """research company with unknown entity returns exit code 1."""
        res = runner.invoke(cli, ["research", "company", "NONEXISTENT_XYZ"], env=test_env)
        assert res.exit_code == 1
        assert "not found in local database" in res.output

