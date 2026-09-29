"""Tests for the topology dispatcher (src/topology/client.py)."""

from __future__ import annotations

import httpx
import pytest

from src.runtime.models import RuntimeToolCall, RuntimeToolErrorCode
from src.topology import provider as provider_module
from src.topology.client import (
    TOPOLOGY_TOOL_NAMES,
    execute_topology_tool,
    is_topology_tool,
    list_topology_tools,
)
from tests.test_topology.conftest import RecordingTransport, json_transport, make_provider


@pytest.fixture
def installed(monkeypatch):
    """Install a stub provider and hand the test its transport."""

    def install(transport: httpx.AsyncBaseTransport) -> RecordingTransport:
        provider = make_provider(transport)
        monkeypatch.setattr(provider_module, "default_provider", lambda: provider)
        return transport

    return install


class TestCatalogue:
    def test_allowlist_matches_the_published_definitions(self) -> None:
        definitions = list_topology_tools()

        assert {definition.name for definition in definitions} == TOPOLOGY_TOOL_NAMES
        assert len(definitions) == 7

    def test_routing_predicate_only_claims_topology_tools(self) -> None:
        assert is_topology_tool("get_topology_overview")
        assert not is_topology_tool("list_executions")
        assert not is_topology_tool("get_topology_job")

    def test_definitions_tell_the_model_a_name_is_enough(self) -> None:
        """The four-iteration budget cannot afford a needless find -> get pair."""
        definitions = {definition.name: definition for definition in list_topology_tools()}

        assert "NAME" in definitions["get_topology_node"].description
        assert (
            "do not call find_topology_node first" in definitions["get_topology_node"].description
        )
        assert "NAME" in definitions["list_topology_dependencies"].description
        assert "EVERY candidate" in definitions["find_topology_node"].description

    def test_input_schemas_expose_the_hop_bound(self) -> None:
        schema = next(
            definition.input_schema
            for definition in list_topology_tools()
            if definition.name == "list_topology_dependencies"
        )

        assert schema["properties"]["hops"]["maximum"] == 2
        assert schema["properties"]["hops"]["minimum"] == 1


class TestDispatch:
    async def test_successful_call_returns_the_pinned_payload_shape(
        self, installed, talend_demo
    ) -> None:
        installed(json_transport(talend_demo))

        result = await execute_topology_tool(
            RuntimeToolCall(
                tool_name="get_topology_overview", arguments={"project_id": "talend_demo"}
            ),
            project_scope=["talend_demo"],
            access_token="user-jwt",
        )

        assert result.success is True
        assert set(result.data) == {"content", "targets"}
        assert result.data["content"]["project_id"] == "talend_demo"
        assert result.data["targets"][0]["level"]["kind"] == "integration"
        assert result.error is None

    async def test_unknown_tool_is_rejected_before_any_request(self, installed, talend_demo) -> None:
        transport = installed(json_transport(talend_demo))

        result = await execute_topology_tool(
            RuntimeToolCall(tool_name="drop_topology", arguments={"project_id": "talend_demo"}),
            project_scope=None,
            access_token=None,
        )

        assert result.error.code is RuntimeToolErrorCode.UNKNOWN_TOOL
        assert transport.call_count == 0

    async def test_invalid_arguments_are_rejected_before_any_request(
        self, installed, talend_demo
    ) -> None:
        transport = installed(json_transport(talend_demo))

        result = await execute_topology_tool(
            RuntimeToolCall(
                tool_name="list_topology_dependencies",
                arguments={"project_id": "talend_demo", "name_or_id": "Log", "hops": 9},
            ),
            project_scope=None,
            access_token=None,
        )

        assert result.error.code is RuntimeToolErrorCode.VALIDATION_ERROR
        assert transport.call_count == 0

    async def test_missing_required_argument_is_a_validation_error(
        self, installed, talend_demo
    ) -> None:
        installed(json_transport(talend_demo))

        result = await execute_topology_tool(
            RuntimeToolCall(tool_name="get_topology_node", arguments={"project_id": "talend_demo"}),
            project_scope=None,
            access_token=None,
        )

        assert result.error.code is RuntimeToolErrorCode.VALIDATION_ERROR


