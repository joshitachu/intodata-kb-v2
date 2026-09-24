"""Route-level contract for `/api/v1/runtime/tools` and `/tools/{tool_name}`.

These two routes are the ONLY way a documentation run reaches the runtime tool
registry: the generator lives in the MCP server, and every other caller of
`execute_runtime_tool` is in-process. Three properties are load-bearing and none
of them is reachable from a registry or repository test:

* the caller authenticates with a MACHINE key, and the key is empty in the
  mcp-server container today -- dev allows keyless, production must not;
* scope is pinned to the body's `project_id` and is never derived from
  `arguments`. The invariant is stated in `execute_runtime_tool`'s docstring and
  enforced in the store, but this route is where untrusted arguments enter the
  process, so it is pinned here against the widest widening surfaces the tool
  input models expose (`project_id`, `project_ids`, `read_terms`);
* a tool-level failure is a 200 carrying a structured `RuntimeToolResult`, not a
  500. The agent has to decide what to do next, and a transport error is not
  something it can tell apart from a broken backend.

Two projects run a job of the SAME name so that a scope leak shows up as a wrong
number rather than merely as a missing filter.
"""

import datetime
from datetime import UTC

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.config import settings
from src.runtime.models import RuntimeExecution, RuntimeExecutionState, RuntimeFailure
from src.runtime.registry import list_runtime_tool_definitions
from src.runtime.store import RuntimeStoreRepository

_LIST_URL = "/api/v1/runtime/tools"
_CALL_URL = "/api/v1/runtime/tools/{tool_name}"
_P = "proj-docs"
_OTHER = "proj-other"
_SRC = "tmc"
_T0 = datetime.datetime.now(UTC) - datetime.timedelta(days=2)
_KEY = "machine-key-under-test"

_SHARED_JOB = "AssetCheck_GetLocation"
_OTHER_ONLY_MARKER = "OTHER-PROJECT-ONLY"

# The double-submit pair every POST test in this package already sends (see
# `tests/test_api/test_admin.py::CSRF_HEADERS`). `POST` is a state-changing
# method to `CSRFMiddleware`, which is on by default, and this route is not in
# its `_EXEMPT_PREFIXES` -- so a matching pair is currently part of the
# transport contract for the machine caller too. Pinned by `TestCsrfPosture`.
_CSRF_HEADERS = {"cookie": "csrf_token=test_csrf", "X-CSRF-Token": "test_csrf"}


def _call_url(tool_name: str) -> str:
    return _CALL_URL.format(tool_name=tool_name)


def _body(**overrides) -> dict:
    payload = {"project_id": _P, "arguments": {}}
    payload.update(overrides)
    return payload


async def _post(client, tool_name: str, payload: dict, **extra_headers):
    """One tool call, with the transport headers the route currently requires."""
    return await client.post(
        _call_url(tool_name), json=payload, headers={**_CSRF_HEADERS, **extra_headers}
    )


async def _run(
    store: RuntimeStoreRepository,
    *,
    project_id: str,
    execution_id: str,
    job_name: str,
    state: str = "success",
    offset_hours: int = 0,
) -> None:
    started = _T0 + datetime.timedelta(hours=offset_hours)
    await store.upsert_execution(
        project_id,
        RuntimeExecution(
            execution_id=execution_id,
            job_name=job_name,
            state=RuntimeExecutionState(state),
            started_at=started,
            ended_at=started + datetime.timedelta(milliseconds=250),
            duration_ms=250,
        ),
        _SRC,
        _T0,
    )


@pytest.fixture
async def client(db_session):
    """Two projects, one shared job name, machine auth left keyless by default."""
    from src.api.dependencies import get_db

    store = RuntimeStoreRepository(db_session)
    # proj-docs: two successful runs of the shared job.
    await _run(store, project_id=_P, execution_id="p-1", job_name=_SHARED_JOB)
    await _run(store, project_id=_P, execution_id="p-2", job_name=_SHARED_JOB, offset_hours=1)
    # proj-other: five FAILED runs of the same job name, each with a marker.
    for index in range(5):
        await _run(
            store,
            project_id=_OTHER,
            execution_id=f"o-{index}",
            job_name=_SHARED_JOB,
            state="failed",
            offset_hours=index,
        )
        await store.upsert_failure(
            _OTHER,
            RuntimeFailure(
                execution_id=f"o-{index}",
                job_name=_SHARED_JOB,
                failed_at=_T0 + datetime.timedelta(hours=index),
                error_summary=f"{_OTHER_ONLY_MARKER} marker",
            ),
            _SRC,
            _T0,
        )

    app.dependency_overrides[get_db] = lambda: db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def keyless_dev(monkeypatch):
    """Default posture: local dev, no machine key, held-store provider."""
    monkeypatch.setattr(settings, "mcp_ingest_api_key", "", raising=False)
    monkeypatch.setattr(settings, "app_env", "development", raising=False)
    monkeypatch.setattr(settings, "runtime_provider_key", "stored", raising=False)
    monkeypatch.setattr(settings, "runtime_tool_timeout", 10.0, raising=False)
    monkeypatch.setattr(settings, "runtime_tool_result_max_chars", 120_000, raising=False)


