"""Compatibility tests for Gather v2 evidence consumed by legacy Analyze."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcp_server.providers.custom.investigator import EvidenceValidator, StagingToolbox
from mcp_server.providers.custom.models import FactsManifest, UnitFact
from mcp_server.providers.opencode_evidence import (
    EvidenceItem,
    EvidenceScope,
    FinishScopeRequest,
    OpenCodeEvidenceStore,
    ReportGapRequest,
    SubmitEvidenceRequest,
)
from mcp_server.providers.opencode_evidence_export import (
    OpenCodeEvidenceExportError,
    export_legacy_gather_artifacts,
)


def _facts() -> FactsManifest:
    units = [
        UnitFact(name=f"Unit{name}", group="A", path=f"Unit{name}.py")
        for name in ("A", "B", "C", "D")
    ]
    return FactsManifest(
        project_id="project",
        platform="generic",
        canonical_unit_label="units",
        unit_count=len(units),
        groups={"A": len(units)},
        units=units,
    )


def _source(staging: Path, name: str) -> str:
    path = staging / "_tasks" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"{name} contains detailed scanner evidence for the integration route.\n",
        encoding="utf-8",
    )
    return path.relative_to(staging).as_posix()


def _finish_scope(
    store: OpenCodeEvidenceStore,
    staging: Path,
    *,
    scope_id: str,
    evidenced_unit: str,
    gap_unit: str,
) -> None:
    source_ref = _source(staging, scope_id)
    store.register_scope(
        EvidenceScope(
            scope_id=scope_id,
            group="A",
            allowed_units=(evidenced_unit, gap_unit),
            source_ref=source_ref,
        )
    )
    store.submit_evidence(
        SubmitEvidenceRequest(
            scope_id=scope_id,
            evidence=(
                EvidenceItem(
                    unit_name=evidenced_unit,
                    category="data flow",
                    snippet=(
                        f"{evidenced_unit} sends customer records through the integration route"
                    ),
                    observation=(
                        f"{evidenced_unit} sends customer records through the evidenced route."
                    ),
                ),
            ),
        )
    )
    store.report_gap(
        ReportGapRequest(
            scope_id=scope_id,
            unit_name=gap_unit,
            reason="scanner source contains no executable detail for this unit",
        )
    )
    store.finish_scope(FinishScopeRequest(scope_id=scope_id))


def _contract(
    staging: Path,
    task_id: str,
    allowed_units: tuple[str, ...],
) -> SimpleNamespace:
    source_ref = _source(staging, task_id)
    return SimpleNamespace(
        output_artifact=(
            f"_transient/opencode/investigation_raw/{task_id}.json"
        ),
        metadata={
            "task_id": task_id,
            "group": "A",
            "allowed_units": list(allowed_units),
            "source_ref": source_ref,
            "validation_receipt": (
                f"_transient/opencode/gather_guard/{task_id}.validated.json"
            ),
        },
    )


def test_export_aggregates_logical_scopes_into_legacy_task_boundaries(
    tmp_path: Path,
) -> None:
    facts = _facts()
    store = OpenCodeEvidenceStore(tmp_path, facts)
    _finish_scope(
        store,
        tmp_path,
        scope_id="logical_scope_1",
        evidenced_unit="UnitA",
        gap_unit="UnitB",
    )
    _finish_scope(
        store,
        tmp_path,
        scope_id="logical_scope_2",
        evidenced_unit="UnitC",
        gap_unit="UnitD",
    )
    first = _contract(tmp_path, "legacy_task_1", ("UnitA", "UnitC"))
    second = _contract(tmp_path, "legacy_task_2", ("UnitB", "UnitD"))

    exported = export_legacy_gather_artifacts(tmp_path, facts, [first, second])

    assert [item.scope_id for item in exported] == ["legacy_task_1", "legacy_task_2"]
    first_payload = json.loads((tmp_path / first.output_artifact).read_text())
    second_payload = json.loads((tmp_path / second.output_artifact).read_text())
    assert {item["unit_name"] for item in first_payload["evidence"]} == {
        "UnitA",
        "UnitC",
    }
    assert first_payload["skipped_units"] == {}
    assert second_payload == {
        "evidence": [],
        "skipped_units": {
            "UnitB": "scanner source contains no executable detail for this unit",
            "UnitD": "scanner source contains no executable detail for this unit",
        },
    }
    assert json.loads(
        (tmp_path / second.metadata["validation_receipt"]).read_text()
    ) == {"evidence_count": 0, "task_id": "legacy_task_2", "validated": True}

    # The exported payload is accepted by the same validator used in
    # OpenCodeCliProvider._finalize_gather.
    validator = EvidenceValidator(facts, StagingToolbox(tmp_path))
    for contract, payload in ((first, first_payload), (second, second_payload)):
        report = validator.validate_report(
            contract.metadata["task_id"],
            payload,
            set(contract.metadata["allowed_units"]),
            default_chunk_ref=contract.metadata["source_ref"],
        )
        assert {
            item.unit_name for item in report.evidence
        } | set(report.skipped_units) == set(contract.metadata["allowed_units"])

    before = (tmp_path / first.output_artifact).read_bytes()
    assert export_legacy_gather_artifacts(tmp_path, facts, [first, second]) == exported
    assert (tmp_path / first.output_artifact).read_bytes() == before


def test_export_preflights_all_contracts_before_writing(tmp_path: Path) -> None:
    facts = _facts()
    store = OpenCodeEvidenceStore(tmp_path, facts)
    _finish_scope(
        store,
        tmp_path,
        scope_id="only_finished_scope",
        evidenced_unit="UnitA",
        gap_unit="UnitB",
    )
    valid = _contract(tmp_path, "valid_task", ("UnitA",))
    missing = _contract(tmp_path, "missing_task", ("UnitC",))

    with pytest.raises(OpenCodeEvidenceExportError, match="does not cover"):
        export_legacy_gather_artifacts(tmp_path, facts, [valid, missing])

    assert not (tmp_path / valid.output_artifact).exists()
    assert not (tmp_path / valid.metadata["validation_receipt"]).exists()


def test_export_rejects_unsafe_legacy_targets(tmp_path: Path) -> None:
    facts = _facts()
    store = OpenCodeEvidenceStore(tmp_path, facts)
    _finish_scope(
        store,
        tmp_path,
        scope_id="finished_scope",
        evidenced_unit="UnitA",
        gap_unit="UnitB",
    )
    contract = _contract(tmp_path, "legacy_task", ("UnitA",))
    contract.output_artifact = "../outside.json"

    with pytest.raises(OpenCodeEvidenceExportError, match="staging-relative"):
        export_legacy_gather_artifacts(tmp_path, facts, [contract])

    assert not (tmp_path.parent / "outside.json").exists()
