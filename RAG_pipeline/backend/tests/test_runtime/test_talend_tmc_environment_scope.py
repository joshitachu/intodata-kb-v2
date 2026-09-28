"""Environment scoping for the Talend TMC provider.

A TMC tenant is partitioned into environments (DEV/UAT/PROD). Polling all of
them is both expensive -- run history is fetched per executable, so the cost
scales with the task count -- and usually wrong, since a source registered for
one project rarely wants another environment's runs. These tests pin the
scope-discovery contract and the guarantees the scoped sync has to keep.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.runtime.providers import RuntimeProviderError
from src.runtime.providers.talend_tmc import TalendTmcRuntimeProvider, _environment_ids

_CREDENTIALS = {"api_token": "tmc-token", "base_url": "https://tmc.example.com"}

_ENVIRONMENTS = [
    {"id": "env-dev", "name": "DEV", "default": False},
    {"id": "env-prod", "name": "PROD", "default": True},
    {"id": "env-empty", "name": "Scratch", "default": False},
]

_WORKSPACES = [
    {"id": "ws-dev", "name": "Dev WS", "environment": {"id": "env-dev", "name": "DEV"}},
    {"id": "ws-prod", "name": "Prod WS", "environment": {"id": "env-prod", "name": "PROD"}},
]

_ENGINES = [
    # Deliberately parked in DEV while a PROD task references it: a scoped sync
    # must keep it so the PROD job still resolves an engine name.
    {
        "id": "eng-shared",
        "name": "shared-engine",
        "status": "RUNNING",
        "workspace": {"environment": {"id": "env-dev", "name": "DEV"}},
    }
]

_ARTIFACTS = [
    {"id": "art-dev", "name": "dev_route", "type": "route", "workspace": {"id": "ws-dev"}},
    {"id": "art-prod", "name": "prod_route", "type": "route", "workspace": {"id": "ws-prod"}},
]

_TASKS_BY_ENVIRONMENT = {
    "env-dev": [
        {
            "executable": "task-dev",
            "name": "dev-load",
            "artifactId": "art-dev",
            "workspace": {"id": "ws-dev", "name": "Dev WS", "environment": {"id": "env-dev", "name": "DEV"}},
            "runtime": {"type": "REMOTE_ENGINE", "id": "eng-shared"},
        }
    ],
    "env-prod": [
        {
            "executable": "task-prod",
            "name": "prod-load",
            "artifactId": "art-prod",
            "workspace": {"id": "ws-prod", "name": "Prod WS", "environment": {"id": "env-prod", "name": "PROD"}},
            "runtime": {"type": "REMOTE_ENGINE", "id": "eng-shared"},
        }
    ],
    "env-empty": [],
}

_PLANS_BY_ENVIRONMENT = {
    "env-dev": [],
    "env-prod": [
        {
            "executable": "plan-prod",
            "name": "prod-orchestration",
            "workspace": {"id": "ws-prod", "name": "Prod WS"},
            "chart": {"flows": [{"workspaceId": "ws-prod", "id": "task-prod"}], "nextStep": None},
        }
    ],
    "env-empty": [],
}


@pytest.fixture
def tmc():
    s = MagicMock()
    s.talend_tmc_base_url = "https://tmc.example.com"
    s.talend_tmc_request_timeout = 10.0
    s.runtime_sync_source_key = "talend_tmc"
    s.talend_tmc_failed_log_run_limit = 100
    s.talend_tmc_component_metric_run_limit = 100
    s.talend_tmc_runs_per_executable_limit = 250
    def build(handler):
        # Mirrors what ``sync()`` does with a source's stored credentials, so
        # these tests exercise the same configured-instance path.
        provider = TalendTmcRuntimeProvider(transport=httpx.MockTransport(handler))
        provider._token = _CREDENTIALS["api_token"]
        provider._base_url_override = _CREDENTIALS["base_url"]
        return provider

    with patch("src.runtime.providers.talend_tmc.settings", s):
        yield SimpleNamespace(settings=s, provider=build)


def _envelope(items):
    return {"items": items, "total": len(items)}


def _tenant_handler(seen: list[httpx.Request]):
    """Serve a three-environment tenant, honouring ``environmentId`` like TMC does."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        environment_id = request.url.params.get("environmentId")
        if path == "/orchestration/environments":
            return httpx.Response(200, json=_envelope(_ENVIRONMENTS))
        if path == "/orchestration/workspaces":
            return httpx.Response(200, json=_envelope(_WORKSPACES))
        if path == "/processing/runtimes/remote-engine-clusters":
            return httpx.Response(200, json=_envelope([]))
        if path == "/processing/runtimes/remote-engines":
            return httpx.Response(200, json=_envelope(_ENGINES))
        if path == "/orchestration/artifacts":
            return httpx.Response(200, json=_envelope(_ARTIFACTS))
        if path == "/orchestration/connections":
            return httpx.Response(200, json=_envelope([]))
        if path == "/orchestration/executables/tasks":
            if environment_id is None:
                items = [item for page in _TASKS_BY_ENVIRONMENT.values() for item in page]
            else:
                items = _TASKS_BY_ENVIRONMENT.get(environment_id, [])
            return httpx.Response(200, json=_envelope(items))
        if path.startswith("/orchestration/executables/tasks/"):
            task_id = path.rsplit("/", 1)[-1]
            task = next(
                (
                    item
                    for page in _TASKS_BY_ENVIRONMENT.values()
                    for item in page
                    if item["executable"] == task_id
                ),
                None,
            )
            if task is not None:
                return httpx.Response(
                    200,
                    json={
                        "id": task_id,
                        "artifact": {"id": task["artifactId"]},
                        "parameters": {},
                    },
                )
        if path == "/orchestration/executables/plans":
            if environment_id is None:
                items = [item for page in _PLANS_BY_ENVIRONMENT.values() for item in page]
            else:
                items = _PLANS_BY_ENVIRONMENT.get(environment_id, [])
            return httpx.Response(200, json=_envelope(items))
        if path.endswith("/executions"):
            return httpx.Response(200, json=_envelope([]))
        raise AssertionError(f"Unexpected request {request.method} {path}")

    return handler


