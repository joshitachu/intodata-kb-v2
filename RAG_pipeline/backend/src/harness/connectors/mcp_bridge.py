"""User-authenticated bridge to the existing Nexus-managed MCP connector store."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from src.config import settings


class ExistingElasticUnavailable(RuntimeError):
    pass


class ExistingElasticSourceDenied(ExistingElasticUnavailable):
    """A listed source is not readable with the connected account's privileges."""


class McpConnectorBridge:
    """Keep Elastic credentials and source locators inside the MCP service."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def existing_elastic(self, access_token: str) -> dict[str, Any] | None:
        payload = await self._request("GET", "/mcp/connectors", access_token)
        candidates = [
            item for item in payload.get("connectors", [])
            if isinstance(item, dict)
            and item.get("provider_id") == "elastic_stack"
            and item.get("enabled") is True
            and item.get("status") == "connected"
        ]
        if len(candidates) > 1:
            raise ExistingElasticUnavailable(
                "More than one connected Elastic account is available; choose one in the Nexus connector screen first."
            )
        return candidates[0] if candidates else None

    async def existing_elastic_sources(
        self, access_token: str, connector_id: str
    ) -> list[dict[str, str]]:
        """Return safe display names for the connected account's indexed sources."""
        if not connector_id:
            return []
        payload = await self._request(
            "GET", f"/mcp/connectors/{quote(connector_id, safe='')}/sources", access_token
        )
        rows = payload.get("sources", [])
        if not isinstance(rows, list):
            return []
        sources: list[dict[str, str]] = []
        for row in rows[:200]:
            if not isinstance(row, dict) or row.get("enabled") is not True:
                continue
            source_id = row.get("id")
            label = row.get("display_label")
            source_type = row.get("source_type")
            if not all(isinstance(value, str) and value for value in (source_id, label, source_type)):
                continue
            sources.append({"id": source_id, "label": label, "type": source_type})
        return sources

    async def prepare_existing_elastic(self, access_token: str) -> dict[str, Any]:
        connector = await self.existing_elastic(access_token)
        if connector is None:
            raise ExistingElasticUnavailable("No connected Elastic account was found in Nexus.")
        connector_id = str(connector.get("id", ""))
        if not connector_id:
            raise ExistingElasticUnavailable("The connected Elastic account could not be identified.")

        # A real probe runs in the credential-owning service, under this user's
        # authority. It never returns or forwards the Elastic secret.
        test = await self._request("POST", f"/mcp/connectors/{quote(connector_id, safe='')}/test", access_token)
        if test.get("status") != "connected":
            raise ExistingElasticUnavailable("The existing Elastic connection needs attention before Labs can use it.")
        sources_payload = await self._request(
            "GET", f"/mcp/connectors/{quote(connector_id, safe='')}/sources", access_token
        )
        source_rows = sources_payload.get("sources", [])
        candidates = _recommended_log_sources(source_rows)
        if not candidates:
            raise ExistingElasticUnavailable(
                "The connected Elastic account has no obvious log or error sources to add to Labs."
            )
        selected: list[dict[str, Any]] = []
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
            if not _has_timestamp_profile(discovery, str(candidate["id"])):
                unusable_count += 1
                continue
            selected.append(candidate)
            if len(selected) == 2:
                break
        if not selected and denied_count:
            raise ExistingElasticUnavailable(
                "Elastic is connected, but this account cannot read the recommended log sources. "
                "Ask your Elastic administrator to grant read access to at least one log source, then retry."
            )
        if not selected:
            if unusable_count:
                raise ExistingElasticUnavailable(
                    "Elastic is connected, but none of the recommended sources has a usable event timestamp. "
                    "Ask your Elastic administrator to check the log-source fields."
                )
            raise ExistingElasticUnavailable(
                "The recommended log sources could not be prepared. Check the connection and retry."
            )
        source_ids = [str(item["id"]) for item in selected]
        labels = [str(item.get("display_label", "Log source")) for item in selected]

        return {
            "connector_id": connector_id,
            "display_name": str(connector.get("display_name") or "Elastic Stack"),
            "source_ids": source_ids,
            "source_labels": labels,
        }

    async def _request(
        self,
        method: str,
        path: str,
        access_token: str,
        *,
        json_body: dict[str, Any] | None = None,
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
        try:
            value = response.json()
        except ValueError as exc:
            raise ExistingElasticUnavailable("The connector service returned an invalid response.") from exc
        if not isinstance(value, dict):
            raise ExistingElasticUnavailable("The connector service returned an invalid response.")
        return value


def _recommended_log_sources(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("enabled") is not True:
            continue
        label = row.get("display_label")
        source_id = row.get("id")
        if not isinstance(label, str) or not isinstance(source_id, str):
            continue
        name = label.casefold()
        if not any(term in name for term in ("log", "error", "monitor")):
            continue
        score = 0
        if row.get("source_type") == "alias":
            score += 4
        if "error" in name:
            score += 3
        if "monitor" in name:
            score += 2
        if "log" in name:
            score += 1
        ranked.append((score, name, row))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    # A few alternatives let setup skip indices that are listed but not
    # readable, while keeping permission probes strictly bounded.
    return [item[2] for item in ranked[:12]]


def _api_error_code(response: httpx.Response) -> str | None:
    """Read only the stable machine error code, never provider response text."""
    try:
        body = response.json()
    except ValueError:
        return None
    detail = body.get("detail") if isinstance(body, dict) else None
    code = detail.get("code") if isinstance(detail, dict) else None
    return code if isinstance(code, str) else None


def _has_timestamp_profile(discovery: dict[str, Any], source_id: str) -> bool:
    sources = discovery.get("sources")
    if not isinstance(sources, list):
        return False
    source = next((item for item in sources if isinstance(item, dict) and item.get("id") == source_id), None)
    if source is None:
        return False
    fields = source.get("field_profile")
    return isinstance(fields, dict) and isinstance(fields.get("timestamp_field"), str)


def _api_base_url() -> str:
    parsed = urlsplit(settings.mcp_docs_base_url)
    path = parsed.path.removesuffix("/mcp").rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")
