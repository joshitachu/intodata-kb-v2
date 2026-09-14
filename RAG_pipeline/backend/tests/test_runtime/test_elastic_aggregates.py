"""Contract for the `elastic-aggregates-1` producer.

The payload this service builds is the ONE input the enterprise Talend
documentation method accepts for the Elastic authority, and it reaches an LLM.
Four properties are load-bearing enough to pin here rather than leave to review:

* **Shape parity with the consumer.** `ElasticAggregatePayload` in
  `providers/custom/runtime_authority.py` sets `extra="forbid"`, so one added or
  renamed field silently downgrades every run to `elastic_payload_invalid` --
  a limitation nobody reads, on a document that then reports the estate as
  unavailable. Parity is asserted against the REAL file, by parsing it.

* **Environment scope applied once, to the whole population.** The measured
  precedent is an unscoped read reporting 3392 runs / 785 failures = 23.1% for
  an estate whose PROD was 0.8% and whose ACC was 60.7%. Here the same defect
  would be a p50 latency and a message count describing different environments,
  so the fixture deliberately makes PROD and ACC disagree on every figure.

* **Absence stated, not implied.** `_fuse_elastic` reads an empty `per_job` as
  `unavailable`; without a limitation saying WHY, a project with no Elastic
  source, a source with an empty mirror, and a mirror whose messages cannot be
  attributed are indistinguishable in the published document.

* **Percentiles, not means.** Verified in this codebase: a mean gap of 2202 s
  against a true median of 1801 s made a mean-fed classifier downgrade a
  30-minute schedule to "ad hoc". The distribution below is chosen so a mean
  (5280) and the real p50 (1800) cannot be confused.
"""

from __future__ import annotations

import ast
import datetime
from datetime import UTC
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from src.config import settings
from src.db.models.runtime_topology import RuntimeJobScanMapRow
from src.runtime import elastic_aggregates as module
from src.runtime.elastic_aggregates import (
    ElasticAggregatePayload,
    ElasticAggregateService,
    ElasticPerJobAggregate,
)
from src.runtime.models import (
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeJobDependency,
    RuntimeJobRun,
    RuntimeLogEvent,
    RuntimeLogSeverity,
)
from src.runtime.store import RuntimeStoreRepository

_P = "proj-elastic"
_OTHER = "proj-other-tenant"
_NOW = datetime.datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
_T0 = _NOW - datetime.timedelta(days=2)
_JOB = "Maximo2RHM_Workorders"
_SIBLING = "AssetCheck_GetLocation"
_SIBLING_PLURAL = "AssetCheck_GetLocations"


@pytest.fixture(autouse=True)
def credential_key(monkeypatch):
    """Source config lives in the encrypted credential blob; a key is required."""
    monkeypatch.setattr(
        settings, "source_credentials_encryption_key", Fernet.generate_key().decode()
    )
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous", "")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_id", "primary")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous_id", "previous")


