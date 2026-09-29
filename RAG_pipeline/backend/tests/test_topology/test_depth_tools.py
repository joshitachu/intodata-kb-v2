"""The job- and component-level topology tools, against real scanner artifacts.

The failure this wave exists to fix was caught by real data and missed by
synthetic data: the landscape rollup for a real composite upload has 43 systems
and NO jobs, so a question about `Quintiq2AssetSys_WorkOrderPickedUpXML` found
nothing and was answered from the document corpus instead. Every test here uses
the real workorders_demo (composite, 14 source roots) or talend_demo (single root)
artifacts for that reason.
"""

from __future__ import annotations

import json

import pytest

from src.config import settings
from src.topology.graph import ComponentScope, build_design_index
from src.topology.models import (
    FindTopologyNodeInput,
    GetComponentDetailInput,
    GetJobDetailInput,
    GetTopologyNodeInput,
    GetTopologyOverviewInput,
    ListTopologyDependenciesInput,
    NavigationIntent,
    ProjectGraph,
)
from src.topology.tools import (
    MAX_JOB_COMPONENTS,
    MAX_MAPPING_EXPRESSIONS,
    MAX_PARAM_TABLE_ROWS,
    MAX_PARAM_TABLES,
    MAX_TARGETS,
    find_topology_node,
    get_component_detail,
    get_job_detail,
    get_topology_node,
    get_topology_overview,
    list_topology_dependencies,
)
from tests.test_topology.conftest import (
    COLLECTOR_JOB,
    COLLECTOR_ROUTING_COMPONENT,
    MAPPING_COMPONENT,
    MAPPING_JOB,
    PHARMA_DEMO_PROJECT,
    QUINTIQ_JOB,
    QUINTIQ_SOURCE_ROOT,
    RICHEST_JOB,
    make_provider,
    workorders_demo_routes,
)


def payload_size(payload) -> int:  # noqa: ANN001 - TopologyToolPayload
    return len(json.dumps(payload.as_data(), default=str))


@pytest.fixture
def workorders_demo_provider(
    workorders_demo,
    workorders_demo_graph,
    quintiq_job_graph,
    richest_job_graph,
    mapping_job_graph,
    richest_component_graph,
):
    """Every workorders_demo artifact this wave reads, on its real URL."""
    transport = workorders_demo_routes(
        landscape=workorders_demo,
        project_graph=workorders_demo_graph,
        job_graphs={
            QUINTIQ_JOB: quintiq_job_graph,
            RICHEST_JOB: richest_job_graph,
            MAPPING_JOB: mapping_job_graph,
        },
        component_graphs={(MAPPING_JOB, MAPPING_COMPONENT): richest_component_graph},
    )
    return transport, make_provider(transport)


class TestTheQuintiqToAssetSysQuestion:
    """The exact question that fell through to the document corpus."""

    async def test_the_job_is_findable_at_all(self, workorders_demo_provider) -> None:
        _transport, provider = workorders_demo_provider

        result = await find_topology_node(
            FindTopologyNodeInput(project_id="workorders_demo", query="Quintiq2AssetSys"),
            provider=provider,
            access_token=None,
        )

        names = {item["name"] for item in result.content["candidates"]}
        assert QUINTIQ_JOB in names
        assert "job" in result.content["matched_kinds"]

    async def test_a_prose_question_finds_the_design_as_a_candidate(
        self, workorders_demo_provider
    ) -> None:
        """'the Quintiq to AssetSys implementation' shares no substring with the name."""
        _transport, provider = workorders_demo_provider

        result = await find_topology_node(
            FindTopologyNodeInput(project_id="workorders_demo", query="Quintiq to AssetSys"),
            provider=provider,
            access_token=None,
        )

        jobs = {item["name"] for item in result.content["candidates"] if item["entity"] == "job"}
        assert jobs, result.content["candidates"]
        assert any("Quintiq" in name and "AssetSys" in name for name in jobs)

    async def test_the_answer_now_comes_from_the_design_manifest_and_says_so(
        self, workorders_demo_provider
    ) -> None:
        transport, provider = workorders_demo_provider

        result = await get_job_detail(
            GetJobDetailInput(project_id="workorders_demo", name_or_id=QUINTIQ_JOB),
            provider=provider,
            access_token=None,
        )

        assert result.content["resolved"] is True
        assert result.content["source"] == "project_design_manifest"
        assert result.content["job"]["name"] == QUINTIQ_JOB
        assert result.content["job"]["source_root"] == QUINTIQ_SOURCE_ROOT
        # Real numbers from the real design, not a description of a rollup.
        assert result.content["component_count"] == 40
        assert result.content["components_by_role"]["input"] > 0
        assert result.content["connection_count"] == 37
        # And it was read from that job's own document.
        assert transport.paths_matching("/jobs/") == [
            f"/mcp/projects/workorders_demo/jobs/{QUINTIQ_JOB}/graph"
        ]

    async def test_the_landscape_rollup_alone_cannot_answer_it(self, workorders_demo) -> None:
        """Why the depth tools exist: the rollup has no node for this job."""
        landscape_only = workorders_demo_routes(landscape=workorders_demo)
        provider = make_provider(landscape_only)

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="workorders_demo", name_or_id=QUINTIQ_JOB),
            provider=provider,
            access_token=None,
        )

        assert result.content["resolved"] is False
        assert result.content["reason"] == "not_found"


