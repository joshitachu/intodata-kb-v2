"""Query the evidence layer.

The point of holding the complete primitive is that questions become filters.
Nothing here knows about any particular business object, system or field: every
question below is the same handful of predicates over ``FieldRecord`` /
``PayloadFieldRecord`` / ``ArtifactRecord``, composed differently.

That is the test of whether the layer is general. If a new question needs a new
function here, the primitive was incomplete; if it needs only a new combination
of existing predicates, it was not.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .models import EvidenceLayer, FieldRecord, RuleClass


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def load(out_dir: Path) -> EvidenceLayer:
    from .models import (
        ArtifactRecord, InterfaceRecord, PayloadFieldRecord, Unresolved,
    )

    def read(name: str) -> list[dict[str, Any]]:
        p = out_dir / name
        if not p.is_file():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def tupleise(d: dict[str, Any], keys: Iterable[str]) -> dict[str, Any]:
        for k in keys:
            if isinstance(d.get(k), list):
                d[k] = tuple(d[k])
        return d

    layer = EvidenceLayer()
    layer.fields = [
        FieldRecord(**tupleise(d, ("source_fields", "category_signals", "rule_signals")))
        for d in read("fields.jsonl")
    ]
    layer.payload_fields = [
        PayloadFieldRecord(**tupleise(d, ("value_domain",)))
        for d in read("payload_fields.jsonl")
    ]
    layer.artifacts = [
        ArtifactRecord(**tupleise(d, (
            "component_types", "external_systems", "channels", "endpoints",
            "calls", "called_by", "mapping_refs")))
        for d in read("artifacts.jsonl")
    ]
    layer.interfaces = [
        InterfaceRecord(**tupleise(d, ("artifact_ids", "steps")))
        for d in read("interfaces.jsonl")
    ]
    layer.unresolved = [Unresolved(**d) for d in read("unresolved.jsonl")]
    stats = out_dir / "stats.json"
    if stats.is_file():
        layer.stats = json.loads(stats.read_text(encoding="utf-8"))
    return layer


# ---------------------------------------------------------------------------
# predicates
# ---------------------------------------------------------------------------

@dataclass
class Filter:
    """Composable predicate set. Every value is optional."""

    category: tuple[str, ...] = ()
    stage: tuple[str, ...] = ()
    direction: tuple[str, ...] = ()
    rule_class: tuple[str, ...] = ()
    confidence: tuple[str, ...] = ()
    transform_kind: tuple[str, ...] = ()
    artifact_like: str = ""
    interface_like: str = ""
    field_like: str = ""
    source_like: str = ""
    # Free-text scope, e.g. a business object name. Matched against artifact
    # id, interface id, field path and source expression -- deliberately broad,
    # because a business object shows up under different names in each system.
    scope: str = ""

    def _rx(self, pattern: str) -> re.Pattern[str] | None:
        return re.compile(pattern, re.IGNORECASE) if pattern else None

    def matches(self, f: FieldRecord) -> bool:
        if self.category and f.category not in self.category:
            return False
        if self.stage and f.stage not in self.stage:
            return False
        if self.direction and f.direction not in self.direction:
            return False
        if self.rule_class and f.rule_class not in self.rule_class:
            return False
        if self.confidence and f.confidence not in self.confidence:
            return False
        if self.transform_kind and f.transform_kind not in self.transform_kind:
            return False
        for pattern, value in (
            (self.artifact_like, f.artifact_id),
            (self.interface_like, f.interface_id),
            (self.field_like, f.field_path),
            (self.source_like, f.source_expr),
        ):
            rx = self._rx(pattern)
            if rx and not rx.search(value):
                return False
        rx = self._rx(self.scope)
        if rx and not any(rx.search(v) for v in (
            f.artifact_id, f.interface_id, f.field_path, f.source_expr
        )):
            return False
        return True


def fields(layer: EvidenceLayer, flt: Filter) -> list[FieldRecord]:
    return [f for f in layer.fields if flt.matches(f)]


# ---------------------------------------------------------------------------
# shaped answers -- each is a composition, not a special case
# ---------------------------------------------------------------------------

def carries_category(
    layer: EvidenceLayer, category: str, scope: str = "", stage: str = ""
) -> dict[str, Any]:
    """"Does anything in <scope> carry fields of <category>?"

    Covers the whole "are there asset identification / location / person
    fields on interface X" family for any category in the vocabulary.
    """
    flt = Filter(category=(category,), scope=scope,
                 stage=(stage,) if stage else ())
    hits = fields(layer, flt)
    return {
        "question": f"fields of category '{category}'"
                    + (f" within scope '{scope}'" if scope else "")
                    + (f" at stage '{stage}'" if stage else ""),
        "answer": "yes" if hits else "no",
        "match_count": len(hits),
        "artifacts": sorted({h.artifact_id for h in hits}),
        "fields": sorted({h.field_path for h in hits},
                         key=lambda s: (s.casefold(), s)),
        "evidence": [
            {"field": h.field_path, "source": h.source_expr[:200],
             "stage": h.stage, "artifact": h.artifact_id,
             "locator": h.evidence_locator, "confidence": h.confidence,
             "category_signals": list(h.category_signals)}
            for h in hits[:50]
        ],
        "truncated": len(hits) > 50,
    }


def structure_at_stage(
    layer: EvidenceLayer, scope: str, stage: str
) -> dict[str, Any]:
    """"What does <scope> emit / produce / receive?"

    With ``stage='offered'`` this is "the structure before any mapping".
    """
    hits = fields(layer, Filter(scope=scope, stage=(stage,)))
    by_field: dict[str, list[FieldRecord]] = defaultdict(list)
    for h in hits:
        by_field[h.field_path].append(h)
    rows = []
    for fp in sorted(by_field, key=lambda s: (s.casefold(), s)):
        recs = by_field[fp]
        rows.append({
            "field": fp,
            "type": next((r.field_type for r in recs if r.field_type), ""),
            "category": recs[0].category,
            "source": next((r.source_expr for r in recs if r.source_expr), "")[:200],
            "transform": recs[0].transform_kind,
            "artifacts": sorted({r.artifact_id for r in recs})[:5],
        })
    return {
        "question": f"structure of '{scope}' at stage '{stage}'",
        "field_count": len(rows),
        "fields": rows,
    }


def wire_structure(layer: EvidenceLayer, scope: str) -> dict[str, Any]:
    """Observed message structure with fill rates -- ground truth, not contract."""
    rx = re.compile(scope, re.IGNORECASE) if scope else None
    hits = [
        p for p in layer.payload_fields
        if not rx or rx.search(p.source_id) or rx.search(p.message_root)
    ]
    rows = [{
        "message_root": p.message_root,
        "field": p.field_path,
        "seen": p.messages_seen,
        "populated": p.messages_populated,
        "fill_rate": round(p.messages_populated / p.messages_seen, 3)
        if p.messages_seen else 0.0,
        "value_domain": list(p.value_domain),
        "domain_suppressed": p.value_domain_truncated,
        "source": p.source_id,
    } for p in hits]
    # Roll up across source directories before judging fill: the same field
    # appears once per sample population, and a field populated in one
    # population and absent in another is NOT "never populated".
    agg: dict[tuple[str, str], list[int]] = {}
    for r in rows:
        key = (r["message_root"], r["field"])
        seen, pop = agg.get(key, [0, 0])
        agg[key] = [seen + r["seen"], pop + r["populated"]]
    never = sorted(
        {f for (_root, f), (seen, pop) in agg.items() if seen > 0 and pop == 0},
        key=lambda s: (s.casefold(), s),
    )
    always = sorted(
        {f for (_root, f), (seen, pop) in agg.items() if seen > 0 and pop == seen},
        key=lambda s: (s.casefold(), s),
    )
    return {
        "question": f"observed wire structure for '{scope}'",
        "message_roots": sorted({r["message_root"] for r in rows}),
        "distinct_fields": len(agg),
        "never_populated_count": len(never),
        "never_populated": never,
        "always_populated_count": len(always),
        "always_populated": always,
        "fields": rows,
    }


def business_logic_locations(
    layer: EvidenceLayer, scope: str = ""
) -> dict[str, Any]:
    """"Which artifacts contain business-level mappings?"

    The question that a landscape scan cannot answer today at all.
    """
    hits = fields(layer, Filter(rule_class=(RuleClass.BUSINESS,), scope=scope))
    per_artifact: dict[str, list[FieldRecord]] = defaultdict(list)
    for h in hits:
        per_artifact[h.artifact_id].append(h)
    rows = []
    for aid in sorted(per_artifact, key=lambda a: (-len(per_artifact[a]), a)):
        recs = per_artifact[aid]
        rows.append({
            "artifact": aid,
            "business_rule_count": len(recs),
            "signals": sorted({s for r in recs for s in r.rule_signals}),
            "examples": [
                {"field": r.field_path, "expression": r.source_expr[:240]}
                for r in recs[:5]
            ],
        })
    return {
        "question": "artifacts containing business-level mappings"
                    + (f" within scope '{scope}'" if scope else ""),
        "artifact_count": len(rows),
        "artifacts": rows,
    }


def artifact_dossier(layer: EvidenceLayer, name_or_id: str) -> dict[str, Any]:
    """"What does X do, what is downstream, what are its IO mappings?"""
    rx = re.compile(name_or_id, re.IGNORECASE)
    matches = [a for a in layer.artifacts
               if rx.search(a.artifact_id) or rx.search(a.name)]
    out = []
    for a in matches[:10]:
        own = [f for f in layer.fields if f.artifact_id == a.artifact_id]
        by_stage: dict[str, list[str]] = defaultdict(list)
        for f in own:
            by_stage[f.stage].append(f.field_path)
        transforms = [f for f in own if f.rule_class != RuleClass.NONE]
        out.append({
            "artifact": a.artifact_id,
            "name": a.name,
            "kind": a.kind,
            "group": a.group,
            "purpose": a.purpose,
            "endpoints": list(a.endpoints)[:20],
            "mapping_refs": list(a.mapping_refs),
            "calls_downstream": list(a.calls),
            "called_by_upstream": list(a.called_by),
            "fields_by_stage": {
                s: sorted(set(v), key=lambda x: (x.casefold(), x))
                for s, v in sorted(by_stage.items())
            },
            "categories_present": dict(Counter(f.category for f in own).most_common()),
            "transformations": [
                {"field": t.field_path, "class": t.rule_class,
                 "signals": list(t.rule_signals),
                 "expression": t.source_expr[:240]}
                for t in transforms[:25]
            ],
        })
    return {"question": f"dossier for '{name_or_id}'",
            "match_count": len(matches), "artifacts": out}


def interface_content(layer: EvidenceLayer, scope: str) -> dict[str, Any]:
    """"What flows through this flow?" -- category profile of an edge."""
    hits = fields(layer, Filter(scope=scope))
    ifaces = [i for i in layer.interfaces
              if re.search(scope, i.interface_id, re.I)
              or re.search(scope, i.label or "", re.I)
              or re.search(scope, f"{i.source_system}2{i.target_system}", re.I)]
    return {
        "question": f"what flows through '{scope}'",
        "interfaces": [i.as_dict() for i in ifaces[:20]],
        "field_count": len(hits),
        "by_category": dict(Counter(f.category for f in hits).most_common()),
        "by_stage": dict(Counter(f.stage for f in hits).most_common()),
        "by_rule_class": dict(Counter(f.rule_class for f in hits).most_common()),
    }


def coverage_report(layer: EvidenceLayer) -> dict[str, Any]:
    """The completeness gate: what the layer knows it does not know."""
    return {
        "stats": layer.stats,
        "unresolved_by_kind": dict(Counter(u.kind for u in layer.unresolved)),
        "unresolved_examples": [
            u.as_dict() for u in layer.unresolved[:40]
        ],
    }
