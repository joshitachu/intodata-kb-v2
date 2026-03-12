"""External usage recording endpoint.

Allows the KB MCP server to POST token usage records (doc generation,
deep dive) into the shared QueryUsage table.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from src.config import settings
from src.db.models import QueryUsage
from src.api.dependencies import DBSession

logger = logging.getLogger(__name__)

router = APIRouter(tags=["usage"])


async def verify_ingest_api_key(
    authorization: str | None = Header(default=None),
) -> None:
    """Verify Bearer token matches MCP_INGEST_API_KEY.

    If no key is configured, the endpoint is open (backwards-compatible).
    """
    if not settings.mcp_ingest_api_key:
        return
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token != settings.mcp_ingest_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


class UsageRecordRequest(BaseModel):
    interface: str = Field(..., max_length=50)
    model_name: str | None = None
    provider: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None
    project_id: str | None = None
    query_text: str | None = None


@router.post("/usage/record", dependencies=[Depends(verify_ingest_api_key)])
async def record_usage(body: UsageRecordRequest, db: DBSession):
    """Record a token usage entry from an external service (e.g. KB MCP server)."""
    entry = QueryUsage(
        interface=body.interface,
        model_name=body.model_name,
        provider=body.provider,
        input_tokens=body.input_tokens,
        output_tokens=body.output_tokens,
        cost_usd=body.cost_usd,
        latency_ms=body.latency_ms,
        project_id=body.project_id,
        query_text=body.query_text,
    )
    db.add(entry)
    await db.commit()
    logger.info("Recorded usage: interface=%s project=%s", body.interface, body.project_id)
    return {"status": "ok", "id": entry.id}
