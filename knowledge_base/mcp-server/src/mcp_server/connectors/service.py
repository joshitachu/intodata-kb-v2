"""Connector lifecycle, scope checks, and immutable snapshot orchestration."""

from __future__ import annotations

import datetime as dt
from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from .catalog import connector_catalog
from .domain import ConnectorSourceProfile, account_key_for
from .errors import ConnectorError
from .providers.elasticsearch import register_elasticsearch_provider
from .providers.kibana import KibanaDiscoveryProvider
from .providers.remote_mcp import (
    REMOTE_MCP_CALL_CAPABILITY,
    register_remote_mcp_provider,
)
from .providers.talend_tmc import register_talend_tmc_provider
from .repository import ConnectorRepository, instance_config, source_profile


# Built-in providers are opt-in code imports rather than filesystem discovery.
register_elasticsearch_provider()
register_talend_tmc_provider()
register_remote_mcp_provider()


_SECRETISH_CONFIG_KEYS = {"credential", "password", "secret", "token", "authorization", "api_key"}


def _safe_config(config: Mapping[str, Any]) -> dict[str, Any]:
    safe = dict(config)
    for key, value in safe.items():
        # This is a routing choice, not credential material.  Keep the explicit
        # exception narrow (including its value type) so similarly named secret
        # fields remain blocked.
        if key.casefold() == "kibana_reuse_elasticsearch_credentials" and isinstance(value, bool):
            continue
        if any(fragment in key.casefold() for fragment in _SECRETISH_CONFIG_KEYS):
            raise ValueError("Credentials must be supplied through write-only credential fields")
    custom_ca = safe.get("custom_ca_pem")
    if custom_ca is not None and (not isinstance(custom_ca, str) or len(custom_ca) > 65_536):
        raise ValueError("Custom CA certificate is invalid or exceeds the configured limit")
    if safe.get("verify_tls") is False:
        raise ValueError("TLS verification cannot be disabled for connector endpoints")
    return safe


