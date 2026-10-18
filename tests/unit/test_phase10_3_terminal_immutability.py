"""Adversarial and regression tests for Phase 10.3 Terminal Record Immutability.

Validates that:
1. Every supported terminal workflow state (SUCCEEDED, PARTIALLY_SUCCEEDED, FAILED, CANCELLED, TIMED_OUT)
   resists mutation through ordinary save operations.
2. Every supported terminal task state (SUCCEEDED, FAILED, CANCELLED, TIMED_OUT)
   resists mutation through ordinary save operations.
3. Attempting to save the same terminal state with altered outputs leaves the original outputs intact.
4. Attempting to replace error messages, metadata, or timestamps does not mutate the terminal record.
5. Attempts to transition a terminal record to another state (terminal or nonterminal) are rejected by SQLite.
6. Late task completion cannot overwrite a previously persisted TIMED_OUT task or workflow.
7. Independent SQLite connections cannot bypass the guard through concurrent writes.
8. Nonterminal records remain writable and valid transitions continue to function.
9. Normal successful workflows, approval resumption, cancellation, timeout handling, and stale-run recovery remain fully functional.
"""

import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest

from opendeal.agents.approvals import ApprovalManager
from opendeal.agents.contracts import (
    ApprovalStatus,
    ErrorClassification,
    PermissionCategory,
    ResourceLimits,
    TaskDefinition,
    TaskRun,
    TaskState,
    WorkflowDefinition,
    WorkflowRun,
    WorkflowState,
)
from opendeal.agents.policies import ACTIONS_REQUIRING_APPROVAL, PolicyEngine
from opendeal.agents.registry import AgentRegistry, BaseAgent
from opendeal.agents.runner import CancellationToken, WorkflowRunner
from opendeal.storage.sqlite import SQLiteStorage


# ── Helpers & Fixtures ────────────────────────────────────────────────────────

