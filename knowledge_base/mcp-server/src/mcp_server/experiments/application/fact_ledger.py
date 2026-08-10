"""Deterministic scanner facts and high-confidence documentation claim checks."""

from __future__ import annotations

from collections import Counter, defaultdict
import functools
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any


_INTEGER = r"(?P<count>\d[\d,]*)"
# Project-total patterns bind the count directly to the phrase that makes it a
# project-wide canonical-unit/component claim (rather than gating on "does
# this LINE contain the word 'canonical' anywhere" and then matching any
# "<N> units" in the whole line -- that used to also catch unrelated numbers
# from a different clause in the same sentence, e.g. a per-job callee count
# such as "AssetCheck_Main_Job calls 6 units").
_PROJECT_CANONICAL_UNITS = re.compile(
    rf"{_INTEGER}\s*(?:/\s*\d[\d,]*\s*)?canonical\s+(?:jobs?|units?)\b",
    re.IGNORECASE,
)
_PROJECT_CONTAINS_EXACTLY = re.compile(
    rf"project\s+contains\s+exactly\s+{_INTEGER}\s+(?:jobs?|units?)\b",
    re.IGNORECASE,
)
_PROJECT_TOTAL_COMPONENTS_PRE = re.compile(
    rf"{_INTEGER}\s+total\s+components?\b",
    re.IGNORECASE,
)
_PROJECT_TOTAL_COMPONENTS_POST = re.compile(
    rf"{_INTEGER}\s+components?\s+total\b",
    re.IGNORECASE,
)
_MISSING_SOURCE = re.compile(
    r"\b(?:source(?:\s+file)?\s+(?:is\s+)?(?:missing|absent|not\s+found)|"
    r"missing\s+source(?:\s+files?)?|not\s+present\s+on\s+disk)\b",
    re.IGNORECASE,
)
_MARKDOWN_TABLE_COUNT = re.compile(
    r"^\s*\|\s*`?(?P<subject>t[A-Za-z0-9_]+)`?\s*\|\s*"
    r"(?P<count>\d[\d,]*)\s*\|",
    re.IGNORECASE,
)


def load_frozen_json_sources(
    source_items: tuple[tuple[str, Path], ...],
) -> dict[str, Any]:
    """Parse frozen scanner JSON sources into a relative-path -> payload map.

    Single source of truth for how frozen ``source_items`` are keyed and
    decoded, shared by the ledger compiler and the per-unit verbatim evidence
    extractor so the two never diverge on what counts as a readable JSON
    source. Frozen snapshot content is stored under opaque names; the logical
    identity is the authoritative media identity.
    """

    json_sources: dict[str, Any] = {}
    normalized_paths: set[str] = set()
    for logical_identity, path in source_items:
        relative = _logical_relative_path(logical_identity)
        if relative in normalized_paths:
            raise ValueError("grounded source logical paths must be unique after normalization")
        normalized_paths.add(relative)
        if PurePosixPath(relative).suffix.casefold() != ".json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        json_sources[relative] = payload
    return json_sources


