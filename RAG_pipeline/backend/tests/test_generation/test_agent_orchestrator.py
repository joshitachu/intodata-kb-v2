from src.generation.agent_orchestrator import AgentTool, AgentTrace, MAX_AGENT_STEPS


def test_agent_trace_caps_steps() -> None:
    trace = AgentTrace()

    for _ in range(MAX_AGENT_STEPS + 2):
        trace.complete(AgentTool.SEARCH_DOCUMENTS)

    assert len(trace.steps) == MAX_AGENT_STEPS


def test_agent_step_serializes_for_sse() -> None:
    trace = AgentTrace()

    trace.complete(AgentTool.FETCH_METADATA)

    assert trace.steps[0].model_dump() == {
        "id": "fetch_metadata",
        "label": "Fetching metadata",
        "status": "completed",
    }
