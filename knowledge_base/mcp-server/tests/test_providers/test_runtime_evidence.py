"""Tests for the runtime-evidence core.

The module is deterministic text and deterministic aggregation handed straight
to a doc-writing model, so the properties that matter are the ones that would
make a published document wrong:

* a unit whose scan identity is a `job_id` must still resolve against the
  DISPLAY name TMC schedules it under -- werkorder loses
  `Maximo2Quintiq_Workorder_Route` otherwise, which is the exact unit whose
  76-day single execution motivated the ESB wording below;
* a batched fetch must merge orphan jobs by intersection, or splitting the unit
  list reports the whole estate as orphaned;
* an ESB route must not be described with batch-job wording;
* a unit with no runtime data must produce an explicit no-data notice, so a
  writer cannot read silence as "this never runs";
* nothing may exceed the rollup byte ceiling, and nothing may be cut mid-row;
* the output must stay ASCII (generated docs target Windows cp1252).
"""

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from mcp_server.config import MCPServerConfig
from mcp_server.providers.custom import runtime_evidence as module
from mcp_server.providers.custom.runtime_evidence import (
    RUNTIME_CACHE_JSON,
    RUNTIME_COVERAGE_JSON,
    RUNTIME_PROFILE_MD,
    AliasKind,
    RuntimeEvidenceStore,
    RuntimeMode,
    RuntimeUnitEvidence,
    RuntimeUnitRequest,
    as_unit_requests,
    classify_cadence,
    fetch_runtime_evidence,
    plan_batches,
    project_rollup,
    redaction_violation,
    unit_names_for_request,
)

_NOW = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)

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
            "error_summary": "Job stopped with errors in component tMSSqlConnection_2",
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
        requested_units=2,
        batches=1,
        source_key="tmc",
    )


# --- HTTP fake ----------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Records every request and replays a scripted handler."""

    def __init__(self, handler, recorder: list) -> None:
        self._handler = handler
        self._recorder = recorder

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def get(self, url, params=None, headers=None):
        sent = [value for key, value in (params or []) if key == "units"]
        self._recorder.append(sent)
        return self._handler(sent)


def _install_http(monkeypatch, handler) -> list:
    """Patch httpx AT THE CONSUMING MODULE, per the project test convention."""
    recorder: list = []
    monkeypatch.setattr(
        module,
        "httpx",
        SimpleNamespace(AsyncClient=lambda **_kwargs: _FakeClient(handler, recorder)),
    )
    return recorder


def _unit(name: str, **overrides) -> dict:
    return {**_PAYLOAD, "unit_name": name, "job_name": name, **overrides}


# --- A1: two identifier keys --------------------------------------------------


def test_both_identifier_keys_are_sent():
    units = [RuntimeUnitRequest(unit_name="i-8999255ea122", display_name="Workorder_Route")]
    assert unit_names_for_request(units) == ("i-8999255ea122", "Workorder_Route")


def test_a_unit_whose_display_name_equals_its_id_sends_one_key():
    units = [RuntimeUnitRequest(unit_name="SameName", display_name="SameName")]
    assert unit_names_for_request(units) == ("SameName",)


def test_unit_facts_and_pairs_and_strings_all_normalise():
    fact = SimpleNamespace(name="i-1", display_name="Route")
    requests = as_unit_requests([fact, ("i-2", "Other"), "i-3"])
    assert [(r.unit_name, r.display_name) for r in requests] == [
        ("i-1", "Route"),
        ("i-2", "Other"),
        ("i-3", ""),
    ]


@pytest.mark.asyncio
async def test_display_name_match_is_keyed_on_the_manifest_unit(monkeypatch):
    """The verified werkorder defect: manifest names matched 4 units, display
    names matched 5, and the lost one was the ESB route."""

    def handler(sent):
        # The runtime source only knows the display name.
        matched = [_unit("Maximo2Quintiq_Workorder_Route")] if "Maximo2Quintiq_Workorder_Route" in sent else []
        return _FakeResponse(
            {
                "window_days": 90,
                "units": matched,
                "unmatched_units": [name for name in sent if name != "Maximo2Quintiq_Workorder_Route"],
                "orphan_runtime_jobs": [],
            }
        )

    _install_http(monkeypatch, handler)
    store = await fetch_runtime_evidence(
        "werkorder",
        [RuntimeUnitRequest(unit_name="i-8999255ea122", display_name="Maximo2Quintiq_Workorder_Route")],
    )
    # Keyed on the MANIFEST identity, which is what every consumer looks up with.
    assert "i-8999255ea122" in store.by_unit
    assert "Maximo2Quintiq_Workorder_Route" not in store.by_unit
    evidence = store.by_unit["i-8999255ea122"]
    assert evidence.alias_kind == AliasKind.DISPLAY_NAME.value
    assert evidence.matched_alias == "Maximo2Quintiq_Workorder_Route"
    # The runtime job's own name is preserved for the document to quote.
    assert evidence.job_name == "Maximo2Quintiq_Workorder_Route"
    assert store.unmatched_units == ()


