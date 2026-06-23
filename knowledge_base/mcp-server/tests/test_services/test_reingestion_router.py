"""Tests for the deterministic + hybrid reingestion router (Slice 3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.config import config
from mcp_server.services.git_diff_patcher import FileChange
from mcp_server.services.quick_change_scanner import QuickChangeScanner
from mcp_server.services.reingestion_router import ReingestionRouter, RoutingDecision


def _manifest(tmp_path: Path, project_id: str, doc_packs: dict) -> Path:
    output_dir = tmp_path / "output" / project_id
    support = output_dir / "docs" / "_rag_support"
    support.mkdir(parents=True, exist_ok=True)
    (support / "_doc_pack_manifest.json").write_text(
        json.dumps({"doc_packs": doc_packs}), encoding="utf-8"
    )
    return output_dir


def _scan_group(tmp_path: Path, project_id: str, group: str) -> None:
    (tmp_path / "intodoc-output" / project_id / "scan" / "jobs" / group).mkdir(
        parents=True, exist_ok=True
    )


def _quick_scan(changes: list[FileChange], diff: str):
    return QuickChangeScanner().scan(changes, diff)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "project_root", tmp_path)
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc-output")
    monkeypatch.setattr(config, "reingestion_llm_expand_enabled", False)


def test_no_doc_relevant_change_routes_to_skip(tmp_path: Path) -> None:
    changes = [FileChange(status="M", path=".github/workflows/ci.yml")]
    quick = _quick_scan(changes, "diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml\n+jobs:\n")
    router = ReingestionRouter()

    decision = router.decide(
        project_id="proj",
        changed_files=changes,
        quick_scan=quick,
        output_dir=tmp_path / "output" / "proj",
    )

    assert decision.strategy == "skip"


def test_added_file_routes_to_scoped_regen_with_exact_doc_set(tmp_path: Path) -> None:
    output_dir = _manifest(
        tmp_path,
        "proj",
        {
            "01_ARCH": {"included": {"jobs.md": ["GROUP_SUMMARY"]}, "included_obligations": []},
            "02_INV": {"included": {"jobs.md": ["GROUP_SUMMARY"]}, "included_obligations": [{"category": "canonical_inventory"}]},
            "03_OTHER": {"included": {"config.md": ["GROUP_SUMMARY"]}, "included_obligations": []},
        },
    )
    _scan_group(tmp_path, "proj", "jobs")
    changes = [FileChange(status="A", path="jobs/new_module/x.py")]
    quick = _quick_scan(changes, "diff --git a/jobs/new_module/x.py b/jobs/new_module/x.py\n+class X: pass\n")
    router = ReingestionRouter()

    decision = router.decide(
        project_id="proj", changed_files=changes, quick_scan=quick, output_dir=output_dir
    )

    assert decision.strategy == "scoped_regen"
    assert decision.structural is True
    # jobs maps to 01_ARCH, 02_INV; 02_INV is also cross-cutting (always included).
    assert decision.affected_docs == ["01_ARCH", "02_INV"]


def test_in_file_edit_small_set_routes_to_bounded_patch(tmp_path: Path) -> None:
    output_dir = _manifest(
        tmp_path,
        "proj",
        {"04_RULES": {"included": {"jobs.md": ["GROUP_SUMMARY"]}, "included_obligations": []}},
    )
    _scan_group(tmp_path, "proj", "jobs")
    changes = [FileChange(status="M", path="jobs/payments/svc.py")]
    quick = _quick_scan(changes, "diff --git a/jobs/payments/svc.py b/jobs/payments/svc.py\n@@\n-x = 1\n+x = 2\n")
    router = ReingestionRouter()

    decision = router.decide(
        project_id="proj", changed_files=changes, quick_scan=quick, output_dir=output_dir
    )

    assert decision.strategy == "bounded_patch"
    assert decision.structural is False
    assert decision.affected_docs == ["04_RULES"]


def test_in_file_edit_wide_set_routes_to_scoped_regen(tmp_path: Path) -> None:
    # jobs feeds more docs than git_diff_patch_max_docs but under the full-regen
    # threshold -> scoped_regen for an in-file edit.
    docs = {f"D{i}": {"included": {"jobs.md": ["GROUP_SUMMARY"]}, "included_obligations": []} for i in range(5)}
    output_dir = _manifest(tmp_path, "proj", docs)
    _scan_group(tmp_path, "proj", "jobs")
    changes = [FileChange(status="M", path="jobs/payments/svc.py")]
    quick = _quick_scan(changes, "diff --git a/jobs/payments/svc.py b/jobs/payments/svc.py\n@@\n-x = 1\n+x = 2\n")
    router = ReingestionRouter(full_regen_doc_threshold=8, max_patch_docs=3)

    decision = router.decide(
        project_id="proj", changed_files=changes, quick_scan=quick, output_dir=output_dir
    )

    assert decision.strategy == "scoped_regen"
    assert len(decision.affected_docs) == 5


def test_breadth_over_threshold_routes_to_full_regen(tmp_path: Path) -> None:
    docs = {f"D{i}": {"included": {"jobs.md": ["GROUP_SUMMARY"]}, "included_obligations": []} for i in range(10)}
    output_dir = _manifest(tmp_path, "proj", docs)
    _scan_group(tmp_path, "proj", "jobs")
    changes = [FileChange(status="A", path="jobs/new/x.py")]
    quick = _quick_scan(changes, "diff --git a/jobs/new/x.py b/jobs/new/x.py\n+class X: pass\n")
    router = ReingestionRouter(full_regen_doc_threshold=8)

    decision = router.decide(
        project_id="proj", changed_files=changes, quick_scan=quick, output_dir=output_dir
    )

    assert decision.strategy == "full_regen"


def test_missing_manifest_routes_to_full_regen(tmp_path: Path) -> None:
    _scan_group(tmp_path, "proj", "jobs")
    output_dir = tmp_path / "output" / "proj"  # no manifest written
    changes = [FileChange(status="A", path="jobs/new/x.py")]
    quick = _quick_scan(changes, "diff --git a/jobs/new/x.py b/jobs/new/x.py\n+class X: pass\n")
    router = ReingestionRouter()

    decision = router.decide(
        project_id="proj", changed_files=changes, quick_scan=quick, output_dir=output_dir
    )

    assert decision.strategy == "full_regen"
    assert decision.manifest_present is False


def test_unresolved_groups_routes_to_full_regen(tmp_path: Path) -> None:
    output_dir = _manifest(
        tmp_path, "proj", {"01_ARCH": {"included": {"jobs.md": ["GROUP_SUMMARY"]}}}
    )
    _scan_group(tmp_path, "proj", "jobs")
    # File path does not contain a known scan group -> groups resolve to all,
    # but here we craft a change whose path matches no group folder. Because the
    # resolver returns all scan groups when nothing matches, this maps to docs;
    # to test the unresolved path we use a project with no scan jobs dir.
    other_dir = tmp_path / "output" / "noscan"
    (other_dir / "docs" / "_rag_support").mkdir(parents=True)
    (other_dir / "docs" / "_rag_support" / "_doc_pack_manifest.json").write_text(
        json.dumps({"doc_packs": {"01": {"included": {"g.md": []}}}}), encoding="utf-8"
    )
    changes = [FileChange(status="M", path="src/x.py")]
    quick = _quick_scan(changes, "diff --git a/src/x.py b/src/x.py\n@@\n-a\n+b\n")
    router = ReingestionRouter()

    decision = router.decide(
        project_id="noscan", changed_files=changes, quick_scan=quick, output_dir=other_dir
    )

    assert decision.strategy == "full_regen"


async def test_expand_disabled_is_identical_to_floor(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "reingestion_llm_expand_enabled", False)
    output_dir = _manifest(
        tmp_path,
        "proj",
        {"01_ARCH": {"included": {"jobs.md": ["GROUP_SUMMARY"]}, "included_obligations": []}},
    )
    _scan_group(tmp_path, "proj", "jobs")
    changes = [FileChange(status="A", path="jobs/new/x.py")]
    quick = _quick_scan(changes, "diff --git a/jobs/new/x.py b/jobs/new/x.py\n+class X: pass\n")
    router = ReingestionRouter()

    floor = router.decide(
        project_id="proj", changed_files=changes, quick_scan=quick, output_dir=output_dir
    )
    expanded = await router.expand(
        floor,
        diff_chunks=[{"index": 0, "paths": ["jobs/new/x.py"], "chars": 20}],
        all_docs=["01_ARCH", "02_INV"],
        doc_generation_provider=None,
    )

    assert expanded == floor  # no model call, no change


async def test_expand_only_can_add_but_never_shrink(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "reingestion_llm_expand_enabled", True)
    router = ReingestionRouter(max_patch_docs=3)
    base = RoutingDecision(
        strategy="bounded_patch",
        affected_docs=["04_RULES"],
        changed_groups=["jobs"],
        structural=False,
        manifest_present=True,
        reason="floor",
    )

    async def fake_invoke(**kwargs):
        # The model tries to shrink to nothing and also add an in-catalog doc.
        return ["05_FLOWS", "99_NOT_IN_CATALOG"]

    monkeypatch.setattr(router, "_invoke_expander", fake_invoke)

    out = await router.expand(
        base,
        diff_chunks=[],
        all_docs=["04_RULES", "05_FLOWS", "06_OPS"],
        doc_generation_provider=None,
    )

    # Floor doc retained; in-catalog addition merged; out-of-catalog discarded.
    assert "04_RULES" in out.affected_docs
    assert "05_FLOWS" in out.affected_docs
    assert "99_NOT_IN_CATALOG" not in out.affected_docs
    assert out.expanded_docs == ["05_FLOWS"]


async def test_expand_only_failure_falls_open_to_floor(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "reingestion_llm_expand_enabled", True)
    router = ReingestionRouter()
    base = RoutingDecision(
        strategy="scoped_regen",
        affected_docs=["01_ARCH"],
        changed_groups=["jobs"],
        structural=True,
        manifest_present=True,
        reason="floor",
    )

    async def boom(**kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(router, "_invoke_expander", boom)

    out = await router.expand(
        base, diff_chunks=[], all_docs=["01_ARCH", "02_INV"], doc_generation_provider=None
    )

    assert out == base  # fail-open: deterministic floor preserved


async def test_expand_upgrades_bounded_to_scoped_when_set_outgrows_bound(monkeypatch) -> None:
    monkeypatch.setattr(config, "reingestion_llm_expand_enabled", True)
    router = ReingestionRouter(max_patch_docs=2)
    base = RoutingDecision(
        strategy="bounded_patch",
        affected_docs=["A", "B"],
        changed_groups=["jobs"],
        structural=False,
        manifest_present=True,
        reason="floor",
    )

    async def add_one(**kwargs):
        return ["C"]

    monkeypatch.setattr(router, "_invoke_expander", add_one)

    out = await router.expand(
        base, diff_chunks=[], all_docs=["A", "B", "C"], doc_generation_provider=None
    )

    assert out.strategy == "scoped_regen"  # upgraded, never downgraded
    assert sorted(out.affected_docs) == ["A", "B", "C"]
