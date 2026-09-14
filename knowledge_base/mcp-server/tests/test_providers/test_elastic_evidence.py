"""Tests for the log-estate (Elastic) producer and its wiring into the fusion.

The defect these pin shut: `_stage_runtime_authority` passed a hardcoded `None`
as the Elastic payload, so `fuse_runtime_authorities` recorded the authority
`unavailable` with `no_elastic_source` on EVERY run and every enterprise dossier
stated that message volumes and end-to-end latency were unavailable, whatever
the estate actually held.

Fixing that must not buy a worse failure, so the assertions here are about what
the run PUBLISHES (`_analysis/_runtime_authority.json`), not about how many
times a mock was called:

* a real payload must make the authority available and its figures must arrive
  with `authority: elastic` provenance, never merged into the TMC rows;
* a fetch that fails, times out or was never configured must leave the
  authority `unavailable` with `no_elastic_source` -- and the run must still
  write the bundle. "We could not see it" and "it did not run" are different
  statements and only the first one is true;
* an empty-but-valid body must NOT read as a healthy idle estate;
* a malformed body must be reported as `elastic_payload_invalid` by the
  fusion's own validator, not silently dropped by a second one on this side.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server.providers.base import DocGenContext
from mcp_server.providers.custom import elastic_evidence as module
from mcp_server.providers.custom.enterprise.layout import RUNTIME_AUTHORITY_JSON
from mcp_server.providers.custom.enterprise.phase_runner import EnterpriseRunner
from mcp_server.providers.custom.runtime_authority import (
    CODE_ELASTIC_PAYLOAD_INVALID,
    CODE_NO_ELASTIC_SOURCE,
    CODE_TMC_OBSERVABILITY_DI_ONLY,
    ELASTIC,
    ELASTIC_SCHEMA_VERSION,
    TMC,
)
from mcp_server.providers.custom.runtime_evidence import (
    RUNTIME_CACHE_JSON,
    RuntimeEvidenceStore,
    RuntimeMode,
    RuntimeUnitEvidence,
)
from mcp_server.services.generation_profile import normalize_profile

#: Phrases that would turn unavailability into a claim about behaviour.
_FORBIDDEN_PHRASES = ("never executed", "not executed", "never ran", "never runs")


# --- HTTP fake ----------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
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
        self._recorder.append({"url": url, "params": dict(params or []), "headers": dict(headers or {})})
        return self._handler(url, dict(params or []))


def _install_http(monkeypatch, handler) -> list:
    """Patch httpx AT THE CONSUMING MODULE, per the project test convention."""
    recorder: list = []
    monkeypatch.setattr(
        module,
        "httpx",
        SimpleNamespace(AsyncClient=lambda **_kwargs: _FakeClient(handler, recorder)),
    )
    return recorder


def _ok(payload: Any):
    return lambda _url, _params: _FakeResponse(payload)


def _status(code: int):
    return lambda _url, _params: _FakeResponse({}, status_code=code)


def _boom(_url, _params):
    raise RuntimeError("connection refused")


# --- payloads -----------------------------------------------------------------


def _elastic_body(**overrides: Any) -> dict[str, Any]:
    """A well-formed `elastic-aggregates-1` body."""
    body: dict[str, Any] = {
        "schema_version": ELASTIC_SCHEMA_VERSION,
        "index_pattern": "talend-esb-monitoring-*",
        "window_days": 240,
        "window_end": "2026-09-09T00:00:00Z",
        "environments": ["PROD"],
        "per_job": [
            {
                "job_name": "Inbound_Orders_Route",
                "environment": "PROD",
                "message_count": 184_233,
                "error_count": 412,
                "last_seen": "2026-09-08T23:59:01Z",
                "latency_p50_ms": 340,
                "latency_p95_ms": 2_780,
                "correlation_key": "order_id",
                "correlated_target_job": "Orders_Load",
            }
        ],
        "limitations": [],
    }
    body.update(overrides)
    return body


def _tmc_cache(staging: Path) -> None:
    """Write the TMC cache the fusion's other side reads."""
    store = RuntimeEvidenceStore(
        window_days=90,
        by_unit={
            "Orders_Load": RuntimeUnitEvidence(
                unit_name="Orders_Load",
                job_name="Orders_Load",
                source_key="tmc:orders",
                resolved_by="exact_name",
                environments=("PROD",),
                total_runs=1_204,
                success_runs=1_190,
                failed_runs=14,
                unfinished_runs=0,
                failure_rate=0.0116,
                current_failure_streak=0,
                first_run_at="2026-06-11T08:30:20Z",
                last_run_at="2026-09-08T23:45:10Z",
                runs_per_day=13.4,
                p50_duration_ms=41_000,
                p95_duration_ms=95_500,
                max_duration_ms=612_000,
                plans=("nightly_plan",),
                recent_failures=(),
                matched_alias="Orders_Load",
            )
        },
        unmatched_units=("Inbound_Orders_Route",),
        requested_units=2,
        batches=1,
        source_key="tmc:orders",
        mode=RuntimeMode.DATA,
    )
    path = staging / RUNTIME_CACHE_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store.to_json()), encoding="utf-8")


