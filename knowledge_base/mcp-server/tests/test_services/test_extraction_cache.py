"""Tests for the universal (pack-independent) extraction cache.

The headline property under test: the extraction layer is keyed on the SCAN,
not on the pack, so a second run with a different pack contract reuses
``_preprocess/`` instead of re-running the deterministic extraction.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from mcp_server.services.claude_service import DocGenerationCoordinator
from mcp_server.services.extraction_cache import (
    EXTRACTION_CODE_VERSION,
    compute_extraction_key,
    extraction_cache_dir,
    prune_extraction_cache,
    restore_extraction,
    store_extraction,
    unit_allowlist_for_partition,
)


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def claude_svc(log_manager):
    return DocGenerationCoordinator(log_manager=log_manager)


def _write_scan(scan_dir: Path, *, jobs: list[dict] | None = None) -> None:
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / "scan_result.json").write_text(
        json.dumps(
            {
                "platform": "talend",
                "jobs": jobs
                if jobs is not None
                else [
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


def _write_preprocess(staging_dir: Path) -> Path:
    preprocess = staging_dir / "_preprocess"
    preprocess.mkdir(parents=True, exist_ok=True)
    (preprocess / "Flows.md").write_text("# Flows\n\nA_Main-h1\n", encoding="utf-8")
    (preprocess / "Logging.md").write_text("# Logging\n\nLogInfo\n", encoding="utf-8")
    # Private bundles must round-trip too, but never count as groups.
    (preprocess / "_analysis_guide.md").write_text("guide", encoding="utf-8")
    (preprocess / "_doc_templates.md").write_text("templates", encoding="utf-8")
    return preprocess


# --------------------------------------------------------------------------- #
# Key composition
# --------------------------------------------------------------------------- #


def test_key_is_stable_for_the_same_scan(tmp_path: Path) -> None:
    scan_dir = tmp_path / "scan_cache"
    _write_scan(scan_dir)

    first = compute_extraction_key(scan_dir, platform="talend", unit_allowlist=None)
    second = compute_extraction_key(scan_dir, platform="talend", unit_allowlist=None)

    assert first
    assert first == second


def test_key_changes_when_a_scan_artifact_changes(tmp_path: Path) -> None:
    scan_dir = tmp_path / "scan_cache"
    _write_scan(scan_dir)
    before = compute_extraction_key(scan_dir, platform="talend", unit_allowlist=None)

    _write_scan(
        scan_dir,
        jobs=[
            {
                "name": "A_Main",
                "job_id": "A_Main-h1",
                "group": "Flows",
                "path": "process/A_Main.item",
                "components": [{"name": "tDBInput_1", "type": "tDBInput"}],
            }
        ],
    )
    after = compute_extraction_key(scan_dir, platform="talend", unit_allowlist=None)

    assert after
    assert after != before, (
        "a changed scan must produce a different key so the stale-scan "
        "invalidation path cannot be defeated by a cache hit"
    )


def test_key_changes_with_platform_allowlist_and_code_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan_dir = tmp_path / "scan_cache"
    _write_scan(scan_dir)
    base = compute_extraction_key(scan_dir, platform="talend", unit_allowlist=None)

    assert compute_extraction_key(
        scan_dir, platform="generic", unit_allowlist=None
    ) != base
    assert compute_extraction_key(
        scan_dir, platform="talend", unit_allowlist={"A_Main-h1"}
    ) != base
    # An empty allowlist is not the same thing as "no scoping".
    assert compute_extraction_key(
        scan_dir, platform="talend", unit_allowlist=set()
    ) != base
    # Set ordering must not matter.
    assert compute_extraction_key(
        scan_dir, platform="talend", unit_allowlist={"b", "a"}
    ) == compute_extraction_key(
        scan_dir, platform="talend", unit_allowlist={"a", "b"}
    )

    monkeypatch.setattr(
        "mcp_server.services.extraction_cache.EXTRACTION_CODE_VERSION", "999",
    )
    assert compute_extraction_key(
        scan_dir, platform="talend", unit_allowlist=None
    ) != base


def test_key_is_empty_when_no_scan_exists(tmp_path: Path) -> None:
    assert compute_extraction_key(
        tmp_path / "missing", platform="talend", unit_allowlist=None
    ) == ""


def test_key_is_empty_when_scan_is_unparseable(tmp_path: Path) -> None:
    scan_dir = tmp_path / "scan_cache"
    scan_dir.mkdir()
    (scan_dir / "scan_result.json").write_text("{not json", encoding="utf-8")

    # No parseable artifact -> compute_scan_fingerprint raises FileNotFoundError,
    # which must surface as "do not cache", not as an exception.
    assert compute_extraction_key(
        scan_dir, platform="talend", unit_allowlist=None
    ) == ""


def test_unit_allowlist_for_partition_mirrors_generate_docs() -> None:
    from mcp_server.providers.custom.partition_planner import WHOLE_PROJECT_PARTITION

    assert unit_allowlist_for_partition(None) is None
    assert unit_allowlist_for_partition(
        SimpleNamespace(name=WHOLE_PROJECT_PARTITION, unit_names=["x"])
    ) is None
    assert unit_allowlist_for_partition(
        SimpleNamespace(name="Alpha", unit_names=["x", "y"])
    ) == {"x", "y"}


# --------------------------------------------------------------------------- #
# store / restore round-trip
# --------------------------------------------------------------------------- #


def test_store_then_restore_round_trips_bytes_and_groups(tmp_path: Path) -> None:
    cache_root = extraction_cache_dir(tmp_path / "output" / "proj")
    staging_one = tmp_path / "output" / "proj" / "docs_staging"
    source = _write_preprocess(staging_one)

    manifest = store_extraction(cache_root, "k" * 64, staging_one)
    assert manifest is not None
    assert manifest["schema_version"] == 1
    assert manifest["key"] == "k" * 64
    assert manifest["code_version"] == EXTRACTION_CODE_VERSION
    assert manifest["groups"] == ["Flows", "Logging"]
    assert sorted(record["name"] for record in manifest["files"]) == [
        "Flows.md", "Logging.md", "_analysis_guide.md", "_doc_templates.md",
    ]
    assert manifest["total_bytes"] == sum(
        p.stat().st_size for p in source.iterdir()
    )
    assert manifest["created_at"] > 0

    staging_two = tmp_path / "run2" / "docs_staging"
    groups = restore_extraction(cache_root, "k" * 64, staging_two)

    assert groups == ["Flows", "Logging"]
    for name in ("Flows.md", "Logging.md", "_analysis_guide.md", "_doc_templates.md"):
        assert (staging_two / "_preprocess" / name).read_bytes() == (
            source / name
        ).read_bytes()


def test_store_replaces_an_existing_entry_atomically(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    staging = tmp_path / "docs_staging"
    _write_preprocess(staging)
    store_extraction(cache_root, "abc", staging)

    (staging / "_preprocess" / "Flows.md").write_text("# Flows v2\n", encoding="utf-8")
    store_extraction(cache_root, "abc", staging)

    target = tmp_path / "restored"
    assert restore_extraction(cache_root, "abc", target) == ["Flows", "Logging"]
    assert (target / "_preprocess" / "Flows.md").read_text(
        encoding="utf-8"
    ) == "# Flows v2\n"
    # No scratch directories left behind.
    assert sorted(p.name for p in cache_root.iterdir()) == ["abc"]


def test_restore_into_a_staging_dir_that_already_has_preprocess(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    staging = tmp_path / "docs_staging"
    _write_preprocess(staging)
    store_extraction(cache_root, "abc", staging)

    other = tmp_path / "other" / "docs_staging"
    stale = other / "_preprocess"
    stale.mkdir(parents=True)
    (stale / "Obsolete.md").write_text("stale", encoding="utf-8")

    assert restore_extraction(cache_root, "abc", other) == ["Flows", "Logging"]
    assert not (stale / "Obsolete.md").exists(), (
        "the restored layer must replace, not merge with, whatever was there"
    )


# --------------------------------------------------------------------------- #
# Invalid / empty-key handling
# --------------------------------------------------------------------------- #


def test_empty_key_is_a_no_op_for_store_and_restore(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    staging = tmp_path / "docs_staging"
    _write_preprocess(staging)

    assert store_extraction(cache_root, "", staging) is None
    assert not cache_root.exists(), (
        "an empty fingerprint means DO NOT CACHE, never 'cache under the empty key'"
    )
    assert restore_extraction(cache_root, "", tmp_path / "other") is None


def test_store_is_a_no_op_without_a_preprocess_dir(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    staging = tmp_path / "docs_staging"
    staging.mkdir()

    assert store_extraction(cache_root, "abc", staging) is None

    (staging / "_preprocess").mkdir()
    assert store_extraction(cache_root, "abc", staging) is None


def test_restore_misses_on_an_unknown_key(tmp_path: Path) -> None:
    assert restore_extraction(tmp_path / "cache", "nope", tmp_path / "s") is None


@pytest.mark.parametrize("corruption", ["truncate", "drop_manifest", "wrong_sha"])
def test_corrupted_entry_is_rejected_and_leaves_staging_untouched(
    tmp_path: Path, corruption: str
) -> None:
    cache_root = tmp_path / "cache"
    staging = tmp_path / "docs_staging"
    _write_preprocess(staging)
    store_extraction(cache_root, "abc", staging)
    entry = cache_root / "abc"

    if corruption == "truncate":
        (entry / "_preprocess" / "Flows.md").write_text("", encoding="utf-8")
    elif corruption == "drop_manifest":
        (entry / "manifest.json").unlink()
    else:
        manifest = json.loads((entry / "manifest.json").read_text(encoding="utf-8"))
        for record in manifest["files"]:
            if record["name"] == "Flows.md":
                record["sha256"] = "0" * 64
        (entry / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    target = tmp_path / "target" / "docs_staging"
    assert restore_extraction(cache_root, "abc", target) is None
    assert not (target / "_preprocess").exists(), (
        "an invalid entry must never partially populate staging"
    )


def test_missing_file_in_entry_is_rejected(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    staging = tmp_path / "docs_staging"
    _write_preprocess(staging)
    store_extraction(cache_root, "abc", staging)
    (cache_root / "abc" / "_preprocess" / "Logging.md").unlink()

    target = tmp_path / "target"
    assert restore_extraction(cache_root, "abc", target) is None
    assert not (target / "_preprocess").exists()


def test_code_version_bump_invalidates_existing_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_root = tmp_path / "cache"
    staging = tmp_path / "docs_staging"
    _write_preprocess(staging)
    store_extraction(cache_root, "abc", staging)

    monkeypatch.setattr(
        "mcp_server.services.extraction_cache.EXTRACTION_CODE_VERSION", "999",
    )
    assert restore_extraction(cache_root, "abc", tmp_path / "target") is None


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #


def test_prune_keeps_the_newest_entries(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    staging = tmp_path / "docs_staging"
    _write_preprocess(staging)

    for index, key in enumerate(["k1", "k2", "k3", "k4"]):
        store_extraction(cache_root, key, staging)
        manifest_path = cache_root / key / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["created_at"] = 1000.0 + index
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    dropped = prune_extraction_cache(cache_root, keep=2)

    assert sorted(dropped) == ["k1", "k2"]
    assert sorted(p.name for p in cache_root.iterdir()) == ["k3", "k4"]


def test_prune_removes_scratch_dirs_and_tolerates_a_missing_root(
    tmp_path: Path,
) -> None:
    assert prune_extraction_cache(tmp_path / "absent", keep=3) == []

    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    (cache_root / ".tmp-leftover").mkdir()
    (cache_root / ".stale-leftover").mkdir()
    prune_extraction_cache(cache_root, keep=3)
    assert list(cache_root.iterdir()) == []


# --------------------------------------------------------------------------- #
# Phase 4: the cache survives the cleanup rmtree
# --------------------------------------------------------------------------- #


async def test_phase4_stores_preprocess_before_the_rmtree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The definition of "persisted": _preprocess/ is gone from staging and
    present in the cache after Phase 4 runs."""
    from mcp_server.config import config
    from mcp_server.providers.base import DocGenContext
    from mcp_server.providers.custom.models import FactsManifest, HarnessRunState
    from mcp_server.providers.custom.pipeline.phases.phase4_cleanup import Phase4Cleanup

    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc-output")
    monkeypatch.setattr(config, "extraction_cache_enabled", True)
    monkeypatch.setattr(config, "extraction_cache_keep", 3)
    scan_dir = config.intodoc_output_dir / "proj"
    _write_scan(scan_dir)

    output_dir = tmp_path / "output" / "proj"
    staging = output_dir / "docs_staging"
    _write_preprocess(staging)
    (staging / "_analysis").mkdir(parents=True, exist_ok=True)
    (staging / "_analysis" / "Flows.md").write_text("analysis", encoding="utf-8")
    assessment = staging / "_assessment"
    assessment.mkdir(parents=True, exist_ok=True)
    (assessment / "enterprise_gates.json").write_text("{}", encoding="utf-8")

    helpers = MagicMock()
    helpers.enterprise_gate_report.return_value = {}
    helpers.archive_engineering_artifacts.return_value = []
    helpers.required_docs_list = []
    helpers.model_provider.model_name = "test-model"
    helpers.write_provider.model_name = "test-model"

    state = HarnessRunState(
        project_id="proj",
        facts_manifest=FactsManifest(
            project_id="proj",
            platform="talend",
            canonical_unit_label="job",
            unit_count=1,
            groups={"Flows": 1},
        ),
    )
    ctx = DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(output_dir / "logs"),
        preprocess_groups=["Flows", "Logging"],
        total_jobs=1,
        total_groups=1,
        effective_model="m",
        write_model="m",
        platform="talend",
        analysis_guide_file="_analysis_guide.md",
    )

    async def log_cb(pid: str, message: str, level: str = "info") -> None:
        return None

    result = await Phase4Cleanup(helpers=helpers).run(ctx, state, log_cb)

    assert result.success is True
    # The rmtree list is unchanged: staging loses the working set.
    assert not (staging / "_preprocess").exists()
    assert not (staging / "_analysis").exists()

    # ...and the cache, which lives OUTSIDE docs_staging/, kept the evidence.
    cache_root = extraction_cache_dir(output_dir)
    key = compute_extraction_key(scan_dir, platform="talend", unit_allowlist=None)
    assert key
    restored = tmp_path / "next_run" / "docs_staging"
    assert restore_extraction(cache_root, key, restored) == ["Flows", "Logging"]
    assert (restored / "_preprocess" / "Flows.md").read_text(
        encoding="utf-8"
    ) == "# Flows\n\nA_Main-h1\n"


