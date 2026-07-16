"""Tests for the fixed runtime tool allowlist (src/runtime/registry.py)."""

import pytest

from src.runtime.models import (
    AnalyzeExecutionFailureInput,
    DetectFailuresInput,
    FindSimilarFailuresInput,
    GetExecutionDetailsInput,
    GetExecutionTimelineInput,
    GetRuntimeIncidentInput,
    ListExecutionsInput,
    ListRuntimeEnginesInput,
    ListRuntimeHealthInput,
    ListRuntimeInventoryInput,
    ListRuntimeJobsInput,
    ListRuntimePlansInput,
    ListRuntimeSyncStatusInput,
)
from src.runtime.registry import (
    RuntimeToolValidationError,
    UnknownRuntimeToolError,
    list_runtime_tool_definitions,
    resolve_tool_call,
)


class TestListRuntimeToolDefinitions:
    def test_lists_all_user_facing_tools(self):
        definitions = list_runtime_tool_definitions()
        names = {d.name for d in definitions}
        assert names == {
            "search_logs",
            "list_executions",
            "get_execution_details",
            "detect_failures",
            "analyze_execution_failure",
            "get_execution_timeline",
            "get_runtime_incident",
            "find_similar_failures",
            "list_runtime_sync_status",
            "list_runtime_health",
            "list_runtime_sources",
            "list_runtime_engines",
            "list_runtime_jobs",
            "list_runtime_artifacts",
            "list_runtime_connections",
            "list_runtime_dependencies",
            "get_runtime_dependency_summary",
            "list_runtime_plans",
            "list_runtime_unhealthy",
        }
        assert len(definitions) == 19

    def test_high_level_tool_descriptions_steer_model(self):
        definitions = {d.name: d for d in list_runtime_tool_definitions()}
        assert "PRIMARY" in definitions["analyze_execution_failure"].description
        assert "incident_id" in definitions["get_runtime_incident"].description
        assert "has this happened before" in definitions["find_similar_failures"].description
        assert "freshness" in definitions["list_runtime_sync_status"].description
        assert "failure rate" in definitions["list_runtime_health"].description
        assert "which engines do we have" in definitions["list_runtime_engines"].description
        assert "which jobs use a" in definitions["list_runtime_dependencies"].description
        assert "credentials" in definitions["list_runtime_connections"].description
        assert "unhealthy engines" in definitions["list_runtime_unhealthy"].description
        assert "get_execution_logs" not in definitions


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

    def test_get_execution_logs_is_not_user_facing_tool(self):
        with pytest.raises(UnknownRuntimeToolError):
            resolve_tool_call("get_execution_logs", {"execution_id": "exec-1", "limit": 10})

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

    def test_list_runtime_sync_status_accepts_filters(self):
        params = resolve_tool_call(
            "list_runtime_sync_status",
            {"source_key": "mock", "project_id": "project-a", "limit": 5},
        )
        assert isinstance(params, ListRuntimeSyncStatusInput)
        assert params.source_key == "mock"
        assert params.project_id == "project-a"

    def test_list_runtime_health_accepts_filters(self):
        params = resolve_tool_call("list_runtime_health", {"source_key": "mock", "limit": 5})
        assert isinstance(params, ListRuntimeHealthInput)
        assert params.source_key == "mock"
        assert params.project_id is None

    def test_list_runtime_sources_accepts_filters(self):
        params = resolve_tool_call(
            "list_runtime_sources",
            {"source_key": "mock", "project_id": "project-a", "limit": 5},
        )
        assert isinstance(params, ListRuntimeInventoryInput)
        assert params.source_key == "mock"
        assert params.project_id == "project-a"

    def test_list_runtime_engines_accepts_status_filter(self):
        params = resolve_tool_call("list_runtime_engines", {"status": "online", "limit": 5})
        assert isinstance(params, ListRuntimeEnginesInput)
        assert params.status.value == "online"

    def test_list_runtime_jobs_accepts_estate_filters(self):
        params = resolve_tool_call(
            "list_runtime_jobs",
            {"environment_id": "env-prod", "workspace_id": "ws-main", "engine_id": "engine-1"},
        )
        assert isinstance(params, ListRuntimeJobsInput)
        assert params.environment_id == "env-prod"
        assert params.workspace_id == "ws-main"
        assert params.engine_id == "engine-1"

    def test_list_runtime_plans_accepts_estate_filters(self):
        params = resolve_tool_call(
            "list_runtime_plans",
            {"environment_id": "env-prod", "workspace_id": "ws-main", "status": "published"},
        )
        assert isinstance(params, ListRuntimePlansInput)
        assert params.environment_id == "env-prod"
        assert params.workspace_id == "ws-main"
        assert params.status == "published"

    def test_list_runtime_unhealthy_accepts_inventory_filters(self):
        params = resolve_tool_call("list_runtime_unhealthy", {"project_id": "project-a", "limit": 3})
        assert isinstance(params, ListRuntimeInventoryInput)
        assert params.project_id == "project-a"
        assert params.limit == 3

    def test_find_similar_failures_accepts_execution_id(self):
        params = resolve_tool_call("find_similar_failures", {"execution_id": "exec-1"})
        assert isinstance(params, FindSimilarFailuresInput)
        assert params.execution_id == "exec-1"
        assert params.incident_id is None

    def test_find_similar_failures_rejects_oversized_limit(self):
        with pytest.raises(RuntimeToolValidationError):
            resolve_tool_call("find_similar_failures", {"incident_id": "abc123", "limit": 100})
