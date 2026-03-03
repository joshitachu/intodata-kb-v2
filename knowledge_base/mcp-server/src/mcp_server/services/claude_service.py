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

        # Clean previous docs to avoid "File has not been read yet" errors
        docs_dir = output_dir / "docs"
        if docs_dir.exists():
            shutil.rmtree(docs_dir, ignore_errors=True)

        (output_dir / "docs").mkdir(parents=True, exist_ok=True)
        (output_dir / "logs").mkdir(parents=True, exist_ok=True)

        # Build Hindsight memory steps conditionally
        hindsight_phase1 = ""
        hindsight_phase4 = ""
        if config.hindsight_enabled:
            hindsight_phase1 = (
                "   - Call recall_process_memory('doc generation best practices') "
                "to load cross-project lessons.\n"
                f"   - Call recall_project_memory('{project_id}', 'previous analysis findings') "
                "to load project-specific context.\n"
            )
            hindsight_phase4 = (
                f"   - Call retain_findings('{project_id}', run_summary, 'run-summary') "
                "with docs generated, health scores, risk flags.\n"
                "   - If errors occurred, call retain_process_lesson(lesson, 'error-lesson').\n"
            )

        prompt = f"""Generate complete documentation for project '{project_id}'.

PHASE 1 -- SETUP:
{hindsight_phase1}   - Call get_scan_status('{project_id}') to check cache freshness.
   - Call get_chunks('{project_id}'). Group chunks by 'group' field.
   - Call fetch_json('{project_id}', 'manifest') for dependency graph.
   - Call fetch_json('{project_id}', 'connections') for metadata connections.
   - Directories output/{project_id}/docs/ and logs/ are pre-created.

PHASE 2 -- GROUP ANALYSIS (parallel sub-agents):
   Spawn one sub-agent per group (all in parallel). Each sub-agent:
   1. Receives: project_id, group name, chunk_ids, manifest, connections.
   2. Calls get_analysis_prompts() to load analysis guidance.
   3. Calls fetch_chunk('{project_id}', chunk_id) for each chunk in its group.
   4. Analyzes ALL chunks across ALL dimensions using the prompt guidance.
   5. Returns a compact text summary (~3-5KB) directly -- NO files written.

   Each summary must follow this structure:
   GROUP: {{name}} | CHUNKS: {{ids}} | JOB_COUNT: {{n}}
   Sections: COMPONENTS, EXECUTION_FLOW, DATA_FLOWS, BUSINESS_RULES (BR-{{GRP}}-NNN),
   EXTERNAL_SYSTEMS, ERROR_HANDLING, CUSTOM_CODE, ENVIRONMENT, SCHEDULING,
   NAMING_PATTERNS, CROSS_GROUP_REFS, RISK_FLAGS, PREVENTIVE_RISKS.

   IMPORTANT for sub-agents:
   - Do NOT call fetch_json for manifest/connections -- use what was passed inline.
   - Do NOT use TodoWrite, mkdir, ls, wc, or ASCII validation scripts.
   - Do NOT read .agent/ docs, CLAUDE.md, or mcp-workflow.md.

PHASE 3 -- WRITE DOCS (parallel sub-agents by theme):
   After ALL group summaries are collected, spawn doc-writing sub-agents in parallel.
   Each doc agent:
   1. Calls get_reference_docs(agent_group) to load format templates in ONE call.
   2. Receives relevant group summaries + manifest.
   3. Writes final .md files to output/{project_id}/docs/.

   Doc agent groups and outputs:
   | Agent       | Writes                                           |
   |-------------|--------------------------------------------------|
   | Overview    | 00_PROJECT_OVERVIEW.md                            |
   | Architecture| 01_COMPONENT_INVENTORY, 02_ARCHITECTURE, 03_EXECUTION_LOGIC |
   | Data        | 04_DATA_FLOWS, 16_SCHEMA_DICTIONARY, 17_DATA_QUALITY |
   | Business    | 10_BUSINESS_LOGIC, 09_CUSTOM_CODE                 |
   | Operations  | 07_ERROR_HANDLING, 08_STATE_SIDE_EFFECTS, 11_KNOWN_ISSUES |
   | Config      | 05_ENVIRONMENT_CONFIG, 20_SECURITY_ACCESS, 21_PERFORMANCE |
   | External    | 06_EXTERNAL_SYSTEMS, 19_SCHEDULING_SLA            |
   | Patterns    | 18_NAMING_CONVENTIONS, 22_CROSS_REGION*, 23_REUSABILITY |
   | UserDocs    | 14_GLOSSARY_FAQ, 15_ONBOARDING, 13_RUNBOOK, 12_CHANGE_LOG |
   | Preventive  | 25_PREVENTIVE_ANALYSIS                            |
   *22_CROSS_REGION only if 2+ groups exist.

   Skip any doc with no meaningful content. Always generate: 00-04, 06, 07, 10, 13-15, 25.

   Quality standards:
   - Every doc MUST have a table of contents
   - Use plain ASCII tables (no Unicode box-drawing)
   - Business rules: BR-{{GROUP}}-NNN tags
   - Risk items: CRITICAL / HIGH / MEDIUM / LOW severity
   - Include job names and component refs for traceability

PHASE 4 -- PUBLISH:
   - Write output/{project_id}/docs/index.json mapping docs to groups.
   - Write output/{project_id}/logs/run_{project_id}_{{timestamp}}.log.
   - Call insert_to_rag('{project_id}') to push docs to vector index.
{hindsight_phase4}
PROHIBITED ACTIONS:
   - Do NOT use TodoWrite tool.
   - Do NOT run ASCII/Unicode validation scripts.
   - Do NOT read .agent/ docs or CLAUDE.md or mcp-workflow.md.
   - Do NOT create _scratch/ directories or intermediate files.
   - Do NOT run mkdir, ls, wc for verification -- directories are pre-created.
   - Only final .md docs go to output/{project_id}/docs/.
"""

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
            "-p", "-",
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
                stdin=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
                cwd=str(config.project_root),
            )

            # Write prompt via stdin to avoid cmd.exe argument parsing issues
            # (.CMD files go through cmd.exe which breaks on newlines and | in args)
            proc.stdin.write(prompt)
            proc.stdin.close()

            await self.log_manager.emit(
                project_id,
                f"Claude Code subprocess started (PID {proc.pid})",
                level="info",
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