async def test_phase4_store_is_disabled_by_the_kill_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_server.config import config
    from mcp_server.providers.custom.pipeline.phases.phase4_cleanup import Phase4Cleanup

    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc-output")
    monkeypatch.setattr(config, "extraction_cache_enabled", False)
    _write_scan(config.intodoc_output_dir / "proj")

    output_dir = tmp_path / "output" / "proj"
    staging = output_dir / "docs_staging"
    _write_preprocess(staging)

    ctx = SimpleNamespace(
        project_id="proj",
        staging_dir=staging,
        platform="talend",
        partition=None,
        scoped_docs=None,
    )
    Phase4Cleanup(helpers=MagicMock())._store_extraction_cache(ctx)

    assert not extraction_cache_dir(output_dir).exists()


async def test_phase4_store_never_raises_on_a_broken_context(tmp_path: Path) -> None:
    """A cache must never fail the phase."""
    from mcp_server.providers.custom.pipeline.phases.phase4_cleanup import Phase4Cleanup

    ctx = SimpleNamespace(
        project_id="proj",
        staging_dir=tmp_path / "docs_staging",
        platform="talend",
        partition=SimpleNamespace(name="Alpha"),  # no unit_names -> AttributeError
        scoped_docs=None,
    )
    Phase4Cleanup(helpers=MagicMock())._store_extraction_cache(ctx)


