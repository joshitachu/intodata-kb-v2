"""Focused tests for scanner-to-Runtime static topology publication."""

from __future__ import annotations

import json
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_server.services.static_topology_publisher import (
    StaticTopologyPublicationError,
    StaticTopologyPublisher,
    StaticTopologyRedactionRefusal,
)


def _write_completed_scan(tmp_path, *, verification_passed: bool = True) -> None:
    scan_dir = tmp_path / "scan"
    scan_dir.mkdir()
    (tmp_path / "scan_result.json").write_text(json.dumps({
        "platform": "talend",
        "jobs": [
            {
                "job_id": "root/A::Producer",
                "name": "Producer",
                "source_root": "root/A",
                "components": [{"unique_name": "tInput_1", "component_type": "tFileInputDelimited"}],
            },
            {
                "job_id": "root/B::Consumer",
                "name": "Consumer",
                "source_root": "root/B",
                "components": [{"unique_name": "tMap_1", "component_type": "tMap"}],
            },
        ],
    }), encoding="utf-8")
    (scan_dir / "manifest.json").write_text(json.dumps({
        "artifact_relations": [{
            "source": "root/A::Producer",
            "target": "root/B::Consumer",
            "resolution": "resolved",
            "relation_type": "job_calls_job",
            "provenance": "tRunJob",
            "component_name": "tRunJob_1",
        }],
    }), encoding="utf-8")
    (scan_dir / "lineage.json").write_text(json.dumps({
        "entries": [{
            "job_name": "root/B::Consumer",
            "source_job_name": "root/A::Producer",
            "source_component": "tInput_1",
            "target_component": "tMap_1",
            "source_table": "source",
            "source_column": "id",
            "target_table": "target",
            "target_column": "id",
            "confidence": "inferred",
            "transformations": [{"transformation_type": "table_identity", "expression": "source.id"}],
        }],
    }), encoding="utf-8")
    (scan_dir / "landscape.json").write_text(json.dumps({
        "applications": [], "systems": [], "edges": [],
    }), encoding="utf-8")
    if verification_passed:
        _write_publication_approval(tmp_path)


def _write_publication_approval(tmp_path) -> None:
    artifacts = {}
    for relative_path in (
        "scan_result.json",
        "scan/manifest.json",
        "scan/lineage.json",
        "scan/landscape.json",
    ):
        raw = (tmp_path / relative_path).read_bytes()
        artifacts[relative_path] = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    (tmp_path / "publication_approval.json").write_text(json.dumps({
        "schema_version": 1,
        "approved": True,
        "allow_redaction_failures": False,
        "artifacts": artifacts,
        "verifications": {path: {"passed": True} for path in artifacts},
    }), encoding="utf-8")


def test_compile_snapshot_is_canonical_and_preserves_exact_scanner_ids(tmp_path):
    _write_completed_scan(tmp_path)

    snapshot = StaticTopologyPublisher().compile_snapshot(
        project_id="topology", output_dir=tmp_path, source_sequence=42,
    )

    assert snapshot["project_id"] == "topology"
    assert snapshot["source_sequence"] == 42
    assert snapshot["scan_revision"].startswith("sha256:")
    assert [job["static_job_id"] for job in snapshot["jobs"]] == [
        "root/A::Producer", "root/B::Consumer",
    ]
    assert snapshot["jobs"][0]["components"][0]["unique_name"] == "tInput_1"
    assert {edge["dependency_type"] for edge in snapshot["dependencies"]} == {
        "control_flow", "data_lineage",
    }
    assert snapshot["lineage"][0]["expressions"] == ["source.id"]
    assert all(item["evidence_id"] for item in snapshot["dependencies"] + snapshot["lineage"])


def test_compile_refuses_unapproved_redaction_receipt(tmp_path):
    _write_completed_scan(tmp_path, verification_passed=False)

    with pytest.raises(StaticTopologyRedactionRefusal):
        StaticTopologyPublisher().compile_snapshot(
            project_id="topology", output_dir=tmp_path, source_sequence=1,
        )


