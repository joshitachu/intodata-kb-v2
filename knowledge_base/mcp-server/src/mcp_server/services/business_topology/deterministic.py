"""Deterministic, evidence-only business-topology fallback.

When the language-model synthesis fails (invalid JSON, evidence-less output, or
a bounded retry that still does not validate), the pipeline must still be able
to publish a *real*, evidence-cited overlay rather than dropping topology
entirely. This module reconstructs an L1 topology straight from the scanner
evidence bundle:

* one system per landscape system/application identity that participates in a
  directional edge, cited by its own identity evidence ID;
* one data stream per ``scanner_edge`` whose source and destination identities
  are known and whose named artifacts resolve into this partition's artifact
  set, cited by the edge plus both identity IDs;
* one integration solution per LOCAL landscape application (its source roots
  overlap this partition's artifacts), citing its identity plus every local
  artifact it owns -- this is the partition's own codebase, fully grounded;
* one system per additional named landscape system/application identity row
  (connection-derived databases and services), deduplicated by name, so the
  L1 canvas shows the systems the partition touches even when no directional
  edge reaches them;
* one ``unknowns`` entry per directional edge whose named artifacts all live
  in a SIBLING sub-project of a composite upload (verified on werkorder
  ESB_part1: 19 of 25 edges) -- these cannot be valid streams here because
  validation requires local artifact grounding, but the edge and both
  identities are cited so the cross-partition flow is captured, not dropped;
* JMS/queue/topic channel evidence rows are cited on the streams whose
  implementing artifacts they belong to (bounded per stream).

The result passes ``validate_business_topology`` by construction. It carries
no invented business meaning: every field is derived from scanner facts.

Residual gap (2026-08-10): the LLM synthesis leg could not be re-verified live
(the run's DeepSeek account returned 402 Insufficient Balance), so this
fallback remains the guaranteed floor. Unresolvable cross-partition edges stay
``unknowns`` rather than streams; only the LLM (or a future cross-project
merge step) can narrate them as business flows once artifacts resolve.
"""

from __future__ import annotations

import re

from mcp_server.services.business_topology.evidence import (
    BusinessTopologyEvidenceBundle,
)
from mcp_server.services.business_topology.models import (
    BusinessDataStream,
    BusinessSystem,
    BusinessTopology,
    BusinessTopologyCoverage,
    BusinessTopologyMetadata,
    BusinessTransformation,
    BusinessUnknown,
    IntegrationSolution,
    TransformationMapping,
)

_IDENTITY_TYPES = frozenset(
    {"scanner_system", "scanner_application", "scanner_connection"}
)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:40] or "x"


def _artifact_identity_map(
    bundle: BusinessTopologyEvidenceBundle,
) -> dict[str, set[str]]:
    """Map casefolded artifact identity tokens to artifact evidence IDs.

    Mirrors the resolution the validator uses so a stream built here always
    passes the directional-artifact check.
    """

    identities: dict[str, set[str]] = {}
    for artifact_id in bundle.artifact_ids:
        record = bundle.evidence_records.get(artifact_id) or {}
        for key in ("name", "job_id", "path"):
            token = str(record.get(key) or "").strip().casefold()
            if token:
                identities.setdefault(token, set()).add(artifact_id)
        source_root = str(record.get("source_root") or "").strip()
        for key in ("name", "job_id"):
            token = str(record.get(key) or "").strip()
            if source_root and token:
                identities.setdefault(
                    f"{source_root}::{token}".casefold(), set()
                ).add(artifact_id)
    return identities


def _system_kind(evidence_type: str | None, record: dict) -> str:
    if evidence_type == "scanner_application":
        return "software_system"
    if evidence_type == "scanner_connection":
        return "external_service"
    return "data_system" if str(record.get("kind") or "") == "database" else "software_system"


def _canonical_protocol(value: str) -> str | None:
    """Normalize an observed mechanism without guessing a technology."""

    token = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    if not token or token in {"unknown", "data", "dependency"}:
        return None
    for protocol, needles in (
        ("SOAP", ("soap",)),
        ("REST", ("rest", "http api", "webhook")),
        ("JMS", ("jms", "activemq", "message queue", "queue", "topic")),
        ("SFTP", ("sftp",)),
        ("File", ("file", "filesystem", "file transfer")),
        ("JDBC", ("jdbc",)),
        ("Database", ("database", "sql", "db")),
    ):
        if any(needle in token for needle in needles):
            return protocol
    return None


