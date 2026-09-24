"""Measure how much of the business-topology contract a candidate fills.

A generated topology and an analyst-curated baseline are compared field by
field, for EVERY field the contract (:mod:`.models`) declares. The field list
is read from the pydantic models at call time, never written out here, so a
field added to the contract is measured the day it lands instead of silently
being absent from the score.

Two numbers per field:

* ``items``  -- how many objects could carry the field (list items of the
  owning collection; for nested lists such as ``delivery_paths.segments[]``
  the total number of nested items across all parents);
* ``filled`` -- how many of those carry a NON-EMPTY value.

"Empty" is ``None``, ``""``, ``[]``, ``{}``, or the field's declared default
when that default is a "not established" sentinel (``"unknown"``,
``"not_established"``). Such a value states nothing a reader did not already
assume, so counting it as filled would reward a producer for omitting the fact.
A substantive default (``kind="software_system"``) is a real answer and counts.

The payloads are walked as plain dicts and are NOT validated: a candidate that
fails strict validation still has a measurable shape, and the point of the
report is to show where it falls short, not to refuse to look at it.
"""

from __future__ import annotations

import types
from dataclasses import asdict, dataclass, field
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from mcp_server.services.business_topology.models import BusinessTopology


@dataclass(frozen=True)
class CollectionParity:
    """Item counts for one top-level collection (or singleton object)."""

    name: str
    candidate_count: int
    baseline_count: int

    @property
    def count_ratio(self) -> float:
        """Candidate count relative to the baseline, capped at 1.0."""

        if self.baseline_count == 0:
            return 1.0
        return min(self.candidate_count / self.baseline_count, 1.0)


@dataclass(frozen=True)
class FieldParity:
    """Fill ratio of one contract field, candidate vs baseline."""

    path: str
    collection: str
    candidate_items: int
    candidate_filled: int
    baseline_items: int
    baseline_filled: int

    @property
    def candidate_ratio(self) -> float:
        return _ratio(self.candidate_filled, self.candidate_items)

    @property
    def baseline_ratio(self) -> float:
        return _ratio(self.baseline_filled, self.baseline_items)

    @property
    def score(self) -> float:
        """How close the candidate's fill comes to the baseline's, in [0, 1].

        Only meaningful when the baseline fills the field at all; a field the
        baseline never fills cannot be missed and is excluded from scoring.
        """

        if self.baseline_ratio == 0.0:
            return 1.0
        return min(self.candidate_ratio / self.baseline_ratio, 1.0)


@dataclass
class ParityReport:
    """Per-collection counts, per-field fill ratios and the rolled-up score.

    ``overall_score`` is the mean of two sub-scores so neither can hide the
    other: ``count_score`` (does the candidate find as many things as the
    baseline) and ``field_score`` (does it say as much about each thing).
    ``field_score`` is weighted by the baseline fill ratio, so a field the
    analyst filled on every item counts more than one filled on a few.
    """

    collections: list[CollectionParity] = field(default_factory=list)
    fields: list[FieldParity] = field(default_factory=list)
    count_score: float = 0.0
    field_score: float = 0.0
    overall_score: float = 0.0
    missing_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_score": round(self.overall_score, 4),
            "count_score": round(self.count_score, 4),
            "field_score": round(self.field_score, 4),
            "collections": [
                {**asdict(item), "count_ratio": round(item.count_ratio, 4)}
                for item in self.collections
            ],
            "fields": [
                {
                    **asdict(item),
                    "candidate_ratio": round(item.candidate_ratio, 4),
                    "baseline_ratio": round(item.baseline_ratio, 4),
                    "score": round(item.score, 4),
                }
                for item in self.fields
            ],
            "missing_fields": list(self.missing_fields),
        }


def _ratio(filled: int, items: int) -> float:
    return filled / items if items else 0.0


# --- Contract introspection --------------------------------------------------


