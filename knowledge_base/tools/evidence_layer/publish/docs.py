"""Generate a document set as a VIEW over the evidence layer.

The document set is not the deliverable -- the layer is. These files are one
rendering of it, scoped by a filter, and regenerating them for a different
business object or system is a different ``--scope``, not a rewrite. That is
the whole point: a hand-written document freezes one answer set, and the next
question needs a new document.

Output is ASCII-only (Windows cp1252 compatibility, per the platform rule) and
byte-stable for a given layer and scope, so the files diff cleanly between
scans.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from ..models import (
    Confidence, EvidenceLayer, FieldRecord, RuleClass, Stage, UNCATEGORISED,
)
from ..query import Filter, fields as q_fields

_ASCII = re.compile(r"[^\x20-\x7e\n]")


def _ascii(text: str) -> str:
    """Fold anything outside printable ASCII. Generated docs must survive cp1252."""
    return _ASCII.sub("?", text.replace("\u2019", "'").replace("\u2014", "--")
                      .replace("\u2013", "-").replace("\u00e9", "e"))


def _cell(text: str, limit: int = 160) -> str:
    """Table-safe single line."""
    t = _ascii(str(text)).replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return (t[: limit - 3] + "...") if len(t) > limit else t


def _short(artifact_id: str) -> str:
    return artifact_id.rsplit("/", 1)[-1]


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    if not rows:
        return ["_No rows._", ""]
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    out.append("")
    return out


def generate(
    layer: EvidenceLayer,
    out_dir: Path,
    scope: str = "",
    title: str = "Integration evidence",
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    flt = Filter(scope=scope)
    sel = q_fields(layer, flt)

    rx = re.compile(scope, re.IGNORECASE) if scope else None

    def in_scope(text: str) -> bool:
        return not rx or bool(rx.search(text or ""))

    artifacts = [a for a in layer.artifacts
                 if in_scope(a.artifact_id) or in_scope(a.name)]
    payloads = [p for p in layer.payload_fields
                if in_scope(p.source_id) or in_scope(p.message_root)]
    interfaces = [i for i in layer.interfaces
                  if in_scope(i.interface_id) or in_scope(i.label)
                  or in_scope(f"{i.source_system}2{i.target_system}")]
    unresolved = [u for u in layer.unresolved
                  if in_scope(u.subject) or in_scope(u.evidence_file)]

    written: list[Path] = []
    for name, lines in (
        ("00_OVERVIEW.md", _overview(title, scope, sel, artifacts, interfaces,
                                     payloads, unresolved, layer)),
        ("01_FIELD_INVENTORY.md", _field_inventory(title, scope, sel)),
        ("02_WIRE_STRUCTURE.md", _wire(title, scope, payloads)),
        ("03_TRANSFORMATIONS.md", _transformations(title, scope, sel)),
        ("04_OPEN_ITEMS.md", _open_items(title, scope, unresolved)),
        ("05_ARTIFACTS.md", _artifacts(title, scope, artifacts, sel)),
    ):
        path = out_dir / name
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="ascii",
                        errors="replace")
        written.append(path)
    return written


# ---------------------------------------------------------------------------

def _hdr(title: str, scope: str, doc: str) -> list[str]:
    return [
        f"# {_ascii(title)} -- {doc}",
        "",
        f"Scope filter: `{_cell(scope) or '(everything)'}`",
        "",
        "Generated from the evidence layer. Every row traces to a file and a",
        "locator; nothing here is authored prose. Confidence values are carried",
        "through from capture: `certain` was read from a declaration, `likely`",
        "took one unambiguous resolution step, `inferred` is heuristic, and",
        "`unresolved` is recorded rather than dropped.",
        "",
    ]


def _overview(title, scope, sel, artifacts, interfaces, payloads, unresolved,
              layer) -> list[str]:
    L = _hdr(title, scope, "Overview")
    by_stage = Counter(f.stage for f in sel)
    by_cat = Counter(f.category for f in sel)
    by_class = Counter(f.rule_class for f in sel)
    by_conf = Counter(f.confidence for f in sel)

    L += ["## Scale", ""]
    L += _table(["Measure", "Value"], [
        ["field records", str(len(sel))],
        ["artifacts", str(len(artifacts))],
        ["interfaces", str(len(interfaces))],
        ["observed message fields", str(len(payloads))],
        ["open items", str(len(unresolved))],
    ])

    L += ["## Fields by stage", "",
          "`offered` is what the source puts on the wire, `mapped` what a",
          "transformation produces, `written` what the target actually",
          "receives. These differ, often by an order of magnitude, and a",
          "single-stage field list is therefore not a contract.", ""]
    L += _table(["Stage", "Fields"],
                [[s, str(by_stage.get(s, 0))] for s in Stage.ALL])

    L += ["## Fields by semantic category", ""]
    L += _table(["Category", "Fields"],
                [[c, str(n)] for c, n in by_cat.most_common()
                 if c != UNCATEGORISED]
                + ([[UNCATEGORISED, str(by_cat[UNCATEGORISED])]]
                   if by_cat.get(UNCATEGORISED) else []))

    L += ["## Transformation character", "",
          "`business` means a domain decision is encoded in the integration",
          "layer. `workaround` means do not rebuild it.", ""]
    L += _table(["Class", "Fields"],
                [[c, str(by_class.get(c, 0))] for c in RuleClass.ALL])

    L += ["## Evidence confidence", ""]
    L += _table(["Confidence", "Fields"],
                [[c, str(by_conf.get(c, 0))] for c in Confidence.ALL])

    if interfaces:
        L += ["## Interfaces in scope", "",
              "`origin` matters: `register` was declared by a configuration",
              "table and is authoritative for routing; `inferred` was derived",
              "from naming and is a lead, not a fact.", ""]
        L += _table(
            ["Source", "Target", "Origin", "Mechanism", "Mapping", "Label"],
            [[_cell(i.source_system, 30), _cell(i.target_system, 30), i.origin,
              _cell(i.mechanism, 12), _cell(i.mapping_ref, 46),
              _cell(i.label, 46)] for i in interfaces[:120]])
    return L


def _field_inventory(title, scope, sel) -> list[str]:
    L = _hdr(title, scope, "Field inventory")
    L += ["The field contract, grouped by stage. This is the answer to",
          "\"which fields cross this boundary\" -- and it has a different",
          "answer per stage by design.", ""]
    for stage in Stage.ALL:
        rows_by_field: dict[str, list[FieldRecord]] = defaultdict(list)
        for f in sel:
            if f.stage == stage:
                rows_by_field[f.field_path].append(f)
        L += [f"## Stage: {stage} ({len(rows_by_field)} distinct fields)", ""]
        rows = []
        for fp in sorted(rows_by_field, key=lambda s: (s.casefold(), s))[:600]:
            recs = rows_by_field[fp]
            r0 = recs[0]
            rows.append([
                _cell(fp, 70),
                _cell(next((r.field_type for r in recs if r.field_type), ""), 18),
                r0.category,
                r0.transform_kind,
                _cell(next((r.source_expr for r in recs if r.source_expr), ""), 90),
                _cell(_short(r0.artifact_id), 44),
            ])
        L += _table(["Field", "Type", "Category", "Transform", "Source", "Artifact"],
                    rows)
        if len(rows_by_field) > 600:
            L += [f"_Truncated: {len(rows_by_field) - 600} further fields at "
                  f"this stage. Query the layer directly for the full set._", ""]
    return L


def _wire(title, scope, payloads) -> list[str]:
    L = _hdr(title, scope, "Observed wire structure")
    L += ["Measured from real message populations: what is declared versus",
          "what is actually populated. A field declared in a contract and",
          "never populated in any observed message is not derivable from code",
          "at all.",
          "",
          "Value domains are shown only where they are a small closed set. A",
          "suppressed domain means high cardinality or free text -- which is",
          "also where personal data lives, so it is not reproduced.", ""]
    agg: dict[tuple[str, str], list[int]] = {}
    domains: dict[tuple[str, str], tuple[str, ...]] = {}
    suppressed: set[tuple[str, str]] = set()
    for p in payloads:
        key = (p.message_root, p.field_path)
        seen, pop = agg.get(key, [0, 0])
        agg[key] = [seen + p.messages_seen, pop + p.messages_populated]
        if p.value_domain_truncated:
            suppressed.add(key)
        elif p.value_domain:
            domains[key] = tuple(sorted(set(domains.get(key, ())) |
                                        set(p.value_domain)))

    for root in sorted({r for r, _ in agg}):
        keys = [k for k in agg if k[0] == root]
        L += [f"## Message root: `{_cell(root, 60)}`", ""]
        rows = []
        for key in sorted(keys, key=lambda k: (k[1].casefold(), k[1])):
            seen, pop = agg[key]
            dom = "(suppressed)" if key in suppressed else _cell(
                ", ".join(domains.get(key, ())), 80)
            rows.append([
                _cell(key[1], 60), str(seen), str(pop),
                f"{pop / seen:.0%}" if seen else "-", dom,
            ])
        L += _table(["Field", "Seen", "Populated", "Fill", "Value domain"], rows)
        never = [k[1] for k in keys if agg[k][1] == 0]
        if never:
            L += [f"**Never populated in any observed message ({len(never)}):** "
                  + ", ".join(f"`{_cell(n, 40)}`" for n in sorted(never)), ""]
    return L


def _transformations(title, scope, sel) -> list[str]:
    L = _hdr(title, scope, "Transformations")
    L += ["Every transformation, classified, with the signal that produced the",
          "verdict. Disagree with the signal, not with the label -- the rules",
          "are in `vocab/classification.yaml`.", ""]
    for cls, blurb in (
        (RuleClass.BUSINESS,
         "A domain decision encoded in the integration layer. Under a "
         "policy that forbids business logic there, each of these is either "
         "a migration item or an explicit exception."),
        (RuleClass.WORKAROUND,
         "Historical accidents. These should not be rebuilt."),
        (RuleClass.TECHNICAL,
         "Format, unit, encoding, envelope. Legitimately belongs in an "
         "integration layer."),
    ):
        recs = [f for f in sel if f.rule_class == cls]
        per: dict[str, list[FieldRecord]] = defaultdict(list)
        for f in recs:
            per[f.artifact_id].append(f)
        L += [f"## {cls} ({len(recs)} in {len(per)} artifacts)", "", blurb, ""]
        for aid in sorted(per, key=lambda a: (-len(per[a]), a))[:40]:
            L += [f"### {_cell(_short(aid), 80)}", "",
                  f"`{_cell(aid, 200)}`", ""]
            L += _table(["Field", "Signal", "Expression"], [
                [_cell(f.field_path, 56),
                 _cell(", ".join(s.split(":", 1)[-1] for s in f.rule_signals), 40),
                 _cell(f.source_expr, 150)]
                for f in per[aid][:30]
            ])
        if len(per) > 40:
            L += [f"_Truncated: {len(per) - 40} further artifacts in this class._",
                  ""]
    return L


def _open_items(title, scope, unresolved) -> list[str]:
    L = _hdr(title, scope, "Open items")
    L += ["Everything the layer could not resolve, kept as data. A recorded",
          "gap is a work item; a dropped gap is a wrong answer waiting to",
          "happen. These are also the questions worth putting to a human,",
          "because none of them is derivable from the source.", ""]
    per: dict[str, list] = defaultdict(list)
    for u in unresolved:
        per[u.kind].append(u)
    L += _table(["Kind", "Count"],
                [[k, str(len(v))] for k, v in
                 sorted(per.items(), key=lambda kv: -len(kv[1]))])
    for kind in sorted(per, key=lambda k: -len(per[k])):
        L += [f"## {kind} ({len(per[kind])})", ""]
        L += _table(["Subject", "Reason"], [
            [_cell(u.subject, 90), _cell(u.reason, 130)]
            for u in per[kind][:60]
        ])
        if len(per[kind]) > 60:
            L += [f"_Truncated: {len(per[kind]) - 60} more._", ""]
    return L


def _artifacts(title, scope, artifacts, sel) -> list[str]:
    L = _hdr(title, scope, "Artifacts")
    own: dict[str, list[FieldRecord]] = defaultdict(list)
    for f in sel:
        own[f.artifact_id].append(f)
    L += _table(
        ["Artifact", "Kind", "Group", "Fields", "Business", "Calls", "Called by"],
        [[_cell(_short(a.artifact_id), 60), a.kind, _cell(a.group, 40),
          str(len(own.get(a.artifact_id, []))),
          str(sum(1 for f in own.get(a.artifact_id, [])
                  if f.rule_class == RuleClass.BUSINESS)),
          str(len(a.calls)), str(len(a.called_by))]
         for a in sorted(artifacts, key=lambda a: (a.kind, a.artifact_id))[:400]])

    L += ["## Dossiers", ""]
    ranked = sorted(artifacts,
                    key=lambda a: -len(own.get(a.artifact_id, [])))[:30]
    for a in ranked:
        recs = own.get(a.artifact_id, [])
        if not recs:
            continue
        L += [f"### {_cell(_short(a.artifact_id), 80)}", "",
              f"- path: `{_cell(a.path or a.artifact_id, 200)}`",
              f"- kind: {a.kind}" + (f" | group: {_cell(a.group, 60)}" if a.group else ""),
        ]
        if a.purpose:
            L += [f"- component labels: {_cell(a.purpose, 220)}"]
        if a.mapping_refs:
            L += [f"- mappings used: {_cell(', '.join(a.mapping_refs), 200)}"]
        if a.calls:
            L += [f"- calls downstream: {_cell(', '.join(_short(c) for c in a.calls), 200)}"]
        if a.called_by:
            L += [f"- called by: {_cell(', '.join(_short(c) for c in a.called_by), 200)}"]
        if a.endpoints:
            L += [f"- endpoints/queries: {len(a.endpoints)} captured"]
        cats = Counter(f.category for f in recs)
        L += [f"- categories present: "
              + _cell(", ".join(f"{c}={n}" for c, n in cats.most_common()
                                if c != UNCATEGORISED), 220),
              ""]
    return L
