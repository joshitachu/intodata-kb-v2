"""The three defects found by reading 18 real topology chat threads.

Every test here is anchored to something observed in those 45 turns, not to a
shape that seemed plausible:

1. A user selected a CONNECTION on the canvas. The page scope carried
   `{kind: "edge", entity_id: "ds-enterpriseservicebus-rhm--smartflow-message"}`,
   the model passed that into `get_topology_node`, resolution looked at nodes
   only, the call returned not_found -- and the answer that followed was about a
   DIFFERENT integration with nothing saying one had been substituted.

2. `get_job_detail` showed 12 of a real 43 components and 12 of 44 connections
   with no cursor, so every drill-down dead-ended on page one. Same for
   `get_topology_overview`, which was truncated on 11 of 11 calls.

3. `scan/lineage.json` -- 2 222 field-level mappings for the workorders_demo project,
   73 of 85 resolved for the RHM2SF design, each carrying source column, target
   column, expression and confidence -- was reachable over HTTP and wired into
   no tool at all.
"""

from __future__ import annotations

import json

import pytest

from src.config import settings
from src.topology.graph import EDGE_KEY_SEPARATOR, build_graph, business_stream_edge_key
from src.topology.models import (
    GetFieldLineageInput,
    GetJobDetailInput,
    GetTopologyNodeInput,
    GetTopologyOverviewInput,
    ListTopologyDependenciesInput,
    ProjectLandscape,
)
from src.topology.provider import (
    TopologyArtifactUnavailableError,
    TopologyDesignUnavailableError,
)
from src.topology.tools import (
    MAX_JOB_COMPONENTS,
    MAX_LINEAGE_EDGES,
    MAX_LINEAGE_TARGET_TABLES,
    MAX_OVERVIEW_ITEMS,
    get_field_lineage,
    get_job_detail,
    get_topology_node,
    get_topology_overview,
    list_topology_dependencies,
)
from tests.test_topology.conftest import (
    LINEAGE_NO_ARTIFACT_DETAIL,
    LINEAGE_NO_ENTRIES_DETAIL,
    MAPPING_JOB,
    QUINTIQ_JOB,
    RHM2SF_GROUP,
    RHM2SF_LINEAGE_JOB,
    RICHEST_JOB,
    SELECTED_EDGE_STREAM_ID,
    lineage_404_transport,
    make_provider,
    workorders_demo_routes,
)


def payload_size(payload) -> int:  # noqa: ANN001 - TopologyToolPayload
    return len(json.dumps(payload.as_data(), default=str))


@pytest.fixture
def workorders_demo_provider(workorders_demo, workorders_demo_graph, quintiq_job_graph, richest_job_graph):
    transport = workorders_demo_routes(
        landscape=workorders_demo,
        project_graph=workorders_demo_graph,
        job_graphs={QUINTIQ_JOB: quintiq_job_graph, RICHEST_JOB: richest_job_graph},
    )
    return transport, make_provider(transport)


@pytest.fixture
def lineage_provider(workorders_demo, workorders_demo_graph, rhm2sf_lineage):
    transport = workorders_demo_routes(
        landscape=workorders_demo,
        project_graph=workorders_demo_graph,
        lineage=rhm2sf_lineage,
        lineage_groups={RHM2SF_GROUP: [RHM2SF_LINEAGE_JOB]},
    )
    return transport, make_provider(transport)


# --- 1. Edge and stream resolution -------------------------------------------