class Mirror:
    """Seeds the generic runtime tables the way a source connector would."""

    def __init__(self, session) -> None:
        self.session = session
        self.store = RuntimeStoreRepository(session)
        self.source_key = ""
        self.source_id = 0

    async def register_elastic(
        self,
        *,
        project_id: str = _P,
        source_type: str = "elastic",
        label: str = "Log estate",
        config: dict | None = None,
    ) -> None:
        source = await self.store.create_source_config(
            project_id=project_id, source_type=source_type, label=label
        )
        self.source_key = source.source_key
        self.source_id = source.id
        if config is not None:
            await self.store.write_credentials(
                source_id=source.id,
                plaintext_payload=config,
                credential_type=source_type,
                rotated_by="1",
            )

    async def message_flow(
        self,
        *,
        job_name: str,
        execution_id: str,
        project_id: str = _P,
        source_key: str | None = None,
        environment_id: str | None = "env-prod",
        environment_name: str | None = "PROD",
        duration_ms: int | None = 250,
        messages: int = 1,
        errors: int = 0,
        offset_minutes: int = 0,
        attribution_safe: bool = True,
        with_execution: bool = True,
        with_job_run: bool = True,
    ) -> None:
        """One correlated exchange: its execution, its job run, its log lines."""
        key = source_key or self.source_key
        started = _T0 + datetime.timedelta(minutes=offset_minutes)
        if with_execution:
            await self.store.upsert_execution(
                project_id,
                RuntimeExecution(
                    execution_id=execution_id,
                    job_name=job_name,
                    state=RuntimeExecutionState.SUCCESS,
                    started_at=started,
                    ended_at=started + datetime.timedelta(milliseconds=duration_ms or 0),
                    duration_ms=duration_ms,
                ),
                key,
                _T0,
            )
        if with_job_run and environment_id is not None:
            await self.store.upsert_job_run(
                project_id,
                RuntimeJobRun(
                    run_id=execution_id,
                    job_id=f"{job_name}-id",
                    job_name=job_name,
                    source_key=key,
                    execution_id=execution_id,
                    state=RuntimeExecutionState.SUCCESS,
                    environment_id=environment_id,
                    environment_name=environment_name,
                    started_at=started,
                ),
                key,
                _T0,
            )
        for ordinal in range(messages):
            severity = (
                RuntimeLogSeverity.ERROR if ordinal < errors else RuntimeLogSeverity.INFO
            )
            await self.store.upsert_log_event(
                project_id,
                RuntimeLogEvent(
                    execution_id=execution_id,
                    timestamp=started + datetime.timedelta(seconds=ordinal),
                    severity=severity,
                    message=f"exchange {execution_id} line {ordinal}",
                    line_ordinal=ordinal,
                    attribution_safe=attribution_safe,
                ),
                key,
                _T0,
            )

    async def correlation_edge(
        self,
        *,
        job_name: str,
        correlation_key: str,
        target_job: str | None,
        project_id: str = _P,
        last_seen_at: datetime.datetime | None = None,
        dependency_type: str = "correlation",
    ) -> None:
        await self.store.upsert_job_dependency(
            project_id,
            RuntimeJobDependency(
                job_id=f"{job_name}-id",
                job_name=job_name,
                source_key=self.source_key,
                dependency_type=dependency_type,
                dependency_id=correlation_key,
                dependency_name=target_job,
                last_seen_at=last_seen_at or _T0,
            ),
            self.source_key,
            _T0,
        )


@pytest.fixture
async def mirror(db_session) -> Mirror:
    return Mirror(db_session)


async def _build(db_session, project_id: str = _P, window_days: int = 90) -> ElasticAggregatePayload:
    return await ElasticAggregateService(db_session).build_payload(
        project_id=project_id, default_window_days=window_days, now=_NOW
    )


def _row(payload: ElasticAggregatePayload, job_name: str, environment: str = "PROD"):
    matches = [
        item
        for item in payload.per_job
        if item.job_name == job_name and item.environment == environment
    ]
    assert len(matches) == 1, f"expected exactly one {job_name}/{environment} row"
    return matches[0]


def _mentions(payload: ElasticAggregatePayload, fragment: str) -> bool:
    return any(fragment in text for text in payload.limitations)


# --- shape parity with the consumer ------------------------------------------

_CONTRACT_PATH = (
    Path(__file__).resolve().parents[4]
    / "knowledge_base/mcp-server/src/mcp_server/providers/custom/runtime_authority.py"
)
_PRODUCER_PATH = Path(module.__file__)

#: Replicated from `runtime_authority.py` so this file states the contract even
#: where the harness source is not present (the polyrepo split moves it). The
#: parity test below compares against the real file whenever it IS present, so
#: this list cannot quietly become the only definition.
_EXPECTED_PER_JOB_FIELDS = [
    ("job_name", "str"),
    ("environment", "str"),
    ("message_count", "int"),
    ("error_count", "int"),
    ("last_seen", "str | None"),
    ("latency_p50_ms", "int | None"),
    ("latency_p95_ms", "int | None"),
    ("correlation_key", "str"),
    ("correlated_target_job", "str"),
]
_EXPECTED_PAYLOAD_FIELDS = [
    # As `ast.unparse` renders it, which normalizes the quoting style.
    ("schema_version", "Literal['elastic-aggregates-1']"),
    ("index_pattern", "str"),
    ("window_days", "int"),
    ("window_end", "str | None"),
    ("environments", "list[str]"),
    ("per_job", "list[ElasticPerJobAggregate]"),
    ("limitations", "list[str]"),
]


