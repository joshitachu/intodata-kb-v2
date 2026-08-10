"""Frozen-input preparation for an optional Grounded business-topology role.

This module deliberately has no scanner or business-semantic heuristics.  It
reconstructs a contained technical scan from an immutable experiment snapshot,
stages only accepted Phase 1/2 analyses, and delegates all contract checks to
:mod:`mcp_server.services.business_topology`.

The Grounded runner can use these helpers to add a topology role after its
existing documentation roles without consulting the live scanner directory.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import shutil
from typing import Any, Mapping

from mcp_server.services.business_topology.evidence import (
    BusinessTopologyEvidenceBundle,
    build_business_topology_evidence,
)
from mcp_server.services.business_topology.prompt import business_topology_prompt
from mcp_server.services.business_topology.validation import (
    BusinessTopologyValidationReport,
    parse_and_validate_business_topology,
)
from mcp_server.services.business_topology.models import BusinessTopology


_REQUIRED_SCAN_ARTIFACTS = (
    "scan_result.json",
    "scan/manifest.json",
    "scan/lineage.json",
)
_OPTIONAL_SCAN_ARTIFACTS = ("scan/landscape.json",)
_ALL_SCAN_ARTIFACTS = frozenset(_REQUIRED_SCAN_ARTIFACTS + _OPTIONAL_SCAN_ARTIFACTS)


class GroundedBusinessTopologyError(RuntimeError):
    """The frozen topology packet cannot be safely or completely prepared."""


@dataclass(frozen=True)
class GroundedBusinessTopologyPreparation:
    """Contained factual packet and stable evidence used by one topology role."""

    scan_root: Path
    staging_dir: Path
    packet_root: Path
    prompt_path: Path
    bundle: BusinessTopologyEvidenceBundle


@dataclass(frozen=True)
class GroundedBusinessTopologyResult:
    """Normalized output and deterministic status from one topology-role response."""

    topology: BusinessTopology | None
    report: BusinessTopologyValidationReport
    normalized_json: str | None
    validation_payload: dict[str, Any]
    state_payload: dict[str, Any]


def prepare_grounded_business_topology(
    *,
    project_id: str,
    source_items: tuple[tuple[str, Path], ...],
    accepted_phase1: Mapping[str, Path],
    accepted_phase2: Mapping[str, Path],
    work_root: Path,
    packet_root: Path,
    max_packet_bytes: int,
) -> GroundedBusinessTopologyPreparation:
    """Create a bounded role packet from frozen sources and accepted artifacts.

    ``source_items`` must be the immutable snapshot content supplied to the
    Grounded runner.  This function never reads ``intodoc_output_dir`` or any
    current project scan, so a later scan cannot alter a candidate in flight.
    """

    if not project_id.strip():
        raise GroundedBusinessTopologyError("project_id must not be blank")
    if max_packet_bytes < 1:
        raise GroundedBusinessTopologyError("max_packet_bytes must be positive")
    if not accepted_phase1 or not accepted_phase2:
        raise GroundedBusinessTopologyError(
            "accepted Phase 1 and Phase 2 analyses are both required"
        )

    root = _safe_existing_directory(work_root, "work_root")
    destination_packet = _safe_child(root, packet_root, "packet_root")
    if destination_packet.exists():
        raise GroundedBusinessTopologyError("topology packet root already exists")

    scan_root = root / "frozen_topology_scan"
    staging_dir = root / "business_topology_staging"
    if scan_root.exists() or staging_dir.exists():
        raise GroundedBusinessTopologyError("topology work directories already exist")

    _reconstruct_frozen_scan(source_items, scan_root)
    _stage_accepted_artifacts(
        staging_dir=staging_dir,
        accepted_phase1=accepted_phase1,
        accepted_phase2=accepted_phase2,
    )
    bundle = build_business_topology_evidence(
        project_id=project_id,
        scan_output_dir=scan_root,
        staging_dir=staging_dir,
    )
    _materialize_packet(
        bundle=bundle,
        staging_dir=staging_dir,
        packet_root=destination_packet,
        project_id=project_id,
        max_packet_bytes=max_packet_bytes,
    )
    return GroundedBusinessTopologyPreparation(
        scan_root=scan_root,
        staging_dir=staging_dir,
        packet_root=destination_packet,
        prompt_path=destination_packet / "topology" / "TOPOLOGY_TASK.md",
        bundle=bundle,
    )


def validate_grounded_business_topology(
    text: str,
    *,
    project_id: str,
    preparation: GroundedBusinessTopologyPreparation,
) -> GroundedBusinessTopologyResult:
    """Parse, validate, and normalize a role response through the shared contract."""

    topology, report = parse_and_validate_business_topology(
        text,
        bundle=preparation.bundle,
        project_id=project_id,
    )
    normalized = topology.model_dump_json(indent=2) if topology is not None and report.validated else None
    validation_payload = report.model_dump(mode="json")
    state_payload: dict[str, Any] = {
        "available": normalized is not None,
        "validated": report.validated,
        "scan_fingerprint": preparation.bundle.scan_fingerprint,
        "errors": list(report.errors),
        "warnings": list(report.warnings),
        "source": "grounded_candidate",
    }
    return GroundedBusinessTopologyResult(
        topology=topology if report.validated else None,
        report=report,
        normalized_json=normalized,
        validation_payload=validation_payload,
        state_payload=state_payload,
    )


def _reconstruct_frozen_scan(
    source_items: tuple[tuple[str, Path], ...],
    destination: Path,
) -> None:
    """Copy exactly one approved snapshot item for each required scanner file."""

    selected: dict[str, Path] = {}
    for logical_identity, source in source_items:
        relative = _safe_snapshot_relative(logical_identity)
        if relative not in _ALL_SCAN_ARTIFACTS:
            continue
        if relative in selected:
            raise GroundedBusinessTopologyError(
                f"frozen snapshot has duplicate scanner artifact: {relative}"
            )
        _require_regular_file(source, f"frozen scanner artifact {relative}")
        _require_json_object(source, f"frozen scanner artifact {relative}")
        selected[relative] = source

    missing = [relative for relative in _REQUIRED_SCAN_ARTIFACTS if relative not in selected]
    if missing:
        raise GroundedBusinessTopologyError(
            "frozen snapshot is missing required scanner artifacts: " + ", ".join(missing)
        )

    destination.mkdir(parents=True, exist_ok=False)
    for relative in sorted(selected):
        target = _safe_child(destination, Path(relative), "scanner artifact target")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(selected[relative], target)


def _stage_accepted_artifacts(
    *,
    staging_dir: Path,
    accepted_phase1: Mapping[str, Path],
    accepted_phase2: Mapping[str, Path],
) -> None:
    """Stage accepted Phase 1 JSON and Phase 2 Markdown for synthesis.

    Candidate paths can be nested; each safe path is made flat and stable
    because the shared evidence builder is intentionally non-recursive. Final
    writer documents are excluded so topology cannot inherit an unvalidated
    restatement of the Phase 1/2 implementation analysis.
    """

    staging_dir.mkdir(parents=True, exist_ok=False)
    phase1_root = staging_dir / "_phase1"
    phase1_root.mkdir()
    analysis_root = staging_dir / "_analysis"
    analysis_root.mkdir()

    used_phase1: set[str] = set()
    for logical_name, source in sorted(accepted_phase1.items()):
        relative = _accepted_relative(
            logical_name,
            "candidate/orchestration/evidence_packets/",
        )
        if relative.suffix.casefold() != ".json":
            raise GroundedBusinessTopologyError("accepted Phase 1 analysis must be JSON")
        staged_name = "__".join(relative.parts)
        if staged_name in used_phase1:
            raise GroundedBusinessTopologyError("duplicate accepted Phase 1 analysis name")
        used_phase1.add(staged_name)
        _copy_accepted(source, phase1_root / staged_name, "accepted Phase 1 analysis")

    used_analyses: set[str] = set()
    for logical_name, source in sorted(accepted_phase2.items()):
        relative = _accepted_relative(logical_name, "candidate/intermediates/")
        if relative.suffix.casefold() != ".md":
            raise GroundedBusinessTopologyError("accepted Phase 2 analysis must be Markdown")
        staged_name = "__".join(relative.parts)
        if staged_name in used_analyses:
            raise GroundedBusinessTopologyError("duplicate accepted analysis name")
        used_analyses.add(staged_name)
        _copy_accepted(source, analysis_root / staged_name, "accepted Phase 2 analysis")


def _materialize_packet(
    *,
    bundle: BusinessTopologyEvidenceBundle,
    staging_dir: Path,
    packet_root: Path,
    project_id: str,
    max_packet_bytes: int,
) -> None:
    """Copy scanner evidence plus accepted Phase 1/2 analyses into one packet."""

    files = tuple(
        path
        for path in sorted(bundle.root.rglob("*"), key=lambda item: item.as_posix())
        if path.is_file() and not path.is_symlink()
    )
    topology_root = packet_root / "topology"
    topology_root.mkdir(parents=True, exist_ok=False)
    try:
        for source in files:
            relative = source.relative_to(bundle.root)
            target = _safe_child(topology_root, relative, "topology packet target")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)

        _copy_packet_markdown(
            source_root=staging_dir / "_analysis",
            pattern="*.md",
            destination_root=topology_root / "analyses" / "phase2",
        )
        _copy_packet_files(
            source_root=staging_dir / "_phase1",
            pattern="*.json",
            destination_root=topology_root / "analyses" / "phase1",
        )
        _rewrite_supporting_artifact_locators(topology_root)

        manifest_path = topology_root / "packet_manifest.json"
        task_path = topology_root / "TOPOLOGY_TASK.md"
        role_prompt_path = packet_root / "ROLE_PROMPT.txt"
        task_path.write_text(
            _topology_task_prompt(project_id=project_id, scan_fingerprint=bundle.scan_fingerprint),
            encoding="utf-8",
        )
        role_prompt_path.write_text(
            "Read `topology/TOPOLOGY_TASK.md` and the contained `topology/` packet only. "
            "Write exactly `candidate/topology/business_topology.json` relative to your role workspace.",
            encoding="utf-8",
        )

        # Packet size includes every final file, not merely the scanner ledger.
        # Iterate because the rendered byte count is itself recorded in the manifest.
        for _ in range(3):
            manifest = {
                "schema_version": "grounded-business-topology-packet-v1",
                "project_id": project_id,
                "scan_fingerprint": bundle.scan_fingerprint,
                "packet_bytes": _packet_size(packet_root),
                "evidence_count": len(bundle.evidence_ids),
                "artifact_count": len(bundle.artifact_ids),
                "phase1_analysis_count": len(
                    tuple((topology_root / "analyses" / "phase1").glob("*.json"))
                ),
                "phase2_analysis_count": len(
                    tuple((topology_root / "analyses" / "phase2").glob("*.md"))
                ),
                "source": "immutable_experiment_snapshot",
                "input_contract": "accepted_phase1_phase2_plus_scanner_evidence",
                "output_contract": "candidate/topology/business_topology.json",
                "semantic_derivations": [],
            }
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
            )
        final_bytes = _packet_size(packet_root)
        if final_bytes > max_packet_bytes:
            raise GroundedBusinessTopologyError(
                f"business topology packet exceeds byte budget ({final_bytes} > {max_packet_bytes})"
            )
    except Exception:
        # This directory was created by this call only after strict containment
        # checks. Removing it prevents a future retry from seeing a partial
        # packet as complete.
        shutil.rmtree(packet_root)
        raise


def _topology_task_prompt(*, project_id: str, scan_fingerprint: str) -> str:
    base = business_topology_prompt(
        project_id=project_id,
        scan_fingerprint=scan_fingerprint,
    )
    base = base.replace("scoped evidence tools", "contained evidence files")
    base = base.replace("_topology/", "topology/")
    return (
        base
        + "\n\nCONTAINED GROUNDING PACKET\n"
        + "- Read `topology/_doc_pack_business_topology.md` first.\n"
        + "- Read accepted Phase 1 evidence analyses in `topology/analyses/phase1/`.\n"
        + "- Read accepted Phase 2 analyses in `topology/analyses/phase2/`.\n"
        + "- Read exact scanner facts only from `topology/evidence/`.\n"
        + "- `topology/factual_evidence_index.json` describes coverage and stable IDs.\n"
        + "- Do not read files outside this packet or infer semantics in Python.\n"
        + "- Write the JSON object to `candidate/topology/business_topology.json`.\n"
    )


def _safe_snapshot_relative(logical_identity: str) -> str:
    """Return the source-snapshot relative path, rejecting path escapes."""

    if not isinstance(logical_identity, str) or not logical_identity.strip():
        raise GroundedBusinessTopologyError("frozen source logical identity is invalid")
    descriptor, separator, raw_relative = logical_identity.partition(":")
    if not separator or not descriptor or not raw_relative:
        raise GroundedBusinessTopologyError("frozen source logical identity must contain descriptor:path")
    if "\\" in raw_relative or "\x00" in raw_relative:
        raise GroundedBusinessTopologyError("frozen source logical path is unsafe")
    relative = PurePosixPath(raw_relative)
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise GroundedBusinessTopologyError("frozen source logical path is unsafe")
    return relative.as_posix()


def _accepted_relative(logical_name: str, prefix: str) -> PurePosixPath:
    if not isinstance(logical_name, str) or not logical_name.startswith(prefix):
        raise GroundedBusinessTopologyError("accepted artifact is outside its declared candidate root")
    raw = logical_name.removeprefix(prefix)
    if "\\" in raw or "\x00" in raw:
        raise GroundedBusinessTopologyError("accepted artifact path is unsafe")
    relative = PurePosixPath(raw)
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise GroundedBusinessTopologyError("accepted artifact path is unsafe")
    return relative


def _safe_existing_directory(path: Path, field: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise GroundedBusinessTopologyError(f"{field} is not an existing directory") from exc
    if not resolved.is_dir() or resolved.is_symlink():
        raise GroundedBusinessTopologyError(f"{field} is not an existing directory")
    return resolved


def _safe_child(root: Path, child: Path, field: str) -> Path:
    if child.is_absolute() or any(part in {"", ".", ".."} for part in child.parts):
        raise GroundedBusinessTopologyError(f"{field} is unsafe")
    candidate = root.joinpath(*child.parts)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise GroundedBusinessTopologyError(f"{field} escapes its root") from exc
    return candidate


def _require_regular_file(path: Path, field: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise GroundedBusinessTopologyError(f"{field} is not a regular file")


def _require_json_object(path: Path, field: str) -> None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GroundedBusinessTopologyError(f"{field} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise GroundedBusinessTopologyError(f"{field} must be a JSON object")


def _copy_accepted(source: Path, target: Path, field: str) -> None:
    _require_regular_file(source, field)
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GroundedBusinessTopologyError(f"{field} is unreadable") from exc
    if not text.strip():
        raise GroundedBusinessTopologyError(f"{field} is empty")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _copy_packet_markdown(*, source_root: Path, pattern: str, destination_root: Path) -> None:
    _copy_packet_files(
        source_root=source_root,
        pattern=pattern,
        destination_root=destination_root,
    )


def _copy_packet_files(*, source_root: Path, pattern: str, destination_root: Path) -> None:
    destination_root.mkdir(parents=True, exist_ok=True)
    if not source_root.is_dir() or source_root.is_symlink():
        raise GroundedBusinessTopologyError("staged topology source directory is unsafe")
    for source in sorted(source_root.glob(pattern), key=lambda item: item.name):
        _require_regular_file(source, "staged topology analysis")
        shutil.copyfile(source, destination_root / source.name)


def _rewrite_supporting_artifact_locators(topology_root: Path) -> None:
    """Point the copied evidence ledger at its packet-relative locations."""

    ledger = topology_root / "evidence" / "supporting_artifacts.jsonl"
    rows: list[dict[str, Any]] = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        path = str(row.get("path") or "")
        if path.startswith("_phase1/"):
            row["path"] = "topology/analyses/phase1/" + path.removeprefix("_phase1/")
        elif path.startswith("_analysis/"):
            row["path"] = "topology/analyses/phase2/" + path.removeprefix("_analysis/")
        rows.append(row)
    ledger.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    overview_path = topology_root / "factual_evidence_index.json"
    overview = json.loads(overview_path.read_text(encoding="utf-8"))
    evidence_files = overview.get("evidence_files") or {}
    if isinstance(evidence_files, dict):
        overview["evidence_files"] = {
            key: str(value).replace("_topology/", "topology/", 1)
            for key, value in evidence_files.items()
        }
    overview_path.write_text(
        json.dumps(overview, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    starter_path = topology_root / "_doc_pack_business_topology.md"
    starter = starter_path.read_text(encoding="utf-8")
    starter = starter.replace("_topology/", "topology/")
    starter = starter.replace("_phase1/", "topology/analyses/phase1/")
    starter = starter.replace("_analysis/", "topology/analyses/phase2/")
    starter_path.write_text(starter, encoding="utf-8")


def _packet_size(root: Path) -> int:
    return sum(
        path.stat().st_size
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
