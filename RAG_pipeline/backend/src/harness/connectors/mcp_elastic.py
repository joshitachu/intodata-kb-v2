"""Read existing, project-bound Elastic evidence through the MCP connector store.

The Elastic credential and source discovery remain owned by the MCP service.
This adapter sends only the signed-in user's session cookie and a bounded
semantic search request; it never sees or stores the Elastic credential.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from src.config import settings

from .models import (
    ConnectorHealthResult,
    ConnectorHealthStatus,
    ConnectorQuery,
    ConnectorReadResult,
    ConnectorResultItem,
)

ELASTIC_SEARCH_CAPABILITY_ID = "connector.mcp_elastic.search"


class ExistingElasticConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector_id: str = Field(min_length=1, max_length=64)
    source_ids: list[str] = Field(min_length=1, max_length=3)
    max_results: int = Field(default=5, ge=1, le=10)
    time_window_days: int = Field(default=7, ge=1, le=30)


class ExistingElasticConnector:
    """A Labs-facing handle to an already-connected 8001 Elastic account."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        return ExistingElasticConfig.model_validate(config).model_dump(mode="json")

    async def health(self, config: dict, credential_reference: str | None) -> ConnectorHealthResult:
        _ = credential_reference
        self.validate_config(config)
        # The live, user-authorized connection check is health_for_user below.
        # Generic probes have no caller identity and must not borrow another one.
        return ConnectorHealthResult(
            status=ConnectorHealthStatus.UNKNOWN,
            summary="Sign in to verify the existing Elastic connection",
        )

    async def health_for_user(self, config: dict, access_token: str) -> ConnectorHealthResult:
        parsed = ExistingElasticConfig.model_validate(self.validate_config(config))
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10), trust_env=False, transport=self._transport,
                cookies={"access_token": access_token},
            ) as client:
                response = await client.post(
                    f"{_api_base_url()}/mcp/connectors/{quote(parsed.connector_id, safe='')}/test",
                )
            if response.status_code < 200 or response.status_code >= 300:
                return ConnectorHealthResult(
                    status=ConnectorHealthStatus.UNHEALTHY,
                    summary="The existing Elastic connection could not be verified",
                )
            if response.json().get("status") != "connected":
                return ConnectorHealthResult(
                    status=ConnectorHealthStatus.UNHEALTHY,
                    summary="The existing Elastic connection needs attention",
                )
        except (httpx.HTTPError, ValueError, AttributeError):
            return ConnectorHealthResult(
                status=ConnectorHealthStatus.UNHEALTHY,
                summary="The existing Elastic service could not be reached",
            )
        return ConnectorHealthResult(
            status=ConnectorHealthStatus.HEALTHY,
            summary="Existing Elastic connection is responding",
        )

    async def read(
        self, capability_id: str, query: ConnectorQuery, config: dict,
        credential_reference: str | None = None, project_id: str | None = None,
    ) -> ConnectorReadResult:
        _ = capability_id, credential_reference, project_id
        raise ValueError("Existing Elastic searches require the signed-in user's access")

    async def read_for_user(
        self,
        capability_id: str,
        query: ConnectorQuery,
        config: dict,
        *,
        project_id: str,
        access_token: str,
    ) -> ConnectorReadResult:
        if capability_id != ELASTIC_SEARCH_CAPABILITY_ID:
            raise ValueError("Unsupported existing Elastic capability")
        parsed = ExistingElasticConfig.model_validate(self.validate_config(config))
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(20), trust_env=False, transport=self._transport,
                cookies={"access_token": access_token},
            ) as client:
                response = await client.post(
                    f"{_api_base_url()}/mcp/projects/{quote(project_id, safe='')}/connectors/"
                    f"{quote(parsed.connector_id, safe='')}/search",
                    json={
                        "source_ids": parsed.source_ids,
                        "query": query.query,
                        "limit": min(query.limit, parsed.max_results),
                        "time_window_days": parsed.time_window_days,
                    },
                )
        except httpx.TimeoutException as exc:
            raise ValueError("Elastic search timed out; try a shorter or more specific query") from exc
        except httpx.HTTPError as exc:
            raise ValueError("The existing Elastic service could not be reached") from exc
        if response.status_code == 403:
            raise ValueError("This project has not been granted access to the selected Elastic logs")
        if response.status_code == 409:
            raise ValueError("The existing Elastic connection is not ready; test it in Connectors")
        if response.status_code < 200 or response.status_code >= 300:
            raise ValueError("The existing Elastic search failed")
        try:
            payload = response.json()
            raw_items = payload.get("items", [])
            truncated = bool(payload.get("truncated", False))
        except (ValueError, AttributeError) as exc:
            raise ValueError("The existing Elastic service returned an invalid response") from exc
        if not isinstance(raw_items, list):
            raise ValueError("The existing Elastic service returned an invalid response")
        items: list[ConnectorResultItem] = []
        for raw in raw_items[: parsed.max_results]:
            if not isinstance(raw, dict):
                continue
            try:
                items.append(ConnectorResultItem.model_validate(raw))
            except ValueError:
                continue
        return ConnectorReadResult(items=items, truncated=truncated)


def _api_base_url() -> str:
    """Derive the MCP REST root from the configured MCP endpoint, never request data."""
    parsed = urlsplit(settings.mcp_docs_base_url)
    path = parsed.path.removesuffix("/mcp").rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")
