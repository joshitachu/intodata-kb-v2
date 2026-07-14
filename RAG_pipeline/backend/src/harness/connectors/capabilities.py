"""Project configured connector bindings into runtime-safe Nexus capabilities."""

from __future__ import annotations

from src.harness.models import (
    CapabilityDefinition,
    HarnessProviderType,
    HarnessRiskTier,
)
from src.harness.registry import CapabilityRegistry, default_capability_registry

from .catalog import default_connector_provider_registry
from .repository import ConnectorRepository


async def build_request_capability_registry(
    repository: ConnectorRepository,
    *,
    project_ids: list[str] | None,
) -> CapabilityRegistry:
    registry = default_capability_registry()
    providers = default_connector_provider_registry()
    connector_base = next(
        (
            capability
            for capability in registry.list(enabled_only=False)
            if capability.id == "external.connector.read_evidence"
        ),
        None,
    )
    if connector_base is None:
        return registry
    rows = await repository.list_eligible_bindings(project_ids)
    for binding in rows:
        instance = binding.connector_instance
        try:
            configured = providers.capability(instance.provider_key, binding.capability_id)
        except KeyError:
            # Catalog changes must not revive a persisted binding with an unknown type.
            continue
        runtime_id = connector_runtime_capability_id(instance.id, binding.capability_id)
        registry.register(
            CapabilityDefinition(
                id=runtime_id,
                label=f"{configured.label}: {instance.name}",
                provider_type=HarnessProviderType.SOURCE,
                provider_id="nexus.connector",
                action=configured.action,
                input_schema=configured.input_schema,
                output_type=configured.output_type,
                risk_tier=HarnessRiskTier(configured.risk_tier),
                packs=list(configured.packs),
                metadata={
                    **connector_base.metadata,
                    "connector_instance_id": instance.id,
                    "connector_capability_id": binding.capability_id,
                    "connector_domain_required": bool(binding.domains),
                    "activity_label": configured.activity_label,
                    "evidence_output": True,
                },
            )
        )
    return registry


def connector_runtime_capability_id(instance_id: int, capability_id: str) -> str:
    normalized = capability_id.replace(".", "_")
    return f"connector.instance_{instance_id}.{normalized}"
