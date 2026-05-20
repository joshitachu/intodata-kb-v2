"""Facts-manifest construction and publish gates for the custom harness."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from mcp_server.platforms import get_platform_strategy
from mcp_server.providers.custom.models import CoverageReport, FactsManifest, InvestigationReport, UnitFact
from mcp_server.providers.custom.prompt_packs import ANALYSIS_SECTION_NAMES, REQUIRED_SECTION_NAMES
from mcp_server.providers.custom.prompts import DOC_SECTION_REQUIREMENTS, REQUIRED_DOCS

GROUP_SUMMARY_SECTION = "GROUP_SUMMARY"


def _load_scan_result(scan_output_dir: Path) -> dict[str, Any]:
    mono = scan_output_dir / "scan_result.json"
    if mono.exists():
        return json.loads(mono.read_text(encoding="utf-8"))

    scan_dir = scan_output_dir / "scan"
    if not scan_dir.exists():
        raise FileNotFoundError(f"No scan result found in {scan_output_dir}")

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
        ref = comp.get("metadata_connection_ref")
        if ref:
            systems.add(str(ref))
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


def validate_analysis_coverage(analysis_dir: Path, facts: FactsManifest) -> CoverageReport:
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
) -> CoverageReport:
    """Validate that Phase 2 contains real per-unit analysis, not just coverage."""

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
) -> CoverageReport:
    """Require canonical guide sections for Talend Phase 2 output."""

    covered_ids: set[str] = set()
    errors: list[str] = []

    if facts.platform != "talend":
        all_ids = {_unit_id(unit) for unit in facts.units}
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
            if not re.search(rf"^###\s+{GROUP_SUMMARY_SECTION}\s*$", _model_analysis_text(text), re.MULTILINE):
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
            if not re.search(rf"^###\s+{re.escape(name)}\s*$", model_text, re.MULTILINE)
        ]
        if not re.search(rf"^###\s+{GROUP_SUMMARY_SECTION}\s*$", model_text, re.MULTILINE):
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

    return _coverage_report_from_identities(
        facts=facts,
        covered_ids=covered_ids,
        skipped_ids={},
        errors=errors,
    )


def normalize_analysis_guide_headings(text: str, platform: str) -> str:
    """Normalize known guide section headings to the canonical Phase 2 level."""

    section_names = (GROUP_SUMMARY_SECTION,)
    if platform == "talend":
        section_names = tuple(ANALYSIS_SECTION_NAMES) + section_names
    pattern = re.compile(
        rf"^#{{1,6}}\s+(?:#{{1,6}}\s+)?(?P<name>{'|'.join(re.escape(n) for n in section_names)})\s*$",
        re.MULTILINE,
    )
    return pattern.sub(lambda m: f"### {m.group('name')}", text)


def missing_required_analysis_sections(text: str, platform: str) -> list[str]:
    """Return required guide sections absent from a Phase 2 model-analysis body."""

    model_text = _model_analysis_text(text)
    missing_summary = [] if re.search(rf"^###\s+{GROUP_SUMMARY_SECTION}\s*$", model_text, re.MULTILINE) else [GROUP_SUMMARY_SECTION]
    if platform != "talend":
        return missing_summary
    return [
        name for name in REQUIRED_SECTION_NAMES
        if not re.search(rf"^###\s+{re.escape(name)}\s*$", model_text, re.MULTILINE)
    ] + missing_summary


def _model_analysis_text(text: str) -> str:
    marker = "## Model Analysis"
    if marker in text:
        return text.split(marker, 1)[1]
    return text


def group_analysis_is_complete(analysis_path: Path, platform: str) -> bool:
    """Return True if a cached Phase 2 file has all required sections (Talend only)."""
    text = analysis_path.read_text(encoding="utf-8", errors="replace")
    model_text = _model_analysis_text(text)
    required = (GROUP_SUMMARY_SECTION,) if platform != "talend" else (*REQUIRED_SECTION_NAMES, GROUP_SUMMARY_SECTION)
    return all(
        re.search(rf"^###\s+{re.escape(name)}\s*$", model_text, re.MULTILINE)
        for name in required
    )


def validate_docs(staging_dir: Path, facts: FactsManifest) -> CoverageReport:
    errors: list[str] = []
    missing_docs = [doc for doc in REQUIRED_DOCS if not (staging_dir / f"{doc}.md").exists()]
    if missing_docs:
        errors.append(f"Missing required docs: {', '.join(missing_docs)}")

    scorecard = staging_dir / "_assessment" / "scorecard.md"
    if not scorecard.exists() or scorecard.stat().st_size < 50:
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
    if missing_units:
        errors.append(f"Generated docs do not mention all units: {', '.join(missing_units[:20])}")

    count_patterns = [
        rf"\b{facts.unit_count}\s+{re.escape(facts.canonical_unit_label)}\b",
        rf"\b{facts.unit_count}\s+canonical\s+units\b",
    ]
    if facts.unit_count and not any(re.search(p, text, re.IGNORECASE) for p in count_patterns):
        errors.append(
            f"Generated docs do not state expected coverage count {facts.unit_count}"
        )

    total_claim_errors = _conflicting_total_count_claims(staging_dir, facts)
    if total_claim_errors:
        errors.extend(total_claim_errors)

    config_claim_errors = _conflicting_config_evidence_claims(staging_dir)
    if config_claim_errors:
        errors.extend(config_claim_errors)

    assistant_contract_errors = _assistant_doc_contract_errors(staging_dir, facts)
    if assistant_contract_errors:
        errors.extend(assistant_contract_errors)

    pack_errors = _doc_evidence_pack_errors(staging_dir)
    if pack_errors:
        errors.extend(pack_errors)

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


def build_documentation_obligation_map(facts: FactsManifest) -> dict[str, Any]:
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
    platform_doc_targets = {
        f"platform_specific.{category}": REQUIRED_DOCS
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


def _doc_evidence_pack_errors(staging_dir: Path) -> list[str]:
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
            for doc in REQUIRED_DOCS:
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
    for doc in REQUIRED_DOCS:
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


def validate_doc_depth(staging_dir: Path, facts: FactsManifest) -> CoverageReport:
    """Validate docs have enough depth for the scanned project shape."""

    errors: list[str] = []
    covered_ids: set[str] = set()
    preprocess_dir = staging_dir / "_preprocess"
    total_preprocess_bytes = sum(
        p.stat().st_size
        for p in preprocess_dir.glob("*.md")
        if not p.name.startswith("_")
    ) if preprocess_dir.exists() else 0
    min_doc_bytes = max(8_000, min(45_000, int(total_preprocess_bytes * 0.04)))

    for doc in REQUIRED_DOCS:
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
        section_terms = DOC_SECTION_REQUIREMENTS.get(doc, [])
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
    if missing_groups:
        errors.append(f"Generated docs do not mention groups: {', '.join(missing_groups)}")

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

    config_files = _scanned_config_files(staging_dir) | _covered_source_files(staging_dir)
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


def _strip_config_contradiction_sentences(text: str, config_files: set[str]) -> str:
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


def _scanned_config_files(staging_dir: Path) -> set[str]:
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


def _covered_source_files(staging_dir: Path) -> set[str]:
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
