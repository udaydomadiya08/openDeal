"""Deterministic Leveraged Buyout (LBO) valuation engine.

Features:
- Sources and Uses of funds verification (strict balance constraint).
- Tranche-level debt schedules with mandatory amortization and priority cash sweeps.
- Multi-year Free Cash Flow Available for Debt Service (CFADS) calculation.
- Exit enterprise value and equity proceeds reconciliation.
- Sponsor returns: Multiple on Invested Capital (MOIC) and Internal Rate of Return (IRR).
- Rejection of invalid financing structures or mismatched sources/uses.
- Zero LLM arithmetic, purely reproducible deterministic calculations.
"""

from __future__ import annotations

from typing import Dict, List, Optional
from opendeal.valuation.engine.contracts import (
    DebtScheduleYear,
    DebtTranche,
    LBOInput,
    LBOOperatingYear,
    LBOOutput,
    LBOSourcesAndUses,
    ValuationCalculationStatus,
)


def calculate_lbo(inp: LBOInput) -> LBOOutput:
    """Execute complete deterministic Leveraged Buyout analysis."""
    missing: List[str] = []
    warnings: List[str] = []

    # 1. Input Validation
    if inp.purchase_enterprise_value <= 0:
        missing.append("purchase_enterprise_value (must be > 0)")
    if inp.exit_multiple <= 0:
        missing.append("exit_multiple (must be > 0)")
    if not inp.operating_projections:
        missing.append("operating_projections (at least 1 forecast year required)")

    if missing:
        return LBOOutput(
            status=ValuationCalculationStatus.INCOMPLETE_MISSING_INPUTS,
            missing_inputs=missing,
            warnings=warnings,
        )

    # 2. Reconcile Sources and Uses of Funds
    total_debt_borrowed = sum(t.initial_principal for t in inp.debt_tranches)
    debt_sources_dict = {t.name: t.initial_principal for t in inp.debt_tranches}

    # Standard LBO Uses: Purchase Equity Consideration + Refinanced Existing Debt + Fees + Min Cash
    purchase_equity = inp.purchase_enterprise_value
    total_uses = (
        purchase_equity
        + inp.refinanced_debt
        + inp.transaction_fees
        + inp.financing_fees
        + inp.min_cash_balance
    )

    if inp.sponsor_equity_contribution is not None:
        sponsor_equity = inp.sponsor_equity_contribution
        total_sources = total_debt_borrowed + sponsor_equity
        imbalance = round(total_sources - total_uses, 2)
        if abs(imbalance) > 0.01:
            return LBOOutput(
                status=ValuationCalculationStatus.MATHEMATICALLY_INVALID,
                sources_and_uses=LBOSourcesAndUses(
                    debt_sources=debt_sources_dict,
                    sponsor_equity=sponsor_equity,
                    total_sources=round(total_sources, 2),
                    purchase_equity=round(purchase_equity, 2),
                    refinanced_debt=inp.refinanced_debt,
                    transaction_fees=inp.transaction_fees,
                    financing_fees=inp.financing_fees,
                    min_cash_balance=inp.min_cash_balance,
                    total_uses=round(total_uses, 2),
                    imbalance=imbalance,
                ),
                missing_inputs=[],
                warnings=[
                    f"Sources ({total_sources:,.2f}) do not equal Uses ({total_uses:,.2f}). Imbalance: {imbalance:,.2f}"
                ],
            )
    else:
        # Sponsor equity plugs the gap to balance sources and uses
        sponsor_equity = max(0.0, total_uses - total_debt_borrowed)
        total_sources = total_debt_borrowed + sponsor_equity
        imbalance = 0.0

    sources_and_uses = LBOSourcesAndUses(
        debt_sources=debt_sources_dict,
        sponsor_equity=round(sponsor_equity, 2),
        total_sources=round(total_sources, 2),
        purchase_equity=round(purchase_equity, 2),
        refinanced_debt=inp.refinanced_debt,
        transaction_fees=inp.transaction_fees,
        financing_fees=inp.financing_fees,
        min_cash_balance=inp.min_cash_balance,
        total_uses=round(total_uses, 2),
        imbalance=imbalance,
    )

    # 3. Multi-Year Debt Schedules & Cash Sweep
    # Tranches sorted by seniority (priority 1 is senior, repaid first)
    sorted_tranches = sorted(inp.debt_tranches, key=lambda t: (t.seniority_order, t.name))
    current_balances = {t.name: t.initial_principal for t in sorted_tranches}
    current_cash = inp.min_cash_balance

    debt_schedule_records: List[DebtScheduleYear] = []
    holding_years = len(inp.operating_projections)

    for op in inp.operating_projections:
        # Step A: Interest expense on beginning balances
        year_interest_by_tranche: Dict[str, float] = {}
        for t in sorted_tranches:
            beg_bal = current_balances[t.name]
            interest = beg_bal * t.interest_rate
            year_interest_by_tranche[t.name] = interest

        total_interest = sum(year_interest_by_tranche.values())

        # Step B: Taxes and Free Cash Flow Available for Debt Service (CFADS)
        # Taxable income = EBITDA - Total Interest (with zero floor on tax)
        taxable_income = max(0.0, op.ebitda - total_interest)
        income_tax = taxable_income * op.tax_rate

        # CFADS = EBITDA - Interest - Tax - Capex - NWC Change
        cfads = op.ebitda - total_interest - income_tax - op.capex - op.nwc_change

        # Step C: Mandatory Amortization
        year_mandatory_by_tranche: Dict[str, float] = {}
        for t in sorted_tranches:
            beg_bal = current_balances[t.name]
            # Mandatory repayment cannot exceed current balance
            mand_target = t.initial_principal * t.mandatory_amortization_rate
            actual_mand = min(beg_bal, mand_target)
            year_mandatory_by_tranche[t.name] = actual_mand

        total_mandatory = sum(year_mandatory_by_tranche.values())
        cash_after_mandatory = cfads - total_mandatory

        # Step D: Cash Sweep on Senior Debt
        year_sweep_by_tranche: Dict[str, float] = {t.name: 0.0 for t in sorted_tranches}
        if cash_after_mandatory > 0 and inp.cash_sweep_percentage > 0:
            sweep_pool = cash_after_mandatory * inp.cash_sweep_percentage
            unallocated_sweep = sweep_pool

            for t in sorted_tranches:
                beg_bal = current_balances[t.name]
                mand = year_mandatory_by_tranche[t.name]
                post_mand_bal = max(0.0, beg_bal - mand)

                if post_mand_bal > 0 and unallocated_sweep > 0:
                    sweep_alloc = min(post_mand_bal, unallocated_sweep)
                    year_sweep_by_tranche[t.name] = sweep_alloc
                    unallocated_sweep -= sweep_alloc

            # Retain any unswept cash on balance sheet
            retained_cash = cash_after_mandatory - (sweep_pool - unallocated_sweep)
            current_cash += retained_cash
        else:
            if cash_after_mandatory > 0:
                current_cash += cash_after_mandatory
            else:
                # Cash deficit absorbed from cash balance
                current_cash += cash_after_mandatory
                if current_cash < 0:
                    warnings.append(
                        f"Year {op.year}: Operating cash flow deficit of {abs(cash_after_mandatory):,.2f} "
                        f"depleted opening cash reserves (ending cash: {current_cash:,.2f})."
                    )

        # Step E: Update ending balances and commit schedule
        for t in sorted_tranches:
            beg_bal = current_balances[t.name]
            mand = year_mandatory_by_tranche[t.name]
            swp = year_sweep_by_tranche[t.name]
            end_bal = max(0.0, beg_bal - mand - swp)

            debt_schedule_records.append(
                DebtScheduleYear(
                    year=op.year,
                    tranche_name=t.name,
                    beginning_balance=round(beg_bal, 2),
                    interest_expense=round(year_interest_by_tranche[t.name], 2),
                    mandatory_repayment=round(mand, 2),
                    sweep_repayment=round(swp, 2),
                    ending_balance=round(end_bal, 2),
                )
            )
            current_balances[t.name] = end_bal

    # 4. Exit Valuation & Returns
    ending_total_debt = sum(current_balances.values())
    net_debt_at_exit = max(0.0, ending_total_debt - max(0.0, current_cash))

    exit_ebitda = inp.operating_projections[-1].ebitda
    exit_enterprise_value = exit_ebitda * inp.exit_multiple
    exit_equity_proceeds = max(0.0, exit_enterprise_value - (ending_total_debt - current_cash))

    # Multiple on Invested Capital (MOIC)
    if sponsor_equity > 0:
        moic = exit_equity_proceeds / sponsor_equity
        # Annualized Internal Rate of Return (IRR)
        if moic > 0:
            irr = (moic ** (1.0 / holding_years)) - 1.0
        else:
            irr = -1.0  # Total loss
            warnings.append(
                "Exit enterprise value is insufficient to cover ending net debt; "
                "sponsor equity was completely wiped out (exit proceeds: $0.00, MOIC: 0.00x, IRR: -100.00%)."
            )
    else:
        moic = 0.0
        irr = 0.0
        warnings.append("Sponsor equity contribution is zero; return multiples cannot be calculated.")

    return LBOOutput(
        status=ValuationCalculationStatus.COMPLETED,
        sources_and_uses=sources_and_uses,
        debt_schedule=debt_schedule_records,
        initial_total_debt=round(total_debt_borrowed, 2),
        ending_total_debt=round(ending_total_debt, 2),
        ending_cash_balance=round(current_cash, 2),
        net_debt_at_exit=round(net_debt_at_exit, 2),
        exit_ebitda=round(exit_ebitda, 2),
        exit_multiple=round(inp.exit_multiple, 2),
        exit_enterprise_value=round(exit_enterprise_value, 2),
        exit_equity_proceeds=round(exit_equity_proceeds, 2),
        sponsor_entry_equity=round(sponsor_equity, 2),
        sponsor_moic=round(moic, 2),
        sponsor_irr=round(irr, 4),
        holding_period_years=holding_years,
        missing_inputs=[],
        warnings=warnings,
    )
