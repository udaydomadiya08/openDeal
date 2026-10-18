"""Deterministic workflow planner proposing executable, bounded plans from user intents."""

import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from opendeal.agents.contracts import (
    PermissionCategory,
    TaskDefinition,
    WorkflowDefinition,
)
from opendeal.agents.policies import PolicyEngine
from opendeal.agents.workflows.definitions import (
    COMPANY_DOSSIER_WORKFLOW,
    DATA_INTEGRITY_AUDIT_WORKFLOW,
    DEAL_MONITORING_WORKFLOW,
    STANDARD_WORKFLOWS_BY_ID,
    TARGET_SCREENING_WORKFLOW,
    VALUATION_ANALYSIS_WORKFLOW,
)


class WorkflowPlanProposal(BaseModel):
    """Structured, validated plan proposal generated for human operator review."""
    workflow_id: str
    workflow_name: str
    description: str
    required_permissions: List[PermissionCategory] = Field(default_factory=list)
    tasks: List[TaskDefinition] = Field(default_factory=list)
    extracted_inputs: Dict[str, Any] = Field(default_factory=dict)
    missing_inputs: List[str] = Field(default_factory=list)
    task_explanations: Dict[str, str] = Field(default_factory=dict)
    estimated_duration_seconds: int = 30
    requires_human_approval: bool = False
    validation_status: str = "VALID"
    rejection_reason: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return self.validation_status == "VALID"


class DeterministicPlanner:
    """Proposes strictly bounded workflows from analyst requests without arbitrary code execution."""

    def __init__(self, policy_engine: Optional[PolicyEngine] = None, storage: Optional[Any] = None) -> None:
        self.policy_engine = policy_engine or PolicyEngine(storage=storage)
        self.storage = storage

    def propose_plan(self, user_intent: str) -> WorkflowPlanProposal:
        """Parse user query, select registered workflow template, and formulate a proposal.

        Adversarial prompt injection in user queries is sanitized and cannot elevate privileges.
        """
        sanitized = PolicyEngine.sanitize_untrusted_input(user_intent)
        lowered = sanitized.lower()

        # Extract potential company entity identifier
        entity_match = re.search(r"\b([A-Z]{1,5}|ent_[a-z0-9_]+)\b", sanitized)
        entity_id = entity_match.group(1) if entity_match else None

        # Intent classification against standard registered blueprints
        if any(w in lowered for w in ["valuation", "dcf", "wacc", "discounted cash flow"]):
            wf = VALUATION_ANALYSIS_WORKFLOW
            inputs = {"entity_id": entity_id or "AAPL", "model_type": "dcf"}
            missing = [] if entity_id else ["entity_id"]
            explanations = {
                "valuation_input_verification": "Verifies historical financials and checks currency/period consistency.",
                "deterministic_valuation_execution": "Calls Python valuation engine with verified cash flows and discount rates.",
            }
        elif any(w in lowered for w in ["screen", "discovery", "target", "acquisition candidate"]):
            wf = TARGET_SCREENING_WORKFLOW
            inputs = {"criteria": {"min_revenue": 10000000.0}}
            missing = []
            explanations = {
                "deterministic_screening": "Applies quantitative revenue/margin filters and calculates composite fit scores.",
                "candidate_data_quality_audit": "Checks factual grounding and evidence chains for the screened candidate pool.",
            }
        elif any(w in lowered for w in ["monitor", "snapshot", "drift", "change", "new deals"]):
            wf = DEAL_MONITORING_WORKFLOW
            inputs = {"dataset_key": "local_deals_baseline"}
            missing = []
            explanations = {
                "deal_snapshot_comparison": "Compares newly recorded funding rounds and M&A events against previous local baseline.",
            }
        elif any(w in lowered for w in ["audit", "integrity", "scan", "consistency", "conflict"]):
            wf = DATA_INTEGRITY_AUDIT_WORKFLOW
            inputs = {}
            missing = []
            explanations = {
                "comprehensive_integrity_scan": "Scans facts, evidence hashes, and deal records to identify orphan references or conflicting data points.",
            }
        elif any(w in lowered for w in ["dossier", "company", "research", "profile", "overview", "brief", "intelligence", "acme", "apple"]):
            wf = COMPANY_DOSSIER_WORKFLOW
            inputs = {"entity_id": entity_id or "AAPL"}
            missing = [] if entity_id else ["entity_id"]
            explanations = {
                "data_quality_check": "Validates reporting periods and checks for missing disclosures.",
                "deal_intelligence_aggregation": "Aggregates documented financing history, acquisitions, and timeline milestones.",
                "grounded_research_synthesis": "Synthesizes an auditable research brief with primary regulatory citations.",
            }
        else:
            return WorkflowPlanProposal(
                workflow_id="unsupported_intent",
                workflow_name="Unsupported Intent",
                description="No matching financial workflow blueprint found.",
                validation_status="REJECTED",
                rejection_reason="No matching financial workflow blueprint for intent. Supported capabilities: company dossier, target screening, valuation analysis, deal monitoring, data integrity audit.",
            )

        # Check permissions through policy engine
        requires_approval = any(
            p in {PermissionCategory.WRITE_APPROVED_TARGET_LIST, PermissionCategory.REQUEST_LIVE_INGESTION}
            for p in wf.required_permissions
        )

        return WorkflowPlanProposal(
            workflow_id=wf.workflow_id,
            workflow_name=wf.name,
            description=wf.description,
            required_permissions=wf.required_permissions,
            tasks=wf.tasks,
            extracted_inputs=inputs,
            missing_inputs=missing,
            task_explanations=explanations,
            estimated_duration_seconds=len(wf.tasks) * 10,
            requires_human_approval=requires_approval,
            validation_status="VALID",
        )