@pytest.mark.asyncio
async def test_manifest_identity_wins_when_both_keys_match(monkeypatch):
    def handler(sent):
        return _FakeResponse(
            {
                "window_days": 90,
                "units": [
                    _unit("i-1", job_name="JobById", total_runs=10),
                    _unit("Display", job_name="JobByName", total_runs=20),
                ],
                "unmatched_units": [],
                "orphan_runtime_jobs": [],
            }
        )

    _install_http(monkeypatch, handler)
    store = await fetch_runtime_evidence(
        "p", [RuntimeUnitRequest(unit_name="i-1", display_name="Display")]
    )
    assert store.by_unit["i-1"].job_name == "JobById"
    # Two different runtime jobs claimed one unit: recorded, not hidden.
    assert store.alias_conflicts == ("i-1",)


# --- A2: batching and merge semantics ----------------------------------------


def test_batches_are_capped_by_count():
    batches = plan_batches([f"unit_{i}" for i in range(450)], max_units=200)
    assert [len(batch) for batch in batches] == [200, 200, 50]


def test_the_server_cap_is_never_exceeded_however_high_the_config():
    batches = plan_batches([f"u{i}" for i in range(500)], max_units=10_000)
    assert max(len(batch) for batch in batches) == module.SERVER_MAX_UNITS_PER_CALL


def test_batches_are_also_capped_by_encoded_query_bytes():
    """200 names at ~30 chars is ~7 KB against a common 8 KB request line, and
    the 414 that follows used to degrade to 'no runtime data at all'."""
    long_names = [f"VolkerVessels_Workorder_Talend_exports_unit_{i:03d}" for i in range(200)]
    batches = plan_batches(long_names, max_units=200, max_query_bytes=1_000)
    assert len(batches) > 1
    assert sum(len(batch) for batch in batches) == 200
    for batch in batches:
        encoded = sum(len(f"&units={name}".encode()) for name in batch)
        assert encoded <= 1_000


def test_an_oversized_single_alias_still_gets_its_own_batch():
    """Dropping a unit silently is worse than one batch failing loudly."""
    batches = plan_batches(["x" * 5_000, "small"], max_units=200, max_query_bytes=100)
    assert batches == [["x" * 5_000], ["small"]]


@pytest.mark.asyncio
async def test_matched_and_unmatched_merge_by_union_orphans_by_intersection(monkeypatch):
    def handler(sent):
        if "a" in sent:
            return _FakeResponse(
                {
                    "window_days": 90,
                    "units": [_unit("a")],
                    "unmatched_units": ["b"],
                    # Job B matched in the OTHER batch, so it looks orphaned here.
                    "orphan_runtime_jobs": ["c", "ghost"],
                }
            )
        return _FakeResponse(
            {
                "window_days": 90,
                "units": [_unit("c")],
                "unmatched_units": ["d"],
                "orphan_runtime_jobs": ["a", "ghost"],
            }
        )

    _install_http(monkeypatch, handler)
    monkeypatch.setattr(module.config, "runtime_evidence_batch_units", 2)
    store = await fetch_runtime_evidence("p", ["a", "b", "c", "d"])
    assert store.batches == 2
    assert sorted(store.by_unit) == ["a", "c"]
    assert sorted(store.unmatched_units) == ["b", "d"]
    # Union would have said a, c and ghost were all orphaned.
    assert store.orphan_runtime_jobs == ("ghost",)


@pytest.mark.asyncio
async def test_a_failed_batch_is_excluded_from_the_orphan_intersection(monkeypatch):
    def handler(sent):
        if "a" in sent:
            return _FakeResponse(
                {"window_days": 90, "units": [_unit("a")], "unmatched_units": [],
                 "orphan_runtime_jobs": ["ghost"]}
            )
        raise RuntimeError("boom")

    _install_http(monkeypatch, handler)
    monkeypatch.setattr(module.config, "runtime_evidence_batch_units", 2)
    store = await fetch_runtime_evidence("p", ["a", "b", "c", "d"])
    # The failed batch has no matched set; folding it in as an empty one would
    # have erased the real orphan.
    assert store.orphan_runtime_jobs == ("ghost",)
    assert store.partial is True
    assert store.failed_batches == 1
    assert store.mode is RuntimeMode.DATA


@pytest.mark.asyncio
async def test_a_failed_batch_yields_unretrieved_units_not_unmatched(monkeypatch):
    """'We do not know' is not 'there is nothing'."""

    def handler(sent):
        if "a" in sent:
            return _FakeResponse(
                {"window_days": 90, "units": [], "unmatched_units": ["a", "b"],
                 "orphan_runtime_jobs": []}
            )
        raise RuntimeError("boom")

    _install_http(monkeypatch, handler)
    monkeypatch.setattr(module.config, "runtime_evidence_batch_units", 2)
    store = await fetch_runtime_evidence("p", ["a", "b", "c", "d"])
    assert sorted(store.unmatched_units) == ["a", "b"]
    assert sorted(store.unretrieved_units) == ["c", "d"]
    section = store.section_for("c", max_bytes=4000)
    assert "did NOT complete" in section


@pytest.mark.asyncio
async def test_every_batch_failing_is_unavailable_not_empty_data(monkeypatch):
    def handler(sent):
        raise RuntimeError("backend down")

    _install_http(monkeypatch, handler)
    store = await fetch_runtime_evidence("p", ["a", "b"])
    assert store.mode is RuntimeMode.UNAVAILABLE
    assert store.partial is True
    assert store.unretrieved_units == ("a", "b")
    assert store.by_unit == {}


