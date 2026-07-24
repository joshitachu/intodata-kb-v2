"""Live read-only primary runtime for the V2 conversation cohort (Slice 3).

This assembles one standard ``create_agent`` graph that reuses the Slice 2
semantic tools, gateway, and middleware in ``READ_ONLY_PRIMARY`` mode. Reads
execute for real through the governed executor; consequential capabilities are
filtered out of the model-facing tool set so they can neither be selected nor
executed. The model writes one natural answer plus a small machine-readable
grounding block, which the grounding validator checks before persistence. A
bounded correction loop and a concise, non-fabricating safe fallback keep the
turn honest. This module never modifies the legacy ``BoundedAgentLoop`` or the
fact-ID renderer, and constructs (not modifies) the ``AgentLoopResult`` type.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.runnables import Runnable

from src.harness.agent_loop import (
    AgentBoundaryAction,
    AgentLoopResult,
    AgentStopReason,
    AgentUsage,
    response_fingerprint,
)
from src.harness.capability_runtime import (
    CapabilityExecutionContext,
    CapabilityObservation,
    CapabilityObservationStatus,
)
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.interaction_protocol import (
    INTERACTION_PROTOCOL_PROMPT,
    TURN_ASSESSMENT_TOOL_NAME,
)
from src.harness.grounding import (
    AnswerStatus,
    GroundedAnswer,
    GroundingClaim,
    GroundingOutcome,
    GroundingValidator,
    GroundingVerdict,
)
from src.harness.manifest import ManifestScope
from src.harness.models import DOCUMENT_SEARCH_CAPABILITY
from src.harness.registry import CapabilityRegistry
from src.harness.semantic_tools import (
    GatewayMode,
    GovernanceClass,
    SemanticEventLedger,
    SemanticToolGateway,
    SemanticToolMiddleware,
    SemanticToolRecord,
    SemanticToolSnapshot,
    ToolEnvelope,
    build_semantic_structured_tools,
    build_semantic_tool_snapshot,
    serialize_tool_envelope,
)
from src.harness.run_budgets import (
    AgentBudgetStop,
    BudgetStopReason,
    LayeredBudgetController,
    LayeredModelBudgetMiddleware,
)
from src.harness.trusted_answer import TrustedAnswerSink
from src.schemas import DocumentSource

logger = logging.getLogger(__name__)

READONLY_RUNTIME_RELEASE = "slice-3-readonly-primary-v1"
READONLY_PROMPT_VERSION = "slice-3-readonly-primary-v4"
READONLY_MODEL_PROFILE = "kimi/kimi-k2.5"

_KIMI_BASE_URL = "https://api.moonshot.ai/v1"

_GROUNDING_BLOCK_RE = re.compile(
    r"```(?:nexus-grounding|json)\s*(?:nexus-grounding\s*)?"
    r"(?P<body>\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)

READ_ONLY_PRIMARY_SYSTEM_PROMPT = INTERACTION_PROTOCOL_PROMPT + """

You are Nexus answering a read-only conversation turn.

You may answer directly, or select read-only tools by meaning to gather evidence
first. You may call several read tools in sequence, consuming each typed
observation before the next. A normal assistant message ends the turn; there is
no response tool.

Answer greetings, capability questions, and how-to guidance directly, with no
tool. When the user restricts scope, forbids acting, or only wants an
explanation, do not call any tool. You cannot perform or propose consequential
actions (creating, saving, generating, onboarding, cancelling, updating); if
asked, explain the read-only boundary instead.

Ground every material factual claim in evidence returned by the tools you called
this turn. Never state unsupported facts as Nexus truth from memory. If a search
returns nothing, a source is unavailable, scope is denied, a lookup fails, or the
evidence conflicts or only partly answers, say so honestly. Respect the format
the user asked for. Never reveal credentials, private reasoning, raw exceptions,
policy internals, SQL, capability identifiers, semantic tool names, evidence IDs,
or raw tool payloads. Evidence IDs belong only inside the final grounding block;
use normal document titles or plain-language source labels in visible prose.

An unqualified request such as "summarize" or "give me a summary" only means
this conversation when there is a prior exchange in it to recap. If this is the
first user turn, or no exchange has yet established a subject, treat the request
as being about project or document content, not this conversation.

