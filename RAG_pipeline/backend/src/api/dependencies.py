from __future__ import annotations

import logging
from typing import Annotated, Any, Callable

from fastapi import Cookie, Depends, HTTPException, Request
from jwt import InvalidTokenError as JWTError
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.security import decode_token
from src.config import settings
from src.db.session import get_db
from src.db.models import User
from src.licensing.core import Capability
from src.licensing.runtime import get_policy
from src.rbac.permissions import (
    RUNTIME_SOURCES_READ,
    RUNTIME_SOURCES_WRITE,
    get_user_project_ids,
    has_permission,
)
from src.runtime.read_scope import (
    RuntimeReadScope,
    resolve_runtime_read_scope,
    resolve_runtime_read_scope_for_user,
)
from src.runtime.store import RuntimeStoreRepository

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


# --- Subscription licence ------------------------------------------------------
#
# Every route that starts an LLM call (spends tokens) declares one of these. It
# is a dependency, not a check inside the handler, so it runs before a
# StreamingResponse exists: a refusal is a plain 402, never a broken SSE turn.
# 402 (not 401/403) because only a real 401 may log a user out.
#
# `tests/test_api/test_route_inventory.py` walks every mutating route and fails
# unless it carries this dependency or sits on a reviewed allowlist; it finds the
# gate through the `__licence_capability__` attribute set below.

LICENCE_CAPABILITY_ATTR = "__licence_capability__"


def require_capability(
    capability: Capability,
    *,
    principal: Callable[..., Any] = get_current_user,
) -> Callable[..., Any]:
    """Dependency factory: refuse with 402 unless the licence grants ``capability``.

    ``principal`` is resolved first, so an unauthenticated caller still gets the
    principal's own 401/403 and learns nothing about the licence. The resolved
    principal is returned, so a route can take it from this dependency. Machine
    entries with their own credential (the labs MCP bridge) pass their
    authenticator here instead of the cookie user.
    """

    async def _check_capability(subject: Any = Depends(principal)) -> Any:
        refusal = get_policy().check(capability)
        if refusal is not None:
            raise HTTPException(status_code=402, detail=refusal.to_detail())
        return subject

    setattr(_check_capability, LICENCE_CAPABILITY_ATTR, capability)
    _check_capability.__name__ = f"require_{capability.value}_capability"
    return _check_capability


#: For ``dependencies=[ChatEntitled]`` on a route that needs no user value.
ChatEntitled = Depends(require_capability(Capability.CHAT))
#: The authenticated user, admitted only while the licence includes chat.
ChatEntitledUser = Annotated[User, ChatEntitled]


ManagerUser = Annotated[User, Depends(require_permission("manage_users"))]
ProjectManager = Annotated[User, Depends(require_permission("assign_projects"))]
AuditViewer = Annotated[User, Depends(require_permission("view_audit_logs"))]
# Admins + the deployment Owner can create projects (they become the project owner). Ownership of
# an *existing* project is enforced separately via rbac.permissions.can_manage_project.
ProjectCreator = Annotated[User, Depends(require_permission("create_projects"))]
RuntimeSourceReader = Annotated[User, Depends(require_permission(RUNTIME_SOURCES_READ))]
RuntimeSourceWriter = Annotated[User, Depends(require_permission(RUNTIME_SOURCES_WRITE))]


# --- Runtime read scope -------------------------------------------------------
#
# ONE resolution point for every runtime READ. Before this, ~30 handlers each
# wrote `scope = await get_user_project_ids(user, db)` followed by
# `_intersect_scope(scope, project_id)`, which is why stored read grants reached
# exactly three of them: the other ~37 filtered on the raw row stamp and returned
# NOTHING to a project reading a shared mirror through a grant.
#
# THE RULE THESE DEPENDENCIES EXIST TO ENFORCE: `RuntimeReadScope.scope` is
# WIDENED to include the granting project, while the per-grant environment and
# window limits live in `.terms`. The two are therefore halves of one filter and
# must travel together -- a handler that passes `read.scope` without
# `read_terms=read.terms` has asked for the grantor's rows with the grant's own
# narrowing switched off. Returning the whole object from the dependency, rather
# than a bare `ProjectScope`, is what makes forgetting the second half visible at
# the call site instead of silent in the result.


async def get_runtime_read_scope(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
) -> RuntimeReadScope:
    """Resolve the read set for a request that NAMES a project.

    FastAPI supplies `project_id` from the query string, so a handler that
    already declares its own `project_id` keeps its own requiredness (a route
    declaring `project_id: str` still 422s when it is missing) and both see the
    same value. A handler that declares none simply inherits an optional filter,
    which is what every `/runtime/*` list endpoint already offered.

    Widening comes ONLY from stored grant rows, never from request input --
    `resolve_runtime_read_scope` checks the caller's own scope against the named
    project FIRST and denies before it looks a grant up. A caller that names no
    project is not widened at all: `RuntimeReadScope.unscoped` carries the RBAC
    boundary with no terms, so a listing across everything behaves exactly as it
    did before grants existed.

    Denial is returned (`denies_all`), not raised, because the routes disagree
    about what it means: a point lookup owes a 404 and a list owes an empty page.
    """
    return await resolve_runtime_read_scope(
        RuntimeStoreRepository(db),
        user_scope=await get_user_project_ids(user, db),
        project_id=project_id,
    )


RuntimeRead = Annotated[RuntimeReadScope, Depends(get_runtime_read_scope)]


async def get_runtime_read_scope_for_user(
    user: CurrentUser,
    db: DBSession,
) -> RuntimeReadScope:
    """Resolve the read set for a request that reads BY ID, naming no project.

    `GET /runtime/executions/{execution_id}` and its siblings identify a row by
    its own id, so `get_runtime_read_scope` would have nothing to widen from and
    the drill-down would 404 on exactly the rows a granted reader had just seen
    in a list -- a dead end one click after the feature appears to work, which is
    worse than not sharing at all.

    The widening is the union of the grants held by every project the user can
    already see; `resolve_runtime_read_scope_for_user` documents why that grants
    no reach naming those projects one at a time would not, and skips the scan
    entirely for an unrestricted user.

    Deliberately NOT the same dependency with `project_id=None`: that case is
    defined as "no widening at all", and quietly widening it would change every
    unfiltered list endpoint in the router at once.
    """
    return await resolve_runtime_read_scope_for_user(
        RuntimeStoreRepository(db),
        user_scope=await get_user_project_ids(user, db),
    )


RuntimeReadById = Annotated[RuntimeReadScope, Depends(get_runtime_read_scope_for_user)]