async def test_phase4_skips_the_store_for_a_scoped_reingestion(tmp_path: Path) -> None:
    from mcp_server.config import config
    from mcp_server.providers.custom.pipeline.phases.phase4_cleanup import Phase4Cleanup

    output_dir = tmp_path / "output" / "proj"
    staging = output_dir / "docs_staging"
    _write_preprocess(staging)

    ctx = SimpleNamespace(
        project_id="proj",
        staging_dir=staging,
        platform="talend",
        partition=None,
        scoped_docs={"01_ARCHITECTURE_MAP"},
    )
    with patch.object(config, "extraction_cache_enabled", True):
        Phase4Cleanup(helpers=MagicMock())._store_extraction_cache(ctx)

    assert not extraction_cache_dir(output_dir).exists()


# --------------------------------------------------------------------------- #
# HEADLINE: a second pack reuses the extraction layer
# --------------------------------------------------------------------------- #


def test_changing_only_the_pack_does_not_change_the_key(tmp_path: Path) -> None:
    """The extraction layer is pack-INDEPENDENT.

    ``compute_extraction_key`` takes no pack argument by construction: the pack
    decides the interpretation (Phase 2 section universe, doc routing), never
    the deterministic extraction. Two runs whose ONLY difference is the pack
    therefore land on the same key, which is what makes "swap the lens, reuse
    the evidence" true.
    """
    import inspect

    scan_dir = tmp_path / "scan_cache"
    _write_scan(scan_dir)

    signature = inspect.signature(compute_extraction_key)
    assert set(signature.parameters) == {
        "scan_output_dir", "platform", "unit_allowlist",
    }, "no pack/contract/doc-catalog input may leak into the extraction key"

    pack_one = {"pack_id": "intodata.engineering-handover", "content_hash": "aaa"}
    pack_two = {"pack_id": "acme.security-review", "content_hash": "bbb"}
    assert pack_one != pack_two

    key_run_one = compute_extraction_key(
        scan_dir, platform="talend", unit_allowlist=None
    )
    key_run_two = compute_extraction_key(
        scan_dir, platform="talend", unit_allowlist=None
    )
    assert key_run_one == key_run_two != ""


