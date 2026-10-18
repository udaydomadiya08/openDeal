"""Unit tests for Deterministic Financial Calculation Primitives.

Validates:
- Hand-calculated benchmark cases for DCF, WACC, Gordon Growth TV, EV Bridge, NAV, Comparables, Sensitivity.
- Zero and negative cash flows where mathematically valid.
- Discount rate equal to or less than terminal growth (g >= WACC) mathematically rejected.
- Negative growth rates handled cleanly.
- Missing inputs producing structured INCOMPLETE_MISSING_INPUTS.
- Floating-point determinism and repeatable precision.
- No hallucinated or manufactured rates/multiples.
"""

import pytest

from opendeal.valuation.engine.contracts import (
    ComparableValuationInput,
    DCFInput,
    DiscountingConvention,
    EVBridgeInput,
    GordonGrowthInput,
    NAVInput,
    SensitivityMatrixInput,
    ValuationCalculationStatus,
    WACCInput,
)
from opendeal.valuation.engine.primitives import (
    calculate_comparable_valuation,
    calculate_dcf,
    calculate_gordon_terminal_value,
    calculate_nav,
    calculate_sensitivity_matrix,
    calculate_wacc,
    reconcile_enterprise_to_equity_value,
)


class TestWACCPrimitive:
    """Tests for Weighted Average Cost of Capital calculation."""

    def test_wacc_standard_capm_benchmark(self):
        """Hand-calculated benchmark:
        Rf = 4.0% (0.04), Beta = 1.2, MRP = 5.0% (0.05) -> Ke = 0.04 + 1.2 * 0.05 = 0.10 (10.0%)
        Kd_pre = 6.0% (0.06), Tax Rate = 25% (0.25) -> Kd_after = 0.06 * (1 - 0.25) = 0.045 (4.5%)
        Equity Value = 800, Debt Value = 200 -> Total V = 1000 -> We = 0.8, Wd = 0.2
        WACC = (0.8 * 0.10) + (0.2 * 0.045) = 0.08 + 0.009 = 0.089 (8.90%)
        """
        inp = WACCInput(
            risk_free_rate=0.04,
            beta=1.2,
            market_risk_premium=0.05,
            pre_tax_cost_of_debt=0.06,
            tax_rate=0.25,
            equity_market_value=800.0,
            total_debt_value=200.0,
        )
        res = calculate_wacc(inp)
        assert res.status == ValuationCalculationStatus.COMPLETED
        assert res.cost_of_equity == 0.10
        assert res.after_tax_cost_of_debt == 0.045
        assert res.equity_weight == 0.8
        assert res.debt_weight == 0.2
        assert res.wacc == 0.089

    def test_wacc_direct_cost_of_equity_and_weights(self):
        """WACC with directly supplied cost of equity and weights."""
        inp = WACCInput(
            cost_of_equity=0.12,
            pre_tax_cost_of_debt=0.08,
            tax_rate=0.20,
            equity_weight=0.70,
            debt_weight=0.30,
        )
        # Kd_after = 0.08 * 0.8 = 0.064
        # WACC = (0.7 * 0.12) + (0.3 * 0.064) = 0.084 + 0.0192 = 0.1032
        res = calculate_wacc(inp)
        assert res.status == ValuationCalculationStatus.COMPLETED
        assert res.wacc == 0.1032

    def test_wacc_missing_inputs_returns_incomplete_status(self):
        """OpenDeal policy: Never manufacture missing rates. Returns INCOMPLETE_MISSING_INPUTS."""
        inp = WACCInput(
            pre_tax_cost_of_debt=0.05,
            tax_rate=0.21,
            # beta, risk_free_rate, mrp, weights all missing
        )
        res = calculate_wacc(inp)
        assert res.status == ValuationCalculationStatus.INCOMPLETE_MISSING_INPUTS
        assert res.wacc is None
        assert "risk_free_rate" in res.missing_inputs
        assert "beta" in res.missing_inputs
        assert "market_risk_premium" in res.missing_inputs


