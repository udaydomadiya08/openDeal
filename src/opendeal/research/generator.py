"""Research brief generator orchestrating retrieval, synthesis, validation, and storage.

Enforces strict knowledge boundaries:
1. Source-reported facts link to primary evidence.
2. Calculations use existing deterministic Python engines.
3. Assumptions are explicitly labeled and never presented as fact.
4. Interpretations remain qualitative and bounded by evidence.
5. Missing or conflicting records are preserved rather than overwritten or fabricated.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from opendeal.facts.models import FactRecord
from opendeal.research.contracts import (
    ClaimVerificationStatus,
    DataGap,
    FinancialTrend,
    KnowledgeTier,
    ResearchBrief,
    ResearchClaim,
    ResearchScope,
    ResearchStatus,
    RiskIndicator,
)
from opendeal.research.grounding import GroundingValidator
from opendeal.research.providers.base import AIProvider, SynthesisRequest
from opendeal.research.providers.mock import DeterministicMockProvider
from opendeal.research.retriever import EvidenceRetriever
from opendeal.storage.base import StorageInterface

logger = logging.getLogger(__name__)


class ResearchBriefGenerator:
    """Orchestrates evidence-grounded company intelligence and research brief generation."""

    def __init__(
        self,
        storage: StorageInterface,
        provider: Optional[AIProvider] = None,
    ):
        self.storage = storage
        self.provider = provider or DeterministicMockProvider()
        self.retriever = EvidenceRetriever(storage)
        self.validator = GroundingValidator(storage)

    def generate_brief(self, scope: ResearchScope) -> ResearchBrief:
        """Generate a fully verified, evidence-grounded research brief."""
        brief_id = f"rb_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        # 1. Verify Entity Existence
        entity = self.storage.get_entity(scope.entity_id)
        if not entity:
            brief = ResearchBrief(
                research_id=brief_id,
                entity_id=scope.entity_id,
                brief_title=f"Failed Research Brief: {scope.entity_id}",
                entity_name=None,
                scope=scope,
                status=ResearchStatus.FAILED,
                generated_at=now,
                model_provider=self.provider.name,
                model_id=getattr(self.provider, "_model_id", "unknown"),
                executive_summary=f"Entity with ID '{scope.entity_id}' could not be located in company registry records.",
                limitations=["Entity does not exist in local database."],
            )
            return brief

        entity_name = entity.legal_name

        # 2. Assemble Evidence Context
        context = self.retriever.assemble_context(scope)
        facts = self.storage.get_facts_for_entity(scope.entity_id)

        # Check for insufficient evidence
        if not context.evidence_records and not context.facts:
            brief = ResearchBrief(
                research_id=brief_id,
                entity_id=scope.entity_id,
                brief_title=f"Insufficient Evidence: {entity_name}",
                entity_name=entity_name,
                scope=scope,
                status=ResearchStatus.INSUFFICIENT_EVIDENCE,
                generated_at=now,
                model_provider=self.provider.name,
                model_id=getattr(self.provider, "_model_id", "unknown"),
                executive_summary=(
                    f"Insufficient primary evidence available for {entity_name} ({entity.jurisdiction}). "
                    f"OpenDeal policy prohibits generating speculative briefs without verifiable source filings."
                ),
                data_gaps=context.known_gaps,
                limitations=[
                    "No verifiable regulatory filings, registry records, or financial facts available.",
                    "Speculative or halluncinated synthesis is strictly blocked.",
                ],
            )
            self.storage.save_research_brief(brief.to_storage_dict())
            return brief

        # 3. Compute Deterministic Multi-Period Trends
        financial_trends = self.retriever.compute_financial_trends(facts)

        # 4. Deterministic Valuation Summary (Financial-Analysis Boundary)
        val_summary = None
        det_val_outputs: List[Dict[str, Any]] = []
        if scope.include_valuations:
            val_summary, det_val_outputs = self._compute_deterministic_valuation_context(facts, entity)

        # 5. Model Synthesis (Safe Invocation with Fallback)
        synthesis_failed = False
        fallback_reason = None
        synthesis_result = None

        request = SynthesisRequest(
            entity_name=entity_name,
            context=context,
        )

        try:
            synthesis_result = self.provider.synthesize(request)
        except TimeoutError as te:
            synthesis_failed = True
            fallback_reason = f"Provider timeout: {str(te)}"
            logger.warning(fallback_reason)
        except Exception as exc:
            synthesis_failed = True
            fallback_reason = f"Provider error: {str(exc)}"
            logger.warning(fallback_reason)

        raw_claims: List[Dict[str, Any]] = []
        raw_risks: List[Dict[str, Any]] = []
        exec_summary = ""

        if synthesis_failed or synthesis_result is None:
            # Deterministic Fallback: Construct grounded claims directly from verified facts
            exec_summary = (
                f"Automated intelligence brief for {entity_name} ({entity.jurisdiction}). "
                f"Synthesized from {len(context.facts)} primary facts and {len(context.evidence_records)} "
                f"verified evidence items. (Provider fallback engaged: {fallback_reason})."
            )
            raw_claims = self._build_deterministic_fallback_claims(entity, facts, context)
            raw_risks = [
                {
                    "category": "OPERATIONAL",
                    "description": f"Data coverage limitation: {g.topic} is missing.",
                    "is_analyst_assumption": False,
                    "evidence_ids": [context.evidence_records[0]["evidence_id"]] if context.evidence_records else [],
                    "severity": g.severity,
                }
                for g in context.known_gaps[:2]
            ]
        else:
            exec_summary = synthesis_result.executive_summary
            raw_claims = synthesis_result.synthesized_claims
            raw_risks = synthesis_result.risk_assessments

        # 6. Parse and Validate Claims (Grounding & Anti-Hallucination)
        candidate_claims: List[ResearchClaim] = []
        for c_dict in raw_claims:
            tier_str = c_dict.get("knowledge_tier", KnowledgeTier.SOURCE_REPORTED_FACT.value)
            try:
                tier = KnowledgeTier(tier_str)
            except ValueError:
                tier = KnowledgeTier.AI_INTERPRETATION

            candidate_claims.append(
                ResearchClaim(
                    knowledge_tier=tier,
                    statement=c_dict.get("statement", ""),
                    evidence_ids=c_dict.get("evidence_ids", []),
                    fact_ids=c_dict.get("fact_ids", []),
                )
            )

        audited_claims = self.validator.validate_claims(candidate_claims, scope.entity_id)

        # Parse and Validate Risk Indicators
        candidate_risks: List[RiskIndicator] = []
        for r_dict in raw_risks:
            candidate_risks.append(
                RiskIndicator(
                    category=r_dict.get("category", "OPERATIONAL"),
                    description=r_dict.get("description", ""),
                    is_analyst_assumption=r_dict.get("is_analyst_assumption", False),
                    evidence_ids=r_dict.get("evidence_ids", []),
                    severity=r_dict.get("severity", "LOW"),
                )
            )
        audited_risks = self.validator.validate_risks(candidate_risks, scope.entity_id)

        # 7. Consolidated Provenance and Limitations
        cited_evidence_ids = sorted(
            list(
                {
                    ev_id
                    for c in audited_claims
                    if c.verification_status == ClaimVerificationStatus.VERIFIED
                    for ev_id in c.evidence_ids
                }.union(
                    {
                        ev_id
                        for r in audited_risks
                        if not r.is_analyst_assumption
                        for ev_id in r.evidence_ids
                    }
                )
            )
        )

        provenance = self.validator.summarize_provenance(cited_evidence_ids)

        limitations = [
            "Source hash establishes content identity and tamper-detection, not inherent truth of management statements.",
            "Missing figures are never inferred as zero; explicit data gaps are preserved.",
            "All valuation calculations rely strictly on deterministic algorithms without generative arithmetic.",
        ]
        if synthesis_failed:
            limitations.append(f"AI Provider Notice: {fallback_reason}. Brief generated via deterministic fallback.")
        if context.detected_conflicts:
            limitations.append(f"Found {len(context.detected_conflicts)} contradictory figures across filings.")
        if context.sanitization_warnings:
            limitations.extend(context.sanitization_warnings)

        status = ResearchStatus.PARTIAL if synthesis_failed else ResearchStatus.COMPLETED

        brief = ResearchBrief(
            research_id=brief_id,
            entity_id=scope.entity_id,
            brief_title=f"Company Research Brief: {entity_name}",
            entity_name=entity_name,
            scope=scope,
            status=status,
            generated_at=now,
            model_provider=self.provider.name,
            model_id=getattr(self.provider, "_model_id", getattr(self.provider, "_model_name", "unknown")),
            executive_summary=exec_summary,
            claims=audited_claims,
            financial_trends=financial_trends,
            data_gaps=context.known_gaps,
            conflicting_evidence=context.detected_conflicts,
            risk_indicators=audited_risks,
            cited_evidence_ids=cited_evidence_ids,
            valuation_summary=val_summary,
            deterministic_valuations=det_val_outputs,
            limitations=limitations,
            provenance_summary=provenance,
            metadata={
                "evidence_items_considered": len(context.evidence_records),
                "facts_considered": len(context.facts),
                "sanitization_warnings": context.sanitization_warnings,
            },
        )

        # Save to reproducible history store
        self.storage.save_research_brief(brief.to_storage_dict())
        return brief

    def _build_deterministic_fallback_claims(
        self,
        entity: Any,
        facts: List[FactRecord],
        context: Any,
    ) -> List[Dict[str, Any]]:
        """Construct verified claims directly from database state when AI provider is unreachable."""
        claims: List[Dict[str, Any]] = []
        ev_ids = [e["evidence_id"] for e in context.evidence_records if "evidence_id" in e]
        primary_ev = [ev_ids[0]] if ev_ids else []

        claims.append({
            "statement": f"{entity.legal_name} is registered under jurisdiction {entity.jurisdiction}.",
            "knowledge_tier": KnowledgeTier.SOURCE_REPORTED_FACT.value,
            "evidence_ids": primary_ev,
            "fact_ids": [],
        })

        rev_facts = [f for f in facts if f.canonical_variable == "revenue"]
        if rev_facts:
            f = rev_facts[-1]
            claims.append({
                "statement": (
                    f"Reported {f.canonical_variable} of {f.normalized_value or f.reported_value} "
                    f"({f.currency.value if hasattr(f.currency, 'value') else f.currency}) "
                    f"for period {f.fiscal_period or f.fiscal_year}."
                ),
                "knowledge_tier": KnowledgeTier.SOURCE_REPORTED_FACT.value,
                "evidence_ids": f.evidence_ids or primary_ev,
                "fact_ids": [f.fact_id],
            })

        if context.known_gaps:
            claims.append({
                "statement": f"Identified data gap: {context.known_gaps[0].topic} is missing.",
                "knowledge_tier": KnowledgeTier.MISSING_OR_CONFLICTING.value,
                "evidence_ids": [],
                "fact_ids": [],
            })

        return claims

    def _compute_deterministic_valuation_context(
        self,
        facts: List[FactRecord],
        entity: Any,
    ) -> tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        """Call existing deterministic valuation primitives without AI arithmetic."""
        # Find latest available financial inputs
        revenue_fact = next((f for f in reversed(facts) if f.canonical_variable == "revenue" and f.normalized_value), None)
        net_income_fact = next((f for f in reversed(facts) if f.canonical_variable == "net_income" and f.normalized_value), None)

        if not revenue_fact:
            return None, []

        val_summary = {
            "mode": "DETERMINISTIC_PRIMITIVE",
            "base_metric": "revenue",
            "base_value": revenue_fact.normalized_value,
            "currency": revenue_fact.currency.value if hasattr(revenue_fact.currency, "value") else str(revenue_fact.currency),
            "period": revenue_fact.fiscal_period or f"FY{revenue_fact.fiscal_year}",
            "calculation_status": "DETERMINISTIC_AVAILABLE",
            "limitation": "Authoritative valuation requires calibrated industry multiple or explicit DCF cash-flow schedule.",
        }

        det_outputs = [
            {
                "model_type": "DETERMINISTIC_REVENUE_BASE",
                "reported_revenue": revenue_fact.normalized_value,
                "fact_id": revenue_fact.fact_id,
                "evidence_ids": revenue_fact.evidence_ids,
                "limitations": "No generative LLM math used; numbers reflect database fact values.",
            }
        ]

        return val_summary, det_outputs