def _annotated_fields(path: Path, class_name: str) -> list[tuple[str, str]]:
    """(name, annotation) per annotated attribute of one class, by parsing it.

    Parsed rather than imported: `mcp_server` is a separate distribution with its
    own dependency tree, and a parity check that can only run where that tree is
    installed is a parity check that does not run.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return [
                (statement.target.id, ast.unparse(statement.annotation))
                for statement in node.body
                if isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
            ]
    raise AssertionError(f"{class_name} not found in {path}")


class TestContractParity:
    def test_the_producer_declares_the_replicated_field_list(self):
        assert _annotated_fields(_PRODUCER_PATH, "ElasticPerJobAggregate") == (
            _EXPECTED_PER_JOB_FIELDS
        )
        assert _annotated_fields(_PRODUCER_PATH, "ElasticAggregatePayload") == (
            _EXPECTED_PAYLOAD_FIELDS
        )

    def test_the_pydantic_models_match_their_own_annotations(self):
        """Guards the ast check above against drifting from the live models."""
        assert list(ElasticPerJobAggregate.model_fields) == [
            name for name, _ in _EXPECTED_PER_JOB_FIELDS
        ]
        assert list(ElasticAggregatePayload.model_fields) == [
            name for name, _ in _EXPECTED_PAYLOAD_FIELDS
        ]

    def test_parity_with_the_real_harness_contract(self):
        if not _CONTRACT_PATH.exists():
            pytest.skip(f"harness contract not present at {_CONTRACT_PATH}")
        for class_name in ("ElasticPerJobAggregate", "ElasticAggregatePayload"):
            assert _annotated_fields(_PRODUCER_PATH, class_name) == _annotated_fields(
                _CONTRACT_PATH, class_name
            ), f"{class_name} drifted from the consumer's contract"

    def test_an_extra_field_is_rejected(self):
        """The consumer's models forbid extras, so this side must too."""
        with pytest.raises(ValueError):
            ElasticAggregatePayload.model_validate(
                {"schema_version": "elastic-aggregates-1", "project_id": _P}
            )

    async def test_a_built_payload_validates_as_the_contract(self, db_session, mirror):
        await mirror.register_elastic(config={"index_pattern": "talend-esb-monitoring-*"})
        await mirror.message_flow(job_name=_JOB, execution_id="x-1", messages=3, errors=1)

        payload = await _build(db_session)

        # Round-trips through a strict re-validation of its own JSON: the exact
        # operation the consumer performs on the body it receives.
        revalidated = ElasticAggregatePayload.model_validate_json(payload.model_dump_json())
        assert revalidated.schema_version == "elastic-aggregates-1"
        assert revalidated.index_pattern == "talend-esb-monitoring-*"
        assert revalidated.window_days == 90
        assert revalidated.window_end == "2026-09-10T12:00:00Z"

        row = _row(revalidated, _JOB)
        assert (row.message_count, row.error_count) == (3, 1)
        # `last_seen` becomes `ObservationRecord.last_run_at`, a string field, so
        # its format is contractual: second-precision UTC with a Z, matching the
        # harness's own `_iso`. The newest of the three lines is +2s.
        assert row.last_seen == "2026-09-08T12:00:02Z"

    async def test_the_index_pattern_is_read_from_the_key_a_real_source_uses(
        self, db_session, mirror
    ):
        """`index_names` is the ONLY key a registered source actually carries.

        It is `ElasticRuntimeProvider.scope_selection().field`, so the connect
        dialog and `editable_config_fields` both bind the operator's index
        selection to that name -- a source cannot store it as `index_pattern`
        even if it wanted to. Reading only the other spellings made every live
        source count as `undeclared`, so the payload permanently reported a
        missing index pattern that nobody had failed to supply, and the failure
        was invisible in tests because the fixtures wrote `index_pattern` by
        hand. Regression guard for that integration mismatch.
        """
        await mirror.register_elastic(config={"index_names": ["talend-esb-monitoring-*"]})
        await mirror.message_flow(job_name=_JOB, execution_id="x-1", messages=2, errors=0)

        payload = await _build(db_session)

        assert payload.index_pattern == "talend-esb-monitoring-*"
        # And it must not simultaneously claim the pattern was never declared.
        assert not [item for item in payload.limitations if "no index pattern" in item.lower()]


# --- environment scoping ------------------------------------------------------