# --- runner harness -----------------------------------------------------------


async def _quiet(*_args) -> None:
    return None


def _runner(tmp_path: Path) -> EnterpriseRunner:
    """An enterprise runner wired for `_stage_runtime_authority` alone.

    `prepare()` does the rest (workspace staging, method, ledgers); none of it
    is what this module changed, and staging a whole synthetic estate to reach
    one fusion call would make the failure mode of these tests unrelated to
    their subject.
    """
    staging = tmp_path / "docs_staging"
    staging.mkdir(parents=True, exist_ok=True)
    ctx = DocGenContext(
        project_id="orders",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["Orders"],
        total_jobs=2,
        total_groups=1,
        effective_model="opencode",
        write_model="opencode",
        platform="talend",
        analysis_guide_file="_analysis_guide.md",
        generation_profile=normalize_profile({"contract": "talend-enterprise-v1"}),
    )
    runner = EnterpriseRunner(
        SimpleNamespace(model_route="ollama/test"),
        resolved="opencode",
        ctx=ctx,
        facts=SimpleNamespace(unit_names=lambda: ["Orders_Load", "Inbound_Orders_Route"]),
        ledger=None,
        log_cb=_quiet,
        on_complete=lambda *_a: None,
        profile=ctx.generation_profile,
        scan_output_dir=tmp_path / "scan-output",
    )
    # `kind` is what `_apply_unit_kind_limitations` reads: TMC's execution model
    # describes tasks, so a Camel route's columns do not mean what a DI job's do.
    runner.exact = SimpleNamespace(
        units={
            "Orders_Load": {"kind": "job"},
            "Inbound_Orders_Route": {"kind": "route"},
        }
    )
    _tmc_cache(staging)
    return runner


def _bundle(runner: EnterpriseRunner) -> dict[str, Any]:
    path = Path(runner.staging) / RUNTIME_AUTHORITY_JSON
    assert path.is_file(), "the bundle must be written on every path, including failure"
    return json.loads(path.read_text(encoding="utf-8"))


def _codes(bundle: dict[str, Any]) -> list[str]:
    return [item["code"] for item in bundle["limitations"]]


def _observations(bundle: dict[str, Any], authority: str) -> list[dict[str, Any]]:
    return [
        item
        for item in bundle["observations"]
        if item["provenance"]["authority"] == authority
    ]


# --- fetcher: absence is never an error --------------------------------------


async def test_a_disabled_lane_makes_no_request_at_all(monkeypatch):
    recorder = _install_http(monkeypatch, _ok(_elastic_body()))
    monkeypatch.setattr(module.config, "elastic_evidence_enabled", False)

    assert await module.fetch_elastic_aggregates("orders") is None
    assert recorder == [], "a disabled lane must not touch the network"


async def test_a_cleared_endpoint_path_makes_no_request(monkeypatch):
    recorder = _install_http(monkeypatch, _ok(_elastic_body()))
    monkeypatch.setattr(module.config, "elastic_evidence_path", "")

    assert await module.fetch_elastic_aggregates("orders") is None
    assert recorder == []


