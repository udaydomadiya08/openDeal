"""Companies and Entity Explorer UI routes."""

from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from opendeal.deals.service import DealsIntelligenceService

router = APIRouter(prefix="/companies")


@router.get("", response_class=HTMLResponse)
async def list_companies_view(
    request: Request,
    q: Optional[str] = Query(None, description="Search query by name or identifier"),
    jurisdiction: Optional[str] = Query(None, description="Filter by jurisdiction code"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    templates = request.app.state.templates
    storage = request.app.state.storage

    all_entities = storage.list_entities(limit=500)

    # Filter in Python if query or jurisdiction provided
    filtered = all_entities
    if q:
        query_str = q.lower().strip()
        filtered = [
            e for e in filtered
            if query_str in e.canonical_name.lower()
            or query_str in e.entity_id.lower()
            or any(query_str in (alias or "").lower() for alias in (e.aliases or []))
        ]

    if jurisdiction:
        jur_str = jurisdiction.upper().strip()
        filtered = [
            e for e in filtered
            if (e.legal_jurisdiction or "").upper() == jur_str
        ]

    total_count = len(filtered)
    offset = (page - 1) * limit
    paginated = filtered[offset : offset + limit]

    # Collect distinct jurisdictions for filter dropdown
    jurisdictions = sorted(list({e.legal_jurisdiction for e in all_entities if e.legal_jurisdiction}))

    return templates.TemplateResponse(
        request,
        "companies/list.html",
        {
            "request": request,
            "entities": paginated,
            "total_count": total_count,
            "query": q or "",
            "selected_jurisdiction": jurisdiction or "",
            "jurisdictions": jurisdictions,
            "current_page": page,
            "total_pages": max(1, (total_count + limit - 1) // limit),
        },
    )


@router.get("/{entity_id}", response_class=HTMLResponse)
async def company_detail_view(request: Request, entity_id: str):
    templates = request.app.state.templates
    storage = request.app.state.storage

    entity = storage.get_entity(entity_id)
    if not entity:
        return templates.TemplateResponse(
        request,
        "error.html",
            {
                "request": request,
                "error_code": 404,
                "error_title": "Entity Not Found",
                "error_message": f"No entity record with identifier '{entity_id}' exists in local storage.",
            },
            status_code=404,
        )

    # Fetch legal identifiers, facts, evidence, and relationships
    legal_identifiers = storage.get_legal_identifiers(entity_id)
    facts = storage.get_facts_for_entity(entity_id)
    evidence = storage.get_evidence_for_entity(entity_id)
    relationships = storage.get_relationships(entity_id)

    # Fetch deals overview via Phase 7 service
    deals_service = DealsIntelligenceService(storage=storage)
    try:
        deals_overview = deals_service.get_company_deals_overview(entity_id)
    except Exception:
        deals_overview = None

    return templates.TemplateResponse(
        request,
        "companies/detail.html",
        {
            "request": request,
            "entity": entity,
            "legal_identifiers": legal_identifiers,
            "facts": facts,
            "evidence": evidence,
            "relationships": relationships,
            "deals_overview": deals_overview,
        },
    )
