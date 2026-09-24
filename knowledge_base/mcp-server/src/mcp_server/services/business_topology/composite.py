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
  org-level nodes; each sub-project's own application node is preserved). The
  strongest partition status/confidence wins, and purpose/owner/aliases travel;
* data streams are the union of every partition's streams, re-pointed at the
  merged system identities and de-duplicated by (source, destination, business
  object) so a cross-project edge detected from both sides (e.g. ETTM -> ESB
  over JMS) appears once WITHOUT collapsing distinct business flows between the
  same pair of systems -- de-duplicating on (source, destination) alone is what
  reduced the werkorder org view to 18 unlabelled edges where the hand-curated
  file has 72 named ones. Every structured stream field travels (see
  :func:`_merge_stream_fields` for the multi-partition merge rule);
* the whole business layer travels with them: business objects, delivery paths
  (segments re-pointed at org ids), transformations, business rules, controls,
  risks, unknowns, runtime observations, conflicts and artifact / component /
  lineage annotations. Partition-local claim ids are re-namespaced per
  partition (``{partition-slug}:{local id}``) and every stream, delivery-path
  and claim reference is re-pointed onto the org ids. Dropping these at the
  merge boundary made every per-partition improvement invisible at the level
  users actually look at;
* one integration solution per logical sub-project (the applications), carrying
  the partitions' own solution evidence and artifact ids;
* an ``unknowns`` entry per logical sub-project whose own topology failed to
  publish (or belongs to an older scan), so the gap is captured rather than
  silently dropped.

