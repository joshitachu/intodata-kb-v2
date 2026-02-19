from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.session import get_db
from src.db.models import User


# --- Database session ---

DBSession = Annotated[AsyncSession, Depends(get_db)]


# --- Qdrant client ---
_qdrant_client: AsyncQdrantClient | None = None


async def get_qdrant() -> AsyncQdrantClient:
    """Return a singleton async Qdrant client."""
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    return _qdrant_client


QdrantClient = Annotated[AsyncQdrantClient, Depends(get_qdrant)]


# --- Current user (Phase 1: header-based) ---

async def get_current_user(
    db: DBSession,
    x_user_id: Annotated[str | None, Header()] = None,
) -> User:
    """Resolve current user from X-User-Id header. Phase 1: simple header auth."""
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing X-User-Id header")

    try:
        user_id = int(x_user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="X-User-Id must be an integer")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
