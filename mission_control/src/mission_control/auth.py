"""Bearer auth for the ``/ops`` API (``MC_API_KEY``) and for heartbeats (ADR-039)."""

from __future__ import annotations

import enum
import hmac
import logging
import threading
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings
from .fleet import FleetDirectory, HeartbeatLookup, get_fleet_directory, valid_slug

logger = logging.getLogger(__name__)

_bearer = HTTPBearer()


def keys_match(presented: str, expected: str) -> bool:
    """Constant-time comparison, so response timing leaks nothing about the key."""
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def _invalid_token() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


def require_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> None:
    if not keys_match(credentials.credentials, settings.api_key):
        raise _invalid_token()


class HeartbeatAuth(str, enum.Enum):
    #: The slug's own telemetry key (registered through ``/ops/fleet/clients``).
    CLIENT_KEY = "client_key"
    #: The global ``MC_API_KEY``: legacy only, for instances not yet moved to
    #: their own key. See ``shared_key_refusal`` for when it is refused.
    SHARED_KEY = "shared_key"


class _OncePerSlug:
    """Remembers which slugs were already logged, with a hard size cap.

    Only valid slugs reach it, and past ``cap`` entries nothing new is added,
    so an attacker cycling slugs cannot grow it without bound (they only lose
    the log line, which is the point of logging once).
    """

    def __init__(self, cap: int = 1024) -> None:
        self._cap = cap
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    def first(self, slug: str) -> bool:
        with self._lock:
            if slug in self._seen or len(self._seen) >= self._cap:
                return False
            self._seen.add(slug)
            return True

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._seen)


_shared_key_accepted = _OncePerSlug()
_shared_key_refused = _OncePerSlug()


def shared_key_refusal(lookup: HeartbeatLookup) -> str | None:
    """Why a shared-key heartbeat for this slug is refused, or ``None`` if allowed.

    Fail closed: when the registry cannot be read we cannot tell whether the
    slug has its own key, so the shared key is refused too.
    """
    if not settings.allow_shared_heartbeat:
        return "MC_ALLOW_SHARED_HEARTBEAT is false"
    if settings.uses_repo_default_api_key:
        return "MC_API_KEY is the public repo default"
    if not lookup.registry_available:
        return "the fleet registry is unreadable"
    if lookup.has_telemetry_key:
        return "the client has its own telemetry key"
    return None


def require_heartbeat_auth(
    client_slug: str,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
    directory: Annotated[FleetDirectory, Depends(get_fleet_directory)],
) -> HeartbeatAuth:
    """Accept THIS slug's telemetry key, or (legacy) the shared ``MC_API_KEY``.

    The slug is validated first, before any lookup or logging. A client key is
    looked up by the slug in the path and compared by hash in constant time,
    so it authorises only its own slug. The shared key is accepted only when
    ``shared_key_refusal`` allows it; a refused shared key gets the same 401
    as a wrong key.
    """
    if not valid_slug(client_slug):
        raise HTTPException(status_code=422,
                            detail={"code": "invalid_slug", "message": "invalid client slug"})
    presented = credentials.credentials
    lookup = directory.heartbeat_lookup(client_slug)
    if lookup.client is not None and lookup.client.telemetry_key_matches(presented):
        return HeartbeatAuth.CLIENT_KEY
    if keys_match(presented, settings.api_key):
        reason = shared_key_refusal(lookup)
        if reason is None:
            if _shared_key_accepted.first(client_slug):
                logger.info("shared-key heartbeat client=%s (register the client in the Fleet "
                            "screen and give it its own MCP_OPS_TELEMETRY_API_KEY)", client_slug)
            return HeartbeatAuth.SHARED_KEY
        if _shared_key_refused.first(client_slug):
            logger.warning("shared-key heartbeat refused client=%s: %s", client_slug, reason)
    raise _invalid_token()
