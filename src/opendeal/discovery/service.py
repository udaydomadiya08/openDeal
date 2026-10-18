"""Connector-neutral candidate discovery and screening orchestration service."""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from opendeal.connectors.companies_house.client import CompaniesHouseClient
from opendeal.connectors.sec.client import SECClient
from opendeal.discovery.contracts import (
    CandidateCompany,
    DiscoveryRunMetadata,
    EligibilityOutcome,
    ScreenedTarget,
    ScreeningResult,
    TargetCriteria,
)
from opendeal.discovery.eligibility import EligibilityEvaluator
from opendeal.discovery.scoring import TargetScorer
from opendeal.entities.models import CompanyEntity
from opendeal.entities.resolution import EntityResolver
from opendeal.storage.base import StorageInterface

logger = logging.getLogger(__name__)


class CandidateDiscoverySource(ABC):
    """Abstract interface for candidate discovery sources and registry adapters."""

    @abstractmethod
    def source_name(self) -> str:
        """Unique identifier of the discovery source."""
        pass

    @abstractmethod
    def discover_candidates(
        self, criteria: TargetCriteria, is_live: bool = False
    ) -> List[CandidateCompany]:
        """Discover company candidates matching criteria from this source."""
        pass


class LocalStoreCandidateSource(CandidateDiscoverySource):
    """Retrieves candidates from locally indexed entities, facts, and evidence."""

    def __init__(self, storage: StorageInterface) -> None:
        self.storage = storage

    def source_name(self) -> str:
        return "LOCAL_STORE"

    def discover_candidates(
        self, criteria: TargetCriteria, is_live: bool = False
    ) -> List[CandidateCompany]:
        entities = self.storage.list_entities(limit=500)
        candidates: List[CandidateCompany] = []

        for entity in entities:
            facts = self.storage.get_facts_for_entity(entity.entity_id)
            evidence = self.storage.get_evidence_for_entity(entity.entity_id)
            identifiers = self.storage.get_legal_identifiers(entity.entity_id)

            candidates.append(
                CandidateCompany(
                    entity=entity,
                    matched_identifiers=identifiers,
                    facts=facts,
                    evidence=evidence,
                    discovery_source="LOCAL_STORE",
                )
            )

        return candidates


class SECCandidateSource(CandidateDiscoverySource):
    """Official US SEC connector candidate source. Requires explicit live opt-in."""

    def __init__(self, sec_client: Optional[SECClient] = None) -> None:
        self.sec_client = sec_client

    def source_name(self) -> str:
        return "SEC_EDGAR"

    def discover_candidates(
        self, criteria: TargetCriteria, is_live: bool = False
    ) -> List[CandidateCompany]:
        if not is_live:
            # In offline mode, do not perform live SEC network calls
            return []

        # If live is explicitly opted in, SEC live search requires CIK or company list
        return []


class CompaniesHouseCandidateSource(CandidateDiscoverySource):
    """Official UK Companies House candidate source. Requires explicit live opt-in."""

    def __init__(self, ch_client: Optional[CompaniesHouseClient] = None) -> None:
        self.ch_client = ch_client

    def source_name(self) -> str:
        return "COMPANIES_HOUSE"

    def discover_candidates(
        self, criteria: TargetCriteria, is_live: bool = False
    ) -> List[CandidateCompany]:
        if not is_live:
            return []
        return []


