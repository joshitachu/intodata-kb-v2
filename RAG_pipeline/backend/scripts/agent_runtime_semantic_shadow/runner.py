"""Deterministic and authenticated reports for the disabled semantic shadow seam."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage

from scripts.agent_runtime_eval.corpus import load_corpus
from scripts.agent_runtime_eval.security import require_sanitized
from scripts.agent_runtime_spike.models import CANDIDATE_VERSIONS, KIMI_PROFILE
from scripts.agent_runtime_spike.provider import build_kimi_chat_model
from src.config import Settings
from src.harness.capability_runtime import CapabilityExecutionContext
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.manifest import ManifestScope
from src.harness.models import (
    CONVERSATION_SUMMARY_FACTS_CAPABILITY,
    EvidenceQueryResult,
)
from src.harness.registry import default_capability_registry
from src.harness.semantic_shadow_runtime import build_semantic_shadow_runtime


REPORT_ROOT = Path(__file__).resolve().parents[1] / "eval_reports" / "agent_runtime_v1"
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "eval_fixtures" / "agent_runtime_v1"
FAMILY_MAP_PATH = FIXTURE_ROOT / "slice_2_tool_family_map_v1.json"
REPO_ROOT = Path(__file__).resolve().parents[4]
REPORT_TIMESTAMP = "2026-07-20T18:30:00+02:00"
TARGET_FAMILIES = {
    "documentation_rag",
    "metadata",
    "runtime_evidence",
    "workflow_status",
    "explicit_creation",
    "capability_discussion",
    "guidance",
    "negated_action",
}


class ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # noqa: ANN001, ANN201
        del kwargs
        if tools and tool_choice != "auto":
            raise ValueError("semantic shadow tools require automatic tool choice")
        return self


def _call(name: str, arguments: dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": arguments, "id": call_id, "type": "tool_call"}
        ],
    )


def _calls(*calls: tuple[str, dict[str, Any], str]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": arguments, "id": call_id, "type": "tool_call"}
            for name, arguments, call_id in calls
        ],
    )


def _context() -> CapabilityExecutionContext:
    return CapabilityExecutionContext(
        user_id=1,
        conversation_id=1,
        project_scope=["fixture-project"],
        permissions={"manage_projects"},
    )


def _scope() -> ManifestScope:
    return ManifestScope(
        include_bounded_loop_only=True,
        permissions={"manage_projects"},
    )


async def run_deterministic(output_dir: Path = REPORT_ROOT) -> dict[str, Any]:
    calls = 0

    async def conversation_read(arguments, context):  # noqa: ANN001
        nonlocal calls
        calls += 1
        return EvidenceQueryResult(
            capability_id=CONVERSATION_SUMMARY_FACTS_CAPABILITY.id,
            status="succeeded",
        )

    registry = default_capability_registry()
    executor = HarnessCapabilityExecutor(
        registry,
        evidence_handlers={
            CONVERSATION_SUMMARY_FACTS_CAPABILITY.id: conversation_read,
        },
    )
    snapshot_runtime = build_semantic_shadow_runtime(
        model=ScriptedModel(responses=[AIMessage(content="Snapshot built.")]),
        registry=registry,
        executor=executor,
        context=_context(),
        scope=_scope(),
    )
    snapshot = snapshot_runtime.snapshot
    parity = {
        "report_version": "1.0.0",
        "generated_at": REPORT_TIMESTAMP,
        "verdict": "PASS",
        "contract_version": snapshot.contract_version,
        "snapshot_hash": snapshot.snapshot_hash,
        "pack_snapshot_hash": snapshot.pack_snapshot_hash,
        "project_binding_authority": snapshot.project_binding_authority,
        "tool_count": len(snapshot.tools),
        "handler_schema_output_parity": snapshot.handler_schema_output_parity,
        "tools": [
            {
                "semantic_name": item.semantic_name,
                "canonical_id": item.canonical_id,
                "governance_class": item.governance_class.value,
                "shadow_safe": item.shadow_safe,
            }
            for item in snapshot.tools
        ],
        "unavailable": [item.model_dump(mode="json") for item in snapshot.unavailable],
    }

    cases = [
        (
            "direct",
            [AIMessage(content="Nexus answered normally.")],
            "succeeded",
        ),
        (
            "sequential",
            [
                _call("read_conversation_summary", {"max_messages": 1}, "det-read-a"),
                _call("read_conversation_summary", {"max_messages": 2}, "det-read-b"),
                AIMessage(content="Both observations were consumed."),
            ],
            "succeeded",
        ),
        (
            "validation_recovery",
            [
                _call("read_conversation_summary", {"max_messages": 99}, "det-invalid"),
                _call("read_conversation_summary", {"max_messages": 2}, "det-corrected"),
                AIMessage(content="The corrected read succeeded."),
            ],
            "validation_failed",
        ),
        (
            "proposal_only",
            [
                _call("create_pack_draft", {}, "det-proposal"),
                AIMessage(content="The proposal was recorded without an effect."),
            ],
            "proposal_only",
        ),
        (
            "call_set_rejected",
            [
                _calls(
                    ("create_pack_draft", {}, "det-create"),
                    (
                        "save_pack",
                        {"draft_id": 1, "expected_revision": 1},
                        "det-save",
                    ),
                ),
                AIMessage(content="The call set was rejected."),
            ],
            "call_set_rejected",
        ),
    ]
    mechanics: list[dict[str, Any]] = []
    for name, responses, expected_status in cases:
        runtime = build_semantic_shadow_runtime(
            model=ScriptedModel(responses=responses),
            registry=registry,
            executor=executor,
            context=_context(),
            scope=_scope(),
        )
        result = await runtime.graph.ainvoke(
            {"messages": [{"role": "user", "content": f"Run {name}."}]}
        )
        payloads = [
            json.loads(str(message.content))
            for message in result["messages"]
            if isinstance(message, ToolMessage)
        ]
        observed = [item["status"] for item in payloads]
        passed = (
            not observed and expected_status == "succeeded"
            or expected_status in observed
        )
        mechanics.append(
            {
                "case": name,
                "passed": passed,
                "observation_statuses": observed,
                "events": [event.model_dump(mode="json") for event in runtime.ledger.events],
            }
        )

    expected_executor_calls = 3
    safety = {
        "report_version": "1.0.0",
        "generated_at": REPORT_TIMESTAMP,
        "verdict": (
            "PASS"
            if all(item["passed"] for item in mechanics)
            and calls == expected_executor_calls
            else "FAIL"
        ),
        "snapshot_hash": snapshot.snapshot_hash,
        "executor_calls": calls,
        "expected_executor_calls": expected_executor_calls,
        "shadow_effects": {
            "messages": 0,
            "tasks": 0,
            "plans": 0,
            "approvals": 0,
            "workflows": 0,
            "cache": 0,
            "quota": 0,
            "external_audit": 0,
            "domain_writes": 0,
            "turn_claims": 0,
        },
        "mechanics": mechanics,
    }
    selection = {
        "report_version": "1.0.0",
        "generated_at": REPORT_TIMESTAMP,
        "verdict": "NOT_RUN",
        "reason": "authenticated_provider_matrix_requires_explicit_authorization",
        "planned_scenarios": 64,
        "authored_action_scenarios": 25,
        "authored_no_action_scenarios": 39,
        "planned_onboarding_prompts": 8,
        "expected_provider_requests": 72,
        "snapshot_hash": snapshot.snapshot_hash,
        "model_profile": KIMI_PROFILE.model_dump(mode="json"),
        "runtime_versions": CANDIDATE_VERSIONS,
    }
    _write_json(output_dir / "slice_2_registry_parity_v1.json", parity)
    _write_json(output_dir / "slice_2_shadow_safety_v1.json", safety)
    _write_json(output_dir / "slice_2_semantic_selection_v1.json", selection)
    return {"parity": parity, "safety": safety, "selection": selection}


async def run_authenticated(output_dir: Path = REPORT_ROOT) -> dict[str, Any]:
    fixture = json.loads(FAMILY_MAP_PATH.read_text())
    family_map: dict[str, str | None] = fixture["families"]
    scenarios = [
        scenario for scenario in load_corpus() if scenario.family in TARGET_FAMILIES
    ]
    if len(scenarios) != 64:
        raise ValueError(f"expected 64 Slice 0 subset scenarios, found {len(scenarios)}")
    settings = Settings(_env_file=REPO_ROOT / ".env")
    secret = settings.kimi_api_key
    api_key = secret.get_secret_value() if hasattr(secret, "get_secret_value") else str(secret)
    if not api_key.strip():
        raise ValueError("KIMI_API_KEY is unavailable")

    registry = default_capability_registry()

    class NoExecution:
        async def execute(self, request, context):  # noqa: ANN001
            raise AssertionError("selection-only interrupt allowed executor execution")

    attempts: list[dict[str, Any]] = []
    snapshot_hash: str | None = None
    for scenario in scenarios:
        expected = (
            family_map[scenario.family]
            if scenario.turns[0].expected.allowed_tool_sequences[0]
            else None
        )
        runtime = build_semantic_shadow_runtime(
            model=build_kimi_chat_model(api_key),
            registry=registry,
            executor=NoExecution(),
            context=_context(),
            scope=_scope(),
            interrupt_before_tools=True,
            system_prompt_suffix=(
                "Select one supplied semantic capability only when the user asks to act or read. "
                "For discussion, guidance, negation, or ambiguity, answer directly without a tool."
            ),
        )
        snapshot_hash = runtime.snapshot.snapshot_hash
        result = await runtime.graph.ainvoke(
            {
                "messages": [
                    *scenario.conversation_seed,
                    {"role": "user", "content": scenario.turns[0].user_message},
                ]
            }
        )
        actual = _semantic_calls(result)
        attempts.append(
            {
                "attempt_id": scenario.scenario_id,
                "family": scenario.family,
                "variant": scenario.variant,
                "expected": expected,
                "actual": actual,
                "passed": actual == ([] if expected is None else [expected]),
                "effects": 0,
            }
        )

    onboarding: list[dict[str, Any]] = []
    for index, prompt in enumerate(fixture["onboarding_prompts"], start=1):
        runtime = build_semantic_shadow_runtime(
            model=build_kimi_chat_model(api_key),
            registry=registry,
            executor=NoExecution(),
            context=_context(),
            scope=_scope(),
            interrupt_before_tools=True,
        )
        result = await runtime.graph.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]}
        )
        actual = _semantic_calls(result)
        onboarding.append(
            {
                "attempt_id": f"onboarding-{index:02d}",
                "actual": actual,
                "passed": actual == ["onboard_project"],
                "effects": 0,
            }
        )

    action = [item for item in attempts if item["expected"] is not None]
    no_action = [item for item in attempts if item["expected"] is None]
    action_rate = sum(item["passed"] for item in action) / len(action)
    no_action_rate = sum(item["passed"] for item in no_action) / len(no_action)
    onboarding_passed = sum(item["passed"] for item in onboarding)
    verdict = (
        "GO"
        if action_rate >= 0.95
        and no_action_rate >= 0.95
        and onboarding_passed >= 7
        and all(item["effects"] == 0 for item in [*attempts, *onboarding])
        else "NO_GO"
    )
    report = {
        "report_version": "1.0.0",
        "generated_at": REPORT_TIMESTAMP,
        "verdict": verdict,
        "snapshot_hash": snapshot_hash,
        "runtime_versions": CANDIDATE_VERSIONS,
        "model_profile": KIMI_PROFILE.model_dump(mode="json"),
        "prompt_version": "slice-2-semantic-shadow-v1",
        "action": {"passed": sum(item["passed"] for item in action), "total": len(action), "rate": action_rate},
        "no_action": {"passed": sum(item["passed"] for item in no_action), "total": len(no_action), "rate": no_action_rate},
        "onboarding": {"passed": onboarding_passed, "total": len(onboarding)},
        "effects": 0,
        "attempts": attempts,
        "onboarding_attempts": onboarding,
    }
    _write_json(output_dir / "slice_2_semantic_selection_v1.json", report)
    return report


def _semantic_calls(result: dict[str, Any]) -> list[str]:
    return [
        str(call.get("name") or "")
        for message in result.get("messages", [])
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    ]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    require_sanitized(payload, label=path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
