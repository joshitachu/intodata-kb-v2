"""Targeted deterministic repair for verified business-topology failure shapes.

The werkorder composite run (2026-08-10, partitions ETTM_part2 /
EnterpriseServiceBus_part1 / EnterpriseServiceBus_part2) showed the LLM
producing complete, parseable, largely well-grounded topologies that were then
rejected WHOLESALE by ``validate_business_topology`` over a handful of
mechanical grounding violations. ESB_part1 attempt 01 (12 systems, 4 business
objects, 1 solution, 1 stream) failed on exactly 3 errors; the blind retry then
diverged and produced 36 errors. All observed error shapes are repairable in
Python without weakening validation -- the repaired topology is re-validated by
the SAME validator and still cites only evidence present in the pushed bundle:

* ``X does not cite one of its scanner artifact_ids`` -- the solution listed
  valid local ``artifact_ids`` but did not repeat any of them in
  ``evidence_ids``; appending its own (already-verified) artifact IDs is a
  citation normalization, not a new claim.
* ``X has no scanner artifact_ids`` (integration solutions for SIBLING
  sub-projects of a composite upload, whose artifacts are not in this
  partition's bundle) -- downgraded to ``not_established`` so the narrative is
  kept without asserting local implementation grounding.
* ``X has unknown artifact_id artifact:<hash>`` -- the model rewrote a
  ``landscape-application:<hash>`` identity into a fabricated ``artifact:``
  ID (verified: ``artifact:2b3dc8c9e713bc0d5c43`` == the AssetCheck
  application identity hash). Unknown citations are dropped, never remapped.
* ``X has no scanner artifact_ids for technical traceability`` -- the stream
  cited a real directional edge but left ``artifact_ids`` empty; the edge's own
  ``artifact_names`` are resolved against this partition's artifact ledger
  (the exact resolution the validator uses) and backfilled.
* ``X directional evidence cannot be resolved to a scanner artifact`` /
  ``... does not establish its declared source/destination`` -- the stream or
  delivery segment is not honestly repairable and is DROPPED (with a note),
  along with anything that references it.
* ``X is an unsupported system promotion without ... identity evidence`` --
  downgraded to ``kind="unresolved"`` / ``status="not_established"``.

Anything the repair cannot make pass validation honestly stays failed, and the
caller falls back to the deterministic evidence-only topology.
"""

from __future__ import annotations

from mcp_server.services.business_topology.evidence import (
    BusinessTopologyEvidenceBundle,
)
from mcp_server.services.business_topology.models import (
    BusinessTopology,
    EvidenceBackedClaim,
)
from mcp_server.services.business_topology.validation import (
    _DIRECTIONAL_EVIDENCE_TYPES,
    _SCANNER_EVIDENCE_TYPES,
    _all_claims,
    _direction_matches_systems,
    _directional_artifact_ids,
    _evidence_of_type,
    _scanner_identity_evidence_ids,
    BusinessTopologyValidationReport,
    parse_business_topology,
    validate_business_topology,
)

_SKIPPED_STATUSES = frozenset({"not_established", "rejected"})


def _drop_unknown_citations(
    topology: BusinessTopology,
    bundle: BusinessTopologyEvidenceBundle,
    notes: list[str],
) -> None:
    """Remove citations that do not exist in the pushed evidence bundle.

    Verified shape: fabricated ``artifact:<hash>`` IDs minted from
    ``landscape-application`` identity hashes. Dropping (never remapping) keeps
    the cite-only-what-you-were-given contract intact.
    """

    for claim in _all_claims(topology):
        unknown = [item for item in claim.evidence_ids if item not in bundle.evidence_ids]
        if unknown:
            claim.evidence_ids = [
                item for item in claim.evidence_ids if item in bundle.evidence_ids
            ]
            notes.append(
                f"{claim.id}: dropped unknown evidence citation(s) "
                + ", ".join(sorted(unknown)[:3])
            )
    for item in (*topology.integration_solutions, *topology.data_streams):
        unknown = [ref for ref in item.artifact_ids if ref not in bundle.artifact_ids]
        if unknown:
            item.artifact_ids = [
                ref for ref in item.artifact_ids if ref in bundle.artifact_ids
            ]
            notes.append(
                f"{item.id}: dropped unknown artifact_id(s) "
                + ", ".join(sorted(unknown)[:3])
            )
    kept_artifact_annotations = []
    for annotation in topology.artifact_annotations:
        if annotation.artifact_id in bundle.artifact_ids:
            kept_artifact_annotations.append(annotation)
        else:
            notes.append(f"{annotation.id}: dropped (unknown artifact target)")
    topology.artifact_annotations = kept_artifact_annotations
    kept_component_annotations = []
    for annotation in topology.component_annotations:
        if annotation.component_id in bundle.component_ids:
            kept_component_annotations.append(annotation)
        else:
            notes.append(f"{annotation.id}: dropped (unknown component target)")
    topology.component_annotations = kept_component_annotations
    kept_lineage_annotations = []
    for annotation in topology.lineage_annotations:
        if annotation.lineage_id in bundle.lineage_ids:
            kept_lineage_annotations.append(annotation)
        else:
            notes.append(f"{annotation.id}: dropped (unknown lineage target)")
    topology.lineage_annotations = kept_lineage_annotations