class TestNameResolutionWithoutARoundTrip:
    async def test_job_detail_resolves_a_name_without_a_find_call(self, workorders_demo_provider) -> None:
        transport, provider = workorders_demo_provider

        result = await get_job_detail(
            GetJobDetailInput(project_id="workorders_demo", name_or_id=QUINTIQ_JOB),
            provider=provider,
            access_token=None,
        )

        assert result.content["resolved"] is True
        # Two artifact reads, no iteration spent on a separate lookup: the design
        # manifest resolves the name and the job graph supplies the components.
        assert transport.paths == [
            "/mcp/projects/workorders_demo/graph",
            f"/mcp/projects/workorders_demo/jobs/{QUINTIQ_JOB}/graph",
        ]

    async def test_component_detail_resolves_both_names_in_one_call(
        self, workorders_demo_provider
    ) -> None:
        _transport, provider = workorders_demo_provider

        result = await get_component_detail(
            GetComponentDetailInput(
                project_id="workorders_demo", job=MAPPING_JOB, component=MAPPING_COMPONENT
            ),
            provider=provider,
            access_token=None,
        )

        assert result.content["resolved"] is True
        assert result.content["component"]["name"] == MAPPING_COMPONENT

    async def test_a_partial_job_name_still_resolves_when_it_is_unambiguous(
        self, workorders_demo_provider
    ) -> None:
        _transport, provider = workorders_demo_provider

        result = await get_job_detail(
            GetJobDetailInput(project_id="workorders_demo", name_or_id="forSuppliersFourfsVendor"),
            provider=provider,
            access_token=None,
        )

        assert result.content["job"]["name"] == RICHEST_JOB


class TestLazyFetch:
    """A landscape-level question must not open a per-job document.

    Not an optimisation: opening a document a question had no reason to open is
    how an answer ends up sourced from something the user never asked about.
    """

    @pytest.mark.parametrize(
        "call_name",
        ["overview", "node", "dependencies", "find"],
    )
    async def test_no_deep_endpoint_is_called_for_a_landscape_question(
        self, workorders_demo_provider, call_name
    ) -> None:
        transport, provider = workorders_demo_provider
        calls = {
            "overview": lambda: get_topology_overview(
                GetTopologyOverviewInput(project_id="workorders_demo"),
                provider=provider,
                access_token=None,
            ),
            "node": lambda: get_topology_node(
                GetTopologyNodeInput(project_id="workorders_demo", name_or_id="AssetSys"),
                provider=provider,
                access_token=None,
            ),
            "dependencies": lambda: list_topology_dependencies(
                ListTopologyDependenciesInput(project_id="workorders_demo", name_or_id="AssetSys"),
                provider=provider,
                access_token=None,
            ),
            "find": lambda: find_topology_node(
                FindTopologyNodeInput(project_id="workorders_demo", query="AssetSys"),
                provider=provider,
                access_token=None,
            ),
        }

        await calls[call_name]()

        assert transport.paths_matching("/jobs/") == []
        assert transport.paths_matching("/components/") == []

    async def test_the_overview_reads_only_the_landscape(self, workorders_demo_provider) -> None:
        transport, provider = workorders_demo_provider

        await get_topology_overview(
            GetTopologyOverviewInput(project_id="workorders_demo"),
            provider=provider,
            access_token=None,
        )

        assert transport.paths == ["/mcp/projects/workorders_demo/landscape"]

    async def test_component_search_only_opens_a_job_when_scoped_to_one(
        self, workorders_demo_provider
    ) -> None:
        transport, provider = workorders_demo_provider

        unscoped = await find_topology_node(
            FindTopologyNodeInput(project_id="workorders_demo", query="tMap_1"),
            provider=provider,
            access_token=None,
        )
        assert transport.paths_matching("/jobs/") == []
        assert (
            unscoped.content["design_coverage"]["component_scope"]
            == ComponentScope.MANIFEST_FACTS.value
        )

        scoped = await find_topology_node(
            FindTopologyNodeInput(project_id="workorders_demo", query="tMap_1", job=MAPPING_JOB),
            provider=provider,
            access_token=None,
        )
        assert transport.paths_matching("/jobs/") == [
            f"/mcp/projects/workorders_demo/jobs/{MAPPING_JOB}/graph"
        ]
        assert (
            scoped.content["design_coverage"]["component_scope"] == ComponentScope.JOB_GRAPH.value
        )
        assert scoped.content["design_coverage"]["component_scope_job"] == MAPPING_JOB