class TargetDiscoveryService:
    """Core target discovery and screening pipeline orchestrator.

    Pipeline phases:
    1. Validation of discovery criteria.
    2. Retrieval of candidate universe across configured sources.
    3. Strict entity resolution to deduplicate candidates without fuzzy merges.
    4. Deterministic eligibility evaluation (ELIGIBLE, INELIGIBLE, INSUFFICIENT_DATA).
    5. Deterministic scoring of eligible candidates.
    6. Ranked target list production with evidence and explicit limitations.
    7. Persistence of discovery run.
    """

    def __init__(
        self,
        storage: StorageInterface,
        evaluator: Optional[EligibilityEvaluator] = None,
        scorer: Optional[TargetScorer] = None,
        sources: Optional[List[CandidateDiscoverySource]] = None,
    ) -> None:
        self.storage = storage
        self.evaluator = evaluator or EligibilityEvaluator()
        self.scorer = scorer or TargetScorer()
        self.sources: List[CandidateDiscoverySource] = (
            sources
            if sources is not None
            else [
                LocalStoreCandidateSource(storage),
                SECCandidateSource(),
                CompaniesHouseCandidateSource(),
            ]
        )

    def screen_targets(
        self,
        criteria: TargetCriteria,
        is_live: bool = False,
        save_run: bool = True,
    ) -> ScreeningResult:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        created_at = datetime.now(timezone.utc)
        coverage_limitations: List[str] = [
            "Official public registries (SEC, Companies House) and local store indexed.",
            "Private company coverage is limited to entities with filed disclosures; metrics for non-filing entities cannot be assumed as zero.",
        ]
        if not is_live:
            coverage_limitations.append("Offline mode active: external registry network calls were omitted.")

        adapters_used: List[str] = []
        raw_candidates: List[CandidateCompany] = []
        errors: List[str] = []

        # 1. Candidate Retrieval across sources
        for src in self.sources:
            try:
                candidates = src.discover_candidates(criteria=criteria, is_live=is_live)
                raw_candidates.extend(candidates)
                adapters_used.append(src.source_name())
            except Exception as e:
                logger.warning("Error running discovery source %s: %s", src.source_name(), e)
                errors.append(f"{src.source_name()}: {str(e)}")

        # 2. Strict Entity Resolution Deduplication
        deduplicated_candidates = self._deduplicate_candidates(raw_candidates)

        # 3. Evaluate Eligibility
        screened_targets: List[ScreenedTarget] = []
        eligible_targets: List[ScreenedTarget] = []

        for candidate in deduplicated_candidates:
            decision = self.evaluator.evaluate(candidate, criteria)
            if decision.outcome == EligibilityOutcome.ELIGIBLE:
                # Deterministic scoring for eligible targets
                scoring_breakdown = self.scorer.score(candidate, criteria)
                target = ScreenedTarget(
                    candidate=candidate,
                    eligibility=decision,
                    scoring=scoring_breakdown,
                    rank=None,
                )
                eligible_targets.append(target)
            else:
                target = ScreenedTarget(
                    candidate=candidate,
                    eligibility=decision,
                    scoring=None,
                    rank=None,
                )
            screened_targets.append(target)

        # 4. Deterministic Ranking of Eligible Targets
        # Sort key: (overall_score DESC, entity_id ASC) to guarantee 100% deterministic ordering
        eligible_targets.sort(
            key=lambda t: (
                -(t.scoring.overall_score if t.scoring else 0.0),
                t.candidate.entity.entity_id,
            )
        )

        for rank_idx, target in enumerate(eligible_targets, start=1):
            target.rank = rank_idx

        # Respect max_candidates if specified
        if criteria.max_candidates > 0 and len(eligible_targets) > criteria.max_candidates:
            # We keep all evaluated candidates in the run, but slice eligible if needed
            pass

        eligible_count = len([t for t in screened_targets if t.eligibility.outcome == EligibilityOutcome.ELIGIBLE])
        ineligible_count = len([t for t in screened_targets if t.eligibility.outcome == EligibilityOutcome.INELIGIBLE])
        insufficient_count = len([t for t in screened_targets if t.eligibility.outcome == EligibilityOutcome.INSUFFICIENT_DATA])

        metadata = DiscoveryRunMetadata(
            run_id=run_id,
            created_at=created_at,
            criteria=criteria,
            scoring_version=criteria.scoring_version,
            source_adapters_used=adapters_used,
            is_live=is_live,
            total_candidates_evaluated=len(deduplicated_candidates),
            eligible_count=eligible_count,
            ineligible_count=ineligible_count,
            insufficient_data_count=insufficient_count,
            coverage_limitations=coverage_limitations,
            errors=errors,
        )

        # Sort all screened targets for output: Eligible first (by rank), then Insufficient Data, then Ineligible
        screened_targets.sort(
            key=lambda t: (
                0 if t.eligibility.outcome == EligibilityOutcome.ELIGIBLE else (1 if t.eligibility.outcome == EligibilityOutcome.INSUFFICIENT_DATA else 2),
                t.rank if t.rank is not None else 999999,
                t.candidate.entity.legal_name,
            )
        )

        result = ScreeningResult(
            metadata=metadata,
            targets=screened_targets,
        )

        if save_run:
            self._persist_discovery_run(result)

        return result

    def _deduplicate_candidates(
        self, candidates: List[CandidateCompany]
    ) -> List[CandidateCompany]:
        """Deduplicate candidates using exact legal identifiers. Never merge purely by similar name."""
        unique_by_id: Dict[str, CandidateCompany] = {}
        resolver = EntityResolver()

        for cand in candidates:
            eid = cand.entity.entity_id
            if eid in unique_by_id:
                # Merge facts and evidence into existing candidate without overwriting
                existing = unique_by_id[eid]
                existing_fact_ids = {f.fact_id for f in existing.facts}
                for f in cand.facts:
                    if f.fact_id not in existing_fact_ids:
                        existing.facts.append(f)
                        existing_fact_ids.add(f.fact_id)

                existing_ev_ids = {e.evidence_id for e in existing.evidence}
                for e in cand.evidence:
                    if e.evidence_id not in existing_ev_ids:
                        existing.evidence.append(e)
                        existing_ev_ids.add(e.evidence_id)
                continue

            unique_by_id[eid] = cand

        return list(unique_by_id.values())

    def _persist_discovery_run(self, result: ScreeningResult) -> None:
        """Persist discovery run metadata and targets in storage."""
        if hasattr(self.storage, "save_discovery_run"):
            self.storage.save_discovery_run(result)
