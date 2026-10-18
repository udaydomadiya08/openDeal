"""Audit trail inspection and timeline formatting for workflow executions."""

import logging
from typing import Any, Dict, List, Optional
from opendeal.storage.base import StorageInterface

logger = logging.getLogger(__name__)


class AuditInspector:
    """Inspects and formats immutable workflow audit logs for forensic review."""

    def __init__(self, storage: StorageInterface) -> None:
        self.storage = storage

    def get_run_timeline(self, run_id: str) -> List[Dict[str, Any]]:
        """Retrieve chronological audit trail formatted with sanitized details."""
        events = self.storage.get_workflow_audit_trail(run_id)
        formatted = []
        for ev in events:
            # Mask any potentially sensitive tokens or parameters
            details = dict(ev.get("details", {}))
            sanitized_details = self._sanitize_dict(details)

            formatted.append({
                "event_id": ev.get("event_id"),
                "timestamp": ev.get("timestamp"),
                "event_type": ev.get("event_type"),
                "initiator": ev.get("initiator"),
                "agent_or_task": ev.get("agent_or_task"),
                "details": sanitized_details,
                "is_mutation": bool(ev.get("is_mutation", 0)),
            })
        return formatted

    get_timeline = get_run_timeline

    def _sanitize_value(self, val: Any) -> Any:
        import re
        if isinstance(val, dict):
            return self._sanitize_dict(val)
        elif isinstance(val, (list, tuple, set)):
            return [self._sanitize_value(item) for item in val]
        elif isinstance(val, str):
            if "BEGIN" in val and "PRIVATE KEY" in val:
                return "[REDACTED_PRIVATE_KEY]"
            # Bearer tokens
            val = re.sub(r"(Bearer\s+)[A-Za-z0-9_\-\.]{8,}", r"\1[REDACTED_TOKEN]", val, flags=re.IGNORECASE)
            # URL credentials e.g. https://user:pass@host
            val = re.sub(r"://([^:]+):([^@]+)@", r"://\1:[REDACTED]@", val)
            return val
        return val

    def _sanitize_dict(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """Redact sensitive keys if present in audit event payloads."""
        sanitized = {}
        sensitive_keys = {"api_key", "password", "secret", "token", "credential", "auth", "private_key", "bearer"}
        for k, v in d.items():
            if any(s in k.lower() for s in sensitive_keys):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = self._sanitize_value(v)
        return sanitized
