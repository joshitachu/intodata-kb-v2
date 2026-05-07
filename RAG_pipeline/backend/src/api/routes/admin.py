"""Admin endpoints: user and role management."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sqlalchemy import func as sa_func

from src.api.dependencies import AdminUser, AuditViewer, DBSession, ManagerUser, ProjectManager
from src.api.routes.auth import limiter
from src.auth.security import hash_password, hash_token
from src.auth.service import AuthService
from src.config import settings
from src.db.models import (
    AccessRequest,
    AuditLog,
    Permission,
    ProjectAssignment,
    QueryUsage,
    Role,
    User,
    UserInvite,
    UserOnboarding,
)
from src.services.audit_service import AuditService
from src.services.email_service import (
    EmailSendError,
    send_invite_email,
    send_rejection_email,
    send_test_email,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# --- Schemas ---

class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=8)
    role_ids: list[int] = Field(default_factory=list)


class UpdateUserRequest(BaseModel):
    username: str | None = None
    email: str | None = None
    is_active: bool | None = None
    role_ids: list[int] | None = None


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=8)


class CreateRoleRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    permission_ids: list[int] = Field(default_factory=list)


class UpdateRoleRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    permission_ids: list[int] | None = None


class RejectAccessRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class TestEmailRequest(BaseModel):
    to: str = Field(..., min_length=3, max_length=255)


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    is_active: bool
    is_superadmin: bool
    roles: list[dict]
    last_login_at: str | None


class PermissionResponse(BaseModel):
    id: int
    codename: str
    description: str | None


class RoleResponse(BaseModel):
    id: int
    name: str
    description: str | None
    permissions: list[PermissionResponse] = Field(default_factory=list)


def _invite_payload(invite: UserInvite | None) -> dict | None:
    if invite is None:
        return None
    return {
        "id": invite.id,
        "email": invite.email,
        "status": invite.status,
        "expires_at": invite.expires_at.isoformat(),
        "accepted_user_id": invite.accepted_user_id,
        "accepted_at": invite.accepted_at.isoformat() if invite.accepted_at else None,
        "created_at": invite.created_at.isoformat(),
    }


def _access_request_payload(row: AccessRequest) -> dict:
    return {
        "id": row.id,
        "email": row.email,
        "full_name": row.full_name,
        "status": row.status,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "rejection_reason": row.rejection_reason,
        "created_at": row.created_at.isoformat(),
        "invite": _invite_payload(row.invite),
    }


def _new_invite_token() -> str:
    return secrets.token_urlsafe(32)



# --- Access requests / invites ---

@router.get("/access-requests")
async def list_access_requests(
    admin: AdminUser,
    db: DBSession,
    status: str | None = Query(default="pending"),
):
    stmt = (
        select(AccessRequest)
        .options(selectinload(AccessRequest.invite))
        .order_by(AccessRequest.created_at.desc())
    )
    if status:
        stmt = stmt.where(AccessRequest.status == status)
    rows = (await db.execute(stmt)).scalars().all()

    invites = (
        await db.execute(
            select(UserInvite)
            .where(UserInvite.status.in_(["pending", "sent"]))
            .order_by(UserInvite.created_at.desc())
        )
    ).scalars().all()
    request_invite_ids = {r.invite_id for r in rows if r.invite_id}
    standalone_invites = [i for i in invites if i.id not in request_invite_ids]

    return {
        "requests": [_access_request_payload(row) for row in rows],
        "invites": [_invite_payload(invite) for invite in standalone_invites],
    }


@router.post("/access-requests/{request_id}/approve")
async def approve_access_request(
    request: Request, request_id: int, admin: AdminUser, db: DBSession
):
    row = (
        await db.execute(select(AccessRequest).where(AccessRequest.id == request_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Access request not found")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="Access request is already reviewed")

    existing_user = (
        await db.execute(select(User).where(User.email == row.email))
    ).scalar_one_or_none()
    if existing_user is not None:
        raise HTTPException(status_code=409, detail="A user with this email already exists")

    token = _new_invite_token()
    invite = UserInvite(
        email=row.email,
        token_hash=hash_token(token),
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=48),
        created_by=admin.id,
        full_name=row.full_name,
    )
    db.add(invite)
    await db.flush()

    try:
        setup_url = send_invite_email(row.email, token)
    except EmailSendError as exc:
        await db.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    row.status = "approved"
    row.reviewed_by = admin.id
    row.reviewed_at = datetime.now(timezone.utc)
    row.invite_id = invite.id
    row.invite = invite
    await db.commit()

    return {
        "message": "Invite sent",
        "request": _access_request_payload(row),
        "setup_url": setup_url if settings.app_env != "production" else None,
    }


@router.post("/access-requests/{request_id}/reject")
async def reject_access_request(
    request_id: int,
    body: RejectAccessRequest,
    admin: AdminUser,
    db: DBSession,
):
    row = (
        await db.execute(select(AccessRequest).where(AccessRequest.id == request_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Access request not found")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="Access request is already reviewed")

    row.status = "rejected"
    row.reviewed_by = admin.id
    row.reviewed_at = datetime.now(timezone.utc)
    row.rejection_reason = body.reason
    await db.commit()

    try:
        send_rejection_email(row.email, body.reason)
    except EmailSendError:
        logger.warning("Failed to send rejection email to %s", row.email)

    return {"message": "Access request rejected", "request": _access_request_payload(row)}


@router.post("/invites/{invite_id}/resend")
async def resend_invite(request: Request, invite_id: int, admin: AdminUser, db: DBSession):
    invite = (
        await db.execute(select(UserInvite).where(UserInvite.id == invite_id))
    ).scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.status not in {"pending", "sent"}:
        raise HTTPException(status_code=409, detail="Invite cannot be resent")

    token = _new_invite_token()
    invite.token_hash = hash_token(token)
    invite.expires_at = datetime.now(timezone.utc) + timedelta(hours=48)
    invite.status = "pending"
    invite.created_by = admin.id
    await db.flush()

    try:
        setup_url = send_invite_email(invite.email, token)
    except EmailSendError as exc:
        await db.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    await db.commit()
    return {
        "message": "Invite resent",
        "invite": _invite_payload(invite),
        "setup_url": setup_url if settings.app_env != "production" else None,
    }


@router.post("/invites/{invite_id}/revoke")
async def revoke_invite(invite_id: int, admin: AdminUser, db: DBSession):
    invite = (
        await db.execute(select(UserInvite).where(UserInvite.id == invite_id))
    ).scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.status in {"accepted", "revoked"}:
        raise HTTPException(status_code=409, detail="Invite cannot be revoked")

    invite.status = "revoked"
    await db.commit()
    return {"message": "Invite revoked", "invite": _invite_payload(invite)}


@router.post("/email/test")
async def test_email(body: TestEmailRequest, admin: AdminUser, db: DBSession):
    try:
        send_test_email(body.to.strip().lower())
    except EmailSendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"message": "Test email sent"}


# --- User management ---

@router.get("/users")
async def list_users(admin: AdminUser, db: DBSession, skip: int = 0, limit: int = Query(default=50, ge=1, le=100)):
    result = await db.execute(select(User).offset(skip).limit(limit))
    users = result.scalars().all()
    total_result = await db.execute(select(sa_func.count(User.id)))
    total = total_result.scalar_one()
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

    # Create onboarding row so the wizard shows on first login
    db.add(UserOnboarding(user_id=user.id))
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
@limiter.limit("5/minute")
async def reset_password(
    request: Request, user_id: int, body: ResetPasswordRequest, admin: AdminUser, db: DBSession
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

def _role_to_response(r: Role) -> RoleResponse:
    return RoleResponse(
        id=r.id,
        name=r.name,
        description=r.description,
        permissions=[
            PermissionResponse(id=p.id, codename=p.codename, description=p.description)
            for p in r.permissions
        ],
    )


@router.get("/roles")
async def list_roles(admin: AdminUser, db: DBSession):
    result = await db.execute(select(Role))
    roles = result.scalars().all()
    return {"roles": [_role_to_response(r) for r in roles]}


@router.post("/roles", status_code=201)
async def create_role(body: CreateRoleRequest, admin: AdminUser, db: DBSession):
    existing = await db.execute(select(Role).where(Role.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Role already exists")

    role = Role(name=body.name, description=body.description)
    if body.permission_ids:
        perms_result = await db.execute(
            select(Permission).where(Permission.id.in_(body.permission_ids))
        )
        role.permissions = list(perms_result.scalars().all())
    else:
        role.permissions = []
    db.add(role)
    await db.flush()

    return _role_to_response(role)


@router.put("/roles/{role_id}")
async def update_role(role_id: int, body: UpdateRoleRequest, admin: AdminUser, db: DBSession):
    """Update a role's name, description, and/or permissions."""
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")

    if body.name is not None:
        role.name = body.name
    if body.description is not None:
        role.description = body.description
    if body.permission_ids is not None:
        perms_result = await db.execute(
            select(Permission).where(Permission.id.in_(body.permission_ids))
        )
        role.permissions = list(perms_result.scalars().all())

    await db.flush()
    return _role_to_response(role)


