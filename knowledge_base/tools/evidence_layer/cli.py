"""Command line for the evidence layer.

    python -m evidence_layer build <repo-root> -o <out-dir> [--registers]
    python -m evidence_layer query <out-dir> carries --category asset --scope 4ps
    python -m evidence_layer query <out-dir> structure --scope Ortec --stage offered
    python -m evidence_layer query <out-dir> wire --scope Ortec
    python -m evidence_layer query <out-dir> business [--scope ...]
    python -m evidence_layer query <out-dir> dossier --name medewerkergegevens
    python -m evidence_layer query <out-dir> flow --scope Maximo2Quintiq
    python -m evidence_layer query <out-dir> coverage
    python -m evidence_layer docs <out-dir> -o <docs-dir> --scope werkorder
    python -m evidence_layer categories
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import query as Q
from .build import build
from .derive.categories import Categoriser
from .models import EvidenceLayer


def _write(out_dir: Path, layer: EvidenceLayer) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    def dump(name: str, rows: list) -> None:
        # JSONL: streamable, greppable, and diffable between scans -- which a
        # single pretty-printed blob is not.
        path = out_dir / name
        with path.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r.as_dict(), ensure_ascii=False,
                                    sort_keys=True) + "\n")

    dump("fields.jsonl", layer.fields)
    dump("payload_fields.jsonl", layer.payload_fields)
    dump("artifacts.jsonl", layer.artifacts)
    dump("interfaces.jsonl", layer.interfaces)
    dump("unresolved.jsonl", layer.unresolved)
    (out_dir / "stats.json").write_text(
        json.dumps(layer.stats, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _cmd_build(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    if not repo_root.is_dir():
        print(f"not a directory: {repo_root}", file=sys.stderr)
        return 2
    out_dir = Path(args.out).resolve()

    layer = build(
        repo_root,
        include_registers=args.registers,
        register_root=Path(args.register_root).resolve() if args.register_root else None,
        payload_dirs=[Path(p).resolve() for p in args.payload_dir] or None,
        category_overlays=tuple(Path(p) for p in args.categories),
        classification_overlays=tuple(Path(p) for p in args.classification),
        max_payload_messages=args.max_messages,
        progress=lambda m: print(m, flush=True),
    )
    _write(out_dir, layer)
    print(json.dumps(layer.stats, indent=2, sort_keys=True))
    print(f"\nwritten to {out_dir}")
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    layer = Q.load(Path(args.out_dir).resolve())
    if not layer.fields and not layer.payload_fields:
        print("empty layer -- run `build` first", file=sys.stderr)
        return 2

    if args.what == "carries":
        result = Q.carries_category(layer, args.category, args.scope, args.stage)
    elif args.what == "structure":
        result = Q.structure_at_stage(layer, args.scope, args.stage or "offered")
    elif args.what == "wire":
        result = Q.wire_structure(layer, args.scope)
    elif args.what == "business":
        result = Q.business_logic_locations(layer, args.scope)
    elif args.what == "dossier":
        result = Q.artifact_dossier(layer, args.name or args.scope)
    elif args.what == "flow":
        result = Q.interface_content(layer, args.scope)
    elif args.what == "coverage":
        result = Q.coverage_report(layer)
    else:  # pragma: no cover - argparse restricts this
        print(f"unknown query: {args.what}", file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _cmd_docs(args: argparse.Namespace) -> int:
    from .publish.docs import generate

    layer = Q.load(Path(args.out_dir).resolve())
    if not layer.fields:
        print("empty layer -- run `build` first", file=sys.stderr)
        return 2
    written = generate(layer, Path(args.out).resolve(),
                       scope=args.scope, title=args.title)
    for p in written:
        print(f"  {p}  ({p.stat().st_size:,} bytes)")
    return 0


def _cmd_categories(_: argparse.Namespace) -> int:
    cats = Categoriser.load()
    print(json.dumps(cats.describe(), indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="evidence_layer",
        description="Field-level integration evidence: capture everything, "
                    "then answer by filtering.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="capture + derive into an output directory")
    b.add_argument("repo_root")
    b.add_argument("-o", "--out", required=True)
    b.add_argument("--registers", action="store_true",
                   help="also read xlsx/csv table exports (credentials are "
                        "redacted on read and reported, never stored)")
    b.add_argument("--register-root", default="",
                   help="limit register reading to this subtree")
    b.add_argument("--payload-dir", action="append", default=[],
                   help="explicit sample-message directory; repeatable. "
                        "Omit to auto-discover.")
    b.add_argument("--categories", action="append", default=[],
                   help="category vocabulary overlay (yaml); repeatable")
    b.add_argument("--classification", action="append", default=[],
                   help="classification signal overlay (yaml); repeatable")
    b.add_argument("--max-messages", type=int, default=200,
                   help="cap on sample messages read per directory")
    b.set_defaults(func=_cmd_build)

    q = sub.add_parser("query", help="ask a question of a built layer")
    q.add_argument("out_dir")
    q.add_argument("what", choices=[
        "carries", "structure", "wire", "business", "dossier", "flow", "coverage",
    ])
    q.add_argument("--category", default="")
    q.add_argument("--scope", default="")
    q.add_argument("--stage", default="")
    q.add_argument("--name", default="")
    q.set_defaults(func=_cmd_query)

    d = sub.add_parser("docs", help="render a document set as a view of the layer")
    d.add_argument("out_dir", help="built layer directory")
    d.add_argument("-o", "--out", required=True, help="where to write the docs")
    d.add_argument("--scope", default="", help="regex scope filter, e.g. a business object")
    d.add_argument("--title", default="Integration evidence")
    d.set_defaults(func=_cmd_docs)

    c = sub.add_parser("categories", help="list the category vocabulary")
    c.set_defaults(func=_cmd_categories)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
