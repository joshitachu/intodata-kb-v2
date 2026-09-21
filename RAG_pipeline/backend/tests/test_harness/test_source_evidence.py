"""Scan-source evidence: hits, honest misses, caps, and path safety.

Every test but the last runs against a fixture tree built in this file. The
live scan is 126 MB of one client's integration estate; asserting against it
would make the suite depend on a mount, on that client still being scanned, and
on nobody re-running the scanner. The one test that does use it is the
regression that motivated the capability, and it skips when the mount is absent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from src.harness.capability_runtime import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    CapabilityHandlerServices,
)
from src.harness.models import (
    SOURCE_COMPONENT_READ_CAPABILITY,
    SOURCE_SEARCH_CAPABILITY,
    HarnessRiskTier,
)
from src.harness.registry import base_capability_registry
from src.harness.source_evidence import (
    SCAN_ROOT_ENV,
    read_component_evidence,
    search_source_evidence,
    source_evidence_capability_handlers,
)

PROJECT = "fixture-project"

#: The real parameter from the real scan, byte for byte. The point of the
#: capability is that this survives redaction intact and reaches the model
#: unaltered, so the fixture must not be a paraphrase of it.
FMD_URI = (
    '"sql:SELECT COUNT(ABAN8) FROM "+ context.jdeTableF0101 +'
    '" WHERE ABAN8=CAST(:#ADDRESS_KEY AS NUMBER) AND ABCLASS01 = \'FMD\''
    '?dataSource=#cSQLConnection_1&outputType=SelectOne&outputHeader=createFMD"'
)


def _job(
    name: str,
    *,
    components: list[dict[str, Any]],
    connections: list[dict[str, Any]] | None = None,
    path: str | None = None,
    context_groups: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "job_id": name,
        "path": path or f"Processors/{name}.item",
        "purpose": f"fixture job {name}",
        "components": components,
        "connections": connections or [],
        "context_groups": context_groups or [],
        "is_esb_route": True,
    }


def _component(
    unique_name: str,
    component_type: str,
    params: list[tuple[str, str]],
    **extra: Any,
) -> dict[str, Any]:
    component: dict[str, Any] = {
        "unique_name": unique_name,
        "component_type": component_type,
        "params": [{"name": name, "value": value} for name, value in params],
        "code": extra.pop("code", None),
        # Empty on every component in every real scan; the reader must derive
        # edges from the job's connection list instead.
        "incoming_connections": [],
        "outgoing_connections": [],
        "schemas": extra.pop("schemas", []),
        "active": extra.pop("active", True),
    }
    component.update(extra)
    return component


@pytest.fixture
def scan_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A miniature scan with the shape the real one has.

    Two jobs share the name ``shared_name`` under different repository
    directories, because the real tree does: pluripharm holds 482 shards under
    445 distinct names, and a reader that resolves a name to the first match
    silently answers from the wrong repository.
    """

    jobs = tmp_path / PROJECT / "scan" / "jobs"
    repo_a = jobs / "RepoA.__intodoc_repo___A__Processors-aaaa"
    repo_b = jobs / "RepoB.__intodoc_repo___B__Processors-bbbb"
    repo_a.mkdir(parents=True)
    repo_b.mkdir(parents=True)

    fmd_job = _job(
        "processor_shipping_confirmation",
        components=[
            _component(
                "cMessagingEndpoint_15",
                "cMessagingEndpoint",
                [
                    ("URI", FMD_URI),
                    ("USE_CUSTOM_COMPONENT", "false"),
                    ("LABEL", "determineFMD"),
                ],
                schemas=[
                    {
                        "connector": "FLOW",
                        "name": "cMessagingEndpoint_15",
                        "columns": [
                            {
                                "name": "ADDRESS_KEY",
                                "type": "id_String",
                                "source_type": "VARCHAR",
                                "key": True,
                                "nullable": False,
                            }
                        ],
                    }
                ],
            ),
            _component(
                "cSetHeader_16",
                "cSetHeader",
                [("LABEL", "checkIfFMDNeeded"), ("BIG", "x" * 5_000)],
            ),
            _component("cLog_2", "cLog", [("LABEL", "No FMD Customer")]),
        ],
        connections=[
            {
                "name": "route16",
                "source": "cSetHeader_16",
                "target": "cMessagingEndpoint_15",
                "connector_name": "ROUTE",
                "line_style": 14,
                "active": True,
            },
            {
                "name": "route17",
                "source": "cMessagingEndpoint_15",
                "target": "cLog_2",
                "connector_name": "ROUTE",
                "line_style": 14,
                "active": True,
            },
        ],
        context_groups=[
            {
                "name": "inline",
                "variables": [
                    {
                        "name": "jdeTableF0101",
                        "type": "id_String",
                        "values": {"PROD": "PRODDTA.F0101", "DEV": "CRPDTA.F0101"},
                    }
                ],
            }
        ],
    )
    (repo_a / "processor_shipping_confirmation-aaa1.json").write_text(
        json.dumps(fmd_job), encoding="utf-8"
    )

    for repo in (repo_a, repo_b):
        (repo / "shared_name-1234.json").write_text(
            json.dumps(
                _job(
                    "shared_name",
                    components=[_component("tMap_1", "tMap", [("LABEL", "shared")])],
                )
            ),
            encoding="utf-8",
        )

    # Twelve near-identical jobs, so a capped search has something to cap.
    for index in range(12):
        (repo_b / f"bulk_{index}-9999.json").write_text(
            json.dumps(
                _job(
                    f"bulk_{index}",
                    components=[
                        _component(
                            f"tPostgresqlRow_{index}",
                            "tPostgresqlRow",
                            [("QUERY", "SELECT ABCLASS01 FROM stg_jde.F0101")],
                        )
                    ],
                )
            ),
            encoding="utf-8",
        )

    monkeypatch.setenv(SCAN_ROOT_ENV, str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# search_source
# ---------------------------------------------------------------------------


async def test_hit_carries_job_component_and_parameter(scan_root: Path) -> None:
    result = await search_source_evidence(
        project_id=PROJECT, query="ABCLASS01", project_scope=[PROJECT]
    )

    assert result.status == "succeeded"
    endpoint = next(
        item
        for item in result.items
        if item.metadata.get("component") == "cMessagingEndpoint_15"
    )
    assert endpoint.metadata["job"] == "processor_shipping_confirmation"
    assert endpoint.metadata["component_type"] == "cMessagingEndpoint"
    assert endpoint.metadata["component_label"] == "determineFMD"
    assert endpoint.metadata["param"] == "URI"
    assert endpoint.metadata["where"] == "component_param_value"
    # The value is the answer, and it is returned unaltered.
    assert FMD_URI in endpoint.summary
    # The ref is what makes the two capabilities compose without guessing a path.
    assert endpoint.metadata["job_ref"].endswith("processor_shipping_confirmation-aaa1.json")


async def test_label_and_context_variable_matches_are_distinguished(scan_root: Path) -> None:
    labels = await search_source_evidence(
        project_id=PROJECT, query="checkIfFMDNeeded", project_scope=[PROJECT]
    )
    assert [item.metadata["where"] for item in labels.items] == ["component_param_value"]

    variables = await search_source_evidence(
        project_id=PROJECT, query="jdeTableF0101", project_scope=[PROJECT]
    )
    wheres = {item.metadata["where"] for item in variables.items}
    assert "context_variable_name" in wheres
    # The environment split is the answer to "which table in production", and it
    # only exists on the context variable.
    variable = next(
        item for item in variables.items if item.metadata["where"] == "context_variable_name"
    )
    assert "PROD=PRODDTA.F0101" in variable.summary


async def test_miss_states_what_was_searched_rather_than_denying_existence(
    scan_root: Path,
) -> None:
    result = await search_source_evidence(
        project_id=PROJECT, query="no_such_identifier", project_scope=[PROJECT]
    )

    assert result.status == "succeeded"
    assert result.items == []
    scope = result.scope or {}
    assert scope["match_count"] == 0
    assert scope["jobs_examined"] == 15
    searched = scope["searched_what"]
    assert "15 scanned job(s)" in searched
    assert "component parameters" in searched
    # A miss must be reportable as "this term is not in the scan", never as
    # "this does not exist".
    assert "fact about this term in this scan" in searched


async def test_result_cap_is_reported_with_the_true_total(scan_root: Path) -> None:
    result = await search_source_evidence(
        project_id=PROJECT,
        query="ABCLASS01",
        project_scope=[PROJECT],
        max_results=3,
    )

    scope = result.scope or {}
    assert len(result.items) == 3
    assert scope["returned"] == 3
    assert scope["match_count"] == 13
    assert scope["complete"] is False
    assert "capped view: do not count from it" in scope["searched_what"]


async def test_value_cap_is_reported_on_the_item_and_in_scope(scan_root: Path) -> None:
    result = await search_source_evidence(
        project_id=PROJECT, query="xxxxxxxxxx", project_scope=[PROJECT]
    )

    item = next(item for item in result.items if item.metadata["param"] == "BIG")
    assert item.metadata["value_truncated"] is True
    assert item.metadata["value_chars"] == 5_000
    assert "value cut at 1200 of 5000 characters" in item.summary
    assert "read_component(" in item.summary
    assert (result.scope or {})["values_truncated"] == 1


async def test_component_type_filter_narrows_and_is_reported(scan_root: Path) -> None:
    result = await search_source_evidence(
        project_id=PROJECT,
        query="ABCLASS01",
        project_scope=[PROJECT],
        component_type="cMessagingEndpoint",
    )

    assert {item.metadata["component_type"] for item in result.items} == {"cMessagingEndpoint"}
    assert (result.scope or {})["component_type_filter"] == "cMessagingEndpoint"


async def test_a_malformed_shard_costs_that_shard_and_not_the_search(
    scan_root: Path,
) -> None:
    # Truncated mid-structure, and it contains the term: a shard the prefilter
    # rejects never needed parsing, so only one that survives the prefilter can
    # exercise the parse failure.
    broken = scan_root / PROJECT / "scan" / "jobs" / "broken-0000.json"
    broken.write_text(
        '{"name": "broken", "components": [{"params": [{"name": "Q", "value": "ABCLASS01"',
        encoding="utf-8",
    )

    result = await search_source_evidence(
        project_id=PROJECT, query="ABCLASS01", project_scope=[PROJECT]
    )

    assert result.status == "succeeded"
    assert any(
        item.metadata.get("component") == "cMessagingEndpoint_15" for item in result.items
    )
    scope = result.scope or {}
    # Named, so a systematically corrupt shard is visible rather than absorbed.
    assert scope["unreadable_jobs"] == ["broken-0000.json"]
    assert scope["unreadable_job_count"] == 1


@pytest.mark.parametrize(
    "project_id",
    ["..", "../other-client", "fixture-project/../other", "a/b", ".hidden", ""],
)
async def test_unsafe_project_ids_are_refused_before_any_path_is_built(
    scan_root: Path,
    project_id: str,
) -> None:
    result = await search_source_evidence(
        project_id=project_id,
        query="ABCLASS01",
        # Scope is permissive on purpose: the id rule has to hold on its own,
        # not only behind an authorization check that happens to fail first.
        project_scope=None,
    )

    assert result.status == "failed"
    assert "not a valid one" in (result.error_message or "")
    assert "Nothing was read" in (result.scope or {})["searched_what"]


async def test_an_out_of_scope_project_learns_nothing_about_the_request(
    scan_root: Path,
) -> None:
    result = await search_source_evidence(
        project_id=PROJECT, query="ABCLASS01", project_scope=["other-project"]
    )

    assert result.status == "failed"
    assert result.error_message == (
        "That project is not in the authorized scope for this turn."
    )
    # No scope at all: the denial must not report how many jobs the project has.
    assert result.scope is None


async def test_an_unmounted_project_reports_that_nothing_was_searched(
    scan_root: Path,
) -> None:
    result = await search_source_evidence(
        project_id="never-scanned", query="ABCLASS01", project_scope=None
    )

    assert result.status == "failed"
    scope = result.scope or {}
    assert scope["scan_mounted"] is False
    assert SCAN_ROOT_ENV in scope["searched_what"]
    assert "say so rather than reporting an absence" in (result.error_message or "")


async def test_a_too_short_term_is_rejected_with_the_correction(scan_root: Path) -> None:
    result = await search_source_evidence(
        project_id=PROJECT, query=" a ", project_scope=[PROJECT]
    )

    assert result.status == "failed"
    assert "at least two characters" in (result.error_message or "")


# ---------------------------------------------------------------------------
# read_component
# ---------------------------------------------------------------------------


async def test_read_component_returns_one_component_whole(scan_root: Path) -> None:
    result = await read_component_evidence(
        project_id=PROJECT,
        job="processor_shipping_confirmation",
        component="cMessagingEndpoint_15",
        project_scope=[PROJECT],
    )

    assert result.status == "succeeded"
    body = result.items[0].summary
    assert f"URI = {FMD_URI}" in body
    assert "LABEL = determineFMD" in body
    assert "USE_CUSTOM_COMPONENT = false" in body
    # Edges derived from the job's connection list; the component's own lists are
    # empty, as they are in every real scan.
    assert "cSetHeader_16 -> cMessagingEndpoint_15  [ROUTE] route16" in body
    assert "cMessagingEndpoint_15 -> cLog_2  [ROUTE] route17" in body
    assert "ADDRESS_KEY: id_String VARCHAR key not-null" in body

    scope = result.scope or {}
    assert scope["param_count"] == 3
    assert scope["incoming_count"] == 1
    assert scope["outgoing_count"] == 1
    assert scope["complete"] is True
    assert "no other job was read" in scope["searched_what"]


async def test_read_component_accepts_the_ref_a_search_hit_returned(
    scan_root: Path,
) -> None:
    hit = await search_source_evidence(
        project_id=PROJECT, query="determineFMD", project_scope=[PROJECT]
    )
    endpoint = next(
        item
        for item in hit.items
        if item.metadata.get("component") == "cMessagingEndpoint_15"
    )

    result = await read_component_evidence(
        project_id=PROJECT,
        job=endpoint.metadata["job_ref"],
        component=endpoint.metadata["component"],
        project_scope=[PROJECT],
    )

    assert result.status == "succeeded"
    assert FMD_URI in result.items[0].summary


async def test_read_component_refuses_an_unknown_component_and_lists_the_real_ones(
    scan_root: Path,
) -> None:
    result = await read_component_evidence(
        project_id=PROJECT,
        job="processor_shipping_confirmation",
        component="cMessagingEndpoint_99",
        project_scope=[PROJECT],
    )

    assert result.status == "failed"
    scope = result.scope or {}
    assert scope["available_components"] == [
        "cMessagingEndpoint_15",
        "cSetHeader_16",
        "cLog_2",
    ]
    assert scope["available_component_count"] == 3
    assert "listed in the scope of this result" in (result.error_message or "")


async def test_an_ambiguous_job_name_lists_candidates_instead_of_guessing(
    scan_root: Path,
) -> None:
    result = await read_component_evidence(
        project_id=PROJECT,
        job="shared_name",
        component="tMap_1",
        project_scope=[PROJECT],
    )

    assert result.status == "failed"
    scope = result.scope or {}
    assert scope["candidate_count"] == 2
    assert len(scope["candidates"]) == 2
    assert all(ref.endswith("shared_name-1234.json") for ref in scope["candidates"])
    assert "more than one repository" in (result.error_message or "")

    # And the ref disambiguates it.
    resolved = await read_component_evidence(
        project_id=PROJECT,
        job=scope["candidates"][0],
        component="tMap_1",
        project_scope=[PROJECT],
    )
    assert resolved.status == "succeeded"


async def test_read_component_refuses_an_unknown_job_and_lists_the_real_ones(
    scan_root: Path,
) -> None:
    result = await read_component_evidence(
        project_id=PROJECT,
        job="no_such_job",
        component="tMap_1",
        project_scope=[PROJECT],
    )

    assert result.status == "failed"
    scope = result.scope or {}
    assert "processor_shipping_confirmation" in scope["available_jobs"]
    assert "says nothing about the project's components" in scope["searched_what"]


@pytest.mark.parametrize(
    "job_ref",
    [
        "../../../other-client/scan/jobs/neighbour-1111.json",
        "{root}/other-client/scan/jobs/neighbour-1111.json",
    ],
)
async def test_read_component_refuses_a_job_ref_outside_the_project(
    scan_root: Path,
    job_ref: str,
) -> None:
    """Relative escape and absolute path, both from inside an authorized project."""

    neighbour = scan_root / "other-client" / "scan" / "jobs"
    neighbour.mkdir(parents=True)
    (neighbour / "neighbour-1111.json").write_text(
        json.dumps(
            _job(
                "other_client_private_job",
                components=[_component("tMap_1", "tMap", [("QUERY", "OTHER_CLIENT_SECRET")])],
            )
        ),
        encoding="utf-8",
    )

    result = await read_component_evidence(
        project_id=PROJECT,
        job=job_ref.format(root=scan_root),
        component="tMap_1",
        project_scope=[PROJECT],
    )

    # Containment rejects the ref, so it falls through to name resolution, finds
    # no job by that name in this project, and says so. What it must never do is
    # read the neighbouring client's shard.
    assert result.status == "failed"
    payload = json.dumps(result.model_dump(mode="json"))
    assert "OTHER_CLIENT_SECRET" not in payload
    assert "other_client_private_job" not in payload


async def test_read_component_is_scope_checked_independently(scan_root: Path) -> None:
    result = await read_component_evidence(
        project_id=PROJECT,
        job="processor_shipping_confirmation",
        component="cMessagingEndpoint_15",
        project_scope=["other-project"],
    )

    assert result.status == "failed"
    assert result.scope is None


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_both_capabilities_are_registered_read_only_with_executable_handlers() -> None:
    registry = base_capability_registry()

    for capability in (SOURCE_SEARCH_CAPABILITY, SOURCE_COMPONENT_READ_CAPABILITY):
        registered = registry.get(capability.id)
        assert registered.risk_tier is HarnessRiskTier.READ_ONLY
        assert registered.packs == ["core", "documentation"]
        assert registered.input_schema["additionalProperties"] is False
        assert registry.handler_for(capability.id) is not None

    assert SOURCE_SEARCH_CAPABILITY.semantic_name == "search_source"
    assert SOURCE_COMPONENT_READ_CAPABILITY.semantic_name == "read_component"


async def test_the_handler_takes_scope_from_the_turn_and_not_from_arguments(
    scan_root: Path,
) -> None:
    handler = next(
        item
        for item in source_evidence_capability_handlers()
        if item.capability_id == SOURCE_SEARCH_CAPABILITY.id
    )

    observation = await handler(
        SOURCE_SEARCH_CAPABILITY,
        CapabilityExecutionRequest(
            capability_id=SOURCE_SEARCH_CAPABILITY.id,
            arguments={"project_id": PROJECT, "query": "determineFMD"},
        ),
        CapabilityExecutionContext(user_id=1, project_scope=["someone-else"]),
        CapabilityHandlerServices(),
    )

    assert observation.status == "failed"
    assert observation.safe_details["evidence_count"] == 0

    allowed = await handler(
        SOURCE_SEARCH_CAPABILITY,
        CapabilityExecutionRequest(
            capability_id=SOURCE_SEARCH_CAPABILITY.id,
            arguments={"project_id": PROJECT, "query": "determineFMD"},
        ),
        CapabilityExecutionContext(user_id=1, project_scope=[PROJECT]),
        CapabilityHandlerServices(),
    )

    assert allowed.status == "succeeded"
    # The item metadata survives the transport: `job_ref` and `component` are
    # what the next call needs, and the shared evidence handler's key allowlist
    # would have dropped both.
    first = allowed.data["items"][0]
    assert first["metadata"]["job_ref"]
    assert first["metadata"]["component"]
    assert allowed.data["scope"]["match_count"] >= 1


# ---------------------------------------------------------------------------
# The regression, against the real scan
# ---------------------------------------------------------------------------


def _live_pluripharm() -> Path | None:
    """The live pluripharm scan, honouring the configured root.

    `INTODOC_SCAN_ROOT` is read here rather than assuming `/intodoc-output` so
    that this runs both in the container, where the mount is at the default, and
    on a developer machine pointed at `knowledge_base/intodoc-output`.
    """

    root = Path(os.environ.get(SCAN_ROOT_ENV, "").strip() or "/intodoc-output")
    jobs = root / "pluripharm" / "scan" / "jobs"
    return jobs if jobs.is_dir() else None


@pytest.mark.skipif(
    _live_pluripharm() is None,
    reason="The live pluripharm scan is not mounted under INTODOC_SCAN_ROOT.",
)
async def test_the_fmd_question_is_answerable_from_the_live_scan() -> None:
    """The case the capability was built for.

    Asked "what orders go through FMD?", an agent reported that it found no
    filter. `ABCLASS01`, `determineFMD`, `checkIfFMDNeeded` and `No FMD
    Customer` appear nowhere in the generated documentation; all four are in the
    scan, and this is the exact lookup that recovers the predicate.
    """

    result = await search_source_evidence(
        project_id="pluripharm", query="ABCLASS01", project_scope=["pluripharm"]
    )

    assert result.status == "succeeded"
    endpoint = next(
        item
        for item in result.items
        if item.metadata.get("component") == "cMessagingEndpoint_15"
        and item.metadata.get("job") == "processor_shipping_confirmation_map_jde_to_esb_p010"
    )
    assert endpoint.metadata["component_label"] == "determineFMD"
    assert endpoint.metadata["param"] == "URI"
    # Verbatim, redaction included: the scan's redaction pass removes secrets and
    # leaves logic, which is the property that lets this be returned at all.
    assert "ABCLASS01 = 'FMD'" in endpoint.summary
    assert "dataSource=#cSQLConnection_1" in endpoint.summary
    assert endpoint.metadata["value_truncated"] is False

    whole = await read_component_evidence(
        project_id="pluripharm",
        job=endpoint.metadata["job_ref"],
        component="cMessagingEndpoint_15",
        project_scope=["pluripharm"],
    )
    assert whole.status == "succeeded"
    assert "ABCLASS01 = 'FMD'" in whole.items[0].summary
    # Derived from the job's connection list -- the component's own lists are
    # empty in the real scan, and reading only those reports it as unwired.
    assert (whole.scope or {})["incoming_count"] >= 1