class TestCapsOnTheRichestRealArtifacts:
    """The caps, not the size backstop, must be what bounds a depth answer."""

    async def test_the_richest_real_job_stays_inside_the_result_ceiling(
        self, workorders_demo_provider
    ) -> None:
        transport, provider = workorders_demo_provider

        result = await get_job_detail(
            GetJobDetailInput(project_id="workorders_demo", name_or_id=RICHEST_JOB),
            provider=provider,
            access_token=None,
        )
        size = payload_size(result)

        # 251 components and 201 connections, 142 KB of raw artifact.
        assert result.content["component_count"] == 251
        assert len(result.content["components"]) == MAX_JOB_COMPONENTS
        assert result.content["components_truncated"] is True
        assert size < settings.runtime_tool_result_max_chars, f"{size} chars"
        assert len(result.as_data()["targets"]) <= MAX_TARGETS

    async def test_the_richest_real_mapping_component_stays_inside_the_ceiling(
        self, workorders_demo_provider
    ) -> None:
        _transport, provider = workorders_demo_provider

        result = await get_component_detail(
            GetComponentDetailInput(
                project_id="workorders_demo", job=MAPPING_JOB, component=MAPPING_COMPONENT
            ),
            provider=provider,
            access_token=None,
        )
        size = payload_size(result)

        # 27 KB of raw mapping artifact, the largest in the whole upload.
        assert result.content["mapping_available"] is True
        assert len(result.content["mapping_expressions"]) <= MAX_MAPPING_EXPRESSIONS
        assert size < settings.runtime_tool_result_max_chars, f"{size} chars"

    async def test_resolved_depth_subjects_are_primary_with_supporting_context(
        self, workorders_demo_provider
    ) -> None:
        _transport, provider = workorders_demo_provider

        job = await get_job_detail(
            GetJobDetailInput(project_id="workorders_demo", name_or_id=QUINTIQ_JOB),
            provider=provider,
            access_token=None,
        )
        component = await get_component_detail(
            GetComponentDetailInput(
                project_id="workorders_demo", job=MAPPING_JOB, component=MAPPING_COMPONENT
            ),
            provider=provider,
            access_token=None,
        )

        assert job.targets[0]["visual_role"] == "primary"
        assert all(target["visual_role"] == "supporting" for target in job.targets[1:])
        assert component.targets[0]["visual_role"] == "primary"
        assert component.targets[1]["visual_role"] == "supporting"

    async def test_mapping_visual_detail_is_bounded_and_scanner_authored(
        self, workorders_demo_provider
    ) -> None:
        _transport, provider = workorders_demo_provider

        result = await get_component_detail(
            GetComponentDetailInput(
                project_id="workorders_demo", job=MAPPING_JOB, component=MAPPING_COMPONENT
            ),
            provider=provider,
            access_token=None,
        )

        detail = result.targets[0]["visual_detail"]
        assert detail["kind"] == "mapping"
        assert 0 < len(detail["tables"]) <= 8
        assert 0 < len(detail["columns"]) <= MAX_MAPPING_EXPRESSIONS
        assert len(detail["edges"]) <= MAX_MAPPING_EXPRESSIONS
        assert all(len(token) <= 200 for token in detail["tables"])
        assert all(
            len(column["table"]) <= 200 and len(column["column"]) <= 200
            for column in detail["columns"]
        )
        assert all(set(edge) == {"target_table", "target_column"} for edge in detail["edges"])

    async def test_component_without_mapping_has_no_visual_detail(
        self, workorders_demo_provider, quintiq_job_graph
    ) -> None:
        _transport, provider = workorders_demo_provider
        plain = next(node for node in quintiq_job_graph["nodes"] if not node["has_subgraph"])

        result = await get_component_detail(
            GetComponentDetailInput(
                project_id="workorders_demo", job=QUINTIQ_JOB, component=plain["unique_name"]
            ),
            provider=provider,
            access_token=None,
        )

        assert "visual_detail" not in result.targets[0]

    async def test_mapping_columns_are_a_string_not_a_list_at_that_depth(
        self, workorders_demo_provider
    ) -> None:
        """A leaf list this deep is replaced by a depth marker in the envelope."""
        _transport, provider = workorders_demo_provider

        result = await get_component_detail(
            GetComponentDetailInput(
                project_id="workorders_demo", job=MAPPING_JOB, component=MAPPING_COMPONENT
            ),
            provider=provider,
            access_token=None,
        )

        for table in result.content["mapping_tables"]:
            assert isinstance(table["columns"], str)
            assert isinstance(table["column_count"], int)

    async def test_a_job_answer_reports_the_real_totals_it_elided(self, workorders_demo_provider) -> None:
        _transport, provider = workorders_demo_provider

        result = await get_job_detail(
            GetJobDetailInput(project_id="workorders_demo", name_or_id=RICHEST_JOB),
            provider=provider,
            access_token=None,
        )

        listed = sum(result.content["components_by_role"].values())
        assert listed == result.content["component_count"]
        assert result.content["connection_count"] == 201
        assert result.truncated is True


