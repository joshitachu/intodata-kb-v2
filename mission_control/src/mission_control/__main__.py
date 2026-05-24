from __future__ import annotations

import uvicorn

from .config import settings

uvicorn.run(
    "mission_control.main:app",
    host=settings.host,
    port=settings.port,
    reload=True,
)
