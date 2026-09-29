"""Tests for the landscape artifact provider (src/topology/provider.py)."""

from __future__ import annotations

import httpx
import pytest

from src.topology.provider import (
    TopologyArtifactUnavailableError,
    TopologyDesignUnavailableError,
    TopologyMappingUnavailableError,
    TopologyPayloadError,
    TopologyProviderError,
    TopologyProviderTimeoutError,
    default_provider,
)
from tests.test_topology.conftest import (
    MAPPING_COMPONENT,
    MAPPING_JOB,
    QUINTIQ_JOB,
    RICHEST_JOB,
    RecordingTransport,
    RoutedTransport,
    component_graph_path,
    job_graph_path,
    json_transport,
    landscape_path,
    make_provider,
    project_graph_path,
)


class TestIdentityForwarding:
    async def test_forwards_access_token_cookie_and_reads_the_landscape_path(
        self, talend_demo
    ) -> None:
        transport = json_transport(talend_demo)
        provider = make_provider(transport)

        await provider.get_landscape(project_id="talend_demo", access_token="user-jwt")

        request = transport.requests[0]
        assert request.url.path == "/mcp/projects/talend_demo/landscape"
        assert request.method == "GET"
        # MCP performs the authoritative project-access check from this cookie.
        # If this assertion ever has to be deleted, access control moved.
        assert request.headers.get("cookie") == "access_token=user-jwt"

    async def test_sends_no_cookie_when_there_is_no_token(self, talend_demo) -> None:
        transport = json_transport(talend_demo)
        provider = make_provider(transport)

        await provider.get_landscape(project_id="talend_demo")

        assert transport.requests[0].headers.get("cookie") is None

    async def test_never_puts_the_token_in_the_url_or_body(self, talend_demo) -> None:
        transport = json_transport(talend_demo)
        provider = make_provider(transport)

        await provider.get_landscape(project_id="talend_demo", access_token="user-jwt")

        request = transport.requests[0]
        assert "user-jwt" not in str(request.url)
        assert request.read() == b""


