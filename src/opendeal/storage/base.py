"""Abstract storage interface defining the persistence contract."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from opendeal.entities.models import CompanyEntity, EntityRelationship, RelationshipType, LegalIdentifier
from opendeal.evidence.models import SourceReference, EvidenceRecord
from opendeal.facts.models import FactRecord, ValidationResult


class StorageInterface(ABC):
    """Abstract persistence interface for OpenDeal.
    
    Guarantees that higher-level pipelines and valuation engines are strictly
    independent of specific database backends (SQLite, PostgreSQL, etc.).
    """

    @abstractmethod
    def initialize(self) -> None:
        """Initialize database schemas, run migrations, and verify connectivity."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Close database connection and free resources."""
        pass

    # ── Entity Operations ───────────────────────────────────────────────────

    @abstractmethod
    def save_entity(self, entity: CompanyEntity) -> None:
        """Persist or update a company entity and its legal identifiers."""
        pass

    @abstractmethod
    def get_entity(self, entity_id: str) -> Optional[CompanyEntity]:
        """Retrieve a company entity by its internal entity_id."""
        pass

    @abstractmethod
    def find_entity_by_identifier(self, scheme: str, identifier: str) -> Optional[CompanyEntity]:
        """Find a company entity by a specific legal identifier (e.g. CIK, LEI)."""
        pass

    @abstractmethod
    def find_entity_by_ticker(self, ticker: str) -> Optional[CompanyEntity]:
        """Find a company entity by its stock ticker."""
        pass

    @abstractmethod
    def list_entities(self, limit: int = 100) -> List[CompanyEntity]:
        """Retrieve a list of company entities."""
        pass

    @abstractmethod
    def get_legal_identifiers(self, entity_id: str) -> List[LegalIdentifier]:
        """Retrieve all legal identifiers for a company entity."""
        pass

    @abstractmethod
    def save_relationship(self, relationship: EntityRelationship) -> None:
        """Persist an evidence-backed relationship between corporate entities."""
        pass

    @abstractmethod
    def get_relationships(
        self, entity_id: str, relationship_type: Optional[RelationshipType] = None
    ) -> List[EntityRelationship]:
        """Retrieve relationships where entity_id is either source or target."""
        pass

    # ── Source & Evidence Operations ────────────────────────────────────────

    @abstractmethod
    def save_source(self, source: SourceReference) -> None:
        """Persist a source document reference."""
        pass

    @abstractmethod
    def get_source(self, source_id: str) -> Optional[SourceReference]:
        """Retrieve a source reference by ID."""
        pass

    @abstractmethod
    def save_evidence(self, evidence: EvidenceRecord) -> None:
        """Persist an immutable piece of extracted evidence."""
        pass

    @abstractmethod
    def get_evidence(self, evidence_id: str) -> Optional[EvidenceRecord]:
        """Retrieve an evidence record by ID."""
        pass

    # ── Fact Operations ─────────────────────────────────────────────────────

    @abstractmethod
    def save_fact(self, fact: FactRecord) -> None:
        """Persist a financial fact record while preserving lineage and competing evidence."""
        pass

    @abstractmethod
    def get_fact(self, fact_id: str) -> Optional[FactRecord]:
        """Retrieve a fact record by its unique fact_id."""
        pass

    @abstractmethod
    def get_facts_for_entity(
        self,
        entity_id: str,
        canonical_variable: Optional[str] = None,
        fiscal_year: Optional[int] = None,
    ) -> List[FactRecord]:
        """Query facts for an entity, optionally filtering by variable and fiscal year."""
        pass

    def list_facts(
        self,
        entity_id: Optional[str] = None,
        canonical_variable: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[FactRecord]:
        """Query facts across or within entities with optional filtering."""
        return []

    def get_system_stats(self) -> Dict[str, int]:
        """Get counts of indexed records across domains."""
        return {}

    # ── Validation Operations ───────────────────────────────────────────────

    @abstractmethod
    def save_validation_result(self, result: ValidationResult) -> None:
        """Persist a validation evaluation result."""
        pass

    @abstractmethod
    def get_validation_results(self, target_id: str) -> List[ValidationResult]:
        """Retrieve all validation findings recorded for a specific fact or entity."""
        pass

    # ── Research Operations ─────────────────────────────────────────────────

    @abstractmethod
    def save_research_brief(self, brief: Dict[str, Any]) -> None:
        """Persist an evidence-grounded research brief."""
        pass

    @abstractmethod
    def get_research_brief(self, research_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a research brief by its unique ID."""
        pass

    @abstractmethod
    def get_research_briefs_for_entity(self, entity_id: str) -> List[Dict[str, Any]]:
        """Retrieve all research briefs generated for an entity."""
        pass

    @abstractmethod
    def get_evidence_for_entity(self, entity_id: str) -> List[EvidenceRecord]:
        """Retrieve all evidence records linked to an entity's facts or relationships."""
        pass

    # ── Target Discovery & Watchlist Operations (Phase 6) ───────────────────

    @abstractmethod
    def save_discovery_run(self, result: Any) -> None:
        """Persist a target discovery run and its screened results."""
        pass

    @abstractmethod
    def get_discovery_run(self, run_id: str) -> Optional[Any]:
        """Retrieve a discovery run by its unique run_id."""
        pass

    @abstractmethod
    def list_discovery_runs(self, limit: int = 50) -> List[Any]:
        """List recent discovery screening runs."""
        pass

    @abstractmethod
    def save_target_list(self, target_list: Any) -> None:
        """Persist or update a user-defined target list."""
        pass

    @abstractmethod
    def get_target_list(self, list_id: str) -> Optional[Any]:
        """Retrieve a target list by list_id."""
        pass

    @abstractmethod
    def list_target_lists(self) -> List[Any]:
        """List all saved target lists with member counts."""
        pass

    @abstractmethod
    def delete_target_list(self, list_id: str) -> bool:
        """Delete a target list and its memberships."""
        pass

    @abstractmethod
    def save_target_list_member(self, item: Any, history: Any) -> None:
        """Add a company entity to a target list and record initial audit history."""
        pass

    @abstractmethod
    def update_target_list_member(self, item: Any, history: Any) -> None:
        """Update a member's status/notes and record audit history."""
        pass

    @abstractmethod
    def remove_target_list_member(self, list_id: str, entity_id: str) -> bool:
        """Remove a member from a target list."""
        pass

    @abstractmethod
    def get_target_list_members(self, list_id: str) -> List[Any]:
        """Retrieve all members of a target list."""
        pass

    @abstractmethod
    def is_target_list_member(self, list_id: str, entity_id: str) -> bool:
        """Check if an entity is already a member of a target list."""
        pass

    @abstractmethod
    def get_target_list_history(self, list_id: str, entity_id: Optional[str] = None) -> List[Any]:
        """Retrieve audit history records for a target list or specific member."""
        pass

    # ── Funding & Transaction Deal Intelligence (Phase 7) ───────────────────

    @abstractmethod
    def save_funding_round(self, funding_round: Any) -> None:
        """Persist or update a funding round and its participant links."""
        pass

    @abstractmethod
    def get_funding_round(self, round_id: str) -> Optional[Any]:
        """Retrieve a funding round by its unique round_id."""
        pass

    @abstractmethod
    def get_funding_rounds_for_company(self, company_id: str) -> List[Any]:
        """Retrieve all funding rounds recorded for a company entity."""
        pass

    @abstractmethod
    def save_funding_participant(self, participant: Any) -> None:
        """Persist a funding participant commitment record."""
        pass

    @abstractmethod
    def get_funding_participants_for_round(self, round_id: str) -> List[Any]:
        """Retrieve all participating investors for a funding round."""
        pass

    @abstractmethod
    def get_funding_participants_for_investor(self, investor_id: str) -> List[Any]:
        """Retrieve all investments participated in by an investor."""
        pass

    @abstractmethod
    def save_transaction(self, transaction: Any) -> None:
        """Persist or update an M&A, divestiture, or corporate control transaction."""
        pass

    @abstractmethod
    def get_transaction(self, transaction_id: str) -> Optional[Any]:
        """Retrieve a transaction event by transaction_id."""
        pass

    @abstractmethod
    def get_transactions_for_target(self, target_id: str) -> List[Any]:
        """Retrieve all transactions where target_id was the target entity."""
        pass

    @abstractmethod
    def get_transactions_for_acquirer(self, acquirer_id: str) -> List[Any]:
        """Retrieve all transactions where acquirer_id was the buyer/acquirer."""
        pass

    @abstractmethod
    def get_transactions_for_seller(self, seller_id: str) -> List[Any]:
        """Retrieve all transactions where seller_id divested assets or subsidiaries."""
        pass

    @abstractmethod
    def get_transactions_for_entity(self, entity_id: str) -> List[Any]:
        """Retrieve all transactions involving an entity as acquirer, target, or seller."""
        pass

    @abstractmethod
    def save_deal_conflict(self, conflict: Any) -> None:
        """Persist an audit log entry for a transaction data conflict."""
        pass

    @abstractmethod
    def get_deal_conflicts_for_event(self, target_event_id: str) -> List[Any]:
        """Retrieve recorded conflicting data points for a specific event."""
        pass

    @abstractmethod
    def search_funding_rounds(
        self,
        company_id: Optional[str] = None,
        min_amount: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> List[Any]:
        """Search funding rounds by company or minimum disclosed amount."""
        pass

    @abstractmethod
    def search_transactions(
        self,
        company_id: Optional[str] = None,
        status: Optional[str] = None,
        min_value: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> List[Any]:
        """Search transactions by target/acquirer, status, or minimum disclosed value."""
        pass

    # ── Workflow Orchestration & Agent Auditing (Phase 9) ───────────────────

    @abstractmethod
    def save_workflow_definition(self, defn: Any) -> None:
        """Persist or update a registered workflow definition."""
        pass

    @abstractmethod
    def get_workflow_definition(self, workflow_id: str) -> Optional[Any]:
        """Retrieve a workflow definition by its unique workflow_id."""
        pass

    @abstractmethod
    def list_workflow_definitions(self) -> List[Any]:
        """List all registered workflow definitions."""
        pass

    @abstractmethod
    def save_workflow_run(self, run: Any) -> None:
        """Persist or update a workflow execution run record."""
        pass

    @abstractmethod
    def get_workflow_run(self, run_id: str) -> Optional[Any]:
        """Retrieve a workflow run record by its run_id."""
        pass

    @abstractmethod
    def list_workflow_runs(
        self,
        workflow_id: Optional[str] = None,
        state: Optional[str] = None,
        limit: int = 50,
    ) -> List[Any]:
        """List recent workflow runs, optionally filtered by workflow or state."""
        pass

    @abstractmethod
    def claim_queued_workflow_run(self, run_id: str, started_at: str) -> bool:
        """Atomically claim an eligible queued workflow run. Returns True if claimed, False otherwise."""
        pass

    @abstractmethod
    def claim_approval_workflow_run(self, run_id: str) -> bool:
        """Atomically claim a workflow run paused in WAITING_FOR_APPROVAL to resume execution."""
        pass

    @abstractmethod
    def save_task_run(self, task: Any) -> None:
        """Persist or update a task execution run record."""
        pass

    @abstractmethod
    def get_task_runs_for_workflow(self, run_id: str) -> List[Any]:
        """Retrieve all task run records associated with a workflow run."""
        pass

    @abstractmethod
    def save_approval_request(self, approval: Any) -> None:
        """Persist or update a human-in-the-loop approval request."""
        pass

    @abstractmethod
    def get_approval_request(self, approval_id: str) -> Optional[Any]:
        """Retrieve an approval request by its unique approval_id."""
        pass

    @abstractmethod
    def list_pending_approvals(self, run_id: Optional[str] = None) -> List[Any]:
        """List pending approval requests requiring user intervention."""
        pass

    @abstractmethod
    def record_workflow_audit_event(self, event: Any) -> None:
        """Append an immutable audit event to the workflow execution trail."""
        pass

    save_audit_event = record_workflow_audit_event

    @abstractmethod
    def get_workflow_audit_trail(self, run_id: str) -> List[Any]:
        """Retrieve the complete chronological audit trail for a workflow run."""
        pass

    get_audit_events_for_run = get_workflow_audit_trail

    @abstractmethod
    def save_workflow_snapshot(self, snapshot: Any) -> None:
        """Persist a dataset snapshot for monitoring and drift detection."""
        pass

    @abstractmethod
    def get_latest_workflow_snapshot(self, dataset_key: str) -> Optional[Any]:
        """Retrieve the most recent dataset snapshot for change comparison."""
        pass


