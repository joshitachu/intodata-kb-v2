"""Tests for the bounded topology tool projections (src/topology/tools.py)."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from src.config import settings
from src.topology.graph import EDGE_KEY_SEPARATOR, TopologyView, build_graph
from src.topology.models import (
    FindTopologyNodeInput,
    GetTopologyNodeInput,
    GetTopologyOverviewInput,
    ListTopologyDependenciesInput,
    ProjectLandscape,
    TopologyDirection,
)
from src.topology.tools import (
    MAX_CANDIDATES,
    MAX_DEPENDENCY_EDGES,
    MAX_DEPENDENCY_NODES,
    MAX_DIAGNOSTICS,
    MAX_NODE_EDGES,
    MAX_TARGETS,
    find_topology_node,
    get_topology_node,
    get_topology_overview,
    list_topology_dependencies,
)
from tests.test_topology.conftest import json_transport, make_provider

# The two overlay systems that share the name "Log" in the real alitalend
# artifact. They are different databases (LogConnection vs LogDBConnection).
LOG_A = "landscape-system:aab3dedb92918d1cf853"
LOG_B = "landscape-system:7535d19590ac8488f941"
ASSETCHECK = "landscape-application:1d5c0f7777b98508aec8"


def scanner_view(payload: dict[str, Any]) -> dict[str, Any]:
    """The same artifact with an unvalidated overlay, as MCP serves it mid-run."""
    mutated = copy.deepcopy(payload)
    mutated["topology_state"] = {
        "available": True,
        "validated": False,
        "stale": False,
        "source": "documentation_topology_agent",
        "message": "The latest business topology did not pass validation.",
    }
    return mutated


def oversized_landscape(*, neighbours: int) -> dict[str, Any]:
    """A star graph far larger than every cap, in the scanner id space."""
    return {
        "applications": [
            {
                "id": "app:hub",
                "source_root": "root/hub",
                "label": "Hub",
                "role": "execution",
                "artifact_count": neighbours,
            }
        ],
        "systems": [
            {"id": f"system:leaf:{index}", "name": f"Leaf {index}", "kind": "database"}
            for index in range(neighbours)
        ],
        "edges": [
            {
                "source": "app:hub",
                "target": f"system:leaf:{index}",
                "source_kind": "application",
                "target_kind": "system",
                "dependency_type": "data",
                "mechanism": "db",
                "direction": "both",
                "evidence_count": index,
            }
            for index in range(neighbours)
        ],
        "diagnostics": [
            {"kind": f"diag_{index}", "description": "d", "severity": "warning"}
            for index in range(MAX_DIAGNOSTICS * 3)
        ],
    }


class TestActiveView:
    def test_validated_current_overlay_is_the_active_graph(self, alitalend) -> None:
        graph = build_graph(ProjectLandscape.model_validate(alitalend))

        assert graph.view is TopologyView.BUSINESS
        assert {node.id for node in graph.nodes} >= {LOG_A, LOG_B, ASSETCHECK}

    def test_unvalidated_overlay_falls_back_to_scanner_facts(self, alitalend) -> None:
        graph = build_graph(ProjectLandscape.model_validate(scanner_view(alitalend)))

        assert graph.view is TopologyView.SCANNER
        # Scanner ids, not overlay ids: the canvas would render this graph, so a
        # target built from the overlay id space would navigate nowhere.
        assert {node.id for node in graph.nodes} == {
            "system:connection:0:log",
            "system:connection:1:log",
            "system:connection:2:common",
            "talend-project:assetcheck:d3ec09658f7d",
            "talend-project:references:c7db6393ae51",
        }

    def test_stale_overlay_falls_back_to_scanner_facts(self, alitalend) -> None:
        mutated = copy.deepcopy(alitalend)
        mutated["topology_state"]["stale"] = True

        assert build_graph(ProjectLandscape.model_validate(mutated)).view is TopologyView.SCANNER

    def test_missing_overlay_falls_back_to_scanner_facts(self, alitalend) -> None:
        mutated = copy.deepcopy(alitalend)
        mutated["business_topology"] = None

        assert build_graph(ProjectLandscape.model_validate(mutated)).view is TopologyView.SCANNER


class TestOverview:
    async def test_reports_counts_roles_state_and_overlay_separately(self, alitalend) -> None:
        provider = make_provider(json_transport(alitalend))

        result = await get_topology_overview(
            GetTopologyOverviewInput(project_id="alitalend"), provider=provider, access_token=None
        )
        content = result.content

        assert content["active_view"] == "business"
        assert content["topology_state"] == {
            "available": True,
            "validated": True,
            "stale": False,
            "source": "documentation_topology_agent",
            "message": "Business topology is available and validated for the current scan.",
        }
        assert content["scanner_facts"]["systems"] == 3
        assert content["scanner_facts"]["applications"] == 2
        assert [item["label"] for item in content["scanner_facts"]["top_applications"]] == [
            "AssetCheck",
            "References",
        ]
        assert content["scanner_facts"]["systems_by_kind"] == {"database": 3}
        assert content["scanner_facts"]["application_roles"] == {"execution": 1, "support": 1}
        assert content["scanner_facts"]["edges_by_mechanism"] == {"db": 2}
        # Scanner counts and overlay counts stay side by side: three scanner
        # systems became five overlay systems, and flattening that would make
        # either number look wrong.
        assert content["business_overlay"]["systems"] == 5
        assert content["business_overlay"]["systems_by_establishment"] == {"observed": 5}
        assert content["business_overlay"]["data_streams"] == 2
        assert content["scan_fingerprint"].startswith("c203140e")

    async def test_composite_overview_reports_the_curated_overlay_source(self, werkorder) -> None:
        provider = make_provider(json_transport(werkorder))

        result = await get_topology_overview(
            GetTopologyOverviewInput(project_id="werkorder"), provider=provider, access_token=None
        )

        assert result.content["topology_state"]["source"] == "analyst_curated"
        assert result.content["scanner_facts"]["systems"] == 43
        assert result.content["scanner_facts"]["applications"] == 8
        assert result.content["business_overlay"]["systems"] == 20
        # Multi-partition upload: every application is a separate source root.
        assert (
            len(
                {
                    item["source_root"]
                    for item in result.content["scanner_facts"]["top_applications"]
                }
            )
            == 8
        )

    async def test_overlay_absent_is_reported_as_null_not_omitted(self, alitalend) -> None:
        mutated = copy.deepcopy(alitalend)
        mutated["business_topology"] = None
        mutated["topology_state"] = None
        provider = make_provider(json_transport(mutated))

        result = await get_topology_overview(
            GetTopologyOverviewInput(project_id="alitalend"), provider=provider, access_token=None
        )

        assert result.content["business_overlay"] is None
        assert result.content["topology_state"]["available"] is False


class TestAmbiguity:
    async def test_find_returns_every_same_named_system(self, alitalend) -> None:
        provider = make_provider(json_transport(alitalend))

        result = await find_topology_node(
            FindTopologyNodeInput(project_id="alitalend", query="Log"),
            provider=provider,
            access_token=None,
        )
        content = result.content

        assert content["ambiguous"] is True
        assert content["match_count"] == 2
        assert {item["id"] for item in content["candidates"]} == {LOG_A, LOG_B}
        # Every candidate is navigable, so the user can pick on the canvas.
        assert {target["level"]["selection"]["systemId"] for target in result.targets} == {
            LOG_A,
            LOG_B,
        }

    async def test_find_gives_each_candidate_a_discriminator(self, alitalend) -> None:
        provider = make_provider(json_transport(alitalend))

        result = await find_topology_node(
            FindTopologyNodeInput(project_id="alitalend", query="Log"),
            provider=provider,
            access_token=None,
        )

        adjacency = {item["id"]: item["adjacent"] for item in result.content["candidates"]}
        assert adjacency[LOG_A] == ["AssetCheck to Log database data: AssetCheck -> Log"]
        assert adjacency[LOG_B] == ["AssetCheck to second Log database data: AssetCheck -> Log"]

    async def test_get_node_refuses_to_pick_one_of_two_same_named_systems(self, alitalend) -> None:
        provider = make_provider(json_transport(alitalend))

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="alitalend", name_or_id="Log"),
            provider=provider,
            access_token=None,
        )
        content = result.content

        assert content["resolved"] is False
        assert content["reason"] == "ambiguous"
        assert "node" not in content
        assert {item["id"] for item in content["candidates"]} == {LOG_A, LOG_B}

    async def test_dependencies_refuse_to_pick_one_of_two_same_named_systems(
        self, alitalend
    ) -> None:
        provider = make_provider(json_transport(alitalend))

        result = await list_topology_dependencies(
            ListTopologyDependenciesInput(project_id="alitalend", name_or_id="Log"),
            provider=provider,
            access_token=None,
        )

        assert result.content["resolved"] is False
        assert result.content["reason"] == "ambiguous"
        assert "dependencies" not in result.content

    async def test_unknown_name_is_reported_as_not_found_not_as_an_error(self, alitalend) -> None:
        provider = make_provider(json_transport(alitalend))

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="alitalend", name_or_id="Salesforce"),
            provider=provider,
            access_token=None,
        )

        assert result.content["resolved"] is False
        assert result.content["reason"] == "not_found"
        assert result.content["candidates"] == []


class TestNameResolutionWithoutARoundTrip:
    async def test_get_node_resolves_a_name_in_one_call(self, alitalend) -> None:
        transport = json_transport(alitalend)
        provider = make_provider(transport)

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="alitalend", name_or_id="AssetCheck"),
            provider=provider,
            access_token=None,
        )

        # One artifact read, no find_topology_node iteration spent first.
        assert transport.call_count == 1
        assert result.content["resolved"] is True
        assert result.content["matched_on"] == "exact_name"
        assert result.content["node"]["id"] == ASSETCHECK

    async def test_get_node_also_accepts_an_id(self, alitalend) -> None:
        provider = make_provider(json_transport(alitalend))

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="alitalend", name_or_id=LOG_B),
            provider=provider,
            access_token=None,
        )

        assert result.content["matched_on"] == "id"
        assert result.content["node"]["id"] == LOG_B

    async def test_dependencies_resolve_a_name_in_one_call(self, alitalend) -> None:
        transport = json_transport(alitalend)
        provider = make_provider(transport)

        result = await list_topology_dependencies(
            ListTopologyDependenciesInput(project_id="alitalend", name_or_id="AssetCheck"),
            provider=provider,
            access_token=None,
        )

        assert transport.call_count == 1
        assert result.content["resolved"] is True
        assert {item["id"] for item in result.content["dependencies"]} == {LOG_A, LOG_B}

    async def test_case_insensitive_name_still_resolves(self, alitalend) -> None:
        provider = make_provider(json_transport(alitalend))

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="alitalend", name_or_id="assetcheck"),
            provider=provider,
            access_token=None,
        )

        assert result.content["node"]["id"] == ASSETCHECK


class TestProvenance:
    async def test_overlay_node_keeps_its_claim_status_and_naming(self, alitalend) -> None:
        provider = make_provider(json_transport(alitalend))

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="alitalend", name_or_id=LOG_A),
            provider=provider,
            access_token=None,
        )

        assert result.content["node"]["provenance"]["naming"] == "business_overlay"
        assert result.content["node"]["provenance"]["claim_status"] == "observed"
        assert result.content["node"]["provenance"]["confidence"] == 1.0

    async def test_overlay_node_name_match_to_scanner_is_labelled_as_a_name_match(
        self, alitalend
    ) -> None:
        provider = make_provider(json_transport(alitalend))

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="alitalend", name_or_id=LOG_A),
            provider=provider,
            access_token=None,
        )
        evidence = result.content["evidence"]

        assert [item["id"] for item in evidence["name_matched_scanner_systems"]] == [
            "system:connection:0:log",
            "system:connection:1:log",
        ]
        # The connection names are the only thing distinguishing the two Log
        # databases in the scan, so they must survive the projection.
        assert evidence["name_matched_scanner_systems"][0]["scanner_signals"] == [
            "metadata_connection:LogConnection"
        ]
        assert "not an established identity" in evidence["name_match_note"]

    async def test_scanner_node_is_not_dressed_up_as_an_overlay_claim(self, alitalend) -> None:
        provider = make_provider(json_transport(scanner_view(alitalend)))

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="alitalend", name_or_id="system:connection:1:log"),
            provider=provider,
            access_token=None,
        )
        provenance = result.content["node"]["provenance"]

        assert provenance["naming"] == "scanner"
        assert provenance["confidence"] is None
        assert provenance["scanner_signals"] == ["metadata_connection:LogDBConnection"]
        assert "name_matched_scanner_systems" not in result.content["evidence"]

    async def test_overlay_stream_reports_the_scanner_jobs_that_implement_it(
        self, alitalend
    ) -> None:
        provider = make_provider(json_transport(alitalend))

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="alitalend", name_or_id="AssetCheck"),
            provider=provider,
            access_token=None,
        )
        first = result.content["relationships"][0]

        assert first["naming"] == "business_overlay"
        assert first["established"] == "observed"
        # A paraphrasable sentence travels with the token. Publishing the token
        # alone produced "this stream has observed claim status" -- the schema
        # field name pasted onto its own value.
        assert first["established_note"]
        assert "claim_status" not in first
        assert first["executed_by"] == ["AssetCheck"]
        # Opaque artifact ids resolved through the published evidence catalogue.
        # Keyed as designs, not as untyped "provenance": a caller that has to guess
        # the kind guesses "component", which means something else here.
        assert "AssetCheck_GetLocation" in first["implemented_by_designs"]
        assert "scanner_provenance" not in first


class TestTargets:
    async def test_system_target_carries_a_system_id(self, alitalend) -> None:
        provider = make_provider(json_transport(alitalend))

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="alitalend", name_or_id=LOG_A),
            provider=provider,
            access_token=None,
        )

        assert result.targets[0] == {
            "label": "Log",
            "level": {
                "kind": "integration",
                "selection": {"kind": "system", "systemId": LOG_A},
            },
            # Passive by default: a chip that navigates unasked moves the canvas
            # out from under the user.
            "intent": "reference",
            "visual_role": "primary",
        }

    async def test_application_target_carries_an_application_id(self, alitalend) -> None:
        provider = make_provider(json_transport(scanner_view(alitalend)))

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="alitalend", name_or_id="AssetCheck"),
            provider=provider,
            access_token=None,
        )

        assert result.targets[0] == {
            "label": "AssetCheck",
            "level": {
                "kind": "integration",
                "selection": {
                    "kind": "application",
                    "applicationId": "talend-project:assetcheck:d3ec09658f7d",
                },
            },
            "intent": "reference",
            "visual_role": "primary",
        }

    async def test_overlay_edge_target_uses_the_business_stream_key(self, alitalend) -> None:
        provider = make_provider(json_transport(alitalend))

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="alitalend", name_or_id="AssetCheck"),
            provider=provider,
            access_token=None,
        )
        edge_targets = [
            target for target in result.targets if target["level"]["selection"]["kind"] == "edge"
        ]

        assert edge_targets[0]["level"]["selection"]["edgeKey"] == (
            f"business-stream{EDGE_KEY_SEPARATOR}stream:assetcheck-to-log-0"
        )

    async def test_scanner_edge_target_uses_the_seven_part_tuple_key(self, alitalend) -> None:
        provider = make_provider(json_transport(scanner_view(alitalend)))

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="alitalend", name_or_id="AssetCheck"),
            provider=provider,
            access_token=None,
        )
        edge_key = next(
            target["level"]["selection"]["edgeKey"]
            for target in result.targets
            if target["level"]["selection"]["kind"] == "edge"
        )

        assert edge_key.split(EDGE_KEY_SEPARATOR) == [
            "application",
            "talend-project:assetcheck:d3ec09658f7d",
            "system",
            "system:connection:0:log",
            "data",
            "db",
            "both",
        ]

    async def test_landscape_level_tools_only_emit_integration_targets(self, werkorder) -> None:
        """Group/job/component targets belong to the depth tools, not to these.

        These four read the landscape rollup, which has no job or component
        nodes; emitting one would be a chip pointing at something the tool never
        read.
        """
        provider = make_provider(json_transport(werkorder))

        results = [
            await get_topology_overview(
                GetTopologyOverviewInput(project_id="werkorder"),
                provider=provider,
                access_token=None,
            ),
            await find_topology_node(
                FindTopologyNodeInput(project_id="werkorder", query="Maximo"),
                provider=provider,
                access_token=None,
            ),
            await get_topology_node(
                GetTopologyNodeInput(project_id="werkorder", name_or_id="Maximo"),
                provider=provider,
                access_token=None,
            ),
            await list_topology_dependencies(
                ListTopologyDependenciesInput(project_id="werkorder", name_or_id="Maximo"),
                provider=provider,
                access_token=None,
            ),
        ]

        for result in results:
            assert result.targets
            for target in result.targets:
                assert target["level"]["kind"] == "integration"
                assert target["level"]["selection"]["kind"] in {
                    "system",
                    "application",
                    "edge",
                    "relationship",
                }
                assert set(target) == {"label", "level", "intent", "visual_role"}


class TestVisualTargetRoles:
    async def test_broad_overview_has_no_primary_target(self, werkorder) -> None:
        result = await get_topology_overview(
            GetTopologyOverviewInput(project_id="werkorder"),
            provider=make_provider(json_transport(werkorder)),
            access_token=None,
        )

        assert result.targets
        assert {target["visual_role"] for target in result.targets} == {"supporting"}

    async def test_ambiguous_search_has_no_primary_target(self, alitalend) -> None:
        result = await find_topology_node(
            FindTopologyNodeInput(project_id="alitalend", query="Log"),
            provider=make_provider(json_transport(alitalend)),
            access_token=None,
        )

        assert result.content["ambiguous"] is True
        assert "primary" not in {target["visual_role"] for target in result.targets}

    async def test_single_search_result_is_primary(self, alitalend) -> None:
        result = await find_topology_node(
            FindTopologyNodeInput(
                project_id="alitalend", query="landscape-system:4dbae6120cef624419bb"
            ),
            provider=make_provider(json_transport(alitalend)),
            access_token=None,
        )

        assert len(result.targets) == 1
        assert result.targets[0]["visual_role"] == "primary"

    async def test_resolved_node_is_primary_and_relationships_are_path(self, werkorder) -> None:
        result = await get_topology_node(
            GetTopologyNodeInput(project_id="werkorder", name_or_id="Maximo"),
            provider=make_provider(json_transport(werkorder)),
            access_token=None,
        )

        assert result.targets[0]["visual_role"] == "primary"
        assert all(target["visual_role"] == "path" for target in result.targets[1:])

    async def test_dependency_root_is_primary_and_traversal_is_path(self, werkorder) -> None:
        result = await list_topology_dependencies(
            ListTopologyDependenciesInput(project_id="werkorder", name_or_id="Maximo"),
            provider=make_provider(json_transport(werkorder)),
            access_token=None,
        )

        assert result.targets[0]["visual_role"] == "primary"
        assert all(target["visual_role"] == "path" for target in result.targets[1:])


class TestTraversal:
    async def test_direction_follows_graph_orientation_not_the_mechanism_attribute(
        self, werkorder
    ) -> None:
        provider = make_provider(json_transport(werkorder))

        upstream = await list_topology_dependencies(
            ListTopologyDependenciesInput(
                project_id="werkorder", name_or_id="Maximo", direction=TopologyDirection.UPSTREAM
            ),
            provider=provider,
            access_token=None,
        )
        downstream = await list_topology_dependencies(
            ListTopologyDependenciesInput(
                project_id="werkorder", name_or_id="Maximo", direction=TopologyDirection.DOWNSTREAM
            ),
            provider=provider,
            access_token=None,
        )

        assert {item["direction"] for item in upstream.content["dependencies"]} == {"upstream"}
        assert {item["direction"] for item in downstream.content["dependencies"]} == {"downstream"}
        assert upstream.content["dependencies"] != downstream.content["dependencies"]

    async def test_two_hops_reaches_further_than_one(self) -> None:
        chain = {
            "systems": [
                {"id": "system:a", "name": "A"},
                {"id": "system:b", "name": "B"},
                {"id": "system:c", "name": "C"},
            ],
            "edges": [
                {
                    "source": "system:a",
                    "target": "system:b",
                    "source_kind": "system",
                    "target_kind": "system",
                    "dependency_type": "data",
                },
                {
                    "source": "system:b",
                    "target": "system:c",
                    "source_kind": "system",
                    "target_kind": "system",
                    "dependency_type": "data",
                },
            ],
        }
        provider = make_provider(json_transport(chain))

        one = await list_topology_dependencies(
            ListTopologyDependenciesInput(project_id="chain", name_or_id="A", hops=1),
            provider=provider,
            access_token=None,
        )
        two = await list_topology_dependencies(
            ListTopologyDependenciesInput(project_id="chain", name_or_id="A", hops=2),
            provider=provider,
            access_token=None,
        )

        assert [item["id"] for item in one.content["dependencies"]] == ["system:b"]
        assert [(item["id"], item["hop"]) for item in two.content["dependencies"]] == [
            ("system:b", 1),
            ("system:c", 2),
        ]

    def test_hops_above_two_is_rejected_by_input_validation(self) -> None:
        with pytest.raises(ValueError):
            ListTopologyDependenciesInput(project_id="alitalend", name_or_id="A", hops=3)

    def test_hops_below_one_is_rejected_by_input_validation(self) -> None:
        with pytest.raises(ValueError):
            ListTopologyDependenciesInput(project_id="alitalend", name_or_id="A", hops=0)

    def test_unknown_direction_is_rejected_by_input_validation(self) -> None:
        with pytest.raises(ValueError):
            ListTopologyDependenciesInput(
                project_id="alitalend", name_or_id="A", direction="sideways"
            )

    def test_traversal_project_id_must_be_a_plain_identifier(self) -> None:
        with pytest.raises(ValueError):
            ListTopologyDependenciesInput(project_id="../../etc", name_or_id="A")


class TestCapsOnAnOversizedArtifact:
    async def test_find_caps_candidates_and_says_so(self) -> None:
        provider = make_provider(json_transport(oversized_landscape(neighbours=60)))

        result = await find_topology_node(
            FindTopologyNodeInput(project_id="huge", query="Leaf"),
            provider=provider,
            access_token=None,
        )

        assert len(result.content["candidates"]) == MAX_CANDIDATES
        assert result.content["more_matches_exist"] is True
        assert result.truncated is True

    async def test_get_node_caps_relationships_and_reports_the_real_total(self) -> None:
        provider = make_provider(json_transport(oversized_landscape(neighbours=60)))

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="huge", name_or_id="Hub"),
            provider=provider,
            access_token=None,
        )

        assert result.content["relationship_count"] == 60
        assert len(result.content["relationships"]) == MAX_NODE_EDGES
        assert result.content["relationships_truncated"] is True

    async def test_dependencies_cap_nodes_and_edges(self) -> None:
        provider = make_provider(json_transport(oversized_landscape(neighbours=60)))

        result = await list_topology_dependencies(
            ListTopologyDependenciesInput(project_id="huge", name_or_id="Hub", hops=2),
            provider=provider,
            access_token=None,
        )

        assert len(result.content["dependencies"]) <= MAX_DEPENDENCY_NODES
        assert len(result.content["relationships"]) <= MAX_DEPENDENCY_EDGES
        assert result.content["truncated"] is True

    async def test_overview_caps_diagnostics(self) -> None:
        provider = make_provider(json_transport(oversized_landscape(neighbours=60)))

        result = await get_topology_overview(
            GetTopologyOverviewInput(project_id="huge"), provider=provider, access_token=None
        )

        assert result.content["scanner_facts"]["diagnostics"] == MAX_DIAGNOSTICS * 3
        assert len(result.content["diagnostics"]["top"]) == MAX_DIAGNOSTICS
        assert result.truncated is True

    async def test_targets_are_capped_on_every_tool(self) -> None:
        provider = make_provider(json_transport(oversized_landscape(neighbours=60)))
        calls = [
            get_topology_overview(
                GetTopologyOverviewInput(project_id="huge"), provider=provider, access_token=None
            ),
            find_topology_node(
                FindTopologyNodeInput(project_id="huge", query="Leaf"),
                provider=provider,
                access_token=None,
            ),
            get_topology_node(
                GetTopologyNodeInput(project_id="huge", name_or_id="Hub"),
                provider=provider,
                access_token=None,
            ),
            list_topology_dependencies(
                ListTopologyDependenciesInput(project_id="huge", name_or_id="Hub", hops=2),
                provider=provider,
                access_token=None,
            ),
        ]

        for call in calls:
            result = await call
            assert len(result.as_data()["targets"]) <= MAX_TARGETS

    @pytest.mark.parametrize("hops", [1, 2])
    async def test_real_composite_hub_stays_inside_the_result_ceiling(
        self, werkorder, hops
    ) -> None:
        """The caps, not the size backstop, are what keeps a hub answer usable.

        Maximo has 32 relationships in this artifact. If a projection here grows,
        this test fails before the backstop silently drops the answer's payload.
        """
        provider = make_provider(json_transport(werkorder))

        calls = {
            "node": get_topology_node(
                GetTopologyNodeInput(project_id="werkorder", name_or_id="Maximo"),
                provider=provider,
                access_token=None,
            ),
            "dependencies": list_topology_dependencies(
                ListTopologyDependenciesInput(
                    project_id="werkorder", name_or_id="Maximo", hops=hops
                ),
                provider=provider,
                access_token=None,
            ),
        }
        for name, call in calls.items():
            result = await call
            size = len(json.dumps(result.as_data()))
            assert size < settings.runtime_tool_result_max_chars, f"{name} is {size} chars"


class TestSchemaDrift:
    async def test_unknown_upstream_fields_are_ignored(self, alitalend) -> None:
        mutated = copy.deepcopy(alitalend)
        mutated["future_family"] = [{"id": "x"}]
        mutated["systems"][0]["future_field"] = {"nested": True}
        mutated["business_topology"]["systems"][0]["future_claim"] = "later"
        provider = make_provider(json_transport(mutated))

        result = await get_topology_overview(
            GetTopologyOverviewInput(project_id="alitalend"), provider=provider, access_token=None
        )

        assert result.content["scanner_facts"]["systems"] == 3

    async def test_missing_optional_fields_fall_back_to_defaults(self) -> None:
        provider = make_provider(
            json_transport({"systems": [{"id": "system:bare"}], "edges": [], "applications": []})
        )

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="bare", name_or_id="system:bare"),
            provider=provider,
            access_token=None,
        )

        assert result.content["node"]["kind"] == "unknown"
        assert result.content["node"]["name"] == "system:bare"

    async def test_channel_and_endpoint_names_are_never_parsed(self) -> None:
        """Queue names, paths and URLs are the likeliest place for a secret."""
        payload = {
            "channels": [{"channel_type": "jms", "channel": "queue://user:hunter2@broker/orders"}],
            "interfaces": [
                {"endpoint_type": "http", "endpoint": "https://user:hunter2@api.internal"}
            ],
        }
        provider = make_provider(json_transport(payload))

        result = await get_topology_overview(
            GetTopologyOverviewInput(project_id="secretive"), provider=provider, access_token=None
        )
        serialized = json.dumps(result.as_data())

        assert result.content["scanner_facts"]["channels_by_type"] == {"jms": 1}
        assert "hunter2" not in serialized
        assert "broker" not in serialized
        assert "api.internal" not in serialized