A follow-up such as "list those" or "tell me more" that refers to something a
prior turn said "the source" or "the documentation" describes continues the same
documentation search; it is not a request to recap this conversation and not a
request for live runtime state, executions, or logs unless the user's own words
ask about runtime, operational, or log evidence.

Use documentation search for every request about project documentation content,
including summaries, available documentation, job or component names, and facts
inside or across documents. Do not substitute active workflows, runtime evidence,
logs, generated-document inventories, or direct file reads for indexed RAG. For a
concise request, begin with at most 8 search results. If those results answer the
question, stop gathering evidence and answer.

Use documentation search for exhaustive inventories and complex questions across
the selected project's indexed documentation. Make the search query preserve the
user's requested entities and completeness requirement, and use a larger bounded
top_k when broader recall is needed. Do not bypass indexed RAG by listing or
opening generated Markdown files.

If the conversation explicitly says there are multiple possible targets and the
user replies only with an unresolved pronoun such as "that one", ask one bounded
clarifying question. An authorized project in server context does not resolve an
ambiguity that the conversation itself left open.

Never repeat an identical read call. If middleware reports a repeated call or
excessive read fan-out, use the evidence already returned and answer directly.

Treat a question about what a named or implied job sends, receives, or does as
a documentation-content question. Use runtime evidence or logs only when the
user explicitly asks about executions, current status, failures, health, or log
content. The word "job" alone does not make a request operational.

End your reply with one fenced block describing the grounding of your answer:

```nexus-grounding
{"answer_status": "grounded|partial|no_evidence|source_unavailable|scope_denied|tool_failed|conflicting|denied",
 "citations": [{"claim_key": "short label", "evidence_refs": ["<id from a tool result>"]}],
 "disclosures": ["<limitation code>"]}
```