# --- A4: mode plus two independent booleans -----------------------------------


@pytest.mark.asyncio
async def test_live_evidence_off_is_opted_out_and_makes_no_request(monkeypatch):
    recorder = _install_http(monkeypatch, lambda sent: _FakeResponse({}))
    store = await fetch_runtime_evidence("p", ["a"], live_evidence=False)
    assert store.mode is RuntimeMode.OPTED_OUT
    assert recorder == []


@pytest.mark.asyncio
async def test_config_disabled_is_opted_out(monkeypatch):
    recorder = _install_http(monkeypatch, lambda sent: _FakeResponse({}))
    monkeypatch.setattr(module.config, "runtime_evidence_enabled", False)
    store = await fetch_runtime_evidence("p", ["a"])
    assert store.mode is RuntimeMode.OPTED_OUT
    assert recorder == []


@pytest.mark.asyncio
async def test_partial_and_scope_mismatch_are_independent(monkeypatch):
    """A partial fetch with a high orphan ratio has no valid single enum value."""

    def handler(sent):
        if "a" in sent:
            return _FakeResponse(
                {
                    "window_days": 90,
                    "units": [_unit("a")],
                    "unmatched_units": [],
                    "orphan_runtime_jobs": [f"ghost_{i}" for i in range(30)],
                }
            )
        raise RuntimeError("boom")

    _install_http(monkeypatch, handler)
    monkeypatch.setattr(module.config, "runtime_evidence_batch_units", 1)
    store = await fetch_runtime_evidence("p", ["a", "b"])
    assert store.mode is RuntimeMode.DATA
    assert store.partial is True
    assert store.scope_mismatch is True


@pytest.mark.asyncio
async def test_a_healthy_partial_scan_is_not_flagged_as_a_scope_mismatch(monkeypatch):
    """werkorder at 5-of-374 with a low orphan ratio is CORRECT (R9), and a
    match-ratio rule would have accused it."""

    def handler(sent):
        return _FakeResponse(
            {
                "window_days": 90,
                "units": [_unit(name) for name in sent if name.startswith("matched")],
                "unmatched_units": [name for name in sent if not name.startswith("matched")],
                "orphan_runtime_jobs": ["one_ghost"],
            }
        )

    _install_http(monkeypatch, handler)
    units = [f"matched_{i}" for i in range(5)] + [f"child_{i}" for i in range(369)]
    store = await fetch_runtime_evidence("werkorder", units)
    assert len(store.by_unit) == 5
    assert store.scope_mismatch is False


# --- R13 / A7: redaction verified before persistence --------------------------


@pytest.mark.parametrize(
    "text, kind",
    [
        ("connection failed password=hunter2", "credential"),
        ("jdbc://admin:hunter2@dbserver/db", "userinfo"),
        ("notified ops@customer-name.com of the failure", "email"),
        ("could not reach 10.44.2.19", "ipv4"),
        ("GCS 404 on gs://customer-name-talend/exports/x.csv", "uri_authority"),
        ("timeout calling mas.manage.customer.almhosting", "fqdn"),
    ],
)
def test_identifying_shapes_are_detected(text, kind):
    violation = redaction_violation(text)
    assert violation is not None
    assert violation[0] == kind
    assert "<flagged>" in violation[1]


@pytest.mark.parametrize(
    "text",
    [
        "Exception in component tMSSqlConnection_2 (RHM2Smartflow) java.sql.SQLException",
        "Exception in component tBigQueryOutput_1_tBQBE",
        "GCS object missing at gs://<bucket>/<path>",
        "could not reach https://<host>/...",
        "password=<redacted>",
        "Execution <execution-id> failed with code 1",
        "java.lang.NullPointerException at Data.truncation",
        "Remote engine was stopped/restarted",
    ],
)
def test_already_redacted_and_diagnostic_text_is_not_flagged(text):
    """Component names carry the diagnosis and the component-echo fabrication
    validator depends on them: flagging one turns a real fact into a false
    positive."""
    assert redaction_violation(text) is None


@pytest.mark.asyncio
async def test_a_leaky_failure_line_is_dropped_at_fetch_time_not_render_time(monkeypatch):
    """The cache is written from the fetched store, so filtering only at render
    time would let a resume reintroduce exactly what was filtered."""

    def handler(sent):
        return _FakeResponse(
            {
                "window_days": 90,
                "units": [
                    _unit(
                        "a",
                        recent_failures=[
                            {"failed_at": "2026-07-01T00:00:00Z",
                             "error_summary": "404 on gs://acme-talend/exports/x.csv",
                             "occurrences": 3},
                            {"failed_at": "2026-07-02T00:00:00Z",
                             "error_summary": "Exception in component tMSSqlConnection_2",
                             "occurrences": 5},
                        ],
                    )
                ],
                "unmatched_units": [],
                "orphan_runtime_jobs": [],
            }
        )

    _install_http(monkeypatch, handler)
    store = await fetch_runtime_evidence("p", ["a"])
    summaries = [summary for _at, summary, _n in store.by_unit["a"].recent_failures]
    assert summaries == ["Exception in component tMSSqlConnection_2"]
    assert len(store.redaction_flags) == 1
    assert store.redaction_flags[0].kind == "uri_authority"
    assert "acme-talend" not in store.redaction_flags[0].excerpt
    # The cache round trip must not carry it back either.
    assert "acme-talend" not in str(store.to_json())
    # The flags payload is RETURNED; this module writes no files.
    payload = store.redaction_flags_payload()
    assert payload["flag_count"] == 1


