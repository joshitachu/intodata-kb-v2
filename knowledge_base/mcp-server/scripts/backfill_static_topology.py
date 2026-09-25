"""Publish runtime static topology snapshots for projects scanned before the lane existed.

`runtime_static_topology_snapshots` is the static side of the runtime
correlation engine: without a row, a project's mirrored runtime rows have
nothing to be matched against. Snapshots are only ever written as a side effect
of a completed scan (`api/routes/projects.py` publishes with the scan's own
`run_id` as `source_sequence`), so every project scanned before that call
existed has runtime data and no static side -- which is why the correlation
engine can be entirely unexercised without anyone noticing.

This script republishes from the artifacts already on disk. It does NOT rescan,
does not derive any fact of its own, and cannot loosen the publication gate: it
calls the same `StaticTopologyPublisher` the scanner calls, which fails closed
unless all five artifacts are present and hash-matched by the scan's own
`publication_approval.json` receipt.

    # read-only: compile and report what WOULD be sent
    python scripts/backfill_static_topology.py werkorder pluripharm
    python scripts/backfill_static_topology.py --all-missing

    # actually POST it
    python scripts/backfill_static_topology.py werkorder --publish

Writing requires `--publish` explicitly. Without it nothing leaves the process.

Exit codes: 0 when every project the caller NAMED compiled (or was already
published); 1 when a named project was refused or a publish failed. Projects
found by `--all-missing` that cannot be backfilled are the finding, not a
failure, and do not change the exit code on their own.

Why a backlog existed at all: `static_topology_publisher._approved_artifact_hashes`
used to accept only `publication_approval.json` `schema_version == 1`, while
`intodoc.cli._save_publication_approval` has written version 2 since it added
the `tree` binding. Version 2 is a strict superset of the fields the publisher
reads, so every scan since that bump refused publication for a reason the scan
log reported only as "final scanner approval is missing" -- and because
publication is advisory, the scan still succeeded. That check now accepts
`{1, 2}` and reports an unsupported version explicitly, so a version refusal and
a missing receipt are no longer the same message.

Environment: `MCP_DATABASE_URL` must point at the runtime database and
`MCP_INGEST_API_KEY` must be set for `--publish` (the ingest endpoint is
machine-authenticated). Running inside the `mcp-server` container is the
straightforward way to get both; from a host, note that `localhost:5432` may
resolve to a different Postgres than `127.0.0.1:5432`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text

from mcp_server.config import config
from mcp_server.db.session import async_session_factory
from mcp_server.services.static_topology_publisher import (
    _APPROVED_ARTIFACTS,
    _PUBLICATION_APPROVAL_FILENAME,
    StaticTopologyPublicationError,
    StaticTopologyPublisher,
)

#: Imported from the publisher rather than restated. The publisher is the one
#: definition of what a publishable scan consists of; a second copy here would
#: be free to drift and would then refuse or admit the wrong projects.
REQUIRED_ARTIFACTS: tuple[str, ...] = (_PUBLICATION_APPROVAL_FILENAME, *_APPROVED_ARTIFACTS)

READY = "ready"
ALREADY_PUBLISHED = "already-published"
REFUSED = "refused"
CONFLICT = "conflict"
PUBLISHED = "published"
FAILED = "failed"


@dataclass
class Outcome:
    """One project's verdict, and the payload facts behind it."""

    project_id: str
    status: str
    detail: str = ""
    platform: str | None = None
    source_sequence: int | None = None
    jobs: int | None = None
    dependencies: int | None = None
    lineage: int | None = None
    scan_revision: str | None = None


async def _projects_with_runtime_but_no_snapshot(session) -> list[str]:
    """Projects the correlation engine has runtime rows for and no static side.

    Both run tables are unioned because a provider may mirror one and not the
    other: TMC writes `runtime_job_runs`, and `runtime_executions` is the
    execution-level mirror the log lanes read.
    """
    rows = await session.execute(
        text(
            """
            SELECT DISTINCT r.project_id
            FROM (
                SELECT project_id FROM runtime_job_runs
                UNION
                SELECT project_id FROM runtime_executions
            ) AS r
            WHERE NOT EXISTS (
                SELECT 1 FROM runtime_static_topology_snapshots s
                WHERE s.project_id = r.project_id
            )
            ORDER BY 1
            """
        )
    )
    return [row[0] for row in rows]


