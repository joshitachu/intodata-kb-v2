"""Publish a validated living-document candidate as one RAG knowledge revision.

The legacy bridge streams a document directly into RAG and the old true-patch
executor writes directly into the active docs directory.  This module is the
incremental alternative: it compares an isolated candidate tree with the active
tree, stages only the changed canonical/internal artifacts, validates membership,
then performs the active revision hand-off.  It deliberately has no full-sync or
full-generation fallback.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from mcp_server.services.atomic_swap import swap_docs
from mcp_server.services.doc_quality_scorer import is_published_doc_filename
from mcp_server.services.living_docs.candidate_store import FileCandidateStore
from mcp_server.services.living_docs.contracts import ArtifactKind, CandidateState, PatchCandidate
from mcp_server.services.living_docs.rag_revisions import RagArtifact, RagMutation
from mcp_server.services.rag_bridge import RagBridge
from mcp_server.services.rag_support_package import collect_internal_artifacts


class CandidatePublicationError(RuntimeError):
    """A candidate cannot safely move toward activation."""


@dataclass(frozen=True, slots=True)
class CandidatePublicationReceipt:
    candidate_id: str
    knowledge_revision_id: str
    base_knowledge_revision_id: str | None
    changed_logical_ids: tuple[str, ...]
    deleted_logical_ids: tuple[str, ...]
    embedded_content_count: int
    reused_content_count: int


def collect_revision_artifacts(project_id: str, docs_dir: Path) -> dict[str, RagArtifact]:
    """Return canonical and internal RAG artifacts from one docs-tree snapshot."""

    if not docs_dir.is_dir():
        raise CandidatePublicationError(f"Docs tree is absent: {docs_dir}")
    artifacts: dict[str, RagArtifact] = {}
    for path in sorted(docs_dir.glob("*.md")):
        if not is_published_doc_filename(path.name):
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        if not content.strip():
            continue
        logical_id = f"canonical/{path.name}"
        if logical_id in artifacts:
            raise CandidatePublicationError(f"Duplicate document logical id: {logical_id}")
        artifacts[logical_id] = RagArtifact(
            logical_id=logical_id,
            content=content,
            source_layer="canonical",
            artifact_type="document",
            metadata={"filename": path.name, "visibility": "canonical"},
        )

    for item in collect_internal_artifacts(docs_dir, project_id=project_id):
        metadata = dict(item["metadata"])
        filename = str(item["filename"])
        logical_id = f"internal/{filename}"
        if logical_id in artifacts:
            raise CandidatePublicationError(f"Duplicate internal logical id: {logical_id}")
        artifacts[logical_id] = RagArtifact(
            logical_id=logical_id,
            content=str(item["content"]),
            source_layer=str(metadata.get("source_layer") or "internal"),
            artifact_type=str(metadata.get("artifact_type") or "support"),
            metadata=metadata,
        )
    return artifacts


class CandidatePublisher:
    """Coordinates candidate artifacts with revision-aware RAG and docs swap."""

    def __init__(self, *, store: FileCandidateStore, rag: RagBridge) -> None:
        self._store = store
        self._rag = rag

    async def prepare(
        self,
        candidate: PatchCandidate,
        *,
        candidate_docs_dir: Path,
        active_docs_dir: Path,
    ) -> CandidatePublicationReceipt:
        """Stage an inactive targeted RAG revision from an immutable candidate.

        The initial baseline intentionally upserts the whole docs snapshot. Once
        a base revision is active, only content-hash changes and removals become
        mutations; unchanged vectors are inherited/reused by the RAG port.
        """

        status = self._store.status(candidate.id)
        if status.state is CandidateState.QUEUED:
            status = self._store.transition(
                candidate.id,
                expected_version=status.version,
                target=CandidateState.PREPARING,
            )
        if status.state not in {CandidateState.PREPARING, CandidateState.VALIDATING}:
            raise CandidatePublicationError(
                f"Candidate {candidate.id} cannot prepare from {status.state.value}"
            )

        # Editorial overlays are revision-bound to the exact canonical bytes
        # they annotated. Any Git, ZIP, or chat candidate that changes those
        # bytes carries the overlay forward as stale instead of silently
        # presenting an unsupported assertion as current truth.
        from mcp_server.services.living_docs.chat_patching import mark_stale_editorial_overlays

        mark_stale_editorial_overlays(candidate_docs_dir)
        candidate_artifacts = collect_revision_artifacts(
            candidate.change_set.source_revision.project_id, candidate_docs_dir
        )
        active_artifacts = (
            collect_revision_artifacts(candidate.change_set.source_revision.project_id, active_docs_dir)
            if active_docs_dir.is_dir()
            else {}
        )
        for logical_id, artifact in candidate_artifacts.items():
            self._store.write_artifact(
                candidate.id,
                logical_id=logical_id,
                artifact_kind=(
                    ArtifactKind.CANONICAL_DOCUMENT
                    if artifact.source_layer == "canonical"
                    else ArtifactKind.INTERNAL_ANALYSIS
                ),
                media_type="text/markdown",
                content=artifact.content.encode("utf-8"),
            )

        base_revision = candidate.base_knowledge_revision_id
        mutations: list[RagMutation] = []
        if base_revision is None:
            mutations.extend(RagMutation.upsert(item) for item in candidate_artifacts.values())
        else:
            for logical_id, artifact in candidate_artifacts.items():
                prior = active_artifacts.get(logical_id)
                if prior is None or prior.content_hash != artifact.content_hash:
                    mutations.append(RagMutation.upsert(artifact))
            mutations.extend(
                RagMutation.delete(logical_id)
                for logical_id in active_artifacts.keys() - candidate_artifacts.keys()
            )

        prepared = await self._rag.prepare_revision(
            candidate.change_set.source_revision.project_id,
            candidate.knowledge_revision_id,
            mutations,
            base_knowledge_revision_id=base_revision,
        )
        expected = {logical_id: item.content_hash for logical_id, item in candidate_artifacts.items()}
        await self._rag.validate_revision(
            candidate.change_set.source_revision.project_id,
            candidate.knowledge_revision_id,
            required_artifact_hashes=expected,
            required_deletes=tuple(sorted(active_artifacts.keys() - candidate_artifacts.keys())),
        )
        if status.state is CandidateState.PREPARING:
            status = self._store.transition(
                candidate.id,
                expected_version=status.version,
                target=CandidateState.VALIDATING,
            )
        if status.state is CandidateState.VALIDATING:
            self._store.transition(
                candidate.id,
                expected_version=status.version,
                target=CandidateState.RAG_PREPARED,
            )
        return CandidatePublicationReceipt(
            candidate_id=candidate.id,
            knowledge_revision_id=candidate.knowledge_revision_id,
            base_knowledge_revision_id=base_revision,
            changed_logical_ids=tuple(sorted(mutation.logical_id for mutation in mutations if mutation.kind.value == "upsert")),
            deleted_logical_ids=tuple(sorted(mutation.logical_id for mutation in mutations if mutation.kind.value == "delete")),
            embedded_content_count=prepared.new_embedding_count,
            reused_content_count=prepared.reused_content_count,
        )

    async def activate(
        self,
        candidate: PatchCandidate,
        *,
        candidate_docs_dir: Path,
        active_output_dir: Path,
        active_source_revision_key: str,
    ) -> None:
        """Make the prepared RAG revision and matching docs tree active.

        The RAG CAS is the authority check; docs are copied to the existing
        same-filesystem staging convention and atomically renamed immediately
        after that check. A docs swap error is surfaced as a recovery-required
        failure instead of falling back to direct writes or full generation.
        """

        status = self._store.status(candidate.id)
        if status.state is not CandidateState.RAG_PREPARED:
            raise CandidatePublicationError("Candidate RAG revision has not been prepared")
        await self._rag.activate_revision(
            candidate.change_set.source_revision.project_id,
            candidate.knowledge_revision_id,
        )
        staging = active_output_dir / "docs_staging"
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(candidate_docs_dir, staging)
        try:
            swap_docs(active_output_dir)
        except Exception as exc:
            self._store.transition(
                candidate.id,
                expected_version=status.version,
                target=CandidateState.RECOVERY_REQUIRED,
                reason_code="docs_activation_recovery_required",
            )
            raise CandidatePublicationError("RAG activation succeeded but docs swap failed; recovery required") from exc
        self._store.transition(
            candidate.id,
            expected_version=status.version,
            target=CandidateState.ACTIVE,
            active_source_revision_key=active_source_revision_key,
            active_knowledge_revision_id=candidate.base_knowledge_revision_id,
        )