Cite only evidence ids that appeared in a tool result. Use an empty citations
list only when no factual claim requires evidence (for example greetings,
capability, or guidance answers). The prose above the block is your visible
answer; keep the block out of the prose.
"""

_SAFE_FALLBACK_TEXT = (
    "I ran into a problem completing this request and could not produce a "
    "grounded answer. Please try again, and rephrase if the issue continues."
)


def grounding_failure_answer() -> GroundedAnswer:
    """Return the one permitted non-budget response for invalid model grounding."""

    return GroundedAnswer(
        content=_SAFE_FALLBACK_TEXT,
        citations=[],
        disclosures=[],
        answer_status=AnswerStatus.TOOL_FAILED,
    )


@dataclass(frozen=True, slots=True)
class ReadOnlyPrimaryRuntime:
    """An assembled read-only ``create_agent`` graph and its boundaries."""

    graph: Runnable
    snapshot: SemanticToolSnapshot
    gateway: SemanticToolGateway
    middleware: SemanticToolMiddleware
    ledger: SemanticEventLedger
    read_only_tools: tuple[SemanticToolRecord, ...]
    filtered_consequential: tuple[str, ...]


@dataclass(slots=True)
class ReadOnlyTurnResult:
    """The outcome of one read-only turn: a standard result plus grounding."""

    result: AgentLoopResult
    grounded: GroundedAnswer
    grounding_verdict: GroundingVerdict
    correction_rounds: int
    used_fallback: bool
    executor_read_calls: int
    consequential_executions: int
    allowed_evidence: tuple[str, ...] = field(default_factory=tuple)
    document_sources: list[DocumentSource] = field(default_factory=list)


MODEL_FACING_READ_EXCLUSIONS = frozenset({DOCUMENT_SEARCH_CAPABILITY.id})


def readonly_manifest_partition(
    snapshot: SemanticToolSnapshot,
) -> tuple[list[SemanticToolRecord], list[str]]:
    """Split a snapshot into model-facing reads and filtered consequential ids.

    ``search_documentation`` is registered but deliberately excluded from the
    model-facing set: it overlaps with ``answer_from_documentation`` (the
    deterministic, correctly-cited path) closely enough that the model
    unreliably picked the wrong one. Kept in the registry, not offered here.
    """

    reads = [
        record
        for record in snapshot.tools
        if record.governance_class == GovernanceClass.READ
        and record.canonical_id not in MODEL_FACING_READ_EXCLUSIONS
    ]
    consequential = [
        record.canonical_id
        for record in snapshot.tools
        if record.governance_class == GovernanceClass.CONSEQUENTIAL
    ]
    return reads, consequential


def build_readonly_primary_runtime(
    *,
    model: BaseChatModel,
    registry: CapabilityRegistry,
    executor: HarnessCapabilityExecutor,
    context: CapabilityExecutionContext,
    scope: ManifestScope | None = None,
    system_prompt_suffix: str = "",
    interrupt_before_tools: bool = False,
    budget_controller: LayeredBudgetController | None = None,
) -> ReadOnlyPrimaryRuntime:
    """Assemble one read-only primary graph; consequential tools are removed."""

    snapshot = build_semantic_tool_snapshot(
        registry,
        scope=scope
        or ManifestScope(
            permissions=context.permissions,
            project_ids=context.project_scope,
            include_bounded_loop_only=True,
            allowed_capability_ids=context.allowed_capability_ids,
            denied_capability_ids=context.denied_capability_ids,
        ),
    )
    reads, consequential = readonly_manifest_partition(snapshot)
    ledger = SemanticEventLedger()
    gateway = SemanticToolGateway(
        snapshot=snapshot,
        executor=executor,
        context=context,
        ledger=ledger,
        mode=GatewayMode.READ_ONLY_PRIMARY,
        registry=registry,
        budget_controller=budget_controller,
    )
    middleware = SemanticToolMiddleware(
        snapshot,
        ledger,
        budget_controller=budget_controller,
    )
    # Only read-only tools reach the model; consequential handlers cannot run.
    read_snapshot = snapshot.model_copy(update={"tools": tuple(reads)})
    tools = build_semantic_structured_tools(read_snapshot, gateway)
    prompt = READ_ONLY_PRIMARY_SYSTEM_PROMPT
    if system_prompt_suffix.strip():
        prompt = f"{prompt}\nAdditional turn context:\n{system_prompt_suffix.strip()}"
    middlewares: list[Any] = [middleware]
    if budget_controller is not None:
        middlewares.append(LayeredModelBudgetMiddleware(
            budget_controller,
            model_name=str(getattr(model, "model_name", getattr(model, "model", ""))),
        ))
    graph = create_agent(
        model,
        tools,
        system_prompt=prompt,
        middleware=middlewares,
        interrupt_before=["tools"] if interrupt_before_tools else None,
        name="nexus_slice_3_readonly_primary",
    )
    return ReadOnlyPrimaryRuntime(
        graph=graph,
        snapshot=snapshot,
        gateway=gateway,
        middleware=middleware,
        ledger=ledger,
        read_only_tools=tuple(reads),
        filtered_consequential=tuple(consequential),
    )


def build_readonly_primary_chat_model(settings) -> BaseChatModel:  # noqa: ANN001
    """Build the configured provider chat model for the read-only graph.

    Additive and default-off: only invoked when a turn is routed to V2. Uses the
    already-pinned ``langchain-openai`` client against the OpenAI-compatible
    provider selected by ``rag_default_model``. Credentials are never logged.
    """

    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr

    model_id = settings.rag_default_model
    provider = model_id.split("/", 1)[0] if "/" in model_id else model_id
    if provider == "kimi":
        api_key = settings.kimi_api_key
        if not str(api_key).strip():
            raise RuntimeError("kimi_api_key is required for the V2 read-only runtime")
        return ChatOpenAI(
            model=settings.kimi_llm_model,
            api_key=SecretStr(str(api_key)),
            base_url=_KIMI_BASE_URL,
            temperature=settings.kimi_temperature,
            timeout=settings.kimi_timeout,
            max_retries=settings.agent_runtime_v2_provider_max_retries,
            extra_body={"thinking": {"type": "disabled"}, "max_tokens": 2048},
            streaming=True,
            stream_usage=True,
        )
    if provider in {"openai", "deepseek"}:
        api_key = (
            settings.openai_api_key if provider == "openai" else settings.deepseek_api_key
        )
        base_url = (
            settings.openai_base_url
            if provider == "openai"
            else settings.deepseek_base_url
        )
        if not str(api_key).strip():
            raise RuntimeError(f"{provider}_api_key is required for the V2 runtime")
        return ChatOpenAI(
            model=model_id.split("/", 1)[1] if "/" in model_id else model_id,
            api_key=SecretStr(str(api_key)),
            base_url=base_url,
            temperature=settings.cloud_llm_temperature,
            timeout=(
                settings.openai_timeout if provider == "openai" else settings.deepseek_timeout
            ),
            max_retries=settings.agent_runtime_v2_provider_max_retries,
            streaming=True,
            stream_usage=True,
        )
    raise RuntimeError(
        f"provider {provider!r} is not supported by the V2 read-only runtime"
    )


async def run_readonly_primary_turn(
    *,
    graph: Runnable,
    ledger: SemanticEventLedger,
    snapshot: SemanticToolSnapshot,
    user_message: str,
    conversation_seed: list[dict[str, str]] | None = None,
    state_version: str = "",
    prior_response_fingerprints: set[str] | None = None,
    max_corrections: int = 1,
    event_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    emit_answer_deltas: bool = True,
    budget_controller: LayeredBudgetController | None = None,
    trusted_answer_sink: TrustedAnswerSink | None = None,
) -> ReadOnlyTurnResult:
    """Run the read-only graph with a bounded grounding-correction loop."""

    validator = GroundingValidator()
    prior = prior_response_fingerprints or set()
    messages: list[Any] = list(conversation_seed or [])
    messages.append({"role": "user", "content": user_message})

    usage = AgentUsage()
    envelopes: list[ToolEnvelope] = []
    ledger_len_before = len(ledger.events)
    grounded: GroundedAnswer | None = None
    outcome_verdict = GroundingVerdict.UNSAFE
    correction_rounds = 0
    outcome_reason_code = "not_evaluated"

    budget_stop: BudgetStopReason | None = None
    for attempt in range(max_corrections + 1):
        try:
            result_state = await _invoke_graph(
                graph,
                {"messages": messages},
                event_callback=event_callback,
                trusted_answer_sink=trusted_answer_sink,
            )
        except AgentBudgetStop as exc:
            budget_stop = exc.reason
            outcome_verdict = GroundingVerdict.UNSAFE
            break
        new_messages = result_state.get("messages", [])
        messages = list(new_messages)
        usage = _accumulate_usage(usage, new_messages)
        envelopes = _collect_envelopes(new_messages)
        if trusted_answer_sink is not None and trusted_answer_sink.answer is not None:
            # The deterministic RAG answer capability already produced a fully
            # grounded answer with real sources; adopt it directly and skip the
            # self-reported grounding block and correction loop entirely.
            grounded = GroundedAnswer(
                content=trusted_answer_sink.answer.content,
                citations=[],
                disclosures=[],
                answer_status=(
                    AnswerStatus.GROUNDED
                    if trusted_answer_sink.answer.document_sources
                    else AnswerStatus.NO_EVIDENCE
                ),
            )
            outcome_verdict = GroundingVerdict.VALID
            outcome_reason_code = "trusted_deterministic_answer"
            break
        allowed = validator.allowed_evidence(envelopes)
        requirement = validator.required_state(envelopes)
        final_text = _final_assistant_text(new_messages)
        grounded = _parse_grounded_answer(final_text, requirement=requirement)
        grounded = grounded.model_copy(
            update={"content": _humanize_visible_content(grounded.content, envelopes)}
        )
        if not grounded.content.strip():
            # An empty terminal message is not an answer; force a rewrite.
            outcome = GroundingOutcome(
                verdict=GroundingVerdict.CORRECTABLE,
                reason_code="empty_answer",
                correction_hint="Write a complete answer with the grounding block.",
            )
        else:
            outcome = validator.validate(
                grounded, allowed=allowed, requirement=requirement
            )
        outcome_verdict = outcome.verdict
        outcome_reason_code = outcome.reason_code
        if outcome.verdict is GroundingVerdict.VALID:
            break
        if outcome.verdict is GroundingVerdict.UNSAFE or attempt >= max_corrections:
            break
        correction_rounds += 1
        messages.append(
            {
                "role": "user",
                "content": (
                    "That answer was not accepted. "
                    + (outcome.correction_hint or "Revise it to be honestly grounded.")
                    + " Re-send the full answer and the grounding block."
                ),
            }
        )

    read_observations = _collect_observations(ledger, ledger_len_before)
    executor_read_calls = len(read_observations)
    consequential_executions = _count_consequential_executions(
        ledger, snapshot, ledger_len_before
    )
    allowed_refs = tuple(sorted(validator.allowed_evidence(envelopes)))

    if (
        budget_stop is None
        and budget_controller is not None
        and budget_controller.stop_reason is not None
    ):
        budget_stop = budget_controller.stop_reason

    used_fallback = outcome_verdict is not GroundingVerdict.VALID
    unsafe_content = (
        grounded is not None
        and outcome_verdict is GroundingVerdict.UNSAFE
        and outcome_reason_code == "internal_output_detected"
    )
    if used_fallback or grounded is None:
        logger.warning(
            "Grounding fallback reason_code=%s correction_rounds=%s read_calls=%s",
            outcome_reason_code,
            correction_rounds,
            executor_read_calls,
        )
        if budget_stop is not None:
            grounded = GroundedAnswer(
                content=_budget_fallback(budget_stop),
                citations=[],
                disclosures=[],
                answer_status=AnswerStatus.TOOL_FAILED,
            )
        elif grounded is None or not grounded.content.strip() or unsafe_content:
            # Nothing safe to show: no answer was ever produced, or the model's
            # text itself leaked internal ids/tool names/raw payloads. This is
            # the one case still worth discarding outright.
            grounded = grounding_failure_answer()
        else:
            # The model wrote a real answer but failed the self-reported
            # citation/disclosure contract (missing/invalid citations, status
            # mismatch, etc.). Show the answer as written rather than
            # discarding it — citations/disclosures are dropped since they
            # were not validated, not because the answer itself is unsafe.
            grounded = grounded.model_copy(
                update={"citations": [], "disclosures": []}
            )

    if budget_controller is not None:
        usage = usage.model_copy(
            update={"model_cost_usd": budget_controller.cost_usd}
        )

    needs_input = any(
        observation.status is CapabilityObservationStatus.NEEDS_INPUT
        for observation in read_observations
    )
    action = (
        AgentBoundaryAction.CLARIFY if needs_input else AgentBoundaryAction.ANSWER
    )
    fingerprint = response_fingerprint(
        action, grounded.content, state_version=state_version, observations=read_observations
    )
    stop_reason = (
        AgentStopReason(budget_stop.value)
        if budget_stop is not None
        else (
            AgentStopReason.CLARIFICATION_REQUIRED
            if needs_input
            else AgentStopReason.ANSWERED
        )
    )
    if budget_stop is None and fingerprint in prior:
        stop_reason = AgentStopReason.REPEATED_RESPONSE

    if event_callback is not None and emit_answer_deltas:
        for chunk in _public_answer_chunks(grounded.content):
            await event_callback("assistant.delta", {"content": chunk})

    result = AgentLoopResult(
        action=action,
        output=grounded.content,
        stop_reason=stop_reason,
        observations=read_observations,
        usage=usage,
        state_version=state_version,
        response_fingerprint=fingerprint,
        typed_response=None,
    )
    document_sources = (
        list(trusted_answer_sink.answer.document_sources)
        if trusted_answer_sink is not None and trusted_answer_sink.answer is not None
        else []
    )
    return ReadOnlyTurnResult(
        result=result,
        grounded=grounded,
        grounding_verdict=outcome_verdict,
        correction_rounds=correction_rounds,
        used_fallback=used_fallback,
        executor_read_calls=executor_read_calls,
        consequential_executions=consequential_executions,
        allowed_evidence=allowed_refs,
        document_sources=document_sources,
    )


_VISIBLE_TOOL_LABELS = {
    "query_project_metadata": "project metadata",
    "search_documentation": "indexed documentation",
    "list_project_documents": "generated document inventory",
    "read_document": "indexed document",
    "read_generated_document": "generated document",
}
_VISIBLE_EVIDENCE_KEY_RE = re.compile(
    r"(?:metadata:[a-z0-9_.:-]+|mcp-doc(?:-content)?:[a-z0-9_.:/-]+|doc:\d+:\d+)\b",
    re.IGNORECASE,
)


def _humanize_visible_content(
    content: str, envelopes: list[ToolEnvelope]
) -> str:
    """Render model-visible implementation references as human source labels.

    Machine evidence keys remain untouched in the parsed grounding block and
    therefore retain citation integrity. Only the user-facing prose is changed.
    """

    titles: dict[str, str] = {}
    for envelope in envelopes:
        items = envelope.data.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            evidence_id = item.get("id")
            title = item.get("title")
            if isinstance(evidence_id, str) and isinstance(title, str) and title.strip():
                titles[evidence_id] = title.strip()

    visible = content
    for semantic_name, label in _VISIBLE_TOOL_LABELS.items():
        visible = re.sub(rf"\b{re.escape(semantic_name)}\b", label, visible)

    def evidence_label(match: re.Match[str]) -> str:
        key = match.group(0)
        title = titles.get(key)
        if title:
            return title
        lowered = key.casefold()
        if lowered.startswith("metadata:"):
            return "project metadata"
        if lowered.startswith("mcp-doc"):
            return "generated documentation"
        return "indexed document passage"

    return _VISIBLE_EVIDENCE_KEY_RE.sub(evidence_label, visible)


async def _invoke_graph(
    graph: Runnable,
    input_value: dict[str, Any],
    *,
    event_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
    trusted_answer_sink: TrustedAnswerSink | None = None,
) -> dict[str, Any]:
    if trusted_answer_sink is not None and trusted_answer_sink.answer is not None:
        return {"messages": list(input_value.get("messages", []))}
    if event_callback is None:
        return await graph.ainvoke(input_value)
    seen_messages: list[BaseMessage] = []
    finalized_messages: list[BaseMessage] = []
    proposed_call_refs: set[str] = set()

    async def emit_proposals(message: AIMessage) -> None:
        for call in message.tool_calls:
            name = str(call.get("name") or "").strip()
            call_ref = str(call.get("id") or "").strip()
            if not name or not call_ref or call_ref in proposed_call_refs:
                continue
            proposed_call_refs.add(call_ref)
            await event_callback(
                "tool.proposed",
                {
                    "tool_label": name.replace("_", " ")[:120],
                    "semantic_name": name,
                    "status": "proposed",
                    "call_ref": call_ref[:160],
                    # Private callback-only data. PublicAgentEvent sanitization
                    # removes it before persistence/SSE projection.
                    "arguments": dict(call.get("args") or {}),
                },
            )

    async for mode, chunk in graph.astream(
        input_value, stream_mode=["messages", "updates"]
    ):
        if mode == "messages":
            message, _metadata = chunk
            if isinstance(message, BaseMessage):
                seen_messages.append(message)
            if isinstance(message, AIMessageChunk):
                # Tool-call chunks can arrive before their JSON arguments are
                # complete. The finalized model update below is authoritative.
                continue
            if isinstance(message, AIMessage) and message.tool_calls:
                await emit_proposals(message)
            elif isinstance(message, ToolMessage):
                await _emit_tool_event(message, event_callback)
                if (
                    trusted_answer_sink is not None
                    and trusted_answer_sink.answer is not None
                ):
                    # ``answer_from_documentation`` delegates to the complete
                    # deterministic RAG pipeline. Once that tool has produced a
                    # trusted answer and its completion event is visible, the
                    # answer is terminal: do not ask the outer agent model to
                    # rewrite it or risk discarding it at the turn deadline.
                    return {"messages": finalized_messages or seen_messages}
        elif mode == "updates":
            phases: set[str] = set()
            trusted_tool_completed = False
            for node, node_update in chunk.items():
                if node == "__interrupt__":
                    continue
                lowered = str(node).lower()
                phases.add("tool" if "tool" in lowered else "model" if "model" in lowered else "runtime")
                if isinstance(node_update, dict):
                    update_messages = node_update.get("messages", [])
                    if isinstance(update_messages, list):
                        for item in update_messages:
                            if not isinstance(item, BaseMessage):
                                continue
                            finalized_messages.append(item)
                            if (
                                isinstance(item, AIMessage)
                                and not isinstance(item, AIMessageChunk)
                                and item.tool_calls
                            ):
                                await emit_proposals(item)
                            elif isinstance(item, ToolMessage):
                                trusted_tool_completed = bool(
                                    trusted_answer_sink is not None
                                    and trusted_answer_sink.answer is not None
                                )
            for phase in sorted(phases):
                await event_callback(
                    "runtime.update",
                    {"status": "running", "phase": phase, "summary": f"{phase.title()} step completed."},
                )
            if trusted_tool_completed:
                return {"messages": finalized_messages or seen_messages}
    return {"messages": finalized_messages or seen_messages}


async def _emit_tool_event(
    message: ToolMessage,
    event_callback: Callable[[str, dict[str, Any]], Awaitable[None]],
) -> None:
    if str(getattr(message, "name", "")) == TURN_ASSESSMENT_TOOL_NAME:
        return
    try:
        envelope = ToolEnvelope.model_validate_json(str(message.content))
    except ValueError:
        await event_callback(
            "tool.completed",
            {"tool_label": "Tool", "status": "failed", "summary": "The tool returned an invalid safe observation."},
        )
        return
    label = str(getattr(message, "name", None) or "Tool").replace("_", " ")[:120]
    if envelope.status == "approval_required":
        event_type = "tool.awaiting_approval"
    elif envelope.status == "denied":
        event_type = "tool.denied"
    else:
        event_type = "tool.completed"
    payload: dict[str, Any] = {
        "tool_label": label,
        "semantic_name": str(getattr(message, "name", None) or "") or None,
        "call_ref": str(getattr(message, "tool_call_id", None) or "") or None,
        "status": envelope.status,
        "summary": envelope.summary,
        "argument_shape": envelope.data.get("argument_shape", {}),
        "validation_errors": envelope.data.get("validation_errors", []),
        "attempt": envelope.data.get("attempt"),
        "error_code": (envelope.error or {}).get("code"),
    }
    if event_type == "tool.awaiting_approval":
        payload["proposal_id"] = envelope.data.get("proposal_id")
    if event_type == "tool.completed":
        payload.update(
            evidence_refs=envelope.evidence_refs,
            receipt_ref=envelope.receipt_ref,
            workflow_ref=envelope.workflow_ref,
        )
    await event_callback(event_type, payload)
    if envelope.workflow_ref:
        workflow_id = envelope.workflow_ref.get("workflow_run_id")
        workflow_status = str(envelope.workflow_ref.get("status") or envelope.status)
        await event_callback(
            "workflow.waiting" if workflow_status in {"waiting", "needs_input"} else "workflow.started",
            {
                "workflow_run_id": workflow_id,
                "status": workflow_status,
                "summary": envelope.summary,
            },
        )
    if str(getattr(message, "name", "")).startswith(("create_agent_plan", "revise_agent_plan", "pause_agent_plan", "resume_agent_plan", "abandon_agent_plan")):
        await event_callback(
            "plan.updated",
            {
                "plan_id": envelope.data.get("plan_id"),
                "version": envelope.data.get("version"),
                "status": envelope.status,
                "summary": envelope.summary,
            },
        )


def _public_answer_chunks(content: str, *, size: int = 96) -> list[str]:
    if not content:
        return []
    return [content[index : index + size] for index in range(0, len(content), size)]


def _budget_fallback(reason: BudgetStopReason | None) -> str:
    if reason is BudgetStopReason.SOFT_DEADLINE:
        return "I reached the interactive time limit before I could safely finish. No new action was started after that limit."
    if reason is BudgetStopReason.HARD_DEADLINE:
        return "I stopped at the absolute run deadline. No new action was started after the stop decision."
    if reason is BudgetStopReason.MODEL_TIMEOUT:
        return "The model took too long to respond, so I stopped this run safely."
    if reason is BudgetStopReason.TOOL_TIMEOUT:
        return "A tool took too long to respond, so I stopped without treating that as missing evidence."
    if reason in {BudgetStopReason.MODEL_CALL_LIMIT, BudgetStopReason.TOOL_CALL_LIMIT}:
        return "I reached this run's call limit before I could safely finish."
    if reason is BudgetStopReason.COST_LIMIT:
        return "I reached this run's cost limit before I could safely finish."
    return _SAFE_FALLBACK_TEXT


def _accumulate_usage(usage: AgentUsage, messages: list[Any]) -> AgentUsage:
    input_tokens = usage.input_tokens
    output_tokens = usage.output_tokens
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        meta = message.usage_metadata or {}
        if isinstance(meta.get("input_tokens"), int):
            input_tokens += int(meta["input_tokens"])
        if isinstance(meta.get("output_tokens"), int):
            output_tokens += int(meta["output_tokens"])
    return AgentUsage(input_tokens=input_tokens, output_tokens=output_tokens)


def _collect_envelopes(messages: list[Any]) -> list[ToolEnvelope]:
    envelopes: list[ToolEnvelope] = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        try:
            payload = json.loads(str(message.content))
        except (ValueError, TypeError):
            continue
        try:
            envelopes.append(ToolEnvelope.model_validate(payload))
        except ValueError:
            continue
    return envelopes


def _final_assistant_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls:
            return _safe_text(message.content)
    return ""


def _safe_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _parse_grounded_answer(text, *, requirement) -> GroundedAnswer:  # noqa: ANN001
    match = _GROUNDING_BLOCK_RE.search(text or "")
    content = (text or "").strip()
    citations: list[GroundingClaim] = []
    disclosures: list[str] = []
    status: AnswerStatus | None = None
    if match is not None:
        content = (text[: match.start()]).strip()
        try:
            block = json.loads(match.group("body"))
        except (ValueError, TypeError):
            block = None
        if isinstance(block, dict):
            status = _coerce_status(block.get("answer_status"))
            raw_disclosures = block.get("disclosures", [])
            if isinstance(raw_disclosures, list):
                disclosures = [
                    str(item)[:120]
                    for item in raw_disclosures
                    if isinstance(item, str) and item
                ][:10]
            raw_citations = block.get("citations", [])
            if not isinstance(raw_citations, list):
                raw_citations = []
            for raw in raw_citations[:20]:
                if not isinstance(raw, dict):
                    continue
                key = str(raw.get("claim_key") or "claim")[:200]
                refs = [
                    str(ref)[:200]
                    for ref in raw.get("evidence_refs", [])
                    if ref
                ][:10]
                citations.append(GroundingClaim(claim_key=key, evidence_refs=refs))
    if status is None:
        status = requirement.status if requirement.status is not None else AnswerStatus.GROUNDED
    return GroundedAnswer(
        content=content,
        citations=citations,
        disclosures=disclosures,
        answer_status=status,
    )


def _coerce_status(value: Any) -> AnswerStatus | None:
    if not isinstance(value, str):
        return None
    try:
        return AnswerStatus(value.strip().lower())
    except ValueError:
        return None


_ENVELOPE_STATUS_TO_OBSERVATION = {
    "succeeded": CapabilityObservationStatus.SUCCEEDED,
    "triggered": CapabilityObservationStatus.TRIGGERED,
    "denied": CapabilityObservationStatus.DENIED,
    "needs_input": CapabilityObservationStatus.NEEDS_INPUT,
}


def _collect_observations(
    ledger: SemanticEventLedger, since: int
) -> list[CapabilityObservation]:
    observations: list[CapabilityObservation] = []
    for event in ledger.events[since:]:
        if event.kind not in {"gateway.executor_completed", "middleware.needs_input"}:
            continue
        status = _ENVELOPE_STATUS_TO_OBSERVATION.get(
            str(event.observation_status), CapabilityObservationStatus.FAILED
        )
        observations.append(
            CapabilityObservation(
                capability_id=str(event.canonical_id or "unknown"),
                status=status,
            )
        )
    return observations


def _count_consequential_executions(
    ledger: SemanticEventLedger,
    snapshot: SemanticToolSnapshot,
    since: int,
) -> int:
    """Count completed consequential calls in runtimes that permit effects."""

    consequential_ids = {
        record.canonical_id
        for record in getattr(snapshot, "tools", ())
        if record.governance_class == GovernanceClass.CONSEQUENTIAL
    }
    return sum(
        event.kind == "gateway.executor_completed"
        and event.canonical_id in consequential_ids
        for event in ledger.events[since:]
    )


def render_safe_fallback_envelope() -> str:
    """A serialized safe envelope, used by scripted mechanics tests."""

    return serialize_tool_envelope(
        ToolEnvelope(status="failed", summary=_SAFE_FALLBACK_TEXT)
    )


__all__ = [
    "MODEL_FACING_READ_EXCLUSIONS",
    "READONLY_MODEL_PROFILE",
    "READONLY_PROMPT_VERSION",
    "READONLY_RUNTIME_RELEASE",
    "READ_ONLY_PRIMARY_SYSTEM_PROMPT",
    "ReadOnlyPrimaryRuntime",
    "ReadOnlyTurnResult",
    "build_readonly_primary_chat_model",
    "build_readonly_primary_runtime",
    "grounding_failure_answer",
    "readonly_manifest_partition",
    "run_readonly_primary_turn",
]
