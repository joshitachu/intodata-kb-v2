"""Guarded documentation updates from explicit harness evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp_server.config import config
from mcp_server.services.doc_version_history import record_push_outcome
from mcp_server.services.git_diff_patcher import OUTCOME_BOUNDED_PATCH
from mcp_server.services.log_manager import LogManager
from mcp_server.services.rag_bridge import RagBridge

_MAX_EVIDENCE_CHARS = 12_000
_MAX_RENDERED_EVIDENCE_CHARS = 6_000
_MAX_REF_COUNT = 25
_MAX_REF_CHARS = 160
_PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bAuthorization\s*[:=]\s*Bearer\s+[A-Za-z0-9._+/=\-]+"),
    re.compile(r"(?i)\b(access_token|refresh_token|api[_-]?key|token|secret)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._+/=\-]{10,}"),
)


class EvidenceDocUpdateError(Exception):
    """Safe service error suitable for HTTP response details."""

    def __init__(self, status_code: int, safe_message: str) -> None:
        super().__init__(safe_message)
        self.status_code = status_code
        self.safe_message = safe_message


@dataclass(frozen=True)
class EvidenceDocUpdateResult:
    project_id: str
    filename: str
    status: str
    message: str
    workflow_run_id: str | None
    evidence_refs: list[str]
    before_sha: str
    after_sha: str
    receipt_path: str
    docs_path: str
    rag_result: dict[str, Any]
    version_id: str
    section: str | None
    revert_path: str
    reconciliation_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "filename": self.filename,
            "status": self.status,
            "message": self.message,
            "workflow_run_id": self.workflow_run_id,
            "evidence_refs": self.evidence_refs,
            "before_sha": self.before_sha,
            "after_sha": self.after_sha,
            "receipt_path": self.receipt_path,
            "docs_path": self.docs_path,
            "rag_result": self.rag_result,
            "version_id": self.version_id,
            "section": self.section,
            "revert_path": self.revert_path,
            "reconciliation_status": self.reconciliation_status,
        }


class EvidenceDocUpdateService:
    """Append a bounded evidence-backed update to one published markdown doc."""

    def __init__(self, *, log_manager: LogManager | None = None) -> None:
        self._log_manager = log_manager

    async def update_from_evidence(
        self,
        *,
        project_id: str,
        filename: str,
        evidence_text: str,
        evidence_refs: list[str] | None = None,
        workflow_run_id: str | None = None,
        section: str | None = None,
        expected_base_sha: str | None = None,
    ) -> EvidenceDocUpdateResult:
        _validate_project_id(project_id)
        safe_filename = _validate_markdown_filename(filename)
        docs_dir = config.project_root / "output" / project_id / "docs"
        doc_path = docs_dir / safe_filename
        if not doc_path.is_file():
            raise EvidenceDocUpdateError(404, f"Documentation file not found: {safe_filename}")

        evidence = _sanitize_evidence_text(evidence_text)
        if not evidence:
            raise EvidenceDocUpdateError(400, "Evidence text is required before updating documentation.")

        refs = _sanitize_evidence_refs(evidence_refs)
        now = datetime.now(timezone.utc)
        original = doc_path.read_text(encoding="utf-8", errors="replace")
        before_sha = _sha256_hex(original)
        expected_sha = _normalize_sha256(expected_base_sha)
        if expected_sha is not None and expected_sha != before_sha:
            raise EvidenceDocUpdateError(
                409,
                f"Documentation changed since the proposal was created; expected sha256:{expected_sha}, "
                f"found sha256:{before_sha}.",
            )
        normalized_section = _normalize_section_name(section)
        backup_path = _backup_original_doc(
            project_id=project_id,
            filename=safe_filename,
            content=original,
            timestamp=now,
        )
        if normalized_section is None:
            updated = _append_evidence_section(
                original,
                timestamp=now,
                workflow_run_id=workflow_run_id,
                evidence_refs=refs,
                evidence_text=evidence,
            )
            changed_section = "Evidence Update"
        else:
            updated = _replace_markdown_section(original, normalized_section, evidence)
            changed_section = normalized_section
        after_sha = _sha256_hex(updated)
        if after_sha == before_sha:
            raise EvidenceDocUpdateError(400, "Evidence update did not change the documentation file.")

        _atomic_write(doc_path, updated)

        rag_result: dict[str, Any]
        try:
            rag_result = await RagBridge(log_manager=self._log_manager).push_docs(
                project_id=project_id,
                output_dir=str(docs_dir),
                filenames={safe_filename},
                force=True,
            )
        except Exception as exc:
            _atomic_write(doc_path, original)
            failure_receipt = _write_receipt(
                project_id=project_id,
                filename=safe_filename,
                workflow_run_id=workflow_run_id,
                evidence_refs=refs,
                before_sha=before_sha,
                after_sha=after_sha,
                backup_path=backup_path,
                rag_result={"pushed": 0, "errors": [str(exc)]},
                timestamp=now,
                section=normalized_section,
                status="rolled_back",
                reconciliation_status="restored_after_index_failure",
            )
            raise EvidenceDocUpdateError(
                502,
                "RAG sync failed; documentation was rolled back. "
                f"Receipt: {_relative_to_project_output(project_id, failure_receipt)}",
            ) from exc

        if not RagBridge.push_succeeded(rag_result, expected_count=1):
            _atomic_write(doc_path, original)
            failure_receipt = _write_receipt(
                project_id=project_id,
                filename=safe_filename,
                workflow_run_id=workflow_run_id,
                evidence_refs=refs,
                before_sha=before_sha,
                after_sha=after_sha,
                backup_path=backup_path,
                rag_result=rag_result,
                timestamp=now,
                section=normalized_section,
                status="rolled_back",
                reconciliation_status="restored_after_index_failure",
            )
            error_detail = "; ".join(str(item) for item in rag_result.get("errors", []) if item)
            message = (
                "RAG sync failed; documentation was rolled back. "
                f"Receipt: {_relative_to_project_output(project_id, failure_receipt)}"
            )
            if error_detail:
                message = f"{message} {error_detail}"
            raise EvidenceDocUpdateError(502, message)

        record_push_outcome(
            project_id,
            {
                "before": f"sha256:{before_sha}",
                "after": f"sha256:{after_sha}",
                "outcome": OUTCOME_BOUNDED_PATCH,
                "touched_docs": [safe_filename],
                "reason_code": "harness_evidence_update",
                "doc_sections": {safe_filename: [changed_section]},
                "rag_result": rag_result,
            },
        )
        receipt_path = _write_receipt(
            project_id=project_id,
            filename=safe_filename,
            workflow_run_id=workflow_run_id,
            evidence_refs=refs,
            before_sha=before_sha,
            after_sha=after_sha,
            backup_path=backup_path,
            rag_result=rag_result,
            timestamp=now,
            section=normalized_section,
            status="updated",
            reconciliation_status="indexed",
        )
        relative_receipt = _relative_to_project_output(project_id, receipt_path)

        return EvidenceDocUpdateResult(
            project_id=project_id,
            filename=safe_filename,
            status="updated",
            message=f"Updated {safe_filename} from provided evidence.",
            workflow_run_id=workflow_run_id,
            evidence_refs=refs,
            before_sha=f"sha256:{before_sha}",
            after_sha=f"sha256:{after_sha}",
            receipt_path=relative_receipt,
            docs_path=f"/projects/{project_id}/docs/{safe_filename}",
            rag_result=rag_result,
            version_id=f"sha256:{after_sha}",
            section=normalized_section,
            revert_path=f"/projects/{project_id}/docs/{safe_filename}/evidence-revert",
            reconciliation_status="indexed",
        )


def _validate_project_id(project_id: str) -> str:
    if not _PROJECT_ID_PATTERN.fullmatch(project_id):
        raise EvidenceDocUpdateError(400, "Invalid project identifier.")
    return project_id


def _validate_markdown_filename(filename: str) -> str:
    candidate = filename.strip()
    if not candidate.endswith(".md"):
        raise EvidenceDocUpdateError(400, "Only .md documentation files can be updated.")
    if "/" in candidate or "\\" in candidate or ".." in candidate or Path(candidate).name != candidate:
        raise EvidenceDocUpdateError(400, "Invalid documentation filename.")
    if candidate.startswith("_"):
        raise EvidenceDocUpdateError(400, "Internal documentation files cannot be updated through this workflow.")
    return candidate


def _sanitize_evidence_text(value: str) -> str:
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    text = text.strip()
    if len(text) > _MAX_EVIDENCE_CHARS:
        text = text[:_MAX_EVIDENCE_CHARS].rstrip()
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_redact_match, text)
    return text


def _sanitize_evidence_refs(values: list[str] | None) -> list[str]:
    refs: list[str] = []
    for raw in values or []:
        ref = re.sub(r"\s+", " ", str(raw)).strip()
        if not ref:
            continue
        refs.append(ref[:_MAX_REF_CHARS])
        if len(refs) >= _MAX_REF_COUNT:
            break
    return refs or ["chat_prompt"]


def _normalize_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip().lower()
    if candidate.startswith("sha256:"):
        candidate = candidate[7:]
    if not _SHA256_PATTERN.fullmatch(candidate):
        raise EvidenceDocUpdateError(400, "expected_base_sha must be a SHA-256 digest.")
    return candidate


def _normalize_section_name(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = re.sub(r"^#{1,6}\s+", "", value.strip()).strip()
    if not candidate or "\n" in candidate or "\r" in candidate or len(candidate) > 200:
        raise EvidenceDocUpdateError(400, "Invalid documentation section.")
    return candidate


def _replace_markdown_section(content: str, section: str, replacement: str) -> str:
    heading_pattern = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
    matches = [
        match
        for match in heading_pattern.finditer(content)
        if match.group(2).strip().casefold() == section.casefold()
    ]
    if not matches:
        raise EvidenceDocUpdateError(404, f"Documentation section not found: {section}")
    if len(matches) > 1:
        raise EvidenceDocUpdateError(409, f"Documentation section is ambiguous: {section}")

    target = matches[0]
    level = len(target.group(1))
    body_start = target.end()
    body_end = len(content)
    for following in heading_pattern.finditer(content, body_start):
        if len(following.group(1)) <= level:
            body_end = following.start()
            break

    prefix = content[:body_start].rstrip()
    suffix = content[body_end:].lstrip("\n")
    updated = f"{prefix}\n\n{replacement.strip()}\n"
    if suffix:
        updated = f"{updated}\n{suffix}"
    return updated


def _append_evidence_section(
    content: str,
    *,
    timestamp: datetime,
    workflow_run_id: str | None,
    evidence_refs: list[str],
    evidence_text: str,
) -> str:
    rendered_evidence = evidence_text[:_MAX_RENDERED_EVIDENCE_CHARS].rstrip()
    if len(evidence_text) > _MAX_RENDERED_EVIDENCE_CHARS:
        rendered_evidence = f"{rendered_evidence}\n\n[Evidence truncated for documentation safety.]"
    ref_line = ", ".join(f"`{ref}`" for ref in evidence_refs)
    workflow_line = f"Nexus workflow: `{workflow_run_id}`" if workflow_run_id else "Nexus workflow: `not recorded`"
    section = "\n".join(
        [
            "",
            "## Evidence Update",
            "",
            f"Updated: {timestamp.isoformat()}",
            workflow_line,
            f"Evidence refs: {ref_line}",
            "",
            rendered_evidence,
            "",
        ]
    )
    return f"{content.rstrip()}\n{section}"


def _backup_original_doc(
    *,
    project_id: str,
    filename: str,
    content: str,
    timestamp: datetime,
) -> Path:
    receipt_dir = _receipt_dir(project_id)
    receipt_dir.mkdir(parents=True, exist_ok=True)
    backup_path = receipt_dir / f"{_timestamp_key(timestamp)}_{filename}.bak.md"
    backup_path.write_text(content, encoding="utf-8")
    return backup_path


def _write_receipt(
    *,
    project_id: str,
    filename: str,
    workflow_run_id: str | None,
    evidence_refs: list[str],
    before_sha: str,
    after_sha: str,
    backup_path: Path,
    rag_result: dict[str, Any],
    timestamp: datetime,
    section: str | None,
    status: str,
    reconciliation_status: str,
) -> Path:
    receipt_id = uuid.uuid4().hex
    receipt_path = _receipt_dir(project_id) / (
        f"{_timestamp_key(timestamp)}_{receipt_id}_{filename}.receipt.json"
    )
    payload = {
        "receipt_id": receipt_id,
        "receipt_type": "evidence_doc_update",
        "project_id": project_id,
        "filename": filename,
        "workflow_run_id": workflow_run_id,
        "evidence_refs": evidence_refs,
        "before_sha": f"sha256:{before_sha}",
        "after_sha": f"sha256:{after_sha}",
        "backup_path": _relative_to_project_output(project_id, backup_path),
        "rag_result": rag_result,
        "section": section,
        "status": status,
        "reconciliation_status": reconciliation_status,
        "version_id": f"sha256:{after_sha}",
        "updated_at": timestamp.isoformat(),
        "reason_code": "harness_evidence_update",
    }
    receipt_path.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="ascii",
    )
    return receipt_path


def _receipt_dir(project_id: str) -> Path:
    return config.project_root / "output" / project_id / "_harness_doc_updates"


def _relative_to_project_output(project_id: str, path: Path) -> str:
    base = config.project_root / "output" / project_id
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _atomic_write(path: Path, content: str) -> None:
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, path)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _timestamp_key(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


def _redact_match(match: re.Match[str]) -> str:
    if match.lastindex and match.lastindex >= 1:
        return f"{match.group(1)}=[REDACTED]"
    return "[REDACTED]"
