"""Registry of specialized financial intelligence agents invoking Phase 1-8 services."""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from opendeal.agents.contracts import PermissionCategory
from opendeal.storage.base import StorageInterface

logger = logging.getLogger(__name__)


# ── Structured Agent Output Artifacts ───────────────────────────────────────

class DataQualityReport(BaseModel):
    """Output artifact of the Evidence & Data-Quality Agent."""
    entity_id: Optional[str] = None
    total_facts_inspected: int = 0
    total_evidence_inspected: int = 0
    missing_required_fields: List[Dict[str, Any]] = Field(default_factory=list)
    conflicting_facts: List[Dict[str, Any]] = Field(default_factory=list)
    unresolvable_evidence_ids: List[str] = Field(default_factory=list)
    currency_or_period_inconsistencies: List[Dict[str, Any]] = Field(default_factory=list)
    overall_quality_score: float = 1.0  # 0.0 to 1.0
    suggested_remediations: List[str] = Field(default_factory=list)


class DealIntelligenceReport(BaseModel):
    """Output artifact of the Deal Intelligence Agent."""
    entity_id: str
    total_funding_rounds: int = 0
    total_transactions: int = 0
    funding_history: List[Dict[str, Any]] = Field(default_factory=list)
    transactions: List[Dict[str, Any]] = Field(default_factory=list)
    timeline_events: List[Dict[str, Any]] = Field(default_factory=list)
    deal_conflicts: List[Dict[str, Any]] = Field(default_factory=list)
    undisclosed_fields: List[str] = Field(default_factory=list)


class ValuationReadinessReport(BaseModel):
    """Output artifact of the Valuation Preparation Agent."""
    entity_id: str
    model_type: str
    is_ready_for_calculation: bool = False
    provided_inputs: Dict[str, Any] = Field(default_factory=dict)
    missing_required_inputs: List[str] = Field(default_factory=list)
    currency_mismatches: List[str] = Field(default_factory=list)
    valuation_result: Optional[Dict[str, Any]] = None
    warnings: List[str] = Field(default_factory=list)


# ── Base Agent Interface ───────────────────────────────────────────────────

