"""Log manager — file writer + SSE broadcast via asyncio queues."""

from __future__ import annotations

import asyncio
import datetime
import logging
from pathlib import Path

from mcp_server.config import config

logger = logging.getLogger(__name__)


class LogManager:
    """Manages log streams for projects.

    - Writes log lines to data/logs/{project_id}_{timestamp}.log
    - Broadcasts to asyncio.Queue subscribers for SSE streaming
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._log_files: dict[str, Path] = {}

    def _ensure_log_file(self, project_id: str) -> Path:
        """Get or create the current log file for a project."""
        if project_id not in self._log_files:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = config.logs_dir / f"{project_id}_{ts}.log"
            self._log_files[project_id] = log_path
        return self._log_files[project_id]

    async def emit(
        self,
        project_id: str,
        message: str,
        level: str = "info",
    ) -> None:
        """Emit a log line: write to file and broadcast to subscribers."""
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        tag = level.upper()
        line = f"[{ts}] [{tag}] {message}"

        # Write to file
        log_path = self._ensure_log_file(project_id)
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:
            logger.error(f"Failed to write log: {e}")

        # Broadcast to subscribers
        if project_id in self._subscribers:
            dead: list[asyncio.Queue] = []
            for queue in self._subscribers[project_id]:
                try:
                    queue.put_nowait({"project_id": project_id, "level": level, "message": line})
                except asyncio.QueueFull:
                    dead.append(queue)
            for q in dead:
                self._subscribers[project_id].remove(q)

    def subscribe(self, project_id: str) -> asyncio.Queue:
        """Subscribe to log events for a project. Returns an asyncio.Queue."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.setdefault(project_id, []).append(queue)
        return queue

    def unsubscribe(self, project_id: str, queue: asyncio.Queue) -> None:
        """Remove a subscriber."""
        if project_id in self._subscribers:
            try:
                self._subscribers[project_id].remove(queue)
            except ValueError:
                pass
            if not self._subscribers[project_id]:
                del self._subscribers[project_id]

    async def emit_done(self, project_id: str) -> None:
        """Broadcast a 'done' event so SSE subscribers know generation finished."""
        log_path = self._log_files.get(project_id)
        log_file = log_path.name if log_path else None
        event = {
            "project_id": project_id,
            "level": "done",
            "message": "Generation complete",
            "log_file": log_file,
        }
        if project_id in self._subscribers:
            dead: list[asyncio.Queue] = []
            for queue in self._subscribers[project_id]:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    dead.append(queue)
            for q in dead:
                self._subscribers[project_id].remove(q)

    def reset_log_file(self, project_id: str) -> None:
        """Start a fresh log file for the next operation."""
        self._log_files.pop(project_id, None)

    def get_current_log_path(self, project_id: str) -> Path | None:
        return self._log_files.get(project_id)


# Separate instance, keyed by experiment_id instead of project_id -- Grounded
# (OpenCode / OpenAI Agents) runs are not tied to a single project the way
# legacy generation is, and importing this module directly (rather than
# threading `request.app.state`) keeps the application layer decoupled from
# the web framework.
experiment_log_manager = LogManager()
