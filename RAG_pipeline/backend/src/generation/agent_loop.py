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
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.generation.llm_client import (
    generate_answer_stream,
    generate_answer_with_runtime_tools,
    get_last_usage,
)
from src.generation.usage import UsageStats
from src.runtime.client import execute_runtime_tool, list_runtime_tools
from src.runtime.models import RuntimeToolCall
from src.runtime.store import ProjectScope

logger = logging.getLogger(__name__)

# Injected into the tool-use turn so the model knows these live tools exist, which to
# pick, and that it must resolve execution_ids itself rather than asking the user.
_RUNTIME_TOOL_GUIDE = (
    "You have live runtime tools for job/pipeline execution data. "
    "When the user asks about runtime status, estate inventory, failures, or logs, answer using these tools "
    "— not from documents or memory.\n"
    "\n"
    "HIGH-LEVEL tools (prefer these first):\n"
    "  analyze_execution_failure  — PRIMARY tool for 'why did X fail?' or 'what caused this error?' "
    "→ returns failed component, error code, root-cause candidate, confidence, next checks, "
    "and bounded evidence.\n"
    "  find_similar_failures      — 'has this happened before?' or 'is this recurring?' "
    "→ returns occurrence count, first/last seen, and a bounded list of prior same-signature "
    "incidents in this project. Requires incident_id OR execution_id (not both). "
    "State occurrences in project-scoped language: 'in this project's data' not 'globally'. "
    "Never say 'never happened before' — only 'no prior same-signature failures found in this project'.\n"
    "  get_execution_timeline     — 'what happened when?' → chronological enriched events "
    "with component names and error codes extracted.\n"
    "  get_runtime_incident       — incident follow-up when you already have an incident_id.\n"
    "  list_runtime_sync_status   — freshness/source health. Call this for runtime failure/status "
    "answers so you can say how fresh the data is.\n"
    "  list_runtime_health        — DB-derived unhealthy jobs/sources, failure rates, streaks, "
    "recurrence, and component metric rollups.\n"
    "  list_runtime_unhealthy     — estate health overview with source freshness plus unhealthy "
    "engines, jobs, and plans. Use for 'what is broken?' or 'which jobs/plans are unhealthy?'.\n"
    "  list_runtime_sources       — source inventory and freshness from the central DB.\n"
    "  list_runtime_engines       — engine inventory: status, environment, workspace, version, "
    "and last seen. Use for 'which engines do we have?'.\n"
    "  list_runtime_jobs          — job/task inventory with source, environment, workspace, and "
    "assigned engine.\n"
    "  list_runtime_plans         — orchestration plan inventory with environment, workspace, "
    "steps, and status.\n"
    "\n"
    "LOW-LEVEL lookup tools (use to find execution IDs):\n"
    "  detect_failures    — 'what failed recently?' → gives execution_id for further calls.\n"
    "  list_executions    — 'recent runs of X' or any listing question.\n"
    "  get_execution_details — details of one execution.\n"
    "\n"
    "ID resolution: analyze_execution_failure, get_execution_timeline, and "
    "get_execution_details all REQUIRE an execution_id. If the user refers to 'that job', "
    "'the last run', or a job by name without an id, FIRST call detect_failures or "
    "list_executions to obtain it. Never ask the user for an id you can look up yourself.\n"
    "\n"
    "Answer discipline for failure questions — structure your response as:\n"
    "  Finding: one sentence on what happened.\n"
    "  Freshness: say the relevant last_synced_at/status if list_runtime_sync_status returned it.\n"
    "  Evidence: specific log signals from the tool result (error code, component, etc.).\n"
    "  Likely Cause: a probable explanation. Use 'likely' or 'probable' — not definitive "
    "unless the evidence is conclusive. Base this on root_cause_candidate and confidence.\n"
    "  Next Checks: the recommended next_checks from the tool, or your own if absent.\n"
    "Never dump or quote raw log text into the answer. Evidence and timeline tools return bounded "
    "sanitized snippets or structured signals; use those instead.\n"
    "For inventory or health questions, name the source freshness/status when available, list only "
    "the relevant engines/jobs/plans, and say when a field is unknown instead of inventing it.\n"
    "\n"
    "Source labelling: when an answer mixes live tool results with retrieved documents, "
    "label each claim by its origin — append '(from logs)' for runtime tool results, "
    "and '(from <doc name>)' for document claims. Never present document content as live "
    "log data, or log data as document content."
)


