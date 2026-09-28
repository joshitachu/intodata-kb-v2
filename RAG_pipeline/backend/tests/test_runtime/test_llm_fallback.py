"""Tests for the bounded opt-in LLM fallback (src/runtime/insights/llm_fallback.py).

The fallback is a Decorator over the deterministic incident result. These tests
mock ``src.generation.llm_client.generate_answer`` — no real network calls — and
prove:

- flag OFF => byte-for-byte identical to deterministic, client NOT called;
- flag ON => fallback runs only on low-confidence incidents;
- high/medium confidence never calls the client;
- bounds: <=5 excerpts, each <=300 chars, prompt under the char cap, single call;
- excerpts are credential-redacted before the call;
- LLM error / unparseable output => deterministic result stands, no raise.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.config import settings
from src.runtime.insights import llm_fallback
from src.runtime.insights.builder import build_runtime_insights_for_execution
from src.runtime.insights.llm_fallback import (
    _MAX_EXCERPT_CHARS,
    _MAX_EXCERPTS,
    _MAX_PROMPT_CHARS,
    build_fallback_input,
    maybe_run_llm_fallback,
)

# Reuse the established builder test helpers (seed + accessor).
from tests.test_runtime.test_insight_builder import (
    _PROJECT,
    _SYNCED_AT,
    _get_incident,
    _seed_custom_failed_execution,
)

_GEN = "src.generation.llm_client.generate_answer"
_FLAG = "runtime_llm_fallback_enabled"

_GOOD_LLM_JSON = json.dumps(
    {
        "root_cause": "Disk volume /data is full on the host",
        "next_checks": ["Free disk space", "Add monitoring"],
    }
)


@pytest.fixture
def fallback_on(monkeypatch):
    monkeypatch.setattr(settings, _FLAG, True)


@pytest.fixture
def fallback_off(monkeypatch):
    monkeypatch.setattr(settings, _FLAG, False)


async def _seed_low_confidence(session, exec_id: str, *, message: str) -> None:
    """A failure with no diagnostic signal (no error_code/exception) => low confidence."""
    await _seed_custom_failed_execution(
        session,
        project_id=_PROJECT,
        execution_id=exec_id,
        job_name="low_conf_job",
        lines=[
            (10, "INFO", "[route_1] Route starting"),
            (11, "ERROR", message),
        ],
    )


async def _build(session, exec_id: str):
    await build_runtime_insights_for_execution(
        session,
        project_id=_PROJECT,
        execution_id=exec_id,
        source_key="mock",
        ingested_at=_SYNCED_AT,
    )
    return await _get_incident(session, _PROJECT, exec_id)


# ---------------------------------------------------------------------------
# Flag OFF: identical to deterministic, no call
# ---------------------------------------------------------------------------


class TestFlagOff:
    async def test_flag_off_does_not_call_llm(self, db_session, fallback_off):
        with patch(_GEN, new_callable=AsyncMock) as spy:
            spy.return_value = _GOOD_LLM_JSON
            await _seed_low_confidence(
                db_session, "off-1", message="[route_1] aborted: disk volume /data is full"
            )
            incident = await _build(db_session, "off-1")
        assert incident is not None
        assert incident.confidence == "low"
        assert incident.cause_origin == "deterministic"
        spy.assert_not_called()

    async def test_flag_off_identical_to_deterministic(self, db_session, fallback_off):
        msg = "[route_1] aborted: disk volume /data is full"
        with patch(_GEN, new_callable=AsyncMock, return_value=_GOOD_LLM_JSON):
            await _seed_low_confidence(db_session, "off-2", message=msg)
            incident = await _build(db_session, "off-2")
            # maybe_run_llm_fallback with flag off returns its inputs verbatim.
            rc, checks, origin = await maybe_run_llm_fallback(
                "DET CAUSE",
                ["det check"],
                confidence="low",
                symptom_summary="s",
                error_code=None,
                exception_class=None,
                failed_component=None,
                excerpts=[("first_error", msg)],
            )
        assert (rc, checks, origin) == ("DET CAUSE", ["det check"], "deterministic")
        assert incident.cause_origin == "deterministic"


# ---------------------------------------------------------------------------
# Flag ON: low-confidence only
# ---------------------------------------------------------------------------


class TestFlagOnLowOnly:
    async def test_low_confidence_triggers_single_call(self, db_session, fallback_on):
        with patch(_GEN, new_callable=AsyncMock, return_value=_GOOD_LLM_JSON) as spy:
            await _seed_low_confidence(
                db_session, "on-low", message="[route_1] aborted: disk volume /data is full"
            )
            incident = await _build(db_session, "on-low")
        assert incident.confidence == "low"
        assert incident.cause_origin == "llm_fallback"
        assert incident.root_cause_candidate == "Disk volume /data is full on the host"
        assert incident.next_checks == ["Free disk space", "Add monitoring"]
        spy.assert_called_once()

    async def test_high_confidence_never_calls_llm(self, db_session, fallback_on):
        from src.runtime.store.seed import _RICH_FAILED_EXEC, seed_rich_runtime_data

        with patch(_GEN, new_callable=AsyncMock) as spy:
            await seed_rich_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
            incident = await _build(db_session, _RICH_FAILED_EXEC)
        assert incident.confidence == "high"
        assert incident.cause_origin == "deterministic"
        spy.assert_not_called()

    async def test_medium_confidence_never_calls_llm(self, db_session, fallback_on):
        with patch(_GEN, new_callable=AsyncMock) as spy:
            # One strong signal (error_code) matched by a specific catalog pattern,
            # no second strong signal => medium (not low, not high).
            await _seed_custom_failed_execution(
                db_session,
                project_id=_PROJECT,
                execution_id="on-med",
                job_name="med_job",
                lines=[
                    (10, "INFO", "[http_call] starting"),
                    (11, "ERROR", "[http_call] request failed with HTTP 503 Service Unavailable"),
                ],
            )
            incident = await _build(db_session, "on-med")
        assert incident.confidence == "medium"
        assert incident.cause_origin == "deterministic"
        spy.assert_not_called()


# ---------------------------------------------------------------------------
# Bounds enforcement (at the payload level)
# ---------------------------------------------------------------------------


class TestBounds:
    def test_excerpt_count_and_char_caps(self):
        over = [(f"role{i}", "x" * 500) for i in range(20)]
        payload = build_fallback_input(
            symptom_summary="sum",
            error_code=None,
            exception_class=None,
            failed_component=None,
            excerpts=over,
        )
        assert len(payload.excerpts) == _MAX_EXCERPTS
        for ev in payload.excerpts:
            assert len(ev.excerpt) <= _MAX_EXCERPT_CHARS

    def test_prompt_under_char_cap(self):
        over = [(f"role{i}", "y" * 300) for i in range(5)]
        payload = build_fallback_input(
            symptom_summary="z" * 5000,
            error_code=None,
            exception_class=None,
            failed_component=None,
            excerpts=over,
        )
        prompt = llm_fallback._render_user_prompt(payload)
        assert len(prompt) <= _MAX_PROMPT_CHARS

    async def test_payload_sent_respects_bounds(self, db_session, fallback_on):
        captured = {}

        async def _capture(messages, *a, **k):
            captured["messages"] = messages
            return _GOOD_LLM_JSON

        with patch(_GEN, side_effect=_capture):
            lines = [(10, "INFO", "[r] start")]
            for i in range(15):
                lines.append((11 + i, "ERROR", "[comp%d] failure detail %s" % (i, "q" * 400)))
            await _seed_custom_failed_execution(
                db_session,
                project_id=_PROJECT,
                execution_id="bounds-1",
                job_name="bounds_job",
                lines=lines,
            )
            await _build(db_session, "bounds-1")
        msgs = captured["messages"]
        user = next(m["content"] for m in msgs if m["role"] == "user")
        assert len(user) <= _MAX_PROMPT_CHARS
        bullet_count = sum(1 for line in user.splitlines() if line.startswith("- ["))
        assert bullet_count <= _MAX_EXCERPTS


# ---------------------------------------------------------------------------
# Redaction before the call
# ---------------------------------------------------------------------------


class TestRedaction:
    async def test_no_credentials_reach_llm(self, db_session, fallback_on):
        captured = {}

        async def _capture(messages, *a, **k):
            captured["messages"] = messages
            return _GOOD_LLM_JSON

        with patch(_GEN, side_effect=_capture):
            secret_line = (
                "[route_1] aborted connecting to "
                "jdbc:mysql://dbuser:SuperSecret123@db.internal/app "
                "password=topsecret token:abc123"
            )
            await _seed_low_confidence(db_session, "redact-1", message=secret_line)
            incident = await _build(db_session, "redact-1")
        assert incident.confidence == "low"
        prompt = "\n".join(m["content"] for m in captured["messages"])
        assert "SuperSecret123" not in prompt
        assert "topsecret" not in prompt
        assert "abc123" not in prompt
        assert "<redacted>" in prompt

    def test_build_input_redacts_structured_fields(self):
        payload = build_fallback_input(
            symptom_summary="failed password=hunter2",
            error_code="token:leak999",
            exception_class=None,
            failed_component=None,
            excerpts=[("first_error", "user/secretpw@host failed")],
        )
        assert "hunter2" not in payload.symptom_summary
        assert "leak999" not in (payload.error_code or "")
        assert "secretpw" not in payload.excerpts[0].excerpt


# ---------------------------------------------------------------------------
# Fail-safe
# ---------------------------------------------------------------------------


class TestFailSafe:
    async def test_llm_error_keeps_deterministic(self, db_session, fallback_on):
        with patch(
            _GEN, new_callable=AsyncMock, side_effect=RuntimeError("all providers failed")
        ):
            await _seed_low_confidence(
                db_session, "err-1", message="[route_1] aborted: disk volume /data is full"
            )
            incident = await _build(db_session, "err-1")  # must not raise
        assert incident.confidence == "low"
        assert incident.cause_origin == "deterministic"
        assert incident.root_cause_candidate

    async def test_unparseable_output_keeps_deterministic(self, db_session, fallback_on):
        with patch(_GEN, new_callable=AsyncMock, return_value="I cannot help with that."):
            await _seed_low_confidence(
                db_session, "unparse-1", message="[route_1] aborted: disk volume /data is full"
            )
            incident = await _build(db_session, "unparse-1")
        assert incident.cause_origin == "deterministic"

    async def test_direct_helper_success_sets_origin(self, fallback_on):
        with patch(_GEN, new_callable=AsyncMock, return_value=_GOOD_LLM_JSON):
            rc, checks, origin = await maybe_run_llm_fallback(
                "DET",
                ["det check"],
                confidence="low",
                symptom_summary="symptom",
                error_code=None,
                exception_class=None,
                failed_component=None,
                excerpts=[("first_error", "disk full")],
            )
        assert origin == "llm_fallback"
        assert rc == "Disk volume /data is full on the host"
        assert checks == ["Free disk space", "Add monitoring"]

    async def test_refused_licence_keeps_deterministic_without_calling_llm(self, fallback_on):
        """Sync route and background poller share this helper; an expired or
        missing licence must stop the token spend here, not at either caller."""
        from src.licensing.core import EntitlementPolicy
        from src.licensing.runtime import set_policy

        class _NoLicence:
            def read_token(self):
                return None

        set_policy(EntitlementPolicy(_NoLicence(), public_keys={}, expected_subject="x", enforce=True))
        try:
            with patch(_GEN, new_callable=AsyncMock, return_value=_GOOD_LLM_JSON) as gen:
                rc, checks, origin = await maybe_run_llm_fallback(
                    "DET",
                    ["det check"],
                    confidence="low",
                    symptom_summary="symptom",
                    error_code=None,
                    exception_class=None,
                    failed_component=None,
                    excerpts=[("first_error", "disk full")],
                )
        finally:
            set_policy(None)
        gen.assert_not_called()
        assert (rc, checks, origin) == ("DET", ["det check"], "deterministic")
