"""Core domain enumeration types and shared constants for OpenDeal."""

from enum import Enum
from typing import Optional


class Currency(str, Enum):
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    JPY = "JPY"
    INR = "INR"
    CAD = "CAD"
    AUD = "AUD"
    CHF = "CHF"
    CNY = "CNY"
    HKD = "HKD"
    SGD = "SGD"
    UNKNOWN = "UNKNOWN"


class UnitScale(str, Enum):
    EXACT = "EXACT"            # 1
    THOUSANDS = "THOUSANDS"    # 1,000
    MILLIONS = "MILLIONS"      # 1,000,000
    BILLIONS = "BILLIONS"      # 1,000,000,000
    TRILLIONS = "TRILLIONS"    # 1,000,000,000,000
    PERCENT = "PERCENT"        # 0.01

    @property
    def multiplier(self) -> float:
        multipliers = {
            UnitScale.EXACT: 1.0,
            UnitScale.THOUSANDS: 1_000.0,
            UnitScale.MILLIONS: 1_000_000.0,
            UnitScale.BILLIONS: 1_000_000_000.0,
            UnitScale.TRILLIONS: 1_000_000_000_000.0,
            UnitScale.PERCENT: 0.01,
        }
        return multipliers[self]


class Sign(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    ZERO = "ZERO"
    UNSPECIFIED = "UNSPECIFIED"

    @classmethod
    def from_value(cls, value: Optional[float]) -> "Sign":
        if value is None:
            return cls.UNSPECIFIED
        if value > 0:
            return cls.POSITIVE
        if value < 0:
            return cls.NEGATIVE
        return cls.ZERO


class AccountingStandard(str, Enum):
    US_GAAP = "US-GAAP"
    IFRS = "IFRS"
    IND_AS = "IND-AS"
    UK_GAAP = "UK-GAAP"
    LOCAL_GAAP = "LOCAL-GAAP"
    UNKNOWN = "UNKNOWN"


class FiscalPeriodType(str, Enum):
    ANNUAL = "ANNUAL"
    Q1 = "Q1"
    Q2 = "Q2"
    Q3 = "Q3"
    Q4 = "Q4"
    YTD_Q2 = "YTD_Q2"   # 6 months cumulative
    YTD_Q3 = "YTD_Q3"   # 9 months cumulative
    TTM = "TTM"         # Trailing twelve months
    INSTANT = "INSTANT" # Balance sheet point-in-time
    OTHER = "OTHER"


class SourceType(str, Enum):
    REGULATORY_FILING = "REGULATORY_FILING"       # SEC 10-K, 10-Q, 8-K
    OFFICIAL_REGISTRY = "OFFICIAL_REGISTRY"       # Companies House, INPI
    OFFICIAL_EXCHANGE = "OFFICIAL_EXCHANGE"       # NSE, BSE, LSE announcements
    AUDITED_REPORT = "AUDITED_REPORT"             # Audited Annual Financial Report
    CENTRAL_BANK = "CENTRAL_BANK"                 # FRED, BoE, ECB, RBI
    OFFICIAL_PRESS_RELEASE = "OFFICIAL_PR"       # Direct Company IR publication
    CALCULATION = "CALCULATION"                   # Deterministic mathematical derivation
    MANUAL_ENTRY = "MANUAL_ENTRY"                 # Human analyst verified entry


class FactState(str, Enum):
    RAW_FACT = "RAW_FACT"                       # Directly extracted from evidence
    STANDARDIZED_FACT = "STANDARDIZED_FACT"     # Scale, currency, and period aligned
    VALIDATED_FACT = "VALIDATED_FACT"           # Passed integrity rules
    DERIVED_FACT = "DERIVED_FACT"               # Mathematically computed from other facts
    MISSING_FACT = "MISSING_FACT"               # DATA NOT FOUND with explicit reason


class ValidationStatus(str, Enum):
    UNVALIDATED = "UNVALIDATED"
    VALID = "VALID"
    WARNING = "WARNING"
    CONTESTED = "CONTESTED"                     # Conflicting values across sources
    INVALID = "INVALID"
    DATA_NOT_FOUND = "DATA_NOT_FOUND"


class MissingDataReason(str, Enum):
    NONE = "NONE"
    NOT_REPORTED = "NOT_REPORTED"               # Issuer omitted tag/disclosure
    RESTRICTED = "RESTRICTED"                   # Source requires paid entitlement
    EXEMPT = "EXEMPT"                           # Entity exempt under applicable regulation
    EXTRACTION_FAILED = "EXTRACTION_FAILED"     # Parser could not extract data
    UNMAPPED_TAXONOMY = "UNMAPPED_TAXONOMY"     # Tag not mapped in canonical dictionary
    NOT_APPLICABLE = "NOT_APPLICABLE"           # e.g., Inventory for a software firm


class EntityType(str, Enum):
    PUBLIC_LISTED = "PUBLIC_LISTED"
    PRIVATE_LIMITED = "PRIVATE_LIMITED"
    LLC = "LLC"
    PARTNERSHIP = "PARTNERSHIP"
    HOLDING = "HOLDING"
    SPAC = "SPAC"
    NON_PROFIT = "NON_PROFIT"
    GOVERNMENT = "GOVERNMENT"
    UNKNOWN = "UNKNOWN"


class ValidationSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