class TestEnvironmentScoping:
    """PROD and ACC are seeded to disagree on every figure."""

    async def _two_environments(self, mirror: Mirror) -> None:
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        # PROD: 3 exchanges, 1 message each, no errors, fast.
        for index in range(3):
            await mirror.message_flow(
                job_name=_JOB,
                execution_id=f"prod-{index}",
                environment_id="env-prod",
                environment_name="PROD",
                duration_ms=100,
                messages=1,
                offset_minutes=index,
            )
        # ACC: 2 exchanges, 10 messages each, all errors, slow.
        for index in range(2):
            await mirror.message_flow(
                job_name=_JOB,
                execution_id=f"acc-{index}",
                environment_id="env-acc",
                environment_name="ACC",
                duration_ms=60_000,
                messages=10,
                errors=10,
                offset_minutes=index,
            )

    async def test_unscoped_reads_report_each_environment_separately(
        self, db_session, mirror
    ):
        """Never pooled: one row per (job, environment), each internally coherent.

        A pooled row would report 23 messages / 20 errors at a p50 nobody
        observed -- the same defect class as the 23.1% failure rate that
        described neither PROD (0.8%) nor ACC (60.7%).
        """
        await self._two_environments(mirror)

        payload = await _build(db_session)

        assert {(item.job_name, item.environment) for item in payload.per_job} == {
            (_JOB, "PROD"),
            (_JOB, "ACC"),
        }
        prod = _row(payload, _JOB, "PROD")
        acc = _row(payload, _JOB, "ACC")
        assert (prod.message_count, prod.error_count) == (3, 0)
        assert (acc.message_count, acc.error_count) == (20, 20)
        assert prod.latency_p50_ms == 100
        assert acc.latency_p50_ms == 60_000
        assert sorted(payload.environments) == ["ACC", "PROD"]
        assert _mentions(payload, "No documentation read scope is configured")

    async def test_a_configured_scope_narrows_the_whole_population(
        self, db_session, mirror
    ):
        """One filter, applied to the population -- not per aggregate."""
        await self._two_environments(mirror)
        await mirror.store.write_credentials(
            source_id=mirror.source_id,
            plaintext_payload={"index_pattern": "esb-*", "doc_environment_ids": ["env-prod"]},
            credential_type="elastic",
            rotated_by="1",
        )

        payload = await _build(db_session)

        assert [(item.job_name, item.environment) for item in payload.per_job] == [
            (_JOB, "PROD")
        ]
        prod = _row(payload, _JOB, "PROD")
        # Every figure is PROD's own: the message count, the error count and the
        # latency percentile all describe the same 3 exchanges.
        assert (prod.message_count, prod.error_count) == (3, 0)
        assert (prod.latency_p50_ms, prod.latency_p95_ms) == (100, 100)
        assert payload.environments == ["PROD"]

    async def test_a_run_with_no_environment_is_not_evidence_for_a_scoped_one(
        self, db_session, mirror
    ):
        """"Only PROD" cannot include a run that cannot be shown to be PROD."""
        await mirror.register_elastic(
            config={"index_pattern": "esb-*", "doc_environment_ids": ["env-prod"]}
        )
        await mirror.message_flow(
            job_name=_JOB,
            execution_id="unnamed-1",
            environment_id=None,
            environment_name=None,
            messages=5,
        )

        payload = await _build(db_session)

        assert payload.per_job == []
        assert _mentions(payload, "cannot be shown to belong to one")

    async def test_an_unnamed_environment_is_reported_as_an_empty_string(
        self, db_session, mirror
    ):
        """The contract's own default for "the provider named no environment"."""
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        await mirror.message_flow(
            job_name=_JOB,
            execution_id="unnamed-1",
            environment_id=None,
            environment_name=None,
            messages=4,
        )

        payload = await _build(db_session)

        assert [(item.job_name, item.environment) for item in payload.per_job] == [(_JOB, "")]
        assert _row(payload, _JOB, "").message_count == 4
        assert payload.environments == []

    async def test_an_empty_selection_reads_nothing_rather_than_everything(
        self, db_session, mirror
    ):
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        await mirror.message_flow(job_name=_JOB, execution_id="x-1", messages=9)
        # An explicitly empty list is a deliberate "nothing"; `None` is
        # "everything mirrored". `resolve_doc_read_scope` only reports a
        # selection as configured when it is non-empty, so this asserts the
        # producer's own guard rather than the scope resolver's.
        payload = await ElasticAggregateService(db_session).build_payload(
            project_id=_P, default_window_days=90, now=_NOW
        )
        assert payload.per_job  # sanity: the unscoped read does see the data

        service = ElasticAggregateService(db_session)
        original = service._store.resolve_doc_read_scope

        async def _empty_selection(*args, **kwargs):
            scope = await original(*args, **kwargs)
            return scope.model_copy(update={"environment_ids": [], "configured": True})

        service._store.resolve_doc_read_scope = _empty_selection  # type: ignore[method-assign]
        narrowed = await service.build_payload(
            project_id=_P, default_window_days=90, now=_NOW
        )

        assert narrowed.per_job == []
        assert narrowed.environments == []
        assert _mentions(narrowed, "selects no environment")