class TestASelectedConnectionResolves:
    """The observed bug: an edge id from page scope came back not_found."""

    async def test_the_real_page_scope_stream_id_resolves(self, workorders_demo_provider) -> None:
        _transport, provider = workorders_demo_provider

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="workorders_demo", name_or_id=SELECTED_EDGE_STREAM_ID),
            provider=provider,
            access_token=None,
        )

        assert result.content["resolved"] is True
        assert result.content["matched_entity"] == "relationship"
        assert result.content["relationship"]["edge_key"] == business_stream_edge_key(
            SELECTED_EDGE_STREAM_ID
        )

    async def test_both_key_forms_resolve_to_the_same_relationship(
        self, workorders_demo_provider
    ) -> None:
        """The client sends the bare id AND the prefixed key; both are real."""
        _transport, provider = workorders_demo_provider
        prefixed = business_stream_edge_key(SELECTED_EDGE_STREAM_ID)
        assert EDGE_KEY_SEPARATOR in prefixed

        bare_result = await get_topology_node(
            GetTopologyNodeInput(project_id="workorders_demo", name_or_id=SELECTED_EDGE_STREAM_ID),
            provider=provider,
            access_token=None,
        )
        keyed_result = await get_topology_node(
            GetTopologyNodeInput(project_id="workorders_demo", name_or_id=prefixed),
            provider=provider,
            access_token=None,
        )

        assert bare_result.content["relationship"] == keyed_result.content["relationship"]
        assert keyed_result.content["matched_on"] == "id"

    async def test_the_relationship_answer_carries_what_an_answer_needs(
        self, workorders_demo_provider
    ) -> None:
        _transport, provider = workorders_demo_provider

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="workorders_demo", name_or_id=SELECTED_EDGE_STREAM_ID),
            provider=provider,
            access_token=None,
        )
        relationship = result.content["relationship"]

        for key in (
            "dependency_type",
            "mechanism",
            "protocols",
            "business_objects",
            "executed_by",
            "implemented_by_designs",
            "naming",
            "established",
            "established_note",
        ):
            assert key in relationship, key
        # The jargon keys a caller used to quote verbatim at the user are gone.
        for gone in ("scanner_provenance", "claim_status"):
            assert gone not in relationship, gone
        # Both endpoints, named, so the sentence written from this is checkable.
        assert result.content["endpoints"]["source"]["name"]
        assert result.content["endpoints"]["target"]["name"]

    async def test_the_relationship_answer_says_it_is_not_an_entity(
        self, workorders_demo_provider
    ) -> None:
        """Self-describing: the caller asked by name and got a connection."""
        _transport, provider = workorders_demo_provider

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="workorders_demo", name_or_id=SELECTED_EDGE_STREAM_ID),
            provider=provider,
            access_token=None,
        )

        assert "not an entity" in result.content["note"]
        assert result.content["matched_entity"] == "relationship"

    async def test_the_relationship_offers_a_navigable_edge_target(
        self, workorders_demo_provider
    ) -> None:
        _transport, provider = workorders_demo_provider

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="workorders_demo", name_or_id=SELECTED_EDGE_STREAM_ID),
            provider=provider,
            access_token=None,
        )
        primary = result.as_data()["targets"][0]

        assert primary["visual_role"] == "primary"
        assert primary["level"]["selection"] == {
            "kind": "edge",
            "edgeKey": business_stream_edge_key(SELECTED_EDGE_STREAM_ID),
        }

    async def test_a_relationship_id_is_not_answered_as_a_dependency_root(
        self, workorders_demo_provider
    ) -> None:
        """A connection has no dependency set; its endpoints do."""
        _transport, provider = workorders_demo_provider

        result = await list_topology_dependencies(
            ListTopologyDependenciesInput(
                project_id="workorders_demo", name_or_id=SELECTED_EDGE_STREAM_ID
            ),
            provider=provider,
            access_token=None,
        )

        assert result.content["resolved"] is False
        assert result.content["reason"] == "matched_a_relationship_not_an_entity"
        # And it names the two things whose dependencies CAN be listed, so the
        # next call succeeds instead of costing an iteration on a failed search.
        assert len(result.content["candidates"]) == 2

    async def test_an_unknown_id_is_still_reported_as_not_found(self, workorders_demo_provider) -> None:
        """Edge resolution must not turn a miss into a lucky match."""
        _transport, provider = workorders_demo_provider

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="workorders_demo", name_or_id="ds-not-a-real-stream"),
            provider=provider,
            access_token=None,
        )

        assert result.content["resolved"] is False
        assert result.content["reason"] == "not_found"
        assert result.content["candidates"] == []