@pytest.mark.asyncio
async def test_a_leaky_incident_is_dropped_before_cache_or_render(monkeypatch):
    def handler(sent):
        return _FakeResponse(
            {
                "window_days": 90,
                "units": [
                    _unit(
                        "a",
                        incident_insights=[
                            {
                                "pattern_id": "network.unknown_host",
                                "confidence": "medium",
                                "occurrences": 1,
                                "most_recent_at": "2026-07-01T00:00:00Z",
                                "causal_message": "could not resolve the configured host",
                                "root_cause_candidate": "DNS resolution failed",
                                "next_checks": ["Open https://secret.internal.example.com/health"],
                            }
                        ],
                    )
                ],
                "unmatched_units": [],
                "orphan_runtime_jobs": [],
            }
        )

    _install_http(monkeypatch, handler)
    fetched = await fetch_runtime_evidence("p", ["a"])
    assert fetched.by_unit["a"].incident_insights == ()
    assert fetched.redaction_flags[0].kind == "uri_authority"
    assert "secret.internal" not in str(fetched.to_json())


@pytest.mark.asyncio
async def test_a_leaky_line_never_fails_the_run(monkeypatch):
    def handler(sent):
        return _FakeResponse(
            {
                "window_days": 90,
                "units": [
                    _unit("a", recent_failures=[
                        {"failed_at": "", "error_summary": "password=hunter2", "occurrences": 1}
                    ])
                ],
                "unmatched_units": [],
                "orphan_runtime_jobs": [],
            }
        )

    _install_http(monkeypatch, handler)
    store = await fetch_runtime_evidence("p", ["a"])
    assert store.mode is RuntimeMode.DATA
    assert "a" in store.by_unit


# --- R11 / A8: cadence classification -----------------------------------------


def test_a_median_snaps_onto_a_configured_interval():
    """Verified real medians are 3600 s, 1801 s and 900 s; the MEAN for one of
    the same jobs is 2204 s, which is not any schedule anyone configured."""
    gaps = [1801.0] * 40
    result = classify_cadence(gaps, starts=41)
    assert result.interval_seconds == 1_800
    assert "every 30 min" in result.label
    assert "1801 s" in result.label


def test_a_mean_distorting_outlier_does_not_move_the_classification():
    gaps = [3600.0] * 39 + [86_400.0]
    result = classify_cadence(gaps, starts=41)
    assert result.interval_seconds == 3_600


def test_below_the_start_floor_is_ad_hoc_not_a_guessed_interval():
    result = classify_cadence([3600.0] * 5, starts=6)
    assert result.interval_seconds is None
    assert result.basis == "insufficient_sample"
    assert "6 starts" in result.label


def test_dispersed_gaps_are_ad_hoc():
    gaps = [10.0, 20.0, 5_000.0, 90_000.0] * 10
    result = classify_cadence(gaps, starts=41)
    assert result.interval_seconds is None
    assert result.basis == "dispersed"
    assert "ad hoc" in result.label


def test_a_multi_environment_blend_says_why_it_could_not_classify():
    """A8: an hourly PROD schedule plus ad-hoc DEV runs legitimately fails the
    dispersion gate; blaming the job for a property of the aggregate is wrong."""
    gaps = [3600.0] * 20 + [17.0, 45_000.0] * 10
    result = classify_cadence(gaps, starts=41, environments=["PROD", "DEV", "TEST"])
    assert result.interval_seconds is None
    assert "blend 3 environments (PROD, DEV, TEST)" in result.label
    assert "cannot be separated" in result.label


def test_a_regular_but_non_standard_median_is_reported_as_such():
    result = classify_cadence([2_204.0] * 40, starts=41)
    assert result.interval_seconds is None
    assert "non-standard" in result.label
    assert "2204 s" in result.label


def test_no_gap_data_is_stated_not_silently_ad_hoc():
    """The endpoint does not expose per-run start times today. Saying 'ad hoc'
    would be a claim the evidence does not support."""
    result = classify_cadence(starts=1316)
    assert result.basis == "no_gap_data"
    assert "not classified" in result.label
    assert "1316 starts" in result.label


def test_precomputed_quantiles_are_accepted():
    result = classify_cadence(
        gap_p25=880.0, gap_p50=900.0, gap_p75=920.0, gap_sample_size=250, starts=251
    )
    assert result.interval_seconds == 900


# --- R10: per-unit render -----------------------------------------------------


def test_recency_leads_the_table(store):
    """61.29 runs/day was rendered for a job that had not run in nearly two months."""
    section = store.section_for("RHM2Smartflow_Workorders", max_bytes=8000, now=_NOW)
    body = section.split("| Fact | Value |")[1]
    first_row = body.strip().splitlines()[1]
    assert first_row.startswith("| Last run | 2026-07-01 (53 days ago)")


def test_the_bare_runs_per_day_float_is_gone(store):
    section = store.section_for("RHM2Smartflow_Workorders", max_bytes=8000, now=_NOW)
    assert "61.29" not in section
    assert "runs/day" not in section
    assert "Observed cadence" in section


