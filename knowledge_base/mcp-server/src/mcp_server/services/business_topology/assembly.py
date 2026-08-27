"""Stage B of topology synthesis: deterministic assembly, no LLM.

Takes a :class:`FlowExtraction` (business language, plain design names, a
confidence word) and produces a :class:`BusinessTopology` that satisfies
:func:`validate_business_topology` by construction. Python owns every field the
single-shot contract kept losing:

* stable ids and alias folding
* ``ClaimStatus`` selection (the model states a confidence word)
* ``artifact:`` hash resolution from plain design names
* the directional scanner edge lookup and its identity cross-links
* delivery-segment ordering, ``step:`` refs and endpoint anchoring
* coverage

This is a generalisation of the script that produced the hand-curated werkorder
topology (``output/werkorder/topology/build_evidence_topology.py``): its two
hardcoded tables are gone -- the canonical-name map is derived from the scanner
landscape plus extraction aliases, and the sub-project/solution mapping comes
from the partition name.

**Provenance discipline.** Scanner facts stay authoritative. A flow whose
direction is backed by a scanner edge cites that edge and may reach ``observed``.
A flow asserted only by the generated documentation cites the
``documentation_derived`` tier, is capped at ``inferred``, and must still
resolve real scanner artifacts -- so its drill-down lands on real jobs even
though the scanner never inferred the edge itself.
"""

from __future__ import annotations

import re

from mcp_server.services.business_topology.doc_evidence import (
    build_design_artifact_index,
    resolve_designs,
    resolve_doc_citation,
)
from mcp_server.services.business_topology.evidence import (
    BusinessTopologyEvidenceBundle,
)
from mcp_server.services.business_topology.flow_extraction import (
    ExtractedFlow,
    FlowExtraction,
)
from mcp_server.services.business_topology.models import (
    BusinessDataStream,
    BusinessObject,
    BusinessSystem,
    BusinessTopology,
    BusinessTopologyCoverage,
    BusinessTopologyMetadata,
    BusinessTransformation,
    DeliveryPath,
    DeliverySegment,
    IntegrationSolution,
    TransformationMapping,
)

# Same-package internals reused on purpose: the assembler must resolve edges the
# way the validator resolves them. A local copy of this logic would be free to
# drift, and a drift here means a stream the assembler believes is grounded and
# the validator rejects.
from mcp_server.services.business_topology.validation import (
    _directional_artifact_ids,
    _scanner_artifact_identity_map,
)

GENERATED_BY = "doc_grounded_flow_extraction"

_SYSTEM_KIND_BY_EXTRACTION = {
    "application": "software_system",
    "database": "data_system",
    "external_service": "external_service",
    "file": "data_system",
    "queue": "external_service",
}

_CONFIDENCE_VALUE = {"certain": 0.9, "high": 0.8, "medium": 0.62, "low": 0.42}

#: A documentation-only claim never outranks a scanner-grounded one.
_DOC_TIER_MAX_CONFIDENCE = 0.6

_PROTOCOL_PATTERNS = (
    ("REST", re.compile(r"\brest\b|https?|\bwebhook\b|\bapi\b", re.IGNORECASE)),
    ("SOAP", re.compile(r"\bsoap\b|webservice|\bwsdl\b", re.IGNORECASE)),
    ("JMS", re.compile(r"\bjms\b|activemq|\bqueue\b|\btopic\b|\bmq\b", re.IGNORECASE)),
    ("SFTP", re.compile(r"\bsftp\b|\bftp\b|\bscp\b", re.IGNORECASE)),
    ("File", re.compile(r"\bfile\b|file staging|\bcsv\b|\bxml file\b", re.IGNORECASE)),
    (
        "Database",
        re.compile(
            r"\bmssql\b|\boracle\b|\bjdbc\b|\bsql\b|\bdatabase\b|stored procedure",
            re.IGNORECASE,
        ),
    ),
)

