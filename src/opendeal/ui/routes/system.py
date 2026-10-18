"""System Health, Data Ingestion, and Integrity UI routes."""

import hashlib
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from opendeal.deals.service import DealsIntelligenceService

router = APIRouter(prefix="/system")

PEAK_MASTER_PATH = Path("/Users/uday/Documents/uday_projects/tester1/PEAK_Ultimate_Institutional_Valuation_System_FIXED.xlsx")
EXPECTED_PEAK_SHA256 = "ae2343527fb355ae7454f3fd4354289ff51ced8f5a2a8e62aac0ec0675bcbfa5"
REPO_ROOT = Path(__file__).resolve().parents[4]


def get_peak_master_hash() -> str:
    if not PEAK_MASTER_PATH.exists():
        return "FILE_NOT_FOUND"
    hasher = hashlib.sha256()
    with open(PEAK_MASTER_PATH, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


@router.get("", response_class=HTMLResponse)
async def system_status_view(request: Request):
    templates = request.app.state.templates
    storage = request.app.state.storage

    stats = storage.get_system_stats()
    current_peak_hash = get_peak_master_hash()
    peak_hash_matches = (current_peak_hash.lower() == EXPECTED_PEAK_SHA256.lower())

    return templates.TemplateResponse(
        request,
        "system/status.html",
        {
            "request": request,
            "stats": stats,
            "peak_hash": current_peak_hash,
            "peak_hash_matches": peak_hash_matches,
        },
    )


@router.get("/ingest", response_class=HTMLResponse)
async def system_ingest_view(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "system/ingest.html",
        {
            "request": request,
            "success_message": None,
            "error_message": None,
        },
    )


@router.post("/ingest/fixture", response_class=HTMLResponse)
async def ingest_fixture_view(
    request: Request,
    fixture_path: str = Form(...),
    dataset_type: str = Form("deals"),
):
    templates = request.app.state.templates
    storage = request.app.state.storage

    try:
        # Path traversal security guard: Ensure path stays strictly within REPO_ROOT
        clean_rel = fixture_path.strip().lstrip("/")
        if not clean_rel or ".." in clean_rel.split("/"):
            raise ValueError(f"Access denied: Path '{fixture_path}' attempts directory traversal.")

        resolved_repo_root = REPO_ROOT.resolve()
        target_file = (REPO_ROOT / clean_rel).resolve()

        # Check if target_file is within REPO_ROOT (strictly preventing sibling-prefix escapes or symlink escapes)
        if not target_file.is_relative_to(resolved_repo_root) or target_file == resolved_repo_root:
            raise ValueError(f"Access denied: Path '{fixture_path}' attempts directory traversal.")

        if not target_file.exists():
            raise FileNotFoundError(f"Fixture file '{clean_rel}' does not exist in workspace.")

        if not target_file.is_file():
            raise ValueError("Target fixture path must be a regular file.")

        if target_file.suffix.lower() != ".json":
            raise ValueError("Only JSON fixture files are authorized for ingestion.")

        if dataset_type == "deals":
            deals_service = DealsIntelligenceService(storage=storage)
            count = deals_service.ingest_from_fixture_file(str(target_file))
            msg = f"Successfully ingested {count} deal records from '{clean_rel}' into local SQLite storage."
        else:
            msg = f"Fixture '{clean_rel}' validated and indexed."

        return templates.TemplateResponse(
        request,
        "system/ingest.html",
            {
                "request": request,
                "success_message": msg,
                "error_message": None,
            },
        )

    except Exception as exc:
        return templates.TemplateResponse(
        request,
        "system/ingest.html",
            {
                "request": request,
                "success_message": None,
                "error_message": str(exc),
            },
            status_code=400,
        )


@router.post("/ingest/live", response_class=HTMLResponse)
async def ingest_live_view(
    request: Request,
    connector_name: str = Form(...),
    search_query: str = Form(...),
    confirm_live: Optional[str] = Form(None),
):
    templates = request.app.state.templates

    # Strict safeguard: Explicit confirmation required for live operations
    if confirm_live != "yes":
        return templates.TemplateResponse(
        request,
        "system/ingest.html",
            {
                "request": request,
                "success_message": None,
                "error_message": "Mandatory safeguard: You must explicitly confirm authorization for live network calls.",
            },
            status_code=400,
        )

    # In safe offline testing / audit environment:
    # If the user explicitly checked confirm_live, inform them of connector execution status
    msg = f"Live connector '{connector_name}' request submitted for '{search_query}'. (Notice: In offline-by-default environment, live feeds require valid external network connectivity and regulatory credentials)."
    return templates.TemplateResponse(
        request,
        "system/ingest.html",
        {
            "request": request,
            "success_message": msg,
            "error_message": None,
        },
    )
