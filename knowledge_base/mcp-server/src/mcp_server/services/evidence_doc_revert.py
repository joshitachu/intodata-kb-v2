"""Safe rollback of evidence-backed documentation updates."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp_server.config import config
from mcp_server.services.doc_version_history import record_push_outcome
from mcp_server.services.evidence_doc_update import (
    EvidenceDocUpdateError,
    _atomic_write,
    _normalize_sha256,
    _receipt_dir,
    _relative_to_project_output,
    _sha256_hex,
    _timestamp_key,
    _validate_markdown_filename,
    _validate_project_id,
)
from mcp_server.services.git_diff_patcher import OUTCOME_BOUNDED_PATCH
from mcp_server.services.log_manager import LogManager
from mcp_server.services.rag_bridge import RagBridge


@dataclass(frozen=True)
class EvidenceDocRevertResult:
    project_id: str
    filename: str
    status: str
    message: str
    workflow_run_id: str | None
    before_sha: str
    after_sha: str
    version_id: str
    reverted_receipt_path: str
    receipt_path: str
    docs_path: str
    rag_result: dict[str, Any]
    reconciliation_status: str

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class EvidenceDocRevertService:
    """Restore the backup named by an update receipt, then reindex the document."""

    def __init__(self, *, log_manager: LogManager | None = None) -> None:
        self._log_manager = log_manager

    async def revert_update(
        self,
        *,
        project_id: str,
        filename: str,
        receipt_path: str,
        expected_base_sha: str | None = None,
        workflow_run_id: str | None = None,
    ) -> EvidenceDocRevertResult:
        _validate_project_id(project_id)
        safe_filename = _validate_markdown_filename(filename)
        update_receipt_path = _resolve_update_receipt(project_id, receipt_path)
        update_receipt = _read_update_receipt(update_receipt_path)
        if (
            update_receipt.get("project_id") != project_id
            or update_receipt.get("filename") != safe_filename
        ):
            raise EvidenceDocUpdateError(400, "Update receipt does not match this document.")
        if update_receipt.get("status") != "updated":
            raise EvidenceDocUpdateError(409, "Only a successfully indexed update can be reverted.")

        docs_dir = config.project_root / "output" / project_id / "docs"
        doc_path = docs_dir / safe_filename
        if not doc_path.is_file():
            raise EvidenceDocUpdateError(404, f"Documentation file not found: {safe_filename}")

        current = doc_path.read_text(encoding="utf-8", errors="replace")
        current_sha = _sha256_hex(current)
        update_after_sha = _required_receipt_sha(update_receipt, "after_sha")
        expected_sha = _normalize_sha256(expected_base_sha)
        if expected_sha is not None and expected_sha != current_sha:
            raise EvidenceDocUpdateError(
                409,
                f"Documentation changed before revert; expected sha256:{expected_sha}, "
                f"found sha256:{current_sha}.",
            )
        if current_sha != update_after_sha:
            raise EvidenceDocUpdateError(
                409,
                "Documentation no longer matches the update receipt; refusing to overwrite a later version.",
            )

        backup_path = _resolve_backup(project_id, update_receipt.get("backup_path"))
        prior = backup_path.read_text(encoding="utf-8", errors="replace")
        prior_sha = _sha256_hex(prior)
        if prior_sha != _required_receipt_sha(update_receipt, "before_sha"):
            raise EvidenceDocUpdateError(409, "Update backup failed integrity verification.")

        now = datetime.now(timezone.utc)
        _atomic_write(doc_path, prior)
        try:
            rag_result = await RagBridge(log_manager=self._log_manager).push_docs(
                project_id=project_id,
                output_dir=str(docs_dir),
                filenames={safe_filename},
                force=True,
            )
        except Exception as exc:
            _atomic_write(doc_path, current)
            failure_receipt = _write_revert_receipt(
                project_id=project_id,
                filename=safe_filename,
                workflow_run_id=workflow_run_id,
                reverted_receipt_path=update_receipt_path,
                before_sha=current_sha,
                after_sha=prior_sha,
                rag_result={"pushed": 0, "errors": [str(exc)]},
                timestamp=now,
                status="rolled_back",
                reconciliation_status="restored_updated_version_after_index_failure",
            )
            raise EvidenceDocUpdateError(
                502,
                "RAG sync failed; revert was rolled back. "
                f"Receipt: {_relative_to_project_output(project_id, failure_receipt)}",
            ) from exc

        if not RagBridge.push_succeeded(rag_result, expected_count=1):
            _atomic_write(doc_path, current)
            failure_receipt = _write_revert_receipt(
                project_id=project_id,
                filename=safe_filename,
                workflow_run_id=workflow_run_id,
                reverted_receipt_path=update_receipt_path,
                before_sha=current_sha,
                after_sha=prior_sha,
                rag_result=rag_result,
                timestamp=now,
                status="rolled_back",
                reconciliation_status="restored_updated_version_after_index_failure",
            )
            raise EvidenceDocUpdateError(
                502,
                "RAG sync failed; revert was rolled back. "
                f"Receipt: {_relative_to_project_output(project_id, failure_receipt)}",
            )

        section = update_receipt.get("section") or "Evidence Update"
        record_push_outcome(
            project_id,
            {
                "before": f"sha256:{current_sha}",
                "after": f"sha256:{prior_sha}",
                "outcome": OUTCOME_BOUNDED_PATCH,
                "touched_docs": [safe_filename],
                "reason_code": "harness_evidence_revert",
                "doc_sections": {safe_filename: [str(section)]},
                "rag_result": rag_result,
            },
        )
        revert_receipt_path = _write_revert_receipt(
            project_id=project_id,
            filename=safe_filename,
            workflow_run_id=workflow_run_id,
            reverted_receipt_path=update_receipt_path,
            before_sha=current_sha,
            after_sha=prior_sha,
            rag_result=rag_result,
            timestamp=now,
            status="reverted",
            reconciliation_status="indexed",
        )
        return EvidenceDocRevertResult(
            project_id=project_id,
            filename=safe_filename,
            status="reverted",
            message=f"Reverted {safe_filename} to its prior version.",
            workflow_run_id=workflow_run_id,
            before_sha=f"sha256:{current_sha}",
            after_sha=f"sha256:{prior_sha}",
            version_id=f"sha256:{prior_sha}",
            reverted_receipt_path=_relative_to_project_output(project_id, update_receipt_path),
            receipt_path=_relative_to_project_output(project_id, revert_receipt_path),
            docs_path=f"/projects/{project_id}/docs/{safe_filename}",
            rag_result=rag_result,
            reconciliation_status="indexed",
        )


def _resolve_update_receipt(project_id: str, receipt_path: str) -> Path:
    candidate = Path(receipt_path.strip())
    if not candidate.name or candidate.name != str(candidate) and candidate.parent.name != "_harness_doc_updates":
        raise EvidenceDocUpdateError(400, "Invalid update receipt path.")
    resolved = _receipt_dir(project_id) / candidate.name
    if not resolved.is_file() or not resolved.name.endswith(".receipt.json"):
        raise EvidenceDocUpdateError(404, "Update receipt not found.")
    return resolved


def _read_update_receipt(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceDocUpdateError(400, "Update receipt is invalid.") from exc
    if not isinstance(payload, dict) or payload.get("receipt_type") != "evidence_doc_update":
        raise EvidenceDocUpdateError(400, "Update receipt is invalid.")
    return payload


def _resolve_backup(project_id: str, value: object) -> Path:
    candidate = Path(str(value or ""))
    path = _receipt_dir(project_id) / candidate.name
    if not candidate.name or not path.is_file() or not path.name.endswith(".bak.md"):
        raise EvidenceDocUpdateError(409, "Update backup is unavailable.")
    return path


def _required_receipt_sha(receipt: dict[str, Any], field: str) -> str:
    try:
        value = _normalize_sha256(str(receipt[field]))
    except (KeyError, EvidenceDocUpdateError) as exc:
        raise EvidenceDocUpdateError(400, "Update receipt is invalid.") from exc
    assert value is not None
    return value


def _write_revert_receipt(
    *,
    project_id: str,
    filename: str,
    workflow_run_id: str | None,
    reverted_receipt_path: Path,
    before_sha: str,
    after_sha: str,
    rag_result: dict[str, Any],
    timestamp: datetime,
    status: str,
    reconciliation_status: str,
) -> Path:
    receipt_id = uuid.uuid4().hex
    path = _receipt_dir(project_id) / (
        f"{_timestamp_key(timestamp)}_{receipt_id}_{filename}.revert.receipt.json"
    )
    payload = {
        "receipt_id": receipt_id,
        "receipt_type": "evidence_doc_revert",
        "project_id": project_id,
        "filename": filename,
        "workflow_run_id": workflow_run_id,
        "reverted_receipt_path": _relative_to_project_output(project_id, reverted_receipt_path),
        "before_sha": f"sha256:{before_sha}",
        "after_sha": f"sha256:{after_sha}",
        "version_id": f"sha256:{after_sha}",
        "rag_result": rag_result,
        "status": status,
        "reconciliation_status": reconciliation_status,
        "reverted_at": timestamp.isoformat(),
        "reason_code": "harness_evidence_revert",
    }
    path.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2), encoding="ascii")
    return path
