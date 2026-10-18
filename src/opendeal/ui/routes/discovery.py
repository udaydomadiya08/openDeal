"""Target Discovery and Watchlists UI routes integrating Phase 6 screening."""

import csv
import io
import json
from typing import Optional
from fastapi import APIRouter, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from opendeal.discovery import (
    TargetCriteria,
    TargetDiscoveryService,
    TargetListManager,
)

router = APIRouter(prefix="/discovery")


@router.get("", response_class=HTMLResponse)
async def discovery_index_view(request: Request):
    templates = request.app.state.templates
    storage = request.app.state.storage

    # Fetch recent runs
    recent_runs = []
    try:
        raw_runs = storage.list_discovery_runs(limit=10)
        for r in raw_runs:
            recent_runs.append({
                "run_id": r.get("run_id", ""),
                "created_at": r.get("created_at", ""),
                "screened_count": r.get("eligible_count", 0),
            })
    except Exception:
        recent_runs = []

    return templates.TemplateResponse(
        request,
        "discovery/index.html",
        {
            "request": request,
            "recent_runs": recent_runs,
            "error": None,
        },
    )


@router.post("/screen", response_class=HTMLResponse)
async def discovery_screen_view(
    request: Request,
    jurisdictions: Optional[str] = Form(None),
    min_revenue: Optional[float] = Form(None),
    max_revenue: Optional[float] = Form(None),
    sectors: Optional[str] = Form(None),
    max_candidates: int = Form(25),
    missing_data_penalty: float = Form(0.20),
    require_active: Optional[str] = Form(None),
):
    templates = request.app.state.templates
    storage = request.app.state.storage

    try:
        jur_list = [j.strip().upper() for j in jurisdictions.split(",") if j.strip()] if jurisdictions else []
        sec_list = [s.strip() for s in sectors.split(",") if s.strip()] if sectors else []

        criteria = TargetCriteria(
            jurisdictions=jur_list,
            min_revenue=min_revenue,
            max_revenue=max_revenue,
            sectors=sec_list,
            max_candidates=max_candidates,
            missing_data_penalty=missing_data_penalty,
            require_active=(require_active == "yes"),
        )

        service = TargetDiscoveryService(storage=storage)
        res = service.screen_targets(criteria, is_live=False, save_run=True)

        return RedirectResponse(
            url=f"/discovery/runs/{res.metadata.run_id}",
            status_code=303,
        )

    except Exception as exc:
        return templates.TemplateResponse(
        request,
        "discovery/index.html",
            {
                "request": request,
                "recent_runs": [],
                "error": f"Screening execution error: {str(exc)}",
            },
            status_code=400,
        )


@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def discovery_run_detail_view(request: Request, run_id: str):
    templates = request.app.state.templates
    storage = request.app.state.storage

    run_data = storage.get_discovery_run(run_id)
    if not run_data:
        return templates.TemplateResponse(
        request,
        "error.html",
            {
                "request": request,
                "error_code": 404,
                "error_title": "Discovery Run Not Found",
                "error_message": f"Screening run '{run_id}' not found in local storage.",
            },
            status_code=404,
        )

    if hasattr(run_data, "model_dump"):
        run_data = run_data.model_dump()

    ranked_candidates = []
    excluded_candidates = []

    for t in run_data.get("targets", []):
        cand = t.get("candidate", {})
        entity = cand.get("entity", {}) if isinstance(cand.get("entity"), dict) else cand
        entity_id = entity.get("entity_id") or cand.get("entity_id", "")
        company_name = entity.get("canonical_name") or entity.get("primary_name") or cand.get("company_name", "") or entity_id
        jurisdiction = entity.get("legal_jurisdiction") or entity.get("jurisdiction") or cand.get("jurisdiction", "")

        elig = t.get("eligibility", {})
        scoring = t.get("scoring", {}) or {}
        outcome = elig.get("outcome", "")

        overall_score = float(scoring.get("overall_score") or scoring.get("composite_score", 0.0))
        fb = scoring.get("factor_breakdowns", {}) or {}

        fit_score = float(fb.get("fit", {}).get("weighted_score", 0.0) * 100.0) if isinstance(fb.get("fit"), dict) else 0.0
        scale_score = float(fb.get("scale", {}).get("weighted_score", 0.0) * 100.0) if isinstance(fb.get("scale"), dict) else 0.0
        penalties = float(sum(item.get("missing_penalty", 0.0) * 100.0 for item in fb.values() if isinstance(item, dict)))

        if outcome == "ELIGIBLE" or overall_score > 0:
            ranked_candidates.append({
                "entity_id": entity_id,
                "company_name": company_name,
                "jurisdiction": jurisdiction,
                "composite_score": overall_score,
                "fit_score": fit_score,
                "scale_score": scale_score,
                "penalty": penalties,
                "scoring_breakdown": scoring,
            })
        else:
            reasons = elig.get("exclusion_reasons", [])
            reason_str = ", ".join([r.get("reason_code", "EXCLUDED") for r in reasons]) if reasons else "Failed criteria"
            detail_str = "; ".join([r.get("message", "") for r in reasons if r.get("message")])
            excluded_candidates.append({
                "entity_id": entity_id,
                "company_name": company_name,
                "jurisdiction": jurisdiction,
                "reason": reason_str,
                "detail": detail_str,
            })

    ranked_candidates = sorted(ranked_candidates, key=lambda x: x["composite_score"], reverse=True)

    metadata = run_data.get("metadata", {})
    criteria_dict = metadata.get("criteria", {})

    return templates.TemplateResponse(
        request,
        "discovery/results.html",
        {
            "request": request,
            "run_id": run_id,
            "total_evaluated": metadata.get("total_evaluated", len(run_data.get("targets", []))),
            "eligible_count": len(ranked_candidates),
            "ranked_candidates": ranked_candidates,
            "excluded_candidates": excluded_candidates,
            "criteria_params": criteria_dict,
        },
    )


