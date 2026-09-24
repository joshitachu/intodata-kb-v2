"""Tests for curated-topology precedence helpers and the stale-judgement overlay."""

from __future__ import annotations

import json
from pathlib import Path

from mcp_server.services.business_topology.curated_overlay import (
    curated_is_fresh,
    load_curated_topology,
    overlay_curated_judgement,
)
from mcp_server.services.business_topology.models import BusinessTopology

CURRENT = "fp-current"
OLD = "fp-old"


def _claim(id_: str, **extra) -> dict:
    return {
        "id": id_,
        "name": extra.pop("name", id_),
        "description": extra.pop("description", ""),
        "status": extra.pop("status", "inferred"),
        "confidence": extra.pop("confidence", 0.7),
        "evidence_ids": extra.pop("evidence_ids", ["artifact:wo"]),
        **extra,
    }


def _topology(fingerprint: str, **collections) -> dict:
    return {
        "schema_version": "business-topology-v1",
        "scan_fingerprint": fingerprint,
        "metadata": {"project_id": "acme", "scan_fingerprint": fingerprint},
        "coverage": {
            "total_artifacts": 1,
            "cited_artifacts": 1,
            "total_evidence": 1,
            "cited_evidence": 1,
            "coverage_ratio": 1.0,
            "notes": [],
        },
        **collections,
    }


def _generated() -> BusinessTopology:
    return BusinessTopology.model_validate(
        _topology(
            CURRENT,
            systems=[
                _claim("sys-maximo", name="Maximo"),
                _claim("sys-4ps", name="4PS"),
                _claim("sys-log", name="LogDB"),
            ],
            business_objects=[_claim("bo-workorder", name="WorkOrder")],
            data_streams=[
                _claim(
                    "ds-wo",
                    name="Maximo -> 4PS",
                    source_system_id="sys-maximo",
                    destination_system_id="sys-4ps",
                    business_object_ids=["bo-workorder"],
                    business_summary="generated",
                    artifact_ids=["artifact:wo"],
                    contract_width={"offered": 40, "mapped": 12, "written": 5},
                    lifecycle="unknown",
                ),
                _claim(
                    "ds-log",
                    name="4PS -> LogDB",
                    source_system_id="sys-4ps",
                    destination_system_id="sys-log",
                    business_summary="generated",
                    artifact_ids=["artifact:wo"],
                ),
            ],
        )
    )


def _curated() -> BusinessTopology:
    return BusinessTopology.model_validate(
        _topology(
            OLD,
            systems=[
                # Different ids and a different primary name: matched by alias.
                _claim(
                    "c-sys-mx",
                    name="IBM Maximo",
                    aliases=["Maximo"],
                    owner="Asset management",
                    purpose="Enterprise asset management",
                    status="confirmed",
                    evidence_ids=[],
                ),
                _claim("c-sys-4ps", name="4PS", evidence_ids=[]),
                _claim("c-sys-log", name="logdb", evidence_ids=[]),
                _claim("c-sys-ghost", name="Retired CRM", owner="Sales", evidence_ids=[]),
            ],
            business_objects=[_claim("c-bo-wo", name="Work Order", aliases=["WorkOrder"])],
            data_streams=[
                _claim(
                    "c-ds-wo",
                    source_system_id="c-sys-mx",
                    destination_system_id="c-sys-4ps",
                    business_object_ids=["c-bo-wo"],
                    business_summary="curated",
                    artifact_ids=["artifact:stale"],
                    # Stale scanner fact: must NOT win over the generated width.
                    contract_width={"offered": 400, "mapped": 1, "written": 1},
                    verdict="keep",
                    key_form="WONUM",
                    lifecycle="active",
                    requiredness="required",
                    risk_ids=["c-risk"],
                    status="confirmed",
                ),
                # No business object on either side, one edge on the pair.
                _claim(
                    "c-ds-log",
                    source_system_id="c-sys-4ps",
                    destination_system_id="c-sys-log",
                    business_summary="curated",
                    artifact_ids=["artifact:stale"],
                    verdict="retire",
                ),
                _claim(
                    "c-ds-ghost",
                    source_system_id="c-sys-ghost",
                    destination_system_id="c-sys-4ps",
                    business_summary="curated",
                    artifact_ids=["artifact:stale"],
                    verdict="retire",
                ),
            ],
            business_rules=[
                _claim(
                    "c-rule",
                    statement="Only approved work orders are sent",
                    stream_ids=["c-ds-wo"],
                    owner="Operations",
                    status="confirmed",
                ),
                _claim("c-rule-ghost", statement="CRM wins", stream_ids=["c-ds-ghost"]),
            ],
            risks=[_claim("c-risk", stream_ids=["c-ds-wo"], severity="high")],
            unknowns=[
                _claim("c-unk-global", question="Who owns the landscape?", status="not_established"),
                _claim("c-unk-ghost", question="Is CRM gone?", stream_ids=["c-ds-ghost"]),
            ],
            conflicts=[
                _claim(
                    "c-conf",
                    conflict_type="unresolved",
                    subject_refs=["c-ds-wo", "c-rule"],
                    supporting_evidence_ids=["doc:x"],
                    evidence_ids=["doc:x"],
                ),
                _claim(
                    "c-conf-ghost",
                    conflict_type="unresolved",
                    subject_refs=["c-ds-ghost"],
                ),
            ],
        )
    )


