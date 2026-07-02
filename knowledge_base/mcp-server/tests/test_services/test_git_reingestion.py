"""Tests for git_reingestion incremental staging (flat analysis_cache contract)."""

from __future__ import annotations

import json
from pathlib import Path

from mcp_server.config import config
from mcp_server.providers.custom.models import FactsManifest, UnitFact
from mcp_server.services.git_reingestion import run_git_reingestion, setup_incremental_staging
from mcp_server.services.project_registry import ProjectRegistry


def _make_cache(tmp_path: Path, project_id: str) -> Path:
    output_dir = tmp_path / "output" / project_id
    docs = output_dir / "docs"
    support = docs / "_rag_support" / "phase2_analysis"
    cache = output_dir / "analysis_cache"
    support.mkdir(parents=True)
    cache.mkdir(parents=True)
    (docs / "01_ARCHITECTURE_MAP.md").write_text("# Architecture", encoding="utf-8")
    (support / "A.md").write_text("# Group A analysis", encoding="utf-8")
    (support / "B.md").write_text("# Group B analysis", encoding="utf-8")
    (cache / "A.md").write_text("# Group A analysis", encoding="utf-8")
    (cache / "B.md").write_text("# Group B analysis", encoding="utf-8")
    return output_dir


def _make_docs_and_support_without_cache(tmp_path: Path, project_id: str) -> Path:
    output_dir = tmp_path / "output" / project_id
    docs = output_dir / "docs"
    support = docs / "_rag_support" / "phase2_analysis"
    support.mkdir(parents=True)
    (docs / "01_ARCHITECTURE_MAP.md").write_text("# Architecture", encoding="utf-8")
    (support / "A.md").write_text("# Group A analysis", encoding="utf-8")
    return output_dir


def test_incremental_staging_restores_flat_cache(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "project_root", tmp_path)
    output_dir = _make_cache(tmp_path, "proj")

    result = setup_incremental_staging("proj", changed_groups={"B"}, is_first_push=False)

    staging_analysis = output_dir / "docs_staging" / "_analysis"
    assert result.staged is True
    assert result.targeted_regen_ready is True
    assert result.reason_codes == []
    assert result.changed_groups == ["B"]
    assert sorted(p.name for p in staging_analysis.iterdir()) == ["A.md"], (
        "unchanged group analyses must be staged flat; changed groups removed"
    )
    assert (staging_analysis / "A.md").read_text(encoding="utf-8") == "# Group A analysis"


def test_incremental_staging_skips_when_cache_absent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "project_root", tmp_path)
    output_dir = _make_docs_and_support_without_cache(tmp_path, "proj")

    result = setup_incremental_staging("proj", changed_groups={"B"}, is_first_push=False)

    assert result.staged is False
    assert result.requires_full_rebaseline is True
    assert result.reason_codes == ["missing_analysis_cache"]
    assert not (output_dir / "docs_staging").exists(), (
        "no cache means no staging — full regeneration fallback"
    )


def test_incremental_staging_skips_on_first_push(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "project_root", tmp_path)
    output_dir = _make_cache(tmp_path, "proj")

    result = setup_incremental_staging("proj", changed_groups={"B"}, is_first_push=True)

    assert result.staged is False
    assert "missing_baseline_commit" in result.reason_codes
    assert not (output_dir / "docs_staging").exists()


def test_incremental_staging_requires_published_rag_support(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "project_root", tmp_path)
    output_dir = tmp_path / "output" / "proj"
    docs = output_dir / "docs"
    cache = output_dir / "analysis_cache"
    docs.mkdir(parents=True)
    cache.mkdir(parents=True)
    (docs / "01_ARCHITECTURE_MAP.md").write_text("# Architecture", encoding="utf-8")
    (cache / "A.md").write_text("# Group A analysis", encoding="utf-8")

    result = setup_incremental_staging("proj", changed_groups={"A"}, is_first_push=False)

    assert result.staged is False
    assert result.targeted_regen_ready is False
    assert result.requires_full_rebaseline is True
    assert result.reason_codes == ["missing_rag_support"]
    assert not (output_dir / "docs_staging").exists()


