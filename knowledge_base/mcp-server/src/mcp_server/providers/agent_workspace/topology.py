"""Compile the agent's plain-language topology into ``business-topology-v1``.

The agent writes ``output/topology.json`` in business language -- system names,
flow endpoints, a business object, a mechanism, plain design names, a
confidence WORD, a human-style document citation. No internal ids, no
``ClaimStatus`` enum, no 20-char ``artifact:`` hash, no coverage arithmetic.
Nexus mints the ids, resolves the artifacts, picks the status, merges the
scanner floor and validates.

That split is not new here: it is exactly Stage A / Stage B of the existing
doc-grounded lane (:mod:`services.business_topology.flow_extraction` /
``assembly``), with the model call replaced by a file the agent already wrote.
Reusing the parser is the point -- it is lenient per ITEM and strict per FIELD,
so one malformed flow costs that flow and nothing else.

**Topology never blocks documentation.** Every failure path in this module ends
in a written ``validation_report.json`` and a returned outcome, never an
exception reaching the caller. The predecessor
(``providers/goal_driven/business_topology.py:93-102``) raised ``ValueError``
on a failed validation, which turned an advisory overlay into a run failure.

**The dashboard hard-gates on the pair.** ``landscapeTransform.ts:258`` renders
nothing unless ``topology_state.validated``, and
``api/routes/projects.py::_load_published_business_topology`` requires both
``_topology/business_topology.json`` and a ``validation_report.json`` saying
``validated: true``. So the scanner-floor fallback has to produce that pair
too, not merely avoid crashing -- an unproven fallback would take the landscape
view dark for every project documented in this lane, which is worse than the
overlay it replaces.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from mcp_server.providers.agent_workspace import contract
from mcp_server.providers.agent_workspace.contract import WorkspaceUnavailableError

logger = logging.getLogger(__name__)


class TopologyUnavailableError(WorkspaceUnavailableError):
    """``_topology/`` cannot even be created, so no outcome can be recorded.

    Carries ``stage="topology"`` so an escape is attributable rather than
    arriving at the operator as a bare "Generation failed" -- the runner has
    one ``except WorkspaceUnavailableError`` and an unrelated exception type
    walks straight past it.

    :func:`compile_agent_topology` catches this itself: the overlay is
    advisory, and a documentation publication that already passed every safety
    check must not be lost because a directory could not be made.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, stage="topology")


