"""Read existing, project-bound Elastic evidence through the MCP connector store.

The Elastic credential and source discovery remain owned by the MCP service.
This adapter sends only the signed-in user's session cookie and a bounded
semantic search request; it never sees or stores the Elastic credential.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.config import settings

from .models import (
    ConnectorFreshnessResult,
    ConnectorHealthResult,
    ConnectorHealthStatus,
    ConnectorQuery,
    ConnectorReadResult,
    ConnectorResultItem,
)

ELASTIC_SEARCH_CAPABILITY_ID = "connector.mcp_elastic.search"


class ElasticSourceContext(BaseModel):
    """Safe, owner-bound field names used to guide the Labs search capability."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    search_fields: list[str] = Field(default_factory=list, max_length=12)
    message_fields: list[str] = Field(default_factory=list, max_length=5)
    timestamp_field: str | None = None
    service_field: str | None = None
    severity_field: str | None = None
    environment_field: str | None = None

    @field_validator("search_fields", "message_fields")
    @classmethod
    def validate_field_names(cls, values: list[str]) -> list[str]:
        pattern = re.compile(r"^[A-Za-z0-9@_.-]{1,128}$")
        if any(not pattern.fullmatch(value) for value in values):
            raise ValueError("Elastic field names must be safe field identifiers")
        return list(dict.fromkeys(values))

    @field_validator("timestamp_field", "service_field", "severity_field", "environment_field")
    @classmethod
    def validate_optional_field_name(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"^[A-Za-z0-9@_.-]{1,128}$", value):
            raise ValueError("Elastic field names must be safe field identifiers")
        return value

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        if not re.fullmatch(r"^[A-Za-z0-9 ._*-]{1,120}$", value):
            raise ValueError("Elastic source labels must be safe identifiers")
        return value


class ExistingElasticConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector_id: str = Field(min_length=1, max_length=64)
    source_ids: list[str] = Field(default_factory=list, max_length=3)
    max_results: int = Field(default=5, ge=1, le=10)
    time_window_days: int = Field(default=7, ge=1, le=30)
    source_contexts: list[ElasticSourceContext] = Field(default_factory=list, max_length=3)
    project_sources: dict[str, list[str]] = Field(default_factory=dict, max_length=100)
    project_source_contexts: dict[str, list[ElasticSourceContext]] = Field(
        default_factory=dict, max_length=100
    )

    @field_validator("source_ids")
    @classmethod
    def validate_legacy_source_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Elastic source IDs must be unique")
        return value

    @field_validator("project_sources")
    @classmethod
    def validate_project_sources(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        for project_id, source_ids in value.items():
            if not project_id or len(project_id) > 255:
                raise ValueError("Elastic project source keys must be valid project IDs")
            if not source_ids or len(source_ids) > 3 or len(source_ids) != len(set(source_ids)):
                raise ValueError(
                    "Each project must select between one and three unique Elastic sources"
                )
            if any(not source_id or len(source_id) > 128 for source_id in source_ids):
                raise ValueError("Elastic source IDs are invalid")
        return value

    @model_validator(mode="after")
    def require_source_scope(self) -> "ExistingElasticConfig":
        if not self.source_ids and not self.project_sources:
            raise ValueError("At least one project-scoped Elastic source is required")
        unknown = set(self.project_source_contexts).difference(self.project_sources)
        if unknown:
            raise ValueError("Elastic field context belongs to an unknown project scope")
        if any(len(contexts) > 3 for contexts in self.project_source_contexts.values()):
            raise ValueError("Each project may describe at most three Elastic sources")
        return self

    def sources_for_project(self, project_id: str) -> list[str]:
        return self.project_sources.get(project_id, self.source_ids)

    def contexts_for_projects(self, project_ids: list[str]) -> list[ElasticSourceContext]:
        if self.project_source_contexts:
            contexts: list[ElasticSourceContext] = []
            for project_id in project_ids:
                contexts.extend(self.project_source_contexts.get(project_id, []))
            return contexts
        return self.source_contexts


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
                timeout=httpx.Timeout(10),
                trust_env=False,
                transport=self._transport,
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
        self,
        capability_id: str,
        query: ConnectorQuery,
        config: dict,
        credential_reference: str | None = None,
        project_id: str | None = None,
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
        source_ids = parsed.sources_for_project(project_id)
        if not source_ids:
            raise ValueError("This project has no Elastic sources selected")

        search_result, freshness_result = await asyncio.gather(
            self._search_for_user(
                query=query,
                parsed=parsed,
                source_ids=source_ids,
                project_id=project_id,
                access_token=access_token,
            ),
            self.freshness_for_user(
                config,
                project_id=project_id,
                access_token=access_token,
            ),
            return_exceptions=True,
        )
        if isinstance(search_result, Exception):
            raise search_result
        raw_items, truncated, search_diagnostics = search_result
        items: list[ConnectorResultItem] = []
        for raw in raw_items[: parsed.max_results]:
            if not isinstance(raw, dict):
                continue
            try:
                items.append(ConnectorResultItem.model_validate(raw))
            except ValueError:
                continue

        diagnostics = _authoritative_diagnostics(
            query=query.query,
            window_days=parsed.time_window_days,
            source_ids=source_ids,
            items=items,
            truncated=truncated,
            freshness=(
                freshness_result if isinstance(freshness_result, ConnectorFreshnessResult) else None
            ),
            freshness_error=(
                str(freshness_result) if isinstance(freshness_result, Exception) else None
            ),
            field_contexts=_validated_field_contexts(search_diagnostics),
        )
        return ConnectorReadResult(items=items, truncated=truncated, diagnostics=diagnostics)

    async def _search_for_user(
        self,
        *,
        query: ConnectorQuery,
        parsed: ExistingElasticConfig,
        source_ids: list[str],
        project_id: str,
        access_token: str,
    ) -> tuple[list[Any], bool, dict[str, Any]]:
        """Run only the bounded text search; the caller joins it with freshness."""
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(20),
                trust_env=False,
                transport=self._transport,
                cookies={"access_token": access_token},
            ) as client:
                response = await client.post(
                    f"{_api_base_url()}/mcp/projects/{quote(project_id, safe='')}/connectors/"
                    f"{quote(parsed.connector_id, safe='')}/search",
                    json={
                        "source_ids": source_ids,
                        "query": query.query,
                        "limit": min(query.limit, parsed.max_results),
                        "time_window_days": parsed.time_window_days,
                    },
                )
        except httpx.TimeoutException as exc:
            raise ValueError(
                "Elastic search timed out; try a shorter or more specific query"
            ) from exc
        except httpx.HTTPError as exc:
            raise ValueError("The existing Elastic service could not be reached") from exc
        if response.status_code == 403:
            raise ValueError(
                "This project has not been granted access to the selected Elastic logs"
            )
        if response.status_code == 409:
            raise ValueError("The existing Elastic connection is not ready; test it in Connectors")
        if response.status_code < 200 or response.status_code >= 300:
            raise ValueError("The existing Elastic search failed")
        try:
            payload = response.json()
            raw_items = payload.get("items", [])
            truncated = bool(payload.get("truncated", False))
            diagnostics = payload.get("diagnostics", {})
        except (ValueError, AttributeError) as exc:
            raise ValueError("The existing Elastic service returned an invalid response") from exc
        if not isinstance(raw_items, list):
            raise ValueError("The existing Elastic service returned an invalid response")
        return raw_items, truncated, diagnostics if isinstance(diagnostics, dict) else {}

    async def freshness_for_user(
        self,
        config: dict[str, Any],
        *,
        project_id: str,
        access_token: str,
    ) -> ConnectorFreshnessResult:
        """Ask the credential-owning service for source-level freshness only."""
        parsed = ExistingElasticConfig.model_validate(self.validate_config(config))
        source_ids = parsed.sources_for_project(project_id)
        if not source_ids:
            raise ValueError("This project has no Elastic sources selected")
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(20),
                trust_env=False,
                transport=self._transport,
                cookies={"access_token": access_token},
            ) as client:
                response = await client.post(
                    f"{_api_base_url()}/mcp/projects/{quote(project_id, safe='')}/connectors/"
                    f"{quote(parsed.connector_id, safe='')}/freshness",
                    json={"source_ids": source_ids, "time_window_days": parsed.time_window_days},
                )
        except httpx.TimeoutException as exc:
            raise ValueError("Elastic freshness check timed out; try again") from exc
        except httpx.HTTPError as exc:
            raise ValueError("The existing Elastic service could not be reached") from exc
        if response.status_code == 403:
            raise ValueError(
                "This project has not been granted access to the selected Elastic logs"
            )
        if response.status_code == 409:
            raise ValueError("The existing Elastic connection is not ready; test it in Connectors")
        if response.status_code < 200 or response.status_code >= 300:
            raise ValueError("The existing Elastic freshness check failed")
        try:
            return ConnectorFreshnessResult.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "The existing Elastic service returned an invalid freshness response"
            ) from exc