class TestComponentSettingsAreReadable:
    """The measured failure: 41 tool calls, 15 of them one component at a time.

    An agent asked which criterion routes a shipment to the Mediq or the
    Distrimed flow. It opened `cSetHeader_5` -- the component that defines the
    routing variable -- got nothing usable back, and swept on. The criterion is
    a ROW of that component's VALUES table, and rows reached neither the MCP
    node nor this projection, so the whole answer was invisible while the
    component that held it had already been read.
    """

    async def test_the_routing_criterion_comes_back_from_one_component_call(
        self, pharma_demo_provider
    ) -> None:
        _transport, provider = pharma_demo_provider

        result = await get_component_detail(
            GetComponentDetailInput(
                project_id=PHARMA_DEMO_PROJECT,
                job=COLLECTOR_JOB,
                component=COLLECTOR_ROUTING_COMPONENT,
            ),
            provider=provider,
            access_token=None,
        )
        rows = result.content["component"]["parameter_tables"]["VALUES"]

        assert result.content["resolved"] is True
        # The name of the routing variable and the field it is read from, which
        # is what makes the answer nameable rather than "undeterminable".
        assert any("AN8_CHECK" in row and "SZAN8" in row for row in rows), rows

    async def test_a_component_whose_whole_config_is_rows_is_not_reported_as_empty(
        self, pharma_demo_provider
    ) -> None:
        """`cSetHeader_5` has an EMPTY param_summary: all of it is in rows.

        Testing the scalar half alone reports exactly the components that hold
        the answer as having nothing configured.
        """
        _transport, provider = pharma_demo_provider

        result = await get_component_detail(
            GetComponentDetailInput(
                project_id=PHARMA_DEMO_PROJECT,
                job=COLLECTOR_JOB,
                component=COLLECTOR_ROUTING_COMPONENT,
            ),
            provider=provider,
            access_token=None,
        )
        component = result.content["component"]

        assert component["parameters"] == {}
        assert component["has_settings"] is True
        assert component["parameter_tables"]

    async def test_one_batched_page_replaces_a_component_by_component_sweep(
        self, pharma_demo_provider
    ) -> None:
        """Four paged calls cover all 41 components' settings, not 41 calls."""
        _transport, provider = pharma_demo_provider

        offset: int | None = 0
        pages = 0
        settings_by_component: dict[str, dict] = {}
        while offset is not None:
            page = await get_job_detail(
                GetJobDetailInput(
                    project_id=PHARMA_DEMO_PROJECT,
                    name_or_id=COLLECTOR_JOB,
                    include_parameters=True,
                    offset=offset,
                ),
                provider=provider,
                access_token=None,
            )
            pages += 1
            for item in page.content["components"]:
                if "parameter_tables" in item or item.get("parameters"):
                    settings_by_component[item["name"]] = item
            offset = page.content["components_next_offset"]
            assert pages < 10, "paging did not terminate"

        assert pages == 4, f"{pages} pages for 41 components"
        rows = settings_by_component[COLLECTOR_ROUTING_COMPONENT]["parameter_tables"]["VALUES"]
        assert any("AN8_CHECK" in row for row in rows)
        # And the SQL and JMS destinations of the same design came along for
        # free, which is the other half of the sweep the agent paid for.
        assert "cSQL_1" in settings_by_component
        assert "cJMS_1" in settings_by_component

    async def test_a_plain_listing_points_at_the_batch_instead_of_a_sweep(
        self, pharma_demo_provider
    ) -> None:
        _transport, provider = pharma_demo_provider

        result = await get_job_detail(
            GetJobDetailInput(project_id=PHARMA_DEMO_PROJECT, name_or_id=COLLECTOR_JOB),
            provider=provider,
            access_token=None,
        )

        assert result.content["component_settings_included"] is False
        assert "include_parameters=true" in result.content["component_settings_note"]
        # And the listing says WHICH components are worth opening at all.
        assert any(item["has_settings"] for item in result.content["components"])

    async def test_every_batched_page_of_the_richest_designs_fits_the_ceiling(
        self, workorders_demo_provider, pharma_demo_provider
    ) -> None:
        """Overrunning is not a soft failure: the backstop drops every list."""
        _transport, workorders_demo = workorders_demo_provider
        _pluri_transport, pharma_demo = pharma_demo_provider
        calls = [
            (workorders_demo, "workorders_demo", RICHEST_JOB),
            (workorders_demo, "workorders_demo", QUINTIQ_JOB),
            (pharma_demo, PHARMA_DEMO_PROJECT, COLLECTOR_JOB),
        ]

        for provider, project_id, job_name in calls:
            for offset in (0, 12, 24):
                result = await get_job_detail(
                    GetJobDetailInput(
                        project_id=project_id,
                        name_or_id=job_name,
                        include_parameters=True,
                        offset=offset,
                    ),
                    provider=provider,
                    access_token=None,
                )
                size = payload_size(result)
                assert size < settings.runtime_tool_result_max_chars, (
                    f"{job_name} offset {offset}: {size} chars"
                )

    async def test_settings_that_do_not_fit_are_counted_not_dropped(
        self, pharma_demo_graph, collector_job_graph
    ) -> None:
        """Forty rows on every component of a 41-component design."""
        rows = [
            {
                "NAME": f'"VAR_{index}"',
                "LANGUAGE": "xpath",
                "EXPRESSION": f'xpath("/A/B/{"segment" * 10}/{index}/text()",String.class)',
            }
            for index in range(40)
        ]
        overloaded = {
            **collector_job_graph,
            "nodes": [
                {
                    **node,
                    "param_tables": [
                        {
                            "name": "VALUES",
                            "columns": ["NAME", "LANGUAGE", "EXPRESSION"],
                            "rows": rows,
                            "row_count": len(rows),
                        }
                    ],
                }
                for node in collector_job_graph["nodes"]
            ],
        }
        provider = make_provider(
            workorders_demo_routes(
                landscape={},
                project_graph=pharma_demo_graph,
                job_graphs={COLLECTOR_JOB: overloaded},
                project_id=PHARMA_DEMO_PROJECT,
            )
        )

        result = await get_job_detail(
            GetJobDetailInput(
                project_id=PHARMA_DEMO_PROJECT, name_or_id=COLLECTOR_JOB, include_parameters=True
            ),
            provider=provider,
            access_token=None,
        )
        shown = [item for item in result.content["components"] if "parameter_tables" in item]

        assert payload_size(result) < settings.runtime_tool_result_max_chars
        # Every component on the page is still listed; only the settings gave way.
        assert len(result.content["components"]) == MAX_JOB_COMPONENTS
        assert result.content["component_settings_omitted"] == MAX_JOB_COMPONENTS - len(shown)
        assert "get_component_detail" in result.content["component_settings_omitted_note"]
        for item in shown:
            note = item["parameter_table_notes"]["VALUES"]
            assert note.endswith("of 40 rows shown"), note

    async def test_a_truncated_table_says_so_rather_than_reading_as_a_short_one(
        self, pharma_demo_graph, collector_job_graph
    ) -> None:
        rows = [{"NAME": f'"VAR_{index}"', "VALUE": f'"v{index}"'} for index in range(40)]
        node = {
            **collector_job_graph["nodes"][0],
            "param_tables": [
                {
                    "name": "VALUES",
                    "columns": ["NAME", "VALUE"],
                    "rows": rows[:12],
                    "row_count": 40,
                    "rows_omitted": 26,
                    "rows_redacted": 2,
                }
            ],
        }
        provider = make_provider(
            workorders_demo_routes(
                landscape={},
                project_graph=pharma_demo_graph,
                job_graphs={COLLECTOR_JOB: {**collector_job_graph, "nodes": [node]}},
                project_id=PHARMA_DEMO_PROJECT,
            )
        )

        result = await get_component_detail(
            GetComponentDetailInput(
                project_id=PHARMA_DEMO_PROJECT,
                job=COLLECTOR_JOB,
                component=node["unique_name"],
            ),
            provider=provider,
            access_token=None,
        )
        component = result.content["component"]

        assert len(component["parameter_tables"]["VALUES"]) == MAX_PARAM_TABLE_ROWS
        # Both counts are against the SCAN's 40 rows, never against what upstream
        # already trimmed: a filtered table that reports its own length is
        # indistinguishable from a complete short one.
        assert component["parameter_table_notes"]["VALUES"] == (
            f"{MAX_PARAM_TABLE_ROWS} of 40 rows shown; "
            "2 withheld by the credential filter"
        )

    async def test_the_richest_mapping_component_with_tables_fits_the_ceiling(
        self, workorders_demo, workorders_demo_graph, mapping_job_graph, richest_component_graph
    ) -> None:
        """Worst case for the single-component read: 27 KB of mapping + tables."""
        rows = [
            {
                "NAME": f'"VAR_{index}"',
                "LANGUAGE": "xpath",
                "EXPRESSION": f'xpath("/A/{"segment" * 20}/{index}/text()",String.class)',
            }
            for index in range(60)
        ]
        overloaded = {
            **mapping_job_graph,
            "nodes": [
                {
                    **node,
                    "param_tables": [
                        {
                            "name": f"TABLE_{table}",
                            "columns": ["NAME", "LANGUAGE", "EXPRESSION"],
                            "rows": rows,
                            "row_count": len(rows),
                        }
                        for table in range(5)
                    ],
                }
                for node in mapping_job_graph["nodes"]
            ],
        }
        provider = make_provider(
            workorders_demo_routes(
                landscape=workorders_demo,
                project_graph=workorders_demo_graph,
                job_graphs={MAPPING_JOB: overloaded},
                component_graphs={(MAPPING_JOB, MAPPING_COMPONENT): richest_component_graph},
            )
        )

        result = await get_component_detail(
            GetComponentDetailInput(
                project_id="workorders_demo", job=MAPPING_JOB, component=MAPPING_COMPONENT
            ),
            provider=provider,
            access_token=None,
        )
        component = result.content["component"]

        assert payload_size(result) < settings.runtime_tool_result_max_chars
        assert len(component["parameter_tables"]) <= MAX_PARAM_TABLES
        assert component["parameter_tables_omitted"] >= 1
        assert result.content["mapping_available"] is True

    async def test_rows_survive_the_envelope_depth_limit_in_a_batched_page(
        self, pharma_demo_provider
    ) -> None:
        """The deepest shape this package produces, through the real sanitizer.

        `data -> content -> components -> component -> parameter_tables -> rows`
        puts a row exactly at the envelope's last usable level. Nesting a row's
        own columns one level deeper -- the obvious shape -- would put every cell
        past the limit and replace the answer with a depth marker, silently.
        """
        from src.harness.semantic_tools import _sanitize_value

        _transport, provider = pharma_demo_provider
        result = await get_job_detail(
            GetJobDetailInput(
                project_id=PHARMA_DEMO_PROJECT,
                name_or_id=COLLECTOR_JOB,
                include_parameters=True,
                # The page `_component_order` puts cSetHeader_5 on (index 32).
                offset=24,
            ),
            provider=provider,
            access_token=None,
        )

        sanitized = _sanitize_value(result.as_data(), depth=0)
        matched = [
            item
            for item in sanitized["content"]["components"]
            if item["name"] == COLLECTOR_ROUTING_COMPONENT
        ]
        assert matched, [item["name"] for item in sanitized["content"]["components"]]
        rows = matched[0]["parameter_tables"]["VALUES"]

        assert all(isinstance(row, str) for row in rows), rows
        assert any("AN8_CHECK" in row and "SZAN8" in row for row in rows), rows

    async def test_a_design_whose_scan_predates_the_field_still_answers(
        self, workorders_demo_provider
    ) -> None:
        """MCP may publish no `param_tables` at all; that is not an error."""
        _transport, provider = workorders_demo_provider

        result = await get_job_detail(
            GetJobDetailInput(
                project_id="workorders_demo", name_or_id=QUINTIQ_JOB, include_parameters=True
            ),
            provider=provider,
            access_token=None,
        )
        listed = result.content["components"]

        assert result.content["component_settings_included"] is True
        assert any(item.get("parameters") for item in listed)
        assert all("parameter_tables" not in item for item in listed)