async def test_run_git_reingestion_advances_baseline_after_successful_background(
    session_factory,
    monkeypatch,
    tmp_path: Path,
    log_manager,
) -> None:
    import mcp_server.api.routes.projects as projects_mod
    import mcp_server.services.git_reingestion as git_reingestion

    monkeypatch.setattr(config, "project_root", tmp_path)
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc-output")
    monkeypatch.setattr(git_reingestion, "async_session_factory", session_factory)
    monkeypatch.setattr(projects_mod, "async_session_factory", session_factory)

    async def pull_stub(local_path: str, github_token_enc: str | None = None) -> str:
        return ""

    async def run_git_stub(local_path: str, *args: str, **kwargs) -> str:
        if args[:2] == ("diff", "--name-status"):
            return "M\tjobs/A/file.py\n"
        return ""

    class ScannerStub:
        def __init__(self, log_manager=None):
            self.log_manager = log_manager

        async def run_scan(self, *, project_id: str, repo_path: str, platform: str, output_dir: str | None = None):
            return {
                "success": True,
                "scan_result_path": str(tmp_path / "scan_result.json"),
                "output_dir": str(tmp_path / "intodoc-output" / project_id),
                "job_count": 1,
            }

    async def generate_background_stub(
        project_id: str,
        gen_run_id: int,
        log_mgr,
        **kwargs,
    ) -> None:
        async with session_factory() as session:
            registry = ProjectRegistry(session)
            await registry.update_status(project_id, "ready", error_message=None)
            await session.commit()

    monkeypatch.setattr(git_reingestion, "pull_remote_ff_only", pull_stub)
    monkeypatch.setattr(git_reingestion, "run_git", run_git_stub)
    monkeypatch.setattr(git_reingestion, "ScannerService", ScannerStub)
    monkeypatch.setattr(projects_mod, "_run_generate_background", generate_background_stub)
    projects_mod._currently_generating.discard("proj")

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.add_project(
            "proj",
            str(tmp_path / "repo"),
            repo_url="https://github.com/example/repo.git",
            source_type="git",
        )
        project.last_ingested_commit_sha = "a" * 40
        await session.commit()
    _make_cache(tmp_path, "proj")
    scan_group = tmp_path / "intodoc-output" / "proj" / "scan" / "jobs" / "A"
    scan_group.mkdir(parents=True)

    ok = await run_git_reingestion(
        "proj",
        "a" * 40,
        "b" * 40,
        triggered_by="webhook",
        log_mgr=log_manager,
    )

    assert ok is True
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.last_ingested_commit_sha == "b" * 40


async def test_run_git_reingestion_records_rebaseline_required_without_generation(
    session_factory,
    monkeypatch,
    tmp_path: Path,
    log_manager,
) -> None:
    import mcp_server.api.routes.projects as projects_mod
    import mcp_server.services.git_reingestion as git_reingestion

    monkeypatch.setattr(config, "project_root", tmp_path)
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc-output")
    monkeypatch.setattr(git_reingestion, "async_session_factory", session_factory)
    monkeypatch.setattr(projects_mod, "async_session_factory", session_factory)

    async def pull_stub(local_path: str, github_token_enc: str | None = None) -> str:
        return ""

    async def run_git_stub(local_path: str, *args: str, **kwargs) -> str:
        if args[:2] == ("diff", "--name-status"):
            return "M\tjobs/A/file.py\n"
        return ""

    class ScannerStub:
        def __init__(self, log_manager=None):
            self.log_manager = log_manager

        async def run_scan(self, *, project_id: str, repo_path: str, platform: str, output_dir: str | None = None):
            return {
                "success": True,
                "scan_result_path": str(tmp_path / "scan_result.json"),
                "output_dir": str(tmp_path / "intodoc-output" / project_id),
                "job_count": 1,
            }

    async def generate_background_stub(*args, **kwargs) -> None:
        raise AssertionError("targeted regen preflight should stop before generation")

    monkeypatch.setattr(git_reingestion, "pull_remote_ff_only", pull_stub)
    monkeypatch.setattr(git_reingestion, "run_git", run_git_stub)
    monkeypatch.setattr(git_reingestion, "ScannerService", ScannerStub)
    monkeypatch.setattr(projects_mod, "_run_generate_background", generate_background_stub)
    projects_mod._currently_generating.discard("proj")

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.add_project(
            "proj",
            str(tmp_path / "repo"),
            repo_url="https://github.com/example/repo.git",
            source_type="git",
        )
        project.last_ingested_commit_sha = "a" * 40
        await session.commit()
    _make_docs_and_support_without_cache(tmp_path, "proj")
    scan_group = tmp_path / "intodoc-output" / "proj" / "scan" / "jobs" / "A"
    scan_group.mkdir(parents=True)

    ok = await run_git_reingestion(
        "proj",
        "a" * 40,
        "b" * 40,
        triggered_by="webhook",
        log_mgr=log_manager,
    )

    assert ok is False
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.last_ingested_commit_sha == "a" * 40
        assert project.git_update_summary["action"] == "manual_review"
        assert project.git_update_summary["reason_code"] == "full_rebaseline_required"
        assert project.git_update_summary["requires_full_rebaseline"] is True
        assert project.git_update_summary["targeted_regen_ready"] is False
        assert project.git_update_summary["reason_codes"] == ["missing_analysis_cache"]


