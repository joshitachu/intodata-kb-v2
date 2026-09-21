"""Policy, health, execution, and redaction boundary for connectors."""

from __future__ import annotations

import re
from typing import Any

from .models import (
    CapabilityBindingCreate,
    CapabilityBindingView,
    ConnectorAuthMode,
    ConnectorHealthResult,
    ConnectorHealthStatus,
    ConnectorInstanceCreate,
    ConnectorInstanceUpdate,
    ConnectorInstanceView,
    ConnectorQuery,
    ConnectorReadResult,
)
from .providers import ConnectorProviderRegistry
from .repository import ConnectorRepository

_SENSITIVE_KEY = re.compile(r"(credential|secret|token|password|api[_-]?key)", re.I)


class ConnectorPolicyError(RuntimeError):
    pass


def redact(value: Any, fields: set[str] | None = None) -> Any:
    fields = fields or set()
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key in fields or _SENSITIVE_KEY.search(key) else redact(item, fields)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, fields) for item in value]
    return value


class ConnectorService:
    def __init__(self, repository: ConnectorRepository, providers: ConnectorProviderRegistry) -> None:
        self.repository = repository
        self.providers = providers

    async def create(self, data: ConnectorInstanceCreate):
        provider = self.providers.get(data.provider_key)
        if data.auth_mode not in self.providers.manifest(data.provider_key).auth_modes:
            raise ConnectorPolicyError("Unsupported authentication mode")
        if data.auth_mode == ConnectorAuthMode.NONE and data.credential_reference is not None:
            raise ConnectorPolicyError("Credential reference requires credential_reference auth mode")
        if data.auth_mode == ConnectorAuthMode.CREDENTIAL_REFERENCE and not data.credential_reference:
            raise ConnectorPolicyError("Credential reference is required")
        config = provider.validate_config(data.config)
        return await self.repository.create_instance(data, config)

    async def list(self):
        return await self.repository.list_instances()

    async def update(self, instance_id: int, data: ConnectorInstanceUpdate):
        row = await self.repository.get_instance(instance_id)
        config = self.providers.get(row.provider_key).validate_config(data.config) if data.config is not None else None
        return await self.repository.update_instance(row, data, config)

    async def bind(self, instance_id: int, data: CapabilityBindingCreate):
        row = await self.repository.get_instance(instance_id)
        manifest = self.providers.manifest(row.provider_key)
        if data.capability_id not in manifest.capability_ids:
            raise ConnectorPolicyError("Capability is not provided by this connector")
        return await self.repository.add_binding(instance_id, data)

    async def list_bindings(self, instance_id: int):
        row = await self.repository.get_instance(instance_id, bindings=True)
        return list(row.capability_bindings)

    async def delete(self, instance_id: int) -> None:
        row = await self.repository.get_instance(instance_id)
        await self.repository.delete_instance(row)

    async def unbind(self, instance_id: int, binding_id: int) -> None:
        await self.repository.delete_binding(instance_id, binding_id)

    async def check_health(self, instance_id: int) -> ConnectorHealthResult:
        row = await self.repository.get_instance(instance_id)
        if not row.enabled:
            raise ConnectorPolicyError("Connector is disabled")
        try:
            result = await self.providers.get(row.provider_key).health(row.config, row.credential_reference)
        except Exception:
            result = ConnectorHealthResult(
                status=ConnectorHealthStatus.UNHEALTHY, summary="Connector health check failed"
            )
        await self.repository.set_health(row, result.status.value, result.summary)
        return result

    async def read(
        self, instance_id: int, capability_id: str, project_id: str | None,
        domain: str | None, query: ConnectorQuery,
    ) -> ConnectorReadResult:
        row = await self.repository.get_instance(instance_id, bindings=True)
        binding = next((item for item in row.capability_bindings if item.capability_id == capability_id), None)
        if not row.enabled or row.health_status != ConnectorHealthStatus.HEALTHY.value:
            raise ConnectorPolicyError("Connector is unavailable")
        if binding is None or not binding.enabled:
            raise ConnectorPolicyError("Connector capability is disabled")
        if binding.project_ids and project_id not in binding.project_ids:
            raise ConnectorPolicyError("Connector is outside project scope")
        if binding.domains and domain not in binding.domains:
            raise ConnectorPolicyError("Connector is outside domain scope")
        result = await self.providers.get(row.provider_key).read(capability_id, query, row.config)
        return ConnectorReadResult.model_validate(redact(result.model_dump(), set(binding.redaction_fields)))


def instance_view(row) -> ConnectorInstanceView:
    reference_type = row.credential_reference.split(":", 1)[0] if row.credential_reference else None
    return ConnectorInstanceView(
        id=row.id, name=row.name, provider_key=row.provider_key, config=redact(row.config),
        auth_mode=row.auth_mode, credential_reference_type=reference_type,
        credential_present=row.credential_reference is not None, enabled=row.enabled,
        health_status=row.health_status, health_summary=row.health_summary,
        health_checked_at=row.health_checked_at, created_at=row.created_at, updated_at=row.updated_at,
    )


def binding_view(row) -> CapabilityBindingView:
    return CapabilityBindingView.model_validate(
        {key: getattr(row, key) for key in CapabilityBindingView.model_fields}
    )
