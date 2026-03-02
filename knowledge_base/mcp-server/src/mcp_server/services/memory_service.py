"""Hindsight memory service for the knowledge_base doc-generation agent.

Two layers:
  Layer A  - Process memory (shared): bank_id = "agent-process"
  Layer B  - Per-project memory:      bank_id = "project-{project_id}"
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

try:
    from hindsight_client import Hindsight
except ImportError:
    Hindsight = None

from mcp_server.config import config

logger = logging.getLogger(__name__)

PROCESS_BANK = "agent-process"

_client: Hindsight | None = None


def _get_client() -> Hindsight | None:
    global _client
    if Hindsight is None:
        return None
    if _client is None:
        _client = Hindsight(base_url=config.hindsight_url, timeout=10.0)
    return _client


def _project_bank(project_id: str) -> str:
    return f"project-{project_id}"


# ---------------------------------------------------------------------------
# Bank lifecycle
# ---------------------------------------------------------------------------

async def ensure_process_bank() -> None:
    """Create the shared process-memory bank (idempotent)."""
    if not config.hindsight_enabled:
        return
    try:
        client = _get_client()
        await client.acreate_bank(
            bank_id=PROCESS_BANK,
            name="Doc Generation Process Memory",
            mission=(
                "Remember how to generate better documentation. Track errors, "
                "optimizations, workflow lessons, and best practices discovered "
                "during documentation generation runs."
            ),
        )
    except Exception:
        logger.debug("ensure_process_bank: bank may already exist")


async def ensure_project_bank(project_id: str) -> None:
    """Create a per-project memory bank (idempotent)."""
    if not config.hindsight_enabled:
        return
    try:
        client = _get_client()
        await client.acreate_bank(
            bank_id=_project_bank(project_id),
            name=f"Memory for project {project_id}",
            mission=(
                f"Remember analysis findings, risk flags, health scores, group structure, "
                f"and documentation decisions for the '{project_id}' project. Track changes "
                f"between scans so re-scans can focus on what changed."
            ),
        )
    except Exception:
        logger.debug("ensure_project_bank: bank may already exist for %s", project_id)


# ---------------------------------------------------------------------------
# Layer A — Process memory (shared across projects)
# ---------------------------------------------------------------------------

async def recall_process_memory(query: str) -> list[str]:
    """Recall process-level lessons and best practices.

    Returns list of fact strings, empty on failure or if disabled.
    """
    if not config.hindsight_enabled:
        return []
    try:
        client = _get_client()
        response = await client.arecall(
            bank_id=PROCESS_BANK,
            query=query,
            max_tokens=2048,
            budget="mid",
        )
        return [r.text for r in response.results]
    except Exception:
        logger.warning("Hindsight recall (process) failed", exc_info=True)
        return []


async def retain_process_lesson(lesson: str, context: str = "process-lesson") -> None:
    """Store an operational lesson learned during doc generation."""
    if not config.hindsight_enabled:
        return
    try:
        client = _get_client()
        await client.aretain(
            bank_id=PROCESS_BANK,
            content=lesson,
            context=context,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
    except Exception:
        logger.warning("Hindsight retain (process) failed", exc_info=True)


# ---------------------------------------------------------------------------
# Layer B — Per-project memory
# ---------------------------------------------------------------------------

async def recall_project_memory(project_id: str, query: str) -> list[str]:
    """Recall prior findings for a specific project.

    Returns list of fact strings, empty on failure or if disabled.
    """
    if not config.hindsight_enabled:
        return []
    try:
        client = _get_client()
        response = await client.arecall(
            bank_id=_project_bank(project_id),
            query=query,
            max_tokens=2048,
            budget="mid",
        )
        return [r.text for r in response.results]
    except Exception:
        logger.warning(
            "Hindsight recall (project %s) failed", project_id, exc_info=True
        )
        return []


async def retain_project_findings(
    project_id: str,
    findings: str,
    context: str = "analysis-findings",
    document_id: str | None = None,
) -> None:
    """Store analysis findings for a project (group summaries, run results, etc.)."""
    if not config.hindsight_enabled:
        return
    try:
        client = _get_client()
        await client.aretain(
            bank_id=_project_bank(project_id),
            content=findings,
            context=context,
            timestamp=datetime.now(timezone.utc).isoformat(),
            document_id=document_id,
        )
    except Exception:
        logger.warning(
            "Hindsight retain (project %s) failed", project_id, exc_info=True
        )
