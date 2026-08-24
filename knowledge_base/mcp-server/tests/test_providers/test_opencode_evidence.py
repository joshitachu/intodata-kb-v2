"""Focused tests for the framework-neutral Gather v2 evidence store."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from mcp_server.providers.custom.models import FactsManifest, UnitFact
from mcp_server.providers.opencode_evidence import (
    EvidenceIncompleteError,
    EvidenceItem,
    EvidenceScope,
    EvidenceScopeConflict,
    EvidenceValidationError,
    FinishScopeRequest,
    GetProgressRequest,
    OpenCodeEvidenceStore,
    ReportGapRequest,
    SubmitEvidenceRequest,
)


def _facts() -> FactsManifest:
    return FactsManifest(
        project_id="project",
        platform="generic",
        canonical_unit_label="units",
        unit_count=3,
        groups={"A": 2, "B": 1},
        units=[
            UnitFact(name="UnitA", group="A", path="UnitA.py"),
            UnitFact(name="UnitB", group="A", path="UnitB.py"),
            UnitFact(name="UnitC", group="B", path="UnitC.py"),
        ],
    )


def _store(tmp_path: Path) -> tuple[OpenCodeEvidenceStore, str]:
    source = tmp_path / "_tasks" / "A_task_1.md"
    source.parent.mkdir(parents=True)
    source.write_text("UnitA sends customer records to UnitB through a route.\n", encoding="utf-8")
    return OpenCodeEvidenceStore(tmp_path, _facts()), "_tasks/A_task_1.md"


def _scope(source_ref: str, scope_id: str = "A_task_1") -> EvidenceScope:
    return EvidenceScope(
        scope_id=scope_id,
        group="A",
        allowed_units=("UnitA", "UnitB"),
        source_ref=source_ref,
    )


def _item(unit: str = "UnitA", suffix: str = "") -> EvidenceItem:
    return EvidenceItem(
        unit_name=unit,
        category="data flow",
        snippet=f"customer records flow through the integration route {suffix}",
        observation=f"{unit} sends customer records through the evidenced integration route {suffix}.",
    )


def test_submit_is_validated_durable_resumable_and_idempotent(tmp_path: Path) -> None:
    store, source_ref = _store(tmp_path)
    opened = store.register_scope(_scope(source_ref))
    assert opened.remaining_units == ("UnitA", "UnitB")

    request = SubmitEvidenceRequest(scope_id="A_task_1", evidence=(_item(),))
    first = store.submit_evidence(request)
    duplicate = store.submit_evidence(request)

    assert first.accepted_count == 1
    assert first.progress.evidenced_units == ("UnitA",)
    assert duplicate.accepted_count == 0
    assert duplicate.duplicate_count == 1
    assert duplicate.progress.version == first.progress.version

    resumed = OpenCodeEvidenceStore(tmp_path, _facts())
    progress = resumed.get_progress(GetProgressRequest(scope_id="A_task_1"))
    assert progress.evidence_count == 1
    assert progress.remaining_units == ("UnitB",)
    assert (tmp_path / "_evidence/scopes/A_task_1/scope.json").is_file()
    state = json.loads(
        (tmp_path / "_evidence/scopes/A_task_1/state.json").read_text(encoding="utf-8")
    )
    assert state["schema_version"] == 2
    assert state["evidence"][0]["source_file"] == source_ref
    assert state["evidence"][0]["chunk_ref"] == source_ref


def test_report_gap_and_finish_create_typed_export_once(tmp_path: Path) -> None:
    store, source_ref = _store(tmp_path)
    store.register_scope(_scope(source_ref))
    store.submit_evidence(
        SubmitEvidenceRequest(scope_id="A_task_1", evidence=(_item(),))
    )
    ready = store.report_gap(
        ReportGapRequest(
            scope_id="A_task_1",
            unit_name="UnitB",
            reason="scanner source contains no executable detail for this unit",
        )
    )
    assert ready.complete is False
    assert ready.ready_to_finish is True
    assert ready.gap_units == ("UnitB",)

    finished = store.finish_scope(FinishScopeRequest(scope_id="A_task_1"))
    repeated = store.finish_scope(FinishScopeRequest(scope_id="A_task_1"))

    assert finished.progress.status == "complete"
    assert finished.progress.complete is True
    assert finished.report.skipped_units == {
        "UnitB": "scanner source contains no executable detail for this unit"
    }
    assert repeated.report == finished.report
    assert finished.report_artifact == "_evidence/scopes/A_task_1/report.json"
    exports = store.list_scope_reports()
    assert len(exports) == 1
    assert exports[0].scope.scope_id == "A_task_1"
    assert exports[0].report == finished.report
    with pytest.raises(EvidenceScopeConflict, match="already complete"):
        store.submit_evidence(
            SubmitEvidenceRequest(scope_id="A_task_1", evidence=(_item("UnitB"),))
        )


def test_resume_reopens_only_coordinator_generated_gaps(tmp_path: Path) -> None:
    store, source_ref = _store(tmp_path)
    store.register_scope(_scope(source_ref))
    exhausted_reason = "OpenCode scope ended before this unit was evidenced"
    store.report_gap(
        ReportGapRequest(
            scope_id="A_task_1",
            unit_name="UnitA",
            reason=exhausted_reason,
        )
    )
    store.report_gap(
        ReportGapRequest(
            scope_id="A_task_1",
            unit_name="UnitB",
            reason="scanner source genuinely contains no detail for this unit",
        )
    )
    store.finish_scope(FinishScopeRequest(scope_id="A_task_1"))

    resumed = store.reopen_system_gaps("A_task_1", reason=exhausted_reason)

    assert resumed.status == "open"
    assert resumed.complete is False
    assert resumed.remaining_units == ("UnitA",)
    assert resumed.gap_units == ("UnitB",)
    store.submit_evidence(
        SubmitEvidenceRequest(scope_id="A_task_1", evidence=(_item("UnitA"),))
    )
    finished = store.finish_scope(FinishScopeRequest(scope_id="A_task_1"))
    assert finished.progress.complete is True


def test_finish_rejects_unresolved_units_and_invalid_evidence(tmp_path: Path) -> None:
    store, source_ref = _store(tmp_path)
    store.register_scope(_scope(source_ref))

    with pytest.raises(EvidenceValidationError, match="unknown canonical unit"):
        store.submit_evidence(
            SubmitEvidenceRequest(scope_id="A_task_1", evidence=(_item("Unknown"),))
        )
    with pytest.raises(EvidenceValidationError, match="snippet is too short"):
        store.submit_evidence(
            SubmitEvidenceRequest(
                scope_id="A_task_1",
                evidence=(
                    EvidenceItem(
                        unit_name="UnitA",
                        category="flow",
                        snippet="short",
                        observation="UnitA has a sufficiently detailed observation for validation.",
                    ),
                ),
            )
        )
    with pytest.raises(EvidenceIncompleteError, match="UnitA, UnitB"):
        store.finish_scope(FinishScopeRequest(scope_id="A_task_1"))


def test_scope_registration_enforces_canonical_units_group_source_and_id(
    tmp_path: Path,
) -> None:
    store, source_ref = _store(tmp_path)
    with pytest.raises(ValidationError):
        EvidenceScope(
            scope_id="../escape",
            group="A",
            allowed_units=("UnitA",),
            source_ref=source_ref,
        )
    with pytest.raises(EvidenceValidationError, match="does not own units"):
        store.register_scope(
            EvidenceScope(
                scope_id="wrong-group",
                group="A",
                allowed_units=("UnitC",),
                source_ref=source_ref,
            )
        )
    with pytest.raises(EvidenceValidationError, match="source does not exist"):
        store.register_scope(
            EvidenceScope(
                scope_id="missing-source",
                group="A",
                allowed_units=("UnitA",),
                source_ref="_tasks/missing.md",
            )
        )


def test_concurrent_submissions_do_not_lose_updates_or_leave_temp_files(
    tmp_path: Path,
) -> None:
    store, source_ref = _store(tmp_path)
    store.register_scope(_scope(source_ref))

    def submit(index: int) -> None:
        store.submit_evidence(
            SubmitEvidenceRequest(
                scope_id="A_task_1",
                evidence=(_item("UnitA", suffix=str(index)),),
            )
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(submit, range(24)))

    progress = store.get_progress(GetProgressRequest(scope_id="A_task_1"))
    assert progress.evidence_count == 24
    assert progress.version == 24
    assert not list((tmp_path / "_evidence").rglob("*.tmp"))
