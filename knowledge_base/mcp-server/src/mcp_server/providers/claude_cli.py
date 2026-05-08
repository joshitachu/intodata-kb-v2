"""Claude Code CLI provider — spawns `claude -p -` as a subprocess."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import traceback

from mcp_server.config import config
from mcp_server.providers.base import AgentResult, DocGenContext, DocGenerationProvider, LogCallback
from mcp_server.services.token_refresh import ensure_token_fresh

logger = logging.getLogger(__name__)


class ClaudeCliProvider(DocGenerationProvider):
    """Drives doc generation by spawning the Claude Code CLI as a subprocess.

    This provider is equivalent to the original ClaudeService subprocess path.
    It builds the 4-phase mega-prompt from the pre-computed DocGenContext and
    streams stream-json events back through log_cb.
    """

    # ------------------------------------------------------------------ #
    # Event formatting (stream-json → human-readable log lines)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _format_event(event: dict) -> str | None:
        etype = event.get("type", "")

        if etype == "assistant":
            message = event.get("message", {})
            parts = []
            for block in message.get("content", []):
                if block.get("type") == "text":
                    text = block.get("text", "").strip()
                    if text:
                        parts.append(text[:500])
                elif block.get("type") == "tool_use":
                    name = block.get("name", "?")
                    inp = block.get("input", {})
                    summary = ", ".join(
                        f"{k}={repr(v)[:80]}" for k, v in list(inp.items())[:4]
                    )
                    parts.append(f"[tool_call] {name}({summary})")
            return " | ".join(parts) if parts else None

        if etype in ("tool_result", "content_block_stop"):
            return None

        if etype == "result":
            text = event.get("result", "")
            if text:
                return f"[result] {text[:300]}"
            return "[result] (empty)"

        if etype == "system":
            return f"[system] {event.get('message', '')[:200]}"

        if etype:
            return f"[{etype}] {json.dumps(event)[:200]}"

        return None

    # ------------------------------------------------------------------ #
    # Prompt building
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_prompt(ctx: DocGenContext) -> str:
        """Assemble the 4-phase mega-prompt."""

        staging_abs = ctx.staging_abs
        logs_abs = ctx.logs_abs
        analysis_guide_file = ctx.analysis_guide_file
        write_model = ctx.write_model
        effective_model = ctx.effective_model
        total_jobs = ctx.total_jobs
        total_groups = ctx.total_groups
        project_id = ctx.project_id

        # Compute scaled analysis target from preprocessed data size
        total_preprocess_bytes = sum(
            f.stat().st_size
            for f in (ctx.staging_dir / "_preprocess").glob("*.md")
            if not f.name.startswith("_")
        )
        analysis_target_kb = max(15, min(60, total_preprocess_bytes // 1024))

        # Depth tier scaling based on total jobs
        _BASE_DEPTH = {
            "00_EXEC": 600, "01_OVERVIEW": 800, "02_ARCH": 1400, "03_DATA": 1800,
            "04_BIZ": 1600, "05_OPS": 1600, "06_ENV": 1000, "07_SEC": 1000,
            "08_PAT": 1000, "09_RUN": 1000, "10_ONBOARD": 1000, "11_MIG": 1200,
        }
        if total_jobs < 50:
            depth_multiplier, depth_tier = 1.0, "small"
        elif total_jobs < config.large_project_threshold:
            depth_multiplier, depth_tier = 1.0, "medium"
        else:
            depth_multiplier, depth_tier = 1.5, "large"

        depth_table_rows = "\n".join(
            f"   | {doc} | {int(base * depth_multiplier)} |"
            for doc, base in _BASE_DEPTH.items()
        )

        # Dynamic Phase 3 agent configuration
        is_large_project = total_jobs >= config.large_project_threshold
        num_agents = 4 if is_large_project else 3

        if is_large_project:
            agent_table = (
                "   The 4 doc-assembly agents (large project -- split for deeper output):\n"
                "   | Agent          | Bundle                            | Reference bundle      | Writes                                                                                          |\n"
                "   |----------------|-----------------------------------|-----------------------|-------------------------------------------------------------------------------------------------|\n"
                "   | Core           | _analysis/_bundle_core.md         | _ref_core*.md         | 00_AGENT_BRIEF, 01_ARCHITECTURE_MAP, 02_COMPONENT_INVENTORY, 03_DATA_FLOWS_AND_LINEAGE          |\n"
                "   | Business+Ops   | _analysis/_bundle_business_ops.md | _ref_business_ops*.md | 04_BUSINESS_RULES_AND_TRANSFORMATIONS, 05_RUNTIME_CONFIG_AND_EXTERNALS, 06_OPERATIONS_TESTING_AND_DEBUGGING |\n"
                "   | User           | _analysis/_bundle_user.md         | _ref_user*.md         | 07_QUALITY_SECURITY_RISK_SCORECARD, 08_CHANGE_PLAYBOOK                                         |"
            )
            agent_retry_map = "Core, Business+Ops, or User"
        else:
            agent_table = (
                "   The 3 doc-assembly agents:\n"
                "   | Agent          | Bundle                             | Reference bundle                 | Writes                                                                                          |\n"
                "   |----------------|------------------------------------|----------------------------------|-------------------------------------------------------------------------------------------------|\n"
                "   | Core           | _analysis/_bundle_core.md          | _ref_core*.md                    | 00_AGENT_BRIEF, 01_ARCHITECTURE_MAP, 02_COMPONENT_INVENTORY, 03_DATA_FLOWS_AND_LINEAGE          |\n"
                "   | Business+Ops   | _analysis/_bundle_business_ops.md  | _ref_business_ops*.md            | 04_BUSINESS_RULES_AND_TRANSFORMATIONS, 05_RUNTIME_CONFIG_AND_EXTERNALS, 06_OPERATIONS_TESTING_AND_DEBUGGING |\n"
                "   | Patterns+User  | _analysis/_bundle_patterns_user.md | _ref_patterns*.md, _ref_user*.md | 07_QUALITY_SECURITY_RISK_SCORECARD, 08_CHANGE_PLAYBOOK                                         |"
            )
            agent_retry_map = "Core, Business+Ops, or Patterns+User"

        # Hindsight memory steps
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

        # Resume block
        resume_prompt_block = ""
        if ctx.resume_state is not None:
            rs = ctx.resume_state
            analysis = rs.get("analysis_files", [])
            scorecard = rs.get("scorecard_exists", False)
            bundles = rs.get("bundles_exist", False)
            completed = rs.get("completed_docs", [])

            parts = ["RESUME MODE -- SKIP COMPLETED WORK:"]
            parts.append("   This is a RESUMED generation. The following artifacts already exist:\n")

            if analysis:
                parts.append(
                    f"   COMPLETED ANALYSIS ({len(analysis)} groups): {', '.join(analysis)}"
                )
                parts.append(
                    f"   -> Skip Phase 2 entirely for these groups. "
                    f"Analysis files are in {staging_abs}/_analysis/."
                )
            else:
                parts.append("   COMPLETED ANALYSIS: None — run Phase 2 for all groups.")

            parts.append(f"   COMPLETED SCORECARD: {'Yes' if scorecard else 'No'}")
            if scorecard:
                parts.append("   -> Skip Phase 2.6.")

            parts.append(f"   COMPLETED BUNDLES: {'Yes' if bundles else 'No'}")
            if bundles:
                parts.append("   -> Skip Phase 2.8.")

            if completed:
                parts.append(f"   COMPLETED DOCS: {', '.join(completed)}")
                parts.append(
                    "   -> In Phase 3, only spawn agents whose assigned docs are MISSING."
                    "\n     Do NOT re-spawn agents whose docs are already written."
                )
            else:
                parts.append("   COMPLETED DOCS: None — run Phase 3 for all agents.")

            parts.append("\n   PROCEED DIRECTLY TO the first incomplete phase.\n")
            resume_prompt_block = "\n".join(parts) + "\n"

        phase2_header = (
            "PHASE 2 -- GROUP ANALYSIS (parallel sub-agents):\n"
            "   Spawn one sub-agent per group (all in parallel). Each sub-agent:"
        )

        return f"""{resume_prompt_block}Generate complete documentation for project '{project_id}'.

