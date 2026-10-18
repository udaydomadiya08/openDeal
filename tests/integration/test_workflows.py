"""Integration tests for Phase 9: End-to-End Workflow Orchestration, Domain Agents & UI."""

from datetime import datetime, timezone
import pytest
from starlette.testclient import TestClient

from opendeal.agents.approvals import ApprovalManager
from opendeal.agents.audit import AuditInspector
from opendeal.agents.contracts import (
    ApprovalStatus,
    PermissionCategory,
    TaskDefinition,
    TaskState,
    WorkflowDefinition,
    WorkflowState,
)
from opendeal.agents.planner import DeterministicPlanner
from opendeal.agents.policies import PolicyEngine
from opendeal.agents.registry import AgentRegistry
from opendeal.agents.runner import WorkflowRunner
from opendeal.agents.workflows.definitions import (
    COMPANY_DOSSIER_WORKFLOW,
    DATA_INTEGRITY_AUDIT_WORKFLOW,
    DEAL_MONITORING_WORKFLOW,
    TARGET_SCREENING_WORKFLOW,
    VALUATION_ANALYSIS_WORKFLOW,
    register_standard_workflows,
)
from opendeal.core.types import (
    Currency,
    EntityType,
    FactState,
    FiscalPeriodType,
    Sign,
    SourceType,
    UnitScale,
)
from opendeal.deals.contracts import (
    FundingRound,
    FundingRoundType,
    TransactionEvent,
    TransactionStatus,
    TransactionType,
)
from opendeal.entities.models import CompanyEntity
from opendeal.evidence.models import EvidenceRecord, SourceReference
from opendeal.facts.models import FactRecord
from opendeal.storage.sqlite import SQLiteStorage
from opendeal.ui.app import create_app


@pytest.fixture
def memory_storage() -> SQLiteStorage:
    """Create a fully migrated SQLite storage."""
    storage = SQLiteStorage(":memory:")
    storage.initialize()
    register_standard_workflows(storage)
    return storage


@pytest.fixture
def populated_storage(memory_storage: SQLiteStorage) -> SQLiteStorage:
    """Populate storage with realistic entities, facts, evidence, and deal records."""
    # 1. Company Entity
    entity = CompanyEntity(
        entity_id="ENT_ACME_001",
        legal_name="Acme Industrial Corp",
        jurisdiction="US",
        ticker="ACME",
        is_active=True,
    )
    memory_storage.save_entity(entity)

    target_sub = CompanyEntity(
        entity_id="ENT_TARGET_SUB",
        legal_name="Target Sub Ltd",
        jurisdiction="US",
        is_active=True,
    )
    memory_storage.save_entity(target_sub)

    # 2. Source and Evidence
    source = SourceReference(
        source_id="SRC_10K_2023",
        source_type=SourceType.REGULATORY_FILING,
        source_url="https://www.sec.gov/edgar/data/0001/10k.htm",
        publisher="SEC EDGAR",
        content_hash="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
    )
    memory_storage.save_source(source)

    evidence = EvidenceRecord(
        evidence_id="EVID_REV_001",
        source_id="SRC_10K_2023",
        raw_content="Acme Industrial reported total revenue of $100,000,000 for fiscal 2023.",
        content_hash="1111111111111111111111111111111111111111111111111111111111111111",
        extraction_method="XBRL_TAG",
    )
    memory_storage.save_evidence(evidence)

    # 3. Facts
    revenue_fact = FactRecord(
        fact_id="FACT_REV_2023",
        entity_id="ENT_ACME_001",
        canonical_variable="REVENUE",
        fact_state=FactState.RAW_FACT,
        normalized_value=100000000.0,
        currency=Currency.USD,
        unit_scale=UnitScale.EXACT,
        sign=Sign.POSITIVE,
        fiscal_year=2023,
        fiscal_period=FiscalPeriodType.ANNUAL,
        accounting_standard="US-GAAP",
        evidence_ids=["EVID_REV_001"],
    )
    memory_storage.save_fact(revenue_fact)

    ebit_fact = FactRecord(
        fact_id="FACT_EBIT_2023",
        entity_id="ENT_ACME_001",
        canonical_variable="OPERATING_INCOME",
        fact_state=FactState.RAW_FACT,
        normalized_value=20000000.0,
        currency=Currency.USD,
        unit_scale=UnitScale.EXACT,
        sign=Sign.POSITIVE,
        fiscal_year=2023,
        fiscal_period=FiscalPeriodType.ANNUAL,
        accounting_standard="US-GAAP",
    )
    memory_storage.save_fact(ebit_fact)

    # 4. Deals
    round_record = FundingRound(
        round_id="RND_SERIES_B",
        company_id="ENT_ACME_001",
        round_type=FundingRoundType.SERIES_B,
        announced_date="2022-04-15",
        target_amount=15000000.0,
        raised_amount=15000000.0,
        currency="USD",
        investor_names=["Apex Ventures", "Summit Capital"],
        is_verified=True,
    )
    memory_storage.save_funding_round(round_record)

    tx_record = TransactionEvent(
        transaction_id="TX_ACQ_001",
        acquirer_id="ENT_ACME_001",
        target_id="ENT_TARGET_SUB",
        transaction_type=TransactionType.ACQUISITION,
        transaction_status=TransactionStatus.COMPLETED,
        announced_date="2023-01-10",
        closed_date="2023-03-01",
        announced_value=5000000.0,
        currency="USD",
        is_verified=True,
    )
    memory_storage.save_transaction(tx_record)

    return memory_storage


