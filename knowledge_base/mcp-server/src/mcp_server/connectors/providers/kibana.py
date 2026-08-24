"""Optional Kibana discovery companion for the Elastic Stack connector."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping
from urllib.parse import quote

from ..domain import ConnectorInstanceConfig, ConnectorSourceProfile
from ..errors import ConnectorError
from ..transport import ConnectorTransport, OutboundPolicy, normalize_endpoint


class KibanaDiscoveryProvider:
    """Use documented Spaces/Data Views APIs only; never query ``.kibana``."""

    def __init__(self, transport: ConnectorTransport | None = None) -> None:
        self._transport = transport

    def _transport_for(self, instance: ConnectorInstanceConfig) -> ConnectorTransport:
        if self._transport:
            return self._transport
        safe = instance.safe_config
        from mcp_server.config import config
        allow_http = config.app_env == "development" and bool(safe.get("development_allow_http", False))
        return ConnectorTransport(OutboundPolicy(
            require_https=not allow_http,
            allow_private_hosts=frozenset(str(item) for item in safe.get("approved_private_hosts", []) if isinstance(item, str)),
            ca_bundle=str(safe["ca_bundle_path"]) if safe.get("ca_bundle_path") else None,
            custom_ca_pem=str(safe["custom_ca_pem"]) if safe.get("custom_ca_pem") else None,
        ))

    def _endpoint(self, instance: ConnectorInstanceConfig) -> str:
        from mcp_server.config import config
        endpoint = instance.safe_config.get("kibana_endpoint")
        if not isinstance(endpoint, str) or not endpoint:
            raise ConnectorError("kibana_not_configured", "Kibana endpoint is not configured")
        allow_http = config.app_env == "development" and bool(instance.safe_config.get("development_allow_http", False))
        return normalize_endpoint(endpoint, require_https=not allow_http)

    def _headers(self, instance: ConnectorInstanceConfig, credential: str) -> dict[str, str]:
        auth_type = instance.safe_config.get("kibana_auth_type") or instance.safe_config.get("auth_type", "api_key")
        if auth_type == "api_key":
            auth = f"ApiKey {credential}"
        elif auth_type == "basic":
            import base64
            auth = f"Basic {base64.b64encode(credential.encode()).decode()}"
        else:
            raise ConnectorError("invalid_credentials", "Unsupported Kibana authentication type")
        return {"Authorization": auth, "Accept": "application/json", "kbn-xsrf": "nexus-connectors"}

    async def list_spaces(self, instance: ConnectorInstanceConfig, credential: str) -> list[dict[str, str]]:
        payload, _ = await self._transport_for(instance).request_json(
            "GET", f"{self._endpoint(instance)}/api/spaces/space", headers=self._headers(instance, credential)
        )
        if not isinstance(payload, list):
            raise ConnectorError("malformed_response", "Kibana spaces response was invalid")
        return [
            {"id": str(space.get("id", ""))[:200], "name": str(space.get("name", ""))[:300]}
            for space in payload if isinstance(space, Mapping) and isinstance(space.get("id"), str)
        ]

    async def list_data_views(
        self, instance: ConnectorInstanceConfig, credential: str, space: str = "default"
    ) -> list[dict[str, str]]:
        endpoint = self._space_endpoint(instance, space)
        payload, _ = await self._transport_for(instance).request_json(
            "GET", f"{endpoint}/api/data_views", headers=self._headers(instance, credential)
        )
        views = payload.get("data_view", []) if isinstance(payload, Mapping) else []
        if not isinstance(views, list):
            raise ConnectorError("malformed_response", "Kibana data views response was invalid")
        return [
            {"id": str(view.get("id", ""))[:200], "title": str(view.get("title", ""))[:500]}
            for view in views if isinstance(view, Mapping) and isinstance(view.get("id"), str)
        ]

    async def get_data_view(
        self, instance: ConnectorInstanceConfig, credential: str, data_view_id: str, space: str = "default"
    ) -> ConnectorSourceProfile:
        if not data_view_id or "/" in data_view_id:
            raise ConnectorError("invalid_source", "Kibana data-view ID is invalid")
        endpoint = self._space_endpoint(instance, space)
        payload, _ = await self._transport_for(instance).request_json(
            "GET", f"{endpoint}/api/data_views/data_view/{quote(data_view_id, safe='')}", headers=self._headers(instance, credential)
        )
        view = payload.get("data_view") if isinstance(payload, Mapping) else None
        if not isinstance(view, Mapping):
            raise ConnectorError("malformed_response", "Kibana data-view response was invalid")
        title = view.get("title")
        if not isinstance(title, str) or not title or "/" in title:
            raise ConnectorError("invalid_source", "Kibana data-view target is invalid")
        fields = view.get("fields", [])
        runtime = view.get("runtimeFieldMap", {})
        profile = _data_view_field_profile(fields, runtime, view.get("timeFieldName"))
        return ConnectorSourceProfile(
            id=f"src_{hashlib.sha256((instance.id + ':kibana:' + data_view_id).encode()).hexdigest()[:24]}",
            connector_instance_id=instance.id,
            source_type="kibana_data_view",
            display_label=str(view.get("name") or title)[:500],
            locator=title,
            fields=profile | {"kibana_space": space, "kibana_data_view_id": data_view_id},
        )

    def _space_endpoint(self, instance: ConnectorInstanceConfig, space: str) -> str:
        if space == "default":
            return self._endpoint(instance)
        if not space or "/" in space:
            raise ConnectorError("invalid_source", "Kibana space is invalid")
        return f"{self._endpoint(instance)}/s/{quote(space, safe='')}"


def _data_view_field_profile(fields: Any, runtime_fields: Any, time_field: Any) -> dict[str, Any]:
    names = [str(field.get("name")) for field in fields if isinstance(field, Mapping) and isinstance(field.get("name"), str)]
    if isinstance(runtime_fields, Mapping):
        names.extend(str(name) for name in runtime_fields if isinstance(name, str))
    def choose(candidates: tuple[str, ...]) -> str | None:
        lowered = {name.casefold(): name for name in names}
        for candidate in candidates:
            if candidate.casefold() in lowered:
                return lowered[candidate.casefold()]
        return next((name for name in names if any(candidate.casefold() in name.casefold() for candidate in candidates)), None)
    result: dict[str, Any] = {
        "timestamp_field": time_field if isinstance(time_field, str) and time_field in names else choose(("@timestamp", "timestamp")),
        "message_fields": [value for value in (choose(("message", "error.message")),) if value],
        "service_field": choose(("service.name", "service", "job", "flow")),
        "severity_field": choose(("log.level", "severity", "level")),
        "environment_field": choose(("service.environment", "environment", "env")),
        "runtime_fields": [str(name)[:200] for name in runtime_fields] if isinstance(runtime_fields, Mapping) else [],
        "max_time_window_days": 30,
        "samples_allowed": True,
    }
    return {key: value for key, value in result.items() if value not in (None, [])}