def test_duration_percentiles_are_gated_behind_a_sample_floor():
    """One ESB route showed p50 = 13.2 days over five successes."""
    thin = RuntimeEvidenceStore(
        window_days=90,
        by_unit={"route": RuntimeUnitEvidence.from_payload(
            _unit("route", success_runs=5, total_runs=8, p50_duration_ms=1_141_724_496)
        )},
    )
    section = thin.section_for("route", max_bytes=8000, now=_NOW)
    assert "insufficient sample (n=5)" in section
    assert "13.2 days" not in section
    assert "below the 30-run floor" in section


def test_a_large_sample_still_reports_percentiles(store):
    section = store.section_for("RHM2Smartflow_Workorders", max_bytes=8000, now=_NOW)
    assert "Run duration (median) | 12.2 s" in section


def test_log_quality_and_bounded_incident_insight_render_for_the_exact_unit():
    indexed = RuntimeEvidenceStore(
        window_days=10,
        by_unit={
            "Quintiq2RHM_NormalPlanning": RuntimeUnitEvidence.from_payload(
                _unit(
                    "Quintiq2RHM_NormalPlanning",
                    failed_runs=4,
                    log_quality={
                        "failed_executions": 4,
                        "executions_with_logs": 4,
                        "diagnosed_executions": 4,
                        "capture_ceiling_reached": 0,
                    },
                    incident_insights=[
                        {
                            "pattern_id": "sqlserver.deadlock",
                            "confidence": "medium",
                            "occurrences": 4,
                            "most_recent_at": "2026-08-24T00:00:00Z",
                            "failed_component": "tMSSqlInput_1",
                            "termination_component": "tDie_1",
                            "error_code": "1205",
                            "exception_class": "java.sql.SQLException",
                            "causal_message": "Transaction was chosen as the deadlock victim",
                            "root_cause_candidate": "SQL Server selected the transaction as a deadlock victim.",
                            "cause_origin": "deterministic",
                            "next_checks": [
                                "Correlate competing transactions at the failure time",
                                "Review lock duration around the affected query",
                            ],
                            "raw_logs": ["RAW_LOG_DUMP_MUST_NEVER_REACH_PROMPT"],
                        }
                    ],
                )
            )
        },
    )

    section = indexed.section_for(
        "Quintiq2RHM_NormalPlanning", max_bytes=8000, now=_NOW
    )
    assert "Failed executions with logs | 4 of 4" in section
    assert "Evidence-backed operational insights" in section
    assert "Dominant recurring issue" in section
    assert "sqlserver.deadlock" in section
    assert "originating component `tMSSqlInput_1` -> termination/reporting component `tDie_1`" in section
    assert "do not describe it as the origin" in section
    assert "Correlate competing transactions at the failure time" in section
    assert "do not strengthen an interpretation into a proven business root cause" in section
    assert "RAW_LOG_DUMP_MUST_NEVER_REACH_PROMPT" not in section


def test_causal_insight_is_isolated_to_its_exact_unit():
    indexed = RuntimeEvidenceStore(
        window_days=10,
        by_unit={
            "job_a": RuntimeUnitEvidence.from_payload(
                _unit(
                    "job_a",
                    incident_insights=[
                        {
                            "pattern_id": "database.connection_reset",
                            "confidence": "high",
                            "occurrences": 3,
                            "most_recent_at": "2026-08-24T00:00:00Z",
                            "failed_component": "tWaitForSqlData_1",
                            "termination_component": "tDie_1",
                            "causal_message": "IO Error: Connection reset",
                            "root_cause_candidate": "The database connection was lost before termination.",
                            "next_checks": ["Check database reachability from the runtime engine"],
                        }
                    ],
                )
            ),
            "job_b": RuntimeUnitEvidence.from_payload(_unit("job_b")),
        },
    )

    job_a = indexed.section_for("job_a", max_bytes=8_000, now=_NOW)
    job_b = indexed.section_for("job_b", max_bytes=8_000, now=_NOW)

    assert "IO Error: Connection reset" in job_a
    assert "tWaitForSqlData_1" in job_a
    assert "tDie_1" in job_a
    assert "IO Error: Connection reset" not in job_b
    assert "tWaitForSqlData_1" not in job_b
    assert "tDie_1" not in job_b


def test_plans_are_dropped_from_the_unit_table(store):
    """4 of 5 werkorder units return `plans: []`, and the RAG-side measurement
    of the proposed job_id fix recovered nothing (16/64/1/2 by name versus
    16/60/1/2 by id), so the column stays out."""
    section = store.section_for("RHM2Smartflow_Workorders", max_bytes=8000, now=_NOW)
    assert "Orchestration plans" not in section
    assert "Nightly_Workorders" not in section


def test_unfinished_runs_are_a_telemetry_note_not_a_table_row(store):
    section = store.section_for("RHM2Smartflow_Workorders", max_bytes=8000, now=_NOW)
    assert "| Never completed |" not in section
    assert "Telemetry gap: 1 of the observed runs never reported completion" in section


