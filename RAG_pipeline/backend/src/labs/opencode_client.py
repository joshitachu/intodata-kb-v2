"""Typed async client for the OpenCode headless server (`opencode serve`).

There is no Python SDK for OpenCode on PyPI. This is a hand-written `httpx`
client over the server's HTTP surface. Nothing under `/experimental/` is used.

Everything below was verified against a live **OpenCode 1.18.7** server on
2026-09-16 unless explicitly flagged otherwise.

Two API families, two envelopes
-------------------------------
The server exposes two overlapping API families and they do **not** agree on
their response envelope:

* **v1** -- `/session`, `/auth`, `/config`, `/event`. Responses are the payload
  itself, **unwrapped**.
* **v2** -- everything under `/api`. Responses are wrapped in ``{"data": ...}``,
  and list endpoints add sibling ``cursor``/``location`` keys.

`_wraps_envelope` decides per path which of the two applies; unwrapping is
deliberately *not* global, because doing it unconditionally silently corrupts
every v1 payload that happens to carry its own ``data`` key.

Unknown *top-level* paths (e.g. `/session/{id}/interrupt`, which does not exist
in v1) fall through to the web UI and return **HTML with HTTP 200**, so a typo'd
endpoint would decode as garbage and the status code proves nothing. `_decode`
therefore rejects any non-JSON content type outright.

Running a turn -- use v1, it is synchronous
-------------------------------------------
``POST /session/{sessionID}/message?directory=<abs-dir>`` runs the whole turn
inside the request and returns the finished assistant message::

    {
      "model": {"providerID": "moonshot", "modelID": "kimi-k2.6"},
      "agent": "nexus",                      # optional
      "tools": {"read": true, "bash": false},# optional, per-call allowlist
      "system": "...",                       # optional, per-call system prompt
      "parts": [{"type": "text", "text": "<the user message>"}]
    }

Note ``modelID`` here, **not** ``id`` -- ``id`` is the session-create spelling.

The response is ``{"info": {...}, "parts": [...]}``:

* ``info.finish`` is ``"stop"`` on success. Other values seen in the protocol:
  ``"tool-calls"``, ``"error"``, ``"length"``, ``"aborted"``.
* ``info.error`` is absent (or null) on success, otherwise ``{"type", "message"}``.
* ``info.tokens`` is ``{"total", "input", "output", "reasoning",
  "cache": {"read", "write"}}`` and ``info.cost`` is a float. Both live on
  ``info``, and the terminal ``step-finish`` part repeats them for the last step
  only. :class:`OpenCodeReply` reads them off ``info`` defensively.
* ``info.id`` is the assistant message id; ``info.agent`` echoes the agent that
  actually ran.
* ``parts`` is an ordered list. Observed part types: ``step-start``,
  ``reasoning``, ``text``, ``step-finish``. Assistant prose is every
  ``{"type": "text", "text": ...}`` entry, in order.

Verified end to end: a session created in a workspace whose `opencode.json`
defines a ``nexus`` agent answered with ``finish: "stop"``, ``error`` absent and
the expected text in a single ``text`` part.

``agent`` is honoured **per message**, and it overrides the agent the session was
created with. Probed live on 2026-09-18 against 1.18.7 in a workspace defining
two agents whose system prompts each opened with a distinct sentinel token. Both
sessions were created with ``agent: "nexus"``; only the message body differed::

    body agent="nexus" -> info.agent == "nexus", text starts "SENTINEL_NEXUS ..."
    body agent="plan"  -> info.agent == "plan",  text starts "SENTINEL_PLAN ..."

So the field selects the agent definition -- prompt, tool map and ``maxSteps``
all follow it -- and ``info.agent`` in the reply is the server's own confirmation
of which one actually ran. Per-turn agent switching needs no ``system`` override
and no second session.

**It returns only the LAST assistant message, not the whole turn.** Verified on
2026-09-16: a prompt that made the model narrate, call a tool, then answer
produced *two* assistant messages, and the response carried only the second.
``GET /session/{id}/message`` afterwards listed both::

    msg_...1fuhLtD5gik5vN  assistant  [step-start, reasoning, text, tool, step-finish]
                                      text = "Checking the directory now."
    msg_...mSoMSQ9MxyC7zz  assistant  [step-start, text, step-finish]
                                      text = "Directory contains `opencode.json`. DONE"

``send_message`` returned only the latter, so any prose the model emitted before
a tool call is **absent** from :attr:`OpenCodeReply.text`. The event bus does
carry it. A caller that needs the complete turn must either read the bus or
re-read the session history; treating the reply as "the answer" silently drops
the earlier blocks of a multi-step turn.

What a turn costs -- per step, never cumulative
-----------------------------------------------
``info.cost`` and ``info.tokens`` are **that assistant message's own** usage.
They do not accumulate: nothing has to be diffed against a running total, and
diffing would be wrong. Verified on 2026-09-21 by replaying two live labs
sessions off ``GET /session/{id}/message``. One of them holds two turns, and the
second turn's single assistant message cost ``$0.00114675`` -- far *below* the
first turn's ``$0.014148``, which a cumulative figure could not be.

``tokens`` is additive and disjoint, not nested::

    total == input + output + reasoning + cache.read + cache.write

verified on every one of the seven assistant messages measured. Two consequences
for anyone converting these numbers into a cost record:

* ``input`` **excludes** the cached prefix. One message reported ``input: 246``
  against ``cache.read: 10880`` -- a prompt of 11126 tokens, 98% of it served
  from cache. Reading ``input`` as "the prompt" under-reports it by that ratio.
* ``output`` **excludes** ``reasoning``. One step reported ``output: 6048`` and
  ``reasoning: 1808``; both are billed at the output rate.

``tokens.total`` rises across a session, which makes it *look* cumulative. It is
not -- it rises because ``cache.read`` grows with the conversation's context.

The trap is the opposite of accumulation: because the reply carries only the
**last** assistant message, ``reply.cost`` is one step of a turn that routinely
runs four or five. Measured against those same two sessions, the reply carried
**37%** and **69%** of what the turn actually cost. The whole turn is obtainable
without a second request only from the bus, whose ``step-finish`` parts report
each step as it completes -- exactly one per assistant message in every session
measured, and equal to that message's ``info.cost``. See
:func:`bus_event_step_usage`.

The live event bus -- `GET /event`, and it *requires* `directory`
------------------------------------------------------------------
A v1 turn is synchronous, but the server narrates it on a server-sent-event bus
while the request is still in flight. That is what makes live token streaming
possible without touching the v2 turn mechanism (``POST /api/session/{id}/prompt``,
which on 1.18.7 accepts a prompt and then never produces an assistant message).

Probed live on 2026-09-16 against OpenCode 1.18.7 with a two-step, tool-using
turn in `/labs-workspaces/freetest`:

* ``GET /event?directory=<abs-dir>`` -> 62 frames, `text/event-stream`. **This
  is the working subscription.**
* ``GET /event`` with **no params** -> ``server.connected`` and
  ``server.heartbeat`` only. Session traffic never appears on it, across two
  independent runs. The ``directory`` query parameter is **mandatory**: it
  selects which project's bus you attach to, and the default is not the one your
  session runs in.
* ``GET /session/{id}/event`` -> **does not exist**. It falls through to the web
  UI and returns the SPA shell as ``text/html`` with HTTP 200.
* ``GET /api/event?directory=...`` -> emits, but delivered a strict subset (5 of
  the same 62 frames in the same window). Not used.

Framing: ``data:`` lines only. There is no SSE ``event:`` name field, so dispatch
is on the JSON ``type`` key.

Event shapes, with the concrete field paths this module reads
-------------------------------------------------------------
Every session-scoped event carries ``properties.sessionID``. The bus is shared,
so filtering on it is not optional.

* ``message.part.delta`` -- incremental text.
  ``properties.{sessionID, messageID, partID, field, delta}``.
  **``field`` is ``"text"`` for reasoning parts *and* answer parts alike**; the
  delta frame itself does not say which. Correlate ``properties.partID`` against
  the part type to keep chain-of-thought out of an answer stream.
* ``message.part.updated`` -- a part reached a new state.
  ``properties.part.{id, type, messageID, sessionID}``; ``type`` observed as
  ``step-start``, ``reasoning``, ``tool``, ``text``, ``step-finish``.
  Tool parts add ``properties.part.tool`` (the tool name, e.g. ``"bash"``),
  ``properties.part.callID`` and ``properties.part.state`` with ``status`` in
  ``pending`` / ``running`` / ``completed`` / ``error`` plus ``input``,
  ``output``, ``metadata`` and, once completed, ``title``.
  **Tool activity is visible here and nowhere else on the bus.**
  ``step-finish`` parts add ``properties.part.{cost, tokens}`` -- the same two
  values ``info.cost`` and ``info.tokens`` carry on the assistant message the
  step belongs to. **A turn's full spend is visible here and nowhere else
  without a second HTTP call**; see "What a turn costs" below.
* ``message.updated`` -- ``properties.info.{id, role, ...}``; ``role`` separates
  the echoed user message from the assistant's.
* ``session.status`` -- ``properties.status.type`` in ``busy`` / ``idle``.
* ``session.idle`` -- ``properties.sessionID`` only. Emitted once, at the end of
  the turn. This is the completion signal.
* ``session.updated`` / ``session.diff`` -- session metadata and the workspace
  diff; not read here.
* ``server.connected`` / ``server.heartbeat`` -- lifecycle, ``properties`` is
  ``{}``, no session id. ``server.connected`` arrives immediately on subscribe
  and is the proof that the stream is live.

Ordering, verified in the capture: the ``message.part.updated`` that announces a
part always precedes that part's first ``message.part.delta``. A
``partID -> type`` map built from ``message.part.updated`` is therefore always
populated by the time a delta for that part arrives.

The bus has **no replay**. Anything emitted before the subscription is live is
lost, so a caller must subscribe *before* sending the prompt.

Authentication (verified empirically, not guessed)
--------------------------------------------------
When ``OPENCODE_SERVER_PASSWORD`` is set, the server answers unauthenticated
requests with ``401`` and ``WWW-Authenticate: Basic realm="Secure Area"``.
Confirmed by probe:

* HTTP Basic with username ``opencode`` -> 200.
* ``Authorization: Bearer <password>`` -> 401. Bearer is **not** supported.
* An empty or arbitrary username -> 401. The username is checked.
* Setting ``OPENCODE_SERVER_USERNAME=nexus`` makes ``nexus`` the only accepted
  username and ``opencode`` starts returning 401.

So the username is a real credential component, defaulting to ``opencode``.

Provider credentials
--------------------
``PUT /auth/{providerID}`` with ``{"type": "api", "key": "<key>"}`` returns
``true`` (200) and is the only supported way to give the server a provider key.
Do **not** put the key in a workspace `opencode.json`: ``{env:VAR}``
interpolation works for `opencode run` but is not resolved in serve mode.

``GET /config/providers`` returns provider API keys **in plaintext**. It is
deliberately not implemented here, and no `/config*` response may ever be logged.

Other verified behaviour
------------------------
* ``POST /session/{id}/abort`` -> ``true`` (200). This is the v1 abort; the v2
  ``POST /api/session/{id}/interrupt`` returns 204 but was never confirmed to
  stop a v1 turn, so :meth:`OpenCodeClient.interrupt` uses the v1 path.
* v1 session create accepts ``agent``, ``title`` and ``model`` but validates
  none of them: a bogus agent name is stored and only fails later, on the
  message call, as a 500.
* v1 errors are ``{"name": "UnknownError", "data": {"message": ..., "ref": ...}}``
  with HTTP 500; v2 errors are ``{"_tag": "SessionNotFoundError", "message": ...}``.
  :meth:`OpenCodeClient._raise_for_status` reads both.
* The v2 session store did **not** surface v1 sessions on 1.18.7:
  ``GET /api/session/{id}/message`` returned an empty ``data`` list for a session
  that `GET /session/{id}/message` reported three messages for. Every
  session-scoped read here therefore uses v1.
* ``GET /api/model`` returns an empty ``data`` list for a second or two after
  startup while providers load; it is not an error.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Self

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL: Final = "http://127.0.0.1:4096"

# The server checks the Basic-auth username, not just the password. `opencode`
# is the built-in default; OPENCODE_SERVER_USERNAME replaces it outright.
DEFAULT_SERVER_USERNAME: Final = "opencode"

ENV_BASE_URL: Final = "OPENCODE_SERVER_URL"
ENV_PASSWORD: Final = "OPENCODE_SERVER_PASSWORD"
ENV_USERNAME: Final = "OPENCODE_SERVER_USERNAME"

# `POST /session/{id}/message` blocks for the entire turn -- model latency, tool
# round-trips and all -- so the read timeout has to cover a whole turn, not a
# round-trip. It matches the runtime's hard deadline.
TURN_READ_TIMEOUT_SECONDS: Final = 300.0

# --- global event bus (see the module docstring for the probe that fixed these)
BUS_EVENT_PART_DELTA: Final = "message.part.delta"
BUS_EVENT_PART_UPDATED: Final = "message.part.updated"
BUS_EVENT_SESSION_IDLE: Final = "session.idle"

# The part types `message.part.updated` announces. `reasoning` is the model's
# chain of thought and streams through the very same delta frames as `text`;
# only `text` is answer prose.
#
# Re-verified on 2026-09-18 with `deepseek/deepseek-flash`, which reasons
# heavily: one turn produced 1283 `message.part.delta` frames whose `field` was
# `"text"` for every single one, split across exactly two parts -- 3072
# characters announced as `reasoning` and 2704 announced as `text`. The part id
# is the only thing that separates them, which is why `BUS_PART_REASONING` is a
# named constant and not an inline literal.
BUS_PART_TEXT: Final = "text"
BUS_PART_TOOL: Final = "tool"
BUS_PART_REASONING: Final = "reasoning"

# The terminal part of one model step, and the only frame on the bus that carries
# what that step cost. One per assistant message in every session measured.
BUS_PART_STEP_FINISH: Final = "step-finish"

_ENVELOPE_KEY: Final = "data"
_V2_PREFIX: Final = "/api"

# Multiple `text` parts in one reply are separate assistant text blocks, one per
# model step (typically: narration, tool call, answer). They are joined with a
# blank line because that is what separates blocks in the rendered answer;
# concatenating them bare would run the last word of one into the first of the next.
_TEXT_PART_SEPARATOR: Final = "\n\n"


class OpenCodeError(RuntimeError):
    """Any failure talking to the OpenCode server."""


class OpenCodeUnavailable(OpenCodeError):
    """The OpenCode server could not be reached at all.

    Raised only for connection-level failures. It lets a caller tell "OpenCode
    isn't running" apart from "OpenCode ran and the model failed".
    """


def _wraps_envelope(path: str) -> bool:
    """True when `path` belongs to the v2 family, whose replies wrap in `data`.

    Per-path rather than global: v1 (`/session`, `/auth`, ...) returns payloads
    unwrapped, and stripping a `data` key off those would silently return the
    wrong object for any payload that has one of its own.
    """

    return path == _V2_PREFIX or path.startswith(f"{_V2_PREFIX}/")


@dataclass(frozen=True, slots=True)
class ModelRef:
    """Reference to a provider-qualified model, e.g. `moonshot` / `kimi-k2.6`."""

    id: str
    provider_id: str
    variant: str | None = None

    def to_payload(self) -> dict[str, str]:
        """Session-create spelling: `{"id", "providerID"}`."""
        payload = {"id": self.id, "providerID": self.provider_id}
        if self.variant is not None:
            payload["variant"] = self.variant
        return payload

    def to_message_payload(self) -> dict[str, str]:
        """Message spelling: `{"providerID", "modelID"}`.

        The two endpoints disagree on the key name for the model id; sending the
        session-create spelling to `POST /session/{id}/message` is a 500.
        `providerID`/`modelID` are verified live; `variant` is carried through
        unchanged but was never exercised on this endpoint.
        """
        payload = {"providerID": self.provider_id, "modelID": self.id}
        if self.variant is not None:
            payload["variant"] = self.variant
        return payload


@dataclass(frozen=True, slots=True)
class Attachment:
    """One file sent with a prompt.

    `url` is either a `data:<mime>;base64,...` URI or a path the server can read.
    The data-URI form is what the labs lane uses: the browser already base64
    encodes an upload, so it travels end to end without ever touching disk.
    """

    mime: str
    url: str
    filename: str = "attachment"

    @classmethod
    def from_base64(cls, payload: str, *, mime: str = "image/png",
                    filename: str = "attachment") -> Attachment:
        """Build one from a raw base64 body or an already-formed data URI."""

        url = payload if payload.startswith("data:") else f"data:{mime};base64,{payload}"
        return cls(mime=mime, url=url, filename=filename)


@dataclass(frozen=True, slots=True)
class OpenCodeSession:
    """A created session and the workspace directory it is bound to."""

    id: str
    directory: str


@dataclass(frozen=True, slots=True)
class OpenCodeReply:
    """One finished assistant turn, as returned by `POST /session/{id}/message`.

    `raw` is the whole response so a caller can reach anything this dataclass
    does not model; nothing here invents a field the server did not send.
    """

    text: str
    finish: str | None
    error: dict[str, Any] | None
    parts: list[dict[str, Any]] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        """True only for a clean stop. `tool-calls`, `length` and `aborted` are not."""
        return self.finish == "stop" and self.error is None

    @property
    def message_id(self) -> str | None:
        """`info.id` -- OpenCode's id for the assistant message."""
        value = self.info.get("id")
        return value if isinstance(value, str) else None

    @property
    def tokens(self) -> dict[str, Any] | None:
        """`info.tokens`, or None when the server did not report usage."""
        value = self.info.get("tokens")
        return value if isinstance(value, dict) else None

    @property
    def cost(self) -> float | None:
        """`info.cost`, or None when the server did not report a cost."""
        value = self.info.get("cost")
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def error_message(self) -> str | None:
        """A short human-readable reason, or None when the turn did not error."""
        if self.error is None:
            return None
        message = self.error.get("message") or self.error.get("type") or ""
        return str(message)[:500] or "unknown OpenCode error"


