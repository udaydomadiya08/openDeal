"""Deals and Transaction Intelligence UI routes integrating Phase 7 private-market intelligence."""

from typing import Optional
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from opendeal.deals.service import DealsIntelligenceService

router = APIRouter(prefix="/deals")


@router.get("", response_class=HTMLResponse)
async def deals_index_view(
    request: Request,
    deal_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    templates = request.app.state.templates
    storage = request.app.state.storage

    deals_service = DealsIntelligenceService(storage=storage)
    rounds = storage.search_funding_rounds(limit=100)
    transactions = storage.search_transactions(limit=100)

    # Count recorded conflicts
    conflict_count = 0
    try:
        cursor = storage.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM deal_conflicts")
        conflict_count = cursor.fetchone()[0]
    except Exception:
        conflict_count = 0

    return templates.TemplateResponse(
        request,
        "deals/index.html",
        {
            "request": request,
            "rounds": rounds,
            "transactions": transactions,
            "conflict_count": conflict_count,
        },
    )


@router.get("/rounds/{round_id}", response_class=HTMLResponse)
async def round_detail_view(request: Request, round_id: str):
    templates = request.app.state.templates
    storage = request.app.state.storage

    funding_round = storage.get_funding_round(round_id)
    if not funding_round:
        return templates.TemplateResponse(
        request,
        "error.html",
            {
                "request": request,
                "error_code": 404,
                "error_title": "Funding Round Not Found",
                "error_message": f"Funding round with ID '{round_id}' was not found in storage.",
            },
            status_code=404,
        )

    participants = storage.get_funding_participants_for_round(round_id)
    company = storage.get_entity(funding_round.company_id)
    if company and not getattr(funding_round, "company_name", None):
        funding_round.company_name = company.canonical_name

    return templates.TemplateResponse(
        request,
        "deals/round_detail.html",
        {
            "request": request,
            "round": funding_round,
            "company": company,
            "participants": participants,
        },
    )


@router.get("/transactions/{transaction_id}", response_class=HTMLResponse)
async def transaction_detail_view(request: Request, transaction_id: str):
    templates = request.app.state.templates
    storage = request.app.state.storage

    tx = storage.get_transaction(transaction_id)
    if not tx:
        return templates.TemplateResponse(
        request,
        "error.html",
            {
                "request": request,
                "error_code": 404,
                "error_title": "Transaction Not Found",
                "error_message": f"M&A transaction with ID '{transaction_id}' was not found in storage.",
            },
            status_code=404,
        )

    return templates.TemplateResponse(
        request,
        "deals/transaction_detail.html",
        {
            "request": request,
            "tx": tx,
        },
    )


@router.get("/timeline/{entity_id}", response_class=HTMLResponse)
async def company_timeline_view(request: Request, entity_id: str):
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
                "error_message": f"No entity with ID '{entity_id}' found in storage.",
            },
            status_code=404,
        )

    deals_service = DealsIntelligenceService(storage=storage)
    overview = deals_service.get_company_deals_overview(entity_id)

    events = []
    # pyrefly: ignore[missing-attribute]
    for item in overview.timeline:
        events.append({
            "date": item.event_date,
            "event_type": item.event_type,
            "title": item.summary,
            "description": f"Status: {item.status}",
            "amount": f"{item.currency or 'USD'} {item.disclosed_amount:,.0f}" if item.disclosed_amount else None,
            "link": f"/deals/{'rounds' if 'round' in item.event_type.lower() else 'transactions'}/{item.event_id}",
        })

    return templates.TemplateResponse(
        request,
        "deals/timeline.html",
        {
            "request": request,
            "entity": entity,
            "timeline_events": events,
        },
    )


@router.get("/conflicts", response_class=HTMLResponse)
async def deal_conflicts_view(request: Request):
    templates = request.app.state.templates
    storage = request.app.state.storage

    conflicts = []
    try:
        cursor = storage.conn.cursor()
        cursor.execute("SELECT * FROM deal_conflicts ORDER BY created_at DESC LIMIT 100")
        for row in cursor.fetchall():
            conflicts.append({
                "event_id": row["target_event_id"],
                "field_name": row["field_name"],
                "value_a": f"{row['observed_value_a']} (Source: {row['source_a_id']})",
                "value_b": f"{row['observed_value_b']} (Source: {row['source_b_id']})",
                "conflict_type": row["event_category"],
                "notes": row["resolution_notes"] or row["resolution_status"],
            })
    except Exception:
        conflicts = []

    return templates.TemplateResponse(
        request,
        "deals/conflicts.html",
        {
            "request": request,
            "conflicts": conflicts,
        },
    )