class TestAmbiguityAcrossEntityKinds:
    async def test_candidates_name_their_kind_and_their_source_root(
        self, workorders_demo_provider
    ) -> None:
        _transport, provider = workorders_demo_provider

        result = await find_topology_node(
            FindTopologyNodeInput(project_id="workorders_demo", query="AssetSys"),
            provider=provider,
            access_token=None,
        )

        candidates = result.content["candidates"]
        assert len({item["entity"] for item in candidates}) > 1, candidates
        for item in candidates:
            assert item["entity"] in {"system", "application", "group", "job", "component"}
            if item["entity"] in {"group", "job", "component"}:
                # Present even when empty: an absent key would read as "there is
                # only one copy" rather than "this scan is flat".
                assert "source_root" in item

    async def test_one_kind_cannot_monopolise_the_candidate_list(self, workorders_demo_provider) -> None:
        """115 designs match "assetsys"; the systems must not be buried by them."""
        _transport, provider = workorders_demo_provider

        result = await find_topology_node(
            FindTopologyNodeInput(project_id="workorders_demo", query="AssetSys"),
            provider=provider,
            access_token=None,
        )

        kinds = {item["entity"] for item in result.content["candidates"]}
        assert "system" in kinds
        assert "job" in kinds

    async def test_the_composite_partitions_are_distinguishable(self, workorders_demo_provider) -> None:
        """ "References" is a real group name in five of the 14 source roots."""
        _transport, provider = workorders_demo_provider

        result = await find_topology_node(
            FindTopologyNodeInput(project_id="workorders_demo", query="REFERENCES"),
            provider=provider,
            access_token=None,
        )

        groups = [item for item in result.content["candidates"] if item["entity"] == "group"]
        assert len(groups) > 1
        assert len({item["name"] for item in groups}) == len(groups)

    async def test_a_source_root_pins_which_copy_of_a_design_is_meant(
        self, workorders_demo_provider
    ) -> None:
        _transport, provider = workorders_demo_provider

        wrong_root = await get_job_detail(
            GetJobDetailInput(
                project_id="workorders_demo",
                name_or_id=QUINTIQ_JOB,
                source_root="ClientA_Workorder/Talend exports/ETTM.__intodoc_zip__/ERP_TO_THE_MAX",
            ),
            provider=provider,
            access_token=None,
        )
        assert wrong_root.content["resolved"] is False
        assert wrong_root.content["reason"] == "not_found"

        right_root = await get_job_detail(
            GetJobDetailInput(
                project_id="workorders_demo", name_or_id=QUINTIQ_JOB, source_root=QUINTIQ_SOURCE_ROOT
            ),
            provider=provider,
            access_token=None,
        )
        assert right_root.content["resolved"] is True

    async def test_two_scanner_systems_with_one_name_still_both_appear(
        self, talend_demo, talend_demo_graph
    ) -> None:
        """The single-root case: talend_demo has two distinct systems named "Log"."""
        transport = workorders_demo_routes(
            landscape=talend_demo, project_graph=talend_demo_graph, project_id="talend_demo"
        )

        result = await find_topology_node(
            FindTopologyNodeInput(project_id="talend_demo", query="Log"),
            provider=make_provider(transport),
            access_token=None,
        )

        systems = [item for item in result.content["candidates"] if item["entity"] == "system"]
        assert len({item["id"] for item in systems}) >= 2


