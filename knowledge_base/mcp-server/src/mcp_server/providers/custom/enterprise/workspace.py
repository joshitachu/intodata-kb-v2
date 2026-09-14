"""Stage the complete sanitized Talend workspace and the exact fact tables.

The scanner (intodoc) writes ``scan/sanitized/`` -- every textual export file
after credential filtering, with a manifest of digests -- and the deterministic
``scan/mapping_accounting.json`` / ``scan/capability_ledger.json``. This module
copies that package into the run's staging directory (``_workspace/``) after
re-verifying every digest, and renders the exact tables OpenCode must reference
instead of reproducing them (``_analysis/_exact/``).

Fail-closed by design: a scan that predates the sanitized package, a digest
mismatch, or a manifest that reports residual redaction findings stops the run
before any model call. There is no raw-source fallback.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp_server.providers.custom.enterprise.layout import (
    EXACT_CAPABILITY,
    EXACT_CONFIGURATION,
    EXACT_DEPENDENCIES,
    EXACT_INDEX,
    EXACT_INTERFACES,
    EXACT_MAPPING_TABLES,
    EXACT_MAPPINGS,
    EXACT_ROOT,
    EXACT_UNITS,
    WORKSPACE_MANIFEST,
    WORKSPACE_ROOT,
)
from mcp_server.providers.custom.knowledge_contracts import write_json


class EnterpriseWorkspaceError(RuntimeError):
    """The sanitized evidence package cannot be trusted or is absent."""


_TEXT_SUFFIXES = {
    ".item", ".properties", ".xml", ".xsl", ".xslt", ".wsdl", ".txt", ".json",
    ".csv", ".prn", ".project", ".md", ".sql", ".yaml", ".yml",
}

# Talend error-handling constructs the deterministic layer can recognise.
_ERROR_COMPONENTS = {
    "tDie", "tWarn", "tLogCatcher", "tStatCatcher", "tAssertCatcher", "tFlowMeterCatcher",
    "cOnException", "cErrorHandler", "cDeadLetterChannel", "cTry",
}
_ERROR_CONNECTORS = {
    "REJECT", "COMPONENT_ERROR", "SUBJOB_ERROR", "FAULT", "ON_COMPONENT_ERROR",
    "ON_SUBJOB_ERROR", "REJECTS", "ERROR", "TRIGGER_OUTPUT_ERROR",
}
_INBOUND_TYPES = {
    "tRESTRequest", "tREST", "tRouteInput", "tMomInput", "tJMSInput", "tKafkaInput",
    "tFileList", "tFTPFileList", "tSOAPInput", "tESBProviderRequest", "tWaitForFile",
    "cREST", "cJMS", "cFile", "cFtp", "cTimer", "cMessagingEndpoint", "cSOAP", "cCXF",
    "cKafka", "cAMQP", "cMQTT", "cHttp", "cSEDA", "cDirect", "cDirectVM",
}
# Components that can START a design on their own (a listener, a consumer, a
# timer, a front door). A database or file read is an inbound DATA interface,
# not a trigger; a route bridge (tRouteInput) is started by its route. Entry
# points are these plus designs no other design calls.
_TRIGGER_TYPES = {
    "tRESTRequest", "tREST", "tESBProviderRequest", "tMomInput", "tJMSInput", "tKafkaInput",
    "tWaitForFile", "cREST", "cJMS", "cFile", "cFtp", "cTimer", "cMessagingEndpoint", "cSOAP",
    "cCXF", "cKafka", "cAMQP", "cMQTT", "cHttp",
}
_OUTBOUND_HINTS = ("Output", "Client", "Send", "Put", "Producer", "Commit", "Delete", "Rename", "Publish")
_INBOUND_HINTS = ("Input", "Request", "Receive", "Get", "Consumer", "List", "Fetch")
_SYSTEM_FAMILIES = (
    ("tDB", "database"), ("tMSSql", "database"), ("tOracle", "database"), ("tMysql", "database"),
    ("tPostgres", "database"), ("tSnowflake", "database"), ("tBigQuery", "database"),
    ("tSalesforce", "saas"), ("tSAP", "erp"), ("tREST", "rest"), ("tHttp", "rest"), ("tHTTP", "rest"),
    ("tSOAP", "soap"), ("tESB", "esb"), ("tRoute", "route_bridge"), ("tMom", "messaging"),
    ("tJMS", "messaging"), ("tKafka", "messaging"), ("tFile", "file"), ("tFTP", "file"),
    ("tS3", "object_store"), ("tAzure", "cloud"), ("tSendMail", "mail"), ("tLDAP", "directory"),
    ("cREST", "rest"), ("cHttp", "rest"), ("cCXF", "soap"), ("cSOAP", "soap"), ("cJMS", "messaging"),
    ("cKafka", "messaging"), ("cAMQP", "messaging"), ("cMQTT", "messaging"), ("cFile", "file"),
    ("cFtp", "file"), ("cMessagingEndpoint", "camel_uri"), ("cDirect", "in_memory"),
    ("cSEDA", "in_memory"), ("cTimer", "scheduler"), ("cMail", "mail"),
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


@dataclass(frozen=True)
class WorkspaceReceipt:
    file_count: int
    byte_count: int
    manifest_sha256: str
    capability_version: str
    source_roots: tuple[str, ...]
    excluded: tuple[dict[str, Any], ...] = ()


def stage_sanitized_workspace(scan_output_dir: Path, staging_dir: Path) -> WorkspaceReceipt:
    """Copy ``scan/sanitized/`` into ``_workspace/`` after verifying every digest."""
    package = scan_output_dir / "scan" / "sanitized"
    manifest_path = package / "manifest.json"
    if not manifest_path.is_file():
        raise EnterpriseWorkspaceError(
            "The scan has no sanitized workspace package (scan/sanitized/manifest.json). "
            "Rescan the project with the current intodoc before an enterprise run."
        )
    manifest = _read_json(manifest_path, None)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise EnterpriseWorkspaceError("scan/sanitized/manifest.json is malformed")
    if manifest.get("residual_free") is False:
        raise EnterpriseWorkspaceError(
            "The sanitized workspace manifest reports residual redaction findings; refusing to expose it"
        )
    expected: dict[str, str] = {}
    for entry in manifest["files"]:
        if not isinstance(entry, dict) or not entry.get("sha256"):
            raise EnterpriseWorkspaceError("sanitized manifest entry without sha256")
        expected[str(entry["sha256"])] = str(entry.get("path") or "")
    target = staging_dir / WORKSPACE_ROOT
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    copied = 0
    byte_count = 0
    package_resolved = package.resolve()
    for source in sorted(package.rglob("*")):
        if not source.is_file() or source == manifest_path:
            continue
        if source.is_symlink() or not source.resolve().is_relative_to(package_resolved):
            raise EnterpriseWorkspaceError(f"sanitized package contains a symlink or escape: {source.name}")
        digest = _sha256_file(source)
        if digest not in expected:
            raise EnterpriseWorkspaceError(
                f"sanitized file not covered by the manifest: {source.relative_to(package).as_posix()}"
            )
        destination = target / source.relative_to(package)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        copied += 1
        byte_count += source.stat().st_size
    if copied != len(manifest["files"]):
        raise EnterpriseWorkspaceError(
            f"sanitized package has {copied} files but the manifest lists {len(manifest['files'])}"
        )
    receipt = WorkspaceReceipt(
        file_count=copied,
        byte_count=byte_count,
        manifest_sha256=_sha256_file(manifest_path),
        capability_version=str(manifest.get("capability_version") or ""),
        source_roots=tuple(
            str(root.get("path") if isinstance(root, dict) else root)
            for root in manifest.get("source_roots") or ()
        ),
        excluded=tuple(entry for entry in manifest.get("excluded") or () if isinstance(entry, dict)),
    )
    write_json(staging_dir / WORKSPACE_MANIFEST, {
        "schema_version": 1,
        "source": "scan/sanitized",
        "manifest_sha256": receipt.manifest_sha256,
        "capability_version": receipt.capability_version,
        "file_count": receipt.file_count,
        "byte_count": receipt.byte_count,
        "source_roots": list(receipt.source_roots),
        "excluded": list(receipt.excluded),
        "files": manifest["files"],
        "note": (
            "Complete sanitized export. Secrets are placeholders such as [PASSWORD_REDACTED]; "
            "a placeholder never proves two endpoints are the same system."
        ),
    })
    return receipt


def workspace_path_for(unit: dict[str, Any]) -> str:
    """Staging-relative path of a design's sanitized ``.item`` file."""
    source_root = str(unit.get("source_root") or "").strip("/")
    design_dir = str(unit.get("source_design") or "process").strip("/")
    path = str(unit.get("path") or "").lstrip("/")
    parts = [WORKSPACE_ROOT.as_posix()]
    if source_root and source_root != ".":
        parts.append(source_root)
    parts.append(design_dir)
    parts.append(path)
    return "/".join(part for part in parts if part)


