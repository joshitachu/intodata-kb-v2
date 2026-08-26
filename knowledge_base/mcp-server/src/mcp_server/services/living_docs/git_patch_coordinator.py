"""Candidate-first Git patch orchestration.

This is the narrow S4 bridge between a Git source revision and the existing
field-level patch executor.  It copies the active output into a private
candidate workspace, runs the patch there with legacy RAG publication disabled,
then delegates all visibility changes to :mod:`candidate_publisher`.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from mcp_server.services.group_doc_dependency import DOC_PACK_MANIFEST_FILENAME, GroupDocDependency
from mcp_server.services.living_docs.candidate_publisher import CandidatePublisher, CandidatePublicationReceipt
from mcp_server.services.living_docs.candidate_store import FileCandidateStore, CandidateNotFound
from mcp_server.services.living_docs.contracts import (
    ArtifactKind,
    CandidateState,
    ChangeKind,
    ChangeSet,
    DeterministicImpactFloor,
    PatchCandidate,
    PatchTarget,
    SemanticChange,
    SourceOrigin,
    SourceRevision,
)
from mcp_server.services.log_manager import LogManager
from mcp_server.services.rag_support_package import RAG_SUPPORT_DIRNAME
from mcp_server.services.scanner_service import ScannerService
from mcp_server.services.true_patch_executor import PatchResult


class CandidatePatchExecutor(Protocol):
    async def run(self, **kwargs: object) -> PatchResult: ...


@dataclass(frozen=True, slots=True)
class GitPatchOutcome:
    action: str
    candidate_id: str
    knowledge_revision_id: str
    patched_docs: tuple[str, ...] = ()
    reason_code: str | None = None
    publication: CandidatePublicationReceipt | None = None
    patch_summary: Mapping[str, object] | None = None


class GitPatchCoordinator:
    """Runs one bounded Git change through a candidate workspace."""

    def __init__(
        self,
        *,
        store: FileCandidateStore,
        publisher: CandidatePublisher,
        executor: CandidatePatchExecutor,
    ) -> None:
        self._store = store
        self._publisher = publisher
        self._executor = executor

    def create_candidate(
        self,
        *,
        project_id: str,
        source_identity: str,
        before_sha: str,
        after_sha: str,
        changed_groups: list[str],
        active_docs_dir: Path,
        base_knowledge_revision_id: str | None,
    ) -> PatchCandidate:
        """Create a source-bound patch candidate and its deterministic floor."""

        source = SourceRevision(
            project_id=project_id,
            origin=SourceOrigin.GIT,
            source_identity=source_identity,
            revision_id=after_sha,
            content_sha256=hashlib.sha256(after_sha.encode("utf-8")).hexdigest(),
        )
        groups = tuple(sorted({group for group in changed_groups if group}))
        if not groups:
            raise ValueError("A Git patch candidate requires changed groups")
        changes = ChangeSet(
            source_revision=source,
            base_source_revision_id=before_sha or None,
            idempotency_key=f"git:{project_id}:{after_sha}",
            changes=tuple(
                SemanticChange(
                    kind=ChangeKind.SEMANTIC,
                    logical_path=f"scan/{group}.json",
                    field_id=f"group:{group}",
                    before_sha256=hashlib.sha256(f"{before_sha}:{group}".encode()).hexdigest(),
                    after_sha256=hashlib.sha256(f"{after_sha}:{group}".encode()).hexdigest(),
                )
                for group in groups
            ),
        )
        floor = DeterministicImpactFloor(
            source_revision_key=source.key,
            targets=tuple(self._targets_for_groups(groups, active_docs_dir)),
            evidence_ids=tuple(f"scan-group:{group}" for group in groups),
        )
        candidate = PatchCandidate.new(
            change_set=changes,
            impact_floor=floor,
            base_knowledge_revision_id=base_knowledge_revision_id,
            knowledge_revision_id=f"kr-{hashlib.sha256(source.key.encode()).hexdigest()[:24]}",
        )
        return self._store.create(candidate)

    def create_agent_candidate(
        self,
        *,
        project_id: str,
        source_identity: str,
        before_sha: str,
        after_sha: str,
        changed_files: list[object],
        base_knowledge_revision_id: str | None,
    ) -> PatchCandidate:
        """Create an idempotent candidate whose semantic scope is agent-owned.

        Unlike the legacy group patch lane, this candidate does not require a
        source path to contain a scanner group name.  The deterministic floor is
        the immutable Git diff plus the single patch-report boundary; the
        OpenCode worker may propose document operations only through that report.
        """

        if not changed_files:
            raise ValueError("An OpenCode Git patch candidate requires changed files")
        source = SourceRevision(
            project_id=project_id,
            origin=SourceOrigin.GIT,
            source_identity=source_identity,
            revision_id=after_sha,
            content_sha256=hashlib.sha256(after_sha.encode("utf-8")).hexdigest(),
        )
        changes: list[SemanticChange] = []
        for item in changed_files:
            status = str(getattr(item, "status", "M") or "M").upper()
            path = str(getattr(item, "path", "") or "")
            previous = getattr(item, "previous_path", None)
            if not path:
                raise ValueError("Changed file path is required")
            kind = {
                "A": ChangeKind.ADDED,
                "D": ChangeKind.DELETED,
                "R": ChangeKind.RENAMED,
            }.get(status[:1], ChangeKind.MODIFIED)
            changes.append(
                SemanticChange(
                    kind=kind,
                    logical_path=path,
                    before_sha256=(
                        None
                        if kind is ChangeKind.ADDED
                        else hashlib.sha256(f"{before_sha}:{previous or path}".encode()).hexdigest()
                    ),
                    after_sha256=(
                        None
                        if kind is ChangeKind.DELETED
                        else hashlib.sha256(f"{after_sha}:{path}".encode()).hexdigest()
                    ),
                    previous_logical_path=(
                        str(previous) if kind is ChangeKind.RENAMED and previous else None
                    ),
                )
            )
        change_set = ChangeSet(
            source_revision=source,
            base_source_revision_id=before_sha or None,
            # An immutable source revision is replay-safe within one contract
            # version.  A contract revision intentionally gets a new candidate,
            # retaining any prior held candidate as audit evidence.
            idempotency_key=f"git:{project_id}:{after_sha}:opencode-v3",
            changes=tuple(changes),
        )
        floor = DeterministicImpactFloor(
            source_revision_key=source.key,
            targets=(
                PatchTarget(
                    artifact_id="support/opencode-patch-report.json",
                    section_id="report",
                    artifact_kind=ArtifactKind.SUPPORT,
                ),
            ),
            evidence_ids=("git:diff",),
            mapper_version="opencode-v3",
        )
        candidate = PatchCandidate.new(
            change_set=change_set,
            impact_floor=floor,
            base_knowledge_revision_id=base_knowledge_revision_id,
            knowledge_revision_id=(
                f"kr-{hashlib.sha256(source.key.encode()).hexdigest()[:24]}"
            ),
        )
        try:
            existing = self._store.get_candidate(candidate.id)
        except CandidateNotFound:
            return self._store.create(candidate)
        if existing.change_set.source_revision.key != source.key:
            raise ValueError("Existing idempotent candidate is bound to another source revision")
        return existing

    def create_zip_candidate(
        self,
        *,
        source_revision: SourceRevision,
        base_source_revision_id: str | None,
        changed_groups: list[str],
        active_docs_dir: Path,
        base_knowledge_revision_id: str | None,
    ) -> PatchCandidate:
        """Create the same candidate contract for an immutable ZIP revision."""

        if source_revision.origin is not SourceOrigin.ZIP:
            raise ValueError("ZIP candidate requires a ZIP source revision")
        groups = tuple(sorted({group for group in changed_groups if group}))
        if not groups:
            raise ValueError("A ZIP patch candidate requires changed groups")
        changes = ChangeSet(
            source_revision=source_revision,
            base_source_revision_id=base_source_revision_id,
            idempotency_key=(
                f"zip:{source_revision.project_id}:{source_revision.revision_id}"
            ),
            changes=tuple(
                SemanticChange(
                    kind=ChangeKind.SEMANTIC,
                    logical_path=f"scan/{group}.json",
                    field_id=f"group:{group}",
                    before_sha256=hashlib.sha256(
                        f"{base_source_revision_id or 'baseline'}:{group}".encode()
                    ).hexdigest(),
                    after_sha256=hashlib.sha256(
                        f"{source_revision.revision_id}:{group}".encode()
                    ).hexdigest(),
                )
                for group in groups
            ),
        )
        floor = DeterministicImpactFloor(
            source_revision_key=source_revision.key,
            targets=tuple(self._targets_for_groups(groups, active_docs_dir)),
            evidence_ids=tuple(f"scan-group:{group}" for group in groups),
        )
        candidate = PatchCandidate.new(
            change_set=changes,
            impact_floor=floor,
            base_knowledge_revision_id=base_knowledge_revision_id,
            knowledge_revision_id=(
                f"kr-{hashlib.sha256(source_revision.key.encode()).hexdigest()[:24]}"
            ),
        )
        return self._store.create(candidate)

    async def run(
        self,
        candidate: PatchCandidate,
        *,
        active_output_dir: Path,
        candidate_scan_output_dir: Path,
        changed_groups: list[str],
        platform: str,
        log_mgr: LogManager,
        before_sha: str,
        after_sha: str,
        active_source_revision_key: str,
        source_path: str | None = None,
        rollout_stage: str = "auto_scoped",
        approved: bool = False,
        low_risk_max_artifacts: int = 3,
        changed_files: list[object] | None = None,
        full_diff_text: str = "",
        scan_before_patch: bool = True,
    ) -> GitPatchOutcome:
        """Patch, stage, validate, and activate without any full fallback."""

        status = self._store.status(candidate.id)
        if status.state is CandidateState.ACTIVE:
            return GitPatchOutcome(
                "active",
                candidate.id,
                candidate.knowledge_revision_id,
                patched_docs=self._reported_docs(candidate.id),
                reason_code="already_active",
            )
        if status.state in {CandidateState.RAG_PREPARED, CandidateState.RECOVERY_REQUIRED}:
            return GitPatchOutcome(
                "prepared",
                candidate.id,
                candidate.knowledge_revision_id,
                reason_code=status.reason_code or "human_approval_required",
            )
        if status.state is CandidateState.QUEUED:
            self._store.transition(
                candidate.id, expected_version=status.version, target=CandidateState.PREPARING
            )
        workspace_output = self._workspace_output(candidate.id, active_output_dir)
        self._copy_active_output(active_output_dir, workspace_output)
        scan_output_dir = candidate_scan_output_dir
        if source_path is not None and scan_before_patch:
            scan = await ScannerService(log_manager=log_mgr).run_scan(
                project_id=candidate.change_set.source_revision.project_id,
                repo_path=source_path,
                platform=platform,
                output_dir=str(workspace_output),
            )
            if not scan.get("success"):
                return self._hold(
                    candidate,
                    str(scan.get("error") or "candidate_scan_failed"),
                    "candidate_scan_failed",
                )
            scan_output_dir = workspace_output
        result = await self._executor.run(
            project_id=candidate.change_set.source_revision.project_id,
            candidate_id=candidate.id,
            output_dir=workspace_output,
            changed_groups=changed_groups,
            platform=platform,
            log_mgr=log_mgr,
            before_sha=before_sha,
            after_sha=after_sha,
            trigger="living_document_candidate",
            scan_output_dir=scan_output_dir,
            publish_rag=False,
            changed_files=changed_files or [],
            full_diff_text=full_diff_text,
        )
        if result.action != "true_patch":
            reason_code = str(result.summary.get("reason_code") or "patch_validation_failed")
            return self._hold(
                candidate,
                result.error or "patch_validation_failed",
                reason_code,
                patch_summary=result.summary,
            )
        try:
            publication = await self._publisher.prepare(
                candidate,
                candidate_docs_dir=workspace_output / "docs",
                active_docs_dir=active_output_dir / "docs",
            )
            activation_reason = self._activation_hold_reason(
                rollout_stage=rollout_stage,
                approved=approved,
                publication=publication,
                patch_result=result,
                low_risk_max_artifacts=low_risk_max_artifacts,
            )
            if activation_reason is not None:
                return GitPatchOutcome(
                    action="prepared",
                    candidate_id=candidate.id,
                    knowledge_revision_id=candidate.knowledge_revision_id,
                    patched_docs=tuple(result.patched_docs),
                    reason_code=activation_reason,
                    publication=publication,
                    patch_summary=result.summary,
                )
            await self._publisher.activate(
                candidate,
                candidate_docs_dir=workspace_output / "docs",
                active_output_dir=active_output_dir,
                active_source_revision_key=active_source_revision_key,
            )
        except Exception as exc:
            return self._hold(
                candidate,
                str(exc),
                "candidate_publication_failed",
                patch_summary=result.summary,
            )
        return GitPatchOutcome(
            action="active",
            candidate_id=candidate.id,
            knowledge_revision_id=candidate.knowledge_revision_id,
            patched_docs=tuple(result.patched_docs),
            publication=publication,
            patch_summary=result.summary,
        )

    @staticmethod
    def _activation_hold_reason(
        *,
        rollout_stage: str,
        approved: bool,
        publication: CandidatePublicationReceipt,
        patch_result: PatchResult,
        low_risk_max_artifacts: int,
    ) -> str | None:
        if rollout_stage == "shadow":
            return "shadow_proposal_only"
        if rollout_stage == "approval_only" and not approved:
            return "human_approval_required"
        if rollout_stage == "auto_low_risk" and not approved:
            changed = len(publication.changed_logical_ids) + len(publication.deleted_logical_ids)
            deterministic = patch_result.summary.get("patch_plan_producer") == "deterministic"
            if not deterministic or changed <= 0 or changed > low_risk_max_artifacts:
                return "human_approval_required"
        if rollout_stage not in {"shadow", "approval_only", "auto_low_risk", "auto_scoped"}:
            return "invalid_rollout_stage"
        return None

    def _targets_for_groups(self, groups: tuple[str, ...], docs_dir: Path) -> list[PatchTarget]:
        dependency = GroupDocDependency.load(
            docs_dir / RAG_SUPPORT_DIRNAME / DOC_PACK_MANIFEST_FILENAME
        )
        if dependency is None:
            raise ValueError("traceability_required")
        docs = set(dependency.cross_cutting_docs())
        for group in groups:
            docs.update(dependency.docs_for_group(group))
        if not docs:
            raise ValueError("traceability_required")
        targets = [
            PatchTarget(
                artifact_id=f"_rag_support/phase2_analysis/{group}.md",
                section_id="phase2",
                artifact_kind=ArtifactKind.INTERNAL_ANALYSIS,
            )
            for group in groups
        ]
        targets.extend(
            PatchTarget(
                artifact_id=f"{doc}.md" if not doc.endswith(".md") else doc,
                section_id="mapped",
                artifact_kind=ArtifactKind.CANONICAL_DOCUMENT,
            )
            for doc in sorted(docs)
        )
        return targets

    def _workspace_output(self, candidate_id: str, active_output_dir: Path) -> Path:
        root = self._store.candidate_root(candidate_id) / "workspace"
        return root / "output"

    def _reported_docs(self, candidate_id: str) -> tuple[str, ...]:
        path = self._workspace_output(candidate_id, Path(".")) / "_reingestion" / "patch_report.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            operations = payload.get("operations") if isinstance(payload, dict) else None
            if not isinstance(operations, list):
                return ()
            return tuple(
                sorted(
                    str(item["path"])
                    for item in operations
                    if isinstance(item, dict) and isinstance(item.get("path"), str)
                )
            )
        except (OSError, json.JSONDecodeError):
            return ()

    @staticmethod
    def _copy_active_output(active_output_dir: Path, workspace_output: Path) -> None:
        if workspace_output.exists():
            shutil.rmtree(workspace_output)
        workspace_output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copytree(
            active_output_dir,
            workspace_output,
            ignore=shutil.ignore_patterns("docs_staging", "docs_prev", ".living_docs"),
        )

    def _hold(
        self,
        candidate: PatchCandidate,
        detail: str,
        reason_code: str,
        *,
        patch_summary: Mapping[str, object] | None = None,
    ) -> GitPatchOutcome:
        status = self._store.status(candidate.id)
        if status.state is CandidateState.RECOVERY_REQUIRED:
            reason_code = status.reason_code or "docs_activation_recovery_required"
        elif status.state not in {CandidateState.ACTIVE, CandidateState.HELD, CandidateState.FAILED, CandidateState.SUPERSEDED}:
            self._store.transition(
                candidate.id,
                expected_version=status.version,
                target=CandidateState.HELD,
                reason_code=reason_code,
            )
        return GitPatchOutcome(
            action="held",
            candidate_id=candidate.id,
            knowledge_revision_id=candidate.knowledge_revision_id,
            reason_code=reason_code,
            patch_summary=patch_summary,
        )
