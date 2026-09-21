"""Wiring tests for the runtime-evidence lane (work package P2).

`runtime_evidence.py` was complete and unit-tested long before any document
contained a runtime fact, because nothing on a default configuration called it:
the custom engine's producer early-returned unless per-unit documents were
switched on, and the OpenCode engine reused the CONSUMER
(`harness._phase3_context_files`) while never calling the producer at all.

These tests therefore assert delivery, not rendering. Each one follows a
sentinel fact from a faked fetch through to the bytes a doc writer receives,
under DEFAULT configuration. Rendering itself is covered by
`test_runtime_evidence.py` (owned by the evidence core) and is not re-asserted
here.

All fixture values are synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mcp_server.config import config
from mcp_server.providers.base import DocGenContext
from mcp_server.providers.custom import harness as harness_module
from mcp_server.providers.custom.harness import CustomHarnessProvider
from mcp_server.providers.custom.models import HarnessRunState
from mcp_server.providers.custom.phase3_slices import _trim_priority
from mcp_server.providers.custom.runtime_evidence import (
    RUNTIME_CACHE_JSON,
    RUNTIME_COVERAGE_JSON,
    RUNTIME_PROFILE_MD,
    RUNTIME_REDACTION_FLAGS_JSON,
    RuntimeEvidenceStore,
    RuntimeMode,
    RuntimeRedactionFlag,
    RuntimeUnitEvidence,
)
from mcp_server.providers.custom.validators import build_facts_manifest

# A string no scanner, prompt template or fixture could produce, so finding it
# in a doc writer's context proves it travelled the whole lane.
SENTINEL = "SENTINEL_RUNTIME_FAILURE_47231"
CAUSAL_SENTINEL = "SENTINEL_DATABASE_CONNECTION_RESET_7319"

GROUP_DOC = "06_OPERATIONS_TESTING_AND_DEBUGGING"
UNIT_DOC = "10_UNIT_REFERENCE__GroupA__Job_GetThing"
UNIT_NAME = "job-id-getthing"
UNIT_DISPLAY = "Job_GetThing"


# --- fixtures -----------------------------------------------------------------


def _scan_result() -> dict:
    return {
        "platform": "talend",
        "jobs": [
            {
                "name": UNIT_DISPLAY,
                "job_id": UNIT_NAME,
                "path": "GroupA/Job_GetThing_1.0.item",
                "group": "GroupA",
                "version": "1.0",
                "components": [
                    {
                        "unique_name": "tDBInput_1",
                        "component_type": "tDBInput",
                        "params": [{"name": "QUERY", "value": "SELECT 1"}],
                    }
                ],
                "connections": [],
            },
            {
                "name": "Job_Other",
                "job_id": "job-id-other",
                "path": "GroupA/Job_Other_1.0.item",
                "group": "GroupA",
                "version": "1.0",
                "components": [],
                "connections": [],
            },
        ],
        "files": [],
    }


@pytest.fixture()
def scan_dir(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "intodoc-output"
    project = root / "proj"
    project.mkdir(parents=True)
    (project / "scan_result.json").write_text(json.dumps(_scan_result()), encoding="utf-8")
    monkeypatch.setattr(config, "intodoc_output_dir", root)
    monkeypatch.setattr(config, "project_root", tmp_path)
    return project


def _ctx(tmp_path: Path, **overrides) -> DocGenContext:
    staging = Path(overrides.pop("staging", tmp_path / "output" / "proj" / "docs_staging"))
    staging.mkdir(parents=True, exist_ok=True)
    kwargs = dict(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["GroupA"],
        total_jobs=2,
        total_groups=1,
        effective_model="m",
        write_model="m",
        platform="talend",
        analysis_guide_file="",
        run_id=1,
    )
    kwargs.update(overrides)
    return DocGenContext(**kwargs)


def _evidence(unit_name: str = UNIT_NAME) -> RuntimeUnitEvidence:
    return RuntimeUnitEvidence.from_payload(
        {
            "unit_name": unit_name,
            "job_name": UNIT_DISPLAY,
            "source_key": "tmc",
            "resolved_by": "name",
            "environments": ["PROD"],
            "total_runs": 120,
            "success_runs": 100,
            "failed_runs": 20,
            "unfinished_runs": 0,
            "failure_rate": 0.1667,
            "current_failure_streak": 3,
            "first_run_at": "2026-05-01T00:00:00+00:00",
            "last_run_at": "2026-08-20T00:00:00+00:00",
            "runs_per_day": 1.3,
            "p50_duration_ms": 4000,
            "p95_duration_ms": 9000,
            "max_duration_ms": 12000,
            "plans": [],
            "recent_failures": [
                {"failed_at": "2026-08-19T00:00:00+00:00", "error_summary": SENTINEL, "occurrences": 17}
            ],
            "log_quality": {
                "failed_executions": 20,
                "executions_with_logs": 17,
                "diagnosed_executions": 17,
                "capture_ceiling_reached": 0,
            },
            "incident_insights": [
                {
                    "pattern_id": "database.connection_reset",
                    "confidence": "high",
                    "occurrences": 17,
                    "most_recent_at": "2026-08-19T00:00:00+00:00",
                    "failed_component": "tWaitForSqlData_1",
                    "termination_component": "tDie_1",
                    "causal_message": CAUSAL_SENTINEL,
                    "root_cause_candidate": "The database connection was lost before termination.",
                    "next_checks": ["Check database reachability from the runtime engine"],
                }
            ],
        }
    )


def _data_store() -> RuntimeEvidenceStore:
    return RuntimeEvidenceStore(
        window_days=90,
        by_unit={UNIT_NAME: _evidence()},
        unmatched_units=("job-id-other",),
        orphan_runtime_jobs=("SomeDeployedJob",),
        requested_units=2,
        batches=1,
        source_key="tmc",
        mode=RuntimeMode.DATA,
    )


def _install_fetch(monkeypatch, store: RuntimeEvidenceStore) -> list[tuple]:
    """Patch the from-imported symbol on the CONSUMING module.

    `harness.py` does `from ...runtime_evidence import fetch_runtime_evidence`,
    so patching the module of origin has no effect on the bound name the harness
    actually calls. Recording every call also lets a test assert what scope was
    requested.
    """
    calls: list[tuple] = []

    async def _fake(project_id, units, *, live_evidence=True):
        calls.append((project_id, list(units), live_evidence))
        if not live_evidence:
            return RuntimeEvidenceStore(
                window_days=90,
                mode=RuntimeMode.OPTED_OUT,
                requested_units=len(list(units)),
            )
        return store

    monkeypatch.setattr(harness_module, "fetch_runtime_evidence", _fake)
    return calls


def _provider(required_docs: list[str] | None = None) -> CustomHarnessProvider:
    provider = CustomHarnessProvider(MagicMock())
    provider._required_docs = list(required_docs or [GROUP_DOC])
    return provider


def _state(scan_dir: Path) -> HarnessRunState:
    facts = build_facts_manifest("proj", scan_dir)
    return HarnessRunState(project_id="proj", facts_manifest=facts)


class _Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    async def __call__(self, project_id: str, message: str, level: str) -> None:
        self.lines.append((message, level))

    def find(self, needle: str) -> list[tuple[str, str]]:
        return [entry for entry in self.lines if needle in entry[0]]


def _text(files: list[Path]) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace") for path in files if path.is_file()
    )


# --- keystone: the custom engine delivers a runtime fact ----------------------


async def test_custom_engine_delivers_a_runtime_fact_to_a_group_doc(tmp_path, scan_dir, monkeypatch):
    """The whole point: a fact from the runtime source reaches a doc writer."""
    _install_fetch(monkeypatch, _data_store())
    provider = _provider()
    ctx = _ctx(tmp_path)
    state = _state(scan_dir)
    log = _Log()

    await provider._ensure_runtime_evidence(ctx, state, log)
    files = provider._phase3_context_files(ctx, GROUP_DOC)

    assert ctx.staging_dir / RUNTIME_PROFILE_MD in files
    assert SENTINEL in _text(files)


async def test_the_rollup_sits_immediately_after_the_cross_cutting_facts(tmp_path, scan_dir, monkeypatch):
    """Byte-stability: the shared leading context is what the provider caches."""
    _install_fetch(monkeypatch, _data_store())
    provider = _provider()
    ctx = _ctx(tmp_path)
    cross_cutting = ctx.staging_dir / "_analysis" / "_cross_cutting_facts.md"
    cross_cutting.parent.mkdir(parents=True, exist_ok=True)
    cross_cutting.write_text("# Cross-cutting facts\n", encoding="utf-8")

    await provider._ensure_runtime_evidence(ctx, _state(scan_dir), _Log())
    files = provider._phase3_context_files(ctx, GROUP_DOC)

    assert files.index(ctx.staging_dir / RUNTIME_PROFILE_MD) == files.index(cross_cutting) + 1


@pytest.mark.parametrize(
    "doc_name",
    [
        "01_ARCHITECTURE_MAP",
        "02_COMPONENT_INVENTORY",
        "03_DATA_FLOWS_AND_LINEAGE",
        "04_BUSINESS_RULES_AND_TRANSFORMATIONS",
    ],
)
async def test_operational_rollup_does_not_pollute_design_or_mapping_docs(
    tmp_path, scan_dir, monkeypatch, doc_name
):
    _install_fetch(monkeypatch, _data_store())
    provider = _provider([doc_name])
    ctx = _ctx(tmp_path)

    await provider._ensure_runtime_evidence(ctx, _state(scan_dir), _Log())
    files = provider._phase3_context_files(ctx, doc_name)

    assert ctx.staging_dir / RUNTIME_PROFILE_MD not in files
    assert SENTINEL not in _text(files)


async def test_the_executive_summary_also_receives_the_rollup(tmp_path, scan_dir, monkeypatch):
    """A6: that branch returns early, before the shared-facts append.

    It is also the single document most likely to make an unsupported claim
    about reliability, so being the one document with no runtime evidence is
    the worst possible place for this gap.
    """
    _install_fetch(monkeypatch, _data_store())
    provider = _provider([GROUP_DOC, "09_EXECUTIVE_SUMMARY"])
    ctx = _ctx(tmp_path)

    await provider._ensure_runtime_evidence(ctx, _state(scan_dir), _Log())
    files = provider._phase3_context_files(ctx, "09_EXECUTIVE_SUMMARY")

    assert ctx.staging_dir / RUNTIME_PROFILE_MD in files
    assert SENTINEL in _text(files)


# --- keystone: the lane runs on DEFAULT configuration -------------------------


async def test_the_fetch_happens_with_no_per_job_doc_units(tmp_path, scan_dir, monkeypatch):
    """The defect that made the whole lane dead by default.

    `_per_job_doc_units` is populated only when per-unit documents are enabled
    (off by default) or a pack declares `per_job: true`, and the old producer
    early-returned on an empty map. Scope now comes from the facts manifest,
    which is the scoping authority everywhere else in the pipeline.
    """
    calls = _install_fetch(monkeypatch, _data_store())
    provider = _provider()
    assert provider._per_job_doc_units == {}

    await provider._ensure_runtime_evidence(_ctx(tmp_path), _state(scan_dir), _Log())

    assert len(calls) == 1
    requested = calls[0][1]
    assert [unit.name for unit in requested] == ["job-id-getthing", "job-id-other"]


async def test_both_identifier_keys_reach_the_fetch(tmp_path, scan_dir, monkeypatch):
    """A1: manifest units are passed through whole, not reduced to names.

    `UnitFact.name` is the scanner's `job_id`; the runtime source schedules
    tasks under the DISPLAY name. Sending only the manifest key silently loses
    every unit whose two identifiers differ.
    """
    calls = _install_fetch(monkeypatch, _data_store())
    await _provider()._ensure_runtime_evidence(_ctx(tmp_path), _state(scan_dir), _Log())

    unit = next(u for u in calls[0][1] if u.name == UNIT_NAME)
    assert unit.display_name == UNIT_DISPLAY


# --- keystone: absence is never silent ----------------------------------------


async def test_coverage_artifact_exists_when_the_fetch_fails(tmp_path, scan_dir, monkeypatch):
    unavailable = RuntimeEvidenceStore(
        window_days=90,
        mode=RuntimeMode.UNAVAILABLE,
        requested_units=2,
        unretrieved_units=(UNIT_NAME, "job-id-other"),
        batches=1,
        failed_batches=1,
        partial=True,
    )
    _install_fetch(monkeypatch, unavailable)
    ctx = _ctx(tmp_path)

    await _provider()._ensure_runtime_evidence(ctx, _state(scan_dir), _Log())

    payload = json.loads((ctx.staging_dir / RUNTIME_COVERAGE_JSON).read_text(encoding="utf-8"))
    assert payload["mode"] == "unavailable"
    assert payload["units_matched"] == 0
    # And the rollup still says so in words, so no document can read silence as
    # "these jobs are idle".
    profile = (ctx.staging_dir / RUNTIME_PROFILE_MD).read_text(encoding="utf-8")
    assert "unavailable" in profile
    assert profile.strip()


async def test_a_raising_fetch_degrades_instead_of_failing_the_run(tmp_path, scan_dir, monkeypatch):
    async def _boom(project_id, units, *, live_evidence=True):
        raise RuntimeError("transport exploded")

    monkeypatch.setattr(harness_module, "fetch_runtime_evidence", _boom)
    ctx = _ctx(tmp_path)

    store = await _provider()._ensure_runtime_evidence(ctx, _state(scan_dir), _Log())

    assert store.mode is RuntimeMode.UNAVAILABLE
    assert (ctx.staging_dir / RUNTIME_COVERAGE_JSON).is_file()
    assert (ctx.staging_dir / RUNTIME_PROFILE_MD).is_file()


async def test_the_three_outcomes_are_distinguishable_in_the_run_log(tmp_path, scan_dir, monkeypatch):
    """Two of the three used to be entirely silent on a green run."""
    state = _state(scan_dir)

    _install_fetch(monkeypatch, _data_store())
    fetched = _Log()
    await _provider()._ensure_runtime_evidence(
        _ctx(tmp_path, staging=tmp_path / "a" / "docs_staging"), state, fetched
    )
    assert fetched.find("runtime evidence: FETCHED")

    opted = _Log()
    await _provider()._ensure_runtime_evidence(
        _ctx(tmp_path, staging=tmp_path / "b" / "docs_staging", live_evidence=False), state, opted
    )
    assert opted.find("runtime evidence: OPTED OUT")

    _install_fetch(
        monkeypatch,
        RuntimeEvidenceStore(
            window_days=90, mode=RuntimeMode.UNAVAILABLE, requested_units=2,
            batches=1, failed_batches=1,
        ),
    )
    down = _Log()
    await _provider()._ensure_runtime_evidence(
        _ctx(tmp_path, staging=tmp_path / "c" / "docs_staging"), state, down
    )
    assert down.find("runtime evidence: UNAVAILABLE")

    messages = {fetched.lines[0][0], opted.lines[0][0], down.lines[0][0]}
    assert len(messages) == 3


async def test_redaction_flags_are_written_only_when_there_are_any(tmp_path, scan_dir, monkeypatch):
    ctx = _ctx(tmp_path)
    _install_fetch(monkeypatch, _data_store())
    await _provider()._ensure_runtime_evidence(ctx, _state(scan_dir), _Log())
    assert not (ctx.staging_dir / RUNTIME_REDACTION_FLAGS_JSON).exists()

    flagged = _ctx(tmp_path, staging=tmp_path / "flagged" / "docs_staging")
    store = RuntimeEvidenceStore(
        window_days=90,
        by_unit={UNIT_NAME: _evidence()},
        requested_units=1,
        source_key="tmc",
        redaction_flags=(
            RuntimeRedactionFlag(unit_name=UNIT_NAME, kind="fqdn", excerpt="host <flagged> refused"),
        ),
    )
    _install_fetch(monkeypatch, store)
    log = _Log()
    await _provider()._ensure_runtime_evidence(flagged, _state(scan_dir), log)
    assert (flagged.staging_dir / RUNTIME_REDACTION_FLAGS_JSON).is_file()
    assert log.find("identifier shape")


# --- A5: the rollup must NOT reach a unit-scoped document ---------------------


async def test_a_unit_doc_gets_its_own_section_but_not_the_project_rollup(tmp_path, scan_dir, monkeypatch):
    """A5, and the reason is a fabrication false positive, not tidiness.

    The rollup's failure-mode census names components belonging to OTHER units.
    Phase 3 component-echo validates a unit document against its own target
    bundle alone, so a unit-doc writer echoing one of those is rejected as an
    invention even though the component is real -- burning repair budget and
    producing salvage stubs.
    """
    _install_fetch(monkeypatch, _data_store())
    provider = _provider([UNIT_DOC])
    provider._per_job_doc_units = {UNIT_DOC: UNIT_NAME}
    ctx = _ctx(tmp_path)

    await provider._ensure_runtime_evidence(ctx, _state(scan_dir), _Log())
    files = provider._phase3_context_files(ctx, UNIT_DOC)

    assert ctx.staging_dir / RUNTIME_PROFILE_MD not in files
    bundle = ctx.staging_dir / "_analysis" / f"_evidence_{UNIT_DOC}.md"
    assert bundle in files
    body = bundle.read_text(encoding="utf-8")
    assert "## RUNTIME EVIDENCE" in body
    assert SENTINEL in body
    assert CAUSAL_SENTINEL in body
    assert "originating component `tWaitForSqlData_1` -> termination/reporting component `tDie_1`" in body


async def test_the_evidence_builder_rehydrates_from_the_staging_cache(tmp_path, scan_dir, monkeypatch):
    """T4: no constructor injection, so a cached builder cannot freeze `None`.

    The builder is cached per staging dir. Constructed before the fetch -- which
    is what happens whenever any earlier step needs a bundle -- an injected
    store pins the whole run to "no runtime data".
    """
    from mcp_server.providers.custom.target_evidence import TargetEvidenceBuilder

    _install_fetch(monkeypatch, _data_store())
    ctx = _ctx(tmp_path)

    builder = TargetEvidenceBuilder(
        scan_output_dir=scan_dir,
        staging_dir=ctx.staging_dir,
        max_bytes=200_000,
        runtime_section_bytes=8_000,
    )
    # Built BEFORE the fetch: no runtime section, and no crash.
    before = builder.build(UNIT_DOC, UNIT_NAME)
    assert before is not None
    assert SENTINEL not in before.path.read_text(encoding="utf-8")

    await _provider()._ensure_runtime_evidence(ctx, _state(scan_dir), _Log())

    fresh = TargetEvidenceBuilder(
        scan_output_dir=scan_dir,
        staging_dir=ctx.staging_dir,
        max_bytes=200_000,
        runtime_section_bytes=8_000,
    )
    after = fresh.build(UNIT_DOC, UNIT_NAME)
    assert after is not None
    assert SENTINEL in after.path.read_text(encoding="utf-8")


def test_an_unreadable_runtime_cache_degrades_to_no_section(tmp_path, scan_dir):
    from mcp_server.providers.custom.target_evidence import TargetEvidenceBuilder

    staging = tmp_path / "output" / "proj" / "docs_staging"
    cache = staging / RUNTIME_CACHE_JSON
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("{not json", encoding="utf-8")

    builder = TargetEvidenceBuilder(
        scan_output_dir=scan_dir,
        staging_dir=staging,
        max_bytes=200_000,
        runtime_section_bytes=8_000,
    )
    evidence = builder.build(UNIT_DOC, UNIT_NAME)
    assert evidence is not None
    assert "## RUNTIME EVIDENCE" not in evidence.path.read_text(encoding="utf-8")


# --- A10: the once-per-run guard is in memory, not on disk --------------------


async def test_the_fetch_is_idempotent_within_one_run(tmp_path, scan_dir, monkeypatch):
    calls = _install_fetch(monkeypatch, _data_store())
    provider = _provider()
    ctx = _ctx(tmp_path)
    state = _state(scan_dir)

    first = await provider._ensure_runtime_evidence(ctx, state, _Log())
    second = await provider._ensure_runtime_evidence(ctx, state, _Log())

    assert first is second
    assert len(calls) == 1


async def test_a_resumed_run_refetches_instead_of_adopting_the_old_snapshot(tmp_path, scan_dir, monkeypatch):
    """A10. Staging survives a resume, so an on-disk existence guard would pin
    a failed run's `mode: unavailable` for every subsequent resume."""
    _install_fetch(
        monkeypatch,
        RuntimeEvidenceStore(
            window_days=90, mode=RuntimeMode.UNAVAILABLE, requested_units=2,
            batches=1, failed_batches=1,
        ),
    )
    ctx = _ctx(tmp_path)
    state = _state(scan_dir)
    await _provider()._ensure_runtime_evidence(ctx, state, _Log())
    assert (ctx.staging_dir / RUNTIME_PROFILE_MD).is_file()

    # Resume: same staging dir, a fresh provider instance, backend healthy again.
    calls = _install_fetch(monkeypatch, _data_store())
    resumed = _ctx(tmp_path, resume_state={"phase": "phase3_docs"}, run_id=2)
    store = await _provider()._ensure_runtime_evidence(resumed, state, _Log())

    assert len(calls) == 1
    assert store.mode is RuntimeMode.DATA
    assert SENTINEL in (ctx.staging_dir / RUNTIME_PROFILE_MD).read_text(encoding="utf-8")


