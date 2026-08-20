"""Governed chat proposals using the common living-document revision lifecycle."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Literal

from mcp_server.config import config
from mcp_server.services.evidence_doc_update import (
    EvidenceDocUpdateError,
    _normalize_section_name,
    _replace_markdown_section,
    _sanitize_evidence_refs,
    _sanitize_evidence_text,
    _sha256_hex,
    _validate_markdown_filename,
    _validate_project_id,
)
from mcp_server.services.living_docs.candidate_publisher import CandidatePublisher
from mcp_server.services.living_docs.candidate_store import FileCandidateStore
from mcp_server.services.living_docs.contracts import (
    ArtifactKind,
    ChangeKind,
    ChangeSet,
    DeterministicImpactFloor,
    PatchCandidate,
    PatchOperation,
    PatchOperationKind,
    PatchPlan,
    PatchPlanProducer,
    PatchTarget,
    SemanticChange,
    SourceOrigin,
    SourceRevision,
    canonical_json_bytes,
    candidate_id_for,
)
from mcp_server.services.log_manager import LogManager
from mcp_server.services.rag_bridge import RagBridge

AssertionKind = Literal["source_backed", "editorial"]
_OVERLAY_DIR = Path("_rag_support/editorial_overlays")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


@dataclass(frozen=True, slots=True)
class ChatPatchProposal:
    proposal_id: str
    project_id: str
    filename: str
    section: str
    assertion_kind: AssertionKind
    requested_by_user_id: int
    base_document_sha256: str
    base_knowledge_revision_id: str | None
    evidence_refs: tuple[str, ...]
    replacement_text: str
    patch_plan_hash: str
    compact_diff: str
    changed_scope: tuple[str, ...]
    estimated_input_tokens: int
    estimated_output_tokens: int
    created_at: str
    expires_at: str
    consumed_at: str | None = None
    approved_by_user_id: int | None = None


@dataclass(frozen=True, slots=True)
class ChatPatchActivation:
    proposal_id: str
    candidate_id: str
    knowledge_revision_id: str
    filename: str
    before_sha: str
    after_sha: str
    assertion_kind: AssertionKind
    changed_logical_ids: tuple[str, ...]
    embedded_content_count: int
    reused_content_count: int


class FileChatProposalStore:
    """Small immutable proposal store with one atomic consume transition."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def create(self, proposal: ChatPatchProposal) -> ChatPatchProposal:
        path = self._path(proposal.proposal_id)
        payload = json.dumps(asdict(proposal), sort_keys=True, separators=(",", ":"))
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            existing = self.get(proposal.proposal_id)
            if existing != proposal:
                raise EvidenceDocUpdateError(409, "A different proposal already uses this identity.")
            return existing
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return proposal

    def get(self, proposal_id: str) -> ChatPatchProposal:
        path = self._path(proposal_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            value["evidence_refs"] = tuple(value["evidence_refs"])
            value["changed_scope"] = tuple(value["changed_scope"])
            return ChatPatchProposal(**value)
        except FileNotFoundError as exc:
            raise EvidenceDocUpdateError(404, "Documentation patch proposal not found.") from exc
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise EvidenceDocUpdateError(409, "Documentation patch proposal is invalid.") from exc

    def consume(self, proposal: ChatPatchProposal, *, approver_user_id: int) -> None:
        current = self.get(proposal.proposal_id)
        if current != proposal or current.consumed_at is not None:
            raise EvidenceDocUpdateError(409, "Documentation patch approval was already used or changed.")
        consumed = asdict(current)
        consumed["consumed_at"] = datetime.now(timezone.utc).isoformat()
        consumed["approved_by_user_id"] = approver_user_id
        temporary = self._path(proposal.proposal_id).with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(consumed, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self._path(proposal.proposal_id))

    def _path(self, proposal_id: str) -> Path:
        if not _SAFE_ID.fullmatch(proposal_id):
            raise EvidenceDocUpdateError(400, "Invalid documentation patch proposal identifier.")
        return self._root / f"{proposal_id}.json"


class ChatPatchService:
    """Preview and activate exact chat edits without directly mutating live docs."""

    def __init__(
        self,
        *,
        log_manager: LogManager | None = None,
        proposal_store: FileChatProposalStore | None = None,
        candidate_store: FileCandidateStore | None = None,
        rag: RagBridge | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        root = config.data_dir / "living_docs"
        self._proposals = proposal_store or FileChatProposalStore(root / "chat_proposals")
        self._candidates = candidate_store or FileCandidateStore(root / "candidates")
        if rag is None:
            raw = RagBridge(log_manager=log_manager)
            rag = RagBridge(log_manager=log_manager, revisioned_rag=raw.http_revisioned_adapter())
        self._rag = rag
        self._publisher = CandidatePublisher(store=self._candidates, rag=rag)
        self._now = now or (lambda: datetime.now(timezone.utc))

    def get_proposal(self, proposal_id: str) -> ChatPatchProposal:
        """Return proposal metadata for route-level scope authorization."""

        return self._proposals.get(proposal_id)

    async def preview(
        self,
        *,
        project_id: str,
        filename: str,
        section: str,
        replacement_text: str,
        evidence_refs: list[str] | None,
        assertion_kind: AssertionKind,
        requested_by_user_id: int,
    ) -> ChatPatchProposal:
        _validate_project_id(project_id)
        safe_filename = _validate_markdown_filename(filename)
        normalized_section = _normalize_section_name(section)
        if normalized_section is None:
            raise EvidenceDocUpdateError(400, "An exact documentation section is required.")
        if assertion_kind not in {"source_backed", "editorial"}:
            raise EvidenceDocUpdateError(400, "Invalid assertion kind.")
        replacement = _sanitize_evidence_text(replacement_text)
        refs = tuple(_sanitize_evidence_refs(evidence_refs))
        if assertion_kind == "source_backed" and refs == ("chat_prompt",):
            raise EvidenceDocUpdateError(409, "Source-backed changes require explicit evidence references.")
        doc_path = self._doc_path(project_id, safe_filename)
        try:
            original = doc_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise EvidenceDocUpdateError(404, f"Documentation file not found: {safe_filename}") from exc
        base_sha = _sha256_hex(original)
        updated = (
            _replace_markdown_section(original, normalized_section, replacement)
            if assertion_kind == "source_backed"
            else original
        )
        active_revision = await self._rag.active_knowledge_revision(project_id)
        target = PatchTarget(
            artifact_id=(safe_filename if assertion_kind == "source_backed" else f"{_OVERLAY_DIR}/{base_sha[:16]}.md"),
            section_id=normalized_section,
            artifact_kind=(ArtifactKind.CANONICAL_DOCUMENT if assertion_kind == "source_backed" else ArtifactKind.INTERNAL_ANALYSIS),
        )
        replacement_hash = _sha256_hex(replacement)
        source_payload = {
            "project_id": project_id,
            "filename": safe_filename,
            "section": normalized_section,
            "base_sha": base_sha,
            "assertion_kind": assertion_kind,
            "replacement_sha": replacement_hash,
            "evidence_refs": refs,
        }
        source_hash = hashlib.sha256(canonical_json_bytes(source_payload)).hexdigest()
        source = SourceRevision(
            project_id=project_id,
            origin=SourceOrigin.CHAT,
            source_identity=f"chat:{requested_by_user_id}",
            revision_id=source_hash,
            content_sha256=source_hash,
        )
        proposal_id = f"ldp-{source_hash[:32]}"
        candidate_id = candidate_id_for(
            project_id=project_id, idempotency_key=f"chat:{proposal_id}"
        )
        plan = PatchPlan(
            candidate_id=candidate_id,
            source_revision_key=source.key,
            base_knowledge_revision_id=active_revision,
            producer=PatchPlanProducer.DETERMINISTIC,
            operations=(PatchOperation(
                target=target,
                kind=PatchOperationKind.REPLACE if assertion_kind == "source_backed" else PatchOperationKind.CREATE,
                evidence_ids=refs,
                replacement_sha256=replacement_hash,
                expected_current_sha256=base_sha,
            ),),
        )
        now = self._now().astimezone(timezone.utc)
        diff = "".join(difflib.unified_diff(
            original.splitlines(keepends=True), updated.splitlines(keepends=True),
            fromfile=safe_filename, tofile=safe_filename, n=2,
        ))
        if assertion_kind == "editorial":
            diff = f"+ editorial overlay for {safe_filename}#{normalized_section}\n+ {replacement[:1000]}\n"
        proposal = ChatPatchProposal(
            proposal_id=proposal_id,
            project_id=project_id,
            filename=safe_filename,
            section=normalized_section,
            assertion_kind=assertion_kind,
            requested_by_user_id=requested_by_user_id,
            base_document_sha256=base_sha,
            base_knowledge_revision_id=active_revision,
            evidence_refs=refs,
            replacement_text=replacement,
            patch_plan_hash=plan.sha256,
            compact_diff=diff[:8000],
            changed_scope=(target.key, f"rag:{'canonical' if assertion_kind == 'source_backed' else 'editorial_overlays'}"),
            estimated_input_tokens=max(1, (len(original) + len(replacement)) // 4),
            estimated_output_tokens=max(1, len(replacement) // 4),
            created_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=30)).isoformat(),
        )
        return self._proposals.create(proposal)

    async def activate(
        self,
        *,
        proposal_id: str,
        expected_patch_plan_hash: str,
        approver_user_id: int,
        editorial_approval_authorized: bool,
    ) -> ChatPatchActivation:
        proposal = self._proposals.get(proposal_id)
        if proposal.patch_plan_hash != expected_patch_plan_hash:
            raise EvidenceDocUpdateError(409, "Documentation patch approval does not match its preview.")
        if datetime.fromisoformat(proposal.expires_at) <= self._now():
            raise EvidenceDocUpdateError(409, "Documentation patch approval has expired.")
        if proposal.assertion_kind == "editorial" and not editorial_approval_authorized:
            raise EvidenceDocUpdateError(403, "Only a project owner may approve an editorial assertion.")
        doc_path = self._doc_path(proposal.project_id, proposal.filename)
        original = doc_path.read_text(encoding="utf-8")
        if _sha256_hex(original) != proposal.base_document_sha256:
            raise EvidenceDocUpdateError(409, "Documentation changed after the proposal was created.")
        active_revision = await self._rag.active_knowledge_revision(proposal.project_id)
        if active_revision != proposal.base_knowledge_revision_id:
            raise EvidenceDocUpdateError(409, "Knowledge revision changed after the proposal was created.")

        candidate = self._create_candidate(proposal)
        if self._plan_for(candidate, proposal).sha256 != proposal.patch_plan_hash:
            raise EvidenceDocUpdateError(409, "Documentation patch plan cannot be reconstructed exactly.")
        active_output = config.project_root / "output" / proposal.project_id
        workspace = self._candidates.candidate_root(candidate.id) / "workspace" / "output"
        if not workspace.exists():
            shutil.copytree(active_output, workspace)
        candidate_doc = workspace / "docs" / proposal.filename
        if proposal.assertion_kind == "source_backed":
            updated = _replace_markdown_section(original, proposal.section, proposal.replacement_text)
            candidate_doc.write_text(updated, encoding="utf-8")
        else:
            updated = original
            self._write_overlay(workspace / "docs", proposal, approver_user_id)

        try:
            publication = await self._publisher.prepare(
                candidate,
                candidate_docs_dir=workspace / "docs",
                active_docs_dir=active_output / "docs",
            )
            await self._publisher.activate(
                candidate,
                candidate_docs_dir=workspace / "docs",
                active_output_dir=active_output,
                active_source_revision_key=candidate.change_set.source_revision.key,
            )
        except EvidenceDocUpdateError:
            raise
        except Exception as exc:
            raise EvidenceDocUpdateError(502, "Documentation patch candidate could not be activated.") from exc
        self._proposals.consume(proposal, approver_user_id=approver_user_id)
        return ChatPatchActivation(
            proposal_id=proposal.proposal_id,
            candidate_id=candidate.id,
            knowledge_revision_id=candidate.knowledge_revision_id,
            filename=proposal.filename,
            before_sha=f"sha256:{proposal.base_document_sha256}",
            after_sha=f"sha256:{_sha256_hex(updated)}",
            assertion_kind=proposal.assertion_kind,
            changed_logical_ids=publication.changed_logical_ids,
            embedded_content_count=publication.embedded_content_count,
            reused_content_count=publication.reused_content_count,
        )

    def _create_candidate(self, proposal: ChatPatchProposal) -> PatchCandidate:
        source_payload = {
            "project_id": proposal.project_id,
            "filename": proposal.filename,
            "section": proposal.section,
            "base_sha": proposal.base_document_sha256,
            "assertion_kind": proposal.assertion_kind,
            "replacement_sha": _sha256_hex(proposal.replacement_text),
            "evidence_refs": proposal.evidence_refs,
        }
        source_hash = hashlib.sha256(canonical_json_bytes(source_payload)).hexdigest()
        source = SourceRevision(
            project_id=proposal.project_id,
            origin=SourceOrigin.CHAT,
            source_identity=f"chat:{proposal.requested_by_user_id}",
            revision_id=source_hash,
            content_sha256=source_hash,
        )
        target = PatchTarget(
            artifact_id=(
                proposal.filename
                if proposal.assertion_kind == "source_backed"
                else f"{_OVERLAY_DIR}/{proposal.base_document_sha256[:16]}.md"
            ),
            section_id=proposal.section,
            artifact_kind=(ArtifactKind.CANONICAL_DOCUMENT if proposal.assertion_kind == "source_backed" else ArtifactKind.INTERNAL_ANALYSIS),
        )
        change_set = ChangeSet(
            source_revision=source,
            base_source_revision_id=None,
            idempotency_key=f"chat:{proposal.proposal_id}",
            changes=(SemanticChange(
                kind=ChangeKind.SEMANTIC,
                logical_path=f"docs/{proposal.filename}",
                field_id=proposal.section,
                before_sha256=proposal.base_document_sha256,
                after_sha256=_sha256_hex(proposal.replacement_text),
            ),),
        )
        candidate = PatchCandidate.new(
            change_set=change_set,
            impact_floor=DeterministicImpactFloor(
                source_revision_key=source.key,
                targets=(target,),
                evidence_ids=proposal.evidence_refs,
                mapper_version="chat-v1",
            ),
            base_knowledge_revision_id=proposal.base_knowledge_revision_id,
            knowledge_revision_id=f"kr-{hashlib.sha256(proposal.patch_plan_hash.encode()).hexdigest()[:24]}",
        )
        return self._candidates.create(candidate)

    @staticmethod
    def _plan_for(candidate: PatchCandidate, proposal: ChatPatchProposal) -> PatchPlan:
        target = candidate.impact_floor.targets[0]
        return PatchPlan(
            candidate_id=candidate.id,
            source_revision_key=candidate.change_set.source_revision.key,
            base_knowledge_revision_id=proposal.base_knowledge_revision_id,
            producer=PatchPlanProducer.DETERMINISTIC,
            operations=(PatchOperation(
                target=target,
                kind=(PatchOperationKind.REPLACE if proposal.assertion_kind == "source_backed" else PatchOperationKind.CREATE),
                evidence_ids=proposal.evidence_refs,
                replacement_sha256=_sha256_hex(proposal.replacement_text),
                expected_current_sha256=proposal.base_document_sha256,
            ),),
        )

    @staticmethod
    def _write_overlay(docs_dir: Path, proposal: ChatPatchProposal, approver_user_id: int) -> None:
        directory = docs_dir / _OVERLAY_DIR
        directory.mkdir(parents=True, exist_ok=True)
        content = "\n".join((
            "---",
            f"proposal_id: {proposal.proposal_id}",
            f"target_document: {proposal.filename}",
            f"target_section: {proposal.section}",
            f"base_document_sha256: {proposal.base_document_sha256}",
            f"author_user_id: {proposal.requested_by_user_id}",
            f"approver_user_id: {approver_user_id}",
            "status: active",
            "---",
            "",
            f"# Editorial overlay for {proposal.filename} — {proposal.section}",
            "",
            proposal.replacement_text,
            "",
            "Evidence refs: " + ", ".join(proposal.evidence_refs),
            "",
        ))
        (directory / f"{proposal.proposal_id}.md").write_text(content, encoding="utf-8")

    @staticmethod
    def _doc_path(project_id: str, filename: str) -> Path:
        return config.project_root / "output" / project_id / "docs" / filename


def mark_stale_editorial_overlays(docs_dir: Path) -> int:
    """Mark overlays stale when their exact base document bytes no longer match."""

    changed = 0
    directory = docs_dir / _OVERLAY_DIR
    for path in sorted(directory.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        fields = dict(re.findall(r"^([a-z_]+):\s*(.+)$", content, re.MULTILINE))
        if fields.get("status") != "active":
            continue
        filename = fields.get("target_document", "")
        base_sha = fields.get("base_document_sha256", "")
        target = docs_dir / filename
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != base_sha:
            path.write_text(content.replace("status: active", "status: stale", 1), encoding="utf-8")
            changed += 1
    return changed
