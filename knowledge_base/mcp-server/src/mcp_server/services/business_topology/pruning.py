"""Partial acceptance: drop the offending claim, keep the architecture.

Validation is all-or-nothing today, and that is the single most expensive
behaviour in the phase. Verified on the werkorder run: ``ETTM_part2`` produced
14 systems, 8 streams, 8 delivery paths and 5 business objects, and every one of
them was discarded because some claims carried the wrong ``status`` enum and a
solution had an empty ``artifact_ids``. What shipped instead was a deterministic
skeleton with 4 systems, 3 streams and no business layer.

Every per-claim error message in :mod:`validation` begins with the claim id, so
offending claims can be identified, removed together with the references that
would dangle, and the remainder re-validated. Global errors (schema version,
scan fingerprint, duplicate ids, secret findings) are NOT prunable -- they
describe the document, not a claim, and a topology carrying one must still fail.
"""

from __future__ import annotations

import re

from mcp_server.services.business_topology.evidence import (
    BusinessTopologyEvidenceBundle,
)
from mcp_server.services.business_topology.models import BusinessTopology
from mcp_server.services.business_topology.validation import (
    BusinessTopologyValidationReport,
    validate_business_topology,
)

#: Claim ids are emitted as the first whitespace-delimited token of an error.
#: Segment and step ids contain colons, so the pattern is deliberately broad and
#: the result is always intersected with the ids actually present.
_LEADING_ID = re.compile(r"^([A-Za-z0-9:_.\-]+)\s")

_MAX_PASSES = 5


def _claim_ids_in_errors(errors: list[str], known_ids: set[str]) -> set[str]:
    found: set[str] = set()
    for error in errors:
        match = _LEADING_ID.match(error)
        if match and match.group(1) in known_ids:
            found.add(match.group(1))
    return found


def _prunable_ids(topology: BusinessTopology) -> dict[str, str]:
    """Return ``{claim id: owning collection}`` for everything droppable.

    A delivery SEGMENT maps to its path: segments must stay contiguous and
    anchored to the stream's endpoints, so removing one would break the path
    that contains it. Dropping the whole path keeps the stream.
    """

    owners: dict[str, str] = {}
    for name in (
        "systems",
        "integration_solutions",
        "business_objects",
        "data_streams",
        "delivery_paths",
        "transformations",
        "business_rules",
        "controls",
        "risks",
        "unknowns",
        "runtime_observations",
        "conflicts",
        "artifact_annotations",
        "component_annotations",
        "lineage_annotations",
    ):
        for item in getattr(topology, name):
            owners[item.id] = name
    for path in topology.delivery_paths:
        for segment in path.segments:
            owners[segment.id] = "delivery_paths::segment"
    return owners


def _apply_drops(topology: BusinessTopology, drop_ids: set[str]) -> None:
    """Remove the named claims and every reference that would dangle."""

    owners = _prunable_ids(topology)
    segment_paths = {
        segment.id: path.id
        for path in topology.delivery_paths
        for segment in path.segments
    }
    # A dropped segment takes its path with it.
    path_drops = {
        segment_paths[claim_id]
        for claim_id in drop_ids
        if owners.get(claim_id) == "delivery_paths::segment"
    }
    drop_ids = drop_ids | path_drops

    for name in (
        "systems",
        "integration_solutions",
        "business_objects",
        "data_streams",
        "delivery_paths",
        "transformations",
        "business_rules",
        "controls",
        "risks",
        "unknowns",
        "runtime_observations",
        "conflicts",
        "artifact_annotations",
        "component_annotations",
        "lineage_annotations",
    ):
        setattr(
            topology,
            name,
            [item for item in getattr(topology, name) if item.id not in drop_ids],
        )

    _cascade(topology)