def _mapping_type(record: dict) -> str:
    types = {
        str(item.get("transformation_type") or "").casefold()
        for item in record.get("transformations") or []
        if isinstance(item, dict)
    }
    if types & {"direct", "direct_map", "identity"}:
        return "direct"
    if types & {"constant", "literal"}:
        return "constant"
    if types & {"value", "value_map", "lookup"}:
        return "value"
    if types & {"expression", "derived", "calculation"}:
        return "derived"
    return "unknown"


def build_deterministic_topology(
    bundle: BusinessTopologyEvidenceBundle,
    *,
    project_id: str,
    model_name: str = "deterministic_topology_fallback",
    max_stream_artifacts: int = 100,
    max_channel_citations_per_stream: int = 10,
) -> BusinessTopology:
    """Build an evidence-only topology from scanner edges and identities."""

    identity_map = _artifact_identity_map(bundle)
    systems: dict[str, BusinessSystem] = {}  # identity_evidence_id -> system

    def _system_for(identity_id: str) -> BusinessSystem | None:
        if bundle.evidence_types.get(identity_id) not in _IDENTITY_TYPES:
            return None
        existing = systems.get(identity_id)
        if existing is not None:
            return existing
        record = bundle.evidence_records.get(identity_id) or {}
        name = str(record.get("name") or record.get("scanner_id") or identity_id)
        system = BusinessSystem(
            id=f"sys-{_slug(name)}-{identity_id.split(':')[-1][:6]}",
            name=name,
            description=(
                "Scanner-identified system participating in at least one "
                "directional integration edge."
            ),
            status="inferred",
            confidence=0.4,
            evidence_ids=[identity_id],
            kind=_system_kind(bundle.evidence_types.get(identity_id), record),
        )
        systems[identity_id] = system
        return system

    streams: list[BusinessDataStream] = []
    used_stream_ids: set[str] = set()
    unresolved_edges: list[tuple[str, dict, str, str]] = []
    for edge_id, edge_type in sorted(bundle.evidence_types.items()):
        if edge_type != "scanner_edge":
            continue
        record = bundle.evidence_records.get(edge_id) or {}
        source_id = record.get("source_identity_evidence_id")
        dest_id = record.get("destination_identity_evidence_id")
        if source_id not in bundle.evidence_ids or dest_id not in bundle.evidence_ids:
            continue
        artifacts: set[str] = set()
        for name in record.get("artifact_names") or []:
            artifacts |= identity_map.get(str(name).strip().casefold(), set())
        artifacts &= bundle.artifact_ids
        if not artifacts:
            # Cross-partition landscape edge: both identities are known, but
            # every implementing artifact lives in a sibling sub-project, so a
            # stream here could never pass local artifact grounding. Capture it
            # as a cited unknown instead of silently dropping the edge.
            unresolved_edges.append((edge_id, record, str(source_id), str(dest_id)))
            continue
        source_system = _system_for(str(source_id))
        dest_system = _system_for(str(dest_id))
        if source_system is None or dest_system is None:
            continue
        stream_id = f"ds-{_slug(edge_id.split(':')[-1])}"
        if stream_id in used_stream_ids:
            stream_id = f"{stream_id}-{len(used_stream_ids)}"
        used_stream_ids.add(stream_id)
        mechanism = str(record.get("mechanism") or "unknown")
        dependency = str(record.get("dependency_type") or "data")
        protocol = _canonical_protocol(mechanism)
        streams.append(
            BusinessDataStream(
                id=stream_id,
                name=f"{source_system.name} -> {dest_system.name}",
                description=(
                    f"Scanner-detected {dependency} flow via {mechanism} between "
                    f"{source_system.name} and {dest_system.name}."
                ),
                status="inferred",
                confidence=0.4,
                evidence_ids=[edge_id, str(source_id), str(dest_id)],
                source_system_id=source_system.id,
                destination_system_id=dest_system.id,
                business_summary=(
                    f"Deterministic scanner-backed {dependency} integration edge "
                    f"({mechanism})."
                ),
                protocols=[protocol] if protocol else [],
                artifact_ids=sorted(artifacts)[:max_stream_artifacts],
            )
        )


    # --- Enrichment: JMS/queue/topic channel citations on streams -----------
    artifact_channels: dict[str, list[str]] = {}
    for channel_id, channel_type in sorted(bundle.evidence_types.items()):
        if channel_type != "scanner_channel":
            continue
        record = bundle.evidence_records.get(channel_id) or {}
        if str(record.get("channel_type") or "").casefold() not in {
            "jms",
            "queue",
            "topic",
        }:
            continue
        for artifact_id in identity_map.get(
            str(record.get("artifact") or "").strip().casefold(), set()
        ):
            artifact_channels.setdefault(artifact_id, []).append(channel_id)
    for stream in streams:
        channel_ids = sorted(
            {
                channel_id
                for artifact_id in stream.artifact_ids
                for channel_id in artifact_channels.get(artifact_id, [])
            }
        )[:max_channel_citations_per_stream]
        stream.evidence_ids.extend(
            channel_id
            for channel_id in channel_ids
            if channel_id not in stream.evidence_ids
        )
        if channel_ids and "JMS" not in stream.protocols:
            stream.protocols.append("JMS")

        endpoint_ids: list[str] = []
        for evidence_id, evidence_type in sorted(bundle.evidence_types.items()):
            if evidence_type != "scanner_endpoint":
                continue
            record = bundle.evidence_records.get(evidence_id) or {}
            artifact_ids = identity_map.get(
                str(record.get("artifact") or "").strip().casefold(), set()
            )
            if not artifact_ids.intersection(stream.artifact_ids):
                continue
            protocol = _canonical_protocol(str(record.get("endpoint_type") or ""))
            if protocol and protocol not in stream.protocols:
                stream.protocols.append(protocol)
            endpoint_ids.append(evidence_id)
        stream.evidence_ids.extend(
            evidence_id
            for evidence_id in endpoint_ids[:max_channel_citations_per_stream]
            if evidence_id not in stream.evidence_ids
        )

    # Exact scanner lineage is safe to preserve as structured mappings. The
    # business meaning still comes from synthesis, but field-to-field facts no
    # longer have to be re-interpreted from prose by the frontend.
    transformations: list[BusinessTransformation] = []
    for stream in streams:
        lineage_rows: list[tuple[str, dict, str]] = []
        stream_artifacts = set(stream.artifact_ids)
        for lineage_id, evidence_type in sorted(bundle.evidence_types.items()):
            if evidence_type != "scanner_lineage":
                continue
            record = bundle.evidence_records.get(lineage_id) or {}
            artifact_ids = identity_map.get(
                str(record.get("job_name") or "").strip().casefold(), set()
            ) & stream_artifacts
            if not artifact_ids:
                continue
            source = str(record.get("source_column") or "").strip()
            target = str(record.get("target_column") or "").strip()
            if not source or not target:
                continue
            lineage_rows.append((lineage_id, record, sorted(artifact_ids)[0]))
        if not lineage_rows:
            continue
        bounded = lineage_rows[:100]
        transformations.append(
            BusinessTransformation(
                id=f"transform-{stream.id.removeprefix('ds-')}",
                name=f"{stream.name} field mappings",
                description="Field mappings reconstructed from scanner lineage evidence.",
                status="observed",
                confidence=0.7,
                evidence_ids=[lineage_id for lineage_id, _, _ in bounded],
                stream_ids=[stream.id],
                artifact_ids=sorted({artifact_id for _, _, artifact_id in bounded}),
                mappings=[
                    TransformationMapping(
                        source=str(record.get("source_column") or ""),
                        target=str(record.get("target_column") or ""),
                        mapping_type=_mapping_type(record),
                    )
                    for _, record, _ in bounded
                ],
            )
        )

    # --- Enrichment: one integration solution per LOCAL application ---------
    artifact_roots: dict[str, set[str]] = {}
    for artifact_id in bundle.artifact_ids:
        record = bundle.evidence_records.get(artifact_id) or {}
        root = str(record.get("source_root") or "").strip()
        if root:
            artifact_roots.setdefault(root, set()).add(artifact_id)
    solutions: list[IntegrationSolution] = []
    for identity_id, evidence_type in sorted(bundle.evidence_types.items()):
        if evidence_type != "scanner_application":
            continue
        record = bundle.evidence_records.get(identity_id) or {}
        roots = {str(root).strip() for root in record.get("source_roots") or []}
        roots.add(str(record.get("source_root") or "").strip())
        owned = sorted(
            {
                artifact_id
                for root in roots
                for artifact_id in artifact_roots.get(root, set())
            }
        )
        if not owned:
            continue  # sibling sub-project application: no local artifacts
        name = str(record.get("name") or record.get("scanner_id") or identity_id)
        solutions.append(
            IntegrationSolution(
                id=f"int-sol-{_slug(name)}-{identity_id.split(':')[-1][:6]}",
                name=name,
                description=(
                    f"Scanned integration codebase '{name}': "
                    f"{len(owned)} artifacts in this partition."
                ),
                status="inferred",
                confidence=0.5,
                evidence_ids=[identity_id, *owned],
                artifact_ids=owned,
            )
        )
    owned_by_solution = {
        solution.id: set(solution.artifact_ids) for solution in solutions
    }
    for stream in streams:
        stream.integration_solution_ids = [
            solution_id
            for solution_id, owned_ids in sorted(owned_by_solution.items())
            if owned_ids.intersection(stream.artifact_ids)
        ]

    # --- Enrichment: named identity systems beyond edge participants --------
    seen_names = {system.name.casefold() for system in systems.values()}
    for identity_id, evidence_type in sorted(bundle.evidence_types.items()):
        if evidence_type not in {"scanner_system", "scanner_application"}:
            continue
        if identity_id in systems:
            continue
        record = bundle.evidence_records.get(identity_id) or {}
        name = str(record.get("name") or "").strip()
        if not name or not str(record.get("scanner_id") or "").strip():
            continue
        if name.casefold() in seen_names:
            continue
        seen_names.add(name.casefold())
        systems[identity_id] = BusinessSystem(
            id=f"sys-{_slug(name)}-{identity_id.split(':')[-1][:6]}",
            name=name,
            description=(
                "Scanner-identified system in this partition's landscape "
                "(no directional edge resolved to local artifacts)."
            ),
            status="inferred",
            confidence=0.3,
            evidence_ids=[identity_id],
            kind=_system_kind(evidence_type, record),
        )

    # --- Enrichment: cited unknowns for cross-partition edges ---------------
    unknowns: list[BusinessUnknown] = []
    for edge_id, record, source_id, dest_id in unresolved_edges:
        source_name = str(
            (bundle.evidence_records.get(source_id) or {}).get("name") or source_id
        )
        dest_name = str(
            (bundle.evidence_records.get(dest_id) or {}).get("name") or dest_id
        )
        mechanism = str(record.get("mechanism") or "unknown")
        unknowns.append(
            BusinessUnknown(
                id=f"unk-edge-{_slug(edge_id.split(':')[-1])}",
                name=f"{source_name} -> {dest_name} ({mechanism})",
                description=(
                    "Directional landscape edge whose implementing artifacts are "
                    "not in this partition's scan scope (composite-upload sibling "
                    "sub-project)."
                ),
                status="not_established",
                confidence=0.2,
                evidence_ids=[edge_id, source_id, dest_id],
                question=(
                    f"Which sibling sub-project artifacts implement the {mechanism} "
                    f"flow from {source_name} to {dest_name}?"
                ),
            )
        )

    return BusinessTopology(
        scan_fingerprint=bundle.scan_fingerprint,
        metadata=BusinessTopologyMetadata(
            project_id=project_id,
            scan_fingerprint=bundle.scan_fingerprint,
            generated_by="deterministic_topology_fallback",
            model_name=model_name,
        ),
        systems=list(systems.values()),
        integration_solutions=solutions,
        data_streams=streams,
        transformations=transformations,
        unknowns=unknowns,
        coverage=BusinessTopologyCoverage(
            total_artifacts=0,
            cited_artifacts=0,
            total_evidence=0,
            cited_evidence=0,
            coverage_ratio=0.0,
            notes=[
                "Deterministic evidence-only fallback: L1 systems and directional "
                "data streams reconstructed from scanner edges and identity "
                "evidence after the language-model synthesis did not validate.",
            ],
        ),
    )
