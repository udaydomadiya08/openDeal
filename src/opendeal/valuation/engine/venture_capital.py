"""Deterministic Venture Capital (VC) valuation and returns engine.

Features:
- Strict Pre-Money / Post-Money consistency and initial ownership math.
- Multi-round dilution modeling (Series follow-ons, option pool expansion) with retention rate derivation.
- Multi-scenario liquidity event analysis (Downside, Base, Bull).
- Exact Multiple on Invested Capital (MOIC) and annualized Internal Rate of Return (IRR).
- Explicit validation; never manufactures missing pre-money valuations or stage benchmarks.
"""

from __future__ import annotations

from typing import List, Optional

from opendeal.valuation.engine.contracts import (
    ValuationCalculationStatus,
    VCExitScenario,
    VCScenarioResult,
    VentureCapitalInput,
    VentureCapitalOutput,
)


def calculate_venture_capital(inp: VentureCapitalInput) -> VentureCapitalOutput:
    """Execute deterministic Venture Capital stage-gate valuation analysis."""
    missing: List[str] = []
    warnings: List[str] = []

    # 1. Input Validation
    if inp.investment_amount <= 0:
        missing.append("investment_amount (must be > 0)")

    if inp.pre_money_valuation is None and inp.post_money_valuation is None:
        missing.append("pre_money_valuation or post_money_valuation (at least one must be provided)")

    if missing:
        return VentureCapitalOutput(
            status=ValuationCalculationStatus.INCOMPLETE_MISSING_INPUTS,
            missing_inputs=missing,
            warnings=warnings,
        )

    # 2. Reconcile Pre-Money and Post-Money Valuation
    if inp.pre_money_valuation is not None and inp.post_money_valuation is not None:
        expected_post = inp.pre_money_valuation + inp.investment_amount
        if abs(expected_post - inp.post_money_valuation) > 0.01:
            return VentureCapitalOutput(
                status=ValuationCalculationStatus.MATHEMATICALLY_INVALID,
                pre_money_valuation=inp.pre_money_valuation,
                investment_amount=inp.investment_amount,
                post_money_valuation=inp.post_money_valuation,
                missing_inputs=[],
                warnings=[
                    f"Post-money valuation ({inp.post_money_valuation:,.2f}) does not equal "
                    f"Pre-money ({inp.pre_money_valuation:,.2f}) + Investment ({inp.investment_amount:,.2f})."
                ],
            )
        pre_money = inp.pre_money_valuation
        post_money = inp.post_money_valuation
    elif inp.pre_money_valuation is not None:
        pre_money = inp.pre_money_valuation
        post_money = pre_money + inp.investment_amount
    else:
        assert inp.post_money_valuation is not None
        post_money = inp.post_money_valuation
        if post_money < inp.investment_amount:
            return VentureCapitalOutput(
                status=ValuationCalculationStatus.MATHEMATICALLY_INVALID,
                pre_money_valuation=0.0,
                investment_amount=inp.investment_amount,
                post_money_valuation=post_money,
                warnings=["Post-money valuation cannot be less than the investment amount."],
            )
        pre_money = post_money - inp.investment_amount

    # 3. Ownership and Cumulative Dilution Retention
    initial_ownership = inp.investment_amount / post_money if post_money > 0 else 0.0

    retention_rate = 1.0
    for round_item in inp.dilution_rounds:
        retention_rate *= (1.0 - round_item.dilution_percentage)

    exit_ownership = initial_ownership * retention_rate

    # 4. Exit Scenarios Analysis
    scenario_results: List[VCScenarioResult] = []
    for sc in inp.exit_scenarios:
        # Standard venture liquidation modeling
        base_proceeds = sc.exit_valuation * exit_ownership
        pref_amount = inp.investment_amount * sc.liquidation_preference_multiple

        if sc.is_participating:
            pref_payout = min(sc.exit_valuation, pref_amount)
            remaining_val = max(0.0, sc.exit_valuation - pref_payout)
            proceeds = min(sc.exit_valuation, pref_payout + (remaining_val * exit_ownership))
        else:
            proceeds = max(pref_amount, base_proceeds) if sc.exit_valuation >= pref_amount else sc.exit_valuation

        moic = proceeds / inp.investment_amount if inp.investment_amount > 0 else 0.0
        if moic > 0:
            irr = (moic ** (1.0 / sc.holding_period_years)) - 1.0
        else:
            irr = -1.0

        scenario_results.append(
            VCScenarioResult(
                scenario_name=sc.scenario_name,
                exit_valuation=round(sc.exit_valuation, 2),
                investor_proceeds=round(proceeds, 2),
                moic=round(moic, 2),
                irr=round(irr, 4),
                holding_period_years=sc.holding_period_years,
            )
        )

    return VentureCapitalOutput(
        status=ValuationCalculationStatus.COMPLETED,
        pre_money_valuation=round(pre_money, 2),
        investment_amount=round(inp.investment_amount, 2),
        post_money_valuation=round(post_money, 2),
        initial_ownership_percentage=round(initial_ownership, 4),
        retention_rate=round(retention_rate, 4),
        exit_ownership_percentage=round(exit_ownership, 4),
        scenario_results=scenario_results,
        missing_inputs=[],
        warnings=warnings,
    )
