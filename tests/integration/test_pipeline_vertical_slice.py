"""Integration test demonstrating the end-to-end vertical slice of OpenDeal Phase 1.

Verifies:
Mocked SEC Submissions
  -> CompanyEntity Resolution
  -> Raw Fact Extraction (XBRL tags + EvidenceRecords)
  -> FactStandardizer (Units, Scales, Signs)
  -> FactValidator (Bounds, Constraints)
  -> SQLiteStorage Persistence (Schema v1, Lineage)
  -> JSON Export
With zero live network calls!
"""

import json
from pathlib import Path
import pytest

from opendeal.connectors.sec.parser import SECParser
from opendeal.core.types import (
    Currency,
    FactState,
    FiscalPeriodType,
    MissingDataReason,
    Sign,
    ValidationStatus,
)
from opendeal.facts.models import FactRecord
from opendeal.pipeline.stages import FactPipeline
from opendeal.storage.sqlite import SQLiteStorage

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "sec"


class TestPipelineVerticalSlice:
    """End-to-end integration test verifying the complete data ingestion pipeline."""

    @pytest.fixture
    def submissions_fixture(self) -> dict:
        with open(FIXTURES_DIR / "apple_submissions.json", "r", encoding="utf-8") as f:
            return json.load(f)

    @pytest.fixture
    def companyfacts_fixture(self) -> dict:
        with open(FIXTURES_DIR / "apple_companyfacts_sample.json", "r", encoding="utf-8") as f:
            return json.load(f)

    @pytest.fixture
    def storage(self) -> SQLiteStorage:
        store = SQLiteStorage(":memory:")
        store.initialize()
        yield store
        store.close()

    def test_full_sec_ingestion_pipeline_run(self, submissions_fixture, companyfacts_fixture, storage):
        """Complete vertical execution from raw SEC fixtures to validated, persistent facts."""
        # 1. Company Resolver / Entity extraction
        entity = SECParser.parse_submissions_to_entity(submissions_fixture)
        assert entity.legal_name == "Apple Inc."
        assert entity.ticker == "AAPL"
        assert entity.get_identifier("CIK") == "0000320193"

        # 2. Ingest via FactPipeline
        pipeline = FactPipeline(storage=storage)
        source_url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
        metrics = pipeline.ingest_sec_facts(
            facts_data=companyfacts_fixture,
            entity=entity,
            source_url=source_url,
            payload_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )

        assert metrics["total_facts"] > 0
        assert metrics["evidence_count"] > 0
        assert metrics["validated_count"] == metrics["total_facts"]
        assert metrics["invalid_count"] == 0

        # 3. Verify storage persistence
        persisted_entity = storage.get_entity(entity.entity_id)
        assert persisted_entity is not None
        assert persisted_entity.legal_name == "Apple Inc."

        # Verify revenue facts
        revenue_facts = storage.get_facts_for_entity(entity.entity_id, canonical_variable="revenue")
        assert len(revenue_facts) == 4

        # Annual 2023 fact
        annual_rev = next(f for f in revenue_facts if f.fiscal_period == FiscalPeriodType.ANNUAL)
        assert annual_rev.normalized_value == 383285000000.0
        assert annual_rev.currency == Currency.USD
        assert annual_rev.fact_state == FactState.VALIDATED_FACT
        assert annual_rev.validation_status == ValidationStatus.VALID
        assert len(annual_rev.transformation_history) >= 1
        assert len(annual_rev.evidence_ids) == 1

        # Verify linked evidence
        evidence_id = annual_rev.evidence_ids[0]
        evidence = storage.get_evidence(evidence_id)
        assert evidence is not None
        assert "383285000000" in evidence.raw_content
        assert evidence.tag_name == "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
        assert len(evidence.content_hash) == 64

        # 4. Verify reported zero is preserved as 0.0, NOT as missing or null
        op_inc_facts = storage.get_facts_for_entity(entity.entity_id, canonical_variable="operating_income")
        assert len(op_inc_facts) == 1
        assert op_inc_facts[0].normalized_value == 0.0
        assert op_inc_facts[0].sign == Sign.ZERO
        assert op_inc_facts[0].validation_status == ValidationStatus.VALID

        # 5. Verify explicit Missing Fact handling
        missing_fact = FactRecord.create_missing(
            entity_id=entity.entity_id,
            canonical_variable="research_and_development",
            fiscal_year=2023,
            fiscal_period=FiscalPeriodType.ANNUAL,
            missing_reason=MissingDataReason.NOT_REPORTED,
        )
        storage.save_fact(missing_fact)

        rnd_facts = storage.get_facts_for_entity(entity.entity_id, canonical_variable="research_and_development")
        assert len(rnd_facts) == 1
        assert rnd_facts[0].normalized_value is None
        assert rnd_facts[0].fact_state == FactState.MISSING_FACT
        assert rnd_facts[0].validation_status == ValidationStatus.DATA_NOT_FOUND
        assert rnd_facts[0].missing_reason == MissingDataReason.NOT_REPORTED

        # 6. Verify complete JSON export
        exported_json_str = storage.export_facts_json(entity.entity_id)
        data = json.loads(exported_json_str)

        assert data["entity"]["legal_name"] == "Apple Inc."
        assert len(data["facts"]) == metrics["total_facts"] + 1  # Including the missing fact
        assert len(data["evidence"]) == metrics["evidence_count"]