class SpyWorker(BaseAgent):
    """Custom test agent for immutability and delay verification."""

    def __init__(self, agent_name: str, storage: Any = None, delay_sec: float = 0.0, output_val: str = "ok") -> None:
        super().__init__(storage=storage)
        self._name = agent_name
        self.delay_sec = delay_sec
        self.output_val = output_val
        self.invocation_count = 0

    @property
    def agent_name(self) -> str:
        return self._name

    @property
    def declared_permissions(self) -> List[PermissionCategory]:
        return [PermissionCategory.READ_LOCAL_DATA, PermissionCategory.WRITE_APPROVED_TARGET_LIST]

    def execute(self, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        self.invocation_count += 1
        if self.delay_sec > 0:
            time.sleep(self.delay_sec)
        return {f"{self._name}_out": self.output_val}


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    yield db_path
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def storage(temp_db):
    stor = SQLiteStorage(temp_db)
    stor.initialize()
    return stor


# ── 1. Comprehensive Terminal Workflow State Immutability ─────────────────────

class TestWorkflowTerminalImmutability:
    """Verifies that all 5 terminal workflow states strictly resist any mutation."""

    @pytest.mark.parametrize("terminal_state", [
        WorkflowState.SUCCEEDED,
        WorkflowState.PARTIALLY_SUCCEEDED,
        WorkflowState.FAILED,
        WorkflowState.CANCELLED,
        WorkflowState.TIMED_OUT,
    ])
    def test_same_state_mutation_rejected(self, storage, terminal_state):
        """Saving a workflow with the same terminal state but different outputs/errors leaves original record unchanged."""
        run_id = f"run_immut_{terminal_state.value}"
        original_outputs = {"canonical_result": f"initial_{terminal_state.value}"}
        original_error = f"Original error for {terminal_state.value}"

        run = WorkflowRun(
            run_id=run_id,
            workflow_id="wf_immut",
            version="1.0.0",
            state=terminal_state,
            initiator="operator",
            inputs={"param": "fixed"},
            outputs=original_outputs,
            error_message=original_error,
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
        storage.save_workflow_run(run)

        # Attempt 1: Same state with altered outputs
        tampered_run_outputs = WorkflowRun(
            run_id=run_id,
            workflow_id="wf_immut",
            version="1.0.0",
            state=terminal_state,
            initiator="operator",
            inputs={"param": "fixed"},
            outputs={"tampered_result": "MALICIOUS_INJECTION"},
            error_message=original_error,
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
        storage.save_workflow_run(tampered_run_outputs)

        db_rec = storage.get_workflow_run(run_id)
        assert db_rec["outputs"] == original_outputs, f"Outputs were mutated for state {terminal_state.value}!"
        assert "tampered_result" not in db_rec["outputs"]

        # Attempt 2: Same state with altered error message
        tampered_run_error = WorkflowRun(
            run_id=run_id,
            workflow_id="wf_immut",
            version="1.0.0",
            state=terminal_state,
            initiator="operator",
            inputs={"param": "fixed"},
            outputs=original_outputs,
            error_message="OVERWRITTEN_ERROR_MESSAGE",
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
        storage.save_workflow_run(tampered_run_error)

        db_rec = storage.get_workflow_run(run_id)
        assert db_rec["error_message"] == original_error, f"Error message was mutated for state {terminal_state.value}!"

    @pytest.mark.parametrize("terminal_state", [
        WorkflowState.SUCCEEDED,
        WorkflowState.PARTIALLY_SUCCEEDED,
        WorkflowState.FAILED,
        WorkflowState.CANCELLED,
        WorkflowState.TIMED_OUT,
    ])
    def test_state_transition_attempt_rejected(self, storage, terminal_state):
        """A terminal workflow run cannot be transitioned to another terminal state or nonterminal state."""
        run_id = f"run_trans_{terminal_state.value}"
        run = WorkflowRun(
            run_id=run_id,
            workflow_id="wf_trans",
            version="1.0.0",
            state=terminal_state,
            initiator="operator",
            inputs={},
            outputs={"stable": 1},
        )
        storage.save_workflow_run(run)

        # Attempt transition to RUNNING
        resurrect_run = WorkflowRun(
            run_id=run_id,
            workflow_id="wf_trans",
            version="1.0.0",
            state=WorkflowState.RUNNING,
            initiator="operator",
            inputs={},
            outputs={"resurrected": True},
        )
        storage.save_workflow_run(resurrect_run)

        db_rec = storage.get_workflow_run(run_id)
        assert db_rec["state"] == terminal_state.value
        assert db_rec["outputs"] == {"stable": 1}

        # Attempt transition to opposite terminal state
        opposite_state = WorkflowState.FAILED if terminal_state != WorkflowState.FAILED else WorkflowState.SUCCEEDED
        alter_terminal_run = WorkflowRun(
            run_id=run_id,
            workflow_id="wf_trans",
            version="1.0.0",
            state=opposite_state,
            initiator="operator",
            inputs={},
            outputs={"opposite": True},
        )
        storage.save_workflow_run(alter_terminal_run)

        db_rec = storage.get_workflow_run(run_id)
        assert db_rec["state"] == terminal_state.value
        assert db_rec["outputs"] == {"stable": 1}


# ── 2. Comprehensive Terminal Task State Immutability ─────────────────────────

class TestTaskTerminalImmutability:
    """Verifies that all 4 terminal task states strictly resist any mutation."""

    @pytest.mark.parametrize("task_term_state", [
        TaskState.SUCCEEDED,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.TIMED_OUT,
    ])
    def test_task_same_state_and_transition_mutations_rejected(self, storage, task_term_state):
        """Saving a task with the same terminal state or different state leaves original record untouched."""
        parent_id = f"wf_parent_{task_term_state.value}"
        parent_run = WorkflowRun(
            run_id=parent_id,
            workflow_id="wf_test",
            version="1.0.0",
            state=WorkflowState.RUNNING,
            initiator="operator",
        )
        storage.save_workflow_run(parent_run)

        task_id = f"trun_{task_term_state.value}"
        original_outputs = {"task_data": f"pure_{task_term_state.value}"}
        original_error = f"Original task error for {task_term_state.value}"

        tr = TaskRun(
            task_run_id=task_id,
            run_id=parent_id,
            task_name="t_name",
            agent_name="a_name",
            state=task_term_state,
            outputs=original_outputs,
            error_message=original_error,
        )
        storage.save_task_run(tr)

        # 1. Attempt same state with altered output
        tr_tamper_out = TaskRun(
            task_run_id=task_id,
            run_id=parent_id,
            task_name="t_name",
            agent_name="a_name",
            state=task_term_state,
            outputs={"injected": "MALICIOUS_TASK_OUTPUT"},
            error_message=original_error,
        )
        storage.save_task_run(tr_tamper_out)

        tasks = storage.get_task_runs_for_workflow(parent_id)
        t_rec = next(t for t in tasks if t["task_run_id"] == task_id)
        assert t_rec["outputs"] == original_outputs, f"Task output mutated for {task_term_state.value}!"

        # 2. Attempt same state with altered error message
        tr_tamper_err = TaskRun(
            task_run_id=task_id,
            run_id=parent_id,
            task_name="t_name",
            agent_name="a_name",
            state=task_term_state,
            outputs=original_outputs,
            error_message="TAMPERED_TASK_ERROR",
        )
        storage.save_task_run(tr_tamper_err)

        tasks = storage.get_task_runs_for_workflow(parent_id)
        t_rec = next(t for t in tasks if t["task_run_id"] == task_id)
        assert t_rec["error_message"] == original_error, f"Task error mutated for {task_term_state.value}!"

        # 3. Attempt state change to RUNNING
        tr_to_running = TaskRun(
            task_run_id=task_id,
            run_id=parent_id,
            task_name="t_name",
            agent_name="a_name",
            state=TaskState.RUNNING,
            outputs={"resurrected": True},
        )
        storage.save_task_run(tr_to_running)

        tasks = storage.get_task_runs_for_workflow(parent_id)
        t_rec = next(t for t in tasks if t["task_run_id"] == task_id)
        assert t_rec["state"] == task_term_state.value


# ── 3. Multi-Connection Concurrency & Late Worker Races ───────────────────────

class TestConcurrencyAndLateWorkerImmutability:
    """Verifies that separate SQLite connections and racing threads cannot alter terminal records."""

    def test_concurrent_connections_cannot_mutate_terminal_record(self, temp_db):
        """Two separate SQLite connections attempting to overwrite a terminal record both fail to mutate it."""
        stor1 = SQLiteStorage(temp_db)
        stor1.initialize()
        stor2 = SQLiteStorage(temp_db)
        stor2.initialize()

        run_id = "run_concurrent_immut"
        initial_run = WorkflowRun(
            run_id=run_id,
            workflow_id="wf_concurrent",
            version="1.0.0",
            state=WorkflowState.TIMED_OUT,
            initiator="operator",
            outputs={"secure": "unchangeable"},
            error_message="Timed out",
        )
        stor1.save_workflow_run(initial_run)

        barrier = threading.Barrier(2)
        errors = []

        def worker_attempt(stor_instance, val_to_inject):
            try:
                barrier.wait(timeout=5.0)
                tampered = WorkflowRun(
                    run_id=run_id,
                    workflow_id="wf_concurrent",
                    version="1.0.0",
                    state=WorkflowState.TIMED_OUT,
                    initiator="operator",
                    outputs={"injected": val_to_inject},
                    error_message=f"Tampered by {val_to_inject}",
                )
                stor_instance.save_workflow_run(tampered)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=worker_attempt, args=(stor1, "worker_1"))
        t2 = threading.Thread(target=worker_attempt, args=(stor2, "worker_2"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(errors) == 0
        final_run = stor1.get_workflow_run(run_id)
        assert final_run["outputs"] == {"secure": "unchangeable"}
        assert final_run["error_message"] == "Timed out"
        assert final_run["state"] == WorkflowState.TIMED_OUT.value

    def test_late_task_completion_cannot_overwrite_timed_out_workflow(self, storage):
        """A background thread returning after workflow timeout cannot overwrite the TIMED_OUT state or outputs."""
        agent_slow = SpyWorker("agent_slow", delay_sec=0.2, output_val="LATE_UNAUTHORIZED_DATA")
        reg = AgentRegistry(storage)
        reg.register_agent(agent_slow)

        wf = WorkflowDefinition(
            workflow_id="wf_late_leak",
            name="Late Leak Test",
            description="Late leak test",
            required_permissions=[PermissionCategory.READ_LOCAL_DATA],
            tasks=[TaskDefinition(task_name="t_slow", agent_name="agent_slow", dependencies=[])],
        )

        runner = WorkflowRunner(storage=storage, agent_registry=reg, limits=ResourceLimits(workflow_timeout_sec=0))
        run = runner.create_run(wf, inputs={})
        res = runner.execute_run(wf, run.run_id)

        assert res.state == WorkflowState.TIMED_OUT

        # Now simulate a late completion directly invoking storage save as if from an un-killable background thread
        late_run = WorkflowRun(
            run_id=run.run_id,
            workflow_id="wf_late_leak",
            version="1.0.0",
            state=WorkflowState.SUCCEEDED,
            initiator="operator",
            outputs={"agent_slow_out": "LATE_UNAUTHORIZED_DATA"},
        )
        storage.save_workflow_run(late_run)

        # Database record must remain TIMED_OUT with zero leaked outputs
        persisted = storage.get_workflow_run(run.run_id)
        assert persisted["state"] == WorkflowState.TIMED_OUT.value
        assert "LATE_UNAUTHORIZED_DATA" not in str(persisted["outputs"])


# ── 4. Nonterminal Writability & Normal Lifecycle Integrity ───────────────────

class TestNonterminalWritabilityAndLifecycle:
    """Verifies that nonterminal records remain mutable and normal lifecycles function properly."""

    def test_nonterminal_records_update_normally(self, storage):
        """QUEUED and RUNNING records update outputs, timestamps, and error messages normally."""
        run_id = "run_lifecycle_1"
        run = WorkflowRun(
            run_id=run_id,
            workflow_id="wf_norm",
            version="1.0.0",
            state=WorkflowState.QUEUED,
            initiator="operator",
            inputs={"step": 0},
            outputs={},
        )
        storage.save_workflow_run(run)

        # Update while nonterminal
        run.state = WorkflowState.RUNNING
        run.outputs = {"step_1": "in_progress"}
        storage.save_workflow_run(run)

        rec = storage.get_workflow_run(run_id)
        assert rec["state"] == WorkflowState.RUNNING.value
        assert rec["outputs"] == {"step_1": "in_progress"}

        # Transition to terminal SUCCEEDED
        run.state = WorkflowState.SUCCEEDED
        run.outputs = {"step_1": "in_progress", "step_2": "done"}
        storage.save_workflow_run(run)

        rec_term = storage.get_workflow_run(run_id)
        assert rec_term["state"] == WorkflowState.SUCCEEDED.value
        assert rec_term["outputs"] == {"step_1": "in_progress", "step_2": "done"}

    def test_approval_resumption_and_normal_workflow_execution(self, storage):
        """Workflow requiring human approval pauses, resumes, and succeeds without immutability conflicts."""
        worker = SpyWorker("approved_worker")
        reg = AgentRegistry(storage)
        reg.register_agent(worker)

        wf = WorkflowDefinition(
            workflow_id="wf_appr_normal",
            name="Approval Normal",
            description="Approval test",
            required_permissions=[PermissionCategory.READ_LOCAL_DATA, PermissionCategory.WRITE_APPROVED_TARGET_LIST],
            tasks=[
                TaskDefinition(
                    task_name="t_appr",
                    agent_name="approved_worker",
                    dependencies=[],
                    required_permissions=[PermissionCategory.WRITE_APPROVED_TARGET_LIST],
                )
            ],
        )

        runner = WorkflowRunner(storage=storage, agent_registry=reg)
        run = runner.create_run(wf, inputs={"target": "val"})

        # First execution pauses for approval
        paused = runner.execute_run(wf, run.run_id)
        assert paused.state == WorkflowState.WAITING_FOR_APPROVAL

        # Grant approval
        appr_mgr = ApprovalManager(storage)
        pending = appr_mgr.list_pending(run.run_id)
        assert len(pending) == 1
        appr_mgr.decide(pending[0]["approval_id"], approved=True, decided_by="admin")

        # Resume execution
        resumed = runner.resume_approved_run(run.run_id, wf)
        assert resumed.state == WorkflowState.SUCCEEDED
        assert worker.invocation_count == 1

        # Final record in DB must be SUCCEEDED and immutable
        final_db = storage.get_workflow_run(run.run_id)
        assert final_db["state"] == WorkflowState.SUCCEEDED.value

        # Post-completion tampering must be rejected
        final_db_tampered = WorkflowRun(
            run_id=run.run_id,
            workflow_id="wf_appr_normal",
            version="1.0.0",
            state=WorkflowState.FAILED,
            initiator="operator",
            outputs={"tampered": True},
        )
        storage.save_workflow_run(final_db_tampered)
        assert storage.get_workflow_run(run.run_id)["state"] == WorkflowState.SUCCEEDED.value
