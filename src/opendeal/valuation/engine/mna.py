"""Deterministic M&A EPS Accretion / Dilution analysis engine.

Features:
- Structured purchase consideration derivation from share offer prices or direct equity values.
- Reconciled financing mix (Cash, Debt, Stock).
- Pro forma share issuance and standalone buyer baseline comparison.
- Financing costs (new debt coupon, foregone cash interest) and after-tax synergy phase-ins.
- Exact EPS accretion/dilution in dollars and percentage terms.
- Closed-form mathematical derivation of breakeven pre-tax synergies.
- Explicit validation: Rejects incomplete deals without fabricating missing prices or synergies.
"""

from __future__ import annotations

from typing import List, Optional

from opendeal.valuation.engine.contracts import (
    AccretionDilutionInput,
    AccretionDilutionOutput,
    ValuationCalculationStatus,
)


def calculate_accretion_dilution(inp: AccretionDilutionInput) -> AccretionDilutionOutput:
    """Execute deterministic M&A EPS Accretion / Dilution valuation analysis."""
    missing: List[str] = []
    warnings: List[str] = []

    # 1. Input Validation
    if inp.buyer_share_price <= 0:
        missing.append("buyer_share_price (must be > 0)")
    if inp.buyer_shares_outstanding <= 0:
        missing.append("buyer_shares_outstanding (must be > 0)")

    # 2. Derive Purchase Consideration
    purchase_equity_value: Optional[float] = None
    offer_premium: Optional[float] = None

    if inp.target_purchase_equity_value is not None:
        purchase_equity_value = inp.target_purchase_equity_value
        if inp.target_share_price is not None and inp.target_shares_outstanding is not None:
            standalone_target_val = inp.target_share_price * inp.target_shares_outstanding
            if standalone_target_val > 0:
                offer_premium = (purchase_equity_value - standalone_target_val) / standalone_target_val

    elif inp.target_shares_outstanding is not None:
        if inp.offer_price_per_share is not None:
            purchase_equity_value = inp.offer_price_per_share * inp.target_shares_outstanding
            if inp.target_share_price is not None and inp.target_share_price > 0:
                offer_premium = (inp.offer_price_per_share - inp.target_share_price) / inp.target_share_price
        elif inp.target_share_price is not None:
            standalone_target_val = inp.target_share_price * inp.target_shares_outstanding
            purchase_equity_value = standalone_target_val
            offer_premium = 0.0

    if purchase_equity_value is None or purchase_equity_value <= 0:
        missing.append(
            "target_purchase_equity_value (or target_shares_outstanding + offer_price_per_share/target_share_price)"
        )

    if missing:
        return AccretionDilutionOutput(
            status=ValuationCalculationStatus.INCOMPLETE_MISSING_INPUTS,
            missing_inputs=missing,
            warnings=warnings,
        )

    assert purchase_equity_value is not None

    # 3. Consideration Financing Breakdown
    mix = inp.consideration_mix
    cash_funded = purchase_equity_value * mix.cash_percentage
    debt_funded = purchase_equity_value * mix.debt_percentage
    stock_funded = purchase_equity_value * mix.stock_percentage

    # 4. Share Counts
    new_shares_issued = stock_funded / inp.buyer_share_price
    pro_forma_shares = inp.buyer_shares_outstanding + new_shares_issued

    buyer_standalone_eps = inp.buyer_net_income / inp.buyer_shares_outstanding

    # 5. Financing Costs & Earnings Adjustments
    incremental_debt_interest = debt_funded * inp.new_debt_interest_rate
    foregone_cash_interest = cash_funded * inp.foregone_cash_interest_rate
    total_financing_costs_pre_tax = incremental_debt_interest + foregone_cash_interest

    after_tax_financing_costs = total_financing_costs_pre_tax * (1.0 - inp.buyer_tax_rate)
    after_tax_synergies = inp.pre_tax_synergies * (1.0 - inp.buyer_tax_rate)

    # Pro Forma Net Income = Buyer NI + Target NI + After-Tax Synergies - After-Tax Financing - Intangible Amort
    pro_forma_net_income = (
        inp.buyer_net_income
        + inp.target_net_income
        + after_tax_synergies
        - after_tax_financing_costs
        - inp.after_tax_intangible_amortization
    )

    pro_forma_eps = pro_forma_net_income / pro_forma_shares
    eps_dollar_diff = pro_forma_eps - buyer_standalone_eps
    eps_pct_diff = (eps_dollar_diff / buyer_standalone_eps) if buyer_standalone_eps != 0 else 0.0
    is_accretive = eps_dollar_diff > 0.0

    # 6. Breakeven Pre-Tax Synergies Calculation
    # Breakeven condition: Pro Forma Net Income / Pro Forma Shares = Buyer Standalone EPS
    # Required Pro Forma Net Income = Buyer Standalone EPS * Pro Forma Shares
    required_pro_forma_ni = buyer_standalone_eps * pro_forma_shares
    required_after_tax_synergies = (
        required_pro_forma_ni
        - inp.buyer_net_income
        - inp.target_net_income
        + after_tax_financing_costs
        + inp.after_tax_intangible_amortization
    )
    breakeven_pre_tax_synergies = (
        required_after_tax_synergies / (1.0 - inp.buyer_tax_rate)
        if inp.buyer_tax_rate < 1.0
        else required_after_tax_synergies
    )

    return AccretionDilutionOutput(
        status=ValuationCalculationStatus.COMPLETED,
        purchase_equity_value=round(purchase_equity_value, 2),
        implied_offer_premium=round(offer_premium, 4) if offer_premium is not None else None,
        cash_funded=round(cash_funded, 2),
        debt_funded=round(debt_funded, 2),
        stock_funded=round(stock_funded, 2),
        new_shares_issued=round(new_shares_issued, 2),
        pro_forma_shares=round(pro_forma_shares, 2),
        buyer_standalone_eps=round(buyer_standalone_eps, 4),
        pro_forma_net_income=round(pro_forma_net_income, 2),
        pro_forma_eps=round(pro_forma_eps, 4),
        eps_accretion_dilution_dollars=round(eps_dollar_diff, 4),
        eps_accretion_dilution_percentage=round(eps_pct_diff, 4),
        is_accretive=is_accretive,
        breakeven_pre_tax_synergies=round(breakeven_pre_tax_synergies, 2),
        missing_inputs=[],
        warnings=warnings,
    )
