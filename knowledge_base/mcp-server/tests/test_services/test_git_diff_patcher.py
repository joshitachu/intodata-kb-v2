"""Tests for diff-only webhook doc patching."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from mcp_server.providers.custom.models import ModelResult
from mcp_server.services.project_registry import ProjectRegistry


def test_parse_agent_json_repairs_malformed_model_output():
    import mcp_server.services.git_diff_patcher as patcher

    parsed = patcher._parse_agent_json(
        '{"change_summary":"x","inspected_context":[],"affected_docs":[],'
        '"edits":[],"confidence":0.9'
    )

    assert parsed["change_summary"] == "x"
    assert parsed["edits"] == []
    assert parsed["confidence"] == 0.9


async def test_run_git_sanitizes_remote_errors(monkeypatch, tmp_path):
    import mcp_server.services.git_diff_patcher as patcher

    class ProcStub:
        returncode = 128

        async def communicate(self):
            return (
                b"",
                b"fatal: Authentication failed for 'https://token@example.com/org/repo.git'",
            )

        def kill(self):
            return None

    async def create_proc(*args, **kwargs):
        return ProcStub()

    monkeypatch.setattr(patcher.asyncio, "create_subprocess_exec", create_proc)

    with pytest.raises(RuntimeError) as exc:
        await patcher._run_git(str(tmp_path), "fetch", "--quiet", "origin")

    assert str(exc.value) == "error_type: authentication_failed"
    assert "token@" not in str(exc.value)


async def test_manual_or_skip_uses_token_aware_pull(monkeypatch, log_manager):
    import mcp_server.services.git_diff_patcher as patcher

    calls: list[tuple[str, str | None, object | None]] = []

    async def pull_stub(local_path: str, github_token_enc: str | None, git_func=None) -> str:
        calls.append((local_path, github_token_enc, git_func))
        return ""

    monkeypatch.setattr(patcher, "pull_remote_ff_only", pull_stub)

    result = await patcher._manual_or_skip(
        "proj",
        "/tmp/repo",
        "b" * 40,
        {"action": "unknown"},
        ("skipped", "Only ignored/non-doc-impacting files changed"),
        log_manager,
        github_token_enc="enc-token",
    )

    assert result.advance_commit is True
    assert result.summary["outcome"] == "skip"
    assert result.summary["reason_code"] == "non_doc_change"
    assert calls == [("/tmp/repo", "enc-token", patcher._git)]


def test_agent_confidence_infers_confidence_for_valid_edits(monkeypatch):
    import mcp_server.services.git_diff_patcher as patcher
    from mcp_server.config import config

    monkeypatch.setattr(config, "git_diff_patch_min_confidence", 0.7)

    data = {
        "edits": [
            {
                "filename": "08_CHANGE_PLAYBOOK.md",
                "heading": "Design Pattern Catalog",
                "replacement_markdown": "### Design Pattern Catalog\n\nUpdated.",
            }
        ]
    }

    assert patcher._agent_confidence(data) == 0.85
    assert patcher._summarize_agent_output(
        data,
        patcher.DiffChunk(index=1, paths=["src/file.cs"], text="+change"),
    )["confidence_inferred"] is True


def test_agent_confidence_respects_explicit_low_confidence():
    import mcp_server.services.git_diff_patcher as patcher

    assert patcher._agent_confidence({"edits": [{"filename": "x"}], "confidence": 0.2}) == 0.2


def test_replacement_markdown_rejects_truncated_inline_code():
    import mcp_server.services.git_diff_patcher as patcher

    original = "# Doc\n\n## Glossary\n\n| Term | Definition |\n|------|------------|\n"

    assert patcher._replacement_markdown_is_complete(
        original,
        "Glossary",
        "## Glossary\n\n| **BadgeFactory** | Caches `",
    ) is False


async def test_webhook_diff_update_skips_ignored_readme_change(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.services.git_diff_patcher as patcher
    from mcp_server.config import config
    from mcp_server.providers.custom.models import ModelResult

    monkeypatch.setattr(patcher, "async_session_factory", session_factory)
    monkeypatch.setattr(config, "project_root", tmp_path)

    calls: list[tuple[str, ...]] = []

    async def git_stub(local_path: str, *args: str) -> str:
        calls.append(args)
        if args[:2] == ("diff", "--name-status"):
            return "M\tREADME.md\n"
        return ""

    monkeypatch.setattr(patcher, "_git", git_stub)
    provider = AsyncMock()
    monkeypatch.setattr(patcher, "_patch_provider", lambda provider_type: provider)

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            "proj",
            str(tmp_path / "repo"),
            repo_url="https://github.com/example/repo.git",
            source_type="git",
        )
        await session.commit()

    ok = await patcher.run_webhook_diff_update(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        log_mgr=log_manager,
    )

    assert ok is True
    provider.complete.assert_not_awaited()
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.last_ingested_commit_sha == "b" * 40
        assert project.git_update_summary["action"] == "skipped"
        assert project.git_update_summary["outcome"] == "skip"
        assert project.git_update_summary["reason_code"] == "non_doc_change"


async def test_webhook_diff_update_patches_matched_doc_only(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.services.git_diff_patcher as patcher
    from mcp_server.config import config
    from mcp_server.providers.custom.models import ModelResult

    monkeypatch.setattr(patcher, "async_session_factory", session_factory)
    monkeypatch.setattr(config, "project_root", tmp_path)
    docs_dir = tmp_path / "output" / "proj" / "docs"
    docs_dir.mkdir(parents=True)
    doc = docs_dir / "04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"
    doc.write_text(
        "# Business Logic\n\n"
        "## PaymentService\n\n"
        "Old payment behavior.\n\n"
        "## Other Section\n\n"
        "Unchanged.\n",
        encoding="utf-8",
    )

    async def git_stub(local_path: str, *args: str) -> str:
        if args[:2] == ("diff", "--name-status"):
            return "M\tsrc/PaymentService.ts\n"
        if args and args[0] == "diff":
            return (
                "diff --git a/src/PaymentService.ts b/src/PaymentService.ts\n"
                "@@ -1,3 +1,4 @@\n"
                "+function validatePayment() { return true }\n"
            )
        return ""

    monkeypatch.setattr(patcher, "_git", git_stub)

    class ProviderStub:
        async def complete(self, prompt, context_files=None, options=None):
            return ModelResult(
                success=True,
                model_name="test-model",
                input_tokens=100,
                output_tokens=20,
                text=(
                    '{"change_summary":"payment validation changed",'
                    '"inspected_context":["src/PaymentService.ts hunk"],'
                    '"affected_docs":["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],'
                    '"edits":[{"filename":"04_BUSINESS_RULES_AND_TRANSFORMATIONS.md","heading":"PaymentService",'
                    '"replacement_markdown":"## PaymentService\\n\\nNew payment behavior."}],'
                    '"confidence":0.92}'
                ),
            )

    monkeypatch.setattr(patcher, "_patch_provider", lambda provider_type: ProviderStub())

    class RagStub:
        def __init__(self, log_manager=None):
            self.log_manager = log_manager

        async def push_docs(self, project_id, output_dir=None, filenames=None, docs=None):
            assert filenames == {"04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"}
            return {"pushed": 1, "errors": []}

    monkeypatch.setattr(patcher, "RagBridge", RagStub)

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            "proj",
            str(tmp_path / "repo"),
            repo_url="https://github.com/example/repo.git",
            source_type="git",
        )
        await session.commit()

    ok = await patcher.run_webhook_diff_update(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        log_mgr=log_manager,
    )

    assert ok is True
    assert "New payment behavior." in doc.read_text(encoding="utf-8")
    assert "Unchanged." in doc.read_text(encoding="utf-8")
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.last_ingested_commit_sha == "b" * 40
        assert project.git_update_summary["action"] == "patched"
        assert project.git_update_summary["outcome"] == "bounded_patch"
        assert project.git_update_summary["reason_code"] == "bounded_patch_succeeded"
        assert project.git_update_summary["patched_docs"] == ["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"]


async def test_webhook_diff_update_summarizes_large_diff_before_patching(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.services.git_diff_patcher as patcher
    from mcp_server.config import config
    from mcp_server.providers.custom.models import ModelResult

    monkeypatch.setattr(patcher, "async_session_factory", session_factory)
    monkeypatch.setattr(config, "project_root", tmp_path)
    monkeypatch.setattr(config, "git_diff_patch_max_chunks", 1)
    monkeypatch.setattr(config, "git_diff_patch_max_summary_chunks", 10)
    monkeypatch.setattr(config, "git_diff_patch_max_chars", 180)
    docs_dir = tmp_path / "output" / "proj" / "docs"
    docs_dir.mkdir(parents=True)
    doc = docs_dir / "04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"
    doc.write_text(
        "# Business Logic\n\n"
        "## PaymentService\n\n"
        "Old payment behavior for PaymentServiceA.\n",
        encoding="utf-8",
    )

    async def git_stub(local_path: str, *args: str) -> str:
        if args[:2] == ("diff", "--name-status"):
            return "M\tsrc/PaymentServiceA.ts\nM\tsrc/PaymentServiceB.ts\nM\tsrc/PaymentServiceC.ts\n"
        if args and args[0] == "diff":
            return (
                "diff --git a/src/PaymentServiceA.ts b/src/PaymentServiceA.ts\n"
                "@@ -1 +1 @@\n+export function validatePaymentA() { return true }\n"
                "diff --git a/src/PaymentServiceB.ts b/src/PaymentServiceB.ts\n"
                "@@ -1 +1 @@\n+export function validatePaymentB() { return true }\n"
                "diff --git a/src/PaymentServiceC.ts b/src/PaymentServiceC.ts\n"
                "@@ -1 +1 @@\n+export function validatePaymentC() { return true }\n"
            )
        return ""

    monkeypatch.setattr(patcher, "_git", git_stub)

    class ProviderStub:
        def __init__(self):
            self.summary_calls = 0

        async def complete(self, prompt, context_files=None, options=None):
            if "compressing a Git diff" in prompt:
                self.summary_calls += 1
                return ModelResult(
                    success=True,
                    model_name="test-model",
                    input_tokens=100,
                    output_tokens=30,
                    text="- PaymentServiceA, PaymentServiceB, and PaymentServiceC validation behavior changed.",
                )
            assert self.summary_calls >= 1
            return ModelResult(
                success=True,
                model_name="test-model",
                input_tokens=120,
                output_tokens=20,
                text=(
                    '{"change_summary":"payment validation changed",'
                    '"inspected_context":["summarized diff facts"],'
                    '"affected_docs":["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],'
                    '"edits":[{"filename":"04_BUSINESS_RULES_AND_TRANSFORMATIONS.md","heading":"PaymentService",'
                    '"replacement_markdown":"## PaymentService\\n\\nNew summarized payment behavior for PaymentServiceA, PaymentServiceB, and PaymentServiceC via validatePaymentA, validatePaymentB, and validatePaymentC."}],'
                    '"confidence":0.92}'
                ),
            )

    provider = ProviderStub()
    monkeypatch.setattr(patcher, "_patch_provider", lambda provider_type: provider)

    class RagStub:
        def __init__(self, log_manager=None):
            self.log_manager = log_manager

        async def push_docs(self, project_id, output_dir=None, filenames=None, docs=None):
            return {"pushed": 1, "errors": []}

    monkeypatch.setattr(patcher, "RagBridge", RagStub)

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            "proj",
            str(tmp_path / "repo"),
            repo_url="https://github.com/example/repo.git",
            source_type="git",
        )
        await session.commit()

    ok = await patcher.run_webhook_diff_update(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        log_mgr=log_manager,
    )

    assert ok is True
    assert provider.summary_calls >= 1
    assert "New summarized payment behavior" in doc.read_text(encoding="utf-8")
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.git_update_summary["action"] == "patched"
        assert project.git_update_summary["summarized_patch"] is True


async def test_webhook_diff_update_manual_review_when_no_doc_match(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.services.git_diff_patcher as patcher
    from mcp_server.config import config
    from mcp_server.providers.custom.models import ModelResult

    monkeypatch.setattr(patcher, "async_session_factory", session_factory)
    monkeypatch.setattr(config, "project_root", tmp_path)
    docs_dir = tmp_path / "output" / "proj" / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "04_BUSINESS_RULES_AND_TRANSFORMATIONS.md").write_text("# Business Logic\n\nNo matching terms.\n", encoding="utf-8")

    async def git_stub(local_path: str, *args: str) -> str:
        if args[:2] == ("diff", "--name-status"):
            return "M\tsrc/UnknownFeature.ts\n"
        if args and args[0] == "diff":
            return "+function unknownFeature() { return true }\n"
        return ""

    monkeypatch.setattr(patcher, "_git", git_stub)
    provider = AsyncMock()
    monkeypatch.setattr(patcher, "_patch_provider", lambda provider_type: provider)

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

    ok = await patcher.run_webhook_diff_update(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        log_mgr=log_manager,
    )

    assert ok is False
    provider.complete.assert_not_awaited()
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.last_ingested_commit_sha == "a" * 40
        assert project.git_update_summary["action"] == "manual_review"
        assert project.git_update_summary["outcome"] == "manual_review"
        assert project.git_update_summary["reason_code"] == "unmapped_diff"


async def test_webhook_diff_update_first_push_requires_manual_initial_ingestion(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.services.git_diff_patcher as patcher
    from mcp_server.config import config

    monkeypatch.setattr(patcher, "async_session_factory", session_factory)
    monkeypatch.setattr(config, "project_root", tmp_path)
    git_mock = AsyncMock(return_value="")
    monkeypatch.setattr(patcher, "_git", git_mock)

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            "proj",
            str(tmp_path / "repo"),
            repo_url="https://github.com/example/repo.git",
            source_type="git",
        )
        await session.commit()

    ok = await patcher.run_webhook_diff_update(
        project_id="proj",
        before_sha="0" * 40,
        after_sha="b" * 40,
        log_mgr=log_manager,
    )

    assert ok is False
    git_mock.assert_not_awaited()
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.last_ingested_commit_sha is None
        assert project.git_update_summary["action"] == "manual_review"
        assert project.git_update_summary["outcome"] == "manual_review"
        assert project.git_update_summary["reason_code"] == "missing_baseline"


async def test_webhook_diff_update_manual_review_when_too_many_docs_match(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.services.git_diff_patcher as patcher
    from mcp_server.config import config

    monkeypatch.setattr(patcher, "async_session_factory", session_factory)
    monkeypatch.setattr(config, "project_root", tmp_path)
    monkeypatch.setattr(config, "git_diff_patch_max_docs", 1)
    monkeypatch.setattr(config, "git_diff_patch_min_confidence", 0.1)
    docs_dir = tmp_path / "output" / "proj" / "docs"
    docs_dir.mkdir(parents=True)
    for name in ("03_DATA_FLOWS_AND_LINEAGE.md", "04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"):
        (docs_dir / name).write_text("# Doc\n\n## PaymentService\n\nExisting.\n", encoding="utf-8")

    async def git_stub(local_path: str, *args: str) -> str:
        if args[:2] == ("diff", "--name-status"):
            return "M\tsrc/PaymentService.ts\n"
        if args and args[0] == "diff":
            return "+function PaymentService() { return true }\n"
        return ""

    monkeypatch.setattr(patcher, "_git", git_stub)

    class ProviderStub:
        async def complete(self, prompt, context_files=None, options=None):
            return ModelResult(
                success=True,
                model_name="test-model",
                input_tokens=50,
                output_tokens=10,
                text='{"change_summary":"none","inspected_context":[],"affected_docs":[],"edits":[],"confidence":0.9}',
            )

    monkeypatch.setattr(patcher, "_patch_provider", lambda provider_type: ProviderStub())

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

    ok = await patcher.run_webhook_diff_update(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        log_mgr=log_manager,
    )

    assert ok is False
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.last_ingested_commit_sha == "a" * 40
        assert project.git_update_summary["action"] == "manual_review"
        assert "no doc edits" in project.git_update_summary["reason"]
        assert project.git_update_summary["matched_docs"] == ["03_DATA_FLOWS_AND_LINEAGE.md"]


def test_pattern_diff_prefers_pattern_architecture_and_onboarding_docs(tmp_path):
    import mcp_server.services.git_diff_patcher as patcher

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "08_CHANGE_PLAYBOOK.md").write_text(
        "# Change Playbook\n\n### Change Impact\n\nExisting change guidance.\n",
        encoding="utf-8",
    )
    (docs_dir / "01_ARCHITECTURE_MAP.md").write_text(
        "# Architecture\n\n### Topology\n\nExisting topology.\n",
        encoding="utf-8",
    )
    (docs_dir / "00_AGENT_BRIEF.md").write_text(
        "# Agent Brief\n\n## Known Traps\n\nExisting traps.\n",
        encoding="utf-8",
    )

    changes = [
        patcher.FileChange(
            status="A",
            path="QuestBoard/src/QuestBoard.Api/Patterns/Chain/TitleValidationHandler.cs",
        )
    ]
    preferred = patcher._preferred_docs_for_diff(
        changes,
        "+### 12. Chain of Responsibility (Behavioral)\n",
    )
    selected = patcher._select_doc_sections(
        [patcher.DocSection("01_ARCHITECTURE_MAP.md", "Topology", "Existing")],
        limit=3,
        preferred_docs=preferred,
        docs_dir=docs_dir,
    )

    assert [(section.filename, section.heading) for section in selected] == [
        ("08_CHANGE_PLAYBOOK.md", "Change Impact"),
        ("01_ARCHITECTURE_MAP.md", "Topology"),
        ("00_AGENT_BRIEF.md", "Known Traps"),
    ]


async def test_webhook_diff_update_invalid_patch_json_leaves_docs_unchanged(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.services.git_diff_patcher as patcher
    from mcp_server.config import config
    from mcp_server.providers.custom.models import ModelResult

    monkeypatch.setattr(patcher, "async_session_factory", session_factory)
    monkeypatch.setattr(config, "project_root", tmp_path)
    docs_dir = tmp_path / "output" / "proj" / "docs"
    docs_dir.mkdir(parents=True)
    doc = docs_dir / "04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"
    original = "# Business Logic\n\n## PaymentService\n\nOld behavior.\n"
    doc.write_text(original, encoding="utf-8")

    async def git_stub(local_path: str, *args: str) -> str:
        if args[:2] == ("diff", "--name-status"):
            return "M\tsrc/PaymentService.ts\n"
        if args and args[0] == "diff":
            return "+function PaymentService() { return true }\n"
        return ""

    monkeypatch.setattr(patcher, "_git", git_stub)

    class ProviderStub:
        async def complete(self, prompt, context_files=None, options=None):
            return ModelResult(
                success=True,
                model_name="test-model",
                input_tokens=50,
                output_tokens=5,
                text="{not valid json",
            )

    monkeypatch.setattr(patcher, "_patch_provider", lambda provider_type: ProviderStub())

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

    ok = await patcher.run_webhook_diff_update(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        log_mgr=log_manager,
    )

    assert ok is False
    assert doc.read_text(encoding="utf-8") == original
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.last_ingested_commit_sha == "a" * 40
        assert project.git_update_summary["action"] == "manual_review"


async def test_webhook_diff_update_rag_failure_prevents_commit_advancement(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.services.git_diff_patcher as patcher
    from mcp_server.config import config
    from mcp_server.providers.custom.models import ModelResult

    monkeypatch.setattr(patcher, "async_session_factory", session_factory)
    monkeypatch.setattr(config, "project_root", tmp_path)
    docs_dir = tmp_path / "output" / "proj" / "docs"
    docs_dir.mkdir(parents=True)
    doc = docs_dir / "04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"
    doc.write_text("# Business Logic\n\n## PaymentService\n\nOld behavior.\n", encoding="utf-8")

    async def git_stub(local_path: str, *args: str) -> str:
        if args[:2] == ("diff", "--name-status"):
            return "M\tsrc/PaymentService.ts\n"
        if args and args[0] == "diff":
            return "+function PaymentService() { return true }\n"
        return ""

    monkeypatch.setattr(patcher, "_git", git_stub)

    class ProviderStub:
        async def complete(self, prompt, context_files=None, options=None):
            return ModelResult(
                success=True,
                model_name="test-model",
                input_tokens=50,
                output_tokens=20,
                text=(
                    '{"change_summary":"payment changed","inspected_context":["diff"],'
                    '"affected_docs":["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],'
                    '"edits":[{"filename":"04_BUSINESS_RULES_AND_TRANSFORMATIONS.md","heading":"PaymentService",'
                    '"replacement_markdown":"## PaymentService\\n\\nNew behavior."}],'
                    '"confidence":0.95}'
                ),
            )

    monkeypatch.setattr(patcher, "_patch_provider", lambda provider_type: ProviderStub())

    class FailingRagStub:
        def __init__(self, log_manager=None):
            self.log_manager = log_manager

        async def push_docs(self, project_id, output_dir=None, filenames=None, docs=None):
            return {"pushed": 0, "errors": ["rag unavailable"]}

    monkeypatch.setattr(patcher, "RagBridge", FailingRagStub)

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

    ok = await patcher.run_webhook_diff_update(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        log_mgr=log_manager,
    )

    assert ok is False
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.last_ingested_commit_sha == "a" * 40
        assert project.git_update_summary["action"] == "failed"
        assert project.git_update_summary["outcome"] == "failed"
        assert project.git_update_summary["reason_code"] == "rag_sync_failed"
        assert project.git_update_summary["rag_result"]["errors"] == ["rag unavailable"]


async def test_webhook_diff_update_zero_pushed_rag_result_prevents_commit_advancement(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.services.git_diff_patcher as patcher
    from mcp_server.config import config

    monkeypatch.setattr(patcher, "async_session_factory", session_factory)
    monkeypatch.setattr(config, "project_root", tmp_path)
    docs_dir = tmp_path / "output" / "proj" / "docs"
    docs_dir.mkdir(parents=True)
    doc = docs_dir / "04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"
    doc.write_text("# Business Logic\n\n## PaymentService\n\nOld behavior.\n", encoding="utf-8")

    async def git_stub(local_path: str, *args: str) -> str:
        if args[:2] == ("diff", "--name-status"):
            return "M\tsrc/PaymentService.ts\n"
        if args and args[0] == "diff":
            return "+function PaymentService() { return true }\n"
        return ""

    monkeypatch.setattr(patcher, "_git", git_stub)

    class ProviderStub:
        async def complete(self, prompt, context_files=None, options=None):
            return ModelResult(
                success=True,
                model_name="test-model",
                input_tokens=50,
                output_tokens=20,
                text=(
                    '{"change_summary":"payment changed","inspected_context":["diff"],'
                    '"affected_docs":["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],'
                    '"edits":[{"filename":"04_BUSINESS_RULES_AND_TRANSFORMATIONS.md","heading":"PaymentService",'
                    '"replacement_markdown":"## PaymentService\\n\\nNew behavior."}],'
                    '"confidence":0.95}'
                ),
            )

    monkeypatch.setattr(patcher, "_patch_provider", lambda provider_type: ProviderStub())

    class ZeroPushRagStub:
        def __init__(self, log_manager=None):
            self.log_manager = log_manager

        async def push_docs(self, project_id, output_dir=None, filenames=None, docs=None):
            return {"pushed": 0, "errors": []}

    monkeypatch.setattr(patcher, "RagBridge", ZeroPushRagStub)

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

    ok = await patcher.run_webhook_diff_update(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        log_mgr=log_manager,
    )

    assert ok is False
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.last_ingested_commit_sha == "a" * 40
        assert project.git_update_summary["outcome"] == "failed"
        assert project.git_update_summary["reason_code"] == "rag_sync_failed"


async def test_webhook_diff_update_manual_review_when_output_cap_hit_before_obligation(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.services.git_diff_patcher as patcher
    from mcp_server.config import config

    monkeypatch.setattr(patcher, "async_session_factory", session_factory)
    monkeypatch.setattr(config, "project_root", tmp_path)
    monkeypatch.setattr(config, "git_diff_patch_output_tokens_small", 8000)
    docs_dir = tmp_path / "output" / "proj" / "docs"
    docs_dir.mkdir(parents=True)
    doc = docs_dir / "04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"
    original = "# Business Logic\n\n## InvoiceWorkflow\n\nOld behavior.\n"
    doc.write_text(original, encoding="utf-8")

    async def git_stub(local_path: str, *args: str) -> str:
        if args[:2] == ("diff", "--name-status"):
            return "A\tsrc/InvoiceWorkflow.ts\n"
        if args and args[0] == "diff":
            return (
                "diff --git a/src/InvoiceWorkflow.ts b/src/InvoiceWorkflow.ts\n"
                "@@ -0,0 +1,2 @@\n"
                "+export class InvoiceWorkflow {}\n"
            )
        return ""

    monkeypatch.setattr(patcher, "_git", git_stub)

    class ProviderStub:
        async def complete(self, prompt, context_files=None, options=None):
            return ModelResult(
                success=True,
                model_name="test-model",
                input_tokens=50,
                output_tokens=8000,
                text=(
                    '{"change_summary":"partial","inspected_context":["diff"],'
                    '"affected_docs":["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],'
                    '"edits":[{"filename":"04_BUSINESS_RULES_AND_TRANSFORMATIONS.md","heading":"InvoiceWorkflow",'
                    '"replacement_markdown":"## InvoiceWorkflow\\n\\nPartial new behavior."}],'
                    '"confidence":0.95}'
                ),
            )

    monkeypatch.setattr(patcher, "_patch_provider", lambda provider_type: ProviderStub())

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

    ok = await patcher.run_webhook_diff_update(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        log_mgr=log_manager,
    )

    assert ok is False
    assert doc.read_text(encoding="utf-8") == original
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.last_ingested_commit_sha == "a" * 40
        assert project.git_update_summary["action"] == "manual_review"
        assert "output cap" in project.git_update_summary["reason"]


async def test_webhook_diff_update_rolls_back_when_stale_count_remains(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.services.git_diff_patcher as patcher
    from mcp_server.config import config

    monkeypatch.setattr(patcher, "async_session_factory", session_factory)
    monkeypatch.setattr(config, "project_root", tmp_path)
    docs_dir = tmp_path / "output" / "proj" / "docs"
    docs_dir.mkdir(parents=True)
    doc_a = docs_dir / "04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"
    doc_b = docs_dir / "00_AGENT_BRIEF.md"
    original_a = "# Business Logic\n\n## Feature Widgets\n\nThe system supports 3 widgets.\n"
    original_b = "# FAQ\n\n## Feature Widgets\n\nThe system supports 3 widgets.\n"
    doc_a.write_text(original_a, encoding="utf-8")
    doc_b.write_text(original_b, encoding="utf-8")

    async def git_stub(local_path: str, *args: str) -> str:
        if args[:2] == ("diff", "--name-status"):
            return "M\tsrc/Feature.ts\n"
        if args and args[0] == "diff":
            return (
                "diff --git a/src/Feature.ts b/src/Feature.ts\n"
                "@@ -1,3 +1,4 @@\n"
                "-The system supports 3 widgets.\n"
                "+The system supports 4 widgets.\n"
                "+export class Feature {}\n"
            )
        return ""

    monkeypatch.setattr(patcher, "_git", git_stub)

    class ProviderStub:
        async def complete(self, prompt, context_files=None, options=None):
            if "04_BUSINESS_RULES_AND_TRANSFORMATIONS.md" in prompt:
                return ModelResult(
                    success=True,
                    model_name="test-model",
                    input_tokens=50,
                    output_tokens=20,
                    text=(
                        '{"change_summary":"widget count changed","inspected_context":["diff"],'
                        '"affected_docs":["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],'
                        '"obligations_satisfied":["obl-01"],"obligations_unsatisfied":[],'
                        '"edits":[{"filename":"04_BUSINESS_RULES_AND_TRANSFORMATIONS.md","heading":"Feature Widgets",'
                        '"replacement_markdown":"## Feature Widgets\\n\\nThe system supports 4 widgets."}],'
                        '"confidence":0.95}'
                    ),
                )
            return ModelResult(
                success=True,
                model_name="test-model",
                input_tokens=50,
                output_tokens=10,
                text=(
                    '{"change_summary":"none","inspected_context":["diff"],'
                    '"affected_docs":[],"obligations_satisfied":[],"obligations_unsatisfied":[],'
                    '"edits":[],"confidence":0.95}'
                ),
            )

    monkeypatch.setattr(patcher, "_patch_provider", lambda provider_type: ProviderStub())

    class RagStub:
        async def push_docs(self, project_id, output_dir=None, filenames=None, docs=None):
            raise AssertionError("RAG should not be called when validation fails")

    monkeypatch.setattr(patcher, "RagBridge", lambda log_manager=None: RagStub())

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

    ok = await patcher.run_webhook_diff_update(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        log_mgr=log_manager,
    )

    assert ok is False
    assert doc_a.read_text(encoding="utf-8") == original_a
    assert doc_b.read_text(encoding="utf-8") == original_b
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("proj")
        assert project is not None
        assert project.last_ingested_commit_sha == "a" * 40
        assert project.git_update_summary["action"] == "manual_review"
        assert "Stale numeric claim" in "; ".join(project.git_update_summary["validation_errors"])


# ---------------------------------------------------------------------------
# _is_ignored_path — new infra/build patterns (Slice 0)
# ---------------------------------------------------------------------------

import pytest as _pytest


@_pytest.mark.parametrize(
    "path",
    [
        # pre-existing patterns still work
        ".github/workflows/ci.yml",
        "README.md",
        "package-lock.json",
        "tests/unit/test_foo.py",
        "src/module.test.ts",
        "CHANGELOG.txt",
        # new infra / build patterns
        "Dockerfile",
        "path/to/Dockerfile",
        "Dockerfile.prod",
        "docker-compose.yml",
        "docker-compose.override.yaml",
        "deploy/docker-compose.staging.yml",
        ".env",
        ".env.production",
        ".env.production.template",
        "config/.env.local",
        "Makefile",
        "pyproject.toml",
        "setup.toml",
        "build.sh",
        "scripts/deploy.sh",
    ],
)
def test_is_ignored_path_returns_true_for_infra(path: str) -> None:
    import mcp_server.services.git_diff_patcher as patcher

    assert patcher._is_ignored_path(path), f"Expected {path!r} to be ignored"


@_pytest.mark.parametrize(
    "path",
    [
        "jobs/etl/job.xml",
        "connections/db.properties",
        "src/module.py",
        "talend/src/jobs/GroupA/transform.xml",
        "workato/recipes/recipe.json",
    ],
)
def test_is_ignored_path_returns_false_for_integration_source(path: str) -> None:
    import mcp_server.services.git_diff_patcher as patcher

    assert not patcher._is_ignored_path(path), f"Expected {path!r} NOT to be ignored"