class TestGordonGrowthTerminalValue:
    """Tests for Gordon Growth Perpetual Terminal Value."""

    def test_gordon_growth_benchmark(self):
        """Hand-calculated benchmark:
        Final year FCF = 100
        Growth rate g = 2.5% (0.025)
        Discount rate WACC = 8.5% (0.085)
        Next year FCF = 100 * (1 + 0.025) = 102.50
        Denominator = 0.085 - 0.025 = 0.060
        TV = 102.50 / 0.060 = 1708.33
        """
        inp = GordonGrowthInput(
            final_year_fcf=100.0,
            growth_rate=0.025,
            discount_rate=0.085,
        )
        res = calculate_gordon_terminal_value(inp)
        assert res.status == ValuationCalculationStatus.COMPLETED
        assert res.terminal_value == 1708.33

    def test_gordon_growth_equal_or_exceeding_discount_rate_rejected(self):
        """Terminal growth g >= WACC is mathematically invalid and must be rejected."""
        # g == WACC -> division by zero
        inp_equal = GordonGrowthInput(
            final_year_fcf=100.0,
            growth_rate=0.08,
            discount_rate=0.08,
        )
        res_equal = calculate_gordon_terminal_value(inp_equal)
        assert res_equal.status == ValuationCalculationStatus.MATHEMATICALLY_INVALID
        assert res_equal.terminal_value is None

        # g > WACC -> negative denominator
        inp_exceed = GordonGrowthInput(
            final_year_fcf=100.0,
            growth_rate=0.09,
            discount_rate=0.08,
        )
        res_exceed = calculate_gordon_terminal_value(inp_exceed)
        assert res_exceed.status == ValuationCalculationStatus.MATHEMATICALLY_INVALID
        assert res_exceed.terminal_value is None

    def test_gordon_growth_negative_perpetual_growth(self):
        """Negative perpetual growth is valid (e.g. terminal secular decline).
        FCF_n = 100, g = -1.0% (-0.01), WACC = 8.0% (0.08)
        FCF_{n+1} = 100 * 0.99 = 99.0
        Denominator = 0.08 - (-0.01) = 0.09
        TV = 99.0 / 0.09 = 1100.0
        """
        inp = GordonGrowthInput(
            final_year_fcf=100.0,
            growth_rate=-0.01,
            discount_rate=0.08,
        )
        res = calculate_gordon_terminal_value(inp)
        assert res.status == ValuationCalculationStatus.COMPLETED
        assert res.terminal_value == 1100.0


class TestDCFPrimitive:
    """Tests for complete Discounted Cash Flow valuation."""

    def test_dcf_year_end_convention_benchmark(self):
        """Hand-calculated benchmark with Year-End timing:
        Forecast FCFs: [100, 110, 121, 133.1, 146.41]
        Discount rate: 10% (0.10)
        Terminal growth: 2% (0.02)
        Total Debt: 300, Cash: 100, Shares: 50
        
        Discount Factors (1 / 1.10^t):
        Year 1: 0.909091 -> PV = 90.91
        Year 2: 0.826446 -> PV = 90.91
        Year 3: 0.751315 -> PV = 90.91
        Year 4: 0.683013 -> PV = 90.91
        Year 5: 0.620921 -> PV = 90.91
        PV Explicit = 454.55
        
        Terminal Value:
        FCF_6 = 146.41 * 1.02 = 149.3382
        TV = 149.3382 / 0.08 = 1866.73
        PV of TV = 1866.73 * (1 / 1.10^5) = 1866.73 * 0.620921 = 1159.09
        
        Enterprise Value = 454.55 + 1159.09 = 1613.64
        Equity Value = 1613.64 - 300 + 100 = 1413.64
        Implied Share Price = 1413.64 / 50 = 28.27
        """
        inp = DCFInput(
            forecast_cash_flows=[100.0, 110.0, 121.0, 133.1, 146.41],
            discount_rate=0.10,
            terminal_growth_rate=0.02,
            discounting_convention=DiscountingConvention.YEAR_END,
            total_debt=300.0,
            cash_and_equivalents=100.0,
            shares_outstanding=50.0,
        )
        res = calculate_dcf(inp)
        assert res.status == ValuationCalculationStatus.COMPLETED
        assert abs(res.present_value_explicit_forecast - 454.55) <= 0.05
        assert abs(res.undiscounted_terminal_value - 1866.73) <= 0.05
        assert abs(res.present_value_terminal_value - 1159.09) <= 0.05
        assert abs(res.enterprise_value - 1613.64) <= 0.10
        assert abs(res.equity_value - 1413.64) <= 0.10
        assert abs(res.implied_share_price - 28.27) <= 0.02

    def test_dcf_mid_year_convention(self):
        """Mid-year discounting applies (t - 0.5) exponent."""
        inp = DCFInput(
            forecast_cash_flows=[100.0, 100.0],
            discount_rate=0.10,
            terminal_growth_rate=0.02,
            discounting_convention=DiscountingConvention.MID_YEAR,
        )
        res = calculate_dcf(inp)
        assert res.status == ValuationCalculationStatus.COMPLETED
        # df_1 = 1 / (1.10^0.5) = 0.953463
        # df_2 = 1 / (1.10^1.5) = 0.866784
        assert abs(res.discount_factors[0] - 0.953463) <= 1e-5
        assert abs(res.discount_factors[1] - 0.866784) <= 1e-5

    def test_dcf_negative_and_zero_intermediate_cash_flows(self):
        """Negative and zero cash flows (turnaround or high capex) are valid and preserved."""
        inp = DCFInput(
            forecast_cash_flows=[-50.0, 0.0, 40.0, 80.0, 120.0],
            discount_rate=0.09,
            terminal_growth_rate=0.025,
            discounting_convention=DiscountingConvention.MID_YEAR,
        )
        res = calculate_dcf(inp)
        assert res.status == ValuationCalculationStatus.COMPLETED
        assert res.discounted_cash_flows[0] < 0.0  # Preserves negative PV
        assert res.discounted_cash_flows[1] == 0.0  # Preserves zero PV
        assert res.enterprise_value is not None