async def _latest_completed_scan_run(session, project_id: str) -> int | None:
    """The scan run whose artifacts are on disk, by the publisher's own convention.

    `source_sequence` identifies the scanner OBSERVATION, and the live publisher
    passes the completing scan's `run_id`. Reusing that id keeps a backfilled row
    indistinguishable from one the scanner wrote, and keeps the per-project
    `uq_runtime_static_topology_project_source_sequence` ordering meaningful --
    `latest_snapshot()` orders by `source_sequence DESC`, so inventing a number
    would silently reorder a project's history.
    """
    row = await session.execute(
        text(
            """
            SELECT id FROM scan_runs
            WHERE project_id = :pid AND status = 'completed'
            ORDER BY id DESC LIMIT 1
            """
        ),
        {"pid": project_id},
    )
    return row.scalar_one_or_none()


async def _existing_snapshots(session, project_id: str) -> list[tuple[int, str]]:
    rows = await session.execute(
        text(
            """
            SELECT source_sequence, scan_revision
            FROM runtime_static_topology_snapshots
            WHERE project_id = :pid
            ORDER BY source_sequence DESC
            """
        ),
        {"pid": project_id},
    )
    return [(int(seq), rev) for seq, rev in rows]


def _missing_artifacts(output_dir: Path) -> list[str]:
    return [name for name in REQUIRED_ARTIFACTS if not (output_dir / name).is_file()]


