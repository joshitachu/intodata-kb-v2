"""Deep Dive service — spawns Claude Code CLI for on-the-fly code analysis."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
import traceback
import uuid
from collections.abc import AsyncGenerator

from mcp_server.config import config

logger = logging.getLogger(__name__)

# Q&A prompt template for deep dive analysis
_QA_PROMPT = """You are a code analyst for project '{project_id}'. Answer this question by reading the scan data:

**Question:** {question}

Use MCP tools strategically:
1. get_scan_status('{project_id}') — verify data exists
2. get_chunks('{project_id}') — see project structure (groups, jobs)
3. fetch_json('{project_id}', 'manifest') — job relationships
4. fetch_json('{project_id}', 'connections') — database/API connections
5. fetch_chunk('{project_id}', chunk_id) — specific job details

Rules:
- Read actual scan data before answering. Don't guess.
- Only fetch what you need — don't load all chunks unless required.
- Reference actual job names, components, and connections.
- Use markdown formatting.
- If the data doesn't answer the question, say so.
- When you produce significant findings (bugs, architectural insights, data flow summaries), \
call insert_to_rag(project_id='{project_id}', docs=[{{"filename": "deep_dive_{project_id}_<topic>.md", "content": "<your findings as markdown>"}}]) \
to persist them in the knowledge base for future queries.
- Keep inserted docs focused and factual — they become permanent knowledge."""


class DeepDiveService:
    """Spawns `claude -p "..."` to analyze scan data on-the-fly via Claude Code."""

    def __init__(self) -> None:
        # Track whether text was streamed via content_block_delta.
        # If deltas arrive, we skip the assistant-event text (it's a duplicate).
        # If deltas never arrive, assistant-event text is the fallback.
        self._text_streamed_via_delta: bool = False

    async def query(
        self, project_id: str, question: str, session_id: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Stream deep-dive analysis results as SSE event dicts.

        Yields dicts with keys: type, data
        Event types: status, tool_use, token, thinking, thinking_start, session, done, error
        """
        start_time = time.monotonic()
        self._text_streamed_via_delta = False  # Reset per-query

        prompt = _QA_PROMPT.format(project_id=project_id, question=question)

        # Resolve CLI path (handles .cmd/.bat on Windows)
        resolved_cmd = shutil.which(config.claude_command)
        if not resolved_cmd:
            yield {"type": "error", "data": f"Claude CLI not found: {config.claude_command}"}
            return

        # Session management for multi-turn conversations
        is_continuation = session_id is not None
        if not session_id:
            session_id = str(uuid.uuid4())

        # Emit session ID so the frontend can track it for follow-ups
        yield {"type": "session", "data": session_id}

        cmd = [
            resolved_cmd,
            "-p", prompt,
            "--dangerously-skip-permissions",
            "--output-format", "stream-json",
            "--verbose",
        ]

        # Only use session flags for multi-turn (chat mode follow-ups)
        if is_continuation:
            cmd.extend(["--session-id", session_id, "--continue"])

        logger.info(f"Deep dive: spawning Claude Code for project={project_id}")
        yield {"type": "status", "data": f"Analyzing project {project_id}..."}

        proc: subprocess.Popen | None = None
        try:
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

            # Drain stderr concurrently to prevent pipe buffer deadlock
            async def _drain_stderr(stream) -> str:
                lines: list[str] = []
                while True:
                    line = await asyncio.to_thread(stream.readline)
                    if not line:
                        break
                    stripped = line.rstrip()
                    if stripped:
                        lines.append(stripped)
                        logger.debug(f"[deep-dive stderr] {stripped[:200]}")
                return "\n".join(lines)

            assert proc.stderr is not None
            stderr_task = asyncio.create_task(_drain_stderr(proc.stderr))

            # Read stdout line by line, parse stream-json events
            assert proc.stdout is not None
            while True:
                line = await asyncio.to_thread(proc.stdout.readline)
                if not line:
                    break
                stripped = line.rstrip()
                if not stripped:
                    continue

                logger.debug(f"[deep-dive raw] {stripped[:300]}")

                try:
                    event = json.loads(stripped)
                except json.JSONDecodeError:
                    continue

                logger.debug(
                    f"[deep-dive event] type={event.get('type')} keys={list(event.keys())}"
                )

                # Convert stream-json events to our SSE event format
                async for sse_event in self._convert_event(event):
                    yield sse_event

            # Wait for process and stderr drain to finish
            returncode = await asyncio.to_thread(proc.wait)
            stderr_text = await stderr_task
            if stderr_text and returncode != 0:
                logger.warning(f"Deep dive stderr: {stderr_text[:500]}")

            duration_ms = int((time.monotonic() - start_time) * 1000)

            if returncode != 0:
                yield {"type": "error", "data": f"Claude Code exited with code {returncode}"}
            else:
                yield {
                    "type": "done",
                    "data": {"duration_ms": duration_ms},
                }

        except Exception as e:
            err_type = type(e).__name__
            logger.error(f"Deep dive error [{err_type}]: {e}\n{traceback.format_exc()}")
            yield {"type": "error", "data": f"Deep dive failed: {err_type}: {e}"}
        finally:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    await asyncio.to_thread(proc.wait, timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

    async def _convert_event(self, event: dict) -> AsyncGenerator[dict, None]:
        """Convert a Claude Code stream-json event into SSE events."""
        etype = event.get("type", "")

        if etype == "content_block_start":
            block = event.get("content_block", {})
            if block.get("type") == "tool_use":
                name = block.get("name", "?")
                yield {"type": "tool_use", "data": f"{name}(...)"}
            elif block.get("type") == "thinking":
                yield {"type": "thinking_start", "data": ""}

        elif etype == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                if text:
                    self._text_streamed_via_delta = True
                    yield {"type": "token", "data": text}
            elif delta.get("type") == "thinking_delta":
                thinking = delta.get("thinking", "")
                if thinking:
                    yield {"type": "thinking", "data": thinking}

        elif etype == "content_block_stop":
            pass  # suppress — block boundaries handled by start/delta

        elif etype == "assistant":
            # Full turn summary — yield text as fallback if content_block_delta
            # never delivered any text (Windows pipe buffering, or CLI doesn't
            # emit deltas). Tool use blocks always emitted.
            message = event.get("message", {})
            for block in message.get("content", []):
                if block.get("type") == "text" and not self._text_streamed_via_delta:
                    text = block.get("text", "")
                    if text:
                        yield {"type": "token", "data": text}
                elif block.get("type") == "tool_use":
                    name = block.get("name", "?")
                    inp = block.get("input", {})
                    summary = ", ".join(
                        f"{k}={repr(v)[:80]}" for k, v in list(inp.items())[:4]
                    )
                    yield {"type": "tool_use", "data": f"{name}({summary})"}

        elif etype == "result":
            # Final result — extract usage stats
            result_data: dict = {}
            if event.get("cost_usd") is not None:
                result_data["cost_usd"] = event["cost_usd"]
            if event.get("total_input_tokens") is not None:
                result_data["input_tokens"] = event["total_input_tokens"]
            if event.get("total_output_tokens") is not None:
                result_data["output_tokens"] = event["total_output_tokens"]
            if event.get("duration_ms") is not None:
                result_data["duration_ms"] = event["duration_ms"]
            if result_data:
                yield {"type": "usage", "data": result_data}

            # Also yield the final result text if present
            result_text = event.get("result", "")
            if result_text:
                yield {"type": "token", "data": result_text}

        elif etype == "system":
            msg = event.get("message", "")
            if msg:
                yield {"type": "status", "data": msg}
