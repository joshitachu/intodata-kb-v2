from __future__ import annotations

import re
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.api.metrics import http_request_duration_seconds, http_requests_total

# Collapse numeric path segments so /documents/123 and /documents/456 share a label.
_NUMERIC_RE = re.compile(r"/\d+")


def _normalize_path(path: str) -> str:
    return _NUMERIC_RE.sub("/{id}", path)


class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = _normalize_path(request.url.path)
        method = request.method
        start = time.perf_counter()
        try:
            response = await call_next(request)
            status = str(response.status_code)
        except Exception:
            status = "500"
            raise
        finally:
            duration = time.perf_counter() - start
            http_requests_total.labels(method=method, path=path, status_code=status).inc()
            http_request_duration_seconds.labels(method=method, path=path).observe(duration)
        return response
