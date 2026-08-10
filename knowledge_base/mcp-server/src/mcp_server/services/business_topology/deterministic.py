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
  set, cited by the edge plus both identity IDs.

The result is intentionally conservative (a small graph of well-supported
streams) and passes ``validate_business_topology`` by construction. It carries
no invented business meaning: every field is derived from scanner facts.
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


def build_deterministic_topology(
    bundle: BusinessTopologyEvidenceBundle,
    *,
    project_id: str,
    model_name: str = "deterministic_topology_fallback",
    max_stream_artifacts: int = 100,
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
    for edge_id, edge_type in bundle.evidence_types.items():
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
                artifact_ids=sorted(artifacts)[:max_stream_artifacts],
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
        data_streams=streams,
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
