"""Project configured connector bindings into runtime-safe Nexus capabilities."""

from __future__ import annotations

import re

from src.harness.models import (
    CapabilityDefinition,
    EvidenceSource,
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
    rows = await repository.list_eligible_bindings(project_ids)
    for binding in rows:
        instance = binding.connector_instance
        try:
            configured = providers.capability(instance.provider_key, binding.capability_id)
        except KeyError:
            # Catalog changes must not revive a persisted binding with an unknown type.
            continue
        runtime_id = connector_runtime_capability_id(instance.id, binding.capability_id)
        purpose = configured.purpose
        if instance.provider_key == "mcp_elastic":
            scoped_projects = (
                [project_id for project_id in binding.project_ids if project_id in project_ids]
                if project_ids is not None else list(binding.project_ids)
            )
            context = _elastic_field_context(
                getattr(instance, "config", {}), project_ids=scoped_projects
            )
            if context:
                purpose = f"{purpose} {context}"[:500]
        registry.register(
            capability := CapabilityDefinition(
                id=runtime_id,
                label=f"{configured.label}: {instance.name}",
                semantic_name=(
                    f"{configured.semantic_name}_connector_{instance.id}"
                ),
                purpose=purpose,
                preconditions=list(configured.preconditions),
                failure_recovery=dict(configured.failure_recovery),
                shadow_safe=configured.shadow_safe,
                provider_type=HarnessProviderType.SOURCE,
                provider_id="nexus.connector",
                action=configured.action,
                input_schema=configured.input_schema,
                output_type=configured.output_type,
                risk_tier=HarnessRiskTier(configured.risk_tier),
                packs=list(configured.packs),
                use_when=list(configured.use_when),
                do_not_use_when=list(configured.do_not_use_when),
                task_types=["connector_evidence"],
                evidence_sources=[EvidenceSource.CONNECTOR_EVIDENCE],
                output_contracts=[configured.output_type],
                metadata={
                    "execution_surface": "request_bound_connector",
                    "auth_required": True,
                    "connector_instance_id": instance.id,
                    "connector_capability_id": binding.capability_id,
                    "connector_domain_required": bool(binding.domains),
                    "activity_label": configured.activity_label,
                    "evidence_output": True,
                },
            ),
            _bound_connector_handler(capability),
        )
    return registry


_SAFE_ELASTIC_FIELD = re.compile(r"^[A-Za-z0-9@_.-]{1,128}$")
_SAFE_ELASTIC_LABEL = re.compile(r"^[A-Za-z0-9 ._*-]{1,120}$")


def _elastic_field_context(config: object, *, project_ids: list[str] | None = None) -> str:
    """Render only allowlisted schema names, never log samples, into tool guidance."""
    if not isinstance(config, dict):
        return ""
    contexts: object = config.get("source_contexts")
    project_contexts = config.get("project_source_contexts")
    if isinstance(project_contexts, dict) and project_ids:
        contexts = [
            context
            for project_id in project_ids
            for context in (
                project_contexts.get(project_id, [])
                if isinstance(project_contexts.get(project_id), list) else []
            )
        ]
    if not isinstance(contexts, list):
        return ""
    lines: list[str] = []
    for index, item in enumerate(contexts[:3], start=1):
        if not isinstance(item, dict):
            continue
        raw_label = item.get("label")
        label = raw_label if isinstance(raw_label, str) and _SAFE_ELASTIC_LABEL.fullmatch(raw_label) else f"source {index}"
        fields = item.get("search_fields")
        names = list(dict.fromkeys(
            value for value in fields[:6]
            if isinstance(fields, list) and isinstance(value, str) and _SAFE_ELASTIC_FIELD.fullmatch(value)
        )) if isinstance(fields, list) else []
        message_fields = item.get("message_fields")
        messages = list(dict.fromkeys(
            value for value in message_fields[:3]
            if isinstance(message_fields, list) and isinstance(value, str) and _SAFE_ELASTIC_FIELD.fullmatch(value)
        )) if isinstance(message_fields, list) else []
        roles: list[str] = []
        for label_name, key in (
            ("timestamp", "timestamp_field"),
            ("service", "service_field"),
            ("severity", "severity_field"),
            ("environment", "environment_field"),
        ):
            value = item.get(key)
            if isinstance(value, str) and _SAFE_ELASTIC_FIELD.fullmatch(value):
                roles.append(f"{label_name}={value}")
        lines.append(
            f"{label}: searchable fields={', '.join(names) if names else 'none discovered'}; "
            f"event text fields={', '.join(messages) if messages else 'none discovered'}; "
            f"field roles={', '.join(roles) if roles else 'none discovered'}"
        )
    if not lines:
        return ""
    guidance = (
        "Fields by selected source: "
        + " | ".join(lines)
    )
    return guidance[:250]


def _bound_connector_handler(capability: CapabilityDefinition):  # noqa: ANN202
    from src.harness.capability_handlers import BoundConnectorEvidenceHandler

    return BoundConnectorEvidenceHandler(capability)


def connector_runtime_capability_id(instance_id: int, capability_id: str) -> str:
    normalized = capability_id.replace(".", "_")
    return f"connector.instance_{instance_id}.{normalized}"