def test_compile_refuses_artifact_changed_after_final_approval(tmp_path):
    _write_completed_scan(tmp_path)
    lineage_path = tmp_path / "scan" / "lineage.json"
    lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
    lineage["entries"][0]["transformations"][0]["expression"] = "changed after approval"
    lineage_path.write_text(json.dumps(lineage), encoding="utf-8")

    with pytest.raises(StaticTopologyRedactionRefusal, match="approved hash"):
        StaticTopologyPublisher().compile_snapshot(
            project_id="topology", output_dir=tmp_path, source_sequence=1,
        )


def test_compile_omits_oversized_expression_without_truncating_it(tmp_path):
    _write_completed_scan(tmp_path)
    lineage_path = tmp_path / "scan" / "lineage.json"
    lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
    lineage["entries"][0]["transformations"][0]["expression"] = "x" * 1_001
    lineage_path.write_text(json.dumps(lineage), encoding="utf-8")

    _write_publication_approval(tmp_path)
    snapshot = StaticTopologyPublisher().compile_snapshot(
        project_id="topology", output_dir=tmp_path, source_sequence=1,
    )

    assert snapshot["lineage"][0]["transformation_types"] == ["table_identity"]
    assert snapshot["lineage"][0]["expressions"] == []


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda data: data["jobs"].append({
                "job_id": "root/A::Producer", "name": "Duplicate", "source_root": "root/C", "components": [],
            }),
            "duplicate scanner static job ID",
        ),
        (
            lambda data: data["artifact_relations"][0].update({"target": "root/Z::Missing"}),
            "unknown scanner job",
        ),
    ],
)
def test_compile_refuses_duplicate_ids_and_invalid_resolved_references(tmp_path, mutate, expected):
    _write_completed_scan(tmp_path)
    target = (
        tmp_path / "scan_result.json"
        if "duplicate" in expected
        else tmp_path / "scan" / "manifest.json"
    )
    data = json.loads(target.read_text(encoding="utf-8"))
    mutate(data)
    target.write_text(json.dumps(data), encoding="utf-8")
    _write_publication_approval(tmp_path)

    with pytest.raises(StaticTopologyPublicationError, match=expected):
        StaticTopologyPublisher().compile_snapshot(
            project_id="topology", output_dir=tmp_path, source_sequence=1,
        )


def test_compile_excludes_resolved_non_invocation_relation(tmp_path):
    _write_completed_scan(tmp_path)
    manifest_path = tmp_path / "scan" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_relations"].append({
        "source": "root/A::Producer",
        "target": "root/B::Consumer",
        "resolution": "resolved",
        "relation_type": "shares_connection",
    })
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _write_publication_approval(tmp_path)

    snapshot = StaticTopologyPublisher().compile_snapshot(
        project_id="topology", output_dir=tmp_path, source_sequence=1,
    )

    assert len([edge for edge in snapshot["dependencies"] if edge["dependency_type"] == "control_flow"]) == 1


async def test_publish_posts_runtime_contract_with_ingest_auth(tmp_path):
    _write_completed_scan(tmp_path)
    publisher = StaticTopologyPublisher(base_url="http://runtime.test/", ingest_api_key="ingest-secret")
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"project_id": "topology", "scan_revision": "sha256:test"}

    with patch("mcp_server.services.static_topology_publisher.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.post.return_value = response
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client_cls.return_value = client

        result = await publisher.publish(
            project_id="topology", output_dir=tmp_path, source_sequence=7,
        )

    assert result["project_id"] == "topology"
    call = client.post.call_args
    assert call.args[0] == "http://runtime.test/api/v1/ingest/runtime-static-topology"
    assert call.kwargs["headers"]["Authorization"] == "Bearer ingest-secret"
    assert call.kwargs["json"]["project_id"] == "topology"
    assert call.kwargs["json"]["source_sequence"] == 7
