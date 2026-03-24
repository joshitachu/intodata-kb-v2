#!/usr/bin/env python3
"""Seed templates from filesystem into the database.

Idempotent: skips templates that already exist (by slug).
Creates v1 for each template with the filesystem content.

Usage:
    cd knowledge_base/mcp-server
    python -m scripts.seed_templates
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Ensure the mcp_server package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mcp_server.db.session import async_session_factory  # noqa: E402
from mcp_server.services.template_service import TemplateService  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Resolve paths
_SCRIPT_DIR = Path(__file__).resolve().parent
_MCP_SERVER_DIR = _SCRIPT_DIR.parent
_KB_DIR = _MCP_SERVER_DIR.parent
_INTODOC_DIR = _KB_DIR / "intodoc" / "src" / "intodoc"
_TEMPLATES_DIR = _MCP_SERVER_DIR / "src" / "mcp_server" / "templates"


def _find_platform_prompts(platform: str) -> list[tuple[str, str, Path]]:
    """Find prompt files for a platform. Returns (slug, display_name, path)."""
    prompts_dir = _INTODOC_DIR / "platforms" / platform / "prompts"
    if not prompts_dir.exists():
        return []

    results = []
    for f in sorted(prompts_dir.iterdir()):
        if not f.is_file():
            continue
        name = f.stem  # e.g. "ANALYSIS_GUIDE", "p01_inventory"

        if name == "ANALYSIS_GUIDE":
            slug = f"analysis/{platform}_guide"
            display = f"Analysis Guide ({platform.title()})"
            results.append((slug, display, f))
        elif name == "PHASE_3_GUIDE":
            slug = f"orchestration/{platform}_phase3_guide"
            display = f"Phase 3 Guide ({platform.title()})"
            results.append((slug, display, f))
        elif name.startswith("p"):
            # Legacy prompt: p01_inventory.txt -> analysis/p01_inventory
            slug = f"analysis/{platform}_{name}"
            display = f"{name.replace('_', ' ').title()} ({platform.title()})"
            results.append((slug, display, f))

    return results


def _find_reference_docs() -> list[tuple[str, str, Path]]:
    """Find reference docs. Returns (slug, display_name, path)."""
    ref_dir = _KB_DIR / "intodoc" / "reference-docs"
    if not ref_dir.exists():
        return []

    results = []
    for f in sorted(ref_dir.glob("*.md")):
        name = f.stem  # e.g. "00_EXECUTIVE_SUMMARY"
        slug = f"output/{name.lower()}"
        display = name.replace("_", " ").title()
        results.append((slug, display, f))

    return results


def _find_scoring_templates() -> list[tuple[str, str, Path]]:
    """Find scoring/assessment templates. Returns (slug, display_name, path)."""
    if not _TEMPLATES_DIR.exists():
        return []

    mapping = {
        "scoring-rubric.md": ("assessment/scoring_rubric", "Scoring Rubric"),
        "anti-patterns.md": ("assessment/anti_patterns", "Anti-Patterns Library"),
        "red-flags.md": ("assessment/red_flags", "Red Flags Library"),
        "security-checklist.md": ("assessment/security_checklist", "Security Checklist"),
        "pattern-library.md": ("pattern/pattern_library", "Pattern Library"),
    }

    results = []
    for filename, (slug, display) in mapping.items():
        f = _TEMPLATES_DIR / filename
        if f.exists():
            results.append((slug, display, f))

    return results


def _slug_to_category(slug: str) -> str:
    """Extract category from slug (e.g. 'analysis/foo' -> 'analysis')."""
    return slug.split("/")[0] if "/" in slug else "other"


def _slug_to_platform(slug: str) -> str | None:
    """Extract platform from slug if applicable."""
    parts = slug.split("/")
    if len(parts) >= 2:
        name = parts[1]
        for platform in ("talend", "workato", "generic"):
            if name.startswith(f"{platform}_"):
                return platform
    return None


async def seed() -> None:
    """Seed all templates into the database."""
    # Collect all templates to seed
    templates: list[tuple[str, str, Path]] = []

    # Platform prompts
    for platform in ("talend", "workato", "_shared"):
        found = _find_platform_prompts(platform)
        templates.extend(found)
        logger.info("Found %d prompt files for %s", len(found), platform)

    # Reference docs
    ref_docs = _find_reference_docs()
    templates.extend(ref_docs)
    logger.info("Found %d reference docs", len(ref_docs))

    # Scoring templates
    scoring = _find_scoring_templates()
    templates.extend(scoring)
    logger.info("Found %d scoring templates", len(scoring))

    logger.info("Total: %d templates to seed", len(templates))

    async with async_session_factory() as db:
        svc = TemplateService(db)
        created = 0
        skipped = 0

        for slug, display_name, file_path in templates:
            existing = await svc.get_template(slug)
            if existing:
                logger.info("  SKIP: %s (already exists)", slug)
                skipped += 1
                continue

            content = file_path.read_text(encoding="utf-8")
            category = _slug_to_category(slug)
            platform = _slug_to_platform(slug)

            # Create template
            template = await svc.create_template(
                slug=slug,
                category=category,
                display_name=display_name,
                platform=platform,
                description=f"Seeded from {file_path.name}",
            )

            # Create v1
            await svc.create_version(
                slug,
                content=content,
                change_summary=f"Initial seed from filesystem ({file_path.name})",
                created_by="seed_script",
            )

            logger.info("  CREATE: %s (v1, %d bytes)", slug, len(content))
            created += 1

        await db.commit()

    logger.info("\nSeed complete: %d created, %d skipped", created, skipped)


if __name__ == "__main__":
    asyncio.run(seed())
