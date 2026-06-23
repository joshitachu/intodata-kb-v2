"""Tests for git_reingestion incremental staging (flat analysis_cache contract)."""

from __future__ import annotations

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

        async def run_scan(self, *, project_id: str, repo_path: str, platform: str):
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

        async def run_scan(self, *, project_id: str, repo_path: str, platform: str):
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

        async def run_scan(self, *, project_id: str, repo_path: str, platform: str):
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