@dataclass(frozen=True, slots=True)
class BusStepUsage:
    """What one finished model step consumed, as the bus reported it.

    `tokens` is passed through in OpenCode's own shape rather than normalised
    here, because the normalisation a caller needs depends on what it is
    comparing against -- see the module docstring for the two ways that shape
    differs from the OpenAI-style one (cache is disjoint from `input`, reasoning
    is disjoint from `output`).

    `part_id` is carried so a caller can count a step once. The bus re-emits a
    part's frame on every state change, and a turn that counted one step twice
    would report double what it cost.
    """

    part_id: str
    cost: float | None
    tokens: dict[str, Any] | None


# ----------------------------------------------------------------------------
# Bus event accessors
#
# Every assumption about the bus payload shape lives in this block and nowhere
# else, so a server-side rename shows up as one edit rather than a scatter of
# silent `None`s across the runtime. Each carries a NOTE naming the event type
# and the exact field path it reads. All are pure.
# ----------------------------------------------------------------------------


def _bus_properties(event: dict[str, Any]) -> dict[str, Any]:
    properties = event.get("properties")
    return properties if isinstance(properties, dict) else {}


def _bus_part(event: dict[str, Any]) -> dict[str, Any]:
    part = _bus_properties(event).get("part")
    return part if isinstance(part, dict) else {}


