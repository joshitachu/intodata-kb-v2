"""CSRF protection via the double-submit cookie pattern.

Flow:
1. Auth routes (login, refresh) set a non-httpOnly `csrf_token` cookie alongside
   the httpOnly JWT cookies. JavaScript reads it.
2. This middleware checks that every state-changing request (POST/PUT/PATCH/DELETE)
   carries an `X-CSRF-Token` header whose value matches the `csrf_token` cookie.
3. Safe methods (GET/HEAD/OPTIONS) and explicitly exempt paths are skipped.

Disable in test environments by setting CSRF_ENABLED=false.
"""

from __future__ import annotations

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

_EXEMPT_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/auth/request-access",
    "/api/v1/auth/refresh",
    "/api/v1/auth/otp/send",
    "/api/v1/auth/otp/verify",
    "/api/v1/auth/verify-mfa",
    "/api/v1/auth/invite/start",
    "/api/v1/auth/invite/accept",
    "/api/v1/auth/invite/validate",
    "/api/v1/auth/bootstrap-admin",
    "/api/v1/auth/sso",
    "/api/v1/auth/sso/callback",
    "/metrics",
    "/health",
}

# Prefix-based exemptions -- no browser CSRF cookie exists on these paths:
#   /api/v1/ingest/   -- server-to-server calls from mcp-server, no browser cookie
#   /api/v1/usage/    -- server-to-server calls, no browser cookie
#   /api/v1/labs/mcp  -- the labs MCP tool bridge. Called by the opencode-labs
#       container, authenticated solely by a per-conversation bearer token that
#       the server minted; it accepts no cookie and derives no identity from one,
#       so there is no cross-site request to forge. Leaving it behind CSRF made
#       every tool call 403, which OpenCode surfaces as MCP `needs_auth`.
#   /api/v1/runtime/tools and /api/v1/runtime/elastic/probe -- the runtime
#       evidence surface a documentation run queries while it writes. Called by
#       a script inside the mcp-server staging tree, authenticated solely by the
#       machine ingest key (verify_machine_topology_ingest_api_key); like the
#       labs bridge above it accepts no cookie and derives no identity from one,
#       so there is nothing for a cross-site request to forge. Their GET
#       siblings (/runtime/doc-evidence, /runtime/elastic-aggregates) never hit
#       this middleware because GET is a safe method -- which is exactly why
#       this gap only appears once the surface gains its first POST.
_EXEMPT_PREFIXES = (
    "/api/v1/ingest/",
    "/api/v1/usage/",
    "/api/v1/labs/mcp",
    "/api/v1/runtime/tools",
    "/api/v1/runtime/elastic/probe",
)


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if request.method not in _SAFE_METHODS and path not in _EXEMPT_PATHS and not any(
            path.startswith(p) for p in _EXEMPT_PREFIXES
        ):
            csrf_header = request.headers.get("X-CSRF-Token", "")
            csrf_cookie = request.cookies.get("csrf_token", "")
            if not csrf_header or not csrf_cookie or csrf_header != csrf_cookie:
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": {
                            "code": "csrf_invalid",
                            "message": "CSRF token mismatch or missing",
                        }
                    },
                )
        return await call_next(request)
