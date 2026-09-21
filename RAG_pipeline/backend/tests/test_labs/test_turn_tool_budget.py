"""The labs tool allowance is per TURN, and the model is told what is left.

The defect these cover, measured on a live thread: the allowance was counted
against the MCP bearer, which is minted once per conversation with an 8-hour
TTL, so 40 calls were spent across every turn the conversation ever made. One
observed conversation reached 38 of 40 across seven turns in three minutes,
after which every further turn in it was crippled -- and neither the user nor
the model was told why.

Two properties carry the fix:

* The count resets at the turn boundary, which is `labs_turn_claims.bind` /
  `.release` -- the pair `src/labs/runtime.py::labs_turn_claim` wraps one turn
  in. Driving it from there means there is one definition of "a turn starts",
  not two that can drift.
* Every tool result states the remaining allowance, so the model can pace
  itself instead of discovering the limit by being denied.
"""

from __future__ import annotations

import json

import pytest

from src.labs import mcp_bridge
from src.labs.contracts import (
    LabsToolCallRecord,
    LabsTurnBudget,
    LabsTurnClaim,
    LabsTurnClaimRegistry,
    LabsTurnScope,
)

CONVERSATION_ID = 4242


def _scope(allowance: int = 40) -> LabsTurnScope:
    return LabsTurnScope(
        user_id=7,
        conversation_id=CONVERSATION_ID,
        user_groups=["developer"],
        project_scope=["acme"],
        permissions=frozenset({"chat:use"}),
        access_token=None,
        workspace_dir="/tmp/labs-budget",
        remaining_tool_calls=allowance,
    )


def _claim(claim_id: int = 1) -> LabsTurnClaim:
    return LabsTurnClaim(
        message_id=claim_id * 10,
        state_version=claim_id,
        claim_id=claim_id,
        claim_token=f"claim-{claim_id}",
    )


@pytest.fixture(autouse=True)
def _isolated_budget(monkeypatch):
    """A fresh budget per test; the production one is a module singleton."""

    budget = LabsTurnBudget()
    monkeypatch.setattr(mcp_bridge, "labs_turn_budget", budget)
    monkeypatch.setattr(mcp_bridge, "_tool_calls", {})
    return budget


def _record(budget, count: int) -> None:
    for index in range(count):
        mcp_bridge._record_tool_call(
            CONVERSATION_ID,
            LabsToolCallRecord(capability_id=f"cap.{index}", succeeded=True),
        )


def test_claim_bind_starts_a_fresh_allowance():
    budget = LabsTurnBudget()
    claims = LabsTurnClaimRegistry(budget=budget)

    claims.bind(CONVERSATION_ID, _claim(1))
    budget.consume(CONVERSATION_ID)
    budget.consume(CONVERSATION_ID)
    assert budget.used(CONVERSATION_ID) == 2

    # Next turn: the previous one released, and the new one binds.
    claims.release(CONVERSATION_ID)
    claims.bind(CONVERSATION_ID, _claim(2))

    assert budget.used(CONVERSATION_ID) == 0
    assert budget.remaining(CONVERSATION_ID, 40) == 40


def test_a_turn_that_lost_the_claim_race_still_starts_from_zero():
    """`labs_turn_claim` binds nothing when acquisition loses; the turn runs anyway.

    The previous turn's `release` already dropped the entry, so the unlocked
    turn still gets a full allowance rather than inheriting a spent one.
    """

    budget = LabsTurnBudget()
    claims = LabsTurnClaimRegistry(budget=budget)

    claims.bind(CONVERSATION_ID, _claim(1))
    for _ in range(38):
        budget.consume(CONVERSATION_ID)
    claims.release(CONVERSATION_ID)

    # No bind this time -- the claim was not acquired.
    assert budget.remaining(CONVERSATION_ID, 40) == 40


