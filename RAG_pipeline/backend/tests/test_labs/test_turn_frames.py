"""What the lane tells the client about a turn, and what it keeps out of the answer.

Two failures observed on one live turn, both fixed here at the transport:

* **The plan was prose.** The agent's step list reached the user only by being
  narrated into `assistant.delta`, so thirteen paragraphs of running commentary
  -- naming `cMessageRouter_1` and `cJMS_1` before the reader had seen a single
  conclusion -- sat above the answer. The steps now travel as `plan.updated`, and
  the answer stream carries the answer.
* **Every call was counted twice.** The live `tool.running` frame named a call
  `nexus_read_topology_structure` while the late `tool.completed` frame named the
  same call `topology.landscape.read_structure`, so nothing downstream could tell
  they were one call. The header read "81 tool calls" for a trace holding 40.
"""

from __future__ import annotations

from typing import Any

from src.labs.contracts import LabsToolCallRecord
from src.labs.runtime import (
    LABS_MCP_SERVER_KEY,
    _BusStream,
    _emit_tool_calls,
    labs_tool_label,
)

SESSION = "ses_labs_frames"


def _tool_part(
    part_id: str,
    tool: str,
    *,
    status: str = "running",
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One `message.part.updated` frame for a tool part, as the bus emits it."""

    return {
        "type": "message.part.updated",
        "properties": {
            "sessionID": SESSION,
            "part": {
                "id": part_id,
                "type": "tool",
                "tool": tool,
                "callID": f"call_{part_id}",
                "state": {"status": status, **(state or {})},
            },
        },
    }


def _todos(*entries: tuple[str, str, str]) -> dict[str, Any]:
    return {
        "input": {
            "todos": [
                {"id": identifier, "content": content, "status": status, "priority": "high"}
                for identifier, content, status in entries
            ]
        }
    }


class _Recorder:
    """Collects the public frames a stream emits, in order."""

    def __init__(self) -> None:
        self.frames: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.frames.append((event_type, payload))

    def of_type(self, event_type: str) -> list[dict[str, Any]]:
        return [payload for name, payload in self.frames if name == event_type]


async def _feed(stream: _BusStream, events: list[dict[str, Any]]) -> None:
    for event in events:
        await stream.handle(event)


# --- The plan leaves the backend as a structured frame ----------------------


async def test_a_todowrite_call_publishes_its_steps() -> None:
    recorder = _Recorder()
    stream = _BusStream(emit=recorder.emit)

    await _feed(
        stream,
        [
            _tool_part(
                "prt_plan",
                "todowrite",
                state=_todos(
                    ("1", "Find the jobs in this chain", "in_progress"),
                    ("2", "Read the interface document", "pending"),
                ),
            )
        ],
    )

    plans = recorder.of_type("plan.updated")
    assert len(plans) == 1
    assert plans[0]["todos"] == [
        {"id": "1", "content": "Find the jobs in this chain", "status": "in_progress"},
        {"id": "2", "content": "Read the interface document", "status": "pending"},
    ]
    # The single replaceable status line is the step actually in progress.
    assert plans[0]["summary"] == "Find the jobs in this chain"
    assert plans[0]["status"] == "running"


async def test_the_plan_frame_drops_everything_except_the_three_fields() -> None:
    """`priority`, and any other argument, never leaves the backend.

    A tool's input is a private key in the production event contract because it
    can carry a path, a query or a credential. The plan is an exception carved
    to three named fields, not a hole.
    """

    recorder = _Recorder()
    stream = _BusStream(emit=recorder.emit)

    await _feed(
        stream,
        [
            _tool_part(
                "prt_plan",
                "todowrite",
                state={
                    "input": {
                        "todos": [
                            {
                                "id": "1",
                                "content": "Read the runtime records",
                                "status": "pending",
                                "priority": "high",
                                "notes": "/labs-workspaces/conversation-8811/secret",
                            }
                        ]
                    }
                },
            )
        ],
    )

    assert recorder.of_type("plan.updated")[0]["todos"] == [
        {"id": "1", "content": "Read the runtime records", "status": "pending"}
    ]


async def test_the_repeated_frames_of_one_call_publish_the_plan_once() -> None:
    """`pending` -> `running` -> `completed` is one call, and one plan."""

    recorder = _Recorder()
    stream = _BusStream(emit=recorder.emit)
    steps = _todos(("1", "Find the jobs in this chain", "pending"))

    await _feed(
        stream,
        [
            _tool_part("prt_plan", "todowrite", status="pending", state=steps),
            _tool_part("prt_plan", "todowrite", status="running", state=steps),
            _tool_part("prt_plan", "todowrite", status="completed", state=steps),
        ],
    )

    assert len(recorder.of_type("plan.updated")) == 1
    # The tool call itself is still announced exactly once, as before.
    assert len(recorder.of_type("tool.running")) == 1


async def test_marking_a_step_done_publishes_the_new_plan() -> None:
    """The updates are the point: a second `todowrite` is a new part, and a
    changed list on the same part is still a change worth publishing."""

    recorder = _Recorder()
    stream = _BusStream(emit=recorder.emit)

    await _feed(
        stream,
        [
            _tool_part(
                "prt_plan_1",
                "todowrite",
                state=_todos(
                    ("1", "Find the jobs in this chain", "in_progress"),
                    ("2", "Read the interface document", "pending"),
                ),
            ),
            _tool_part(
                "prt_plan_2",
                "todowrite",
                state=_todos(
                    ("1", "Find the jobs in this chain", "completed"),
                    ("2", "Read the interface document", "in_progress"),
                ),
            ),
            _tool_part(
                "prt_plan_3",
                "todowrite",
                state=_todos(
                    ("1", "Find the jobs in this chain", "completed"),
                    ("2", "Read the interface document", "completed"),
                ),
            ),
        ],
    )

    plans = recorder.of_type("plan.updated")
    assert [plan["summary"] for plan in plans] == [
        "Find the jobs in this chain",
        "Read the interface document",
        # Nothing is in progress once every step is closed, so there is no
        # status line to show and none is invented.
        None,
    ]
    assert [plan["status"] for plan in plans] == ["running", "running", "completed"]


async def test_a_tool_that_is_not_the_plan_tool_publishes_no_plan() -> None:
    recorder = _Recorder()
    stream = _BusStream(emit=recorder.emit)

    await _feed(
        stream,
        [
            _tool_part(
                "prt_read",
                "nexus_read_topology_structure",
                state={"input": {"operation": "get_component_detail", "todos": "not a list"}},
            )
        ],
    )

    assert recorder.of_type("plan.updated") == []
    assert len(recorder.of_type("tool.running")) == 1


async def test_a_plan_with_no_readable_step_publishes_nothing() -> None:
    recorder = _Recorder()
    stream = _BusStream(emit=recorder.emit)

    await _feed(
        stream,
        [
            _tool_part(
                "prt_plan",
                "todowrite",
                state={"input": {"todos": [{"status": "pending"}, "not an object", {"content": "  "}]}},
            )
        ],
    )

    assert recorder.of_type("plan.updated") == []


# --- One call, one label, on both of its frames -----------------------------


def test_the_late_frame_is_labelled_with_the_name_the_model_called() -> None:
    record = LabsToolCallRecord(
        capability_id="topology.landscape.read_structure",
        tool_name="read_topology_structure",
        succeeded=True,
    )

    assert labs_tool_label(record) == f"{LABS_MCP_SERVER_KEY}_read_topology_structure"


def test_a_record_without_a_tool_name_falls_back_instead_of_guessing() -> None:
    record = LabsToolCallRecord(
        capability_id="topology.landscape.read_structure", succeeded=True
    )

    assert labs_tool_label(record) == "topology.landscape.read_structure"


async def test_the_live_and_late_frames_of_one_call_agree_on_the_label() -> None:
    """The regression that made 40 calls read as 81.

    The live frame comes off the bus and the late one off the MCP bridge; they
    are produced by different code at different times, and nothing downstream can
    pair them except this string.
    """

    recorder = _Recorder()
    stream = _BusStream(emit=recorder.emit)

    await _feed(
        stream,
        [_tool_part("prt_read", f"{LABS_MCP_SERVER_KEY}_read_topology_structure")],
    )
    await _emit_tool_calls(
        recorder.emit,
        [
            LabsToolCallRecord(
                capability_id="topology.landscape.read_structure",
                tool_name="read_topology_structure",
                succeeded=True,
                summary="Read 3 components",
            )
        ],
    )

    running = recorder.of_type("tool.running")[0]
    completed = recorder.of_type("tool.completed")[0]
    assert running["tool_label"] == completed["tool_label"]


async def test_the_live_label_survives_a_server_supplied_title() -> None:
    """A title is a display string and may appear on any frame.

    `tool_label` has to stay the stable name or the correlation breaks on
    whichever calls the server happened to have titled; the title is reported as
    the summary, where a changing value costs nothing.
    """

    recorder = _Recorder()
    stream = _BusStream(emit=recorder.emit)

    await _feed(
        stream,
        [
            _tool_part(
                "prt_read",
                f"{LABS_MCP_SERVER_KEY}_read_topology_structure",
                state={"title": "Read topology structure"},
            )
        ],
    )

    running = recorder.of_type("tool.running")[0]
    assert running["tool_label"] == f"{LABS_MCP_SERVER_KEY}_read_topology_structure"
    assert running["summary"] == "Read topology structure"


# --- Nothing above may reach the answer -------------------------------------


async def test_no_plan_frame_ever_becomes_answer_text() -> None:
    recorder = _Recorder()
    stream = _BusStream(emit=recorder.emit)

    await _feed(
        stream,
        [
            _tool_part(
                "prt_plan",
                "todowrite",
                state=_todos(("1", "Find the jobs in this chain", "in_progress")),
            )
        ],
    )

    assert stream.text == ""
    assert recorder.of_type("assistant.delta") == []


# --- The turn log counts every tool, not only Nexus capabilities ------------
#
# A measured turn logged `tool_calls=4` and had in fact run twelve: four
# `search_documentation` calls the MCP bridge saw, and eight `grep`/`read`
# calls over the mounted `docs/` that it could not see. Every tuning decision
# about call economy was being made against the partial number, and the hidden
# eight pulled more evidence than the visible four.


async def test_the_stream_counts_every_tool_the_turn_ran() -> None:
    recorder = _Recorder()
    stream = _BusStream(emit=recorder.emit)

    await _feed(
        stream,
        [
            _tool_part("p1", "nexus_search_documentation"),
            _tool_part("p2", "nexus_search_documentation"),
            _tool_part("p3", "grep"),
            _tool_part("p4", "read"),
            _tool_part("p5", "read"),
        ],
    )

    assert stream.tool_counts == {
        "nexus_search_documentation": 2,
        "grep": 1,
        "read": 2,
    }
    assert sum(stream.tool_counts.values()) == 5


async def test_the_repeated_frames_of_one_call_count_once() -> None:
    """`pending` -> `running` -> `completed` is one call, as it is one frame."""

    recorder = _Recorder()
    stream = _BusStream(emit=recorder.emit)

    await _feed(
        stream,
        [
            _tool_part("p1", "grep", status="pending"),
            _tool_part("p1", "grep", status="running"),
            _tool_part("p1", "grep", status="completed"),
        ],
    )

    assert stream.tool_counts == {"grep": 1}
    assert len(recorder.of_type("tool.running")) == 1


async def test_the_counter_is_a_copy_a_caller_cannot_corrupt() -> None:
    recorder = _Recorder()
    stream = _BusStream(emit=recorder.emit)
    await _feed(stream, [_tool_part("p1", "read")])

    taken = stream.tool_counts
    taken["read"] = 999
    taken["invented"] = 1

    assert stream.tool_counts == {"read": 1}
