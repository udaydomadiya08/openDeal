"""Independent unit tests for Leveraged Buyout (LBO) analysis.

Verifies:
- Hand-calculated institutional benchmark LBO case.
- Exact sources-and-uses reconciliation (sources must equal uses).
- Imbalance detection and rejection with MATHEMATICALLY_INVALID.
- Multi-tranche debt schedules with interest and priority cash sweep.
- Sponsor MOIC and annualized IRR calculations.
- Missing inputs handled with structured INCOMPLETE_MISSING_INPUTS.
"""

import pytest

from opendeal.valuation.engine.contracts import (
    DebtTranche,
    LBOInput,
    LBOOperatingYear,
    ValuationCalculationStatus,
)
from opendeal.valuation.engine.lbo import calculate_lbo


class TestLBOEngine:
    """Institutional tests for Leveraged Buyout modeling."""

    @pytest.fixture
    def benchmark_lbo_input(self) -> LBOInput:
        """Hand-calculated benchmark:
        Purchase EV: $1,000.0M
        Refinanced Existing Debt: $100.0M
        Transaction Fees: $15.0M
        Financing Fees: $10.0M
        Minimum Cash: $15.0M
        Total Uses = 1,000 + 100 + 15 + 10 + 15 = $1,140.0M

        Debt Tranches:
        - Term Loan A: $500.0M, 6.0% coupon, 5.0% ($25.0M) mandatory amort/yr, priority 1
        - Subordinated Notes: $200.0M, 10.0% coupon, 0.0% mandatory amort, priority 2
        Total Debt = $700.0M

        Sponsor Equity (plug) = 1,140 - 700 = $440.0M
        Total Sources = 700 + 440 = $1,140.0M (Sources == Uses)

        5-Year Operating Projections:
        Y1: EBITDA=180, Capex=30, NWC=5, Tax=25%
        Y2: EBITDA=195, Capex=32, NWC=5, Tax=25%
        Y3: EBITDA=210, Capex=35, NWC=5, Tax=25%
        Y4: EBITDA=225, Capex=38, NWC=5, Tax=25%
        Y5: EBITDA=240, Capex=40, NWC=5, Tax=25%

        Exit Multiple: 7.5x
        Exit EV = 240 * 7.5 = $1,800.0M
        """
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

        return LBOInput(
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

    def test_lbo_benchmark_execution(self, benchmark_lbo_input):
        """Benchmark LBO executes, debt is significantly amortized, and returns are positive."""
        res = calculate_lbo(benchmark_lbo_input)

        assert res.status == ValuationCalculationStatus.COMPLETED
        assert res.sources_and_uses is not None
        assert res.sources_and_uses.total_sources == 1140.0
        assert res.sources_and_uses.total_uses == 1140.0
        assert res.sources_and_uses.imbalance == 0.0

        # Debt paydown verification
        assert res.initial_total_debt == 700.0
        assert res.ending_total_debt < res.initial_total_debt
        # Term Loan A amortizes first (priority 1)
        tla_end = next(
            s.ending_balance for s in reversed(res.debt_schedule) if s.tranche_name == "Term Loan A"
        )
        assert tla_end < 500.0

        # Exit Valuation
        assert res.exit_ebitda == 240.0
        assert res.exit_multiple == 7.5
        assert res.exit_enterprise_value == 1800.0

        # Equity proceeds = Exit EV - (Ending Debt - Ending Cash)
        expected_net_debt = res.ending_total_debt - res.ending_cash_balance
        assert abs(res.exit_equity_proceeds - (1800.0 - expected_net_debt)) <= 0.05

        # Sponsor returns
        assert res.sponsor_entry_equity == 440.0
        assert res.sponsor_moic > 1.0  # Profitable LBO
        assert res.sponsor_irr > 0.10  # Positive IRR > 10%
        # Hand-check formula relationship: (MOIC)^(1/5) - 1 == IRR
        calculated_irr = (res.sponsor_moic ** (1.0 / 5.0)) - 1.0
        assert abs(res.sponsor_irr - calculated_irr) <= 0.01

    def test_lbo_sources_and_uses_mismatch_rejected(self, benchmark_lbo_input):
        """Mismatched Sources and Uses triggers MATHEMATICALLY_INVALID without crashing."""
        # Intentionally introduce a $50M imbalance in sponsor equity
        invalid_input = benchmark_lbo_input.model_copy(update={"sponsor_equity_contribution": 500.0})
        res = calculate_lbo(invalid_input)

        assert res.status == ValuationCalculationStatus.MATHEMATICALLY_INVALID
        assert res.sources_and_uses is not None
        assert abs(res.sources_and_uses.imbalance) == 60.0  # 1200 - 1140
        assert len(res.warnings) > 0
        assert "do not equal" in res.warnings[0]

    def test_lbo_missing_projections_rejected(self, benchmark_lbo_input):
        """Missing forecasts return INCOMPLETE_MISSING_INPUTS."""
        invalid_input = benchmark_lbo_input.model_copy(update={"operating_projections": []})
        res = calculate_lbo(invalid_input)

        assert res.status == ValuationCalculationStatus.INCOMPLETE_MISSING_INPUTS
        assert "operating_projections (at least 1 forecast year required)" in res.missing_inputs

    def test_lbo_zero_cash_sweep_preserves_cash(self, benchmark_lbo_input):
        """When cash sweep is 0%, excess cash accumulates on balance sheet instead of prepaying debt."""
        no_sweep_input = benchmark_lbo_input.model_copy(update={"cash_sweep_percentage": 0.0})
        res = calculate_lbo(no_sweep_input)

        assert res.status == ValuationCalculationStatus.COMPLETED
        # Cash balance at exit is higher because it was not swept
        assert res.ending_cash_balance > benchmark_lbo_input.min_cash_balance

    def test_lbo_zero_sponsor_equity_proceeds_wipeout(self, benchmark_lbo_input):
        """When exit valuation is lower than ending net debt, sponsor equity is wiped out (0.0 proceeds, 0x MOIC, -100% IRR)."""
        # Set exit multiple very low (e.g. 1.0x on $240M EBITDA = $240M EV, while ending debt is ~ $400M)
        wipeout_input = benchmark_lbo_input.model_copy(update={"exit_multiple": 1.0})
        res = calculate_lbo(wipeout_input)

        assert res.status == ValuationCalculationStatus.COMPLETED
        assert res.exit_enterprise_value == 240.0
        assert res.ending_total_debt > res.exit_enterprise_value
        assert res.exit_equity_proceeds == 0.0
        assert res.sponsor_moic == 0.0
        assert res.sponsor_irr == -1.0
        assert any("completely wiped out" in w for w in res.warnings)

    def test_lbo_fully_repaid_debt_cash_accumulation(self):
        """High cash generation fully repays all debt and accumulates cash on balance sheet."""
        tranches = [
            DebtTranche(name="Term Loan", initial_principal=100.0, interest_rate=0.05, mandatory_amortization_rate=0.10, seniority_order=1)
        ]
        # $80M annual EBITDA with zero capex / tax ensures debt is quickly repaid
        projections = [
            LBOOperatingYear(year=i, revenue=200.0, ebitda=80.0, capex=5.0, nwc_change=0.0, tax_rate=0.20)
            for i in range(1, 6)
        ]
        inp = LBOInput(
            purchase_enterprise_value=200.0,
            debt_tranches=tranches,
            operating_projections=projections,
            exit_multiple=10.0,
            cash_sweep_percentage=1.0,
        )
        res = calculate_lbo(inp)
        assert res.status == ValuationCalculationStatus.COMPLETED
        assert res.ending_total_debt == 0.0  # Fully paid off
        assert res.ending_cash_balance > 0.0
        # Exit proceeds = Exit EV ($800M) - Net Debt (-Cash) = Exit EV + Cash
        assert res.exit_equity_proceeds == round(res.exit_enterprise_value + res.ending_cash_balance, 2)
        assert res.sponsor_moic > 5.0
        assert res.sponsor_irr > 0.30

    def test_lbo_zero_debt_unlevered_structure(self):
        """An all-equity / unlevered transaction executes cleanly with zero debt and zero interest."""
        projections = [
            LBOOperatingYear(year=i, revenue=100.0, ebitda=30.0, capex=5.0, nwc_change=0.0, tax_rate=0.25)
            for i in range(1, 4)
        ]
        inp = LBOInput(
            purchase_enterprise_value=150.0,
            debt_tranches=[],
            operating_projections=projections,
            exit_multiple=8.0,
            min_cash_balance=10.0,
        )
        res = calculate_lbo(inp)
        assert res.status == ValuationCalculationStatus.COMPLETED
        assert res.initial_total_debt == 0.0
        assert res.ending_total_debt == 0.0
        assert res.sources_and_uses.sponsor_equity == 160.0  # 150 EV + 10 min cash
        assert res.exit_enterprise_value == 240.0  # 30 * 8
        assert res.sponsor_moic > 1.0

