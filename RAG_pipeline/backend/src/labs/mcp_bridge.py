"""MCP Streamable HTTP bridge over the Nexus capability registry.

The OpenCode runtime talks MCP; Nexus talks capabilities. This module is the
only translation layer between the two, and it is deliberately generic: every
tool it advertises is derived from `CapabilityRegistry` at request time, and
every call it accepts is executed by the same `HarnessCapabilityExecutor` that
`/chat` uses. No capability is hand-wired here, and no policy decision is taken
here -- permissions, risk tier, JSON-schema and project scope are all enforced
by `CapabilityPolicyEngine` inside the executor.

The bearer token is the RBAC boundary. OpenCode never states who it is acting
for: it presents a token Nexus minted for one turn, and the server rebuilds the
entire `CapabilityExecutionContext` from that token's scope. Identity, project
scope and permissions are never read from the JSON-RPC params.

There is no MCP SDK in this project's dependency set, so the protocol is
implemented directly: JSON-RPC 2.0 over HTTP POST, answered with plain JSON
(a valid Streamable HTTP response -- SSE is optional for servers).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any, Final

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.dependencies import DBSession, QdrantClient
from src.db.models import Conversation
from src.harness.agent_chat_runtime import (
    ProductionAgentTurnRuntime,
    build_production_agent_turn_runtime,
)
from src.harness.capability_runtime import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    CapabilityObservation,
    CapabilityObservationStatus,
)
from src.harness.executor import CapabilityExecutionDenied, CapabilityPolicyCode
from src.harness.models import CapabilityDefinition, HarnessRiskTier
from src.harness.registry import base_capability_registry
from src.labs.contracts import (
    LABS_MCP_ROUTE,
    LabsToolCallRecord,
    LabsTurnMode,
    LabsTurnScope,
    labs_token_registry,
    labs_turn_budget,
    labs_turn_claims,
    labs_turn_modes,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["labs"])


MCP_PROTOCOL_VERSION: Final = "2025-06-18"
MCP_SERVER_NAME: Final = "nexus-capabilities"
MCP_SERVER_VERSION: Final = "0.1.0"

# JSON-RPC 2.0 reserved error codes.
_PARSE_ERROR: Final = -32700
_INVALID_REQUEST: Final = -32600
_METHOD_NOT_FOUND: Final = -32601
_INVALID_PARAMS: Final = -32602
_INTERNAL_ERROR: Final = -32603

# MCP tool names must match ^[a-zA-Z0-9_-]{1,64}$; capability ids are dotted.
_TOOL_NAME_ILLEGAL = re.compile(r"[^a-zA-Z0-9_-]")
_MAX_TOOL_NAME_LENGTH: Final = 64

# Per-tool description budget. Descriptions are re-sent on every model call, so
# this is a direct per-turn cost; measured at 4,200 tokens unbounded across 20
# tools. The purpose line is exempt -- only the guidance clauses are trimmed.
_MAX_DESCRIPTION_CHARS: Final = 420
# The purpose line is what a model picks on, but several run to thousands of
# characters; keep the leading sentences only.
_MAX_PURPOSE_CHARS: Final = 320

# Every Labs capability returns the same sanitized observation envelope.  MCP's
# ``outputSchema`` applies to ``structuredContent`` rather than the outer tool
# result, so publish that envelope once and return it alongside the text form
# retained for clients that only understand content blocks.
_TOOL_OUTPUT_SCHEMA: Final = {
    "type": "object",
    "required": ["capability_id", "status"],
    "properties": {
        "capability_id": {"type": "string"},
        "status": {
            "type": "string",
            "enum": ["succeeded", "triggered", "failed", "denied", "needs_input"],
        },
        "output_type": {"type": ["string", "null"]},
        "summary": {"type": ["string", "null"]},
        "data": {"type": "object"},
        "artifact_links": {"type": "array"},
        "evidence_refs": {"type": "array"},
        "retryable": {"type": "boolean"},
        "error_code": {"type": ["string", "null"]},
        "error_message": {"type": ["string", "null"]},
        "workflow_ref": {"type": ["object", "null"]},
        "receipt_ref": {"type": ["object", "null"]},
        "safe_details": {"type": "object"},
        "task_ref": {"type": ["object", "null"]},
        "_lane_note": {"type": "string"},
        "remaining_tool_calls_this_turn": {"type": "integer", "minimum": 0},
        "tool_calls_per_turn": {"type": "integer", "minimum": 1},
        "_budget_note": {"type": "string"},
        "field_path": {"type": ["string", "null"]},
    },
    "additionalProperties": True,
}

# Evidence ledger bounds. A conversation's entry is removed when the turn drains
# it, so the map is normally one entry per in-flight turn; a turn that dies
# before draining must not be able to grow it without limit.
_MAX_RECORDS_PER_CONVERSATION: Final = 200
_MAX_TRACKED_CONVERSATIONS: Final = 64

# conversation_id -> the capability calls that conversation's turn has made.
#
# Keyed by conversation, NOT by the MCP bearer token, and that is a correction.
# A token is a credential with its own lifecycle: it carries an 8-hour expiry,
# it is re-minted whenever `_resolve_binding` cannot reuse the one it holds, and
# OpenCode caches whichever value it read out of `opencode.json` when it
# connected. So the two sides can legitimately disagree about which token is
# current -- the bridge records under the token the client presented while the
# runtime drains under the token it just minted, or the reverse -- and every
# record written under the other value was silently unreachable. Measured: two
# live turns logged zero capability calls while the event bus had seen four and
# five, with no `tool.completed` frames and no evidence refs for either.
#
# The conversation is what the records actually belong to and is the one
# identifier both sides already agree on: it is inside the signed token, so the
# bridge reads it from `scope.conversation_id` no matter which token arrived,
# and the runtime holds it directly. Keying an audit trail by a credential meant
# a routine credential rotation destroyed it.
_tool_calls: dict[int, list[LabsToolCallRecord]] = {}


class LabsToolNameCollision(RuntimeError):
    """Two capabilities projected onto the same MCP tool name."""


class LabsScopeError(RuntimeError):
    """The turn scope no longer resolves to an authorized conversation."""


class _JsonRpcError(Exception):
    """A JSON-RPC error raised from a method handler and rendered by dispatch."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --- Evidence ledger -------------------------------------------------------


