"""Onboarding state management + background setup orchestration."""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Notification, User, UserOnboarding

logger = logging.getLogger(__name__)


VALID_STEPS = {"workspace", "platforms", "comm_tools", "source", "tour"}
VALID_PLATFORMS = {"talend", "workato", "lobster", "generic"}
VALID_COMM_TOOLS = {"slack", "teams", "jira", "confluence"}
WORKSPACE_NAME_MAX_LEN = 200


class OnboardingService:
    """Manage per-user onboarding state with lazy row creation."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_or_create(self, user: User, *, create: bool = True) -> UserOnboarding | None:
        """Return the user's onboarding row, creating a fresh one on first touch.

        When `create=False`, returns None if no row exists — used to distinguish
        "existing user (no row)" from "new user (row with wizard_completed=False)".
        """
        result = await self.db.execute(
            select(UserOnboarding).where(UserOnboarding.user_id == user.id)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            return row
        if not create:
            return None

        row = UserOnboarding(user_id=user.id)
        self.db.add(row)
        await self.db.flush()
        return row

    async def save_step(self, user: User, step: str, data: dict[str, Any]) -> UserOnboarding:
        """Partial save for a wizard step."""
        if step not in VALID_STEPS:
            raise ValueError(f"Invalid step: {step}")

        row = await self.get_or_create(user)
        assert row is not None

        if step == "workspace":
            name = (data.get("workspace_name") or "").strip()
            if not name:
                raise ValueError("workspace_name is required")
            if len(name) > WORKSPACE_NAME_MAX_LEN:
                raise ValueError(
                    f"workspace_name must be <= {WORKSPACE_NAME_MAX_LEN} characters"
                )
            row.workspace_name = name
            row.wizard_current_step = max(row.wizard_current_step, 1)

        elif step == "platforms":
            platforms = data.get("platforms") or []
            for p in platforms:
                if p not in VALID_PLATFORMS:
                    raise ValueError(f"Unknown platform: {p}")
            row.selected_platforms = platforms
            row.wizard_current_step = max(row.wizard_current_step, 2)

        elif step == "comm_tools":
            tools = data.get("comm_tools") or []
            # We accept any comm_tool id from the config endpoint (no hard whitelist
            # here so the admin can add new tools via config). Known "coming soon"
            # tools are still recorded as user preference.
            row.selected_comm_tools = tools
            row.wizard_current_step = max(row.wizard_current_step, 3)

        elif step == "source":
            source_type = data.get("source_type")
            if source_type not in (None, "github", "zip"):
                raise ValueError(f"Invalid source_type: {source_type}")
            row.source_type = source_type
            row.source_url = data.get("source_url")
            row.source_filename = data.get("source_filename")
            row.wizard_current_step = max(row.wizard_current_step, 4)

        elif step == "tour":
            row.tour_current_step = int(data.get("tour_step", 0))

        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def complete_wizard(self, user: User, *, has_source: bool) -> UserOnboarding:
        row = await self.get_or_create(user)
        assert row is not None
        row.wizard_completed = True
        row.wizard_completed_at = datetime.datetime.now(datetime.timezone.utc)
        # 6-step wizard: 0-welcome, 1-workspace, 2-platforms, 3-tools, 4-source, 5-provisioning
        row.wizard_current_step = 5
        row.setup_status = "processing" if has_source else "skipped"
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def complete_tour(self, user: User) -> UserOnboarding:
        row = await self.get_or_create(user)
        assert row is not None
        row.tour_completed = True
        row.tour_completed_at = datetime.datetime.now(datetime.timezone.utc)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def reset(self, user: User) -> None:
        """Admin-only: wipe a user's onboarding state so the wizard shows again."""
        row = await self.get_or_create(user, create=False)
        if row is not None:
            await self.db.delete(row)
            await self.db.commit()

    async def mark_setup_failed(self, user_id: int, error: str) -> None:
        result = await self.db.execute(
            select(UserOnboarding).where(UserOnboarding.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.setup_status = "failed"
        row.setup_error = error[:4000]
        await self.db.commit()

    async def mark_setup_completed(self, user_id: int) -> None:
        result = await self.db.execute(
            select(UserOnboarding).where(UserOnboarding.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.setup_status = "completed"
        row.setup_error = None
        await self.db.commit()


async def run_background_setup(user_id: int, session_factory) -> None:
    """Stub background processor for uploaded sources.

    TODO: wire up to real ingest pipeline (clone repo / extract ZIP, run scan,
    ingest docs). For now just simulates work then marks complete + notifies.
    """
    from src.services.notification_service import NotificationService

    await asyncio.sleep(3)  # simulate work — replace with real pipeline

    async with session_factory() as db:
        try:
            svc = OnboardingService(db)
            await svc.mark_setup_completed(user_id)
            notif = NotificationService(db)
            await notif.create(
                user_id=user_id,
                type="setup_complete",
                title="Workspace setup complete",
                message="Your source has been ingested and is ready to query.",
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("Background onboarding setup failed for user %s", user_id)
            svc = OnboardingService(db)
            await svc.mark_setup_failed(user_id, str(e))
