"""Collect and evaluate Slice 8 canary evidence."""

from __future__ import annotations

import argparse
import asyncio
import datetime
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from src.db import async_session_factory

from .collector import collect_canary_window
from .gate import evaluate_canary_window
from .models import CanaryEvidenceInputs, CanaryWindowEvidence


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _timestamp(value: str) -> datetime.datetime:
    parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed


async def _collect(args) -> int:  # noqa: ANN001
    inputs = CanaryEvidenceInputs.model_validate_json(args.inputs.read_text())
    async with async_session_factory() as db:
        evidence = await collect_canary_window(
            db,
            started_at=args.started_at,
            ended_at=args.ended_at,
            inputs=inputs,
        )
    _atomic_write(args.output, evidence.model_dump_json(indent=2) + "\n")
    print(f"COLLECTED: {len(evidence.samples)} content-free authenticated turn audits")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect")
    collect.add_argument("--inputs", type=Path, required=True)
    collect.add_argument("--started-at", type=_timestamp, required=True)
    collect.add_argument("--ended-at", type=_timestamp, required=True)
    collect.add_argument("--output", type=Path, required=True)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("evidence", type=Path)
    evaluate.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "collect":
        return asyncio.run(_collect(args))
    evidence = CanaryWindowEvidence.model_validate_json(args.evidence.read_text())
    verdict = evaluate_canary_window(evidence)
    _atomic_write(args.output, verdict.model_dump_json(indent=2) + "\n")
    print(f"{verdict.verdict}: {verdict.total_turns} turns; {len(verdict.blockers)} blocker(s)")
    return 0 if verdict.verdict == "GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
