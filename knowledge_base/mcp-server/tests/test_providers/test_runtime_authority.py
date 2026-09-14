"""Tests for runtime authority fusion.

The failures that matter here are the ones that produce a document a reader
believes:

* a missing figure must read as UNAVAILABLE. If it reads as non-execution, the
  run has published an inference nobody made, and it reads exactly like a fact;
* an opted-out or failed TMC fetch fills `unmatched_units` with the WHOLE
  request list, so treating that list as "no runtime record" turns "we did not
  look" into "there is nothing";
* TMC and log-estate numbers must never merge. Two records for one job is
  correct output; one blended record describes no population that exists;
* every record must carry authority + environment + window, or two runs of one
  project are not comparable and a narrowed read scope is indistinguishable
  from a shrinking estate;
* output must be ASCII and byte-stable across processes (generated docs target
  Windows cp1252, and an unstable prompt file is a silent cache miss).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from mcp_server.providers.custom.runtime_authority import (
    CODE_ELASTIC_PAYLOAD_INVALID,
    CODE_NO_ELASTIC_SOURCE,
    CODE_PARTIAL_FETCH,
    CODE_REDACTION_FLAG,
    CODE_RUNTIME_OPTED_OUT,
    CODE_RUNTIME_UNAVAILABLE,
    CODE_SCOPE_MISMATCH,
    CODE_SCOPE_MISMATCH_SUPPRESSED,
    CODE_TMC_OBSERVABILITY_DI_ONLY,
    ELASTIC,
    RUNTIME_AUTHORITY_JSON,
    RUNTIME_AUTHORITY_MD,
    SCHEMA_VERSION,
    TMC,
    ElasticAggregatePayload,
    RuntimeAuthorityBundle,
    fuse_runtime_authorities,
    load_tmc_store_payload,
    render_runtime_authority,
    write_runtime_authority,
)
from mcp_server.providers.custom.runtime_evidence import (
    RUNTIME_CACHE_JSON,
    RuntimeEvidenceStore,
    RuntimeMode,
    RuntimeRedactionFlag,
    RuntimeUnitEvidence,
)

_NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)

#: Phrases that would turn unavailability into a claim about behaviour.
_FORBIDDEN_PHRASES = ("never executed", "not executed", "never ran", "never runs")


def _unit(
    unit_name: str,
    *,
    job_name: str | None = None,
    environments: tuple[str, ...] = ("PROD",),
    total_runs: int = 1257,
    success_runs: int = 492,
    failed_runs: int = 764,
    unfinished_runs: int = 1,
    failure_rate: float = 0.6078,
    plans: tuple[str, ...] = ("nightly_plan",),
    recent_failures: tuple[tuple[str, str, int], ...] = (
        ("2026-09-08T02:11:00Z", "tDBOutput_1 constraint violation", 703),
    ),
) -> RuntimeUnitEvidence:
    """One unit's evidence, in the real dataclass shape the producer emits."""
    return RuntimeUnitEvidence(
        unit_name=unit_name,
        job_name=job_name or unit_name,
        source_key="tmc:acme",
        resolved_by="exact_name",
        environments=environments,
        total_runs=total_runs,
        success_runs=success_runs,
        failed_runs=failed_runs,
        unfinished_runs=unfinished_runs,
        failure_rate=failure_rate,
        current_failure_streak=7,
        first_run_at="2026-06-11T08:30:20Z",
        last_run_at="2026-09-08T23:45:10Z",
        runs_per_day=14.2,
        p50_duration_ms=41_000,
        p95_duration_ms=95_500,
        max_duration_ms=612_000,
        plans=plans,
        recent_failures=recent_failures,
        matched_alias=unit_name,
        gap_p25_seconds=1790.0,
        gap_p50_seconds=1801.0,
        gap_p75_seconds=1812.0,
        gap_sample_size=400,
    )


