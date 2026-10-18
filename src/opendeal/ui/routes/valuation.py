"""Valuation Workspace UI routes integrating deterministic Python analytical engines."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from opendeal.valuation import (
    DCFInput,
    DiscountingConvention,
    WACCInput,
    GordonGrowthInput,
    NAVInput,
    ComparableValuationInput,
    LBOInput,
    LBOOperatingYear,
    DebtTranche,
    VentureCapitalInput,
    VCExitScenario,
    AccretionDilutionInput,
    ConsiderationMix,
    calculate_dcf,
    calculate_wacc,
    calculate_gordon_terminal_value,
    calculate_nav,
    calculate_comparable_valuation,
    calculate_lbo,
    calculate_venture_capital,
    calculate_accretion_dilution,
)

router = APIRouter(prefix="/valuation")

MODEL_METADATA = {
    "dcf": {
        "name": "Discounted Cash Flow (DCF)",
        "description": "Multi-stage enterprise valuation using explicit Free Cash Flow projections and terminal Gordon Growth.",
    },
    "wacc": {
        "name": "Weighted Average Cost of Capital (WACC)",
        "description": "Blended institutional cost of capital with CAPM cost of equity and tax-shielded debt weighting.",
    },
    "gordon": {
        "name": "Gordon Growth Terminal Value",
        "description": "Perpetual terminal value calculation using stable terminal growth and discount rate hurdles.",
    },
    "nav": {
        "name": "Adjusted Net Asset Value (NAV)",
        "description": "Balance-sheet asset-based valuation with category-level fair-value mark-to-market adjustments.",
    },
    "comps": {
        "name": "Trading Multiples / Peer Comparables",
        "description": "Peer multiple distribution analysis deriving EV and Equity Value ranges from observed market comps.",
    },
    "lbo": {
        "name": "Leveraged Buyout (LBO) Analysis",
        "description": "Multi-year debt paydown schedules, cash sweeps, exit multiples, and sponsor IRR/MOIC returns.",
    },
    "vc": {
        "name": "Venture Capital Valuation",
        "description": "Ownership dilution schedules, pre/post-money capitalization, and multi-scenario liquidity returns.",
    },
    "accretion_dilution": {
        "name": "M&A EPS Accretion / Dilution",
        "description": "Pro forma buyer EPS impact across flexible cash, debt, and stock consideration financing mixes.",
    },
}


@router.get("", response_class=HTMLResponse)
async def valuation_index_view(request: Request):
    templates = request.app.state.templates
    storage = request.app.state.storage
    entities = storage.list_entities(limit=100)

    return templates.TemplateResponse(
        request,
        "valuation/index.html",
        {
            "request": request,
            "entities": entities,
        },
    )


@router.get("/{model_key}", response_class=HTMLResponse)
async def valuation_form_view(
    request: Request,
    model_key: str,
    entity_id: Optional[str] = Query(None),
):
    templates = request.app.state.templates
    storage = request.app.state.storage

    if model_key not in MODEL_METADATA:
        return templates.TemplateResponse(
        request,
        "error.html",
            {
                "request": request,
                "error_code": 404,
                "error_title": "Valuation Model Not Supported",
                "error_message": f"The valuation model '{model_key}' is not implemented in OpenDeal's deterministic engine.",
            },
            status_code=404,
        )

    meta = MODEL_METADATA[model_key]
    entities = storage.list_entities(limit=100)

    return templates.TemplateResponse(
        request,
        "valuation/form.html",
        {
            "request": request,
            "model_key": model_key,
            "model_name": meta["name"],
            "model_description": meta["description"],
            "entities": entities,
            "selected_entity_id": entity_id or "",
            "form_data": {},
            "error": None,
        },
    )


@router.post("/{model_key}/calculate", response_class=HTMLResponse)
async def valuation_calculate_view(request: Request, model_key: str):
    templates = request.app.state.templates
    storage = request.app.state.storage

    if model_key not in MODEL_METADATA:
        return templates.TemplateResponse(
        request,
        "error.html",
            {
                "request": request,
                "error_code": 404,
                "error_title": "Valuation Model Not Supported",
                "error_message": f"Model '{model_key}' is not recognized.",
            },
            status_code=404,
        )

    meta = MODEL_METADATA[model_key]
    form = await request.form()
    form_data = dict(form)
    entity_id = form_data.get("entity_id", "")

    try:
        key_metrics: List[Dict[str, Any]] = []
        output_rows: List[Dict[str, Any]] = []
        formula_expression = ""
        warnings: List[str] = []
        missing_inputs: List[str] = []
        status = "COMPLETED"

        if model_key == "dcf":
            fcfs_raw = str(form_data.get("forecast_cash_flows", "")).strip()
            fcfs = [float(x.strip()) for x in fcfs_raw.split(",") if x.strip()]
            discount_rate = float(form_data["discount_rate"])
            terminal_growth = float(form_data["terminal_growth_rate"])
            convention = (
                DiscountingConvention.MID_YEAR
                if form_data.get("discounting_convention") == "MID_YEAR"
                else DiscountingConvention.YEAR_END
            )
            cash = float(form_data.get("cash_and_equivalents") or 0.0)
            debt = float(form_data.get("total_debt") or 0.0)
            shares_raw = form_data.get("shares_outstanding")
            shares = float(shares_raw) if shares_raw and str(shares_raw).strip() else None

            dcf_in = DCFInput(
                forecast_cash_flows=fcfs,
                discount_rate=discount_rate,
                terminal_growth_rate=terminal_growth,
                discounting_convention=convention,
                cash_and_equivalents=cash,
                total_debt=debt,
                shares_outstanding=shares,
            )
            out = calculate_dcf(dcf_in)
            status = out.status.value if hasattr(out.status, "value") else str(out.status)
            warnings = out.warnings or []
            missing_inputs = out.missing_inputs or []

            ev_formatted = f"${out.enterprise_value:,.2f}" if out.enterprise_value is not None else "N/A"
            eq_formatted = f"${out.equity_value:,.2f}" if out.equity_value is not None else "N/A"
            share_formatted = f"${out.implied_share_price:,.2f}" if out.implied_share_price is not None else "N/A"

            key_metrics = [
                {"label": "Enterprise Value (EV)", "value": ev_formatted, "subtext": f"WACC: {discount_rate*100:.2f}% | g: {terminal_growth*100:.2f}%"},
                {"label": "Implied Equity Value", "value": eq_formatted, "subtext": f"Net Debt: ${(debt - cash):,.2f}"},
                {"label": "Implied Share Price", "value": share_formatted, "subtext": f"Shares: {shares:,.0f}" if shares else "Per Share N/A"},
            ]

            output_rows = [
                {"name": "PV of Explicit Forecast Cash Flows", "unit": "USD", "value": f"${out.present_value_explicit_forecast:,.2f}" if out.present_value_explicit_forecast is not None else "N/A", "notes": f"Sum of {len(fcfs)} discounted periods"},
                {"name": "Undiscounted Terminal Value", "unit": "USD", "value": f"${out.undiscounted_terminal_value:,.2f}" if out.undiscounted_terminal_value is not None else "N/A", "notes": "Perpetual Gordon formula at Year T"},
                {"name": "PV of Terminal Value", "unit": "USD", "value": f"${out.present_value_terminal_value:,.2f}" if out.present_value_terminal_value is not None else "N/A", "notes": "Discounted back to Year 0"},
                {"name": "Terminal Value Share of EV", "unit": "%", "value": f"{out.terminal_value_share_of_ev*100:.1f}%" if out.terminal_value_share_of_ev is not None else "N/A", "notes": "Proportion of Enterprise Value in perpetuity"},
            ]
            formula_expression = f"EV = PV(Explicit FCFs, r={discount_rate}) + PV(Terminal Value, r={discount_rate}, g={terminal_growth}) [{convention.value}]"

        elif model_key == "wacc":
            cost_of_equity_raw = form_data.get("cost_of_equity")
            cost_of_equity = float(cost_of_equity_raw) if cost_of_equity_raw and str(cost_of_equity_raw).strip() else None
            rf_raw = form_data.get("risk_free_rate")
            rf = float(rf_raw) if rf_raw and str(rf_raw).strip() else None
            beta_raw = form_data.get("beta")
            beta = float(beta_raw) if beta_raw and str(beta_raw).strip() else None
            mrp_raw = form_data.get("market_risk_premium")
            mrp = float(mrp_raw) if mrp_raw and str(mrp_raw).strip() else None
            cost_debt = float(form_data["pre_tax_cost_of_debt"])
            tax_rate = float(form_data["tax_rate"])
            eq_weight_raw = form_data.get("equity_weight")
            eq_weight = float(eq_weight_raw) if eq_weight_raw and str(eq_weight_raw).strip() else None
            debt_weight_raw = form_data.get("debt_weight")
            debt_weight = float(debt_weight_raw) if debt_weight_raw and str(debt_weight_raw).strip() else None

            wacc_in = WACCInput(
                cost_of_equity=cost_of_equity,
                risk_free_rate=rf,
                beta=beta,
                market_risk_premium=mrp,
                pre_tax_cost_of_debt=cost_debt,
                tax_rate=tax_rate,
                equity_weight=eq_weight,
                debt_weight=debt_weight,
            )
            out = calculate_wacc(wacc_in)
            status = out.status.value if hasattr(out.status, "value") else str(out.status)
            warnings = out.warnings or []
            missing_inputs = out.missing_inputs or []

            wacc_formatted = f"{out.wacc*100:.2f}%" if out.wacc is not None else "N/A"
            ke_formatted = f"{out.cost_of_equity*100:.2f}%" if out.cost_of_equity is not None else "N/A"
            kd_formatted = f"{out.after_tax_cost_of_debt*100:.2f}%" if out.after_tax_cost_of_debt is not None else "N/A"

            key_metrics = [
                {"label": "Weighted Average Cost of Capital (WACC)", "value": wacc_formatted, "subtext": f"Tax Rate: {tax_rate*100:.1f}%"},
                {"label": "Cost of Equity (Ke)", "value": ke_formatted, "subtext": f"Beta: {beta or 'N/A'}"},
                {"label": "After-Tax Cost of Debt (Kd)", "value": kd_formatted, "subtext": f"Pre-tax: {cost_debt*100:.2f}%"},
            ]
            output_rows = [
                {"name": "Equity Weight (We)", "unit": "%", "value": f"{out.equity_weight*100:.1f}%" if out.equity_weight is not None else "N/A", "notes": "Equity share of capital structure"},
                {"name": "Debt Weight (Wd)", "unit": "%", "value": f"{out.debt_weight*100:.1f}%" if out.debt_weight is not None else "N/A", "notes": "Debt share of capital structure"},
            ]
            formula_expression = out.formula_expression or "WACC = (E/V * Ke) + (D/V * Kd * (1 - TaxRate))"

        elif model_key == "gordon":
            fcf = float(form_data["final_year_fcf"])
            growth = float(form_data["growth_rate"])
            rate = float(form_data["discount_rate"])

            gordon_in = GordonGrowthInput(
                final_year_fcf=fcf,
                growth_rate=growth,
                discount_rate=rate,
            )
            out = calculate_gordon_terminal_value(gordon_in)
            status = out.status.value if hasattr(out.status, "value") else str(out.status)
            warnings = out.warnings or []

            tv_formatted = f"${out.terminal_value:,.2f}" if out.terminal_value is not None else "N/A"
            key_metrics = [
                {"label": "Terminal Value", "value": tv_formatted, "subtext": f"FCF: ${fcf:,.0f}"},
                {"label": "Discount Rate (WACC)", "value": f"{rate*100:.2f}%", "subtext": "Hurdle rate"},
                {"label": "Perpetual Growth Rate", "value": f"{growth*100:.2f}%", "subtext": "Terminal g"},
            ]
            output_rows = [
                {"name": "Terminal Value", "unit": "USD", "value": tv_formatted, "notes": "Undiscounted perpetuity value at end of forecast"},
            ]
            formula_expression = out.formula_expression or "TV = (FCF_T * (1 + g)) / (r - g)"

        elif model_key == "nav":
            assets = float(form_data["book_value_assets"])
            liabilities = float(form_data["book_value_liabilities"])
            asset_adj = float(form_data.get("asset_adjustment_total") or 0.0)
            liab_adj = float(form_data.get("liability_adjustment_total") or 0.0)

            nav_in = NAVInput(
                book_value_assets=assets,
                asset_adjustments={"Total Net Adjustment": asset_adj} if asset_adj else {},
                book_value_liabilities=liabilities,
                liability_adjustments={"Total Net Adjustment": liab_adj} if liab_adj else {},
            )
            out = calculate_nav(nav_in)
            key_metrics = [
                {"label": "Net Asset Value (NAV)", "value": f"${out.net_asset_value:,.2f}", "subtext": "Adjusted Equity Value"},
                {"label": "Adjusted Assets", "value": f"${out.adjusted_asset_value:,.2f}", "subtext": f"Book: ${assets:,.0f}"},
                {"label": "Adjusted Liabilities", "value": f"${out.adjusted_liability_value:,.2f}", "subtext": f"Book: ${liabilities:,.0f}"},
            ]
            output_rows = [
                {"name": "Total Asset Adjustments", "unit": "USD", "value": f"${out.total_asset_adjustment:,.2f}", "notes": "Fair value mark-ups/downs"},
                {"name": "Total Liability Adjustments", "unit": "USD", "value": f"${out.total_liability_adjustment:,.2f}", "notes": "Off-balance sheet or fair value obligations"},
            ]
            formula_expression = "NAV = (Book Assets + Asset Adjustments) - (Book Liabilities + Liability Adjustments)"

        elif model_key == "comps":
            metric_val = float(form_data["subject_metric_value"])
            metric_name = str(form_data["metric_name"]).strip()
            multiples_raw = str(form_data["peer_multiples"]).strip()
            multiples = [float(x.strip()) for x in multiples_raw.split(",") if x.strip()]
            mult_type = str(form_data["multiple_type"]).strip()
            debt = float(form_data.get("total_debt") or 0.0)
            cash = float(form_data.get("cash_and_equivalents") or 0.0)

            comps_in = ComparableValuationInput(
                subject_metric_value=metric_val,
                metric_name=metric_name,
                peer_multiples=multiples,
                multiple_type=mult_type,
                is_ev_multiple=True,
                total_debt=debt,
                cash_and_equivalents=cash,
            )
            out = calculate_comparable_valuation(comps_in)
            warnings = out.warnings or []

            key_metrics = [
                {"label": "Implied Equity Value (Median)", "value": f"${out.implied_equity_value_median:,.2f}", "subtext": f"Median Multiple: {out.median_multiple:.2f}x"},
                {"label": "Implied Enterprise Value (Median)", "value": f"${out.implied_ev_median:,.2f}" if out.implied_ev_median else "N/A", "subtext": f"Metric: ${metric_val:,.0f}"},
                {"label": "Peer Count", "value": f"{out.peer_count}", "subtext": f"Min: {out.min_multiple:.1f}x | Max: {out.max_multiple:.1f}x"},
            ]
            output_rows = [
                {"name": "Equity Value Range (Min - Max)", "unit": "USD", "value": f"${out.implied_equity_value_range[0]:,.2f} – ${out.implied_equity_value_range[1]:,.2f}", "notes": "Range based on observed peer multiples"},
                {"name": "Mean Multiple", "unit": "x", "value": f"{out.mean_multiple:.2f}x", "notes": "Average multiple across sample"},
            ]
            formula_expression = f"Implied EV = Subject {metric_name} * Peer Multiple; Implied Equity = Implied EV - Net Debt"

        elif model_key == "lbo":
            pe_val = float(form_data["purchase_enterprise_value"])
            exit_mult = float(form_data["exit_multiple"])
            debt_amt = float(form_data.get("senior_debt_amount") or (pe_val * 0.5))
            debt_rate = float(form_data.get("senior_debt_rate") or 0.06)
            ebitda_raw = str(form_data["ebitda_projections"]).strip()
            ebitda_list = [float(x.strip()) for x in ebitda_raw.split(",") if x.strip()]
            sweep = float(form_data.get("cash_sweep_percentage") or 1.0)

            op_years = [
                LBOOperatingYear(year=i+1, ebitda=eb, capex=eb*0.1, nwc_change=0.0, tax_rate=0.25)
                for i, eb in enumerate(ebitda_list)
            ]
            tranches = [
                DebtTranche(name="Senior Debt", initial_principal=debt_amt, interest_rate=debt_rate, mandatory_amortization_rate=0.05)
            ]

            lbo_in = LBOInput(
                purchase_enterprise_value=pe_val,
                debt_tranches=tranches,
                operating_projections=op_years,
                exit_multiple=exit_mult,
                cash_sweep_percentage=sweep,
            )
            out = calculate_lbo(lbo_in)
            status = out.status.value if hasattr(out.status, "value") else str(out.status)
            warnings = out.warnings or []
            missing_inputs = out.missing_inputs or []

            key_metrics = [
                {"label": "Sponsor MOIC", "value": f"{out.sponsor_moic:.2f}x", "subtext": f"Entry Equity: ${out.sponsor_entry_equity:,.0f}"},
                {"label": "Sponsor IRR", "value": f"{out.sponsor_irr*100:.2f}%", "subtext": f"{out.holding_period_years} Year Hold"},
                {"label": "Exit Equity Proceeds", "value": f"${out.exit_equity_proceeds:,.2f}", "subtext": f"Exit EV: ${out.exit_enterprise_value:,.0f}"},
            ]
            output_rows = [
                {"name": "Initial Total Debt", "unit": "USD", "value": f"${out.initial_total_debt:,.2f}", "notes": "Opening leverage"},
                {"name": "Ending Total Debt at Exit", "unit": "USD", "value": f"${out.ending_total_debt:,.2f}", "notes": "Debt remaining after amortization and cash sweeps"},
                {"name": "Exit EBITDA", "unit": "USD", "value": f"${out.exit_ebitda:,.2f}", "notes": f"Valued at {exit_mult:.1f}x exit multiple"},
            ]
            formula_expression = "Sponsor MOIC = Exit Equity Proceeds / Entry Equity; IRR = (MOIC)^(1/T) - 1"

        elif model_key == "vc":
            inv_amt = float(form_data["investment_amount"])
            pre_money = float(form_data["pre_money_valuation"])
            exit_val = float(form_data["exit_valuation"])
            hold_years = float(form_data["holding_period_years"])

            scenarios = [
                VCExitScenario(scenario_name="Target Exit", exit_valuation=exit_val, holding_period_years=hold_years)
            ]
            vc_in = VentureCapitalInput(
                investment_amount=inv_amt,
                pre_money_valuation=pre_money,
                exit_scenarios=scenarios,
            )
            out = calculate_venture_capital(vc_in)
            status = out.status.value if hasattr(out.status, "value") else str(out.status)
            warnings = out.warnings or []

            res = out.scenario_results[0] if out.scenario_results else None
            moic_str = f"{res.moic:.2f}x" if res else "N/A"
            irr_str = f"{res.irr*100:.2f}%" if res else "N/A"
            proceeds_str = f"${res.investor_proceeds:,.2f}" if res else "N/A"

            key_metrics = [
                {"label": "Target MOIC", "value": moic_str, "subtext": f"Investment: ${inv_amt:,.0f}"},
                {"label": "Target Net IRR", "value": irr_str, "subtext": f"{hold_years} Year Horizon"},
                {"label": "Exit Proceeds", "value": proceeds_str, "subtext": f"Exit Val: ${exit_val:,.0f}"},
            ]
            output_rows = [
                {"name": "Post-Money Valuation", "unit": "USD", "value": f"${out.post_money_valuation:,.2f}", "notes": "Pre-money + New Capital"},
                {"name": "Initial Ownership", "unit": "%", "value": f"{out.initial_ownership_percentage*100:.2f}%", "notes": "Ownership at round closing"},
                {"name": "Exit Ownership", "unit": "%", "value": f"{out.exit_ownership_percentage*100:.2f}%", "notes": "Net retention after subsequent dilution"},
            ]
            formula_expression = "Post-Money = Pre-Money + Investment; Ownership = Investment / Post-Money"

        elif model_key == "accretion_dilution":
            buyer_price = float(form_data["buyer_share_price"])
            buyer_shares = float(form_data["buyer_shares_outstanding"])
            buyer_ni = float(form_data["buyer_net_income"])
            target_ni = float(form_data["target_net_income"])
            target_val = float(form_data["target_purchase_equity_value"])
            stock_pct = float(form_data.get("stock_percentage") or 0.70)
            cash_pct = round(1.0 - stock_pct, 4)

            acc_in = AccretionDilutionInput(
                buyer_share_price=buyer_price,
                buyer_shares_outstanding=buyer_shares,
                buyer_net_income=buyer_ni,
                target_net_income=target_ni,
                target_purchase_equity_value=target_val,
                consideration_mix=ConsiderationMix(cash_percentage=cash_pct, debt_percentage=0.0, stock_percentage=stock_pct),
            )
            out = calculate_accretion_dilution(acc_in)
            status = out.status.value if hasattr(out.status, "value") else str(out.status)
            warnings = out.warnings or []

            status_color = "var(--color-success)" if out.is_accretive else "var(--color-danger)"
            key_metrics = [
                {"label": "Accretion / Dilution ($)", "value": f"${out.eps_accretion_dilution_dollars:+.4f}", "subtext": "Accretive" if out.is_accretive else "Dilutive", "color": status_color},
                {"label": "Accretion / Dilution (%)", "value": f"{out.eps_accretion_dilution_percentage:+.2f}%", "subtext": f"Standalone EPS: ${out.buyer_standalone_eps:.2f}", "color": status_color},
                {"label": "Pro Forma EPS", "value": f"${out.pro_forma_eps:.4f}", "subtext": f"Pro Forma NI: ${out.pro_forma_net_income:,.0f}"},
            ]
            output_rows = [
                {"name": "Purchase Equity Value", "unit": "USD", "value": f"${out.purchase_equity_value:,.2f}", "notes": "Target acquisition consideration"},
                {"name": "New Shares Issued", "unit": "Shares", "value": f"{out.new_shares_issued:,.0f}", "notes": f"Funded by {stock_pct*100:.1f}% stock"},
                {"name": "Pro Forma Shares Outstanding", "unit": "Shares", "value": f"{out.pro_forma_shares:,.0f}", "notes": "Combined share count"},
                {"name": "Breakeven Pre-Tax Synergies", "unit": "USD", "value": f"${out.breakeven_pre_tax_synergies:,.2f}", "notes": "Synergies required for 0% dilution"},
            ]
            formula_expression = "Pro Forma EPS = Pro Forma Net Income / Pro Forma Shares; Accretion% = (PF EPS - Standalone EPS) / Standalone EPS"

        return templates.TemplateResponse(
        request,
        "valuation/result.html",
            {
                "request": request,
                "model_key": model_key,
                "model_name": meta["name"],
                "status": status,
                "key_metrics": key_metrics,
                "output_rows": output_rows,
                "formula_expression": formula_expression,
                "warnings": warnings,
                "missing_inputs": missing_inputs,
                "inputs_applied": {k: v for k, v in form_data.items() if k not in ("entity_id",)},
            },
        )

    except Exception as exc:
        entities = storage.list_entities(limit=100)
        return templates.TemplateResponse(
        request,
        "valuation/form.html",
            {
                "request": request,
                "model_key": model_key,
                "model_name": meta["name"],
                "model_description": meta["description"],
                "entities": entities,
                "selected_entity_id": entity_id,
                "form_data": form_data,
                "error": str(exc),
            },
            status_code=400,
        )
