"""User-authenticated bridge to the existing Nexus-managed MCP connector store."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from src.config import settings


class ExistingElasticUnavailable(RuntimeError):
    pass


class ExistingElasticSourceDenied(ExistingElasticUnavailable):
    """A listed source is not readable with the connected account's privileges."""


class RemoteMcpUnavailable(RuntimeError):
    """A remote MCP lifecycle or invocation request failed safely."""


class McpConnectorBridge:
    """Keep Elastic credentials and source locators inside the MCP service."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def connected_elastic_accounts(self, access_token: str) -> list[dict[str, Any]]:
        """Return every connected Elastic account the signed-in owner may manage."""
        payload = await self._request("GET", "/mcp/connectors", access_token)
        return [
            item for item in payload.get("connectors", [])
            if isinstance(item, dict)
            and item.get("provider_id") == "elastic_stack"
            and item.get("enabled") is True
            and item.get("status") == "connected"
        ]

    async def existing_elastic(self, access_token: str) -> dict[str, Any] | None:
        """Backward-compatible helper for callers that expect a single account."""
        accounts = await self.connected_elastic_accounts(access_token)
        return accounts[0] if accounts else None

    async def create_remote_mcp(
        self,
        access_token: str,
        *,
        name: str,
        server_url: str,
        authentication: str,
        credential: str | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Create, test, and discover a credential-owned remote MCP server."""

        connector: dict[str, Any] | None = None
        try:
            connector = await self._request(
                "POST",
                "/mcp/connectors",
                access_token,
                json_body={
                    "provider_id": "remote_mcp",
                    "display_name": name,
                    "safe_config": {
                        "endpoint": server_url,
                        "transport": "streamable_http",
                        "auth_type": authentication,
                    },
                    "credential_type": authentication,
                    "credential": credential,
                },
            )
            connector_id = connector.get("id")
            if not isinstance(connector_id, str) or not connector_id:
                raise RemoteMcpUnavailable("Nexus did not return the new MCP connection.")
            tested = await self._request(
                "POST", f"/mcp/connectors/{quote(connector_id, safe='')}/test", access_token
            )
            if tested.get("status") != "connected":
                raise RemoteMcpUnavailable("The MCP server did not pass its connection test.")
            discovered = await self._request(
                "POST",
                f"/mcp/connectors/{quote(connector_id, safe='')}/discover",
                access_token,
                json_body={},
            )
            sources = discovered.get("sources")
            if not isinstance(sources, list):
                raise RemoteMcpUnavailable("The MCP server returned no usable tool catalogue.")
            return connector, [item for item in sources if isinstance(item, dict)]
        except ExistingElasticUnavailable as exc:
            if connector is not None and isinstance(connector.get("id"), str):
                try:
                    await self._request(
                        "DELETE",
                        f"/mcp/connectors/{quote(str(connector['id']), safe='')}",
                        access_token,
                    )
                except ExistingElasticUnavailable:
                    pass
            raise RemoteMcpUnavailable("The remote MCP server could not be connected.") from exc
        except RemoteMcpUnavailable:
            if connector is not None and isinstance(connector.get("id"), str):
                try:
                    await self._request(
                        "DELETE",
                        f"/mcp/connectors/{quote(str(connector['id']), safe='')}",
                        access_token,
                    )
                except ExistingElasticUnavailable:
                    pass
            raise

    async def test_remote_mcp(self, access_token: str, connector_id: str) -> dict[str, Any]:
        try:
            return await self._request(
                "POST", f"/mcp/connectors/{quote(connector_id, safe='')}/test", access_token
            )
        except ExistingElasticUnavailable as exc:
            raise RemoteMcpUnavailable("The remote MCP health check failed.") from exc

    async def discover_remote_mcp(
        self, access_token: str, connector_id: str
    ) -> list[dict[str, Any]]:
        try:
            payload = await self._request(
                "POST",
                f"/mcp/connectors/{quote(connector_id, safe='')}/discover",
                access_token,
                json_body={},
            )
        except ExistingElasticUnavailable as exc:
            raise RemoteMcpUnavailable("The remote MCP tool catalogue could not be refreshed.") from exc
        rows = payload.get("sources")
        if not isinstance(rows, list):
            raise RemoteMcpUnavailable("The remote MCP server returned an invalid tool catalogue.")
        return [row for row in rows if isinstance(row, dict)]

    async def call_remote_mcp_tool(
        self,
        access_token: str,
        *,
        project_id: str,
        connector_id: str,
        tool_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return await self._request(
                "POST",
                f"/mcp/projects/{quote(project_id, safe='')}/connectors/"
                f"{quote(connector_id, safe='')}/tools/{quote(tool_id, safe='')}/call",
                access_token,
                json_body={"arguments": arguments},
            )
        except ExistingElasticUnavailable as exc:
            raise RemoteMcpUnavailable("The approved MCP tool call failed.") from exc

    async def review_remote_mcp_tool(
        self,
        access_token: str,
        *,
        connector_id: str,
        source_id: str,
        enabled: bool,
        approved_purpose: str | None,
    ) -> dict[str, Any]:
        """Record an owner's explicit review in the credential-owning service."""

        try:
            return await self._request(
                "PATCH",
                f"/mcp/connectors/{quote(connector_id, safe='')}/sources/"
                f"{quote(source_id, safe='')}",
                access_token,
                json_body={
                    "enabled": enabled,
                    "approved_purpose": approved_purpose if enabled else None,
                },
            )
        except ExistingElasticUnavailable as exc:
            raise RemoteMcpUnavailable("The MCP tool review could not be saved.") from exc

    async def bind_remote_mcp_project(
        self,
        access_token: str,
        *,
        project_id: str,
        connector_id: str,
        source_ids: list[str],
    ) -> dict[str, Any]:
        """Grant an exact reviewed MCP tool set to one project."""

        try:
            return await self._request(
                "PUT",
                f"/mcp/projects/{quote(project_id, safe='')}/connector-bindings/"
                f"{quote(connector_id, safe='')}",
                access_token,
                json_body={
                    "source_ids": list(dict.fromkeys(source_ids)),
                    "capability_ids": ["remote.mcp.call"],
                    "budgets": {},
                    "enabled": True,
                },
            )
        except ExistingElasticUnavailable as exc:
            raise RemoteMcpUnavailable("The MCP project access grant could not be saved.") from exc

    async def unbind_remote_mcp_project(
        self, access_token: str, *, project_id: str, connector_id: str
    ) -> None:
        try:
            await self._request(
                "DELETE",
                f"/mcp/projects/{quote(project_id, safe='')}/connector-bindings/"
                f"{quote(connector_id, safe='')}",
                access_token,
                ignore_not_found=True,
            )
        except ExistingElasticUnavailable as exc:
            raise RemoteMcpUnavailable("The MCP project access grant could not be removed.") from exc

    async def delete_remote_mcp(self, access_token: str, connector_id: str) -> None:
        try:
            await self._request(
                "DELETE", f"/mcp/connectors/{quote(connector_id, safe='')}", access_token
            )
        except ExistingElasticUnavailable as exc:
            raise RemoteMcpUnavailable("The remote MCP connection could not be removed.") from exc

    async def existing_elastic_sources(
        self, access_token: str, connector_id: str, *, refresh: bool = False
    ) -> list[dict[str, str]]:
        """Return safe display names for the connected account's indexed sources."""
        if not connector_id:
            return []
        path = f"/mcp/connectors/{quote(connector_id, safe='')}"
        payload = await self._request(
            "POST" if refresh else "GET",
            f"{path}/discover" if refresh else f"{path}/sources",
            access_token,
            **({"json_body": {}} if refresh else {}),
        )
        rows = payload.get("sources", [])
        if not isinstance(rows, list):
            return []
        sources: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict) or row.get("enabled") is not True:
                continue
            source_id = row.get("id")
            label = row.get("display_label")
            source_type = row.get("source_type")
            if not all(isinstance(value, str) and value for value in (source_id, label, source_type)):
                continue
            sources.append({"id": source_id, "label": label, "type": source_type})
        return sources

    async def prepare_existing_elastic(
        self,
        access_token: str,
        *,
        connector_id: str,
        source_ids: list[str],
    ) -> dict[str, Any]:
        accounts = await self.connected_elastic_accounts(access_token)
        connector = next((item for item in accounts if str(item.get("id", "")) == connector_id), None)
        if connector is None:
            raise ExistingElasticUnavailable("No connected Elastic account was found in Nexus.")
        selected_ids = list(dict.fromkeys(source_ids))
        if not selected_ids or len(selected_ids) > 3:
            raise ExistingElasticUnavailable("Choose between one and three Elastic sources for this project.")

        # A real probe runs in the credential-owning service, under this user's
        # authority. It never returns or forwards the Elastic secret.
        test = await self._request("POST", f"/mcp/connectors/{quote(connector_id, safe='')}/test", access_token)
        if test.get("status") != "connected":
            raise ExistingElasticUnavailable("The existing Elastic connection needs attention before Labs can use it.")
        available = await self.existing_elastic_sources(access_token, connector_id)
        available_by_id = {item["id"]: item for item in available}
        if any(source_id not in available_by_id for source_id in selected_ids):
            raise ExistingElasticUnavailable(
                "One or more selected Elastic sources are unavailable. Refresh the source list and try again."
            )
        candidates = [available_by_id[source_id] for source_id in selected_ids]
        selected: list[dict[str, Any]] = []
        source_contexts: list[dict[str, Any]] = []
        denied_count = 0
        unusable_count = 0
        # Source listings can include indices whose fields are hidden from this
        # account. Probe independently so one restricted alias cannot block
        # other log sources the same account can read.
        for candidate in candidates:
            try:
                discovery = await self._request(
                    "POST",
                    f"/mcp/connectors/{quote(connector_id, safe='')}/discover",
                    access_token,
                    json_body={"source_ids": [str(candidate["id"])]},
                )
            except ExistingElasticSourceDenied:
                denied_count += 1
                continue
            if not _has_usable_search_profile(discovery, str(candidate["id"])):
                unusable_count += 1
                continue
            selected.append(candidate)
            source_contexts.append(_search_context(discovery, str(candidate["id"]), len(selected)))
        if selected and (denied_count or unusable_count):
            problems: list[str] = []
            if denied_count:
                problems.append(f"{denied_count} could not be read")
            if unusable_count:
                problems.append(f"{unusable_count} had no usable timestamp")
            raise ExistingElasticUnavailable(
                "Some selected Elastic sources could not be enabled (" + ", ".join(problems) + "). "
                "Remove those sources or fix their access and field mapping, then retry."
            )
        if not selected and denied_count:
            raise ExistingElasticUnavailable(
                "Elastic is connected, but this account cannot read the selected sources. "
                "Ask your Elastic administrator to grant read access to at least one log source, then retry."
            )
        if not selected:
            if unusable_count:
                raise ExistingElasticUnavailable(
                    "Elastic is connected, but none of the selected sources has both a usable event timestamp "
                    "and searchable text fields. "
                    "Ask your Elastic administrator to check the log-source fields."
                )
            raise ExistingElasticUnavailable(
                "The selected log sources could not be prepared. Check the connection and retry."
            )
        source_ids = [str(item["id"]) for item in selected]
        labels = [str(item.get("display_label", "Log source")) for item in selected]

        return {
            "connector_id": connector_id,
            "display_name": str(connector.get("display_name") or "Elastic Stack"),
            "source_ids": source_ids,
            "source_labels": labels,
            "source_contexts": source_contexts,
        }

    async def _request(
        self,
        method: str,
        path: str,
        access_token: str,
        *,
        json_body: dict[str, Any] | None = None,
        ignore_not_found: bool = False,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(20), trust_env=False, transport=self._transport,
                cookies={"access_token": access_token},
            ) as client:
                response = await client.request(
                    method,
                    f"{_api_base_url()}{path}",
                    json=json_body,
                )
        except httpx.HTTPError as exc:
            raise ExistingElasticUnavailable("Nexus could not reach its connector service.") from exc
        if response.status_code == 404 and ignore_not_found:
            return {}
        if not response.is_success:
            if (
                method == "POST"
                and path.endswith("/discover")
                and _api_error_code(response) == "authorization_failed"
            ):
                raise ExistingElasticSourceDenied("Elastic denied access to this source")
            if response.status_code == 403:
                raise ExistingElasticUnavailable(
                    "Your account needs connector and project-owner access to enable Elastic for Labs."
                )
            if response.status_code == 401:
                raise ExistingElasticUnavailable("Your session expired. Sign in again and retry.")
            if response.status_code == 404:
                raise ExistingElasticUnavailable("The selected project or Elastic connection was not found.")
            raise ExistingElasticUnavailable("The existing Elastic connection could not be prepared for Labs.")
        if response.status_code == 204 or not response.content:
            return {}
        try:
            value = response.json()
        except ValueError as exc:
            raise ExistingElasticUnavailable("The connector service returned an invalid response.") from exc
        if not isinstance(value, dict):
            raise ExistingElasticUnavailable("The connector service returned an invalid response.")
        return value


