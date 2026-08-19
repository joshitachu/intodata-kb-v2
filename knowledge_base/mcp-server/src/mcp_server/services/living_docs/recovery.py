"""Idempotent reconciliation and rollback for interrupted candidate activation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

from mcp_server.services.atomic_swap import swap_docs
from mcp_server.services.living_docs.audit import FilePatchAuditStore, PatchAuditReceipt
from mcp_server.services.living_docs.candidate_publisher import CandidatePublisher
from mcp_server.services.living_docs.candidate_store import FileCandidateStore
from mcp_server.services.living_docs.contracts import CandidateState, PatchCandidate
from mcp_server.services.rag_bridge import RagBridge


class CandidateRecoveryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RecoveryReceipt:
    candidate_id: str
    action: str
    state: CandidateState
    active_knowledge_revision_id: str | None
    reason_code: str | None = None


class CandidateRecoveryCoordinator:
    """Reconcile journal/RAG/docs state without rerunning a patch or provider."""

    def __init__(
        self,
        *,
        store: FileCandidateStore,
        rag: RagBridge,
        publisher: CandidatePublisher,
        audit: FilePatchAuditStore | None = None,
    ) -> None:
        self._store = store
        self._rag = rag
        self._publisher = publisher
        self._audit = audit

    async def reconcile(
        self,
        candidate_id: str,
        *,
        active_output_dir: Path,
        active_source_revision_key: str,
    ) -> RecoveryReceipt:
        candidate = self._store.get_candidate(candidate_id)
        status = self._store.status(candidate_id)
        active_rag = await self._rag.active_knowledge_revision(
            candidate.change_set.source_revision.project_id
        )
        if status.state is CandidateState.ACTIVE:
            return self._receipt(candidate, "already_active", status.state, active_rag)
        if status.state not in {
            CandidateState.RAG_PREPARED,
            CandidateState.RECOVERY_REQUIRED,
        }:
            return self._receipt(
                candidate,
                "no_recovery_action",
                status.state,
                active_rag,
                "candidate_not_recoverable",
            )

        candidate_docs = self._candidate_docs(candidate_id)
        if active_rag == candidate.knowledge_revision_id:
            self._activate_docs(candidate_docs, active_output_dir)
            current = self._store.status(candidate_id)
            final = self._store.transition(
                candidate_id,
                expected_version=current.version,
                target=CandidateState.ACTIVE,
                active_source_revision_key=active_source_revision_key,
                active_knowledge_revision_id=candidate.base_knowledge_revision_id,
            )
            receipt = self._receipt(candidate, "completed_docs_activation", final.state, active_rag)
            self._audit_recovery(candidate, receipt)
            return receipt

        if status.state is CandidateState.RAG_PREPARED and active_rag == candidate.base_knowledge_revision_id:
            await self._publisher.activate(
                candidate,
                candidate_docs_dir=candidate_docs,
                active_output_dir=active_output_dir,
                active_source_revision_key=active_source_revision_key,
            )
            receipt = self._receipt(
                candidate,
                "replayed_activation",
                CandidateState.ACTIVE,
                candidate.knowledge_revision_id,
            )
            self._audit_recovery(candidate, receipt)
            return receipt

        current = self._store.status(candidate_id)
        final = self._store.transition(
            candidate_id,
            expected_version=current.version,
            target=CandidateState.SUPERSEDED,
            reason_code="active_revision_advanced",
        )
        receipt = self._receipt(
            candidate,
            "superseded",
            final.state,
            active_rag,
            "active_revision_advanced",
        )
        self._audit_recovery(candidate, receipt)
        return receipt

    async def rollback(
        self,
        target: PatchCandidate,
        *,
        expected_active_revision_id: str,
        active_output_dir: Path,
    ) -> RecoveryReceipt:
        """Restore retained RAG membership and its matching candidate docs."""

        project_id = target.change_set.source_revision.project_id
        active_rag = await self._rag.active_knowledge_revision(project_id)
        if active_rag != target.knowledge_revision_id:
            await self._rag.rollback_revision(
                project_id,
                target.knowledge_revision_id,
                expected_active_revision_id=expected_active_revision_id,
            )
        self._activate_docs(self._candidate_docs(target.id), active_output_dir)
        receipt = self._receipt(
            target,
            "rolled_back",
            CandidateState.ACTIVE,
            target.knowledge_revision_id,
        )
        self._audit_recovery(target, receipt)
        return receipt

    def _candidate_docs(self, candidate_id: str) -> Path:
        path = self._store.candidate_root(candidate_id) / "workspace" / "output" / "docs"
        if not path.is_dir():
            raise CandidateRecoveryError("candidate docs snapshot is not retained")
        return path

    @staticmethod
    def _activate_docs(candidate_docs: Path, active_output_dir: Path) -> None:
        staging = active_output_dir / "docs_staging"
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(candidate_docs, staging)
        try:
            swap_docs(active_output_dir)
        except Exception as exc:
            raise CandidateRecoveryError("docs activation still requires recovery") from exc

    @staticmethod
    def _receipt(
        candidate: PatchCandidate,
        action: str,
        state: CandidateState,
        active_rag: str | None,
        reason: str | None = None,
    ) -> RecoveryReceipt:
        return RecoveryReceipt(candidate.id, action, state, active_rag, reason)

    def _audit_recovery(self, candidate: PatchCandidate, receipt: RecoveryReceipt) -> None:
        if self._audit is None:
            return
        self._audit.append(PatchAuditReceipt(
            candidate_id=candidate.id,
            project_id=candidate.change_set.source_revision.project_id,
            source_revision_id=candidate.change_set.source_revision.revision_id,
            knowledge_revision_id=candidate.knowledge_revision_id,
            state=receipt.state.value,
            reason_code=receipt.reason_code,
            recovery_action=receipt.action,
        ))