def test_second_pack_reuses_the_cached_extraction_layer(tmp_path: Path) -> None:
    """Run 1 (pack A) stores; run 2 (pack B) restores under the same key."""
    scan_dir = tmp_path / "intodoc-output" / "proj"
    _write_scan(scan_dir)
    output_dir = tmp_path / "output" / "proj"
    cache_root = extraction_cache_dir(output_dir)

    # --- run 1, pack A -----------------------------------------------------
    staging_one = output_dir / "docs_staging"
    source = _write_preprocess(staging_one)
    key_one = compute_extraction_key(
        scan_dir, platform="talend", unit_allowlist=None
    )
    assert store_extraction(cache_root, key_one, staging_one) is not None
    # Phase 4 then deletes the working copy.
    shutil.rmtree(source)

    # --- run 2, pack B (fresh staging, different pack) ----------------------
    staging_two = output_dir / "docs_staging"
    key_two = compute_extraction_key(
        scan_dir, platform="talend", unit_allowlist=None
    )
    assert key_two == key_one

    groups = restore_extraction(cache_root, key_two, staging_two)
    assert groups == ["Flows", "Logging"]
    assert (staging_two / "_preprocess" / "Flows.md").read_text(
        encoding="utf-8"
    ) == "# Flows\n\nA_Main-h1\n"