def _cascade(topology: BusinessTopology) -> None:
    """Repair references after a removal, dropping what cannot stand alone."""

    system_ids = {item.id for item in topology.systems}
    object_ids = {item.id for item in topology.business_objects}
    solution_ids = {item.id for item in topology.integration_solutions}

    # A stream without both endpoints is not a relationship.
    topology.data_streams = [
        stream
        for stream in topology.data_streams
        if stream.source_system_id in system_ids
        and stream.destination_system_id in system_ids
    ]
    stream_ids = {item.id for item in topology.data_streams}

    topology.delivery_paths = [
        path for path in topology.delivery_paths if path.stream_id in stream_ids
    ]
    path_ids = {item.id for item in topology.delivery_paths}

    control_ids = {item.id for item in topology.controls}
    risk_ids = {item.id for item in topology.risks}
    unknown_ids = {item.id for item in topology.unknowns}
    observation_ids = {item.id for item in topology.runtime_observations}
    conflict_ids = {item.id for item in topology.conflicts}

    for stream in topology.data_streams:
        stream.business_object_ids = [
            ref for ref in stream.business_object_ids if ref in object_ids
        ]
        stream.integration_solution_ids = [
            ref for ref in stream.integration_solution_ids if ref in solution_ids
        ]
        stream.control_ids = [ref for ref in stream.control_ids if ref in control_ids]
        stream.risk_ids = [ref for ref in stream.risk_ids if ref in risk_ids]
        stream.unknown_ids = [ref for ref in stream.unknown_ids if ref in unknown_ids]
        stream.runtime_observation_ids = [
            ref for ref in stream.runtime_observation_ids if ref in observation_ids
        ]
        stream.conflict_ids = [
            ref for ref in stream.conflict_ids if ref in conflict_ids
        ]
        if stream.delivery_path_id and stream.delivery_path_id not in path_ids:
            stream.delivery_path_id = None

    for collection in (topology.controls, topology.risks, topology.unknowns):
        for claim in collection:
            claim.stream_ids = [ref for ref in claim.stream_ids if ref in stream_ids]

    for transformation in topology.transformations:
        transformation.stream_ids = [
            ref for ref in transformation.stream_ids if ref in stream_ids
        ]
        transformation.integration_solution_ids = [
            ref for ref in transformation.integration_solution_ids if ref in solution_ids
        ]
    # A transformation that no longer annotates any stream has nothing to say.
    topology.transformations = [
        item for item in topology.transformations if item.stream_ids
    ]

    transformation_ids = {item.id for item in topology.transformations}
    for rule in topology.business_rules:
        rule.stream_ids = [ref for ref in rule.stream_ids if ref in stream_ids]
        rule.transformation_ids = [
            ref for ref in rule.transformation_ids if ref in transformation_ids
        ]
    # A rule detached from every stream can no longer be acted on: nobody can
    # tell which exchange it governs, so publishing it would leave an
    # unanswerable question rather than a finding.
    topology.business_rules = [
        item for item in topology.business_rules if item.stream_ids
    ]

    for observation in topology.runtime_observations:
        observation.stream_ids = [
            ref for ref in observation.stream_ids if ref in stream_ids
        ]
        observation.delivery_path_ids = [
            ref for ref in observation.delivery_path_ids if ref in path_ids
        ]
    topology.runtime_observations = [
        item
        for item in topology.runtime_observations
        if item.stream_ids or item.delivery_path_ids
    ]

    remaining_ids = {
        item.id
        for name in (
            "systems",
            "integration_solutions",
            "business_objects",
            "data_streams",
            "delivery_paths",
            "transformations",
            "business_rules",
            "controls",
            "risks",
            "unknowns",
            "runtime_observations",
        )
        for item in getattr(topology, name)
    }
    for conflict in topology.conflicts:
        conflict.subject_refs = [
            ref for ref in conflict.subject_refs if ref in remaining_ids
        ]
    topology.conflicts = [item for item in topology.conflicts if item.subject_refs]

    for annotation in topology.lineage_annotations:
        if annotation.business_object_id and annotation.business_object_id not in object_ids:
            annotation.business_object_id = None


def prune_to_valid(
    topology: BusinessTopology,
    *,
    bundle: BusinessTopologyEvidenceBundle,
    project_id: str,
    max_passes: int = _MAX_PASSES,
) -> tuple[BusinessTopology, BusinessTopologyValidationReport, list[str]]:
    """Drop invalid claims until the topology validates, or give up.

    Returns ``(topology, report, dropped_ids)``. The topology is mutated in
    place. When no prunable claim explains the remaining errors -- a global
    defect, or an error whose message does not lead with a claim id -- the
    failing report is returned unchanged so the caller still fails closed.
    """

    dropped: list[str] = []
    report = validate_business_topology(topology, bundle=bundle, project_id=project_id)
    for _ in range(max_passes):
        if report.validated:
            break
        candidates = _claim_ids_in_errors(report.errors, set(_prunable_ids(topology)))
        if not candidates:
            break
        _apply_drops(topology, candidates)
        dropped.extend(sorted(candidates))
        report = validate_business_topology(
            topology, bundle=bundle, project_id=project_id
        )

    if dropped:
        note = (
            f"{len(dropped)} claim(s) were dropped for failing provenance validation "
            f"(e.g. {', '.join(dropped[:5])}); the remaining topology published as-is"
        )
        report.warnings = [note, *report.warnings]
        if note not in topology.coverage.notes:
            topology.coverage.notes.append(note)
    return topology, report, dropped