# --- stated absence -----------------------------------------------------------


class TestStatedAbsence:
    """Three absences that look identical from the outside, kept distinct."""

    async def test_no_elastic_source_is_a_stated_limitation_not_an_empty_payload(
        self, db_session
    ):
        payload = await _build(db_session)

        assert payload.schema_version == "elastic-aggregates-1"
        assert payload.per_job == []
        assert payload.window_days == 90
        assert payload.window_end == "2026-09-10T12:00:00Z"
        assert _mentions(payload, "No Elastic runtime source is registered")
        # Names the types it looked for, so a connector registered under another
        # key is a visible mismatch rather than a silent absence.
        assert _mentions(payload, "elastic")

    async def test_a_registered_source_with_an_empty_mirror_says_so(
        self, db_session, mirror
    ):
        await mirror.register_elastic(config={"index_pattern": "esb-*"})

        payload = await _build(db_session)

        assert payload.per_job == []
        assert _mentions(payload, "holds no attributable message")
        assert not _mentions(payload, "No Elastic runtime source is registered")

    async def test_messages_no_execution_accounts_for_are_reported(
        self, db_session, mirror
    ):
        """The mirror has traffic but nothing to attribute it to.

        A log line carries `execution_id` and no job name, so the execution is
        the only join to one. Silence here would read as an idle estate.
        """
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        await mirror.message_flow(
            job_name=_JOB,
            execution_id="orphan-1",
            messages=7,
            with_execution=False,
            with_job_run=True,
        )

        payload = await _build(db_session)

        assert payload.per_job == []
        assert _mentions(payload, "no in-scope execution accounts for")
        assert _mentions(payload, "Elastic job run(s) in this window but no")

    async def test_data_outside_the_window_is_absence_not_silence(
        self, db_session, mirror
    ):
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        await mirror.message_flow(
            job_name=_JOB, execution_id="old-1", messages=3, offset_minutes=-60 * 24 * 40
        )

        payload = await _build(db_session, window_days=7)

        assert payload.window_days == 7
        assert payload.per_job == []
        assert _mentions(payload, "holds no attributable message")


# --- percentiles --------------------------------------------------------------


class TestLatencyPercentiles:
    #: mean 5280, median 1800. A mean-fed reader invents a latency profile.
    _DISTRIBUTION = (1000, 1800, 1800, 1800, 20_000)

    async def test_p50_is_the_median_and_not_the_mean(self, db_session, mirror):
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        for index, duration in enumerate(self._DISTRIBUTION):
            await mirror.message_flow(
                job_name=_JOB,
                execution_id=f"lat-{index}",
                duration_ms=duration,
                messages=1,
                offset_minutes=index,
            )

        row = _row(await _build(db_session), _JOB)

        mean = sum(self._DISTRIBUTION) / len(self._DISTRIBUTION)
        assert row.latency_p50_ms == 1800
        assert row.latency_p50_ms != int(mean)
        # Nearest-rank: p95 of 5 samples is the 5th, an observed value.
        assert row.latency_p95_ms == 20_000
        assert row.latency_p95_ms in self._DISTRIBUTION

    async def test_no_mirrored_duration_yields_null_not_an_approximation(
        self, db_session, mirror
    ):
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        await mirror.message_flow(
            job_name=_JOB, execution_id="nodur-1", duration_ms=None, messages=6
        )

        payload = await _build(db_session)
        row = _row(payload, _JOB)

        assert row.message_count == 6
        assert row.latency_p50_ms is None
        assert row.latency_p95_ms is None
        assert _mentions(payload, "report no latency percentile")

    async def test_a_partial_distribution_uses_only_observed_values(
        self, db_session, mirror
    ):
        """Executions with no duration must not become zeros in the sample."""
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        for index, duration in enumerate((None, 4000, None, 4000)):
            await mirror.message_flow(
                job_name=_JOB,
                execution_id=f"mixed-{index}",
                duration_ms=duration,
                messages=1,
                offset_minutes=index,
            )

        row = _row(await _build(db_session), _JOB)

        assert row.latency_p50_ms == 4000
        assert row.latency_p95_ms == 4000


# --- authority separation -----------------------------------------------------


