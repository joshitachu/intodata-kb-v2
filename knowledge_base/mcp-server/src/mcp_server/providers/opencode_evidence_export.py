"""Compatibility export from canonical Gather v2 evidence to legacy artifacts.

The canonical evidence store is the durable source of truth.  This module only
materializes the narrow JSON hand-off consumed by the existing OpenCode Gather
finalizer, allowing Analyze and later phases to remain unchanged while Gather
execution moves to the v2 store.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from mcp_server.providers.custom.models import FactsManifest


class LegacyGatherContract(Protocol):
    """The subset of ``OpenCodeTaskContract`` required by the exporter."""

    output_artifact: str
    metadata: Mapping[str, Any]


class OpenCodeEvidenceExportError(ValueError):
    """Canonical evidence cannot be exported to the requested legacy contract."""


@dataclass(frozen=True)
class LegacyGatherExport:
    """One deterministic compatibility artifact pair."""

    scope_id: str
    raw_artifact: str
    validation_receipt: str
    evidence_count: int


@dataclass(frozen=True)
class _ExportPlan:
    scope_id: str
    raw_artifact: str
    raw_path: Path
    receipt_artifact: str
    receipt_path: Path
    evidence: tuple[dict[str, Any], ...]
    skipped_units: dict[str, str]


def _artifact_path(
    staging_dir: Path,
    artifact: object,
    *,
    expected_prefix: tuple[str, ...],
) -> tuple[str, Path]:
    value = str(artifact or "").strip()
    relative = PurePosixPath(value)
    if not value or relative.is_absolute() or ".." in relative.parts:
        raise OpenCodeEvidenceExportError(
            f"legacy artifact must be a staging-relative path: {value!r}"
        )
    if relative.parts[: len(expected_prefix)] != expected_prefix:
        raise OpenCodeEvidenceExportError(
            f"legacy artifact {value!r} must be under {'/'.join(expected_prefix)}"
        )
    root = staging_dir.resolve()
    path = (root / Path(*relative.parts)).resolve()
    if path == root or root not in path.parents:
        raise OpenCodeEvidenceExportError(
            f"legacy artifact escapes the staging directory: {value!r}"
        )
    return relative.as_posix(), path


def _source_path(staging_dir: Path, source_ref: object) -> Path:
    value = str(source_ref or "").strip()
    relative = PurePosixPath(value)
    if not value or relative.is_absolute() or ".." in relative.parts:
        raise OpenCodeEvidenceExportError(
            f"canonical scope source_ref must be staging-relative: {value!r}"
        )
    root = staging_dir.resolve()
    path = (root / Path(*relative.parts)).resolve()
    if root not in path.parents or not path.is_file():
        raise OpenCodeEvidenceExportError(
            f"canonical scope source_ref is unavailable: {value!r}"
        )
    return path


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def export_legacy_gather_artifacts(
    staging_dir: Path,
    facts: FactsManifest,
    contracts: Iterable[LegacyGatherContract],
) -> tuple[LegacyGatherExport, ...]:
    """Export finished Gather v2 scopes for the existing Gather finalizer.

    Every contract is preflighted before any file is written. Finished logical
    scopes are aggregated by group, then evidence and explicit gaps are selected
    by the contract's canonical unit allowlist. This deliberately does not map
    scope IDs one-to-one to legacy task IDs: v2 scopes are independent execution
    boundaries, while legacy contracts remain finalization checkpoints.
    """

    # Imported lazily so this compatibility module does not participate in the
    # evidence store's model/import graph.
    from mcp_server.providers.opencode_evidence import OpenCodeEvidenceStore

    root = Path(staging_dir).resolve()
    reports_by_group: dict[str, list[Any]] = {}
    unit_groups = {unit.name: unit.group for unit in facts.units}
    for item in sorted(
        OpenCodeEvidenceStore(root, facts).list_scope_reports(),
        key=lambda value: value.scope.scope_id,
    ):
        scope = item.scope
        if item.report.group != scope.group:
            raise OpenCodeEvidenceExportError(
                f"canonical scope {scope.scope_id!r} report has a different group"
            )
        _source_path(root, scope.source_ref)
        allowed = set(scope.allowed_units)
        invalid_units = sorted(
            unit
            for unit in allowed
            if unit_groups.get(unit) != scope.group
        )
        reported_units = {
            evidence.unit_name for evidence in item.report.evidence
        } | set(item.report.skipped_units)
        invalid_reported = sorted(reported_units - allowed)
        if invalid_units or invalid_reported:
            detail = invalid_units or invalid_reported
            raise OpenCodeEvidenceExportError(
                f"canonical scope {scope.scope_id!r} has invalid unit membership: "
                + ", ".join(detail)
            )
        reports_by_group.setdefault(scope.group, []).append(item)
    plans: list[_ExportPlan] = []
    seen_scopes: set[str] = set()
    seen_paths: set[Path] = set()

    for contract in contracts:
        metadata = contract.metadata
        scope_id = str(metadata.get("task_id") or "").strip()
        if not scope_id:
            raise OpenCodeEvidenceExportError("legacy Gather contract is missing task_id")
        if scope_id in seen_scopes:
            raise OpenCodeEvidenceExportError(
                f"duplicate legacy Gather contract for scope {scope_id!r}"
            )
        seen_scopes.add(scope_id)

        expected_group = str(metadata.get("group") or "")
        expected_units = tuple(str(unit) for unit in metadata.get("allowed_units") or ())
        if not expected_group or not expected_units:
            raise OpenCodeEvidenceExportError(
                f"legacy Gather contract {scope_id!r} is missing group or allowed_units"
            )
        invalid_expected = sorted(
            unit for unit in expected_units if unit_groups.get(unit) != expected_group
        )
        if invalid_expected:
            raise OpenCodeEvidenceExportError(
                f"legacy Gather contract {scope_id!r} has invalid group membership: "
                + ", ".join(invalid_expected)
            )
        # The legacy source still has to be a valid deterministic fallback for
        # EvidenceValidator. Canonical scopes may use different source chunks.
        _source_path(root, metadata.get("source_ref"))

        allowed_units = set(expected_units)
        evidence_by_key: dict[str, dict[str, Any]] = {}
        gap_reasons: dict[str, set[str]] = {}
        for item in reports_by_group.get(expected_group, []):
            for evidence in item.report.evidence:
                if evidence.unit_name not in allowed_units:
                    continue
                payload = evidence.model_dump(mode="json")
                key = json.dumps(payload, sort_keys=True, separators=(",", ":"))
                evidence_by_key[key] = payload
            for unit, reason in item.report.skipped_units.items():
                if unit in allowed_units:
                    gap_reasons.setdefault(unit, set()).add(reason)

        evidenced_units = {
            payload["unit_name"] for payload in evidence_by_key.values()
        }
        skipped_units: dict[str, str] = {}
        missing: list[str] = []
        for unit in expected_units:
            if unit in evidenced_units:
                continue
            reasons = gap_reasons.get(unit, set())
            if not reasons:
                missing.append(unit)
                continue
            if len(reasons) > 1:
                raise OpenCodeEvidenceExportError(
                    f"canonical Gather v2 has conflicting gap reasons for {unit!r}"
                )
            skipped_units[unit] = next(iter(reasons))
        if missing:
            raise OpenCodeEvidenceExportError(
                f"canonical Gather v2 evidence does not cover legacy contract "
                f"{scope_id!r}: {', '.join(missing)}"
            )

        raw_artifact, raw_path = _artifact_path(
            root,
            contract.output_artifact,
            expected_prefix=("_transient", "opencode", "investigation_raw"),
        )
        receipt_artifact, receipt_path = _artifact_path(
            root,
            metadata.get("validation_receipt"),
            expected_prefix=("_transient", "opencode", "gather_guard"),
        )
        for path in (raw_path, receipt_path):
            if path in seen_paths:
                raise OpenCodeEvidenceExportError(
                    f"multiple legacy Gather contracts target {path.relative_to(root)}"
                )
            seen_paths.add(path)

        plans.append(
            _ExportPlan(
                scope_id=scope_id,
                raw_artifact=raw_artifact,
                raw_path=raw_path,
                receipt_artifact=receipt_artifact,
                receipt_path=receipt_path,
                evidence=tuple(evidence_by_key[key] for key in sorted(evidence_by_key)),
                skipped_units=dict(sorted(skipped_units.items())),
            )
        )

    exports: list[LegacyGatherExport] = []
    for plan in plans:
        _atomic_write_json(
            plan.raw_path,
            {
                "evidence": list(plan.evidence),
                "skipped_units": plan.skipped_units,
            },
        )
        _atomic_write_json(
            plan.receipt_path,
            {
                "task_id": plan.scope_id,
                "evidence_count": len(plan.evidence),
                "validated": True,
            },
        )
        exports.append(
            LegacyGatherExport(
                scope_id=plan.scope_id,
                raw_artifact=plan.raw_artifact,
                validation_receipt=plan.receipt_artifact,
                evidence_count=len(plan.evidence),
            )
        )

    return tuple(exports)


__all__ = [
    "LegacyGatherContract",
    "LegacyGatherExport",
    "OpenCodeEvidenceExportError",
    "export_legacy_gather_artifacts",
]
