"""Pack catalog, reference validation, include resolution, and snapshots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path

from src.harness.packs.loader import load_pack_directory
from src.harness.packs.models import (
    PackActivation,
    PackIdentity,
    PackManifest,
    PackReferenceSets,
    PackSnapshot,
    PackView,
    ResolvedPackSet,
)


class PackCatalogError(ValueError):
    """Raised when catalog definitions or activation are inconsistent."""


def manifest_hash(manifest: PackManifest) -> str:
    payload = manifest.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PackCatalog:
    def __init__(
        self,
        manifests: Iterable[PackManifest],
        *,
        allowed_references: PackReferenceSets | None = None,
    ) -> None:
        by_id: dict[str, PackManifest] = {}
        for manifest in manifests:
            if manifest.id in by_id:
                raise PackCatalogError(f"duplicate pack id: {manifest.id}")
            by_id[manifest.id] = manifest
        self._manifests = by_id
        self._allowed = allowed_references or PackReferenceSets()
        self._validate_includes()
        self._validate_references()
        for pack_id in sorted(self._manifests):
            self._validate_resolved_ids(self._closure_for((pack_id,)))

    @classmethod
    def from_directory(
        cls,
        directory: Path,
        *,
        allowed_references: PackReferenceSets | None = None,
    ) -> PackCatalog:
        return cls(load_pack_directory(directory), allowed_references=allowed_references)

    def definitions(self) -> tuple[PackManifest, ...]:
        return tuple(self._manifests[key] for key in sorted(self._manifests))

    def views(self) -> tuple[PackView, ...]:
        return tuple(
            PackView(
                id=manifest.id,
                name=manifest.name,
                version=manifest.version,
                pack_type=manifest.pack_type,
                includes=manifest.includes,
                workflow_labels=tuple(workflow.label for workflow in manifest.workflows),
                workflow_descriptions=tuple(
                    workflow.description for workflow in manifest.workflows
                ),
                entity_labels=tuple(entity.label for entity in manifest.entities),
                documentation_provider_ref=manifest.documentation_provider_ref,
                content_hash=manifest_hash(manifest),
            )
            for manifest in self.definitions()
        )

    def resolve(self, activation: PackActivation) -> ResolvedPackSet:
        unknown = sorted(set(activation.enabled_pack_ids) - self._manifests.keys())
        if unknown:
            raise PackCatalogError(f"unknown active pack reference(s): {', '.join(unknown)}")

        manifests = self._closure_for(activation.enabled_pack_ids)
        self._validate_resolved_ids(manifests)
        identities = tuple(
            PackIdentity(
                id=manifest.id, version=manifest.version, content_hash=manifest_hash(manifest)
            )
            for manifest in manifests
        )
        active_ids = tuple(sorted(activation.enabled_pack_ids))
        snapshot_payload = {
            "active_pack_ids": active_ids,
            "resolved_packs": [identity.model_dump(mode="json") for identity in identities],
        }
        canonical = json.dumps(snapshot_payload, sort_keys=True, separators=(",", ":"))
        snapshot = PackSnapshot(
            active_pack_ids=active_ids,
            resolved_packs=identities,
            snapshot_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )
        return ResolvedPackSet(manifests=manifests, snapshot=snapshot)

    def _closure_for(self, root_ids: Iterable[str]) -> tuple[PackManifest, ...]:
        resolved_ids: set[str] = set()

        def collect(pack_id: str) -> None:
            if pack_id in resolved_ids:
                return
            for included_id in sorted(self._manifests[pack_id].includes):
                collect(included_id)
            resolved_ids.add(pack_id)

        for pack_id in sorted(root_ids):
            collect(pack_id)
        return tuple(self._manifests[pack_id] for pack_id in sorted(resolved_ids))

    def _validate_includes(self) -> None:
        for manifest in self._manifests.values():
            unknown = sorted(set(manifest.includes) - self._manifests.keys())
            if unknown:
                raise PackCatalogError(
                    f"pack {manifest.id} includes unknown pack reference(s): {', '.join(unknown)}"
                )

        visiting: list[str] = []
        visited: set[str] = set()

        def visit(pack_id: str) -> None:
            if pack_id in visiting:
                cycle = visiting[visiting.index(pack_id) :] + [pack_id]
                raise PackCatalogError(f"pack include cycle: {' -> '.join(cycle)}")
            if pack_id in visited:
                return
            visiting.append(pack_id)
            for included_id in sorted(self._manifests[pack_id].includes):
                visit(included_id)
            visiting.pop()
            visited.add(pack_id)

        for pack_id in sorted(self._manifests):
            visit(pack_id)

    def _validate_references(self) -> None:
        allowed = self._allowed
        for manifest in self._manifests.values():
            self._check_allowed(
                manifest.id,
                "documentation provider",
                [manifest.documentation_provider_ref]
                if manifest.documentation_provider_ref
                else [],
                allowed.documentation_provider_refs,
            )
            self._check_allowed(
                manifest.id,
                "capability",
                (
                    capability
                    for workflow in manifest.workflows
                    for capability in workflow.capability_ids
                ),
                allowed.capability_ids,
            )
            self._check_allowed(
                manifest.id,
                "capability",
                (
                    capability
                    for requirement in manifest.connector_requirements
                    for capability in requirement.capability_ids
                ),
                allowed.capability_ids,
            )
            self._check_allowed(
                manifest.id,
                "connector provider",
                (requirement.provider_key for requirement in manifest.connector_requirements),
                allowed.connector_provider_keys,
            )
            if allowed.evidence_types is not None:
                self._check_allowed(
                    manifest.id,
                    "evidence type",
                    (rule.evidence_type for rule in manifest.evidence_ranking),
                    allowed.evidence_types,
                )
            if allowed.source_kinds is not None:
                self._check_allowed(
                    manifest.id,
                    "source kind",
                    (rule.source_kind for rule in manifest.evidence_ranking if rule.source_kind),
                    allowed.source_kinds,
                )

    @staticmethod
    def _check_allowed(
        pack_id: str, label: str, values: Iterable[str], allowed: frozenset[str]
    ) -> None:
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise PackCatalogError(
                f"pack {pack_id} has unknown {label} reference(s): {', '.join(unknown)}"
            )

    @staticmethod
    def _validate_resolved_ids(manifests: tuple[PackManifest, ...]) -> None:
        for label, values in (
            (
                "entity",
                ((item.id, manifest.id) for manifest in manifests for item in manifest.entities),
            ),
            (
                "workflow",
                ((item.id, manifest.id) for manifest in manifests for item in manifest.workflows),
            ),
            (
                "artifact template",
                (
                    (item.id, manifest.id)
                    for manifest in manifests
                    for item in manifest.artifact_templates
                ),
            ),
            (
                "connector requirement",
                (
                    (item.id, manifest.id)
                    for manifest in manifests
                    for item in manifest.connector_requirements
                ),
            ),
        ):
            owners: dict[str, str] = {}
            for item_id, owner in values:
                if item_id in owners:
                    raise PackCatalogError(
                        f"duplicate resolved {label} id {item_id} in packs {owners[item_id]} and {owner}"
                    )
                owners[item_id] = owner

        entity_ids = {item.id for manifest in manifests for item in manifest.entities}
        artifact_ids = {item.id for manifest in manifests for item in manifest.artifact_templates}
        requirement_ids = {
            item.id for manifest in manifests for item in manifest.connector_requirements
        }
        for manifest in manifests:
            for workflow in manifest.workflows:
                for label, refs, known in (
                    ("entity", workflow.entity_type_ids, entity_ids),
                    ("artifact template", workflow.artifact_template_ids, artifact_ids),
                    ("connector requirement", workflow.connector_requirement_ids, requirement_ids),
                ):
                    unknown = sorted(set(refs) - known)
                    if unknown:
                        raise PackCatalogError(
                            f"workflow {workflow.id} has unknown {label} reference(s): {', '.join(unknown)}"
                        )
