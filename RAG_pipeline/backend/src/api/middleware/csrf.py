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

# Paths that set the CSRF cookie (login-like flows) — exempt from the check
# because the cookie doesn't exist yet on the first call.
_EXEMPT_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/otp/send",
    "/api/v1/auth/otp/verify",
    "/api/v1/auth/invite/accept",
    "/api/v1/auth/bootstrap-admin",
    "/api/v1/auth/sso",
    "/api/v1/auth/sso/callback",
    "/metrics",
    "/health",
}


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method not in _SAFE_METHODS and request.url.path not in _EXEMPT_PATHS:
            csrf_header = request.headers.get("X-CSRF-Token", "")
            csrf_cookie = request.cookies.get("csrf_token", "")
            if not csrf_header or not csrf_cookie or csrf_header != csrf_cookie:
                raise api_error(403, "csrf_invalid", "CSRF token mismatch or missing")
        return await call_next(request)
