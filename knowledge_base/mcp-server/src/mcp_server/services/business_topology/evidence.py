"""Build a factual, non-semantic evidence package for topology synthesis.

This module deliberately does not infer applications, systems, aliases,
business objects, purposes, or streams.  It only assigns stable evidence IDs
to scanner facts and accepted Phase 1/2 supporting artifacts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


_SCAN_ARTIFACTS = (
    "scan_result.json",
    "scan/manifest.json",
    "scan/lineage.json",
    "scan/landscape.json",
)

_SUPPLEMENTAL_CLAIM_TYPES = {
    "implemented": "implemented_supporting",
    "observed_runtime": "observed_runtime",
    "intended_documented": "intended_documented",
}

_SAFE_COMPONENT_PARAMS = {
    "ACCEPT_TYPE",
    "CONTENT_TYPE",
    "ENDPOINT_TYPE",
    "HTTP_METHOD",
    "METHOD",
    "SCHEMA",
    "TABLE",
    "TABLE_ACTION",
}


@dataclass(frozen=True)
class BusinessTopologyEvidenceBundle:
    root: Path
    overview_path: Path
    starter_pack_path: Path
    evidence_ids_path: Path
    scan_fingerprint: str
    evidence_ids: frozenset[str]
    evidence_types: dict[str, str]
    evidence_records: dict[str, dict[str, Any]]
    artifact_ids: frozenset[str]
    component_ids: frozenset[str]
    lineage_ids: frozenset[str]


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def _factual_landscape(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {key: item for key, item in value.items() if key != "semantic_topology"}


def compute_scan_fingerprint(scan_output_dir: Path) -> str:
    """Hash the current factual scan inputs, excluding semantic overlays.

    The filename and canonical JSON payload are both hashed, making the result
    stable across whitespace changes while still detecting artifact removal.
    At least one scanner artifact must exist.
    """

    digest = hashlib.sha256()
    found = False
    for relative in _SCAN_ARTIFACTS:
        path = scan_output_dir / relative
        if not path.is_file():
            continue
        payload = _load_json(path, None)
        if payload is None:
            continue
        if relative == "scan/landscape.json":
            payload = _factual_landscape(payload)
        found = True
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_canonical_json(payload))
        digest.update(b"\0")
    if not found:
        raise FileNotFoundError(f"No factual scan artifacts found in {scan_output_dir}")
    return digest.hexdigest()


def _evidence_id(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(_canonical_json(value)).hexdigest()[:20]
    return f"{prefix}:{digest}"


def artifact_evidence_id(job: dict[str, Any]) -> str:
    """Return the stable evidence ID used for a scanner artifact record."""

    root = str(job.get("source_root") or "")
    job_id = str(job.get("job_id") or job.get("name") or job.get("path") or "unknown")
    return _evidence_id("artifact", {"source_root": root, "job_id": job_id})


def component_evidence_id(artifact_id: str, component: dict[str, Any]) -> str:
    """Return the stable evidence ID used for one artifact component."""

    return _evidence_id(
        "component",
        {
            "artifact_id": artifact_id,
            "name": component.get("unique_name") or "",
            "type": component.get("component_type") or "",
        },
    )


def lineage_evidence_id(entry: dict[str, Any]) -> str:
    """Return the stable evidence ID used for a scanner lineage entry."""

    return _evidence_id("lineage", entry)


def _safe_component_params(component: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for param in component.get("params") or []:
        if not isinstance(param, dict):
            continue
        name = str(param.get("name") or "")
        descriptor = param.get("redaction_descriptor")
        if name in _SAFE_COMPONENT_PARAMS:
            result.append({"name": name, "value": param.get("value"), "redaction_descriptor": descriptor})
        elif descriptor:
            result.append({"name": name, "value": "[REDACTED]", "redaction_descriptor": descriptor})
    return result


def _jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _document_records(staging_dir: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for path in sorted((staging_dir / "_phase1").glob("*.json")):
        records.append({
            "id": f"phase1:{path.name}",
            "type": "phase1_analysis",
            "path": path.relative_to(staging_dir).as_posix(),
        })
    for path in sorted((staging_dir / "_analysis").glob("*.md")):
        if path.name.startswith("_") or "_chunk_" in path.name:
            continue
        records.append({
            "id": f"analysis:{path.name}",
            "type": "phase2_analysis",
            "path": path.relative_to(staging_dir).as_posix(),
        })
    return records


def _phase1_claim_records(staging_dir: Path) -> list[dict[str, Any]]:
    """Expose accepted supplemental claims with their provenance class intact.

    A whole Phase 1 packet is useful context, but it is not evidence that every
    kind of claim in the packet is true.  Assigning an ID to each accepted claim
    prevents runtime observations and intended-state assertions from borrowing
    authority from unrelated prose in the same JSON file.
    """

    records: list[dict[str, Any]] = []
    for path in sorted((staging_dir / "_phase1").glob("*.json")):
        payload = _load_json(path, {})
        if not isinstance(payload, dict):
            continue
        source_artifact_id = f"phase1:{path.name}"
        for claim in payload.get("claims") or []:
            if not isinstance(claim, dict):
                continue
            claim_class = str(claim.get("claim_class") or "")
            evidence_type = _SUPPLEMENTAL_CLAIM_TYPES.get(claim_class)
            if evidence_type is None:
                continue
            statement = str(claim.get("statement") or "").strip()
            source_evidence_ids = [
                str(value) for value in claim.get("evidence_ids") or [] if str(value)
            ]
            if not statement or (
                claim_class in {"observed_runtime", "intended_documented"}
                and not source_evidence_ids
            ):
                continue
            normalized = {
                "claim_class": claim_class,
                "claim_id": str(claim.get("claim_id") or ""),
                "statement": statement,
                "source_evidence_ids": source_evidence_ids,
                "fact_ids": [str(value) for value in claim.get("fact_ids") or []],
                "source_artifact_id": source_artifact_id,
            }
            records.append(
                {
                    "id": _evidence_id("supporting-claim", normalized),
                    "type": evidence_type,
                    **normalized,
                }
            )
    return records


def _phase2_claim_records(staging_dir: Path) -> list[dict[str, Any]]:
    """Load the concise semantic index produced by Phase 2 claim mode.

    Claims may enrich labels, summaries, risks, and unknowns, but they are not
    directional scanner evidence.  The topology validator therefore still
    requires scanner relation/resource/endpoint/channel/lineage IDs for every
    data-stream edge.
    """

    records: list[dict[str, Any]] = []
    claim_root = staging_dir / "_analysis" / "_claims"
    for path in sorted(claim_root.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("record_type") != "claim":
                continue
            claim_id = str(row.get("claim_id") or "")
            if not claim_id:
                continue
            records.append(
                {
                    "id": claim_id,
                    "type": "phase2_claim",
                    "group": row.get("group") or "",
                    "task_id": row.get("task_id") or "",
                    "concern": row.get("concern") or "",
                    "section": row.get("section") or "",
                    "kind": row.get("kind") or "",
                    "unit_names": row.get("unit_names") or [],
                    "related_units": row.get("related_units") or [],
                    "statement": row.get("statement") or "",
                    "source_evidence_refs": row.get("evidence_refs") or [],
                    "confidence": row.get("confidence") or "unknown",
                    "source_ledger": path.relative_to(staging_dir).as_posix(),
                }
            )
    return records


def _starter_pack(staging_dir: Path, overview: dict[str, Any]) -> str:
    starter_overview = {
        key: value
        for key, value in overview.items()
        if key != "artifact_inventory"
    }
    parts = [
        "# Business Topology Starter Evidence\n",
        "Accepted Phase 1/2 analyses are supporting context, not "
        "implementation truth. Verify every technical relationship with scanner evidence IDs.\n",
        "## Factual Evidence Overview\n\n```json\n"
        + json.dumps(starter_overview, indent=2, ensure_ascii=False)
        + "\n```\n",
    ]
    analyses = [
        path.relative_to(staging_dir).as_posix()
        for path in sorted((staging_dir / "_analysis").glob("*.md"))
        if not path.name.startswith("_") and "_chunk_" not in path.name
    ]
    parts.append(
        "\n## Accepted Phase 2 Analyses\n\nThese files are available through scoped evidence tools:\n"
        + "\n".join(f"- {path}" for path in analyses)
        + "\n"
    )
    phase1 = [
        path.relative_to(staging_dir).as_posix()
        for path in sorted((staging_dir / "_phase1").glob("*.json"))
    ]
    parts.append(
        "\n## Accepted Phase 1 Analyses\n\nThese files are available through scoped evidence tools:\n"
        + "\n".join(f"- {path}" for path in phase1)
        + "\n"
    )
    # Composite sub-project runs: the orchestrator stages a deterministic
    # cross-project index describing how this sub-project connects to its
    # siblings (job-call edges, shared systems). Including it here lets the
    # topology overlay describe the connective layer with scanner-backed facts
    # instead of guessing, without loading sibling sub-projects' content.
    cross_index_path = staging_dir / "_topology" / "cross_project_index.json"
    if cross_index_path.is_file():
        try:
            cross_index_text = cross_index_path.read_text(encoding="utf-8")
        except OSError:
            cross_index_text = ""
        if cross_index_text:
            max_bytes = 20_000
            if len(cross_index_text.encode("utf-8")) > max_bytes:
                cross_index_text = (
                    cross_index_text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
                    + "\n... [truncated cross-project index]"
                )
            parts.append(
                "\n## Cross-Project Context (composite upload)\n\n"
                "This sub-project is part of a composite upload. The deterministic "
                "index below lists cross-sub-project call edges and shared external "
                "systems. Treat sibling sub-projects as EXTERNAL systems: reference "
                "them, do not describe their internals.\n\n```json\n"
                + cross_index_text
                + "\n```\n"
            )
    return "".join(parts)


def build_business_topology_evidence(
    *,
    project_id: str,
    scan_output_dir: Path,
    staging_dir: Path,
    unit_allowlist: set[str] | frozenset[str] | None = None,
) -> BusinessTopologyEvidenceBundle:
    """Materialize a stable factual evidence ledger and bounded starter pack.

    ``unit_allowlist`` (composite sub-project scoping) restricts scanner
    artifact/component rows and lineage entries to the scoped partition's
    canonical units, so a per-sub-project topology overlay only claims and
    cites its own artifacts. Landscape system/application/edge rows and typed
    manifest rows stay upload-wide deliberately: they carry the cross-project
    connective evidence (labeled as such in the starter pack via the
    cross-project index). ``None`` keeps the full scan: behaviour unchanged.
    """

    root = staging_dir / "_topology"
    evidence_root = root / "evidence"
    evidence_root.mkdir(parents=True, exist_ok=True)

    scan = _load_json(scan_output_dir / "scan_result.json", {})
    manifest = _load_json(scan_output_dir / "scan" / "manifest.json", {})
    lineage = _load_json(scan_output_dir / "scan" / "lineage.json", {})
    landscape = _factual_landscape(
        _load_json(scan_output_dir / "scan" / "landscape.json", {})
    )
    fingerprint = compute_scan_fingerprint(scan_output_dir)

    if unit_allowlist is not None:
        allowed_jobs = [
            job for job in scan.get("jobs") or []
            if isinstance(job, dict)
            and str(job.get("job_id") or job.get("name") or "") in unit_allowlist
        ]
        allowed_display_names = {
            str(job.get("name") or job.get("job_id") or "") for job in allowed_jobs
        }
        scan = {**scan, "jobs": allowed_jobs}
        lineage = {
            **lineage,
            "entries": [
                entry for entry in lineage.get("entries") or []
                if isinstance(entry, dict)
                and str(entry.get("job_name") or "") in allowed_display_names
            ],
        }

    records: list[dict[str, Any]] = []
    artifact_ids: set[str] = set()
    component_ids: set[str] = set()
    lineage_ids: set[str] = set()

    scanner_identity_by_id: dict[str, str] = {}
    landscape_system_rows: list[dict[str, Any]] = []
    for item in landscape.get("systems") or []:
        if not isinstance(item, dict) or not str(item.get("id") or ""):
            continue
        value = {
            "scanner_id": str(item["id"]),
            "name": str(item.get("name") or ""),
            "kind": str(item.get("kind") or ""),
            "source_roots": [str(root) for root in item.get("source_roots") or []],
        }
        record = {
            "id": _evidence_id("landscape-system", value),
            "type": "scanner_system",
            **value,
        }
        scanner_identity_by_id[value["scanner_id"]] = record["id"]
        landscape_system_rows.append(record)
        records.append(record)

    landscape_application_rows: list[dict[str, Any]] = []
    for item in landscape.get("applications") or []:
        if not isinstance(item, dict) or not str(item.get("id") or ""):
            continue
        value = {
            "scanner_id": str(item["id"]),
            "name": str(item.get("label") or item.get("name") or ""),
            "project_id": str(item.get("project_id") or ""),
            "source_root": str(item.get("source_root") or ""),
            "source_roots": [str(root) for root in item.get("source_roots") or []],
            "role": str(item.get("role") or ""),
        }
        record = {
            "id": _evidence_id("landscape-application", value),
            "type": "scanner_application",
            **value,
        }
        scanner_identity_by_id[value["scanner_id"]] = record["id"]
        landscape_application_rows.append(record)
        records.append(record)

    landscape_edge_rows: list[dict[str, Any]] = []
    for item in landscape.get("edges") or []:
        if not isinstance(item, dict):
            continue
        source_scanner_id = str(item.get("source") or "")
        destination_scanner_id = str(item.get("target") or "")
        source_identity_id = scanner_identity_by_id.get(source_scanner_id)
        destination_identity_id = scanner_identity_by_id.get(destination_scanner_id)
        if source_identity_id is None or destination_identity_id is None:
            continue
        value = {
            "source_scanner_id": source_scanner_id,
            "destination_scanner_id": destination_scanner_id,
            "source_identity_evidence_id": source_identity_id,
            "destination_identity_evidence_id": destination_identity_id,
            "dependency_type": str(item.get("dependency_type") or ""),
            "mechanism": str(item.get("mechanism") or ""),
            "direction": str(item.get("direction") or ""),
            "reliability": str(item.get("reliability") or ""),
            "artifact_names": sorted(
                {
                    str(provenance.get("artifact_id"))
                    for provenance in item.get("provenance") or []
                    if isinstance(provenance, dict) and provenance.get("artifact_id")
                }
            ),
        }
        record = {
            "id": _evidence_id("landscape-edge", value),
            "type": "scanner_edge",
            **value,
        }
        landscape_edge_rows.append(record)
        records.append(record)

    artifact_rows: list[dict[str, Any]] = []
    for job in scan.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        aid = artifact_evidence_id(job)
        artifact_ids.add(aid)
        components: list[dict[str, Any]] = []
        for component in job.get("components") or []:
            if not isinstance(component, dict):
                continue
            cid = component_evidence_id(aid, component)
            component_ids.add(cid)
            component_record = {
                "id": cid,
                "type": "scanner_component",
                "artifact_id": aid,
                "name": component.get("unique_name") or "",
                "component_type": component.get("component_type") or "",
                "metadata_connection_ref": component.get("metadata_connection_ref") or "",
                "safe_params": _safe_component_params(component),
            }
            components.append(component_record)
            records.append(component_record)
        artifact_record = {
            "id": aid,
            "type": "scanner_artifact",
            "name": job.get("name") or "",
            "job_id": job.get("job_id") or "",
            "source_root": job.get("source_root") or "",
            "source_design": job.get("source_design") or "",
            "path": job.get("path") or "",
            "purpose": job.get("purpose") or "",
            "group": job.get("group") or "",
            "subgroup": job.get("subgroup") or "",
            "artifact_kind": job.get("artifact_kind") or "",
            "artifact_role": job.get("artifact_role") or "",
            "connections": job.get("connections") or [],
            "component_ids": [item["id"] for item in components],
        }
        artifact_rows.append(artifact_record)
        records.append(artifact_record)

    typed_manifest_rows: dict[str, list[dict[str, Any]]] = {}
    for key, prefix in (
        ("artifact_relations", "relation"),
        ("artifact_resources", "resource"),
        ("artifact_endpoints", "endpoint"),
        ("artifact_channels", "channel"),
    ):
        rows: list[dict[str, Any]] = []
        for item in manifest.get(key) or []:
            if not isinstance(item, dict):
                continue
            record = {"id": _evidence_id(prefix, item), "type": f"scanner_{prefix}", **item}
            rows.append(record)
            records.append(record)
        typed_manifest_rows[key] = rows

    lineage_rows: list[dict[str, Any]] = []
    for item in lineage.get("entries") or []:
        if not isinstance(item, dict):
            continue
        record = {"id": lineage_evidence_id(item), "type": "scanner_lineage", **item}
        lineage_ids.add(record["id"])
        lineage_rows.append(record)
        records.append(record)

    connection_rows_by_id: dict[str, dict[str, Any]] = {}
    for item in scan.get("metadata_connections") or []:
        if not isinstance(item, dict):
            continue
        safe_item = {
            "name": item.get("name") or "",
            "connection_type": item.get("connection_type") or "",
            "host": item.get("host") or "",
            "port": item.get("port") or "",
            "database": item.get("database") or "",
            "schema_name": item.get("schema_name") or "",
            "file_path": item.get("file_path") or "",
            "host_redaction": item.get("host_redaction"),
        }
        record = {"id": _evidence_id("connection", safe_item), "type": "scanner_connection", **safe_item}
        existing = connection_rows_by_id.get(record["id"])
        if existing is not None:
            if existing != record:
                raise ValueError(
                    f"conflicting scanner connection evidence ID: {record['id']}"
                )
            continue
        connection_rows_by_id[record["id"]] = record
        records.append(record)
    connection_rows = list(connection_rows_by_id.values())

    document_records = _document_records(staging_dir)
    records.extend(document_records)
    phase1_claim_records = _phase1_claim_records(staging_dir)
    records.extend(phase1_claim_records)
    phase2_claim_records = _phase2_claim_records(staging_dir)
    records.extend(phase2_claim_records)

    _jsonl(evidence_root / "artifacts.jsonl", artifact_rows)
    _jsonl(evidence_root / "components.jsonl", (row for row in records if row.get("type") == "scanner_component"))
    _jsonl(evidence_root / "relations.jsonl", typed_manifest_rows["artifact_relations"])
    _jsonl(evidence_root / "resources.jsonl", typed_manifest_rows["artifact_resources"])
    _jsonl(evidence_root / "endpoints.jsonl", typed_manifest_rows["artifact_endpoints"])
    _jsonl(evidence_root / "channels.jsonl", typed_manifest_rows["artifact_channels"])
    _jsonl(evidence_root / "connections.jsonl", connection_rows)
    _jsonl(evidence_root / "lineage.jsonl", lineage_rows)
    _jsonl(evidence_root / "phase2_claims.jsonl", phase2_claim_records)
    _jsonl(evidence_root / "supporting_artifacts.jsonl", document_records)
    _jsonl(evidence_root / "supporting_claims.jsonl", phase1_claim_records)
    _jsonl(evidence_root / "systems.jsonl", landscape_system_rows)
    _jsonl(evidence_root / "applications.jsonl", landscape_application_rows)
    _jsonl(evidence_root / "edges.jsonl", landscape_edge_rows)

    evidence_types = {str(record["id"]): str(record["type"]) for record in records}
    evidence_ids = frozenset(evidence_types)
    evidence_ids_path = evidence_root / "evidence_ids.json"
    evidence_ids_path.write_text(
        json.dumps({"evidence": evidence_types}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    overview = {
        "schema_version": "business-topology-evidence-v1",
        "project_id": project_id,
        "scan_fingerprint": fingerprint,
        "platform": scan.get("platform") or "unknown",
        "counts": {
            "artifacts": len(artifact_ids),
            "components": len(component_ids),
            "relations": len(typed_manifest_rows["artifact_relations"]),
            "resources": len(typed_manifest_rows["artifact_resources"]),
            "endpoints": len(typed_manifest_rows["artifact_endpoints"]),
            "channels": len(typed_manifest_rows["artifact_channels"]),
            "connections": len(connection_rows),
            "scanner_systems": len(landscape_system_rows),
            "scanner_applications": len(landscape_application_rows),
            "scanner_edges": len(landscape_edge_rows),
            "lineage_entries": len(lineage_rows),
            "documents": 0,
            "phase1_analyses": sum(
                record["type"] == "phase1_analysis" for record in document_records
            ),
            "phase2_analyses": sum(
                record["type"] == "phase2_analysis" for record in document_records
            ),
            "phase2_claims": len(phase2_claim_records),
            "typed_phase1_claims": len(phase1_claim_records),
            "evidence": len(evidence_ids),
        },
        "source_roots": manifest.get("source_roots") or [],
        "artifact_inventory": [
            {
                "id": row["id"],
                "name": row["name"],
                "source_root": row["source_root"],
                "kind": row["artifact_kind"],
                "role": row["artifact_role"],
            }
            for row in artifact_rows
        ],
        "evidence_files": {
            "artifacts": "_topology/evidence/artifacts.jsonl",
            "components": "_topology/evidence/components.jsonl",
            "relations": "_topology/evidence/relations.jsonl",
            "resources": "_topology/evidence/resources.jsonl",
            "endpoints": "_topology/evidence/endpoints.jsonl",
            "channels": "_topology/evidence/channels.jsonl",
            "connections": "_topology/evidence/connections.jsonl",
            "lineage": "_topology/evidence/lineage.jsonl",
            "phase2_claims": "_topology/evidence/phase2_claims.jsonl",
            "supporting_artifacts": "_topology/evidence/supporting_artifacts.jsonl",
            "supporting_claims": "_topology/evidence/supporting_claims.jsonl",
            "systems": "_topology/evidence/systems.jsonl",
            "applications": "_topology/evidence/applications.jsonl",
            "edges": "_topology/evidence/edges.jsonl",
            "evidence_ids": "_topology/evidence/evidence_ids.json",
        },
        "semantic_derivations": [],
    }
    overview_path = root / "factual_evidence_index.json"
    overview_path.write_text(json.dumps(overview, indent=2, ensure_ascii=False), encoding="utf-8")
    starter_pack_path = root / "_doc_pack_business_topology.md"
    starter_pack_path.write_text(_starter_pack(staging_dir, overview), encoding="utf-8")

    return BusinessTopologyEvidenceBundle(
        root=root,
        overview_path=overview_path,
        starter_pack_path=starter_pack_path,
        evidence_ids_path=evidence_ids_path,
        scan_fingerprint=fingerprint,
        evidence_ids=evidence_ids,
        evidence_types=evidence_types,
        evidence_records={str(record["id"]): record for record in records},
        artifact_ids=frozenset(artifact_ids),
        component_ids=frozenset(component_ids),
        lineage_ids=frozenset(lineage_ids),
    )


def load_business_topology_evidence(
    root: Path,
) -> BusinessTopologyEvidenceBundle:
    """Load a previously published evidence bundle without mutating it.

    Publication readers use this instead of trusting a stale validation flag.
    The ID ledger and every materialized record must agree before the bundle is
    eligible for a fresh validation pass.
    """

    if root.is_symlink() or not root.is_dir():
        raise ValueError("business topology evidence root is unavailable")
    evidence_root = root / "evidence"
    overview_path = root / "factual_evidence_index.json"
    starter_pack_path = root / "_doc_pack_business_topology.md"
    evidence_ids_path = evidence_root / "evidence_ids.json"
    try:
        overview = json.loads(overview_path.read_text(encoding="utf-8"))
        evidence_index = json.loads(evidence_ids_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("business topology evidence index is unreadable") from exc
    if not isinstance(overview, dict) or not isinstance(evidence_index, dict):
        raise ValueError("business topology evidence index must be an object")
    raw_types = evidence_index.get("evidence")
    if not isinstance(raw_types, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in raw_types.items()
    ):
        raise ValueError("business topology evidence ID ledger is invalid")
    evidence_types = dict(raw_types)

    evidence_records: dict[str, dict[str, Any]] = {}
    if evidence_root.is_symlink() or not evidence_root.is_dir():
        raise ValueError("business topology evidence directory is unavailable")
    for path in sorted(evidence_root.glob("*.jsonl"), key=lambda item: item.name):
        if path.is_symlink() or not path.is_file():
            raise ValueError("business topology evidence file is unsafe")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError("business topology evidence file is unreadable") from exc
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError("business topology evidence record is invalid") from exc
            if not isinstance(record, dict):
                raise ValueError("business topology evidence record must be an object")
            evidence_id = record.get("id")
            evidence_type = record.get("type")
            if not isinstance(evidence_id, str) or not isinstance(evidence_type, str):
                raise ValueError("business topology evidence record has no typed ID")
            if evidence_id in evidence_records:
                raise ValueError("business topology evidence IDs are not unique")
            if evidence_types.get(evidence_id) != evidence_type:
                raise ValueError("business topology evidence record disagrees with ID ledger")
            evidence_records[evidence_id] = record
    if set(evidence_records) != set(evidence_types):
        raise ValueError("business topology evidence records do not cover the ID ledger")

    fingerprint = overview.get("scan_fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ValueError("business topology evidence has no scan fingerprint")
    artifact_ids = frozenset(
        evidence_id
        for evidence_id, evidence_type in evidence_types.items()
        if evidence_type == "scanner_artifact"
    )
    component_ids = frozenset(
        evidence_id
        for evidence_id, evidence_type in evidence_types.items()
        if evidence_type == "scanner_component"
    )
    lineage_ids = frozenset(
        evidence_id
        for evidence_id, evidence_type in evidence_types.items()
        if evidence_type == "scanner_lineage"
    )
    return BusinessTopologyEvidenceBundle(
        root=root,
        overview_path=overview_path,
        starter_pack_path=starter_pack_path,
        evidence_ids_path=evidence_ids_path,
        scan_fingerprint=fingerprint,
        evidence_ids=frozenset(evidence_types),
        evidence_types=evidence_types,
        evidence_records=evidence_records,
        artifact_ids=artifact_ids,
        component_ids=component_ids,
        lineage_ids=lineage_ids,
    )
