"""Bearer auth for the ``/ops`` API (``MC_API_KEY``)."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings

_bearer = HTTPBearer()


def keys_match(presented: str, expected: str) -> bool:
    """Constant-time comparison, so response timing leaks nothing about the key."""
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def require_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> None:
    if not keys_match(credentials.credentials, settings.api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
