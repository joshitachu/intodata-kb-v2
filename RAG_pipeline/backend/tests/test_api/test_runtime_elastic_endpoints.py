"""Route-level contract for `GET /api/v1/runtime/elastic-aggregates`.

The consumer is a documentation run, it authenticates with the MACHINE ingest
key, and -- like the doc-evidence client it sits beside -- it swallows
exceptions. So a regression in any property below is not a visible error: it is
a silent, permanent "Elastic authority unavailable" on an otherwise green run,
which is precisely the state this endpoint was built to end.

Four things are therefore pinned here rather than in the service tests:

* the path and the query names, because the consumer's URL is configuration and
  a mismatch is a silent 404;
* the machine key gate -- keyless is allowed only in local dev, and a configured
  key must reject a missing, wrong or wrongly-schemed token;
* scope pinning: with no user behind the key, scope is the requested project and
  a caller cannot name its own read set. Two projects mirror the same job name
  so a leak shows up as a wrong number, not merely a missing filter;
* the response is the `elastic-aggregates-1` object at the TOP LEVEL. The
  consumer's models set `extra="forbid"`, so an envelope field would make the
  whole payload invalid.
"""

from __future__ import annotations

import datetime
from datetime import UTC

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.config import settings
from src.runtime.elastic_aggregates import ElasticAggregatePayload
from src.runtime.models import (
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeJobRun,
    RuntimeLogEvent,
    RuntimeLogSeverity,
)
from src.runtime.store import RuntimeStoreRepository

_URL = "/api/v1/runtime/elastic-aggregates"
_P = "proj-elastic"
_OTHER = "proj-other-tenant"
_T0 = datetime.datetime.now(UTC) - datetime.timedelta(days=2)
_KEY = "machine-key-under-test"
_JOB = "Maximo2RHM_Workorders"
#: Would appear in the response only through a scope leak.
_OTHER_MARKER = "OtherTenant_NightlyRollup"


@pytest.fixture(autouse=True)
def credential_key(monkeypatch):
    monkeypatch.setattr(
        settings, "source_credentials_encryption_key", Fernet.generate_key().decode()
    )
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous", "")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_id", "primary")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous_id", "previous")


@pytest.fixture(autouse=True)
def keyless_dev(monkeypatch):
    """Default posture for these tests: local dev, no machine key configured."""
    monkeypatch.setattr(settings, "mcp_ingest_api_key", "", raising=False)
    monkeypatch.setattr(settings, "app_env", "development", raising=False)


async def _seed(store: RuntimeStoreRepository, *, project_id: str, job_name: str, messages: int):
    # A customer-shaped label, which is what operators really type. It must not
    # reach the prompt, so it is deliberately a string nothing else can produce.
    source = await store.create_source_config(
        project_id=project_id, source_type="elastic", label=f"ACME-{project_id}-PRODUCTION"
    )
    await store.write_credentials(
        source_id=source.id,
        plaintext_payload={"index_pattern": f"esb-{project_id}-*"},
        credential_type="elastic",
        rotated_by="1",
    )
    execution_id = f"{project_id}-exec"
    await store.upsert_execution(
        project_id,
        RuntimeExecution(
            execution_id=execution_id,
            job_name=job_name,
            state=RuntimeExecutionState.SUCCESS,
            started_at=_T0,
            ended_at=_T0 + datetime.timedelta(milliseconds=400),
            duration_ms=400,
        ),
        source.source_key,
        _T0,
    )
    await store.upsert_job_run(
        project_id,
        RuntimeJobRun(
            run_id=execution_id,
            job_id=f"{job_name}-id",
            job_name=job_name,
            source_key=source.source_key,
            execution_id=execution_id,
            state=RuntimeExecutionState.SUCCESS,
            environment_id="env-prod",
            environment_name="PROD",
            started_at=_T0,
        ),
        source.source_key,
        _T0,
    )
    for ordinal in range(messages):
        await store.upsert_log_event(
            project_id,
            RuntimeLogEvent(
                execution_id=execution_id,
                timestamp=_T0 + datetime.timedelta(seconds=ordinal),
                severity=RuntimeLogSeverity.INFO,
                message=f"line {ordinal}",
                line_ordinal=ordinal,
            ),
            source.source_key,
            _T0,
        )
    return source


