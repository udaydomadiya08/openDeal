"""Package containing workflow definitions and implementations."""

from opendeal.agents.workflows.definitions import (
    COMPANY_DOSSIER_WORKFLOW,
    DATA_INTEGRITY_AUDIT_WORKFLOW,
    DEAL_MONITORING_WORKFLOW,
    STANDARD_WORKFLOWS,
    STANDARD_WORKFLOWS_BY_ID,
    TARGET_SCREENING_WORKFLOW,
    VALUATION_ANALYSIS_WORKFLOW,
    register_standard_workflows,
)

__all__ = [
    "COMPANY_DOSSIER_WORKFLOW",
    "TARGET_SCREENING_WORKFLOW",
    "VALUATION_ANALYSIS_WORKFLOW",
    "DEAL_MONITORING_WORKFLOW",
    "DATA_INTEGRITY_AUDIT_WORKFLOW",
    "STANDARD_WORKFLOWS",
    "STANDARD_WORKFLOWS_BY_ID",
    "register_standard_workflows",
]
