"""Per-request ID middleware: generates a UUID4 for each request and propagates it."""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.logging_config import _request_id_ctx


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Set a request-scoped ID on every inbound request.

    Reads X-Request-ID from the incoming headers so upstream proxies or clients
    can supply their own trace ID; falls back to a fresh UUID4 otherwise.
    The ID is stored in a context var so all log calls within the request lifecycle
    pick it up automatically via _RequestIDFilter.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = _request_id_ctx.set(req_id)
        try:
            response: Response = await call_next(request)
            response.headers["X-Request-ID"] = req_id
            return response
        finally:
            _request_id_ctx.reset(token)
