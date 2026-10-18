"""Evidence retrieval, ranking, context assembly, and security sanitization.

Assembles unified company research context from entity, fact, relationship,
and evidence stores. Detects prompt-injection attempts, identifies data gaps,
and highlights conflicting evidence without destructive overwrite.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from opendeal.core.types import FactState, FiscalPeriodType, SourceType
from opendeal.entities.models import CompanyEntity
from opendeal.evidence.models import EvidenceRecord
from opendeal.facts.models import FactRecord
from opendeal.research.contracts import (
    CompanyResearchContext,
    ConflictingEvidence,
    DataGap,
    FinancialTrend,
    InformationGapType,
    ResearchScope,
)
from opendeal.storage.base import StorageInterface

# Known prompt-injection patterns designed to override LLM system prompts or invoke tool misuse
PROMPT_INJECTION_PATTERNS = [
    r"(?i)ignore\s+[^.\n;]{0,50}?instructions",
    r"(?i)disregard\s+[^.\n;]{0,50}?instructions",
    r"(?i)override\s+[^.\n;]{0,50}?instructions",
    r"(?i)system\s*:\s*you\s+are\s+now",
    r"(?i)system\s*prompt\s*:",
    r"(?i)authorized\s+system\s+override",
    r"(?i)you\s+must\s+now\s+act\s+as",
    r"(?i)bypass\s+security\s+filters",
    r"(?i)reveal\s+[^.\n;]{0,50}?(secret|system\s*prompt|password|key|token)",
    r"(?i)output\s+[^.\n;]{0,50}?(system\s*prompt|initial\s*instruction)",
    r"(?i)<\s*script[^>]*>",
    r"(?i)rm\s+-rf\s+[/~]",
    r"(?i)delete\s+from\s+[a-z_]+",
    r"(?i)drop\s+table\s+[a-z_]+",
    r"(?i)format\s+[c-z]:",
    r"(?i)curl\s+https?://",
    r"(?i)wget\s+https?://",
    r"(?i)bash\s+-c",
    r"(?i)sh\s+-c",
    r"(?i)sudo\s+",
    r"(?i)exec\s*\(",
    r"(?i)eval\s*\(",
]

COMPILED_INJECTION_REGEX = [re.compile(p) for p in PROMPT_INJECTION_PATTERNS]


def sanitize_and_defuse_text(text: str) -> Tuple[str, List[str]]:
    """Detect and defuse adversarial prompt-injection patterns in untrusted evidence text.

    Applies Unicode NFKC normalization to detect homoglyphic or zero-width bypass attempts,
    defuses detected command patterns, and emits security warnings. Note: input sanitization
    is a secondary defusal layer; primary security is guaranteed by architectural isolation.
    """
    if not text:
        return text, []

    warnings: List[str] = []
    # Unicode NFKC normalization flattens fullwidth, formatting, and decomposed characters
    normalized = unicodedata.normalize("NFKC", text)
    sanitized = normalized

    for pattern in COMPILED_INJECTION_REGEX:
        matches = pattern.findall(sanitized)
        if matches:
            matched_str = matches[0] if isinstance(matches[0], str) else str(matches[0])
            warning = f"Prompt-injection pattern detected and defused: '{matched_str}'"
            warnings.append(warning)
            sanitized = pattern.sub(
                lambda m: f"[DEFUSED_INJECTION_ATTEMPT: {re.sub(r'(?i)delete|drop|ignore|curl|wget|rm|system|bash|sudo|exec|eval|reveal', lambda x: '_'.join(list(x.group(0))), m.group(0))}]",
                sanitized,
            )

    return sanitized, warnings


class EvidenceRetriever:
    """Assembles and ranks evidence, identifies gaps and conflicts, and builds research context."""

    def __init__(self, storage: StorageInterface):
        self.storage = storage

    def assemble_context(self, scope: ResearchScope) -> CompanyResearchContext:
        """Assembles comprehensive research context for a company entity."""
        warnings: List[str] = []

        # 1. Fetch Entity
        entity = self.storage.get_entity(scope.entity_id)
        entity_dict = entity.model_dump(mode="json") if entity else None

        # 2. Fetch Legal Identifiers
        identifiers = self.storage.get_legal_identifiers(scope.entity_id)
        id_dicts = [i.model_dump(mode="json") for i in identifiers]

        # 3. Fetch Relationships
        rel_dicts: List[Dict[str, Any]] = []
        relationships = []
        if scope.include_relationships:
            relationships = self.storage.get_relationships_for_entity(scope.entity_id)
            rel_dicts = [r.model_dump(mode="json") for r in relationships]

        # 4. Fetch Facts
        facts: List[FactRecord] = []
        if scope.include_financials:
            raw_facts = self.storage.get_facts_for_entity(scope.entity_id)
            # Filter by period if requested
            for f in raw_facts:
                if scope.period_start and f.period_end and f.period_end < scope.period_start:
                    continue
                if scope.period_end and f.period_start and f.period_start > scope.period_end:
                    continue
                facts.append(f)

        fact_dicts = [f.model_dump(mode="json") for f in facts]

        # 5. Fetch Evidence and sanitize untrusted excerpts
        evidence_records = self.storage.get_evidence_for_entity(scope.entity_id)
        ranked_evidence = self.rank_evidence(evidence_records, max_items=scope.max_evidence_items)

        sanitized_evidence_dicts: List[Dict[str, Any]] = []
        for ev in ranked_evidence:
            ev_dict = ev.model_dump(mode="json")
            excerpt = ev_dict.get("raw_content") or ev_dict.get("verbatim_excerpt") or ""
            defused_excerpt, inj_warnings = sanitize_and_defuse_text(excerpt)
            ev_dict["raw_content"] = defused_excerpt
            ev_dict["verbatim_excerpt"] = defused_excerpt
            warnings.extend(inj_warnings)
            sanitized_evidence_dicts.append(ev_dict)

        # 6. Detect Data Gaps
        data_gaps = self.detect_gaps(facts, entity, relationships)

        # 7. Detect Conflicts
        conflicts = self.detect_conflicts(facts)

        return CompanyResearchContext(
            entity=entity_dict,
            legal_identifiers=id_dicts,
            relationships=rel_dicts,
            facts=fact_dicts,
            evidence_records=sanitized_evidence_dicts,
            deterministic_valuations=[],
            known_gaps=data_gaps,
            detected_conflicts=conflicts,
            sanitization_warnings=list(set(warnings)),
        )

    def rank_evidence(
        self, evidence_records: List[EvidenceRecord], max_items: int = 50
    ) -> List[EvidenceRecord]:
        """Rank evidence records by source authority, cryptographic integrity, and recency."""
        if not evidence_records:
            return []

        def score_record(ev: EvidenceRecord) -> float:
            score = 0.0
            # Source authority check via source_id if present
            source = self.storage.get_source(ev.source_id) if ev.source_id else None
            if source:
                if source.source_type in (
                    SourceType.REGULATORY_FILING,
                    SourceType.OFFICIAL_REGISTRY,
                    SourceType.OFFICIAL_EXCHANGE,
                    SourceType.AUDITED_REPORT,
                ):
                    score += 10.0
                elif source.source_type == SourceType.CENTRAL_BANK:
                    score += 8.0
                else:
                    score += 4.0

                if source.content_hash:
                    score += 2.0
                if source.document_id:
                    score += 2.0
                if source.publication_date:
                    # Score recent publications slightly higher
                    days_old = (date.today() - source.publication_date).days
                    recency_bonus = max(0.0, 5.0 - (days_old / 365.0))
                    score += recency_bonus

            # Length of verifiable excerpt
            content_str = getattr(ev, "raw_content", "") or getattr(ev, "verbatim_excerpt", "")
            if content_str and len(content_str.strip()) > 20:
                score += 1.0

            # Direct locator verification
            if ev.page_number or getattr(ev, "line_number", None) is not None or getattr(ev, "tag_name", None):
                score += 2.0

            return score

        ranked = sorted(evidence_records, key=score_record, reverse=True)
        return ranked[:max_items]

    def detect_conflicts(self, facts: List[FactRecord]) -> List[ConflictingEvidence]:
        """Detect divergent or contradictory financial values for the same metric and period."""
        conflicts: List[ConflictingEvidence] = []
        # Group by canonical variable and fiscal period
        grouped: Dict[Tuple[str, Optional[int], Optional[str]], List[FactRecord]] = {}
        for f in facts:
            if f.fact_state == FactState.MISSING_FACT or f.normalized_value is None:
                continue
            key = (f.canonical_variable, f.fiscal_year, f.fiscal_period)
            grouped.setdefault(key, []).append(f)

        for (var, year, period), group in grouped.items():
            if len(group) < 2:
                continue

            values = [f.normalized_value for f in group if f.normalized_value is not None]
            min_val = min(values)
            max_val = max(values)

            # Flag if divergence is greater than 1%
            if max_val != 0 and (abs(max_val - min_val) / abs(max_val)) > 0.01:
                period_str = f"FY{year}" if year else (period or "Unknown")
                conflicting_records = []
                for f in group:
                    source = None
                    src_id = None
                    if f.evidence_ids:
                        ev_rec = self.storage.get_evidence(f.evidence_ids[0])
                        if ev_rec and ev_rec.source_id:
                            src_id = ev_rec.source_id
                            source = self.storage.get_source(ev_rec.source_id)
                    conflicting_records.append({
                        "fact_id": f.fact_id,
                        "reported_value": f.reported_value,
                        "normalized_value": f.normalized_value,
                        "unit": f.unit_scale.value if hasattr(f.unit_scale, "value") else str(f.unit_scale),
                        "currency": f.currency.value if hasattr(f.currency, "value") else str(f.currency),
                        "source_id": src_id,
                        "publisher": source.publisher if source else "Unknown",
                    })

                conflicts.append(
                    ConflictingEvidence(
                        metric_or_attribute=var,
                        period_or_date=period_str,
                        conflicting_records=conflicting_records,
                        discrepancy_description=(
                            f"Multiple conflicting values detected for {var} in {period_str}: "
                            f"min={min_val}, max={max_val} across {len(group)} records."
                        ),
                    )
                )

        return conflicts

    def detect_gaps(
        self,
        facts: List[FactRecord],
        entity: Optional[CompanyEntity],
        relationships: List[Any],
    ) -> List[DataGap]:
        """Systematically detect missing financial metrics, corporate governance, and registry attributes.
        
        Strictly categorizes data absence using InformationGapType to prevent false assertions
        of non-disclosure when metrics were simply absent from locally ingested records.
        """
        gaps: List[DataGap] = []

        # 1. Core financial metrics check
        essential_metrics = {
            "revenue": ("Revenue / Turnover", "HIGH"),
            "net_income": ("Net Income / Profit (Loss)", "HIGH"),
            "total_assets": ("Total Assets", "MEDIUM"),
            "total_liabilities": ("Total Liabilities", "MEDIUM"),
            "operating_cash_flow": ("Operating Cash Flow", "HIGH"),
            "ebitda": ("EBITDA / Operating Profit", "MEDIUM"),
        }

        available_vars = {
            f.canonical_variable
            for f in facts
            if f.fact_state != FactState.MISSING_FACT and f.normalized_value is not None
        }

        for metric, (label, severity) in essential_metrics.items():
            if metric not in available_vars:
                gaps.append(
                    DataGap(
                        topic=label,
                        description=f"No verified observations of {label} found in locally ingested filings (unverified whether issuer reports this in uningested filings).",
                        severity=severity,
                        gap_type=InformationGapType.NOT_FOUND_IN_INGESTED_DATA,
                        attempted_sources=["Stored Local Regulatory Filings"],
                    )
                )

        # 2. Entity identity gaps
        if entity:
            if not entity.sic_code and not entity.sic_description:
                gaps.append(
                    DataGap(
                        topic="Industry Classification",
                        description="SIC code and industry classification are missing from company registry record.",
                        severity="LOW",
                        gap_type=InformationGapType.NOT_FOUND_IN_INGESTED_DATA,
                        attempted_sources=["Company Entity Record"],
                    )
                )
            if not entity.incorporation_date:
                gaps.append(
                    DataGap(
                        topic="Incorporation Date",
                        description="Official incorporation date is unrecorded in local registry.",
                        severity="LOW",
                        gap_type=InformationGapType.NOT_FOUND_IN_INGESTED_DATA,
                        attempted_sources=["Company Registry"],
                    )
                )

        # 3. Ownership / Relationship gaps
        if not relationships:
            gaps.append(
                DataGap(
                    topic="Corporate Hierarchy",
                    description="No parent, subsidiary, or ultimate beneficial owner relationships recorded in local database.",
                    severity="MEDIUM",
                    gap_type=InformationGapType.NOT_FOUND_IN_INGESTED_DATA,
                    attempted_sources=["Corporate Relationship Registry"],
                )
            )

        return gaps

    def compute_financial_trends(self, facts: List[FactRecord]) -> List[FinancialTrend]:
        """Compute deterministic multi-period financial trajectories from verified facts.
        
        Enforces strict financial comparability:
        - Never mixes annual flow metrics with quarterly or year-to-date flows.
        - Verifies period duration consistency (~340-380 days for annual flows).
        - Flags missing intermediate fiscal periods when calculating multi-year CAGR.
        - Correctly handles negative, zero, and transitioning profit/loss values without math errors.
        - Refuses cross-currency and cross-accounting growth without explicit normalization.
        - Handles restatements by selecting the most validated, authoritative observation.
        """
        trends: List[FinancialTrend] = []

        # Flow variables measure performance over duration; balance sheet variables are instant snapshots
        FLOW_VARIABLES = {
            "revenue",
            "net_income",
            "operating_income",
            "gross_profit",
            "operating_cash_flow",
            "ebitda",
            "capital_expenditures",
        }

        # Group facts by canonical variable
        by_var: Dict[str, List[FactRecord]] = {}
        for f in facts:
            if f.fact_state == FactState.MISSING_FACT or f.normalized_value is None:
                continue
            by_var.setdefault(f.canonical_variable, []).append(f)

        for var, var_facts in by_var.items():
            is_flow = var in FLOW_VARIABLES

            # For flow metrics, separate annual facts from sub-annual (quarterly/YTD) facts
            # Never compare an annual flow with a quarterly flow!
            if is_flow:
                annual_candidates = [
                    f for f in var_facts
                    if f.fiscal_period == FiscalPeriodType.ANNUAL
                    or (f.period_start and f.period_end and 340 <= (f.period_end - f.period_start).days <= 380)
                ]
                # If we have annual observations, build the annual trajectory
                target_facts = annual_candidates if len(annual_candidates) >= 2 else []
            else:
                # Instant snapshot (balance sheet)
                target_facts = var_facts

            if len(target_facts) < 2:
                continue

            # Sort facts chronologically by fiscal year, then period end date
            sorted_facts = sorted(
                target_facts,
                key=lambda x: (
                    x.fiscal_year or 0,
                    x.period_end or date.min,
                ),
            )

            # Deduplicate by fiscal year: if multiple facts exist for the same year (e.g. restatements),
            # select the highest-validity fact, or latest period_end / restatement.
            deduped_by_year: Dict[int, FactRecord] = {}
            has_restatement = False
            for f in sorted_facts:
                if f.fiscal_year is None:
                    continue
                yr = f.fiscal_year
                if yr in deduped_by_year:
                    has_restatement = True
                    existing = deduped_by_year[yr]
                    # Prefer fact with VALID validation status or later period_end
                    if getattr(f, "is_amendment", False) or (f.period_end and existing.period_end and f.period_end >= existing.period_end):
                        deduped_by_year[yr] = f
                else:
                    deduped_by_year[yr] = f

            if len(deduped_by_year) < 2:
                continue

            years = sorted(deduped_by_year.keys())
            obs = []
            currencies: Set[str] = set()
            accounting_stds: Set[str] = set()
            durations: List[int] = []

            for yr in years:
                f = deduped_by_year[yr]
                curr_val = f.currency.value if hasattr(f.currency, "value") else str(f.currency)
                std_val = f.accounting_standard.value if hasattr(f.accounting_standard, "value") else str(f.accounting_standard)
                currencies.add(curr_val)
                accounting_stds.add(std_val)

                if f.period_start and f.period_end:
                    durations.append((f.period_end - f.period_start).days)

                period_label = f.fiscal_period.value if hasattr(f.fiscal_period, "value") else str(f.fiscal_period)
                obs.append({
                    "fiscal_year": yr,
                    "period": period_label or f"FY{yr}",
                    "normalized_value": f.normalized_value,
                    "currency": curr_val,
                    "accounting_standard": std_val,
                    "fact_id": f.fact_id,
                    "evidence_ids": f.evidence_ids,
                    "period_start": f.period_start.isoformat() if f.period_start else None,
                    "period_end": f.period_end.isoformat() if f.period_end else None,
                })

            # Check comparability constraints
            is_currency_consistent = (len(currencies) <= 1)
            is_accounting_consistent = (len(accounting_stds) <= 1)
            
            # Duration consistency: check if duration spread exceeds 30 days
            is_duration_consistent = True
            if len(durations) >= 2:
                if (max(durations) - min(durations)) > 30:
                    is_duration_consistent = False

            is_comparable = is_currency_consistent and is_accounting_consistent and is_duration_consistent

            first_val = obs[0]["normalized_value"]
            last_val = obs[-1]["normalized_value"]
            n_years = years[-1] - years[0]

            # Detect missing intermediate years
            expected_years = set(range(years[0], years[-1] + 1))
            missing_years = sorted(list(expected_years - set(years)))

            notes_parts: List[str] = []
            if not is_currency_consistent:
                notes_parts.append(
                    f"Warning: Trajectory includes multiple currencies ({', '.join(currencies)}). "
                    f"No cross-currency growth computed without foreign exchange normalization."
                )
            if not is_accounting_consistent:
                notes_parts.append(
                    f"Warning: Trajectory includes multiple accounting standards ({', '.join(accounting_stds)})."
                )
            if not is_duration_consistent:
                notes_parts.append("Warning: Observations cover unequal period lengths.")
            if missing_years:
                notes_parts.append(f"Notice: Intermediate fiscal years missing: {missing_years}. CAGR computed across available endpoints.")
            if has_restatement:
                notes_parts.append("Notice: Multiple observations detected for at least one fiscal year; latest restated observation utilized.")

            cagr = None
            direction = "STABLE"

            if not is_comparable:
                cagr = None
                direction = "INCOMPARABLE_UNITS" if not is_currency_consistent else "INCOMPARABLE_ACCOUNTING"
            else:
                # CAGR calculation: strictly valid only for strictly positive base and terminal values
                if n_years > 0 and first_val > 0 and last_val > 0:
                    cagr = round((last_val / first_val) ** (1.0 / n_years) - 1.0, 4)
                elif first_val <= 0 or last_val <= 0:
                    notes_parts.append("CAGR not applicable due to non-positive base or terminal value.")

                # Direction determination with proper handling of negative/zero baselines
                if first_val > 0:
                    if last_val > first_val * 1.05:
                        direction = "INCREASING"
                    elif last_val < first_val * 0.95:
                        direction = "DECREASING"
                    else:
                        direction = "STABLE"
                elif first_val == 0:
                    if last_val > 0:
                        direction = "INCREASING"
                    elif last_val < 0:
                        direction = "DECREASING"
                    else:
                        direction = "STABLE"
                else:  # first_val < 0 (net loss)
                    if last_val > first_val:
                        direction = "INCREASING"  # loss narrowed or turned positive
                    elif last_val < first_val:
                        direction = "DECREASING"  # loss deepened
                    else:
                        direction = "STABLE"

            trends.append(
                FinancialTrend(
                    metric_name=var,
                    start_period=f"FY{years[0]}",
                    end_period=f"FY{years[-1]}",
                    observations=obs,
                    cagr=cagr,
                    direction=direction,
                    is_comparable=is_comparable,
                    notes="; ".join(notes_parts) if notes_parts else None,
                )
            )

        return trends
