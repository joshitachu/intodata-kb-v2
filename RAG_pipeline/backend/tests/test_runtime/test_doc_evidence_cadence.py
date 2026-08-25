"""Inter-run gap quantiles on the documentation-evidence read path.

A downstream consumer classifies an OBSERVED CADENCE from these fields, so that
a document can publish "runs every 30 minutes" instead of an average that
describes no schedule.  Its rule, reproduced verbatim as `_cadence_verdict`
below, is: emit an interval only when the MEDIAN gap sits within +/-10% of a
common interval AND the sample has at least 20 gaps AND `IQR / median < 0.5`;
otherwise the job is ad hoc.

What these tests defend:

* the quantiles are the discrete nearest-rank picks the rest of the repository
  uses, so a published interval is always a gap that was really observed;
* the mean is not a substitute -- verified in werkorder, `Maximo2RHM_Workorders`
  has a mean gap of 2204 s against a median of 1801 s, and the mean would
  announce a 37-minute schedule that nobody configured;
* the statistics belong to ONE environment.  `runtime_jobs` is unique on
  `(project_id, source_key, job_id)`, not on name, so a single name spans
  DEV/TEST/PROD (alitalend: 124 rows, 92 names; werkorder: 8 rows, 6 names).
  Pooling an hourly PROD schedule with ad-hoc TST runs inflates the IQR past
  the 0.5 gate and downgrades to "ad hoc" exactly the jobs worth classifying --
  `test_pooling_across_environments_would_destroy_the_classification` pins that
  harm with the two verdicts side by side;
* too little data yields NULLS, never an error and never a guess: one run is a
  sighting, not a cadence.
"""

import datetime
from datetime import UTC

import pytest

from src.runtime.models import RuntimeExecution, RuntimeExecutionState, RuntimeJobRun
from src.runtime.store import RuntimeStoreRepository

_P = "proj-cadence"
_OTHER = "proj-other"
_SRC = "tmc"
_T0 = datetime.datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
_SINCE = _T0 - datetime.timedelta(days=30)

# Common schedule intervals, in seconds, as the consumer knows them.
_COMMON_INTERVALS = (300, 900, 1800, 3600, 7200, 21600, 43200, 86400)


def _discrete_percentile(values: list[float], percentile: int) -> float:
    """The nearest-rank pick the SQL performs, restated for the expected value."""
    ordered = sorted(values)
    for rank, value in enumerate(ordered, start=1):
        if rank * 100 >= len(ordered) * percentile > (rank - 1) * 100:
            return value
    return ordered[-1]


def _cadence_verdict(p25: int | None, p50: int | None, p75: int | None, n: int | None) -> str:
    """The downstream rule, restated here so its inputs are tested, not assumed."""
    if p50 is None or not n or n < 20:
        return "ad hoc"
    if (p75 - p25) / p50 >= 0.5:
        return "ad hoc"
    for interval in _COMMON_INTERVALS:
        if abs(p50 - interval) <= 0.1 * interval:
            return f"every {interval}s"
    return "ad hoc"


async def _seed_runs(
    store: RuntimeStoreRepository,
    *,
    project_id: str,
    job_name: str,
    environment_name: str | None,
    starts: list[datetime.datetime],
    key: str,
) -> None:
    """One execution and one job run per start.

    Both tables are written because the two feed different halves of the
    payload: the reliability rollup that MATCHES a unit reads
    `runtime_executions`, while the gap quantiles read `runtime_job_runs` --
    the only one of the two that carries `environment_name`.
    """
    for index, started in enumerate(starts):
        execution_id = f"{key}-{index}"
        await store.upsert_execution(
            project_id,
            RuntimeExecution(
                execution_id=execution_id,
                job_name=job_name,
                state=RuntimeExecutionState.SUCCESS,
                started_at=started,
                ended_at=started + datetime.timedelta(milliseconds=250),
                duration_ms=250,
            ),
            _SRC,
            _T0,
        )
        await store.upsert_job_run(
            project_id,
            RuntimeJobRun(
                run_id=execution_id,
                job_id=f"job-{job_name}-{environment_name}",
                job_name=job_name,
                source_key=_SRC,
                execution_id=execution_id,
                state=RuntimeExecutionState.SUCCESS,
                environment_name=environment_name,
                started_at=started,
            ),
            _SRC,
            _T0,
        )


def _at(*offsets_seconds: float) -> list[datetime.datetime]:
    return [_T0 + datetime.timedelta(seconds=offset) for offset in offsets_seconds]


def _cumulative(first_offset: float, gaps: list[float]) -> list[float]:
    offsets = [first_offset]
    for gap in gaps:
        offsets.append(offsets[-1] + gap)
    return offsets


