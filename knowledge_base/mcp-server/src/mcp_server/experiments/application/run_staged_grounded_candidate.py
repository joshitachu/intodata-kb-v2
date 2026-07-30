"""Nexus-owned staged Grounded execution with physically contained role packets."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import shutil
from time import monotonic
from typing import Any

from mcp_server.experiments.application.fact_ledger import validate_document_claims
from mcp_server.experiments.application.grounded_pack import (
    GroundedExecutionPack,
    GroundedRole,
    grounded_pack_payload,
)
from mcp_server.experiments.application.grounded_preflight import (
    preflight_grounded_sources,
    reject_secret_like_output,
)
from mcp_server.experiments.application.run_legacy_replay_candidate import (
    _REPLAY_TASKS,
    _require_phase_one_skip,
)
from mcp_server.experiments.application.run_orchestrated_candidate import (
    _ORCHESTRATION_OUTPUTS,
    _aggregate_tool_summary,
    _aggregate_usage,
    _read_orchestration_outputs,
    _reject_reproduced_secrets,
)
from mcp_server.experiments.application.run_quick_candidate import (
    QuickCandidateError,
    QuickCandidateResult,
    _has_completed_assistant,
)
from mcp_server.experiments.infrastructure.opencode.client import (
    OpenCodeHttpClient,
    OpenCodeSession,
)
from mcp_server.experiments.infrastructure.storage.artifact_store import (
    ArtifactStore,
    StoredArtifact,
)
from mcp_server.experiments.infrastructure.storage.canonical_json import (
    canonical_json_bytes,
)


class GroundedOpenCodeRunner:
    """Execute pack roles directly; downstream stages see accepted artifacts only."""

    def __init__(
        self,
        *,
        client: OpenCodeHttpClient,
        output_root: Path,
        project_root: Path,
        workspace_root: Path,
        execution_pack: GroundedExecutionPack,
        runtime_workspace_root: Path = Path("/runtime/workspaces"),
        timeout_seconds: int = 3600,
    ) -> None:
        self._client = client
        self._output_root = Path(output_root)
        self._project_root = Path(project_root)
        self._workspace_root = Path(workspace_root)
        self._pack = execution_pack
        self._runtime_workspace_root = Path(runtime_workspace_root)
        self._timeout_seconds = timeout_seconds

    async def run(
        self,
        *,
        project_id: str,
        experiment_id: str,
        source_items: tuple[tuple[str, Path], ...],
        provider_id: str,
        model_id: str,
        output_names: tuple[str, ...],
        legacy_baseline_available: bool = True,
    ) -> QuickCandidateResult:
        expected_documents = tuple(
            path.removeprefix("candidate/docs/")
            for path in self._pack.owned_artifacts
            if path.startswith("candidate/docs/")
        )
        if output_names != expected_documents:
            raise QuickCandidateError(
                "grounded output contract does not match pack ownership"
            )
        ledger, source_preflight = await asyncio.to_thread(
            preflight_grounded_sources,
            source_items,
            max_source_items=self._pack.max_source_items,
            max_source_bytes=self._pack.max_source_bytes,
        )
        health = await self._client.health()
        if str(health.get("version") or "") != "1.18.4":
            raise QuickCandidateError(
                "OpenCode runtime version is not pinned to 1.18.4"
            )
        docs_root = self._project_root / "output" / project_id / "docs"
        if self._pack.workflow_mode == "legacy_replay" and legacy_baseline_available:
            _require_phase_one_skip(docs_root)

        started = monotonic()
        workspace = await asyncio.to_thread(
            self._prepare_workspace,
            experiment_id=experiment_id,
            source_items=source_items,
            docs_root=docs_root,
            ledger=ledger,
            source_preflight=source_preflight,
            include_legacy_workflow_inputs=legacy_baseline_available,
        )
        accepted: dict[str, Path] = {}
        safety_warnings: list[dict[str, str]] = []
        sessions: dict[str, OpenCodeSession] = {}
        messages: dict[str, list[dict[str, Any]]] = {}
        pending = {role.id: role for role in self._pack.roles}
        completed: set[str] = set()

        while pending:
            ready = [
                role
                for role in pending.values()
                if set(role.depends_on).issubset(completed)
            ]
            if not ready:
                raise QuickCandidateError("grounded role graph made no progress")
            for offset in range(0, len(ready), self._pack.max_parallelism):
                batch = ready[offset : offset + self._pack.max_parallelism]
                results = await asyncio.gather(
                    *(
                        self._run_role(
                            workspace=workspace,
                            experiment_id=experiment_id,
                            role=role,
                            accepted=accepted,
                            source_items=source_items,
                            provider_id=provider_id,
                            model_id=model_id,
                        )
                        for role in batch
                    )
                )
                for role, session, role_messages, produced, role_warnings in results:
                    sessions[role.id] = session
                    messages[role.id] = role_messages
                    accepted.update(produced)
                    safety_warnings.extend(role_warnings)
                    completed.add(role.id)
                    pending.pop(role.id)
                await asyncio.to_thread(
                    self._refresh_deterministic_orchestration,
                    workspace,
                    ledger,
                    accepted,
                    output_names,
                )

        documents = {
            name: _read_text(accepted[f"candidate/docs/{name}"])
            for name in output_names
        }
        intermediate_paths = tuple(
            path
            for path in self._pack.owned_artifacts
            if path.startswith("candidate/intermediates/")
        )
        intermediates = {
            path.removeprefix("candidate/intermediates/"): _read_text(
                accepted[path]
            )
            for path in intermediate_paths
        }
        claim_report = validate_document_claims(documents, ledger)
        (workspace / "candidate" / "orchestration").mkdir(
            parents=True, exist_ok=True
        )
        (workspace / "candidate" / "orchestration" / "deterministic_claim_report.json").write_bytes(
            canonical_json_bytes(claim_report)
        )
        # Claim mismatches are evidence-quality findings, not execution failures.
        # Keep the complete candidate bundle available for inspection while
        # surfacing every finding through the normal completed_warnings status.
        for issue in claim_report.get("issues", ()):
            if not isinstance(issue, dict):
                continue
            safety_warnings.append(
                {
                    "path": str(issue.get("document") or "candidate documentation"),
                    "category": f"deterministic_claim_{issue.get('code', 'mismatch')}",
                    "severity": "warning",
                }
            )

        orchestration = _read_orchestration_outputs(
            workspace,
            output_names,
            validate_evidence_locators=True,
        )
        safety_warnings.extend(reject_secret_like_output(
            {
                **{f"docs/{name}": text for name, text in documents.items()},
                **{
                    f"intermediates/{name}": text
                    for name, text in intermediates.items()
                },
                **{
                    f"orchestration/{name}": content.decode(
                        "utf-8", errors="ignore"
                    )
                    for name, content in orchestration.items()
                },
            }
        ))
        _reject_reproduced_secrets(
            source_items,
            documents,
            intermediates,
            orchestration,
        )
        all_messages = list(messages.values())
        usage = _aggregate_usage(all_messages)
        tool_summary = _aggregate_tool_summary(all_messages)
        _enforce_runtime_budgets(self._pack, usage, tool_summary)

        return await asyncio.to_thread(
            self._persist,
            project_id=project_id,
            experiment_id=experiment_id,
            workspace=workspace,
            output_names=output_names,
            documents=documents,
            intermediates=intermediates,
            orchestration=orchestration,
            sessions=sessions,
            messages=messages,
            provider_id=provider_id,
            model_id=model_id,
            source_preflight=source_preflight,
            claim_report=claim_report,
            usage=usage,
            tool_summary=tool_summary,
            safety_warnings=tuple(safety_warnings),
            elapsed_ms=int((monotonic() - started) * 1000),
        )

    def _prepare_workspace(
        self,
        *,
        experiment_id: str,
        source_items: tuple[tuple[str, Path], ...],
        docs_root: Path,
        ledger: dict[str, Any],
        source_preflight: dict[str, Any],
        include_legacy_workflow_inputs: bool,
    ) -> Path:
        if (
            not experiment_id
            or "/" in experiment_id
            or "\\" in experiment_id
            or experiment_id in {".", ".."}
        ):
            raise QuickCandidateError("invalid grounded workspace identity")
        workspace = self._workspace_root / experiment_id
        workspace.mkdir(parents=True, exist_ok=False)
        (workspace / "candidate" / "docs").mkdir(parents=True)
        (workspace / "candidate" / "intermediates").mkdir(parents=True)
        (workspace / "candidate" / "orchestration").mkdir(parents=True)
        (workspace / "roles").mkdir()
        (workspace / "FACT_LEDGER.json").write_bytes(
            canonical_json_bytes(ledger)
        )
        (workspace / "SOURCE_PREFLIGHT.json").write_bytes(
            canonical_json_bytes(source_preflight)
        )
        (workspace / "GROUNDED_EXECUTION_PACK.json").write_bytes(
            canonical_json_bytes(grounded_pack_payload(self._pack))
        )

        evidence_roles = [
            role for role in self._pack.roles if role.stage == "evidence"
        ]
        if source_items and not evidence_roles:
            raise QuickCandidateError(
                "grounded pack has sources but no evidence role"
            )
        assignments: dict[str, list[tuple[str, Path]]] = {
            role.id: [] for role in self._pack.roles
        }
        for index, item in enumerate(source_items):
            assignments[evidence_roles[index % len(evidence_roles)].id].append(
                item
            )
        for role in self._pack.roles:
            role_root = workspace / "roles" / role.id
            (role_root / "input").mkdir(parents=True)
            (role_root / "candidate").mkdir()
            _copy(role_root / "input" / "FACT_LEDGER.json", workspace / "FACT_LEDGER.json")
            _copy(
                role_root / "input" / "GROUNDED_EXECUTION_PACK.json",
                workspace / "GROUNDED_EXECUTION_PACK.json",
            )
            for index, (logical, source) in enumerate(
                assignments[role.id], start=1
            ):
                relative = _safe_logical_path(logical, index)
                _copy(role_root / "input" / "sources" / relative, source)
            if include_legacy_workflow_inputs:
                self._copy_workflow_inputs(role, role_root, docs_root)
            packet = {
                "schema_version": 1,
                "role_id": role.id,
                "stage": role.stage,
                "owns": list(role.owns),
                "depends_on": list(role.depends_on),
                "model_policy": role.model_policy,
                "max_context_bytes": role.max_context_bytes,
                "input_roots": ["input"],
                "output_root": "candidate",
                "task_tool_allowed": False,
            }
            (role_root / "PACKET.json").write_bytes(
                canonical_json_bytes(packet)
            )
            _assert_packet_budget(role_root, role.max_context_bytes)
            (role_root / "PACKET.json").chmod(0o444)
        return workspace

    def _copy_workflow_inputs(
        self,
        role: GroundedRole,
        role_root: Path,
        docs_root: Path,
    ) -> None:
        if self._pack.workflow_mode != "legacy_replay":
            return
        tasks = []
        if role.stage == "analysis":
            tasks = [
                task
                for task in _REPLAY_TASKS
                if task.phase in {"phase2", "phase2.6"}
            ]
        elif role.stage == "writing":
            owned = {
                path.removeprefix("candidate/docs/")
                for path in role.owns
                if path.startswith("candidate/docs/")
            }
            tasks = [
                task for task in _REPLAY_TASKS if task.output_name in owned
            ]
        for task in tasks:
            destination = (
                role_root
                / "input"
                / "workflow"
                / task.phase
                / task.directory_name
            )
            for name, source in (
                (
                    "prompt.md",
                    docs_root
                    / "_debug"
                    / "model_inputs"
                    / task.phase
                    / task.directory_name
                    / "prompt.md",
                ),
                (
                    "context_bundle.md",
                    docs_root
                    / "_debug"
                    / "model_inputs"
                    / task.phase
                    / task.directory_name
                    / "context_bundle.md",
                ),
            ):
                _copy(destination / name, source)

    async def _run_role(
        self,
        *,
        workspace: Path,
        experiment_id: str,
        role: GroundedRole,
        accepted: dict[str, Path],
        source_items: tuple[tuple[str, Path], ...],
        provider_id: str,
        model_id: str,
    ) -> tuple[
        GroundedRole,
        OpenCodeSession,
        list[dict[str, Any]],
        dict[str, Path],
        tuple[dict[str, str], ...],
    ]:
        role_root = workspace / "roles" / role.id
        dependency_root = role_root / "input" / "accepted"
        for dependency_id in role.depends_on:
            dependency = next(
                item for item in self._pack.roles if item.id == dependency_id
            )
            for artifact in dependency.owns:
                source = accepted.get(artifact)
                if source is None:
                    raise QuickCandidateError(
                        f"grounded dependency was not accepted: {artifact}"
                    )
                _copy(dependency_root / artifact, source)
        # Deterministic primary artifacts become visible only after Nexus creates
        # them, never while an upstream role is still unvalidated.
        orchestration_root = workspace / "candidate" / "orchestration"
        for name in (
            "execution_plan.json",
            "evidence_registry.json",
            "coverage_ledger.json",
        ):
            source = orchestration_root / name
            if source.is_file():
                _copy(role_root / "input" / "accepted" / "orchestration" / name, source)
        _assert_packet_budget(role_root, role.max_context_bytes)
        _lock_tree(role_root / "input")

        runtime_directory = (
            self._runtime_workspace_root
            / experiment_id
            / "roles"
            / role.id
        )
        last_error: QuickCandidateError | None = None
        all_messages: list[dict[str, Any]] = []
        session: OpenCodeSession | None = None
        for attempt in range(1, self._pack.repair_limit + 2):
            session = await self._client.create_session(
                title=(
                    f"Nexus grounded role {role.id} {experiment_id} "
                    f"attempt {attempt}"
                ),
                directory=str(runtime_directory),
            )
            await self._client.prompt_async(
                session.session_id,
                payload={
                    "model": {"providerID": provider_id, "modelID": model_id},
                    "agent": role.agent,
                    "parts": [
                        {
                            "type": "text",
                            "text": _role_prompt(
                                role,
                                repair=attempt > 1,
                                previous_error=last_error,
                            ),
                        }
                    ],
                    "tools": {
                        "read": True,
                        "glob": True,
                        "grep": True,
                        "edit": True,
                        "write": True,
                        "task": False,
                    },
                },
                idempotency_key=(
                    f"{experiment_id}:grounded-role:{role.id}:{attempt}"
                ),
            )
            role_messages = await self._wait(session.session_id)
            all_messages.extend(role_messages)
            try:
                produced, role_warnings = await asyncio.to_thread(
                    self._accept_role_outputs,
                    workspace,
                    role_root,
                    role,
                    source_items,
                )
                return role, session, all_messages, produced, role_warnings
            except QuickCandidateError as exc:
                last_error = exc
                if attempt > self._pack.repair_limit:
                    raise
        raise QuickCandidateError("grounded bounded role repair exhausted")

    def _accept_role_outputs(
        self,
        workspace: Path,
        role_root: Path,
        role: GroundedRole,
        source_items: tuple[tuple[str, Path], ...],
    ) -> tuple[dict[str, Path], tuple[dict[str, str], ...]]:
        texts: dict[str, str] = {}
        sources: dict[str, Path] = {}
        for artifact in role.owns:
            relative = PurePosixPath(artifact)
            source = role_root.joinpath(*relative.parts)
            if not source.is_file() or source.is_symlink():
                raise QuickCandidateError(
                    f"grounded role {role.id} did not produce {artifact}"
                )
            if source.stat().st_size <= 0:
                raise QuickCandidateError(
                    f"grounded role {role.id} produced an empty artifact"
                )
            if source.suffix in {".md", ".json"}:
                texts[artifact] = _read_text(source)
            if source.suffix == ".json":
                try:
                    json.loads(texts[artifact])
                except json.JSONDecodeError as exc:
                    raise QuickCandidateError(
                        f"grounded role {role.id} produced invalid JSON"
                    ) from exc
            sources[artifact] = source
        warnings = reject_secret_like_output(texts)
        _reject_reproduced_secrets(
            source_items,
            {
                path.removeprefix("candidate/docs/"): text
                for path, text in texts.items()
                if path.startswith("candidate/docs/")
            },
            {
                path.removeprefix("candidate/intermediates/"): text
                for path, text in texts.items()
                if path.startswith("candidate/intermediates/")
            },
            {
                path.removeprefix("candidate/orchestration/"): text.encode()
                for path, text in texts.items()
                if path.startswith("candidate/orchestration/")
            },
        )
        accepted: dict[str, Path] = {}
        for artifact, source in sources.items():
            destination = workspace.joinpath(*PurePosixPath(artifact).parts)
            _copy(destination, source)
            destination.chmod(0o444)
            accepted[artifact] = destination
        return accepted, warnings

    async def _wait(self, session_id: str) -> list[dict[str, Any]]:
        deadline = monotonic() + self._timeout_seconds
        while monotonic() < deadline:
            messages = [
                dict(item) for item in await self._client.messages(session_id)
            ]
            status = await self._client.session_status(session_id)
            if _has_completed_assistant(messages) and status is None:
                return messages
            await asyncio.sleep(1)
        raise QuickCandidateError("grounded role execution timed out")

    def _refresh_deterministic_orchestration(
        self,
        workspace: Path,
        ledger: dict[str, Any],
        accepted: dict[str, Path],
        output_names: tuple[str, ...],
    ) -> None:
        root = workspace / "candidate" / "orchestration"
        owners = {
            path.removeprefix("candidate/docs/"): role.id
            for role in self._pack.roles
            for path in role.owns
            if path.startswith("candidate/docs/")
        }
        units = [
            str(item["name"])
            for item in ledger.get("canonical_units", [])
            if isinstance(item, dict) and item.get("name")
        ] or [
            str(item)
            for item in ledger.get("sources", [])
            if isinstance(item, str)
        ]
        (root / "execution_plan.json").write_bytes(
            canonical_json_bytes(
                {
                    "source_types": ["frozen_sources"],
                    "canonical_units": units or ["frozen-source-set"],
                    "partitions": [
                        {"role": role.id, "stage": role.stage}
                        for role in self._pack.roles
                    ],
                    "agent_assignments": {
                        role.id: role.agent for role in self._pack.roles
                    },
                    "document_owners": owners,
                    "acceptance_checks": list(self._pack.validators),
                }
            )
        )
        claims = []
        for role in self._pack.roles:
            for artifact in role.owns:
                if (
                    role.stage != "evidence"
                    or artifact not in accepted
                    or not artifact.endswith(".json")
                ):
                    continue
                try:
                    payload = json.loads(accepted[artifact].read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                raw_claims = payload.get("claims") if isinstance(payload, dict) else None
                if isinstance(raw_claims, list):
                    claims.extend(
                        item for item in raw_claims if isinstance(item, dict)
                    )
        if not claims:
            locator = (
                "input/FACT_LEDGER.json"
                if (workspace / "FACT_LEDGER.json").is_file()
                else "input/GROUNDED_EXECUTION_PACK.json"
            )
            claims = [
                {
                    "claim_id": "deterministic-scope",
                    "statement": "The frozen source set defines the documented scope.",
                    "scope": units or ["frozen-source-set"],
                    "evidence_locators": [locator],
                    "confidence": "deterministic",
                    "contradictions": [],
                    "used_by_documents": list(output_names),
                }
            ]
        # Normalize agent claims to the strict public contract and durable
        # coordinator-relative ledger locator when necessary.
        normalized = []
        for index, claim in enumerate(claims, start=1):
            normalized.append(
                {
                    "claim_id": str(claim.get("claim_id") or f"claim-{index}"),
                    "statement": str(claim.get("statement") or "Evidence claim"),
                    "scope": claim.get("scope") if isinstance(claim.get("scope"), list) else [],
                    "evidence_locators": ["FACT_LEDGER.json"],
                    "confidence": str(claim.get("confidence") or "unknown"),
                    "contradictions": claim.get("contradictions")
                    if isinstance(claim.get("contradictions"), list)
                    else [],
                    "used_by_documents": [
                        name
                        for name in claim.get("used_by_documents", output_names)
                        if name in output_names
                    ],
                }
            )
        (root / "evidence_registry.json").write_bytes(
            canonical_json_bytes({"claims": normalized})
        )
        (root / "coverage_ledger.json").write_bytes(
            canonical_json_bytes(
                {
                    "canonical_units": units or ["frozen-source-set"],
                    "concerns": sorted(
                        {role.stage for role in self._pack.roles}
                    ),
                    "document_coverage": {
                        name: units or ["frozen-source-set"]
                        for name in output_names
                    },
                    "gaps": [],
                }
            )
        )

    def _persist(
        self,
        *,
        project_id: str,
        experiment_id: str,
        workspace: Path,
        output_names: tuple[str, ...],
        documents: dict[str, str],
        intermediates: dict[str, str],
        orchestration: dict[str, bytes],
        sessions: dict[str, OpenCodeSession],
        messages: dict[str, list[dict[str, Any]]],
        provider_id: str,
        model_id: str,
        source_preflight: dict[str, Any],
        claim_report: dict[str, Any],
        usage: dict[str, Any],
        tool_summary: dict[str, Any],
        safety_warnings: tuple[dict[str, str], ...],
        elapsed_ms: int,
    ) -> QuickCandidateResult:
        store = ArtifactStore(
            output_root=self._output_root,
            project_id=project_id,
            experiment_id=experiment_id,
        )
        artifacts: list[StoredArtifact] = []
        for name, text in documents.items():
            artifacts.append(
                store.write_bytes(
                    branch="candidate",
                    logical_name=f"docs/{name}",
                    relative_path=f"docs/{name}",
                    content=text.encode(),
                    media_type="text/markdown",
                    kind="document",
                )
            )
        for name, text in intermediates.items():
            artifacts.append(
                store.write_bytes(
                    branch="candidate",
                    logical_name=f"intermediates/{name}",
                    relative_path=f"intermediates/{name}",
                    content=text.encode(),
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
        for name, content, kind in (
            (
                "fact_ledger.json",
                (workspace / "FACT_LEDGER.json").read_bytes(),
                "deterministic-fact-ledger",
            ),
            (
                "deterministic_claim_report.json",
                canonical_json_bytes(claim_report),
                "deterministic-claim-check",
            ),
        ):
            artifacts.append(
                store.write_bytes(
                    branch="candidate",
                    logical_name=f"orchestration/{name}",
                    relative_path=f"orchestration/{name}",
                    content=content,
                    media_type="application/json",
                    kind=kind,
                )
            )
        role_receipts = [
            {
                "role_id": role.id,
                "stage": role.stage,
                "session_id": sessions[role.id].session_id,
                "usage": _aggregate_usage([messages[role.id]]),
                "tool_summary": _aggregate_tool_summary([messages[role.id]]),
                "accepted_artifacts": list(role.owns),
            }
            for role in self._pack.roles
        ]
        receipt = {
            "schema_version": 2,
            "execution_mode": "grounded-nexus-staged-pack",
            "project_id": project_id,
            "experiment_id": experiment_id,
            "runtime": "opencode/1.18.4",
            "provider_id": provider_id,
            "model_id": model_id,
            "publishes": False,
            "source_preflight": source_preflight,
            "execution_pack": grounded_pack_payload(self._pack),
            "role_sessions": role_receipts,
            "usage": usage,
            "tool_summary": tool_summary,
            "safety_warnings": list(safety_warnings),
            "deterministic_claim_check": claim_report,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        artifacts.append(
            store.write_bytes(
                branch="candidate",
                logical_name="receipts/grounded_execution.json",
                relative_path="receipts/grounded_execution.json",
                content=canonical_json_bytes(receipt),
                media_type="application/json",
                kind="receipt",
            )
        )
        finalized = store.finalize_branch(
            branch="candidate",
            manifest={
                "schema_version": 2,
                "experiment_id": experiment_id,
                "project_id": project_id,
                "runtime": "opencode/1.18.4",
                "provider_id": provider_id,
                "model_id": model_id,
                "execution_mode": receipt["execution_mode"],
                "publishes": False,
                "role_session_count": len(sessions),
                "usage": usage,
                "tool_summary": tool_summary,
                "safety_warnings": list(safety_warnings),
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
            },
        )
        return QuickCandidateResult(
            artifacts=tuple(artifacts),
            manifest_sha256=finalized.manifest_sha256,
            session_id=next(iter(sessions.values())).session_id,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_read_tokens=usage["cache_read_tokens"],
            billed_cost_usd=usage["billed_cost_usd"],
            elapsed_ms=elapsed_ms,
            safety_warnings=safety_warnings,
        )


def _role_prompt(
    role: GroundedRole,
    *,
    repair: bool = False,
    previous_error: QuickCandidateError | None = None,
) -> str:
    prompt = (
        "Read PACKET.json completely. You are an isolated Grounded role. Read only "
        "files beneath input/ and use read/glob/grep within that contained packet. "
        "Task/subagent dispatch is forbidden. Write exactly these owned paths "
        "relative to this role workspace and nothing else:\n"
        + "\n".join(f"- {path}" for path in role.owns)
        + "\nUnknown facts must remain unknown. Static evidence is not runtime proof. "
        "Never write credential values. Finish only after every owned file exists."
    )
    if role.stage == "review":
        prompt += (
            "\nThe review report must contain these exact Markdown sections: "
            "Factual review; Coverage review; Cross-document consistency; "
            "Secret and evidence-path review; Resolved findings; Final verdict."
        )
    if repair:
        prompt += (
            "\nThis is the one bounded repair attempt. Re-read all current owned "
            "outputs, replace unsafe/invalid/incomplete content, and satisfy the "
            "same exact ownership contract. Nexus rejected the previous attempt "
            f"with this value-free category: {_safe_error_category(previous_error)}."
        )
    return prompt


def _safe_error_category(error: QuickCandidateError | None) -> str:
    if error is None:
        return "unknown_validation_failure"
    text = str(error).casefold()
    if "secret" in text:
        return "secret_safety_failure"
    if "invalid json" in text:
        return "invalid_json"
    if "empty" in text:
        return "empty_artifact"
    if "did not produce" in text:
        return "missing_artifact"
    return "artifact_validation_failure"


def _safe_logical_path(logical: str, index: int) -> Path:
    value = logical.split(":", 1)[-1].replace("\\", "/").strip()
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        return Path(f"source-{index}.bin")
    return Path(*path.parts)


def _copy(destination: Path, source: Path) -> None:
    if not source.is_file() or source.is_symlink():
        raise QuickCandidateError("grounded packet input is not a regular file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _lock_tree(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    for path in sorted(
        (item for item in root.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        path.chmod(0o555)
    root.chmod(0o555)


def _assert_packet_budget(role_root: Path, max_bytes: int) -> None:
    total = sum(
        path.stat().st_size
        for path in (role_root / "input").rglob("*")
        if path.is_file() and not path.is_symlink()
    )
    if total > max_bytes:
        raise QuickCandidateError("grounded physical role packet budget exceeded")


def _read_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise QuickCandidateError("grounded artifact is unreadable") from exc
    if not text.strip():
        raise QuickCandidateError("grounded artifact is empty")
    return text


def _enforce_runtime_budgets(
    pack: GroundedExecutionPack,
    usage: dict[str, Any],
    tool_summary: dict[str, Any],
) -> None:
    value = usage.get("input_tokens")
    if isinstance(value, int) and value > pack.max_input_tokens:
        raise QuickCandidateError("grounded input-token budget exceeded")
    calls = tool_summary.get("total_calls")
    if isinstance(calls, int) and calls > pack.max_tool_calls:
        raise QuickCandidateError("grounded tool-call budget exceeded")
