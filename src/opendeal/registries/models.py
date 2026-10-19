"""Data models for Global Securities Regulators, Corporate Registries, and Source Hierarchy."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SourceHierarchyTier(str, Enum):
    """Source authority hierarchy for evidence-based financial research.

    Enforces that statutory corporate filings and securities regulators
    outrank secondary or commercial sources.
    """
    TIER_1_STATUTORY_REGISTRY = "TIER_1_STATUTORY_REGISTRY"     # Official Corporate Registry (MCA, Companies House, State Registry)
    TIER_2_SECURITIES_REGULATOR = "TIER_2_SECURITIES_REGULATOR" # Securities Commission (SEBI, SEC, BaFin, ESMA, ASIC)
    TIER_3_STOCK_EXCHANGE = "TIER_3_STOCK_EXCHANGE"             # Official Exchange Filings (NSE/BSE, NYSE, LSE, Deutsche Börse)
    TIER_4_AUDITED_DISCLOSURE = "TIER_4_AUDITED_DISCLOSURE"     # Direct Audited Annual Reports & Notes
    TIER_5_CENTRAL_BANK = "TIER_5_CENTRAL_BANK"                 # Macro/Banking Regulators (RBI, ECB, Fed, BoE)


class JurisdictionAuthority(BaseModel):
    """Authoritative legal, corporate, and securities framework for a country/jurisdiction.

    Designed for global research and valuation systems to ensure research uses
    the exact statutory regulatory sources rather than hardcoding a single regulator.
    """
    country_code: str = Field(
        description="ISO 3166-1 alpha-2 code or regional identifier (e.g. 'IN', 'US', 'GB', 'DE', 'EU')",
    )
    jurisdiction: str = Field(
        description="Official country or market name (e.g. 'India', 'United States', 'Germany')",
    )
    region: str = Field(
        description="Global economic region (e.g. 'Asia-Pacific', 'Europe', 'North America', 'Middle East', 'Africa', 'Latin America', 'Central Asia')",
    )
    securities_regulator: str = Field(
        description="Primary securities and market conduct regulator (e.g. 'Securities and Exchange Board of India (SEBI)')",
    )
    corporate_registry: str = Field(
        description="Official corporate and entity registrar (e.g. 'Ministry of Corporate Affairs (MCA)')",
    )
    stock_exchange: str = Field(
        description="Primary national stock exchanges (e.g. 'NSE / BSE', 'NYSE / NASDAQ', 'LSE')",
    )
    official_disclosure_portal: str = Field(
        description="Official public filing and disclosure system (e.g. 'MCA Portal / NSE Disclosures', 'SEC EDGAR', 'Bundesanzeiger')",
    )
    financial_statement_source: str = Field(
        description="Primary source for statutory balance sheets and P&Ls (e.g. 'MCA AOC-4 XBRL', 'SEC Form 10-K', 'Companies House Accounts')",
    )
    ownership_disclosure_source: str = Field(
        description="Statutory source for beneficial ownership and shareholding patterns (e.g. 'MCA MGT-7 / SEBI SAST Reg 29', 'SEC Schedule 13D/G')",
    )
    official_api_or_download_url: str = Field(
        description="Official portal, API endpoint, or public search URL",
    )
    source_priority: int = Field(
        default=1,
        description="Source hierarchy ranking (1 = primary statutory authority)",
    )
    accounting_standards: List[str] = Field(
        default_factory=list,
        description="Primary accounting frameworks enforced (e.g. ['IND-AS', 'AS'], ['US-GAAP'], ['IFRS'])",
    )
    primary_identifiers: List[str] = Field(
        default_factory=list,
        description="Statutory entity identifiers used in jurisdiction (e.g. ['CIN', 'PAN', 'LEI'], ['CIK', 'EIN'], ['CRN'])",
    )
    currency: str = Field(
        default="USD",
        description="Primary national reporting currency (ISO 4217, e.g. 'INR', 'EUR', 'GBP', 'USD')",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Jurisdiction-specific legal context (e.g. federal vs free-zone, provincial vs national)",
    )
    last_verified_at: str = Field(
        default="2026-10-19",
        description="ISO date when regulatory authority contacts and portals were last verified",
    )


class FilingSourceResolution(BaseModel):
    """Result of resolving the authoritative regulatory filing framework for a company or jurisdiction."""
    is_supported: bool = Field(description="Whether the jurisdiction or identifier is registered")
    authority: Optional[JurisdictionAuthority] = Field(default=None, description="Matched jurisdiction authority")
    detected_scheme: Optional[str] = Field(default=None, description="Detected identifier scheme (e.g. CIN, CIK, CRN, LEI)")
    query_term: str = Field(description="Input query or identifier")
    resolution_status: str = Field(
        description="'VERIFIED_STATUTORY_AUTHORITY' if matched, or 'DATA_NOT_FOUND' if unverified",
    )
    recommended_filing_portal: Optional[str] = Field(default=None, description="Recommended official URL for research")
    guidance: str = Field(description="Actionable instruction on where statutory filings must be obtained")
