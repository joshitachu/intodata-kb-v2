"""A restart must not cost a labs turn its evidence or its conversation history.

Both defects these cover were measured on live conversations, and both trace to
the same cause: the backend runs `uvicorn --reload --reload-dir /app/src` and
reloaded 39 times in one working session, wiping every in-process structure each
time. A production deploy does exactly the same thing, only less often.

* **The evidence trail vanished.** Two turns logged `tool_calls=0` while the
  OpenCode event bus had seen four and five `nexus_*` calls: no `tool.completed`
  frames, nothing in the trace explorer, no `evidence_refs` -- and the answer
  still read as fully grounded. The ledger was keyed by the MCP bearer token,
  which is a credential with its own lifecycle and is deliberately re-mintable,
  so a routine rotation orphaned every record written under the previous value.
  It is now keyed by conversation, which is what the records belong to and the
  one identifier both sides always agree on.

* **The conversation was split in two.** Losing the in-process
  conversation -> session map made the next turn open a SECOND top-level
  OpenCode session for the same conversation. Verified on two live
  conversations, each left holding two sessions with `parentID=None`: the model
  silently lost everything said before the restart while the Nexus-side message
  list still looked complete. The session id is now persisted on the
  conversation row; the token still is not, because re-minting a credential is
  the correct handling for one.
"""

from __future__ import annotations

import collections
import logging
from dataclasses import dataclass
from typing import Any

import pytest

from src.labs import mcp_bridge, runtime as labs_runtime
from src.labs.contracts import (
    LabsTokenRegistry,
    LabsToolCallRecord,
    LabsTurnBudget,
    LabsTurnScope,
)

CONVERSATION_ID = 639
OTHER_CONVERSATION_ID = 640
WORKSPACE = "/tmp/labs-session-binding"


def _scope(conversation_id: int = CONVERSATION_ID) -> LabsTurnScope:
    return LabsTurnScope(
        user_id=7,
        conversation_id=conversation_id,
        user_groups=["developer"],
        project_scope=["acme"],
        permissions=frozenset({"chat:use"}),
        access_token=None,
        workspace_dir=WORKSPACE,
    )


def _record(capability_id: str) -> LabsToolCallRecord:
    return LabsToolCallRecord(
        capability_id=capability_id,
        tool_name=capability_id.rsplit(".", 1)[-1],
        succeeded=True,
    )


@pytest.fixture(autouse=True)
def _isolated_ledger(monkeypatch):
    """A fresh ledger and budget per test; both are module singletons."""

    monkeypatch.setattr(mcp_bridge, "_tool_calls", {})
    monkeypatch.setattr(mcp_bridge, "labs_turn_budget", LabsTurnBudget())
    monkeypatch.setattr(labs_runtime, "_CONVERSATION_BINDINGS", {})


# --- The ledger is keyed by the conversation, not by the credential ---------


def test_the_ledger_survives_a_token_remint_for_the_same_conversation():
    """The measured defect: `tool_calls=0` against four calls on the bus.

    A re-mint is routine -- `_resolve_binding` does it whenever it cannot reuse
    the token it holds -- and OpenCode may still be presenting the previous
    value it cached out of `opencode.json`. Both tokens resolve to the same
    conversation, so both halves of the turn land in one ledger entry.
    """

    registry = LabsTokenRegistry(secret="test-secret")
    before = registry.mint(_scope())
    after = registry.mint(_scope())
    assert before != after

    for token in (before, after):
        scope = registry.resolve(token)
        assert scope is not None
        mcp_bridge._record_tool_call(scope.conversation_id, _record("rag.docs.read"))

    drained = mcp_bridge.drain_tool_calls(CONVERSATION_ID)

    assert len(drained) == 2


def test_draining_one_conversation_leaves_another_conversations_records():
    mcp_bridge._record_tool_call(CONVERSATION_ID, _record("rag.docs.read"))
    mcp_bridge._record_tool_call(OTHER_CONVERSATION_ID, _record("rag.docs.search"))

    drained = mcp_bridge.drain_tool_calls(CONVERSATION_ID)

    assert [record.capability_id for record in drained] == ["rag.docs.read"]
    assert len(mcp_bridge._tool_calls[OTHER_CONVERSATION_ID]) == 1


