"""Contracts, validation models, and result containers for deterministic valuation primitives."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field, field_validator, model_validator


class ValuationCalculationStatus(str, Enum):
    """Execution status of a valuation primitive."""
    COMPLETED = "COMPLETED"
    INCOMPLETE_MISSING_INPUTS = "INCOMPLETE_MISSING_INPUTS"
    MATHEMATICALLY_INVALID = "MATHEMATICALLY_INVALID"


class DiscountingConvention(str, Enum):
    """Cash flow discounting timing convention."""
    YEAR_END = "YEAR_END"      # (1 + r)^t
    MID_YEAR = "MID_YEAR"      # (1 + r)^(t - 0.5)


# ── WACC Models ─────────────────────────────────────────────────────────────

class WACCInput(BaseModel):
    """Input parameters for Weighted Average Cost of Capital calculation."""
    cost_of_equity: Optional[float] = Field(default=None, description="Direct cost of equity as decimal, e.g. 0.08")
    risk_free_rate: Optional[float] = Field(default=None, description="Risk-free rate as decimal, e.g. 0.035")
    beta: Optional[float] = Field(default=None, ge=0.0, description="Equity beta (levered or unlevered)")
    market_risk_premium: Optional[float] = Field(default=None, description="Equity market risk premium, e.g. 0.055")
    pre_tax_cost_of_debt: float = Field(..., ge=0.0, description="Cost of debt as decimal, e.g. 0.05")
    tax_rate: float = Field(..., ge=0.0, le=1.0, description="Effective corporate tax rate as decimal, e.g. 0.25")
    equity_weight: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Proportion of equity in capital structure")
    debt_weight: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Proportion of debt in capital structure")
    equity_market_value: Optional[float] = Field(default=None, ge=0.0, description="Total market value of equity")
    total_debt_value: Optional[float] = Field(default=None, ge=0.0, description="Total book or market value of debt")


class WACCOutput(BaseModel):
    """Result of Weighted Average Cost of Capital calculation."""
    status: ValuationCalculationStatus
    wacc: Optional[float] = None
    cost_of_equity: Optional[float] = None
    after_tax_cost_of_debt: Optional[float] = None
    equity_weight: Optional[float] = None
    debt_weight: Optional[float] = None
    formula_expression: str = ""
    missing_inputs: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


# ── Gordon Growth Terminal Value Models ──────────────────────────────────────

class GordonGrowthInput(BaseModel):
    """Inputs for Gordon Growth Perpetual Terminal Value."""
    final_year_fcf: float = Field(..., description="Free Cash Flow in final explicit forecast year")
    growth_rate: float = Field(..., description="Perpetual terminal growth rate as decimal, e.g. 0.025")
    discount_rate: float = Field(..., description="Discount rate (WACC) as decimal, e.g. 0.08")


class GordonGrowthOutput(BaseModel):
    """Result of Gordon Growth Terminal Value calculation."""
    status: ValuationCalculationStatus
    terminal_value: Optional[float] = None
    discount_rate: float
    growth_rate: float
    formula_expression: str = ""
    error_message: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)


# ── DCF Models ──────────────────────────────────────────────────────────────

class DCFInput(BaseModel):
    """Input dataset for Discounted Cash Flow valuation."""
    forecast_cash_flows: List[float] = Field(..., min_length=1, description="Sequential explicit forecast cash flows")
    discount_rate: float = Field(..., gt=0.0, description="Discount rate (WACC) as decimal, e.g. 0.08")
    terminal_growth_rate: float = Field(..., description="Terminal perpetual growth rate as decimal, e.g. 0.025")
    discounting_convention: DiscountingConvention = Field(
        default=DiscountingConvention.MID_YEAR,
        description="Mid-year (standard PEAK) or year-end timing",
    )
    cash_and_equivalents: float = Field(default=0.0, ge=0.0, description="Cash & marketable securities")
    total_debt: float = Field(default=0.0, ge=0.0, description="Total interest-bearing financial debt")
    minority_interest: float = Field(default=0.0, ge=0.0, description="Minority / non-controlling interest")
    preferred_equity: float = Field(default=0.0, ge=0.0, description="Preferred stock value")
    non_operating_assets: float = Field(default=0.0, ge=0.0, description="Non-operating asset fair value")
    shares_outstanding: Optional[float] = Field(default=None, gt=0.0, description="Diluted shares count")


class DCFOutput(BaseModel):
    """Comprehensive output of Discounted Cash Flow valuation."""
    status: ValuationCalculationStatus
    enterprise_value: Optional[float] = None
    equity_value: Optional[float] = None
    implied_share_price: Optional[float] = None
    present_value_explicit_forecast: Optional[float] = None
    undiscounted_terminal_value: Optional[float] = None
    present_value_terminal_value: Optional[float] = None
    terminal_value_share_of_ev: Optional[float] = None
    discount_factors: List[float] = Field(default_factory=list)
    discounted_cash_flows: List[float] = Field(default_factory=list)
    discount_rate_wacc: float
    terminal_growth_rate: float
    discounting_convention: str
    missing_inputs: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


# ── Enterprise Value to Equity Value Bridge ─────────────────────────────────

class EVBridgeInput(BaseModel):
    """Parameters to bridge Enterprise Value to Equity Value."""
    enterprise_value: float = Field(..., description="Enterprise Value")
    total_debt: float = Field(default=0.0, ge=0.0)
    cash_and_equivalents: float = Field(default=0.0, ge=0.0)
    minority_interest: float = Field(default=0.0, ge=0.0)
    preferred_equity: float = Field(default=0.0, ge=0.0)
    non_operating_assets: float = Field(default=0.0, ge=0.0)


class EVBridgeOutput(BaseModel):
    """Result of Enterprise Value to Equity Value reconciliation."""
    status: ValuationCalculationStatus = ValuationCalculationStatus.COMPLETED
    enterprise_value: float
    net_debt: float
    equity_value: float
    bridge_components: Dict[str, float] = Field(default_factory=dict)


# ── Net Asset Value (NAV) Models ────────────────────────────────────────────

class NAVInput(BaseModel):
    """Inputs for adjusted Net Asset Value calculation."""
    book_value_assets: float = Field(..., ge=0.0, description="Total book value of balance sheet assets")
    asset_adjustments: Dict[str, float] = Field(
        default_factory=dict,
        description="Mark-to-market adjustments by asset category (e.g. PP&E, Intangibles)",
    )
    book_value_liabilities: float = Field(..., ge=0.0, description="Total book value of balance sheet liabilities")
    liability_adjustments: Dict[str, float] = Field(
        default_factory=dict,
        description="Fair value adjustments to liabilities (e.g. unrecorded provisions)",
    )


class NAVOutput(BaseModel):
    """Result of Net Asset Value calculation."""
    status: ValuationCalculationStatus = ValuationCalculationStatus.COMPLETED
    adjusted_asset_value: float
    adjusted_liability_value: float
    net_asset_value: float
    total_asset_adjustment: float
    total_liability_adjustment: float


# ── Comparable Multiples Models ─────────────────────────────────────────────

class ComparableValuationInput(BaseModel):
    """Parameters for peer multiple implied valuation."""
    subject_metric_value: float = Field(..., description="Financial metric value of target (e.g. EBITDA)")
    metric_name: str = Field(..., description="Metric identifier, e.g. 'EBITDA', 'REVENUE', 'NET_INCOME'")
    peer_multiples: List[float] = Field(..., min_length=1, description="Observed peer multiple values")
    multiple_type: str = Field(..., description="Multiple identifier, e.g. 'EV/EBITDA', 'EV/Revenue', 'P/E'")
    is_ev_multiple: bool = Field(default=True, description="True if multiple derives EV, False if Equity Value")
    total_debt: float = Field(default=0.0, ge=0.0)
    cash_and_equivalents: float = Field(default=0.0, ge=0.0)


class ComparableValuationOutput(BaseModel):
    """Result of peer comparable valuation."""
    status: ValuationCalculationStatus = ValuationCalculationStatus.COMPLETED
    multiple_type: str
    peer_count: int
    min_multiple: float
    max_multiple: float
    median_multiple: float
    mean_multiple: float
    implied_ev_median: Optional[float] = None
    implied_equity_value_median: float
    implied_equity_value_range: Tuple[float, float]
    warnings: List[str] = Field(default_factory=list)


# ── Sensitivity Analysis Models ─────────────────────────────────────────────

class SensitivityMatrixInput(BaseModel):
    """Parameters to evaluate a 2D DCF sensitivity matrix."""
    base_dcf_input: DCFInput
    discount_rates: List[float] = Field(..., min_length=2, description="List of WACC rates to test, e.g. [0.07, 0.08, 0.09]")
    terminal_growth_rates: List[float] = Field(..., min_length=2, description="List of growth rates, e.g. [0.02, 0.025, 0.03]")


class SensitivityMatrixOutput(BaseModel):
    """2D sensitivity matrix of Equity Values across discount and growth rates."""
    status: ValuationCalculationStatus = ValuationCalculationStatus.COMPLETED
    discount_rates: List[float]
    terminal_growth_rates: List[float]
    # Matrix rows correspond to discount_rates, columns to terminal_growth_rates
    equity_value_matrix: List[List[Optional[float]]]
    warnings: List[str] = Field(default_factory=list)


# ── Leveraged Buyout (LBO) Models ───────────────────────────────────────────

class DebtTranche(BaseModel):
    """Specification of a debt instrument in an LBO financing structure."""
    name: str = Field(description="Tranche name (e.g. 'Term Loan A', 'Subordinated Notes')")
    initial_principal: float = Field(..., ge=0.0, description="Initial principal debt amount borrowed")
    interest_rate: float = Field(..., ge=0.0, description="Annual interest coupon rate as decimal, e.g. 0.065")
    mandatory_amortization_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Annual mandatory principal repayment as percentage of initial balance",
    )
    seniority_order: int = Field(default=1, ge=1, description="Priority order for repayment (1 = highest seniority)")


class LBOSourcesAndUses(BaseModel):
    """Reconciled Sources and Uses of Funds at LBO transaction close."""
    debt_sources: Dict[str, float] = Field(default_factory=dict, description="Debt borrowings by tranche")
    sponsor_equity: float = Field(..., ge=0.0, description="Sponsor cash equity contributed")
    management_rollover: float = Field(default=0.0, ge=0.0, description="Management rollover equity")
    other_sources: float = Field(default=0.0, ge=0.0, description="Other funding sources")
    total_sources: float = Field(..., ge=0.0, description="Total capital raised")

    purchase_equity: float = Field(..., ge=0.0, description="Target equity purchase consideration")
    refinanced_debt: float = Field(default=0.0, ge=0.0, description="Target existing debt refinanced")
    transaction_fees: float = Field(default=0.0, ge=0.0, description="M&A advisory, legal, and diligence fees")
    financing_fees: float = Field(default=0.0, ge=0.0, description="Debt underwriting and arrangement fees")
    min_cash_balance: float = Field(default=0.0, ge=0.0, description="Minimum opening balance sheet cash")
    total_uses: float = Field(..., ge=0.0, description="Total capital deployed")
    imbalance: float = Field(default=0.0, description="Difference between Sources and Uses (must be 0.0)")


class LBOOperatingYear(BaseModel):
    """Annual operating and cash flow inputs for an LBO holding period year."""
    year: int = Field(..., ge=1, description="Projection year (1, 2, ...)")
    revenue: float = Field(default=0.0, ge=0.0, description="Projected annual revenue")
    ebitda: float = Field(..., description="Projected EBITDA")
    capex: float = Field(default=0.0, ge=0.0, description="Capital expenditures")
    nwc_change: float = Field(default=0.0, description="Change in non-cash net working capital (outflow if positive)")
    tax_rate: float = Field(default=0.25, ge=0.0, le=1.0, description="Effective income tax rate")


class DebtScheduleYear(BaseModel):
    """Tranche-level debt balance and interest tracking for a specific projection year."""
    year: int
    tranche_name: str
    beginning_balance: float
    interest_expense: float
    mandatory_repayment: float
    sweep_repayment: float
    ending_balance: float


class LBOInput(BaseModel):
    """Full inputs for deterministic Leveraged Buyout analysis."""
    purchase_enterprise_value: float = Field(..., gt=0.0, description="Entry purchase enterprise value")
    sponsor_equity_contribution: Optional[float] = Field(default=None, ge=0.0, description="Sponsor equity")
    debt_tranches: List[DebtTranche] = Field(default_factory=list, description="Financing debt tranches")
    refinanced_debt: float = Field(default=0.0, ge=0.0, description="Existing target debt paid off")
    transaction_fees: float = Field(default=0.0, ge=0.0, description="Advisory fees")
    financing_fees: float = Field(default=0.0, ge=0.0, description="Financing fees")
    min_cash_balance: float = Field(default=0.0, ge=0.0, description="Opening minimum cash")
    operating_projections: List[LBOOperatingYear] = Field(..., min_length=1, description="Explicit annual forecasts")
    exit_multiple: float = Field(..., gt=0.0, description="Exit EV / EBITDA multiple")
    cash_sweep_percentage: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Percentage of excess free cash flow used to prepay senior debt",
    )


class LBOOutput(BaseModel):
    """Institutional LBO returns and debt schedule output."""
    status: ValuationCalculationStatus
    sources_and_uses: Optional[LBOSourcesAndUses] = None
    debt_schedule: List[DebtScheduleYear] = Field(default_factory=list)
    initial_total_debt: float = 0.0
    ending_total_debt: float = 0.0
    ending_cash_balance: float = 0.0
    net_debt_at_exit: float = 0.0
    exit_ebitda: float = 0.0
    exit_multiple: float = 0.0
    exit_enterprise_value: float = 0.0
    exit_equity_proceeds: float = 0.0
    sponsor_entry_equity: float = 0.0
    sponsor_moic: float = 0.0
    sponsor_irr: float = 0.0
    holding_period_years: int = 0
    missing_inputs: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


# ── Monte Carlo Valuation Models ───────────────────────────────────────────

class DistributionType(str, Enum):
    """Supported probability distribution families for Monte Carlo simulation."""
    NORMAL = "NORMAL"
    TRIANGULAR = "TRIANGULAR"
    UNIFORM = "UNIFORM"
    LOGNORMAL = "LOGNORMAL"


class ParameterDistribution(BaseModel):
    """Specification of a random variable distribution for Monte Carlo sampling."""
    parameter_name: str = Field(description="Name of parameter: 'discount_rate', 'terminal_growth_rate', 'fcf_multiplier'")
    distribution_type: DistributionType
    mean: Optional[float] = None
    std_dev: Optional[float] = None
    min_val: Optional[float] = None
    mode_val: Optional[float] = None
    max_val: Optional[float] = None

    @model_validator(mode="after")
    def validate_distribution_bounds(self) -> ParameterDistribution:
        if self.std_dev is not None and self.std_dev < 0:
            raise ValueError(f"std_dev for parameter '{self.parameter_name}' cannot be negative (got {self.std_dev})")
        if self.min_val is not None and self.max_val is not None and self.min_val > self.max_val:
            raise ValueError(
                f"min_val ({self.min_val}) cannot exceed max_val ({self.max_val}) for parameter '{self.parameter_name}'"
            )
        if self.distribution_type == DistributionType.TRIANGULAR:
            if self.min_val is not None and self.mode_val is not None and self.mode_val < self.min_val:
                raise ValueError(
                    f"mode_val ({self.mode_val}) cannot be less than min_val ({self.min_val}) for parameter '{self.parameter_name}'"
                )
            if self.max_val is not None and self.mode_val is not None and self.mode_val > self.max_val:
                raise ValueError(
                    f"mode_val ({self.mode_val}) cannot be greater than max_val ({self.max_val}) for parameter '{self.parameter_name}'"
                )
        return self


class MonteCarloDCFInput(BaseModel):
    """Inputs to execute reproducible Monte Carlo simulation over a DCF valuation."""
    base_dcf_input: DCFInput
    parameter_distributions: List[ParameterDistribution] = Field(..., min_length=1)
    iteration_count: int = Field(default=1000, ge=100, le=50000, description="Number of simulation trials")
    random_seed: int = Field(default=42, description="Reproducible pseudo-random seed")
    target_equity_threshold: Optional[float] = Field(
        default=None,
        description="Optional hurdle threshold to calculate probability of achieving",
    )


class MonteCarloDCFOutput(BaseModel):
    """Statistical summary of Monte Carlo DCF simulation trials."""
    status: ValuationCalculationStatus
    iteration_count: int
    successful_iterations: int
    failed_iterations: int
    mean_equity_value: float
    median_equity_value: float
    std_dev_equity_value: float
    min_equity_value: float
    max_equity_value: float
    percentiles: Dict[str, float] = Field(default_factory=dict)
    probability_below_threshold: Optional[float] = Field(
        default=None,
        description="Empirical probability of equity value falling below target threshold among successful trials (denominator: successful_iterations)",
    )
    random_seed: int
    warnings: List[str] = Field(default_factory=list)


# ── Venture Capital Valuation Models ────────────────────────────────────────

class DilutionRound(BaseModel):
    """Subsequent financing round or option pool expansion causing dilution prior to exit."""
    round_name: str = Field(description="Round identifier, e.g. 'Series B Expansion', 'Unallocated Option Pool'")
    dilution_percentage: float = Field(..., ge=0.0, le=1.0, description="Percentage ownership granted to new capital")


class VCExitScenario(BaseModel):
    """Target liquidity exit scenario with holding period and enterprise valuation."""
    scenario_name: str = Field(description="Scenario label: 'Base', 'Bull', 'Downside'")
    exit_valuation: float = Field(..., ge=0.0, description="Enterprise or equity valuation at liquidity event")
    holding_period_years: float = Field(..., gt=0.0, description="Years from initial investment to liquidity")
    liquidation_preference_multiple: float = Field(default=1.0, ge=0.0, description="Liquidation preference multiplier (e.g. 1.0x, 2.0x)")
    is_participating: bool = Field(default=False, description="Whether preferred shares participate in remaining proceeds")


class VCScenarioResult(BaseModel):
    """Realized return metrics for a specific VC exit scenario."""
    scenario_name: str
    exit_valuation: float
    investor_proceeds: float
    moic: float
    irr: float
    holding_period_years: float


class VentureCapitalInput(BaseModel):
    """Inputs for Venture Capital stage-gate valuation."""
    investment_amount: float = Field(..., gt=0.0, description="Cash capital invested by the venture firm")
    pre_money_valuation: Optional[float] = Field(default=None, ge=0.0, description="Valuation prior to investment")
    post_money_valuation: Optional[float] = Field(default=None, ge=0.0, description="Valuation after investment")
    dilution_rounds: List[DilutionRound] = Field(default_factory=list, description="Subsequent financing dilution")
    exit_scenarios: List[VCExitScenario] = Field(default_factory=list, description="Exit valuation scenarios")


class VentureCapitalOutput(BaseModel):
    """Venture Capital ownership, retention, and return multiples."""
    status: ValuationCalculationStatus
    pre_money_valuation: float = 0.0
    investment_amount: float = 0.0
    post_money_valuation: float = 0.0
    initial_ownership_percentage: float = 0.0
    retention_rate: float = 1.0
    exit_ownership_percentage: float = 0.0
    scenario_results: List[VCScenarioResult] = Field(default_factory=list)
    missing_inputs: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


# ── M&A Accretion/Dilution Models ───────────────────────────────────────────

class ConsiderationMix(BaseModel):
    """Proportion of consideration funded via cash, debt, and stock."""
    cash_percentage: float = Field(default=0.0, ge=0.0, le=1.0)
    debt_percentage: float = Field(default=0.0, ge=0.0, le=1.0)
    stock_percentage: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("stock_percentage")
    @classmethod
    def validate_sum(cls, v: float, values: Any) -> float:
        cash = values.data.get("cash_percentage", 0.0)
        debt = values.data.get("debt_percentage", 0.0)
        total = round(cash + debt + v, 4)
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"Consideration mix must sum to 100% (cash={cash}, debt={debt}, stock={v}, sum={total})")
        return v


class AccretionDilutionInput(BaseModel):
    """Parameters for M&A EPS Accretion / Dilution analysis."""
    buyer_share_price: float = Field(..., gt=0.0, description="Buyer current common share trading price")
    buyer_shares_outstanding: float = Field(..., gt=0.0, description="Buyer diluted shares outstanding")
    buyer_net_income: float = Field(..., description="Buyer standalone net income")
    buyer_tax_rate: float = Field(default=0.25, ge=0.0, le=1.0, description="Buyer marginal tax rate")

    target_net_income: float = Field(..., description="Target standalone net income")
    target_share_price: Optional[float] = Field(default=None, gt=0.0, description="Target share price prior to offer")
    target_shares_outstanding: Optional[float] = Field(default=None, gt=0.0, description="Target shares outstanding")
    offer_price_per_share: Optional[float] = Field(default=None, gt=0.0, description="Offer price per target share")
    target_purchase_equity_value: Optional[float] = Field(
        default=None,
        gt=0.0,
        description="Direct target purchase equity consideration (if target is private firm)",
    )

    consideration_mix: ConsiderationMix = Field(
        default_factory=ConsiderationMix,
        description="Financing mix of purchase consideration",
    )
    new_debt_interest_rate: float = Field(default=0.06, ge=0.0, description="Annual coupon on new acquisition debt")
    foregone_cash_interest_rate: float = Field(default=0.03, ge=0.0, description="Pre-tax foregone return on cash used")
    pre_tax_synergies: float = Field(default=0.0, description="Annual pre-tax operational synergies")
    after_tax_intangible_amortization: float = Field(
        default=0.0,
        ge=0.0,
        description="Annual after-tax amortization of write-ups",
    )


class AccretionDilutionOutput(BaseModel):
    """Institutional M&A EPS Accretion / Dilution results."""
    status: ValuationCalculationStatus
    purchase_equity_value: float = 0.0
    implied_offer_premium: Optional[float] = None
    cash_funded: float = 0.0
    debt_funded: float = 0.0
    stock_funded: float = 0.0
    new_shares_issued: float = 0.0
    pro_forma_shares: float = 0.0
    buyer_standalone_eps: float = 0.0
    pro_forma_net_income: float = 0.0
    pro_forma_eps: float = 0.0
    eps_accretion_dilution_dollars: float = 0.0
    eps_accretion_dilution_percentage: float = 0.0
    is_accretive: bool = False
    breakeven_pre_tax_synergies: float = 0.0
    missing_inputs: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