async def _unit(repo: RuntimeStoreRepository, unit_name: str, project_id: str = _P):
    units, _unmatched, _orphans = await repo.list_doc_evidence(
        None, project_id=project_id, unit_names=[unit_name], since=_SINCE
    )
    assert units, f"{unit_name} must resolve to a runtime job"
    return units[0]


@pytest.fixture
async def store(db_session):
    return RuntimeStoreRepository(db_session)


class TestQuantiles:
    async def test_a_known_series_yields_the_expected_quantiles(self, store):
        """Five runs, gaps of 60/120/180/240 s, nearest-rank picks."""
        await _seed_runs(
            store,
            project_id=_P,
            job_name="KnownSeries",
            environment_name="PROD",
            starts=_at(*_cumulative(0, [60, 120, 180, 240])),
            key="known",
        )
        unit = await _unit(store, "KnownSeries")
        assert unit.gap_sample_size == 4, "four gaps from five runs, not five"
        assert unit.gap_p25_seconds == 60
        assert unit.gap_p50_seconds == 120
        assert unit.gap_p75_seconds == 180

    async def test_quantiles_survive_out_of_order_ingestion(self, store):
        """Gaps are successive STARTS, so insertion order must not matter."""
        offsets = _cumulative(0, [60, 120, 180, 240])
        await _seed_runs(
            store,
            project_id=_P,
            job_name="Shuffled",
            environment_name="PROD",
            starts=_at(*reversed(offsets)),
            key="shuffled",
        )
        unit = await _unit(store, "Shuffled")
        assert (unit.gap_p25_seconds, unit.gap_p50_seconds, unit.gap_p75_seconds) == (60, 120, 180)

    async def test_the_median_beats_the_mean_on_the_werkorder_shape(self, store):
        """The reason the field exists, reproduced in miniature.

        A half-hourly job with a handful of catch-up outages: the mean gap is
        dragged well off the schedule, the median lands on it.
        """
        gaps = [1800.0] * 40 + [21600.0, 18000.0, 25200.0]
        await _seed_runs(
            store,
            project_id=_P,
            job_name="HalfHourlyWithOutages",
            environment_name="PROD",
            starts=_at(*_cumulative(0, gaps)),
            key="halfhourly",
        )
        unit = await _unit(store, "HalfHourlyWithOutages")
        mean_gap = sum(gaps) / len(gaps)
        assert mean_gap > 3000, "the mean is off the schedule, which is the point"
        assert unit.gap_p50_seconds == 1800
        assert _cadence_verdict(
            unit.gap_p25_seconds, unit.gap_p50_seconds, unit.gap_p75_seconds, unit.gap_sample_size
        ) == "every 1800s"

    async def test_an_irregular_series_is_reported_as_spread_not_smoothed(self, store):
        """Ad-hoc really is ad hoc: the IQR must carry that, not hide it."""
        gaps = [float(60 * (index % 7 + 1) ** 3) for index in range(30)]
        await _seed_runs(
            store,
            project_id=_P,
            job_name="AdHocJob",
            environment_name="PROD",
            starts=_at(*_cumulative(0, gaps)),
            key="adhoc",
        )
        unit = await _unit(store, "AdHocJob")
        assert unit.gap_sample_size == 30
        assert unit.gap_p25_seconds == int(round(_discrete_percentile(gaps, 25)))
        assert unit.gap_p50_seconds == int(round(_discrete_percentile(gaps, 50)))
        assert unit.gap_p75_seconds == int(round(_discrete_percentile(gaps, 75)))
        assert _cadence_verdict(
            unit.gap_p25_seconds, unit.gap_p50_seconds, unit.gap_p75_seconds, unit.gap_sample_size
        ) == "ad hoc"

    async def test_sub_second_precision_rounds_to_whole_seconds(self, store):
        """Both backends deliver a float; a schedule has no use for the tail."""
        await _seed_runs(
            store,
            project_id=_P,
            job_name="SubSecond",
            environment_name="PROD",
            starts=_at(*_cumulative(0, [1800.6, 1800.6, 1800.6])),
            key="subsec",
        )
        unit = await _unit(store, "SubSecond")
        assert unit.gap_p50_seconds == 1801