def _tmc_payload(**overrides: object) -> dict:
    """A `RuntimeEvidenceStore.to_json()` body, built from the real store."""
    defaults: dict = {
        "window_days": 90,
        "by_unit": {
            "RHM2Smartflow_Workorders": _unit("RHM2Smartflow_Workorders"),
            "Maximo2RHM_Workorders": _unit(
                "Maximo2RHM_Workorders",
                job_name="Maximo2RHM_Workorders_v2",
                environments=("ACC", "PROD"),
            ),
        },
        "unmatched_units": ("AssetCheck_GetLocation",),
        "unretrieved_units": ("AssetCheck_GetLocations",),
        "orphan_runtime_jobs": ("SomeSiblingJob",),
        "requested_units": 4,
        "batches": 1,
        "failed_batches": 0,
        "source_key": "tmc:acme",
        "mode": RuntimeMode.DATA,
        "read_environments": ("PROD",),
    }
    defaults.update(overrides)
    return RuntimeEvidenceStore(**defaults).to_json()  # type: ignore[arg-type]


def _elastic_payload(**overrides: object) -> dict:
    payload: dict = {
        "schema_version": "elastic-aggregates-1",
        "index_pattern": "talend-esb-monitoring-*",
        "window_days": 30,
        "window_end": "2026-09-09T00:00:00Z",
        "environments": ["PROD"],
        "per_job": [
            {
                "job_name": "RHM2Smartflow_Workorders",
                "environment": "PROD",
                "message_count": 88_000,
                "error_count": 120,
                "last_seen": "2026-09-08T23:59:00Z",
                "latency_p50_ms": 210,
                "latency_p95_ms": 1_900,
                "correlation_key": "workorder_id",
                "correlated_target_job": "Maximo2RHM_Workorders_v2",
            },
            {
                "job_name": "Maximo2RHM_Workorders_v2",
                "environment": "PROD",
                "message_count": 87_500,
                "error_count": 4,
                "last_seen": "2026-09-08T23:58:00Z",
                "latency_p50_ms": 95,
                "latency_p95_ms": 400,
            },
        ],
        "limitations": [],
    }
    payload.update(overrides)
    return payload


def _codes(bundle: RuntimeAuthorityBundle) -> list[str]:
    return [item.code for item in bundle.limitations]


# --- TMC lane -----------------------------------------------------------------


def test_tmc_data_mode_produces_deployed_set_and_observations() -> None:
    bundle = fuse_runtime_authorities(
        _tmc_payload(), None, project_id="acme", now=_NOW
    )

    assert bundle.schema_version == SCHEMA_VERSION
    assert bundle.project_id == "acme"
    assert bundle.generated_at == "2026-09-09T12:00:00Z"
    assert bundle.authorities[TMC] == "data"
    assert bundle.window_days == 90
    assert bundle.read_environments == ["PROD"]

    assert [record.unit_name for record in bundle.deployed_set] == [
        "Maximo2RHM_Workorders",
        "RHM2Smartflow_Workorders",
    ]
    deployed = {record.unit_name: record for record in bundle.deployed_set}
    assert deployed["Maximo2RHM_Workorders"].job_name == "Maximo2RHM_Workorders_v2"
    assert deployed["Maximo2RHM_Workorders"].environments == ["ACC", "PROD"]
    assert deployed["RHM2Smartflow_Workorders"].plans == ["nightly_plan"]

    tmc_observations = bundle.observations_for(TMC)
    assert len(tmc_observations) == 2
    observed = {record.unit_name: record for record in tmc_observations}
    single = observed["RHM2Smartflow_Workorders"]
    assert single.environment == "PROD"
    assert single.pooled_environments is False
    assert (single.total_runs, single.success_runs, single.failed_runs) == (1257, 492, 764)
    assert single.unfinished_runs == 1
    assert single.failure_rate == pytest.approx(0.6078)
    assert single.current_failure_streak == 7
    assert single.first_run_at == "2026-06-11T08:30:20Z"
    assert single.last_run_at == "2026-09-08T23:45:10Z"
    assert single.runs_per_day == pytest.approx(14.2)
    assert single.p50_duration_ms == 41_000
    assert single.p95_duration_ms == 95_500
    assert single.max_duration_ms == 612_000
    assert [mode.occurrences for mode in single.recent_failure_modes] == [703]
    assert single.recent_failure_modes[0].error_summary.startswith("tDBOutput_1")
    # Cadence is the deterministic classification, never an estimate.
    assert single.cadence is not None
    assert set(single.cadence) == {"label", "interval_seconds", "sample_size", "basis"}
    assert single.cadence["basis"] == "gaps"


