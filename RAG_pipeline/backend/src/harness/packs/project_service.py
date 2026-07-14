"""Project-scoped Pack catalog, recommendations, and immutable persistence."""

from __future__ import annotations

import datetime
import json
import re
from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Document,
    HarnessCustomPackVersionRow,
    HarnessPackActivationSnapshotRow,
    HarnessProjectPackActivationRow,
    RuntimeSourceRow,
)
from src.harness.packs.catalog import PackCatalog, PackCatalogError, manifest_hash
from src.harness.packs.models import (
    PackActivation,
    PackManifest,
    PackRecommendation,
    PackSnapshot,
    ProjectPackActivation,
    ResolvedPackSet,
)
from src.harness.packs.runtime import build_pack_catalog, build_pack_reference_sets
from src.harness.registry import CapabilityRegistry

DEFAULT_WORKFLOW_KEY = "default"
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+][a-zA-Z0-9.-]+)?$")
_FORBIDDEN_TEXT = re.compile(
    r"(?:```|\b(?:system\s*prompt|prompt\s*template|api[_ -]?key|secret|password|token|credential)\b|"
    r"\b(?:import|exec|eval|subprocess)\b|\$\{|<script)",
    re.IGNORECASE,
)
_WORKFLOW_KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")


class PackServiceError(ValueError):
    """A safe client-facing Pack validation or persistence error."""


def parse_custom_manifest(payload: dict[str, Any]) -> PackManifest:
    """Parse user JSON and reject content that could be interpreted as instructions or secrets."""
    try:
        manifest = PackManifest.model_validate_json(json.dumps(payload), strict=True)
    except (ValidationError, TypeError, ValueError) as exc:
        raise PackServiceError(f"Invalid Pack manifest: {exc}") from exc
    if not manifest.id.startswith("custom."):
        raise PackServiceError("Custom Pack ids must start with custom.")
    for text in _string_values(manifest.model_dump(mode="json")):
        if _FORBIDDEN_TEXT.search(text):
            raise PackServiceError("Pack data cannot contain prompts, executable code, or secrets")
    return manifest


