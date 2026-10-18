"""OpenDeal Target Discovery and Deal Sourcing Module (Phase 6)."""

from opendeal.discovery.contracts import (
    CandidateCompany,
    DiscoveryRunMetadata,
    EligibilityDecision,
    EligibilityOutcome,
    ExclusionReason,
    FinancialFilterCriterion,
    ScoringFactorBreakdown,
    ScreenedTarget,
    ScreeningResult,
    TargetCriteria,
    TargetList,
    TargetListHistory,
    TargetListItem,
    TargetListStatus,
    TargetScoringBreakdown,
)
from opendeal.discovery.eligibility import EligibilityEvaluator
from opendeal.discovery.scoring import TargetScorer
from opendeal.discovery.service import TargetDiscoveryService
from opendeal.discovery.watchlists import (
    DuplicateMemberError,
    TargetListManager,
    TargetListNotFoundError,
)

__all__ = [
    "CandidateCompany",
    "DiscoveryRunMetadata",
    "EligibilityDecision",
    "EligibilityOutcome",
    "ExclusionReason",
    "FinancialFilterCriterion",
    "ScoringFactorBreakdown",
    "ScreenedTarget",
    "ScreeningResult",
    "TargetCriteria",
    "TargetList",
    "TargetListHistory",
    "TargetListItem",
    "TargetListStatus",
    "TargetScoringBreakdown",
    "EligibilityEvaluator",
    "TargetScorer",
    "TargetDiscoveryService",
    "TargetListManager",
    "DuplicateMemberError",
    "TargetListNotFoundError",
]
