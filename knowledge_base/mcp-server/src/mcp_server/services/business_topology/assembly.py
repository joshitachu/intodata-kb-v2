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
    primary_document_id,
    resolve_designs,
    resolve_doc_citation,
)
from mcp_server.services.business_topology.evidence import (
    BusinessTopologyEvidenceBundle,
)
from mcp_server.services.business_topology.flow_extraction import (
    FlowExtraction,
)
from mcp_server.services.business_topology.models import (
    BusinessConflict,
    BusinessControl,
    BusinessDataStream,
    BusinessObject,
    BusinessRisk,
    BusinessRule,
    BusinessSystem,
    BusinessTopology,
    BusinessTopologyCoverage,
    BusinessTopologyMetadata,
    BusinessTransformation,
    BusinessUnknown,
    DeliveryPath,
    DeliverySegment,
    IntegrationSolution,
    StreamCondition,
    TransformationMapping,
)

# Same-package internals reused on purpose: the assembler must resolve edges the
# way the validator resolves them. A local copy of this logic would be free to
# drift, and a drift here means a stream the assembler believes is grounded and
# the validator rejects.
from mcp_server.services.business_topology.validation import (
    _directional_artifact_ids,
    _scanner_artifact_identity_map,
    redact_secretlike,
)

GENERATED_BY = "doc_grounded_flow_extraction"

_SYSTEM_KIND_BY_EXTRACTION = {
    "application": "software_system",
    "database": "data_system",
    "external_service": "external_service",
    "file": "data_system",
    "queue": "external_service",
}

#: The kinds `BusinessSystem.kind` accepts. The scanner landscape uses a wider
#: vocabulary, so its value is only adopted when it is one of these.
_PUBLISHED_SYSTEM_KINDS = frozenset(
    {
        "software_system",
        "data_system",
        "external_service",
        "organization",
        "person_role",
        "unresolved",
    }
)

_CONFIDENCE_VALUE = {"certain": 0.9, "high": 0.8, "medium": 0.62, "low": 0.42}

#: A documentation-only claim never outranks a scanner-grounded one.
_DOC_TIER_MAX_CONFIDENCE = 0.6

#: Confidence carried by an open question. It is a gap, not a claim, so the
#: number only orders it; the deterministic overlay uses the same value.
_UNKNOWN_CONFIDENCE = 0.2

_NAME_CHARS = 90

#: Typed supporting evidence a conflict may cite, per declared conflict type.
#: Mirrors the validator's rules: documentation is NOT among them, which is why
#: a documented disagreement only becomes a `BusinessConflict` when typed
#: runtime / intended-state evidence about the same designs exists.
_RUNTIME_SUPPORT = frozenset({"observed_runtime"})
_INTENDED_SUPPORT = frozenset({"intended_documented", "approved_business_declaration"})
_CONFLICT_SUPPORT_TYPES = {
    "runtime_vs_implementation": _RUNTIME_SUPPORT,
    "supporting_vs_implementation": _INTENDED_SUPPORT,
    "supporting_sources_disagree": _RUNTIME_SUPPORT | _INTENDED_SUPPORT,
    "unresolved": _RUNTIME_SUPPORT | _INTENDED_SUPPORT,
}

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


def _clean(text: str) -> str:
    """Strip and redact one piece of copied prose (the copy boundary)."""

    return redact_secretlike((text or "").strip())


def _short_name(text: str) -> str:
    """A display name derived from a statement when the model gave none."""

    clean = re.sub(r"\s+", " ", text or "").strip()
    if len(clean) <= _NAME_CHARS:
        return clean
    return clean[: _NAME_CHARS - 3].rstrip() + "..."


def _flow_key(name: str) -> str:
    """Key a model-written flow name for cross-reference lookup.

    Whitespace- and case-insensitive only: a flow name is a sentence the model
    wrote twice, not a design name, so this is not the fuzzy matching the design
    resolver forbids.
    """

    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