class TestEdgeVersusNodeAmbiguityIsReportedNotGuessed:
    """A string matching both kinds must list both. Ranking hides the bug."""

    @staticmethod
    def _landscape_with_a_shared_name() -> ProjectLandscape:
        """A scanner graph where one name is a system AND a relationship name.

        Built rather than taken from a fixture because no published artifact
        happens to contain the collision; the discipline still has to hold when
        one does, and the failure it guards against is silent.
        """
        return ProjectLandscape.model_validate(
            {
                "systems": [
                    {"id": "sys-a", "name": "queue_publish via jms", "kind": "queue"},
                    {"id": "sys-b", "name": "Warehouse", "kind": "database"},
                ],
                "edges": [
                    {
                        "source": "sys-a",
                        "target": "sys-b",
                        "source_kind": "queue",
                        "target_kind": "database",
                        "dependency_type": "queue_publish",
                        "mechanism": "jms",
                        "direction": "out",
                    }
                ],
            }
        )

    def test_the_graph_reports_a_node_and_an_edge_for_one_string(self) -> None:
        graph = build_graph(self._landscape_with_a_shared_name())

        resolution = graph.resolve_any("queue_publish via jms")

        assert len(resolution.nodes) == 1
        assert len(resolution.edges) == 1
        # Neither is promoted over the other, so neither can be dropped.
        assert resolution.only_node is None
        assert resolution.only_edge is None
        assert resolution.total == 2

    async def test_the_tool_lists_both_kinds_instead_of_answering(self, monkeypatch) -> None:
        from tests.test_topology.conftest import json_transport

        landscape = self._landscape_with_a_shared_name()
        provider = make_provider(json_transport(landscape.model_dump(mode="json")))

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="collision", name_or_id="queue_publish via jms"),
            provider=provider,
            access_token=None,
        )

        assert result.content["resolved"] is False
        assert result.content["reason"] == "ambiguous"
        assert result.content["matched_kinds"] == ["relationship", "system"]
        kinds = {item["entity"] for item in result.content["candidates"]}
        assert kinds == {"system", "relationship"}
        assert "Do not answer about one of them" in result.content["note"]

    def test_an_edge_candidate_is_shaped_so_it_cannot_pass_for_a_node(self) -> None:
        graph = build_graph(self._landscape_with_a_shared_name())

        edge_match = graph.resolve_edge("queue_publish via jms")[0]

        # `edge_key`, never `id`: a mixed candidate list has to make the two
        # kinds of identifier legible or the model calls back with the wrong one.
        assert edge_match.edge.key
        assert edge_match.match == "exact_name"

    def test_edge_names_are_matched_exactly_and_not_by_substring(self) -> None:
        """Substring matching a generated edge name would manufacture ambiguity."""
        graph = build_graph(self._landscape_with_a_shared_name())

        assert graph.resolve_edge("queue_publish") == []
        assert graph.resolve_edge("via jms") == []
        assert len(graph.resolve_edge("queue_publish via jms")) == 1


# --- 2. Pagination and honest totals -----------------------------------------