PHASE 1 -- SETUP:
{hindsight_phase1}   - All data is pre-processed and ready in {staging_abs}/_preprocess/.
   - Do NOT call get_scan_status, get_chunks, fetch_json, fetch_chunk, or any MCP tools.
     Everything needed is pre-bundled in the files below.
   - Directories {staging_abs}/, {staging_abs}/_analysis/, {staging_abs}/_assessment/, and logs/ are pre-created.
   - Pre-processed scan data: {staging_abs}/_preprocess/{{group}}.md for each group.
     If a group was too large, it is split into {{group}}_part1.md and {{group}}_part2.md.
   - Analysis guide: {staging_abs}/_preprocess/{analysis_guide_file}
   - Phase 3 synthesis guide: {staging_abs}/_preprocess/_phase3_guide.md
   - Document templates (read BEFORE Phase 3): {staging_abs}/_preprocess/_doc_templates.md
   - Quick scan report (pre-computed risk flags): {staging_abs}/_assessment/quick_scan.md
   - Reference doc templates: {staging_abs}/_preprocess/_ref_*.md files per Phase 3 agent.

{phase2_header}
   1. Reads {staging_abs}/_assessment/quick_scan.md to understand which groups have high-risk flags
      and calibrate analysis depth accordingly (CRITICAL/HIGH flags warrant deeper investigation).
   2. Reads {staging_abs}/_preprocess/{analysis_guide_file} for analysis guidance.
   3. Reads {staging_abs}/_preprocess/{{group}}.md for ALL scan data.
      If the group is split ({{group}}_part1.md and {{group}}_part2.md exist), read BOTH parts.
      IMPORTANT: If any file is >500 lines, use `Bash cat file | head -N` instead of the Read tool.
      Never issue more than 3 parallel Read calls.
   4. Analyzes the pre-processed data across ALL dimensions defined in the analysis guide.
   5. Writes a RICH structured analysis file to {staging_abs}/_analysis/{{group}}.md
      (MAXIMUM: {analysis_target_kb}KB -- prioritize depth over breadth when approaching the cap).
   6. Writes a concise summary (max 50 lines) to {staging_abs}/_analysis/{{group}}_summary.md
      containing: job count, key findings, top risks, critical expressions/rules discovered.
      This summary is used by Phase 3 agents to avoid re-reading the full analysis file.
   7. Returns a ~2KB navigation summary to the orchestrator (group name, job count, key findings).

   If a group is split into parts (e.g. USA_part1.md and USA_part2.md), spawn one sub-agent per part,
   then merge their analyses into a single {staging_abs}/_analysis/{{group}}.md and one {{group}}_summary.md.

   The analysis file MUST contain ALL sections defined in the analysis guide ({analysis_guide_file}).
   Be EXHAUSTIVE. Include every expression, every rule, every column. Depth over brevity.

   IMPORTANT for sub-agents:
   - Read quick_scan.md first to know which risks to prioritize.
   - Read the _preprocess/{{group}}.md file for your group -- it has ALL the data you need.
   - Read {staging_abs}/_preprocess/{analysis_guide_file} for structured analysis guidance.
   - Do NOT call fetch_chunk(), fetch_json, get_analysis_prompts(), get_reference_docs(), or any MCP tools.
   - Do NOT use TodoWrite, mkdir, ls, wc, or ASCII validation scripts.
   - Do NOT read .agent/ docs, CLAUDE.md, or mcp-workflow.md.
   - PROHIBITED: Do NOT read any files outside {staging_abs}/.
   - Write the analysis file using the Write tool to {staging_abs}/_analysis/{{group}}.md.
   - Write the summary file using the Write tool to {staging_abs}/_analysis/{{group}}_summary.md.

