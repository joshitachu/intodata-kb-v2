"""Idempotent baseline priming for projects with validated existing docs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from mcp_server.services.living_docs.candidate_publisher import collect_revision_artifacts
from mcp_server.services.living_docs.rag_revisions import RagMutation
from mcp_server.services.rag_bridge import RagBridge


@dataclass(frozen=True, slots=True)
class BaselinePrimeReceipt:
    project_id: str
    source_revision_id: str
    knowledge_revision_id: str
    artifact_count: int
    embedded_content_count: int
    reused_content_count: int
    already_active: bool = False


async def prime_project_baseline(
    *,
    project_id: str,
    source_revision_id: str,
    docs_dir: Path,
    rag: RagBridge,
) -> BaselinePrimeReceipt:
    """Create the first revision from existing docs; never generate content."""

    if not source_revision_id:
        raise ValueError("source revision is required to prime a baseline")
    artifacts = collect_revision_artifacts(project_id, docs_dir)
    if not artifacts:
        raise ValueError("existing docs contain no baseline artifacts")
    digest = hashlib.sha256()
    digest.update(source_revision_id.encode("utf-8"))
    for logical_id, artifact in sorted(artifacts.items()):
        digest.update(logical_id.encode("utf-8"))
        digest.update(artifact.content_hash.encode("ascii"))
    revision_id = f"kr-baseline-{digest.hexdigest()[:24]}"
    active = await rag.active_knowledge_revision(project_id)
    if active == revision_id:
        return BaselinePrimeReceipt(
            project_id, source_revision_id, revision_id, len(artifacts), 0, len(artifacts), True
        )
    if active is not None:
        raise ValueError("project already has an active knowledge revision")
    prepared = await rag.prepare_revision(
        project_id,
        revision_id,
        [RagMutation.upsert(artifact) for artifact in artifacts.values()],
        base_knowledge_revision_id=None,
    )
    await rag.validate_revision(
        project_id,
        revision_id,
        required_artifact_hashes={key: value.content_hash for key, value in artifacts.items()},
    )
    await rag.activate_revision(project_id, revision_id)
    return BaselinePrimeReceipt(
        project_id=project_id,
        source_revision_id=source_revision_id,
        knowledge_revision_id=revision_id,
        artifact_count=len(artifacts),
        embedded_content_count=prepared.new_embedding_count,
        reused_content_count=prepared.reused_content_count,
    )