class TestScopeDiscovery:
    async def test_lists_environments_with_task_counts(self, tmc):
        scopes = await tmc.provider(_tenant_handler([])).discover_scopes(_CREDENTIALS)

        assert [(scope.id, scope.label, scope.detail) for scope in scopes] == [
            ("env-dev", "DEV", "1 task"),
            ("env-prod", "PROD", "1 task"),
            ("env-empty", "Scratch", "0 tasks"),
        ]

    async def test_empty_environments_are_offered_but_not_preselected(self, tmc):
        scopes = await tmc.provider(_tenant_handler([])).discover_scopes(_CREDENTIALS)

        assert {scope.id: scope.default_selected for scope in scopes} == {
            "env-dev": True,
            "env-prod": True,
            "env-empty": False,
        }

    async def test_a_failed_count_probe_still_offers_the_environment(self, tmc):
        """A missing count is cosmetic; hiding the environment is not."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/orchestration/environments":
                return httpx.Response(200, json=_envelope(_ENVIRONMENTS))
            if request.url.path == "/orchestration/executables/tasks":
                return httpx.Response(500, json={"detail": "counts unavailable"})
            raise AssertionError(f"Unexpected request {request.url.path}")

        scopes = await tmc.provider(handler).discover_scopes(_CREDENTIALS)

        assert [scope.id for scope in scopes] == ["env-dev", "env-prod", "env-empty"]
        assert all(scope.detail is None for scope in scopes)
        assert all(scope.default_selected is False for scope in scopes)

    async def test_missing_credentials_are_rejected_before_any_call(self, tmc):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("must not call TMC without credentials")

        with pytest.raises(RuntimeProviderError):
            await tmc.provider(handler).discover_scopes({"base_url": "https://tmc.example.com"})


class TestScopedSync:
    async def test_scoped_sync_only_asks_tmc_for_the_selected_environments(self, tmc):
        seen: list[httpx.Request] = []
        provider = tmc.provider(_tenant_handler(seen))
        provider._environment_ids = ("env-prod",)

        await provider.fetch_runtime_batch(project_id="project-a")

        executable_envs = {
            request.url.params.get("environmentId")
            for request in seen
            if request.url.path in {
                "/orchestration/executables/tasks",
                "/orchestration/executables/plans",
            }
        }
        assert executable_envs == {"env-prod"}
        # The whole point of scoping: no run-history fan-out for excluded work.
        assert not any("task-dev" in request.url.path for request in seen)
        assert any("task-prod" in request.url.path for request in seen)

    async def test_scoped_sync_excludes_other_environments_jobs(self, tmc):
        provider = tmc.provider(_tenant_handler([]))
        provider._environment_ids = ("env-prod",)

        batch = await provider.fetch_runtime_batch(project_id="project-a")

        assert [job.name for job in batch.jobs] == ["prod-load"]
        assert [plan.name for plan in batch.plans] == ["prod-orchestration"]
        assert [workspace.workspace_id for workspace in batch.workspaces] == ["ws-prod"]
        assert [artifact.name for artifact in batch.artifacts] == ["prod_route"]

    async def test_unscoped_sync_still_ingests_the_whole_tenant(self, tmc):
        """Sources registered before scope selection existed must not narrow."""
        seen: list[httpx.Request] = []
        provider = tmc.provider(_tenant_handler(seen))

        batch = await provider.fetch_runtime_batch(project_id="project-a")

        assert {job.name for job in batch.jobs} == {"dev-load", "prod-load"}
        assert all(
            request.url.params.get("environmentId") is None
            for request in seen
            if request.url.path == "/orchestration/executables/tasks"
        )

    async def test_environments_are_never_narrowed_by_scope(self, tmc):
        """Environments are the identity markers; a scoped subset would churn it.

        ``physical_identity_from_batch`` hashes the environment ids in the batch.
        If scoping shrank that set, every sync would recompute a different
        identity and a re-registration of the same tenant would slip past the
        duplicate guard.
        """
        provider = tmc.provider(_tenant_handler([]))
        unscoped = await provider.fetch_runtime_batch(project_id="project-a")

        scoped_provider = tmc.provider(_tenant_handler([]))
        scoped_provider._environment_ids = ("env-prod",)
        scoped = await scoped_provider.fetch_runtime_batch(project_id="project-a")

        assert [item.environment_id for item in scoped.environments] == [
            "env-dev",
            "env-prod",
            "env-empty",
        ]
        assert provider.physical_identity_from_batch(
            _CREDENTIALS, scoped
        ) == provider.physical_identity_from_batch(_CREDENTIALS, unscoped)

    async def test_an_engine_outside_the_scope_is_kept_when_a_scoped_task_uses_it(self, tmc):
        """Dropping it would blank the engine name on every run of that job."""
        provider = tmc.provider(_tenant_handler([]))
        provider._environment_ids = ("env-prod",)

        batch = await provider.fetch_runtime_batch(project_id="project-a")

        assert [engine.engine_id for engine in batch.engines] == ["eng-shared"]
        assert [job.engine_name for job in batch.jobs] == ["shared-engine"]

    async def test_a_scope_that_no_longer_exists_fails_loudly(self, tmc):
        """A silently smaller batch reads downstream as "those jobs stopped existing"."""
        provider = tmc.provider(_tenant_handler([]))
        provider._environment_ids = ("env-prod", "env-deleted")

        with pytest.raises(RuntimeProviderError, match="no longer visible"):
            await provider.fetch_runtime_batch(project_id="project-a")

    async def test_a_scope_with_no_tasks_fails_loudly(self, tmc):
        provider = tmc.provider(_tenant_handler([]))
        provider._environment_ids = ("env-empty",)

        with pytest.raises(RuntimeProviderError, match="No Talend TMC tasks"):
            await provider.fetch_runtime_batch(project_id="project-a")


class TestEnvironmentIdParsing:
    def test_absent_selection_means_the_whole_tenant(self):
        assert _environment_ids({}) == ()
        assert _environment_ids({"environment_ids": None}) == ()

    def test_list_is_deduplicated_and_stripped(self):
        assert _environment_ids({"environment_ids": [" env-a ", "env-b", "env-a", ""]}) == (
            "env-a",
            "env-b",
        )

    def test_comma_separated_string_is_accepted(self):
        assert _environment_ids({"environment_ids": "env-a, env-b"}) == ("env-a", "env-b")

    def test_unusable_type_is_rejected(self):
        with pytest.raises(RuntimeProviderError):
            _environment_ids({"environment_ids": {"env-a": True}})