class TestEndToEndWorkflows:
    """Comprehensive integration tests executing all 5 standard Phase 9 workflows."""

    def test_workflow_1_company_dossier_success(self, populated_storage):
        """Workflow 1 resolves entity, checks quality, gathers deals, and generates brief."""
        runner = WorkflowRunner(storage=populated_storage)
        run = runner.execute_workflow(
            workflow_id="wf_company_dossier",
            inputs={"entity_id": "ENT_ACME_001"},
            initiator="analyst_test",
        )

        assert run.state == WorkflowState.SUCCEEDED
        assert "company_dossier" in run.outputs
        dossier = run.outputs["company_dossier"]
        assert dossier["entity"]["entity_id"] == "ENT_ACME_001"
        assert dossier["entity"]["legal_name"] == "Acme Industrial Corp"
        assert dossier["data_quality"]["quality_score"] > 0.8
        assert dossier["deal_intelligence"]["total_funding_rounds"] == 1
        assert dossier["deal_intelligence"]["total_transactions"] == 1
        assert "research_brief" in dossier

        # Check audit trail
        audit_events = populated_storage.get_audit_events_for_run(run.run_id)
        assert len(audit_events) >= 6
        event_types = {e["event_type"] for e in audit_events}
        assert "WORKFLOW_RUNNING" in event_types
        assert "TASK_STARTED" in event_types
        assert "TASK_SUCCEEDED" in event_types
        assert "WORKFLOW_SUCCEEDED" in event_types

    def test_workflow_1_unknown_entity_handled_gracefully(self, populated_storage):
        """Workflow 1 handles missing entity without crashing."""
        runner = WorkflowRunner(storage=populated_storage)
        run = runner.execute_workflow(
            workflow_id="wf_company_dossier",
            inputs={"entity_id": "ENT_NONEXISTENT"},
            initiator="analyst_test",
        )
        assert run.state == WorkflowState.FAILED
        assert "resolve_company" in run.error_message

    def test_workflow_2_target_screening(self, populated_storage):
        """Workflow 2 validates screening criteria and scores candidates deterministically."""
        runner = WorkflowRunner(storage=populated_storage)
        run = runner.execute_workflow(
            workflow_id="wf_target_screening",
            inputs={"criteria": {"sector": "Industrial", "min_revenue": 10000000}},
            initiator="screener_test",
        )

        assert run.state == WorkflowState.SUCCEEDED
        assert "candidate_ranking" in run.outputs
        screening_res = run.outputs["candidate_ranking"]
        assert "total_candidates_screened" in screening_res
        assert "shortlist" in screening_res

    def test_workflow_3_valuation_analysis_complete_inputs(self, populated_storage):
        """Workflow 3 evaluates readiness and runs deterministic DCF calculation."""
        # Provide explicit valuation assumptions
        inputs = {
            "entity_id": "ENT_ACME_001",
            "model_type": "DCF",
            "assumptions": {
                "forecast_revenue": [105000000.0, 110000000.0, 115000000.0, 120000000.0, 125000000.0],
                "ebit_margin": 0.20,
                "tax_rate": 0.25,
                "reinvestment_rate": 0.10,
                "wacc": 0.09,
                "terminal_growth_rate": 0.025,
                "cash": 15000000.0,
                "total_debt": 20000000.0,
                "shares_outstanding": 10000000.0,
            },
        }

        runner = WorkflowRunner(storage=populated_storage)
        run = runner.execute_workflow(
            workflow_id="wf_valuation_analysis",
            inputs=inputs,
            initiator="valuation_analyst",
        )

        assert run.state == WorkflowState.SUCCEEDED
        assert "valuation_readiness" in run.outputs
        assert run.outputs["valuation_readiness"]["is_ready_for_calculation"] is True
        assert "valuation_results" in run.outputs
        val_res = run.outputs["valuation_results"]
        assert val_res["status"] == "EXECUTED"
        assert val_res["enterprise_value"] > 0
        assert val_res["equity_value"] > 0
        assert val_res["per_share_value"] > 0

    def test_workflow_3_valuation_analysis_incomplete_inputs_partial_success(self, populated_storage):
        """Workflow 3 safely halts execution without guessing when inputs are incomplete."""
        inputs = {
            "entity_id": "ENT_ACME_001",
            "model_type": "DCF",
            "assumptions": {
                # Missing required revenue forecast and wacc!
                "tax_rate": 0.25,
            },
        }

        runner = WorkflowRunner(storage=populated_storage)
        run = runner.execute_workflow(
            workflow_id="wf_valuation_analysis",
            inputs=inputs,
            initiator="valuation_analyst",
        )

        # Readiness task succeeded, but execution task skipped/prevented
        assert run.state in (WorkflowState.PARTIALLY_SUCCEEDED, WorkflowState.SUCCEEDED)
        assert run.outputs["valuation_readiness"]["is_ready_for_calculation"] is False
        assert len(run.outputs["valuation_readiness"]["missing_inputs"]) > 0

    def test_workflow_4_deal_monitoring_detects_changes(self, populated_storage):
        """Workflow 4 compares local dataset snapshots to detect new funding and acquisitions."""
        # 1. First run: establishes initial snapshot
        runner = WorkflowRunner(storage=populated_storage)
        run_1 = runner.execute_workflow(
            workflow_id="wf_deal_monitoring",
            inputs={},
            initiator="monitoring_job",
        )
        assert run_1.state == WorkflowState.SUCCEEDED
        assert run_1.outputs["deal_changes"]["new_funding_count"] == 0

        # 2. Add a newly ingested transaction
        tx_new = TransactionEvent(
            transaction_id="TX_ACQ_NEW_002",
            acquirer_id="ENT_ACME_001",
            target_id="ENT_ACME_001",
            transaction_type=TransactionType.MERGER,
            transaction_status=TransactionStatus.ANNOUNCED,
            announced_date="2023-06-01",
            announced_value=12000000.0,
            currency="USD",
            is_verified=True,
        )
        populated_storage.save_transaction(tx_new)

        # 3. Second run: detects newly ingested transaction
        run_2 = runner.execute_workflow(
            workflow_id="wf_deal_monitoring",
            inputs={},
            initiator="monitoring_job",
        )
        assert run_2.state == WorkflowState.SUCCEEDED
        assert run_2.outputs["deal_changes"]["new_transactions_count"] == 1
        assert "TX_ACQ_NEW_002" in run_2.outputs["deal_changes"]["new_transaction_ids"]

    def test_workflow_5_data_integrity_audit(self, populated_storage):
        """Workflow 5 detects conflicting facts, unresolvable evidence, and missing fields read-only."""
        # Add a conflicting revenue fact
        conflicting_fact = FactRecord(
            fact_id="FACT_REV_CONFLICT_2023",
            entity_id="ENT_ACME_001",
            canonical_variable="REVENUE",
            fact_state=FactState.RAW_FACT,
            normalized_value=125000000.0,  # Differs from 100,000,000
            currency=Currency.USD,
            unit_scale=UnitScale.EXACT,
            sign=Sign.POSITIVE,
            fiscal_year=2023,
            fiscal_period=FiscalPeriodType.ANNUAL,
            metadata={"unresolvable_evidence_ids": ["EVID_DANGLING_999"]},
        )
        populated_storage.save_fact(conflicting_fact)

        runner = WorkflowRunner(storage=populated_storage)
        run = runner.execute_workflow(
            workflow_id="wf_data_integrity_audit",
            inputs={"entity_id": "ENT_ACME_001"},
            initiator="auditor_test",
        )

        assert run.state == WorkflowState.SUCCEEDED
        audit_res = run.outputs["integrity_audit_findings"]
        assert len(audit_res["conflicting_facts"]) >= 1
        assert "EVID_DANGLING_999" in audit_res["unresolvable_evidence_ids"]
        assert len(audit_res["severity_ranked_findings"]) >= 1
        assert audit_res["read_only_mode"] is True


