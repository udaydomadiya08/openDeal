"""Deterministic mock provider for offline tests and reproducible evaluations.

Generates structured claims with explicit knowledge tier tagging and verifiable
evidence citations without requiring external network connectivity or paid APIs.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from opendeal.research.contracts import KnowledgeTier
from opendeal.research.providers.base import AIProvider, SynthesisRequest, SynthesisResult


class DeterministicMockProvider(AIProvider):
    """Offline deterministic provider simulating LLM synthesis for tests and offline workflows."""

    def __init__(
        self,
        mode: str = "NORMAL",
        model_id: str = "mock-v1.0-deterministic",
        simulated_delay_ms: float = 0.0,
    ):
        self._mode = mode
        self._model_id = model_id
        self._simulated_delay_ms = simulated_delay_ms

    @property
    def name(self) -> str:
        return "deterministic_mock"

    @property
    def is_external(self) -> bool:
        return False

    @property
    def is_enabled(self) -> bool:
        return True

    def health_check(self) -> bool:
        return True

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        start_time = time.perf_counter()

        if self._mode == "TIMEOUT":
            raise TimeoutError("Deterministic mock provider simulated request timeout after 30.0s")

        if self._mode == "MALFORMED_OUTPUT":
            return SynthesisResult(
                executive_summary="Malformed response structure.",
                synthesized_claims=[
                    {"invalid_key": "No tier or text"},
                    {"statement": "Missing knowledge tier field"},
                ],
                risk_assessments=[],
                provider_name=self.name,
                model_id=self._model_id,
                execution_time_ms=1.0,
                raw_response="INVALID_JSON_STREAM_TRUNCATED",
            )

        if self._mode == "HALLUCINATE_INVALID_CITATIONS":
            return SynthesisResult(
                executive_summary="Mock brief with hallucinated citations.",
                synthesized_claims=[
                    {
                        "statement": "Company holds undisclosed intellectual property in quantum storage.",
                        "knowledge_tier": KnowledgeTier.SOURCE_REPORTED_FACT.value,
                        "evidence_ids": ["ev_non_existent_fake_id_99999"],
                        "fact_ids": ["fact_invented_0000"],
                    },
                    {
                        "statement": "Annual revenue reached 50 billion USD according to undisclosed sources.",
                        "knowledge_tier": KnowledgeTier.SOURCE_REPORTED_FACT.value,
                        "evidence_ids": ["ev_ghost_citation_abc123"],
                        "fact_ids": [],
                    },
                ],
                risk_assessments=[],
                provider_name=self.name,
                model_id=self._model_id,
                execution_time_ms=2.0,
            )

        # NORMAL mode: Ground claims in real context
        context = request.context
        entity = context.entity or {}
        facts = context.facts
        evidence = context.evidence_records
        relationships = context.relationships
        legal_ids = context.legal_identifiers

        entity_name = entity.get("legal_name") or request.entity_name
        jurisdiction = entity.get("jurisdiction", "Unknown Jurisdiction")

        claims: List[Dict[str, Any]] = []

        # Claim 1: Legal Identity (Source-Reported Fact)
        ev_ids_all = [e["evidence_id"] for e in evidence if "evidence_id" in e]
        primary_ev = [ev_ids_all[0]] if ev_ids_all else []

        claims.append({
            "statement": f"{entity_name} is an active corporate entity registered in jurisdiction {jurisdiction}.",
            "knowledge_tier": KnowledgeTier.SOURCE_REPORTED_FACT.value,
            "evidence_ids": primary_ev,
            "fact_ids": [],
        })

        # Claim 2: Registered Identifiers
        if legal_ids:
            first_id = legal_ids[0]
            scheme = first_id.get("scheme", "ID")
            val = first_id.get("identifier", "N/A")
            claims.append({
                "statement": f"Registered under official scheme {scheme} with identifier {val}.",
                "knowledge_tier": KnowledgeTier.SOURCE_REPORTED_FACT.value,
                "evidence_ids": primary_ev,
                "fact_ids": [],
            })

        # Claim 3: Corporate Relationships
        if relationships:
            rel = relationships[0]
            rel_type = rel.get("relationship_type", "AFFILIATE")
            rel_ev = rel.get("evidence_ids", []) or primary_ev
            claims.append({
                "statement": f"Recorded corporate relationship: {rel_type} with entity {rel.get('target_entity_id')}.",
                "knowledge_tier": KnowledgeTier.SOURCE_REPORTED_FACT.value,
                "evidence_ids": rel_ev,
                "fact_ids": [],
            })

        # Claim 4 & 5: Financial Facts
        rev_facts = [f for f in facts if f.get("canonical_variable") == "revenue"]
        if rev_facts:
            rf = rev_facts[-1]
            val = rf.get("normalized_value") or rf.get("reported_value")
            curr = rf.get("currency", "USD")
            period = rf.get("fiscal_period") or f"FY{rf.get('fiscal_year', '')}"
            ev_for_fact = rf.get("evidence_ids", []) or primary_ev
            claims.append({
                "statement": f"Reported revenue of {val:,.2f} {curr} for period {period} in official filings.",
                "knowledge_tier": KnowledgeTier.SOURCE_REPORTED_FACT.value,
                "evidence_ids": ev_for_fact,
                "fact_ids": [rf.get("fact_id")],
            })

        # Claim 6: Derived Calculation (Deterministic computation across comparable periods)
        annual_rev_facts = [
            f for f in rev_facts
            if str(f.get("fiscal_period", "")).upper() in ("ANNUAL", "FY", "FISCALPERIODTYPE.ANNUAL")
        ]
        if len(annual_rev_facts) >= 2:
            sorted_annual = sorted(annual_rev_facts, key=lambda x: x.get("fiscal_year", 0))
            rf0 = sorted_annual[0]
            rf1 = sorted_annual[-1]
            v0 = rf0.get("normalized_value")
            v1 = rf1.get("normalized_value")
            y0 = rf0.get("fiscal_year")
            y1 = rf1.get("fiscal_year")
            curr0 = rf0.get("currency")
            curr1 = rf1.get("currency")

            if v0 and v1 and v0 > 0 and y0 != y1 and curr0 == curr1:
                growth_pct = round(((v1 - v0) / v0) * 100.0, 2)
                claims.append({
                    "statement": f"Deterministic annual revenue growth of {growth_pct}% calculated between FY{y0} and FY{y1}.",
                    "knowledge_tier": KnowledgeTier.DERIVED_CALCULATION.value,
                    "evidence_ids": list(set((rf0.get("evidence_ids", []) + rf1.get("evidence_ids", [])))),
                    "fact_ids": [rf0.get("fact_id"), rf1.get("fact_id")],
                })

        # Claim 7: Analyst Assumption (Explicitly marked)
        claims.append({
            "statement": "Baseline valuation assumption models a normalized long-term GDP terminal growth rate of 2.0%.",
            "knowledge_tier": KnowledgeTier.ANALYST_ASSUMPTION.value,
            "evidence_ids": [],
            "fact_ids": [],
        })

        # Claim 8: AI Interpretation (Carefully labeled interpretation of trends)
        claims.append({
            "statement": "Operational filings reflect consistent revenue reporting discipline across the analyzed historical windows.",
            "knowledge_tier": KnowledgeTier.AI_INTERPRETATION.value,
            "evidence_ids": primary_ev,
            "fact_ids": [],
        })

        # Claim 9: Missing or Conflicting Information
        if context.known_gaps:
            gap = context.known_gaps[0]
            claims.append({
                "statement": f"Data limitation noted: {gap.topic} is currently unrecorded in accessible registry filings.",
                "knowledge_tier": KnowledgeTier.MISSING_OR_CONFLICTING.value,
                "evidence_ids": [],
                "fact_ids": [],
            })

        # Risk Assessments
        risks: List[Dict[str, Any]] = []
        if context.known_gaps:
            risks.append({
                "category": "OPERATIONAL",
                "description": f"Missing disclosure for {context.known_gaps[0].topic} impedes complete debt service audit.",
                "is_analyst_assumption": False,
                "evidence_ids": primary_ev,
                "severity": "MEDIUM",
            })

        risks.append({
            "category": "FINANCIAL_HEALTH",
            "description": "Macroeconomic inflation risks could compress margins (analyst model assumption).",
            "is_analyst_assumption": True,
            "evidence_ids": [],
            "severity": "LOW",
        })

        exec_summary = (
            f"Research brief for {entity_name} ({jurisdiction}). Analysis established {len(facts)} canonical facts "
            f"and {len(evidence)} primary evidence records. Disclosed financial filings and relationships have been "
            f"verified with zero fabricated figures. Identified {len(context.known_gaps)} operational data gaps."
        )

        elapsed = (time.perf_counter() - start_time) * 1000.0

        return SynthesisResult(
            executive_summary=exec_summary,
            synthesized_claims=claims,
            risk_assessments=risks,
            provider_name=self.name,
            model_id=self._model_id,
            execution_time_ms=elapsed,
            raw_response="DETERMINISTIC_MOCK_SUCCESS",
        )