class TestProjectScope:
    async def test_project_outside_scope_is_refused_before_any_request(
        self, installed, talend_demo
    ) -> None:
        transport = installed(json_transport(talend_demo))

        result = await execute_topology_tool(
            RuntimeToolCall(tool_name="get_topology_overview", arguments={"project_id": "secret"}),
            project_scope=["talend_demo"],
            access_token="user-jwt",
        )

        assert result.success is False
        assert result.error.code is RuntimeToolErrorCode.UNAVAILABLE
        # The point of the local check: no upstream call is even attempted, so
        # dropping the token forward could never widen access on its own.
        assert transport.call_count == 0

    async def test_empty_scope_grants_nothing(self, installed, talend_demo) -> None:
        transport = installed(json_transport(talend_demo))

        result = await execute_topology_tool(
            RuntimeToolCall(
                tool_name="get_topology_overview", arguments={"project_id": "talend_demo"}
            ),
            project_scope=[],
            access_token="user-jwt",
        )

        assert result.success is False
        assert transport.call_count == 0

    async def test_none_scope_is_unrestricted(self, installed, talend_demo) -> None:
        installed(json_transport(talend_demo))

        result = await execute_topology_tool(
            RuntimeToolCall(
                tool_name="get_topology_overview", arguments={"project_id": "talend_demo"}
            ),
            project_scope=None,
            access_token="user-jwt",
        )

        assert result.success is True

    async def test_refusal_message_does_not_reveal_whether_the_project_exists(
        self, installed, talend_demo
    ) -> None:
        installed(json_transport(talend_demo))

        refused = await execute_topology_tool(
            RuntimeToolCall(tool_name="get_topology_overview", arguments={"project_id": "secret"}),
            project_scope=["talend_demo"],
            access_token=None,
        )

        assert refused.error.message == "No topology data is available for project 'secret'."


class TestIdentityForwarding:
    async def test_access_token_reaches_mcp_as_a_cookie(self, installed, talend_demo) -> None:
        transport = installed(json_transport(talend_demo))

        await execute_topology_tool(
            RuntimeToolCall(
                tool_name="get_topology_overview", arguments={"project_id": "talend_demo"}
            ),
            project_scope=None,
            access_token="user-jwt",
        )

        assert transport.requests[0].headers.get("cookie") == "access_token=user-jwt"

    async def test_a_token_in_the_arguments_is_never_used(self, installed, talend_demo) -> None:
        """`access_token` is keyword-only; a model cannot supply its own."""
        transport = installed(json_transport(talend_demo))

        result = await execute_topology_tool(
            RuntimeToolCall(
                tool_name="get_topology_overview",
                arguments={"project_id": "talend_demo", "access_token": "attacker-jwt"},
            ),
            project_scope=None,
            access_token=None,
        )

        assert result.success is True
        assert transport.requests[0].headers.get("cookie") is None
        assert "attacker-jwt" not in str(transport.requests[0].url)

    async def test_a_scope_in_the_arguments_is_never_used(self, installed, talend_demo) -> None:
        transport = installed(json_transport(talend_demo))

        result = await execute_topology_tool(
            RuntimeToolCall(
                tool_name="get_topology_overview",
                arguments={"project_id": "secret", "project_scope": ["secret"]},
            ),
            project_scope=["talend_demo"],
            access_token=None,
        )

        assert result.success is False
        assert transport.call_count == 0