class TestApprovalQueueAndDecisionFlow:
    """Test approval interception, approval recording, and authorization checks."""

    def test_approval_manager_flow(self, populated_storage):
        """ApprovalManager lists pending requests and allows approving or rejecting with rationale."""
        mgr = ApprovalManager(storage=populated_storage)

        # Create prerequisite workflow run to respect foreign keys
        populated_storage.save_workflow_run({
            "run_id": "run_approval_test",
            "workflow_id": "wf_target_screening",
            "version": "1.0.0",
            "state": "WAITING_FOR_APPROVAL",
        })

        # Request approval
        appr_id = mgr.request_approval(
            run_id="run_approval_test",
            action_type="WRITE_APPROVED_TARGET_LIST",
            action_payload={"candidates": ["ENT_ACME_001"]},
            scope="TARGET_LIST",
        )

        pending = mgr.list_pending_approvals()
        assert any(p["approval_id"] == appr_id for p in pending)

        # Approve with rationale
        approved = mgr.decide_approval(
            approval_id=appr_id,
            status=ApprovalStatus.APPROVED,
            decided_by="senior_analyst",
            rationale="Verified target financial eligibility",
        )
        assert approved.status == ApprovalStatus.APPROVED

        # Verify not in pending anymore
        assert not any(p["approval_id"] == appr_id for p in mgr.list_pending_approvals())


