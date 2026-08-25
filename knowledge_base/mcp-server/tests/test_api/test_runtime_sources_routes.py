"""The dashboard's runtime-source proxy: /mcp/runtime-sources.

These routes exist so a Talend Management Console connection can be added from
the Connectors page instead of a second application, while its credential still
lands in the RAG backend's ``runtime_sources`` tables where the mirroring
scheduler reads it.

Two things here are load-bearing and easy to regress, so both are pinned:

* the connector id ``talend_tmc`` is NOT the RAG runtime provider key -- that is
  ``tmc``, and posting the connector id straight through earns a 400 "Unknown
  runtime source type";
* the proxy authenticates as the CALLER (forwarded cookie), not as the service
  (machine key), which is what keeps the RAG backend's per-project
  ``runtime_sources:write`` check in force.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import text

from mcp_server.services.runtime_sources_bridge import (
    ForwardedIdentity,
    RuntimeSourcesBridge,
    RuntimeSourcesBridgeError,
)


class FakeBridge:
    """Stands in for the RAG backend, recording exactly what the routes asked for."""

    def __init__(self, *, sources=None, error: RuntimeSourcesBridgeError | None = None):
        self.sources = sources if sources is not None else []
        self.error = error
        self.create_calls: list[dict] = []
        self.discover_calls: list[dict] = []
        self.sync_calls: list[int] = []
        self.delete_calls: list[int] = []
        self.identities: list[ForwardedIdentity] = []

    def _maybe_raise(self):
        if self.error is not None:
            raise self.error

    async def list_source_types(self, identity):
        self.identities.append(identity)
        self._maybe_raise()
        return [
            {
                "provider_key": "tmc",
                "provider_label": "Talend Management Console",
                "credential_schema": [
                    {"name": "api_token", "type": "secret", "label": "API token", "required": True},
                    {"name": "base_url", "type": "url", "label": "Base URL", "required": True},
                ],
            }
        ]

    async def list_sources(self, identity, *, project_id=None, connector_provider_id=None):
        self.identities.append(identity)
        self._maybe_raise()
        return list(self.sources)

    async def create_source(self, identity, **kwargs):
        self.identities.append(identity)
        self.create_calls.append(kwargs)
        self._maybe_raise()
        return {
            "id": 42,
            "project_id": kwargs["project_id"],
            "source_type": "tmc",
            "label": kwargs["label"],
            "status": "pending",
            "enabled": True,
            "sync_interval_seconds": kwargs["sync_interval_seconds"],
            "last_synced_at": None,
        }

    async def discover_scopes(self, identity, *, connector_provider_id, credentials):
        self.identities.append(identity)
        self.discover_calls.append(
            {"connector_provider_id": connector_provider_id, "credentials": credentials}
        )
        self._maybe_raise()
        return [
            {"id": "env-prod", "label": "PROD", "detail": "32 tasks", "default_selected": True},
            {"id": "env-empty", "label": "Scratch", "detail": "0 tasks", "default_selected": False},
        ]

    async def sync_source(self, identity, source_id):
        self.identities.append(identity)
        self.sync_calls.append(source_id)
        self._maybe_raise()
        return {"source": None, "status": "success", "records_ingested": 7}

    async def delete_source(self, identity, source_id):
        self.identities.append(identity)
        self.delete_calls.append(source_id)
        self._maybe_raise()


@pytest.fixture
def bridge(client):
    """Install a fake bridge on app state; the routes prefer it over a real one."""
    from mcp_server.api.app import app

    fake = FakeBridge()
    app.state.runtime_sources_bridge = fake
    yield fake
    app.state.runtime_sources_bridge = None


class TestCreate:
    async def test_translates_the_connector_id_to_the_runtime_provider_key(self, client, bridge):
        """`talend_tmc` is the catalogue id; the RAG registry only knows `tmc`.

        `get_source_provider("talend_tmc")` returns None, so a proxy that passed
        the connector id through would 400 on every single connect attempt.
        """
        response = await client.post(
            "/mcp/runtime-sources",
            json={
                "provider_id": "talend_tmc",
                "project_id": "werkorder",
                "label": "Talend TMC",
                "credentials": {"api_token": "fake", "base_url": "https://eu.cloud.talend.com"},
            },
        )

        assert response.status_code == 201, response.text
        assert bridge.create_calls[0]["connector_provider_id"] == "talend_tmc"
        # The bridge -- not the browser, and not this route -- owns the mapping,
        # so the assertion is that the connector id reached it intact.
        assert response.json()["source_type"] == "tmc"

    async def test_never_echoes_the_credential_back(self, client, bridge):
        response = await client.post(
            "/mcp/runtime-sources",
            json={
                "provider_id": "talend_tmc",
                "project_id": "werkorder",
                "label": "Talend TMC",
                "credentials": {"api_token": "super-secret-token", "base_url": "https://x"},
            },
        )

        assert response.status_code == 201
        assert "super-secret-token" not in response.text
        assert "credentials" not in response.json()

    async def test_defaults_the_sync_interval_and_enforces_the_floor(self, client, bridge):
        ok = await client.post(
            "/mcp/runtime-sources",
            json={
                "provider_id": "talend_tmc",
                "project_id": "werkorder",
                "label": "TMC",
                "credentials": {"api_token": "t", "base_url": "https://x"},
            },
        )
        assert ok.status_code == 201
        assert bridge.create_calls[0]["sync_interval_seconds"] == 60

        too_fast = await client.post(
            "/mcp/runtime-sources",
            json={
                "provider_id": "talend_tmc",
                "project_id": "werkorder",
                "label": "TMC",
                "credentials": {"api_token": "t", "base_url": "https://x"},
                "sync_interval_seconds": 5,
            },
        )
        # The RAG model's own floor is ge=30; rejecting here keeps the error
        # local instead of spending a round trip to be told the same thing.
        assert too_fast.status_code == 422

    async def test_rejects_a_provider_that_is_not_runtime_managed(self, client, bridge):
        """Elastic's credential belongs in the connector store, not this lane."""
        response = await client.post(
            "/mcp/runtime-sources",
            json={
                "provider_id": "elastic_stack",
                "project_id": "werkorder",
                "label": "Elastic",
                "credentials": {"api_token": "t"},
            },
        )

        assert response.status_code == 400
        assert "elastic_stack" in response.json()["detail"]
        assert bridge.create_calls == []

    async def test_relays_the_rag_backends_own_conflict_verbatim(self, client, bridge):
        """A 409 here is genuinely useful and must not be flattened to 500.

        The RAG backend sanitises its error strings before they leave it, so
        relaying the text is safe as well as informative.
        """
        bridge.error = RuntimeSourcesBridgeError(
            409, "That physical runtime source is already registered"
        )

        response = await client.post(
            "/mcp/runtime-sources",
            json={
                "provider_id": "talend_tmc",
                "project_id": "werkorder",
                "label": "TMC",
                "credentials": {"api_token": "t", "base_url": "https://x"},
            },
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "That physical runtime source is already registered"

    async def test_relays_a_validation_failure_as_the_backend_worded_it(self, client, bridge):
        bridge.error = RuntimeSourcesBridgeError(400, "401 Unauthorized")

        response = await client.post(
            "/mcp/runtime-sources",
            json={
                "provider_id": "talend_tmc",
                "project_id": "werkorder",
                "label": "TMC",
                "credentials": {"api_token": "bogus", "base_url": "https://x"},
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "401 Unauthorized"


class TestList:
    async def test_reports_sources_with_the_project_they_are_bound_to(self, client, bridge):
        bridge.sources = [
            {
                "id": 35,
                "project_id": "werkorder",
                "source_type": "talend_tmc",
                "label": "Talend TMC",
                "status": "success",
                "enabled": True,
                "sync_interval_seconds": 60,
                "last_synced_at": "2026-08-24T10:00:00Z",
            }
        ]

        response = await client.get("/mcp/runtime-sources?provider_id=talend_tmc")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["sources"][0]["project_id"] == "werkorder"
        assert body["sources"][0]["status"] == "success"

    async def test_rejects_an_unmapped_provider_filter(self, client, bridge):
        response = await client.get("/mcp/runtime-sources?provider_id=elastic_stack")
        assert response.status_code == 400

    async def test_an_unfamiliar_status_survives_parsing(self, client, bridge):
        """Status is a plain string on purpose: a new value must not blank the row."""
        bridge.sources = [
            {
                "id": 1,
                "project_id": "p",
                "source_type": "tmc",
                "label": "l",
                "status": "quarantined_by_a_newer_backend",
                "enabled": True,
                "sync_interval_seconds": 60,
            }
        ]

        response = await client.get("/mcp/runtime-sources")

        assert response.status_code == 200
        assert response.json()["sources"][0]["status"] == "quarantined_by_a_newer_backend"


class TestSourceTypes:
    async def test_serves_the_providers_own_credential_schema(self, client, bridge):
        """The form is generated from this, so it cannot offer a field TMC lacks."""
        response = await client.get("/mcp/runtime-sources/source-types")

        assert response.status_code == 200
        tmc = response.json()["source_types"][0]
        assert tmc["provider_key"] == "tmc"
        assert [field["name"] for field in tmc["credential_schema"]] == ["api_token", "base_url"]
        assert tmc["credential_schema"][0]["type"] == "secret"


class TestSyncAndDelete:
    async def test_sync_now_reports_what_was_mirrored(self, client, bridge):
        response = await client.post("/mcp/runtime-sources/35/sync")

        assert response.status_code == 200
        assert bridge.sync_calls == [35]
        assert response.json()["records_ingested"] == 7

    async def test_delete_returns_no_content(self, client, bridge):
        response = await client.delete("/mcp/runtime-sources/35")

        assert response.status_code == 204
        assert bridge.delete_calls == [35]


class TestAuthorization:
    """The MCP-side gate: the same workspace permission that guards connectors."""

    async def _demote(self, session_factory):
        async with session_factory() as session:
            await session.execute(text("UPDATE users SET is_superadmin = 0 WHERE id = 1"))
            await session.commit()

    async def test_writes_require_connector_management(self, client, bridge, session_factory):
        await self._demote(session_factory)

        response = await client.post(
            "/mcp/runtime-sources",
            json={
                "provider_id": "talend_tmc",
                "project_id": "werkorder",
                "label": "TMC",
                "credentials": {"api_token": "t", "base_url": "https://x"},
            },
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Connector management permission required"
        # The gate runs BEFORE the credential leaves this process.
        assert bridge.create_calls == []

    async def test_sync_and_delete_require_connector_management(
        self, client, bridge, session_factory
    ):
        await self._demote(session_factory)

        assert (await client.post("/mcp/runtime-sources/35/sync")).status_code == 403
        assert (await client.delete("/mcp/runtime-sources/35")).status_code == 403
        assert bridge.sync_calls == []
        assert bridge.delete_calls == []

    async def test_reads_require_connector_view(self, client, bridge, session_factory):
        await self._demote(session_factory)

        assert (await client.get("/mcp/runtime-sources")).status_code == 403
        assert (await client.get("/mcp/runtime-sources/source-types")).status_code == 403


class TestForwardedIdentity:
    """The proxy's whole security posture is in these headers."""

    def test_forwards_the_callers_session_and_csrf_token(self):
        identity = ForwardedIdentity.from_cookies(
            {"access_token": "jwt-value", "csrf_token": "csrf-value"}
        )

        headers = identity.headers()

        # The RAG backend's get_current_user reads the access_token COOKIE; a
        # bearer machine key is answered 401, which is why this is a cookie.
        assert "access_token=jwt-value" in headers["Cookie"]
        # Runtime-source routes are not CSRF-exempt (only /ingest/ and /usage/
        # are), so the double-submit header is required on every write.
        assert headers["X-CSRF-Token"] == "csrf-value"

    def test_drops_cookies_the_rag_backend_has_no_business_seeing(self):
        identity = ForwardedIdentity.from_cookies(
            {"access_token": "jwt", "session_hint": "unrelated", "ga_id": "tracking"}
        )

        cookie_header = identity.headers()["Cookie"]

        assert "session_hint" not in cookie_header
        assert "ga_id" not in cookie_header

    def test_sends_no_authorization_header_at_all(self):
        """Proving the machine key is not in play.

        Authenticating as the service would erase the RAG backend's per-project
        runtime_sources:write check and misattribute the audit entry.
        """
        headers = ForwardedIdentity.from_cookies({"access_token": "jwt"}).headers()

        assert "Authorization" not in headers

    def test_an_anonymous_identity_sends_nothing_rather_than_a_blank_cookie(self):
        assert ForwardedIdentity.from_cookies({}).headers() == {}


class TestBridgeTransport:
    """End-to-end through httpx, against a stub standing in for the RAG backend."""

    async def test_posts_the_runtime_provider_key_not_the_connector_id(self):
        seen: dict = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            import json

            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            seen["headers"] = dict(request.headers)
            return httpx.Response(200, json={"source": {"id": 1, "project_id": "p", "source_type": "tmc"}})

        bridge = RuntimeSourcesBridge(
            base_url="http://rag.test", transport=httpx.MockTransport(handler)
        )
        await bridge.create_source(
            ForwardedIdentity.from_cookies({"access_token": "jwt", "csrf_token": "c"}),
            connector_provider_id="talend_tmc",
            project_id="werkorder",
            label="TMC",
            credentials={"api_token": "t", "base_url": "https://eu.cloud.talend.com"},
            sync_interval_seconds=60,
        )

        assert seen["url"] == "http://rag.test/api/v1/runtime/sources"
        # The mapping that makes the whole feature work.
        assert seen["body"]["provider_key"] == "tmc"
        assert seen["body"]["project_id"] == "werkorder"
        assert seen["headers"]["x-csrf-token"] == "c"
        assert "authorization" not in seen["headers"]

    async def test_list_filters_to_the_connectors_own_source_types(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"sources": [
                {"id": 1, "source_type": "tmc", "project_id": "a"},
                # A row written before the provider key was shortened. Live data
                # contains both spellings; dropping this one would report a
                # connected project as unconnected.
                {"id": 2, "source_type": "talend_tmc", "project_id": "b"},
                {"id": 3, "source_type": "mock", "project_id": "c"},
            ]})

        bridge = RuntimeSourcesBridge(
            base_url="http://rag.test", transport=httpx.MockTransport(handler)
        )
        sources = await bridge.list_sources(
            ForwardedIdentity.from_cookies({"access_token": "j"}),
            connector_provider_id="talend_tmc",
        )

        assert [s["id"] for s in sources] == [1, 2]

    async def test_surfaces_the_backends_status_and_detail(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={"detail": "That physical runtime source is already registered"})

        bridge = RuntimeSourcesBridge(
            base_url="http://rag.test", transport=httpx.MockTransport(handler)
        )

        with pytest.raises(RuntimeSourcesBridgeError) as caught:
            await bridge.delete_source(ForwardedIdentity.from_cookies({"access_token": "j"}), 1)

        assert caught.value.status_code == 409
        assert caught.value.detail == "That physical runtime source is already registered"

    async def test_an_unreachable_backend_is_a_502_not_a_traceback(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        bridge = RuntimeSourcesBridge(
            base_url="http://rag.test", transport=httpx.MockTransport(handler)
        )

        with pytest.raises(RuntimeSourcesBridgeError) as caught:
            await bridge.list_sources(ForwardedIdentity.from_cookies({"access_token": "j"}))

        assert caught.value.status_code == 502


class TestDiscover:
    """Scope discovery is the dialog's "test connection" step.

    It writes nothing, but it accepts a credential and causes a live vendor
    call, so it is guarded as a management action rather than a read.
    """

    async def test_forwards_the_connector_id_and_credentials(self, client, bridge):
        response = await client.post(
            "/mcp/runtime-sources/discover",
            json={
                "provider_id": "talend_tmc",
                "credentials": {"api_token": "fake", "base_url": "https://eu.cloud.talend.com"},
            },
        )

        assert response.status_code == 200, response.text
        assert bridge.discover_calls[0]["connector_provider_id"] == "talend_tmc"
        assert bridge.discover_calls[0]["credentials"]["base_url"] == "https://eu.cloud.talend.com"
        assert [scope["id"] for scope in response.json()["scopes"]] == ["env-prod", "env-empty"]

    async def test_never_echoes_the_credential_back(self, client, bridge):
        response = await client.post(
            "/mcp/runtime-sources/discover",
            json={
                "provider_id": "talend_tmc",
                "credentials": {"api_token": "super-secret-token", "base_url": "https://x"},
            },
        )

        assert response.status_code == 200
        assert "super-secret-token" not in response.text
