"""Assemble the evidence layer: capture everything, then derive.

Order matters. Capture is purely mechanical -- read what is declared, record
what cannot be read. Derivation (category, rule class, reconciliation) only
ever adds columns to captured rows; it never invents or removes one. That split
is what keeps the layer auditable: any derived value can be recomputed from the
captured rows plus the vocabularies, and a vocabulary change never silently
loses evidence.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Callable

from .capture import payload_census, registers, talend_designs, xslt_mappings
from .derive.categories import Categoriser
from .derive.classify import Classifier, DUPLICATE_TARGET_MARKER
from .models import (
    ArtifactRecord, Confidence, EvidenceLayer, FieldRecord, InterfaceRecord,
    RuleClass, TransformKind, UNCATEGORISED, Unresolved,
)

Progress = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def build(
    repo_root: Path,
    *,
    payload_dirs: list[Path] | None = None,
    register_root: Path | None = None,
    include_registers: bool = False,
    category_overlays: tuple[Path, ...] = (),
    classification_overlays: tuple[Path, ...] = (),
    max_payload_messages: int = 200,
    progress: Progress = _noop,
) -> EvidenceLayer:
    layer = EvidenceLayer()
    categoriser = Categoriser.load(*category_overlays)
    classifier = Classifier.load(*classification_overlays)

    # --- capture: Talend designs ---------------------------------------
    designs = talend_designs.latest_designs(repo_root)
    progress(f"designs: {len(designs)} latest-version item files")
    for i, (path, _ver) in enumerate(sorted(designs.items()), 1):
        kind = _design_kind(path, repo_root)
        artifact, fields, unresolved = talend_designs.capture_design(
            path, repo_root, artifact_kind=kind
        )
        layer.artifacts.append(artifact)
        layer.fields.extend(fields)
        layer.unresolved.extend(unresolved)
        if i % 250 == 0:
            progress(f"  ... {i}/{len(designs)} designs, {len(layer.fields)} fields")

    # --- capture: stylesheets (the mappings the scanner never sees) -----
    sheets = xslt_mappings.find_stylesheets(repo_root)
    progress(f"stylesheets: {len(sheets)}")
    for path in sheets:
        artifact, fields, unresolved = xslt_mappings.capture_stylesheet(path, repo_root)
        layer.artifacts.append(artifact)
        layer.fields.extend(fields)
        layer.unresolved.extend(unresolved)

    # --- capture: real message populations ------------------------------
    dirs = payload_dirs if payload_dirs is not None else \
        payload_census.discover_payload_dirs(repo_root)
    progress(f"payload sources: {len(dirs)}")
    for directory in dirs:
        recs, unresolved = payload_census.census_directory(
            directory, repo_root, max_messages=max_payload_messages
        )
        layer.payload_fields.extend(recs)
        layer.unresolved.extend(unresolved)

    # --- capture: configuration registers (opt-in) ----------------------
    if include_registers:
        root = register_root or repo_root
        arts, ifaces, creds, unresolved = registers.capture_registers(root, repo_root)
        layer.artifacts.extend(arts)
        layer.interfaces.extend(ifaces)
        layer.unresolved.extend(unresolved)
        layer.stats["credential_findings"] = [c.as_dict() for c in creds]
        if creds:
            progress(
                f"  !! {len(creds)} credential value(s) found and redacted in "
                f"table exports -- see stats.credential_findings"
            )
    else:
        layer.unresolved.append(Unresolved(
            "registers_not_read",
            "table exports",
            "register capture was not enabled (--registers). Where routing "
            "lives in database tables, the interface topology cannot be "
            "derived from code alone.",
        ))

    # --- link artifacts both ways --------------------------------------
    _link_callers(layer)

    # --- derive ---------------------------------------------------------
    progress("deriving categories and rule classes")
    layer.fields = _derive_fields(layer.fields, categoriser, classifier)
    _reconcile(layer)
    _infer_interfaces_from_artifacts(layer)

    layer.sort()
    layer.stats.update(_stats(layer, categoriser, classifier))
    return layer


# ---------------------------------------------------------------------------
# derivation
# ---------------------------------------------------------------------------

# XPath expressions that name no field: the context node, a wildcard, an axis.
# Two targets both fed from `.` is the ordinary shape of an XSLT copy, not a
# duplicated contract field -- treating it as one produced 2,639 false
# positives on a single landscape and buried the real duplicates.
_CONTEXT_ONLY = frozenset({
    ".", "..", "*", "@*", "text()", "node()", "self::node()", "current()",
    "position()", "last()", "/", "//", "",
})


def _is_named_source(expr: str) -> bool:
    e = (expr or "").strip()
    if e in _CONTEXT_ONLY:
        return False
    # Must reference at least one identifier of real length.
    return bool(re.search(r"[A-Za-z_]\w{2,}", e))


def _derive_fields(
    fields: list[FieldRecord],
    categoriser: Categoriser,
    classifier: Classifier,
) -> list[FieldRecord]:
    # Structural signal a regex over one expression cannot see: two targets in
    # the same artifact fed by the same single source with no distinguishing
    # transform. On real data this is how duplicated contract fields show up.
    seen: dict[tuple[str, str], str] = {}
    dupes: set[tuple[str, str, str]] = set()
    for f in fields:
        if f.transform_kind not in (TransformKind.DIRECT, TransformKind.DECLARED):
            continue
        if not _is_named_source(f.source_expr):
            continue
        key = (f.artifact_id, f.source_expr.strip())
        prev = seen.get(key)
        if prev is not None and prev != f.field_path:
            dupes.add((f.artifact_id, f.source_expr.strip(), prev))
            dupes.add((f.artifact_id, f.source_expr.strip(), f.field_path))
        else:
            seen.setdefault(key, f.field_path)

    out: list[FieldRecord] = []
    for f in fields:
        category, cat_conf, cat_sigs = categoriser.categorise(
            f.field_path, f.source_expr
        )
        expr = f.source_expr
        if (f.artifact_id, expr.strip(), f.field_path) in dupes:
            expr = f"{expr}\n{DUPLICATE_TARGET_MARKER}"
        verdict = classifier.classify(expr)
        out.append(replace(
            f,
            category=category,
            category_confidence=cat_conf,
            category_signals=cat_sigs,
            rule_class=verdict.rule_class,
            rule_signals=verdict.signals,
        ))
    return out


def _reconcile(layer: EvidenceLayer) -> None:
    """Join the census against declared contracts.

    Two findings come out of this that neither source yields alone: a field
    declared in a contract but never populated in any observed message, and a
    field populated on the wire that no contract declares. Both are recorded as
    unresolved items rather than as prose, so they stay queryable.
    """
    declared: set[str] = {
        f.field_path.rsplit("/", 1)[-1].casefold() for f in layer.fields
    }
    on_wire: dict[str, tuple[int, int]] = {}
    for p in layer.payload_fields:
        leaf = p.field_path.rsplit("/", 1)[-1].casefold()
        seen, pop = on_wire.get(leaf, (0, 0))
        on_wire[leaf] = (seen + p.messages_seen, pop + p.messages_populated)

    for leaf, (seen, pop) in sorted(on_wire.items()):
        if pop == 0 and seen > 0:
            layer.unresolved.append(Unresolved(
                "declared_never_populated", leaf,
                f"present in {seen} observed message(s), never populated",
            ))
        elif leaf not in declared:
            layer.unresolved.append(Unresolved(
                "on_wire_not_declared", leaf,
                f"populated in {pop}/{seen} observed message(s) but not "
                "declared in any captured contract",
            ))


def _link_callers(layer: EvidenceLayer) -> None:
    """Fill ``called_by`` from ``calls`` so downstream traversal works both ways."""
    by_name: dict[str, list[str]] = {}
    for a in layer.artifacts:
        by_name.setdefault(a.name.casefold(), []).append(a.artifact_id)

    callers: dict[str, set[str]] = {}
    resolved_calls: dict[str, set[str]] = {}
    for a in layer.artifacts:
        for target in a.calls:
            ids = by_name.get(target.casefold())
            if not ids:
                layer.unresolved.append(Unresolved(
                    "unresolved_call", f"{a.artifact_id} -> {target}",
                    "callee not found among captured artifacts", a.path,
                ))
                continue
            for tid in ids:
                callers.setdefault(tid, set()).add(a.artifact_id)
                resolved_calls.setdefault(a.artifact_id, set()).add(tid)

    for i, a in enumerate(layer.artifacts):
        cb = callers.get(a.artifact_id)
        rc = resolved_calls.get(a.artifact_id)
        if cb or rc:
            layer.artifacts[i] = replace(
                a,
                called_by=tuple(sorted(cb or ())),
                calls=tuple(sorted(rc)) if rc else a.calls,
            )


_SYS_HINT = re.compile(r"([A-Za-z][A-Za-z0-9]{2,})2([A-Za-z][A-Za-z0-9]{2,})")
# Talend route/job naming prefixes. Left in place they become fictional
# systems: `rMaximo2Quintiq` would otherwise report a system called "rMaximo"
# sitting next to the real "Maximo".
_ARTIFACT_PREFIX = re.compile(r"^(r|j|t|c|sub|main)(?=[A-Z])")


def _strip_prefix(system: str) -> str:
    return _ARTIFACT_PREFIX.sub("", system)


def _infer_interfaces_from_artifacts(layer: EvidenceLayer) -> None:
    """Interfaces implied by artifact naming, where no register declares them.

    Kept strictly separate from register-declared interfaces via ``origin``:
    an inferred edge is a lead, a declared one is a fact, and a consumer must
    be able to tell which it has.
    """
    declared = {
        (i.source_system.casefold(), i.target_system.casefold())
        for i in layer.interfaces
    }
    for a in layer.artifacts:
        if a.kind not in ("job", "route", "joblet", "routelet"):
            continue
        m = _SYS_HINT.search(a.name)
        if not m:
            continue
        src, tgt = _strip_prefix(m.group(1)), _strip_prefix(m.group(2))
        if not src or not tgt:
            continue
        if (src.casefold(), tgt.casefold()) in declared:
            continue
        declared.add((src.casefold(), tgt.casefold()))
        layer.interfaces.append(InterfaceRecord(
            interface_id=f"inferred:{a.artifact_id}",
            source_system=src,
            target_system=tgt,
            origin="inferred",
            mechanism=_mechanism_of(a),
            artifact_ids=(a.artifact_id,),
            label=a.name,
            confidence=Confidence.INFERRED,
            notes="inferred from artifact name; no register declares this edge",
        ))


def _mechanism_of(a: ArtifactRecord) -> str:
    types = " ".join(a.component_types).lower()
    for needle, mech in (
        ("cjms", "jms"), ("jms", "jms"), ("rest", "http"), ("http", "http"),
        ("soap", "soap"), ("webservice", "soap"), ("file", "file"),
        ("ftp", "ftp"), ("sql", "db"), ("db", "db"),
    ):
        if needle in types:
            return mech
    return "unknown"


def _design_kind(path: Path, repo_root: Path) -> str:
    parts = set(path.relative_to(repo_root).parts)
    for d, kind in (("joblets", "joblet"), ("routelets", "routelet"),
                    ("routes", "route"), ("process", "job")):
        if d in parts:
            return kind
    return "job"


# ---------------------------------------------------------------------------
# stats -- the completeness gate, not a summary
# ---------------------------------------------------------------------------

def _stats(
    layer: EvidenceLayer, categoriser: Categoriser, classifier: Classifier
) -> dict:
    from collections import Counter

    by_stage = Counter(f.stage for f in layer.fields)
    by_kind = Counter(f.transform_kind for f in layer.fields)
    by_class = Counter(f.rule_class for f in layer.fields)
    by_cat = Counter(f.category for f in layer.fields)
    by_conf = Counter(f.confidence for f in layer.fields)
    uncategorised = by_cat.get(UNCATEGORISED, 0)

    return {
        "field_records": len(layer.fields),
        "payload_field_records": len(layer.payload_fields),
        "artifacts": len(layer.artifacts),
        "artifacts_by_kind": dict(Counter(a.kind for a in layer.artifacts)),
        "interfaces": len(layer.interfaces),
        "interfaces_by_origin": dict(Counter(i.origin for i in layer.interfaces)),
        "unresolved": len(layer.unresolved),
        "unresolved_by_kind": dict(Counter(u.kind for u in layer.unresolved)),
        "fields_by_stage": dict(by_stage),
        "fields_by_transform_kind": dict(by_kind),
        "fields_by_rule_class": dict(by_class),
        "fields_by_confidence": dict(by_conf),
        "categories_used": len([c for c in by_cat if c != UNCATEGORISED]),
        "categories_available": len(categoriser.names()),
        "uncategorised_fields": uncategorised,
        "uncategorised_ratio": round(uncategorised / len(layer.fields), 4)
        if layer.fields else 0.0,
        "top_categories": dict(by_cat.most_common(20)),
        "business_rule_artifacts": len({
            f.artifact_id for f in layer.fields
            if f.rule_class == RuleClass.BUSINESS
        }),
        "classification_signals_available": len(classifier.signal_ids()),
    }