def _receipt_note(output_dir: Path) -> str:
    """Report-only context for a refusal. Decides nothing.

    The publisher's refusals are deliberately terse and identical for several
    different causes ("final scanner approval is missing" covers a corrupt
    receipt, an unapproved one, AND one whose `schema_version` the publisher
    does not accept). Naming the version the receipt actually declares turns an
    unactionable refusal into an actionable one without adding a second gate.
    """
    try:
        receipt = json.loads(
            (output_dir / _PUBLICATION_APPROVAL_FILENAME).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return "receipt unreadable"
    if not isinstance(receipt, dict):
        return "receipt is not a JSON object"
    return (
        f"receipt schema_version={receipt.get('schema_version')!r} "
        f"approved={receipt.get('approved')!r} "
        f"allow_redaction_failures={receipt.get('allow_redaction_failures')!r}"
    )


async def _evaluate(session, project_id: str, output_root: Path) -> Outcome:
    """Compile one project's snapshot and decide whether it may be published.

    Nothing here writes. A refusal names what is missing rather than publishing
    a partial snapshot: a snapshot compiled from a subset of the approved
    artifacts is a static topology that silently disagrees with the scan it
    claims to describe.
    """
    output_dir = output_root / project_id
    if not output_dir.is_dir():
        return Outcome(project_id, REFUSED, f"no scan output at {output_dir}")

    missing = _missing_artifacts(output_dir)
    if missing:
        return Outcome(project_id, REFUSED, "missing approved artifacts: " + ", ".join(missing))

    source_sequence = await _latest_completed_scan_run(session, project_id)
    if source_sequence is None:
        return Outcome(
            project_id,
            REFUSED,
            "no completed scan_runs row, so no scanner observation to name",
        )

    existing = await _existing_snapshots(session, project_id)
    try:
        snapshot = StaticTopologyPublisher().compile_snapshot(
            project_id=project_id,
            output_dir=output_dir,
            source_sequence=source_sequence,
        )
    except StaticTopologyPublicationError as exc:
        return Outcome(
            project_id,
            REFUSED,
            f"{exc} [{_receipt_note(output_dir)}]",
            source_sequence=source_sequence,
        )

    outcome = Outcome(
        project_id,
        READY,
        platform=snapshot["platform"],
        source_sequence=source_sequence,
        jobs=len(snapshot["jobs"]),
        dependencies=len(snapshot["dependencies"]),
        lineage=len(snapshot["lineage"]),
        scan_revision=snapshot["scan_revision"],
    )

    at_sequence = {seq: rev for seq, rev in existing}
    if source_sequence in at_sequence:
        if at_sequence[source_sequence] == snapshot["scan_revision"]:
            outcome.status = ALREADY_PUBLISHED
            outcome.detail = f"sequence {source_sequence} already holds this revision"
        else:
            outcome.status = CONFLICT
            outcome.detail = (
                f"sequence {source_sequence} already holds revision "
                f"{at_sequence[source_sequence]}"
            )
        return outcome
    if any(rev == snapshot["scan_revision"] for _, rev in existing):
        outcome.status = ALREADY_PUBLISHED
        outcome.detail = "this revision is already published under another sequence"
        return outcome
    if existing and existing[0][0] > source_sequence:
        # Reported, not corrected. Raising the number to win the ordering would
        # attach a scan's facts to a run id that never happened.
        outcome.detail = (
            f"NOTE: sequence {source_sequence} is below the project's newest "
            f"snapshot ({existing[0][0]}), so it will not become latest"
        )
    return outcome


async def _publish(outcome: Outcome, output_root: Path) -> Outcome:
    try:
        receipt = await StaticTopologyPublisher().publish(
            project_id=outcome.project_id,
            output_dir=output_root / outcome.project_id,
            source_sequence=outcome.source_sequence,
        )
    # Deliberately broad: one project's transport or contract failure is
    # reported in its own row, never allowed to abort the batch.
    except Exception as exc:  # noqa: BLE001
        outcome.status = FAILED
        outcome.detail = f"{type(exc).__name__}: {exc}"
        return outcome
    outcome.status = PUBLISHED
    outcome.detail = f"received_at={receipt.get('received_at', 'unknown')}"
    return outcome


def _print_table(outcomes: list[Outcome]) -> None:
    headers = (
        "project", "verdict", "platform", "seq", "jobs", "deps", "lineage", "scan_revision",
    )

    def cells(outcome: Outcome) -> tuple[str, ...]:
        return (
            outcome.project_id,
            outcome.status,
            outcome.platform or "-",
            "-" if outcome.source_sequence is None else str(outcome.source_sequence),
            "-" if outcome.jobs is None else str(outcome.jobs),
            "-" if outcome.dependencies is None else str(outcome.dependencies),
            "-" if outcome.lineage is None else str(outcome.lineage),
            outcome.scan_revision or "-",
        )

    rows = [headers, *(cells(outcome) for outcome in outcomes)]
    widths = [max(len(row[i]) for row in rows) for i in range(len(headers))]
    line = "  ".join("-" * width for width in widths)
    print(line)
    print("  ".join(value.ljust(width) for value, width in zip(headers, widths)))
    print(line)
    for row in rows[1:]:
        print("  ".join(value.ljust(width) for value, width in zip(row, widths)))
    print(line)
    for outcome in outcomes:
        if outcome.detail:
            print(f"{outcome.project_id}: {outcome.detail}")


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("projects", nargs="*", help="project ids to backfill")
    parser.add_argument(
        "--all-missing",
        action="store_true",
        help="discover every project that has runtime rows and no snapshot",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="actually POST the snapshots; without this nothing is sent",
    )
    parser.add_argument(
        "--output-root",
        default=str(config.intodoc_output_dir),
        help="scan output root (default: MCP_INTODOC_OUTPUT_DIR)",
    )
    args = parser.parse_args(argv)

    if not args.projects and not args.all_missing:
        parser.error("name at least one project, or pass --all-missing")

    output_root = Path(args.output_root)
    named = list(dict.fromkeys(args.projects))

    async with async_session_factory() as session:
        targets = list(named)
        if args.all_missing:
            discovered = await _projects_with_runtime_but_no_snapshot(session)
            print(f"discovered {len(discovered)} project(s) with runtime data and no snapshot")
            targets += [pid for pid in discovered if pid not in targets]

        outcomes = [await _evaluate(session, pid, output_root) for pid in targets]

    if args.publish:
        if not config.ingest_api_key:
            print(
                "WARNING: MCP_INGEST_API_KEY is empty; the ingest endpoint will "
                "receive an unauthenticated request.",
                file=sys.stderr,
            )
        publishable = [item for item in outcomes if item.status == READY]
        if not publishable:
            print("nothing is publishable; no request sent", file=sys.stderr)
        for outcome in publishable:
            await _publish(outcome, output_root)
    else:
        print("DRY RUN -- nothing was sent. Re-run with --publish to write.")

    _print_table(outcomes)

    by_project = {outcome.project_id: outcome for outcome in outcomes}
    named_failures = [
        pid for pid in named if by_project[pid].status in {REFUSED, CONFLICT, FAILED}
    ]
    publish_failures = [item for item in outcomes if item.status == FAILED]
    return 1 if named_failures or publish_failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