@dataclass(frozen=True)
class _FieldSpec:
    """How one contract field is walked."""

    name: str
    # "scalar" | "model" (optional nested object) | "model_list"
    shape: str
    default: Any
    model: type[BaseModel] | None = None


def _strip_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        members = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(members) == 1:
            return members[0]
    return annotation


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _field_specs(model: type[BaseModel]) -> list[_FieldSpec]:
    specs: list[_FieldSpec] = []
    for name, info in model.model_fields.items():
        annotation = _strip_optional(info.annotation)
        default = None if info.default is PydanticUndefined else info.default
        if get_origin(annotation) is list:
            (item,) = get_args(annotation) or (Any,)
            if _is_model(item):
                specs.append(_FieldSpec(name, "model_list", default, item))
                continue
        elif _is_model(annotation):
            specs.append(_FieldSpec(name, "model", default, annotation))
            continue
        specs.append(_FieldSpec(name, "scalar", default))
    return specs


_NOT_ESTABLISHED = frozenset({"unknown", "not_established"})


def _is_filled(value: Any, default: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, list, dict, tuple)) and len(value) == 0:
        return False
    if isinstance(value, str) and value.strip() == "":
        return False
    # A "not established" default states nothing. A required field has no
    # default, and a substantive default is an answer, so either counts.
    if isinstance(default, str) and default in _NOT_ESTABLISHED and value == default:
        return False
    return True


# --- Measurement -------------------------------------------------------------


@dataclass
class _Tally:
    items: int = 0
    filled: int = 0


def _measure(
    objects: list[dict[str, Any]],
    model: type[BaseModel],
    prefix: str,
    tallies: dict[str, _Tally],
    order: list[tuple[str, str]],
    collection: str,
) -> None:
    """Accumulate fill counts for every field of ``model`` over ``objects``."""

    for spec in _field_specs(model):
        path = f"{prefix}{spec.name}"
        if path not in tallies:
            tallies[path] = _Tally()
            order.append((path, collection))
        tally = tallies[path]
        tally.items += len(objects)
        values = [obj.get(spec.name) if isinstance(obj, dict) else None for obj in objects]
        tally.filled += sum(1 for value in values if _is_filled(value, spec.default))

        if spec.shape == "model_list" and spec.model is not None:
            nested = [
                item
                for value in values
                if isinstance(value, list)
                for item in value
                if isinstance(item, dict)
            ]
            _measure(nested, spec.model, f"{path}[].", tallies, order, collection)
        elif spec.shape == "model" and spec.model is not None:
            # Denominator stays the PARENT count: "3 of 48 streams state an
            # offered width" is the comparable fact, not "3 of 3 widths".
            nested = [value if isinstance(value, dict) else {} for value in values]
            _measure(nested, spec.model, f"{path}.", tallies, order, collection)