def test_multi_environment_units_say_the_figures_are_blended():
    blended = RuntimeEvidenceStore(
        window_days=90,
        by_unit={"u": RuntimeUnitEvidence.from_payload(
            _unit("u", environments=["ACC", "TST"])
        )},
    )
    section = blended.section_for("u", max_bytes=8000, now=_NOW)
    assert "AGGREGATES the environments listed (ACC, TST)" in section


def test_batch_job_reports_run_duration(store):
    section = store.section_for("RHM2Smartflow_Workorders", max_bytes=8000, now=_NOW)
    assert "Run duration (median)" in section
    assert "Uptime per start" not in section
    assert "ESB route" not in section


def test_esb_route_is_described_as_uptime_not_run_duration(store):
    section = store.section_for(
        "RHM2Smartflow_Workorders", is_esb_route=True, max_bytes=8000, now=_NOW
    )
    assert "Uptime per start (median) | 12.2 s" in section
    assert "Run duration" not in section
    assert "long-running listener" in section


def test_core_counts_are_rendered_verbatim(store):
    section = store.section_for("RHM2Smartflow_Workorders", max_bytes=8000, now=_NOW)
    assert "| Runs observed | 1257 |" in section
    assert "| Succeeded | 492 |" in section
    assert "| Failed | 764 (60.8%) |" in section
    assert "| Consecutive failures (most recent) | 7 |" in section
    assert "| Environments | ACC |" in section
    assert "do not recompute" in section


def test_latency_caveat_is_always_present(store):
    section = store.section_for("RHM2Smartflow_Workorders", max_bytes=8000, now=_NOW)
    assert "completed, successful runs only" in section


def test_absent_unit_states_no_data_explicitly(store):
    section = store.section_for("AssetCheck_NeverRan", max_bytes=8000)
    assert "No runtime execution data was available" in section
    assert "NOT evidence that the unit never runs" in section
    assert "tRunJob" in section


def test_unknown_unit_renders_nothing(store):
    """A unit that was never asked about is not the same as one with no data."""
    assert store.section_for("SomeOtherUnit", max_bytes=8000) == ""


def test_failures_drop_before_the_section_is_abandoned(store):
    section = store.section_for("RHM2Smartflow_Workorders", max_bytes=1400, now=_NOW)
    assert "| Runs observed | 1257 |" in section
    assert "Distinct failure modes" not in section


def test_failure_modes_report_their_occurrence_count(store):
    section = store.section_for("RHM2Smartflow_Workorders", max_bytes=8000, now=_NOW)
    assert "Seen 764 times, most recently 2026-07-01" in section
    assert "Seen once, most recently 2026-06-20" in section
    assert "one entry can represent many executions" in section


def test_section_is_ascii_only(store):
    for is_route in (False, True):
        section = store.section_for(
            "RHM2Smartflow_Workorders", is_esb_route=is_route, max_bytes=8000, now=_NOW
        )
        section.encode("ascii")


def test_empty_store_is_falsey():
    assert not RuntimeEvidenceStore(window_days=90)


# --- Section 3: the project rollup --------------------------------------------


def _rollup_store(unit_count: int = 3, unmatched: int = 0, orphans: int = 0):
    by_unit = {}
    for index in range(unit_count):
        by_unit[f"unit_{index:03d}"] = RuntimeUnitEvidence.from_payload(
            _unit(
                f"unit_{index:03d}",
                total_runs=100 + index,
                failed_runs=index,
                failure_rate=index / 100,
                last_run_at="2026-08-20T00:00:00Z" if index else "2026-01-01T00:00:00Z",
            )
        )
    return RuntimeEvidenceStore(
        window_days=90,
        by_unit=by_unit,
        unmatched_units=tuple(f"child_{i:03d}" for i in range(unmatched)),
        orphan_runtime_jobs=tuple(f"ghost_{i:03d}" for i in range(orphans)),
        requested_units=unit_count + unmatched,
        batches=1,
        source_key="tmc",
    )


def test_the_rollup_never_returns_empty_for_any_mode():
    for mode in RuntimeMode:
        text = project_rollup(None, mode=mode, max_bytes=12_000)
        assert text.strip()
        assert "RUNTIME EVIDENCE" in text


def test_an_absent_rollup_forbids_the_inference_it_exists_to_stop():
    text = project_rollup(None, mode=RuntimeMode.UNAVAILABLE, max_bytes=12_000)
    assert "Do NOT read this absence as evidence that jobs are idle" in text


def test_the_opted_out_rollup_says_so_rather_than_looking_like_a_failure():
    text = project_rollup(None, mode=RuntimeMode.OPTED_OUT, max_bytes=12_000)
    assert "switched OFF" in text
    assert "could not be read" not in text


def test_the_rollup_carries_all_six_sections():
    text = project_rollup(_rollup_store(3, unmatched=2, orphans=2), max_bytes=40_000, now=_NOW)
    for heading in (
        "## 1. Coverage and confidence",
        "## 2. Reliability ranking",
        "## 3. Failure-mode census",
        "## 4. Observed schedule",
        "## 5a. Silent units",
        "## 5b. Unobserved units",
        "## 6. Orphan runtime jobs",
    ):
        assert heading in text


def test_the_coverage_caveat_is_present_whenever_there_is_data():
    text = project_rollup(_rollup_store(1), max_bytes=40_000, now=_NOW)
    assert "The runtime source lists SCHEDULED TASKS" in text
    assert "tRunJob" in text


