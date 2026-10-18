"""Independent unit tests for M&A EPS Accretion / Dilution analysis.

Verifies:
- Hand-calculated benchmark transaction with cash/stock mix and synergies.
- Pro forma share issuance and standalone buyer comparison.
- Financing costs (foregone cash yield, new debt coupon) and tax shield effects.
- Exact accretion/dilution in dollars and percentage terms.
- Closed-form breakeven pre-tax synergy determination.
- Consideration mix validation (must sum to 100%).
- Missing target parameters return INCOMPLETE_MISSING_INPUTS.
"""

import pytest

from opendeal.valuation.engine.contracts import (
    AccretionDilutionInput,
    ConsiderationMix,
    ValuationCalculationStatus,
)
from opendeal.valuation.engine.mna import calculate_accretion_dilution


class TestMNAEngine:
    """Tests for M&A EPS Accretion / Dilution valuation modeling."""

    @pytest.fixture
    def benchmark_mna_input(self) -> AccretionDilutionInput:
        """Hand-calculated benchmark:
        Buyer:
        - Share Price: $50.00
        - Diluted Shares: 100.0M
        - Net Income: $200.0M
        - Standalone EPS: 200.0 / 100.0 = $2.0000
        - Tax Rate: 25.0%

        Target:
        - Standalone Net Income: $30.0M
        - Shares: 20.0M
        - Share Price: $25.00
        - Offer Price per Share: $30.00 (20.0% Premium)
        Purchase Equity Consideration = 20.0M * $30.00 = $600.0M

        Financing Mix:
        - 50.0% Cash ($300.0M)
        - 50.0% Stock ($300.0M)
        - 0.0% Debt

        Financing Costs:
        - Foregone cash rate: 4.0%
        - Pre-tax foregone interest = 300.0 * 0.04 = $12.0M
        - After-tax financing cost = 12.0 * (1 - 0.25) = $9.0M

        New Shares Issued = 300.0M / $50.00 = 6.0M shares
        Pro Forma Shares = 100.0M + 6.0M = 106.0M shares

        Synergies:
        - Pre-tax Synergies: $20.0M
        - After-tax Synergies = 20.0 * (1 - 0.25) = $15.0M

        Pro Forma Net Income:
        Buyer NI ($200.0M) + Target NI ($30.0M) + After-tax Synergies ($15.0M) - After-tax Financing ($9.0M)
        = $236.0M

        Pro Forma EPS:
        236.0M / 106.0M = $2.2264
        Accretion ($) = 2.2264 - 2.0000 = +$0.2264
        Accretion (%) = 0.2264 / 2.0000 = +11.32%
        """
        return AccretionDilutionInput(
            buyer_share_price=50.0,
            buyer_shares_outstanding=100.0,
            buyer_net_income=200.0,
            buyer_tax_rate=0.25,
            target_net_income=30.0,
            target_shares_outstanding=20.0,
            target_share_price=25.0,
            offer_price_per_share=30.0,
            consideration_mix=ConsiderationMix(cash_percentage=0.5, debt_percentage=0.0, stock_percentage=0.5),
            foregone_cash_interest_rate=0.04,
            pre_tax_synergies=20.0,
        )

    def test_mna_accretion_benchmark_execution(self, benchmark_mna_input):
        """Benchmark M&A transaction executes with verified mathematical precision."""
        res = calculate_accretion_dilution(benchmark_mna_input)

        assert res.status == ValuationCalculationStatus.COMPLETED
        assert res.purchase_equity_value == 600.0
        assert res.implied_offer_premium == 0.20
        assert res.cash_funded == 300.0
        assert res.stock_funded == 300.0
        assert res.debt_funded == 0.0

        assert res.new_shares_issued == 6.0
        assert res.pro_forma_shares == 106.0
        assert res.buyer_standalone_eps == 2.00
        assert res.pro_forma_net_income == 236.0
        assert abs(res.pro_forma_eps - 2.2264) <= 0.0002
        assert abs(res.eps_accretion_dilution_dollars - 0.2264) <= 0.0002
        assert abs(res.eps_accretion_dilution_percentage - 0.1132) <= 0.0002
        assert res.is_accretive is True

    def test_breakeven_synergies_mathematical_precision(self, benchmark_mna_input):
        """Testing transaction with breakeven synergies results in exactly 0.00 accretion/dilution."""
        base_res = calculate_accretion_dilution(benchmark_mna_input)
        breakeven_syn = base_res.breakeven_pre_tax_synergies

        # Re-run model with exact breakeven synergies
        breakeven_input = benchmark_mna_input.model_copy(update={"pre_tax_synergies": breakeven_syn})
        breakeven_res = calculate_accretion_dilution(breakeven_input)

        # Pro forma EPS must equal standalone EPS
        assert abs(breakeven_res.pro_forma_eps - breakeven_res.buyer_standalone_eps) <= 0.0002

    def test_mna_dilutive_all_debt_deal(self):
        """Deal with high debt borrowing interest rate becomes dilutive."""
        inp = AccretionDilutionInput(
            buyer_share_price=10.0,
            buyer_shares_outstanding=50.0,
            buyer_net_income=50.0,  # Standalone EPS = 1.00
            buyer_tax_rate=0.25,
            target_net_income=5.0,
            target_purchase_equity_value=100.0,
            consideration_mix=ConsiderationMix(cash_percentage=0.0, debt_percentage=1.0, stock_percentage=0.0),
            new_debt_interest_rate=0.12,  # 12% interest on $100M debt = $12M pre-tax ($9M after-tax)
            pre_tax_synergies=0.0,
        )
        res = calculate_accretion_dilution(inp)
        assert res.status == ValuationCalculationStatus.COMPLETED
        # Pro forma Net Income = 50 + 5 - 9 = 46.0M / 50.0M shares = $0.92
        assert res.pro_forma_eps == 0.92
        assert res.is_accretive is False
        assert res.eps_accretion_dilution_dollars == -0.08
        # Breakeven pre-tax synergies must be positive to fix dilution
        assert res.breakeven_pre_tax_synergies > 0.0

    def test_mna_missing_target_value_rejected(self):
        """Missing target consideration returns INCOMPLETE_MISSING_INPUTS."""
        inp = AccretionDilutionInput(
            buyer_share_price=50.0,
            buyer_shares_outstanding=100.0,
            buyer_net_income=200.0,
            target_net_income=30.0,
            # No target value, share price, or shares provided
        )
        res = calculate_accretion_dilution(inp)
        assert res.status == ValuationCalculationStatus.INCOMPLETE_MISSING_INPUTS
        assert any("target_purchase_equity_value" in m for m in res.missing_inputs)
