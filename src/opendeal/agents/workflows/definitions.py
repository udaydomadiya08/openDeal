"""Predefined, deterministic workflow blueprints for OpenDeal automation."""

from datetime import datetime, timezone
from typing import Dict, List

from opendeal.agents.contracts import (
    PermissionCategory,
    TaskDefinition,
    WorkflowDefinition,
)

# ── 1. Company Intelligence Dossier Workflow ────────────────────────────────
COMPANY_DOSSIER_WORKFLOW = WorkflowDefinition(
    workflow_id="wf_company_dossier",
    name="Company Intelligence Dossier",
    version="1.0.0",
    description="Resolves entity, runs data-quality checks, aggregates deals & timeline, and generates a grounded research brief.",
    required_permissions=[
        PermissionCategory.READ_LOCAL_DATA,
        PermissionCategory.GENERATE_RESEARCH,
        PermissionCategory.CREATE_WORKFLOW_ARTIFACT,
    ],
    tasks=[
        TaskDefinition(
            task_name="resolve_company",
            agent_name="company_research_agent",
            description="Verify entity identity and resolve company from local store",
            required_permissions=[
                PermissionCategory.READ_LOCAL_DATA,
                PermissionCategory.CREATE_WORKFLOW_ARTIFACT,
            ],
            dependencies=[],
        ),
        TaskDefinition(
            task_name="data_quality_check",
            agent_name="evidence_data_quality_agent",
            description="Inspect facts, evidence, and currency/period consistency for target entity",
            required_permissions=[
                PermissionCategory.READ_LOCAL_DATA,
                PermissionCategory.CREATE_WORKFLOW_ARTIFACT,
            ],
            dependencies=["resolve_company"],
        ),
        TaskDefinition(
            task_name="deal_intelligence_aggregation",
            agent_name="deal_intelligence_agent",
            description="Assemble funding rounds, M&A history, timeline, and source conflicts",
            required_permissions=[
                PermissionCategory.READ_LOCAL_DATA,
                PermissionCategory.CREATE_WORKFLOW_ARTIFACT,
            ],
            dependencies=["resolve_company"],
        ),
        TaskDefinition(
            task_name="grounded_research_synthesis",
            agent_name="company_research_agent",
            description="Generate evidence-backed research brief via Phase 5 pipeline",
            required_permissions=[
                PermissionCategory.READ_LOCAL_DATA,
                PermissionCategory.GENERATE_RESEARCH,
                PermissionCategory.CREATE_WORKFLOW_ARTIFACT,
            ],
            dependencies=["data_quality_check", "deal_intelligence_aggregation"],
        ),
    ],
    created_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
    updated_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
)

# ── 2. Acquisition Target Screening Workflow ────────────────────────────────
TARGET_SCREENING_WORKFLOW = WorkflowDefinition(
    workflow_id="wf_target_screening",
    name="Acquisition Target Screening",
    version="1.0.0",
    description="Executes deterministic eligibility checks, computes multi-factor scores, explains exclusions, and generates a shortlist report.",
    required_permissions=[
        PermissionCategory.READ_LOCAL_DATA,
        PermissionCategory.CREATE_WORKFLOW_ARTIFACT,
    ],
    tasks=[
        TaskDefinition(
            task_name="deterministic_screening",
            agent_name="target_discovery_agent",
            description="Screen candidates against criteria and compute composite scores with penalties",
            required_permissions=[
                PermissionCategory.READ_LOCAL_DATA,
                PermissionCategory.CREATE_WORKFLOW_ARTIFACT,
            ],
            dependencies=[],
        ),
        TaskDefinition(
            task_name="candidate_data_quality_audit",
            agent_name="evidence_data_quality_agent",
            description="Audit fact integrity and evidence links for screened candidate pool",
            required_permissions=[
                PermissionCategory.READ_LOCAL_DATA,
                PermissionCategory.CREATE_WORKFLOW_ARTIFACT,
            ],
            dependencies=["deterministic_screening"],
        ),
    ],
    created_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
    updated_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
)

