"""Strongly-typed contracts, state machine, and permissions for OpenDeal Agent Orchestration."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field


# ── State Machine & Enums ───────────────────────────────────────────────────

class WorkflowState(str, Enum):
    """Explicit lifecycle states for workflow runs."""
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    PARTIALLY_SUCCEEDED = "PARTIALLY_SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class TaskState(str, Enum):
    """Lifecycle states for individual task executions."""
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class PermissionCategory(str, Enum):
    """Fine-grained permission privileges governed by the policy engine."""
    READ_LOCAL_DATA = "READ_LOCAL_DATA"
    GENERATE_RESEARCH = "GENERATE_RESEARCH"
    RUN_DETERMINISTIC_VALUATION = "RUN_DETERMINISTIC_VALUATION"
    CREATE_WORKFLOW_ARTIFACT = "CREATE_WORKFLOW_ARTIFACT"
    PROPOSE_DATABASE_CHANGE = "PROPOSE_DATABASE_CHANGE"
    WRITE_APPROVED_TARGET_LIST = "WRITE_APPROVED_TARGET_LIST"
    REQUEST_LIVE_INGESTION = "REQUEST_LIVE_INGESTION"


class ApprovalStatus(str, Enum):
    """Human-in-the-loop approval decision states."""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"


class ErrorClassification(str, Enum):
    """Taxonomy of workflow task errors governing retry semantics."""
    TRANSIENT = "TRANSIENT"  # E.g. sqlite busy / lock, transient IO -> retryable
    PERMANENT_VALIDATION = "PERMANENT_VALIDATION"  # E.g. bad inputs, invalid units -> non-retryable
    PERMANENT_PERMISSION = "PERMANENT_PERMISSION"  # E.g. unauthorized action -> non-retryable
    PERMANENT_LOGIC = "PERMANENT_LOGIC"  # E.g. entity not found, incompatible criteria -> non-retryable
    USER_CANCELLED = "USER_CANCELLED"  # Run stopped by user operator


# ── State Transition Validation ─────────────────────────────────────────────

VALID_WORKFLOW_TRANSITIONS: Dict[WorkflowState, Set[WorkflowState]] = {
    WorkflowState.DRAFT: {
        WorkflowState.QUEUED,
        WorkflowState.PENDING_APPROVAL,
        WorkflowState.CANCELLED,
    },
    WorkflowState.PENDING_APPROVAL: {
        WorkflowState.QUEUED,
        WorkflowState.CANCELLED,
    },
    WorkflowState.QUEUED: {
        WorkflowState.RUNNING,
        WorkflowState.CANCELLED,
    },
    WorkflowState.RUNNING: {
        WorkflowState.WAITING_FOR_APPROVAL,
        WorkflowState.SUCCEEDED,
        WorkflowState.PARTIALLY_SUCCEEDED,
        WorkflowState.FAILED,
        WorkflowState.CANCELLED,
        WorkflowState.TIMED_OUT,
    },
    WorkflowState.WAITING_FOR_APPROVAL: {
        WorkflowState.RUNNING,
        WorkflowState.CANCELLED,
        WorkflowState.FAILED,
        WorkflowState.TIMED_OUT,
    },
    # Terminal states: immutable once entered
    WorkflowState.SUCCEEDED: set(),
    WorkflowState.PARTIALLY_SUCCEEDED: set(),
    WorkflowState.FAILED: set(),
    WorkflowState.CANCELLED: set(),
    WorkflowState.TIMED_OUT: set(),
}


class InvalidStateTransitionError(Exception):
    """Raised when an illegal workflow state transition is attempted."""
    pass


class WorkflowExecutionError(Exception):
    """Base exception for workflow execution failures."""
    pass


class WorkflowTimeoutError(WorkflowExecutionError):
    """Raised when a workflow run or task exceeds its monotonic execution deadline."""
    pass


class WorkflowCancelledError(WorkflowExecutionError):
    """Raised when a workflow run or task is cancelled by an operator."""
    pass


class CycleDetectedError(WorkflowExecutionError):
    """Raised when circular task dependencies are detected."""
    pass


class InvalidDependencyError(WorkflowExecutionError):
    """Raised when a task declares an unknown prerequisite dependency."""
    pass


class ResourceLimitExceededError(WorkflowExecutionError):
    """Raised when workflow size or depth exceeds bounds."""
    pass



def validate_workflow_transition(current_state: WorkflowState, next_state: WorkflowState) -> None:
    """Enforce strict workflow state machine transitions.

    Raises InvalidStateTransitionError if the requested transition is illegal.
    """
    if current_state == next_state:
        return
    allowed = VALID_WORKFLOW_TRANSITIONS.get(current_state, set())
    if next_state not in allowed:
        raise InvalidStateTransitionError(
            f"Illegal workflow state transition from '{current_state.value}' to '{next_state.value}'. "
            f"Allowed transitions: {[s.value for s in allowed]}"
        )


# ── Core Data Models ────────────────────────────────────────────────────────

class ResourceLimits(BaseModel):
    """Execution boundaries enforced by the coordinator."""
    max_tasks: int = 25
    max_dependency_depth: int = 5
    max_retries: int = 2
    task_timeout_sec: int = 60
    workflow_timeout_sec: int = 300
    max_result_size_bytes: int = 5_000_000


class TaskDefinition(BaseModel):
    """Specification of an individual task step within a workflow."""
    task_name: str
    agent_name: str
    description: str = ""
    required_permissions: List[PermissionCategory] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    max_retries: int = 0
    timeout_seconds: int = 60


class WorkflowDefinition(BaseModel):
    """Declarative blueprint for a repeatable business workflow."""
    workflow_id: str
    name: str
    version: str = "1.0.0"
    description: str
    required_permissions: List[PermissionCategory] = Field(default_factory=list)
    tasks: List[TaskDefinition] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskRun(BaseModel):
    """Execution record and outcome for an individual task step."""
    task_run_id: str
    run_id: str
    task_name: str
    agent_name: str
    state: TaskState = TaskState.QUEUED
    attempt: int = 1
    max_retries: int = 0
    dependencies: List[str] = Field(default_factory=list)
    inputs: Dict[str, Any] = Field(default_factory=dict)
    outputs: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
    error_classification: Optional[ErrorClassification] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class WorkflowRun(BaseModel):
    """Audit record and context for a single workflow execution."""
    run_id: str
    workflow_id: str
    version: str = "1.0.0"
    state: WorkflowState = WorkflowState.DRAFT
    initiator: str = "system"
    inputs: Dict[str, Any] = Field(default_factory=dict)
    outputs: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
    idempotency_key: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ApprovalRequest(BaseModel):
    """Human-in-the-loop authorization request for a privileged action."""
    approval_id: str
    run_id: str
    action_type: str
    action_payload: Dict[str, Any] = Field(default_factory=dict)
    scope: str = "GLOBAL"
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decided_at: Optional[datetime] = None
    decided_by: Optional[str] = None
    decision_rationale: Optional[str] = None


class AuditEvent(BaseModel):
    """Immutable audit trail entry for workflow execution forensics."""
    event_id: str
    run_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str
    initiator: str = "system"
    agent_or_task: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    is_mutation: bool = False


class DatasetSnapshot(BaseModel):
    """Point-in-time metrics and hashes used for change detection & monitoring."""
    snapshot_id: str
    dataset_key: str
    snapshot_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    record_counts: Dict[str, int] = Field(default_factory=dict)
    content_hashes: Dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
