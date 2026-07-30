"""Replay frozen legacy model inputs through OpenCode for a controlled A/B test."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path, PurePosixPath
from time import monotonic
from typing import Any

from mcp_server.experiments.application.run_quick_candidate import (
    QuickCandidateError,
    QuickCandidateResult,
    _has_completed_assistant,
    _latest_assistant,
    _usage,
)
from mcp_server.experiments.infrastructure.opencode.client import OpenCodeHttpClient
from mcp_server.experiments.infrastructure.storage.artifact_store import (
    ArtifactStore,
    StoredArtifact,
)
from mcp_server.experiments.infrastructure.storage.canonical_json import (
    canonical_json_bytes,
)


@dataclass(frozen=True, slots=True)
class _ReplayTask:
    phase: str
    directory_name: str
    output_name: str | None = None


@dataclass(frozen=True, slots=True)
class _FrozenInput:
    task: _ReplayTask
    model_id: str
    max_tokens: int
    temperature: float
    full_prompt: str
    prompt_sha256: str
    context_sha256: str
    request_sha256: str
    full_prompt_sha256: str


_REPLAY_TASKS = (
    _ReplayTask("phase2", "005_AssetCheck_Interface"),
    _ReplayTask("phase2", "005_Maximo2AssetCheck"),
    _ReplayTask("phase2.6", "006_scorecard"),
    _ReplayTask("phase3", "007_00_AGENT_BRIEF", "00_AGENT_BRIEF.md"),
    _ReplayTask("phase3", "007_01_ARCHITECTURE_MAP", "01_ARCHITECTURE_MAP.md"),
    _ReplayTask("phase3", "007_02_COMPONENT_INVENTORY", "02_COMPONENT_INVENTORY.md"),
    _ReplayTask(
        "phase3",
        "007_03_DATA_FLOWS_AND_LINEAGE",
        "03_DATA_FLOWS_AND_LINEAGE.md",
    ),
    _ReplayTask(
        "phase3",
        "011_04_BUSINESS_RULES_AND_TRANSFORMATIONS",
        "04_BUSINESS_RULES_AND_TRANSFORMATIONS.md",
    ),
    _ReplayTask(
        "phase3",
        "011_05_RUNTIME_CONFIG_AND_EXTERNALS",
        "05_RUNTIME_CONFIG_AND_EXTERNALS.md",
    ),
    _ReplayTask(
        "phase3",
        "011_06_OPERATIONS_TESTING_AND_DEBUGGING",
        "06_OPERATIONS_TESTING_AND_DEBUGGING.md",
    ),
    _ReplayTask(
        "phase3",
        "011_07_QUALITY_SECURITY_RISK_SCORECARD",
        "07_QUALITY_SECURITY_RISK_SCORECARD.md",
    ),
    _ReplayTask("phase3", "015_08_CHANGE_PLAYBOOK", "08_CHANGE_PLAYBOOK.md"),
    _ReplayTask("phase3", "016_09_EXECUTIVE_SUMMARY", "09_EXECUTIVE_SUMMARY.md"),
)


class LegacyReplayOpenCodeRunner:
    """Execute the preserved AlitaLand Phase 2/2.6/3 inputs without rewriting them."""

    def __init__(
        self,
        *,
        client: OpenCodeHttpClient,
        output_root: Path,
        project_root: Path,
        timeout_seconds: int = 3600,
    ) -> None:
        self._client = client
        self._output_root = Path(output_root)
        self._project_root = Path(project_root)
        self._timeout_seconds = timeout_seconds

    async def run(
        self,
        *,
        project_id: str,
        experiment_id: str,
        provider_id: str,
        output_names: tuple[str, ...],
    ) -> QuickCandidateResult:
        health = await self._client.health()
        if str(health.get("version") or "") != "1.18.4":
            raise QuickCandidateError("OpenCode runtime version is not pinned to 1.18.4")

        docs_root = self._project_root / "output" / project_id / "docs"
        _require_phase_one_skip(docs_root)
        frozen_inputs = tuple(
            _load_frozen_input(docs_root=docs_root, task=task) for task in _REPLAY_TASKS
        )
        expected_outputs = tuple(
            task.output_name for task in _REPLAY_TASKS if task.output_name is not None
        )
        if output_names != expected_outputs:
            raise QuickCandidateError(
                "legacy replay output contract does not match the preserved Phase 3 run"
            )

        store = ArtifactStore(
            output_root=self._output_root,
            project_id=project_id,
            experiment_id=experiment_id,
        )
        artifacts: list[StoredArtifact] = []
        task_receipts: list[dict[str, Any]] = []
        aggregate_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "billed_cost_usd": Decimal("0"),
        }
        known_usage = {
            "input_tokens": False,
            "output_tokens": False,
            "cache_read_tokens": False,
            "billed_cost_usd": False,
        }
        started = monotonic()
        last_session_id = ""

        for index, frozen in enumerate(frozen_inputs, start=1):
            task_started = monotonic()
            session = await self._client.create_session(
                title=(
                    f"Nexus legacy replay {project_id} "
                    f"{frozen.task.phase}/{frozen.task.directory_name}"
                )
            )
            last_session_id = session.session_id
            await self._client.prompt_async(
                session.session_id,
                payload={
                    "model": {
                        "providerID": provider_id,
                        "modelID": frozen.model_id,
                    },
                    "parts": [{"type": "text", "text": frozen.full_prompt}],
                    # The legacy call made no tool calls. Adding OpenCode filesystem
                    # tools would change the controlled input and invalidate parity.
                    "tools": {},
                },
                idempotency_key=f"{experiment_id}:legacy-replay:{index:03d}",
            )
            messages = await self._wait_for_messages(session.session_id)
            output = _assistant_text(messages)
            usage = _usage(messages)
            _add_usage(aggregate_usage, known_usage, usage)
            relative_output = _task_output_path(frozen.task)
            artifact = store.write_bytes(
                branch="candidate",
                logical_name=relative_output,
                relative_path=relative_output,
                content=(output.rstrip() + "\n").encode("utf-8"),
                media_type="text/markdown",
                kind="document" if frozen.task.output_name else "analysis",
            )
            artifacts.append(artifact)
            task_receipts.append(
                {
                    "phase": frozen.task.phase,
                    "task": frozen.task.directory_name,
                    "output": relative_output,
                    "recorded_model_id": frozen.model_id,
                    "recorded_max_tokens": frozen.max_tokens,
                    "recorded_temperature": frozen.temperature,
                    "runtime_model_controls_applied": False,
                    "prompt_sha256": frozen.prompt_sha256,
                    "context_bundle_sha256": frozen.context_sha256,
                    "request_sha256": frozen.request_sha256,
                    "reconstructed_full_prompt_sha256": frozen.full_prompt_sha256,
                    "session_id": session.session_id,
                    "elapsed_ms": int((monotonic() - task_started) * 1000),
                    "usage": usage,
                }
            )

        receipt = {
            "schema_version": 1,
            "replay_mode": "legacy-model-input-snapshot",
            "project_id": project_id,
            "experiment_id": experiment_id,
            "runtime": "opencode/1.18.4",
            "provider_id": provider_id,
            "phase1": {
                "status": "skipped",
                "reason": "Direct path: Phase 1 skipped for small project.",
            },
            "tools": [],
            "tasks": task_receipts,
            "limitations": [
                (
                    "The legacy forensic snapshot preserves the exact rendered prompt "
                    "and context contents, but not the provider SDK's final serialized "
                    "HTTP request."
                ),
                (
                    "Recorded max_tokens and temperature are retained in this receipt; "
                    "OpenCode 1.18.4 does not expose per-prompt controls for them."
                ),
            ],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        receipt_artifact = store.write_bytes(
            branch="candidate",
            logical_name="receipts/legacy_replay.json",
            relative_path="receipts/legacy_replay.json",
            content=canonical_json_bytes(receipt),
            media_type="application/json",
            kind="receipt",
        )
        artifacts.append(receipt_artifact)
        public_usage = _public_usage(aggregate_usage, known_usage)
        finalized = store.finalize_branch(
            branch="candidate",
            manifest={
                "schema_version": 1,
                "experiment_id": experiment_id,
                "project_id": project_id,
                "runtime": "opencode/1.18.4",
                "provider_id": provider_id,
                "model_routes": sorted({item.model_id for item in frozen_inputs}),
                "replay_mode": "legacy-model-input-snapshot",
                "phase1": "skipped-as-baseline",
                "publishes": False,
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
                "usage": public_usage,
            },
        )
        return QuickCandidateResult(
            artifacts=tuple(artifacts),
            manifest_sha256=finalized.manifest_sha256,
            session_id=last_session_id,
            input_tokens=public_usage["input_tokens"],
            output_tokens=public_usage["output_tokens"],
            cache_read_tokens=public_usage["cache_read_tokens"],
            billed_cost_usd=public_usage["billed_cost_usd"],
            elapsed_ms=int((monotonic() - started) * 1000),
        )

    async def _wait_for_messages(self, session_id: str) -> list[dict[str, Any]]:
        deadline = monotonic() + self._timeout_seconds
        while monotonic() < deadline:
            messages = [dict(item) for item in await self._client.messages(session_id)]
            if _has_completed_assistant(messages):
                return messages
            await asyncio.sleep(1)
        raise QuickCandidateError(
            f"OpenCode legacy replay timed out for session {session_id}"
        )


def _require_phase_one_skip(docs_root: Path) -> None:
    summary = docs_root / "_investigation" / "_summary.md"
    if not summary.is_file() or summary.is_symlink():
        raise QuickCandidateError("legacy Phase 1 decision snapshot is unavailable")
    if "Phase 1 skipped for small project" not in summary.read_text(encoding="utf-8"):
        raise QuickCandidateError(
            "legacy Phase 1 was not skipped; this replay requires its frozen inputs"
        )


def _load_frozen_input(*, docs_root: Path, task: _ReplayTask) -> _FrozenInput:
    debug_dir = docs_root / "_debug" / "model_inputs" / task.phase / task.directory_name
    ledger_dir = docs_root / "_model_inputs" / task.phase / task.directory_name
    prompt_path = debug_dir / "prompt.md"
    context_path = debug_dir / "context_bundle.md"
    request_path = debug_dir / "request.json"
    manifest_path = ledger_dir / "manifest.json"
    for path in (prompt_path, context_path, request_path, manifest_path):
        if not path.is_file() or path.is_symlink():
            raise QuickCandidateError(
                f"legacy replay input is unavailable: {task.phase}/{task.directory_name}/{path.name}"
            )
    prompt_bytes = prompt_path.read_bytes()
    context_bytes = context_path.read_bytes()
    request_bytes = request_path.read_bytes()
    request = _json_object(request_bytes, request_path)
    manifest = _json_object(manifest_path.read_bytes(), manifest_path)
    model_id = _required_string(request, "model", request_path)
    max_tokens = _required_int(request, "max_tokens", request_path)
    temperature = _required_number(request, "temperature", request_path)
    full_prompt = _reconstruct_legacy_prompt(
        prompt=prompt_bytes.decode("utf-8"),
        context_bundle=context_bytes.decode("utf-8"),
        context_entries=manifest.get("context_files"),
        label=f"{task.phase}/{task.directory_name}",
    )
    return _FrozenInput(
        task=task,
        model_id=model_id,
        max_tokens=max_tokens,
        temperature=temperature,
        full_prompt=full_prompt,
        prompt_sha256=_sha256(prompt_bytes),
        context_sha256=_sha256(context_bytes),
        request_sha256=_sha256(request_bytes),
        full_prompt_sha256=_sha256(full_prompt.encode("utf-8")),
    )


def _reconstruct_legacy_prompt(
    *,
    prompt: str,
    context_bundle: str,
    context_entries: object,
    label: str,
) -> str:
    if not isinstance(context_entries, list) or not context_entries:
        raise QuickCandidateError(f"legacy context manifest is invalid: {label}")
    marker = "\n## Context Files"
    marker_index = context_bundle.find(marker)
    if marker_index < 0:
        raise QuickCandidateError(f"legacy context bundle has no context section: {label}")
    context_section = context_bundle[marker_index + len(marker) :]
    contents: list[tuple[str, str]] = []
    cursor = 0
    for position, raw_entry in enumerate(context_entries):
        if not isinstance(raw_entry, dict):
            raise QuickCandidateError(f"legacy context entry is invalid: {label}")
        index = raw_entry.get("index")
        path_value = raw_entry.get("path")
        if not isinstance(index, int) or not isinstance(path_value, str):
            raise QuickCandidateError(f"legacy context entry is incomplete: {label}")
        heading = f"\n\n### {index}. {path_value}\n\n"
        start = context_section.find(heading, cursor)
        if start < 0:
            raise QuickCandidateError(
                f"legacy context content is unavailable: {label}/{path_value}"
            )
        content_start = start + len(heading)
        if position + 1 < len(context_entries):
            next_entry = context_entries[position + 1]
            if not isinstance(next_entry, dict):
                raise QuickCandidateError(f"legacy context entry is invalid: {label}")
            next_heading = (
                f"\n\n### {next_entry.get('index')}. {next_entry.get('path')}\n\n"
            )
            content_end = context_section.find(next_heading, content_start)
            if content_end < 0:
                raise QuickCandidateError(
                    f"legacy context boundary is unavailable: {label}/{path_value}"
                )
        else:
            content_end = len(context_section)
        contents.append((PurePosixPath(path_value).name, context_section[content_start:content_end]))
        cursor = content_end
    full_prompt = prompt.rstrip()
    for name, content in contents:
        full_prompt += f"\n\n--- CONTEXT FILE: {name} ---\n"
        full_prompt += content.rstrip() + "\n"
    return full_prompt


def _assistant_text(messages: list[dict[str, Any]]) -> str:
    message = _latest_assistant(messages)
    text = "".join(
        str(part.get("text") or "")
        for part in message.get("parts", [])
        if isinstance(part, dict) and part.get("type") == "text"
    ).strip()
    if not text:
        raise QuickCandidateError("OpenCode legacy replay returned no Markdown text")
    return text


def _task_output_path(task: _ReplayTask) -> str:
    if task.output_name:
        return f"docs/{task.output_name}"
    safe_phase = task.phase.replace(".", "_")
    return f"intermediates/{safe_phase}/{task.directory_name}.md"


def _add_usage(
    aggregate: dict[str, int | Decimal],
    known: dict[str, bool],
    usage: dict[str, Any],
) -> None:
    for key in ("input_tokens", "output_tokens", "cache_read_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            aggregate[key] = int(aggregate[key]) + value
            known[key] = True
    cost = usage.get("billed_cost_usd")
    if isinstance(cost, str):
        aggregate["billed_cost_usd"] = Decimal(aggregate["billed_cost_usd"]) + Decimal(cost)
        known["billed_cost_usd"] = True


def _public_usage(
    aggregate: dict[str, int | Decimal],
    known: dict[str, bool],
) -> dict[str, int | str | None]:
    return {
        "input_tokens": (
            int(aggregate["input_tokens"]) if known["input_tokens"] else None
        ),
        "output_tokens": (
            int(aggregate["output_tokens"]) if known["output_tokens"] else None
        ),
        "cache_read_tokens": (
            int(aggregate["cache_read_tokens"]) if known["cache_read_tokens"] else None
        ),
        "billed_cost_usd": (
            str(aggregate["billed_cost_usd"])
            if known["billed_cost_usd"]
            else None
        ),
    }


def _json_object(content: bytes, path: Path) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QuickCandidateError(f"legacy replay JSON is invalid: {path}") from exc
    if not isinstance(value, dict):
        raise QuickCandidateError(f"legacy replay JSON must be an object: {path}")
    return value


def _required_string(value: dict[str, Any], key: str, path: Path) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise QuickCandidateError(f"legacy replay {key} is invalid: {path}")
    return item


def _required_int(value: dict[str, Any], key: str, path: Path) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool) or item <= 0:
        raise QuickCandidateError(f"legacy replay {key} is invalid: {path}")
    return item


def _required_number(value: dict[str, Any], key: str, path: Path) -> float:
    item = value.get(key)
    if not isinstance(item, (int, float)) or isinstance(item, bool):
        raise QuickCandidateError(f"legacy replay {key} is invalid: {path}")
    return float(item)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
