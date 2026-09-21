"""What a labs turn cost, and where that number comes from.

Thirty-eight V2 turns ran before this existed and left no trace of their spend
anywhere but the OpenCode process's own memory::

    select count(*) from query_usage q
      join conversations c on c.id = q.conversation_id
     where c.title like '[labs]%';
    -- 0

while the production lane wrote a `QueryUsage` row per turn. No cost trend, no
per-project attribution, no way to notice a regression.

Two properties of OpenCode's numbers decide how the row is built, and both were
established by replaying live sessions off `GET /session/{id}/message` rather
than assumed:

* **Usage is per model step, never cumulative.** One live session holds two
  turns; the second turn's message cost $0.00114675 against the first turn's
  $0.014148 total. Nothing is diffed against a running total, because diffing
  would subtract one real turn's cost from the next one's.
* **The reply is one step of the turn.** `send_message` returns the LAST
  assistant message only, so pricing a turn off it charged for a fraction of it
  -- 37% and 69% on the two live conversations measured. The whole turn is
  visible on the event bus, which the turn already subscribes to, as one
  `step-finish` part per step.

The numbers in `_LIVE_STEPS` are those real steps, kept verbatim so a change to
the folding rules shows up as a wrong dollar figure rather than a wrong shape.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, ClassVar, Self

import pytest
from sqlalchemy.exc import SQLAlchemyError

from src.db.models.query_usage import QueryUsage
from src.labs import mcp_bridge
from src.labs import runtime as labs_runtime
from src.labs.contracts import LabsTokenRegistry, LabsTurnBudget
from src.labs.opencode_client import OpenCodeError, OpenCodeReply
from src.labs.runtime import (
    LABS_USAGE_INTERFACE,
    LABS_USAGE_MODEL_NAME,
    _BusStream,
    _turn_usage,
)
from src.labs.workspace import _PROVIDER_ID as LABS_PROVIDER_ID

CONVERSATION_ID = 771
USER_ID = 7
PROJECT_ID = "acme"
WORKSPACE = "/tmp/labs-usage-recording"
SESSION = "ses_usage"

#: The five steps of one live labs turn (conversation 645), verbatim.
#:
#: `input` is disjoint from `cache.read` and `output` is disjoint from
#: `reasoning` -- `total` equals the sum of all five fields on every one of them,
#: which is what makes the folding in `_turn_usage` necessary.
_LIVE_STEPS: list[tuple[float, dict[str, Any]]] = [
    (0.001504176, {"total": 11286, "input": 9328, "output": 115, "reasoning": 51,
                   "cache": {"read": 1792, "write": 0}}),
    (0.000464292, {"total": 13429, "input": 1930, "output": 84, "reasoning": 151,
                   "cache": {"read": 11264, "write": 0}}),
    (0.000570936, {"total": 15394, "input": 1596, "output": 147, "reasoning": 339,
                   "cache": {"read": 13312, "write": 0}}),
    (0.001033230, {"total": 19370, "input": 3153, "output": 165, "reasoning": 692,
                   "cache": {"read": 15360, "write": 0}}),
    (0.002141184, {"total": 26946, "input": 5528, "output": 1586, "reasoning": 504,
                   "cache": {"read": 19328, "write": 0}}),
]

#: What that turn really cost. The reply alone would have reported the last
#: step's $0.002141184 -- 37% of it.
_LIVE_TURN_COST = 0.005713818


def _step_finish(
    part_id: str,
    *,
    cost: float | None = None,
    tokens: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One `message.part.updated` frame for a `step-finish` part."""

    part: dict[str, Any] = {"id": part_id, "type": "step-finish", "sessionID": SESSION}
    if cost is not None:
        part["cost"] = cost
    if tokens is not None:
        part["tokens"] = tokens
    return {
        "type": "message.part.updated",
        "properties": {"sessionID": SESSION, "part": part},
    }


# --- Folding OpenCode's shape into the table's shape ------------------------