# --- Slice 1: blocking correctness gates + hold-SHA ---------------------------


def _facts_manifest() -> FactsManifest:
    return FactsManifest(
        project_id="proj",
        platform="generic",
        canonical_unit_label="units",
        unit_count=1,
        groups={"A": 1},
        units=[UnitFact(name="job_alpha", group="A")],
    )


def _write_staging(output_dir: Path, *, doc_body: str, analysis_body: str | None) -> None:
    staging = output_dir / "docs_staging"
    staging.mkdir(parents=True, exist_ok=True)
    _facts_manifest().write(staging / "facts_manifest.json")
    (staging / "01_X.md").write_text(doc_body, encoding="utf-8")
    if analysis_body is not None:
        analysis = staging / "_rag_support" / "phase2_analysis"
        analysis.mkdir(parents=True, exist_ok=True)
        (analysis / "A.md").write_text(analysis_body, encoding="utf-8")


def _good_analysis() -> str:
    return "### GROUP_SUMMARY\n" + ("Substantive analysis. " * 50)


async def _common_reingestion_setup(monkeypatch, tmp_path, session_factory):
    import mcp_server.api.routes.projects as projects_mod
    import mcp_server.services.git_reingestion as git_reingestion

    monkeypatch.setattr(config, "project_root", tmp_path)
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc-output")
    monkeypatch.setattr(git_reingestion, "async_session_factory", session_factory)
    monkeypatch.setattr(projects_mod, "async_session_factory", session_factory)

    async def pull_stub(local_path: str, github_token_enc: str | None = None) -> str:
        return ""

    async def run_git_stub(local_path: str, *args: str, **kwargs) -> str:
        if args[:2] == ("diff", "--name-status"):
            return "M\tjobs/A/file.py\n"
        return ""

    class ScannerStub:
        def __init__(self, log_manager=None):
            self.log_manager = log_manager

        async def run_scan(self, *, project_id: str, repo_path: str, platform: str, output_dir: str | None = None):
            return {
                "success": True,
                "scan_result_path": str(tmp_path / "scan_result.json"),
                "output_dir": str(tmp_path / "intodoc-output" / project_id),
                "job_count": 1,
            }

    monkeypatch.setattr(git_reingestion, "pull_remote_ff_only", pull_stub)
    monkeypatch.setattr(git_reingestion, "run_git", run_git_stub)
    monkeypatch.setattr(git_reingestion, "ScannerService", ScannerStub)
    projects_mod._currently_generating.discard("proj")

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.add_project(
            "proj",
            str(tmp_path / "repo"),
            repo_url="https://github.com/example/repo.git",
            source_type="git",
        )
        project.last_ingested_commit_sha = "a" * 40
        await session.commit()
    _make_cache(tmp_path, "proj")
    scan_group = tmp_path / "intodoc-output" / "proj" / "scan" / "jobs" / "A"
    scan_group.mkdir(parents=True)
    return projects_mod


