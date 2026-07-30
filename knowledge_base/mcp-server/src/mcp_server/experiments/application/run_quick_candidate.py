"""One-shot OpenCode candidate bundle generation for the normal Generate UI."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, DecimalException
from pathlib import Path
from pathlib import PurePosixPath
import shutil
from time import monotonic
from typing import Any

from mcp_server.experiments.infrastructure.opencode.client import OpenCodeHttpClient
from mcp_server.experiments.infrastructure.storage.artifact_store import (
    ArtifactStore,
    StoredArtifact,
)


class QuickCandidateError(RuntimeError):
    """The bounded candidate call failed or returned an invalid bundle."""


@dataclass(frozen=True, slots=True)
class QuickCandidateResult:
    artifacts: tuple[StoredArtifact, ...]
    manifest_sha256: str
    session_id: str
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    billed_cost_usd: str | None
    elapsed_ms: int
    safety_warnings: tuple[dict[str, str], ...] = ()


class QuickOpenCodeCandidateRunner:
    """Let OpenCode investigate a frozen workspace and produce a candidate bundle."""

    def __init__(
        self,
        *,
        client: OpenCodeHttpClient,
        output_root: Path,
        workspace_root: Path,
        runtime_workspace_root: Path = Path("/runtime/workspaces"),
        timeout_seconds: int = 3600,
    ) -> None:
        self._client = client
        self._output_root = Path(output_root)
        self._workspace_root = Path(workspace_root)
        self._runtime_workspace_root = Path(runtime_workspace_root)
        self._timeout_seconds = timeout_seconds

    async def run(
        self,
        *,
        project_id: str,
        experiment_id: str,
        source_items: tuple[tuple[str, Path], ...],
        output_names: tuple[str, ...],
        output_requirements: tuple[tuple[str, tuple[str, ...]], ...],
        recipe_guidance: str,
        provider_id: str,
        model_id: str,
    ) -> QuickCandidateResult:
        if not source_items:
            raise QuickCandidateError("OpenCode candidate requires frozen source content")
        health = await self._client.health()
        if str(health.get("version") or "") != "1.18.4":
            raise QuickCandidateError("OpenCode runtime version is not pinned to 1.18.4")
        workspace = await asyncio.to_thread(
            _prepare_workspace,
            self._workspace_root,
            experiment_id,
            source_items,
            output_names,
            output_requirements,
            recipe_guidance,
        )
        started = monotonic()
        session = await self._client.create_session(
            title=f"Nexus agentic candidate {project_id} {experiment_id}",
            directory=str(self._runtime_workspace_root / experiment_id),
        )
        await self._client.prompt_async(
            session.session_id,
            payload={
                "model": {"providerID": provider_id, "modelID": model_id},
                "parts": [
                    {
                        "type": "text",
                        "text": _prompt(output_names),
                    }
                ],
                "tools": {
                    "read": True,
                    "glob": True,
                    "grep": True,
                    "edit": True,
                    "write": True,
                },
            },
            idempotency_key=f"{experiment_id}:candidate-bundle",
        )
        messages = await self._wait_for_messages(session.session_id)
        _latest_assistant(messages)
        bundle = await asyncio.to_thread(_read_bundle, workspace, output_names)
        store = ArtifactStore(
            output_root=self._output_root,
            project_id=project_id,
            experiment_id=experiment_id,
        )
        artifacts = tuple(
            store.write_bytes(
                branch="candidate",
                logical_name=f"docs/{name}",
                relative_path=f"docs/{name}",
                content=bundle[name].encode("utf-8"),
                media_type="text/markdown",
                kind="document",
            )
            for name in output_names
        )
        usage = _usage(messages)
        finalized = store.finalize_branch(
            branch="candidate",
            manifest={
                "schema_version": 1,
                "experiment_id": experiment_id,
                "project_id": project_id,
                "runtime": "opencode/1.18.4",
                "provider_id": provider_id,
                "model_id": model_id,
                "publishes": False,
                "artifacts": [
                    {
                        "logical_name": item.logical_name,
                        "relative_path": item.relative_path,
                        "sha256": item.sha256,
                        "size_bytes": item.size_bytes,
                    }
                    for item in artifacts
                ],
                "usage": usage,
            },
        )
        return QuickCandidateResult(
            artifacts=artifacts,
            manifest_sha256=finalized.manifest_sha256,
            session_id=session.session_id,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_read_tokens=usage["cache_read_tokens"],
            billed_cost_usd=usage["billed_cost_usd"],
            elapsed_ms=int((monotonic() - started) * 1000),
        )

    async def _wait_for_messages(self, session_id: str) -> list[dict[str, Any]]:
        deadline = monotonic() + self._timeout_seconds
        while monotonic() < deadline:
            messages = [dict(item) for item in await self._client.messages(session_id)]
            if _has_completed_assistant(messages):
                return messages
            await asyncio.sleep(1)
        raise QuickCandidateError("OpenCode candidate generation timed out")


def _prompt(output_names: tuple[str, ...]) -> str:
    names = "\n".join(f"- candidate/docs/{name}" for name in output_names)
    return (
        "Act as the documentation harness for this isolated Nexus experiment. "
        "Read AGENTS.md and SOURCES.md first. Investigate the approved files under "
        "sources/ using read, glob, and grep. Decide your own evidence-gathering and "
        "writing sequence; do not assume a particular platform or business domain. "
        "Create complete, evidence-grounded Markdown documentation at exactly these "
        "paths:\n"
        f"{names}\n\n"
        "Use write/edit to create the files. Re-open and review every document before "
        "finishing. Do not merely describe what you would do and do not return the "
        "documents in chat. Unknown facts must be marked unknown; never invent runtime "
        "behavior. Finish only after all required files exist."
    )


def _latest_assistant(messages: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [
        message
        for message in messages
        if isinstance(message.get("info"), dict)
        and message["info"].get("role") == "assistant"
    ]
    if not candidates:
        raise QuickCandidateError("OpenCode returned no assistant message")
    return candidates[-1]


def _has_completed_assistant(messages: list[dict[str, Any]]) -> bool:
    """Return true only for the terminal assistant turn.

    OpenCode emits a completed assistant message with ``reason=tool-calls`` after
    every tool-use step.  Those messages are checkpoints in the same agent run,
    not proof that the session has finished.
    """

    try:
        message = _latest_assistant(messages)
    except QuickCandidateError:
        return False
    info = message.get("info")
    time_info = info.get("time") if isinstance(info, dict) else None
    if not isinstance(time_info, dict) or not time_info.get("completed"):
        return False
    finishes = [
        part
        for part in message.get("parts", [])
        if isinstance(part, dict) and part.get("type") == "step-finish"
    ]
    if not finishes:
        return True
    reason = str(finishes[-1].get("reason") or "").strip().lower().replace("_", "-")
    return reason not in {"tool", "tool-call", "tool-calls"}


def _prepare_workspace(
    workspace_root: Path,
    experiment_id: str,
    source_items: tuple[tuple[str, Path], ...],
    output_names: tuple[str, ...],
    output_requirements: tuple[tuple[str, tuple[str, ...]], ...],
    recipe_guidance: str,
) -> Path:
    if "/" in experiment_id or "\\" in experiment_id or experiment_id in {"", ".", ".."}:
        raise QuickCandidateError("invalid experiment workspace identity")
    workspace = workspace_root / experiment_id
    workspace.mkdir(parents=True, exist_ok=False)
    workspace.chmod(0o777)
    source_root = workspace / "sources"
    output_root = workspace / "candidate" / "docs"
    source_root.mkdir(parents=True)
    output_root.mkdir(parents=True)
    (workspace / "candidate").chmod(0o777)
    output_root.chmod(0o777)
    inventory: list[str] = []
    for index, (logical_identity, source_path) in enumerate(source_items, start=1):
        relative = _safe_source_relative_path(logical_identity, index)
        destination = source_root.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, destination)
        destination.chmod(0o444)
        inventory.append(f"- `sources/{relative.as_posix()}`")
    for directory in sorted(
        (path for path in source_root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        directory.chmod(0o555)
    source_root.chmod(0o555)
    requirements = dict(output_requirements)
    required = "\n".join(
        (
            f"- `candidate/docs/{name}`"
            + (
                f" — required sections: {', '.join(requirements[name])}"
                if requirements.get(name)
                else ""
            )
        )
        for name in output_names
    )
    (workspace / "AGENTS.md").write_text(
        "# Nexus documentation agent\n\n"
        "You are operating inside a candidate-only experiment workspace. Read and "
        "search only `sources/`. Write only beneath `candidate/`. The source files "
        "are immutable approved scanner evidence. Build a coherent understanding "
        "before writing, cite source paths where useful, distinguish evidence from "
        "inference, disclose unknowns, and review the complete bundle for internal "
        "consistency. Never modify sources and never claim publication.\n\n"
        "## Required outputs\n\n"
        f"{required}\n\n"
        "## Certified recipe guidance\n\n"
        f"{recipe_guidance.strip() or 'Use approved evidence only.'}\n",
        encoding="utf-8",
    )
    (workspace / "AGENTS.md").chmod(0o444)
    (workspace / "SOURCES.md").write_text(
        "# Approved frozen sources\n\n" + "\n".join(inventory) + "\n",
        encoding="utf-8",
    )
    (workspace / "SOURCES.md").chmod(0o444)
    return workspace


def _safe_source_relative_path(logical_identity: str, index: int) -> PurePosixPath:
    value = logical_identity.split(":", 1)[-1].strip().replace("\\", "/")
    relative = PurePosixPath(value)
    if (
        not value
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        return PurePosixPath(f"source_{index:03d}.json")
    return relative


def _read_bundle(workspace: Path, output_names: tuple[str, ...]) -> dict[str, str]:
    docs = workspace / "candidate" / "docs"
    actual = {path.name for path in docs.iterdir() if path.is_file() and not path.is_symlink()}
    if actual != set(output_names):
        missing = sorted(set(output_names) - actual)
        unexpected = sorted(actual - set(output_names))
        raise QuickCandidateError(
            f"OpenCode candidate files mismatch; missing={missing}, unexpected={unexpected}"
        )
    bundle: dict[str, str] = {}
    for name in output_names:
        text = (docs / name).read_text(encoding="utf-8").strip()
        if not text:
            raise QuickCandidateError(f"OpenCode candidate document is empty: {name}")
        bundle[name] = text + "\n"
    return bundle


def _usage(messages: list[dict[str, Any]]) -> dict[str, Any]:
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cost = Decimal("0")
    saw_input = False
    saw_output = False
    saw_cache = False
    saw_cost = False
    for message in messages:
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        if info.get("role") != "assistant":
            continue
        parts = [
            part
            for part in message.get("parts", [])
            if isinstance(part, dict) and part.get("type") == "step-finish"
        ]
        finish = parts[-1] if parts else {}
        token_source = (
            finish.get("tokens")
            if isinstance(finish.get("tokens"), dict)
            else info.get("tokens")
        )
        tokens = token_source if isinstance(token_source, dict) else {}
        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        value = _int_or_none(tokens.get("input"))
        if value is not None:
            input_tokens += value
            saw_input = True
        value = _int_or_none(tokens.get("output"))
        if value is not None:
            output_tokens += value
            saw_output = True
        value = _int_or_none(cache.get("read"))
        if value is not None:
            cache_read_tokens += value
            saw_cache = True
        raw_cost = finish.get("cost", info.get("cost"))
        if isinstance(raw_cost, (int, float, str)) and not isinstance(raw_cost, bool):
            try:
                cost += Decimal(str(raw_cost))
                saw_cost = True
            except DecimalException:
                pass
    return {
        "input_tokens": input_tokens if saw_input else None,
        "output_tokens": output_tokens if saw_output else None,
        "cache_read_tokens": cache_read_tokens if saw_cache else None,
        "billed_cost_usd": str(cost) if saw_cost else None,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def _int_or_none(value: object) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None
