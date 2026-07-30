"""Reusable fake-only legacy-state invariant helpers for experiment tests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest


@dataclass(frozen=True)
class LegacyStateSnapshot:
    project_row: str
    generate_run_row: str
    published_doc_hashes: tuple[tuple[str, str], ...]
    staging_state: tuple[tuple[str, str], ...]
    rag_hash_rows: tuple[tuple[str, str], ...]


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _tree_hashes(directory: Path) -> tuple[tuple[str, str], ...]:
    if not directory.exists():
        return ()
    return tuple(
        (path.relative_to(directory).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    )


def snapshot_legacy_state(
    *,
    project_row: dict[str, object],
    generate_run_row: dict[str, object],
    published_docs_dir: Path,
    staging_dir: Path,
    rag_hash_rows: list[dict[str, object]],
) -> LegacyStateSnapshot:
    """Capture only legacy state that experiments are forbidden to mutate."""
    return LegacyStateSnapshot(
        project_row=_canonical_json(project_row),
        generate_run_row=_canonical_json(generate_run_row),
        published_doc_hashes=_tree_hashes(published_docs_dir),
        staging_state=_tree_hashes(staging_dir),
        rag_hash_rows=tuple(sorted((str(row["doc_filename"]), str(row["content_hash"])) for row in rag_hash_rows)),
    )


def assert_legacy_state_unchanged(before: LegacyStateSnapshot, after: LegacyStateSnapshot) -> None:
    assert after.project_row == before.project_row, "experiment mutated the project row"
    assert after.generate_run_row == before.generate_run_row, "experiment mutated the baseline GenerateRun row"
    assert after.published_doc_hashes == before.published_doc_hashes, "experiment mutated published docs"
    assert after.staging_state == before.staging_state, "experiment mutated docs_staging"
    assert after.rag_hash_rows == before.rag_hash_rows, "experiment mutated RAG document hashes"


def _legacy_fixture(tmp_path: Path) -> tuple[dict[str, object], dict[str, object], Path, Path, list[dict[str, object]]]:
    output_root = tmp_path / "output" / "alitalend"
    published_docs_dir = output_root / "docs"
    published_docs_dir.mkdir(parents=True)
    (published_docs_dir / "09_EXECUTIVE_SUMMARY.md").write_text("# Published\n", encoding="utf-8")
    staging_dir = output_root / "docs_staging"
    project_row = {"id": "alitalend", "status": "ready", "last_generate_at": "2026-07-13T11:14:12Z", "doc_count": 10}
    generate_run_row = {"id": 44, "project_id": "alitalend", "status": "completed", "cost_usd": None}
    rag_hash_rows = [{"doc_filename": "09_EXECUTIVE_SUMMARY.md", "content_hash": "a" * 64}]
    return project_row, generate_run_row, published_docs_dir, staging_dir, rag_hash_rows


def _add_staging_file(
    state: tuple[dict[str, object], dict[str, object], Path, Path, list[dict[str, object]]],
) -> None:
    state[3].mkdir(parents=True)
    (state[3] / "pending.md").write_text("staging", encoding="utf-8")


def test_experiment_action_keeps_all_legacy_state_immutable(tmp_path: Path) -> None:
    project_row, run_row, docs_dir, staging_dir, rag_hash_rows = _legacy_fixture(tmp_path)
    before = snapshot_legacy_state(
        project_row=project_row,
        generate_run_row=run_row,
        published_docs_dir=docs_dir,
        staging_dir=staging_dir,
        rag_hash_rows=rag_hash_rows,
    )

    experiment_root = tmp_path / "output" / "alitalend" / "experiments" / "exp-1"
    (experiment_root / "candidate").mkdir(parents=True)
    (experiment_root / "candidate" / "09_EXECUTIVE_SUMMARY.md").write_text(
        "# Candidate only\n", encoding="utf-8"
    )

    after = snapshot_legacy_state(
        project_row=project_row,
        generate_run_row=run_row,
        published_docs_dir=docs_dir,
        staging_dir=staging_dir,
        rag_hash_rows=rag_hash_rows,
    )
    assert_legacy_state_unchanged(before, after)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda state: state[0].update(status="generating"),
        lambda state: state[1].update(status="failed"),
        lambda state: (state[2] / "09_EXECUTIVE_SUMMARY.md").write_text("changed", encoding="utf-8"),
        _add_staging_file,
        lambda state: state[4].append({"doc_filename": "new.md", "content_hash": "b" * 64}),
    ],
)
def test_legacy_invariant_helper_detects_every_forbidden_mutation(
    tmp_path: Path,
    mutate: Callable[[tuple[dict[str, object], dict[str, object], Path, Path, list[dict[str, object]]]], object],
) -> None:
    state = _legacy_fixture(tmp_path)
    before = snapshot_legacy_state(
        project_row=state[0],
        generate_run_row=state[1],
        published_docs_dir=state[2],
        staging_dir=state[3],
        rag_hash_rows=state[4],
    )
    mutate(state)
    after = snapshot_legacy_state(
        project_row=state[0],
        generate_run_row=state[1],
        published_docs_dir=state[2],
        staging_dir=state[3],
        rag_hash_rows=state[4],
    )

    with pytest.raises(AssertionError):
        assert_legacy_state_unchanged(before, after)
