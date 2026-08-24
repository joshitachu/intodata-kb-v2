"""Durable Gather v2 evidence operations for run-scoped agent bridges.

The store is deliberately independent of OpenCode and MCP transports.  A
bridge registers bounded scopes, then exposes the four domain operations below.
Validated artifacts under ``staging/_evidence`` are the resumable source of
truth; conversation history is never part of the contract.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mcp_server.providers.custom.investigator import (
    EvidenceValidator,
    InvestigatorError,
    StagingToolbox,
)
from mcp_server.providers.custom.models import (
    FactsManifest,
    InvestigationEvidence,
    InvestigationReport,
)

_SCHEMA_VERSION = 2
_SCOPE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"


class OpenCodeEvidenceError(RuntimeError):
    """Base error for durable evidence operations."""


class EvidenceScopeNotFound(OpenCodeEvidenceError):
    """Raised when an operation names an unregistered scope."""


class EvidenceScopeConflict(OpenCodeEvidenceError):
    """Raised when a scope or unit already has conflicting durable state."""


class EvidenceValidationError(OpenCodeEvidenceError):
    """Raised when submitted evidence violates canonical Gather semantics."""


class EvidenceIncompleteError(OpenCodeEvidenceError):
    """Raised when a scope is finished before every unit is resolved."""


class EvidenceScope(BaseModel):
    """Immutable orchestration contract for one bounded Gather scope.

    ``scope_id`` is the canonical evidence task id. ``source_ref`` is the
    staging-relative evidence chunk used as the validator's default source.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = _SCHEMA_VERSION
    scope_id: str = Field(pattern=_SCOPE_ID_PATTERN)
    group: str = Field(min_length=1)
    allowed_units: tuple[str, ...] = Field(min_length=1)
    source_ref: str = Field(min_length=1)

    @field_validator("allowed_units")
    @classmethod
    def _canonical_units(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(unit.strip() for unit in value)
        if any(not unit for unit in cleaned):
            raise ValueError("allowed_units cannot contain blank names")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("allowed_units cannot contain duplicates")
        return cleaned


class EvidenceItem(BaseModel):
    """One typed evidence claim accepted by ``submit_evidence``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    unit_name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    source_file: str | None = None
    line_range: str | None = None
    chunk_ref: str | None = None
    snippet: str | None = None
    observation: str = Field(min_length=1)


class SubmitEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str = Field(pattern=_SCOPE_ID_PATTERN)
    evidence: tuple[EvidenceItem, ...] = Field(min_length=1)


class ReportGapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str = Field(pattern=_SCOPE_ID_PATTERN)
    unit_name: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def _clean_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("reason cannot be blank")
        return cleaned


class GetProgressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str = Field(pattern=_SCOPE_ID_PATTERN)


class FinishScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str = Field(pattern=_SCOPE_ID_PATTERN)


class EvidenceProgress(BaseModel):
    """Transport-neutral progress snapshot returned by every operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str
    group: str
    status: Literal["open", "complete"]
    version: int = Field(ge=0)
    expected_units: tuple[str, ...]
    evidenced_units: tuple[str, ...]
    gap_units: tuple[str, ...]
    remaining_units: tuple[str, ...]
    evidence_count: int = Field(ge=0)
    complete: bool
    ready_to_finish: bool
    report_artifact: str | None = None


class SubmitEvidenceResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    progress: EvidenceProgress
    accepted_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)


class FinishScopeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    progress: EvidenceProgress
    report: InvestigationReport
    report_artifact: str


