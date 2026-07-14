"""Runtime-safe capability manifests for Nexus agent runtimes."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field

from src.harness.models import CapabilityDefinition, CapabilityHealth
from src.harness.registry import CapabilityRegistry


_SAFE_METADATA_KEYS = {
    "activity_label",
    "evidence_output",
    "redaction_policy",
    "requires_approval",
    "requires_explicit_user_evidence",
    "requires_named_target_doc",
}


class ManifestScope(BaseModel):
    """Caller context used to filter the runtime-safe manifest."""

    project_ids: list[str] | None = None
    permissions: set[str] | None = None
    packs: set[str] | None = None
    include_unavailable: bool = False


class RuntimeCapabilityTool(BaseModel):
    """Capability shape exposed to an agent runtime.

    This is intentionally not the internal provider definition. Runtimes need
    capability ids and schemas, but not provider ids, credentials, raw metadata,
    or implementation notes.
    """

    id: str
    label: str
    action: str
    provider_type: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_type: str
    risk_tier: str
    required_permissions: list[str] = Field(default_factory=list)
    packs: list[str] = Field(default_factory=list)
    use_when: list[str] = Field(default_factory=list)
    do_not_use_when: list[str] = Field(default_factory=list)
    task_types: list[str] = Field(default_factory=list)
    evidence_sources: list[str] = Field(default_factory=list)
    output_contracts: list[str] = Field(default_factory=list)
    activity_label: str
    requires_approval: bool = False
    redaction_policy: dict[str, Any] = Field(default_factory=dict)
    evidence_output: bool = False
    available: bool = True


class RuntimeCapabilityManifest(BaseModel):
    """Filtered tool manifest for a model/graph runtime."""

    tools: list[RuntimeCapabilityTool]
    total: int
    filtered: int
    pack_snapshot: dict[str, Any] = Field(default_factory=dict)
    application_context: dict[str, Any] = Field(default_factory=dict)


def build_runtime_capability_manifest(
    registry: CapabilityRegistry,
    *,
    scope: ManifestScope | None = None,
) -> RuntimeCapabilityManifest:
    """Build a filtered, provider-agnostic manifest for agent runtimes."""

    scope = scope or ManifestScope()
    all_capabilities = registry.list(enabled_only=False)
    tools = [
        _to_runtime_tool(capability)
        for capability in all_capabilities
        if _is_visible_to_runtime(capability, scope)
    ]
    tools.sort(key=lambda tool: tool.id)
    pack_snapshot: dict[str, Any] = {}
    application_context: dict[str, Any] = {}
    for capability in all_capabilities:
        if isinstance(capability.metadata.get("nexus_pack_snapshot"), dict):
            pack_snapshot = dict(capability.metadata["nexus_pack_snapshot"])
        if isinstance(capability.metadata.get("nexus_pack_context"), dict):
            application_context = dict(capability.metadata["nexus_pack_context"])
        if pack_snapshot and application_context:
            break
    return RuntimeCapabilityManifest(
        tools=tools,
        total=len(tools),
        filtered=len(all_capabilities) - len(tools),
        pack_snapshot=pack_snapshot,
        application_context=application_context,
    )


def user_facing_capability_lines(tools: Iterable[RuntimeCapabilityTool]) -> list[str]:
    """Summarize tools without exposing internal capability ids."""

    labels = sorted({tool.label for tool in tools})
    return [f"- {label}" for label in labels]


def _is_visible_to_runtime(capability: CapabilityDefinition, scope: ManifestScope) -> bool:
    if capability.metadata.get("runtime_hidden") is True:
        return False
    if not scope.include_unavailable:
        if not capability.enabled or capability.health == CapabilityHealth.UNAVAILABLE:
            return False
    if scope.permissions is not None:
        required = set(capability.required_permissions)
        if required and not required.issubset(scope.permissions):
            return False
    if scope.packs is not None and capability.packs:
        if not set(capability.packs).intersection(scope.packs):
            return False
    return True


def _to_runtime_tool(capability: CapabilityDefinition) -> RuntimeCapabilityTool:
    safe_metadata = {
        key: value
        for key, value in capability.metadata.items()
        if key in _SAFE_METADATA_KEYS
    }
    return RuntimeCapabilityTool(
        id=capability.id,
        label=capability.label,
        action=capability.action,
        provider_type=capability.provider_type.value,
        input_schema=capability.input_schema,
        output_type=capability.output_type,
        risk_tier=capability.risk_tier.value,
        required_permissions=list(capability.required_permissions),
        packs=list(capability.packs),
        use_when=list(capability.use_when),
        do_not_use_when=list(capability.do_not_use_when),
        task_types=list(capability.task_types),
        evidence_sources=list(capability.evidence_sources),
        output_contracts=list(capability.output_contracts),
        activity_label=str(safe_metadata.get("activity_label") or capability.label),
        requires_approval=bool(safe_metadata.get("requires_approval")),
        redaction_policy=dict(safe_metadata.get("redaction_policy") or {}),
        evidence_output=bool(
            safe_metadata.get("evidence_output")
            or capability.output_type == "evidence_items"
            or capability.output_type.endswith("_evidence")
        ),
        available=capability.enabled and capability.health != CapabilityHealth.UNAVAILABLE,
    )