_SUMMARY_MAX_ENTRIES = 10
_HEADLINE_MAX_CHARS = 200

# Tools whose data consists of timestamped events with message text — keep only
# the execution pointer in the summary, never event messages or excerpts.
_EVENT_LIKE_TOOLS = frozenset({"get_execution_logs", "get_execution_timeline"})


def build_runtime_tool_summary(
    progress_events: list[RuntimeProgressEvent],
) -> list[dict] | None:
    """Derive a compact, sanitized reference summary from a turn's runtime tool results.

    Stores only reference fields (execution_id/incident_id/job_name/state/headline) so a
    later turn can resolve "that execution" or "that incident". Never stores evidence
    excerpts, timeline messages, or raw log text. Returns None when the turn ran no
    runtime tools.
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

        if tool_name == "find_similar_failures":
            # Store only the recurrence reference — not the similar_incidents list.
            if isinstance(data, dict):
                recurrence = data.get("recurrence") or {}
                ref_incident_id = data.get("incident_id")
                ref_execution_id = data.get("execution_id")
                occurrence_count = recurrence.get("occurrence_count")
                is_recurring = recurrence.get("is_recurring", False)
                entry: dict = {"tool_name": tool_name}
                if ref_incident_id:
                    entry["incident_id"] = ref_incident_id
                if ref_execution_id:
                    entry["execution_id"] = ref_execution_id
                if occurrence_count is not None:
                    headline = (
                        f"recurring ({occurrence_count} occurrences)"
                        if is_recurring
                        else "first occurrence"
                    )
                    entry["headline"] = headline[:_HEADLINE_MAX_CHARS]
                dedup_key = (tool_name, ref_incident_id or ref_execution_id)
                if dedup_key not in seen and (ref_incident_id or ref_execution_id):
                    seen.add(dedup_key)
                    entries.append(entry)
                    if len(entries) >= _SUMMARY_MAX_ENTRIES:
                        return entries
            continue
        elif tool_name == "get_runtime_incident":
            # data is RuntimeIncidentDetail: {"incident": {...}, "evidence": [...]}
            # Drill into the incident sub-dict; never include evidence.
            records = [_flatten_incident_detail(data)] if isinstance(data, dict) else []
        else:
            records = data if isinstance(data, list) else [data] if isinstance(data, dict) else []

        for record in records:
            if not isinstance(record, dict):
                continue
            execution_id = record.get("execution_id")
            entry: dict = {"tool_name": tool_name}
            if execution_id:
                entry["execution_id"] = execution_id
            if tool_name not in _EVENT_LIKE_TOOLS:
                if record.get("job_name"):
                    entry["job_name"] = record["job_name"]
                if record.get("state"):
                    entry["state"] = record["state"]
                if record.get("incident_id"):
                    entry["incident_id"] = record["incident_id"]
                # Headline: try failure-analysis fields first, then generic error_summary.
                headline = (
                    record.get("primary_error_code")
                    or record.get("error_summary")
                    or record.get("root_cause_candidate")
                )
                if isinstance(headline, str) and headline:
                    entry["headline"] = headline[:_HEADLINE_MAX_CHARS]
            # Skip entries that carry no usable reference.
            if len(entry) == 1 and "execution_id" not in entry and "incident_id" not in entry:
                continue
            dedup_key = (tool_name, execution_id)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            entries.append(entry)
            if len(entries) >= _SUMMARY_MAX_ENTRIES:
                return entries

    return entries or None


def _flatten_incident_detail(data: dict) -> dict:
    """Extract summary reference fields from a RuntimeIncidentDetail dict.

    The detail shape is {"incident": {...}, "evidence": [...]}. Evidence must not
    be forwarded into the summary.
    """
    incident = data.get("incident") or {}
    record: dict = {}
    if incident.get("execution_id"):
        record["execution_id"] = incident["execution_id"]
    if incident.get("incident_id"):
        record["incident_id"] = incident["incident_id"]
    if incident.get("job_name"):
        record["job_name"] = incident["job_name"]
    if incident.get("state"):
        record["state"] = incident["state"]
    if incident.get("root_cause_candidate"):
        record["root_cause_candidate"] = incident["root_cause_candidate"]
    return record


def _render_runtime_memory(summary: list[dict]) -> str:
    """Render a prior-turn runtime_tool_summary as compact reference lines. Reference
    fields only (the summary never holds raw logs); data is still re-fetched via tools."""
    lines: list[str] = []
    for entry in summary:
        parts: list[str] = []
        if entry.get("execution_id"):
            parts.append(f"execution_id={entry['execution_id']}")
        if entry.get("job_name"):
            parts.append(f"job={entry['job_name']}")
        if entry.get("state"):
            parts.append(f"state={entry['state']}")
        if entry.get("headline"):
            parts.append(f"note={entry['headline']}")
        if parts:
            lines.append("- " + ", ".join(parts))
    return "\n".join(lines)


def _with_runtime_guide(
    messages: list[dict], prior_runtime_summary: list[dict] | None = None
) -> list[dict]:
    """Merge the runtime tool-use guidance (and, when present, a compact note of the
    executions this conversation already referenced) into the leading system message
    (or prepend one), so it travels with the prompt regardless of LLM provider."""
    guide = _RUNTIME_TOOL_GUIDE
    if prior_runtime_summary:
        rendered = _render_runtime_memory(prior_runtime_summary)
        if rendered:
            guide = (
                f"{guide}\n\nRecent runtime context from this conversation you may "
                f"reference (re-fetch live data with the tools as needed):\n{rendered}"
            )
    if messages and messages[0].get("role") == "system":
        head = {**messages[0]}
        head["content"] = f"{head.get('content', '')}\n\n{guide}".strip()
        return [head, *messages[1:]]
    return [{"role": "system", "content": guide}, *messages]


@dataclass
class RuntimeProgressEvent:
    """A single tool-call or tool-result step, for SSE/UI progress."""

    event_type: str  # "runtime_tool_call" | "runtime_tool_result"
    payload: dict[str, Any]


@dataclass
class RuntimeAnswerChunk:
    """One streamed piece of the final synthesized answer."""

    text: str


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


async def run_runtime_tool_loop_stream(
    messages: list[dict],
    images: list[str] | None = None,
    *,
    project_scope: ProjectScope = None,
    db: AsyncSession | None = None,
    prior_runtime_summary: list[dict] | None = None,
) -> AsyncGenerator[RuntimeProgressEvent | RuntimeAnswerChunk | RuntimeLoopOutcome, None]:
    """Run a bounded generate -> tool-call -> execute -> continue loop, yielding
    each step live so a streaming caller can show progress as it happens.

    `project_scope` and `db` are threaded into every runtime tool execution so the
    DB-backed `stored` provider can enforce RBAC and read the held store. They are
    keyword-only and supplied by authenticated chat code — never from tool arguments.

    Yields, in order: zero or more `RuntimeProgressEvent`s (a "runtime_tool_call"
    immediately followed by its "runtime_tool_result", per tool call, across
    iterations), then zero or more `RuntimeAnswerChunk`s carrying the streamed
    final answer, and always terminates with exactly one `RuntimeLoopOutcome`.

    Exception: when the very first decision returns plain text with no tool
    calls ever made (`progress` empty), the outcome is yielded directly with
    no `RuntimeAnswerChunk`s — nothing ran that needs a re-synthesis pass.

    A terminal `RuntimeLoopOutcome(final_answer=None, ...)` means the active
    provider chain has no structured tool-call support and no tool was ever
    called, so the caller should fall back to the existing non-runtime answer
    path without claiming live data.
    """
    tools = list_runtime_tools()
    progress: list[RuntimeProgressEvent] = []
    working_messages = _with_runtime_guide(messages, prior_runtime_summary)
    usage: UsageStats | None = None
    max_iterations = max(1, settings.runtime_tool_loop_max_iterations)

    for _ in range(max_iterations):
        decision = await generate_answer_with_runtime_tools(working_messages, tools, images=images)
        usage = _accumulate_usage(usage, decision.usage)

        if decision.tool_calls:
            # Every call_id in the assistant message must get a tool message —
            # OpenAI-compat APIs 400 on any dangling tool_call_id. So the cap
            # answers overflow calls with a synthetic budget error instead of
            # dropping them.
            max_parallel = max(1, settings.runtime_tool_max_parallel_calls)
            executed = decision.tool_calls[:max_parallel]
            overflow = decision.tool_calls[max_parallel:]
            tool_messages: list[dict] = []

            # Sequential on purpose: executions share one AsyncSession, which
            # cannot run concurrent queries.
            for request in executed:
                call = RuntimeToolCall(tool_name=request.tool_name, arguments=request.arguments)
                call_event = RuntimeProgressEvent("runtime_tool_call", call.model_dump())
                progress.append(call_event)
                yield call_event

                result = await execute_runtime_tool(call, project_scope=project_scope, db=db)
                result_payload = result.model_dump(mode="json")
                result_event = RuntimeProgressEvent("runtime_tool_result", result_payload)
                progress.append(result_event)
                yield result_event

                tool_messages.append({
                    "role": "tool",
                    "tool_call_id": request.call_id,
                    "content": json.dumps(result_payload, default=str),
                })

            if overflow:
                logger.warning(
                    "Runtime tool loop: %d parallel tool calls exceed the cap of %d; "
                    "answering overflow calls (%s) with tool_call_budget_exceeded",
                    len(decision.tool_calls), max_parallel,
                    ", ".join(r.tool_name for r in overflow),
                )
            for request in overflow:
                tool_messages.append({
                    "role": "tool",
                    "tool_call_id": request.call_id,
                    "content": json.dumps({
                        "error": "tool_call_budget_exceeded",
                        "detail": (
                            f"Only the first {max_parallel} tool calls per turn are "
                            "executed. Re-issue this call in your next turn if still needed."
                        ),
                    }),
                })

            assistant_message = decision.assistant_message or {"role": "assistant", "content": ""}
            working_messages = working_messages + [assistant_message, *tool_messages]
            continue

        if decision.text is not None:
            if not progress:
                # No tool ever ran: nothing to re-synthesize, deliver as-is.
                yield RuntimeLoopOutcome(
                    final_answer=decision.text,
                    used_runtime_data=False,
                    progress_events=[],
                    usage=usage,
                )
                return
            break  # Tools ran; fall through to the shared streamed synthesis tail.

        if not progress:
            # No tool call, no text, no prior tool results: the provider
            # chain has no structured tool support for this turn.
            yield RuntimeLoopOutcome(final_answer=None, used_runtime_data=False, usage=usage)
            return

        break  # Have tool results but the provider gave neither text nor another call.

    # Iteration cap reached, decision loop ended with text after >=1 tool
    # call, or stalled with tool data but no final text: synthesize the
    # final answer from everything gathered so far, streamed token-by-token.
    # Single shared tail for all three cases -- not duplicated per exit path.
    final_chunks: list[str] = []
    async for token in generate_answer_stream(working_messages, images=images):
        final_chunks.append(token)
        yield RuntimeAnswerChunk(token)
    usage = _accumulate_usage(usage, get_last_usage())
    yield RuntimeLoopOutcome(
        final_answer="".join(final_chunks),
        used_runtime_data=bool(progress),
        progress_events=progress,
        usage=usage,
    )


async def run_runtime_tool_loop(
    messages: list[dict],
    images: list[str] | None = None,
    *,
    project_scope: ProjectScope = None,
    db: AsyncSession | None = None,
    prior_runtime_summary: list[dict] | None = None,
) -> RuntimeLoopOutcome:
    """Non-streaming convenience wrapper over `run_runtime_tool_loop_stream` for
    callers that only need the final outcome (the REST endpoint).

    Drains the same generator the streaming caller consumes live, so tool
    execution logic is never duplicated between the two.
    """
    outcome: RuntimeLoopOutcome | None = None
    async for event in run_runtime_tool_loop_stream(
        messages,
        images=images,
        project_scope=project_scope,
        db=db,
        prior_runtime_summary=prior_runtime_summary,
    ):
        if isinstance(event, RuntimeLoopOutcome):
            outcome = event
    assert outcome is not None, (
        "run_runtime_tool_loop_stream must always terminate with a RuntimeLoopOutcome"
    )
    return outcome
