from __future__ import annotations

import hashlib
import io
import logging
from pathlib import Path
import zipfile

import pytest

from mcp_server.experiments.application.prepare_supporting_evidence import (
    PdfProbe,
    SupportingEvidenceLimits,
    SupportingEvidencePreparationService,
    XlsProbe,
    classify_unsupported,
)
from mcp_server.experiments.application.snapshot_project_evidence import (
    CreateProjectEvidenceSnapshotRequest,
    ProjectEvidenceSnapshotService,
)
from mcp_server.experiments.domain.sources import SourceDescriptor
from mcp_server.experiments.infrastructure.sources.prepared_directory import (
    PreparedDirectoryEvidenceAdapter,
)
from mcp_server.experiments.infrastructure.sources.registry import (
    SourceAdapterRegistry,
    SourceAdapterError,
)


def _descriptor(root: Path, **metadata: object) -> SourceDescriptor:
    return SourceDescriptor(
        id="managed-project-workspace",
        source_kind="directory_evidence",
        source_role="supporting_reference",
        source_path=root,
        provenance_locator="project-workspace://werkorder",
        capabilities=frozenset({"searchable_text", "structured_records"}),
        trust_level="managed_workspace_unredacted",
        metadata=metadata,
    )


def test_preparation_redacts_text_inventories_unknowns_and_freezes_snapshot(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = "hunter2-never-expose"
    (workspace / "scope.txt").write_text(
        f"Work order interface\npassword={secret}\nAuthorization: Bearer secret-token-value\n",
        encoding="utf-8",
    )
    (workspace / "opaque.bin").write_bytes(b"\x00\x01\x02")
    (workspace / "scanner-owned" ).mkdir()
    (workspace / "scanner-owned" / "source.item").write_text("scanner only", encoding="utf-8")

    prepared = SupportingEvidencePreparationService().prepare(
        _descriptor(workspace, exclude_relative_prefixes=["scanner-owned"]),
        output_root=tmp_path / "prepared",
    )

    assert prepared.prepared_count == 1
    assert prepared.skipped_count == 2
    statuses = {str(item["status"]) for item in prepared.inventory}
    assert statuses == {"prepared", "unsupported_by_probes", "excluded_by_owner"}
    content = next((prepared.descriptor.source_path / "content").glob("*.txt")).read_text()
    assert secret not in content
    assert "secret-token-value" not in content
    assert "[REDACTED]" in content
    receipt = prepared.descriptor.redaction_receipt_path
    assert receipt is not None
    assert hashlib.sha256((workspace / "scope.txt").read_bytes()).hexdigest() in receipt.read_text()

    snapshot = ProjectEvidenceSnapshotService(
        registry=SourceAdapterRegistry(
            (PreparedDirectoryEvidenceAdapter(allowed_roots=(tmp_path / "prepared",)),)
        )
    ).create(CreateProjectEvidenceSnapshotRequest(descriptors=(prepared.descriptor,)))
    assert len(snapshot.prepared_content_items) == 1
    assert secret.encode() not in snapshot.prepared_content_items[0].extracted_bytes
    assert b"[REDACTED]" in snapshot.prepared_content_items[0].extracted_bytes


def test_preparation_is_idempotent_and_never_treats_oversize_as_text(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "large.txt").write_text("x" * 1025, encoding="utf-8")
    (workspace / "small.txt").write_text("safe evidence", encoding="utf-8")
    service = SupportingEvidencePreparationService()
    limits = SupportingEvidenceLimits(max_file_bytes=1024)

    first = service.prepare(_descriptor(workspace), output_root=tmp_path / "prepared", limits=limits)
    second = service.prepare(_descriptor(workspace), output_root=tmp_path / "prepared", limits=limits)

    assert first.preparation_fingerprint_sha256 == second.preparation_fingerprint_sha256
    assert first.descriptor.source_path == second.descriptor.source_path
    assert {entry["status"] for entry in first.inventory} == {"prepared", "skipped_file_limit"}


def test_preparation_extracts_safe_office_open_xml_without_business_inference(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workbook = workspace / "mapping.xlsx"
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "xl/sharedStrings.xml",
            "<sst><si><t>Work Order</t></si><si><t>password=never-expose</t></si></sst>",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            "<worksheet><sheetData><row><c><v>Location</v></c></row></sheetData></worksheet>",
        )
    workbook.write_bytes(package.getvalue())

    prepared = SupportingEvidencePreparationService().prepare(
        _descriptor(workspace), output_root=tmp_path / "prepared"
    )

    assert prepared.prepared_count == 1
    content = next((prepared.descriptor.source_path / "content").glob("*.txt")).read_text()
    assert "Work Order" in content
    assert "Location" in content
    assert "never-expose" not in content
    assert "password=[REDACTED]" in content


def test_preparation_fails_closed_when_nothing_is_safely_extractable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "opaque.bin").write_bytes(b"\x00binary")

    with pytest.raises(SourceAdapterError, match="no safely preparable evidence"):
        SupportingEvidencePreparationService().prepare(
            _descriptor(workspace), output_root=tmp_path / "prepared"
        )


# --------------------------------------------------------------------------
# Slice 5 -- PDF / legacy .xls probes, OCR/legacy classification, loud caps.
# --------------------------------------------------------------------------


def _minimal_pdf(body_text: str) -> bytes:
    """A tiny, well-formed single-page PDF carrying one line of visible text."""

    content = f"BT /F1 12 Tf 10 100 Td ({body_text}) Tj ET".encode("latin-1")
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>stream\n%s\nendstream" % (len(content), content),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj" % index + obj + b"endobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer<</Root 1 0 R/Size %d>>\n" % (len(objects) + 1)
    out += b"startxref\n%d\n%%%%EOF" % xref_pos
    return bytes(out)


