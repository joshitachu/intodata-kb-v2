"""Adapter for an instance's control channel (``{mcp_url}/ops/control/*``).

Both the instance URL (``mcp_url``) and the bearer key (``control_key``) come
from the operator's ``clients.json``, never from a heartbeat snapshot: a
heartbeat is guarded only by the shared ``MC_API_KEY``, so anyone holding that
key could otherwise redirect the control key (and a signed licence) to a host
they control. This module knows nothing about FastAPI: resolution and transport
problems are typed exceptions, and each route decides which HTTP status they
become.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from .clients import ClientRegistry, default_registry

DEFAULT_TIMEOUT_SECONDS = 20.0


#: Hosts that may be addressed over plain ``http://`` (local development and
#: tests). Everything else must be ``https://``: the control key is a bearer.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class ResolutionFailure(str, enum.Enum):
    #: No ``clients.json`` entry: an unknown client (404).
    NO_CLIENT_ENTRY = "no_client_entry"
    NO_CONTROL_KEY = "no_control_key"
    #: The entry has no ``mcp_url``. There is deliberately no heartbeat fallback.
    NO_PINNED_URL = "no_pinned_url"
    #: ``http://`` to a non-loopback host, or credentials in the URL.
    INSECURE_URL = "insecure_url"
    #: Not an absolute http(s) URL with a host (or it carries a query/fragment).
    INVALID_URL = "invalid_url"


class InstanceResolutionError(Exception):
    """The instance cannot be addressed: no entry, control key or usable pinned URL."""

    def __init__(self, failure: ResolutionFailure, message: str) -> None:
        super().__init__(message)
        self.failure = failure
        self.message = message


class InstanceUnreachable(Exception):
    """Transport-level failure (DNS, connect, timeout, TLS)."""


class InstanceHTTPError(Exception):
    """The instance answered with a non-2xx status."""

    def __init__(self, status_code: int, text: str) -> None:
        super().__init__(f"instance returned {status_code}")
        self.status_code = status_code
        self.text = text


@dataclass(frozen=True)
class InstanceTarget:
    mcp_url: str
    control_key: str

    def __repr__(self) -> str:  # never print the control key
        return f"InstanceTarget(mcp_url={self.mcp_url!r}, control_key=***)"


@dataclass(frozen=True)
class InstanceReply:
    """A response from the instance, whatever its status."""

    status_code: int
    body: Any  # parsed JSON, or the raw text when the body is not JSON

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


def _checked_url(client_slug: str, mcp_url: str) -> str:
    """The pinned URL, normalised, or ``InstanceResolutionError``.

    ``https://`` is required unless the host is loopback; userinfo is refused
    (it would be sent to, and logged by, whatever host the URL names).
    """
    def fail(failure: ResolutionFailure, why: str) -> InstanceResolutionError:
        return InstanceResolutionError(
            failure, f"mcp_url for '{client_slug}' in clients.json {why}")

    try:
        parts = urlsplit(mcp_url)
        host = parts.hostname
        parts.port  # noqa: B018 -- raises ValueError on a malformed port
    except ValueError:
        raise fail(ResolutionFailure.INVALID_URL, "is not a valid URL") from None
    if parts.scheme not in ("http", "https") or not host:
        raise fail(ResolutionFailure.INVALID_URL,
                   "must be an absolute https:// URL with a host")
    if parts.query or parts.fragment:
        raise fail(ResolutionFailure.INVALID_URL, "must not carry a query or fragment")
    if "@" in parts.netloc:
        raise fail(ResolutionFailure.INSECURE_URL,
                   "must not contain credentials (user:password@)")
    if parts.scheme != "https" and host.lower() not in _LOOPBACK_HOSTS:
        raise fail(ResolutionFailure.INSECURE_URL,
                   "must use https:// (plain http is allowed only for localhost)")
    return mcp_url.rstrip("/")


class InstanceControlClient:
    def __init__(
        self,
        *,
        registry: ClientRegistry | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._registry = registry or default_registry
        self._timeout = timeout

    # -- addressing -------------------------------------------------------------

    def resolve(self, client_slug: str) -> InstanceTarget:
        """Address + key for a client's control channel, from ``clients.json`` only.

        Used by every control call (licence push and scan/generate triggers),
        since they all send the same control key. The heartbeat's ``mcp_url``
        is never used: heartbeats share one ``MC_API_KEY``, so a reported URL
        is attacker-controllable by anyone holding that key. An entry without
        a pinned ``mcp_url`` is refused, not guessed.
        """
        entry = self._registry.get(client_slug)
        if entry is None:
            raise InstanceResolutionError(
                ResolutionFailure.NO_CLIENT_ENTRY,
                f"Unknown client '{client_slug}': no entry in clients.json",
            )
        control_key = str(entry.get("control_key") or "")
        if not control_key:
            raise InstanceResolutionError(
                ResolutionFailure.NO_CONTROL_KEY,
                f"No control_key configured for '{client_slug}' in clients.json",
            )
        mcp_url = str(entry.get("mcp_url") or "").strip()
        if not mcp_url:
            raise InstanceResolutionError(
                ResolutionFailure.NO_PINNED_URL,
                f"No mcp_url for '{client_slug}': pin mcp_url for '{client_slug}' in "
                "clients.json (the heartbeat URL is not trusted for addressing)",
            )
        return InstanceTarget(_checked_url(client_slug, mcp_url), control_key)

    # -- transport --------------------------------------------------------------

    async def _post(self, target: InstanceTarget, path: str,
                    json_body: Any | None = None) -> httpx.Response:
        kwargs: dict[str, Any] = {"headers": {"Authorization": f"Bearer {target.control_key}"}}
        if json_body is not None:
            kwargs["json"] = json_body
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                return await client.post(f"{target.mcp_url}{path}", **kwargs)
            except httpx.RequestError as exc:
                raise InstanceUnreachable(str(exc)) from exc

    async def _post_expect_success(self, client_slug: str, path: str) -> Any:
        response = await self._post(self.resolve(client_slug), path)
        if not response.is_success:
            raise InstanceHTTPError(response.status_code, response.text)
        return response.json()

    # -- commands ---------------------------------------------------------------

    async def trigger_scan(self, client_slug: str, project_id: str) -> Any:
        return await self._post_expect_success(client_slug, f"/ops/control/scan/{project_id}")

    async def trigger_generate(self, client_slug: str, project_id: str) -> Any:
        return await self._post_expect_success(
            client_slug, f"/ops/control/generate/{project_id}"
        )

    async def install_licence(self, target: InstanceTarget, token: str) -> InstanceReply:
        """Push a signed licence. Any HTTP status is returned, not raised.

        Raises ``InstanceUnreachable`` on a transport failure. ``target`` is
        resolved by the caller up front, so an unaddressable client is refused
        before a licence is minted for it.
        """
        response = await self._post(target, "/ops/control/licence", {"token": token})
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text
        return InstanceReply(response.status_code, body)


def get_instance_client() -> InstanceControlClient:
    """FastAPI dependency; tests override it or mock the httpx transport."""
    return InstanceControlClient()
