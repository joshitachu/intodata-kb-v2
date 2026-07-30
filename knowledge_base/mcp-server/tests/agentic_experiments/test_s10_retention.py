"""Recoverable retention must never reach legacy generation directories."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from uuid import uuid4

import pytest

import mcp_server.experiments.infrastructure.storage.experiment_retention as retention_module
from mcp_server.experiments.infrastructure.storage.experiment_retention import (
    ExperimentRetentionError,
    ExperimentRetention,
    RetentionAcknowledgementRequired,
    RetentionCandidate,
    RetentionEligibilityError,
    UnsafeRetentionPathError,
)


def _at(year: int) -> datetime.datetime:
    return datetime.datetime(year, 1, 1, tzinfo=datetime.timezone.utc)


def _candidate(*, project_id: str, experiment_id: str, **changes) -> RetentionCandidate:
    values = {
        "project_id": project_id,
        "experiment_id": experiment_id,
        "status": "completed",
        "completed_at": _at(2020),
        "has_live_lease": False,
    }
    values.update(changes)
    return RetentionCandidate(**values)


def _experiment_root(output_root: Path, project_id: str, experiment_id: str) -> Path:
    root = output_root / project_id / "experiments" / experiment_id
    root.mkdir(parents=True)
    (root / "candidate.md").write_text("candidate documentation", encoding="utf-8")
    return root


def test_quarantine_and_restore_are_atomic_scoped_and_recoverable(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir()
    project_id = "logistics-demo"
    experiment_id = str(uuid4())
    experiment = _experiment_root(output_root, project_id, experiment_id)
    legacy = output_root / project_id / "docs" / "legacy.md"
    legacy.parent.mkdir()
    legacy.write_text("must never move", encoding="utf-8")
    retention = ExperimentRetention(output_root=output_root)

    receipt = retention.quarantine(
        candidate=_candidate(project_id=project_id, experiment_id=experiment_id),
        cutoff=_at(2021),
        acknowledged=True,
        now=_at(2022),
    )

    trash = output_root / project_id / ".agentic-experiment-trash" / experiment_id
    assert not experiment.exists()
    assert (trash / "candidate.md").read_text(encoding="utf-8") == "candidate documentation"
    assert legacy.read_text(encoding="utf-8") == "must never move"
    receipt_path = trash / ".agentic-retention-receipt.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert set(payload) == {
        "schema_version",
        "project_id",
        "experiment_id",
        "status",
        "completed_at",
        "cutoff",
        "quarantined_at",
        "source_relative_path",
        "trash_relative_path",
    }
    assert str(output_root) not in receipt_path.read_text(encoding="utf-8")
    assert receipt_path.stat().st_mode & 0o222 == 0
    assert receipt.receipt_sha256

    restored = retention.restore(
        project_id=project_id,
        experiment_id=experiment_id,
        acknowledged=True,
        now=_at(2023),
    )
    assert restored.retention.receipt_sha256 == receipt.receipt_sha256
    assert (experiment / "candidate.md").exists()
    assert not trash.exists()
    assert legacy.exists()


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"status": "queued"}, "terminal"),
        ({"status": "running"}, "terminal"),
        ({"status": "validating"}, "terminal"),
        ({"has_live_lease": True}, "live lease"),
        ({"completed_at": _at(2021)}, "not older"),
    ],
)
def test_quarantine_rejects_non_terminal_live_or_not_old_experiments(
    tmp_path: Path,
    changes: dict[str, object],
    error: str,
) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir()
    experiment_id = str(uuid4())
    _experiment_root(output_root, "project", experiment_id)
    with pytest.raises(RetentionEligibilityError, match=error):
        ExperimentRetention(output_root=output_root).quarantine(
            candidate=_candidate(project_id="project", experiment_id=experiment_id, **changes),
            cutoff=_at(2021),
            acknowledged=True,
        )


def test_quarantine_and_restore_require_explicit_acknowledgement(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir()
    experiment_id = str(uuid4())
    experiment = _experiment_root(output_root, "project", experiment_id)
    retention = ExperimentRetention(output_root=output_root)
    with pytest.raises(RetentionAcknowledgementRequired):
        retention.quarantine(
            candidate=_candidate(project_id="project", experiment_id=experiment_id),
            cutoff=_at(2021),
            acknowledged=False,
        )
    assert experiment.exists()
    retention.quarantine(
        candidate=_candidate(project_id="project", experiment_id=experiment_id),
        cutoff=_at(2021),
        acknowledged=True,
    )
    with pytest.raises(RetentionAcknowledgementRequired):
        retention.restore(project_id="project", experiment_id=experiment_id, acknowledged=False)


def test_retention_rejects_traversal_and_symlinked_experiment_tree(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir()
    retention = ExperimentRetention(output_root=output_root)
    with pytest.raises(ValueError, match="path segment"):
        RetentionCandidate(
            project_id="../project",
            experiment_id=str(uuid4()),
            status="completed",
            completed_at=_at(2020),
            has_live_lease=False,
        )

    project_id = "project"
    experiment_id = str(uuid4())
    root = _experiment_root(output_root, project_id, experiment_id)
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    (root / "escape").symlink_to(target)
    with pytest.raises(UnsafeRetentionPathError, match="symlinks"):
        retention.quarantine(
            candidate=_candidate(project_id=project_id, experiment_id=experiment_id),
            cutoff=_at(2021),
            acknowledged=True,
        )
    assert root.exists()
    assert target.exists()


def test_quarantine_and_restore_resume_after_atomic_move_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir()
    project_id = "project"
    experiment_id = str(uuid4())
    root = _experiment_root(output_root, project_id, experiment_id)
    retention = ExperimentRetention(output_root=output_root)
    candidate = _candidate(project_id=project_id, experiment_id=experiment_id)
    original_replace = retention_module.os.replace
    calls = 0

    def fail_once(source, destination):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected rename failure")
        return original_replace(source, destination)

    monkeypatch.setattr(retention_module.os, "replace", fail_once)
    with pytest.raises(ExperimentRetentionError, match="quarantine"):
        retention.quarantine(candidate=candidate, cutoff=_at(2021), acknowledged=True)
    # The first receipt is preserved, not rewritten, and the next acknowledged
    # request resumes the exact atomic move.
    assert (root / ".agentic-retention-receipt.json").exists()
    first = retention.quarantine(candidate=candidate, cutoff=_at(2021), acknowledged=True)
    second = retention.quarantine(candidate=candidate, cutoff=_at(2021), acknowledged=True)
    assert second.receipt_sha256 == first.receipt_sha256

    restore_calls = 0

    def fail_restore_once(source, destination):
        nonlocal restore_calls
        restore_calls += 1
        if restore_calls == 1:
            raise OSError("injected restore failure")
        return original_replace(source, destination)

    monkeypatch.setattr(retention_module.os, "replace", fail_restore_once)
    with pytest.raises(ExperimentRetentionError, match="restore"):
        retention.restore(project_id=project_id, experiment_id=experiment_id, acknowledged=True)
    trash = output_root / project_id / ".agentic-experiment-trash" / experiment_id
    assert trash.exists()
    restored = retention.restore(project_id=project_id, experiment_id=experiment_id, acknowledged=True)
    assert restored.retention.receipt_sha256 == first.receipt_sha256
    assert (root / "candidate.md").exists()