def _doc_tier(word: str | None, fallback: str) -> tuple[str, float]:
    """Status for a claim whose only authority is the generated documentation."""

    return _status_for(word or fallback, scanner_backed=False)


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
        self._endpoint_artifacts: dict[str, set[str]] = {}

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

    def artifacts_touching(self, identity_id: str | None) -> set[str]:
        """Artifacts on any scanner edge that has ``identity_id`` as an endpoint.

        The scanner and the documentation model the landscape at different
        altitudes: verified on the real werkorder scan, the landscape carries
        `Erp_to_the_Max -> Maximo` and `Erp_to_the_Max -> 4PS` (integration
        application to external system) while the documentation describes
        `Maximo -> 4PS` (the business flow the application implements). No
        system-to-system edge exists for the model to match, so an exact
        direction lookup finds nothing and EVERY flow would be published as a
        bare assertion at capped confidence -- a whole landscape rendered as
        guesswork when the scanner in fact proves both endpoints are touched by
        the very designs the flow names.
        """

        if not identity_id:
            return set()
        cached = self._endpoint_artifacts.get(identity_id)
        if cached is not None:
            return cached
        edge_ids = [
            evidence_id
            for (source, destination), ids in self._by_pair.items()
            if identity_id in (source, destination)
            for evidence_id in ids
        ]
        artifacts = (
            _directional_artifact_ids(set(edge_ids), self._bundle) if edge_ids else set()
        )
        self._endpoint_artifacts[identity_id] = artifacts
        return artifacts


