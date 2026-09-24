"""The analyst-curated WerkOrder topology loads through the production path.

Exercises ``_load_published_business_topology`` -- the same function
``GET /mcp/projects/{id}/landscape`` calls -- against the real curated artifact
at ``output/VW_FULL_LANDSCAPE/topology/business_topology.curated.json``.

The whole module is skipped when that artifact or the scan cache is absent:
both live under gitignored, confidential client paths, so a clean checkout does
not have them.  When they ARE present this is the highest-signal check on the
curated file, because the curated branch of the loader (projects.py:4699) does
not itself run ``BusinessTopology.model_validate`` -- it only matches the scan
fingerprint.  This test therefore asserts the schema too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

KB_ROOT = Path(__file__).resolve().parents[3]
PROJECT_ID = "VW_FULL_LANDSCAPE"
TOPOLOGY_DIR = KB_ROOT / "output" / PROJECT_ID / "topology"
CURATED_PATH = TOPOLOGY_DIR / "business_topology.curated.json"
SCAN_DIR = KB_ROOT / "intodoc-output" / PROJECT_ID
PUBLISHED_PATH = (
    KB_ROOT / "output" / PROJECT_ID / "docs" / "_topology" / "business_topology.json"
)

pytestmark = pytest.mark.skipif(
    not CURATED_PATH.is_file() or not SCAN_DIR.is_dir(),
    reason="VW_FULL_LANDSCAPE curated topology or scan cache is not present locally",
)

EXPECTED = {
    "systems": 18,
    "integration_solutions": 19,
    "business_objects": 12,
    "data_streams": 48,
    "delivery_paths": 19,
    "transformations": 36,
    "controls": 11,
}


@pytest.fixture
def real_project_paths(monkeypatch):
    """Point config at the real knowledge_base tree for this test only."""

    from mcp_server.config import config

    monkeypatch.setattr(config, "project_root", KB_ROOT)
    monkeypatch.setattr(config, "intodoc_output_dir", KB_ROOT / "intodoc-output")
    return config


def test_curated_werkorder_topology_is_served_by_the_loader(real_project_paths):
    from mcp_server.api.routes.projects import _load_published_business_topology

    assert not PUBLISHED_PATH.is_file(), (
        "a published docs/_topology/business_topology.json takes precedence over the "
        "curated overlay (projects.py:4560); the curated file would never be served"
    )

    topology, state, evidence = _load_published_business_topology(PROJECT_ID)

    assert state["source"] == "analyst_curated", state
    assert state["available"] is True
    assert state["validated"] is True
    assert state["stale"] is False
    assert topology is not None

    for key, count in EXPECTED.items():
        assert len(topology[key]) == count, f"{key}: {len(topology[key])} != {count}"
    assert topology["schema_version"] == "business-topology-v1"
    assert topology["metadata"]["project_id"] == PROJECT_ID

    # The drill-down catalogue resolves the designs the streams cite.
    assert evidence["artifacts"], "topology_evidence carries no artifacts"
    catalogued = {row["id"] for row in evidence["artifacts"]}
    cited = {
        artifact_id
        for stream in topology["data_streams"]
        for artifact_id in stream["artifact_ids"]
    }
    assert cited & catalogued, "no cited design resolves in the evidence catalogue"


def test_curated_werkorder_topology_is_schema_valid_and_referentially_closed():
    from mcp_server.services.business_topology.models import BusinessTopology

    payload = json.loads(CURATED_PATH.read_text(encoding="utf-8"))
    model = BusinessTopology.model_validate(payload)

    system_ids = {item.id for item in model.systems}
    object_ids = {item.id for item in model.business_objects}
    solution_ids = {item.id for item in model.integration_solutions}
    stream_ids = {item.id for item in model.data_streams}
    path_ids = {item.id for item in model.delivery_paths}
    control_ids = {item.id for item in model.controls}
    risk_ids = {item.id for item in model.risks}
    unknown_ids = {item.id for item in model.unknowns}

    for stream in model.data_streams:
        assert stream.source_system_id in system_ids, stream.id
        assert stream.destination_system_id in system_ids, stream.id
        assert set(stream.business_object_ids) <= object_ids, stream.id
        assert set(stream.integration_solution_ids) <= solution_ids, stream.id
        assert set(stream.control_ids) <= control_ids, stream.id
        assert set(stream.risk_ids) <= risk_ids, stream.id
        assert set(stream.unknown_ids) <= unknown_ids, stream.id
        assert stream.artifact_ids, stream.id
        if stream.delivery_path_id:
            assert stream.delivery_path_id in path_ids, stream.id

    for path in model.delivery_paths:
        assert path.stream_id in stream_ids, path.id
        sequences = [segment.sequence for segment in path.segments]
        assert sequences == list(range(1, len(sequences) + 1)), path.id
        allowed = system_ids | solution_ids
        for segment in path.segments:
            assert segment.source_ref in allowed, segment.id
            assert segment.destination_ref in allowed, segment.id
        for previous, current in zip(path.segments, path.segments[1:]):
            assert previous.destination_ref == current.source_ref, path.id

    for transformation in model.transformations:
        assert set(transformation.stream_ids) <= stream_ids, transformation.id

    # Field-level detail is the point of this artifact.
    mappings = sum(len(item.mappings) for item in model.transformations)
    assert mappings >= 300, mappings


def test_curated_werkorder_topology_is_ascii_only_and_carries_safe_ids():
    """cp1252 rule, and the id charset the canvas edge keys can survive.

    ``lib/landscapeTransform.ts::landscapeEdgeKey`` joins on the unit separator
    U+001F and the backend folds C0 separators as whitespace, so an id may not
    contain one -- nor whitespace, nor the composite markers the renderer uses.
    """

    raw = CURATED_PATH.read_bytes()
    assert all(byte < 128 for byte in raw), "curated topology is not ASCII-only"

    payload = json.loads(raw.decode("ascii"))
    ids: list[str] = []
    for key in (
        "systems", "integration_solutions", "business_objects", "data_streams",
        "delivery_paths", "transformations", "business_rules", "controls",
        "runtime_observations", "conflicts", "artifact_annotations",
        "risks", "unknowns",
    ):
        ids.extend(item["id"] for item in payload[key])
    for path in payload["delivery_paths"]:
        ids.extend(segment["id"] for segment in path["segments"])

    forbidden = ("\x1c", "\x1d", "\x1e", "\x1f", " ", "\t", "\r", "\n", "\x0b", "\x0c",
                 "->", "<->", "::")
    for value in ids:
        assert value, "empty id"
        assert len(value) <= 500, value
        for marker in forbidden:
            assert marker not in value, f"{value!r} contains {marker!r}"


def test_curated_werkorder_topology_publishes_the_structured_facts():
    """The enrichment is pinned by invariant, not by count.

    Counts move whenever the analyst adds a rule or a measurement, and a test
    that fails for that reason teaches people to update the number instead of
    reading it. What must never regress is the shape: a rule that states
    nothing, a value table that names no field, or a measurement without its
    window is worse than an absent one, because each reads as an answer.
    """

    from mcp_server.services.business_topology.models import BusinessTopology

    model = BusinessTopology.model_validate(
        json.loads(CURATED_PATH.read_text(encoding="utf-8"))
    )
    stream_ids = {item.id for item in model.data_streams}
    transformation_ids = {item.id for item in model.transformations}

    assert model.business_rules, "the curated overlay publishes no business rules"
    for rule in model.business_rules:
        assert rule.statement.strip(), rule.id
        assert rule.stream_ids, rule.id
        assert set(rule.stream_ids) <= stream_ids, rule.id
        assert set(rule.transformation_ids) <= transformation_ids, rule.id
        assert rule.owner or rule.owner_question, rule.id
        assert rule.kind != "unknown", rule.id

    for transformation in model.transformations:
        assert transformation.kind != "unknown", transformation.id
        if transformation.kind == "value_domain":
            assert transformation.target_field, transformation.id

    assert model.runtime_observations, "no measured or absent runtime behaviour"
    for observation in model.runtime_observations:
        assert observation.status == "observed", observation.id
        assert observation.stream_ids or observation.delivery_path_ids, observation.id
        assert observation.observed_behavior.strip(), observation.id
        for metric in observation.metrics:
            assert metric.window.strip(), f"{observation.id}/{metric.label}"
            assert metric.source.strip(), f"{observation.id}/{metric.label}"

    # Runtime evidence annotates; it may never ground a business direction.
    minted = {
        evidence_id
        for observation in model.runtime_observations
        for evidence_id in observation.evidence_ids
        if evidence_id.startswith("supporting-claim:")
    }
    grounding = [
        claim.id
        for collection in (model.systems, model.data_streams, model.delivery_paths)
        for claim in collection
        if set(claim.evidence_ids) & minted
    ]
    assert not grounding, f"runtime evidence grounds {grounding[:3]}"
    assert all(stream.status != "observed" for stream in model.data_streams)

    every_stream_has_runtime = {
        stream_id
        for observation in model.runtime_observations
        for stream_id in observation.stream_ids
    }
    missing = sorted(stream_ids - every_stream_has_runtime)
    assert not missing, f"streams with neither a measurement nor a stated absence: {missing}"

    for stream in model.data_streams:
        if stream.contract_width is None:
            continue
        counts = [
            value
            for value in (
                stream.contract_width.offered,
                stream.contract_width.mapped,
                stream.contract_width.written,
            )
            if value is not None
        ]
        # A widening funnel is legitimate (a mapping adds constants and splits
        # fields) but must explain itself, because it otherwise reads as a
        # transposed pair of numbers.
        if any(a < b for a, b in zip(counts, counts[1:])):
            assert stream.contract_width.note.strip(), stream.id
