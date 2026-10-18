"""Official US SEC EDGAR API client with fair-access rate limiting and error handling."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Dict, Optional, Tuple
import httpx

from opendeal.config.settings import SECConfig, settings

logger = logging.getLogger(__name__)

# Type alias for SEC response: (data_dict, source_url, payload_sha256)
TupleResponse = Tuple[Dict[str, Any], str, str]


class SECClientError(Exception):
    """Base exception for SEC API errors."""
    pass


class SECRateLimitError(SECClientError):
    """Raised when SEC 429 Too Many Requests response is encountered."""
    pass


class SECNotFoundError(SECClientError):
    """Raised when an SEC entity or filing is not found (404)."""
    pass


class SECClient:
    """Official SEC EDGAR REST API Client.
    
    Adheres strictly to the SEC's Fair Access Policy:
    - User-Agent header with Organization and Contact Email.
    - Max rate limit enforced locally (default: 5 req/sec).
    - Exponential backoff retries for transient errors.
    """

    def __init__(
        self,
        config: Optional[SECConfig] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.config = config or settings.sec
        self._last_request_time: float = 0.0
        self._min_interval: float = 1.0 / self.config.rate_limit_per_second
        self._transport = transport
        self._client: Optional[httpx.Client] = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                transport=self._transport,
                timeout=self.config.timeout_seconds,
                headers={
                    "User-Agent": self.config.user_agent,
                    "Accept-Encoding": "gzip, deflate",
                    "Accept": "application/json",
                },
            )
        return self._client

    def close(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.close()
            self._client = None

    def _wait_for_rate_limit(self) -> None:
        """Enforce fair-access rate limit intervals between requests."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.monotonic()

    @staticmethod
    def normalize_cik(cik: str) -> str:
        """Normalize CIK to 10-digit zero-padded string required by SEC endpoints."""
        cleaned = "".join(filter(str.isdigit, str(cik)))
        if not cleaned:
            raise ValueError(f"Invalid CIK: '{cik}' contains no digits")
        return cleaned.zfill(10)

    def _request(self, endpoint: str) -> TupleResponse:
        """Execute request with retries, rate limiting, and response validation."""
        url = f"{self.config.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        retries = 0

        while True:
            self._wait_for_rate_limit()
            try:
                response = self.client.get(url)

                if response.status_code == 200:
                    raw_text = response.text
                    payload_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
                    try:
                        data = response.json()
                        return data, url, payload_hash
                    except json.JSONDecodeError as e:
                        raise SECClientError(f"Invalid JSON returned from {url}: {e}") from e

                if response.status_code == 404:
                    raise SECNotFoundError(f"SEC endpoint returned 404 Not Found: {url}")

                if response.status_code == 429:
                    retries += 1
                    if retries > self.config.max_retries:
                        raise SECRateLimitError(f"SEC rate limit (429) exceeded for {url} after {retries} retries")
                    sleep_time = 1.0 * (2 ** retries)
                    logger.warning("SEC 429 Rate Limit encountered. Backing off for %.2f seconds.", sleep_time)
                    time.sleep(sleep_time)
                    continue

                if response.status_code >= 500:
                    retries += 1
                    if retries > self.config.max_retries:
                        raise SECClientError(f"SEC server error {response.status_code} for {url}")
                    time.sleep(1.0 * retries)
                    continue

                raise SECClientError(f"SEC request failed with status {response.status_code} for {url}")

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                retries += 1
                if retries > self.config.max_retries:
                    raise SECClientError(f"SEC request network error for {url}: {e}") from e
                time.sleep(1.0 * retries)

    def get_submissions(self, cik: str) -> TupleResponse:
        """Fetch official company submissions JSON.
        
        Returns: (data_dict, source_url, payload_sha256)
        """
        padded_cik = self.normalize_cik(cik)
        return self._request(f"submissions/CIK{padded_cik}.json")

    def get_company_facts(self, cik: str) -> TupleResponse:
        """Fetch all official XBRL financial facts for an issuer.
        
        Returns: (data_dict, source_url, payload_sha256)
        """
        padded_cik = self.normalize_cik(cik)
        return self._request(f"api/xbrl/companyfacts/CIK{padded_cik}.json")

    def get_company_concept(self, cik: str, taxonomy: str, concept: str) -> TupleResponse:
        """Fetch all disclosures for a single XBRL concept (e.g. us-gaap/AccountsPayableCurrent).
        
        Returns: (data_dict, source_url, payload_sha256)
        """
        padded_cik = self.normalize_cik(cik)
        return self._request(f"api/xbrl/companyconcept/CIK{padded_cik}/{taxonomy}/{concept}.json")

