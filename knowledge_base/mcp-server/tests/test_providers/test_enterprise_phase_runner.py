"""End-to-end enterprise runner against a fake OpenCode session.

The fake session is the "model": it reads the assignment from the prompt and
submits canned, grounded payloads through the same facade entry point the MCP
tool uses. That exercises the full-workspace permission contract, durable
ledgers, resume, repair-vs-regenerate, budgets and the publication verdict
without a provider.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcp_server.providers.base import DocGenContext
from mcp_server.providers.custom import elastic_evidence
from mcp_server.providers.custom.enterprise import phase_contracts as pc
from mcp_server.providers.custom.enterprise import phase_runner as runner_mod
from mcp_server.providers.custom.enterprise.evaluation import evaluate_enterprise_talend
from mcp_server.providers.custom.enterprise.layout import (
    KNOWLEDGE_MODEL,
    PHASE_STATE,
    PUBLICATION_VERDICT,
    RAG_KNOWLEDGE_ROOT,
    RUN_RECEIPT,
)
from mcp_server.providers.custom.enterprise.phase_runner import EnterpriseRunner, submit_enterprise_artifact
from mcp_server.providers.opencode_cli import OpenCodeCompatibilityError
from mcp_server.services.generation_profile import normalize_profile
from tests.test_providers import enterprise_fixture as fx

EXPECTED = Path(__file__).parents[1] / "fixtures" / "talend_enterprise" / "expected.json"


class FakeProvider:
    """Stands in for OpenCodeCliProvider._execute_step."""

    def __init__(self, staging: Path, *, fail_first_design: bool = False, retype_first_mapping: bool = False,
                 review_findings: list[dict] | None = None, tokens_per_session: int = 100) -> None:
        self.staging = staging
        self.model_route = "ollama/test"
        self.calls: list[dict] = []
        self.fail_first_design = fail_first_design
        self.retype_first_mapping = retype_first_mapping
        self.review_findings = review_findings
        self.tokens = tokens_per_session
        self._design_attempts = 0
        self._mapping_attempts = 0
        self._review_attempts = 0

    def _submit(self, phase: str, payload: dict) -> dict:
        return submit_enterprise_artifact(self.staging, {"enterprise_phase": phase}, json.dumps(payload))

    async def _execute_step(self, **kwargs):
        contract = kwargs["contracts"][0]
        phase = contract.metadata["enterprise_phase"]
        self.calls.append({"phase": phase, "kind": contract.metadata["kind"], "kwargs": kwargs,
                           "assignment": contract.metadata["assignment_count"],
                           "step": kwargs["step"]})
        assert kwargs["exploration_artifacts"] is None, "enterprise sessions must see the whole workspace"
        # A real session is a subprocess and always yields. Without this the
        # fake runs to completion synchronously, concurrent chunks never
        # interleave, and a step-name race cannot be observed at all.
        await asyncio.sleep(0)
        if kwargs.get("ledger") is not None:
            # Mirrors the real _execute_step: one dispatch permitted per step
            # name, so a name reused across concurrent chunks raises here.
            kwargs["ledger"].dispatch(f"step:{kwargs['step']}")
        if phase == pc.PHASE_DEEP_ANALYZE:
            self._design_attempts += 1
            payload = fx.design_payloads()[0]
            if self.fail_first_design and self._design_attempts == 1:
                payload["designs"][1]["evidence"] = [{"path": fx.ws(fx.LOADER), "locator": "", "quote": "no such text"}]
            self._submit(phase, payload)
        elif phase == pc.PHASE_RECONSTRUCT:
            self._submit(phase, fx.journey_payloads()[0])
        elif phase == pc.PHASE_MODEL:
            self._mapping_attempts += 1
            # A retyped table passes the ledger contract only if we bypass the
            # handler check; emulate a subtle case instead: bad quote on first try.
            payload = fx.model_payloads()[0]
            if self.retype_first_mapping and self._mapping_attempts == 1:
                payload["mappings"][0]["evidence"] = [{"path": fx.ws(fx.LOADER), "locator": "", "quote": "row1.order_id"}]
                # Accepted, then corrupted on disk to simulate an entry that fails
                # assembled validation (repair path, not regenerate path).
                self._submit(phase, payload)
                ledger = pc.PhaseLedger(self.staging, pc.PHASE_MODEL)
                broken = ledger.entries()[f"mapping:{fx.TABLE_ID}"]
                broken["evidence"] = [{"path": fx.ws(fx.LOADER), "locator": "", "quote": "vanished"}]
                ledger.accept(f"mapping:{fx.TABLE_ID}", broken)
                return self._execution()
            self._submit(phase, payload)
        elif phase == pc.PHASE_RECONCILE:
            self._submit(phase, fx.reconcile_payloads()[0])
        elif phase == pc.PHASE_WRITE:
            model = json.loads((self.staging / KNOWLEDGE_MODEL).read_text())
            claim_ids = sorted(d["claim_id"] for d in model["designs"])
            for doc in contract.metadata["assignment"]:
                self._submit(phase, fx.doc_payload(doc, claim_ids))
        elif phase == pc.PHASE_REVIEW:
            self._review_attempts += 1
            findings = self.review_findings if (self.review_findings and self._review_attempts == 1) else []
            self._submit(phase, fx.review_payload(findings))
        return self._execution()

    def _execution(self):
        return SimpleNamespace(session_id="s", events=[], input_tokens=self.tokens // 2, output_tokens=self.tokens // 2,
                               cost_usd=0.01, latency_ms=5, processed_tokens=self.tokens)


def _ctx(tmp_path: Path, profile: dict | None = None) -> DocGenContext:
    staging = tmp_path / "docs_staging"
    staging.mkdir(exist_ok=True)
    return DocGenContext(
        project_id="orders", staging_dir=staging, staging_abs=str(staging), logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["Orders"], total_jobs=3, total_groups=1, effective_model="opencode", write_model="opencode",
        platform="talend", analysis_guide_file="_analysis_guide.md",
        generation_profile=normalize_profile(profile or {"contract": "talend-enterprise-v1"}),
    )


async def _quiet(*_args):
    return None


@pytest.fixture(autouse=True)
def no_log_estate_network(monkeypatch):
    """No test in this module may open a socket.

    `prepare()` stages the runtime authority, and its log-estate side is a live
    HTTP read (`providers/custom/elastic_evidence.py` -> the RAG backend). Left
    unstubbed that reaches `config.rag_base_url` for real: in CI or a container
    nothing is listening and the cost is a connect timeout, and on a machine
    where something IS listening the answer comes from whatever holds that
    port. Both make this suite slow or flaky for reasons that have nothing to
    do with the enterprise phase sequence.

    Patched AT THE CONSUMING MODULE, per the project convention, so
    `httpx.AsyncClient` is never constructed at all -- there is no transport to
    fall back to. The script is the "this project has no log estate" answer,
    which is precisely what the fusion received before the Elastic authority
    was wired in, so every assertion in this module keeps its exact meaning and
    the frozen `expected.json` comparison is unaffected. The Elastic lane's own
    behaviour is covered by `test_elastic_evidence.py`.

    Yields the recorder so a test can assert the seam was the only network
    touch point.
    """
    reads: list[str] = []

    class _AbsentResponse:
        """The endpoint's answer for a project with no log-estate source."""

        status_code = 404

        def raise_for_status(self) -> None:
            raise RuntimeError("HTTP 404")

        def json(self) -> dict:
            return {}

    class _NoNetworkClient:
        async def __aenter__(self) -> "_NoNetworkClient":
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        async def get(self, url, params=None, headers=None):
            reads.append(str(url))
            return _AbsentResponse()

    monkeypatch.setattr(
        elastic_evidence,
        "httpx",
        SimpleNamespace(AsyncClient=lambda **_kwargs: _NoNetworkClient()),
    )
    return reads


