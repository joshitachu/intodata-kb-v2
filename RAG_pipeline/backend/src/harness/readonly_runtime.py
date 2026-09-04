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

import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.runnables import Runnable

from src.generation.usage import append_truncation_notice
from src.harness.agent_loop import (
    AgentBoundaryAction,
    AgentLoopResult,
    AgentStopReason,
    AgentUsage,
    response_fingerprint,
)
from src.harness.agent_runs import sanitize_public_payload
from src.harness.capability_runtime import (
    CapabilityExecutionContext,
    CapabilityObservation,
    CapabilityObservationStatus,
)
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.evidence_summary import EvidenceSummary, build_evidence_summary
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
    contains_internal_output,
)
from src.harness.manifest import ManifestScope
from src.harness.model_usage import token_usage_from_messages
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
from src.harness.topology_capability import (
    TOPOLOGY_STRUCTURE_CAPABILITY,
    topology_targets,
)
from src.harness.trusted_answer import ANSWER_DELTA_CHARS, TrustedAnswerSink
from src.harness.turn_instrumentation import ModelLatencyMiddleware
from src.schemas import DocumentSource
from src.turn_progress import turn_progress_scope

logger = logging.getLogger(__name__)

READONLY_RUNTIME_RELEASE = "slice-3-readonly-primary-v1"
READONLY_PROMPT_VERSION = "evidence-fusion-source-ownership-v1"
READONLY_MODEL_PROFILE = "kimi/kimi-k2.6"

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
tool. When the user forbids acting, do not call a consequential tool. A request
for an explanation about project facts still requires the appropriate read
evidence. You cannot perform or propose consequential
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

Choose evidence by question shape. Published topology owns complete inventory and
counts, connections, dependency traversal, and which jobs or components
structurally implement a flow. Indexed documentation owns purpose, why, business
meaning, explanatory implementation, business rules, and mapping explanation.
Runtime records and logs own present-tense executions, failures, health, and
timestamps. One source may complement another but must never silently substitute
for a missing, failed, or excluded source.

Use documentation search for every request about project documentation content:
project-content explanations and facts inside or across documents. Do not
substitute active workflows, runtime evidence, logs, generated-document
inventories, or direct file reads for indexed RAG. For a concise request, begin
with at most 8 search results. If those results answer the question, stop
gathering evidence and answer.

Use documentation search for exhaustive inventories of facts explicitly contained
in documentation and for complex explanatory questions across the selected
project's indexed documentation. Use topology, not similarity search, for
exhaustive structural inventories, counts, connections, and dependency traversal.
Preserve the requested entities in a documentation search query and use a larger
bounded top_k when broader recall is needed. Do not bypass indexed RAG by listing
or opening generated Markdown files.

For mixed questions, call every needed topology and runtime read before the
terminal answer_from_documentation capability. This ordering keeps every source
in the current turn's evidence summary. Runtime evidence must not be inferred
from documented design, and business purpose must not be inferred from topology
structure.

Honor explicit source instructions. If the user says not to use topology,
documentation, runtime records, or logs, do not call that source. If the excluded
source is necessary for a complete or reliable answer, answer only the supported
portion and name what cannot be grounded, or ask permission to use it. A failed or
unavailable source does not cancel this rule and does not authorize silent
substitution.

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

#: Minimum gap between two live progress events. Almost all of a turn's wall time
#: is model decode, during which the graph emits no node update at all, so the
#: client used to sit on a static status line for 20s or more. One event every
#: couple of seconds is enough for the status line to visibly move; shorter
#: intervals buy nothing and every event is a durable, sequenced public event.
PROGRESS_INTERVAL_SECONDS = 2.0

#: Server-authored labels for each observable phase. These strings are the *only*
#: thing published while the model is generating -- see ``_LiveProgressReporter``.
#:
#: Phases below ``reading_evidence`` are reported by the work itself, from inside
#: a tool call, via :func:`src.turn_progress.report_phase`. That matters because
#: on the dominant chat path a single ``answer_from_documentation`` call *is* the
#: turn: the outer model finishes in ~2s and the tool then runs the whole RAG
#: pipeline. Without those inner phases the status line has nothing to say for the
#: entire wait.
_PROGRESS_LABELS: dict[str, str] = {
    "thinking": "Waiting on the model",
    "selecting_tool": "Choosing the next step",
    "reading_evidence": "Reading the evidence",
    "searching_documents": "Searching the documentation",
    "generating_answer": "Writing the answer from the sources",
    "writing_answer": "Writing the answer",
    "validating_answer": "Checking the answer against its sources",
    "finalizing_answer": "Finishing up",
}

#: Label for a phase name this module does not know. Deliberately vague: an
#: unrecognized phase means some layer reported work this module cannot describe,
#: and a vague truth beats a specific guess. ``thinking`` has its own, sharper
#: label ("waiting on the model") precisely because that phase *is* verified --
#: it is only ever reported while a model call is outstanding.
_GENERIC_PROGRESS_LABEL = "Working on it"

#: Hard ceiling on the label half of a ``summary``. The status line is one short
#: line in a ~360px panel, and the summary is a public payload: bounding it here
#: keeps a long composed label from ever becoming a wall of text downstream.
#: :func:`sanitize_public_payload` still redacts and re-bounds the final value.
_MAX_PROGRESS_LABEL_CHARS = 120

#: Verb for a topology operation whose work is not plain reading. The noun comes
#: from ``_VISIBLE_TOOL_LABELS`` (the humanizer), so this map holds only the verb
#: and cannot drift from the source names the humanizer publishes. Anything not
#: listed reads: an unknown or model-garbled operation therefore degrades to
#: "Reading the project topology", which is true of every topology call.
_TOPOLOGY_OPERATION_VERBS: dict[str, str] = {
    "find_topology_node": "Searching",
    "list_topology_dependencies": "Traversing",
    # Field lineage walks source column -> target column, so "reading" would
    # understate a call that can take noticeably longer than a rollup read.
    "get_field_lineage": "Tracing",
}

