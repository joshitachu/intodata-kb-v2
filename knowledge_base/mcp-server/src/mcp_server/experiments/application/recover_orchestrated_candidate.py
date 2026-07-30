"""Finalize an existing OpenCode orchestration workspace without provider traffic."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp_server.experiments.application.run_agentic_legacy_candidate import (
    _INTERMEDIATE_OUTPUTS,
    _read_candidate_outputs,
)
from mcp_server.experiments.application.run_orchestrated_candidate import (
    _MINIMUM_CHILD_SESSIONS,
    _ORCHESTRATION_OUTPUTS,
    _aggregate_tool_summary,
    _aggregate_usage,
    _child_receipt,
    _read_orchestration_outputs,
    _reject_reproduced_secrets,
)
from mcp_server.experiments.application.run_quick_candidate import (
    QuickCandidateError,
    QuickCandidateResult,
    _latest_assistant,
)
from mcp_server.experiments.infrastructure.opencode.client import OpenCodeHttpClient
from mcp_server.experiments.infrastructure.storage.artifact_store import (
    ArtifactStore,
    StoredArtifact,
)
from mcp_server.experiments.infrastructure.storage.canonical_json import (
    canonical_json_bytes,
)


class OrchestratedCandidateRecovery:
    """Validate and index a failed run's complete workspace; never submit a prompt."""

    def __init__(
        self,
        *,
        client: OpenCodeHttpClient,
        output_root: Path,
        workspace_root: Path,
        runtime_workspace_root: Path = Path("/runtime/workspaces"),
    ) -> None:
        self._client = client
        self._output_root = Path(output_root)
        self._workspace_root = Path(workspace_root)
        self._runtime_workspace_root = Path(runtime_workspace_root)

    async def recover(
        self,
        *,
        project_id: str,
        experiment_id: str,
        session_id: str,
        source_items: tuple[tuple[str, Path], ...],
        provider_id: str,
        model_id: str,
        output_names: tuple[str, ...],
    ) -> QuickCandidateResult:
        """Recover only a proven idle session bound to this exact experiment."""

        if not source_items:
            raise QuickCandidateError("recovery requires frozen scanner sources")
        health = await self._client.health()
        if str(health.get("version") or "") != "1.18.4":
            raise QuickCandidateError("OpenCode runtime version is not pinned to 1.18.4")

        session = await self._client.session(session_id)
        raw = dict(session.raw or {})
        expected_directory = str(self._runtime_workspace_root / experiment_id)
        expected_title = (
            f"Nexus orchestrated documentation {project_id} {experiment_id}"
        )
        if raw.get("directory") != expected_directory:
            raise QuickCandidateError(
                "OpenCode recovery session is bound to a different workspace"
            )
        if raw.get("title") != expected_title:
            raise QuickCandidateError(
                "OpenCode recovery session title does not match the experiment"
            )
        if str(raw.get("version") or "") != "1.18.4":
            raise QuickCandidateError(
                "OpenCode recovery session used an unsupported runtime version"
            )
        session_model = raw.get("model")
        if not isinstance(session_model, dict):
            raise QuickCandidateError("OpenCode recovery session has no model identity")
        if session_model.get("providerID") != provider_id:
            raise QuickCandidateError(
                "OpenCode recovery session provider does not match the experiment"
            )
        if session_model.get("id") != model_id:
            raise QuickCandidateError(
                "OpenCode recovery session model does not match the experiment"
            )
        if await self._client.session_status(session_id) is not None:
            raise QuickCandidateError("OpenCode recovery session is still active")

        workspace = self._workspace_root / experiment_id
        if not workspace.is_dir() or workspace.is_symlink():
            raise QuickCandidateError("OpenCode recovery workspace is unavailable")
        parent_messages = [
            dict(item) for item in await self._client.messages(session_id)
        ]
        _latest_assistant(parent_messages)
        children = await self._client.children(session_id)
        if len(children) < _MINIMUM_CHILD_SESSIONS:
            raise QuickCandidateError(
                "OpenCode recovery session has too few specialist child sessions"
            )
        child_messages = {
            child.session_id: [
                dict(item) for item in await self._client.messages(child.session_id)
            ]
            for child in children
        }
        for messages in child_messages.values():
            _latest_assistant(messages)

        documents, intermediates = _read_candidate_outputs(workspace, output_names)
        orchestration = _read_orchestration_outputs(workspace, output_names)
        _reject_reproduced_secrets(
            source_items,
            documents,
            intermediates,
            orchestration,
        )
        all_messages = [parent_messages, *child_messages.values()]
        usage = _aggregate_usage(all_messages)
        tool_summary = _aggregate_tool_summary(all_messages)
        child_receipts = [
            _child_receipt(child, child_messages[child.session_id])
            for child in children
        ]

        store = ArtifactStore(
            output_root=self._output_root,
            project_id=project_id,
            experiment_id=experiment_id,
        )
        artifacts: list[StoredArtifact] = []
        for name in output_names:
            artifacts.append(
                store.write_bytes(
                    branch="candidate",
                    logical_name=f"docs/{name}",
                    relative_path=f"docs/{name}",
                    content=documents[name].encode("utf-8"),
                    media_type="text/markdown",
                    kind="document",
                )
            )
        for relative_name in _INTERMEDIATE_OUTPUTS:
            artifacts.append(
                store.write_bytes(
                    branch="candidate",
                    logical_name=f"intermediates/{relative_name}",
                    relative_path=f"intermediates/{relative_name}",
                    content=intermediates[relative_name].encode("utf-8"),
                    media_type="text/markdown",
                    kind="analysis",
                )
            )
        for name, media_type, kind in _ORCHESTRATION_OUTPUTS:
            artifacts.append(
                store.write_bytes(
                    branch="candidate",
                    logical_name=f"orchestration/{name}",
                    relative_path=f"orchestration/{name}",
                    content=orchestration[name],
                    media_type=media_type,
                    kind=kind,
                )
            )

        elapsed_ms = _session_elapsed_ms(raw)
        receipt = {
            "schema_version": 1,
            "execution_mode": "recovered-multi-session-orchestrated-legacy-equivalence",
            "project_id": project_id,
            "experiment_id": experiment_id,
            "runtime": "opencode/1.18.4",
            "provider_id": provider_id,
            "model_id": model_id,
            "phase1": {
                "status": "skipped",
                "reason": "Direct path: Phase 1 skipped for small project.",
            },
            "recovery": {
                "provider_calls_performed": 0,
                "source": "existing-completed-opencode-session",
            },
            "parent_session_id": session_id,
            "child_sessions": child_receipts,
            "tool_summary": tool_summary,
            "usage": usage,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        artifacts.append(
            store.write_bytes(
                branch="candidate",
                logical_name="receipts/recovered_orchestrated_execution.json",
                relative_path="receipts/recovered_orchestrated_execution.json",
                content=canonical_json_bytes(receipt),
                media_type="application/json",
                kind="receipt",
            )
        )
        finalized = store.finalize_branch(
            branch="candidate",
            manifest={
                "schema_version": 1,
                "experiment_id": experiment_id,
                "project_id": project_id,
                "runtime": "opencode/1.18.4",
                "provider_id": provider_id,
                "model_id": model_id,
                "execution_mode": receipt["execution_mode"],
                "publishes": False,
                "recovered_without_provider_call": True,
                "parent_session_id": session_id,
                "child_session_count": len(children),
                "artifacts": [
                    {
                        "logical_name": item.logical_name,
                        "relative_path": item.relative_path,
                        "sha256": item.sha256,
                        "size_bytes": item.size_bytes,
                        "kind": item.kind,
                    }
                    for item in artifacts
                ],
                "usage": usage,
                "tool_summary": tool_summary,
            },
        )
        return QuickCandidateResult(
            artifacts=tuple(artifacts),
            manifest_sha256=finalized.manifest_sha256,
            session_id=session_id,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_read_tokens=usage["cache_read_tokens"],
            billed_cost_usd=usage["billed_cost_usd"],
            elapsed_ms=elapsed_ms,
        )


def _session_elapsed_ms(raw: dict[str, Any]) -> int:
    time_data = raw.get("time")
    if not isinstance(time_data, dict):
        raise QuickCandidateError("OpenCode recovery session has no timing metadata")
    created = time_data.get("created")
    updated = time_data.get("updated")
    if (
        not isinstance(created, int)
        or not isinstance(updated, int)
        or updated < created
    ):
        raise QuickCandidateError("OpenCode recovery session timing is invalid")
    return updated - created
