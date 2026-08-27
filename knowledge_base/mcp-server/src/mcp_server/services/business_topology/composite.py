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
  merged system identities and de-duplicated by (source, destination, business
  object) so a cross-project edge detected from both sides (e.g. ETTM -> ESB
  over JMS) appears once WITHOUT collapsing distinct business flows between the
  same pair of systems -- de-duplicating on (source, destination) alone is what
  reduced the werkorder org view to 18 unlabelled edges where the hand-curated
  file has 72 named ones;
* the business layer travels with them: business objects, delivery paths (with
  their ordered segments re-pointed at org ids) and transformations. Dropping
  these at the merge boundary made every per-partition improvement invisible at
  the level users actually look at;
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
    BusinessObject,
    BusinessSystem,
    BusinessTopology,
    BusinessTopologyCoverage,
    BusinessTopologyMetadata,
    BusinessTransformation,
    BusinessUnknown,
    DeliveryPath,
    DeliverySegment,
    IntegrationSolution,
    TransformationMapping,
)

logger = logging.getLogger(__name__)

_PART_SUFFIX_RE = re.compile(r"_part\d+$", re.IGNORECASE)

#: Statuses a merged claim may carry, strongest first. The merge used to flatten
#: everything to ``inferred``, which made a scanner-edge-backed flow and a
#: documentation-asserted one look identical in the UI.
_STATUS_RANK = ("observed", "confirmed", "inferred", "proposed", "not_established")


def _stronger_status(left: str, right: str) -> str:
    def rank(value: str) -> int:
        try:
            return _STATUS_RANK.index(value)
        except ValueError:
            return len(_STATUS_RANK)

    return left if rank(left) <= rank(right) else right


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