def _downgrade_unsupported_claims(
    topology: BusinessTopology,
    bundle: BusinessTopologyEvidenceBundle,
    notes: list[str],
) -> None:
    """Downgrade claims validation would reject, instead of losing the output.

    * confirmed claims without approved business evidence -> ``inferred``;
    * evidence-less claims -> ``not_established`` (validator-skipped);
    * systems without scanner identity evidence -> ``unresolved``;
    * integration solutions without local artifacts/scanner evidence ->
      ``not_established`` (verified: sibling sub-project solutions in a
      composite upload cannot cite this partition's artifacts).
    """

    def _claim_floor(claim: EvidenceBackedClaim) -> None:
        if claim.status == "confirmed" and not any(
            bundle.evidence_types.get(item) == "approved_business_declaration"
            for item in claim.evidence_ids
        ):
            claim.status = "inferred"
            notes.append(f"{claim.id}: downgraded confirmed -> inferred (no approved evidence)")
        if claim.status not in _SKIPPED_STATUSES and not claim.evidence_ids:
            claim.status = "not_established"
            notes.append(f"{claim.id}: downgraded to not_established (no resolvable evidence)")

    for claim in _all_claims(topology):
        _claim_floor(claim)

    for system in topology.systems:
        if system.kind == "unresolved" or system.status in _SKIPPED_STATUSES:
            continue
        scanner_ids = _evidence_of_type(system.evidence_ids, bundle, _SCANNER_EVIDENCE_TYPES)
        identity_ids = _scanner_identity_evidence_ids(system.evidence_ids, bundle)
        if not scanner_ids or not identity_ids:
            system.kind = "unresolved"
            system.status = "not_established"
            notes.append(
                f"{system.id}: downgraded to unresolved/not_established "
                "(no scanner-backed identity evidence)"
            )

    for solution in topology.integration_solutions:
        if solution.status in _SKIPPED_STATUSES:
            continue
        has_scanner = bool(
            _evidence_of_type(solution.evidence_ids, bundle, _SCANNER_EVIDENCE_TYPES)
        )
        if solution.artifact_ids and has_scanner:
            if not set(solution.artifact_ids).intersection(solution.evidence_ids):
                solution.evidence_ids = [*solution.evidence_ids, *solution.artifact_ids]
                notes.append(
                    f"{solution.id}: cited its own artifact_ids in evidence_ids "
                    "(citation normalization)"
                )
        else:
            solution.status = "not_established"
            notes.append(
                f"{solution.id}: downgraded to not_established "
                "(no artifacts from this partition's scan)"
            )


def _repair_streams(
    topology: BusinessTopology,
    bundle: BusinessTopologyEvidenceBundle,
    notes: list[str],
    *,
    max_stream_artifacts: int,
) -> None:
    """Backfill stream artifacts from cited directional evidence; drop the rest."""

    system_by_id = {item.id: item for item in topology.systems}
    kept = []
    for stream in topology.data_streams:
        directional_ids = {
            item
            for item in stream.evidence_ids
            if bundle.evidence_types.get(item) in _DIRECTIONAL_EVIDENCE_TYPES
        }
        if not directional_ids:
            notes.append(f"{stream.id}: dropped (no directional scanner evidence)")
            continue
        direction_artifacts = _directional_artifact_ids(directional_ids, bundle)
        if not direction_artifacts:
            notes.append(
                f"{stream.id}: dropped (directional evidence resolves to no artifact "
                "in this partition)"
            )
            continue
        if not stream.artifact_ids:
            stream.artifact_ids = sorted(direction_artifacts)[:max_stream_artifacts]
            notes.append(
                f"{stream.id}: backfilled {len(stream.artifact_ids)} artifact_ids "
                "from its cited directional evidence"
            )
        elif not set(stream.artifact_ids) & direction_artifacts:
            extra = sorted(direction_artifacts)[:max_stream_artifacts]
            stream.artifact_ids = [*stream.artifact_ids, *extra]
            notes.append(
                f"{stream.id}: added {len(extra)} directional-evidence artifact_ids "
                "(cited artifacts did not include the edge's own)"
            )
        source = system_by_id.get(stream.source_system_id)
        destination = system_by_id.get(stream.destination_system_id)
        if source is None or destination is None:
            notes.append(f"{stream.id}: dropped (unknown source/destination system)")
            continue
        if not _direction_matches_systems(
            evidence_ids=directional_ids,
            source_evidence_ids=set(source.evidence_ids),
            destination_evidence_ids=set(destination.evidence_ids),
            bundle=bundle,
        ):
            notes.append(
                f"{stream.id}: dropped (directional evidence does not establish "
                "its declared source/destination)"
            )
            continue
        kept.append(stream)
    topology.data_streams = kept


