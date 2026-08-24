"""Pack draft lifecycle: chat and Workbench scratch space for one plugin.yaml.

The service is the only mutation authority. A builder or model turn may propose
a manifest, but persistence goes through MCP, which owns the one pack format and
validates it with the real loader.
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
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from src.db.models.harness_pack import (
    HarnessPackDraftRow,
    HarnessPackAttachmentBindingRow,
    HarnessPackAttachmentBlobRow,
)
from src.harness.capability_runtime import CapabilityExecutionContext, CapabilityExecutionRequest
from src.harness.packs.draft_artifact import PackDraftArtifact
from src.harness.providers.mcp_packs import McpPackProvider, McpPackProviderError
from src.harness.packs.reference_limits import (
    MAX_REFERENCE_AGGREGATE_EXTRACTED_TEXT_CHARS,
    MAX_REFERENCE_ATTACHMENTS,
    MAX_REFERENCE_EXTRACTED_TEXT_CHARS,
)
from src.harness.registry import CapabilityRegistry

if TYPE_CHECKING:
    from src.harness.executor import HarnessCapabilityExecutor


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
class SavedPackRecord:
    """A pack that now exists in MCP. The RAG side keeps no second copy."""

    mcp_pack_id: int
    name: str
    version: str
    documents: tuple[str, ...]
    content_hash: str


@dataclass(frozen=True)
class AttachmentRecord:
    id: int
    sha256: str
    filename: str
    content_type: str
    size_bytes: int
    purpose: str
    extracted_text: str | None


def normalized_audience(artifact: PackDraftArtifact) -> dict[str, str]:
    """Return only bounded normalized dimensions for render/provider metadata."""
    return artifact.audience_render()


def artifact_hash(
    artifact: PackDraftArtifact, attachment_receipts: list[dict[str, str]] | tuple[dict[str, str], ...] = ()
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
    """Draft lifecycle for chat- and Workbench-authored packs.

    The draft table is scratch space for work in progress. Saved packs live in
    MCP (``plugin_packs`` + ``data/plugin_packs/users/{uid}/``) because MCP owns
    the one pack format, so :meth:`save` persists THROUGH the gateway rather
    than into a second registry here.
    """

    def __init__(
        self,
        db: AsyncSession,
        registry: CapabilityRegistry,
        packs: McpPackProvider | None = None,
    ) -> None:
        self._db = db
        self._registry = registry
        self._packs = packs or McpPackProvider()

    def atomic(self) -> AsyncSessionTransaction:
        """Bound a domain mutation and its conversation receipt to one savepoint."""

        return self._db.begin_nested()

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
        reuse_active: bool = True,
    ) -> DraftRecord:
        if conversation_id is not None and reuse_active:
            active = await self.active_for_conversation(conversation_id, owner_user_id)
            if active is not None:
                return active
        candidate: dict[str, Any] = dict(artifact_patch) if artifact_patch else {}
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

    async def create_saved_pack(
        self,
        *,
        conversation_id: int | None,
        owner_user_id: int,
        name: str,
        artifact: dict[str, Any],
        access_token: str | None = None,
    ) -> tuple[int, SavedPackRecord]:
        """Create one valid pack in MCP without exposing a blank draft."""
        normalized_name = " ".join(name.strip().split())
        if not normalized_name or len(normalized_name) > 160:
            raise DraftValidationError("Pack name must be between 1 and 160 characters")
        validation = self._validation(dict(artifact))
        if not validation["valid"]:
            raise DraftValidationError("Pack is invalid: " + "; ".join(validation["errors"]))
        draft = await self.create_draft(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            name=normalized_name,
            artifact_patch=artifact,
            reuse_active=False,
        )
        saved = await self.save(
            draft_id=draft.id,
            owner_user_id=owner_user_id,
            expected_revision=draft.revision,
            access_token=access_token,
        )
        return draft.id, saved

    async def get_draft(self, draft_id: int, owner_user_id: int) -> DraftRecord:
        row = await self._db.get(HarnessPackDraftRow, draft_id)
        if row is None or row.owner_user_id != owner_user_id:
            raise DraftNotFoundError("Pack draft not found")
        return await self._draft(row)

    async def replace_artifact(
        self,
        *,
        draft_id: int,
        owner_user_id: int,
        expected_revision: int,
        artifact: PackDraftArtifact,
        name: str | None = None,
    ) -> DraftRecord:
        """Replace the draft's manifest wholesale.

        A manifest is one YAML document, so this REPLACES rather than deep-merges
        the way the old JSON artifact did. Merging two manifest texts would
        produce a document neither the author nor the authoring model wrote --
        refinement happens upstream, where the model is given the current
        manifest and returns a complete updated one.
        """
        row = await self._owned_active_draft(draft_id, owner_user_id)
        if row.revision != expected_revision:
            raise DraftConflictError("Pack draft revision is stale")
        candidate = artifact.model_dump(mode="json")
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

    async def save(
        self,
        *,
        draft_id: int,
        owner_user_id: int,
        expected_revision: int,
        pack_id: str | None = None,
        access_token: str | None = None,
    ) -> SavedPackRecord:
        """Persist the draft as a personal pack in MCP.

        ``pack_id`` is accepted and ignored: MCP owns the pack id namespace, and
        letting a caller choose one is exactly the kind of second-registry
        behaviour this collapse removes.
        """
        row = await self._owned_active_draft(draft_id, owner_user_id)
        if row.revision != expected_revision:
            raise DraftConflictError("Pack draft revision is stale")
        validation = self._validation(dict(row.artifact or {}))
        if not validation["valid"]:
            raise DraftValidationError(
                "Pack draft is invalid: " + "; ".join(validation["errors"])
            )
        artifact = _parse_artifact(row.artifact)
        attachments = await self._attachments_for(draft_id=row.id)
        content_hash = artifact_hash(artifact, _attachment_receipts(attachments))
        try:
            pack = await self._packs.create_personal_pack(
                name=row.name,
                manifest_yaml=artifact.manifest_yaml,
                files=artifact.files,
                # The author emits a complete file set; inheriting the starter on
                # top of it would leak an unrelated prompt into the new pack.
                inherit_starter=False,
                access_token=access_token,
            )
        except McpPackProviderError as exc:
            raise DraftValidationError(f"Pack could not be saved: {exc.safe_message}") from exc
        row.status = "saved"
        row.revision += 1
        row.updated_at = datetime.datetime.now(datetime.UTC)
        await self._db.flush()
        return SavedPackRecord(
            mcp_pack_id=pack.id,
            name=pack.name,
            version=pack.version,
            documents=artifact.doc_names,
            content_hash=content_hash,
        )

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

    def _validation(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Structural verdict for editor and chat feedback.

        MCP's ``load_plugin_manifest`` is the authority on admissibility and
        runs at save time. This check exists so a conversational turn can say
        what is missing without a network round-trip, and so an authority
        violation is caught even if the save path changes.
        """
        if not payload:
            return {"valid": False, "errors": ["the pack has no manifest yet"]}
        try:
            artifact = _parse_artifact(payload)
        except ValidationError as exc:
            return {"valid": False, "errors": [item["msg"] for item in exc.errors()][:20]}
        errors = artifact.structural_errors()
        if errors:
            return {"valid": False, "errors": errors}
        return {
            "valid": True,
            "errors": [],
            "documents": list(artifact.doc_names),
            "audience_render": normalized_audience(artifact),
        }

    async def _draft(self, row: HarnessPackDraftRow) -> DraftRecord:
        return DraftRecord(
            row.id, row.conversation_id, row.owner_user_id, row.status, row.revision, row.name,
            dict(row.artifact or {}), dict(row.validation or {}), dict(row.discovery_context or {}),
            tuple(await self._attachments_for(draft_id=row.id)),
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


def _parse_artifact(payload: dict[str, Any]) -> PackDraftArtifact:
    """Build the draft artifact from its stored JSON form.

    The stored shape is ``{"manifest_yaml": str, "files": {path: content}}`` --
    the pack itself, not a second schema describing one.
    """
    return PackDraftArtifact.model_validate(payload)