def _string_values(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _string_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _string_values(child)


def _semver(value: str) -> tuple[int, int, int]:
    match = _SEMVER.match(value)
    if not match:
        raise PackServiceError("Pack version must use semantic versioning")
    return tuple(int(part) for part in match.groups())


class ProjectPackService:
    def __init__(self, db: AsyncSession, registry: CapabilityRegistry) -> None:
        self._db = db
        self._registry = registry

    async def catalog(self, project_id: str | None = None) -> PackCatalog:
        manifests = list(build_pack_catalog(self._registry).definitions())
        if project_id:
            manifests.extend(await self._latest_custom_manifests(project_id))
        return PackCatalog(manifests, allowed_references=build_pack_reference_sets(self._registry))

    async def available(self, project_id: str | None = None) -> tuple[PackManifest, ...]:
        return (await self.catalog(project_id)).definitions()

    async def recommendations(self, project_id: str) -> tuple[PackRecommendation, ...]:
        catalog = await self.catalog(project_id)
        has_documents = bool(
            (await self._db.execute(
                select(Document.id).where(Document.project_id == project_id).limit(1)
            )).scalar_one_or_none()
        )
        runtime_sources = list(
            (await self._db.execute(
                select(RuntimeSourceRow.source_type).where(
                    RuntimeSourceRow.project_id == project_id,
                    RuntimeSourceRow.enabled.is_(True),
                )
            )).scalars()
        )
        results: list[PackRecommendation] = []
        for manifest in catalog.definitions():
            reasons: list[str] = []
            confidence = 0.45
            domains = set(manifest.default_scopes.domains)
            if "documentation" in domains:
                confidence += 0.24
                reasons.append("Supports project documentation workflows")
                if has_documents:
                    confidence += 0.14
                    reasons.append("Project already has indexed documents")
            if runtime_sources and ({"operations", "data-integration"} & domains):
                confidence += 0.27
                reasons.append("Enabled runtime source metadata matches operational workflows")
            if manifest.pack_type.value == "domain" and not runtime_sources:
                reasons.append("Available for this project; no enabled runtime source raised confidence")
            if not reasons:
                reasons.append("Available for this project")
            results.append(
                PackRecommendation(
                    pack_id=manifest.id,
                    confidence=round(min(confidence, 0.98), 2),
                    reasons=tuple(reasons[:3]),
                )
            )
        return tuple(sorted(results, key=lambda item: (-item.confidence, item.pack_id)))

    async def current_activation(
        self, project_id: str, workflow_key: str = DEFAULT_WORKFLOW_KEY
    ) -> ProjectPackActivation | None:
        _validate_workflow_key(workflow_key)
        row = await self._activation_row(project_id, workflow_key)
        if row is None:
            return None
        snapshot = await self._db.get(HarnessPackActivationSnapshotRow, row.snapshot_id)
        if snapshot is None:
            raise PackServiceError("Pack activation snapshot is unavailable")
        return ProjectPackActivation(
            project_id=project_id,
            workflow_key=workflow_key,
            active_pack_ids=tuple(snapshot.active_pack_ids),
            snapshot=_parse_snapshot(snapshot.resolved_snapshot),
        )

    async def activate(
        self,
        *,
        project_id: str,
        workflow_key: str,
        active_pack_ids: Iterable[str],
        user_id: int,
    ) -> ProjectPackActivation:
        _validate_workflow_key(workflow_key)
        catalog = await self.catalog(project_id)
        try:
            resolved = catalog.resolve(PackActivation(enabled_pack_ids=frozenset(active_pack_ids)))
        except PackCatalogError as exc:
            raise PackServiceError(str(exc)) from exc
        snapshot = await self._persist_snapshot(
            project_id=project_id,
            workflow_key=workflow_key,
            resolved=resolved,
            user_id=user_id,
        )
        current = await self._activation_row(project_id, workflow_key)
        now = datetime.datetime.now(datetime.UTC)
        if current is None:
            self._db.add(HarnessProjectPackActivationRow(
                project_id=project_id,
                workflow_key=workflow_key,
                snapshot_id=snapshot.id,
                updated_by_user_id=user_id,
                updated_at=now,
            ))
        else:
            current.snapshot_id = snapshot.id
            current.updated_by_user_id = user_id
            current.updated_at = now
        await self._db.commit()
        return ProjectPackActivation(
            project_id=project_id,
            workflow_key=workflow_key,
            active_pack_ids=tuple(snapshot.active_pack_ids),
            snapshot=_parse_snapshot(snapshot.resolved_snapshot),
        )

    async def create_custom(
        self, *, project_id: str, payload: dict[str, Any], user_id: int
    ) -> PackManifest:
        manifest = parse_custom_manifest(payload)
        existing = await self._db.scalar(
            select(HarnessCustomPackVersionRow.id).where(
                HarnessCustomPackVersionRow.project_id == project_id,
                HarnessCustomPackVersionRow.pack_id == manifest.id,
            ).limit(1)
        )
        if existing is not None:
            raise PackServiceError("Custom Pack already exists; save a new version instead")
        await self._validate_custom_catalog(project_id, manifest)
        await self._save_version(project_id, manifest, user_id)
        return manifest

    async def save_custom_version(
        self, *, project_id: str, pack_id: str, payload: dict[str, Any], user_id: int
    ) -> PackManifest:
        manifest = parse_custom_manifest(payload)
        if manifest.id != pack_id:
            raise PackServiceError("Pack id cannot change between versions")
        latest = await self._latest_row(project_id, pack_id)
        if latest is None:
            raise PackServiceError("Custom Pack was not found")
        if _semver(manifest.version) <= _semver(latest.version):
            raise PackServiceError("New Pack version must be greater than the current version")
        await self._validate_custom_catalog(project_id, manifest, replacing_pack_id=pack_id)
        await self._save_version(project_id, manifest, user_id)
        return manifest

    async def resolve_for_execution(
        self, project_id: str, workflow_key: str = DEFAULT_WORKFLOW_KEY
    ) -> ResolvedPackSet | None:
        """Integration hook for planner/executor request construction.

        Callers receive the exact immutable manifest content selected for this
        project/workflow, rather than recompiling against later custom versions.
        """
        active = await self.current_activation(project_id, workflow_key)
        if active is None and workflow_key != DEFAULT_WORKFLOW_KEY:
            active = await self.current_activation(project_id, DEFAULT_WORKFLOW_KEY)
        if active is None:
            return None
        snapshot_row = await self._snapshot_for(active.snapshot.snapshot_hash)
        if snapshot_row is None:
            raise PackServiceError("Pack activation snapshot is unavailable")
        manifests = tuple(PackManifest.model_validate_json(json.dumps(item), strict=True) for item in snapshot_row.resolved_manifests)
        return ResolvedPackSet(manifests=manifests, snapshot=active.snapshot)

    async def _validate_custom_catalog(
        self, project_id: str, manifest: PackManifest, *, replacing_pack_id: str | None = None
    ) -> None:
        existing = [item for item in await self._latest_custom_manifests(project_id) if item.id != replacing_pack_id]
        try:
            PackCatalog(
                [*build_pack_catalog(self._registry).definitions(), *existing, manifest],
                allowed_references=build_pack_reference_sets(self._registry),
            )
        except PackCatalogError as exc:
            raise PackServiceError(str(exc)) from exc

    async def _save_version(self, project_id: str, manifest: PackManifest, user_id: int) -> None:
        content_hash = manifest_hash(manifest)
        duplicate = await self._db.scalar(
            select(HarnessCustomPackVersionRow.id).where(
                HarnessCustomPackVersionRow.content_hash == content_hash
            ).limit(1)
        )
        if duplicate is not None:
            raise PackServiceError("An identical Pack version already exists")
        self._db.add(HarnessCustomPackVersionRow(
            project_id=project_id,
            pack_id=manifest.id,
            version=manifest.version,
            content_hash=content_hash,
            manifest=manifest.model_dump(mode="json"),
            created_by_user_id=user_id,
            created_at=datetime.datetime.now(datetime.UTC),
        ))
        await self._db.commit()

    async def _persist_snapshot(
        self, *, project_id: str, workflow_key: str, resolved: ResolvedPackSet, user_id: int
    ) -> HarnessPackActivationSnapshotRow:
        existing = await self._snapshot_for(resolved.snapshot.snapshot_hash)
        if existing is not None:
            return existing
        row = HarnessPackActivationSnapshotRow(
            project_id=project_id,
            workflow_key=workflow_key,
            active_pack_ids=list(resolved.snapshot.active_pack_ids),
            resolved_snapshot=resolved.snapshot.model_dump(mode="json"),
            resolved_manifests=[item.model_dump(mode="json") for item in resolved.manifests],
            snapshot_hash=resolved.snapshot.snapshot_hash,
            created_by_user_id=user_id,
            created_at=datetime.datetime.now(datetime.UTC),
        )
        self._db.add(row)
        await self._db.flush()
        return row

    async def _latest_custom_manifests(self, project_id: str) -> list[PackManifest]:
        rows = list((await self._db.execute(
            select(HarnessCustomPackVersionRow).where(
                HarnessCustomPackVersionRow.project_id == project_id
            ).order_by(HarnessCustomPackVersionRow.pack_id, desc(HarnessCustomPackVersionRow.id))
        )).scalars())
        latest: dict[str, PackManifest] = {}
        for row in rows:
            latest.setdefault(
                row.pack_id,
                PackManifest.model_validate_json(json.dumps(row.manifest), strict=True),
            )
        return list(latest.values())

    async def _latest_row(self, project_id: str, pack_id: str) -> HarnessCustomPackVersionRow | None:
        return await self._db.scalar(
            select(HarnessCustomPackVersionRow).where(
                HarnessCustomPackVersionRow.project_id == project_id,
                HarnessCustomPackVersionRow.pack_id == pack_id,
            ).order_by(desc(HarnessCustomPackVersionRow.id)).limit(1)
        )

    async def _activation_row(self, project_id: str, workflow_key: str) -> HarnessProjectPackActivationRow | None:
        return await self._db.scalar(
            select(HarnessProjectPackActivationRow).where(
                HarnessProjectPackActivationRow.project_id == project_id,
                HarnessProjectPackActivationRow.workflow_key == workflow_key,
            ).limit(1)
        )

    async def _snapshot_for(self, snapshot_hash: str) -> HarnessPackActivationSnapshotRow | None:
        return await self._db.scalar(
            select(HarnessPackActivationSnapshotRow).where(
                HarnessPackActivationSnapshotRow.snapshot_hash == snapshot_hash
            ).limit(1)
        )


def _validate_workflow_key(value: str) -> None:
    if not _WORKFLOW_KEY.fullmatch(value):
        raise PackServiceError("Workflow key must use lowercase letters, numbers, dots, underscores, or hyphens")


def _parse_snapshot(value: dict[str, Any]) -> PackSnapshot:
    # Database JSON returns lists while strict Pack contracts intentionally use tuples.
    return PackSnapshot.model_validate_json(json.dumps(value), strict=True)