@dataclass(frozen=True)
class TopologyOutcome:
    """Whether an overlay was published, and by which lane."""

    available: bool
    validated: bool
    lane: str
    systems: int = 0
    data_streams: int = 0
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def compile_agent_topology(
    *,
    project_id: str,
    staging_dir: Path,
    scan_output_dir: Path,
    document_names: tuple[str, ...] = (),
    model_name: str = "agent_workspace",
    subproject: str = "",
) -> TopologyOutcome:
    """Build and publish ``_topology/`` from the agent's ``output/topology.json``.

    ``document_names`` are the paths the publisher accepted, RELATIVE TO
    ``output/`` -- which is also where they still are when this runs, so they
    double as the documentation-evidence tier without waiting for the copy to
    the staging root. Passing them explicitly is what makes nested documents
    (``integrations/orders.md``) citable: ``discover_doc_filenames`` only looks
    at a directory's top level. EVERY accepted document is registered; see
    :func:`_evidence_documents`.

    Never raises. The directory setup used to sit OUTSIDE the guard below, so
    an ``OSError`` from ``mkdir`` escaped as an unrelated type, walked past the
    runner's single ``except WorkspaceUnavailableError``, and lost a
    publication that had already passed every safety check.
    """

    staging_dir = Path(staging_dir)
    topology_dir = staging_dir / "_topology"
    topology_path = topology_dir / "business_topology.json"
    report_path = topology_dir / "validation_report.json"
    state_path = topology_dir / "state.json"

    bundle = None
    try:
        output_dir = staging_dir / contract.TOPOLOGY_FILE.parent
        _prepare_topology_dir(topology_dir)
        from mcp_server.services.business_topology import (
            build_business_topology_evidence,
            build_deterministic_topology,
            register_doc_evidence,
            validate_business_topology,
        )

        bundle = build_business_topology_evidence(
            project_id=project_id,
            scan_output_dir=Path(scan_output_dir),
            staging_dir=staging_dir,
        )
        deterministic = build_deterministic_topology(bundle, project_id=project_id)
        deterministic_report = validate_business_topology(
            deterministic, bundle=bundle, project_id=project_id
        )

        topology = None
        report = None
        active_bundle = bundle
        lane = "deterministic"
        notes: list[str] = []

        doc_bundle, doc_records = register_doc_evidence(
            bundle,
            output_dir,
            filenames=_evidence_documents(document_names),
        )

        model_extraction, model_notes = _extraction_from_integration_model(
            staging_dir,
            subproject=subproject or project_id,
        )
        notes.extend(model_notes)
        if model_extraction is not None and model_extraction.flows:
            topology, report, model_assembly_notes = _assemble_extraction(
                model_extraction,
                bundle=doc_bundle,
                deterministic=deterministic if deterministic_report.validated else None,
                project_id=project_id,
                subproject=subproject,
                model_name=model_name,
                doc_records=doc_records,
            )
            notes.extend(model_assembly_notes)
            if topology is not None:
                lane = "integration_model"
                active_bundle = doc_bundle

        agent_file = staging_dir / contract.TOPOLOGY_FILE
        if topology is None and agent_file.is_file():
            topology, report, agent_notes = _assemble_from_agent_file(
                agent_file,
                bundle=doc_bundle,
                deterministic=deterministic if deterministic_report.validated else None,
                project_id=project_id,
                subproject=subproject,
                model_name=model_name,
                doc_records=doc_records,
            )
            notes.extend(agent_notes)
            if topology is not None:
                lane = "agent_topology"
                active_bundle = doc_bundle
        elif topology is None:
            notes.append("the agent published no output/topology.json")

        if topology is None or not topology.data_streams:
            if not deterministic_report.validated or not deterministic.data_streams:
                errors = tuple(
                    dict.fromkeys(
                        [
                            *(report.errors if report is not None else ()),
                            *deterministic_report.errors,
                            "no publishable business topology was produced",
                        ]
                    )
                )
                return _publish_absent(
                    topology_path,
                    report_path,
                    state_path,
                    scan_fingerprint=bundle.scan_fingerprint,
                    project_id=project_id,
                    errors=errors,
                    warnings=tuple(notes),
                )
            topology, report = deterministic, deterministic_report
            active_bundle = bundle
            lane = "deterministic"

        # Re-validate the exact object that will be persisted, so the report
        # describes what is served rather than an earlier revision of it.
        report = validate_business_topology(topology, bundle=active_bundle, project_id=project_id)
        if notes:
            report.warnings = [*[f"assembly: {note}" for note in notes[:10]], *report.warnings]
        report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        if not report.validated:
            topology_path.unlink(missing_ok=True)
            _write_state(
                state_path,
                available=False,
                validated=False,
                scan_fingerprint=bundle.scan_fingerprint,
                errors=list(report.errors),
                warnings=list(report.warnings),
            )
            return TopologyOutcome(
                available=False,
                validated=False,
                lane=lane,
                errors=tuple(report.errors[:5]),
                warnings=tuple(report.warnings[:5]),
            )

        topology_path.write_text(topology.model_dump_json(indent=2), encoding="utf-8")
        _write_state(
            state_path,
            available=True,
            validated=True,
            scan_fingerprint=bundle.scan_fingerprint,
            errors=[],
            warnings=list(report.warnings),
            lane=lane,
        )
        return TopologyOutcome(
            available=True,
            validated=True,
            lane=lane,
            systems=len(topology.systems),
            data_streams=len(topology.data_streams),
            warnings=tuple(report.warnings[:5]),
        )
    except Exception as exc:
        logger.warning("Agent topology compilation failed for %s", project_id, exc_info=True)
        return _publish_absent(
            topology_path,
            report_path,
            state_path,
            scan_fingerprint=getattr(bundle, "scan_fingerprint", ""),
            project_id=project_id,
            errors=(f"{type(exc).__name__}: {exc}",),
            warnings=("Documentation publication continued without a topology overlay",),
        )