def test_load_curated_topology_is_strict(tmp_path: Path) -> None:
    path = tmp_path / "business_topology.curated.json"
    assert load_curated_topology(path, project_id="acme").topology is None
    assert not load_curated_topology(path, project_id="acme").present

    path.write_text(json.dumps({**_topology(CURRENT), "unexpected": 1}), encoding="utf-8")
    invalid = load_curated_topology(path, project_id="acme")
    assert invalid.topology is None and "invalid" in invalid.error

    path.write_text(json.dumps(_topology(CURRENT)), encoding="utf-8")
    assert load_curated_topology(path, project_id="acme").topology is not None
    other = load_curated_topology(path, project_id="other")
    assert other.topology is None and "project_id" in other.error


def test_curated_freshness_is_an_exact_fingerprint_match() -> None:
    curated = BusinessTopology.model_validate(_topology(CURRENT))
    assert curated_is_fresh(curated, CURRENT)
    assert not curated_is_fresh(curated, OLD)
    assert not curated_is_fresh(curated, "")


def test_stale_overlay_carries_analyst_judgement() -> None:
    generated = _generated()
    result = overlay_curated_judgement(generated, _curated())
    topology = result.topology
    streams = {stream.id: stream for stream in topology.data_streams}

    # Stream judgement is overlaid onto the matched generated stream ...
    wo = streams["ds-wo"]
    assert (wo.verdict, wo.key_form, wo.lifecycle, wo.requiredness) == (
        "keep",
        "WONUM",
        "active",
        "required",
    )
    assert streams["ds-log"].verdict == "retire"
    # ... but generated scanner facts win over the stale curated ones, and the
    # generated stream keeps its own status and artifacts.
    assert wo.contract_width is not None and wo.contract_width.offered == 40
    assert wo.status == "inferred"
    assert wo.artifact_ids == ["artifact:wo"]

    maximo = next(system for system in topology.systems if system.id == "sys-maximo")
    assert maximo.owner == "Asset management"
    assert maximo.purpose == "Enterprise asset management"
    assert maximo.status == "inferred"

    [rule] = [item for item in topology.business_rules]
    assert rule.id == "c-rule" and rule.stream_ids == ["ds-wo"]
    assert rule.status == "confirmed"  # original status kept
    [risk] = topology.risks
    assert risk.stream_ids == ["ds-wo"] and "c-risk" in wo.risk_ids
    assert [unknown.id for unknown in topology.unknowns] == ["c-unk-global"]
    [conflict] = topology.conflicts
    assert set(conflict.subject_refs) == {"ds-wo", "c-rule"}
    assert "c-conf" in wo.conflict_ids

    # The generated input is not mutated.
    assert generated.data_streams[0].verdict == ""


def test_unmatched_curated_items_are_reported_not_dropped() -> None:
    result = overlay_curated_judgement(_generated(), _curated())
    assert result.unmatched == {
        "systems": ["c-sys-ghost"],
        "data_streams": ["c-ds-ghost"],
        "unknowns": ["c-unk-ghost"],
        "business_rules": ["c-rule-ghost"],
        "conflicts": ["c-conf-ghost"],
    }
    assert result.unmatched_count == 5
    metadata = result.state_metadata()
    assert metadata["unmatched_count"] == 5
    notes = " ".join(result.topology.coverage.notes)
    assert "could not be matched" in notes
    for item in result.unmatched_ids:
        assert item in notes
    # Provenance of what WAS overlaid is recorded in the served contract.
    assert "Analyst-curated judgement overlaid" in notes


def test_overlay_does_not_guess_between_ambiguous_streams() -> None:
    generated = _generated()
    # A second, unlabelled Maximo -> 4PS edge next to the WorkOrder one.
    payload = generated.model_dump(mode="json")
    payload["data_streams"].append(
        _claim(
            "ds-wo-2",
            source_system_id="sys-maximo",
            destination_system_id="sys-4ps",
            business_summary="generated",
            artifact_ids=["artifact:wo"],
        )
    )
    curated = _topology(
        OLD,
        systems=[_claim("a", name="Maximo"), _claim("b", name="4PS")],
        data_streams=[
            _claim(
                "c-unlabelled",
                source_system_id="a",
                destination_system_id="b",
                business_summary="x",
                verdict="keep",
                business_object_ids=["c-bo-invoice"],
            )
        ],
        business_objects=[_claim("c-bo-invoice", name="Invoice")],
    )
    result = overlay_curated_judgement(
        BusinessTopology.model_validate(payload), BusinessTopology.model_validate(curated)
    )
    # The curated Invoice flow names an object the generated side does not
    # have; binding its verdict to either edge would be a guess.
    assert result.unmatched["data_streams"] == ["c-unlabelled"]
    assert all(stream.verdict == "" for stream in result.topology.data_streams)


def test_overlaid_prose_is_redacted() -> None:
    curated = _curated().model_dump(mode="json")
    curated["business_rules"][0]["statement"] = "Send via https://svc:hunter2@erp.internal/api"
    result = overlay_curated_judgement(_generated(), BusinessTopology.model_validate(curated))
    rule = next(item for item in result.topology.business_rules if item.id == "c-rule")
    assert "hunter2" not in rule.statement
