"""Independent unit tests for Venture Capital (VC) valuation.

Verifies:
- Hand-calculated VC stage-gate benchmark.
- Pre-money / Post-money mathematical reconciliation.
- Multi-round cumulative retention and dilution decay.
- Liquidity exit scenarios with MOIC and annualized IRR.
- Rejection of conflicting pre/post valuations with MATHEMATICALLY_INVALID.
- Missing inputs return INCOMPLETE_MISSING_INPUTS.
"""

import pytest

from opendeal.valuation.engine.contracts import (
    DilutionRound,
    ValuationCalculationStatus,
    VCExitScenario,
    VentureCapitalInput,
)
from opendeal.valuation.engine.venture_capital import calculate_venture_capital


class TestVentureCapitalEngine:
    """Tests for Venture Capital stage-gate valuation."""

    def test_vc_benchmark_hand_calculated(self):
        """Hand-calculated benchmark:
        Investment: $5.0M
        Pre-money: $15.0M
        Post-money = 15.0 + 5.0 = $20.0M
        Initial Ownership = 5.0 / 20.0 = 25.0% (0.2500)

        Dilution Rounds:
        - Series B: 20% dilution (retention = 0.80)
        - Option Pool: 10% dilution (retention = 0.90)
        Cumulative Retention = 0.80 * 0.90 = 0.72 (72.0%)
        Ownership at Exit = 0.25 * 0.72 = 0.18 (18.0%)

        Exit Scenario (Base):
        Exit Valuation: $100.0M in 5 years
        Investor Proceeds = 100.0 * 0.18 = $18.0M
        MOIC = 18.0 / 5.0 = 3.60x
        IRR = (3.60)^(1/5) - 1 = 1.292388 - 1 = 29.24%
        """
        inp = VentureCapitalInput(
            investment_amount=5.0,
            pre_money_valuation=15.0,
            dilution_rounds=[
                DilutionRound(round_name="Series B", dilution_percentage=0.20),
                DilutionRound(round_name="Option Pool", dilution_percentage=0.10),
            ],
            exit_scenarios=[
                VCExitScenario(scenario_name="Base", exit_valuation=100.0, holding_period_years=5.0),
                VCExitScenario(scenario_name="Bull", exit_valuation=250.0, holding_period_years=5.0),
            ],
        )

        res = calculate_venture_capital(inp)

        assert res.status == ValuationCalculationStatus.COMPLETED
        assert res.pre_money_valuation == 15.0
        assert res.post_money_valuation == 20.0
        assert res.initial_ownership_percentage == 0.25
        assert res.retention_rate == 0.72
        assert res.exit_ownership_percentage == 0.18

        base_sc = next(s for s in res.scenario_results if s.scenario_name == "Base")
        assert base_sc.exit_valuation == 100.0
        assert base_sc.investor_proceeds == 18.0
        assert base_sc.moic == 3.60
        assert abs(base_sc.irr - 0.2924) <= 0.001

        bull_sc = next(s for s in res.scenario_results if s.scenario_name == "Bull")
        assert bull_sc.exit_valuation == 250.0
        assert bull_sc.investor_proceeds == 45.0  # 250 * 0.18
        assert bull_sc.moic == 9.00  # 45 / 5
        expected_bull_irr = (9.0 ** (1.0 / 5.0)) - 1.0
        assert abs(bull_sc.irr - expected_bull_irr) <= 0.001

    def test_vc_post_money_input_derives_pre_money(self):
        """Supplying post-money valuation correctly derives pre-money valuation."""
        inp = VentureCapitalInput(
            investment_amount=2.0,
            post_money_valuation=10.0,
        )
        res = calculate_venture_capital(inp)
        assert res.status == ValuationCalculationStatus.COMPLETED
        assert res.pre_money_valuation == 8.0
        assert res.post_money_valuation == 10.0
        assert res.initial_ownership_percentage == 0.20

    def test_vc_inconsistent_pre_and_post_money_rejected(self):
        """Inconsistent pre-money and post-money valuations trigger MATHEMATICALLY_INVALID."""
        inp = VentureCapitalInput(
            investment_amount=5.0,
            pre_money_valuation=15.0,
            post_money_valuation=25.0,  # Should be 20.0!
        )
        res = calculate_venture_capital(inp)
        assert res.status == ValuationCalculationStatus.MATHEMATICALLY_INVALID
        assert "does not equal Pre-money" in res.warnings[0]

    def test_vc_missing_valuation_inputs_rejected(self):
        """Missing both pre-money and post-money valuations returns INCOMPLETE_MISSING_INPUTS."""
        inp = VentureCapitalInput(
            investment_amount=5.0,
        )
        res = calculate_venture_capital(inp)
        assert res.status == ValuationCalculationStatus.INCOMPLETE_MISSING_INPUTS
        assert any("pre_money_valuation" in m for m in res.missing_inputs)

    def test_vc_participating_preferred_cap_when_exit_below_preference(self):
        """Participating preferred proceeds cannot exceed total exit valuation when exit < preference."""
        inp = VentureCapitalInput(
            investment_amount=5.0,
            pre_money_valuation=15.0,  # post-money = 20.0, ownership = 25%
            exit_scenarios=[
                VCExitScenario(
                    scenario_name="Distressed Exit",
                    exit_valuation=2.0,  # Below $5.0M preference
                    holding_period_years=3.0,
                    liquidation_preference_multiple=1.0,
                    is_participating=True,
                )
            ],
        )
        res = calculate_venture_capital(inp)
        assert res.status == ValuationCalculationStatus.COMPLETED
        sc = res.scenario_results[0]
        # Investor proceeds must be capped at total company exit valuation ($2.0M), NOT $5.0M!
        assert sc.investor_proceeds == 2.0
        assert sc.moic == 0.40  # 2.0 / 5.0
        assert sc.irr < 0.0

    def test_vc_zero_exit_valuation(self):
        """Total company failure ($0 exit) produces $0 proceeds, 0.0x MOIC, and -100% IRR."""
        inp = VentureCapitalInput(
            investment_amount=5.0,
            pre_money_valuation=15.0,
            exit_scenarios=[
                VCExitScenario(
                    scenario_name="Failure",
                    exit_valuation=0.0,
                    holding_period_years=4.0,
                    liquidation_preference_multiple=1.0,
                    is_participating=False,
                )
            ],
        )
        res = calculate_venture_capital(inp)
        assert res.status == ValuationCalculationStatus.COMPLETED
        sc = res.scenario_results[0]
        assert sc.investor_proceeds == 0.0
        assert sc.moic == 0.0
        assert sc.irr == -1.0

    def test_vc_exit_below_preference_non_participating(self):
        """Non-participating preferred takes full exit proceeds when exit is below preference."""
        inp = VentureCapitalInput(
            investment_amount=5.0,
            pre_money_valuation=15.0,
            exit_scenarios=[
                VCExitScenario(
                    scenario_name="Sub-pref Exit",
                    exit_valuation=3.5,
                    holding_period_years=2.0,
                    liquidation_preference_multiple=1.0,
                    is_participating=False,
                )
            ],
        )
        res = calculate_venture_capital(inp)
        assert res.status == ValuationCalculationStatus.COMPLETED
        sc = res.scenario_results[0]
        assert sc.investor_proceeds == 3.5
        assert sc.moic == 0.70

