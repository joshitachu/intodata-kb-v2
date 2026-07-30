from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.experiments.application.snapshot_sources import (
    CreateSourceSnapshotRequest, SourceSnapshotPersistence, SourceSnapshotService,
)
from mcp_server.experiments.domain.sources import SourceDescriptor, SourceLimits, SourceSnapshot
from mcp_server.experiments.infrastructure.sources.governed_directory import DirectoryEvidenceAdapter
from mcp_server.experiments.infrastructure.sources.intodoc_scan import IntodocScanSourceAdapter
from mcp_server.experiments.infrastructure.sources.registry import SourceAdapterError, SourceAdapterRegistry


def _descriptor(kind: str, root: Path, **kwargs: object) -> SourceDescriptor:
    return SourceDescriptor(
        id=f"{kind}-source",
        source_kind=kind,
        source_role="implementation" if kind == "intodoc_scan" else "operational_policy",
        source_path=root,
        provenance_locator=f"fixture://{kind}",
        capabilities=frozenset({"searchable_text", "structured_records"}),
        **kwargs,
    )


def test_alitalend_shaped_and_non_code_sources_use_identical_snapshot_schema(tmp_path: Path) -> None:
    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "scan_result.json").write_text('{"jobs": [{"name": "JobA"}]}', encoding="utf-8")
    (scan / "redaction.json").write_text(json.dumps({"approved": True}), encoding="utf-8")
    logistics = tmp_path / "logistics"
    logistics.mkdir()
    (logistics / "sop.md").write_text("# Warehouse SOP", encoding="utf-8")
    (logistics / "events.csv").write_text("shipment,status\nS1,delayed\n", encoding="utf-8")
    registry = SourceAdapterRegistry((
        IntodocScanSourceAdapter(), DirectoryEvidenceAdapter(allowed_roots=(logistics,)),
    ))
    service = SourceSnapshotService(registry=registry)

    scan_snapshot = service.create(CreateSourceSnapshotRequest(
        descriptors=(_descriptor("intodoc_scan", scan, redaction_receipt_path=scan / "redaction.json"),),
        snapshot_id="scan-snapshot",
    ))
    logistics_snapshot = service.create(CreateSourceSnapshotRequest(
        descriptors=(_descriptor("directory_evidence", logistics),), snapshot_id="logistics-snapshot"
    ))

    assert scan_snapshot.snapshot.schema_version == logistics_snapshot.snapshot.schema_version == 1
    assert scan_snapshot.snapshot.security_state.value == logistics_snapshot.snapshot.security_state.value == "approved"
    assert scan_snapshot.snapshot.items[0].source_kind == "intodoc_scan"
    assert {item.source_kind for item in logistics_snapshot.snapshot.items} == {"directory_evidence"}
    assert scan_snapshot.snapshot.manifest_sha256 != logistics_snapshot.snapshot.manifest_sha256


def test_redaction_failure_blocks_intodoc_snapshot(tmp_path: Path) -> None:
    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "scan_result.json").write_text("{}", encoding="utf-8")
    receipt = scan / "redaction.json"
    receipt.write_text('{"approved": false}', encoding="utf-8")
    service = SourceSnapshotService(registry=SourceAdapterRegistry((IntodocScanSourceAdapter(),)))

    with pytest.raises(SourceAdapterError, match="approved redaction"):
        service.create(CreateSourceSnapshotRequest(descriptors=(
            _descriptor("intodoc_scan", scan, redaction_receipt_path=receipt),
        )))


def test_established_intodoc_redaction_report_is_an_approved_receipt(tmp_path: Path) -> None:
    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "scan_result.json").write_text("{}", encoding="utf-8")
    receipt = scan / "redaction_report.json"
    receipt.write_text(
        json.dumps(
            {
                "redaction_level": "standard",
                "total_redactions": 3,
                "summary": {"password": 2, "host": 1},
            }
        ),
        encoding="utf-8",
    )
    service = SourceSnapshotService(
        registry=SourceAdapterRegistry((IntodocScanSourceAdapter(),))
    )

    result = service.create(
        CreateSourceSnapshotRequest(
            descriptors=(
                _descriptor(
                    "intodoc_scan",
                    scan,
                    redaction_receipt_path=receipt,
                ),
            )
        )
    )

    assert result.snapshot.security_state.value == "approved"


