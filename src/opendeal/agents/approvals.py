"""Human-in-the-loop approval management for bounded agent operations."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from opendeal.agents.contracts import ApprovalRequest, ApprovalStatus, AuditEvent
from opendeal.storage.base import StorageInterface

logger = logging.getLogger(__name__)


class ApprovalManager:
    """Manages creation, review, and resolution of privileged approval requests."""

    def __init__(self, storage: StorageInterface) -> None:
        self.storage = storage

    def request_approval(
        self,
        run_id: str,
        action_type: str,
        action_payload: Dict[str, Any],
        scope: str = "GLOBAL",
    ) -> str:
        """Create and persist a pending approval request."""
        import uuid
        appr_id = f"appr_{uuid.uuid4().hex[:12]}"
        req = ApprovalRequest(
            approval_id=appr_id,
            run_id=run_id,
            action_type=action_type,
            action_payload=action_payload,
            scope=scope,
            status=ApprovalStatus.PENDING,
            requested_at=datetime.now(timezone.utc),
        )
        self.storage.save_approval_request(req)
        return appr_id

    def list_pending(self, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all pending approval requests."""
        return self.storage.list_pending_approvals(run_id=run_id)

    def list_pending_approvals(self, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Alias for list_pending."""
        return self.list_pending(run_id=run_id)

    def get_approval(self, approval_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve an approval request by its ID."""
        return self.storage.get_approval_request(approval_id)

    def decide(
        self,
        approval_id: str,
        approved: bool,
        decided_by: str = "operator",
        rationale: str = "",
    ) -> Dict[str, Any]:
        """Submit an explicit human decision on a pending approval request.

        Records decision timestamp, operator identity, and rationale.
        """
        appr_dict = self.storage.get_approval_request(approval_id)
        if not appr_dict:
            raise ValueError(f"Approval request '{approval_id}' not found.")

        current_status = appr_dict.get("status")
        if current_status != ApprovalStatus.PENDING.value:
            raise ValueError(
                f"Cannot decide on approval '{approval_id}' with status '{current_status}' (must be PENDING)"
            )

        new_status = ApprovalStatus.APPROVED.value if approved else ApprovalStatus.REJECTED.value
        appr_dict["status"] = new_status
        appr_dict["decided_at"] = datetime.now(timezone.utc).isoformat()
        appr_dict["decided_by"] = decided_by
        appr_dict["decision_rationale"] = rationale

        self.storage.save_approval_request(appr_dict)

        # Record audit event
        run_id = appr_dict.get("run_id", "")
        self.storage.record_workflow_audit_event(
            AuditEvent(
                event_id=f"audit_appr_{approval_id[-6:]}",
                run_id=run_id,
                timestamp=datetime.now(timezone.utc),
                event_type=f"APPROVAL_{new_status}",
                initiator=decided_by,
                details={
                    "approval_id": approval_id,
                    "action_type": appr_dict.get("action_type"),
                    "rationale": rationale,
                },
            )
        )

        logger.info(
            "Approval '%s' for run '%s' decided as %s by '%s'",
            approval_id,
            run_id,
            new_status,
            decided_by,
        )
        return appr_dict

    def decide_approval(
        self,
        approval_id: str,
        status: ApprovalStatus,
        decided_by: str = "operator",
        rationale: str = "",
    ) -> ApprovalRequest:
        """Alias for decide returning an ApprovalRequest model."""
        approved = (status == ApprovalStatus.APPROVED)
        res = self.decide(approval_id, approved=approved, decided_by=decided_by, rationale=rationale)
        return ApprovalRequest.model_validate(res)
