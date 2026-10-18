"""Unit, integration, and security tests for Phase 7 Deals & Transaction Intelligence.

Validates:
- Funding round modeling: multiple investors, undisclosed amounts, currencies, valuations.
- M&A transactions: statuses (announced, completed, terminated), consideration mixes, missing values.
- Relationship graph: investor org vs fund, cycle-safe bounded traversal, ownership claims.
- Conflict detection and event reconciliation: preserving differing source observations.
- SQLite migration V5 and foreign-key integrity.
- CLI commands: company, funding, acquisitions, event, investors, timeline, explain, search, ingest.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
import pytest
from click.testing import CliRunner

from opendeal.cli import cli
from opendeal.core.types import Currency
from opendeal.deals.contracts import (
    AmountKind,
    CompanyDealsOverview,
    ConsiderationType,
    DealConflict,
    DealVerificationStatus,
    FundingParticipant,
    FundingRound,
    FundingRoundType,
    InvestorType,
    TransactionEvent,
    TransactionStatus,
    TransactionType,
)
from opendeal.deals.graph import RelationshipGraphEngine
from opendeal.deals.reconciliation import EventReconciler
from opendeal.deals.service import DealsIntelligenceService
from opendeal.entities.models import CompanyEntity, EntityRelationship, RelationshipType
from opendeal.storage.sqlite import SQLiteStorage


@pytest.fixture
def memory_storage() -> SQLiteStorage:
    """Provide a clean, migrated in-memory SQLite storage instance."""
    storage = SQLiteStorage(":memory:")
    storage.initialize()
    return storage


@pytest.fixture
def sample_company(memory_storage: SQLiteStorage) -> CompanyEntity:
    """Create and persist a base operating company."""
    company = CompanyEntity(
        entity_id="ent_test_co",
        legal_name="Test Robotics Corp",
        jurisdiction="US",
    )
    memory_storage.save_entity(company)
    return company


@pytest.fixture
def sample_investor_org(memory_storage: SQLiteStorage) -> CompanyEntity:
    """Create and persist an investor organization."""
    investor = CompanyEntity(
        entity_id="ent_investor_org",
        legal_name="Apex Global Ventures LLC",
        jurisdiction="US",
    )
    memory_storage.save_entity(investor)
    return investor


@pytest.fixture
def sample_fund(memory_storage: SQLiteStorage) -> CompanyEntity:
    """Create and persist an investment fund."""
    fund = CompanyEntity(
        entity_id="ent_fund_01",
        legal_name="Apex Venture Fund I LP",
        jurisdiction="US",
    )
    memory_storage.save_entity(fund)
    return fund


# ── 1. Funding Round Tests ──────────────────────────────────────────────────

class TestFundingRounds:
    """Tests for funding round contracts, modeling, and valuation safeguards."""

    def test_multiple_investors_in_one_round(
        self, memory_storage: SQLiteStorage, sample_company: CompanyEntity, sample_investor_org: CompanyEntity
    ) -> None:
        """Verify multiple investors can participate in one round with lead vs syndicate roles."""
        p1 = FundingParticipant(
            participant_id="fp_lead_01",
            round_id="rnd_seed_01",
            investor_id=sample_investor_org.entity_id,
            investor_name=sample_investor_org.legal_name,
            is_lead=True,
            disclosed_commitment=1500000.0,
            currency=Currency.USD,
            implied_ownership_pct=15.0,
            ownership_assumptions="Documented in term sheet",
        )
        p2 = FundingParticipant(
            participant_id="fp_part_02",
            round_id="rnd_seed_01",
            investor_name="Angel Participant Alpha",
            is_lead=False,
            disclosed_commitment=500000.0,
            currency=Currency.USD,
            implied_ownership_pct=5.0,
        )

        rnd = FundingRound(
            round_id="rnd_seed_01",
            company_id=sample_company.entity_id,
            round_type=FundingRoundType.SEED,
            announced_date=date(2024, 1, 15),
            closing_date=date(2024, 2, 1),
            disclosed_amount=2000000.0,
            currency=Currency.USD,
            amount_kind=AmountKind.ROUND_TOTAL,
            pre_money_valuation=8000000.0,
            post_money_valuation=10000000.0,
            lead_investor_ids=[sample_investor_org.entity_id],
            participant_ids=[sample_investor_org.entity_id],
            participants=[p1, p2],
            verification_status=DealVerificationStatus.VERIFIED_PRIMARY_SOURCE,
        )

        service = DealsIntelligenceService(memory_storage)
        saved_rnd, conflicts = service.record_funding_round(rnd)

        assert saved_rnd.round_id == "rnd_seed_01"
        assert len(conflicts) == 0

        # Retrieve and verify round and participants
        fetched = memory_storage.get_funding_round("rnd_seed_01")
        assert fetched is not None
        assert fetched.disclosed_amount == 2000000.0
        assert len(fetched.participants) == 2
        assert fetched.participants[0].is_lead is True
        assert fetched.participants[0].implied_ownership_pct == 15.0
        assert fetched.participants[1].is_lead is False

    def test_missing_funding_amount_remains_none(
        self, memory_storage: SQLiteStorage, sample_company: CompanyEntity
    ) -> None:
        """Undisclosed funding amounts must strictly remain None, never defaulting to 0.0."""
        rnd = FundingRound(
            round_id="rnd_undisclosed_01",
            company_id=sample_company.entity_id,
            round_type=FundingRoundType.VENTURE_SERIES_UNKNOWN,
            announced_date=date(2024, 5, 20),
            disclosed_amount=None,  # Explicitly missing
            currency=Currency.USD,
            pre_money_valuation=None,
            post_money_valuation=None,
        )

        memory_storage.save_funding_round(rnd)
        fetched = memory_storage.get_funding_round("rnd_undisclosed_01")
        assert fetched is not None
        assert fetched.disclosed_amount is None
        assert fetched.disclosed_amount != 0.0
        assert fetched.pre_money_valuation is None
        assert fetched.post_money_valuation is None

    def test_different_currencies(
        self, memory_storage: SQLiteStorage, sample_company: CompanyEntity
    ) -> None:
        """Ensure multi-currency funding rounds maintain their distinct currency attributes."""
        rnd_gbp = FundingRound(
            round_id="rnd_gbp_01",
            company_id=sample_company.entity_id,
            round_type=FundingRoundType.SEED,
            disclosed_amount=1000000.0,
            currency=Currency.GBP,
        )
        rnd_eur = FundingRound(
            round_id="rnd_eur_01",
            company_id=sample_company.entity_id,
            round_type=FundingRoundType.SERIES_A,
            disclosed_amount=5000000.0,
            currency=Currency.EUR,
        )

        memory_storage.save_funding_round(rnd_gbp)
        memory_storage.save_funding_round(rnd_eur)

        f_gbp = memory_storage.get_funding_round("rnd_gbp_01")
        f_eur = memory_storage.get_funding_round("rnd_eur_01")
        assert f_gbp.currency == Currency.GBP
        assert f_eur.currency == Currency.EUR

    def test_amount_kind_announced_vs_cumulative(self) -> None:
        """Verify amount_kind distinguishes between round totals, tranches, and cumulative funding."""
        rnd_total = FundingRound(
            company_id="ent_x",
            round_type=FundingRoundType.SERIES_A,
            disclosed_amount=10000000.0,
            amount_kind=AmountKind.ROUND_TOTAL,
        )
        rnd_tranche = FundingRound(
            company_id="ent_x",
            round_type=FundingRoundType.SERIES_A,
            disclosed_amount=3000000.0,
            amount_kind=AmountKind.TRANCHE,
        )
        assert rnd_total.amount_kind == AmountKind.ROUND_TOTAL
        assert rnd_tranche.amount_kind == AmountKind.TRANCHE

    def test_pre_and_post_money_valuation_distinctions(self) -> None:
        """Valuation amounts must preserve explicit pre/post distinctions without hallucination."""
        rnd = FundingRound(
            company_id="ent_x",
            round_type=FundingRoundType.SEED,
            disclosed_amount=2000000.0,
            pre_money_valuation=8000000.0,
            post_money_valuation=10000000.0,
        )
        assert rnd.pre_money_valuation == 8000000.0
        assert rnd.post_money_valuation == 10000000.0

        # If post-money is not disclosed, it is NOT automatically assumed
        rnd_partial = FundingRound(
            company_id="ent_x",
            round_type=FundingRoundType.SEED,
            disclosed_amount=2000000.0,
            pre_money_valuation=8000000.0,
            post_money_valuation=None,
        )
        assert rnd_partial.post_money_valuation is None

    def test_unknown_investor_identity(self) -> None:
        """Support participants where individual or syndicate identity is undisclosed."""
        p = FundingParticipant(
            round_id="rnd_01",
            investor_id=None,  # Unknown / undisclosed investor entity
            investor_name="Undisclosed Syndicate",
            disclosed_commitment=250000.0,
        )
        assert p.investor_id is None
        assert p.investor_name == "Undisclosed Syndicate"

    def test_repeated_announcement_ingestion(
        self, memory_storage: SQLiteStorage, sample_company: CompanyEntity
    ) -> None:
        """Repeated ingestion of the exact same round is idempotent without duplication."""
        rnd = FundingRound(
            round_id="rnd_repeat_01",
            company_id=sample_company.entity_id,
            round_type=FundingRoundType.SEED,
            announced_date=date(2024, 1, 1),
            disclosed_amount=1000000.0,
        )
        service = DealsIntelligenceService(memory_storage)
        service.record_funding_round(rnd)
        service.record_funding_round(rnd)

        rounds = memory_storage.get_funding_rounds_for_company(sample_company.entity_id)
        assert len(rounds) == 1

    def test_conflicting_round_amounts(
        self, memory_storage: SQLiteStorage, sample_company: CompanyEntity
    ) -> None:
        """Conflicting reported amounts for the same round generate an explicit DealConflict."""
        service = DealsIntelligenceService(memory_storage)

        rnd_source_a = FundingRound(
            round_id="rnd_conflict_01",
            company_id=sample_company.entity_id,
            round_type=FundingRoundType.SERIES_A,
            announced_date=date(2024, 6, 1),
            disclosed_amount=10000000.0,
            source_reference_ids=["Press Release Alpha"],
        )
        service.record_funding_round(rnd_source_a)

        # Incoming report claims round was $12M instead of $10M
        rnd_source_b = FundingRound(
            round_id="rnd_conflict_01",
            company_id=sample_company.entity_id,
            round_type=FundingRoundType.SERIES_A,
            announced_date=date(2024, 6, 1),
            disclosed_amount=12000000.0,
            source_reference_ids=["Regulatory Form D Filing"],
        )
        reconciled, conflicts = service.record_funding_round(rnd_source_b)

        assert len(conflicts) > 0
        conflict = conflicts[0]
        assert conflict.field_name == "disclosed_amount"
        assert "10,000,000" in str(conflict.observed_value_a)
        assert "12,000,000" in str(conflict.observed_value_b)

        # Stored conflict records can be retrieved
        stored_conflicts = memory_storage.get_deal_conflicts_for_event("rnd_conflict_01")
        assert len(stored_conflicts) > 0


# ── 2. M&A and Transaction Tests ────────────────────────────────────────────

class TestTransactions:
    """Tests for M&A, divestitures, exits, and corporate control events."""

    def test_announced_vs_completed_deal(
        self, memory_storage: SQLiteStorage, sample_company: CompanyEntity, sample_investor_org: CompanyEntity
    ) -> None:
        """Distinguish announced versus legally completed deals."""
        tx_announced = TransactionEvent(
            transaction_id="tx_ma_01",
            transaction_type=TransactionType.ACQUISITION,
            acquirer_id=sample_investor_org.entity_id,
            target_id=sample_company.entity_id,
            announced_date=date(2025, 2, 1),
            actual_completion_date=None,
            status=TransactionStatus.ANNOUNCED,
            disclosed_value=50000000.0,
        )
        service = DealsIntelligenceService(memory_storage)
        service.record_transaction(tx_announced)

        fetched = memory_storage.get_transaction("tx_ma_01")
        assert fetched.status == TransactionStatus.ANNOUNCED
        assert fetched.actual_completion_date is None

        # When completion occurs, status updates safely
        tx_completed = TransactionEvent(
            transaction_id="tx_ma_01",
            transaction_type=TransactionType.ACQUISITION,
            acquirer_id=sample_investor_org.entity_id,
            target_id=sample_company.entity_id,
            announced_date=date(2025, 2, 1),
            actual_completion_date=date(2025, 3, 15),
            status=TransactionStatus.COMPLETED,
            disclosed_value=50000000.0,
        )
        service.record_transaction(tx_completed)
        fetched_after = memory_storage.get_transaction("tx_ma_01")
        assert fetched_after.status == TransactionStatus.COMPLETED
        assert fetched_after.actual_completion_date == date(2025, 3, 15)

    def test_terminated_and_withdrawn_transactions(
        self, memory_storage: SQLiteStorage, sample_company: CompanyEntity
    ) -> None:
        """Transactions marked TERMINATED or WITHDRAWN preserve their termination reason."""
        tx = TransactionEvent(
            transaction_id="tx_terminated_01",
            transaction_type=TransactionType.ACQUISITION,
            target_id=sample_company.entity_id,
            status=TransactionStatus.TERMINATED,
            termination_reason="Failed regulatory anti-trust approval",
        )
        memory_storage.save_transaction(tx)
        fetched = memory_storage.get_transaction("tx_terminated_01")
        assert fetched.status == TransactionStatus.TERMINATED
        assert fetched.termination_reason == "Failed regulatory anti-trust approval"

    def test_unknown_deal_value_remains_none(
        self, memory_storage: SQLiteStorage, sample_company: CompanyEntity
    ) -> None:
        """Undisclosed deal consideration must remain None, never 0."""
        tx = TransactionEvent(
            transaction_id="tx_undisclosed_01",
            transaction_type=TransactionType.ACQUISITION,
            target_id=sample_company.entity_id,
            disclosed_value=None,
            consideration_type=ConsiderationType.UNDISCLOSED,
        )
        memory_storage.save_transaction(tx)
        fetched = memory_storage.get_transaction("tx_undisclosed_01")
        assert fetched.disclosed_value is None

    def test_mixed_consideration_details(
        self, memory_storage: SQLiteStorage, sample_company: CompanyEntity
    ) -> None:
        """Mixed consideration records cash, stock, and earnout components."""
        tx = TransactionEvent(
            transaction_id="tx_mixed_01",
            transaction_type=TransactionType.ACQUISITION,
            target_id=sample_company.entity_id,
            disclosed_value=100000000.0,
            consideration_type=ConsiderationType.MIXED,
            consideration_details={"cash": 60000000.0, "stock": 40000000.0},
        )
        memory_storage.save_transaction(tx)
        fetched = memory_storage.get_transaction("tx_mixed_01")
        assert fetched.consideration_type == ConsiderationType.MIXED
        assert fetched.consideration_details["cash"] == 60000000.0

    def test_acquirer_and_target_identity_collisions(self) -> None:
        """Target and acquirer must remain distinct entities."""
        tx = TransactionEvent(
            transaction_id="tx_col_01",
            transaction_type=TransactionType.ACQUISITION,
            acquirer_id="ent_acquirer_123",
            target_id="ent_target_456",
        )
        assert tx.acquirer_id != tx.target_id

    def test_conflicting_dates_and_statuses(
        self, memory_storage: SQLiteStorage, sample_company: CompanyEntity
    ) -> None:
        """Contradictory deal statuses (e.g. COMPLETED vs TERMINATED) generate an explicit conflict."""
        service = DealsIntelligenceService(memory_storage)
        tx1 = TransactionEvent(
            transaction_id="tx_conflict_status_01",
            transaction_type=TransactionType.ACQUISITION,
            target_id=sample_company.entity_id,
            status=TransactionStatus.COMPLETED,
            source_reference_ids=["Source Alpha"],
        )
        service.record_transaction(tx1)

        tx2 = TransactionEvent(
            transaction_id="tx_conflict_status_01",
            transaction_type=TransactionType.ACQUISITION,
            target_id=sample_company.entity_id,
            status=TransactionStatus.TERMINATED,
            source_reference_ids=["Source Beta"],
        )
        reconciled, conflicts = service.record_transaction(tx2)

        assert len(conflicts) > 0
        assert conflicts[0].field_name == "status"


# ── 3. Relationship and Graph Tests ─────────────────────────────────────────

class TestRelationshipsAndGraph:
    """Tests for investor vs fund differentiation, parent/subsidiary, and bounded traversal."""

    def test_investor_org_vs_fund_relationship(
        self,
        memory_storage: SQLiteStorage,
        sample_company: CompanyEntity,
        sample_investor_org: CompanyEntity,
        sample_fund: CompanyEntity,
    ) -> None:
        """Distinguish investor organization from the fund vehicle it manages."""
        # Management relationship: Organization -> Fund
        rel_mgmt = EntityRelationship(
            source_entity_id=sample_investor_org.entity_id,
            relationship_type=RelationshipType.FUND_MANAGER,
            target_entity_id=sample_fund.entity_id,
        )
        # Investment relationship: Fund -> Company
        rel_inv = EntityRelationship(
            source_entity_id=sample_fund.entity_id,
            relationship_type=RelationshipType.INVESTOR,
            target_entity_id=sample_company.entity_id,
        )
        memory_storage.save_relationship(rel_mgmt)
        memory_storage.save_relationship(rel_inv)

        graph = RelationshipGraphEngine(memory_storage)
        funds = graph.get_funds_managed_by(sample_investor_org.entity_id)
        assert len(funds) == 1
        assert funds[0]["fund_id"] == sample_fund.entity_id

    def test_cyclic_graph_relationships_and_cycle_prevention(
        self, memory_storage: SQLiteStorage
    ) -> None:
        """Verify cyclic corporate relationships are bounded and do not cause infinite loops."""
        ent_a = CompanyEntity(entity_id="ent_cycle_a", legal_name="Company A", jurisdiction="US")
        ent_b = CompanyEntity(entity_id="ent_cycle_b", legal_name="Company B", jurisdiction="US")
        ent_c = CompanyEntity(entity_id="ent_cycle_c", legal_name="Company C", jurisdiction="US")
        memory_storage.save_entity(ent_a)
        memory_storage.save_entity(ent_b)
        memory_storage.save_entity(ent_c)

        # Create cycle A -> B -> C -> A
        memory_storage.save_relationship(EntityRelationship(source_entity_id="ent_cycle_a", relationship_type=RelationshipType.AFFILIATE, target_entity_id="ent_cycle_b"))
        memory_storage.save_relationship(EntityRelationship(source_entity_id="ent_cycle_b", relationship_type=RelationshipType.AFFILIATE, target_entity_id="ent_cycle_c"))
        memory_storage.save_relationship(EntityRelationship(source_entity_id="ent_cycle_c", relationship_type=RelationshipType.AFFILIATE, target_entity_id="ent_cycle_a"))

        graph = RelationshipGraphEngine(memory_storage)
        paths = graph.find_paths_between("ent_cycle_a", "ent_cycle_c", max_depth=3)
        assert len(paths) >= 1
        # Traversal successfully halts without recursion error

    def test_bounded_graph_traversal_depth(
        self, memory_storage: SQLiteStorage
    ) -> None:
        """Graph traversal strictly respects max_depth limit."""
        for i in range(1, 6):
            memory_storage.save_entity(CompanyEntity(entity_id=f"ent_node_{i}", legal_name=f"Node {i}", jurisdiction="US"))

        for i in range(1, 5):
            memory_storage.save_relationship(EntityRelationship(
                source_entity_id=f"ent_node_{i}",
                relationship_type=RelationshipType.SUBSIDIARY,
                target_entity_id=f"ent_node_{i+1}",
            ))

        graph = RelationshipGraphEngine(memory_storage)
        # Depth 2 cannot reach Node 5 from Node 1
        paths_depth_2 = graph.find_paths_between("ent_node_1", "ent_node_5", max_depth=2)
        assert len(paths_depth_2) == 0

        # Depth 4 can reach Node 5
        paths_depth_4 = graph.find_paths_between("ent_node_1", "ent_node_5", max_depth=4)
        assert len(paths_depth_4) == 1

    def test_unsupported_ownership_percentage_claims(self) -> None:
        """Ownership percentage cannot be inferred without explicit transaction support."""
        p = FundingParticipant(
            round_id="rnd_01",
            investor_name="Generic Investor",
            disclosed_commitment=1000000.0,
            implied_ownership_pct=None,
        )
        assert p.implied_ownership_pct is None

    def test_graph_queries_investors_portfolio_acquisitions_syndicate(
        self,
        memory_storage: SQLiteStorage,
        sample_company: CompanyEntity,
        sample_investor_org: CompanyEntity,
    ) -> None:
        """Test investor lookup, portfolio lookup, acquisitions, and syndicate co-investors."""
        inv2 = CompanyEntity(entity_id="ent_inv_2", legal_name="Beta Syndicate LLC", jurisdiction="US")
        co2 = CompanyEntity(entity_id="ent_co_2", legal_name="Second Portfolio Co", jurisdiction="US")
        memory_storage.save_entity(inv2)
        memory_storage.save_entity(co2)

        # Round 1 for sample_company with sample_investor_org and inv2
        p1 = FundingParticipant(round_id="rnd_g_1", investor_id=sample_investor_org.entity_id, is_lead=True)
        p2 = FundingParticipant(round_id="rnd_g_1", investor_id=inv2.entity_id, is_lead=False)
        r1 = FundingRound(
            round_id="rnd_g_1",
            company_id=sample_company.entity_id,
            round_type=FundingRoundType.SEED,
            participants=[p1, p2],
        )
        memory_storage.save_funding_round(r1)

        # Round 2 for co2 with sample_investor_org and inv2
        p3 = FundingParticipant(round_id="rnd_g_2", investor_id=sample_investor_org.entity_id, is_lead=True)
        p4 = FundingParticipant(round_id="rnd_g_2", investor_id=inv2.entity_id, is_lead=False)
        r2 = FundingRound(
            round_id="rnd_g_2",
            company_id=co2.entity_id,
            round_type=FundingRoundType.SERIES_A,
            participants=[p3, p4],
        )
        memory_storage.save_funding_round(r2)

        # Acquisition by sample_company of co2
        tx = TransactionEvent(
            transaction_id="tx_g_acq",
            transaction_type=TransactionType.ACQUISITION,
            acquirer_id=sample_company.entity_id,
            target_id=co2.entity_id,
            status=TransactionStatus.COMPLETED,
            disclosed_value=25000000.0,
        )
        memory_storage.save_transaction(tx)

        graph = RelationshipGraphEngine(memory_storage)

        # 1. Investors for company
        investors = graph.get_investors_for_company(sample_company.entity_id)
        assert len(investors) == 2
        assert any(i["investor_id"] == sample_investor_org.entity_id for i in investors)

        # 2. Portfolio for investor
        portfolio = graph.get_portfolio_for_investor(sample_investor_org.entity_id)
        assert len(portfolio) == 2

        # 3. Acquisitions for entity
        acquisitions = graph.get_acquisitions_for_entity(sample_company.entity_id)
        assert len(acquisitions) == 1
        assert acquisitions[0]["target_id"] == co2.entity_id

        # 4. Syndicate co-investors
        syndicate = graph.get_syndicate_co_investors(sample_investor_org.entity_id)
        assert len(syndicate) >= 1
        assert syndicate[0]["investor_id"] == inv2.entity_id

    def test_reconciliation_date_and_currency_conflicts(self) -> None:
        """Verify event reconciler identifies date discrepancies and currency conflicts."""
        reconciler = EventReconciler()

        r1 = FundingRound(
            round_id="rnd_recon_1",
            company_id="ent_c1",
            round_type=FundingRoundType.SERIES_A,
            announced_date=date(2025, 1, 1),
            disclosed_amount=5000000.0,
            currency=Currency.USD,
            source_reference_ids=["Source 1"],
        )
        r2 = FundingRound(
            round_id="rnd_recon_1",
            company_id="ent_c1",
            round_type=FundingRoundType.SERIES_A,
            announced_date=date(2025, 1, 15),  # Date mismatch
            disclosed_amount=5000000.0,
            currency=Currency.EUR,  # Currency mismatch
            source_reference_ids=["Source 2"],
        )

        reconciled, conflicts = reconciler.reconcile_funding_rounds(r1, r2)
        conflict_fields = {c.field_name for c in conflicts}
        assert "announced_date" in conflict_fields
        assert "currency" in conflict_fields


# ── 4. Provenance and Source Behavior Tests ─────────────────────────────────

class TestProvenanceAndSourceBehavior:
    """Tests for offline default behavior, explicit opt-ins, and evidence explanations."""

    def test_offline_mode_zero_network_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ensure offline ingestion makes strictly zero network calls."""
        import urllib.request
        def mock_urlopen(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("CRITICAL: Network call attempted in offline test mode!")

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

        runner = CliRunner()
        result = runner.invoke(cli, ["deals", "ingest", "sec"])
        assert result.exit_code != 0
        assert "Offline mode enforced" in result.output

    def test_explain_deal_event_missing_fields_and_provenance(
        self, memory_storage: SQLiteStorage, sample_company: CompanyEntity
    ) -> None:
        """explain_deal_event identifies missing valuation fields and primary source reference."""
        rnd = FundingRound(
            round_id="rnd_test_exp_01",
            company_id=sample_company.entity_id,
            round_type=FundingRoundType.SEED,
            disclosed_amount=2000000.0,
            pre_money_valuation=None,
            post_money_valuation=None,
            source_reference_ids=["SEC Form D 000123"],
            verification_status=DealVerificationStatus.VERIFIED_PRIMARY_SOURCE,
        )
        service = DealsIntelligenceService(memory_storage)
        service.record_funding_round(rnd)

        explanation = service.explain_deal_event("rnd_test_exp_01")
        assert explanation["event_id"] == "rnd_test_exp_01"
        assert explanation["primary_source"] == "SEC Form D 000123"
        assert "pre_money_valuation" in explanation["missing_fields"]
        assert "post_money_valuation" in explanation["missing_fields"]


# ── 5. Storage and Migration Tests ──────────────────────────────────────────

class TestStorageAndMigrations:
    """Tests for SQLite schema migration V5 and database operations."""

    def test_fresh_database_creation(self) -> None:
        """Verify clean initialization of a fresh database with all V1-V5 migrations."""
        storage = SQLiteStorage(":memory:")
        storage.initialize()
        cursor = storage.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}

        assert "funding_rounds" in tables
        assert "funding_participants" in tables
        assert "transactions" in tables
        assert "deal_conflicts" in tables

    def test_repeated_ingestion_idempotency(
        self, memory_storage: SQLiteStorage, sample_company: CompanyEntity
    ) -> None:
        """Verify repeated ingestion of same round and transaction is idempotent."""
        service = DealsIntelligenceService(memory_storage)
        rnd = FundingRound(
            round_id="rnd_idem_01",
            company_id=sample_company.entity_id,
            round_type=FundingRoundType.SEED,
            disclosed_amount=500000.0,
        )
        tx = TransactionEvent(
            transaction_id="tx_idem_01",
            transaction_type=TransactionType.ACQUISITION,
            target_id=sample_company.entity_id,
            disclosed_value=5000000.0,
        )

        service.record_funding_round(rnd)
        service.record_funding_round(rnd)
        service.record_transaction(tx)
        service.record_transaction(tx)

        overview = service.get_company_deals_overview(sample_company.entity_id)
        assert overview.funding_rounds_count == 1
        assert len(overview.transactions_as_target) == 1