def test_the_failure_census_carries_a_share_column():
    text = project_rollup(_rollup_store(2), max_bytes=40_000, now=_NOW)
    assert "| Failure mode | Executions | Share | Units affected | Most recent |" in text
    assert "%" in text


def test_dormant_and_dead_are_two_separated_lists_and_neither_claims_dead_code():
    text = project_rollup(_rollup_store(3, unmatched=4), max_bytes=40_000, now=_NOW)
    assert "## 5a. Silent units" in text
    assert "## 5b. Unobserved units" in text
    assert "NOT a dead-code list" in text


def test_the_asymmetry_is_stated_with_both_readings_and_no_accusation():
    """A3: no BINDING_SUSPECT. werkorder at 5/374 is correct; alitalend at 1/10
    with 91/92 orphans is the broken one, and no available signal separates a
    mis-binding from a deliberately partial scan."""
    text = project_rollup(_rollup_store(1, unmatched=9, orphans=91), max_bytes=40_000, now=_NOW)
    assert "Scan/runtime asymmetry" in text
    assert "may cover a deliberate subset" in text
    assert "may describe different estates" in text
    assert "Do not assert either" in text
    assert "BINDING SUSPECT" not in text
    assert "binding_suspect" not in text


def test_a_shared_read_states_that_orphans_are_expected():
    """One tenant backing several scanned projects makes a sibling project's
    jobs orphans BY DESIGN. Offering the mis-binding reading there would accuse
    a correct configuration -- the same class of error `scope_mismatch` was
    already narrowed to avoid."""
    store = _rollup_store(1, unmatched=9, orphans=91)
    shared = replace(store, shared_read=True, shared_from=("Talend EU",))

    text = project_rollup(shared, max_bytes=40_000, now=_NOW)

    assert "Scan/runtime asymmetry" in text
    assert "shared with other scanned projects" in text
    assert "NOT evidence of a mis-bound project" in text
    # The competing readings belong to an OWNED mirror; here they would be wrong.
    assert "may describe different estates" not in text
    # The figures themselves are still published either way.
    assert "91 of 92 runtime jobs" in text


def test_a_shared_read_names_the_mirror_it_borrowed():
    shared = replace(
        _rollup_store(2), shared_read=True, shared_from=("Talend EU", "Talend US")
    )

    text = project_rollup(shared, max_bytes=40_000, now=_NOW)

    assert "| Mirror ownership | shared read of Talend EU, Talend US |" in text


def test_an_owned_read_says_nothing_about_sharing():
    text = project_rollup(_rollup_store(2), max_bytes=40_000, now=_NOW)

    assert "Mirror ownership" not in text
    assert "shared read" not in text


def test_a_correct_partial_scan_is_not_accused():
    text = project_rollup(_rollup_store(5, unmatched=369), max_bytes=40_000, now=_NOW)
    assert "Scan/runtime asymmetry" not in text  # no orphans, no asymmetry claim
    assert "expected shape of a scan that includes child jobs" in text


def test_a_partial_fetch_says_unknown_is_not_idle():
    store = RuntimeEvidenceStore(
        window_days=90,
        by_unit={"a": RuntimeUnitEvidence.from_payload(_unit("a"))},
        unretrieved_units=("b", "c"),
        requested_units=3,
        batches=3,
        failed_batches=1,
        partial=True,
    )
    text = project_rollup(store, max_bytes=40_000, now=_NOW)
    assert "Partial retrieval" in text
    assert "UNKNOWN, not idle" in text


def test_the_rollup_respects_the_byte_ceiling_by_dropping_whole_rows():
    store = _rollup_store(20, unmatched=400, orphans=50)
    text = project_rollup(store, max_bytes=6_000, now=_NOW)
    assert len(text.encode("utf-8")) <= 6_000
    assert "row(s) omitted to fit the rollup byte budget" in text
    # Whole rows only: every table line is a complete pipe-delimited row.
    for line in text.splitlines():
        if line.startswith("|"):
            assert line.rstrip().endswith("|"), line


def test_the_omission_count_is_reported():
    store = _rollup_store(2, unmatched=300)
    text = project_rollup(store, max_bytes=5_000, now=_NOW)
    rendered_children = sum(1 for line in text.splitlines() if line.startswith("| child_"))
    omitted = 300 - rendered_children
    assert f"_{omitted} further row(s) omitted" in text


def test_the_coverage_header_survives_any_ceiling():
    """The counts are the part a document must never lose."""
    store = _rollup_store(20, unmatched=400, orphans=50)
    text = project_rollup(store, max_bytes=3_000, now=_NOW)
    assert "## 1. Coverage and confidence" in text
    assert "| Scanned units in scope | 420 |" in text
    assert "SCHEDULED TASKS" in text


def test_the_rollup_is_ascii_only():
    project_rollup(_rollup_store(5, unmatched=5, orphans=5), max_bytes=40_000, now=_NOW).encode(
        "ascii"
    )
    for mode in RuntimeMode:
        project_rollup(None, mode=mode, max_bytes=12_000).encode("ascii")