class TestToolCatalog:
    async def test_every_registry_tool_is_offered(self, client):
        """The generator can only ask for what this list names."""
        resp = await client.get(_LIST_URL)
        assert resp.status_code == 200
        served = {tool["name"] for tool in resp.json()["tools"]}
        assert served == {spec.name for spec in list_runtime_tool_definitions()}
        assert len(resp.json()["tools"]) == len(list_runtime_tool_definitions())

    async def test_each_entry_carries_exactly_the_three_contract_fields(self, client):
        resp = await client.get(_LIST_URL)
        for tool in resp.json()["tools"]:
            assert set(tool) == {"name", "description", "input_schema"}
            assert tool["description"].strip()
            # Without a usable argument schema the agent cannot build a call.
            assert isinstance(tool["input_schema"], dict)
            assert "properties" in tool["input_schema"] or tool["input_schema"].get("type")

    async def test_the_catalog_is_independent_of_the_provider(self, client, monkeypatch):
        """Discovery must work on a deployment with live runtime switched off;
        unavailability is a per-call fact, reported per call."""
        monkeypatch.setattr(settings, "runtime_provider_key", "", raising=False)
        resp = await client.get(_LIST_URL)
        assert resp.status_code == 200
        assert len(resp.json()["tools"]) == len(list_runtime_tool_definitions())


class TestMachineAuth:
    """Same gate as `GET /runtime/doc-evidence`, on both routes."""

    async def test_keyless_dev_is_allowed(self, client):
        assert (await client.get(_LIST_URL)).status_code == 200
        resp = await _post(client, "list_executions", _body())
        assert resp.status_code == 200

    async def test_keyless_non_dev_is_disabled_not_open(self, client, monkeypatch):
        monkeypatch.setattr(settings, "app_env", "staging", raising=False)
        assert (await client.get(_LIST_URL)).status_code == 503
        resp = await _post(client, "list_executions", _body())
        assert resp.status_code == 503

    async def test_configured_key_rejects_a_missing_header(self, client, monkeypatch):
        monkeypatch.setattr(settings, "mcp_ingest_api_key", _KEY, raising=False)
        assert (await client.get(_LIST_URL)).status_code == 401
        resp = await _post(client, "list_executions", _body())
        assert resp.status_code == 401

    async def test_configured_key_rejects_a_wrong_token(self, client, monkeypatch):
        monkeypatch.setattr(settings, "mcp_ingest_api_key", _KEY, raising=False)
        headers = {"Authorization": "Bearer not-the-key"}
        assert (await client.get(_LIST_URL, headers=headers)).status_code == 401
        resp = await _post(client, "list_executions", _body(), **headers)
        assert resp.status_code == 401

    async def test_configured_key_rejects_a_wrong_scheme(self, client, monkeypatch):
        monkeypatch.setattr(settings, "mcp_ingest_api_key", _KEY, raising=False)
        headers = {"Authorization": f"Basic {_KEY}"}
        assert (await client.get(_LIST_URL, headers=headers)).status_code == 401
        resp = await _post(client, "list_executions", _body(), **headers)
        assert resp.status_code == 401

    async def test_configured_key_accepts_the_right_token(self, client, monkeypatch):
        monkeypatch.setattr(settings, "mcp_ingest_api_key", _KEY, raising=False)
        headers = {"Authorization": f"Bearer {_KEY}"}
        assert (await client.get(_LIST_URL, headers=headers)).status_code == 200
        resp = await _post(client, "list_executions", _body(), **headers)
        assert resp.status_code == 200

    async def test_a_rejected_call_returns_no_runtime_data(self, client, monkeypatch):
        """A 401 must not carry a result envelope that a lenient client reads."""
        monkeypatch.setattr(settings, "mcp_ingest_api_key", _KEY, raising=False)
        resp = await _post(client, "list_executions", _body())
        assert "tool_name" not in resp.json()