class TestTargetsForTheDeeperLevels:
    async def test_a_component_target_carries_job_component_and_source_root(
        self, workorders_demo_provider
    ) -> None:
        _transport, provider = workorders_demo_provider

        result = await get_job_detail(
            GetJobDetailInput(project_id="workorders_demo", name_or_id=QUINTIQ_JOB),
            provider=provider,
            access_token=None,
        )
        component = next(
            target for target in result.targets if target["level"]["kind"] == "component"
        )

        assert component["level"]["jobName"] == QUINTIQ_JOB
        assert component["level"]["componentName"]
        assert component["level"]["sourceRoot"] == QUINTIQ_SOURCE_ROOT
        assert set(component["level"]) == {"kind", "jobName", "componentName", "sourceRoot"}

    async def test_a_job_target_uses_the_manifest_name_and_root(self, workorders_demo_provider) -> None:
        _transport, provider = workorders_demo_provider

        result = await get_job_detail(
            GetJobDetailInput(project_id="workorders_demo", name_or_id=QUINTIQ_JOB),
            provider=provider,
            access_token=None,
        )

        assert result.targets[0]["level"] == {
            "kind": "job",
            "jobName": QUINTIQ_JOB,
            "sourceRoot": QUINTIQ_SOURCE_ROOT,
        }

    async def test_a_group_target_uses_the_canvas_group_name(self, workorders_demo_provider) -> None:
        """`topologyGroupForJob`'s value, not the source root or a display label."""
        _transport, provider = workorders_demo_provider

        result = await get_job_detail(
            GetJobDetailInput(project_id="workorders_demo", name_or_id=QUINTIQ_JOB),
            provider=provider,
            access_token=None,
        )
        group = next(target for target in result.targets if target["level"]["kind"] == "group")

        assert group["level"]["groupName"] == result.content["job"]["group"]
        assert group["level"]["sourceRoot"] == QUINTIQ_SOURCE_ROOT

    async def test_an_absent_source_root_is_omitted_never_null(
        self, talend_demo, talend_demo_graph, talend_demo_job_graph
    ) -> None:
        transport = workorders_demo_routes(
            landscape=talend_demo,
            project_graph=talend_demo_graph,
            job_graphs={"InspectHub_SendWorkOrderUpdate": talend_demo_job_graph},
            project_id="talend_demo",
        )

        result = await get_job_detail(
            GetJobDetailInput(project_id="talend_demo", name_or_id="InspectHub_SendWorkOrderUpdate"),
            provider=make_provider(transport),
            access_token=None,
        )

        for target in result.targets:
            level = target["level"]
            assert "sourceRoot" not in level or isinstance(level["sourceRoot"], str)
            assert None not in level.values()

    async def test_a_run_job_portal_offers_the_child_design(
        self, workorders_demo_provider, mapping_job_graph
    ) -> None:
        """A tRunJob component IS a hop to another design; offer that design.

        The child is named by the scan but is not in this fixture's manifest, so
        its target carries no source root — which is correct: declaring one the
        manifest cannot confirm would filter the canvas to nothing.
        """
        _transport, provider = workorders_demo_provider
        portal = next(node for node in mapping_job_graph["nodes"] if node.get("subjob_target"))

        result = await get_component_detail(
            GetComponentDetailInput(
                project_id="workorders_demo", job=MAPPING_JOB, component=portal["unique_name"]
            ),
            provider=provider,
            access_token=None,
        )

        assert result.content["component"]["runs_job"] == portal["subjob_target"]
        assert any(
            target["level"] == {"kind": "job", "jobName": portal["subjob_target"]}
            for target in result.targets
        )