def bus_event_session_id(event: dict[str, Any]) -> str | None:
    """The session an event belongs to, or None for a server lifecycle frame.

    # NOTE: every session-scoped event -> `properties.sessionID`.
    # `message.part.updated` repeats it at `properties.part.sessionID` and
    # `message.updated` at `properties.info.sessionID`; both are read as
    # fallbacks so one payload variant cannot silently defeat the session
    # filter on a shared bus.
    """

    properties = _bus_properties(event)
    for candidate in (
        properties.get("sessionID"),
        _bus_part(event).get("sessionID"),
        properties.get("info", {}).get("sessionID")
        if isinstance(properties.get("info"), dict)
        else None,
    ):
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def bus_event_text_delta(event: dict[str, Any]) -> str | None:
    """Incremental assistant text from one delta frame, else None.

    # NOTE: `message.part.delta` -> `properties.delta`, admitted only when
    # `properties.field == "text"`.
    #
    # CAUTION: reasoning parts stream through this same event type with this
    # same field value. This helper cannot tell them apart on its own -- pair it
    # with :func:`bus_event_delta_part_id` and :func:`bus_event_part_kind` and
    # forward only the deltas of a part whose announced type is `text`.
    """

    if event.get("type") != BUS_EVENT_PART_DELTA:
        return None
    properties = _bus_properties(event)
    if properties.get("field") != "text":
        return None
    delta = properties.get("delta")
    return delta if isinstance(delta, str) and delta else None