def _repair_delivery_paths(
    topology: BusinessTopology,
    bundle: BusinessTopologyEvidenceBundle,
    notes: list[str],
) -> None:
    """Keep only delivery paths whose stream survived and whose segments ground."""

    stream_by_id = {item.id: item for item in topology.data_streams}
    system_by_id = {item.id: item for item in topology.systems}
    solution_by_id = {item.id: item for item in topology.integration_solutions}
    kept = []
    for path in topology.delivery_paths:
        stream = stream_by_id.get(path.stream_id)
        if stream is None:
            notes.append(f"{path.id}: dropped (its stream was dropped or unknown)")
            continue
        sequences = [segment.sequence for segment in path.segments]
        segments_ok = bool(path.segments) and sequences == list(
            range(1, len(sequences) + 1)
        )
        internal_refs = {
            ref
            for segment in path.segments
            for ref in (segment.source_ref, segment.destination_ref)
            if ref.startswith("step:")
        }
        if path.segments:
            segments_ok = segments_ok and path.segments[0].source_ref == stream.source_system_id
            segments_ok = segments_ok and path.segments[-1].destination_ref == stream.destination_system_id
            segments_ok = segments_ok and all(
                previous.destination_ref == current.source_ref
                for previous, current in zip(path.segments, path.segments[1:])
            )
        for segment in path.segments:
            directional_ids = _evidence_of_type(
                segment.evidence_ids, bundle, _DIRECTIONAL_EVIDENCE_TYPES
            )
            if not directional_ids:
                segments_ok = False
                break
            direction_artifacts = _directional_artifact_ids(directional_ids, bundle)
            if not direction_artifacts or not (
                set(stream.artifact_ids) & direction_artifacts
            ):
                segments_ok = False
                break
            source_claim = system_by_id.get(segment.source_ref) or solution_by_id.get(
                segment.source_ref
            )
            destination_claim = system_by_id.get(
                segment.destination_ref
            ) or solution_by_id.get(segment.destination_ref)
            if source_claim is None and segment.source_ref not in internal_refs:
                segments_ok = False
                break
            if destination_claim is None and segment.destination_ref not in internal_refs:
                segments_ok = False
                break
            if source_claim is None or destination_claim is None:
                continue
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
                segments_ok = False
                break
        if not segments_ok:
            notes.append(f"{path.id}: dropped (a segment could not be grounded)")
            continue
        kept.append(path)
    topology.delivery_paths = kept

    kept_stream_ids = {item.id for item in topology.data_streams}
    kept_solution_ids = {item.id for item in topology.integration_solutions}
    topology.transformations = [
        transformation
        for transformation in topology.transformations
        if transformation.stream_ids
        and all(ref in kept_stream_ids for ref in transformation.stream_ids)
        and all(
            ref in kept_solution_ids
            for ref in transformation.integration_solution_ids
        )
        and all(
            artifact_id in bundle.artifact_ids
            for artifact_id in transformation.artifact_ids
        )
    ]


