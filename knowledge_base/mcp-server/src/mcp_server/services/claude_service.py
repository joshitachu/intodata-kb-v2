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
from mcp_server.services.token_refresh import ensure_token_fresh

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

        # Use a staging directory so old docs survive if generation fails
        staging_dir = output_dir / "docs_staging"
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)

        for d in [
            staging_dir,
            staging_dir / "_analysis",
            output_dir / "logs",
        ]:
            d.mkdir(parents=True, exist_ok=True)
            d.chmod(0o777)

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
   - Directories output/{project_id}/docs_staging/, output/{project_id}/docs_staging/_analysis/, and logs/ are pre-created.

PHASE 2 -- GROUP ANALYSIS (parallel sub-agents):
   Spawn one sub-agent per group (all in parallel). Each sub-agent:
   1. Receives: project_id, group name, chunk_ids, manifest, connections.
   2. Calls get_analysis_prompts() to load analysis guidance.
   3. Calls get_reference_docs('all') to understand what detail the final docs need.
   4. Calls fetch_chunk('{project_id}', chunk_id) for each chunk in its group.
   5. Analyzes ALL chunks across ALL dimensions using the prompt guidance.
   6. Writes a RICH structured analysis file to output/{project_id}/docs_staging/_analysis/{{group}}.md (target: 15-40KB).
   7. Returns a ~2KB navigation summary to the orchestrator (group name, job count, key findings, section list).

   The analysis file (output/{project_id}/docs_staging/_analysis/{{group}}.md) MUST contain ALL of the following sections:

   ## HEADER
   GROUP: {{name}} | CHUNKS: {{ids}} | JOB_COUNT: {{n}}

   ## COMPONENTS
   Full inventory: every job name, type, purpose, input/output connections.

   ## EXECUTION_FLOW
   Ordered execution sequences with trigger conditions. Include parent-child relationships.

   ## DATA_FLOWS
   Column-level lineage for key data flows. For EVERY tMap component:
   - Source table.column -> target table.column mapping
   - The FULL tMap expression (do NOT truncate)
   - Human-readable explanation of what the expression does
   - Filter/reject conditions

   ## BUSINESS_RULES
   EVERY business rule with structured format:
   - BR-{{GRP}}-NNN: rule ID
   - Expression: the actual code/expression implementing the rule
   - Implementing job: which job contains it
   - Domain: what business area it covers
   - Impact: what happens if the rule is violated
   Include rules from tMap expressions, tJavaRow, tJava, WHERE clauses, filter conditions.

   ## SCHEMA_DICTIONARY
   EVERY table and view referenced:
   - Table/view name
   - Column names, data types, nullable flag
   - Which jobs READ from it, which jobs WRITE to it
   - Key constraints if visible

   ## SOURCE_SQL
   EVERY SQL query in FULL (not truncated). Include:
   - Which component/job contains it
   - Whether it's a source query, lookup, or DML

   ## EXTERNAL_SYSTEMS
   All external connections: databases, APIs, file systems, FTP, etc.
   Include connection parameters (host patterns, ports, database names).

   ## ERROR_HANDLING
   Per-job error handling rating (GOOD / PARTIAL / MISSING).
   Specific gaps: which jobs lack try/catch, which have silent failures.
   Die-on-error settings per subjob.

   ## CUSTOM_CODE
   All tJava, tJavaRow, tJavaFlex code blocks in full.
   Hidden business rules embedded in custom code.
   Utility routines and their purpose.

   ## ENVIRONMENT_CONFIG
   Every context variable with PROD vs TEST/DEV differences.
   Connection strings (sanitized), file paths, feature flags.

   ## SCHEDULING
   Trigger types, cron expressions, execution windows, dependencies between jobs.

   ## NAMING_PATTERNS
   Observed naming conventions for jobs, schemas, tables, variables.

   ## CROSS_GROUP_REFS
   Dependencies on other groups: shared tables, shared context vars, execution ordering.

   ## RISK_FLAGS
   Preventive risks with structured format:
   - Severity: CRITICAL / HIGH / MEDIUM / LOW
   - Affected jobs
   - Description of the risk
   - Recommended remediation

   IMPORTANT for sub-agents:
   - Do NOT call fetch_json for manifest/connections -- use what was passed inline.
   - Do NOT use TodoWrite, mkdir, ls, wc, or ASCII validation scripts.
   - Do NOT read .agent/ docs, CLAUDE.md, or mcp-workflow.md.
   - Write the analysis file using the Write tool to output/{project_id}/docs_staging/_analysis/{{group}}.md.
   - Be EXHAUSTIVE. Include every expression, every rule, every column. Depth over brevity.