def test_the_cached_prefix_is_counted_as_input_not_dropped() -> None:
    """The production lane's invariant: `input_tokens` is the WHOLE prompt.

    OpenCode reports the uncached remainder. A warm DeepSeek session serves
    almost the entire prompt from cache, so reading `tokens.input` as "the
    prompt" would report 246 tokens for a prompt of 11126 -- and `query_usage`
    would say this lane runs on a fraction of the context it actually pays for.
    """

    usage = _turn_usage(
        [(0.0002, {"input": 246, "output": 72, "reasoning": 157,
                   "cache": {"read": 10880, "write": 0}})]
    )

    assert usage.input_tokens == 246 + 10880
    assert usage.cached_input_tokens == 10880
    assert usage.cache_write_tokens == 0


def test_reasoning_is_counted_as_output_because_that_is_how_it_is_billed() -> None:
    """OpenCode counts reasoning beside output; every pricing table counts it as
    output. Dropping it would understate this step by 1808 of 7856 tokens."""

    usage = _turn_usage(
        [(0.0097, {"input": 33266, "output": 6048, "reasoning": 1808,
                   "cache": {"read": 23040, "write": 0}})]
    )

    assert usage.output_tokens == 6048 + 1808
    assert usage.input_tokens == 33266 + 23040


def test_cache_writes_are_prompt_tokens_too() -> None:
    usage = _turn_usage(
        [(0.01, {"input": 100, "output": 10, "reasoning": 0,
                 "cache": {"read": 20, "write": 30}})]
    )

    assert usage.input_tokens == 150
    assert usage.cache_write_tokens == 30


def test_a_multi_step_turn_is_the_sum_of_its_steps() -> None:
    """The measured turn, end to end. Pricing it off the reply reported 37%."""

    usage = _turn_usage(list(_LIVE_STEPS))

    assert usage.steps == 5
    assert usage.cost_usd == pytest.approx(_LIVE_TURN_COST)
    assert usage.input_tokens == sum(
        tokens["input"] + tokens["cache"]["read"] + tokens["cache"]["write"]
        for _cost, tokens in _LIVE_STEPS
    )
    assert usage.output_tokens == sum(
        tokens["output"] + tokens["reasoning"] for _cost, tokens in _LIVE_STEPS
    )


def test_nothing_is_diffed_against_a_previous_total() -> None:
    """OpenCode's figures are per step, so a second turn stands on its own.

    Treating them as cumulative -- subtracting what a conversation has already
    recorded -- would have charged this turn a negative amount, because the
    second live turn genuinely cost less than the first.
    """

    first = _turn_usage(list(_LIVE_STEPS))
    second = _turn_usage(
        [(0.00114675, {"total": 65724, "input": 177, "output": 455, "reasoning": 1092,
                       "cache": {"read": 64000, "write": 0}})]
    )

    assert second.cost_usd == pytest.approx(0.00114675)
    assert second.cost_usd < first.cost_usd


@pytest.mark.parametrize(
    "sample",
    [
        (None, None),
        (None, {}),
        (None, {"cache": {}}),
        (None, "not an object"),
    ],
)
def test_a_step_that_reported_nothing_reads_as_unobserved(sample: Any) -> None:
    usage = _turn_usage([sample])

    assert usage.cost_usd is None
    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert not usage.observed


def test_a_missing_cost_leaves_a_null_not_a_zero() -> None:
    """A zero is a claim that the turn was free; a null says nobody reported."""

    usage = _turn_usage(
        [(None, {"input": 10, "output": 5, "reasoning": 0, "cache": {"read": 0, "write": 0}})]
    )

    assert usage.cost_usd is None
    assert usage.input_tokens == 10
    assert usage.output_tokens == 5
    assert usage.observed


def test_missing_tokens_leave_nulls_beside_a_real_cost() -> None:
    usage = _turn_usage([(0.004, None)])

    assert usage.cost_usd == pytest.approx(0.004)
    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert usage.observed


