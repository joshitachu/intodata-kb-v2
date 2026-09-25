"""Claude Code CLI provider -- spawns `claude -p -` as a subprocess."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import traceback

from pathlib import Path

from mcp_server.config import config
from mcp_server.providers.base import AgentResult, DocGenContext, DocGenerationProvider, LogCallback
from mcp_server.services.doc_quality_scorer import collapse_floor_by_doc
from mcp_server.services.generation_method import METHOD_FILENAME
from mcp_server.services.group_attribution_guard import analysis_group_for_slice
from mcp_server.services.investigation_method import STAGED_METHOD_DIRNAME
from mcp_server.services.token_refresh import (
    apply_auth_env,
    describe_auth,
    ensure_token_fresh,
)

logger = logging.getLogger(__name__)

#: How many group names the run brief lists before it defers to the work plan.
#: The names are a genuine per-run fact and the cheapest possible checklist, but
#: an estate with hundreds of groups would turn the brief into the thing it
#: replaced.  ``_assessment/phase2_work_plan.md`` carries the complete table.
_MAX_LISTED_GROUPS = 60


class ClaudeCliProvider(DocGenerationProvider):
    """Drives doc generation by spawning the Claude Code CLI as a subprocess.

    This provider is equivalent to the original ClaudeService subprocess path.
    It builds the run brief from the pre-computed DocGenContext and streams
    stream-json events back through log_cb.  The METHOD -- the four phases and
    every standing rule -- is a staged file the agent reads, not prose this
    module assembles; see ``services/generation_method.py``.
    """

    # ------------------------------------------------------------------ #
    # Event formatting (stream-json -> human-readable log lines)
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
    # Run brief
    #
    # NOT a prompt template.  Until 2026-09 this function returned a ~47 KB
    # f-string carrying the whole four-phase procedure, ~98 interpolated
    # staging paths, and every imperative the pipeline had accumulated --
    # rebuilt from Python locals on each run, so the method existed only as the
    # output of a function and Python owned the choreography between phases.
    # The method now lives in ``data/generation_method/METHOD.md``, is staged
    # to ``{staging}/_method/METHOD.md`` by ``_preprocess_scan_data``, and is
    # read by the agent.
    #
    # What stays here is exactly what a method file cannot know: where THIS
    # run's workspace is, how big THIS estate is, which groups it owes an
    # analysis, which switches are on, and what a previous attempt already
    # finished.  Anything that would be true of every run belongs in METHOD.md;
    # putting it back here is how the two drift.
    #
    # NO NUMBER HERE IS DERIVED FROM EVIDENCE VOLUME, deliberately.  There used
    # to be one: ``max(15, min(60, total_preprocess_bytes // 1024))``, computed
    # from the PROJECT's total evidence and then stated PER GROUP, so on a
    # measured 24-group estate every group got the same 60 KB ceiling -- 954%
    # of one group's 6 KB and 2.3% of another's 2,569 KB.  It was deleted
    # rather than recalibrated: how much analysis a group's evidence warrants
    # is not a number this side can compute.  METHOD.md states the intent
    # instead (Phase 2 is a retrieval corpus, Phase 3 is the human deliverable)
    # and leaves the judgement with the agent holding the evidence.
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_prompt(ctx: DocGenContext) -> str:
        """The run brief: this run's facts, and a pointer at the method."""

        staging_abs = ctx.staging_abs
        logs_abs = ctx.logs_abs
        project_id = ctx.project_id
        total_jobs = ctx.total_jobs
        total_groups = ctx.total_groups

        method_path = f"{staging_abs}/{STAGED_METHOD_DIRNAME}/{METHOD_FILENAME}"
        shared_dir = f"{staging_abs}/{STAGED_METHOD_DIRNAME}/_shared"

        # Per-document depth floors, rendered from the ONE table that exists:
        # ``doc_quality_scorer._BASE_MIN_LINES_AT_50`` scaled by estate size.
        # It cannot live in METHOD.md because it scales with THIS estate, and
        # it must not be copied beside the grader -- two tables free to
        # disagree is the original defect.  The COLLAPSE floor, not the grading
        # floor (see ``doc_quality_scorer._COLLAPSE_FLOOR_FRACTION``): stating
        # the grade would ask every run for ~1.7x the best output ever measured
        # on this pipeline, and the model would pad to reach it.
        depth_floors = collapse_floor_by_doc(total_jobs)
        _doc_col = max(len(name) for name in depth_floors)
        _num_col = len("Depth floor (lines)")
        depth_floor_table = "\n".join(
            [
                "   | " + "Document".ljust(_doc_col) + " | Depth floor (lines) |",
                "   |" + "-" * (_doc_col + 2) + "|" + "-" * (_num_col + 2) + "|",
            ]
            + [
                "   | " + name.ljust(_doc_col) + " | " + str(floor).ljust(_num_col) + " |"
                for name, floor in depth_floors.items()
            ]
        )

        # What this estate OWES an analysis, which is not the same as what
        # ``_preprocess/`` contains: a group over 40 designs is written as two
        # slices and merged into ONE analysis, so listing slices would demand
        # an analysis that is never meant to exist.  ``analysis_group_for_slice``
        # is the same mapping the work planner and the attribution guard use.
        expected_analyses = sorted({
            analysis_group_for_slice(name) for name in ctx.preprocess_groups
        })
        work_plan = f"{staging_abs}/_assessment/phase2_work_plan.md"
        if not expected_analyses:
            groups_owed = (
                "   Groups owed an analysis: every row of the Groups table in\n"
                f"     {work_plan}"
            )
        elif len(expected_analyses) <= _MAX_LISTED_GROUPS:
            groups_owed = (
                f"   Groups owed an analysis ({len(expected_analyses)}): "
                + ", ".join(expected_analyses)
            )
        else:
            groups_owed = (
                f"   Groups owed an analysis ({len(expected_analyses)}): "
                + ", ".join(expected_analyses[:_MAX_LISTED_GROUPS])
                + f", and {len(expected_analyses) - _MAX_LISTED_GROUPS} more -- the"
                f" complete list is the\n     Groups table in {work_plan}"
            )

        scorecard_line = (
            "enabled -- run the Phase 2.6 scoring sub-agent"
            if config.docgen_llm_scorecard_enabled
            else "DISABLED -- do not spawn a scoring sub-agent; the server has\n"
            "                      already written _assessment/scorecard.md stating that no"
            "\n                      dimension scores were computed"
        )

        # Memory is the one MCP surface this lane may touch, and only when the
        # server has it configured.  METHOD.md prohibits MCP tools outright
        # because on most runs there are none; the exception is a per-run fact
        # and therefore belongs here rather than in the method.
        memory_block = ""
        if config.hindsight_enabled:
            memory_block = f"""
PROJECT MEMORY (configured for this run -- the one MCP exception to METHOD.md):
   - Before Phase 2, call recall_process_memory('doc generation best practices')
     and recall_project_memory('{project_id}', 'previous analysis findings').
   - In Phase 4, call retain_findings('{project_id}', run_summary, 'run-summary')
     with docs generated, health scores and risk flags. If errors occurred, call
     retain_process_lesson(lesson, 'error-lesson').
"""

        # Resume states the REMAINDER, not the completions.  A previous version
        # listed what existed in ``_analysis/`` and compared it to nothing: on
        # one run it reported 30 completed analyses (every ``{group}_summary.md``
        # counted as one) against a 20-group manifest, never named a single
        # missing group, and the model correctly concluded there was nothing
        # left to do.  Five groups were never analysed and the run reported
        # success.  A list of completions cannot express absence.
        resume_block = ""
        if ctx.resume_state is not None:
            rs = ctx.resume_state
            analysed = rs.get("analysis_files", [])
            expected = rs.get("expected_groups", [])
            pending = rs.get("pending_groups", [])
            checklist_source = rs.get("expected_groups_source", "unknown")
            analysis_complete = bool(rs.get("analysis_complete", False))
            scorecard = rs.get("scorecard_exists", False)
            completed = rs.get("completed_docs", [])

            parts = ["", "RESUME -- FINISH THE WORK THAT IS STILL MISSING:"]
            parts.append(
                "   This is a RESUMED generation. The counts below come from this run's own\n"
                "   manifest and the files on disk. A group's summary file is a navigation\n"
                "   aid, NEVER an analysis: a group whose only file is a summary has NOT been\n"
                "   analysed. A group analysed before this run may be stored as a\n"
                f"   {staging_abs}/_analysis/{{group}}/ directory of section files rather than\n"
                f"   one {staging_abs}/_analysis/{{group}}.md file. Both count as analysed and\n"
                "   neither is rewritten. Anything you write now is one file per group."
            )

            if checklist_source == "unknown":
                parts.append(
                    "   GROUP CHECKLIST: UNAVAILABLE -- this staging directory carries no run\n"
                    "   manifest, so the server cannot tell you which groups are already done."
                )
                parts.append(
                    "   -> Do NOT assume Phase 2 is finished. Take the Groups table in\n"
                    f"     {work_plan} as the checklist and run Phase 2 for every row that has\n"
                    f"     neither a non-empty {staging_abs}/_analysis/{{group}}.md nor a\n"
                    f"     non-empty {staging_abs}/_analysis/{{group}}/ directory."
                )
            else:
                parts.append(
                    f"   GROUPS IN THIS ESTATE ({len(expected)}): {', '.join(expected)}"
                )
                parts.append(
                    f"   ANALYSED ALREADY ({len(analysed)}): "
                    f"{', '.join(analysed) if analysed else 'none'}"
                )
                parts.append(
                    f"   STILL TO ANALYSE ({len(pending)}): "
                    f"{', '.join(pending) if pending else 'none'}"
                )
                if pending:
                    parts.append(
                        "   -> Run Phase 2 for the groups on the STILL TO ANALYSE line, and only\n"
                        "     those. Skip Phase 2 for the groups on the ANALYSED ALREADY line;\n"
                        f"     their analyses are in {staging_abs}/_analysis/."
                    )
                else:
                    parts.append(
                        "   -> Every group in this estate already has a non-empty analysis.\n"
                        "     Skip Phase 2 and check its coverage as METHOD.md describes."
                    )

            parts.append(f"   COMPLETED SCORECARD: {'Yes' if scorecard else 'No'}")
            if scorecard and analysis_complete:
                parts.append("   -> Skip Phase 2.6.")
            elif scorecard:
                parts.append(
                    "   -> It was computed before the groups above were analysed, so it does\n"
                    "     not cover them. Treat its numbers as partial and say so wherever a\n"
                    "     document cites one."
                )

            if completed:
                parts.append(f"   COMPLETED DOCS: {', '.join(completed)}")
                if analysis_complete:
                    parts.append(
                        "   -> In Phase 3, only spawn agents whose assigned docs are MISSING."
                        "\n     Do NOT re-spawn agents whose docs are already written."
                    )
                else:
                    parts.append(
                        "   -> They were written without the groups on the STILL TO ANALYSE line,\n"
                        "     so they describe part of the estate. Re-write ALL of them in Phase 3\n"
                        "     once the missing analyses exist."
                    )
            else:
                parts.append("   COMPLETED DOCS: None -- run Phase 3 for all agents.")

            parts.append("   PROCEED DIRECTLY TO the first incomplete phase.")
            resume_block = "\n".join(parts) + "\n"

        return f"""Generate complete documentation for project '{project_id}'.

THE METHOD IS A FILE. READ IT FIRST AND FOLLOW IT:
   {method_path}
   It is the whole procedure -- the four phases, the workspace layout, what each
   phase produces, and how to check your own work. Read it end to end before you
   spawn anything. It grades findings in the vocabulary of
   {shared_dir}/confidence-model.md and self-checks against
   {shared_dir}/validation-checklist.md; read those too, and do not restate them.
   Everything below is FACT about THIS run -- what a method file cannot know.

THIS RUN:
   Project:           {project_id}
   Platform:          {ctx.platform}
   Staging directory: {staging_abs}
                      Every path in METHOD.md is relative to it, and it is also
                      the security boundary: read nothing outside it.
   Logs directory:    {logs_abs}
                      Write run_{project_id}_{{timestamp}}.log there in Phase 4.
   Scanned designs:   {total_jobs} across {total_groups} groups
   Analysis guide:    {staging_abs}/_preprocess/{ctx.analysis_guide_file}
                      Its section headings are the Phase 2 contract.
   Write model:       {ctx.write_model}
                      Pass model="{ctx.write_model}" to every Phase 3 Task call.
   LLM scorecard:     {scorecard_line}
{groups_owed}
     Every one of them owes ONE {staging_abs}/_analysis/{{group}}.md.

DEPTH FLOOR FOR THIS ESTATE (lines per document, scaled to its {total_jobs}
scanned designs and graded after the run against these same numbers -- a
DETECTOR of missed evidence, never a quota; METHOD.md says how to read it):
{depth_floor_table}
{memory_block}{resume_block}"""

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

        # Preflight in two steps, because they are two different faults with two
        # different fixes. describe_auth() catches "nothing is configured" --
        # which ensure_token_fresh() cannot report, since it returns True for an
        # absent credentials file (API-key auth needs no refresh). Without this
        # the run reached the subprocess and died there on a vaguer error.
        auth = describe_auth()
        if not auth.configured:
            logger.error(auth.remedy)
            await log_cb(project_id, auth.remedy, "error")
            return AgentResult(success=False, error=auth.remedy)

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
            # An OAuth login must not be overridden by the harness's API key.
            resolved_auth = apply_auth_env(env)
            await log_cb(
                project_id, f"Claude Code auth mode: {resolved_auth.mode}", "info",
            )
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
