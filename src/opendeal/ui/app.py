"""FastAPI Application Factory for OpenDeal Local UI."""

import os
from pathlib import Path
from typing import Optional, Set
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.templating import Jinja2Templates

from opendeal import __version__
from opendeal.config.paths import resolve_database_path
from opendeal.config.settings import settings
from opendeal.storage.sqlite import SQLiteStorage
from opendeal.ui.routes import (
    companies,
    dashboard,
    deals,
    discovery,
    facts,
    registries,
    research,
    system,
    valuation,
    workflows,
)

UI_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = UI_DIR / "templates"
STATIC_DIR = UI_DIR / "static"


class PayloadTooLargeError(BaseException):
    """Exception raised when an inbound request body exceeds the configured limit."""


def parse_host_authority(host_header: str) -> Optional[str]:
    """Parse and validate Host authority, returning canonical hostname or None if malformed."""
    if not host_header:
        return ""
    host_header = host_header.strip()
    if not host_header:
        return ""

    # IPv6 bracket notation, e.g. [::1] or [::1]:8000
    if host_header.startswith("["):
        end_bracket = host_header.find("]")
        if end_bracket == -1:
            return None  # Malformed IPv6 literal without closing bracket
        ipv6_host = host_header[1:end_bracket].strip().lower()
        remainder = host_header[end_bracket + 1:]
        if remainder:
            if not remainder.startswith(":"):
                return None  # Malformed characters after bracket
            port_str = remainder[1:]
            if not port_str.isdigit() or not (1 <= int(port_str) <= 65535):
                return None  # Invalid port
        return ipv6_host

    # Standard host[:port]
    parts = host_header.split(":")
    if len(parts) == 1:
        return parts[0].strip().lower()
    elif len(parts) == 2:
        host, port_str = parts[0].strip().lower(), parts[1].strip()
        if not port_str.isdigit() or not (1 <= int(port_str) <= 65535):
            return None  # Invalid port
        return host
    else:
        # Multiple colons without brackets is malformed IPv6 or syntax error
        return None


def is_host_allowed(host: str, allowed_hosts: Set[str]) -> bool:
    """Check if host matches any allowed host or wildcard/subdomain pattern."""
    if not host:
        return False
    host = host.lower().strip()
    if host in allowed_hosts:
        return True
    for allowed in allowed_hosts:
        if allowed.startswith("."):
            if host.endswith(allowed) or host == allowed[1:]:
                return True
        elif allowed.startswith("*."):
            suffix = allowed[1:]
            if host.endswith(suffix):
                return True
    return False