class TestErrorMapping:
    async def test_missing_artifact_is_unavailable_and_asks_for_a_rescan(self, installed) -> None:
        installed(RecordingTransport(lambda request: httpx.Response(404, json={"detail": "no"})))

        result = await execute_topology_tool(
            RuntimeToolCall(
                tool_name="get_topology_overview", arguments={"project_id": "never-scanned"}
            ),
            project_scope=None,
            access_token=None,
        )

        assert result.error.code is RuntimeToolErrorCode.UNAVAILABLE
        assert "scan" in result.error.message

    async def test_upstream_failure_is_a_provider_error_without_the_body(self, installed) -> None:
        installed(
            RecordingTransport(
                lambda request: httpx.Response(500, json={"detail": "admin:hunter2@db.internal"})
            )
        )

        result = await execute_topology_tool(
            RuntimeToolCall(
                tool_name="get_topology_overview", arguments={"project_id": "talend_demo"}
            ),
            project_scope=None,
            access_token=None,
        )

        assert result.error.code is RuntimeToolErrorCode.PROVIDER_ERROR
        assert "hunter2" not in result.error.message

    async def test_malformed_artifact_is_a_provider_error_distinct_from_unavailable(
        self, installed
    ) -> None:
        installed(json_transport({"systems": "not-a-list"}))

        result = await execute_topology_tool(
            RuntimeToolCall(
                tool_name="get_topology_overview", arguments={"project_id": "talend_demo"}
            ),
            project_scope=None,
            access_token=None,
        )

        assert result.error.code is RuntimeToolErrorCode.PROVIDER_ERROR
        assert "rescan" in result.error.message

    async def test_upstream_timeout_is_a_timeout(self, installed) -> None:
        def responder(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        installed(RecordingTransport(responder))

        result = await execute_topology_tool(
            RuntimeToolCall(
                tool_name="get_topology_overview", arguments={"project_id": "talend_demo"}
            ),
            project_scope=None,
            access_token=None,
        )

        assert result.error.code is RuntimeToolErrorCode.TIMEOUT

    async def test_connection_failure_is_a_provider_error(self, installed) -> None:
        def responder(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        installed(RecordingTransport(responder))

        result = await execute_topology_tool(
            RuntimeToolCall(
                tool_name="get_topology_overview", arguments={"project_id": "talend_demo"}
            ),
            project_scope=None,
            access_token=None,
        )

        assert result.error.code is RuntimeToolErrorCode.PROVIDER_ERROR
        assert result.error.message == "Topology provider could not be reached."


class TestResultBounds:
    async def test_result_is_marked_truncated_when_a_cap_was_hit(
        self, installed, workorders_demo
    ) -> None:
        installed(json_transport(workorders_demo))

        result = await execute_topology_tool(
            RuntimeToolCall(
                tool_name="get_topology_node",
                arguments={"project_id": "workorders_demo", "name_or_id": "AssetSys"},
            ),
            project_scope=["workorders_demo"],
            access_token=None,
        )

        assert result.success is True
        assert result.truncated is True
        assert result.data["content"]["relationships_truncated"] is True

    async def test_size_backstop_drops_collections_instead_of_the_whole_answer(
        self, installed, monkeypatch, talend_demo
    ) -> None:
        installed(json_transport(talend_demo))
        monkeypatch.setattr("src.config.settings.runtime_tool_result_max_chars", 400)

        result = await execute_topology_tool(
            RuntimeToolCall(
                tool_name="get_topology_overview", arguments={"project_id": "talend_demo"}
            ),
            project_scope=None,
            access_token=None,
        )

        assert result.truncated is True
        assert result.data["content"]["truncated"] is True
        # Scalars survive so the model can still answer "which project, which view".
        assert result.data["content"]["project_id"] == "talend_demo"
        assert result.data["content"]["active_view"] == "business"
        assert "scanner_facts" in result.data["content"]["omitted_fields"]

    async def test_untruncated_result_is_not_flagged(self, installed, talend_demo) -> None:
        installed(json_transport(talend_demo))

        result = await execute_topology_tool(
            RuntimeToolCall(
                tool_name="get_topology_node",
                arguments={"project_id": "talend_demo", "name_or_id": "InspectHub"},
            ),
            project_scope=None,
            access_token=None,
        )

        assert result.truncated is False


class TestRuntimeIndependence:
    async def test_topology_works_with_live_runtime_disabled(
        self, installed, monkeypatch, talend_demo
    ) -> None:
        """Topology is published artifact data, not runtime data."""
        installed(json_transport(talend_demo))
        monkeypatch.setattr("src.config.settings.runtime_provider_key", "")

        result = await execute_topology_tool(
            RuntimeToolCall(
                tool_name="get_topology_overview", arguments={"project_id": "talend_demo"}
            ),
            project_scope=None,
            access_token=None,
        )

        assert result.success is True


class TestFieldLineageDispatch:
    """The seventh operation, through the same dispatcher the harness calls.

    Covered here as well as at tool level because the harness capability mirrors
    this seam: a name that does not route, or an argument set that validates
    differently on the two sides, fails only at run time in a real turn.
    """

    async def test_the_name_routes_to_this_dispatcher(self) -> None:
        assert is_topology_tool("get_field_lineage")
        assert "get_field_lineage" in TOPOLOGY_TOOL_NAMES

    async def test_a_scopeless_call_is_a_validation_error_not_a_read(self, installed) -> None:
        """An unscoped lineage read must never reach the provider."""
        transport = installed(json_transport({}))

        result = await execute_topology_tool(
            RuntimeToolCall(tool_name="get_field_lineage", arguments={"project_id": "workorders_demo"}),
            project_scope=None,
            access_token=None,
        )

        assert result.success is False
        assert result.error is not None
        assert result.error.code is RuntimeToolErrorCode.VALIDATION_ERROR
        assert transport.call_count == 0

    async def test_an_out_of_scope_project_is_refused_before_any_read(self, installed) -> None:
        transport = installed(json_transport({}))

        result = await execute_topology_tool(
            RuntimeToolCall(
                tool_name="get_field_lineage",
                arguments={"project_id": "workorders_demo", "job": "anything"},
            ),
            project_scope=["other-project"],
            access_token=None,
        )

        assert result.success is False
        assert result.error is not None
        assert result.error.code is RuntimeToolErrorCode.UNAVAILABLE
        assert transport.call_count == 0

    async def test_a_successful_read_returns_the_rollup_through_the_envelope(
        self, installed, rhm2sf_lineage
    ) -> None:
        from tests.test_topology.conftest import (
            RHM2SF_LINEAGE_JOB,
            field_lineage_path,
            lineage_responder,
        )
        from tests.test_topology.conftest import RoutedTransport

        installed(
            RoutedTransport({field_lineage_path("workorders_demo"): lineage_responder(rhm2sf_lineage)})
        )

        result = await execute_topology_tool(
            RuntimeToolCall(
                tool_name="get_field_lineage",
                arguments={"project_id": "workorders_demo", "job": RHM2SF_LINEAGE_JOB},
            ),
            project_scope=None,
            access_token=None,
        )

        assert result.success is True
        assert result.data is not None
        rollup = result.data["content"]["rollup"]
        assert rollup["field_mappings_total"] == 85
        assert rollup["runtime_determined_field_mappings"] == 12
        # Paged, so the envelope must say the result was clipped.
        assert result.truncated is True

    async def test_a_missing_lineage_artifact_surfaces_as_unavailable(self, installed) -> None:
        """Not an error the model should apologise for: a rescan is the answer."""
        from tests.test_topology.conftest import (
            LINEAGE_NO_ARTIFACT_DETAIL,
            lineage_404_transport,
        )

        installed(lineage_404_transport(LINEAGE_NO_ARTIFACT_DETAIL))

        result = await execute_topology_tool(
            RuntimeToolCall(
                tool_name="get_field_lineage",
                arguments={"project_id": "workorders_demo", "job": "anything"},
            ),
            project_scope=None,
            access_token=None,
        )

        assert result.success is False
        assert result.error is not None
        assert result.error.code is RuntimeToolErrorCode.UNAVAILABLE
        assert "needs a scan" in result.error.message
