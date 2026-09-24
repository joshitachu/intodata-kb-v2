"""Read one reviewed OpenAPI GET operation as project-scoped evidence.

The owner supplies an OpenAPI 3 document and selects one operation. The agent
cannot choose an endpoint, method, credential, or project scope at call time.
Only flat JSON result objects are supported; unsupported API shapes fail closed.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from .credentials import resolve_env_secret
from .http_safety import PinnedHostTransport
from .models import (
    ConnectorHealthResult,
    ConnectorHealthStatus,
    ConnectorQuery,
    ConnectorReadResult,
    ConnectorResultItem,
)


OPENAPI_SEARCH_CAPABILITY_ID = "connector.openapi.search"
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
_SAFE_PATH = re.compile(r"^/(?:[A-Za-z0-9._~-]+/?)*$")
_MAX_SPEC_BYTES = 65_536


class OpenApiReadOnlyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: AnyHttpUrl
    allowed_hosts: tuple[str, ...] = Field(min_length=1, max_length=20)
    openapi_spec: dict[str, Any]
    operation_id: str = Field(min_length=1, max_length=160)
    operation_path: str = Field(min_length=1, max_length=240)
    query_parameter: str = "q"
    project_parameter: str = "project_id"
    limit_parameter: str = "limit"
    items_field: str = "items"
    title_field: str = "title"
    excerpt_field: str = "excerpt"
    source_ref_field: str = "source_ref"
    result_project_field: str = "project_id"
    health_project_id: str = Field(min_length=1, max_length=255)
    timeout_seconds: float = Field(default=5.0, ge=0.1, le=15.0)
    max_response_bytes: int = Field(default=1_000_000, ge=1_024, le=5_000_000)
    max_results: int = Field(default=10, ge=1, le=50)

    @field_validator("allowed_hosts")
    @classmethod
    def validate_allowed_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        hosts = tuple(host.lower().strip() for host in value)
        if any(not host or "/" in host or ":" in host for host in hosts):
            raise ValueError("allowed_hosts must contain hostnames only")
        return hosts

    @field_validator(
        "query_parameter", "project_parameter", "limit_parameter", "items_field",
        "title_field", "excerpt_field", "source_ref_field", "result_project_field",
    )
    @classmethod
    def validate_field_name(cls, value: str) -> str:
        if _NAME.fullmatch(value) is None:
            raise ValueError("OpenAPI parameter and result field names must be simple names")
        return value

    @field_validator("operation_path")
    @classmethod
    def validate_operation_path(cls, value: str) -> str:
        if _SAFE_PATH.fullmatch(value) is None or ".." in value or "//" in value:
            raise ValueError("operation_path must be a fixed safe absolute path")
        return value

    @model_validator(mode="after")
    def validate_operation(self) -> "OpenApiReadOnlyConfig":
        parsed = urlsplit(str(self.base_url))
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or parsed.port not in {None, 443}:
            raise ValueError("base_url must use standard-port HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("base_url may contain only an HTTPS origin")
        if host not in self.allowed_hosts:
            raise ValueError("base_url host must be explicitly allowlisted")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("base_url must use a hostname")
        if len({self.query_parameter, self.project_parameter, self.limit_parameter}) != 3:
            raise ValueError("query, project, and limit parameters must be distinct")
        if len(json.dumps(self.openapi_spec, separators=(",", ":")).encode()) > _MAX_SPEC_BYTES:
            raise ValueError("OpenAPI document exceeds the size limit")
        version = self.openapi_spec.get("openapi")
        if not isinstance(version, str) or not version.startswith("3."):
            raise ValueError("An OpenAPI 3 document is required")
        paths = self.openapi_spec.get("paths")
        path = paths.get(self.operation_path) if isinstance(paths, dict) else None
        operation = path.get("get") if isinstance(path, dict) else None
        if not isinstance(operation, dict) or operation.get("operationId") != self.operation_id:
            raise ValueError("Selected OpenAPI GET operation was not found")
        parameters = operation.get("parameters", [])
        if not isinstance(parameters, list):
            raise ValueError("Selected OpenAPI operation has invalid parameters")
        required = {self.query_parameter, self.project_parameter, self.limit_parameter}
        declared = {
            param.get("name") for param in parameters
            if isinstance(param, dict) and param.get("in") == "query" and param.get("required") is True
            and "$ref" not in param
        }
        if not required.issubset(declared):
            raise ValueError("Selected OpenAPI GET must require query, project, and limit parameters")
        return self


HostResolver = Callable[[str], Awaitable[Sequence[str]]]
SecretResolver = Callable[..., str]


class OpenApiReadOnlyConnector:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        host_resolver: HostResolver | None = None,
        secret_resolver: SecretResolver = resolve_env_secret,
    ) -> None:
        self._transport = transport
        self._host_resolver = host_resolver or _resolve_host_addresses
        self._secret_resolver = secret_resolver

    def validate_config(self, config: dict) -> dict:
        return OpenApiReadOnlyConfig.model_validate(config).model_dump(mode="json")

    async def health(self, config: dict, credential_reference: str | None) -> ConnectorHealthResult:
        parsed = OpenApiReadOnlyConfig.model_validate(config)
        await self._request(parsed, credential_reference, "nexus-health-probe", parsed.health_project_id, 1)
        return ConnectorHealthResult(
            status=ConnectorHealthStatus.HEALTHY,
            summary="Selected OpenAPI GET operation responded successfully",
        )

    async def read(
        self, capability_id: str, query: ConnectorQuery, config: dict,
        credential_reference: str | None, project_id: str | None,
    ) -> ConnectorReadResult:
        if capability_id != OPENAPI_SEARCH_CAPABILITY_ID:
            raise ValueError("Unsupported connector capability")
        if not project_id:
            raise ValueError("OpenAPI connector requires a bound project")
        parsed = OpenApiReadOnlyConfig.model_validate(config)
        limit = min(query.limit, parsed.max_results)
        payload = await self._request(parsed, credential_reference, query.query, project_id, limit)
        raw_items = payload.get(parsed.items_field)
        if not isinstance(raw_items, list):
            raise ValueError("OpenAPI response has no result items")
        items: list[ConnectorResultItem] = []
        for raw in raw_items[:limit]:
            if not isinstance(raw, dict) or raw.get(parsed.result_project_field) != project_id:
                raise ValueError("OpenAPI response contains an unscoped result")
            title = raw.get(parsed.title_field)
            excerpt = raw.get(parsed.excerpt_field)
            ref = raw.get(parsed.source_ref_field)
            if not all(isinstance(value, str) and value.strip() for value in (title, excerpt, ref)):
                raise ValueError("OpenAPI response contains an incomplete result")
            items.append(ConnectorResultItem(
                title=title[:200], excerpt=excerpt[:2000],
                source_ref=f"openapi:{urlsplit(str(parsed.base_url)).hostname}:{ref}"[:255],
                metadata={"project_id": project_id, "operation_id": parsed.operation_id},
            ))
        return ConnectorReadResult(
            items=items,
            truncated=len(raw_items) > limit or payload.get("has_more") is True,
        )

    async def _request(
        self, config: OpenApiReadOnlyConfig, credential_reference: str | None,
        query: str, project_id: str, limit: int,
    ) -> dict[str, Any]:
        host = urlsplit(str(config.base_url)).hostname
        if host is None:
            raise ValueError("OpenAPI endpoint has no hostname")
        address = await _require_public_host(host, self._host_resolver)
        headers: dict[str, str] = {}
        if credential_reference is not None:
            secret = self._secret_resolver(credential_reference, expected_host=host)
            if "\r" in secret or "\n" in secret:
                raise ValueError("Connector credential is invalid")
            headers["Authorization"] = f"Bearer {secret}"
        url = f"{str(config.base_url).rstrip('/')}{config.operation_path}"
        params = {
            config.query_parameter: query,
            config.project_parameter: project_id,
            config.limit_parameter: limit,
        }
        async with httpx.AsyncClient(
            transport=PinnedHostTransport(hostname=host, address=address, delegate=self._transport),
            timeout=httpx.Timeout(config.timeout_seconds),
            follow_redirects=False, trust_env=False,
        ) as client:
            async with client.stream("GET", url, params=params, headers=headers) as response:
                if response.is_redirect:
                    raise ValueError("OpenAPI endpoint redirects are not allowed")
                if not 200 <= response.status_code < 300:
                    raise ValueError("OpenAPI endpoint returned an unsuccessful response")
                length = response.headers.get("content-length")
                if length is not None and int(length) > config.max_response_bytes:
                    raise ValueError("OpenAPI response exceeds size limit")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > config.max_response_bytes:
                        raise ValueError("OpenAPI response exceeds size limit")
                    chunks.append(chunk)
        try:
            payload = json.loads(b"".join(chunks))
        except (TypeError, ValueError) as exc:
            raise ValueError("OpenAPI endpoint returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("OpenAPI endpoint must return a JSON object")
        return payload


async def _resolve_host_addresses(host: str) -> Sequence[str]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return tuple(record[4][0] for record in records)


async def _require_public_host(host: str, resolver: HostResolver) -> str:
    try:
        addresses = await resolver(host)
    except OSError as exc:
        raise ValueError("OpenAPI host could not be resolved") from exc
    if not addresses:
        raise ValueError("OpenAPI host did not resolve")
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise ValueError("OpenAPI host resolved to a non-public address")
    return addresses[0]
