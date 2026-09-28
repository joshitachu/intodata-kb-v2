from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .clients import load_client_config
from .config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ops")
_bearer = HTTPBearer()


def _require_auth(credentials: HTTPAuthorizationCredentials = Depends(_bearer)) -> None:
    if credentials.credentials != settings.api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


def _snapshot_path(client_slug: str) -> Path:
    return settings.snapshots_dir / f"{client_slug}.json"


# ── Heartbeat receiver ────────────────────────────────────────────────────────

@router.post(
    "/clients/{client_slug}/telemetry",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_require_auth)],
)
async def receive_telemetry(client_slug: str, request: Request) -> None:
    payload: dict[str, Any] = await request.json()
    payload["_mc_source_ip"] = request.client.host if request.client else None
    payload["_mc_received_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    settings.snapshots_dir.mkdir(parents=True, exist_ok=True)
    _snapshot_path(client_slug).write_text(json.dumps(payload, indent=2))
    logger.info(
        "snapshot stored client=%s schema=%s projects=%s cost_usd=%s",
        client_slug,
        payload.get("schema_version"),
        payload.get("totals", {}).get("project_count"),
        payload.get("totals", {}).get("cost_usd"),
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

def _get_vps_url_and_key(client_slug: str) -> tuple[str, str]:
    """Resolve the remote VPS MCP URL and control key for a given client."""
    snapshot_path = _snapshot_path(client_slug)
    if not snapshot_path.exists():
        raise HTTPException(status_code=404, detail=f"No snapshot for '{client_slug}'")

    snapshot = json.loads(snapshot_path.read_text())
    mcp_url = snapshot.get("mcp_url")
    if not mcp_url:
        raise HTTPException(
            status_code=422,
            detail=f"No mcp_url in snapshot for '{client_slug}' — ensure MCP_PUBLIC_URL is set on the VPS",
        )

    client_cfg = load_client_config(client_slug)
    control_key = client_cfg.get("control_key", "")
    if not control_key:
        raise HTTPException(
            status_code=422,
            detail=f"No control_key configured for '{client_slug}' in clients.json",
        )

    return mcp_url.rstrip("/"), control_key


@router.post(
    "/clients/{client_slug}/trigger/scan/{project_id}",
    dependencies=[Depends(_require_auth)],
)
async def trigger_scan(client_slug: str, project_id: str) -> dict[str, Any]:
    """Proxy a scan trigger to the remote VPS MCP server."""
    mcp_url, control_key = _get_vps_url_and_key(client_slug)
    url = f"{mcp_url}/ops/control/scan/{project_id}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            resp = await client.post(url, headers={"Authorization": f"Bearer {control_key}"})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Could not reach VPS: {exc}") from exc


@router.post(
    "/clients/{client_slug}/trigger/generate/{project_id}",
    dependencies=[Depends(_require_auth)],
)
async def trigger_generate(client_slug: str, project_id: str) -> dict[str, Any]:
    """Proxy a doc generation trigger to the remote VPS MCP server."""
    mcp_url, control_key = _get_vps_url_and_key(client_slug)
    url = f"{mcp_url}/ops/control/generate/{project_id}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            resp = await client.post(url, headers={"Authorization": f"Bearer {control_key}"})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Could not reach VPS: {exc}") from exc
