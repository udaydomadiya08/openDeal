"""Pure, deterministic financial valuation primitives with zero LLM arithmetic and zero external dependencies."""

from __future__ import annotations

import statistics
from typing import List, Optional

from opendeal.valuation.engine.contracts import (
    ComparableValuationInput,
    ComparableValuationOutput,
    DCFInput,
    DCFOutput,
    DiscountingConvention,
    EVBridgeInput,
    EVBridgeOutput,
    GordonGrowthInput,
    GordonGrowthOutput,
    NAVInput,
    NAVOutput,
    SensitivityMatrixInput,
    SensitivityMatrixOutput,
    ValuationCalculationStatus,
    WACCInput,
    WACCOutput,
)


def calculate_wacc(inp: WACCInput) -> WACCOutput:
    """Calculate Weighted Average Cost of Capital (WACC) with strict input validation.

    Never manufactures a risk-free rate, beta, or capital structure weight.
    """
    missing: List[str] = []
    warnings: List[str] = []

    # 1. Determine Cost of Equity
    cost_of_equity: Optional[float] = inp.cost_of_equity
    if cost_of_equity is None:
        if inp.risk_free_rate is not None and inp.beta is not None and inp.market_risk_premium is not None:
            # CAPM: Ke = Rf + Beta * ERP
            cost_of_equity = inp.risk_free_rate + (inp.beta * inp.market_risk_premium)
        else:
            if inp.risk_free_rate is None:
                missing.append("risk_free_rate")
            if inp.beta is None:
                missing.append("beta")
            if inp.market_risk_premium is None:
                missing.append("market_risk_premium")

    # 2. Determine Capital Structure Weights
    we: Optional[float] = inp.equity_weight
    wd: Optional[float] = inp.debt_weight

    if we is None or wd is None:
        if inp.equity_market_value is not None and inp.total_debt_value is not None:
            total_v = inp.equity_market_value + inp.total_debt_value
            if total_v > 0:
                we = inp.equity_market_value / total_v
                wd = inp.total_debt_value / total_v
            else:
                warnings.append("Total firm enterprise value (Equity + Debt) is zero; weights cannot be derived.")
                missing.extend(["equity_weight", "debt_weight"])
        else:
            if inp.equity_weight is None and inp.equity_market_value is None:
                missing.append("equity_weight (or equity_market_value)")
            if inp.debt_weight is None and inp.total_debt_value is None:
                missing.append("debt_weight (or total_debt_value)")

    if missing:
        return WACCOutput(
            status=ValuationCalculationStatus.INCOMPLETE_MISSING_INPUTS,
            missing_inputs=missing,
            warnings=warnings,
            formula_expression="WACC = (We * Ke) + (Wd * Kd * (1 - t))",
        )

    assert cost_of_equity is not None
    assert we is not None
    assert wd is not None

    # Validate weight consistency
    if abs((we + wd) - 1.0) > 0.01:
        warnings.append(f"Capital structure weights do not sum to 1.0 (We={we:.4f}, Wd={wd:.4f}).")

    # 3. Calculate After-Tax Cost of Debt
    after_tax_kd = inp.pre_tax_cost_of_debt * (1.0 - inp.tax_rate)

    # 4. Compute WACC
    wacc = (we * cost_of_equity) + (wd * after_tax_kd)

    return WACCOutput(
        status=ValuationCalculationStatus.COMPLETED,
        wacc=round(wacc, 6),
        cost_of_equity=round(cost_of_equity, 6),
        after_tax_cost_of_debt=round(after_tax_kd, 6),
        equity_weight=round(we, 6),
        debt_weight=round(wd, 6),
        formula_expression=f"({we:.4f} * {cost_of_equity:.4f}) + ({wd:.4f} * {after_tax_kd:.4f})",
        missing_inputs=[],
        warnings=warnings,
    )


def calculate_gordon_terminal_value(inp: GordonGrowthInput) -> GordonGrowthOutput:
    """Calculate Terminal Value using the Gordon Growth Perpetual method.

    Validates that discount_rate > growth_rate. Rejects invalid inputs.
    """
    warnings: List[str] = []

    # Mathematical validity check: r must strictly exceed g
    if inp.discount_rate <= inp.growth_rate:
        msg = (
            f"Mathematically invalid: Discount rate ({inp.discount_rate:.4%}) must strictly exceed "
            f"terminal growth rate ({inp.growth_rate:.4%}). Value approaches infinity."
        )
        return GordonGrowthOutput(
            status=ValuationCalculationStatus.MATHEMATICALLY_INVALID,
            terminal_value=None,
            discount_rate=inp.discount_rate,
            growth_rate=inp.growth_rate,
            formula_expression="TV = FCF_n * (1 + g) / (WACC - g)",
            error_message=msg,
        )

    if inp.growth_rate > 0.045:
        warnings.append(
            f"Terminal perpetual growth rate ({inp.growth_rate:.2%}) exceeds typical long-term GDP growth rate."
        )

    # Next year perpetual cash flow: FCF_{n+1} = FCF_n * (1 + g)
    next_year_fcf = inp.final_year_fcf * (1.0 + inp.growth_rate)
    tv = next_year_fcf / (inp.discount_rate - inp.growth_rate)

    return GordonGrowthOutput(
        status=ValuationCalculationStatus.COMPLETED,
        terminal_value=round(tv, 2),
        discount_rate=inp.discount_rate,
        growth_rate=inp.growth_rate,
        formula_expression=f"{next_year_fcf:.2f} / ({inp.discount_rate:.4f} - {inp.growth_rate:.4f})",
        warnings=warnings,
    )


