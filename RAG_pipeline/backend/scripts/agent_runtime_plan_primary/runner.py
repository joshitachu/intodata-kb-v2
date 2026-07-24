"""Authenticated selection-only evaluator for the Slice 4 planning canary."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from scripts.agent_runtime_eval.corpus import load_corpus
from scripts.agent_runtime_spike.provider import build_kimi_chat_model
from src.harness.capability_runtime import CapabilityExecutionContext
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.planning_runtime import build_planning_primary_runtime
from src.harness.registry import base_capability_registry

REPORT_ROOT = Path(__file__).parents[1] / "eval_reports" / "agent_runtime_v1"
_MAX_TEXT = 4_000
_MAX_ARGS = 2_000


def _safe(value: Any, limit: int = _MAX_ARGS) -> Any:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return json.loads(text[:limit]) if len(text) <= limit else {"truncated": True}


def _detail(state: dict[str, Any]) -> dict[str, Any]:
    text, calls, finish = "", [], None
    for message in state.get("messages", []):
        if not isinstance(message, AIMessage):
            continue
        if isinstance(message.content, str) and message.content.strip():
            text = message.content[:_MAX_TEXT]
        for call in message.tool_calls:
            calls.append({"name": str(call.get("name") or ""), "arguments": _safe(dict(call.get("args") or {}))})
        metadata = message.response_metadata or {}
        finish = metadata.get("finish_reason") or metadata.get("stop_reason") or finish
    return {"assistant_text": text, "tool_calls": calls, "finish_reason": str(finish) if finish else None}


async def run_authenticated(output_dir: Path = REPORT_ROOT, *, trajectories: int = 50) -> dict[str, Any]:
    """Call Kimi once per held-out trajectory, interrupting before all tools."""
    from src.config import settings
    api_key = str(settings.kimi_api_key)
    if not api_key:
        # Evaluators run from the backend directory, while local Compose setup
        # keeps shared credentials at repository root. Parse it; never source it.
        from dotenv import dotenv_values

        api_key = str(dotenv_values(Path(__file__).parents[4] / ".env").get("KIMI_API_KEY") or "")
    if not api_key:
        raise RuntimeError("KIMI_API_KEY is required for authenticated evaluation")
    scenarios = sorted(load_corpus(), key=lambda item: item.scenario_id)[:trajectories]
    if len(scenarios) != trajectories:
        raise RuntimeError(f"Corpus has only {len(scenarios)} scenarios, not {trajectories}")
    checkpoint_path = output_dir / "slice_4_plan_primary_checkpoint.json"
    attempts: list[dict[str, Any]] = []
    if checkpoint_path.exists():
        previous = json.loads(checkpoint_path.read_text())
        saved = previous.get("attempts") if isinstance(previous, dict) else None
        if isinstance(saved, list):
            attempts = saved[:trajectories]
    registry = base_capability_registry()
    completed = {str(item.get("attempt_id")) for item in attempts}
    for scenario in scenarios:
        if scenario.scenario_id in completed:
            continue
        runtime = build_planning_primary_runtime(
            model=build_kimi_chat_model(api_key), registry=registry,
            executor=HarnessCapabilityExecutor(registry),
            context=CapabilityExecutionContext(user_id=1, conversation_id=1, project_scope=[], permissions=set(), current_message_id=1, conversation_state_version=1, conversation_turn_claim_id=1, conversation_turn_claim_token="evaluation"),
            plan_context=None, interrupt_before_tools=True,
        )
        # Selection-only is the safety boundary: no handler is reached.
        state = await runtime.graph.ainvoke({"messages": [*scenario.conversation_seed, {"role": "user", "content": scenario.turns[0].user_message}]})
        detail = _detail(state)
        selected = [call["name"] for call in detail["tool_calls"]]
        projected = "project_objective" in selected
        attempts.append({"attempt_id": scenario.scenario_id, "family": scenario.family, "variant": scenario.variant, "user_turn": scenario.turns[0].user_message[:1000], **detail, "project_objective_selected": projected, "effects": 0, "validation": {"selection_only": True, "domain_executor_calls": 0}, "review_rationale": "Review assistant text, selected tools, and safe arguments against the authored scenario objective."})
        checkpoint = {"generated_at": datetime.now(timezone.utc).isoformat(), "attempts": attempts}
        output_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False) + "\n")
    passed = sum(item["project_objective_selected"] for item in attempts)
    report = {"report_version": "1.0.0", "generated_at": datetime.now(timezone.utc).isoformat(), "mode": "authenticated_selection_only", "trajectories": trajectories, "passed": passed, "threshold": 45, "point_rate": passed / trajectories, "verdict": "GO" if passed >= 45 and all(item["effects"] == 0 for item in attempts) else "NO_GO", "effects": 0, "attempts": attempts}
    (output_dir / "slice_4_plan_primary_authenticated.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return report