class TestCsrfPosture:
    """A machine caller reaches this route with no browser artefacts at all.

    `CSRFMiddleware` treats POST as state-changing, so this route 403'd every
    call until `/api/v1/runtime/tools` joined its `_EXEMPT_PREFIXES` -- exactly
    the failure already recorded there for `/api/v1/labs/mcp`. The sibling
    machine endpoints (`/runtime/doc-evidence`, `/runtime/elastic-aggregates`)
    never met it because GET is a safe method, which is why the gap appeared
    only once this surface gained its first POST.

    The exemption is sound rather than a convenience: authentication is the
    machine ingest key in an Authorization header, no cookie is read, and no
    identity is derived from one -- so there is no ambient browser credential
    for a cross-site request to abuse.
    """

    async def test_a_post_without_the_pair_is_accepted(self, client):
        """The documentation-run client sends no cookie and must not need one."""
        resp = await client.post(_call_url("list_executions"), json=_body())
        assert resp.status_code != 403
        body = resp.json()
        assert not (
            isinstance(body, dict)
            and isinstance(body.get("detail"), dict)
            and body["detail"].get("code") == "csrf_invalid"
        )

    async def test_discovery_is_a_get_and_needs_no_pair(self, client):
        assert (await client.get(_LIST_URL)).status_code == 200

    async def test_the_prefix_is_exempt(self, client):
        from src.api.middleware.csrf import _EXEMPT_PREFIXES

        assert any(_call_url("x").startswith(prefix) for prefix in _EXEMPT_PREFIXES)

    async def test_exemption_does_not_remove_the_machine_key_gate(
        self, client, monkeypatch
    ):
        """CSRF and authentication are separate controls.

        Dropping the CSRF requirement must not leave the route open: with a key
        configured, an unauthenticated POST is still refused.
        """
        monkeypatch.setattr(settings, "mcp_ingest_api_key", _KEY, raising=False)
        resp = await client.post(_call_url("list_executions"), json=_body())
        assert resp.status_code == 401


class TestDispatch:
    async def test_a_valid_call_returns_the_projects_own_data(self, client):
        resp = await _post(
            client, "list_executions", _body(arguments={"job_name": _SHARED_JOB})
        )
        body = resp.json()
        assert resp.status_code == 200
        assert body["success"] is True
        assert body["tool_name"] == "list_executions"
        assert body["error"] is None
        assert body["truncated"] is False
        assert isinstance(body["latency_ms"], int)
        assert [row["execution_id"] for row in body["data"]] == ["p-2", "p-1"]

    async def test_arguments_are_honoured_as_filters(self, client):
        """`arguments` still reaches the tool -- it is a filter, just not a scope."""
        resp = await _post(
            client, "list_executions", _body(arguments={"job_name": "NotAJobHere"})
        )
        assert resp.json()["data"] == []

    async def test_an_empty_arguments_object_is_valid(self, client):
        resp = await _post(client, "list_executions", _body())
        assert resp.json()["success"] is True
        assert len(resp.json()["data"]) == 2

    async def test_arguments_may_be_omitted_entirely(self, client):
        resp = await _post(client, "list_executions", {"project_id": _P})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    async def test_a_second_tool_dispatches_to_its_own_provider_method(self, client):
        """detect_failures over the project that actually fails."""
        resp = await _post(client, "detect_failures", _body(project_id=_OTHER))
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]) == 5
        assert all(row["job_name"] == _SHARED_JOB for row in body["data"])

    async def test_an_aggregate_rollup_dispatches(self, client):
        resp = await _post(
            client, "list_runtime_job_reliability", _body(arguments={"job_name": _SHARED_JOB})
        )
        body = resp.json()
        assert body["success"] is True
        assert [row["total_runs"] for row in body["data"]] == [2]