def drain_tool_calls(conversation_id: int) -> list[LabsToolCallRecord]:
    """Return and clear every capability call recorded for ``conversation_id``.

    The ledger and the tool BUDGET are now keyed alike -- both by conversation --
    but they still answer different questions and are still separate objects.
    The budget lives in `labs_turn_budget` and is reset at the turn boundary;
    draining the ledger does not touch it, so a mid-turn drain for incremental
    grounding cannot hand the runtime a fresh allowance.
    """

    return _tool_calls.pop(conversation_id, [])


def _record_tool_call(conversation_id: int, record: LabsToolCallRecord) -> None:
    """Append one capability call to the conversation's ledger and count it.

    See `_tool_calls` for why the key is the conversation rather than the bearer
    token the call arrived on.
    """

    if (
        conversation_id not in _tool_calls
        and len(_tool_calls) >= _MAX_TRACKED_CONVERSATIONS
    ):
        _tool_calls.pop(next(iter(_tool_calls)), None)
    records = _tool_calls.setdefault(conversation_id, [])
    records.append(record)
    if len(records) > _MAX_RECORDS_PER_CONVERSATION:
        del records[: len(records) - _MAX_RECORDS_PER_CONVERSATION]
    labs_turn_budget.consume(conversation_id)


# --- Tool projection -------------------------------------------------------


# Tiers this lane will advertise.
#
# WRITES_CONTENT and EXTERNAL_SIDE_EFFECT are deliberately absent. Both are
# gated in `CapabilityPolicyEngine._validate_risk` by context flags this bridge
# leaves False, so advertising them would only offer the model tools that always
# deny -- and enabling them would mean an LLM could rewrite published client
# documentation or attach an external source, which is not a decision this lane
# gets to make on its own.
_ADVERTISED_TIERS: Final = frozenset(
    {
        HarnessRiskTier.READ_ONLY,
        HarnessRiskTier.REVERSIBLE_WRITE,
        HarnessRiskTier.TRIGGERS_WORKFLOW,
    }
)

# Human-readable risk marker, so a model choosing between tools can tell a read
# from something that changes state.
_TIER_MARKERS: Final = {
    HarnessRiskTier.READ_ONLY: "[read-only]",
    HarnessRiskTier.REVERSIBLE_WRITE: "[reversible write]",
    HarnessRiskTier.TRIGGERS_WORKFLOW: "[starts a long-running workflow]",
    HarnessRiskTier.WRITES_CONTENT: "[writes published content]",
    HarnessRiskTier.EXTERNAL_SIDE_EFFECT: "[external side effect]",
}


# The lane's tool allowlist.
#
# Every advertised tool's schema is re-sent on every model call, so the list is a
# per-turn tax: 36 capabilities cost ~7,000 tokens before the user's question is
# read. This set is what earned its place across a day of measured use, and the
# omissions are deliberate rather than accidental:
#
#   * `rag.docs.answer_question` is excluded as actively harmful. It runs a
#     nested retrieval-and-generation pipeline and returns finished prose, which
#     doubles turn latency and summarises away the specifics -- table names,
#     component ids, truncation limits -- that make an answer worth reading. An
#     agent needs evidence, not someone else's conclusions.
#   * The seven `nexus.agent_plan.*` capabilities are excluded: zero calls across
#     every thread, including multi-step pack building. An agent loop already
#     holds a plan in context; explicit plan objects were built for a runtime
#     that could not.
#   * `mcp.projects.trigger_scan` and `publish_index` are excluded pending a
#     policy decision, not a technical one -- both let a model act on a client
#     estate on request.
#   * Read-only housekeeping (`read_status`, `list_source_options`,
#     `list_active_workflows`, `read_summary_facts`) is excluded as thin: the
#     session already carries conversation history, and the rest is admin surface
#     rather than anything a chat answer needs.
#
# Set LABS_CAPABILITY_ALLOWLIST to a comma-separated list to override, or to
# `*` to advertise everything the tier and permission filters already allow.
_DEFAULT_ALLOWLIST: Final = frozenset(
    {
        # Core reads -- 97 of 107 measured calls came from these.
        "rag.docs.search_evidence",
        # Advertised as `search_within_document`: a ranked search inside one
        # document, which is what it has always been.
        "rag.docs.read_document",
        # The ordered document read, advertised as `read_document`. Added
        # because its absence is what forced every completeness question -- how
        # many fields, list every step -- through similarity chunks, which
        # cannot answer one. See `DOCUMENT_FULL_TEXT_CAPABILITY`.
        "rag.docs.read_document_text",
        "runtime.stored.query_evidence",
        "runtime.logs.search_evidence",
        "nexus.metadata.query",
        # Packs.
        "nexus.docs.list_documentation_packs",
        "nexus.packs.create",
        "nexus.packs.drafts.create",
        "nexus.packs.drafts.refine",
        "nexus.packs.drafts.validate",
        "nexus.packs.drafts.read",
        # Documentation generation, and the only way to ask whether it finished.
        "mcp.docs.generate_project_documentation",
        "mcp.docs.get_generation_status",
        "mcp.docs.list_project_docs",
        "mcp.docs.read_project_doc",
        # Long work that must outlive one turn. Both pack building and doc
        # generation have exceeded a turn deadline in practice.
        "nexus.conversation_tasks.background_conversation_task",
        "nexus.conversation_tasks.pause_conversation_task",
        "nexus.conversation_tasks.resume_conversation_task",
        # Onboarding a project conversationally.
        "nexus.workflow.onboard_project",
        "nexus.workflow.resume_onboarding",
    }
)

