"""Claude service — spawns Claude Code CLI for doc generation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import traceback

from mcp_server.config import config
from mcp_server.services.log_manager import LogManager

logger = logging.getLogger(__name__)


class ClaudeService:
    """Spawns `claude -p "..."` to generate documentation via Claude Code."""

    def __init__(self, log_manager: LogManager) -> None:
        self.log_manager = log_manager

    @staticmethod
    def _format_event(event: dict) -> str | None:
        """Turn a stream-json event into a human-readable log line.

        Returns None for events we want to suppress (e.g. large tool results).
        """
        etype = event.get("type", "")

        # --- Assistant text ---
        if etype == "assistant":
            message = event.get("message", {})
            # message.content is a list of content blocks
            parts = []
            for block in message.get("content", []):
                if block.get("type") == "text":
                    text = block.get("text", "").strip()
                    if text:
                        # Truncate very long text to keep logs readable
                        parts.append(text[:500])
                elif block.get("type") == "tool_use":
                    name = block.get("name", "?")
                    inp = block.get("input", {})
                    # Show tool name + key params
                    summary = ", ".join(
                        f"{k}={repr(v)[:80]}" for k, v in list(inp.items())[:4]
                    )
                    parts.append(f"[tool_call] {name}({summary})")
            return " | ".join(parts) if parts else None

        # --- Tool result (keep short) ---
        if etype == "tool_result" or etype == "content_block_stop":
            return None  # suppress noisy tool output

        # --- Result / final output ---
        if etype == "result":
            text = event.get("result", "")
            if text:
                return f"[result] {text[:300]}"
            return "[result] (empty)"

        # --- System / init messages ---
        if etype == "system":
            return f"[system] {event.get('message', '')[:200]}"

        # --- Catch-all for unknown types ---
        if etype:
            return f"[{etype}] {json.dumps(event)[:200]}"

        return None

    async def generate_docs(self, project_id: str) -> dict:
        """Spawn Claude Code CLI to generate docs for a project.

        Returns dict with success, cost_usd, duration_ms, input_tokens, etc.
        """
        output_dir = config.project_root / "output" / project_id
        output_dir.mkdir(parents=True, exist_ok=True)

        prompt = (
            f"Generate complete documentation for project '{project_id}'. "
            f"Follow the Documentation Agent workflow in CLAUDE.md exactly. "
            f"Phase 1: Start by calling recall_process_memory() and "
            f"recall_project_memory('{project_id}') to load prior lessons and findings. "
            f"Then call get_scan_status, get_chunks. Group chunks by their 'group' field. "
            f"Call fetch_json for manifest and connections. "
            f"Phase 2: Spawn one sub-agent per group (in parallel). Each sub-agent calls "
            f"fetch_chunk for every chunk in its group, reads the intodoc prompt files, "
            f"analyzes all dimensions, and returns a compact text summary -- no files written. "
            f"Phase 3: Spawn doc-writing sub-agents in parallel by theme. Each reads the "
            f"matching reference doc from intodoc/reference-docs/ for format, then writes "
            f"final .md files directly to output/{project_id}/docs/. "
            f"Phase 4: Write index.json and run log, then call insert_to_rag. "
            f"Call retain_findings('{project_id}', summary, 'run-summary') with a summary of "
            f"docs generated, health scores, and risk flags. If any errors occurred, call "
            f"retain_process_lesson(lesson, 'error-lesson') to remember what went wrong. "
            f"IMPORTANT: Do NOT write any intermediate files or _scratch/ directories. "
            f"Only final .md documents go to output/{project_id}/docs/."
        )

        # Resolve full path so .CMD/.BAT scripts work on Windows
        # (subprocess.Popen without shell=True only finds .exe by bare name)
        resolved_cmd = shutil.which(config.claude_command)
        if not resolved_cmd:
            msg = f"Claude CLI not found on PATH: {config.claude_command}"
            logger.error(msg)
            await self.log_manager.emit(project_id, msg, level="error")
            return False

        cmd = [
            resolved_cmd,
            "-p", prompt,
            "--dangerously-skip-permissions",
            "--output-format", "stream-json",
            "--verbose",
        ]

        # Diagnostic logging
        logger.info(f"claude resolved path: {resolved_cmd}")
        logger.info(f"Spawning Claude Code: {cmd[0]} -p '...' --dangerously-skip-permissions  cwd={config.project_root}")
        await self.log_manager.emit(
            project_id,
            f"claude resolved: {resolved_cmd} | Python {sys.version}",
            level="info",
        )
        await self.log_manager.emit(
            project_id,
            "Starting doc generation via Claude Code...",
            level="info",
        )

        try:
            # Use subprocess.Popen instead of asyncio.create_subprocess_exec
            # to avoid ProactorEventLoop pipe issues on Windows + Python 3.14.
            env = {**os.environ, "PYTHONUTF8": "1"}
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                env=env,
                cwd=str(config.project_root),
            )

            async def _read_stderr(stream) -> list[str]:
                """Read stderr lines, emit each to logs."""
                lines: list[str] = []
                while True:
                    line = await asyncio.to_thread(stream.readline)
                    if not line:
                        break
                    stripped = line.rstrip()
                    if stripped:
                        lines.append(stripped)
                        await self.log_manager.emit(
                            project_id, f"[stderr] {stripped}", level="warning",
                        )
                return lines

            async def _read_stdout_json(stream) -> tuple[list[str], dict | None]:
                """Parse stream-json events from stdout into readable logs.

                Returns (lines, result_event) where result_event is the final
                'result' event from Claude Code containing usage data.
                """
                lines: list[str] = []
                result_event: dict | None = None
                while True:
                    line = await asyncio.to_thread(stream.readline)
                    if not line:
                        break
                    stripped = line.rstrip()
                    if not stripped:
                        continue
                    lines.append(stripped)

                    try:
                        event = json.loads(stripped)
                    except json.JSONDecodeError:
                        # Not JSON — emit raw line
                        await self.log_manager.emit(
                            project_id, stripped, level="info",
                        )
                        continue

                    if event.get("type") == "result":
                        result_event = event

                    msg = self._format_event(event)
                    if msg:
                        await self.log_manager.emit(
                            project_id, msg, level="info",
                        )
                return lines, result_event

            # Read both streams concurrently
            stdout_result, stderr_lines = await asyncio.gather(
                _read_stdout_json(proc.stdout),
                _read_stderr(proc.stderr),
            )
            stdout_lines, result_event = stdout_result

            returncode = await asyncio.to_thread(proc.wait)

            # Extract usage from the result event
            usage: dict = {"success": returncode == 0}
            if result_event:
                usage["session_id"] = result_event.get("session_id")
                usage["num_turns"] = result_event.get("num_turns")
                usage["duration_ms"] = result_event.get("duration_ms")
                usage["duration_api_ms"] = result_event.get("duration_api_ms")
                usage["cost_usd"] = result_event.get("cost_usd")
                usage["input_tokens"] = result_event.get("total_input_tokens")
                usage["output_tokens"] = result_event.get("total_output_tokens")

            if returncode == 0:
                await self.log_manager.emit(
                    project_id,
                    "Doc generation completed successfully",
                    level="info",
                )
            else:
                await self.log_manager.emit(
                    project_id,
                    f"Doc generation failed (exit code {returncode})",
                    level="error",
                )

            return usage

        except FileNotFoundError:
            msg = f"Claude CLI not found: {config.claude_command} (resolved: {resolved_cmd})"
            logger.error(msg)
            await self.log_manager.emit(project_id, msg, level="error")
            return {"success": False}
        except Exception as e:
            err_type = type(e).__name__
            err_repr = repr(e)
            err_tb = traceback.format_exc()
            msg = f"Claude service error [{err_type}]: {err_repr}"
            logger.error(f"{msg}\n{err_tb}")
            await self.log_manager.emit(project_id, msg, level="error")
            return {"success": False}