async def test_run_git_reingestion_holds_sha_when_gate_fails_after_retry(
    session_factory,
    monkeypatch,
    tmp_path: Path,
    log_manager,
) -> None:
    """Gate fails on both the first attempt and the single retry -> hold SHA."""
    projects_mod = await _common_reingestion_setup(monkeypatch, tmp_path, session_factory)
    output_dir = tmp_path / "output" / "proj"
    calls: list[str] = []

    async def generate_background_stub(project_id, run_id, log_mgr, **kwargs):
        calls.append("gen")
        # Bad output: the changed unit is absent from the doc.
        _write_staging(output_dir, doc_body="no unit named here", analysis_body=_good_analysis())
        gate = kwargs.get("pre_swap_gate")
        outcome = gate(output_dir / "docs_staging") if gate else None
        async with session_factory() as session:
            registry = ProjectRegistry(session)
            if outcome is not None and not outcome.passed:
                await registry.update_status(
                    project_id, "error", error_message=f"manual_review: {outcome.reason}"
                )
            else:
                await registry.update_status(project_id, "ready", error_message=None)
            await session.commit()

    monkeypatch.setattr(projects_mod, "_run_generate_background", generate_background_stub)

    ok = await run_git_reingestion(
        "proj", "a" * 40, "b" * 40, triggered_by="webhook", log_mgr=log_manager
    )

    assert ok is False
    assert calls == ["gen", "gen"], "must generate once, then retry exactly once"
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.last_ingested_commit_sha == "a" * 40, "SHA held at prior commit"
        summary = project.git_update_summary
        assert summary["held"] is True
        assert summary["reason_code"] == "correctness_gate_failed"
        assert summary["action"] == "manual_review"
        assert summary["gate"]["missing_units"] == ["job_alpha"]


async def test_run_git_reingestion_advances_sha_when_retry_passes(
    session_factory,
    monkeypatch,
    tmp_path: Path,
    log_manager,
) -> None:
    """Gate fails first, retry produces correct docs -> swap + SHA advances."""
    projects_mod = await _common_reingestion_setup(monkeypatch, tmp_path, session_factory)
    output_dir = tmp_path / "output" / "proj"
    calls: list[str] = []

    async def generate_background_stub(project_id, run_id, log_mgr, **kwargs):
        attempt = len(calls)
        calls.append("gen")
        if attempt == 0:
            _write_staging(output_dir, doc_body="no unit named here", analysis_body=_good_analysis())
        else:
            _write_staging(
                output_dir, doc_body="Documents job_alpha fully.", analysis_body=_good_analysis()
            )
        gate = kwargs.get("pre_swap_gate")
        outcome = gate(output_dir / "docs_staging") if gate else None
        async with session_factory() as session:
            registry = ProjectRegistry(session)
            if outcome is not None and not outcome.passed:
                await registry.update_status(
                    project_id, "error", error_message=f"manual_review: {outcome.reason}"
                )
            else:
                await registry.update_status(project_id, "ready", error_message=None)
            await session.commit()

    monkeypatch.setattr(projects_mod, "_run_generate_background", generate_background_stub)

    ok = await run_git_reingestion(
        "proj", "a" * 40, "b" * 40, triggered_by="webhook", log_mgr=log_manager
    )

    assert ok is True
    assert calls == ["gen", "gen"]
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.last_ingested_commit_sha == "b" * 40, "SHA advanced after retry passed"


async def test_run_git_reingestion_threads_targeted_docs_as_scoped(
    session_factory,
    monkeypatch,
    tmp_path: Path,
    log_manager,
) -> None:
    """Slice 3: targeted_docs reach _run_generate_background as scoped_docs."""
    projects_mod = await _common_reingestion_setup(monkeypatch, tmp_path, session_factory)
    output_dir = tmp_path / "output" / "proj"
    seen: dict[str, object] = {}

    async def generate_background_stub(project_id, run_id, log_mgr, **kwargs):
        seen["scoped_docs"] = kwargs.get("scoped_docs")
        _write_staging(
            output_dir, doc_body="Documents job_alpha fully.", analysis_body=_good_analysis()
        )
        gate = kwargs.get("pre_swap_gate")
        outcome = gate(output_dir / "docs_staging") if gate else None
        async with session_factory() as session:
            registry = ProjectRegistry(session)
            if outcome is not None and not outcome.passed:
                await registry.update_status(
                    project_id, "error", error_message=f"manual_review: {outcome.reason}"
                )
            else:
                await registry.update_status(project_id, "ready", error_message=None)
            await session.commit()

    monkeypatch.setattr(projects_mod, "_run_generate_background", generate_background_stub)

    ok = await run_git_reingestion(
        "proj",
        "a" * 40,
        "b" * 40,
        triggered_by="webhook",
        log_mgr=log_manager,
        targeted_docs=["01_ARCHITECTURE_MAP"],
    )

    assert ok is True
    assert seen["scoped_docs"] == {"01_ARCHITECTURE_MAP"}