def _prepare_topology_dir(topology_dir: Path) -> None:
    """Create ``_topology/``, or fail with a STAGED error.

    Separated so the failure has a name and a stage. Called inside the guard;
    the caller converts it into an outcome, because topology is advisory.
    """

    try:
        topology_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TopologyUnavailableError(
            f"the topology directory could not be created ({type(exc).__name__})"
        ) from exc


def _evidence_documents(document_names: tuple[str, ...]) -> tuple[str, ...] | None:
    """EVERY accepted document becomes citable evidence. No ceiling.

    There used to be one: ``_MAX_EVIDENCE_DOCUMENTS = 6``, mirroring
    ``doc_evidence.DEFAULT_DOC_EVIDENCE_LIMIT``. That limit is correct WHERE IT
    LIVES -- the legacy Stage A pushes the documents it registers into a model
    prompt, so admitting more costs tokens on every call. Nothing of the kind
    happens here: this lane compiles a topology from files the agent has
    already written, with no model call anywhere in the path, and
    ``collect_doc_evidence`` only reads each file and mints section ids.

    So the ceiling bought nothing and cost a run its links. A run that
    publishes ten documents had four of them silently unregistered, and any
    journey whose only citation was one of those four was pruned for citing
    evidence that "does not exist" -- deterministically the four sorting last
    by filename. The replacement is the explicit reference the model now
    carries: a journey names the documents that explain it
    (``Journey.document_paths``) and every one of them resolves.

    ``None`` lets ``collect_doc_evidence`` fall back to its own ranked
    discovery, which is the right behaviour when the caller passed nothing.
    """

    if not document_names:
        return None
    return tuple(sorted(dict.fromkeys(document_names)))


def _extraction_from_integration_model(
    staging_dir: Path,
    *,
    subproject: str,
):
    """Translate the shared knowledge model into business-level flows.

    The translation is deliberately tolerant. An absent, incomplete, or
    malformed working model remains an advisory topology gap and never blocks
    documentation. Exact IDs and provenance are still minted and checked by
    the existing topology assembler.

    Takes the STAGING DIRECTORY, not one file. The model lives in two shapes
    -- the original ``notes/integration_model.json`` and the per-integration
    ``notes/integrations/*.json`` the agent is now taught to write -- and this
    used to test the monolith with ``is_file()`` and give up. On a run that
    wrote only per-integration files that reported "the shared integration
    model is absent" while every integration sat in the directory beside it.
    """

    from mcp_server.providers.agent_workspace.integration_model import (
        IntegrationModelError,
        load_workspace_model,
    )
    from mcp_server.services.business_topology.flow_extraction import (
        ExtractedFlow,
        ExtractedSystem,
        FlowExtraction,
    )

    notes: list[str] = []
    try:
        report = load_workspace_model(staging_dir)
    except IntegrationModelError as exc:
        # Typed and already client-safe: it names the file and the defect, and
        # never the offending content.
        return None, [f"the shared integration model is unreadable: {exc}"]
    except Exception as exc:  # noqa: BLE001 - working state is advisory
        return None, [f"the shared integration model is unreadable: {type(exc).__name__}"]
    if report.rejected:
        # LOUD, not silent. A dropped record used to vanish with no trace
        # anywhere an operator looks.
        notes.append(
            f"the shared integration model dropped {len(report.rejected)} unusable record(s): "
            + "; ".join(item.describe() for item in report.rejected[:3])
        )
    if report.salvaged:
        # The model did not parse whole and was recovered in part. Said out
        # loud for the same reason a dropped record is: a topology built from
        # a salvaged model has a hole in it whose size nobody knows.
        notes.append(report.salvaged)
    knowledge = report.knowledge
    if not knowledge.integrations:
        return None, [*notes, "the shared integration model contains no integrations"]

    systems: dict[str, ExtractedSystem] = {}
    flows: list[ExtractedFlow] = []

    for integration in knowledge.integrations:
        for participant in integration.participants:
            systems.setdefault(
                participant.id,
                ExtractedSystem(
                    name=participant.label,
                    role=participant.role or "",
                    kind="application",
                ),
            )
        for journey in integration.journeys:
            flows.extend(_flows_for_journey(journey, integration, notes))

    extraction = FlowExtraction(
        subproject=subproject,
        systems=list(systems.values()),
        flows=flows,
    )
    if not flows:
        notes.append("the shared integration model contains no topology-ready journeys")
    return extraction, notes


