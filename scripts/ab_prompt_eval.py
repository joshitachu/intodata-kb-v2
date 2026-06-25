#!/usr/bin/env python3
"""A/B evaluation harness for Phase 2 / Phase 3 prompt templates.

Measures the impact of the Slice 4 prompt revisions against the old templates
on a fixed reference model and a fixed scan cache, without touching the full
7-phase pipeline. Operator step -- requires local Ollama running the target model.

Usage:
    python scripts/ab_prompt_eval.py \\
        --scan-cache knowledge_base/intodoc-output/<project> \\
        --model qwen2.5-coder:7b \\
        --ollama-url http://localhost:11434 \\
        --output-dir /tmp/ab_result \\
        [--dry-run]   # render-only, no LLM calls

Output:
    <output-dir>/ab_result.json   -- machine-readable before/after comparison
    stdout                         -- human-readable summary table

Metrics:
    - mean_quality_score        (via doc quality rubric heuristic)
    - empty_analysis_count      (groups whose analysis is below THIN floor)
    - depth_gap_count           (groups with fewer than 2 substantive sections)
    - section_gap_count         (groups missing at least one required h3 section)
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

THIN_FLOOR_BYTES = 500
REQUIRED_PHASE2_SECTIONS = [
    "COMPONENTS",
    "EXECUTION_FLOW",
    "DATA_FLOWS",
    "TRANSFORMATION_DETAIL",
    "BUSINESS_RULES",
    "EXTERNAL_SYSTEMS",
    "ERROR_HANDLING",
    "ENVIRONMENT_CONFIG",
    "RISK_FLAGS",
    "GROUP_SUMMARY",
]


# ---------------------------------------------------------------------------
# Scan-cache loader (minimal -- reads intodoc JSON output)
# ---------------------------------------------------------------------------

def _load_scan_groups(scan_cache_dir: Path) -> dict[str, list[str]]:
    """Return {group: [unit_name, ...]} from a scan-cache directory.

    Reads the top-level `groups.json` (or derives from per-group JSON files).
    Falls back to single-group 'default' if no group structure is found.
    """
    groups_file = scan_cache_dir / "groups.json"
    if groups_file.exists():
        try:
            data = json.loads(groups_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {k: list(v) for k, v in data.items() if isinstance(v, list)}
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("groups.json parse error: %s", exc, exc_info=True)

    # Derive from per-unit JSON files: use their 'group' field if present.
    result: dict[str, list[str]] = {}
    for p in sorted(scan_cache_dir.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            unit = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Skip %s: %s", p, exc, exc_info=True)
            continue
        group = unit.get("group", "default")
        name = unit.get("name", p.stem)
        result.setdefault(group, []).append(name)

    if not result:
        result["default"] = ["(no units found)"]
    return result


# ---------------------------------------------------------------------------
# Prompt rendering (isolated -- uses PromptRenderer directly)
# ---------------------------------------------------------------------------

def _render_phase2_prompt(
    group: str,
    units: list[str],
    platform: str,
    template_root: Path,
) -> str:
    """Render the Phase 2 group-analysis prompt for one group."""
    from string import Template

    tmpl_path = template_root / "phase2_analysis.md"
    raw = tmpl_path.read_text(encoding="utf-8")

    section_names = REQUIRED_PHASE2_SECTIONS[:-1]  # exclude GROUP_SUMMARY (added by template)
    section_headings = "\n".join(f"- `### {s}`" for s in section_names)

    # Platform section depth
    platform_depth_path = template_root / "phase2_platform_sections" / f"{platform.lower()}.md"
    if not platform_depth_path.exists():
        platform_depth_path = template_root / "phase2_platform_sections" / "generic.md"
    platform_depth = platform_depth_path.read_text(encoding="utf-8") if platform_depth_path.exists() else ""

    slots: dict[str, str] = {
        "PREAMBLE": (
            "You are generating Phase 2 group analysis. Your output is the SOLE factual foundation"
            " for every documentation file produced in Phase 3. Be EXHAUSTIVE."
        ),
        "GROUP": group,
        "DOC_COUNT": "10",
        "DOC_LIST": "- (standard doc set)",
        "UNIT_LIST": "\n".join(f"- {u}" for u in units),
        "FACTS_SUMMARY": f"Platform: {platform}\nCanonical unit count: {len(units)}\nGroups: {group}={len(units)}",
        "PLATFORM_ANALYSIS_REQUIREMENTS": f"Platform: {platform}. Follow platform analysis conventions.",
        "SECTION_HEADINGS": section_headings,
        "PLATFORM_SECTION_DEPTH": platform_depth,
        "CANONICAL_UNIT_COUNT": str(len(units)),
        "CANONICAL_UNIT_LABEL": "units",
    }
    return Template(raw).safe_substitute(slots)


# ---------------------------------------------------------------------------
# Ollama client (minimal)
# ---------------------------------------------------------------------------

def _ollama_complete(
    prompt: str,
    model: str,
    ollama_url: str,
    timeout: int = 300,
) -> str:
    """Call Ollama /api/generate and return the response text."""
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(
        f"{ollama_url.rstrip('/')}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
            return body.get("response", "")
    except OSError as exc:
        logger.error("Ollama request failed: %s", exc, exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _measure_analysis(text: str) -> dict[str, Any]:
    """Return metrics for one group-analysis output."""
    byte_len = len(text.encode("utf-8"))
    is_empty = byte_len < THIN_FLOOR_BYTES

    h3_sections = set(re.findall(r"^###\s+(\S+)", text, re.MULTILINE))
    required = set(REQUIRED_PHASE2_SECTIONS)
    missing_sections = sorted(required - h3_sections)

    # Depth: count sections with at least 3 lines of non-heading content.
    substantive = 0
    current_section_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("### "):
            if sum(1 for l in current_section_lines if l.strip()) >= 3:
                substantive += 1
            current_section_lines = []
        else:
            current_section_lines.append(line)
    if sum(1 for l in current_section_lines if l.strip()) >= 3:
        substantive += 1

    has_depth_gap = substantive < max(1, len(h3_sections) // 2)

    # Heuristic quality score: 0-5 scale
    score = 0.0
    if not is_empty:
        score += 1.0
    score += min(2.0, len(h3_sections) / len(required) * 2.0)
    score += min(1.0, substantive / max(1, len(required)) * 1.0)
    if not missing_sections:
        score += 1.0

    # Filler check
    has_filler = bool(
        re.match(r"^\s*(okay|sure|i will|i'll|certainly|of course|great|absolutely)", text, re.IGNORECASE)
    )

    return {
        "byte_len": byte_len,
        "is_empty": is_empty,
        "h3_sections_found": sorted(h3_sections),
        "missing_sections": missing_sections,
        "substantive_section_count": substantive,
        "has_depth_gap": has_depth_gap,
        "has_filler": has_filler,
        "quality_score": round(score, 2),
    }


def _aggregate(per_group: list[dict[str, Any]]) -> dict[str, Any]:
    if not per_group:
        return {}
    n = len(per_group)
    return {
        "group_count": n,
        "mean_quality_score": round(sum(g["quality_score"] for g in per_group) / n, 3),
        "empty_analysis_count": sum(1 for g in per_group if g["is_empty"]),
        "depth_gap_count": sum(1 for g in per_group if g["has_depth_gap"]),
        "section_gap_count": sum(1 for g in per_group if g["missing_sections"]),
        "filler_count": sum(1 for g in per_group if g["has_filler"]),
    }


# ---------------------------------------------------------------------------
# Main harness
# ---------------------------------------------------------------------------

def _run_variant(
    variant_label: str,
    template_root: Path,
    groups: dict[str, list[str]],
    platform: str,
    model: str,
    ollama_url: str,
    dry_run: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Run one variant (old or new templates) across all groups.

    Returns (per_group_metrics, rendered_prompts).
    """
    per_group: list[dict[str, Any]] = []
    prompts: list[str] = []

    for group, units in groups.items():
        prompt = _render_phase2_prompt(group, units, platform, template_root)
        prompts.append(prompt)

        if dry_run:
            # In dry-run mode, measure the prompt itself as a proxy.
            text = f"### COMPONENTS\n(dry-run placeholder for group {group})\n"
        else:
            logger.info("[%s] Calling model for group %s ...", variant_label, group)
            text = _ollama_complete(prompt, model, ollama_url)

        metrics = _measure_analysis(text)
        metrics["group"] = group
        metrics["variant"] = variant_label
        per_group.append(metrics)

    return per_group, prompts


