"""Run the exact 100-attempt authenticated Slice 1 compatibility matrix."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import src.db.models  # noqa: F401
from scripts.agent_runtime_eval.corpus import load_corpus
from scripts.agent_runtime_eval.security import require_sanitized
from src.config import Settings
from src.db.base import Base

from .checkpoint import SafeAgentCheckpointSaver
from .models import (
    CANDIDATE_VERSIONS,
    KIMI_PROFILE,
    AttemptResult,
    CompatibilityReport,
    SpikeExecution,
    SpikeRunIdentity,
)
from .provider import build_kimi_chat_model, category_system_suffix
from .runtime import StandardRuntimeSpike, execution_to_trace


REPORT_ROOT = Path(__file__).resolve().parents[1] / "eval_reports" / "agent_runtime_v1"
REPO_ROOT = Path(__file__).resolve().parents[4]


def _category_scenarios() -> list[tuple[str, Any]]:
    corpus = load_corpus()

    def pool(families: set[str], variants: set[str]) -> list[Any]:
        return [
            scenario
            for scenario in corpus
            if scenario.family in families and scenario.variant in variants
        ]

    tool_variants = {"formal", "informal", "typo", "pronoun", "keyword_collision"}
    direct_pool = pool({"direct_conversation"}, tool_variants)
    single_pool = pool(
        {"documentation_rag", "metadata", "workflow_status"}, tool_variants
    )
    two_pool = pool({"multi_part_read"}, tool_variants)
    error_pool = pool({"correctable_args"}, tool_variants)
    stream_pool = pool({"direct_conversation"}, tool_variants)
    checkpoint_pool = pool({"multi_part_read"}, tool_variants)
    matrix = (
        [("direct", direct_pool[index % len(direct_pool)]) for index in range(20)]
        + [("single_read", single_pool[index % len(single_pool)]) for index in range(30)]
        + [("two_tool", two_pool[index % len(two_pool)]) for index in range(20)]
        + [("typed_error", error_pool[index % len(error_pool)]) for index in range(10)]
        + [("streaming", stream_pool[index % len(stream_pool)]) for index in range(10)]
        + [("checkpoint", checkpoint_pool[index % len(checkpoint_pool)]) for index in range(10)]
    )
    counts = Counter(category for category, _ in matrix)
    if counts != {
        "direct": 20,
        "single_read": 30,
        "two_tool": 20,
        "typed_error": 10,
        "streaming": 10,
        "checkpoint": 10,
    }:
        raise AssertionError(f"invalid attempt matrix: {counts}")
    return matrix


def _tool_names(category: str, scenario) -> list[str] | None:  # noqa: ANN001
    if category in {"direct", "streaming"}:
        return None
    if category == "single_read":
        return list(scenario.turns[0].expected.allowed_tool_sequences[0])
    if category in {"two_tool", "checkpoint"}:
        return ["workflow_status_read", "pack_list"]
    if category == "typed_error":
        return ["documentation_search"]
    raise ValueError(category)


def _expected_protocol(category: str, scenario, execution: SpikeExecution) -> bool:  # noqa: ANN001
    tools = [str(call.get("name") or "") for call in execution.tool_calls]
    statuses = [str(item.get("status") or "") for item in execution.observations]
    response_ok = bool(execution.response.strip())
    if category in {"direct", "streaming"}:
        category_ok = not tools and response_ok
    elif category == "single_read":
        category_ok = tools == scenario.turns[0].expected.allowed_tool_sequences[0] and response_ok
    elif category in {"two_tool", "checkpoint"}:
        category_ok = tools == ["workflow_status_read", "pack_list"] and response_ok
    else:
        category_ok = (
            tools == ["documentation_search", "documentation_search"]
            and statuses == ["validation_failed", "evidence_found"]
            and response_ok
        )
    if category == "streaming":
        category_ok = category_ok and sum(
            event.kind == "assistant.delta" for event in execution.events
        ) >= 2
    if category == "checkpoint":
        category_ok = category_ok and execution.resumed
    return execution.status == "completed" and category_ok


def _combine_segments(segments: list[SpikeExecution]) -> SpikeExecution:
    final = segments[-1]
    return final.model_copy(
        update={
            "observations": [item for segment in segments for item in segment.observations],
            "proposed_call_ids": [
                call_id for segment in segments for call_id in segment.proposed_call_ids
            ],
            "intercepted_call_ids": [
                call_id for segment in segments for call_id in segment.intercepted_call_ids
            ],
            "events": [event for segment in segments for event in segment.events],
            "interrupted": any(segment.interrupted for segment in segments),
            "resumed": len(segments) > 1,
        }
    )


async def _run_attempt(
    *,
    category: str,
    scenario,
    index: int,
    api_key: str,
    session_factory,
) -> tuple[AttemptResult, Any]:  # noqa: ANN001
    attempt_id = f"{category}-{index:03d}"
    identity = SpikeRunIdentity(
        agent_run_id=f"spike-{category.replace('_', '-')}-{index:03d}-runtime",
        manifest_hash=scenario.authorized_context.manifest_hash,
    )
    started = time.perf_counter()
    segments: list[SpikeExecution] = []
    sequence_start = 0
    max_segments = 4 if category == "checkpoint" else 1
    for stage in range(max_segments):
        checkpointer = (
            SafeAgentCheckpointSaver(session_factory=session_factory)
            if category == "checkpoint"
            else None
        )
        runtime = StandardRuntimeSpike(
            model=build_kimi_chat_model(api_key),
            identity=identity,
            tool_names=_tool_names(category, scenario),
            checkpointer=checkpointer,
            interrupt_before_tools=category == "checkpoint",
            event_sequence_start=sequence_start,
            system_prompt_suffix=category_system_suffix(category, scenario),
        )
        execution = await runtime.run(
            [
                *scenario.conversation_seed,
                {"role": "user", "content": scenario.turns[0].user_message},
            ]
            if stage == 0
            else None,
            resume=stage > 0,
        )
        segments.append(execution)
        sequence_start = execution.events[-1].sequence
        if execution.status != "interrupted":
            break
    combined = _combine_segments(segments)
    protocol_valid = _expected_protocol(category, scenario, combined)
    middleware_complete = all(segment.middleware_complete for segment in segments)
    checkpoint_valid = (
        category != "checkpoint"
        or (
            combined.resumed
            and combined.interrupted
            and combined.checkpoint_namespace == identity.checkpoint_namespace
        )
    )
    sequences = [event.sequence for event in combined.events]
    stream_valid = sequences == list(range(1, len(sequences) + 1)) and len(
        {event.event_id for event in combined.events}
    ) == len(combined.events)
    result = AttemptResult(
        attempt_id=attempt_id,
        category=category,
        scenario_id=scenario.scenario_id,
        completed=combined.status == "completed",
        protocol_valid=protocol_valid,
        middleware_complete=middleware_complete,
        unauthorized_effects=combined.unauthorized_effects,
        duplicate_effects=combined.duplicate_effects,
        checkpoint_identity_valid=checkpoint_valid,
        stream_identity_valid=stream_valid,
        latency_ms=int((time.perf_counter() - started) * 1000),
        failure_code=(
            None
            if protocol_valid and middleware_complete and checkpoint_valid and stream_valid
            else combined.error_code or "RUNTIME_PROTOCOL"
        ),
    )
    return result, execution_to_trace(scenario, combined)


def _verdict(attempts: list[AttemptResult]) -> tuple[str, list[str]]:
    blockers: list[str] = []
    if len(attempts) != 100:
        return "BLOCKED", [f"authenticated attempts incomplete: {len(attempts)}/100"]
    if not all(item.middleware_complete for item in attempts):
        blockers.append("middleware interception below 100%")
    if any(item.unauthorized_effects or item.duplicate_effects for item in attempts):
        blockers.append("unauthorized or duplicate effect observed")
    mechanics = [item for item in attempts if item.category in {"streaming", "checkpoint"}]
    if not all(item.stream_identity_valid and item.checkpoint_identity_valid for item in mechanics):
        blockers.append("checkpoint/stream identity below 100%")
    protocol_rate = sum(item.protocol_valid for item in attempts) / len(attempts)
    if protocol_rate < 0.98:
        blockers.append(f"protocol completion {protocol_rate:.3f} below 0.98")
    two_tool = [item for item in attempts if item.category == "two_tool"]
    two_tool_rate = sum(item.protocol_valid for item in two_tool) / len(two_tool)
    if two_tool_rate < 0.95:
        blockers.append(f"two-tool completion {two_tool_rate:.3f} below 0.95")
    return ("NO_GO", blockers) if blockers else ("GO", [])


async def run_authenticated(output_dir: Path, *, report_suffix: str = "") -> CompatibilityReport:
    settings = Settings(_env_file=REPO_ROOT / ".env")
    api_key = settings.kimi_api_key.get_secret_value() if hasattr(settings.kimi_api_key, "get_secret_value") else str(settings.kimi_api_key)
    if not api_key.strip():
        report = CompatibilityReport(
            runtime_versions=CANDIDATE_VERSIONS,
            model_profile=KIMI_PROFILE,
            credential_available=False,
            network_available=False,
            attempts=[],
            verdict="BLOCKED",
            blockers=["KIMI_API_KEY is unavailable"],
        )
        _write_reports(output_dir, report, [], suffix=report_suffix)
        return report

    attempts: list[AttemptResult] = []
    traces = []
    with tempfile.TemporaryDirectory(prefix="nexus-slice1-checkpoint-") as temp_dir:
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(temp_dir) / 'checkpoint.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            for index, (category, scenario) in enumerate(_category_scenarios(), start=1):
                result, trace = await _run_attempt(
                    category=category,
                    scenario=scenario,
                    index=index,
                    api_key=api_key,
                    session_factory=session_factory,
                )
                attempts.append(result)
                traces.append(trace)
                print(
                    f"{index:03d}/100 {category} "
                    f"{'PASS' if result.protocol_valid else result.failure_code}"
                )
        finally:
            await engine.dispose()
    verdict, blockers = _verdict(attempts)
    report = CompatibilityReport(
        runtime_versions=CANDIDATE_VERSIONS,
        model_profile=KIMI_PROFILE,
        credential_available=True,
        network_available=bool(attempts),
        attempts=attempts,
        verdict=verdict,
        blockers=blockers,
    )
    _write_reports(output_dir, report, traces, suffix=report_suffix)
    return report


def _write_reports(
    output_dir: Path,
    report: CompatibilityReport,
    traces: list[Any],
    *,
    suffix: str = "",
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = report.model_dump(mode="json")
    trace_payload = [trace.model_dump(mode="json") for trace in traces]
    require_sanitized(payload, label="Slice 1 compatibility report")
    require_sanitized(trace_payload, label="Slice 1 provider traces")
    label = f"_{suffix}" if suffix else ""
    (output_dir / f"slice_1_compatibility_report_v1{label}.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )
    (output_dir / f"slice_1_authenticated_traces_v1{label}.json").write_text(
        json.dumps({"traces": trace_payload}, indent=2, sort_keys=True) + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=REPORT_ROOT)
    parser.add_argument("--report-suffix", default="")
    args = parser.parse_args()
    report = asyncio.run(
        run_authenticated(args.output_dir, report_suffix=args.report_suffix)
    )
    print(f"Slice 1 authenticated verdict: {report.verdict}")
    return 0 if report.verdict == "GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