async def test_run_git_reingestion_full_regen_passes_no_scoped_docs(
    session_factory,
    monkeypatch,
    tmp_path: Path,
    log_manager,
) -> None:
    projects_mod = await _common_reingestion_setup(monkeypatch, tmp_path, session_factory)
    output_dir = tmp_path / "output" / "proj"
    seen: dict[str, object] = {"set": False}

    async def generate_background_stub(project_id, run_id, log_mgr, **kwargs):
        seen["scoped_docs"] = kwargs.get("scoped_docs")
        seen["set"] = True
        _write_staging(
            output_dir, doc_body="Documents job_alpha fully.", analysis_body=_good_analysis()
        )
        gate = kwargs.get("pre_swap_gate")
        outcome = gate(output_dir / "docs_staging") if gate else None
        async with session_factory() as session:
            registry = ProjectRegistry(session)
            await registry.update_status(project_id, "ready", error_message=None)
            await session.commit()

    monkeypatch.setattr(projects_mod, "_run_generate_background", generate_background_stub)

    ok = await run_git_reingestion(
        "proj", "a" * 40, "b" * 40, triggered_by="webhook", log_mgr=log_manager
    )

    assert ok is True
    assert seen["set"] is True
    assert seen["scoped_docs"] is None


def _write_impact_manifest(tmp_path: Path, project_id: str) -> None:
    """Provenance manifest mapping scan group 'A' to a group-scoped doc plus a
    cross-cutting (canonical_inventory) summary doc."""
    support = tmp_path / "output" / project_id / "docs" / "_rag_support"
    support.mkdir(parents=True, exist_ok=True)
    (support / "_doc_pack_manifest.json").write_text(
        json.dumps(
            {
                "doc_packs": {
                    "02_ORDERS": {
                        "included": {"A.md": ["GROUP_SUMMARY"]},
                        "included_obligations": [],
                    },
                    "09_SUMMARY": {
                        "included": {"A.md": ["GROUP_SUMMARY"]},
                        "included_obligations": [{"category": "canonical_inventory"}],
                    },
                }
            }
        ),
        encoding="utf-8",
    )


async def test_impact_routing_derives_scoped_docs_from_fingerprint_not_targeted(
    session_factory,
    monkeypatch,
    tmp_path: Path,
    log_manager,
) -> None:
    """Slice 1: with the flag ON, the Phase 3 scope comes from the fingerprint
    dirty-set via the provenance map, NOT from the legacy passed-in targeted_docs."""
    projects_mod = await _common_reingestion_setup(monkeypatch, tmp_path, session_factory)
    monkeypatch.setattr(config, "impact_routing_enabled", True)
    _write_impact_manifest(tmp_path, "proj")
    output_dir = tmp_path / "output" / "proj"
    seen: dict[str, object] = {}

    async def generate_background_stub(project_id, run_id, log_mgr, **kwargs):
        seen["scoped_docs"] = kwargs.get("scoped_docs")
        _write_staging(
            output_dir, doc_body="Documents job_alpha fully.", analysis_body=_good_analysis()
        )
        # Satisfy the (now dependency-aware) correctness gate: the changed unit
        # must appear in one of group A's MAPPED docs.
        (output_dir / "docs_staging" / "02_ORDERS.md").write_text(
            "Documents job_alpha fully.", encoding="utf-8"
        )
        gate = kwargs.get("pre_swap_gate")
        outcome = gate(output_dir / "docs_staging") if gate else None
        async with session_factory() as session:
            registry = ProjectRegistry(session)
            await registry.update_status(project_id, "ready", error_message=None)
            await session.commit()

    monkeypatch.setattr(projects_mod, "_run_generate_background", generate_background_stub)

    ok = await run_git_reingestion(
        "proj",
        "a" * 40,
        "b" * 40,
        triggered_by="webhook",
        log_mgr=log_manager,
        targeted_docs=["LEGACY_ROUTER_VALUE_SHOULD_BE_IGNORED"],
    )

    assert ok is True
    # Derived from group 'A' -> {02_ORDERS (group-scoped), 09_SUMMARY (cross-cutting)}.
    assert seen["scoped_docs"] == {"02_ORDERS", "09_SUMMARY"}
    assert "LEGACY_ROUTER_VALUE_SHOULD_BE_IGNORED" not in seen["scoped_docs"]


