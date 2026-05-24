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
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.errors import api_error

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
#   /api/v1/ingest/ -- server-to-server calls from mcp-server, no browser cookie
#   /api/v1/usage/  -- server-to-server calls, no browser cookie
_EXEMPT_PREFIXES = (
    "/api/v1/ingest/",
    "/api/v1/usage/",
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
                raise api_error(403, "csrf_invalid", "CSRF token mismatch or missing")
        return await call_next(request)
