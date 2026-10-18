"""UI route handlers for workflow catalogue, execution, approvals, and audit viewer."""

import json
import logging
from typing import Any, Dict, Optional
from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from opendeal.agents import (
    ApprovalManager,
    AuditInspector,
    DeterministicPlanner,
    STANDARD_WORKFLOWS,
    STANDARD_WORKFLOWS_BY_ID,
    WorkflowRunner,
    WorkflowState,
    register_standard_workflows,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.get("", response_class=HTMLResponse)
async def workflows_catalogue_view(request: Request):
    """Display workflow blueprints, recent execution runs, and pending approvals."""
    templates = request.app.state.templates
    storage = request.app.state.storage

    register_standard_workflows(storage)
    definitions = storage.list_workflow_definitions()
    runs = storage.list_workflow_runs(limit=25)
    pending_approvals = storage.list_pending_approvals()

    return templates.TemplateResponse(
        request,
        "workflows/index.html",
        {
            "request": request,
            "active_tab": "workflows",
            "workflows": definitions,
            "recent_runs": runs,
            "pending_count": len(pending_approvals),
            "total_definitions": len(definitions),
        },
    )


@router.get("/run/{workflow_id}", response_class=HTMLResponse)
async def workflow_launch_form_view(request: Request, workflow_id: str):
    """Render the configuration and parameter review form before launching a workflow."""
    templates = request.app.state.templates
    storage = request.app.state.storage

    wf_defn = storage.get_workflow_definition(workflow_id)
    if not wf_defn:
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "request": request,
                "error_code": 404,
                "error_title": "Workflow Not Found",
                "error_message": f"Workflow blueprint '{workflow_id}' is not registered.",
            },
            status_code=404,
        )

    all_entities = storage.list_entities(limit=100)

    return templates.TemplateResponse(
        request,
        "workflows/run_form.html",
        {
            "request": request,
            "active_tab": "workflows",
            "workflow": wf_defn,
            "entities": all_entities,
        },
    )


@router.post("/run/{workflow_id}", response_class=HTMLResponse)
async def workflow_execute_action(
    request: Request,
    workflow_id: str,
    entity_id: Optional[str] = Form(None),
    model_type: Optional[str] = Form("dcf"),
    dataset_key: Optional[str] = Form("local_baseline"),
    custom_inputs_json: Optional[str] = Form(None),
):
    """Execute a workflow run with operator-reviewed inputs."""
    storage = request.app.state.storage
    wf_defn = STANDARD_WORKFLOWS_BY_ID.get(workflow_id)
    if not wf_defn:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found.")

    inputs: Dict[str, Any] = {}
    if entity_id:
        inputs["entity_id"] = entity_id.strip()
    if model_type:
        inputs["model_type"] = model_type.strip()
    if dataset_key:
        inputs["dataset_key"] = dataset_key.strip()

    if custom_inputs_json and custom_inputs_json.strip():
        try:
            parsed = json.loads(custom_inputs_json)
            if isinstance(parsed, dict):
                inputs.update(parsed)
        except Exception as e:
            logger.warning("Error parsing custom workflow inputs JSON: %s", e)

    runner = WorkflowRunner(storage=storage)
    run = runner.create_run(wf_defn, inputs=inputs, initiator="ui_operator")
    completed = runner.execute_run(wf_defn, run.run_id)

    return RedirectResponse(url=f"/workflows/runs/{completed.run_id}", status_code=303)


@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def workflow_run_detail_view(request: Request, run_id: str):
    """View detailed execution status, task timeline, outputs, and audit trail."""
    templates = request.app.state.templates
    storage = request.app.state.storage

    run = storage.get_workflow_run(run_id)
    if not run:
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "request": request,
                "error_code": 404,
                "error_title": "Workflow Run Not Found",
                "error_message": f"Execution run '{run_id}' does not exist.",
            },
            status_code=404,
        )

    tasks = storage.get_task_runs_for_workflow(run_id)
    wf_defn = storage.get_workflow_definition(run["workflow_id"])

    inspector = AuditInspector(storage=storage)
    audit_events = inspector.get_run_timeline(run_id)

    appr_mgr = ApprovalManager(storage=storage)
    pending_approvals = appr_mgr.list_pending(run_id=run_id)

    return templates.TemplateResponse(
        request,
        "workflows/run_detail.html",
        {
            "request": request,
            "active_tab": "workflows",
            "run": run,
            "tasks": tasks,
            "workflow": wf_defn,
            "audit_events": audit_events,
            "pending_approvals": pending_approvals,
        },
    )