PHASE 3 -- WRITE DOCS (parallel sub-agents by theme):
   After ALL Phase 2 sub-agents complete, spawn 5 doc-assembly sub-agents in parallel.
   Each doc agent:
   1. Reads ALL files in output/{project_id}/docs_staging/_analysis/ (every group's analysis).
   2. Calls get_reference_docs(agent_group) to load format templates in ONE call.
   3. Receives the manifest.
   4. Writes final .md files to output/{project_id}/docs_staging/.

   The 5 doc-assembly agents:
   | Agent         | Writes                                                                          |
   |---------------|---------------------------------------------------------------------------------|
   | Core          | 00_PROJECT_OVERVIEW, 01_COMPONENT_INVENTORY, 02_ARCHITECTURE, 03_EXECUTION_LOGIC |
   | Data          | 04_DATA_FLOWS, 16_SCHEMA_DICTIONARY, 17_DATA_QUALITY                            |
   | Business+Ops  | 07_ERROR_HANDLING, 08_STATE_SIDE_EFFECTS, 09_CUSTOM_CODE, 10_BUSINESS_LOGIC, 11_KNOWN_ISSUES |
   | Config+External | 05_ENVIRONMENT_CONFIG, 06_EXTERNAL_SYSTEMS, 19_SCHEDULING_SLA, 20_SECURITY_ACCESS, 21_PERFORMANCE |
   | User+Meta     | 12_CHANGE_LOG, 13_RUNBOOK, 14_GLOSSARY_FAQ, 15_ONBOARDING, 18_NAMING_CONVENTIONS, 22_CROSS_REGION*, 23_REUSABILITY, 25_PREVENTIVE_ANALYSIS |
   *22_CROSS_REGION only if 2+ groups exist.

   Each doc agent MUST:
   - Read EVERY _analysis/{{group}}.md file to get full detail across all groups.
   - Cross-reference data from multiple groups to build comprehensive docs.
   - Match the depth and structure shown in reference docs from get_reference_docs().
   - Include specific expressions, column names, job names -- not just summaries.

   Skip any doc with no meaningful content. Always generate: 00-04, 06, 07, 10, 13-15, 25.

   Quality standards:
   - Every doc MUST have a table of contents
   - Use plain ASCII tables (no Unicode box-drawing)
   - Business rules: BR-{{GROUP}}-NNN tags with full expressions
   - Risk items: CRITICAL / HIGH / MEDIUM / LOW severity
   - Include job names, component refs, and actual code/expressions for traceability
   - Schema docs must include column-level detail (names, types, nullable)
   - Data flow docs must include tMap expressions with explanations

PHASE 4 -- CLEANUP & PUBLISH:
   - Delete the output/{project_id}/docs_staging/_analysis/ directory and all its contents.
   - Write output/{project_id}/docs_staging/index.json mapping docs to groups.
   - Write output/{project_id}/logs/run_{project_id}_{{timestamp}}.log.
   - Do NOT call insert_to_rag -- the server will push docs to RAG automatically after successful generation.
{hindsight_phase4}
PROHIBITED ACTIONS:
   - Do NOT use TodoWrite tool.
   - Do NOT run ASCII/Unicode validation scripts.
   - Do NOT read .agent/ docs or CLAUDE.md or mcp-workflow.md.
   - Do NOT create _scratch/ directories or other intermediate files (the _analysis/ directory is allowed).
   - Do NOT run mkdir, ls, wc for verification -- directories are pre-created.
   - Only final .md docs and _analysis/ files go to output/{project_id}/docs_staging/.
"""

        # Ensure OAuth token is fresh before spawning CLI
        if not ensure_token_fresh():
            msg = "Claude OAuth token expired and refresh failed. Run 'claude login' as the service user to re-authenticate."
            logger.error(msg)
            await self.log_manager.emit(project_id, msg, level="error")
            return {"success": False}

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
