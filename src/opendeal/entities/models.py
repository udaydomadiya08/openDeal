"""Company identity, legal identifiers, entity relationships, and resolution models."""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
import re
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field, field_validator

from opendeal.core.types import EntityType


class EntityNature(str, Enum):
    """Distinguishes legal entities from brands, business units, and securities."""
    LEGAL_ENTITY = "LEGAL_ENTITY"
    BRAND = "BRAND"
    BUSINESS_UNIT = "BUSINESS_UNIT"
    SECURITY = "SECURITY"


class RelationshipType(str, Enum):
    """Categorization of corporate and structural relationships between entities."""
    SUBSIDIARY = "SUBSIDIARY"
    PARENT = "PARENT"
    PARENT_SUBSIDIARY = "PARENT_SUBSIDIARY"
    ULTIMATE_PARENT = "ULTIMATE_PARENT"
    AFFILIATE = "AFFILIATE"
    JOINT_VENTURE = "JOINT_VENTURE"
    BRANCH = "BRANCH"
    BRAND_OF = "BRAND_OF"
    BUSINESS_UNIT_OF = "BUSINESS_UNIT_OF"
    SECURITY_OF = "SECURITY_OF"
    # Private-Market & Transaction Relationships (Phase 7)
    INVESTOR = "INVESTOR"
    LEAD_INVESTOR = "LEAD_INVESTOR"
    FUND_MANAGER = "FUND_MANAGER"
    PORTFOLIO_COMPANY = "PORTFOLIO_COMPANY"
    ACQUIRER = "ACQUIRER"
    DIVESTED_UNIT = "DIVESTED_UNIT"
    EXECUTIVE = "EXECUTIVE"
    FOUNDER = "FOUNDER"


class ResolutionOutcome(str, Enum):
    """Explicit outcomes for company entity resolution.

    Ensures ambiguous names or conflicting records are never silently merged.
    """
    EXACT_IDENTIFIER_MATCH = "EXACT_IDENTIFIER_MATCH"
    STRONG_NAME_AND_JURISDICTION_MATCH = "STRONG_NAME_AND_JURISDICTION_MATCH"
    POTENTIAL_MATCH_REQUIRES_REVIEW = "POTENTIAL_MATCH_REQUIRES_REVIEW"
    NO_MATCH = "NO_MATCH"


