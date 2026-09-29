from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import settings
from .fleet_api import router as fleet_router
from .instance_client import UrlRejected, validate_mcp_url
from .licences_api import get_signer_provider
from .licences_api import router as licences_router
from .receiver import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


def log_heartbeat_auth_posture() -> None:
    """Say at startup how heartbeats authenticate; loudly when the key is public."""
    if settings.uses_repo_default_api_key:
        logger.warning(
            "MC_API_KEY is the public repo default: every /ops route is open to anyone who "
            "reads the repo, and shared-key heartbeats are refused. Set a random MC_API_KEY.")
    if not settings.allow_shared_heartbeat:
        logger.info("Heartbeats: per-client telemetry keys only (MC_ALLOW_SHARED_HEARTBEAT=false)")
    else:
        logger.info("Heartbeats: per-client keys, plus the shared MC_API_KEY for slugs without "
                    "their own key (legacy; set MC_ALLOW_SHARED_HEARTBEAT=false to refuse it)")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings.snapshots_dir.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Mission Control ready  host=%s port=%s snapshots=%s",
        settings.host,
        settings.port,
        settings.snapshots_dir.resolve(),
    )
    # Reports only whether issuing is possible; the key itself is never logged.
    logger.info("Licence issuing %s", "enabled" if get_signer_provider().available()
                else "disabled (no signing key)")
    logger.info("Fleet registry %s", settings.fleet_registry_path)
    log_heartbeat_auth_posture()
    public_url = settings.telemetry_base_url
    if public_url is None:
        logger.info("MC_PUBLIC_URL unset: new clients get no MCP_OPS_TELEMETRY_URL to paste")
    else:
        try:
            validate_mcp_url(public_url)
        except UrlRejected as exc:
            # Clients would send their telemetry key to it: say so loudly.
            logger.warning("MC_PUBLIC_URL %s", exc.reason)
    yield


app = FastAPI(title="Mission Control", version="0.1.0", lifespan=lifespan)
app.include_router(router)
app.include_router(licences_router)
app.include_router(fleet_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "mission-control"}
