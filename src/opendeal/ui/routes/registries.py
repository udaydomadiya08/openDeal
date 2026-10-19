"""Global Securities Regulators & Corporate Registries UI routes."""

from typing import Optional
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from opendeal.registries import get_global_registry

router = APIRouter(prefix="/registries")


@router.get("", response_class=HTMLResponse)
async def registries_directory_view(
    request: Request,
    region: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    resolve_id: Optional[str] = Query(default=None),
):
    templates = request.app.state.templates
    reg = get_global_registry()

    all_regions = reg.list_regions()

    # Search / Filter
    if q:
        authorities = reg.search(q)
    elif region:
        authorities = reg.list_by_region(region)
    else:
        authorities = reg.list_all()

    # Resolver test
    resolution = None
    if resolve_id and resolve_id.strip():
        resolution = reg.resolve_filing_source(resolve_id.strip())

    return templates.TemplateResponse(
        request,
        "registries/index.html",
        {
            "request": request,
            "active_tab": "registries",
            "authorities": authorities,
            "regions": all_regions,
            "selected_region": region or "",
            "search_query": q or "",
            "resolve_id": resolve_id or "",
            "resolution": resolution,
            "total_count": len(authorities),
        },
    )