class TestOutcomes:
    async def test_upstream_404_is_a_typed_unavailability_not_a_provider_error(self) -> None:
        transport = RecordingTransport(
            lambda request: httpx.Response(404, json={"detail": "No landscape data"})
        )
        provider = make_provider(transport)

        with pytest.raises(TopologyArtifactUnavailableError) as caught:
            await provider.get_landscape(project_id="never-scanned")

        # Sibling classes on purpose: a handler for provider failures must not be
        # able to absorb "this project needs a rescan".
        assert not isinstance(caught.value, TopologyProviderError)
        assert "never-scanned" in caught.value.safe_message
        assert "scan" in caught.value.safe_message

    async def test_upstream_500_is_a_provider_error_without_the_response_body(self) -> None:
        transport = RecordingTransport(
            lambda request: httpx.Response(
                500, json={"detail": "psql://admin:hunter2@db.internal/intodata"}
            )
        )
        provider = make_provider(transport)

        with pytest.raises(TopologyProviderError) as caught:
            await provider.get_landscape(project_id="talend_demo")

        assert caught.value.safe_message == "Topology provider returned HTTP 500."
        assert "hunter2" not in caught.value.safe_message
        assert "db.internal" not in caught.value.safe_message

    async def test_non_json_body_is_a_payload_error_with_nothing_leaked(self) -> None:
        transport = RecordingTransport(
            lambda request: httpx.Response(200, text="<html>admin:hunter2@db.internal</html>")
        )
        provider = make_provider(transport)

        with pytest.raises(TopologyPayloadError) as caught:
            await provider.get_landscape(project_id="talend_demo")

        assert caught.value.safe_message == "Topology provider returned an invalid response."
        assert "hunter2" not in caught.value.safe_message

    async def test_json_array_is_a_payload_error(self) -> None:
        transport = RecordingTransport(lambda request: httpx.Response(200, json=[1, 2, 3]))
        provider = make_provider(transport)

        with pytest.raises(TopologyPayloadError):
            await provider.get_landscape(project_id="talend_demo")

    async def test_structurally_invalid_artifact_is_a_payload_error(self) -> None:
        # `systems` is required to be a list of objects with an id; a scalar
        # there means the artifact itself is broken, not that MCP is down.
        transport = json_transport({"systems": "not-a-list"})
        provider = make_provider(transport)

        with pytest.raises(TopologyPayloadError) as caught:
            await provider.get_landscape(project_id="talend_demo")

        assert "rescan" in caught.value.safe_message

    async def test_timeout_is_its_own_outcome(self) -> None:
        def responder(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        provider = make_provider(RecordingTransport(responder))

        with pytest.raises(TopologyProviderTimeoutError) as caught:
            await provider.get_landscape(project_id="talend_demo")

        assert not isinstance(caught.value, TopologyProviderError)
        assert "timed out after 5s" in caught.value.safe_message

    async def test_connection_failure_is_a_provider_error(self) -> None:
        def responder(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        provider = make_provider(RecordingTransport(responder))

        with pytest.raises(TopologyProviderError) as caught:
            await provider.get_landscape(project_id="talend_demo")

        assert caught.value.safe_message == "Topology provider could not be reached."

    async def test_unconfigured_base_url_never_issues_a_request(self, talend_demo) -> None:
        transport = json_transport(talend_demo)
        provider = make_provider(transport)
        provider._base_url = ""

        with pytest.raises(TopologyProviderError, match="not configured"):
            await provider.get_landscape(project_id="talend_demo")
        assert transport.call_count == 0

    async def test_traversal_project_id_is_rejected_before_any_request(self, talend_demo) -> None:
        transport = json_transport(talend_demo)
        provider = make_provider(transport)

        with pytest.raises(TopologyProviderError, match="Invalid project id"):
            await provider.get_landscape(project_id="../../etc/passwd")
        assert transport.call_count == 0


class TestCaching:
    async def test_second_read_inside_the_ttl_is_served_from_cache(self, talend_demo) -> None:
        transport = json_transport(talend_demo)
        provider = make_provider(transport, cache_ttl_seconds=60.0)

        first = await provider.get_landscape(project_id="talend_demo")
        second = await provider.get_landscape(project_id="talend_demo")

        assert transport.call_count == 1
        # The parsed document, not its wrapper: four artifact families now share
        # one cache, so the wrapper is a per-call value object and the guarantee
        # that matters is that a multi-megabyte document is validated once.
        assert second.landscape is first.landscape

    async def test_each_project_is_cached_separately(self, talend_demo, workorders_demo) -> None:
        def responder(request: httpx.Request) -> httpx.Response:
            payload = workorders_demo if "workorders_demo" in request.url.path else talend_demo
            return httpx.Response(200, json=payload)

        transport = RecordingTransport(responder)
        provider = make_provider(transport)

        await provider.get_landscape(project_id="talend_demo")
        await provider.get_landscape(project_id="workorders_demo")
        await provider.get_landscape(project_id="talend_demo")

        assert transport.call_count == 2

    async def test_unchanged_fingerprint_reuses_the_parsed_artifact(self, talend_demo) -> None:
        transport = json_transport(talend_demo)
        provider = make_provider(transport, cache_ttl_seconds=0.0)

        first = await provider.get_landscape(project_id="talend_demo")
        second = await provider.get_landscape(project_id="talend_demo")

        assert transport.call_count == 2
        # Refetched, but the identical artifact is not re-parsed.
        assert second.landscape is first.landscape

    async def test_changed_fingerprint_replaces_the_cached_artifact(self, talend_demo) -> None:
        republished = {
            **talend_demo,
            "business_topology": {
                **talend_demo["business_topology"],
                "scan_fingerprint": "f" * 64,
                "systems": [
                    {
                        "id": "landscape-system:renamed",
                        "name": "Renamed",
                        "kind": "data_system",
                        "status": "observed",
                        "confidence": 1.0,
                        "evidence_ids": [],
                    }
                ],
            },
        }
        payloads = [talend_demo, republished]

        def responder(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payloads.pop(0) if payloads else republished)

        transport = RecordingTransport(responder)
        provider = make_provider(transport, cache_ttl_seconds=0.0)

        first = await provider.get_landscape(project_id="talend_demo")
        second = await provider.get_landscape(project_id="talend_demo")

        assert transport.call_count == 2
        assert second is not first
        assert first.fingerprint.scan != second.fingerprint.scan
        assert [item.name for item in second.landscape.business_topology.systems] == ["Renamed"]

    async def test_overlay_republished_against_the_same_scan_still_invalidates(
        self, talend_demo
    ) -> None:
        """The scan fingerprint alone is not a sufficient cache key.

        A documentation run can republish a different overlay without a rescan,
        so identity includes the artifact content digest.
        """
        republished = {
            **talend_demo,
            "business_topology": {
                **talend_demo["business_topology"],
                "data_streams": [],
            },
        }
        payloads = [talend_demo, republished]

        def responder(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payloads.pop(0) if payloads else republished)

        provider = make_provider(RecordingTransport(responder), cache_ttl_seconds=0.0)

        first = await provider.get_landscape(project_id="talend_demo")
        second = await provider.get_landscape(project_id="talend_demo")

        assert first.fingerprint.scan == second.fingerprint.scan
        assert first.fingerprint.content != second.fingerprint.content
        assert second.landscape.business_topology.data_streams == []

    async def test_clear_cache_forces_a_refetch(self, talend_demo) -> None:
        transport = json_transport(talend_demo)
        provider = make_provider(transport)

        await provider.get_landscape(project_id="talend_demo")
        provider.clear_cache()
        await provider.get_landscape(project_id="talend_demo")

        assert transport.call_count == 2


class TestDefaultProvider:
    def test_default_provider_is_process_wide_so_its_cache_is_shared(self) -> None:
        assert default_provider() is default_provider()


class TestGraphManifestFamily:
    """The three design endpoints, same discipline as the landscape read."""

    async def test_each_family_reads_its_own_path_and_forwards_the_cookie(
        self, workorders_demo_graph, quintiq_job_graph, richest_component_graph
    ) -> None:
        transport = RoutedTransport(
            {
                project_graph_path("workorders_demo"): workorders_demo_graph,
                job_graph_path("workorders_demo", QUINTIQ_JOB): quintiq_job_graph,
                component_graph_path("workorders_demo", MAPPING_JOB, MAPPING_COMPONENT): (
                    richest_component_graph
                ),
            }
        )
        provider = make_provider(transport)

        await provider.get_project_graph(project_id="workorders_demo", access_token="user-jwt")
        await provider.get_job_graph(
            project_id="workorders_demo", job_name=QUINTIQ_JOB, access_token="user-jwt"
        )
        await provider.get_component_graph(
            project_id="workorders_demo",
            job_name=MAPPING_JOB,
            component_name=MAPPING_COMPONENT,
            access_token="user-jwt",
        )

        assert transport.paths == [
            "/mcp/projects/workorders_demo/graph",
            f"/mcp/projects/workorders_demo/jobs/{QUINTIQ_JOB}/graph",
            f"/mcp/projects/workorders_demo/jobs/{MAPPING_JOB}/components/{MAPPING_COMPONENT}/graph",
        ]
        for request in transport.requests:
            assert request.headers.get("cookie") == "access_token=user-jwt"

    async def test_a_name_is_percent_encoded_as_one_path_segment(self) -> None:
        transport = RecordingTransport(lambda request: httpx.Response(200, json={}))
        provider = make_provider(transport)

        await provider.get_job_graph(project_id="p1", job_name="A B & C")

        # Encoded on the wire, not split: the browser client uses
        # encodeURIComponent, and a raw space or ampersand would address a
        # different resource. `url.path` decodes it again, so assert the target.
        assert transport.requests[0].url.raw_path == (
            b"/mcp/projects/p1/jobs/A%20B%20%26%20C/graph"
        )

    @pytest.mark.parametrize("name", ["../../etc/passwd", "a/b", "a\\b", "..", "x\x00y", "  "])
    async def test_an_unsafe_name_never_reaches_the_url_builder(self, name) -> None:
        transport = json_transport({})
        provider = make_provider(transport)

        with pytest.raises(TopologyProviderError, match="Invalid job name"):
            await provider.get_job_graph(project_id="p1", job_name=name)
        assert transport.call_count == 0

    @pytest.mark.parametrize("name", ["../secret", "c/d"])
    async def test_an_unsafe_component_name_is_refused_too(self, name) -> None:
        transport = json_transport({})
        provider = make_provider(transport)

        with pytest.raises(TopologyProviderError, match="Invalid component name"):
            await provider.get_component_graph(project_id="p1", job_name="job", component_name=name)
        assert transport.call_count == 0

    async def test_a_real_scanner_name_with_dots_and_colons_is_allowed(self) -> None:
        """MCP's own rule, no stricter: real names carry dots, spaces and `::`."""
        transport = RecordingTransport(lambda request: httpx.Response(200, json={}))
        provider = make_provider(transport)

        await provider.get_job_graph(
            project_id="p1", job_name="Inspecthub.__intodoc_zip__::LogConnection"
        )

        assert transport.call_count == 1

    async def test_a_missing_design_is_not_a_missing_project(self) -> None:
        """ "Rescan the project" is the wrong answer to a typo in a job name."""
        provider = make_provider(RoutedTransport({}))

        with pytest.raises(TopologyDesignUnavailableError, match="No design named"):
            await provider.get_job_graph(project_id="p1", job_name="Nope")

    async def test_a_missing_project_graph_still_reports_the_project(self) -> None:
        provider = make_provider(RoutedTransport({}))

        with pytest.raises(TopologyArtifactUnavailableError):
            await provider.get_project_graph(project_id="p1")

    @pytest.mark.parametrize(
        "body",
        [{"fallback": "inspector"}, {"detail": {"fallback": "inspector"}}],
    )
    async def test_the_documented_no_deeper_graph_signal_is_its_own_outcome(self, body) -> None:
        """Both hops' shapes: MCP wraps it in `detail`, the proxy flattens it."""
        transport = RecordingTransport(lambda request: httpx.Response(404, json=body))
        provider = make_provider(transport)

        with pytest.raises(TopologyMappingUnavailableError, match="no deeper mapping graph"):
            await provider.get_component_graph(
                project_id="p1", job_name="job", component_name="tJava_1"
            )

    async def test_a_plain_component_404_is_a_missing_component(self) -> None:
        provider = make_provider(RoutedTransport({}))

        with pytest.raises(TopologyDesignUnavailableError, match="No component named"):
            await provider.get_component_graph(
                project_id="p1", job_name="job", component_name="tJava_1"
            )

    async def test_the_families_share_one_cache_without_colliding(
        self, talend_demo, workorders_demo_graph, quintiq_job_graph
    ) -> None:
        transport = RoutedTransport(
            {
                landscape_path("workorders_demo"): talend_demo,
                project_graph_path("workorders_demo"): workorders_demo_graph,
                job_graph_path("workorders_demo", QUINTIQ_JOB): quintiq_job_graph,
            }
        )
        provider = make_provider(transport)

        landscape = await provider.get_landscape(project_id="workorders_demo")
        graph = await provider.get_project_graph(project_id="workorders_demo")
        job = await provider.get_job_graph(project_id="workorders_demo", job_name=QUINTIQ_JOB)
        # Second pass: all three served from cache, none confused for another.
        assert (await provider.get_landscape(project_id="workorders_demo")).landscape is (
            landscape.landscape
        )
        assert (await provider.get_project_graph(project_id="workorders_demo")).graph is graph.graph
        assert (
            await provider.get_job_graph(project_id="workorders_demo", job_name=QUINTIQ_JOB)
        ).graph is job.graph
        assert transport.call_count == 3

    async def test_two_jobs_are_cached_separately(
        self, quintiq_job_graph, richest_job_graph
    ) -> None:
        transport = RoutedTransport(
            {
                job_graph_path("workorders_demo", QUINTIQ_JOB): quintiq_job_graph,
                job_graph_path("workorders_demo", RICHEST_JOB): richest_job_graph,
            }
        )
        provider = make_provider(transport)

        await provider.get_job_graph(project_id="workorders_demo", job_name=QUINTIQ_JOB)
        await provider.get_job_graph(project_id="workorders_demo", job_name=RICHEST_JOB)
        await provider.get_job_graph(project_id="workorders_demo", job_name=QUINTIQ_JOB)

        assert transport.call_count == 2

    async def test_unknown_upstream_fields_are_tolerated(self) -> None:
        """MCP owns these artifacts and adds fields to them."""
        transport = RecordingTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "job_name": "j",
                    "future_key": [{"deep": True}],
                    "nodes": [{"unique_name": "c1", "future_node_field": 1}],
                },
            )
        )

        artifact = await make_provider(transport).get_job_graph(project_id="p1", job_name="j")

        assert [node.unique_name for node in artifact.graph.nodes] == ["c1"]
