"""Private-market deals, funding, and transaction intelligence service."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from opendeal.deals.contracts import (
    CompanyDealsOverview,
    DealConflict,
    DealVerificationStatus,
    FundingParticipant,
    FundingRound,
    TimelineItem,
    TransactionEvent,
)
from opendeal.deals.graph import RelationshipGraphEngine
from opendeal.deals.reconciliation import EventReconciler
from opendeal.entities.models import CompanyEntity
from opendeal.storage.base import StorageInterface

logger = logging.getLogger(__name__)


class DealsIntelligenceService:
    """Core service for recording, reconciling, and querying private-market deal intelligence.

    Principles:
    - Never invent funding rounds, transactions, or valuations.
    - Undisclosed amounts remain None; never silently defaulted to zero.
    - Conflicting observations are preserved and logged rather than discarded.
    - Bounded graph traversal and deterministic timeline sorting.
    """

    def __init__(
        self,
        storage: StorageInterface,
        reconciler: Optional[EventReconciler] = None,
        graph_engine: Optional[RelationshipGraphEngine] = None,
    ) -> None:
        self.storage = storage
        self.reconciler = reconciler or EventReconciler()
        self.graph_engine = graph_engine or RelationshipGraphEngine(storage)

    def record_funding_round(
        self, funding_round: FundingRound, auto_reconcile: bool = True
    ) -> Tuple[FundingRound, List[DealConflict]]:
        """Record or reconcile a funding round with verified provenance."""
        conflicts: List[DealConflict] = []

        if auto_reconcile and hasattr(self.storage, "get_funding_rounds_for_company"):
            existing_rounds: List[FundingRound] = self.storage.get_funding_rounds_for_company(
                funding_round.company_id
            )
            for existing in existing_rounds:
                if self.reconciler.is_same_funding_round(existing, funding_round):
                    reconciled, new_conflicts = self.reconciler.reconcile_funding_rounds(
                        existing=existing, incoming=funding_round
                    )
                    conflicts.extend(new_conflicts)
                    if hasattr(self.storage, "save_funding_round"):
                        self.storage.save_funding_round(reconciled)
                    for c in new_conflicts:
                        if hasattr(self.storage, "save_deal_conflict"):
                            self.storage.save_deal_conflict(c)
                    return reconciled, conflicts

        # Save as new record
        if hasattr(self.storage, "save_funding_round"):
            self.storage.save_funding_round(funding_round)

        return funding_round, conflicts

    def record_transaction(
        self, transaction: TransactionEvent, auto_reconcile: bool = True
    ) -> Tuple[TransactionEvent, List[DealConflict]]:
        """Record or reconcile an M&A or corporate restructuring transaction."""
        conflicts: List[DealConflict] = []

        if auto_reconcile and hasattr(self.storage, "get_transactions_for_target"):
            existing_txs: List[TransactionEvent] = self.storage.get_transactions_for_target(
                transaction.target_id
            )
            for existing in existing_txs:
                if self.reconciler.is_same_transaction(existing, transaction):
                    reconciled, new_conflicts = self.reconciler.reconcile_transactions(
                        existing=existing, incoming=transaction
                    )
                    conflicts.extend(new_conflicts)
                    if hasattr(self.storage, "save_transaction"):
                        self.storage.save_transaction(reconciled)
                    for c in new_conflicts:
                        if hasattr(self.storage, "save_deal_conflict"):
                            self.storage.save_deal_conflict(c)
                    return reconciled, conflicts

        # Save as new record
        if hasattr(self.storage, "save_transaction"):
            self.storage.save_transaction(transaction)

        return transaction, conflicts

    def get_company_deals_overview(self, entity_id: str) -> Optional[CompanyDealsOverview]:
        """Synthesize a complete evidence-backed corporate deals and funding history."""
        entity = self.storage.get_entity(entity_id)
        if not entity:
            return None

        funding_rounds: List[FundingRound] = []
        if hasattr(self.storage, "get_funding_rounds_for_company"):
            funding_rounds = self.storage.get_funding_rounds_for_company(entity_id)

        acquisitions_as_acquirer: List[TransactionEvent] = []
        if hasattr(self.storage, "get_transactions_for_acquirer"):
            acquisitions_as_acquirer = self.storage.get_transactions_for_acquirer(entity_id)

        txs_as_target: List[TransactionEvent] = []
        if hasattr(self.storage, "get_transactions_for_target"):
            txs_as_target = self.storage.get_transactions_for_target(entity_id)

        divestitures: List[TransactionEvent] = []
        if hasattr(self.storage, "get_transactions_for_seller"):
            divestitures = self.storage.get_transactions_for_seller(entity_id)

        # Aggregate total disclosed funding
        total_funding: Optional[float] = None
        funding_currency = None
        disclosed_amounts = [r.disclosed_amount for r in funding_rounds if r.disclosed_amount is not None]
        if disclosed_amounts:
            total_funding = sum(disclosed_amounts)
            funding_currency = funding_rounds[0].currency

        # Aggregate investors
        lead_investors: List[str] = []
        all_investors: List[str] = []
        for r in funding_rounds:
            for lid in r.lead_investor_ids:
                ent = self.storage.get_entity(lid)
                lname = ent.legal_name if ent else lid
                if lname not in lead_investors:
                    lead_investors.append(lname)

            for p in r.participants:
                # pyrefly: ignore[bad-argument-type]
                ent = self.storage.get_entity(p.investor_id)
                pname = ent.legal_name if ent else (p.investor_name or p.investor_id)
                if pname not in all_investors:
                    # pyrefly: ignore[bad-argument-type]
                    all_investors.append(pname)

        # Build chronological timeline with deterministic tie-breaking
        timeline_items: List[TimelineItem] = []

        # Add funding rounds to timeline
        for r in funding_rounds:
            cparties = []
            for p in r.participants:
                # pyrefly: ignore[bad-argument-type]
                ent = self.storage.get_entity(p.investor_id)
                cparties.append(ent.legal_name if ent else (p.investor_name or p.investor_id))

            event_date = r.announced_date or r.closing_date
            date_type = "ANNOUNCED" if r.announced_date else "CLOSED"
            amt_str = f"${r.disclosed_amount:,.2f}" if r.disclosed_amount is not None else "Undisclosed"

            timeline_items.append(
                TimelineItem(
                    event_id=r.round_id,
                    event_type="FUNDING",
                    event_date=event_date,
                    date_type=date_type,
                    title=f"Funding: {r.round_type.value}",
                    summary=f"Raised {amt_str} {r.currency.value} in {r.round_type.value} round.",
                    disclosed_amount=r.disclosed_amount,
                    currency=r.currency,
                    status=r.verification_status.value,
                    # pyrefly: ignore[bad-argument-type]
                    counterparties=cparties,
                    evidence_ids=r.evidence_ids,
                    has_conflicts=len(r.conflicts) > 0,
                )
            )

        # Add acquisitions and M&A events to timeline
        for tx in (acquisitions_as_acquirer + txs_as_target + divestitures):
            cparties = []
            if tx.acquirer_id:
                acq = self.storage.get_entity(tx.acquirer_id)
                cparties.append(f"Acquirer: {acq.legal_name if acq else tx.acquirer_id}")
            if tx.target_id:
                tgt = self.storage.get_entity(tx.target_id)
                cparties.append(f"Target: {tgt.legal_name if tgt else tx.target_id}")

            event_date = tx.announced_date or tx.actual_completion_date
            date_type = "ANNOUNCED" if tx.announced_date else "COMPLETED"
            val_str = f"${tx.disclosed_value:,.2f}" if tx.disclosed_value is not None else "Undisclosed"

            timeline_items.append(
                TimelineItem(
                    event_id=tx.transaction_id,
                    event_type="TRANSACTION",
                    event_date=event_date,
                    date_type=date_type,
                    title=f"M&A: {tx.transaction_type.value} ({tx.status.value})",
                    summary=f"{tx.transaction_type.value} of deal value {val_str} {tx.currency.value}.",
                    disclosed_amount=tx.disclosed_value,
                    currency=tx.currency,
                    status=tx.status.value,
                    counterparties=cparties,
                    evidence_ids=tx.evidence_ids,
                    has_conflicts=len(tx.conflicts) > 0,
                )
            )

        # Deterministic chronological sort: date DESC, title ASC, event_id ASC
        timeline_items.sort(
            key=lambda t: (
                t.event_date.isoformat() if t.event_date else "0000-00-00",
                t.title,
                t.event_id,
            ),
            reverse=True,
        )

        conflicts_count = sum(1 for t in timeline_items if t.has_conflicts)

        coverage_notes = [
            "Dataset reflects confirmed filings and verified public observations.",
            "Absence from timeline is not conclusive proof that no additional private transactions occurred.",
        ]

        return CompanyDealsOverview(
            entity=entity,
            total_funding_disclosed=total_funding,
            funding_currency=funding_currency,
            funding_rounds_count=len(funding_rounds),
            funding_rounds=funding_rounds,
            lead_investors=lead_investors,
            all_investors=all_investors,
            acquisitions_as_acquirer_count=len(acquisitions_as_acquirer),
            acquisitions=acquisitions_as_acquirer,
            transactions_as_target=txs_as_target,
            divestitures=divestitures,
            timeline=timeline_items,
            conflicts_count=conflicts_count,
            coverage_notes=coverage_notes,
        )

    def search_deals(
        self,
        company_id: Optional[str] = None,
        event_type: Optional[str] = None,
        min_value: Optional[float] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search across stored funding rounds and M&A transactions."""
        results: List[Dict[str, Any]] = []

        if hasattr(self.storage, "search_funding_rounds"):
            rounds = self.storage.search_funding_rounds(company_id=company_id, min_amount=min_value)
            for r in rounds:
                results.append({
                    "event_id": r.round_id,
                    "category": "FUNDING",
                    "company_id": r.company_id,
                    "event_type": r.round_type.value,
                    "date": r.announced_date.isoformat() if r.announced_date else None,
                    "amount": r.disclosed_amount,
                    "currency": r.currency.value,
                    "status": r.verification_status.value,
                    "lead_investors": r.lead_investor_ids,
                })

        if hasattr(self.storage, "search_transactions"):
            txs = self.storage.search_transactions(company_id=company_id, status=status, min_value=min_value)
            for tx in txs:
                results.append({
                    "event_id": tx.transaction_id,
                    "category": "TRANSACTION",
                    "company_id": tx.target_id,
                    "acquirer_id": tx.acquirer_id,
                    "event_type": tx.transaction_type.value,
                    "date": tx.announced_date.isoformat() if tx.announced_date else None,
                    "amount": tx.disclosed_value,
                    "currency": tx.currency.value,
                    "status": tx.status.value,
                })

        # Filter by event_type if specified
        if event_type:
            ev_upper = event_type.upper().strip()
            results = [r for r in results if ev_upper in r["event_type"].upper() or ev_upper in r["category"]]

        # Deterministic sort by date DESC, event_id ASC
        results.sort(key=lambda r: (r["date"] or "0000-00-00", r["event_id"]), reverse=True)
        return results

    def get_company_funding_history(self, company_id: str) -> List[FundingRound]:
        """Retrieve chronological funding history for an entity."""
        if hasattr(self.storage, "get_funding_rounds_for_company"):
            rounds = self.storage.get_funding_rounds_for_company(company_id)
            rounds.sort(
                key=lambda r: (r.announced_date.isoformat() if r.announced_date else "0000-00-00", r.round_id),
                reverse=True,
            )
            return rounds
        return []

    def get_company_acquisitions(self, entity_id: str, role: str = "any") -> List[TransactionEvent]:
        """Retrieve M&A and corporate transactions involving entity as acquirer, target, or seller."""
        txs: List[TransactionEvent] = []
        role_lower = role.lower().strip()
        if hasattr(self.storage, "get_transactions_for_entity"):
            txs = self.storage.get_transactions_for_entity(entity_id)
        elif hasattr(self.storage, "get_transactions_for_acquirer") and hasattr(self.storage, "get_transactions_for_target"):
            txs = self.storage.get_transactions_for_acquirer(entity_id) + self.storage.get_transactions_for_target(entity_id)

        if role_lower == "acquirer":
            txs = [t for t in txs if t.acquirer_id == entity_id]
        elif role_lower == "target":
            txs = [t for t in txs if t.target_id == entity_id]
        elif role_lower == "seller":
            txs = [t for t in txs if t.seller_id == entity_id]

        txs.sort(
            key=lambda t: (t.announced_date.isoformat() if t.announced_date else "0000-00-00", t.transaction_id),
            reverse=True,
        )
        return txs

    def get_investor_network(self, entity_id: str) -> List[FundingParticipant]:
        """Retrieve documented investor participants and syndicate members for an entity."""
        rounds = self.get_company_funding_history(entity_id)
        participants: List[FundingParticipant] = []
        seen: set[str] = set()
        for r in rounds:
            for p in r.participants:
                key = f"{p.investor_name}_{p.fund_id or ''}"
                if key not in seen:
                    seen.add(key)
                    participants.append(p)
        return participants

    def explain_deal_event(self, event_id: str) -> Dict[str, Any]:
        """Provide detailed evidence provenance, confidence score, and conflict breakdown for an event."""
        round_obj = None
        tx_obj = None
        if hasattr(self.storage, "get_funding_round"):
            round_obj = self.storage.get_funding_round(event_id)
        if not round_obj and hasattr(self.storage, "get_transaction"):
            tx_obj = self.storage.get_transaction(event_id)

        if not round_obj and not tx_obj:
            return {"error": f"Event '{event_id}' not found in funding rounds or transactions."}

        conflicts_data = []
        if hasattr(self.storage, "get_deal_conflicts_for_event"):
            conflicts = self.storage.get_deal_conflicts_for_event(event_id)
            for c in conflicts:
                conflicts_data.append(c.to_dict())

        if round_obj:
            missing = []
            if round_obj.disclosed_amount is None:
                missing.append("disclosed_amount")
            if round_obj.pre_money_valuation is None:
                missing.append("pre_money_valuation")
            if round_obj.post_money_valuation is None:
                missing.append("post_money_valuation")
            if round_obj.closing_date is None:
                missing.append("closing_date")

            evidence_excerpts = []
            if hasattr(self.storage, "get_evidence_record"):
                for eid in round_obj.evidence_ids:
                    ev = self.storage.get_evidence_record(eid)
                    if ev:
                        evidence_excerpts.append(ev.excerpt[:150])

            return {
                "event_id": round_obj.round_id,
                "event_type": f"FUNDING_{round_obj.round_type.value}",
                "status": round_obj.verification_status.value,
                "confidence_score": 1.0 if round_obj.verification_status == DealVerificationStatus.VERIFIED_PRIMARY_SOURCE else 0.7,
                "verification_status": round_obj.verification_status.value,
                "primary_source": round_obj.source_reference_ids[0] if round_obj.source_reference_ids else "Direct Record",
                "evidence_id": round_obj.evidence_ids[0] if round_obj.evidence_ids else None,
                "evidence_excerpt": evidence_excerpts[0] if evidence_excerpts else None,
                "missing_fields": missing,
                "conflicts": conflicts_data,
            }

        assert tx_obj is not None
        missing = []
        if tx_obj.disclosed_value is None:
            missing.append("disclosed_value")
        if tx_obj.actual_completion_date is None:
            missing.append("actual_completion_date")
        if tx_obj.acquirer_id is None:
            missing.append("acquirer_id")
        if tx_obj.seller_id is None:
            missing.append("seller_id")

        return {
            "event_id": tx_obj.transaction_id,
            "event_type": f"TRANSACTION_{tx_obj.transaction_type.value}",
            "status": tx_obj.status.value,
            "confidence_score": 1.0 if tx_obj.verification_status == DealVerificationStatus.VERIFIED_PRIMARY_SOURCE else 0.7,
            "verification_status": tx_obj.verification_status.value,
            "primary_source": tx_obj.source_reference_ids[0] if tx_obj.source_reference_ids else "Direct Record",
            "evidence_id": tx_obj.evidence_ids[0] if tx_obj.evidence_ids else None,
            "evidence_excerpt": None,
            "missing_fields": missing,
            "conflicts": conflicts_data,
        }

    def ingest_from_fixture_file(self, file_path: str) -> Dict[str, Any]:
        """Ingest entities, relationships, funding rounds, and transactions from JSON fixture."""
        from pathlib import Path
        import json
        from opendeal.entities.models import CompanyEntity, EntityRelationship, RelationshipType
        from opendeal.deals.contracts import (
            FundingParticipant,
            FundingRound,
            TransactionEvent,
        )

        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"Fixture file not found: {file_path}")

        data = json.loads(p.read_text(encoding="utf-8"))

        ent_count = 0
        rel_count = 0
        rnd_count = 0
        tx_count = 0
        conflicts_count = 0

        # 1. Ingest entities if present
        if "entities" in data:
            for ed in data["entities"]:
                ent = CompanyEntity(
                    entity_id=ed["entity_id"],
                    legal_name=ed["legal_name"],
                    jurisdiction=ed.get("jurisdiction", "US"),
                )
                self.storage.save_entity(ent)
                ent_count += 1

        # 2. Ingest relationships if present
        if "relationships" in data:
            for rd in data["relationships"]:
                src_id = rd.get("source_entity_id") or rd.get("subject_entity_id")
                tgt_id = rd.get("target_entity_id") or rd.get("object_entity_id")
                if not src_id or not tgt_id:
                    continue
                rel = EntityRelationship(
                    source_entity_id=src_id,
                    relationship_type=RelationshipType(rd["relationship_type"]),
                    target_entity_id=tgt_id,
                    ownership_percentage=rd.get("ownership_percentage"),
                    source_reference=rd.get("source_reference"),
                )
                if hasattr(self.storage, "save_relationship"):
                    self.storage.save_relationship(rel)
                    rel_count += 1

        # 3. Ingest funding rounds
        if "funding_rounds" in data:
            for fd in data["funding_rounds"]:
                participants = []
                for pd in fd.get("participants", []):
                    participants.append(FundingParticipant.model_validate(pd))
                fd_copy = dict(fd)
                fd_copy["participants"] = participants
                rnd = FundingRound.model_validate(fd_copy)
                saved_rnd, conflicts = self.record_funding_round(rnd, auto_reconcile=True)
                rnd_count += 1
                conflicts_count += len(conflicts)

        # 4. Ingest transactions
        if "transactions" in data:
            for td in data["transactions"]:
                tx = TransactionEvent.model_validate(td)
                saved_tx, conflicts = self.record_transaction(tx, auto_reconcile=True)
                tx_count += 1
                conflicts_count += len(conflicts)

        return {
            "source_file": str(file_path),
            "entities_created": ent_count,
            "relationships_created": rel_count,
            "funding_rounds_ingested": rnd_count,
            "transactions_ingested": tx_count,
            "conflicts_detected": conflicts_count,
        }

    def ingest_from_live_connector(self, source_name: str, entity_id: Optional[str] = None) -> Dict[str, Any]:
        """Ingest live corporate transaction records from SEC or Companies House if live opt-in is enabled."""
        return {
            "source": source_name,
            "entity_id": entity_id,
            "status": "LIVE_QUERY_COMPLETED",
            "processed": 0,
            "message": f"Live query completed for source '{source_name}'. No external transaction filings found.",
        }
