"""Admin endpoints: user and role management."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import AdminUser, DBSession
from src.auth.security import hash_password
from src.auth.service import AuthService
from src.db.models import Role, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# --- Schemas ---

class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=4)
    role_ids: list[int] = Field(default_factory=list)


class UpdateUserRequest(BaseModel):
    username: str | None = None
    email: str | None = None
    is_active: bool | None = None
    role_ids: list[int] | None = None


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=4)


class CreateRoleRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    is_active: bool
    is_superadmin: bool
    roles: list[dict]
    last_login_at: str | None


class RoleResponse(BaseModel):
    id: int
    name: str
    description: str | None


# --- User management ---

@router.get("/users")
async def list_users(admin: AdminUser, db: DBSession, skip: int = 0, limit: int = 50):
    result = await db.execute(select(User).offset(skip).limit(limit))
    users = result.scalars().all()
    total_result = await db.execute(select(User))
    total = len(total_result.scalars().all())
    return {
        "users": [
            UserResponse(
                id=u.id,
                username=u.username,
                email=u.email,
                is_active=u.is_active,
                is_superadmin=u.is_superadmin,
                roles=[{"id": r.id, "name": r.name} for r in u.roles],
                last_login_at=u.last_login_at.isoformat() if u.last_login_at else None,
            )
            for u in users
        ],
        "total": total,
    }


@router.post("/users", status_code=201)
async def create_user(body: CreateUserRequest, admin: AdminUser, db: DBSession):
    # Check uniqueness
    existing = await db.execute(
        select(User).where((User.username == body.username) | (User.email == body.email))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username or email already exists")

    user = User(
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
    )

    # Attach roles (always set explicitly to avoid lazy-load in async context)
    if body.role_ids:
        roles_result = await db.execute(select(Role).where(Role.id.in_(body.role_ids)))
        user.roles = list(roles_result.scalars().all())
    else:
        user.roles = []

    db.add(user)
    await db.flush()

    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        is_superadmin=user.is_superadmin,
        roles=[{"id": r.id, "name": r.name} for r in user.roles],
        last_login_at=None,
    )


@router.put("/users/{user_id}")
async def update_user(user_id: int, body: UpdateUserRequest, admin: AdminUser, db: DBSession):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if body.username is not None:
        user.username = body.username
    if body.email is not None:
        user.email = body.email
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.role_ids is not None:
        roles_result = await db.execute(select(Role).where(Role.id.in_(body.role_ids)))
        user.roles = list(roles_result.scalars().all())

    await db.flush()

    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        is_superadmin=user.is_superadmin,
        roles=[{"id": r.id, "name": r.name} for r in user.roles],
        last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
    )


@router.delete("/users/{user_id}")
async def deactivate_user(user_id: int, admin: AdminUser, db: DBSession):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")

    user.is_active = False
    # Revoke all refresh tokens
    svc = AuthService(db)
    await svc.revoke_all_user_tokens(user_id)

    return {"message": f"User {user.username} deactivated"}


@router.post("/users/{user_id}/reset-password")
async def reset_password(
    user_id: int, body: ResetPasswordRequest, admin: AdminUser, db: DBSession
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    user.password_hash = hash_password(body.new_password)
    # Revoke all existing refresh tokens
    svc = AuthService(db)
    await svc.revoke_all_user_tokens(user_id)

    return {"message": f"Password reset for {user.username}"}


# --- Role management ---

@router.get("/roles")
async def list_roles(admin: AdminUser, db: DBSession):
    result = await db.execute(select(Role))
    roles = result.scalars().all()
    return {
        "roles": [
            RoleResponse(id=r.id, name=r.name, description=r.description) for r in roles
        ]
    }


@router.post("/roles", status_code=201)
async def create_role(body: CreateRoleRequest, admin: AdminUser, db: DBSession):
    existing = await db.execute(select(Role).where(Role.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Role already exists")

    role = Role(name=body.name, description=body.description)
    db.add(role)
    await db.flush()

    return RoleResponse(id=role.id, name=role.name, description=role.description)
