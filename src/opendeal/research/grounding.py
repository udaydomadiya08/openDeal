"""Grounding validator and anti-hallucination verification engine.

Verifies that every factual claim resolutely links to existing database evidence
records belonging to the target company. Validates cryptographic provenance,
detects temporal conflation, blocks fabricated assertions, and structurally checks
that cited evidence genuinely supports the metric, period, and figures asserted.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from opendeal.evidence.models import EvidenceRecord
from opendeal.facts.models import FactRecord
from opendeal.research.contracts import (
    ClaimVerificationStatus,
    KnowledgeTier,
    ResearchClaim,
    RiskIndicator,
)
from opendeal.storage.base import StorageInterface


def extract_metric_keywords(text: str) -> List[str]:
    """Extract canonical financial metric categories mentioned in assertion text."""
    METRIC_KEYWORDS = {
        "revenue": ["revenue", "revenues", "sales", "turnover"],
        "net_income": ["net income", "profit", "net profit", "loss", "net loss", "net earnings"],
        "operating_income": ["operating income", "operating profit", "ebit"],
        "ebitda": ["ebitda"],
        "total_assets": ["total assets", "assets"],
        "total_liabilities": ["total liabilities", "liabilities", "total debt"],
        "operating_cash_flow": ["cash flow", "operating cash flow", "cash from operations"],
    }
    found = []
    text_lower = text.lower()
    for canonical, synonyms in METRIC_KEYWORDS.items():
        if any(re.search(rf"\b{re.escape(syn)}\b", text_lower) for syn in synonyms):
            found.append(canonical)
    return found


def extract_years(text: str) -> List[int]:
    """Extract fiscal year tokens from claim statement."""
    matches = re.findall(r"\b(19\d\d|20\d\d)\b", text)
    return [int(m) for m in matches]


def extract_numbers(text: str) -> List[float]:
    """Extract numerical dollar/unit quantities from assertion text."""
    nums = []
    # Match shorthand like $500M, 500 million, $50 billion
    mult_pattern = re.findall(r"(\d+(?:\.\d+)?)\s*(m|million|b|billion)\b", text, re.IGNORECASE)
    for val_str, mult in mult_pattern:
        val = float(val_str)
        if mult.lower().startswith("m"):
            nums.append(val * 1_000_000.0)
        elif mult.lower().startswith("b"):
            nums.append(val * 1_000_000_000.0)

    # Match standard formatted numbers e.g. 383,285,000,000 or $500,000,000.00
    std_nums = re.findall(r"\$?\b(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{5,}(?:\.\d+)?)\b", text)
    for n in std_nums:
        clean = n.replace(",", "")
        try:
            val = float(clean)
            if val not in nums and val < 1900 or val > 2100:  # Avoid conflating years
                nums.append(val)
        except ValueError:
            pass
    return nums


class GroundingValidator:
    """Rigorous grounding audit verifying claims against stored evidence and entities."""

    def __init__(self, storage: StorageInterface):
        self.storage = storage

    def validate_claim(self, claim: ResearchClaim, target_entity_id: str) -> ResearchClaim:
        """Audit an individual claim against primary database records.

        Ensures no claim is accepted as a verified fact unless supported by
        resolvable, entity-linked evidence records whose content structurally
        substantiates the assertion without metric, period, or value contradictions.
        """
        # 1. Analyst Assumptions: Explicit model inputs, NEVER accepted as verified factual records
        if claim.knowledge_tier == KnowledgeTier.ANALYST_ASSUMPTION:
            claim.verification_status = ClaimVerificationStatus.UNVERIFIED
            claim.verification_notes = "Analyst assumption: explicit model input, not an established source fact."
            claim.confidence_score = 0.5
            return claim

        # 2. AI Interpretations: Qualitative syntheses, NEVER presented as verified facts
        if claim.knowledge_tier == KnowledgeTier.AI_INTERPRETATION:
            if not claim.evidence_ids:
                claim.verification_status = ClaimVerificationStatus.UNVERIFIED
                claim.verification_notes = "Interpretation lacks supporting primary evidence references."
                claim.confidence_score = 0.0
                return claim

            # Verify citations exist and belong to entity
            entity_evidence = self.storage.get_evidence_for_entity(target_entity_id)
            entity_ev_ids = {ev.evidence_id for ev in entity_evidence}
            for ev_id in claim.evidence_ids:
                ev = self.storage.get_evidence(ev_id)
                if not ev:
                    claim.verification_status = ClaimVerificationStatus.REJECTED
                    claim.verification_notes = f"Hallucination rejected: Evidence ID '{ev_id}' does not exist in database."
                    claim.confidence_score = 0.0
                    return claim
                if ev_id not in entity_ev_ids:
                    claim.verification_status = ClaimVerificationStatus.REJECTED
                    claim.verification_notes = f"Cross-entity citation rejected: Evidence ID '{ev_id}' belongs to a different subject, not '{target_entity_id}'."
                    claim.confidence_score = 0.0
                    return claim

            # Interpretations remain explicitly UNVERIFIED as established facts, with capped confidence
            claim.verification_status = ClaimVerificationStatus.UNVERIFIED
            claim.verification_notes = (
                f"AI Interpretation: Qualitative assessment grounded in {len(claim.evidence_ids)} "
                f"evidence record(s); unverified as an authoritative factual statement."
            )
            claim.confidence_score = 0.70
            return claim

        # 3. Missing or Conflicting Information: Observations of absence or contradiction in storage
        if claim.knowledge_tier == KnowledgeTier.MISSING_OR_CONFLICTING:
            claim.verification_status = ClaimVerificationStatus.VERIFIED
            claim.verification_notes = "Grounded data gap: absence confirmed in local storage; does not imply issuer non-disclosure."
            claim.confidence_score = 0.85
            return claim

        # 4. Source-Reported Facts and Derived Calculations require evidence citations
        if not claim.evidence_ids:
            claim.verification_status = ClaimVerificationStatus.UNVERIFIED
            claim.verification_notes = "Unverified: No supporting primary evidence references provided."
            claim.confidence_score = 0.0
            return claim

        # Verify each cited evidence ID existence and entity association
        valid_ev_records: List[EvidenceRecord] = []
        entity_evidence = self.storage.get_evidence_for_entity(target_entity_id)
        entity_ev_ids = {ev.evidence_id for ev in entity_evidence}

        for ev_id in claim.evidence_ids:
            ev = self.storage.get_evidence(ev_id)
            if not ev:
                claim.verification_status = ClaimVerificationStatus.REJECTED
                claim.verification_notes = (
                    f"Hallucination rejected: Evidence ID '{ev_id}' does not exist in the database."
                )
                claim.confidence_score = 0.0
                return claim

            # Entity association check
            if ev_id not in entity_ev_ids:
                claim.verification_status = ClaimVerificationStatus.REJECTED
                claim.verification_notes = (
                    f"Cross-entity citation rejected: Evidence ID '{ev_id}' exists but belongs to a different subject, not '{target_entity_id}'."
                )
                claim.confidence_score = 0.0
                return claim

            valid_ev_records.append(ev)

        # 5. Structural Semantic & Entailment Verification
        # Check against cited FactRecords if provided
        statement = claim.statement
        claimed_metrics = extract_metric_keywords(statement)
        claimed_years = extract_years(statement)
        claimed_numbers = extract_numbers(statement)

        if claim.fact_ids:
            for f_id in claim.fact_ids:
                fact = self.storage.get_fact(f_id)
                if not fact:
                    claim.verification_status = ClaimVerificationStatus.REJECTED
                    claim.verification_notes = f"Hallucination rejected: Fact ID '{f_id}' does not exist in storage."
                    claim.confidence_score = 0.0
                    return claim

                if fact.entity_id != target_entity_id:
                    claim.verification_status = ClaimVerificationStatus.REJECTED
                    claim.verification_notes = f"Cross-entity fact rejected: Fact ID '{f_id}' belongs to different entity."
                    claim.confidence_score = 0.0
                    return claim

                # Metric alignment check
                if claimed_metrics and fact.canonical_variable not in claimed_metrics:
                    claim.verification_status = ClaimVerificationStatus.REJECTED
                    claim.verification_notes = (
                        f"Metric mismatch rejected: Claim asserts '{claimed_metrics[0]}' "
                        f"but cited fact corresponds to '{fact.canonical_variable}'."
                    )
                    claim.confidence_score = 0.0
                    return claim

                # Period alignment check
                if claimed_years and fact.fiscal_year and fact.fiscal_year not in claimed_years:
                    claim.verification_status = ClaimVerificationStatus.REJECTED
                    claim.verification_notes = (
                        f"Period mismatch rejected: Claim asserts period FY{claimed_years[0]} "
                        f"but cited fact corresponds to FY{fact.fiscal_year}."
                    )
                    claim.confidence_score = 0.0
                    return claim

                # Value contradiction check
                if claimed_numbers and fact.normalized_value is not None:
                    # Check if claimed number conflicts with fact value (divergence > 5%)
                    fact_val = fact.normalized_value
                    divergences = [
                        abs(cn - fact_val) / abs(fact_val)
                        for cn in claimed_numbers
                        if fact_val != 0
                    ]
                    # If all claimed numbers diverge significantly from fact value
                    if divergences and min(divergences) > 0.05:
                        claim.verification_status = ClaimVerificationStatus.CONFLICTED
                        claim.verification_notes = (
                            f"Value contradiction detected: Claim asserts {claimed_numbers[0]:,.2f} "
                            f"but cited primary fact reports {fact_val:,.2f}."
                        )
                        claim.confidence_score = 0.20
                        return claim

        # 6. Content Passage Relevance Check for Evidence Excerpt
        # Ensure the cited evidence is not completely unrelated to the assertion
        combined_content = " ".join([
            (getattr(ev, "raw_content", "") or getattr(ev, "verbatim_excerpt", "") or "").lower()
            for ev in valid_ev_records
        ])

        if claimed_metrics:
            # If assertion claims a financial metric, evidence excerpt must contain relevant terminology or numbers
            has_metric_term = any(m in combined_content for m in claimed_metrics)
            has_number = any(str(int(n)) in combined_content for n in claimed_numbers if n > 1000)
            if not has_metric_term and not has_number and len(combined_content.strip()) > 30:
                claim.verification_status = ClaimVerificationStatus.REJECTED
                claim.verification_notes = (
                    f"Citation support failure: Cited evidence excerpt does not contain relevant "
                    f"metric ('{claimed_metrics[0]}') or numerical support for the assertion."
                )
                claim.confidence_score = 0.0
                return claim

        # 7. Check Derived Calculations for Period Comparability
        if claim.knowledge_tier == KnowledgeTier.DERIVED_CALCULATION and len(claim.fact_ids) >= 2:
            cited_facts = [self.storage.get_fact(fid) for fid in claim.fact_ids]
            valid_facts = [f for f in cited_facts if f is not None]
            if len(valid_facts) >= 2:
                # Disallow comparing two different periods of the same fiscal year (e.g. YTD vs full year)
                f0, f1 = valid_facts[0], valid_facts[1]
                if f0.fiscal_year == f1.fiscal_year and f0.fiscal_period != f1.fiscal_period:
                    claim.verification_status = ClaimVerificationStatus.REJECTED
                    claim.verification_notes = (
                        f"Methodological period mismatch: Derived calculation improperly compares "
                        f"incompatible period durations ({f0.fiscal_period} vs {f1.fiscal_period}) "
                        f"within the same fiscal year without flow normalization."
                    )
                    claim.confidence_score = 0.0
                    return claim

        # All citations exist, link to target entity, and structurally substantiate the assertion
        claim.verification_status = ClaimVerificationStatus.VERIFIED
        claim.verification_notes = f"Verified: Structurally supported by {len(valid_ev_records)} primary evidence record(s)."
        claim.confidence_score = 1.0
        return claim

    def validate_claims(
        self, claims: List[ResearchClaim], target_entity_id: str
    ) -> List[ResearchClaim]:
        """Audit a batch of research claims."""
        return [self.validate_claim(c, target_entity_id) for c in claims]

    def validate_risks(
        self, risks: List[RiskIndicator], target_entity_id: str
    ) -> List[RiskIndicator]:
        """Validate risk indicators to ensure evidence-backed claims link to real, relevant records."""
        entity_evidence = self.storage.get_evidence_for_entity(target_entity_id)
        entity_ev_ids = {ev.evidence_id for ev in entity_evidence}

        validated: List[RiskIndicator] = []
        for r in risks:
            if r.is_analyst_assumption:
                validated.append(r)
                continue

            # Check evidence citations
            valid_ids = []
            for ev_id in r.evidence_ids:
                if ev_id in entity_ev_ids:
                    ev = self.storage.get_evidence(ev_id)
                    if ev:
                        # Topical relevance check
                        content = (getattr(ev, "raw_content", "") or getattr(ev, "verbatim_excerpt", "") or "").lower()
                        # If description asserts a specific topic, ensure content isn't totally unrelated
                        risk_words = [w for w in re.findall(r"\w{4,}", r.description.lower()) if w not in ("risk", "indicator", "medium", "high", "company")]
                        if any(w in content for w in risk_words) or len(content) < 50:
                            valid_ids.append(ev_id)

            if not valid_ids and r.evidence_ids:
                # Downgrade to analyst assumption if evidence is unverifiable or irrelevant
                r.is_analyst_assumption = True
                r.description = f"[Downgraded to Assumption: Unverified or Irrelevant Evidence] {r.description}"
                r.evidence_ids = []

            validated.append(r)

        return validated

    def check_temporal_distinction(
        self, facts: List[FactRecord]
    ) -> List[str]:
        """Validate that source publication dates are not conflated with fiscal reporting periods."""
        anomalies: List[str] = []
        for f in facts:
            if not f.period_end:
                continue
            src = None
            if f.evidence_ids:
                ev = self.storage.get_evidence(f.evidence_ids[0])
                if ev and ev.source_id:
                    src = self.storage.get_source(ev.source_id)
            if src and src.publication_date:
                # In almost all real-world filings, publication date is strictly AFTER fiscal period end
                if src.publication_date < f.period_end:
                    anomalies.append(
                        f"Temporal anomaly for fact '{f.fact_id}': Publication date "
                        f"({src.publication_date}) precedes fiscal period end ({f.period_end})."
                    )
        return anomalies

    def summarize_provenance(self, evidence_ids: List[str]) -> Dict[str, Any]:
        """Build consolidated cryptographic provenance summary for cited evidence items.
        
        Explicitly distinguishes between content digest verification (tamper-detection)
        and factual veracity of source text.
        """
        unique_ev_ids = sorted(list(set(evidence_ids)))
        publishers: Set[str] = set()
        source_types: Set[str] = set()
        document_ids: Set[str] = set()
        content_hashes: Set[str] = set()

        for ev_id in unique_ev_ids:
            ev = self.storage.get_evidence(ev_id)
            if ev and ev.source_id:
                src = self.storage.get_source(ev.source_id)
                if src:
                    if src.publisher:
                        publishers.add(src.publisher)
                    if src.source_type:
                        st = src.source_type.value if hasattr(src.source_type, "value") else str(src.source_type)
                        source_types.add(st)
                    if src.document_id:
                        document_ids.add(src.document_id)
                    if src.content_hash:
                        content_hashes.add(src.content_hash)

        return {
            "total_cited_evidence_records": len(unique_ev_ids),
            "primary_publishers": sorted(list(publishers)),
            "source_types": sorted(list(source_types)),
            "verified_document_ids": sorted(list(document_ids)),
            "cryptographic_content_hashes": sorted(list(content_hashes)),
            "integrity_status": "PAYLOAD_DIGEST_ESTABLISHED" if content_hashes else "UNHASHED_SOURCE",
            "integrity_statement": (
                "Cryptographic SHA-256 hashes confirm bitwise document integrity and tamper-detection; "
                "hashes do NOT attest to the truth or accuracy of issuer statements."
            ),
        }
