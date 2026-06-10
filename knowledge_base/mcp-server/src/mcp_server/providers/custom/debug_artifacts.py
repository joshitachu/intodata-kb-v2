"""Gated persistence for human-only pipeline forensics.

All artifacts that exist purely for offline debugging (model-call snapshots,
agent-task traces, advisory reports) are written through this module. By
default nothing is written; setting ``MCP_PIPELINE_DEBUG=1`` materializes the
full forensic record under ``docs_staging/_debug/``. Artifacts with a runtime
consumer (resume, gates, REST API, RAG push) must never go through here.
"""

from __future__ import annotations

from pathlib import Path

from mcp_server.config import config

DEBUG_DIR_NAME = "_debug"


def pipeline_debug_enabled() -> bool:
    return config.pipeline_debug


def debug_dir(staging_dir: Path, *parts: str) -> Path:
    """Path under the staging _debug root; not created until a write happens."""

    return staging_dir.joinpath(DEBUG_DIR_NAME, *parts)


def debug_write(path: Path, content: str) -> None:
    """Atomically write a debug artifact; no-op unless MCP_PIPELINE_DEBUG is set."""

    if not config.pipeline_debug:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
