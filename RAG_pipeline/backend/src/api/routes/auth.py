"""Authentication endpoints: login, logout, refresh, me, MFA."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from jose import JWTError, jwt
from pydantic import BaseModel, Field, model_validator
from slowapi import Limiter
from slowapi.util import get_remote_address

from sqlalchemy import func as sa_func, select
from sqlalchemy.orm.attributes import flag_modified

from src.api.dependencies import CurrentUser, DBSession
from src.auth.security import hash_password, hash_token, verify_password
from src.auth.service import AuthService, PasswordlessAccountError
from src.auth.totp import (
    consume_backup_code,
    generate_backup_codes,
    generate_totp_secret,
    hash_backup_codes,
    provisioning_uri,
    verify_totp,
)
from src.config import settings
from src.db.models import AccessRequest, EmailOTPCode, Notification, Role, User, UserInvite, UserOnboarding
from src.rbac.permissions import get_user_permissions
from src.services.audit_service import AuditService
from src.services.email_service import EmailSendError, send_invite_otp_email, send_login_otp_email

MFA_TOKEN_TTL_SECONDS = 300  # 5 minutes between login and MFA verify
EMAIL_OTP_TTL_MINUTES = 10
EMAIL_OTP_MAX_ATTEMPTS = 5

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
    identifier: str | None = Field(default=None, min_length=1)
    username: str | None = Field(default=None, min_length=1)
    password: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def require_identifier(self) -> "LoginRequest":
        if not (self.identifier or self.username or "").strip():
            raise ValueError("Username, email, or identifier is required")
        return self

    @property
    def login_identifier(self) -> str:
        return (self.identifier or self.username or "").strip()


class BootstrapAdminRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=12, max_length=128)


class RequestAccessBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    full_name: str | None = Field(default=None, max_length=255)


class InviteAcceptBody(BaseModel):
    token: str | None = Field(default=None, min_length=16)
    email: str | None = Field(default=None, min_length=3, max_length=255)
    code: str | None = Field(default=None, min_length=6, max_length=6)
    username: str = Field(..., min_length=3, max_length=100)
    password: str | None = Field(default=None, min_length=12, max_length=128)
    full_name: str = Field(..., min_length=1, max_length=255)
    role: str = Field(..., min_length=1, max_length=255)
    intended_use: str = Field(..., min_length=1, max_length=2000)


class OTPSendBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    purpose: str = Field(..., pattern="^(login|invite)$")


class OTPVerifyBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    code: str = Field(..., min_length=6, max_length=6)
    purpose: str = Field(..., pattern="^(login|invite)$")


class InviteStartBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)


class VerifyMFARequest(BaseModel):
    mfa_token: str = Field(..., min_length=1)
    # 6-digit TOTP code OR a backup code (alphanumeric, up to 16 chars)
    code: str = Field(..., min_length=6, max_length=16)


class TOTPVerifyRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6)


class TOTPDisableRequest(BaseModel):
    password: str | None = Field(default=None, min_length=1)
    code: str = Field(..., min_length=6, max_length=16)


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    is_active: bool
    is_superadmin: bool
    roles: list[str]
    permissions: list[str] = []
    onboarding_completed: bool = True
    tour_completed: bool = True
    mfa_enabled: bool = False


def _create_mfa_token(user_id: int) -> str:
    """Short-lived JWT that proves the user passed the password step.

    Scoped with `type: "mfa"` so it cannot be confused with an access or
    refresh token by middleware that trusts only `type=access`.
    """
    expire = datetime.now(timezone.utc) + timedelta(seconds=MFA_TOKEN_TTL_SECONDS)
    payload = {"sub": str(user_id), "type": "mfa", "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _decode_mfa_token(token: str) -> int | None:
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        return None
    if payload.get("type") != "mfa":
        return None
    try:
        return int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None


async def _onboarding_flags(db, user_id: int) -> tuple[bool, bool]:
    """Return (wizard_completed, tour_completed). Missing row = completed."""
    result = await db.execute(
        select(UserOnboarding).where(UserOnboarding.user_id == user_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return True, True
    return row.wizard_completed, row.tour_completed


def _normalize_email(email: str) -> str:
    email = email.strip().lower()
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise HTTPException(status_code=422, detail="Valid email is required")
    return email


def _is_expired(expires_at: datetime) -> bool:
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


def _new_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _otp_hash(email: str, purpose: str, code: str) -> str:
    return hash_token(f"{settings.jwt_secret_key}:{email}:{purpose}:{code}")


def _request_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _expire_active_otps(db, email: str, purpose: str) -> None:
    rows = (
        await db.execute(
            select(EmailOTPCode).where(
                EmailOTPCode.email == email,
                EmailOTPCode.purpose == purpose,
                EmailOTPCode.consumed_at.is_(None),
            )
        )
    ).scalars().all()
    now = datetime.now(timezone.utc)
    for row in rows:
        row.consumed_at = now


async def _issue_email_otp(
    db,
    *,
    email: str,
    purpose: str,
    request: Request,
    invite: UserInvite | None = None,
) -> str:
    code = _new_otp_code()
    await _expire_active_otps(db, email, purpose)
    db.add(
        EmailOTPCode(
            email=email,
            code_hash=_otp_hash(email, purpose, code),
            purpose=purpose,
            invite_id=invite.id if invite else None,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=EMAIL_OTP_TTL_MINUTES),
            ip_address=_request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    )
    await db.flush()
    return code


async def _verify_email_otp(
    db,
    *,
    email: str,
    purpose: str,
    code: str,
    invite_required: bool = False,
) -> EmailOTPCode:
    row = (
        await db.execute(
            select(EmailOTPCode)
            .where(
                EmailOTPCode.email == email,
                EmailOTPCode.purpose == purpose,
                EmailOTPCode.consumed_at.is_(None),
            )
            .order_by(EmailOTPCode.created_at.desc(), EmailOTPCode.id.desc())
        )
    ).scalars().first()
    if row is None or _is_expired(row.expires_at):
        raise HTTPException(status_code=401, detail="Invalid or expired code")
    if row.attempt_count >= EMAIL_OTP_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many verification attempts")
    if invite_required and row.invite_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired code")

    if row.code_hash != _otp_hash(email, purpose, code.strip()):
        row.attempt_count += 1
        await db.flush()
        raise HTTPException(status_code=401, detail="Invalid or expired code")
    return row


def _user_response(user: User, onboarding_completed: bool, tour_completed: bool) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        is_superadmin=user.is_superadmin,
        roles=[r.name for r in user.roles],
        permissions=sorted(get_user_permissions(user)),
        onboarding_completed=onboarding_completed,
        tour_completed=tour_completed,
        mfa_enabled=user.mfa_enabled,
    )


@router.post("/request-access")
@limiter.limit("5/minute")
async def request_access(request: Request, body: RequestAccessBody, db: DBSession):
    email = _normalize_email(body.email)

    existing_user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing_user is not None:
        return {
            "status": "submitted",
            "message": "If access is needed, an administrator will review the request.",
        }

    pending = (
        await db.execute(
            select(AccessRequest).where(
                AccessRequest.email == email,
                AccessRequest.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if pending is not None:
        return {
            "status": "already_pending",
            "message": "A request for this email is already pending.",
        }

    access_request = AccessRequest(
        email=email,
        full_name=body.full_name.strip() if body.full_name else None,
        status="pending",
    )
    db.add(access_request)
    await db.flush()

    admins = (
        await db.execute(select(User).where(User.is_superadmin == True, User.is_active == True))  # noqa: E712
    ).scalars().all()
    for admin in admins:
        db.add(
            Notification(
                user_id=admin.id,
                type="access_request",
                title="Access request submitted",
                message=f"{email} requested access to this app.",
                meta={"access_request_id": access_request.id, "email": email},
            )
        )

    await db.commit()
    logger.info(
        "Access request submitted id=%s email=%s ip=%s",
        access_request.id,
        email,
        request.client.host if request.client else None,
    )
    return {"status": "submitted", "message": "Request submitted for admin review."}


async def _load_invite_by_token(db, token: str) -> UserInvite:
    invite = (
        await db.execute(select(UserInvite).where(UserInvite.token_hash == hash_token(token)))
    ).scalar_one_or_none()
    if invite is None or invite.status != "pending" or _is_expired(invite.expires_at):
        raise HTTPException(status_code=404, detail="Invite is invalid or expired")
    return invite


async def _load_pending_invite_by_email(db, email: str) -> UserInvite:
    invite = (
        await db.execute(
            select(UserInvite)
            .where(UserInvite.email == email, UserInvite.status.in_(["pending", "sent"]))
            .order_by(UserInvite.created_at.desc(), UserInvite.id.desc())
        )
    ).scalars().first()
    if invite is None or _is_expired(invite.expires_at):
        raise HTTPException(status_code=404, detail="Invite is invalid or expired")
    return invite


@router.post("/otp/send")
@limiter.limit("5/minute")
async def send_otp(request: Request, body: OTPSendBody, db: DBSession):
    email = _normalize_email(body.email)
    if body.purpose == "login":
        user = (
            await db.execute(select(User).where(User.email == email, User.is_active == True))  # noqa: E712
        ).scalar_one_or_none()
        if user is not None:
            code = await _issue_email_otp(db, email=email, purpose="login", request=request)
            try:
                send_login_otp_email(email, code)
            except EmailSendError as exc:
                await db.rollback()
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            await db.commit()
        return {
            "status": "sent",
            "message": "If an active account exists for that email, a sign-in code has been sent.",
        }

    invite = await _load_pending_invite_by_email(db, email)
    code = await _issue_email_otp(db, email=email, purpose="invite", request=request, invite=invite)
    try:
        send_invite_otp_email(email, code)
    except EmailSendError as exc:
        await db.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await db.commit()
    return {"status": "sent", "message": "Setup code sent."}


@router.post("/otp/verify")
@limiter.limit("5/minute")
async def verify_otp(
    request: Request, body: OTPVerifyBody, db: DBSession, response: Response
):
    email = _normalize_email(body.email)
    otp = await _verify_email_otp(db, email=email, purpose=body.purpose, code=body.code)
    if body.purpose == "invite":
        invite = await _load_pending_invite_by_email(db, email)
        if otp.invite_id != invite.id:
            raise HTTPException(status_code=401, detail="Invalid or expired code")
        return {
            "status": "verified",
            "email": invite.email,
            "full_name": invite.full_name,
            "expires_at": invite.expires_at.isoformat(),
        }

    user = (
        await db.execute(select(User).where(User.email == email, User.is_active == True))  # noqa: E712
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired code")

    otp.consumed_at = datetime.now(timezone.utc)
    if user.mfa_enabled:
        await db.commit()
        return {
            "mfa_required": True,
            "mfa_token": _create_mfa_token(user.id),
            "expires_in": MFA_TOKEN_TTL_SECONDS,
        }

    user.last_login_at = datetime.now(timezone.utc)
    svc = AuthService(db)
    access, refresh = await svc.create_tokens(user)
    _set_auth_cookies(response, access, refresh)
    await db.commit()

    audit = AuditService(db)
    await audit.log(
        user_id=user.id,
        username=user.username,
        action="login_email_otp",
        resource_type="session",
        ip_address=_request_ip(request),
    )
    onboarding_completed, tour_completed = await _onboarding_flags(db, user.id)
    return {
        "message": "Login successful",
        "user": _user_response(user, onboarding_completed, tour_completed),
    }


@router.post("/invite/start")
@limiter.limit("5/minute")
async def start_invite(request: Request, body: InviteStartBody, db: DBSession):
    email = _normalize_email(body.email)
    invite = await _load_pending_invite_by_email(db, email)
    code = await _issue_email_otp(db, email=email, purpose="invite", request=request, invite=invite)
    try:
        send_invite_otp_email(email, code)
    except EmailSendError as exc:
        await db.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await db.commit()
    return {"status": "sent", "message": "Setup code sent."}


@router.get("/invite/validate")
async def validate_invite(token: str, db: DBSession):
    invite = await _load_invite_by_token(db, token)
    return {
        "email": invite.email,
        "expires_at": invite.expires_at.isoformat(),
        "full_name": invite.full_name,
    }


@router.post("/invite/accept")
@limiter.limit("5/minute")
async def accept_invite(
    request: Request, body: InviteAcceptBody, db: DBSession, response: Response
):
    otp: EmailOTPCode | None = None
    if body.token:
        invite = await _load_invite_by_token(db, body.token)
    else:
        if not body.email or not body.code:
            raise HTTPException(status_code=422, detail="Email and verification code are required")
        email = _normalize_email(body.email)
        otp = await _verify_email_otp(
            db,
            email=email,
            purpose="invite",
            code=body.code,
            invite_required=True,
        )
        invite = await _load_pending_invite_by_email(db, email)
        if otp.invite_id != invite.id:
            raise HTTPException(status_code=401, detail="Invalid or expired code")

    existing = await db.execute(
        select(User).where((User.username == body.username) | (User.email == invite.email))
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Username or email already exists")

    user = User(
        username=body.username.strip(),
        email=invite.email,
        password_hash=hash_password(body.password) if body.password else None,
    )
    default_role = (
        await db.execute(select(Role).where(Role.name == "developer"))
    ).scalar_one_or_none()
    user.roles = [default_role] if default_role is not None else []
    db.add(user)
    await db.flush()

    db.add(UserOnboarding(user_id=user.id))
    invite.status = "accepted"
    invite.accepted_user_id = user.id
    invite.accepted_at = datetime.now(timezone.utc)
    invite.full_name = body.full_name.strip()
    invite.role_title = body.role.strip()
    invite.intended_use = body.intended_use.strip()
    if otp is not None:
        otp.consumed_at = datetime.now(timezone.utc)

    svc = AuthService(db)
    access, refresh = await svc.create_tokens(user)
    _set_auth_cookies(response, access, refresh)

    await db.commit()

    audit = AuditService(db)
    await audit.log(
        user_id=user.id,
        username=user.username,
        action="invite_accepted",
        resource_type="user_invite",
        resource_id=str(invite.id),
        ip_address=request.client.host if request.client else None,
    )

    return {
        "message": "Invite accepted",
        "user": _user_response(user, onboarding_completed=False, tour_completed=False),
    }


@router.post("/login")
@limiter.limit("5/minute")
async def login(request: Request, body: LoginRequest, db: DBSession, response: Response):
    svc = AuthService(db)
    try:
        user = await svc.authenticate_user(body.login_identifier, body.password)
    except PasswordlessAccountError as exc:
        raise HTTPException(
            status_code=401,
            detail="This account does not have a password. Use email code or contact an admin.",
        ) from exc
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username/email or password")

    # If MFA is enabled, stop here and hand the client a short-lived mfa_token.
    # No cookies are set until /auth/verify-mfa succeeds, so a stolen password
    # alone is not enough to get a session.
    if user.mfa_enabled:
        mfa_token = _create_mfa_token(user.id)
        return {
            "mfa_required": True,
            "mfa_token": mfa_token,
            "expires_in": MFA_TOKEN_TTL_SECONDS,
        }

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

    onboarding_completed, tour_completed = await _onboarding_flags(db, user.id)

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
            onboarding_completed=onboarding_completed,
            tour_completed=tour_completed,
            mfa_enabled=user.mfa_enabled,
        ),
    }


@router.post("/verify-mfa")
@limiter.limit("5/minute")
async def verify_mfa(
    request: Request, body: VerifyMFARequest, db: DBSession, response: Response
):
    """Second step of login when the user has MFA enabled.

    Accepts either a 6-digit TOTP code or one of the single-use backup codes.
    On success, issues the same access/refresh cookie pair as /auth/login.
    """
    user_id = _decode_mfa_token(body.mfa_token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired MFA token")

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active or not user.mfa_enabled or not user.totp_secret:
        raise HTTPException(status_code=401, detail="Invalid or expired MFA token")

    code = body.code.strip()
    ok = False
    used_backup = False
    if len(code) == 6 and code.isdigit():
        ok = verify_totp(user.totp_secret, code)
    if not ok:
        ok, updated = consume_backup_code(user.backup_codes_hashed, code)
        if ok:
            user.backup_codes_hashed = updated
            flag_modified(user, "backup_codes_hashed")
            used_backup = True

    if not ok:
        raise HTTPException(status_code=401, detail="Invalid code")

    user.last_login_at = datetime.now(timezone.utc)

    svc = AuthService(db)
    access, refresh = await svc.create_tokens(user)
    _set_auth_cookies(response, access, refresh)

    await db.commit()

    audit = AuditService(db)
    await audit.log(
        user_id=user.id,
        username=user.username,
        action="login_mfa_backup" if used_backup else "login_mfa",
        resource_type="session",
        ip_address=request.client.host if request.client else None,
    )

    onboarding_completed, tour_completed = await _onboarding_flags(db, user.id)

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
            onboarding_completed=onboarding_completed,
            tour_completed=tour_completed,
            mfa_enabled=user.mfa_enabled,
        ),
    }


@router.post("/totp/setup")
async def totp_setup(user: CurrentUser, db: DBSession):
    """Generate a fresh TOTP secret for the user. Does not enable MFA yet.

    The returned `otpauth_uri` is rendered as a QR code by the frontend; the
    user scans it, then calls /auth/totp/verify with a 6-digit code. Only
    after a successful verify does `mfa_enabled` flip to True and backup codes
    get generated.
    """
    if user.mfa_enabled:
        raise HTTPException(status_code=409, detail="MFA is already enabled")
    secret = generate_totp_secret()
    user.totp_secret = secret
    await db.commit()
    return {
        "secret": secret,
        "otpauth_uri": provisioning_uri(secret, user.email or user.username),
    }


@router.post("/totp/verify")
async def totp_verify(body: TOTPVerifyRequest, user: CurrentUser, db: DBSession):
    """Confirm the pending secret by verifying a 6-digit code.

    Flips mfa_enabled to True and generates 10 single-use backup codes. The
    plaintext codes are returned here ONCE — they are stored only as bcrypt
    hashes server-side.
    """
    if not user.totp_secret:
        raise HTTPException(
            status_code=400,
            detail="No pending TOTP secret. Call /auth/totp/setup first.",
        )
    if not verify_totp(user.totp_secret, body.code):
        raise HTTPException(status_code=401, detail="Invalid code")

    plaintext_codes = generate_backup_codes()
    user.backup_codes_hashed = hash_backup_codes(plaintext_codes)
    user.mfa_enabled = True
    await db.commit()
    logger.info(f"MFA enabled for user id={user.id}")
    return {"message": "MFA enabled", "backup_codes": plaintext_codes}


@router.post("/totp/disable")
async def totp_disable(body: TOTPDisableRequest, user: CurrentUser, db: DBSession):
    """Turn off MFA after verifying account password, when present, and a current code.

    Passwordless invited users do not have a password hash, so the current TOTP
    or backup code remains the required proof for those accounts.
    """
    if not user.mfa_enabled or not user.totp_secret:
        raise HTTPException(status_code=409, detail="MFA is not enabled")
    if user.password_hash and (
        not body.password or not verify_password(body.password, user.password_hash)
    ):
        raise HTTPException(status_code=401, detail="Invalid password")

    code = body.code.strip()
    ok = False
    if len(code) == 6 and code.isdigit():
        ok = verify_totp(user.totp_secret, code)
    if not ok:
        ok, updated = consume_backup_code(user.backup_codes_hashed, code)
        if ok:
            user.backup_codes_hashed = updated
            flag_modified(user, "backup_codes_hashed")
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid code")

    user.mfa_enabled = False
    user.totp_secret = None
    user.backup_codes_hashed = None
    await db.commit()
    logger.info(f"MFA disabled for user id={user.id}")
    return {"message": "MFA disabled"}


@router.get("/bootstrap-status")
async def bootstrap_status(db: DBSession):
    """Open endpoint telling the login page whether any users exist yet.

    Returns ``{"needs_bootstrap": true}`` on an empty users table so the client
    can show the "create first admin" form instead of the login form.
    """
    user_count = (await db.execute(select(sa_func.count(User.id)))).scalar_one()
    return {"needs_bootstrap": user_count == 0}


@router.post("/bootstrap-admin")
@limiter.limit("5/minute")
async def bootstrap_admin(
    request: Request, body: BootstrapAdminRequest, db: DBSession, response: Response
):
    """Create the very first admin on a fresh client stack.

    Only succeeds when the users table is empty. Once any user exists this
    endpoint returns 409 forever. Creates an active, superadmin user attached
    to the seeded `admin` role, writes a UserOnboarding row (so the wizard
    shows), and logs the caller in by setting the auth cookies.
    """
    user_count = (await db.execute(select(sa_func.count(User.id)))).scalar_one()
    if user_count > 0:
        raise HTTPException(
            status_code=409,
            detail="Bootstrap already complete — an admin exists. Use POST /auth/login.",
        )

    existing = await db.execute(
        select(User).where((User.username == body.username) | (User.email == body.email))
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Username or email already exists")

    admin_role = (
        await db.execute(select(Role).where(Role.name == "admin"))
    ).scalar_one_or_none()

    user = User(
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
        is_superadmin=True,
    )
    if admin_role is not None:
        user.roles = [admin_role]
    db.add(user)
    await db.flush()

    db.add(UserOnboarding(user_id=user.id))
    await db.flush()

    svc = AuthService(db)
    access, refresh = await svc.create_tokens(user)
    _set_auth_cookies(response, access, refresh)

    await db.commit()

    audit = AuditService(db)
    await audit.log(
        user_id=user.id,
        username=user.username,
        action="bootstrap_admin",
        resource_type="user",
        resource_id=str(user.id),
        ip_address=request.client.host if request.client else None,
    )

    logger.info(f"Bootstrapped first admin: id={user.id} username={user.username}")

    return {
        "message": "Admin created",
        "user": UserResponse(
            id=user.id,
            username=user.username,
            email=user.email,
            is_active=user.is_active,
            is_superadmin=user.is_superadmin,
            roles=[r.name for r in user.roles],
            permissions=sorted(get_user_permissions(user)),
            onboarding_completed=False,
            tour_completed=False,
            mfa_enabled=user.mfa_enabled,
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
async def me(user: CurrentUser, db: DBSession):
    onboarding_completed, tour_completed = await _onboarding_flags(db, user.id)
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        is_superadmin=user.is_superadmin,
        roles=[r.name for r in user.roles],
        permissions=sorted(get_user_permissions(user)),
        onboarding_completed=onboarding_completed,
        tour_completed=tour_completed,
        mfa_enabled=user.mfa_enabled,
    )