_PHASE_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+")
_PHASE_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class _ProgressPhase:
    """One publishable progress state.

    ``phase`` is the stable machine-readable name the client keys on; ``label``
    is the human sentence that goes into ``summary``. Both are server-authored:
    ``label`` is composed only from constants in this module and integers derived
    from server-computed observation fields, never from model output, tool
    arguments, or any free text a capability returned.
    """

    phase: str
    label: str


def _phase_slug(label: str) -> str:
    """Derive a stable machine-readable phase fragment from a human label."""

    return _PHASE_SLUG_RE.sub("_", _PHASE_ARTICLE_RE.sub("", label.casefold())).strip("_")


def _source_read_phase(
    semantic_name: str, arguments: dict[str, Any] | None
) -> _ProgressPhase | None:
    """Name the source a single in-flight read is about to touch.

    Returns ``None`` when the capability has no human-facing source label, so the
    caller falls back to the generic ``reading_evidence`` phase. A semantic name
    is an implementation detail and never reaches the payload, not even as a
    phase slug.
    """

    source = _VISIBLE_TOOL_LABELS.get(semantic_name)
    if source is None:
        return None
    verb = "Reading"
    if semantic_name == TOPOLOGY_STRUCTURE_CAPABILITY.semantic_name:
        # The operation the model asked for is what the executor is about to run,
        # so naming it describes the call in flight. It is matched against a
        # closed allowlist rather than echoed, so a garbled value cannot mislead.
        operation = (arguments or {}).get("operation")
        if isinstance(operation, str):
            verb = _TOPOLOGY_OPERATION_VERBS.get(operation, verb)
    return _ProgressPhase(
        phase=f"{verb.casefold()}_{_phase_slug(source)}",
        label=f"{verb} {source}",
    )


def _read_batch_phase(
    calls: list[tuple[str, str, dict[str, Any]]],
) -> _ProgressPhase:
    """Name the phase for one batch of freshly proposed read calls.

    A single call can be named exactly. Two calls of the same capability are
    still that capability's source, but their operations may differ, so the
    operation is dropped. A mixed batch has no single true answer and falls back
    to the generic phase rather than naming whichever call happened to be first.
    """

    names = {name for name, _call_ref, _arguments in calls}
    if len(names) == 1:
        arguments = calls[0][2] if len(calls) == 1 else None
        named = _source_read_phase(next(iter(names)), arguments)
        if named is not None:
            return named
    return _ProgressPhase("reading_evidence", _PROGRESS_LABELS["reading_evidence"])


def _ambiguous_candidate_count(data: dict[str, Any]) -> int | None:
    """Count the candidates a topology read refused to guess between.

    Both flags are computed server-side by the topology tools (more than one
    exact-name match, or an unresolved name), so the count is a fact about the
    observation rather than an inference about the model. Cheap by construction:
    two dict lookups and one ``len``, no walk of the observation.
    """

    content = data.get("content")
    if not isinstance(content, dict):
        return None
    unresolved = content.get("resolved") is False and content.get("reason") == "ambiguous"
    if not (content.get("ambiguous") is True or unresolved):
        return None
    candidates = content.get("candidates")
    if not isinstance(candidates, list) or len(candidates) < 2:
        return None
    return len(candidates)


def _post_read_phase(envelope: ToolEnvelope | None) -> _ProgressPhase:
    """Name what happens once a read has returned.

    Control is back with the model either way; the ambiguity case says what the
    model has been handed, which is the one thing about that wait a user can act
    on ("it found several things called that").
    """

    if envelope is not None:
        count = _ambiguous_candidate_count(envelope.data)
        if count is not None:
            return _ProgressPhase(
                "resolving_candidates", f"Resolving {count} candidate matches"
            )
    return _ProgressPhase("thinking", _PROGRESS_LABELS["thinking"])