def test_pooled_environments_are_stated_never_silent() -> None:
    bundle = fuse_runtime_authorities(_tmc_payload(), None, project_id="acme", now=_NOW)
    pooled = next(
        record for record in bundle.observations if record.unit_name == "Maximo2RHM_Workorders"
    )
    assert pooled.pooled_environments is True
    assert pooled.environment == "ACC+PROD"
    assert any("pool environments" in note for note in bundle.authority_notes)


def test_every_record_carries_provenance_with_environment_and_window() -> None:
    bundle = fuse_runtime_authorities(
        _tmc_payload(), _elastic_payload(), project_id="acme", now=_NOW
    )
    records = [
        *bundle.deployed_set,
        *bundle.observations,
        *bundle.correlation_latency,
    ]
    assert records
    for record in records:
        provenance = record.provenance
        assert provenance.authority in {TMC, ELASTIC}
        assert provenance.window_days > 0
        assert provenance.window_end
        assert provenance.source_key
        if provenance.authority == TMC:
            assert provenance.read_scope == ["PROD"]
            # TMC's window ends at generation time and runs backwards from there.
            assert provenance.window_end == bundle.generated_at
        else:
            assert provenance.window_end == "2026-09-09T00:00:00Z"
        # Environment is always populated for these fixtures; a pooled read
        # carries the joined key rather than a single misleading name.
        assert provenance.environment


def test_unmatched_and_unretrieved_are_different_gap_statuses() -> None:
    bundle = fuse_runtime_authorities(_tmc_payload(), None, project_id="acme", now=_NOW)
    gaps = {gap.unit_name: gap.status for gap in bundle.deployed_set_gaps}
    assert gaps == {
        "AssetCheck_GetLocation": "not_observed_in_window",
        "AssetCheck_GetLocations": "lookup_failed",
    }


def test_partial_fetch_downgrades_status_and_records_a_limitation() -> None:
    bundle = fuse_runtime_authorities(
        _tmc_payload(partial=True, batches=3, failed_batches=1),
        None,
        project_id="acme",
        now=_NOW,
    )
    assert bundle.authorities[TMC] == "partial"
    assert CODE_PARTIAL_FETCH in _codes(bundle)
    # A partial fetch still publishes what it retrieved -- it is a floor, not a
    # reason to withhold evidence.
    assert bundle.observations_for(TMC)


def test_unavailable_mode_yields_zero_observations() -> None:
    """Never infer non-execution: no data means no rows, plus a stated reason."""
    bundle = fuse_runtime_authorities(
        _tmc_payload(
            by_unit={},
            mode=RuntimeMode.UNAVAILABLE,
            unmatched_units=(),
            unretrieved_units=("A", "B"),
            partial=True,
            batches=2,
            failed_batches=2,
        ),
        None,
        project_id="acme",
        now=_NOW,
    )
    assert bundle.authorities[TMC] == "unavailable"
    assert bundle.observations == []
    assert bundle.deployed_set == []
    assert CODE_RUNTIME_UNAVAILABLE in _codes(bundle)
    assert CODE_PARTIAL_FETCH in _codes(bundle)
    assert {gap.status for gap in bundle.deployed_set_gaps} == {"lookup_failed"}


def test_unavailable_mode_never_reports_unmatched_as_observed_absence() -> None:
    """A store that did not look must not report `not_observed_in_window`."""
    bundle = fuse_runtime_authorities(
        _tmc_payload(
            by_unit={},
            mode=RuntimeMode.UNAVAILABLE,
            unmatched_units=("A", "B"),
            unretrieved_units=(),
        ),
        None,
        project_id="acme",
        now=_NOW,
    )
    assert {gap.status for gap in bundle.deployed_set_gaps} == {"lookup_failed"}