# Labs chat is deliberately documentation-first.  The topology capability stays
# registered for the topology canvas and other dedicated surfaces, but it must
# never be projected through this bridge -- not even when an operator sets
# LABS_CAPABILITY_ALLOWLIST="*".  Keeping this as a hard exclusion avoids an
# environment override silently restoring the exploratory topology loop that
# this lane is intended to prevent.
_LABS_EXCLUDED_CAPABILITY_IDS: Final = frozenset(
    {"topology.landscape.read_structure"}
)

# The raw scan. Executable only in `LabsTurnMode.DEEP_DIVE`.
#
# These read the scanner's own output rather than the generated documentation,
# so they carry what the documentation summarises away -- component parameters,
# expressions, conditions and the literal SQL. That is the whole reason the mode
# exists, and also the whole reason they are gated: the scan is large, the reads
# are slow, and an ungated scan tool makes every routine "what does this job do"
# question pay for a source search it never needed.
#
# Held apart from `_DEFAULT_ALLOWLIST` rather than added to it, for two reasons:
# the allowlist is the CHAT menu and these are deliberately not in it, and
# `_DEFAULT_ALLOWLIST` is asserted to be a subset of the live registry, which
# these ids only become once the capabilities themselves are registered.
_DEEP_DIVE_CAPABILITY_IDS: Final = frozenset(
    {
        # Advertised as `search_source`.
        "intodoc.source.search",
        # Advertised as `read_component`.
        "intodoc.source.read_component",
    }
)

# What a chat turn is told when it calls a scan tool it can see but may not run.
#
# A bare "Denied by policy: allowlist" is what the executor's own gate produces,
# and it reads to a model as a broken tool: it says nothing about WHY, so the
# model either retries the same call or goes looking for the source with the
# filesystem tools -- measured at 44 seconds of `glob` on conversation 661 for
# an answer it never reached. This text names the reason, names the remedy, and
# says who can apply it, which is the same thing the chat system prompt says.
_MODE_REFUSAL_MESSAGE: Final = (
    "This tool reads the scanned source, which is only available in Deep Dive, "
    "and this is an ordinary chat turn. Do not retry it and do not go looking "
    "for the source with file tools -- it is not on this filesystem. Answer "
    "from the documentation and runtime evidence you can reach, say plainly "
    "what the documentation does not record, and tell the user that turning on "
    "Deep Dive and asking again would let you read the source. You cannot "
    "switch mode yourself."
)


def _allowlist() -> frozenset[str] | None:
    """Every capability id this lane may ADVERTISE, or None when all are allowed.

    The turn's mode is deliberately not an input, and that is the correction.
    OpenCode asks for `tools/list` once, when the session is created, and caches
    the answer for the life of the session -- measured on conversation 661, one
    `tools/list` served three turns. A mode-filtered catalogue therefore froze at
    whatever the conversation's FIRST turn happened to be: a thread that opened
    in chat could never be offered the scan again, and a thread that opened in
    deep dive kept offering it forever.

    So the advertised set is a superset of every mode, always. What a mode may
    actually RUN is decided per call by `_excluded_capability_ids`, which is
    reached through `tools/call` -- a request the client makes every single time,
    and therefore the only place a per-turn decision can live.

    `LABS_CAPABILITY_ALLOWLIST="*"` still means "advertise everything the tier
    and permission filters already allow"; it cannot widen what a chat turn may
    execute, because that gate is elsewhere.
    """

    raw = os.environ.get("LABS_CAPABILITY_ALLOWLIST", "").strip()
    if raw == "*":
        return None
    configured = (
        _DEFAULT_ALLOWLIST
        if not raw
        else frozenset(item.strip() for item in raw.split(",") if item.strip())
    )
    # Unconditional: the catalogue has to carry the scan whatever mode the
    # session was created in, or a later deep-dive turn has nothing to enable.
    return configured | _DEEP_DIVE_CAPABILITY_IDS


def _excluded_capability_ids(mode: LabsTurnMode) -> frozenset[str]:
    """Ids this mode must never EXECUTE, whatever the catalogue advertises.

    This is the server-side half of the mode, and since the catalogue stopped
    filtering it is the whole of it: the agent definition in `opencode.json`
    switches the scan tools off for the chat agent, but that is a client-side
    hint the model's runtime applies to itself. A hint is not a boundary. This
    function is, and it already earned its place -- on conversation 657 a
    deep-dive-then-chat thread had the scan tools still in its cached menu, the
    model called one, and this gate is what refused it.

    Merged with `_LABS_EXCLUDED_CAPABILITY_IDS` because the argument is the same
    one: an operator setting `LABS_CAPABILITY_ALLOWLIST="*"` must not be able to
    put the scan into ordinary chat by accident.
    """

    if mode is LabsTurnMode.DEEP_DIVE:
        return _LABS_EXCLUDED_CAPABILITY_IDS
    return _LABS_EXCLUDED_CAPABILITY_IDS | _DEEP_DIVE_CAPABILITY_IDS