def test_a_second_drain_of_the_same_conversation_is_empty():
    mcp_bridge._record_tool_call(CONVERSATION_ID, _record("rag.docs.read"))

    assert len(mcp_bridge.drain_tool_calls(CONVERSATION_ID)) == 1
    assert mcp_bridge.drain_tool_calls(CONVERSATION_ID) == []


def test_the_ledger_evicts_the_oldest_conversation_once_it_is_full():
    """A turn that dies before draining leaves its entry behind; memory is bounded."""

    for conversation_id in range(mcp_bridge._MAX_TRACKED_CONVERSATIONS):
        mcp_bridge._record_tool_call(conversation_id, _record("rag.docs.read"))
    assert len(mcp_bridge._tool_calls) == mcp_bridge._MAX_TRACKED_CONVERSATIONS

    mcp_bridge._record_tool_call(999_999, _record("rag.docs.read"))

    assert len(mcp_bridge._tool_calls) == mcp_bridge._MAX_TRACKED_CONVERSATIONS
    assert 0 not in mcp_bridge._tool_calls
    assert 999_999 in mcp_bridge._tool_calls


def test_one_conversation_cannot_grow_its_own_entry_without_limit():
    overflow = mcp_bridge._MAX_RECORDS_PER_CONVERSATION + 5
    for index in range(overflow):
        mcp_bridge._record_tool_call(CONVERSATION_ID, _record(f"rag.docs.read{index}"))

    drained = mcp_bridge.drain_tool_calls(CONVERSATION_ID)

    assert len(drained) == mcp_bridge._MAX_RECORDS_PER_CONVERSATION
    # The newest records are the ones kept: they are the ones a turn is still
    # about to report.
    assert drained[-1].capability_id == f"rag.docs.read{overflow - 1}"


# --- The OpenCode session outlives the process ------------------------------


class _FakeConversation:
    """Stands in for the `Conversation` ORM row the route passes in."""

    def __init__(self, conversation_id: int, session_id: str | None = None) -> None:
        self.id = conversation_id
        self.opencode_session_id = session_id


async def test_a_lost_in_process_binding_reuses_the_persisted_session(monkeypatch):
    """The restart path: re-mint the credential, keep the conversation."""

    monkeypatch.setattr(
        labs_runtime, "labs_token_registry", LabsTokenRegistry(secret="test-secret")
    )

    token, session_id = await labs_runtime._resolve_binding(
        conversation_id=CONVERSATION_ID,
        scope_factory=lambda workspace_dir: _scope(),
        persisted_session_id="ses_before_the_restart",
    )

    assert session_id == "ses_before_the_restart"
    assert token
    # And it is cached again, so the rest of the turn reads it without a second
    # database round trip.
    assert labs_runtime._CONVERSATION_BINDINGS[CONVERSATION_ID] == (
        token,
        "ses_before_the_restart",
    )


async def test_a_conversation_with_no_persisted_session_still_opens_one(monkeypatch):
    monkeypatch.setattr(
        labs_runtime, "labs_token_registry", LabsTokenRegistry(secret="test-secret")
    )

    token, session_id = await labs_runtime._resolve_binding(
        conversation_id=CONVERSATION_ID,
        scope_factory=lambda workspace_dir: _scope(),
        persisted_session_id=None,
    )

    assert token
    assert session_id is None
    assert CONVERSATION_ID not in labs_runtime._CONVERSATION_BINDINGS


async def test_the_in_process_binding_still_wins_when_it_is_present(monkeypatch):
    """The persisted id is a fallback, not an override.

    Within one process the cached pair is current by construction, and going to
    the row first would only add a way for a stale column to displace it.
    """

    monkeypatch.setattr(
        labs_runtime, "labs_token_registry", LabsTokenRegistry(secret="test-secret")
    )
    labs_runtime._CONVERSATION_BINDINGS[CONVERSATION_ID] = ("live-token", "ses_live")

    token, session_id = await labs_runtime._resolve_binding(
        conversation_id=CONVERSATION_ID,
        scope_factory=lambda workspace_dir: _scope(),
        persisted_session_id="ses_stale",
    )

    assert session_id == "ses_live"
    del token


@pytest.mark.parametrize("stored", [None, "", "   "])
def test_a_blank_stored_session_reads_as_no_session(stored):
    assert labs_runtime._persisted_session_id(_FakeConversation(CONVERSATION_ID, stored)) is None


