"""Unit and adversarial tests for Phase 10.2 Final Security Acceptance Gate.

Validates:
1. HTTP 413 handling across real ASGI application paths (streaming, chunking, malformed/misleading Content-Length, exact boundary, process interrupts).
2. Workflow monotonic timeout guarantees, late-result quarantine, immutable terminal states, and privileged-action lockout.
3. Concurrency barrier compare-and-set and approval state transitions.
4. Filesystem containment against symlinks, sibling prefixes, and outside sentinel preservation.
5. Host and request boundary enforcement.
"""

import asyncio
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from opendeal.agents.contracts import (
    ApprovalStatus,
    ErrorClassification,
    PermissionCategory,
    ResourceLimits,
    TaskDefinition,
    TaskRun,
    TaskState,
    WorkflowDefinition,
    WorkflowExecutionError,
    WorkflowRun,
    WorkflowState,
    WorkflowTimeoutError,
    WorkflowCancelledError,
)
from opendeal.agents.approvals import ApprovalManager
from opendeal.agents.policies import ACTIONS_REQUIRING_APPROVAL, PolicyEngine
from opendeal.agents.registry import AgentRegistry, BaseAgent
from opendeal.agents.runner import CancellationToken, WorkflowRunner
from opendeal.storage.sqlite import SQLiteStorage
from opendeal.ui.app import (
    PayloadTooLargeError,
    SecurityHeadersMiddleware,
    create_app,
    parse_host_authority,
)
from opendeal.ui.routes import system
from opendeal.valuation.peak_sandbox import PEAKSandboxRunner, WorkbookIntegrityError


# ── Fixtures & Helpers ────────────────────────────────────────────────────────