def calculate_dcf(inp: DCFInput) -> DCFOutput:
    """Execute complete Discounted Cash Flow valuation with mid-year or year-end timing."""
    warnings: List[str] = []

    # 1. Validate discount rate vs terminal growth rate
    if inp.discount_rate <= inp.terminal_growth_rate:
        return DCFOutput(
            status=ValuationCalculationStatus.MATHEMATICALLY_INVALID,
            discount_rate_wacc=inp.discount_rate,
            terminal_growth_rate=inp.terminal_growth_rate,
            discounting_convention=inp.discounting_convention.value,
            warnings=[
                f"Discount rate ({inp.discount_rate:.2%}) does not exceed terminal growth ({inp.terminal_growth_rate:.2%})."
            ],
        )

    n_years = len(inp.forecast_cash_flows)
    discount_factors: List[float] = []
    pv_cash_flows: List[float] = []

    # 2. Discount explicit cash flows
    for t_idx, fcf in enumerate(inp.forecast_cash_flows, start=1):
        if inp.discounting_convention == DiscountingConvention.MID_YEAR:
            exponent = t_idx - 0.5
        else:
            exponent = float(t_idx)

        df = 1.0 / ((1.0 + inp.discount_rate) ** exponent)
        pv_fcf = fcf * df

        discount_factors.append(round(df, 6))
        pv_cash_flows.append(round(pv_fcf, 2))

    pv_explicit = sum(pv_cash_flows)

    # 3. Compute Terminal Value
    final_fcf = inp.forecast_cash_flows[-1]
    tv_res = calculate_gordon_terminal_value(
        GordonGrowthInput(
            final_year_fcf=final_fcf,
            growth_rate=inp.terminal_growth_rate,
            discount_rate=inp.discount_rate,
        )
    )
    if tv_res.status != ValuationCalculationStatus.COMPLETED or tv_res.terminal_value is None:
        return DCFOutput(
            status=tv_res.status,
            discount_rate_wacc=inp.discount_rate,
            terminal_growth_rate=inp.terminal_growth_rate,
            discounting_convention=inp.discounting_convention.value,
            warnings=tv_res.warnings + [tv_res.error_message or "Terminal value calculation failed."],
        )

    undiscounted_tv = tv_res.terminal_value

    # Terminal value is discounted back from end of explicit forecast period n
    tv_discount_factor = 1.0 / ((1.0 + inp.discount_rate) ** n_years)
    pv_tv = undiscounted_tv * tv_discount_factor

    # 4. Enterprise Value
    enterprise_value = pv_explicit + pv_tv

    # 5. Bridge to Equity Value
    bridge = reconcile_enterprise_to_equity_value(
        EVBridgeInput(
            enterprise_value=enterprise_value,
            total_debt=inp.total_debt,
            cash_and_equivalents=inp.cash_and_equivalents,
            minority_interest=inp.minority_interest,
            preferred_equity=inp.preferred_equity,
            non_operating_assets=inp.non_operating_assets,
        )
    )
    equity_value = bridge.equity_value

    # 6. Implied Share Price
    implied_share_price: Optional[float] = None
    if inp.shares_outstanding is not None and inp.shares_outstanding > 0:
        implied_share_price = round(equity_value / inp.shares_outstanding, 2)

    tv_share = round((pv_tv / enterprise_value) if enterprise_value != 0 else 0.0, 4)

    return DCFOutput(
        status=ValuationCalculationStatus.COMPLETED,
        enterprise_value=round(enterprise_value, 2),
        equity_value=round(equity_value, 2),
        implied_share_price=implied_share_price,
        present_value_explicit_forecast=round(pv_explicit, 2),
        undiscounted_terminal_value=round(undiscounted_tv, 2),
        present_value_terminal_value=round(pv_tv, 2),
        terminal_value_share_of_ev=tv_share,
        discount_factors=discount_factors,
        discounted_cash_flows=pv_cash_flows,
        discount_rate_wacc=inp.discount_rate,
        terminal_growth_rate=inp.terminal_growth_rate,
        discounting_convention=inp.discounting_convention.value,
        warnings=warnings + tv_res.warnings,
    )