class TestTooLittleData:
    async def test_a_single_run_yields_nulls_not_an_error(self, store):
        await _seed_runs(
            store,
            project_id=_P,
            job_name="RanOnce",
            environment_name="PROD",
            starts=_at(0),
            key="once",
        )
        unit = await _unit(store, "RanOnce")
        assert unit.total_runs == 1
        assert unit.gap_p25_seconds is None
        assert unit.gap_p50_seconds is None
        assert unit.gap_p75_seconds is None
        assert unit.gap_sample_size is None
        assert _cadence_verdict(None, None, None, None) == "ad hoc"

    async def test_two_runs_give_exactly_one_gap(self, store):
        """The floor is a consumer decision; the store still reports the truth."""
        await _seed_runs(
            store,
            project_id=_P,
            job_name="RanTwice",
            environment_name="PROD",
            starts=_at(0, 900),
            key="twice",
        )
        unit = await _unit(store, "RanTwice")
        assert unit.gap_sample_size == 1
        assert unit.gap_p50_seconds == 900
        assert _cadence_verdict(900, 900, 900, 1) == "ad hoc", "below the n>=20 floor"

    async def test_a_unit_with_no_runtime_data_at_all_is_unmatched(self, store):
        units, unmatched, _orphans = await store.list_doc_evidence(
            None, project_id=_P, unit_names=["NeverRan"], since=_SINCE
        )
        assert units == []
        assert unmatched == ["NeverRan"]

    async def test_executions_without_a_job_run_mirror_report_no_cadence(self, store):
        """`runtime_job_runs` is the only table carrying an environment.

        A project whose job-run mirror is empty must come back with nulls rather
        than an environment-blind figure -- an unqualified cadence is the exact
        claim these fields exist to prevent.
        """
        for index in range(30):
            started = _T0 + datetime.timedelta(seconds=1800 * index)
            await store.upsert_execution(
                _P,
                RuntimeExecution(
                    execution_id=f"exec-only-{index}",
                    job_name="ExecutionsOnly",
                    state=RuntimeExecutionState.SUCCESS,
                    started_at=started,
                    ended_at=started + datetime.timedelta(milliseconds=250),
                    duration_ms=250,
                ),
                _SRC,
                _T0,
            )
        unit = await _unit(store, "ExecutionsOnly")
        assert unit.total_runs == 30
        assert unit.environments == []
        assert unit.gap_p50_seconds is None
        assert unit.gap_sample_size is None


class TestPerEnvironment:
    """The part a name-keyed rollup gets wrong.

    `Maximo2Quintiq_Workorder_Route` and `maximoFourpsWorkOrder` each run in two
    werkorder environments under one name; alitalend carries 124 job rows under
    92 names.  A gap series pooled over those deployments measures the
    interleaving, not the schedule.
    """

    @pytest.fixture
    async def two_environments(self, store):
        """PROD on a strict hourly schedule; TST ad hoc in the same window."""
        await _seed_runs(
            store,
            project_id=_P,
            job_name="Maximo2RHM_Workorders",
            environment_name="PROD",
            starts=_at(*_cumulative(0, [3600.0] * 24)),
            key="prod",
        )
        tst_gaps = [137.0, 940.0, 4300.0, 250.0, 6100.0] * 4
        await _seed_runs(
            store,
            project_id=_P,
            job_name="Maximo2RHM_Workorders",
            environment_name="TST",
            starts=_at(*_cumulative(90, tst_gaps)),
            key="tst",
        )
        return store

    async def test_the_reported_statistics_are_the_dominant_environments(self, two_environments):
        unit = await _unit(two_environments, "Maximo2RHM_Workorders")
        assert unit.gap_sample_size == 24, "PROD's 25 runs give 24 gaps; TST's 21 are excluded"
        assert unit.gap_p25_seconds == 3600
        assert unit.gap_p50_seconds == 3600
        assert unit.gap_p75_seconds == 3600

    async def test_the_named_environment_is_the_measured_one(self, two_environments):
        """`environments[0]` is the qualifier a document must print with the figure."""
        unit = await _unit(two_environments, "Maximo2RHM_Workorders")
        assert unit.environments[0] == "PROD"
        assert set(unit.environments) == {"PROD", "TST"}
        assert len(unit.environments) > 1, "the consumer's cue that the figure is per-environment"

    async def test_pooling_across_environments_would_destroy_the_classification(
        self, two_environments
    ):
        """The harm, with both verdicts computed from the same seeded data."""
        unit = await _unit(two_environments, "Maximo2RHM_Workorders")
        per_environment = _cadence_verdict(
            unit.gap_p25_seconds, unit.gap_p50_seconds, unit.gap_p75_seconds, unit.gap_sample_size
        )

        prod = _cumulative(0, [3600.0] * 24)
        tst = _cumulative(90, [137.0, 940.0, 4300.0, 250.0, 6100.0] * 4)
        merged = sorted(prod + tst)
        pooled_gaps = [later - earlier for earlier, later in zip(merged, merged[1:])]
        pooled = _cadence_verdict(
            int(round(_discrete_percentile(pooled_gaps, 25))),
            int(round(_discrete_percentile(pooled_gaps, 50))),
            int(round(_discrete_percentile(pooled_gaps, 75))),
            len(pooled_gaps),
        )

        assert per_environment == "every 3600s"
        assert pooled == "ad hoc", (
            "pooling has plenty of samples and still cannot classify: the blended "
            "spread is what breaks it, which is why the store never pools"
        )

    async def test_the_quieter_environment_never_shifts_the_figure(self, two_environments):
        """More ad-hoc TST runs must not move a PROD number by one second."""
        before = await _unit(two_environments, "Maximo2RHM_Workorders")
        await _seed_runs(
            two_environments,
            project_id=_P,
            job_name="Maximo2RHM_Workorders",
            environment_name="TST",
            starts=_at(*_cumulative(200, [11.0, 13.0, 17.0])),
            key="tst-extra",
        )
        after = await _unit(two_environments, "Maximo2RHM_Workorders")
        assert (after.gap_p25_seconds, after.gap_p50_seconds, after.gap_p75_seconds) == (
            before.gap_p25_seconds,
            before.gap_p50_seconds,
            before.gap_p75_seconds,
        )
        assert after.gap_sample_size == before.gap_sample_size

    async def test_a_dominance_flip_moves_both_the_name_and_the_number(self, store):
        """The pairing is structural, so it holds when the dominant side changes."""
        await _seed_runs(
            store,
            project_id=_P,
            job_name="FlipJob",
            environment_name="PROD",
            starts=_at(*_cumulative(0, [3600.0] * 4)),
            key="flip-prod",
        )
        await _seed_runs(
            store,
            project_id=_P,
            job_name="FlipJob",
            environment_name="ACC",
            starts=_at(*_cumulative(10, [900.0] * 20)),
            key="flip-acc",
        )
        unit = await _unit(store, "FlipJob")
        assert unit.environments[0] == "ACC", "21 ACC runs against 5 in PROD"
        assert unit.gap_p50_seconds == 900
        assert unit.gap_sample_size == 20

    async def test_an_unnamed_environment_is_one_population_not_none(self, store):
        """A provider that reports no environment blends nothing by pooling."""
        await _seed_runs(
            store,
            project_id=_P,
            job_name="NoEnvironment",
            environment_name=None,
            starts=_at(*_cumulative(0, [1800.0] * 25)),
            key="noenv",
        )
        unit = await _unit(store, "NoEnvironment")
        assert unit.environments == []
        assert unit.gap_p50_seconds == 1800
        assert unit.gap_sample_size == 25

    async def test_a_dominant_environment_with_one_run_reports_nulls(self, store):
        """Nulls beat quietly falling through to a deployment nobody named."""
        await _seed_runs(
            store,
            project_id=_P,
            job_name="OneEach",
            environment_name="ACC",
            starts=_at(0),
            key="one-acc",
        )
        await _seed_runs(
            store,
            project_id=_P,
            job_name="OneEach",
            environment_name="PROD",
            starts=_at(50),
            key="one-prod",
        )
        unit = await _unit(store, "OneEach")
        assert len(unit.environments) == 2
        assert unit.gap_p50_seconds is None
        assert unit.gap_sample_size is None