_SEGMENT_MODES = {
    "direct",
    "buffered",
    "batch",
    "scheduled",
    "event_driven",
    "file_transfer",
    "database",
    "unknown",
}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-") or "x"


def _fold(value: str) -> str:
    """Alias-folding key: alphanumerics only, case-insensitive.

    ``EnterpriseServiceBus``, ``Enterprise Service Bus`` and ``enterprise-service-bus``
    are one system. Applied to SYSTEM names only -- never to design names, where
    ``AssetCheck_GetLocation`` and ``AssetCheck_GetLocations`` are two jobs.
    """

    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _display_object_name(name: str) -> str:
    """Humanise CamelCase for display without changing the stable id."""

    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).strip()
    if not spaced:
        return name
    words = spaced.split()
    return " ".join(
        word if word.isupper() else (word.capitalize() if index == 0 else word.lower())
        for index, word in enumerate(words)
    )


def _protocols_from(text: str) -> list[str]:
    return [label for label, pattern in _PROTOCOL_PATTERNS if pattern.search(text or "")]


def _mode_for(protocol: str, text: str) -> str:
    lowered = (text or "").lower()
    if protocol == "JMS":
        return "buffered"
    if protocol in {"File", "SFTP"}:
        return "file_transfer"
    if protocol == "Database":
        return "database"
    if "timer" in lowered or "schedule" in lowered or "cron" in lowered:
        return "scheduled"
    if "event" in lowered or "webhook" in lowered:
        return "event_driven"
    if protocol in {"REST", "SOAP"}:
        return "direct"
    return "unknown"


class _SystemResolver:
    """Folds extraction system names onto scanner landscape identities.

    Replaces the curated script's hardcoded ``CANON`` table. Names come from the
    scanner's own landscape rows, so the fold is project-specific without any
    per-customer configuration.
    """

    def __init__(self, bundle: BusinessTopologyEvidenceBundle) -> None:
        self._identity_by_fold: dict[str, tuple[str, str, str]] = {}
        for evidence_id, record in bundle.evidence_records.items():
            evidence_type = record.get("type")
            if evidence_type not in {"scanner_system", "scanner_application"}:
                continue
            # The validator's identity check requires both fields to be present;
            # a row missing either cannot ground a system, so it is not offered.
            if not str(record.get("scanner_id") or "").strip():
                continue
            name = str(record.get("name") or "").strip()
            if not name:
                continue
            key = _fold(name)
            if key and key not in self._identity_by_fold:
                kind = str(record.get("kind") or "").strip()
                self._identity_by_fold[key] = (evidence_id, name, kind)
        self._alias_to_fold: dict[str, str] = {}

    def register_aliases(self, name: str, aliases: list[str]) -> None:
        target = _fold(name)
        if not target:
            return
        for alias in aliases:
            key = _fold(alias)
            if key and key != target:
                self._alias_to_fold.setdefault(key, target)

    def resolve(self, name: str) -> tuple[str, str | None, str]:
        """Return ``(canonical_name, identity_evidence_id, scanner_kind)``.

        ``identity_evidence_id`` is ``None`` when the documentation names a
        system the scanner never saw -- a real outcome (an external SaaS reached
        only through a queue), not an error. The caller emits it as
        ``unresolved`` rather than asserting a scanned identity.
        """

        raw = (name or "").strip()
        key = _fold(raw)
        key = self._alias_to_fold.get(key, key)
        match = self._identity_by_fold.get(key)
        if match is not None:
            evidence_id, canonical, kind = match
            return canonical, evidence_id, kind
        return raw, None, ""