def _prune_dangling_references(topology: BusinessTopology, notes: list[str]) -> None:
    """After drops, remove references to IDs that no longer exist."""

    stream_ids = {item.id for item in topology.data_streams}
    path_ids = {item.id for item in topology.delivery_paths}
    object_ids = {item.id for item in topology.business_objects}
    solution_ids = {item.id for item in topology.integration_solutions}
    control_ids = {item.id for item in topology.controls}
    risk_ids = {item.id for item in topology.risks}
    unknown_ids = {item.id for item in topology.unknowns}
    observation_ids = {item.id for item in topology.runtime_observations}
    conflict_ids = {item.id for item in topology.conflicts}

    path_by_id = {item.id: item for item in topology.delivery_paths}
    for stream in topology.data_streams:
        stream.business_object_ids = [r for r in stream.business_object_ids if r in object_ids]
        stream.integration_solution_ids = [
            r for r in stream.integration_solution_ids if r in solution_ids
        ]
        stream.control_ids = [r for r in stream.control_ids if r in control_ids]
        stream.risk_ids = [r for r in stream.risk_ids if r in risk_ids]
        stream.unknown_ids = [r for r in stream.unknown_ids if r in unknown_ids]
        stream.runtime_observation_ids = [
            r for r in stream.runtime_observation_ids if r in observation_ids
        ]
        stream.conflict_ids = [r for r in stream.conflict_ids if r in conflict_ids]
        if stream.delivery_path_id and (
            stream.delivery_path_id not in path_ids
            or path_by_id[stream.delivery_path_id].stream_id != stream.id
        ):
            stream.delivery_path_id = None

    for collection in (topology.controls, topology.risks, topology.unknowns):
        for claim in collection:
            claim.stream_ids = [r for r in claim.stream_ids if r in stream_ids]

    kept_observations = []
    for observation in topology.runtime_observations:
        observation.stream_ids = [r for r in observation.stream_ids if r in stream_ids]
        observation.delivery_path_ids = [
            r for r in observation.delivery_path_ids if r in path_ids
        ]
        if observation.stream_ids or observation.delivery_path_ids:
            kept_observations.append(observation)
        else:
            notes.append(f"{observation.id}: dropped (annotated only dropped streams/paths)")
    topology.runtime_observations = kept_observations

    claim_ids = {claim.id for claim in _all_claims(topology)}
    kept_conflicts = []
    for conflict in topology.conflicts:
        conflict.subject_refs = [
            r for r in conflict.subject_refs if r in claim_ids and r != conflict.id
        ]
        if conflict.subject_refs:
            kept_conflicts.append(conflict)
        else:
            notes.append(f"{conflict.id}: dropped (all subject_refs were dropped)")
    topology.conflicts = kept_conflicts


def repair_business_topology(
    topology: BusinessTopology,
    *,
    bundle: BusinessTopologyEvidenceBundle,
    max_stream_artifacts: int = 100,
) -> tuple[BusinessTopology, list[str]]:
    """Apply the verified-shape repairs and return (repaired copy, notes).

    The result is NOT guaranteed to validate; callers must re-run
    ``validate_business_topology`` and only accept a passing result. Repairs
    only drop, downgrade, or backfill from evidence the model already cited --
    they never invent identities, directions, or citations.
    """

    repaired = topology.model_copy(deep=True)
    notes: list[str] = []
    _drop_unknown_citations(repaired, bundle, notes)
    _downgrade_unsupported_claims(repaired, bundle, notes)
    _repair_streams(repaired, bundle, notes, max_stream_artifacts=max_stream_artifacts)
    _repair_delivery_paths(repaired, bundle, notes)
    _prune_dangling_references(repaired, notes)
    if notes:
        summary = (
            f"Deterministic post-synthesis repair applied {len(notes)} correction(s); "
            "dropped or downgraded claims are listed in the run warnings."
        )
        repaired.coverage.notes = [*repaired.coverage.notes, summary]
    return repaired, notes


def parse_validate_repair_business_topology(
    text: str,
    *,
    bundle: BusinessTopologyEvidenceBundle,
    project_id: str,
) -> tuple[BusinessTopology | None, BusinessTopologyValidationReport, list[str]]:
    """Parse + validate, then attempt the targeted deterministic repair.

    Returns ``(topology, report, repair_notes)``:

    * raw output valid -> the raw topology, its passing report, no notes;
    * raw invalid but repairable -> the repaired topology, a passing report
      whose warnings record the raw errors and repair count, and the notes;
    * unparseable or unrepairable -> ``(None, raw failing report, [])`` so the
      RAW validation errors drive the bounded LLM retry feedback.
    """

    raw_topology, parse_failure = parse_business_topology(
        text, bundle=bundle, project_id=project_id
    )
    if raw_topology is None:
        assert parse_failure is not None
        return None, parse_failure, []
    report = validate_business_topology(
        raw_topology, bundle=bundle, project_id=project_id
    )
    if report.validated:
        return raw_topology, report, []
    repaired, notes = repair_business_topology(raw_topology, bundle=bundle)
    repaired_report = validate_business_topology(
        repaired, bundle=bundle, project_id=project_id
    )
    if not repaired_report.validated or not repaired.data_streams:
        return None, report, []
    repaired_report.warnings = [
        f"accepted after deterministic repair ({len(notes)} corrections); "
        f"raw output had {len(report.errors)} validation error(s)",
        *[f"repaired: {note}" for note in notes[:20]],
        *repaired_report.warnings,
    ]
    return repaired, repaired_report, notes
