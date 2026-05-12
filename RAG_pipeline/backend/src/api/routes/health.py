import logging

import httpx
from fastapi import APIRouter
from sqlalchemy import text

from src.config import settings
from src.db.session import async_session_factory
from src.schemas import HealthResponse, ServiceStatus

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health", response_model=HealthResponse, summary="Service health check")
async def health_check() -> HealthResponse:
    """Check status of PostgreSQL, Qdrant, Ollama, and Hindsight."""
    db_status = await _check_database()
    qdrant_status = await _check_qdrant()
    ollama_status = await _check_ollama()
    hindsight_status = await _check_hindsight()

    # Core services determine healthy/unhealthy; Hindsight failure only degrades
    core_statuses = [db_status.status, qdrant_status.status, ollama_status.status]
    if all(s == "ok" for s in core_statuses):
        overall = "healthy" if hindsight_status.status == "ok" else "degraded"
    elif any(s == "ok" for s in core_statuses):
        overall = "degraded"
    else:
        overall = "unhealthy"

    return HealthResponse(
        status=overall,
        database=db_status,
        qdrant=qdrant_status,
        ollama=ollama_status,
        hindsight=hindsight_status,
    )


async def _check_database() -> ServiceStatus:
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        return ServiceStatus(status="ok")
    except Exception as e:
        logger.warning(f"Database health check failed: {e}")
        return ServiceStatus(status="error", message="Service unavailable")


async def _check_qdrant() -> ServiceStatus:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            headers = {"api-key": settings.qdrant_api_key}
            resp = await client.get(settings.qdrant_url, headers=headers)
            if resp.status_code == 200:
                return ServiceStatus(status="ok")
            return ServiceStatus(status="error", message="Service unavailable")
    except Exception as e:
        logger.warning(f"Qdrant health check failed: {e}")
        return ServiceStatus(status="error", message="Service unavailable")


async def _check_ollama() -> ServiceStatus:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            if resp.status_code == 200:
                return ServiceStatus(status="ok")
            return ServiceStatus(status="error", message="Service unavailable")
    except Exception as e:
        logger.warning(f"Ollama health check failed: {e}")
        return ServiceStatus(status="error", message="Service unavailable")


async def _check_hindsight() -> ServiceStatus:
    if not settings.hindsight_enabled:
        return ServiceStatus(status="ok", message="disabled")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.hindsight_url}/health")
            if resp.status_code == 200:
                return ServiceStatus(status="ok")
            return ServiceStatus(status="error", message="Service unavailable")
    except Exception as e:
        logger.warning(f"Hindsight health check failed: {e}")
        return ServiceStatus(status="error", message="Service unavailable")
