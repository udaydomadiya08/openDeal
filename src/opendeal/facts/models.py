"""Financial fact records, transformation tracking, and validation models."""

import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, field_validator

from opendeal.core.types import (
    AccountingStandard,
    Currency,
    FactState,
    FiscalPeriodType,
    MissingDataReason,
    Sign,
    UnitScale,
    ValidationSeverity,
    ValidationStatus,
)


class TransformationStep(BaseModel):
    """An audit step describing a transformation or calculation applied to a fact."""
    step_name: str = Field(
        description="Name of operation (e.g. 'UNIT_SCALE_MULTIPLIER', 'SIGN_INVERSION', 'XBRL_NORMALIZATION')",
    )
    input_value: Any = Field(description="Value prior to transformation")
    output_value: Any = Field(description="Value resulting from transformation")
    rationale: str = Field(description="Deterministic reason or formula for the change")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of transformation",
    )


class FactRecord(BaseModel):
    """Canonical representation of a financial or operational fact.
    
    Distinguishes raw, standardized, validated, derived, and missing facts.
    Preserves competing evidence without silent destructive overwrite.
    """
    fact_id: str = Field(
        default_factory=lambda: f"fact_{uuid.uuid4().hex[:12]}",
        description="Stable canonical fact identifier",
    )
    entity_id: str = Field(
        description="Identifier of the target company entity",
    )
    canonical_variable: str = Field(
        description="Standardized metric identifier (e.g. 'revenue', 'net_income', 'total_assets')",
    )
    fact_state: FactState = Field(
        default=FactState.RAW_FACT,
        description="Lifecycle state in the pipeline (RAW, STANDARDIZED, VALIDATED, DERIVED, MISSING)",
    )
    reported_value: Optional[Union[float, int, str]] = Field(
        default=None,
        description="Original un-normalized value verbatim from source. None if DATA NOT FOUND.",
    )
    normalized_value: Optional[float] = Field(
        default=None,
        description="Standardized numeric value in base unit (e.g. exact currency amount). None if missing.",
    )
    currency: Currency = Field(
        default=Currency.USD,
        description="ISO 4217 currency code",
    )
    unit_scale: UnitScale = Field(
        default=UnitScale.EXACT,
        description="Multiplier scale of reported value (EXACT, THOUSANDS, MILLIONS, BILLIONS)",
    )
    scale: int = Field(
        default=0,
        description="Power-of-10 exponent if applicable (e.g. 6 for millions)",
    )
    sign: Sign = Field(
        default=Sign.UNSPECIFIED,
        description="Sign classification (POSITIVE, NEGATIVE, ZERO, UNSPECIFIED)",
    )
    fiscal_year: int = Field(
        description="Fiscal reporting year (e.g. 2023)",
    )
    fiscal_period: FiscalPeriodType = Field(
        default=FiscalPeriodType.ANNUAL,
        description="Reporting period type (ANNUAL, Q1, Q2, Q3, Q4, YTD_Q2, YTD_Q3, TTM, INSTANT)",
    )
    period_start: Optional[date] = Field(
        default=None,
        description="Start date of the reporting period for duration metrics",
    )
    period_end: Optional[date] = Field(
        default=None,
        description="End date of reporting period or instant snapshot date",
    )
    is_instant: bool = Field(
        default=False,
        description="True for point-in-time Balance Sheet facts, False for duration (P&L/Cash Flow)",
    )
    accounting_standard: AccountingStandard = Field(
        default=AccountingStandard.UNKNOWN,
        description="Accounting taxonomy/standard (US-GAAP, IFRS, IND-AS)",
    )
    evidence_ids: List[str] = Field(
        default_factory=list,
        description="List of EvidenceRecord IDs supporting this fact",
    )
    parent_fact_ids: List[str] = Field(
        default_factory=list,
        description="IDs of input facts used to compute this fact if DERIVED",
    )
    transformation_history: List[TransformationStep] = Field(
        default_factory=list,
        description="Complete transformation history from RAW to current state",
    )
    validation_status: ValidationStatus = Field(
        default=ValidationStatus.UNVALIDATED,
        description="Validation outcome",
    )
    missing_reason: MissingDataReason = Field(
        default=MissingDataReason.NONE,
        description="Explicit reason when fact is DATA NOT FOUND / MISSING_FACT",
    )
    competing_fact_ids: List[str] = Field(
        default_factory=list,
        description="IDs of conflicting or alternative facts reported for same entity/variable/period",
    )
    conflict_note: Optional[str] = Field(
        default=None,
        description="Notes regarding source discrepancies or contradictions",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Fact record creation timestamp in UTC",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Ontology tags, form numbers (10-K), or XBRL accession metadata",
    )

    @property
    def source_evidence_ids(self) -> List[str]:
        """Alias for evidence_ids for backward compatibility."""
        return self.evidence_ids

    @property
    def knowledge_tier(self) -> str:
        """Presentation epistemic tier representation."""
        return "DERIVED" if self.fact_state == FactState.DERIVED_FACT else "REPORTED"


    @field_validator("canonical_variable")
    @classmethod
    def clean_variable(cls, v: str) -> str:
        cleaned = v.strip().lower().replace(" ", "_")
        if not cleaned:
            raise ValueError("Canonical variable identifier cannot be blank")
        return cleaned

    @classmethod
    def create_missing(
        cls,
        entity_id: str,
        canonical_variable: str,
        fiscal_year: int,
        fiscal_period: FiscalPeriodType,
        missing_reason: MissingDataReason,
        currency: Currency = Currency.USD,
        accounting_standard: AccountingStandard = AccountingStandard.UNKNOWN,
        conflict_note: Optional[str] = None,
    ) -> "FactRecord":
        """Factory method to explicitly record a missing financial fact (DATA NOT FOUND)."""
        return cls(
            entity_id=entity_id,
            canonical_variable=canonical_variable,
            fact_state=FactState.MISSING_FACT,
            reported_value=None,
            normalized_value=None,
            currency=currency,
            fiscal_year=fiscal_year,
            fiscal_period=fiscal_period,
            accounting_standard=accounting_standard,
            validation_status=ValidationStatus.DATA_NOT_FOUND,
            missing_reason=missing_reason,
            conflict_note=conflict_note or f"DATA NOT FOUND: {missing_reason.value}",
        )


class ValidationResult(BaseModel):
    """The outcome of a validation rule executed against a fact or entity."""
    result_id: str = Field(
        default_factory=lambda: f"val_{uuid.uuid4().hex[:12]}",
        description="Unique identifier for validation finding",
    )
    target_id: str = Field(
        description="Identifier of the validated FactRecord or CompanyEntity",
    )
    target_type: str = Field(
        default="fact",
        description="'fact' or 'entity'",
    )
    rule_name: str = Field(
        description="Identifier of the rule executed (e.g. 'non_negative_assets', 'period_continuity')",
    )
    passed: bool = Field(
        description="True if rule passed, False if violated",
    )
    severity: ValidationSeverity = Field(
        default=ValidationSeverity.ERROR,
        description="Severity level if violated",
    )
    observed_value: Any = Field(
        default=None,
        description="The actual value that was checked",
    )
    expected_range: Optional[str] = Field(
        default=None,
        description="Expected range or condition specification (e.g. 'value >= 0')",
    )
    message: str = Field(
        description="Detailed human-readable explanation of the validation outcome",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of evaluation",
    )
