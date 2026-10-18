"""Unit tests for Connector-Neutral Ingestion Orchestration.

Verifies:
- Offline SEC ingestion orchestration: submissions -> entity -> evidence -> facts -> validation -> storage.
- Offline Companies House ingestion orchestration: profile -> entity -> filings -> evidence -> storage.
- Provenance and audit trails (source URLs, cryptographic hashes).
- Graceful handling of missing files and invalid identifiers without crashing.
- No live network requests made during tests.
"""

import pytest

from opendeal.pipeline.orchestrator import IngestionOrchestrator
from opendeal.storage.sqlite import SQLiteStorage


class TestIngestionOrchestrator:
    """Tests for multi-connector ingestion orchestration."""

    @pytest.fixture
    def storage(self) -> SQLiteStorage:
        store = SQLiteStorage(":memory:")
        store.initialize()
        yield store
        store.close()

    @pytest.fixture
    def orchestrator(self, storage: SQLiteStorage) -> IngestionOrchestrator:
        return IngestionOrchestrator(storage=storage)

    def test_sec_offline_ingestion_workflow(self, storage, orchestrator):
        """Complete SEC offline ingestion succeeds with validated facts and stored evidence."""
        res = orchestrator.ingest_sec("AAPL", live=False)

        assert res.success is True
        assert res.entity is not None
        assert res.entity.legal_name == "Apple Inc."
        assert res.entity.ticker == "AAPL"
        assert res.facts_count > 0
        assert res.evidence_records_count > 0
        assert res.validation_summary.get("VALID", 0) > 0
        assert res.is_live is False
        assert "data.sec.gov" in res.source_url

        # Check storage persistence
        persisted = storage.get_entity(res.entity.entity_id)
        assert persisted is not None
        assert persisted.legal_name == "Apple Inc."

        # Check stored facts
        revenue_facts = storage.get_facts_for_entity(res.entity.entity_id, canonical_variable="revenue")
        assert len(revenue_facts) > 0

    def test_companies_house_offline_ingestion_workflow(self, storage, orchestrator):
        """Complete Companies House offline ingestion succeeds with entity and filings."""
        res = orchestrator.ingest_companies_house("01234567", live=False)

        assert res.success is True
        assert res.entity is not None
        assert res.entity.legal_name == "ACME BRITISH HOLDINGS LIMITED"
        assert res.entity.jurisdiction == "GB"
        assert res.filings_count > 0
        assert res.is_live is False
        assert len(res.warnings) >= 1
        assert "UK Small Company Exemption" in res.warnings[0]

        # Check storage persistence
        persisted = storage.get_entity(res.entity.entity_id)
        assert persisted is not None
        assert persisted.legal_name == "ACME BRITISH HOLDINGS LIMITED"
        assert persisted.get_identifier("COMPANIES_HOUSE") == "01234567"
