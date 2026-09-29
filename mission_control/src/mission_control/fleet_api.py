"""Fleet client management API (ADR-039): register, edit, re-key and remove clients.

Thin routes over ``fleet.FleetDirectory`` / ``FleetRegistry``. All behind
``MC_API_KEY``. Responses never contain key material, with one exception: the
telemetry key is returned exactly once, by the call that creates it (register
or rotate); only its hash is stored, so it cannot be shown again.

Routes are plain ``def`` on purpose: registry writes take a file lock and
fsync, so FastAPI runs them in its threadpool instead of on the event loop.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from .auth import require_api_key
from .config import settings
from .fleet import (
    ClientExists,
    ClientNotFound,
    FleetClient,
    FleetDirectory,
    RegistryUnavailable,
    get_fleet_directory,
    hash_telemetry_key,
    new_telemetry_key,
    valid_slug,
)
from .instance_client import UrlRejected, validate_mcp_url
from .snapshots import load_snapshot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ops/fleet", dependencies=[Depends(require_api_key)])

_MAX_URL_CHARS = 2048
#: A control key is sent verbatim as ``Authorization: Bearer <key>``: printable
#: ASCII without spaces, so it can never split or inject a header.
_CONTROL_KEY = re.compile(r"^[\x21-\x7e]{1,512}$")

Directory = Annotated[FleetDirectory, Depends(get_fleet_directory)]


# -- schemas --------------------------------------------------------------------

class RegisterClientRequest(BaseModel):
    slug: str
    mcp_url: str
    control_key: str | None = None


class UpdateClientRequest(BaseModel):
    mcp_url: str | None = None
    control_key: str | None = None


# -- helpers --------------------------------------------------------------------

def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _registry_down(exc: RegistryUnavailable) -> HTTPException:
    return _error(503, exc.code, "The fleet registry is not readable; nothing was changed.")


def _check_slug(slug: str) -> None:
    if not valid_slug(slug):
        raise _error(422, "invalid_slug",
                     "slug must match ^[a-z0-9][a-z0-9-]{0,62}$ (lowercase, digits, hyphens)")


def _checked_url(mcp_url: str) -> str:
    candidate = mcp_url.strip()
    if not candidate or len(candidate) > _MAX_URL_CHARS:
        raise _error(422, "invalid_url", "mcp_url must be an absolute https:// URL with a host")
    try:
        return validate_mcp_url(candidate)
    except UrlRejected as exc:
        raise _error(422, exc.failure.value, f"mcp_url {exc.reason}") from None


def _checked_control_key(control_key: str | None) -> str | None:
    """``None`` / blank -> ``None`` (not configured yet); else a header-safe token."""
    candidate = (control_key or "").strip()
    if not candidate:
        return None
    if not _CONTROL_KEY.match(candidate):
        raise _error(422, "invalid_control_key",
                     "control_key must be 1-512 printable ASCII characters without spaces")
    return candidate


def _last_heartbeat_at(slug: str) -> str | None:
    try:
        snapshot = load_snapshot(slug)
    except (OSError, ValueError):
        return None
    received = snapshot.get("_mc_received_at") if isinstance(snapshot, dict) else None
    return received if isinstance(received, str) else None


def _public(client: FleetClient) -> dict[str, Any]:
    """The API view of a client. Never any key material."""
    return {
        "slug": client.slug,
        "mcp_url": client.mcp_url,
        "has_control_key": client.has_control_key,
        "heartbeat_auth": client.heartbeat_auth,
        "source": client.source,
        "created_at": client.created_at,
        "updated_at": client.updated_at,
        "last_heartbeat_at": _last_heartbeat_at(client.slug),
    }


def _client_env(slug: str, telemetry_key: str) -> dict[str, str | None]:
    """The three lines the client server's env file needs, ready to paste."""
    return {
        "MCP_OPS_TELEMETRY_URL": settings.telemetry_base_url,
        "MCP_OPS_TELEMETRY_API_KEY": telemetry_key,
        "MCP_OPS_CLIENT_SLUG": slug,
    }


def _registered(directory: FleetDirectory, slug: str) -> FleetClient:
    """The slug's REGISTRY entry, or 404 / 409 ``managed_by_file`` / 503."""
    _check_slug(slug)
    try:
        client = directory.registry.get(slug)
    except RegistryUnavailable as exc:
        raise _registry_down(exc) from exc
    if client is not None:
        return client
    if directory.entry(slug) is not None:
        raise _error(409, "managed_by_file",
                     f"'{slug}' is defined in the read-only clients.json; edit that file "
                     "or remove it there and register the client here")
    raise _error(404, "client_not_found", f"no fleet client '{slug}'")


