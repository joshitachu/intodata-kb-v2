"""Unit tests for auth service: lockout, backup codes, TOTP, refresh token rotation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pyotp
import pytest

from src.auth.security import create_refresh_token, hash_password, hash_token
from src.auth.service import (
    LOCKOUT_DURATION_MINUTES,
    LOCKOUT_THRESHOLD,
    AccountLockedError,
    AuthService,
    PasswordlessAccountError,
)
from src.auth.totp import (
    consume_backup_code,
    generate_backup_codes,
    generate_totp_secret,
    hash_backup_codes,
    verify_totp,
)
from src.db.models import RefreshToken, User  # noqa: F401 — registers model with Base.metadata


# ---------------------------------------------------------------------------
# TOTP — pure function tests
# ---------------------------------------------------------------------------


class TestVerifyTotp:
    def test_valid_code_accepted(self) -> None:
        secret = generate_totp_secret()
        assert verify_totp(secret, pyotp.TOTP(secret).now()) is True

    def test_wrong_code_rejected(self) -> None:
        assert verify_totp(generate_totp_secret(), "000000") is False

    def test_non_numeric_rejected(self) -> None:
        assert verify_totp(generate_totp_secret(), "abcdef") is False

    def test_wrong_length_rejected(self) -> None:
        assert verify_totp(generate_totp_secret(), "12345") is False

    def test_empty_string_rejected(self) -> None:
        assert verify_totp(generate_totp_secret(), "") is False

    def test_whitespace_stripped_before_check(self) -> None:
        secret = generate_totp_secret()
        code = pyotp.TOTP(secret).now()
        assert verify_totp(secret, f"  {code}  ") is True


# ---------------------------------------------------------------------------
# Backup codes — pure function tests
# ---------------------------------------------------------------------------


class TestConsumeBackupCode:
    def _prepared(self, count: int = 3) -> tuple[list[str], list[dict]]:
        plain = generate_backup_codes(count)
        return plain, hash_backup_codes(plain)

    def test_valid_code_accepted(self) -> None:
        plain, stored = self._prepared()
        matched, _ = consume_backup_code(stored, plain[0])
        assert matched is True

    def test_valid_code_marked_used(self) -> None:
        plain, stored = self._prepared()
        _, updated = consume_backup_code(stored, plain[0])
        assert sum(1 for e in updated if e.get("used")) == 1

    def test_used_code_rejected_on_replay(self) -> None:
        plain, stored = self._prepared()
        _, after = consume_backup_code(stored, plain[0])
        matched, _ = consume_backup_code(after, plain[0])
        assert matched is False

    def test_wrong_code_rejected(self) -> None:
        _, stored = self._prepared()
        matched, _ = consume_backup_code(stored, "NOTACODE")
        assert matched is False

    def test_other_codes_still_valid_after_one_consumed(self) -> None:
        plain, stored = self._prepared(3)
        _, after = consume_backup_code(stored, plain[0])
        matched, _ = consume_backup_code(after, plain[1])
        assert matched is True

    def test_empty_list_returns_false(self) -> None:
        matched, _ = consume_backup_code([], "ANYCODE")
        assert matched is False

    def test_none_stored_returns_false(self) -> None:
        matched, returned = consume_backup_code(None, "ANYCODE")
        assert matched is False
        assert returned is None


# ---------------------------------------------------------------------------
# Lockout logic — requires DB
# ---------------------------------------------------------------------------


class TestLockoutLogic:
    async def _create_user(self, db_session, username: str = "alice") -> User:
        user = User(
            username=username,
            email=f"{username}@test.com",
            password_hash=hash_password("correct-pw"),
            is_active=True,
        )
        db_session.add(user)
        await db_session.flush()
        return user

    async def test_wrong_password_increments_counter(self, db_session) -> None:
        user = await self._create_user(db_session)
        result = await AuthService(db_session).authenticate_user(user.username, "wrong")
        assert result is None
        assert user.failed_login_attempts == 1

    async def test_lockout_raised_after_threshold_failures(self, db_session) -> None:
        user = await self._create_user(db_session)
        svc = AuthService(db_session)
        for _ in range(LOCKOUT_THRESHOLD - 1):
            await svc.authenticate_user(user.username, "wrong")
        with pytest.raises(AccountLockedError) as exc_info:
            await svc.authenticate_user(user.username, "wrong")
        assert exc_info.value.locked_until > datetime.now(timezone.utc)

    async def test_counter_resets_to_zero_on_lockout(self, db_session) -> None:
        user = await self._create_user(db_session)
        svc = AuthService(db_session)
        for _ in range(LOCKOUT_THRESHOLD):
            try:
                await svc.authenticate_user(user.username, "wrong")
            except AccountLockedError:
                pass
        assert user.failed_login_attempts == 0
        assert user.locked_until is not None

    async def test_locked_account_raises_even_on_correct_password(self, db_session) -> None:
        user = await self._create_user(db_session)
        svc = AuthService(db_session)
        for _ in range(LOCKOUT_THRESHOLD):
            try:
                await svc.authenticate_user(user.username, "wrong")
            except AccountLockedError:
                pass
        with pytest.raises(AccountLockedError):
            await svc.authenticate_user(user.username, "correct-pw")

    async def test_successful_login_resets_counter(self, db_session) -> None:
        user = await self._create_user(db_session)
        svc = AuthService(db_session)
        for _ in range(2):
            await svc.authenticate_user(user.username, "wrong")
        assert user.failed_login_attempts == 2
        result = await svc.authenticate_user(user.username, "correct-pw")
        assert result is not None
        assert user.failed_login_attempts == 0

    async def test_expired_lockout_clears_and_allows_auth(self, db_session) -> None:
        user = await self._create_user(db_session)
        user.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        user.failed_login_attempts = 0
        await db_session.flush()
        result = await AuthService(db_session).authenticate_user(user.username, "correct-pw")
        assert result is not None
        assert user.locked_until is None

    async def test_unknown_user_returns_none(self, db_session) -> None:
        result = await AuthService(db_session).authenticate_user("nobody", "pw")
        assert result is None

    async def test_inactive_user_returns_none(self, db_session) -> None:
        user = await self._create_user(db_session)
        user.is_active = False
        await db_session.flush()
        result = await AuthService(db_session).authenticate_user(user.username, "correct-pw")
        assert result is None

    async def test_passwordless_account_raises(self, db_session) -> None:
        user = User(
            username="sso_user",
            email="sso@test.com",
            password_hash=None,
            is_active=True,
        )
        db_session.add(user)
        await db_session.flush()
        with pytest.raises(PasswordlessAccountError):
            await AuthService(db_session).authenticate_user("sso_user", "anything")


# ---------------------------------------------------------------------------
# Refresh token rotation — requires DB
# ---------------------------------------------------------------------------


class TestRefreshTokenRotation:
    async def _create_user(self, db_session, username: str = "bob") -> User:
        user = User(
            username=username,
            email=f"{username}@test.com",
            password_hash=hash_password("pw"),
            is_active=True,
        )
        db_session.add(user)
        await db_session.flush()
        return user

    async def _store_refresh_token(self, db_session, user: User) -> str:
        token, expires_at = create_refresh_token(user.id)
        db_session.add(RefreshToken(
            token_hash=hash_token(token),
            user_id=user.id,
            expires_at=expires_at,
        ))
        await db_session.flush()
        return token

    async def test_valid_token_returns_new_pair_and_user(self, db_session) -> None:
        user = await self._create_user(db_session)
        old = await self._store_refresh_token(db_session, user)
        result = await AuthService(db_session).rotate_refresh_token(old)
        assert result is not None
        new_access, new_refresh, returned_user = result
        assert new_access
        assert new_refresh
        assert returned_user.id == user.id

    async def test_rotation_revokes_old_token(self, db_session) -> None:
        user = await self._create_user(db_session)
        old = await self._store_refresh_token(db_session, user)
        svc = AuthService(db_session)
        await svc.rotate_refresh_token(old)
        # Replaying the same token must return None
        result = await svc.rotate_refresh_token(old)
        assert result is None

    async def test_nonexistent_token_returns_none(self, db_session) -> None:
        fake, _ = create_refresh_token(999)
        result = await AuthService(db_session).rotate_refresh_token(fake)
        assert result is None

    async def test_expired_token_returns_none(self, db_session) -> None:
        user = await self._create_user(db_session)
        token, _ = create_refresh_token(user.id)
        db_session.add(RefreshToken(
            token_hash=hash_token(token),
            user_id=user.id,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ))
        await db_session.flush()
        result = await AuthService(db_session).rotate_refresh_token(token)
        assert result is None

    async def test_inactive_user_returns_none(self, db_session) -> None:
        user = await self._create_user(db_session)
        user.is_active = False
        old = await self._store_refresh_token(db_session, user)
        await db_session.flush()
        result = await AuthService(db_session).rotate_refresh_token(old)
        assert result is None