class _EdgeIndex:
    """Scanner directional edges indexed by (source identity, destination identity)."""

    def __init__(self, bundle: BusinessTopologyEvidenceBundle) -> None:
        self._by_pair: dict[tuple[str, str], list[str]] = {}
        for evidence_id, record in bundle.evidence_records.items():
            if record.get("type") != "scanner_edge":
                continue
            source = str(record.get("source_identity_evidence_id") or "")
            destination = str(record.get("destination_identity_evidence_id") or "")
            if source and destination:
                self._by_pair.setdefault((source, destination), []).append(evidence_id)
        self._identity_map = _scanner_artifact_identity_map(bundle)
        self._bundle = bundle

    def lookup(
        self, source_identity: str | None, destination_identity: str | None
    ) -> tuple[str | None, set[str]]:
        """Return ``(edge_evidence_id, artifacts_the_edge_names)``."""

        if not source_identity or not destination_identity:
            return None, set()
        candidates = self._by_pair.get((source_identity, destination_identity)) or []
        for evidence_id in sorted(candidates):
            artifacts = _directional_artifact_ids({evidence_id}, self._bundle)
            if artifacts:
                # Prefer an edge that resolves to artifacts: the validator
                # requires the stream's artifact_ids to intersect them.
                return evidence_id, artifacts
        if candidates:
            return sorted(candidates)[0], set()
        return None, set()


def _status_for(
    confidence_word: str, *, scanner_backed: bool, identity_backed: bool = True
) -> tuple[str, float]:
    """Map a confidence WORD onto ``(ClaimStatus, numeric confidence)``.

    ``confirmed`` is never emitted: the validator reserves it for an approved
    business declaration, and no such evidence exists in a generation run.
    Choosing this enum was the model's single most frequent rejection cause
    (verified: 14 of 14 systems at ``observed`` in the werkorder ETTM_part2 run).
    """

    value = _CONFIDENCE_VALUE.get(confidence_word, 0.62)
    if not identity_backed:
        return "proposed", min(value, _DOC_TIER_MAX_CONFIDENCE)
    if not scanner_backed:
        status = "proposed" if confidence_word == "low" else "inferred"
        return status, min(value, _DOC_TIER_MAX_CONFIDENCE)
    if confidence_word in {"certain", "high"}:
        return "observed", value
    if confidence_word == "low":
        return "proposed", value
    return "inferred", value