async def test_impact_routing_full_rederive_forces_no_scoped_docs(
    session_factory,
    monkeypatch,
    tmp_path: Path,
    log_manager,
) -> None:
    """Slice 1: full_rederive=True forces a full Phase 3 regen (scoped_docs=None)
    even under impact routing -- the explicit first-push / manual-rebuild path."""
    projects_mod = await _common_reingestion_setup(monkeypatch, tmp_path, session_factory)
    monkeypatch.setattr(config, "impact_routing_enabled", True)
    _write_impact_manifest(tmp_path, "proj")
    output_dir = tmp_path / "output" / "proj"
    seen: dict[str, object] = {"set": False}

    async def generate_background_stub(project_id, run_id, log_mgr, **kwargs):
        seen["scoped_docs"] = kwargs.get("scoped_docs")
        seen["set"] = True
        _write_staging(
            output_dir, doc_body="Documents job_alpha fully.", analysis_body=_good_analysis()
        )
        # Satisfy the dependency-aware correctness gate (mapped doc for group A).
        (output_dir / "docs_staging" / "02_ORDERS.md").write_text(
            "Documents job_alpha fully.", encoding="utf-8"
        )
        gate = kwargs.get("pre_swap_gate")
        outcome = gate(output_dir / "docs_staging") if gate else None
        async with session_factory() as session:
            registry = ProjectRegistry(session)
            await registry.update_status(project_id, "ready", error_message=None)
            await session.commit()

    monkeypatch.setattr(projects_mod, "_run_generate_background", generate_background_stub)

    ok = await run_git_reingestion(
        "proj",
        "a" * 40,
        "b" * 40,
        triggered_by="webhook",
        log_mgr=log_manager,
        full_rederive=True,
    )

    assert ok is True
    assert seen["set"] is True
    assert seen["scoped_docs"] is None


async def test_run_git_reingestion_advances_sha_when_gate_passes_first_try(
    session_factory,
    monkeypatch,
    tmp_path: Path,
    log_manager,
) -> None:
    projects_mod = await _common_reingestion_setup(monkeypatch, tmp_path, session_factory)
    output_dir = tmp_path / "output" / "proj"
    calls: list[str] = []

    async def generate_background_stub(project_id, run_id, log_mgr, **kwargs):
        calls.append("gen")
        _write_staging(
            output_dir, doc_body="Documents job_alpha fully.", analysis_body=_good_analysis()
        )
        gate = kwargs.get("pre_swap_gate")
        outcome = gate(output_dir / "docs_staging") if gate else None
        async with session_factory() as session:
            registry = ProjectRegistry(session)
            if outcome is not None and not outcome.passed:
                await registry.update_status(
                    project_id, "error", error_message=f"manual_review: {outcome.reason}"
                )
            else:
                await registry.update_status(project_id, "ready", error_message=None)
            await session.commit()

    monkeypatch.setattr(projects_mod, "_run_generate_background", generate_background_stub)

    ok = await run_git_reingestion(
        "proj", "a" * 40, "b" * 40, triggered_by="webhook", log_mgr=log_manager
    )

    assert ok is True
    assert calls == ["gen"], "no retry when the gate passes on the first attempt"
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.last_ingested_commit_sha == "b" * 40


# ---------------------------------------------------------------------------
# Slice 0 — infra-only push early-exit
# ---------------------------------------------------------------------------


