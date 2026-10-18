"""Unit tests for CompanyEntity and legal identifier contracts."""

import pytest
from pydantic import ValidationError

from opendeal.core.types import EntityType
from opendeal.entities.models import (
    CompanyEntity,
    LegalIdentifier,
    EntityResolutionQuery,
    EntityResolutionResult,
)


class TestCompanyEntity:
    """Test CompanyEntity validation, identifiers, and private-market handling."""

    def test_company_creation_minimal(self):
        """Minimal company entity with only legal_name and jurisdiction."""
        entity = CompanyEntity(legal_name="Stripe, Inc.", jurisdiction="US-DE")
        assert entity.legal_name == "Stripe, Inc."
        assert entity.jurisdiction == "US-DE"
        assert entity.entity_id.startswith("ent_")
        assert entity.ticker is None  # Private company, no ticker!
        assert entity.entity_type == EntityType.UNKNOWN
        assert entity.is_active is True
        assert len(entity.legal_identifiers) == 0

    def test_blank_legal_name_rejected(self):
        """Validation fails if legal_name is blank or whitespace."""
        with pytest.raises(ValidationError):
            CompanyEntity(legal_name="   ", jurisdiction="US")

    def test_add_and_retrieve_legal_identifiers(self):
        """Identifiers can be added and looked up case-insensitively."""
        entity = CompanyEntity(legal_name="Space Exploration Technologies Corp.", jurisdiction="US-DE")
        entity.add_identifier("IRS_EIN", "20-0000000", jurisdiction="US")
        entity.add_identifier("LEI", "5493006MHB84DD0ZWV18", is_primary=True)

        assert entity.get_identifier("lei") == "5493006MHB84DD0ZWV18"
        assert entity.get_identifier("IRS_EIN") == "20-0000000"
        assert entity.get_identifier("NON_EXISTENT") is None

    def test_adding_ticker_identifier_updates_entity_ticker(self):
        """Adding a TICKER legal identifier automatically sets the entity's ticker attribute."""
        entity = CompanyEntity(legal_name="Apple Inc.", jurisdiction="US-CA")
        entity.add_identifier("TICKER", "aapl")
        assert entity.ticker == "AAPL"

    def test_duplicate_identifier_scheme_updated(self):
        """Adding an identifier with existing scheme replaces the previous value."""
        entity = CompanyEntity(legal_name="Test Corp", jurisdiction="GB")
        entity.add_identifier("COMPANIES_HOUSE", "01234567")
        assert entity.get_identifier("COMPANIES_HOUSE") == "01234567"

        entity.add_identifier("COMPANIES_HOUSE", "99999999")
        assert entity.get_identifier("COMPANIES_HOUSE") == "99999999"
        assert len([i for i in entity.legal_identifiers if i.scheme == "COMPANIES_HOUSE"]) == 1

    def test_entity_aliases_and_metadata(self):
        """Aliases and arbitrary registry metadata are preserved."""
        entity = CompanyEntity(
            legal_name="Alphabet Inc.",
            jurisdiction="US-DE",
            entity_type=EntityType.PUBLIC_LISTED,
            aliases=["Google", "Google LLC"],
            metadata={"sector": "Technology", "employees": 180000},
        )
        assert "Google" in entity.aliases
        assert entity.metadata["sector"] == "Technology"

    def test_resolution_models(self):
        """Entity resolution query and result data classes validate properly."""
        query = EntityResolutionQuery(query="AAPL", jurisdiction="US")
        assert query.query == "AAPL"

        entity = CompanyEntity(legal_name="Apple Inc.", jurisdiction="US-CA", ticker="AAPL")
        result = EntityResolutionResult(
            entity=entity,
            match_score=0.98,
            match_strategy="Exact Ticker",
            matched_term="AAPL",
        )
        assert result.match_score == 0.98
        assert result.entity.ticker == "AAPL"
