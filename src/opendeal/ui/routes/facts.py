"""Financial Facts and Evidence Explorer UI routes."""

from typing import Optional
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/facts")


@router.get("", response_class=HTMLResponse)
async def list_facts_view(
    request: Request,
    entity_id: Optional[str] = Query(None, description="Filter by entity ID"),
    metric: Optional[str] = Query(None, description="Filter by canonical variable"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    templates = request.app.state.templates
    storage = request.app.state.storage

    offset = (page - 1) * limit
    facts = storage.list_facts(
        entity_id=entity_id if entity_id else None,
        canonical_variable=metric if metric else None,
        limit=limit,
        offset=offset,
    )

    # Fetch entities list for dropdown filter
    all_entities = storage.list_entities(limit=200)

    # Collect known canonical variables for filter dropdown
    known_metrics = [
        "revenue", "ebitda", "ebit", "net_income", "gross_profit",
        "total_assets", "total_liabilities", "cash_and_equivalents", "total_debt",
        "shares_outstanding", "operating_cash_flow", "capex", "free_cash_flow"
    ]

    return templates.TemplateResponse(
        request,
        "facts/list.html",
        {
            "request": request,
            "facts": facts,
            "entities": all_entities,
            "metrics": known_metrics,
            "selected_entity_id": entity_id or "",
            "selected_metric": metric or "",
            "current_page": page,
        },
    )


@router.get("/{fact_id}", response_class=HTMLResponse)
async def fact_detail_view(request: Request, fact_id: str):
    templates = request.app.state.templates
    storage = request.app.state.storage

    fact = storage.get_fact(fact_id)
    if not fact:
        return templates.TemplateResponse(
        request,
        "error.html",
            {
                "request": request,
                "error_code": 404,
                "error_title": "Fact Not Found",
                "error_message": f"No financial fact record with identifier '{fact_id}' exists in local storage.",
            },
            status_code=404,
        )

    entity = storage.get_entity(fact.entity_id)

    # Retrieve all citing evidence records
    evidence_records = []
    for eid in (fact.source_evidence_ids or []):
        ev = storage.get_evidence(eid)
        if ev:
            evidence_records.append(ev)

    return templates.TemplateResponse(
        request,
        "facts/detail.html",
        {
            "request": request,
            "fact": fact,
            "entity": entity,
            "evidence_records": evidence_records,
        },
    )


@router.get("/evidence/{evidence_id}", response_class=HTMLResponse)
async def evidence_detail_view(request: Request, evidence_id: str):
    templates = request.app.state.templates
    storage = request.app.state.storage

    evidence = storage.get_evidence(evidence_id)
    if not evidence:
        return templates.TemplateResponse(
        request,
        "error.html",
            {
                "request": request,
                "error_code": 404,
                "error_title": "Evidence Citation Not Found",
                "error_message": f"No evidence record with identifier '{evidence_id}' exists in local storage.",
            },
            status_code=404,
        )

    source = storage.get_source(evidence.source_id) if hasattr(evidence, "source_id") and evidence.source_id else None

    return templates.TemplateResponse(
        request,
        "facts/evidence.html",
        {
            "request": request,
            "evidence": evidence,
            "source": source,
        },
    )
