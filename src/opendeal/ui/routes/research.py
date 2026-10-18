"""Research Workspace UI routes integrating Phase 5 evidence-grounded research generation."""

from typing import Optional
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from opendeal.research import (
    ResearchBriefGenerator,
    ResearchScope,
)

router = APIRouter(prefix="/research")


@router.get("", response_class=HTMLResponse)
async def research_index_view(
    request: Request,
    entity_id: Optional[str] = None,
):
    templates = request.app.state.templates
    storage = request.app.state.storage

    entities = storage.list_entities(limit=200)

    # Collect recent briefs across entities
    recent_briefs = []
    try:
        cursor = storage.conn.cursor()
        cursor.execute("SELECT research_id, entity_id, brief_title, claims_json, created_at FROM research_briefs ORDER BY created_at DESC LIMIT 10")
        import json
        for row in cursor.fetchall():
            claims = json.loads(row["claims_json"]) if row["claims_json"] else []
            recent_briefs.append({
                "research_id": row["research_id"],
                "entity_id": row["entity_id"],
                "brief_title": row["brief_title"],
                "claims_count": len(claims),
                "created_at": row["created_at"],
            })
    except Exception:
        recent_briefs = []

    return templates.TemplateResponse(
        request,
        "research/index.html",
        {
            "request": request,
            "entities": entities,
            "selected_entity_id": entity_id or "",
            "recent_briefs": recent_briefs,
            "error": None,
        },
    )


@router.post("/generate", response_class=HTMLResponse)
async def research_generate_view(
    request: Request,
    entity_id: str = Form(...),
    include_financials: Optional[str] = Form(None),
    include_relationships: Optional[str] = Form(None),
    include_valuations: Optional[str] = Form(None),
    max_evidence_items: int = Form(25),
):
    templates = request.app.state.templates
    storage = request.app.state.storage

    entity = storage.get_entity(entity_id)
    if not entity:
        entities = storage.list_entities(limit=200)
        return templates.TemplateResponse(
        request,
        "research/index.html",
            {
                "request": request,
                "entities": entities,
                "selected_entity_id": entity_id,
                "recent_briefs": [],
                "error": f"Entity '{entity_id}' not found in storage.",
            },
            status_code=404,
        )

    try:
        scope = ResearchScope(
            entity_id=entity_id,
            include_financials=(include_financials == "yes"),
            include_relationships=(include_relationships == "yes"),
            include_valuations=(include_valuations == "yes"),
            max_evidence_items=max_evidence_items,
        )

        generator = ResearchBriefGenerator(storage=storage)
        brief = generator.generate_brief(scope)

        # Persist brief to SQLite
        storage.save_research_brief(brief.model_dump())

        return RedirectResponse(
            url=f"/research/briefs/{brief.research_id}",
            status_code=303,
        )

    except Exception as exc:
        entities = storage.list_entities(limit=200)
        return templates.TemplateResponse(
        request,
        "research/index.html",
            {
                "request": request,
                "entities": entities,
                "selected_entity_id": entity_id,
                "recent_briefs": [],
                "error": f"Synthesis failure: {str(exc)}",
            },
            status_code=400,
        )


@router.get("/briefs/{research_id}", response_class=HTMLResponse)
async def research_brief_view(request: Request, research_id: str):
    templates = request.app.state.templates
    storage = request.app.state.storage

    brief_dict = storage.get_research_brief(research_id)
    if not brief_dict:
        return templates.TemplateResponse(
        request,
        "error.html",
            {
                "request": request,
                "error_code": 404,
                "error_title": "Research Brief Not Found",
                "error_message": f"No synthesized research brief with identifier '{research_id}' exists.",
            },
            status_code=404,
        )

    return templates.TemplateResponse(
        request,
        "research/brief.html",
        {
            "request": request,
            "brief": brief_dict,
        },
    )