def test_shared_runtime_jobs_are_flagged_so_a_summary_does_not_double_count():
    store = RuntimeEvidenceStore(
        window_days=90,
        by_unit={
            "i-aaa": RuntimeUnitEvidence.from_payload(_unit("i-aaa", job_name="Shared_Route")),
            "i-bbb": RuntimeUnitEvidence.from_payload(_unit("i-bbb", job_name="Shared_Route")),
        },
        requested_units=2,
    )
    text = project_rollup(store, max_bytes=40_000, now=_NOW)
    assert "Shared runtime jobs" in text
    assert "Never sum them" in text


# --- Serialisation and shared artifact schema ---------------------------------


def test_store_round_trips_through_json(store):
    revived = RuntimeEvidenceStore.from_json(store.to_json())
    assert revived.window_days == store.window_days
    assert revived.mode is store.mode
    assert sorted(revived.by_unit) == sorted(store.by_unit)
    assert revived.by_unit["RHM2Smartflow_Workorders"] == store.by_unit[
        "RHM2Smartflow_Workorders"
    ]
    assert revived.unmatched_units == store.unmatched_units
    assert revived.requested_units == store.requested_units
    assert revived.to_json() == store.to_json()


def test_a_full_store_round_trips_including_flags_and_orphans():
    original = RuntimeEvidenceStore(
        window_days=30,
        by_unit={"a": RuntimeUnitEvidence.from_payload(_unit("a"))},
        unmatched_units=("b",),
        unretrieved_units=("c",),
        orphan_runtime_jobs=("ghost",),
        requested_units=3,
        batches=2,
        failed_batches=1,
        source_key="tmc",
        partial=True,
        scope_mismatch=True,
        mode=RuntimeMode.DATA,
        redaction_flags=(module.RuntimeRedactionFlag("a", "ipv4", "x <flagged> y"),),
        alias_conflicts=("a",),
    )
    revived = RuntimeEvidenceStore.from_json(original.to_json())
    assert revived.to_json() == original.to_json()
    assert revived.redaction_flags[0].kind == "ipv4"


def test_from_json_degrades_on_a_malformed_payload():
    revived = RuntimeEvidenceStore.from_json({"mode": "nonsense", "by_unit": "not a mapping"})
    assert revived.mode is RuntimeMode.UNAVAILABLE
    assert revived.by_unit == {}


def test_the_coverage_payload_carries_every_key_the_reader_expects(store):
    """`api/routes/generate_runs.py::_load_runtime_coverage` reads these by name;
    prose in a plan document is how the writer and reader diverged before."""
    payload = store.coverage_payload()
    for key in (
        "mode", "source_key", "partial", "batches", "orphan_runtime_jobs",
        "units_requested", "units_matched", "matched", "unmatched", "window_days",
    ):
        assert key in payload, key
    assert payload["mode"] == "data"
    assert payload["units_requested"] == 2
    assert payload["units_matched"] == 1
    assert payload["matched"] == ["RHM2Smartflow_Workorders"]
    assert payload["unmatched"] == ["AssetCheck_NeverRan"]
    assert payload["resolved_by_alias"] == {"RHM2Smartflow_Workorders": "unit_name"}


def test_the_coverage_mode_cannot_contradict_availability():
    """The verified defect: `available=true` beside `mode="unavailable"`."""
    payload = project_store_unavailable().coverage_payload()
    assert payload["mode"] == "unavailable"
    assert payload["units_matched"] == 0


def project_store_unavailable() -> RuntimeEvidenceStore:
    return RuntimeEvidenceStore(window_days=90, mode=RuntimeMode.UNAVAILABLE, requested_units=4)


def test_artifact_paths_are_placed_where_cleanup_expects_them():
    # `_transient/` is removed by Phase 4; `_evidence/` would publish the raw
    # payload into docs/ forever, and is an existing namespace besides.
    assert RUNTIME_CACHE_JSON.startswith("_transient/")
    assert not RUNTIME_CACHE_JSON.startswith("_evidence/")
    # The rollup must survive to Phase 3 and be removed by cleanup.
    assert RUNTIME_PROFILE_MD.startswith("_analysis/")
    # `_evidence_` in a Phase 3 context filename silently promotes to trim rank 0
    # in `phase3_slices._trim_priority`, colliding with the per-doc bundle
    # convention. The rollup earns rank 0 by name, not by collision.
    assert "_evidence_" not in RUNTIME_PROFILE_MD
    # The coverage artifact is read off the PUBLISHED docs tree, so it must not
    # be in a directory cleanup deletes.
    assert RUNTIME_COVERAGE_JSON.startswith("_assessment/")


# --- A9: the config ceiling is clamped, never asserted ------------------------


def test_the_profile_ceiling_is_clamped_below_the_shared_context_threshold():
    tuned = MCPServerConfig(phase3_shared_context_file_bytes=8_000)
    assert tuned.phase3_runtime_profile_bytes == 7_999


def test_a_lowered_shared_threshold_does_not_crash_the_server():
    """`MCPServerConfig` is instantiated at import; an assert here would turn a
    tuning change into a server that will not start, and is stripped under -O."""
    assert MCPServerConfig(phase3_shared_context_file_bytes=1).phase3_runtime_profile_bytes == 1


def test_the_default_ceiling_is_below_the_default_shared_threshold():
    default = MCPServerConfig()
    assert default.phase3_runtime_profile_bytes < default.phase3_shared_context_file_bytes
