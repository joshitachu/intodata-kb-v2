#!/usr/bin/env python3
"""Render 24 markdown documents from pass_results + scan_result.

Reads:
  - intodoc/docs/scan_result.json (full scan)
  - intodoc/docs/pass_results/p{NN}_{chunk_id}.json (630 files)

Writes:
  - intodoc/docs/*.md (24 markdown documents)

Usage:
    python intodoc/scripts/render_docs.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Add the intodoc source to Python path
INTODOC_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(INTODOC_SRC))

from intodoc.generator.renderer import DocRenderer
from intodoc.models import PassResult, ScanResult

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
PASS_RESULTS_DIR = DOCS_DIR / "pass_results"
SCAN_RESULT_FILE = DOCS_DIR / "scan_result.json"


def load_scan_result() -> ScanResult:
    print(f"Loading scan result from {SCAN_RESULT_FILE}...")
    t0 = time.monotonic()
    result = ScanResult.model_validate_json(
        SCAN_RESULT_FILE.read_text(encoding="utf-8")
    )
    elapsed = time.monotonic() - t0
    print(f"  Loaded in {elapsed:.1f}s — {len(result.jobs)} jobs")
    return result


def load_pass_results() -> dict[int, dict[str, PassResult]]:
    """Load all pass_result JSON files into {pass_number: {chunk_id: PassResult}}."""
    print(f"Loading pass results from {PASS_RESULTS_DIR}...")
    t0 = time.monotonic()

    results: dict[int, dict[str, PassResult]] = {}
    files = sorted(PASS_RESULTS_DIR.glob("p*.json"))

    for f in files:
        raw = json.loads(f.read_text(encoding="utf-8"))
        pr = PassResult(**raw)
        if pr.pass_number not in results:
            results[pr.pass_number] = {}
        results[pr.pass_number][pr.chunk_id] = pr

    elapsed = time.monotonic() - t0
    total = sum(len(v) for v in results.values())
    print(f"  Loaded {total} pass results across {len(results)} passes in {elapsed:.1f}s")
    return results


def build_template_context(
    scan_result: ScanResult,
    pass_results: dict[int, dict[str, PassResult]],
) -> dict:
    """Build the Jinja2 template context from scan result + pass results.

    This mirrors _build_template_context in cli.py but works with loaded data.
    """
    ctx: dict = {
        "scan_result": scan_result,
    }

    pass_context_map = {
        1: "inventories",
        2: "execution_logic",
        3: "data_flows",
        4: "environment_configs",
        5: "external_systems",
        6: "error_handling",
        7: "state_effects",
        8: "custom_code",
        9: "business_logic",
        10: "synthesis",
        11: "schema_dictionary",
        12: "data_quality",
        13: "naming_conventions",
        14: "scheduling_sla",
        15: "security_access",
        16: "performance_profile",
        17: "cross_region",
        18: "reusability",
    }

    for pass_num, context_key in pass_context_map.items():
        if pass_num in pass_results:
            ctx[context_key] = {
                chunk_id: result.data
                for chunk_id, result in pass_results[pass_num].items()
            }
        else:
            ctx[context_key] = {}

    return ctx


def main() -> None:
    scan_result = load_scan_result()
    pass_results = load_pass_results()

    context = build_template_context(scan_result, pass_results)

    print(f"\nRendering documents to {DOCS_DIR}...")
    renderer = DocRenderer(DOCS_DIR)
    t0 = time.monotonic()
    generated = renderer.render_all(context)
    elapsed = time.monotonic() - t0

    print(f"\nGenerated {len(generated)} documents in {elapsed:.1f}s:")
    for p in generated:
        print(f"  {p.name}")


if __name__ == "__main__":
    main()