@pytest.fixture
async def client(db_session):
    """Two tenants, each with its own mirrored log estate."""
    from src.api.dependencies import get_db

    store = RuntimeStoreRepository(db_session)
    await _seed(store, project_id=_P, job_name=_JOB, messages=3)
    # A different tenant, a job name this project never logs, 40 messages: a
    # scope leak is visible as an extra row or a wrong count.
    await _seed(store, project_id=_OTHER, job_name=_OTHER_MARKER, messages=40)

    app.dependency_overrides[get_db] = lambda: db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


@pytest.fixture
async def bare_client(db_session):
    """No runtime data at all: the stated-absence path."""
    from src.api.dependencies import get_db

    app.dependency_overrides[get_db] = lambda: db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


def _params(**overrides) -> dict:
    params = {"project_id": _P}
    params.update(overrides)
    return params


class TestMachineAuth:
    """The same gate `/runtime/doc-evidence` uses. No new credential."""

    async def test_keyless_dev_is_allowed(self, client):
        resp = await client.get(_URL, params=_params())
        assert resp.status_code == 200

    async def test_keyless_non_dev_is_disabled_not_open(self, client, monkeypatch):
        monkeypatch.setattr(settings, "app_env", "staging", raising=False)
        resp = await client.get(_URL, params=_params())
        assert resp.status_code == 503

    async def test_configured_key_rejects_a_missing_header(self, client, monkeypatch):
        monkeypatch.setattr(settings, "mcp_ingest_api_key", _KEY, raising=False)
        resp = await client.get(_URL, params=_params())
        assert resp.status_code == 401

    async def test_configured_key_rejects_a_wrong_token(self, client, monkeypatch):
        monkeypatch.setattr(settings, "mcp_ingest_api_key", _KEY, raising=False)
        resp = await client.get(
            _URL, params=_params(), headers={"Authorization": "Bearer not-the-key"}
        )
        assert resp.status_code == 401

    async def test_configured_key_rejects_a_wrong_scheme(self, client, monkeypatch):
        monkeypatch.setattr(settings, "mcp_ingest_api_key", _KEY, raising=False)
        resp = await client.get(
            _URL, params=_params(), headers={"Authorization": f"Basic {_KEY}"}
        )
        assert resp.status_code == 401

    async def test_configured_key_accepts_the_right_token(self, client, monkeypatch):
        monkeypatch.setattr(settings, "mcp_ingest_api_key", _KEY, raising=False)
        resp = await client.get(
            _URL, params=_params(), headers={"Authorization": f"Bearer {_KEY}"}
        )
        assert resp.status_code == 200

    async def test_no_user_session_is_required(self, client):
        """A machine caller has no cookie; requiring one would 401 every run."""
        resp = await client.get(_URL, params=_params())
        assert resp.status_code == 200


class TestRequestContract:
    async def test_project_id_is_required(self, client):
        resp = await client.get(_URL)
        assert resp.status_code == 422

    @pytest.mark.parametrize("window_days", [0, -1, 366])
    async def test_window_days_out_of_range(self, client, window_days):
        resp = await client.get(_URL, params=_params(window_days=window_days))
        assert resp.status_code == 422

    @pytest.mark.parametrize("window_days", [1, 90, 365])
    async def test_window_days_in_range(self, client, window_days):
        resp = await client.get(_URL, params=_params(window_days=window_days))
        assert resp.status_code == 200
        assert resp.json()["window_days"] == window_days

    async def test_the_response_is_the_contract_at_the_top_level(self, client):
        """No envelope: the consumer's models forbid unknown keys."""
        resp = await client.get(_URL, params=_params())
        body = resp.json()

        assert set(body) == set(ElasticAggregatePayload.model_fields)
        assert body["schema_version"] == "elastic-aggregates-1"
        # Re-validated exactly as the consumer validates it.
        assert ElasticAggregatePayload.model_validate(body).per_job

    async def test_the_payload_reports_this_projects_estate(self, client):
        body = resp_json = (await client.get(_URL, params=_params())).json()

        assert [item["job_name"] for item in body["per_job"]] == [_JOB]
        assert resp_json["per_job"][0]["message_count"] == 3
        assert resp_json["per_job"][0]["environment"] == "PROD"
        assert resp_json["index_pattern"] == f"esb-{_P}-*"