def _run(tmp_path, provider_factory, profile=None):
    scan = fx.write_scan_output(tmp_path / "scan-output")
    ctx = _ctx(tmp_path, profile)
    provider = provider_factory(ctx.staging_dir)
    completed: list[str] = []
    runner = EnterpriseRunner(provider, resolved="opencode", ctx=ctx, facts=fx.facts(), ledger=None, log_cb=_quiet,
                              on_complete=lambda step, execution: completed.append(step), profile=ctx.generation_profile,
                              scan_output_dir=scan)
    result = asyncio.run(runner.run())
    return ctx, provider, runner, result, completed


def test_full_run_publishes_grounded_docs_and_frozen_fixture_evaluates(tmp_path):
    ctx, provider, runner, (ok, error, required), completed = _run(tmp_path, FakeProvider)
    assert ok, error
    assert required == ["INTEGRATION_CATALOGUE", "INTEGRATION__rest-orders-orders-database-warehouse-drop-folder"]
    staging = ctx.staging_dir
    phases = [c["phase"] for c in provider.calls]
    assert phases[:4] == [pc.PHASE_DEEP_ANALYZE, pc.PHASE_RECONSTRUCT, pc.PHASE_MODEL, pc.PHASE_RECONCILE]
    for call in provider.calls:  # budgets from the profile reach every session
        assert call["kwargs"]["timeout_seconds"] == 1800 and call["kwargs"]["max_model_turns"] == 200
        assert call["kwargs"]["max_processed_tokens"] == 6_000_000
        assert "_method/talend/nexus-adaptation.md" in call["kwargs"]["prompt_override"]
    verdict = json.loads((staging / PUBLICATION_VERDICT).read_text())
    assert verdict["ok"], verdict["blocking"]
    receipt = json.loads((staging / RUN_RECEIPT).read_text())
    assert receipt["status"] == "complete" and receipt["model"]["source"] == "configured_default"
    assert receipt["usage"]["sessions"] == len(provider.calls) == len(completed)
    assert (staging / RAG_KNOWLEDGE_ROOT / "journey__orders-intake.md").is_file()
    assert (staging / "_analysis" / "Orders.md").is_file()
    gates = json.loads((staging / "_assessment" / "enterprise_gates.json").read_text())
    assert gates["client_ready"] is True and gates["contract"] == "talend-enterprise-v1"
    report = evaluate_enterprise_talend(staging, EXPECTED)
    assert report["ok"], report