class TestAuthoritySeparation:
    async def test_another_authoritys_rows_are_not_counted_as_messages(
        self, db_session, mirror
    ):
        """TMC and Elastic mirror into the same tables.

        Fusing them into one number is exactly what `runtime_authority` exists
        to prevent, so the source is an authority selector on every aggregate.
        """
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        await mirror.message_flow(job_name=_JOB, execution_id="elastic-1", messages=2)
        await mirror.message_flow(
            job_name=_JOB, execution_id="tmc-1", source_key="tmc-abc", messages=50
        )

        payload = await _build(db_session)

        assert _row(payload, _JOB).message_count == 2

    async def test_lines_that_are_not_attribution_safe_are_excluded(
        self, db_session, mirror
    ):
        """An engine-scoped line may belong to a sibling deployment."""
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        await mirror.message_flow(job_name=_JOB, execution_id="safe-1", messages=2)
        await mirror.message_flow(
            job_name=_JOB,
            execution_id="unsafe-1",
            messages=30,
            errors=30,
            attribution_safe=False,
            offset_minutes=5,
        )

        payload = await _build(db_session)
        row = _row(payload, _JOB)

        assert (row.message_count, row.error_count) == (2, 0)
        assert _mentions(payload, "attribution-safe log lines")

    async def test_another_projects_traffic_is_never_counted(self, db_session, mirror):
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        await mirror.message_flow(job_name=_JOB, execution_id="mine-1", messages=2)
        await mirror.message_flow(
            job_name=_JOB, execution_id="theirs-1", project_id=_OTHER, messages=40
        )

        payload = await _build(db_session)

        assert _row(payload, _JOB).message_count == 2

    async def test_warnings_are_not_errors(self, db_session, mirror):
        """`failure_rate` is derived from `error_count / message_count` downstream."""
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        await mirror.message_flow(job_name=_JOB, execution_id="warn-1", messages=1)
        await mirror.store.upsert_log_event(
            _P,
            RuntimeLogEvent(
                execution_id="warn-1",
                timestamp=_T0,
                severity=RuntimeLogSeverity.WARNING,
                message="retrying",
                line_ordinal=99,
            ),
            mirror.source_key,
            _T0,
        )

        row = _row(await _build(db_session), _JOB)

        assert row.message_count == 2
        assert row.error_count == 0


# --- job-name keying ----------------------------------------------------------


class TestJobNameKeying:
    """The name the estate LOGS UNDER, never a resemblance match.

    `_fuse_elastic` sets `unit_name=""` and does no matching on purpose:
    `AssetCheck_GetLocation` and `AssetCheck_GetLocations` are both real,
    separately-scheduled integrations, and 79 of one project's 429 job rows have
    `job_id != name`. A normaliser anywhere in this lane would merge two
    integrations into one row.
    """

    async def test_sibling_names_stay_separate_rows(self, db_session, mirror):
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        await mirror.message_flow(job_name=_SIBLING, execution_id="s-1", messages=2)
        await mirror.message_flow(
            job_name=_SIBLING_PLURAL, execution_id="p-1", messages=9, offset_minutes=1
        )

        payload = await _build(db_session)

        assert _row(payload, _SIBLING).message_count == 2
        assert _row(payload, _SIBLING_PLURAL).message_count == 9

    async def test_a_scan_map_does_not_rename_the_logged_job(self, db_session, mirror):
        """`runtime_job_scan_maps` answers the opposite question.

        The doc-evidence lane uses it to find the runtime job backing a scanned
        unit. Applying it here would publish a scanned unit's name over a log
        estate's counts and assert a join this payload explicitly does not make.
        """
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        await mirror.message_flow(job_name=_SIBLING, execution_id="s-1", messages=2)
        db_session.add(
            RuntimeJobScanMapRow(
                project_id=_P,
                source_key=mirror.source_key,
                runtime_job_id=f"{_SIBLING}-id",
                static_job_id="SCANNED_UNIT_NAME",
                created_at=_T0,
                created_by="1",
            )
        )
        await db_session.flush()

        payload = await _build(db_session)

        assert [item.job_name for item in payload.per_job] == [_SIBLING]
        assert "SCANNED_UNIT_NAME" not in payload.model_dump_json()
        assert _mentions(payload, "not scanned unit names")


# --- bounds -------------------------------------------------------------------


