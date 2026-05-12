from __future__ import annotations

from fastapi import HTTPException


def api_error(status_code: int, code: str, message: str, **extra) -> HTTPException:
    """Return a machine-readable HTTPException with a consistent detail shape."""
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, **extra},
    )