@router.delete("/roles/{role_id}")
async def delete_role(role_id: int, admin: AdminUser, db: DBSession):
    """Delete a role (cannot delete built-in admin role)."""
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.name == "admin":
        raise HTTPException(status_code=400, detail="Cannot delete the admin role")

    await db.delete(role)
    await db.flush()
    return {"message": f"Role '{role.name}' deleted"}


@router.get("/permissions")
async def list_permissions(admin: AdminUser, db: DBSession):
    """List all available permissions."""
    result = await db.execute(select(Permission).order_by(Permission.id))
    perms = result.scalars().all()
    return {
        "permissions": [
            PermissionResponse(id=p.id, codename=p.codename, description=p.description)
            for p in perms
        ]
    }


# --- Project assignments ---

class AssignProjectRequest(BaseModel):
    user_id: int
    project_id: str = Field(..., min_length=1, max_length=255)


class ProjectAssignmentResponse(BaseModel):
    id: int
    user_id: int
    username: str
    project_id: str
    assigned_by: int | None
    assigned_at: str


class AuditLogResponse(BaseModel):
    id: int
    user_id: int | None
    username: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    details: dict | None
    ip_address: str | None
    created_at: str


@router.post("/project-assignments", status_code=201)
async def assign_project(
    request: Request,
    body: AssignProjectRequest,
    manager: ProjectManager,
    db: DBSession,
):
    """Assign a project to a user."""
    # Check user exists
    user_result = await db.execute(select(User).where(User.id == body.user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Check not already assigned
    existing = await db.execute(
        select(ProjectAssignment).where(
            ProjectAssignment.user_id == body.user_id,
            ProjectAssignment.project_id == body.project_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Project already assigned to this user")

    assignment = ProjectAssignment(
        user_id=body.user_id,
        project_id=body.project_id,
        assigned_by=manager.id,
    )
    db.add(assignment)
    await db.flush()

    audit = AuditService(db)
    await audit.log(
        user_id=manager.id,
        username=manager.username,
        action="assign_project",
        resource_type="project_assignment",
        resource_id=str(assignment.id),
        details={"user_id": body.user_id, "project_id": body.project_id},
        ip_address=request.client.host if request.client else None,
    )

    return ProjectAssignmentResponse(
        id=assignment.id,
        user_id=assignment.user_id,
        username=user.username,
        project_id=assignment.project_id,
        assigned_by=assignment.assigned_by,
        assigned_at=assignment.assigned_at.isoformat() if assignment.assigned_at else "",
    )


@router.delete("/project-assignments/{assignment_id}")
async def unassign_project(
    request: Request,
    assignment_id: int,
    manager: ProjectManager,
    db: DBSession,
):
    """Remove a project assignment."""
    result = await db.execute(
        select(ProjectAssignment).where(ProjectAssignment.id == assignment_id)
    )
    assignment = result.scalar_one_or_none()
    if assignment is None:
        raise HTTPException(status_code=404, detail="Assignment not found")

    audit = AuditService(db)
    await audit.log(
        user_id=manager.id,
        username=manager.username,
        action="unassign_project",
        resource_type="project_assignment",
        resource_id=str(assignment_id),
        details={"user_id": assignment.user_id, "project_id": assignment.project_id},
        ip_address=request.client.host if request.client else None,
    )

    await db.delete(assignment)
    await db.flush()

    return {"message": "Project unassigned"}


@router.get("/project-assignments")
async def list_project_assignments(
    manager: ProjectManager,
    db: DBSession,
    user_id: int | None = None,
    project_id: str | None = None,
):
    """List project assignments, optionally filtered."""
    query = select(ProjectAssignment)
    if user_id is not None:
        query = query.where(ProjectAssignment.user_id == user_id)
    if project_id is not None:
        query = query.where(ProjectAssignment.project_id == project_id)

    result = await db.execute(query)
    assignments = result.scalars().all()

    # Fetch usernames
    user_ids = {a.user_id for a in assignments}
    users_result = await db.execute(select(User).where(User.id.in_(user_ids))) if user_ids else None
    users_map = {u.id: u.username for u in users_result.scalars().all()} if users_result else {}

    return {
        "assignments": [
            ProjectAssignmentResponse(
                id=a.id,
                user_id=a.user_id,
                username=users_map.get(a.user_id, ""),
                project_id=a.project_id,
                assigned_by=a.assigned_by,
                assigned_at=a.assigned_at.isoformat() if a.assigned_at else "",
            )
            for a in assignments
        ],
        "total": len(assignments),
    }


@router.get("/users/{user_id}/projects")
async def get_user_projects(user_id: int, manager: ProjectManager, db: DBSession):
    """Get projects assigned to a specific user."""
    result = await db.execute(
        select(ProjectAssignment).where(ProjectAssignment.user_id == user_id)
    )
    assignments = result.scalars().all()
    return {
        "user_id": user_id,
        "projects": [a.project_id for a in assignments],
    }


# --- Audit logs ---

@router.get("/audit-logs")
async def list_audit_logs(
    admin: AuditViewer,
    db: DBSession,
    action: str | None = None,
    user_id: int | None = None,
    resource_type: str | None = None,
    skip: int = 0,
    limit: int = Query(default=50, ge=1, le=100),
):
    """List audit log entries (paginated, filterable)."""
    base_filter = select(AuditLog).order_by(AuditLog.created_at.desc())
    if action:
        base_filter = base_filter.where(AuditLog.action == action)
    if user_id is not None:
        base_filter = base_filter.where(AuditLog.user_id == user_id)
    if resource_type:
        base_filter = base_filter.where(AuditLog.resource_type == resource_type)

    # Efficient count query
    count_query = select(sa_func.count()).select_from(base_filter.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    result = await db.execute(base_filter.offset(skip).limit(limit))
    logs = result.scalars().all()

    return {
        "logs": [
            AuditLogResponse(
                id=log.id,
                user_id=log.user_id,
                username=log.username,
                action=log.action,
                resource_type=log.resource_type,
                resource_id=log.resource_id,
                details=log.details,
                ip_address=log.ip_address,
                created_at=log.created_at.isoformat(),
            )
            for log in logs
        ],
        "total": total,
    }


# --- Usage / Token tracking ---

@router.get("/usage/summary")
async def usage_summary(
    admin: AuditViewer,
    db: DBSession,
    days: int = 30,
):
    """Aggregated token usage summary for the last N days."""
    import datetime

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)

    # Total tokens by interface
    interface_result = await db.execute(
        select(
            QueryUsage.interface,
            sa_func.count(QueryUsage.id).label("count"),
            sa_func.coalesce(sa_func.sum(QueryUsage.input_tokens), 0).label("input_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.output_tokens), 0).label("output_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.cost_usd), 0).label("cost_usd"),
        )
        .where(QueryUsage.created_at >= cutoff)
        .group_by(QueryUsage.interface)
    )
    by_interface = [
        {
            "interface": row.interface,
            "count": row.count,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "cost_usd": round(float(row.cost_usd), 4),
        }
        for row in interface_result
    ]

    # Total tokens by model
    model_result = await db.execute(
        select(
            QueryUsage.model_name,
            QueryUsage.provider,
            sa_func.count(QueryUsage.id).label("count"),
            sa_func.coalesce(sa_func.sum(QueryUsage.input_tokens), 0).label("input_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.output_tokens), 0).label("output_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.cost_usd), 0).label("cost_usd"),
        )
        .where(QueryUsage.created_at >= cutoff)
        .group_by(QueryUsage.model_name, QueryUsage.provider)
    )
    by_model = [
        {
            "model": row.model_name,
            "provider": row.provider,
            "count": row.count,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "cost_usd": round(float(row.cost_usd), 4),
        }
        for row in model_result
    ]

    # Daily totals
    daily_result = await db.execute(
        select(
            sa_func.date_trunc("day", QueryUsage.created_at).label("day"),
            sa_func.count(QueryUsage.id).label("count"),
            sa_func.coalesce(sa_func.sum(QueryUsage.input_tokens), 0).label("input_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.output_tokens), 0).label("output_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.cost_usd), 0).label("cost_usd"),
        )
        .where(QueryUsage.created_at >= cutoff)
        .group_by("day")
        .order_by("day")
    )
    daily = [
        {
            "date": row.day.isoformat() if row.day else "",
            "count": row.count,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "cost_usd": round(float(row.cost_usd), 4),
        }
        for row in daily_result
    ]

    return {
        "days": days,
        "by_interface": by_interface,
        "by_model": by_model,
        "daily": daily,
    }


@router.get("/usage/recent")
async def usage_recent(
    admin: AuditViewer,
    db: DBSession,
    skip: int = 0,
    limit: int = Query(default=50, ge=1, le=100),
    interface: str | None = None,
    user_id: int | None = None,
):
    """List recent query usage entries."""
    query = select(QueryUsage).order_by(QueryUsage.created_at.desc())
    if interface:
        query = query.where(QueryUsage.interface == interface)
    if user_id is not None:
        query = query.where(QueryUsage.user_id == user_id)

    result = await db.execute(query.offset(skip).limit(limit))
    entries = result.scalars().all()

    return {
        "entries": [
            {
                "id": e.id,
                "user_id": e.user_id,
                "interface": e.interface,
                "query_text": (e.query_text[:100] + "...") if e.query_text and len(e.query_text) > 100 else e.query_text,
                "model_name": e.model_name,
                "provider": e.provider,
                "input_tokens": e.input_tokens,
                "output_tokens": e.output_tokens,
                "cost_usd": e.cost_usd,
                "latency_ms": e.latency_ms,
                "created_at": e.created_at.isoformat(),
            }
            for e in entries
        ],
    }


@router.get("/usage/by-user")
async def usage_by_user(
    admin: AuditViewer,
    db: DBSession,
    days: int = 30,
):
    """Aggregated usage grouped by user for the last N days."""
    import datetime

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)

    result = await db.execute(
        select(
            QueryUsage.user_id,
            User.username,
            User.email,
            sa_func.count(QueryUsage.id).label("count"),
            sa_func.coalesce(sa_func.sum(QueryUsage.input_tokens), 0).label("input_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.output_tokens), 0).label("output_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.cost_usd), 0).label("cost_usd"),
            sa_func.coalesce(sa_func.avg(QueryUsage.latency_ms), 0).label("avg_latency_ms"),
        )
        .join(User, QueryUsage.user_id == User.id, isouter=True)
        .where(QueryUsage.created_at >= cutoff)
        .group_by(QueryUsage.user_id, User.username, User.email)
        .order_by(sa_func.sum(QueryUsage.cost_usd).desc())
    )

    return {
        "days": days,
        "by_user": [
            {
                "user_id": row.user_id,
                "username": row.username or "(anonymous)",
                "email": row.email,
                "count": row.count,
                "input_tokens": int(row.input_tokens),
                "output_tokens": int(row.output_tokens),
                "cost_usd": round(float(row.cost_usd), 6),
                "avg_latency_ms": round(float(row.avg_latency_ms), 0),
            }
            for row in result
        ],
    }


