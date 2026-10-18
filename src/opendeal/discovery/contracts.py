"""Typed contracts and domain models for target discovery, screening, and watchlists."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from opendeal.core.types import (
    AccountingStandard,
    Currency,
    EntityType,
    FiscalPeriodType,
    UnitScale,
)
from opendeal.entities.models import CompanyEntity, LegalIdentifier
from opendeal.evidence.models import EvidenceRecord, SourceReference
from opendeal.facts.models import FactRecord


# ── Eligibility Contracts ───────────────────────────────────────────────────

class EligibilityOutcome(str, Enum):
    """Explicit eligibility determination outcome."""
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ExclusionReason(BaseModel):
    """Detailed rationale and evidence for why a candidate failed a criterion or has missing data."""
    criterion_name: str
    outcome: EligibilityOutcome
    message: str
    metric_name: Optional[str] = None
    expected_threshold: Optional[Any] = None
    actual_value: Optional[Any] = None
    supporting_fact_ids: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)


class FinancialFilterCriterion(BaseModel):
    """Declarative specification for a financial metric threshold."""
    metric: str  # e.g., "revenue", "operating_income", "ebitda", "net_income", "total_assets"
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    currency: Optional[Currency] = None
    unit_scale: Optional[UnitScale] = None
    fiscal_period: Optional[FiscalPeriodType] = None
    fiscal_year: Optional[int] = None
    require_exact_currency: bool = True


class TargetCriteria(BaseModel):
    """User-specified criteria for target discovery and screening."""
    jurisdictions: List[str] = Field(default_factory=list)
    excluded_jurisdictions: List[str] = Field(default_factory=list)
    sectors: List[str] = Field(default_factory=list)
    sic_codes: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    company_types: List[EntityType] = Field(default_factory=list)
    excluded_company_types: List[EntityType] = Field(default_factory=list)
    financial_filters: List[FinancialFilterCriterion] = Field(default_factory=list)

    # Shorthand helpers for common screening filters
    min_revenue: Optional[float] = None
    max_revenue: Optional[float] = None
    revenue_currency: Optional[Currency] = None

    min_confidence_score: float = 0.5
    require_active: bool = True
    max_candidates: int = 50

    # Deterministic scoring preferences
    scoring_weights: Dict[str, float] = Field(
        default_factory=lambda: {
            "scale": 0.30,
            "profitability": 0.25,
            "fit": 0.20,
            "evidence_quality": 0.15,
            "completeness": 0.10,
        }
    )
    scoring_version: str = "v1.0.0"
    missing_data_penalty: float = 0.5


class EligibilityDecision(BaseModel):
    """Complete record of an eligibility evaluation for a target candidate."""
    outcome: EligibilityOutcome
    passed_criteria: List[str] = Field(default_factory=list)
    exclusion_reasons: List[ExclusionReason] = Field(default_factory=list)
    insufficient_data_reasons: List[ExclusionReason] = Field(default_factory=list)
    summary: str = ""


# ── Scoring Contracts ───────────────────────────────────────────────────────

class ScoringFactorBreakdown(BaseModel):
    """Detailed calculation breakdown for an individual scoring factor."""
    factor_name: str
    raw_value: Optional[float] = None
    normalized_score: float  # 0.0 to 1.0
    weight: float
    weighted_score: float
    missing: bool = False
    missing_penalty: float = 0.0
    explanation: str


class TargetScoringBreakdown(BaseModel):
    """Machine-readable, deterministic breakdown of overall candidate score."""
    overall_score: float  # 0.0 to 100.0
    factor_breakdowns: Dict[str, ScoringFactorBreakdown] = Field(default_factory=dict)
    scoring_version: str
    tie_breaker_key: str


# ── Candidate & Screening Result Contracts ──────────────────────────────────

class CandidateCompany(BaseModel):
    """Discovered company candidate unified with verified facts and evidence."""
    entity: CompanyEntity
    matched_identifiers: List[LegalIdentifier] = Field(default_factory=list)
    facts: List[FactRecord] = Field(default_factory=list)
    evidence: List[EvidenceRecord] = Field(default_factory=list)
    source_references: List[SourceReference] = Field(default_factory=list)
    discovery_source: str = "LOCAL_STORE"


class ScreenedTarget(BaseModel):
    """A target evaluated through the discovery pipeline."""
    candidate: CandidateCompany
    eligibility: EligibilityDecision
    scoring: Optional[TargetScoringBreakdown] = None
    rank: Optional[int] = None


class DiscoveryRunMetadata(BaseModel):
    """Audit trail and reproducibility metadata for a discovery run."""
    run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    criteria: TargetCriteria
    scoring_version: str
    source_adapters_used: List[str] = Field(default_factory=list)
    is_live: bool = False
    total_candidates_evaluated: int = 0
    eligible_count: int = 0
    ineligible_count: int = 0
    insufficient_data_count: int = 0
    coverage_limitations: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class ScreeningResult(BaseModel):
    """Complete result container for a discovery run."""
    metadata: DiscoveryRunMetadata
    targets: List[ScreenedTarget] = Field(default_factory=list)


# ── Watchlist / Target List Contracts ───────────────────────────────────────

class TargetListStatus(str, Enum):
    """Workflow statuses for tracked targets in a target list."""
    NEW = "NEW"
    RESEARCHING = "RESEARCHING"
    SHORTLISTED = "SHORTLISTED"
    REJECTED = "REJECTED"
    ON_HOLD = "ON_HOLD"


class TargetList(BaseModel):
    """Persistent user-defined target list or watchlist."""
    list_id: str
    name: str
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    member_count: int = 0

    @property
    def status(self) -> str:
        """Presentation status for target list."""
        return "ACTIVE"



class TargetListItem(BaseModel):
    """Membership entry of a company entity in a target list."""
    list_id: str
    entity_id: str
    entity_name: str
    status: TargetListStatus = TargetListStatus.NEW
    notes: Optional[str] = None
    source_run_id: Optional[str] = None
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TargetListHistory(BaseModel):
    """Audit log entry capturing a status change or note update for a list member."""
    history_id: str
    list_id: str
    entity_id: str
    old_status: Optional[TargetListStatus] = None
    new_status: TargetListStatus
    note: Optional[str] = None
    changed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