class TestRelationshipTargets:
    async def test_a_system_pair_view_is_produced_and_well_formed(self, workorders_demo) -> None:
        """ "go back to the view between AssetSys and Quintiq" needs this variant."""
        provider = make_provider(workorders_demo_routes(landscape=workorders_demo))

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="workorders_demo", name_or_id="AssetSys"),
            provider=provider,
            access_token=None,
        )
        relationships = [
            target
            for target in result.as_data()["targets"]
            if target["level"]["selection"]["kind"] == "relationship"
        ]

        assert relationships
        for target in relationships:
            selection = target["level"]["selection"]
            assert set(selection) == {"kind", "source", "target", "streamKeys"}
            assert selection["streamKeys"]
            assert all(isinstance(key, str) and key for key in selection["streamKeys"])
            assert target["intent"] == "reference"

    async def test_the_quintiq_assetsys_pair_is_one_of_them(self, workorders_demo) -> None:
        provider = make_provider(workorders_demo_routes(landscape=workorders_demo))

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="workorders_demo", name_or_id="AssetSys"),
            provider=provider,
            access_token=None,
        )
        labels = {
            target["label"]
            for target in result.targets
            if target["level"]["selection"]["kind"] == "relationship"
        }

        assert any("Quintiq" in label for label in labels), labels

    async def test_no_pair_view_in_the_scanner_view(self, workorders_demo) -> None:
        """The canvas only aggregates pairs in the overlay view.

        A pair target built from the scanner view would point at a node the
        diagram never draws, which is the same class of defect as emitting an
        overlay id while the scanner graph is rendered.
        """
        scanner = {
            **workorders_demo,
            "topology_state": {**workorders_demo["topology_state"], "validated": False},
        }
        provider = make_provider(workorders_demo_routes(landscape=scanner))

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="workorders_demo", name_or_id="AssetSys"),
            provider=provider,
            access_token=None,
        )

        assert all(
            target["level"]["selection"]["kind"] != "relationship" for target in result.targets
        )

    async def test_pair_views_survive_the_target_cap_on_a_hub(self, workorders_demo) -> None:
        """A hub has more edges than the cap; the pair views must not be dropped."""
        provider = make_provider(workorders_demo_routes(landscape=workorders_demo))

        result = await get_topology_node(
            GetTopologyNodeInput(project_id="workorders_demo", name_or_id="AssetSys"),
            provider=provider,
            access_token=None,
        )
        kinds = [target["level"]["selection"]["kind"] for target in result.as_data()["targets"]]

        assert "relationship" in kinds


class TestNavigationIntent:
    async def test_the_default_is_reference(self, workorders_demo_provider) -> None:
        _transport, provider = workorders_demo_provider

        result = await get_job_detail(
            GetJobDetailInput(project_id="workorders_demo", name_or_id=QUINTIQ_JOB),
            provider=provider,
            access_token=None,
        )

        assert all(target["intent"] == "reference" for target in result.targets)

    async def test_navigate_is_stamped_on_every_target_of_the_call(
        self, workorders_demo_provider
    ) -> None:
        _transport, provider = workorders_demo_provider

        result = await get_job_detail(
            GetJobDetailInput(
                project_id="workorders_demo", name_or_id=QUINTIQ_JOB, navigation_intent="navigate"
            ),
            provider=provider,
            access_token=None,
        )

        assert result.targets
        assert all(target["intent"] == "navigate" for target in result.targets)

    @pytest.mark.parametrize(
        "value",
        ["Navigate", "NAVIGATE", " navigate", "go", "true", "", None, 1],
    )
    def test_an_unrecognized_value_never_becomes_navigate(self, value) -> None:
        """Coerced, not rejected: a rejection would cost a loop iteration, and a
        coercion to navigate would move the user's canvas unbidden."""
        params = GetJobDetailInput(
            project_id="workorders_demo", name_or_id=QUINTIQ_JOB, navigation_intent=value
        )

        assert params.navigation_intent is NavigationIntent.REFERENCE

    def test_the_exact_string_is_accepted(self) -> None:
        params = GetJobDetailInput(
            project_id="workorders_demo", name_or_id=QUINTIQ_JOB, navigation_intent="navigate"
        )

        assert params.navigation_intent is NavigationIntent.NAVIGATE

    def test_omitting_it_yields_reference(self) -> None:
        assert (
            GetTopologyOverviewInput(project_id="workorders_demo").navigation_intent
            is NavigationIntent.REFERENCE
        )