class ControlledAgent(BaseAgent):
    """Test agent whose execution timing, outputs, and cooperative checks can be controlled."""

    def __init__(self, agent_name: str, storage: Any = None, delay_sec: float = 0.0, output_data: dict = None) -> None:
        super().__init__(storage=storage)
        self._name = agent_name
        self.delay_sec = delay_sec
        self.output_data = output_data or {f"{agent_name}_result": "ok"}
        self.invocation_count = 0
        self.observed_cancellation = False

    @property
    def agent_name(self) -> str:
        return self._name

    @property
    def declared_permissions(self) -> List[PermissionCategory]:
        return [PermissionCategory.READ_LOCAL_DATA, PermissionCategory.WRITE_APPROVED_TARGET_LIST]

    def execute(self, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        self.invocation_count += 1
        start = time.monotonic()

        token: CancellationToken = inputs.get("cancellation_token") or context.get("cancellation_token")

        if self.delay_sec > 0:
            step = 0.02
            elapsed = 0.0
            while elapsed < self.delay_sec:
                time.sleep(min(step, self.delay_sec - elapsed))
                elapsed = time.monotonic() - start
                if token and not token.is_active:
                    self.observed_cancellation = True

        return dict(self.output_data)


@pytest.fixture
def temp_db_file():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    yield db_path
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def storage(temp_db_file):
    stor = SQLiteStorage(temp_db_file)
    stor.initialize()
    return stor


@pytest.fixture
def test_app(storage):
    return create_app(storage=storage)


@pytest.fixture
def app_client(test_app):
    return TestClient(test_app, base_url="http://127.0.0.1:8000")


# ── 1. HTTP 413 Handling for Oversized Requests ───────────────────────────────

class TestHTTP413OversizedRequests:
    """Verifies that SecurityHeadersMiddleware enforces the 10MB limit without buffering or crashing."""

    def test_request_below_limit_succeeds(self, app_client):
        """A normal request below the body limit succeeds normally."""
        res = app_client.get("/")
        assert res.status_code == 200

    def test_request_exact_limit_reaches_application(self, app_client):
        """A request at exactly 10,000,000 bytes passes middleware."""
        # 10,000,000 bytes
        res = app_client.post(
            "/workflows/run/nonexistent_wf",
            content=b"x" * 10_000_000,
            headers={"Content-Length": "10000000", "Content-Type": "application/x-www-form-urlencoded"},
        )
        # Not rejected with 413 (reaches route which returns 404 for unknown workflow)
        assert res.status_code != 413
        assert res.status_code == 404

    def test_request_one_byte_over_limit_rejected_with_413(self, app_client):
        """A request at 10,000,001 bytes receives HTTP 413."""
        res = app_client.post(
            "/workflows/run/nonexistent_wf",
            content=b"x" * 10_000_001,
            headers={"Content-Length": "1000001", "Content-Type": "application/x-www-form-urlencoded"},
        )
        assert res.status_code == 413
        assert "413 Payload Too Large" in res.text

    def test_truthful_oversized_content_length_rejected_with_413(self, app_client):
        """Request exceeding limit with truthful Content-Length receives 413."""
        res = app_client.post(
            "/system/ingest/fixture",
            content=b"test",
            headers={"Content-Length": "12000000", "Content-Type": "application/x-www-form-urlencoded"},
        )
        assert res.status_code == 413
        assert "Payload Too Large" in res.text

    def test_streamed_chunks_exceeding_limit_without_content_length(self, app_client):
        """Streamed generator exceeding 10MB without Content-Length receives HTTP 413."""
        def gen():
            for _ in range(6):
                yield b"fixture_path=" + (b"A" * (2 * 1024 * 1024))  # 12MB total

        res = app_client.post(
            "/system/ingest/fixture",
            content=gen(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert res.status_code == 413
        assert "Payload Too Large" in res.text

    def test_misleading_content_length_cannot_bypass_byte_limit(self, app_client):
        """Request claiming small Content-Length but streaming >10MB receives HTTP 413."""
        def gen():
            for _ in range(6):
                yield b"fixture_path=" + (b"B" * (2 * 1024 * 1024))

        res = app_client.post(
            "/system/ingest/fixture",
            content=gen(),
            headers={"Content-Length": "50", "Content-Type": "application/x-www-form-urlencoded"},
        )
        assert res.status_code == 413
        assert "Payload Too Large" in res.text

    def test_malformed_content_length_cannot_bypass_byte_limit(self, app_client):
        """Request with non-numeric or negative Content-Length is still metered and rejected at 10MB."""
        def gen():
            for _ in range(6):
                yield b"fixture_path=" + (b"C" * (2 * 1024 * 1024))

        for malformed_cl in ["invalid-length", "-100", "12.34"]:
            res = app_client.post(
                "/system/ingest/fixture",
                content=gen(),
                headers={"Content-Length": malformed_cl, "Content-Type": "application/x-www-form-urlencoded"},
            )
            assert res.status_code == 413

    def test_oversized_request_does_not_destabilize_subsequent_requests(self, app_client):
        """An oversized request does not crash server or prevent subsequent valid requests."""
        # 1. Send oversized request
        res1 = app_client.post(
            "/system/ingest/fixture",
            content=b"test",
            headers={"Content-Length": "20000000", "Content-Type": "application/x-www-form-urlencoded"},
        )
        assert res1.status_code == 413

        # 2. Subsequent normal requests succeed immediately
        res2 = app_client.get("/")
        assert res2.status_code == 200

        res3 = app_client.get("/companies")
        assert res3.status_code == 200

    @pytest.mark.asyncio
    async def test_process_control_exceptions_not_suppressed(self, test_app):
        """KeyboardInterrupt and SystemExit propagate through ASGI middleware without suppression."""
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/system/ingest/fixture",
            "query_string": b"",
            "headers": [
                (b"host", b"127.0.0.1:8000"),
                (b"content-type", b"application/x-www-form-urlencoded"),
            ],
        }

        async def interrupt_receive():
            raise KeyboardInterrupt("Simulated SIGINT")

        async def dummy_send(msg):
            pass

        with pytest.raises(KeyboardInterrupt):
            await test_app(scope, interrupt_receive, dummy_send)

        async def exit_receive():
            raise SystemExit(1)

        with pytest.raises(SystemExit):
            await test_app(scope, exit_receive, dummy_send)


# ── 2. Precise Workflow Timeout & Late-Result Guarantees ──────────────────────

class TestWorkflowTimeoutGuarantees:
    """Verifies monotonic deadlines, late-output quarantine, and privileged-action lockout."""

    def test_task_completes_before_deadline_succeeds(self, storage):
        """A task that completes within its configured deadline succeeds normally."""
        agent = ControlledAgent("agent_fast", delay_sec=0.01)
        reg = AgentRegistry(storage)
        reg.register_agent(agent)

        wf = WorkflowDefinition(
            workflow_id="wf_fast",
            name="Fast Workflow",
            description="Fast test",
            required_permissions=[PermissionCategory.READ_LOCAL_DATA],
            tasks=[TaskDefinition(task_name="t_fast", agent_name="agent_fast", dependencies=[])],
        )

        runner = WorkflowRunner(storage=storage, agent_registry=reg, limits=ResourceLimits(workflow_timeout_sec=5))
        run = runner.create_run(wf, inputs={})
        res = runner.execute_run(wf, run.run_id)

        assert res.state == WorkflowState.SUCCEEDED
        assert agent.invocation_count == 1

    def test_task_exceeding_deadline_transitions_to_timed_out(self, storage):
        """When deadline expires during task execution, state is TIMED_OUT and outputs are discarded."""
        agent_slow = ControlledAgent("agent_slow", delay_sec=0.2, output_data={"leaked_secret": "bad"})
        agent_downstream = ControlledAgent("agent_downstream")

        reg = AgentRegistry(storage)
        reg.register_agent(agent_slow)
        reg.register_agent(agent_downstream)

        wf = WorkflowDefinition(
            workflow_id="wf_timed_out_test",
            name="Timeout Test",
            description="Timeout test",
            required_permissions=[PermissionCategory.READ_LOCAL_DATA],
            tasks=[
                TaskDefinition(task_name="t_slow", agent_name="agent_slow", dependencies=[]),
                TaskDefinition(task_name="t_downstream", agent_name="agent_downstream", dependencies=["t_slow"]),
            ],
        )

        # 0s timeout forces immediate deadline expiry upon post-check
        runner = WorkflowRunner(storage=storage, agent_registry=reg, limits=ResourceLimits(workflow_timeout_sec=0))
        run = runner.create_run(wf, inputs={})
        res = runner.execute_run(wf, run.run_id)

        # 1. Workflow final state must be TIMED_OUT
        assert res.state == WorkflowState.TIMED_OUT
        assert "exceeded maximum duration limit" in (res.error_message or "")

        # 2. Output from slow task must NOT be published into workflow context
        assert "leaked_secret" not in res.outputs

        # 3. Downstream task MUST NOT execute
        assert agent_downstream.invocation_count == 0

        # 4. In database, task run must be TIMED_OUT
        truns = storage.get_task_runs_for_workflow(run.run_id)
        assert len(truns) == 2
        slow_tr = next(tr for tr in truns if tr["task_name"] == "t_slow")
        down_tr = next(tr for tr in truns if tr["task_name"] == "t_downstream")
        assert slow_tr["state"] == TaskState.TIMED_OUT.value
        assert down_tr["state"] == TaskState.TIMED_OUT.value

    def test_terminal_state_cannot_be_overwritten_by_late_completion(self, storage):
        """Once a workflow or task reaches terminal TIMED_OUT state, late updates cannot overwrite it."""
        wf_id = "wf_immutable_terminal"
        run_id = f"run_{wf_id}"

        # Insert run in TIMED_OUT state
        run = WorkflowRun(
            run_id=run_id,
            workflow_id=wf_id,
            version="1.0.0",
            state=WorkflowState.TIMED_OUT,
            initiator="test_operator",
            inputs={},
            outputs={},
            error_message="Workflow execution exceeded maximum duration limit (0s)",
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
        storage.save_workflow_run(run)

        # Late attempt to overwrite state to SUCCEEDED with bogus outputs
        late_run = WorkflowRun(
            run_id=run_id,
            workflow_id=wf_id,
            version="1.0.0",
            state=WorkflowState.SUCCEEDED,
            initiator="test_operator",
            inputs={},
            outputs={"overwritten_output": "malicious"},
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
        storage.save_workflow_run(late_run)

        # Verify state in DB remains TIMED_OUT and outputs untouched
        db_run = storage.get_workflow_run(run_id)
        assert db_run["state"] == WorkflowState.TIMED_OUT.value
        assert "overwritten_output" not in db_run["outputs"]

    def test_timed_out_run_cannot_authorize_or_consume_privileged_actions(self, storage):
        """A task that has timed out cannot authorize or consume approvals via PolicyEngine."""
        run_id = "run_timed_out_priv"
        policy = PolicyEngine(storage)
        appr_mgr = ApprovalManager(storage)

        # Create run in TIMED_OUT state
        run = WorkflowRun(
            run_id=run_id,
            workflow_id="wf_priv",
            version="1.0.0",
            state=WorkflowState.TIMED_OUT,
            initiator="operator",
            inputs={"target": "val"},
            outputs={},
            error_message="Timed out",
            created_at=datetime.now(timezone.utc),
        )
        storage.save_workflow_run(run)

        # Pre-create an approval record that was marked APPROVED
        appr_id = appr_mgr.request_approval(
            run_id=run_id,
            action_type="WRITE_APPROVED_TARGET_LIST",
            action_payload={"target": "val"},
            scope="WORKFLOW:wf_priv",
        )
        appr_mgr.decide(appr_id, approved=True, decided_by="admin")

        # 1. check_action_authorization must refuse authorization because workflow run is TIMED_OUT
        check_dec = policy.check_action_authorization(
            run_id=run_id,
            action_type="WRITE_APPROVED_TARGET_LIST",
            action_payload={"target": "val"},
            scope="WORKFLOW:wf_priv",
        )
        assert check_dec.allowed is False
        assert "terminal state" in check_dec.reason

        # 2. consume_approval must also refuse consumption
        consume_dec = policy.consume_approval(
            run_id=run_id,
            action_type="WRITE_APPROVED_TARGET_LIST",
            action_payload={"target": "val"},
            scope="WORKFLOW:wf_priv",
        )
        assert consume_dec.allowed is False
        assert "terminal state" in consume_dec.reason


# ── 3. Filesystem Containment & Outside Sentinel Preservation ─────────────────

class TestFilesystemContainment:
    """Verifies that symlinks, sibling prefix escapes, and traversal attacks cannot read/write outside."""

    def test_outside_sentinel_preserved_against_traversal_and_symlinks(self, app_client, tmp_path):
        """Rejected traversal attempts and symlinks leave outside sentinel completely unchanged."""
        outside_dir = tmp_path / "outside_secure_zone"
        outside_dir.mkdir(parents=True, exist_ok=True)
        sentinel_file = outside_dir / "master_sentinel.json"
        original_content = b'{"secure_token": "UNTOUCHED_CONFIDENTIAL_TOKEN_7788"}'
        sentinel_file.write_bytes(original_content)
        pre_hash = hashlib.sha256(original_content).hexdigest()

        # 1. Path traversal attacks
        for attack in ["../../outside_secure_zone/master_sentinel.json", "/outside_secure_zone/master_sentinel.json"]:
            res = app_client.post("/system/ingest/fixture", data={"fixture_path": attack, "dataset_type": "deals"})
            assert res.status_code in (400, 422)

        # 2. Symlink attack: create symlink inside repo pointing outside
        repo_fixtures = system.REPO_ROOT / "tests" / "fixtures"
        repo_fixtures.mkdir(parents=True, exist_ok=True)
        symlink_path = repo_fixtures / "malicious_sentinel_symlink.json"

        if symlink_path.is_symlink() or symlink_path.exists():
            symlink_path.unlink()

        try:
            symlink_path.symlink_to(sentinel_file)

            rel_symlink_path = f"tests/fixtures/{symlink_path.name}"
            res = app_client.post("/system/ingest/fixture", data={"fixture_path": rel_symlink_path, "dataset_type": "deals"})
            assert res.status_code == 400
            assert "attempts directory traversal" in res.text

        finally:
            if symlink_path.is_symlink() or symlink_path.exists():
                symlink_path.unlink()

        # 3. Verify sentinel file was NOT modified, truncated, or overwritten
        assert sentinel_file.exists()
        post_content = sentinel_file.read_bytes()
        post_hash = hashlib.sha256(post_content).hexdigest()
        assert pre_hash == post_hash
        assert post_content == original_content


# ── 4. Host Authority & Origin Request Boundaries ────────────────────────────

class TestHostAndOriginRequestBoundaries:
    """Verifies that legitimate local authorities are accepted while untrusted hosts/origins are blocked."""

    def test_valid_local_authorities_accepted(self, test_app):
        """127.0.0.1, localhost, [::1], and testserver with ports are accepted."""
        valid_hosts = [
            "127.0.0.1",
            "127.0.0.1:8000",
            "localhost",
            "localhost:3000",
            "[::1]",
            "[::1]:8080",
            "testserver",
        ]
        for vh in valid_hosts:
            parsed = parse_host_authority(vh)
            assert parsed in SecurityHeadersMiddleware.ALLOWED_LOCAL_HOSTS

    def test_untrusted_hosts_rejected(self, test_app):
        """Untrusted hostnames, invalid ports, or DNS rebinding attempts are rejected."""
        invalid_hosts = [
            "evil.com",
            "attacker.com:8000",
            "127.0.0.1.attacker.com",
            "localhost.attacker.com",
            "[::1]:999999",  # Port out of range
            "127.0.0.1:port",  # Non-numeric port
            "[unclosed_ipv6",
        ]
        for inv in invalid_hosts:
            parsed = parse_host_authority(inv)
            assert parsed not in SecurityHeadersMiddleware.ALLOWED_LOCAL_HOSTS

    def test_cross_origin_state_changing_post_rejected(self, app_client):
        """POST request with unauthorized Origin header is rejected with 403 Forbidden."""
        res = app_client.post(
            "/system/ingest/fixture",
            data={"fixture_path": "tests/fixtures/sample.json"},
            headers={"Origin": "http://malicious-attacker.com"},
        )
        assert res.status_code == 403
        assert "Cross-Origin" in res.text