class _LiveProgressReporter:
    """Publish server-authored progress while the turn is still working.

    HARD CONSTRAINT: not one character of model output passes through here. The
    grounding validator has not yet accepted the text the model is streaming, so
    only the phase, a server-authored label and the elapsed time are published.
    A label is either a ``_PROGRESS_LABELS`` constant or one composed from this
    module's constants plus an integer the server itself computed (see
    ``_source_read_phase`` and ``_post_read_phase``) -- never observation prose,
    tool arguments, or a semantic capability name. Answer prose stays gated behind
    validation and is delivered afterwards by the caller.

    Two independent drivers, because either alone leaves the user staring at a
    frozen line:

    * **Phase changes**, reported inline from the working task.
    * **Elapsed time**, from :meth:`running`'s background ticker. A model chunk is
      not a usable clock -- a turn whose work is one long tool call produces no
      chunks at all -- so the ticker re-publishes the current phase on a timer.

    The ticker emits through ``heartbeat_callback`` rather than through
    ``event_callback``, because it runs *concurrently* with the turn's own database
    session: that session may be mid-query inside a tool call, and committing on it
    from another task is a correctness hazard, not just a race. With no
    ``heartbeat_callback`` the ticker simply does not run.
    """

    __slots__ = (
        "_clock",
        "_emit",
        "_heartbeat_emit",
        "_interval",
        "_label",
        "_last_emit",
        "_phase",
        "_started",
    )

    def __init__(
        self,
        event_callback: Callable[[str, dict[str, Any]], Awaitable[None]],
        *,
        heartbeat_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        interval: float | None = None,
    ) -> None:
        self._emit = event_callback
        self._heartbeat_emit = heartbeat_callback
        self._clock = clock
        # Resolved at construction, not as a default argument, so the module
        # constant stays the single knob (and is patchable in tests).
        self._interval = max(
            0.0, PROGRESS_INTERVAL_SECONDS if interval is None else interval
        )
        self._started = clock()
        self._last_emit: float | None = None
        self._phase = ""
        self._label = ""

    def _payload(self, phase: str, label: str, *, now: float) -> dict[str, Any]:
        elapsed_ms = max(0, int((now - self._started) * 1_000))
        # Bounded here, then run through the same allowlist/redaction the durable
        # public event goes through. `label` is server-authored by construction,
        # so this is defence in depth rather than the primary guarantee -- but it
        # means no future caller can widen the payload or smuggle a credential
        # through the one channel that fires dozens of times a turn.
        summary = f"{label[:_MAX_PROGRESS_LABEL_CHARS]}... {elapsed_ms // 1_000}s"
        return sanitize_public_payload(
            "assistant.progress",
            {
                "status": "running",
                "phase": phase,
                "summary": summary,
                "elapsed_ms": elapsed_ms,
            },
        )

    async def note_phase(
        self, phase: str, *, label: str | None = None, force: bool = False
    ) -> None:
        """Emit progress for ``phase`` when it is new or the interval elapsed.

        ``label`` overrides the static wording for phases whose sentence depends
        on real in-flight state (which source is being read, how many candidates
        came back). Omitted, the phase's own server-authored label is used.
        """

        now = self._clock()
        resolved = label or _PROGRESS_LABELS.get(phase, _GENERIC_PROGRESS_LABEL)
        # A relabelled phase is a state change too: "resolving 4 candidate
        # matches" must not wait out the interval behind a stale "reading".
        changed = phase != self._phase or resolved != self._label
        due = self._last_emit is None or (now - self._last_emit) >= self._interval
        if not (force or changed or due):
            return
        self._phase = phase
        self._label = resolved
        self._last_emit = now
        await self._emit("assistant.progress", self._payload(phase, resolved, now=now))

    async def note_state(self, state: _ProgressPhase, *, force: bool = False) -> None:
        """Emit a phase whose label was derived from real in-flight state."""

        await self.note_phase(state.phase, label=state.label, force=force)

    async def note_model_chunk(self, chunk: AIMessageChunk) -> None:
        """Classify one streamed model chunk into a publishable phase."""

        if getattr(chunk, "tool_call_chunks", None):
            await self.note_phase("selecting_tool")
        elif _safe_text(chunk.content).strip():
            await self.note_phase("writing_answer")
        else:
            await self.note_phase("thinking")

    async def beat(self) -> None:
        """Re-publish the current phase with a fresh elapsed time.

        Called only by the ticker. It does not change ``_phase``, so it cannot
        misreport what the turn is doing -- it only refreshes the clock the user
        is watching.
        """

        if self._heartbeat_emit is None:
            return
        now = self._clock()
        if self._last_emit is not None and (now - self._last_emit) < self._interval:
            return
        self._last_emit = now
        phase = self._phase or "thinking"
        label = self._label or _PROGRESS_LABELS.get(phase, _GENERIC_PROGRESS_LABEL)
        await self._heartbeat_emit(
            "assistant.progress", self._payload(phase, label, now=now)
        )

    @asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        """Run the elapsed-time ticker for the duration of the block."""

        if self._heartbeat_emit is None or self._interval <= 0:
            yield
            return
        task = asyncio.create_task(self._tick_forever(), name="nexus_turn_progress")
        try:
            yield
        finally:
            task.cancel()
            # Await the cancellation so the ticker cannot emit after the answer.
            # `_tick_forever` swallows its own failures, so CancelledError is the
            # only thing that can escape here.
            with suppress(asyncio.CancelledError):
                await task

    async def _tick_forever(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.beat()
            except Exception:
                # A cosmetic heartbeat must never take the turn down with it.
                logger.debug("Progress heartbeat failed", exc_info=True)


#: Hold-back window at the tail of the raw draft, in characters.
#:
#: A grounding fence arrives as ``` one token at a time, so the buffer's last few
#: characters may be an incomplete fence opener. Nothing inside this window is
#: ever eligible for emission, which is what stops a partial "``" from reaching
#: the screen and then having to be retracted.
_DRAFT_FENCE_HOLDBACK = 3


class _DraftAnswerStreamer:
    """Show the outer model's answer prose while it is still being written.

    This is the general counterpart to :class:`AnswerDeltaSink`, which streams the
    ONE capability whose answer needs no validation. Here the answer has NOT been
    validated yet, so the text is explicitly provisional: every delta is published
    ``replayable=False`` and the authoritative, post-persistence delta run resets
    the client's buffer and rebuilds it. Whatever the server persisted always wins
    on screen -- streaming changes only WHEN text appears.

    Provisional is not the same as unfiltered. Raw model chunks cannot be forwarded
    as they arrive, because three separate transforms sit between what the model
    writes and what a user may read:

    1. **The grounding block.** The model appends a fenced ``nexus-grounding``
       JSON block carrying evidence ids. ``_parse_grounded_answer`` splits it off;
       until then it is raw contract, so streaming stops at the first fence.
    2. **The humanizer.** ``_humanize_visible_content`` relabels semantic tool
       names and evidence keys, and on the topology surface deletes structural
       references outright. Forwarding pre-humanized text would leak exactly the
       implementation references that transform exists to remove.
    3. **The leak gate.** ``contains_internal_output`` is the one grounding
       failure that discards an answer outright rather than correcting it. Text
       that trips it must never have been displayed, so it is screened here, on
       the same rule the validator uses, before anything is published.

    Emission is therefore bounded by a *safe boundary*: a position where applying
    the humanizer to the prefix provably yields a prefix of applying it to the
    whole text, so already-shown characters can never need retracting.

    * Default surface: the last whitespace character. The humanizer's patterns
      there (``\\bsemantic_name\\b`` and evidence keys) are single tokens and
      cannot span whitespace.
    * Topology surface (``suppress_structural``): the last newline. Suppression
      matches multi-word spans like "(from the project topology)" and trims line
      ends, so whitespace is no longer a safe cut -- a line boundary is.

    A prefix-stability check backs that reasoning up at runtime rather than
    trusting it: if a recomputation ever contradicts what was already shown, the
    streamer halts for the turn and the authoritative replay delivers the answer.
    """

    __slots__ = (
        "_emitted",
        "_halted",
        "_publish",
        "_raw",
        "_sealed",
        "_suppress_structural",
    )

    def __init__(
        self,
        publish: Callable[[str], Awaitable[None]],
        *,
        suppress_structural: bool = False,
    ) -> None:
        self._publish = publish
        self._suppress_structural = suppress_structural
        self._raw = ""
        self._emitted = ""
        #: Set once a grounding fence is seen: prose is complete, the rest is contract.
        self._sealed = False
        #: Set on a leak or a prefix contradiction. Terminal for the turn.
        self._halted = False

    @property
    def streamed(self) -> str:
        """Exactly what has been shown, for assertions and for the reset decision."""

        return self._emitted

    async def feed(
        self, text: str, envelopes: list[ToolEnvelope], *, flush: bool = False
    ) -> None:
        """Accumulate one model chunk and publish whatever became safe to show.

        ``flush`` lowers the batch threshold to zero, for the end of a draft: the
        validator gate and persistence that follow are real dead time, so the last
        partial batch is worth showing rather than holding for a size it will
        never reach.
        """

        if self._halted:
            return
        if text and not self._sealed:
            self._raw += text
        candidate = self._safe_prose(flush=flush)
        if candidate is None:
            return
        visible = _humanize_visible_content(
            candidate, envelopes, suppress_structural=self._suppress_structural
        )
        if not visible.startswith(self._emitted):
            # The reasoning above says this cannot happen. If it does, the honest
            # move is to stop rather than to contradict text already on screen.
            logger.warning(
                "Draft answer stream halted: humanized prefix diverged at %d chars",
                len(self._emitted),
            )
            self._halted = True
            return
        delta = visible[len(self._emitted) :]
        if not delta or (not flush and len(delta) < ANSWER_DELTA_CHARS):
            return
        if contains_internal_output(visible):
            # The validator would discard this answer outright. Stop before the
            # leak is displayed; the turn still completes and replays normally.
            logger.warning("Draft answer stream halted: internal output detected")
            self._halted = True
            return
        self._emitted = visible
        try:
            await self._publish(delta)
        except Exception:
            # Display is cosmetic. A publish failure must not fail the turn, but
            # it does mean the screen no longer matches `_emitted`, so stop.
            logger.debug("Draft answer delta was not published", exc_info=True)
            self._halted = True

    def _safe_prose(self, *, flush: bool) -> str | None:
        """Return the prefix of the raw draft that is safe to humanize, if any."""

        fence = self._raw.find("```")
        if fence >= 0:
            # Prose ends at the fence. A fenced block inside the prose seals the
            # stream early: the rest of the answer then arrives with the
            # authoritative replay, which is correct but not live. That is the
            # conservative direction, and the alternative -- guessing which fence
            # is the grounding contract mid-stream -- is not safe.
            self._sealed = True
            return self._raw[:fence]
        body = self._raw if flush else self._raw[:-_DRAFT_FENCE_HOLDBACK]
        if not body:
            return None
        if flush:
            return body
        boundary = (
            body.rfind("\n")
            if self._suppress_structural
            else max(body.rfind(char) for char in " \t\n")
        )
        if boundary <= 0:
            return None
        return body[: boundary + 1]

    async def reset(self, *, notify: Callable[[], Awaitable[None]]) -> None:
        """Discard a draft that will not become the answer.

        Called when the model turns out to be calling a tool after narrating, and
        when the grounding validator sends a draft back for correction. Both mean
        the text on screen is not the answer, so the client is told to drop it
        rather than have the next draft appended to it. ``_halted`` is cleared:
        a discarded draft is not a reason to stop streaming the replacement.
        """

        had_text = bool(self._emitted)
        self._raw = ""
        self._emitted = ""
        self._sealed = False
        self._halted = False
        if had_text:
            await notify()


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
    #: Topology navigation targets observed this turn, in call order, verbatim.
    topology_targets: list[dict[str, Any]] = field(default_factory=list)
    evidence_summary: EvidenceSummary = field(default_factory=EvidenceSummary)


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
    # Timing is unconditional: it must be recorded on every turn, not only when
    # the canary-gated budget controller happens to be installed.
    middlewares: list[Any] = [ModelLatencyMiddleware(), middleware]
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
            extra_body={
                "thinking": {"type": "disabled"},
                "max_tokens": settings.llm_max_output_tokens,
            },
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
            # Previously unset on this branch alone, so a turn served by the
            # fallback provider had no output ceiling while the same turn on Kimi
            # was capped. One value now applies to every provider.
            max_tokens=settings.llm_max_output_tokens,
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
    heartbeat_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    emit_answer_deltas: bool = True,
    budget_controller: LayeredBudgetController | None = None,
    trusted_answer_sink: TrustedAnswerSink | None = None,
    suppress_structural_citations: bool = False,
    stream_draft_answer: bool = False,
) -> ReadOnlyTurnResult:
    """Run the read-only graph with a bounded grounding-correction loop.

    ``suppress_structural_citations`` is set when the turn came from the topology
    canvas; it reaches `_humanize_visible_content` and drops structural references
    from the prose the user reads. It never affects grounding validation, which
    reads the structured citation block instead.

    ``stream_draft_answer`` shows the model's answer prose while it is still being
    written, as provisional ``replayable=False`` deltas that the authoritative
    post-persistence replay overwrites. See :class:`_DraftAnswerStreamer`.
    """

    validator = GroundingValidator()
    prior = prior_response_fingerprints or set()
    messages: list[Any] = list(conversation_seed or [])
    messages.append({"role": "user", "content": user_message})
    # One reporter for the whole turn, so its elapsed clock is the turn's clock
    # and a grounding correction round does not restart the user's timer.
    progress = (
        _LiveProgressReporter(event_callback, heartbeat_callback=heartbeat_callback)
        if event_callback is not None
        else None
    )
    draft: _DraftAnswerStreamer | None = None
    draft_reset_callback: Callable[[], Awaitable[None]] | None = None
    if stream_draft_answer and heartbeat_callback is not None:
        # Provisional prose goes out on the heartbeat channel, exactly like the
        # trusted answer's live deltas: that channel writes `replayable=False` on
        # an independent session, which is both the "never replayed" property this
        # text requires and the only safe way to publish from inside a turn whose
        # own session may be mid-query.
        async def publish_draft(text: str) -> None:
            await heartbeat_callback("assistant.delta", {"content": text})

        async def notify_draft_reset() -> None:
            await heartbeat_callback("assistant.draft_reset", {"status": "discarded"})

        draft = _DraftAnswerStreamer(
            publish_draft, suppress_structural=suppress_structural_citations
        )
        draft_reset_callback = notify_draft_reset
    if progress is None:
        return await _run_readonly_attempts(
            graph=graph,
            ledger=ledger,
            snapshot=snapshot,
            messages=messages,
            validator=validator,
            prior=prior,
            state_version=state_version,
            max_corrections=max_corrections,
            event_callback=event_callback,
            emit_answer_deltas=emit_answer_deltas,
            budget_controller=budget_controller,
            trusted_answer_sink=trusted_answer_sink,
            progress=None,
            suppress_structural_citations=suppress_structural_citations,
            draft=draft,
            draft_reset_callback=draft_reset_callback,
        )
    # The ticker and the phase sink share one lifetime: nested work can report a
    # phase, and the ticker keeps that phase's clock visibly moving even when the
    # work makes no further announcements for half a minute.
    async with progress.running():
        with turn_progress_scope(progress.note_phase):
            return await _run_readonly_attempts(
                graph=graph,
                ledger=ledger,
                snapshot=snapshot,
                messages=messages,
                validator=validator,
                prior=prior,
                state_version=state_version,
                max_corrections=max_corrections,
                event_callback=event_callback,
                emit_answer_deltas=emit_answer_deltas,
                budget_controller=budget_controller,
                trusted_answer_sink=trusted_answer_sink,
                progress=progress,
                suppress_structural_citations=suppress_structural_citations,
                draft=draft,
                draft_reset_callback=draft_reset_callback,
            )


