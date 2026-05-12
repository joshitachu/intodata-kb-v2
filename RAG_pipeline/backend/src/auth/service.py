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
        access = create_access_token(user.id, user.username)
        refresh, expires_at = create_refresh_token(user.id)

        token_record = RefreshToken(
            token_hash=hash_token(refresh),
            user_id=user.id,
            expires_at=expires_at,
        )
        self.db.add(token_record)
        return access, refresh

    async def rotate_refresh_token(self, old_refresh: str) -> tuple[str, str, User] | None:
        """Revoke old refresh token, issue new pair. Returns (access, refresh, user) or None."""
        old_hash = hash_token(old_refresh)
        result = await self.db.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == old_hash,
                RefreshToken.revoked == False,  # noqa: E712
            )
        )
        token_record = result.scalar_one_or_none()
        if token_record is None:
            return None
        if token_record.expires_at < datetime.now(timezone.utc):
            return None

        # Revoke old token
        token_record.revoked = True

        # Load user
        user_result = await self.db.execute(select(User).where(User.id == token_record.user_id))
        user = user_result.scalar_one_or_none()
        if user is None or not user.is_active:
            return None

        # Issue new pair
        access, refresh = await self.create_tokens(user)
        return access, refresh, user

    async def revoke_refresh_token(self, refresh: str) -> None:
        """Revoke a specific refresh token."""
        token_hash = hash_token(refresh)
        await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .values(revoked=True)
        )

    async def revoke_all_user_tokens(self, user_id: int) -> None:
        """Revoke all refresh tokens for a user (e.g., on password reset)."""
        await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked == False)  # noqa: E712
            .values(revoked=True)
        )