async def test_two_consecutive_turns_that_both_lose_the_claim_do_not_share_a_count(
    monkeypatch,
):
    """The hole `bind`-driven resetting left open.

    A turn that loses the claim race binds nothing, so driving the reset from
    `bind` meant it reset nothing either. One losing turn was fine -- the
    previous turn's `release` had dropped the entry -- but two in a row shared a
    single count, and the second started with the first's calls already spent.
    Whether a turn holds the conversation lock has nothing to do with how many
    tool calls it may make, so the reset is now unconditional and sits at the
    entry to `labs_turn_claim`.
    """

    from src.labs import runtime as labs_runtime

    budget = LabsTurnBudget()
    monkeypatch.setattr(labs_runtime, "labs_turn_budget", budget)

    async def _lose_the_race(**_kwargs):
        return None

    monkeypatch.setattr(labs_runtime, "_acquire_labs_claim", _lose_the_race)

    for turn in (1, 2):
        async with labs_runtime.labs_turn_claim(
            db=None,  # never touched: the claim is never acquired
            conversation_id=CONVERSATION_ID,
            user_id=7,
            message_id=turn,
        ) as guard:
            assert guard.claim is None
            assert budget.remaining(CONVERSATION_ID, 40) == 40
            for _ in range(30):
                budget.consume(CONVERSATION_ID)
            assert budget.remaining(CONVERSATION_ID, 40) == 10


async def test_the_allowance_is_released_even_when_no_claim_was_bound(monkeypatch):
    """Otherwise the unconditional `start_turn` would leak an entry per turn."""

    from src.labs import runtime as labs_runtime

    budget = LabsTurnBudget()
    monkeypatch.setattr(labs_runtime, "labs_turn_budget", budget)

    async def _lose_the_race(**_kwargs):
        return None

    monkeypatch.setattr(labs_runtime, "_acquire_labs_claim", _lose_the_race)

    async with labs_runtime.labs_turn_claim(
        db=None,
        conversation_id=CONVERSATION_ID,
        user_id=7,
        message_id=1,
    ):
        budget.consume(CONVERSATION_ID)

    assert budget.used(CONVERSATION_ID) == 0


def test_recording_tool_calls_consumes_the_turn_budget(_isolated_budget):
    _record(_isolated_budget, 3)

    assert _isolated_budget.used(CONVERSATION_ID) == 3
    assert mcp_bridge._remaining_tool_calls(_scope()) == 37


def test_draining_the_evidence_ledger_does_not_refund_the_budget(_isolated_budget):
    """A mid-turn drain for incremental grounding must not widen the allowance."""

    _record(_isolated_budget, 5)
    drained = mcp_bridge.drain_tool_calls(CONVERSATION_ID)

    assert len(drained) == 5
    assert _isolated_budget.used(CONVERSATION_ID) == 5
    assert mcp_bridge._remaining_tool_calls(_scope()) == 35


def test_budget_note_states_the_remaining_allowance(_isolated_budget):
    note = mcp_bridge._budget_note(_scope())

    assert note["remaining_tool_calls_this_turn"] == 40
    assert note["tool_calls_per_turn"] == 40
    # No nagging while there is plenty left.
    assert "_budget_note" not in note


def test_budget_note_warns_as_the_allowance_runs_low(_isolated_budget):
    _record(_isolated_budget, 36)

    note = mcp_bridge._budget_note(_scope())

    assert note["remaining_tool_calls_this_turn"] == 4
    assert "4 tool call(s) remain" in note["_budget_note"]


def test_budget_note_is_explicit_once_the_allowance_is_spent(_isolated_budget):
    _record(_isolated_budget, 40)

    note = mcp_bridge._budget_note(_scope())

    assert note["remaining_tool_calls_this_turn"] == 0
    assert "allowance is spent" in note["_budget_note"]


def test_remaining_never_goes_negative(_isolated_budget):
    _record(_isolated_budget, 55)

    assert mcp_bridge._remaining_tool_calls(_scope()) == 0


def test_execution_context_carries_the_per_turn_remainder(_isolated_budget):
    _record(_isolated_budget, 6)

    context = mcp_bridge._execution_context(_scope(), {"rag.docs.read_document"})

    assert context.remaining_tool_calls == 34


def test_tool_result_payload_is_still_valid_json_with_the_budget_merged(
    _isolated_budget,
):
    """The note rides inside the same JSON text block every other field does."""

    payload = {"capability_id": "rag.docs.read_document", "status": "succeeded"}
    result = mcp_bridge._tool_result(
        {**payload, **mcp_bridge._budget_note(_scope())}, is_error=False
    )

    decoded = json.loads(result["content"][0]["text"])
    assert decoded["capability_id"] == "rag.docs.read_document"
    assert decoded["remaining_tool_calls_this_turn"] == 40
    assert result["structuredContent"] == decoded
