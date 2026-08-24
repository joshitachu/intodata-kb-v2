from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request
from jwt import InvalidTokenError as JWTError
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.security import decode_token
from src.config import settings
from src.db.session import get_db
from src.db.models import User
from src.rbac.permissions import (
    RUNTIME_SOURCES_READ,
    RUNTIME_SOURCES_WRITE,
    has_permission,
)

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

MFA_ENROLLMENT_EXEMPT_PATHS = {
    "/api/v1/auth/me",
    "/api/v1/auth/logout",
    "/api/v1/auth/refresh",
    "/api/v1/auth/totp/setup",
    "/api/v1/auth/totp/verify",
    "/api/v1/auth/change-password",
}

PASSWORD_SETUP_EXEMPT_PATHS = {
    "/api/v1/auth/me",
    "/api/v1/auth/logout",
    "/api/v1/auth/refresh",
    "/api/v1/auth/set-password",
}


def _is_mfa_enrollment_exempt(path: str) -> bool:
    return path in MFA_ENROLLMENT_EXEMPT_PATHS


def _is_password_setup_exempt(path: str) -> bool:
    return path in PASSWORD_SETUP_EXEMPT_PATHS

async def get_current_user(
    request: Request,
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

    if not user.mfa_enabled and not _is_mfa_enrollment_exempt(request.url.path):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "mfa_required",
                "message": "MFA enrollment is required before using this endpoint.",
            },
        )

    # Email-code users must first complete the mandatory MFA step, then create
    # a password before the rest of the API becomes available. Keep only the
    # endpoints needed to inspect/end/refresh the session and finish setup.
    if (
        user.mfa_enabled
        and user.password_hash is None
        and user.sso_provider is None
        and not _is_password_setup_exempt(request.url.path)
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "password_setup_required",
                "message": "Create a password before using this endpoint.",
            },
        )

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


# --- Deployment owner ---

async def require_owner(user: CurrentUser) -> User:
    """Require the deployment Owner (stored as ``is_superadmin``).

    ``is_superadmin`` remains the persistence/API field for backwards
    compatibility.  In the product hierarchy it represents the Owner, not the
    day-to-day Admin role.
    """
    if not user.is_superadmin:
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


OwnerUser = Annotated[User, Depends(require_owner)]


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
# Admins + the deployment Owner can create projects (they become the project owner). Ownership of
# an *existing* project is enforced separately via rbac.permissions.can_manage_project.
ProjectCreator = Annotated[User, Depends(require_permission("create_projects"))]
RuntimeSourceReader = Annotated[User, Depends(require_permission(RUNTIME_SOURCES_READ))]
RuntimeSourceWriter = Annotated[User, Depends(require_permission(RUNTIME_SOURCES_WRITE))]