def test_all_zero_counts_do_not_count_as_observed_spend() -> None:
    """OpenCode announces an assistant message with a zeroed usage block before
    the model has produced anything. A row built from that asserts a free turn."""

    usage = _turn_usage(
        [(0.0, {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}})]
    )

    assert not usage.observed


def test_a_nonsense_token_value_is_ignored_rather_than_coerced() -> None:
    usage = _turn_usage([(0.001, {"input": "lots", "output": 5, "reasoning": None,
                                  "cache": {"read": True, "write": 3}})]
                        )

    # `True` is not a token count, and neither is `"lots"`.
    assert usage.input_tokens == 3
    assert usage.output_tokens == 5


# --- The bus is where a whole turn is visible -------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.frames: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.frames.append((event_type, payload))


async def test_the_stream_collects_every_step_of_the_turn() -> None:
    recorder = _Recorder()
    stream = _BusStream(emit=recorder.emit)

    for index, (cost, tokens) in enumerate(_LIVE_STEPS):
        await stream.handle(_step_finish(f"prt_{index}", cost=cost, tokens=tokens))

    usage = _turn_usage([(step.cost, step.tokens) for step in stream.step_usage])
    assert len(stream.step_usage) == 5
    assert usage.cost_usd == pytest.approx(_LIVE_TURN_COST)


async def test_a_repeated_frame_for_one_step_is_not_charged_twice() -> None:
    """The bus re-emits a part on every state change; the turn paid once."""

    recorder = _Recorder()
    stream = _BusStream(emit=recorder.emit)
    frame = _step_finish("prt_one", cost=0.004, tokens={"input": 10, "output": 2})

    await stream.handle(frame)
    await stream.handle(frame)
    await stream.handle(frame)

    usage = _turn_usage([(step.cost, step.tokens) for step in stream.step_usage])
    assert usage.cost_usd == pytest.approx(0.004)
    assert usage.steps == 1


async def test_a_step_finish_frame_never_reaches_the_answer() -> None:
    recorder = _Recorder()
    stream = _BusStream(emit=recorder.emit)

    await stream.handle(_step_finish("prt_one", cost=0.004, tokens={"input": 10}))

    assert stream.text == ""
    assert recorder.frames == []


# --- One turn, one row ------------------------------------------------------


class _FakeSession:
    """Stands in for the `AsyncSession` the route owns.

    It records what was staged and never commits, which is the contract under
    test: `execute_labs_turn` stages, the route commits.
    """

    def __init__(self, *, fail_on_add: bool = False) -> None:
        self.added: list[Any] = []
        self.fail_on_add = fail_on_add
        self.rollbacks = 0
        self.commits = 0

    def add(self, row: Any) -> None:
        if self.fail_on_add:
            raise SQLAlchemyError("the identity map rejected this row")
        self.added.append(row)

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def commit(self) -> None:
        self.commits += 1

    @property
    def usage_rows(self) -> list[QueryUsage]:
        return [row for row in self.added if isinstance(row, QueryUsage)]


@dataclass(frozen=True)
class _FakeOpenCodeSession:
    id: str
    directory: str


class _FakeConversation:
    def __init__(self, conversation_id: int = CONVERSATION_ID) -> None:
        self.id = conversation_id
        self.opencode_session_id: str | None = None


class _FakeOpenCodeClient:
    """One turn's worth of `OpenCodeClient`, scripted per test.

    Class attributes because `execute_labs_turn` constructs its own client.
    """

    steps: ClassVar[list[tuple[float | None, dict[str, Any] | None]]] = []
    reply: ClassVar[OpenCodeReply | None] = None
    raises: ClassVar[Exception | None] = None
    hangs: ClassVar[bool] = False
    emit_idle: ClassVar[bool] = True
    interrupts: ClassVar[int] = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def create_session(self, *, directory: str, agent: str) -> _FakeOpenCodeSession:
        del agent
        return _FakeOpenCodeSession(id=SESSION, directory=directory)

    async def stream_bus_events(self, *, session_id: str, directory: str):
        del directory, session_id
        for index, (cost, tokens) in enumerate(type(self).steps):
            yield _step_finish(f"prt_{index}", cost=cost, tokens=tokens)
        if type(self).emit_idle:
            yield {"type": "session.idle", "properties": {"sessionID": SESSION}}

    async def send_message(self, session_id: str, message: str, **_kwargs: Any):
        del session_id, message
        if type(self).raises is not None:
            raise type(self).raises
        if type(self).hangs:
            await asyncio.sleep(30)
        return type(self).reply

    async def interrupt(self, session_id: str, *, directory: str | None = None) -> None:
        del session_id, directory
        type(self).interrupts += 1