#: Journey state -> the confidence WORD the business-topology extraction uses.
_JOURNEY_CONFIDENCE = {
    "exact": "high",
    "partial": "medium",
    "dynamic": "medium",
    "unresolved": "low",
    "unsupported": "low",
}


#: How many distinct paths one journey may contribute. A decision step with
#: several conditions multiplies, and an advisory overlay must not become the
#: expensive part of a run. Hitting it is recorded, never silent.
_MAX_JOURNEY_PATHS = 12


def _flows_for_journey(journey, integration, notes: list[str]) -> list:
    """Translate ONE ordered journey into business-level flows.

    One flow per PATH through the journey's steps, which is what makes fan-out
    survive the translation: a journey that forks into two arms describes two
    destinations, and the predecessor -- which had only ``participant_ids`` to
    work with -- published a single edge from ``participant_ids[0]`` to
    ``participant_ids[-1]``, i.e. one arbitrary arm of the fork with the other
    silently gone.

    The step order is the technical path, and a step's own mappings become the
    transformation attached to THAT step rather than one undifferentiated
    "journey transformation".
    """

    from mcp_server.services.business_topology.flow_extraction import (
        ExtractedFlow,
        ExtractedPathStep,
        ExtractedTransformation,
        ExtractedTransformationMapping,
    )

    participants = {record.id: record for record in integration.participants}
    interfaces = {record.id: record for record in integration.interfaces}
    units = {record.id: record for record in integration.implementing_units}
    mappings = {record.id: record for record in integration.mappings}
    citation = _journey_citation(journey, integration)
    business_object = integration.capability or integration.label
    carries = journey.purpose or integration.summary or ""
    confidence = _JOURNEY_CONFIDENCE[journey.state]

    paths, truncated = _journey_paths(journey)
    if truncated:
        notes.append(
            f"journey '{journey.label}' has more than {_MAX_JOURNEY_PATHS} distinct paths; "
            "the overlay carries the first of them"
        )
    flows = []
    used_names: set[str] = set()
    for steps in paths or [[]]:
        source_id, target_id = _path_endpoints(journey, steps)
        source = participants.get(source_id or "")
        target = participants.get(target_id or "")
        if source is None or target is None:
            # An unresolvable endpoint is recorded, never guessed: the whole
            # point of a step's nullable participant is that "we could not name
            # this end" is a real answer.
            notes.append(
                f"journey '{journey.label}' has no resolvable source and target participants"
            )
            continue

        design_names: list[str] = []
        path_steps = []
        transformations = []
        mechanism = ""
        for step in steps:
            step_interfaces = [
                interfaces[interface_id]
                for interface_id in step.interface_ids
                if interface_id in interfaces
            ]
            step_designs = [units[unit_id].label for unit_id in step.unit_ids if unit_id in units]
            design_names.extend(name for name in step_designs if name not in design_names)
            step_mechanism = (
                step.mechanism
                or (step_interfaces[0].interface_kind if step_interfaces else "")
                or ""
            )
            mechanism = mechanism or step_mechanism
            path_steps.append(
                ExtractedPathStep(
                    name=_step_name(step, step_interfaces, step_designs),
                    description=step.summary or "Step established by the investigation.",
                    mechanism=step_mechanism,
                    component=step_interfaces[0].label if step_interfaces else "",
                    resource="; ".join(step.sql_refs),
                    implementing_designs=step_designs,
                )
            )
            rows = [
                ExtractedTransformationMapping(
                    source=mappings[mapping_id].source_ref,
                    target=mappings[mapping_id].target_ref,
                    mapping_type=(
                        "derived" if mappings[mapping_id].transformation_summary else "direct"
                    ),
                )
                for mapping_id in step.mapping_ids
                if mapping_id in mappings
            ]
            if rows:
                transformations.append(
                    ExtractedTransformation(
                        name=_step_name(step, step_interfaces, step_designs) or journey.label,
                        description=(
                            step.summary
                            or "Business-significant mappings applied at this step."
                        ),
                        implementing_designs=step_designs,
                        mappings=rows,
                    )
                )
        name = journey.label
        if len(paths) > 1:
            name = f"{journey.label} ({source.label} -> {target.label})"
            suffix = 2
            while name in used_names:
                name = f"{journey.label} ({source.label} -> {target.label}) {suffix}"
                suffix += 1
        used_names.add(name)
        flows.append(
            ExtractedFlow(
                name=name,
                source_system=source.label,
                target_system=target.label,
                business_object=business_object,
                carries=carries,
                mechanism=mechanism,
                implementing_designs=design_names,
                technical_path=path_steps,
                transformations=transformations,
                confidence=confidence,
                evidence=citation,
            )
        )
    return flows