class TestJobDetailPages:
    async def test_page_one_reports_shown_against_total(self, workorders_demo_provider) -> None:
        _transport, provider = workorders_demo_provider

        result = await get_job_detail(
            GetJobDetailInput(project_id="workorders_demo", name_or_id=RICHEST_JOB),
            provider=provider,
            access_token=None,
        )
        content = result.content

        assert content["component_count"] == 251
        assert content["components_shown"] == MAX_JOB_COMPONENTS
        assert content["components_offset"] == 0
        assert content["components_has_more"] is True
        assert content["components_next_offset"] == MAX_JOB_COMPONENTS
        assert content["connection_count"] == 201
        assert content["connections_shown"] > 0
        assert content["connections_has_more"] is True

    async def test_paging_is_stable_across_offsets(self, workorders_demo_provider) -> None:
        """No repeats and no skips: `_component_order` is a total order."""
        _transport, provider = workorders_demo_provider

        seen: list[str] = []
        for page in range(4):
            result = await get_job_detail(
                GetJobDetailInput(
                    project_id="workorders_demo",
                    name_or_id=RICHEST_JOB,
                    offset=page * MAX_JOB_COMPONENTS,
                ),
                provider=provider,
                access_token=None,
            )
            assert result.content["components_offset"] == page * MAX_JOB_COMPONENTS
            seen += [item["name"] for item in result.content["components"]]

        assert len(seen) == 4 * MAX_JOB_COMPONENTS
        assert len(set(seen)) == len(seen), "a component appeared on two pages"

    async def test_page_one_still_leads_with_the_drillable_components(
        self, workorders_demo_provider
    ) -> None:
        """The ranking is the paging order, so page 1 keeps its old contents."""
        _transport, provider = workorders_demo_provider

        unpaged = await get_job_detail(
            GetJobDetailInput(project_id="workorders_demo", name_or_id=RICHEST_JOB),
            provider=provider,
            access_token=None,
        )
        explicit_zero = await get_job_detail(
            GetJobDetailInput(project_id="workorders_demo", name_or_id=RICHEST_JOB, offset=0),
            provider=provider,
            access_token=None,
        )

        assert unpaged.content["components"] == explicit_zero.content["components"]

    async def test_the_last_page_says_there_is_nothing_after_it(self, workorders_demo_provider) -> None:
        _transport, provider = workorders_demo_provider
        total = 251
        last_offset = (total // MAX_JOB_COMPONENTS) * MAX_JOB_COMPONENTS

        result = await get_job_detail(
            GetJobDetailInput(project_id="workorders_demo", name_or_id=RICHEST_JOB, offset=last_offset),
            provider=provider,
            access_token=None,
        )

        assert result.content["components_shown"] == total - last_offset
        assert result.content["components_has_more"] is False
        assert result.content["components_next_offset"] is None

    async def test_an_offset_past_the_end_says_so_rather_than_looking_empty(
        self, workorders_demo_provider
    ) -> None:
        _transport, provider = workorders_demo_provider

        result = await get_job_detail(
            GetJobDetailInput(project_id="workorders_demo", name_or_id=RICHEST_JOB, offset=5_000),
            provider=provider,
            access_token=None,
        )

        assert result.content["components"] == []
        assert result.content["component_count"] == 251
        assert "past the end" in result.content["page_note"]

    async def test_targets_follow_the_page(self, workorders_demo_provider) -> None:
        """A chip set frozen on page 1 leaves a page-3 component unclickable."""
        _transport, provider = workorders_demo_provider

        first = await get_job_detail(
            GetJobDetailInput(project_id="workorders_demo", name_or_id=RICHEST_JOB),
            provider=provider,
            access_token=None,
        )
        second = await get_job_detail(
            GetJobDetailInput(
                project_id="workorders_demo", name_or_id=RICHEST_JOB, offset=MAX_JOB_COMPONENTS
            ),
            provider=provider,
            access_token=None,
        )

        def component_labels(payload) -> set[str]:  # noqa: ANN001
            return {
                target["level"]["componentName"]
                for target in payload.as_data()["targets"]
                if target["level"]["kind"] == "component"
            }

        assert component_labels(first)
        assert component_labels(first) & component_labels(second) == set()

    async def test_a_later_page_still_fits_the_result_ceiling(self, workorders_demo_provider) -> None:
        _transport, provider = workorders_demo_provider

        result = await get_job_detail(
            GetJobDetailInput(
                project_id="workorders_demo", name_or_id=RICHEST_JOB, offset=MAX_JOB_COMPONENTS * 3
            ),
            provider=provider,
            access_token=None,
        )

        assert payload_size(result) < settings.runtime_tool_result_max_chars


class TestOverviewPagesToo:
    """Truncated on 11 of 11 real calls, with no way to read past the clip."""

    async def test_diagnostics_report_shown_against_total(self, workorders_demo) -> None:
        from tests.test_topology.conftest import json_transport

        provider = make_provider(json_transport(workorders_demo))

        result = await get_topology_overview(
            GetTopologyOverviewInput(project_id="workorders_demo"),
            provider=provider,
            access_token=None,
        )
        diagnostics = result.content["diagnostics"]

        assert diagnostics["total"] == 38
        assert diagnostics["shown"] == len(diagnostics["top"])
        assert diagnostics["has_more"] is True
        assert diagnostics["next_offset"] == diagnostics["shown"]

    async def test_a_second_page_of_diagnostics_is_reachable(self, workorders_demo) -> None:
        from tests.test_topology.conftest import json_transport

        provider = make_provider(json_transport(workorders_demo))

        first = await get_topology_overview(
            GetTopologyOverviewInput(project_id="workorders_demo"),
            provider=provider,
            access_token=None,
        )
        second = await get_topology_overview(
            GetTopologyOverviewInput(
                project_id="workorders_demo", offset=first.content["diagnostics"]["next_offset"]
            ),
            provider=provider,
            access_token=None,
        )

        assert second.content["diagnostics"]["top"]
        assert second.content["diagnostics"]["top"] != first.content["diagnostics"]["top"]
        assert second.content["diagnostics"]["offset"] == first.content["diagnostics"]["shown"]

    async def test_applications_report_shown_against_total(self, workorders_demo) -> None:
        from tests.test_topology.conftest import json_transport

        provider = make_provider(json_transport(workorders_demo))

        result = await get_topology_overview(
            GetTopologyOverviewInput(project_id="workorders_demo"),
            provider=provider,
            access_token=None,
        )
        facts = result.content["scanner_facts"]

        # Eight applications against a cap of eight: nothing is missing here,
        # and the fields have to be able to say that as well.
        assert facts["applications"] == MAX_OVERVIEW_ITEMS
        assert facts["applications_shown"] == MAX_OVERVIEW_ITEMS
        assert facts["applications_has_more"] is False
        assert facts["applications_next_offset"] is None

    async def test_the_default_page_is_unchanged(self, workorders_demo) -> None:
        """Paging is additive: offset 0 is the answer the canvas already gets."""
        from tests.test_topology.conftest import json_transport

        provider = make_provider(json_transport(workorders_demo))

        result = await get_topology_overview(
            GetTopologyOverviewInput(project_id="workorders_demo"),
            provider=provider,
            access_token=None,
        )

        assert result.content["scanner_facts"]["systems"] == 43
        assert len(result.content["scanner_facts"]["top_applications"]) == MAX_OVERVIEW_ITEMS


# --- 3. Field lineage --------------------------------------------------------


class TestFieldLineageRollup:
    async def test_the_rollup_arithmetic_matches_the_real_scan(self, lineage_provider) -> None:
        """85 mappings, 73 established, 12 decided at runtime."""
        _transport, provider = lineage_provider

        result = await get_field_lineage(
            GetFieldLineageInput(project_id="workorders_demo", job=RHM2SF_LINEAGE_JOB),
            provider=provider,
            access_token=None,
        )
        rollup = result.content["rollup"]

        assert rollup["field_mappings_total"] == 85
        assert rollup["established_field_mappings"] == 73
        assert rollup["runtime_determined_field_mappings"] == 12
        assert (
            rollup["established_field_mappings"] + rollup["runtime_determined_field_mappings"]
            == rollup["field_mappings_total"]
        )
        assert rollup["runtime_determined_share_pct"] == pytest.approx(14.1, abs=0.1)

    async def test_the_confidence_breakdown_is_carried_whole(self, lineage_provider) -> None:
        _transport, provider = lineage_provider

        result = await get_field_lineage(
            GetFieldLineageInput(project_id="workorders_demo", job=RHM2SF_LINEAGE_JOB),
            provider=provider,
            access_token=None,
        )
        breakdown = result.content["rollup"]["confidence_breakdown"]

        assert breakdown == {"certain": 54, "dynamic": 12, "inferred": 12, "likely": 7}
        assert sum(breakdown.values()) == result.content["rollup"]["field_mappings_total"]
        assert result.content["rollup"]["dynamic_confidence_field_mappings"] == 12

    async def test_the_unresolved_count_cannot_be_understated_by_upstream(
        self, rhm2sf_lineage, workorders_demo, workorders_demo_graph
    ) -> None:
        """A zero unresolved beside entries > resolved is not passed through.

        This is the failure mode the rollup exists to prevent: an answer built
        from the confident rows alone sounds complete and understates the
        integration.
        """
        understated = {
            **rhm2sf_lineage,
            "totals": {**rhm2sf_lineage["totals"], "unresolved": 0},
        }
        transport = workorders_demo_routes(
            landscape=workorders_demo, project_graph=workorders_demo_graph, lineage=understated
        )
        provider = make_provider(transport)

        result = await get_field_lineage(
            GetFieldLineageInput(project_id="workorders_demo", job=RHM2SF_LINEAGE_JOB),
            provider=provider,
            access_token=None,
        )

        # Recomputed from entries - resolved rather than believed.
        assert result.content["rollup"]["runtime_determined_field_mappings"] == 12

    async def test_the_target_tables_are_named_with_their_total(self, lineage_provider) -> None:
        _transport, provider = lineage_provider

        result = await get_field_lineage(
            GetFieldLineageInput(project_id="workorders_demo", job=RHM2SF_LINEAGE_JOB),
            provider=provider,
            access_token=None,
        )
        rollup = result.content["rollup"]

        assert rollup["target_tables_total"] == 7
        assert "PlanningRealization" in rollup["target_tables"]
        assert len(rollup["target_tables"]) <= MAX_LINEAGE_TARGET_TABLES

    async def test_it_leads_with_the_rollup_and_only_then_a_bounded_page(
        self, lineage_provider
    ) -> None:
        """A ~360px panel cannot render 85 mappings; the rollup is the answer."""
        _transport, provider = lineage_provider

        result = await get_field_lineage(
            GetFieldLineageInput(project_id="workorders_demo", job=RHM2SF_LINEAGE_JOB),
            provider=provider,
            access_token=None,
        )

        keys = list(result.content)
        assert keys.index("rollup") < keys.index("mappings")
        assert len(result.content["mappings"]) == MAX_LINEAGE_EDGES
        assert result.content["page"]["total"] == 85
        assert result.content["page"]["shown"] == MAX_LINEAGE_EDGES
        assert result.content["page"]["has_more"] is True
        assert result.truncated is True
        assert payload_size(result) < settings.runtime_tool_result_max_chars

    async def test_it_pages_through_the_mappings(self, lineage_provider) -> None:
        _transport, provider = lineage_provider

        first = await get_field_lineage(
            GetFieldLineageInput(project_id="workorders_demo", job=RHM2SF_LINEAGE_JOB),
            provider=provider,
            access_token=None,
        )
        second = await get_field_lineage(
            GetFieldLineageInput(
                project_id="workorders_demo",
                job=RHM2SF_LINEAGE_JOB,
                offset=first.content["page"]["next_offset"],
            ),
            provider=provider,
            access_token=None,
        )

        assert second.content["page"]["offset"] == MAX_LINEAGE_EDGES
        assert second.content["mappings"] != first.content["mappings"]
        # Totals are over the whole set, not the page, on every page.
        assert second.content["rollup"]["field_mappings_total"] == 85

    async def test_the_last_page_closes_the_cursor(self, lineage_provider) -> None:
        _transport, provider = lineage_provider

        result = await get_field_lineage(
            GetFieldLineageInput(project_id="workorders_demo", job=RHM2SF_LINEAGE_JOB, offset=84),
            provider=provider,
            access_token=None,
        )

        assert result.content["page"]["shown"] == 1
        assert result.content["page"]["has_more"] is False
        assert result.content["page"]["next_offset"] is None

    async def test_a_runtime_determined_mapping_says_so_at_row_level(
        self, lineage_provider
    ) -> None:
        """An empty source column must not render as a mapping from nothing."""
        _transport, provider = lineage_provider

        rows: list[dict] = []
        for offset in range(0, 85, MAX_LINEAGE_EDGES):
            result = await get_field_lineage(
                GetFieldLineageInput(project_id="workorders_demo", job=RHM2SF_LINEAGE_JOB, offset=offset),
                provider=provider,
                access_token=None,
            )
            rows += result.content["mappings"]

        unresolved = [row for row in rows if not row["resolved"]]
        assert len(unresolved) == 12
        sourceless = [row for row in unresolved if row["from"] == "(not established by the scan)"]
        assert sourceless, "an unresolved mapping rendered its missing source silently"
        assert all(row["transformation"] for row in unresolved)

    async def test_it_never_calls_a_missing_mapping_an_absent_transformation(
        self, lineage_provider
    ) -> None:
        _transport, provider = lineage_provider

        result = await get_field_lineage(
            GetFieldLineageInput(project_id="workorders_demo", job=RHM2SF_LINEAGE_JOB),
            provider=provider,
            access_token=None,
        )

        note = result.content["note"]
        assert "never report a missing mapping as an absence of transformation" in note
        assert "do not infer one" in note

    async def test_a_group_scope_covers_a_whole_integration(self, lineage_provider) -> None:
        _transport, provider = lineage_provider

        result = await get_field_lineage(
            GetFieldLineageInput(project_id="workorders_demo", group=RHM2SF_GROUP),
            provider=provider,
            access_token=None,
        )

        assert result.content["scope"]["group"] == RHM2SF_GROUP
        assert result.content["scope"]["designs_covered"] == [RHM2SF_LINEAGE_JOB]
        assert result.content["rollup"]["field_mappings_total"] == 85

    async def test_the_request_carries_the_scope_and_the_page(self, lineage_provider) -> None:
        transport, provider = lineage_provider

        await get_field_lineage(
            GetFieldLineageInput(project_id="workorders_demo", job=RHM2SF_LINEAGE_JOB, offset=24),
            provider=provider,
            access_token=None,
        )

        query = transport.queries_matching("/lineage/job")[0]
        assert "job=" in query
        assert "offset=24" in query
        assert f"limit={MAX_LINEAGE_EDGES}" in query

    async def test_it_offers_the_mapping_components_as_targets(self, lineage_provider) -> None:
        _transport, provider = lineage_provider

        result = await get_field_lineage(
            GetFieldLineageInput(project_id="workorders_demo", job=RHM2SF_LINEAGE_JOB),
            provider=provider,
            access_token=None,
        )
        targets = result.as_data()["targets"]

        assert targets[0]["level"] == {"kind": "job", "jobName": RHM2SF_LINEAGE_JOB}
        assert targets[0]["visual_role"] == "primary"
        assert any(target["level"]["kind"] == "component" for target in targets)

    async def test_a_source_root_pins_the_partition_in_the_targets(self, lineage_provider) -> None:
        _transport, provider = lineage_provider

        result = await get_field_lineage(
            GetFieldLineageInput(
                project_id="workorders_demo", job=RHM2SF_LINEAGE_JOB, source_root=RHM2SF_GROUP
            ),
            provider=provider,
            access_token=None,
        )

        assert result.as_data()["targets"][0]["level"]["sourceRoot"] == RHM2SF_GROUP

    async def test_a_scope_is_required(self) -> None:
        """An unscoped read of 2 222 mappings is a document, not an answer."""
        with pytest.raises(ValueError, match="job=<design name>"):
            GetFieldLineageInput(project_id="workorders_demo")

    async def test_the_name_is_not_gated_on_the_design_manifest(self, lineage_provider) -> None:
        """Lineage job names and manifest design names verifiably diverge.

        The lineage artifact calls this partition `..._intod-31f9ad59e878`; the
        project manifest calls the same one `..._intod-249ee1cc0d2b`. Resolving
        against the manifest first would answer "not in the scan" for a design
        that has 85 field mappings.
        """
        transport, provider = lineage_provider

        result = await get_field_lineage(
            GetFieldLineageInput(project_id="workorders_demo", job=RHM2SF_LINEAGE_JOB),
            provider=provider,
            access_token=None,
        )

        assert result.content["resolved"] is True
        assert transport.paths_matching("/graph") == []


class TestTheTwoLineage404s:
    """Opposite answers: "rescan the project" versus "that name is not in it"."""

    async def test_no_lineage_artifact_is_an_artifact_error(self) -> None:
        provider = make_provider(lineage_404_transport(LINEAGE_NO_ARTIFACT_DETAIL))

        with pytest.raises(TopologyArtifactUnavailableError) as caught:
            await provider.get_job_lineage(project_id="workorders_demo", jobs=(RHM2SF_LINEAGE_JOB,))

        assert "needs a scan" in caught.value.safe_message

    async def test_no_entries_for_the_scope_is_a_design_error(self) -> None:
        provider = make_provider(lineage_404_transport(LINEAGE_NO_ENTRIES_DETAIL))

        with pytest.raises(TopologyDesignUnavailableError) as caught:
            await provider.get_job_lineage(project_id="workorders_demo", jobs=("NotADesign",))

        message = caught.value.safe_message
        assert "NotADesign" in message
        # And explicitly NOT a rescan instruction: telling a user to rescan over
        # a typo is what this distinction exists to prevent.
        assert "needs a scan" not in message

    async def test_a_structured_reason_wins_over_the_message_text(self) -> None:
        """Preferred signal, so this does not depend on upstream prose."""
        import httpx

        from tests.test_topology.conftest import RoutedTransport, field_lineage_path

        transport = RoutedTransport(
            {
                field_lineage_path("workorders_demo"): lambda _request: httpx.Response(
                    404,
                    json={
                        "detail": {
                            "reason": "lineage_artifact_absent",
                            "message": "nothing here",
                        }
                    },
                )
            }
        )
        provider = make_provider(transport)

        with pytest.raises(TopologyArtifactUnavailableError):
            await provider.get_job_lineage(project_id="workorders_demo", jobs=("anything",))

    async def test_an_unrecognised_404_defaults_to_the_safer_reading(self) -> None:
        """No marker means "name not in the scan", never "rescan the project"."""
        provider = make_provider(lineage_404_transport("something went wrong"))

        with pytest.raises(TopologyDesignUnavailableError):
            await provider.get_job_lineage(project_id="workorders_demo", jobs=("anything",))

    async def test_a_scope_with_no_entries_404s_through_the_real_filter(
        self, lineage_provider
    ) -> None:
        """End to end: the endpoint's own filter produces the scope-miss 404."""
        _transport, provider = lineage_provider

        with pytest.raises(TopologyDesignUnavailableError):
            await get_field_lineage(
                GetFieldLineageInput(project_id="workorders_demo", job=MAPPING_JOB),
                provider=provider,
                access_token=None,
            )
