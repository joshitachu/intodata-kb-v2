"""Private V3 Analysis Pack draft lifecycle and guarded compilation.

The service is the only mutation authority.  Builder/model turns may propose a
structured patch, but cannot expand the contract, registry, or permissions.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.harness_pack import (
    HarnessPackDraftRow,
    HarnessPackPreviewReceiptRow,
    HarnessPackPromotionProposalRow,
    HarnessPrivateAnalysisPackRow,
    HarnessPrivateAnalysisPackShareRow,
    HarnessPrivateAnalysisPackVersionRow,
)
from src.harness.executor import CapabilityExecutionContext, CapabilityExecutionRequest, HarnessCapabilityExecutor
from src.harness.packs.models import PackArtifactV3
from src.harness.registry import CapabilityRegistry


_PACK_ID = re.compile(r"^[a-z][a-z0-9.-]{1,70}$")
_RESERVED = frozenset({
    "baseline", "foundation", "permissions", "safety", "tools", "quality_gates",
    "required_sections", "protected_identifiers", "endpoints", "imports", "code",
    "prompt", "prompt_template", "provider_actions", "capability_grants",
})


class DraftServiceError(ValueError):
    status_code = 409


class DraftValidationError(DraftServiceError):
    status_code = 422


class DraftConflictError(DraftServiceError):
    status_code = 409


class DraftNotFoundError(DraftServiceError):
    status_code = 404


@dataclass(frozen=True)
class DraftRecord:
    id: int
    conversation_id: int
    owner_user_id: int
    status: str
    revision: int
    name: str
    artifact: dict[str, Any]
    validation: dict[str, Any]


@dataclass(frozen=True)
class PrivateVersionRecord:
    id: int
    pack_id: str
    owner_user_id: int
    name: str
    version: str
    content_hash: str
    artifact: PackArtifactV3
    receipt: dict[str, Any]


def normalized_audience(artifact: PackArtifactV3) -> dict[str, str]:
    """Return only bounded normalized dimensions for render/provider metadata."""
    return artifact.audience.render.model_dump(mode="json", exclude_none=True)


def artifact_hash(artifact: PackArtifactV3) -> str:
    raw = json.dumps(artifact.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()


class PackDraftService:
    def __init__(self, db: AsyncSession, registry: CapabilityRegistry) -> None:
        self._db = db
        self._registry = registry

    async def active_for_conversation(self, conversation_id: int, owner_user_id: int) -> DraftRecord | None:
        row = (await self._db.execute(
            select(HarnessPackDraftRow).where(
                HarnessPackDraftRow.conversation_id == conversation_id,
                HarnessPackDraftRow.owner_user_id == owner_user_id,
                HarnessPackDraftRow.status == "active",
            ).order_by(desc(HarnessPackDraftRow.id)).limit(1)
        )).scalar_one_or_none()
        return self._draft(row) if row else None

    async def create_draft(self, *, conversation_id: int, owner_user_id: int, name: str = "Untitled Analysis Pack") -> DraftRecord:
        active = await self.active_for_conversation(conversation_id, owner_user_id)
        if active is not None:
            return active
        now = datetime.datetime.now(datetime.UTC)
        row = HarnessPackDraftRow(
            conversation_id=conversation_id, owner_user_id=owner_user_id, status="active", revision=1,
            name=name.strip()[:160] or "Untitled Analysis Pack", artifact={},
            validation={"valid": False, "errors": ["purpose, analyses, evidence, outputs, and audience are required"]},
            created_at=now, updated_at=now,
        )
        self._db.add(row)
        await self._db.flush()
        return self._draft(row)

    async def get_draft(self, draft_id: int, owner_user_id: int) -> DraftRecord:
        row = await self._db.get(HarnessPackDraftRow, draft_id)
        if row is None or row.owner_user_id != owner_user_id:
            raise DraftNotFoundError("Pack draft not found")
        return self._draft(row)

    async def mutate(
        self,
        *,
        draft_id: int,
        owner_user_id: int,
        expected_revision: int,
        patch: dict[str, Any],
        name: str | None = None,
    ) -> DraftRecord:
        row = await self._owned_active_draft(draft_id, owner_user_id)
        if row.revision != expected_revision:
            raise DraftConflictError("Pack draft revision is stale")
        _reject_reserved(patch)
        candidate = _merge(dict(row.artifact or {}), patch)
        validation = self._validation(candidate)
        row.artifact, row.validation = candidate, validation
        if name is not None:
            normalized_name = " ".join(name.strip().split())
            if not normalized_name or len(normalized_name) > 160:
                raise DraftValidationError("Pack name must be between 1 and 160 characters")
            row.name = normalized_name
        row.revision += 1
        row.updated_at = datetime.datetime.now(datetime.UTC)
        await self._db.flush()
        return self._draft(row)

    async def validate(self, draft_id: int, owner_user_id: int) -> DraftRecord:
        row = await self._owned_draft(draft_id, owner_user_id)
        row.validation = self._validation(dict(row.artifact or {}))
        await self._db.flush()
        return self._draft(row)

    async def abandon(self, draft_id: int, owner_user_id: int, expected_revision: int) -> DraftRecord:
        row = await self._owned_active_draft(draft_id, owner_user_id)
        if row.revision != expected_revision:
            raise DraftConflictError("Pack draft revision is stale")
        row.status = "abandoned"
        row.revision += 1
        row.updated_at = datetime.datetime.now(datetime.UTC)
        await self._db.flush()
        return self._draft(row)

    async def save(self, *, draft_id: int, owner_user_id: int, expected_revision: int, pack_id: str | None = None) -> PrivateVersionRecord:
        row = await self._owned_active_draft(draft_id, owner_user_id)
        if row.revision != expected_revision:
            raise DraftConflictError("Pack draft revision is stale")
        validation = self._validation(dict(row.artifact or {}))
        if not validation["valid"]:
            raise DraftValidationError("Pack draft is invalid: " + "; ".join(validation["errors"]))
        artifact = _parse_artifact(row.artifact)
        chosen_id = pack_id or _pack_id(row.name, row.id)
        if not _PACK_ID.fullmatch(chosen_id):
            raise DraftValidationError("Pack id must be a lowercase safe identifier")
        pack = await self._owned_pack(chosen_id, owner_user_id)
        now = datetime.datetime.now(datetime.UTC)
        if pack is None:
            pack = HarnessPrivateAnalysisPackRow(pack_id=chosen_id, owner_user_id=owner_user_id, name=row.name, created_at=now)
            self._db.add(pack)
            await self._db.flush()
        prior = (await self._db.execute(
            select(HarnessPrivateAnalysisPackVersionRow).where(HarnessPrivateAnalysisPackVersionRow.private_pack_id == pack.id)
            .order_by(desc(HarnessPrivateAnalysisPackVersionRow.id)).limit(1)
        )).scalar_one_or_none()
        version = f"1.0.{0 if prior is None else int(prior.version.rsplit('.', 1)[-1]) + 1}"
        content_hash = artifact_hash(artifact)
        record = HarnessPrivateAnalysisPackVersionRow(
            private_pack_id=pack.id, version=version, content_hash=content_hash,
            artifact=artifact.model_dump(mode="json"),
            receipt={"kind": "private_analysis_pack", "audience_render": normalized_audience(artifact), "additive": True},
            created_by_user_id=owner_user_id, created_at=now,
        )
        self._db.add(record)
        row.status, row.revision, row.updated_at = "saved", row.revision + 1, now
        await self._db.flush()
        return self._version(record, pack)

    async def list_versions(self, owner_user_id: int) -> list[PrivateVersionRecord]:
        rows = (await self._db.execute(
            select(HarnessPrivateAnalysisPackVersionRow, HarnessPrivateAnalysisPackRow)
            .join(HarnessPrivateAnalysisPackRow, HarnessPrivateAnalysisPackVersionRow.private_pack_id == HarnessPrivateAnalysisPackRow.id)
            .where(HarnessPrivateAnalysisPackRow.owner_user_id == owner_user_id)
            .order_by(desc(HarnessPrivateAnalysisPackVersionRow.id))
        )).all()
        return [self._version(version, pack) for version, pack in rows]

    async def get_version(self, version_id: int, owner_user_id: int, *, project_id: str | None = None) -> PrivateVersionRecord:
        row = await self._version_row(version_id)
        pack = await self._db.get(HarnessPrivateAnalysisPackRow, row.private_pack_id)
        assert pack is not None
        if pack.owner_user_id != owner_user_id:
            if project_id is None or not await self._shared(row.id, project_id):
                raise DraftNotFoundError("Private Pack version not found")
        return self._version(row, pack)

    async def compatibility(self, version_id: int, owner_user_id: int, project_id: str) -> dict[str, Any]:
        version = await self.get_version(version_id, owner_user_id, project_id=project_id)
        available = {item.id for item in self._registry.list(enabled_only=True)}
        required = [item.capability_id for item in version.artifact.capability_requirements if item.required and item.capability_id not in available]
        optional = [item.capability_id for item in version.artifact.capability_requirements if not item.required and item.capability_id not in available]
        return {"project_id": project_id, "compatible": not required, "missing_required": required, "missing_optional": optional, "version_id": version.id, "content_hash": version.content_hash}

    async def share(self, version_id: int, owner_user_id: int, project_id: str) -> dict[str, Any]:
        version = await self.get_version(version_id, owner_user_id)
        existing = (await self._db.execute(select(HarnessPrivateAnalysisPackShareRow).where(
            HarnessPrivateAnalysisPackShareRow.private_pack_version_id == version.id,
            HarnessPrivateAnalysisPackShareRow.project_id == project_id,
        ))).scalar_one_or_none()
        if existing is None:
            self._db.add(HarnessPrivateAnalysisPackShareRow(private_pack_version_id=version.id, project_id=project_id, shared_by_user_id=owner_user_id, created_at=datetime.datetime.now(datetime.UTC)))
            await self._db.flush()
        return {"version_id": version.id, "project_id": project_id, "content_hash": version.content_hash, "shared": True}

    async def preview(self, *, version_id: int, owner_user_id: int, project_id: str, executor: HarnessCapabilityExecutor | None = None) -> dict[str, Any]:
        version = await self.get_version(version_id, owner_user_id, project_id=project_id)
        compatibility = await self.compatibility(version_id, owner_user_id, project_id)
        if not compatibility["compatible"]:
            raise DraftValidationError("Required capabilities are unavailable: " + ", ".join(compatibility["missing_required"]))
        allowed = {requirement.capability_id for requirement in version.artifact.capability_requirements if requirement.capability_id not in compatibility["missing_optional"]}
        observations: list[dict[str, Any]] = []
        if executor is not None:
            for capability_id in sorted(allowed):
                observation = await executor.execute(CapabilityExecutionRequest(capability_id=capability_id), CapabilityExecutionContext(user_id=owner_user_id, project_scope=[project_id], allowed_capability_ids=allowed))
                observations.append(observation.model_dump(mode="json"))
        markdown = "\n\n".join(
            f"# {document.title}\n\n" + "\n".join(f"## {section.heading}\nEvidence-backed findings pending." for section in document.sections)
            for document in version.artifact.output_documents
        )[:12000]
        receipt = {"version_id": version.id, "content_hash": version.content_hash, "project_id": project_id, "missing_optional": compatibility["missing_optional"], "audience_render": normalized_audience(version.artifact), "capability_observations": observations}
        self._db.add(HarnessPackPreviewReceiptRow(private_pack_version_id=version.id, project_id=project_id, receipt=receipt, created_by_user_id=owner_user_id, created_at=datetime.datetime.now(datetime.UTC)))
        await self._db.flush()
        return {"markdown": markdown, "receipt": receipt}

    async def propose_promotion(self, version_id: int, owner_user_id: int) -> dict[str, Any]:
        version = await self.get_version(version_id, owner_user_id)
        # Canonical GitHub App integration is intentionally optional.  No local
        # endpoint can install, certify, register, or activate this artifact.
        detail = {"reason": "GitHub App canonical artifact configuration is not available", "content_hash": version.content_hash, "side_effects": "none"}
        proposal = HarnessPackPromotionProposalRow(private_pack_version_id=version.id, content_hash=version.content_hash, status="blocked_not_configured", detail=detail, proposed_by_user_id=owner_user_id, created_at=datetime.datetime.now(datetime.UTC))
        self._db.add(proposal)
        await self._db.flush()
        return {"id": proposal.id, "status": proposal.status, "content_hash": version.content_hash, "detail": detail}

    async def _owned_active_draft(self, draft_id: int, owner_user_id: int) -> HarnessPackDraftRow:
        row = await self._owned_draft(draft_id, owner_user_id)
        if row.status != "active":
            raise DraftConflictError("Pack draft is no longer active")
        return row

    async def _owned_draft(self, draft_id: int, owner_user_id: int) -> HarnessPackDraftRow:
        row = await self._db.get(HarnessPackDraftRow, draft_id)
        if row is None or row.owner_user_id != owner_user_id:
            raise DraftNotFoundError("Pack draft not found")
        return row

    async def _owned_pack(self, pack_id: str, owner_user_id: int) -> HarnessPrivateAnalysisPackRow | None:
        return (await self._db.execute(
            select(HarnessPrivateAnalysisPackRow).where(
                HarnessPrivateAnalysisPackRow.pack_id == pack_id,
                HarnessPrivateAnalysisPackRow.owner_user_id == owner_user_id,
            )
        )).scalar_one_or_none()

    async def _version_row(self, version_id: int) -> HarnessPrivateAnalysisPackVersionRow:
        row = await self._db.get(HarnessPrivateAnalysisPackVersionRow, version_id)
        if row is None:
            raise DraftNotFoundError("Private Pack version not found")
        return row

    async def _shared(self, version_id: int, project_id: str) -> bool:
        return (await self._db.execute(select(HarnessPrivateAnalysisPackShareRow.id).where(HarnessPrivateAnalysisPackShareRow.private_pack_version_id == version_id, HarnessPrivateAnalysisPackShareRow.project_id == project_id))).scalar_one_or_none() is not None

    def _validation(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            artifact = _parse_artifact(payload)
        except ValidationError as exc:
            return {"valid": False, "errors": [item["msg"] for item in exc.errors()][:20]}
        capability_ids = {item.id for item in self._registry.list(enabled_only=False)}
        unknown = [item.capability_id for item in artifact.capability_requirements if item.capability_id not in capability_ids]
        return {"valid": True, "errors": [], "unknown_capabilities": unknown, "audience_render": normalized_audience(artifact)}

    @staticmethod
    def _draft(row: HarnessPackDraftRow) -> DraftRecord:
        return DraftRecord(row.id, row.conversation_id, row.owner_user_id, row.status, row.revision, row.name, dict(row.artifact or {}), dict(row.validation or {}))

    @staticmethod
    def _version(row: HarnessPrivateAnalysisPackVersionRow, pack: HarnessPrivateAnalysisPackRow) -> PrivateVersionRecord:
        return PrivateVersionRecord(row.id, pack.pack_id, pack.owner_user_id, pack.name, row.version, row.content_hash, _parse_artifact(row.artifact), dict(row.receipt or {}))


def _reject_reserved(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in _RESERVED:
                raise DraftValidationError(f"Private Analysis Packs cannot set protected field: {key}")
            _reject_reserved(child)
    elif isinstance(value, list):
        for child in value:
            _reject_reserved(child)


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _merge(dict(base[key]), value)
        else:
            base[key] = value
    return base


def _pack_id(name: str, draft_id: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:55] or "analysis"
    return f"analysis.{slug}-{draft_id}"


def _parse_artifact(payload: dict[str, Any]) -> PackArtifactV3:
    # The API boundary receives JSON arrays.  Validating through JSON preserves
    # the strict immutable domain contract while accepting legal JSON transport.
    return PackArtifactV3.model_validate_json(json.dumps(payload))
