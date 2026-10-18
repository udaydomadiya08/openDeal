"""Dashboard UI routes."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard_view(request: Request):
    templates = request.app.state.templates
    storage = request.app.state.storage

    # Fetch real stats from storage
    stats = storage.get_system_stats()

    # Fetch recent records for preview tables
    recent_entities = storage.list_entities(limit=5)
    recent_rounds = storage.search_funding_rounds(limit=5)
    recent_transactions = storage.search_transactions(limit=5)

    recent_deals = []
    for r in recent_rounds:
        recent_deals.append({
            "event_id": r.round_id,
            "event_type": r.round_type.value if hasattr(r.round_type, "value") else str(r.round_type),
            "category": "Funding Round",
            "target_name": r.company_name or r.company_id,
            "company_id": r.company_id,
            "date": r.announced_date,
            "amount": r.disclosed_amount,
            "currency": r.currency.value if hasattr(r.currency, "value") else str(r.currency),
            "status": "ANNOUNCED",
            "link": f"/deals/rounds/{r.round_id}",
        })

    for tx in recent_transactions:
        recent_deals.append({
            "event_id": tx.transaction_id,
            "event_type": tx.transaction_type.value if hasattr(tx.transaction_type, "value") else str(tx.transaction_type),
            "category": "M&A",
            "target_name": tx.target_name or tx.target_id,
            "company_id": tx.target_id,
            "date": tx.announced_date,
            "amount": tx.disclosed_value,
            "currency": tx.currency.value if hasattr(tx.currency, "value") else str(tx.currency),
            "status": tx.status.value if hasattr(tx.status, "value") else str(tx.status),
            "link": f"/deals/transactions/{tx.transaction_id}",
        })

    # Sort combined recent deals by date descending if possible
    recent_deals = sorted(
        recent_deals,
        key=lambda d: str(d["date"] or ""),
        reverse=True,
    )[:5]

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "stats": stats,
            "total_entities": stats.get("entities", 0),
            "total_facts": stats.get("facts", 0),
            "total_funding_rounds": stats.get("funding_rounds", 0),
            "total_transactions": stats.get("transactions", 0),
            "total_target_lists": stats.get("target_lists", 0),
            "total_research_briefs": stats.get("research_briefs", 0),
            "recent_entities": recent_entities,
            "recent_deals": recent_deals,
        },
    )
