"""Resume only activations with durable authorization, never approval drafts."""

from __future__ import annotations

import json
from pathlib import Path

from mcp_server.services.living_docs.candidate_publisher import CandidatePublisher
from mcp_server.services.living_docs.candidate_store import FileCandidateStore
from mcp_server.services.living_docs.contracts import CandidateState
from mcp_server.services.living_docs.contracts import SourceOrigin
from mcp_server.services.living_docs.recovery import CandidateRecoveryCoordinator
from mcp_server.services.living_docs.run_journal import atomic_json


async def recover_git_activations(
    *, store: FileCandidateStore, rag, output: Path, source_baseline: str | None
) -> list[dict]:
    recovered = []
    for intent in sorted((store.root / "candidates").glob("*/activation_intent.json")):
        candidate = store.get_candidate(intent.parent.name)
        if candidate.change_set.source_revision.origin is not SourceOrigin.GIT:
            continue
        if candidate.change_set.base_source_revision_id != source_baseline:
            continue
        status = store.status(candidate.id)
        if status.state not in {
            CandidateState.RAG_PREPARED,
            CandidateState.RECOVERY_REQUIRED,
            CandidateState.ACTIVE,
        }:
            continue
        active = await rag.active_knowledge_revision(
            candidate.change_set.source_revision.project_id
        )
        if status.state is CandidateState.ACTIVE and active != candidate.knowledge_revision_id:
            continue
        if active == candidate.knowledge_revision_id and status.state is not CandidateState.ACTIVE:
            from mcp_server.services.living_docs.candidate_publisher import (
                collect_revision_artifacts,
            )

            def hashes(path):
                return (
                    {
                        key: value.content_hash
                        for key, value in collect_revision_artifacts(
                            candidate.change_set.source_revision.project_id, path
                        ).items()
                    }
                    if path.is_dir()
                    else {}
                )

            current = hashes(output / "docs")
            expected = hashes(intent.parent / "workspace/output/docs")
            baseline_path = intent.parent / "publication_base.json"
            baseline = json.loads(baseline_path.read_text()) if baseline_path.is_file() else None
            if current != expected and current != baseline and current:
                raise ValueError(
                    "Recovery found unexpected document edits; manual reconciliation required"
                )
        receipt = await CandidateRecoveryCoordinator(
            store=store,
            rag=rag,
            publisher=CandidatePublisher(store=store, rag=rag),
        ).reconcile(
            candidate.id,
            active_output_dir=output,
            active_source_revision_key=candidate.change_set.source_revision.key,
        )
        if receipt.state is not CandidateState.ACTIVE:
            continue
        index = intent.parent / "workspace/output/_reingestion/next_evidence_index.json"
        if index.is_file():
            atomic_json(
                output / "_version_history/evidence_index.json", json.loads(index.read_text())
            )
        summary_path = intent.parent / "patch_summary.json"
        summary = json.loads(summary_path.read_text()) if summary_path.is_file() else {}
        summary.update(
            action="true_patch",
            outcome="bounded_patch",
            reason="Recovered interrupted publication",
            before=source_baseline,
            after=candidate.change_set.source_revision.revision_id,
            candidate_id=candidate.id,
            touched_docs=summary.get("patched_docs", []),
        )
        recovered.append(summary)
        source_baseline = candidate.change_set.source_revision.revision_id
    return recovered
