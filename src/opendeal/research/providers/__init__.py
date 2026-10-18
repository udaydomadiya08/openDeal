"""Provider abstractions and implementations for OpenDeal Research Layer."""

from opendeal.research.providers.base import AIProvider, SynthesisRequest, SynthesisResult
from opendeal.research.providers.mock import DeterministicMockProvider
from opendeal.research.providers.local import LocalModelProvider

__all__ = [
    "AIProvider",
    "SynthesisRequest",
    "SynthesisResult",
    "DeterministicMockProvider",
    "LocalModelProvider",
]