def test_opted_out_mode_reports_opted_out_and_looks_nothing_up() -> None:
    bundle = fuse_runtime_authorities(
        _tmc_payload(
            by_unit={},
            mode=RuntimeMode.OPTED_OUT,
            # This is what `fetch_runtime_evidence` really does when opted out.
            unmatched_units=("A", "B", "C"),
            unretrieved_units=(),
        ),
        None,
        project_id="acme",
        now=_NOW,
    )
    assert bundle.authorities[TMC] == "opted_out"
    assert bundle.observations == []
    assert CODE_RUNTIME_OPTED_OUT in _codes(bundle)
    assert [gap.status for gap in bundle.deployed_set_gaps] == ["lookup_failed"] * 3


def test_absent_tmc_payload_is_unavailable_not_empty() -> None:
    bundle = fuse_runtime_authorities(None, None, project_id="acme", now=_NOW)
    assert bundle.authorities[TMC] == "unavailable"
    assert bundle.observations == []
    assert CODE_RUNTIME_UNAVAILABLE in _codes(bundle)
    # A window is still stated, so the reader knows what was not read.
    assert bundle.window_days > 0


def test_malformed_tmc_fields_degrade_to_unavailable() -> None:
    """`RuntimeEvidenceStore.from_json` is total, so garbage lands as UNAVAILABLE."""
    bundle = fuse_runtime_authorities(
        {"by_unit": "not-a-mapping", "mode": "nonsense"},
        None,
        project_id="acme",
        now=_NOW,
    )
    assert bundle.authorities[TMC] == "unavailable"
    assert bundle.observations == []
    codes = _codes(bundle)
    assert CODE_RUNTIME_UNAVAILABLE in codes
    # One reason, not two: the "no payload was supplied" message would be a
    # false statement beside a payload that was supplied.
    assert codes.count(CODE_RUNTIME_UNAVAILABLE) == 1


def test_non_mapping_tmc_payload_degrades_to_unavailable() -> None:
    bundle = fuse_runtime_authorities(
        ["not", "a", "mapping"],  # type: ignore[arg-type]
        None,
        project_id="acme",
        now=_NOW,
    )
    assert bundle.authorities[TMC] == "unavailable"
    assert bundle.observations == []
    codes = _codes(bundle)
    assert codes.count(CODE_RUNTIME_UNAVAILABLE) == 1
    assert "could not be read" in next(
        item.message for item in bundle.limitations if item.code == CODE_RUNTIME_UNAVAILABLE
    )


def test_scope_mismatch_under_a_shared_read_is_suppressed_not_asserted() -> None:
    bundle = fuse_runtime_authorities(
        _tmc_payload(scope_mismatch=True, shared_read=True, shared_from=("Acme TMC",)),
        None,
        project_id="acme",
        now=_NOW,
    )
    codes = _codes(bundle)
    assert CODE_SCOPE_MISMATCH_SUPPRESSED in codes
    assert CODE_SCOPE_MISMATCH not in codes
    suppressed = next(
        item for item in bundle.limitations if item.code == CODE_SCOPE_MISMATCH_SUPPRESSED
    )
    assert "Acme TMC" in suppressed.message
    assert suppressed.scope == "project"


def test_scope_mismatch_without_a_shared_read_is_reported() -> None:
    bundle = fuse_runtime_authorities(
        _tmc_payload(scope_mismatch=True),
        None,
        project_id="acme",
        now=_NOW,
    )
    codes = _codes(bundle)
    assert CODE_SCOPE_MISMATCH in codes
    assert CODE_SCOPE_MISMATCH_SUPPRESSED not in codes


def test_redaction_flags_become_a_limitation() -> None:
    bundle = fuse_runtime_authorities(
        _tmc_payload(
            redaction_flags=(
                RuntimeRedactionFlag(
                    unit_name="RHM2Smartflow_Workorders",
                    kind="uri_authority",
                    excerpt="jdbc://<redacted>/db",
                ),
            )
        ),
        None,
        project_id="acme",
        now=_NOW,
    )
    assert CODE_REDACTION_FLAG in _codes(bundle)


# --- unit kinds ---------------------------------------------------------------


