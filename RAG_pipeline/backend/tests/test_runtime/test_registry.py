"""Tests for the fixed runtime tool allowlist (src/runtime/registry.py)."""

import pytest

from src.runtime.models import (
    AnalyzeExecutionFailureInput,
    DetectFailuresInput,
    FindSimilarFailuresInput,
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    GetExecutionTimelineInput,
    GetRuntimeIncidentInput,
    ListExecutionsInput,
)
from src.runtime.registry import (
    RuntimeToolValidationError,
    UnknownRuntimeToolError,
    list_runtime_tool_definitions,
    resolve_tool_call,
)


class TestListRuntimeToolDefinitions:
    def test_lists_all_eight_tools(self):
        definitions = list_runtime_tool_definitions()
        names = {d.name for d in definitions}
        assert names == {
            "list_executions",
            "get_execution_details",
            "detect_failures",
            "get_execution_logs",
            "analyze_execution_failure",
            "get_execution_timeline",
            "get_runtime_incident",
            "find_similar_failures",
        }
        assert len(definitions) == 8

    def test_high_level_tool_descriptions_steer_model(self):
        definitions = {d.name: d for d in list_runtime_tool_definitions()}
        assert "PRIMARY" in definitions["analyze_execution_failure"].description
        assert "analyze_execution_failure" in definitions["get_execution_logs"].description
        assert "incident_id" in definitions["get_runtime_incident"].description
        assert "has this happened before" in definitions["find_similar_failures"].description


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

    def test_analyze_execution_failure_requires_execution_id(self):
        with pytest.raises(RuntimeToolValidationError):
            resolve_tool_call("analyze_execution_failure", {})

    def test_analyze_execution_failure_accepts_execution_id(self):
        params = resolve_tool_call("analyze_execution_failure", {"execution_id": "exec-42"})
        assert isinstance(params, AnalyzeExecutionFailureInput)
        assert params.execution_id == "exec-42"

    def test_get_execution_timeline_accepts_optional_filters(self):
        params = resolve_tool_call(
            "get_execution_timeline",
            {"execution_id": "exec-42", "min_severity": "error", "limit": 30},
        )
        assert isinstance(params, GetExecutionTimelineInput)
        assert params.execution_id == "exec-42"
        assert params.limit == 30

    def test_get_execution_timeline_rejects_oversized_limit(self):
        with pytest.raises(RuntimeToolValidationError):
            resolve_tool_call("get_execution_timeline", {"execution_id": "exec-42", "limit": 9999})

    def test_get_runtime_incident_requires_incident_id(self):
        with pytest.raises(RuntimeToolValidationError):
            resolve_tool_call("get_runtime_incident", {})

    def test_get_runtime_incident_accepts_optional_params(self):
        params = resolve_tool_call(
            "get_runtime_incident",
            {"incident_id": "abc123", "include_evidence": False},
        )
        assert isinstance(params, GetRuntimeIncidentInput)
        assert params.incident_id == "abc123"
        assert params.include_evidence is False

    def test_find_similar_failures_requires_at_least_one_id(self):
        with pytest.raises(RuntimeToolValidationError):
            resolve_tool_call("find_similar_failures", {})

    def test_find_similar_failures_rejects_both_ids(self):
        with pytest.raises(RuntimeToolValidationError):
            resolve_tool_call(
                "find_similar_failures",
                {"incident_id": "abc123", "execution_id": "exec-1"},
            )

    def test_find_similar_failures_accepts_incident_id(self):
        params = resolve_tool_call("find_similar_failures", {"incident_id": "abc123"})
        assert isinstance(params, FindSimilarFailuresInput)
        assert params.incident_id == "abc123"
        assert params.execution_id is None

    def test_find_similar_failures_accepts_execution_id(self):
        params = resolve_tool_call("find_similar_failures", {"execution_id": "exec-1"})
        assert isinstance(params, FindSimilarFailuresInput)
        assert params.execution_id == "exec-1"
        assert params.incident_id is None

    def test_find_similar_failures_rejects_oversized_limit(self):
        with pytest.raises(RuntimeToolValidationError):
            resolve_tool_call("find_similar_failures", {"incident_id": "abc123", "limit": 100})