@dataclass
class ExactTables:
    units: dict[str, dict[str, Any]]
    dependencies: dict[str, Any]
    interfaces: dict[str, Any]
    configuration: dict[str, Any]
    mappings: list[dict[str, Any]]
    capability: dict[str, Any]
    mapping_table_ids: tuple[str, ...] = ()
    entry_point_units: tuple[str, ...] = ()
    error_handling_units: tuple[str, ...] = ()
    context_group_keys: tuple[str, ...] = ()
    inline_context_units: tuple[str, ...] = ()
    warnings: list[str] = field(default_factory=list)


_DIRECTION_ALIASES = {
    "input": "inbound", "in": "inbound", "inbound": "inbound", "consumer": "inbound", "from": "inbound",
    "output": "outbound", "out": "outbound", "outbound": "outbound", "producer": "outbound", "to": "outbound",
    "both": "both", "bidirectional": "both",
}


def _normalize_direction(value: Any) -> str:
    """One vocabulary for every interface source.

    The scanner manifest says ``input``/``output`` for endpoints, channels and
    repository resources; component classification says ``inbound``/``outbound``.
    Mixing them silently broke the trigger rule for a route fronted by a REST
    endpoint that another route also calls.
    """
    return _DIRECTION_ALIASES.get(str(value or "").strip().lower(), "unknown")