async def test_run_git_reingestion_skips_when_all_changes_are_infra(
    session_factory,
    monkeypatch,
    tmp_path: Path,
    log_manager,
) -> None:
    """Infra-only push (Dockerfile, .env) must advance SHA and return True without generation."""
    import mcp_server.api.routes.projects as projects_mod
    import mcp_server.services.git_reingestion as git_reingestion

    monkeypatch.setattr(config, "project_root", tmp_path)
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc-output")
    monkeypatch.setattr(git_reingestion, "async_session_factory", session_factory)
    monkeypatch.setattr(projects_mod, "async_session_factory", session_factory)

    async def pull_stub(local_path: str, github_token_enc: str | None = None) -> str:
        return ""

    async def run_git_stub(local_path: str, *args: str, **kwargs) -> str:
        if args[:2] == ("diff", "--name-status"):
            return "M\tDockerfile\nM\t.env.production\n"
        return ""

    class ScannerStub:
        def __init__(self, log_manager=None):
            self.log_manager = log_manager

        async def run_scan(self, *, project_id: str, repo_path: str, platform: str, output_dir: str | None = None):
            return {
                "success": True,
                "scan_result_path": str(tmp_path / "scan_result.json"),
                "output_dir": str(tmp_path / "intodoc-output" / project_id),
                "job_count": 1,
            }

    generation_called: list[str] = []

    async def generate_background_stub(*args, **kwargs) -> None:
        generation_called.append("called")

    monkeypatch.setattr(git_reingestion, "pull_remote_ff_only", pull_stub)
    monkeypatch.setattr(git_reingestion, "run_git", run_git_stub)
    monkeypatch.setattr(git_reingestion, "ScannerService", ScannerStub)
    monkeypatch.setattr(projects_mod, "_run_generate_background", generate_background_stub)
    projects_mod._currently_generating.discard("proj")

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.add_project(
            "proj",
            str(tmp_path / "repo"),
            repo_url="https://github.com/example/repo.git",
            source_type="git",
        )
        project.last_ingested_commit_sha = "a" * 40
        await session.commit()
    _make_cache(tmp_path, "proj")
    scan_group = tmp_path / "intodoc-output" / "proj" / "scan" / "jobs" / "A"
    scan_group.mkdir(parents=True)

    sink: dict = {}
    ok = await run_git_reingestion(
        "proj", "a" * 40, "b" * 40, triggered_by="webhook", log_mgr=log_manager,
        outcome_sink=sink,
    )

    assert ok is True, "infra-only push must return True (skip = success)"
    assert generation_called == [], "generation must NOT be triggered on infra-only push"
    assert sink.get("skipped") is True, (
        "outcome_sink must report skipped=True so the caller does not record a "
        "phantom targeted_regen (no docs were regenerated)"
    )
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.last_ingested_commit_sha == "b" * 40, "SHA must advance on infra-only skip"


async def test_run_git_reingestion_proceeds_on_mixed_infra_and_source_push(
    session_factory,
    monkeypatch,
    tmp_path: Path,
    log_manager,
) -> None:
    """Mixed push (one infra file + one source file) must NOT trigger the early exit."""
    import mcp_server.api.routes.projects as projects_mod
    import mcp_server.services.git_reingestion as git_reingestion

    monkeypatch.setattr(config, "project_root", tmp_path)
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc-output")
    monkeypatch.setattr(git_reingestion, "async_session_factory", session_factory)
    monkeypatch.setattr(projects_mod, "async_session_factory", session_factory)

    async def pull_stub(local_path: str, github_token_enc: str | None = None) -> str:
        return ""

    async def run_git_stub(local_path: str, *args: str, **kwargs) -> str:
        if args[:2] == ("diff", "--name-status"):
            # One infra file + one integration source file
            return "M\tDockerfile\nM\tjobs/A/file.xml\n"
        return ""

    class ScannerStub:
        def __init__(self, log_manager=None):
            self.log_manager = log_manager

        async def run_scan(self, *, project_id: str, repo_path: str, platform: str, output_dir: str | None = None):
            return {
                "success": True,
                "scan_result_path": str(tmp_path / "scan_result.json"),
                "output_dir": str(tmp_path / "intodoc-output" / project_id),
                "job_count": 1,
            }

    generation_called: list[str] = []

    async def generate_background_stub(project_id, run_id, log_mgr, **kwargs):
        generation_called.append("called")
        output_dir = tmp_path / "output" / project_id
        _write_staging(
            output_dir, doc_body="Documents job_alpha fully.", analysis_body=_good_analysis()
        )
        gate = kwargs.get("pre_swap_gate")
        outcome = gate(output_dir / "docs_staging") if gate else None
        async with session_factory() as session:
            registry = ProjectRegistry(session)
            if outcome is not None and not outcome.passed:
                await registry.update_status(
                    project_id, "error", error_message=f"manual_review: {outcome.reason}"
                )
            else:
                await registry.update_status(project_id, "ready", error_message=None)
            await session.commit()

    monkeypatch.setattr(git_reingestion, "pull_remote_ff_only", pull_stub)
    monkeypatch.setattr(git_reingestion, "run_git", run_git_stub)
    monkeypatch.setattr(git_reingestion, "ScannerService", ScannerStub)
    monkeypatch.setattr(projects_mod, "_run_generate_background", generate_background_stub)
    projects_mod._currently_generating.discard("proj")

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.add_project(
            "proj",
            str(tmp_path / "repo"),
            repo_url="https://github.com/example/repo.git",
            source_type="git",
        )
        project.last_ingested_commit_sha = "a" * 40
        await session.commit()
    _make_cache(tmp_path, "proj")
    scan_group = tmp_path / "intodoc-output" / "proj" / "scan" / "jobs" / "A"
    scan_group.mkdir(parents=True)

    ok = await run_git_reingestion(
        "proj", "a" * 40, "b" * 40, triggered_by="webhook", log_mgr=log_manager
    )

    assert ok is True
    assert generation_called == ["called"], "mixed push must proceed to generation"