class TestEVBridgeAndNAV:
    """Tests for Enterprise Value bridge and Net Asset Value primitives."""

    def test_ev_bridge_complete_reconciliation(self):
        """Institutional EV-to-Equity reconciliation."""
        inp = EVBridgeInput(
            enterprise_value=1000.0,
            total_debt=250.0,
            cash_and_equivalents=50.0,
            minority_interest=20.0,
            preferred_equity=30.0,
            non_operating_assets=40.0,
        )
        # Net Debt = 250 - 50 = 200
        # Equity = 1000 - 250 + 50 - 20 - 30 + 40 = 790.0
        res = reconcile_enterprise_to_equity_value(inp)
        assert res.enterprise_value == 1000.0
        assert res.net_debt == 200.0
        assert res.equity_value == 790.0
        assert res.bridge_components["minority_interest"] == -20.0
        assert res.bridge_components["preferred_equity"] == -30.0
        assert res.bridge_components["non_operating_assets"] == 40.0

    def test_nav_with_adjustments(self):
        """NAV calculation with positive and negative fair value mark-to-market adjustments."""
        inp = NAVInput(
            book_value_assets=500.0,
            book_value_liabilities=300.0,
            asset_adjustments={"real_estate_step_up": 50.0, "inventory_write_down": -10.0},
            liability_adjustments={"contingent_litigation": 20.0},
        )
        # Adjusted assets = 500 + 40 = 540
        # Adjusted liabilities = 300 + 20 = 320
        # NAV = 540 - 320 = 220.0
        res = calculate_nav(inp)
        assert res.adjusted_asset_value == 540.0
        assert res.adjusted_liability_value == 320.0
        assert res.net_asset_value == 220.0

    def test_nav_negative_for_insolvent_firm(self):
        """Insolvent firms have negative Net Asset Value; result is preserved without truncation."""
        inp = NAVInput(
            book_value_assets=100.0,
            book_value_liabilities=250.0,
        )
        res = calculate_nav(inp)
        assert res.net_asset_value == -150.0


class TestComparableAndSensitivityPrimitives:
    """Tests for Comparable multiples and 2D Sensitivity matrix."""

    def test_comparable_valuation_ev_multiple(self):
        """Enterprise value multiple (e.g. EV/EBITDA)."""
        inp = ComparableValuationInput(
            multiple_type="EV/EBITDA",
            metric_name="EBITDA",
            peer_multiples=[8.0, 10.0, 12.0, 14.0],
            subject_metric_value=50.0,  # EBITDA = 50
            total_debt=100.0,
            cash_and_equivalents=20.0,
            is_ev_multiple=True,
        )
        # Median multiple = (10 + 12) / 2 = 11.0
        # Implied EV = 50 * 11.0 = 550.0
        # Implied Equity = 550 - 100 + 20 = 470.0
        res = calculate_comparable_valuation(inp)
        assert res.peer_count == 4
        assert res.median_multiple == 11.0
        assert res.implied_ev_median == 550.0
        assert res.implied_equity_value_median == 470.0

    def test_sensitivity_matrix_2d(self):
        """2D DCF Equity Value sensitivity table varying WACC and growth."""
        base_dcf = DCFInput(
            forecast_cash_flows=[100.0, 100.0, 100.0],
            discount_rate=0.08,
            terminal_growth_rate=0.02,
        )
        inp = SensitivityMatrixInput(
            base_dcf_input=base_dcf,
            discount_rates=[0.08, 0.09],
            terminal_growth_rates=[0.02, 0.025, 0.08],  # Note: 0.08 >= 0.08 (invalid cell!)
        )
        res = calculate_sensitivity_matrix(inp)
        assert len(res.equity_value_matrix) == 2  # 2 WACC rows
        assert len(res.equity_value_matrix[0]) == 3  # 3 g columns

        # Cell (r=0.08, g=0.08) is invalid (g >= r) -> None
        assert res.equity_value_matrix[0][2] is None
        # Cell (r=0.08, g=0.02) is valid -> float > 0
        assert res.equity_value_matrix[0][0] is not None
        assert res.equity_value_matrix[0][0] > 0