def test_concurrent_chunks_get_unique_step_names(tmp_path):
    """Every session must be dispatchable under the real attempt ledger.

    Regression: chunks inside one phase round run concurrently and each derived
    its step name from the recorded attempt count, which none of them had
    written yet -- so they collided and the ledger killed all but the first
    with "exceeds max_attempts=1". A real run died mid-phase-1 this way.
    """
    from mcp_server.providers.opencode_cli import OpenCodeAttemptLedger

    scan = fx.write_scan_output(tmp_path / "scan-output")
    # One design per session so the three fixture designs need three chunks,
    # run two at a time.
    ctx = _ctx(tmp_path, {"contract": "talend-enterprise-v1",
                          "enterprise": {"units_per_session": 1, "concurrency": 2}})
    provider = FakeProvider(ctx.staging_dir)
    ledger = OpenCodeAttemptLedger(ctx.staging_dir, run_id=7)
    runner = EnterpriseRunner(provider, resolved="opencode", ctx=ctx, facts=fx.facts(), ledger=ledger,
                              log_cb=_quiet, on_complete=lambda *a: None, profile=ctx.generation_profile,
                              scan_output_dir=scan)
    ok, error, required = asyncio.run(runner.run())

    assert ok, error
    steps = [call["step"] for call in provider.calls]
    assert len(steps) == len(set(steps)), f"duplicate step names dispatched: {steps}"
    analyze = [s for s in steps if pc.PHASE_DEEP_ANALYZE in s]
    assert len(analyze) >= 3, "each chunk must get its own session"
    # The ledger recorded one dispatch per session and refused none.
    assert sorted(ledger.attempts.values()) == [1] * len(steps)


