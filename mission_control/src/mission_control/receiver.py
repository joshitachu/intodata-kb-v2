from __future__ import annotations

import json
import logging
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from .auth import HeartbeatAuth, require_heartbeat_auth
from .auth import require_api_key as _require_auth
from .config import settings
from .fleet import valid_slug
from .instance_client import (
    InstanceControlClient,
    InstanceHTTPError,
    InstanceResolutionError,
    InstanceUnreachable,
    ResolutionFailure,
    get_instance_client,
)
from .snapshots import snapshot_path as _snapshot_path
from .storage import atomic_write_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ops")


# ── Heartbeat receiver ────────────────────────────────────────────────────────

#: Largest heartbeat body accepted. nginx caps the public ingress at the same
#: 512k, but loopback callers bypass nginx, so the app enforces it too.
MAX_HEARTBEAT_BYTES = 512 * 1024


def _payload_too_large() -> HTTPException:
    return HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail={
        "code": "payload_too_large",
        "message": f"the heartbeat body must be at most {MAX_HEARTBEAT_BYTES} bytes"})


def _invalid_payload() -> HTTPException:
    return HTTPException(status_code=422, detail={
        "code": "invalid_payload", "message": "the heartbeat body must be a JSON object"})


def _render_snapshot(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2)


async def _read_capped_body(request: Request, limit: int) -> bytes:
    """The request body, refused with 413 as soon as it exceeds ``limit``.

    A declared ``Content-Length`` over the limit is refused before reading;
    otherwise the stream is read chunk by chunk, so a chunked or lying client
    never makes us buffer more than ``limit`` plus one chunk.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                raise _payload_too_large()
        except ValueError:
            raise HTTPException(status_code=400, detail={
                "code": "invalid_content_length", "message": "bad Content-Length"}) from None
    received = bytearray()
    async for chunk in request.stream():
        received.extend(chunk)
        if len(received) > limit:
            raise _payload_too_large()
    return bytes(received)


@router.post(
    "/clients/{client_slug}/telemetry",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def receive_telemetry(
    client_slug: str,
    request: Request,
    auth: Annotated[HeartbeatAuth, Depends(require_heartbeat_auth)],
) -> None:
    """Store a heartbeat. Auth: the slug's own telemetry key, or (legacy) the
    shared ``MC_API_KEY`` (see ``require_heartbeat_auth``). The body is read
    only after auth, capped at ``MAX_HEARTBEAT_BYTES``."""
    if not valid_slug(client_slug):  # auth already refused it; kept for the path write
        raise HTTPException(status_code=422,
                            detail={"code": "invalid_slug", "message": "invalid client slug"})
    body = await _read_capped_body(request, MAX_HEARTBEAT_BYTES)
    try:
        payload: Any = json.loads(body)
    except (ValueError, RecursionError):
        # ValueError covers JSONDecodeError and UnicodeDecodeError; RecursionError
        # is a deeply nested body ("[[[[...") that overflows the parser.
        raise _invalid_payload() from None
    if not isinstance(payload, dict):
        raise _invalid_payload()
    payload["_mc_source_ip"] = request.client.host if request.client else None
    payload["_mc_received_at"] = datetime.now(UTC).isoformat()
    payload["_mc_auth"] = auth.value
    try:
        # The pretty-printer recurses in Python, deeper than the C parser: a
        # body nested just under the parse limit can still overflow here.
        text = _render_snapshot(payload)
    except RecursionError:
        raise _invalid_payload() from None
    atomic_write_text(_snapshot_path(client_slug), text, mode=0o640)
    totals = payload.get("totals")
    totals = totals if isinstance(totals, dict) else {}
    logger.info(
        "snapshot stored client=%s auth=%s schema=%s projects=%s cost_usd=%s",
        client_slug,
        auth.value,
        payload.get("schema_version"),
        totals.get("project_count"),
        totals.get("cost_usd"),
    )


# ── Read endpoints ────────────────────────────────────────────────────────────

@router.get("/clients/{client_slug}/snapshot", dependencies=[Depends(_require_auth)])
async def get_snapshot(client_slug: str) -> dict[str, Any]:
    path = _snapshot_path(client_slug)
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No snapshot on file for '{client_slug}'",
        )
    return json.loads(path.read_text())


@router.get("/clients", dependencies=[Depends(_require_auth)])
async def list_clients() -> list[str]:
    if not settings.snapshots_dir.exists():
        return []
    return sorted(p.stem for p in settings.snapshots_dir.glob("*.json"))


@router.get("/snapshots", dependencies=[Depends(_require_auth)])
async def all_snapshots() -> dict[str, Any]:
    """Return all client snapshots in one call — used by the Fleet dashboard."""
    if not settings.snapshots_dir.exists():
        return {}
    result: dict[str, Any] = {}
    for path in sorted(settings.snapshots_dir.glob("*.json")):
        try:
            result[path.stem] = json.loads(path.read_text())
        except Exception as exc:
            logger.warning("Failed to read snapshot %s: %s", path, exc)
    return result


# -- Billing webhook (scaffold, not live) --------------------------------------

@router.post("/billing/webhook", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def billing_webhook() -> JSONResponse:
    """Reserved for the hosted payment provider's webhook. Deliberately inert.

    It reads no body, calls no provider and issues nothing, so leaving it
    unauthenticated grants nothing. When a provider is integrated, this must
    verify the provider's signature (``PaymentProvider.parse_webhook``) before
    acting; an unverified event would mint a licence.
    """
    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        content={
            "detail": {
                "code": "billing_not_live",
                "message": (
                    "No payment provider is integrated. Subscriptions are billed "
                    "manually and licences are issued by the operator."
                ),
            }
        },
    )


# ── Remote trigger proxy ──────────────────────────────────────────────────────

async def _proxy_trigger(call: Awaitable[Any]) -> Any:
    """Map the adapter's typed failures to the trigger endpoints' HTTP contract."""
    try:
        return await call
    except InstanceResolutionError as exc:
        code = 404 if exc.failure is ResolutionFailure.NO_CLIENT_ENTRY else 422
        raise HTTPException(status_code=code, detail=exc.message) from exc
    except InstanceHTTPError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.text) from exc
    except InstanceUnreachable as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach VPS: {exc}") from exc


@router.post(
    "/clients/{client_slug}/trigger/scan/{project_id}",
    dependencies=[Depends(_require_auth)],
)
async def trigger_scan(
    client_slug: str,
    project_id: str,
    instances: Annotated[InstanceControlClient, Depends(get_instance_client)],
) -> dict[str, Any]:
    """Proxy a scan trigger to the remote VPS MCP server."""
    return await _proxy_trigger(instances.trigger_scan(client_slug, project_id))


@router.post(
    "/clients/{client_slug}/trigger/generate/{project_id}",
    dependencies=[Depends(_require_auth)],
)
async def trigger_generate(
    client_slug: str,
    project_id: str,
    instances: Annotated[InstanceControlClient, Depends(get_instance_client)],
) -> dict[str, Any]:
    """Proxy a doc generation trigger to the remote VPS MCP server."""
    return await _proxy_trigger(instances.trigger_generate(client_slug, project_id))