class TestScopingAndWindow:
    async def test_another_projects_runs_never_enter_the_gap_series(self, store):
        """Two projects run a job of the same name; a leak shows as a wrong gap."""
        await _seed_runs(
            store,
            project_id=_P,
            job_name="SharedName",
            environment_name="PROD",
            starts=_at(*_cumulative(0, [3600.0] * 24)),
            key="mine",
        )
        await _seed_runs(
            store,
            project_id=_OTHER,
            job_name="SharedName",
            environment_name="PROD",
            starts=_at(*_cumulative(30, [60.0] * 200)),
            key="theirs",
        )
        units, _unmatched, _orphans = await store.list_doc_evidence(
            [_P], project_id=_P, unit_names=["SharedName"], since=_SINCE
        )
        assert units[0].gap_sample_size == 24
        assert units[0].gap_p50_seconds == 3600

    async def test_runs_before_the_window_are_excluded_from_the_gaps(self, store):
        """The window is the caller's claim about what the figure covers."""
        old = [_T0 - datetime.timedelta(days=60, seconds=index * 10) for index in range(5)]
        await _seed_runs(
            store,
            project_id=_P,
            job_name="Windowed",
            environment_name="PROD",
            starts=old + _at(*_cumulative(0, [1800.0] * 3)),
            key="windowed",
        )
        unit = await _unit(store, "Windowed")
        assert unit.gap_sample_size == 3, "the 60-day-old burst is outside a 30-day window"
        assert unit.gap_p50_seconds == 1800

    async def test_a_denied_scope_returns_nothing_rather_than_a_cadence(self, store):
        await _seed_runs(
            store,
            project_id=_P,
            job_name="Denied",
            environment_name="PROD",
            starts=_at(*_cumulative(0, [1800.0] * 24)),
            key="denied",
        )
        units, unmatched, _orphans = await store.list_doc_evidence(
            [], project_id=_P, unit_names=["Denied"], since=_SINCE
        )
        assert units == []
        assert unmatched == ["Denied"]