def _print_summary(label: str, agg: dict[str, Any]) -> None:
    print(f"\n  {label}:")
    print(f"    Groups evaluated     : {agg.get('group_count', 0)}")
    print(f"    Mean quality score   : {agg.get('mean_quality_score', 'n/a')}/5.0")
    print(f"    Empty analyses       : {agg.get('empty_analysis_count', 0)}")
    print(f"    Depth-gap groups     : {agg.get('depth_gap_count', 0)}")
    print(f"    Section-gap groups   : {agg.get('section_gap_count', 0)}")
    print(f"    Filler-start groups  : {agg.get('filler_count', 0)}")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="A/B prompt eval for Phase 2 templates.")
    parser.add_argument("--scan-cache", required=True, type=Path, help="Path to intodoc scan-cache directory")
    parser.add_argument("--platform", default="generic", help="Platform name (talend/workato/generic)")
    parser.add_argument("--model", default="qwen2.5-coder:7b", help="Ollama model to use")
    parser.add_argument("--ollama-url", default="http://localhost:11434", help="Ollama base URL")
    parser.add_argument("--output-dir", type=Path, default=Path("ab_result"), help="Output directory")
    parser.add_argument("--dry-run", action="store_true", help="Render prompts only; skip LLM calls")
    args = parser.parse_args(argv)

    scan_cache = args.scan_cache.resolve()
    if not scan_cache.is_dir():
        logger.error("scan-cache directory not found: %s", scan_cache)
        return 1

    groups = _load_scan_groups(scan_cache)
    if not groups:
        logger.error("No groups found in scan cache %s", scan_cache)
        return 1

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Locate NEW template root (this repo's current templates)
    repo_root = Path(__file__).resolve().parent.parent
    new_template_root = repo_root / "knowledge_base" / "mcp-server" / "data" / "prompt_templates"
    if not new_template_root.exists():
        logger.error("Template root not found: %s", new_template_root)
        return 1

    # Build OLD template root: copy current root to a temp dir, restore the pre-Slice4 bodies.
    # The old Phase 2 body is reconstructed from the known pre-Slice4 text (committed in git).
    with tempfile.TemporaryDirectory() as _tmpdir:
        old_template_root = Path(_tmpdir) / "prompt_templates"
        import shutil
        shutil.copytree(new_template_root, old_template_root)

        # Write the OLD phase2_analysis.md (pre-Slice4 version reconstructed from git history)
        old_phase2 = _old_phase2_template()
        (old_template_root / "phase2_analysis.md").write_text(old_phase2, encoding="utf-8")

        old_phase3 = _old_phase3_template()
        (old_template_root / "phase3_doc.md").write_text(old_phase3, encoding="utf-8")

        # Run OLD variant
        old_metrics, old_prompts = _run_variant(
            "OLD", old_template_root, groups, args.platform, args.model, args.ollama_url, args.dry_run
        )

    # Run NEW variant (current templates)
    new_metrics, new_prompts = _run_variant(
        "NEW", new_template_root, groups, args.platform, args.model, args.ollama_url, args.dry_run
    )

    old_agg = _aggregate(old_metrics)
    new_agg = _aggregate(new_metrics)

    result = {
        "dry_run": args.dry_run,
        "model": args.model,
        "scan_cache": str(scan_cache),
        "platform": args.platform,
        "old": old_agg,
        "new": new_agg,
        "delta": {
            k: round(new_agg.get(k, 0) - old_agg.get(k, 0), 3)
            for k in ("mean_quality_score", "empty_analysis_count", "depth_gap_count",
                       "section_gap_count", "filler_count")
        },
        "per_group_old": old_metrics,
        "per_group_new": new_metrics,
    }

    out_file = output_dir / "ab_result.json"
    out_file.write_text(json.dumps(result, indent=2), encoding="utf-8")

    mode = "(DRY RUN -- no LLM calls)" if args.dry_run else f"(model: {args.model})"
    print(f"\n=== Slice 4 A/B Prompt Evaluation {mode} ===")
    _print_summary("OLD templates", old_agg)
    _print_summary("NEW templates", new_agg)

    delta = result["delta"]
    print(f"\n  Delta (NEW - OLD):")
    print(f"    mean_quality_score   : {delta['mean_quality_score']:+.3f}  (positive = improvement)")
    print(f"    empty_analysis_count : {delta['empty_analysis_count']:+d}  (negative = improvement)")
    print(f"    depth_gap_count      : {delta['depth_gap_count']:+d}  (negative = improvement)")
    print(f"    section_gap_count    : {delta['section_gap_count']:+d}  (negative = improvement)")
    print(f"    filler_count         : {delta['filler_count']:+d}  (negative = improvement)")
    print(f"\n  Result written to: {out_file}")

    if args.dry_run:
        print("\n  NOTE: dry-run mode. Re-run without --dry-run against a live Ollama")
        print("        (model: qwen2.5-coder:7b on xps over Tailscale) for real A/B numbers.")

    return 0


# ---------------------------------------------------------------------------
# Pre-Slice4 template bodies (reconstructed from git HEAD~1 of phase2_analysis.md
# and phase3_doc.md for the old-variant baseline)
# ---------------------------------------------------------------------------

def _old_phase2_template() -> str:
    return """\
${PREAMBLE}

Group: ${GROUP}
Current Phase 3 document set (${DOC_COUNT} files):
${DOC_LIST}

Canonical units in this group -- mention EVERY one by exact name:
${UNIT_LIST}

Run facts:
${FACTS_SUMMARY}

Platform evidence contract:
${PLATFORM_ANALYSIS_REQUIREMENTS}

EngineeringFacts context:
- The harness may supply `engineering_facts.*` plus artifact-specific files such as `entity_index`,
  `usage_index`, `dependency_map`, `data_flow_map`, `api_surface`, `config_runtime_map`,
  `test_coverage_map`, and `known_risks`.
- Treat these artifacts as normalized scanner evidence. Cite them where relevant and do not invent
  relationships that are absent from both EngineeringFacts and the supplied scan/preprocess data.

Direct source context:
- If `_source_coverage.json` or redacted source bundles are supplied, treat those file contents as
  the evidence of record.
- Do not say a covered file is missing, unavailable, not scanned, or has no content.
- Values marked `[REDACTED]` are intentionally hidden secrets, not missing evidence.

MANDATORY SECTION STRUCTURE
Write every section below in this exact order. Each section must be substantive.
${SECTION_HEADINGS}


${PLATFORM_SECTION_DEPTH}

### GROUP_SUMMARY
Finish with a concise index for Phase 3. Include:
- Units covered and scanner-limited units
- Top flows and entrypoints
- External systems and runtime/config facts
- Business rules and transformations with evidence pointers
- Top risks, unknowns, and redacted vs missing vs scanner-limited distinctions
- Deep evidence pointers to the sections above

The supplied prompt pack contains the full platform analysis guide.
It is the authoritative reference for platform-specific patterns, component behavior, and additional depth requirements.
Follow all extraction rules, examples, and depth requirements defined there in addition to the instructions above.
Your output is the analysis body only -- the Python harness writes the coverage block and output file.

NARRATIVE RULES (strictly enforced):
- Never summarize when you can enumerate
- Every claim requires an exact unit name + component reference
- Show ACTUAL code / expressions / SQL verbatim, then explain -- never paraphrase without showing the raw value
- "Error handling is weak" is UNACCEPTABLE
- "7 of 9 ${GROUP} jobs have no tLogCatcher" IS acceptable
- Include concrete counts, percentages, exact names throughout
- Expressions must be shown in full -- never truncate even if 500+ chars
- SQL queries must be shown in full -- never truncate
- Custom code blocks must be shown in full
- Hardcoded values must be cataloged in a markdown table with location and risk

FACTUAL CONSTRAINTS:
- Use only the supplied scanner facts and preprocessed scan data
- Do not invent jobs, counts, systems, schedules, or risks not present in the data
- The exact canonical inventory for this project is ${CANONICAL_UNIT_COUNT} ${CANONICAL_UNIT_LABEL}
- Never use a conflicting unit count anywhere in your output
- If detail is absent for a specific unit, name the unit and state exactly what is missing
"""


def _old_phase3_template() -> str:
    return """\
${PREAMBLE}

FACTUAL CONSTRAINTS (enforced):
- Platform: ${PLATFORM}
- Canonical inventory: exactly ${CANONICAL_UNIT_COUNT} ${CANONICAL_UNIT_LABEL} -- never use a conflicting count
- Cover or mention all canonical units where relevant to this document
- If detail is absent for a unit, write "scanner detail is limited for this unit" -- never invent
- Synthesize from the supplied validated analysis; do not re-extract from raw scanner data

NARRATIVE RULES (enforced):
- Never summarize when you can enumerate
- Every claim needs an exact unit name + component reference
- Show actual code / expressions / SQL verbatim, then explain
- Include concrete counts, percentages, exact names
- Business rules: BR-{GROUP}-NNN tags with full expressions
- Risk items: CRITICAL / HIGH / MEDIUM / LOW with specific unit references
- Schema docs: column-level detail (name, type, nullable)
- Data flow docs: full tMap expressions with explanations
- "Error handling is weak" is UNACCEPTABLE -- cite specific job names and counts

FORMAT:
- Start with a table of contents
- Use plain ASCII tables (no Unicode box-drawing characters)
- Use markdown headings, bullet lists, and fenced code blocks for expressions/SQL/code
- Reference scorecard dimension scores where relevant (e.g. "Security: 2/5 -- see Assessment Scorecard")
- Mark evidence confidence with a leading HIGH / MEDIUM / LOW token where claims are inferred or scanner-limited
- Use exact source artifact/path names where available
- Use the preloaded `_analysis/_doc_pack_${DOC_NAME}.md` evidence pack as the primary evidence source.
- Include evidence pointers from the pack when a section depends on Phase 2 findings.
- Include a `## Evidence Register` table with these exact columns:
  `| Topic | Units / Scope | Evidence Source | Source Artifact / Path | Confidence |`
- The Evidence Register must cover the major topics in this document with concrete supplied evidence,
  exact artifact/path names where available, and a confidence cell that starts with `HIGH`, `MEDIUM`,
  or `LOW`.
- Write that leading confidence token as plain unformatted text. Do not bold, italicize, quote, or
  wrap it in backticks: use `LOW (scanner-limited)`, not `**LOW**`, `_LOW_`, or `` `LOW` ``.
- Explanatory scanner-limited, inferred, or redaction notes may follow that confidence token or appear
  in evidence/topic cells. Mark those topics as such instead of guessing.

MANDATORY HEADINGS for ${DOC_NAME} -- your response MUST contain each of these as a `##` markdown
heading, using exactly this wording (do not rename, merge, or omit any of them):
${HEADINGS_BLOCK}
  ## Evidence Register

${DOC_SPECIFIC}
Platform-specific evidence obligations:
${PLATFORM_DOCUMENTATION_OBLIGATIONS}
Platform documentation guidance:
${PLATFORM_ANALYSIS_REQUIREMENTS}

Facts:
${FACTS_SUMMARY}
${EXAMPLES_BLOCK}
"""


if __name__ == "__main__":
    sys.exit(main())
