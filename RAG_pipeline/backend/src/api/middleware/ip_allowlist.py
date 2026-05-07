"""IP allowlist middleware.

No-op when `allowed_ips` is empty, otherwise rejects any request whose real
client IP is not in the allowlist. Supports bare IPv4/IPv6 addresses and CIDR
ranges (e.g. ``10.0.0.0/8``).

The middleware reads `request.client.host`, so it must be registered AFTER
uvicorn's ProxyHeadersMiddleware (or the proxy's IP will be checked instead of
the real client's).
"""

from __future__ import annotations

import ipaddress
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


def _parse_allowlist(raw: str) -> list[ipaddress._BaseNetwork]:
    """Parse a comma-separated allowlist into ip_network objects.

    Bare IPs are treated as /32 or /128 networks so `in` checks work uniformly.
    Invalid entries are logged and dropped.
    """
    networks: list[ipaddress._BaseNetwork] = []
    for entry in (e.strip() for e in raw.split(",")):
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.warning("Ignoring invalid ALLOWED_IPS entry: %r", entry)
    return networks


class IPAllowlistMiddleware(BaseHTTPMiddleware):
    """Reject requests whose client IP is not in the allowlist."""

    def __init__(self, app, allowed_ips: str) -> None:
        super().__init__(app)
        self._networks = _parse_allowlist(allowed_ips)
        self._enabled = bool(self._networks)
        if self._enabled:
            logger.info(
                "IP allowlist enabled with %d network(s)", len(self._networks)
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

        logger.warning("IP allowlist denied request from %s", client_host)
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})
