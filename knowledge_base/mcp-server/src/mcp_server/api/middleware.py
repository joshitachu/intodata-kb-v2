"""FastAPI middleware for the MCP server.

Mirrors the small IP allowlist helper used by the RAG backend. Duplicated here
to keep the mcp-server package self-contained (no cross-package imports).
"""

from __future__ import annotations

import ipaddress
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


def _parse_allowlist(raw: str) -> list[ipaddress._BaseNetwork]:
    networks: list[ipaddress._BaseNetwork] = []
    for entry in (e.strip() for e in raw.split(",")):
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.warning("Ignoring invalid MCP_ALLOWED_IPS entry: %r", entry)
    return networks


class IPAllowlistMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, allowed_ips: str) -> None:
        super().__init__(app)
        self._networks = _parse_allowlist(allowed_ips)
        self._enabled = bool(self._networks)
        if self._enabled:
            logger.info(
                "MCP IP allowlist enabled with %d network(s)", len(self._networks)
            )

    async def dispatch(self, request: Request, call_next):
        if not self._enabled:
            return await call_next(request)

        client_host = request.client.host if request.client else None
        if client_host:
            try:
                client_ip = ipaddress.ip_address(client_host)
            except ValueError:
                client_ip = None
            if client_ip is not None and any(
                client_ip in net for net in self._networks
            ):
                return await call_next(request)

        logger.warning("MCP IP allowlist denied request from %s", client_host)
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})