@router.get("/usage/user/{user_id}")
async def usage_user_detail(
    user_id: int,
    admin: AuditViewer,
    db: DBSession,
    days: int = 30,
):
    """Detailed usage breakdown for a single user."""
    import datetime

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)

    # Fetch user info
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    base_filter = (QueryUsage.created_at >= cutoff) & (QueryUsage.user_id == user_id)

    # Totals
    totals_result = await db.execute(
        select(
            sa_func.count(QueryUsage.id).label("count"),
            sa_func.coalesce(sa_func.sum(QueryUsage.input_tokens), 0).label("input_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.output_tokens), 0).label("output_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.cost_usd), 0).label("cost_usd"),
            sa_func.coalesce(sa_func.avg(QueryUsage.latency_ms), 0).label("avg_latency_ms"),
        ).where(base_filter)
    )
    totals_row = totals_result.one()

    # Daily breakdown
    daily_result = await db.execute(
        select(
            sa_func.date_trunc("day", QueryUsage.created_at).label("day"),
            sa_func.count(QueryUsage.id).label("count"),
            sa_func.coalesce(sa_func.sum(QueryUsage.input_tokens), 0).label("input_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.output_tokens), 0).label("output_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.cost_usd), 0).label("cost_usd"),
        )
        .where(base_filter)
        .group_by("day")
        .order_by("day")
    )

    # By interface
    interface_result = await db.execute(
        select(
            QueryUsage.interface,
            sa_func.count(QueryUsage.id).label("count"),
            sa_func.coalesce(sa_func.sum(QueryUsage.input_tokens), 0).label("input_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.output_tokens), 0).label("output_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.cost_usd), 0).label("cost_usd"),
        )
        .where(base_filter)
        .group_by(QueryUsage.interface)
    )

    # By model
    model_result = await db.execute(
        select(
            QueryUsage.model_name,
            QueryUsage.provider,
            sa_func.count(QueryUsage.id).label("count"),
            sa_func.coalesce(sa_func.sum(QueryUsage.input_tokens), 0).label("input_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.output_tokens), 0).label("output_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.cost_usd), 0).label("cost_usd"),
        )
        .where(base_filter)
        .group_by(QueryUsage.model_name, QueryUsage.provider)
    )

    return {
        "user_id": user_id,
        "username": user.username,
        "email": user.email,
        "days": days,
        "totals": {
            "count": totals_row.count,
            "input_tokens": int(totals_row.input_tokens),
            "output_tokens": int(totals_row.output_tokens),
            "cost_usd": round(float(totals_row.cost_usd), 6),
            "avg_latency_ms": round(float(totals_row.avg_latency_ms), 0),
        },
        "daily": [
            {
                "date": row.day.isoformat() if row.day else "",
                "count": row.count,
                "input_tokens": int(row.input_tokens),
                "output_tokens": int(row.output_tokens),
                "cost_usd": round(float(row.cost_usd), 4),
            }
            for row in daily_result
        ],
        "by_interface": [
            {
                "interface": row.interface,
                "count": row.count,
                "input_tokens": int(row.input_tokens),
                "output_tokens": int(row.output_tokens),
                "cost_usd": round(float(row.cost_usd), 4),
            }
            for row in interface_result
        ],
        "by_model": [
            {
                "model": row.model_name,
                "provider": row.provider,
                "count": row.count,
                "input_tokens": int(row.input_tokens),
                "output_tokens": int(row.output_tokens),
                "cost_usd": round(float(row.cost_usd), 4),
            }
            for row in model_result
        ],
    }


