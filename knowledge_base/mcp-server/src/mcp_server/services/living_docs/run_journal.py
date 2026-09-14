"""Durable per-run receipts and a derived, human-readable update journal."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from mcp_server.providers.custom.noncode_evidence import redact_text


def atomic_json(path: Path, value: dict) -> None:
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class RunJournal:
    def __init__(self, output: Path):
        self.root = output / "_version_history" / "runs"

    def start(self, before: str, after: str, trigger: str) -> str:
        run_id = uuid4().hex
        atomic_json(
            self.root / f"{run_id}.json",
            {
                "id": run_id,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "before": before,
                "after": after,
                "trigger": trigger,
                "state": "queued",
            },
        )
        return run_id

    def activate(self, run_id: str) -> None:
        # Called only while owning the OS project lock. Other queued callers
        # are left alone; a previous running owner cannot still be executing.
        for path in self.root.glob("*.json"):
            entry = json.loads(path.read_text())
            if entry["id"] == run_id:
                entry["state"] = "running"
                atomic_json(path, entry)
            elif entry["state"] == "running":
                entry.update(
                    state="interrupted",
                    summary={"reason": "Previous worker exited before recording its outcome"},
                )
                atomic_json(path, entry)

    def finish(self, run_id: str, summary: dict) -> None:
        path = self.root / f"{run_id}.json"
        entry = json.loads(path.read_text())
        # The workflow, not the model, supplies the actual publication state.
        entry.update(
            state=str(summary.get("action", "failed")),
            summary=summary,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        entry["before"] = summary.get("before", entry["before"])
        entry["after"] = summary.get("after", entry["after"])

        def redact(value):
            if isinstance(value, str):
                return redact_text(value)[0]
            if isinstance(value, dict):
                return {key: redact(item) for key, item in value.items()}
            if isinstance(value, list):
                return [redact(item) for item in value]
            return value

        safe = redact(json.loads(json.dumps(entry, default=str)))
        atomic_json(path, safe)
        self.render()

    def render(self) -> None:
        entries = [json.loads(p.read_text()) for p in self.root.glob("*.json")]
        entries.sort(key=lambda e: (e["started_at"], e["id"]))
        lines = [
            "# Documentation update journal\n",
            "Historical outcomes; not current system behavior.\n",
        ]
        for entry in entries:
            summary = entry.get("summary", {})
            lines.extend(
                [
                    f"\n## {entry['started_at']} — {entry['state']}\n",
                    f"Commits: `{entry['before']}` → `{entry['after']}`\n",
                    str(
                        summary.get("agent_summary")
                        or summary.get("reason")
                        or "Run started; outcome not yet recorded."
                    )
                    + "\n",
                    "Documents: "
                    + ", ".join(summary.get("patched_docs") or summary.get("touched_docs") or [])
                    + "\n",
                ]
            )
            analysis = summary.get("change_analysis") or {}
            for finding in analysis.get("findings", []):
                lines.extend(
                    [
                        f"\n- Change: {finding['before']} → {finding['after']}",
                        f"  Intent ({finding['intent_basis']}): {finding['intent']}",
                        f"  Decision: {finding['disposition']} — {finding['reason']}",
                    ]
                )
            for target in analysis.get("targets", []):
                lines.append(
                    f"\n- Planned {target['action']}: {target['path']} ({', '.join(target['sections'])}) — {target['reason']}"
                )
            if summary.get("verification"):
                lines.append(
                    "\nVerification: "
                    + summary["verification"]["verdict"]
                    + " — "
                    + summary["verification"]["explanation"]
                )
            if summary.get("candidate_id"):
                lines.append("\nCandidate: `" + str(summary["candidate_id"]) + "`\n")
        atomic_text(self.root.parent / "CHANGELOG.md", "\n".join(lines))


class ProjectRunLock:
    """OS-owned lock: shared across workers, released automatically on crash.

    Separate from the scan lock because source evidence reads no mutable scan
    output. All automatic update entrypoints use this same project lock.
    """

    def __init__(self, output: Path):
        self.path = output.parent / f".{output.name}.reingestion.lock"
        self.stream = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                if stream.read(1) == b"":
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            return False
        self.stream = stream
        return True

    def release(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None
