"""Hardened async transport shared by every connector provider.

The wrapper intentionally exposes only JSON/byte responses with bounded reads;
providers cannot obtain a raw unrestricted httpx client from this module.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx

from .errors import ConnectorError, sanitize_provider_error


_MAX_RETRIES = 2
_TRANSIENT = {429, 500, 502, 503, 504}


@dataclass(frozen=True, slots=True)
class OutboundPolicy:
    require_https: bool = True
    allow_private_hosts: frozenset[str] = field(default_factory=frozenset)
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 15.0
    total_timeout_seconds: float = 20.0
    max_response_bytes: int = 262_144
    ca_bundle: str | None = None
    custom_ca_pem: str | None = None

    def tls_verify(self) -> bool | str | ssl.SSLContext:
        if self.custom_ca_pem:
            context = ssl.create_default_context()
            try:
                context.load_verify_locations(cadata=self.custom_ca_pem)
            except ssl.SSLError as exc:
                raise ConnectorError("invalid_ca", "Custom CA certificate is invalid") from exc
            return context
        return self.ca_bundle or True


def normalize_endpoint(url: str, *, require_https: bool = True) -> str:
    try:
        parsed = urlsplit(url.strip())
    except ValueError as exc:
        raise ConnectorError("invalid_endpoint", "Connector endpoint is invalid") from exc
    if parsed.scheme not in ({"https"} if require_https else {"http", "https"}):
        raise ConnectorError("invalid_endpoint", "Connector endpoint must use HTTPS")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ConnectorError("invalid_endpoint", "Connector endpoint must not include credentials")
    if parsed.query or parsed.fragment:
        raise ConnectorError("invalid_endpoint", "Connector endpoint must not include query or fragment")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _is_always_denied(address: ipaddress._BaseAddress) -> bool:
    return bool(
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
        or (isinstance(address, ipaddress.IPv6Address) and address.is_site_local)
    )


def _validate_address(address: str, hostname: str, policy: OutboundPolicy) -> None:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError as exc:
        raise ConnectorError("dns_failed", "Connector endpoint could not be resolved") from exc
    # IPs used by cloud metadata services are link-local and therefore covered.
    if _is_always_denied(ip):
        raise ConnectorError("endpoint_denied", "Connector endpoint address is not allowed")
    if ip.is_private and hostname.casefold() not in {h.casefold() for h in policy.allow_private_hosts}:
        raise ConnectorError("private_host_denied", "Private connector endpoint is not approved")


async def _validated_endpoint_address(url: str, policy: OutboundPolicy) -> tuple[str, str, str]:
    normalized = normalize_endpoint(url, require_https=policy.require_https)
    parsed = urlsplit(normalized)
    hostname = parsed.hostname
    assert hostname is not None
    try:
        infos = await asyncio.get_running_loop().run_in_executor(
            None, lambda: socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        )
    except socket.gaierror as exc:
        raise ConnectorError("dns_failed", "Connector endpoint could not be resolved") from exc
    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise ConnectorError("dns_failed", "Connector endpoint could not be resolved")
    for address in addresses:
        _validate_address(address, hostname, policy)
    # Pin one validated address into the outbound connection. The Host header
    # and SNI hostname are preserved by ``request_bytes`` so HTTPS certificate
    # validation remains against the configured name, not an attacker-selected
    # post-validation DNS answer.
    return normalized, hostname, sorted(addresses)[0]


async def validate_endpoint(url: str, policy: OutboundPolicy) -> str:
    """Validate a configured endpoint without exposing resolver internals."""
    normalized, _, _ = await _validated_endpoint_address(url, policy)
    return normalized


class ConnectorTransport:
    def __init__(self, policy: OutboundPolicy | None = None) -> None:
        self.policy = policy or OutboundPolicy()

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> tuple[dict[str, Any] | list[Any], int]:
        data, status = await self.request_bytes(
            method, url, headers=headers, json=json, params=params
        )
        try:
            parsed = httpx.Response(status_code=200, content=data).json()
        except ValueError as exc:
            raise ConnectorError("malformed_response", "Provider returned malformed JSON", status) from exc
        if not isinstance(parsed, (dict, list)):
            raise ConnectorError("malformed_response", "Provider returned unexpected JSON", status)
        return parsed, status

    async def request_bytes(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> tuple[bytes, int]:
        method = method.upper()
        # Provider adapters may issue only server-built read requests. POST is
        # needed for Elasticsearch _search/_field_caps but is never retried
        # because POST is not generically idempotent.
        if method not in {"GET", "HEAD", "POST"}:
            raise ConnectorError("method_denied", "Connector transport permits read methods only")
        endpoint, hostname, address = await _validated_endpoint_address(url, self.policy)
        safe_headers = {str(k): str(v) for k, v in (headers or {}).items()}
        # Do not allow a caller to steer virtual-host routing. Provider adapters
        # supply auth/content headers only; authority is derived from endpoint.
        safe_headers.pop("Host", None)
        parsed = urlsplit(endpoint)
        host_header = hostname
        if parsed.port and parsed.port not in {80, 443}:
            host_header = f"{hostname}:{parsed.port}"
        safe_headers["Host"] = host_header
        timeout = httpx.Timeout(
            timeout=self.policy.total_timeout_seconds,
            connect=self.policy.connect_timeout_seconds,
            read=self.policy.read_timeout_seconds,
        )
        retryable_method = method in {"GET", "HEAD"}
        for attempt in range((_MAX_RETRIES if retryable_method else 0) + 1):
            try:
                async with httpx.AsyncClient(
                    verify=self.policy.tls_verify(),
                    follow_redirects=False,
                    timeout=timeout,
                ) as client:
                    pinned_url = httpx.URL(endpoint).copy_with(host=address)
                    request = client.build_request(method, pinned_url, headers=safe_headers, json=json, params=params)
                    # httpcore honors this extension when establishing TLS; it
                    # preserves certificate/SNI validation for the configured
                    # hostname while connecting to the validated pinned IP.
                    request.extensions["sni_hostname"] = hostname
                    response = await client.send(request, stream=True)
                    try:
                        body = await self._read_limited(response)
                    finally:
                        await response.aclose()
            except ConnectorError:
                raise
            except httpx.TimeoutException as exc:
                error = ConnectorError("query_timeout", "Provider request timed out", retryable=True)
                if retryable_method and attempt < _MAX_RETRIES:
                    await asyncio.sleep(0.1 * (attempt + 1))
                    continue
                raise error from exc
            except httpx.TransportError as exc:
                error = ConnectorError("network_failed", "Connector endpoint could not be reached", retryable=True)
                if retryable_method and attempt < _MAX_RETRIES:
                    await asyncio.sleep(0.1 * (attempt + 1))
                    continue
                raise error from exc
            if response.is_redirect:
                raise ConnectorError("redirect_denied", "Connector endpoint redirect was denied", response.status_code)
            if response.status_code in _TRANSIENT and retryable_method and attempt < _MAX_RETRIES:
                await asyncio.sleep(0.1 * (attempt + 1))
                continue
            if response.status_code >= 400:
                raise sanitize_provider_error(response.status_code)
            return body, response.status_code
        raise ConnectorError("provider_failure", "Provider request failed")

    async def _read_limited(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self.policy.max_response_bytes:
                raise ConnectorError("response_too_large", "Provider response exceeded configured limit")
            chunks.append(chunk)
        return b"".join(chunks)


class _BoundedResponseStream(httpx.AsyncByteStream):
    """Cap all bytes received during one remote MCP session."""

    def __init__(self, stream: httpx.AsyncByteStream, maximum: int) -> None:
        self._stream = stream
        self._maximum = maximum
        self._total = 0

    async def __aiter__(self):  # type: ignore[override]
        async for chunk in self._stream:
            self._total += len(chunk)
            if self._total > self._maximum:
                raise ConnectorError(
                    "response_too_large", "Provider response exceeded configured limit"
                )
            yield chunk

    async def aclose(self) -> None:
        await self._stream.aclose()


class PinnedConnectorTransport(httpx.AsyncBaseTransport):
    """Keep a streaming MCP session on the validated HTTPS origin and IP."""

    def __init__(
        self,
        *,
        endpoint: str,
        hostname: str,
        address: str,
        policy: OutboundPolicy,
    ) -> None:
        parsed = urlsplit(endpoint)
        self._scheme = parsed.scheme
        self._hostname = hostname.casefold()
        self._port = parsed.port
        self._max_response_bytes = policy.max_response_bytes
        self._address = address
        self._delegate = httpx.AsyncHTTPTransport(verify=policy.tls_verify(), retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if (
            request.url.scheme != self._scheme
            or (request.url.host or "").casefold() != self._hostname
            or request.url.port != self._port
            or request.method not in {"GET", "POST", "DELETE"}
        ):
            raise ConnectorError("endpoint_denied", "Connector request escaped its approved endpoint")
        host_header = self._hostname
        if self._port and self._port not in {80, 443}:
            host_header = f"{host_header}:{self._port}"
        request.headers["Host"] = host_header
        request.extensions["sni_hostname"] = self._hostname
        request.url = request.url.copy_with(host=self._address)
        response = await self._delegate.handle_async_request(request)
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise ConnectorError("redirect_denied", "Connector endpoint redirect was denied")
        response.stream = _BoundedResponseStream(
            response.stream, self._max_response_bytes
        )
        return response

    async def aclose(self) -> None:
        await self._delegate.aclose()


@asynccontextmanager
async def pinned_http_client(
    url: str,
    *,
    policy: OutboundPolicy,
    headers: Mapping[str, str] | None = None,
):
    """Yield an HTTP client suitable for one bounded Streamable HTTP MCP session."""

    endpoint, hostname, address = await _validated_endpoint_address(url, policy)
    safe_headers = {str(key): str(value) for key, value in (headers or {}).items()}
    safe_headers.pop("Host", None)
    timeout = httpx.Timeout(
        timeout=policy.total_timeout_seconds,
        connect=policy.connect_timeout_seconds,
        read=policy.read_timeout_seconds,
    )
    async with httpx.AsyncClient(
        transport=PinnedConnectorTransport(
            endpoint=endpoint,
            hostname=hostname,
            address=address,
            policy=policy,
        ),
        headers=safe_headers,
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        yield endpoint, client