@router.get("/usage/daily-by-user")
async def usage_daily_by_user(
    admin: AuditViewer,
    db: DBSession,
    days: int = 30,
    top: int = 10,
):
    """Daily usage broken down by top N users (for stacked charts)."""
    import datetime
    from collections import defaultdict

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)

    # Find top N users by total cost in period
    top_users_result = await db.execute(
        select(
            QueryUsage.user_id,
            User.username,
            sa_func.coalesce(sa_func.sum(QueryUsage.cost_usd), 0).label("total_cost"),
        )
        .join(User, QueryUsage.user_id == User.id, isouter=True)
        .where(QueryUsage.created_at >= cutoff, QueryUsage.user_id.is_not(None))
        .group_by(QueryUsage.user_id, User.username)
        .order_by(sa_func.sum(QueryUsage.cost_usd).desc())
        .limit(top)
    )
    top_users = [(row.user_id, row.username or f"user_{row.user_id}") for row in top_users_result]
    if not top_users:
        return {"days": days, "users": [], "daily": []}

    top_user_ids = [uid for uid, _ in top_users]
    user_names = {uid: name for uid, name in top_users}

    # Get daily data for those users
    daily_result = await db.execute(
        select(
            sa_func.date_trunc("day", QueryUsage.created_at).label("day"),
            QueryUsage.user_id,
            sa_func.count(QueryUsage.id).label("count"),
            sa_func.coalesce(sa_func.sum(QueryUsage.cost_usd), 0).label("cost_usd"),
            sa_func.coalesce(sa_func.sum(QueryUsage.input_tokens), 0).label("input_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.output_tokens), 0).label("output_tokens"),
        )
        .where(QueryUsage.created_at >= cutoff, QueryUsage.user_id.in_(top_user_ids))
        .group_by("day", QueryUsage.user_id)
        .order_by("day")
    )

    # Build chart-friendly structure
    days_data: dict[str, dict[int, dict]] = defaultdict(dict)
    for row in daily_result:
        date_str = row.day.isoformat() if row.day else ""
        days_data[date_str][row.user_id] = {
            "count": row.count,
            "cost_usd": round(float(row.cost_usd), 6),
            "input_tokens": int(row.input_tokens),
            "output_tokens": int(row.output_tokens),
        }

    daily = []
    for date_str in sorted(days_data.keys()):
        entry: dict = {"date": date_str}
        for uid in top_user_ids:
            name = user_names[uid]
            entry[name] = days_data[date_str].get(uid, {
                "count": 0, "cost_usd": 0, "input_tokens": 0, "output_tokens": 0,
            })
        daily.append(entry)

    return {
        "days": days,
        "users": [user_names[uid] for uid in top_user_ids],
        "daily": daily,
    }
