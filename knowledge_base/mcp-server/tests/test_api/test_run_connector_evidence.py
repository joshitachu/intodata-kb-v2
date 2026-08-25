"""GET /mcp/generate-runs/{run_id}/connector-evidence.

The dashboard's run-history popover has called this route since the connector
work landed; until now nothing served it, so every run reported "No connector
evidence summary is available for this run." These tests pin the contract the
popover reads, and in particular that runtime *absence* is a 200 with a mode,
never an error (build plan R7/R17).
"""

from __future__ import annotations

import json

import pytest

from mcp_server.config import config
from mcp_server.services.project_registry import ProjectRegistry


async def _make_run(
    session_factory,
    project_id: str = "runtimeproj",
    *,
    partition_name: str | None = None,
    add_project: bool = True,
) -> int:
    async with session_factory() as session:
        registry = ProjectRegistry(session)
        if add_project:
            await registry.add_project(
                project_id=project_id,
                local_path=f"/tmp/{project_id}",
                platform="talend",
                source_type="zip",
            )
        run = await registry.create_generate_run(
            project_id,
            triggered_by="composite" if partition_name else "api",
            partition_name=partition_name,
        )
        run_id = run.id
        await session.commit()
    return run_id


def _write_coverage(project_id: str, payload: dict, subproject: str | None = None) -> None:
    root = config.project_root / "output" / project_id
    if subproject:
        root = root / subproject
    assessment = root / "docs" / "_assessment"
    assessment.mkdir(parents=True, exist_ok=True)
    (assessment / "runtime_evidence_coverage.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _write_partition_state(project_id: str, partitions: list[str]) -> None:
    """Mirror what the partition orchestrator records after a composite run."""
    topology = config.project_root / "output" / project_id / "topology"
    topology.mkdir(parents=True, exist_ok=True)
    (topology / "partition_state.json").write_text(
        json.dumps({name: {"scan_fingerprint": "abc", "doc_count": 3} for name in partitions}),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_returns_runtime_coverage_for_a_matched_run(client, session_factory):
    run_id = await _make_run(session_factory)
    _write_coverage(
        "runtimeproj",
        {
            "mode": "data",
            "window_days": 90,
            "units_requested": 374,
            "units_matched": 5,
            "matched": ["a", "b", "c", "d", "e"],
            "unmatched": [],
            "orphan_runtime_jobs": [],
            "partial": False,
            "batches": 2,
            "source_key": "tmc",
        },
    )

    resp = await client.get(f"/mcp/generate-runs/{run_id}/connector-evidence")

    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == run_id
    assert body["runtime"] == {
        "subproject": None,
        "available": True,
        "mode": "data",
        "window_days": 90,
        "units_requested": 374,
        "units_matched": 5,
        "orphan_runtime_job_count": 0,
        "partial": False,
        "scope_mismatch": False,
        "batches": 2,
        "source_key": "tmc",
        "subproject_count": 0,
    }
    assert body["runtime_subprojects"] == []
    # The live-connector counters are out of scope for this slice and must keep
    # their defaults rather than being invented from the runtime lane.
    assert body["available"] is False
    assert body["successful_calls"] == 0
    assert body["findings"] == []


@pytest.mark.asyncio
async def test_absent_artifact_is_a_normal_unavailable_response(client, session_factory):
    """Every run older than the artifact hits this path. It is not an error."""
    run_id = await _make_run(session_factory, "noartifactproj")

    resp = await client.get(f"/mcp/generate-runs/{run_id}/connector-evidence")

    assert resp.status_code == 200
    assert resp.json()["runtime"]["available"] is False
    assert resp.json()["runtime"]["mode"] == "unavailable"


@pytest.mark.asyncio
async def test_malformed_artifact_degrades_instead_of_failing(client, session_factory):
    run_id = await _make_run(session_factory, "badjsonproj")
    assessment = config.project_root / "output" / "badjsonproj" / "docs" / "_assessment"
    assessment.mkdir(parents=True, exist_ok=True)
    (assessment / "runtime_evidence_coverage.json").write_text("{not json", encoding="utf-8")

    resp = await client.get(f"/mcp/generate-runs/{run_id}/connector-evidence")

    assert resp.status_code == 200
    assert resp.json()["runtime"]["available"] is False


@pytest.mark.asyncio
async def test_missing_fields_fall_back_to_the_arrays_the_writer_also_emits(
    client, session_factory
):
    run_id = await _make_run(session_factory, "partialfieldsproj")
    _write_coverage(
        "partialfieldsproj",
        {
            "mode": "binding_suspect",
            "matched": [],
            "unmatched": ["j1", "j2", "j3"],
            "orphan_runtime_jobs": ["r1", "r2"],
        },
    )

    resp = await client.get(f"/mcp/generate-runs/{run_id}/connector-evidence")

    runtime = resp.json()["runtime"]
    assert runtime["mode"] == "binding_suspect"
    assert runtime["units_requested"] == 3
    assert runtime["units_matched"] == 0
    assert runtime["orphan_runtime_job_count"] == 2
    assert runtime["window_days"] is None
    assert runtime["source_key"] is None


@pytest.mark.asyncio
async def test_orphan_job_names_never_cross_the_boundary(client, session_factory):
    """The popover needs a count. Job names are customer-identifying."""
    run_id = await _make_run(session_factory, "orphanproj")
    _write_coverage(
        "orphanproj",
        {"mode": "binding_suspect", "orphan_runtime_jobs": ["AcmeBank_NightlyLoad"]},
    )

    resp = await client.get(f"/mcp/generate-runs/{run_id}/connector-evidence")

    assert "AcmeBank_NightlyLoad" not in resp.text
    assert resp.json()["runtime"]["orphan_runtime_job_count"] == 1


@pytest.mark.asyncio
async def test_unknown_run_is_a_404(client):
    resp = await client.get("/mcp/generate-runs/999999/connector-evidence")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_traversal_shaped_project_id_reads_nothing(client, session_factory, tmp_path):
    """The id comes from a DB row, so this is defence in depth, not the control."""
    from mcp_server.api.routes.generate_runs import _runtime_coverage_path

    assert _runtime_coverage_path("../../etc") is None
    assert _runtime_coverage_path("a/b") is None
    assert _runtime_coverage_path("good-project_1.2") is not None


@pytest.mark.asyncio
async def test_traversal_shaped_partition_name_reads_nothing(client):
    """`partition_name` is a zip name an operator chose. Same guard, same rule."""
    from mcp_server.api.routes.generate_runs import _runtime_coverage_path

    assert _runtime_coverage_path("proj", "../../etc") is None
    assert _runtime_coverage_path("proj", "a/b") is None
    assert _runtime_coverage_path("proj", "..") is None
    assert _runtime_coverage_path("proj", "sub_a") is not None


# --- mode inference (build plan A12) -----------------------------------------


@pytest.mark.asyncio
async def test_present_artifact_without_a_mode_key_is_not_unavailable(
    client, session_factory
):
    """The shipped writer emits no `mode`. `available=true, mode=unavailable`
    is self-contradictory, and it is what this endpoint returned for every real
    run before the shared schema landed."""
    run_id = await _make_run(session_factory, "nomodeproj")
    _write_coverage(
        "nomodeproj",
        {
            "window_days": 90,
            "units_requested": 10,
            "units_matched": 4,
            "matched": ["a", "b", "c", "d"],
            "unmatched": ["e"],
        },
    )

    runtime = (await client.get(f"/mcp/generate-runs/{run_id}/connector-evidence")).json()["runtime"]

    assert runtime["available"] is True
    assert runtime["mode"] == "data"
    assert runtime["units_matched"] == 4


@pytest.mark.asyncio
async def test_a_writer_that_says_unavailable_is_believed(client, session_factory):
    run_id = await _make_run(session_factory, "saysunavailproj")
    _write_coverage("saysunavailproj", {"mode": "unavailable", "units_requested": 10})

    runtime = (await client.get(f"/mcp/generate-runs/{run_id}/connector-evidence")).json()["runtime"]

    assert runtime["available"] is True
    assert runtime["mode"] == "unavailable"


@pytest.mark.asyncio
async def test_an_empty_artifact_carries_no_coverage_field_and_stays_unavailable(
    client, session_factory
):
    run_id = await _make_run(session_factory, "emptyartifactproj")
    _write_coverage("emptyartifactproj", {"generated_at": "2026-08-24T00:00:00Z"})

    runtime = (await client.get(f"/mcp/generate-runs/{run_id}/connector-evidence")).json()["runtime"]

    assert runtime["mode"] == "unavailable"


def test_the_reader_uses_the_producers_own_schema():
    """Tripwire for the A12 wire-up.

    The field set must come from `RuntimeEvidenceStore.coverage_payload()`, not
    from a list restated in the route -- restating it is how the writer and the
    reader came to disagree. The local fallback exists only so this status route
    survives a broken producer, and it must not drift from the real thing.
    """
    from mcp_server.api.routes.generate_runs import (
        _FALLBACK_COVERAGE_FIELDS,
        _FALLBACK_COVERAGE_FILENAME,
        COVERAGE_FIELDS,
        RUNTIME_COVERAGE_FILENAME,
    )
    from mcp_server.providers.custom import runtime_evidence

    shared_fields = set(
        runtime_evidence.RuntimeEvidenceStore(window_days=0).coverage_payload()
    )
    assert set(COVERAGE_FIELDS) == shared_fields, "reader is not reading the shared schema"
    assert set(_FALLBACK_COVERAGE_FIELDS) == shared_fields, "fallback drifted from the schema"
    assert (
        RUNTIME_COVERAGE_FILENAME
        == runtime_evidence.RUNTIME_COVERAGE_FILENAME
        == _FALLBACK_COVERAGE_FILENAME
    )


@pytest.mark.asyncio
async def test_the_writers_own_payload_reads_back_without_contradiction(
    client, session_factory
):
    """End to end against the producer: whatever `coverage_payload()` emits must
    not come back as `available=true, mode="unavailable"`."""
    from mcp_server.providers.custom.runtime_evidence import RuntimeEvidenceStore

    run_id = await _make_run(session_factory, "writerpayloadproj")
    _write_coverage(
        "writerpayloadproj", RuntimeEvidenceStore(window_days=90, requested_units=7).coverage_payload()
    )

    runtime = (await client.get(f"/mcp/generate-runs/{run_id}/connector-evidence")).json()["runtime"]

    assert runtime["available"] is True
    assert runtime["mode"] != "unavailable"
    assert runtime["window_days"] == 90
    assert runtime["units_requested"] == 7


@pytest.mark.asyncio
async def test_failed_batches_alone_still_read_as_partial(client, session_factory):
    run_id = await _make_run(session_factory, "failedbatchproj")
    _write_coverage(
        "failedbatchproj",
        {"mode": "data", "units_requested": 40, "units_matched": 2, "batches": 3, "failed_batches": 1},
    )

    runtime = (await client.get(f"/mcp/generate-runs/{run_id}/connector-evidence")).json()["runtime"]

    assert runtime["partial"] is True


@pytest.mark.asyncio
async def test_scope_mismatch_is_carried_as_its_own_axis(client, session_factory):
    """Build-plan A4: mode and completeness are different axes, so a run with
    data can still report that the scan and the runtime source disagree."""
    run_id = await _make_run(session_factory, "scopemismatchproj")
    _write_coverage(
        "scopemismatchproj",
        {
            "mode": "data",
            "units_requested": 10,
            "units_matched": 1,
            "scope_mismatch": True,
            "orphan_runtime_jobs": ["a", "b", "c"],
        },
    )

    runtime = (await client.get(f"/mcp/generate-runs/{run_id}/connector-evidence")).json()["runtime"]

    assert runtime["mode"] == "data"
    assert runtime["scope_mismatch"] is True
    assert runtime["orphan_runtime_job_count"] == 3


# --- composite (partitioned) uploads (red-team F12) --------------------------


@pytest.mark.asyncio
async def test_composite_child_run_reads_its_own_sub_project(client, session_factory):
    """A partitioned run publishes to `output/{pid}/{subproject}/docs`. Reading
    the project-level path returned `available=false` for every composite run."""
    run_id = await _make_run(session_factory, "compositeproj", partition_name="zip_a")
    _write_coverage(
        "compositeproj",
        {"mode": "data", "window_days": 90, "units_requested": 20, "units_matched": 3},
        subproject="zip_a",
    )

    body = (await client.get(f"/mcp/generate-runs/{run_id}/connector-evidence")).json()

    assert body["runtime"]["available"] is True
    assert body["runtime"]["subproject"] == "zip_a"
    assert body["runtime"]["units_matched"] == 3
    # A child run speaks for its own sub-project only.
    assert body["runtime_subprojects"] == []


@pytest.mark.asyncio
async def test_composite_child_with_no_artifact_names_the_sub_project_it_missed(
    client, session_factory
):
    run_id = await _make_run(session_factory, "childgapproj", partition_name="zip_b")

    runtime = (await client.get(f"/mcp/generate-runs/{run_id}/connector-evidence")).json()["runtime"]

    assert runtime["available"] is False
    assert runtime["subproject"] == "zip_b"


@pytest.mark.asyncio
async def test_child_run_partition_name_is_sanitised_to_the_published_directory(
    client, session_factory
):
    """The orchestrator sanitises the zip name before using it as a directory;
    the DB keeps the raw name. The reader must apply the same transform."""
    run_id = await _make_run(session_factory, "sanitiseproj", partition_name="Zip A (v2).zip")
    _write_coverage(
        "sanitiseproj",
        {"mode": "data", "units_requested": 5, "units_matched": 5},
        subproject="Zip_A_v2_.zip",
    )

    runtime = (await client.get(f"/mcp/generate-runs/{run_id}/connector-evidence")).json()["runtime"]

    assert runtime["available"] is True, "partition name was not mapped to its output subdir"
    assert runtime["units_matched"] == 5


@pytest.mark.asyncio
async def test_top_level_run_on_a_composite_project_aggregates_every_sub_project(
    client, session_factory
):
    run_id = await _make_run(session_factory, "aggproj")
    _write_partition_state("aggproj", ["zip_a", "zip_b"])
    _write_coverage(
        "aggproj",
        {
            "mode": "data",
            "window_days": 90,
            "units_requested": 20,
            "units_matched": 3,
            "orphan_runtime_jobs": ["Shared_Orphan", "OnlyA"],
            "batches": 1,
            "source_key": "tmc",
        },
        subproject="zip_a",
    )
    _write_coverage(
        "aggproj",
        {
            "mode": "data",
            "window_days": 90,
            "units_requested": 30,
            "units_matched": 7,
            "orphan_runtime_jobs": ["Shared_Orphan", "OnlyB"],
            "batches": 2,
            "source_key": "tmc",
        },
        subproject="zip_b",
    )

    body = (await client.get(f"/mcp/generate-runs/{run_id}/connector-evidence")).json()

    runtime = body["runtime"]
    assert runtime["subproject"] is None
    assert runtime["subproject_count"] == 2
    assert runtime["units_requested"] == 50
    assert runtime["units_matched"] == 10
    # Both sub-projects are bound to the same runtime source, so the orphan sets
    # are drawn from one vocabulary: intersect (A2), never sum.
    assert runtime["orphan_runtime_job_count"] == 1
    assert runtime["batches"] == 3
    assert runtime["window_days"] == 90
    assert runtime["source_key"] == "tmc"
    assert runtime["partial"] is False
    assert [row["subproject"] for row in body["runtime_subprojects"]] == ["zip_a", "zip_b"]
    assert [row["units_matched"] for row in body["runtime_subprojects"]] == [3, 7]


@pytest.mark.asyncio
async def test_aggregate_is_partial_when_one_sub_project_has_no_artifact(
    client, session_factory
):
    """The failure mode the aggregate must not hide: a sub-project that never
    published is a hole in the total, not a smaller total."""
    run_id = await _make_run(session_factory, "gapproj")
    _write_partition_state("gapproj", ["zip_a", "zip_b"])
    _write_coverage(
        "gapproj",
        {"mode": "data", "units_requested": 20, "units_matched": 3},
        subproject="zip_a",
    )

    body = (await client.get(f"/mcp/generate-runs/{run_id}/connector-evidence")).json()

    assert body["runtime"]["partial"] is True
    assert body["runtime"]["units_matched"] == 3
    assert body["runtime"]["subproject_count"] == 2
    assert [row["available"] for row in body["runtime_subprojects"]] == [True, False]


@pytest.mark.asyncio
async def test_sub_projects_are_found_without_partition_state(client, session_factory):
    """`partition_state.json` is written only on success. Published artifacts on
    disk are the other half of the answer."""
    run_id = await _make_run(session_factory, "nostateproj")
    _write_coverage(
        "nostateproj",
        {"mode": "data", "units_requested": 8, "units_matched": 2},
        subproject="zip_only",
    )

    body = (await client.get(f"/mcp/generate-runs/{run_id}/connector-evidence")).json()

    assert body["runtime"]["units_matched"] == 2
    assert body["runtime"]["subproject_count"] == 1


@pytest.mark.asyncio
async def test_orphan_names_never_cross_the_boundary_from_an_aggregate(
    client, session_factory
):
    run_id = await _make_run(session_factory, "aggorphanproj")
    _write_partition_state("aggorphanproj", ["zip_a", "zip_b"])
    for subproject in ("zip_a", "zip_b"):
        _write_coverage(
            "aggorphanproj",
            {
                "mode": "data",
                "units_matched": 1,
                "matched": ["u"],
                "orphan_runtime_jobs": ["AcmeBank_NightlyLoad"],
            },
            subproject=subproject,
        )

    resp = await client.get(f"/mcp/generate-runs/{run_id}/connector-evidence")

    assert "AcmeBank_NightlyLoad" not in resp.text
    assert resp.json()["runtime"]["orphan_runtime_job_count"] == 1


@pytest.mark.asyncio
async def test_a_malformed_sub_project_artifact_does_not_fail_the_aggregate(
    client, session_factory
):
    run_id = await _make_run(session_factory, "aggbadproj")
    _write_partition_state("aggbadproj", ["zip_a", "zip_b"])
    _write_coverage(
        "aggbadproj",
        {"mode": "data", "units_requested": 4, "units_matched": 4},
        subproject="zip_a",
    )
    bad = config.project_root / "output" / "aggbadproj" / "zip_b" / "docs" / "_assessment"
    bad.mkdir(parents=True, exist_ok=True)
    (bad / "runtime_evidence_coverage.json").write_text("{not json", encoding="utf-8")

    resp = await client.get(f"/mcp/generate-runs/{run_id}/connector-evidence")

    assert resp.status_code == 200
    assert resp.json()["runtime"]["units_matched"] == 4
    assert resp.json()["runtime"]["partial"] is True