def test_a_stub_conversation_without_the_column_reads_as_no_session():
    assert labs_runtime._persisted_session_id(object()) is None


def test_a_new_session_id_is_staged_on_the_conversation_row():
    conversation = _FakeConversation(CONVERSATION_ID)

    labs_runtime._remember_session_id(conversation, "ses_new")

    assert conversation.opencode_session_id == "ses_new"


def test_recording_the_same_session_twice_is_a_no_op():
    conversation = _FakeConversation(CONVERSATION_ID, "ses_new")

    labs_runtime._remember_session_id(conversation, "ses_new")

    assert conversation.opencode_session_id == "ses_new"


def test_a_conversation_that_cannot_hold_the_session_does_not_break_the_turn(caplog):
    """Bookkeeping failing is worth an error line, not a dead turn."""

    @dataclass(frozen=True)
    class _Frozen:
        id: int = CONVERSATION_ID
        opencode_session_id: str | None = None

    with caplog.at_level(logging.ERROR, logger="src.labs.runtime"):
        labs_runtime._remember_session_id(_Frozen(), "ses_new")

    assert "could not be recorded on conversation" in caplog.text


# --- A turn end to end: one session, persisted, reused ----------------------


@dataclass(frozen=True)
class _FakeSession:
    id: str
    directory: str


class _FakeOpenCodeClient:
    """The minimum of `OpenCodeClient` that one labs turn touches.

    Class-level counters rather than instance ones because `execute_labs_turn`
    constructs its own client; the assertions are about how many sessions the
    LANE opened, which is the defect under test.
    """

    created_sessions: list[str] = []
    prompts: list[tuple[str, str]] = []
    _next_session = 0

    async def __aenter__(self) -> _FakeOpenCodeClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def create_session(self, *, directory: str, agent: str) -> _FakeSession:
        del agent
        type(self)._next_session += 1
        session_id = f"ses_fake_{type(self)._next_session}"
        type(self).created_sessions.append(session_id)
        return _FakeSession(id=session_id, directory=directory)

    async def stream_bus_events(self, *, session_id: str, directory: str):
        del directory
        # One capability call and one OpenCode-native call, then the server's
        # end-of-turn signal.
        for tool in ("nexus_read_document", "read"):
            yield {
                "type": "message.part.updated",
                "properties": {
                    "sessionID": session_id,
                    "part": {
                        "id": f"prt_{tool}",
                        "type": "tool",
                        "tool": tool,
                        "callID": f"call_{tool}",
                        "state": {"status": "running"},
                    },
                },
            }
        yield {"type": "session.idle", "properties": {"sessionID": session_id}}

    async def send_message(self, session_id: str, message: str, **_kwargs: Any):
        from src.labs.opencode_client import OpenCodeReply

        type(self).prompts.append((session_id, message))
        return OpenCodeReply(text="An answer.", finish="stop", error=None)


