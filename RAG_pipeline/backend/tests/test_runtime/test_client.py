"""Tests for the generic runtime tool router (src/runtime/client.py)."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from src.runtime.client import _redact, _sanitize_and_bound, execute_runtime_tool
from src.runtime.models import (
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeToolCall,
    RuntimeToolErrorCode,
)
from src.runtime.providers import RuntimeProviderError
from src.runtime.store import RuntimeStoreRepository


class TestExecuteRuntimeToolDisabled:
    async def test_empty_provider_key_returns_disabled(self):
        with patch("src.runtime.client.settings") as mock_settings:
            mock_settings.runtime_provider_key = ""
            result = await execute_runtime_tool(
                RuntimeToolCall(tool_name="list_executions", arguments={}), project_scope=None
            )
        assert result.success is False
        assert result.error.code == RuntimeToolErrorCode.DISABLED


class TestExecuteRuntimeToolUnknownProvider:
    async def test_unknown_provider_key_returns_unavailable(self):
        with patch("src.runtime.client.settings") as mock_settings:
            mock_settings.runtime_provider_key = "not_configured"
            result = await execute_runtime_tool(
                RuntimeToolCall(tool_name="list_executions", arguments={}), project_scope=None
            )
        assert result.success is False
        assert result.error.code == RuntimeToolErrorCode.UNAVAILABLE


class TestExecuteRuntimeToolValidation:
    async def test_unknown_tool_name_returns_unknown_tool_error(self):
        with patch("src.runtime.client.settings") as mock_settings:
            mock_settings.runtime_provider_key = "mock"
            mock_settings.runtime_tool_timeout = 10.0
            mock_settings.runtime_tool_result_max_chars = 12000
            result = await execute_runtime_tool(
                RuntimeToolCall(tool_name="not_a_real_tool", arguments={}), project_scope=None
            )
        assert result.success is False
        assert result.error.code == RuntimeToolErrorCode.UNKNOWN_TOOL

    async def test_malformed_arguments_return_validation_error(self):
        with patch("src.runtime.client.settings") as mock_settings:
            mock_settings.runtime_provider_key = "mock"
            mock_settings.runtime_tool_timeout = 10.0
            mock_settings.runtime_tool_result_max_chars = 12000
            result = await execute_runtime_tool(
                RuntimeToolCall(tool_name="get_execution_details", arguments={}), project_scope=None
            )
        assert result.success is False
        assert result.error.code == RuntimeToolErrorCode.VALIDATION_ERROR


class TestExecuteRuntimeToolSuccess:
    async def test_mock_provider_list_executions_succeeds(self):
        with patch("src.runtime.client.settings") as mock_settings:
            mock_settings.runtime_provider_key = "mock"
            mock_settings.runtime_tool_timeout = 10.0
            mock_settings.runtime_tool_result_max_chars = 12000
            result = await execute_runtime_tool(
                RuntimeToolCall(tool_name="list_executions", arguments={}), project_scope=None
            )
        assert result.success is True
        assert result.error is None
        assert isinstance(result.data, list)


class TestExecuteRuntimeToolProviderErrorsAndTimeouts:
    async def test_provider_error_is_returned_as_structured_failure(self):
        with patch("src.runtime.client.settings") as mock_settings:
            mock_settings.runtime_provider_key = "mock"
            mock_settings.runtime_tool_timeout = 10.0
            mock_settings.runtime_tool_result_max_chars = 12000
            result = await execute_runtime_tool(
                RuntimeToolCall(tool_name="get_execution_details", arguments={"execution_id": "does-not-exist"}),
                project_scope=None,
            )
        assert result.success is False
        assert result.error.code == RuntimeToolErrorCode.PROVIDER_ERROR

    async def test_provider_timeout_is_returned_as_structured_failure(self):
        async def _slow_list_executions(params, context=None):
            await asyncio.sleep(0.05)
            return []

        with patch("src.runtime.client.settings") as mock_settings, patch(
            "src.runtime.providers.mock.provider.list_executions", side_effect=_slow_list_executions
        ):
            mock_settings.runtime_provider_key = "mock"
            mock_settings.runtime_tool_timeout = 0.01
            mock_settings.runtime_tool_result_max_chars = 12000
            result = await execute_runtime_tool(
                RuntimeToolCall(tool_name="list_executions", arguments={}), project_scope=None
            )
        assert result.success is False
        assert result.error.code == RuntimeToolErrorCode.TIMEOUT


class TestExecuteRuntimeToolResultSizeLimiting:
    async def test_oversized_result_is_truncated(self):
        with patch("src.runtime.client.settings") as mock_settings:
            mock_settings.runtime_provider_key = "mock"
            mock_settings.runtime_tool_timeout = 10.0
            mock_settings.runtime_tool_result_max_chars = 10
            result = await execute_runtime_tool(
                RuntimeToolCall(tool_name="list_executions", arguments={}), project_scope=None
            )
        assert result.success is True
        assert result.truncated is True


class TestExecuteRuntimeToolTalendProvider:
    """The Talend TMC provider key resolves to the real adapter module. Its
    HTTP behavior is unit-tested separately; here we only prove the client
    routes to it and maps its outcomes to the structured envelope."""

    async def test_talend_provider_resolves_and_succeeds(self):
        execution = RuntimeExecution(
            execution_id="exec-1",
            job_name="nightly_sync",
            project_name="demo",
            state=RuntimeExecutionState.SUCCESS,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        with patch("src.runtime.client.settings") as mock_settings, patch(
            "src.runtime.providers.talend_tmc.provider.list_executions",
            new=AsyncMock(return_value=[execution]),
        ):
            mock_settings.runtime_provider_key = "talend_tmc"
            mock_settings.runtime_tool_timeout = 10.0
            mock_settings.runtime_tool_result_max_chars = 12000
            result = await execute_runtime_tool(
                RuntimeToolCall(tool_name="list_executions", arguments={}), project_scope=None
            )
        assert result.success is True
        assert result.error is None
        assert isinstance(result.data, list)

    async def test_talend_provider_error_maps_to_provider_error(self):
        with patch("src.runtime.client.settings") as mock_settings, patch(
            "src.runtime.providers.talend_tmc.provider.list_executions",
            new=AsyncMock(side_effect=RuntimeProviderError("upstream HTTP 500")),
        ):
            mock_settings.runtime_provider_key = "talend_tmc"
            mock_settings.runtime_tool_timeout = 10.0
            mock_settings.runtime_tool_result_max_chars = 12000
            result = await execute_runtime_tool(
                RuntimeToolCall(tool_name="list_executions", arguments={}), project_scope=None
            )
        assert result.success is False
        assert result.error.code == RuntimeToolErrorCode.PROVIDER_ERROR


class TestExecuteRuntimeToolStoredProviderRBAC:
    """End-to-end RBAC enforcement through the runtime boundary with the stored
    provider. The authenticated project scope must constrain reads, and LLM tool
    arguments must never widen it."""

    @staticmethod
    async def _seed_two_projects(session):
        repo = RuntimeStoreRepository(session)
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        await repo.upsert_execution(
            "project-a",
            RuntimeExecution(
                execution_id="a1",
                job_name="nightly_sync",
                project_name="demo",
                state=RuntimeExecutionState.SUCCESS,
                started_at=t0,
            ),
            "mock",
            t0,
        )
        await repo.upsert_execution(
            "project-b",
            RuntimeExecution(
                execution_id="b1",
                job_name="nightly_sync",
                project_name="secret_proj",
                state=RuntimeExecutionState.SUCCESS,
                started_at=t0,
            ),
            "mock",
            t0,
        )

    @staticmethod
    def _stored_settings(mock_settings):
        mock_settings.runtime_provider_key = "stored"
        mock_settings.runtime_tool_timeout = 10.0
        mock_settings.runtime_tool_result_max_chars = 12000

    async def test_scope_excludes_other_project(self, db_session):
        await self._seed_two_projects(db_session)
        with patch("src.runtime.client.settings") as mock_settings:
            self._stored_settings(mock_settings)
            result = await execute_runtime_tool(
                RuntimeToolCall(tool_name="list_executions", arguments={}),
                project_scope=["project-a"],
                db=db_session,
            )
        assert result.success is True
        assert {e["execution_id"] for e in result.data} == {"a1"}

    async def test_tool_arguments_cannot_widen_scope(self, db_session):
        await self._seed_two_projects(db_session)
        with patch("src.runtime.client.settings") as mock_settings:
            self._stored_settings(mock_settings)
            # The LLM names project-b's descriptive project_name, but scope is project-a.
            result = await execute_runtime_tool(
                RuntimeToolCall(tool_name="list_executions", arguments={"project_name": "secret_proj"}),
                project_scope=["project-a"],
                db=db_session,
            )
        assert result.success is True
        assert result.data == []  # cannot reach project-b through tool arguments

    async def test_empty_scope_returns_no_rows(self, db_session):
        await self._seed_two_projects(db_session)
        with patch("src.runtime.client.settings") as mock_settings:
            self._stored_settings(mock_settings)
            result = await execute_runtime_tool(
                RuntimeToolCall(tool_name="list_executions", arguments={}),
                project_scope=[],
                db=db_session,
            )
        assert result.success is True
        assert result.data == []

    async def test_none_scope_reads_all_projects(self, db_session):
        await self._seed_two_projects(db_session)
        with patch("src.runtime.client.settings") as mock_settings:
            self._stored_settings(mock_settings)
            result = await execute_runtime_tool(
                RuntimeToolCall(tool_name="list_executions", arguments={}),
                project_scope=None,
                db=db_session,
            )
        assert result.success is True
        assert {e["execution_id"] for e in result.data} == {"a1", "b1"}

    async def test_missing_db_maps_to_provider_error(self):
        with patch("src.runtime.client.settings") as mock_settings:
            self._stored_settings(mock_settings)
            result = await execute_runtime_tool(
                RuntimeToolCall(tool_name="list_executions", arguments={}),
                project_scope=None,
                db=None,
            )
        assert result.success is False
        assert result.error.code == RuntimeToolErrorCode.PROVIDER_ERROR


class TestSanitizeAndBound:
    def test_truncates_oversized_payload(self):
        _, truncated = _sanitize_and_bound({"a": "b" * 50}, max_chars=10)
        assert truncated is True

    def test_does_not_truncate_payload_within_bound(self):
        _, truncated = _sanitize_and_bound({"a": "b"}, max_chars=1000)
        assert truncated is False

    def test_redacts_credential_looking_keys(self):
        redacted = _redact({"token": "sekrit", "nested": {"password": "hunter2", "ok": "fine"}})
        assert redacted["token"] == "***REDACTED***"
        assert redacted["nested"]["password"] == "***REDACTED***"
        assert redacted["nested"]["ok"] == "fine"
