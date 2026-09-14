"""Compile complete client-facing registers from an agent-safe workspace.

The documentation agent is good at explaining a landscape, but it is the
wrong mechanism for copying thousands of exact rows without omission.  This
module is the deterministic complement: it turns the already-approved facts
under ``facts/`` and ``scan/`` into a client-readable reference shelf under
``output/reference/``.  No provider and no LLM participates.

Only safe, structured facts are consumed.  Source files, staged design files,
free-form component parameters, endpoint descriptors and workspace paths are
deliberately outside this compiler's input surface.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp_server.providers.agent_workspace.contract import (
    WorkspaceUnavailableError,
)
from mcp_server.providers.agent_workspace.staged_data import (
    StagedDataError,
    read_complete_list,
)

DEFAULT_MAPPING_PART_BYTES = 256 * 1024

#: The compiler-owned directory under ``output/``. Named here because more
#: than one module has to know which entries in ``output/`` are the agent's
#: work and which are this compiler's; two spellings of it would make a
#: rebuilt shelf look like agent drafts.
REFERENCE_DIR_NAME = "reference"
_INTERNAL_REFERENCE_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:facts|scan|notes|output)/[^\s|`,;)]*",
    re.IGNORECASE,
)
_SENSITIVE_NAME_RE = re.compile(
    r"(?:password|passwd|pwd|secret|token|api[_-]?key|credential|private[_-]?key|"
    r"connection[_-]?string|client[_-]?secret)",
    re.IGNORECASE,
)


class ReferenceRegisterError(WorkspaceUnavailableError):
    """The safe workspace cannot be compiled into factual registers.

    A ``WorkspaceUnavailableError`` carrying ``stage="reference"``, not a
    sibling type. It used to be a bare ``RuntimeError``, so a compilation
    failure inside ``build_workspace`` escaped the runner's single ``except``
    and reached the operator as an unqualified "Generation failed". The name
    stays exported because ``runner.py`` catches it specifically, and
    catching the subclass is still the narrower, more informative handler.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, stage="reference")


@dataclass(frozen=True)
class ReferenceRegisterReceipt:
    """Counts reconciled from the rows actually written."""

    output_dir: Path
    files: tuple[str, ...]
    unit_count: int
    dependency_count: int
    unresolved_dependency_count: int
    interface_count: int
    channel_count: int
    configuration_variable_count: int
    mapping_table_count: int
    mapping_row_count: int
    runtime_unit_count: int
    integration_count: int
    journey_count: int
    reconciliation_warnings: tuple[str, ...]


def _read_json(path: Path, expected: type) -> Any:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReferenceRegisterError(f"required factual input is unreadable: {path.name}") from exc
    if not isinstance(payload, expected):
        raise ReferenceRegisterError(f"required factual input has the wrong shape: {path.name}")
    return payload


def _ascii(value: Any) -> str:
    text = str(value if value is not None else "")
    text = " ".join(text.replace("\x00", "").split())
    text = _INTERNAL_REFERENCE_RE.sub("[internal reference omitted]", text)
    return text


def _cell(value: Any) -> str:
    return (
        _ascii(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "\\|")
        .replace("`", "'")
    )


def _join(values: Iterable[Any]) -> str:
    return ", ".join(_cell(value) for value in values if str(value or ""))