def _journey_paths(journey) -> tuple[list[list], bool]:
    """Every maximal path through a journey's steps, deterministically.

    Two kinds of edge, and the interaction between them is the whole subtlety:

    * an EXPLICIT transition, which is how a fork and a merge are expressed;
    * an IMPLICIT edge from one step to the next by sequence, which is what a
      linear journey relies on -- and which must NOT fire into a step that some
      transition already targets, or the second arm of a fork would be read as
      running after the first.

    Returns ``(paths, truncated)``. A journey with no steps yields no path and
    the caller falls back to the journey's own endpoints.
    """

    ordered = journey.ordered_steps
    if not ordered:
        return [], False
    explicit_targets = {
        target_id for step in ordered for target_id in step.target_step_ids
    }
    by_id = {step.id: step for step in ordered}
    implicit: dict[str, str] = {}
    for current, following in zip(ordered, ordered[1:]):
        if current.transitions or following.id in explicit_targets:
            continue
        implicit[current.id] = following.id

    def successors(step) -> list[str]:
        if step.transitions:
            return [target for target in step.target_step_ids if target in by_id]
        following = implicit.get(step.id)
        return [following] if following else []

    reached = explicit_targets | set(implicit.values())
    entries = [step for step in ordered if step.id not in reached] or [ordered[0]]

    paths: list[list] = []
    truncated = False
    for entry in entries:
        stack: list[tuple[list, set[str]]] = [([entry], {entry.id})]
        while stack:
            path, seen = stack.pop()
            following = [
                by_id[target] for target in successors(path[-1]) if target not in seen
            ]
            if not following:
                paths.append(path)
                if len(paths) >= _MAX_JOURNEY_PATHS:
                    return paths, True
                continue
            # Reversed so the declared transition order survives the stack.
            for step in reversed(following):
                stack.append(([*path, step], seen | {step.id}))
    return paths, truncated


def _path_endpoints(journey, steps) -> tuple[str | None, str | None]:
    """First established source and last established target on this path."""

    source = next(
        (step.source_participant_id for step in steps if step.source_participant_id), None
    )
    target = next(
        (step.target_participant_id for step in reversed(steps) if step.target_participant_id),
        None,
    )
    if source is None or target is None:
        # A journey whose path is not yet established still has endpoints.
        journey_source, journey_target = journey.endpoints
        source = source or journey_source
        target = target or journey_target
    return source, target


def _step_name(step, step_interfaces: list, design_names: list[str]) -> str:
    """A readable name for one step, from what the step actually has."""

    if step.label:
        return step.label
    if design_names:
        return ", ".join(design_names)
    if step_interfaces:
        return step_interfaces[0].label
    if step.kind and step.kind != "unknown":
        return step.kind.replace("_", " ")
    return ""


def _journey_citation(journey, integration) -> str:
    """The client documents that explain this journey, as one citation string.

    ``resolve_doc_citation`` matches registered documents by containment, so a
    citation naming several documents resolves to one evidence id per
    document -- which is exactly what "a journey may be covered by several
    dossiers" needs, and what the removed six-document ceiling used to make
    impossible. The integration's own documents are the fallback, and its
    label the last resort (any non-empty citation still resolves to the
    best-ranked registered document rather than dropping the flow).
    """

    paths = list(journey.document_paths) or list(integration.document_paths)
    return "; ".join(paths) if paths else integration.label