PHASE 2.5 -- GATE VALIDATION + INDEX BUILD (before Phase 3):
   After ALL Phase 2 sub-agents return, validate the analysis output:
   1. Run: Bash ls -la {staging_abs}/_analysis/
   2. For each preprocessed group, verify its _analysis/{{group}}.md exists and is non-empty.
   3. If any analysis file is MISSING or under 1KB:
      - Log which group(s) failed.
      - Re-spawn a sub-agent for that group ONLY (same instructions as Phase 2). Max 1 retry per group.
   4. After retries, if any group still has no analysis: proceed with available data but log a WARNING.
   5. If any group is missing its {{group}}_summary.md, write a minimal one-paragraph summary
      by reading the first 30 lines of its analysis file.
   6. Only proceed to Phase 3 once this gate passes.

PHASE 2.6 -- SCORE & ASSESS (one sub-agent, after Phase 2.5 gate passes):
   Spawn ONE sub-agent to produce a structured assessment scorecard:
   1. Read ALL {staging_abs}/_analysis/{{group}}.md files (every group's analysis).
   2. Read the scoring reference templates:
      - {staging_abs}/_preprocess/_scoring_rubric.md (8 dimensions x 5 levels with criteria)
      - {staging_abs}/_preprocess/_anti_patterns.md (curated anti-pattern catalog with IDs)
      - {staging_abs}/_preprocess/_red_flags.md (common issue indicators with IDs)
      - {staging_abs}/_preprocess/_security_checklist.md (structured security review)
      - {staging_abs}/_preprocess/_pattern_library.md (known good patterns for comparison)
   3. Score each of the 8 dimensions (1-5) following the rubric criteria exactly.
      Cite specific evidence: job names, component references, expressions, counts.
   4. Identify anti-patterns (reference by AP-xxx-nnn ID) and red flags (RF-xxx-nnn ID).
   5. Complete the security checklist evaluation.
   6. Write the full scorecard to {staging_abs}/_assessment/scorecard.md using the exact format
      defined in _scoring_rubric.md (summary table, detailed findings, anti-patterns, red flags, security).
   7. Return a ~2KB summary to the orchestrator: overall score, top 3 risks, top 3 strengths.

   IMPORTANT for the scoring sub-agent:
   - Read ALL _analysis/ files -- the scorecard must reflect the ENTIRE project.
   - Be SPECIFIC: "7/12 DEU jobs lack tLogCatcher" not "error handling is weak."
   - Every score must cite evidence. A score without evidence is invalid.
   - Do NOT read files outside {staging_abs}/.
   - Do NOT use TodoWrite, mkdir, ls, or any MCP tools.

PHASE 2.8 -- BUNDLE ANALYSIS (after Phase 2.6, before Phase 3):
   Run this command to create per-agent analysis bundles:
   Bash: python3 {staging_abs}/_preprocess/_bundle_analysis.py {staging_abs} {total_jobs} {config.large_project_threshold}
   This creates {staging_abs}/_analysis/_bundle_{{agent}}.md files -- one per Phase 3 agent,
   containing only the analysis sections relevant to that agent's docs.
   Verify: Bash ls -la {staging_abs}/_analysis/_bundle_*.md

PHASE 3 -- WRITE DOCS (parallel sub-agents by theme):
   After Phase 2.8 completes, spawn {num_agents} doc-assembly sub-agents in parallel using model: "{write_model}".
   IMPORTANT: When spawning Phase 3 Task agents, pass model="{write_model}" to each Task tool call.

   BEFORE spawning agents, read {staging_abs}/_preprocess/_doc_templates.md for the exact required
   section structure of every document. Agents MUST follow these templates strictly to avoid post-hoc patching.

   Each Phase 3 agent reads its PRE-BUNDLED analysis file from {staging_abs}/_analysis/_bundle_{{agent}}.md.
   If the bundle was split (e.g. _bundle_core_a.md and _bundle_core_b.md), read BOTH files.
   Each Phase 3 agent also reads {staging_abs}/_analysis/_index.md FIRST to navigate to needed groups.
   Do NOT read individual _analysis/{{group}}.md files -- use the bundles + index.
   Each Phase 3 agent also reads {staging_abs}/_assessment/scorecard.md for quantitative grounding.
   Each Phase 3 agent also reads {staging_abs}/_assessment/quick_scan.md for pre-computed risk flags.
   Each doc agent also reads its REFERENCE DOC BUNDLE from _preprocess/ for format templates.
   Each Phase 3 agent also reads {staging_abs}/_preprocess/_phase3_guide.md for cross-group synthesis.
   Each doc agent writes final .md files to {staging_abs}/.

{agent_table}

   Each sub-agent prompt MUST include:
   - "Read {staging_abs}/_analysis/_index.md first to understand what's available."
   - "Read {staging_abs}/_analysis/_bundle_{{your_agent}}.md (and _bundle_{{agent}}_b.md if it exists)."
   - "Read {staging_abs}/_assessment/scorecard.md for quantitative scores."
   - "Read {staging_abs}/_assessment/quick_scan.md for pre-computed risk flags."
   - "Read ALL _ref_{{agent}}*.md files in {staging_abs}/_preprocess/ for format reference."
   - "Read {staging_abs}/_preprocess/_doc_templates.md for required section structure."
   - "Read {staging_abs}/_preprocess/_phase3_guide.md for cross-group synthesis guidance."
   - Do NOT call get_reference_docs() or get_analysis_prompts() -- use the pre-bundled files.
   - Do NOT read individual _analysis/{{group}}.md files -- use ONLY your bundle + index.
   - NEVER read a file >500 lines in one call; use `Bash cat file | head -N` for large files.
   SCORECARD INTEGRATION:
   - Reference specific scores from the scorecard in your documents.
   - Example: "Security: 2/5 (see Assessment Scorecard for details)"
   - Include anti-pattern IDs and red flag IDs where relevant.
   - The Scorecard doc (doc 07) MUST include scores, confidence, evidence, red flags, quick wins, and unknowns.
   - The Change Playbook (doc 08) MUST include inspect/test/rollback guidance.
   - The Agent Brief (doc 00) MUST include onboarding and documentation map guidance.

   DEPTH GUIDANCE:
   This project has {total_jobs} jobs across {total_groups} groups ({depth_tier} project, {depth_multiplier}x depth scaling).

   MINIMUM line targets (these are MINIMUMS, not maximums -- larger projects MUST produce proportionally deeper docs):
   | Doc | Min Lines |
   |-----|-----------|
{depth_table_rows}

   Scale document depth to match the actual content available:
   - Write as much as needed to cover every relevant finding. Do NOT pad.
   - Do NOT truncate to hit a target. Include everything relevant.
   - A 4-job project may produce 50-line docs. A 250-job project may produce 1000+ line docs.
   - Depth comes from ACTUAL content (expressions, rules, schemas, risks), not filler.
   - Every claim needs a specific job name and component reference.
   - Show actual code/expressions/SQL, then explain. Never just summarize.

   NARRATIVE RULES:
   - Never summarize when you can enumerate
   - Every claim needs a job name + component reference
   - Show ACTUAL code/expressions/SQL, then explain
   - "Error handling is weak" is NOT acceptable
   - "7 of 12 DEU jobs have no tLogCatcher" IS acceptable
   - Include concrete counts, percentages, and job names

   Each doc agent MUST:
   - Cross-reference data from multiple groups to build comprehensive docs.
   - Match the depth and structure shown in reference doc templates from the bundle.
   - Include specific expressions, column names, job names -- not just summaries.

   DOCUMENT CATALOG:
   - Always generate exactly the 9 assistant-grade docs: 00_AGENT_BRIEF through 08_CHANGE_PLAYBOOK.
   - Do not generate the old executive summary, standalone FAQ, migration assessment, or split 05a-05d docs.

   Quality standards:
   - Every doc MUST have a table of contents
   - Use plain ASCII tables (no Unicode box-drawing)
   - Business rules: BR-{{GROUP}}-NNN tags with full expressions
   - Risk items: CRITICAL / HIGH / MEDIUM / LOW severity
   - Include job names, component refs, and actual code/expressions for traceability
   - Schema docs must include column-level detail (names, types, nullable)
   - Data flow docs must include tMap expressions with explanations

PHASE 3.5 -- DOC VALIDATION (before cleanup):
   After ALL Phase 3 doc agents return, validate the output:
   1. Run: Bash ls -la {staging_abs}/*.md
   2. Required docs (must exist and be >500 bytes):
      00_AGENT_BRIEF, 01_ARCHITECTURE_MAP, 02_COMPONENT_INVENTORY,
      03_DATA_FLOWS_AND_LINEAGE, 04_BUSINESS_RULES_AND_TRANSFORMATIONS,
      05_RUNTIME_CONFIG_AND_EXTERNALS, 06_OPERATIONS_TESTING_AND_DEBUGGING,
      07_QUALITY_SECURITY_RISK_SCORECARD, 08_CHANGE_PLAYBOOK.
   3. If any REQUIRED doc is missing or under 500 bytes:
      - Identify which Phase 3 agent was responsible ({agent_retry_map}).
      - Re-spawn ONLY that agent with the same instructions. Max 1 retry per agent.
   4. After retries, proceed to Phase 4 with whatever docs exist. Log any still-missing docs as WARNINGS.

PHASE 4 -- CLEANUP & PUBLISH:
   - Delete the {staging_abs}/_analysis/ directory and all its contents.
   - Delete the {staging_abs}/_preprocess/ directory and all its contents.
   - KEEP {staging_abs}/_assessment/scorecard.md -- it is part of the final output.
   - KEEP {staging_abs}/_assessment/quick_scan.md -- it is part of the final output.
   - Write {staging_abs}/index.json mapping docs to groups. The index MUST include all produced docs with their filenames exactly as written.
   - Write {logs_abs}/run_{project_id}_{{timestamp}}.log.
   - Do NOT call insert_to_rag -- the server will push docs to RAG automatically after successful generation.
{hindsight_phase4}
PROHIBITED ACTIONS:
   - Do NOT use TodoWrite tool.
   - Do NOT run ASCII/Unicode validation scripts.
   - Do NOT read .agent/ docs or CLAUDE.md or mcp-workflow.md.
   - Do NOT create _scratch/ directories or other intermediate files (the _analysis/, _assessment/, and _preprocess/ directories are allowed).
   - Do NOT run mkdir, ls, wc for verification -- directories are pre-created.
   - Do NOT call get_analysis_prompts(), get_reference_docs(), get_scan_status, get_chunks, fetch_json, fetch_chunk, trigger_scan, or any other MCP/fetch tools.
     ALL scan data, prompts, and reference docs are pre-bundled in _preprocess/.
   - Only final .md docs, _analysis/, _assessment/, and _preprocess/ files go to {staging_abs}/.

FILE READING GUIDANCE (prevents cascading failures):
   - For files > 500 lines, use `Bash cat file | head -N` or `tail -n +M | head -N` instead of the Read tool.
   - Never issue more than 3 parallel Read calls -- if one fails, ALL siblings cascade-fail.
   - For JSON files, use `python3 -c "import json; ..."` with json.load() to extract specific fields.
   - The _preprocess/ markdown files are designed to be readable without chunking.
"""

    # ------------------------------------------------------------------ #
    # Provider entry point
    # ------------------------------------------------------------------ #

    async def run(
        self,
        ctx: DocGenContext,
        log_cb: LogCallback,
    ) -> AgentResult:
        """Build the mega-prompt and spawn the Claude Code CLI subprocess."""

        project_id = ctx.project_id

        # Ensure OAuth token is fresh before spawning CLI
        if not ensure_token_fresh():
            msg = (
                "Claude OAuth token expired and refresh failed. "
                "Run 'claude login' as the service user to re-authenticate."
            )
            logger.error(msg)
            await log_cb(project_id, msg, "error")
            return AgentResult(success=False, error=msg)

        resolved_cmd = shutil.which(config.claude_command)
        if not resolved_cmd:
            msg = f"Claude CLI not found on PATH: {config.claude_command}"
            logger.error(msg)
            await log_cb(project_id, msg, "error")
            return AgentResult(success=False, error=msg)

        prompt = self._build_prompt(ctx)

        cmd = [
            resolved_cmd,
            "-p", "-",
            "--max-turns", "200",
            "--dangerously-skip-permissions",
            "--output-format", "stream-json",
            "--verbose",
        ]
        if ctx.effective_model:
            cmd[3:3] = ["--model", ctx.effective_model]

        from pathlib import Path
        _subprocess_cwd = Path(__file__).resolve().parent.parent.parent.parent.parent

        resume_label = "resume" if ctx.resume_state is not None else "fresh"
        logger.info(
            "Doc generation config: model=%s, write_model=%s, groups=%d, jobs=%d, max_turns=200",
            ctx.effective_model, ctx.write_model, ctx.total_groups, ctx.total_jobs,
        )
        logger.info("claude resolved path: %s", resolved_cmd)
        await log_cb(
            project_id,
            f"Model: {ctx.effective_model} | Write model: {ctx.write_model} | "
            f"Groups: {ctx.total_groups} | Jobs: {ctx.total_jobs} | Mode: {resume_label}",
            "info",
        )
        await log_cb(project_id, "Starting doc generation via Claude Code...", "info")

        try:
            env = {**os.environ, "PYTHONUTF8": "1"}
            env.pop("CLAUDECODE", None)
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
                cwd=str(_subprocess_cwd),
            )

            proc.stdin.write(prompt)
            proc.stdin.close()

            await log_cb(
                project_id, f"Claude Code subprocess started (PID {proc.pid})", "info",
            )

            async def _read_stderr(stream) -> list[str]:
                lines: list[str] = []
                while True:
                    line = await asyncio.to_thread(stream.readline)
                    if not line:
                        break
                    stripped = line.rstrip()
                    if stripped:
                        lines.append(stripped)
                        await log_cb(project_id, f"[stderr] {stripped}", "warning")
                return lines

            async def _read_stdout_json(stream) -> tuple[list[str], dict | None]:
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
                        await log_cb(project_id, stripped, "info")
                        continue
                    if event.get("type") == "result":
                        result_event = event
                    msg = self._format_event(event)
                    if msg:
                        await log_cb(project_id, msg, "info")
                return lines, result_event

            stdout_result, _ = await asyncio.gather(
                _read_stdout_json(proc.stdout),
                _read_stderr(proc.stderr),
            )
            _, result_event = stdout_result

            returncode = await asyncio.to_thread(proc.wait)

            # Build AgentResult from stream-json result event
            ar = AgentResult(
                success=returncode == 0,
                model_name=ctx.effective_model,
                template_snapshot=ctx.template_snapshot,
            )
            if result_event:
                ar.session_id = result_event.get("session_id")
                ar.num_turns = result_event.get("num_turns")
                ar.duration_ms = result_event.get("duration_ms")
                ar.duration_api_ms = result_event.get("duration_api_ms")
                ar.cost_usd = result_event.get("total_cost_usd")
                re_usage = result_event.get("usage", {})
                ar.input_tokens = (
                    re_usage.get("input_tokens", 0)
                    + re_usage.get("cache_read_input_tokens", 0)
                    + re_usage.get("cache_creation_input_tokens", 0)
                )
                ar.output_tokens = re_usage.get("output_tokens", 0)
                model_usage = result_event.get("modelUsage", {})
                if model_usage:
                    ar.model_name = next(iter(model_usage.keys()), "claude")

            level = "info" if returncode == 0 else "error"
            msg = (
                "Doc generation completed successfully"
                if returncode == 0
                else f"Doc generation failed (exit code {returncode})"
            )
            await log_cb(project_id, msg, level)
            return ar

        except FileNotFoundError:
            msg = f"Claude CLI not found: {config.claude_command} (resolved: {resolved_cmd})"
            logger.error(msg)
            await log_cb(project_id, msg, "error")
            return AgentResult(success=False, error=msg)
        except Exception as e:
            err_type = type(e).__name__
            msg = f"Claude CLI provider error [{err_type}]: {repr(e)}"
            logger.error("%s\n%s", msg, traceback.format_exc())
            await log_cb(project_id, msg, "error")
            return AgentResult(success=False, error=msg)
