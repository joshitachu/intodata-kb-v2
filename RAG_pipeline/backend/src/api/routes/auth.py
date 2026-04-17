"""Authentication endpoints: login, logout, refresh, me."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.api.dependencies import CurrentUser, DBSession
from src.auth.security import decode_token
from src.auth.service import AuthService
from src.config import settings
from src.rbac.permissions import get_user_permissions
from src.services.audit_service import AuditService

limiter = Limiter(key_func=get_remote_address)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

def _cookie_domain() -> str | None:
    return settings.cookie_domain or None


def _set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    response.set_cookie(
        key="access_token",
        value=access,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.access_token_expire_minutes * 60,
        path="/",
        domain=_cookie_domain(),
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.refresh_token_expire_days * 86400,
        path="/",
        domain=_cookie_domain(),
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie("access_token", path="/", domain=_cookie_domain())
    response.delete_cookie("refresh_token", path="/", domain=_cookie_domain())


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    is_active: bool
    is_superadmin: bool
    roles: list[str]
    permissions: list[str] = []


@router.post("/login")
@limiter.limit("5/minute")
async def login(request: Request, body: LoginRequest, db: DBSession, response: Response):
    svc = AuthService(db)
    user = await svc.authenticate_user(body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    access, refresh = await svc.create_tokens(user)
    _set_auth_cookies(response, access, refresh)

    audit = AuditService(db)
    await audit.log(
        user_id=user.id,
        username=user.username,
        action="login",
        resource_type="session",
        ip_address=request.client.host if request.client else None,
    )

    return {
        "message": "Login successful",
        "user": UserResponse(
            id=user.id,
            username=user.username,
            email=user.email,
            is_active=user.is_active,
            is_superadmin=user.is_superadmin,
            roles=[r.name for r in user.roles],
            permissions=sorted(get_user_permissions(user)),
        ),
    }


@router.post("/logout")
async def logout(request: Request, db: DBSession, response: Response):
    refresh = request.cookies.get("refresh_token")
    if refresh:
        svc = AuthService(db)
        await svc.revoke_refresh_token(refresh)
    _clear_auth_cookies(response)
    return {"message": "Logged out"}


@router.post("/refresh")
@limiter.limit("10/minute")
async def refresh(request: Request, db: DBSession, response: Response):
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=401, detail="No refresh token")

    svc = AuthService(db)
    result = await svc.rotate_refresh_token(refresh_token)
    if result is None:
        _clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    access, new_refresh, user = result
    _set_auth_cookies(response, access, new_refresh)
    return {"message": "Token refreshed"}


@router.get("/me")
async def me(user: CurrentUser):
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        is_superadmin=user.is_superadmin,
        roles=[r.name for r in user.roles],
        permissions=sorted(get_user_permissions(user)),
    )