class TestBounds:
    async def test_the_per_job_row_cap_keeps_the_biggest_and_says_so(
        self, db_session, mirror, monkeypatch
    ):
        monkeypatch.setattr(module, "MAX_PER_JOB_ROWS", 2)
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        for index, volume in enumerate((1, 5, 9)):
            await mirror.message_flow(
                job_name=f"Job_{index}",
                execution_id=f"cap-{index}",
                messages=volume,
                offset_minutes=index,
            )

        payload = await _build(db_session)

        assert [item.job_name for item in payload.per_job] == ["Job_1", "Job_2"]
        assert _mentions(payload, "More than 2 (job, environment) pairs")

    async def test_the_execution_probe_cap_reports_a_floor(
        self, db_session, mirror, monkeypatch
    ):
        monkeypatch.setattr(module, "MAX_PROBED_EXECUTIONS", 2)
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        for index in range(4):
            await mirror.message_flow(
                job_name=_JOB,
                execution_id=f"many-{index}",
                messages=1,
                offset_minutes=index,
            )

        payload = await _build(db_session)

        # Newest two only, and the count says it is a floor rather than a total.
        assert _row(payload, _JOB).message_count == 2
        assert _mentions(payload, "cover the most recent 2")
        assert _mentions(payload, "floor, not a total")

    async def test_the_payload_carries_no_log_body_or_execution_id(
        self, db_session, mirror
    ):
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        await mirror.message_flow(job_name=_JOB, execution_id="secret-exec-id", messages=3)

        body = (await _build(db_session)).model_dump_json()

        assert "secret-exec-id" not in body
        assert "exchange " not in body


# --- correlation --------------------------------------------------------------


class TestCorrelation:
    async def test_a_declared_edge_reaches_the_row(self, db_session, mirror):
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        await mirror.message_flow(job_name=_JOB, execution_id="c-1", messages=2)
        await mirror.correlation_edge(
            job_name=_JOB, correlation_key="Log_CorrelationId", target_job="RHM2Smartflow"
        )

        row = _row(await _build(db_session), _JOB)

        assert row.correlation_key == "Log_CorrelationId"
        assert row.correlated_target_job == "RHM2Smartflow"

    async def test_no_mirrored_correlation_is_stated_not_implied(
        self, db_session, mirror
    ):
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        await mirror.message_flow(job_name=_JOB, execution_id="c-1", messages=2)

        payload = await _build(db_session)
        row = _row(payload, _JOB)

        assert row.correlation_key == ""
        assert row.correlated_target_job == ""
        assert _mentions(payload, "No message correlation is mirrored")

    async def test_several_edges_publish_one_and_report_the_rest(
        self, db_session, mirror
    ):
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        await mirror.message_flow(job_name=_JOB, execution_id="c-1", messages=2)
        await mirror.correlation_edge(
            job_name=_JOB,
            correlation_key="key-old",
            target_job="OldTarget",
            last_seen_at=_T0 - datetime.timedelta(days=5),
        )
        await mirror.correlation_edge(
            job_name=_JOB, correlation_key="key-new", target_job="NewTarget", last_seen_at=_T0
        )

        payload = await _build(db_session)
        row = _row(payload, _JOB)

        assert (row.correlation_key, row.correlated_target_job) == ("key-new", "NewTarget")
        assert _mentions(payload, "more than one mirrored correlation edge")
        # One row per (job, environment): a second row would republish the same
        # message counts under a second key and double-count them.
        assert len(payload.per_job) == 1

    async def test_an_edge_with_no_target_is_dropped(self, db_session, mirror):
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        await mirror.message_flow(job_name=_JOB, execution_id="c-1", messages=2)
        await mirror.correlation_edge(
            job_name=_JOB, correlation_key="key-1", target_job=None
        )

        payload = await _build(db_session)

        assert _row(payload, _JOB).correlation_key == ""
        assert _mentions(payload, "name no target job")

    async def test_a_non_correlation_dependency_is_not_a_correlation(
        self, db_session, mirror
    ):
        """Artifact and connection edges are the table's ordinary content."""
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        await mirror.message_flow(job_name=_JOB, execution_id="c-1", messages=2)
        await mirror.correlation_edge(
            job_name=_JOB,
            correlation_key="conn-1",
            target_job="SomeDatabase",
            dependency_type="connection",
        )

        row = _row(await _build(db_session), _JOB)

        assert row.correlation_key == ""
        assert row.correlated_target_job == ""


# --- shared read --------------------------------------------------------------


