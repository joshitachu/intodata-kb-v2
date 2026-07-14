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
    PackReferenceSets,
    ResolvedPackSet,
)
from src.harness.registry import CapabilityRegistry


def active_pack_ids() -> frozenset[str]:
    values = {
        item.strip()
        for item in settings.nexus_active_packs.split(",")
        if item.strip()
    }
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
    return build_pack_catalog(registry).resolve(
        PackActivation(enabled_pack_ids=active_pack_ids())
    )


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
    """Compile an immutable project/workflow Pack selection into capability policy.

    ``ProjectPackService.resolve_for_execution`` supplies ``resolved`` for
    project-scoped runs; the legacy deployment selection uses this same path.
    """
    allowed = active_capability_ids(resolved)
    compiled = CapabilityRegistry()
    for capability in registry.list(enabled_only=False):
        if capability.id in allowed:
            compiled.register(_with_pack_snapshot(capability, resolved))
    return compiled


def _with_pack_snapshot(
    capability: CapabilityDefinition,
    resolved: ResolvedPackSet,
) -> CapabilityDefinition:
    active_for_capability = sorted(
        manifest.id
        for manifest in resolved.manifests
        if any(
            capability.id in workflow.capability_ids
            for workflow in manifest.workflows
        )
    )
    application_context = {
        "packs": [
            {"id": manifest.id, "name": manifest.name, "version": manifest.version}
            for manifest in resolved.manifests
        ],
        "vocabulary": [
            {
                "term": term.term,
                "aliases": list(term.aliases),
                "description": term.description,
            }
            for manifest in resolved.manifests
            for term in manifest.vocabulary
        ],
        "workflows": [
            {
                "id": workflow.id,
                "label": workflow.label,
                "description": workflow.description,
            }
            for manifest in resolved.manifests
            for workflow in manifest.workflows
        ],
        "output_rules": [
            manifest.output_rules.model_dump(mode="json")
            for manifest in resolved.manifests
        ],
    }
    return capability.model_copy(
        update={
            "metadata": {
                **capability.metadata,
                "nexus_pack_ids": active_for_capability,
                "nexus_pack_snapshot_hash": resolved.snapshot.snapshot_hash,
                "nexus_pack_snapshot": resolved.snapshot.model_dump(mode="json"),
                "nexus_pack_context": application_context,
            }
        }
    )