class EvidenceScopeReport(BaseModel):
    """Typed export record for compatibility/finalization adapters."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: EvidenceScope
    report: InvestigationReport
    report_artifact: str


class _EvidenceScopeState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = _SCHEMA_VERSION
    scope_id: str
    status: Literal["open", "complete"] = "open"
    version: int = Field(default=0, ge=0)
    evidence: list[InvestigationEvidence] = Field(default_factory=list)
    gaps: dict[str, str] = Field(default_factory=dict)
    report_artifact: str | None = None


def _canonical_json(value: BaseModel | Mapping[str, object]) -> bytes:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"


def _atomic_write_json(path: Path, value: BaseModel | Mapping[str, object]) -> None:
    """Durably replace one JSON artifact without exposing partial content."""

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            output.write(_canonical_json(value))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_fd = os.open(path.parent, directory_flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary_path.unlink(missing_ok=True)


class OpenCodeEvidenceStore:
    """Canonical, resumable store for Gather v2 evidence scopes."""

    def __init__(self, staging_dir: Path, facts: FactsManifest) -> None:
        self.staging_dir = staging_dir.resolve()
        self.facts = facts
        self.root = self.staging_dir / "_evidence"
        self.scopes_root = self.root / "scopes"
        self._ensure_directory(self.root)
        self._ensure_directory(self.scopes_root)
        self.toolbox = StagingToolbox(self.staging_dir)
        self.validator = EvidenceValidator(facts, self.toolbox)
        self._units = {unit.name: unit for unit in facts.units}

    def register_scope(self, scope: EvidenceScope) -> EvidenceProgress:
        """Create a scope once, or reopen an identical durable definition."""

        self._validate_scope(scope)
        with self._locked(scope.scope_id, create=True):
            definition_path = self._definition_path(scope.scope_id)
            state_path = self._state_path(scope.scope_id)
            if definition_path.is_file():
                persisted = self._read_model(definition_path, EvidenceScope)
                if persisted != scope:
                    raise EvidenceScopeConflict(
                        f"scope {scope.scope_id!r} is already registered differently"
                    )
            else:
                _atomic_write_json(definition_path, scope)

            if state_path.is_file():
                state = self._read_state(scope.scope_id)
            else:
                state = _EvidenceScopeState(scope_id=scope.scope_id)
                _atomic_write_json(state_path, state)
            return self._progress(scope, state)

    def submit_evidence(self, request: SubmitEvidenceRequest) -> SubmitEvidenceResult:
        """Validate and atomically add evidence to one open scope."""

        with self._locked(request.scope_id):
            scope = self._read_scope(request.scope_id)
            state = self._read_state(request.scope_id)
            self._require_open(state)
            payload = {
                "evidence": [item.model_dump(exclude_none=True) for item in request.evidence],
                "skipped_units": {},
            }
            accepted, _skipped, _missing, errors = self.validator.collect_partial_evidence(
                payload,
                set(scope.allowed_units),
                default_chunk_ref=scope.source_ref,
            )
            if errors or len(accepted) != len(request.evidence):
                reason = "; ".join(errors) or "evidence submission was not fully accepted"
                raise EvidenceValidationError(reason)

            known = {self._evidence_key(item) for item in state.evidence}
            new_items: list[InvestigationEvidence] = []
            for item in accepted:
                key = self._evidence_key(item)
                if key not in known:
                    known.add(key)
                    new_items.append(item)

            changed = bool(new_items)
            if new_items:
                state.evidence.extend(new_items)
                state.evidence.sort(key=self._evidence_key)
                for unit_name in {item.unit_name for item in new_items}:
                    changed = state.gaps.pop(unit_name, None) is not None or changed
            if changed:
                state.version += 1
                _atomic_write_json(self._state_path(scope.scope_id), state)

            return SubmitEvidenceResult(
                progress=self._progress(scope, state),
                accepted_count=len(new_items),
                duplicate_count=len(accepted) - len(new_items),
            )

    def report_gap(self, request: ReportGapRequest) -> EvidenceProgress:
        """Resolve one unit with a durable, explicit no-evidence reason."""

        with self._locked(request.scope_id):
            scope = self._read_scope(request.scope_id)
            state = self._read_state(request.scope_id)
            self._require_open(state)
            if request.unit_name not in scope.allowed_units:
                raise EvidenceValidationError(
                    f"unit is not assigned to this evidence scope: {request.unit_name}"
                )
            if any(item.unit_name == request.unit_name for item in state.evidence):
                raise EvidenceScopeConflict(
                    f"unit {request.unit_name!r} already has validated evidence"
                )
            if state.gaps.get(request.unit_name) != request.reason:
                state.gaps[request.unit_name] = request.reason
                state.gaps = dict(sorted(state.gaps.items()))
                state.version += 1
                _atomic_write_json(self._state_path(scope.scope_id), state)
            return self._progress(scope, state)

    def get_progress(self, request: GetProgressRequest) -> EvidenceProgress:
        """Return a consistent snapshot for one scope."""

        with self._locked(request.scope_id):
            scope = self._read_scope(request.scope_id)
            state = self._read_state(request.scope_id)
            return self._progress(scope, state)

    def reopen_system_gaps(self, scope_id: str, *, reason: str) -> EvidenceProgress:
        """Reopen coordinator-generated gaps while preserving agent conclusions.

        A system fallback gap means an invocation exhausted its bounded attempts;
        it is not evidence that the unit cannot be investigated. A later user run
        may therefore retry those units (for example after tool configuration is
        repaired). Gaps carrying any other reason remain durable.
        """

        with self._locked(scope_id):
            scope = self._read_scope(scope_id)
            state = self._read_state(scope_id)
            removable = [unit for unit, value in state.gaps.items() if value == reason]
            if removable:
                for unit_name in removable:
                    state.gaps.pop(unit_name, None)
                state.gaps = dict(sorted(state.gaps.items()))
                state.status = "open"
                state.report_artifact = None
                state.version += 1
                _atomic_write_json(self._state_path(scope_id), state)
            return self._progress(scope, state)

    def finish_scope(self, request: FinishScopeRequest) -> FinishScopeResult:
        """Finalize a complete scope into a validated InvestigationReport."""

        with self._locked(request.scope_id):
            scope = self._read_scope(request.scope_id)
            state = self._read_state(request.scope_id)
            if state.status == "complete":
                report = self._read_report(scope.scope_id)
                progress = self._progress(scope, state)
                assert state.report_artifact is not None
                return FinishScopeResult(
                    progress=progress,
                    report=report,
                    report_artifact=state.report_artifact,
                )

            progress = self._progress(scope, state)
            if progress.remaining_units:
                raise EvidenceIncompleteError(
                    f"scope {scope.scope_id!r} has unresolved units: "
                    + ", ".join(progress.remaining_units)
                )
            try:
                report = self.validator.validate_report(
                    scope.group,
                    {
                        "evidence": [item.model_dump(mode="json") for item in state.evidence],
                        "skipped_units": state.gaps,
                    },
                    set(scope.allowed_units),
                    default_chunk_ref=scope.source_ref,
                )
            except InvestigatorError as exc:
                raise EvidenceValidationError(str(exc)) from exc

            report_path = self._report_path(scope.scope_id)
            _atomic_write_json(report_path, report)
            state.status = "complete"
            state.version += 1
            state.report_artifact = report_path.relative_to(self.staging_dir).as_posix()
            _atomic_write_json(self._state_path(scope.scope_id), state)
            return FinishScopeResult(
                progress=self._progress(scope, state),
                report=report,
                report_artifact=state.report_artifact,
            )

    def list_scope_reports(self) -> tuple[EvidenceScopeReport, ...]:
        """Return all finished reports in stable scope-id order."""

        reports: list[EvidenceScopeReport] = []
        scope_ids: list[str] = []
        for child in self.scopes_root.iterdir():
            if child.is_dir() and (child / "scope.json").is_file():
                try:
                    scope_ids.append(self._read_model(child / "scope.json", EvidenceScope).scope_id)
                except OpenCodeEvidenceError:
                    continue
        for scope_id in sorted(scope_ids):
            with self._locked(scope_id):
                scope = self._read_scope(scope_id)
                state = self._read_state(scope_id)
                if state.status != "complete" or not state.report_artifact:
                    continue
                reports.append(
                    EvidenceScopeReport(
                        scope=scope,
                        report=self._read_report(scope_id),
                        report_artifact=state.report_artifact,
                    )
                )
        return tuple(reports)

    def _validate_scope(self, scope: EvidenceScope) -> None:
        unknown = sorted(set(scope.allowed_units) - set(self._units))
        if unknown:
            raise EvidenceValidationError(
                "scope contains unknown canonical units: " + ", ".join(unknown)
            )
        wrong_group = sorted(
            unit for unit in scope.allowed_units if self._units[unit].group != scope.group
        )
        if wrong_group:
            raise EvidenceValidationError(
                f"scope group {scope.group!r} does not own units: " + ", ".join(wrong_group)
            )
        try:
            source = self.toolbox.resolve_inside_staging(scope.source_ref)
        except InvestigatorError as exc:
            raise EvidenceValidationError(str(exc)) from exc
        if not source.is_file():
            raise EvidenceValidationError(f"scope source does not exist: {scope.source_ref}")
        if self.toolbox.relative_path(source) != scope.source_ref:
            raise EvidenceValidationError(
                "scope source_ref must be a canonical staging-relative path"
            )

    @staticmethod
    def _require_open(state: _EvidenceScopeState) -> None:
        if state.status != "open":
            raise EvidenceScopeConflict(f"scope {state.scope_id!r} is already complete")

    def _progress(
        self, scope: EvidenceScope, state: _EvidenceScopeState
    ) -> EvidenceProgress:
        evidenced = tuple(sorted({item.unit_name for item in state.evidence}))
        gaps = tuple(sorted(state.gaps))
        resolved = set(evidenced) | set(gaps)
        remaining = tuple(unit for unit in scope.allowed_units if unit not in resolved)
        return EvidenceProgress(
            scope_id=scope.scope_id,
            group=scope.group,
            status=state.status,
            version=state.version,
            expected_units=scope.allowed_units,
            evidenced_units=evidenced,
            gap_units=gaps,
            remaining_units=remaining,
            evidence_count=len(state.evidence),
            complete=state.status == "complete",
            ready_to_finish=state.status == "open" and not remaining,
            report_artifact=state.report_artifact,
        )

    @staticmethod
    def _evidence_key(item: InvestigationEvidence) -> str:
        return hashlib.sha256(_canonical_json(item)).hexdigest()

    @contextmanager
    def _locked(self, scope_id: str, *, create: bool = False) -> Iterator[None]:
        scope_dir = self._scope_dir(scope_id)
        if create:
            self._ensure_directory(scope_dir)
        elif not self._definition_path(scope_id).is_file():
            raise EvidenceScopeNotFound(f"evidence scope is not registered: {scope_id}")
        lock_path = scope_dir / ".lock"
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise OpenCodeEvidenceError(f"cannot lock evidence scope {scope_id!r}") from exc
        with os.fdopen(descriptor, "a+b", closefd=True) as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _scope_dir(self, scope_id: str) -> Path:
        if not scope_id or len(scope_id) > 128 or any(
            char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-"
            for char in scope_id
        ) or not scope_id[0].isalnum():
            raise EvidenceScopeNotFound("invalid evidence scope id")
        return self.scopes_root / scope_id

    def _definition_path(self, scope_id: str) -> Path:
        return self._scope_dir(scope_id) / "scope.json"

    def _state_path(self, scope_id: str) -> Path:
        return self._scope_dir(scope_id) / "state.json"

    def _report_path(self, scope_id: str) -> Path:
        return self._scope_dir(scope_id) / "report.json"

    def _read_scope(self, scope_id: str) -> EvidenceScope:
        path = self._definition_path(scope_id)
        if not path.is_file():
            raise EvidenceScopeNotFound(f"evidence scope is not registered: {scope_id}")
        scope = self._read_model(path, EvidenceScope)
        if scope.scope_id != scope_id:
            raise OpenCodeEvidenceError("evidence scope definition id mismatch")
        return scope

    def _read_state(self, scope_id: str) -> _EvidenceScopeState:
        path = self._state_path(scope_id)
        if not path.is_file():
            raise OpenCodeEvidenceError(f"evidence scope state is missing: {scope_id}")
        state = self._read_model(path, _EvidenceScopeState)
        if state.scope_id != scope_id:
            raise OpenCodeEvidenceError("evidence scope state id mismatch")
        return state

    def _read_report(self, scope_id: str) -> InvestigationReport:
        path = self._report_path(scope_id)
        if not path.is_file():
            raise OpenCodeEvidenceError(f"finished evidence report is missing: {scope_id}")
        return self._read_model(path, InvestigationReport)

    @staticmethod
    def _read_model(path: Path, model_type: type[BaseModel]):
        try:
            return model_type.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise OpenCodeEvidenceError(f"invalid evidence artifact: {path.name}") from exc

    @staticmethod
    def _ensure_directory(path: Path) -> None:
        if path.exists():
            if path.is_symlink() or not path.is_dir():
                raise OpenCodeEvidenceError(f"evidence store path is unsafe: {path}")
            return
        try:
            path.mkdir(mode=0o700, parents=True)
        except FileExistsError:
            if path.is_symlink() or not path.is_dir():
                raise OpenCodeEvidenceError(f"evidence store path is unsafe: {path}")


__all__ = [
    "EvidenceIncompleteError",
    "EvidenceItem",
    "EvidenceProgress",
    "EvidenceScope",
    "EvidenceScopeConflict",
    "EvidenceScopeNotFound",
    "EvidenceScopeReport",
    "EvidenceValidationError",
    "FinishScopeRequest",
    "FinishScopeResult",
    "GetProgressRequest",
    "OpenCodeEvidenceError",
    "OpenCodeEvidenceStore",
    "ReportGapRequest",
    "SubmitEvidenceRequest",
    "SubmitEvidenceResult",
]
