"""Unit tests for Unified Company Identity and Strict Entity Resolution Layer.

Verifies:
- Exact identifier matching (CIK, CRN, Ticker).
- Strong name-and-jurisdiction matching.
- Potential match requiring review when multiple ambiguous candidates exist.
- No match when search term is unknown.
- Strict rejection of automatic/silent merges.
- Former corporate names matching.
- Evidence-backed entity relationships (Parent/Subsidiary, Joint Venture).
- Entity nature classification (Legal Entity, Brand, Business Unit, Security).
"""

from datetime import date
import pytest

from opendeal.entities.models import (
    CompanyEntity,
    EntityNature,
    EntityRelationship,
    EntityType,
    RelationshipType,
    ResolutionOutcome,
)
from opendeal.entities.resolution import EntityResolver
from opendeal.storage.sqlite import SQLiteStorage


class TestEntityResolutionAndRelationships:
    """Tests for Phase 3 unified identity, resolution waterfall, and relationship tracking."""

    @pytest.fixture
    def storage(self) -> SQLiteStorage:
        store = SQLiteStorage(":memory:")
        store.initialize()
        yield store
        store.close()

    @pytest.fixture
    def resolver(self, storage: SQLiteStorage) -> EntityResolver:
        return EntityResolver(storage=storage)

    def test_exact_cik_resolution(self, storage, resolver):
        """Exact 10-digit CIK lookup yields EXACT_IDENTIFIER_MATCH with 1.0 confidence."""
        apple = CompanyEntity(
            legal_name="Apple Inc.",
            jurisdiction="US-CA",
            entity_type=EntityType.PUBLIC_LISTED,
            ticker="AAPL",
        )
        apple.add_identifier("CIK", "0000320193", is_primary=True)
        storage.save_entity(apple)

        # Query with unpadded CIK
        result = resolver.resolve("320193", jurisdiction="US")
        assert result.outcome == ResolutionOutcome.EXACT_IDENTIFIER_MATCH
        assert result.match_score == 1.0
        assert result.match_strategy == "EXACT_CIK"
        assert result.entity is not None
        assert result.entity.legal_name == "Apple Inc."

    def test_exact_crn_resolution(self, storage, resolver):
        """Exact 8-char UK Companies House number yields EXACT_IDENTIFIER_MATCH."""
        acme_uk = CompanyEntity(
            legal_name="Acme British Holdings Limited",
            jurisdiction="GB",
            entity_type=EntityType.PRIVATE_LIMITED,
        )
        acme_uk.add_identifier("COMPANIES_HOUSE", "01234567", is_primary=True)
        storage.save_entity(acme_uk)

        result = resolver.resolve("01234567", jurisdiction="GB")
        assert result.outcome == ResolutionOutcome.EXACT_IDENTIFIER_MATCH
        assert result.match_score == 1.0
        assert result.match_strategy == "EXACT_CRN"
        assert result.entity is not None
        assert result.entity.legal_name == "Acme British Holdings Limited"

    def test_exact_ticker_resolution(self, storage, resolver):
        """Exact ticker symbol lookup yields EXACT_IDENTIFIER_MATCH."""
        msft = CompanyEntity(
            legal_name="Microsoft Corporation",
            jurisdiction="US-WA",
            entity_type=EntityType.PUBLIC_LISTED,
            ticker="MSFT",
        )
        msft.add_identifier("TICKER", "MSFT", is_primary=True)
        storage.save_entity(msft)

        result = resolver.resolve("msft")
        assert result.outcome == ResolutionOutcome.EXACT_IDENTIFIER_MATCH
        assert result.match_score == 1.0
        assert result.match_strategy == "EXACT_TICKER"
        assert result.entity.ticker == "MSFT"

    def test_strong_name_and_jurisdiction_match(self, storage, resolver):
        """Single matching name in same jurisdiction yields STRONG_NAME_AND_JURISDICTION_MATCH."""
        google = CompanyEntity(
            legal_name="Alphabet Inc.",
            jurisdiction="US-DE",
            entity_type=EntityType.PUBLIC_LISTED,
            ticker="GOOGL",
        )
        storage.save_entity(google)

        result = resolver.resolve("Alphabet", jurisdiction="US")
        assert result.outcome == ResolutionOutcome.STRONG_NAME_AND_JURISDICTION_MATCH
        assert result.match_score == 0.95
        assert result.entity is not None
        assert result.entity.legal_name == "Alphabet Inc."

    def test_potential_match_requiring_review_multiple_candidates(self, storage, resolver):
        """Multiple candidates with similar names MUST NEVER silently merge and require manual review."""
        ent1 = CompanyEntity(
            legal_name="Apollo Global Management Inc.",
            jurisdiction="US-DE",
            entity_type=EntityType.PUBLIC_LISTED,
        )
        ent2 = CompanyEntity(
            legal_name="Apollo Management Holdings L.P.",
            jurisdiction="US-DE",
            entity_type=EntityType.PARTNERSHIP,
        )
        storage.save_entity(ent1)
        storage.save_entity(ent2)

        result = resolver.resolve("Apollo")
        assert result.outcome == ResolutionOutcome.POTENTIAL_MATCH_REQUIRES_REVIEW
        assert result.match_score == 0.70
        assert result.match_strategy == "MULTIPLE_NAME_MATCHES"
        assert result.entity is None  # Never auto-select one over the other!
        assert len(result.candidate_entities) == 2
        candidate_ids = {c.entity_id for c in result.candidate_entities}
        assert ent1.entity_id in candidate_ids
        assert ent2.entity_id in candidate_ids

    def test_no_match_returns_clean_result(self, storage, resolver):
        """Search with nonexistent identifier/name produces NO_MATCH without crashing."""
        result = resolver.resolve("NonExistentEnterprise_XYZ123")
        assert result.outcome == ResolutionOutcome.NO_MATCH
        assert result.match_score == 0.0
        assert result.entity is None
        assert len(result.candidate_entities) == 0

    def test_former_corporate_name_matching(self, storage, resolver):
        """Entity can be resolved via verified previous statutory corporate names."""
        meta = CompanyEntity(
            legal_name="Meta Platforms Inc.",
            jurisdiction="US-DE",
            entity_type=EntityType.PUBLIC_LISTED,
            ticker="META",
            former_names=["Facebook, Inc."],
        )
        storage.save_entity(meta)

        result = resolver.resolve("Facebook")
        assert result.outcome == ResolutionOutcome.POTENTIAL_MATCH_REQUIRES_REVIEW
        assert result.match_strategy == "FORMER_NAME_OR_ALIAS"
        assert len(result.candidate_entities) == 1
        assert result.candidate_entities[0].legal_name == "Meta Platforms Inc."

    def test_evidence_backed_parent_subsidiary_relationship(self, storage):
        """Evidence-backed relationships between corporate entities are stored and retrieved."""
        parent = CompanyEntity(
            legal_name="Alphabet Inc.",
            jurisdiction="US-DE",
            entity_type=EntityType.PUBLIC_LISTED,
        )
        subsidiary = CompanyEntity(
            legal_name="DeepMind Technologies Limited",
            jurisdiction="GB",
            entity_type=EntityType.PRIVATE_LIMITED,
        )
        storage.save_entity(parent)
        storage.save_entity(subsidiary)

        rel = EntityRelationship(
            source_entity_id=parent.entity_id,
            target_entity_id=subsidiary.entity_id,
            relationship_type=RelationshipType.PARENT_SUBSIDIARY,
            ownership_percentage=100.0,
            effective_date=date(2014, 1, 26),
            source_reference="SEC 10-K Exhibit 21 List of Subsidiaries",
            evidence_ids=["ev_sec_exhibit21_2014"],
            notes="Wholly owned UK subsidiary acquired in January 2014",
        )
        storage.save_relationship(rel)

        # Retrieve relationships
        rels = storage.get_relationships(parent.entity_id)
        assert len(rels) == 1
        saved_rel = rels[0]
        assert saved_rel.source_entity_id == parent.entity_id
        assert saved_rel.target_entity_id == subsidiary.entity_id
        assert saved_rel.relationship_type == RelationshipType.PARENT_SUBSIDIARY
        assert saved_rel.ownership_percentage == 100.0
        assert saved_rel.effective_date == date(2014, 1, 26)
        assert "ev_sec_exhibit21_2014" in saved_rel.evidence_ids
        assert saved_rel.source_reference == "SEC 10-K Exhibit 21 List of Subsidiaries"

    def test_entity_nature_distinction(self):
        """Distinguish legal entities from brands, business units, and securities."""
        corp = CompanyEntity(
            legal_name="Nike, Inc.",
            jurisdiction="US-OR",
            entity_nature=EntityNature.LEGAL_ENTITY.value,
        )
        brand = CompanyEntity(
            legal_name="Jordan Brand",
            jurisdiction="US-OR",
            entity_nature=EntityNature.BRAND.value,
        )
        unit = CompanyEntity(
            legal_name="Nike Direct",
            jurisdiction="US-OR",
            entity_nature=EntityNature.BUSINESS_UNIT.value,
        )
        security = CompanyEntity(
            legal_name="Nike Class B Common Stock",
            jurisdiction="US",
            entity_nature=EntityNature.SECURITY.value,
        )

        assert corp.entity_nature == "LEGAL_ENTITY"
        assert brand.entity_nature == "BRAND"
        assert unit.entity_nature == "BUSINESS_UNIT"
        assert security.entity_nature == "SECURITY"