def _reply(
    *,
    text: str = "An answer.",
    finish: str = "stop",
    cost: float | None = None,
    tokens: dict[str, Any] | None = None,
) -> OpenCodeReply:
    info: dict[str, Any] = {"id": "msg_fake", "finish": finish}
    if cost is not None:
        info["cost"] = cost
    if tokens is not None:
        info["tokens"] = tokens
    return OpenCodeReply(text=text, finish=finish, error=None, info=info)


@pytest.fixture(autouse=True)
def _isolated_lane(monkeypatch):
    monkeypatch.setattr(mcp_bridge, "_tool_calls", {})
    monkeypatch.setattr(mcp_bridge, "labs_turn_budget", LabsTurnBudget())
    monkeypatch.setattr(labs_runtime, "_CONVERSATION_BINDINGS", {})


@pytest.fixture
def _turn(monkeypatch):
    """Stub everything the turn reaches outside the two files under test."""

    _FakeOpenCodeClient.steps = []
    _FakeOpenCodeClient.reply = _reply()
    _FakeOpenCodeClient.raises = None
    _FakeOpenCodeClient.hangs = False
    _FakeOpenCodeClient.emit_idle = True
    _FakeOpenCodeClient.interrupts = 0

    async def _none(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _no_texts(*_args: Any, **_kwargs: Any) -> dict[int, str]:
        return {}

    async def _workspace(**_kwargs: Any) -> str:
        return WORKSPACE

    monkeypatch.setattr(
        labs_runtime, "labs_token_registry", LabsTokenRegistry(secret="test-secret")
    )
    monkeypatch.setattr(labs_runtime, "OpenCodeClient", _FakeOpenCodeClient)
    monkeypatch.setattr(labs_runtime, "compile_guidance", _none)
    monkeypatch.setattr(labs_runtime, "compile_project_material", _none)
    monkeypatch.setattr(labs_runtime, "load_material_texts", _no_texts)
    monkeypatch.setattr(labs_runtime, "ensure_labs_workspace", _workspace)
    return _FakeOpenCodeClient


async def _run_turn(
    db: Any,
    *,
    conversation: _FakeConversation | None = None,
    project_scope: list[str] | None = None,
) -> Any:
    async def _emit(_event_type: str, _payload: dict[str, Any]) -> None:
        return None

    return await labs_runtime.execute_labs_turn(
        db=db,
        conversation=conversation or _FakeConversation(),
        user_id=USER_ID,
        user_groups=["developer"],
        project_scope=[PROJECT_ID] if project_scope is None else project_scope,
        permissions=frozenset({"chat:use"}),
        access_token=None,
        message="Which jobs write to the invoice queue?",
        emit=_emit,
    )


async def test_a_turn_stages_one_row_the_other_lane_can_be_charted_beside(_turn):
    """`interface`, `model_name` and `provider` are spelled the production way.

    `usage_admin` groups by `interface` and by (`model_name`, `provider`), so a
    row that spells either differently is a row nobody can chart against V1.
    """

    _turn.steps = list(_LIVE_STEPS)
    db = _FakeSession()

    result = await _run_turn(db)

    assert result.answer_text == "An answer."
    assert len(db.usage_rows) == 1
    row = db.usage_rows[0]
    assert row.interface == LABS_USAGE_INTERFACE == "labs_opencode"
    assert row.model_name == LABS_USAGE_MODEL_NAME
    assert row.model_name.endswith(f"/{labs_runtime.LABS_MODEL_ID}")
    assert row.provider == LABS_PROVIDER_ID
    assert row.user_id == USER_ID
    assert row.conversation_id == CONVERSATION_ID
    assert row.latency_ms == result.latency_ms
    assert row.query_text is None


async def test_the_row_carries_the_whole_turn_not_just_the_last_step(_turn):
    """The defect that made this worth building off the bus.

    The reply reports the last assistant message alone. Priced off it, this turn
    would have been recorded at 37% of what it cost.
    """

    _turn.steps = list(_LIVE_STEPS)
    _turn.reply = _reply(cost=_LIVE_STEPS[-1][0], tokens=_LIVE_STEPS[-1][1])
    db = _FakeSession()

    await _run_turn(db)

    row = db.usage_rows[0]
    assert row.cost_usd == pytest.approx(_LIVE_TURN_COST)
    assert row.cost_usd > _LIVE_STEPS[-1][0] * 2


async def test_the_second_turn_records_its_own_cost_not_a_running_total(_turn):
    """OpenCode's figures are per step, so no delta arithmetic is applied.

    Both turns run on the same conversation and the same OpenCode session; the
    second one's row must be the second one's spend, neither the cumulative
    total nor a subtraction against the first.
    """

    conversation = _FakeConversation()
    db = _FakeSession()

    _turn.steps = list(_LIVE_STEPS)
    await _run_turn(db, conversation=conversation)

    _turn.steps = [(0.00114675, {"total": 65724, "input": 177, "output": 455,
                                 "reasoning": 1092, "cache": {"read": 64000, "write": 0}})]
    await _run_turn(db, conversation=conversation)

    first, second = db.usage_rows
    assert first.cost_usd == pytest.approx(_LIVE_TURN_COST)
    assert second.cost_usd == pytest.approx(0.00114675)
    assert second.input_tokens == 177 + 64000
    assert second.output_tokens == 455 + 1092


async def test_a_single_project_scope_is_attributed_to_that_project(_turn):
    """The production lane leaves `project_id` null on every row it writes, which
    is why the table cannot answer what a project costs."""

    _turn.steps = list(_LIVE_STEPS)
    db = _FakeSession()

    await _run_turn(db, project_scope=[PROJECT_ID])

    assert db.usage_rows[0].project_id == PROJECT_ID


async def test_an_ambiguous_scope_is_not_attributed_to_a_guess(_turn):
    _turn.steps = list(_LIVE_STEPS)
    db = _FakeSession()

    await _run_turn(db, project_scope=["acme", "globex"])

    assert db.usage_rows[0].project_id is None


async def test_the_reply_is_the_fallback_when_the_bus_reported_nothing(_turn):
    """A dead subscription is survivable by design, so the reply's numbers are
    better than none -- but they are a floor, and the log says which was used."""

    _turn.steps = []
    _turn.reply = _reply(cost=0.0021, tokens={"input": 5528, "output": 1586,
                                              "reasoning": 504,
                                              "cache": {"read": 19328, "write": 0}})
    db = _FakeSession()

    await _run_turn(db)

    row = db.usage_rows[0]
    assert row.cost_usd == pytest.approx(0.0021)
    assert row.input_tokens == 5528 + 19328


async def test_a_turn_that_produced_no_reply_writes_no_row(_turn, caplog):
    """A runtime failure with nothing reported is not a free turn -- it is not a
    turn. A zero row would read as one."""

    _turn.steps = []
    _turn.raises = OpenCodeError("the model provider refused the request")
    db = _FakeSession()

    with caplog.at_level(logging.WARNING, logger="src.labs.runtime"):
        result = await _run_turn(db)

    assert db.usage_rows == []
    assert "the model provider refused the request" in result.answer_text
    # And no "the turn answered but nothing reported spend" warning either:
    # nothing was answered.
    assert "labs.usage_missing" not in caplog.text


async def test_a_deadline_abort_still_records_what_it_already_spent(_turn, monkeypatch):
    """The deliberate exception to "no reply, no row".

    The rule exists because a zero row reads as a free turn. A turn that burned
    two steps before the deadline cut it off did not produce nothing, and
    dropping its cost would put the lane back where it started: real spend with
    no record anywhere.
    """

    monkeypatch.setattr(labs_runtime, "HARD_DEADLINE_SECONDS", 0.01)
    _turn.steps = _LIVE_STEPS[:2]
    _turn.hangs = True
    _turn.emit_idle = False
    db = _FakeSession()

    await _run_turn(db)

    assert _turn.interrupts == 1
    row = db.usage_rows[0]
    assert row.cost_usd == pytest.approx(_LIVE_STEPS[0][0] + _LIVE_STEPS[1][0])


async def test_a_turn_that_reported_no_usage_at_all_writes_no_row(_turn, caplog):
    """An answer with no numbers behind it is a gap in the record, said out loud."""

    _turn.steps = []
    _turn.reply = _reply()
    db = _FakeSession()

    with caplog.at_level(logging.WARNING, logger="src.labs.runtime"):
        result = await _run_turn(db)

    assert db.usage_rows == []
    assert result.answer_text == "An answer."
    assert "labs.usage_missing" in caplog.text


async def test_a_missing_cost_on_the_reply_is_stored_as_null_not_zero(_turn):
    """A zero cost is a claim. A null is the absence of one, and only one of
    those is true when the server reported nothing."""

    _turn.steps = []
    _turn.reply = _reply(tokens={"input": 100, "output": 20, "reasoning": 0,
                                 "cache": {"read": 0, "write": 0}})
    db = _FakeSession()

    await _run_turn(db)

    row = db.usage_rows[0]
    assert row.cost_usd is None
    assert row.input_tokens == 100
    assert row.output_tokens == 20


async def test_missing_tokens_on_the_reply_are_stored_as_nulls(_turn):
    _turn.steps = []
    _turn.reply = _reply(cost=0.0031)
    db = _FakeSession()

    await _run_turn(db)

    row = db.usage_rows[0]
    assert row.cost_usd == pytest.approx(0.0031)
    assert row.input_tokens is None
    assert row.output_tokens is None


# --- Bookkeeping must never cost the answer ---------------------------------


async def test_a_failed_write_does_not_cost_the_user_their_answer(_turn, caplog):
    _turn.steps = list(_LIVE_STEPS)
    db = _FakeSession(fail_on_add=True)

    with caplog.at_level(logging.ERROR, logger="src.labs.runtime"):
        result = await _run_turn(db)

    assert result.answer_text == "An answer."
    assert "labs.usage_unrecorded" in caplog.text
    assert f"conversation_id={CONVERSATION_ID}" in caplog.text


async def test_a_failed_write_leaves_the_session_usable(_turn):
    """`Session.add` issues no SQL, so a failure here has nothing in flight to
    poison -- hence no SAVEPOINT, and hence no rollback of the route's work."""

    _turn.steps = list(_LIVE_STEPS)
    db = _FakeSession(fail_on_add=True)

    await _run_turn(db)

    assert db.rollbacks == 0
    db.fail_on_add = False
    db.add(object())
    assert len(db.added) == 1


async def test_the_runtime_never_commits_the_row_itself(_turn):
    """The route owns the transaction; two committers on one turn could persist a
    cost row for an answer that then failed to save."""

    _turn.steps = list(_LIVE_STEPS)
    db = _FakeSession()

    await _run_turn(db)

    assert db.commits == 0
    assert len(db.usage_rows) == 1


async def test_a_turn_without_a_session_still_answers(_turn, caplog):
    """The lane's own turn tests drive the whole path with `db=None`. Having
    nowhere to stage the row is not a reason to fail a turn that answered."""

    _turn.steps = list(_LIVE_STEPS)

    with caplog.at_level(logging.WARNING, logger="src.labs.runtime"):
        result = await _run_turn(None)

    assert result.answer_text == "An answer."
    assert "labs.usage_unrecorded" in caplog.text