# ── 3. Valuation Readiness and Analysis Workflow ────────────────────────────
VALUATION_ANALYSIS_WORKFLOW = WorkflowDefinition(
    workflow_id="wf_valuation_analysis",
    name="Valuation Readiness and Analysis",
    version="1.0.0",
    description="Validates model inputs, checks reporting periods/currencies, identifies missing fields, and executes the deterministic valuation engine.",
    required_permissions=[
        PermissionCategory.READ_LOCAL_DATA,
        PermissionCategory.RUN_DETERMINISTIC_VALUATION,
        PermissionCategory.CREATE_WORKFLOW_ARTIFACT,
    ],
    tasks=[
        TaskDefinition(
            task_name="valuation_input_verification",
            agent_name="evidence_data_quality_agent",
            description="Verify data quality, currency matching, and facts for valuation",
            required_permissions=[
                PermissionCategory.READ_LOCAL_DATA,
                PermissionCategory.CREATE_WORKFLOW_ARTIFACT,
            ],
            dependencies=[],
        ),
        TaskDefinition(
            task_name="deterministic_valuation_execution",
            agent_name="valuation_preparation_agent",
            description="Execute deterministic DCF, WACC, or Multiples engine if inputs are complete",
            required_permissions=[
                PermissionCategory.READ_LOCAL_DATA,
                PermissionCategory.RUN_DETERMINISTIC_VALUATION,
                PermissionCategory.CREATE_WORKFLOW_ARTIFACT,
            ],
            dependencies=["valuation_input_verification"],
        ),
    ],
    created_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
    updated_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
)

# ── 4. Deal Event Monitoring from Local Data ────────────────────────────────
DEAL_MONITORING_WORKFLOW = WorkflowDefinition(
    workflow_id="wf_deal_monitoring",
    name="Deal Event Monitoring",
    version="1.0.0",
    description="Compares local dataset against recorded snapshots, detecting newly ingested funding/transaction observations and status changes.",
    required_permissions=[
        PermissionCategory.READ_LOCAL_DATA,
        PermissionCategory.CREATE_WORKFLOW_ARTIFACT,
    ],
    tasks=[
        TaskDefinition(
            task_name="deal_snapshot_comparison",
            agent_name="deal_intelligence_agent",
            description="Inspect recent transactions, funding rounds, and conflicts against baseline",
            required_permissions=[
                PermissionCategory.READ_LOCAL_DATA,
                PermissionCategory.CREATE_WORKFLOW_ARTIFACT,
            ],
            dependencies=[],
        ),
    ],
    created_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
    updated_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
)

# ── 5. Data Integrity Audit Workflow ────────────────────────────────────────
DATA_INTEGRITY_AUDIT_WORKFLOW = WorkflowDefinition(
    workflow_id="wf_data_integrity_audit",
    name="Data Integrity Audit",
    version="1.0.0",
    description="Scans local facts, evidence references, and entity linkages to produce severity-ranked findings and non-destructive remediation advice.",
    required_permissions=[
        PermissionCategory.READ_LOCAL_DATA,
        PermissionCategory.CREATE_WORKFLOW_ARTIFACT,
    ],
    tasks=[
        TaskDefinition(
            task_name="comprehensive_integrity_scan",
            agent_name="evidence_data_quality_agent",
            description="Scan database for unresolvable citations, conflicting values, and format errors",
            required_permissions=[
                PermissionCategory.READ_LOCAL_DATA,
                PermissionCategory.CREATE_WORKFLOW_ARTIFACT,
            ],
            dependencies=[],
        ),
    ],
    created_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
    updated_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
)

STANDARD_WORKFLOWS: List[WorkflowDefinition] = [
    COMPANY_DOSSIER_WORKFLOW,
    TARGET_SCREENING_WORKFLOW,
    VALUATION_ANALYSIS_WORKFLOW,
    DEAL_MONITORING_WORKFLOW,
    DATA_INTEGRITY_AUDIT_WORKFLOW,
]

STANDARD_WORKFLOWS_BY_ID: Dict[str, WorkflowDefinition] = {
    w.workflow_id: w for w in STANDARD_WORKFLOWS
}


def register_standard_workflows(storage) -> None:
    """Seed or update the 5 standard workflow blueprints into storage."""
    for wf in STANDARD_WORKFLOWS:
        storage.save_workflow_definition(wf)
