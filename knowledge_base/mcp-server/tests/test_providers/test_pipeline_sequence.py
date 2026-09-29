"""The run path and the cost estimate share one phase sequence."""

from __future__ import annotations

from mcp_server.providers.custom.pipeline.sequence import build_phases

RUN_ORDER = [
    "phase1_investigation",
    "phase2_analysis",
    "phase26_scorecard",
    "phase28_bundles",
    "evidence_quality_gate",
    "phase3_docs",
    "phase37_business_topology",
    "phase4_cleanup",
]


def test_sequence_is_the_documented_run_order() -> None:
    assert [phase.name for phase in build_phases(helpers=None)] == RUN_ORDER


def test_estimate_counts_every_phase_including_business_topology() -> None:
    estimates = {
        phase.name: phase.estimate(["group_a"], ["01_OVERVIEW"], 3)
        for phase in build_phases(helpers=None)
    }

    assert set(estimates) == set(RUN_ORDER)
    # Phase 3.7 always makes at least one model call; the old estimate omitted it.
    assert estimates["phase37_business_topology"].calls_min >= 1
    assert estimates["evidence_quality_gate"].calls_max == 0
