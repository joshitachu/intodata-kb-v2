"""The labs turn runtime: OpenCode as the reasoning loop, Nexus as the tools.

This is the whole point of the lane. `/chat` runs the production LangGraph
harness; this runs the OpenCode headless server over the *same* capability
executor (reached through `src/labs/mcp_bridge.py`), with the same RBAC scope,
and then MEASURES the result. It never re-steers, never retries, and never
repairs the model's output -- a spike that silently fixes the model's mistakes
measures the repair, not the model.

Transport note -- one blocking call, narrated live
---------------------------------------------------
The turn itself runs through OpenCode's **v1** `POST /session/{id}/message`,
which is synchronous: one request blocks for the whole turn and returns the
finished assistant message. That request stays the authoritative source of the
answer.

While it is in flight, the server narrates the turn on its global SSE bus
(`GET /event?directory=<workspace>`), and this module subscribes to it
concurrently so tokens and tool activity reach the client as they happen instead
of arriving in one block at the end. The two run together:

1. the bus subscriber starts first and waits for `server.connected` -- the bus
   has no replay, so anything emitted before the subscription is live is lost;
2. the prompt is sent;
3. delta frames become `assistant.delta` (answer parts) or `assistant.reasoning`
   (reasoning parts), tool parts become `tool.running`, and a `todowrite` tool
   part additionally becomes `plan.updated` carrying the step list;
4. `session.idle`, or the blocking call returning, ends the subscription.

The event names and payload keys are the production harness's, unchanged, with
two additions this lane owns: `assistant.reasoning`, and a `todos` array on
`plan.updated`. The production harness has no event for chain of thought because
its models are not asked for any; here the model streams reasoning through the
identical bus event as the answer, so it needs a channel of its own or it would
have to be thrown away. The `todos` array is there for the same reason: this
agent's plan exists only as the argument of a tool call, and without a structured
frame to carry it the only route it has to the user is the answer body -- which
is where it used to end up, as thirteen paragraphs of narration above the answer.

What a turn cost, in the same table as the production lane
-----------------------------------------------------------
Every turn stages one `query_usage` row under the interface
:data:`LABS_USAGE_INTERFACE`, so this lane's spend can be charted next to the
production harness's (`conversation_agent`) with one `GROUP BY`. Before that row
existed, thirty-eight measured turns left no trace of their cost anywhere but the
OpenCode process's own memory.

The numbers come off the **bus**, not off the reply, and that is the load-bearing
part. `send_message` returns the last assistant message alone, so its `info.cost`
is a single step of a turn that routinely runs four or five -- 37% and 69% of the
true total on the two live conversations this was measured against. The bus
announces every step, the turn is already subscribed to it, and the reply is kept
only as the fallback for a turn whose subscription never went live.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import collections
import contextlib
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.harness import (
    HarnessConversationStateRow,
    HarnessConversationTurnClaimRow,
)
from src.db.models.query_usage import QueryUsage
from src.harness.conversation_state import (
    ConversationStateConflict,
    ConversationStateDenied,
    ConversationStateRepository,
)
from src.harness.guidance.compiler import compile_guidance
from src.harness.guidance.material import ProjectMaterial
from src.harness.guidance.material_repository import (
    compile_project_material,
    load_material_texts,
)
from src.harness.guidance.models import ResolvedGuidance
from src.labs.contracts import (
    LabsToolCallRecord,
    LabsTurnClaim,
    LabsTurnMode,
    LabsTurnScope,
    labs_token_registry,
    labs_turn_budget,
    labs_turn_claims,
    labs_turn_modes,
)
from src.labs.generation_memory import GenerationMemory, compile_generation_memory
from src.labs.mcp_bridge import drain_tool_calls
from src.labs.opencode_client import (
    BUS_PART_REASONING,
    BUS_PART_TEXT,
    Attachment,
    BusStepUsage,
    ModelRef,
    OpenCodeClient,
    OpenCodeError,
    OpenCodeReply,
    OpenCodeUnavailable,
    bus_event_delta_part_id,
    bus_event_is_idle,
    bus_event_part_kind,
    bus_event_step_usage,
    bus_event_text_delta,
    bus_event_tool_input,
    bus_event_tool_label,
    bus_event_tool_name,
    bus_event_tool_status,
)

# The provider/model/agent triple is a contract between this module and
# `workspace.py`, which writes the very same values into each workspace's
# `opencode.json`. They are imported rather than restated so a rename fails
# loudly at import time instead of silently running a turn against a model the
# workspace config never declared.
from src.labs.workspace import (
    _AGENT_NAME as LABS_AGENT,
)
from src.labs.workspace import (
    _MCP_SERVER_KEY as LABS_MCP_SERVER_KEY,
)
from src.labs.workspace import (
    _MODEL_ID as LABS_MODEL_ID,
)
from src.labs.workspace import (
    _PLAN_TOOL_WRITE as LABS_PLAN_TOOL,
)
from src.labs.workspace import (
    _PROVIDER_ID as LABS_PROVIDER_ID,
)
from src.labs.workspace import (
    ensure_labs_workspace,
    labs_workspace_path,
)

logger = logging.getLogger(__name__)

_TRUE_TOKENS: Final = frozenset({"1", "true", "yes", "on"})
_FALSE_TOKENS: Final = frozenset({"0", "false", "no", "off"})


def _env_flag(name: str, *, default: bool) -> bool:
    """Read a boolean environment flag, falling back when unset or unparseable.

    An unrecognised value takes the default rather than raising or reading as
    false: these flags are operator switches on a live container, and a typo'd
    `LABS_STREAM_REASONING=treu` silently turning a feature off is harder to
    diagnose than the feature simply staying on.
    """

    raw = os.environ.get(name, "").strip().lower()
    if raw in _TRUE_TOKENS:
        return True
    if raw in _FALSE_TOKENS:
        return False
    return default


#: The read-only planning agent, as the second agent definition in every
#: workspace's `opencode.json` (see `workspace.py`): no write-class tools, a low
#: `maxSteps`, and a prompt that produces a numbered plan instead of acting.
#:
#: A literal rather than an import because `workspace.py` names the default agent
#: in a private constant and this one only inside the config dict. The string is
#: the contract between the two files; if it ever drifts, OpenCode answers the
#: message call with HTTP 500 for an unknown agent (v1 validates the name on the
#: message, never on session create -- see `opencode_client`'s module docstring),
#: which this module reports as a failed turn rather than swallowing.
LABS_PLAN_AGENT: Final = "plan"

#: Whether reasoning deltas are published as `assistant.reasoning` frames.
#:
#: On by default because a collapsed reasoning panel is the point of the event.
#: Switchable without a deploy because some models emit far more reasoning than
#: answer -- `deepseek-flash` produced 3072 characters of it against 2704
#: characters of answer on a single question -- and an operator watching that
#: cross a slow link needs a way to stop paying for it that is not a rebuild.
#:
#: It gates *publication only*. Reasoning is excluded from `assistant.delta` and
#: from the persisted answer whether this is on or off; that is a correctness
#: rule, not a display preference, and it has no switch.
STREAM_REASONING: Final = _env_flag("LABS_STREAM_REASONING", default=True)

# Matches the production hard deadline contract: one turn gets 300s, then the
# OpenCode session is aborted and we report what we have.
# A turn's hard ceiling. Lower than the production harness's 300s on purpose:
# the long turns here are not slow answers, they are hangs. Observed live, a
# model hallucinated a tool name and OpenCode's invalid-tool path stalled for
# 292 seconds on the third attempt after failing the first two in 2ms -- the
# user waited five minutes for nothing. A real answer, including the heaviest
# multi-tool documentation turn measured, has never exceeded ~100s.
HARD_DEADLINE_SECONDS = float(os.environ.get("LABS_TURN_DEADLINE_SECONDS", "") or 120.0)

LABS_MODEL: Final = ModelRef(id=LABS_MODEL_ID, provider_id=LABS_PROVIDER_ID)

#: The `query_usage.interface` value this lane writes.
#:
#: A value of its own is the entire point of the row: V1 and V2 have to be
#: separable in one `GROUP BY interface`, which is what `usage_admin` already
#: does. The column holds `conversation_agent` (the production harness),
#: `conversation`, `rag_query`, `deep_dive` and `doc_generation`; this collides
#: with none of them, and it is the same lane name the turn already publishes as
#: the `cohort` on `run.started` and writes into the claim receipt as `lane`, so
#: the log, the stream and the cost table all say one thing.
LABS_USAGE_INTERFACE: Final = "labs_opencode"

#: `query_usage.model_name`, spelled the way the production lane spells it.
#:
#: `agent_chat_runtime` stores the provider-qualified name (`kimi/kimi-k2.6`) and
#: the bare provider beside it. Matching that is not cosmetic: `usage_admin`
#: groups by (`model_name`, `provider`), so a bare `deepseek-flash` would sit in
#: its own bucket instead of alongside every model the other lane reports.
LABS_USAGE_MODEL_NAME: Final = f"{LABS_PROVIDER_ID}/{LABS_MODEL_ID}"

# `query_usage` column widths. Values are cut to these before the row is staged
# because the row rides on the ROUTE's commit -- the one that persists the
# answer -- so an oversize string here would cost the user their answer rather
# than just its cost record. See `_stage_turn_usage`.
_USAGE_MODEL_NAME_MAX_CHARS: Final = 100
_USAGE_PROVIDER_MAX_CHARS: Final = 50
_USAGE_PROJECT_ID_MAX_CHARS: Final = 255

# How long to wait for the bus subscription to prove itself live before sending
# the prompt anyway. `server.connected` arrives on connect, so this is a
# round-trip, not a model latency; exceeding it means the bus is broken and the
# turn should still run (blind) rather than fail.
_BUS_CONNECT_TIMEOUT_SECONDS: Final = 5.0

# After the blocking call returns, how long to let the bus deliver its trailing
# frames (the last deltas and `session.idle`). Bounded so a silent bus cannot
# add latency to every turn.
_BUS_DRAIN_SECONDS: Final = 2.0

# Separator between two assistant text parts of one turn, matching how
# `OpenCodeReply.text` joins them. Deltas inside a part concatenate bare.
_TEXT_PART_SEPARATOR: Final = "\n\n"

# Tool part statuses worth publishing as a single `tool.running` frame. The bus
# emits `pending` -> `running` (repeatedly) -> `completed` for one call; the
# first frame seen for a part is the one that means "this tool just started".
_TOOL_STARTED_STATUSES: Final = frozenset({"pending", "running"})

EmitCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


class LabsRuntimeUnavailable(RuntimeError):
    """The OpenCode server could not be reached. The route maps this to HTTP 503."""


@dataclass(frozen=True, slots=True)
class LabsTurnResult:
    answer_text: str
    tool_calls: list[LabsToolCallRecord]
    opencode_session_id: str
    latency_ms: int


# conversation_id -> (mcp_token, opencode_session_id).
#
# A cache, no longer the record. It is in-process and therefore empty after
# every restart; the durable half of the binding is
# `Conversation.opencode_session_id`, which `_resolve_binding` falls back to.
#
# The two halves have deliberately different lifetimes. The token is a
# credential: re-minting it after a restart costs nothing and is the correct
# handling for one, so it is never persisted. The session id is the
# conversation's history on the OpenCode side, and losing it opened a SECOND
# top-level session for the same conversation -- observed on two live
# conversations, each holding two sessions with `parentID=None`, with everything
# said before the restart silently invisible to the model from then on.
_CONVERSATION_BINDINGS: dict[int, tuple[str, str]] = {}


# --- Turn claim ------------------------------------------------------------


@dataclass(slots=True)
class LabsClaimGuard:
    """The claim a turn is running under, and how that turn ended.

    The outcome is set through this handle rather than read off a return value
    because the labs stream reports a failed turn as an SSE frame and then
    returns normally. Without an explicit mark, every failure would look like a
    clean answer and get finalized as a validated agent action.
    """

    claim: LabsTurnClaim | None = None
    #: ``None`` means the turn answered.
    stop_reason: str | None = None

    def mark_failed(self, stop_reason: str) -> None:
        self.stop_reason = stop_reason


async def _acquire_labs_claim(
    *,
    db: AsyncSession,
    conversation_id: int,
    user_id: int,
    message_id: int,
) -> LabsTurnClaim | None:
    """Reserve Conversation State for this turn, or return None if it is taken.

    Contention is not a turn failure. Another turn holding the claim, or a
    version that moved under us, only means the stateful capabilities stay out
    of this turn's catalogue -- exactly the behaviour the lane had before the
    claim existed. Failing a read-only question because a lock was contended
    would be a regression, not a safety property.
    """

    repository = ConversationStateRepository(db)
    try:
        state = await repository.get_or_create(
            conversation_id=conversation_id, owner_user_id=user_id
        )
        claim = await repository.claim_turn(
            conversation_id=conversation_id,
            owner_user_id=user_id,
            message_id=message_id,
            expected_version=state.version,
            idempotency_key=f"labs-turn:{conversation_id}:{message_id}",
        )
        # `ConversationTurnClaim.lease_token` is `Field(exclude=True)`: it is a
        # real attribute that only serialization drops, so it is read off the
        # model here and never round-tripped through `model_dump`.
        lease_token = claim.lease_token
        if claim.replayed or lease_token is None:
            logger.error(
                "Labs turn claim for conversation %s message %s is already settled "
                "(status=%s); running without stateful capabilities",
                conversation_id,
                message_id,
                claim.status.value,
            )
            await db.rollback()
            return None
        # The claim must be durable before the model can pick a stateful tool:
        # the MCP bridge validates the lease from a different request's session.
        await db.commit()
    except (
        ConversationStateConflict,
        ConversationStateDenied,
        SQLAlchemyError,
        # Pydantic raises a ValueError subclass when a persisted state row no
        # longer validates. That is a data problem, not a reason to break the
        # user's stream after the answer has already been delivered.
        ValueError,
    ) as exc:
        logger.error(
            "Labs turn claim was not acquired for conversation %s message %s: %s; "
            "running without stateful capabilities",
            conversation_id,
            message_id,
            exc,
            exc_info=True,
        )
        await db.rollback()
        return None

    return LabsTurnClaim(
        message_id=message_id,
        state_version=state.version,
        claim_id=claim.id,
        claim_token=lease_token,
    )


async def _settle_labs_claim(
    *, db: AsyncSession, claim: LabsTurnClaim, stop_reason: str | None
) -> None:
    """Close the claim exactly once, mirroring the production turn's rule.

    A completed effect receipt has to be finalized even when the turn then
    failed: the capability already changed domain state, and the receipt is the
    only thing that carries that change into Conversation State. Only a turn
    that failed with nothing completed releases the claim as failed, and a call
    that started without completing hands over for recovery rather than
    pretending it did nothing.

    Every row this reads is re-selected with ``populate_existing``. Unlike the
    production harness, the capability calls of a labs turn run in a *different*
    request's session: this session added the claim row and still holds it in
    its identity map with the empty receipt list it was created with, so a
    plain ``get`` would return that stale object and finalize zero effects for
    a turn that actually recorded several.
    """

    repository = ConversationStateRepository(db)
    try:
        row = await db.get(
            HarnessConversationTurnClaimRow, claim.claim_id, populate_existing=True
        )
        if row is not None:
            await db.get(
                HarnessConversationStateRow,
                row.conversation_state_id,
                populate_existing=True,
            )
        receipts = list(row.effect_receipts or []) if row is not None else []
        has_completed_effect = any(item.get("phase") == "completed" for item in receipts)
        has_started_effect = any(item.get("phase") == "started" for item in receipts)
        if has_completed_effect or stop_reason is None:
            await repository.finalize_claim(
                claim_id=claim.claim_id,
                lease_token=claim.claim_token,
                action="labs_turn",
                final_result={
                    "stop_reason": stop_reason or "answered",
                    "lane": "labs_opencode",
                },
            )
        else:
            await repository.fail_claim(
                claim_id=claim.claim_id,
                lease_token=claim.claim_token,
                final_result={"stop_reason": stop_reason, "lane": "labs_opencode"},
                recovery_required=has_started_effect,
            )
        await db.commit()
    except (
        ConversationStateConflict,
        ConversationStateDenied,
        SQLAlchemyError,
        # Pydantic raises a ValueError subclass when a persisted state row no
        # longer validates. That is a data problem, not a reason to break the
        # user's stream after the answer has already been delivered.
        ValueError,
    ) as exc:
        logger.error(
            "Labs turn claim %s could not be settled (stop_reason=%s): %s",
            claim.claim_id,
            stop_reason or "answered",
            exc,
            exc_info=True,
        )
        await db.rollback()


@contextlib.asynccontextmanager
async def labs_turn_claim(
    *,
    db: AsyncSession,
    conversation_id: int,
    user_id: int,
    message_id: int,
    mode: LabsTurnMode = LabsTurnMode.CHAT,
) -> AsyncIterator[LabsClaimGuard]:
    """Hold the Conversation State claim for the duration of one labs turn.

    Acquired after the user message is durable (the claim pins its id) and
    before the model runs (it pins the version the turn started from), then
    published through `labs_turn_claims` so the MCP bridge can put it into the
    execution context of every capability call the turn makes.

    This is also the turn boundary for the per-turn tool allowance, and that is
    reset BEFORE the claim is acquired rather than from `bind`. Driving it from
    `bind` left one hole: a turn that loses the claim race binds nothing, so it
    reset nothing, and two consecutive losing turns shared a single count -- the
    second one starting with the first one's calls already spent. The claim race
    has nothing to do with how many tool calls a turn may make, so the reset must
    not depend on its outcome. `bind` still resets as well, which is harmless:
    `start_turn` is idempotent within one boundary and the second call sets the
    same zero.

    `mode` is published here for the same reason and with the same lifetime: the
    MCP bridge builds the tool menu in a DIFFERENT request from this one and can
    only read per-conversation state, while the mode is a property of one turn.
    Published unconditionally, ahead of the claim, so a turn that loses the claim
    race still runs under the mode the user asked for rather than under whatever
    the previous turn left behind.
    """

    labs_turn_budget.start_turn(conversation_id)
    labs_turn_modes.start_turn(conversation_id, mode)
    guard = LabsClaimGuard(
        claim=await _acquire_labs_claim(
            db=db,
            conversation_id=conversation_id,
            user_id=user_id,
            message_id=message_id,
        )
    )
    if guard.claim is not None:
        labs_turn_claims.bind(conversation_id, guard.claim)
    try:
        yield guard
    finally:
        claim = guard.claim
        # Unbind first, and synchronously: a cancelled stream must not be able
        # to leave a dead turn advertising stateful tools, and every await below
        # can be interrupted. Called unconditionally -- releasing an absent
        # claim is a no-op -- so a turn that lost the race cannot leave one
        # bound either.
        labs_turn_claims.release(conversation_id)
        # And the matching half of the unconditional `start_turn` above. The
        # release already ends the allowance through the claim registry's own
        # budget reference, which in production is this same singleton; spelling
        # it out keeps the pair visible in one function instead of depending on
        # two objects sharing an instance. `end_turn` is idempotent.
        labs_turn_budget.end_turn(conversation_id)
        # And the mode, cleared on the same boundary. Leaving a deep-dive entry
        # behind would hand the NEXT turn in this conversation the wide tool
        # menu without anyone asking for it, which is the one failure this
        # registry exists to prevent.
        labs_turn_modes.end_turn(conversation_id)
        if claim is not None:
            # Settling is shielded for the same reason the route shields the
            # turn task -- an unsettled claim locks the conversation until its
            # lease expires.
            await asyncio.shield(
                _settle_labs_claim(db=db, claim=claim, stop_reason=guard.stop_reason)
            )


def _build_scope(
    *,
    conversation_id: int,
    user_id: int,
    user_groups: Sequence[str],
    project_scope: Sequence[str] | None,
    permissions: frozenset[str] | set[str],
    access_token: str | None,
    workspace_dir: str,
    mode: LabsTurnMode = LabsTurnMode.CHAT,
) -> LabsTurnScope:
    return LabsTurnScope(
        user_id=user_id,
        conversation_id=conversation_id,
        user_groups=list(user_groups),
        project_scope=list(project_scope) if project_scope is not None else None,
        permissions=frozenset(permissions),
        access_token=access_token,
        workspace_dir=workspace_dir,
        mode=mode,
    )


def _workspace_project_id(project_scope: Sequence[str] | None) -> str | None:
    """The project whose published docs get mounted into the workspace.

    Only an unambiguous single-project scope mounts docs. A broader scope has no
    single answer, and mounting an arbitrary one would let the agent cite the
    wrong project's files. Live capabilities remain reachable for the whole
    scope through MCP either way.
    """

    if project_scope is not None and len(project_scope) == 1:
        return project_scope[0]
    return None


async def _resolve_binding(
    *,
    conversation_id: int,
    scope_factory: Callable[[str], LabsTurnScope],
    persisted_session_id: str | None,
) -> tuple[str, str | None]:
    """Return (mcp_token, opencode_session_id) for the conversation.

    The token outlives the turn because it is baked into `opencode.json`, so a
    repeat turn refreshes the existing token's scope in place rather than minting
    a new one and leaving the workspace pointing at a dead bearer.

    `persisted_session_id` is `Conversation.opencode_session_id`, and it is what
    makes this survive a restart. The in-process cache is empty after one, and
    without the fallback the turn opened a brand-new top-level OpenCode session
    while the Nexus-side message list carried on as if nothing had happened --
    so the model saw an empty thread and the user saw a full one.

    Re-minting the token in that situation is deliberate and is not a loss: it
    is a credential, the workspace's `opencode.json` is rewritten with the new
    value on the same turn, and the evidence ledger is keyed by conversation
    precisely so a rotation cannot orphan the turn's records.

    A persisted id that OpenCode no longer knows is not detected here. The turn
    would fail on `send_message` and be reported as a failed turn, which is the
    same outcome the in-process cache has always had when the OpenCode server
    restarted under it; recovering by opening a replacement session would mean
    this lane silently discarding the very history the column exists to keep.
    """

    workspace_dir = labs_workspace_path(conversation_id)
    scope = scope_factory(workspace_dir)
    existing = _CONVERSATION_BINDINGS.get(conversation_id)
    if existing is not None:
        token, session_id = existing
        if labs_token_registry.refresh(token, scope):
            return token, session_id
        # The token expired out of the registry; mint a fresh one and keep the
        # session, whose id is still valid on the OpenCode side.
        token = labs_token_registry.mint(scope)
        _CONVERSATION_BINDINGS[conversation_id] = (token, session_id)
        return token, session_id

    token = labs_token_registry.mint(scope)
    session_id = (persisted_session_id or "").strip() or None
    if session_id is not None:
        logger.info(
            "labs.session recovered conversation_id=%s session=%s; "
            "the in-process binding was lost (restart) and the persisted "
            "session id was reused instead of opening a second one",
            conversation_id,
            session_id,
        )
        _CONVERSATION_BINDINGS[conversation_id] = (token, session_id)
    return token, session_id


def _persisted_session_id(conversation: Any) -> str | None:
    """The conversation row's stored OpenCode session, or None.

    Read through `getattr` because `execute_labs_turn` types `conversation` as
    `Any`: the route hands it a `Conversation` ORM object, and tests hand it a
    stub. A stub without the attribute means "no session recorded", which is the
    same thing a fresh conversation means, so it needs no special case.
    """

    value = getattr(conversation, "opencode_session_id", None)
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _remember_session_id(conversation: Any, session_id: str) -> None:
    """Stage the newly created OpenCode session on the conversation row.

    Assignment only, and that is the design rather than an omission: this lane
    persists nothing itself. The route owns the turn's transaction, the
    `conversation` object here was loaded in that same `AsyncSession`, and the
    route already relies on this exact pattern for the auto-generated title --
    it mutates the attribute and lets its own `commit` carry it. Opening a
    second write path from inside the runtime would put two committers on one
    row for no gain.

    A failed assignment is logged rather than raised. The turn itself is fine
    and the user is waiting on it; what is lost is the restart-survival of this
    conversation's session, and that is worth an error line, not a dead turn.
    """

    if _persisted_session_id(conversation) == session_id:
        return
    try:
        conversation.opencode_session_id = session_id
    except AttributeError:
        logger.error(
            "Labs session id %s could not be recorded on conversation %s; "
            "a restart will split this conversation's OpenCode history",
            session_id,
            getattr(conversation, "id", "-"),
            exc_info=True,
        )


def _turn_failure(reply: OpenCodeReply) -> str | None:
    """A short reason when OpenCode returned a reply that is not a clean answer.

    `finish == "stop"` with no error is the only success. `"tool-calls"` coming
    back from the synchronous endpoint means the server stopped mid-loop,
    `"length"` means the answer was truncated, and `"aborted"` means the turn was
    cut short -- all of them are reported, never repaired.
    """

    if reply.succeeded:
        return None
    if reply.error is not None:
        return reply.error_message or "OpenCode reported an unspecified error"
    return f"OpenCode ended the turn with finish reason {reply.finish!r}"


def labs_tool_label(record: LabsToolCallRecord) -> str:
    """The label one capability call is announced under, on both of its frames.

    This is the correlation key between the live `tool.running` frame the bus
    produced when the call started and the late `tool.completed` frame drained
    from the MCP bridge when the turn ended. It has to be one string, because a
    client that cannot tell the two frames describe the same call lists it twice
    and reports double the tool calls that happened -- observed on a live turn as
    "81 tool calls" against a trace holding 40.

    The bus names a tool the way OpenCode does: the MCP server key, an
    underscore, and the tool name the bridge advertised. `capability_id` is NOT
    that name (`topology.landscape.read_structure` is called as
    `read_topology_structure`), so it is used only as the fallback for a record
    that predates :attr:`LabsToolCallRecord.tool_name` or was built outside the
    bridge -- in which case the two frames stay uncorrelated, exactly as before,
    rather than being forced together on a guess.
    """

    if record.tool_name:
        return f"{LABS_MCP_SERVER_KEY}_{record.tool_name}"[:100]
    return record.capability_id[:100]


async def _emit_tool_calls(
    emit: EmitCallback, tool_calls: Sequence[LabsToolCallRecord]
) -> None:
    """Project the turn's capability calls as `tool.completed` frames.

    The MCP bridge only surrenders its records once the turn is over, so these
    are necessarily late. The matching `tool.running` frames are not synthesized
    from them -- they come from the event bus, live, off tool parts the server
    actually reported (see :meth:`_BusStream._maybe_announce_tool`). The two sets
    are not the same size either: the bus sees every OpenCode tool (`bash`,
    `read`, `todowrite`, ...) while these records cover only Nexus capabilities.
    What they DO share is a label, per :func:`labs_tool_label`, so a client can
    close the running row for a call instead of appending a second one.

    The payload keys are the production ones
    (`_PUBLIC_EVENT_FIELDS["tool.completed"]`).
    """

    for record in tool_calls:
        await emit(
            "tool.completed",
            {
                "tool_label": labs_tool_label(record),
                "status": "succeeded" if record.succeeded else "failed",
                "summary": record.summary,
                "evidence_refs": list(record.evidence_refs),
            },
        )


#: The prefix OpenCode gives every tool it reached through the Nexus MCP server.
#:
#: The bus names a tool `<mcp server key>_<advertised tool name>`, so this is
#: what separates a capability call from OpenCode's own `read`, `grep` and
#: `todowrite` in `_BusStream.tool_counts`.
_NEXUS_BUS_TOOL_PREFIX: Final = f"{LABS_MCP_SERVER_KEY}_"


def _nexus_bus_tool_calls(tool_counts: collections.Counter[str]) -> int:
    """How many capability calls the event bus saw this turn start."""

    return sum(
        count
        for name, count in tool_counts.items()
        if name.startswith(_NEXUS_BUS_TOOL_PREFIX)
    )


def _warn_on_lost_capability_records(
    *,
    conversation_id: int,
    drained: Sequence[LabsToolCallRecord],
    tool_counts: collections.Counter[str],
) -> None:
    """Report a turn whose capability calls left no evidence behind.

    The two counts come from opposite ends of the same calls: `tool_counts` is
    what the OpenCode event bus announced as started, and `drained` is what the
    MCP bridge recorded as executed. Capability calls that the bus saw and the
    ledger did not are an evidence trail that no longer exists -- no
    `tool.completed` frames, nothing in the trace explorer, no `evidence_refs`
    on the persisted message -- and the answer still reads as fully grounded.

    That is how the ledger's token key stayed broken: two measured turns logged
    `tool_calls=0` against four and five `nexus_*` calls on the bus, and nothing
    anywhere said so. A count that can be wrong in silence gets believed.

    Only total loss is reported. A partial shortfall is expected and would be
    noise: the bus counts a call the moment its part appears, so a call that
    started and then failed in transport legitimately reaches no ledger. Zero
    records against a bus that saw capability calls run cannot be explained that
    way.
    """

    bus_calls = _nexus_bus_tool_calls(tool_counts)
    if drained or not bus_calls:
        return
    logger.warning(
        "labs.evidence_lost conversation_id=%s capability_records=%s "
        "bus_capability_calls=%s bus_breakdown=%s; the turn ran capability tools "
        "but the MCP ledger surrendered nothing, so this turn has no evidence "
        "trail and its answer is ungrounded in the record",
        conversation_id,
        len(drained),
        bus_calls,
        ",".join(
            f"{name}={count}"
            for name, count in sorted(tool_counts.items())
            if name.startswith(_NEXUS_BUS_TOOL_PREFIX)
        ),
    )


# Some models emit tool calls as literal prose instead of through the provider's
# structured tool-call channel. `big-pickle` does this constantly:
#
#     <tool_call> <function=nexus_read_runtime_evidence>
#       <parameter=operation>list_runtime_health</parameter> </function> </tool_call>
#
# OpenCode never parses those, so they arrive as answer text and the user watches
# the model's plumbing scroll past. Stripping them is presentation only -- the
# malformed call still failed, and the turn still wasted a step; the real fix is
# a model that emits tool calls properly.
_TOOL_CALL_OPEN: Final = "<tool_call"
_TOOL_CALL_CLOSE: Final = "</tool_call>"
_STRAY_TAG_RE: Final = re.compile(
    r"</?(?:tool_call|function|parameter)\b[^>]*>", re.IGNORECASE
)


class _ToolCallScrubber:
    """Remove leaked tool-call markup from a text stream, chunk by chunk.

    A block can straddle any number of deltas, so text that might be the start of
    one is withheld until it is resolved. Anything still held at the end of the
    turn was an unterminated block and is dropped rather than shown.
    """

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str) -> str:
        self._buffer += chunk
        out: list[str] = []
        while True:
            start = self._buffer.find(_TOOL_CALL_OPEN)
            if start == -1:
                break
            end = self._buffer.find(_TOOL_CALL_CLOSE, start)
            if end == -1:
                # Block still open: emit what precedes it, hold the rest.
                out.append(self._buffer[:start])
                self._buffer = self._buffer[start:]
                return _STRAY_TAG_RE.sub("", "".join(out))
            out.append(self._buffer[:start])
            self._buffer = self._buffer[end + len(_TOOL_CALL_CLOSE):]

        # No open block. Hold back any tail that could still become one.
        hold = 0
        for size in range(min(len(_TOOL_CALL_OPEN), len(self._buffer)), 0, -1):
            if self._buffer.endswith(_TOOL_CALL_OPEN[:size]):
                hold = size
                break
        if hold:
            out.append(self._buffer[:-hold])
            self._buffer = self._buffer[-hold:]
        else:
            out.append(self._buffer)
            self._buffer = ""
        return _STRAY_TAG_RE.sub("", "".join(out))

    def flush(self) -> str:
        """Emit whatever is safe at end of turn; drop an unterminated block."""

        held, self._buffer = self._buffer, ""
        if _TOOL_CALL_OPEN in held:
            held = held[: held.find(_TOOL_CALL_OPEN)]
        return _STRAY_TAG_RE.sub("", held)


def scrub_tool_call_markup(text: str) -> str:
    """One-shot scrub for a complete string (the persisted answer)."""

    scrubber = _ToolCallScrubber()
    return (scrubber.feed(text) + scrubber.flush()).strip()


#: Step statuses that mean the step will not be worked on again.
_PLAN_CLOSED_STATUSES: Final = frozenset({"completed", "cancelled"})

#: Ceilings on a published plan. The list is model-authored, so it is bounded on
#: the way out rather than trusted: a plan with two hundred steps is a malfunction,
#: and rendering it would be one too.
_MAX_PLAN_STEPS: Final = 25
_MAX_PLAN_STEP_CHARS: Final = 200


def _plan_steps(arguments: dict[str, Any]) -> list[dict[str, str]]:
    """Normalise a `todowrite` argument object into publishable steps.

    Three fields survive -- `id`, `content`, `status` -- and everything else in
    the tool's input is dropped. A step with no readable `content` is dropped
    whole: an empty row in a plan card tells the reader nothing and costs them a
    line of screen.

    `status` is passed through verbatim when the model reported one, because the
    vocabulary belongs to OpenCode and a value this function does not recognise
    today may be meaningful tomorrow. Only a step that reported nothing at all is
    given `pending`, which is the one thing that can be said about a step nobody
    has claimed to have started.
    """

    raw = arguments.get("todos")
    if not isinstance(raw, list):
        return []

    steps: list[dict[str, str]] = []
    for index, entry in enumerate(raw[:_MAX_PLAN_STEPS]):
        if not isinstance(entry, dict):
            continue
        content = entry.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        identifier = entry.get("id")
        status = entry.get("status")
        steps.append(
            {
                "id": str(identifier)
                if isinstance(identifier, (str, int)) and str(identifier)
                else str(index),
                "content": content.strip()[:_MAX_PLAN_STEP_CHARS],
                "status": status if isinstance(status, str) and status else "pending",
            }
        )
    return steps


@dataclass(slots=True)
class _BusStream:
    """Turns raw bus frames into live public events, and remembers what it saw.

    Deliberately a small state machine rather than a pile of closures: the bus
    is stateless about *what kind* of part a delta belongs to, so something has
    to hold the `partID -> part type` map, and holding it here keeps the
    correlation next to the rule it exists to enforce (reasoning never reaches
    `assistant.delta`).

    Reasoning is separated, not discarded: a delta whose part was announced as
    `reasoning` is published on `assistant.reasoning` and is never appended to
    `_chunks`, never fed to the scrubber, and therefore never reachable from
    :attr:`text` -- which is the only thing the turn can persist as the answer.
    """

    emit: EmitCallback
    #: Publish `assistant.reasoning` frames. Off still means "never in the
    #: answer": this only decides whether the client is shown the reasoning.
    stream_reasoning: bool = STREAM_REASONING
    #: Set on the first frame of any kind -- proof the subscription is live.
    connected: asyncio.Event = field(default_factory=asyncio.Event)
    #: Set by `session.idle`, the server's end-of-turn signal.
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    _chunks: list[str] = field(default_factory=list)
    _part_kinds: dict[str, str] = field(default_factory=dict)
    #: Holds back text that might turn out to be leaked tool-call markup.
    _scrubber: _ToolCallScrubber = field(default_factory=_ToolCallScrubber)
    _text_parts: list[str] = field(default_factory=list)
    _announced_tools: set[str] = field(default_factory=set)
    #: Every tool the turn ran, counted by name, from the bus rather than from
    #: the MCP bridge.
    #:
    #: The bridge only ever sees `nexus_*` capability calls, so the turn log's
    #: `tool_calls` counted those alone and silently omitted `grep`, `read`,
    #: `glob` and the rest. A measured turn that reported four calls had in fact
    #: run twelve, and the eight it hid pulled more evidence than the four it
    #: showed. Every tuning decision about call economy was being made against
    #: that partial number, so the count has to come from the one place that
    #: sees the whole turn.
    _tool_counts: collections.Counter[str] = field(default_factory=collections.Counter)
    #: The last plan published, so the repeated frames of one `todowrite` call
    #: do not publish the same step list three times.
    _plan_signature: str | None = None
    #: What each model step of this turn cost, keyed by its `step-finish` part.
    #:
    #: Keyed rather than appended because the bus re-emits a part's frame on
    #: every state change, and a turn that counted one step twice would report
    #: double its cost. A later frame for the same part replaces the earlier one:
    #: the part is terminal, so the server's last word on it is the right one.
    _step_usage: dict[str, BusStepUsage] = field(default_factory=dict)

    @property
    def tool_counts(self) -> collections.Counter[str]:
        """Every tool this turn ran, by name. A copy: callers must not mutate."""

        return collections.Counter(self._tool_counts)

    @property
    def step_usage(self) -> list[BusStepUsage]:
        """Every model step this turn finished, in the order the bus closed them.

        The complete turn, which is the point. The blocking `send_message` hands
        back the last assistant message only, so pricing a turn off its reply
        charges for one step of four or five -- 37% and 69% of the real total on
        the two live conversations this was measured against.
        """

        return list(self._step_usage.values())

    @property
    def text(self) -> str:
        """Everything streamed as answer prose, in order. Reasoning excluded."""

        return "".join(self._chunks)

    async def handle(self, event: dict[str, Any]) -> None:
        self.connected.set()

        kind = bus_event_part_kind(event)
        if kind is not None:
            part_id, part_type = kind
            self._part_kinds[part_id] = part_type
            if part_type == BUS_PART_TEXT and part_id not in self._text_parts:
                # A second text part is a new assistant block, not a continuation
                # of the previous one; separate them the way the finished reply
                # does so the live text and the authoritative text agree.
                if self._text_parts and self._chunks:
                    self._chunks.append(_TEXT_PART_SEPARATOR)
                    await self.emit("assistant.delta", {"content": _TEXT_PART_SEPARATOR})
                self._text_parts.append(part_id)
            await self._maybe_announce_tool(event, part_id)
            await self._maybe_publish_plan(event)
            self._record_step_usage(event)
            return

        delta = bus_event_text_delta(event)
        if delta is not None:
            part_id = bus_event_delta_part_id(event)
            # An unannounced part is not assumed to be prose. Reasoning streams
            # through the identical event with the identical `field`, so
            # defaulting to "this is the answer" would leak chain of thought.
            # Hence an exhaustive match on the announced part type: anything that
            # is neither text nor reasoning is dropped, not guessed at.
            part_type = self._part_kinds.get(part_id) if part_id is not None else None
            if part_type == BUS_PART_TEXT:
                visible = self._scrubber.feed(delta)
                if visible:
                    self._chunks.append(visible)
                    await self.emit("assistant.delta", {"content": visible})
            elif part_type == BUS_PART_REASONING and self.stream_reasoning:
                # Its own event type, and deliberately not through `_scrubber`:
                # that buffer carries answer text across delta boundaries, and
                # interleaving a second stream into it would splice reasoning
                # into the answer -- the exact failure this branch exists to
                # prevent. Reasoning is also never appended to `_chunks`, so it
                # cannot reach `text` and therefore cannot be persisted.
                await self.emit("assistant.reasoning", {"content": delta})
            return

        if bus_event_is_idle(event):
            self.finished.set()

    async def _maybe_announce_tool(self, event: dict[str, Any], part_id: str) -> None:
        """Publish `tool.running` once per tool call, on its first frame."""

        if part_id in self._announced_tools:
            return
        status = bus_event_tool_status(event)
        if status not in _TOOL_STARTED_STATUSES:
            return
        # The bare tool name, NOT `bus_event_tool_label`: the label prefers the
        # server's `state.title` once it has one, and a label that changes shape
        # between a call's frames cannot be the key the late `tool.completed`
        # frame is matched on (see `labs_tool_label`). The title is not lost --
        # it is the `summary` below whenever the server has produced one.
        name = bus_event_tool_name(event)
        if name is None:
            return
        title = bus_event_tool_label(event) or name
        self._announced_tools.add(part_id)
        # Counted on the same first-frame-per-part guard that publishes the
        # event, so a tool whose `running` status repeats is still one call.
        self._tool_counts[name] += 1
        # Same payload keys `_emit_tool_calls` uses for `tool.completed`, minus
        # `evidence_refs`: a tool that has just started has produced no evidence,
        # and an empty list would assert one. `summary` is what the frontend's
        # live progress line reads (`lib/agentProgress.ts:latestToolActivity`),
        # `tool_label` is what the trace explorer reads.
        await self.emit(
            "tool.running",
            {
                "tool_label": name[:100],
                "status": "running",
                "summary": title[:200],
            },
        )

    def _record_step_usage(self, event: dict[str, Any]) -> None:
        """Remember what a finished model step consumed.

        Nothing is published to the client from this. A turn's cost is an
        operator concern and the production event contract has no frame for it;
        it leaves the process as a `query_usage` row and a log line.
        """

        usage = bus_event_step_usage(event)
        if usage is None:
            return
        self._step_usage[usage.part_id] = usage

    async def _maybe_publish_plan(self, event: dict[str, Any]) -> None:
        """Publish the agent's step list on `plan.updated` when it changes.

        The plan is the one tool ARGUMENT this lane forwards, and it is forwarded
        because there is nowhere else for a plan to go. Before this, the prompt
        asked for a spoken plan and the model obliged by narrating every step
        into `assistant.delta` -- so the plan, and a running commentary on it,
        landed in the answer body and pushed the actual answer thirteen
        paragraphs down. The steps belong in a structured frame the client can
        render beside the answer instead of inside it.

        Deliberately not gated on `_announced_tools`: `todowrite` is called
        repeatedly through a turn to mark steps done, each call is its own part,
        and the *updates* are the point. De-duplication is by content, so the
        `pending` -> `running` -> `completed` frames of one call publish once.

        Only `id`, `content` and `status` are read off each entry. The rest of a
        tool's input is never published -- `arguments` is a private key in the
        production event contract for good reason, and widening that here for a
        display nicety would be the wrong trade.
        """

        if bus_event_tool_name(event) != LABS_PLAN_TOOL:
            return
        arguments = bus_event_tool_input(event)
        if arguments is None:
            return
        steps = _plan_steps(arguments)
        if not steps:
            return
        signature = repr(steps)
        if signature == self._plan_signature:
            return
        self._plan_signature = signature
        current = next(
            (step["content"] for step in steps if step["status"] == "in_progress"),
            None,
        )
        await self.emit(
            "plan.updated",
            {
                "status": "completed"
                if all(step["status"] in _PLAN_CLOSED_STATUSES for step in steps)
                else "running",
                # `summary` is the production `plan.updated` field, and it is
                # what every existing client collapses the frame down to -- the
                # single replaceable status line. The step in progress is the
                # only honest value for "what is happening now".
                "summary": current,
                "todos": steps,
            },
        )


def _resolve_answer_text(*, reply_text: str, streamed_text: str) -> str:
    """Pick the text that becomes the persisted answer.

    Neither source is unconditionally authoritative, which is why this is a
    function and not a one-liner:

    * ``send_message`` returns only the **last** assistant message of the turn
      (verified -- see `opencode_client`'s module docstring), so on a multi-step
      turn it silently drops any prose the model wrote before a tool call. The
      bus carries all of it.
    * The bus, in exchange, can be cut short: a dropped subscription leaves a
      truncated fragment, and persisting that over a complete reply would lose
      the end of the answer.

    So the stream wins only when it demonstrably contains the reply -- that is
    the evidence that it ran to the end and is the superset. Otherwise the reply
    wins, and the stream is the fallback for the case where there is no reply at
    all (the hard-deadline path), so a turn can never come back empty.
    """

    if streamed_text and reply_text in streamed_text:
        return streamed_text
    return reply_text or streamed_text


async def _subscribe_to_bus(
    client: OpenCodeClient,
    *,
    session_id: str,
    directory: str,
    stream: _BusStream,
) -> None:
    """Feed `stream` from the OpenCode bus until the turn ends or we are cancelled.

    Best effort by construction: the blocking `send_message` is the turn, this is
    only its narration. A bus failure is logged and the subscription ends; it
    never propagates, because killing a turn that is running fine on the other
    connection would trade a real answer for a cosmetic one.
    """

    try:
        async for event in client.stream_bus_events(
            session_id=session_id, directory=directory
        ):
            await stream.handle(event)
            if stream.finished.is_set():
                return
    except OpenCodeError as exc:
        logger.error(
            "Labs event bus subscription failed for session %s: %s",
            session_id,
            exc,
            exc_info=True,
        )
        # Unblock the sender, which is waiting on this flag before prompting.
        stream.connected.set()


async def _stop_subscriber(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


#: Magic-byte signatures for the image types the chat input accepts.
#:
#: `ChatInput.tsx` strips the data-URI prefix before sending (`result.split(",")[1]`),
#: so the mime type does not survive the trip and has to be recovered from the
#: bytes. Assuming one type would mislabel three of the four accepted formats.
_IMAGE_SIGNATURES: Final = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _sniff_image_mime(payload: str) -> str:
    """Best-effort mime type for a base64 image, defaulting to PNG.

    WebP is `RIFF....WEBP`, so it needs the 12-byte window rather than a prefix
    match. A payload that matches nothing is still sent as PNG: most decoders
    sniff the bytes themselves, and refusing the upload outright would be worse
    than a possibly-wrong label.
    """

    try:
        head = base64.b64decode(payload[:32] + "==", validate=False)[:12]
    except (ValueError, binascii.Error):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    for signature, mime in _IMAGE_SIGNATURES:
        if head.startswith(signature):
            return mime
    return "image/png"


# --- What the turn cost -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class LabsTurnUsage:
    """One turn's spend, already folded into the columns `query_usage` has.

    Every token field is `None` when nothing reported it, and that is not the
    same as zero. A row of zeroes reads as a turn that was free, which is a
    worse record than no row at all -- see :attr:`observed`.
    """

    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    #: Not columns. `query_usage` has no cache fields, so the split that
    #: `input_tokens` folds in is kept here for the turn log -- the cached share
    #: is the difference between a turn that cost cents and one that cost dollars,
    #: and it should not be invisible just because the table cannot hold it.
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    #: How many model steps this was summed from. One on the reply fallback.
    steps: int = 0

    @property
    def observed(self) -> bool:
        """True when at least one step actually reported spend.

        All-zero counts read as unobserved on purpose: OpenCode announces an
        assistant message with a zeroed usage block before the model has
        produced anything, and a row built from that would assert a free turn.
        """

        return bool(self.cost_usd) or bool(self.input_tokens) or bool(self.output_tokens)


def _usage_int(value: Any) -> int | None:
    """A token count, or None when the field was absent or not a number."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, int(value))


def _turn_usage(
    samples: Sequence[tuple[float | None, dict[str, Any] | None]],
) -> LabsTurnUsage:
    """Sum per-step OpenCode usage into the shape `query_usage` stores.

    OpenCode reports usage **per model step**, never cumulatively -- verified by
    replaying two live sessions, where a second turn's message cost far less than
    the first turn's total (see `opencode_client`'s module docstring). So these
    are summed, and nothing is diffed against a running total; diffing would
    subtract one real turn's cost from the next one's.

    Two conversions happen here, and both exist so a labs row can be added to a
    production row without lying about either:

    * **Cache tokens are folded into `input_tokens`.** OpenCode's `tokens.input`
      is disjoint from `tokens.cache.read`, while the production lane's invariant
      (`ModelTokenUsage`) is that `input_tokens` is the whole prompt and the
      cache counts are a SUBSET of it. Storing `tokens.input` raw would report a
      246-token prompt for a turn whose prompt was 11126, because a warm DeepSeek
      session serves ~98% of it from cache -- so the cached tokens are not
      dropped, they are counted where the other lane already counts them. The
      read/write split cannot be recovered from the row afterwards; `query_usage`
      has no column for it, and V1's row has exactly the same limitation (it
      keeps the split on its separate audit row, which this lane has no
      equivalent of). The split survives in the `labs.usage` log line.
    * **Reasoning tokens are folded into `output_tokens`.** OpenCode counts them
      separately; the OpenAI-shaped metadata the production lane reads counts
      them inside `output_tokens`. They are billed at the output rate either way,
      and dropping them would under-report a reasoning model's output by up to a
      quarter -- 1808 of 7856 output tokens on one measured step.

    `cost` is OpenCode's own figure and is summed rather than recomputed. The
    server prices each call against the provider's rates with the cache discount
    already applied, and a second pricing table here would be a second thing to
    keep correct against every model change.
    """

    cost_total = 0.0
    saw_cost = False
    prompt_tokens = 0
    completion_tokens = 0
    cached = 0
    written = 0
    saw_tokens = False

    for cost, tokens in samples:
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            cost_total += float(cost)
            saw_cost = True
        if not isinstance(tokens, dict):
            continue
        cache = tokens.get("cache")
        cache = cache if isinstance(cache, dict) else {}
        fields = (
            _usage_int(tokens.get("input")),
            _usage_int(tokens.get("output")),
            _usage_int(tokens.get("reasoning")),
            _usage_int(cache.get("read")),
            _usage_int(cache.get("write")),
        )
        if all(field_value is None for field_value in fields):
            # A `tokens` object carrying nothing this function understands is
            # not evidence of a free step; leave the counts unreported.
            continue
        saw_tokens = True
        step_input, step_output, step_reasoning, step_cached, step_written = (
            field_value or 0 for field_value in fields
        )
        cached += step_cached
        written += step_written
        prompt_tokens += step_input + step_cached + step_written
        completion_tokens += step_output + step_reasoning

    return LabsTurnUsage(
        cost_usd=cost_total if saw_cost else None,
        input_tokens=prompt_tokens if saw_tokens else None,
        output_tokens=completion_tokens if saw_tokens else None,
        cached_input_tokens=cached,
        cache_write_tokens=written,
        steps=len(samples),
    )


def _stage_turn_usage(
    *,
    db: Any,
    conversation_id: int,
    user_id: int,
    project_id: str | None,
    usage: LabsTurnUsage,
    latency_ms: int,
) -> bool:
    """Stage this turn's `query_usage` row on the route's transaction.

    Staged, never committed, which is the rule `_remember_session_id` already
    follows in this file: the route owns the turn's transaction and commits once,
    after the assistant message. A second committer on one turn would be able to
    persist a cost row for an answer that then failed to save.

    No SAVEPOINT, deliberately. `Session.add` issues no SQL -- the INSERT is
    emitted by the route's flush -- so there is nothing in flight for a failure
    here to poison, and `begin_nested` would force exactly the extra round trip
    this avoids. What that does leave is the mirror risk: a row that fails to
    INSERT fails the commit that persists the ANSWER. Every varchar value is
    therefore cut to its column width on the way in rather than trusted to fit.

    Returns whether the row was staged, so a turn whose cost went unrecorded can
    say so instead of being assumed to have landed.
    """

    if db is None:
        # `db` is `Any` by this function's own signature; the route always passes
        # a live session, and the lane's turn tests drive the whole path without
        # one. Having nowhere to stage the row is not a reason to fail a turn
        # that already answered -- the same trade this file makes everywhere else
        # for bookkeeping.
        logger.warning(
            "labs.usage_unrecorded conversation_id=%s: no database session was "
            "supplied, so this turn's cost is not in query_usage",
            conversation_id,
        )
        return False

    row = QueryUsage(
        user_id=user_id,
        interface=LABS_USAGE_INTERFACE,
        # Deliberately not the prompt, matching the production lane's row. The
        # question is already persisted verbatim as the conversation's user
        # message; a second copy on a cost table widens the personal-data
        # surface for nothing a cost query needs.
        query_text=None,
        model_name=LABS_USAGE_MODEL_NAME[:_USAGE_MODEL_NAME_MAX_CHARS],
        provider=LABS_PROVIDER_ID[:_USAGE_PROVIDER_MAX_CHARS],
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=usage.cost_usd,
        latency_ms=latency_ms,
        conversation_id=conversation_id,
        # The production lane leaves this null on every row it has ever written,
        # which is why the table cannot answer "what does project X cost". This
        # lane knows the answer whenever the turn ran against exactly one
        # project; an ambiguous scope stays null rather than being attributed to
        # whichever project happened to be first.
        project_id=project_id[:_USAGE_PROJECT_ID_MAX_CHARS] if project_id else None,
        # `embedding_tokens`, `embedding_ms`, `search_ms` and `llm_ms` stay null.
        # The production lane can split its turn because it owns each phase; here
        # the whole turn happens inside OpenCode and the only honest split is
        # none at all.
    )
    try:
        db.add(row)
    except SQLAlchemyError:
        logger.error(
            "labs.usage_unrecorded conversation_id=%s: the turn's cost row could "
            "not be staged, so this turn is invisible to every cost query",
            conversation_id,
            exc_info=True,
        )
        return False
    return True


async def execute_labs_turn(
    *,
    db: Any,
    conversation: Any,
    user_id: int,
    user_groups: Sequence[str],
    project_scope: Sequence[str] | None,
    permissions: frozenset[str] | set[str],
    access_token: str | None,
    message: str,
    emit: EmitCallback,
    images: Sequence[str] = (),
    agent: str = LABS_AGENT,
    mode: LabsTurnMode = LabsTurnMode.CHAT,
) -> LabsTurnResult:
    """Run one labs turn end to end and report what happened.

    `db` is read here only to build the workspace: the guidance stack, and the
    project's uploaded material. This lane still persists nothing itself -- the
    route owns message persistence, and it is also the route that emits the
    terminal `assistant.completed` / `run.completed` frames once the assistant
    message has a real id.

    `agent` names one of the agent definitions the workspace's `opencode.json`
    carries, and applies to this turn only: OpenCode honours the message body's
    `agent` over the one the session was created with (verified -- see
    `opencode_client`'s module docstring). The default keeps the lane's existing
    behaviour, and :data:`LABS_PLAN_AGENT` is the read-only planning mode. The
    session is always *created* on the default agent, because a session outlives
    the turn and its creation agent would otherwise be decided by whichever mode
    the conversation happened to open in.

    `mode` is the evidence breadth for THIS turn, and it reaches the model twice
    because the two halves have different lifetimes. It goes on the scope, which
    is what the MCP bridge rebuilds the tool menu from; and it selects the system
    prompt written into the workspace's `opencode.json`, which
    `ensure_labs_workspace` rewrites on every turn. Nothing about it is persisted
    on the conversation: the same thread may ask an ordinary question and then a
    deep one, and a stored mode would make the second answer depend on the first
    turn's toggle.
    """

    started = time.monotonic()
    conversation_id = int(conversation.id)
    project_id = _workspace_project_id(project_scope)

    mcp_token, session_id = await _resolve_binding(
        conversation_id=conversation_id,
        persisted_session_id=_persisted_session_id(conversation),
        scope_factory=lambda workspace_dir: _build_scope(
            conversation_id=conversation_id,
            user_id=user_id,
            user_groups=user_groups,
            project_scope=project_scope,
            permissions=permissions,
            access_token=access_token,
            workspace_dir=workspace_dir,
            mode=mode,
        ),
    )

    # Guidance tunes an answer; it must never be able to prevent one.
    # `compile_guidance` already absorbs its own database failures and returns an
    # empty stack, so anything reaching this handler is a defect below it -- which
    # is worth the full traceback and worth exactly nothing to the user waiting on
    # a read-only question. `None` makes the workspace fall back to the code-owned
    # invariants, which are what the safety floor actually rests on.
    guidance: ResolvedGuidance | None = None
    try:
        guidance = await compile_guidance(db, project_id=project_id, user_id=user_id)
    except Exception:  # deliberately blind: see above
        logger.error(
            "Labs guidance compile failed for conversation %s (project_id=%s user_id=%s); "
            "falling back to the code-owned invariants",
            conversation_id,
            project_id or "-",
            user_id,
            exc_info=True,
        )

    # The project's uploaded material, guarded exactly like the guidance compile
    # above and for the same reason. Both functions are specified never to raise,
    # so anything caught here is a defect below this line -- worth the traceback,
    # worth nothing to the person waiting on a read-only question. A workspace
    # that mounts nothing still answers; a turn that dies because one document's
    # text could not be loaded does not.
    material: ProjectMaterial | None = None
    material_texts: dict[int, str] = {}
    try:
        material = await compile_project_material(
            db,
            project_id=project_id,
            # The union the Qdrant search filter composes internally -- role
            # names plus the caller's assigned project ids -- not the role names
            # alone this lane carries in `user_groups`. `_caller_may_read` tests
            # membership of the project id itself, so passing roles only would
            # fail closed for every non-admin and leave the manifest empty on
            # exactly the projects a consultant actually works in. Composed here
            # because this is the one place that holds both halves.
            user_groups=[*user_groups, *(project_scope or ())],
        )
        material_texts = await load_material_texts(
            db, [entry.document_id for entry in material.mountable]
        )
    except Exception:  # deliberately blind: see above
        logger.error(
            "Labs project material failed to compile for conversation %s "
            "(project_id=%s user_id=%s); the workspace will mount no material",
            conversation_id,
            project_id or "-",
            user_id,
            exc_info=True,
        )
        material = None
        material_texts = {}

    # What the documentation run recorded about this project: its vocabulary,
    # what the scan could not see, where a person corrected it, and what it
    # could not settle. `compile_generation_memory` absorbs its own failures
    # and returns None, which the workspace treats exactly as "no memory" --
    # the same resolution as `material=None`, because an extract of a record
    # this process does not own must read as absent when it cannot be trusted.
    memory: GenerationMemory | None = await compile_generation_memory(project_id)

    workspace_dir = await ensure_labs_workspace(
        conversation_id=conversation_id,
        project_id=project_id,
        mcp_token=mcp_token,
        guidance=guidance,
        material=material,
        material_texts=material_texts,
        memory=memory,
        mode=mode,
    )

    answer_text = ""
    # Uploads arrive base64-encoded from the browser and go straight through as
    # file parts; nothing is written to disk. Only images are handled here --
    # a document belongs in the workspace, where it survives past this turn.
    attachments = tuple(
        Attachment.from_base64(
            payload,
            mime=_sniff_image_mime(payload),
            filename=f"upload-{n}",
        )
        for n, payload in enumerate(images, start=1)
        if payload
    )

    streamed_text = ""
    #: Populated from the bus. Stays empty when the subscription never went
    #: live, which is itself worth seeing in the log rather than reading as
    #: "this turn ran no tools".
    tool_counts: collections.Counter[str] = collections.Counter()
    #: Per-step spend off the bus. Populated in the same `finally` as
    #: `tool_counts`, so a turn that hit the hard deadline still accounts for the
    #: steps it had already paid for.
    step_usage: list[BusStepUsage] = []
    #: The reply's own `(cost, tokens)`, kept only as the fallback for a turn
    #: whose bus subscription never went live. It covers the LAST assistant
    #: message alone, so it is a floor on what the turn cost, not the total.
    reply_usage: tuple[float | None, dict[str, Any] | None] | None = None
    finish_reason: str | None = None
    stop_reason = "answered"
    turn_error: str | None = None

    try:
        async with OpenCodeClient() as client:
            if session_id is None:
                session = await client.create_session(
                    directory=workspace_dir, agent=LABS_AGENT
                )
                session_id = session.id
                # Recorded the moment it exists, not at the end of the turn: a
                # turn that then fails, or whose client walks away, still opened
                # a real session carrying this conversation's history, and the
                # next turn has to find it rather than open a second one.
                _remember_session_id(conversation, session_id)
            _CONVERSATION_BINDINGS[conversation_id] = (mcp_token, session_id)

            await emit(
                "run.started",
                {
                    "status": "running",
                    "cohort": "labs_opencode",
                    # Tokens reach the client as the model produces them, over
                    # the OpenCode event bus. The value is part of the production
                    # vocabulary, so the UI and any downstream analysis read it
                    # unchanged.
                    "transport": "stream",
                    "objective": message[:200],
                },
            )

            if agent != LABS_AGENT:
                # Only a non-default mode is announced. The default is the lane's
                # normal behaviour and narrating it on every turn would train the
                # user to ignore the line that matters. Payload keys are the
                # production `runtime.update` set exactly -- `status`, `phase`,
                # `summary` (`_PUBLIC_EVENT_FIELDS["runtime.update"]`) -- so the
                # existing progress renderer picks it up with no frontend change.
                await emit(
                    "runtime.update",
                    {
                        "status": "running",
                        "phase": f"agent:{agent}",
                        "summary": (
                            f"Plan mode: this turn runs on the read-only {agent} agent, "
                            "which drafts a numbered plan instead of acting."
                        ),
                    },
                )

            stream = _BusStream(emit=emit)
            subscriber = asyncio.create_task(
                _subscribe_to_bus(
                    client,
                    session_id=session_id,
                    directory=workspace_dir,
                    stream=stream,
                )
            )
            try:
                # Subscribe before prompting. The bus has no replay, so every
                # frame emitted before the stream is live is gone for good.
                try:
                    await asyncio.wait_for(
                        stream.connected.wait(), timeout=_BUS_CONNECT_TIMEOUT_SECONDS
                    )
                except TimeoutError:
                    logger.error(
                        "Labs event bus did not connect within %ss for session %s; "
                        "running the turn without live narration",
                        _BUS_CONNECT_TIMEOUT_SECONDS,
                        session_id,
                        exc_info=True,
                    )

                remaining = HARD_DEADLINE_SECONDS - (time.monotonic() - started)
                try:
                    reply = await asyncio.wait_for(
                        client.send_message(
                            session_id,
                            message,
                            directory=workspace_dir,
                            attachments=attachments,
                            model=LABS_MODEL,
                            agent=agent,
                        ),
                        timeout=max(remaining, 1.0),
                    )
                except TimeoutError:
                    stop_reason = "hard_deadline_exceeded"
                    turn_error = (
                        "The OpenCode turn exceeded the 300s deadline and was interrupted."
                    )
                    try:
                        await client.interrupt(session_id, directory=workspace_dir)
                    except OpenCodeError as exc:
                        logger.error(
                            "Failed to interrupt labs session %s: %s",
                            session_id,
                            exc,
                            exc_info=True,
                        )
                else:
                    answer_text = reply.text.strip()
                    finish_reason = reply.finish
                    turn_error = _turn_failure(reply)
                    reply_usage = (reply.cost, reply.tokens)
                    # The blocking call can return a moment before the bus has
                    # flushed its last deltas and `session.idle`. Bounded, so a
                    # mute bus costs a fixed two seconds rather than the turn.
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(
                            stream.finished.wait(), timeout=_BUS_DRAIN_SECONDS
                        )
            finally:
                await _stop_subscriber(subscriber)
                streamed_text = stream.text.strip()
                tool_counts = stream.tool_counts
                step_usage = stream.step_usage

    except OpenCodeUnavailable as exc:
        logger.error(
            "OpenCode unavailable for labs conversation %s: %s",
            conversation_id,
            exc,
            exc_info=True,
        )
        raise LabsRuntimeUnavailable(
            "The labs OpenCode server is not reachable."
        ) from exc
    except OpenCodeError as exc:
        logger.error(
            "OpenCode turn failed for labs conversation %s: %s",
            conversation_id,
            exc,
            exc_info=True,
        )
        stop_reason = "runtime_failed"
        turn_error = str(exc)
        session_id = session_id or ""

    # Drained by conversation, not by `mcp_token`. The token is re-mintable and
    # both sides can hold different values for one turn, so keying the ledger by
    # it meant a rotation silently emptied the turn's evidence trail; see
    # `mcp_bridge._tool_calls`.
    tool_calls = drain_tool_calls(conversation_id)
    _warn_on_lost_capability_records(
        conversation_id=conversation_id,
        drained=tool_calls,
        tool_counts=tool_counts,
    )
    if turn_error and stop_reason == "answered":
        stop_reason = "runtime_failed"

    answer_text = scrub_tool_call_markup(
        _resolve_answer_text(reply_text=answer_text, streamed_text=streamed_text)
    )

    # Capability calls are drained after the turn, so `tool.completed` is
    # necessarily late; the live `tool.running` frames already told the client
    # that something was happening. The labels will not match one for one:
    # `tool.running` covers every OpenCode tool (bash, read, ...), while
    # `tool.completed` covers only the Nexus capabilities the MCP bridge records.
    await _emit_tool_calls(emit, tool_calls)
    if answer_text and answer_text != streamed_text:
        # What the client has on screen is not what is about to be persisted:
        # either nothing streamed at all (mute bus, renamed frames) or the
        # stream was cut short and the reply won. Retract the draft first --
        # deltas append, so emitting the answer on top of a fragment of itself
        # would render the fragment twice. `assistant.draft_reset` is the
        # production primitive for exactly this.
        if streamed_text:
            await emit("assistant.draft_reset", {"status": "discarded"})
        await emit("assistant.delta", {"content": answer_text})

    latency_ms = int((time.monotonic() - started) * 1000)
    # `tool_calls` is capability calls, `all_tool_calls` is every tool the turn
    # ran. Both are reported because they answer different questions and the
    # difference between them is the finding: a turn reading `docs/` with `grep`
    # and `read` spends evidence the capability count cannot see. `tool_breakdown`
    # names which, so "12 calls" can be read as "4 searches and 8 file reads"
    # without reconstructing it from the OpenCode session afterwards.
    # `mode` is logged because without it a turn's tool menu cannot be explained
    # after the fact. A deep-dive turn that used no scan tool and a chat turn
    # that could not have used one produce the SAME breakdown, and the only way
    # anyone could tell them apart was to guess from what the answer said. That
    # guess was wrong at least once.
    logger.info(
        "labs.turn conversation_id=%s session=%s agent=%s mode=%s latency_ms=%s "
        "tool_calls=%s all_tool_calls=%s tool_breakdown=%s streamed_chars=%s "
        "stop_reason=%s finish=%s",
        conversation_id,
        session_id or "-",
        agent,
        mode.value if hasattr(mode, "value") else mode,
        latency_ms,
        len(tool_calls),
        sum(tool_counts.values()),
        ",".join(f"{name}={count}" for name, count in sorted(tool_counts.items())) or "-",
        len(streamed_text),
        stop_reason,
        finish_reason or "-",
    )

    usage = _turn_usage([(step.cost, step.tokens) for step in step_usage])
    usage_source = "bus"
    if not usage.observed and reply_usage is not None:
        # The bus is best effort by construction (`_subscribe_to_bus` swallows
        # its own failures so a cosmetic problem cannot kill a running turn), so
        # a turn that answered over a dead subscription still has the reply's
        # numbers. They are a floor rather than the total -- the reply is the
        # last step of the turn -- and the source is recorded so an
        # under-reported row can be recognised instead of read as a cheap turn.
        usage = _turn_usage([reply_usage])
        usage_source = "reply"

    if usage.observed:
        # Recorded whenever the turn demonstrably spent money, including when it
        # then failed. The rule is "no row for a turn that produced nothing",
        # and its reason is that a zero row reads as a free turn; a turn that
        # burned four steps before the hard deadline cut it off did not produce
        # nothing, and dropping its cost would put the lane back where it
        # started -- spend with no record. A turn that reported no usage at all
        # still writes no row.
        recorded = _stage_turn_usage(
            db=db,
            conversation_id=conversation_id,
            user_id=user_id,
            project_id=project_id,
            usage=usage,
            latency_ms=latency_ms,
        )
        # `cached_input_tokens` and `cache_write_tokens` have no column, so this
        # line is the only place the cache split survives. The cached share is
        # the difference between a turn that cost cents and one that cost
        # dollars, and it should not vanish just because the table cannot hold it.
        logger.info(
            "labs.usage conversation_id=%s source=%s steps=%s cost_usd=%s "
            "input_tokens=%s output_tokens=%s cached_input_tokens=%s "
            "cache_write_tokens=%s project_id=%s recorded=%s",
            conversation_id,
            usage_source,
            usage.steps,
            usage.cost_usd,
            usage.input_tokens,
            usage.output_tokens,
            usage.cached_input_tokens,
            usage.cache_write_tokens,
            project_id or "-",
            recorded,
        )
    elif answer_text:
        # An answer with no usage behind it means the bus reported no step and
        # the reply carried no numbers either. The turn was not free, so this is
        # a gap in the cost record and is worth saying out loud rather than
        # leaving as a silently missing row.
        logger.warning(
            "labs.usage_missing conversation_id=%s session=%s: the turn answered "
            "but neither the event bus nor the reply reported any spend, so it "
            "is absent from query_usage",
            conversation_id,
            session_id or "-",
        )

    if turn_error:
        await emit("runtime.update", {"status": "degraded", "phase": stop_reason,
                                      "summary": turn_error[:200]})

    return LabsTurnResult(
        answer_text=answer_text or (turn_error or ""),
        tool_calls=tool_calls,
        opencode_session_id=session_id or "",
        latency_ms=latency_ms,
    )


def new_labs_run_id() -> str:
    """A per-turn run identity for SSE framing; labs has no durable run rows."""

    return f"labs-{uuid.uuid4().hex}"


__all__ = [
    "HARD_DEADLINE_SECONDS",
    "LABS_AGENT",
    "LABS_MODEL",
    "LABS_PLAN_AGENT",
    "LABS_USAGE_INTERFACE",
    "LABS_USAGE_MODEL_NAME",
    "STREAM_REASONING",
    "LabsClaimGuard",
    "LabsRuntimeUnavailable",
    "LabsTurnResult",
    "LabsTurnUsage",
    "execute_labs_turn",
    "labs_turn_claim",
    "new_labs_run_id",
]
