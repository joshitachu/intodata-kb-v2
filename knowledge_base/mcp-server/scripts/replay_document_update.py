"""Replay a historical change through real OpenCode without publishing.

Run from mcp-server: .venv/bin/python scripts/replay_document_update.py --help
Requires the documentation snapshot that actually corresponds to --before.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import shutil
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mcp_server.services.git_diff_patcher import parse_name_status
from mcp_server.services.git_remote_service import run_git
from mcp_server.services.living_docs.git_evidence import collect_git_evidence
from mcp_server.services.living_docs.opencode_reingestion import OpenCodeReingestionExecutor
from mcp_server.services.living_docs.run_journal import atomic_json


class ReplayLog:
    async def emit(self, project_id, message, level="info"):
        print(f"[{level}] {message}", file=sys.stderr)


async def replay(args) -> int:
    output = Path(args.output).resolve()
    baseline = Path(args.baseline_docs).resolve()
    if not baseline.is_dir() or output.exists():
        raise ValueError("Baseline docs must exist and replay output must be a new directory")
    repo = str(Path(args.repo).resolve())
    changes = parse_name_status(
        await run_git(repo, "diff", "--name-status", "-M", args.before, args.after)
    )
    evidence = await collect_git_evidence(repo, args.before, args.after, changes)
    diff = await run_git(repo, "diff", "--unified=20", args.before, args.after)
    shutil.copytree(baseline, output / "docs")
    atomic_json(output / "_reingestion/evidence.json", evidence)
    result = await OpenCodeReingestionExecutor().run(
        project_id="historical-replay",
        output_dir=output,
        candidate_id="replay-" + uuid4().hex,
        before_sha=args.before,
        after_sha=args.after,
        full_diff_text=diff,
        changed_files=changes,
        changed_groups=[],
        log_mgr=ReplayLog(),
        source_path=repo,
    )
    summary = dict(result.summary)
    summary.update(action=result.action, published=False, error=result.error)
    if args.expected:
        expected = json.loads(Path(args.expected).read_text())
        actual = set(result.patched_docs)
        required = set(expected.get("required_docs", []))
        allowed = set(expected.get("allowed_docs", required))
        summary["evaluation"] = {
            "missing_docs": sorted(required - actual),
            "unexpected_docs": sorted(actual - allowed),
        }
        checks = []
        for path, strings in expected.get("required_text", {}).items():
            content = (
                (output / "docs" / path).read_text() if (output / "docs" / path).is_file() else ""
            )
            checks.extend(f"{path}: missing {value}" for value in strings if value not in content)
        for path, strings in expected.get("forbidden_text", {}).items():
            content = (
                (output / "docs" / path).read_text() if (output / "docs" / path).is_file() else ""
            )
            checks.extend(
                f"{path}: still contains {value}" for value in strings if value in content
            )
        summary["evaluation"]["text_failures"] = checks
    atomic_json(output / "replay_result.json", summary)
    print(json.dumps(summary, indent=2))
    return int(result.action != "true_patch" or any(summary.get("evaluation", {}).values()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repo", "before", "after", "baseline-docs", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument(
        "--expected",
        help="JSON expectations: required_docs, allowed_docs, required_text, forbidden_text",
    )
    raise SystemExit(asyncio.run(replay(parser.parse_args())))