class BaseAgent(ABC):
    """Abstract contract for specialized OpenDeal agents."""

    def __init__(self, storage: StorageInterface) -> None:
        self.storage = storage

    @property
    @abstractmethod
    def agent_name(self) -> str:
        """Unique identifier of the agent."""
        pass

    @property
    @abstractmethod
    def declared_permissions(self) -> List[PermissionCategory]:
        """Permissions permitted to this agent."""
        pass

    @abstractmethod
    def execute(self, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the agent task using existing Phase 1-8 service contracts."""
        pass


# ── Specialized Agent Implementations ───────────────────────────────────────

class EvidenceAndDataQualityAgent(BaseAgent):
    """Agent inspecting records for missing fields, conflicts, and evidence integrity."""

    @property
    def agent_name(self) -> str:
        return "evidence_data_quality_agent"

    @property
    def declared_permissions(self) -> List[PermissionCategory]:
        return [PermissionCategory.READ_LOCAL_DATA, PermissionCategory.CREATE_WORKFLOW_ARTIFACT]

    def execute(self, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        entity_id = inputs.get("entity_id")
        facts = self.storage.get_facts_for_entity(entity_id) if entity_id else self.storage.list_facts(limit=500)
        all_evidence = self.storage.get_evidence_for_entity(entity_id) if entity_id else []
        evidence_id_set = {e.evidence_id for e in all_evidence}

        missing_fields = []
        conflicts = []
        unresolvable = []
        inconsistencies = []

        # Check fact integrity
        fact_by_var_period: Dict[str, List[Any]] = {}
        for f in facts:
            # Check mandatory fields
            if not f.canonical_variable:
                missing_fields.append({"fact_id": f.fact_id, "field": "canonical_variable"})
            if not f.currency or str(getattr(f.currency, "value", f.currency)).upper() in ("", "UNKNOWN"):
                missing_fields.append({"fact_id": f.fact_id, "field": "currency"})
            if not f.fiscal_period or str(getattr(f.fiscal_period, "value", f.fiscal_period)).upper() in ("", "OTHER"):
                missing_fields.append({"fact_id": f.fact_id, "field": "fiscal_period"})

            # Check unresolvable evidence IDs
            raw_ev_ids = (
                getattr(f, "evidence_ids", [])
                + getattr(f, "source_evidence_ids", [])
                + getattr(f, "metadata", {}).get("unresolvable_evidence_ids", [])
                + getattr(f, "metadata", {}).get("citation_ids", [])
            )
            for ev_id in raw_ev_ids:
                if ev_id not in evidence_id_set and self.storage.get_evidence(ev_id) is None:
                    unresolvable.append(ev_id)

            key = f"{f.canonical_variable}:{f.fiscal_year}:{f.fiscal_period}"
            fact_by_var_period.setdefault(key, []).append(f)

        # Detect conflicting facts for identical metric & period
        for key, matching_facts in fact_by_var_period.items():
            if len(matching_facts) > 1:
                vals = {m.normalized_value for m in matching_facts if m.normalized_value is not None}
                if len(vals) > 1:
                    conflicts.append({
                        "metric_period": key,
                        "fact_ids": [m.fact_id for m in matching_facts],
                        "differing_values": list(vals),
                    })

        penalty = (len(missing_fields) * 0.05) + (len(conflicts) * 0.1) + (len(unresolvable) * 0.05)
        quality_score = max(0.0, min(1.0, 1.0 - penalty))

        remediations = []
        if missing_fields:
            remediations.append(f"Ingest missing metadata fields for {len(missing_fields)} fact records.")
        if conflicts:
            remediations.append(f"Review and reconcile {len(conflicts)} conflicting financial facts.")
        if unresolvable:
            remediations.append(f"Verify provenance source for {len(unresolvable)} dangling evidence IDs.")

        report = DataQualityReport(
            entity_id=entity_id,
            total_facts_inspected=len(facts),
            total_evidence_inspected=len(all_evidence),
            missing_required_fields=missing_fields,
            conflicting_facts=conflicts,
            unresolvable_evidence_ids=list(set(unresolvable)),
            currency_or_period_inconsistencies=inconsistencies,
            overall_quality_score=quality_score,
            suggested_remediations=remediations,
        )
        findings = []
        for c in conflicts:
            findings.append({"severity": "HIGH", "category": "CONFLICTING_FACTS", "details": c})
        for u in set(unresolvable):
            findings.append({"severity": "HIGH", "category": "UNRESOLVABLE_EVIDENCE", "evidence_id": u})
        for m in missing_fields:
            findings.append({"severity": "MEDIUM", "category": "MISSING_REQUIRED_METADATA", "details": m})

        report_dict = report.model_dump()
        integrity_audit_findings = {
            "entity_id": entity_id,
            "overall_quality_score": quality_score,
            "conflicting_facts": conflicts,
            "unresolvable_evidence_ids": list(set(unresolvable)),
            "missing_required_fields": missing_fields,
            "severity_ranked_findings": findings,
            "suggested_remediations": remediations,
            "read_only_mode": True,
        }
        return {
            "data_quality_report": report_dict,
            "integrity_audit_findings": integrity_audit_findings,
        }


class CompanyResearchAgent(BaseAgent):
    """Agent generating evidence-backed research briefs using Phase 5 services."""

    @property
    def agent_name(self) -> str:
        return "company_research_agent"

    @property
    def declared_permissions(self) -> List[PermissionCategory]:
        return [
            PermissionCategory.READ_LOCAL_DATA,
            PermissionCategory.GENERATE_RESEARCH,
            PermissionCategory.CREATE_WORKFLOW_ARTIFACT,
        ]

    def execute(self, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        entity_id = inputs.get("entity_id")
        if not entity_id:
            raise ValueError("CompanyResearchAgent requires 'entity_id' parameter.")

        entity = self.storage.get_entity(entity_id)
        if not entity:
            raise ValueError(f"Entity '{entity_id}' not found in local storage.")

        from opendeal.research.contracts import ResearchScope
        from opendeal.research.generator import ResearchBriefGenerator
        from opendeal.research.providers.mock import DeterministicMockProvider

        provider = DeterministicMockProvider()
        generator = ResearchBriefGenerator(
            storage=self.storage,
            provider=provider,
        )

        scope = ResearchScope(entity_id=entity_id)
        brief = generator.generate_brief(scope=scope)
        return {"research_brief": brief.model_dump() if hasattr(brief, "model_dump") else brief}


class TargetDiscoveryAgent(BaseAgent):
    """Agent screening candidates against deterministic criteria using Phase 6 services."""

    @property
    def agent_name(self) -> str:
        return "target_discovery_agent"

    @property
    def declared_permissions(self) -> List[PermissionCategory]:
        return [PermissionCategory.READ_LOCAL_DATA, PermissionCategory.CREATE_WORKFLOW_ARTIFACT]

    def execute(self, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        from opendeal.discovery.contracts import TargetCriteria
        from opendeal.discovery.service import TargetDiscoveryService

        criteria_dict = inputs.get("criteria", {})
        criteria = TargetCriteria.model_validate(criteria_dict) if criteria_dict else TargetCriteria()

        engine = TargetDiscoveryService(storage=self.storage)
        screening_result = engine.screen_targets(criteria=criteria)
        dumped = screening_result.model_dump() if hasattr(screening_result, "model_dump") else screening_result
        meta = dumped.get("metadata", {})
        targets = dumped.get("targets", [])
        return {
            "screening_result": dumped,
            "candidate_ranking": {
                "total_candidates_screened": meta.get("total_candidates_evaluated", len(targets)),
                "eligible_count": meta.get("eligible_count", 0),
                "shortlist": targets,
                "targets": targets,
                "metadata": meta,
            },
        }


class DealIntelligenceAgent(BaseAgent):
    """Agent assembling funding, M&A history, timelines, and conflicts using Phase 7 services."""

    @property
    def agent_name(self) -> str:
        return "deal_intelligence_agent"

    @property
    def declared_permissions(self) -> List[PermissionCategory]:
        return [PermissionCategory.READ_LOCAL_DATA, PermissionCategory.CREATE_WORKFLOW_ARTIFACT]

    def execute(self, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        entity_id = inputs.get("entity_id")
        dataset_key = inputs.get("dataset_key", "deals_local_baseline")

        if not entity_id:
            # Monitoring mode: snapshot comparison across local dataset
            prev_snapshot = self.storage.get_latest_workflow_snapshot(dataset_key)

            cursor = self.storage.conn.cursor()
            cursor.execute("SELECT round_id FROM funding_rounds")
            current_rounds = {row[0] for row in cursor.fetchall()}

            cursor.execute("SELECT transaction_id, status FROM transactions")
            current_txs = {row[0]: row[1] for row in cursor.fetchall()}

            prev_hashes = prev_snapshot.get("content_hashes", {}) if prev_snapshot else {}
            prev_round_ids = set(prev_hashes.get("round_ids", []))
            prev_tx_ids = set(prev_hashes.get("transaction_ids", []))
            prev_tx_statuses = prev_hashes.get("tx_statuses", {})

            if prev_snapshot is None:
                new_rounds = []
                new_txs = []
            else:
                new_rounds = list(current_rounds - prev_round_ids)
                new_txs = list(set(current_txs.keys()) - prev_tx_ids)

            status_changes = []
            for tx_id, status in current_txs.items():
                if tx_id in prev_tx_statuses and prev_tx_statuses[tx_id] != status:
                    status_changes.append({"transaction_id": tx_id, "old_status": prev_tx_statuses[tx_id], "new_status": status})

            import uuid
            new_snapshot_id = f"snap_{uuid.uuid4().hex[:12]}"
            self.storage.save_workflow_snapshot({
                "snapshot_id": new_snapshot_id,
                "dataset_key": dataset_key,
                "record_counts": {
                    "total_funding_rounds": len(current_rounds),
                    "total_transactions": len(current_txs),
                },
                "content_hashes": {
                    "round_ids": list(current_rounds),
                    "transaction_ids": list(current_txs.keys()),
                    "tx_statuses": current_txs,
                },
            })

            change_report = {
                "dataset_key": dataset_key,
                "new_funding_count": len(new_rounds),
                "new_funding_ids": new_rounds,
                "new_transactions_count": len(new_txs),
                "new_transaction_ids": new_txs,
                "status_changes": status_changes,
                "previous_snapshot_timestamp": prev_snapshot.get("snapshot_timestamp") if prev_snapshot else None,
                "current_snapshot_id": new_snapshot_id,
                "offline_mode": True,
            }
            return {
                "deal_changes": change_report,
                "deal_intelligence_report": change_report,
            }

        from opendeal.deals.service import DealsIntelligenceService

        deals_service = DealsIntelligenceService(storage=self.storage)
        overview = deals_service.get_company_deals_overview(entity_id)

        rounds = overview.funding_rounds if overview else self.storage.get_funding_rounds_for_company(entity_id)
        txs = (
            (overview.transactions_as_target + overview.acquisitions + overview.divestitures)
            if overview
            else self.storage.get_transactions_for_entity(entity_id)
        )
        timeline = overview.timeline if overview else []

        conflicts = []
        for r in rounds:
            conflicts.extend(self.storage.get_deal_conflicts_for_event(r.round_id))
        for t in txs:
            conflicts.extend(self.storage.get_deal_conflicts_for_event(t.transaction_id))

        undisclosed = []
        for r in rounds:
            if getattr(r, "disclosed_amount", None) is None and getattr(r, "raised_amount", None) is None:
                undisclosed.append(f"Round '{r.round_id}' raised amount undisclosed")
            if getattr(r, "post_money_valuation", None) is None:
                undisclosed.append(f"Round '{r.round_id}' post-money valuation undisclosed")
        for t in txs:
            if t.disclosed_value is None:
                undisclosed.append(f"Transaction '{t.transaction_id}' value undisclosed")

        report = DealIntelligenceReport(
            entity_id=entity_id,
            total_funding_rounds=len(rounds),
            total_transactions=len(txs),
            funding_history=[r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in rounds],
            transactions=[t.model_dump() if hasattr(t, "model_dump") else dict(t) for t in txs],
            timeline_events=[e.model_dump() if hasattr(e, "model_dump") else dict(e) for e in timeline],
            deal_conflicts=[c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in conflicts],
            undisclosed_fields=undisclosed,
        )
        return {"deal_intelligence_report": report.model_dump()}


class ValuationPreparationAgent(BaseAgent):
    """Agent verifying inputs and deterministically executing Python valuation engines."""

    @property
    def agent_name(self) -> str:
        return "valuation_preparation_agent"

    @property
    def declared_permissions(self) -> List[PermissionCategory]:
        return [
            PermissionCategory.READ_LOCAL_DATA,
            PermissionCategory.RUN_DETERMINISTIC_VALUATION,
            PermissionCategory.CREATE_WORKFLOW_ARTIFACT,
        ]

    def execute(self, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        entity_id = inputs.get("entity_id")
        model_type = inputs.get("model_type", "dcf").lower()
        assumptions = inputs.get("assumptions", {})

        missing_required = []
        warnings = []
        result = None

        if model_type == "dcf":
            cash_flows = assumptions.get("cash_flows") or assumptions.get("forecast_cash_flows") or assumptions.get("forecast_revenue") or []
            wacc = assumptions.get("discount_rate") if assumptions.get("discount_rate") is not None else assumptions.get("wacc")
            growth = assumptions.get("perpetual_growth_rate") if assumptions.get("perpetual_growth_rate") is not None else assumptions.get("terminal_growth_rate")

            if not cash_flows:
                missing_required.append("cash_flows")
            if wacc is None:
                missing_required.append("discount_rate")
            if growth is None:
                missing_required.append("perpetual_growth_rate")

            if not missing_required:
                from opendeal.valuation.engine.contracts import DCFInput, DiscountingConvention
                from opendeal.valuation.engine.primitives import calculate_dcf
                try:
                    convention = (
                        DiscountingConvention.MID_YEAR
                        if assumptions.get("mid_year_convention", True)
                        else DiscountingConvention.YEAR_END
                    )
                    dcf_in = DCFInput(
                        forecast_cash_flows=[float(c) for c in cash_flows],
                        discount_rate=float(wacc),
                        terminal_growth_rate=float(growth),
                        discounting_convention=convention,
                        cash_and_equivalents=float(assumptions.get("cash") or assumptions.get("cash_and_equivalents", 0.0)),
                        total_debt=float(assumptions.get("total_debt", 0.0)),
                        shares_outstanding=float(assumptions.get("shares_outstanding")) if assumptions.get("shares_outstanding") else None,
                    )
                    dcf_res = calculate_dcf(dcf_in)
                    result = dcf_res.model_dump() if hasattr(dcf_res, "model_dump") else dcf_res
                except Exception as e:
                    warnings.append(f"DCF execution error: {str(e)}")

        elif model_type == "wacc":
            risk_free = assumptions.get("risk_free_rate")
            beta = assumptions.get("equity_beta")
            erp = assumptions.get("equity_risk_premium")
            debt_weight = assumptions.get("debt_weight")
            cost_of_debt = assumptions.get("cost_of_debt")
            tax_rate = assumptions.get("tax_rate")

            for k, val in [
                ("risk_free_rate", risk_free),
                ("equity_beta", beta),
                ("equity_risk_premium", erp),
                ("debt_weight", debt_weight),
                ("cost_of_debt", cost_of_debt),
                ("tax_rate", tax_rate),
            ]:
                if val is None:
                    missing_required.append(k)

            if not missing_required:
                from opendeal.valuation.engine.contracts import WACCInput
                from opendeal.valuation.engine.primitives import calculate_wacc
                try:
                    wacc_in = WACCInput(
                        risk_free_rate=float(risk_free),
                        beta=float(beta),
                        market_risk_premium=float(erp),
                        pre_tax_cost_of_debt=float(cost_of_debt),
                        tax_rate=float(tax_rate),
                        debt_weight=float(debt_weight),
                        equity_weight=1.0 - float(debt_weight),
                    )
                    wacc_res = calculate_wacc(wacc_in)
                    result = wacc_res.model_dump() if hasattr(wacc_res, "model_dump") else wacc_res
                except Exception as e:
                    warnings.append(f"WACC execution error: {str(e)}")

        else:
            warnings.append(f"Valuation model '{model_type}' readiness check passed; computation deferred.")

        is_ready = len(missing_required) == 0 and result is not None
        report = ValuationReadinessReport(
            entity_id=entity_id or "GENERIC",
            model_type=model_type,
            is_ready_for_calculation=is_ready,
            provided_inputs=assumptions,
            missing_required_inputs=missing_required,
            valuation_result=result,
            warnings=warnings,
        )
        report_dict = report.model_dump()
        out = {
            "valuation_readiness_report": report_dict,
            "valuation_readiness": {
                "is_ready_for_calculation": is_ready,
                "missing_inputs": missing_required,
                "report": report_dict,
            },
        }
        if result is not None:
            ev = result.get("enterprise_value", 0.0)
            eq = result.get("equity_value", 0.0)
            ps = result.get("implied_share_price") or result.get("per_share_value", 0.0)
            out["valuation_results"] = {
                "status": "EXECUTED",
                "enterprise_value": ev,
                "equity_value": eq,
                "per_share_value": ps,
                "valuation_summary": result,
            }
        else:
            out["valuation_results"] = {
                "status": "NOT_EXECUTED",
                "reason": "Missing required inputs or model execution deferred",
                "missing_inputs": missing_required,
            }
        return out


# ── Agent Registry Container ────────────────────────────────────────────────

class AgentRegistry:
    """Registry maintaining active specialized agent workers."""

    def __init__(self, storage: StorageInterface) -> None:
        self.storage = storage
        self._agents: Dict[str, BaseAgent] = {}
        self._register_default_agents()

    def _register_default_agents(self) -> None:
        """Register the 5 specialized domain agents."""
        agents = [
            EvidenceAndDataQualityAgent(self.storage),
            CompanyResearchAgent(self.storage),
            TargetDiscoveryAgent(self.storage),
            DealIntelligenceAgent(self.storage),
            ValuationPreparationAgent(self.storage),
        ]
        for a in agents:
            self._agents[a.agent_name] = a

    def get_agent(self, agent_name: str) -> Optional[BaseAgent]:
        """Retrieve registered agent worker by name."""
        return self._agents.get(agent_name)

    def register_agent(self, agent: BaseAgent) -> None:
        """Register a custom or specialized agent worker."""
        self._agents[agent.agent_name] = agent

    def list_agents(self) -> List[str]:
        """List all registered agent names."""
        return sorted(list(self._agents.keys()))