async def test_generate_docs_second_run_skips_preprocess_on_a_cache_hit(
    claude_svc, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End-to-end at the generate_docs seam.

    Run 1 preprocesses for real and Phase 4 stores the layer; run 2 (fresh
    staging) must NOT call ``_preprocess_scan_data``.

    The pack is not varied here on purpose: ``generate_docs`` resolves the pack
    (``plugin_pack_dir``) BEFORE this seam and then never passes it to
    ``_preprocess_scan_data`` or to ``compute_extraction_key``, so there is no
    code path by which a different pack could change the outcome. That
    structural claim is asserted separately in
    ``test_changing_only_the_pack_does_not_change_the_key``.
    """
    from mcp_server.config import config
    from mcp_server.providers.base import AgentResult
    from mcp_server.services.scanner_service import ScannerService

    monkeypatch.setattr(config, "project_root", tmp_path)
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc-output")
    monkeypatch.setattr(config, "extraction_cache_enabled", True)
    monkeypatch.setattr(config, "extraction_cache_keep", 3)
    scan_dir = config.intodoc_output_dir / "proj"
    _write_scan(scan_dir)

    project = SimpleNamespace(platform="talend")
    captured: list = []

    class _FakeProvider:
        async def run(self, ctx, log_cb):
            captured.append(ctx)
            return AgentResult(success=True)

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    # Keep the preprocess off the intodoc package (not installed in tests).
    monkeypatch.setattr(
        ScannerService, "get_analysis_prompts",
        lambda self, platform: {
            "prompts": {"ANALYSIS_GUIDE.md": "guide"}, "consolidated": True,
        },
    )
    monkeypatch.setattr(
        ScannerService, "get_phase3_guide",
        lambda self, platform: {"content": "phase3 guide"},
    )

    real_preprocess = DocGenerationCoordinator._preprocess_scan_data
    calls: list[str] = []

    async def _counting_preprocess(self, project_id, staging_dir, platform="talend", **kwargs):
        calls.append(project_id)
        return await real_preprocess(
            self, project_id, staging_dir, platform, **kwargs
        )

    monkeypatch.setattr(
        DocGenerationCoordinator, "_preprocess_scan_data", _counting_preprocess,
    )

    async def _run() -> dict:
        with patch("mcp_server.db.session.async_session_factory", lambda: _FakeSession()), \
             patch(
                 "mcp_server.services.project_registry.ProjectRegistry.get_project",
                 return_value=project,
             ), \
             patch(
                 "mcp_server.services.claude_service.get_provider",
                 return_value=_FakeProvider(),
             ), \
             patch(
                 "mcp_server.services.claude_service.instance_settings_service.get_value",
                 return_value=None,
             ):
            return await claude_svc.generate_docs(
                "proj", doc_generation_provider="custom", resume=False,
            )

    # --- run 1: pack A. Real preprocessing happens once. -------------------
    result_one = await _run()
    assert result_one.get("success") is True
    assert calls == ["proj"], "run 1 must do the real extraction"
    ctx_one = captured[0]
    groups_one = sorted(ctx_one.preprocess_groups)
    assert groups_one, "run 1 produced no groups; the fixture is wrong"

    # Simulate the Phase 4 store (the phase itself is covered above).
    output_dir = tmp_path / "output" / "proj"
    cache_root = extraction_cache_dir(output_dir)
    key = compute_extraction_key(scan_dir, platform="talend", unit_allowlist=None)
    assert store_extraction(cache_root, key, ctx_one.staging_dir) is not None

    # --- run 2: pack B, fresh staging. The cache must serve it. ------------
    result_two = await _run()
    assert result_two.get("success") is True
    assert calls == ["proj"], (
        "run 2 re-ran _preprocess_scan_data; the extraction cache did not hit"
    )
    ctx_two = captured[1]
    assert sorted(ctx_two.preprocess_groups) == groups_one
    for group in groups_one:
        assert (ctx_two.staging_dir / "_preprocess" / f"{group}.md").exists()


async def test_generate_docs_respects_the_extraction_cache_kill_switch(
    claude_svc, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from mcp_server.config import config
    from mcp_server.providers.base import AgentResult
    from mcp_server.services.scanner_service import ScannerService

    monkeypatch.setattr(config, "project_root", tmp_path)
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc-output")
    monkeypatch.setattr(config, "extraction_cache_enabled", False)
    scan_dir = config.intodoc_output_dir / "proj"
    _write_scan(scan_dir)

    project = SimpleNamespace(platform="talend")

    class _FakeProvider:
        async def run(self, ctx, log_cb):
            return AgentResult(success=True)

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(
        ScannerService, "get_analysis_prompts",
        lambda self, platform: {
            "prompts": {"ANALYSIS_GUIDE.md": "guide"}, "consolidated": True,
        },
    )
    monkeypatch.setattr(
        ScannerService, "get_phase3_guide",
        lambda self, platform: {"content": "phase3 guide"},
    )

    restores: list[str] = []
    monkeypatch.setattr(
        "mcp_server.services.claude_service.restore_extraction",
        lambda *args, **kwargs: restores.append("called") or None,
    )

    with patch("mcp_server.db.session.async_session_factory", lambda: _FakeSession()), \
         patch(
             "mcp_server.services.project_registry.ProjectRegistry.get_project",
             return_value=project,
         ), \
         patch(
             "mcp_server.services.claude_service.get_provider",
             return_value=_FakeProvider(),
         ), \
         patch(
             "mcp_server.services.claude_service.instance_settings_service.get_value",
             return_value=None,
         ):
        result = await claude_svc.generate_docs(
            "proj", doc_generation_provider="custom", resume=False,
        )

    assert result.get("success") is True
    assert restores == [], "the kill switch must prevent any restore attempt"