def _api_error_code(response: httpx.Response) -> str | None:
    """Read only the stable machine error code, never provider response text."""
    try:
        body = response.json()
    except ValueError:
        return None
    detail = body.get("detail") if isinstance(body, dict) else None
    code = detail.get("code") if isinstance(detail, dict) else None
    return code if isinstance(code, str) else None


def _has_usable_search_profile(discovery: dict[str, Any], source_id: str) -> bool:
    sources = discovery.get("sources")
    if not isinstance(sources, list):
        return False
    source = next((item for item in sources if isinstance(item, dict) and item.get("id") == source_id), None)
    if source is None:
        return False
    fields = source.get("field_profile")
    return (
        isinstance(fields, dict)
        and isinstance(fields.get("timestamp_field"), str)
        and isinstance(fields.get("search_fields"), list)
        and any(isinstance(value, str) and value for value in fields["search_fields"])
    )


_FIELD_NAME = re.compile(r"^[A-Za-z0-9@_.-]{1,128}$")
_SOURCE_LABEL = re.compile(r"^[A-Za-z0-9._*-]{1,120}$")


def _search_context(discovery: dict[str, Any], source_id: str, ordinal: int) -> dict[str, Any]:
    """Keep only bounded schema names from the trusted field-capability profile."""
    rows = discovery.get("sources")
    source = next((
        item for item in rows
        if isinstance(item, dict) and item.get("id") == source_id
    ), None) if isinstance(rows, list) else None
    profile = source.get("field_profile") if isinstance(source, dict) else None
    profile = profile if isinstance(profile, dict) else {}

    def field_name(value: Any) -> str | None:
        return value if isinstance(value, str) and _FIELD_NAME.fullmatch(value) else None

    def field_list(key: str, limit: int) -> list[str]:
        values = profile.get(key)
        if not isinstance(values, list):
            return []
        return list(dict.fromkeys(
            item for raw in values[:limit * 2]
            if (item := field_name(raw)) is not None
        ))[:limit]

    raw_label = source.get("display_label") if isinstance(source, dict) else None
    safe_label = "".join(
        char for char in raw_label if char.isascii() and (char.isalnum() or char in "._*-")
    )[:120] if isinstance(raw_label, str) else ""
    if not _SOURCE_LABEL.fullmatch(safe_label):
        safe_label = f"Elastic source {ordinal}"
    context: dict[str, Any] = {
        "label": safe_label,
        "search_fields": field_list("search_fields", 12),
        "message_fields": field_list("message_fields", 5),
    }
    for key in ("timestamp_field", "service_field", "severity_field", "environment_field"):
        value = field_name(profile.get(key))
        if value:
            context[key] = value
    return context


def _api_base_url() -> str:
    parsed = urlsplit(settings.mcp_docs_base_url)
    path = parsed.path.removesuffix("/mcp").rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")