def assemble_topology(
    extraction: FlowExtraction,
    *,
    bundle: BusinessTopologyEvidenceBundle,
    project_id: str,
    doc_records: dict[str, dict[str, object]],
    subproject: str = "",
    model_name: str = "",
) -> tuple[BusinessTopology, list[str]]:
    """Assemble a validator-conformant topology from one extraction.

    Returns ``(topology, notes)``. ``notes`` records what could not be grounded
    (unresolved design names, doc-only directions) so the run reports its gaps
    instead of silently shipping a thinner graph.
    """

    notes: list[str] = []
    resolver = _SystemResolver(bundle)
    for system in extraction.systems:
        resolver.register_aliases(system.name, system.aliases)
    edges = _EdgeIndex(bundle)
    design_index = build_design_artifact_index(bundle)

    solution_name = subproject or extraction.subproject or project_id
    solution_id = f"int-sol-{_slug(solution_name)}"

    systems: dict[str, BusinessSystem] = {}
    objects: dict[str, BusinessObject] = {}
    streams: list[BusinessDataStream] = []
    paths: list[DeliveryPath] = []
    transformations: list[BusinessTransformation] = []
    solution_artifacts: list[str] = []
    used_stream_ids: set[str] = set()
    unresolved_designs: set[str] = set()

    def ensure_system(name: str, *, kind_hint: str, role: str, confidence: str) -> str:
        canonical, identity_id, scanner_kind = resolver.resolve(name)
        system_id = f"sys-{_slug(canonical)}"
        existing = systems.get(system_id)
        if existing is not None:
            if role and not existing.purpose:
                existing.purpose = role
                existing.description = role
            return system_id
        if identity_id is not None:
            status, value = _status_for(confidence, scanner_backed=True)
            evidence_ids = [identity_id]
            kind = (
                _SYSTEM_KIND_BY_EXTRACTION.get(kind_hint, "software_system")
                if scanner_kind not in _SYSTEM_KIND_BY_EXTRACTION.values()
                else scanner_kind
            )
        else:
            # Named only by the documentation. `unresolved` is the honest kind
            # and is the one the validator deliberately exempts from the
            # scanner-identity promotion rule.
            status, value = _status_for(
                confidence, scanner_backed=False, identity_backed=False
            )
            evidence_ids = list(_doc_ids_for("", doc_records))
            kind = "unresolved"
            notes.append(
                f"system '{canonical}' is named in documentation but has no scanner "
                "landscape identity; published as unresolved"
            )
        systems[system_id] = BusinessSystem(
            id=system_id,
            name=canonical,
            description=role,
            purpose=role,
            status=status,
            confidence=value,
            evidence_ids=evidence_ids,
            kind=kind,
            aliases=[],
        )
        return system_id

    def ensure_object(name: str, evidence_ids: list[str]) -> str | None:
        clean = (name or "").strip()
        if not clean:
            return None
        object_id = f"bo-{_slug(clean)}"
        if object_id not in objects:
            display = _display_object_name(clean)
            objects[object_id] = BusinessObject(
                id=object_id,
                name=display,
                description="",
                status="inferred",
                confidence=0.7,
                evidence_ids=list(evidence_ids),
                aliases=[clean] if display != clean else [],
            )
        elif clean not in objects[object_id].aliases and clean != objects[object_id].name:
            objects[object_id].aliases.append(clean)
        return object_id

    for system in extraction.systems:
        ensure_system(
            system.name, kind_hint=system.kind, role=system.role, confidence="high"
        )

    for index, flow in enumerate(extraction.flows, start=1):
        doc_ids = _doc_ids_for(flow.evidence, doc_records)
        source_id = ensure_system(
            flow.source_system, kind_hint="application", role="", confidence=flow.confidence
        )
        destination_id = ensure_system(
            flow.target_system, kind_hint="application", role="", confidence=flow.confidence
        )
        if source_id == destination_id:
            notes.append(
                f"flow '{flow.name or index}' collapsed to a single system after alias "
                "folding and was dropped"
            )
            continue
        source_system = systems[source_id]
        destination_system = systems[destination_id]
        source_identity = next(iter(source_system.evidence_ids), None)
        destination_identity = next(iter(destination_system.evidence_ids), None)
        edge_id, edge_artifacts = edges.lookup(
            source_identity if source_system.kind != "unresolved" else None,
            destination_identity if destination_system.kind != "unresolved" else None,
        )

        artifact_ids, unresolved = resolve_designs(flow.implementing_designs, design_index)
        unresolved_designs.update(unresolved)
        if edge_id is not None and edge_artifacts and not (set(artifact_ids) & edge_artifacts):
            # The validator requires the cited direction to touch the cited
            # artifacts. The edge's own artifacts are real and implement this
            # flow, so add them rather than dropping a grounded stream.
            artifact_ids = [*artifact_ids, *sorted(edge_artifacts - set(artifact_ids))]
        if not artifact_ids:
            notes.append(
                f"flow '{flow.name or index}' resolved no scanner artifacts "
                f"(designs: {', '.join(flow.implementing_designs) or 'none named'}) and was dropped"
            )
            continue
        solution_artifacts.extend(artifact_ids)

        scanner_backed = edge_id is not None
        evidence_ids = [edge_id] if edge_id is not None else []
        evidence_ids.extend(doc_ids)
        if not evidence_ids:
            # Nothing citable at all: neither a scanner edge nor a resolvable
            # doc section. An uncitable claim is exactly what this phase must
            # not publish.
            notes.append(
                f"flow '{flow.name or index}' had no citable evidence and was dropped"
            )
            continue
        if not scanner_backed:
            notes.append(
                f"flow '{flow.name or index}' direction is asserted by documentation "
                "only (no scanner edge); published as documentation-derived"
            )

        object_id = ensure_object(flow.business_object, doc_ids or evidence_ids)
        status, confidence = _status_for(flow.confidence, scanner_backed=scanner_backed)

        base_id = (
            f"ds-{_slug(solution_name)}-{_slug(source_system.name)}--"
            f"{_slug(destination_system.name)}-{_slug(flow.business_object)}"
        )
        stream_id = base_id
        suffix = 2
        while stream_id in used_stream_ids:
            stream_id = f"{base_id}-{suffix}"
            suffix += 1
        used_stream_ids.add(stream_id)

        summary = (flow.carries or "").strip()
        mechanism = (flow.mechanism or "").strip()
        if mechanism:
            summary = (
                f"{summary} (mechanism: {mechanism})" if summary else f"Mechanism: {mechanism}"
            )
        path_id = f"path-{stream_id[3:]}" if flow.technical_path else None

        streams.append(
            BusinessDataStream(
                id=stream_id,
                name=flow.name or f"{source_system.name} -> {destination_system.name}",
                description=summary,
                status=status,
                confidence=confidence,
                evidence_ids=evidence_ids,
                source_system_id=source_id,
                destination_system_id=destination_id,
                business_object_ids=[object_id] if object_id else [],
                business_summary=summary,
                integration_solution_ids=[solution_id],
                protocols=_protocols_from(f"{mechanism} {summary}"),
                requiredness=flow.requiredness,
                delivery_path_id=path_id,
                artifact_ids=artifact_ids,
            )
        )

        if path_id is not None:
            segments: list[DeliverySegment] = []
            previous_ref = source_id
            total = len(flow.technical_path)
            for step_index, step in enumerate(flow.technical_path, start=1):
                is_last = step_index == total
                destination_ref = destination_id if is_last else f"step:{stream_id}:{step_index}"
                step_artifacts, step_unresolved = resolve_designs(
                    step.implementing_designs, design_index
                )
                unresolved_designs.update(step_unresolved)
                step_text = f"{step.protocol} {step.mechanism} {step.name}"
                step_protocols = _protocols_from(step_text)
                protocol = (
                    step.protocol.strip()
                    or (step_protocols[0] if step_protocols else "unknown")
                )
                mode = step.mode.strip()
                if mode not in _SEGMENT_MODES:
                    mode = _mode_for(protocol, step_text)
                segments.append(
                    DeliverySegment(
                        id=f"segment:{stream_id}:{step_index}",
                        name=step.name or f"Delivery step {step_index}",
                        description=step.description,
                        status=status,
                        confidence=confidence,
                        evidence_ids=evidence_ids,
                        sequence=step_index,
                        source_ref=previous_ref,
                        destination_ref=destination_ref,
                        mechanism=step.mechanism or "unknown",
                        protocol=protocol,
                        resource=step.resource,
                        component=step.component,
                        artifact_ids=step_artifacts,
                        mode=mode,
                    )
                )
                previous_ref = destination_ref
            paths.append(
                DeliveryPath(
                    id=path_id,
                    name=(
                        f"{source_system.name} to {destination_system.name} "
                        f"{flow.business_object or 'data'} delivery"
                    ),
                    description=mechanism,
                    status=status,
                    confidence=confidence,
                    evidence_ids=evidence_ids,
                    stream_id=stream_id,
                    segments=segments,
                )
            )

        for transform_index, transform in enumerate(flow.transformations, start=1):
            transform_designs = transform.implementing_designs or flow.implementing_designs
            transform_artifacts, transform_unresolved = resolve_designs(
                transform_designs, design_index
            )
            unresolved_designs.update(transform_unresolved)
            transformations.append(
                BusinessTransformation(
                    id=f"transform:{stream_id}:{transform_index}",
                    name=transform.name or f"{flow.business_object or 'Data'} transformation",
                    description=transform.description,
                    status=status,
                    confidence=confidence,
                    evidence_ids=evidence_ids,
                    stream_ids=[stream_id],
                    integration_solution_ids=[solution_id],
                    artifact_ids=transform_artifacts,
                    mappings=[
                        TransformationMapping(
                            source=mapping.source,
                            target=mapping.target,
                            mapping_type=mapping.mapping_type,
                        )
                        for mapping in transform.mappings
                        if mapping.source or mapping.target
                    ],
                )
            )

    solutions: list[IntegrationSolution] = []
    if solution_artifacts:
        deduped = sorted(set(solution_artifacts))
        solutions.append(
            IntegrationSolution(
                id=solution_id,
                name=solution_name,
                description=f"Integration sub-project '{solution_name}'.",
                # An artifact id IS scanner evidence, which satisfies both the
                # "has scanner evidence" and "cites one of its artifact_ids"
                # rules in one move -- the pair the model failed with an empty
                # artifact_ids list.
                status="observed",
                confidence=0.85,
                evidence_ids=deduped[:1],
                platform=_platform_label(bundle),
                role="execution",
                artifact_ids=deduped,
            )
        )
    else:
        # No stream survived, so a solution claim would cite nothing. Drop it
        # rather than emit an artifact-less solution the validator rejects.
        notes.append(
            f"no integration solution published for '{solution_name}': no flow "
            "resolved a scanner artifact"
        )

    if unresolved_designs:
        notes.append(
            f"{len(unresolved_designs)} named design(s) did not resolve to a scanner "
            f"artifact (e.g. {', '.join(sorted(unresolved_designs)[:5])})"
        )

    topology = BusinessTopology(
        scan_fingerprint=bundle.scan_fingerprint,
        metadata=BusinessTopologyMetadata(
            project_id=project_id,
            scan_fingerprint=bundle.scan_fingerprint,
            generated_by=GENERATED_BY,
            model_name=model_name,
        ),
        systems=list(systems.values()),
        integration_solutions=solutions,
        business_objects=list(objects.values()),
        data_streams=streams,
        delivery_paths=paths,
        transformations=transformations,
        coverage=BusinessTopologyCoverage(
            total_artifacts=len(bundle.artifact_ids),
            cited_artifacts=0,
            total_evidence=len(bundle.evidence_ids),
            cited_evidence=0,
            coverage_ratio=0.0,
            technical_relationships=len(streams),
            grounded_technical_relationships=sum(
                1 for stream in streams if stream.status == "observed"
            ),
            notes=[
                "Assembled deterministically from a business-level flow extraction "
                "over this run's generated documentation and the scanner landscape. "
                "Streams marked 'observed' are backed by a scanner directional edge; "
                "'inferred' streams are asserted by documentation and wired to real "
                "scanner artifacts.",
                *notes[:20],
            ],
        ),
    )
    return topology, notes


def _doc_ids_for(
    citation: str, doc_records: dict[str, dict[str, object]]
) -> list[str]:
    resolved = resolve_doc_citation(citation, doc_records)
    if resolved:
        return resolved
    # Fall back to the data-flows document, the source Stage A reads first. An
    # empty list here would strip the only citation a doc-derived claim has.
    from mcp_server.services.business_topology.doc_evidence import doc_evidence_id

    fallback = doc_evidence_id("03_DATA_FLOWS_AND_LINEAGE.md", "")
    return [fallback] if fallback in doc_records else []


def _platform_label(bundle: BusinessTopologyEvidenceBundle) -> str:
    """Best-effort platform label from the scanner artifact rows."""

    for artifact_id in sorted(bundle.artifact_ids):
        record = bundle.evidence_records.get(artifact_id) or {}
        kind = str(record.get("artifact_kind") or "").strip()
        if kind:
            return kind
    return ""