# ── 6. CLI Command Tests ────────────────────────────────────────────────────

class TestCliCommands:
    """Tests for Click CLI deals commands using runner and fixtures."""

    def test_cli_deals_ingest_and_company_overview(
        self, tmp_path: Path
    ) -> None:
        """Test deals ingest fixture followed by company overview and funding list."""
        db_path = tmp_path / "cli_test.db"
        fixture_path = "tests/fixtures/deals/sample_funding_and_transactions.json"

        runner = CliRunner(env={"OPENDEAL_DATABASE_PATH": str(db_path)})

        # 1. Ingest fixture
        res_ingest = runner.invoke(cli, ["deals", "ingest", "fixture", "--file", fixture_path])
        assert res_ingest.exit_code == 0
        assert "Fixture Ingestion Complete" in res_ingest.output

        # 2. Company overview
        res_company = runner.invoke(cli, ["deals", "company", "ent_apex_001"])
        assert res_company.exit_code == 0
        assert "Apex Neural Technologies Inc" in res_company.output
        assert "17,500,000.00 USD" in res_company.output

        # 3. Company overview JSON
        res_json = runner.invoke(cli, ["deals", "company", "ent_apex_001", "--format", "json"])
        assert res_json.exit_code == 0
        data = json.loads(res_json.output)
        assert data["funding_rounds_count"] == 2

        # 4. Funding rounds
        res_funding = runner.invoke(cli, ["deals", "funding", "ent_apex_001"])
        assert res_funding.exit_code == 0
        assert "rnd_apex_seed" in res_funding.output

        # 5. Acquisitions
        res_acq = runner.invoke(cli, ["deals", "acquisitions", "ent_apex_001"])
        assert res_acq.exit_code == 0
        assert "tx_titan_apex_acq" in res_acq.output

        # 6. Event details
        res_ev = runner.invoke(cli, ["deals", "event", "rnd_apex_seed"])
        assert res_ev.exit_code == 0
        assert "Horizon Venture Partners LLC" in res_ev.output

        # 7. Investors
        res_inv = runner.invoke(cli, ["deals", "investors", "ent_apex_001"])
        assert res_inv.exit_code == 0
        assert "Horizon Venture Partners LLC" in res_inv.output

        # 8. Timeline
        res_time = runner.invoke(cli, ["deals", "timeline", "ent_apex_001"])
        assert res_time.exit_code == 0
        assert "TRANSACTION" in res_time.output
        assert "FUNDING" in res_time.output

        # 9. Explain
        res_exp = runner.invoke(cli, ["deals", "explain", "rnd_apex_series_a"])
        assert res_exp.exit_code == 0
        assert "pre_money_valuation" in res_exp.output

        # 10. Search
        res_search = runner.invoke(cli, ["deals", "search"])
        assert res_search.exit_code == 0
        assert "tx_titan_apex_acq" in res_search.output
