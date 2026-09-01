"""Coverage for the topology seam in the bounded tool loop.

Three properties are load-bearing here:
  * topology names route to the topology dispatcher and runtime names to the runtime
    one, because the runtime dispatcher answers DISABLED without a provider key;
  * the authenticated access_token reaches the topology dispatcher (dropping the
    forward would silently rely on project_scope alone);
  * runtime guidance and the runtime catalogue disappear on a runtime-disabled
    deployment, while topology stays offered.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.config import settings
from src.generation.agent_loop import (
    _ANSWER_DISCIPLINE_GUIDE,
    _TOPOLOGY_TOOL_GUIDE,
    _describe_page_scope,
    _with_tool_guides,
    run_runtime_tool_loop,
)
from src.generation.llm_client import RuntimeToolCallRequest, ToolAwareLLMResponse
from src.generation.usage import UsageStats
from src.runtime.models import RuntimeToolDefinition, RuntimeToolResult

MESSAGES = [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "What feeds the werkorder job?"},
]

RUNTIME_TOOLS = [
    RuntimeToolDefinition(
        name="detect_failures", description="Detect failures", input_schema={"type": "object"}
    ),
]

TOPOLOGY_TOOLS = [
    RuntimeToolDefinition(
        name="get_topology_overview",
        description="Project landscape overview",
        input_schema={"type": "object"},
    ),
    RuntimeToolDefinition(
        name="get_topology_node",
        description="One node's detail",
        input_schema={"type": "object"},
    ),
    RuntimeToolDefinition(
        name="find_topology_node",
        description="Locate a node by name",
        input_schema={"type": "object"},
    ),
    RuntimeToolDefinition(
        name="list_topology_dependencies",
        description="Upstream/downstream dependencies",
        input_schema={"type": "object"},
    ),
]

PAGE_SCOPE = {
    "surface": "topology",
    "project_id": "werkorder",
    "level": {"kind": "job", "jobName": "vw_load_werkorder", "sourceRoot": "src/main"},
}


@pytest.fixture(autouse=True)
def stub_topology_catalogue(monkeypatch):
    """Topology tool bodies belong to the topology layer; the seam is what is tested."""
    monkeypatch.setattr(
        "src.generation.agent_loop.list_topology_tools", lambda: list(TOPOLOGY_TOOLS)
    )


def _decision(tool_call=None, text=None):
    return ToolAwareLLMResponse(
        provider="kimi",
        tool_calls=[tool_call] if tool_call else [],
        text=text,
        assistant_message={"role": "assistant", "content": text or "", "tool_calls": []},
        usage=UsageStats(provider="kimi", model="kimi-k2.6", input_tokens=10, output_tokens=5),
    )


def _stream_of(*chunks: str):
    async def _stream(messages, images=None):
        for chunk in chunks:
            yield chunk

    return _stream


def _tool_result(tool_name: str) -> RuntimeToolResult:
    return RuntimeToolResult(tool_name=tool_name, success=True, data={}, latency_ms=3)


async def _run(tool_name: str, *, provider_key: str = "mock", **loop_kwargs):
    """Drive one tool call of `tool_name` through the loop, returning both dispatchers,
    the captured per-iteration message lists, and the offered tool catalogue."""
    decisions = [
        _decision(
            tool_call=RuntimeToolCallRequest(
                call_id="call_1", tool_name=tool_name, arguments={"name": "x"}
            )
        ),
        _decision(text="done"),
    ]
    captured_messages: list[list[dict]] = []
    captured_tools: list[list[RuntimeToolDefinition]] = []

    async def _decide(messages, tools, images=None):
        captured_messages.append(messages)
        captured_tools.append(tools)
        return decisions[len(captured_messages) - 1]

    runtime_exec = AsyncMock(return_value=_tool_result(tool_name))
    topology_exec = AsyncMock(return_value=_tool_result(tool_name))

    with (
        patch("src.generation.agent_loop.list_runtime_tools", return_value=RUNTIME_TOOLS),
        patch("src.generation.agent_loop.generate_answer_with_runtime_tools", side_effect=_decide),
        patch("src.generation.agent_loop.execute_runtime_tool", runtime_exec),
        patch("src.generation.agent_loop.execute_topology_tool", topology_exec),
        patch("src.generation.agent_loop.generate_answer_stream", _stream_of("done")),
        patch("src.generation.agent_loop.get_last_usage", return_value=None),
        patch.object(settings, "runtime_provider_key", provider_key),
    ):
        outcome = await run_runtime_tool_loop(MESSAGES, **loop_kwargs)

    return {
        "outcome": outcome,
        "runtime_exec": runtime_exec,
        "topology_exec": topology_exec,
        "messages": captured_messages,
        "tools": captured_tools,
    }


class TestDispatchRouting:
    @pytest.mark.parametrize(
        "tool_name",
        [
            "get_topology_overview",
            "find_topology_node",
            "get_topology_node",
            "list_topology_dependencies",
        ],
    )
    async def test_topology_names_reach_only_the_topology_dispatcher(self, tool_name):
        run = await _run(tool_name, project_scope=["werkorder"], access_token="tok-abc")

        run["topology_exec"].assert_awaited_once()
        run["runtime_exec"].assert_not_awaited()
        kwargs = run["topology_exec"].await_args.kwargs
        assert kwargs["project_scope"] == ["werkorder"]
        assert run["outcome"].used_runtime_data is True

    async def test_runtime_names_reach_only_the_runtime_dispatcher(self):
        run = await _run("detect_failures", project_scope=["werkorder"], access_token="tok-abc")

        run["runtime_exec"].assert_awaited_once()
        run["topology_exec"].assert_not_awaited()
        assert run["runtime_exec"].await_args.kwargs["project_scope"] == ["werkorder"]

    async def test_access_token_is_forwarded_to_the_topology_dispatcher(self):
        """MCP performs the authoritative project-access check with this token; if the
        forward is dropped this assertion fails rather than degrading silently."""
        run = await _run("get_topology_node", access_token="tok-abc")

        assert run["topology_exec"].await_args.kwargs["access_token"] == "tok-abc"

    async def test_topology_stays_dispatchable_with_runtime_disabled(self):
        """execute_runtime_tool answers DISABLED without a provider key — topology
        must not inherit that, since it reads published artifacts."""
        run = await _run("get_topology_node", provider_key="", access_token="tok-abc")

        run["topology_exec"].assert_awaited_once()
        assert run["outcome"].final_answer == "done"


class TestRuntimeGating:
    async def test_runtime_catalogue_and_guide_omitted_when_provider_key_is_empty(self):
        run = await _run("get_topology_node", provider_key="")

        offered = [tool.name for tool in run["tools"][0]]
        assert "detect_failures" not in offered
        assert "get_topology_overview" in offered
        system = run["messages"][0][0]["content"]
        assert "analyze_execution_failure" not in system
        assert "get_topology_overview" in system

    async def test_runtime_catalogue_and_guide_present_when_provider_configured(self):
        run = await _run("detect_failures", provider_key="mock")

        offered = [tool.name for tool in run["tools"][0]]
        assert "detect_failures" in offered
        assert "get_topology_overview" in offered
        system = run["messages"][0][0]["content"]
        assert "analyze_execution_failure" in system
        assert "get_topology_overview" in system

    def test_prior_runtime_memory_is_not_replayed_on_a_disabled_deployment(self):
        summary = [{"tool_name": "detect_failures", "execution_id": "exec-7841"}]
        out = _with_tool_guides(
            [{"role": "system", "content": "Base."}], summary, runtime_enabled=False
        )
        assert "exec-7841" not in out[0]["content"]


class TestBudgetHint:
    async def test_remaining_tool_call_count_is_stated_and_counts_down(self):
        always_call = _decision(
            tool_call=RuntimeToolCallRequest(
                call_id="c", tool_name="get_topology_node", arguments={}
            )
        )
        captured: list[list[dict]] = []

        async def _decide(messages, tools, images=None):
            captured.append(messages)
            return always_call

        with (
            patch("src.generation.agent_loop.settings") as mock_settings,
            patch("src.generation.agent_loop.list_runtime_tools", return_value=RUNTIME_TOOLS),
            patch(
                "src.generation.agent_loop.generate_answer_with_runtime_tools",
                side_effect=_decide,
            ),
            patch(
                "src.generation.agent_loop.execute_topology_tool",
                AsyncMock(return_value=_tool_result("get_topology_node")),
            ),
            patch("src.generation.agent_loop.generate_answer_stream", _stream_of("done")),
            patch("src.generation.agent_loop.get_last_usage", return_value=None),
        ):
            mock_settings.runtime_provider_key = "mock"
            mock_settings.runtime_tool_loop_max_iterations = 3
            mock_settings.runtime_tool_max_parallel_calls = 5
            await run_runtime_tool_loop(MESSAGES)

        remaining = [messages[0]["content"] for messages in captured]
        assert "3 tool-calling turn(s) remain" in remaining[0]
        assert "2 tool-calling turn(s) remain" in remaining[1]
        assert "1 tool-calling turn(s) remain" in remaining[2]
        # One hint per request: the count must not accumulate across iterations.
        assert all(content.count("Tool budget:") == 1 for content in remaining)


class TestPageScopeHint:
    async def test_hint_is_injected_early_as_a_preference(self):
        run = await _run("get_topology_node", page_scope=PAGE_SCOPE)

        system = run["messages"][0][0]["content"]
        assert "vw_load_werkorder" in system
        assert "werkorder" in system
        assert "PREFERENCE, not a restriction" in system
        assert "runtime/log questions" in system
        # Early: ahead of both tool catalogues, so scope frames the tool choice.
        assert system.index("Page context:") < system.index("get_topology_overview")
        assert system.index("Page context:") < system.index("analyze_execution_failure")

    async def test_absent_page_scope_injects_no_hint(self):
        run = await _run("get_topology_node")

        assert "Page context:" not in run["messages"][0][0]["content"]

    def test_project_level_scope_renders_without_an_entity_name(self):
        hint = _describe_page_scope(
            {"surface": "topology", "project_id": "werkorder", "level": {"kind": "project"}}
        )
        assert hint is not None
        assert "the project level" in hint

    def test_rich_scope_names_selected_entity_tab_and_filters(self):
        hint = _describe_page_scope(
            {
                "surface": "topology",
                "project_id": "werkorder",
                "level": {"kind": "component", "jobName": "sync", "componentName": "tMap_1"},
                "selected_entity": {
                    "kind": "column",
                    "label": "WorkOrder.Status",
                    "entity_id": "output::Status",
                },
                "active_detail_tab": "mapping",
                "filters": [{"name": "connections", "value": "main flow"}],
            }
        )

        assert hint is not None
        assert "Selected column 'WorkOrder.Status'" in hint
        assert "Active detail tab: 'mapping'" in hint
        assert "connections=main flow" in hint

    @pytest.mark.parametrize(
        "page_scope",
        [
            {"project_id": "p"},  # no level
            {"project_id": 5, "level": {"kind": "job"}},  # non-string project
            {"project_id": "p", "level": {"jobName": "x"}},  # no kind
            {"project_id": "p", "level": "job"},  # level not an object
        ],
    )
    def test_malformed_scope_is_dropped_rather_than_rendered(self, page_scope):
        """The value is client-supplied, so the renderer must fail closed on any shape
        that slipped past validation instead of formatting garbage into the prompt."""
        assert _describe_page_scope(page_scope) is None


class TestTopologyGuide:
    def test_guide_names_all_four_tools(self):
        content = _with_tool_guides([{"role": "system", "content": "Base."}])[0]["content"]
        for name in (
            "get_topology_overview",
            "find_topology_node",
            "get_topology_node",
            "list_topology_dependencies",
        ):
            assert name in content

    def test_guide_states_names_are_accepted_directly(self):
        assert "accept a name directly" in _TOPOLOGY_TOOL_GUIDE
        assert "separate id-lookup call" in _TOPOLOGY_TOOL_GUIDE

    def test_guide_requires_presenting_candidates_on_ambiguity(self):
        assert "returns candidates" in _TOPOLOGY_TOOL_GUIDE
        assert "Never silently pick one" in _TOPOLOGY_TOOL_GUIDE

    def test_guide_requires_disclosing_a_weak_overlay(self):
        assert "topology_state" in _TOPOLOGY_TOOL_GUIDE
        assert "missing, unvalidated, or stale" in _TOPOLOGY_TOOL_GUIDE
        assert "scanner evidence (job names" in _TOPOLOGY_TOOL_GUIDE

    def test_guide_says_topology_reads_the_canvas_artifacts(self):
        assert "the topology canvas renders" in _TOPOLOGY_TOOL_GUIDE


class TestAnswerDiscipline:
    def test_source_labelling_covers_topology(self):
        assert "(from topology)" in _ANSWER_DISCIPLINE_GUIDE
        assert "(from logs)" in _ANSWER_DISCIPLINE_GUIDE
        assert "topology structure as live log data" in _ANSWER_DISCIPLINE_GUIDE

    def test_structural_shape_is_entity_led_and_has_no_next_checks(self):
        assert "structural questions" in _ANSWER_DISCIPLINE_GUIDE
        assert "name the entities" in _ANSWER_DISCIPLINE_GUIDE
        assert "state provenance" in _ANSWER_DISCIPLINE_GUIDE
        assert "Do not add a next-checks section" in _ANSWER_DISCIPLINE_GUIDE

    def test_brevity_rule_mentions_the_narrow_panel(self):
        assert "narrow side panel" in _ANSWER_DISCIPLINE_GUIDE
        assert "one line beats five bullets" in _ANSWER_DISCIPLINE_GUIDE

    def test_answer_discipline_survives_a_runtime_disabled_deployment(self):
        """Structural shape and labelling must not vanish with the runtime guide."""
        out = _with_tool_guides([{"role": "system", "content": "Base."}], runtime_enabled=False)
        content = out[0]["content"]
        assert "Do not add a next-checks section" in content
        assert "(from topology)" in content
        assert "Finding:" not in content  # the failure shape belongs to runtime