def test_governed_directory_rejects_path_outside_injected_root_and_caps(tmp_path: Path) -> None:
    governed = tmp_path / "governed"
    governed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("not allowed", encoding="utf-8")
    service = SourceSnapshotService(registry=SourceAdapterRegistry((
        DirectoryEvidenceAdapter(allowed_roots=(governed,)),
    )))

    with pytest.raises(SourceAdapterError, match="outside configured"):
        service.create(CreateSourceSnapshotRequest(descriptors=(_descriptor("directory_evidence", outside),)))

    (governed / "large.md").write_text("x" * 16, encoding="utf-8")
    with pytest.raises(SourceAdapterError, match="limit exceeded"):
        service.create(CreateSourceSnapshotRequest(
            descriptors=(_descriptor("directory_evidence", governed),),
            limits=SourceLimits(max_files=1, max_bytes=8),
        ))


def test_snapshot_manifest_hash_is_deterministic_and_duplicate_identity_is_rejected(tmp_path: Path) -> None:
    governed = tmp_path / "governed"
    governed.mkdir()
    (governed / "policy.md").write_text("policy", encoding="utf-8")
    service = SourceSnapshotService(registry=SourceAdapterRegistry((
        DirectoryEvidenceAdapter(allowed_roots=(governed,)),
    )))
    descriptor = _descriptor("directory_evidence", governed)

    first = service.create(CreateSourceSnapshotRequest(descriptors=(descriptor,), snapshot_id="first"))
    second = service.create(CreateSourceSnapshotRequest(descriptors=(descriptor,), snapshot_id="second"))

    assert first.snapshot.manifest_sha256 == second.snapshot.manifest_sha256
    with pytest.raises(ValueError, match="unique"):
        service.create(CreateSourceSnapshotRequest(descriptors=(descriptor, descriptor)))


def test_snapshot_captures_approved_content_before_source_changes_and_sink_failure_is_not_finalized(tmp_path: Path) -> None:
    class RecordingSink:
        def __init__(self) -> None:
            self.contents: dict[str, bytes] = {}

        def save_snapshot(self, snapshot: SourceSnapshot) -> SourceSnapshotPersistence:
            self.contents = {content.item_id: content.extracted_bytes for content in snapshot.contents}
            return SourceSnapshotPersistence(
                snapshot_id=snapshot.id,
                content_relative_paths={item_id: f"shared/evidence/{item_id}" for item_id in self.contents},
                manifest_relative_path="shared/evidence/manifest.json",
            )

    governed = tmp_path / "governed"
    governed.mkdir()
    original = governed / "approved.md"
    original.write_bytes(b"approved snapshot bytes")
    descriptor = _descriptor("directory_evidence", governed)
    sink = RecordingSink()
    service = SourceSnapshotService(
        registry=SourceAdapterRegistry((DirectoryEvidenceAdapter(allowed_roots=(governed,)),)), sink=sink
    )

    result = service.create(CreateSourceSnapshotRequest(descriptors=(descriptor,), snapshot_id="immutable"))
    original.unlink()
    content = result.snapshot.contents[0]
    assert sink.contents[content.item_id] == b"approved snapshot bytes"
    assert content.extracted_sha256 == result.snapshot.items[0].extracted_sha256
    assert result.persistence is not None

    class FailingSink:
        def save_snapshot(self, snapshot: SourceSnapshot) -> SourceSnapshotPersistence:
            raise RuntimeError("storage failure")

    (governed / "again.md").write_bytes(b"approved snapshot bytes")
    failing = SourceSnapshotService(
        registry=SourceAdapterRegistry((DirectoryEvidenceAdapter(allowed_roots=(governed,)),)), sink=FailingSink()
    )
    with pytest.raises(RuntimeError, match="storage failure"):
        failing.create(CreateSourceSnapshotRequest(descriptors=(descriptor,), snapshot_id="failed"))
