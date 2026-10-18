"""Abstract base interface and contracts for AI model providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from opendeal.research.contracts import CompanyResearchContext


class SynthesisRequest(BaseModel):
    """Encapsulated payload submitted to a research synthesis model."""
    entity_name: str = Field(description="Target company name")
    context: CompanyResearchContext = Field(description="Grounded facts and evidence context")
    prompt_instruction: str = Field(
        default="Synthesize an evidence-grounded research brief based strictly on verified records.",
        description="Instruction directing model synthesis",
    )
    temperature: float = Field(default=0.0, description="Sampling temperature")
    max_tokens: int = Field(default=2000, description="Maximum generation budget")


class SynthesisResult(BaseModel):
    """Raw structured output produced by a synthesis provider prior to grounding validation."""
    executive_summary: str = Field(description="Synthesized executive overview")
    synthesized_claims: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Candidate assertions with knowledge tiers and cited evidence references",
    )
    risk_assessments: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Operational, legal, or financial risks identified",
    )
    provider_name: str = Field(description="Provider identifier (e.g. deterministic_mock, local_llama)")
    model_id: str = Field(description="Exact model identifier or version")
    execution_time_ms: float = Field(default=0.0, description="Latency in milliseconds")
    raw_response: Optional[str] = Field(default=None, description="Verbatim raw response text")


class AIProvider(ABC):
    """Abstract interface for intelligence and research synthesis providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider identifier."""
        pass

    @property
    @abstractmethod
    def is_external(self) -> bool:
        """Returns True if this provider communicates with external cloud services."""
        pass

    @property
    @abstractmethod
    def is_enabled(self) -> bool:
        """Returns True if the provider is currently active and authorized."""
        pass

    @abstractmethod
    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        """Execute evidence-backed brief synthesis.
        
        Must never corrupt, delete, or overwrite existing evidence or facts on failure.
        """
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """Verify provider availability and operational readiness."""
        pass
