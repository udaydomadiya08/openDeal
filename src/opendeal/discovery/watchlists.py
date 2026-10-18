"""Target list and watchlist management with audit history and deterministic export."""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from opendeal.discovery.contracts import (
    TargetList,
    TargetListHistory,
    TargetListItem,
    TargetListStatus,
)
from opendeal.storage.base import StorageInterface

logger = logging.getLogger(__name__)


class DuplicateMemberError(Exception):
    """Raised when attempting to add a company that is already a member of the target list."""
    pass


class TargetListNotFoundError(Exception):
    """Raised when a requested target list does not exist."""
    pass


class TargetListManager:
    """Manages saved target lists, watchlists, member status transitions, and exports."""

    def __init__(self, storage: StorageInterface) -> None:
        self.storage = storage

    def create_list(self, name: str, description: Optional[str] = None) -> TargetList:
        """Create a new user-controlled target list."""
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Target list name cannot be empty.")

        list_id = f"list_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        target_list = TargetList(
            list_id=list_id,
            name=clean_name,
            description=description.strip() if description else None,
            created_at=now,
            updated_at=now,
            member_count=0,
        )

        if hasattr(self.storage, "save_target_list"):
            self.storage.save_target_list(target_list)

        return target_list

    def get_list(self, list_id: str) -> Optional[TargetList]:
        """Retrieve a target list by its ID."""
        if hasattr(self.storage, "get_target_list"):
            return self.storage.get_target_list(list_id)
        return None

    def list_lists(self) -> List[TargetList]:
        """List all saved target lists."""
        if hasattr(self.storage, "list_target_lists"):
            return self.storage.list_target_lists()
        return []

    def delete_list(self, list_id: str) -> bool:
        """Delete a target list and its associated members and history."""
        if hasattr(self.storage, "delete_target_list"):
            return self.storage.delete_target_list(list_id)
        return False

    def add_member(
        self,
        list_id: str,
        entity_id: str,
        status: TargetListStatus = TargetListStatus.NEW,
        notes: Optional[str] = None,
        source_run_id: Optional[str] = None,
    ) -> TargetListItem:
        """Add a company entity to a target list with status and optional note.

        Guarantees:
        - Prevents duplicate membership.
        - Records initial history audit record.
        """
        target_list = self.get_list(list_id)
        if not target_list:
            raise TargetListNotFoundError(f"Target list '{list_id}' not found.")

        entity = self.storage.get_entity(entity_id)
        if not entity:
            raise ValueError(f"Entity '{entity_id}' not found in storage.")

        if hasattr(self.storage, "is_target_list_member"):
            if self.storage.is_target_list_member(list_id, entity_id):
                raise DuplicateMemberError(
                    f"Entity '{entity.legal_name}' ({entity_id}) is already a member of list '{list_id}'."
                )

        now = datetime.now(timezone.utc)
        item = TargetListItem(
            list_id=list_id,
            entity_id=entity_id,
            entity_name=entity.legal_name,
            status=status,
            notes=notes,
            source_run_id=source_run_id,
            added_at=now,
            updated_at=now,
        )

        history_id = f"hist_{uuid.uuid4().hex[:12]}"
        history = TargetListHistory(
            history_id=history_id,
            list_id=list_id,
            entity_id=entity_id,
            old_status=None,
            new_status=status,
            note=notes or "Initial addition to target list.",
            changed_at=now,
        )

        if hasattr(self.storage, "save_target_list_member"):
            self.storage.save_target_list_member(item, history)

        return item

    def remove_member(self, list_id: str, entity_id: str) -> bool:
        """Remove a company from a target list."""
        if hasattr(self.storage, "remove_target_list_member"):
            return self.storage.remove_target_list_member(list_id, entity_id)
        return False

    def update_member_status(
        self,
        list_id: str,
        entity_id: str,
        new_status: TargetListStatus,
        note: Optional[str] = None,
    ) -> TargetListItem:
        """Update workflow status and record audit history."""
        target_list = self.get_list(list_id)
        if not target_list:
            raise TargetListNotFoundError(f"Target list '{list_id}' not found.")

        members = self.get_members(list_id)
        member = next((m for m in members if m.entity_id == entity_id), None)
        if not member:
            raise ValueError(f"Entity '{entity_id}' is not a member of list '{list_id}'.")

        old_status = member.status
        now = datetime.now(timezone.utc)
        member.status = new_status
        if note:
            member.notes = note
        member.updated_at = now

        history_id = f"hist_{uuid.uuid4().hex[:12]}"
        history = TargetListHistory(
            history_id=history_id,
            list_id=list_id,
            entity_id=entity_id,
            old_status=old_status,
            new_status=new_status,
            note=note or f"Status changed from {old_status.value} to {new_status.value}.",
            changed_at=now,
        )

        if hasattr(self.storage, "update_target_list_member"):
            self.storage.update_target_list_member(member, history)

        return member

    def get_members(self, list_id: str) -> List[TargetListItem]:
        """Retrieve all members of a target list."""
        if hasattr(self.storage, "get_target_list_members"):
            return self.storage.get_target_list_members(list_id)
        return []

    def get_member_history(self, list_id: str, entity_id: str) -> List[TargetListHistory]:
        """Retrieve audit history of status changes for an entity in a list."""
        if hasattr(self.storage, "get_target_list_history"):
            return self.storage.get_target_list_history(list_id, entity_id)
        return []

    def export_list(self, list_id: str, format: str = "json") -> str:
        """Export a target list with all members and provenance in deterministic JSON or CSV format."""
        target_list = self.get_list(list_id)
        if not target_list:
            raise TargetListNotFoundError(f"Target list '{list_id}' not found.")

        members = self.get_members(list_id)
        # Sort members deterministically by (status, entity_name)
        members.sort(key=lambda m: (m.status.value, m.entity_name))

        if format.lower() == "csv":
            output = io.StringIO()
            writer = csv.writer(output, lineterminator="\n")
            writer.writerow([
                "list_id",
                "list_name",
                "entity_id",
                "entity_name",
                "status",
                "notes",
                "source_run_id",
                "added_at",
                "updated_at",
            ])
            for m in members:
                writer.writerow([
                    m.list_id,
                    target_list.name,
                    m.entity_id,
                    m.entity_name,
                    m.status.value,
                    m.notes or "",
                    m.source_run_id or "",
                    m.added_at.isoformat(),
                    m.updated_at.isoformat(),
                ])
            return output.getvalue()

        # Default: JSON export
        payload: Dict[str, Any] = {
            "list": target_list.model_dump(mode="json"),
            "member_count": len(members),
            "members": [m.model_dump(mode="json") for m in members],
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
        return json.dumps(payload, indent=2, default=str)