def _status_for(
    confidence_word: str,
    *,
    scanner_backed: bool,
    identity_backed: bool = True,
    endpoint_corroborated: bool = False,
) -> tuple[str, float]:
    """Map a confidence WORD onto ``(ClaimStatus, numeric confidence)``.

    Three grounding tiers, strongest first:

    * ``scanner_backed`` -- a scanner edge establishes this exact direction.
      May reach ``observed``.
    * ``endpoint_corroborated`` -- no system-to-system edge exists, but the
      scanner proves the flow's own implementing designs touch BOTH endpoint
      systems. Direction remains documentation-asserted, so never ``observed``,
      but the confidence is not capped: this is a well-established flow, not a
      bare assertion, and treating the two alike renders the whole landscape as
      guesswork (the real werkorder shape -- see ``artifacts_touching``).
    * documentation only -- capped at ``_DOC_TIER_MAX_CONFIDENCE``.

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
        return status, value if endpoint_corroborated else min(
            value, _DOC_TIER_MAX_CONFIDENCE
        )
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
    rules: list[BusinessRule] = []
    stream_by_id: dict[str, BusinessDataStream] = {}
    streams_by_flow: dict[str, list[str]] = {}
    solution_artifacts: list[str] = []
    used_stream_ids: set[str] = set()
    unresolved_designs: set[str] = set()

    def add_aliases(system: BusinessSystem, names: list[str]) -> None:
        for raw in names:
            alias = _clean(raw)
            if (
                alias
                and alias.casefold() != system.name.casefold()
                and alias not in system.aliases
            ):
                system.aliases.append(alias)

    def ensure_system(
        name: str,
        *,
        kind_hint: str,
        role: str,
        confidence: str,
        owner: str = "",
        aliases: list[str] | None = None,
    ) -> str:
        role = redact_secretlike(role)
        owner = _clean(owner)
        canonical, identity_id, scanner_kind = resolver.resolve(name)
        system_id = f"sys-{_slug(canonical)}"
        existing = systems.get(system_id)
        if existing is not None:
            if role and not existing.purpose:
                existing.purpose = role
                existing.description = role
            if owner and not existing.owner:
                existing.owner = owner
            # The spelling the documents used is kept as an alias: it is how a
            # reader searching the prose will look for this node.
            add_aliases(existing, [name, *(aliases or [])])
            return system_id
        if identity_id is not None:
            status, value = _status_for(confidence, scanner_backed=True)
            evidence_ids = [identity_id]
            # The scanner's own kind wins when it is one the published contract
            # accepts; its vocabulary is wider than ours (`rest_api`, `database`),
            # so anything else falls back to the extraction's business-level hint.
            kind = (
                scanner_kind
                if scanner_kind in _PUBLISHED_SYSTEM_KINDS
                else _SYSTEM_KIND_BY_EXTRACTION.get(kind_hint, "software_system")
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
            owner=owner,
        )
        add_aliases(systems[system_id], [name, *(aliases or [])])
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
            system.name,
            kind_hint=system.kind,
            role=system.role,
            confidence="high",
            owner=system.owner,
            aliases=system.aliases,
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
        # No system-to-system edge? The scanner can still corroborate BOTH
        # endpoints through the flow's own implementing designs. Note that the
        # corroborating edges are deliberately NOT cited on the stream: a
        # `landscape-edge` id in `evidence_ids` would make the validator treat
        # the direction as scanner-established and then reject it for not
        # connecting these two systems.
        endpoint_corroborated = False
        if not scanner_backed:
            resolved_set = set(artifact_ids)
            endpoint_corroborated = bool(
                resolved_set & edges.artifacts_touching(source_identity)
                and resolved_set & edges.artifacts_touching(destination_identity)
            )
            if endpoint_corroborated:
                notes.append(
                    f"flow '{flow.name or index}' has no system-to-system scanner edge, "
                    "but its implementing designs are scanner-proven at both endpoints"
                )
            else:
                notes.append(
                    f"flow '{flow.name or index}' direction is asserted by documentation "
                    "only (no scanner edge); published as documentation-derived"
                )

        object_id = ensure_object(flow.business_object, doc_ids or evidence_ids)
        status, confidence = _status_for(
            flow.confidence,
            scanner_backed=scanner_backed,
            endpoint_corroborated=endpoint_corroborated,
        )

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

        # Prose from the documents is scrubbed at the copy boundary: a leaked
        # connection string here is a non-prunable secret finding that would
        # fail the whole overlay.
        # Mechanism is a STRUCTURED field. It used to be concatenated into
        # description/business_summary, where no panel renders it and no reader
        # can table or compare it.
        summary = _clean(flow.carries)
        mechanism = _clean(flow.mechanism)
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
                mechanism=mechanism,
                generation=_clean(flow.generation),
                key_form=_clean(flow.key_form),
                verdict=_clean(flow.verdict),
                conditions=[
                    StreamCondition(
                        kind=condition.kind,
                        statement=_clean(condition.statement),
                        evidence_note=_clean(condition.evidence or flow.evidence),
                    )
                    for condition in flow.conditions
                    if _clean(condition.statement)
                ],
                document_refs=[_clean(flow.evidence)] if _clean(flow.evidence) else [],
            )
        )
        stream_by_id[stream_id] = streams[-1]
        for key in {_flow_key(flow.name), _flow_key(streams[-1].name)} - {""}:
            streams_by_flow.setdefault(key, []).append(stream_id)

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
                        description=redact_secretlike(step.description),
                        status=status,
                        confidence=confidence,
                        evidence_ids=evidence_ids,
                        sequence=step_index,
                        source_ref=previous_ref,
                        destination_ref=destination_ref,
                        mechanism=step.mechanism or "unknown",
                        protocol=protocol,
                        resource=redact_secretlike(step.resource),
                        component=redact_secretlike(step.component),
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

        transformation_by_name: dict[str, str] = {}
        for transform_index, transform in enumerate(flow.transformations, start=1):
            transform_id = f"transform:{stream_id}:{transform_index}"
            if transform.name:
                transformation_by_name.setdefault(_flow_key(transform.name), transform_id)
            transform_designs = transform.implementing_designs or flow.implementing_designs
            transform_artifacts, transform_unresolved = resolve_designs(
                transform_designs, design_index
            )
            unresolved_designs.update(transform_unresolved)
            transformations.append(
                BusinessTransformation(
                    id=transform_id,
                    name=_clean(transform.name)
                    or f"{flow.business_object or 'Data'} transformation",
                    description=redact_secretlike(transform.description),
                    kind=transform.kind,
                    target_field=_clean(transform.target_field),
                    status=status,
                    confidence=confidence,
                    evidence_ids=evidence_ids,
                    stream_ids=[stream_id],
                    integration_solution_ids=[solution_id],
                    artifact_ids=transform_artifacts,
                    mappings=[
                        TransformationMapping(
                            source=redact_secretlike(mapping.source),
                            target=redact_secretlike(mapping.target),
                            mapping_type=mapping.mapping_type,
                            expression=_clean(mapping.expression),
                            category=_clean(mapping.category),
                            reaches_target=mapping.reaches_target,
                            note=_clean(mapping.note),
                        )
                        for mapping in transform.mappings
                        if mapping.source or mapping.target
                    ],
                )
            )

        # Business rules and conditions are documentation assertions about a
        # flow, so they take the documentation tier regardless of how well the
        # flow's DIRECTION is grounded: a scanner edge proves the exchange
        # happens, not that the rule the prose describes is the one applied.
        for rule_index, rule in enumerate(flow.business_rules, start=1):
            statement = _clean(rule.statement)
            rule_evidence = resolve_doc_citation(rule.evidence, doc_records) or doc_ids
            if not statement or not rule_evidence:
                notes.append(
                    f"rule #{rule_index} of flow '{flow.name or index}' had no statement "
                    "or no citable document and was not published"
                )
                continue
            rule_status, rule_confidence = _doc_tier(rule.confidence, flow.confidence)
            linked = transformation_by_name.get(_flow_key(rule.transformation))
            if rule.transformation and linked is None:
                notes.append(
                    f"rule '{rule.name or statement[:40]}' names transformation "
                    f"'{rule.transformation}', which flow '{flow.name or index}' does not declare"
                )
            rules.append(
                BusinessRule(
                    id=f"rule:{stream_id}:{rule_index}",
                    name=_clean(rule.name) or _short_name(statement),
                    description="",
                    status=rule_status,
                    confidence=rule_confidence,
                    evidence_ids=list(rule_evidence),
                    kind=rule.kind,
                    statement=statement,
                    inputs=[_clean(item) for item in rule.inputs if _clean(item)],
                    output=_clean(rule.output),
                    stream_ids=[stream_id],
                    transformation_ids=[linked] if linked else [],
                    owner=_clean(rule.owner),
                    owner_question=_clean(rule.owner_question),
                )
            )

    controls, risks, unknowns, conflicts = _assemble_findings(
        extraction,
        bundle=bundle,
        doc_records=doc_records,
        solution_name=solution_name,
        stream_by_id=stream_by_id,
        streams_by_flow=streams_by_flow,
        notes=notes,
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
        business_rules=rules,
        controls=controls,
        risks=risks,
        unknowns=unknowns,
        conflicts=conflicts,
        coverage=BusinessTopologyCoverage(
            total_artifacts=len(bundle.artifact_ids),
            cited_artifacts=0,
            total_evidence=len(bundle.evidence_ids),
            cited_evidence=0,
            coverage_ratio=0.0,
            technical_relationships=len(streams),
            # "Grounded" counts BOTH scanner tiers: a direct directional edge and
            # an endpoint-corroborated flow are both scanner-backed claims about
            # real designs. Counting only `observed` reported 0 of 43 on the real
            # werkorder scan, where no system-to-system edges exist at all.
            grounded_technical_relationships=sum(
                1
                for stream in streams
                if stream.status == "observed"
                or stream.confidence > _DOC_TIER_MAX_CONFIDENCE
            ),
            notes=[
                "Assembled deterministically from a business-level flow extraction "
                "over this run's generated documentation and the scanner landscape. "
                "Streams marked 'observed' are backed by a scanner directional edge; "
                "'inferred' streams above 0.6 confidence have scanner-proven "
                "implementing designs at both endpoints; the rest are asserted by "
                "documentation and wired to real scanner artifacts.",
                *notes[:20],
            ],
        ),
    )
    return topology, notes


def _assemble_findings(
    extraction: FlowExtraction,
    *,
    bundle: BusinessTopologyEvidenceBundle,
    doc_records: dict[str, dict[str, object]],
    solution_name: str,
    stream_by_id: dict[str, BusinessDataStream],
    streams_by_flow: dict[str, list[str]],
    notes: list[str],
) -> tuple[
    list[BusinessControl],
    list[BusinessRisk],
    list[BusinessUnknown],
    list[BusinessConflict],
]:
    """Map the extraction's top-level findings onto published claims.

    The model refers to flows by the names it wrote; Python resolves those to
    stream ids and writes the reverse links (`control_ids`, `risk_ids`,
    `unknown_ids`, `conflict_ids`) onto the streams. A reference to a flow that
    did not survive assembly is dropped from the link with a note, and the
    finding itself still publishes: a control or risk without a stream is a
    valid landscape-level claim.
    """

    scope = _slug(solution_name)

    def stream_refs(flow_names: list[str], label: str) -> list[str]:
        refs: list[str] = []
        for name in flow_names:
            matched = streams_by_flow.get(_flow_key(name))
            if not matched:
                notes.append(f"{label} refers to flow '{name}', which was not published")
                continue
            refs.extend(ref for ref in matched if ref not in refs)
        return refs

    def evidence_for(citation: str) -> list[str]:
        return _doc_ids_for(citation, doc_records)

    controls: list[BusinessControl] = []
    for index, item in enumerate(extraction.controls, start=1):
        evidence_ids = evidence_for(item.evidence)
        name = _clean(item.name)
        if not evidence_ids or not name:
            notes.append(f"control #{index} had no citable document and was not published")
            continue
        status, confidence = _doc_tier(item.confidence, "medium")
        control_id = f"control:{scope}:{index}"
        refs = stream_refs(item.flows, f"control '{name}'")
        controls.append(
            BusinessControl(
                id=control_id,
                name=name,
                description=_clean(item.description),
                status=status,
                confidence=confidence,
                evidence_ids=evidence_ids,
                stream_ids=refs,
                control_type=_clean(item.control_type),
            )
        )
        for ref in refs:
            stream_by_id[ref].control_ids.append(control_id)

    risks: list[BusinessRisk] = []
    for index, item in enumerate(extraction.risks, start=1):
        evidence_ids = evidence_for(item.evidence)
        name = _clean(item.name)
        if not evidence_ids or not name:
            notes.append(f"risk #{index} had no citable document and was not published")
            continue
        status, confidence = _doc_tier(item.confidence, "medium")
        risk_id = f"risk:{scope}:{index}"
        refs = stream_refs(item.flows, f"risk '{name}'")
        risks.append(
            BusinessRisk(
                id=risk_id,
                name=name,
                description=_clean(item.description),
                status=status,
                confidence=confidence,
                evidence_ids=evidence_ids,
                stream_ids=refs,
                severity=item.severity,
            )
        )
        for ref in refs:
            stream_by_id[ref].risk_ids.append(risk_id)

    unknowns: list[BusinessUnknown] = []

    def add_unknown(
        question: str, *, name: str, description: str, flows: list[str], citation: str
    ) -> None:
        unknown_id = f"unknown:{scope}:{len(unknowns) + 1}"
        refs = stream_refs(flows, f"open question '{_short_name(question)}'")
        unknowns.append(
            BusinessUnknown(
                id=unknown_id,
                name=name or _short_name(question),
                description=description,
                # A gap, not an assertion: `not_established` is the honest
                # status and the only one that needs no supporting evidence.
                # The document that raised the question is still cited.
                status="not_established",
                confidence=_UNKNOWN_CONFIDENCE,
                evidence_ids=evidence_for(citation),
                stream_ids=refs,
                question=question,
            )
        )
        for ref in refs:
            stream_by_id[ref].unknown_ids.append(unknown_id)

    for item in extraction.open_questions:
        question = _clean(item.question)
        if question:
            add_unknown(
                question,
                name=_clean(item.name),
                description="",
                flows=item.flows,
                citation=item.evidence,
            )

    conflicts: list[BusinessConflict] = []
    support_index = _typed_support_index(bundle)
    for item in extraction.conflicts:
        description = _clean(item.description)
        if not description:
            continue
        refs = [
            ref
            for name in item.flows
            for ref in streams_by_flow.get(_flow_key(name), [])
        ]
        refs = list(dict.fromkeys(refs))
        conflict = _typed_conflict(
            item.conflict_type,
            description=description,
            name=_clean(item.name) or _short_name(description),
            subject_ids=refs,
            stream_by_id=stream_by_id,
            support_index=support_index,
            doc_ids=evidence_for(item.evidence),
            conflict_id=f"conflict:{scope}:{len(conflicts) + 1}",
        )
        if conflict is not None:
            conflicts.append(conflict)
            for ref in conflict.subject_refs:
                stream_by_id[ref].conflict_ids.append(conflict.id)
            continue
        # The validator requires typed runtime or intended-state evidence for a
        # conflict, and documentation is neither. Rather than publish a claim
        # the validator must prune, the disagreement is kept as the question it
        # really is -- still cited and still linked to its flows.
        notes.append(
            f"documented conflict '{_short_name(description)}' has no typed runtime or "
            "intended-state evidence on its designs; published as an open question"
        )
        add_unknown(
            f"Which account is correct? {description}",
            name=_clean(item.name) or _short_name(description),
            description=f"Conflict reported by the documentation ({item.conflict_type}).",
            flows=item.flows,
            citation=item.evidence,
        )
    return controls, risks, unknowns, conflicts


def _typed_support_index(
    bundle: BusinessTopologyEvidenceBundle,
) -> dict[str, list[tuple[str, str]]]:
    """``{scanner evidence id: [(typed supporting claim id, type)]}``.

    Built from the supporting claims' own ``source_evidence_ids``, so a claim
    is only ever associated with the designs it was recorded against.
    """

    index: dict[str, list[tuple[str, str]]] = {}
    for evidence_id, record in sorted(bundle.evidence_records.items()):
        evidence_type = bundle.evidence_types.get(evidence_id, "")
        if evidence_type not in _RUNTIME_SUPPORT | _INTENDED_SUPPORT:
            continue
        for source_id in record.get("source_evidence_ids") or []:
            index.setdefault(str(source_id), []).append((evidence_id, evidence_type))
    return index


def _typed_conflict(
    conflict_type: str,
    *,
    description: str,
    name: str,
    subject_ids: list[str],
    stream_by_id: dict[str, BusinessDataStream],
    support_index: dict[str, list[tuple[str, str]]],
    doc_ids: list[str],
    conflict_id: str,
) -> BusinessConflict | None:
    """Build a conflict only when the validator's typed-evidence rule is met.

    Implementation evidence is the subject streams' own scanner artifacts that
    typed supporting claims were recorded against; supporting evidence is those
    claims, filtered to the types the declared ``conflict_type`` accepts.
    """

    if not subject_ids:
        return None
    accepted = _CONFLICT_SUPPORT_TYPES.get(conflict_type, frozenset())
    implementation: list[str] = []
    supporting: list[str] = []
    for ref in subject_ids:
        for artifact_id in stream_by_id[ref].artifact_ids:
            claims = [
                claim_id
                for claim_id, claim_type in support_index.get(artifact_id, [])
                if claim_type in accepted
            ]
            if not claims:
                continue
            if artifact_id not in implementation:
                implementation.append(artifact_id)
            supporting.extend(claim for claim in claims if claim not in supporting)
    if not supporting:
        return None
    if conflict_type == "supporting_sources_disagree":
        if len(supporting) < 2:
            return None
        implementation = []
    evidence_ids = list(dict.fromkeys([*implementation, *supporting, *doc_ids]))
    return BusinessConflict(
        id=conflict_id,
        name=name,
        description=description,
        status="inferred",
        confidence=_DOC_TIER_MAX_CONFIDENCE,
        evidence_ids=evidence_ids,
        conflict_type=conflict_type,  # type: ignore[arg-type]
        subject_refs=list(subject_ids),
        implementation_evidence_ids=implementation,
        supporting_evidence_ids=supporting,
    )


def merge_scanner_floor(
    assembled: BusinessTopology,
    deterministic: BusinessTopology,
) -> list[str]:
    """Add scanner edges the extraction missed, without duplicating nodes.

    The two builders mint different system ids -- ``sys-{slug}`` here versus
    ``sys-{slug}-{identity hash}`` in :mod:`deterministic` -- so a naive union
    publishes the same system twice, which is precisely the "wrong shape"
    outcome this rebuild exists to fix. Systems are therefore folded on their
    scanner IDENTITY evidence id, which both builders cite.

    Without this floor, coverage would depend entirely on extraction recall: a
    run whose documents describe 6 of 18 real edges would publish a landscape
    missing 12 flows the scanner already proved. With it, the business layer and
    the scanner's edge coverage are additive rather than a choice.

    Mutates ``assembled``. Returns notes describing what was added.
    """

    notes: list[str] = []
    identity_to_assembled: dict[str, str] = {}
    for system in assembled.systems:
        if system.kind == "unresolved":
            continue
        for evidence_id in system.evidence_ids:
            identity_to_assembled.setdefault(evidence_id, system.id)

    existing_pairs = {
        (stream.source_system_id, stream.destination_system_id)
        for stream in assembled.data_streams
    }
    assembled_system_ids = {system.id for system in assembled.systems}
    solution_ids = {item.id for item in assembled.integration_solutions}

    # Fold deterministic systems onto assembled ones, or adopt them outright.
    remap: dict[str, str] = {}
    adopted_systems: list[BusinessSystem] = []
    for system in deterministic.systems:
        target = next(
            (
                identity_to_assembled[evidence_id]
                for evidence_id in system.evidence_ids
                if evidence_id in identity_to_assembled
            ),
            None,
        )
        if target is not None:
            remap[system.id] = target
            continue
        if system.id not in assembled_system_ids:
            adopted_systems.append(system)
            assembled_system_ids.add(system.id)
        remap[system.id] = system.id

    # The floor's own business-layer claims that can travel with it. Its
    # transformations are scanner-lineage field mappings (status `observed`) --
    # discarding them made an adopted stream an unlabelled edge again. Its
    # unknowns are either attached to a stream or landscape-level questions
    # about cross-partition edges; both cite only scanner evidence.
    floor_unknowns = {item.id: item for item in deterministic.unknowns}

    added_streams: list[BusinessDataStream] = []
    needed_systems: set[str] = set()
    needed_solutions: set[str] = set()
    for stream in deterministic.data_streams:
        source_id = remap.get(stream.source_system_id)
        destination_id = remap.get(stream.destination_system_id)
        if source_id is None or destination_id is None or source_id == destination_id:
            continue
        if (source_id, destination_id) in existing_pairs:
            # The extraction already describes this relationship, with business
            # meaning the scanner edge does not have. Keep the richer one.
            continue
        existing_pairs.add((source_id, destination_id))
        copy = stream.model_copy(deep=True)
        copy.source_system_id = source_id
        copy.destination_system_id = destination_id
        # These reference deterministic-local claims that are not being adopted;
        # a dangling ref would fail validation and cost the whole floor.
        copy.business_object_ids = []
        copy.control_ids = []
        copy.risk_ids = []
        copy.unknown_ids = [ref for ref in copy.unknown_ids if ref in floor_unknowns]
        copy.runtime_observation_ids = []
        copy.conflict_ids = []
        copy.delivery_path_id = None
        copy.integration_solution_ids = [
            ref for ref in copy.integration_solution_ids if ref
        ]
        needed_solutions.update(copy.integration_solution_ids)
        needed_systems.update({source_id, destination_id})
        added_streams.append(copy)

    # Deep copies: these objects belong to the deterministic topology, which
    # remains the phase's fallback candidate. Sharing them would let a later
    # mutation of one published overlay alter the other.
    assembled.systems.extend(
        system.model_copy(deep=True)
        for system in adopted_systems
        if system.id in needed_systems
    )
    published_system_ids = {system.id for system in assembled.systems}
    added_streams = [
        stream
        for stream in added_streams
        if stream.source_system_id in published_system_ids
        and stream.destination_system_id in published_system_ids
    ]
    for solution in deterministic.integration_solutions:
        if solution.id in needed_solutions and solution.id not in solution_ids:
            assembled.integration_solutions.append(solution.model_copy(deep=True))
            solution_ids.add(solution.id)
    assembled.data_streams.extend(added_streams)
    assembled.coverage.technical_relationships = len(assembled.data_streams)

    adopted_stream_ids = {stream.id for stream in added_streams}
    taken_ids = {
        claim.id
        for collection in (
            assembled.systems,
            assembled.integration_solutions,
            assembled.business_objects,
            assembled.data_streams,
            assembled.delivery_paths,
            assembled.transformations,
            assembled.business_rules,
            assembled.controls,
            assembled.risks,
            assembled.unknowns,
            assembled.conflicts,
            assembled.runtime_observations,
        )
        for claim in collection
    }
    kept_transformations = 0
    for transformation in deterministic.transformations:
        refs = [ref for ref in transformation.stream_ids if ref in adopted_stream_ids]
        if not refs or transformation.id in taken_ids:
            continue
        copy = transformation.model_copy(deep=True)
        copy.stream_ids = refs
        copy.integration_solution_ids = [
            ref for ref in copy.integration_solution_ids if ref in solution_ids
        ]
        assembled.transformations.append(copy)
        taken_ids.add(copy.id)
        kept_transformations += 1

    kept_unknowns: set[str] = set()
    for unknown in deterministic.unknowns:
        refs = [ref for ref in unknown.stream_ids if ref in adopted_stream_ids]
        # A stream-attached unknown travels only with its stream; a
        # landscape-level one (no stream at all) stands on its own evidence.
        if (unknown.stream_ids and not refs) or unknown.id in taken_ids:
            continue
        copy = unknown.model_copy(deep=True)
        copy.stream_ids = refs
        assembled.unknowns.append(copy)
        taken_ids.add(copy.id)
        kept_unknowns.add(copy.id)
    for stream in added_streams:
        stream.unknown_ids = [ref for ref in stream.unknown_ids if ref in kept_unknowns]

    if added_streams:
        notes.append(
            f"added {len(added_streams)} scanner-backed stream(s) the documentation "
            "extraction did not describe, so scanner edge coverage is not lost"
        )
    if kept_transformations or kept_unknowns:
        notes.append(
            f"kept {kept_transformations} scanner-lineage transformation(s) and "
            f"{len(kept_unknowns)} open question(s) from the scanner floor"
        )
    assembled.coverage.notes.extend(notes)
    return notes


def _doc_ids_for(
    citation: str, doc_records: dict[str, dict[str, object]]
) -> list[str]:
    resolved = resolve_doc_citation(citation, doc_records)
    if resolved:
        return resolved
    # Fall back to the best-ranked registered document. An empty list would
    # strip the only citation a documentation-derived claim has, and naming a
    # specific filename here would break every pack that renames its documents.
    fallback = primary_document_id(doc_records)
    return [fallback] if fallback else []


def _platform_label(bundle: BusinessTopologyEvidenceBundle) -> str:
    """Best-effort platform label from the scanner artifact rows."""

    for artifact_id in sorted(bundle.artifact_ids):
        record = bundle.evidence_records.get(artifact_id) or {}
        kind = str(record.get("artifact_kind") or "").strip()
        if kind:
            return kind
    return ""