async def _run_readonly_attempts(
    *,
    graph: Runnable,
    ledger: SemanticEventLedger,
    snapshot: SemanticToolSnapshot,
    messages: list[Any],
    validator: GroundingValidator,
    prior: set[str],
    state_version: str,
    max_corrections: int,
    event_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
    emit_answer_deltas: bool,
    budget_controller: LayeredBudgetController | None,
    trusted_answer_sink: TrustedAnswerSink | None,
    progress: _LiveProgressReporter | None,
    suppress_structural_citations: bool = False,
    draft: _DraftAnswerStreamer | None = None,
    draft_reset_callback: Callable[[], Awaitable[None]] | None = None,
) -> ReadOnlyTurnResult:
    """The bounded attempt loop, split out so the caller owns progress lifetime."""

    usage = AgentUsage()
    envelopes: list[ToolEnvelope] = []
    ledger_len_before = len(ledger.events)
    grounded: GroundedAnswer | None = None
    outcome_verdict = GroundingVerdict.UNSAFE
    correction_rounds = 0
    outcome_reason_code = "not_evaluated"
    output_ceiling_hit = False

    budget_stop: BudgetStopReason | None = None
    for attempt in range(max_corrections + 1):
        try:
            result_state = await _invoke_graph(
                graph,
                {"messages": messages},
                event_callback=event_callback,
                trusted_answer_sink=trusted_answer_sink,
                progress=progress,
                draft=draft,
                draft_reset_callback=draft_reset_callback,
            )
        except AgentBudgetStop as exc:
            budget_stop = exc.reason
            outcome_verdict = GroundingVerdict.UNSAFE
            break
        new_messages = result_state.get("messages", [])
        messages = list(new_messages)
        # Reassigned each round, so a round that was cut off but then rewritten
        # by the correction loop does not leave a stale truncation mark behind.
        output_ceiling_hit = _hit_output_ceiling(new_messages)
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
            if progress is not None:
                # This path deliberately skips the grounding validator (the answer
                # is already deterministically cited), so claiming a validating
                # phase here would be a lie. Report what actually happens next.
                await progress.note_phase("finalizing_answer", force=True)
            break
        if progress is not None:
            # The validator gate is itself user-visible dead time on a long
            # answer; name it rather than leaving the status line stale.
            await progress.note_phase("validating_answer", force=True)
        allowed = validator.allowed_evidence(envelopes)
        requirement = validator.required_state(envelopes)
        final_text = _final_assistant_text(new_messages)
        grounded = _parse_grounded_answer(final_text, requirement=requirement)
        grounded = grounded.model_copy(
            update={
                "content": _humanize_visible_content(
                    grounded.content,
                    envelopes,
                    suppress_structural=suppress_structural_citations,
                )
            }
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
        if draft is not None and draft_reset_callback is not None:
            # The draft on screen was just rejected. Retract it before the rewrite
            # streams, or the client would append the corrected answer to the one
            # the validator refused.
            await draft.reset(notify=draft_reset_callback)
        # SYSTEM, not user. As a user turn this is indistinguishable from the
        # person actually saying "that answer was not accepted", so the model
        # answered the reprimand it thought it had received: every corrected
        # reply opened with "You're right - I should have been upfront about the
        # limitation" and conceded fault to someone who had said nothing. The
        # validator is internal machinery; it must not be able to put words in
        # the user's mouth. Same defect class as the unverified-replay note
        # leaking into answers (see agent_loop_context.strip_replay_note).
        messages.append(
            {
                "role": "system",
                "content": (
                    "Automated grounding check: the previous draft was not "
                    "accepted. "
                    + (outcome.correction_hint or "Revise it to be honestly grounded.")
                    + " Re-send the full answer and the grounding block."
                    " This notice is internal: do not acknowledge it, apologise"
                    " for it, or refer to it in the answer. Emit only the"
                    " corrected answer as if writing it for the first time."
                ),
            }
        )

    collected_targets = _collect_topology_targets(messages)
    read_observations = _collect_observations(ledger, ledger_len_before)
    evidence_observations = _collect_envelope_observations(messages, snapshot)
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
    # Marked here, after the correction loop has settled on the answer that will
    # actually be shown, so the notice reaches the fingerprint, the streamed
    # deltas and the persisted output as one consistent string. A fallback answer
    # replaces the truncated text entirely, so it is not marked.
    if output_ceiling_hit and not used_fallback:
        grounded = grounded.model_copy(
            update={"content": append_truncation_notice(grounded.content)}
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
        for chunk in public_answer_chunks(grounded.content):
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
        topology_targets=collected_targets,
        evidence_summary=build_evidence_summary(
            evidence_observations,
            document_source_count=len(document_sources),
            documentation_trace=(
                trusted_answer_sink.documentation_provider_trace
                if trusted_answer_sink is not None
                else None
            ),
        ),
    )


_VISIBLE_TOOL_LABELS = {
    "query_project_metadata": "project metadata",
    "search_documentation": "indexed documentation",
    "list_project_documents": "generated document inventory",
    "read_document": "indexed document",
    "read_generated_document": "generated document",
    "read_topology_structure": "the project topology",
}
_VISIBLE_EVIDENCE_KEY_RE = re.compile(
    r"(?:metadata:[a-z0-9_.:-]+|mcp-doc(?:-content)?:[a-z0-9_.:/-]+|doc:\d+:\d+"
    r"|topology:[a-z0-9_.:-]+)\b",
    re.IGNORECASE,
)

#: Every way a structural reference reaches the prose. Both halves matter: the
#: raw evidence key, AND the friendly label the humanizer would otherwise
#: substitute for it. In observed output the friendly form dominated -- five of
#: eight answers said "(from the project topology)" and only two leaked a raw id --
#: so suppressing keys alone would have missed the actual defect. The reader's
#: opaque node ids and the tool's own semantic name are included for the same
#: reason: relabelling them here would just reintroduce the phrase being removed.
_STRUCTURAL_REF = (
    r"(?:topology:[a-z0-9_.:-]+"
    r"|landscape-(?:application|system|group):[a-z0-9]+"
    r"|read_topology_structure"
    r"|the\s+project\s+topology)"
)

#: Wording models put in front of a reference. "topology node" and "node" are
#: here because the observed leak was "*from topology node landscape-...*", where
#: removing only the id leaves a dangling preposition.
_STRUCTURAL_LEADIN = (
    r"(?:(?:from|in|per|via|using|source|sources|evidence|evidence_id|evidence_ids|ref|refs)"
    r"(?:\s+topology\s+node|\s+node)?\s*:?\s*)?"
)

#: A whole bracketed aside whose only content is structural references. Deleting
#: just the reference would leave "(from )", which reads worse than the citation.
#: Anything else inside the brackets means real prose is in there too, and only
#: the reference itself is taken.
_STRUCTURAL_CITATION_PAREN_RE = re.compile(
    r"\s*[(\[]\s*[*_]{0,2}\s*"
    + _STRUCTURAL_LEADIN
    + _STRUCTURAL_REF
    + r"(?:\s*(?:[,;/&]|and)\s*"
    + _STRUCTURAL_REF
    + r")*"
    + r"\s*[*_]{0,2}\s*[)\]]",
    re.IGNORECASE,
)

#: The same reference sitting in running prose or a table cell rather than in
#: brackets, with its lead-in.
_STRUCTURAL_REFERENCE_RE = re.compile(
    r"[*_]{0,2}\s*" + _STRUCTURAL_LEADIN + _STRUCTURAL_REF + r"\s*[*_]{0,2}",
    re.IGNORECASE,
)

#: Debris a removal can strand: an emptied bracket, a conjunction or separator now
#: leading the bracket it used to join inside, orphaned emphasis markers, and the
#: em-dash or colon that used to introduce the reference at end of line or cell.
_SUPPRESSION_CLEANUPS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[(\[]\s*(?:and|,|;|/|&)\s*"), "("),
    (re.compile(r"[(\[]\s*[)\]]"), ""),
    (re.compile(r"\s*[-\u2013\u2014:]\s*(?=\|)"), " "),
    (re.compile(r"\s*[-\u2013\u2014:]\s*$", re.MULTILINE), ""),
    (re.compile(r"[ \t]{2,}"), " "),
    (re.compile(r" +([,.;:)\]])"), r"\1"),
    (re.compile(r"\|\s+\|"), "| |"),
)


def _humanize_visible_content(
    content: str, envelopes: list[ToolEnvelope], *, suppress_structural: bool = False
) -> str:
    """Render model-visible implementation references as human source labels.

    Machine evidence keys remain untouched in the parsed grounding block and
    therefore retain citation integrity. Only the user-facing prose is changed.

    ``suppress_structural`` removes structural references from the prose instead of
    relabelling them, and is set when the turn came from the topology canvas. There
    the label is tautology -- the user is looking at the artifact, so "(from the
    project topology)" states only that a topology fact came from the topology, and
    it does so in the space the explanation needed.

    Suppression happens HERE rather than by prompt rule alone because the prompt
    cannot win this argument on its own. `GroundingValidator` rejects a materially
    factual grounded answer that cites nothing, so a model told "do not cite" is
    also being told "cite or your answer is discarded" and will hedge by citing in
    both places. The validator reads the structured grounding block, never this
    prose, so dropping references here costs no citation integrity: the answer is
    validated first and only then humanized for display.

    Only the topology surface suppresses. In plain chat there is no diagram on
    screen, so the same label is the only thing separating published structure from
    a document or a log line, which is why the default is ``False``.
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
        # On the canvas the topology reader's own name is suppressed with its
        # references; relabelling it to "the project topology" would reintroduce
        # exactly the phrase being removed.
        if suppress_structural and semantic_name == "read_topology_structure":
            continue
        visible = re.sub(rf"\b{re.escape(semantic_name)}\b", label, visible)

    if suppress_structural:
        # Order matters: whole brackets first, so "(from topology:x)" goes entirely
        # rather than collapsing to "(from )"; then references left in running
        # prose; then the debris either pass can strand.
        visible = _STRUCTURAL_CITATION_PAREN_RE.sub("", visible)
        visible = _STRUCTURAL_REFERENCE_RE.sub("", visible)
        for pattern, replacement in _SUPPRESSION_CLEANUPS:
            visible = pattern.sub(replacement, visible)
        return visible.strip()

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
        if lowered.startswith("topology:"):
            return "the project topology"
        return "indexed document passage"

    return _VISIBLE_EVIDENCE_KEY_RE.sub(evidence_label, visible)


async def _invoke_graph(
    graph: Runnable,
    input_value: dict[str, Any],
    *,
    event_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
    trusted_answer_sink: TrustedAnswerSink | None = None,
    progress: _LiveProgressReporter | None = None,
    draft: _DraftAnswerStreamer | None = None,
    draft_reset_callback: Callable[[], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    if trusted_answer_sink is not None and trusted_answer_sink.answer is not None:
        return {"messages": list(input_value.get("messages", []))}
    if event_callback is None:
        return await graph.ainvoke(input_value)
    seen_messages: list[BaseMessage] = []
    finalized_messages: list[BaseMessage] = []
    proposed_call_refs: set[str] = set()
    #: Evidence titles the draft humanizer needs, accumulated as tool results
    #: arrive. Re-deriving this from `seen_messages` on every model chunk would
    #: re-parse every tool payload hundreds of times over one answer.
    draft_envelopes: list[ToolEnvelope] = []
    if progress is None:
        progress = _LiveProgressReporter(event_callback)
    await progress.note_phase("thinking", force=True)

    def completed_state() -> dict[str, Any]:
        """Return the full graph state, not only this invocation's deltas.

        LangGraph's ``updates`` stream contains node deltas. A grounding
        correction invokes the graph again with the prior messages as input; if
        this function returns only the new deltas, the caller replaces the turn
        history and loses every successful ToolMessage (and therefore its
        evidence) from the first attempt.
        """

        prior_messages = list(input_value.get("messages", []))
        produced_messages = finalized_messages or seen_messages
        return {"messages": [*prior_messages, *produced_messages]}

    async def discard_draft() -> None:
        """Drop provisional prose that turned out not to be the answer."""

        if draft is None or draft_reset_callback is None:
            return
        await draft.reset(notify=draft_reset_callback)

    async def emit_proposals(message: AIMessage) -> None:
        fresh: list[tuple[str, str, dict[str, Any]]] = []
        for call in message.tool_calls:
            name = str(call.get("name") or "").strip()
            call_ref = str(call.get("id") or "").strip()
            if not name or not call_ref or call_ref in proposed_call_refs:
                continue
            proposed_call_refs.add(call_ref)
            fresh.append((name, call_ref, dict(call.get("args") or {})))
        if not fresh:
            return
        # Prose that preceded a tool call was narration, not the answer: the model
        # is still gathering evidence and will write its answer afterwards. Retract
        # it rather than let the real answer be appended to it.
        await discard_draft()
        # One phase for the whole batch, decided before the first proposal goes
        # out: the calls in it run together, so naming them one at a time would
        # leave the status line on whichever happened to be last.
        await progress.note_state(_read_batch_phase(fresh), force=True)
        for name, call_ref, arguments in fresh:
            await event_callback(
                "tool.proposed",
                {
                    "tool_label": name.replace("_", " ")[:120],
                    "semantic_name": name,
                    "status": "proposed",
                    "call_ref": call_ref[:160],
                    # Private callback-only data. PublicAgentEvent sanitization
                    # removes it before persistence/SSE projection.
                    "arguments": arguments,
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
                # complete, so a chunk's *arguments* are never published -- the
                # finalized model update below is authoritative there. A chunk's
                # answer prose is different: `_DraftAnswerStreamer` publishes it
                # provisionally (see its docstring for what that costs and what
                # guards it), and the throttled server-authored progress event
                # still carries liveness when there is no prose yet.
                await progress.note_model_chunk(message)
                if draft is not None and not message.tool_call_chunks:
                    await draft.feed(_safe_text(message.content), draft_envelopes)
                continue
            if isinstance(message, AIMessage) and message.tool_calls:
                await emit_proposals(message)
            elif isinstance(message, ToolMessage):
                draft_envelopes.extend(_collect_envelopes([message]))
                envelope = await _emit_tool_event(message, event_callback)
                if (
                    trusted_answer_sink is not None
                    and trusted_answer_sink.answer is not None
                ):
                    # ``answer_from_documentation`` delegates to the complete
                    # deterministic RAG pipeline. Once that tool has produced a
                    # trusted answer and its completion event is visible, the
                    # answer is terminal: do not ask the outer agent model to
                    # rewrite it or risk discarding it at the turn deadline.
                    return completed_state()
                await progress.note_state(_post_read_phase(envelope), force=True)
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
                return completed_state()
    if draft is not None:
        # The draft is complete. Validation and persistence still have to run, and
        # that is visible dead time, so show the last partial batch now instead of
        # holding it for a batch size it will never reach.
        await draft.feed("", draft_envelopes, flush=True)
    return completed_state()


async def _emit_tool_event(
    message: ToolMessage,
    event_callback: Callable[[str, dict[str, Any]], Awaitable[None]],
) -> ToolEnvelope | None:
    """Publish one tool result and return its envelope for progress reporting.

    ``None`` means there is no safe observation to describe: the assessment tool,
    or a result that would not parse.
    """

    if str(getattr(message, "name", "")) == TURN_ASSESSMENT_TOOL_NAME:
        return None
    try:
        envelope = ToolEnvelope.model_validate_json(str(message.content))
    except ValueError:
        await event_callback(
            "tool.completed",
            {"tool_label": "Tool", "status": "failed", "summary": "The tool returned an invalid safe observation."},
        )
        return None
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
        # Topology navigation targets, verbatim. The canvas consumes them live to
        # highlight and navigate; they are also persisted on the assistant message
        # so the non-streaming transport and a reload see the same targets.
        targets = topology_targets(envelope.data)
        if targets:
            payload["targets"] = targets
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
    return envelope


def public_answer_chunks(content: str, *, size: int = 96) -> list[str]:
    """Slice one *already validated* answer into transport-sized deltas.

    Answer text is only ever chunked after the grounding validator has accepted
    it. Slicing is a transport concern, not a generation one -- these are not
    model deltas, and nothing here may be fed unvalidated draft prose.
    """

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
    """Fold this attempt's model token usage into the turn's running total.

    Carries the cache breakdown, not just the two headline counts: the prompt
    prefix is what a multi-tool turn re-sends on every model call, so on this path
    most of ``input_tokens`` is typically ``cached_input_tokens``.
    """

    tokens = token_usage_from_messages(messages)
    return AgentUsage(
        input_tokens=usage.input_tokens + tokens.input_tokens,
        output_tokens=usage.output_tokens + tokens.output_tokens,
        cached_input_tokens=usage.cached_input_tokens + tokens.cached_input_tokens,
        cache_write_tokens=usage.cache_write_tokens + tokens.cache_write_tokens,
    )


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


def _collect_topology_targets(messages: list[Any]) -> list[dict[str, Any]]:
    """Collect this turn's topology navigation targets in call order.

    Matched on the topology tool's own semantic name rather than on payload shape,
    so no other tool's data can be mistaken for navigation targets. Ids are passed
    through verbatim — the two id spaces (overlay and scanner) have no bridge, so
    normalizing or re-deriving an id here would produce a chip that navigates
    nowhere. Duplicates across calls are collapsed, order preserved.
    """

    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        if str(getattr(message, "name", "")) != TOPOLOGY_STRUCTURE_CAPABILITY.semantic_name:
            continue
        try:
            payload = json.loads(str(message.content))
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        for target in topology_targets(payload.get("data")):
            key = json.dumps(target, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            collected.append(target)
    return collected


def _hit_output_ceiling(messages: list[Any]) -> bool:
    """True when the terminal assistant message stopped at the output ceiling.

    The agent path does not go through ``src.generation.providers``; LangChain
    surfaces the same provider signal on ``response_metadata``. Without this, a
    turn whose answer was cut off mid-sentence is recorded as ``status=completed``
    exactly like a finished one.
    """
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls:
            metadata = getattr(message, "response_metadata", None) or {}
            if not isinstance(metadata, dict):
                return False
            return metadata.get("finish_reason") == "length"
    return False


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


def _collect_envelope_observations(
    messages: list[Any], snapshot: SemanticToolSnapshot
) -> list[CapabilityObservation]:
    """Rehydrate sanitized current-turn tool envelopes for provenance only.

    Conversation seeds contain plain role/content dictionaries, whereas executed
    tools are ``ToolMessage`` instances. Consequently this cannot inherit an old
    turn's evidence or failure even when the prose history mentions one.
    """

    observations: list[CapabilityObservation] = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        try:
            envelope = ToolEnvelope.model_validate_json(str(message.content))
        except ValueError:
            continue
        semantic_name = str(getattr(message, "name", "") or "")
        semantic_to_canonical = getattr(snapshot, "semantic_to_canonical", {})
        capability_id = semantic_to_canonical.get(semantic_name, semantic_name or "unknown")
        status = (
            CapabilityObservationStatus.SUCCEEDED
            if envelope.status in {"succeeded", "triggered"}
            else CapabilityObservationStatus.NEEDS_INPUT
            if envelope.status in {"needs_input", "correction_required", "validation_failed"}
            else CapabilityObservationStatus.DENIED
            if envelope.status in {"denied", "approval_required"}
            else CapabilityObservationStatus.FAILED
        )
        error_code = envelope.error.get("code") if envelope.error else None
        observations.append(
            CapabilityObservation(
                capability_id=capability_id,
                status=status,
                summary=envelope.summary,
                data=envelope.data,
                evidence_refs=envelope.evidence_refs,
                retryable=envelope.retryable,
                error_code=error_code,
                error_message=(
                    envelope.summary
                    if status.value not in {"succeeded", "triggered"}
                    else None
                ),
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
    "PROGRESS_INTERVAL_SECONDS",
    "READONLY_MODEL_PROFILE",
    "READONLY_PROMPT_VERSION",
    "READONLY_RUNTIME_RELEASE",
    "READ_ONLY_PRIMARY_SYSTEM_PROMPT",
    "ReadOnlyPrimaryRuntime",
    "ReadOnlyTurnResult",
    "build_readonly_primary_chat_model",
    "build_readonly_primary_runtime",
    "grounding_failure_answer",
    "public_answer_chunks",
    "readonly_manifest_partition",
    "run_readonly_primary_turn",
]