async def test_an_unset_backend_url_makes_no_request(monkeypatch):
    recorder = _install_http(monkeypatch, _ok(_elastic_body()))
    monkeypatch.setattr(module.config, "rag_base_url", "")

    assert module.endpoint_url() == ""
    assert await module.fetch_elastic_aggregates("orders") is None
    assert recorder == []


async def test_live_evidence_off_makes_no_request(monkeypatch):
    recorder = _install_http(monkeypatch, _ok(_elastic_body()))

    assert await module.fetch_elastic_aggregates("orders", live_evidence=False) is None
    assert recorder == []


@pytest.mark.parametrize("status", [204, 404])
async def test_a_project_with_no_log_estate_is_absence_not_failure(monkeypatch, status):
    _install_http(monkeypatch, _status(status))
    assert await module.fetch_elastic_aggregates("orders") is None


async def test_a_transport_failure_returns_none_and_does_not_raise(monkeypatch):
    _install_http(monkeypatch, _boom)
    assert await module.fetch_elastic_aggregates("orders") is None


async def test_a_server_error_returns_none_and_does_not_raise(monkeypatch):
    _install_http(monkeypatch, _status(503))
    assert await module.fetch_elastic_aggregates("orders") is None


async def test_a_null_body_is_not_an_idle_estate(monkeypatch):
    _install_http(monkeypatch, _ok(None))
    assert await module.fetch_elastic_aggregates("orders") is None


async def test_a_non_object_body_is_refused(monkeypatch):
    """Same rule as `load_tmc_store_payload`: a non-object body is not the contract."""
    _install_http(monkeypatch, _ok([{"job_name": "Orders_Load"}]))
    assert await module.fetch_elastic_aggregates("orders") is None


# --- fetcher: the request itself ---------------------------------------------


async def test_the_request_carries_the_project_the_window_and_the_machine_key(monkeypatch):
    recorder = _install_http(monkeypatch, _ok(_elastic_body()))
    monkeypatch.setattr(module.config, "rag_base_url", "http://rag:8000/")
    monkeypatch.setattr(module.config, "elastic_evidence_path", "/api/v1/runtime/elastic-aggregates")
    monkeypatch.setattr(module.config, "ingest_api_key", "machine-key")
    monkeypatch.setattr(module.config, "elastic_evidence_window_days", 0)
    monkeypatch.setattr(module.config, "runtime_evidence_window_days", 90)

    payload = await module.fetch_elastic_aggregates("orders")

    assert payload is not None and payload["index_pattern"] == "talend-esb-monitoring-*"
    assert len(recorder) == 1, "one project-scoped read, never a per-unit fan-out"
    sent = recorder[0]
    assert sent["url"] == "http://rag:8000/api/v1/runtime/elastic-aggregates"
    assert sent["params"] == {"project_id": "orders", "window_days": "90"}
    assert sent["headers"]["Authorization"] == "Bearer machine-key"
    # No environment travels from this side: the read scope is resolved
    # server-side, exactly as `GET /runtime/doc-evidence` resolves it, so the
    # two authorities cannot end up with two copies of one rule.
    assert "environment_ids" not in sent["params"]


async def test_the_window_inherits_tmc_unless_deliberately_widened(monkeypatch):
    recorder = _install_http(monkeypatch, _ok(_elastic_body()))
    monkeypatch.setattr(module.config, "runtime_evidence_window_days", 90)
    monkeypatch.setattr(module.config, "elastic_evidence_window_days", 240)

    await module.fetch_elastic_aggregates("orders")

    assert recorder[0]["params"]["window_days"] == "240"


async def test_a_path_without_a_leading_slash_is_still_addressable(monkeypatch):
    recorder = _install_http(monkeypatch, _ok(_elastic_body()))
    monkeypatch.setattr(module.config, "rag_base_url", "http://rag:8000")
    monkeypatch.setattr(module.config, "elastic_evidence_path", "api/v1/runtime/elastic-aggregates")

    await module.fetch_elastic_aggregates("orders")

    assert recorder[0]["url"] == "http://rag:8000/api/v1/runtime/elastic-aggregates"