def reconcile_enterprise_to_equity_value(inp: EVBridgeInput) -> EVBridgeOutput:
    """Bridge Enterprise Value (EV) to Equity Value using standard institutional reconciliation."""
    net_debt = inp.total_debt - inp.cash_and_equivalents

    # Equity Value = EV - Debt + Cash - Minority Interest - Preferred Stock + Non-Operating Assets
    equity_value = (
        inp.enterprise_value
        - inp.total_debt
        + inp.cash_and_equivalents
        - inp.minority_interest
        - inp.preferred_equity
        + inp.non_operating_assets
    )

    bridge_components = {
        "enterprise_value": inp.enterprise_value,
        "total_debt": -inp.total_debt,
        "cash_and_equivalents": inp.cash_and_equivalents,
        "net_debt": -net_debt,
        "minority_interest": -inp.minority_interest,
        "preferred_equity": -inp.preferred_equity,
        "non_operating_assets": inp.non_operating_assets,
        "equity_value": equity_value,
    }

    return EVBridgeOutput(
        enterprise_value=round(inp.enterprise_value, 2),
        net_debt=round(net_debt, 2),
        equity_value=round(equity_value, 2),
        bridge_components={k: round(v, 2) for k, v in bridge_components.items()},
    )


def calculate_nav(inp: NAVInput) -> NAVOutput:
    """Calculate Net Asset Value (NAV) with marked-to-market adjustments."""
    total_asset_adj = sum(inp.asset_adjustments.values())
    total_liab_adj = sum(inp.liability_adjustments.values())

    adjusted_assets = inp.book_value_assets + total_asset_adj
    adjusted_liabilities = inp.book_value_liabilities + total_liab_adj
    nav = adjusted_assets - adjusted_liabilities

    return NAVOutput(
        adjusted_asset_value=round(adjusted_assets, 2),
        adjusted_liability_value=round(adjusted_liabilities, 2),
        net_asset_value=round(nav, 2),
        total_asset_adjustment=round(total_asset_adj, 2),
        total_liability_adjustment=round(total_liab_adj, 2),
    )


def calculate_comparable_valuation(inp: ComparableValuationInput) -> ComparableValuationOutput:
    """Compute implied enterprise and equity valuation from peer trading multiples."""
    sorted_multiples = sorted(inp.peer_multiples)
    peer_count = len(sorted_multiples)

    min_m = sorted_multiples[0]
    max_m = sorted_multiples[-1]
    med_m = statistics.median(sorted_multiples)
    mean_m = statistics.mean(sorted_multiples)

    if inp.is_ev_multiple:
        implied_ev_median = inp.subject_metric_value * med_m
        implied_equity_median = implied_ev_median - inp.total_debt + inp.cash_and_equivalents
        implied_equity_min = (inp.subject_metric_value * min_m) - inp.total_debt + inp.cash_and_equivalents
        implied_equity_max = (inp.subject_metric_value * max_m) - inp.total_debt + inp.cash_and_equivalents
    else:
        implied_ev_median = None
        implied_equity_median = inp.subject_metric_value * med_m
        implied_equity_min = inp.subject_metric_value * min_m
        implied_equity_max = inp.subject_metric_value * max_m

    return ComparableValuationOutput(
        multiple_type=inp.multiple_type,
        peer_count=peer_count,
        min_multiple=round(min_m, 2),
        max_multiple=round(max_m, 2),
        median_multiple=round(med_m, 2),
        mean_multiple=round(mean_m, 2),
        implied_ev_median=round(implied_ev_median, 2) if implied_ev_median is not None else None,
        implied_equity_value_median=round(implied_equity_median, 2),
        implied_equity_value_range=(round(implied_equity_min, 2), round(implied_equity_max, 2)),
    )


def calculate_sensitivity_matrix(inp: SensitivityMatrixInput) -> SensitivityMatrixOutput:
    """Generate a 2D DCF Equity Value sensitivity matrix varying WACC and Terminal Growth."""
    matrix: List[List[Optional[float]]] = []
    warnings: List[str] = []

    for r in inp.discount_rates:
        row: List[Optional[float]] = []
        for g in inp.terminal_growth_rates:
            if g >= r:
                row.append(None)  # Mathematically invalid
            else:
                dcf_config = inp.base_dcf_input.model_copy(update={"discount_rate": r, "terminal_growth_rate": g})
                res = calculate_dcf(dcf_config)
                if res.status == ValuationCalculationStatus.COMPLETED and res.equity_value is not None:
                    row.append(res.equity_value)
                else:
                    row.append(None)
        matrix.append(row)

    return SensitivityMatrixOutput(
        discount_rates=inp.discount_rates,
        terminal_growth_rates=inp.terminal_growth_rates,
        equity_value_matrix=matrix,
        warnings=warnings,
    )
