"""Adversarial coverage, source provenance, human edits and run durability."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mcp_server.services.living_docs.change_analysis import (
    ChangeAnalysis,
    Review,
    build_index,
    digest,
    sections,
    validate_analysis,
    validate_edits,
    validate_review,
)
from mcp_server.services.living_docs.run_journal import ProjectRunLock, RunJournal


def analysis():
    return ChangeAnalysis.model_validate(
        {
            "candidate_id": "candidate",
            "before_sha": "a",
            "after_sha": "b",
            "summary": "Retry count changed",
            "covered_paths": ["retry.py"],
            "findings": [
                {
                    "id": "retry",
                    "before": "One attempt",
                    "after": "Three attempts",
                    "intent": "Unknown",
                    "intent_basis": "unknown",
                    "evidence": [{"path": "retry.py", "version": "after", "quote": "attempts = 3"}],
                    "disposition": "update",
                    "reason": "New configured value",
                }
            ],
            "targets": [
                {
                    "path": "operations.md",
                    "sections": ["## Retry [1]"],
                    "finding_ids": ["retry"],
                    "reason": "Update retry value",
                }
            ],
            "unchanged_docs": {},
            "uncertainties": [],
        }
    )


def test_analysis_rejects_invented_evidence_missing_coverage_and_unresolved_questions():
    value = analysis()
    task = {
        "candidate_id": "candidate",
        "before_sha": "a",
        "after_sha": "b",
        "changed_files": [{"path": "retry.py"}],
        "suggested_docs": ["operations.md"],
    }
    bundle = {"files": {"retry.py": {"after": "attempts = 3\n"}}}
    catalog = {"operations.md": {"## Retry [1]": "hash"}}
    validate_analysis(value, task, bundle, catalog)
    for update in (
        {"covered_paths": []},
        {"uncertainties": ["Unknown downstream effect"]},
        {"targets": []},
    ):
        with pytest.raises(ValueError):
            validate_analysis(value.model_copy(update=update), task, bundle, catalog)
    value.findings[0].evidence[0].quote = "invented source claim"
    with pytest.raises(ValueError, match="Unsupported"):
        validate_analysis(value, task, bundle, catalog)
    value = analysis()
    value.findings[0].intent_basis = "commit_message"
    value.findings[0].intent_evidence = "Developer said this fixes outages"
    with pytest.raises(ValueError, match="intent"):
        validate_analysis(value, task, bundle, catalog)


def test_preserves_unrelated_sections_and_human_changes(tmp_path):
    old = "# Operations\nIntro\n## Retry\nOne attempt\n## Deploy\nHuman instructions\n"
    path = tmp_path / "operations.md"
    path.write_text(old)
    value = analysis()
    op = SimpleNamespace(
        path="operations.md", action="update", content=old.replace("One attempt", "Three attempts")
    )
    report = SimpleNamespace(operations=[op])
    validate_edits(value, report, tmp_path, {})
    op.content = op.content.replace("Human instructions", "Invented instructions")
    with pytest.raises(ValueError, match="Unrelated"):
        validate_edits(value, report, tmp_path, {})
    op.content = old.replace("One attempt", "Three attempts")
    with pytest.raises(ValueError, match="Human edit"):
        validate_edits(
            value,
            report,
            tmp_path,
            {"documents": {"operations.md": {"## Retry [1]": {"sha256": "old-generated-hash"}}}},
        )


def test_manual_blocks_cannot_be_changed_even_in_targeted_sections(tmp_path):
    old = "## Retry\n<!-- nexus:manual:start -->Owner: Alice<!-- nexus:manual:end -->\n"
    (tmp_path / "operations.md").write_text(old)
    report = SimpleNamespace(
        operations=[
            SimpleNamespace(
                path="operations.md", action="update", content=old.replace("Alice", "Bob")
            )
        ]
    )
    with pytest.raises(ValueError, match="manually maintained"):
        validate_edits(analysis(), report, tmp_path, {})


def test_review_is_bound_to_exact_outputs_and_requires_every_finding():
    value = analysis()
    review = Review(
        candidate_id="candidate",
        analysis_sha256=digest("analysis"),
        patch_sha256=digest("patch"),
        verdict="pass",
        finding_verdicts={"retry": "pass"},
        unsupported_claims=[],
        missed_impacts=[],
        contradictions=[],
        explanation="Checked source",
    )
    validate_review(review, value, "analysis", "patch")
    with pytest.raises(ValueError):
        validate_review(review, value, "analysis", "different patch")
    with pytest.raises(ValueError):
        validate_review(
            review.model_copy(update={"finding_verdicts": {}}), value, "analysis", "patch"
        )
    with pytest.raises(ValueError):
        validate_review(
            review.model_copy(update={"contradictions": ["Support doc still says one retry"]}),
            value,
            "analysis",
            "patch",
        )


def test_index_preserves_unrelated_manual_edit_detection(tmp_path):
    (tmp_path / "operations.md").write_text("## Retry\nThree\n## Deploy\nHuman change\n")
    previous = {
        "documents": {
            "operations.md": {"## Deploy [1]": {"sha256": "original", "sources": ["deploy.py"]}}
        }
    }
    result = build_index(tmp_path, analysis(), previous)
    assert result["documents"]["operations.md"]["## Deploy [1]"]["sha256"] == "original"
    assert result["documents"]["operations.md"]["## Retry [1]"]["sources"] == ["retry.py"]


def test_lock_excludes_other_workers_and_journal_includes_skips_and_holds(tmp_path):
    first, second = ProjectRunLock(tmp_path / "project"), ProjectRunLock(tmp_path / "project")
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()
    journal = RunJournal(tmp_path / "project")
    for state in ("skipped", "held", "true_patch"):
        run = journal.start("before", "after", "test")
        journal.finish(run, {"action": state, "reason": "Test reason"})
    assert len(list(journal.root.glob("*.json"))) == 3
    text = (journal.root.parent / "CHANGELOG.md").read_text()
    assert all(state in text for state in ("skipped", "held", "true_patch"))


def test_section_parser_ignores_fenced_headings():
    assert list(sections("## Real\n```python\n# comment\n```\n## Next\n")) == [
        "preamble",
        "## Real [1]",
        "## Next [1]",
    ]


@pytest.mark.asyncio
async def test_drift_audit_reports_human_edits_and_unmapped_sections_without_editing(tmp_path):
    from mcp_server.services.living_docs.drift_audit import audit_document_drift
    from mcp_server.services.living_docs.run_journal import atomic_json

    docs = tmp_path / "docs"
    docs.mkdir()
    original = "## Retry\nHuman update\n## New section\nNew explanation\n"
    (docs / "operations.md").write_text(original)
    atomic_json(
        tmp_path / "_version_history/evidence_index.json",
        {
            "source_revision": "abc",
            "documents": {
                "operations.md": {"## Retry [1]": {"sha256": "old hash", "sources": ["retry.py"]}}
            },
        },
    )
    result = await audit_document_drift(tmp_path, "unused", "abc", force=True)
    reasons = {f["reason"] for f in result["findings"]}
    assert "document_changed_outside_recorded_update" in reasons
    assert "source_evidence_not_yet_mapped" in reasons
    assert (docs / "operations.md").read_text() == original


@pytest.mark.asyncio
async def test_two_updates_serialize_and_second_reads_fresh_baseline(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    import mcp_server.services.auto_doc_update as mod
    from mcp_server.services.git_diff_patcher import DiffPatchResult

    monkeypatch.setattr(mod.config, "project_root", tmp_path)
    monkeypatch.setattr(mod.config, "data_dir", tmp_path / "data")
    monkeypatch.setattr(mod, "fetch_remote", AsyncMock())

    async def git(repo, *args):
        return args[1]  # merge-base is the current documented commit.

    monkeypatch.setattr(mod, "_git", git)
    baseline = "a" * 40
    started, release = asyncio.Event(), asyncio.Event()
    seen = []

    class Service(mod.AutoDocUpdateService):
        async def _load_context(self, project_id):
            return mod.AutoDocUpdateContext(project_id, "repo", None, None, baseline)

        async def _run(self, ctx, **kwargs):
            seen.append(kwargs["before_sha"])
            if len(seen) == 1:
                started.set()
                await release.wait()
            return DiffPatchResult(
                action="true_patch",
                summary={"action": "true_patch"},
                patched_docs=[],
                advance_commit=True,
            )

        async def _persist_result(self, project_id, after_sha, result):
            nonlocal baseline
            baseline = after_sha

    service = Service(log_mgr=SimpleNamespace(emit=AsyncMock()))
    first = asyncio.create_task(
        service.run(project_id="project", before_sha=baseline, after_sha="b" * 40)
    )
    await started.wait()
    second = asyncio.create_task(
        service.run(project_id="project", before_sha=baseline, after_sha="c" * 40)
    )
    await asyncio.sleep(0.01)
    assert seen == ["a" * 40]
    release.set()
    assert await first and await second
    assert seen == ["a" * 40, "b" * 40]
    assert baseline == "c" * 40
