"""Tests for the deterministic + hybrid reingestion router (Slice 3).

Also covers the per-group delta classifier routing integration added in
Smart Reingestion Slice 3: classify_dirty_groups + lane partitioning.
"""

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


# ---------------------------------------------------------------------------
# Slice 3 -- per-group delta classifier routing integration
# Tests for classify_dirty_groups() + scoped/patch partition.
# ---------------------------------------------------------------------------


def _make_scan_jobs(tmp_path: Path, project_id: str, group: str, jobs: list[dict]) -> None:
    """Write synthetic scan JSON files for a group into the intodoc-output tree."""
    jobs_dir = tmp_path / "intodoc-output" / project_id / "scan" / "jobs" / group
    jobs_dir.mkdir(parents=True, exist_ok=True)
    for job in jobs:
        name = job.get("name", "job")
        (jobs_dir / f"{name}.json").write_text(
            json.dumps(job), encoding="utf-8"
        )


def _make_prev_scan(tmp_path: Path, project_id: str, group: str, jobs: list[dict]) -> None:
    """Write a prev_scan baseline JSON for a group."""
    prev_dir = tmp_path / "output" / project_id / "prev_scan"
    prev_dir.mkdir(parents=True, exist_ok=True)
    (prev_dir / f"{group}.json").write_text(
        json.dumps(jobs), encoding="utf-8"
    )


def _make_job(name: str, comp_type: str = "tFileInput", n_params: int = 5) -> dict:
    params = [
        {"name": f"p{i}", "value": f"v_{name}_{i}", "field_type": "TEXT"}
        for i in range(n_params)
    ]
    return {
        "name": name,
        "components": [
            {"unique_name": "comp_0", "component_type": comp_type, "params": params}
        ],
        "group": "grp",
    }


def test_classify_dirty_groups_no_prev_baseline_returns_new_unit(tmp_path: Path) -> None:
    """No prev_scan file -> NEW_UNIT -> routes to scoped_regen."""
    from mcp_server.services.git_reingestion import classify_dirty_groups

    jobs = [_make_job("job_A")]
    _make_scan_jobs(tmp_path, "proj", "grp_1", jobs)

    output_dir = tmp_path / "output" / "proj"
    result = classify_dirty_groups("proj", {"grp_1"}, output_dir)

    assert result["grp_1"].kind == "NEW_UNIT"
    assert result["grp_1"].changed_fraction == 0.0


def test_classify_dirty_groups_structural_change_routes_scoped(tmp_path: Path) -> None:
    """NEW_UNIT classification routes to scoped_regen, not patch lane."""
    from mcp_server.services.git_reingestion import classify_dirty_groups

    prev_jobs = [_make_job("job_A")]
    curr_jobs = [_make_job("job_A"), _make_job("job_B")]

    _make_prev_scan(tmp_path, "proj", "grp_1", prev_jobs)
    _make_scan_jobs(tmp_path, "proj", "grp_1", curr_jobs)

    output_dir = tmp_path / "output" / "proj"
    result = classify_dirty_groups("proj", {"grp_1"}, output_dir)

    assert result["grp_1"].kind == "NEW_UNIT"
    # Partition: scoped_groups includes grp_1, patch_groups is empty
    patch_groups = {
        g for g, cls in result.items()
        if cls.kind == "FIELD_EDIT" and cls.changed_fraction < 0.20
    }
    scoped_groups = {"grp_1"} - patch_groups
    assert "grp_1" in scoped_groups
    assert "grp_1" not in patch_groups


def test_classify_dirty_groups_small_field_edit_routes_patch(tmp_path: Path) -> None:
    """1 of 5 fields changed (0.20 fraction) -> at threshold -> scoped.
    Use 1 of 10 fields instead: fraction 0.10 < 0.20 -> patch lane."""
    import copy
    from mcp_server.services.git_reingestion import classify_dirty_groups

    # 2 jobs, 1 component, 5 params each = 10 fields total
    prev_jobs = [_make_job("job_A", n_params=5), _make_job("job_B", n_params=5)]
    curr_jobs = copy.deepcopy(prev_jobs)
    # Change 1 of 10 fields -> fraction = 0.10 < 0.20 -> patch
    curr_jobs[0]["components"][0]["params"][0]["value"] = "CHANGED"

    _make_prev_scan(tmp_path, "proj", "grp_1", prev_jobs)
    _make_scan_jobs(tmp_path, "proj", "grp_1", curr_jobs)

    output_dir = tmp_path / "output" / "proj"
    result = classify_dirty_groups("proj", {"grp_1"}, output_dir)

    cls = result["grp_1"]
    assert cls.kind == "FIELD_EDIT"
    assert cls.changed_fraction == pytest.approx(1 / 10)
    # Partition: grp_1 goes to patch lane
    patch_groups = {
        g for g, c in result.items()
        if c.kind == "FIELD_EDIT" and c.changed_fraction < 0.20
    }
    assert "grp_1" in patch_groups


