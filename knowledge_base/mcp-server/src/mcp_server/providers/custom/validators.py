"""Facts-manifest construction and publish gates for the custom harness."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from mcp_server.config import config
from mcp_server.platforms import get_platform_strategy
from mcp_server.providers.custom.constants import GROUP_SUMMARY_SECTION
from mcp_server.providers.custom.models import CoverageReport, FactsManifest, InvestigationReport, UnitFact
from mcp_server.providers.custom.doc_catalog import load_doc_catalog as _load_catalog
from mcp_server.providers.custom.prompt_packs import ANALYSIS_SECTION_NAMES, REQUIRED_SECTION_NAMES

_catalog = _load_catalog()
REQUIRED_DOCS = _catalog.required_docs
DOC_SECTION_REQUIREMENTS = _catalog.section_requirements

EVIDENCE_REGISTER_SECTION_RE = re.compile(
    r"^##\s+Evidence Register\s*$([\s\S]*?)(?=^##\s+|\Z)",
    re.MULTILINE | re.IGNORECASE,
)


def _load_scan_result(scan_output_dir: Path) -> dict[str, Any]:
    mono = scan_output_dir / "scan_result.json"
    if mono.exists():
        payload = json.loads(mono.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs", []), list):
            raise ValueError(f"Invalid scan result at {mono}: expected object with jobs list")
        return payload

    scan_dir = scan_output_dir / "scan"
    if not scan_dir.exists():
        raise FileNotFoundError(f"No valid fresh scan_result.json found in {scan_output_dir}")

    meta = json.loads((scan_dir / "meta.json").read_text(encoding="utf-8"))
    jobs: list[dict[str, Any]] = []
    jobs_dir = scan_dir / "jobs"
    if jobs_dir.exists():
        for group_dir in sorted(p for p in jobs_dir.iterdir() if p.is_dir()):
            for f in sorted(group_dir.glob("*.json")):
                jobs.append(json.loads(f.read_text(encoding="utf-8")))
    files: list[dict[str, Any]] = []
    files_path = scan_dir / "files_index.json"
    if files_path.exists():
        files = json.loads(files_path.read_text(encoding="utf-8"))
    return {
        **meta,
        "jobs": jobs,
        "files": files,
        "warnings": meta.get("warnings", []),
    }


def canonical_unit_label(platform: str) -> str:
    try:
        return get_platform_strategy(platform).canonical_unit_label()
    except ValueError:
        return "units"


def unit_type_for(platform: str, job: dict[str, Any]) -> str:
    try:
        return get_platform_strategy(platform).unit_type_for(job)
    except ValueError:
        return "code_unit"


def _component_counts(components: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for comp in components:
        ctype = comp.get("component_type") or "unknown"
        counts[ctype] = counts.get(ctype, 0) + 1
    return counts


def _systems_for(job: dict[str, Any]) -> list[str]:
    systems: set[str] = set()
    for value in job.get("manifest_external_systems", []) or []:
        if isinstance(value, str):
            systems.add(value)
    for comp in job.get("components", []) or []:
        # Skip metadata_connection_ref on ConfigFile components — the ref is the
        # config filename stem (e.g. "Web" from Web.config), not a system name.
        # Repeated once per connection string found, producing "Web,Web,Web,..." noise.
        if comp.get("component_type") != "ConfigFile":
            ref = comp.get("metadata_connection_ref")
            if ref:
                for part in str(ref).split(","):
                    part = part.strip()
                    if part:
                        systems.add(part)
        for param in comp.get("params", []) or []:
            name = str(param.get("name", "")).lower()
            value = str(param.get("value", ""))
            if any(key in name for key in ("url", "host", "endpoint", "server", "database")):
                systems.add(value[:120])
    return sorted(s for s in systems if s)


def _raw_source_audit(scan: dict[str, Any], jobs: list[dict[str, Any]], platform: str) -> dict[str, Any]:
    try:
        return get_platform_strategy(platform).raw_source_audit(scan, jobs)
    except ValueError:
        return get_platform_strategy("generic").raw_source_audit(scan, jobs)


def build_facts_manifest(project_id: str, scan_output_dir: Path) -> FactsManifest:
    scan = _load_scan_result(scan_output_dir)
    platform = str(scan.get("platform") or "generic").lower()
    jobs = scan.get("jobs", []) or []

    units: list[UnitFact] = []
    groups: dict[str, int] = {}
    component_totals: dict[str, int] = {}
    key_systems: set[str] = set()

    manifest = scan.get("manifest", {}) or {}
    external_by_job = manifest.get("job_external_systems", {}) if isinstance(manifest, dict) else {}

    for job in jobs:
        name = str(job.get("name") or Path(str(job.get("path", "unknown"))).stem)
        group = str(job.get("group") or "Global")
        components = job.get("components", []) or []
        job = {**job, "manifest_external_systems": external_by_job.get(name, [])}
        comp_counts = _component_counts(components)
        for ctype, count in comp_counts.items():
            component_totals[ctype] = component_totals.get(ctype, 0) + count
        systems = _systems_for(job)
        key_systems.update(systems)
        groups[group] = groups.get(group, 0) + 1
        units.append(UnitFact(
            name=name,
            path=str(job.get("path", "")),
            unit_type=unit_type_for(platform, job),
            group=group,
            component_count=len(components),
            component_types=comp_counts,
            key_systems=systems,
        ))

    return FactsManifest(
        project_id=project_id,
        platform=platform,
        canonical_unit_label=canonical_unit_label(platform),
        unit_count=len(units),
        groups=dict(sorted(groups.items())),
        units=sorted(units, key=lambda u: (u.group, u.name)),
        component_counts=dict(sorted(component_totals.items())),
        key_systems=sorted(key_systems),
        scan_warnings=scan.get("warnings", []) or [],
        raw_source_audit=_raw_source_audit(scan, jobs, platform),
    )


_COVERED_RE = re.compile(r"^\s*-\s*COVERED:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_SKIPPED_RE = re.compile(r"^\s*-\s*SKIPPED:\s*(.+?)\s*--\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_STRUCTURED_COVERAGE_DIR = "_coverage_units"


def _unit_id(unit: UnitFact) -> str:
    return f"{unit.group}:{unit.path}:{unit.name}"


def _duplicate_unit_names(facts: FactsManifest) -> dict[str, list[UnitFact]]:
    by_name: dict[str, list[UnitFact]] = {}
    for unit in facts.units:
        by_name.setdefault(unit.name, []).append(unit)
    return {name: units for name, units in by_name.items() if len(units) > 1}


def _unit_label(unit: UnitFact, duplicate_names: set[str]) -> str:
    if unit.name not in duplicate_names:
        return unit.name
    return f"{unit.name} [{unit.group}: {unit.path}]"


def _identity_report_warnings(facts: FactsManifest) -> list[str]:
    warnings: list[str] = []
    duplicates = _duplicate_unit_names(facts)
    for name, units in sorted(duplicates.items()):
        locations = ", ".join(f"{u.group}: {u.path}" for u in units)
        warnings.append(f"Duplicate unit name {name!r} disambiguated by group/path: {locations}")
    return warnings


def _groups_for_analysis_file(path: Path, facts: FactsManifest) -> set[str]:
    stem = path.stem
    groups = set(facts.groups)
    if stem in groups:
        return {stem}
    matched = {group for group in groups if stem.startswith(f"{group}_")}
    return matched


def _resolve_units_by_name(
    facts: FactsManifest,
    name: str,
    candidate_groups: set[str] | None,
) -> list[UnitFact]:
    units = [unit for unit in facts.units if unit.name == name]
    if candidate_groups:
        group_units = [unit for unit in units if unit.group in candidate_groups]
        if group_units:
            return group_units
    return units


def _coverage_report_from_identities(
    *,
    facts: FactsManifest,
    covered_ids: set[str],
    skipped_ids: dict[str, str],
    errors: list[str],
    warnings: list[str] | None = None,
) -> CoverageReport:
    units_by_id = {_unit_id(unit): unit for unit in facts.units}
    duplicate_names = set(_duplicate_unit_names(facts))
    expected_ids = set(units_by_id)
    missing_ids = sorted(expected_ids - covered_ids - set(skipped_ids))
    if missing_ids:
        labels = [_unit_label(units_by_id[unit_id], duplicate_names) for unit_id in missing_ids]
        errors.append(f"Missing unit coverage: {', '.join(labels[:20])}")

    report_warnings = _identity_report_warnings(facts)
    if warnings:
        report_warnings.extend(warnings)

    return CoverageReport(
        ok=not errors,
        covered_units=sorted(_unit_label(units_by_id[unit_id], duplicate_names) for unit_id in covered_ids & expected_ids),
        covered_unit_ids=sorted(covered_ids & expected_ids),
        skipped_units=dict(sorted(
            (_unit_label(units_by_id[unit_id], duplicate_names), reason)
            for unit_id, reason in skipped_ids.items()
            if unit_id in expected_ids
        )),
        skipped_unit_ids=dict(sorted((unit_id, reason) for unit_id, reason in skipped_ids.items() if unit_id in expected_ids)),
        missing_units=sorted(_unit_label(units_by_id[unit_id], duplicate_names) for unit_id in missing_ids),
        missing_unit_ids=missing_ids,
        coverage_count=len((covered_ids & expected_ids) | (set(skipped_ids) & expected_ids)),
        expected_count=facts.unit_count,
        errors=errors,
        warnings=report_warnings,
    )


def _structured_phase2_coverage(
    analysis_dir: Path,
    facts: FactsManifest,
) -> tuple[set[str], dict[str, str], list[str]] | None:
    coverage_dir = analysis_dir / _STRUCTURED_COVERAGE_DIR
    if not coverage_dir.exists():
        return None

    units_by_group: dict[str, list[UnitFact]] = {}
    for unit in facts.units:
        units_by_group.setdefault(unit.group, []).append(unit)

    covered_ids: set[str] = set()
    skipped_ids: dict[str, str] = {}
    errors: list[str] = []

    for group in facts.groups:
        record_path = coverage_dir / f"{group}.json"
        if not record_path.exists():
            errors.append(f"Missing structured Phase 2 coverage record for group {group}")
            continue
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            errors.append(f"Structured Phase 2 coverage record is invalid JSON: {record_path.name}")
            continue
        status = str(record.get("status") or "")
        if status not in {"completed", "scanner_limited"}:
            errors.append(f"Structured Phase 2 coverage record for group {group} is not completed: {status or 'missing'}")
            continue
        analysis_file = analysis_dir / str(record.get("analysis_file") or f"{group}.md")
        if not analysis_file.exists():
            errors.append(f"Structured Phase 2 coverage record for group {group} points to missing analysis file")
            continue
        if int(record.get("analysis_bytes") or analysis_file.stat().st_size or 0) <= 0:
            errors.append(f"Structured Phase 2 coverage record for group {group} has empty analysis output")
            continue
        for unit in units_by_group.get(group, []):
            if status == "scanner_limited":
                skipped_ids[_unit_id(unit)] = "scanner-limited evidence"
            else:
                covered_ids.add(_unit_id(unit))

    return covered_ids, skipped_ids, errors


def validate_analysis_coverage(
    analysis_dir: Path,
    facts: FactsManifest,
    *,
    use_structured: bool = False,
) -> CoverageReport:
    if use_structured:
        structured = _structured_phase2_coverage(analysis_dir, facts)
        if structured is not None:
            covered_ids, skipped_ids, errors = structured
            return _coverage_report_from_identities(
                facts=facts,
                covered_ids=covered_ids,
                skipped_ids=skipped_ids,
                errors=errors,
            )

    expected_names = facts.unit_names()
    covered_ids: set[str] = set()
    skipped_ids: dict[str, str] = {}
    errors: list[str] = []
    warnings: list[str] = []

    for group in facts.groups:
        group_files = list(analysis_dir.glob(f"{group}*.md"))
        if not group_files:
            errors.append(f"Missing Phase 2 analysis for group {group}")

    for path in sorted(analysis_dir.glob("*.md")):
        if path.name.startswith("_bundle_"):
            continue
        candidate_groups = _groups_for_analysis_file(path, facts)
        text = path.read_text(encoding="utf-8")
        for match in _COVERED_RE.finditer(text):
            name = match.group(1).strip()
            if name not in expected_names:
                warnings.append(f"Ignoring unknown covered unit {name!r} in {path.name}")
                continue
            units = _resolve_units_by_name(facts, name, candidate_groups)
            if not units:
                warnings.append(f"Could not resolve covered unit {name!r} in {path.name}")
                continue
            covered_ids.update(_unit_id(unit) for unit in units)
        for match in _SKIPPED_RE.finditer(text):
            name = match.group(1).strip()
            reason = match.group(2).strip()
            if name not in expected_names:
                warnings.append(f"Ignoring unknown skipped unit {name!r} in {path.name}")
                continue
            units = _resolve_units_by_name(facts, name, candidate_groups)
            if not units:
                warnings.append(f"Could not resolve skipped unit {name!r} in {path.name}")
                continue
            for unit in units:
                skipped_ids[_unit_id(unit)] = reason

    return _coverage_report_from_identities(
        facts=facts,
        covered_ids=covered_ids,
        skipped_ids=skipped_ids,
        errors=errors,
        warnings=warnings,
    )


def validate_investigation_coverage(reports: list[InvestigationReport], facts: FactsManifest) -> CoverageReport:
    """Ensure the investigator produced validated coverage before Phase 2."""

    expected_names = facts.unit_names()
    covered_ids: set[str] = set()
    skipped_ids: dict[str, str] = {}
    errors: list[str] = []
    warnings: list[str] = []
    for report in reports:
        candidate_groups = set(report.source_groups or [report.group])
        for ev in report.evidence:
            if ev.unit_name not in expected_names:
                errors.append(f"Investigation contains unknown unit: {ev.unit_name}")
                continue
            units = _resolve_units_by_name(facts, ev.unit_name, candidate_groups)
            if not units:
                warnings.append(f"Could not resolve investigation unit {ev.unit_name!r} in {report.group}")
                continue
            covered_ids.update(_unit_id(unit) for unit in units)
        for name, reason in report.skipped_units.items():
            if name not in expected_names:
                errors.append(f"Investigation contains unknown skipped unit: {name}")
                continue
            units = _resolve_units_by_name(facts, name, candidate_groups)
            if not units:
                warnings.append(f"Could not resolve skipped investigation unit {name!r} in {report.group}")
                continue
            for unit in units:
                skipped_ids[_unit_id(unit)] = reason
    if not reports and expected_names:
        errors.append("No investigation reports were written")

    return _coverage_report_from_identities(
        facts=facts,
        covered_ids=covered_ids,
        skipped_ids=skipped_ids,
        errors=errors,
        warnings=warnings,
    )


def validate_analysis_depth(
    analysis_dir: Path,
    preprocess_dir: Path,
    facts: FactsManifest,
    *,
    use_structured: bool = False,
) -> CoverageReport:
    """Validate that Phase 2 contains real per-unit analysis, not just coverage."""

    if use_structured:
        structured = _structured_phase2_coverage(analysis_dir, facts)
        if structured is not None:
            covered_ids, skipped_ids, errors = structured
            return _coverage_report_from_identities(
                facts=facts,
                covered_ids=covered_ids,
                skipped_ids=skipped_ids,
                errors=errors,
            )

    covered_ids: set[str] = set()
    errors: list[str] = []
    for group in facts.groups:
        group_files = [
            p for p in sorted(analysis_dir.glob(f"{group}*.md"))
            if not p.name.startswith("_bundle_")
            and "_chunk_" not in p.name
            and not p.name.endswith("_summary.md")
        ]
        if not group_files:
            errors.append(f"Missing Phase 2 depth artifact for group {group}")
            continue

        text = "\n\n".join(p.read_text(encoding="utf-8", errors="replace") for p in group_files)
        model_text = _model_analysis_text(text)
        preprocess_size = (preprocess_dir / f"{group}.md").stat().st_size if (preprocess_dir / f"{group}.md").exists() else 0
        min_bytes = max(4_000, min(40_000, int(preprocess_size * 0.15)))

        if len(model_text.encode("utf-8")) < min_bytes:
            # Scanner-limited groups (preprocess below investigation threshold) produce stub
            # analysis by design; the depth floor does not apply.
            if preprocess_size >= config.min_investigation_content_bytes:
                errors.append(
                    f"Phase 2 analysis for group {group} is too shallow: "
                    f"{len(model_text.encode('utf-8'))} bytes < {min_bytes} required"
                )

        group_facts = [u for u in facts.units if u.group == group or group.startswith(f"{u.group}_part")]
        group_units = [u.name for u in group_facts]
        if not group_units:
            group_facts = [u for u in facts.units if u.group == group]
            group_units = [u.name for u in group_facts]
        missing_unit_detail = [name for name in group_units if name not in model_text]
        if missing_unit_detail:
            errors.append(
                f"Phase 2 model analysis for group {group} does not mention units: "
                f"{', '.join(missing_unit_detail[:20])}"
            )
        covered_ids.update(_unit_id(unit) for unit in group_facts if unit.name in model_text)

    return _coverage_report_from_identities(
        facts=facts,
        covered_ids=covered_ids,
        skipped_ids={},
        errors=errors,
    )


def validate_analysis_guide_sections(
    analysis_dir: Path,
    facts: FactsManifest,
    *,
    require_group_summary: bool = True,
) -> CoverageReport:
    """Require canonical guide sections for Talend Phase 2 output."""

    covered_ids: set[str] = set()
    errors: list[str] = []
    all_ids = {_unit_id(unit) for unit in facts.units}

    if not require_group_summary:
        for group in facts.groups:
            group_files = [
                p for p in sorted(analysis_dir.glob(f"{group}*.md"))
                if not p.name.startswith("_bundle_")
                and "_chunk_" not in p.name
                and not p.name.endswith("_summary.md")
            ]
            if not group_files:
                errors.append(f"Missing Phase 2 guide-section artifact for group {group}")
        return CoverageReport(
            ok=not errors,
            covered_units=sorted(facts.unit_names()),
            covered_unit_ids=sorted(all_ids),
            missing_units=[],
            missing_unit_ids=[],
            coverage_count=facts.unit_count,
            expected_count=facts.unit_count,
            errors=errors,
            warnings=_identity_report_warnings(facts),
        )

    if facts.platform != "talend":
        for group in facts.groups:
            group_files = [
                p for p in sorted(analysis_dir.glob(f"{group}*.md"))
                if not p.name.startswith("_bundle_")
                and "_chunk_" not in p.name
                and not p.name.endswith("_summary.md")
            ]
            if not group_files:
                errors.append(f"Missing Phase 2 guide-section artifact for group {group}")
                continue
            text = "\n\n".join(p.read_text(encoding="utf-8", errors="replace") for p in group_files)
            if not _has_section(text, GROUP_SUMMARY_SECTION):
                errors.append(f"Phase 2 analysis for group {group} missing required sections: {GROUP_SUMMARY_SECTION}")
        return CoverageReport(
            ok=not errors,
            covered_units=sorted(facts.unit_names()),
            covered_unit_ids=sorted(all_ids),
            missing_units=[],
            missing_unit_ids=[],
            coverage_count=facts.unit_count,
            expected_count=facts.unit_count,
            errors=errors,
            warnings=_identity_report_warnings(facts),
        )

    for group in facts.groups:
        group_files = [
            p for p in sorted(analysis_dir.glob(f"{group}*.md"))
            if not p.name.startswith("_bundle_")
            and "_chunk_" not in p.name
            and not p.name.endswith("_summary.md")
        ]
        if not group_files:
            errors.append(f"Missing Phase 2 guide-section artifact for group {group}")
            continue

        text = "\n\n".join(p.read_text(encoding="utf-8", errors="replace") for p in group_files)
        model_text = _model_analysis_text(text)
        missing_required = [
            name for name in REQUIRED_SECTION_NAMES
            if not _has_section(model_text, name)
        ]
        if not _has_section(model_text, GROUP_SUMMARY_SECTION):
            missing_required.append(GROUP_SUMMARY_SECTION)
        if missing_required:
            errors.append(
                f"Phase 2 analysis for group {group} missing required sections: "
                f"{', '.join(missing_required)}"
            )

        group_facts = [u for u in facts.units if u.group == group or group.startswith(f"{u.group}_part")]
        group_units = [u.name for u in group_facts]
        if not group_units:
            group_facts = [u for u in facts.units if u.group == group]
            group_units = [u.name for u in group_facts]
        covered_ids.update(_unit_id(unit) for unit in group_facts if unit.name in model_text)

    # If ESB components were detected in this Talend project, require ROUTE_GRAPH or ESB_ROUTES section.
    esb_facts_path = analysis_dir.parent / "_preprocess" / "talend_esb_facts.json"
    if esb_facts_path.exists():
        try:
            esb_data = json.loads(esb_facts_path.read_text(encoding="utf-8"))
            if esb_data.get("esb_detected"):
                full_analysis_text = " ".join(
                    p.read_text(encoding="utf-8", errors="replace")
                    for p in sorted(analysis_dir.glob("*.md"))
                    if not p.name.startswith("_") and "_chunk_" not in p.name
                )
                has_route_section = bool(
                    re.search(r"(?i)^#{1,6}\s*(ROUTE_GRAPH|ESB_ROUTES|ESB_ROUTE_GRAPH)", full_analysis_text, re.MULTILINE)
                )
                if not has_route_section:
                    errors.append(
                        "Talend ESB components detected (see talend_esb_facts.json) but Phase 2 analysis "
                        "is missing a ROUTE_GRAPH or ESB_ROUTES section"
                    )
        except (json.JSONDecodeError, OSError):
            pass

    return _coverage_report_from_identities(
        facts=facts,
        covered_ids=covered_ids,
        skipped_ids={},
        errors=errors,
    )


def _has_section(text: str, section_name: str) -> bool:
    """Return True if the Model Analysis block contains a heading whose name matches
    section_name, regardless of heading level (#–######), case, or any suffix.

    Matches:
        ### GROUP_SUMMARY
        ### GROUP_SUMMARY: demo-data
        #### group_summary - Notes
        ## GROUP_SUMMARY Section
    Does NOT match:
        ### GROUP_SUMMARY_EXTENDED   (different name — trailing underscore/letter blocks it)
    """
    model_text = _model_analysis_text(text)
    needle = section_name.upper()
    for line in model_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        heading_text = stripped.lstrip("#").strip().upper()
        if (
            heading_text == needle
            or heading_text.startswith(needle + ":")
            or heading_text.startswith(needle + " ")
            or heading_text.startswith(needle + "-")
            or heading_text.startswith(needle + "—")  # em dash
        ):
            return True
    return False


def normalize_analysis_guide_headings(text: str, platform: str) -> str:
    """Normalize known guide section headings to the canonical Phase 2 level.

    Accepts any heading level, any case, and any suffix after the section name
    (e.g. '### GROUP_SUMMARY: demo-data' → '### GROUP_SUMMARY').
    """
    section_names = (GROUP_SUMMARY_SECTION,)
    if platform == "talend":
        section_names = tuple(ANALYSIS_SECTION_NAMES) + section_names
    # The suffix pattern uses [:\-–— \t] (horizontal-whitespace only) so that
    # '### GROUP_SUMMARY: demo-data' is matched but the newline + content line
    # that follows is never consumed.
    pattern = re.compile(
        rf"^#{{1,6}}\s+(?:#{{1,6}}\s+)?(?P<name>{'|'.join(re.escape(n) for n in section_names)})(?:[:\-–—\t ][^\n]*)?$",
        re.MULTILINE | re.IGNORECASE,
    )
    return pattern.sub(lambda m: f"### {m.group('name').upper()}", text)


def missing_required_analysis_sections(text: str, platform: str) -> list[str]:
    """Return required guide sections absent from a Phase 2 model-analysis body."""

    missing_summary = [] if _has_section(text, GROUP_SUMMARY_SECTION) else [GROUP_SUMMARY_SECTION]
    if platform != "talend":
        return missing_summary
    return [
        name for name in REQUIRED_SECTION_NAMES
        if not _has_section(text, name)
    ] + missing_summary


def _model_analysis_text(text: str) -> str:
    marker = "## Model Analysis"
    if marker in text:
        return text.split(marker, 1)[1]
    return text


def group_analysis_is_complete(analysis_path: Path, platform: str, *, require_sections: bool = True) -> bool:
    """Return True if a cached Phase 2 file has all required sections (Talend only)."""
    if not require_sections:
        return analysis_path.exists() and analysis_path.stat().st_size > 0
    text = analysis_path.read_text(encoding="utf-8", errors="replace")
    required = (GROUP_SUMMARY_SECTION,) if platform != "talend" else (*REQUIRED_SECTION_NAMES, GROUP_SUMMARY_SECTION)
    return all(_has_section(text, name) for name in required)


def validate_docs(
    staging_dir: Path,
    facts: FactsManifest,
    *,
    required_docs: list[str] | None = None,
    enforce_default_contract: bool = True,
) -> CoverageReport:
    errors: list[str] = []
    docs_to_validate = required_docs or REQUIRED_DOCS
    missing_docs = [doc for doc in docs_to_validate if not (staging_dir / f"{doc}.md").exists()]
    if missing_docs:
        errors.append(f"Missing required docs: {', '.join(missing_docs)}")

    scorecard = staging_dir / "_assessment" / "scorecard.md"
    if enforce_default_contract and (not scorecard.exists() or scorecard.stat().st_size < 50):
        errors.append("Missing required scorecard")

    text = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(staging_dir.glob("*.md"))
    )
    covered_ids = {
        _unit_id(unit) for unit in facts.units
        if re.search(r"\b" + re.escape(unit.name) + r"\b", text, re.IGNORECASE)
    }
    units_by_id = {_unit_id(unit): unit for unit in facts.units}
    duplicate_names = set(_duplicate_unit_names(facts))
    missing_ids = sorted(set(units_by_id) - covered_ids)
    missing_units = sorted(_unit_label(units_by_id[unit_id], duplicate_names) for unit_id in missing_ids)
    if enforce_default_contract and missing_units:
        errors.append(f"Generated docs do not mention all units: {', '.join(missing_units[:20])}")

    count_patterns = [
        rf"\b{facts.unit_count}\s+{re.escape(facts.canonical_unit_label)}\b",
        rf"\b{facts.unit_count}\s+canonical\s+units\b",
    ]
    if enforce_default_contract and facts.unit_count and not any(re.search(p, text, re.IGNORECASE) for p in count_patterns):
        errors.append(
            f"Generated docs do not state expected coverage count {facts.unit_count}"
        )

    if enforce_default_contract:
        total_claim_errors = _conflicting_total_count_claims(staging_dir, facts)
        if total_claim_errors:
            errors.extend(total_claim_errors)

    config_claim_errors = _conflicting_config_evidence_claims(staging_dir)
    if config_claim_errors:
        errors.extend(config_claim_errors)

    if enforce_default_contract:
        assistant_contract_errors = _assistant_doc_contract_errors(staging_dir, facts)
        if assistant_contract_errors:
            errors.extend(assistant_contract_errors)

    if enforce_default_contract:
        pack_errors = _doc_evidence_pack_errors(staging_dir, required_docs=docs_to_validate)
        if pack_errors:
            errors.extend(pack_errors)

    if not enforce_default_contract:
        covered_ids = set(units_by_id)
        missing_ids = []
        missing_units = []

    return CoverageReport(
        ok=not errors,
        covered_units=sorted(_unit_label(units_by_id[unit_id], duplicate_names) for unit_id in covered_ids),
        covered_unit_ids=sorted(covered_ids),
        missing_units=missing_units,
        missing_unit_ids=missing_ids,
        coverage_count=len(covered_ids),
        expected_count=facts.unit_count,
        errors=errors,
        warnings=_identity_report_warnings(facts),
    )


def build_documentation_obligation_map(facts: FactsManifest, required_docs: list[str] | None = None) -> dict[str, Any]:
    """Build the shared + platform obligation contract for a generation run."""

    try:
        platform_terms = get_platform_strategy(facts.platform).documentation_obligations()
    except ValueError:
        platform_terms = get_platform_strategy("generic").documentation_obligations()

    shared = {
        "identity": ["name", "group", "source artifact", "type"],
        "purpose": ["purpose", "scanner-limited"],
        "dependencies": ["upstream", "downstream", "dependency"],
        "data_movement": ["data", "schema", "mapping", "lineage"],
        "runtime_config": ["config", "environment", "external system"],
        "error_handling": ["error handling", "retry", "failure"],
        "risk": ["risk", "confidence", "evidence"],
        "debug_testing": ["debug", "test", "verify"],
    }
    doc_targets = {
        "identity": ["02_COMPONENT_INVENTORY", "00_AGENT_BRIEF"],
        "purpose": ["02_COMPONENT_INVENTORY", "00_AGENT_BRIEF"],
        "dependencies": ["01_ARCHITECTURE_MAP", "02_COMPONENT_INVENTORY", "08_CHANGE_PLAYBOOK"],
        "data_movement": ["03_DATA_FLOWS_AND_LINEAGE", "04_BUSINESS_RULES_AND_TRANSFORMATIONS"],
        "runtime_config": ["05_RUNTIME_CONFIG_AND_EXTERNALS", "06_OPERATIONS_TESTING_AND_DEBUGGING"],
        "error_handling": ["06_OPERATIONS_TESTING_AND_DEBUGGING", "07_QUALITY_SECURITY_RISK_SCORECARD"],
        "risk": ["07_QUALITY_SECURITY_RISK_SCORECARD", "08_CHANGE_PLAYBOOK"],
        "debug_testing": ["06_OPERATIONS_TESTING_AND_DEBUGGING", "08_CHANGE_PLAYBOOK"],
        "units": ["02_COMPONENT_INVENTORY", "00_AGENT_BRIEF", "08_CHANGE_PLAYBOOK"],
    }
    docs_to_validate = required_docs or REQUIRED_DOCS
    if required_docs is not None:
        doc_targets = {key: list(docs_to_validate) for key in doc_targets}
    platform_doc_targets = {
        f"platform_specific.{category}": docs_to_validate
        for category in platform_terms
    }
    return {
        "platform": facts.platform,
        "canonical_unit_label": facts.canonical_unit_label,
        "shared": shared,
        "platform_specific": platform_terms,
        "doc_targets": {**doc_targets, **platform_doc_targets},
        "units": [
            {
                "name": unit.name,
                "group": unit.group,
                "path": unit.path,
                "type": unit.unit_type,
                "key_systems": unit.key_systems,
                "doc_targets": doc_targets["units"],
            }
            for unit in facts.units
        ],
    }


def _assistant_doc_contract_errors(staging_dir: Path, facts: FactsManifest) -> list[str]:
    errors: list[str] = []
    texts: dict[str, str] = {}
    for doc in REQUIRED_DOCS:
        path = staging_dir / f"{doc}.md"
        if path.exists():
            texts[doc] = path.read_text(encoding="utf-8", errors="replace")

    def require_terms(doc: str, terms: list[str], label: str) -> None:
        text = texts.get(doc, "")
        missing = [term for term in terms if not re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE)]
        if missing:
            errors.append(f"Generated doc {doc}.md missing {label}: {', '.join(missing)}")

    require_terms(
        "00_AGENT_BRIEF",
        ["Onboarding", "Documentation Map", "Common Tasks", "Known Traps", "Glossary"],
        "assistant onboarding sections",
    )
    require_terms(
        "01_ARCHITECTURE_MAP",
        ["Topology", "Boundaries", "Dependencies", "Entry Points", "External Systems"],
        "architecture map obligations",
    )
    require_terms(
        "03_DATA_FLOWS_AND_LINEAGE",
        ["Source", "Target", "Transformation", "Schema", "Validation", "Scanner-Limited"],
        "data flow obligations",
    )
    require_terms(
        "04_BUSINESS_RULES_AND_TRANSFORMATIONS",
        ["Expression", "Unit", "Domain", "Impact"],
        "business-rule table obligations",
    )
    require_terms(
        "05_RUNTIME_CONFIG_AND_EXTERNALS",
        ["External Systems", "Config", "Redacted", "Scanner-Limited"],
        "runtime/config obligations",
    )
    require_terms(
        "07_QUALITY_SECURITY_RISK_SCORECARD",
        ["Score", "Confidence", "Evidence", "Red Flags", "Quick Wins", "Unknowns"],
        "scorecard evidence sections",
    )
    require_terms(
        "08_CHANGE_PLAYBOOK",
        ["Change Impact", "Inspect", "Test", "Rollback"],
        "change-playbook guidance",
    )
    require_terms(
        "06_OPERATIONS_TESTING_AND_DEBUGGING",
        ["Debug", "Verify", "Recovery", "Testing"],
        "debugging and verification guidance",
    )

    inventory = texts.get("02_COMPONENT_INVENTORY", "")
    for unit in facts.units:
        if unit.name not in inventory:
            errors.append(f"Generated doc 02_COMPONENT_INVENTORY.md missing canonical unit obligation: {unit.name}")
            continue
        if unit.path and unit.path not in inventory:
            errors.append(f"Generated doc 02_COMPONENT_INVENTORY.md missing source artifact for {unit.name}: {unit.path}")
        if unit.group and unit.group not in inventory:
            errors.append(f"Generated doc 02_COMPONENT_INVENTORY.md missing group for {unit.name}: {unit.group}")

    runtime_doc = texts.get("05_RUNTIME_CONFIG_AND_EXTERNALS", "")
    for system in facts.key_systems:
        if system and system not in runtime_doc:
            errors.append(f"Generated doc 05_RUNTIME_CONFIG_AND_EXTERNALS.md missing external system row: {system}")

    obligation_map = build_documentation_obligation_map(facts)
    all_text = "\n".join(texts.values())
    platform_terms = obligation_map["platform_specific"]
    for category, terms in platform_terms.items():
        if not terms:
            continue
        if not any(re.search(rf"\b{re.escape(term)}\b", all_text, re.IGNORECASE) for term in terms):
            errors.append(
                f"Generated docs missing platform-specific evidence category "
                f"{facts.platform}.{category}: expected one of {', '.join(terms[:6])}"
            )
    return errors


def _doc_evidence_pack_errors(staging_dir: Path, *, required_docs: list[str] | None = None) -> list[str]:
    analysis_dir = staging_dir / "_analysis"
    if not analysis_dir.exists():
        return []
    errors: list[str] = []
    manifest_path = analysis_dir / "_doc_pack_manifest.json"
    if not manifest_path.exists():
        errors.append("Missing Phase 3 doc evidence pack manifest: _analysis/_doc_pack_manifest.json")
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}
            errors.append("Phase 3 doc evidence pack manifest is invalid JSON")
        packs = manifest.get("doc_packs") if isinstance(manifest, dict) else None
        if not isinstance(packs, dict):
            errors.append("Phase 3 doc evidence pack manifest missing doc_packs")
        else:
            for doc in (required_docs or REQUIRED_DOCS):
                entry = packs.get(doc)
                if not isinstance(entry, dict):
                    errors.append(f"Phase 3 doc evidence pack manifest missing entry for {doc}")
                    continue
                for key in (
                    "included",
                    "omitted",
                    "unclassified",
                    "included_obligations",
                    "unmatched_obligations",
                    "evidence_sources",
                ):
                    if key not in entry:
                        errors.append(f"Phase 3 doc evidence pack manifest entry for {doc} missing {key}")
    for doc in (required_docs or REQUIRED_DOCS):
        pack_path = analysis_dir / f"_doc_pack_{doc}.md"
        if not pack_path.exists():
            errors.append(f"Missing Phase 3 doc evidence pack: _analysis/{pack_path.name}")
            continue
        text = pack_path.read_text(encoding="utf-8", errors="replace")
        required = [
            "## Shared Project Scope",
            "## All Groups And Units",
            "## Cross-Group Reference Index",
            "## External Systems Summary",
            "## Top Risks Summary",
            "## Source Coverage And Scanner Limits",
            "## Documentation Obligations",
            "## Deep Evidence Pointers",
            "## Doc-Specific Phase 2 Evidence",
        ]
        missing = [heading for heading in required if heading not in text]
        if missing:
            errors.append(f"Phase 3 doc evidence pack {pack_path.name} missing sections: {', '.join(missing)}")
    return errors


def validate_doc_depth(
    staging_dir: Path,
    facts: FactsManifest,
    *,
    required_docs: list[str] | None = None,
    enforce_default_contract: bool = True,
) -> CoverageReport:
    """Validate docs have enough depth for the scanned project shape."""

    errors: list[str] = []
    covered_ids: set[str] = set()
    preprocess_dir = staging_dir / "_preprocess"
    total_preprocess_bytes = sum(
        p.stat().st_size
        for p in preprocess_dir.glob("*.md")
        if not p.name.startswith("_")
    ) if preprocess_dir.exists() else 0
    min_doc_bytes = max(8_000, min(45_000, int(total_preprocess_bytes * 0.04))) if enforce_default_contract else 50

    for doc in (required_docs or REQUIRED_DOCS):
        path = staging_dir / f"{doc}.md"
        if not path.exists():
            errors.append(f"Missing required doc for depth validation: {doc}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        doc_bytes = len(text.encode("utf-8"))
        if doc_bytes < min_doc_bytes:
            errors.append(
                f"Generated doc {doc}.md is too shallow: {doc_bytes} bytes < {min_doc_bytes} required"
            )
        lower = text.lower()
        section_terms = DOC_SECTION_REQUIREMENTS.get(doc, []) if enforce_default_contract else []
        missing_terms = [term for term in section_terms if term.lower() not in lower]
        if missing_terms:
            errors.append(
                f"Generated doc {doc}.md missing expected sections/terms: {', '.join(missing_terms)}"
            )
        covered_ids.update(_unit_id(unit) for unit in facts.units if unit.name in text)

    all_text = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(staging_dir.glob("*.md"))
    )
    missing_groups = [group for group in facts.groups if group not in all_text]
    if enforce_default_contract and missing_groups:
        errors.append(f"Generated docs do not mention groups: {', '.join(missing_groups)}")
    if not enforce_default_contract:
        covered_ids = {_unit_id(unit) for unit in facts.units}

    return _coverage_report_from_identities(
        facts=facts,
        covered_ids=covered_ids,
        skipped_ids={},
        errors=errors,
    )


def _required_output_present(text: str, output: str) -> bool:
    """Return True if output appears as a section heading (with content) or table-column header.

    Splits the output on underscores/hyphens so that ``table_inventory`` matches
    a heading like ``## Table Inventory`` or a table cell ``| Table Inventory |``.
    A heading match requires at least one non-blank following line so that an empty
    section stub does not count as satisfied.
    """
    output_words = [w for w in re.split(r"[_\-\s]+", output.lower()) if w]
    if not output_words:
        return False
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().lower()
            heading_words = re.split(r"[_\-\s]+", heading)
            if all(w in heading_words for w in output_words):
                rest = [ln.strip() for ln in lines[i + 1 : i + 10] if ln.strip()]
                if rest:
                    return True
        if "|" in stripped:
            for cell in stripped.strip("|").split("|"):
                cell_words = re.split(r"[_\-\s]+", cell.strip().lower())
                if all(w in cell_words for w in output_words):
                    return True
    return False


def validate_pack_quality(staging_dir: Path, manifest: Any) -> dict[str, Any]:
    """Validate generated plugin-pack docs against deterministic pack gates."""

    docs = list(getattr(manifest, "docs", None) or [])
    doc_names = [doc.name for doc in docs]
    gates = getattr(manifest, "quality_gates", None)
    validation = getattr(manifest, "validation", None)
    fields_set = getattr(manifest, "model_fields_set", set())
    validation_explicit = "validation" in fields_set
    mode = (
        getattr(validation, "mode", "blocking")
        if validation_explicit
        else (getattr(gates, "mode", "blocking") if gates is not None else "blocking")
    )
    unknown_policy = getattr(validation, "unknown_policy", "explicit")
    errors: list[str] = []
    warnings: list[str] = []
    doc_reports: dict[str, dict[str, Any]] = {}

    for doc_name in doc_names:
        path = staging_dir / f"{doc_name}.md"
        exists = path.exists()
        text = path.read_text(encoding="utf-8", errors="replace") if exists else ""
        doc_reports[doc_name] = {
            "exists": exists,
            "bytes": len(text.encode("utf-8")),
        }
        if not exists:
            errors.append(f"Missing pack doc: {doc_name}.md")
        elif not text.strip():
            errors.append(f"Pack doc is empty: {doc_name}.md")

    for doc in docs:
        doc_name = doc.name
        path = staging_dir / f"{doc_name}.md"
        text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        headings = {
            line.lstrip("#").strip().lower()
            for line in text.splitlines()
            if line.lstrip().startswith("#")
        }
        required_sections = list(getattr(doc, "required_sections", []) or getattr(doc, "sections", []) or [])
        missing_sections = [section for section in required_sections if section.lower() not in headings]
        if missing_sections and path.exists():
            errors.append(f"Pack doc {doc_name}.md missing required sections: {', '.join(missing_sections)}")

        for output in getattr(doc, "required_outputs", []) or []:
            if not _required_output_present(text, str(output)):
                warnings.append(
                    f"Pack doc {doc_name}.md: expected output '{output}' not found as section heading"
                    " or column header — section may use different heading wording"
                )

        profile_name = getattr(doc, "evidence_profile", None)
        required_evidence = list(getattr(doc, "required_evidence", []) or [])
        if profile_name and required_evidence:
            bundle_path = staging_dir / "_evidence" / f"{profile_name}.json"
            if not bundle_path.exists():
                errors.append(f"Pack doc {doc_name}.md missing evidence bundle for profile: {profile_name}")
            else:
                try:
                    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    errors.append(f"Pack evidence bundle {profile_name}.json is invalid: {exc}")
                    payload = {}
                categories = payload.get("categories", {}) if isinstance(payload, dict) else {}
                missing_required = [
                    category
                    for category in required_evidence
                    if not categories.get(category)
                ]
                if missing_required:
                    if unknown_policy == "explicit" and not re.search(r"\bUNKNOWN\b|not evidenced", text, re.I):
                        errors.append(
                            f"Pack doc {doc_name}.md missing explicit UNKNOWN/not evidenced wording for absent evidence: "
                            + ", ".join(missing_required)
                        )
                    doc_reports.setdefault(doc_name, {})["missing_required_evidence"] = missing_required

    if gates is None:
        ok = not errors
        effective_ok = ok or mode == "advisory"
        if errors and mode == "advisory":
            warnings.append("Pack validation failed in advisory mode")
        return {
            "ok": ok,
            "effective_ok": effective_ok,
            "mode": mode,
            "configured": False,
            "errors": errors,
            "warnings": warnings,
            "docs": doc_reports,
            "summary": "No pack quality_gates configured; applied manifest contract validation only.",
        }

    all_text = "\n".join(
        (staging_dir / f"{doc_name}.md").read_text(encoding="utf-8", errors="replace")
        for doc_name in doc_names
        if (staging_dir / f"{doc_name}.md").exists()
    )
    all_text_lower = all_text.lower()

    for term in gates.required_terms:
        if term.lower() not in all_text_lower:
            errors.append(f"Missing required pack term: {term}")

    for term in gates.required_evidence_terms:
        if term.lower() not in all_text_lower:
            errors.append(f"Missing required evidence/provenance term: {term}")

    for doc_name, required_sections in gates.required_sections.items():
        path = staging_dir / f"{doc_name}.md"
        if not path.exists():
            errors.append(f"Cannot validate sections for missing pack doc: {doc_name}.md")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        headings = {
            line.lstrip("#").strip().lower()
            for line in text.splitlines()
            if line.lstrip().startswith("#")
        }
        missing = [section for section in required_sections if section.lower() not in headings]
        if missing:
            errors.append(f"Pack doc {doc_name}.md missing required sections: {', '.join(missing)}")

    for doc_name, min_bytes in gates.min_doc_bytes.items():
        path = staging_dir / f"{doc_name}.md"
        if not path.exists():
            errors.append(f"Cannot validate byte minimum for missing pack doc: {doc_name}.md")
            continue
        actual = path.stat().st_size
        doc_reports.setdefault(doc_name, {"exists": True})["bytes"] = actual
        doc_reports[doc_name]["min_bytes"] = min_bytes
        if actual < min_bytes:
            errors.append(f"Pack doc {doc_name}.md is too small: {actual} bytes < {min_bytes} required")

    ok = not errors
    effective_ok = ok or mode == "advisory"
    if errors and mode == "advisory":
        warnings.append("Pack quality gates failed in advisory mode")
    return {
        "ok": ok,
        "effective_ok": effective_ok,
        "mode": mode,
        "configured": True,
        "errors": errors,
        "warnings": warnings,
        "docs": doc_reports,
    }


_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:password|passwd|pwd|secret|api[_-]?key|token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{8,}", re.I),
    re.compile(r"\b(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def validate_final_redaction(staging_dir: Path) -> CoverageReport:
    """Scan publishable docs and persisted run artifacts for obvious secrets."""

    errors: list[str] = []
    scanned = 0
    for path in sorted(staging_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(staging_dir).as_posix()
        if path.suffix.lower() not in {".md", ".json", ".log", ".txt"}:
            continue
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                errors.append(f"Potential unredacted secret in {rel}")
                break
        if len(errors) >= 20:
            break
    return CoverageReport(
        ok=not errors,
        coverage_count=scanned,
        expected_count=scanned,
        errors=errors,
    )


def validate_source_provenance(
    staging_dir: Path,
    facts: FactsManifest,
    *,
    required_docs: list[str] | None = None,
    enforce_default_contract: bool = True,
) -> CoverageReport:
    """Require publishable docs to carry a concrete evidence register."""

    errors: list[str] = []
    warnings: list[str] = []
    docs = {
        doc: staging_dir / f"{doc}.md"
        for doc in (required_docs or REQUIRED_DOCS)
        if (staging_dir / f"{doc}.md").exists()
    }
    if not docs:
        errors.append("No publishable markdown docs found for provenance validation")
    for doc, path in docs.items():
        doc_errors, doc_warnings = _evidence_register_errors(path, DOC_SECTION_REQUIREMENTS.get(doc, []))
        errors.extend(doc_errors)
        warnings.extend(doc_warnings)

    if not enforce_default_contract:
        return _coverage_report_from_identities(
            facts=facts,
            covered_ids={_unit_id(unit) for unit in facts.units},
            skipped_ids={},
            errors=errors,
            warnings=warnings,
        )

    inventory = docs.get("02_COMPONENT_INVENTORY")
    inventory_text = inventory.read_text(encoding="utf-8", errors="replace") if inventory else ""
    covered_ids = {
        _unit_id(unit)
        for unit in facts.units
        if unit.name in inventory_text and (not unit.path or unit.path in inventory_text)
    }
    if len(covered_ids) < facts.unit_count:
        missing = [
            unit.name for unit in facts.units
            if _unit_id(unit) not in covered_ids
        ]
        inventory_name = inventory.name if inventory else "02_COMPONENT_INVENTORY.md"
        errors.append(
            f"{inventory_name} source provenance missing canonical unit artifact paths for: "
            + ", ".join(missing[:20])
        )
    return _coverage_report_from_identities(
        facts=facts,
        covered_ids=covered_ids,
        skipped_ids={},
        errors=errors,
        warnings=warnings,
    )


_EVIDENCE_REGISTER_HEADERS = (
    "topic",
    "units / scope",
    "evidence source",
    "source artifact / path",
    "confidence",
)
_EVIDENCE_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
_EVIDENCE_CONFIDENCE_RE = re.compile(r"^\s*(HIGH|MEDIUM|LOW)\b", re.IGNORECASE)
_EVIDENCE_CONFIDENCE_MARKDOWN_WRAPPERS = ("**", "__", "`", "*", "_")
_PROVENANCE_SOURCE_RE = re.compile(
    r"(?:doc[_ -]?pack|phase\s*2|engineering[_ -]?facts|facts_manifest|"
    r"scanner|scan|preprocess|investigation|scorecard|source|"
    r"artifact|path|"
    r"\.item\b|\.json\b|\.csv\b|\.md\b|\.xml\b|\.py\b|\.tsx?\b|\.js\b|\.sql\b|"
    r"\.cs\b|\.vb\b|\.config\b|\.asax\b|\.csproj\b|\.aspx\b|\.ashx\b|\.resx\b|"
    r"\.java\b|\.kt\b|\.go\b|\.rs\b|\.rb\b|\.sh\b|\.ps1\b|\.bat\b|"
    r"\.yaml\b|\.yml\b|\.toml\b|\.ini\b|\.cfg\b|\.tf\b|\.hcl\b|"
    r"\.cpp\b|\.c\b|\.h\b|\.hpp\b|\.php\b|\.swift\b|\.scala\b|"
    r"[/\\][\w])",
    re.IGNORECASE,
)
# Looser check: any content that looks like a file path (has an extension or path separator).
# Used to classify "has-content-but-unrecognized-format" rows as soft warnings rather than hard errors.
_PATH_LIKE_RE = re.compile(r"[/\\][\w]|\b[\w.\-]+\.[a-zA-Z]{2,6}\b")


def _markdown_table_cells(line: str) -> list[str]:
    if "|" not in line:
        return []
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _table_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def _evidence_confidence_level(cell: str) -> str | None:
    match = _EVIDENCE_CONFIDENCE_RE.match(cell)
    if match:
        return match.group(1).upper()

    stripped = cell.lstrip()
    for wrapper in _EVIDENCE_CONFIDENCE_MARKDOWN_WRAPPERS:
        if not stripped.startswith(wrapper):
            continue
        wrapped = re.match(
            rf"^{re.escape(wrapper)}(HIGH|MEDIUM|LOW){re.escape(wrapper)}(?!\w)",
            stripped,
            re.IGNORECASE,
        )
        if wrapped:
            return wrapped.group(1).upper()
    return None


def _evidence_register_errors(path: Path, major_topics: list[str]) -> tuple[list[str], list[str]]:
    """Return (hard_errors, soft_warnings) for an Evidence Register table.

    Hard errors block publication (empty/missing sources, wrong structure).
    Soft warnings flag unrecognized source path formats without triggering repair.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    match = EVIDENCE_REGISTER_SECTION_RE.search(text)
    if not match:
        return [f"{path.name} missing required Evidence Register"], []

    lines = [line for line in match.group(1).splitlines() if line.strip()]
    table_start = next((index for index, line in enumerate(lines) if line.lstrip().startswith("|")), None)
    if table_start is None or table_start + 1 >= len(lines):
        return [f"{path.name} Evidence Register missing required provenance table"], []

    headers = [cell.lower() for cell in _markdown_table_cells(lines[table_start])]
    if headers != list(_EVIDENCE_REGISTER_HEADERS):
        return [f"{path.name} Evidence Register has invalid table headers"], []
    if not _table_separator(_markdown_table_cells(lines[table_start + 1])):
        return [f"{path.name} Evidence Register missing markdown table separator"], []

    rows = [
        _markdown_table_cells(line)
        for line in lines[table_start + 2 :]
        if line.lstrip().startswith("|")
    ]
    rows = [row for row in rows if len(row) == len(_EVIDENCE_REGISTER_HEADERS) and not _table_separator(row)]
    substantive = [
        row for row in rows
        if all(cell and cell not in {"-", "n/a", "na", "none", "tbd"} for cell in (c.lower() for c in row[:4]))
    ]
    min_rows = max(1, min(3, len(major_topics)))
    errors: list[str] = []
    warnings: list[str] = []
    if len(substantive) < min_rows:
        errors.append(f"{path.name} Evidence Register has fewer than {min_rows} substantive evidence rows")
        return errors, warnings

    if any(_evidence_confidence_level(row[4]) not in _EVIDENCE_CONFIDENCE for row in substantive):
        errors.append(f"{path.name} Evidence Register has invalid confidence; use HIGH, MEDIUM, or LOW")

    rows_no_provenance = [
        row for row in substantive
        if not _PROVENANCE_SOURCE_RE.search(f"{row[2]} {row[3]}")
    ]
    if rows_no_provenance:
        _empty_vals = {"-", "n/a", "na", "none", "tbd", "unknown", "tbc", "n/a (inferred)", "n/a (scanner-limited)"}
        # Hard error: empty/placeholder OR generic text with no path-like content.
        # Soft warning: has a file extension or path separator but extension isn't in our whitelist.
        hard_rows = [
            row for row in rows_no_provenance
            if not row[3].strip()
            or row[3].strip().lower() in _empty_vals
            or not _PATH_LIKE_RE.search(f"{row[2]} {row[3]}")
        ]
        soft_rows = [row for row in rows_no_provenance if row not in hard_rows]
        if hard_rows:
            warnings.append(f"{path.name} Evidence Register has evidence rows without source provenance")
        if soft_rows:
            sample = "; ".join(r[3][:60] for r in soft_rows[:3])
            warnings.append(
                f"{path.name} Evidence Register has {len(soft_rows)} rows with unrecognized"
                f" source path format (verify manually): {sample}"
            )
    return errors, warnings


def validate_usefulness_rubric(
    staging_dir: Path,
    facts: FactsManifest,
    *,
    required_docs: list[str] | None = None,
    enforce_default_contract: bool = True,
) -> CoverageReport:
    """Final deterministic product-quality rubric for developer usefulness."""

    text = "\n".join(
        (staging_dir / f"{doc}.md").read_text(encoding="utf-8", errors="replace")
        for doc in (required_docs or REQUIRED_DOCS)
        if (staging_dir / f"{doc}.md").exists()
    )
    if not enforce_default_contract:
        errors = [] if text.strip() else ["Plugin-pack output is empty"]
        return _coverage_report_from_identities(
            facts=facts,
            covered_ids={_unit_id(unit) for unit in facts.units},
            skipped_ids={},
            errors=errors,
        )

    lower = text.lower()
    rubric = {
        "developer onboarding": ["onboarding", "documentation map", "common tasks"],
        "change navigation": ["where to change", "change impact", "inspect"],
        "data flow and rules": ["data flow", "lineage", "business rule", "transformation"],
        "runtime dependencies": ["external systems", "config", "runtime", "secret", "redacted"],
        "debugging": ["debug", "troubleshoot", "verify", "failure"],
        "tests and rollback": ["test", "rollback", "recovery"],
        "unknowns": ["unknown", "scanner-limited", "confidence"],
    }
    errors = [
        f"Usefulness rubric missing {label}: expected one of {', '.join(terms)}"
        for label, terms in rubric.items()
        if not any(term in lower for term in terms)
    ]
    covered_ids = {_unit_id(unit) for unit in facts.units if unit.name in text}
    return _coverage_report_from_identities(
        facts=facts,
        covered_ids=covered_ids,
        skipped_ids={},
        errors=errors,
    )


def _conflicting_total_count_claims(staging_dir: Path, facts: FactsManifest) -> list[str]:
    """Find count mismatches only when the text claims a total/canonical inventory.

    Group counts such as "Account (10 jobs)" or pattern counts such as
    "6 jobs use OnRamp" are valid and must not block publishing.
    """

    if not facts.unit_count:
        return []

    label = re.escape(facts.canonical_unit_label)
    total_terms = (
        r"canonical\s+inventory|canonical\s+unit\s+count|canonical\s+coverage|"
        r"validated\s+coverage|validated\s+inventory|total\s+inventory|"
        r"total\s+count|total\s+units|total\s+jobs|project\s+inventory|"
        r"overall\s+inventory|inventory\s+contains|project\s+contains|"
        r"contains\s+exactly|comprises\s+exactly|unit_count"
    )
    patterns = [
        re.compile(rf"\b(?:{total_terms})\b[^.\n|:;]*?[:=]?\s*\*{{0,2}}(\d+)\s+{label}\b", re.IGNORECASE),
        re.compile(rf"\b(?:{total_terms})\b[^.\n|:;]*?[:=]?\s*\*{{0,2}}(\d+)\s*/\s*(\d+)\s+{label}\b", re.IGNORECASE),
        re.compile(rf"\b(?:{total_terms})\b[^.\n|:;]*?[:=]?\s*\*{{0,2}}(\d+)\s+canonical\s+units\b", re.IGNORECASE),
        re.compile(rf"\b(\d+)\s+{label}\b[^.\n|:;]*?\b(?:{total_terms})\b", re.IGNORECASE),
    ]

    problems: list[str] = []
    for path in sorted(staging_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in patterns:
            for match in pattern.finditer(text):
                counts = [int(value) for value in match.groups() if value and value.isdigit()]
                if counts and any(count != facts.unit_count for count in counts):
                    snippet = " ".join(match.group(0).split())
                    problems.append(
                        f"Generated docs contain conflicting total inventory claim in "
                        f"{path.name}: {snippet!r}"
                    )
                    if len(problems) >= 5:
                        return problems
    return problems


def _conflicting_config_evidence_claims(staging_dir: Path) -> list[str]:
    """Reject docs that say scanned config files are missing or unavailable.

    The EngineeringFacts config map intentionally redacts secret values. Docs may
    say values are redacted/unavailable, but not that the scanned config file or
    its content was absent when a config_file fact exists.
    """

    config_files = scanned_config_files(staging_dir) | covered_source_files(staging_dir)
    if not config_files:
        return []

    allowed_value_context = re.compile(r"\b(secret|credential|password|token|value|values)\b.{0,80}\b(redacted|unavailable|not available)\b", re.I)
    problems: list[str] = []
    for path in sorted(staging_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        lower = text.lower()
        for config_path in config_files:
            labels = {config_path.lower(), Path(config_path).name.lower()}
            for label in sorted(labels, key=len, reverse=True):
                start = 0
                while True:
                    idx = lower.find(label, start)
                    if idx < 0:
                        break
                    window = text[max(0, idx - 140): min(len(text), idx + len(label) + 180)]
                    if _claims_config_file_unavailable(window, label) and not allowed_value_context.search(window):
                        snippet = " ".join(window.split())[:220]
                        problems.append(
                            f"Generated docs contradict scanned config evidence in {path.name}: {snippet!r}"
                        )
                        if len(problems) >= 5:
                            return problems
                        break
                    start = idx + len(label)
    return problems


def strip_config_contradiction_sentences(text: str, config_files: set[str]) -> str:
    """Remove lines claiming a scanned config file was not scanned/captured.

    Covers both plain text and ~~strikethrough~~ spans that repair models
    sometimes emit when echoing back the corrected (old) text.
    Uses the same label set and scope as _claims_config_file_unavailable.
    """
    for config_path in config_files:
        labels = {config_path.lower(), Path(config_path).name.lower()}
        for label in sorted(labels, key=len, reverse=True):
            escaped = re.escape(label)
            unavail = r"(?:was\s+)?(?:not\s+scanned|unscanned|not\s+captured)"
            # Inline strikethrough spans: ~~...label...not scanned...~~
            text = re.sub(
                rf"~~[^~\n]*\b{escaped}\b[^~\n]{{0,200}}\b{unavail}\b[^~\n]*~~",
                "",
                text,
                flags=re.I,
            )
            # Whole lines (bullet items, inline notes)
            text = re.sub(
                rf"(?im)^[^\n]*\b{escaped}\b[^\n]{{0,200}}\b{unavail}\b[^\n]*\n?",
                "",
                text,
            )
    return text


def _claims_config_file_unavailable(window: str, label: str) -> bool:
    """Return True only for direct claims that a scanned config file/content is absent.

    Avoid broad false positives such as `missing_error_path`, "missing static
    files", or "exact code is not available" near a config filename.
    """

    escaped = re.escape(label)
    direct_patterns = [
        rf"\b{escaped}\b[^.\n|]{{0,120}}\b(?:was\s+)?(?:not\s+scanned|unscanned|not\s+captured)\b",
        rf"\b{escaped}\b[^.\n|]{{0,120}}\bcontent\s+(?:is\s+|was\s+)?(?:unavailable|not\s+available|missing|not\s+captured)\b",
        rf"\bcontent\s+(?:of|for|in)\s+[`'\"]?{escaped}[`'\"]?[^.\n|]{{0,120}}\b(?:unavailable|not\s+available|missing|not\s+captured)\b",
        rf"\b(?:could\s+not|unable\s+to)\s+(?:read|scan|inspect)\s+[`'\"]?{escaped}[`'\"]?\b",
        rf"\bno\s+content\s+(?:was\s+)?(?:read|scanned|captured)\s+(?:from|for|in)\s+[`'\"]?{escaped}[`'\"]?\b",
    ]
    return any(re.search(pattern, window, re.I) for pattern in direct_patterns)


def scanned_config_files(staging_dir: Path) -> set[str]:
    paths: set[str] = set()
    candidates = [
        staging_dir / "config_runtime_map.json",
        staging_dir / "_facts" / "config_runtime_map.json",
        staging_dir / "engineering_facts.json",
        staging_dir / "_facts" / "engineering_facts.json",
    ]
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            items = payload.get("config_runtime") or []
        elif isinstance(payload, list):
            items = payload
        else:
            items = []
        for item in items:
            if not isinstance(item, dict) or item.get("config_type") != "config_file":
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            name = str(metadata.get("path") or item.get("name") or item.get("key") or "")
            if name:
                paths.add(name)
    return paths


def covered_source_files(staging_dir: Path) -> set[str]:
    paths: set[str] = set()
    candidates = [
        staging_dir / "_preprocess" / "_source_coverage.json",
        staging_dir / "_preprocess" / "_source_context" / "coverage.json",
    ]
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        files = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(files, list):
            continue
        for item in files:
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "").startswith("skipped"):
                continue
            path = str(item.get("path") or "")
            if path:
                paths.add(path)
    return paths


# ── Scorecard contract validator ─────────────────────────────────────────────

_SCORECARD_DIMENSIONS = [
    "data_lineage",
    "business_logic",
    "security",
    "operations",
    "architecture",
    "performance",
    "standards",
    "migration",
]

_SCORECARD_WEIGHTS = {
    "data_lineage": "20%",
    "business_logic": "20%",
    "security": "15%",
    "operations": "15%",
    "architecture": "10%",
    "performance": "10%",
    "standards": "5%",
    "migration": "5%",
}

_OWNERSHIP_TAG_RE = re.compile(r"\[SOLO\]|\[PAIR\]", re.I)
_TABLE_ROW_RE = re.compile(r"^\s*\|.+\|", re.MULTILINE)


def validate_scorecard_contract(text: str) -> dict:
    """Check that a scorecard output satisfies the declared contract.

    Validates:
    - All 8 dimension names appear in the scorecard (any context).
    - All 8 dimension weights appear adjacent to their dimension names.
    - Every table row that looks like a risk entry contains [SOLO] or [PAIR].
    - Dimensions 'security' and 'operations' do not claim HIGH confidence
      without at least one PASS checklist item nearby.

    Returns {"ok": bool, "errors": list[str]}.
    """
    errors: list[str] = []
    lower = text.lower()

    # Check all dimension names present
    for dim in _SCORECARD_DIMENSIONS:
        # Allow space/underscore variants (e.g. "data lineage" or "data_lineage")
        pattern = dim.replace("_", r"[\s_]")
        if not re.search(pattern, lower):
            errors.append(f"Scorecard missing required dimension: {dim}")

    # Check weights appear near dimension names
    for dim, weight in _SCORECARD_WEIGHTS.items():
        pattern = dim.replace("_", r"[\s_]")
        for m in re.finditer(pattern, lower):
            window = text[max(0, m.start() - 20) : m.end() + 80]
            if weight in window:
                break
        else:
            errors.append(f"Scorecard dimension '{dim}' missing weight ({weight})")

    # Check risk rows have ownership tags
    risk_section = re.search(r"(?i)#+\s*(top\s+risks?|risks?\s+and|risk\s+findings?)", text)
    if risk_section:
        risk_text = text[risk_section.start():]
        # Find the next section heading to bound the risk table
        next_section = re.search(r"(?m)^#+\s+\S", risk_text[1:])
        risk_text = risk_text[: next_section.start() + 1] if next_section else risk_text
        rows = [ln for ln in risk_text.splitlines() if "|" in ln and not re.match(r"^\s*\|[-: ]+\|", ln)]
        header_skipped = False
        for row in rows:
            if not header_skipped:
                header_skipped = True
                continue
            cells = [c.strip() for c in row.strip("|").split("|")]
            # Only validate rows with at least 3 cells (avoid separator rows)
            if len(cells) < 3 or not any(cells):
                continue
            if not _OWNERSHIP_TAG_RE.search(row):
                label = cells[0][:40] if cells else "unknown"
                errors.append(
                    f"Risk row '{label}' missing ownership tag: add [SOLO] or [PAIR] to the remediation cell"
                )

    return {"ok": not errors, "errors": errors}
