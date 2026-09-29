from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import settings
from .licences_api import get_signer_provider
from .licences_api import router as licences_router
from .receiver import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


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
    yield


app = FastAPI(title="Mission Control", version="0.1.0", lifespan=lifespan)
app.include_router(router)
app.include_router(licences_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "mission-control"}
