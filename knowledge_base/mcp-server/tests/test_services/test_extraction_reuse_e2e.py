"""End-to-end proof that a second pack reuses the first pack's extraction.

`test_extraction_cache.py` unit-tests the cache primitives. What it does not
prove is that the two INDEPENDENT call sites agree:

* the STORE side lives in Phase 4 cleanup
  (`pipeline/phases/phase4_cleanup.py::_store_extraction_cache`) and derives its
  key from `DocGenContext`
* the RESTORE side lives in `services/claude_service.py::generate_docs` and
  derives its key from the generate_docs arguments

Those are two separately-written key computations over the same three inputs
(scan dir, platform, partition unit allowlist). If they ever disagree by so
much as a lower-cased platform string, every run is a silent cache MISS: the
feature still "works", it is just permanently paying full extraction cost, and
no unit test would notice.

The headline property: the key must NOT depend on the pack, so swapping the
lens on a project is cheap.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mcp_server.config import config
from mcp_server.providers.base import DocGenContext
from mcp_server.providers.custom.pipeline.phases.phase4_cleanup import Phase4Cleanup
from mcp_server.services.extraction_cache import (
    compute_extraction_key,
    extraction_cache_dir,
    restore_extraction,
)

PROJECT_ID = "reuse-e2e"
GROUPS = ["Flows", "Logging"]


def _write_scan(scan_dir: Path) -> None:
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / "scan_result.json").write_text(
        json.dumps(
            {
                "platform": "talend",
                "jobs": [
                    {
                        "name": "A_Main",
                        "job_id": "A_Main-h1",
                        "group": "Flows",
                        "path": "process/A_Main.item",
                        "components": [],
                    }
                ],
                "files": [],
                "manifest": {},
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )


def _write_preprocess(staging_dir: Path) -> None:
    preprocess = staging_dir / "_preprocess"
    preprocess.mkdir(parents=True, exist_ok=True)
    for group in GROUPS:
        (preprocess / f"{group}.md").write_text(f"# {group}\n\nevidence\n", encoding="utf-8")
    # Private bundles round-trip but are not groups.
    (preprocess / "_analysis_guide.md").write_text("guide", encoding="utf-8")


def _ctx(staging_dir: Path) -> DocGenContext:
    """A DocGenContext carrying only what the store path actually reads."""
    return DocGenContext(
        project_id=PROJECT_ID,
        staging_dir=staging_dir,
        staging_abs=str(staging_dir),
        logs_abs=str(staging_dir / "logs"),
        preprocess_groups=list(GROUPS),
        total_jobs=1,
        total_groups=len(GROUPS),
        effective_model="test-model",
        write_model="test-model",
        platform="talend",
        analysis_guide_file="",
    )


@pytest.fixture
def scan_and_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Wire config.intodoc_output_dir to a temp scan and return (scan, output)."""
    intodoc_root = tmp_path / "intodoc-output"
    scan_dir = intodoc_root / PROJECT_ID
    _write_scan(scan_dir)
    monkeypatch.setattr(config, "intodoc_output_dir", intodoc_root)
    monkeypatch.setattr(config, "extraction_cache_enabled", True)
    output_dir = tmp_path / "output" / PROJECT_ID
    output_dir.mkdir(parents=True, exist_ok=True)
    return scan_dir, output_dir


def _restore_side_key(scan_dir: Path) -> str:
    """Recompute the key exactly as ClaudeService.generate_docs does.

    Mirrors `claude_service.py::generate_docs`: the scan output dir, the DB
    platform, and the partition unit allowlist -- and nothing about the pack.
    """
    return compute_extraction_key(scan_dir, platform="talend", unit_allowlist=None)


def test_phase4_store_is_restorable_by_the_generate_docs_key(
    scan_and_output: tuple[Path, Path],
) -> None:
    """Run 1 stores via Phase 4; run 2 restores via the generate_docs key."""
    scan_dir, output_dir = scan_and_output

    # --- run 1: a pack produces an extraction layer, Phase 4 caches it -------
    staging_one = output_dir / "docs_staging"
    _write_preprocess(staging_one)
    Phase4Cleanup(MagicMock())._store_extraction_cache(_ctx(staging_one))

    cache_root = extraction_cache_dir(output_dir)
    assert cache_root.exists(), "Phase 4 stored nothing into the extraction cache"

    # --- run 2: a DIFFERENT pack, fresh staging, restore side ----------------
    staging_two = output_dir / "docs_staging_run2"
    staging_two.mkdir(parents=True, exist_ok=True)
    restored = restore_extraction(cache_root, _restore_side_key(scan_dir), staging_two)

    assert restored is not None, (
        "cache MISS: the Phase 4 store key and the generate_docs restore key "
        "disagree, so every run silently pays full extraction cost"
    )
    assert sorted(restored) == sorted(GROUPS)
    for group in GROUPS:
        assert (staging_two / "_preprocess" / f"{group}.md").read_text(
            encoding="utf-8"
        ) == f"# {group}\n\nevidence\n"


def test_extraction_key_is_independent_of_the_pack(
    scan_and_output: tuple[Path, Path],
) -> None:
    """The same scan yields one key regardless of which pack is active.

    This is the property that makes swapping a lens cheap. It holds because the
    key has no pack input at all -- asserted here so that adding one later
    fails loudly rather than quietly halving cache hit rate.
    """
    scan_dir, _ = scan_and_output
    assert _restore_side_key(scan_dir) == _restore_side_key(scan_dir)
    assert compute_extraction_key(
        scan_dir, platform="talend", unit_allowlist=None
    ) == _restore_side_key(scan_dir)


def test_scan_change_invalidates_the_shared_entry(
    scan_and_output: tuple[Path, Path],
) -> None:
    """A rescan must MISS, so a stale extraction can never be restored."""
    scan_dir, output_dir = scan_and_output

    staging_one = output_dir / "docs_staging"
    _write_preprocess(staging_one)
    Phase4Cleanup(MagicMock())._store_extraction_cache(_ctx(staging_one))
    cache_root = extraction_cache_dir(output_dir)
    before = _restore_side_key(scan_dir)

    payload = json.loads((scan_dir / "scan_result.json").read_text(encoding="utf-8"))
    payload["jobs"].append(
        {
            "name": "B_New",
            "job_id": "B_New-h2",
            "group": "Flows",
            "path": "process/B_New.item",
            "components": [],
        }
    )
    (scan_dir / "scan_result.json").write_text(json.dumps(payload), encoding="utf-8")

    after = _restore_side_key(scan_dir)
    assert after != before
    staging_two = output_dir / "docs_staging_run2"
    staging_two.mkdir(parents=True, exist_ok=True)
    assert restore_extraction(cache_root, after, staging_two) is None


def test_store_is_skipped_when_the_cache_is_disabled(
    scan_and_output: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The kill switch must actually stop the write, not just the read."""
    _, output_dir = scan_and_output
    monkeypatch.setattr(config, "extraction_cache_enabled", False)

    staging_one = output_dir / "docs_staging"
    _write_preprocess(staging_one)
    Phase4Cleanup(MagicMock())._store_extraction_cache(_ctx(staging_one))

    cache_root = extraction_cache_dir(output_dir)
    assert not cache_root.exists() or not any(cache_root.iterdir())
