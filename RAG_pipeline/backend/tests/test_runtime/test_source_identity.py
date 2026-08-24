"""Physical-source identity: one identity per tenant, not per endpoint.

Every Talend Cloud customer in a region shares one base URL, so an identity
derived from the URL alone rejects the second customer registered as a duplicate
of the first. These tests pin the narrowing rule, the repair path for rows
written under the old rule, and the refresh that keeps an identity aligned with
its tenant as environments come and go.
"""

import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.db.models import runtime_data  # noqa: F401 - registers the source tables
from src.runtime.models import RuntimeEnvironment
from src.runtime.providers import RuntimeProviderError
from src.runtime.providers.base import SingleTenantIdentityMixin, compose_tenant_identity
from src.runtime.providers.talend_tmc import TalendTmcRuntimeProvider
from src.runtime.store import RuntimeStoreRepository
from src.runtime.sync import RuntimeSyncBatch

_EU = "https://api.eu.cloud.talend.com"
_TENANT_A = ["env-a1", "env-a2"]
_TENANT_B = ["env-b1"]


def _credentials(token: str, base_url: str = _EU) -> dict[str, str]:
    return {"api_token": token, "base_url": base_url}


def _environments_handler(environments_by_token: dict[str, list[str]]):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/orchestration/environments"
        token = request.headers["Authorization"].removeprefix("Bearer ")
        environment_ids = environments_by_token.get(token)
        if environment_ids is None:
            return httpx.Response(401, json={"error": "unauthorized"})
        return httpx.Response(
            200,
            json={
                "items": [{"id": item, "name": item.upper()} for item in environment_ids],
                "total": len(environment_ids),
            },
        )

    return handler


@pytest.fixture
def tmc():
    s = MagicMock()
    s.talend_tmc_request_timeout = 10.0
    s.runtime_sync_source_key = "talend_tmc"
    with patch("src.runtime.providers.talend_tmc.settings", s):
        yield SimpleNamespace(
            provider=lambda handler: TalendTmcRuntimeProvider(transport=httpx.MockTransport(handler))
        )


def _environment(environment_id: str) -> RuntimeEnvironment:
    return RuntimeEnvironment(
        source_key="talend_tmc",
        environment_id=environment_id,
        name=environment_id.upper(),
        last_seen_at=datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC),
    )


class TestTenantNarrowing:
    async def test_two_accounts_on_one_endpoint_get_different_identities(self, tmc):
        provider = tmc.provider(_environments_handler({"token-a": _TENANT_A, "token-b": _TENANT_B}))

        identity_a = await provider.resolve_physical_identity(_credentials("token-a"))
        identity_b = await provider.resolve_physical_identity(_credentials("token-b"))

        assert identity_a != identity_b
        assert identity_a.startswith(f"tmc:{_EU}#")
        assert identity_b.startswith(f"tmc:{_EU}#")

    async def test_endpoint_identity_stays_the_shared_prefix(self, tmc):
        provider = tmc.provider(_environments_handler({"token-a": _TENANT_A}))

        endpoint = provider.physical_source_identity(_credentials("token-a"))
        resolved = await provider.resolve_physical_identity(_credentials("token-a"))

        assert endpoint == f"tmc:{_EU}"
        assert resolved.startswith(f"{endpoint}#")

    async def test_identity_does_not_depend_on_environment_order(self, tmc):
        forward = tmc.provider(_environments_handler({"t": ["env-1", "env-2", "env-3"]}))
        reversed_ = tmc.provider(_environments_handler({"t": ["env-3", "env-1", "env-2"]}))

        assert await forward.resolve_physical_identity(_credentials("t")) == (
            await reversed_.resolve_physical_identity(_credentials("t"))
        )

    async def test_identity_carries_no_token_material(self, tmc):
        token = "super-secret-talend-token-value"
        provider = tmc.provider(_environments_handler({token: _TENANT_A}))

        identity = await provider.resolve_physical_identity(_credentials(token))

        assert token not in identity

    async def test_token_without_environments_cannot_identify_an_account(self, tmc):
        provider = tmc.provider(_environments_handler({"t": []}))

        with pytest.raises(RuntimeProviderError, match="no environments"):
            await provider.resolve_physical_identity(_credentials("t"))

    async def test_unreachable_endpoint_raises_provider_error(self, tmc):
        provider = tmc.provider(_environments_handler({"other": _TENANT_A}))

        with pytest.raises(RuntimeProviderError):
            await provider.resolve_physical_identity(_credentials("t"))

    async def test_missing_credentials_raise_before_any_call(self, tmc):
        provider = tmc.provider(_environments_handler({}))

        with pytest.raises(RuntimeProviderError, match="Missing required TMC credentials"):
            await provider.resolve_physical_identity({"base_url": _EU})

    async def test_batch_identity_matches_the_resolved_identity(self, tmc):
        provider = tmc.provider(_environments_handler({"t": _TENANT_A}))
        batch = RuntimeSyncBatch(environments=[_environment(item) for item in _TENANT_A])

        assert provider.physical_identity_from_batch(_credentials("t"), batch) == (
            await provider.resolve_physical_identity(_credentials("t"))
        )

    def test_batch_without_environments_keeps_the_stored_identity(self, tmc):
        provider = tmc.provider(_environments_handler({}))

        assert provider.physical_identity_from_batch(_credentials("t"), RuntimeSyncBatch()) is None


class TestComposeTenantIdentity:
    def test_markers_are_deduplicated_and_whitespace_trimmed(self):
        assert compose_tenant_identity("tmc:x", [" a ", "a", "b"]) == compose_tenant_identity(
            "tmc:x", ["b", "a"]
        )

    def test_empty_markers_are_rejected(self):
        with pytest.raises(ValueError):
            compose_tenant_identity("tmc:x", ["", "  "])


class TestSingleTenantProviders:
    async def test_endpoint_identity_is_used_unchanged(self):
        class _Provider(SingleTenantIdentityMixin):
            def physical_source_identity(self, credentials):
                return "local:only-one-tenant"

        provider = _Provider()

        assert await provider.resolve_physical_identity({}) == "local:only-one-tenant"
        assert provider.physical_identity_from_batch({}, RuntimeSyncBatch()) is None


class TestUnresolvedIdentityLookup:
    async def test_finds_legacy_and_endpoint_scoped_rows_only(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        legacy = await repo.create_source_config(
            project_id="p1", source_type="tmc", physical_identity="legacy:tmc-1", label="Legacy"
        )
        endpoint_scoped = await repo.create_source_config(
            project_id="p2", source_type="tmc", physical_identity=f"tmc:{_EU}", label="Pre-narrowing"
        )
        narrowed = await repo.create_source_config(
            project_id="p3",
            source_type="tmc",
            physical_identity=compose_tenant_identity(f"tmc:{_EU}", _TENANT_A),
            label="Current",
        )
        other_endpoint = await repo.create_source_config(
            project_id="p4",
            source_type="tmc",
            physical_identity="tmc:https://api.us.cloud.talend.com",
            label="Other region",
        )

        found = await repo.list_unresolved_identity_source_ids({"tmc"}, f"tmc:{_EU}")

        assert set(found) == {legacy.id, endpoint_scoped.id}
        assert narrowed.id not in found
        assert other_endpoint.id not in found

    async def test_other_source_types_are_never_returned(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.create_source_config(
            project_id="p1", source_type="mock", physical_identity="legacy:mock-1", label="Mock"
        )

        assert await repo.list_unresolved_identity_source_ids({"tmc"}, f"tmc:{_EU}") == []