def bus_event_delta_part_id(event: dict[str, Any]) -> str | None:
    """Which part a delta frame belongs to, else None.

    # NOTE: `message.part.delta` -> `properties.partID`.
    """

    if event.get("type") != BUS_EVENT_PART_DELTA:
        return None
    part_id = _bus_properties(event).get("partID")
    return part_id if isinstance(part_id, str) and part_id else None


def bus_event_part_kind(event: dict[str, Any]) -> tuple[str, str] | None:
    """`(part id, part type)` announced by a part-state frame, else None.

    # NOTE: `message.part.updated` -> (`properties.part.id`,
    # `properties.part.type`). Observed types: `step-start`, `reasoning`,
    # `tool`, `text`, `step-finish`.
    """

    if event.get("type") != BUS_EVENT_PART_UPDATED:
        return None
    part = _bus_part(event)
    part_id = part.get("id")
    part_type = part.get("type")
    if isinstance(part_id, str) and part_id and isinstance(part_type, str) and part_type:
        return part_id, part_type
    return None


def bus_event_is_idle(event: dict[str, Any]) -> bool:
    """True when the turn finished.

    # NOTE: `session.idle`. Its `properties` carries `sessionID` and nothing
    # else, so the session filter is the only thing that scopes it.
    """

    return event.get("type") == BUS_EVENT_SESSION_IDLE


