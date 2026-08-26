"""Bounded OpenCode worker for Git-driven living-document patches.

The agent may reason over one immutable Git revision pair and the candidate
documentation snapshot.  Its only write is a JSON patch report submitted via
the existing OpenCode artifact facade.  Nexus validates and applies that report
inside the candidate workspace; publication remains owned by CandidatePublisher.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any, Protocol

from mcp_server.config import config
from mcp_server.providers.base import AgentResult, LogCallback
from mcp_server.providers.custom.noncode_evidence import redact_text
from mcp_server.providers.custom.validators import secret_leak_findings
from mcp_server.providers.opencode_cli import OpenCodeCliProvider, OpenCodeTaskContract
from mcp_server.services.true_patch_executor import PatchResult


PATCH_REPORT_SCHEMA_VERSION = 1
PATCH_REPORT_PATH = "_reingestion/patch_report.json"
_ALLOWED_ACTIONS = frozenset({"update", "create", "delete"})


class OpenCodeReingestionError(ValueError):
    """The agent result cannot be admitted into a candidate revision."""


class OpenCodePatchRuntime(Protocol):
    async def run(
        self,
        *,
        project_id: str,
        workspace: Path,
        contract: OpenCodeTaskContract,
        prompt: str,
        log_cb: LogCallback,
    ) -> AgentResult: ...


class CliOpenCodePatchRuntime:
    """Thin adapter over the shared pinned OpenCode contract runtime."""

    def __init__(self, provider: OpenCodeCliProvider | None = None) -> None:
        self._provider = provider or OpenCodeCliProvider()

    async def run(
        self,
        *,
        project_id: str,
        workspace: Path,
        contract: OpenCodeTaskContract,
        prompt: str,
        log_cb: LogCallback,
    ) -> AgentResult:
        return await self._provider.run_bounded_contract(
            project_id=project_id,
            workspace=workspace,
            contract=contract,
            log_cb=log_cb,
            step="reingestion_patch",
            prompt=prompt,
        )


@dataclass(frozen=True, slots=True)
class PatchReportOperation:
    path: str
    action: str
    base_sha256: str | None
    content: str | None
    content_sha256: str | None
    evidence_paths: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class OpenCodePatchReport:
    candidate_id: str
    before_sha: str
    after_sha: str
    operations: tuple[PatchReportOperation, ...]
    files_read: tuple[str, ...]
    summary: str


class OpenCodeReingestionExecutor:
    """Produce and apply one validated OpenCode patch report in a candidate."""

    def __init__(self, runtime: OpenCodePatchRuntime | None = None) -> None:
        self._runtime = runtime or CliOpenCodePatchRuntime()

    async def run(self, **kwargs: object) -> PatchResult:
        project_id = _required_str(kwargs, "project_id")
        output_dir = _required_path(kwargs, "output_dir")
        candidate_id = _required_str(kwargs, "candidate_id")
        before_sha = _required_str(kwargs, "before_sha")
        after_sha = _required_str(kwargs, "after_sha")
        full_diff_text = _required_str(kwargs, "full_diff_text")
        raw_changes = kwargs.get("changed_files")
        if not isinstance(raw_changes, Sequence) or isinstance(raw_changes, (str, bytes)):
            return _held("changed_files_missing", "OpenCode reingestion requires changed files")
        changed_files = tuple(_normalise_change(item) for item in raw_changes)
        log_mgr = kwargs.get("log_mgr")
        if log_mgr is None or not hasattr(log_mgr, "emit"):
            return _held("log_manager_missing", "OpenCode reingestion requires a log manager")

        docs_dir = output_dir / "docs"
        if not docs_dir.is_dir():
            return _held("missing_docs", "Candidate documentation tree is absent")
        before_membership = _snapshot_tree(docs_dir)
        evidence_dir = output_dir / "_reingestion"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        redacted_diff, redaction_count = redact_text(full_diff_text)
        (evidence_dir / "git.diff").write_text(redacted_diff, encoding="utf-8")
        (evidence_dir / "changed_files.json").write_text(
            json.dumps(changed_files, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (evidence_dir / "baseline_docs.json").write_text(
            json.dumps(before_membership, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        task = {
            "schema_version": PATCH_REPORT_SCHEMA_VERSION,
            "candidate_id": candidate_id,
            "project_id": project_id,
            "before_sha": before_sha,
            "after_sha": after_sha,
            "max_operations": config.git_diff_patch_max_docs,
            "changed_files": changed_files,
            "baseline_docs": before_membership,
            "output_artifact": PATCH_REPORT_PATH,
        }
        (evidence_dir / "task.json").write_text(
            json.dumps(task, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        prompt = _patch_prompt(task)
        contract = OpenCodeTaskContract(
            phase="produce",
            target=f"reingestion:{candidate_id}",
            prompt_artifact="_reingestion/task.json",
            prompt=prompt,
            evidence_artifacts=(
                "_reingestion/task.json",
                "_reingestion/git.diff",
                "_reingestion/changed_files.json",
                "_reingestion/baseline_docs.json",
                "docs",
            ),
            output_artifact=PATCH_REPORT_PATH,
        )

        result: AgentResult | None = None
        try:
            result = await self._runtime.run(
                project_id=project_id,
                workspace=output_dir,
                contract=contract,
                prompt=prompt,
                log_cb=log_mgr.emit,
            )
            if not result.success:
                return _held("opencode_patch_failed", result.error or "OpenCode patch session failed")
            report = load_patch_report(
                evidence_dir / "patch_report.json",
                candidate_id=candidate_id,
                before_sha=before_sha,
                after_sha=after_sha,
                changed_files=changed_files,
                docs_dir=docs_dir,
            )
            changed = apply_patch_report(
                report,
                docs_dir=docs_dir,
                before_membership=before_membership,
                max_operations=config.git_diff_patch_max_docs,
            )
        except (OSError, OpenCodeReingestionError, RuntimeError, ValueError) as exc:
            telemetry = (
                {
                    "opencode_session_id": result.session_id,
                    "opencode_model": result.model_name,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "cost_usd": result.cost_usd or 0.0,
                    "elapsed_ms": result.duration_ms or 0,
                }
                if result is not None else {}
            )
            return _held("opencode_patch_validation_failed", str(exc), **telemetry)

        after_membership = _snapshot_tree(docs_dir)
        actual_changed = tuple(
            sorted(
                path
                for path in before_membership.keys() | after_membership.keys()
                if before_membership.get(path) != after_membership.get(path)
            )
        )
        if actual_changed != changed:
            return _held(
                "opencode_patch_scope_mismatch",
                "Candidate filesystem changes do not match the validated patch report",
            )
        created = tuple(path for path in changed if path not in before_membership)
        deleted = tuple(path for path in changed if path not in after_membership)
        updated = tuple(path for path in changed if path not in created and path not in deleted)
        return PatchResult(
            action="true_patch",
            patched_groups=[],
            patched_docs=list(changed),
            summary={
                "reason_code": "opencode_patch_succeeded",
                "patch_plan_producer": "opencode",
                "opencode_session_id": result.session_id,
                "opencode_model": result.model_name,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cost_usd": result.cost_usd or 0.0,
                "elapsed_ms": result.duration_ms or 0,
                "files_read": list(report.files_read),
                "file_read_count": len(report.files_read),
                "redacted_diff_values": redaction_count,
                "patched_docs": list(changed),
                "created_docs": list(created),
                "updated_docs": list(updated),
                "deleted_docs": list(deleted),
                "unchanged_artifact_count": len(
                    set(before_membership) & set(after_membership) - set(changed)
                ),
                "agent_summary": report.summary,
            },
            advance_commit=True,
        )


def load_patch_report(
    path: Path,
    *,
    candidate_id: str,
    before_sha: str,
    after_sha: str,
    changed_files: tuple[dict[str, str | None], ...],
    docs_dir: Path,
) -> OpenCodePatchReport:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OpenCodeReingestionError("OpenCode did not submit a patch report") from exc
    except json.JSONDecodeError as exc:
        raise OpenCodeReingestionError("OpenCode patch report is not valid JSON") from exc
    if not isinstance(raw, Mapping):
        raise OpenCodeReingestionError("OpenCode patch report must be an object")
    if raw.get("schema_version") != PATCH_REPORT_SCHEMA_VERSION:
        raise OpenCodeReingestionError("OpenCode patch report schema is unsupported")
    if raw.get("candidate_id") != candidate_id:
        raise OpenCodeReingestionError("OpenCode patch report is bound to another candidate")
    if raw.get("before_sha") != before_sha or raw.get("after_sha") != after_sha:
        raise OpenCodeReingestionError("OpenCode patch report is bound to another Git revision")

    metadata_evidence = {
        "_reingestion/task.json",
        "_reingestion/git.diff",
        "_reingestion/changed_files.json",
        "_reingestion/baseline_docs.json",
    }
    source_evidence: set[str] = set()
    for item in changed_files:
        if item.get("path"):
            source_evidence.add(str(item["path"]))
        if item.get("previous_path"):
            source_evidence.add(str(item["previous_path"]))
    allowed_evidence = metadata_evidence | source_evidence
    files_read = _string_tuple(raw.get("files_read", []), "files_read")
    for value in files_read:
        if value.startswith("docs/"):
            _safe_relative(value.removeprefix("docs/"))
        elif value not in allowed_evidence:
            raise OpenCodeReingestionError(f"patch report cites unavailable file: {value}")

    operations_raw = raw.get("operations")
    if not isinstance(operations_raw, list):
        raise OpenCodeReingestionError("OpenCode patch report operations must be an array")
    operations: list[PatchReportOperation] = []
    for item in operations_raw:
        if not isinstance(item, Mapping):
            raise OpenCodeReingestionError("OpenCode patch report operation must be an object")
        action = _required_mapping_string(item, "action")
        if action not in _ALLOWED_ACTIONS:
            raise OpenCodeReingestionError(f"unsupported patch action: {action}")
        relative = _safe_relative(_required_mapping_string(item, "path"))
        target = (docs_dir / relative).resolve()
        if docs_dir.resolve() not in target.parents:
            raise OpenCodeReingestionError("patch target escapes the docs directory")
        evidence_paths = _string_tuple(item.get("evidence_paths", []), "evidence_paths")
        if (
            not evidence_paths
            or any(value not in allowed_evidence for value in evidence_paths)
            or not set(evidence_paths).intersection(source_evidence)
        ):
            raise OpenCodeReingestionError(f"patch operation has unavailable evidence: {relative}")
        content = item.get("content")
        content_sha = item.get("content_sha256")
        base_sha = item.get("base_sha256")
        if action == "delete":
            if content is not None or content_sha is not None:
                raise OpenCodeReingestionError("delete operation must not include content")
        else:
            if not isinstance(content, str) or not content.strip():
                raise OpenCodeReingestionError(f"{action} operation requires non-empty content")
            if content_sha is not None and (
                not isinstance(content_sha, str) or _sha(content) != content_sha
            ):
                raise OpenCodeReingestionError(f"content hash mismatch for {relative}")
        if base_sha is not None and not _is_sha(base_sha):
            raise OpenCodeReingestionError(f"invalid base hash for {relative}")
        operations.append(
            PatchReportOperation(
                path=relative,
                action=action,
                base_sha256=base_sha if isinstance(base_sha, str) else None,
                content=content if isinstance(content, str) else None,
                content_sha256=_sha(content) if isinstance(content, str) else None,
                evidence_paths=evidence_paths,
                reason=_required_mapping_string(item, "reason"),
            )
        )
    if len({item.path for item in operations}) != len(operations):
        raise OpenCodeReingestionError("patch report contains duplicate document operations")
    summary = raw.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise OpenCodeReingestionError("patch report requires a summary")
    return OpenCodePatchReport(
        candidate_id=candidate_id,
        before_sha=before_sha,
        after_sha=after_sha,
        operations=tuple(operations),
        files_read=files_read,
        summary=summary.strip(),
    )


def apply_patch_report(
    report: OpenCodePatchReport,
    *,
    docs_dir: Path,
    before_membership: Mapping[str, str],
    max_operations: int,
) -> tuple[str, ...]:
    if len(report.operations) > max_operations:
        raise OpenCodeReingestionError(
            f"patch report exceeds the {max_operations}-document blast-radius limit"
        )
    changed: list[str] = []
    for operation in report.operations:
        target = docs_dir / operation.path
        current_hash = before_membership.get(operation.path)
        if operation.action == "create":
            if current_hash is not None or target.exists():
                raise OpenCodeReingestionError(f"create target already exists: {operation.path}")
            if target.suffix.lower() != ".md":
                raise OpenCodeReingestionError("new reingestion artifacts must be Markdown")
        else:
            if current_hash is None or not target.is_file() or target.is_symlink():
                raise OpenCodeReingestionError(f"patch target does not exist: {operation.path}")
            if operation.base_sha256 != current_hash:
                raise OpenCodeReingestionError(f"stale base hash for {operation.path}")
        if operation.action == "delete":
            target.unlink()
        else:
            assert operation.content is not None
            # The contract carries complete replacements. Existing generated
            # packs can contain an explicitly documented, inert placeholder
            # (for example an `.env.example` value). Reject any secret-bearing
            # line that is new, while allowing an unchanged baseline line to
            # survive a targeted update without forcing unrelated rewrites.
            before_content = target.read_text(encoding="utf-8") if target.exists() else ""
            leaks = _new_secret_leak_findings(operation.content, before_content)
            if leaks:
                raise OpenCodeReingestionError(
                    f"candidate content failed secret validation for {operation.path}: {leaks[0]}"
                )
            _atomic_write(target, operation.content)
        changed.append(operation.path)
    return tuple(sorted(changed))


def _patch_prompt(task: Mapping[str, Any]) -> str:
    return f"""You are patching an existing Nexus documentation pack for one immutable Git push.

