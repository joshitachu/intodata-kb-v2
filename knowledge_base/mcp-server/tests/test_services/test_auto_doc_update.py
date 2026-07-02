"""Tests for the internal agentic auto-doc workflow (deterministic routing)."""

from __future__ import annotations

from pathlib import Path

from mcp_server.services.git_diff_patcher import FileChange
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.reingestion_router import RoutingDecision


def _make_targeted_regen_ready_tree(root: Path, project_id: str) -> None:
    output_dir = root / "output" / project_id
    docs = output_dir / "docs"
    support = docs / "_rag_support" / "phase2_analysis"
    cache = output_dir / "analysis_cache"
    support.mkdir(parents=True)
    cache.mkdir(parents=True)
    (docs / "01_ARCHITECTURE_MAP.md").write_text("# Architecture", encoding="utf-8")
    (support / "new_module.md").write_text("# new_module analysis", encoding="utf-8")
    (cache / "new_module.md").write_text("# new_module analysis", encoding="utf-8")


def _make_scan_group(root: Path, project_id: str, group: str) -> None:
    (root / project_id / "scan" / "jobs" / group).mkdir(parents=True)


class _StubRouter:
    """Deterministic router stub: returns a fixed decision; never calls a model."""

    def __init__(self, decision: RoutingDecision) -> None:
        self._decision = decision
        self.expand_calls = 0

    def decide(self, **kwargs) -> RoutingDecision:
        return self._decision

    async def expand(self, decision: RoutingDecision, **kwargs) -> RoutingDecision:
        # Expand-only is non-blocking and, in tests, a pass-through (no model).
        self.expand_calls += 1
        return decision


def test_quick_change_scanner_detects_source_tests_readme_claims_and_modules():
    from mcp_server.services.quick_change_scanner import QuickChangeScanner

    changes = [
        FileChange(status="A", path="src/payments/InvoiceWorkflow.ts"),
        FileChange(status="A", path="tests/payments/InvoiceWorkflow.test.ts"),
        FileChange(status="M", path="README.md"),
    ]
    diff = (
        "diff --git a/src/payments/InvoiceWorkflow.ts b/src/payments/InvoiceWorkflow.ts\n"
        "+export class InvoiceWorkflow {}\n"
        "diff --git a/README.md b/README.md\n"
        "-The API has 3 endpoints.\n"
        "+The API has 4 endpoints.\n"
    )

    report = QuickChangeScanner().scan(changes, diff)

    assert report.new_source_files == ["src/payments/InvoiceWorkflow.ts"]
    assert report.added_tests == ["tests/payments/InvoiceWorkflow.test.ts"]
    assert "InvoiceWorkflow" in report.symbols
    assert any("4 endpoints" in claim for claim in report.readme_numeric_claims)
    assert "module_folder_changes" in report.likely_doc_impact


async def test_auto_doc_update_skip_advances_commit_and_persists_summary(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.services.auto_doc_update as auto_doc_update
    from mcp_server.config import config

    monkeypatch.setattr(auto_doc_update, "async_session_factory", session_factory)
    monkeypatch.setattr(config, "project_root", tmp_path)

    calls: list[tuple[str, ...]] = []

    async def git_stub(local_path: str, *args: str) -> str:
        calls.append(args)
        if args[:2] == ("diff", "--name-status"):
            return "M\tREADME.md\n"
        if args and args[0] == "diff":
            return "+The README changed.\n"
        return ""

    monkeypatch.setattr(auto_doc_update, "_git", git_stub)

    # README-only change -> no doc-relevant files -> router routes to skip (the
    # runtime short-circuits before the router on an empty doc-relevant set, so
    # this exercises the deterministic skip path).
    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            "proj",
            str(tmp_path / "repo"),
            repo_url="https://github.com/example/repo.git",
            source_type="git",
        )
        await session.commit()

    ok = await auto_doc_update.AutoDocUpdateService(log_mgr=log_manager).run(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        trigger="webhook",
    )

    assert ok is True
    assert ("pull", "--ff-only") in calls
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.last_ingested_commit_sha == "b" * 40
        assert project.git_update_summary["mode"] == "tool_loop_agent"
        assert project.git_update_summary["action"] == "skipped"
        assert project.git_update_summary["outcome"] == "skip"
        assert project.git_update_summary["reason_code"] == "non_doc_change"

    # Slice 4 (US-9) AC3: a skip push touches no documents -> no change-history
    # entry, no history file at all.
    from mcp_server.services.doc_version_history import DocVersionHistoryRepository

    repo = DocVersionHistoryRepository("proj", tmp_path / "output" / "proj")
    assert not repo.history_path.exists()
    assert not repo.sidecar_path.exists()


