"""Contracts and data models for Phase 5 AI Research and Intelligence Layer.

Defines research requests, scopes, knowledge tiers, evidence-backed claims,
research briefs, financial trend models, data gaps, and context contracts.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field


class KnowledgeTier(str, Enum):
    """Categorization of research claims into explicit epistemic tiers.
    
    Prevents presenting interpretations or assumptions as verified facts.
    """
    SOURCE_REPORTED_FACT = "SOURCE_REPORTED_FACT"
    DERIVED_CALCULATION = "DERIVED_CALCULATION"
    ANALYST_ASSUMPTION = "ANALYST_ASSUMPTION"
    AI_INTERPRETATION = "AI_INTERPRETATION"
    MISSING_OR_CONFLICTING = "MISSING_OR_CONFLICTING"


class ClaimVerificationStatus(str, Enum):
    """Grounding verification status of an extracted research claim."""
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    CONFLICTED = "CONFLICTED"
    REJECTED = "REJECTED"


class ResearchStatus(str, Enum):
    """Status lifecycle of a research brief."""
    COMPLETED = "COMPLETED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


class ResearchScope(BaseModel):
    """Defines the boundaries and parameters of an automated research inquiry."""
    entity_id: str = Field(description="Target company entity ID")
    include_financials: bool = Field(
        default=True,
        description="Include financial facts and historical trend analysis",
    )
    include_relationships: bool = Field(
        default=True,
        description="Include corporate hierarchy and legal entity relationships",
    )
    include_valuations: bool = Field(
        default=True,
        description="Include deterministic valuation engine outputs",
    )
    max_evidence_items: int = Field(
        default=50,
        description="Maximum evidence items to retrieve and rank into context",
    )
    period_start: Optional[date] = Field(
        default=None,
        description="Earliest financial reporting period date to include",
    )
    period_end: Optional[date] = Field(
        default=None,
        description="Latest financial reporting period date to include",
    )


class ResearchClaim(BaseModel):
    """A single atomic research assertion grounded in evidence."""
    claim_id: str = Field(
        default_factory=lambda: f"clm_{uuid.uuid4().hex[:10]}",
        description="Stable claim identifier",
    )
    knowledge_tier: KnowledgeTier = Field(
        description="Epistemic tier (Fact, Calculation, Assumption, Interpretation, Gap)",
    )
    statement: str = Field(
        description="Verbatim statement or synthesized summary of the claim",
    )
    evidence_ids: List[str] = Field(
        default_factory=list,
        description="Resolvable evidence IDs backing this assertion",
    )
    fact_ids: List[str] = Field(
        default_factory=list,
        description="Underlying canonical financial fact IDs, if applicable",
    )
    verification_status: ClaimVerificationStatus = Field(
        default=ClaimVerificationStatus.UNVERIFIED,
        description="Status after validation against stored records",
    )
    verification_notes: Optional[str] = Field(
        default=None,
        description="Auditor explanation or reason for unverified/conflicted status",
    )
    confidence_score: float = Field(
        default=1.0,
        description="Confidence score between 0.0 and 1.0",
    )


class FinancialTrend(BaseModel):
    """Empirical multi-period financial trajectory computed from verified facts."""
    metric_name: str = Field(description="Canonical metric name (e.g. revenue, ebitda)")
    start_period: str = Field(description="Initial observation period")
    end_period: str = Field(description="Final observation period")
    observations: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Ordered list of period data points with fact and evidence references",
    )
    cagr: Optional[float] = Field(
        default=None,
        description="Compound annual growth rate if strictly comparable across periods",
    )
    direction: str = Field(
        default="UNKNOWN",
        description="Directional trajectory: INCREASING, DECREASING, STABLE, VOLATILE, INSUFFICIENT_DATA",
    )
    is_comparable: bool = Field(
        default=True,
        description="Whether accounting standard and currency remain consistent across all points",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Analytical notes regarding reporting anomalies or currency shifts",
    )


class InformationGapType(str, Enum):
    """Taxonomy distinguishing reasons for missing data and unresolved facts.
    
    Prevents claiming a company failed to report a metric when data was merely
    not accessed or absent from locally ingested records.
    """
    NOT_FOUND_IN_INGESTED_DATA = "NOT_FOUND_IN_INGESTED_DATA"
    SOURCE_DOES_NOT_REPORT = "SOURCE_DOES_NOT_REPORT"
    SOURCE_NOT_ACCESSED = "SOURCE_NOT_ACCESSED"
    SOURCE_ACCESS_FAILED = "SOURCE_ACCESS_FAILED"
    CONFLICTING_OBSERVATIONS = "CONFLICTING_OBSERVATIONS"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class DataGap(BaseModel):
    """Explicitly identified missing information or unresolved data requirement."""
    gap_id: str = Field(
        default_factory=lambda: f"gap_{uuid.uuid4().hex[:8]}",
        description="Unique identifier for the data gap",
    )
    topic: str = Field(
        description="Domain of the missing data (e.g., EBITDA, Governance, Shareholding)",
    )
    description: str = Field(
        description="Detailed description of what data is absent and its analytical significance",
    )
    severity: str = Field(
        default="MEDIUM",
        description="Impact severity: LOW, MEDIUM, HIGH, CRITICAL",
    )
    gap_type: InformationGapType = Field(
        default=InformationGapType.NOT_FOUND_IN_INGESTED_DATA,
        description="Epistemic categorization of why the data is absent",
    )
    attempted_sources: List[str] = Field(
        default_factory=list,
        description="Sources examined where data was absent",
    )


class ConflictingEvidence(BaseModel):
    """Identified divergence or contradiction across primary records."""
    conflict_id: str = Field(
        default_factory=lambda: f"cnf_{uuid.uuid4().hex[:8]}",
        description="Unique identifier for the detected conflict",
    )
    metric_or_attribute: str = Field(
        description="The variable or attribute under dispute",
    )
    period_or_date: Optional[str] = Field(
        default=None,
        description="Relevant fiscal period or effective date",
    )
    conflicting_records: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="List of divergent fact/evidence records with source details",
    )
    discrepancy_description: str = Field(
        description="Explanation of the discrepancy between sources",
    )


class RiskIndicator(BaseModel):
    """Identified operational, legal, or financial risk backed by evidence or analyst assumption."""
    indicator_id: str = Field(
        default_factory=lambda: f"rsk_{uuid.uuid4().hex[:8]}",
        description="Unique identifier for this risk observation",
    )
    category: str = Field(
        description="Risk category (FINANCIAL_HEALTH, GOVERNANCE, LEGAL, OPERATIONAL, LIQUIDITY)",
    )
    description: str = Field(
        description="Evidence-backed description of the risk indicator",
    )
    is_analyst_assumption: bool = Field(
        default=False,
        description="True if based on analyst assumptions; False if supported by direct evidence",
    )
    evidence_ids: List[str] = Field(
        default_factory=list,
        description="Evidence records supporting this risk finding",
    )
    severity: str = Field(
        default="LOW",
        description="Assessed severity level: LOW, MEDIUM, HIGH",
    )


class CompanyResearchContext(BaseModel):
    """Aggregated context assembled from entity, fact, relationship, and evidence stores."""
    entity: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Core company identity record",
    )
    legal_identifiers: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Known official identifiers (CIK, Companies House, LEI, etc.)",
    )
    relationships: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Corporate hierarchy and entity relationships",
    )
    facts: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Financial and operational facts",
    )
    evidence_records: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Supporting evidence records retrieved and ranked",
    )
    deterministic_valuations: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Pre-calculated deterministic valuation outputs, if available",
    )
    known_gaps: List[DataGap] = Field(
        default_factory=list,
        description="Systematically detected data gaps",
    )
    detected_conflicts: List[ConflictingEvidence] = Field(
        default_factory=list,
        description="Systematically detected conflicts across records",
    )
    sanitization_warnings: List[str] = Field(
        default_factory=list,
        description="Security notices (e.g. prompt injection patterns detected and defused)",
    )


class ResearchBrief(BaseModel):
    """The canonical evidence-grounded company research brief."""
    research_id: str = Field(
        default_factory=lambda: f"res_{uuid.uuid4().hex[:12]}",
        description="Globally unique research brief identifier",
    )
    entity_id: str = Field(description="Target company identifier")
    brief_title: str = Field(default="", description="Descriptive title of the research brief")
    entity_name: Optional[str] = Field(default=None, description="Primary legal company name")
    scope: ResearchScope = Field(description="Scope specification used to generate the brief")
    status: ResearchStatus = Field(description="Completion status of the brief")
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Generation timestamp in UTC",
    )
    model_provider: str = Field(
        default="deterministic_mock",
        description="Model or synthesis provider used (e.g. deterministic_mock, local_llama)",
    )
    model_id: Optional[str] = Field(
        default=None,
        description="Specific model version or identifier string",
    )
    executive_summary: str = Field(
        description="Synthesized executive overview grounded in established evidence",
    )
    claims: List[ResearchClaim] = Field(
        default_factory=list,
        description="All individual factual assertions, calculations, and interpretations",
    )
    financial_trends: List[FinancialTrend] = Field(
        default_factory=list,
        description="Multi-period financial trajectories computed from verified facts",
    )
    data_gaps: List[DataGap] = Field(
        default_factory=list,
        description="Explicit gaps in evidence and missing metrics",
    )
    conflicting_evidence: List[ConflictingEvidence] = Field(
        default_factory=list,
        description="Contradictions and discrepancies preserved without destructive selection",
    )
    risk_indicators: List[RiskIndicator] = Field(
        default_factory=list,
        description="Risk indicators labeled by evidence or analyst assumption",
    )
    cited_evidence_ids: List[str] = Field(
        default_factory=list,
        description="Consolidated list of all verified evidence IDs cited across claims and risks",
    )
    valuation_summary: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Deterministic valuation summary if calculated",
    )
    deterministic_valuations: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Valuation engine outputs preserved with inputs and limitations",
    )
    limitations: List[str] = Field(
        default_factory=list,
        description="Methodological limitations, missing coverage, or confidence boundaries",
    )
    provenance_summary: Dict[str, Any] = Field(
        default_factory=dict,
        description="Summary of sources, evidence counts, and cryptographic hashes cited",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Execution metadata, timing, and security audit flags",
    )

    def to_storage_dict(self) -> Dict[str, Any]:
        """Convert brief to dictionary suitable for SQLite storage."""
        return {
            "research_id": self.research_id,
            "entity_id": self.entity_id,
            "brief_title": self.brief_title or f"Company Research Brief: {self.entity_name or self.entity_id}",
            "executive_summary": self.executive_summary,
            "status": self.status.value if isinstance(self.status, ResearchStatus) else str(self.status),
            "claims": [c.model_dump(mode="json") for c in self.claims],
            "financial_trends": [t.model_dump(mode="json") for t in self.financial_trends],
            "data_gaps": [g.model_dump(mode="json") for g in self.data_gaps],
            "risk_indicators": [r.model_dump(mode="json") for r in self.risk_indicators],
            "cited_evidence_ids": list(self.cited_evidence_ids),
            "valuation_summary": self.valuation_summary,
            "model_provider": self.model_provider,
            "model_id": self.model_id,
            "metadata": {
                **self.metadata,
                "scope": self.scope.model_dump(mode="json"),
                "conflicting_evidence": [c.model_dump(mode="json") for c in self.conflicting_evidence],
                "deterministic_valuations": self.deterministic_valuations,
                "limitations": self.limitations,
                "provenance_summary": self.provenance_summary,
                "entity_name": self.entity_name,
            },
            "created_at": self.generated_at.isoformat(),
        }

    @classmethod
    def from_storage_dict(cls, data: Dict[str, Any]) -> "ResearchBrief":
        """Reconstruct a ResearchBrief instance from storage dictionary with resilient fallbacks."""
        meta = data.get("metadata", {}) or {}
        scope_data = meta.get("scope") or {"entity_id": data.get("entity_id", "UNKNOWN")}
        
        conflicts = []
        for c in meta.get("conflicting_evidence", []):
            try:
                conflicts.append(ConflictingEvidence(**c) if isinstance(c, dict) else c)
            except Exception:
                continue

        limitations = meta.get("limitations", []) or []
        provenance = meta.get("provenance_summary", {}) or {}
        val_engine_outputs = meta.get("deterministic_valuations", []) or []
        entity_name = meta.get("entity_name")

        gen_at = data.get("created_at")
        if isinstance(gen_at, str):
            try:
                dt = datetime.fromisoformat(gen_at)
            except Exception:
                dt = datetime.now(timezone.utc)
        elif isinstance(gen_at, datetime):
            dt = gen_at
        else:
            dt = datetime.now(timezone.utc)

        status_val = data.get("status", "COMPLETED")
        try:
            status = ResearchStatus(status_val)
        except ValueError:
            status = ResearchStatus.COMPLETED

        claims = []
        for c in data.get("claims", []):
            try:
                claims.append(ResearchClaim(**c) if isinstance(c, dict) else c)
            except Exception:
                continue

        financial_trends = []
        for t in data.get("financial_trends", []):
            try:
                financial_trends.append(FinancialTrend(**t) if isinstance(t, dict) else t)
            except Exception:
                continue

        data_gaps = []
        for g in data.get("data_gaps", []):
            try:
                data_gaps.append(DataGap(**g) if isinstance(g, dict) else g)
            except Exception:
                continue

        risk_indicators = []
        for r in data.get("risk_indicators", []):
            try:
                risk_indicators.append(RiskIndicator(**r) if isinstance(r, dict) else r)
            except Exception:
                continue

        return cls(
            research_id=data.get("research_id", f"res_fallback_{uuid.uuid4().hex[:8]}"),
            entity_id=data.get("entity_id", "UNKNOWN"),
            brief_title=data.get("brief_title", ""),
            entity_name=entity_name,
            scope=ResearchScope(**scope_data) if isinstance(scope_data, dict) else ResearchScope(entity_id=data.get("entity_id", "UNKNOWN")),
            status=status,
            generated_at=dt,
            model_provider=data.get("model_provider", "deterministic_mock"),
            model_id=data.get("model_id"),
            executive_summary=data.get("executive_summary", ""),
            claims=claims,
            financial_trends=financial_trends,
            data_gaps=data_gaps,
            conflicting_evidence=conflicts,
            risk_indicators=risk_indicators,
            cited_evidence_ids=data.get("cited_evidence_ids", []) or [],
            valuation_summary=data.get("valuation_summary"),
            deterministic_valuations=val_engine_outputs,
            limitations=limitations,
            provenance_summary=provenance,
            metadata=meta,
        )
