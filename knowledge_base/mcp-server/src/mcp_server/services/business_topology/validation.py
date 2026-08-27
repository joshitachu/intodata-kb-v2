"""Structural, provenance, freshness, and secret checks for business topology."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from mcp_server.services.business_topology.evidence import BusinessTopologyEvidenceBundle
from mcp_server.services.business_topology.models import BusinessTopology, EvidenceBackedClaim


_DIRECTIONAL_EVIDENCE_TYPES = frozenset(
    {
        "scanner_edge",
        "scanner_relation",
    }
)
_SCANNER_EVIDENCE_TYPES = frozenset(
    {
        "scanner_artifact",
        "scanner_component",
        "scanner_connection",
        "scanner_system",
        "scanner_application",
        "scanner_resource",
        "scanner_endpoint",
        "scanner_channel",
        "scanner_lineage",
        *_DIRECTIONAL_EVIDENCE_TYPES,
    }
)

#: Evidence extracted from this run's own generated documentation. Deliberately
#: absent from every scanner set above: it makes a citation RESOLVABLE without
#: making it authoritative, so it can neither promote a system nor create an
#: inter-system edge. A stream whose direction rests on this tier alone is
#: capped below ``observed`` (see ``_DOC_DERIVED_MAX_STATUS``).
_DOC_DERIVED_EVIDENCE_TYPES = frozenset({"documentation_derived"})

#: Statuses a documentation-only claim may not reach. ``observed`` means a
#: directly stated technical fact and ``confirmed`` an approved business
#: declaration; generated prose is neither.
_DOC_DERIVED_FORBIDDEN_STATUSES = frozenset({"observed", "confirmed"})

_CANONICAL_PROTOCOLS = {
    "rest": "REST",
    "http": "REST",
    "webhook": "REST",
    "soap": "SOAP",
    "jms": "JMS",
    "activemq": "JMS",
    "queue": "JMS",
    "sftp": "SFTP",
    "file": "File",
    "file transfer": "File",
    "jdbc": "JDBC",
    "database": "Database",
    "sql": "Database",
}


def _normalize_structured_fields(topology: BusinessTopology) -> None:
    """Canonicalize additive enrichment without inventing absent facts."""

    def protocol(value: str) -> str:
        token = re.sub(r"\s+", " ", value.strip()).casefold()
        return _CANONICAL_PROTOCOLS.get(token, value.strip())

    path_by_id = {path.id: path for path in topology.delivery_paths}
    for path in topology.delivery_paths:
        for segment in path.segments:
            segment.protocol = protocol(segment.protocol) or "unknown"
            segment.mechanism = segment.mechanism.strip() or "unknown"
            segment.resource = segment.resource.strip()
            segment.component = segment.component.strip()
            segment.artifact_ids = list(dict.fromkeys(segment.artifact_ids))
    for stream in topology.data_streams:
        values = [protocol(value) for value in stream.protocols if value.strip()]
        path = path_by_id.get(stream.delivery_path_id or "")
        if path is not None:
            values.extend(
                segment.protocol
                for segment in path.segments
                if segment.protocol.casefold() not in {"", "unknown", "transformation"}
            )
        stream.protocols = list(dict.fromkeys(values))
        stream.artifact_ids = list(dict.fromkeys(stream.artifact_ids))
    for transformation in topology.transformations:
        transformation.stream_ids = list(dict.fromkeys(transformation.stream_ids))
        transformation.artifact_ids = list(dict.fromkeys(transformation.artifact_ids))
        for mapping in transformation.mappings:
            mapping.source = mapping.source.strip()
            mapping.target = mapping.target.strip()
    for system in topology.systems:
        system.aliases = list(dict.fromkeys(alias.strip() for alias in system.aliases if alias.strip()))
    for business_object in topology.business_objects:
        business_object.aliases = list(
            dict.fromkeys(alias.strip() for alias in business_object.aliases if alias.strip())
        )
_SYSTEM_IDENTITY_EVIDENCE_TYPES = frozenset(
    {
        "scanner_connection",
        "scanner_system",
        "scanner_application",
    }
)
_SUPPORTING_EVIDENCE_TYPES = frozenset(
    {
        "phase1_analysis",
        "phase2_analysis",
        "phase2_claim",
        "implemented_supporting",
        "observed_runtime",
        "intended_documented",
        "approved_business_declaration",
    }
)
_RUNTIME_EVIDENCE_TYPES = frozenset({"observed_runtime"})
_INTENDED_EVIDENCE_TYPES = frozenset(
    {"intended_documented", "approved_business_declaration"}
)


class BusinessTopologyValidationReport(BaseModel):
    validated: bool
    scan_fingerprint: str
    project_id: str
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    cited_evidence_count: int = 0
    artifact_count: int = 0
    cited_artifact_count: int = 0


def _strip_json_fence(text: str) -> str:
    """Extract the JSON object from a model response.

    Handles three shapes, in order:
    * the whole response is a ```json ... ``` fence (or a bare object);
    * a fenced ```json ... ``` block preceded/followed by prose (a model
      preamble such as "Given the tool budget errors, here is the JSON:");
    * an unfenced object embedded in surrounding prose.

    Returning the embedded object rather than failing at char 0 keeps a
    well-formed topology parseable even when the model adds narration.
    """

    value = text.strip()
    full = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, flags=re.DOTALL | re.IGNORECASE)
    if full:
        return full.group(1).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", value, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    if value.startswith("{") and value.endswith("}"):
        return value
    embedded = re.search(r"\{.*\}", value, flags=re.DOTALL)
    if embedded:
        return embedded.group(0).strip()
    return value


def _all_claims(topology: BusinessTopology) -> Iterable[EvidenceBackedClaim]:
    collections = (
        topology.systems,
        topology.integration_solutions,
        topology.business_objects,
        topology.data_streams,
        topology.delivery_paths,
        topology.transformations,
        topology.controls,
        topology.runtime_observations,
        topology.conflicts,
        topology.risks,
        topology.unknowns,
        topology.artifact_annotations,
        topology.component_annotations,
        topology.lineage_annotations,
    )
    for collection in collections:
        yield from collection
    for path in topology.delivery_paths:
        yield from path.segments


def _duplicate_ids(topology: BusinessTopology) -> list[str]:
    ids = [claim.id for claim in _all_claims(topology)]
    return sorted({item for item in ids if ids.count(item) > 1})


#: The secret shapes that fail a topology closed. Compiled once and shared with
#: :func:`redact_secretlike` so the scrubber and the check cannot drift -- a
#: scrubber that misses what the check catches turns a redaction bug into a
#: whole-lane failure.
_CREDENTIAL_URL_RE = re.compile(r"[a-z][a-z0-9+.-]*://[^\s/:]+:[^\s/@]+@", re.I)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.I)
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")


def redact_secretlike(text: str) -> str:
    """Replace secret-like spans in free text with ``[REDACTED]``.

    Used where prose is copied into the published topology. Generated documents
    are already credential-filtered, but a single leaked connection string in a
    description would otherwise fail the whole overlay (secret findings are
    correctly non-prunable) and silently downgrade the run to the deterministic
    skeleton. Redacting at the copy boundary keeps the failure proportionate
    without weakening the check.
    """

    if not text:
        return text
    scrubbed = _CREDENTIAL_URL_RE.sub("[REDACTED]@", text)
    scrubbed = _BEARER_RE.sub("Bearer [REDACTED]", scrubbed)
    scrubbed = _PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", scrubbed)
    return scrubbed


def _sensitive_findings(value: Any, path: str = "$") -> list[str]:
    findings: list[str] = []
    sensitive_key = re.compile(r"(?:password|passwd|secret|api[_-]?key|access[_-]?token|private[_-]?key)$", re.I)
    credential_url = _CREDENTIAL_URL_RE
    bearer = _BEARER_RE
    private_key = _PRIVATE_KEY_RE
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if sensitive_key.search(str(key)) and item not in (None, "", "[REDACTED]", "not established"):
                findings.append(f"sensitive value in {child}")
            findings.extend(_sensitive_findings(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(_sensitive_findings(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        if credential_url.search(value) or bearer.search(value) or private_key.search(value):
            findings.append(f"secret-like content in {path}")
    return findings


def _cited_evidence_ids(topology: BusinessTopology) -> set[str]:
    cited = {evidence_id for claim in _all_claims(topology) for evidence_id in claim.evidence_ids}
    cited.update(
        artifact_id
        for item in (
            *topology.integration_solutions,
            *topology.data_streams,
            *topology.transformations,
            *(segment for path in topology.delivery_paths for segment in path.segments),
        )
        for artifact_id in item.artifact_ids
    )
    cited.update(item.artifact_id for item in topology.artifact_annotations)
    cited.update(item.component_id for item in topology.component_annotations)
    cited.update(item.lineage_id for item in topology.lineage_annotations)
    return cited


def _recalculate_coverage(topology: BusinessTopology, bundle: BusinessTopologyEvidenceBundle) -> None:
    cited = _cited_evidence_ids(topology)
    cited_artifacts = cited & bundle.artifact_ids
    total = len(bundle.evidence_ids)
    topology.coverage.total_artifacts = len(bundle.artifact_ids)
    topology.coverage.cited_artifacts = len(cited_artifacts)
    topology.coverage.total_evidence = total
    topology.coverage.cited_evidence = len(cited & bundle.evidence_ids)
    topology.coverage.coverage_ratio = round(len(cited & bundle.evidence_ids) / total, 6) if total else 1.0
    relationships = [*topology.data_streams]
    relationships.extend(segment for path in topology.delivery_paths for segment in path.segments)
    topology.coverage.technical_relationships = len(relationships)
    topology.coverage.grounded_technical_relationships = sum(
        1
        for relationship in relationships
        if any(
            bundle.evidence_types.get(evidence_id) in _DIRECTIONAL_EVIDENCE_TYPES
            for evidence_id in relationship.evidence_ids
        )
    )


def _evidence_of_type(
    evidence_ids: Iterable[str],
    bundle: BusinessTopologyEvidenceBundle,
    allowed_types: frozenset[str],
) -> set[str]:
    return {
        evidence_id
        for evidence_id in evidence_ids
        if bundle.evidence_types.get(evidence_id) in allowed_types
    }


def _scanner_identity_evidence_ids(
    evidence_ids: Iterable[str], bundle: BusinessTopologyEvidenceBundle
) -> set[str]:
    result: set[str] = set()
    for evidence_id in evidence_ids:
        evidence_type = bundle.evidence_types.get(evidence_id)
        if evidence_type not in _SYSTEM_IDENTITY_EVIDENCE_TYPES:
            continue
        record = bundle.evidence_records.get(evidence_id) or {}
        if evidence_type == "scanner_connection":
            if not str(record.get("name") or "").strip():
                continue
        elif not str(record.get("scanner_id") or "").strip() or not str(
            record.get("name") or ""
        ).strip():
            continue
        result.add(evidence_id)
    return result


def _directional_artifact_ids(
    evidence_ids: Iterable[str], bundle: BusinessTopologyEvidenceBundle
) -> set[str]:
    """Resolve artifacts actually named by cited directional scanner rows."""

    identity_to_artifacts = _scanner_artifact_identity_map(bundle)

    resolved: set[str] = set()
    for evidence_id in evidence_ids:
        record = bundle.evidence_records.get(evidence_id) or {}
        for key, raw_value in record.items():
            normalized_key = str(key).casefold()
            if (
                "artifact" not in normalized_key
                and "job" not in normalized_key
                and normalized_key not in {"source", "target", "destination"}
            ):
                continue
            values = raw_value if isinstance(raw_value, list) else [raw_value]
            for value in values:
                identity = str(value or "").strip().casefold()
                resolved.update(identity_to_artifacts.get(identity, set()))
    return resolved


def _scanner_artifact_identity_map(
    bundle: BusinessTopologyEvidenceBundle,
) -> dict[str, set[str]]:
    identities: dict[str, set[str]] = {}
    for artifact_id in bundle.artifact_ids:
        record = bundle.evidence_records.get(artifact_id) or {}
        for key in ("name", "job_id", "path"):
            value = str(record.get(key) or "").strip().casefold()
            if value:
                identities.setdefault(value, set()).add(artifact_id)
        source_root = str(record.get("source_root") or "").strip()
        for key in ("name", "job_id"):
            value = str(record.get(key) or "").strip()
            if source_root and value:
                identities.setdefault(
                    f"{source_root}::{value}".casefold(), set()
                ).add(artifact_id)
    return identities


def _direction_matches_systems(
    *,
    evidence_ids: Iterable[str],
    source_evidence_ids: set[str],
    destination_evidence_ids: set[str],
    bundle: BusinessTopologyEvidenceBundle,
) -> bool:
    """Return whether one cited scanner edge establishes this exact direction."""

    artifact_identities = _scanner_artifact_identity_map(bundle)
    for evidence_id in evidence_ids:
        record = bundle.evidence_records.get(evidence_id) or {}
        evidence_type = bundle.evidence_types.get(evidence_id)
        if evidence_type == "scanner_edge":
            if (
                record.get("source_identity_evidence_id") in source_evidence_ids
                and record.get("destination_identity_evidence_id")
                in destination_evidence_ids
            ):
                return True
        elif evidence_type == "scanner_relation":
            source_artifacts: set[str] = set()
            destination_artifacts: set[str] = set()
            for key in ("source_artifact", "source_job", "source"):
                source_artifacts.update(
                    artifact_identities.get(
                        str(record.get(key) or "").strip().casefold(), set()
                    )
                )
            for key in (
                "target_artifact",
                "destination_artifact",
                "target_job",
                "target",
                "destination",
            ):
                destination_artifacts.update(
                    artifact_identities.get(
                        str(record.get(key) or "").strip().casefold(), set()
                    )
                )
            if (
                source_artifacts.intersection(source_evidence_ids)
                and destination_artifacts.intersection(destination_evidence_ids)
            ):
                return True
    return False


def validate_business_topology(
    topology: BusinessTopology,
    *,
    bundle: BusinessTopologyEvidenceBundle,
    project_id: str,
) -> BusinessTopologyValidationReport:
    errors: list[str] = []
    warnings: list[str] = []
    if topology.schema_version != "business-topology-v1":
        errors.append(f"unsupported schema_version: {topology.schema_version}")
    if topology.scan_fingerprint != bundle.scan_fingerprint:
        errors.append("top-level scan_fingerprint does not match current factual scan")
    if topology.metadata.scan_fingerprint != bundle.scan_fingerprint:
        errors.append("metadata.scan_fingerprint does not match current factual scan")
    if topology.metadata.project_id != project_id:
        errors.append("metadata.project_id does not match generation project")

    duplicates = _duplicate_ids(topology)
    if duplicates:
        errors.append("duplicate claim IDs: " + ", ".join(duplicates[:10]))

    claim_ids = {claim.id for claim in _all_claims(topology)}
    system_ids = {item.id for item in topology.systems}
    system_by_id = {item.id: item for item in topology.systems}
    solution_ids = {item.id for item in topology.integration_solutions}
    solution_by_id = {item.id: item for item in topology.integration_solutions}
    object_ids = {item.id for item in topology.business_objects}
    stream_ids = {item.id for item in topology.data_streams}
    path_ids = {item.id for item in topology.delivery_paths}
    control_ids = {item.id for item in topology.controls}
    risk_ids = {item.id for item in topology.risks}
    unknown_ids = {item.id for item in topology.unknowns}
    for claim in _all_claims(topology):
        unknown_evidence = sorted(set(claim.evidence_ids) - bundle.evidence_ids)
        if unknown_evidence:
            errors.append(f"{claim.id} cites unknown evidence: {', '.join(unknown_evidence[:5])}")
        if claim.status == "confirmed":
            supported = any(bundle.evidence_types.get(item) == "approved_business_declaration" for item in claim.evidence_ids)
            if not supported:
                errors.append(f"{claim.id} is confirmed without approved business evidence")
        if claim.status not in {"not_established", "rejected"} and not claim.evidence_ids:
            errors.append(f"{claim.id} has no evidence_ids")

    for system in topology.systems:
        if system.kind == "unresolved" or system.status in {"not_established", "rejected"}:
            continue
        scanner_ids = _evidence_of_type(
            system.evidence_ids,
            bundle,
            _SCANNER_EVIDENCE_TYPES,
        )
        identity_ids = _scanner_identity_evidence_ids(system.evidence_ids, bundle)
        if not scanner_ids:
            errors.append(f"{system.id} is an unsupported system promotion without scanner evidence")
        elif not identity_ids:
            errors.append(
                f"{system.id} is an unsupported system promotion without "
                "scanner-backed system/application identity evidence"
            )

    for solution in topology.integration_solutions:
        if solution.status in {"not_established", "rejected"}:
            continue
        if not solution.artifact_ids:
            errors.append(f"{solution.id} has no scanner artifact_ids")
        if not _evidence_of_type(solution.evidence_ids, bundle, _SCANNER_EVIDENCE_TYPES):
            errors.append(f"{solution.id} has no scanner evidence")
        if not set(solution.artifact_ids).intersection(solution.evidence_ids):
            errors.append(f"{solution.id} does not cite one of its scanner artifact_ids")

    doc_derived_stream_ids: set[str] = set()
    for stream in topology.data_streams:
        directional_ids = {
            item
            for item in stream.evidence_ids
            if bundle.evidence_types.get(item) in _DIRECTIONAL_EVIDENCE_TYPES
        }
        doc_derived_ids = _evidence_of_type(
            stream.evidence_ids, bundle, _DOC_DERIVED_EVIDENCE_TYPES
        )
        if not directional_ids:
            # The documentation tier may assert a direction the scanner never
            # inferred -- the verified reason the curated werkorder topology is
            # richer than any scanner-only merge. It is accepted as a WARNING
            # only when the flow is still wired to real scanner artifacts and
            # stays below `observed`; otherwise it remains a hard error.
            if doc_derived_ids and stream.artifact_ids:
                doc_derived_stream_ids.add(stream.id)
                warnings.append(
                    f"{stream.id} direction is asserted by generated documentation "
                    "only; no scanner directional edge establishes it"
                )
                if stream.status in _DOC_DERIVED_FORBIDDEN_STATUSES:
                    errors.append(
                        f"{stream.id} is documentation-derived and may not claim "
                        f"status {stream.status}"
                    )
            else:
                errors.append(f"{stream.id} has no directional scanner evidence")
        if not stream.artifact_ids:
            errors.append(f"{stream.id} has no scanner artifact_ids for technical traceability")
        elif directional_ids:
            direction_artifacts = _directional_artifact_ids(directional_ids, bundle)
            if not direction_artifacts:
                errors.append(
                    f"{stream.id} directional evidence cannot be resolved to a scanner artifact"
                )
            elif not (set(stream.artifact_ids) & direction_artifacts):
                errors.append(
                    f"{stream.id} directional evidence does not touch its cited scanner artifacts"
                )
        if stream.source_system_id not in system_ids:
            errors.append(f"{stream.id} has unknown source_system_id {stream.source_system_id}")
        if stream.destination_system_id not in system_ids:
            errors.append(f"{stream.id} has unknown destination_system_id {stream.destination_system_id}")
        source_system = system_by_id.get(stream.source_system_id)
        destination_system = system_by_id.get(stream.destination_system_id)
        if (
            directional_ids
            and source_system is not None
            and destination_system is not None
            and not _direction_matches_systems(
                evidence_ids=directional_ids,
                source_evidence_ids=set(source_system.evidence_ids),
                destination_evidence_ids=set(destination_system.evidence_ids),
                bundle=bundle,
            )
        ):
            errors.append(
                f"{stream.id} directional evidence does not establish its declared source/destination"
            )
        for ref in stream.business_object_ids:
            if ref not in object_ids:
                errors.append(f"{stream.id} has unknown business_object_id {ref}")
        for ref in stream.integration_solution_ids:
            if ref not in solution_ids:
                errors.append(f"{stream.id} has unknown integration_solution_id {ref}")
        if stream.delivery_path_id and stream.delivery_path_id not in path_ids:
            errors.append(f"{stream.id} has unknown delivery_path_id {stream.delivery_path_id}")
        for refs, allowed, label in (
            (stream.control_ids, control_ids, "control"),
            (stream.risk_ids, risk_ids, "risk"),
            (stream.unknown_ids, unknown_ids, "unknown"),
        ):
            for ref in refs:
                if ref not in allowed:
                    errors.append(f"{stream.id} has unknown {label}_id {ref}")
        for artifact_id in stream.artifact_ids:
            if artifact_id not in bundle.artifact_ids:
                errors.append(f"{stream.id} has unknown artifact_id {artifact_id}")

    path_by_id = {path.id: path for path in topology.delivery_paths}
    for path in topology.delivery_paths:
        if path.stream_id not in stream_ids:
            errors.append(f"{path.id} has unknown stream_id {path.stream_id}")
        if not path.segments:
            errors.append(f"{path.id} has no delivery segments")
        sequences = [segment.sequence for segment in path.segments]
        if sequences != list(range(1, len(sequences) + 1)):
            errors.append(f"{path.id} segments must be ordered contiguously from 1")
        internal_refs = {
            ref
            for segment in path.segments
            for ref in (segment.source_ref, segment.destination_ref)
            if ref.startswith("step:")
        }
        allowed_refs = system_ids | solution_ids | internal_refs
        for segment in path.segments:
            directional_ids = _evidence_of_type(
                segment.evidence_ids,
                bundle,
                _DIRECTIONAL_EVIDENCE_TYPES,
            )
            if not directional_ids:
                # Inherit the owning stream's tier: a documentation-derived
                # stream cannot have scanner-backed segments, and rejecting them
                # would discard the ordered delivery detail that makes the
                # overlay useful while keeping the stream itself.
                if path.stream_id in doc_derived_stream_ids and _evidence_of_type(
                    segment.evidence_ids, bundle, _DOC_DERIVED_EVIDENCE_TYPES
                ):
                    if segment.status in _DOC_DERIVED_FORBIDDEN_STATUSES:
                        errors.append(
                            f"{segment.id} is documentation-derived and may not claim "
                            f"status {segment.status}"
                        )
                else:
                    errors.append(f"{segment.id} has no directional scanner evidence")
            stream = next((item for item in topology.data_streams if item.id == path.stream_id), None)
            if stream is not None and directional_ids:
                direction_artifacts = _directional_artifact_ids(directional_ids, bundle)
                if not direction_artifacts:
                    errors.append(
                        f"{segment.id} directional evidence cannot be resolved to a scanner artifact"
                    )
                elif not (set(stream.artifact_ids) & direction_artifacts):
                    errors.append(
                        f"{segment.id} directional evidence does not touch its stream's scanner artifacts"
                    )
            if segment.source_ref not in allowed_refs:
                errors.append(f"{segment.id} has unknown source_ref {segment.source_ref}")
            if segment.destination_ref not in allowed_refs:
                errors.append(f"{segment.id} has unknown destination_ref {segment.destination_ref}")
            for artifact_id in segment.artifact_ids:
                if artifact_id not in bundle.artifact_ids:
                    errors.append(f"{segment.id} has unknown artifact_id {artifact_id}")
            source_claim = system_by_id.get(segment.source_ref) or solution_by_id.get(
                segment.source_ref
            )
            destination_claim = system_by_id.get(
                segment.destination_ref
            ) or solution_by_id.get(segment.destination_ref)
            if (
                directional_ids
                and source_claim is not None
                and destination_claim is not None
            ):
                source_evidence = set(source_claim.evidence_ids)
                destination_evidence = set(destination_claim.evidence_ids)
                if hasattr(source_claim, "artifact_ids"):
                    source_evidence.update(source_claim.artifact_ids)
                if hasattr(destination_claim, "artifact_ids"):
                    destination_evidence.update(destination_claim.artifact_ids)
                if not _direction_matches_systems(
                    evidence_ids=directional_ids,
                    source_evidence_ids=source_evidence,
                    destination_evidence_ids=destination_evidence,
                    bundle=bundle,
                ):
                    errors.append(
                        f"{segment.id} directional evidence does not establish its declared source/destination"
                    )
        if path.segments:
            if path.segments[0].source_ref != next(
                (stream.source_system_id for stream in topology.data_streams if stream.id == path.stream_id),
                "",
            ):
                errors.append(f"{path.id} does not start at its stream source")
            if path.segments[-1].destination_ref != next(
                (stream.destination_system_id for stream in topology.data_streams if stream.id == path.stream_id),
                "",
            ):
                errors.append(f"{path.id} does not end at its stream destination")
            for previous, current in zip(path.segments, path.segments[1:]):
                if previous.destination_ref != current.source_ref:
                    errors.append(f"{path.id} has disconnected delivery segments")
    for stream in topology.data_streams:
        if stream.delivery_path_id and path_by_id.get(stream.delivery_path_id, None):
            if path_by_id[stream.delivery_path_id].stream_id != stream.id:
                errors.append(f"{stream.id} delivery path belongs to another stream")

    for transformation in topology.transformations:
        for ref in transformation.stream_ids:
            if ref not in stream_ids:
                errors.append(f"{transformation.id} has unknown stream_id {ref}")
        for ref in transformation.integration_solution_ids:
            if ref not in solution_ids:
                errors.append(
                    f"{transformation.id} has unknown integration_solution_id {ref}"
                )
        for artifact_id in transformation.artifact_ids:
            if artifact_id not in bundle.artifact_ids:
                errors.append(
                    f"{transformation.id} has unknown artifact_id {artifact_id}"
                )

    observation_ids = {item.id for item in topology.runtime_observations}
    conflict_ids = {item.id for item in topology.conflicts}
    for stream in topology.data_streams:
        for ref in stream.runtime_observation_ids:
            if ref not in observation_ids:
                errors.append(f"{stream.id} has unknown runtime_observation_id {ref}")
        for ref in stream.conflict_ids:
            if ref not in conflict_ids:
                errors.append(f"{stream.id} has unknown conflict_id {ref}")

    for observation in topology.runtime_observations:
        if observation.status != "observed":
            errors.append(f"{observation.id} runtime status must be observed")
        if not observation.stream_ids and not observation.delivery_path_ids:
            errors.append(f"{observation.id} does not annotate a stream or delivery path")
        for ref in observation.stream_ids:
            if ref not in stream_ids:
                errors.append(f"{observation.id} has unknown stream_id {ref}")
        for ref in observation.delivery_path_ids:
            if ref not in path_ids:
                errors.append(f"{observation.id} has unknown delivery_path_id {ref}")
        if not _evidence_of_type(
            observation.evidence_ids, bundle, _RUNTIME_EVIDENCE_TYPES
        ):
            errors.append(f"{observation.id} has no typed runtime evidence")

    conflict_subject_ids = claim_ids - conflict_ids
    for conflict in topology.conflicts:
        if not conflict.subject_refs:
            errors.append(f"{conflict.id} has no subject_refs")
        for ref in conflict.subject_refs:
            if ref not in conflict_subject_ids:
                errors.append(f"{conflict.id} has unknown subject_ref {ref}")
        implementation_ids = set(conflict.implementation_evidence_ids)
        supporting_ids = set(conflict.supporting_evidence_ids)
        scanner_implementation_ids = _evidence_of_type(
            implementation_ids, bundle, _SCANNER_EVIDENCE_TYPES
        )
        if conflict.conflict_type == "supporting_vs_implementation":
            accepted_supporting_ids = _evidence_of_type(
                supporting_ids, bundle, _INTENDED_EVIDENCE_TYPES
            )
            if not implementation_ids or scanner_implementation_ids != implementation_ids:
                errors.append(
                    f"{conflict.id} does not contain only scanner implementation evidence"
                )
            if not supporting_ids or accepted_supporting_ids != supporting_ids:
                errors.append(
                    f"{conflict.id} does not contain only intended/documented supporting evidence"
                )
        elif conflict.conflict_type == "runtime_vs_implementation":
            accepted_supporting_ids = _evidence_of_type(
                supporting_ids, bundle, _RUNTIME_EVIDENCE_TYPES
            )
            if not implementation_ids or scanner_implementation_ids != implementation_ids:
                errors.append(
                    f"{conflict.id} does not contain only scanner implementation evidence"
                )
            if not supporting_ids or accepted_supporting_ids != supporting_ids:
                errors.append(
                    f"{conflict.id} does not contain only typed runtime evidence"
                )
        elif conflict.conflict_type == "supporting_sources_disagree":
            accepted_supporting_ids = _evidence_of_type(
                supporting_ids,
                bundle,
                _INTENDED_EVIDENCE_TYPES | _RUNTIME_EVIDENCE_TYPES,
            )
            if implementation_ids:
                errors.append(
                    f"{conflict.id} supporting-source conflict must not declare implementation evidence"
                )
            if len(supporting_ids) < 2 or accepted_supporting_ids != supporting_ids:
                errors.append(
                    f"{conflict.id} requires at least two typed supporting evidence IDs"
                )
        else:
            accepted_supporting_ids = _evidence_of_type(
                supporting_ids,
                bundle,
                _INTENDED_EVIDENCE_TYPES | _RUNTIME_EVIDENCE_TYPES,
            )
            if implementation_ids and scanner_implementation_ids != implementation_ids:
                errors.append(
                    f"{conflict.id} unresolved implementation evidence is not scanner-backed"
                )
            if not supporting_ids or accepted_supporting_ids != supporting_ids:
                errors.append(
                    f"{conflict.id} unresolved conflict has no typed supporting evidence"
                )
        declared = implementation_ids | supporting_ids
        if not declared.issubset(conflict.evidence_ids):
            errors.append(f"{conflict.id} evidence_ids omit declared conflict evidence")

    for collection in (topology.controls, topology.risks, topology.unknowns):
        for claim in collection:
            for ref in claim.stream_ids:
                if ref not in stream_ids:
                    errors.append(f"{claim.id} has unknown stream_id {ref}")
    for control in topology.controls:
        if control.status == "observed" and not _evidence_of_type(
            control.evidence_ids,
            bundle,
            _SCANNER_EVIDENCE_TYPES,
        ):
            errors.append(f"{control.id} is observed without scanner evidence")
    for item in topology.integration_solutions:
        for artifact_id in item.artifact_ids:
            if artifact_id not in bundle.artifact_ids:
                errors.append(f"{item.id} has unknown artifact_id {artifact_id}")
    for item in topology.artifact_annotations:
        if item.artifact_id not in bundle.artifact_ids:
            errors.append(f"{item.id} targets unknown artifact_id {item.artifact_id}")
    for item in topology.component_annotations:
        if item.component_id not in bundle.component_ids:
            errors.append(f"{item.id} targets unknown component_id {item.component_id}")
    for item in topology.lineage_annotations:
        if item.lineage_id not in bundle.lineage_ids:
            errors.append(f"{item.id} targets unknown lineage_id {item.lineage_id}")
        if item.business_object_id and item.business_object_id not in object_ids:
            errors.append(f"{item.id} has unknown business_object_id {item.business_object_id}")

    errors.extend(_sensitive_findings(topology.model_dump()))
    _recalculate_coverage(topology, bundle)
    cited = _cited_evidence_ids(topology)
    if not topology.data_streams:
        warnings.append("No business data streams were established from the available evidence")
    streams_without_protocols = sum(not stream.protocols for stream in topology.data_streams)
    if streams_without_protocols:
        warnings.append(
            f"{streams_without_protocols} data stream(s) have no structured protocols; "
            "mechanism details may remain only in evidence or prose"
        )
    incomplete_segments = sum(
        segment.protocol == "unknown" or not (segment.component or segment.resource)
        for path in topology.delivery_paths
        for segment in path.segments
    )
    if incomplete_segments:
        warnings.append(
            f"{incomplete_segments} delivery segment(s) lack a structured protocol, "
            "component, or resource"
        )
    empty_transformations = sum(
        not transformation.mappings for transformation in topology.transformations
    )
    if empty_transformations:
        warnings.append(
            f"{empty_transformations} transformation claim(s) contain no structured mappings"
        )
    if topology.coverage.cited_artifacts < topology.coverage.total_artifacts:
        warnings.append(
            f"Topology cites {topology.coverage.cited_artifacts} of {topology.coverage.total_artifacts} scanned artifacts; "
            "uncited artifacts remain available for technical drill-down"
        )
    if len(claim_ids) > 500:
        warnings.append("Business topology contains more than 500 claims and may be too dense for L1/L2 presentation")
    return BusinessTopologyValidationReport(
        validated=not errors,
        scan_fingerprint=bundle.scan_fingerprint,
        project_id=project_id,
        errors=errors,
        warnings=warnings,
        evidence_count=len(bundle.evidence_ids),
        cited_evidence_count=len(cited & bundle.evidence_ids),
        artifact_count=len(bundle.artifact_ids),
        cited_artifact_count=len(cited & bundle.artifact_ids),
    )


def parse_business_topology(
    text: str,
    *,
    bundle: BusinessTopologyEvidenceBundle,
    project_id: str,
) -> tuple[BusinessTopology | None, BusinessTopologyValidationReport | None]:
    """Parse model output into a schema-valid topology WITHOUT semantic checks.

    Returns ``(topology, None)`` on success or ``(None, failing report)`` when
    the text is empty or not a schema-conformant JSON object. An empty response
    (verified shape: provider-level failure such as a 402 leaves ``text=\"\"``)
    is reported as such instead of a misleading char-0 JSON error.
    """

    def _failure(message: str) -> BusinessTopologyValidationReport:
        return BusinessTopologyValidationReport(
            validated=False,
            scan_fingerprint=bundle.scan_fingerprint,
            project_id=project_id,
            errors=[message],
            evidence_count=len(bundle.evidence_ids),
            artifact_count=len(bundle.artifact_ids),
        )

    if not text or not text.strip():
        return None, _failure(
            "model returned no output text (provider failure or empty response)"
        )
    try:
        payload = json.loads(_strip_json_fence(text))
        if isinstance(payload, dict) and set(payload) == {"business_topology"}:
            payload = payload["business_topology"]
        topology = BusinessTopology.model_validate(payload)
        _normalize_structured_fields(topology)
        return topology, None
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        return None, _failure(f"invalid business topology JSON: {exc}")


def parse_and_validate_business_topology(
    text: str,
    *,
    bundle: BusinessTopologyEvidenceBundle,
    project_id: str,
) -> tuple[BusinessTopology | None, BusinessTopologyValidationReport]:
    topology, parse_failure = parse_business_topology(
        text, bundle=bundle, project_id=project_id
    )
    if topology is None:
        assert parse_failure is not None
        return None, parse_failure
    report = validate_business_topology(topology, bundle=bundle, project_id=project_id)
    return (topology if report.validated else None), report