def test_per_job_count_never_raises_on_a_hostile_body():
    assert module.per_job_count(None) == 0
    assert module.per_job_count({"per_job": "not-a-list"}) == 0
    assert module.per_job_count({"per_job": [{}, {}]}) == 2


# --- wiring: what the run publishes ------------------------------------------


async def test_a_valid_payload_reaches_the_fusion_and_the_authority_is_available(
    monkeypatch, tmp_path
):
    _install_http(monkeypatch, _ok(_elastic_body()))
    runner = _runner(tmp_path)

    await runner._stage_runtime_authority()
    bundle = _bundle(runner)

    assert bundle["authorities"][ELASTIC] == "data"
    assert CODE_NO_ELASTIC_SOURCE not in _codes(bundle)
    elastic_rows = _observations(bundle, ELASTIC)
    assert [row["job_name"] for row in elastic_rows] == ["Inbound_Orders_Route"]
    row = elastic_rows[0]
    # Messages, not executions -- and carrying the log estate's own provenance.
    assert row["total_runs"] == 184_233 and row["failed_runs"] == 412
    assert row["p95_duration_ms"] == 2_780
    assert row["provenance"]["source_key"] == "talend-esb-monitoring-*"
    assert row["provenance"]["window_days"] == 240
    assert row["provenance"]["read_scope"] == ["PROD"]
    # Never merged: the TMC row for the same estate keeps its own numbers.
    tmc_rows = _observations(bundle, TMC)
    assert [r["unit_name"] for r in tmc_rows] == ["Orders_Load"]
    assert tmc_rows[0]["total_runs"] == 1_204
    # The one figure TMC cannot produce at all now exists.
    assert bundle["correlation_latency"], "a correlated pair must yield a latency record"
    latency = bundle["correlation_latency"][0]
    assert latency["source_unit"] == "Inbound_Orders_Route"
    assert latency["target_unit"] == "Orders_Load"
    assert latency["provenance"]["authority"] == ELASTIC


async def test_a_failed_fetch_leaves_the_authority_unavailable_and_the_run_continues(
    monkeypatch, tmp_path
):
    _install_http(monkeypatch, _boom)
    runner = _runner(tmp_path)

    await runner._stage_runtime_authority()
    bundle = _bundle(runner)

    assert bundle["authorities"][ELASTIC] == "unavailable"
    assert CODE_NO_ELASTIC_SOURCE in _codes(bundle)
    assert _observations(bundle, ELASTIC) == []
    assert bundle["correlation_latency"] == []
    # The run is not degraded elsewhere: TMC still published its figures.
    assert bundle["authorities"][TMC] == "data"
    assert _observations(bundle, TMC)
    text = json.dumps(bundle).lower()
    for phrase in _FORBIDDEN_PHRASES:
        assert phrase not in text, f"unavailability must never read as {phrase!r}"


async def test_a_malformed_payload_is_reported_as_elastic_payload_invalid(
    monkeypatch, tmp_path
):
    """A JSON object that is not the contract must be named, not swallowed.

    Validation belongs to `ElasticAggregatePayload` at the fusion boundary. If
    this side pre-parsed it, the malformed body would come back as
    `no_elastic_source` -- "none was supplied" -- which is a different and
    false statement about the run's inputs.
    """
    _install_http(monkeypatch, _ok(_elastic_body(per_job=[{"environment": "PROD"}])))
    runner = _runner(tmp_path)

    await runner._stage_runtime_authority()
    bundle = _bundle(runner)

    assert bundle["authorities"][ELASTIC] == "unavailable"
    assert CODE_ELASTIC_PAYLOAD_INVALID in _codes(bundle)
    assert CODE_NO_ELASTIC_SOURCE not in _codes(bundle)
    assert _observations(bundle, ELASTIC) == []


async def test_a_wrong_schema_version_is_invalid_not_absent(monkeypatch, tmp_path):
    _install_http(monkeypatch, _ok(_elastic_body(schema_version="elastic-aggregates-2")))
    runner = _runner(tmp_path)

    await runner._stage_runtime_authority()

    assert CODE_ELASTIC_PAYLOAD_INVALID in _codes(_bundle(runner))