Read `_reingestion/task.json`, `_reingestion/git.diff`, `_reingestion/changed_files.json`,
`_reingestion/baseline_docs.json`, and the existing files under `docs/`. The Git diff is
credential-redacted and is the authoritative source change. Inspect only evidence needed to
decide which existing documentation statements became stale.

Do not edit files, run shell commands, publish, call RAG, or regenerate the entire pack. Your
only write is one call to `nexus_artifacts_submit_artifact` for `{PATCH_REPORT_PATH}` containing
plain JSON (no Markdown fence). Use this exact schema:

{{
  "schema_version": 1,
  "candidate_id": {json.dumps(task["candidate_id"])},
  "before_sha": {json.dumps(task["before_sha"])},
  "after_sha": {json.dumps(task["after_sha"])},
  "files_read": ["_reingestion/git.diff", "docs/04_EXAMPLE.md"],
  "summary": "short explanation",
  "operations": [
    {{
      "path": "04_EXAMPLE.md",
      "action": "update",
      "base_sha256": "sha256 from baseline_docs.json",
      "content": "complete replacement file content",
      "evidence_paths": ["path/from/changed_files.json"],
      "reason": "why the Git evidence requires this document change"
    }}
  ]
}}

For delete, omit content. The host computes the final content SHA-256; do not invent one.
For create, base_sha256 must be null. Return an
empty operations array when the push changes no documentation truth. Preserve unrelated
sections and wording byte-for-byte wherever possible. Prefer updating an existing canonical
document over creating a new one. Never duplicate an existing document or logical subject.
Every operation needs at least one changed source path as evidence. Keep the total operation
count at or below {int(task["max_operations"])}. Use ASCII Markdown. Submit the report once and stop.
"""


def _normalise_change(value: object) -> dict[str, str | None]:
    if isinstance(value, Mapping):
        status, path, previous = value.get("status"), value.get("path"), value.get("previous_path")
    else:
        status, path, previous = (
            getattr(value, "status", None),
            getattr(value, "path", None),
            getattr(value, "previous_path", None),
        )
    if not isinstance(status, str) or not status.strip() or not isinstance(path, str):
        raise OpenCodeReingestionError("changed file entry is invalid")
    _safe_relative(path)
    if previous is not None:
        if not isinstance(previous, str):
            raise OpenCodeReingestionError("changed file previous path is invalid")
        _safe_relative(previous)
    return {"status": status.strip(), "path": path, "previous_path": previous}


def _snapshot_tree(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or "\\" in value or any(part in {"", ".", ".."} for part in path.parts):
        raise OpenCodeReingestionError(f"unsafe relative path: {value!r}")
    return path.as_posix()


def _required_str(values: Mapping[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OpenCodeReingestionError(f"{key} is required")
    return value


def _required_path(values: Mapping[str, object], key: str) -> Path:
    value = values.get(key)
    if not isinstance(value, Path):
        raise OpenCodeReingestionError(f"{key} path is required")
    return value


def _required_mapping_string(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise OpenCodeReingestionError(f"patch operation {key} is required")
    return item.strip()


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise OpenCodeReingestionError(f"{field_name} must be an array of non-empty strings")
    if len(set(value)) != len(value):
        raise OpenCodeReingestionError(f"{field_name} must not contain duplicates")
    return tuple(value)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_secret_leak_findings(content: str, baseline_content: str) -> list[str]:
    """Return findings that occur on content lines absent from the baseline."""

    findings = secret_leak_findings(content)
    if not findings:
        return []
    baseline_lines = set(baseline_content.splitlines())
    introduced = "\n".join(line for line in content.splitlines() if line not in baseline_lines)
    return secret_leak_findings(introduced)


def _is_sha(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.patch-")
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _held(reason_code: str, reason: str, **summary: object) -> PatchResult:
    return PatchResult(
        action="held",
        patched_groups=[],
        patched_docs=[],
        summary={"reason_code": reason_code, "reason": reason, **summary},
        advance_commit=False,
        error=reason,
    )