def _collections(topology: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Top-level objects to measure, keyed by contract field name.

    List fields are collections; a nested-object field (``metadata``,
    ``coverage``) is a singleton collection so its sub-fields are measured the
    same way. Top-level scalars (``schema_version``, ``scan_fingerprint``) are
    identity, not content, and are not scored.
    """

    found: dict[str, list[dict[str, Any]]] = {}
    for spec in _field_specs(BusinessTopology):
        value = topology.get(spec.name) if isinstance(topology, dict) else None
        if spec.shape == "model_list":
            found[spec.name] = [item for item in value or [] if isinstance(item, dict)]
        elif spec.shape == "model":
            found[spec.name] = [value] if isinstance(value, dict) else []
    return found


def _models_by_collection() -> dict[str, type[BaseModel]]:
    return {
        spec.name: spec.model
        for spec in _field_specs(BusinessTopology)
        if spec.model is not None
    }


def score_topology_parity(candidate: dict[str, Any], baseline: dict[str, Any]) -> ParityReport:
    """Compare ``candidate`` against ``baseline`` on every contract field."""

    models = _models_by_collection()
    candidate_objects = _collections(candidate)
    baseline_objects = _collections(baseline)

    report = ParityReport()
    candidate_tallies: dict[str, _Tally] = {}
    baseline_tallies: dict[str, _Tally] = {}
    order: list[tuple[str, str]] = []
    for name, model in models.items():
        report.collections.append(
            CollectionParity(
                name=name,
                candidate_count=len(candidate_objects.get(name, [])),
                baseline_count=len(baseline_objects.get(name, [])),
            )
        )
        prefix = f"{name}."
        _measure(candidate_objects.get(name, []), model, prefix, candidate_tallies, order, name)
        _measure(baseline_objects.get(name, []), model, prefix, baseline_tallies, [], name)

    for path, collection in order:
        cand = candidate_tallies.get(path, _Tally())
        base = baseline_tallies.get(path, _Tally())
        report.fields.append(
            FieldParity(
                path=path,
                collection=collection,
                candidate_items=cand.items,
                candidate_filled=cand.filled,
                baseline_items=base.items,
                baseline_filled=base.filled,
            )
        )

    counted = [item for item in report.collections if item.baseline_count > 0]
    report.count_score = (
        sum(item.count_ratio for item in counted) / len(counted) if counted else 1.0
    )
    scored = [item for item in report.fields if item.baseline_ratio > 0.0]
    weight = sum(item.baseline_ratio for item in scored)
    report.field_score = (
        sum(item.score * item.baseline_ratio for item in scored) / weight if weight else 1.0
    )
    report.overall_score = (report.count_score + report.field_score) / 2
    report.missing_fields = [
        item.path for item in scored if item.candidate_filled == 0
    ]
    return report


# --- Rendering ---------------------------------------------------------------


def _table(headers: list[str], rows: list[list[str]], align_right: set[int]) -> list[str]:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def line(cells: list[str]) -> str:
        return "| " + " | ".join(
            cell.rjust(widths[index]) if index in align_right else cell.ljust(widths[index])
            for index, cell in enumerate(cells)
        ) + " |"

    rule = "+-" + "-+-".join("-" * width for width in widths) + "-+"
    return [rule, line(headers), rule, *(line(row) for row in rows), rule]


def render_parity_table(
    report: ParityReport,
    *,
    candidate_label: str = "candidate",
    only_baseline_filled: bool = False,
) -> str:
    """ASCII-only rendering (cp1252-safe) of a :class:`ParityReport`."""

    lines = [
        f"Topology parity: {candidate_label}",
        f"overall={report.overall_score:.3f}  count_score={report.count_score:.3f}"
        f"  field_score={report.field_score:.3f}",
        "",
        "Collections",
    ]
    lines += _table(
        ["collection", "candidate", "baseline", "ratio"],
        [
            [item.name, str(item.candidate_count), str(item.baseline_count), f"{item.count_ratio:.2f}"]
            for item in report.collections
        ],
        {1, 2, 3},
    )
    fields = [
        item for item in report.fields if not only_baseline_filled or item.baseline_filled > 0
    ]
    lines += ["", "Field fill (filled/items)"]
    lines += _table(
        ["field", "candidate", "cand%", "baseline", "base%", "score"],
        [
            [
                item.path,
                f"{item.candidate_filled}/{item.candidate_items}",
                f"{item.candidate_ratio * 100:.0f}",
                f"{item.baseline_filled}/{item.baseline_items}",
                f"{item.baseline_ratio * 100:.0f}",
                f"{item.score:.2f}" if item.baseline_ratio > 0 else "-",
            ]
            for item in fields
        ],
        {1, 2, 3, 4, 5},
    )
    lines += ["", f"Filled by baseline, never by candidate ({len(report.missing_fields)}):"]
    lines += [f"  - {path}" for path in report.missing_fields] or ["  (none)"]
    return "\n".join(lines)


__all__ = [
    "CollectionParity",
    "FieldParity",
    "ParityReport",
    "render_parity_table",
    "score_topology_parity",
]