def bus_event_tool_label(event: dict[str, Any]) -> str | None:
    """A human-readable label for a tool part reaching a new state, else None.

    # NOTE: `message.part.updated` with `properties.part.type == "tool"` ->
    # `properties.part.state.title` when the server has produced one (it only
    # does once the call completes), otherwise `properties.part.tool`, which is
    # the bare tool name and is present from the first `pending` frame.
    #
    # One tool call produces several of these frames (pending -> running ->
    # completed); de-duplication is the caller's job, because only the caller
    # knows which transitions it wants to publish.
    """

    kind = bus_event_part_kind(event)
    if kind is None or kind[1] != BUS_PART_TOOL:
        return None
    part = _bus_part(event)
    state = part.get("state")
    if isinstance(state, dict):
        title = state.get("title")
        if isinstance(title, str) and title:
            return title
    tool = part.get("tool")
    return tool if isinstance(tool, str) and tool else None


def bus_event_tool_status(event: dict[str, Any]) -> str | None:
    """The lifecycle status of a tool part, else None.

    # NOTE: `message.part.updated` -> `properties.part.state.status`, observed
    # as `pending`, `running`, `completed` and (per the protocol) `error`.
    """

    kind = bus_event_part_kind(event)
    if kind is None or kind[1] != BUS_PART_TOOL:
        return None
    state = _bus_part(event).get("state")
    if not isinstance(state, dict):
        return None
    status = state.get("status")
    return status if isinstance(status, str) and status else None


def bus_event_tool_name(event: dict[str, Any]) -> str | None:
    """The bare tool name of a tool part, else None.

    # NOTE: `message.part.updated` with `properties.part.type == "tool"` ->
    # `properties.part.tool`, e.g. `"todowrite"` or `"nexus_read_topology_structure"`.
    #
    # Distinct from :func:`bus_event_tool_label`, which prefers the server's
    # human-readable `state.title` once a call completes. A caller that has to
    # DECIDE something about a call -- which tool this is -- needs the stable
    # name, not the display string; a caller that has to SHOW it wants the label.
    """

    kind = bus_event_part_kind(event)
    if kind is None or kind[1] != BUS_PART_TOOL:
        return None
    tool = _bus_part(event).get("tool")
    return tool if isinstance(tool, str) and tool else None


def bus_event_tool_input(event: dict[str, Any]) -> dict[str, Any] | None:
    """The arguments a tool part was called with, else None.

    # NOTE: `message.part.updated` with `properties.part.type == "tool"` ->
    # `properties.part.state.input`. Absent on the first `pending` frame of a
    # call and populated from `running` onwards, so a caller that wants the
    # arguments cannot also take the first-frame-only shortcut
    # `_maybe_announce_tool` takes.
    #
    # CAUTION: these are model-authored arguments. Nothing here may be published
    # wholesale -- the production event contract lists `arguments` among its
    # private keys precisely because a tool's input can carry a path, a query or
    # a credential. Read the one field you need and forward that.
    """

    kind = bus_event_part_kind(event)
    if kind is None or kind[1] != BUS_PART_TOOL:
        return None
    state = _bus_part(event).get("state")
    if not isinstance(state, dict):
        return None
    arguments = state.get("input")
    return arguments if isinstance(arguments, dict) else None


def bus_event_step_usage(event: dict[str, Any]) -> BusStepUsage | None:
    """What a finished model step cost, else None.

    # NOTE: `message.part.updated` with `properties.part.type == "step-finish"`
    # -> `properties.part.{cost, tokens}`. Both are optional here: a caller must
    # treat an absent field as "not reported", never as zero.
    #
    # This is the only route to a turn's FULL spend that costs no extra request.
    # `send_message` returns the last assistant message alone, so its `info.cost`
    # is one step of a turn that commonly runs four or five -- measured against
    # two live labs conversations, 37% and 69% of the real total. The bus
    # announces every step, and a caller streaming the turn is already
    # subscribed to it.
    """

    kind = bus_event_part_kind(event)
    if kind is None or kind[1] != BUS_PART_STEP_FINISH:
        return None
    part = _bus_part(event)
    cost = part.get("cost")
    tokens = part.get("tokens")
    return BusStepUsage(
        part_id=kind[0],
        cost=float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
        tokens=tokens if isinstance(tokens, dict) else None,
    )


def _reply_text(parts: list[dict[str, Any]]) -> str:
    """Join every `text` part, in order. Reasoning and tool parts are dropped."""

    chunks = [
        part["text"]
        for part in parts
        if isinstance(part, dict)
        and part.get("type") == "text"
        and isinstance(part.get("text"), str)
        and part["text"]
    ]
    return _TEXT_PART_SEPARATOR.join(chunks)


