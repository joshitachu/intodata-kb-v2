#!/usr/bin/env python3
"""Score a generated business topology against a curated baseline.

Usage:
    python scripts/topology_parity.py --candidate path/to/business_topology.json \
        --baseline path/to/business_topology.curated.json [--json] [--baseline-fields-only]

Prints an ASCII table (or the report as JSON). No model call, no writes. The
baseline is typically a confidential client artifact; the output contains field
names and counts only, never claim content.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mcp_server.services.business_topology.parity import (
    render_parity_table,
    score_topology_parity,
)


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"{path}: expected a JSON object, got {type(payload).__name__}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument(
        "--baseline-fields-only",
        action="store_true",
        help="table only the fields the baseline fills at least once",
    )
    args = parser.parse_args(argv)

    report = score_topology_parity(_load(args.candidate), _load(args.baseline))
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(
            render_parity_table(
                report,
                candidate_label=str(args.candidate),
                only_baseline_filled=args.baseline_fields_only,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