def deep_dive_tool_names() -> tuple[str, ...]:
    """The MCP tool names the scan capabilities are advertised under.

    Exists so `workspace.py` can switch exactly these tools off in the chat
    agent's `tools` map without restating the names. It must not: the map is
    keyed by the name the MODEL sees, which is this projection of
    `semantic_name`, not the capability id -- and a `tools` entry naming a tool
    that does not exist is accepted silently and does nothing at all. Deriving
    it from the same registry and the same `_tool_name_for` that `tools/list`
    uses is what makes "wrong name" impossible rather than merely unlikely.

    Returned WITHOUT the MCP server-key prefix. The prefix is `opencode.json`'s
    own key for this server, which is workspace's to own; the bare name is the
    part this module is authoritative about.

    An id that the registry does not carry yields no name. That is the honest
    result -- the bridge cannot advertise it either, so there is nothing for an
    agent map to switch -- and the caller warns about it rather than this
    function raising, because a scan provider that has not landed must not stop
    the lane from answering documentation questions.
    """

    registered = {
        capability.id: capability for capability in base_capability_registry().list()
    }
    return tuple(
        sorted(
            _tool_name_for(registered[capability_id])
            for capability_id in _DEEP_DIVE_CAPABILITY_IDS
            if capability_id in registered
        )
    )


def _turn_mode(scope: LabsTurnScope) -> LabsTurnMode:
    """The evidence mode of the turn this request belongs to.

    Two sources, and the order matters. `labs_turn_modes` holds the live value,
    set at the turn boundary in the request that is running the turn; the
    scope's own `mode` is what the conversation's token was minted under. The
    live value wins because the token is minted once per conversation and cached
    by OpenCode for eight hours, so a thread whose first turn was chat would
    otherwise never be able to run a deep one.

    This is the same resolution `_remaining_tool_calls` already performs for the
    per-turn allowance, and it is safe for the same reason the claim lookup
    below it is: the bridge answers `tools/call` inside the turn that set the
    entry. Outside one -- which should not happen -- the token's own mode is the
    floor, and that defaults to chat.

    `tools/list` reads it only to log it. The catalogue itself no longer depends
    on the mode, because that request is answered once per session and cached;
    see `_allowlist`.
    """

    return labs_turn_modes.current(scope.conversation_id, scope.mode)


def _requires_unavailable_precondition(
    capability: CapabilityDefinition, *, turn_claim_bound: bool
) -> bool:
    """True when the executor would deny this capability for this lane regardless.

    Two preconditions, and only one of them is now unconditional:

    * `requires_turn_claim` -- the harness's per-conversation concurrency lock,
      held via `harness_conversation_turn_claims`. The turn acquires one before
      the model runs (`src/labs/runtime.py::labs_turn_claim`) and publishes it
      through `labs_turn_claims`, so these capabilities are advertised while a
      claim is bound. When acquisition lost a race the turn still runs, without
      the lock, and they go back to being filtered -- an advertised tool that
      can only answer TURN_CLAIM is worse than an absent one.
    * `requires_explicit_approval` -- needs the capability id in
      `context.approvals`, which is populated by the approval flow this lane
      does not run. Still unconditionally filtered.
    """

    metadata = capability.metadata
    if metadata.get("requires_explicit_approval") is True:
        return True
    return metadata.get("requires_turn_claim") is True and not turn_claim_bound


def _visible_capabilities(
    runtime: ProductionAgentTurnRuntime, scope: LabsTurnScope
) -> list[CapabilityDefinition]:
    """Capabilities this turn's permissions cover and the executor can actually run.

    This is a projection, not the authority: the executor re-checks permissions,
    risk, schema and project scope on every call. Filtering here keeps the model
    from being offered tools it would only be denied.

    The turn's `LabsTurnMode` is deliberately NOT one of the inputs any more. It
    used to be, and that was the bug: this function backs `tools/list`, OpenCode
    asks for that once per session, and a mode-filtered catalogue therefore
    froze at the mode of the conversation's first turn. The set returned here is
    the union of every mode, and the mode is applied per call in
    `_handle_tools_call`, which runs every time.

    The cost of the wider catalogue is two extra tool schemas in every turn's
    prompt. That is real and it is small, and it buys the only shape in which a
    per-turn mode works at all against a client that caches the menu.
    """

    permissions = set(scope.permissions)
    allowed = _allowlist()
    # Only the hard exclusion. The mode's own exclusions are an EXECUTION rule,
    # and applying them here is what froze the menu -- see the docstring.
    excluded = _LABS_EXCLUDED_CAPABILITY_IDS
    turn_claim_bound = labs_turn_claims.get(scope.conversation_id) is not None
    return [
        capability
        for capability in runtime.registry.list(enabled_only=True)
        if capability.risk_tier in _ADVERTISED_TIERS
        and capability.id not in excluded
        and (
            allowed is None
            or capability.id in allowed
            # Connector ids are created from healthy, project-scoped bindings
            # at request time, so they cannot appear in the static allowlist.
            # Admitting this governed provider family keeps every other dynamic
            # capability closed and does not bypass the executor's scope,
            # permission, schema, or risk checks.
            or capability.provider_id == "nexus.connector"
        )
        and not _requires_unavailable_precondition(
            capability, turn_claim_bound=turn_claim_bound
        )
        and set(capability.required_permissions) <= permissions
    ]


