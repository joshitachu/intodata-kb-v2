"""Auth service: user authentication, refresh token management."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)
from src.db.models import RefreshToken, User

logger = logging.getLogger(__name__)

LOCKOUT_THRESHOLD = 5
LOCKOUT_DURATION_MINUTES = 15

# How long a just-rotated refresh token keeps working.
#
# Rotation is single-use, so two refreshes racing on the same token used to
# leave the loser with a 401 -- which the frontend read as "logged out". This
# window absorbs that race (aligned tab timers, a retry after a slow response,
# development double-effects) without weakening the single-use property for
# anything older. Deliberately short: within the window a leaked token is
# replayable, so this trades a small exposure for not evicting live sessions.
ROTATION_GRACE_SECONDS = 15


def _as_utc(value: datetime) -> datetime:
    """Treat naive timestamps from the DB as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class PasswordlessAccountError(Exception):
    """Raised when password auth is attempted for an account without a password."""


class AccountLockedError(Exception):
    """Raised when the account is temporarily locked due to repeated failures."""

    def __init__(self, locked_until: datetime) -> None:
        self.locked_until = locked_until
        super().__init__(f"Account locked until {locked_until.isoformat()}")


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def authenticate_user(self, identifier: str, password: str) -> User | None:
        """Verify username/email + password. Returns User or None.

        Raises AccountLockedError if the account is temporarily locked.
        Raises PasswordlessAccountError if the account has no password set.
        """
        identifier = identifier.strip()
        result = await self.db.execute(
            select(User).where(
                (User.username == identifier)
                | (func.lower(User.email) == identifier.lower())
            )
        )
        user = result.scalar_one_or_none()
        if user is None:
            return None
        if not user.is_active:
            return None
        if user.password_hash is None:
            raise PasswordlessAccountError

        now = datetime.now(timezone.utc)

        # Check active lockout
        if user.locked_until is not None and user.locked_until > now:
            raise AccountLockedError(user.locked_until)

        # Lockout window may have expired — clear stale state
        if user.locked_until is not None and user.locked_until <= now:
            user.locked_until = None
            user.failed_login_attempts = 0

        if not verify_password(password, user.password_hash):
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= LOCKOUT_THRESHOLD:
                user.locked_until = now + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
                user.failed_login_attempts = 0
                logger.warning(
                    "Account locked after %d failed attempts",
                    LOCKOUT_THRESHOLD,
                    extra={"user_id": user.id, "locked_until": user.locked_until.isoformat()},
                )
                raise AccountLockedError(user.locked_until)
            return None

        # Successful auth — reset lockout state
        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login_at = now
        return user

    async def create_tokens(self, user: User) -> tuple[str, str]:
        """Create access + refresh tokens. Stores refresh token hash in DB."""
        access = create_access_token(user.id, user.username, user.mfa_enabled)
        refresh, expires_at = create_refresh_token(user.id)

        token_record = RefreshToken(
            token_hash=hash_token(refresh),
            user_id=user.id,
            expires_at=expires_at,
        )
        self.db.add(token_record)
        return access, refresh

    async def rotate_refresh_token(self, old_refresh: str) -> tuple[str, str, User] | None:
        """Revoke old refresh token, issue new pair. Returns (access, refresh, user) or None.

        Accepts a token that was itself rotated within ``ROTATION_GRACE_SECONDS``
        so two concurrent refreshes both succeed instead of one being treated as
        a logout. Tokens revoked for any other reason (logout, password change,
        admin revocation) have no ``rotated_at`` and are always rejected.
        """
        old_hash = hash_token(old_refresh)
        now = datetime.now(timezone.utc)

        # Fetch regardless of `revoked` so a just-rotated token can be told apart
        # from one that was deliberately revoked.
        result = await self.db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == old_hash)
        )
        token_record = result.scalar_one_or_none()
        if token_record is None:
            return None
        if _as_utc(token_record.expires_at) < now:
            return None

        if token_record.revoked:
            if token_record.rotated_at is None:
                # Revoked on purpose -- never honour it again.
                return None
            if _as_utc(token_record.rotated_at) < now - timedelta(seconds=ROTATION_GRACE_SECONDS):
                return None
            logger.info(
                "Refresh token replayed inside the rotation grace window",
                extra={"user_id": token_record.user_id},
            )
        else:
            # Revoke old token, marking it as superseded rather than withdrawn.
            token_record.revoked = True
            token_record.rotated_at = now

        # Load user
        user_result = await self.db.execute(select(User).where(User.id == token_record.user_id))
        user = user_result.scalar_one_or_none()
        if user is None or not user.is_active:
            return None

        # Issue new pair
        access, refresh = await self.create_tokens(user)
        return access, refresh, user

    async def refresh_token_is_dead(self, old_refresh: str) -> bool:
        """True only when the presented token proves the session is over.

        A rotation can fail for reasons that say nothing about the session: a
        cookie that never reached the backend, or a value this deployment has
        no row for. Treating those as a logout is what evicted users whose
        access token was still valid for the rest of its hour and whose
        refresh row was still unrevoked. Only a deliberate revocation
        (``rotated_at is None`` -- logout, password change, admin action) or a
        genuinely expired row is proof, and only those may clear the cookies.
        """
        result = await self.db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(old_refresh))
        )
        record = result.scalar_one_or_none()
        if record is None:
            return False
        if _as_utc(record.expires_at) < datetime.now(timezone.utc):
            return True
        return bool(record.revoked and record.rotated_at is None)

    # Every revocation below clears `rotated_at` as well as setting `revoked`.
    # Leaving it set would let a token that happened to be rotated seconds
    # earlier still mint a new pair through the grace window in
    # `rotate_refresh_token` -- so a password change would not actually end the
    # other sessions. For the same reason these statements deliberately do not
    # filter on `revoked == False`: already-revoked rows must have their grace
    # withdrawn too.

    async def revoke_refresh_token(self, refresh: str) -> None:
        """Revoke a specific refresh token."""
        token_hash = hash_token(refresh)
        await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .values(revoked=True, rotated_at=None)
        )

    async def revoke_all_user_tokens(self, user_id: int) -> None:
        """Revoke all refresh tokens for a user (e.g., on password reset)."""
        await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id)
            .values(revoked=True, rotated_at=None)
        )

    async def revoke_other_user_tokens(self, user_id: int, keep_token: str) -> None:
        """Revoke all refresh tokens for a user except the one provided (keeps current session alive)."""
        keep_hash = hash_token(keep_token)
        await self.db.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.token_hash != keep_hash,
            )
            .values(revoked=True, rotated_at=None)
        )