def _component_direction(component_type: str) -> str:
    if component_type in _INBOUND_TYPES:
        return "inbound"
    if any(hint in component_type for hint in _OUTBOUND_HINTS):
        return "outbound"
    if any(hint in component_type for hint in _INBOUND_HINTS):
        return "inbound"
    return "unknown"


def _system_family(component_type: str) -> str:
    for prefix, family in _SYSTEM_FAMILIES:
        if component_type.startswith(prefix):
            return family
    return ""


def _unit_key(job: dict[str, Any]) -> str:
    return str(job.get("job_id") or job.get("name") or "")


def build_exact_tables(
    scan_output_dir: Path,
    staging_dir: Path,
    *,
    unit_allowlist: set[str] | None = None,
) -> ExactTables:
    """Render the deterministic fact tables under ``_analysis/_exact/``."""
    scan = _read_json(scan_output_dir / "scan_result.json", None)
    if not isinstance(scan, dict):
        raise EnterpriseWorkspaceError("scan_result.json is missing or malformed")
    manifest = scan.get("manifest") if isinstance(scan.get("manifest"), dict) else {}
    segmented = _read_json(scan_output_dir / "scan" / "manifest.json", None)
    if isinstance(segmented, dict):
        manifest = {**segmented, **manifest} if manifest else segmented
    mappings_raw = scan.get("mapping_accounting")
    if not isinstance(mappings_raw, list) or not mappings_raw:
        mappings_raw = _read_json(scan_output_dir / "scan" / "mapping_accounting.json", None)
    if not isinstance(mappings_raw, list):
        raise EnterpriseWorkspaceError(
            "The scan has no deterministic mapping accounting (scan/mapping_accounting.json); "
            "rescan with the current intodoc before an enterprise run."
        )
    capability = scan.get("capability_ledger")
    if not isinstance(capability, dict):
        capability = _read_json(scan_output_dir / "scan" / "capability_ledger.json", None)
    if not isinstance(capability, dict):
        raise EnterpriseWorkspaceError("The scan has no capability ledger; rescan before an enterprise run.")

    jobs = [job for job in scan.get("jobs", []) or [] if isinstance(job, dict)]
    if unit_allowlist is not None:
        jobs = [job for job in jobs if _unit_key(job) in unit_allowlist]
    known_units = {_unit_key(job) for job in jobs}
    mappings = [
        row for row in mappings_raw
        if isinstance(row, dict) and (unit_allowlist is None or str(row.get("unit_id") or "") in known_units)
    ]

    # --- dependencies -----------------------------------------------------
    edges: list[dict[str, Any]] = []
    callers: dict[str, set[str]] = {unit: set() for unit in known_units}
    callees: dict[str, set[str]] = {unit: set() for unit in known_units}
    for job in jobs:
        source = _unit_key(job)
        for ref in job.get("trunjob_refs", []) or []:
            target = str(ref.get("resolved_job_id") or "")
            edge = {
                "source": source, "target": target,
                "declared_target": str(ref.get("child_job") or ""),
                "relation_type": "job_calls_job",
                "resolution": str(ref.get("resolution") or "unresolved"),
                "reference_source": str(ref.get("reference_source") or ""),
                "version": str(ref.get("version") or ""),
                "context_name": str(ref.get("context_name") or ""),
                "transmit_whole_context": ref.get("transmit_whole_context"),
                "context_params": [
                    {"name": str(p.get("name") or ""), "value": str(p.get("value") or "")}
                    for p in ref.get("context_params", []) or [] if isinstance(p, dict)
                ],
                "dynamic": bool(ref.get("dynamic_job")),
            }
            edges.append(edge)
            if target and target in known_units:
                callees[source].add(target)
                callers[target].add(source)
        for ref in job.get("artifact_refs", []) or []:
            target = str(ref.get("resolved_artifact_id") or "")
            edge = {
                "source": source, "target": target,
                "declared_target": str(ref.get("target_name") or ""),
                "relation_type": str(ref.get("relation_type") or ""),
                "resolution": str(ref.get("resolution") or "unresolved"),
                "reference_source": str(ref.get("reference_source") or ""),
                "version": str(ref.get("version") or ""),
                "context_name": str(ref.get("context_name") or ""),
                "use_route_context": ref.get("use_route_context"),
                "component_name": str(ref.get("component_name") or ""),
                "context_params": [
                    {"name": str(p.get("name") or ""), "value": str(p.get("value") or "")}
                    for p in ref.get("context_params", []) or [] if isinstance(p, dict)
                ],
            }
            edges.append(edge)
            if target and target in known_units:
                callees[source].add(target)
                callers[target].add(source)
    resolved_edges = [e for e in edges if e["target"] and e["target"] in known_units]
    unresolved_edges = [e for e in edges if not (e["target"] and e["target"] in known_units)]

    # --- interfaces + units -------------------------------------------------
    endpoints = [e for e in manifest.get("artifact_endpoints", []) or [] if isinstance(e, dict)]
    channels = [c for c in manifest.get("artifact_channels", []) or [] if isinstance(c, dict)]
    resources = [r for r in manifest.get("artifact_resources", []) or [] if isinstance(r, dict)]
    interfaces_by_unit: dict[str, list[dict[str, Any]]] = {unit: [] for unit in known_units}
    for endpoint in endpoints:
        unit = str(endpoint.get("artifact") or "")
        if unit in interfaces_by_unit:
            interfaces_by_unit[unit].append({
                "kind": "endpoint", "type": str(endpoint.get("endpoint_type") or ""),
                "direction": _normalize_direction(endpoint.get("direction")),
                "component_name": str(endpoint.get("component_name") or ""),
                "descriptor": endpoint.get("endpoint_descriptor"),
                "provenance": str(endpoint.get("provenance") or ""),
            })
    for channel in channels:
        unit = str(channel.get("artifact") or "")
        if unit in interfaces_by_unit:
            interfaces_by_unit[unit].append({
                "kind": "channel", "type": str(channel.get("channel_type") or ""),
                "channel": str(channel.get("channel") or ""),
                "direction": _normalize_direction(channel.get("direction")),
                "component_name": str(channel.get("component_name") or ""),
                "descriptor": channel.get("channel_descriptor"),
                "provenance": str(channel.get("provenance") or ""),
            })
    for resource in resources:
        unit = str(resource.get("artifact") or "")
        if unit in interfaces_by_unit:
            interfaces_by_unit[unit].append({
                "kind": "resource", "type": str(resource.get("resource_type") or ""),
                "resource": str(resource.get("resource") or ""),
                "direction": _normalize_direction(resource.get("direction")),
                "component_name": str(resource.get("component_name") or ""),
                "provenance": str(resource.get("provenance") or ""),
            })
    units: dict[str, dict[str, Any]] = {}
    entry_points: list[str] = []
    error_units: list[str] = []
    inline_context_units: list[str] = []
    for job in jobs:
        unit = _unit_key(job)
        components = [c for c in job.get("components", []) or [] if isinstance(c, dict)]
        connections = [c for c in job.get("connections", []) or [] if isinstance(c, dict)]
        seen_components = {str(i.get("component_name") or "") for i in interfaces_by_unit[unit]}
        component_interfaces: list[dict[str, Any]] = []
        for component in components:
            ctype = str(component.get("component_type") or "")
            family = _system_family(ctype)
            direction = _component_direction(ctype)
            if family and str(component.get("unique_name") or "") not in seen_components:
                component_interfaces.append({
                    "kind": "component", "type": ctype, "family": family,
                    "direction": direction,
                    "component_name": str(component.get("unique_name") or ""),
                    "active": component.get("active", True) is not False,
                    "repository_connection": str(component.get("metadata_connection_ref") or ""),
                })
        interfaces_by_unit[unit].extend(component_interfaces)
        error_constructs = sorted({
            str(c.get("component_type")) for c in components
            if str(c.get("component_type") or "") in _ERROR_COMPONENTS and c.get("active", True) is not False
        } | {
            str(c.get("connector_name")) for c in connections
            if str(c.get("connector_name") or "") in _ERROR_CONNECTORS
        })
        has_inbound = any(
            item.get("direction") == "inbound" and item.get("kind") in {"endpoint", "channel", "component"}
            for item in interfaces_by_unit[unit]
        )
        has_trigger = any(
            (item.get("kind") in {"endpoint", "channel"}
             and (item.get("direction") == "inbound" or str(item.get("provenance") or "") in _TRIGGER_TYPES))
            or (item.get("kind") == "component" and item.get("type") in _TRIGGER_TYPES and item.get("active", True))
            for item in interfaces_by_unit[unit]
        )
        is_root = not callers[unit]
        kind = str(job.get("artifact_kind") or "job")
        entry_point = kind in {"job", "route"} and (has_trigger or is_root)
        if entry_point:
            entry_points.append(unit)
        if error_constructs:
            error_units.append(unit)
        inline_groups = [g for g in job.get("context_groups", []) or [] if isinstance(g, dict)]
        if inline_groups:
            inline_context_units.append(unit)
        disabled = sum(1 for c in components if c.get("active", True) is False)
        units[unit] = {
            "unit_id": unit,
            "display_name": str(job.get("name") or unit),
            "kind": kind,
            "role": str(job.get("artifact_role") or "standalone"),
            "group": str(job.get("group") or "Global"),
            "source_root": str(job.get("source_root") or ""),
            "path": str(job.get("path") or ""),
            "workspace_path": workspace_path_for(job),
            "version": str(job.get("version") or ""),
            "purpose": str(job.get("purpose") or ""),
            "component_count": len(components),
            "disabled_component_count": disabled,
            "component_types": sorted({str(c.get("component_type") or "") for c in components}),
            "entry_point": entry_point,
            "inbound_interfaces": has_inbound,
            "trigger_interfaces": has_trigger,
            "callers": sorted(callers[unit]),
            "callees": sorted(callees[unit]),
            "error_handling_constructs": error_constructs,
            "has_error_handling": bool(error_constructs),
            "mapping_table_ids": sorted(
                str(m.get("table_id") or "") for m in mappings if str(m.get("unit_id") or "") == unit
            ),
            "scan_quality": str(job.get("scan_quality") or "complete"),
        }

    # --- configuration ------------------------------------------------------
    context_groups = [g for g in scan.get("context_groups", []) or [] if isinstance(g, dict)]
    context_metadata = manifest.get("context_metadata", {}) or {}
    groups_out = []
    for group in context_groups:
        key = str(group.get("context_key") or group.get("name") or "")
        variables = []
        for variable in group.get("variables", []) or []:
            if not isinstance(variable, dict):
                continue
            variables.append({
                "name": str(variable.get("name") or ""),
                "type": str(variable.get("type") or ""),
                "values": dict(variable.get("values", {}) or {}),
                "redacted_environments": sorted((variable.get("redaction_descriptors") or {}).keys()),
            })
        groups_out.append({
            "key": key, "name": str(group.get("name") or ""),
            "source_root": str(group.get("source_root") or ""),
            "source_role": str(group.get("source_role") or ""),
            "environments": sorted({env for v in variables for env in v["values"]}),
            "variables": variables,
            "used_by": sorted(u for u in (manifest.get("context_used_by", {}) or {}).get(key, []) if u in known_units),
            "metadata": context_metadata.get(key, {}),
        })
    inline_out = {}
    for job in jobs:
        unit = _unit_key(job)
        groups = [g for g in job.get("context_groups", []) or [] if isinstance(g, dict)]
        if not groups:
            continue
        inline_out[unit] = [
            {
                "name": str(g.get("name") or "inline"),
                "variables": [
                    {
                        "name": str(v.get("name") or ""), "type": str(v.get("type") or ""),
                        "values": dict(v.get("values", {}) or {}),
                        "repository_context_id": str(v.get("repository_context_id") or ""),
                    }
                    for v in g.get("variables", []) or [] if isinstance(v, dict)
                ],
            }
            for g in groups
        ]
    configuration = {
        "schema_version": "exact-configuration-1",
        "context_groups": groups_out,
        "inline_contexts": inline_out,
        "job_context_links": {
            u: links for u, links in (manifest.get("job_context_links", {}) or {}).items() if u in known_units
        },
        "unresolved_context_refs": {
            u: refs for u, refs in (manifest.get("unresolved_context_refs", {}) or {}).items() if u in known_units
        },
        "repository_connections": [
            {
                "name": str(c.get("name") or ""), "connection_type": str(c.get("connection_type") or ""),
                "source_root": str((c.get("additional_params") or {}).get("source_root") or ""),
                "used_by": sorted(
                    u for u in (manifest.get("connection_used_by", {}) or {}).get(str(c.get("name") or ""), [])
                    if u in known_units
                ),
            }
            for c in scan.get("metadata_connections", []) or [] if isinstance(c, dict)
        ],
    }

    dependencies = {
        "schema_version": "exact-dependencies-1",
        "edges": resolved_edges,
        "unresolved": unresolved_edges,
        "roots": sorted(u for u in known_units if not callers[u]),
        "leaves": sorted(u for u in known_units if not callees[u]),
        "routine_links": {
            u: links for u, links in (manifest.get("job_routine_links", {}) or {}).items() if u in known_units
        },
        "unresolved_routine_refs": {
            u: refs for u, refs in (manifest.get("unresolved_routine_refs", {}) or {}).items() if u in known_units
        },
    }
    interfaces = {
        "schema_version": "exact-interfaces-1",
        "by_unit": interfaces_by_unit,
        "channels": [
            {**c, "dynamic": not _literal_channel(str(c.get("channel") or ""))}
            for c in channels if str(c.get("artifact") or "") in known_units
        ],
    }

    exact_dir = staging_dir / EXACT_ROOT
    exact_dir.mkdir(parents=True, exist_ok=True)
    write_json(staging_dir / EXACT_UNITS, {"schema_version": "exact-units-1", "units": units})
    write_json(staging_dir / EXACT_DEPENDENCIES, dependencies)
    write_json(staging_dir / EXACT_INTERFACES, interfaces)
    write_json(staging_dir / EXACT_CONFIGURATION, configuration)
    write_json(staging_dir / EXACT_MAPPINGS, {"schema_version": "exact-mapping-accounting-1", "tables": mappings})
    write_json(staging_dir / EXACT_CAPABILITY, capability)
    table_ids = []
    tables_dir = staging_dir / EXACT_MAPPING_TABLES
    tables_dir.mkdir(parents=True, exist_ok=True)
    for table in mappings:
        table_id = str(table.get("table_id") or "")
        if not table_id:
            continue
        table_ids.append(table_id)
        (tables_dir / f"{table_id}.md").write_text(render_mapping_table(table), encoding="utf-8")
    (staging_dir / EXACT_INDEX).write_text(
        _render_exact_index(units, table_ids, len(resolved_edges), len(unresolved_edges), len(groups_out)),
        encoding="utf-8",
    )
    return ExactTables(
        units=units, dependencies=dependencies, interfaces=interfaces, configuration=configuration,
        mappings=mappings, capability=capability, mapping_table_ids=tuple(sorted(table_ids)),
        entry_point_units=tuple(sorted(entry_points)), error_handling_units=tuple(sorted(error_units)),
        context_group_keys=tuple(sorted(g["key"] for g in groups_out if g["key"])),
        inline_context_units=tuple(sorted(inline_context_units)),
    )