class TestSharedRead:
    """One tenant, several documenting projects: widening comes only from grants."""

    async def test_a_grant_widens_the_read_to_the_granted_estate(
        self, db_session, mirror
    ):
        await mirror.register_elastic(
            project_id=_OTHER, label="Shared log estate", config={"index_pattern": "esb-*"}
        )
        await mirror.message_flow(
            job_name=_JOB, execution_id="shared-1", project_id=_OTHER, messages=4
        )

        ungranted = await _build(db_session)
        assert ungranted.per_job == []
        assert _mentions(ungranted, "No Elastic runtime source is registered")

        await mirror.store.add_read_grant(
            source_id=mirror.source_id,
            project_id=_P,
            environment_ids=None,
            window_days=None,
            created_by="1",
        )
        granted = await _build(db_session)

        assert _row(granted, _JOB).message_count == 4
        assert _mentions(granted, "read grant(s)")

    async def test_the_granting_sources_label_never_reaches_the_payload(
        self, db_session, mirror
    ):
        """A label is operator free text: "<customer> PROD Elastic" is typical."""
        await mirror.register_elastic(
            project_id=_OTHER,
            label="ACME Holdings production Elastic",
            config={"index_pattern": "esb-*"},
        )
        await mirror.message_flow(
            job_name=_JOB, execution_id="shared-1", project_id=_OTHER, messages=4
        )
        await mirror.store.add_read_grant(
            source_id=mirror.source_id,
            project_id=_P,
            environment_ids=None,
            window_days=None,
            created_by="1",
        )

        body = (await _build(db_session)).model_dump_json()

        assert "ACME" not in body


# --- index pattern ------------------------------------------------------------


class TestIndexPattern:
    async def test_a_declared_pattern_is_published(self, db_session, mirror):
        await mirror.register_elastic(config={"index_pattern": "talend-esb-monitoring-*"})
        payload = await _build(db_session)
        assert payload.index_pattern == "talend-esb-monitoring-*"

    async def test_a_list_of_indices_is_joined(self, db_session, mirror):
        await mirror.register_elastic(config={"indices": ["esb-a-*", "esb-b-*"]})
        payload = await _build(db_session)
        assert payload.index_pattern == "esb-a-*,esb-b-*"

    async def test_a_credential_shaped_value_is_dropped_and_reported(
        self, db_session, mirror
    ):
        """The one operator-supplied string that crosses into a prompt."""
        await mirror.register_elastic(
            config={"index_pattern": "https://elastic:sup3rs3cret@logs.internal:9200/esb-*"}
        )

        payload = await _build(db_session)

        assert payload.index_pattern == ""
        assert "sup3rs3cret" not in payload.model_dump_json()
        assert _mentions(payload, "did not match the accepted shape")

    async def test_no_declared_pattern_is_stated(self, db_session, mirror):
        await mirror.register_elastic(config={"api_key": "unused-by-this-lane"})
        payload = await _build(db_session)
        assert payload.index_pattern == ""
        assert _mentions(payload, "declare no index pattern")

    async def test_an_unreadable_config_does_not_break_the_payload(
        self, db_session, mirror
    ):
        await mirror.register_elastic(config=None)
        await mirror.message_flow(job_name=_JOB, execution_id="x-1", messages=2)

        payload = await _build(db_session)

        assert payload.index_pattern == ""
        assert _row(payload, _JOB).message_count == 2
        assert _mentions(payload, "could not be read")

    async def test_no_credential_material_reaches_the_payload(self, db_session, mirror):
        await mirror.register_elastic(
            config={"index_pattern": "esb-*", "api_key": "sup3rs3cret", "password": "hunter2"}
        )
        await mirror.message_flow(job_name=_JOB, execution_id="x-1", messages=2)

        body = (await _build(db_session)).model_dump_json()

        assert "sup3rs3cret" not in body
        assert "hunter2" not in body
        assert "api_key" not in body


# --- window -------------------------------------------------------------------


class TestWindow:
    async def test_a_configured_window_wins_over_the_caller_default(
        self, db_session, mirror
    ):
        """The operator's window is a deliberate statement, not a suggestion."""
        await mirror.register_elastic(
            config={"index_pattern": "esb-*", "doc_window_days": 7}
        )
        await mirror.message_flow(job_name=_JOB, execution_id="recent-1", messages=1)
        await mirror.message_flow(
            job_name=_JOB,
            execution_id="old-1",
            messages=50,
            offset_minutes=-60 * 24 * 30,
        )

        payload = await _build(db_session, window_days=90)

        assert payload.window_days == 7
        assert _row(payload, _JOB).message_count == 1

    async def test_the_default_window_is_labelled_as_a_default(self, db_session, mirror):
        await mirror.register_elastic(config={"index_pattern": "esb-*"})
        payload = await _build(db_session, window_days=30)
        assert payload.window_days == 30
        assert _mentions(payload, "the default 30 day(s)")
