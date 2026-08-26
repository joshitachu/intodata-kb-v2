"""Proxy to the RAG backend's runtime-sources API, on the caller's own identity.

Why a proxy at all
------------------
A Talend Management Console credential MUST land in the RAG backend's
``runtime_sources`` tables: a scheduler in that service reads it every 60
seconds to mirror executions, and documentation reads bounded aggregates back
out of that mirror. Writing it into the MCP connector store instead would look
connected and never sync. So the storage stays where it is and only the user
experience moves into the dashboard's Connectors page.

Why the caller's cookie and not the machine key
-----------------------------------------------
``RagBridge`` authenticates to the RAG backend with ``MCP_INGEST_API_KEY``, a
machine credential with no user scope. That key does not work here, and could
not be made to work without weakening the RAG side:

* ``/api/v1/runtime/sources`` depends on ``RuntimeSourceReader`` /
  ``RuntimeSourceWriter``, which resolve through ``get_current_user`` -- a
  function that reads the ``access_token`` **cookie** and nothing else. A
  ``Authorization: Bearer <key>`` request is answered ``401 Not authenticated``
  (verified against the running backend). Unlike ``/api/v1/ingest/*``, these
  routes are also NOT in the CSRF middleware's exempt prefixes.
* Teaching those routes to accept the machine key would delete the per-project
  ``RUNTIME_SOURCES_WRITE`` check for every caller, not just this one, and would
  leave the audit log attributing operator actions to a service account.

Both services validate the *same* JWT: ``JWT_SECRET_KEY`` is one environment
variable shared by the ``backend`` and ``mcp-server`` compose services, and the
dashboard's session cookie is therefore already a valid RAG credential. So this
bridge forwards the caller's own ``access_token`` and ``csrf_token`` cookies and
lets the RAG backend authenticate and authorise the real human.

This is not a new pattern: the RAG app's own Next.js proxy
(``RAG_pipeline/frontend/lib/proxyHeaders.ts``) reaches these same routes the
same way -- raw ``Cookie`` header plus an ``X-CSRF-Token`` echoed from the
``csrf_token`` cookie.

Security consequences, stated plainly:

* ENFORCED, by the RAG backend, on the real user: ``runtime_sources:read`` /
  ``runtime_sources:write``, the per-project scope check
  (``_assert_scope_allows``), MFA enrolment, and the runtime audit-log entry.
* ENFORCED, additionally, by this service: ``manage_connectors`` /
  ``view_connectors`` (see ``api/routes/runtime_sources.py``).
* BYPASSED: nothing. A user who cannot create a runtime source in the RAG app
  cannot create one through the dashboard either -- they receive that service's
  own 401/403 verbatim.

Credential handling: credentials travel one way. Nothing in this module logs a
request body, and the RAG backend never returns a credential in a response.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from mcp_server.config import config

logger = logging.getLogger(__name__)

# Cookies the RAG backend needs to authenticate and CSRF-check the caller.
# An explicit allowlist rather than the raw inbound Cookie header: the dashboard
# origin may carry unrelated cookies, and forwarding a bearer-ish value to
# another service by accident is exactly the kind of leak worth designing out.
_FORWARDED_COOKIES = ("access_token", "csrf_token")

# Connector catalogue provider_id -> RAG runtime-source ``provider_key``.
#
# These genuinely differ. The connector manifest registers ``talend_tmc``, while
# ``RuntimeSourceProvider.provider_key()`` returns ``tmc`` and the create route
# rejects anything ``get_source_provider`` does not know -- so posting the
# connector id straight through answers 400 "Unknown runtime source type".
# One mapping, owned here, keeps that translation out of the browser.
_PROVIDER_KEY_BY_CONNECTOR_ID = {
    "talend_tmc": "tmc",
}

# Values seen in ``runtime_sources.source_type`` for a given connector provider.
# Rows created before self-service configuration standardised on the short key
# were stored as ``talend_tmc``, and both spellings exist in live data, so a row
# filter that matched only the current key would report a connected project as
# unconnected.
_SOURCE_TYPES_BY_CONNECTOR_ID = {
    "talend_tmc": frozenset({"tmc", "talend_tmc"}),
}


def runtime_provider_key(connector_provider_id: str) -> str | None:
    """Return the RAG ``provider_key`` for a connector id, or None if unmapped."""
    return _PROVIDER_KEY_BY_CONNECTOR_ID.get(connector_provider_id)


def runtime_source_types(connector_provider_id: str) -> frozenset[str]:
    """Return every ``source_type`` value that belongs to this connector."""
    return _SOURCE_TYPES_BY_CONNECTOR_ID.get(connector_provider_id, frozenset())


def runtime_managed_connector_ids() -> frozenset[str]:
    """Connector providers whose credential lives in the runtime-sources store."""
    return frozenset(_PROVIDER_KEY_BY_CONNECTOR_ID)


class RuntimeSourcesBridgeError(Exception):
    """A failure to be relayed to the dashboard with its original status.

    ``detail`` is whatever the RAG backend said. That service sanitises its own
    error strings (``_sanitize_error`` redacts Authorization/Bearer/api_key/
    token/secret) before they leave it, so relaying them verbatim keeps genuinely
    useful messages -- a 409 "That physical runtime source is already registered"
    -- without echoing a credential back to the browser.
    """

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(f"Runtime source request failed with {status_code}")
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ForwardedIdentity:
    """The caller's RAG credentials, lifted off the inbound dashboard request.

    Built by the route from ``request.cookies`` so the bridge itself stays a
    plain HTTP client with no FastAPI dependency (and so tests can construct one
    without a Request).
    """

    cookies: dict[str, str]

    @classmethod
    def from_cookies(cls, cookies: dict[str, str]) -> "ForwardedIdentity":
        return cls(cookies={
            name: value for name, value in cookies.items() if name in _FORWARDED_COOKIES
        })

    def headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.cookies:
            headers["Cookie"] = "; ".join(
                f"{name}={value}" for name, value in self.cookies.items()
            )
        # Double-submit CSRF: the RAG backend compares this header against the
        # csrf_token cookie. Runtime-source routes are not CSRF-exempt (only
        # /api/v1/ingest/ and /api/v1/usage/ are), so a write without it is a
        # 403 "csrf_token mismatch or missing" no matter how valid the JWT is.
        csrf = self.cookies.get("csrf_token")
        if csrf:
            headers["X-CSRF-Token"] = csrf
        return headers


class RuntimeSourcesBridge:
    """Thin HTTP client for ``/api/v1/runtime/*`` on the caller's behalf."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_s: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or config.rag_base_url).rstrip("/")
        self._timeout_s = timeout_s if timeout_s is not None else config.runtime_sources_timeout_s
        # Injection seam for tests, which assert on the exact headers this
        # bridge sends -- the forwarded cookie and CSRF token ARE the security
        # design here, so they need to be observable rather than taken on trust.
        self._transport = transport

    async def _request(
        self,
        method: str,
        path: str,
        identity: ForwardedIdentity,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_s, transport=self._transport
            ) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    params=params,
                    json=payload,
                    headers=identity.headers(),
                )
        except httpx.RequestError as exc:
            # Never interpolate the exception's request body; only the class and
            # the URL it was reaching for.
            logger.warning("Runtime-source proxy could not reach the RAG backend: %s", type(exc).__name__)
            raise RuntimeSourcesBridgeError(
                502, "The runtime-source service is unreachable."
            ) from exc

        if response.status_code >= 400:
            raise RuntimeSourcesBridgeError(response.status_code, _detail_of(response))
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def list_source_types(self, identity: ForwardedIdentity) -> list[dict[str, Any]]:
        """Return every runtime provider's credential schema.

        Proxied rather than duplicated in the dashboard so the connect form is
        generated from the provider's own ``credential_schema()`` and cannot
        drift into offering a field the provider does not have.
        """
        payload = await self._request("GET", "/api/v1/runtime/source-types", identity)
        return list((payload or {}).get("source_types") or [])

    async def list_sources(
        self,
        identity: ForwardedIdentity,
        *,
        project_id: str | None = None,
        connector_provider_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List runtime sources the caller may see, newest filter applied here.

        The RAG list route has no source-type filter, so narrowing to one
        connector happens on this side against the alias set.
        """
        params = {"project_id": project_id} if project_id else None
        payload = await self._request("GET", "/api/v1/runtime/sources", identity, params=params)
        sources = list((payload or {}).get("sources") or [])
        if connector_provider_id is not None:
            allowed = runtime_source_types(connector_provider_id)
            sources = [s for s in sources if str(s.get("source_type") or "") in allowed]
        return sources

    async def discover_scopes(
        self,
        identity: ForwardedIdentity,
        *,
        connector_provider_id: str,
        credentials: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Test unsaved credentials and return the ingest scopes they reach.

        Nothing is persisted on either side: the RAG backend validates, calls
        the vendor API, and answers. A 200 therefore IS the connection test, and
        the payload is what the operator chooses from before saving.
        """
        provider_key = runtime_provider_key(connector_provider_id)
        if provider_key is None:
            raise RuntimeSourcesBridgeError(
                400, f"{connector_provider_id!r} is not a runtime-managed connector."
            )
        payload = await self._request(
            "POST",
            "/api/v1/runtime/sources/discover",
            identity,
            payload={"provider_key": provider_key, "credentials": credentials},
        )
        return list((payload or {}).get("scopes") or [])

    async def create_source(
        self,
        identity: ForwardedIdentity,
        *,
        connector_provider_id: str,
        project_id: str,
        label: str,
        credentials: dict[str, Any],
        sync_interval_seconds: int,
    ) -> dict[str, Any]:
        """Register a runtime source, translating the connector id to a provider key."""
        provider_key = runtime_provider_key(connector_provider_id)
        if provider_key is None:
            raise RuntimeSourcesBridgeError(
                400, f"{connector_provider_id!r} is not a runtime-managed connector."
            )
        payload = await self._request(
            "POST",
            "/api/v1/runtime/sources",
            identity,
            payload={
                "project_id": project_id,
                "provider_key": provider_key,
                "label": label,
                "credentials": credentials,
                "sync_interval_seconds": sync_interval_seconds,
            },
        )
        return dict((payload or {}).get("source") or {})

    async def read_scope(
        self, identity: ForwardedIdentity, *, project_id: str
    ) -> dict[str, Any]:
        """The documentation read scope a run for this project will apply.

        Surfaced in the generate dialog because the scope changes what the run
        produces -- a project narrowed to production reports production's
        failure rate -- and an operator about to pay for a run should see that
        before it starts, not discover it in the published document.
        """
        payload = await self._request(
            "GET",
            "/api/v1/runtime/doc-read-scope",
            identity,
            params={"project_id": project_id},
        )
        return dict((payload or {}).get("scope") or {})

    async def list_read_grants(
        self, identity: ForwardedIdentity, source_id: int
    ) -> list[dict[str, Any]]:
        """Projects allowed to read this source's mirror without owning it.

        One vendor tenant commonly backs several scanned projects, and
        registering its credential twice is refused on purpose -- that would
        mean a second poll and two mirrors free to drift apart. Sharing is
        therefore a read grant, and the operator needs to see the grants to
        manage them.
        """
        payload = await self._request(
            "GET", f"/api/v1/runtime/sources/{source_id}/readers", identity
        )
        return [dict(item) for item in (payload or {}).get("grants") or []]

    async def add_read_grant(
        self,
        identity: ForwardedIdentity,
        source_id: int,
        *,
        project_id: str,
        environment_ids: list[str] | None,
        window_days: int | None,
    ) -> dict[str, Any]:
        payload = await self._request(
            "POST",
            f"/api/v1/runtime/sources/{source_id}/readers",
            identity,
            payload={
                "project_id": project_id,
                "environment_ids": environment_ids,
                "window_days": window_days,
            },
        )
        return dict((payload or {}).get("grant") or {})

    async def delete_read_grant(
        self, identity: ForwardedIdentity, source_id: int, project_id: str
    ) -> None:
        await self._request(
            "DELETE",
            f"/api/v1/runtime/sources/{source_id}/readers/{quote(project_id, safe='')}",
            identity,
        )

    async def delete_source(self, identity: ForwardedIdentity, source_id: int) -> None:
        await self._request("DELETE", f"/api/v1/runtime/sources/{source_id}", identity)

    async def sync_source(self, identity: ForwardedIdentity, source_id: int) -> dict[str, Any]:
        payload = await self._request(
            "POST", f"/api/v1/runtime/sources/{source_id}/sync", identity
        )
        return dict(payload or {})


def _detail_of(response: httpx.Response) -> Any:
    """Pull FastAPI's ``detail`` out of an error body, falling back to status text.

    Returns the parsed value (string or object) rather than a rendered string so
    the dashboard's existing error parser sees the same shape it sees from every
    other MCP route.
    """
    try:
        body = response.json()
    except ValueError:
        return response.reason_phrase or f"Request failed with {response.status_code}"
    if isinstance(body, dict) and "detail" in body:
        return body["detail"]
    return body
