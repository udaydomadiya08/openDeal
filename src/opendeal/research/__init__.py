"""Phase 5 Evidence-Grounded AI Research and Intelligence Layer."""

from opendeal.research.contracts import (
    ClaimVerificationStatus,
    CompanyResearchContext,
    ConflictingEvidence,
    DataGap,
    FinancialTrend,
    KnowledgeTier,
    ResearchBrief,
    ResearchClaim,
    ResearchScope,
    ResearchStatus,
    RiskIndicator,
)
from opendeal.research.generator import ResearchBriefGenerator
from opendeal.research.grounding import GroundingValidator
from opendeal.research.providers.base import AIProvider, SynthesisRequest, SynthesisResult
from opendeal.research.providers.local import LocalModelProvider
from opendeal.research.providers.mock import DeterministicMockProvider
from opendeal.research.retriever import EvidenceRetriever, sanitize_and_defuse_text

__all__ = [
    "ClaimVerificationStatus",
    "CompanyResearchContext",
    "ConflictingEvidence",
    "DataGap",
    "FinancialTrend",
    "KnowledgeTier",
    "ResearchBrief",
    "ResearchClaim",
    "ResearchScope",
    "ResearchStatus",
    "RiskIndicator",
    "ResearchBriefGenerator",
    "GroundingValidator",
    "AIProvider",
    "SynthesisRequest",
    "SynthesisResult",
    "DeterministicMockProvider",
    "LocalModelProvider",
    "EvidenceRetriever",
    "sanitize_and_defuse_text",
]