def test_resume_keeps_accepted_ledgers_and_makes_no_new_sessions(tmp_path):
    ctx, provider, runner, (ok, _, _), _ = _run(tmp_path, FakeProvider)
    assert ok and provider.calls
    provider2 = FakeProvider(ctx.staging_dir)
    runner2 = EnterpriseRunner(provider2, resolved="opencode", ctx=ctx, facts=fx.facts(), ledger=None, log_cb=_quiet,
                               on_complete=lambda *a: None, profile=ctx.generation_profile, scan_output_dir=tmp_path / "scan-output")
    ok2, error2, required2 = asyncio.run(runner2.run())
    assert ok2, error2
    # Only the review phase is re-run on resume (it re-checks the final docs); no analysis or writing sessions.
    assert {c["phase"] for c in provider2.calls} <= {pc.PHASE_REVIEW}
    state = json.loads((ctx.staging_dir / PHASE_STATE).read_text())
    assert state["phases"][pc.PHASE_DEEP_ANALYZE]["status"] == "complete"


def test_rejected_entry_triggers_a_recovery_session_not_a_full_regeneration(tmp_path):
    ctx, provider, runner, (ok, error, _), _ = _run(tmp_path, lambda s: FakeProvider(s, fail_first_design=True))
    assert ok, error
    design_calls = [c for c in provider.calls if c["phase"] == pc.PHASE_DEEP_ANALYZE]
    assert [c["kind"] for c in design_calls] == ["initial", "recovery"]
    assert design_calls[1]["assignment"] == 1  # only the rejected design is re-assigned
    state = json.loads((ctx.staging_dir / PHASE_STATE).read_text())
    attempts = state["phases"][pc.PHASE_DEEP_ANALYZE]["attempts"]
    assert attempts[0]["remaining_after"] == 1 and attempts[1]["remaining_after"] == 0


def test_invalid_knowledge_entry_is_repaired_by_a_targeted_session(tmp_path):
    ctx, provider, runner, (ok, error, _), _ = _run(tmp_path, lambda s: FakeProvider(s, retype_first_mapping=True))
    assert ok, error
    model_calls = [c for c in provider.calls if c["phase"] == pc.PHASE_MODEL]
    assert [c["kind"] for c in model_calls] == ["initial", "repair"]
    assert "quoted evidence not found" in model_calls[1]["kwargs"]["prompt_override"]
    validation = json.loads((ctx.staging_dir / "_evidence_registry" / "enterprise" / "knowledge_model_validation.json").read_text())
    assert validation["ok"]


def test_review_findings_drive_targeted_document_repair(tmp_path):
    finding = {"doc": "INTEGRATION__rest-orders-orders-database-warehouse-drop-folder", "section": "operations",
               "severity": "blocking", "kind": "shallow", "detail": "monitoring is not described"}
    ctx, provider, runner, (ok, error, _), _ = _run(tmp_path, lambda s: FakeProvider(s, review_findings=[finding]))
    assert ok, error
    repairs = [c for c in provider.calls if c["phase"] == pc.PHASE_WRITE and c["kind"] == "repair"]
    assert len(repairs) == 1 and repairs[0]["assignment"] == 1
    assert "monitoring is not described" in repairs[0]["kwargs"]["prompt_override"]
    review = json.loads((ctx.staging_dir / "_evidence_registry" / "enterprise" / "review.json").read_text())
    assert review["blocking"] == 0 and review["attempt"] == 2


