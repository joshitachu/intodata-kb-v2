"""Layered-RAG evaluation harness — golden-set retrieval + ladder gate.

Two evaluation scopes (select with ``--mode``):

  retrieval  Raw retrieval metrics against the running Qdrant stack:
             recall@30 pre-rerank, recall@8 post-rerank, and source-layer
             selection accuracy.

  ladder     The conversational retrieval ladder itself, via
             ``_prepare_rag_context`` against a transient conversation, so
             the exact contract chat uses is measured: ``fallback_used``,
             ``primary_source_layer``, ``doc_gap``, refusal correctness, and
             per-category latency. Also surfaces the Slice 5 inspection
             warnings (alias-row count, alias-match timing).

  both       Run retrieval then ladder for every applicable case.

The runner is admin-level (``user_groups=["admin"]``) and project-scoped; the
report states this explicitly. Fixtures are typed and validated; unsupported
cases may not declare an expected source. The committed Markdown report is the
durable artifact — it never embeds full answers, raw chunk text, or raw
``_internal/`` paths (only friendly ``display_name`` values).

Usage:
    cd RAG_pipeline/backend
    uv run python scripts/retrieval_eval.py \\
      --fixture scripts/eval_fixtures/layered_rag_phase2.json \\
      --project-id phase2 --mode both \\
      --output-md scripts/eval_reports/layered_rag_slice6.md \\
      --output-json scripts/eval_reports/layered_rag_slice6.json
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# Allow running from the project root or backend/ without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent))

CATEGORIES = (
    "final_doc_answerable",
    "phase2_only",
    "unsupported",
    "entity_lookup",
    "risk_config_dependency",
)

# Reporting thresholds — surfaced as warnings, not hard gates (see plan).
ALIAS_ROW_WARN = 5000
LADDER_LATENCY_WARN_MS = 30000

# Aggregate pass gates (acceptance criteria).
MIN_RECALL_AT_8 = 0.90
MIN_LAYER_ACCURACY = 0.90


class FixtureCase(BaseModel):
    """One golden-set evaluation case."""

    id: str
    category: str
    question: str
    project_id: str | None = None
    expected_behavior: Literal["answer", "refuse"] = "answer"
    expected_filename: str | None = None
    expected_section: str | None = None
    expected_unit: str | None = None
    expected_source_layers: list[str] | None = None
    expected_primary_source_layer: str | None = None
    expected_answer_contains: list[str] = Field(default_factory=list)
    forbidden_answer_contains: list[str] = Field(default_factory=list)
    must_pass: bool = False
    notes: str | None = None

    @model_validator(mode="after")
    def _validate_category(self) -> "FixtureCase":
        if self.category not in CATEGORIES:
            raise ValueError(f"{self.id}: unknown category {self.category!r}")
        if self.category == "unsupported":
            if self.expected_behavior != "refuse":
                raise ValueError(f"{self.id}: unsupported cases must expect refuse")
            if self.expected_filename or self.expected_source_layers:
                raise ValueError(
                    f"{self.id}: unsupported cases must not declare an expected source"
                )
        elif self.expected_behavior == "answer" and not (
            self.expected_filename or self.expected_source_layers
        ):
            raise ValueError(
                f"{self.id}: answerable cases need expected_filename or expected_source_layers"
            )
        return self


def parse_fixture(raw: list[dict]) -> list[FixtureCase]:
    """Parse fixture rows, accepting the legacy {question, expected_filename} shape."""
    cases: list[FixtureCase] = []
    for i, row in enumerate(raw):
        data = dict(row)
        data.setdefault("id", f"case-{i + 1}")
        if "category" not in data:
            data["category"] = "final_doc_answerable"
        cases.append(FixtureCase(**data))
    return cases


# --- Scoring (pure functions, unit-tested without the live stack) ----------


class SourceLite(BaseModel):
    """The subset of a Source/DocumentSource the scorer needs."""

    filename: str
    source_layer: str | None = None
    section: str | None = None
    unit: str | None = None
    display_name: str | None = None


def filename_hit(expected: str | None, sources: list[SourceLite]) -> bool:
    if not expected:
        return False
    return any(expected in s.filename for s in sources)


def layer_hit(expected_layers: list[str] | None, sources: list[SourceLite]) -> bool:
    if not expected_layers:
        return False
    present = {s.source_layer for s in sources if s.source_layer}
    return any(layer in present for layer in expected_layers)


def section_hit(expected: str | None, sources: list[SourceLite]) -> bool:
    if not expected:
        return True  # not asserted
    return any(s.section and expected in s.section for s in sources)


def unit_hit(expected: str | None, sources: list[SourceLite]) -> bool:
    if not expected:
        return True
    return any(s.unit == expected for s in sources)


def answer_contains_ok(expected: list[str], answer: str) -> bool:
    low = answer.lower()
    return all(term.lower() in low for term in expected)


def forbidden_absent(forbidden: list[str], answer: str) -> bool:
    low = answer.lower()
    return not any(term.lower() in low for term in forbidden)


def score_ladder_case(
    case: FixtureCase,
    *,
    answer: str,
    sources: list[SourceLite],
    primary_source_layer: str | None,
) -> tuple[bool, list[str]]:
    """Return (passed, reasons) for one ladder case. Pure — no I/O."""
    reasons: list[str] = []

    if case.expected_behavior == "refuse":
        # Per plan: a refusal passes when it does not assert fixture-defined
        # factual claims. Retrieving canonical chunks and then saying "not
        # found" is a valid grounded refusal — source presence alone is not a
        # failure; fabrication is.
        if not forbidden_absent(case.forbidden_answer_contains, answer):
            reasons.append("refusal leaked a forbidden factual claim")
        return (not reasons), reasons

    if case.expected_primary_source_layer and primary_source_layer != case.expected_primary_source_layer:
        reasons.append(
            f"primary layer {primary_source_layer!r} != expected "
            f"{case.expected_primary_source_layer!r}"
        )
    if case.expected_filename and not filename_hit(case.expected_filename, sources):
        reasons.append(f"expected file {case.expected_filename!r} absent from sources")
    if case.expected_source_layers and not layer_hit(case.expected_source_layers, sources):
        reasons.append(f"expected layer {case.expected_source_layers} absent")
    if not section_hit(case.expected_section, sources):
        reasons.append(f"expected section {case.expected_section!r} absent")
    if not unit_hit(case.expected_unit, sources):
        reasons.append(f"expected unit {case.expected_unit!r} absent")
    if not answer_contains_ok(case.expected_answer_contains, answer):
        reasons.append("answer missing expected substring(s)")
    if not forbidden_absent(case.forbidden_answer_contains, answer):
        reasons.append("answer contains forbidden substring(s)")
    return (not reasons), reasons


# --- Report rendering (pure) -----------------------------------------------


def render_report(summary: dict) -> str:
    """Render the Markdown report from an aggregate summary dict. Pure."""
    lines: list[str] = []
    lines.append("# Layered RAG — Slice 6 Evaluation Report\n")
    meta = summary["meta"]
    lines.append(f"- Generated: {meta['timestamp']}")
    lines.append(f"- Git commit: {meta['git_commit']}")
    lines.append(f"- Project: `{meta['project_id']}`")
    lines.append(f"- Fixture: `{meta['fixture']}`")
    lines.append(f"- Access level: `{meta['user_groups']}` (admin-level eval)")
    lines.append(f"- Mode: {meta['mode']}")
    lines.append(f"- Cases: {meta['total_cases']}")
    lines.append("")
    lines.append("## Config values at run time\n")
    lines.append("| Setting | Value |")
    lines.append("|---|---|")
    for k, v in summary["config"].items():
        lines.append(f"| `{k}` | {v} |")
    lines.append("")

    if "retrieval" in summary:
        r = summary["retrieval"]
        lines.append("## Retrieval metrics\n")
        lines.append(f"- recall@30 (pre-rerank): {r['recall_at_30']:.1%} ({r['recall_at_30_hits']}/{r['indexed_cases']})")
        lines.append(f"- recall@8 (post-rerank): {r['recall_at_8']:.1%} ({r['recall_at_8_hits']}/{r['indexed_cases']})")
        lines.append(f"- source-layer accuracy: {r['layer_accuracy']:.1%} ({r['layer_hits']}/{r['layer_cases']})")
        lines.append("")

    if "ladder" in summary:
        lines.append("## Ladder metrics by category\n")
        lines.append("| Category | Pass | Total | must_pass | Avg ms |")
        lines.append("|---|---|---|---|---|")
        for cat, c in summary["ladder"]["by_category"].items():
            lines.append(
                f"| {cat} | {c['passed']} | {c['total']} | "
                f"{c['must_pass_passed']}/{c['must_pass_total']} | {c['avg_ms']:.0f} |"
            )
        lines.append("")
        lines.append("### Case detail\n")
        lines.append("| ID | Category | Result | Layer | Fallback | DocGap | ms | Sources |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for c in summary["ladder"]["cases"]:
            srcs = ", ".join(c["source_display_names"][:3]) or "—"
            lines.append(
                f"| {c['id']} | {c['category']} | {'PASS' if c['passed'] else 'FAIL'} | "
                f"{c['primary_source_layer'] or '—'} | {c['fallback_used']} | {c['doc_gap']} | "
                f"{c['elapsed_ms']:.0f} | {srcs} |"
            )
            if not c["passed"]:
                lines.append(f"|  | ↳ {'; '.join(c['reasons'])} ||||||| ")
        lines.append("")

    lines.append("## Slice 5 inspection metrics\n")
    s5 = summary["slice5"]
    warn = f"  WARN exceeds {ALIAS_ROW_WARN}" if s5["alias_rows"] > ALIAS_ROW_WARN else ""
    lines.append(f"- Alias rows for project: {s5['alias_rows']}{warn}")
    if s5.get("alias_match_ms_avg") is not None:
        lines.append(f"- Alias-match avg time: {s5['alias_match_ms_avg']:.1f} ms")
    if "ladder" in summary:
        lat = summary["ladder"]["latency"]
        lwarn = f"  WARN exceeds {LADDER_LATENCY_WARN_MS}" if lat["max_ms"] > LADDER_LATENCY_WARN_MS else ""
        lines.append(
            f"- Ladder latency — rung1 avg {lat['rung1_avg_ms']:.0f} ms | "
            f"rung2 answer avg {lat['rung2_avg_ms']:.0f} ms | "
            f"refusal avg {lat['refuse_avg_ms']:.0f} ms | worst {lat['max_ms']:.0f} ms{lwarn}"
        )
    lines.append("")

    lines.append("## Verdicts\n")
    for v in summary["verdicts"]:
        lines.append(f"- {v}")
    lines.append("")
    if summary.get("notes"):
        lines.append("## Notes\n")
        for note in summary["notes"]:
            lines.append(f"- {note}")
        lines.append("")
    return "\n".join(lines)


def compute_verdicts(summary: dict) -> list[str]:
    """Data-backed gate results. Pure."""
    verdicts: list[str] = []
    if "retrieval" in summary:
        r = summary["retrieval"]
        ok = r["recall_at_8"] >= MIN_RECALL_AT_8
        verdicts.append(
            f"{'PASS' if ok else 'FAIL'}: recall@8 {r['recall_at_8']:.1%} (gate {MIN_RECALL_AT_8:.0%})"
        )
        la_ok = r["layer_accuracy"] >= MIN_LAYER_ACCURACY
        verdicts.append(
            f"{'PASS' if la_ok else 'FAIL'}: source-layer accuracy {r['layer_accuracy']:.1%} "
            f"(gate {MIN_LAYER_ACCURACY:.0%})"
        )
    if "ladder" in summary:
        mp = summary["ladder"]["must_pass"]
        verdicts.append(
            f"{'PASS' if mp['failed'] == 0 else 'FAIL'}: operator must-pass set "
            f"{mp['passed']}/{mp['total']}"
        )
        unsup = summary["ladder"].get("unsupported", {})
        if unsup and unsup["total"]:
            verdicts.append(
                f"{'PASS' if unsup['leaked'] == 0 else 'FAIL'}: unsupported refusals with "
                f"zero factual leak ({unsup['clean']}/{unsup['total']})"
            )
    return verdicts


# --- Live-stack execution --------------------------------------------------


def _to_lite(sources) -> list[SourceLite]:
    return [
        SourceLite(
            filename=s.filename,
            source_layer=getattr(s, "source_layer", None),
            section=getattr(s, "section", None),
            unit=getattr(s, "unit", None),
            display_name=getattr(s, "display_name", None),
        )
        for s in sources
    ]


async def run_retrieval(client, cases: list[FixtureCase], project_ids, top_k: int) -> dict:
    from src.retrieval.search import search_and_rerank, search_layered, search_similar_hybrid

    # Filename recall is a canonical-doc metric — only score cases that declare
    # an expected_filename. Layer presence is scored separately, and for
    # internal-layer cases through search_layered (rung 2), since the default
    # rerank path returns only canonical_docs by design.
    filename_cases = [
        c for c in cases if c.expected_behavior == "answer" and c.expected_filename
    ]
    layer_cases = [
        c for c in cases if c.expected_behavior == "answer" and c.expected_source_layers
    ]
    pre_hits = post_hits = layer_hits = 0

    for case in filename_cases:
        candidates = await search_similar_hybrid(
            client=client, query=case.question, user_groups=["admin"],
            top_k=30, threshold=0.0, project_ids=project_ids,
        )
        if filename_hit(case.expected_filename, _to_lite(candidates)):
            pre_hits += 1
        results = await search_and_rerank(
            client=client, query=case.question, user_groups=["admin"],
            top_k=top_k, project_ids=project_ids,
        )
        if filename_hit(case.expected_filename, _to_lite(results)):
            post_hits += 1

    for case in layer_cases:
        layers = case.expected_source_layers
        # Canonical-only expectation → check the default path; otherwise probe
        # the named internal layers directly via the rung-2 helper.
        if layers == ["canonical_docs"]:
            results = await search_and_rerank(
                client=client, query=case.question, user_groups=["admin"],
                top_k=top_k, project_ids=project_ids,
            )
        else:
            internal_layers = [l for l in layers if l != "canonical_docs"]
            results = await search_layered(
                client, case.question, ["admin"],
                source_layers=internal_layers, top_k=top_k, project_ids=project_ids,
            )
        if layer_hit(layers, _to_lite(results)):
            layer_hits += 1

    fn_n = len(filename_cases) or 1
    return {
        "indexed_cases": len(filename_cases),
        "recall_at_30": pre_hits / fn_n,
        "recall_at_30_hits": pre_hits,
        "recall_at_8": post_hits / fn_n,
        "recall_at_8_hits": post_hits,
        "layer_cases": len(layer_cases),
        "layer_hits": layer_hits,
        "layer_accuracy": (layer_hits / len(layer_cases)) if layer_cases else 1.0,
    }


async def run_ladder(client, db, cases: list[FixtureCase], project_ids) -> dict:
    from src.db.models import Conversation
    from src.generation.conversational_rag import _prepare_rag_context
    from src.retrieval.entity_matcher import match_query_entities

    case_rows: list[dict] = []
    alias_match_times: list[float] = []

    for case in cases:
        conv = Conversation(user_id=1)
        conv.messages = []

        if case.category == "entity_lookup":
            t0 = time.perf_counter()
            await match_query_entities(db, case.question, project_ids)
            alias_match_times.append((time.perf_counter() - t0) * 1000)

        start = time.perf_counter()
        ctx = await _prepare_rag_context(
            conv, case.question, ["admin"], client,
            project_ids=project_ids, db=db, precompute=True,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        answer = (ctx.precomputed_answer or "").replace("[NEED_SEARCH]", "").strip()
        lite = _to_lite(ctx.document_sources)
        passed, reasons = score_ladder_case(
            case, answer=answer, sources=lite,
            primary_source_layer=ctx.primary_source_layer,
        )
        case_rows.append({
            "id": case.id,
            "category": case.category,
            "passed": passed,
            "reasons": reasons,
            "primary_source_layer": ctx.primary_source_layer,
            "fallback_used": ctx.fallback_used,
            "doc_gap": ctx.doc_gap,
            "elapsed_ms": elapsed_ms,
            "must_pass": case.must_pass,
            "expected_behavior": case.expected_behavior,
            "source_display_names": [s.display_name or s.filename for s in lite],
        })

    by_cat: dict[str, dict] = {}
    for cat in CATEGORIES:
        rows = [r for r in case_rows if r["category"] == cat]
        if not rows:
            continue
        mp = [r for r in rows if r["must_pass"]]
        by_cat[cat] = {
            "passed": sum(r["passed"] for r in rows),
            "total": len(rows),
            "must_pass_passed": sum(r["passed"] for r in mp),
            "must_pass_total": len(mp),
            "avg_ms": sum(r["elapsed_ms"] for r in rows) / len(rows),
        }

    mp_rows = [r for r in case_rows if r["must_pass"]]
    unsup_rows = [r for r in case_rows if r["expected_behavior"] == "refuse"]
    rung1 = [r["elapsed_ms"] for r in case_rows if not r["fallback_used"] and r["expected_behavior"] == "answer"]
    rung2 = [r["elapsed_ms"] for r in case_rows if r["doc_gap"]]
    refuse = [r["elapsed_ms"] for r in case_rows if r["expected_behavior"] == "refuse"]
    all_ms = [r["elapsed_ms"] for r in case_rows] or [0.0]

    def _avg(xs):
        return sum(xs) / len(xs) if xs else 0.0

    return {
        "cases": case_rows,
        "by_category": by_cat,
        "must_pass": {
            "passed": sum(r["passed"] for r in mp_rows),
            "failed": sum(not r["passed"] for r in mp_rows),
            "total": len(mp_rows),
        },
        "unsupported": {
            "total": len(unsup_rows),
            "clean": sum(r["passed"] for r in unsup_rows),
            "leaked": sum(not r["passed"] for r in unsup_rows),
        },
        "latency": {
            "rung1_avg_ms": _avg(rung1),
            "rung2_avg_ms": _avg(rung2),
            "refuse_avg_ms": _avg(refuse),
            "max_ms": max(all_ms),
        },
        "alias_match_ms_avg": (_avg(alias_match_times) if alias_match_times else None),
    }


async def _git_commit() -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "--short", "HEAD",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        return out.decode().strip() or "unknown"
    except (FileNotFoundError, OSError):
        return "unknown"


async def _alias_row_count(db, project_id: str | None) -> int:
    from sqlalchemy import func, select
    from src.db.models import EntityAlias
    stmt = select(func.count()).select_from(EntityAlias)
    if project_id:
        stmt = stmt.where(EntityAlias.project_id == project_id)
    return int((await db.execute(stmt)).scalar() or 0)


async def _run(args) -> None:
    from qdrant_client import AsyncQdrantClient
    from src.config import settings
    from src.db.session import async_session_factory

    cases = parse_fixture(json.loads(args.fixture.read_text(encoding="utf-8")))
    if args.project_id:
        cases = [c for c in cases if c.project_id in (None, args.project_id)]
    if not cases:
        print("No cases to evaluate.")
        return

    project_ids = [args.project_id] if args.project_id else None
    # Match the app's get_qdrant: pass the configured key verbatim (an empty
    # string sends an empty api-key header, which the local Qdrant requires —
    # converting to None drops the header and Qdrant returns 401).
    client = AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)

    summary: dict = {
        "meta": {
            "timestamp": args.timestamp,
            "git_commit": args.git_commit or await _git_commit(),
            "project_id": args.project_id or "ALL",
            "fixture": str(args.fixture),
            "user_groups": ["admin"],
            "mode": args.mode,
            "total_cases": len(cases),
        },
        "config": {
            "default_threshold": settings.default_threshold,
            "rerank_top_n": settings.rerank_top_n,
            "rung2_prefetch_limit": settings.rung2_prefetch_limit,
            "rung2_rerank_top_n": settings.rung2_rerank_top_n,
            "rung2_entity_match_limit": settings.rung2_entity_match_limit,
            "rung2_section_expansion_limit": settings.rung2_section_expansion_limit,
        },
        "notes": args.notes or [],
    }

    if args.mode in ("retrieval", "both"):
        summary["retrieval"] = await run_retrieval(client, cases, project_ids, args.top_k)

    if args.mode in ("ladder", "both"):
        async with async_session_factory() as db:
            summary["ladder"] = await run_ladder(client, db, cases, project_ids)
            summary["slice5"] = {
                "alias_rows": await _alias_row_count(db, args.project_id),
                "alias_match_ms_avg": summary["ladder"].get("alias_match_ms_avg"),
            }
    else:
        async with async_session_factory() as db:
            summary["slice5"] = {
                "alias_rows": await _alias_row_count(db, args.project_id),
                "alias_match_ms_avg": None,
            }

    summary["verdicts"] = compute_verdicts(summary)
    await client.close()

    report = render_report(summary)
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(report, encoding="utf-8")
        print(f"Wrote {args.output_md}")
    else:
        print(report)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print(f"Wrote {args.output_json}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Layered RAG evaluation harness")
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--mode", choices=("retrieval", "ladder", "both"), default="both")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--output-md", type=Path, default=None)
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument(
        "--timestamp", default="unspecified",
        help="Run timestamp for the report header (pass to keep reports deterministic)",
    )
    parser.add_argument("--notes", action="append", help="Note line(s) for the report")
    parser.add_argument(
        "--git-commit", default=None,
        help="Git commit for the report header (pass when git is unavailable in the runtime)",
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