Freshness: ``partition_state.json`` is keyed by PER-PARTITION fingerprint
(``partition_fingerprint`` -- the skip rule's input); its ``scan_fingerprint``
is only the whole-scan digest the partition was LAST PUBLISHED from, kept for
diagnostics. A reused partition therefore legitimately carries an older
whole-scan digest. The org topology is an upload-level artifact whose identity
is the CURRENT whole-scan fingerprint; a partition contributes only when it is
current -- its recorded whole-scan digest equals the current one, or the skip
rule (``partition_orchestrator.decide_partition_actions``) says it is unchanged.
The previous ``sorted(fingerprints)[0]`` picked an arbitrary partition's digest,
so any incremental run served the org view as stale (or, worse, fresh against
the wrong scan).

Validation: :func:`build_validated_composite_org_topology` validates the merge
for real (``validate_business_topology`` + ``prune_to_valid``) against an org
evidence bundle assembled from the contributing partitions' own ledgers. The
merge used to be persisted as ``validated: true`` without ever being checked.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp_server.services.business_topology.models import (
    ArtifactAnnotation,
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
    ComponentAnnotation,
    ContractWidth,
    DeliveryPath,
    DeliverySegment,
    DeploymentAgreement,
    IntegrationSolution,
    LineageAnnotation,
    RuntimeMetric,
    RuntimeObservation,
    StreamCondition,
    TransformationMapping,
)

if TYPE_CHECKING:
    from mcp_server.services.business_topology.evidence import (
        BusinessTopologyEvidenceBundle,
    )
    from mcp_server.services.business_topology.validation import (
        BusinessTopologyValidationReport,
    )

logger = logging.getLogger(__name__)

GENERATED_BY = "composite_org_merge"

_PART_SUFFIX_RE = re.compile(r"_part\d+$", re.IGNORECASE)
#: Claim ids must stay inside the character class ``pruning._LEADING_ID``
#: accepts, or a claim-local validation error could not be attributed to (and
#: pruned from) the claim that caused it.
_UNSAFE_ID_CHARS = re.compile(r"[^A-Za-z0-9:_.\-]+")

#: Statuses a merged claim may carry, strongest first. The merge used to flatten
#: everything to ``inferred``, which made a scanner-edge-backed flow and a
#: documentation-asserted one look identical in the UI.
_STATUS_RANK = ("observed", "confirmed", "inferred", "proposed", "not_established")
#: Every ``ClaimStatus``; ``rejected`` is valid but never "stronger" than another.
_CLAIM_STATUSES = frozenset({*_STATUS_RANK, "rejected"})

_DEFAULT_SYSTEM_DESCRIPTION = (
    "System participating in the organization integration landscape."
)
_SYSTEM_KINDS = frozenset(
    {
        "software_system",
        "data_system",
        "external_service",
        "organization",
        "person_role",
        "unresolved",
    }
)
_SEGMENT_MODES = frozenset(
    {
        "direct",
        "buffered",
        "batch",
        "scheduled",
        "event_driven",
        "file_transfer",
        "database",
        "unknown",
    }
)


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


def _namespaced(partition_name: str, local_id: str) -> str:
    """Org id for a partition-local claim: unique per partition, prune-safe."""

    safe = _UNSAFE_ID_CHARS.sub("-", str(local_id)).strip("-") or "claim"
    return f"{_slug(partition_name)}:{safe}"


def _unique_id(base: str, taken: set[str]) -> str:
    candidate = base
    counter = 2
    while candidate in taken:
        candidate = f"{base}-{counter}"
        counter += 1
    taken.add(candidate)
    return candidate


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _strs(values: Any) -> list[str]:
    return [str(item) for item in values or [] if str(item)]


def _extend_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value and value not in target:
            target.append(value)


def partition_entries(partition_state: dict[str, Any] | None) -> dict[str, dict]:
    """Real partition entries only.

    ``partition_state.json`` carries a reserved ``__meta__`` entry (shared
    artifact digest, flat-source marker). Iterating it as a partition minted an
    ``__meta__`` integration solution and a "business topology unavailable"
    unknown for it in every org view.
    """

    return {
        str(name): entry
        for name, entry in (partition_state or {}).items()
        if isinstance(entry, dict) and not str(name).startswith("__")
    }


class _SystemRegistry:
    """De-duplicates systems by name and hands out stable org-level ids."""

    def __init__(self) -> None:
        self._by_name: dict[str, BusinessSystem] = {}
        self._ids: set[str] = set()

    def resolve(
        self,
        *,
        name: str,
        kind: str = "software_system",
        description: str = "",
        evidence_ids: list[str] | None = None,
        aliases: list[str] | None = None,
        status: str = "inferred",
        confidence: float = 0.4,
        purpose: str = "",
        owner: str = "",
    ) -> BusinessSystem:
        key = name.strip().casefold()
        existing = self._by_name.get(key)
        if existing is not None:
            _extend_unique(existing.evidence_ids, list(evidence_ids or []))
            for alias in aliases or []:
                if alias and alias not in existing.aliases and alias != existing.name:
                    existing.aliases.append(alias)
            # A concrete data_system classification wins over a generic default,
            # and any classification wins over "unresolved".
            if kind in _SYSTEM_KINDS and (
                (existing.kind == "software_system" and kind == "data_system")
                or (existing.kind == "unresolved" and kind != "unresolved")
            ):
                existing.kind = kind  # type: ignore[assignment]
            if description and existing.description == _DEFAULT_SYSTEM_DESCRIPTION:
                existing.description = description
            if purpose and not existing.purpose:
                existing.purpose = purpose
            if owner and not existing.owner:
                existing.owner = owner
            existing.status = _stronger_status(existing.status, status)  # type: ignore[assignment]
            existing.confidence = max(existing.confidence, confidence)
            return existing
        system = BusinessSystem(
            id=_unique_id(f"sys-{_slug(name)}", self._ids),
            name=name.strip(),
            description=description or _DEFAULT_SYSTEM_DESCRIPTION,
            status=status if status in _CLAIM_STATUSES else "inferred",
            confidence=min(max(confidence, 0.0), 1.0),
            evidence_ids=list(dict.fromkeys(evidence_ids or [])),
            kind=kind if kind in _SYSTEM_KINDS else "software_system",
            aliases=list(dict.fromkeys(a for a in (aliases or []) if a and a != name)),
            purpose=purpose,
            owner=owner,
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
                mode=mode if mode in _SEGMENT_MODES else "unknown",
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


# --------------------------------------------------------------------------
# Stream field merge
# --------------------------------------------------------------------------


@dataclass
class _StreamContribution:
    partition: str
    artifact_ids: frozenset[str]
    contract_width: ContractWidth | None


def _prefer_scalar(current: str, incoming: str, *, unknown: str = "") -> str:
    """Keep the first established value; fill it when still empty/unknown."""

    if current and current != unknown:
        return current
    return incoming if incoming and incoming != unknown else current


def _conditions(raw: Any) -> list[StreamCondition]:
    result: list[StreamCondition] = []
    for item in raw or []:
        if not isinstance(item, dict) or not str(item.get("statement") or "").strip():
            continue
        kind = str(item.get("kind") or "unknown")
        result.append(
            StreamCondition(
                kind=kind
                if kind
                in {"trigger", "filter", "ordering", "schedule", "disabled", "guard", "unknown"}
                else "unknown",
                statement=str(item.get("statement")).strip(),
                evidence_note=str(item.get("evidence_note") or ""),
            )
        )
    return result


def _deployment(raw: Any) -> DeploymentAgreement | None:
    if not isinstance(raw, dict):
        return None
    try:
        return DeploymentAgreement.model_validate(raw)
    except ValueError:
        return None


def _contract_width(raw: Any) -> ContractWidth | None:
    if not isinstance(raw, dict):
        return None
    try:
        return ContractWidth.model_validate(raw)
    except ValueError:
        return None


def _merge_stream_fields(existing: BusinessDataStream, raw: dict[str, Any]) -> None:
    """Fold one more partition's view of an org stream into it.

    Merge rule (several partitions contributing to ONE org stream):

    * lists (protocols, evidence, artifacts, business objects, document refs,
      conditions) are unioned, de-duplicated, first-seen order;
    * scalars keep the first established value (sorted partition order) and are
      only filled while still empty / ``unknown`` / ``not_established`` -- a
      later partition never overwrites an earlier non-empty verdict;
    * status: the stronger status wins; confidence: the maximum;
    * deployment: the first agreement that is not ``unknown`` wins;
    * contract width is resolved once all partitions are in, see
      :func:`_resolve_contract_width`.
    """

    existing.mechanism = _prefer_scalar(existing.mechanism, str(raw.get("mechanism") or ""))
    existing.generation = _prefer_scalar(existing.generation, str(raw.get("generation") or ""))
    existing.key_form = _prefer_scalar(existing.key_form, str(raw.get("key_form") or ""))
    existing.verdict = _prefer_scalar(existing.verdict, str(raw.get("verdict") or ""))
    direction = str(raw.get("direction_pattern") or "unknown")
    if existing.direction_pattern == "unknown" and direction in {"push", "pull", "hybrid"}:
        existing.direction_pattern = direction  # type: ignore[assignment]
    lifecycle = str(raw.get("lifecycle") or "unknown")
    if existing.lifecycle == "unknown" and lifecycle in {
        "active",
        "duplicate",
        "stopped",
        "dormant",
        "reference_only",
    }:
        existing.lifecycle = lifecycle  # type: ignore[assignment]
    requiredness = str(raw.get("requiredness") or "not_established")
    if existing.requiredness == "not_established" and requiredness in {
        "required",
        "optional",
        "conditional",
    }:
        existing.requiredness = requiredness  # type: ignore[assignment]
    if existing.description.startswith("Integration flow from ") and raw.get("description"):
        existing.description = str(raw["description"])
    if existing.business_summary.startswith("Scanner-backed integration between ") and raw.get(
        "business_summary"
    ):
        existing.business_summary = str(raw["business_summary"])
    _extend_unique(existing.document_refs, _strs(raw.get("document_refs")))
    seen_conditions = {(item.kind, item.statement.casefold()) for item in existing.conditions}
    for condition in _conditions(raw.get("conditions")):
        key = (condition.kind, condition.statement.casefold())
        if key not in seen_conditions:
            seen_conditions.add(key)
            existing.conditions.append(condition)
    incoming_deployment = _deployment(raw.get("deployment"))
    if incoming_deployment is not None and (
        existing.deployment is None
        or (
            existing.deployment.agreement == "unknown"
            and incoming_deployment.agreement != "unknown"
        )
    ):
        existing.deployment = incoming_deployment


def _resolve_contract_width(
    contributions: list[_StreamContribution],
) -> ContractWidth | None:
    """One org-level contract width from N partition widths.

    * one partition reports a width -> take it verbatim;
    * all reported widths identical -> that width (the same design seen twice,
      e.g. a shared REFERENCES job);
    * the reporting partitions implement the stream with DISJOINT designs ->
      each count is summed (a count unknown in any contributor is unknown in
      the sum -- a partial sum would under-report the funnel), and the note
      says it is a sum;
    * overlapping designs with different widths -> keep the first partition's
      width and record the disagreement in the note. Summing would count the
      shared design twice; picking silently would hide the disagreement.
    """

    widths = [item for item in contributions if item.contract_width is not None]
    if not widths:
        return None
    first = widths[0].contract_width
    assert first is not None
    if len(widths) == 1:
        return first.model_copy()
    dumped = [item.contract_width.model_dump() for item in widths if item.contract_width]
    if all(value == dumped[0] for value in dumped):
        return first.model_copy()
    artifact_sets = [item.artifact_ids for item in widths]
    disjoint = all(
        not (artifact_sets[i] & artifact_sets[j])
        for i in range(len(artifact_sets))
        for j in range(i + 1, len(artifact_sets))
    )
    if disjoint:

        def total(attr: str) -> int | None:
            values = [getattr(item.contract_width, attr) for item in widths]
            return None if any(value is None for value in values) else sum(values)

        notes = [item.contract_width.note for item in widths if item.contract_width and item.contract_width.note]
        note = (
            "Summed across sub-projects with disjoint implementing designs ("
            + ", ".join(item.partition for item in widths)
            + ")"
        )
        if notes:
            note += ": " + "; ".join(dict.fromkeys(notes))
        return ContractWidth(
            offered=total("offered"),
            mapped=total("mapped"),
            written=total("written"),
            note=note,
        )

    def triple(width: ContractWidth) -> str:
        return "/".join("?" if v is None else str(v) for v in (width.offered, width.mapped, width.written))

    disagreements = [
        f"{item.partition} reports offered/mapped/written {triple(item.contract_width)}"
        for item in widths[1:]
        if item.contract_width is not None
        and item.contract_width.model_dump() != first.model_dump()
    ]
    note = "; ".join(
        part
        for part in (
            first.note,
            f"width from {widths[0].partition}; overlapping designs disagree: "
            + "; ".join(disagreements),
        )
        if part
    )
    return ContractWidth(
        offered=first.offered, mapped=first.mapped, written=first.written, note=note
    )


# --------------------------------------------------------------------------
# Cross-project edge grounding
# --------------------------------------------------------------------------


def _ground_application_edge(
    bundle: "BusinessTopologyEvidenceBundle",
    source_name: str,
    dest_name: str,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Resolve an index application edge to scanner evidence, or nothing.

    Returns ``(edge_ids, source_identity_ids, destination_identity_ids,
    artifact_ids)``. The cross-project index is derived from the landscape's
    application edges, which the evidence ledger records as ``scanner_edge``
    rows between ``scanner_application`` identities. Citing those rows -- not a
    synthetic ``cross-project-application-edge:`` id no ledger contains -- is
    what lets the edge pass the same directional check as any other stream.
    """

    from mcp_server.services.business_topology.validation import (
        _directional_artifact_ids,
    )

    def identities(name: str) -> set[str]:
        wanted = name.strip().casefold()
        return {
            evidence_id
            for evidence_id, record in bundle.evidence_records.items()
            if bundle.evidence_types.get(evidence_id)
            in {"scanner_application", "scanner_system"}
            and str(record.get("name") or "").strip().casefold() == wanted
            and str(record.get("scanner_id") or "").strip()
        }

    source_ids = identities(source_name)
    dest_ids = identities(dest_name)
    if not source_ids or not dest_ids:
        return [], [], [], []
    edge_ids = sorted(
        evidence_id
        for evidence_id, record in bundle.evidence_records.items()
        if bundle.evidence_types.get(evidence_id) == "scanner_edge"
        and record.get("source_identity_evidence_id") in source_ids
        and record.get("destination_identity_evidence_id") in dest_ids
    )
    if not edge_ids:
        return [], [], [], []
    records = [bundle.evidence_records[evidence_id] for evidence_id in edge_ids]
    return (
        edge_ids,
        sorted({str(r["source_identity_evidence_id"]) for r in records}),
        sorted({str(r["destination_identity_evidence_id"]) for r in records}),
        sorted(_directional_artifact_ids(edge_ids, bundle)),
    )


# --------------------------------------------------------------------------
# Merge
# --------------------------------------------------------------------------


@dataclass
class _MergeOutcome:
    topology: BusinessTopology | None
    contributing: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    unpublished: list[str] = field(default_factory=list)


def _default_fingerprint(entries: dict[str, dict]) -> str:
    """Fingerprint when the caller does not name the current scan.

    Only an unambiguous value is trustworthy: when partitions were published
    from different scans, no single recorded digest identifies the merge.
    """

    fingerprints = {
        str(entry.get("scan_fingerprint"))
        for entry in entries.values()
        if entry.get("scan_fingerprint")
    }
    return next(iter(fingerprints)) if len(fingerprints) == 1 else ""


def _merge(
    project_id: str,
    *,
    project_root: Path,
    scan_fingerprint: str | None,
    current_partitions: Collection[str] | None,
    bundle: "BusinessTopologyEvidenceBundle | None",
) -> _MergeOutcome | None:
    topo_dir = project_root / "output" / project_id / "topology"
    raw_state = _load_json(topo_dir / "partition_state.json")
    if not raw_state:
        return None
    entries = partition_entries(raw_state)
    if not entries:
        return None

    index = _load_json(topo_dir / "cross_project_index.json") or {}
    fingerprint = (
        scan_fingerprint if scan_fingerprint is not None else _default_fingerprint(entries)
    )
    current = set(current_partitions or ())

    registry = _SystemRegistry()
    streams: dict[tuple[str, str, str], BusinessDataStream] = {}
    stream_ids_taken: set[str] = set()
    contributions: dict[str, list[_StreamContribution]] = {}
    objects: dict[str, BusinessObject] = {}
    paths: dict[str, DeliveryPath] = {}
    transformations: list[BusinessTransformation] = []
    business_rules: list[BusinessRule] = []
    controls: list[BusinessControl] = []
    risks: list[BusinessRisk] = []
    unknowns: list[BusinessUnknown] = []
    observations: list[RuntimeObservation] = []
    conflicts: list[BusinessConflict] = []
    artifact_annotations: list[ArtifactAnnotation] = []
    component_annotations: list[ComponentAnnotation] = []
    lineage_annotations: list[LineageAnnotation] = []
    solution_parts: dict[str, list[dict[str, Any]]] = {}
    solution_artifacts: dict[str, list[str]] = {}
    total_artifacts = cited_artifacts = total_evidence = cited_evidence = 0
    contributing_logical: set[str] = set()
    outcome = _MergeOutcome(topology=None)
    merge_notes: list[str] = []

    for partition_name in sorted(entries):
        entry = entries[partition_name]
        topology_path = _partition_topology_path(project_root, project_id, partition_name)
        topology = _load_json(topology_path)
        if not topology:
            outcome.unpublished.append(partition_name)
            continue
        report = _load_json(topology_path.parent / "validation_report.json") or {}
        if report.get("validated") is False:
            # A partition that did not itself validate is skipped: its systems
            # and streams are not trustworthy org-level facts.
            outcome.unpublished.append(partition_name)
            continue
        if fingerprint and not (
            str(entry.get("scan_fingerprint") or "") == fingerprint
            or partition_name in current
        ):
            # Published from an older scan and changed since: its claims are
            # not facts about the current scan.
            outcome.stale.append(partition_name)
            continue

        logical_name = _logical_subproject(partition_name)
        solution_ref = f"int-sol-{_slug(logical_name)}"
        contributing_logical.add(logical_name)
        outcome.contributing.append(partition_name)
        coverage = topology.get("coverage") or {}
        total_artifacts += int(coverage.get("total_artifacts") or 0)
        cited_artifacts += int(coverage.get("cited_artifacts") or 0)
        total_evidence += int(coverage.get("total_evidence") or 0)
        cited_evidence += int(coverage.get("cited_evidence") or 0)

        def items(key: str) -> list[dict[str, Any]]:
            return [item for item in topology.get(key) or [] if isinstance(item, dict)]

        def ns(local_id: Any, _partition: str = partition_name) -> str:
            return _namespaced(_partition, str(local_id))

        # Every partition-local claim id -> its org id. Conflicts may reference
        # any claim, so the map spans all collections.
        local_to_org_id: dict[str, str] = {}

        # --- systems --------------------------------------------------------
        local_to_org: dict[str, BusinessSystem] = {}
        for raw in items("systems"):
            system = registry.resolve(
                name=str(raw.get("name") or raw.get("id") or "unknown"),
                kind=str(raw.get("kind") or "software_system"),
                description=str(raw.get("description") or ""),
                evidence_ids=_strs(raw.get("evidence_ids")),
                aliases=_strs(raw.get("aliases")),
                status=str(raw.get("status") or "inferred"),
                confidence=float(raw.get("confidence") or 0.4),
                purpose=str(raw.get("purpose") or ""),
                owner=str(raw.get("owner") or ""),
            )
            local_to_org[str(raw.get("id"))] = system
            local_to_org_id[str(raw.get("id"))] = system.id

        # --- integration solutions (folded into the logical sub-project) ---
        for raw in items("integration_solutions"):
            solution_parts.setdefault(logical_name, []).append(raw)
            local_to_org_id[str(raw.get("id"))] = solution_ref

        # --- business objects (streams reference them by org id) -----------
        local_object_to_org: dict[str, str] = {}
        for raw in items("business_objects"):
            name = str(raw.get("name") or "").strip()
            if not name:
                continue
            org_id = f"bo-{_slug(name)}"
            local_object_to_org[str(raw.get("id"))] = org_id
            local_to_org_id[str(raw.get("id"))] = org_id
            existing_object = objects.get(org_id)
            aliases = _strs(raw.get("aliases"))
            status = str(raw.get("status") or "inferred")
            if existing_object is None:
                objects[org_id] = BusinessObject(
                    id=org_id,
                    name=name,
                    description=str(raw.get("description") or ""),
                    status=status,
                    confidence=float(raw.get("confidence") or 0.6),
                    evidence_ids=_strs(raw.get("evidence_ids")),
                    aliases=[alias for alias in aliases if alias != name],
                )
            else:
                for alias in aliases:
                    if alias not in existing_object.aliases and alias != existing_object.name:
                        existing_object.aliases.append(alias)
                _extend_unique(existing_object.evidence_ids, _strs(raw.get("evidence_ids")))
                existing_object.status = _stronger_status(existing_object.status, status)  # type: ignore[assignment]
                existing_object.confidence = max(
                    existing_object.confidence, float(raw.get("confidence") or 0.0)
                )
                if not existing_object.description and raw.get("description"):
                    existing_object.description = str(raw["description"])

        # Local ids of the annotative collections, known BEFORE streams are
        # merged so stream-level refs can be re-pointed in one pass.
        local_ids = {
            key: {str(raw.get("id")) for raw in items(key) if raw.get("id")}
            for key in ("controls", "risks", "unknowns", "runtime_observations", "conflicts")
        }

        def repoint(refs: Any, collection: str) -> list[str]:
            return [ns(ref) for ref in _strs(refs) if ref in local_ids[collection]]

        local_paths = {str(raw.get("id")): raw for raw in items("delivery_paths")}
        local_stream_to_org: dict[str, str] = {}
        local_path_to_org: dict[str, str] = {}

        # --- data streams ---------------------------------------------------
        for raw in items("data_streams"):
            source = local_to_org.get(str(raw.get("source_system_id")))
            dest = local_to_org.get(str(raw.get("destination_system_id")))
            if source is None or dest is None or source.id == dest.id:
                continue
            org_object_ids = [
                local_object_to_org[ref]
                for ref in _strs(raw.get("business_object_ids"))
                if ref in local_object_to_org
            ]
            # The business object is part of the identity: Maximo -> 4PS
            # WorkOrder and Maximo -> 4PS Invoice are two flows, not one edge.
            object_key = org_object_ids[0] if org_object_ids else ""
            key = (source.id, dest.id, object_key)
            evidence_ids = _strs(raw.get("evidence_ids"))
            artifact_ids = _strs(raw.get("artifact_ids"))
            protocols = _strs(raw.get("protocols"))
            status = str(raw.get("status") or "inferred")
            existing = streams.get(key)
            if existing is None:
                suffix = f"-{object_key[3:]}" if object_key else ""
                merged = BusinessDataStream(
                    id=_unique_id(
                        f"ds-{_slug(source.name)}--{_slug(dest.name)}{suffix}",
                        stream_ids_taken,
                    ),
                    name=str(raw.get("name") or "") or f"{source.name} -> {dest.name}",
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
                    integration_solution_ids=[solution_ref],
                    protocols=list(dict.fromkeys(protocols)),
                    artifact_ids=list(dict.fromkeys(artifact_ids)),
                )
                _merge_stream_fields(merged, raw)
                streams[key] = merged
                existing = merged
            else:
                existing.status = _stronger_status(existing.status, status)  # type: ignore[assignment]
                existing.confidence = max(
                    existing.confidence, float(raw.get("confidence") or 0.0)
                )
                _extend_unique(existing.evidence_ids, evidence_ids)
                _extend_unique(existing.artifact_ids, artifact_ids)
                _extend_unique(existing.protocols, protocols)
                _extend_unique(existing.business_object_ids, org_object_ids)
                _extend_unique(existing.integration_solution_ids, [solution_ref])
                _merge_stream_fields(existing, raw)
            _extend_unique(existing.control_ids, repoint(raw.get("control_ids"), "controls"))
            _extend_unique(existing.risk_ids, repoint(raw.get("risk_ids"), "risks"))
            _extend_unique(existing.unknown_ids, repoint(raw.get("unknown_ids"), "unknowns"))
            _extend_unique(
                existing.runtime_observation_ids,
                repoint(raw.get("runtime_observation_ids"), "runtime_observations"),
            )
            _extend_unique(existing.conflict_ids, repoint(raw.get("conflict_ids"), "conflicts"))
            contributions.setdefault(existing.id, []).append(
                _StreamContribution(
                    partition=partition_name,
                    artifact_ids=frozenset(artifact_ids),
                    contract_width=_contract_width(raw.get("contract_width")),
                )
            )
            local_stream_to_org[str(raw.get("id"))] = existing.id
            local_to_org_id[str(raw.get("id"))] = existing.id
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
                    local_path_to_org[str(local_path.get("id"))] = merged_path.id
                    local_to_org_id[str(local_path.get("id"))] = merged_path.id

        def stream_refs(raw: dict[str, Any]) -> list[str]:
            return list(
                dict.fromkeys(
                    local_stream_to_org[ref]
                    for ref in _strs(raw.get("stream_ids"))
                    if ref in local_stream_to_org
                )
            )

        # --- transformations ------------------------------------------------
        local_transformation_ids: dict[str, str] = {}
        for raw in items("transformations"):
            org_stream_ids = stream_refs(raw)
            if not org_stream_ids:
                continue
            transformation_id = f"transform-{_slug(logical_name)}-{len(transformations) + 1}"
            if raw.get("id"):
                local_transformation_ids[str(raw["id"])] = transformation_id
                local_to_org_id[str(raw["id"])] = transformation_id
            transformations.append(
                BusinessTransformation(
                    id=transformation_id,
                    name=str(raw.get("name") or "Transformation"),
                    description=str(raw.get("description") or ""),
                    status=str(raw.get("status") or "inferred"),
                    confidence=float(raw.get("confidence") or 0.6),
                    evidence_ids=_strs(raw.get("evidence_ids")),
                    stream_ids=org_stream_ids,
                    integration_solution_ids=[solution_ref],
                    artifact_ids=_strs(raw.get("artifact_ids")),
                    kind=str(raw.get("kind") or "unknown"),
                    target_field=str(raw.get("target_field") or ""),
                    mappings=[
                        TransformationMapping(
                            source=str(mapping.get("source") or ""),
                            target=str(mapping.get("target") or ""),
                            mapping_type=str(mapping.get("mapping_type") or "unknown"),
                            expression=str(mapping.get("expression") or ""),
                            category=str(mapping.get("category") or ""),
                            reaches_target=str(
                                mapping.get("reaches_target") or "unknown"
                            ),
                            note=str(mapping.get("note") or ""),
                        )
                        for mapping in raw.get("mappings") or []
                        if isinstance(mapping, dict)
                        and (mapping.get("source") or mapping.get("target"))
                    ],
                )
            )

        # --- business rules -------------------------------------------------
        # Business rules travel to the org level like transformations do: a
        # rule is what the business must confirm or retire, so dropping it in
        # the merge would make the org view look decision-free while every
        # sub-project view showed decisions.
        for raw in items("business_rules"):
            statement = str(raw.get("statement") or "").strip()
            if not statement:
                continue
            rule_id = f"rule-{_slug(logical_name)}-{len(business_rules) + 1}"
            if raw.get("id"):
                local_to_org_id[str(raw["id"])] = rule_id
            business_rules.append(
                BusinessRule(
                    id=rule_id,
                    name=str(raw.get("name") or "Business rule"),
                    description=str(raw.get("description") or ""),
                    status=str(raw.get("status") or "inferred"),
                    confidence=float(raw.get("confidence") or 0.6),
                    evidence_ids=_strs(raw.get("evidence_ids")),
                    kind=str(raw.get("kind") or "unknown"),
                    statement=statement,
                    inputs=_strs(raw.get("inputs")),
                    output=str(raw.get("output") or ""),
                    stream_ids=stream_refs(raw),
                    transformation_ids=[
                        local_transformation_ids[ref]
                        for ref in _strs(raw.get("transformation_ids"))
                        if ref in local_transformation_ids
                    ],
                    owner=str(raw.get("owner") or ""),
                    owner_question=str(raw.get("owner_question") or ""),
                )
            )

        # --- controls / risks / unknowns ------------------------------------
        for raw in items("controls"):
            local_to_org_id[str(raw.get("id"))] = ns(raw.get("id"))
            controls.append(
                BusinessControl(
                    id=ns(raw.get("id")),
                    name=str(raw.get("name") or "Control"),
                    description=str(raw.get("description") or ""),
                    status=str(raw.get("status") or "inferred"),
                    confidence=float(raw.get("confidence") or 0.5),
                    evidence_ids=_strs(raw.get("evidence_ids")),
                    stream_ids=stream_refs(raw),
                    control_type=str(raw.get("control_type") or ""),
                )
            )
        for raw in items("risks"):
            local_to_org_id[str(raw.get("id"))] = ns(raw.get("id"))
            severity = str(raw.get("severity") or "unknown")
            risks.append(
                BusinessRisk(
                    id=ns(raw.get("id")),
                    name=str(raw.get("name") or "Risk"),
                    description=str(raw.get("description") or ""),
                    status=str(raw.get("status") or "inferred"),
                    confidence=float(raw.get("confidence") or 0.5),
                    evidence_ids=_strs(raw.get("evidence_ids")),
                    stream_ids=stream_refs(raw),
                    severity=severity
                    if severity in {"low", "medium", "high", "critical"}
                    else "unknown",
                )
            )
        for raw in items("unknowns"):
            question = str(raw.get("question") or "").strip()
            if not question:
                continue
            local_to_org_id[str(raw.get("id"))] = ns(raw.get("id"))
            unknowns.append(
                BusinessUnknown(
                    id=ns(raw.get("id")),
                    name=str(raw.get("name") or "Open question"),
                    description=str(raw.get("description") or ""),
                    status=str(raw.get("status") or "not_established"),
                    confidence=float(raw.get("confidence") or 0.2),
                    evidence_ids=_strs(raw.get("evidence_ids")),
                    stream_ids=stream_refs(raw),
                    question=question,
                )
            )

        # --- runtime observations ------------------------------------------
        for raw in items("runtime_observations"):
            org_streams = stream_refs(raw)
            org_paths = [
                local_path_to_org[ref]
                for ref in _strs(raw.get("delivery_path_ids"))
                if ref in local_path_to_org
            ]
            behaviour = str(raw.get("observed_behavior") or "").strip()
            if not (org_streams or org_paths) or not behaviour:
                # An observation that annotates nothing at the org level has
                # nothing to say; the validator would reject it anyway.
                continue
            local_to_org_id[str(raw.get("id"))] = ns(raw.get("id"))
            observations.append(
                RuntimeObservation(
                    id=ns(raw.get("id")),
                    name=str(raw.get("name") or "Runtime observation"),
                    description=str(raw.get("description") or ""),
                    status=str(raw.get("status") or "observed"),
                    confidence=float(raw.get("confidence") or 0.6),
                    evidence_ids=_strs(raw.get("evidence_ids")),
                    stream_ids=org_streams,
                    delivery_path_ids=list(dict.fromkeys(org_paths)),
                    observed_behavior=behaviour,
                    observation_window=str(raw.get("observation_window") or ""),
                    kind=str(raw.get("kind") or "unknown"),
                    metrics=[
                        RuntimeMetric.model_validate(metric)
                        for metric in raw.get("metrics") or []
                        if isinstance(metric, dict)
                        and metric.get("label")
                        and metric.get("value") is not None
                    ],
                    source=str(raw.get("source") or ""),
                )
            )

        # --- annotations ----------------------------------------------------
        for raw in items("artifact_annotations"):
            if not raw.get("artifact_id"):
                continue
            artifact_annotations.append(
                ArtifactAnnotation(
                    id=ns(raw.get("id")),
                    name=str(raw.get("name") or "Artifact annotation"),
                    description=str(raw.get("description") or ""),
                    status=str(raw.get("status") or "inferred"),
                    confidence=float(raw.get("confidence") or 0.5),
                    evidence_ids=_strs(raw.get("evidence_ids")),
                    artifact_id=str(raw["artifact_id"]),
                    business_role=str(raw.get("business_role") or ""),
                )
            )
        for raw in items("component_annotations"):
            if not raw.get("component_id") or not raw.get("human_explanation"):
                continue
            component_annotations.append(
                ComponentAnnotation(
                    id=ns(raw.get("id")),
                    name=str(raw.get("name") or "Component annotation"),
                    description=str(raw.get("description") or ""),
                    status=str(raw.get("status") or "inferred"),
                    confidence=float(raw.get("confidence") or 0.5),
                    evidence_ids=_strs(raw.get("evidence_ids")),
                    component_id=str(raw["component_id"]),
                    human_explanation=str(raw["human_explanation"]),
                )
            )
        for raw in items("lineage_annotations"):
            if not raw.get("lineage_id") or not raw.get("business_meaning"):
                continue
            lineage_annotations.append(
                LineageAnnotation(
                    id=ns(raw.get("id")),
                    name=str(raw.get("name") or "Lineage annotation"),
                    description=str(raw.get("description") or ""),
                    status=str(raw.get("status") or "inferred"),
                    confidence=float(raw.get("confidence") or 0.5),
                    evidence_ids=_strs(raw.get("evidence_ids")),
                    lineage_id=str(raw["lineage_id"]),
                    business_meaning=str(raw["business_meaning"]),
                    business_object_id=local_object_to_org.get(
                        str(raw.get("business_object_id") or "")
                    ),
                )
            )

        # --- conflicts (last: they may reference any claim above) ----------
        for raw in items("conflicts"):
            conflict_id = ns(raw.get("id"))
            subject_refs = list(
                dict.fromkeys(
                    local_to_org_id[ref]
                    for ref in _strs(raw.get("subject_refs"))
                    if ref in local_to_org_id and local_to_org_id[ref] != conflict_id
                )
            )
            conflict_type = str(raw.get("conflict_type") or "unresolved")
            if not subject_refs:
                continue
            conflicts.append(
                BusinessConflict(
                    id=conflict_id,
                    name=str(raw.get("name") or "Conflict"),
                    description=str(raw.get("description") or ""),
                    status=str(raw.get("status") or "inferred"),
                    confidence=float(raw.get("confidence") or 0.5),
                    evidence_ids=_strs(raw.get("evidence_ids")),
                    conflict_type=conflict_type
                    if conflict_type
                    in {
                        "supporting_vs_implementation",
                        "runtime_vs_implementation",
                        "supporting_sources_disagree",
                    }
                    else "unresolved",
                    subject_refs=subject_refs,
                    implementation_evidence_ids=_strs(raw.get("implementation_evidence_ids")),
                    supporting_evidence_ids=_strs(raw.get("supporting_evidence_ids")),
                    resolution=str(raw.get("resolution") or ""),
                )
            )

    if not registry.values() and not streams:
        outcome.topology = None
        return outcome

    # Contract widths need every contributor, so they resolve after the loop.
    stream_by_id = {stream.id: stream for stream in streams.values()}
    for stream_id, items_ in contributions.items():
        stream_by_id[stream_id].contract_width = _resolve_contract_width(items_)

    # Drop stream refs whose target claim was not carried (e.g. an observation
    # that annotated nothing at the org level), so no dangling id survives.
    carried = {
        "control_ids": {item.id for item in controls},
        "risk_ids": {item.id for item in risks},
        "unknown_ids": {item.id for item in unknowns},
        "runtime_observation_ids": {item.id for item in observations},
        "conflict_ids": {item.id for item in conflicts},
    }
    for stream in streams.values():
        for attr, allowed in carried.items():
            setattr(stream, attr, [ref for ref in getattr(stream, attr) if ref in allowed])

    # --- Overlay: cross-project application edges (guarantee they exist) ------
    ungrounded_edges: list[str] = []
    for edge in index.get("application_edges") or []:
        if not isinstance(edge, dict):
            continue
        source_name = str(edge.get("source_application") or edge.get("from_subproject") or "")
        dest_name = str(edge.get("target_application") or edge.get("to_subproject") or "")
        if not source_name or not dest_name:
            continue
        mechanism = str(edge.get("mechanism") or "unknown")
        if bundle is not None:
            edge_ids, source_identity, dest_identity, edge_artifacts = (
                _ground_application_edge(bundle, source_name, dest_name)
            )
            if not edge_ids:
                # No scanner row establishes this direction: asserting it would
                # only be pruned again, so record the gap instead.
                ungrounded_edges.append(f"{source_name} -> {dest_name}")
                continue
            edge_evidence = edge_ids
        else:
            edge_ids, source_identity, dest_identity, edge_artifacts = [], [], [], []
            edge_evidence = [f"cross-project-application-edge:{source_name}->{dest_name}"]
        source = registry.resolve(name=source_name, evidence_ids=source_identity)
        dest = registry.resolve(name=dest_name, evidence_ids=dest_identity)
        if source.id == dest.id:
            continue
        # Keyed with an empty business object: a cross-project index edge carries
        # no business object, and must not silently absorb (or be absorbed by) a
        # named business flow between the same pair.
        key = (source.id, dest.id, "")
        existing = streams.get(key)
        if existing is None:
            streams[key] = BusinessDataStream(
                id=_unique_id(
                    f"ds-{_slug(source.name)}--{_slug(dest.name)}", stream_ids_taken
                ),
                name=f"{source.name} -> {dest.name}",
                description=(
                    f"Cross-project integration flow via {mechanism} from "
                    f"{source.name} to {dest.name}."
                ),
                status="inferred",
                confidence=0.4,
                evidence_ids=list(edge_evidence),
                source_system_id=source.id,
                destination_system_id=dest.id,
                business_summary=(
                    f"Cross-project index application edge ({mechanism})."
                ),
                artifact_ids=list(edge_artifacts),
                mechanism=mechanism if mechanism != "unknown" else "",
            )
        else:
            _extend_unique(existing.evidence_ids, list(edge_evidence))
            _extend_unique(existing.artifact_ids, list(edge_artifacts))
    if ungrounded_edges:
        merge_notes.append(
            f"{len(ungrounded_edges)} cross-project application edge(s) have no "
            "scanner edge evidence in the organization catalogue and are not "
            "asserted: " + ", ".join(ungrounded_edges[:10]) + "."
        )

    # --- Integration solutions: one per logical sub-project ------------------
    subprojects = index.get("subprojects") or {}
    logical_names = sorted(
        {str(name) for name in subprojects}
        | {_logical_subproject(name) for name in entries}
    )
    solutions: list[IntegrationSolution] = []
    for logical in logical_names:
        info = subprojects.get(logical) if isinstance(subprojects, dict) else None
        unit_count = int((info or {}).get("unit_count") or 0)
        parts = solution_parts.get(logical) or []
        artifact_ids = list(
            dict.fromkeys(
                [a for part in parts for a in _strs(part.get("artifact_ids"))]
                + (solution_artifacts.get(logical) or [])
            )
        )
        contributing = logical in contributing_logical
        evidence_ids = list(
            dict.fromkeys(
                [e for part in parts for e in _strs(part.get("evidence_ids"))]
                + artifact_ids
            )
        )
        status = "inferred"
        for part in parts:
            status = _stronger_status(status, str(part.get("status") or "inferred"))
        description = next(
            (str(part["description"]) for part in parts if part.get("description")),
            f"Integration sub-project '{logical}' ({unit_count} scanned units)."
            if unit_count
            else f"Integration sub-project '{logical}'.",
        )
        solutions.append(
            IntegrationSolution(
                id=f"int-sol-{_slug(logical)}",
                name=logical,
                description=description,
                # A sub-project that contributed nothing has no claim to make:
                # `not_established` with no evidence, instead of synthetic ids
                # that no ledger resolves.
                status=status if contributing else "not_established",
                confidence=max(
                    [0.5, *(float(part.get("confidence") or 0.0) for part in parts)]
                )
                if contributing
                else 0.2,
                evidence_ids=evidence_ids if contributing else [],
                platform=next(
                    (str(part["platform"]) for part in parts if part.get("platform")),
                    str(index.get("platform") or ""),
                ),
                role=next((str(part["role"]) for part in parts if part.get("role")), ""),
                artifact_ids=artifact_ids if contributing else [],
            )
        )
        if not contributing:
            stale_parts = [
                name for name in outcome.stale if _logical_subproject(name) == logical
            ]
            reason = (
                "belongs to an older scan and changed since"
                if stale_parts
                else "did not publish a validated business topology"
            )
            unknowns.append(
                BusinessUnknown(
                    id=f"unk-subproject-{_slug(logical)}",
                    name=f"{logical}: business topology unavailable",
                    description=(
                        f"Sub-project '{logical}' {reason}, so its systems and "
                        "streams are absent from the organization view."
                    ),
                    status="not_established",
                    confidence=0.2,
                    evidence_ids=[],
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
    missing = sorted(
        logical for logical in logical_names if logical not in contributing_logical
    )
    if missing:
        notes.append(
            "Sub-projects without a published topology: " + ", ".join(missing) + "."
        )
    if outcome.stale:
        notes.append(
            "Partition topologies excluded as belonging to an older scan: "
            + ", ".join(outcome.stale)
            + "."
        )
    notes.extend(merge_notes)

    outcome.topology = BusinessTopology(
        scan_fingerprint=fingerprint,
        metadata=BusinessTopologyMetadata(
            project_id=project_id,
            scan_fingerprint=fingerprint,
            generated_by=GENERATED_BY,
            model_name=GENERATED_BY,
        ),
        systems=registry.values(),
        integration_solutions=solutions,
        business_objects=list(objects.values()),
        data_streams=list(streams.values()),
        delivery_paths=list(paths.values()),
        transformations=transformations,
        business_rules=business_rules,
        controls=controls,
        runtime_observations=observations,
        conflicts=conflicts,
        risks=risks,
        unknowns=unknowns,
        artifact_annotations=artifact_annotations,
        component_annotations=component_annotations,
        lineage_annotations=lineage_annotations,
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
    return outcome


def build_composite_org_topology(
    project_id: str,
    *,
    project_root: Path,
    scan_fingerprint: str | None = None,
    current_partitions: Collection[str] | None = None,
    bundle: "BusinessTopologyEvidenceBundle | None" = None,
) -> BusinessTopology | None:
    """Merge per-partition topologies + cross-project index into one org view.

    UNVALIDATED -- serving or persisting callers must use
    :func:`build_validated_composite_org_topology`.

    ``scan_fingerprint`` is the CURRENT whole-scan digest; a partition whose
    recorded digest differs contributes only when named in
    ``current_partitions`` (the skip rule's "unchanged" verdict). ``None`` keeps
    the recorded digest when every partition agrees on one. ``bundle`` (the org
    evidence catalogue) lets cross-project edges cite real scanner rows.

    Returns ``None`` when the project is not a composite upload (no
    ``topology/partition_state.json``) or no partition published a topology.
    """

    outcome = _merge(
        project_id,
        project_root=project_root,
        scan_fingerprint=scan_fingerprint,
        current_partitions=current_partitions,
        bundle=bundle,
    )
    return outcome.topology if outcome is not None else None


# --------------------------------------------------------------------------
# Evidence + validation
# --------------------------------------------------------------------------

#: Phase 3.7's lane name for the documentation-grounded overlay. Only that lane
#: validates against the documentation evidence tier; every other lane is
#: validated -- and its coverage computed -- against the scanner-only ledger.
_DOC_TIER_LANES = frozenset({"flow_extraction"})


def load_publication_evidence_bundle(
    topology_root: Path,
) -> "BusinessTopologyEvidenceBundle":
    """The evidence bundle a published ``docs/_topology`` was validated against.

    The persisted ledger holds scanner evidence only. The documentation tier is
    registered at run time from the run's own documents and never written to
    the ledger, so a ``flow_extraction`` topology -- whose claims cite ``doc:``
    ids and whose coverage counts them -- could never re-validate at read time.
    ``state.json`` records the lane; the doc tier is re-registered from the
    published documents (``topology_root.parent``) for that lane only.

    Raises ``ValueError`` when the ledger is unreadable (as
    ``load_business_topology_evidence`` does).
    """

    from mcp_server.services.business_topology.doc_evidence import (
        discover_doc_filenames,
        register_doc_evidence,
    )
    from mcp_server.services.business_topology.evidence import (
        load_business_topology_evidence,
    )

    bundle = load_business_topology_evidence(topology_root)
    state = _load_json(topology_root / "state.json") or {}
    if str(state.get("lane") or "") in _DOC_TIER_LANES:
        docs_dir = topology_root.parent
        bundle, _ = register_doc_evidence(
            bundle, docs_dir, filenames=discover_doc_filenames(docs_dir)
        )
    return bundle


def build_org_evidence_bundle(
    project_id: str,
    *,
    project_root: Path,
    partitions: Collection[str],
    scan_fingerprint: str,
) -> "BusinessTopologyEvidenceBundle | None":
    """Union the contributing partitions' own evidence ledgers.

    The org merge re-uses partition claims verbatim, so the evidence they cite
    is exactly the union of the partitions' published ledgers (plus each one's
    documentation tier when its lane used it). Evidence ids are content hashes,
    so a design shared across partitions resolves to the same id and record.
    The bundle is stamped with the CURRENT whole-scan fingerprint: partition
    freshness was already decided by the caller, and a reused partition's
    ledger legitimately carries the digest it was published from.

    Returns ``None`` when no partition ledger is readable -- the merge cannot
    then be validated and must not be reported as validated.
    """

    from mcp_server.services.business_topology.evidence import (
        BusinessTopologyEvidenceBundle,
    )

    evidence_types: dict[str, str] = {}
    evidence_records: dict[str, dict[str, Any]] = {}
    loaded = 0
    for partition_name in sorted(partitions):
        root = _partition_topology_path(project_root, project_id, partition_name).parent
        try:
            bundle = load_publication_evidence_bundle(root)
        except (ValueError, OSError) as exc:
            logger.warning(
                "Partition evidence ledger unavailable for %s/%s: %s",
                project_id,
                partition_name,
                exc,
            )
            continue
        loaded += 1
        for evidence_id, evidence_type in bundle.evidence_types.items():
            if evidence_id in evidence_types and evidence_types[evidence_id] != evidence_type:
                logger.warning(
                    "Evidence id %s is typed differently across partitions (%s vs %s); "
                    "keeping the first",
                    evidence_id,
                    evidence_types[evidence_id],
                    evidence_type,
                )
                continue
            evidence_types.setdefault(evidence_id, evidence_type)
            record = bundle.evidence_records.get(evidence_id)
            if record is not None:
                evidence_records.setdefault(evidence_id, record)
    if not loaded:
        return None
    org_root = project_root / "output" / project_id / "topology"

    def typed(kind: str) -> frozenset[str]:
        return frozenset(key for key, value in evidence_types.items() if value == kind)

    return BusinessTopologyEvidenceBundle(
        root=org_root,
        overview_path=org_root / "factual_evidence_index.json",
        starter_pack_path=org_root / "_doc_pack_business_topology.md",
        evidence_ids_path=org_root / "evidence" / "evidence_ids.json",
        scan_fingerprint=scan_fingerprint,
        evidence_ids=frozenset(evidence_types),
        evidence_types=evidence_types,
        evidence_records=evidence_records,
        artifact_ids=typed("scanner_artifact"),
        component_ids=typed("scanner_component"),
        lineage_ids=typed("scanner_lineage"),
    )


@dataclass
class CompositeOrgTopologyResult:
    """A composite org topology plus the TRUE result of validating it."""

    project_id: str
    scan_fingerprint: str
    #: Present only when ``report.validated`` -- a failing merge is not served.
    topology: BusinessTopology | None
    report: "BusinessTopologyValidationReport"
    dropped_ids: list[str] = field(default_factory=list)
    contributing_partitions: list[str] = field(default_factory=list)
    stale_partitions: list[str] = field(default_factory=list)
    unpublished_partitions: list[str] = field(default_factory=list)
    #: The org evidence bundle ``topology`` was validated against, so anything
    #: layered on afterwards (a stale curated overlay) is judged by the same
    #: provenance rather than inheriting the flag.
    bundle: "BusinessTopologyEvidenceBundle | None" = None

    @property
    def validated(self) -> bool:
        return self.topology is not None and self.report.validated

    @property
    def all_stale(self) -> bool:
        return not self.contributing_partitions and bool(self.stale_partitions)

    def report_payload(self) -> dict[str, Any]:
        payload = self.report.model_dump(mode="json")
        payload.update(
            {
                "generated_by": GENERATED_BY,
                "pruned_count": len(self.dropped_ids),
                "pruned_claim_ids": list(self.dropped_ids),
                "contributing_partitions": list(self.contributing_partitions),
                "stale_partitions": list(self.stale_partitions),
                "unpublished_partitions": list(self.unpublished_partitions),
            }
        )
        return payload

    def write(self, topology_dir: Path) -> None:
        """Persist exactly what is (or is not) served.

        ``validation_report.json`` always carries the real outcome;
        ``business_topology.json`` exists only for a validated merge, mirroring
        the per-run publication (a failing overlay is removed, never left for a
        reader to mistake for the current one).
        """

        topology_dir.mkdir(parents=True, exist_ok=True)
        (topology_dir / "validation_report.json").write_text(
            json.dumps(self.report_payload(), indent=2), encoding="utf-8"
        )
        topology_path = topology_dir / "business_topology.json"
        if self.validated and self.topology is not None:
            topology_path.write_text(
                json.dumps(self.topology.model_dump(mode="json"), indent=2),
                encoding="utf-8",
            )
        else:
            topology_path.unlink(missing_ok=True)


def resolve_current_partitions(
    project_id: str,
    *,
    project_root: Path,
    scan_fingerprint: str,
) -> set[str]:
    """Partitions whose published topology describes the current scan.

    Cheap path: the partition was published from exactly this whole-scan
    digest. Otherwise the ONE skip rule decides (``decide_partition_actions``
    via ``build_generation_plan``): a partition it would reuse as
    ``unchanged`` is current even though its recorded whole-scan digest is
    older -- duplicating that predicate here would let the org view and the
    generation plan disagree. Never raises.
    """

    entries = partition_entries(
        _load_json(project_root / "output" / project_id / "topology" / "partition_state.json")
    )
    current = {
        name
        for name, entry in entries.items()
        if str(entry.get("scan_fingerprint") or "") == scan_fingerprint
    }
    if len(current) == len(entries):
        return current
    try:
        from mcp_server.services.partition_orchestrator import (
            REASON_UNCHANGED,
            build_generation_plan,
        )

        plan = build_generation_plan(project_id, resume=True)
        current.update(
            decision.name
            for decision in plan.decisions
            if decision.reason == REASON_UNCHANGED
        )
    except Exception as exc:  # noqa: BLE001 - freshness degrades to "stale", never a 500
        logger.warning(
            "Partition freshness unavailable for %s (%s); treating recorded-digest "
            "mismatches as stale",
            project_id,
            exc,
        )
    return current


def build_validated_composite_org_topology(
    project_id: str,
    *,
    project_root: Path,
    scan_fingerprint: str,
    current_partitions: Collection[str] | None = None,
) -> CompositeOrgTopologyResult | None:
    """Merge, then validate for real -- the only entry point for serving.

    Claim-local defects are pruned (``prune_to_valid``, as Phase 3.7 does) and
    reported; global defects (duplicate ids, fingerprint, secrets) leave the
    result unvalidated. A missing org evidence catalogue is itself a failure:
    an unvalidatable merge is never reported as validated.

    Returns ``None`` when the project is not a composite upload.
    """

    from mcp_server.services.business_topology.pruning import prune_to_valid
    from mcp_server.services.business_topology.validation import (
        BusinessTopologyValidationReport,
    )

    def failure(message: str) -> BusinessTopologyValidationReport:
        return BusinessTopologyValidationReport(
            validated=False,
            scan_fingerprint=scan_fingerprint,
            project_id=project_id,
            errors=[message],
        )

    if not scan_fingerprint:
        # Without the current scan's identity there is nothing to validate the
        # merge against; an empty digest would "match" an empty topology stamp.
        if not partition_entries(
            _load_json(project_root / "output" / project_id / "topology" / "partition_state.json")
        ):
            return None
        return CompositeOrgTopologyResult(
            project_id=project_id,
            scan_fingerprint="",
            topology=None,
            report=failure("current scan fingerprint is unavailable"),
        )

    if current_partitions is None:
        current_partitions = resolve_current_partitions(
            project_id, project_root=project_root, scan_fingerprint=scan_fingerprint
        )

    # First pass decides which partitions contribute; their ledgers form the
    # bundle, which the second pass needs to ground cross-project edges.
    probe = _merge(
        project_id,
        project_root=project_root,
        scan_fingerprint=scan_fingerprint,
        current_partitions=current_partitions,
        bundle=None,
    )
    if probe is None:
        return None

    result = CompositeOrgTopologyResult(
        project_id=project_id,
        scan_fingerprint=scan_fingerprint,
        topology=None,
        report=failure("no sub-project published a current business topology"),
        contributing_partitions=list(probe.contributing),
        stale_partitions=list(probe.stale),
        unpublished_partitions=list(probe.unpublished),
    )
    if probe.topology is None:
        return result

    bundle = build_org_evidence_bundle(
        project_id,
        project_root=project_root,
        partitions=probe.contributing,
        scan_fingerprint=scan_fingerprint,
    )
    if bundle is None:
        result.report = failure(
            "organization evidence catalogue is unavailable: no contributing "
            "partition evidence ledger could be read, so the merged topology "
            "cannot be validated"
        )
        return result

    index = _load_json(
        project_root / "output" / project_id / "topology" / "cross_project_index.json"
    ) or {}
    merged = probe.topology
    if index.get("application_edges"):
        # Only cross-project edges depend on the bundle (they are grounded in
        # its scanner rows), so only then is a second pass needed.
        outcome = _merge(
            project_id,
            project_root=project_root,
            scan_fingerprint=scan_fingerprint,
            current_partitions=current_partitions,
            bundle=bundle,
        )
        assert outcome is not None and outcome.topology is not None
        merged = outcome.topology
    topology, report, dropped = prune_to_valid(
        merged, bundle=bundle, project_id=project_id
    )
    result.report = report
    result.dropped_ids = list(dropped)
    result.topology = topology if report.validated else None
    result.bundle = bundle
    return result


__all__ = [
    "CompositeOrgTopologyResult",
    "GENERATED_BY",
    "build_composite_org_topology",
    "build_org_evidence_bundle",
    "build_validated_composite_org_topology",
    "load_publication_evidence_bundle",
    "partition_entries",
    "resolve_current_partitions",
]
