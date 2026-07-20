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
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.harness_pack import (
    HarnessPackDraftRow,
    HarnessPackAttachmentBindingRow,
    HarnessPackAttachmentBlobRow,
    HarnessPackPreviewReceiptRow,
    HarnessPackPromotionProposalRow,
    HarnessPrivateAnalysisPackRow,
    HarnessPrivateAnalysisPackShareRow,
    HarnessPrivateAnalysisPackVersionRow,
)
from src.harness.capability_runtime import CapabilityExecutionContext, CapabilityExecutionRequest
from src.harness.packs.models import PackArtifactV3
from src.harness.packs.reference_limits import (
    MAX_REFERENCE_AGGREGATE_EXTRACTED_TEXT_CHARS,
    MAX_REFERENCE_ATTACHMENTS,
    MAX_REFERENCE_EXTRACTED_TEXT_CHARS,
)
from src.harness.registry import CapabilityRegistry

if TYPE_CHECKING:
    from src.harness.executor import HarnessCapabilityExecutor


_PACK_ID = re.compile(r"^[a-z][a-z0-9.-]{1,70}$")
_RESERVED = frozenset({
    "baseline", "foundation", "permissions", "safety", "tools", "quality_gates",
    "required_sections", "protected_identifiers", "endpoints", "imports", "code",
    "prompt", "prompt_template", "provider_actions", "capability_grants",
})
_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
_MAX_TOTAL_ATTACHMENT_BYTES = 50 * 1024 * 1024
_ATTACHMENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "txt": "text/plain",
    "md": "text/markdown",
    "csv": "text/csv",
    "json": "application/json",
    "yaml": "application/x-yaml",
    "yml": "application/x-yaml",
    "png": "image/png",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
}
_TEXT_ATTACHMENT_TYPES = frozenset({"txt", "md", "csv", "json", "yaml", "yml"})
_PARSED_ATTACHMENT_TYPES = frozenset({"pdf", "docx", "pptx", "xlsx"})
_MAX_DISCOVERY_TURNS = 3
_MAX_DISCOVERY_INSTRUCTION_CHARS = 4_000
_MAX_DISCOVERY_QUESTION_CHARS = 240


class DraftServiceError(ValueError):
    status_code = 409


class DraftValidationError(DraftServiceError):
    status_code = 422


class DraftConflictError(DraftServiceError):
    status_code = 409


class DraftNotFoundError(DraftServiceError):
    status_code = 404


class AttachmentValidationError(DraftValidationError):
    pass


@dataclass(frozen=True)
class DraftRecord:
    id: int
    conversation_id: int | None
    owner_user_id: int
    status: str
    revision: int
    name: str
    artifact: dict[str, Any]
    validation: dict[str, Any]
    discovery_context: dict[str, Any]
    attachments: tuple["AttachmentRecord", ...]


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
    attachments: tuple["AttachmentRecord", ...]


@dataclass(frozen=True)
class AttachmentRecord:
    id: int
    sha256: str
    filename: str
    content_type: str
    size_bytes: int
    purpose: str
    extracted_text: str | None


def normalized_audience(artifact: PackArtifactV3) -> dict[str, str]:
    """Return only bounded normalized dimensions for render/provider metadata."""
    return artifact.audience.render.model_dump(mode="json", exclude_none=True)


def artifact_hash(
    artifact: PackArtifactV3, attachment_receipts: list[dict[str, str]] | tuple[dict[str, str], ...] = ()
) -> str:
    """Hash immutable Pack content and its sorted reference identities.

    Empty references retain the original V3 artifact hash for backward
    compatibility with versions written before Pack attachments existed.
    """
    if attachment_receipts:
        payload: Any = {
            "artifact": artifact.model_dump(mode="json"),
            "reference_attachments": sorted(
                attachment_receipts, key=lambda item: (item["sha256"], item["purpose"])
            ),
        }
    else:
        payload = artifact.model_dump(mode="json")
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _bounded_discovery_text(value: str, limit: int) -> str:
    return " ".join(value.strip().split())[:limit]