def test_classify_dirty_groups_large_field_edit_routes_scoped(tmp_path: Path) -> None:
    """3 of 5 fields changed -> fraction 0.60 >= 0.20 -> scoped_regen."""
    import copy
    from mcp_server.services.git_reingestion import classify_dirty_groups

    prev_jobs = [_make_job("job_A", n_params=5)]
    curr_jobs = copy.deepcopy(prev_jobs)
    # Change 3 of 5 fields
    for i in range(3):
        curr_jobs[0]["components"][0]["params"][i]["value"] = f"CHANGED_{i}"

    _make_prev_scan(tmp_path, "proj", "grp_1", prev_jobs)
    _make_scan_jobs(tmp_path, "proj", "grp_1", curr_jobs)

    output_dir = tmp_path / "output" / "proj"
    result = classify_dirty_groups("proj", {"grp_1"}, output_dir)

    cls = result["grp_1"]
    assert cls.kind == "FIELD_EDIT"
    assert cls.changed_fraction >= 0.20
    # Not patch-eligible
    patch_groups = {
        g for g, c in result.items()
        if c.kind == "FIELD_EDIT" and c.changed_fraction < 0.20
    }
    assert "grp_1" not in patch_groups


def test_multi_group_mixed_routing(tmp_path: Path) -> None:
    """Mixed push: grp_structural (NEW_UNIT) -> scoped; grp_small (FIELD_EDIT 0.10) -> patch."""
    import copy
    from mcp_server.services.git_reingestion import classify_dirty_groups

    # grp_structural: add a job -> NEW_UNIT -> scoped
    prev_structural = [_make_job("job_A", n_params=5)]
    curr_structural = prev_structural + [_make_job("job_B", n_params=5)]
    _make_prev_scan(tmp_path, "proj", "grp_structural", prev_structural)
    _make_scan_jobs(tmp_path, "proj", "grp_structural", curr_structural)

    # grp_small: change 1 of 10 fields -> FIELD_EDIT 0.10 -> patch
    prev_small = [_make_job("job_X", n_params=5), _make_job("job_Y", n_params=5)]
    curr_small = copy.deepcopy(prev_small)
    curr_small[0]["components"][0]["params"][0]["value"] = "CHANGED"
    _make_prev_scan(tmp_path, "proj", "grp_small", prev_small)
    _make_scan_jobs(tmp_path, "proj", "grp_small", curr_small)

    output_dir = tmp_path / "output" / "proj"
    result = classify_dirty_groups("proj", {"grp_structural", "grp_small"}, output_dir)
    threshold = config.reingestion_patch_field_edit_threshold

    patch_groups = {
        g for g, c in result.items()
        if c.kind == "FIELD_EDIT" and c.changed_fraction < threshold
    }
    scoped_groups = {"grp_structural", "grp_small"} - patch_groups

    assert "grp_structural" in scoped_groups, "NEW_UNIT must route to scoped_regen"
    assert "grp_small" in patch_groups, "small FIELD_EDIT must route to patch lane"
    # Gate receives scoped subset (only grp_structural), not the full dirty set
    assert "grp_small" not in scoped_groups


# ---------------------------------------------------------------------------
# Living-Document Reingestion, Slice 1 -- impact-routing flag behaviour.
# With config.impact_routing_enabled the doc-count thresholds and the
# "path-resolution failed -> full" fallback are retired; the fingerprint
# dirty-set (inside run_git_reingestion) is the authoritative scope.
# ---------------------------------------------------------------------------


def _wide_docs(n: int) -> dict:
    return {
        f"D{i}": {"included": {"jobs.md": ["GROUP_SUMMARY"]}, "included_obligations": []}
        for i in range(n)
    }


def test_impact_mode_wide_in_file_edit_routes_bounded_not_full(tmp_path, monkeypatch) -> None:
    """12 affected docs (> default threshold 8), in-file edit: flag ON keeps it in
    the patch lane; the doc-count full-regen threshold is retired (REC-3)."""
    monkeypatch.setattr(config, "impact_routing_enabled", True)
    output_dir = _manifest(tmp_path, "proj", _wide_docs(12))
    _scan_group(tmp_path, "proj", "jobs")
    changes = [FileChange(status="M", path="jobs/payments/svc.py")]
    quick = _quick_scan(changes, "diff --git a/jobs/payments/svc.py b/jobs/payments/svc.py\n@@\n-x=1\n+x=2\n")

    decision = ReingestionRouter().decide(
        project_id="proj", changed_files=changes, quick_scan=quick, output_dir=output_dir
    )

    assert decision.strategy == "bounded_patch"
    assert len(decision.affected_docs) == 12


