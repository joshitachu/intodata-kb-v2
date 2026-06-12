"""Tests for git_reingestion incremental staging (flat analysis_cache contract)."""

from __future__ import annotations

from pathlib import Path

from mcp_server.config import config
from mcp_server.services.git_reingestion import setup_incremental_staging


def _make_cache(tmp_path: Path, project_id: str) -> Path:
    output_dir = tmp_path / "output" / project_id
    cache = output_dir / "analysis_cache"
    cache.mkdir(parents=True)
    (cache / "A.md").write_text("# Group A analysis", encoding="utf-8")
    (cache / "B.md").write_text("# Group B analysis", encoding="utf-8")
    return output_dir


def test_incremental_staging_restores_flat_cache(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "project_root", tmp_path)
    output_dir = _make_cache(tmp_path, "proj")

    setup_incremental_staging("proj", changed_groups={"B"}, is_first_push=False)

    staging_analysis = output_dir / "docs_staging" / "_analysis"
    assert sorted(p.name for p in staging_analysis.iterdir()) == ["A.md"], (
        "unchanged group analyses must be staged flat; changed groups removed"
    )
    assert (staging_analysis / "A.md").read_text(encoding="utf-8") == "# Group A analysis"


def test_incremental_staging_skips_when_cache_absent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "project_root", tmp_path)
    output_dir = tmp_path / "output" / "proj"
    output_dir.mkdir(parents=True)

    setup_incremental_staging("proj", changed_groups={"B"}, is_first_push=False)

    assert not (output_dir / "docs_staging").exists(), (
        "no cache means no staging — full regeneration fallback"
    )


def test_incremental_staging_skips_on_first_push(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "project_root", tmp_path)
    output_dir = _make_cache(tmp_path, "proj")

    setup_incremental_staging("proj", changed_groups={"B"}, is_first_push=True)

    assert not (output_dir / "docs_staging").exists()