def _discovery_turns(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, dict) or not isinstance(value.get("turns"), list):
        return []
    turns: list[dict[str, str]] = []
    for item in value["turns"][-_MAX_DISCOVERY_TURNS:]:
        if not isinstance(item, dict):
            continue
        instruction = item.get("instruction")
        question = item.get("question")
        if isinstance(instruction, str) and isinstance(question, str):
            normalized_instruction = _bounded_discovery_text(
                instruction, _MAX_DISCOVERY_INSTRUCTION_CHARS
            )
            normalized_question = _bounded_discovery_text(question, _MAX_DISCOVERY_QUESTION_CHARS)
            if normalized_instruction and normalized_question:
                turns.append({"instruction": normalized_instruction, "question": normalized_question})
    return turns


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
        return await self._draft(row) if row else None

    async def create_draft(
        self,
        *,
        conversation_id: int | None = None,
        owner_user_id: int,
        name: str = "Untitled Analysis Pack",
        artifact_patch: dict[str, Any] | None = None,
    ) -> DraftRecord:
        if conversation_id is not None:
            active = await self.active_for_conversation(conversation_id, owner_user_id)
            if active is not None:
                return active
        candidate: dict[str, Any] = {}
        if artifact_patch is not None:
            _reject_reserved(artifact_patch)
            candidate = _merge({}, artifact_patch)
        now = datetime.datetime.now(datetime.UTC)
        row = HarnessPackDraftRow(
            conversation_id=conversation_id, owner_user_id=owner_user_id, status="active", revision=1,
            name=name.strip()[:160] or "Untitled Analysis Pack", artifact=candidate,
            validation=self._validation(candidate),
            discovery_context={},
            created_at=now, updated_at=now,
        )
        self._db.add(row)
        await self._db.flush()
        return await self._draft(row)

    async def get_draft(self, draft_id: int, owner_user_id: int) -> DraftRecord:
        row = await self._db.get(HarnessPackDraftRow, draft_id)
        if row is None or row.owner_user_id != owner_user_id:
            raise DraftNotFoundError("Pack draft not found")
        return await self._draft(row)

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
        row.artifact, row.validation, row.discovery_context = candidate, validation, {}
        if name is not None:
            normalized_name = " ".join(name.strip().split())
            if not normalized_name or len(normalized_name) > 160:
                raise DraftValidationError("Pack name must be between 1 and 160 characters")
            row.name = normalized_name
        row.revision += 1
        row.updated_at = datetime.datetime.now(datetime.UTC)
        await self._db.flush()
        return await self._draft(row)

    async def validate(
        self,
        draft_id: int,
        owner_user_id: int,
        expected_revision: int | None = None,
    ) -> DraftRecord:
        row = await self._owned_draft(draft_id, owner_user_id)
        if expected_revision is not None and row.revision != expected_revision:
            raise DraftConflictError("Pack draft revision is stale")
        row.validation = self._validation(dict(row.artifact or {}))
        await self._db.flush()
        return await self._draft(row)

    async def abandon(self, draft_id: int, owner_user_id: int, expected_revision: int) -> DraftRecord:
        row = await self._owned_active_draft(draft_id, owner_user_id)
        if row.revision != expected_revision:
            raise DraftConflictError("Pack draft revision is stale")
        row.status = "abandoned"
        row.revision += 1
        row.updated_at = datetime.datetime.now(datetime.UTC)
        await self._db.flush()
        return await self._draft(row)

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
        now = datetime.datetime.now(datetime.UTC)
        attachments = await self._attachments_for(draft_id=row.id)
        attachment_receipts = _attachment_receipts(attachments)
        content_hash = artifact_hash(artifact, attachment_receipts)
        if row.target_private_pack_id is not None:
            pack = await self._db.get(HarnessPrivateAnalysisPackRow, row.target_private_pack_id)
            if pack is None or pack.owner_user_id != owner_user_id:
                raise DraftNotFoundError("Private Pack target not found")
            if pack_id is not None and pack_id != pack.pack_id:
                raise DraftValidationError("Forked Pack drafts cannot change Pack identity")
        else:
            pack = await self._owned_pack(chosen_id, owner_user_id)
            if pack is None:
                pack = HarnessPrivateAnalysisPackRow(pack_id=chosen_id, owner_user_id=owner_user_id, name=row.name, created_at=now)
                self._db.add(pack)
                await self._db.flush()
        prior = (await self._db.execute(
            select(HarnessPrivateAnalysisPackVersionRow)
            .where(HarnessPrivateAnalysisPackVersionRow.private_pack_id == pack.id)
            .order_by(desc(HarnessPrivateAnalysisPackVersionRow.id)).limit(1)
        )).scalar_one_or_none()
        version = f"1.0.{0 if prior is None else int(prior.version.rsplit('.', 1)[-1]) + 1}"
        record = HarnessPrivateAnalysisPackVersionRow(
            private_pack_id=pack.id, version=version, content_hash=content_hash,
            artifact=artifact.model_dump(mode="json"),
            receipt={
                "kind": "private_analysis_pack",
                "audience_render": normalized_audience(artifact),
                "additive": True,
                "reference_attachments": attachment_receipts,
            },
            created_by_user_id=owner_user_id, created_at=now,
        )
        self._db.add(record)
        await self._db.flush()
        for attachment in attachments:
            self._db.add(HarnessPackAttachmentBindingRow(
                blob_id=await self._blob_id_for_attachment(attachment.id),
                private_pack_version_id=record.id,
                purpose=attachment.purpose,
            ))
        row.status, row.revision, row.updated_at = "saved", row.revision + 1, now
        await self._db.flush()
        return await self._version(record, pack)

    async def list_versions(self, owner_user_id: int) -> list[PrivateVersionRecord]:
        rows = (await self._db.execute(
            select(HarnessPrivateAnalysisPackVersionRow, HarnessPrivateAnalysisPackRow)
            .join(HarnessPrivateAnalysisPackRow, HarnessPrivateAnalysisPackVersionRow.private_pack_id == HarnessPrivateAnalysisPackRow.id)
            .where(HarnessPrivateAnalysisPackRow.owner_user_id == owner_user_id)
            .order_by(desc(HarnessPrivateAnalysisPackVersionRow.id))
        )).all()
        return [await self._version(version, pack) for version, pack in rows]

    async def get_version(self, version_id: int, owner_user_id: int, *, project_id: str | None = None) -> PrivateVersionRecord:
        row = await self._version_row(version_id)
        pack = await self._db.get(HarnessPrivateAnalysisPackRow, row.private_pack_id)
        assert pack is not None
        if pack.owner_user_id != owner_user_id:
            if project_id is None or not await self._shared(row.id, project_id):
                raise DraftNotFoundError("Private Pack version not found")
        return await self._version(row, pack)

    async def fork_version(
        self, *, version_id: int, conversation_id: int | None = None, owner_user_id: int
    ) -> DraftRecord:
        version = await self._version_row(version_id)
        pack = await self._db.get(HarnessPrivateAnalysisPackRow, version.private_pack_id)
        if pack is None or pack.owner_user_id != owner_user_id:
            raise DraftNotFoundError("Private Pack version not found")
        if conversation_id is not None and await self.active_for_conversation(
            conversation_id, owner_user_id
        ) is not None:
            raise DraftConflictError("Conversation already has an active Pack draft")
        now = datetime.datetime.now(datetime.UTC)
        row = HarnessPackDraftRow(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            status="active",
            revision=1,
            name=pack.name,
            artifact=dict(version.artifact or {}),
            validation=self._validation(dict(version.artifact or {})),
            discovery_context={},
            target_private_pack_id=pack.id,
            base_private_pack_version_id=version.id,
            created_at=now,
            updated_at=now,
        )
        self._db.add(row)
        await self._db.flush()
        bindings = await self._binding_rows(private_pack_version_id=version.id)
        for binding, _ in bindings:
            self._db.add(HarnessPackAttachmentBindingRow(
                blob_id=binding.blob_id, draft_id=row.id, purpose=binding.purpose
            ))
        await self._db.flush()
        return await self._draft(row)

    async def upload_attachment(
        self, *, draft_id: int, owner_user_id: int, filename: str | None, content: bytes, purpose: str
    ) -> AttachmentRecord:
        draft = await self._owned_active_draft(draft_id, owner_user_id)
        safe_filename, content_type = _attachment_file_metadata(filename)
        normalized_purpose = _attachment_purpose(purpose)
        if not content:
            raise AttachmentValidationError("Attachment must not be empty")
        if len(content) > _MAX_ATTACHMENT_BYTES:
            raise AttachmentValidationError("Attachment exceeds the 10 MiB limit")
        attachments = await self._attachments_for(draft_id=draft.id)
        if len(attachments) >= MAX_REFERENCE_ATTACHMENTS:
            raise AttachmentValidationError("A Pack draft may contain at most 20 references")
        if sum(item.size_bytes for item in attachments) + len(content) > _MAX_TOTAL_ATTACHMENT_BYTES:
            raise AttachmentValidationError("Pack draft references exceed the 50 MiB total limit")
        extracted_text = _extract_attachment_text(content, safe_filename)
        aggregate_extracted_chars = sum(len(item.extracted_text or "") for item in attachments)
        if aggregate_extracted_chars + len(extracted_text or "") > MAX_REFERENCE_AGGREGATE_EXTRACTED_TEXT_CHARS:
            raise AttachmentValidationError(
                "Pack reference extracted text exceeds the 32,000 character aggregate limit"
            )
        digest = hashlib.sha256(content).hexdigest()
        blob = (await self._db.execute(select(HarnessPackAttachmentBlobRow).where(
            HarnessPackAttachmentBlobRow.owner_user_id == owner_user_id,
            HarnessPackAttachmentBlobRow.sha256 == digest,
        ))).scalar_one_or_none()
        if blob is None:
            blob = HarnessPackAttachmentBlobRow(
                owner_user_id=owner_user_id,
                sha256=digest,
                safe_filename=safe_filename,
                content_type=content_type,
                size_bytes=len(content),
                content=content,
                extracted_text=extracted_text,
            )
            self._db.add(blob)
            await self._db.flush()
        duplicate = (await self._db.execute(select(HarnessPackAttachmentBindingRow.id).where(
            HarnessPackAttachmentBindingRow.draft_id == draft.id,
            HarnessPackAttachmentBindingRow.blob_id == blob.id,
            HarnessPackAttachmentBindingRow.purpose == normalized_purpose,
        ))).scalar_one_or_none()
        if duplicate is not None:
            raise DraftConflictError("This reference is already attached with that purpose")
        binding = HarnessPackAttachmentBindingRow(blob_id=blob.id, draft_id=draft.id, purpose=normalized_purpose)
        self._db.add(binding)
        await self._db.flush()
        return _attachment(binding, blob)

    async def update_attachment_purpose(
        self, *, draft_id: int, attachment_id: int, owner_user_id: int, purpose: str
    ) -> AttachmentRecord:
        await self._owned_active_draft(draft_id, owner_user_id)
        binding, blob = await self._owned_draft_attachment(draft_id, attachment_id, owner_user_id)
        normalized_purpose = _attachment_purpose(purpose)
        if binding.purpose != normalized_purpose:
            duplicate = (await self._db.execute(select(HarnessPackAttachmentBindingRow.id).where(
                HarnessPackAttachmentBindingRow.draft_id == draft_id,
                HarnessPackAttachmentBindingRow.blob_id == binding.blob_id,
                HarnessPackAttachmentBindingRow.purpose == normalized_purpose,
            ))).scalar_one_or_none()
            if duplicate is not None:
                raise DraftConflictError("This reference is already attached with that purpose")
        binding.purpose = normalized_purpose
        await self._db.flush()
        return _attachment(binding, blob)

    async def delete_attachment(self, *, draft_id: int, attachment_id: int, owner_user_id: int) -> None:
        await self._owned_active_draft(draft_id, owner_user_id)
        binding, _ = await self._owned_draft_attachment(draft_id, attachment_id, owner_user_id)
        await self._db.delete(binding)
        await self._db.flush()

    async def draft_attachment_content(
        self, *, draft_id: int, attachment_id: int, owner_user_id: int
    ) -> tuple[AttachmentRecord, bytes]:
        await self._owned_draft(draft_id, owner_user_id)
        binding, blob = await self._owned_draft_attachment(draft_id, attachment_id, owner_user_id)
        return _attachment(binding, blob), blob.content

    async def version_attachment_content(
        self, *, version_id: int, attachment_id: int, owner_user_id: int
    ) -> tuple[AttachmentRecord, bytes]:
        await self.get_version(version_id, owner_user_id)
        binding_rows = await self._binding_rows(private_pack_version_id=version_id)
        for binding, blob in binding_rows:
            if binding.id == attachment_id:
                return _attachment(binding, blob), blob.content
        raise DraftNotFoundError("Pack attachment not found")

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

    async def get_active_draft(self, draft_id: int, owner_user_id: int) -> DraftRecord:
        """Load one exact owner-private active draft for interactive assistance."""
        return await self._draft(await self._owned_active_draft(draft_id, owner_user_id))

    async def retain_discovery_turn(
        self,
        *,
        draft_id: int,
        owner_user_id: int,
        instruction: str,
        question: str,
    ) -> DraftRecord:
        """Persist one bounded clarification exchange without changing Pack content."""
        row = await self._owned_active_draft(draft_id, owner_user_id)
        turn = {
            "instruction": _bounded_discovery_text(instruction, _MAX_DISCOVERY_INSTRUCTION_CHARS),
            "question": _bounded_discovery_text(question, _MAX_DISCOVERY_QUESTION_CHARS),
        }
        if not turn["instruction"] or not turn["question"]:
            return await self._draft(row)
        prior = _discovery_turns(row.discovery_context)
        # Retain the original brief and the most recent follow-up exchanges.
        turns = (prior + [turn])[-_MAX_DISCOVERY_TURNS:]
        if len(prior) >= _MAX_DISCOVERY_TURNS:
            turns[0] = prior[0]
        row.discovery_context = {"turns": turns}
        row.updated_at = datetime.datetime.now(datetime.UTC)
        await self._db.flush()
        return await self._draft(row)

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

    async def _draft(self, row: HarnessPackDraftRow) -> DraftRecord:
        return DraftRecord(
            row.id, row.conversation_id, row.owner_user_id, row.status, row.revision, row.name,
            dict(row.artifact or {}), dict(row.validation or {}), dict(row.discovery_context or {}),
            tuple(await self._attachments_for(draft_id=row.id)),
        )

    async def _version(self, row: HarnessPrivateAnalysisPackVersionRow, pack: HarnessPrivateAnalysisPackRow) -> PrivateVersionRecord:
        return PrivateVersionRecord(
            row.id, pack.pack_id, pack.owner_user_id, pack.name, row.version, row.content_hash,
            _parse_artifact(row.artifact), dict(row.receipt or {}),
            tuple(await self._attachments_for(private_pack_version_id=row.id)),
        )

    async def _attachments_for(
        self, *, draft_id: int | None = None, private_pack_version_id: int | None = None
    ) -> list[AttachmentRecord]:
        return [_attachment(binding, blob) for binding, blob in await self._binding_rows(
            draft_id=draft_id, private_pack_version_id=private_pack_version_id
        )]

    async def _binding_rows(
        self, *, draft_id: int | None = None, private_pack_version_id: int | None = None
    ) -> list[tuple[HarnessPackAttachmentBindingRow, HarnessPackAttachmentBlobRow]]:
        if (draft_id is None) == (private_pack_version_id is None):
            raise ValueError("Exactly one attachment target is required")
        criterion = (
            HarnessPackAttachmentBindingRow.draft_id == draft_id
            if draft_id is not None
            else HarnessPackAttachmentBindingRow.private_pack_version_id == private_pack_version_id
        )
        return (await self._db.execute(
            select(HarnessPackAttachmentBindingRow, HarnessPackAttachmentBlobRow)
            .join(HarnessPackAttachmentBlobRow, HarnessPackAttachmentBindingRow.blob_id == HarnessPackAttachmentBlobRow.id)
            .where(criterion)
            .order_by(HarnessPackAttachmentBlobRow.sha256, HarnessPackAttachmentBindingRow.purpose, HarnessPackAttachmentBindingRow.id)
        )).all()

    async def _blob_id_for_attachment(self, attachment_id: int) -> int:
        blob_id = (await self._db.execute(select(HarnessPackAttachmentBindingRow.blob_id).where(
            HarnessPackAttachmentBindingRow.id == attachment_id
        ))).scalar_one()
        return int(blob_id)

    async def _owned_draft_attachment(
        self, draft_id: int, attachment_id: int, owner_user_id: int
    ) -> tuple[HarnessPackAttachmentBindingRow, HarnessPackAttachmentBlobRow]:
        row = (await self._db.execute(
            select(HarnessPackAttachmentBindingRow, HarnessPackAttachmentBlobRow)
            .join(HarnessPackAttachmentBlobRow, HarnessPackAttachmentBindingRow.blob_id == HarnessPackAttachmentBlobRow.id)
            .where(
                HarnessPackAttachmentBindingRow.id == attachment_id,
                HarnessPackAttachmentBindingRow.draft_id == draft_id,
                HarnessPackAttachmentBlobRow.owner_user_id == owner_user_id,
            )
        )).one_or_none()
        if row is None:
            raise DraftNotFoundError("Pack attachment not found")
        return row


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


