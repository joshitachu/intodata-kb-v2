from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from .config import settings
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
    yield


app = FastAPI(title="Mission Control", version="0.1.0", lifespan=lifespan)
app.include_router(router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "mission-control"}
