"""Tests for the _rag_support/ package builder and analysis_cache restore."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from mcp_server.services.rag_support_package import (
    MANIFEST_SCHEMA_VERSION,
    build_rag_support_package,
    replace_analysis_cache_from_rag_support,
)


def _make_staging(tmp_path: Path) -> Path:
    staging = tmp_path / "docs_staging"
    analysis = staging / "_analysis"
    analysis.mkdir(parents=True)
    (analysis / "A.md").write_text("# Group A analysis", encoding="utf-8")
    (analysis / "B.md").write_text("# Group B analysis", encoding="utf-8")
    (analysis / "C.md").write_text("# Not in manifest", encoding="utf-8")
    (analysis / "_doc_pack_01_ARCHITECTURE_MAP.md").write_text("pack", encoding="utf-8")
    (analysis / "_bundle_core.md").write_text("bundle", encoding="utf-8")
    (analysis / "_coverage_report.json").write_text("{}", encoding="utf-8")
    return staging


def test_build_copies_only_manifest_groups(tmp_path: Path) -> None:
    staging = _make_staging(tmp_path)

    manifest = build_rag_support_package(
        staging, ["A", "B"], project_id="proj", platform="talend"
    )

    package_dir = staging / "_rag_support" / "phase2_analysis"
    assert sorted(p.name for p in package_dir.iterdir()) == ["A.md", "B.md"]
    assert manifest["missing_groups"] == []
    assert (staging / "_rag_support" / "manifest.json").exists()


def test_build_manifest_metadata(tmp_path: Path) -> None:
    staging = _make_staging(tmp_path)

    build_rag_support_package(staging, ["B", "A"], project_id="proj", platform="talend")

    manifest = json.loads(
        (staging / "_rag_support" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert manifest["project_id"] == "proj"
    assert manifest["platform"] == "talend"
    assert [a["group"] for a in manifest["artifacts"]] == ["A", "B"]

    entry = manifest["artifacts"][0]
    content = (staging / "_analysis" / "A.md").read_bytes()
    assert entry["path"] == "phase2_analysis/A.md"
    assert entry["source_path"] == "_analysis/A.md"
    assert entry["source_layer"] == "technical_analysis"
    assert entry["artifact_type"] == "phase2_group_analysis"
    assert entry["visibility"] == "internal"
    assert entry["bytes"] == len(content)
    assert entry["sha256"] == hashlib.sha256(content).hexdigest()


def test_build_records_missing_groups_without_fallback(tmp_path: Path) -> None:
    staging = _make_staging(tmp_path)

    manifest = build_rag_support_package(
        staging, ["A", "GHOST"], project_id="proj", platform="talend"
    )

    assert manifest["missing_groups"] == ["GHOST"]
    package_dir = staging / "_rag_support" / "phase2_analysis"
    assert sorted(p.name for p in package_dir.iterdir()) == ["A.md"]


def test_build_removes_stale_package_files(tmp_path: Path) -> None:
    staging = _make_staging(tmp_path)
    stale_dir = staging / "_rag_support" / "phase2_analysis"
    stale_dir.mkdir(parents=True)
    (stale_dir / "OLD_GROUP.md").write_text("stale", encoding="utf-8")

    build_rag_support_package(staging, ["A"], project_id="proj", platform="talend")

    assert sorted(p.name for p in stale_dir.iterdir()) == ["A.md"]


def test_cache_restore_from_support_package(tmp_path: Path) -> None:
    output_dir = tmp_path / "output" / "proj"
    package_dir = output_dir / "docs" / "_rag_support" / "phase2_analysis"
    package_dir.mkdir(parents=True)
    (package_dir / "A.md").write_text("# Group A analysis", encoding="utf-8")
    (package_dir / "B.md").write_text("# Group B analysis", encoding="utf-8")

    restored = replace_analysis_cache_from_rag_support(output_dir)

    assert restored is True
    cache_dir = output_dir / "analysis_cache"
    assert sorted(p.name for p in cache_dir.iterdir()) == ["A.md", "B.md"]
    assert (cache_dir / "A.md").read_text(encoding="utf-8") == "# Group A analysis"


def test_cache_restore_replaces_stale_cache(tmp_path: Path) -> None:
    output_dir = tmp_path / "output" / "proj"
    cache_dir = output_dir / "analysis_cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "OLD.md").write_text("stale", encoding="utf-8")
    package_dir = output_dir / "docs" / "_rag_support" / "phase2_analysis"
    package_dir.mkdir(parents=True)
    (package_dir / "A.md").write_text("fresh", encoding="utf-8")

    restored = replace_analysis_cache_from_rag_support(output_dir)

    assert restored is True
    assert sorted(p.name for p in cache_dir.iterdir()) == ["A.md"]


def test_cache_restore_removes_cache_when_no_package(tmp_path: Path) -> None:
    output_dir = tmp_path / "output" / "proj"
    cache_dir = output_dir / "analysis_cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "OLD.md").write_text("stale", encoding="utf-8")
    (output_dir / "docs").mkdir()

    restored = replace_analysis_cache_from_rag_support(output_dir)

    assert restored is False
    assert not cache_dir.exists()


def test_cache_restore_treats_empty_package_as_absent(tmp_path: Path) -> None:
    output_dir = tmp_path / "output" / "proj"
    cache_dir = output_dir / "analysis_cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "OLD.md").write_text("stale", encoding="utf-8")
    package_dir = output_dir / "docs" / "_rag_support" / "phase2_analysis"
    package_dir.mkdir(parents=True)

    restored = replace_analysis_cache_from_rag_support(output_dir)

    assert restored is False
    assert not cache_dir.exists(), (
        "an empty package must not leave an empty cache dir — "
        "incremental regen must fall back to full regeneration"
    )
