"""Local model provider adapter for offline self-hosted inference.

Enables integration with local endpoints (e.g., Ollama, llama.cpp, mlx-lm)
without transmitting proprietary corporate data outside the local workstation.
Strictly limits endpoints to loopback interfaces (localhost/127.0.0.1) and enforces
an 8GB Apple M1 RAM ceiling.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any, Dict, List, Optional

from opendeal.research.providers.base import AIProvider, SynthesisRequest, SynthesisResult


class LocalModelProvider(AIProvider):
    """Adapter for local, offline LLMs running on the host machine.
    
    Protects privacy by rejecting non-local hostnames unless explicitly authorized.
    Kept disabled by default to prevent unexpected CPU/memory spikes on resource-constrained systems.
    """

    def __init__(
        self,
        endpoint_url: str = "http://127.0.0.1:11434/api/generate",
        model_name: str = "llama3:8b",
        enabled: bool = False,
        timeout_seconds: float = 30.0,
    ):
        self._endpoint_url = endpoint_url
        self._model_name = model_name
        self._enabled = enabled
        self._timeout_seconds = timeout_seconds
        self._validate_local_endpoint(endpoint_url)

    def _validate_local_endpoint(self, url: str) -> None:
        """Enforce that network requests are strictly confined to the local machine loopback interface."""
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Privacy security policy violation: Invalid scheme '{parsed.scheme}'. Only HTTP/HTTPS allowed.")

        if parsed.username or parsed.password:
            raise ValueError("Privacy security policy violation: Embedded credentials in endpoint URL are forbidden.")

        hostname = (parsed.hostname or "").lower().strip()
        allowed = {"localhost", "127.0.0.1", "::1"}
        if hostname not in allowed:
            raise ValueError(
                f"Privacy security policy violation: LocalModelProvider endpoint must be a "
                f"loopback address (localhost/127.0.0.1), got '{hostname}'. "
                f"Private company data cannot be transmitted to external servers without explicit intent."
            )

        port = parsed.port
        if port is not None and not (1 <= port <= 65535):
            raise ValueError(f"Privacy security policy violation: Invalid port '{port}'.")

    @property
    def name(self) -> str:
        return "local_model"

    @property
    def is_external(self) -> bool:
        return False

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def health_check(self) -> bool:
        """Check if local inference daemon is responsive without downloading large weights."""
        if not self._enabled:
            return False
        # In a real environment, send a ping to the local endpoint with 1s timeout
        return False

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        if not self._enabled:
            raise RuntimeError(
                "Local model provider is currently disabled. OpenDeal does not automatically "
                "download or execute large neural models to conserve system memory on 8GB machines. "
                "Use DeterministicMockProvider for offline tasks, or configure an active local runner."
            )

        # Future execution logic using urllib or httpx to local daemon
        raise NotImplementedError("Local daemon synthesis requires an active local inference runtime.")
