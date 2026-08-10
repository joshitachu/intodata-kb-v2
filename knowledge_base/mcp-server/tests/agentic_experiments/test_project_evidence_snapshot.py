from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from mcp_server.experiments.application.snapshot_project_evidence import (
    CreateProjectEvidenceSnapshotRequest,
    PreparedEvidenceLimits,
    ProjectEvidenceSnapshotService,
)
from mcp_server.experiments.domain.project_evidence import (
    ProjectEvidencePreparedContent,
    ProjectEvidenceSnapshot,
)
from mcp_server.experiments.domain.sources import (
    RedactionState,
    SourceDescriptor,
    SourceLimits,
    SourceSnapshotContent,
    SourceSnapshotItem,
)
from mcp_server.experiments.infrastructure.sources.governed_directory import (
    DirectoryEvidenceAdapter,
)
from mcp_server.experiments.infrastructure.sources.intodoc_scan import IntodocScanSourceAdapter
from mcp_server.experiments.infrastructure.sources.registry import (
    AdapterSnapshotResult,
    CapturedRawSource,
    SourceAdapterRegistry,
)
from mcp_server.experiments.infrastructure.sources.runtime_export import RuntimeExportAdapter
from mcp_server.experiments.infrastructure.storage.artifact_store import ArtifactStore
from mcp_server.experiments.infrastructure.storage.canonical_json import canonical_json_bytes
from mcp_server.experiments.infrastructure.storage.project_evidence_snapshot_store import (
    ProjectEvidenceSnapshotArtifactSink,
)


def _descriptor(
    *,
    identifier: str,
    source_kind: str,
    role: str,
    path: Path,
    version: str,
    **kwargs: object,
) -> SourceDescriptor:
    receipt_path = kwargs.pop("redaction_receipt_path", None)
    if receipt_path is None:
        receipt_path = path.parent / f".{identifier}-redaction.json"
        source_paths = _regular_source_paths(path, receipt_path)
        if source_paths:
            _write_bound_receipt(receipt_path, source_paths)
        else:
            _write_bound_receipt_bytes(
                receipt_path,
                (f"prepared:{identifier}".encode("utf-8"),),
            )
    return SourceDescriptor(
        id=identifier,
        source_kind=source_kind,
        source_role=role,
        source_path=path,
        provenance_locator=f"fixture://evidence/{identifier}",
        capabilities=frozenset({"searchable_text"}),
        metadata={"version": version},
        redaction_receipt_path=receipt_path,
        **kwargs,
    )


def _regular_source_paths(path: Path, receipt_path: Path) -> tuple[Path, ...]:
    if path.is_file():
        return (path,)
    if not path.is_dir():
        return ()
    return tuple(
        candidate
        for candidate in sorted(path.rglob("*"))
        if candidate.is_file() and candidate != receipt_path
    )


def _write_bound_receipt(path: Path, sources: tuple[Path, ...]) -> None:
    _write_bound_receipt_bytes(path, tuple(source.read_bytes() for source in sources))


