from __future__ import annotations

import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, JSON, String, Table, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

# Many-to-many association table: User <-> Role
user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)

# Many-to-many association table: Role <-> Permission
role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    last_login_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # TOTP / MFA. `totp_secret` is the base32-encoded pyotp secret, held in
    # plaintext because verification needs it — same threat model as
    # `password_hash`: DB compromise = game over. `backup_codes_hashed` is a
    # JSON list of `{"hash": <bcrypt>, "used": <bool>}` so each code can be
    # invalidated after a single use without rotating the whole set.
    mfa_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    backup_codes_hashed: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )
    # Replay protection: track the last accepted TOTP code so the same code
    # cannot be used twice within the same 90-second validity window.
    totp_last_code: Mapped[str | None] = mapped_column(String(6), nullable=True)
    totp_last_code_used_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Account lockout: after LOCKOUT_THRESHOLD consecutive bad passwords the
    # account is locked for LOCKOUT_DURATION_MINUTES. Counter resets on success
    # or after the lockout window expires.
    failed_login_attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    locked_until: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # SSO / OIDC — set when the user authenticates via an external IdP.
    # sso_provider identifies the IdP (e.g. "google", "azure", "okta").
    # sso_subject is the IdP's stable user identifier (the `sub` claim).
    # A user can have both a password_hash (for local login) and SSO fields
    # (for federated login) — they are not mutually exclusive.
    sso_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sso_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationships
    roles: Mapped[list[Role]] = relationship(
        secondary=user_roles, back_populates="users", lazy="selectin"
    )
    documents: Mapped[list["Document"]] = relationship(back_populates="uploaded_by_user")
    query_logs: Mapped[list["QueryLog"]] = relationship(back_populates="user")
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Set only when this token was superseded by rotation. Stays NULL for tokens
    # revoked on logout, password change, or admin action -- those must never be
    # accepted again, while a just-rotated token is honoured inside the grace
    # window so concurrent refreshes don't log the user out. See AuthService.
    rotated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(500))

    # Relationships
    users: Mapped[list[User]] = relationship(
        secondary=user_roles, back_populates="roles", lazy="selectin"
    )
    permissions: Mapped[list[Permission]] = relationship(
        secondary=role_permissions, back_populates="roles", lazy="selectin"
    )


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    codename: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(500))

    # Relationships
    roles: Mapped[list[Role]] = relationship(
        secondary=role_permissions, back_populates="permissions", lazy="selectin"
    )


class ProjectAssignment(Base):
    __tablename__ = "project_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(String(255), index=True)
    # True when this user owns the project (created it). Owners can manage the
    # project and grant/revoke access for other users; members (is_owner=False)
    # can only view/query it. Superadmins bypass ownership entirely.
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    assigned_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    user: Mapped[User] = relationship(foreign_keys=[user_id])
    assigner: Mapped[User | None] = relationship(foreign_keys=[assigned_by])


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(nullable=True)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
