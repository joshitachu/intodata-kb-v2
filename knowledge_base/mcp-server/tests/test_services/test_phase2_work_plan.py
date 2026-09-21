"""Phase 2 batching: how many workers, and which groups each one covers.

Measured problem (ETTM run 259): the orchestrator was told to spawn one
sub-agent per group and did -- 20 at once -- and the dispatcher ran 3-4 at a
time. Analysis files landed one or two a minute from 09:29:11 to 09:45:34, 5 of
20 groups were never analysed and Phase 3 never started. ``opencode run --help``
has no concurrency flag, so the fan-out itself has to be smaller.

Group-size spread differs per estate (86x on ETTM, ~5000x on talend8), so a
constant worker count is wrong at one end or the other. The rule derives the
count from the estate and bounds it with one attribution-safety constant; the
regression tests at the bottom run it against real staged scans.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server.config import config
from mcp_server.services.claude_service import (
    PHASE2_OBSERVED_SUBAGENT_CONCURRENCY,
    PHASE2_WORK_PLAN_FILENAME,
    DocGenerationCoordinator,
    GroupWorkItem,
    build_group_work_items,
    plan_phase2_workers,
    resolve_owning_group,
)

OUTPUT_ROOT = Path(__file__).resolve().parents[3] / "output"
ETTM = OUTPUT_ROOT / "ettm_fix" / "docs_staging" / "_preprocess"
TALEND8 = [
    OUTPUT_ROOT / "talend8" / f"part{index}" / "docs_staging" / "_preprocess"
    for index in (1, 2, 3, 4)
]


def _items(sizes: dict[str, int]) -> list[GroupWorkItem]:
    return [
        GroupWorkItem(name=name, evidence_files=(name,), evidence_bytes=size, design_count=1)
        for name, size in sizes.items()
    ]


def _write_groups(preprocess: Path, sizes: dict[str, int]) -> list[str]:
    preprocess.mkdir(parents=True, exist_ok=True)
    for name, size in sizes.items():
        (preprocess / f"{name}.md").write_text("x" * size, encoding="utf-8")
    return sorted(sizes)


def _real_items(dirs: list[Path]) -> list[GroupWorkItem]:
    merged: dict[str, GroupWorkItem] = {}
    for directory in dirs:
        slices = sorted(
            path.stem
            for path in directory.glob("*.md")
            if not path.name.startswith("_")
        )
        for item in build_group_work_items(directory, slices):
            merged[item.name] = item
    return list(merged.values())


# --------------------------------------------------------------------------
# 1. The rule
# --------------------------------------------------------------------------


def test_the_heaviest_group_sets_the_target_weight() -> None:
    """A group that size already has to be carried by one sub-agent, so that
    much work in one context is proven workable on this estate."""
    workers, target = plan_phase2_workers(_items({"a": 400, "b": 100, "c": 100}))
    assert target == 400
    # total 600 / target 400 -> 2 by weight; 3 groups / cap 6 -> 1 by safety.
    assert len(workers) == 2


def test_an_oversized_group_ends_up_alone() -> None:
    workers, _ = plan_phase2_workers(
        _items({"huge": 1_000_000, "a": 10, "b": 10, "c": 10})
    )
    assert ["huge"] in workers


def test_the_group_cap_binds_during_placement_not_only_in_the_count() -> None:
    """Measured: enforcing the cap only in the worker-count formula still let
    greedy placement dump 13 groups into one bin on talend8, because a bin that
    is light in BYTES attracts every remaining small group."""
    sizes = {"huge": 1_000_000}
    sizes.update({f"tiny{i}": 10 for i in range(12)})
    workers, _ = plan_phase2_workers(_items(sizes), max_groups_per_worker=4)
    assert max(len(w) for w in workers) <= 4
    assert sum(len(w) for w in workers) == len(sizes)


def test_leftovers_go_to_the_heavy_worker_rather_than_a_new_one(tmp_path: Path) -> None:
    """Once the light workers are at the group cap, the remaining small groups
    join the heavy worker instead of spawning another lane. That is correct:
    they add a rounding error in bytes, and a fifth worker would add a whole
    dispatch wave. ``by_safety`` guarantees the slots exist, so the planner's
    "every worker is at the cap, add one" branch is a guard that this count
    formula never reaches."""
    sizes = {"huge": 1_000_000}
    sizes.update({f"g{i}": 1 for i in range(11)})
    workers, _ = plan_phase2_workers(_items(sizes), max_groups_per_worker=4)

    assert sorted(len(w) for w in workers) == [4, 4, 4]
    heavy = next(w for w in workers if "huge" in w)
    assert len(heavy) == 4


def test_a_perfectly_uniform_estate_is_not_batched_at_all() -> None:
    """KNOWN HOLE, pinned rather than papered over.

    ``by_weight = ceil(total / biggest)`` equals the group count when every
    group is the same size, so a uniform estate gets one worker per group --
    the behaviour this change exists to replace. It is harmless when the groups
    are genuinely large (each worker is then carrying one target-weight unit of
    work, which is all a worker should carry), and wasteful when they are
    uniformly small: 20 groups of 5 KB become 20 workers and five dispatch
    waves to move 100 KB. No real estate measured so far is uniform (spreads
    run 12x to ~5000x), so this is reported, not worked around with a constant.
    """
    workers, target = plan_phase2_workers(_items({f"g{i}": 5_000 for i in range(20)}))
    assert target == 5_000
    assert len(workers) == 20
    assert max(len(w) for w in workers) == 1


def test_every_group_lands_exactly_once() -> None:
    sizes = {f"g{i}": (i + 1) * 37 for i in range(23)}
    workers, _ = plan_phase2_workers(_items(sizes))
    placed = [name for worker in workers for name in worker]
    assert sorted(placed) == sorted(sizes)
    assert len(placed) == len(set(placed))


def test_no_empty_workers_are_emitted() -> None:
    workers, _ = plan_phase2_workers(_items({"a": 10, "b": 10}))
    assert all(worker for worker in workers)


def test_the_plan_is_deterministic() -> None:
    sizes = {f"g{i}": (i * 7919) % 1000 for i in range(30)}
    first, _ = plan_phase2_workers(_items(sizes))
    second, _ = plan_phase2_workers(_items(dict(reversed(list(sizes.items())))))
    assert first == second


def test_an_empty_estate_plans_nothing() -> None:
    assert plan_phase2_workers([]) == ([], 0)


def test_the_cap_is_configurable_because_it_is_a_safety_bound(monkeypatch) -> None:
    """It limits how many groups' evidence sit in one context, and therefore how
    far a misattributed component can travel. Lowering it must tighten that."""
    sizes = {f"g{i}": 100 for i in range(12)}
    monkeypatch.setattr(config, "phase2_max_groups_per_worker", 2, raising=False)
    workers, _ = plan_phase2_workers(_items(sizes))
    assert max(len(w) for w in workers) <= 2


# --------------------------------------------------------------------------
# 2. Whole groups are the assignment unit
# --------------------------------------------------------------------------


def test_a_split_group_is_one_work_item_so_one_worker_merges_it(tmp_path: Path) -> None:
    """The two parts produce ONE ``_analysis/{group}.md``. A merge split across
    two workers is a merge nobody owns."""
    groups = _write_groups(tmp_path, {"VRI_part1": 100, "VRI_part2": 250, "Other": 50})
    items = build_group_work_items(tmp_path, groups)

    names = {item.name: item for item in items}
    assert set(names) == {"VRI", "Other"}
    assert names["VRI"].evidence_files == ("VRI_part1", "VRI_part2")
    assert names["VRI"].evidence_bytes == 350

    workers, _ = plan_phase2_workers(items)
    assert all(
        ("VRI" in worker) == (len(worker) > 0 and "VRI" in worker) for worker in workers
    )
    assert sum("VRI" in worker for worker in workers) == 1


def test_a_case_collision_slice_stays_its_own_work_item(tmp_path: Path) -> None:
    groups = _write_groups(tmp_path, {"Monitoring": 10, "monitoring_part_ci1": 20})
    items = build_group_work_items(tmp_path, groups)
    assert sorted(item.name for item in items) == ["Monitoring", "monitoring_part_ci1"]


def test_risk_flags_and_design_counts_come_from_the_quick_scan(tmp_path: Path) -> None:
    groups = _write_groups(tmp_path, {"A_part1": 10, "A_part2": 10})
    items = build_group_work_items(
        tmp_path,
        groups,
        findings={
            "A_part1": [("CRITICAL", "hardcoded_credential", "x")],
            "A_part2": [("HIGH", "god_profile", "y"), ("LOW", "no_context_vars", "z")],
        },
        design_counts={"A_part1": 3, "A_part2": 4},
    )
    assert len(items) == 1
    assert items[0].risk_counts == (1, 1, 0, 1)
    assert items[0].design_count == 7


# --------------------------------------------------------------------------
# 3. The staged file
# --------------------------------------------------------------------------


def _plan_for(tmp_path: Path, sizes: dict[str, int]):
    staging = tmp_path / "docs_staging"
    groups = _write_groups(staging / "_preprocess", sizes)
    return staging, DocGenerationCoordinator._write_phase2_work_plan(
        staging,
        groups,
        scan_dir=tmp_path / "missing_scan",
        findings={},
        design_counts={},
    )


def test_the_plan_is_staged_as_ascii_under_an_underscore_directory(
    tmp_path: Path,
) -> None:
    staging, plan = _plan_for(tmp_path, {"a": 400, "b": 100})
    path = staging / "_assessment" / PHASE2_WORK_PLAN_FILENAME
    text = path.read_text(encoding="utf-8")

    assert text.isascii(), "the staged plan must be ASCII (cp1252 hosts)"
    assert "## Suggested split" in text
    assert "_knowledge/decisions.md" in text, "the chosen split must be recorded"
    assert "one analysis file per group" in text
    for worker in plan.workers:
        for name in worker:
            assert name in text


def test_an_estate_without_reusable_designs_says_so_rather_than_inventing_a_pass(
    tmp_path: Path,
) -> None:
    """talend8 has no joblets or routelets at all. The pre-pass is skipped, and
    absence is stated instead of being silently missing."""
    staging, plan = _plan_for(tmp_path, {"a": 10})
    text = (staging / "_assessment" / PHASE2_WORK_PLAN_FILENAME).read_text()
    assert plan.reusable == ()
    assert "Shared-library pre-pass: not needed" in text


def test_a_missing_scan_manifest_does_not_fabricate_a_composition(
    tmp_path: Path,
) -> None:
    staging, plan = _plan_for(tmp_path, {"a": 10})
    text = (staging / "_assessment" / PHASE2_WORK_PLAN_FILENAME).read_text()
    assert plan.composition == ()
    assert "not available from this scan" in text


def test_a_resumed_run_says_the_risk_columns_were_not_recomputed(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "docs_staging"
    groups = _write_groups(staging / "_preprocess", {"a": 10})
    DocGenerationCoordinator._write_phase2_work_plan(
        staging, groups, scan_dir=tmp_path / "nope", findings=None, design_counts=None
    )
    text = (staging / "_assessment" / PHASE2_WORK_PLAN_FILENAME).read_text()
    assert "quick scan was not re-run" in text


@pytest.mark.parametrize(
    ("raw", "names", "expected"),
    [
        ("ETTM-2/ERP_TO_THE_MAX/fourps", ["fourps", "maximo"], "fourps"),
        ("ETTM-2/ERP_TO_THE_MAX/monitoring", ["monitoring_part_ci1"], "monitoring_part_ci1"),
        ("Global", ["Global"], "Global"),
        ("a/b/unknown", ["fourps"], "unknown"),
    ],
)
def test_the_scan_manifests_group_path_is_mapped_to_the_evidence_file(
    raw: str, names: list[str], expected: str
) -> None:
    """Pointing a worker at ``ETTM-2/ERP_TO_THE_MAX/fourps`` sends it after a
    file that does not exist; the evidence is ``fourps.md``."""
    assert resolve_owning_group(raw, names) == expected


# --------------------------------------------------------------------------
# 4. Regression against real staged estates
# --------------------------------------------------------------------------


def _waves(worker_count: int) -> int:
    lanes = PHASE2_OBSERVED_SUBAGENT_CONCURRENCY
    return -(-worker_count // lanes)


def _prompt(tmp_path: Path) -> str:
    from mcp_server.providers.base import DocGenContext
    from mcp_server.providers.claude_cli import ClaudeCliProvider

    staging = tmp_path / "docs_staging"
    (staging / "_preprocess").mkdir(parents=True, exist_ok=True)
    return ClaudeCliProvider._build_prompt(
        DocGenContext(
            project_id="p",
            staging_dir=staging,
            staging_abs=str(staging),
            logs_abs=str(tmp_path / "logs"),
            preprocess_groups=["a", "b"],
            total_jobs=142,
            total_groups=20,
            effective_model="m",
            write_model="m",
            platform="talend",
            analysis_guide_file="_analysis_guide.md",
            enterprise_mode=False,
        )
    )


def test_the_prompt_no_longer_asks_for_one_sub_agent_per_group(tmp_path: Path) -> None:
    """The instruction that caused the measured serialisation, and the file
    that replaces it. ``opencode_cli`` returns this prompt verbatim, so this
    covers both lanes."""
    prompt = _prompt(tmp_path)

    assert "Spawn one sub-agent per group" not in prompt
    assert "ONE sub-agent per WORKER" in prompt
    assert "Do not spawn one per group." in prompt
    assert "_assessment/phase2_work_plan.md" in prompt
    assert "_knowledge/decisions.md" in prompt
    # One analysis per GROUP, whatever a worker's batch looks like. Phase 2
    # writes it as a directory of section files (plan slice 2); the rule that
    # a batch never merges two groups into one analysis is unchanged.
    assert "ONE ANALYSIS DIRECTORY PER GROUP, ALWAYS." in prompt
    assert "Never\n   merge two groups into one directory" in prompt


def test_the_prompt_keeps_the_gate_per_group_not_per_worker(tmp_path: Path) -> None:
    """A worker that wrote three of its four files passes a per-worker check."""
    gate = _prompt(tmp_path).split("PHASE 2.5 -- GATE VALIDATION")[1].split("PHASE 2.6")[0]
    assert "PER GROUP, never per worker" in gate
    assert "not for\n        the whole batch" in gate
    assert "separate failures with" in gate


def test_the_prompt_stops_the_agent_deleting_its_own_evidence(tmp_path: Path) -> None:
    """The attribution check reads ``_analysis/`` and ``_preprocess/`` after the
    run, so a Phase 4 that deletes them makes the check unrunnable."""
    cleanup = _prompt(tmp_path).split("PHASE 4 -- CLEANUP")[1]
    assert "Do NOT delete" in cleanup
    assert "The server\n     removes both after the run" in cleanup


def test_the_coverage_obligation_says_what_the_design_count_contains(
    tmp_path: Path,
) -> None:
    """27 of ETTM's 142 designs are reusable. A writer told only the total
    either drops them silently or pads the table to reach the number."""
    prompt = _prompt(tmp_path)
    assert "WHAT THAT COUNT CONTAINS" in prompt
    assert "reusable ones that exist only to be called by another" in prompt
    assert "accounted FOR by naming it and what calls it" in prompt.replace(
        "ACCOUNTED FOR", "accounted FOR"
    )
    assert "Never pad a table with sub-components" in prompt
    assert "Copy_of_* duplicates and test-named artifacts are the canonical examples" in prompt


@pytest.mark.skipif(not ETTM.is_dir(), reason="ettm_fix artifacts absent")
def test_ettm_batches_20_groups_into_5_workers() -> None:
    items = _real_items([ETTM])
    workers, _ = plan_phase2_workers(items)

    assert len(items) == 20
    assert len(workers) == 5
    assert max(len(w) for w in workers) == 6
    assert _waves(len(workers)) == 2, "was 5 waves at one sub-agent per group"
    # The 562 KB group is the estate's critical path and gets a worker to itself.
    assert ["fourps"] in workers


@pytest.mark.skipif(
    not all(d.is_dir() for d in TALEND8), reason="talend8 artifacts absent"
)
def test_talend8_batches_24_groups_into_4_workers() -> None:
    """The high-spread end: ~5000x between the biggest and smallest group.
    Target-weight alone would concentrate 13 groups into one context here; the
    per-worker group cap is what stops it."""
    items = _real_items(TALEND8)
    workers, _ = plan_phase2_workers(items)

    assert len(items) == 24
    assert len(workers) == 4
    assert max(len(w) for w in workers) == 6
    assert _waves(len(workers)) == 1, "was 6 waves at one sub-agent per group"