def _tool_name_for(capability: CapabilityDefinition) -> str:
    raw = capability.semantic_name or capability.id
    name = _TOOL_NAME_ILLEGAL.sub("_", raw)[:_MAX_TOOL_NAME_LENGTH]
    if not name:
        raise LabsToolNameCollision(
            f"Capability {capability.id} has no projectable MCP tool name"
        )
    return name


def _tool_name_map(capabilities: list[CapabilityDefinition]) -> dict[str, str]:
    """Bidirectional-by-construction map of MCP tool name -> capability id.

    A collision is raised rather than resolved: silently shadowing one
    capability with another would route a call to the wrong executor binding.
    """

    mapping: dict[str, str] = {}
    for capability in capabilities:
        name = _tool_name_for(capability)
        existing = mapping.get(name)
        if existing is not None and existing != capability.id:
            raise LabsToolNameCollision(
                f"MCP tool name {name!r} maps to both {existing} and {capability.id}"
            )
        mapping[name] = capability.id
    return mapping


def _first_sentences(text: str, budget: int = _MAX_PURPOSE_CHARS) -> str:
    """The leading sentences of a purpose, up to a character budget.

    Several registry purposes run to thousands of characters -- the worst is
    4,995 -- because they document ownership boundaries between capabilities for
    a planner that read them once. The opening sentence is what a model needs to
    pick a tool; the rest is re-sent on every call for no benefit. Cut on a
    sentence boundary so the text never ends mid-clause.
    """

    text = " ".join(text.split())
    if len(text) <= budget:
        return text
    kept: list[str] = []
    used = 0
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if used + len(sentence) > budget and kept:
            break
        kept.append(sentence)
        used += len(sentence) + 1
    return " ".join(kept) if kept else text[:budget].rstrip()


def _tool_description(capability: CapabilityDefinition) -> str:
    """Compose a tool description, bounded.

    Every advertised tool's description is re-sent on every model call, so this
    text is a per-turn tax. The registry's `use_when` / `do_not_use_when` prose
    was written for a single-shot planner that read it once; unbounded, the 20
    advertised tools cost ~4,200 tokens of description alone, and one capability
    (`read_topology_structure`) accounted for 8,039 characters by itself.

    The head -- risk marker plus purpose -- is never truncated, because that is
    what the model needs to choose at all. The guidance lists are trimmed to a
    budget, whole clauses first so a sentence is never cut mid-word.
    """

    marker = _TIER_MARKERS.get(capability.risk_tier, "")
    head = f"{marker} {_first_sentences(capability.purpose or capability.label)}".strip()
    lines = [head]
    if capability.risk_tier is HarnessRiskTier.TRIGGERS_WORKFLOW:
        lines.append(
            "Starts work that continues after the call returns; the result says "
            "it started, not that it finished."
        )

    remaining = max(0, _MAX_DESCRIPTION_CHARS - len("\n".join(lines)))
    for label, clauses in (
        ("Use when", capability.use_when),
        ("Avoid when", capability.do_not_use_when),
    ):
        if not clauses or remaining <= 0:
            continue
        kept: list[str] = []
        budget = remaining // 2 if label == "Use when" else remaining
        for clause in clauses:
            clause = clause.strip()
            if not clause:
                continue
            if sum(len(k) + 1 for k in kept) + len(clause) > budget:
                break
            kept.append(clause)
        if kept:
            line = f"{label}: " + " ".join(kept)
            lines.append(line)
            remaining -= len(line)
    return "\n".join(lines)


def _tool_descriptor(capability: CapabilityDefinition, name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": _tool_description(capability),
        # MCP requires an object schema; several capabilities declare none.
        "inputSchema": capability.input_schema or {"type": "object", "properties": {}},
        "outputSchema": _TOOL_OUTPUT_SCHEMA,
    }


# --- Runtime assembly ------------------------------------------------------


