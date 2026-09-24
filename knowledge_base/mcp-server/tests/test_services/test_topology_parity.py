"""Parity scoring between a generated topology and a curated baseline.

Synthetic topologies only: the real baseline is confidential client data.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_server.services.business_topology.models import BusinessTopology
from mcp_server.services.business_topology.parity import (
    render_parity_table,
    score_topology_parity,
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "topology_parity.py"


def _claim(identifier: str, **extra):
    return {
        "id": identifier,
        "name": identifier,
        "description": "",
        "status": "inferred",
        "confidence": 0.7,
        "evidence_ids": ["ev:1"],
        **extra,
    }


def _topology(*, rich: bool) -> dict:
    stream_extra = (
        {
            "mechanism": "queue relay",
            "lifecycle": "active",
            "direction_pattern": "push",
            "contract_width": {"offered": 40, "mapped": 12, "written": None, "note": ""},
            "conditions": [{"kind": "filter", "statement": "Only closed orders."}],
            "runtime_observation_ids": ["ro-1"],
        }
        if rich
        else {}
    )
    streams = [
        _claim(
            "ds-1",
            source_system_id="sys-a",
            destination_system_id="sys-b",
            business_summary="A sends orders to B.",
            artifact_ids=["artifact:1"],
            **stream_extra,
        )
    ]
    if rich:
        streams.append(
            _claim(
                "ds-2",
                source_system_id="sys-b",
                destination_system_id="sys-a",
                business_summary="B confirms orders.",
                artifact_ids=["artifact:2"],
                lifecycle="unknown",
            )
        )
    payload = {
        "schema_version": "business-topology-v1",
        "scan_fingerprint": "scan:synthetic",
        "metadata": {"project_id": "synthetic", "scan_fingerprint": "scan:synthetic"},
        "systems": [
            _claim("sys-a", owner="Team A" if rich else ""),
            _claim("sys-b"),
        ],
        "data_streams": streams,
        "coverage": {
            "total_artifacts": 2,
            "cited_artifacts": 2,
            "total_evidence": 1,
            "cited_evidence": 1,
            "coverage_ratio": 1.0,
        },
    }
    if rich:
        payload["runtime_observations"] = [
            _claim(
                "ro-1",
                stream_ids=["ds-1"],
                observed_behavior="Runs every 30 minutes.",
                kind="cadence",
                metrics=[
                    {"label": "runs", "value": "48", "window": "1 day", "source": "mirror"},
                    {"label": "failures", "value": "0", "window": "", "source": "mirror"},
                ],
            )
        ]
    return payload


def _field(report, path):
    return next(item for item in report.fields if item.path == path)


def test_synthetic_fixtures_are_contract_valid():
    # Guards the fixtures themselves: a parity test over an invalid shape
    # would measure something the producer could never publish.
    BusinessTopology.model_validate(_topology(rich=True))
    BusinessTopology.model_validate(_topology(rich=False))


def test_identical_topologies_score_one_and_miss_nothing():
    baseline = _topology(rich=True)
    report = score_topology_parity(baseline, baseline)
    assert report.overall_score == pytest.approx(1.0)
    assert report.missing_fields == []


def test_counts_fill_ratios_and_missing_fields():
    report = score_topology_parity(_topology(rich=False), _topology(rich=True))
    collections = {item.name: item for item in report.collections}
    assert collections["data_streams"].candidate_count == 1
    assert collections["data_streams"].baseline_count == 2
    assert collections["data_streams"].count_ratio == pytest.approx(0.5)
    assert collections["runtime_observations"].candidate_count == 0

    owner = _field(report, "systems.owner")
    assert (owner.baseline_filled, owner.baseline_items) == (1, 2)
    assert (owner.candidate_filled, owner.candidate_items) == (0, 2)

    assert "systems.owner" in report.missing_fields
    assert "data_streams.mechanism" in report.missing_fields
    assert "runtime_observations.metrics" in report.missing_fields
    # A field neither side fills is not "missing".
    assert "data_streams.key_form" not in report.missing_fields
    assert 0.0 < report.overall_score < 1.0


def test_every_contract_field_is_measured():
    """The field list comes from the models; nothing is hand-maintained."""

    report = score_topology_parity({}, _topology(rich=True))
    paths = {item.path for item in report.fields}
    for path in (
        "data_streams.delivery_path_id",
        "data_streams.conflict_ids",
        "data_streams.contract_width.offered",
        "data_streams.conditions[].statement",
        "data_streams.deployment.agreement",
        "delivery_paths.segments[].mode",
        "transformations.mappings[].reaches_target",
        "business_rules.owner_question",
        "runtime_observations.metrics[].window",
        "conflicts.resolution",
        "lineage_annotations.business_meaning",
        "coverage.grounded_technical_relationships",
    ):
        assert path in paths, path
    # An empty candidate still reports without raising.
    assert report.overall_score < 0.5


def test_not_established_default_does_not_count_as_filled():
    report = score_topology_parity(_topology(rich=True), _topology(rich=True))
    lifecycle = _field(report, "data_streams.lifecycle")
    # ds-2 explicitly says lifecycle "unknown": that is not a fact.
    assert (lifecycle.baseline_filled, lifecycle.baseline_items) == (1, 2)
    # A substantive default is an answer.
    kind = _field(report, "systems.kind")
    assert kind.baseline_filled == 0  # absent in the synthetic payload
    with_kind = _topology(rich=True)
    for system in with_kind["systems"]:
        system["kind"] = "software_system"
    kind = _field(score_topology_parity(with_kind, with_kind), "systems.kind")
    assert kind.baseline_filled == 2


def test_nested_denominators():
    report = score_topology_parity(_topology(rich=True), _topology(rich=True))
    window = _field(report, "runtime_observations.metrics[].window")
    assert (window.baseline_filled, window.baseline_items) == (1, 2)
    # Optional nested objects keep the PARENT count as denominator.
    offered = _field(report, "data_streams.contract_width.offered")
    assert (offered.baseline_filled, offered.baseline_items) == (1, 2)
    written = _field(report, "data_streams.contract_width.written")
    assert written.baseline_filled == 0


def test_render_is_ascii_and_names_missing_fields():
    report = score_topology_parity(_topology(rich=False), _topology(rich=True))
    text = render_parity_table(report, candidate_label="synthetic")
    text.encode("ascii")
    assert "systems.owner" in text
    assert "overall=" in text


def test_cli_prints_table_and_json(tmp_path):
    candidate = tmp_path / "candidate.json"
    baseline = tmp_path / "baseline.json"
    candidate.write_text(json.dumps(_topology(rich=False)), encoding="utf-8")
    baseline.write_text(json.dumps(_topology(rich=True)), encoding="utf-8")

    table = subprocess.run(
        [sys.executable, str(SCRIPT), "--candidate", str(candidate), "--baseline", str(baseline)],
        check=True, capture_output=True, text=True,
    )
    assert "Field fill" in table.stdout

    as_json = subprocess.run(
        [sys.executable, str(SCRIPT), "--candidate", str(candidate), "--baseline", str(baseline), "--json"],
        check=True, capture_output=True, text=True,
    )
    payload = json.loads(as_json.stdout)
    assert "systems.owner" in payload["missing_fields"]
    assert 0.0 < payload["overall_score"] < 1.0