def test_route_units_get_the_di_only_observability_limitation() -> None:
    bundle = fuse_runtime_authorities(
        _tmc_payload(),
        None,
        project_id="acme",
        unit_kinds={
            "Maximo2Quintiq_Workorder_Route": "route",
            "RHM2Smartflow_Workorders": "job",
            "Shared_Logging": "joblet",
            "Legacy_Routelet": "routelet",
        },
        now=_NOW,
    )
    flagged = {
        item.unit_name
        for item in bundle.limitations
        if item.code == CODE_TMC_OBSERVABILITY_DI_ONLY
    }
    assert flagged == {"Maximo2Quintiq_Workorder_Route", "Legacy_Routelet"}
    for item in bundle.limitations:
        if item.code == CODE_TMC_OBSERVABILITY_DI_ONLY:
            assert item.scope == "unit"
            assert item.authority == TMC


def test_no_unit_kinds_means_no_route_limitations() -> None:
    bundle = fuse_runtime_authorities(_tmc_payload(), None, project_id="acme", now=_NOW)
    assert CODE_TMC_OBSERVABILITY_DI_ONLY not in _codes(bundle)


# --- Elastic lane -------------------------------------------------------------


def test_absent_elastic_source_is_stated() -> None:
    bundle = fuse_runtime_authorities(_tmc_payload(), None, project_id="acme", now=_NOW)
    assert bundle.authorities[ELASTIC] == "unavailable"
    assert CODE_NO_ELASTIC_SOURCE in _codes(bundle)
    assert bundle.correlation_latency == []


def test_elastic_present_adds_its_own_observations_and_correlation_records() -> None:
    bundle = fuse_runtime_authorities(
        _tmc_payload(), _elastic_payload(), project_id="acme", now=_NOW
    )
    assert bundle.authorities[ELASTIC] == "data"

    elastic_observations = bundle.observations_for(ELASTIC)
    assert [record.job_name for record in elastic_observations] == [
        "Maximo2RHM_Workorders_v2",
        "RHM2Smartflow_Workorders",
    ]
    first = next(
        record
        for record in elastic_observations
        if record.job_name == "RHM2Smartflow_Workorders"
    )
    # Message counts, not executions, and no invented scanner identity.
    assert first.unit_name == ""
    assert first.total_runs == 88_000
    assert first.failed_runs == 120
    assert first.success_runs == 87_880
    assert first.failure_rate == pytest.approx(round(120 / 88_000, 4))
    assert first.last_run_at == "2026-09-08T23:59:00Z"
    assert first.p50_duration_ms == 210
    assert first.cadence is None
    assert first.provenance.source_key == "talend-esb-monitoring-*"
    assert first.provenance.window_days == 30

    assert len(bundle.correlation_latency) == 1
    link = bundle.correlation_latency[0]
    assert link.correlation_key == "workorder_id"
    assert link.source_unit == "RHM2Smartflow_Workorders"
    assert link.target_unit == "Maximo2RHM_Workorders_v2"
    assert (link.p50_ms, link.p95_ms) == (210, 1_900)
    assert link.sample_size == 88_000
    assert link.provenance.authority == ELASTIC


def test_tmc_and_elastic_figures_are_never_merged() -> None:
    """One job, two authorities, two rows -- never one blended number."""
    bundle = fuse_runtime_authorities(
        _tmc_payload(), _elastic_payload(), project_id="acme", now=_NOW
    )
    rows = [
        record
        for record in bundle.observations
        if "RHM2Smartflow_Workorders" in (record.unit_name, record.job_name)
    ]
    assert len(rows) == 2
    assert {record.provenance.authority for record in rows} == {TMC, ELASTIC}
    totals = {record.provenance.authority: record.total_runs for record in rows}
    assert totals == {TMC: 1257, ELASTIC: 88_000}


