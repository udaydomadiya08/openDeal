"""Deterministic conflict detection and reconciliation engine for funding and M&A events."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, List, Optional, Tuple

from opendeal.deals.contracts import (
    DealConflict,
    DealVerificationStatus,
    FundingRound,
    TransactionEvent,
)

logger = logging.getLogger(__name__)


def are_dates_close(d1: Optional[date], d2: Optional[date], max_days: int = 14) -> bool:
    """Check if two event dates are within a tolerance window (default 14 days)."""
    if d1 is None or d2 is None:
        return True  # Date is missing in one observation; allow match if other keys align
    return abs((d1 - d2).days) <= max_days


def are_amounts_conflicting(
    amt1: Optional[float], amt2: Optional[float], tolerance_pct: float = 0.01
) -> bool:
    """Determine whether two disclosed amounts are meaningfully different."""
    if amt1 is None or amt2 is None:
        return False  # Missing amount is a data gap, not a conflict
    if amt1 == 0 and amt2 == 0:
        return False
    diff = abs(amt1 - amt2)
    max_val = max(abs(amt1), abs(amt2))
    return (diff / max_val) > tolerance_pct


class EventReconciler:
    """Reconciles repeat observations of corporate transactions and funding rounds.

    Principles:
    - Never merge distinct events solely due to similar company names or amounts.
    - Preserve competing source observations without silently overwriting.
    - Explicitly record field discrepancies as DealConflict instances.
    - Set verification status to CONTESTED when credible sources disagree.
    """

    def is_same_funding_round(self, round_a: FundingRound, round_b: FundingRound) -> bool:
        """Evaluate if two funding round observations refer to the same real-world event."""
        if round_a.company_id != round_b.company_id:
            return False

        # If round types differ, they must be compatible or same
        if round_a.round_type != round_b.round_type:
            # Series D and Series D+ may align, but Seed and Series B do not
            return False

        # Date alignment check
        if not are_dates_close(round_a.announced_date, round_b.announced_date, max_days=21):
            return False

        return True

    def reconcile_funding_rounds(
        self, existing: FundingRound, incoming: FundingRound, source_b_id: str = "INCOMING_FEED"
    ) -> Tuple[FundingRound, List[DealConflict]]:
        """Reconcile an incoming funding round observation with an existing record."""
        source_a_id = existing.source_reference_ids[0] if existing.source_reference_ids else "PRIMARY_STORE"
        new_conflicts: List[DealConflict] = []

        # 1. Check Disclosed Amount & Currency Conflict
        if existing.currency != incoming.currency:
            conflict = DealConflict(
                target_event_id=existing.round_id,
                event_category="FUNDING",
                field_name="currency",
                source_a_id=source_a_id,
                observed_value_a=existing.currency.value,
                source_b_id=source_b_id,
                observed_value_b=incoming.currency.value,
                resolution_status="UNRESOLVED",
                resolution_notes="Discrepancy in reported funding currency.",
            )
            new_conflicts.append(conflict)

        if are_amounts_conflicting(existing.disclosed_amount, incoming.disclosed_amount):
            conflict = DealConflict(
                target_event_id=existing.round_id,
                event_category="FUNDING",
                field_name="disclosed_amount",
                source_a_id=source_a_id,
                observed_value_a=f"{existing.disclosed_amount:,.2f} {existing.currency.value}",
                source_b_id=source_b_id,
                observed_value_b=f"{incoming.disclosed_amount:,.2f} {incoming.currency.value}",
                resolution_status="UNRESOLVED",
                resolution_notes=f"Conflicting round amounts reported across sources.",
            )
            new_conflicts.append(conflict)

        # 2. Check Announcement Date Conflict
        if existing.announced_date and incoming.announced_date:
            if existing.announced_date != incoming.announced_date:
                days_diff = abs((existing.announced_date - incoming.announced_date).days)
                if days_diff > 3:  # Beyond minor timezone reporting difference
                    conflict = DealConflict(
                        target_event_id=existing.round_id,
                        event_category="FUNDING",
                        field_name="announced_date",
                        source_a_id=source_a_id,
                        observed_value_a=existing.announced_date.isoformat(),
                        source_b_id=source_b_id,
                        observed_value_b=incoming.announced_date.isoformat(),
                        resolution_status="UNRESOLVED",
                        resolution_notes=f"Announcement date discrepancy of {days_diff} days.",
                    )
                    new_conflicts.append(conflict)

        # Merge Non-conflicting details
        if existing.disclosed_amount is None and incoming.disclosed_amount is not None:
            existing.disclosed_amount = incoming.disclosed_amount
            existing.currency = incoming.currency

        if existing.announced_date is None and incoming.announced_date is not None:
            existing.announced_date = incoming.announced_date

        if existing.closing_date is None and incoming.closing_date is not None:
            existing.closing_date = incoming.closing_date

        if existing.pre_money_valuation is None and incoming.pre_money_valuation is not None:
            existing.pre_money_valuation = incoming.pre_money_valuation

        if existing.post_money_valuation is None and incoming.post_money_valuation is not None:
            existing.post_money_valuation = incoming.post_money_valuation

        # Corroborate sources and evidence
        for sid in incoming.source_reference_ids:
            if sid not in existing.source_reference_ids:
                existing.source_reference_ids.append(sid)

        for eid in incoming.evidence_ids:
            if eid not in existing.evidence_ids:
                existing.evidence_ids.append(eid)

        for lid in incoming.lead_investor_ids:
            if lid not in existing.lead_investor_ids:
                existing.lead_investor_ids.append(lid)

        for pid in incoming.participant_ids:
            if pid not in existing.participant_ids:
                existing.participant_ids.append(pid)

        # Merge participants list
        existing_part_map = {p.investor_id: p for p in existing.participants}
        for inc_p in incoming.participants:
            if inc_p.investor_id not in existing_part_map:
                existing.participants.append(inc_p)

        if new_conflicts:
            existing.verification_status = DealVerificationStatus.CONTESTED
            existing.conflicts.extend(new_conflicts)
            notes_str = "; ".join(f"{c.field_name}: {c.observed_value_a} vs {c.observed_value_b}" for c in new_conflicts)
            existing.conflict_notes = (
                f"{existing.conflict_notes}; {notes_str}".strip("; ")
                if existing.conflict_notes
                else notes_str
            )
        elif not existing.conflicts:
            existing.verification_status = DealVerificationStatus.VERIFIED

        return existing, new_conflicts

    def is_same_transaction(self, tx_a: TransactionEvent, tx_b: TransactionEvent) -> bool:
        """Evaluate if two M&A transaction observations refer to the same deal."""
        if tx_a.target_id != tx_b.target_id:
            return False

        if tx_a.transaction_type != tx_b.transaction_type:
            return False

        # If both specify an acquirer, they must match
        if tx_a.acquirer_id and tx_b.acquirer_id:
            if tx_a.acquirer_id != tx_b.acquirer_id:
                return False

        # Date alignment check
        if not are_dates_close(tx_a.announced_date, tx_b.announced_date, max_days=30):
            return False

        return True

    def reconcile_transactions(
        self, existing: TransactionEvent, incoming: TransactionEvent, source_b_id: str = "INCOMING_FEED"
    ) -> Tuple[TransactionEvent, List[DealConflict]]:
        """Reconcile an incoming transaction observation with an existing record."""
        source_a_id = existing.source_reference_ids[0] if existing.source_reference_ids else "PRIMARY_STORE"
        new_conflicts: List[DealConflict] = []

        # 1. Check Disclosed Deal Value Conflict
        if existing.currency != incoming.currency:
            conflict = DealConflict(
                target_event_id=existing.transaction_id,
                event_category="TRANSACTION",
                field_name="currency",
                source_a_id=source_a_id,
                observed_value_a=existing.currency.value,
                source_b_id=source_b_id,
                observed_value_b=incoming.currency.value,
                resolution_status="UNRESOLVED",
                resolution_notes="Discrepancy in reported deal currency.",
            )
            new_conflicts.append(conflict)

        if are_amounts_conflicting(existing.disclosed_value, incoming.disclosed_value):
            conflict = DealConflict(
                target_event_id=existing.transaction_id,
                event_category="TRANSACTION",
                field_name="disclosed_value",
                source_a_id=source_a_id,
                observed_value_a=f"{existing.disclosed_value:,.2f} {existing.currency.value}",
                source_b_id=source_b_id,
                observed_value_b=f"{incoming.disclosed_value:,.2f} {incoming.currency.value}",
                resolution_status="UNRESOLVED",
                resolution_notes="Discrepancy in disclosed transaction valuation.",
            )
            new_conflicts.append(conflict)

        # 2. Check Status Discrepancy (e.g. COMPLETED vs TERMINATED)
        if existing.status != incoming.status:
            # Distinguish harmless progression (ANNOUNCED -> COMPLETED) from conflict (COMPLETED vs TERMINATED)
            terminal_states = {"COMPLETED", "TERMINATED", "WITHDRAWN"}
            if existing.status.value in terminal_states and incoming.status.value in terminal_states:
                if existing.status != incoming.status:
                    conflict = DealConflict(
                        target_event_id=existing.transaction_id,
                        event_category="TRANSACTION",
                        field_name="status",
                        source_a_id=source_a_id,
                        observed_value_a=existing.status.value,
                        source_b_id=source_b_id,
                        observed_value_b=incoming.status.value,
                        resolution_status="UNRESOLVED",
                        resolution_notes="Conflicting terminal transaction status reported across sources.",
                    )
                    new_conflicts.append(conflict)
            else:
                # Progression: e.g. update ANNOUNCED or PENDING to COMPLETED if incoming reports verified completion
                if incoming.status.value == "COMPLETED":
                    existing.status = incoming.status

        # Merge Non-conflicting details
        if existing.disclosed_value is None and incoming.disclosed_value is not None:
            existing.disclosed_value = incoming.disclosed_value
            existing.currency = incoming.currency

        if existing.acquirer_id is None and incoming.acquirer_id is not None:
            existing.acquirer_id = incoming.acquirer_id

        if existing.seller_id is None and incoming.seller_id is not None:
            existing.seller_id = incoming.seller_id

        if existing.announced_date is None and incoming.announced_date is not None:
            existing.announced_date = incoming.announced_date

        if existing.actual_completion_date is None and incoming.actual_completion_date is not None:
            existing.actual_completion_date = incoming.actual_completion_date

        # Corroborate sources and evidence
        for sid in incoming.source_reference_ids:
            if sid not in existing.source_reference_ids:
                existing.source_reference_ids.append(sid)

        for eid in incoming.evidence_ids:
            if eid not in existing.evidence_ids:
                existing.evidence_ids.append(eid)

        if new_conflicts:
            existing.verification_status = DealVerificationStatus.CONTESTED
            existing.conflicts.extend(new_conflicts)
            notes_str = "; ".join(f"{c.field_name}: {c.observed_value_a} vs {c.observed_value_b}" for c in new_conflicts)
            existing.conflict_notes = (
                f"{existing.conflict_notes}; {notes_str}".strip("; ")
                if existing.conflict_notes
                else notes_str
            )
        elif not existing.conflicts:
            existing.verification_status = DealVerificationStatus.VERIFIED

        return existing, new_conflicts