def test_flag_off_same_wide_edit_still_full_regen(tmp_path, monkeypatch) -> None:
    """Contrast: with the flag OFF the same 12-doc edit hits the legacy full-regen
    threshold. Guards flag-off byte-identical behaviour."""
    monkeypatch.setattr(config, "impact_routing_enabled", False)
    output_dir = _manifest(tmp_path, "proj", _wide_docs(12))
    _scan_group(tmp_path, "proj", "jobs")
    changes = [FileChange(status="M", path="jobs/payments/svc.py")]
    quick = _quick_scan(changes, "diff --git a/jobs/payments/svc.py b/jobs/payments/svc.py\n@@\n-x=1\n+x=2\n")

    decision = ReingestionRouter().decide(
        project_id="proj", changed_files=changes, quick_scan=quick, output_dir=output_dir
    )

    assert decision.strategy == "full_regen"


def test_impact_mode_structural_wide_set_routes_scoped_not_full(tmp_path, monkeypatch) -> None:
    """Structural change over a wide doc set: flag ON routes to scoped_regen (the
    threshold no longer forces full)."""
    monkeypatch.setattr(config, "impact_routing_enabled", True)
    output_dir = _manifest(tmp_path, "proj", _wide_docs(12))
    _scan_group(tmp_path, "proj", "jobs")
    changes = [FileChange(status="A", path="jobs/new/x.py")]
    quick = _quick_scan(changes, "diff --git a/jobs/new/x.py b/jobs/new/x.py\n+class X: pass\n")

    decision = ReingestionRouter().decide(
        project_id="proj", changed_files=changes, quick_scan=quick, output_dir=output_dir
    )

    assert decision.strategy == "scoped_regen"
    assert decision.structural is True


def test_impact_mode_path_unresolved_routes_scoped_not_full(tmp_path, monkeypatch) -> None:
    """A change that resolves to no scan group no longer forces full regen under the
    flag; scope is delegated to the fingerprint dirty-set downstream."""
    monkeypatch.setattr(config, "impact_routing_enabled", True)
    other_dir = tmp_path / "output" / "noscan"
    (other_dir / "docs" / "_rag_support").mkdir(parents=True)
    (other_dir / "docs" / "_rag_support" / "_doc_pack_manifest.json").write_text(
        json.dumps({"doc_packs": {"01": {"included": {"g.md": []}}}}), encoding="utf-8"
    )
    changes = [FileChange(status="M", path="src/x.py")]
    quick = _quick_scan(changes, "diff --git a/src/x.py b/src/x.py\n@@\n-a\n+b\n")

    decision = ReingestionRouter().decide(
        project_id="noscan", changed_files=changes, quick_scan=quick, output_dir=other_dir
    )

    assert decision.strategy == "scoped_regen"


def test_impact_mode_missing_manifest_still_full_regen(tmp_path, monkeypatch) -> None:
    """Missing provenance manifest is a reasoned full regen even under the flag."""
    monkeypatch.setattr(config, "impact_routing_enabled", True)
    _scan_group(tmp_path, "proj", "jobs")
    output_dir = tmp_path / "output" / "proj"  # no manifest
    changes = [FileChange(status="A", path="jobs/new/x.py")]
    quick = _quick_scan(changes, "diff --git a/jobs/new/x.py b/jobs/new/x.py\n+class X: pass\n")

    decision = ReingestionRouter().decide(
        project_id="proj", changed_files=changes, quick_scan=quick, output_dir=output_dir
    )

    assert decision.strategy == "full_regen"
    assert decision.manifest_present is False


def test_impact_mode_infra_only_still_skips(tmp_path, monkeypatch) -> None:
    """The skip gate survives impact routing."""
    monkeypatch.setattr(config, "impact_routing_enabled", True)
    changes = [FileChange(status="M", path=".github/workflows/ci.yml")]
    quick = _quick_scan(changes, "diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml\n+jobs:\n")

    decision = ReingestionRouter().decide(
        project_id="proj", changed_files=changes, quick_scan=quick,
        output_dir=tmp_path / "output" / "proj",
    )

    assert decision.strategy == "skip"
