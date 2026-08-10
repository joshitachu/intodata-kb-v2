"""Create the immutable receipt required to freeze scanner evidence.

IntoDoc's ``redaction_report.json`` proves that the scanner completed its
redaction pass, but it is deliberately an audit summary rather than a content
manifest.  Grounded runs also need a closed list of the *exact* redacted scan
files they freeze.  This module derives that companion receipt from an
already-approved scanner output; it never reads the uploaded source project.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from mcp_server.experiments.domain.sources import SourceLimits
from mcp_server.experiments.infrastructure.sources.intodoc_scan import (
    redaction_receipt_approved,
)
from mcp_server.experiments.infrastructure.sources.registry import (
    SourceAdapterError,
    read_regular,
    regular_files,
)
from mcp_server.experiments.infrastructure.storage.canonical_json import (
    canonical_json_bytes,
)


_COMPANION_RECEIPT = "grounded_evidence_receipt.json"
_SCANNER_REPORT = "redaction_report.json"


def ensure_grounded_scanner_receipt(
    output_dir: Path,
    *,
    limits: SourceLimits | None = None,
) -> Path:
    """Return a strict receipt for the current redacted scanner output.

    Existing test/legacy scans may already have a strict receipt at
    ``redaction_report.json``; preserve and use it.  A normal IntoDoc report is
    accepted only as proof that redaction completed, then a separate immutable
    receipt is generated alongside it.  Rebuilding the companion on every
    request intentionally detects changed scanner output before it is handed to
    an agent.
    """

    output_dir = Path(output_dir)
    report_path = output_dir / _SCANNER_REPORT
    try:
        report_bytes = read_regular(report_path, max_bytes=1024 * 1024)
    except SourceAdapterError as exc:
        raise ValueError("approved IntoDoc redaction report is unavailable") from exc

    if _is_strict_bound_receipt(report_bytes):
        return report_path
    if not redaction_receipt_approved(report_bytes):
        raise ValueError("IntoDoc redaction report is not approved")

    receipt_path = output_dir / _COMPANION_RECEIPT
    active_limits = limits or SourceLimits()
    entries: list[dict[str, object]] = []
    try:
        for path in regular_files(output_dir, limits=active_limits):
            if path == receipt_path:
                continue
            content = read_regular(path, max_bytes=path.stat().st_size)
            entries.append(
                {
                    "raw_sha256": hashlib.sha256(content).hexdigest(),
                    "size_bytes": len(content),
                }
            )
    except SourceAdapterError as exc:
        raise ValueError("approved IntoDoc scan output could not be frozen") from exc

    if not entries:
        raise ValueError("approved IntoDoc scan output contains no evidence files")
    entries.sort(key=lambda value: (str(value["raw_sha256"]), int(value["size_bytes"])))
    payload = canonical_json_bytes(
        {"schema_version": 1, "source_files": entries, "status": "approved"}
    )
    temporary = receipt_path.with_name(f".{receipt_path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, receipt_path)
    finally:
        temporary.unlink(missing_ok=True)
    return receipt_path


def _is_strict_bound_receipt(content: bytes) -> bool:
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(value, dict)
        and value.get("schema_version") == 1
        and value.get("status") == "approved"
        and isinstance(value.get("source_files"), list)
    )
