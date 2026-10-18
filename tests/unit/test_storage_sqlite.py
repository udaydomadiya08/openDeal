"""Unit tests for SQLite storage, schema versioning, and conflict preservation."""

import json
from datetime import date
import pytest

from opendeal.core.types import (
    Currency,
    FiscalPeriodType,
    Sign,
    SourceType,
    UnitScale,
    ValidationSeverity,
    ValidationStatus,
)
from opendeal.entities.models import CompanyEntity
from opendeal.evidence.models import EvidenceRecord, SourceReference
from opendeal.facts.models import FactRecord, TransformationStep, ValidationResult
from opendeal.storage.sqlite import SQLiteStorage


class TestSQLiteStorage:
    """Test SQLite persistence, uniqueness constraints, schema versioning, and conflict preservation."""

    @pytest.fixture
    def storage(self) -> SQLiteStorage:
        store = SQLiteStorage(database_path=":memory:")
        store.initialize()
        yield store
        store.close()

    def test_schema_migrations_applied(self, storage):
        """Schema migrations table records all migrations including version 3 as applied."""
        cursor = storage.conn.cursor()
        cursor.execute("SELECT version, name FROM schema_migrations")
        rows = cursor.fetchall()
        assert len(rows) == 6
        assert rows[0]["version"] == 1
        assert rows[0]["name"] == "001_initial_schema"
        assert rows[1]["version"] == 2
        assert rows[1]["name"] == "002_entity_relationships"
        assert rows[2]["version"] == 3
        assert rows[2]["name"] == "003_research_briefs"
        assert rows[3]["version"] == 4
        assert rows[3]["name"] == "004_target_discovery_and_lists"
        assert rows[4]["version"] == 5
        assert rows[4]["name"] == "005_funding_and_transactions"
        assert rows[5]["version"] == 6
        assert rows[5]["name"] == "006_workflow_orchestration"

    def test_entity_persistence_and_lookups(self, storage):
        """Save and retrieve company entity by ID, CIK, and ticker."""
        entity = CompanyEntity(
            legal_name="Microsoft Corporation",
            jurisdiction="US-WA",
            ticker="MSFT",
            aliases=["Microsoft", "MSFT Corp"],
            metadata={"industry": "Software"},
        )
        entity.add_identifier("CIK", "0000789019", is_primary=True)
        entity.add_identifier("LEI", "INR2ET0498X7N9Q20L32")

        storage.save_entity(entity)

        # 1. Lookup by ID
        by_id = storage.get_entity(entity.entity_id)
        assert by_id is not None
        assert by_id.legal_name == "Microsoft Corporation"
        assert by_id.ticker == "MSFT"
        assert "Microsoft" in by_id.aliases
        assert by_id.metadata["industry"] == "Software"
        assert by_id.get_identifier("CIK") == "0000789019"
        assert by_id.get_identifier("LEI") == "INR2ET0498X7N9Q20L32"

        # 2. Lookup by CIK
        by_cik = storage.find_entity_by_identifier("CIK", "0000789019")
        assert by_cik is not None
        assert by_cik.entity_id == entity.entity_id

        # 3. Lookup by Ticker
        by_ticker = storage.find_entity_by_ticker("msft")
        assert by_ticker is not None
        assert by_ticker.entity_id == entity.entity_id

    def test_source_and_evidence_persistence(self, storage):
        """Save and retrieve SourceReference and immutable EvidenceRecord."""
        source = SourceReference(
            source_type=SourceType.REGULATORY_FILING,
            source_url="https://data.sec.gov/filing/123",
            publisher="SEC",
            document_id="000123-23-001",
            publication_date=date(2023, 11, 1),
            content_hash="hash_source_abc",
        )
        storage.save_source(source)

        evidence = EvidenceRecord.create(
            source_id=source.source_id,
            raw_content="Revenues: $50,000,000",
            extraction_method="PDF_PARSER",
            page_number=12,
            tag_name="us-gaap:Revenues",
        )
        storage.save_evidence(evidence)

        retrieved_src = storage.get_source(source.source_id)
        assert retrieved_src is not None
        assert retrieved_src.document_id == "000123-23-001"

        retrieved_ev = storage.get_evidence(evidence.evidence_id)
        assert retrieved_ev is not None
        assert retrieved_ev.raw_content == "Revenues: $50,000,000"
        assert retrieved_ev.page_number == 12

    def test_competing_facts_preserved_without_silent_overwrite(self, storage):
        """When conflicting figures are ingested for same period, both are preserved and cross-referenced."""
        entity = CompanyEntity(legal_name="Contested Corp", jurisdiction="US")
        storage.save_entity(entity)

        # First Fact: SEC 10-K reported $1,000,000,000
        fact_sec = FactRecord(
            entity_id=entity.entity_id,
            canonical_variable="revenue",
            reported_value=1000.0,
            normalized_value=1_000_000_000.0,
            unit_scale=UnitScale.MILLIONS,
            fiscal_year=2023,
            fiscal_period=FiscalPeriodType.ANNUAL,
            conflict_note="From official 10-K filing",
        )
        storage.save_fact(fact_sec)

        # Second Fact: Press Release reported $1,050,000,000
        fact_pr = FactRecord(
            entity_id=entity.entity_id,
            canonical_variable="revenue",
            reported_value=1050.0,
            normalized_value=1_050_000_000.0,
            unit_scale=UnitScale.MILLIONS,
            fiscal_year=2023,
            fiscal_period=FiscalPeriodType.ANNUAL,
            conflict_note="From preliminary press release",
        )
        storage.save_fact(fact_pr)

        # Both facts MUST remain in storage
        all_rev = storage.get_facts_for_entity(entity.entity_id, canonical_variable="revenue")
        assert len(all_rev) == 2

        fact_sec_updated = storage.get_fact(fact_sec.fact_id)
        fact_pr_updated = storage.get_fact(fact_pr.fact_id)

        assert fact_sec_updated is not None
        assert fact_pr_updated is not None

        # They must cross-reference each other in competing_fact_ids
        assert fact_pr.fact_id in fact_sec_updated.competing_fact_ids
        assert fact_sec.fact_id in fact_pr_updated.competing_fact_ids

    def test_validation_results_persistence(self, storage):
        """Save and retrieve validation results for a fact."""
        result = ValidationResult(
            target_id="fact_xyz",
            target_type="fact",
            rule_name="non_negative_assets",
            passed=True,
            severity=ValidationSeverity.INFO,
            observed_value=500000.0,
            message="Assets are positive",
        )
        storage.save_validation_result(result)

        findings = storage.get_validation_results("fact_xyz")
        assert len(findings) == 1
        assert findings[0].passed is True
        assert findings[0].rule_name == "non_negative_assets"

    def test_json_export_round_trip(self, storage):
        """export_facts_json serializes entity, facts, and linked evidence."""
        entity = CompanyEntity(legal_name="Export Corp", jurisdiction="US", ticker="EXPO")
        storage.save_entity(entity)

        source = SourceReference(
            source_type=SourceType.REGULATORY_FILING,
            source_url="https://sec.gov",
            publisher="SEC",
        )
        storage.save_source(source)

        ev = EvidenceRecord.create(
            source_id=source.source_id,
            raw_content="Net Income: $100M",
            extraction_method="XBRL",
        )
        storage.save_evidence(ev)

        fact = FactRecord(
            entity_id=entity.entity_id,
            canonical_variable="net_income",
            reported_value=100.0,
            normalized_value=100_000_000.0,
            fiscal_year=2023,
            fiscal_period=FiscalPeriodType.ANNUAL,
            evidence_ids=[ev.evidence_id],
        )
        storage.save_fact(fact)

        json_str = storage.export_facts_json(entity.entity_id)
        data = json.loads(json_str)

        assert data["entity"]["legal_name"] == "Export Corp"
        assert len(data["facts"]) == 1
        assert data["facts"][0]["normalized_value"] == 100_000_000.0
        assert len(data["evidence"]) == 1
        assert data["evidence"][0]["raw_content"] == "Net Income: $100M"