# -- routes ---------------------------------------------------------------------

@router.get("/clients")
def list_fleet_clients(directory: Directory) -> dict[str, Any]:
    """``registry_unavailable`` is true when the registry file is unreadable or
    corrupt; ``clients`` then holds only the ``clients.json`` entries."""
    listing = directory.listing()
    return {
        "clients": [_public(c) for c in listing.clients],
        "telemetry_base_url": settings.telemetry_base_url,
        "registry_unavailable": listing.registry_unavailable,
    }


@router.post("/clients", status_code=status.HTTP_201_CREATED)
def register_fleet_client(body: RegisterClientRequest, directory: Directory) -> dict[str, Any]:
    slug = body.slug.strip()
    _check_slug(slug)
    mcp_url = _checked_url(body.mcp_url)
    control_key = _checked_control_key(body.control_key)
    if directory.entry(slug) is not None:  # either source: a file entry is a client too
        raise _error(409, "client_exists", f"'{slug}' is already a fleet client")
    telemetry_key = new_telemetry_key()
    try:
        client = directory.registry.create(slug, mcp_url=mcp_url, control_key=control_key,
                                           telemetry_key_sha256=hash_telemetry_key(telemetry_key))
    except ClientExists as exc:  # lost a race with a concurrent register
        raise _error(409, exc.code, f"'{slug}' is already a fleet client") from exc
    except RegistryUnavailable as exc:
        raise _registry_down(exc) from exc
    logger.info("fleet client registered slug=%s has_control_key=%s",
                slug, client.has_control_key)
    return {"client": _public(client), "telemetry_key": telemetry_key,
            "client_env": _client_env(slug, telemetry_key)}


@router.patch("/clients/{client_slug}")
def update_fleet_client(client_slug: str, body: UpdateClientRequest,
                        directory: Directory) -> dict[str, Any]:
    _registered(directory, client_slug)
    changes: dict[str, Any] = {}
    if "mcp_url" in body.model_fields_set:
        if body.mcp_url is None:
            raise _error(422, "invalid_url", "mcp_url cannot be cleared; give an https:// URL")
        changes["mcp_url"] = _checked_url(body.mcp_url)
    if "control_key" in body.model_fields_set:
        changes["control_key"] = _checked_control_key(body.control_key)
    try:
        client = (directory.registry.update(client_slug, **changes) if changes
                  else directory.registry.get(client_slug))
    except ClientNotFound as exc:  # removed concurrently
        raise _error(404, "client_not_found", f"no fleet client '{client_slug}'") from exc
    except RegistryUnavailable as exc:
        raise _registry_down(exc) from exc
    if client is None:
        raise _error(404, "client_not_found", f"no fleet client '{client_slug}'")
    logger.info("fleet client updated slug=%s fields=%s", client_slug, sorted(changes) or "-")
    return {"client": _public(client)}


@router.post("/clients/{client_slug}/telemetry-key")
def rotate_telemetry_key(client_slug: str, directory: Directory) -> dict[str, Any]:
    _registered(directory, client_slug)
    telemetry_key = new_telemetry_key()
    try:
        directory.registry.update(client_slug,
                                  telemetry_key_sha256=hash_telemetry_key(telemetry_key))
    except ClientNotFound as exc:
        raise _error(404, "client_not_found", f"no fleet client '{client_slug}'") from exc
    except RegistryUnavailable as exc:
        raise _registry_down(exc) from exc
    logger.info("fleet telemetry key rotated slug=%s (the previous key no longer works)",
                client_slug)
    return {"telemetry_key": telemetry_key, "client_env": _client_env(client_slug, telemetry_key)}


@router.delete("/clients/{client_slug}", status_code=status.HTTP_204_NO_CONTENT)
def remove_fleet_client(client_slug: str, directory: Directory) -> Response:
    """Remove the registry entry and its keys. Snapshot and licence ledger stay."""
    _registered(directory, client_slug)
    try:
        directory.registry.delete(client_slug)
    except ClientNotFound as exc:
        raise _error(404, "client_not_found", f"no fleet client '{client_slug}'") from exc
    except RegistryUnavailable as exc:
        raise _registry_down(exc) from exc
    logger.info("fleet client removed slug=%s (snapshot and licence history kept)", client_slug)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