def _xls_workbook(rows: list[list[str]]) -> bytes:
    xlwt = pytest.importorskip("xlwt")
    book = xlwt.Workbook()
    sheet = book.add_sheet("Mapping")
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            sheet.write(row_index, col_index, value)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_pdf_probe_extracts_text_and_redacts_embedded_credential(tmp_path: Path) -> None:
    raw = _minimal_pdf("Orders mapping password=hunter2-secret")
    # The probe itself recovers the text verbatim (redaction happens after).
    extracted = PdfProbe().extract(raw, max_bytes=64 * 1024)
    assert extracted is not None
    assert b"Orders mapping" in extracted

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "spec.pdf").write_bytes(raw)
    prepared = SupportingEvidencePreparationService().prepare(
        _descriptor(workspace), output_root=tmp_path / "prepared"
    )
    assert prepared.prepared_count == 1
    item = next(entry for entry in prepared.inventory if entry.get("status") == "prepared")
    assert item["probe"] == {"id": "pdf-text", "version": "1"}
    content = next((prepared.descriptor.source_path / "content").glob("*.txt")).read_text()
    assert "Orders mapping" in content
    # Credential redacted before the text can leave the probe path.
    assert "hunter2-secret" not in content
    assert "[REDACTED]" in content


def test_xls_probe_extracts_legacy_workbook_and_redacts_credential(tmp_path: Path) -> None:
    raw = _xls_workbook(
        [
            ["Orders", "password=hunter2-secret"],
            ["SystemA endpoint", "https://svc.example/orders"],
        ]
    )
    assert raw.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")  # OLE2 signature
    extracted = XlsProbe().extract(raw, max_bytes=64 * 1024)
    assert extracted is not None
    assert b"Orders" in extracted

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "mapping.xls").write_bytes(raw)
    prepared = SupportingEvidencePreparationService().prepare(
        _descriptor(workspace), output_root=tmp_path / "prepared"
    )
    assert prepared.prepared_count == 1
    item = next(entry for entry in prepared.inventory if entry.get("status") == "prepared")
    assert item["probe"] == {"id": "legacy-xls", "version": "1"}
    content = next((prepared.descriptor.source_path / "content").glob("*.txt")).read_text()
    assert "Orders" in content
    assert "hunter2-secret" not in content
    assert "[REDACTED]" in content


def test_ole2_non_workbook_is_logged_unsupported_not_silently_dropped(tmp_path: Path) -> None:
    # A legacy OLE2 file that is not a readable .xls workbook (e.g. a .doc) must
    # be recorded with a distinct, explicit status -- never a silent drop.
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.doc").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    (workspace / "safe.txt").write_text("safe evidence", encoding="utf-8")
    prepared = SupportingEvidencePreparationService().prepare(
        _descriptor(workspace), output_root=tmp_path / "prepared"
    )
    statuses = {str(item["status"]) for item in prepared.inventory}
    assert "unsupported_needs_legacy_office_reader" in statuses


def test_image_is_classified_needs_ocr_not_silently_dropped(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "scan.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    (workspace / "safe.txt").write_text("safe evidence", encoding="utf-8")
    prepared = SupportingEvidencePreparationService().prepare(
        _descriptor(workspace), output_root=tmp_path / "prepared"
    )
    statuses = {str(item["status"]) for item in prepared.inventory}
    assert "unsupported_needs_ocr" in statuses
    # Direct classifier coverage for a scanned (image-only) PDF.
    assert classify_unsupported(b"%PDF-1.4 image only") == "unsupported_needs_ocr"


def test_size_cap_skip_is_loud_with_filename_and_size(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "huge_mapping.txt").write_text("x" * 5000, encoding="utf-8")
    (workspace / "small.txt").write_text("safe evidence", encoding="utf-8")
    limits = SupportingEvidenceLimits(max_file_bytes=1024)

    with caplog.at_level(
        logging.WARNING, logger="mcp_server.experiments.prepare_supporting_evidence"
    ):
        prepared = SupportingEvidencePreparationService().prepare(
            _descriptor(workspace), output_root=tmp_path / "prepared", limits=limits
        )

    # Not a silent drop: the skip is recorded with its own path, size, and cap.
    assert prepared.prepared_count == 1
    skipped = {entry["relative_path"]: entry for entry in prepared.skipped_files}
    assert "huge_mapping.txt" in skipped
    assert skipped["huge_mapping.txt"]["status"] == "skipped_file_limit"
    assert skipped["huge_mapping.txt"]["size_bytes"] == 5000
    assert skipped["huge_mapping.txt"]["cap_bytes"] == 1024
    # And it is emitted to the operator log with the same detail.
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "skipped_file_limit" in message
        and "huge_mapping.txt" in message
        and "5000" in message
        for message in messages
    )


def test_supporting_evidence_max_file_bytes_is_configurable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "mid.txt").write_text("y" * 3000, encoding="utf-8")
    (workspace / "small.txt").write_text("safe evidence", encoding="utf-8")

    tight = SupportingEvidencePreparationService().prepare(
        _descriptor(workspace),
        output_root=tmp_path / "tight",
        limits=SupportingEvidenceLimits(max_file_bytes=1024),
    )
    # The tight cap skips mid.txt; the small file still prepares.
    assert tight.prepared_count == 1
    assert {e["relative_path"] for e in tight.skipped_files} == {"mid.txt"}

    loose = SupportingEvidencePreparationService().prepare(
        _descriptor(workspace),
        output_root=tmp_path / "loose",
        limits=SupportingEvidenceLimits(max_file_bytes=1_000_000),
    )
    assert loose.prepared_count == 2
    assert loose.skipped_files == ()