# --- A6: trim priority --------------------------------------------------------


def test_the_rollup_is_trimmed_as_a_whole_file_not_by_section():
    """At the default rank the coverage header could survive while the
    unobserved-units list is cut -- precisely the silence the lane prevents."""
    assert _trim_priority("_runtime_profile.md") == 0
    # And it must earn rank 0 by name, not by colliding with the per-doc bundle
    # convention: `_evidence_` in a filename auto-promotes.
    assert "_evidence_" not in RUNTIME_PROFILE_MD


# --- A13: runs that are not a plain full run ----------------------------------


async def test_a_scoped_run_records_that_it_describes_more_than_it_rewrote(tmp_path, scan_dir, monkeypatch):
    """`_apply_scoped_docs` narrows the doc set but not the unit set.

    Narrowing the fetch instead was rejected: the rollup's coverage denominator
    would then disagree with the sibling documents preserved byte-stable from
    the baseline. So the record stays project-wide and names the discrepancy.
    """
    _install_fetch(monkeypatch, _data_store())
    ctx = _ctx(tmp_path, scoped_docs={GROUP_DOC})

    await _provider()._ensure_runtime_evidence(ctx, _state(scan_dir), _Log())

    scope = json.loads(
        (ctx.staging_dir / RUNTIME_COVERAGE_JSON).read_text(encoding="utf-8")
    )["run_scope"]
    assert scope["kind"] == "scoped_docs"
    assert scope["scoped_docs"] == [GROUP_DOC]
    assert scope["covers_all_documents"] is False