@router.post("/runs/{run_id}/cancel", response_class=HTMLResponse)
async def workflow_cancel_action(request: Request, run_id: str, reason: str = Form("Cancelled via UI")):
    """Cancel an active or queued workflow run."""
    storage = request.app.state.storage
    runner = WorkflowRunner(storage=storage)
    try:
        runner.cancel_run(run_id, reason=reason)
    except Exception as e:
        logger.warning("Error cancelling run '%s': %s", run_id, e)

    return RedirectResponse(url=f"/workflows/runs/{run_id}", status_code=303)


@router.get("/approvals", response_class=HTMLResponse)
async def workflow_approvals_queue_view(request: Request):
    """Display pending approvals requiring human intervention."""
    templates = request.app.state.templates
    storage = request.app.state.storage

    appr_mgr = ApprovalManager(storage=storage)
    pending = appr_mgr.list_pending()

    return templates.TemplateResponse(
        request,
        "workflows/approvals.html",
        {
            "request": request,
            "active_tab": "workflows",
            "pending_approvals": pending,
        },
    )


@router.post("/approvals/{approval_id}", response_class=HTMLResponse)
async def workflow_approval_decision_action(
    request: Request,
    approval_id: str,
    decision: str = Form(...),
    rationale: str = Form(""),
    decided_by: str = Form("ui_operator"),
):
    """Submit approval or rejection for a pending privileged action request."""
    storage = request.app.state.storage
    appr_mgr = ApprovalManager(storage=storage)
    is_approved = (decision.lower() == "approve")

    try:
        appr_rec = appr_mgr.decide(
            approval_id=approval_id,
            approved=is_approved,
            decided_by=decided_by,
            rationale=rationale or ("Approved via UI" if is_approved else "Rejected via UI"),
        )
        run_id = appr_rec.get("run_id")

        # If approved, resume the paused run
        if is_approved and run_id:
            run_dict = storage.get_workflow_run(run_id)
            if run_dict and run_dict.get("state") == WorkflowState.WAITING_FOR_APPROVAL.value:
                wf_defn = STANDARD_WORKFLOWS_BY_ID.get(run_dict.get("workflow_id"))
                if wf_defn:
                    runner = WorkflowRunner(storage=storage)
                    runner.resume_approved_run(run_id, wf_defn)

        return RedirectResponse(url=f"/workflows/runs/{run_id}" if run_id else "/workflows/approvals", status_code=303)

    except Exception as e:
        templates = request.app.state.templates
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "request": request,
                "error_code": 400,
                "error_title": "Approval Decision Error",
                "error_message": str(e),
            },
            status_code=400,
        )


@router.get("/planner", response_class=HTMLResponse)
async def workflow_planner_form_view(request: Request, q: Optional[str] = Query(None)):
    """Render the AI Planner interface for formulating executable workflow proposals."""
    templates = request.app.state.templates
    proposal = None
    if q and q.strip():
        planner = DeterministicPlanner()
        proposal = planner.propose_plan(q.strip())

    return templates.TemplateResponse(
        request,
        "workflows/planner.html",
        {
            "request": request,
            "active_tab": "workflows",
            "query": q or "",
            "proposal": proposal,
        },
    )


@router.post("/planner", response_class=HTMLResponse)
async def workflow_planner_propose_action(request: Request, intent: str = Form(...)):
    """Formulate an executable plan proposal from an operator query."""
    return RedirectResponse(url=f"/workflows/planner?q={intent.strip()}", status_code=303)
