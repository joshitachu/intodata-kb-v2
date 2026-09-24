"""Pin outbound connector requests to the public address that passed validation.

Checking DNS and then letting the HTTP client resolve again leaves a rebinding
window. This transport keeps the original Host/SNI for TLS verification while
connecting to the already-checked address. Redirects remain disabled by callers.
"""

from __future__ import annotations

import ipaddress

import httpx


class PinnedHostTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        *,
        hostname: str,
        address: str,
        delegate: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not ipaddress.ip_address(address).is_global:
            raise ValueError("Connector address must be public")
        self._hostname = hostname.lower()
        self._address = address
        self._delegate = delegate or httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host.lower() != self._hostname or request.url.scheme != "https":
            raise ValueError("Connector request escaped its approved host")
        request.headers["host"] = self._hostname
        request.extensions["sni_hostname"] = self._hostname
        request.url = request.url.copy_with(host=self._address)
        return await self._delegate.handle_async_request(request)

    async def aclose(self) -> None:
        await self._delegate.aclose()
