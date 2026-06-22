"""Tests for internal agentic auto-doc workflow pieces."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server.services.git_diff_patcher import FileChange
from mcp_server.services.project_registry import ProjectRegistry


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


def test_doc_impact_parser_accepts_valid_strict_json():
    from mcp_server.services.doc_impact_agent import parse_impact_plan

    plan = parse_impact_plan(
        '{"strategy":"bounded_patch","affected_docs":["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],'
        '"required_facts":["InvoiceWorkflow"],"stale_claims":[],"confidence":0.91,"reason":"small change"}'
    )

    assert plan.strategy == "bounded_patch"
    assert plan.confidence == 0.91
    assert plan.affected_docs == ["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"]


@pytest.mark.parametrize(
    "payload",
    [
        '{"affected_docs":["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],"confidence":0.9}',
        '{"strategy":"bounded_patch","affected_docs":[],"confidence":0.9}',
        '{"strategy":"skip","affected_docs":[]}',
    ],
)
def test_doc_impact_parser_rejects_unsafe_model_output(payload):
    from mcp_server.services.doc_impact_agent import parse_impact_plan

    with pytest.raises((ValueError, TypeError)):
        parse_impact_plan(payload)


async def test_auto_doc_update_skip_advances_commit_and_persists_summary(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.services.auto_doc_update as auto_doc_update

    monkeypatch.setattr(auto_doc_update, "async_session_factory", session_factory)

    calls: list[tuple[str, ...]] = []

    async def git_stub(local_path: str, *args: str) -> str:
        calls.append(args)
        if args[:2] == ("diff", "--name-status"):
            return "M\tREADME.md\n"
        if args and args[0] == "diff":
            return "+The README changed.\n"
        return ""

    monkeypatch.setattr(auto_doc_update, "_git", git_stub)

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
        assert project.git_update_summary["decision_strategy"] == "tool_loop_agent"
        assert project.git_update_summary["tool_call_count"] >= 2


async def test_auto_doc_update_repairs_validation_failure_once(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.services.auto_doc_update as auto_doc_update
    from mcp_server.services.doc_impact_agent import DocImpactPlan
    from mcp_server.services.git_diff_patcher import DiffPatchResult

    monkeypatch.setattr(auto_doc_update, "async_session_factory", session_factory)

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

    class ImpactStub:
        async def plan(self, **kwargs):
            return DocImpactPlan(
                strategy="bounded_patch",
                affected_docs=["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],
                required_facts=["validatePayment"],
                confidence=0.95,
                reason="small source change",
            )

    results = [
        DiffPatchResult(
            action="manual_review",
            summary={
                "action": "manual_review",
                "validation_errors": ["Required obligations not satisfied: obl-01"],
                "patched_docs": ["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],
            },
            error="Manual review required: validation failed",
        ),
        DiffPatchResult(
            action="patched",
            summary={
                "action": "patched",
                "patched_docs": ["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],
            },
            patched_docs=["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],
            advance_commit=True,
        ),
    ]

    async def patch_stub(**kwargs):
        return results.pop(0)

    monkeypatch.setattr(auto_doc_update, "_run_diff_patch", patch_stub)

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
        impact_agent=ImpactStub(),
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
        assert project.last_ingested_commit_sha == "b" * 40
        assert project.git_update_summary["action"] == "patched"
        assert project.git_update_summary["outcome"] == "bounded_patch"
        assert project.git_update_summary["decision_strategy"] == "bounded_patch"
        assert project.git_update_summary["repair_attempts"] == 1
        assert project.git_update_summary["touched_docs"] == ["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"]


async def test_auto_doc_update_targeted_regen_requested_by_agent(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.services.auto_doc_update as auto_doc_update
    from mcp_server.config import config
    from mcp_server.services.doc_impact_agent import DocImpactPlan

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

    class ImpactStub:
        async def plan(self, **kwargs):
            return DocImpactPlan(
                strategy="targeted_regen",
                affected_docs=["01_ARCHITECTURE_MAP.md"],
                confidence=0.8,
                reason="new module needs regenerated coverage",
            )

    async def regen_stub(*args, **kwargs):
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
        impact_agent=ImpactStub(),
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
        assert project.git_update_summary["action"] == "targeted_regen"
        assert project.git_update_summary["outcome"] == "targeted_regen"
        assert project.git_update_summary["reason_code"] == "targeted_regen_succeeded"
        assert project.git_update_summary["decision_strategy"] == "targeted_regen"
        assert project.git_update_summary["regen_result"]["success"] is True
        assert project.git_update_summary["fallback_used"] is True
        assert project.git_update_summary["targeted_regen_ready"] is True
        assert project.git_update_summary["touched_docs"] == ["01_ARCHITECTURE_MAP.md"]


async def test_auto_doc_update_targeted_regen_missing_cache_requires_rebaseline(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.services.auto_doc_update as auto_doc_update
    from mcp_server.config import config
    from mcp_server.services.doc_impact_agent import DocImpactPlan

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

    class ImpactStub:
        async def plan(self, **kwargs):
            return DocImpactPlan(
                strategy="targeted_regen",
                affected_docs=["01_ARCHITECTURE_MAP.md"],
                confidence=0.8,
                reason="new module needs regenerated coverage",
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
        impact_agent=ImpactStub(),
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
        assert project.git_update_summary["reason_codes"] == [
            "missing_rag_support",
            "missing_analysis_cache",
        ]


async def test_auto_doc_update_broad_bounded_plan_stays_in_patch_lane(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.services.auto_doc_update as auto_doc_update
    from mcp_server.config import config
    from mcp_server.services.doc_impact_agent import DocImpactPlan
    from mcp_server.services.git_diff_patcher import DiffPatchResult

    monkeypatch.setattr(auto_doc_update, "async_session_factory", session_factory)
    monkeypatch.setattr(config, "git_diff_patch_max_files", 2)

    async def git_stub(local_path: str, *args: str) -> str:
        if args[:2] == ("diff", "--name-status"):
            return "M\tsrc/a.ts\nM\tsrc/b.ts\nM\tsrc/c.ts\n"
        if args and args[0] == "diff":
            return (
                "diff --git a/src/a.ts b/src/a.ts\n+export class A {}\n"
                "diff --git a/src/b.ts b/src/b.ts\n+export class B {}\n"
                "diff --git a/src/c.ts b/src/c.ts\n+export class C {}\n"
            )
        return ""

    monkeypatch.setattr(auto_doc_update, "_git", git_stub)

    class ImpactStub:
        async def plan(self, **kwargs):
            return DocImpactPlan(
                strategy="bounded_patch",
                affected_docs=["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],
                confidence=0.9,
                reason="model thought patching was enough",
            )

    async def patch_stub(**kwargs):
        return DiffPatchResult(
            action="patched",
            summary={
                "action": "patched",
                "patched_docs": ["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],
            },
            patched_docs=["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],
            advance_commit=True,
        )

    async def regen_stub(*args, **kwargs):
        raise AssertionError("bounded patch plans should not auto-regenerate because file count is broad")

    monkeypatch.setattr(auto_doc_update, "_run_diff_patch", patch_stub)
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
        impact_agent=ImpactStub(),
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
        assert project.git_update_summary["action"] == "patched"
        assert project.git_update_summary["fallback_used"] is False
        assert project.git_update_summary["touched_docs"] == ["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"]


async def test_auto_doc_update_prompt_too_large_does_not_regenerate(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.services.auto_doc_update as auto_doc_update
    from mcp_server.services.doc_impact_agent import DocImpactPlan
    from mcp_server.services.git_diff_patcher import DiffPatchResult

    monkeypatch.setattr(auto_doc_update, "async_session_factory", session_factory)

    async def git_stub(local_path: str, *args: str) -> str:
        if args[:2] == ("diff", "--name-status"):
            return "M\tsrc/PaymentService.ts\n"
        if args and args[0] == "diff":
            return "diff --git a/src/PaymentService.ts b/src/PaymentService.ts\n+export class PaymentService {}\n"
        return ""

    monkeypatch.setattr(auto_doc_update, "_git", git_stub)

    class ImpactStub:
        async def plan(self, **kwargs):
            return DocImpactPlan(
                strategy="bounded_patch",
                affected_docs=["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],
                confidence=0.9,
                reason="small enough to try",
            )

    async def patch_stub(**kwargs):
        return DiffPatchResult(
            action="manual_review",
            summary={
                "action": "manual_review",
                "reason": "Patch prompts too large (40929 estimated tokens)",
            },
            error="Manual review required: Patch prompts too large (40929 estimated tokens)",
        )

    async def regen_stub(*args, **kwargs):
        raise AssertionError("oversized patch prompts should not launch heavy regeneration")

    monkeypatch.setattr(auto_doc_update, "_run_diff_patch", patch_stub)
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
        impact_agent=ImpactStub(),
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
        assert project.git_update_summary["reason_code"] == "unsafe_scope"
        assert project.git_update_summary["fallback_used"] is False
        assert "Patch prompts too large" in project.git_update_summary["reason"]