async def test_auto_doc_update_true_patch_success(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    """Slice 4 true patch: TruePatchExecutor returns action='true_patch' -> commit advances."""
    import mcp_server.services.auto_doc_update as auto_doc_update
    from mcp_server.services.true_patch_executor import PatchResult
    from mcp_server.config import config

    monkeypatch.setattr(auto_doc_update, "async_session_factory", session_factory)
    # Slice 4: a bounded_patch outcome with touched_docs writes a change-history
    # entry under output/{project_id}/ -- keep it off the real repo tree.
    monkeypatch.setattr(config, "project_root", tmp_path)

    async def git_stub(local_path: str, *args: str) -> str:
        if args[:2] == ("diff", "--name-status"):
            return "M\tsrc/PaymentService.ts\n"
        if args and args[0] == "diff":
            return (
                "diff --git a/src/PaymentService.ts b/src/PaymentService.ts\n"
                "@@ -1 +1 @@\n"
                "+export function validatePayment() { return true }\n"
            )
        return ""

    monkeypatch.setattr(auto_doc_update, "_git", git_stub)

    router = _StubRouter(
        RoutingDecision(
            strategy="bounded_patch",
            affected_docs=["04_BUSINESS_RULES_AND_TRANSFORMATIONS"],
            changed_groups=["src"],
            reason="small source change",
        )
    )

    class _StubExecutor:
        def __init__(self, **kwargs):
            pass

        async def run(self, **kwargs):
            return PatchResult(
                action="true_patch",
                patched_groups=["src"],
                patched_docs=["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],
                summary={
                    "true_patch": True,
                    "patched_groups": ["src"],
                    "patched_docs": ["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],
                    "fallback_groups": [],
                    "reason_code": "true_patch_succeeded",
                },
                advance_commit=True,
            )

    monkeypatch.setattr(auto_doc_update, "TruePatchExecutor", _StubExecutor)

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            "proj",
            str(tmp_path / "repo"),
            repo_url="https://github.com/example/repo.git",
            source_type="git",
        )
        await session.commit()

    ok = await auto_doc_update.AutoDocUpdateService(
        log_mgr=log_manager,
        router=router,
    ).run(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        trigger="webhook",
    )

    assert ok is True
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        # advance_commit=True -> SHA advances
        assert project.last_ingested_commit_sha == "b" * 40
        assert project.git_update_summary["action"] == "true_patch"
        # true_patch is the successor to the legacy "patched" action in the
        # patch lane, so it normalizes to the bounded_patch operator outcome;
        # the finer detail is carried by reason_code "true_patch_succeeded".
        assert project.git_update_summary["outcome"] == "bounded_patch"
        assert project.git_update_summary["reason_code"] == "true_patch_succeeded"
        assert project.git_update_summary["touched_docs"] == ["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"]

    # Slice 4 (US-9): the true_patch outcome is a confirmed write -> exactly
    # one change-history entry, and the rollback lookup resolves the touched
    # doc to this push's commit range.
    from mcp_server.services.doc_version_history import CommitRange, DocVersionHistoryRepository

    repo = DocVersionHistoryRepository("proj", tmp_path / "output" / "proj")
    entries = repo.read_all()
    assert len(entries) == 1
    assert entries[0]["outcome"] == "bounded_patch"
    assert entries[0]["before_sha"] == "a" * 40
    assert entries[0]["after_sha"] == "b" * 40
    assert entries[0]["touched_docs"] == ["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"]
    assert repo.resolve("04_BUSINESS_RULES_AND_TRANSFORMATIONS.md") == CommitRange(
        before_sha="a" * 40, after_sha="b" * 40
    )


async def test_auto_doc_update_scoped_regen_threads_targeted_docs(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.services.auto_doc_update as auto_doc_update
    from mcp_server.config import config

    monkeypatch.setattr(auto_doc_update, "async_session_factory", session_factory)
    monkeypatch.setattr(config, "project_root", tmp_path)
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc-output")

    async def git_stub(local_path: str, *args: str) -> str:
        if args[:2] == ("diff", "--name-status"):
            return "A\tsrc/new_module/A.ts\nA\tsrc/new_module/B.ts\n"
        if args and args[0] == "diff":
            return (
                "diff --git a/src/new_module/A.ts b/src/new_module/A.ts\n+export class A {}\n"
                "diff --git a/src/new_module/B.ts b/src/new_module/B.ts\n+export class B {}\n"
            )
        return ""

    monkeypatch.setattr(auto_doc_update, "_git", git_stub)

    router = _StubRouter(
        RoutingDecision(
            strategy="scoped_regen",
            affected_docs=["01_ARCHITECTURE_MAP"],
            changed_groups=["new_module"],
            structural=True,
            manifest_present=True,
            reason="new module needs regenerated coverage",
        )
    )

    seen: dict[str, object] = {}

    async def regen_stub(*args, **kwargs):
        seen["targeted_docs"] = kwargs.get("targeted_docs")
        return True

    monkeypatch.setattr(auto_doc_update, "run_git_reingestion", regen_stub)

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
    _make_targeted_regen_ready_tree(tmp_path, "proj")
    _make_scan_group(tmp_path / "intodoc-output", "proj", "new_module")

    ok = await auto_doc_update.AutoDocUpdateService(
        log_mgr=log_manager,
        router=router,
    ).run(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        trigger="webhook",
    )

    assert ok is True
    assert seen["targeted_docs"] == ["01_ARCHITECTURE_MAP"]
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.git_update_summary["action"] == "targeted_regen"
        assert project.git_update_summary["outcome"] == "targeted_regen"
        assert project.git_update_summary["regen_result"]["success"] is True
        assert project.git_update_summary["regen_result"]["targeted_docs"] == ["01_ARCHITECTURE_MAP"]
        assert project.git_update_summary["fallback_used"] is True
        assert project.git_update_summary["touched_docs"] == ["01_ARCHITECTURE_MAP"]


async def test_auto_doc_update_full_regen_threads_no_doc_filter(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.services.auto_doc_update as auto_doc_update
    from mcp_server.config import config

    monkeypatch.setattr(auto_doc_update, "async_session_factory", session_factory)
    monkeypatch.setattr(config, "project_root", tmp_path)
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc-output")

    async def git_stub(local_path: str, *args: str) -> str:
        if args[:2] == ("diff", "--name-status"):
            return "A\tsrc/new_module/A.ts\n"
        if args and args[0] == "diff":
            return "diff --git a/src/new_module/A.ts b/src/new_module/A.ts\n+export class A {}\n"
        return ""

    monkeypatch.setattr(auto_doc_update, "_git", git_stub)

    router = _StubRouter(
        RoutingDecision(
            strategy="full_regen",
            affected_docs=[],
            changed_groups=["new_module"],
            structural=True,
            manifest_present=False,
            reason="no provenance manifest",
        )
    )

    seen: dict[str, object] = {}

    async def regen_stub(*args, **kwargs):
        seen["targeted_docs"] = kwargs.get("targeted_docs")
        return True

    monkeypatch.setattr(auto_doc_update, "run_git_reingestion", regen_stub)

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
    _make_targeted_regen_ready_tree(tmp_path, "proj")
    _make_scan_group(tmp_path / "intodoc-output", "proj", "new_module")

    ok = await auto_doc_update.AutoDocUpdateService(
        log_mgr=log_manager,
        router=router,
    ).run(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        trigger="webhook",
    )

    assert ok is True
    assert seen["targeted_docs"] is None  # full regen -> no Phase 3 doc filter
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.git_update_summary["action"] == "full_regen"
        assert project.git_update_summary["outcome"] == "full_regen"


async def test_auto_doc_update_scoped_regen_missing_cache_requires_rebaseline(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.services.auto_doc_update as auto_doc_update
    from mcp_server.config import config

    monkeypatch.setattr(auto_doc_update, "async_session_factory", session_factory)
    monkeypatch.setattr(config, "project_root", tmp_path)
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc-output")

    async def git_stub(local_path: str, *args: str) -> str:
        if args[:2] == ("diff", "--name-status"):
            return "A\tsrc/new_module/A.ts\n"
        if args and args[0] == "diff":
            return "diff --git a/src/new_module/A.ts b/src/new_module/A.ts\n+export class A {}\n"
        return ""

    monkeypatch.setattr(auto_doc_update, "_git", git_stub)

    router = _StubRouter(
        RoutingDecision(
            strategy="scoped_regen",
            affected_docs=["01_ARCHITECTURE_MAP"],
            changed_groups=["new_module"],
            structural=True,
            manifest_present=True,
            reason="new module needs regenerated coverage",
        )
    )

    async def regen_stub(*args, **kwargs):
        raise AssertionError("missing cache/support must not launch generation")

    monkeypatch.setattr(auto_doc_update, "run_git_reingestion", regen_stub)

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
    docs = tmp_path / "output" / "proj" / "docs"
    docs.mkdir(parents=True)
    (docs / "01_ARCHITECTURE_MAP.md").write_text("# Architecture", encoding="utf-8")
    _make_scan_group(tmp_path / "intodoc-output", "proj", "new_module")

    ok = await auto_doc_update.AutoDocUpdateService(
        log_mgr=log_manager,
        router=router,
    ).run(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        trigger="webhook",
    )

    assert ok is False
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.last_ingested_commit_sha == "a" * 40
        assert project.git_update_summary["action"] == "manual_review"
        assert project.git_update_summary["outcome"] == "manual_review"
        assert project.git_update_summary["reason_code"] == "full_rebaseline_required"
        assert project.git_update_summary["targeted_regen_ready"] is False
        assert project.git_update_summary["requires_full_rebaseline"] is True
        assert project.git_update_summary["regen_result"]["skipped"] is True


async def test_auto_doc_update_true_patch_scoped_regen_fallback_gates_reingestion(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    """Slice 4 true patch fallback: scoped_regen_fallback from TruePatchExecutor routes to
    _scoped_regen; without a cache/support tree the preflight gates run_git_reingestion."""
    import mcp_server.services.auto_doc_update as auto_doc_update
    from mcp_server.services.true_patch_executor import PatchResult
    from mcp_server.config import config

    monkeypatch.setattr(auto_doc_update, "async_session_factory", session_factory)
    monkeypatch.setattr(config, "project_root", tmp_path)

    async def git_stub(local_path: str, *args: str) -> str:
        if args[:2] == ("diff", "--name-status"):
            return "M\tsrc/PaymentService.ts\n"
        if args and args[0] == "diff":
            return "diff --git a/src/PaymentService.ts b/src/PaymentService.ts\n+export class PaymentService {}\n"
        return ""

    monkeypatch.setattr(auto_doc_update, "_git", git_stub)

    router = _StubRouter(
        RoutingDecision(
            strategy="bounded_patch",
            affected_docs=["04_BUSINESS_RULES_AND_TRANSFORMATIONS"],
            changed_groups=["src"],
            reason="small enough to try",
        )
    )

    class _StubExecutor:
        def __init__(self, **kwargs):
            pass

        async def run(self, **kwargs):
            return PatchResult(
                action="scoped_regen_fallback",
                patched_groups=[],
                patched_docs=[],
                summary={"reason": "all groups failed G3 validation", "fallback_groups": ["src"]},
                advance_commit=False,
                error="All patch groups failed validation; scoped_regen required",
            )

    monkeypatch.setattr(auto_doc_update, "TruePatchExecutor", _StubExecutor)

    async def regen_stub(*args, **kwargs):
        raise AssertionError("_scoped_regen preflight must gate reingestion when cache is absent")

    monkeypatch.setattr(auto_doc_update, "run_git_reingestion", regen_stub)

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            "proj",
            str(tmp_path / "repo"),
            repo_url="https://github.com/example/repo.git",
            source_type="git",
        )
        await session.commit()

    ok = await auto_doc_update.AutoDocUpdateService(
        log_mgr=log_manager,
        router=router,
    ).run(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        trigger="webhook",
    )

    assert ok is False
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.git_update_summary["action"] == "manual_review"
        assert project.git_update_summary["outcome"] == "manual_review"
        # _scoped_regen preflight fails -> full_rebaseline_required (no docs/cache on disk)
        assert project.git_update_summary["reason_code"] == "full_rebaseline_required"
        assert project.git_update_summary["targeted_regen_ready"] is False
        assert project.git_update_summary["requires_full_rebaseline"] is True
        assert project.git_update_summary["fallback_used"] is False
        assert project.git_update_summary["regen_result"]["skipped"] is True

    # Slice 4 (US-9): manual_review is not a confirmed write outcome, even
    # though its summary carries a candidate touched_docs list -- no
    # change-history entry (see doc_version_history.WRITE_OUTCOMES).
    from mcp_server.services.doc_version_history import DocVersionHistoryRepository

    repo = DocVersionHistoryRepository("proj", tmp_path / "output" / "proj")
    assert not repo.history_path.exists()