def test_budget_exhaustion_stops_dispatch_and_reports_incomplete_without_absence_claims(tmp_path):
    profile = {"contract": "talend-enterprise-v1", "enterprise": {"total_processed_token_limit": 1500}}
    ctx, provider, runner, (ok, error, required), _ = _run(tmp_path, lambda s: FakeProvider(s, tokens_per_session=1000), profile)
    assert not ok and "total_processed_token_budget" in error and required == []
    assert len(provider.calls) == 2  # the third dispatch is refused
    receipt = json.loads((ctx.staging_dir / RUN_RECEIPT).read_text())
    assert receipt["status"] == "incomplete" and receipt["stop_reason"] == "total_processed_token_budget"
    assert not list(ctx.staging_dir.glob("INTEGRATION*.md"))
    assert not (ctx.staging_dir / PUBLICATION_VERDICT).exists()
    # Accepted work survives for resume.
    assert pc.remaining_for(runner.ectx, pc.PHASE_DEEP_ANALYZE) == []


def test_missing_sanitized_package_fails_before_any_session(tmp_path):
    scan = fx.write_scan_output(tmp_path / "scan-output")
    (scan / "scan" / "sanitized" / "manifest.json").unlink()
    ctx = _ctx(tmp_path)
    provider = FakeProvider(ctx.staging_dir)
    runner = EnterpriseRunner(provider, resolved="opencode", ctx=ctx, facts=fx.facts(), ledger=None, log_cb=_quiet,
                              on_complete=lambda *a: None, profile=ctx.generation_profile, scan_output_dir=scan)
    ok, error, _ = asyncio.run(runner.run())
    assert not ok and "Rescan" in error and provider.calls == []


def test_profile_model_pin_overrides_route_and_default_inherits(tmp_path):
    pinned = {"contract": "talend-enterprise-v1", "model": "openrouter/some-model"}
    ctx, provider, runner, (ok, error, _), _ = _run(tmp_path, FakeProvider, pinned)
    assert ok, error
    assert provider.model_route == "openrouter/some-model"
    assert json.loads((ctx.staging_dir / RUN_RECEIPT).read_text())["model"] == {"route": "openrouter/some-model", "source": "pinned_by_profile"}
    ctx2, provider2, _, (ok2, _, _), _ = _run(tmp_path / "second", FakeProvider)
    assert ok2 and provider2.model_route == "ollama/test"


def test_provider_routes_enterprise_contract_and_incomplete_runs_raise(tmp_path, monkeypatch):
    from mcp_server.providers.opencode_cli import OpenCodeCliProvider

    provider = OpenCodeCliProvider(model_route="ollama/test")
    ctx = _ctx(tmp_path)
    assert OpenCodeCliProvider._enterprise_v1(ctx) is True
    assert OpenCodeCliProvider._landscape_v2(ctx) is False

    class Stub:
        def __init__(self, *a, **k):
            pass

        async def run(self):
            return False, "Enterprise run incomplete: total_timeout", []

    monkeypatch.setattr(runner_mod, "EnterpriseRunner", Stub)
    with pytest.raises(OpenCodeCompatibilityError, match="incomplete"):
        asyncio.run(provider._run_enterprise(resolved="x", ctx=ctx, facts=fx.facts(), harness=SimpleNamespace(_required_docs=[]),
                                             state=None, ledger=None, log_cb=_quiet, on_complete=lambda *a: None))


def test_the_run_reaches_the_network_only_through_the_log_estate_seam(tmp_path, no_log_estate_network):
    """The enterprise sequence itself opens no connection.

    Exactly one read is attempted -- the log-estate authority -- and it is
    intercepted at the consuming module before a client exists. If a future
    phase acquires a live dependency, or the seam moves, this fails loudly
    instead of the suite quietly starting to depend on a listening port.
    """
    _ctx_unused, _provider, _runner, (ok, error, _required), _completed = _run(tmp_path, FakeProvider)

    assert ok, error
    assert no_log_estate_network == [elastic_evidence.endpoint_url()], no_log_estate_network
