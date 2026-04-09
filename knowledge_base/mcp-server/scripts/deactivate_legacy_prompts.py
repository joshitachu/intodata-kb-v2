#!/usr/bin/env python3
"""Deactivate legacy p01-p22 individual analysis prompts.

These are superseded by the consolidated ANALYSIS_GUIDE templates.
Sets is_active=false so they no longer appear in the dashboard or API.

Usage:
    cd knowledge_base/mcp-server
    python -m scripts.deactivate_legacy_prompts
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select, update  # noqa: E402

from mcp_server.db.session import async_session_factory  # noqa: E402
from mcp_server.db.template_models import Template  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Matches slugs like analysis/talend_p01_inventory, analysis/workato_p22_preventive
_LEGACY_PATTERN = re.compile(r"^analysis/\w+_p\d{2}_")


async def deactivate() -> None:
    """Set is_active=false for all legacy p01-p22 analysis templates."""
    async with async_session_factory() as db:
        # Find all matching templates
        result = await db.execute(
            select(Template).where(
                Template.category == "analysis",
                Template.is_active.is_(True),
            )
        )
        templates = list(result.scalars().all())

        deactivated = 0
        for t in templates:
            if _LEGACY_PATTERN.match(t.slug):
                t.is_active = False
                logger.info("  DEACTIVATE: %s", t.slug)
                deactivated += 1
            else:
                logger.info("  KEEP: %s", t.slug)

        await db.commit()

    logger.info(
        "\nDone: %d templates deactivated, %d kept active",
        deactivated,
        len(templates) - deactivated,
    )


if __name__ == "__main__":
    asyncio.run(deactivate())
