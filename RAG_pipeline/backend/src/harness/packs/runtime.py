"""Compile operator-selected Nexus packs into executable request policy."""

from __future__ import annotations

from pathlib import Path

from src.config import settings
from src.harness.models import CapabilityDefinition
from src.harness.connectors.catalog import default_connector_provider_registry
from src.harness.packs import FIXTURE_PACK_DIRECTORY
from src.harness.packs.catalog import PackCatalog
from src.harness.packs.models import (
    CompiledPackStack,
    PackActivation,
    PackPhase,
    PackReferenceSets,
    ResolvedPackSet,
)
from src.harness.registry import CapabilityRegistry


class PackRuntimeError(ValueError):
    """A legacy or malformed Pack cannot grant runtime authority."""


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
    return _compile_pack_registry(
        registry,
        manifests=resolved.manifests,
        snapshot=resolved.snapshot.model_dump(mode="json"),
    )


def compile_governed_pack_registry(
    registry: CapabilityRegistry,
    compiled: CompiledPackStack,
    private_analysis_packs: tuple[dict[str, object], ...] = (),
) -> CapabilityRegistry:
    """Compile a persisted governed stack without leaking Style into semantics.

    The compiled snapshot is the execution authority.  Presentation travels as
    receipt metadata only and is consumed at answer/document rendering seams.
    """
    return _compile_pack_registry(
        registry,
        manifests=compiled.manifests,
        snapshot=compiled.snapshot.model_dump(mode="json"),
        presentation=(compiled.presentation.model_dump(mode="json") if compiled.presentation else None),
        private_analysis_packs=private_analysis_packs,
    )


def governed_pack_receipt(compiled: CompiledPackStack) -> dict[str, object]:
    """Stable provenance stored on runs, activity, and provider receipts."""
    snapshot = compiled.snapshot
    return {
        "policy_id": snapshot.policy_id,
        "policy_hash": snapshot.policy_hash,
        "compiled_snapshot_hash": snapshot.snapshot_hash,
        "foundation_pack_id": snapshot.foundation_pack_id,
        "functional_pack_ids": list(snapshot.functional_pack_ids),
        "style_pack_ids": list(snapshot.style_pack_ids),
        # Resolved orchestration modules are internal implementation context.
        # User-facing Documentation Packs come from the dedicated catalog tool.
        "presentation": compiled.presentation.model_dump(mode="json") if compiled.presentation else None,
    }


def _compile_pack_registry(
    registry: CapabilityRegistry,
    *,
    manifests: tuple,
    snapshot: dict[str, object],
    presentation: dict[str, object] | None = None,
    private_analysis_packs: tuple[dict[str, object], ...] = (),
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
        if capability.id in allowed:
            compiled.register(
                _with_pack_snapshot(
                    capability,
                    manifests=semantic_manifests,
                    snapshot=snapshot,
                    presentation=presentation,
                    private_analysis_packs=private_analysis_packs,
                )
            )
    return compiled


def _with_pack_snapshot(
    capability: CapabilityDefinition,
    *,
    manifests: tuple,
    snapshot: dict[str, object],
    presentation: dict[str, object] | None,
    private_analysis_packs: tuple[dict[str, object], ...],
) -> CapabilityDefinition:
    capability_pack_ids = sorted(
        manifest.id
        for manifest in manifests
        if any(
            capability.id in workflow.capability_ids
            for workflow in manifest.workflows
        )
    )
    full_stack_ids = _full_stack_ids(snapshot)
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
        "output_rules": [
            manifest.output_rules.model_dump(mode="json")
            for manifest in manifests
        ],
    }
    private_metadata = (
        {"nexus_private_analysis_packs": [dict(item) for item in private_analysis_packs]}
        if capability.id == "mcp.docs.generate_project_documentation" and private_analysis_packs
        else {}
    )
    return capability.model_copy(
        update={
            "metadata": {
                **capability.metadata,
                # MCP validates this against the complete compiled stack,
                # including Style identities. It is not the capability subset.
                "nexus_pack_ids": full_stack_ids,
                "nexus_capability_pack_ids": capability_pack_ids,
                "nexus_pack_snapshot_hash": snapshot["snapshot_hash"],
                "nexus_pack_snapshot": snapshot,
                "nexus_pack_context": application_context,
                # Presentation is deliberately not part of nexus_pack_context.
                "nexus_pack_presentation": presentation,
                # Verified private V3 artifacts are Phase 3-only additive data,
                # not Pack authority or general capability context.
                **private_metadata,
            }
        }
    )


def _full_stack_ids(snapshot: dict[str, object]) -> list[str]:
    selected = [
        snapshot.get("foundation_pack_id"),
        *(snapshot.get("functional_pack_ids") or []),
        *(snapshot.get("style_pack_ids") or []),
    ]
    canonical = [item for item in selected if isinstance(item, str) and item]
    if canonical:
        return canonical

    resolved = snapshot.get("resolved_packs")
    if isinstance(resolved, list):
        identities = [item.get("id") for item in resolved if isinstance(item, dict)]
        if all(isinstance(item, str) and item for item in identities):
            return identities
    return []