class TestScopeResolution:
    """Read scope is resolved server-side, from stored configuration only."""

    async def test_another_tenants_estate_never_appears(self, client):
        resp = await client.get(_URL, params=_params())
        body = resp.text

        assert _OTHER_MARKER not in body
        assert f"esb-{_OTHER}-*" not in body

    async def test_a_caller_cannot_name_its_own_read_set(self, client):
        """The widening parameters do not exist, and supplying them is inert.

        Pinned as a test rather than left to review: this endpoint has no second
        gate behind the machine key, so a signature that accepted a project set
        would be an authorization bypass with nothing behind it.
        """
        resp = await client.get(
            _URL,
            params={
                "project_id": _P,
                "project_ids": [_P, _OTHER],
                "read_project_ids": [_OTHER],
                "environment_ids": ["env-acc"],
                "source_key": "anything",
                "units": [_JOB],
            },
        )
        body = resp.json()

        assert resp.status_code == 200
        assert [item["job_name"] for item in body["per_job"]] == [_JOB]
        assert body["per_job"][0]["message_count"] == 3
        assert _OTHER_MARKER not in resp.text

    async def test_the_configured_environment_scope_is_applied_server_side(
        self, client, db_session
    ):
        """`doc_environment_ids` decides the population; no request field can.

        The seeded estate runs in PROD only, so narrowing to ACC must empty the
        payload -- and say why -- rather than fall back to everything.
        """
        store = RuntimeStoreRepository(db_session)
        source_id = await store.find_source_id_by_project_and_type(_P, "elastic")
        assert source_id is not None
        await store.write_credentials(
            source_id=source_id,
            plaintext_payload={
                "index_pattern": f"esb-{_P}-*",
                "doc_environment_ids": ["env-acc"],
            },
            credential_type="elastic",
            rotated_by="1",
        )

        body = (await client.get(_URL, params=_params())).json()

        assert body["per_job"] == []
        assert body["limitations"], "an empty payload must state its own scope"

    async def test_a_configured_window_overrides_the_requested_default(
        self, client, db_session
    ):
        store = RuntimeStoreRepository(db_session)
        source_id = await store.find_source_id_by_project_and_type(_P, "elastic")
        assert source_id is not None
        await store.write_credentials(
            source_id=source_id,
            plaintext_payload={"index_pattern": f"esb-{_P}-*", "doc_window_days": 1},
            credential_type="elastic",
            rotated_by="1",
        )

        body = (await client.get(_URL, params=_params(window_days=90))).json()

        # The window actually applied, always reported.
        assert body["window_days"] == 1
        # The seeded exchange is 2 days old, so a 1-day window holds nothing.
        assert body["per_job"] == []


class TestStatedAbsence:
    """Absence is a well-formed answer, never a 404 and never an empty body."""

    async def test_a_project_with_no_elastic_source_gets_a_stated_payload(
        self, bare_client
    ):
        resp = await bare_client.get(_URL, params=_params(project_id="proj-nothing-here"))
        body = resp.json()

        assert resp.status_code == 200
        assert body["schema_version"] == "elastic-aggregates-1"
        assert body["per_job"] == []
        assert any("No Elastic runtime source is registered" in text for text in body["limitations"])
        # A 404 would be indistinguishable from an unreachable backend, and the
        # consumer swallows both.
        assert resp.status_code != 404

    async def test_an_unknown_project_is_not_a_leak_and_not_an_error(self, client):
        resp = await client.get(_URL, params=_params(project_id="proj-does-not-exist"))
        body = resp.json()

        assert resp.status_code == 200
        assert body["per_job"] == []
        assert _OTHER_MARKER not in resp.text


class TestPromptSafety:
    """The body reaches an LLM prompt."""

    async def test_no_log_body_execution_id_or_source_label_crosses(self, client):
        body = (await client.get(_URL, params=_params())).text

        assert "line 0" not in body
        assert f"{_P}-exec" not in body
        assert "ACME" not in body

    async def test_only_aggregate_fields_are_exposed_per_row(self, client):
        row = (await client.get(_URL, params=_params())).json()["per_job"][0]

        assert set(row) == set(
            (
                "job_name",
                "environment",
                "message_count",
                "error_count",
                "last_seen",
                "latency_p50_ms",
                "latency_p95_ms",
                "correlation_key",
                "correlated_target_job",
            )
        )