async def test_a_partition_run_writes_its_own_scoped_coverage_record(tmp_path, scan_dir, monkeypatch):
    """Each composite sub-project publishes under its own output dir, and the
    already-shipped operator endpoint aggregates the per-sub-project files."""
    _install_fetch(monkeypatch, _data_store())
    partition = SimpleNamespace(name="sub_a", units=(UNIT_NAME,), groups=("GroupA",))
    ctx = _ctx(
        tmp_path,
        staging=tmp_path / "output" / "proj" / "sub_a" / "docs_staging",
        partition=partition,
    )

    await _provider()._ensure_runtime_evidence(ctx, _state(scan_dir), _Log())

    payload = json.loads((ctx.staging_dir / RUNTIME_COVERAGE_JSON).read_text(encoding="utf-8"))
    assert payload["run_scope"]["kind"] == "partition"
    assert payload["run_scope"]["partition"] == "sub_a"
    assert payload["run_scope"]["covers_all_documents"] is True
    # And it lands beside the sub-project's docs, not the upload root.
    assert (ctx.staging_dir / RUNTIME_COVERAGE_JSON).is_file()
    assert not (tmp_path / "output" / "proj" / "docs_staging" / RUNTIME_COVERAGE_JSON).exists()


async def test_the_full_run_scope_is_the_default(tmp_path, scan_dir, monkeypatch):
    _install_fetch(monkeypatch, _data_store())
    ctx = _ctx(tmp_path)
    await _provider()._ensure_runtime_evidence(ctx, _state(scan_dir), _Log())
    payload = json.loads((ctx.staging_dir / RUNTIME_COVERAGE_JSON).read_text(encoding="utf-8"))
    assert payload["run_scope"]["kind"] == "full"