class TestStructuredFailures:
    """A tool-level failure is data, not an HTTP error."""

    @pytest.mark.parametrize(
        "tool_name", ["not_a_real_tool", "get_execution_logs", "list_executions_"]
    )
    async def test_an_unknown_tool_is_a_structured_error_not_a_crash(self, client, tool_name):
        resp = await _post(client, tool_name, _body())
        body = resp.json()
        assert resp.status_code == 200
        assert body["success"] is False
        assert body["error"]["code"] == "unknown_tool"
        assert body["tool_name"] == tool_name
        assert body["data"] is None

    async def test_bad_arguments_are_a_structured_validation_error(self, client):
        """`get_execution_details` requires `execution_id`."""
        resp = await _post(client, "get_execution_details", _body())
        body = resp.json()
        assert resp.status_code == 200
        assert body["success"] is False
        assert body["error"]["code"] == "validation_error"

    async def test_an_out_of_range_argument_is_a_validation_error(self, client):
        resp = await _post(client, "list_executions", _body(arguments={"limit": 10_000}))
        assert resp.json()["error"]["code"] == "validation_error"

    async def test_no_provider_is_a_structured_disabled_result(self, client, monkeypatch):
        monkeypatch.setattr(settings, "runtime_provider_key", "", raising=False)
        resp = await _post(client, "list_executions", _body())
        body = resp.json()
        assert resp.status_code == 200
        assert body["success"] is False
        assert body["error"]["code"] == "disabled"

    async def test_an_unknown_provider_is_a_structured_unavailable_result(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(settings, "runtime_provider_key", "nope", raising=False)
        resp = await _post(client, "list_executions", _body())
        assert resp.json()["error"]["code"] == "unavailable"


class TestRequestEnvelope:
    """The envelope itself is still an HTTP contract."""

    async def test_project_id_is_required(self, client):
        resp = await _post(client, "list_executions", {"arguments": {}})
        assert resp.status_code == 422

    async def test_an_empty_project_id_is_rejected(self, client):
        """`scope=[""]` would match nothing and read as 'no runtime data'."""
        resp = await _post(client, "list_executions", _body(project_id=""))
        assert resp.status_code == 422

    async def test_a_non_object_arguments_value_is_rejected(self, client):
        resp = await _post(client, "list_executions", _body(arguments="job_name=x"))
        assert resp.status_code == 422


class TestScopeIsNeverDerivedFromArguments:
    """The security invariant `execute_runtime_tool` states in its docstring.

    Pinned here rather than left to code review: this is where untrusted
    arguments enter the process, there is no user behind the machine key, and
    the tool input models expose three separate fields (`project_id`,
    `project_ids`, `read_terms`) that the store treats as row-stamp filters.
    None of them may widen the boundary the body's `project_id` set.
    """

    async def test_an_arguments_project_id_cannot_widen_the_read(self, client):
        resp = await _post(
            client,
            "list_runtime_job_reliability",
            _body(arguments={"project_id": _OTHER, "job_name": _SHARED_JOB}),
        )
        body = resp.json()
        # Narrowing to a project outside scope yields nothing -- never _OTHER's rows.
        assert body["success"] is True
        assert body["data"] == []

    async def test_an_arguments_project_ids_set_cannot_widen_the_read(self, client):
        """`project_ids` is the documentation lane's flat read set and is tried
        FIRST in `_scoped_execution_base`; it is still ANDed with the scope."""
        resp = await _post(
            client,
            "list_runtime_job_reliability",
            _body(arguments={"project_ids": [_P, _OTHER], "job_name": _SHARED_JOB}),
        )
        body = resp.json()
        assert body["success"] is True
        assert [row["project_id"] for row in body["data"]] == [_P]
        assert [row["total_runs"] for row in body["data"]] == [2]

    async def test_injected_read_terms_cannot_widen_the_read(self, client):
        """`read_terms` is documented as server-resolved only. Supplied by a
        caller it can only narrow, and here it narrows to nothing."""
        resp = await _post(
            client,
            "list_executions",
            _body(arguments={"read_terms": [{"project_id": _OTHER}]}),
        )
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == []

    async def test_another_projects_failure_text_never_appears(self, client):
        for tool, arguments in (
            ("detect_failures", {}),
            ("detect_failures", {"read_terms": [{"project_id": _OTHER}]}),
            ("list_executions", {"project_id": _OTHER}),
        ):
            resp = await _post(client, tool, _body(arguments=arguments))
            assert _OTHER_ONLY_MARKER not in resp.text, f"{tool} leaked {arguments}"

    async def test_the_body_project_id_is_what_scopes_the_call(self, client):
        """The positive control: the SAME call under the other project id reads
        that project's rows, so the assertions above are a real boundary and not
        an empty database."""
        resp = await _post(
            client,
            "list_runtime_job_reliability",
            _body(project_id=_OTHER, arguments={"job_name": _SHARED_JOB}),
        )
        body = resp.json()
        assert [row["project_id"] for row in body["data"]] == [_OTHER]
        assert [row["failed_runs"] for row in body["data"]] == [5]
