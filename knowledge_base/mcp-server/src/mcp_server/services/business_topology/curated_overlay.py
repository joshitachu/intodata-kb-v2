"""Analyst-curated topology: precedence when current, judgement overlay when stale.

An analyst may hand-curate ``output/{pid}/topology/business_topology.curated.json``
(same ``BusinessTopology`` contract). Two cases, one rule each:

* **Fresh** -- its ``scan_fingerprint`` equals the current scan. The file wins
  over EVERY generated topology (single-run and composite alike). It used to be
  consulted only in the composite branch, which the loader reaches only when no
  single-run ``docs/_topology/business_topology.json`` exists, so any single-run
  publish silently hid it. It is parsed with ``BusinessTopology.model_validate``
  -- the previous branch served the raw dict unchecked.

* **Stale** -- the scan moved on. The curated scanner-level facts (artifact ids,
  contract widths, runtime numbers, deployment agreement) describe an older
  scan, so the GENERATED topology is served. What an analyst decided is not
  scan-bound, though, and used to be discarded wholesale. That judgement layer
  is overlaid onto the generated topology: business rules, conflicts, unknowns,
  risks, the stream verdict / key form / lifecycle / requiredness / generation,
  and system owner / purpose.

Matching is by identity the scanner cannot rename: systems by normalised name or
alias, business objects likewise, streams by (source system, destination
system, business object). Nothing is matched fuzzily. A curated item that
cannot be placed is NOT dropped silently: it is returned in
:attr:`CuratedOverlayResult.unmatched` (ids per kind) and summarised in the
topology's ``coverage.notes``, which is part of the served contract.

Overlaid claims are then held to the SAME provenance as the topology they are
laid on (:func:`validate_curated_overlay`): each keeps only the citations the
served evidence bundle can resolve; a claim left with none is published as
``not_established`` (its statement survives, its establishment does not); the
result is validated and pruned like any generated topology, and an overlay that
cannot validate is not served at all. Serving the overlay under the generated
topology's ``validated: true`` after a schema check alone was the same "fake
validated" defect the composite merge had. Provenance is recorded in
``coverage.notes``. Generated scanner / runtime facts always win over stale
curated ones.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from mcp_server.services.business_topology.models import (
    BusinessConflict,
    BusinessDataStream,
    BusinessRisk,
    BusinessRule,
    BusinessTopology,
    BusinessUnknown,
)

logger = logging.getLogger(__name__)

CURATED_FILENAME = "business_topology.curated.json"
CURATED_SOURCE = "analyst_curated"

_NON_ALNUM = re.compile(r"[^0-9a-z]+")

#: Stream fields an analyst decides and the scanner never produces. Overlaid
#: only when the curated value is established (non-empty / not ``unknown``).
_JUDGEMENT_STREAM_FIELDS: dict[str, frozenset[str]] = {
    "verdict": frozenset({""}),
    "key_form": frozenset({""}),
    "generation": frozenset({""}),
    "lifecycle": frozenset({"", "unknown"}),
    "requiredness": frozenset({"", "not_established"}),
}


def _norm(value: str) -> str:
    return _NON_ALNUM.sub("", str(value or "").casefold())


def _redact(text: str) -> str:
    from mcp_server.services.business_topology.validation import redact_secretlike

    return redact_secretlike(text) if text else text


@dataclass
class CuratedTopologyLoad:
    """Outcome of reading the curated file: a model, or why there is none."""

    topology: BusinessTopology | None
    error: str = ""

    @property
    def present(self) -> bool:
        return self.topology is not None or bool(self.error)


def load_curated_topology(path: Path, *, project_id: str) -> CuratedTopologyLoad:
    """Parse the curated file through the strict contract, never raw.

    Absent file -> ``CuratedTopologyLoad(None)``. Unreadable, schema-invalid or
    belonging to another project -> ``topology=None`` with ``error`` set, so the
    caller can say so instead of serving an unchecked dict.
    """

    if not path.is_file():
        return CuratedTopologyLoad(None)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        topology = BusinessTopology.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError, ValidationError) as exc:
        logger.warning("Curated business topology for %s is invalid: %s", project_id, exc)
        return CuratedTopologyLoad(None, error=f"curated topology is invalid: {type(exc).__name__}")
    if topology.metadata.project_id != project_id:
        return CuratedTopologyLoad(
            None, error="curated topology metadata.project_id does not match the project"
        )
    return CuratedTopologyLoad(topology)


def curated_is_fresh(curated: BusinessTopology, current_fingerprint: str) -> bool:
    return bool(current_fingerprint) and curated.scan_fingerprint == current_fingerprint


@dataclass
class CuratedOverlayResult:
    topology: BusinessTopology
    applied: dict[str, int] = field(default_factory=dict)
    #: Curated ids that could not be placed on the generated topology, by kind.
    unmatched: dict[str, list[str]] = field(default_factory=dict)
    #: Ids of the claims the overlay ADDED (on the served topology).
    overlaid_ids: set[str] = field(default_factory=set)
    #: Overlaid claims published as ``not_established`` because none of their
    #: curated citations resolve in the served evidence bundle.
    demoted_ids: list[str] = field(default_factory=list)

    @property
    def unmatched_ids(self) -> list[str]:
        return [item for ids in self.unmatched.values() for item in ids]

    @property
    def unmatched_count(self) -> int:
        return len(self.unmatched_ids)

    @property
    def applied_count(self) -> int:
        return sum(self.applied.values())

    def state_metadata(self) -> dict[str, Any]:
        return {
            "applied": dict(self.applied),
            "unmatched_count": self.unmatched_count,
            "unmatched_ids": {kind: list(ids) for kind, ids in self.unmatched.items() if ids},
            "demoted_count": len(self.demoted_ids),
        }


class _Matcher:
    """Identity maps from curated ids onto generated ids."""

    def __init__(self, generated: BusinessTopology, curated: BusinessTopology) -> None:
        self.generated = generated
        self.curated = curated
        self.systems = self._by_names(generated.systems, curated.systems)
        self.objects = self._by_names(generated.business_objects, curated.business_objects)
        self.streams: dict[str, str] = {}
        self._match_streams()

    @staticmethod
    def _names(item: Any) -> set[str]:
        names = {_norm(item.name)}
        names.update(_norm(alias) for alias in getattr(item, "aliases", []) or [])
        return {name for name in names if name}

    def _by_names(self, generated: list[Any], curated: list[Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in curated:
            names = self._names(item)
            candidates = [g for g in generated if self._names(g) & names]
            if len(candidates) > 1:
                # Several generated items share an alias: only an exact primary
                # name is unambiguous enough to bind analyst judgement to.
                candidates = [g for g in candidates if _norm(g.name) == _norm(item.name)]
            if len(candidates) == 1:
                result[item.id] = candidates[0].id
        return result

    def _match_streams(self) -> None:
        by_pair: dict[tuple[str, str], list[BusinessDataStream]] = {}
        for stream in self.generated.data_streams:
            by_pair.setdefault(
                (stream.source_system_id, stream.destination_system_id), []
            ).append(stream)
        curated_per_pair: dict[tuple[str, str], int] = {}
        mapped_pairs: dict[str, tuple[str, str]] = {}
        for stream in self.curated.data_streams:
            source = self.systems.get(stream.source_system_id)
            dest = self.systems.get(stream.destination_system_id)
            if source is None or dest is None:
                continue
            mapped_pairs[stream.id] = (source, dest)
            curated_per_pair[(source, dest)] = curated_per_pair.get((source, dest), 0) + 1

        used: set[str] = set()
        for stream in self.curated.data_streams:
            pair = mapped_pairs.get(stream.id)
            if pair is None:
                continue
            candidates = by_pair.get(pair, [])
            objects = {
                self.objects[ref] for ref in stream.business_object_ids if ref in self.objects
            }
            if objects:
                chosen = [c for c in candidates if objects & set(c.business_object_ids)]
            elif stream.business_object_ids:
                # The curated object is unknown to the generated side.
                chosen = []
            else:
                chosen = [c for c in candidates if not c.business_object_ids]
            if not chosen and len(candidates) == 1 and curated_per_pair[pair] == 1:
                # One generated edge, one curated flow, no contradicting object
                # on the generated side: the same exchange, less labelled.
                if not candidates[0].business_object_ids:
                    chosen = list(candidates)
            chosen = [c for c in chosen if c.id not in used]
            if len(chosen) == 1:
                used.add(chosen[0].id)
                self.streams[stream.id] = chosen[0].id


def _unique_id(curated_id: str, taken: set[str]) -> str:
    candidate = curated_id if curated_id not in taken else f"curated-{curated_id}"
    counter = 2
    while candidate in taken:
        candidate = f"curated-{curated_id}-{counter}"
        counter += 1
    taken.add(candidate)
    return candidate


def overlay_curated_judgement(
    generated: BusinessTopology,
    curated: BusinessTopology,
) -> CuratedOverlayResult:
    """Overlay a STALE curated topology's analyst judgement onto ``generated``.

    ``generated`` is not mutated; the returned topology is a validated copy.
    """

    topology = generated.model_copy(deep=True)
    matcher = _Matcher(topology, curated)
    result = CuratedOverlayResult(topology=topology)
    applied = result.applied
    unmatched = result.unmatched

    taken = {
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
            "conflicts",
            "artifact_annotations",
            "component_annotations",
            "lineage_annotations",
        )
        for item in getattr(topology, name)
    }
    streams_by_id = {stream.id: stream for stream in topology.data_streams}
    systems_by_id = {system.id: system for system in topology.systems}

    def note_unmatched(kind: str, item_id: str) -> None:
        unmatched.setdefault(kind, []).append(item_id)

    # --- systems: owner / purpose -------------------------------------------
    for system in curated.systems:
        target_id = matcher.systems.get(system.id)
        if target_id is None:
            if system.owner or system.purpose:
                note_unmatched("systems", system.id)
            continue
        target = systems_by_id[target_id]
        changed = False
        if system.owner and system.owner != target.owner:
            target.owner = _redact(system.owner)
            changed = True
        if system.purpose and system.purpose != target.purpose:
            target.purpose = _redact(system.purpose)
            changed = True
        if changed:
            applied["systems"] = applied.get("systems", 0) + 1

    # --- streams: judgement scalars -----------------------------------------
    for stream in curated.data_streams:
        target_id = matcher.streams.get(stream.id)
        if target_id is None:
            if any(
                getattr(stream, attr) not in empty
                for attr, empty in _JUDGEMENT_STREAM_FIELDS.items()
            ) or (stream.risk_ids or stream.unknown_ids or stream.conflict_ids):
                note_unmatched("data_streams", stream.id)
            continue
        target = streams_by_id[target_id]
        changed = False
        for attr, empty in _JUDGEMENT_STREAM_FIELDS.items():
            value = getattr(stream, attr)
            if value in empty or value == getattr(target, attr):
                continue
            setattr(target, attr, _redact(value) if attr in {"verdict", "key_form", "generation"} else value)
            changed = True
        if changed:
            applied["data_streams"] = applied.get("data_streams", 0) + 1

    def map_streams(refs: list[str]) -> list[str]:
        return list(dict.fromkeys(matcher.streams[ref] for ref in refs if ref in matcher.streams))

    # Curated claim id -> overlaid id, so conflicts can reference them.
    overlaid_ids: dict[str, str] = {}

    # --- unknowns / risks ------------------------------------------------------
    for unknown in curated.unknowns:
        stream_ids = map_streams(unknown.stream_ids)
        if unknown.stream_ids and not stream_ids:
            note_unmatched("unknowns", unknown.id)
            continue
        new_id = _unique_id(unknown.id, taken)
        overlaid_ids[unknown.id] = new_id
        topology.unknowns.append(
            BusinessUnknown(
                **{
                    **unknown.model_dump(),
                    "id": new_id,
                    "stream_ids": stream_ids,
                    "description": _redact(unknown.description),
                    "question": _redact(unknown.question),
                }
            )
        )
        applied["unknowns"] = applied.get("unknowns", 0) + 1
    for risk in curated.risks:
        stream_ids = map_streams(risk.stream_ids)
        if risk.stream_ids and not stream_ids:
            note_unmatched("risks", risk.id)
            continue
        new_id = _unique_id(risk.id, taken)
        overlaid_ids[risk.id] = new_id
        topology.risks.append(
            BusinessRisk(
                **{
                    **risk.model_dump(),
                    "id": new_id,
                    "stream_ids": stream_ids,
                    "description": _redact(risk.description),
                }
            )
        )
        applied["risks"] = applied.get("risks", 0) + 1

    # --- business rules -------------------------------------------------------
    transformation_by_name = {
        _norm(item.name): item.id for item in topology.transformations if _norm(item.name)
    }
    curated_transformation_names = {item.id: _norm(item.name) for item in curated.transformations}
    for rule in curated.business_rules:
        stream_ids = map_streams(rule.stream_ids)
        if not stream_ids:
            # A rule detached from every exchange cannot be acted on (the same
            # reason pruning drops such rules); report it instead.
            note_unmatched("business_rules", rule.id)
            continue
        new_id = _unique_id(rule.id, taken)
        overlaid_ids[rule.id] = new_id
        topology.business_rules.append(
            BusinessRule(
                **{
                    **rule.model_dump(),
                    "id": new_id,
                    "stream_ids": stream_ids,
                    "transformation_ids": list(
                        dict.fromkeys(
                            transformation_by_name[curated_transformation_names[ref]]
                            for ref in rule.transformation_ids
                            if curated_transformation_names.get(ref) in transformation_by_name
                        )
                    ),
                    "statement": _redact(rule.statement),
                    "description": _redact(rule.description),
                    "owner": _redact(rule.owner),
                    "owner_question": _redact(rule.owner_question),
                }
            )
        )
        applied["business_rules"] = applied.get("business_rules", 0) + 1

    # --- conflicts (last: may reference any claim, including overlaid ones) ---
    subject_map: dict[str, str] = {
        **matcher.systems,
        **matcher.objects,
        **matcher.streams,
        **overlaid_ids,
    }
    for conflict in curated.conflicts:
        subject_refs = list(
            dict.fromkeys(subject_map[ref] for ref in conflict.subject_refs if ref in subject_map)
        )
        if not subject_refs:
            note_unmatched("conflicts", conflict.id)
            continue
        new_id = _unique_id(conflict.id, taken)
        overlaid_ids[conflict.id] = new_id
        topology.conflicts.append(
            BusinessConflict(
                **{
                    **conflict.model_dump(),
                    "id": new_id,
                    "subject_refs": subject_refs,
                    "description": _redact(conflict.description),
                    "resolution": _redact(conflict.resolution),
                }
            )
        )
        applied["conflicts"] = applied.get("conflicts", 0) + 1

    # --- stream back-references to overlaid claims ------------------------------
    for stream in curated.data_streams:
        target_id = matcher.streams.get(stream.id)
        if target_id is None:
            continue
        target = streams_by_id[target_id]
        for attr in ("risk_ids", "unknown_ids", "conflict_ids"):
            refs = [overlaid_ids[ref] for ref in getattr(stream, attr) if ref in overlaid_ids]
            current = getattr(target, attr)
            current.extend(ref for ref in refs if ref not in current)
    for claim in (*topology.unknowns, *topology.risks):
        if claim.id not in overlaid_ids.values():
            continue
        for stream_id in claim.stream_ids:
            refs = (
                streams_by_id[stream_id].unknown_ids
                if isinstance(claim, BusinessUnknown)
                else streams_by_id[stream_id].risk_ids
            )
            if claim.id not in refs:
                refs.append(claim.id)
    for conflict in topology.conflicts:
        if conflict.id not in overlaid_ids.values():
            continue
        for ref in conflict.subject_refs:
            if ref in streams_by_id and conflict.id not in streams_by_id[ref].conflict_ids:
                streams_by_id[ref].conflict_ids.append(conflict.id)

    # --- provenance --------------------------------------------------------------
    if result.applied_count:
        topology.coverage.notes.append(
            "Analyst-curated judgement overlaid from a curated topology built for an "
            f"older scan (curated scan {curated.scan_fingerprint[:12]}, current scan "
            f"{generated.scan_fingerprint[:12]}): "
            + ", ".join(f"{count} {kind}" for kind, count in sorted(applied.items()))
            + ". Overlaid items keep their curated status and citations; generated "
            "scanner and runtime facts take precedence."
        )
    if result.unmatched_count:
        ids = result.unmatched_ids
        topology.coverage.notes.append(
            f"{len(ids)} curated item(s) could not be matched to the current topology "
            "and were not applied: "
            + ", ".join(ids[:25])
            + (f" (+{len(ids) - 25} more)" if len(ids) > 25 else "")
            + "."
        )

    # Strict contract round-trip: an overlay can never publish a shape the
    # frontend would reject.
    result.topology = BusinessTopology.model_validate(topology.model_dump(mode="json"))
    result.overlaid_ids = set(overlaid_ids.values())
    return result


_UNESTABLISHED = {"not_established", "rejected"}


def validate_curated_overlay(
    result: CuratedOverlayResult,
    *,
    bundle: Any,
    project_id: str,
) -> CuratedOverlayResult | None:
    """Hold an overlay to the provenance of the topology it was laid on.

    Returns the validated result, or ``None`` when the overlaid topology cannot
    validate (a global defect such as a secret finding, or pruning that would
    remove a GENERATED claim) -- the caller then serves the generated topology
    without the overlay. Overlaid claims that fail claim-local validation are
    pruned and reported under ``unmatched["failed_validation"]``.
    """

    from mcp_server.services.business_topology.pruning import prune_to_valid

    topology = result.topology.model_copy(deep=True)
    known = bundle.evidence_ids
    demoted: list[str] = []
    for name in ("unknowns", "risks", "business_rules", "conflicts"):
        for claim in getattr(topology, name):
            if claim.id not in result.overlaid_ids:
                continue
            claim.evidence_ids = [ref for ref in claim.evidence_ids if ref in known]
            if isinstance(claim, BusinessConflict):
                claim.implementation_evidence_ids = [
                    ref for ref in claim.implementation_evidence_ids if ref in known
                ]
                claim.supporting_evidence_ids = [
                    ref for ref in claim.supporting_evidence_ids if ref in known
                ]
            if not claim.evidence_ids and claim.status not in _UNESTABLISHED:
                claim.status = "not_established"
                demoted.append(claim.id)

    topology, report, dropped = prune_to_valid(topology, bundle=bundle, project_id=project_id)
    if not report.validated:
        logger.warning(
            "Curated overlay for %s does not validate (%d error(s)); not served",
            project_id,
            len(report.errors),
        )
        return None
    foreign = [item for item in dropped if item not in result.overlaid_ids]
    if foreign:
        # The overlay must never cost the generated topology a claim.
        logger.warning(
            "Curated overlay for %s would prune generated claims %s; not served",
            project_id,
            foreign[:5],
        )
        return None
    # `applied` was counted before validation; pruned items were not applied.
    applied = dict(result.applied)
    dropped_set = set(dropped)
    for name in ("unknowns", "risks", "business_rules", "conflicts"):
        lost = sum(1 for claim in getattr(result.topology, name) if claim.id in dropped_set)
        if lost:
            remaining = applied.get(name, 0) - lost
            if remaining > 0:
                applied[name] = remaining
            else:
                applied.pop(name, None)
    validated = CuratedOverlayResult(
        topology=topology,
        applied=applied,
        unmatched={kind: list(ids) for kind, ids in result.unmatched.items()},
        overlaid_ids=set(result.overlaid_ids) - set(dropped),
        demoted_ids=[item for item in demoted if item not in dropped],
    )
    if dropped:
        validated.unmatched.setdefault("failed_validation", []).extend(dropped)
        topology.coverage.notes.append(
            f"{len(dropped)} overlaid curated item(s) failed validation against the "
            "current scan's evidence and were not applied: " + ", ".join(dropped[:25]) + "."
        )
    if validated.demoted_ids:
        topology.coverage.notes.append(
            f"{len(validated.demoted_ids)} overlaid curated item(s) cite no evidence the "
            "current scan can resolve and are published as not established."
        )
    return validated


__all__ = [
    "CURATED_FILENAME",
    "CURATED_SOURCE",
    "CuratedOverlayResult",
    "CuratedTopologyLoad",
    "curated_is_fresh",
    "validate_curated_overlay",
    "load_curated_topology",
    "overlay_curated_judgement",
]
