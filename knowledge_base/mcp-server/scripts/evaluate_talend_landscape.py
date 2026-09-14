#!/usr/bin/env python3
"""Evaluate retained Talend v2 artifacts without invoking a model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from mcp_server.providers.custom.talend_landscape_eval import evaluate_talend_landscape


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("staging_dir", type=Path)
    parser.add_argument("expected", type=Path)
    args = parser.parse_args()
    report = evaluate_talend_landscape(args.staging_dir, args.expected)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
