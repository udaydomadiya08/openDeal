"""Deterministic, explainable target scoring engine."""

from __future__ import annotations

import math
from typing import Dict, List, Optional
from opendeal.discovery.contracts import (
    CandidateCompany,
    ScoringFactorBreakdown,
    TargetCriteria,
    TargetScoringBreakdown,
)
from opendeal.facts.models import FactRecord


CORE_CANONICAL_VARIABLES = [
    "revenue",
    "operating_income",
    "net_income",
    "total_assets",
    "cash_and_equivalents",
    "total_liabilities",
]


class TargetScorer:
    """Computes explainable, deterministic acquisition target scores.

    Principles:
    - 100% deterministic arithmetic in Python.
    - Factor breakdown for every score component.
    - Missing data is never silently converted to zero.
    - Missing data handling is explicit and configurable.
    - Deterministic tie-breaking using entity identifiers.
    """

    def score(
        self, candidate: CandidateCompany, criteria: TargetCriteria
    ) -> TargetScoringBreakdown:
        weights = criteria.scoring_weights
        total_weight = sum(weights.values())
        if total_weight <= 0:
            norm_weights = {k: 1.0 / len(weights) for k in weights}
        else:
            norm_weights = {k: w / total_weight for k, w in weights.items()}

        factor_breakdowns: Dict[str, ScoringFactorBreakdown] = {}

        # 1. Financial Scale Factor
        scale_weight = norm_weights.get("scale", 0.0)
        scale_breakdown = self._score_scale(
            candidate=candidate,
            weight=scale_weight,
            penalty=criteria.missing_data_penalty,
        )
        factor_breakdowns["scale"] = scale_breakdown

        # 2. Profitability Margin Factor
        profit_weight = norm_weights.get("profitability", 0.0)
        profit_breakdown = self._score_profitability(
            candidate=candidate,
            weight=profit_weight,
            penalty=criteria.missing_data_penalty,
        )
        factor_breakdowns["profitability"] = profit_breakdown

        # 3. Strategic & Geographic Fit Factor
        fit_weight = norm_weights.get("fit", 0.0)
        fit_breakdown = self._score_fit(
            candidate=candidate,
            criteria=criteria,
            weight=fit_weight,
        )
        factor_breakdowns["fit"] = fit_breakdown

        # 4. Evidence Quality & Freshness Factor
        quality_weight = norm_weights.get("evidence_quality", 0.0)
        quality_breakdown = self._score_evidence_quality(
            candidate=candidate,
            weight=quality_weight,
        )
        factor_breakdowns["evidence_quality"] = quality_breakdown

        # 5. Financial Completeness Factor
        comp_weight = norm_weights.get("completeness", 0.0)
        comp_breakdown = self._score_completeness(
            candidate=candidate,
            weight=comp_weight,
        )
        factor_breakdowns["completeness"] = comp_breakdown

        # Compute overall score: 0.0 to 100.0
        overall_score = sum(fb.weighted_score for fb in factor_breakdowns.values()) * 100.0
        overall_score = round(max(0.0, min(100.0, overall_score)), 2)

        tie_breaker_key = f"{candidate.entity.entity_id}:{candidate.entity.legal_name}"

        return TargetScoringBreakdown(
            overall_score=overall_score,
            factor_breakdowns=factor_breakdowns,
            scoring_version=criteria.scoring_version,
            tie_breaker_key=tie_breaker_key,
        )

    def _score_scale(
        self, candidate: CandidateCompany, weight: float, penalty: float
    ) -> ScoringFactorBreakdown:
        rev_facts = [
            f for f in candidate.facts if f.canonical_variable.lower() in ("revenue", "total_revenue")
        ]
        if not rev_facts:
            norm_score = max(0.0, 0.5 * (1.0 - penalty))
            return ScoringFactorBreakdown(
                factor_name="scale",
                raw_value=None,
                normalized_score=round(norm_score, 4),
                weight=weight,
                weighted_score=round(norm_score * weight, 4),
                missing=True,
                missing_penalty=penalty,
                explanation="No revenue facts recorded; neutral baseline with missing penalty applied.",
            )

        rev_facts.sort(key=lambda f: f.fiscal_year, reverse=True)
        latest_rev = rev_facts[0].normalized_value

        if latest_rev is None or latest_rev <= 0:
            norm_score = max(0.0, 0.2 * (1.0 - penalty))
            return ScoringFactorBreakdown(
                factor_name="scale",
                raw_value=latest_rev,
                normalized_score=round(norm_score, 4),
                weight=weight,
                weighted_score=round(norm_score * weight, 4),
                missing=False,
                missing_penalty=0.0,
                explanation=f"Revenue is non-positive ({latest_rev}); assigned minimum scale score.",
            )

        # Log scale benchmark: $1M (10^6) -> 0.0, $1B (10^9) -> 1.0
        log_val = math.log10(latest_rev)
        normalized = (log_val - 6.0) / (9.0 - 6.0)
        norm_score = max(0.0, min(1.0, normalized))

        return ScoringFactorBreakdown(
            factor_name="scale",
            raw_value=latest_rev,
            normalized_score=round(norm_score, 4),
            weight=weight,
            weighted_score=round(norm_score * weight, 4),
            missing=False,
            missing_penalty=0.0,
            explanation=f"Revenue of ${latest_rev:,.0f} normalized on logarithmic scale [1M - 1B].",
        )

    def _score_profitability(
        self, candidate: CandidateCompany, weight: float, penalty: float
    ) -> ScoringFactorBreakdown:
        rev_facts = [
            f for f in candidate.facts if f.canonical_variable.lower() in ("revenue", "total_revenue")
        ]
        op_facts = [
            f for f in candidate.facts if f.canonical_variable.lower() in ("operating_income", "ebitda", "operating_profit")
        ]

        if not rev_facts or not op_facts:
            norm_score = max(0.0, 0.5 * (1.0 - penalty))
            return ScoringFactorBreakdown(
                factor_name="profitability",
                raw_value=None,
                normalized_score=round(norm_score, 4),
                weight=weight,
                weighted_score=round(norm_score * weight, 4),
                missing=True,
                missing_penalty=penalty,
                explanation="Missing operating income or revenue data; neutral baseline with penalty.",
            )

        rev_facts.sort(key=lambda f: f.fiscal_year, reverse=True)
        op_facts.sort(key=lambda f: f.fiscal_year, reverse=True)

        rev = rev_facts[0].normalized_value
        op = op_facts[0].normalized_value

        if rev is None or op is None or rev <= 0:
            norm_score = 0.2
            return ScoringFactorBreakdown(
                factor_name="profitability",
                raw_value=None,
                normalized_score=round(norm_score, 4),
                weight=weight,
                weighted_score=round(norm_score * weight, 4),
                missing=False,
                missing_penalty=0.0,
                explanation="Non-positive revenue prevents margin calculation.",
            )

        margin = op / rev
        # Map margin: < -0.10 -> 0.1, 0.0 -> 0.4, 0.15 (15%) -> 0.8, >= 0.30 (30%) -> 1.0
        if margin <= -0.10:
            norm_score = 0.1
        elif margin <= 0.0:
            norm_score = 0.1 + (margin + 0.10) / 0.10 * 0.3  # 0.1 to 0.4
        elif margin <= 0.20:
            norm_score = 0.4 + (margin / 0.20) * 0.4  # 0.4 to 0.8
        else:
            norm_score = 0.8 + min(0.2, (margin - 0.20) / 0.10 * 0.2)  # 0.8 to 1.0

        norm_score = max(0.0, min(1.0, norm_score))

        return ScoringFactorBreakdown(
            factor_name="profitability",
            raw_value=round(margin, 4),
            normalized_score=round(norm_score, 4),
            weight=weight,
            weighted_score=round(norm_score * weight, 4),
            missing=False,
            missing_penalty=0.0,
            explanation=f"Operating margin of {margin:.1%} evaluated against standard commercial brackets.",
        )

    def _score_fit(
        self, candidate: CandidateCompany, criteria: TargetCriteria, weight: float
    ) -> ScoringFactorBreakdown:
        score_components: List[float] = []

        # Geographic fit component (0.0 to 0.5)
        entity_jur = candidate.entity.jurisdiction.upper().strip()
        if criteria.jurisdictions:
            geo_match = any(
                req.upper().strip() in entity_jur or entity_jur in req.upper().strip()
                for req in criteria.jurisdictions
            )
            score_components.append(0.5 if geo_match else 0.0)
        else:
            score_components.append(0.5)  # No geo preference specified

        # Industry / Sector fit component (0.0 to 0.5)
        combined_text = (
            f"{candidate.entity.legal_name} "
            f"{' '.join(candidate.entity.aliases)} "
            f"{candidate.entity.sic_description or ''} "
            f"{candidate.entity.metadata.get('sector', '')}"
        ).upper()

        if criteria.sectors or criteria.keywords or criteria.sic_codes:
            matched_industry = False
            if criteria.sic_codes and candidate.entity.sic_code in criteria.sic_codes:
                matched_industry = True
            elif criteria.sectors and any(s.upper() in combined_text for s in criteria.sectors):
                matched_industry = True
            elif criteria.keywords and any(k.upper() in combined_text for k in criteria.keywords):
                matched_industry = True

            score_components.append(0.5 if matched_industry else 0.2)
        else:
            score_components.append(0.5)  # No industry preference specified

        total_fit = sum(score_components)
        norm_score = max(0.0, min(1.0, total_fit))

        return ScoringFactorBreakdown(
            factor_name="fit",
            raw_value=round(norm_score, 4),
            normalized_score=round(norm_score, 4),
            weight=weight,
            weighted_score=round(norm_score * weight, 4),
            missing=False,
            missing_penalty=0.0,
            explanation=f"Jurisdiction '{entity_jur}' and business focus aligned with target preferences.",
        )

    def _score_evidence_quality(
        self, candidate: CandidateCompany, weight: float
    ) -> ScoringFactorBreakdown:
        if not candidate.evidence and not candidate.facts:
            norm_score = 0.0
            return ScoringFactorBreakdown(
                factor_name="evidence_quality",
                raw_value=0.0,
                normalized_score=0.0,
                weight=weight,
                weighted_score=0.0,
                missing=True,
                missing_penalty=1.0,
                explanation="No evidence or facts available to assess provenance quality.",
            )

        confidences = [e.confidence_score for e in candidate.evidence if e.confidence_score is not None]
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.80

        # Recency bonus: check latest fact year
        fact_years = [f.fiscal_year for f in candidate.facts if f.fiscal_year]
        recency_factor = 1.0
        if fact_years:
            latest_year = max(fact_years)
            if latest_year >= 2023:
                recency_factor = 1.0
            elif latest_year >= 2021:
                recency_factor = 0.85
            else:
                recency_factor = 0.70

        norm_score = max(0.0, min(1.0, avg_conf * recency_factor))

        return ScoringFactorBreakdown(
            factor_name="evidence_quality",
            raw_value=round(avg_conf, 4),
            normalized_score=round(norm_score, 4),
            weight=weight,
            weighted_score=round(norm_score * weight, 4),
            missing=False,
            missing_penalty=0.0,
            explanation=f"Average evidence confidence {avg_conf:.2f} scaled by data recency factor {recency_factor:.2f}.",
        )

    def _score_completeness(
        self, candidate: CandidateCompany, weight: float
    ) -> ScoringFactorBreakdown:
        found_vars = {
            f.canonical_variable.lower().strip() for f in candidate.facts
        }
        present_count = sum(1 for var in CORE_CANONICAL_VARIABLES if var in found_vars)
        ratio = present_count / len(CORE_CANONICAL_VARIABLES)
        norm_score = max(0.0, min(1.0, ratio))

        return ScoringFactorBreakdown(
            factor_name="completeness",
            raw_value=round(ratio, 4),
            normalized_score=round(norm_score, 4),
            weight=weight,
            weighted_score=round(norm_score * weight, 4),
            missing=False,
            missing_penalty=0.0,
            explanation=f"Recorded {present_count}/{len(CORE_CANONICAL_VARIABLES)} core financial metrics.",
        )
