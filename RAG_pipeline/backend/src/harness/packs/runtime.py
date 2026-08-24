"""Compile operator-selected Nexus packs into executable request policy."""

from __future__ import annotations

from pathlib import Path

from src.config import settings
from src.harness.models import CapabilityDefinition
from src.harness.connectors.catalog import default_connector_provider_registry
from src.harness.packs import FIXTURE_PACK_DIRECTORY
from src.harness.packs.catalog import PackCatalog
from src.harness.packs.models import (
    PackActivation,
    PackPhase,
    PackReferenceSets,
    ResolvedPackSet,
)
from src.harness.registry import CapabilityRegistry


class PackRuntimeError(ValueError):
    """A legacy or malformed Pack cannot grant runtime authority."""


def active_pack_ids() -> frozenset[str]:
    values = {item.strip() for item in settings.nexus_active_packs.split(",") if item.strip()}
    return frozenset(values)


def pack_directory() -> Path:
    configured = settings.nexus_pack_directory.strip()
    return Path(configured).expanduser() if configured else FIXTURE_PACK_DIRECTORY


def build_pack_catalog(registry: CapabilityRegistry) -> PackCatalog:
    return PackCatalog.from_directory(
        pack_directory(), allowed_references=build_pack_reference_sets(registry)
    )


def build_pack_reference_sets(registry: CapabilityRegistry) -> PackReferenceSets:
    """Reference whitelist shared by fixture and persisted data-only Packs."""
    capability_ids = frozenset(item.id for item in registry.list(enabled_only=False))
    connector_provider_keys = default_connector_provider_registry().provider_keys()
    return PackReferenceSets(
        capability_ids=capability_ids,
        connector_provider_keys=connector_provider_keys,
        documentation_provider_refs=frozenset({"mcp.docs.default"}),
        evidence_types=frozenset({"document", "runtime_incident", "connector_item"}),
        source_kinds=frozenset({"documentation", "runtime", "connector"}),
    )


def resolve_active_packs(registry: CapabilityRegistry) -> ResolvedPackSet:
    return build_pack_catalog(registry).resolve(PackActivation(enabled_pack_ids=active_pack_ids()))


def active_capability_ids(resolved: ResolvedPackSet) -> frozenset[str]:
    return frozenset(
        capability_id
        for manifest in resolved.manifests
        for workflow in manifest.workflows
        for capability_id in workflow.capability_ids
    )


def compile_active_pack_registry(registry: CapabilityRegistry) -> CapabilityRegistry:
    """Return exactly the capabilities enabled by the active pack closure."""
    resolved = resolve_active_packs(registry)
    return compile_resolved_pack_registry(registry, resolved)


def compile_resolved_pack_registry(
    registry: CapabilityRegistry,
    resolved: ResolvedPackSet,
) -> CapabilityRegistry:
    """Compile the static fixture allowlist into capability policy.

    These fixtures are the agent-chat tool allowlist, not project-selectable
    documentation packs. They deliberately carry no documentation-pack or
    presentation metadata across the RAG/MCP boundary.
    """
    return _compile_pack_registry(
        registry,
        manifests=resolved.manifests,
        snapshot=resolved.snapshot.model_dump(mode="json"),
    )


def _compile_pack_registry(
    registry: CapabilityRegistry,
    *,
    manifests: tuple,
    snapshot: dict[str, object],
    presentation: dict[str, object] | None = None,
) -> CapabilityRegistry:
    legacy = [manifest.id for manifest in manifests if manifest.governance is None]
    if legacy:
        raise PackRuntimeError(
            "Legacy Pack manifests cannot grant runtime authority: " + ", ".join(sorted(legacy))
        )
    semantic_manifests = tuple(
        manifest for manifest in manifests if manifest.governance.phase is not PackPhase.STYLE
    )
    allowed = frozenset(
        capability_id
        for manifest in semantic_manifests
        for workflow in manifest.workflows
        for capability_id in workflow.capability_ids
    )
    compiled = CapabilityRegistry()
    for capability in registry.list(enabled_only=False):
        if capability.id in allowed or capability.metadata.get("agent_control_plane") is True:
            compiled.register(
                _with_pack_snapshot(
                    capability,
                    manifests=semantic_manifests,
                    snapshot=snapshot,
                    presentation=presentation,
                            ),
                registry.handler_for(capability.id),
            )
    return compiled

def _with_pack_snapshot(
    capability: CapabilityDefinition,
    *,
    manifests: tuple,
    snapshot: dict[str, object],
    presentation: dict[str, object] | None,
) -> CapabilityDefinition:
    capability_pack_ids = sorted(
        manifest.id
        for manifest in manifests
        if any(capability.id in workflow.capability_ids for workflow in manifest.workflows)
    )
    application_context = {
        # These manifests configure orchestration and policy. They are not
        # user-selectable Documentation Packs and must not be presented as such.
        "vocabulary": [
            {
                "term": term.term,
                "aliases": list(term.aliases),
                "description": term.description,
            }
            for manifest in manifests
            for term in manifest.vocabulary
        ],
        "workflows": [
            {
                "id": workflow.id,
                "label": workflow.label,
                "description": workflow.description,
            }
            for manifest in manifests
            for workflow in manifest.workflows
        ],
        "output_rules": [manifest.output_rules.model_dump(mode="json") for manifest in manifests],
    }
    return capability.model_copy(
        update={
            "metadata": {
                **capability.metadata,
                # Static fixture provenance remains local to the chat
                # capability registry. It must never become a second pack
                # selection transported to MCP generation.
                "capability_fixture_ids": capability_pack_ids,
                "capability_fixture_context": application_context,
            }
        }
    )
