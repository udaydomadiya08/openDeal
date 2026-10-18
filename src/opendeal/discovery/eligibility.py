"""Deterministic eligibility evaluation engine for candidate screening."""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple
from opendeal.core.types import Currency, FiscalPeriodType
from opendeal.discovery.contracts import (
    CandidateCompany,
    EligibilityDecision,
    EligibilityOutcome,
    ExclusionReason,
    FinancialFilterCriterion,
    TargetCriteria,
)
from opendeal.facts.models import FactRecord

logger = logging.getLogger(__name__)


class EligibilityEvaluator:
    """Evaluates mandatory screening criteria against candidate facts and metadata.

    Guarantees:
    - 100% deterministic evaluation.
    - Missing data never silently passes as meeting a threshold or as zero.
    - Explicit distinction between ELIGIBLE, INELIGIBLE, and INSUFFICIENT_DATA.
    """

    def evaluate(
        self, candidate: CandidateCompany, criteria: TargetCriteria
    ) -> EligibilityDecision:
        passed_rules: List[str] = []
        exclusion_reasons: List[ExclusionReason] = []
        insufficient_data_reasons: List[ExclusionReason] = []

        # 1. Active Status Check
        if criteria.require_active:
            if not candidate.entity.is_active:
                exclusion_reasons.append(
                    ExclusionReason(
                        criterion_name="require_active",
                        outcome=EligibilityOutcome.INELIGIBLE,
                        message=f"Entity '{candidate.entity.legal_name}' is inactive, dissolved, or in liquidation.",
                        metric_name="is_active",
                        expected_threshold=True,
                        actual_value=candidate.entity.is_active,
                    )
                )
            else:
                passed_rules.append("require_active")

        # 2. Jurisdiction Criteria
        entity_jurisdiction = candidate.entity.jurisdiction.upper().strip()

        # Excluded jurisdictions
        if criteria.excluded_jurisdictions:
            is_excluded = any(
                ex.upper().strip() in entity_jurisdiction or entity_jurisdiction in ex.upper().strip()
                for ex in criteria.excluded_jurisdictions
            )
            if is_excluded:
                exclusion_reasons.append(
                    ExclusionReason(
                        criterion_name="excluded_jurisdictions",
                        outcome=EligibilityOutcome.INELIGIBLE,
                        message=f"Jurisdiction '{entity_jurisdiction}' is explicitly excluded.",
                        metric_name="jurisdiction",
                        expected_threshold=f"Not in {criteria.excluded_jurisdictions}",
                        actual_value=entity_jurisdiction,
                    )
                )
            else:
                passed_rules.append("excluded_jurisdictions")

        # Required jurisdictions
        if criteria.jurisdictions:
            matches_jurisdiction = any(
                req.upper().strip() in entity_jurisdiction or entity_jurisdiction in req.upper().strip()
                for req in criteria.jurisdictions
            )
            if not matches_jurisdiction:
                exclusion_reasons.append(
                    ExclusionReason(
                        criterion_name="jurisdictions",
                        outcome=EligibilityOutcome.INELIGIBLE,
                        message=f"Jurisdiction '{entity_jurisdiction}' does not match required {criteria.jurisdictions}.",
                        metric_name="jurisdiction",
                        expected_threshold=criteria.jurisdictions,
                        actual_value=entity_jurisdiction,
                    )
                )
            else:
                passed_rules.append("jurisdictions")

        # 3. Company Type Inclusion / Exclusion
        if criteria.excluded_company_types:
            if candidate.entity.entity_type in criteria.excluded_company_types:
                exclusion_reasons.append(
                    ExclusionReason(
                        criterion_name="excluded_company_types",
                        outcome=EligibilityOutcome.INELIGIBLE,
                        message=f"Entity type '{candidate.entity.entity_type.value}' is excluded.",
                        metric_name="entity_type",
                        expected_threshold=f"Not in {[t.value for t in criteria.excluded_company_types]}",
                        actual_value=candidate.entity.entity_type.value,
                    )
                )
            else:
                passed_rules.append("excluded_company_types")

        if criteria.company_types:
            if candidate.entity.entity_type not in criteria.company_types:
                exclusion_reasons.append(
                    ExclusionReason(
                        criterion_name="company_types",
                        outcome=EligibilityOutcome.INELIGIBLE,
                        message=f"Entity type '{candidate.entity.entity_type.value}' not in required {[t.value for t in criteria.company_types]}.",
                        metric_name="entity_type",
                        expected_threshold=[t.value for t in criteria.company_types],
                        actual_value=candidate.entity.entity_type.value,
                    )
                )
            else:
                passed_rules.append("company_types")

        # 4. SIC Codes
        if criteria.sic_codes:
            if not candidate.entity.sic_code:
                insufficient_data_reasons.append(
                    ExclusionReason(
                        criterion_name="sic_codes",
                        outcome=EligibilityOutcome.INSUFFICIENT_DATA,
                        message="Entity lacks a recorded SIC code.",
                        metric_name="sic_code",
                        expected_threshold=criteria.sic_codes,
                        actual_value=None,
                    )
                )
            elif candidate.entity.sic_code.strip() not in criteria.sic_codes:
                exclusion_reasons.append(
                    ExclusionReason(
                        criterion_name="sic_codes",
                        outcome=EligibilityOutcome.INELIGIBLE,
                        message=f"SIC code '{candidate.entity.sic_code}' does not match requested codes {criteria.sic_codes}.",
                        metric_name="sic_code",
                        expected_threshold=criteria.sic_codes,
                        actual_value=candidate.entity.sic_code,
                    )
                )
            else:
                passed_rules.append("sic_codes")

        # 5. Sectors / Industry Description
        if criteria.sectors:
            entity_desc = (candidate.entity.sic_description or "").upper()
            entity_sector = str(candidate.entity.metadata.get("sector", "")).upper()
            combined_ind = f"{entity_desc} {entity_sector}".strip()

            if not combined_ind:
                insufficient_data_reasons.append(
                    ExclusionReason(
                        criterion_name="sectors",
                        outcome=EligibilityOutcome.INSUFFICIENT_DATA,
                        message="Entity has no recorded industry, sector, or business description.",
                        metric_name="sector",
                        expected_threshold=criteria.sectors,
                        actual_value=None,
                    )
                )
            else:
                matches_sector = any(sec.upper() in combined_ind for sec in criteria.sectors)
                if not matches_sector:
                    exclusion_reasons.append(
                        ExclusionReason(
                            criterion_name="sectors",
                            outcome=EligibilityOutcome.INELIGIBLE,
                            message=f"Industry '{combined_ind}' does not match requested sectors {criteria.sectors}.",
                            metric_name="sector",
                            expected_threshold=criteria.sectors,
                            actual_value=combined_ind,
                        )
                    )
                else:
                    passed_rules.append("sectors")

        # 6. Keywords
        if criteria.keywords:
            text_corpus = (
                f"{candidate.entity.legal_name} "
                f"{' '.join(candidate.entity.aliases)} "
                f"{candidate.entity.sic_description or ''} "
                f"{candidate.entity.metadata.get('description', '')}"
            ).upper()

            matches_kw = any(kw.upper() in text_corpus for kw in criteria.keywords)
            if not matches_kw:
                exclusion_reasons.append(
                    ExclusionReason(
                        criterion_name="keywords",
                        outcome=EligibilityOutcome.INELIGIBLE,
                        message=f"Entity does not match any of the required keywords: {criteria.keywords}.",
                        metric_name="keywords",
                        expected_threshold=criteria.keywords,
                        actual_value=None,
                    )
                )
            else:
                passed_rules.append("keywords")

        # 7. Minimum Revenue Shorthand Filter
        if criteria.min_revenue is not None or criteria.max_revenue is not None:
            self._evaluate_revenue_shorthand(
                candidate=candidate,
                criteria=criteria,
                passed_rules=passed_rules,
                exclusion_reasons=exclusion_reasons,
                insufficient_data_reasons=insufficient_data_reasons,
            )

        # 8. Detailed Financial Metric Filters
        for fin_filter in criteria.financial_filters:
            self._evaluate_financial_filter(
                candidate=candidate,
                filter_criterion=fin_filter,
                passed_rules=passed_rules,
                exclusion_reasons=exclusion_reasons,
                insufficient_data_reasons=insufficient_data_reasons,
            )

        # 9. Evidence Confidence Quality
        if criteria.min_confidence_score > 0.0:
            if not candidate.evidence and not candidate.facts:
                insufficient_data_reasons.append(
                    ExclusionReason(
                        criterion_name="evidence_quality",
                        outcome=EligibilityOutcome.INSUFFICIENT_DATA,
                        message="No evidence or facts available to verify data quality.",
                        metric_name="confidence_score",
                        expected_threshold=criteria.min_confidence_score,
                        actual_value=0.0,
                    )
                )
            else:
                confidences = [
                    e.confidence_score for e in candidate.evidence if e.confidence_score is not None
                ]
                if confidences:
                    avg_conf = sum(confidences) / len(confidences)
                    if avg_conf < criteria.min_confidence_score:
                        exclusion_reasons.append(
                            ExclusionReason(
                                criterion_name="evidence_quality",
                                outcome=EligibilityOutcome.INELIGIBLE,
                                message=f"Average evidence confidence {avg_conf:.2f} is below required {criteria.min_confidence_score:.2f}.",
                                metric_name="confidence_score",
                                expected_threshold=criteria.min_confidence_score,
                                actual_value=avg_conf,
                            )
                        )
                    else:
                        passed_rules.append("evidence_quality")
                else:
                    passed_rules.append("evidence_quality")

        # Synthesize Overall Determination
        if exclusion_reasons:
            overall_outcome = EligibilityOutcome.INELIGIBLE
            summary = (
                f"Candidate '{candidate.entity.legal_name}' is INELIGIBLE: "
                f"failed {len(exclusion_reasons)} mandatory criteria."
            )
        elif insufficient_data_reasons:
            overall_outcome = EligibilityOutcome.INSUFFICIENT_DATA
            summary = (
                f"Candidate '{candidate.entity.legal_name}' has INSUFFICIENT_DATA: "
                f"missing data for {len(insufficient_data_reasons)} criteria."
            )
        else:
            overall_outcome = EligibilityOutcome.ELIGIBLE
            summary = (
                f"Candidate '{candidate.entity.legal_name}' is ELIGIBLE: "
                f"passed all {len(passed_rules)} evaluated criteria."
            )

        return EligibilityDecision(
            outcome=overall_outcome,
            passed_criteria=passed_rules,
            exclusion_reasons=exclusion_reasons,
            insufficient_data_reasons=insufficient_data_reasons,
            summary=summary,
        )

    def _evaluate_revenue_shorthand(
        self,
        candidate: CandidateCompany,
        criteria: TargetCriteria,
        passed_rules: List[str],
        exclusion_reasons: List[ExclusionReason],
        insufficient_data_reasons: List[ExclusionReason],
    ) -> None:
        """Evaluate shorthand min_revenue / max_revenue criteria."""
        # Find revenue facts (canonical_variable = 'revenue')
        rev_facts = [
            f for f in candidate.facts if f.canonical_variable.lower() in ("revenue", "total_revenue")
        ]

        if not rev_facts:
            insufficient_data_reasons.append(
                ExclusionReason(
                    criterion_name="revenue",
                    outcome=EligibilityOutcome.INSUFFICIENT_DATA,
                    message="Missing reported revenue observation. Data cannot be silently assumed as zero.",
                    metric_name="revenue",
                    expected_threshold=f"min={criteria.min_revenue}, max={criteria.max_revenue}",
                    actual_value=None,
                )
            )
            return

        # Prefer most recent annual fact (ANNUAL)
        annual_facts = [f for f in rev_facts if f.fiscal_period == FiscalPeriodType.ANNUAL]
        target_facts = annual_facts if annual_facts else rev_facts
        target_facts.sort(key=lambda f: f.fiscal_year, reverse=True)
        latest_fact = target_facts[0]

        # Check currency match if required
        if criteria.revenue_currency and latest_fact.currency != criteria.revenue_currency:
            insufficient_data_reasons.append(
                ExclusionReason(
                    criterion_name="revenue_currency",
                    outcome=EligibilityOutcome.INSUFFICIENT_DATA,
                    message=(
                        f"Currency mismatch for revenue: fact reported in {latest_fact.currency.value}, "
                        f"criterion requires {criteria.revenue_currency.value}."
                    ),
                    metric_name="currency",
                    expected_threshold=criteria.revenue_currency.value,
                    actual_value=latest_fact.currency.value,
                    supporting_fact_ids=[latest_fact.fact_id],
                )
            )
            return

        value = latest_fact.normalized_value
        if value is None:
            insufficient_data_reasons.append(
                ExclusionReason(
                    criterion_name="revenue",
                    outcome=EligibilityOutcome.INSUFFICIENT_DATA,
                    message="Revenue fact has None normalized_value.",
                    metric_name="revenue",
                    actual_value=None,
                    supporting_fact_ids=[latest_fact.fact_id],
                )
            )
            return

        # Min revenue check
        if criteria.min_revenue is not None:
            if value < criteria.min_revenue:
                exclusion_reasons.append(
                    ExclusionReason(
                        criterion_name="min_revenue",
                        outcome=EligibilityOutcome.INELIGIBLE,
                        message=f"Revenue {value:,.2f} is below minimum required {criteria.min_revenue:,.2f}.",
                        metric_name="revenue",
                        expected_threshold=criteria.min_revenue,
                        actual_value=value,
                        supporting_fact_ids=[latest_fact.fact_id],
                        evidence_ids=latest_fact.evidence_ids,
                    )
                )
            else:
                passed_rules.append("min_revenue")

        # Max revenue check
        if criteria.max_revenue is not None:
            if value > criteria.max_revenue:
                exclusion_reasons.append(
                    ExclusionReason(
                        criterion_name="max_revenue",
                        outcome=EligibilityOutcome.INELIGIBLE,
                        message=f"Revenue {value:,.2f} exceeds maximum allowed {criteria.max_revenue:,.2f}.",
                        metric_name="revenue",
                        expected_threshold=criteria.max_revenue,
                        actual_value=value,
                        supporting_fact_ids=[latest_fact.fact_id],
                        evidence_ids=latest_fact.evidence_ids,
                    )
                )
            else:
                passed_rules.append("max_revenue")

    def _evaluate_financial_filter(
        self,
        candidate: CandidateCompany,
        filter_criterion: FinancialFilterCriterion,
        passed_rules: List[str],
        exclusion_reasons: List[ExclusionReason],
        insufficient_data_reasons: List[ExclusionReason],
    ) -> None:
        """Evaluate a specific financial metric threshold filter."""
        metric = filter_criterion.metric.lower().strip()
        matching_facts = [
            f for f in candidate.facts if f.canonical_variable.lower() == metric
        ]

        # Filter by fiscal year if specified
        if filter_criterion.fiscal_year is not None:
            matching_facts = [
                f for f in matching_facts if f.fiscal_year == filter_criterion.fiscal_year
            ]

        # Filter by fiscal period if specified
        if filter_criterion.fiscal_period is not None:
            matching_facts = [
                f for f in matching_facts if f.fiscal_period == filter_criterion.fiscal_period
            ]

        if not matching_facts:
            insufficient_data_reasons.append(
                ExclusionReason(
                    criterion_name=f"filter_{metric}",
                    outcome=EligibilityOutcome.INSUFFICIENT_DATA,
                    message=f"No verified fact recorded for financial metric '{metric}'.",
                    metric_name=metric,
                    expected_threshold=f"min={filter_criterion.min_value}, max={filter_criterion.max_value}",
                    actual_value=None,
                )
            )
            return

        # Sort by fiscal year DESC, fiscal period DESC
        matching_facts.sort(key=lambda f: (f.fiscal_year, f.fiscal_period.value), reverse=True)
        fact = matching_facts[0]

        # Check currency match if required
        if filter_criterion.currency is not None and filter_criterion.require_exact_currency:
            if fact.currency != filter_criterion.currency:
                insufficient_data_reasons.append(
                    ExclusionReason(
                        criterion_name=f"currency_{metric}",
                        outcome=EligibilityOutcome.INSUFFICIENT_DATA,
                        message=(
                            f"Currency mismatch for {metric}: fact reported in {fact.currency.value}, "
                            f"criterion specifies {filter_criterion.currency.value}."
                        ),
                        metric_name=metric,
                        expected_threshold=filter_criterion.currency.value,
                        actual_value=fact.currency.value,
                        supporting_fact_ids=[fact.fact_id],
                    )
                )
                return

        val = fact.normalized_value
        if val is None:
            insufficient_data_reasons.append(
                ExclusionReason(
                    criterion_name=f"filter_{metric}",
                    outcome=EligibilityOutcome.INSUFFICIENT_DATA,
                    message=f"Financial metric '{metric}' fact has None normalized_value.",
                    metric_name=metric,
                    actual_value=None,
                    supporting_fact_ids=[fact.fact_id],
                )
            )
            return

        passed = True
        if filter_criterion.min_value is not None:
            if val < filter_criterion.min_value:
                exclusion_reasons.append(
                    ExclusionReason(
                        criterion_name=f"min_{metric}",
                        outcome=EligibilityOutcome.INELIGIBLE,
                        message=f"{metric} {val:,.2f} is below minimum required {filter_criterion.min_value:,.2f}.",
                        metric_name=metric,
                        expected_threshold=filter_criterion.min_value,
                        actual_value=val,
                        supporting_fact_ids=[fact.fact_id],
                        evidence_ids=fact.evidence_ids,
                    )
                )
                passed = False

        if filter_criterion.max_value is not None:
            if val > filter_criterion.max_value:
                exclusion_reasons.append(
                    ExclusionReason(
                        criterion_name=f"max_{metric}",
                        outcome=EligibilityOutcome.INELIGIBLE,
                        message=f"{metric} {val:,.2f} exceeds maximum allowed {filter_criterion.max_value:,.2f}.",
                        metric_name=metric,
                        expected_threshold=filter_criterion.max_value,
                        actual_value=val,
                        supporting_fact_ids=[fact.fact_id],
                        evidence_ids=fact.evidence_ids,
                    )
                )
                passed = False

        if passed:
            passed_rules.append(f"filter_{metric}")