def _merge_delivery_path(
    raw: dict[str, Any],
    *,
    org_stream: BusinessDataStream,
    local_to_org: dict[str, BusinessSystem],
) -> DeliveryPath | None:
    """Re-point one partition delivery path onto org-level identities.

    Segment ids, ``step:`` hand-off refs and endpoint refs all embed the LOCAL
    stream/system ids. The validator requires a path to start at its stream's
    source, end at its destination and stay contiguous, so every ref has to be
    rewritten together or the path is dropped -- a half-translated path is worse
    than none, because it renders as a broken chain.
    """

    segments_raw = [item for item in raw.get("segments") or [] if isinstance(item, dict)]
    if not segments_raw:
        return None
    segments_raw.sort(key=lambda item: int(item.get("sequence") or 0))
    path_id = f"path-{org_stream.id[3:]}" if org_stream.id.startswith("ds-") else (
        f"path-{org_stream.id}"
    )

    def org_ref(value: str, position: int, total: int) -> str | None:
        if position == 1:
            return org_stream.source_system_id
        if position > total:
            return org_stream.destination_system_id
        system = local_to_org.get(value)
        if system is not None:
            return system.id
        if value.startswith("step:"):
            return f"step:{org_stream.id}:{position - 1}"
        return None

    segments: list[DeliverySegment] = []
    total = len(segments_raw)
    for index, item in enumerate(segments_raw, start=1):
        source_ref = org_ref(str(item.get("source_ref") or ""), index, total)
        destination_ref = org_ref(str(item.get("destination_ref") or ""), index + 1, total)
        if source_ref is None or destination_ref is None:
            return None
        mode = str(item.get("mode") or "unknown")
        segments.append(
            DeliverySegment(
                id=f"segment:{org_stream.id}:{index}",
                name=str(item.get("name") or f"Delivery step {index}"),
                description=str(item.get("description") or ""),
                status=str(item.get("status") or org_stream.status),
                confidence=float(item.get("confidence") or org_stream.confidence),
                evidence_ids=[str(e) for e in item.get("evidence_ids") or []],
                sequence=index,
                source_ref=source_ref,
                destination_ref=destination_ref,
                mechanism=str(item.get("mechanism") or "unknown"),
                protocol=str(item.get("protocol") or "unknown"),
                resource=str(item.get("resource") or ""),
                component=str(item.get("component") or ""),
                artifact_ids=[str(a) for a in item.get("artifact_ids") or []],
                mode=mode if mode in {
                    "direct",
                    "buffered",
                    "batch",
                    "scheduled",
                    "event_driven",
                    "file_transfer",
                    "database",
                    "unknown",
                } else "unknown",
            )
        )
    return DeliveryPath(
        id=path_id,
        name=str(raw.get("name") or f"{org_stream.name} delivery"),
        description=str(raw.get("description") or ""),
        status=str(raw.get("status") or org_stream.status),
        confidence=float(raw.get("confidence") or org_stream.confidence),
        evidence_ids=[str(e) for e in raw.get("evidence_ids") or []],
        stream_id=org_stream.id,
        segments=segments,
    )


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
    streams: dict[tuple[str, str, str], BusinessDataStream] = {}
    objects: dict[str, BusinessObject] = {}
    paths: dict[str, DeliveryPath] = {}
    transformations: list[BusinessTransformation] = []
    solution_artifacts: dict[str, list[str]] = {}
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

        logical_name = _logical_subproject(partition_name)
        contributing_logical.add(logical_name)
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

        # Business objects are merged first: streams reference them by org id.
        local_object_to_org: dict[str, str] = {}
        for raw in topology.get("business_objects") or []:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            if not name:
                continue
            org_id = f"bo-{_slug(name)}"
            local_object_to_org[str(raw.get("id"))] = org_id
            existing_object = objects.get(org_id)
            aliases = [str(a) for a in raw.get("aliases") or [] if str(a)]
            if existing_object is None:
                objects[org_id] = BusinessObject(
                    id=org_id,
                    name=name,
                    description=str(raw.get("description") or ""),
                    status=str(raw.get("status") or "inferred"),
                    confidence=float(raw.get("confidence") or 0.6),
                    evidence_ids=[str(e) for e in raw.get("evidence_ids") or []],
                    aliases=aliases,
                )
            else:
                for alias in aliases:
                    if alias not in existing_object.aliases and alias != existing_object.name:
                        existing_object.aliases.append(alias)
                for evidence_id in raw.get("evidence_ids") or []:
                    if str(evidence_id) not in existing_object.evidence_ids:
                        existing_object.evidence_ids.append(str(evidence_id))

        local_paths = {
            str(raw.get("id")): raw
            for raw in topology.get("delivery_paths") or []
            if isinstance(raw, dict)
        }
        local_stream_to_org: dict[str, str] = {}

        for raw in topology.get("data_streams") or []:
            if not isinstance(raw, dict):
                continue
            source = local_to_org.get(str(raw.get("source_system_id")))
            dest = local_to_org.get(str(raw.get("destination_system_id")))
            if source is None or dest is None or source.id == dest.id:
                continue
            org_object_ids = [
                local_object_to_org[str(ref)]
                for ref in raw.get("business_object_ids") or []
                if str(ref) in local_object_to_org
            ]
            # The business object is part of the identity: Maximo -> 4PS
            # WorkOrder and Maximo -> 4PS Invoice are two flows, not one edge.
            object_key = org_object_ids[0] if org_object_ids else ""
            key = (source.id, dest.id, object_key)
            evidence_ids = [str(e) for e in raw.get("evidence_ids") or []]
            artifact_ids = [str(a) for a in raw.get("artifact_ids") or []]
            protocols = [str(p) for p in raw.get("protocols") or [] if str(p)]
            status = str(raw.get("status") or "inferred")
            existing = streams.get(key)
            if existing is None:
                suffix = f"-{object_key[3:]}" if object_key else ""
                merged = BusinessDataStream(
                    id=f"ds-{_slug(source.name)}--{_slug(dest.name)}{suffix}",
                    name=str(raw.get("name") or "")
                    or f"{source.name} -> {dest.name}",
                    description=str(raw.get("description") or "")
                    or f"Integration flow from {source.name} to {dest.name}.",
                    status=status,
                    confidence=float(raw.get("confidence") or 0.4),
                    evidence_ids=list(dict.fromkeys(evidence_ids)),
                    source_system_id=source.id,
                    destination_system_id=dest.id,
                    business_object_ids=list(dict.fromkeys(org_object_ids)),
                    business_summary=str(raw.get("business_summary") or "")
                    or f"Scanner-backed integration between {source.name} and {dest.name}.",
                    integration_solution_ids=[f"int-sol-{_slug(logical_name)}"],
                    protocols=list(dict.fromkeys(protocols)),
                    requiredness=str(raw.get("requiredness") or "not_established"),
                    artifact_ids=list(dict.fromkeys(artifact_ids)),
                )
                streams[key] = merged
                existing = merged
            else:
                existing.status = _stronger_status(existing.status, status)
                existing.confidence = max(
                    existing.confidence, float(raw.get("confidence") or 0.0)
                )
                for evidence_id in evidence_ids:
                    if evidence_id not in existing.evidence_ids:
                        existing.evidence_ids.append(evidence_id)
                for artifact_id in artifact_ids:
                    if artifact_id not in existing.artifact_ids:
                        existing.artifact_ids.append(artifact_id)
                for protocol in protocols:
                    if protocol not in existing.protocols:
                        existing.protocols.append(protocol)
                for object_id in org_object_ids:
                    if object_id not in existing.business_object_ids:
                        existing.business_object_ids.append(object_id)
                solution_ref = f"int-sol-{_slug(logical_name)}"
                if solution_ref not in existing.integration_solution_ids:
                    existing.integration_solution_ids.append(solution_ref)
            local_stream_to_org[str(raw.get("id"))] = existing.id
            solution_artifacts.setdefault(logical_name, []).extend(artifact_ids)

            local_path = local_paths.get(str(raw.get("delivery_path_id") or ""))
            if local_path is not None:
                merged_path = _merge_delivery_path(
                    local_path,
                    org_stream=existing,
                    local_to_org=local_to_org,
                )
                if merged_path is not None:
                    incumbent = paths.get(merged_path.id)
                    # A stream owns exactly one path (the validator anchors it to
                    # the stream's endpoints); keep the most detailed contributor.
                    if incumbent is None or len(merged_path.segments) > len(
                        incumbent.segments
                    ):
                        paths[merged_path.id] = merged_path
                    existing.delivery_path_id = merged_path.id

        for raw in topology.get("transformations") or []:
            if not isinstance(raw, dict):
                continue
            org_stream_ids = [
                local_stream_to_org[str(ref)]
                for ref in raw.get("stream_ids") or []
                if str(ref) in local_stream_to_org
            ]
            if not org_stream_ids:
                continue
            transformations.append(
                BusinessTransformation(
                    id=f"transform-{_slug(logical_name)}-{len(transformations) + 1}",
                    name=str(raw.get("name") or "Transformation"),
                    description=str(raw.get("description") or ""),
                    status=str(raw.get("status") or "inferred"),
                    confidence=float(raw.get("confidence") or 0.6),
                    evidence_ids=[str(e) for e in raw.get("evidence_ids") or []],
                    stream_ids=list(dict.fromkeys(org_stream_ids)),
                    integration_solution_ids=[f"int-sol-{_slug(logical_name)}"],
                    artifact_ids=[str(a) for a in raw.get("artifact_ids") or []],
                    mappings=[
                        TransformationMapping(
                            source=str(mapping.get("source") or ""),
                            target=str(mapping.get("target") or ""),
                            mapping_type=str(mapping.get("mapping_type") or "unknown"),
                        )
                        for mapping in raw.get("mappings") or []
                        if isinstance(mapping, dict)
                        and (mapping.get("source") or mapping.get("target"))
                    ],
                )
            )

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
        # Keyed with an empty business object: a cross-project index edge carries
        # no business object, and must not silently absorb (or be absorbed by) a
        # named business flow between the same pair.
        key = (source.id, dest.id, "")
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
                artifact_ids=list(
                    dict.fromkeys(solution_artifacts.get(logical) or [])
                ),
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
        business_objects=list(objects.values()),
        data_streams=list(streams.values()),
        delivery_paths=list(paths.values()),
        transformations=transformations,
        unknowns=unknowns,
        coverage=BusinessTopologyCoverage(
            total_artifacts=total_artifacts,
            cited_artifacts=cited_artifacts,
            total_evidence=total_evidence,
            cited_evidence=cited_evidence,
            coverage_ratio=coverage_ratio,
            technical_relationships=len(streams),
            grounded_technical_relationships=sum(
                1 for stream in streams.values() if stream.status == "observed"
            ),
            notes=notes,
        ),
    )