@pytest.fixture
def _turn_harness(monkeypatch):
    """Stub everything `execute_labs_turn` reaches outside the two files under test."""

    _FakeOpenCodeClient.created_sessions = []
    _FakeOpenCodeClient.prompts = []
    _FakeOpenCodeClient._next_session = 0

    async def _no_guidance(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _no_material(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _no_texts(*_args: Any, **_kwargs: Any) -> dict[int, str]:
        return {}

    async def _workspace(**_kwargs: Any) -> str:
        return WORKSPACE

    monkeypatch.setattr(
        labs_runtime, "labs_token_registry", LabsTokenRegistry(secret="test-secret")
    )
    monkeypatch.setattr(labs_runtime, "OpenCodeClient", _FakeOpenCodeClient)
    monkeypatch.setattr(labs_runtime, "compile_guidance", _no_guidance)
    monkeypatch.setattr(labs_runtime, "compile_project_material", _no_material)
    monkeypatch.setattr(labs_runtime, "load_material_texts", _no_texts)
    monkeypatch.setattr(labs_runtime, "ensure_labs_workspace", _workspace)
    return _FakeOpenCodeClient


async def _run_turn(conversation: _FakeConversation) -> Any:
    async def _emit(_event_type: str, _payload: dict[str, Any]) -> None:
        return None

    return await labs_runtime.execute_labs_turn(
        db=None,
        conversation=conversation,
        user_id=7,
        user_groups=["developer"],
        project_scope=["acme"],
        permissions=frozenset({"chat:use"}),
        access_token=None,
        message="Which jobs write to the invoice queue?",
        emit=_emit,
    )


async def test_a_turn_persists_the_session_it_opened(_turn_harness):
    conversation = _FakeConversation(CONVERSATION_ID)

    result = await _run_turn(conversation)

    assert _turn_harness.created_sessions == ["ses_fake_1"]
    assert conversation.opencode_session_id == "ses_fake_1"
    assert result.opencode_session_id == "ses_fake_1"


async def test_the_next_turn_after_a_restart_reuses_that_session(_turn_harness):
    """The split-conversation defect, end to end.

    The second turn runs with an empty in-process binding -- which is exactly
    what a restart leaves behind -- and must prompt the session the first turn
    opened instead of opening a second top-level one.
    """

    conversation = _FakeConversation(CONVERSATION_ID)
    await _run_turn(conversation)

    labs_runtime._CONVERSATION_BINDINGS.clear()
    await _run_turn(conversation)

    assert _turn_harness.created_sessions == ["ses_fake_1"]
    assert [session for session, _ in _turn_harness.prompts] == [
        "ses_fake_1",
        "ses_fake_1",
    ]


async def test_the_bearer_token_is_never_written_to_the_conversation(_turn_harness):
    """A credential belongs in memory and in `opencode.json`, not in the database."""

    conversation = _FakeConversation(CONVERSATION_ID)
    await _run_turn(conversation)

    token, _session = labs_runtime._CONVERSATION_BINDINGS[CONVERSATION_ID]
    assert token
    assert token not in vars(conversation).values()


# --- Losing the evidence is reported, not swallowed -------------------------


def _counts(**tools: int) -> collections.Counter[str]:
    return collections.Counter(tools)


def test_the_warning_fires_when_the_bus_saw_capability_calls_and_the_ledger_did_not(
    caplog,
):
    """Exactly the two measured turns: `cap=0` against `total=4` and `total=5`."""

    with caplog.at_level(logging.WARNING, logger="src.labs.runtime"):
        labs_runtime._warn_on_lost_capability_records(
            conversation_id=CONVERSATION_ID,
            drained=[],
            tool_counts=_counts(
                nexus_read_document=2,
                nexus_search_documentation=1,
                nexus_search_within_document=1,
            ),
        )

    assert "labs.evidence_lost" in caplog.text
    assert f"conversation_id={CONVERSATION_ID}" in caplog.text
    assert "capability_records=0" in caplog.text
    assert "bus_capability_calls=4" in caplog.text
    assert "nexus_read_document=2" in caplog.text


def test_no_warning_when_the_turn_ran_no_capability_tools_at_all(caplog):
    """A turn answered from the mounted workspace is not a defect."""

    with caplog.at_level(logging.WARNING, logger="src.labs.runtime"):
        labs_runtime._warn_on_lost_capability_records(
            conversation_id=CONVERSATION_ID,
            drained=[],
            tool_counts=_counts(read=8, grep=3, todowrite=1),
        )

    assert "labs.evidence_lost" not in caplog.text


def test_no_warning_when_the_ledger_produced_records(caplog):
    with caplog.at_level(logging.WARNING, logger="src.labs.runtime"):
        labs_runtime._warn_on_lost_capability_records(
            conversation_id=CONVERSATION_ID,
            drained=[_record("rag.docs.read_document")],
            tool_counts=_counts(nexus_read_document=2),
        )

    assert "labs.evidence_lost" not in caplog.text


async def test_a_real_turn_reports_its_own_lost_evidence(_turn_harness, caplog):
    """The check is wired into the turn, not merely available to it.

    The fake bus runs one `nexus_*` tool and no MCP bridge is involved, so the
    ledger is empty -- the same shape as the measured turns, and the turn has to
    say so rather than log `tool_calls=0` and move on.
    """

    with caplog.at_level(logging.WARNING, logger="src.labs.runtime"):
        await _run_turn(_FakeConversation(CONVERSATION_ID))

    assert "labs.evidence_lost" in caplog.text
    assert "bus_capability_calls=1" in caplog.text


def test_only_nexus_tools_count_towards_the_expected_capability_calls():
    assert (
        labs_runtime._nexus_bus_tool_calls(
            _counts(nexus_read_document=2, read=8, grep=3)
        )
        == 2
    )