def test_elastic_producer_limitations_downgrade_it_to_partial() -> None:
    bundle = fuse_runtime_authorities(
        None,
        _elastic_payload(limitations=["index rolled over mid-window"]),
        project_id="acme",
        now=_NOW,
    )
    assert bundle.authorities[ELASTIC] == "partial"
    partial = [
        item
        for item in bundle.limitations
        if item.code == CODE_PARTIAL_FETCH and item.authority == ELASTIC
    ]
    assert partial and "index rolled over mid-window" in partial[0].message


def test_empty_elastic_per_job_is_unavailable_not_zero_activity() -> None:
    bundle = fuse_runtime_authorities(
        None, _elastic_payload(per_job=[]), project_id="acme", now=_NOW
    )
    assert bundle.authorities[ELASTIC] == "unavailable"
    assert bundle.observations_for(ELASTIC) == []


def test_invalid_elastic_payload_degrades_with_a_named_code() -> None:
    bundle = fuse_runtime_authorities(
        None,
        {"schema_version": "elastic-aggregates-1", "per_job": [{"nope": 1}]},
        project_id="acme",
        now=_NOW,
    )
    codes = _codes(bundle)
    assert bundle.authorities[ELASTIC] == "unavailable"
    assert CODE_ELASTIC_PAYLOAD_INVALID in codes
    # Not "no source was supplied" -- one was, and it was rejected.
    assert CODE_NO_ELASTIC_SOURCE not in codes
    assert bundle.observations_for(ELASTIC) == []


def test_elastic_input_contract_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ElasticAggregatePayload.model_validate({**_elastic_payload(), "surprise": 1})


def test_bundle_model_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RuntimeAuthorityBundle.model_validate({"project_id": "x", "surprise": 1})


# --- rendering ----------------------------------------------------------------


def test_markdown_states_unavailability_and_never_non_execution() -> None:
    bundle = fuse_runtime_authorities(None, None, project_id="acme", now=_NOW)
    text = render_runtime_authority(bundle)

    assert text.isascii()
    assert "unavailable" in text.lower()
    lowered = text.lower()
    for phrase in _FORBIDDEN_PHRASES:
        assert phrase not in lowered, phrase
    # Status before facts: a reader must not meet an empty table first.
    assert text.index("## 1. Authorities and status") < text.index("## 3. Observations")
    assert "Not available." in text  # correlation latency section


def test_markdown_with_data_keeps_the_authorities_separate() -> None:
    bundle = fuse_runtime_authorities(
        _tmc_payload(), _elastic_payload(), project_id="acme", now=_NOW
    )
    text = render_runtime_authority(bundle)

    assert text.isascii()
    lowered = text.lower()
    for phrase in _FORBIDDEN_PHRASES:
        assert phrase not in lowered, phrase
    assert "### Authority: tmc" in text
    assert "### Authority: elastic" in text
    assert "MESSAGES seen in the log estate" in text
    assert "ACC+PROD (pooled)" in text
    assert "not_observed_in_window" in text
    assert "lookup_failed" in text
    assert "workorder_id" in text
    assert "### Recent failure modes" in text


def test_elastic_table_omits_execution_only_columns() -> None:
    """A zero in a column the authority does not measure reads as a measurement."""
    text = render_runtime_authority(
        fuse_runtime_authorities(None, _elastic_payload(), project_id="acme", now=_NOW)
    )
    elastic_block = text.split("### Authority: elastic", 1)[1].split("## 4.", 1)[0]
    header = next(line for line in elastic_block.splitlines() if line.startswith("| Job"))
    assert "Messages" in header
    assert "Error rate" in header
    assert "Unfinished" not in header
    assert "Streak" not in header
    assert "Cadence" not in header


def test_markdown_is_byte_stable_for_identical_input() -> None:
    first = render_runtime_authority(
        fuse_runtime_authorities(_tmc_payload(), _elastic_payload(), project_id="a", now=_NOW)
    )
    second = render_runtime_authority(
        fuse_runtime_authorities(_tmc_payload(), _elastic_payload(), project_id="a", now=_NOW)
    )
    assert first == second


