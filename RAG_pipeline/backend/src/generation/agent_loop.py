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

from src.config import settings
from src.generation.llm_client import generate_answer, generate_answer_with_runtime_tools, get_last_usage
from src.generation.usage import UsageStats
from src.runtime.client import execute_runtime_tool, list_runtime_tools
from src.runtime.models import RuntimeToolCall

logger = logging.getLogger(__name__)


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
) -> RuntimeLoopOutcome:
    """Run a bounded generate -> tool-call -> execute -> continue loop.

    Returns final_answer=None when the active provider chain has no
    structured tool-call support and no tool was ever called, so the caller
    can fall back to the existing non-runtime answer path without claiming
    live data.
    """
    tools = list_runtime_tools()
    progress: list[RuntimeProgressEvent] = []
    working_messages = list(messages)
    usage: UsageStats | None = None
    max_iterations = max(1, settings.runtime_tool_loop_max_iterations)

    for _ in range(max_iterations):
        decision = await generate_answer_with_runtime_tools(working_messages, tools, images=images)
        usage = _accumulate_usage(usage, decision.usage)

        if decision.tool_call is not None:
            call = RuntimeToolCall(tool_name=decision.tool_call.tool_name, arguments=decision.tool_call.arguments)
            progress.append(RuntimeProgressEvent("runtime_tool_call", call.model_dump()))

            result = await execute_runtime_tool(call)
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