class OpenCodeClient:
    """Async client for one OpenCode headless server.

    Usable either as an async context manager (preferred) or standalone, in
    which case the underlying `httpx.AsyncClient` is created lazily and must be
    released with :meth:`aclose`.
    """

    def __init__(
        self,
        base_url: str | None = None,
        password: str | None = None,
        timeout: float = TURN_READ_TIMEOUT_SECONDS,
        *,
        username: str | None = None,
    ) -> None:
        resolved = base_url or os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL
        self._base_url = resolved.rstrip("/")
        self._password = password if password is not None else os.environ.get(ENV_PASSWORD)
        self._username = (
            username
            or os.environ.get(ENV_USERNAME)
            or DEFAULT_SERVER_USERNAME
        )
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> Self:
        self._ensure_client()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def base_url(self) -> str:
        return self._base_url

    def _auth(self) -> httpx.BasicAuth | None:
        if not self._password:
            return None
        return httpx.BasicAuth(self._username, self._password)

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout, connect=10.0),
                auth=self._auth(),
            )
        return self._client

    # ------------------------------------------------------------------
    # Transport helpers
    # ------------------------------------------------------------------

    def _decode(self, response: httpx.Response) -> Any:
        """Decode a JSON body, refusing the web-UI HTML fallback.

        An unrecognised top-level path returns the SPA shell with HTTP 200, so
        status alone cannot be trusted.
        """
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type.lower():
            raise OpenCodeError(
                f"{response.request.method} {response.request.url} returned "
                f"non-JSON content-type {content_type!r} (HTTP {response.status_code}); "
                "the path is probably not a real OpenCode API endpoint"
            )
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            logger.error("OpenCode returned undecodable JSON", exc_info=True)
            raise OpenCodeError(
                f"{response.request.method} {response.request.url} returned malformed JSON"
            ) from exc

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        detail = response.text[:500]
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError):
            body = None
        if isinstance(body, dict):
            # v2: {"_tag": ..., "message": ...}. v1: {"name": ..., "data": {"message", "ref"}}.
            tag = body.get("_tag") or body.get("name")
            message = body.get("message")
            nested = body.get(_ENVELOPE_KEY)
            if message is None and isinstance(nested, dict):
                parts = [nested.get("message"), nested.get("ref")]
                message = " ".join(str(part) for part in parts if part)
            if tag:
                detail = f"{tag}: {message}" if message else str(tag)
            elif message:
                detail = str(message)
        raise OpenCodeError(
            f"OpenCode {response.request.method} {response.request.url} "
            f"failed with HTTP {response.status_code}: {detail}"
        )

    @staticmethod
    def _unwrap(payload: Any) -> Any:
        if isinstance(payload, dict) and _ENVELOPE_KEY in payload:
            return payload[_ENVELOPE_KEY]
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> httpx.Response:
        client = self._ensure_client()
        extra: dict[str, Any] = {} if timeout is None else {"timeout": timeout}
        try:
            return await client.request(method, path, json=json_body, params=params, **extra)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            logger.error("OpenCode server unreachable at %s", self._base_url, exc_info=True)
            raise OpenCodeUnavailable(
                f"Cannot reach the OpenCode server at {self._base_url}: {exc}"
            ) from exc
        except httpx.ReadTimeout as exc:
            logger.error("OpenCode read timeout on %s %s", method, path, exc_info=True)
            raise OpenCodeError(
                f"OpenCode did not answer {method} {self._base_url}{path} within the "
                "read timeout; the turn was still running when the client gave up"
            ) from exc
        except httpx.TransportError as exc:
            logger.error("OpenCode transport error on %s %s", method, path, exc_info=True)
            raise OpenCodeError(
                f"Transport error on {method} {self._base_url}{path}: {exc}"
            ) from exc

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> Any:
        response = await self._request(
            method, path, json_body=json_body, params=params, timeout=timeout
        )
        self._raise_for_status(response)
        if response.status_code == 204 or not response.content:
            return None
        payload = self._decode(response)
        return self._unwrap(payload) if _wraps_envelope(path) else payload

    @staticmethod
    def _as_list(payload: Any, path: str) -> list[dict[str, Any]]:
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise OpenCodeError(f"Expected a list from {path}, got {type(payload).__name__}")
        return payload

    @staticmethod
    def _directory_params(directory: str | None) -> dict[str, Any] | None:
        return {"directory": directory} if directory else None

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        """`GET /api/health` -> `{"healthy": true}` (no `data` envelope on this one)."""
        payload = await self._call("GET", "/api/health")
        return payload if isinstance(payload, dict) else {"healthy": bool(payload)}

    async def set_auth(self, provider_id: str, api_key: str) -> None:
        """`PUT /auth/{providerID}` -- store an API key for a provider.

        This is the only credential path that works in serve mode: `{env:VAR}`
        interpolation inside a workspace `opencode.json` is resolved by
        `opencode run`, not by the server, so a key written there is never read.

        The key is never logged, and never echoed into an exception message --
        `_raise_for_status` reports the response body, not the request body.
        """

        if not provider_id or "/" in provider_id or provider_id.strip() != provider_id:
            raise ValueError(f"Invalid OpenCode provider id: {provider_id!r}")
        if not api_key:
            raise ValueError("Refusing to store an empty OpenCode API key")
        await self._call(
            "PUT", f"/auth/{provider_id}", json_body={"type": "api", "key": api_key}
        )
        logger.info("Stored an OpenCode API key for provider %s", provider_id)

    async def create_session(
        self,
        *,
        directory: str,
        model: ModelRef | None = None,
        agent: str | None = None,
        title: str | None = None,
    ) -> OpenCodeSession:
        """`POST /session?directory=...` -- create a session bound to a workspace.

        `directory` is a **query** parameter; there is no `location` body field
        in v1. The response is the session object itself, unwrapped.

        `agent` and `model` are accepted but not validated here: an unknown agent
        name is stored happily and only fails on the first :meth:`send_message`.
        """
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if model is not None:
            body["model"] = model.to_payload()
        if agent is not None:
            body["agent"] = agent

        payload = await self._call(
            "POST", "/session", json_body=body, params={"directory": directory}
        )
        if not isinstance(payload, dict):
            raise OpenCodeError(f"Unexpected session payload: {type(payload).__name__}")
        session_id = payload.get("id")
        if not isinstance(session_id, str) or not session_id:
            raise OpenCodeError(f"OpenCode returned a session without an id: {payload!r}")

        bound = payload.get("directory")
        return OpenCodeSession(
            id=session_id, directory=bound if isinstance(bound, str) and bound else directory
        )

    async def send_message(
        self,
        session_id: str,
        text: str,
        *,
        directory: str,
        model: ModelRef | None = None,
        agent: str | None = None,
        tools: dict[str, bool] | None = None,
        system: str | None = None,
        attachments: Sequence[Attachment] = (),
    ) -> OpenCodeReply:
        """`POST /session/{id}/message?directory=...` -- run one turn, synchronously.

        Blocks for the whole turn and returns the **last** assistant message, so
        the read timeout covers the turn rather than a round-trip. Use this
        instead of the v2 prompt/event pair, which never completes a turn on
        1.18.7 (see the module docstring).

        A multi-step turn produces one assistant message per step and only the
        last is returned, so prose the model emitted before a tool call is not in
        :attr:`OpenCodeReply.text`. That is verified, not theoretical -- see the
        module docstring. Use :meth:`stream_bus_events` (or :meth:`messages`) when
        the complete turn matters.

        `agent` selects one of the agent definitions in the workspace's
        `opencode.json` **for this turn only**, and overrides the agent the
        session was created with -- verified live, see the module docstring. Its
        prompt, tool map and `maxSteps` all take effect, and `info.agent` on the
        reply echoes which one ran. That makes it the mechanism for per-turn mode
        switching; `system` is not needed for it.

        `tools` is a per-call allowlist (`{"read": True, "bash": False}`) layered
        on top of the workspace's own policy; `system` is a per-call system
        prompt. Both are optional and neither replaces the workspace config.
        """

        parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
        # A file part rides alongside the text in the same message. `url` accepts
        # a data URI -- verified live against 1.18.7 with an image the model then
        # described correctly -- so nothing has to be written to disk for the
        # model to see it.
        parts.extend(
            {
                "type": "file",
                "mime": item.mime,
                "filename": item.filename,
                "url": item.url,
            }
            for item in attachments
        )
        body: dict[str, Any] = {"parts": parts}
        if model is not None:
            body["model"] = model.to_message_payload()
        if agent is not None:
            body["agent"] = agent
        if tools is not None:
            body["tools"] = dict(tools)
        if system is not None:
            body["system"] = system

        payload = await self._call(
            "POST",
            f"/session/{session_id}/message",
            json_body=body,
            params={"directory": directory},
            timeout=httpx.Timeout(
                self._timeout,
                connect=10.0,
                read=max(self._timeout, TURN_READ_TIMEOUT_SECONDS),
            ),
        )
        if not isinstance(payload, dict):
            raise OpenCodeError(f"Unexpected message payload: {type(payload).__name__}")

        info = payload.get("info")
        info = info if isinstance(info, dict) else {}
        raw_parts = payload.get("parts")
        parts = [part for part in raw_parts if isinstance(part, dict)] if isinstance(
            raw_parts, list
        ) else []
        finish = info.get("finish")
        error = info.get("error")

        return OpenCodeReply(
            text=_reply_text(parts),
            finish=finish if isinstance(finish, str) else None,
            error=error if isinstance(error, dict) else None,
            parts=parts,
            info=info,
            raw=payload,
        )

    async def stream_bus_events(
        self,
        *,
        session_id: str | None = None,
        directory: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield events from the global bus, optionally filtered to one session.

        `GET /event`. See the module docstring for the live probe that fixed the
        shapes below; the short version:

        * `directory` is **not** optional in practice. Without it the stream
          connects, returns 200, and then only ever emits `server.connected` and
          `server.heartbeat` -- no session traffic at all. Pass the same absolute
          workspace directory the session was created with.
        * `GET /session/{id}/event` does not exist; it returns the web UI.
        * The bus is shared across sessions, hence `session_id`.

        Server lifecycle frames (`server.connected`, `server.heartbeat`) carry no
        session id and are yielded even when `session_id` is set: they are not
        *about* any session, and swallowing `server.connected` would discard the
        one frame that proves the subscription is live before the prompt is sent.

        Long-lived, so the read timeout is disabled for this request only -- the
        stream is legitimately idle while the model thinks.
        """

        client = self._ensure_client()
        url = "/event"
        params = self._directory_params(directory)
        stream_timeout = httpx.Timeout(self._timeout, connect=10.0, read=None)
        try:
            async with client.stream(
                "GET", url, params=params, timeout=stream_timeout
            ) as response:
                if not response.is_success:
                    await response.aread()
                    self._raise_for_status(response)
                content_type = response.headers.get("content-type", "")
                if "event-stream" not in content_type.lower():
                    # The SPA shell answers unknown paths with HTTP 200 text/html.
                    await response.aread()
                    raise OpenCodeError(
                        f"GET {self._base_url}{url} returned content-type "
                        f"{content_type!r} instead of an event stream"
                    )
                data_lines: list[str] = []
                async for line in response.aiter_lines():
                    # Comment/keep-alive frame.
                    if line.startswith(":"):
                        continue
                    if line == "":
                        if not data_lines:
                            continue
                        raw = "\n".join(data_lines)
                        data_lines = []
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            logger.error(
                                "Discarding undecodable SSE frame from the OpenCode bus: %r",
                                raw[:200],
                                exc_info=True,
                            )
                            continue
                        if not isinstance(event, dict):
                            continue
                        if session_id is not None:
                            event_session = bus_event_session_id(event)
                            if event_session is not None and event_session != session_id:
                                continue
                        yield event
                        continue
                    field_name, _, value = line.partition(":")
                    if field_name != "data":
                        # There is no SSE `event:` name on this stream; `id` and
                        # `retry` carry nothing we need.
                        continue
                    # Per the SSE spec, strip exactly one leading space.
                    data_lines.append(value.removeprefix(" "))
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            logger.error("OpenCode event bus unreachable at %s", self._base_url, exc_info=True)
            raise OpenCodeUnavailable(
                f"Cannot reach the OpenCode event bus at {self._base_url}{url}: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            logger.error("OpenCode event bus stream failed", exc_info=True)
            raise OpenCodeError(
                f"The OpenCode event bus at {self._base_url}{url} failed: {exc}"
            ) from exc

    async def interrupt(self, session_id: str, *, directory: str | None = None) -> None:
        """`POST /session/{id}/abort` -> `true`. Also returns `true` when idle.

        The v1 abort, deliberately: the turn being aborted was started by
        :meth:`send_message` on the v1 family, and the v2
        `POST /api/session/{id}/interrupt` (204) was never confirmed to reach a
        v1 turn. `/session/{id}/interrupt` does not exist and returns the web UI.
        """
        await self._call(
            "POST",
            f"/session/{session_id}/abort",
            json_body={},
            params=self._directory_params(directory),
        )

    async def messages(
        self, session_id: str, *, directory: str | None = None
    ) -> list[dict[str, Any]]:
        """`GET /session/{id}/message` -> `[{"info": ..., "parts": [...]}, ...]`.

        v1, not v2: `GET /api/session/{id}/message` returned an empty list for a
        session created and prompted through v1 on 1.18.7.
        """
        path = f"/session/{session_id}/message"
        payload = await self._call("GET", path, params=self._directory_params(directory))
        return self._as_list(payload, path)

    async def message(
        self, session_id: str, message_id: str, *, directory: str | None = None
    ) -> dict[str, Any]:
        """`GET /session/{id}/message/{messageID}` -> `{"info": ..., "parts": [...]}`."""
        path = f"/session/{session_id}/message/{message_id}"
        payload = await self._call("GET", path, params=self._directory_params(directory))
        if not isinstance(payload, dict):
            raise OpenCodeError(f"Unexpected message payload from {path}")
        return payload

    async def list_models(self, *, directory: str | None = None) -> list[dict[str, Any]]:
        """`GET /api/model`. Entries carry `id` and `providerID` for :class:`ModelRef`.

        Returns an empty list for a second or two after server startup while
        providers are still loading -- that is not an error condition.
        """
        return await self._list("/api/model", directory)

    async def list_providers(self, *, directory: str | None = None) -> list[dict[str, Any]]:
        """`GET /api/provider`. Never log the result: entries can carry credentials."""
        return await self._list("/api/provider", directory)

    async def list_agents(self, *, directory: str | None = None) -> list[dict[str, Any]]:
        """`GET /api/agent`."""
        return await self._list("/api/agent", directory)

    async def list_skills(self, *, directory: str | None = None) -> list[dict[str, Any]]:
        """`GET /api/skill`."""
        return await self._list("/api/skill", directory)

    async def _list(self, path: str, directory: str | None) -> list[dict[str, Any]]:
        payload = await self._call("GET", path, params=self._directory_params(directory))
        return self._as_list(payload, path)


__all__ = [
    "BUS_EVENT_PART_DELTA",
    "BUS_EVENT_PART_UPDATED",
    "BUS_EVENT_SESSION_IDLE",
    "BUS_PART_REASONING",
    "BUS_PART_STEP_FINISH",
    "BUS_PART_TEXT",
    "BUS_PART_TOOL",
    "DEFAULT_BASE_URL",
    "DEFAULT_SERVER_USERNAME",
    "TURN_READ_TIMEOUT_SECONDS",
    "BusStepUsage",
    "ModelRef",
    "OpenCodeClient",
    "OpenCodeError",
    "OpenCodeReply",
    "OpenCodeSession",
    "OpenCodeUnavailable",
    "bus_event_delta_part_id",
    "bus_event_is_idle",
    "bus_event_part_kind",
    "bus_event_session_id",
    "bus_event_step_usage",
    "bus_event_text_delta",
    "bus_event_tool_input",
    "bus_event_tool_label",
    "bus_event_tool_name",
    "bus_event_tool_status",
]