def _literal_channel(value: str) -> bool:
    if not value or "[" in value:
        return False
    return not re.search(r"context\.|globalMap|\+|\$\{", value)


def _md_cell(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("|", "\\|").replace("\n", " ").encode("ascii", "replace").decode("ascii")


def render_mapping_table(table: dict[str, Any]) -> str:
    """Exact, untruncated mapping table (ASCII). The evidence of record for a mapper."""
    lines = [
        f"# Exact mapping table {table.get('table_id', '')}",
        "",
        f"Design: `{table.get('unit_id', '')}`; mapper: `{table.get('component_name', '')}` "
        f"({table.get('mapper_kind', '')}). Deterministic scanner extraction; not model output.",
        "",
        "| Denominator | Count |",
        "|---|---|",
        f"| Offered input fields | {table.get('offered_fields', 0)} |",
        f"| Offered fields enumerable | {table.get('offered_enumerable', True)} |",
        f"| Written output fields | {table.get('written_fields', 0)} |",
        f"| Mapped from input | {table.get('mapped_from_input', 0)} |",
        f"| Constant or generated | {table.get('constant_or_generated', 0)} |",
        f"| Lookup derived | {table.get('lookup_derived', 0)} |",
        f"| Dynamic or unresolved | {table.get('dynamic_unresolved', 0)} |",
        "",
    ]
    unenumerable = table.get("unenumerable_inputs") or []
    if unenumerable:
        lines += ["Non-enumerable inputs (dynamic schema): " + ", ".join(_md_cell(x) for x in unenumerable), ""]
    lookups = table.get("lookup_tables") or []
    if lookups:
        lines += ["| Lookup table | Match model | Load model | Inner join | Filter |", "|---|---|---|---|---|"]
        for lookup in lookups:
            lines.append(
                f"| {_md_cell(lookup.get('name'))} | {_md_cell(lookup.get('match_model'))} | "
                f"{_md_cell(lookup.get('load_model'))} | {_md_cell(lookup.get('inner_join'))} | "
                f"{_md_cell(lookup.get('expression_filter'))} |"
            )
        lines.append("")
    filters = table.get("filters") or []
    if filters:
        lines += ["Filters: " + "; ".join(f"`{_md_cell(f)}`" for f in filters), ""]
    rejects = table.get("reject_outputs") or []
    if rejects:
        lines += ["Reject outputs: " + ", ".join(_md_cell(r) for r in rejects), ""]
    lines += ["| Output table | Output field | Expression | Classification | Source refs | Dynamic refs |",
              "|---|---|---|---|---|---|"]
    for row in table.get("rows") or []:
        lines.append(
            f"| {_md_cell(row.get('output_table'))} | {_md_cell(row.get('output_field'))} | "
            f"`{_md_cell(row.get('expression'))}` | {_md_cell(row.get('classification'))} | "
            f"{_md_cell(', '.join(row.get('source_refs') or []))} | "
            f"{_md_cell(', '.join(row.get('dynamic_refs') or []))} |"
        )
    unwritten = table.get("unwritten_offered") or []
    lines += ["", f"Offered input fields not written to any output ({len(unwritten)}): "
              + (", ".join(_md_cell(x) for x in unwritten) if unwritten else "none"), ""]
    return "\n".join(lines)


def _render_exact_index(
    units: dict[str, dict[str, Any]], table_ids: list[str], edges: int, unresolved: int, groups: int,
) -> str:
    lines = [
        "# Exact fact tables (deterministic; reference these, never retype them)",
        "",
        f"- units.json: {len(units)} designs with workspace paths, callers/callees, entry-point and error-handling facts",
        f"- dependencies.json: {edges} resolved call/invocation edges, {unresolved} unresolved or ambiguous",
        "- interfaces.json: inbound/outbound endpoints, channels, repository connections and system-facing components per design",
        f"- configuration.json: {groups} repository context groups, inline contexts, context links, repository connections",
        f"- mapping_accounting.json + mapping_tables/<table_id>.md: {len(table_ids)} exact mapping tables",
        "- capability_ledger.json: what the scanner parsed fully, partially, dynamically or not at all",
        "",
        "Cite a table by its id (for example `mapping_table:<table_id>`) and explain it; do not reproduce rows.",
        "",
    ]
    return "\n".join(lines)