def _attachment_file_metadata(filename: str | None) -> tuple[str, str]:
    raw_name = (filename or "").strip()
    if not raw_name or raw_name != Path(raw_name).name:
        raise AttachmentValidationError("Attachment filename is invalid")
    extension = Path(raw_name).suffix.casefold().lstrip(".")
    content_type = _ATTACHMENT_TYPES.get(extension)
    if content_type is None:
        raise AttachmentValidationError("Attachment type is not supported")
    stem = Path(raw_name).stem.encode("ascii", "ignore").decode("ascii")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-")[:220]
    if not stem:
        stem = "reference"
    return f"{stem}.{extension}", content_type


def _attachment_purpose(value: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized or len(normalized) > 500:
        raise AttachmentValidationError("Attachment purpose must be between 1 and 500 characters")
    return normalized


def _extract_attachment_text(content: bytes, safe_filename: str) -> str | None:
    extension = Path(safe_filename).suffix.casefold().lstrip(".")
    if extension in {"png", "jpeg", "jpg"}:
        return None
    try:
        if extension in _TEXT_ATTACHMENT_TYPES:
            text = content.decode("utf-8-sig", errors="strict")
        else:
            from src.ingestion.parser import parse_file

            text = parse_file(content, safe_filename)
    except UnicodeDecodeError as exc:
        raise AttachmentValidationError("Text attachments must be valid UTF-8") from exc
    except Exception as exc:
        raise AttachmentValidationError("Attachment text could not be extracted") from exc
    return text[:MAX_REFERENCE_EXTRACTED_TEXT_CHARS] or None


def _attachment(binding: HarnessPackAttachmentBindingRow, blob: HarnessPackAttachmentBlobRow) -> AttachmentRecord:
    return AttachmentRecord(
        id=binding.id,
        sha256=blob.sha256,
        filename=blob.safe_filename,
        content_type=blob.content_type,
        size_bytes=blob.size_bytes,
        purpose=binding.purpose,
        extracted_text=blob.extracted_text,
    )


def _attachment_receipts(attachments: list[AttachmentRecord] | tuple[AttachmentRecord, ...]) -> list[dict[str, str]]:
    return [
        {"sha256": item.sha256, "purpose": item.purpose}
        for item in sorted(attachments, key=lambda item: (item.sha256, item.purpose))
    ]


def _parse_artifact(payload: dict[str, Any]) -> PackArtifactV3:
    # The API boundary receives JSON arrays.  Validating through JSON preserves
    # the strict immutable domain contract while accepting legal JSON transport.
    return PackArtifactV3.model_validate_json(json.dumps(payload))