async def _build_runtime(
    db: AsyncSession, qdrant: AsyncQdrantClient, scope: LabsTurnScope
) -> ProductionAgentTurnRuntime:
    """Assemble the identical runtime `/chat` uses for this turn's scope.

    NOTE: the brief asked for a module-level per-(token, conversation) cache of
    this runtime. It is deliberately absent: the registry, providers and every
    evidence handler close over the request-scoped `AsyncSession`, which
    `get_db` closes when the HTTP request ends. Caching the runtime across
    tools/call requests would reuse a closed session and fail on the second
    tool call of a turn. Making the cache correct requires the bridge to own a
    turn-lifetime session (created from `async_session_factory`, serialized by a
    per-turn lock, closed on drain/expiry) -- worth doing if rebuild latency
    shows up in measurement, but not worth a known-broken cache now.
    """

    conversation = (
        await db.execute(
            select(Conversation)
            .where(
                Conversation.id == scope.conversation_id,
                Conversation.user_id == scope.user_id,
                Conversation.deleted_at.is_(None),
            )
            .options(selectinload(Conversation.messages))
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise LabsScopeError("The turn scope does not resolve to an open conversation")

    return await build_production_agent_turn_runtime(
        db=db,
        qdrant=qdrant,
        conversation=conversation,
        user_id=scope.user_id,
        user_groups=list(scope.user_groups),
        project_scope=scope.project_scope,
        permissions=set(scope.permissions),
        access_token=scope.access_token,
    )


def _remaining_tool_calls(scope: LabsTurnScope) -> int:
    """This TURN's remaining capability-call allowance.

    `scope.remaining_tool_calls` is the constant per-turn allowance carried by
    the conversation-lifetime bearer; `labs_turn_budget` is the live count for
    the turn in flight and is reset at the turn boundary. Subtracting the second
    from the first is what makes the budget per turn rather than per
    conversation.
    """

    return labs_turn_budget.remaining(
        scope.conversation_id, scope.remaining_tool_calls
    )


def _budget_note(scope: LabsTurnScope) -> dict[str, Any]:
    """The allowance, stated to the model in every tool result.

    Without it the model has no way to pace itself: it discovers the budget only
    by being denied, which costs a call and reads to the model like a broken
    tool rather than a spent allowance.
    """

    remaining = _remaining_tool_calls(scope)
    note: dict[str, Any] = {
        "remaining_tool_calls_this_turn": remaining,
        "tool_calls_per_turn": scope.remaining_tool_calls,
    }
    if remaining == 0:
        note["_budget_note"] = (
            "This turn's tool allowance is spent. Answer from what you already "
            "have and say what you could not check; further tool calls will be "
            "refused until the user's next message."
        )
    elif remaining <= 5:
        note["_budget_note"] = (
            f"Only {remaining} tool call(s) remain in this turn. Spend them on "
            "the evidence that decides the answer."
        )
    return note


def _execution_context(
    scope: LabsTurnScope, allowed_capability_ids: set[str]
) -> CapabilityExecutionContext:
    """Server-owned context for one labs tool call.

    `allowed_capability_ids` is the advertised catalogue MINUS whatever this
    turn's mode may not run, so the policy engine denies anything the catalogue
    never advertised even if the name map were wrong, and denies the scan in an
    ordinary chat turn even if the explicit mode check in `_handle_tools_call`
    were removed. Writes and external side effects stay off: the labs lane does
    not decide on its own that an LLM may rewrite published client content.

    The raw bearer token is deliberately not a parameter. Everything this
    context needs from it has already been verified and unpacked into `scope`,
    and the two per-turn values -- the claim and the budget -- cannot come from
    it at all: the token is minted once per conversation and cached by OpenCode,
    while the claim pins one `message_id` and one `expected_version` that change
    every turn. Both are therefore read from the in-process, per-turn registries
    keyed by conversation.
    """

    remaining = _remaining_tool_calls(scope)
    claim = labs_turn_claims.get(scope.conversation_id)
    return CapabilityExecutionContext(
        user_id=scope.user_id,
        conversation_id=scope.conversation_id,
        project_scope=scope.project_scope,
        permissions=set(scope.permissions),
        access_token=scope.access_token,
        allow_writes_content=False,
        allow_external_side_effects=False,
        allowed_capability_ids=allowed_capability_ids,
        remaining_tool_calls=remaining,
        current_message_id=claim.message_id if claim is not None else None,
        conversation_state_version=claim.state_version if claim is not None else None,
        conversation_turn_claim_id=claim.claim_id if claim is not None else None,
        conversation_turn_claim_token=claim.claim_token if claim is not None else None,
    )


# --- JSON-RPC plumbing -----------------------------------------------------


def _json_rpc_result(request_id: Any, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


def _json_rpc_error(request_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    )


def _tool_result(payload: dict[str, Any], *, is_error: bool) -> dict[str, Any]:
    return {
        "content": [
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}
        ],
        "structuredContent": payload,
        "isError": is_error,
    }


def _observation_payload(observation: CapabilityObservation) -> dict[str, Any]:
    """The observation exactly as every other Nexus transport receives it.

    `CapabilityObservation` is already the sanitized boundary type -- fields the
    harness considers internal are excluded on the model itself -- so nothing is
    stripped here beyond what `model_dump` already drops.

    One addition: a workflow-starting capability returns as soon as the run is
    accepted, and the observation alone reads like a completed result. Without
    saying so plainly, a model will report a scan or a documentation build as
    finished the moment it was queued.
    """

    payload = observation.model_dump(mode="json")
    started = (
        observation.status is CapabilityObservationStatus.TRIGGERED
        or observation.workflow_ref is not None
    )
    if started:
        payload["_lane_note"] = (
            "This work has STARTED and is not finished. Do not report it as "
            "complete. Poll mcp.docs.get_generation_status for a documentation "
            "build, or mcp.projects.list_active_workflows for anything else, "
            "and tell the user it is running."
        )
    return payload


def _idempotency_key(
    scope: LabsTurnScope, capability_id: str, arguments: dict[str, Any]
) -> str:
    """A stable key for one capability call within one turn.

    Stateful handlers require it, not just the turn claim: `PackTaskHandler._claim`
    raises unless `request.idempotency_key` is set, and the executor renders that
    RuntimeError as the generic "capability handler is not available for this
    request" -- which reads like a missing dependency and is not one.

    Derived from the turn's claim plus the call itself, so the same call repeated
    inside one turn is recognised as a retry, while the same call in a later turn
    is correctly a new operation.
    """

    claim = labs_turn_claims.get(scope.conversation_id)
    turn = f"{claim.claim_id}:{claim.message_id}" if claim else "noclaim"
    payload = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"labs:{scope.conversation_id}:{turn}:{capability_id}:{digest}"


def _authenticate(request: Request) -> LabsTurnScope:
    """Resolve the caller's server-owned scope from the bearer token alone.

    Deliberately answers 403 without `WWW-Authenticate`. A 401 carrying that
    header tells an MCP client the server speaks OAuth: OpenCode then probes
    /.well-known/oauth-protected-resource, gets a 404, and leaves the server in
    `needs_auth` forever -- the later authenticated handshake still succeeds but
    the tools are never exposed to the model. This server issues no tokens and
    supports no authorization flow, so 403 is also the honest status.
    """

    # Path token first, Authorization header second. OpenCode's remote-MCP client
    # does not reliably send configured `headers` on the transport handshake --
    # observed live: both the POST and the SSE GET arrived with no Authorization
    # at all, so a header-only bridge can never finish connecting. The token is
    # still a server-minted, per-conversation credential carrying the full scope;
    # only where it rides changes. The header path is kept for direct callers.
    # Diagnostic: whether an MCP client actually delivers the configured
    # `headers` on its transport handshake decides whether a header-only bridge
    # can ever connect. Logged at INFO because it is the first thing to check
    # when tools silently fail to appear.
    logger.info(
        "labs.mcp auth probe method=%s path=%s authorization_header=%s",
        request.method,
        request.url.path,
        "present" if request.headers.get("authorization") else "absent",
    )

    token = (request.path_params.get("token") or "").strip()
    if not token:
        header = request.headers.get("authorization", "")
        scheme, _, header_token = header.partition(" ")
        if scheme.lower() == "bearer":
            token = header_token.strip()
    if not token:
        raise HTTPException(
            status_code=403,
            detail="A labs turn bearer token is required",
        )
    scope = labs_token_registry.resolve(token)
    if scope is None:
        raise HTTPException(
            status_code=403,
            detail="The labs turn token is unknown or expired",
        )
    # The token itself is not returned. It is a credential, and nothing past
    # this point has a use for one: the scope it carries is verified and
    # unpacked here, and the per-turn state is keyed by conversation. Handing
    # the raw bearer down the call chain only invites it back into a key or a
    # log line, which is how the evidence ledger came to be keyed by it.
    return scope


# --- Method handlers -------------------------------------------------------


def _handle_initialize() -> dict[str, Any]:
    return {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
    }


async def _handle_tools_list(
    db: AsyncSession, qdrant: AsyncQdrantClient, scope: LabsTurnScope
) -> dict[str, Any]:
    runtime = await _build_runtime(db, qdrant, scope)
    capabilities = _visible_capabilities(runtime, scope)
    name_map = _tool_name_map(capabilities)
    by_id = {capability.id: capability for capability in capabilities}
    tools = [
        _tool_descriptor(by_id[capability_id], name)
        for name, capability_id in name_map.items()
    ]
    # Kept, with its meaning changed. It used to be the evidence that the menu
    # was frozen: one line per conversation rather than one per turn proved the
    # client caches this answer, which is what made a mode gate here unworkable.
    # The catalogue no longer depends on the mode, so `scan_offered` should now
    # be true on EVERY line regardless of `mode`, and a false one means the scan
    # provider did not register rather than that the mode filtered it out. The
    # mode is still logged, as the correlation handle between this line and the
    # `labs.turn` lines that follow it.
    logger.info(
        "labs.mcp tools/list conversation_id=%s mode=%s tools=%s scan_offered=%s",
        scope.conversation_id,
        _turn_mode(scope).value,
        len(tools),
        any(
            capability_id in _DEEP_DIVE_CAPABILITY_IDS
            for capability_id in name_map.values()
        ),
    )
    return {"tools": tools}


async def _handle_tools_call(
    db: AsyncSession,
    qdrant: AsyncQdrantClient,
    scope: LabsTurnScope,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Execute one capability and render it as an MCP tool result."""

    name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(name, str) or not name:
        raise _JsonRpcError(_INVALID_PARAMS, "tools/call requires a string 'name'")
    if not isinstance(arguments, dict):
        raise _JsonRpcError(_INVALID_PARAMS, "tools/call 'arguments' must be an object")

    runtime = await _build_runtime(db, qdrant, scope)
    capabilities = _visible_capabilities(runtime, scope)
    name_map = _tool_name_map(capabilities)
    capability_id = name_map.get(name)
    if capability_id is None:
        raise _JsonRpcError(_INVALID_PARAMS, f"Unknown tool: {name}")

    # The mode gate, and the only one that is a boundary rather than a hint.
    #
    # It has to be here rather than on the catalogue because this is the request
    # the client makes every time: `tools/list` is served once per session and
    # cached, so a menu built from the mode describes whichever turn opened the
    # thread. The agent definition in `opencode.json` switches these tools off
    # for the chat agent too, per message and therefore correctly -- but that is
    # the model's own runtime policing itself, and a lane that reaches a client
    # estate does not rest on that.
    mode = _turn_mode(scope)
    excluded = _excluded_capability_ids(mode)
    executable = {
        allowed_id for allowed_id in name_map.values() if allowed_id not in excluded
    }
    if capability_id in excluded:
        logger.info(
            "labs.mcp tools/call refused by mode: conversation_id=%s mode=%s "
            "capability_id=%s tool=%s",
            scope.conversation_id,
            mode.value,
            capability_id,
            name,
        )
        _record_tool_call(
            scope.conversation_id,
            LabsToolCallRecord(
                capability_id=capability_id,
                tool_name=name,
                succeeded=False,
                summary=f"Not available in {mode.value} mode",
                error_code=CapabilityPolicyCode.ALLOWLIST.value,
            ),
        )
        # Counted against the turn budget by `_record_tool_call` above, and that
        # is deliberate: a refused call still cost a model round trip, and a
        # free refusal is one a model can spend the whole turn repeating.
        return _tool_result(
            {
                "capability_id": capability_id,
                "status": "denied",
                # The production denial vocabulary rather than a code invented
                # here: this IS an allowlist denial, and it is the same one the
                # executor below would raise from `executable`.
                "error_code": CapabilityPolicyCode.ALLOWLIST.value,
                "error_message": _MODE_REFUSAL_MESSAGE,
                **_budget_note(scope),
            },
            is_error=True,
        )

    # `executable`, not the whole name map. The check above already refused this
    # call, so this is the backstop: if a future edit lets an excluded id past
    # the branch, the executor's own ALLOWLIST gate still denies it.
    context = _execution_context(scope, executable)
    try:
        observation = await runtime.executor.execute(
            CapabilityExecutionRequest(
                capability_id=capability_id,
                arguments=arguments,
                idempotency_key=_idempotency_key(scope, capability_id, arguments),
            ),
            context,
        )
    except CapabilityExecutionDenied as exc:
        # The executor normally converts denials into observations itself; this
        # path covers a denial raised outside that conversion. A denial is a
        # tool outcome, not a transport failure.
        logger.warning(
            "Labs MCP capability denied: capability_id=%s reason=%s field=%s",
            capability_id,
            exc.code.value,
            exc.field_path,
        )
        _record_tool_call(
            scope.conversation_id,
            LabsToolCallRecord(
                capability_id=capability_id,
                tool_name=name,
                succeeded=False,
                summary=f"Denied by policy: {exc.code.value}",
                error_code=exc.code.value,
            ),
        )
        payload: dict[str, Any] = {
            "capability_id": capability_id,
            "status": "denied",
            "error_code": exc.code.value,
            "error_message": f"Denied by policy: {exc.code.value}",
            **_budget_note(scope),
        }
        if exc.field_path is not None:
            payload["field_path"] = exc.field_path
        return _tool_result(payload, is_error=True)

    _record_tool_call(
        scope.conversation_id,
        LabsToolCallRecord(
            capability_id=capability_id,
            # The name the model called, not a re-derivation of it: the runtime
            # uses this to emit the late `tool.completed` frame under the same
            # label the live `tool.running` frame already carried.
            tool_name=name,
            succeeded=observation.succeeded,
            summary=observation.summary,
            evidence_refs=list(observation.evidence_refs),
            error_code=observation.error_code,
        ),
    )
    # Budget stated AFTER the call is counted, so the number the model reads is
    # what it has left, not what it had before this call.
    return _tool_result(
        {**_observation_payload(observation), **_budget_note(scope)},
        is_error=not observation.succeeded,
    )


# --- Transport -------------------------------------------------------------


@router.post(LABS_MCP_ROUTE)
@router.post(LABS_MCP_ROUTE + "/{token}")
async def labs_mcp_endpoint(
    request: Request,
    db: DBSession,
    qdrant: QdrantClient,
) -> Response:
    """MCP Streamable HTTP endpoint backed by the production capability executor."""

    scope = _authenticate(request)

    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        logger.error("Labs MCP request body was not valid JSON", exc_info=True)
        return _json_rpc_error(None, _PARSE_ERROR, "Request body is not valid JSON")

    if not isinstance(payload, dict):
        # MCP 2025-06-18 removed JSON-RPC batching.
        return _json_rpc_error(
            None, _INVALID_REQUEST, "Expected a single JSON-RPC request object"
        )

    method = payload.get("method")
    if not isinstance(method, str):
        return _json_rpc_error(
            payload.get("id"), _INVALID_REQUEST, "Missing JSON-RPC 'method'"
        )

    # A notification carries no `id` at all -- `id: null` is a request.
    if "id" not in payload:
        logger.debug("Labs MCP notification received: method=%s", method)
        return Response(status_code=202)

    request_id = payload["id"]
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return _json_rpc_error(request_id, _INVALID_PARAMS, "'params' must be an object")

    try:
        if method == "initialize":
            return _json_rpc_result(request_id, _handle_initialize())
        if method == "ping":
            return _json_rpc_result(request_id, {})
        if method == "tools/list":
            return _json_rpc_result(
                request_id, await _handle_tools_list(db, qdrant, scope)
            )
        if method == "tools/call":
            return _json_rpc_result(
                request_id, await _handle_tools_call(db, qdrant, scope, params)
            )
    except _JsonRpcError as exc:
        return _json_rpc_error(request_id, exc.code, exc.message)
    except LabsScopeError as exc:
        logger.error("Labs MCP scope no longer resolvable: %s", exc, exc_info=True)
        return _json_rpc_error(request_id, _INVALID_REQUEST, str(exc))
    except LabsToolNameCollision as exc:
        logger.error("Labs MCP tool catalogue is ambiguous: %s", exc, exc_info=True)
        return _json_rpc_error(request_id, _INTERNAL_ERROR, "Tool catalogue is ambiguous")
    except Exception:
        logger.error(
            "Labs MCP method failed: method=%s conversation_id=%s",
            method,
            scope.conversation_id,
            exc_info=True,
        )
        return _json_rpc_error(request_id, _INTERNAL_ERROR, "Internal server error")

    return _json_rpc_error(request_id, _METHOD_NOT_FOUND, f"Unknown method: {method}")


@router.get(LABS_MCP_ROUTE)
@router.get(LABS_MCP_ROUTE + "/{token}")
async def labs_mcp_stream(request: Request) -> Response:
    """Streamable HTTP allows a server to decline the optional SSE stream."""

    _authenticate(request)
    return Response(status_code=405, headers={"Allow": "POST"})


__all__ = [
    "MCP_PROTOCOL_VERSION",
    "LabsScopeError",
    "LabsToolNameCollision",
    "deep_dive_tool_names",
    "drain_tool_calls",
    "router",
]
