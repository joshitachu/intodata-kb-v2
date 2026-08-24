"""Rendering tests for the Phase 3 runtime-evidence section.

The section is deterministic text handed straight to a doc-writing model, so the
properties that matter are the ones that would make a published document wrong:

* an ESB route must not be described with batch-job wording -- werkorder's
  `Maximo2Quintiq_Workorder_Route` really did run for 76 days in one execution,
  and "run duration: 76 days" reads as a defect rather than as uptime;
* a unit with no runtime data must produce an explicit no-data notice, so a
  writer cannot read silence as "this never runs";
* the output must stay ASCII (generated docs target Windows cp1252).
"""

import pytest

from mcp_server.providers.custom.runtime_evidence import (
    RuntimeEvidenceStore,
    RuntimeUnitEvidence,
)

_PAYLOAD = {
    "unit_name": "RHM2Smartflow_Workorders",
    "job_name": "RHM2Smartflow_Workorders",
    "source_key": "tmc",
    "resolved_by": "exact_name",
    "environments": ["ACC"],
    "total_runs": 1257,
    "success_runs": 492,
    "failed_runs": 764,
    "unfinished_runs": 1,
    "failure_rate": 0.6078,
    "current_failure_streak": 7,
    "first_run_at": "2026-06-11T08:30:20Z",
    "last_run_at": "2026-07-01T20:45:33Z",
    "runs_per_day": 61.29,
    "p50_duration_ms": 12160,
    "p95_duration_ms": 24358,
    "max_duration_ms": 49361,
    "plans": ["Nightly_Workorders"],
    "recent_failures": [
        {
            "failed_at": "2026-07-01T20:45:33Z",
            "error_summary": "Job stopped with code 1",
            "occurrences": 764,
        },
        {
            "failed_at": "2026-06-20T02:11:00Z",
            "error_summary": "Remote engine was stopped/restarted",
            "occurrences": 1,
        },
    ],
}


@pytest.fixture
def store() -> RuntimeEvidenceStore:
    return RuntimeEvidenceStore(
        window_days=90,
        by_unit={"RHM2Smartflow_Workorders": RuntimeUnitEvidence.from_payload(_PAYLOAD)},
        unmatched_units=("AssetCheck_NeverRan",),
    )


def test_batch_job_reports_run_duration(store):
    section = store.section_for("RHM2Smartflow_Workorders", max_bytes=8000)
    assert "Run duration (median) | 12.2 s" in section
    assert "Uptime per start" not in section
    assert "ESB route" not in section


def test_esb_route_is_described_as_uptime_not_run_duration(store):
    section = store.section_for(
        "RHM2Smartflow_Workorders", is_esb_route=True, max_bytes=8000
    )
    assert "Uptime per start (median) | 12.2 s" in section
    assert "Run duration" not in section
    assert "long-running listener" in section


def test_core_counts_are_rendered_verbatim(store):
    section = store.section_for("RHM2Smartflow_Workorders", max_bytes=8000)
    assert "| Runs observed | 1257 |" in section
    assert "| Succeeded | 492 |" in section
    assert "| Failed | 764 (60.8%) |" in section
    assert "| Never completed | 1 |" in section
    assert "| Consecutive failures (most recent) | 7 |" in section
    assert "| Environments | ACC |" in section
    assert "| Orchestration plans | Nightly_Workorders |" in section
    assert "do not recompute" in section


def test_latency_caveat_is_always_present(store):
    """A reader must know the percentiles exclude failed and hung runs."""
    section = store.section_for("RHM2Smartflow_Workorders", max_bytes=8000)
    assert "completed, successful runs only" in section


def test_absent_unit_states_no_data_explicitly(store):
    section = store.section_for("AssetCheck_NeverRan", max_bytes=8000)
    assert "No runtime execution data was available" in section
    assert "NOT evidence that the unit never runs" in section


def test_unknown_unit_renders_nothing(store):
    """A unit that was never asked about is not the same as one with no data."""
    assert store.section_for("SomeOtherUnit", max_bytes=8000) == ""


def test_failures_drop_before_the_section_is_abandoned(store):
    """Under a tight budget the table survives and the failure list goes first."""
    section = store.section_for("RHM2Smartflow_Workorders", max_bytes=900)
    assert "| Runs observed | 1257 |" in section
    assert "Distinct failure modes" not in section


def test_failure_modes_report_their_occurrence_count(store):
    """One line per failure mode, carrying how many executions it represents."""
    section = store.section_for("RHM2Smartflow_Workorders", max_bytes=8000)
    assert "Seen 764 times, most recently 2026-07-01: Job stopped with code 1" in section
    assert "Seen once, most recently 2026-06-20" in section
    assert "one entry can represent many executions" in section


def test_section_is_ascii_only(store):
    """Generated docs target Windows cp1252; non-ASCII breaks them."""
    for is_route in (False, True):
        section = store.section_for(
            "RHM2Smartflow_Workorders", is_esb_route=is_route, max_bytes=8000
        )
        section.encode("ascii")


def test_empty_store_is_falsey():
    assert not RuntimeEvidenceStore(window_days=90)
