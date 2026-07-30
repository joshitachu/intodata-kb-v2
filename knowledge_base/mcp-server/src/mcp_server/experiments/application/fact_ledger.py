"""Deterministic scanner facts and high-confidence documentation claim checks."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any


_INTEGER = r"(?P<count>\d[\d,]*)"
_JOBS = rf"{_INTEGER}\s+(?:canonical\s+)?(?:jobs?|units?)\b"
_COMPONENTS = rf"{_INTEGER}\s+(?:total\s+)?components?\b"
_MARKDOWN_TABLE_COUNT = re.compile(
    r"^\s*\|\s*`?(?P<subject>t[A-Za-z0-9_]+)`?\s*\|\s*"
    r"(?P<count>\d[\d,]*)\s*\|",
    re.IGNORECASE,
)


def compile_fact_ledger(
    source_items: tuple[tuple[str, Path], ...],
) -> dict[str, Any]:
    """Compile provider-neutral, reproducible facts from frozen scanner JSON."""

    json_sources: dict[str, Any] = {}
    source_hash_inputs: list[str] = []
    for logical_identity, path in source_items:
        relative = _logical_relative_path(logical_identity)
        # Frozen snapshot content is intentionally stored under opaque ``.bin``
        # names.  The logical identity is the authoritative media identity.
        if PurePosixPath(relative).suffix.casefold() != ".json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        json_sources[relative] = payload
        source_hash_inputs.append(relative)

    split_jobs = _split_job_records(json_sources)
    embedded_jobs = _embedded_job_records(json_sources)
    job_records = split_jobs or embedded_jobs
    units = [_unit_fact(path, payload) for path, payload in job_records]
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
    ]
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
        "preflight": _ledger_preflight_summary(
            json_sources=json_sources,
            split_jobs=split_jobs,
            embedded_jobs=embedded_jobs,
            units=units,
            manifest=manifest,
        ),
        "totals": {
            "canonical_units": len(units),
            "groups": len(groups),
            "components": total_components if all_units_have_component_arrays else None,
            "job_call_edges": sum(len(value) for value in job_calls.values()),
            "connection_usage_edges": sum(
                len(value) for value in connection_usage.values()
            ),
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
    issues = raw_issues if isinstance(raw_issues, list) else [
        {"code": "invalid_preflight_metadata"}
    ]
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
            _check_table_component_count(
                issues, facts, document, line_number, line
            )
            _check_subject_counts(
                issues, facts, document, line_number, line
            )
            _check_project_totals(
                issues, facts, document, line_number, line
            )
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
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    split_names = _record_names(split_jobs)
    embedded_names = _record_names(embedded_jobs)
    unit_names = {str(item["name"]) for item in units}
    scanner_job_inputs = sum(
        path.startswith("scan/jobs/") for path in json_sources
    )

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
        value for value in embedded_totals
        if isinstance(value, int) and not isinstance(value, bool)
    ]
    if expected_totals and any(value != len(unit_names) for value in expected_totals):
        issues.append(
            {
                "code": "scanner_total_reconciliation_mismatch",
                "ledger_count": len(unit_names),
                "scanner_totals": sorted(set(expected_totals)),
            }
        )

    manifest_groups = manifest.get("job_groups")
    if isinstance(manifest_groups, dict):
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
        str(item["name"])
        for item in units
        if not bool(item.get("components_captured"))
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
        "issues": issues,
    }


def _record_names(
    records: list[tuple[str, dict[str, Any]]],
) -> set[str]:
    return {
        str(payload["name"])
        for _, payload in records
        if isinstance(payload.get("name"), str)
    }


def _unit_fact(path: str, payload: dict[str, Any]) -> dict[str, Any]:
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
    refs = payload.get("trunjob_refs")
    callees = [
        str(item["child_job"])
        for item in refs if isinstance(item, dict) and item.get("child_job")
    ] if isinstance(refs, list) else []
    raw_group = payload.get("group")
    group = str(raw_group).strip() if raw_group else "ungrouped"
    return {
        "name": str(payload["name"]),
        "group": group,
        "version": str(payload.get("version") or "unknown"),
        "active": payload.get("active"),
        "source": _source_locator(path),
        "components_captured": isinstance(raw_components, list),
        "component_count": len(components),
        "component_types": dict(sorted(component_types.items())),
        "inactive_component_count": len(inactive),
        "inactive_components": inactive,
        "connection_count": (
            len(payload["connections"])
            if isinstance(payload.get("connections"), list)
            else None
        ),
        "called_units": callees,
    }


def _first_mapping_source(
    json_sources: dict[str, Any],
    *,
    suffix: str,
) -> tuple[str, dict[str, Any]]:
    for path, payload in sorted(json_sources.items()):
        if path.endswith(suffix) and isinstance(payload, dict):
            return path, payload
    return suffix, {}


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
                if kind == "component_type_count"
                and name.casefold() == subject.casefold()
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
                rf"{re.escape(subject)}\s*\(\s*{_INTEGER}\s+jobs?\b",
                re.IGNORECASE,
            )
            _append_matches(
                issues, pattern, document, line_number, subject, expected, line
            )
        elif kind == "group_component_count":
            if "group" not in line.casefold():
                continue
            pattern = re.compile(
                rf"{re.escape(subject)}\s*\([^)]*?{_INTEGER}\s+components?\b",
                re.IGNORECASE,
            )
            _append_matches(
                issues, pattern, document, line_number, subject, expected, line
            )
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
            _append_matches(
                issues, pattern, document, line_number, subject, expected, line
            )
        elif kind == "connection_job_count":
            pattern = re.compile(
                rf"{_exact_subject(subject)}"
                rf"(?:(?!{re.escape(subject)}).){{0,180}}?"
                rf"(?:used\s+by\s+)?{_INTEGER}(?:\s+of\s+\d+)?\s+jobs?\b",
                re.IGNORECASE,
            )
            _append_matches(
                issues, pattern, document, line_number, subject, expected, line
            )
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
    folded = line.casefold()
    expected_units = facts.get(("canonical_unit_count", "project"))
    if expected_units is not None and (
        "canonical" in folded
        or "project contains" in folded
        or "exactly" in folded
    ):
        for match in re.finditer(_JOBS, line, flags=re.IGNORECASE):
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
    if expected_components is not None and (
        "total components" in folded or "components total" in folded
    ):
        for match in re.finditer(_COMPONENTS, line, flags=re.IGNORECASE):
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
            or "no independent rest endpoints, cron triggers, or scheduling exists"
            in folded
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


def _check_call_edges(
    issues: list[dict[str, Any]],
    unit_names: set[str],
    call_edges: set[tuple[str, str]],
    document: str,
    line_number: int,
    line: str,
) -> None:
    for caller in unit_names:
        caller_match = re.search(_exact_subject(caller), line, re.IGNORECASE)
        if caller_match is None:
            continue
        tail = line[caller_match.end():]
        if not re.search(r"(?:->|calls?|invokes?|dispatches?)", tail, re.IGNORECASE):
            continue
        for callee in unit_names - {caller}:
            if re.search(_exact_subject(callee), tail, re.IGNORECASE) and (
                caller,
                callee,
            ) not in call_edges:
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


def _exact_subject(subject: str) -> str:
    return (
        rf"(?<![A-Za-z0-9_]){re.escape(subject)}"
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