class SecurityHeadersMiddleware:
    """Pure ASGI middleware enforcing secure institutional HTTP response headers, host policy, and streaming limits."""

    ALLOWED_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}
    MAX_REQUEST_BODY_BYTES = 10_000_000  # 10 MB

    def __init__(self, app, allowed_hosts: Optional[Set[str]] = None, demo_mode: bool = False) -> None:
        self.app = app
        self.demo_mode = demo_mode
        self.allowed_hosts = set(self.ALLOWED_LOCAL_HOSTS)
        if allowed_hosts:
            self.allowed_hosts.update(h.strip().lower() for h in allowed_hosts if h.strip())

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 0. Health check fast-path (unauthenticated platform probe)
        path = scope.get("path", "")
        if path in ("/healthz", "/api/health"):
            import json
            body = json.dumps({
                "status": "healthy",
                "version": __version__,
                "mode": "demo" if self.demo_mode else "local",
            }).encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-cache"),
                ],
            })
            await send({
                "type": "http.response.body",
                "body": body,
            })
            return

        headers = dict(scope.get("headers", []))
        host_header = headers.get(b"host", b"").decode("latin-1")

        # 1. Host header validation to prevent DNS rebinding attacks (handles ports and IPv6 brackets)
        parsed_host = parse_host_authority(host_header)
        if parsed_host is None or not is_host_allowed(parsed_host, self.allowed_hosts):
            response_body = b"403 Forbidden: Host header rejected by local security policy."
            await send({
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"text/plain"),
                    (b"content-length", str(len(response_body)).encode("ascii")),
                ],
            })
            await send({
                "type": "http.response.body",
                "body": response_body,
            })
            return

        # 2. Content-Length size protection against memory exhaustion
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                cl = int(content_length.decode("ascii"))
                if cl > self.MAX_REQUEST_BODY_BYTES:
                    response_body = b"413 Payload Too Large: Request body exceeds 10MB limit."
                    await send({
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [
                            (b"content-type", b"text/plain"),
                            (b"content-length", str(len(response_body)).encode("ascii")),
                        ],
                    })
                    await send({
                        "type": "http.response.body",
                        "body": response_body,
                    })
                    return
            except ValueError:
                pass

        # 3. CSRF / Cross-Origin state-changing request protection
        method = scope.get("method", "GET").upper()
        if method in ("POST", "PUT", "DELETE", "PATCH"):
            import urllib.parse
            origin = headers.get(b"origin", b"").decode("latin-1")
            if origin:
                parsed_origin = urllib.parse.urlparse(origin)
                origin_host = (parsed_origin.hostname or "").lower()
                if origin_host and not is_host_allowed(origin_host, self.allowed_hosts):
                    response_body = b"403 Forbidden: Cross-Origin state-changing requests are prohibited."
                    await send({
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [
                            (b"content-type", b"text/plain"),
                            (b"content-length", str(len(response_body)).encode("ascii")),
                        ],
                    })
                    await send({
                        "type": "http.response.body",
                        "body": response_body,
                    })
                    return
            else:
                referer = headers.get(b"referer", b"").decode("latin-1")
                if referer:
                    parsed_ref = urllib.parse.urlparse(referer)
                    ref_host = (parsed_ref.hostname or "").lower()
                    if ref_host and not is_host_allowed(ref_host, self.allowed_hosts):
                        response_body = b"403 Forbidden: Cross-Origin state-changing requests are prohibited."
                        await send({
                            "type": "http.response.start",
                            "status": 403,
                            "headers": [
                                (b"content-type", b"text/plain"),
                                (b"content-length", str(len(response_body)).encode("ascii")),
                            ],
                        })
                        await send({
                            "type": "http.response.body",
                            "body": response_body,
                        })
                        return

        # 4. Streamed / chunked body meter to prevent unbounded streaming memory exhaustion
        total_received = 0
        response_started = False

        async def limited_receive():
            nonlocal total_received
            message = await receive()
            if message.get("type") == "http.request":
                chunk = message.get("body", b"")
                total_received += len(chunk)
                if total_received > self.MAX_REQUEST_BODY_BYTES:
                    raise PayloadTooLargeError("Request body exceeds maximum allowed size")
            return message

        async def send_wrapper(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
                sec_headers = [
                    (b"x-frame-options", b"DENY"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-xss-protection", b"1; mode=block"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"content-security-policy", b"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self';"),
                ]
                existing = list(message.get("headers", []))
                existing_names = {h[0].lower() for h in existing}
                for name, val in sec_headers:
                    if name not in existing_names:
                        existing.append((name, val))
                message["headers"] = existing
            await send(message)

        try:
            await self.app(scope, limited_receive, send_wrapper)
        except PayloadTooLargeError:
            if not response_started:
                response_body = b"413 Payload Too Large: Request body exceeds 10MB limit."
                await send({
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [
                        (b"content-type", b"text/plain"),
                        (b"content-length", str(len(response_body)).encode("ascii")),
                        (b"connection", b"close"),
                    ],
                })
                await send({
                    "type": "http.response.body",
                    "body": response_body,
                })


def create_app(
    storage: Optional[SQLiteStorage] = None,
    db_path: Optional[str] = None,
    demo_mode: Optional[bool] = None,
    allowed_hosts: Optional[Set[str]] = None,
) -> FastAPI:
    """Create and configure the OpenDeal FastAPI application."""
    if demo_mode is None:
        demo_mode = os.environ.get("OPENDEAL_DEMO_MODE", "").strip().lower() in ("1", "true", "yes")

    # Determine allowed hosts for security middleware
    effective_allowed_hosts: Set[str] = set()
    if allowed_hosts:
        effective_allowed_hosts.update(allowed_hosts)
    env_hosts = os.environ.get("OPENDEAL_ALLOWED_HOSTS", "").strip()
    if env_hosts:
        effective_allowed_hosts.update(h.strip().lower() for h in env_hosts.split(",") if h.strip())
    render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip().lower()
    if render_host:
        effective_allowed_hosts.add(render_host)
    if demo_mode:
        effective_allowed_hosts.add(".onrender.com")

    app = FastAPI(
        title="OpenDeal Financial Intelligence Platform" + (" (Demo Mode)" if demo_mode else ""),
        version=__version__,
        docs_url=None,  # Disabled in production UI for security
        redoc_url=None,
    )

    # Add security headers and host validation middleware
    app.add_middleware(
        SecurityHeadersMiddleware,
        allowed_hosts=effective_allowed_hosts,
        demo_mode=demo_mode,
    )

    # Initialize storage if not injected
    if storage is None:
        if demo_mode:
            if db_path is None and "OPENDEAL_DATABASE_PATH" not in os.environ:
                import tempfile
                actual_db = str(Path(tempfile.gettempdir()) / "opendeal_demo.db")
            else:
                actual_db = resolve_database_path(db_path)
            storage = SQLiteStorage(database_path=actual_db)
            storage.initialize()
            from opendeal.demo.dataset import seed_demo_database
            seed_demo_database(storage)
        else:
            actual_db = resolve_database_path(db_path)
            storage = SQLiteStorage(database_path=actual_db)
            storage.initialize()

    # Mount static assets
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Configure template engine
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.globals["app_version"] = __version__
    templates.env.globals["demo_mode"] = demo_mode

    # Attach storage and templates to app state
    app.state.storage = storage
    app.state.templates = templates
    app.state.demo_mode = demo_mode

    # Register routers
    app.include_router(dashboard.router)
    app.include_router(companies.router)
    app.include_router(facts.router)
    app.include_router(valuation.router)
    app.include_router(research.router)
    app.include_router(discovery.router)
    app.include_router(deals.router)
    app.include_router(workflows.router)
    app.include_router(registries.router)
    app.include_router(system.router)

    # Health check route
    @app.get("/healthz", response_class=HTMLResponse)
    async def health_check_route():
        from fastapi.responses import JSONResponse
        return JSONResponse(
            content={
                "status": "healthy",
                "version": __version__,
                "mode": "demo" if demo_mode else "local",
            }
        )

    # Convenience alias redirect for single /company/{entity_id} -> /companies/{entity_id}
    @app.get("/company/{entity_id}")
    async def company_redirect_alias(entity_id: str):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"/companies/{entity_id}", status_code=302)

    # Global 404 Exception Handler
    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc: HTTPException):
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "request": request,
                "error_code": 404,
                "error_title": "Page Not Found",
                "error_message": exc.detail if exc.detail != "Not Found" else "The requested OpenDeal view or resource does not exist.",
            },
            status_code=404,
        )

    # Global 500 / Unhandled Exception Handler
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        # Prevent stack trace leakage in institutional UI
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "request": request,
                "error_code": 500,
                "error_title": "Internal System Error",
                "error_message": "An unexpected error occurred while processing the financial request. Internal details have been omitted for security.",
            },
            status_code=500,
        )

    return app
