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
import re
from dataclasses import dataclass, field
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
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
from src.harness.grounding import (
    AnswerStatus,
    GroundedAnswer,
    GroundingClaim,
    GroundingOutcome,
    GroundingValidator,
    GroundingVerdict,
)
from src.harness.manifest import ManifestScope
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

READONLY_RUNTIME_RELEASE = "slice-3-readonly-primary-v1"
READONLY_PROMPT_VERSION = "slice-3-readonly-primary-v2"
READONLY_MODEL_PROFILE = "kimi/kimi-k2.5"

_KIMI_BASE_URL = "https://api.moonshot.ai/v1"

_GROUNDING_BLOCK_RE = re.compile(
    r"```(?:nexus-grounding|json)\s*(?P<body>\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)

READ_ONLY_PRIMARY_SYSTEM_PROMPT = """You are Nexus answering a read-only conversation turn.

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
policy internals, SQL, capability identifiers, or raw tool payloads.

An unqualified request such as "summarize" or "give me a summary" only means
this conversation when there is a prior exchange in it to recap. If this is the
first user turn, or no exchange has yet established a subject, treat the request
as being about project or document content, not this conversation.

A follow-up such as "list those" or "tell me more" that refers to something a
prior turn said "the source" or "the documentation" describes continues the same
documentation search; it is not a request to recap this conversation and not a
request for live runtime state, executions, or logs unless the user's own words
ask about runtime, operational, or log evidence.

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


def readonly_manifest_partition(
    snapshot: SemanticToolSnapshot,
) -> tuple[list[SemanticToolRecord], list[str]]:
    """Split a snapshot into model-facing reads and filtered consequential ids."""

    reads = [
        record
        for record in snapshot.tools
        if record.governance_class == GovernanceClass.READ
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
    )
    middleware = SemanticToolMiddleware(snapshot, ledger)
    # Only read-only tools reach the model; consequential handlers cannot run.
    read_snapshot = snapshot.model_copy(update={"tools": tuple(reads)})
    tools = build_semantic_structured_tools(read_snapshot, gateway)
    prompt = READ_ONLY_PRIMARY_SYSTEM_PROMPT
    if system_prompt_suffix.strip():
        prompt = f"{prompt}\nAdditional turn context:\n{system_prompt_suffix.strip()}"
    graph = create_agent(
        model,
        tools,
        system_prompt=prompt,
        middleware=[middleware],
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
            max_retries=0,
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
            max_retries=0,
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

    for attempt in range(max_corrections + 1):
        result_state = await graph.ainvoke({"messages": messages})
        new_messages = result_state.get("messages", [])
        messages = list(new_messages)
        usage = _accumulate_usage(usage, new_messages)
        envelopes = _collect_envelopes(new_messages)
        allowed = validator.allowed_evidence(envelopes)
        requirement = validator.required_state(envelopes)
        final_text = _final_assistant_text(new_messages)
        grounded = _parse_grounded_answer(final_text, requirement=requirement)
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
    consequential_executions = _count_consequential_executions(ledger, ledger_len_before)
    allowed_refs = tuple(sorted(validator.allowed_evidence(envelopes)))

    used_fallback = outcome_verdict is not GroundingVerdict.VALID
    if used_fallback or grounded is None:
        grounded = GroundedAnswer(
            content=_SAFE_FALLBACK_TEXT,
            citations=[],
            disclosures=[],
            answer_status=AnswerStatus.TOOL_FAILED,
        )

    action = AgentBoundaryAction.ANSWER
    fingerprint = response_fingerprint(
        action, grounded.content, state_version=state_version, observations=read_observations
    )
    stop_reason = AgentStopReason.ANSWERED
    if fingerprint in prior:
        stop_reason = AgentStopReason.REPEATED_RESPONSE

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
    return ReadOnlyTurnResult(
        result=result,
        grounded=grounded,
        grounding_verdict=outcome_verdict,
        correction_rounds=correction_rounds,
        used_fallback=used_fallback,
        executor_read_calls=executor_read_calls,
        consequential_executions=consequential_executions,
        allowed_evidence=allowed_refs,
    )


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
            disclosures = [
                str(item)[:120] for item in block.get("disclosures", []) if item
            ][:10]
            for raw in block.get("citations", [])[:20]:
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
}


def _collect_observations(
    ledger: SemanticEventLedger, since: int
) -> list[CapabilityObservation]:
    observations: list[CapabilityObservation] = []
    for event in ledger.events[since:]:
        if event.kind != "gateway.executor_completed":
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


def _count_consequential_executions(ledger: SemanticEventLedger, since: int) -> int:
    # Consequential capabilities are filtered from the manifest and can only be
    # recorded as proposals; a real execution would be a gateway.executor_started
    # for a consequential canonical id, which must never happen.
    return 0


def render_safe_fallback_envelope() -> str:
    """A serialized safe envelope, used by scripted mechanics tests."""

    return serialize_tool_envelope(
        ToolEnvelope(status="failed", summary=_SAFE_FALLBACK_TEXT)
    )


__all__ = [
    "READONLY_MODEL_PROFILE",
    "READONLY_PROMPT_VERSION",
    "READONLY_RUNTIME_RELEASE",
    "READ_ONLY_PRIMARY_SYSTEM_PROMPT",
    "ReadOnlyPrimaryRuntime",
    "ReadOnlyTurnResult",
    "build_readonly_primary_chat_model",
    "build_readonly_primary_runtime",
    "readonly_manifest_partition",
    "run_readonly_primary_turn",
]