class TestHonestyAboutEmptySources:
    async def test_a_component_without_a_mapping_graph_says_so_explicitly(
        self, workorders_demo_provider, quintiq_job_graph
    ) -> None:
        """Most components have no deeper graph. That is not "no transformations"."""
        _transport, provider = workorders_demo_provider
        plain = next(node for node in quintiq_job_graph["nodes"] if not node["has_subgraph"])

        result = await get_component_detail(
            GetComponentDetailInput(
                project_id="workorders_demo", job=QUINTIQ_JOB, component=plain["unique_name"]
            ),
            provider=provider,
            access_token=None,
        )

        assert result.content["resolved"] is True
        assert result.content["mapping_available"] is False
        assert result.content["mapping_reason"] == "no_deeper_graph"
        assert "no column-level mapping graph" in result.content["mapping_note"]

    async def test_the_documented_upstream_fallback_is_not_an_error(
        self, workorders_demo, workorders_demo_graph, mapping_job_graph
    ) -> None:
        """Upstream signals "no deeper graph" as a 404 with a fallback body."""
        transport = workorders_demo_routes(
            landscape=workorders_demo,
            project_graph=workorders_demo_graph,
            job_graphs={MAPPING_JOB: mapping_job_graph},
        )
        # The component graph route is deliberately absent, so it 404s.
        result = await get_component_detail(
            GetComponentDetailInput(
                project_id="workorders_demo", job=MAPPING_JOB, component=MAPPING_COMPONENT
            ),
            provider=make_provider(transport),
            access_token=None,
        )

        assert result.content["resolved"] is True
        assert result.content["mapping_available"] is False
        assert result.content["mapping_reason"] in {"no_deeper_graph", "unreadable"}

    async def test_a_missing_design_manifest_is_reported_not_papered_over(self, workorders_demo) -> None:
        """No manifest means no jobs. Never answer from the rollup instead."""
        provider = make_provider(workorders_demo_routes(landscape=workorders_demo))

        result = await get_job_detail(
            GetJobDetailInput(project_id="workorders_demo", name_or_id=QUINTIQ_JOB),
            provider=provider,
            access_token=None,
        )

        assert result.content["resolved"] is False
        assert result.content["reason"] == "design_manifest_unavailable"
        assert "no published design manifest" in result.content["note"]
        assert result.targets == []

    async def test_find_still_answers_from_the_landscape_and_declares_the_gap(
        self, workorders_demo
    ) -> None:
        provider = make_provider(workorders_demo_routes(landscape=workorders_demo))

        result = await find_topology_node(
            FindTopologyNodeInput(project_id="workorders_demo", query="AssetSys"),
            provider=provider,
            access_token=None,
        )

        assert result.content["candidates"], "landscape matches are still valid"
        coverage = result.content["design_coverage"]
        assert coverage["available"] is False
        assert "cannot be searched" in coverage["reason"]

    async def test_a_named_design_that_is_not_in_the_scan_is_not_found(
        self, workorders_demo_provider
    ) -> None:
        _transport, provider = workorders_demo_provider

        result = await get_job_detail(
            GetJobDetailInput(project_id="workorders_demo", name_or_id="NoSuchDesign_v9"),
            provider=provider,
            access_token=None,
        )

        assert result.content["resolved"] is False
        assert result.content["reason"] == "not_found"


class TestHealthRelevantDesignFacts:
    async def test_design_facts_are_labelled_as_design_time_only(self, workorders_demo_provider) -> None:
        """Structure is not observed state; conflating them is the labelling defect."""
        _transport, provider = workorders_demo_provider

        result = await get_job_detail(
            GetJobDetailInput(project_id="workorders_demo", name_or_id=QUINTIQ_JOB),
            provider=provider,
            access_token=None,
        )
        health = result.content["health_relevant"]

        assert "no run has been observed" in health["note"]
        assert health["control_flow_count"] + health["data_flow_count"] == 37
        assert health["logging_components"]


class TestDesignIndex:
    def test_group_naming_mirrors_the_canvas_rule(self, workorders_demo_graph) -> None:
        graph = ProjectGraph.model_validate(workorders_demo_graph)
        index = build_design_index(graph)

        job = index.job(QUINTIQ_JOB)
        assert job is not None
        assert job.group == graph.manifest.job_groups[QUINTIQ_JOB]
        assert job.source_root == QUINTIQ_SOURCE_ROOT

    def test_a_flat_scan_yields_jobs_without_source_roots(self, talend_demo_graph) -> None:
        index = build_design_index(ProjectGraph.model_validate(talend_demo_graph))

        assert index.jobs
        assert all(job.group for job in index.jobs)

    def test_an_unreadable_manifest_shape_still_indexes_what_it_can(self) -> None:
        """MCP owns these artifacts and adds fields; a partial one must not error."""
        index = build_design_index(
            ProjectGraph.model_validate(
                {"manifest": {"job_groups": {"only": "grp"}, "future_key": [1, 2]}}
            )
        )

        assert [job.name for job in index.jobs] == ["only"]
        assert index.component_scope is ComponentScope.NONE
