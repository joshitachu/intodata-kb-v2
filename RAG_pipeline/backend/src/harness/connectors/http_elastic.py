"""Bounded read-only HTTP adapter for Elastic-style search responses.

This module owns vendor-shaped request and response handling. Generic connector
contracts only receive normalized evidence items and never see HTTP endpoints,
credentials, or vendor response structures.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from .credentials import ConnectorCredentialError, resolve_env_secret
from .http_safety import PinnedHostTransport
from .models import (
    ConnectorHealthResult,
    ConnectorHealthStatus,
    ConnectorQuery,
    ConnectorReadResult,
    ConnectorResultItem,
)


class ElasticHttpConfig(BaseModel):
    """Safe HTTP boundary configuration for a single Elastic-compatible endpoint."""

    model_config = ConfigDict(extra="forbid")

    base_url: AnyHttpUrl
    allowed_hosts: tuple[str, ...] = Field(min_length=1, max_length=20)
    index: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    project_field: str = Field(default="project_id.keyword", pattern=r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")
    search_path: str = Field(default="/{index}/_search", min_length=1, max_length=200)
    health_path: str = Field(default="/{index}/_search", min_length=1, max_length=200)
    timeout_seconds: float = Field(default=5.0, ge=0.1, le=30.0)
    max_response_bytes: int = Field(default=1_000_000, ge=1_024, le=5_000_000)
    max_results: int = Field(default=10, ge=1, le=50)

    @field_validator("allowed_hosts")
    @classmethod
    def validate_allowed_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        hosts = tuple(host.lower().strip() for host in value)
        if any(not host or "/" in host or ":" in host for host in hosts):
            raise ValueError("allowed_hosts must contain hostnames only")
        return hosts

    @field_validator("search_path", "health_path")
    @classmethod
    def validate_safe_path(cls, value: str) -> str:
        if not value.startswith("/") or "?" in value or "#" in value:
            raise ValueError("connector paths must be absolute paths without query or fragment")
        parts = value.split("/")
        if any(part in {".", ".."} for part in parts):
            raise ValueError("connector paths may not contain traversal segments")
        return value

    @model_validator(mode="after")
    def validate_endpoint(self) -> "ElasticHttpConfig":
        parsed = urlsplit(str(self.base_url))
        if parsed.scheme != "https":
            raise ValueError("base_url must use HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url may not contain credentials, query, or fragment")
        if parsed.path not in {"", "/"}:
            raise ValueError("base_url must not contain a path")
        if parsed.port not in {None, 443}:
            raise ValueError("base_url must use the standard HTTPS port")
        host = (parsed.hostname or "").lower()
        if host not in self.allowed_hosts:
            raise ValueError("base_url host must be explicitly allowlisted")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("base_url must use a hostname, not an IP address")
        if self.search_path.count("{index}") != 1:
            raise ValueError("search_path must contain exactly one {index} placeholder")
        if self.health_path.count("{index}") > 1:
            raise ValueError("health_path may contain at most one {index} placeholder")
        return self


HostResolver = Callable[[str], Awaitable[Sequence[str]]]
_API_KEY = re.compile(r"^[A-Za-z0-9+/_=-]{1,8192}$")
_INDEX_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MAX_DISCOVERED_INDEXES = 100


class ElasticHttpConnector:
    """Elastic-style search adapter with DNS, redirect, timeout, and size limits."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        host_resolver: HostResolver | None = None,
    ) -> None:
        self._transport = transport
        self._host_resolver = host_resolver or _resolve_host_addresses

    def validate_config(self, config: dict) -> dict:
        return ElasticHttpConfig.model_validate(config).model_dump(mode="json")

    async def health(
        self, config: dict, credential_reference: str | None
    ) -> ConnectorHealthResult:
        parsed = ElasticHttpConfig.model_validate(self.validate_config(config))
        path = parsed.health_path.replace("{index}", quote(parsed.index, safe=""))
        search_probe = path.endswith("/_search")
        await self._request(
            parsed, method="POST" if search_probe else "GET", path=path,
            credential_reference=credential_reference,
            json_body={"size": 0, "query": {"match_none": {}}} if search_probe else None,
        )
        return ConnectorHealthResult(
            status=ConnectorHealthStatus.HEALTHY,
            summary="Connector endpoint responded to the health probe",
        )

    async def read(
        self, capability_id: str, query: ConnectorQuery, config: dict,
        credential_reference: str | None = None,
        project_id: str | None = None,
    ) -> ConnectorReadResult:
        _ = capability_id  # Binding/catalog validation owns capability authorization.
        if not project_id:
            raise ValueError("Connector search requires a bound project")
        parsed = ElasticHttpConfig.model_validate(self.validate_config(config))
        limit = min(query.limit, parsed.max_results)
        payload = await self._request(
            parsed,
            method="POST",
            path=parsed.search_path.replace("{index}", quote(parsed.index, safe="")),
            credential_reference=credential_reference,
            json_body={
                "size": limit,
                "query": {
                    "bool": {
                        "must": [{"simple_query_string": {"query": query.query}}],
                        "filter": [{"term": {parsed.project_field: project_id}}],
                    }
                },
                "_source": True,
            },
        )
        return _normalize_search_response(payload, limit, project_id, parsed.project_field)

    async def discover_indexes(
        self, config: dict, credential_reference: str | None
    ) -> list[str]:
        """List visible index names through a bounded, authenticated metadata read."""

        parsed = ElasticHttpConfig.model_validate(self.validate_config(config))
        payload = await self._request(
            parsed, method="GET", path="/_aliases",
            credential_reference=credential_reference,
        )
        return sorted(
            name for name in payload
            if _INDEX_NAME.fullmatch(name) and not name.startswith(".")
        )[:_MAX_DISCOVERED_INDEXES]

    async def _request(
        self,
        config: ElasticHttpConfig,
        *,
        method: str,
        path: str,
        credential_reference: str | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target = _build_target(config.base_url, path)
        host = urlsplit(target).hostname
        if host is None:
            raise ValueError("connector endpoint has no host")
        address = await _require_public_host(host, self._host_resolver)
        headers = _auth_headers(credential_reference, host)
        timeout = httpx.Timeout(config.timeout_seconds)
        async with httpx.AsyncClient(
            transport=PinnedHostTransport(hostname=host, address=address, delegate=self._transport),
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream(method, target, json=json_body, headers=headers) as response:
                if response.is_redirect:
                    raise ValueError("connector endpoint redirects are not allowed")
                if response.status_code < 200 or response.status_code >= 300:
                    raise ValueError("connector endpoint returned an unsuccessful response")
                content_length = response.headers.get("content-length")
                if content_length is not None and int(content_length) > config.max_response_bytes:
                    raise ValueError("connector response exceeds configured size limit")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > config.max_response_bytes:
                        raise ValueError("connector response exceeds configured size limit")
                    chunks.append(chunk)
        try:
            payload = json.loads(b"".join(chunks))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("connector endpoint returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("connector endpoint returned an invalid JSON object")
        return payload


def _auth_headers(credential_reference: str | None, host: str) -> dict[str, str]:
    if credential_reference is None:
        return {}
    secret = resolve_env_secret(credential_reference, expected_host=host)
    if _API_KEY.fullmatch(secret) is None:
        raise ConnectorCredentialError("Connector API key is invalid")
    return {"Authorization": f"ApiKey {secret}"}


def _build_target(base_url: AnyHttpUrl, path: str) -> str:
    base = urlsplit(str(base_url))
    return urlunsplit((base.scheme, base.netloc, path, "", ""))


async def _resolve_host_addresses(host: str) -> Sequence[str]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return tuple(record[4][0] for record in records)


async def _require_public_host(host: str, resolver: HostResolver) -> str:
    try:
        addresses = await resolver(host)
    except OSError as exc:
        raise ValueError("connector host could not be resolved") from exc
    if not addresses:
        raise ValueError("connector host did not resolve")
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise ValueError("connector host resolved to a non-public address")
    return addresses[0]


def _normalize_search_response(
    payload: dict[str, Any], limit: int, project_id: str, project_field: str
) -> ConnectorReadResult:
    hits = payload.get("hits")
    raw_hits = hits.get("hits") if isinstance(hits, dict) else None
    if not isinstance(raw_hits, list):
        raise ValueError("connector response does not contain search hits")
    total = hits.get("total") if isinstance(hits, dict) else None
    total_value = total.get("value") if isinstance(total, dict) else total
    items: list[ConnectorResultItem] = []
    for hit in raw_hits[:limit]:
        if not isinstance(hit, dict):
            continue
        source = hit.get("_source") if isinstance(hit.get("_source"), dict) else {}
        project_path = project_field.removesuffix(".keyword").split(".")
        value: Any = source
        for part in project_path:
            value = value.get(part) if isinstance(value, dict) else None
        if value != project_id:
            raise ValueError("connector response contains an unscoped result")
        document_id = str(hit.get("_id") or "unknown")
        index = str(hit.get("_index") or "unknown")
        title = str(source.get("title") or source.get("name") or document_id)[:200]
        excerpt = _excerpt(source)[:2000]
        items.append(
            ConnectorResultItem(
                title=title,
                excerpt=excerpt,
                source_ref=f"search:{index}:{document_id}"[:255],
                metadata={"index": index, "document_id": document_id},
            )
        )
    return ConnectorReadResult(
        items=items,
        truncated=isinstance(total_value, int) and total_value > len(items),
    )


def _excerpt(source: dict[str, Any]) -> str:
    for key in ("excerpt", "summary", "message", "text", "content", "description"):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(source, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
