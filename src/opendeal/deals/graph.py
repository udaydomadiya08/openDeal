"""Lightweight relational graph traversal for corporate relationships and transaction networks."""

from __future__ import annotations

import logging
from collections import deque
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

from opendeal.deals.contracts import (
    FundingParticipant,
    FundingRound,
    TransactionEvent,
)
from opendeal.entities.models import CompanyEntity
from opendeal.storage.base import StorageInterface

logger = logging.getLogger(__name__)


class GraphNode(BaseModel):
    """A node in the corporate relationship network."""
    entity_id: str
    legal_name: str
    entity_type: str
    jurisdiction: str


class GraphEdge(BaseModel):
    """An evidence-grounded edge connecting two corporate entities."""
    source_id: str
    target_id: str
    relationship_type: str
    event_id: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    effective_date: Optional[str] = None


class GraphPath(BaseModel):
    """An evidence-backed path connecting two entities in the corporate graph."""
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    depth: int
    summary: str


class RelationshipGraphEngine:
    """Traverses corporate ownership, funding, and M&A networks on relational storage.

    Guarantees:
    - Bounded traversal depth (`max_depth=3` default) to prevent infinite recursion.
    - Explicit cycle detection avoiding infinite loops.
    - Preserves evidence records backing every discovered edge.
    - Strictly avoids conflating investment participation with beneficial ownership.
    """

    def __init__(self, storage: StorageInterface) -> None:
        self.storage = storage

    def get_investors_for_company(self, company_id: str) -> List[Dict[str, Any]]:
        """Retrieve all documented investors that have invested in a target company."""
        rounds: List[FundingRound] = []
        if hasattr(self.storage, "get_funding_rounds_for_company"):
            rounds = self.storage.get_funding_rounds_for_company(company_id)

        investor_records: Dict[str, Dict[str, Any]] = {}
        for r in rounds:
            # Check participants
            participants = r.participants
            if not participants and hasattr(self.storage, "get_funding_participants_for_round"):
                participants = self.storage.get_funding_participants_for_round(r.round_id)

            for p in participants:
                inv_id = p.investor_id
                if inv_id not in investor_records:
                    # pyrefly: ignore[bad-argument-type]
                    ent = self.storage.get_entity(inv_id)
                    inv_name = ent.legal_name if ent else (p.investor_name or inv_id)
                    # pyrefly: ignore[unsupported-operation]
                    investor_records[inv_id] = {
                        "investor_id": inv_id,
                        "investor_name": inv_name,
                        "is_lead_in_any": p.is_lead,
                        "rounds_participated": [r.round_type.value],
                        "disclosed_commitments": [p.disclosed_commitment] if p.disclosed_commitment else [],
                        "evidence_ids": list(set(r.evidence_ids + p.evidence_ids)),
                    }
                else:
                    if p.is_lead:
                        investor_records[inv_id]["is_lead_in_any"] = True
                    if r.round_type.value not in investor_records[inv_id]["rounds_participated"]:
                        investor_records[inv_id]["rounds_participated"].append(r.round_type.value)
                    if p.disclosed_commitment:
                        investor_records[inv_id]["disclosed_commitments"].append(p.disclosed_commitment)
                    investor_records[inv_id]["evidence_ids"].extend(r.evidence_ids + p.evidence_ids)

        # Check entity relationships for INVESTOR / LEAD_INVESTOR
        relationships = self.storage.get_relationships(company_id)
        for rel in relationships:
            if rel.relationship_type.value in ("INVESTOR", "LEAD_INVESTOR"):
                inv_id = rel.source_entity_id if rel.target_entity_id == company_id else rel.target_entity_id
                if inv_id != company_id and inv_id not in investor_records:
                    ent = self.storage.get_entity(inv_id)
                    if ent:
                        investor_records[inv_id] = {
                            "investor_id": inv_id,
                            "investor_name": ent.legal_name,
                            "is_lead_in_any": rel.relationship_type.value == "LEAD_INVESTOR",
                            "rounds_participated": ["DIRECT_INVESTMENT"],
                            "disclosed_commitments": [],
                            "evidence_ids": rel.evidence_ids,
                        }

        results = list(investor_records.values())
        results.sort(key=lambda x: (not x["is_lead_in_any"], x["investor_name"]))
        return results

    def get_portfolio_for_investor(self, investor_id: str) -> List[Dict[str, Any]]:
        """Retrieve all companies receiving investment from a specific investor."""
        portfolio_records: Dict[str, Dict[str, Any]] = {}

        # 1. Query funding participants where investor_id matches
        if hasattr(self.storage, "get_funding_participants_for_investor"):
            parts: List[FundingParticipant] = self.storage.get_funding_participants_for_investor(investor_id)
            for p in parts:
                r: Optional[FundingRound] = None
                if hasattr(self.storage, "get_funding_round"):
                    r = self.storage.get_funding_round(p.round_id)
                comp_id = r.company_id if r else None
                if comp_id:
                    if comp_id not in portfolio_records:
                        ent = self.storage.get_entity(comp_id)
                        comp_name = ent.legal_name if ent else comp_id
                        portfolio_records[comp_id] = {
                            "company_id": comp_id,
                            "company_name": comp_name,
                            "rounds": [r.round_type.value] if r else [],
                            "is_lead": p.is_lead,
                            "evidence_ids": list(set((r.evidence_ids if r else []) + p.evidence_ids)),
                        }
                    else:
                        if r and r.round_type.value not in portfolio_records[comp_id]["rounds"]:
                            portfolio_records[comp_id]["rounds"].append(r.round_type.value)
                        if p.is_lead:
                            portfolio_records[comp_id]["is_lead"] = True

        # 2. Query entity relationships for PORTFOLIO_COMPANY / INVESTOR
        relationships = self.storage.get_relationships(investor_id)
        for rel in relationships:
            if rel.relationship_type.value in ("PORTFOLIO_COMPANY", "INVESTOR"):
                comp_id = rel.target_entity_id if rel.source_entity_id == investor_id else rel.source_entity_id
                if comp_id != investor_id and comp_id not in portfolio_records:
                    ent = self.storage.get_entity(comp_id)
                    if ent:
                        portfolio_records[comp_id] = {
                            "company_id": comp_id,
                            "company_name": ent.legal_name,
                            "rounds": ["DIRECT_HOLDING"],
                            "is_lead": False,
                            "evidence_ids": rel.evidence_ids,
                        }

        results = list(portfolio_records.values())
        results.sort(key=lambda x: x["company_name"])
        return results

    def get_acquisitions_for_entity(self, entity_id: str) -> List[Dict[str, Any]]:
        """Retrieve all completed or announced acquisitions where entity_id is acquirer."""
        acquisitions: List[TransactionEvent] = []
        if hasattr(self.storage, "get_transactions_for_acquirer"):
            acquisitions = self.storage.get_transactions_for_acquirer(entity_id)

        results: List[Dict[str, Any]] = []
        for tx in acquisitions:
            tgt = self.storage.get_entity(tx.target_id)
            tgt_name = tgt.legal_name if tgt else (tx.target_name or tx.target_id)
            results.append({
                "transaction_id": tx.transaction_id,
                "target_id": tx.target_id,
                "target_name": tgt_name,
                "transaction_type": tx.transaction_type.value,
                "status": tx.status.value,
                "announced_date": tx.announced_date.isoformat() if tx.announced_date else None,
                "completion_date": tx.actual_completion_date.isoformat() if tx.actual_completion_date else None,
                "disclosed_value": tx.disclosed_value,
                "currency": tx.currency.value,
                "consideration_type": tx.consideration_type.value,
                "evidence_ids": tx.evidence_ids,
            })

        results.sort(key=lambda x: x["announced_date"] or "1900-01-01", reverse=True)
        return results

    def get_syndicate_co_investors(self, investor_id: str) -> List[Dict[str, Any]]:
        """Identify co-investors who frequently co-invest in the same funding rounds."""
        portfolio = self.get_portfolio_for_investor(investor_id)
        co_investor_counts: Dict[str, Dict[str, Any]] = {}

        for p_comp in portfolio:
            comp_id = p_comp["company_id"]
            comp_investors = self.get_investors_for_company(comp_id)
            for c_inv in comp_investors:
                c_id = c_inv["investor_id"]
                if c_id == investor_id:
                    continue

                if c_id not in co_investor_counts:
                    co_investor_counts[c_id] = {
                        "investor_id": c_id,
                        "investor_name": c_inv["investor_name"],
                        "co_investment_count": 1,
                        "shared_companies": [p_comp["company_name"]],
                        "evidence_ids": list(c_inv["evidence_ids"]),
                    }
                else:
                    co_investor_counts[c_id]["co_investment_count"] += 1
                    if p_comp["company_name"] not in co_investor_counts[c_id]["shared_companies"]:
                        co_investor_counts[c_id]["shared_companies"].append(p_comp["company_name"])
                    co_investor_counts[c_id]["evidence_ids"].extend(c_inv["evidence_ids"])

        results = list(co_investor_counts.values())
        results.sort(key=lambda x: (-x["co_investment_count"], x["investor_name"]))
        return results

    def find_paths_between_entities(
        self, start_id: str, end_id: str, max_depth: int = 3
    ) -> List[GraphPath]:
        """Find paths connecting two entities in the corporate graph using bounded BFS."""
        if start_id == end_id:
            return []

        start_entity = self.storage.get_entity(start_id)
        end_entity = self.storage.get_entity(end_id)
        if not start_entity or not end_entity:
            return []

        # Queue holds: (current_entity_id, path_nodes, path_edges, visited_ids)
        start_node = GraphNode(
            entity_id=start_entity.entity_id,
            legal_name=start_entity.legal_name,
            entity_type=start_entity.entity_type.value,
            jurisdiction=start_entity.jurisdiction,
        )

        queue = deque([(start_id, [start_node], [], {start_id})])
        found_paths: List[GraphPath] = []

        while queue:
            curr_id, path_nodes, path_edges, visited = queue.popleft()

            if len(path_edges) >= max_depth:
                continue

            # Gather outward and inward edges from relationships, transactions, and funding rounds
            neighbors = self._get_adjacent_neighbors(curr_id)

            for neighbor_id, rel_type, ev_ids, notes, eff_date in neighbors:
                if neighbor_id in visited:
                    continue  # Cycle detected; skip

                next_entity = self.storage.get_entity(neighbor_id)
                if not next_entity:
                    continue

                next_node = GraphNode(
                    entity_id=next_entity.entity_id,
                    legal_name=next_entity.legal_name,
                    entity_type=next_entity.entity_type.value,
                    jurisdiction=next_entity.jurisdiction,
                )

                edge = GraphEdge(
                    source_id=curr_id,
                    target_id=neighbor_id,
                    relationship_type=rel_type,
                    evidence_ids=ev_ids,
                    notes=notes,
                    effective_date=eff_date,
                )

                new_nodes = path_nodes + [next_node]
                new_edges = path_edges + [edge]
                new_visited = visited | {neighbor_id}

                if neighbor_id == end_id:
                    summary = " -> ".join(f"{n.legal_name} [{e.relationship_type}]" for n, e in zip(new_nodes[:-1], new_edges)) + f" -> {end_entity.legal_name}"
                    found_paths.append(
                        GraphPath(
                            nodes=new_nodes,
                            edges=new_edges,
                            depth=len(new_edges),
                            summary=summary,
                        )
                    )
                else:
                    queue.append((neighbor_id, new_nodes, new_edges, new_visited))

        # Sort paths by depth ASC, then summary
        found_paths.sort(key=lambda p: (p.depth, p.summary))
        return found_paths

    find_paths_between = find_paths_between_entities

    def get_funds_managed_by(self, investor_org_id: str) -> List[Dict[str, Any]]:
        """Retrieve investment fund entities managed by an investor organization."""
        rels = self.storage.get_relationships(investor_org_id)
        funds: List[Dict[str, Any]] = []
        for r in rels:
            if r.source_entity_id == investor_org_id and r.relationship_type.value == "FUND_MANAGER":
                fund_ent = self.storage.get_entity(r.target_entity_id)
                funds.append({
                    "fund_id": r.target_entity_id,
                    "fund_name": fund_ent.legal_name if fund_ent else r.target_entity_id,
                    "evidence_ids": r.evidence_ids,
                })
        return funds

    def _get_adjacent_neighbors(
        self, entity_id: str
    # pyrefly: ignore[unknown-name]
    ) -> List[Tuple[str, str, List[str], Optional[str], Optional[str]]]:
        """Collect all directly connected neighboring entities with their relationship metadata."""
        # pyrefly: ignore[unknown-name]
        neighbors: List[Tuple[str, str, List[str], Optional[str], Optional[str]]] = []

        # 1. Entity relationships table
        rels = self.storage.get_relationships(entity_id)
        for r in rels:
            if r.source_entity_id == entity_id:
                neighbors.append((r.target_entity_id, r.relationship_type.value, r.evidence_ids, r.notes, r.effective_date.isoformat() if r.effective_date else None))
            elif r.target_entity_id == entity_id:
                neighbors.append((r.source_entity_id, f"INVERSE_{r.relationship_type.value}", r.evidence_ids, r.notes, r.effective_date.isoformat() if r.effective_date else None))

        # 2. Funding rounds (company -> investor)
        if hasattr(self.storage, "get_funding_rounds_for_company"):
            rounds = self.storage.get_funding_rounds_for_company(entity_id)
            for rnd in rounds:
                parts = rnd.participants
                if not parts and hasattr(self.storage, "get_funding_participants_for_round"):
                    parts = self.storage.get_funding_participants_for_round(rnd.round_id)
                for p in parts:
                    if p.investor_id:
                        neighbors.append((p.investor_id, f"FUNDED_BY_{rnd.round_type.value}", rnd.evidence_ids + p.evidence_ids, f"Funding participant in {rnd.round_type.value}", rnd.announced_date.isoformat() if rnd.announced_date else None))
                    if getattr(p, "fund_id", None):
                        neighbors.append((p.fund_id, f"FUNDED_BY_VEHICLE_{rnd.round_type.value}", rnd.evidence_ids + p.evidence_ids, f"Investment vehicle in {rnd.round_type.value}", rnd.announced_date.isoformat() if rnd.announced_date else None))

        # 3. Transactions (acquirer <-> target)
        if hasattr(self.storage, "get_transactions_for_acquirer"):
            txs_as_acquirer = self.storage.get_transactions_for_acquirer(entity_id)
            for tx in txs_as_acquirer:
                neighbors.append((tx.target_id, f"ACQUIRED_{tx.transaction_type.value}", tx.evidence_ids, f"Status: {tx.status.value}", tx.announced_date.isoformat() if tx.announced_date else None))

        if hasattr(self.storage, "get_transactions_for_target"):
            txs_as_target = self.storage.get_transactions_for_target(entity_id)
            for tx in txs_as_target:
                if tx.acquirer_id:
                    neighbors.append((tx.acquirer_id, f"ACQUIRED_BY_{tx.transaction_type.value}", tx.evidence_ids, f"Status: {tx.status.value}", tx.announced_date.isoformat() if tx.announced_date else None))

        return neighbors
