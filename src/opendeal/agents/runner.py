"""Deterministic workflow coordinator and execution engine enforcing policies, retries, and bounds."""

import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from opendeal.agents.contracts import (
    ApprovalRequest,
    ApprovalStatus,
    AuditEvent,
    CycleDetectedError,
    ErrorClassification,
    InvalidDependencyError,
    InvalidStateTransitionError,
    PermissionCategory,
    ResourceLimitExceededError,
    ResourceLimits,
    TaskDefinition,
    TaskRun,
    TaskState,
    WorkflowDefinition,
    WorkflowExecutionError,
    WorkflowRun,
    WorkflowState,
    validate_workflow_transition,
)
from opendeal.agents.policies import PolicyEngine
from opendeal.agents.registry import AgentRegistry
from opendeal.storage.base import StorageInterface

logger = logging.getLogger(__name__)

# Active execution registry to distinguish active executions from abandoned/crashed runs
_ACTIVE_RUN_LOCK = threading.Lock()
_ACTIVE_RUN_IDS: Set[str] = set()


def _register_active_run(run_id: str) -> None:
    with _ACTIVE_RUN_LOCK:
        _ACTIVE_RUN_IDS.add(run_id)


def _unregister_active_run(run_id: str) -> None:
    with _ACTIVE_RUN_LOCK:
        _ACTIVE_RUN_IDS.discard(run_id)


def _is_run_active_in_process(run_id: str) -> bool:
    with _ACTIVE_RUN_LOCK:
        return run_id in _ACTIVE_RUN_IDS


class CancellationToken:
    """Explicit cooperative cancellation and monotonic deadline token passed to agents."""

    def __init__(self, run_id: str, deadline: float, is_cancelled_fn) -> None:
        self.run_id = run_id
        self.deadline = deadline
        self._is_cancelled_fn = is_cancelled_fn

    @property
    def is_cancelled(self) -> bool:
        return bool(self._is_cancelled_fn(self.run_id))

    @property
    def is_timed_out(self) -> bool:
        return time.monotonic() >= self.deadline

    @property
    def is_active(self) -> bool:
        return not self.is_cancelled and not self.is_timed_out

    def check_active(self) -> None:
        if self.is_cancelled:
            from opendeal.agents.contracts import WorkflowCancelledError
            raise WorkflowCancelledError(f"Workflow run '{self.run_id}' was cancelled by operator")
        if self.is_timed_out:
            from opendeal.agents.contracts import WorkflowTimeoutError
            raise WorkflowTimeoutError(f"Workflow run '{self.run_id}' exceeded execution deadline")


