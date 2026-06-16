"""Unit tests for the Talend TMC runtime provider adapter.

All HTTP traffic is served by httpx.MockTransport — no real network. The wire
format asserted here mirrors the Slice 5 plan's assumed contract; Slice 6
reconciles it against the live TMC API.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.runtime.models import (
    DetectFailuresInput,
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    ListExecutionsInput,
    RuntimeExecutionState,
    RuntimeLogSeverity,
)
from src.runtime.providers import RuntimeProviderError
from src.runtime.providers.talend_tmc import TalendTmcRuntimeProvider

_TOKEN = "super-secret-talend-token-value"


@pytest.fixture
def tmc(tmp_path):
    """Patches the adapter's settings for the test and provides a builder that
    wires a fresh provider to an httpx.MockTransport handler. The patch is
    context-managed, so it is always undone after the test."""
    token_path = tmp_path / "talend_token"
    token_path.write_text(f"  {_TOKEN}\n", encoding="utf-8")

    s = MagicMock()
    s.talend_tmc_base_url = "http://talend-tmc-runtime:9000"
    s.talend_tmc_api_token_file = str(token_path)
    s.talend_tmc_request_timeout = 10.0

    with patch("src.runtime.providers.talend_tmc.settings", s):
        yield SimpleNamespace(
            settings=s,
            token_path=token_path,
            tmp_path=tmp_path,
            provider=lambda handler: TalendTmcRuntimeProvider(transport=httpx.MockTransport(handler)),
        )


class TestOperationMapping:
    async def test_each_generic_method_hits_its_upstream_operation(self, tmc):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            if request.url.path == "/get_execution_details":
                return httpx.Response(
                    200,
                    json={
                        "execution": {
                            "id": "e1",
                            "job_name": "j",
                            "status": "success",
                            "start_time": "2026-01-01T00:00:00Z",
                        }
                    },
                )
            if request.url.path == "/detect_execution_failures":
                return httpx.Response(200, json={"failures": []})
            if request.url.path == "/get_execution_logs":
                return httpx.Response(200, json={"logs": []})
            return httpx.Response(200, json={"executions": []})

        provider = tmc.provider(handler)
        await provider.list_executions(ListExecutionsInput())
        await provider.get_execution_details(GetExecutionDetailsInput(execution_id="e1"))
        await provider.detect_failures(DetectFailuresInput())
        await provider.get_execution_logs(GetExecutionLogsInput(execution_id="e1"))

        assert seen == [
            "/get_task_executions",
            "/get_execution_details",
            "/detect_execution_failures",
            "/get_execution_logs",
        ]

    async def test_bearer_token_is_sent(self, tmc):
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("authorization", "")
            return httpx.Response(200, json={"executions": []})

        await tmc.provider(handler).list_executions(ListExecutionsInput())
        assert captured["auth"] == f"Bearer {_TOKEN}"


class TestNormalization:
    async def test_executions_are_normalized(self, tmc):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "executions": [
                        {
                            "id": "exec-1",
                            "job_name": "nightly_sync",
                            "project_name": "demo",
                            "status": "FAILED",
                            "start_time": "2026-01-01T00:00:00Z",
                            "end_time": "2026-01-01T00:01:00Z",
                            "duration_ms": 60000,
                        }
                    ]
                },
            )

        result = await tmc.provider(handler).list_executions(ListExecutionsInput())
        assert len(result) == 1
        assert result[0].execution_id == "exec-1"
        assert result[0].state == RuntimeExecutionState.FAILED
        assert result[0].ended_at == datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
        assert result[0].duration_ms == 60000

    async def test_unknown_status_normalizes_to_unknown(self, tmc):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "execution": {
                        "id": "e1",
                        "job_name": "j",
                        "status": "weird_state",
                        "start_time": "2026-01-01T00:00:00Z",
                    }
                },
            )

        result = await tmc.provider(handler).get_execution_details(
            GetExecutionDetailsInput(execution_id="e1")
        )
        assert result.state == RuntimeExecutionState.UNKNOWN

    async def test_failures_are_normalized(self, tmc):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "failures": [
                        {
                            "execution_id": "exec-2",
                            "job_name": "nightly_sync",
                            "failed_at": "2026-01-01T00:00:00Z",
                            "error": "source db timeout",
                        }
                    ]
                },
            )

        result = await tmc.provider(handler).detect_failures(DetectFailuresInput())
        assert result[0].execution_id == "exec-2"
        assert result[0].error_summary == "source db timeout"

    async def test_logs_are_normalized(self, tmc):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "logs": [
                        {
                            "execution_id": "exec-1",
                            "timestamp": "2026-01-01T00:00:00Z",
                            "level": "ERROR",
                            "message": "boom",
                        }
                    ]
                },
            )

        result = await tmc.provider(handler).get_execution_logs(
            GetExecutionLogsInput(execution_id="exec-1")
        )
        assert result[0].severity == RuntimeLogSeverity.ERROR
        assert result[0].message == "boom"


class TestConfigFailures:
    async def test_missing_token_file_is_provider_error(self, tmc):
        tmc.settings.talend_tmc_api_token_file = str(tmc.tmp_path / "does_not_exist")
        with pytest.raises(RuntimeProviderError):
            await tmc.provider(lambda r: httpx.Response(200, json={"executions": []})).list_executions(
                ListExecutionsInput()
            )

    async def test_empty_token_file_is_provider_error(self, tmc):
        empty = tmc.tmp_path / "empty_token"
        empty.write_text("   \n", encoding="utf-8")
        tmc.settings.talend_tmc_api_token_file = str(empty)
        with pytest.raises(RuntimeProviderError):
            await tmc.provider(lambda r: httpx.Response(200, json={"executions": []})).list_executions(
                ListExecutionsInput()
            )

    async def test_missing_base_url_is_provider_error(self, tmc):
        tmc.settings.talend_tmc_base_url = ""
        with pytest.raises(RuntimeProviderError):
            await tmc.provider(lambda r: httpx.Response(200, json={"executions": []})).list_executions(
                ListExecutionsInput()
            )


class TestUpstreamFailures:
    @pytest.mark.parametrize("status", [401, 403, 500])
    async def test_http_error_status_is_provider_error(self, tmc, status):
        with pytest.raises(RuntimeProviderError):
            await tmc.provider(lambda r: httpx.Response(status, json={"detail": "nope"})).list_executions(
                ListExecutionsInput()
            )

    async def test_timeout_is_provider_error(self, tmc):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out", request=request)

        with pytest.raises(RuntimeProviderError):
            await tmc.provider(handler).list_executions(ListExecutionsInput())

    async def test_malformed_payload_is_provider_error(self, tmc):
        # executions list present but an entry is missing the required id field
        with pytest.raises(RuntimeProviderError):
            await tmc.provider(
                lambda r: httpx.Response(200, json={"executions": [{"job_name": "j"}]})
            ).list_executions(ListExecutionsInput())

    async def test_missing_list_field_is_provider_error(self, tmc):
        with pytest.raises(RuntimeProviderError):
            await tmc.provider(lambda r: httpx.Response(200, json={"wrong_key": []})).list_executions(
                ListExecutionsInput()
            )


class TestSecretHygiene:
    async def test_token_value_never_appears_in_error_messages(self, tmc):
        with pytest.raises(RuntimeProviderError) as exc_info:
            await tmc.provider(lambda r: httpx.Response(500, json={"detail": "server error"})).list_executions(
                ListExecutionsInput()
            )
        assert _TOKEN not in str(exc_info.value)
