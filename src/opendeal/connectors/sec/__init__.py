"""Official US SEC EDGAR connector package."""

from opendeal.connectors.sec.client import (
    SECClient,
    SECClientError,
    SECRateLimitError,
    SECNotFoundError,
)
from opendeal.connectors.sec.parser import SECParser, US_GAAP_TAG_MAPPING

__all__ = [
    "SECClient",
    "SECClientError",
    "SECRateLimitError",
    "SECNotFoundError",
    "SECParser",
    "US_GAAP_TAG_MAPPING",
]
