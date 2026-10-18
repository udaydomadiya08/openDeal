"""UK Companies House connector module."""

from opendeal.connectors.companies_house.client import (
    CompaniesHouseAuthError,
    CompaniesHouseClient,
    CompaniesHouseClientError,
    CompaniesHouseNotFoundError,
    CompaniesHouseRateLimitError,
)
from opendeal.connectors.companies_house.parser import CompaniesHouseParser

__all__ = [
    "CompaniesHouseClient",
    "CompaniesHouseClientError",
    "CompaniesHouseRateLimitError",
    "CompaniesHouseNotFoundError",
    "CompaniesHouseAuthError",
    "CompaniesHouseParser",
]
