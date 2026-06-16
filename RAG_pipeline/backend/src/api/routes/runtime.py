"""Runtime status — reports whether a live runtime provider (e.g. Talend TMC)
is configured and reachable. Available to every authenticated user; no
additional role/permission gate.
"""

from __future__ import annotations

from fastapi import APIRouter

from src.api.dependencies import CurrentUser
from src.runtime.models import RuntimeStatusResponse
from src.runtime.status import get_runtime_status

router = APIRouter(prefix="/runtime", tags=["runtime"])


@router.get("/status", response_model=RuntimeStatusResponse, summary="Get live runtime connection status")
async def get_status(_user: CurrentUser) -> RuntimeStatusResponse:
    return await get_runtime_status()
