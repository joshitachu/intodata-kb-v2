"""Scripted Git fixtures for the OpenCode living-document patch lane."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_server.providers.base import AgentResult
from mcp_server.services.git_diff_patcher import parse_name_status
from mcp_server.services.living_docs.baseline import prime_project_baseline
from mcp_server.services.living_docs.candidate_publisher import CandidatePublisher
from mcp_server.services.living_docs.candidate_store import FileCandidateStore
from mcp_server.services.living_docs.git_patch_coordinator import GitPatchCoordinator
from mcp_server.services.living_docs.opencode_reingestion import (
    OpenCodeReingestionExecutor,
    _new_secret_leak_findings,
)
from mcp_server.services.living_docs.rag_revisions import InMemoryRevisionedRagStore
from mcp_server.services.rag_bridge import RagBridge


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def test_existing_placeholder_is_allowed_but_new_secret_is_rejected() -> None:
    baseline = "API key example: `SIMMER_API_KEY=sk_live_your_key_here`\n"
    unchanged = baseline + "Updated runtime text.\n"
    introduced = baseline + "api_key=sk_live_ABCDEFGHIJKLMNOP\n"

    assert _new_secret_leak_findings(unchanged, baseline) == []
    assert _new_secret_leak_findings(introduced, baseline)


def _fixture_repo(root: Path) -> tuple[Path, dict[str, str]]:
    repo = root / "fixture-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixture@example.com")
    _git(repo, "config", "user.name", "Fixture")
    (repo / "weather-trader").mkdir()
    config_path = repo / "weather-trader" / "config.py"
    config_path.write_text("entry_threshold = 0.36\n", encoding="utf-8")
    worker_path = repo / "weather-trader" / "worker.py"
    worker_path.write_text("def run():\n    return 'sunny'\n", encoding="utf-8")
    shas = {"base": _commit(repo, "baseline")}

    config_path.write_text("entry_threshold = 0.37\n", encoding="utf-8")
    shas["small"] = _commit(repo, "raise threshold")

    worker_path.rename(repo / "weather-trader" / "forecast_worker.py")
    shas["rename"] = _commit(repo, "rename worker")

    config_path.unlink()
    shas["delete"] = _commit(repo, "remove threshold config")

    (repo / "weather-trader" / "alerts.py").write_text(
        "def alert():\n    return True\n", encoding="utf-8"
    )
    shas["add"] = _commit(repo, "add alerts")
    return repo, shas


def _sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class _ReportRuntime:
    def __init__(self, report_factory) -> None:
        self._report_factory = report_factory
        self.calls = 0

    async def run(self, *, workspace: Path, contract, **kwargs) -> AgentResult:
        self.calls += 1
        report = self._report_factory(workspace)
        target = workspace / contract.output_artifact
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return AgentResult(
            success=True,
            input_tokens=120,
            output_tokens=45,
            cost_usd=0.01,
            duration_ms=25,
            session_id=f"session-{self.calls}",
            model_name="fake/opencode",
        )


def _report(workspace: Path, operations: list[dict], *, summary: str) -> dict:
    task = json.loads((workspace / "_reingestion" / "task.json").read_text())
    return {
        "schema_version": 1,
        "candidate_id": task["candidate_id"],
        "before_sha": task["before_sha"],
        "after_sha": task["after_sha"],
        "files_read": ["_reingestion/git.diff", "docs/01_ARCHITECTURE.md"],
        "summary": summary,
        "operations": operations,
    }


def _change(repo: Path, before: str, after: str):
    status = _git(repo, "diff", "--name-status", "-M", before, after)
    diff = _git(repo, "diff", "--unified=20", before, after)
    return parse_name_status(status), diff


async def _run_candidate(
    *,
    repo: Path,
    before: str,
    after: str,
    active_output: Path,
    store: FileCandidateStore,
    rag: InMemoryRevisionedRagStore,
    runtime: _ReportRuntime,
):
    changes, diff = _change(repo, before, after)
    bridge = RagBridge(revisioned_rag=rag)
    coordinator = GitPatchCoordinator(
        store=store,
        publisher=CandidatePublisher(store=store, rag=bridge),
        executor=OpenCodeReingestionExecutor(runtime=runtime),
    )
    candidate = coordinator.create_agent_candidate(
        project_id="poly-fixture",
        source_identity=str(repo),
        before_sha=before,
        after_sha=after,
        changed_files=changes,
        base_knowledge_revision_id=await bridge.active_knowledge_revision("poly-fixture"),
    )
    outcome = await coordinator.run(
        candidate,
        active_output_dir=active_output,
        candidate_scan_output_dir=active_output / "unused-scan",
        changed_groups=[],
        platform="generic",
        log_mgr=MagicMock(emit=AsyncMock()),
        before_sha=before,
        after_sha=after,
        active_source_revision_key=candidate.change_set.source_revision.key,
        changed_files=changes,
        full_diff_text=diff,
        scan_before_patch=False,
    )
    return coordinator, candidate, outcome


@pytest.mark.asyncio
async def test_scripted_small_push_patches_in_place_and_replay_skips_opencode(tmp_path: Path) -> None:
    repo, shas = _fixture_repo(tmp_path)
    active_output = tmp_path / "active"
    docs = active_output / "docs"
    docs.mkdir(parents=True)
    original_architecture = "# Architecture\n\nThreshold: 0.36\n"
    unchanged_operations = "# Operations\n\nRun the worker.\n"
    (docs / "01_ARCHITECTURE.md").write_text(original_architecture, encoding="utf-8")
    (docs / "02_OPERATIONS.md").write_text(unchanged_operations, encoding="utf-8")
    rag = InMemoryRevisionedRagStore()
    bridge = RagBridge(revisioned_rag=rag)
    baseline = await prime_project_baseline(
        project_id="poly-fixture",
        source_revision_id=shas["base"],
        docs_dir=docs,
        rag=bridge,
    )
    replacement = "# Architecture\n\nThreshold: 0.37\n"

    def report_factory(workspace: Path) -> dict:
        base = json.loads((workspace / "_reingestion" / "baseline_docs.json").read_text())
        return _report(
            workspace,
            [
                {
                    "path": "01_ARCHITECTURE.md",
                    "action": "update",
                    "base_sha256": base["01_ARCHITECTURE.md"],
                    "content": replacement,
                    "content_sha256": _sha(replacement),
                    "evidence_paths": ["weather-trader/config.py"],
                    "reason": "entry_threshold changed from 0.36 to 0.37",
                }
            ],
            summary="Updated the threshold statement only.",
        )

    runtime = _ReportRuntime(report_factory)
    store = FileCandidateStore((tmp_path / "candidate-store").resolve())
    coordinator, candidate, outcome = await _run_candidate(
        repo=repo,
        before=shas["base"],
        after=shas["small"],
        active_output=active_output,
        store=store,
        rag=rag,
        runtime=runtime,
    )

    assert outcome.action == "active"
    assert outcome.patched_docs == ("01_ARCHITECTURE.md",)
    assert (docs / "01_ARCHITECTURE.md").read_text() == replacement
    assert (docs / "02_OPERATIONS.md").read_text() == unchanged_operations
    assert outcome.publication is not None
    assert outcome.publication.changed_logical_ids == ("canonical/01_ARCHITECTURE.md",)
    assert outcome.publication.deleted_logical_ids == ()
    assert outcome.publication.base_knowledge_revision_id == baseline.knowledge_revision_id
    assert runtime.calls == 1

    replay = await coordinator.run(
        candidate,
        active_output_dir=active_output,
        candidate_scan_output_dir=active_output / "unused-scan",
        changed_groups=[],
        platform="generic",
        log_mgr=MagicMock(emit=AsyncMock()),
        before_sha=shas["base"],
        after_sha=shas["small"],
        active_source_revision_key=candidate.change_set.source_revision.key,
        changed_files=[],
        full_diff_text="must not be read",
        scan_before_patch=False,
    )
    assert replay.action == "active"
    assert replay.reason_code == "already_active"
    assert replay.patched_docs == ("01_ARCHITECTURE.md",)
    assert runtime.calls == 1


@pytest.mark.asyncio
async def test_scripted_rename_and_delete_leave_no_duplicate_or_orphan(tmp_path: Path) -> None:
    repo, shas = _fixture_repo(tmp_path)
    active_output = tmp_path / "active"
    docs = active_output / "docs"
    docs.mkdir(parents=True)
    architecture = "# Architecture\n\nWorker: worker.py\n"
    operations = "# Operations\n\nThreshold configuration is required.\n"
    (docs / "01_ARCHITECTURE.md").write_text(architecture, encoding="utf-8")
    (docs / "02_OPERATIONS.md").write_text(operations, encoding="utf-8")
    rag = InMemoryRevisionedRagStore()
    await prime_project_baseline(
        project_id="poly-fixture",
        source_revision_id=shas["small"],
        docs_dir=docs,
        rag=RagBridge(revisioned_rag=rag),
    )
    store = FileCandidateStore((tmp_path / "candidate-store").resolve())
    renamed = "# Architecture\n\nWorker: forecast_worker.py\n"

    def rename_report(workspace: Path) -> dict:
        base = json.loads((workspace / "_reingestion" / "baseline_docs.json").read_text())
        return _report(
            workspace,
            [{
                "path": "01_ARCHITECTURE.md", "action": "update",
                "base_sha256": base["01_ARCHITECTURE.md"], "content": renamed,
                "content_sha256": _sha(renamed),
                "evidence_paths": ["weather-trader/forecast_worker.py"],
                "reason": "source worker was renamed",
            }],
            summary="Replaced the old worker identity in place.",
        )

    _, _, rename_outcome = await _run_candidate(
        repo=repo, before=shas["small"], after=shas["rename"],
        active_output=active_output, store=store, rag=rag,
        runtime=_ReportRuntime(rename_report),
    )
    assert rename_outcome.action == "active"
    assert sorted(path.name for path in docs.glob("*.md")) == [
        "01_ARCHITECTURE.md", "02_OPERATIONS.md"
    ]
    assert "worker.py" not in (docs / "01_ARCHITECTURE.md").read_text().replace(
        "forecast_worker.py", ""
    )

    def delete_report(workspace: Path) -> dict:
        base = json.loads((workspace / "_reingestion" / "baseline_docs.json").read_text())
        return _report(
            workspace,
            [{
                "path": "02_OPERATIONS.md", "action": "delete",
                "base_sha256": base["02_OPERATIONS.md"], "content": None,
                "content_sha256": None,
                "evidence_paths": ["weather-trader/config.py"],
                "reason": "the only subject documented by this file was deleted",
            }],
            summary="Removed the obsolete operations document.",
        )

    _, _, delete_outcome = await _run_candidate(
        repo=repo, before=shas["rename"], after=shas["delete"],
        active_output=active_output, store=store, rag=rag,
        runtime=_ReportRuntime(delete_report),
    )
    assert delete_outcome.action == "active"
    assert delete_outcome.publication is not None
    assert delete_outcome.publication.deleted_logical_ids == ("canonical/02_OPERATIONS.md",)
    active = await rag.retrieve("poly-fixture")
    logical_ids = [item.artifact.logical_id for item in active]
    assert logical_ids == ["canonical/01_ARCHITECTURE.md"]
    assert len(logical_ids) == len(set(logical_ids))
    assert not (docs / "02_OPERATIONS.md").exists()


@pytest.mark.asyncio
async def test_unreported_or_overbroad_patch_is_held_before_publication(
    tmp_path: Path, monkeypatch
) -> None:
    repo, shas = _fixture_repo(tmp_path)
    active_output = tmp_path / "active"
    docs = active_output / "docs"
    docs.mkdir(parents=True)
    (docs / "01_ARCHITECTURE.md").write_text("# Architecture\n", encoding="utf-8")
    (docs / "02_OPERATIONS.md").write_text("# Operations\n", encoding="utf-8")
    rag = InMemoryRevisionedRagStore()
    await prime_project_baseline(
        project_id="poly-fixture", source_revision_id=shas["base"], docs_dir=docs,
        rag=RagBridge(revisioned_rag=rag),
    )
    monkeypatch.setattr("mcp_server.services.living_docs.opencode_reingestion.config.git_diff_patch_max_docs", 1)

    def broad_report(workspace: Path) -> dict:
        base = json.loads((workspace / "_reingestion" / "baseline_docs.json").read_text())
        operations = []
        for path in ("01_ARCHITECTURE.md", "02_OPERATIONS.md"):
            content = f"# Changed {path}\n"
            operations.append({
                "path": path, "action": "update", "base_sha256": base[path],
                "content": content, "content_sha256": _sha(content),
                "evidence_paths": ["weather-trader/config.py"], "reason": "too broad",
            })
        return _report(workspace, operations, summary="Attempted an overbroad patch.")

    _, _, outcome = await _run_candidate(
        repo=repo, before=shas["base"], after=shas["small"],
        active_output=active_output,
        store=FileCandidateStore((tmp_path / "candidate-store").resolve()),
        rag=rag,
        runtime=_ReportRuntime(broad_report),
    )
    assert outcome.action == "held"
    assert outcome.reason_code == "opencode_patch_validation_failed"
    assert (docs / "01_ARCHITECTURE.md").read_text() == "# Architecture\n"
    assert (docs / "02_OPERATIONS.md").read_text() == "# Operations\n"
