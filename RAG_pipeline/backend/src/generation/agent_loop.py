"""Bounded backend tool-use loop for runtime queries (EP-004 slice 3).

Lets a conversational RAG turn call the generic Slice 2 runtime tools when the
active LLM provider supports structured tool calls. Provider mechanics (tool
schemas, SDK response shapes, tool_call ids) stay inside llm_client.py; this
module only sees the normalized ToolAwareLLMResponse / RuntimeToolResult
contracts, so it never has to know about OpenAI wire formats or any future
provider's specifics.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.generation.llm_client import generate_answer, generate_answer_with_runtime_tools, get_last_usage
from src.generation.usage import UsageStats
from src.runtime.client import execute_runtime_tool, list_runtime_tools
from src.runtime.models import RuntimeToolCall
from src.runtime.store import ProjectScope

logger = logging.getLogger(__name__)

# Injected into the tool-use turn so the model knows these live tools exist, which to
# pick, and that it must resolve execution_ids itself rather than asking the user.
_RUNTIME_TOOL_GUIDE = (
    "You have live runtime tools for job/pipeline execution data: list_executions, "
    "detect_failures, get_execution_details, get_execution_logs. When the user asks "
    "about runtime status, recent runs, failures, or execution logs, answer using these "
    "tools — not from documents or memory.\n"
    "Tool selection: 'what failed' -> detect_failures; 'recent runs' / 'last run of X' / "
    "'list executions' -> list_executions; details of one run -> get_execution_details; "
    "logs for a run -> get_execution_logs.\n"
    "get_execution_details and get_execution_logs REQUIRE an execution_id. If the user "
    "refers to 'that execution', 'the failed job', or a job by name without giving an id, "
    "FIRST call list_executions or detect_failures to obtain the execution_id, THEN call "
    "the details/logs tool. Never ask the user for an execution_id you can look up yourself."
)


_SUMMARY_MAX_ENTRIES = 10
_HEADLINE_MAX_CHARS = 200


def build_runtime_tool_summary(
    progress_events: list[RuntimeProgressEvent],
) -> list[dict] | None:
    """Derive a compact, sanitized reference summary from a turn's runtime tool results.

    Stores only reference fields (tool_name + execution_id/job_name/state/headline) so a
    later turn can resolve "that execution". Never stores raw `data`, log message text,
    or other payload content. Input is already redacted/size-bounded by
    runtime/client.py. Returns None when the turn ran no runtime tools.
    """
    entries: list[dict] = []
    seen: set[tuple[str, str | None]] = set()

    for event in progress_events:
        if event.event_type != "runtime_tool_result":
            continue
        payload = event.payload
        tool_name = payload.get("tool_name")
        if not tool_name:
            continue
        data = payload.get("data")
        records = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
        for record in records:
            if not isinstance(record, dict):
                continue
            execution_id = record.get("execution_id")
            entry: dict = {"tool_name": tool_name}
            if execution_id:
                entry["execution_id"] = execution_id
            # Logs carry message text per line — keep only the execution pointer.
            if tool_name != "get_execution_logs":
                if record.get("job_name"):
                    entry["job_name"] = record["job_name"]
                if record.get("state"):
                    entry["state"] = record["state"]
                headline = record.get("error_summary")
                if isinstance(headline, str) and headline:
                    entry["headline"] = headline[:_HEADLINE_MAX_CHARS]
            # Skip entries that carry no usable reference.
            if len(entry) == 1 and "execution_id" not in entry:
                continue
            dedup_key = (tool_name, execution_id)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            entries.append(entry)
            if len(entries) >= _SUMMARY_MAX_ENTRIES:
                return entries

    return entries or None


def _with_runtime_guide(messages: list[dict]) -> list[dict]:
    """Merge the runtime tool-use guidance into the leading system message (or prepend
    one), so it travels with the prompt regardless of LLM provider."""
    if messages and messages[0].get("role") == "system":
        head = {**messages[0]}
        head["content"] = f"{head.get('content', '')}\n\n{_RUNTIME_TOOL_GUIDE}".strip()
        return [head, *messages[1:]]
    return [{"role": "system", "content": _RUNTIME_TOOL_GUIDE}, *messages]


@dataclass
class RuntimeProgressEvent:
    """A single tool-call or tool-result step, for SSE/UI progress."""

    event_type: str  # "runtime_tool_call" | "runtime_tool_result"
    payload: dict[str, Any]


@dataclass
class RuntimeLoopOutcome:
    final_answer: str | None
    """None when no provider in the chain offered structured tool support and
    no tool was ever called — the caller should fall back to generate_answer()."""
    used_runtime_data: bool
    progress_events: list[RuntimeProgressEvent] = field(default_factory=list)
    usage: UsageStats | None = None


def _accumulate_usage(total: UsageStats | None, usage: UsageStats | None) -> UsageStats | None:
    """Sum token/latency counts across loop iterations. Keeps the most recent
    provider/model label since cost/pricing is looked up per-call already."""
    if usage is None:
        return total
    if total is None:
        return UsageStats(
            provider=usage.provider,
            model=usage.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=usage.cost_usd,
            latency_ms=usage.latency_ms,
        )
    total.provider = usage.provider
    total.model = usage.model
    total.input_tokens += usage.input_tokens
    total.output_tokens += usage.output_tokens
    total.cost_usd += usage.cost_usd
    total.latency_ms += usage.latency_ms
    return total


async def run_runtime_tool_loop(
    messages: list[dict],
    images: list[str] | None = None,
    *,
    project_scope: ProjectScope = None,
    db: AsyncSession | None = None,
) -> RuntimeLoopOutcome:
    """Run a bounded generate -> tool-call -> execute -> continue loop.

    `project_scope` and `db` are threaded into every runtime tool execution so the
    DB-backed `stored` provider can enforce RBAC and read the held store. They are
    keyword-only and supplied by authenticated chat code — never from tool arguments.

    Returns final_answer=None when the active provider chain has no
    structured tool-call support and no tool was ever called, so the caller
    can fall back to the existing non-runtime answer path without claiming
    live data.
    """
    tools = list_runtime_tools()
    progress: list[RuntimeProgressEvent] = []
    working_messages = _with_runtime_guide(messages)
    usage: UsageStats | None = None
    max_iterations = max(1, settings.runtime_tool_loop_max_iterations)

    for _ in range(max_iterations):
        decision = await generate_answer_with_runtime_tools(working_messages, tools, images=images)
        usage = _accumulate_usage(usage, decision.usage)

        if decision.tool_call is not None:
            call = RuntimeToolCall(tool_name=decision.tool_call.tool_name, arguments=decision.tool_call.arguments)
            progress.append(RuntimeProgressEvent("runtime_tool_call", call.model_dump()))

            result = await execute_runtime_tool(call, project_scope=project_scope, db=db)
            result_payload = result.model_dump(mode="json")
            progress.append(RuntimeProgressEvent("runtime_tool_result", result_payload))

            assistant_message = decision.assistant_message or {"role": "assistant", "content": ""}
            working_messages = working_messages + [
                assistant_message,
                {
                    "role": "tool",
                    "tool_call_id": decision.tool_call.call_id,
                    "content": json.dumps(result_payload, default=str),
                },
            ]
            continue

        if decision.text is not None:
            return RuntimeLoopOutcome(
                final_answer=decision.text,
                used_runtime_data=bool(progress),
                progress_events=progress,
                usage=usage,
            )

        if not progress:
            # No tool call, no text, no prior tool results: the provider
            # chain has no structured tool support for this turn.
            return RuntimeLoopOutcome(final_answer=None, used_runtime_data=False, usage=usage)

        break  # Have tool results but the provider gave neither text nor another call.

    # Iteration cap reached, or stalled with tool data but no final text:
    # synthesize a plain-text answer from everything gathered so far.
    final_text = await generate_answer(working_messages, images=images)
    usage = _accumulate_usage(usage, get_last_usage())
    return RuntimeLoopOutcome(
        final_answer=final_text,
        used_runtime_data=bool(progress),
        progress_events=progress,
        usage=usage,
    )