class WorkflowRunner:
    """Orchestrates workflow task graphs with policy enforcement and audit trails."""

    def __init__(
        self,
        storage: StorageInterface,
        agent_registry: Optional[AgentRegistry] = None,
        policy_engine: Optional[PolicyEngine] = None,
        limits: Optional[ResourceLimits] = None,
    ) -> None:
        self.storage = storage
        self.agent_registry = agent_registry or AgentRegistry(storage)
        self.policy_engine = policy_engine or PolicyEngine(storage)
        self.limits = limits or ResourceLimits()
    def execute_workflow(
        self,
        workflow_id: str,
        inputs: Dict[str, Any],
        initiator: str = "operator",
        idempotency_key: Optional[str] = None,
    ) -> WorkflowRun:
        """Resolve workflow definition, create run, and execute it to completion."""
        defn_dict = self.storage.get_workflow_definition(workflow_id)
        if defn_dict:
            workflow_defn = WorkflowDefinition.model_validate(defn_dict)
        else:
            from opendeal.agents.workflows.definitions import STANDARD_WORKFLOWS_BY_ID
            workflow_defn = STANDARD_WORKFLOWS_BY_ID.get(workflow_id)
            if not workflow_defn:
                raise WorkflowExecutionError(f"Workflow '{workflow_id}' not found in registry or storage")

        run = self.create_run(workflow_defn, inputs=inputs, initiator=initiator, idempotency_key=idempotency_key)
        return self.execute_run(workflow_defn, run.run_id)

    def create_run(
        self,
        workflow_defn: WorkflowDefinition,
        inputs: Dict[str, Any],
        initiator: str = "operator",
        idempotency_key: Optional[str] = None,
    ) -> WorkflowRun:
        """Initialize and validate a new workflow run record."""
        # Check idempotency to prevent duplicate runs
        if idempotency_key:
            existing = self.storage.list_workflow_runs(workflow_id=workflow_defn.workflow_id, limit=50)
            for r in existing:
                if r.get("idempotency_key") == idempotency_key:
                    logger.info("Reusing existing workflow run for idempotency key '%s'", idempotency_key)
                    return WorkflowRun.model_validate(r)

        # Validate task limits
        if len(workflow_defn.tasks) > self.limits.max_tasks:
            raise ResourceLimitExceededError(
                f"Workflow contains {len(workflow_defn.tasks)} tasks, exceeding limit of {self.limits.max_tasks}"
            )

        # Validate inputs size limit
        import json
        if len(json.dumps(inputs, default=str)) > self.limits.max_result_size_bytes:
            raise ResourceLimitExceededError(
                f"Workflow inputs size exceeds limit of {self.limits.max_result_size_bytes} bytes"
            )

        # Validate topological ordering & cycle detection
        self._topological_sort(workflow_defn.tasks)

        run_id = f"wfrun_{uuid.uuid4().hex[:12]}"
        run = WorkflowRun(
            run_id=run_id,
            workflow_id=workflow_defn.workflow_id,
            version=workflow_defn.version,
            state=WorkflowState.QUEUED,
            initiator=initiator,
            inputs=inputs,
            idempotency_key=idempotency_key,
            created_at=datetime.now(timezone.utc),
        )
        self.storage.save_workflow_run(run)

        # Record audit event
        self._audit(
            run_id=run_id,
            event_type="WORKFLOW_QUEUED",
            initiator=initiator,
            details={"workflow_id": workflow_defn.workflow_id, "tasks_count": len(workflow_defn.tasks)},
        )

        # Create initial TaskRun records in QUEUED state
        for task in workflow_defn.tasks:
            task_run = TaskRun(
                task_run_id=f"trun_{uuid.uuid4().hex[:12]}",
                run_id=run_id,
                task_name=task.task_name,
                agent_name=task.agent_name,
                state=TaskState.QUEUED,
                attempt=1,
                max_retries=task.max_retries,
                dependencies=task.dependencies,
                inputs=inputs,
            )
            self.storage.save_task_run(task_run)

        return run

    def _is_run_cancelled(self, run_id: str) -> bool:
        cur = self.storage.get_workflow_run(run_id)
        return bool(cur and cur.get("state") == WorkflowState.CANCELLED.value)

    def execute_run(
        self,
        workflow_defn: WorkflowDefinition,
        run_id: str,
    ) -> WorkflowRun:
        """Execute a previously created or queued workflow run."""
        run_dict = self.storage.get_workflow_run(run_id)
        if not run_dict:
            raise WorkflowExecutionError(f"Workflow run '{run_id}' not found")

        run = WorkflowRun.model_validate(run_dict)

        # Check if run is already in terminal state
        if run.state in {WorkflowState.SUCCEEDED, WorkflowState.PARTIALLY_SUCCEEDED, WorkflowState.FAILED, WorkflowState.CANCELLED, WorkflowState.TIMED_OUT}:
            logger.info("Workflow run '%s' already in terminal state '%s'", run_id, run.state.value)
            return run

        # Transition state to RUNNING strictly from QUEUED
        validate_workflow_transition(run.state, WorkflowState.RUNNING)

        # Atomic claim of the queued run (prevents duplicate concurrent execution claims)
        now_iso = datetime.now(timezone.utc).isoformat()
        claimed = self.storage.claim_queued_workflow_run(run_id, now_iso)
        if not claimed:
            fresh_run = self.storage.get_workflow_run(run_id)
            if fresh_run:
                cur_state = fresh_run.get("state")
                if cur_state in {
                    WorkflowState.SUCCEEDED.value,
                    WorkflowState.PARTIALLY_SUCCEEDED.value,
                    WorkflowState.FAILED.value,
                    WorkflowState.CANCELLED.value,
                    WorkflowState.TIMED_OUT.value,
                }:
                    return WorkflowRun.model_validate(fresh_run)
                if cur_state == WorkflowState.RUNNING.value:
                    raise WorkflowExecutionError(
                        f"Workflow run '{run_id}' is already actively executing in another worker/thread"
                    )
                if cur_state == WorkflowState.WAITING_FOR_APPROVAL.value:
                    raise WorkflowExecutionError(
                        f"Workflow run '{run_id}' is waiting for approval; use resume_approved_run"
                    )
            raise WorkflowExecutionError(
                f"Workflow run '{run_id}' cannot be executed concurrently or has invalid state: {fresh_run.get('state') if fresh_run else 'unknown'}"
            )

        run.state = WorkflowState.RUNNING
        run.started_at = datetime.now(timezone.utc)
        self._audit(run_id=run_id, event_type="WORKFLOW_RUNNING", initiator=run.initiator)

        _register_active_run(run_id)
        try:
            return self._execute_task_pipeline(run, workflow_defn)
        finally:
            _unregister_active_run(run_id)

    def _execute_task_pipeline(
        self,
        run: WorkflowRun,
        workflow_defn: WorkflowDefinition,
    ) -> WorkflowRun:
        """Internal execution loop processing DAG tasks under monotonic deadlines and cancellation guards."""
        run_id = run.run_id

        # Load ordered task definitions
        ordered_tasks = self._topological_sort(workflow_defn.tasks)
        task_def_map = {t.task_name: t for t in ordered_tasks}

        # Context accumulating outputs across execution
        context: Dict[str, Any] = {"workflow_inputs": dict(run.inputs)}
        if run.outputs:
            context.update(run.outputs)

        task_runs = {tr["task_name"]: tr for tr in self.storage.get_task_runs_for_workflow(run_id)}

        failed_tasks: Set[str] = set()
        skipped_tasks: Set[str] = set()
        succeeded_tasks: Set[str] = set()

        monotonic_deadline = time.monotonic() + self.limits.workflow_timeout_sec

        for task_def in ordered_tasks:
            t_name = task_def.task_name
            tr_data = task_runs.get(t_name, {})
            task_run_id = tr_data.get("task_run_id", f"trun_{uuid.uuid4().hex[:12]}")

            # If task already succeeded in a prior execution (e.g. before approval pause), do not re-run!
            if tr_data.get("state") == TaskState.SUCCEEDED.value:
                logger.info("Task '%s' already succeeded in run '%s'; skipping duplicate execution", t_name, run_id)
                succeeded_tasks.add(t_name)
                if tr_data.get("outputs"):
                    context.update(tr_data["outputs"])
                continue

            # Check if workflow duration exceeded monotonic timeout limit before starting task
            if time.monotonic() >= monotonic_deadline:
                logger.warning("Workflow '%s' exceeded timeout of %ds before starting task '%s'", run_id, self.limits.workflow_timeout_sec, t_name)
                failed_tasks.add(t_name)
                self._mark_task(
                    task_run_id, t_name, run_id, TaskState.TIMED_OUT,
                    error=f"Workflow execution exceeded maximum duration limit ({self.limits.workflow_timeout_sec}s)",
                )
                for rem_task in ordered_tasks:
                    rem_name = rem_task.task_name
                    rem_tr = task_runs.get(rem_name, {})
                    if rem_name != t_name and rem_tr.get("state") in (TaskState.QUEUED.value, TaskState.RUNNING.value, None):
                        rem_id = rem_tr.get("task_run_id", f"trun_{uuid.uuid4().hex[:12]}")
                        self._mark_task(rem_id, rem_name, run_id, TaskState.TIMED_OUT, error="Workflow execution exceeded maximum duration limit")
                break

            # Check if workflow was cancelled asynchronously
            if self._is_run_cancelled(run_id):
                logger.info("Workflow '%s' has been cancelled by operator; halting task execution", run_id)
                self._mark_task(task_run_id, t_name, run_id, TaskState.CANCELLED, error="Run cancelled by operator")
                break

            # If any prerequisite dependency failed or was skipped, skip this task
            prereqs_failed = any(dep in failed_tasks or dep in skipped_tasks for dep in task_def.dependencies)
            if prereqs_failed:
                skipped_tasks.add(t_name)
                self._mark_task(
                    task_run_id, t_name, run_id, TaskState.SKIPPED,
                    error=f"Prerequisite dependencies failed or skipped: {task_def.dependencies}",
                )
                continue

            # 1. Policy check: undeclared permissions
            policy_dec = self.policy_engine.check_task_permission(workflow_defn, task_def)
            if not policy_dec.allowed:
                failed_tasks.add(t_name)
                self._mark_task(
                    task_run_id, t_name, run_id, TaskState.FAILED,
                    error=policy_dec.reason,
                    classification=ErrorClassification.PERMANENT_PERMISSION,
                )
                continue

            # 2. Check for human-in-the-loop approval if task requests privileged operations
            from opendeal.agents.policies import ACTIONS_REQUIRING_APPROVAL
            privileged_action_needed = None
            for action_name, action_perm in ACTIONS_REQUIRING_APPROVAL.items():
                if action_perm in task_def.required_permissions:
                    privileged_action_needed = action_name
                    break

            if privileged_action_needed:
                auth_dec = self.policy_engine.check_action_authorization(
                    run_id=run_id,
                    action_type=privileged_action_needed,
                    action_payload=run.inputs,
                    scope=f"WORKFLOW:{run.workflow_id}",
                )
                if not auth_dec.allowed:
                    # Create approval request if none exists
                    self._request_approval(
                        run_id=run_id,
                        action_type=privileged_action_needed,
                        payload=run.inputs,
                        scope=f"WORKFLOW:{run.workflow_id}",
                    )
                    # Pause workflow execution until human approves; preserve current context
                    validate_workflow_transition(run.state, WorkflowState.WAITING_FOR_APPROVAL)
                    run.state = WorkflowState.WAITING_FOR_APPROVAL
                    run.outputs = context
                    self.storage.save_workflow_run(run)
                    self._audit(
                        run_id=run_id,
                        event_type="WORKFLOW_WAITING_FOR_APPROVAL",
                        initiator=run.initiator,
                        details={"task": t_name, "reason": auth_dec.reason},
                    )
                    return run
                else:
                    # Atomically consume the approval to prevent double-execution or replay
                    consume_dec = self.policy_engine.consume_approval(
                        run_id=run_id,
                        action_type=privileged_action_needed,
                        action_payload=run.inputs,
                        scope=f"WORKFLOW:{run.workflow_id}",
                    )
                    if not consume_dec.allowed:
                        failed_tasks.add(t_name)
                        self._mark_task(
                            task_run_id, t_name, run_id, TaskState.FAILED,
                            error=consume_dec.reason,
                            classification=ErrorClassification.PERMANENT_PERMISSION,
                        )
                        continue

            # 3. Retrieve agent worker
            agent = self.agent_registry.get_agent(task_def.agent_name)
            if not agent:
                failed_tasks.add(t_name)
                self._mark_task(
                    task_run_id, t_name, run_id, TaskState.FAILED,
                    error=f"Specialized agent '{task_def.agent_name}' not registered",
                    classification=ErrorClassification.PERMANENT_LOGIC,
                )
                continue

            # 4. Execute task with retries for transient errors
            attempt = 1
            max_attempts = 1 + max(0, min(self.limits.max_retries, task_def.max_retries))
            task_success = False

            while attempt <= max_attempts:
                # Deadline check before attempt
                if time.monotonic() >= monotonic_deadline:
                    logger.warning("Workflow '%s' exceeded timeout of %ds before attempt %d", run_id, self.limits.workflow_timeout_sec, attempt)
                    failed_tasks.add(t_name)
                    self._mark_task(
                        task_run_id, t_name, run_id, TaskState.TIMED_OUT,
                        error=f"Workflow execution exceeded maximum duration limit ({self.limits.workflow_timeout_sec}s)",
                    )
                    break

                if self._is_run_cancelled(run_id):
                    logger.info("Workflow '%s' cancelled before attempt %d", run_id, attempt)
                    self._mark_task(task_run_id, t_name, run_id, TaskState.CANCELLED, error="Run cancelled by operator")
                    break

                self._audit(
                    run_id=run_id,
                    event_type="TASK_STARTED",
                    initiator=run.initiator,
                    agent_or_task=t_name,
                    details={"attempt": attempt},
                )
                start_time = datetime.now(timezone.utc)
                try:
                    # Pass inputs merged with accumulated context and cooperative cancellation check token
                    task_inputs = dict(run.inputs)
                    task_inputs.update(context)
                    cancel_token = CancellationToken(run_id, monotonic_deadline, self._is_run_cancelled)
                    context["cancellation_token"] = cancel_token
                    task_inputs["cancellation_token"] = cancel_token
                    task_inputs["_cancellation_check"] = lambda: not cancel_token.is_active
                    task_outputs = agent.execute(inputs=task_inputs, context=context)

                    # Post-task execution check: Did deadline expire or was run cancelled while task was running?
                    timed_out_post = time.monotonic() >= monotonic_deadline
                    cancelled_post = self._is_run_cancelled(run_id)
                    if timed_out_post or cancelled_post:
                        disc_reason = "Workflow deadline exceeded during task execution" if timed_out_post else "Workflow cancelled by operator during task execution"
                        logger.warning("Task '%s' completed after %s; discarding late result and halting pipeline", t_name, disc_reason)
                        failed_tasks.add(t_name)
                        self._mark_task(
                            task_run_id, t_name, run_id, TaskState.TIMED_OUT if timed_out_post else TaskState.CANCELLED,
                            error=f"{disc_reason}; late outputs discarded",
                        )
                        self._audit(
                            run_id=run_id,
                            event_type="TASK_TIMED_OUT" if timed_out_post else "TASK_CANCELLED",
                            initiator=run.initiator,
                            agent_or_task=t_name,
                            details={"reason": disc_reason, "late_discard": True},
                        )
                        for rem_task in ordered_tasks:
                            rem_name = rem_task.task_name
                            rem_tr = task_runs.get(rem_name, {})
                            if rem_name != t_name and rem_tr.get("state") in (TaskState.QUEUED.value, TaskState.RUNNING.value, None) and rem_name not in succeeded_tasks:
                                rem_id = rem_tr.get("task_run_id", f"trun_{uuid.uuid4().hex[:12]}")
                                self._mark_task(rem_id, rem_name, run_id, TaskState.TIMED_OUT if timed_out_post else TaskState.CANCELLED, error=disc_reason)
                        break

                    # Update context with task outputs
                    context.update(task_outputs)
                    succeeded_tasks.add(t_name)
                    task_success = True

                    self._update_task_run(
                        task_run_id=task_run_id,
                        run_id=run_id,
                        task_name=t_name,
                        agent_name=task_def.agent_name,
                        state=TaskState.SUCCEEDED,
                        attempt=attempt,
                        outputs=task_outputs,
                        started_at=start_time,
                        completed_at=datetime.now(timezone.utc),
                    )
                    self._audit(
                        run_id=run_id,
                        event_type="TASK_SUCCEEDED",
                        initiator=run.initiator,
                        agent_or_task=t_name,
                        details={"outputs_keys": list(task_outputs.keys())},
                    )
                    break

                except Exception as exc:
                    err_str = str(exc)
                    logger.warning("Task '%s' attempt %d failed: %s", t_name, attempt, err_str)

                    # Classify error: permanent validation vs transient
                    classification = (
                        ErrorClassification.PERMANENT_VALIDATION
                        if isinstance(exc, (ValueError, TypeError, KeyError))
                        else ErrorClassification.TRANSIENT
                    )

                    # If permanent validation or max attempts reached, fail task
                    if classification != ErrorClassification.TRANSIENT or attempt == max_attempts:
                        failed_tasks.add(t_name)
                        self._update_task_run(
                            task_run_id=task_run_id,
                            run_id=run_id,
                            task_name=t_name,
                            agent_name=task_def.agent_name,
                            state=TaskState.FAILED,
                            attempt=attempt,
                            error_message=err_str,
                            error_classification=classification,
                            started_at=start_time,
                            completed_at=datetime.now(timezone.utc),
                        )
                        self._audit(
                            run_id=run_id,
                            event_type="TASK_FAILED",
                            initiator=run.initiator,
                            agent_or_task=t_name,
                            details={"attempt": attempt, "error": err_str, "classification": classification.value},
                        )
                        break
                    attempt += 1

        # Check if cancelled asynchronously
        if self._is_run_cancelled(run_id):
            run.state = WorkflowState.CANCELLED
            run.completed_at = datetime.now(timezone.utc)
            run.outputs = context
            self.storage.save_workflow_run(run)
            return run

        # 5. Determine final workflow run state
        run.completed_at = datetime.now(timezone.utc)

        # Consolidate company dossier if components are present
        if "data_quality_report" in context and "deal_intelligence_report" in context:
            entity_id = run.inputs.get("entity_id")
            entity = self.storage.get_entity(entity_id) if entity_id else None
            entity_data = (
                entity.model_dump() if entity and hasattr(entity, "model_dump")
                else (dict(entity) if entity else {"entity_id": entity_id, "legal_name": ""})
            )
            context["company_dossier"] = {
                "entity": entity_data,
                "data_quality": {
                    "quality_score": context.get("data_quality_report", {}).get("overall_quality_score", 1.0),
                    "report": context.get("data_quality_report", {}),
                },
                "deal_intelligence": {
                    "total_funding_rounds": context.get("deal_intelligence_report", {}).get("total_funding_rounds", 0),
                    "total_transactions": context.get("deal_intelligence_report", {}).get("total_transactions", 0),
                    "report": context.get("deal_intelligence_report", {}),
                },
                "research_brief": context.get("research_brief", {}),
            }

        run.outputs = context

        # Check if any task was cancelled or failed due to duration limit
        all_truns = self.storage.get_task_runs_for_workflow(run_id)
        timed_out_any = any(
            tr.get("state") == TaskState.TIMED_OUT.value
            or "exceeded maximum duration limit" in (tr.get("error_message") or "")
            or "deadline exceeded" in (tr.get("error_message") or "")
            for tr in all_truns
        )

        if timed_out_any:
            run.error_message = f"Workflow execution exceeded maximum duration limit ({self.limits.workflow_timeout_sec}s)"
            final_state = WorkflowState.TIMED_OUT
        elif failed_tasks:
            if succeeded_tasks:
                final_state = WorkflowState.PARTIALLY_SUCCEEDED
            else:
                final_state = WorkflowState.FAILED
            run.error_message = f"Tasks failed: {list(failed_tasks)}"
        else:
            final_state = WorkflowState.SUCCEEDED

        validate_workflow_transition(run.state, final_state)
        run.state = final_state
        self.storage.save_workflow_run(run)

        self._audit(
            run_id=run_id,
            event_type=f"WORKFLOW_{final_state.value}",
            initiator=run.initiator,
            details={"succeeded": list(succeeded_tasks), "failed": list(failed_tasks), "skipped": list(skipped_tasks)},
        )
        return run

    def cancel_run(self, run_id: str, reason: str = "Operator cancelled") -> WorkflowRun:
        """Explicitly cancel an active or queued workflow run."""
        run_dict = self.storage.get_workflow_run(run_id)
        if not run_dict:
            raise WorkflowExecutionError(f"Workflow run '{run_id}' not found")

        run = WorkflowRun.model_validate(run_dict)
        if run.state in {WorkflowState.SUCCEEDED, WorkflowState.FAILED, WorkflowState.CANCELLED, WorkflowState.TIMED_OUT}:
            logger.info("Cannot cancel terminal run '%s'", run_id)
            return run

        validate_workflow_transition(run.state, WorkflowState.CANCELLED)
        run.state = WorkflowState.CANCELLED
        run.completed_at = datetime.now(timezone.utc)
        run.error_message = reason
        self.storage.save_workflow_run(run)

        # Cancel all pending/queued task runs
        for tr in self.storage.get_task_runs_for_workflow(run_id):
            if tr.get("state") in (TaskState.QUEUED.value, TaskState.RUNNING.value):
                self._mark_task(
                    tr["task_run_id"],
                    tr["task_name"],
                    run_id,
                    TaskState.CANCELLED,
                    error="Workflow cancelled by operator",
                )

        self._audit(
            run_id=run_id,
            event_type="WORKFLOW_CANCELLED",
            initiator=run.initiator,
            details={"reason": reason},
        )
        return run

    def resume_approved_run(self, run_id: str, workflow_defn: WorkflowDefinition) -> WorkflowRun:
        """Resume a workflow run that was paused in WAITING_FOR_APPROVAL once approved."""
        run_dict = self.storage.get_workflow_run(run_id)
        if not run_dict:
            raise WorkflowExecutionError(f"Workflow run '{run_id}' not found")

        run = WorkflowRun.model_validate(run_dict)
        if run.state != WorkflowState.WAITING_FOR_APPROVAL:
            raise WorkflowExecutionError(f"Cannot resume run in state '{run.state.value}' (expected WAITING_FOR_APPROVAL)")

        # Verify approval status
        pending = self.storage.list_pending_approvals(run_id=run_id)
        if pending:
            raise WorkflowExecutionError("Run still has pending approval requests")

        # Atomic transition strictly from WAITING_FOR_APPROVAL to RUNNING
        claimed = self.storage.claim_approval_workflow_run(run_id)
        if not claimed:
            fresh_run = self.storage.get_workflow_run(run_id)
            raise WorkflowExecutionError(
                f"Workflow run '{run_id}' could not be resumed (current state: {fresh_run.get('state') if fresh_run else 'unknown'})"
            )

        run.state = WorkflowState.RUNNING
        self._audit(run_id=run_id, event_type="WORKFLOW_RESUMED_AFTER_APPROVAL", initiator=run.initiator)

        _register_active_run(run_id)
        try:
            return self._execute_task_pipeline(run, workflow_defn)
        finally:
            _unregister_active_run(run_id)

    # ── Internal Helpers ───────────────────────────────────────────────────

    def _topological_sort(self, tasks: List[TaskDefinition]) -> List[TaskDefinition]:
        """Verify DAG properties and return tasks in dependency order."""
        task_map = {t.task_name: t for t in tasks}
        in_degree: Dict[str, int] = {t.task_name: 0 for t in tasks}
        adj_list: Dict[str, List[str]] = defaultdict(list)

        for t in tasks:
            for dep in t.dependencies:
                if dep not in task_map:
                    raise InvalidDependencyError(
                        f"Task '{t.task_name}' declares unknown dependency '{dep}'"
                    )
                adj_list[dep].append(t.task_name)
                in_degree[t.task_name] += 1

        queue = deque([name for name, deg in in_degree.items() if deg == 0])
        ordered: List[TaskDefinition] = []

        while queue:
            node = queue.popleft()
            ordered.append(task_map[node])
            for neighbor in adj_list[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(ordered) != len(tasks):
            raise CycleDetectedError("Circular dependency cycle detected in workflow task definitions")

        # Compute dependency depth for each node in the DAG
        depth: Dict[str, int] = {t.task_name: 1 for t in tasks}
        for t in ordered:
            for neighbor in adj_list[t.task_name]:
                depth[neighbor] = max(depth[neighbor], depth[t.task_name] + 1)

        max_depth = max(depth.values()) if depth else 0
        if max_depth > self.limits.max_dependency_depth:
            raise ResourceLimitExceededError(
                f"Workflow dependency depth {max_depth} exceeds limit of {self.limits.max_dependency_depth}"
            )

        return ordered

    def _mark_task(
        self,
        task_run_id: str,
        task_name: str,
        run_id: str,
        state: TaskState,
        error: str = "",
        classification: Optional[ErrorClassification] = None,
    ) -> None:
        """Mark a task run state and persist."""
        tr = TaskRun(
            task_run_id=task_run_id,
            run_id=run_id,
            task_name=task_name,
            agent_name="system",
            state=state,
            error_message=error,
            error_classification=classification,
            completed_at=datetime.now(timezone.utc),
        )
        self.storage.save_task_run(tr)

    def _update_task_run(
        self,
        task_run_id: str,
        run_id: str,
        task_name: str,
        agent_name: str,
        state: TaskState,
        attempt: int = 1,
        inputs: Optional[Dict[str, Any]] = None,
        outputs: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        error_classification: Optional[ErrorClassification] = None,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
    ) -> None:
        import json
        if outputs and len(json.dumps(outputs, default=str)) > self.limits.max_result_size_bytes:
            raise ResourceLimitExceededError(
                f"Task '{task_name}' output size exceeds limit of {self.limits.max_result_size_bytes} bytes"
            )

        tr = TaskRun(
            task_run_id=task_run_id,
            run_id=run_id,
            task_name=task_name,
            agent_name=agent_name,
            state=state,
            attempt=attempt,
            inputs=inputs or {},
            outputs=outputs or {},
            error_message=error_message,
            error_classification=error_classification,
            started_at=started_at,
            completed_at=completed_at,
        )
        self.storage.save_task_run(tr)

    def _request_approval(
        self,
        run_id: str,
        action_type: str,
        payload: Dict[str, Any],
        scope: str,
    ) -> None:
        """Create a human approval record for a privileged action."""
        approval_id = f"appr_{uuid.uuid4().hex[:12]}"
        req = ApprovalRequest(
            approval_id=approval_id,
            run_id=run_id,
            action_type=action_type,
            action_payload=payload,
            scope=scope,
            status=ApprovalStatus.PENDING,
            requested_at=datetime.now(timezone.utc),
        )
        self.storage.save_approval_request(req)

    def _audit(
        self,
        run_id: str,
        event_type: str,
        initiator: str = "system",
        agent_or_task: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        is_mutation: bool = False,
    ) -> None:
        """Log append-only audit event."""
        evt = AuditEvent(
            event_id=f"audit_{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            timestamp=datetime.now(timezone.utc),
            event_type=event_type,
            initiator=initiator,
            agent_or_task=agent_or_task,
            details=details or {},
            is_mutation=is_mutation,
        )
        self.storage.record_workflow_audit_event(evt)

    def recover_stale_runs(self) -> List[str]:
        """Detect and recover runs left in RUNNING state due to application restart or crash."""
        # pyrefly: ignore[missing-attribute]
        cursor = self.storage.conn.cursor()
        cursor.execute("SELECT run_id FROM workflow_runs WHERE state = 'RUNNING'")
        stale_ids = [row["run_id"] for row in cursor.fetchall()]
        recovered = []
        for r_id in stale_ids:
            if _is_run_active_in_process(r_id):
                logger.info("Workflow run '%s' is actively executing in current process; skipping recovery", r_id)
                continue
            run_dict = self.storage.get_workflow_run(r_id)
            if run_dict:
                run = WorkflowRun.model_validate(run_dict)
                run.state = WorkflowState.FAILED
                run.completed_at = datetime.now(timezone.utc)
                run.error_message = "Execution interrupted by application restart or process termination"
                self.storage.save_workflow_run(run)
                # Also cancel any orphaned tasks in RUNNING or QUEUED
                for tr in self.storage.get_task_runs_for_workflow(r_id):
                    if tr.get("state") in (TaskState.RUNNING.value, TaskState.QUEUED.value):
                        self._mark_task(
                            tr["task_run_id"],
                            tr["task_name"],
                            r_id,
                            TaskState.CANCELLED,
                            error="Interrupted by application restart",
                        )
                self._audit(
                    run_id=r_id,
                    event_type="WORKFLOW_FAILED",
                    initiator="system_recovery",
                    details={"reason": "Recovered from interrupted execution"},
                )
                recovered.append(r_id)
        return recovered