def _api_base_url() -> str:
    """Derive the MCP REST root from the configured MCP endpoint, never request data."""
    parsed = urlsplit(settings.mcp_docs_base_url)
    path = parsed.path.removesuffix("/mcp").rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def _authoritative_diagnostics(
    *,
    query: str,
    window_days: int,
    source_ids: list[str],
    items: list[ConnectorResultItem],
    truncated: bool,
    freshness: ConnectorFreshnessResult | None,
    freshness_error: str | None,
    field_contexts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Describe one complete Elastic attempt without prompting agent retries.

    The result is authoritative for the exact query and selected source scope,
    not a claim about every log source or about the underlying estate.
    """

    source_diagnostics: list[dict[str, Any]] = []
    unavailable_sources: list[str] = []
    recent_event_count = 0
    if freshness is not None:
        for source in freshness.sources:
            if source.status == "unavailable":
                activity = "unavailable"
                unavailable_sources.append(source.source)
            elif source.freshness_status:
                activity = source.freshness_status
                recent_event_count += source.events_in_window or 0
            elif (source.events_in_window or 0) > 0:
                activity = "active"
                recent_event_count += source.events_in_window or 0
            elif source.total_documents == 0:
                activity = "empty"
            else:
                activity = "stale"
            source_diagnostics.append(
                {
                    **source.model_dump(mode="json"),
                    "activity": activity,
                }
            )

    available_source_ids = {
        str(source["source_id"])
        for source in source_diagnostics
        if source.get("status") == "available" and source.get("source_id") in source_ids
    }
    available_count = len(available_source_ids)
    if freshness is None or available_count == 0:
        coverage_status = "unavailable"
    elif available_count < len(source_ids):
        coverage_status = "partial"
    else:
        coverage_status = "complete"

    if items:
        outcome = "matches_found"
    elif coverage_status != "complete" or unavailable_sources:
        outcome = "coverage_unavailable"
    elif recent_event_count > 0:
        outcome = "no_matches_in_active_sources"
    else:
        outcome = "no_recent_events"

    diagnostics: dict[str, Any] = {
        "contract": "authoritative_elastic_search_v1",
        "outcome": outcome,
        "query": query,
        "window_days": freshness.window_days if freshness is not None else window_days,
        "searched_source_ids": list(source_ids),
        "match_count": len(items),
        "truncated": truncated,
        "coverage_status": coverage_status,
        "recent_event_count": recent_event_count,
        "unavailable_sources": unavailable_sources,
        "sources": source_diagnostics,
        "field_contexts": field_contexts,
        "terminal_for_exact_query": True,
        "retry_recommended": False,
        "cross_provider_fallback_recommended": False,
        "next_action": (
            "Answer from this observation. Do not retry with synonyms or a wildcard, "
            "and do not switch to another evidence provider unless the user explicitly asks."
        ),
    }
    if freshness_error:
        diagnostics["coverage_error"] = freshness_error
    return diagnostics


def _validated_field_contexts(diagnostics: dict[str, Any]) -> list[dict[str, Any]]:
    """Accept only the bounded schema-name contract from the credential owner."""
    raw_contexts = diagnostics.get("field_contexts")
    if not isinstance(raw_contexts, list):
        return []
    contexts: list[dict[str, Any]] = []
    for raw in raw_contexts[:3]:
        try:
            contexts.append(ElasticSourceContext.model_validate(raw).model_dump(mode="json"))
        except ValueError:
            continue
    return contexts
