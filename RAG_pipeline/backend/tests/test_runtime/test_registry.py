"""Tests for the fixed runtime tool allowlist (src/runtime/registry.py)."""

import pytest

from src.runtime.models import (
    DetectFailuresInput,
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    ListExecutionsInput,
)
from src.runtime.registry import (
    RuntimeToolValidationError,
    UnknownRuntimeToolError,
    list_runtime_tool_definitions,
    resolve_tool_call,
)


class TestListRuntimeToolDefinitions:
    def test_lists_exactly_four_generic_tools(self):
        definitions = list_runtime_tool_definitions()
        names = {d.name for d in definitions}
        assert names == {
            "list_executions",
            "get_execution_details",
            "detect_failures",
            "get_execution_logs",
        }
        assert len(definitions) == 4


class TestResolveToolCall:
    def test_unknown_tool_raises_without_touching_provider(self):
        with pytest.raises(UnknownRuntimeToolError):
            resolve_tool_call("delete_everything", {})

    def test_list_executions_accepts_valid_arguments(self):
        params = resolve_tool_call("list_executions", {"project_name": "demo", "limit": 5})
        assert isinstance(params, ListExecutionsInput)
        assert params.project_name == "demo"
        assert params.limit == 5

    def test_get_execution_details_requires_execution_id(self):
        with pytest.raises(RuntimeToolValidationError):
            resolve_tool_call("get_execution_details", {})

    def test_get_execution_details_accepts_execution_id(self):
        params = resolve_tool_call("get_execution_details", {"execution_id": "exec-1"})
        assert isinstance(params, GetExecutionDetailsInput)
        assert params.execution_id == "exec-1"

    def test_detect_failures_accepts_empty_arguments(self):
        params = resolve_tool_call("detect_failures", {})
        assert isinstance(params, DetectFailuresInput)

    def test_get_execution_logs_rejects_malformed_limit(self):
        with pytest.raises(RuntimeToolValidationError):
            resolve_tool_call("get_execution_logs", {"execution_id": "exec-1", "limit": "not-a-number"})

    def test_get_execution_logs_accepts_valid_arguments(self):
        params = resolve_tool_call("get_execution_logs", {"execution_id": "exec-1", "limit": 10})
        assert isinstance(params, GetExecutionLogsInput)
        assert params.execution_id == "exec-1"
        assert params.limit == 10