def _stable_id(prefix: str, payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{prefix}:{hashlib.sha256(encoded.encode('ascii')).hexdigest()[:16]}"


def _write_ascii(path: Path, lines: Sequence[str]) -> None:
    text = "\n".join(lines).rstrip() + "\n"
    path.write_text(text, encoding="utf-8")


def _configuration_state(
    name: str,
    value: Any,
    *,
    explicitly_redacted: bool = False,
) -> str:
    """Describe presence without ever publishing a configuration value.

    A value that looks harmless can still be a tenant identifier, host alias,
    or credential fragment in another estate. Names, types, environments and
    usage are sufficient for documentation and keep this boundary categorical.
    """

    if explicitly_redacted or _SENSITIVE_NAME_RE.search(name):
        return "redacted"
    if value is None or value == "":
        return "empty"
    return "configured"


def _mapping_block(table: dict[str, Any], warnings: list[str]) -> tuple[list[str], int]:
    table_id = _ascii(table.get("table_id") or "")
    if not table_id:
        raise ReferenceRegisterError("a mapping table has no stable table_id")
    raw_rows = table.get("rows", []) or []
    if not isinstance(raw_rows, list) or any(not isinstance(row, dict) for row in raw_rows):
        raise ReferenceRegisterError(f"mapping table {table_id} carries a malformed row")
    rows = raw_rows
    declared = table.get("written_fields")
    if isinstance(declared, int) and declared != len(rows):
        warnings.append(
            f"mapping table {table_id} declares {declared} written field(s) but carries {len(rows)} row(s)"
        )
    lines = [
        f"## Mapping table {_cell(table_id)}",
        "",
        (
            f"Design: {_cell(table.get('unit_id') or 'unresolved')}; "
            f"mapper: {_cell(table.get('component_name') or 'unresolved')}; "
            f"kind: {_cell(table.get('mapper_kind') or 'unknown')}."
        ),
        "",
        "| Measure | Count |",
        "|---|---:|",
        f"| Offered input fields | {_cell(table.get('offered_fields', 0))} |",
        f"| Written output rows | {len(rows)} |",
        f"| Mapped from input | {_cell(table.get('mapped_from_input', 0))} |",
        f"| Constant or generated | {_cell(table.get('constant_or_generated', 0))} |",
        f"| Lookup derived | {_cell(table.get('lookup_derived', 0))} |",
        f"| Dynamic or unresolved | {_cell(table.get('dynamic_unresolved', 0))} |",
        "",
    ]
    filters = table.get("filters") or []
    lookups = table.get("lookup_tables") or []
    rejects = table.get("reject_outputs") or []
    if filters:
        lines += [f"Filters: {_join(filters)}.", ""]
    if lookups:
        lines += [
            "| Lookup | Match model | Load model | Inner join | Filter |",
            "|---|---|---|---|---|",
        ]
        for lookup in lookups:
            if not isinstance(lookup, dict):
                continue
            lines.append(
                f"| {_cell(lookup.get('name'))} | {_cell(lookup.get('match_model'))} | "
                f"{_cell(lookup.get('load_model'))} | {_cell(lookup.get('inner_join'))} | "
                f"{_cell(lookup.get('expression_filter'))} |"
            )
        lines.append("")
    if rejects:
        lines += [f"Reject outputs: {_join(rejects)}.", ""]
    lines += [
        "| Row | Output table | Output field | Exact expression | Classification | Source fields | Dynamic references |",
        "|---:|---|---|---|---|---|---|",
    ]
    for index, row in enumerate(rows, start=1):
        expression = row.get("expression")
        classification = str(row.get("classification") or "").lower()
        if _SENSITIVE_NAME_RE.search(str(row.get("output_field") or "")) and classification in {
            "constant",
            "constant_or_generated",
            "value",
        }:
            expression = "[credential value withheld]"
        lines.append(
            f"| {index} | {_cell(row.get('output_table'))} | {_cell(row.get('output_field'))} | "
            f"{_cell(expression)} | {_cell(row.get('classification'))} | "
            f"{_join(row.get('source_refs') or [])} | {_join(row.get('dynamic_refs') or [])} |"
        )
    unwritten = table.get("unwritten_offered") or []
    lines += [
        "",
        f"Offered inputs not written ({len(unwritten)}): {_join(unwritten) or 'none'}.",
        "",
    ]
    return lines, len(rows)


def _write_mappings(
    target: Path, tables: list[dict[str, Any]], max_part_bytes: int, warnings: list[str]
) -> tuple[list[str], int]:
    if max_part_bytes < 1024:
        raise ReferenceRegisterError("mapping part byte ceiling must be at least 1024")
    ordered = sorted(
        tables,
        key=lambda row: (
            _ascii(row.get("unit_id")),
            _ascii(row.get("component_name")),
            _ascii(row.get("table_id")),
        ),
    )
    parts: list[list[str]] = []
    current = [
        "# Exact field mappings",
        "",
        "Every deterministic mapping row is reproduced; none are sampled.",
        "",
    ]
    current_bytes = len("\n".join(current).encode("utf-8"))
    total_rows = 0
    for table in ordered:
        block, rows = _mapping_block(table, warnings)
        block_bytes = len(("\n".join(block) + "\n").encode("utf-8"))
        if len(current) > 4 and current_bytes + block_bytes > max_part_bytes:
            parts.append(current)
            current = ["# Exact field mappings (continued)", ""]
            current_bytes = len("\n".join(current).encode("utf-8"))
        current.extend(block)
        current_bytes += block_bytes
        total_rows += rows
    if len(current) > 2 or not parts:
        parts.append(current)

    names: list[str] = []
    for index, lines in enumerate(parts, start=1):
        name = f"mappings_part_{index:03d}.md"
        _write_ascii(target / name, lines)
        names.append(name)

    index_lines = [
        "# Mapping register",
        "",
        f"Tables: {len(ordered)}. Exact output rows: {total_rows}. Parts: {len(names)}.",
        "",
        "| Part | Mapping tables | Exact rows |",
        "|---|---:|---:|",
    ]
    for index, name in enumerate(names, start=1):
        text = (target / name).read_text(encoding="utf-8")
        row_count = sum(1 for line in text.splitlines() if re.match(r"^\| [0-9]+ \|", line))
        index_lines.append(
            f"| [{name}]({name}) | {text.count('## Mapping table ')} | {row_count} |"
        )
    _write_ascii(target / "mappings.md", index_lines)
    return ["mappings.md", *names], total_rows


def _write_units(target: Path, payload: dict[str, Any]) -> tuple[str, int]:
    units = payload.get("units")
    if not isinstance(units, dict):
        raise ReferenceRegisterError("units.json has no units object")
    lines = [
        "# Implementing units",
        "",
        f"Total units: {len(units)}.",
        "",
        "| Stable unit ID | Name | Purpose | Kind | Role | Group | Version | Components | Component types | Entry point | Calls | Called by | Error handling | Scan quality |",
        "|---|---|---|---|---|---|---|---:|---|---|---|---|---|---|",
    ]
    for unit_id, unit in sorted(units.items(), key=lambda item: _ascii(item[0])):
        if not isinstance(unit, dict):
            raise ReferenceRegisterError(f"unit {unit_id} has the wrong shape")
        lines.append(
            f"| {_cell(unit_id)} | {_cell(unit.get('display_name'))} | {_cell(unit.get('purpose'))} | {_cell(unit.get('kind'))} | "
            f"{_cell(unit.get('role'))} | {_cell(unit.get('group'))} | {_cell(unit.get('version'))} | "
            f"{_cell(unit.get('component_count', 0))} | {_join(unit.get('component_types') or [])} | "
            f"{'yes' if unit.get('entry_point') else 'no'} | "
            f"{_join(unit.get('callees') or [])} | {_join(unit.get('callers') or [])} | "
            f"{_join(unit.get('error_handling_constructs') or [])} | {_cell(unit.get('scan_quality'))} |"
        )
    _write_ascii(target / "implementing_units.md", lines)
    return "implementing_units.md", len(units)


def _write_dependencies(target: Path, payload: dict[str, Any]) -> tuple[str, int, int]:
    resolved = payload.get("edges", []) or []
    unresolved = payload.get("unresolved", []) or []
    if (
        not isinstance(resolved, list)
        or not isinstance(unresolved, list)
        or any(not isinstance(row, dict) for row in [*resolved, *unresolved])
    ):
        raise ReferenceRegisterError("dependencies.json carries a malformed dependency row")
    rows = [(True, row) for row in resolved] + [(False, row) for row in unresolved]
    rows.sort(
        key=lambda pair: (
            _ascii(pair[1].get("source")),
            _ascii(pair[1].get("target") or pair[1].get("declared_target")),
            _ascii(pair[1].get("relation_type")),
        )
    )
    lines = [
        "# Dependencies",
        "",
        f"Resolved dependencies: {len(resolved)}. Unresolved or ambiguous: {len(unresolved)}.",
        "",
        f"Roots: {_join(payload.get('roots') or []) or 'none'}.",
        f"Leaves: {_join(payload.get('leaves') or []) or 'none'}.",
        "",
        "| Dependency ID | Source | Target | Relationship | Resolution | Component | Version | Context |",
        "|---|---|---|---|---|---|---|---|",
    ]
    id_occurrences: dict[str, int] = {}
    for is_resolved, row in rows:
        public = {
            "source": row.get("source"),
            "target": row.get("target") or row.get("declared_target"),
            "relation_type": row.get("relation_type"),
            "resolution": row.get("resolution"),
            "component": row.get("component_name"),
        }
        resolution = row.get("resolution") or ("resolved" if is_resolved else "unresolved")
        signature = json.dumps(public, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        occurrence = id_occurrences.get(signature, 0) + 1
        id_occurrences[signature] = occurrence
        lines.append(
            f"| {_stable_id('dependency', {'row': public, 'occurrence': occurrence})} | {_cell(row.get('source'))} | "
            f"{_cell(row.get('target') or row.get('declared_target'))} | "
            f"{_cell(row.get('relation_type'))} | {_cell(resolution)} | "
            f"{_cell(row.get('component_name'))} | {_cell(row.get('version'))} | "
            f"{_cell(row.get('context_name'))} |"
        )
    routine_links = payload.get("routine_links") or {}
    if isinstance(routine_links, dict) and routine_links:
        lines += ["", "## Routine dependencies", "", "| Unit | Routine references |", "|---|---|"]
        for unit, links in sorted(routine_links.items()):
            names = []
            for link in links or []:
                if isinstance(link, dict):
                    names.append(
                        link.get("routine")
                        or link.get("routine_name")
                        or link.get("name")
                        or "unresolved"
                    )
                else:
                    names.append(link)
            lines.append(f"| {_cell(unit)} | {_join(names)} |")
    _write_ascii(target / "dependencies.md", lines)
    return "dependencies.md", len(resolved), len(unresolved)


def _interface_name(item: dict[str, Any]) -> str:
    if item.get("redaction_descriptor") or item.get("descriptor"):
        return "configured details withheld"
    return _ascii(item.get("channel") or item.get("resource") or item.get("name") or "")


def _write_interfaces(target: Path, payload: dict[str, Any]) -> tuple[str, int, int]:
    by_unit = payload.get("by_unit") or {}
    if not isinstance(by_unit, dict):
        raise ReferenceRegisterError("interfaces.json has no by_unit object")
    interface_rows: list[tuple[str, dict[str, Any]]] = []
    for unit, items in by_unit.items():
        for item in items or []:
            if not isinstance(item, dict):
                raise ReferenceRegisterError(f"interface for unit {unit} has the wrong shape")
            interface_rows.append((str(unit), item))
    interface_rows.sort(
        key=lambda pair: (
            _ascii(pair[0]),
            _ascii(pair[1].get("kind")),
            _ascii(pair[1].get("component_name")),
            _interface_name(pair[1]),
        )
    )
    channels = payload.get("channels", []) or []
    if not isinstance(channels, list) or any(not isinstance(row, dict) for row in channels):
        raise ReferenceRegisterError("interfaces.json carries a malformed channel row")
    channels.sort(
        key=lambda row: (
            _ascii(row.get("artifact")),
            _ascii(row.get("direction")),
            _ascii(row.get("channel")),
            _ascii(row.get("component_name")),
        )
    )
    lines = [
        "# Interfaces and channels",
        "",
        f"Unit interface records: {len(interface_rows)}. Channel bindings: {len(channels)}.",
        "",
        "Endpoint descriptor payloads are intentionally withheld; protocol, direction, component and safe literal names remain.",
        "",
        "## Interfaces by implementing unit",
        "",
        "| Interface ID | Unit | Kind | Type/family | Direction | Component | Safe name | Active |",
        "|---|---|---|---|---|---|---|---|",
    ]
    interface_occurrences: dict[str, int] = {}
    for unit, item in interface_rows:
        public = {
            "unit": unit,
            "kind": item.get("kind"),
            "type": item.get("type") or item.get("family"),
            "direction": item.get("direction"),
            "component": item.get("component_name"),
            "name": _interface_name(item),
        }
        signature = json.dumps(public, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        occurrence = interface_occurrences.get(signature, 0) + 1
        interface_occurrences[signature] = occurrence
        lines.append(
            f"| {_stable_id('interface', {'row': public, 'occurrence': occurrence})} | {_cell(unit)} | {_cell(item.get('kind'))} | "
            f"{_cell(item.get('type') or item.get('family'))} | {_cell(item.get('direction'))} | "
            f"{_cell(item.get('component_name'))} | {_cell(_interface_name(item))} | "
            f"{'no' if item.get('active') is False else 'yes'} |"
        )
    lines += [
        "",
        "## Channel bindings",
        "",
        "| Channel ID | Unit | Direction | Type | Component | Channel | Dynamic |",
        "|---|---|---|---|---|---|---|",
    ]
    channel_occurrences: dict[str, int] = {}
    for row in channels:
        safe_name = (
            "configured details withheld"
            if row.get("redaction_descriptor")
            else _ascii(row.get("channel"))
        )
        public = {
            "unit": row.get("artifact"),
            "direction": row.get("direction"),
            "type": row.get("channel_type") or row.get("type"),
            "component": row.get("component_name"),
            "channel": safe_name,
        }
        signature = json.dumps(public, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        occurrence = channel_occurrences.get(signature, 0) + 1
        channel_occurrences[signature] = occurrence
        lines.append(
            f"| {_stable_id('channel', {'row': public, 'occurrence': occurrence})} | {_cell(row.get('artifact'))} | {_cell(row.get('direction'))} | "
            f"{_cell(row.get('channel_type') or row.get('type'))} | {_cell(row.get('component_name'))} | "
            f"{_cell(safe_name)} | {'yes' if row.get('dynamic') else 'no'} |"
        )
    _write_ascii(target / "interfaces_and_channels.md", lines)
    return "interfaces_and_channels.md", len(interface_rows), len(channels)


def _write_configuration(target: Path, payload: dict[str, Any]) -> tuple[str, int]:
    groups = [row for row in payload.get("context_groups", []) or [] if isinstance(row, dict)]
    lines = [
        "# Safe configuration register",
        "",
        "Configuration values are never published. Only names, types, environments, presence state and usage are exposed.",
        "",
        "## Repository context groups",
        "",
        "| Group | Variable | Type | State by environment | Used by |",
        "|---|---|---|---|---|",
    ]
    count = 0
    for group in sorted(groups, key=lambda row: _ascii(row.get("key") or row.get("name"))):
        variables = [row for row in group.get("variables", []) or [] if isinstance(row, dict)]
        for variable in sorted(variables, key=lambda row: _ascii(row.get("name"))):
            values = variable.get("values") or {}
            if not isinstance(values, dict) or not values:
                values = {"unspecified": None}
            redacted = {str(env) for env in variable.get("redacted_environments", []) or []}
            environment_values = "; ".join(
                f"{_cell(environment)}={_configuration_state(str(variable.get('name') or ''), value, explicitly_redacted=str(environment) in redacted)}"
                for environment, value in sorted(values.items(), key=lambda item: _ascii(item[0]))
            )
            lines.append(
                f"| {_cell(group.get('key') or group.get('name'))} | {_cell(variable.get('name'))} | "
                f"{_cell(variable.get('type'))} | {environment_values} | {_join(group.get('used_by') or [])} |"
            )
            count += 1
    inline = payload.get("inline_contexts") or {}
    if isinstance(inline, dict) and inline:
        lines += [
            "",
            "## Inline contexts",
            "",
            "| Unit | Group | Variable | Type | State by environment |",
            "|---|---|---|---|---|",
        ]
        for unit, inline_groups in sorted(inline.items()):
            for group in inline_groups or []:
                if not isinstance(group, dict):
                    continue
                for variable in group.get("variables", []) or []:
                    if not isinstance(variable, dict):
                        continue
                    values = variable.get("values") or {"unspecified": None}
                    environment_values = "; ".join(
                        f"{_cell(environment)}={_configuration_state(str(variable.get('name') or ''), value)}"
                        for environment, value in sorted(
                            values.items(), key=lambda item: _ascii(item[0])
                        )
                    )
                    lines.append(
                        f"| {_cell(unit)} | {_cell(group.get('name'))} | {_cell(variable.get('name'))} | "
                        f"{_cell(variable.get('type'))} | {environment_values} |"
                    )
                    count += 1
    connections = [
        row for row in payload.get("repository_connections", []) or [] if isinstance(row, dict)
    ]
    if connections:
        lines += ["", "## Repository connections", "", "| Name | Type | Used by |", "|---|---|---|"]
        for row in sorted(connections, key=lambda item: _ascii(item.get("name"))):
            lines.append(
                f"| {_cell(row.get('name'))} | {_cell(row.get('connection_type'))} | {_join(row.get('used_by') or [])} |"
            )
    unresolved = payload.get("unresolved_context_refs") or {}
    if isinstance(unresolved, dict) and unresolved:
        lines += ["", "## Unresolved context references", "", "| Unit | References |", "|---|---|"]
        for unit, refs in sorted(unresolved.items()):
            lines.append(f"| {_cell(unit)} | {_cell(len(refs or []))} unresolved reference(s) |")
    _write_ascii(target / "configuration.md", lines)
    return "configuration.md", count


def _write_runtime(target: Path, payload: dict[str, Any]) -> tuple[str, int]:
    """Publish every safe structured runtime row returned for this run.

    ``runtime/profile.md`` is intentionally a compact orientation view.  This
    register is compiled from ``runtime/evidence.json`` instead, so the
    profile's readability ceiling cannot silently erase lower-ranked units or
    failure modes from the client deliverable.
    """

    by_unit = payload.get("by_unit") or {}
    if not isinstance(by_unit, dict) or any(not isinstance(row, dict) for row in by_unit.values()):
        raise ReferenceRegisterError("runtime evidence has a malformed by_unit object")

    collections: dict[str, list[Any]] = {}
    for name in (
        "unmatched_units",
        "unretrieved_units",
        "orphan_runtime_jobs",
        "alias_conflicts",
        "redaction_flags",
    ):
        value = payload.get(name) or []
        if not isinstance(value, list):
            raise ReferenceRegisterError(f"runtime evidence has a malformed {name} list")
        collections[name] = value

    mode = _ascii(payload.get("mode") or "unavailable")
    lines = [
        "# Runtime evidence",
        "",
        "This register contains every safe structured runtime record returned for this generation. It does not contain raw logs.",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Status | {_cell(mode)} |",
        f"| Observation window | {_cell(payload.get('window_days', 0))} days |",
        f"| Units requested | {_cell(payload.get('requested_units', 0))} |",
        f"| Units matched | {len(by_unit)} |",
        f"| Request batches | {_cell(payload.get('batches', 0))} |",
        f"| Failed batches | {_cell(payload.get('failed_batches', 0))} |",
        f"| Partial result | {'yes' if payload.get('partial') else 'no'} |",
        f"| Scope mismatch observed | {'yes' if payload.get('scope_mismatch') else 'no'} |",
        f"| Read environments | {_join(payload.get('read_environments') or []) or 'all available environments'} |",
        f"| Shared runtime read | {'yes' if payload.get('shared_read') else 'no'} |",
        "",
    ]

    exception_labels = {
        "unmatched_units": "Scanned unit with no matching runtime record",
        "unretrieved_units": "Scanned unit whose runtime lookup did not complete",
        "orphan_runtime_jobs": "Runtime job with no scanned counterpart",
        "alias_conflicts": "Unit with conflicting runtime aliases",
    }
    exceptions = [
        (exception_labels[kind], item) for kind in exception_labels for item in collections[kind]
    ]
    if exceptions:
        lines += [
            "## Coverage exceptions",
            "",
            "| Kind | Name |",
            "|---|---|",
        ]
        lines.extend(f"| {_cell(kind)} | {_cell(item)} |" for kind, item in exceptions)
        lines.append("")

    for unit_name, row in sorted(by_unit.items(), key=lambda item: _ascii(item[0])):
        failures = row.get("recent_failures") or []
        incidents = row.get("incident_insights") or []
        if not isinstance(failures, list) or any(not isinstance(item, dict) for item in failures):
            raise ReferenceRegisterError(
                f"runtime evidence for {unit_name} has malformed recent failures"
            )
        if not isinstance(incidents, list) or any(not isinstance(item, dict) for item in incidents):
            raise ReferenceRegisterError(
                f"runtime evidence for {unit_name} has malformed incident insights"
            )
        lines += [
            f"## {_cell(unit_name)}",
            "",
            "| Measure | Value |",
            "|---|---|",
            f"| Runtime job name | {_cell(row.get('job_name'))} |",
            f"| Environments | {_join(row.get('environments') or []) or 'not reported'} |",
            f"| Total runs | {_cell(row.get('total_runs', 0))} |",
            f"| Successful runs | {_cell(row.get('success_runs', 0))} |",
            f"| Failed runs | {_cell(row.get('failed_runs', 0))} |",
            f"| Unfinished runs | {_cell(row.get('unfinished_runs', 0))} |",
            f"| Failure rate | {_cell(row.get('failure_rate', 0))} |",
            f"| Current failure streak | {_cell(row.get('current_failure_streak', 0))} |",
            f"| First observed run | {_cell(row.get('first_run_at') or 'not reported')} |",
            f"| Last observed run | {_cell(row.get('last_run_at') or 'not reported')} |",
            f"| Runs per day | {_cell(row.get('runs_per_day') if row.get('runs_per_day') is not None else 'not classified')} |",
            f"| P50 duration | {_cell(row.get('p50_duration_ms') if row.get('p50_duration_ms') is not None else 'not reported')} ms |",
            f"| P95 duration | {_cell(row.get('p95_duration_ms') if row.get('p95_duration_ms') is not None else 'not reported')} ms |",
            f"| Maximum duration | {_cell(row.get('max_duration_ms') if row.get('max_duration_ms') is not None else 'not reported')} ms |",
            f"| Plans | {_join(row.get('plans') or []) or 'not reported'} |",
            f"| Matched alias | {_cell(row.get('matched_alias') or 'not reported')} |",
            "",
        ]
        if failures:
            lines += [
                "### Observed failure modes",
                "",
                "| Most recent occurrence | Occurrences | Redacted summary |",
                "|---|---:|---|",
            ]
            lines.extend(
                f"| {_cell(item.get('failed_at'))} | {_cell(item.get('occurrences', 1))} | {_cell(item.get('error_summary'))} |"
                for item in failures
            )
            lines.append("")
        if incidents:
            lines += [
                "### Runtime incident analysis",
                "",
                "| Pattern | Confidence | Occurrences | Last observed | Failed component | Exception/error | Root-cause candidate | Origin | Next checks |",
                "|---|---|---:|---|---|---|---|---|---|",
            ]
            for item in incidents:
                error = item.get("exception_class") or item.get("error_code")
                lines.append(
                    f"| {_cell(item.get('pattern_id'))} | {_cell(item.get('confidence'))} | "
                    f"{_cell(item.get('occurrences', 0))} | {_cell(item.get('most_recent_at'))} | "
                    f"{_cell(item.get('failed_component'))} | {_cell(error)} | "
                    f"{_cell(item.get('root_cause_candidate'))} | {_cell(item.get('cause_origin'))} | "
                    f"{_join(item.get('next_checks') or [])} |"
                )
            lines.append("")

    flags = collections["redaction_flags"]
    if flags:
        lines += [
            "## Runtime evidence withheld by redaction verification",
            "",
            "These records name safe masked excerpts that were removed before documentation analysis.",
            "",
            "| Unit | Finding | Masked excerpt |",
            "|---|---|---|",
        ]
        for flag in flags:
            if not isinstance(flag, dict):
                raise ReferenceRegisterError("runtime evidence has a malformed redaction flag")
            lines.append(
                f"| {_cell(flag.get('unit_name'))} | {_cell(flag.get('kind'))} | {_cell(flag.get('excerpt'))} |"
            )
        lines.append("")

    _write_ascii(target / "runtime.md", lines)
    return "runtime.md", len(by_unit)


def _withheld_code_counts(text: str) -> tuple[str, str] | None:
    match = re.search(
        r"\*\*(\d[\d,]*) custom-code body/bodies.*?of (\d[\d,]*) staged",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    return (match.group(1), match.group(2)) if match else None


def _write_limitations(target: Path, workspace: Path, warnings: list[str]) -> str:
    ledger = _read_json(workspace / "scan" / "capability_ledger.json", dict)
    try:
        inventory = read_complete_list(workspace / "scan" / "inventory.json")
    except StagedDataError as exc:
        raise ReferenceRegisterError(str(exc)) from exc
    outcome_counts: dict[tuple[str, str, str], int] = {}
    for row in inventory:
        if not isinstance(row, dict):
            continue
        key = (
            _ascii(row.get("kind") or "unknown"),
            _ascii(row.get("outcome") or "unknown"),
            _ascii(row.get("reason") or "unspecified"),
        )
        outcome_counts[key] = outcome_counts.get(key, 0) + 1
    lines = [
        "# Evidence limitations",
        "",
        "These are boundaries of the available technical evidence, not claims that the corresponding behavior does not exist.",
        "",
        "## File outcomes",
        "",
        "| Kind | Outcome | Reason | Files |",
        "|---|---|---|---:|",
    ]
    for (kind, outcome, reason), count in sorted(outcome_counts.items()):
        lines.append(f"| {_cell(kind)} | {_cell(outcome)} | {_cell(reason)} | {count} |")
    lines += [
        "",
        "## Construct coverage",
        "",
        "| Category | Construct | Status | Count | Note |",
        "|---|---|---|---:|---|",
    ]
    constructs = [row for row in ledger.get("constructs", []) or [] if isinstance(row, dict)]
    for row in sorted(
        constructs, key=lambda item: (_ascii(item.get("category")), _ascii(item.get("construct")))
    ):
        lines.append(
            f"| {_cell(row.get('category'))} | {_cell(row.get('construct'))} | {_cell(row.get('status'))} | "
            f"{_cell(row.get('count', 0))} | {_cell(row.get('note'))} |"
        )
    parser_note = workspace / "facts" / "parser_limitations.md"
    if parser_note.is_file():
        counts = _withheld_code_counts(parser_note.read_text(encoding="utf-8"))
        if counts:
            lines += [
                "",
                "## Custom code boundary",
                "",
                f"Custom implementation bodies were intentionally excluded for {counts[0]} block(s) across {counts[1]} technical artifact(s).",
                "Only their structural behavior, references, and surrounding flow are available here. Rules that exist only inside a body remain unresolved and require an authorised security-reviewed analysis to establish.",
            ]
    if warnings:
        lines += ["", "## Reconciliation findings", ""]
        lines.extend(f"- {_cell(message)}." for message in sorted(set(warnings)))
    _write_ascii(target / "limitations.md", lines)
    return "limitations.md"


def _write_integration_catalogue(
    target: Path,
    workspace: Path,
    warnings: list[str],
) -> tuple[str, int, int]:
    """Compile the shared investigation model into a stable client index."""

    from mcp_server.providers.agent_workspace.integration_model import (
        IntegrationKnowledgeBase,
        IntegrationModelError,
        load_workspace_model,
    )

    # Both on-disk shapes, resolved from the staging root. This built
    # `workspace / "notes" / "integration_model.json"` BY HAND -- a third copy
    # of a path `contract` already owns -- so it saw only the monolith. A run
    # whose agent wrote per-integration files would have published an EMPTY
    # client catalogue with no warning at all, because an absent monolith is
    # the silent `IntegrationKnowledgeBase()` branch below.
    try:
        knowledge = load_workspace_model(workspace).knowledge
    except (OSError, ValueError) as exc:
        # `warnings` is rendered into the CLIENT-FACING
        # `reference/limitations.md`, so the exception TYPE must not go in
        # it: a reader of a paid deliverable was being shown
        # `(ValidationError)`, then `(IntegrationModelError)` -- both of
        # them our vocabulary, neither of them an answer to "what is
        # missing from this document". `IntegrationModelError` messages are
        # written to be client-safe (they name the record and the reason,
        # never the offending value); anything else is summarised rather
        # than quoted, because an `OSError` carries a filesystem path.
        detail = str(exc).strip() if isinstance(exc, IntegrationModelError) else ""
        warnings.append(
            "the working integration catalogue could not be compiled"
            + (f": {detail}" if detail else " and no catalogue is included below")
        )
        knowledge = IntegrationKnowledgeBase()

    journey_count = sum(len(item.journeys) for item in knowledge.integrations)
    lines = [
        "# Integration and journey catalogue",
        "",
        f"Meaningful integrations: {len(knowledge.integrations)}. Journeys: {journey_count}.",
        "",
        "This catalogue reflects the current investigation model; an empty catalogue at the start of an investigation is not evidence that the estate has no integrations.",
        "An integration is a business or operational capability; it can contain several directional journeys, interfaces and implementing units.",
        "",
        "| Integration | Capability | State | Participants | Journeys | Interfaces | Implementing units | Material mappings | Risks | Open questions |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for integration in knowledge.catalogue():
        lines.append(
            f"| {_cell(integration.label)} | {_cell(integration.capability)} | "
            f"{_cell(integration.state)} | {_join(integration.participant_labels)} | "
            f"{integration.journey_count} | {integration.interface_count} | "
            f"{integration.implementing_unit_count} | {integration.mapping_count} | "
            f"{integration.risk_count} | {integration.unknown_count} |"
        )

    for integration in knowledge.integrations:
        lines += ["", f"## {_cell(integration.label)}", ""]
        if integration.summary:
            lines += [_cell(integration.summary), ""]
        participants = {item.id: item.label for item in integration.participants}
        interfaces = {item.id: item for item in integration.interfaces}
        lines += [
            "| Journey | Direction | Purpose | Interfaces | Implementing units | State |",
            "|---|---|---|---|---|---|",
        ]
        units = {item.id: item.label for item in integration.implementing_units}
        for journey in integration.journeys:
            interface_directions = []
            for item_id in journey.interface_ids:
                interface = interfaces.get(item_id)
                if interface is None:
                    continue
                source = participants.get(interface.source_participant_id or "")
                target_participant = participants.get(interface.target_participant_id or "")
                if source and target_participant:
                    interface_directions.append(f"{source} -> {target_participant}")
            direction = " / ".join(interface_directions) or " -> ".join(
                participants[item_id]
                for item_id in journey.participant_ids
                if item_id in participants
            )
            interface_labels = [
                interfaces[item_id].label
                for item_id in journey.interface_ids
                if item_id in interfaces
            ]
            unit_labels = [
                units[item_id] for item_id in journey.implementing_unit_ids if item_id in units
            ]
            lines.append(
                f"| {_cell(journey.label)} | {_cell(direction or 'unresolved')} | "
                f"{_cell(journey.purpose)} | {_join(interface_labels)} | "
                f"{_join(unit_labels)} | {_cell(journey.state)} |"
            )
    _write_ascii(target / "integrations.md", lines)
    return "integrations.md", len(knowledge.integrations), journey_count


def _write_readme(target: Path, receipt: ReferenceRegisterReceipt) -> None:
    lines = [
        "# Deterministic factual reference",
        "",
        "This shelf contains the complete structured facts available for this documentation set.",
        "Narrative documentation explains these records; it does not replace or sample them.",
        "",
        "| Register | Reconciled count |",
        "|---|---:|",
        f"| [Implementing units](implementing_units.md) | {receipt.unit_count} |",
        f"| [Resolved dependencies](dependencies.md) | {receipt.dependency_count} |",
        f"| Unresolved dependencies | {receipt.unresolved_dependency_count} |",
        f"| [Unit interfaces](interfaces_and_channels.md) | {receipt.interface_count} |",
        f"| Channel bindings | {receipt.channel_count} |",
        f"| [Configuration variables](configuration.md) | {receipt.configuration_variable_count} |",
        f"| [Mapping tables](mappings.md) | {receipt.mapping_table_count} |",
        f"| Exact mapping rows | {receipt.mapping_row_count} |",
        f"| [Runtime unit records](runtime.md) | {receipt.runtime_unit_count} |",
        f"| [Meaningful integrations and journeys](integrations.md) | {receipt.integration_count} / {receipt.journey_count} |",
        "| [Evidence limitations](limitations.md) | see register |",
        "",
        f"Reconciliation findings: {len(receipt.reconciliation_warnings)}.",
    ]
    _write_ascii(target / "README.md", lines)


def compile_reference_registers(
    workspace: Path,
    *,
    output_dir: Path | None = None,
    mapping_part_bytes: int = DEFAULT_MAPPING_PART_BYTES,
) -> ReferenceRegisterReceipt:
    """Write complete factual registers and return their reconciled counts.

    ``workspace`` is the root created by the independent agent-workspace
    builder.  A caller may override ``output_dir`` only within
    ``workspace/output``; publication outside the run boundary is refused.
    Existing ``reference/`` output is replaced as one compiler-owned unit so
    stale mapping parts cannot survive a rerun.
    """

    workspace = Path(workspace).resolve()
    output_root = (workspace / "output").resolve()
    target = (output_dir or output_root / REFERENCE_DIR_NAME).resolve()
    if target.parent != output_root:
        raise ReferenceRegisterError("reference output must be a direct child of workspace/output")
    for required in (
        workspace / "facts" / "units.json",
        workspace / "facts" / "dependencies.json",
        workspace / "facts" / "interfaces.json",
        workspace / "facts" / "configuration.json",
        workspace / "scan" / "mapping_accounting.json",
        workspace / "scan" / "capability_ledger.json",
        workspace / "scan" / "inventory.json",
    ):
        if not required.is_file():
            raise ReferenceRegisterError(f"required factual input is absent: {required.name}")

    build = output_root / ".reference-build"
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True, exist_ok=False)
    warnings: list[str] = []
    try:
        units_name, unit_count = _write_units(
            build, _read_json(workspace / "facts" / "units.json", dict)
        )
        dependencies_name, dependency_count, unresolved_count = _write_dependencies(
            build, _read_json(workspace / "facts" / "dependencies.json", dict)
        )
        interfaces_name, interface_count, channel_count = _write_interfaces(
            build, _read_json(workspace / "facts" / "interfaces.json", dict)
        )
        configuration_name, configuration_count = _write_configuration(
            build, _read_json(workspace / "facts" / "configuration.json", dict)
        )
        try:
            mapping_tables = read_complete_list(workspace / "scan" / "mapping_accounting.json")
        except StagedDataError as exc:
            raise ReferenceRegisterError(str(exc)) from exc
        if any(not isinstance(row, dict) for row in mapping_tables):
            raise ReferenceRegisterError("mapping_accounting.json carries a malformed table")
        valid_tables = mapping_tables
        mapping_names, mapping_row_count = _write_mappings(
            build, valid_tables, mapping_part_bytes, warnings
        )
        runtime_path = workspace / "runtime" / "evidence.json"
        runtime_payload = (
            _read_json(runtime_path, dict)
            if runtime_path.is_file()
            else {"mode": "unavailable", "by_unit": {}}
        )
        runtime_name, runtime_unit_count = _write_runtime(build, runtime_payload)
        integrations_name, integration_count, journey_count = _write_integration_catalogue(
            build, workspace, warnings
        )
        limitations_name = _write_limitations(build, workspace, warnings)
        names = (
            "README.md",
            units_name,
            dependencies_name,
            interfaces_name,
            configuration_name,
            *mapping_names,
            runtime_name,
            integrations_name,
            limitations_name,
        )
        provisional = ReferenceRegisterReceipt(
            output_dir=target,
            files=tuple(sorted(names)),
            unit_count=unit_count,
            dependency_count=dependency_count,
            unresolved_dependency_count=unresolved_count,
            interface_count=interface_count,
            channel_count=channel_count,
            configuration_variable_count=configuration_count,
            mapping_table_count=len(valid_tables),
            mapping_row_count=mapping_row_count,
            runtime_unit_count=runtime_unit_count,
            integration_count=integration_count,
            journey_count=journey_count,
            reconciliation_warnings=tuple(sorted(set(warnings))),
        )
        _write_readme(build, provisional)
        if target.exists():
            shutil.rmtree(target)
        build.rename(target)
        return provisional
    except Exception:
        if build.exists():
            shutil.rmtree(build)
        raise