def _assemble_from_agent_file(
    agent_file: Path,
    *,
    bundle,
    deterministic,
    project_id: str,
    subproject: str,
    model_name: str,
    doc_records: dict,
):
    """Parse, assemble, merge the scanner floor, prune. Returns the triple.

    ``(topology, report, notes)`` with ``topology is None`` whenever nothing
    publishable came out -- an absent file, unparseable JSON, no usable flow,
    or a validation failure that pruning could not localise to a claim.
    """

    from mcp_server.services.business_topology import parse_flow_extraction

    notes: list[str] = []
    try:
        text = agent_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, None, [f"output/topology.json is unreadable: {type(exc).__name__}"]

    extraction, dropped = parse_flow_extraction(text, subproject=subproject or project_id)
    notes.extend(dropped)
    if extraction is None or not extraction.flows:
        notes.append("output/topology.json contained no usable flow")
        return None, None, notes

    topology, report, assembly_notes = _assemble_extraction(
        extraction,
        bundle=bundle,
        deterministic=deterministic,
        project_id=project_id,
        subproject=subproject,
        model_name=model_name,
        doc_records=doc_records,
    )
    notes.extend(assembly_notes)
    return topology, report, notes


def _assemble_extraction(
    extraction,
    *,
    bundle,
    deterministic,
    project_id: str,
    subproject: str,
    model_name: str,
    doc_records: dict,
):
    """Ground, merge and locally prune one business-level extraction."""

    from mcp_server.services.business_topology import prune_to_valid
    from mcp_server.services.business_topology.assembly import (
        assemble_topology,
        merge_scanner_floor,
    )

    notes: list[str] = []
    topology, assembly_notes = assemble_topology(
        extraction,
        bundle=bundle,
        project_id=project_id,
        doc_records=doc_records,
        subproject=subproject,
        model_name=model_name,
    )
    notes.extend(assembly_notes)
    if deterministic is not None:
        notes.extend(merge_scanner_floor(topology, deterministic))
    # Pruning runs LAST, after the floor merge, so a defect introduced by
    # either source costs only its own claim.
    topology, report, pruned = prune_to_valid(topology, bundle=bundle, project_id=project_id)
    if pruned:
        notes.append(
            f"{len(pruned)} claim(s) failed provenance validation and were dropped "
            f"(e.g. {pruned[0]}); the remainder published"
        )
    if not report.validated:
        notes.extend(report.errors[:3])
        return None, report, notes
    return topology, report, notes


def _publish_absent(
    topology_path: Path,
    report_path: Path,
    state_path: Path,
    *,
    scan_fingerprint: str,
    project_id: str,
    errors: tuple[str, ...],
    warnings: tuple[str, ...] = (),
) -> TopologyOutcome:
    """Record an honest "no overlay" state; never leave a stale one behind."""

    payload = {
        "validated": False,
        "scan_fingerprint": scan_fingerprint,
        "project_id": project_id,
        "errors": list(errors),
        "warnings": list(warnings),
        "evidence_count": 0,
        "cited_evidence_count": 0,
        "artifact_count": 0,
        "cited_artifact_count": 0,
    }
    try:
        report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        topology_path.unlink(missing_ok=True)
        _write_state(
            state_path,
            available=False,
            validated=False,
            scan_fingerprint=scan_fingerprint,
            errors=list(errors),
            warnings=list(warnings),
        )
    except OSError:
        logger.warning("Could not record the absent-topology state", exc_info=True)
    return TopologyOutcome(
        available=False,
        validated=False,
        lane="none",
        errors=tuple(errors[:5]),
        warnings=tuple(warnings[:5]),
    )


def _write_state(
    path: Path,
    *,
    available: bool,
    validated: bool,
    scan_fingerprint: str,
    errors: list[str],
    warnings: list[str] | None = None,
    lane: str = "",
) -> None:
    payload: dict[str, object] = {
        "available": available,
        "validated": validated,
        "scan_fingerprint": scan_fingerprint,
        "errors": errors,
        "warnings": warnings or [],
    }
    if lane:
        payload["lane"] = lane
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