class TestAuditInspector:
    """Test chronological audit trail formatting and sensitive key redaction."""

    def test_audit_timeline_and_redaction(self, populated_storage):
        """Audit log displays execution events and redacts any secret tokens."""
        inspector = AuditInspector(storage=populated_storage)

        populated_storage.save_workflow_run({
            "run_id": "run_audit_test",
            "workflow_id": "wf_company_dossier",
            "version": "1.0.0",
            "state": "RUNNING",
        })

        populated_storage.save_audit_event({
            "event_id": "ev_1",
            "run_id": "run_audit_test",
            "task_name": "task_1",
            "event_type": "TASK_STARTED",
            "details": {"api_key": "SECRET_SHOULD_BE_REDACTED", "status": "ok"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        timeline = inspector.get_timeline("run_audit_test")
        assert len(timeline) == 1
        assert timeline[0]["details"]["api_key"] == "[REDACTED]"
        assert timeline[0]["details"]["status"] == "ok"


class TestDeterministicPlanner:
    """Test translation of natural language queries into validated workflow proposals."""

    def test_planner_proposes_valid_schema(self, populated_storage):
        """Planner identifies required workflow, inputs, permissions, and explains reason."""
        planner = DeterministicPlanner(storage=populated_storage)
        proposal = planner.propose_plan("Please compile a full company dossier on Acme Corp")

        assert proposal.is_valid is True
        assert proposal.workflow_id == "wf_company_dossier"
        assert PermissionCategory.READ_LOCAL_DATA in proposal.required_permissions
        assert PermissionCategory.GENERATE_RESEARCH in proposal.required_permissions
        assert len(proposal.tasks) == 4
        assert proposal.tasks[0].task_name == "resolve_company"

    def test_planner_rejects_unsupported_request(self, populated_storage):
        """Planner returns invalid proposal when intent cannot be matched."""
        planner = DeterministicPlanner(storage=populated_storage)
        proposal = planner.propose_plan("Order a pizza to my house")
        assert proposal.is_valid is False
        assert proposal.validation_status == "REJECTED"
        assert proposal.rejection_reason is not None


class TestWorkflowUIIntegration:
    """Verify Phase 8 Web UI endpoints for workflows, runs, approvals, and planner."""

    @pytest.fixture
    def client(self, populated_storage) -> TestClient:
        app = create_app(storage=populated_storage)
        return TestClient(app)

    def test_ui_workflows_catalogue(self, client):
        """GET /workflows returns HTTP 200 with workflow catalogue."""
        res = client.get("/workflows")
        assert res.status_code == 200
        assert "Bounded Autonomous Workflows" in res.text
        assert "Company Intelligence Dossier" in res.text
        assert "Valuation Readiness and Analysis" in res.text

    def test_ui_workflow_run_form(self, client):
        """GET /workflows/run/{workflow_id} returns run configuration form."""
        res = client.get("/workflows/run/wf_company_dossier")
        assert res.status_code == 200
        assert "Launch Workflow:" in res.text
        assert "wf_company_dossier" in res.text

    def test_ui_workflow_execute_and_redirect(self, client):
        """POST /workflows/run/{workflow_id} executes workflow and redirects to run detail."""
        res = client.post(
            "/workflows/run/wf_company_dossier",
            data={"initiator": "ui_tester", "entity_id": "ENT_ACME_001"},
            follow_redirects=False,
        )
        assert res.status_code == 303
        redirect_url = res.headers["location"]
        assert "/workflows/runs/" in redirect_url

        # Follow redirect
        detail_res = client.get(redirect_url)
        assert detail_res.status_code == 200
        assert "SUCCEEDED" in detail_res.text
        assert "ENT_ACME_001" in detail_res.text

    def test_ui_approvals_queue(self, client):
        """GET /workflows/approvals returns approval queue page."""
        res = client.get("/workflows/approvals")
        assert res.status_code == 200
        assert "Human-in-the-Loop Approval Queue" in res.text

    def test_ui_planner_page_and_submission(self, client):
        """GET & POST /workflows/planner returns proposal interactive assistant."""
        get_res = client.get("/workflows/planner")
        assert get_res.status_code == 200
        assert "AI Workflow Planner" in get_res.text

        post_res = client.post(
            "/workflows/planner",
            data={"intent": "Audit data integrity for Acme"},
        )
        assert post_res.status_code == 200
        assert "wf_data_integrity_audit" in post_res.text
        assert "Proposed Plan" in post_res.text