# ---------------------------------------------------------------------------
# Slice 5 (US-7/US-8): manual full-rebuild success path records history.
# Additive tests only -- everything above this line is unmodified S1 coverage.
# ---------------------------------------------------------------------------


async def test_full_rederive_success_records_manual_rebuild_history_entry(
    session_factory,
    monkeypatch,
    tmp_path: Path,
    log_manager,
) -> None:
    """The ONE call site (full_rederive=True) must, on success, persist a
    history entry with reason_code == manual_full_rebuild_reason_code so
    drift_summary.compute_drift_summary() recognizes the reset (Slice 5)."""
    from mcp_server.services.doc_version_history import DocVersionHistoryRepository

    projects_mod = await _common_reingestion_setup(monkeypatch, tmp_path, session_factory)
    output_dir = tmp_path / "output" / "proj"

    async def generate_background_stub(project_id, run_id, log_mgr, **kwargs):
        docs_dir = output_dir / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "01_ARCHITECTURE_MAP.md").write_text("# Architecture", encoding="utf-8")
        (docs_dir / "02_ORDERS.md").write_text("# Orders", encoding="utf-8")
        async with session_factory() as session:
            registry = ProjectRegistry(session)
            await registry.update_status(project_id, "ready", error_message=None)
            await session.commit()

    monkeypatch.setattr(projects_mod, "_run_generate_background", generate_background_stub)

    ok = await run_git_reingestion(
        "proj",
        "a" * 40,
        "b" * 40,
        triggered_by="manual_full_rebuild",
        log_mgr=log_manager,
        full_rederive=True,
    )

    assert ok is True

    repo = DocVersionHistoryRepository("proj", tmp_path / "output" / "proj")
    entries = repo.read_all()
    assert len(entries) == 1
    assert entries[0]["outcome"] == "full_regen"
    assert entries[0]["reason_code"] == config.manual_full_rebuild_reason_code
    assert entries[0]["before_sha"] == "a" * 40
    assert entries[0]["after_sha"] == "b" * 40
    assert sorted(entries[0]["touched_docs"]) == ["01_ARCHITECTURE_MAP.md", "02_ORDERS.md"]

    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.git_update_summary["reason_code"] == config.manual_full_rebuild_reason_code
        assert project.git_update_summary["outcome"] == "full_regen"


async def test_full_rederive_reason_code_is_independent_of_triggered_by_string(
    session_factory,
    monkeypatch,
    tmp_path: Path,
    log_manager,
) -> None:
    """Attack 3 (critique.md): the reset reason_code must be assigned
    UNCONDITIONALLY by the full_rederive branch, never derived from the
    caller-supplied `triggered_by` string -- so an automatic caller cannot
    smuggle a reset merely by naming its trigger similarly."""
    from mcp_server.services.doc_version_history import DocVersionHistoryRepository

    projects_mod = await _common_reingestion_setup(monkeypatch, tmp_path, session_factory)
    output_dir = tmp_path / "output" / "proj"

    async def generate_background_stub(project_id, run_id, log_mgr, **kwargs):
        docs_dir = output_dir / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "01_ARCHITECTURE_MAP.md").write_text("# Architecture", encoding="utf-8")
        async with session_factory() as session:
            registry = ProjectRegistry(session)
            await registry.update_status(project_id, "ready", error_message=None)
            await session.commit()

    monkeypatch.setattr(projects_mod, "_run_generate_background", generate_background_stub)

    # triggered_by is an arbitrary string unrelated to "manual_full_rebuild" --
    # the reason code must still be assigned because full_rederive=True.
    ok = await run_git_reingestion(
        "proj",
        "a" * 40,
        "b" * 40,
        triggered_by="webhook",
        log_mgr=log_manager,
        full_rederive=True,
    )

    assert ok is True
    repo = DocVersionHistoryRepository("proj", tmp_path / "output" / "proj")
    entries = repo.read_all()
    assert entries[0]["reason_code"] == config.manual_full_rebuild_reason_code, (
        "reason_code must come from full_rederive=True, not from triggered_by"
    )
