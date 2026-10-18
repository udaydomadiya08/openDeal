"""Official UK Companies House REST API client."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Dict, Optional, Tuple
import httpx

from opendeal.config.settings import CompaniesHouseConfig, settings

logger = logging.getLogger(__name__)

# Type alias: (response_dict, source_url, payload_sha256)
CompaniesHouseResponse = Tuple[Dict[str, Any], str, str]


class CompaniesHouseClientError(Exception):
    """Base exception for UK Companies House API errors."""


class CompaniesHouseRateLimitError(CompaniesHouseClientError):
    """Raised when 429 Too Many Requests is encountered."""


class CompaniesHouseNotFoundError(CompaniesHouseClientError):
    """Raised when a company or document is not found (404)."""


class CompaniesHouseAuthError(CompaniesHouseClientError):
    """Raised on authentication/authorization failure (401/403)."""


class CompaniesHouseClient:
    """Official UK Companies House REST API Client.

    Operates with:
    - HTTP Basic Authentication (API key as username, empty password).
    - Rate limit regulation (default 2 req/sec, adhering to 600 req/5min quota).
    - Bounded retries with exponential backoff on transient errors.
    - Full provenance recording of retrieval URL, timestamp, and SHA-256 payload hash.
    """

    def __init__(
        self,
        config: Optional[CompaniesHouseConfig] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.config = config or settings.companies_house
        self._last_request_time: float = 0.0

        # Companies House requires HTTP Basic auth with API key as username and empty password
        auth = (self.config.api_key, "") if self.config.api_key else None
        headers = {
            "Accept": "application/json",
            "User-Agent": "OpenDealPlatform (research@opendeal-intelligence.org)",
        }

        self._client = httpx.Client(
            base_url=self.config.base_url.rstrip("/"),
            auth=auth,
            headers=headers,
            timeout=self.config.timeout_seconds,
            transport=transport,
        )

    def _enforce_rate_limit(self) -> None:
        """Throttle requests according to configured rate limit."""
        if self.config.rate_limit_per_second <= 0:
            return
        interval = 1.0 / self.config.rate_limit_per_second
        elapsed = time.time() - self._last_request_time
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_request_time = time.time()

    def _execute_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> CompaniesHouseResponse:
        """Execute GET request with bounded exponential backoff retries."""
        url = f"{self.config.base_url.rstrip('/')}{endpoint}"
        retries = 0

        while True:
            self._enforce_rate_limit()
            try:
                resp = self._client.get(endpoint, params=params)

                if resp.status_code == 200:
                    raw_bytes = resp.content
                    payload_hash = hashlib.sha256(raw_bytes).hexdigest()
                    data = resp.json()
                    return data, str(resp.url), payload_hash

                elif resp.status_code == 404:
                    raise CompaniesHouseNotFoundError(f"Companies House resource not found: {url}")

                elif resp.status_code in (401, 403):
                    raise CompaniesHouseAuthError(
                        f"Companies House authentication failed ({resp.status_code}): Check API key."
                    )

                elif resp.status_code == 429:
                    retries += 1
                    if retries > self.config.max_retries:
                        raise CompaniesHouseRateLimitError(
                            f"Companies House rate limit exceeded (429) after {retries} retries."
                        )
                    backoff = (2 ** retries) * 0.5
                    logger.warning("Companies House 429 rate limit hit. Backing off for %.2fs", backoff)
                    time.sleep(backoff)
                    continue

                else:
                    retries += 1
                    if retries > self.config.max_retries:
                        raise CompaniesHouseClientError(
                            f"Companies House HTTP error {resp.status_code}: {resp.text}"
                        )
                    time.sleep(0.5 * retries)

            except httpx.RequestError as e:
                retries += 1
                if retries > self.config.max_retries:
                    raise CompaniesHouseClientError(f"Network error accessing Companies House: {str(e)}") from e
                time.sleep(0.5 * retries)

    def get_company_profile(self, company_number: str) -> CompaniesHouseResponse:
        """Fetch profile for a UK company by registration number (CRN)."""
        clean_num = company_number.strip().upper().zfill(8)
        return self._execute_request(f"/company/{clean_num}")

    def get_filing_history(
        self, company_number: str, category: Optional[str] = None, items_per_page: int = 25
    ) -> CompaniesHouseResponse:
        """Fetch filing history for a UK company."""
        clean_num = company_number.strip().upper().zfill(8)
        params: Dict[str, Any] = {"items_per_page": items_per_page}
        if category:
            params["category"] = category
        return self._execute_request(f"/company/{clean_num}/filing-history", params=params)

    def get_officers(self, company_number: str) -> CompaniesHouseResponse:
        """Fetch registered officers (directors, secretaries) for a UK company."""
        clean_num = company_number.strip().upper().zfill(8)
        return self._execute_request(f"/company/{clean_num}/officers")

    def search_companies(self, query: str, items_per_page: int = 10) -> CompaniesHouseResponse:
        """Search UK companies by name or keyword."""
        return self._execute_request("/search/companies", params={"q": query, "items_per_page": items_per_page})

    def close(self) -> None:
        """Close underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> CompaniesHouseClient:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