async def test_an_unconfigured_lane_is_a_clean_no_op(monkeypatch, tmp_path):
    recorder = _install_http(monkeypatch, _ok(_elastic_body()))
    monkeypatch.setattr(module.config, "elastic_evidence_enabled", False)
    runner = _runner(tmp_path)

    await runner._stage_runtime_authority()
    bundle = _bundle(runner)

    assert recorder == [], "no configuration must mean no network call"
    assert bundle["authorities"][ELASTIC] == "unavailable"
    assert CODE_NO_ELASTIC_SOURCE in _codes(bundle)
    assert bundle["authorities"][TMC] == "data"


async def test_an_empty_but_valid_body_is_unavailable_not_a_healthy_estate(
    monkeypatch, tmp_path
):
    """Zero rows is not zero traffic.

    The endpoint answering with a conforming body that carries no per-job
    aggregate means the query matched nothing. Publishing that as `data` with
    an empty table would let a reader conclude the estate is idle.
    """
    _install_http(monkeypatch, _ok(_elastic_body(per_job=[])))
    runner = _runner(tmp_path)

    await runner._stage_runtime_authority()
    bundle = _bundle(runner)

    assert bundle["authorities"][ELASTIC] == "unavailable"
    assert _observations(bundle, ELASTIC) == []


async def test_producer_reported_limitations_downgrade_the_authority_to_partial(
    monkeypatch, tmp_path
):
    _install_http(
        monkeypatch,
        _ok(_elastic_body(limitations=["index rolled over mid-window"])),
    )
    runner = _runner(tmp_path)

    await runner._stage_runtime_authority()
    bundle = _bundle(runner)

    assert bundle["authorities"][ELASTIC] == "partial"
    messages = [item["message"] for item in bundle["limitations"]]
    assert any("index rolled over mid-window" in message for message in messages)


async def test_the_route_di_only_limitation_is_emitted_once_and_stays_tmc_scoped(
    monkeypatch, tmp_path
):
    """Routes are DI-only in TMC; Elastic is the authority that can see them.

    Supplying a real Elastic payload must not double-report that limitation nor
    contradict it: it is scoped to TMC by construction, and the log estate adds
    the message-volume row TMC cannot produce rather than a second caveat about
    the same columns.
    """
    _install_http(monkeypatch, _ok(_elastic_body()))
    runner = _runner(tmp_path)

    await runner._stage_runtime_authority()
    bundle = _bundle(runner)

    route_limits = [
        item
        for item in bundle["limitations"]
        if item["code"] == CODE_TMC_OBSERVABILITY_DI_ONLY
    ]
    assert len(route_limits) == 1, "one route, one limitation -- never one per authority"
    assert route_limits[0]["unit_name"] == "Inbound_Orders_Route"
    assert route_limits[0]["authority"] == TMC
    # And the note that would contradict a present log estate is gone.
    notes = " ".join(bundle["authority_notes"])
    assert "With no log estate supplied" not in notes
    assert "Log-estate counts are MESSAGES" in notes


async def test_the_module_being_absent_leaves_only_the_elastic_lane_unavailable(
    monkeypatch, tmp_path
):
    """A build without the producer must still publish the TMC authority.

    The pre-existing `ImportError` fallback writes a stub that declares BOTH
    authorities unavailable; the Elastic import must not be able to trigger it.
    """
    import sys

    # A payload that WOULD have produced `data`, so the assertions below can
    # only pass if the import genuinely failed rather than the fetch.
    _install_http(monkeypatch, _ok(_elastic_body()))
    monkeypatch.setitem(sys.modules, "mcp_server.providers.custom.elastic_evidence", None)
    runner = _runner(tmp_path)

    await runner._stage_runtime_authority()
    bundle = _bundle(runner)

    assert bundle["authorities"][TMC] == "data"
    assert _observations(bundle, TMC), "the TMC lane must survive a missing producer"
    assert bundle["authorities"][ELASTIC] == "unavailable"
    assert CODE_NO_ELASTIC_SOURCE in _codes(bundle)
