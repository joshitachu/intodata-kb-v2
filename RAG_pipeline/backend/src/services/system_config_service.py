"""Company-level onboarding config management."""

from __future__ import annotations

import datetime
import io
import logging
import re
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.system_config import SystemConfig

logger = logging.getLogger(__name__)

VALID_STEPS = {"workspace", "platforms", "comm_tools", "source"}
VALID_PLATFORMS = {"talend", "workato", "lobster", "generic"}
WORKSPACE_NAME_MAX_LEN = 200


class SystemConfigService:
    """Manage the single-row company-level onboarding config."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, *, create: bool = True) -> SystemConfig | None:
        result = await self.db.execute(select(SystemConfig).limit(1))
        row = result.scalar_one_or_none()
        if row is not None:
            return row
        if not create:
            return None
        row = SystemConfig()
        self.db.add(row)
        await self.db.flush()
        return row

    async def save_step(self, step: str, data: dict[str, Any]) -> SystemConfig:
        if step not in VALID_STEPS:
            raise ValueError(f"Invalid step: {step}")

        row = await self.get()
        assert row is not None

        if step == "workspace":
            name = (data.get("workspace_name") or "").strip()
            if not name:
                raise ValueError("workspace_name is required")
            if len(name) > WORKSPACE_NAME_MAX_LEN:
                raise ValueError(f"workspace_name must be <= {WORKSPACE_NAME_MAX_LEN} characters")
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
            row.selected_comm_tools = data.get("comm_tools") or []
            row.wizard_current_step = max(row.wizard_current_step, 3)

        elif step == "source":
            source_type = data.get("source_type")
            if source_type not in (None, "github", "zip"):
                raise ValueError(f"Invalid source_type: {source_type}")
            row.source_type = source_type
            row.source_url = data.get("source_url")
            row.source_filename = data.get("source_filename")
            row.wizard_current_step = max(row.wizard_current_step, 4)

        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def complete_wizard(self, *, has_source: bool) -> SystemConfig:
        row = await self.get()
        assert row is not None
        row.company_wizard_completed = True
        row.company_wizard_completed_at = datetime.datetime.now(datetime.timezone.utc)
        row.wizard_current_step = 5
        row.setup_status = "processing" if has_source else "skipped"
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def mark_setup_completed(self) -> None:
        row = await self.get(create=False)
        if row is None:
            return
        row.setup_status = "completed"
        row.setup_error = None
        await self.db.commit()

    async def mark_setup_failed(self, error: str) -> None:
        row = await self.get(create=False)
        if row is None:
            return
        row.setup_status = "failed"
        row.setup_error = error[:4000]
        await self.db.commit()

    async def get_model_config(self) -> tuple[str | None, str | None]:
        """Return the persisted (llm_provider, llm_model) override, or (None, None)."""
        row = await self.get(create=False)
        if row is None:
            return None, None
        return row.llm_provider, row.llm_model

    async def set_model_config(self, provider: str, model: str) -> SystemConfig:
        """Persist the superadmin LLM answer-model standard and refresh the
        in-process override. Validates against the curated allow-list."""
        from src.generation import model_config

        if not model_config.is_allowed(provider, model):
            raise ValueError(f"Model not allowed: {provider}/{model}")

        row = await self.get()
        assert row is not None
        row.llm_provider = provider
        row.llm_model = model
        await self.db.commit()
        await self.db.refresh(row)
        model_config.set_override(provider, model)
        return row

    async def reset(self) -> None:
        """Wipe company onboarding state so the admin wizard shows again."""
        row = await self.get(create=False)
        if row is not None:
            await self.db.delete(row)
            await self.db.commit()


async def run_company_background_setup(session_factory) -> None:
    """Background processor for company-level onboarding source setup.

    ZIP sources: ProvisioningStep (frontend) already ran the MCP pipeline before
    the wizard completed, so we just mark setup complete.

    GitHub sources: download the repo archive, extract supported text files, and
    ingest them into RAG directly.
    """
    try:
        async with session_factory() as db:
            row = await SystemConfigService(db).get(create=False)
            if row is None:
                return

            svc = SystemConfigService(db)
            source_type = row.source_type

            if source_type == "github":
                from src.services.onboarding_service import (
                    _derive_project_id,
                    _ingest_github_source,
                )
                project_id = _derive_project_id(row.workspace_name, None)
                await _ingest_github_source(
                    source_url=row.source_url or "",
                    project_id=project_id,
                    user_id=0,
                    db=db,
                )
            # ZIP: ProvisioningStep handled it. None/unknown: nothing to ingest.

            await svc.mark_setup_completed()
    except Exception as e:
        logger.exception("Background company setup failed")
        async with session_factory() as db:
            await SystemConfigService(db).mark_setup_failed(str(e))