@router.get("/export/{run_id}")
async def discovery_export_run_view(
    request: Request,
    run_id: str,
    format: str = Query("csv", pattern="^(csv|json)$"),
):
    storage = request.app.state.storage
    run_data = storage.get_discovery_run(run_id)
    if not run_data:
        raise HTTPException(status_code=404, detail="Run not found")

    if hasattr(run_data, "model_dump"):
        run_data = run_data.model_dump()

    targets = run_data.get("targets", [])

    if format == "json":
        return Response(
            content=json.dumps(run_data, indent=2, default=str),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=screening_run_{run_id}.json"},
        )
    else:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["rank", "entity_id", "company_name", "jurisdiction", "composite_score", "eligibility"])
        for idx, t in enumerate(targets):
            cand = t.get("candidate", {})
            elig = t.get("eligibility", {})
            scoring = t.get("scoring", {})
            writer.writerow([
                idx + 1,
                cand.get("entity_id", ""),
                cand.get("company_name", ""),
                cand.get("jurisdiction", ""),
                scoring.get("composite_score", 0.0),
                elig.get("outcome", ""),
            ])
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=screening_run_{run_id}.csv"},
        )


# ── Watchlists Sub-routes ───────────────────────────────────────────────────

@router.get("/watchlists", response_class=HTMLResponse)
async def watchlists_list_view(request: Request):
    templates = request.app.state.templates
    storage = request.app.state.storage

    mgr = TargetListManager(storage=storage)
    lists = mgr.list_lists()

    return templates.TemplateResponse(
        request,
        "discovery/watchlists.html",
        {
            "request": request,
            "lists": lists,
            "error": None,
        },
    )


@router.post("/watchlists/create", response_class=HTMLResponse)
async def watchlists_create_view(
    request: Request,
    name: str = Form(...),
    description: Optional[str] = Form(None),
):
    templates = request.app.state.templates
    storage = request.app.state.storage

    try:
        mgr = TargetListManager(storage=storage)
        tl = mgr.create_list(name=name, description=description or "")
        return RedirectResponse(
            url=f"/discovery/watchlists/{tl.list_id}",
            status_code=303,
        )
    except Exception as exc:
        lists = TargetListManager(storage=storage).list_lists()
        return templates.TemplateResponse(
        request,
        "discovery/watchlists.html",
            {
                "request": request,
                "lists": lists,
                "error": f"Failed to create list: {str(exc)}",
            },
            status_code=400,
        )


@router.get("/watchlists/{list_id}", response_class=HTMLResponse)
async def watchlist_detail_view(request: Request, list_id: str):
    templates = request.app.state.templates
    storage = request.app.state.storage

    mgr = TargetListManager(storage=storage)
    target_list = mgr.get_list(list_id)
    if not target_list:
        return templates.TemplateResponse(
        request,
        "error.html",
            {
                "request": request,
                "error_code": 404,
                "error_title": "Target List Not Found",
                "error_message": f"Target list '{list_id}' not found in storage.",
            },
            status_code=404,
        )

    members = mgr.get_members(list_id)
    all_entities = storage.list_entities(limit=200)

    return templates.TemplateResponse(
        request,
        "discovery/watchlist_detail.html",
        {
            "request": request,
            "target_list": target_list,
            "members": members,
            "available_entities": all_entities,
            "error": None,
        },
    )


@router.post("/watchlists/{list_id}/add", response_class=HTMLResponse)
async def watchlist_add_member_view(
    request: Request,
    list_id: str,
    entity_id: str = Form(...),
    notes: Optional[str] = Form(None),
):
    templates = request.app.state.templates
    storage = request.app.state.storage

    mgr = TargetListManager(storage=storage)
    try:
        mgr.add_member(list_id=list_id, entity_id=entity_id, notes=notes or "")
        return RedirectResponse(
            url=f"/discovery/watchlists/{list_id}",
            status_code=303,
        )
    except Exception as exc:
        target_list = mgr.get_list(list_id)
        members = mgr.get_members(list_id)
        all_entities = storage.list_entities(limit=200)
        return templates.TemplateResponse(
        request,
        "discovery/watchlist_detail.html",
            {
                "request": request,
                "target_list": target_list,
                "members": members,
                "available_entities": all_entities,
                "error": f"Failed to add member: {str(exc)}",
            },
            status_code=400,
        )


@router.post("/watchlists/{list_id}/remove/{entity_id}", response_class=HTMLResponse)
async def watchlist_remove_member_view(
    request: Request,
    list_id: str,
    entity_id: str,
):
    storage = request.app.state.storage
    mgr = TargetListManager(storage=storage)
    try:
        mgr.remove_member(list_id=list_id, entity_id=entity_id)
    except Exception:
        pass
    return RedirectResponse(
        url=f"/discovery/watchlists/{list_id}",
        status_code=303,
    )


@router.get("/watchlists/{list_id}/export")
async def watchlist_export_view(
    request: Request,
    list_id: str,
    format: str = Query("csv", pattern="^(csv|json)$"),
):
    storage = request.app.state.storage
    mgr = TargetListManager(storage=storage)
    exported_str = mgr.export_list(list_id=list_id, format=format)

    media_type = "application/json" if format == "json" else "text/csv"
    return Response(
        content=exported_str,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename=target_list_{list_id}.{format}"},
    )