def test_non_ascii_provider_text_is_coerced_not_leaked() -> None:
    bundle = fuse_runtime_authorities(
        _tmc_payload(
            by_unit={
                "Job_A": _unit(
                    "Job_A",
                    recent_failures=(("2026-09-01T00:00:00Z", "café | pipe", 3),),
                )
            }
        ),
        None,
        project_id="acme",
        now=_NOW,
    )
    text = render_runtime_authority(bundle)
    assert text.isascii()
    # A literal pipe from provider text must not break the table.
    assert "\\|" in text


# --- persistence --------------------------------------------------------------


def test_write_runtime_authority_produces_both_artifacts(tmp_path: Path) -> None:
    bundle = fuse_runtime_authorities(
        _tmc_payload(),
        _elastic_payload(),
        project_id="acme",
        # A clean run of both authorities records no limitation at all, so the
        # field-name assertions below need one row to inspect.
        unit_kinds={"Maximo2Quintiq_Workorder_Route": "route"},
        now=_NOW,
    )
    json_path, md_path = write_runtime_authority(tmp_path, bundle)

    assert json_path == tmp_path / RUNTIME_AUTHORITY_JSON
    assert md_path == tmp_path / RUNTIME_AUTHORITY_MD
    assert json_path.is_file() and md_path.is_file()
    # Both live under `_analysis/`, which Phase 4 deletes: prompt input, not a
    # published deliverable.
    assert json_path.parent.name == "_analysis"

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert set(payload) == {
        "schema_version",
        "project_id",
        "generated_at",
        "authorities",
        "window_days",
        "read_environments",
        "deployed_set",
        "deployed_set_gaps",
        "observations",
        "correlation_latency",
        "limitations",
        "authority_notes",
    }
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["authorities"] == {"elastic": "data", "tmc": "data"}
    assert set(payload["deployed_set"][0]) == {
        "unit_name",
        "job_name",
        "environments",
        "plans",
        "provenance",
    }
    assert set(payload["deployed_set"][0]["provenance"]) == {
        "authority",
        "environment",
        "window_days",
        "window_end",
        "source_key",
        "read_scope",
    }
    assert set(payload["deployed_set_gaps"][0]) == {"unit_name", "status"}
    assert set(payload["limitations"][0]) == {
        "code",
        "message",
        "scope",
        "unit_name",
        "authority",
    }
    assert set(payload["correlation_latency"][0]) == {
        "correlation_key",
        "source_unit",
        "target_unit",
        "p50_ms",
        "p95_ms",
        "sample_size",
        "environment",
        "provenance",
    }
    assert md_path.read_text(encoding="utf-8").startswith(
        "# RUNTIME AUTHORITY -- FUSED RUNTIME EVIDENCE"
    )
    # Round-trips through its own contract.
    assert RuntimeAuthorityBundle.model_validate(payload) == bundle


def test_write_is_byte_stable_across_calls(tmp_path: Path) -> None:
    bundle = fuse_runtime_authorities(_tmc_payload(), None, project_id="acme", now=_NOW)
    json_path, md_path = write_runtime_authority(tmp_path, bundle)
    first = (json_path.read_bytes(), md_path.read_bytes())
    write_runtime_authority(tmp_path, bundle)
    assert (json_path.read_bytes(), md_path.read_bytes()) == first


def test_load_tmc_store_payload_reads_the_producer_cache(tmp_path: Path) -> None:
    cache = tmp_path / RUNTIME_CACHE_JSON
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(_tmc_payload()), encoding="utf-8")

    payload = load_tmc_store_payload(tmp_path)
    assert payload is not None
    assert payload["mode"] == "data"

    bundle = fuse_runtime_authorities(payload, None, project_id="acme", now=_NOW)
    assert bundle.authorities[TMC] == "data"
    assert bundle.observations_for(TMC)


@pytest.mark.parametrize("body", ["", "{not json", "[]", '"a string"'])
def test_load_tmc_store_payload_degrades_on_unusable_content(
    tmp_path: Path, body: str
) -> None:
    cache = tmp_path / RUNTIME_CACHE_JSON
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(body, encoding="utf-8")
    assert load_tmc_store_payload(tmp_path) is None


def test_load_tmc_store_payload_absent_is_none(tmp_path: Path) -> None:
    assert load_tmc_store_payload(tmp_path) is None