# --- A7: the cache must never publish -----------------------------------------


async def test_the_cache_is_staged_under_transient_so_phase4_destroys_it(tmp_path, scan_dir, monkeypatch):
    """Phase 4 cleanup is a denylist and the swap is a directory rename, so a
    cache written anywhere else would ship its provider failure text into
    `output/{pid}/docs/` and accumulate there."""
    _install_fetch(monkeypatch, _data_store())
    ctx = _ctx(tmp_path)
    await _provider()._ensure_runtime_evidence(ctx, _state(scan_dir), _Log())

    cache = ctx.staging_dir / RUNTIME_CACHE_JSON
    assert cache.is_file()
    assert RUNTIME_CACHE_JSON.startswith("_transient/")
    assert RUNTIME_PROFILE_MD.startswith("_analysis/")
    # The coverage record is the one that must SURVIVE: the operator endpoint
    # reads it off the published tree.
    assert RUNTIME_COVERAGE_JSON.startswith("_assessment/")

    import shutil

    for directory in ("_preprocess", "_analysis", "_transient"):
        target = ctx.staging_dir / directory
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
    assert not cache.exists()
    assert not (ctx.staging_dir / RUNTIME_PROFILE_MD).exists()
    assert (ctx.staging_dir / RUNTIME_COVERAGE_JSON).is_file()


# --- opt-out ------------------------------------------------------------------


async def test_a_code_only_run_still_stages_every_artifact(tmp_path, scan_dir, monkeypatch):
    """`live_evidence=False` is a deliberate opt-out, not an absence of facts:
    the documents must still be told that nothing was measured."""
    _install_fetch(monkeypatch, _data_store())
    provider = _provider()
    ctx = _ctx(tmp_path, live_evidence=False)

    store = await provider._ensure_runtime_evidence(ctx, _state(scan_dir), _Log())
    files = provider._phase3_context_files(ctx, GROUP_DOC)

    assert store.mode is RuntimeMode.OPTED_OUT
    assert ctx.staging_dir / RUNTIME_PROFILE_MD in files
    body = (ctx.staging_dir / RUNTIME_PROFILE_MD).read_text(encoding="utf-8")
    assert "opted_out" in body
    assert SENTINEL not in body
    assert json.loads(
        (ctx.staging_dir / RUNTIME_COVERAGE_JSON).read_text(encoding="utf-8")
    )["mode"] == "opted_out"
