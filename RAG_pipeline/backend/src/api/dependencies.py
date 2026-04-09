from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException
from jose import JWTError
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.security import decode_token
from src.config import settings
from src.db.session import get_db
from src.db.models import User
from src.rbac.permissions import has_permission

logger = logging.getLogger(__name__)


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


# --- Current user (JWT cookie auth) ---

async def get_current_user(
    db: DBSession,
    access_token: Annotated[str | None, Cookie()] = None,
) -> User:
    """Resolve current user from access_token httpOnly cookie."""
    if not access_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = decode_token(access_token)
        user_id = int(payload["sub"])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=401, detail="User account is deactivated")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


# --- Admin user ---

async def require_admin(user: CurrentUser) -> User:
    """Require the current user to be a superadmin."""
    if not user.is_superadmin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


# --- Permission-based access control ---

def require_permission(codename: str):
    """Dependency factory: require a specific permission codename."""

    async def _check(user: CurrentUser) -> User:
        if not has_permission(user, codename):
            raise HTTPException(
                status_code=403,
                detail=f"Permission required: {codename}",
            )
        return user

    return _check


ManagerUser = Annotated[User, Depends(require_permission("manage_users"))]
ProjectManager = Annotated[User, Depends(require_permission("assign_projects"))]
AuditViewer = Annotated[User, Depends(require_permission("view_audit_logs"))]