def _write_bound_receipt_bytes(path: Path, sources: tuple[bytes, ...]) -> None:
    entries = sorted(
        (
            {
                "raw_sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
            for content in sources
        ),
        key=lambda value: (value["raw_sha256"], value["size_bytes"]),
    )
    path.write_text(
        json.dumps(
            {"schema_version": 1, "source_files": entries, "status": "approved"},
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _service(supporting_root: Path) -> ProjectEvidenceSnapshotService:
    return ProjectEvidenceSnapshotService(
        registry=SourceAdapterRegistry(
            (
                IntodocScanSourceAdapter(),
                DirectoryEvidenceAdapter(allowed_roots=(supporting_root,)),
                RuntimeExportAdapter(),
            )
        )
    )


def _refingerprinted_manifest(manifest: dict[str, object]) -> bytes:
    fingerprint_payload = dict(manifest)
    fingerprint_payload.pop("fingerprint_sha256")
    manifest["fingerprint_sha256"] = hashlib.sha256(
        canonical_json_bytes(fingerprint_payload)
    ).hexdigest()
    return canonical_json_bytes(manifest)


class _StaticAdapter:
    version = "1"

    def __init__(
        self,
        *,
        kind: str,
        item_id: str | None = None,
        logical_identity: str | None = None,
        owner: str | None = None,
        source_kind: str | None = None,
        source_role: str | None = None,
        capabilities: frozenset[str] | None = None,
        trust_level: str | None = None,
    ) -> None:
        self.kind = kind
        self.id = f"static-{kind}"
        self.item_id = item_id
        self.logical_identity = logical_identity
        self.owner = owner
        self.item_source_kind = source_kind
        self.item_source_role = source_role
        self.item_capabilities = capabilities
        self.item_trust_level = trust_level

    def supports(self, descriptor: SourceDescriptor) -> bool:
        return descriptor.source_kind == self.kind

    def snapshot(self, descriptor: SourceDescriptor, limits: SourceLimits) -> AdapterSnapshotResult:
        content = f"prepared:{descriptor.id}".encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        logical_identity = self.logical_identity or f"{descriptor.id}:record"
        item = SourceSnapshotItem(
            id=self.item_id or hashlib.sha256(logical_identity.encode("utf-8")).hexdigest(),
            source_descriptor_id=self.owner or descriptor.id,
            logical_identity=logical_identity,
            source_kind=self.item_source_kind or descriptor.source_kind,
            source_role=self.item_source_role or descriptor.source_role,
            capabilities=self.item_capabilities or descriptor.capabilities,
            locator=descriptor.provenance_locator,
            raw_sha256=digest,
            extracted_sha256=digest,
            media_type="text/plain",
            size_bytes=len(content),
            extracted_size_bytes=len(content),
            trust_level=self.item_trust_level or descriptor.trust_level,
            redaction_state=RedactionState.APPROVED,
            adapter_id=self.id,
            adapter_version=self.version,
        )
        return AdapterSnapshotResult(
            items=(item,),
            contents=(
                SourceSnapshotContent(
                    item_id=item.id,
                    extracted_bytes=content,
                    extracted_sha256=digest,
                    media_type="text/plain",
                    size_bytes=len(content),
                ),
            ),
            raw_sources=(CapturedRawSource(logical_identity, digest, len(content)),),
        )


def test_project_evidence_snapshot_freezes_mixed_talend_supporting_and_runtime_sources(
    tmp_path: Path,
) -> None:
    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "scan_result.json").write_text('{"jobs":[{"name":"OrderLoad"}]}', encoding="utf-8")
    receipt = scan / "redaction.json"
    _write_bound_receipt(receipt, (scan / "scan_result.json",))
    supporting = tmp_path / "supporting"
    supporting.mkdir()
    (supporting / "runbook.md").write_text("# Order load runbook\n", encoding="utf-8")
    runtime = tmp_path / "runtime.jsonl"
    runtime.write_text(
        '{"event_id":"evt-1","timestamp":"2026-08-01T00:00:00Z"}\n', encoding="utf-8"
    )

    result = _service(supporting).create(
        CreateProjectEvidenceSnapshotRequest(
            descriptors=(
                _descriptor(
                    identifier="talend-scan",
                    source_kind="intodoc_scan",
                    role="implementation",
                    path=scan,
                    version="scan-v7",
                    redaction_receipt_path=receipt,
                ),
                _descriptor(
                    identifier="supporting-runbook",
                    source_kind="directory_evidence",
                    role="supporting_reference",
                    path=supporting,
                    version="2026.08",
                ),
                _descriptor(
                    identifier="runtime-events",
                    source_kind="runtime_export",
                    role="runtime_observation",
                    path=runtime,
                    version="export-12",
                ),
            )
        )
    )

    manifest = result.manifest()
    assert result.id.startswith("project-evidence-")
    assert manifest["snapshot_id"] == result.id
    assert [entry["descriptor_id"] for entry in manifest["sources"]] == [
        "runtime-events",
        "supporting-runbook",
        "talend-scan",
    ]
    assert {entry["status"] for entry in manifest["sources"]} == {"readable"}
    by_id = {entry["descriptor_id"]: entry for entry in manifest["sources"]}
    assert by_id["talend-scan"]["provenance"]["redaction_receipt_reference"].startswith(
        "receipt://sha256/"
    )
    assert (
        by_id["talend-scan"]["provenance"]["redaction_receipt_sha256"]
        == hashlib.sha256(receipt.read_bytes()).hexdigest()
    )
    assert by_id["talend-scan"]["provenance"]["source_version"] == "scan-v7"
    assert by_id["talend-scan"]["provenance"]["redaction_status"] == "approved"
    assert by_id["talend-scan"]["prepared_representations"][0]["logical_path"] == (
        "scan_result.json"
    )
    assert by_id["runtime-events"]["source_kind"] == "runtime_export"
    assert by_id["runtime-events"]["source_role"] == "runtime_observation"
    assert by_id["runtime-events"]["raw_evidence_references"][0]["raw_sha256"]
    assert (
        by_id["runtime-events"]["prepared_representations"][0]["media_type"] == "application/json"
    )
    assert all("extracted_bytes" not in entry for entry in manifest["sources"])
    assert result.prepared_contents()


def test_scanner_semantic_paths_preserve_known_contracts_and_hash_arbitrary_names(
    tmp_path: Path,
) -> None:
    scan = tmp_path / "scan-output"
    (scan / "scan" / "jobs").mkdir(parents=True)
    sources = (
        scan / "scan_result.json",
        scan / "scan" / "lineage.json",
        scan / "scan" / "jobs" / "SensitiveBusinessGroup" / "SensitiveCustomerJob.json",
    )
    for source in sources:
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("{}", encoding="utf-8")
    receipt = scan / "redaction_report.json"
    _write_bound_receipt(receipt, sources)

    snapshot = _service(tmp_path).create(
        CreateProjectEvidenceSnapshotRequest(
            descriptors=(
                _descriptor(
                    identifier="scanner",
                    source_kind="intodoc_scan",
                    role="implementation",
                    path=scan,
                    version="1",
                    redaction_receipt_path=receipt,
                ),
            )
        )
    )
    logical_paths = {
        item["logical_path"]
        for item in snapshot.manifest()["sources"][0]["prepared_representations"]
    }

    assert "scan_result.json" in logical_paths
    assert "scan/lineage.json" in logical_paths
    job_path = next(path for path in logical_paths if path.startswith("scan/jobs/"))
    assert job_path.endswith(".json")
    assert "SensitiveCustomerJob" not in job_path


def test_project_evidence_denies_unbound_legacy_scanner_receipt(tmp_path: Path) -> None:
    scan = tmp_path / "scan-output"
    scan.mkdir()
    (scan / "scan_result.json").write_text('{"jobs":[]}', encoding="utf-8")
    receipt = scan / "redaction_report.json"
    receipt.write_text(
        json.dumps(
            {"redaction_level": "standard", "summary": {}, "total_redactions": 0}
        ),
        encoding="utf-8",
    )

    snapshot = _service(tmp_path).create(
        CreateProjectEvidenceSnapshotRequest(
            descriptors=(
                _descriptor(
                    identifier="scanner",
                    source_kind="intodoc_scan",
                    role="implementation",
                    path=scan,
                    version="1",
                    redaction_receipt_path=receipt,
                ),
            )
        )
    )

    source = snapshot.manifest()["sources"][0]
    assert source["status"] == "unreadable"
    assert source["status_reason"] == "redaction_receipt_invalid"
    assert source["prepared_representations"] == []


def test_project_evidence_rejects_stale_hash_bound_scanner_receipt(tmp_path: Path) -> None:
    scan = tmp_path / "scan-output"
    scan.mkdir()
    scan_result = scan / "scan_result.json"
    scan_result.write_text('{"jobs":[]}', encoding="utf-8")
    receipt = scan / "redaction_report.json"
    _write_bound_receipt(receipt, (scan_result,))
    scan_result.write_text('{"jobs":[{"name":"changed"}]}', encoding="utf-8")

    snapshot = _service(tmp_path).create(
        CreateProjectEvidenceSnapshotRequest(
            descriptors=(
                _descriptor(
                    identifier="scanner",
                    source_kind="intodoc_scan",
                    role="implementation",
                    path=scan,
                    version="1",
                    redaction_receipt_path=receipt,
                ),
            )
        )
    )

    source = snapshot.manifest()["sources"][0]
    assert source["status"] == "unreadable"
    assert source["status_reason"] == "adapter_capture_failed"
    assert source["prepared_representations"] == []


def test_duplicate_ids_are_rejected_but_versions_remain_distinct_inventory_sources(
    tmp_path: Path,
) -> None:
    supporting = tmp_path / "supporting"
    supporting.mkdir()
    (supporting / "policy.md").write_text("policy", encoding="utf-8")
    service = _service(supporting)
    v1 = _descriptor(
        identifier="policy-v1",
        source_kind="directory_evidence",
        role="supporting_reference",
        path=supporting,
        version="1",
    )
    v2 = _descriptor(
        identifier="policy-v2",
        source_kind="directory_evidence",
        role="supporting_reference",
        path=supporting,
        version="2",
    )

    snapshot = service.create(CreateProjectEvidenceSnapshotRequest(descriptors=(v2, v1)))
    versions = [entry["provenance"]["source_version"] for entry in snapshot.manifest()["sources"]]
    assert versions == ["1", "2"]
    assert (
        snapshot.manifest()["sources"][0]["raw_evidence_references"][0]["item_id"]
        != snapshot.manifest()["sources"][1]["raw_evidence_references"][0]["item_id"]
    )

    with pytest.raises(ValueError, match="ids must be unique"):
        service.create(CreateProjectEvidenceSnapshotRequest(descriptors=(v1, v1)))


def test_unsupported_and_unreadable_sources_are_frozen_without_silent_skips(tmp_path: Path) -> None:
    supporting = tmp_path / "supporting"
    supporting.mkdir()
    (supporting / "good.md").write_text("readable", encoding="utf-8")
    malformed_runtime = tmp_path / "bad.json"
    malformed_runtime.write_text('["not an object"]', encoding="utf-8")
    unsupported = tmp_path / "unhandled.pdf"
    unsupported.write_bytes(b"not parsed")
    result = _service(supporting).create(
        CreateProjectEvidenceSnapshotRequest(
            descriptors=(
                _descriptor(
                    identifier="readable",
                    source_kind="directory_evidence",
                    role="supporting_reference",
                    path=supporting,
                    version="1",
                ),
                _descriptor(
                    identifier="unreadable",
                    source_kind="runtime_export",
                    role="runtime_observation",
                    path=malformed_runtime,
                    version="1",
                ),
                _descriptor(
                    identifier="unsupported",
                    source_kind="pdf_evidence",
                    role="supporting_reference",
                    path=unsupported,
                    version="1",
                ),
            )
        )
    )

    entries = {entry["descriptor_id"]: entry for entry in result.manifest()["sources"]}
    assert entries["readable"]["status"] == "readable"
    assert entries["unreadable"]["status"] == "unreadable"
    assert entries["unreadable"]["status_reason"] == "adapter_capture_failed"
    assert entries["unreadable"]["raw_evidence_references"] == []
    assert entries["unreadable"]["prepared_representations"] == []
    assert entries["unreadable"]["usable_snapshot_manifest_sha256"] is None
    assert entries["unsupported"]["status"] == "unsupported"
    assert entries["unsupported"]["status_reason"] == "no_registered_adapter"
    assert {item_id for item_id, _ in result.prepared_contents()} == {
        reference["item_id"] for reference in entries["readable"]["prepared_representations"]
    }


def test_fingerprint_is_immutable_and_includes_usable_snapshot_identity(tmp_path: Path) -> None:
    supporting = tmp_path / "supporting"
    supporting.mkdir()
    source = supporting / "policy.md"
    source.write_text("first version", encoding="utf-8")
    descriptor = _descriptor(
        identifier="policy",
        source_kind="directory_evidence",
        role="supporting_reference",
        path=supporting,
        version="1",
    )
    service = _service(supporting)

    frozen = service.create(CreateProjectEvidenceSnapshotRequest(descriptors=(descriptor,)))
    frozen_manifest = frozen.manifest()
    frozen_manifest["sources"][0]["status"] = "tampered"
    source.write_text("second version", encoding="utf-8")
    stale_receipt = service.create(CreateProjectEvidenceSnapshotRequest(descriptors=(descriptor,)))
    assert stale_receipt.manifest()["sources"][0]["status"] == "unreadable"
    assert stale_receipt.manifest()["sources"][0]["status_reason"] == "adapter_capture_failed"
    assert descriptor.redaction_receipt_path is not None
    _write_bound_receipt(descriptor.redaction_receipt_path, (source,))
    later = service.create(CreateProjectEvidenceSnapshotRequest(descriptors=(descriptor,)))

    assert frozen.manifest()["sources"][0]["status"] == "readable"
    assert frozen.fingerprint_sha256 != later.fingerprint_sha256
    assert (
        frozen.manifest()["sources"][0]["usable_snapshot_manifest_sha256"]
        != later.manifest()["sources"][0]["usable_snapshot_manifest_sha256"]
    )
    assert frozen.prepared_contents()[0][1] == b"first version"
    assert later.prepared_contents()[0][1] == b"second version"


def test_invalid_prepared_content_is_visible_as_unreadable_not_admitted(tmp_path: Path) -> None:
    class InconsistentPreparedAdapter:
        id = "inconsistent-prepared"
        version = "1"

        def supports(self, descriptor: SourceDescriptor) -> bool:
            return descriptor.source_kind == "inconsistent_prepared"

        def snapshot(
            self, descriptor: SourceDescriptor, limits: SourceLimits
        ) -> AdapterSnapshotResult:
            expected = b"expected prepared bytes"
            actual = b"different prepared bytes"
            logical_identity = f"{descriptor.id}:record"
            item = SourceSnapshotItem(
                id=hashlib.sha256(logical_identity.encode("utf-8")).hexdigest(),
                source_descriptor_id=descriptor.id,
                logical_identity=logical_identity,
                source_kind=descriptor.source_kind,
                source_role=descriptor.source_role,
                capabilities=descriptor.capabilities,
                locator=descriptor.provenance_locator,
                raw_sha256=hashlib.sha256(expected).hexdigest(),
                extracted_sha256=hashlib.sha256(expected).hexdigest(),
                media_type="text/plain",
                size_bytes=len(expected),
                extracted_size_bytes=len(expected),
                trust_level=descriptor.trust_level,
                redaction_state=RedactionState.APPROVED,
                adapter_id=self.id,
                adapter_version=self.version,
            )
            content = SourceSnapshotContent(
                item_id=item.id,
                extracted_bytes=actual,
                extracted_sha256=hashlib.sha256(actual).hexdigest(),
                media_type="application/json",
                size_bytes=len(actual),
            )
            return AdapterSnapshotResult(items=(item,), contents=(content,))

    descriptor = _descriptor(
        identifier="inconsistent",
        source_kind="inconsistent_prepared",
        role="supporting_reference",
        path=tmp_path,
        version="1",
    )
    service = ProjectEvidenceSnapshotService(
        registry=SourceAdapterRegistry((InconsistentPreparedAdapter(),))
    )

    result = service.create(CreateProjectEvidenceSnapshotRequest(descriptors=(descriptor,)))

    entry = result.manifest()["sources"][0]
    assert entry["status"] == "unreadable"
    assert entry["status_reason"] == "adapter_capture_failed"
    assert result.prepared_contents() == ()


@pytest.mark.parametrize(
    "limits",
    (
        SourceLimits(max_files=1, max_bytes=100),
        SourceLimits(max_files=10, max_bytes=7),
    ),
)
def test_aggregate_limits_apply_across_individually_valid_descriptors(
    tmp_path: Path, limits: SourceLimits
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "one.md").write_text("1234", encoding="utf-8")
    (second / "two.md").write_text("5678", encoding="utf-8")
    service = ProjectEvidenceSnapshotService(
        registry=SourceAdapterRegistry((DirectoryEvidenceAdapter(allowed_roots=(first, second)),))
    )
    descriptors = (
        _descriptor(
            identifier="first",
            source_kind="directory_evidence",
            role="supporting_reference",
            path=first,
            version="1",
        ),
        _descriptor(
            identifier="second",
            source_kind="directory_evidence",
            role="supporting_reference",
            path=second,
            version="1",
        ),
    )

    with pytest.raises(ValueError, match="aggregate project evidence"):
        service.create(CreateProjectEvidenceSnapshotRequest(descriptors=descriptors, limits=limits))


def test_public_snapshot_state_is_deeply_immutable(tmp_path: Path) -> None:
    supporting = tmp_path / "supporting"
    supporting.mkdir()
    (supporting / "policy.md").write_text("policy", encoding="utf-8")
    snapshot = _service(supporting).create(
        CreateProjectEvidenceSnapshotRequest(
            descriptors=(
                _descriptor(
                    identifier="policy",
                    source_kind="directory_evidence",
                    role="supporting_reference",
                    path=supporting,
                    version="1",
                ),
            )
        )
    )
    original_fingerprint = snapshot.fingerprint_sha256

    with pytest.raises(TypeError):
        snapshot.sources[0].metadata["version"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        snapshot.sources[0].raw_evidence_references[0]["raw_sha256"] = "0" * 64  # type: ignore[index]
    decoded = snapshot.manifest()
    decoded["sources"][0]["metadata"]["version"] = "changed"

    assert snapshot.manifest()["sources"][0]["metadata"]["version"] == "1"
    assert snapshot.fingerprint_sha256 == original_fingerprint


@pytest.mark.parametrize(
    ("first_adapter", "second_adapter", "message"),
    (
        (
            _StaticAdapter(kind="first", item_id="shared-item"),
            _StaticAdapter(kind="second", item_id="shared-item"),
            "duplicate item ids across descriptors",
        ),
        (
            _StaticAdapter(kind="first", item_id="first-item", logical_identity="shared-logical"),
            _StaticAdapter(kind="second", item_id="second-item", logical_identity="shared-logical"),
            "duplicate logical identities across descriptors",
        ),
    ),
)
def test_global_capture_identity_collisions_are_rejected(
    tmp_path: Path,
    first_adapter: _StaticAdapter,
    second_adapter: _StaticAdapter,
    message: str,
) -> None:
    service = ProjectEvidenceSnapshotService(
        registry=SourceAdapterRegistry((first_adapter, second_adapter))
    )
    descriptors = (
        _descriptor(
            identifier="a",
            source_kind="first",
            role="supporting_reference",
            path=tmp_path / "a",
            version="1",
        ),
        _descriptor(
            identifier="b",
            source_kind="second",
            role="supporting_reference",
            path=tmp_path / "b",
            version="1",
        ),
    )

    with pytest.raises(ValueError, match=message):
        service.create(CreateProjectEvidenceSnapshotRequest(descriptors=descriptors))


def _two_source_static_snapshot(tmp_path: Path) -> ProjectEvidenceSnapshot:
    service = ProjectEvidenceSnapshotService(
        registry=SourceAdapterRegistry(
            (_StaticAdapter(kind="first"), _StaticAdapter(kind="second"))
        )
    )
    return service.create(
        CreateProjectEvidenceSnapshotRequest(
            descriptors=(
                _descriptor(
                    identifier="a",
                    source_kind="first",
                    role="supporting_reference",
                    path=tmp_path / "a",
                    version="1",
                ),
                _descriptor(
                    identifier="b",
                    source_kind="second",
                    role="supporting_reference",
                    path=tmp_path / "b",
                    version="1",
                ),
            )
        )
    )


@pytest.mark.parametrize(
    ("field", "message"),
    (
        ("item_id", "prepared item ids must be globally unique"),
        ("logical_identity", "prepared logical identities must be globally unique"),
    ),
)
def test_manifest_reconstruction_rejects_global_prepared_identity_collisions(
    tmp_path: Path,
    field: str,
    message: str,
) -> None:
    snapshot = _two_source_static_snapshot(tmp_path)
    manifest = snapshot.manifest()
    first = manifest["sources"][0]["prepared_representations"][0]
    second = manifest["sources"][1]["prepared_representations"][0]
    second[field] = first[field]

    with pytest.raises(ValueError, match=message):
        ProjectEvidenceSnapshot.from_manifest_json(
            manifest_json=_refingerprinted_manifest(manifest),
            prepared_content_items=snapshot.prepared_content_items,
        )


def test_capture_item_descriptor_ownership_is_verified(tmp_path: Path) -> None:
    descriptor = _descriptor(
        identifier="owner",
        source_kind="wrong_owner",
        role="supporting_reference",
        path=tmp_path / "source",
        version="1",
    )
    service = ProjectEvidenceSnapshotService(
        registry=SourceAdapterRegistry(
            (_StaticAdapter(kind="wrong_owner", owner="another-descriptor"),)
        )
    )

    snapshot = service.create(CreateProjectEvidenceSnapshotRequest(descriptors=(descriptor,)))

    assert snapshot.manifest()["sources"][0]["status"] == "unreadable"
    assert snapshot.prepared_contents() == ()


def test_directory_and_runtime_sources_require_verified_redaction_receipts(
    tmp_path: Path,
) -> None:
    supporting = tmp_path / "supporting"
    supporting.mkdir()
    (supporting / "policy.md").write_text("policy", encoding="utf-8")
    runtime = tmp_path / "runtime.json"
    runtime.write_text('{"id":"event-1"}', encoding="utf-8")
    invalid_receipt = tmp_path / "invalid-redaction.json"
    invalid_receipt.write_text('{"approved":false}', encoding="utf-8")
    directory_descriptor = SourceDescriptor(
        id="directory",
        source_kind="directory_evidence",
        source_role="supporting_reference",
        source_path=supporting,
        provenance_locator="fixture://directory",
        capabilities=frozenset({"searchable_text"}),
    )
    runtime_descriptor = SourceDescriptor(
        id="runtime",
        source_kind="runtime_export",
        source_role="runtime_observation",
        source_path=runtime,
        provenance_locator="fixture://runtime",
        capabilities=frozenset({"runtime_events"}),
        redaction_receipt_path=invalid_receipt,
    )

    snapshot = _service(supporting).create(
        CreateProjectEvidenceSnapshotRequest(descriptors=(directory_descriptor, runtime_descriptor))
    )
    entries = {entry["descriptor_id"]: entry for entry in snapshot.manifest()["sources"]}

    assert entries["directory"]["status_reason"] == "redaction_receipt_missing"
    assert entries["directory"]["provenance"]["redaction_status"] == "missing"
    assert entries["runtime"]["status_reason"] == "redaction_receipt_invalid"
    assert entries["runtime"]["provenance"]["redaction_status"] == "invalid"
    assert entries["runtime"]["provenance"]["redaction_receipt_sha256"]
    assert snapshot.prepared_contents() == ()


def test_receipt_validation_and_hash_use_one_read_and_bind_original_receipt_bytes(
    tmp_path: Path,
) -> None:
    class MutatingIntodocAdapter(IntodocScanSourceAdapter):
        def __init__(self) -> None:
            self.receipt_bytes_seen: bytes | None = None

        def snapshot_with_redaction_receipt(
            self,
            descriptor: SourceDescriptor,
            limits: SourceLimits,
            receipt_bytes: bytes,
        ) -> AdapterSnapshotResult:
            self.receipt_bytes_seen = receipt_bytes
            assert descriptor.redaction_receipt_path is not None
            descriptor.redaction_receipt_path.write_text('{"approved":false}', encoding="utf-8")
            return super().snapshot_with_redaction_receipt(descriptor, limits, receipt_bytes)

    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "scan_result.json").write_text("{}", encoding="utf-8")
    receipt = scan / "redaction.json"
    _write_bound_receipt(receipt, (scan / "scan_result.json",))
    approved = receipt.read_bytes()
    adapter = MutatingIntodocAdapter()
    descriptor = _descriptor(
        identifier="talend",
        source_kind="intodoc_scan",
        role="implementation",
        path=scan,
        version="1",
        redaction_receipt_path=receipt,
    )

    snapshot = ProjectEvidenceSnapshotService(registry=SourceAdapterRegistry((adapter,))).create(
        CreateProjectEvidenceSnapshotRequest(descriptors=(descriptor,))
    )
    entry = snapshot.manifest()["sources"][0]

    assert adapter.receipt_bytes_seen == approved
    assert entry["provenance"]["redaction_receipt_sha256"] == hashlib.sha256(approved).hexdigest()
    assert entry["status"] == "readable"


def test_unreadable_fingerprint_includes_bounded_raw_source_identity(tmp_path: Path) -> None:
    runtime = tmp_path / "bad.json"
    runtime.write_text("[1]", encoding="utf-8")
    supporting = tmp_path / "supporting"
    supporting.mkdir()
    descriptor = _descriptor(
        identifier="bad-runtime",
        source_kind="runtime_export",
        role="runtime_observation",
        path=runtime,
        version="1",
    )
    service = _service(supporting)

    first = service.create(CreateProjectEvidenceSnapshotRequest(descriptors=(descriptor,)))
    runtime.write_text("[2]", encoding="utf-8")
    second = service.create(CreateProjectEvidenceSnapshotRequest(descriptors=(descriptor,)))

    assert first.fingerprint_sha256 != second.fingerprint_sha256
    assert first.manifest()["sources"][0]["failure_identity"]["identity_status"] == "captured"


def test_unsupported_source_cannot_bypass_aggregate_budget(tmp_path: Path) -> None:
    unsupported = tmp_path / "unsupported.bin"
    unsupported.write_bytes(b"larger-than-budget")
    descriptor = _descriptor(
        identifier="unsupported",
        source_kind="unknown_binary",
        role="supporting_reference",
        path=unsupported,
        version="1",
    )

    with pytest.raises(ValueError, match="failure identity source budget exceeded"):
        ProjectEvidenceSnapshotService(registry=SourceAdapterRegistry()).create(
            CreateProjectEvidenceSnapshotRequest(
                descriptors=(descriptor,),
                limits=SourceLimits(max_files=1, max_bytes=4),
            )
        )


def test_durable_manifest_sanitizes_locator_and_metadata_secrets(tmp_path: Path) -> None:
    supporting = tmp_path / "supporting"
    supporting.mkdir()
    (supporting / "policy.md").write_text("policy", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    _write_bound_receipt(receipt, (supporting / "policy.md",))
    descriptor = SourceDescriptor(
        id="sanitized",
        source_kind="directory_evidence",
        source_role="supporting_reference",
        source_path=supporting,
        provenance_locator=(
            "https://user:locator-password@example.test/evidence?token=query-secret"
        ),
        capabilities=frozenset({"searchable_text"}),
        metadata={
            "api_key": "metadata-secret",
            "connection": "https://user:metadata-password@example.test?token=nested-secret",
            "note": "authorization=inline-secret",
            "version": "1",
        },
        redaction_receipt_path=receipt,
    )

    snapshot = _service(supporting).create(
        CreateProjectEvidenceSnapshotRequest(descriptors=(descriptor,))
    )
    durable = snapshot.manifest_json.decode("utf-8")

    for secret in (
        "locator-password",
        "query-secret",
        "metadata-secret",
        "metadata-password",
        "nested-secret",
        "inline-secret",
    ):
        assert secret not in durable
    assert snapshot.manifest()["sources"][0]["metadata"]["api_key"] == "[REDACTED]"


def test_project_evidence_snapshot_store_persists_content_before_linkage_manifest(
    tmp_path: Path,
) -> None:
    supporting = tmp_path / "supporting"
    supporting.mkdir()
    (supporting / "policy.md").write_text("policy", encoding="utf-8")
    snapshot = _service(supporting).create(
        CreateProjectEvidenceSnapshotRequest(
            descriptors=(
                _descriptor(
                    identifier="policy",
                    source_kind="directory_evidence",
                    role="supporting_reference",
                    path=supporting,
                    version="1",
                ),
            ),
            snapshot_id="project-evidence-persisted",
        )
    )
    store = ArtifactStore(
        output_root=tmp_path / "output",
        project_id="project-a",
        experiment_id="7f55cf4e-6228-47d6-b36d-74eb838753ee",
    )

    receipt = ProjectEvidenceSnapshotArtifactSink(store).save_snapshot(snapshot)

    assert receipt.snapshot_id == snapshot.id
    assert receipt.fingerprint_sha256 == snapshot.fingerprint_sha256
    assert receipt.manifest_sha256
    assert receipt.manifest_size_bytes > 0
    with pytest.raises(TypeError):
        receipt.content_relative_paths["new"] = "shared/new"  # type: ignore[index]
    manifest_path = store.experiment_root / receipt.manifest_relative_path
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted["snapshot"] == snapshot.manifest()
    assert set(receipt.content_relative_paths) == {
        item.item_id for item in snapshot.prepared_content_items
    }
    for item_id, relative_path in receipt.content_relative_paths.items():
        expected = dict(snapshot.prepared_contents())[item_id]
        assert (store.experiment_root / relative_path).read_bytes() == expected
    restored = ProjectEvidenceSnapshotArtifactSink(store).load_snapshot(receipt)
    assert restored.manifest_json == snapshot.manifest_json
    assert restored.prepared_contents() == snapshot.prepared_contents()


@pytest.mark.parametrize(
    ("field", "message"),
    (
        ("item_id", "prepared item ids must be globally unique"),
        ("logical_identity", "prepared logical identities must be globally unique"),
    ),
)
def test_persistence_loader_rejects_global_prepared_identity_collisions(
    tmp_path: Path,
    field: str,
    message: str,
) -> None:
    snapshot = _two_source_static_snapshot(tmp_path)
    store = ArtifactStore(
        output_root=tmp_path / "output",
        project_id="project-a",
        experiment_id="00f1f6ef-34a4-40f8-b256-ddfdd0701e85",
    )
    sink = ProjectEvidenceSnapshotArtifactSink(store)
    persistence = sink.save_snapshot(snapshot)
    manifest_path = store.experiment_root / persistence.manifest_relative_path
    envelope = json.loads(manifest_path.read_bytes())
    manifest = envelope["snapshot"]
    first = manifest["sources"][0]["prepared_representations"][0]
    second = manifest["sources"][1]["prepared_representations"][0]
    second[field] = first[field]
    _refingerprinted_manifest(manifest)
    encoded = canonical_json_bytes(envelope)
    manifest_path.write_bytes(encoded)
    tampered_persistence = replace(
        persistence,
        fingerprint_sha256=manifest["fingerprint_sha256"],
        manifest_sha256=hashlib.sha256(encoded).hexdigest(),
        manifest_size_bytes=len(encoded),
    )

    with pytest.raises(ValueError, match=message):
        sink.load_snapshot(tampered_persistence)


def test_generic_approval_and_stale_hash_bound_receipts_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("approved bytes", encoding="utf-8")
    runtime = tmp_path / "runtime.json"
    runtime.write_text('{"event_id":"one"}', encoding="utf-8")
    generic = tmp_path / "generic.json"
    generic.write_text('{"approved":true}', encoding="utf-8")
    runtime_generic = tmp_path / "runtime-generic.json"
    runtime_generic.write_text('{"approved":true}', encoding="utf-8")
    directory_descriptor = SourceDescriptor(
        id="generic-directory",
        source_kind="directory_evidence",
        source_role="supporting_reference",
        source_path=source,
        provenance_locator="fixture://generic",
        capabilities=frozenset({"searchable_text"}),
        redaction_receipt_path=generic,
    )
    runtime_descriptor = SourceDescriptor(
        id="generic-runtime",
        source_kind="runtime_export",
        source_role="runtime_observation",
        source_path=runtime,
        provenance_locator="fixture://runtime",
        capabilities=frozenset({"runtime_events"}),
        redaction_receipt_path=runtime_generic,
    )
    service = ProjectEvidenceSnapshotService(
        registry=SourceAdapterRegistry(
            (
                DirectoryEvidenceAdapter(allowed_roots=(tmp_path,)),
                RuntimeExportAdapter(),
            )
        )
    )

    generic_snapshot = service.create(
        CreateProjectEvidenceSnapshotRequest(descriptors=(directory_descriptor, runtime_descriptor))
    )
    assert {source["status_reason"] for source in generic_snapshot.manifest()["sources"]} == {
        "redaction_receipt_invalid"
    }

    _write_bound_receipt(generic, (source,))
    source.write_text("changed after approval", encoding="utf-8")
    stale_snapshot = service.create(
        CreateProjectEvidenceSnapshotRequest(descriptors=(directory_descriptor,))
    )
    assert stale_snapshot.manifest()["sources"][0]["status_reason"] == ("adapter_capture_failed")


def test_raw_file_and_prepared_record_budgets_are_accounted_separately(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "events.jsonl"
    runtime.write_text('{"event_id":"one"}\n{"event_id":"two"}\n', encoding="utf-8")
    descriptor = _descriptor(
        identifier="events",
        source_kind="runtime_export",
        role="runtime_observation",
        path=runtime,
        version="1",
    )
    service = ProjectEvidenceSnapshotService(
        registry=SourceAdapterRegistry((RuntimeExportAdapter(),))
    )

    snapshot = service.create(
        CreateProjectEvidenceSnapshotRequest(
            descriptors=(descriptor,),
            limits=SourceLimits(max_files=1, max_bytes=len(runtime.read_bytes())),
            prepared_limits=PreparedEvidenceLimits(max_records=2, max_bytes=1_000),
        )
    )
    assert snapshot.manifest()["counts"]["raw_files"] == 1
    assert snapshot.manifest()["counts"]["prepared_records"] == 2

    with pytest.raises(ValueError, match="prepared record limit"):
        service.create(
            CreateProjectEvidenceSnapshotRequest(
                descriptors=(descriptor,),
                limits=SourceLimits(max_files=1, max_bytes=len(runtime.read_bytes())),
                prepared_limits=PreparedEvidenceLimits(max_records=1, max_bytes=1_000),
            )
        )
    with pytest.raises(ValueError, match="prepared byte limit"):
        service.create(
            CreateProjectEvidenceSnapshotRequest(
                descriptors=(descriptor,),
                limits=SourceLimits(max_files=1, max_bytes=len(runtime.read_bytes())),
                prepared_limits=PreparedEvidenceLimits(max_records=2, max_bytes=1),
            )
        )


def test_mutable_prepared_bytes_are_rejected_before_admission() -> None:
    content = bytearray(b"mutable")
    digest = hashlib.sha256(content).hexdigest()
    with pytest.raises(ValueError, match="immutable bytes"):
        SourceSnapshotContent(
            item_id="item",
            extracted_bytes=content,  # type: ignore[arg-type]
            extracted_sha256=digest,
            media_type="text/plain",
            size_bytes=len(content),
        )
    with pytest.raises(ValueError, match="immutable bytes"):
        ProjectEvidencePreparedContent(
            item_id="item",
            extracted_bytes=content,  # type: ignore[arg-type]
            extracted_sha256=digest,
            media_type="text/plain",
            size_bytes=len(content),
        )


@pytest.mark.parametrize(
    "adapter",
    (
        _StaticAdapter(kind="owned", source_kind="other"),
        _StaticAdapter(kind="owned", source_role="other"),
        _StaticAdapter(kind="owned", capabilities=frozenset({"other"})),
        _StaticAdapter(kind="owned", trust_level="untrusted"),
    ),
)
def test_all_item_ownership_fields_must_match_descriptor(
    tmp_path: Path, adapter: _StaticAdapter
) -> None:
    descriptor = _descriptor(
        identifier="owned",
        source_kind="owned",
        role="supporting_reference",
        path=tmp_path / "virtual",
        version="1",
    )
    snapshot = ProjectEvidenceSnapshotService(registry=SourceAdapterRegistry((adapter,))).create(
        CreateProjectEvidenceSnapshotRequest(descriptors=(descriptor,))
    )
    assert snapshot.manifest()["sources"][0]["status_reason"] == "adapter_capture_failed"


def test_snapshot_constructor_rejects_noncanonical_unknown_and_inconsistent_state(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("source", encoding="utf-8")
    snapshot = _service(tmp_path).create(
        CreateProjectEvidenceSnapshotRequest(
            descriptors=(
                _descriptor(
                    identifier="source",
                    source_kind="directory_evidence",
                    role="supporting_reference",
                    path=source,
                    version="1",
                ),
            )
        )
    )
    noncanonical = json.dumps(snapshot.manifest(), indent=2).encode("utf-8")
    with pytest.raises(ValueError, match="canonical JSON"):
        replace(snapshot, manifest_json=noncanonical)

    unknown = snapshot.manifest()
    unknown["unfingerprinted"] = True
    with pytest.raises(ValueError, match="unknown or missing fields"):
        ProjectEvidenceSnapshot.from_manifest_json(
            manifest_json=canonical_json_bytes(unknown),
            prepared_content_items=snapshot.prepared_content_items,
        )

    inconsistent = snapshot.manifest()
    inconsistent["sources"][0]["status_reason"] = "should-not-exist"
    fingerprint_payload = dict(inconsistent)
    fingerprint_payload.pop("fingerprint_sha256")
    inconsistent["fingerprint_sha256"] = hashlib.sha256(
        canonical_json_bytes(fingerprint_payload)
    ).hexdigest()
    with pytest.raises(ValueError, match="inconsistent state"):
        ProjectEvidenceSnapshot.from_manifest_json(
            manifest_json=canonical_json_bytes(inconsistent),
            prepared_content_items=snapshot.prepared_content_items,
        )


def test_sanitization_redacts_entire_headers_and_hashes_all_url_components(
    tmp_path: Path,
) -> None:
    source = tmp_path / "filename-secret.md"
    source.write_text("source", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    _write_bound_receipt(receipt, (source,))
    descriptor = SourceDescriptor(
        id="secrets",
        source_kind="directory_evidence",
        source_role="supporting_reference",
        source_path=source,
        provenance_locator="https://example.test/path-secret?safe=query-secret#fragment-secret",
        capabilities=frozenset({"searchable_text"}),
        metadata={
            "headers": {"X-Custom": "header-secret", "Authorization": "Bearer auth-secret"},
            "note": "Bearer inline-secret",
            "reference": "https://example.test/metadata-secret?x=value-secret",
            "version": "1",
        },
        redaction_receipt_path=receipt,
    )
    snapshot = _service(tmp_path).create(
        CreateProjectEvidenceSnapshotRequest(descriptors=(descriptor,))
    )
    encoded = snapshot.manifest_json.decode("utf-8")
    for secret in (
        "path-secret",
        "filename-secret",
        "example.test",
        "query-secret",
        "fragment-secret",
        "header-secret",
        "auth-secret",
        "inline-secret",
        "metadata-secret",
        "value-secret",
    ):
        assert secret not in encoded
    metadata = snapshot.manifest()["sources"][0]["metadata"]
    assert metadata["headers"] == "[REDACTED]"
    assert metadata["note"] == "[REDACTED]"


@pytest.mark.parametrize(
    ("locator", "expected_port"),
    (
        ("https://example.test:8443/evidence", ":8443"),
        ("https://example.test:not-a-port/evidence", ""),
    ),
)
def test_locator_sanitization_never_restores_raw_hostname_for_ports(
    tmp_path: Path,
    locator: str,
    expected_port: str,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("source", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    _write_bound_receipt(receipt, (source,))
    descriptor = SourceDescriptor(
        id="port-sanitization",
        source_kind="directory_evidence",
        source_role="supporting_reference",
        source_path=source,
        provenance_locator=locator,
        capabilities=frozenset({"searchable_text"}),
        metadata={"version": "1"},
        redaction_receipt_path=receipt,
    )

    snapshot = _service(tmp_path).create(
        CreateProjectEvidenceSnapshotRequest(descriptors=(descriptor,))
    )
    sanitized = snapshot.manifest()["sources"][0]["provenance"]["locator"]
    hashed_hostname = hashlib.sha256(b"example.test").hexdigest()

    assert sanitized.startswith(f"https://sha256-{hashed_hostname}{expected_port}/sha256/")
    assert "example.test" not in sanitized


def test_persistence_loader_rejects_tampered_content(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("source", encoding="utf-8")
    snapshot = _service(tmp_path).create(
        CreateProjectEvidenceSnapshotRequest(
            descriptors=(
                _descriptor(
                    identifier="source",
                    source_kind="directory_evidence",
                    role="supporting_reference",
                    path=source,
                    version="1",
                ),
            )
        )
    )
    store = ArtifactStore(
        output_root=tmp_path / "output",
        project_id="project-a",
        experiment_id="1e7573a8-199d-4d85-91a2-bf555bc0f1cd",
    )
    sink = ProjectEvidenceSnapshotArtifactSink(store)
    persistence = sink.save_snapshot(snapshot)
    content_path = store.experiment_root / next(iter(persistence.content_relative_paths.values()))
    content_path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="failed verification"):
        sink.load_snapshot(persistence)
