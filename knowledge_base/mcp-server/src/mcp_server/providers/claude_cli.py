"""Claude Code CLI provider — spawns `claude -p -` as a subprocess."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import textwrap
import traceback

from pathlib import Path

from mcp_server.config import config
from mcp_server.providers.base import AgentResult, DocGenContext, DocGenerationProvider, LogCallback
from mcp_server.providers.custom.analysis_parser import PHASE2_SECTION_NAMES
from mcp_server.providers.custom.section_parser import allcaps_heading_name
from mcp_server.services.doc_quality_scorer import collapse_floor_by_doc
from mcp_server.services.group_attribution_guard import analysis_group_for_slice
from mcp_server.services.token_refresh import ensure_token_fresh

logger = logging.getLogger(__name__)

#: Canonical order of the section contract, so the prompt states the same list
#: in the same order on every platform and every run (a provider prefix cache
#: is keyed on bytes; a set-ordered list would void it non-deterministically).
_SECTION_ORDER: dict[str, int] = {
    name: index for index, name in enumerate(PHASE2_SECTION_NAMES)
}


def expected_analysis_sections(staging_dir: Path, analysis_guide_file: str) -> tuple[str, ...]:
    """Section files a Phase 2 worker owes per group, read off the ANALYSIS GUIDE.

    Phase 2 now writes one file per section (``_analysis/{group}/{SECTION}.md``),
    so the Phase 2.5 gate can COUNT what a group produced instead of asking the
    model to eyeball one file.  Counting needs a denominator, and the only
    honest one is the guide this run actually staged: the guide is what the
    workers are told to follow, and it differs per platform.  A second list in
    this module would be a second contract, free to disagree with the first --
    which is the defect class this whole slice removes.

    The guide's own headings are harvested with the shared heading
    recognition and intersected with
    :data:`~mcp_server.providers.custom.analysis_parser.PHASE2_SECTION_NAMES`,
    so a guide's instructional headings (``SELF_CHECK_BEFORE_RETURNING``,
    ``ASSISTANT_DOC_OBLIGATIONS``) never become files somebody owes, and a
    structural name that is not a guide section (``GROUP_SUMMARY``,
    ``UNCLASSIFIED_OR_EXTRA_FINDINGS``) is never demanded either.  All five
    shipped guides yield the same 15 evidence sections.

    Returns ``()`` when the guide is absent or unreadable -- preprocessing
    stages it before the prompt is built, so that is the edge case, not the
    normal path.  The prompt then states the contract by reference ("one file
    per section of the guide") rather than inventing a count it cannot justify.
    """
    preprocess_dir = Path(staging_dir) / "_preprocess"
    names: set[str] = set()
    try:
        candidates = sorted(preprocess_dir.glob(analysis_guide_file))
    except (OSError, ValueError):
        return ()
    for path in candidates:
        try:
            # read_text() translates \r\n to \n, which would matter if these
            # bytes were compared, digested or shipped. They are not: only
            # heading NAMES are harvested, and the guide is ours, not model
            # output. Anything reading an ANALYSIS goes through analysis_store,
            # which opens with newline="" for exactly that reason.
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Analysis guide %s is unreadable (%s)", path.name, exc)
            continue
        for line in text.splitlines():
            candidate = allcaps_heading_name(line, allow_enumerated=True)
            if candidate in _SECTION_ORDER:
                names.add(candidate)
    return tuple(sorted(names, key=_SECTION_ORDER.__getitem__))


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

        # Per-document depth floors, rendered from the ONE table that exists:
        # ``doc_quality_scorer._BASE_MIN_LINES_AT_50`` scaled by estate size.
        # The prompt used to carry its own second table, written for a retired
        # 12-document set (00_EXEC .. 11_MIG) that the DOCUMENT CATALOG below
        # explicitly forbids -- the model was handed line counts for documents
        # it was banned from writing. Deleting it was right on the facts and
        # wrong in effect: it was the only depth signal in the prompt, and
        # 00_AGENT_BRIEF went from 628 lines (run 258) to 76 (run 260) with all
        # nine documents then flagged ``below_min_lines`` by the scorer that
        # still graded them. So the expectation is back, addressed to the nine
        # documents that actually exist, and derived from the grader rather than
        # copied beside it -- two tables free to disagree is the original defect.
        # The COLLAPSE floor, not the grading floor -- see
        # doc_quality_scorer._COLLAPSE_FLOOR_FRACTION. Stating the grade here
        # would ask every run for ~1.7x the best output ever measured, and the
        # model would pad to reach it.
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

        # The Phase 2.5 checklist: what this estate OWES an analysis, which is
        # not the same as what ``_preprocess/`` contains. A group over 40 designs
        # is written as two slices and the prompt requires them MERGED into one
        # ``_analysis/{group}/``, so listing slices would demand an analysis that
        # is never meant to exist. ``analysis_group_for_slice`` is the same
        # mapping the work planner and the attribution guard use.
        expected_analyses = sorted({
            analysis_group_for_slice(name) for name in ctx.preprocess_groups
        })
        expected_analysis_count = len(expected_analyses)
        _listed = 60
        if not expected_analyses:
            expected_analysis_list = (
                "every row of the Groups table in "
                f"{staging_abs}/_assessment/phase2_work_plan.md"
            )
        elif expected_analysis_count <= _listed:
            expected_analysis_list = ", ".join(expected_analyses)
        else:
            expected_analysis_list = (
                ", ".join(expected_analyses[:_listed])
                + f", and {expected_analysis_count - _listed} more -- the complete list is "
                f"the Groups table in {staging_abs}/_assessment/phase2_work_plan.md"
            )

        # The per-section contract (plan decision 2). Phase 2 writes ONE FILE
        # PER SECTION into _analysis/{group}/ instead of one file with section
        # headings inside it, because the heading parse is the only seam in this
        # pipeline that can fail silently -- and did: the guide asked for
        # "### COMPONENTS", the model wrote "## COMPONENTS", every consumer
        # matched zero sections, ~95% of the evidence was dropped with no
        # warning and Phase 3 documented an estate it could not read. A missing
        # FILE cannot be confused with an empty parse.
        #
        # The section list is read off the guide this run staged (see
        # expected_analysis_sections) so the prompt and the workers follow one
        # contract. Empty means the guide could not be read: state the contract
        # by reference rather than assert a count that is not derived.
        expected_sections = expected_analysis_sections(ctx.staging_dir, analysis_guide_file)
        section_examples = expected_sections[:3] or PHASE2_SECTION_NAMES[:3]
        section_file_examples = "\n".join(
            f"        {staging_abs}/_analysis/{{group}}/{name}.md"
            for name in section_examples
        ) + f"\n        ... and so on for every section of {analysis_guide_file}"
        if expected_sections:
            section_name_block = textwrap.fill(
                ", ".join(expected_sections),
                width=86,
                initial_indent="        ",
                subsequent_indent="        ",
                break_long_words=False,
                break_on_hyphens=False,
            )
            sections_owed = (
                f"      This estate's guide names {len(expected_sections)} sections, so "
                f"each group owes\n      {len(expected_sections)} section files:\n"
                f"{section_name_block}"
            )
            gate_expected_count = (
                f"      That count must be {len(expected_sections)} -- one file for each "
                f"section of\n      {analysis_guide_file}:\n"
                f"{section_name_block}\n"
                f"      Report one line per group: \"<group>: N of "
                f"{len(expected_sections)} sections\", and name the\n"
                "      missing ones."
            )
        else:
            sections_owed = (
                "      One file for every section heading in the analysis guide -- the\n"
                "      guide is the list, and it is the same list the gate counts against."
            )
            gate_expected_count = (
                "      That count must equal the number of section headings in\n"
                f"      {analysis_guide_file}. Report one line per group: \"<group>: N of "
                "M\n      sections\", and name the missing ones."
            )

        # Dynamic Phase 3 agent configuration. The count is derived from the
        # table rows below, never asserted separately: the large-project branch
        # used to announce 4 agents while defining 3, and the retry map listed 3.
        is_large_project = total_jobs >= config.large_project_threshold

        if is_large_project:
            agent_table = (
                "   The 3 doc-assembly agents (large project):\n"
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
                "   | Patterns+User  | _analysis/_bundle_patterns_user.md | _ref_user*.md                    | 07_QUALITY_SECURITY_RISK_SCORECARD, 08_CHANGE_PLAYBOOK                                         |"
            )
            agent_retry_map = "Core, Business+Ops, or Patterns+User"

        # Both branches define exactly three agents; a large project only changes
        # which bundle the 07/08 writer reads. No _ref_patterns*.md bundle is ever
        # staged (services/claude_service.py stages _ref_core, _ref_business_ops
        # and _ref_user only), so naming one sent the writer after a missing file.
        num_agents = 3

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

        # Resume block.
        #
        # It states the REMAINDER, not only the completions. The previous
        # version listed what existed in _analysis/ and compared it to nothing:
        # on ETTM run 260 it reported "COMPLETED ANALYSIS (30 groups) -> Skip
        # Phase 2 entirely for these groups" for 15 real analyses (every
        # {group}_summary.md counted as one) against a 20-group manifest, never
        # named a single missing group, and the model correctly concluded there
        # was nothing left to do. Monitoring, afas, fourpsForSuppliers,
        # fourpsMaximo and utils never reached any document and the run reported
        # success. A list of completions cannot express absence.
        resume_prompt_block = ""
        if ctx.resume_state is not None:
            rs = ctx.resume_state
            analysed = rs.get("analysis_files", [])
            expected = rs.get("expected_groups", [])
            pending = rs.get("pending_groups", [])
            checklist_source = rs.get("expected_groups_source", "unknown")
            analysis_complete = bool(rs.get("analysis_complete", False))
            scorecard = rs.get("scorecard_exists", False)
            bundles = rs.get("bundles_exist", False)
            bundles_stale = bool(rs.get("bundles_stale", False))
            completed = rs.get("completed_docs", [])

            parts = ["RESUME MODE -- FINISH THE WORK THAT IS STILL MISSING:"]
            parts.append(
                "   This is a RESUMED generation. The server took the counts below from this\n"
                "   run's own manifest and the files on disk. A group's summary file is a\n"
                "   navigation aid, NEVER an analysis: a group whose only file is a summary\n"
                "   has NOT been analysed.\n"
                "   A group analysed before this run may be stored as one\n"
                f"   {staging_abs}/_analysis/{{group}}.md file rather than a {{group}}/\n"
                "   directory of section files. Both count as analysed and neither is\n"
                "   rewritten. Anything you write in Phase 2 uses the per-section layout.\n"
            )

            if checklist_source == "unknown":
                parts.append(
                    "   GROUP CHECKLIST: UNAVAILABLE -- this staging directory carries no run\n"
                    "   manifest, so the server cannot tell you which groups are already done."
                )
                parts.append(
                    "   -> Do NOT assume Phase 2 is finished. Take the Groups table in\n"
                    f"     {staging_abs}/_assessment/phase2_work_plan.md as the checklist and run\n"
                    "     Phase 2 for every row that has neither a non-empty\n"
                    f"     {staging_abs}/_analysis/{{group}}/ directory of section files nor a\n"
                    f"     non-empty {staging_abs}/_analysis/{{group}}.md from an earlier run."
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
                        f"     their analysis files are in {staging_abs}/_analysis/."
                    )
                else:
                    parts.append(
                        "   -> Every group in this estate already has a non-empty analysis.\n"
                        "     Skip Phase 2 and go to the Phase 2.5 gate."
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

            parts.append(f"   COMPLETED BUNDLES: {'Yes' if bundles else 'No'}")
            if bundles and not bundles_stale:
                parts.append("   -> Skip Phase 2.8.")
            elif bundles:
                parts.append(
                    "   -> STALE. They were built without the groups on the STILL TO ANALYSE\n"
                    "     line. Re-run Phase 2.8 once those analyses exist; bundling what is on\n"
                    "     disk now would republish the same gap."
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

            parts.append("\n   PROCEED DIRECTLY TO the first incomplete phase.\n")
            resume_prompt_block = "\n".join(parts) + "\n"

        # Phase 2 is BATCHED, not one sub-agent per group. Measured on ETTM run
        # 259: the orchestrator was told to spawn one sub-agent per group and
        # did -- 20 of them at once -- and the dispatcher ran 3-4 at a time.
        # Analysis files landed one or two a minute from 09:29:11 to 09:45:34,
        # 5 of 20 groups never got one and Phase 3 never started. There is no
        # concurrency flag in `opencode run --help` and no such knob in the
        # binary, so the fan-out itself has to be smaller. The split is
        # bin-packed by the server from THIS estate's distribution (group sizes
        # vary 12x to 3580x between estates, so a constant would be wrong
        # somewhere) and staged as _assessment/phase2_work_plan.md.
        phase2_header = f"""PHASE 2 -- GROUP ANALYSIS (batched parallel sub-agents):
   Sub-agents do NOT run as wide as you spawn them. Measured: 20 groups requested
   in parallel, 3-4 actually running at a time, analyses landing one or two a
   minute for 16 minutes, 5 groups never analysed and Phase 3 never reached. One
   sub-agent per group therefore SERIALISES this phase. Batch instead: fewer
   workers, each covering several groups.

   0. Read {staging_abs}/_assessment/phase2_work_plan.md. It lists every group with
      its evidence size, design count and pre-computed risk flags, a suggested
      split of the groups across workers, and -- where this estate has reusable
      designs -- a shared-library pre-pass to run first.
   1. If the plan lists a shared-library pre-pass, spawn that ONE worker first and
      let it finish. It reads only the reusable designs (joblets, routelets) named
      there and writes the interface digest to {staging_abs}/_analysis/_shared_library.md.
      It writes NO group analysis: a reusable design is still documented in full by
      the group that owns it. Every group worker reads that digest, because a
      design that CALLS a joblet has no definition of that joblet in its own
      evidence.
   2. Then spawn ONE sub-agent per WORKER, all at once, each owning the SET of
      groups in its row. Do not spawn one per group.
   3. The suggested split balances evidence bytes and caps how many groups sit in
      one worker's context; that cap is what stops a component belonging to one
      group being written into another group's analysis. You can see what the
      server cannot -- a group with CRITICAL flags may deserve a worker to itself.
      Deviate when you have a reason, keep whole groups together, and do not
      exceed the group-per-worker cap the plan states.
   4. Record the split you actually used, and why, by APPENDING to
      {staging_abs}/_knowledge/decisions.md (heredoc, never the Write tool -- other
      agents write that directory). Put the closing EOF at the start of its own
      line; an indented terminator does not end a heredoc:
      Bash cat >> {staging_abs}/_knowledge/decisions.md <<'EOF'
      ### <UTC timestamp> | author: agent
      Phase 2 split: <N> workers. W1: <groups>. W2: <groups>. ...
      <used the suggested split unchanged | deviated because ...>
      EOF

   Each worker takes the groups it owns ONE AT A TIME, and for EACH of them:"""

        # Phase 2.6 costs one sub-agent that reads EVERY group analysis (520 KB
        # on the ETTM estate) to produce one advisory file. The same phase was
        # measured in the custom lane at ~0.8M tokens with contract gaps and is
        # disabled by config default; this lane previously hardcoded it into the
        # prompt, so the flag had no effect here. When it is off, Python has
        # already written the deterministic marker to _assessment/scorecard.md.
        if config.docgen_llm_scorecard_enabled:
            phase26_block = f"""PHASE 2.6 -- SCORE & ASSESS (one sub-agent, after Phase 2.5 gate passes):
   Spawn ONE sub-agent to produce a structured assessment scorecard:
   1. Read every group's analysis -- the section files under
      {staging_abs}/_analysis/{{group}}/ (one {{group}}.md file where an earlier run
      wrote one).
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
"""
        else:
            phase26_block = f"""PHASE 2.6 -- SKIPPED (LLM scorecard disabled for this run):
   Do NOT spawn a scoring sub-agent. {staging_abs}/_assessment/scorecard.md has
   already been written by the server and states that no dimension scores were
   computed. Proceed directly from the Phase 2.5 gate to Phase 3.

   Consequence for Phase 3, and it is not optional: where a document would cite a
   score, rating or ranking, state that scorecard-derived dimensions were not
   computed for this run, and present the risk and quality findings directly from
   the evidence you do have -- each group analysis's RISK_FLAGS and ERROR_HANDLING
   sections, and {staging_abs}/_assessment/quick_scan.md. Do not invent a number to
   fill the shape of a scorecard.
"""

        return f"""{resume_prompt_block}Generate complete documentation for project '{project_id}'.

PHASE 1 -- SETUP:
{hindsight_phase1}   - All data is pre-processed and ready in {staging_abs}/_preprocess/.
   - Do NOT call get_scan_status, get_chunks, fetch_json, fetch_chunk, or any MCP tools.
     Everything needed is pre-bundled in the files below.
   - Directories {staging_abs}/, {staging_abs}/_analysis/, {staging_abs}/_assessment/, and logs/ are pre-created.
   - Create the two run-record directories once, now, before spawning anything:
     Bash mkdir -p {staging_abs}/_provenance {staging_abs}/_knowledge
     _provenance/ takes one JSON sidecar per document (Phase 3). _knowledge/ takes the
     run's unresolved-evidence ledger. Neither is a client document.
   - Pre-processed scan data: {staging_abs}/_preprocess/{{group}}.md for each group.
     If a group was too large, it is split into {{group}}_part1.md and {{group}}_part2.md.
   - Analysis guide: {staging_abs}/_preprocess/{analysis_guide_file}
   - Phase 3 synthesis guide: {staging_abs}/_preprocess/_phase3_guide.md
   - Document templates (read BEFORE Phase 3): {staging_abs}/_preprocess/_doc_templates.md
   - Quick scan, per group (pre-computed risk flags): {staging_abs}/_assessment/quick_scan/{{group}}.md
     (the cross-group roll-up is {staging_abs}/_assessment/quick_scan.md)
   - Phase 2 work plan: {staging_abs}/_assessment/phase2_work_plan.md -- every group with its
     evidence size, design count and risk-flag counts, plus the suggested batching of groups
     onto workers. Read it before spawning anything in Phase 2.
   - Reference doc templates: {staging_abs}/_preprocess/_ref_core*.md, _ref_business_ops*.md
     and _ref_user*.md -- one set per Phase 3 agent (see the agent table in Phase 3).

{phase2_header}
   a. Reads {staging_abs}/_assessment/quick_scan/{{group}}.md -- THAT group's pre-computed
      risk flags -- and calibrates analysis depth accordingly (CRITICAL/HIGH flags warrant
      deeper investigation). This file also states the group's priority rank. There is one
      such file per evidence file named in the work plan, so a split group has one per part.
      Do NOT read the full _assessment/quick_scan.md for this: it carries all {total_groups}
      groups' flags and a worker needs only those of the groups it owns. It remains available
      if you genuinely need another group's flags for a cross-group observation.
   b. Reads {staging_abs}/_preprocess/{analysis_guide_file} for analysis guidance, and
      {staging_abs}/_analysis/_shared_library.md if the pre-pass produced one -- that is
      where the definition of any joblet or routelet this group calls lives.
   c. Reads {staging_abs}/_preprocess/{{group}}.md for ALL scan data.
      If the group is split ({{group}}_part1.md and {{group}}_part2.md exist), read BOTH parts.
      IMPORTANT: If a file is >500 lines, do not Read it in one call -- walk it in
      successive windows with `Bash sed -n 'M,Np' file` (or `tail -n +M | head -N`) until
      you reach the end of the file. `Bash cat file | head -N` alone reads the FIRST window
      and silently leaves the rest of your evidence unread.
      Read in parallel as widely as you like. If a batch of reads fails, retry the failed
      reads individually -- never drop evidence because one call in a batch failed.
   d. Analyzes the pre-processed data across ALL dimensions defined in the analysis guide.
   e. Writes the analysis as ONE FILE PER SECTION, into a directory named for the
      group: {staging_abs}/_analysis/{{group}}/
{section_file_examples}
      Create the directory first: Bash mkdir -p {staging_abs}/_analysis/{{group}}
      THE FILE NAME IS THE CONTRACT; the format INSIDE a file is yours to choose.
      Nothing downstream looks inside a section file for structure, so write each one
      the way its evidence reads best -- tables, prose, code fences, any heading shape.
      What is not yours to choose is the name: the section name in CAPITALS, then .md.
      Not components.md, not 01_COMPONENTS.md, not one file holding every section.
{sections_owed}
      A SECTION YOU FOUND NOTHING FOR STILL GETS ITS FILE, and it is a short one:
      say what you looked at and what was not there. "No SQL in this group: none of
      the 14 designs uses tDBInput, tDBRow or tELT*; the three tFileInput components
      read flat files." Do not leave the file out, and do not pad it into a finding.
      A MISSING FILE MEANS NOBODY LOOKED. A short file means somebody looked and
      there was nothing to find. Those are different facts, every gate after this one
      needs to tell them apart, and one file per group made them indistinguishable.
      (MAXIMUM: {analysis_target_kb}KB for the group's section files TOGETHER.)
      If that cap binds, EVERY job in the group must
      still be named, with its components and its role -- shorten the prose, never drop a
      unit. A unit missing here is a unit missing from every document downstream. Record
      the depth you had to sacrifice in the open-questions ledger.
   f. Writes a concise summary (max 50 lines) to {staging_abs}/_analysis/{{group}}/_summary.md
      containing: job count, key findings, top risks, critical expressions/rules discovered.
      The leading underscore keeps it out of the section count. Phase 3 agents use this
      summary to decide WHICH analyses to open. It is a navigation aid; it does not
      replace the section files as the evidence of record.
   g. Returns a ~2KB navigation summary to the orchestrator (group name, job count, key findings).

   ONE ANALYSIS DIRECTORY PER GROUP, ALWAYS. A worker covering four groups writes four
   {{group}}/ directories, each with its own section files and its own _summary.md. Never
   merge two groups into one directory, never widen a section file to cover a group it is
   not named for, and never let a group you own go unwritten because you ran out of room
   -- report it instead (see Phase 2.5).
   The component names under {{group}}/ must come from THAT group's own evidence. Holding
   four groups at once is exactly how a component that is real in one of them gets written
   into another's analysis, and a later check compares each file against its own group's
   evidence alone, so a sibling's evidence will not excuse it.

   If a group is split into parts (e.g. USA_part1.md and USA_part2.md), ONE worker owns both
   parts -- the work plan already assigns them together -- and merges them into a single
   {staging_abs}/_analysis/{{group}}/ directory: one set of section files covering both
   parts, and one _summary.md. Never one directory per part.

   THERE MUST BE ONE FILE FOR EVERY SECTION defined in the analysis guide
   ({analysis_guide_file}) -- either written from evidence, or written to state what was
   looked at and not found.
   Be EXHAUSTIVE. Include every expression, every rule, every column. Depth over brevity.

   IMPORTANT for sub-agents:
   - You own a SET of groups. Work through them one at a time and finish each group's
     directory -- every section file, then _summary.md -- before starting the next, so
     an interruption costs one group, not all of them.
   - Read _assessment/quick_scan/{{group}}.md first for each group, to know which risks to prioritize.
   - Read the _preprocess/{{group}}.md file for each group you own -- it has ALL the data you need.
   - Read {staging_abs}/_preprocess/{analysis_guide_file} for structured analysis guidance.
   - Read {staging_abs}/_analysis/_shared_library.md if it exists: it is the only place the
     interfaces of the reusable designs (joblets, routelets) your groups call are written down.
   - Do NOT call fetch_chunk(), fetch_json, get_analysis_prompts(), get_reference_docs(), or any MCP tools.
   - Do NOT use TodoWrite or ASCII validation scripts. The ONLY mkdir you may run is
     the one in step e, for the group directory you are writing section files into.
     `wc -l` is allowed for sizing a file before a windowed read; `ls` only where a
     gate step below calls for it.
   - Do NOT read .agent/ docs, CLAUDE.md, or mcp-workflow.md.
   - PROHIBITED: Do NOT read any files outside {staging_abs}/. This is a security
     boundary and it is absolute. It says nothing about which files INSIDE
     {staging_abs}/ you may read: inside the staging directory, read whatever your
     claims need.
   - COPY, NEVER RECONSTRUCT. Every code body, tMap expression, SQL statement, schema
     column, context variable and configuration value you put in the analysis must be
     copied character for character out of {staging_abs}/_preprocess/. In a published
     run only 16% of the quoted code lines existed verbatim in the scan; the other 84%
     were reconstructed from memory and reached the client as fact. If you cannot find
     the literal text, write what is known and record the gap -- do not produce a
     plausible-looking value.
   - Record what you could NOT determine, and the evidence that would settle it, by
     APPENDING to {staging_abs}/_knowledge/open-questions.md (see UNRESOLVED EVIDENCE
     in Phase 3 for the entry shape and the append command). Several agents write that
     file; never rewrite it with the Write tool.
   - Write each section file using the Write tool, to
     {staging_abs}/_analysis/{{group}}/{{SECTION}}.md -- one Write per section.
   - Write the summary file using the Write tool to
     {staging_abs}/_analysis/{{group}}/_summary.md.

PHASE 2.5 -- GATE VALIDATION + INDEX BUILD (before Phase 3):
   After ALL Phase 2 sub-agents return, validate the analysis output. One directory per
   group and one file per section makes this a COUNT, not a judgement about what is
   inside a file: what a group produced is what `ls` says it produced.
   1. Run: Bash ls -la {staging_abs}/_analysis/
   2. Check PER GROUP, never per worker, against THIS checklist -- the
      {expected_analysis_count} group(s) this estate owes an analysis:
        {expected_analysis_list}
      For each name on it, count its section files:
        Bash ls -1 {staging_abs}/_analysis/{{group}}/ | grep -c '^[A-Z].*[.]md$'
{gate_expected_count}
      A group with no directory counts as 0 sections. That is not a rounding error; it
      is a group nobody analysed.
      _summary.md is NOT a section: the leading underscore is what keeps it out of the
      count above, and a group whose only file is its summary has NOT been analysed. A
      published run counted summaries as analyses and reported 30 analyses where 15
      existed, against a 20-group estate; it skipped Phase 2 and shipped documents with
      a quarter of the estate missing. Check the checklist, not the listing's length.
      A worker that returned "done" having written three of its four groups passes a
      per-worker check and fails this one; that is the point of checking per group.
      A group analysed by an earlier run may be one {{group}}.md file instead of a
      {{group}}/ directory. That counts as analysed; leave it alone.
   3. If a group's directory is MISSING, or holds no section file, or its section files
      total under 1KB:
      - Log which group(s) failed, and say which worker owned each of them.
      - Re-spawn a sub-agent for that group ONLY (same instructions as Phase 2), not for
        the whole batch it came from -- the batch's other groups are already written.
        Max 1 retry per group.
      - If a worker died mid-batch, its unwritten groups are separate failures with
        separate retries, not one failure.
   4. If a group has a directory but is missing SOME of its section files, name the
      missing sections in the log and re-spawn ONE sub-agent for that group to write
      exactly those files. It must not rewrite the files that are already there. Max 1
      retry per group. A section with nothing to report is still written down (see
      Phase 2 step e), so a missing file means nobody looked, never "nothing to say".
   5. After retries, if any group still has no analysis: proceed with available data but
      log a WARNING that names every group and every section still missing.
   6. If any group is missing its _summary.md, write a minimal one-paragraph summary by
      reading the first 30 lines of its largest section file.
   7. Only proceed to Phase 3 once this gate passes.

{phase26_block}
PHASE 2.8 -- BUNDLE ANALYSIS (after Phase 2.5/2.6, before Phase 3):
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

   Each Phase 3 agent STARTS from its pre-bundled analysis file, {staging_abs}/_analysis/_bundle_{{agent}}.md.
   If that file does not exist the bundle was auto-split: read BOTH _bundle_{{agent}}_a.md and
   _bundle_{{agent}}_b.md -- each half carries different groups, and one half is half the estate.
   Each Phase 3 agent also reads {staging_abs}/_analysis/_index.md FIRST to navigate to needed groups.

   THE BUNDLE IS THE STARTING POINT, NOT THE EVIDENCE OF RECORD.
   A bundle is a size-capped (60KB), section-filtered extract; the full group analyses are
   several times larger. The evidence of record is the section files under
   {staging_abs}/_analysis/{{group}}/, and _index.md exists precisely to tell you which
   group to open. OPEN the group analysis whenever
   you need to check a claim, quote code, name a component, a column, a context variable or a
   schedule, or whenever the bundle reads thin for a group you must document. The file name
   says which section carries the fact: SOURCE_SQL.md for a query, TRANSFORMATION_DETAIL.md
   for a tMap expression, ENVIRONMENT_CONFIG.md for a context variable. A group's
   _summary.md is a navigation aid, not evidence.
   Writers who were kept away from the group analyses invented a SOAP namespace, a token
   context variable and a component type that do not exist in the platform -- while the real
   values sat in the analyses they were forbidden to open. If a fact is not in your bundle,
   go and read it.
   Each Phase 3 agent also reads {staging_abs}/_assessment/scorecard.md for quantitative grounding.
   Each Phase 3 agent also reads {staging_abs}/_assessment/quick_scan.md for pre-computed risk flags.
   Each doc agent also reads its REFERENCE DOC BUNDLE from _preprocess/ for format templates.
   Each Phase 3 agent also reads {staging_abs}/_preprocess/_phase3_guide.md for cross-group synthesis.
   Each doc agent writes final .md files to {staging_abs}/.

{agent_table}

   Each sub-agent prompt MUST include:
   - "Read {staging_abs}/_analysis/_index.md first to understand what's available."
   - "Read {staging_abs}/_analysis/_bundle_{{your_agent}}.md; if it is absent, read both
     _bundle_{{your_agent}}_a.md and _bundle_{{your_agent}}_b.md."
   - "Open {staging_abs}/_analysis/{{group}}/{{SECTION}}.md for any group whose facts you
     quote, count or verify. The bundle is a filtered extract; the group analysis is the
     evidence of record. _index.md lists the groups, and a group written by an earlier
     run is one {{group}}.md file rather than a directory."
   - "Read {staging_abs}/_assessment/scorecard.md for quantitative scores."
   - "Read {staging_abs}/_assessment/quick_scan.md for pre-computed risk flags."
   - "Read ALL reference-doc bundles named in your row of the agent table above
     (in {staging_abs}/_preprocess/) for format reference."
   - "Read {staging_abs}/_preprocess/_doc_templates.md for required section structure."
   - "Read {staging_abs}/_preprocess/_phase3_guide.md for cross-group synthesis guidance."
   - Do NOT call get_reference_docs() or get_analysis_prompts() -- use the pre-bundled files.
   - PROHIBITED: Do NOT read any files outside {staging_abs}/. That is a security boundary
     and it is absolute. Inside {staging_abs}/ there is no such restriction: read whatever
     your claims need.
   - For a file >500 lines, do not Read it in one call -- walk it in successive windows with
     `Bash sed -n 'M,Np' file` until you reach the end. A single `head -N` reads the first
     window only and leaves the rest of the evidence unread.
   - Read in parallel as widely as you like. If a batch of reads fails, retry the failed
     reads individually; never drop evidence because one call in a batch failed.
   - Copy the four blocks below -- COVERAGE OBLIGATION, EVIDENCE GROUNDING, CLIENT PROSE
     and UNRESOLVED EVIDENCE -- VERBATIM into every doc sub-agent prompt. A writer that
     never receives them pads, reconstructs quotes from memory, names internal files in
     client prose and has nowhere to put doubt.
   SCORECARD INTEGRATION:
   - Reference specific scores in your documents, and name the published document that
     carries them, never an internal file.
   - Example: "Security: 2/5 (see 07_QUALITY_SECURITY_RISK_SCORECARD)"
   - Include anti-pattern IDs and red flag IDs where relevant.
   - If the scores were not computed for this run, say so where a score would go. Do not
     invent a number to fill the shape of a scorecard.
   - The Scorecard doc (doc 07) MUST include scores, confidence, evidence, red flags, quick wins, and unknowns.
   - The Change Playbook (doc 08) MUST include inspect/test/rollback guidance.
   - The Agent Brief (doc 00) MUST include onboarding and documentation map guidance.

   COVERAGE OBLIGATION (there are no line targets -- coverage is the measured property):
   This project has {total_jobs} scanned designs across {total_groups} groups.
   - WHAT THAT COUNT CONTAINS. It counts every scanned design, of every kind: the ones
     that run on their own AND the reusable ones that exist only to be called by another
     design (on this platform, joblets and routelets). The per-kind breakdown for this
     estate is in {staging_abs}/_assessment/phase2_work_plan.md. Do not assume the total
     is all standalone jobs -- on a measured estate nearly a fifth of it was reusable.
   - A reusable design is ACCOUNTED FOR by naming it and what calls it. It does not need
     the full standalone treatment a runnable design gets. Naming it in a row that says
     what it is and which designs invoke it satisfies this obligation.
   - Never pad a table with sub-components, steps or sub-jobs to make the numbers reach
     the total. A row must be a scanned design.
   - If you judge a whole CLASS of artifact out of scope for the document you are writing,
     say so in the omission list -- "the 22 joblets are covered in <doc> rather than here"
     is a listed omission and is fine. Dropping them silently is not.
   - A document that claims estate-wide scope -- 02_COMPONENT_INVENTORY above all, then
     01_ARCHITECTURE_MAP and 03_DATA_FLOWS_AND_LINEAGE -- must ACCOUNT FOR EVERY scanned
     design in its scope. Accounted for means: named in a row of a table, or named in an
     explicit omission list. Nothing may be silently absent.
   - Deliberate omissions are ACCEPTABLE and must be LISTED. Say which designs you left
     out and why (test harnesses, Copy_of_* duplicates, generated stubs, empty shells).
     An explicit, listed omission is fine. A silent one is a defect.
   - Copy_of_* duplicates and test-named artifacts are the canonical examples: an estate
     commonly carries a handful of each, and they belong in the omission list by name,
     not in the inventory as if they were production designs and not silently dropped.
   - Never write "all N designs" over a partial list. A published inventory claimed
     "All 142 jobs" and named 78 of them. Length was the measured thing, so a 55%
     coverage gap shipped unnoticed.
   - Before you finish a document with estate-wide scope, count the designs you named
     and compare that to the design count for the groups in scope. If the numbers differ,
     add the missing rows or list them as omissions. State the count you actually named.
   - Write as much as needed to cover every relevant finding. Do NOT pad.
   - Do NOT truncate to hit a target. Include everything relevant.
   - Depth comes from ACTUAL content (expressions, rules, schemas, risks), not filler.
   - Every claim needs a specific job name and component reference.
   - Show actual code/expressions/SQL, then explain. Never just summarize.

   DEPTH FLOOR -- A SHORT DOCUMENT MEANS EVIDENCE WAS MISSED:
   Scaled to this estate's {total_jobs} scanned designs, and graded after the run
   against these same numbers:
{depth_floor_table}
   Read the floor as a DETECTOR, not as a quota. It is roughly what covering this
   estate honestly costs, so a document that lands under its floor is telling you
   that designs, expressions, columns, schemas or risks were left out: go back to
   the evidence and put them in. It is NEVER a reason to stretch prose. Padding to
   reach a floor is the worse failure of the two, because filler is indistinguishable
   from content to the reader and it buries the findings that are real.
   A floor is not a ceiling. A document with more to say says more. Depth is
   enumerated evidence and nothing else: Do NOT pad, and Do NOT truncate to hit
   a target.
   Measured: one published run wrote a 76-line project brief for a {total_groups}-group
   estate because nothing in these instructions said what covering it costs.

   EVIDENCE GROUNDING -- COPY, NEVER RECONSTRUCT:
   - Every quoted code block, expression, SQL statement, schema column, context variable,
     endpoint and configuration value must be COPIED character for character from the
     evidence in front of you (your bundle, or the group analysis you opened). If you did
     not copy it, do not quote it.
   - In a published run only 16% of the quoted code lines existed verbatim anywhere in the
     scan. The other 84% were reconstructed from memory: invented namespaces, invented
     context variables, invented component types -- indistinguishable from facts to the
     reader, and wrong.
   - If you cannot find the literal text, write what IS established and record the gap (see
     UNRESOLVED EVIDENCE below). A plausible-looking reconstruction is the worst outcome
     available to you; an honest gap is a correct one.

   CLIENT PROSE -- NAME THE ESTATE, NEVER THE MACHINERY:
   - The published documents name Talend designs, components, jobs, tables, systems and
     business objects. They NEVER name the machinery that produced them.
   - PROHIBITED anywhere in a published .md document: staging or other internal directory
     paths; the names of internal working files (_analysis, _assessment, _preprocess,
     _bundle, _ref, _provenance, quick_scan, docs_staging, scan_result.json); and any
     phrase of the form "based on our internal analysis", "per the analysis in ...",
     "the analysis file states", "according to the bundle", "as bundled", or a reference
     to a phase, a sub-agent or these instructions.
   - Cite evidence the way an engineer would, in the prose itself: name the DESIGN and the
     COMPONENT ("OrderLoad_Daily, tMSSqlOutput_1"), never the file you read it from.
   - Machine-checkable provenance goes in a sidecar the reader never sees. Write exactly one
     JSON file per document you produce, to {staging_abs}/_provenance/{{DOC_NAME}}.json
     (DOC_NAME is the document's filename without .md), with this shape:
       {{
         "document": "02_COMPONENT_INVENTORY",
         "entries": [
           {{
             "claim": "<the quoted block or protected fact, first 200 chars>",
             "group": "<group name>",
             "unit": "<design or job name, or null for a cross-group fact>",
             "components": ["tMSSqlOutput_1", "tMap_3"],
             "source": "_analysis/<group>/<SECTION>.md",
             "verbatim": true
           }}
         ]
       }}
   - One entry per quoted code block, expression, SQL statement, configuration value and
     protected fact (counts, schedules, environments, coverage numbers). A quoted block
     with no sidecar entry counts as unsourced.
   - "components" lists the component instances the claim rests on, copied from the
     evidence for that "group" -- the ones you actually read to make the claim, not every
     name the sentence mentions. Use [] when the claim rests on no single component (a
     count, a group-wide statement). This is the one field checked mechanically: each name
     is looked up in that group's own evidence, so a name that is not in it is reported
     against you by name, and naming a component you did not read is worse than naming
     none. Declaring them is also cheaper than it looks -- you have the evidence open at
     the moment you write the claim, and nothing reconstructs them afterwards.

   UNRESOLVED EVIDENCE -- DOUBT HAS SOMEWHERE TO GO:
   - Across 302 KB of published output, a previous run said "unavailable" essentially zero
     times. That was not confidence. There was no channel for doubt, so doubt was published
     as confident prose.
   - In the document: "not determinable from the scanned sources" is a CORRECT and wanted
     answer. Say which design and which component the gap sits in, and what IS established.
     Runtime behaviour, throughput, live schedules and operator intent are routinely not
     determinable from a code scan. Say so.
   - In the run ledger: APPEND one entry per open question to
     {staging_abs}/_knowledge/open-questions.md, in this shape:
       ## <the question, one line>
       - Document: <document name>
       - Scope: <group / design / component>
       - Established: <what the evidence does show>
       - Would settle it: <the exact evidence needed>
   - Append with a heredoc, never the Write tool:
       Bash cat >> {staging_abs}/_knowledge/open-questions.md <<'EOF'
       ... entry ...
       EOF
     Several agents write that file in parallel; a read-modify-write destroys the others'
     entries.

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
   - Do NOT delete {staging_abs}/_analysis/ or {staging_abs}/_preprocess/. The server
     removes both after the run, once the deterministic checks that read them have run.
     Deleting your own evidence before it can be checked is how a check stops existing.
     Neither directory is published: a leading underscore keeps it out of the client
     corpus and out of the document count.
   - KEEP {staging_abs}/_assessment/scorecard.md -- it is part of the final output.
   - KEEP {staging_abs}/_assessment/quick_scan.md -- it is part of the final output.
   - KEEP {staging_abs}/_provenance/ -- the per-document provenance sidecars. They are
     checked after the run and are never shown to the reader.
   - Move the unresolved-evidence ledger under an underscore-prefixed directory, so it
     stays an internal record and is not published as a client document:
     A markdown file under a directory whose name does not start with "_" is counted as a
     delivered document and pushed to the client corpus. Skip this step only if the ledger
     was never created (no agent recorded an open question).
   - Write {staging_abs}/index.json mapping docs to groups. The index MUST include all produced docs with their filenames exactly as written.
   - Write {logs_abs}/run_{project_id}_{{timestamp}}.log.
   - Do NOT call insert_to_rag -- the server will push docs to RAG automatically after successful generation.
{hindsight_phase4}
PROHIBITED ACTIONS:
   - Do NOT use TodoWrite tool.
   - Do NOT run ASCII/Unicode validation scripts.
   - Do NOT read .agent/ docs or CLAUDE.md or mcp-workflow.md.
   - Do NOT create _scratch/ directories or other intermediate files. The _analysis/,
     _assessment/, _preprocess/, _provenance/ and _knowledge/ directories are allowed.
   - Do NOT run mkdir except for {staging_abs}/_provenance/, {staging_abs}/_knowledge/
     and the per-group {staging_abs}/_analysis/{{group}}/ directories Phase 2 writes its
     section files into -- every other directory is pre-created. `ls` and `wc -l`
     are allowed only where a phase or gate step above calls for them, or to size a file
     before a windowed read.
   - Do NOT call get_analysis_prompts(), get_reference_docs(), get_scan_status, get_chunks, fetch_json, fetch_chunk, trigger_scan, or any other MCP/fetch tools.
     ALL scan data, prompts, and reference docs are pre-bundled in _preprocess/.
   - Only final .md docs and the _analysis/, _assessment/, _preprocess/, _provenance/ and
     _knowledge/ contents go to {staging_abs}/.

FILE READING GUIDANCE:
   - For files > 500 lines, do not Read the whole file in one call. Walk it in successive
     windows with `Bash sed -n 'M,Np' file` or `tail -n +M | head -N` until you reach the
     end of the file. Stopping after the first window leaves evidence unread; a partial
     read is how a document ends up describing an estate it never saw.
   - Read in parallel as widely as the work needs. If a batch of reads fails, retry the
     failed reads individually -- a failed call is a call to repeat, never a reason to
     proceed without the evidence.
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