class LegalIdentifier(BaseModel):
    """A formal registry or legal identifier issued to a corporate entity."""
    scheme: str = Field(
        description="Identification authority scheme (e.g. CIK, LEI, COMPANIES_HOUSE, CIN, SIREN, IRS_EIN, TICKER)",
    )
    identifier: str = Field(
        description="The assigned identifier string in standard normalized format",
    )
    jurisdiction: Optional[str] = Field(
        default=None,
        description="Jurisdiction where the identifier was issued (e.g. US, US-DE, GB, IN)",
    )
    is_primary: bool = Field(
        default=False,
        description="Whether this is the primary identifier within its scheme",
    )

    @field_validator("identifier")
    @classmethod
    def clean_identifier(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Legal identifier cannot be empty")
        return cleaned


class EntityRelationship(BaseModel):
    """Evidence-backed relationship between corporate entities (e.g. parent-subsidiary)."""
    relationship_id: str = Field(
        default_factory=lambda: f"rel_{uuid.uuid4().hex[:12]}",
        description="Stable relationship record identifier",
    )
    source_entity_id: str = Field(description="Originating company entity ID")
    target_entity_id: str = Field(description="Related target company entity ID")
    relationship_type: RelationshipType = Field(description="Type of corporate relationship")
    ownership_percentage: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Equity or voting control percentage if applicable",
    )
    effective_date: Optional[date] = Field(default=None, description="Date relationship became effective")
    end_date: Optional[date] = Field(default=None, description="Date relationship terminated if dissolved")
    evidence_ids: List[str] = Field(
        default_factory=list,
        description="Supporting evidence records backing this relationship",
    )
    source_reference: Optional[str] = Field(
        default=None,
        description="Human-readable citation (e.g. 'Exhibit 21 to 2023 10-K')",
    )
    notes: Optional[str] = Field(default=None, description="Contextual notes on relationship terms")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CompanyEntity(BaseModel):
    """Canonical representation of an enterprise or corporate entity.

    Supports public corporations, private startups, holding companies, and subsidiaries.
    Tickers are treated as market-specific securities rather than universal company IDs.
    """
    entity_id: str = Field(
        default_factory=lambda: f"ent_{uuid.uuid4().hex[:12]}",
        description="Stable canonical internal entity identifier",
    )
    legal_name: str = Field(
        description="Official registered corporate name in primary jurisdiction",
    )
    jurisdiction: str = Field(
        description="ISO 3166 jurisdiction code (e.g. 'US', 'US-DE', 'GB', 'IN', 'FR')",
    )
    entity_type: EntityType = Field(
        default=EntityType.UNKNOWN,
        description="Legal structure (e.g. PUBLIC_LISTED, PRIVATE_LIMITED, LLC)",
    )
    entity_nature: str = Field(
        default="LEGAL_ENTITY",
        description="Distinguishes LEGAL_ENTITY, BRAND, BUSINESS_UNIT, or SECURITY",
    )
    legal_identifiers: List[LegalIdentifier] = Field(
        default_factory=list,
        description="List of official registered identifiers (CIK, LEI, Companies House number)",
    )
    aliases: List[str] = Field(
        default_factory=list,
        description="Alternate trade names, DBAs, or commercial brand names",
    )
    former_names: List[str] = Field(
        default_factory=list,
        description="Previous legal corporate names prior to statutory name changes",
    )
    ticker: Optional[str] = Field(
        default=None,
        description="Current trading symbol if publicly listed, None for private companies",
    )
    primary_exchange: Optional[str] = Field(
        default=None,
        description="Primary exchange if listed (e.g. 'NASDAQ', 'NYSE', 'LSE', 'NSE')",
    )
    incorporation_date: Optional[date] = Field(
        default=None,
        description="Date of legal formation or registration",
    )
    is_active: bool = Field(
        default=True,
        description="Whether the entity is currently active and in good standing",
    )
    primary_country: Optional[str] = Field(
        default=None,
        description="Country of principal executive offices (ISO 2-letter code)",
    )
    website: Optional[str] = Field(
        default=None,
        description="Official corporate website URL",
    )
    sic_code: Optional[str] = Field(
        default=None,
        description="Standard Industrial Classification code",
    )
    sic_description: Optional[str] = Field(
        default=None,
        description="Description corresponding to SIC or industry category",
    )
    ticker_history: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Historical stock tickers, exchanges, and listing dates",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Record ingestion timestamp in UTC",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Record modification timestamp in UTC",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary supplementary attributes from primary registries",
    )

    @property
    def canonical_name(self) -> str:
        """Alias for legal_name for presentation and backward compatibility."""
        return self.legal_name

    @property
    def legal_jurisdiction(self) -> str:
        """Alias for jurisdiction for presentation and backward compatibility."""
        return self.jurisdiction


    @field_validator("legal_name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Legal name cannot be blank")
        return cleaned

    def get_identifier(self, scheme: str) -> Optional[str]:
        """Retrieve identifier for a specific scheme (case-insensitive)."""
        scheme_upper = scheme.upper()
        for ident in self.legal_identifiers:
            if ident.scheme.upper() == scheme_upper:
                return ident.identifier
        return None

    def add_identifier(
        self, scheme: str, identifier: str, jurisdiction: Optional[str] = None, is_primary: bool = False
    ) -> None:
        """Add or update a legal identifier."""
        scheme_upper = scheme.upper()
        clean_id = identifier.strip()
        # Remove existing for same scheme
        self.legal_identifiers = [i for i in self.legal_identifiers if i.scheme.upper() != scheme_upper]
        self.legal_identifiers.append(
            LegalIdentifier(
                scheme=scheme_upper,
                identifier=clean_id,
                jurisdiction=jurisdiction or self.jurisdiction,
                is_primary=is_primary,
            )
        )
        if scheme_upper == "TICKER":
            self.ticker = clean_id.upper()

    def add_former_name(self, former_name: str) -> None:
        """Record a verified previous corporate name."""
        clean = former_name.strip()
        if clean and clean not in self.former_names and clean.lower() != self.legal_name.lower():
            self.former_names.append(clean)


class EntityResolutionQuery(BaseModel):
    """Search query parameters to resolve an entity."""
    query: str = Field(description="Search text: company name, ticker, or registration number")
    jurisdiction: Optional[str] = Field(default=None, description="Optional jurisdiction filter")
    country: Optional[str] = Field(default=None, description="Optional country filter")


class EntityResolutionResult(BaseModel):
    """Result of an entity resolution lookup."""
    outcome: ResolutionOutcome = Field(
        default=ResolutionOutcome.EXACT_IDENTIFIER_MATCH,
        description="Resolution classification outcome",
    )
    entity: Optional[CompanyEntity] = Field(default=None, description="Resolved entity if match is high confidence")
    match_score: float = Field(ge=0.0, le=1.0, description="Confidence in resolution match (0.0 to 1.0)")
    match_strategy: str = Field(description="Method used (e.g. EXACT_CIK, EXACT_CRN, EXACT_TICKER, NORMALIZED_NAME)")
    matched_term: str = Field(description="The specific search term that triggered the match")
    candidate_entities: List[CompanyEntity] = Field(
        default_factory=list,
        description="Candidate entities when manual review is required",
    )
    resolution_notes: Optional[str] = Field(default=None, description="Audit notes on resolution reason")