def canonical_unit_records(
    json_sources: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Return ``(source_relative_path, unit_payload)`` for every canonical unit.

    Prefers the split ``scan/jobs/<group>/<name>.json`` layout and falls back
    to the ``scan_result.json`` embedded jobs list -- the same precedence the
    ledger compiler uses to derive canonical units.
    """

    return _split_job_records(json_sources) or _embedded_job_records(json_sources)


def compile_fact_ledger(
    source_items: tuple[tuple[str, Path], ...],
    *,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Compile provider-neutral, reproducible facts from frozen scanner JSON."""

    json_sources = load_frozen_json_sources(source_items)
    source_hash_inputs = sorted(json_sources)

    _, raw_files_index = _first_source(
        json_sources,
        suffix="scan/files_index.json",
    )
    files_index = raw_files_index if isinstance(raw_files_index, list) else []
    indexed_paths = {
        str(item.get("relative_path") or "").strip().replace("\\", "/")
        for item in files_index
        if isinstance(item, dict) and item.get("relative_path")
    }
    split_jobs = _split_job_records(json_sources)
    embedded_jobs = _embedded_job_records(json_sources)
    job_records = split_jobs or embedded_jobs
    units = [
        _unit_fact(
            path,
            payload,
            indexed_paths=indexed_paths,
        )
        for path, payload in job_records
    ]
    units.sort(key=lambda item: (str(item["group"]), str(item["name"])))

    group_units: dict[str, list[dict[str, Any]]] = defaultdict(list)
    component_totals: Counter[str] = Counter()
    component_units: dict[str, dict[str, int]] = defaultdict(dict)
    for unit in units:
        group_units[str(unit["group"])].append(unit)
        for component_type, count in unit["component_types"].items():
            component_totals[component_type] += count
            component_units[component_type][str(unit["name"])] = count

    manifest_path, manifest = _first_mapping_source(
        json_sources,
        suffix="scan/manifest.json",
    )
    job_calls = _string_list_mapping(manifest.get("job_calls"))
    for unit in units:
        caller = str(unit["name"])
        callees = [str(item) for item in unit.get("called_units", []) if isinstance(item, str)]
        if callees:
            job_calls[caller] = sorted(set(job_calls.get(caller, ())) | set(callees))
    connection_usage = _string_list_mapping(manifest.get("connection_used_by"))
    job_external_systems = _string_list_mapping(manifest.get("job_external_systems"))
    routines_used_by = _string_list_mapping(manifest.get("routines_used_by"))

    groups = [
        {
            "name": name,
            "unit_count": len(group),
            "component_count": sum(int(item["component_count"]) for item in group),
            "units": [str(item["name"]) for item in group],
        }
        for name, group in sorted(group_units.items())
    ]
    component_types = [
        {
            "name": name,
            "count": count,
            "units": dict(sorted(component_units[name].items())),
        }
        for name, count in sorted(component_totals.items())
    ]
    connections = [
        {
            "name": name,
            "job_count": len(used_by),
            "used_by": used_by,
            "source": _source_locator(manifest_path),
        }
        for name, used_by in sorted(connection_usage.items())
    ]
    call_graph = [
        {
            "caller": caller,
            "callee_count": len(callees),
            "callees": callees,
            "source": _source_locator(manifest_path),
        }
        for caller, callees in sorted(job_calls.items())
    ]
    all_units_have_component_arrays = bool(units) and all(
        bool(item["components_captured"]) for item in units
    )
    total_components = sum(int(item["component_count"]) for item in units)
    kind_counts = Counter(
        str(item["kind"]) for item in units if item.get("kind") in {"job", "route"}
    )
    known_source_presence = [
        bool(item["source_present"])
        for item in units
        if isinstance(item.get("source_present"), bool)
    ]
    all_source_presence_known = len(known_source_presence) == len(units)

    protected_facts: list[dict[str, Any]] = [
        {
            "fact_id": "project.canonical_unit_count",
            "kind": "canonical_unit_count",
            "subject": "project",
            "value": len(units),
        },
        {
            "fact_id": "project.group_count",
            "kind": "group_count",
            "subject": "project",
            "value": len(groups),
        },
        {
            "fact_id": "project.job_count",
            "kind": "job_count",
            "subject": "project",
            "value": kind_counts["job"],
        },
        {
            "fact_id": "project.route_count",
            "kind": "route_count",
            "subject": "project",
            "value": kind_counts["route"],
        },
    ]
    if all_source_presence_known:
        protected_facts.append(
            {
                "fact_id": "project.source_present_count",
                "kind": "source_present_count",
                "subject": "project",
                "value": sum(known_source_presence),
            }
        )
    for group in groups:
        protected_facts.append(
            {
                "fact_id": f"group.{group['name']}.unit_count",
                "kind": "group_unit_count",
                "subject": group["name"],
                "value": group["unit_count"],
            }
        )
        if all_units_have_component_arrays:
            protected_facts.append(
                {
                    "fact_id": f"group.{group['name']}.component_count",
                    "kind": "group_component_count",
                    "subject": group["name"],
                    "value": group["component_count"],
                }
            )
    if all_units_have_component_arrays:
        protected_facts.append(
            {
                "fact_id": "project.component_count",
                "kind": "component_count",
                "subject": "project",
                "value": total_components,
            }
        )
        protected_facts.extend(
            {
                "fact_id": f"component_type.{item['name']}.count",
                "kind": "component_type_count",
                "subject": item["name"],
                "value": item["count"],
            }
            for item in component_types
        )
        protected_facts.extend(
            {
                "fact_id": f"unit.{item['name']}.component_count",
                "kind": "unit_component_count",
                "subject": item["name"],
                "value": item["component_count"],
            }
            for item in units
        )
        protected_facts.extend(
            {
                "fact_id": f"unit.{item['name']}.connection_count",
                "kind": "unit_connection_count",
                "subject": item["name"],
                "value": item["connection_count"],
            }
            for item in units
            if isinstance(item.get("connection_count"), int)
        )
    protected_facts.extend(
        {
            "fact_id": f"connection.{item['name']}.job_count",
            "kind": "connection_job_count",
            "subject": item["name"],
            "value": item["job_count"],
        }
        for item in connections
    )
    protected_facts.extend(
        {
            "fact_id": f"call_graph.{item['caller']}.callee_count",
            "kind": "callee_count",
            "subject": item["caller"],
            "value": item["callee_count"],
        }
        for item in call_graph
    )

    meta_path, meta = _first_mapping_source(
        json_sources,
        suffix="scan/meta.json",
    )
    meta_stats = meta.get("stats") if isinstance(meta.get("stats"), dict) else {}
    source_roots = [
        {
            "id": str(item.get("id") or item.get("path") or ""),
            "path": str(item.get("path") or item.get("id") or ""),
            "label": str(item.get("label") or ""),
            "technical_label": str(item.get("technical_label") or ""),
            "role": str(item.get("role") or ""),
        }
        for item in meta_stats.get("talend_roots", [])
        if isinstance(item, dict) and (item.get("id") or item.get("path"))
    ]

    return {
        "schema_version": 1,
        "authority": "deterministic-frozen-scanner-facts",
        "instructions": {
            "precedence": (
                "For counts, inventories, call edges, and connection usage, this ledger "
                "overrides model inference and prose in preserved context bundles."
            ),
            "runtime_boundary": (
                "Absence from this static ledger is not proof of absence at runtime."
            ),
        },
        "sources": sorted(source_hash_inputs),
        "project": {
            "id": project_id,
            "platform": meta.get("platform"),
            "source_roots": source_roots,
            "source": _source_locator(meta_path),
        },
        "preflight": _ledger_preflight_summary(
            json_sources=json_sources,
            split_jobs=split_jobs,
            embedded_jobs=embedded_jobs,
            units=units,
            manifest=manifest,
            meta=meta,
        ),
        "totals": {
            "canonical_units": len(units),
            "groups": len(groups),
            "jobs": kind_counts["job"],
            "routes": kind_counts["route"],
            "components": total_components if all_units_have_component_arrays else None,
            "connections": (
                sum(
                    int(item["connection_count"])
                    for item in units
                    if isinstance(item.get("connection_count"), int)
                )
                if units
                else None
            ),
            "source_files_present": (
                sum(known_source_presence) if all_source_presence_known else None
            ),
            "job_call_edges": sum(len(value) for value in job_calls.values()),
            "connection_usage_edges": sum(len(value) for value in connection_usage.values()),
            "inactive_components": (
                sum(int(item["inactive_component_count"]) for item in units)
                if all_units_have_component_arrays
                else None
            ),
        },
        "groups": groups,
        "canonical_units": units,
        "component_types": component_types,
        "call_graph": call_graph,
        "connections": connections,
        "job_external_systems": job_external_systems,
        "routines_used_by": routines_used_by,
        "protected_facts": protected_facts,
        "static_evidence_unknowns": [
            "runtime execution frequency and scheduling outside scanned sources",
            "production traffic, latency, failure rates, and data volumes",
            "external monitoring, alerting, gateways, and network controls",
            "deployed configuration values hidden or redacted from the frozen scan",
        ],
    }


def validate_fact_ledger_preflight(fact_ledger: dict[str, Any]) -> dict[str, Any]:
    """Fail-closed diagnostics for a scanner-derived grounded ledger.

    The compiler remains usable for partial/static evidence, while Grounded
    execution requires this report to pass before a provider session exists.
    """

    preflight = fact_ledger.get("preflight")
    if not isinstance(preflight, dict):
        return {
            "schema_version": 1,
            "status": "failed",
            "issue_count": 1,
            "issues": [{"code": "missing_preflight_metadata"}],
        }
    raw_issues = preflight.get("issues")
    issues = (
        raw_issues if isinstance(raw_issues, list) else [{"code": "invalid_preflight_metadata"}]
    )
    return {
        "schema_version": 1,
        "status": "pass" if not issues else "failed",
        "issue_count": len(issues),
        "issues": issues,
    }


def validate_document_claims(
    documents: dict[str, str],
    fact_ledger: dict[str, Any],
) -> dict[str, Any]:
    """Return high-confidence mismatches between prose and protected scanner facts."""

    issues: list[dict[str, Any]] = []
    facts = {
        (str(item["kind"]), str(item["subject"])): int(item["value"])
        for item in fact_ledger.get("protected_facts", [])
        if isinstance(item, dict)
        and isinstance(item.get("value"), int)
        and not isinstance(item.get("value"), bool)
        and item.get("kind")
        and item.get("subject")
    }
    unit_names = {
        str(item["name"])
        for item in fact_ledger.get("canonical_units", [])
        if isinstance(item, dict) and item.get("name")
    }
    unit_versions = {
        str(item["name"]): str(item["version"])
        for item in fact_ledger.get("canonical_units", [])
        if isinstance(item, dict)
        and item.get("name")
        and item.get("version")
        and item.get("version") != "unknown"
    }
    call_edges = {
        (str(item["caller"]), str(callee))
        for item in fact_ledger.get("call_graph", [])
        if isinstance(item, dict)
        for callee in item.get("callees", [])
        if isinstance(callee, str)
    }

    for document, text in documents.items():
        for line_number, line in _prose_and_table_lines(text):
            _check_table_component_count(issues, facts, document, line_number, line)
            _check_subject_counts(issues, facts, document, line_number, line)
            _check_project_totals(issues, facts, document, line_number, line)
            _check_unit_versions(
                issues,
                unit_versions,
                document,
                line_number,
                line,
            )
            _check_call_edges(
                issues,
                unit_names,
                call_edges,
                document,
                line_number,
                line,
            )
        _check_semantic_overclaims(issues, document, text)

    component_inventory = documents.get("02_COMPONENT_INVENTORY.md")
    if component_inventory is not None:
        missing_units = sorted(
            name for name in unit_names if name.casefold() not in component_inventory.casefold()
        )
        if missing_units:
            issues.append(
                {
                    "code": "missing_canonical_units",
                    "document": "02_COMPONENT_INVENTORY.md",
                    "line": None,
                    "subject": "canonical unit inventory",
                    "claimed": None,
                    "expected": len(unit_names),
                    "detail": "Missing canonical units: " + ", ".join(missing_units),
                }
            )

    return {
        "schema_version": 1,
        "status": "pass" if not issues else "repair_required",
        "issue_count": len(issues),
        "issues": issues,
    }


def validate_protected_document_facts(
    documents: dict[str, str],
    fact_ledger: dict[str, Any],
) -> dict[str, Any]:
    """Validate only deterministic claims safe enough to block publication.

    This intentionally avoids the broader advisory prose matcher.  It protects
    explicit project kind totals, per-unit component/connection rows, and source
    presence without interpreting unrelated table numbers.
    """

    units = {
        str(item["name"]): item
        for item in fact_ledger.get("canonical_units", [])
        if isinstance(item, dict) and item.get("name")
    }
    totals = fact_ledger.get("totals")
    totals = totals if isinstance(totals, dict) else {}
    issues: list[dict[str, Any]] = []
    for document, text in documents.items():
        current_unit: str | None = None
        for line_number, line in _prose_and_table_lines(text):
            heading_unit = _heading_unit(line, units)
            if heading_unit is not None:
                current_unit = heading_unit
            _check_kind_totals(
                issues,
                document=document,
                line_number=line_number,
                line=line,
                expected_jobs=totals.get("jobs"),
                expected_routes=totals.get("routes"),
            )
            _check_missing_source_claim(
                issues,
                document=document,
                line_number=line_number,
                line=line,
                current_unit=current_unit,
                units=units,
                totals=totals,
            )
            _check_explicit_unit_counts(
                issues,
                document=document,
                line_number=line_number,
                line=line,
                current_unit=current_unit,
                units=units,
            )
    return _validation_report(issues)


def validate_evidence_claims(
    artifacts: dict[str, Any],
    fact_ledger: dict[str, Any],
) -> dict[str, Any]:
    """Reject structured evidence that mutates protected scanner facts."""

    units = {
        str(item["name"]): item
        for item in fact_ledger.get("canonical_units", [])
        if isinstance(item, dict) and item.get("name")
    }
    totals = fact_ledger.get("totals")
    totals = totals if isinstance(totals, dict) else {}
    issues: list[dict[str, Any]] = []
    for artifact, payload in artifacts.items():
        _walk_evidence(
            payload,
            artifact=artifact,
            path="$",
            units=units,
            totals=totals,
            issues=issues,
        )
    return _validation_report(issues)


def _validation_report(issues: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "pass" if not issues else "repair_required",
        "issue_count": len(issues),
        "issues": issues,
    }


def _heading_unit(
    line: str,
    units: dict[str, dict[str, Any]],
) -> str | None:
    if not line.lstrip().startswith("#"):
        return None
    folded = line.casefold()
    matches = [name for name in units if name.casefold() in folded]
    return max(matches, key=len) if matches else None


def _check_kind_totals(
    issues: list[dict[str, Any]],
    *,
    document: str,
    line_number: int,
    line: str,
    expected_jobs: Any,
    expected_routes: Any,
) -> None:
    folded = line.casefold()
    if "job" not in folded or "route" not in folded:
        return
    if not any(
        marker in folded for marker in ("comprises", "consists", "contains", "canonical unit")
    ):
        return
    job_match = re.search(r"(?P<count>\d[\d,]*)\s+(?:talend\s+)?jobs?\b", line, re.IGNORECASE)
    route_match = re.search(r"(?P<count>\d[\d,]*)\s+(?:camel\s+)?routes?\b", line, re.IGNORECASE)
    if (
        job_match is not None
        and isinstance(expected_jobs, int)
        and not isinstance(expected_jobs, bool)
    ):
        _append_protected_issue(
            issues,
            code="project_job_count_mismatch",
            document=document,
            line_number=line_number,
            subject="project jobs",
            claimed=_parse_count(job_match.group("count")),
            expected=expected_jobs,
        )
    if (
        route_match is not None
        and isinstance(expected_routes, int)
        and not isinstance(expected_routes, bool)
    ):
        _append_protected_issue(
            issues,
            code="project_route_count_mismatch",
            document=document,
            line_number=line_number,
            subject="project routes",
            claimed=_parse_count(route_match.group("count")),
            expected=expected_routes,
        )


def _check_missing_source_claim(
    issues: list[dict[str, Any]],
    *,
    document: str,
    line_number: int,
    line: str,
    current_unit: str | None,
    units: dict[str, dict[str, Any]],
    totals: dict[str, Any],
) -> None:
    folded = line.casefold()
    table_missing = "source on disk" in folded and any(
        marker in folded for marker in ("not found", "missing", "absent", "❌")
    )
    if _MISSING_SOURCE.search(line) is None and not table_missing:
        return
    matching_units = [
        name
        for name, unit in units.items()
        if name.casefold() in line.casefold() and unit.get("source_present") is True
    ]
    if current_unit is not None and units[current_unit].get("source_present") is True:
        matching_units.append(current_unit)
    for name in sorted(set(matching_units)):
        issues.append(
            {
                "code": "source_presence_mismatch",
                "document": document,
                "line": line_number,
                "subject": name,
                "claimed": "missing",
                "expected": "present",
            }
        )
    present = totals.get("source_files_present")
    canonical = totals.get("canonical_units")
    if (
        isinstance(present, int)
        and isinstance(canonical, int)
        and present == canonical
        and not matching_units
        and re.search(r"\b\d+\s+of\s+\d+\s+units?\b", line, re.IGNORECASE)
    ):
        issues.append(
            {
                "code": "source_presence_mismatch",
                "document": document,
                "line": line_number,
                "subject": "project source files",
                "claimed": "missing",
                "expected": f"{present}/{canonical} present",
            }
        )


def _check_explicit_unit_counts(
    issues: list[dict[str, Any]],
    *,
    document: str,
    line_number: int,
    line: str,
    current_unit: str | None,
    units: dict[str, dict[str, Any]],
) -> None:
    candidates = [name for name in units if name.casefold() in line.casefold()]
    if current_unit is not None:
        candidates.append(current_unit)
    for name in sorted(set(candidates)):
        unit = units[name]
        component_match = re.search(
            r"(?:component\s+count\s*\|\s*|"
            + re.escape(name)
            + r"(?:(?!\n).){0,100}?)(?P<count>\d[\d,]*)\s+components?\b",
            line,
            re.IGNORECASE,
        )
        if component_match is None and current_unit == name:
            component_match = re.search(
                r"\|\s*component\s+count\s*\|\s*(?P<count>\d[\d,]*)\s*\|",
                line,
                re.IGNORECASE,
            )
        connection_match = re.search(
            r"(?:connection\s+count\s*\|\s*|"
            + re.escape(name)
            + r"(?:(?!\n).){0,100}?)(?P<count>\d[\d,]*)\s+connections?\b",
            line,
            re.IGNORECASE,
        )
        if connection_match is None and current_unit == name:
            connection_match = re.search(
                r"\|\s*connection\s+count\s*\|\s*(?P<count>\d[\d,]*)\s*\|",
                line,
                re.IGNORECASE,
            )
        if component_match is not None:
            _append_protected_issue(
                issues,
                code="unit_component_count_mismatch",
                document=document,
                line_number=line_number,
                subject=name,
                claimed=_parse_count(component_match.group("count")),
                expected=unit.get("component_count"),
            )
        if connection_match is not None:
            _append_protected_issue(
                issues,
                code="unit_connection_count_mismatch",
                document=document,
                line_number=line_number,
                subject=name,
                claimed=_parse_count(connection_match.group("count")),
                expected=unit.get("connection_count"),
            )


def _append_protected_issue(
    issues: list[dict[str, Any]],
    *,
    code: str,
    document: str,
    line_number: int,
    subject: str,
    claimed: Any,
    expected: Any,
) -> None:
    if expected is None or claimed == expected:
        return
    issues.append(
        {
            "code": code,
            "document": document,
            "line": line_number,
            "subject": subject,
            "claimed": claimed,
            "expected": expected,
        }
    )


def _walk_evidence(
    value: Any,
    *,
    artifact: str,
    path: str,
    units: dict[str, dict[str, Any]],
    totals: dict[str, Any],
    issues: list[dict[str, Any]],
) -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            _walk_evidence(
                item,
                artifact=artifact,
                path=f"{path}[{index}]",
                units=units,
                totals=totals,
                issues=issues,
            )
        return
    if not isinstance(value, dict):
        return

    subject = next(
        (
            str(value[key])
            for key in ("name", "unit", "unit_name", "job_id")
            if isinstance(value.get(key), str) and str(value[key]) in units
        ),
        None,
    )
    if subject is not None:
        unit = units[subject]
        for keys, expected, fact_name in (
            (
                ("component_count", "components", "total_components"),
                unit.get("component_count"),
                "component_count",
            ),
            (
                ("connection_count", "connections", "total_connections"),
                unit.get("connection_count"),
                "connection_count",
            ),
        ):
            for key in keys:
                claimed = value.get(key)
                if not isinstance(claimed, int) or isinstance(claimed, bool):
                    continue
                _append_evidence_issue(
                    issues,
                    artifact=artifact,
                    path=f"{path}.{key}",
                    code=f"evidence_{fact_name}_mismatch",
                    subject=subject,
                    claimed=claimed,
                    expected=expected,
                )
        for key in ("kind", "type", "artifact_kind"):
            claimed_kind = value.get(key)
            if isinstance(claimed_kind, str) and claimed_kind.casefold() in {"job", "route"}:
                _append_evidence_issue(
                    issues,
                    artifact=artifact,
                    path=f"{path}.{key}",
                    code="evidence_unit_kind_mismatch",
                    subject=subject,
                    claimed=claimed_kind.casefold(),
                    expected=unit.get("kind"),
                )
        for key in ("source_present", "source_file_present"):
            if isinstance(value.get(key), bool) and unit.get("source_present") is not None:
                _append_evidence_issue(
                    issues,
                    artifact=artifact,
                    path=f"{path}.{key}",
                    code="evidence_source_presence_mismatch",
                    subject=subject,
                    claimed=value[key],
                    expected=unit.get("source_present"),
                )

    for key in ("missing_source_artifacts", "missing_sources", "missing_source_files"):
        raw_missing = value.get(key)
        if not isinstance(raw_missing, list):
            continue
        for item in raw_missing:
            name = (
                str(item)
                if isinstance(item, str)
                else str(item.get("name") or item.get("unit") or "")
                if isinstance(item, dict)
                else ""
            )
            if name in units and units[name].get("source_present") is True:
                _append_evidence_issue(
                    issues,
                    artifact=artifact,
                    path=f"{path}.{key}",
                    code="evidence_source_presence_mismatch",
                    subject=name,
                    claimed="missing",
                    expected="present",
                )

    if path == "$":
        for keys, expected, fact_name in (
            (("job_count", "jobs", "total_jobs"), totals.get("jobs"), "job_count"),
            (
                ("route_count", "routes", "total_routes"),
                totals.get("routes"),
                "route_count",
            ),
            (
                ("component_count", "components", "total_components"),
                totals.get("components"),
                "component_count",
            ),
            (
                ("connection_count", "connections", "total_connections"),
                totals.get("connections"),
                "connection_count",
            ),
        ):
            for key in keys:
                claimed = value.get(key)
                if not isinstance(claimed, int) or isinstance(claimed, bool):
                    continue
                _append_evidence_issue(
                    issues,
                    artifact=artifact,
                    path=f"$.{key}",
                    code=f"evidence_project_{fact_name}_mismatch",
                    subject="project",
                    claimed=claimed,
                    expected=expected,
                )

    for key, nested in value.items():
        _walk_evidence(
            nested,
            artifact=artifact,
            path=f"{path}.{key}",
            units=units,
            totals=totals,
            issues=issues,
        )


def _append_evidence_issue(
    issues: list[dict[str, Any]],
    *,
    artifact: str,
    path: str,
    code: str,
    subject: str,
    claimed: Any,
    expected: Any,
) -> None:
    if expected is None or claimed == expected:
        return
    issues.append(
        {
            "code": code,
            "artifact": artifact,
            "path": path,
            "subject": subject,
            "claimed": claimed,
            "expected": expected,
        }
    )


def _split_job_records(
    json_sources: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    records = []
    for path, payload in json_sources.items():
        if not path.startswith("scan/jobs/") or not isinstance(payload, dict):
            continue
        if isinstance(payload.get("name"), str):
            records.append((path, payload))
    return records


def _embedded_job_records(
    json_sources: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    for path, payload in sorted(json_sources.items()):
        if not path.endswith("scan_result.json") or not isinstance(payload, dict):
            continue
        jobs = payload.get("jobs")
        if isinstance(jobs, list):
            return [
                (path, item)
                for item in jobs
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            ]
    return []


def _ledger_preflight_summary(
    *,
    json_sources: dict[str, Any],
    split_jobs: list[tuple[str, dict[str, Any]]],
    embedded_jobs: list[tuple[str, dict[str, Any]]],
    units: list[dict[str, Any]],
    manifest: dict[str, Any],
    meta: dict[str, Any],
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    split_names = _record_names(split_jobs)
    embedded_names = _record_names(embedded_jobs)
    unit_names = {str(item["name"]) for item in units}
    scanner_job_inputs = sum(path.startswith("scan/jobs/") for path in json_sources)

    if scanner_job_inputs and not units:
        issues.append(
            {
                "code": "scanner_jobs_without_ledger_units",
                "scanner_job_input_count": scanner_job_inputs,
            }
        )
    if split_jobs and embedded_jobs and split_names != embedded_names:
        issues.append(
            {
                "code": "split_embedded_unit_mismatch",
                "split_count": len(split_names),
                "embedded_count": len(embedded_names),
                "missing_from_split": sorted(embedded_names - split_names),
                "missing_from_embedded": sorted(split_names - embedded_names),
            }
        )

    embedded_totals = [
        payload.get("stats", {}).get("total_jobs")
        for path, payload in json_sources.items()
        if path.endswith("scan_result.json")
        and isinstance(payload, dict)
        and isinstance(payload.get("stats"), dict)
    ]
    expected_totals = [
        value for value in embedded_totals if isinstance(value, int) and not isinstance(value, bool)
    ]
    if expected_totals and any(value != len(units) for value in expected_totals):
        issues.append(
            {
                "code": "scanner_total_reconciliation_mismatch",
                "ledger_count": len(units),
                "scanner_totals": sorted(set(expected_totals)),
            }
        )

    meta_stats = meta.get("stats") if isinstance(meta.get("stats"), dict) else {}
    expected_jobs = meta_stats.get("job_count")
    expected_routes = meta_stats.get("route_count")
    derived_jobs = sum(item.get("kind") == "job" for item in units)
    derived_routes = sum(item.get("kind") == "route" for item in units)
    derived_known_kinds = sum(
        item.get("kind") in {"job", "route", "joblet", "routelet"} for item in units
    )
    if (
        isinstance(expected_jobs, int)
        and not isinstance(expected_jobs, bool)
        and expected_jobs != derived_jobs
    ) or (
        isinstance(expected_routes, int)
        and not isinstance(expected_routes, bool)
        and expected_routes != derived_routes
    ):
        issues.append(
            {
                "code": "scanner_kind_reconciliation_mismatch",
                "ledger_jobs": derived_jobs,
                "ledger_routes": derived_routes,
                "scanner_jobs": expected_jobs,
                "scanner_routes": expected_routes,
            }
        )

    manifest_groups = manifest.get("job_groups")
    if isinstance(manifest_groups, dict):
        # Composite Talend manifests use ``job_files`` as the identity index.
        # Display names are not stable identities: the same job name can occur
        # in several source roots (and the embedded jobs list can contain
        # duplicate display names). Compare names only for the small legacy
        # manifest shape that has no identity index.
        manifest_files = manifest.get("job_files")
        if isinstance(manifest_files, dict):
            manifest_count = len(manifest_files)
            if manifest_count != len(units):
                issues.append(
                    {
                        "code": "manifest_unit_reconciliation_mismatch",
                        "manifest_count": manifest_count,
                        "ledger_count": len(units),
                    }
                )
        else:
            manifest_names = {str(name) for name in manifest_groups}
            if manifest_names != unit_names:
                issues.append(
                    {
                        "code": "manifest_unit_reconciliation_mismatch",
                        "manifest_count": len(manifest_names),
                        "ledger_count": len(unit_names),
                        "missing_from_manifest": sorted(unit_names - manifest_names),
                        "missing_from_ledger": sorted(manifest_names - unit_names),
                    }
                )

    incomplete_units = sorted(
        str(item["name"]) for item in units if not bool(item.get("components_captured"))
    )
    if incomplete_units:
        issues.append(
            {
                "code": "incomplete_component_inventory",
                "unit_count": len(incomplete_units),
                "units": incomplete_units,
            }
        )

    return {
        "status": "pass" if not issues else "failed",
        "scanner_job_input_count": scanner_job_inputs,
        "split_unit_count": len(split_names),
        "embedded_unit_count": len(embedded_names),
        "ledger_unit_count": len(unit_names),
        "component_totals_complete": bool(units) and not incomplete_units,
        "kind_totals_complete": derived_known_kinds == len(units),
        "issues": issues,
    }


def _record_names(
    records: list[tuple[str, dict[str, Any]]],
) -> set[str]:
    return {str(payload["name"]) for _, payload in records if isinstance(payload.get("name"), str)}


def _unit_fact(
    path: str,
    payload: dict[str, Any],
    *,
    indexed_paths: set[str],
) -> dict[str, Any]:
    raw_components = payload.get("components")
    components = raw_components if isinstance(raw_components, list) else []
    component_types = Counter(
        str(component.get("component_type"))
        for component in components
        if isinstance(component, dict) and component.get("component_type")
    )
    inactive = [
        str(component.get("unique_name") or component.get("component_type") or "unknown")
        for component in components
        if isinstance(component, dict) and _component_is_inactive(component)
    ]
    callees = _called_units(payload, components)
    raw_group = payload.get("group")
    group = str(raw_group).strip() if raw_group else "ungrouped"
    source_design = str(payload.get("source_design") or "").strip().casefold()
    is_esb_route = payload.get("is_esb_route")
    artifact_kind = str(payload.get("artifact_kind") or "").strip().casefold()
    if artifact_kind in {"job", "route", "joblet", "routelet"}:
        kind = artifact_kind
    elif is_esb_route is True or source_design in {"route", "routes"}:
        kind = "route"
    elif is_esb_route is False or source_design in {"job", "jobs", "process"}:
        kind = "job"
    else:
        prefixes = {
            str(component.get("component_type") or "")[:1].casefold()
            for component in components
            if isinstance(component, dict)
        }
        kind = "route" if prefixes == {"c"} else "job" if prefixes == {"t"} else "unknown"
    source_root = str(payload.get("source_root") or "").strip().replace("\\", "/")
    project_path = str(payload.get("path") or "").strip().replace("\\", "/")
    direct_project_path = "/".join(
        part.strip("/") for part in (source_root, project_path) if part.strip("/")
    )
    design_project_path = "/".join(
        part.strip("/") for part in (source_root, source_design, project_path) if part.strip("/")
    )
    indexed_project_path = next(
        (
            candidate
            for candidate in (direct_project_path, design_project_path)
            if candidate in indexed_paths
        ),
        None,
    )
    canonical_project_path = (
        indexed_project_path or design_project_path or direct_project_path or project_path or None
    )
    # A canonical scanner unit was parsed from its Talend design file.  That
    # record is stronger presence evidence than ``files_index.json``, which
    # intentionally omits some Route ``.item`` files from its general index.
    source_present = True if project_path else None
    source_presence_evidence = (
        "files_index"
        if indexed_project_path is not None
        else "canonical_scanner_unit"
        if project_path
        else "unknown"
    )
    return {
        "name": str(payload["name"]),
        "kind": kind,
        "group": group,
        "version": str(payload.get("version") or "unknown"),
        "active": payload.get("active"),
        "source": _source_locator(path),
        "project_source_path": canonical_project_path,
        "source_present": source_present,
        "source_presence_evidence": source_presence_evidence,
        "components_captured": isinstance(raw_components, list),
        "component_count": len(components),
        "component_types": dict(sorted(component_types.items())),
        "inactive_component_count": len(inactive),
        "inactive_components": inactive,
        "connection_count": (
            len(payload["connections"]) if isinstance(payload.get("connections"), list) else None
        ),
        "called_units": callees,
    }


def _called_units(
    payload: dict[str, Any],
    components: list[Any],
) -> list[str]:
    refs = payload.get("trunjob_refs")
    callees = (
        {
            str(item["child_job"])
            for item in refs
            if isinstance(item, dict) and item.get("child_job")
        }
        if isinstance(refs, list)
        else set()
    )
    for component in components:
        if not isinstance(component, dict):
            continue
        component_type = str(component.get("component_type") or "")
        if component_type.casefold() not in {"ctalendjob", "trunjob"}:
            continue
        params = component.get("params")
        if not isinstance(params, list):
            continue
        for parameter in params:
            if not isinstance(parameter, dict):
                continue
            name = str(parameter.get("name") or "").casefold()
            value = str(parameter.get("value") or "").strip().strip('"')
            if name in {"selected_job_name", "process"} and value and not value.startswith("_"):
                callees.add(value)
    return sorted(callees)


def _first_mapping_source(
    json_sources: dict[str, Any],
    *,
    suffix: str,
) -> tuple[str, dict[str, Any]]:
    for path, payload in sorted(json_sources.items()):
        if path.endswith(suffix) and isinstance(payload, dict):
            return path, payload
    return suffix, {}


def _first_source(
    json_sources: dict[str, Any],
    *,
    suffix: str,
) -> tuple[str | None, Any]:
    for path, payload in sorted(json_sources.items()):
        if path.endswith(suffix):
            return path, payload
    return None, None


def _string_list_mapping(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, list[str]] = {}
    for key, raw_items in value.items():
        if not isinstance(raw_items, list):
            continue
        result[str(key)] = [str(item) for item in raw_items if isinstance(item, str)]
    return result


def _logical_relative_path(logical_identity: str) -> str:
    value = logical_identity.split(":", 1)[-1].strip().replace("\\", "/")
    relative = PurePosixPath(value)
    if (
        not value
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        return PurePosixPath(Path(value).name or "source.json").as_posix()
    return relative.as_posix()


def _source_locator(relative_path: str) -> str:
    return f"sources/{relative_path}"


def _prose_and_table_lines(text: str) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    in_fence = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            result.append((line_number, line))
    return result


def _check_table_component_count(
    issues: list[dict[str, Any]],
    facts: dict[tuple[str, str], int],
    document: str,
    line_number: int,
    line: str,
) -> None:
    match = _MARKDOWN_TABLE_COUNT.match(line)
    if match is None:
        return
    subject = match.group("subject")
    expected = facts.get(("component_type_count", subject))
    if expected is None:
        expected = next(
            (
                value
                for (kind, name), value in facts.items()
                if kind == "component_type_count" and name.casefold() == subject.casefold()
            ),
            None,
        )
    if expected is None:
        return
    _append_count_issue(
        issues,
        document=document,
        line_number=line_number,
        subject=subject,
        claimed=_parse_count(match.group("count")),
        expected=expected,
        text=line,
    )


def _check_subject_counts(
    issues: list[dict[str, Any]],
    facts: dict[tuple[str, str], int],
    document: str,
    line_number: int,
    line: str,
) -> None:
    for (kind, subject), expected in facts.items():
        if kind == "group_unit_count":
            pattern = re.compile(
                rf"{_exact_subject(subject)}\s*\(\s*{_INTEGER}\s+jobs?\b",
                re.IGNORECASE,
            )
            _append_matches(issues, pattern, document, line_number, subject, expected, line)
        elif kind == "group_component_count":
            if "group" not in line.casefold():
                continue
            pattern = re.compile(
                rf"{_exact_subject(subject)}\s*\([^)]*?{_INTEGER}\s+components?\b",
                re.IGNORECASE,
            )
            _append_matches(issues, pattern, document, line_number, subject, expected, line)
        elif kind == "unit_component_count":
            subject_pattern = _exact_subject(subject)
            if "|" in line:
                pattern = re.compile(
                    rf"\|\s*{subject_pattern}\s*\|"
                    rf"(?:(?!\n).){{0,160}}?{_INTEGER}\s+components?\b",
                    re.IGNORECASE,
                )
            else:
                pattern = re.compile(
                    rf"{subject_pattern}(?:(?!\n).){{0,100}}?"
                    rf"{_INTEGER}\s+components?\b",
                    re.IGNORECASE,
                )
            _append_matches(issues, pattern, document, line_number, subject, expected, line)
        elif kind == "connection_job_count":
            pattern = re.compile(
                rf"{_exact_subject(subject)}"
                rf"(?:(?!{re.escape(subject)}).){{0,180}}?"
                rf"(?:used\s+by\s+)?{_INTEGER}(?:\s+of\s+\d+)?\s+jobs?\b",
                re.IGNORECASE,
            )
            _append_matches(issues, pattern, document, line_number, subject, expected, line)
        elif kind == "callee_count":
            pattern = re.compile(
                rf"{_exact_subject(subject)}\s*"
                rf"(?:->|calls?|dispatches?|delegates?)"
                rf"(?:(?!\n).){{0,60}}?{_INTEGER}\s+"
                rf"(?:child(?:ren)?|child jobs?|callees?)\b",
                re.IGNORECASE,
            )
            _append_matches(
                issues,
                pattern,
                document,
                line_number,
                f"{subject} callees",
                expected,
                line,
            )


def _check_project_totals(
    issues: list[dict[str, Any]],
    facts: dict[tuple[str, str], int],
    document: str,
    line_number: int,
    line: str,
) -> None:
    """Flag a project-wide canonical-unit/component count that disagrees with the ledger.

    Each pattern binds its count directly to the exact phrase that makes it a
    project-total claim ("<N> canonical units", "project contains exactly
    <N> jobs", "<N> total components"). A line can legitimately carry more
    than one unrelated number claim (e.g. a project-total sentence next to a
    per-unit callee-count example); matching on "does this line contain the
    word canonical anywhere" and then grabbing any "<N> units" in the whole
    line -- the previous approach -- attributed those unrelated numbers to
    the project total. Binding the trigger phrase and the count into one
    pattern removes that cross-claim leakage without losing real mismatches.
    """

    expected_units = facts.get(("canonical_unit_count", "project"))
    if expected_units is not None:
        for pattern in (_PROJECT_CANONICAL_UNITS, _PROJECT_CONTAINS_EXACTLY):
            for match in pattern.finditer(line):
                _append_count_issue(
                    issues,
                    document=document,
                    line_number=line_number,
                    subject="project canonical units",
                    claimed=_parse_count(match.group("count")),
                    expected=expected_units,
                    text=line,
                )
    expected_components = facts.get(("component_count", "project"))
    if expected_components is not None:
        for pattern in (_PROJECT_TOTAL_COMPONENTS_PRE, _PROJECT_TOTAL_COMPONENTS_POST):
            for match in pattern.finditer(line):
                _append_count_issue(
                    issues,
                    document=document,
                    line_number=line_number,
                    subject="project components",
                    claimed=_parse_count(match.group("count")),
                    expected=expected_components,
                    text=line,
                )


def _check_semantic_overclaims(
    issues: list[dict[str, Any]],
    document: str,
    text: str,
) -> None:
    paragraphs = re.split(r"\n\s*\n", text)
    line_offset = 1
    for paragraph in paragraphs:
        folded = " ".join(paragraph.casefold().split())
        if (
            "need_auth=false" in folded
            or "need_auth = false" in folded
            or "`need_auth` | false" in folded
        ) and (
            "bypass downstream" in folded
            or "bypasses downstream" in folded
            or "custom checks may never execute" in folded
            or "checks built into the code may never execute" in folded
        ):
            issues.append(
                {
                    "code": "need_auth_semantics",
                    "document": document,
                    "line": line_offset,
                    "subject": "NEED_AUTH=false",
                    "claimed": None,
                    "expected": None,
                    "detail": (
                        "NEED_AUTH=false disables framework authentication; it does not "
                        "by itself bypass or prevent downstream custom checks."
                    ),
                }
            )
        unsupported_absence = (
            "no scheduling exists" in folded
            or "no cron triggers exist" in folded
            or "no independent rest endpoints, cron triggers, or scheduling exists" in folded
        )
        qualified = any(
            marker in folded
            for marker in (
                "scan",
                "scanned",
                "metadata",
                "evidence",
                "codebase",
                "static",
            )
        )
        if unsupported_absence and not qualified:
            issues.append(
                {
                    "code": "static_absence_overclaim",
                    "document": document,
                    "line": line_offset,
                    "subject": "runtime scheduling",
                    "claimed": None,
                    "expected": None,
                    "detail": (
                        "Static scan absence must be stated as 'not found in scanned "
                        "sources', not as proof that runtime scheduling does not exist."
                    ),
                }
            )
        unsupported_reachability = any(
            claim in folded
            for claim in (
                "any client can invoke",
                "any client can call",
                "publicly accessible",
                "reachable by anyone",
                "any unauthenticated client can",
            )
        )
        runtime_supported = any(
            marker in folded
            for marker in (
                "runtime evidence",
                "network evidence",
                "gateway evidence",
                "traffic observation",
            )
        )
        if unsupported_reachability and not runtime_supported:
            issues.append(
                {
                    "code": "network_reachability_overclaim",
                    "document": document,
                    "line": line_offset,
                    "subject": "network reachability",
                    "claimed": None,
                    "expected": None,
                    "detail": (
                        "Static scanner/authentication configuration cannot prove "
                        "which clients can reach or invoke a deployed endpoint."
                    ),
                }
            )
        line_offset += paragraph.count("\n") + 2


def _check_unit_versions(
    issues: list[dict[str, Any]],
    versions: dict[str, str],
    document: str,
    line_number: int,
    line: str,
) -> None:
    for subject, expected in versions.items():
        pattern = re.compile(
            rf"{_exact_subject(subject)}(?:(?!\n).){{0,80}}?"
            r"(?:version|v)\s*[`'\"]?(?P<version>[0-9][A-Za-z0-9_.-]*)",
            re.IGNORECASE,
        )
        for match in pattern.finditer(line):
            claimed = match.group("version").rstrip(".,;:`'\"")
            if claimed != expected:
                issues.append(
                    {
                        "code": "unit_version_mismatch",
                        "document": document,
                        "line": line_number,
                        "subject": subject,
                        "claimed": claimed,
                        "expected": expected,
                        "detail": line.strip()[:500],
                    }
                )


_CALL_VERB = r"(?:->|calls?|invokes?|dispatches?)"
_CALL_VERB_RE = re.compile(_CALL_VERB, re.IGNORECASE)
# How close a call verb must be to the name it belongs to. Real prose states
# a call directly, e.g. "`Caller` calls Callee" or "Caller -> Callee" (a gap
# of 1-3 characters for punctuation/backticks). This bound intentionally
# excludes a verb that actually belongs to a DIFFERENT name mentioned later
# in the same sentence -- the shape that caused the run-51 false positives
# (a citation sentence bundling "<caller A> calls 6 units, <caller B> calls
# 6" as two independent examples).
_CALL_VERB_GAP = r"(?:(?!\n).){0,15}?"


@functools.lru_cache(maxsize=4096)
def _compiled_exact_subject(subject: str) -> re.Pattern[str]:
    """Cache one compiled whole-identifier pattern per subject name.

    ``re.search``/``re.finditer`` called with a pattern STRING recompile on
    every call unless the string is already in the ``re`` module's own
    cache; that module-level cache is a small, size-bounded dict that is
    cleared wholesale (not LRU-evicted) once it fills up. A document with
    hundreds of canonical units easily cycles past that limit, which was
    silently turning every call-edge check into O(units^2) fresh regex
    COMPILATIONS per line rather than per subject. Owning a larger,
    dedicated cache keeps each subject's pattern compiled exactly once for
    the life of the process.
    """

    return re.compile(_exact_subject(subject), re.IGNORECASE)


@functools.lru_cache(maxsize=4096)
def _compiled_call_verb_probe(subject: str) -> re.Pattern[str]:
    return re.compile(rf"{_exact_subject(subject)}{_CALL_VERB_GAP}{_CALL_VERB}", re.IGNORECASE)


def _check_call_edges(
    issues: list[dict[str, Any]],
    unit_names: set[str],
    call_edges: set[tuple[str, str]],
    document: str,
    line_number: int,
    line: str,
) -> None:
    for caller in unit_names:
        verb_match = _compiled_call_verb_probe(caller).search(line)
        if verb_match is None:
            continue
        others = unit_names - {caller}
        tail = line[verb_match.end() :]
        clause = tail[: _call_clause_end(tail, others)]
        for callee in others:
            if (
                _compiled_exact_subject(callee).search(clause)
                and (caller, callee) not in call_edges
            ):
                issues.append(
                    {
                        "code": "call_edge_mismatch",
                        "document": document,
                        "line": line_number,
                        "subject": caller,
                        "claimed": callee,
                        "expected": None,
                        "detail": line.strip()[:500],
                    }
                )


def _call_clause_end(tail: str, other_units: set[str]) -> int:
    """Bound one caller's clause to before the next INDEPENDENT caller mention.

    A single citation-heavy sentence commonly bundles more than one
    caller/callee example together, e.g. "AssetCheck_Main_Job calls 6 units,
    VISE_SP_VIP_OTL calls 6". Searching the whole remainder of the line for
    any known unit name -- the previous behaviour -- reads the SECOND
    caller's name as a callee of the FIRST caller. A later mention of
    another known unit only starts a new, independent clause when it is
    ITSELF immediately (within ``_CALL_VERB_GAP``) followed by a call verb;
    otherwise it is a legitimate callee in THIS caller's own list (e.g. "X
    calls A, B, and C" must still resolve A/B/C as X's callees). The same
    tight adjacency bound as the caller lookup is required here -- a looser
    bound would itself misread a THIRD name's verb as belonging to this
    "other" name, recreating the same cross-attribution bug one level down.
    """

    clause_end = len(tail)
    for other in other_units:
        for other_match in _compiled_exact_subject(other).finditer(tail):
            lookahead = tail[other_match.end() : other_match.end() + 15]
            if _CALL_VERB_RE.search(lookahead):
                clause_end = min(clause_end, other_match.start())
    return clause_end


def _exact_subject(subject: str) -> str:
    """Return a whole-identifier, CASE-SENSITIVE match for one canonical name.

    Every caller of this helper compiles its full pattern with
    ``re.IGNORECASE`` so that surrounding English prose words ("calls",
    "components", ...) match regardless of case. Scanner identifiers
    (canonical unit/group names) are case-sensitive, and real projects can
    have two distinct names that differ only by case (e.g. ``AssetCheck2VISE``
    vs. ``AssetCheck2Vise``); without the local ``(?-i:...)`` scope below, the
    outer IGNORECASE flag would silently treat those as the same subject and
    attribute one name's claim to the other's protected fact. The local
    scope keeps the identifier itself exact while the rest of the pattern
    stays case-insensitive.
    """

    return (
        rf"(?<![A-Za-z0-9_])(?-i:{re.escape(subject)})"
        rf"(?![A-Za-z0-9_])"
    )


def _append_count_issue(
    issues: list[dict[str, Any]],
    *,
    document: str,
    line_number: int,
    subject: str,
    claimed: int,
    expected: int,
    text: str,
) -> None:
    if claimed == expected:
        return
    key = (document, line_number, subject, claimed, expected)
    if any(
        (
            item.get("document"),
            item.get("line"),
            item.get("subject"),
            item.get("claimed"),
            item.get("expected"),
        )
        == key
        for item in issues
    ):
        return
    issues.append(
        {
            "code": "protected_count_mismatch",
            "document": document,
            "line": line_number,
            "subject": subject,
            "claimed": claimed,
            "expected": expected,
            "detail": text.strip()[:500],
        }
    )


def _append_matches(
    issues: list[dict[str, Any]],
    pattern: re.Pattern[str],
    document: str,
    line_number: int,
    subject: str,
    expected: int,
    text: str,
) -> None:
    for match in pattern.finditer(text):
        _append_count_issue(
            issues,
            document=document,
            line_number=line_number,
            subject=subject,
            claimed=_parse_count(match.group("count")),
            expected=expected,
            text=text,
        )


def _component_is_inactive(component: dict[str, Any]) -> bool:
    if component.get("active") is False:
        return True
    params = component.get("params")
    if not isinstance(params, list):
        return False
    return any(
        isinstance(param, dict)
        and str(param.get("name") or "").casefold() == "activate"
        and str(param.get("value") or "").strip().strip('"').casefold() == "false"
        for param in params
    )


def _parse_count(value: str) -> int:
    return int(value.replace(",", ""))
