"""Policy and permissions enforcement engine for bounded agent workflows."""

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from opendeal.agents.contracts import (
    ApprovalRequest,
    ApprovalStatus,
    PermissionCategory,
    TaskDefinition,
    WorkflowDefinition,
)
from opendeal.storage.base import StorageInterface

logger = logging.getLogger(__name__)

# Privileged action categories that strictly mandate explicit human approval
ACTIONS_REQUIRING_APPROVAL = {
    "WRITE_APPROVED_TARGET_LIST": PermissionCategory.WRITE_APPROVED_TARGET_LIST,
    "REQUEST_LIVE_INGESTION": PermissionCategory.REQUEST_LIVE_INGESTION,
    "PROPOSE_DATABASE_CHANGE": PermissionCategory.PROPOSE_DATABASE_CHANGE,
}


class PolicyDecision(BaseModel):
    """Result of an evaluation by the PolicyEngine."""
    allowed: bool
    requires_approval: bool = False
    reason: str = ""
    required_permission: Optional[PermissionCategory] = None


class PolicyEngine:
    """Enforces fine-grained permission boundaries, approval checks, and input hygiene."""

    def __init__(self, storage: Optional[StorageInterface] = None) -> None:
        self.storage = storage

    def check_task_permission(
        self,
        workflow_defn: WorkflowDefinition,
        task_defn: TaskDefinition,
    ) -> PolicyDecision:
        """Verify that all permissions required by a task are declared in the workflow definition.

        Prevents privilege escalation from undeclared agent behaviors.
        """
        declared_perms = set(workflow_defn.required_permissions)

        for perm in task_defn.required_permissions:
            if perm not in declared_perms:
                logger.warning(
                    "Policy rejection: Task '%s' requests undeclared permission '%s' in workflow '%s'",
                    task_defn.task_name,
                    perm.value,
                    workflow_defn.workflow_id,
                )
                return PolicyDecision(
                    allowed=False,
                    requires_approval=False,
                    reason=f"Task '{task_defn.task_name}' requests undeclared permission '{perm.value}'",
                    required_permission=perm,
                )

        return PolicyDecision(
            allowed=True,
            reason=f"All permissions for task '{task_defn.task_name}' are declared and authorized",
        )

    def check_action_authorization(
        self,
        run_id: str,
        action_type: str,
        action_payload: Dict[str, Any],
        scope: str = "GLOBAL",
    ) -> PolicyDecision:
        """Enforce strict human-in-the-loop approval before executing privileged operations.

        Guarantees:
        - Approvals are strictly scoped to the exact run_id (no cross-run reuse).
        - Approvals match the exact action_type and payload hash.
        - Client-supplied flags (e.g. approved=true) without persistent record are rejected.
        """
        if action_type not in ACTIONS_REQUIRING_APPROVAL:
            # Action does not require privileged approval
            return PolicyDecision(allowed=True, reason=f"Action '{action_type}' is standard read/compute")

        required_perm = ACTIONS_REQUIRING_APPROVAL[action_type]

        if not self.storage:
            return PolicyDecision(
                allowed=False,
                requires_approval=True,
                reason="Storage required to verify approval record",
                required_permission=required_perm,
            )

        # Check that workflow run is active before checking approvals
        cursor = self.storage.conn.cursor()
        cursor.execute("SELECT state FROM workflow_runs WHERE run_id = ?", (run_id,))
        wf_row = cursor.fetchone()
        if wf_row and wf_row["state"] in ("CANCELLED", "TIMED_OUT", "FAILED", "SUCCEEDED"):
            return PolicyDecision(
                allowed=False,
                requires_approval=True,
                reason=f"Workflow run '{run_id}' is in terminal state '{wf_row['state']}'; cannot authorize privileged actions",
                required_permission=required_perm,
            )

        # Retrieve all approvals for this run
        cursor.execute(
            """
            SELECT * FROM workflow_approvals
            WHERE run_id = ? AND action_type = ? AND status = 'APPROVED'
            """,
            (run_id, action_type),
        )
        rows = cursor.fetchall()
        if not rows:
            return PolicyDecision(
                allowed=False,
                requires_approval=True,
                reason=f"Action '{action_type}' requires explicit human approval for run '{run_id}'",
                required_permission=required_perm,
            )

        # Compute payload hash to prevent tampering with action parameters
        current_payload_json = json.dumps(action_payload, sort_keys=True)
        current_hash = hashlib.sha256(current_payload_json.encode("utf-8")).hexdigest()

        for row in rows:
            record_payload = json.loads(row["action_payload_json"])
            rec_hash = hashlib.sha256(json.dumps(record_payload, sort_keys=True).encode("utf-8")).hexdigest()
            rec_scope = row["scope"]

            # Exact match on scope and payload hash
            if rec_scope == scope and rec_hash == current_hash:
                return PolicyDecision(
                    allowed=True,
                    requires_approval=False,
                    reason=f"Action '{action_type}' authorized by approval record '{row['approval_id']}'",
                    required_permission=required_perm,
                )

        return PolicyDecision(
            allowed=False,
            requires_approval=True,
            reason=f"No approved record matches scope '{scope}' and payload hash '{current_hash}'",
            required_permission=required_perm,
        )

    def consume_approval(
        self,
        run_id: str,
        action_type: str,
        action_payload: Dict[str, Any],
        scope: str = "GLOBAL",
    ) -> PolicyDecision:
        """Atomically consume an approval record so it cannot be reused.

        Guarantees:
        - Atomically transitions status from APPROVED to CONSUMED.
        - Prevents double-execution under concurrency.
        - Prevents replay across multiple tasks or runs.
        """
        if action_type not in ACTIONS_REQUIRING_APPROVAL:
            return PolicyDecision(allowed=True, reason=f"Action '{action_type}' is standard read/compute")

        required_perm = ACTIONS_REQUIRING_APPROVAL[action_type]

        if not self.storage:
            return PolicyDecision(
                allowed=False,
                requires_approval=True,
                reason="Storage required to consume approval record",
                required_permission=required_perm,
            )

        current_payload_json = json.dumps(action_payload, sort_keys=True)
        current_hash = hashlib.sha256(current_payload_json.encode("utf-8")).hexdigest()

        # Check that workflow run is active before consuming approvals
        cursor = self.storage.conn.cursor()
        cursor.execute("SELECT state FROM workflow_runs WHERE run_id = ?", (run_id,))
        wf_row = cursor.fetchone()
        if wf_row and wf_row["state"] in ("CANCELLED", "TIMED_OUT", "FAILED", "SUCCEEDED"):
            return PolicyDecision(
                allowed=False,
                requires_approval=True,
                reason=f"Workflow run '{run_id}' is in terminal state '{wf_row['state']}'; cannot consume approvals for non-running workflow",
                required_permission=required_perm,
            )

        cursor.execute(
            """
            SELECT approval_id, action_payload_json, scope FROM workflow_approvals
            WHERE run_id = ? AND action_type = ? AND status = 'APPROVED'
            """,
            (run_id, action_type),
        )
        rows = cursor.fetchall()
        if not rows:
            return PolicyDecision(
                allowed=False,
                requires_approval=True,
                reason=f"No APPROVED record found for action '{action_type}' in run '{run_id}' (may be pending or already consumed)",
                required_permission=required_perm,
            )

        target_appr_id = None
        for row in rows:
            record_payload = json.loads(row["action_payload_json"])
            rec_hash = hashlib.sha256(json.dumps(record_payload, sort_keys=True).encode("utf-8")).hexdigest()
            if row["scope"] == scope and rec_hash == current_hash:
                target_appr_id = row["approval_id"]
                break

        if not target_appr_id:
            return PolicyDecision(
                allowed=False,
                requires_approval=True,
                reason=f"No matching approved record matches scope '{scope}' and payload hash '{current_hash}'",
                required_permission=required_perm,
            )

        # Atomic state transition from APPROVED to CONSUMED
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            """
            UPDATE workflow_approvals
            SET status = 'CONSUMED', decided_at = ?
            WHERE approval_id = ? AND status = 'APPROVED'
            """,
            (now_iso, target_appr_id),
        )
        self.storage.conn.commit()

        if cursor.rowcount == 1:
            logger.info("Approval '%s' atomically consumed for run '%s'", target_appr_id, run_id)
            return PolicyDecision(
                allowed=True,
                requires_approval=False,
                reason=f"Approval '{target_appr_id}' atomically consumed for action '{action_type}'",
                required_permission=required_perm,
            )
        else:
            logger.warning("Approval '%s' could not be consumed (already consumed concurrently)", target_appr_id)
            return PolicyDecision(
                allowed=False,
                requires_approval=False,
                reason=f"Concurrency violation: Approval '{target_appr_id}' was already consumed by another execution",
                required_permission=required_perm,
            )

    @staticmethod
    def sanitize_untrusted_input(text: str) -> str:
        """Neutralize prompt injection and adversarial commands in untrusted source text.

        Ensures text excerpts cannot trick planners or agents into claiming unauthorized permissions.
        """
        if not text:
            return ""
        # Remove null bytes and unprintable control characters
        cleaned = "".join(ch for ch in text if ch == "\n" or ch == "\t" or (ord(ch) >= 32 and ord(ch) != 127))
        return cleaned.strip()
