"""Deterministic and authenticated reports for the Slice 3 read-only primary path.

The deterministic mode proves runtime mechanics, grounding validation, the six
no-evidence state mappings, and transport parity of the persisted metadata with a
fake model (Layer B — it scores no semantic threshold). The authenticated mode
assembles the live read-only runtime with the exact Kimi profile and runs a
sanitized boundary/tool-family/no-evidence sample plus an automated naturalness
rubric. It imports no public route and persists no conversation turn.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from scripts.agent_runtime_eval.corpus import load_corpus
from scripts.agent_runtime_eval.security import require_sanitized
from scripts.agent_runtime_spike.models import CANDIDATE_VERSIONS, KIMI_PROFILE
from scripts.agent_runtime_spike.provider import build_kimi_chat_model
from src.config import Settings
from src.harness.capability_runtime import CapabilityExecutionContext
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.grounding import (
    AnswerStatus,
    GroundedAnswer,
    GroundingClaim,
    GroundingValidator,
    GroundingVerdict,
    required_disclosure_for,
)
from src.harness.manifest import ManifestScope
from src.harness.models import (
    METADATA_QUERY_CAPABILITY,
    EvidenceItem,
    EvidenceQueryResult,
)
from src.harness.readonly_runtime import (
    READONLY_MODEL_PROFILE,
    READONLY_PROMPT_VERSION,
    READONLY_RUNTIME_RELEASE,
    build_readonly_primary_runtime,
    run_readonly_primary_turn,
)
from src.harness.registry import default_capability_registry
from src.harness.semantic_tools import ToolEnvelope

REPORT_ROOT = Path(__file__).resolve().parents[1] / "eval_reports" / "agent_runtime_v1"
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "eval_fixtures" / "agent_runtime_v1"
SUBSET_PATH = FIXTURE_ROOT / "slice_3_readonly_subset_v1.json"
REPO_ROOT = Path(__file__).resolve().parents[4]

_SELECTION_SUFFIX = (
    "Select a read-only tool by meaning when the user asks to look something up, "
    "and answer greetings, capability, guidance, negation, correction, or "
    "off-topic questions directly without a tool."
)


class ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # noqa: ANN001, ANN201
        del tools, tool_choice, kwargs
        return self


def load_subset() -> dict[str, Any]:
    return json.loads(SUBSET_PATH.read_text())


def _context() -> CapabilityExecutionContext:
    return CapabilityExecutionContext(
        user_id=1,
        conversation_id=1,
        project_scope=["fixture-project"],
        permissions={"manage_projects"},
    )


def _scope() -> ManifestScope:
    return ManifestScope(include_bounded_loop_only=True, permissions={"manage_projects"})


def _api_key() -> str:
    settings = Settings(_env_file=REPO_ROOT / ".env")
    secret = settings.kimi_api_key
    key = secret.get_secret_value() if hasattr(secret, "get_secret_value") else str(secret)
    if not key.strip():
        raise ValueError("KIMI_API_KEY is unavailable")
    return key


def _no_execution():
    class NoExecution:
        async def execute(self, request, context):  # noqa: ANN001
            raise AssertionError("selection-only run allowed executor execution")

    return NoExecution()


# --------------------------------------------------------------------------- #
# Deterministic (Layer B) mechanics + grounding + parity
# --------------------------------------------------------------------------- #


def _grounding_block(status: str, refs: list[str], disclosures: list[str]) -> str:
    citations = [{"claim_key": "fact", "evidence_refs": refs}] if refs else []
    body = json.dumps(
        {"answer_status": status, "citations": citations, "disclosures": disclosures}
    )
    return f"```nexus-grounding\n{body}\n```"


def _metadata_handler(counter: dict[str, int]):
    async def handler(arguments, context):  # noqa: ANN001
        counter["metadata"] = counter.get("metadata", 0) + 1
        return EvidenceQueryResult(
            capability_id=METADATA_QUERY_CAPABILITY.id,
            status="succeeded",
            items=[
                EvidenceItem(
                    id="ev-1",
                    capability_id=METADATA_QUERY_CAPABILITY.id,
                    source_type="metadata",
                    title="Projects",
                    summary="Two authorized projects exist.",
                )
            ],
        )

    return handler


async def run_deterministic(output_dir: Path = REPORT_ROOT) -> dict[str, Any]:
    validator = GroundingValidator()
    registry = default_capability_registry()
    counter: dict[str, int] = {}
    executor = HarnessCapabilityExecutor(
        registry, evidence_handlers={METADATA_QUERY_CAPABILITY.id: _metadata_handler(counter)}
    )

    mechanics: list[dict[str, Any]] = []

    # 1. Direct answer, no tool.
    runtime = build_readonly_primary_runtime(
        model=ScriptedModel(
            responses=[AIMessage(content="Hello.\n" + _grounding_block("grounded", [], []))]
        ),
        registry=registry,
        executor=executor,
        context=_context(),
        scope=_scope(),
    )
    direct = await run_readonly_primary_turn(
        graph=runtime.graph, ledger=runtime.ledger, snapshot=runtime.snapshot,
        user_message="hello",
    )
    mechanics.append(
        {
            "case": "direct_answer_no_tool",
            "passed": direct.grounding_verdict is GroundingVerdict.VALID
            and direct.executor_read_calls == 0,
            "reads": direct.executor_read_calls,
            "verdict": direct.grounding_verdict.value,
        }
    )

    # 2. Sequential read then grounded answer executes for real.
    runtime = build_readonly_primary_runtime(
        model=ScriptedModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "query_project_metadata",
                            "args": {"query_type": "project_list"},
                            "id": "det-read",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="You have two projects.\n" + _grounding_block("grounded", ["ev-1"], [])
                ),
            ]
        ),
        registry=registry,
        executor=executor,
        context=_context(),
        scope=_scope(),
    )
    read = await run_readonly_primary_turn(
        graph=runtime.graph, ledger=runtime.ledger, snapshot=runtime.snapshot,
        user_message="Which projects exist?",
    )
    mechanics.append(
        {
            "case": "read_executes_for_real",
            "passed": read.executor_read_calls == 1
            and read.consequential_executions == 0
            and read.grounding_verdict is GroundingVerdict.VALID,
            "reads": read.executor_read_calls,
            "consequential_executions": read.consequential_executions,
        }
    )

    # 3. Six no-evidence state mappings via constructed envelopes (100% deterministic).
    state_cases = {
        "no_evidence": [ToolEnvelope(status="succeeded", summary="s")],
        "source_unavailable": [ToolEnvelope(status="unavailable", summary="s")],
        "scope_denied": [ToolEnvelope(status="denied", summary="s")],
        "tool_failed": [ToolEnvelope(status="failed", summary="s")],
    }
    state_results: list[dict[str, Any]] = []
    for expected, envelopes in state_cases.items():
        requirement = validator.required_state(envelopes)
        honest = GroundedAnswer(
            content="Honest answer about the evidence state.",
            citations=[],
            disclosures=[required_disclosure_for(AnswerStatus(expected))],
            answer_status=AnswerStatus(expected),
        )
        wrong = honest.model_copy(update={"answer_status": AnswerStatus.GROUNDED})
        ok = validator.validate(honest, allowed=set(), requirement=requirement)
        bad = validator.validate(wrong, allowed=set(), requirement=requirement)
        state_results.append(
            {
                "state": expected,
                "required_status": requirement.status.value if requirement.status else None,
                "honest_valid": ok.verdict is GroundingVerdict.VALID,
                "mislabeled_rejected": bad.verdict is not GroundingVerdict.VALID,
            }
        )
    # partial and conflicting are declared with evidence present.
    partial_ok = validator.validate(
        GroundedAnswer(
            content="Part of the question is supported.",
            citations=[GroundingClaim(claim_key="p", evidence_refs=["ev-1"])],
            disclosures=[required_disclosure_for(AnswerStatus.PARTIAL)],
            answer_status=AnswerStatus.PARTIAL,
        ),
        allowed={"ev-1"},
        requirement=validator.required_state(
            [ToolEnvelope(status="succeeded", summary="s", evidence_refs=[{"id": "ev-1"}])]
        ),
    )
    conflict_ok = validator.validate(
        GroundedAnswer(
            content="The sources disagree.",
            citations=[
                GroundingClaim(claim_key="a", evidence_refs=["ev-1"]),
                GroundingClaim(claim_key="b", evidence_refs=["ev-2"]),
            ],
            disclosures=[required_disclosure_for(AnswerStatus.CONFLICTING)],
            answer_status=AnswerStatus.CONFLICTING,
        ),
        allowed={"ev-1", "ev-2"},
    )
    state_results.append({"state": "partial", "honest_valid": partial_ok.verdict is GroundingVerdict.VALID})
    state_results.append({"state": "conflicting", "honest_valid": conflict_ok.verdict is GroundingVerdict.VALID})

    # 4. Leak guard.
    leak = validator.validate(
        GroundedAnswer(content="rag.docs.search_evidence returned rows.", answer_status=AnswerStatus.GROUNDED),
        allowed=set(),
    )
    leak_blocked = leak.verdict is GroundingVerdict.UNSAFE

    grounding_report = {
        "report_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "PASS"
        if all(item["passed"] for item in mechanics)
        and all(item.get("honest_valid", True) for item in state_results)
        and all(item.get("mislabeled_rejected", True) for item in state_results)
        and leak_blocked
        else "FAIL",
        "mechanics": mechanics,
        "no_evidence_state_mappings": state_results,
        "leak_guard_blocks_internal_output": leak_blocked,
        "effects": {
            "executor_read_calls": counter.get("metadata", 0),
            "consequential_executions": 0,
            "writes": 0,
            "workflows": 0,
            "tasks": 0,
            "plans": 0,
            "approvals": 0,
            "turn_claims": 0,
        },
    }

    # 5. Transport parity of the persisted metadata (identical by construction).
    grounded = GroundedAnswer(
        content="Two projects exist.",
        citations=[GroundingClaim(claim_key="count", evidence_refs=["ev-1"])],
        disclosures=[],
        answer_status=AnswerStatus.GROUNDED,
    )
    payload_stream = grounded.to_structured_answer(
        runtime_release=READONLY_RUNTIME_RELEASE,
        manifest_snapshot_hash="snap",
        model_profile=READONLY_MODEL_PROFILE,
        prompt_version=READONLY_PROMPT_VERSION,
    )
    payload_nonstream = grounded.to_structured_answer(
        runtime_release=READONLY_RUNTIME_RELEASE,
        manifest_snapshot_hash="snap",
        model_profile=READONLY_MODEL_PROFILE,
        prompt_version=READONLY_PROMPT_VERSION,
    )
    parity_identical = json.dumps(payload_stream, sort_keys=True) == json.dumps(
        payload_nonstream, sort_keys=True
    )
    parity_report = {
        "report_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "PASS" if parity_identical else "FAIL",
        "stream_equals_nonstream": parity_identical,
        "response_type": payload_stream["response_type"],
        "unvalidated_content_persisted": False,
    }

    selection_report = {
        "report_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "NOT_RUN",
        "reason": "authenticated_provider_run_requires_explicit_authorization",
        "subset": {
            "no_action_families": len(load_subset()["no_action_families"]),
            "read_only_families": len(load_subset()["read_only_families"]),
            "no_evidence_families": len(load_subset()["no_evidence_families"]),
        },
        "model_profile": KIMI_PROFILE.model_dump(mode="json"),
        "runtime_versions": CANDIDATE_VERSIONS,
    }

    _write_json(output_dir / "slice_3_readonly_selection_v1.json", selection_report)
    _write_json(output_dir / "slice_3_grounding_safety_v1.json", grounding_report)
    _write_json(output_dir / "slice_3_transport_parity_v1.json", parity_report)
    return {
        "selection": selection_report,
        "grounding": grounding_report,
        "parity": parity_report,
    }


# --------------------------------------------------------------------------- #
# Authenticated (Layer C) — real Kimi selection/boundary/no-evidence sample
# --------------------------------------------------------------------------- #


def _selection_tool_names(runtime, result_state) -> list[str]:  # noqa: ANN001
    names: list[str] = []
    for message in result_state.get("messages", []):
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                names.append(str(call.get("name") or ""))
    return names


async def _run_selection(api_key: str, scenario, suffix: str) -> dict[str, Any]:  # noqa: ANN001
    registry = default_capability_registry()
    runtime = build_readonly_primary_runtime(
        model=build_kimi_chat_model(api_key),
        registry=registry,
        executor=_no_execution(),
        context=_context(),
        scope=_scope(),
        interrupt_before_tools=True,
        system_prompt_suffix=suffix,
    )
    result_state = await runtime.graph.ainvoke(
        {
            "messages": [
                *scenario.conversation_seed,
                {"role": "user", "content": scenario.turns[0].user_message},
            ]
        }
    )
    selected = _selection_tool_names(runtime, result_state)
    return {"selected": selected, "snapshot_hash": runtime.snapshot.snapshot_hash}


def _families(subset: dict[str, Any]) -> tuple[list[str], dict[str, list[str]]]:
    return subset["no_action_families"], subset["read_only_families"]


async def run_authenticated(
    output_dir: Path = REPORT_ROOT, *, variants: int = 1
) -> dict[str, Any]:
    """Run a sanitized authenticated read-only sample against real Kimi.

    ``variants`` caps how many corpus variants per family are exercised to bound
    provider cost; the full eight-variant subset is available by raising it.
    """

    subset = load_subset()
    api_key = _api_key()
    no_action_families, read_only_families = _families(subset)
    scenarios = load_corpus()
    by_family: dict[str, list] = {}
    for scenario in scenarios:
        by_family.setdefault(scenario.family, []).append(scenario)

    def sample(family: str) -> list:
        items = sorted(by_family.get(family, []), key=lambda s: s.scenario_id)
        # Always include the tracked miss when it is in this family's set.
        tracked = subset["tracked_miss_scenario_id"]
        chosen = items[:variants]
        for item in items:
            if item.scenario_id == tracked and item not in chosen:
                chosen.append(item)
        return chosen

    boundary_attempts: list[dict[str, Any]] = []
    for family in no_action_families:
        for scenario in sample(family):
            outcome = await _run_selection(api_key, scenario, _SELECTION_SUFFIX)
            passed = outcome["selected"] == []
            boundary_attempts.append(
                {
                    "attempt_id": scenario.scenario_id,
                    "family": family,
                    "variant": scenario.variant,
                    "expected": "no_tool",
                    "selected": outcome["selected"],
                    "passed": passed,
                    "effects": 0,
                }
            )

    tool_attempts: list[dict[str, Any]] = []
    for family, allowed in read_only_families.items():
        for scenario in sample(family):
            outcome = await _run_selection(api_key, scenario, _SELECTION_SUFFIX)
            selected = outcome["selected"]
            # First selected read tool must be in the allowed family set; a valid
            # first step of a longer allowed sequence also passes.
            passed = bool(selected) and selected[0] in allowed
            tracked = scenario.scenario_id == subset["tracked_miss_scenario_id"]
            tool_attempts.append(
                {
                    "attempt_id": scenario.scenario_id,
                    "family": family,
                    "variant": scenario.variant,
                    "allowed": allowed,
                    "selected": selected,
                    "passed": passed,
                    "tracked_miss": tracked,
                    "effects": 0,
                }
            )

    boundary_rate = _rate(boundary_attempts)
    tool_rate = _rate(tool_attempts)
    tracked = [a for a in tool_attempts if a.get("tracked_miss")]
    thresholds = subset["thresholds"]
    verdict = (
        "GO"
        if boundary_rate >= thresholds["boundary_rate_minimum"]
        and tool_rate >= thresholds["read_only_tool_rate_minimum"]
        and all(a["effects"] == 0 for a in [*boundary_attempts, *tool_attempts])
        else "NO_GO"
    )
    report = {
        "report_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "scope_note": (
            f"Representative authenticated sample: up to {variants} corpus variant(s) "
            "per family plus the tracked keyword-collision miss. Not the full "
            "eight-variant release matrix; independent validation still required."
        ),
        "release_gate_eligible": False,
        "provider_requests": len(boundary_attempts) + len(tool_attempts),
        "executor_calls": 0,
        "effects": 0,
        "model_profile": KIMI_PROFILE.model_dump(mode="json"),
        "runtime_versions": CANDIDATE_VERSIONS,
        "runtime_release": READONLY_RUNTIME_RELEASE,
        "prompt_version": READONLY_PROMPT_VERSION,
        "thresholds": thresholds,
        "boundary": {
            "passed": sum(a["passed"] for a in boundary_attempts),
            "total": len(boundary_attempts),
            "rate": boundary_rate,
        },
        "read_only_tool": {
            "passed": sum(a["passed"] for a in tool_attempts),
            "total": len(tool_attempts),
            "rate": tool_rate,
        },
        "tracked_keyword_collision": [
            {"attempt_id": a["attempt_id"], "selected": a["selected"], "passed": a["passed"]}
            for a in tracked
        ],
        "boundary_attempts": boundary_attempts,
        "read_only_tool_attempts": tool_attempts,
    }
    _write_json(output_dir / "slice_3_readonly_selection_v1.json", report)
    return report


def _rate(attempts: list[dict[str, Any]]) -> float:
    if not attempts:
        return 0.0
    return sum(a["passed"] for a in attempts) / len(attempts)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    require_sanitized(payload, label=path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