class ConnectorService:
    def __init__(self, session: AsyncSession, repository: ConnectorRepository | None = None) -> None:
        self.session = session
        self.repository = repository or ConnectorRepository(session)

    async def create_instance(self, *, provider_id: str, display_name: str, safe_config: Mapping[str, Any],
                              credential_type: str, credential: str | None, kibana_credential: str | None,
                              user_id: int, username: str | None) -> object:
        try:
            manifest = connector_catalog.manifest(provider_id)
        except KeyError as exc:
            # Provider IDs are caller input on the management API.  Preserve a
            # structured validation failure instead of leaking a catalogue
            # lookup exception as a 500.
            raise ValueError("Unknown connector provider") from exc
        if credential_type not in manifest.authentication_types:
            raise ValueError("Unsupported connector credential type")
        if credential_type == "none" and credential is not None:
            raise ValueError("Unauthenticated connectors cannot store a credential")
        if credential_type != "none" and not credential:
            raise ValueError("Connector credential is required")
        account_key = account_key_for(manifest, safe_config)
        if account_key:
            existing = await self.repository.find_by_account_key(provider_id, account_key)
            if existing is not None:
                raise ConnectorError(
                    "duplicate_account",
                    f"{manifest.label} is already connected for this account "
                    f"as '{existing.display_name}'. Rotate that connection's "
                    "credential instead of adding a second one.",
                )
        instance = await self.repository.create_instance(
            provider_id=provider_id, display_name=display_name, safe_config=_safe_config(safe_config),
            credential_type=credential_type, credential=credential, kibana_credential=kibana_credential, user_id=user_id,
            account_key=account_key,
        )
        await self.repository.audit(user_id=user_id, username=username, action="connector.create",
                                    resource_type="connector", resource_id=instance.id,
                                    details={"provider_id": provider_id})
        return instance

    async def update_instance(self, connector_id: str, *, display_name: str | None, safe_config: Mapping[str, Any] | None,
                              enabled: bool | None, user_id: int, username: str | None) -> object:
        instance = await self._required_instance(connector_id)
        result = await self.repository.update_instance(
            instance, display_name=display_name, safe_config=_safe_config(safe_config) if safe_config is not None else None,
            enabled=enabled, user_id=user_id,
        )
        await self.repository.audit(user_id=user_id, username=username, action="connector.update",
                                    resource_type="connector", resource_id=connector_id,
                                    details={"display_name_changed": display_name is not None, "enabled": enabled})
        return result

    async def rotate_credential(self, connector_id: str, *, credential: str, credential_type: str | None,
                                kibana_credential: str | None, reuse_elasticsearch_credential: bool,
                                user_id: int, username: str | None) -> None:
        instance = await self._required_instance(connector_id)
        manifest = connector_catalog.manifest(instance.provider_id)
        if credential_type and credential_type not in manifest.authentication_types:
            raise ValueError("Unsupported connector credential type")
        await self.repository.rotate_credential(instance, credential=credential, credential_type=credential_type,
                                                kibana_credential=kibana_credential,
                                                reuse_elasticsearch_credential=reuse_elasticsearch_credential, user_id=user_id)
        await self.repository.audit(user_id=user_id, username=username, action="connector.rotate_credential",
                                    resource_type="connector", resource_id=connector_id, details={"rotated": True})

    async def test_instance(self, connector_id: str, *, user_id: int, username: str | None) -> dict[str, object]:
        instance = await self._required_instance(connector_id)
        if not instance.enabled:
            raise ConnectorError("connector_disabled", "Connector is disabled")
        provider = connector_catalog.provider(instance.provider_id)
        now = dt.datetime.now(dt.timezone.utc)
        try:
            credential = self._credential(instance)
            provider_result = await provider.test(instance_config(instance), credential)
            instance.status = "connected"
            instance.health_code = None
            result = {
                "status": "connected",
                "message": "Connection succeeded",
                "checked_at": now,
                "details": {key: value for key, value in provider_result.items() if key != "status"},
            }
        except ConnectorError as exc:
            instance.status = "degraded"
            instance.health_code = exc.code
            result = {
                "status": "degraded", "message": exc.message, "checked_at": now,
                "details": {"code": exc.code},
            }
        finally:
            instance.last_checked_at = now
        await self.repository.audit(user_id=user_id, username=username, action="connector.test",
                                    resource_type="connector", resource_id=connector_id,
                                    details={"status": instance.status, "health_code": instance.health_code})
        return result

    async def discover(self, connector_id: str, *, selected_source_ids: list[str] | None = None,
                       include_kibana: bool = False, kibana_space: str = "default", data_view_id: str | None = None,
                       user_id: int, username: str | None) -> list[object]:
        instance = await self._required_instance(connector_id)
        if not instance.enabled:
            raise ConnectorError("connector_disabled", "Connector is disabled")
        provider = connector_catalog.provider(instance.provider_id)
        credential = self._credential(instance)
        config = instance_config(instance)
        profiles: list[ConnectorSourceProfile]
        if selected_source_ids:
            selected = await self.repository.get_sources(connector_id, selected_source_ids)
            if len(selected) != len(set(selected_source_ids)):
                raise ConnectorError("invalid_source", "One or more selected sources are not approved for this connector")
            profiles = [await provider.describe(config, source_profile(row), credential) for row in selected]
        else:
            profiles = await provider.discover(config, credential)
        if include_kibana and instance.provider_id == "elastic_stack":
            kibana_credential = (
                self.repository.secrets.decrypt_for_provider(instance.kibana_credential_ciphertext)
                if instance.kibana_credential_ciphertext else credential
            )
            kibana = KibanaDiscoveryProvider()
            if data_view_id:
                profiles.append(await kibana.get_data_view(config, kibana_credential, data_view_id, kibana_space))
            else:
                # A successful Kibana probe is intentionally non-fatal to direct
                # Elasticsearch discovery; UI can call the focused data-view API.
                await kibana.list_spaces(config, kibana_credential)
        rows = await self.repository.upsert_sources(profiles)
        await self.repository.audit(user_id=user_id, username=username, action="connector.discover",
                                    resource_type="connector", resource_id=connector_id,
                                    details={"source_count": len(rows), "kibana": include_kibana})
        return rows

    async def review_remote_tool(
        self,
        connector_id: str,
        source_id: str,
        *,
        enabled: bool,
        approved_purpose: str | None,
        user_id: int,
        username: str | None,
    ) -> object:
        instance = await self._required_instance(connector_id)
        if instance.provider_id != "remote_mcp":
            raise ConnectorError(
                "unsupported_provider", "Tool review is available only for remote MCP servers"
            )
        source = await self.repository.get_source(connector_id, source_id)
        if source is None:
            raise ConnectorError("not_found", "Remote MCP tool was not found")
        try:
            row = await self.repository.review_remote_tool(
                source,
                enabled=enabled,
                approved_purpose=approved_purpose,
                user_id=user_id,
            )
        except ValueError as exc:
            raise ConnectorError("invalid_tool_review", str(exc)) from exc
        await self.repository.audit(
            user_id=user_id,
            username=username,
            action="connector.tool.review",
            resource_type="connector_source",
            resource_id=source_id,
            details={"connector_id": connector_id, "enabled": enabled},
        )
        return row

    async def call_remote_tool(
        self,
        *,
        project_id: str,
        connector_id: str,
        source_id: str,
        arguments: dict[str, Any],
        user_id: int,
        username: str | None,
    ) -> dict[str, Any]:
        instance = await self._required_instance(connector_id)
        if (
            instance.provider_id != "remote_mcp"
            or not instance.enabled
            or instance.status != "connected"
        ):
            raise ConnectorError("connector_unavailable", "Remote MCP connector is unavailable")
        binding = await self.repository.get_binding(project_id, connector_id)
        if (
            binding is None
            or not binding.enabled
            or source_id not in (binding.source_ids or [])
            or REMOTE_MCP_CALL_CAPABILITY not in (binding.capability_ids or [])
        ):
            raise ConnectorError("scope_denied", "Remote MCP tool is outside project scope")
        source = await self.repository.get_source(connector_id, source_id)
        profile = dict(source.field_profile or {}) if source is not None else {}
        if (
            source is None
            or source.source_type != "mcp_tool"
            or not source.enabled
            or profile.get("review_status") != "approved"
            or profile.get("approved_risk") != "read_only"
        ):
            raise ConnectorError("tool_unavailable", "Remote MCP tool is not approved")
        provider = connector_catalog.provider(instance.provider_id)
        result = await provider.call_reviewed_tool(
            instance_config(instance),
            source_profile(source, self.repository.secrets),
            self._credential(instance),
            arguments,
        )
        await self.repository.audit(
            user_id=user_id,
            username=username,
            action="connector.tool.call",
            resource_type="project_connector_binding",
            resource_id=binding.id,
            details={
                "project_id": project_id,
                "connector_id": connector_id,
                "source_id": source_id,
                "status": "provider_error" if result.get("is_error") else "succeeded",
            },
        )
        return {
            "connector_id": connector_id,
            "source_id": source_id,
            "tool_label": source.display_label[:120],
            **result,
        }

    async def kibana_spaces(self, connector_id: str) -> list[dict[str, str]]:
        instance = await self._required_instance(connector_id)
        if instance.provider_id != "elastic_stack":
            raise ConnectorError("unsupported_provider", "Kibana discovery is available only for Elastic Stack")
        credential = self.repository.secrets.decrypt_for_provider(
            instance.kibana_credential_ciphertext or instance.credential_ciphertext
        )
        return await KibanaDiscoveryProvider().list_spaces(instance_config(instance), credential)

    async def kibana_data_views(self, connector_id: str, space: str) -> list[dict[str, str]]:
        instance = await self._required_instance(connector_id)
        if instance.provider_id != "elastic_stack":
            raise ConnectorError("unsupported_provider", "Kibana discovery is available only for Elastic Stack")
        credential = self.repository.secrets.decrypt_for_provider(
            instance.kibana_credential_ciphertext or instance.credential_ciphertext
        )
        return await KibanaDiscoveryProvider().list_data_views(instance_config(instance), credential, space)

    async def upsert_binding(self, *, project_id: str, connector_id: str, source_ids: list[str], capability_ids: list[str],
                             budgets: Mapping[str, int], max_time_window_days: int | None, enabled: bool,
                             user_id: int, username: str | None) -> object:
        instance = await self._required_instance(connector_id)
        if not await self.repository.project_exists(project_id):
            raise ConnectorError("project_not_found", "Project does not exist")
        connector_catalog.validate_grant(instance.provider_id, capability_ids)
        sources = await self.repository.get_sources(connector_id, source_ids)
        if len(sources) != len(set(source_ids)):
            raise ConnectorError("invalid_source", "Binding contains a source from another connector")
        if not source_ids or not capability_ids:
            raise ValueError("Connector binding requires at least one source and capability")
        if any(not source.enabled for source in sources):
            raise ConnectorError("invalid_source", "Binding contains a disabled source")
        for value in budgets.values():
            if not isinstance(value, int) or value < 0:
                raise ValueError("Connector budgets must be non-negative integers")
        binding = await self.repository.upsert_binding(
            project_id=project_id, connector_instance_id=connector_id, source_ids=source_ids,
            capability_ids=capability_ids, budgets=budgets, max_time_window_days=max_time_window_days,
            enabled=enabled, user_id=user_id,
        )
        await self.repository.audit(user_id=user_id, username=username, action="connector.binding.upsert",
                                    resource_type="project_connector_binding", resource_id=binding.id,
                                    details={"project_id": project_id, "connector_id": connector_id,
                                             "version": binding.version, "source_count": len(source_ids),
                                             "capability_count": len(capability_ids)})
        return binding

    async def delete_instance(self, connector_id: str, *, user_id: int, username: str | None) -> None:
        instance = await self._required_instance(connector_id)
        if await self.repository.active_binding_count(connector_id):
            raise ConnectorError("active_bindings", "Remove active project bindings before deleting this connector")
        await self.repository.delete_instance(instance)
        await self.repository.audit(user_id=user_id, username=username, action="connector.delete",
                                    resource_type="connector", resource_id=connector_id, details={})

    async def _required_instance(self, connector_id: str) -> object:
        instance = await self.repository.get_instance(connector_id)
        if instance is None:
            raise ConnectorError("connector_not_found", "Connector was not found")
        return instance

    def _credential(self, instance: object) -> str | None:
        credential_type = getattr(instance, "credential_type", None)
        ciphertext = getattr(instance, "credential_ciphertext", None)
        if credential_type == "none":
            return None
        if not isinstance(ciphertext, str) or not ciphertext:
            raise ConnectorError("credential_unavailable", "Connector credential is unavailable")
        return self.repository.secrets.decrypt_for_provider(ciphertext)
