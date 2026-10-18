"""Typed contracts and domain models for private-market deals, funding, and transaction intelligence."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from opendeal.core.types import Currency
from opendeal.entities.models import CompanyEntity
from opendeal.evidence.models import EvidenceRecord, SourceReference


# ── Enumerations ────────────────────────────────────────────────────────────

class FundingRoundType(str, Enum):
    """Categorization of capital-raising events."""
    PRE_SEED = "PRE_SEED"
    SEED = "SEED"
    SERIES_A = "SERIES_A"
    SERIES_B = "SERIES_B"
    SERIES_C = "SERIES_C"
    SERIES_D_PLUS = "SERIES_D_PLUS"
    GROWTH_EQUITY = "GROWTH_EQUITY"
    PRIVATE_EQUITY = "PRIVATE_EQUITY"
    DEBT_FINANCING = "DEBT_FINANCING"
    CONVERTIBLE_NOTE = "CONVERTIBLE_NOTE"
    SAFE = "SAFE"
    GRANT = "GRANT"
    VENTURE_SERIES_UNKNOWN = "VENTURE_SERIES_UNKNOWN"
    OTHER = "OTHER"


class AmountKind(str, Enum):
    """Distinguishes aggregate round amount from tranche or cumulative funding."""
    ROUND_TOTAL = "ROUND_TOTAL"
    TRANCHE = "TRANCHE"
    CUMULATIVE = "CUMULATIVE"
    UNKNOWN = "UNKNOWN"


class InvestorType(str, Enum):
    """Classification of capital providers."""
    INSTITUTION = "INSTITUTION"
    FUND = "FUND"
    INDIVIDUAL = "INDIVIDUAL"
    ANGEL_SYNDICATE = "ANGEL_SYNDICATE"
    CORPORATE_VENTURE = "CORPORATE_VENTURE"
    GOVERNMENT_AGENCY = "GOVERNMENT_AGENCY"
    OTHER = "OTHER"


class TransactionType(str, Enum):
    """M&A and corporate restructuring event categories."""
    ACQUISITION = "ACQUISITION"
    MERGER = "MERGER"
    DIVESTITURE = "DIVESTITURE"
    ASSET_PURCHASE = "ASSET_PURCHASE"
    SUBSIDIARY_ACQUISITION = "SUBSIDIARY_ACQUISITION"
    BUYOUT = "BUYOUT"
    IPO = "IPO"
    SPAC_MERGER = "SPAC_MERGER"
    OTHER = "OTHER"


class TransactionStatus(str, Enum):
    """Verified stage of corporate transaction."""
    RUMORED = "RUMORED"
    ANNOUNCED = "ANNOUNCED"
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    TERMINATED = "TERMINATED"
    WITHDRAWN = "WITHDRAWN"
    UNKNOWN = "UNKNOWN"


class ConsiderationType(str, Enum):
    """Payment structure for M&A consideration."""
    CASH = "CASH"
    STOCK = "STOCK"
    DEBT_ASSUMPTION = "DEBT_ASSUMPTION"
    EARNOUT = "EARNOUT"
    MIXED = "MIXED"
    UNDISCLOSED = "UNDISCLOSED"


class DealVerificationStatus(str, Enum):
    """Verification grounding status of transaction records."""
    VERIFIED = "VERIFIED"
    VERIFIED_PRIMARY_SOURCE = "VERIFIED_PRIMARY_SOURCE"
    UNVERIFIED = "UNVERIFIED"
    CONTESTED = "CONTESTED"


# ── Conflict Model ──────────────────────────────────────────────────────────

class DealConflict(BaseModel):
    """Audit log of conflicting data points recorded by distinct sources for a transaction."""
    conflict_id: str = Field(default_factory=lambda: f"cnf_{uuid.uuid4().hex[:12]}")
    target_event_id: str
    event_category: str  # "FUNDING" or "TRANSACTION"
    field_name: str
    source_a_id: str
    observed_value_a: Any
    source_b_id: str
    observed_value_b: Any
    resolution_status: str = "UNRESOLVED"
    resolution_notes: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Convert conflict to dictionary suitable for JSON serialization and CLI presentation."""
        return {
            "conflict_id": self.conflict_id,
            "target_event_id": self.target_event_id,
            "event_category": self.event_category,
            "field_name": self.field_name,
            "differing_fields": [self.field_name],
            "existing_source_reference": self.source_a_id,
            "existing_value": str(self.observed_value_a),
            "incoming_source_reference": self.source_b_id,
            "incoming_value": str(self.observed_value_b),
            "resolution_status": self.resolution_status,
            "resolution_notes": self.resolution_notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ── Funding Models ──────────────────────────────────────────────────────────

class FundingParticipant(BaseModel):
    """An investor participating in a specific capital raise."""
    participant_id: str = Field(default_factory=lambda: f"fp_{uuid.uuid4().hex[:12]}")
    round_id: str
    investor_id: Optional[str] = None
    fund_id: Optional[str] = None
    investor_name: Optional[str] = None
    is_lead: bool = False
    investor_type: InvestorType = InvestorType.INSTITUTION
    disclosed_commitment: Optional[float] = None
    currency: Optional[Currency] = None
    shares_acquired: Optional[float] = None
    implied_ownership_pct: Optional[float] = None
    ownership_assumptions: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class FundingRound(BaseModel):
    """Capital raise event backed by verifiable primary evidence."""
    round_id: str = Field(default_factory=lambda: f"rnd_{uuid.uuid4().hex[:12]}")
    company_id: str
    company_name: Optional[str] = None
    round_type: FundingRoundType
    announced_date: Optional[date] = None
    closing_date: Optional[date] = None
    disclosed_amount: Optional[float] = None  # None indicates undisclosed; NEVER 0!
    currency: Currency = Currency.USD
    amount_kind: AmountKind = AmountKind.ROUND_TOTAL
    pre_money_valuation: Optional[float] = None
    post_money_valuation: Optional[float] = None
    instrument_type: Optional[str] = None
    lead_investor_ids: List[str] = Field(default_factory=list)
    participant_ids: List[str] = Field(default_factory=list)
    participants: List[FundingParticipant] = Field(default_factory=list)
    source_reference_ids: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    verification_status: DealVerificationStatus = DealVerificationStatus.UNVERIFIED
    conflict_notes: Optional[str] = None
    conflicts: List[DealConflict] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── M&A and Transaction Models ──────────────────────────────────────────────

class TransactionEvent(BaseModel):
    """M&A, divestiture, or corporate control transaction backed by verified sources."""
    transaction_id: str = Field(default_factory=lambda: f"tx_{uuid.uuid4().hex[:12]}")
    transaction_type: TransactionType
    acquirer_id: Optional[str] = None
    acquirer_name: Optional[str] = None
    target_id: str
    target_name: Optional[str] = None
    seller_id: Optional[str] = None
    seller_name: Optional[str] = None
    announced_date: Optional[date] = None
    expected_completion_date: Optional[date] = None
    actual_completion_date: Optional[date] = None
    status: TransactionStatus = TransactionStatus.ANNOUNCED
    disclosed_value: Optional[float] = None  # None indicates undisclosed; NEVER 0!
    currency: Currency = Currency.USD
    consideration_type: ConsiderationType = ConsiderationType.UNDISCLOSED
    consideration_details: Dict[str, Any] = Field(default_factory=dict)
    assets_or_business_unit: Optional[str] = None
    regulatory_approvals: Optional[str] = None
    termination_reason: Optional[str] = None
    source_reference_ids: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    verification_status: DealVerificationStatus = DealVerificationStatus.UNVERIFIED
    conflict_notes: Optional[str] = None
    conflicts: List[DealConflict] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Timeline & Overview Models ──────────────────────────────────────────────

class TimelineItem(BaseModel):
    """Chronologically sorted event entry with deterministic tie-breaking."""
    event_id: str
    event_type: str  # "FUNDING" or "TRANSACTION"
    event_date: Optional[date] = None
    date_type: str  # "ANNOUNCED", "CLOSED", "EFFECTIVE"
    title: str
    summary: str
    disclosed_amount: Optional[float] = None
    currency: Optional[Currency] = None
    status: str
    counterparties: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    has_conflicts: bool = False


class CompanyDealsOverview(BaseModel):
    """Synthesized corporate funding and transaction profile."""
    entity: CompanyEntity
    total_funding_disclosed: Optional[float] = None
    funding_currency: Optional[Currency] = None
    funding_rounds_count: int = 0
    funding_rounds: List[FundingRound] = Field(default_factory=list)
    lead_investors: List[str] = Field(default_factory=list)
    all_investors: List[str] = Field(default_factory=list)
    acquisitions_as_acquirer_count: int = 0
    acquisitions: List[TransactionEvent] = Field(default_factory=list)
    transactions_as_target: List[TransactionEvent] = Field(default_factory=list)
    divestitures: List[TransactionEvent] = Field(default_factory=list)
    timeline: List[TimelineItem] = Field(default_factory=list)
    conflicts_count: int = 0
    coverage_notes: List[str] = Field(default_factory=list)
