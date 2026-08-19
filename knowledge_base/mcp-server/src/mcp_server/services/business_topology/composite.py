"""Deterministic org-level business topology for composite uploads.

A composite upload (one ZIP per sub-project) generates documentation one
partition at a time. Each partition publishes its own, individually validated
``business_topology.json`` under ``output/{pid}/{subproject}/docs/_topology/``.
There is no ``output/{pid}/docs/`` tree, so the single-run landscape endpoint
finds no upload-level topology and serves ``business_topology: null`` -- which
the strict frontend contract rejects (``expected object, received null``).

This module reconstructs ONE org-level topology by merging the per-partition
topologies with the deterministic cross-project index
(``output/{pid}/topology/cross_project_index.json``):

* systems are de-duplicated by name across partitions (the shared external
  systems -- Maximo, LogDB, RHM_DB, JMS brokers -- collapse into single
  org-level nodes; each sub-project's own application node is preserved);
* data streams are the union of every partition's streams, re-pointed at the
  merged system identities and de-duplicated by (source, destination) so a
  cross-project edge detected from both sides (e.g. ETTM -> ESB over JMS)
  appears once;
* one integration solution per logical sub-project (the applications);
* an ``unknowns`` entry per logical sub-project whose own topology failed to
  publish, so the gap is captured rather than silently dropped;
* coverage is the honest sum of the contributing partitions' coverage.

Everything is derived from already-validated scanner facts -- no business
meaning is invented. The result validates against ``BusinessTopology`` (and,
by construction of identical field shapes, the frontend Zod contract).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from mcp_server.services.business_topology.models import (
    BusinessDataStream,
    BusinessSystem,
    BusinessTopology,
    BusinessTopologyCoverage,
    BusinessTopologyMetadata,
    BusinessUnknown,
    IntegrationSolution,
)

logger = logging.getLogger(__name__)

_PART_SUFFIX_RE = re.compile(r"_part\d+$", re.IGNORECASE)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48] or "x"


def _logical_subproject(partition_name: str) -> str:
    """Fold a size-split partition (``ETTM_part2``) back to its zip (``ETTM``)."""

    return _PART_SUFFIX_RE.sub("", partition_name)


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


class _SystemRegistry:
    """De-duplicates systems by name and hands out stable org-level ids."""

    def __init__(self) -> None:
        self._by_name: dict[str, BusinessSystem] = {}

    def resolve(
        self,
        *,
        name: str,
        kind: str = "software_system",
        description: str = "",
        evidence_ids: list[str] | None = None,
        aliases: list[str] | None = None,
    ) -> BusinessSystem:
        key = name.strip().casefold()
        existing = self._by_name.get(key)
        if existing is not None:
            for evidence_id in evidence_ids or []:
                if evidence_id not in existing.evidence_ids:
                    existing.evidence_ids.append(evidence_id)
            for alias in aliases or []:
                if alias and alias not in existing.aliases and alias != existing.name:
                    existing.aliases.append(alias)
            # A concrete data_system classification wins over a generic default.
            if existing.kind == "software_system" and kind == "data_system":
                existing.kind = "data_system"
            return existing
        system = BusinessSystem(
            id=f"sys-{_slug(name)}",
            name=name.strip(),
            description=description
            or "System participating in the organization integration landscape.",
            status="inferred",
            confidence=0.4,
            evidence_ids=list(dict.fromkeys(evidence_ids or [])),
            kind=kind if kind in {
                "software_system",
                "data_system",
                "external_service",
                "organization",
                "person_role",
                "unresolved",
            } else "software_system",
            aliases=list(dict.fromkeys(a for a in (aliases or []) if a)),
        )
        self._by_name[key] = system
        return system

    def values(self) -> list[BusinessSystem]:
        return list(self._by_name.values())


def _partition_topology_path(
    project_root: Path, project_id: str, partition_name: str
) -> Path:
    # partition_state keys are the on-disk sub-directory names for composite
    # runs (``ETTM_part2`` ...); the folder equals the key here.
    return (
        project_root
        / "output"
        / project_id
        / partition_name
        / "docs"
        / "_topology"
        / "business_topology.json"
    )


def build_composite_org_topology(
    project_id: str,
    *,
    project_root: Path,
) -> BusinessTopology | None:
    """Merge per-partition topologies + cross-project index into one org view.

    Returns ``None`` when the project is not a composite upload (no
    ``topology/partition_state.json``) or no partition published a topology.
    """

    topo_dir = project_root / "output" / project_id / "topology"
    partition_state = _load_json(topo_dir / "partition_state.json")
    if not partition_state:
        return None

    index = _load_json(topo_dir / "cross_project_index.json") or {}

    fingerprints = {
        str(entry.get("scan_fingerprint"))
        for entry in partition_state.values()
        if isinstance(entry, dict) and entry.get("scan_fingerprint")
    }
    scan_fingerprint = sorted(fingerprints)[0] if fingerprints else ""

    registry = _SystemRegistry()
    streams: dict[tuple[str, str], BusinessDataStream] = {}
    total_artifacts = cited_artifacts = total_evidence = cited_evidence = 0
    contributing_logical: set[str] = set()

    for partition_name in sorted(partition_state):
        topology = _load_json(
            _partition_topology_path(project_root, project_id, partition_name)
        )
        if not topology:
            continue
        report = _load_json(
            _partition_topology_path(project_root, project_id, partition_name).parent
            / "validation_report.json"
        ) or {}
        if report.get("validated") is False:
            # A partition that did not itself validate is skipped: its systems
            # and streams are not trustworthy org-level facts.
            continue

        contributing_logical.add(_logical_subproject(partition_name))
        coverage = topology.get("coverage") or {}
        total_artifacts += int(coverage.get("total_artifacts") or 0)
        cited_artifacts += int(coverage.get("cited_artifacts") or 0)
        total_evidence += int(coverage.get("total_evidence") or 0)
        cited_evidence += int(coverage.get("cited_evidence") or 0)

        # Map the partition's local system ids to merged org-level systems.
        local_to_org: dict[str, BusinessSystem] = {}
        for raw in topology.get("systems") or []:
            if not isinstance(raw, dict):
                continue
            system = registry.resolve(
                name=str(raw.get("name") or raw.get("id") or "unknown"),
                kind=str(raw.get("kind") or "software_system"),
                description=str(raw.get("description") or ""),
                evidence_ids=[str(e) for e in raw.get("evidence_ids") or []],
                aliases=[str(a) for a in raw.get("aliases") or []],
            )
            local_to_org[str(raw.get("id"))] = system

        for raw in topology.get("data_streams") or []:
            if not isinstance(raw, dict):
                continue
            source = local_to_org.get(str(raw.get("source_system_id")))
            dest = local_to_org.get(str(raw.get("destination_system_id")))
            if source is None or dest is None or source.id == dest.id:
                continue
            key = (source.id, dest.id)
            evidence_ids = [str(e) for e in raw.get("evidence_ids") or []]
            artifact_ids = [str(a) for a in raw.get("artifact_ids") or []]
            existing = streams.get(key)
            if existing is None:
                streams[key] = BusinessDataStream(
                    id=f"ds-{_slug(source.name)}--{_slug(dest.name)}",
                    name=f"{source.name} -> {dest.name}",
                    description=str(raw.get("description") or "")
                    or f"Integration flow from {source.name} to {dest.name}.",
                    status="inferred",
                    confidence=float(raw.get("confidence") or 0.4),
                    evidence_ids=list(dict.fromkeys(evidence_ids)),
                    source_system_id=source.id,
                    destination_system_id=dest.id,
                    business_summary=str(raw.get("business_summary") or "")
                    or f"Scanner-backed integration between {source.name} and {dest.name}.",
                    artifact_ids=list(dict.fromkeys(artifact_ids)),
                )
            else:
                for evidence_id in evidence_ids:
                    if evidence_id not in existing.evidence_ids:
                        existing.evidence_ids.append(evidence_id)
                for artifact_id in artifact_ids:
                    if artifact_id not in existing.artifact_ids:
                        existing.artifact_ids.append(artifact_id)

    if not registry.values() and not streams:
        return None

    # --- Overlay: cross-project application edges (guarantee they exist) ------
    for edge in index.get("application_edges") or []:
        if not isinstance(edge, dict):
            continue
        source_name = str(edge.get("source_application") or edge.get("from_subproject") or "")
        dest_name = str(edge.get("target_application") or edge.get("to_subproject") or "")
        if not source_name or not dest_name:
            continue
        mechanism = str(edge.get("mechanism") or "unknown")
        edge_evidence = f"cross-project-application-edge:{source_name}->{dest_name}"
        source = registry.resolve(name=source_name)
        dest = registry.resolve(name=dest_name)
        if source.id == dest.id:
            continue
        key = (source.id, dest.id)
        existing = streams.get(key)
        if existing is None:
            streams[key] = BusinessDataStream(
                id=f"ds-{_slug(source.name)}--{_slug(dest.name)}",
                name=f"{source.name} -> {dest.name}",
                description=(
                    f"Cross-project integration flow via {mechanism} from "
                    f"{source.name} to {dest.name}."
                ),
                status="inferred",
                confidence=0.4,
                evidence_ids=[edge_evidence],
                source_system_id=source.id,
                destination_system_id=dest.id,
                business_summary=(
                    f"Cross-project index application edge ({mechanism})."
                ),
                artifact_ids=[],
            )
        elif edge_evidence not in existing.evidence_ids:
            existing.evidence_ids.append(edge_evidence)

    # --- Integration solutions: one per logical sub-project ------------------
    subprojects = index.get("subprojects") or {}
    logical_names = sorted(
        {str(name) for name in subprojects}
        | {_logical_subproject(name) for name in partition_state}
    )
    solutions: list[IntegrationSolution] = []
    unknowns: list[BusinessUnknown] = []
    for logical in logical_names:
        info = subprojects.get(logical) if isinstance(subprojects, dict) else None
        unit_count = int((info or {}).get("unit_count") or 0)
        solutions.append(
            IntegrationSolution(
                id=f"int-sol-{_slug(logical)}",
                name=logical,
                description=(
                    f"Integration sub-project '{logical}' "
                    f"({unit_count} scanned units)."
                    if unit_count
                    else f"Integration sub-project '{logical}'."
                ),
                status="inferred",
                confidence=0.5,
                evidence_ids=[
                    f"cross-project-index:{project_id}",
                    f"cross-project-subproject:{logical}",
                ],
                platform=str(index.get("platform") or ""),
                artifact_ids=[],
            )
        )
        if logical not in contributing_logical:
            unknowns.append(
                BusinessUnknown(
                    id=f"unk-subproject-{_slug(logical)}",
                    name=f"{logical}: business topology unavailable",
                    description=(
                        f"Sub-project '{logical}' did not publish a validated "
                        "business topology, so its systems and streams are absent "
                        "from the organization view."
                    ),
                    status="not_established",
                    confidence=0.2,
                    evidence_ids=[f"cross-project-subproject:{logical}"],
                    question=(
                        f"Regenerate documentation for sub-project '{logical}' to "
                        "surface its integration topology."
                    ),
                )
            )

    coverage_ratio = (
        round(cited_evidence / total_evidence, 6) if total_evidence else 0.0
    )
    coverage_ratio = min(max(coverage_ratio, 0.0), 1.0)
    notes = [
        "Organization-level topology deterministically merged from "
        f"{len(contributing_logical)} sub-project topologies and the "
        "cross-project index (no language-model synthesis).",
    ]
    if unknowns:
        missing = sorted(
            logical for logical in logical_names if logical not in contributing_logical
        )
        notes.append(
            "Sub-projects without a published topology: " + ", ".join(missing) + "."
        )

    return BusinessTopology(
        scan_fingerprint=scan_fingerprint,
        metadata=BusinessTopologyMetadata(
            project_id=project_id,
            scan_fingerprint=scan_fingerprint,
            generated_by="composite_org_merge",
            model_name="composite_org_merge",
        ),
        systems=registry.values(),
        integration_solutions=solutions,
        data_streams=list(streams.values()),
        unknowns=unknowns,
        coverage=BusinessTopologyCoverage(
            total_artifacts=total_artifacts,
            cited_artifacts=cited_artifacts,
            total_evidence=total_evidence,
            cited_evidence=cited_evidence,
            coverage_ratio=coverage_ratio,
            technical_relationships=len(streams),
            grounded_technical_relationships=len(streams),
            notes=notes,
        ),
    )
