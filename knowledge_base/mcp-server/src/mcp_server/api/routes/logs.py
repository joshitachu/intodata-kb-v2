"""SSE log streaming endpoint."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse

from mcp_server.api.permissions import require_project_access

router = APIRouter(tags=["logs"])


@router.get("/logs/stream")
async def stream_logs(project_id: str, request: Request):
    """SSE endpoint for live log streaming.

    Usage: GET /logs/stream?project_id=vaxxinova
    """
    await require_project_access(request, project_id)
    log_mgr = request.app.state.log_manager
    queue = log_mgr.subscribe(project_id)

    async def event_generator():
        try:
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    if event.get("project_id") != project_id:
                        continue
                    data = json.dumps(event)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield f": keepalive\n\n"
        finally:
            log_mgr.unsubscribe(project_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
