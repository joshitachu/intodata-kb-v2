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

from pathlib import Path

from mcp_server.config import config
from mcp_server.services.log_manager import LogManager
from mcp_server.services.scanner_service import ScannerService
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

    @staticmethod
    def _format_chunk_markdown(
        scanner: ScannerService, output_dir: str, chunks: list[dict],
    ) -> tuple[list[str], int]:
        """Process chunks into markdown parts. Returns (parts, job_count)."""
        parts: list[str] = []
        job_count = 0

        for chunk in chunks:
            chunk_data = scanner.read_chunk_data(output_dir, chunk["chunk_id"])
            if "error" in chunk_data:
                parts.append(f"\n## Chunk {chunk['chunk_id']} — ERROR: {chunk_data['error']}\n")
                continue

            for job in chunk_data.get("jobs", []):
                job_count += 1
                job_name = job.get("name", "unknown")
                parts.append(f"\n---\n## Job: {job_name}\n")

                # Components inventory
                components = job.get("components", [])
                if components:
                    parts.append("\n### Components\n")
                    for comp in components:
                        ctype = comp.get("component_type", "?")
                        cname = comp.get("unique_name", "?")
                        parts.append(f"\n#### {cname} ({ctype})\n")

                        # Key params
                        params = comp.get("params", [])
                        if isinstance(params, list):
                            for p in params:
                                pname = p.get("name", "")
                                pval = p.get("value", "")
                                if not pval or pname in ("UNIQUE_NAME", "CODEGENERATION"):
                                    continue
                                if len(str(pval)) > 5000:
                                    parts.append(f"- **{pname}**:\n```\n{pval}\n```\n")
                                else:
                                    parts.append(f"- **{pname}**: `{pval}`\n")

                        # Connections
                        for direction, key in [("Incoming", "incoming_connections"),
                                               ("Outgoing", "outgoing_connections")]:
                            conns = comp.get(key, [])
                            if conns:
                                parts.append(f"- {direction} connections: {json.dumps(conns)}\n")

                        # Metadata connection ref
                        ref = comp.get("metadata_connection_ref")
                        if ref:
                            parts.append(f"- Metadata connection: `{ref}`\n")

                # Context variables
                ctx_vars = job.get("context_variables", [])
                if ctx_vars:
                    parts.append("\n### Context Variables\n")
                    for cv in ctx_vars:
                        cv_name = cv.get("name", "?")
                        cv_val = cv.get("value", "")
                        cv_type = cv.get("type", "")
                        prompt_val = cv.get("prompt_value", "")
                        line = f"- `{cv_name}` ({cv_type}): `{cv_val}`"
                        if prompt_val and prompt_val != cv_val:
                            line += f" (prompt: `{prompt_val}`)"
                        parts.append(line + "\n")

                # Routines
                routines = job.get("raw_routines", [])
                if routines:
                    parts.append(f"\n### Routines used: {', '.join(routines)}\n")

            # Related connections for this chunk
            related_conns = chunk_data.get("related_connections", [])
            if related_conns:
                parts.append(f"\n---\n## Connections (chunk {chunk['chunk_id']})\n")
                for conn in related_conns:
                    conn_name = conn.get("name", "?")
                    parts.append(f"\n### Connection: {conn_name}\n")
                    for k, v in conn.items():
                        if k == "name":
                            continue
                        parts.append(f"- **{k}**: `{v}`\n")

            # Related contexts for this chunk
            related_ctx = chunk_data.get("related_contexts", [])
            if related_ctx:
                parts.append(f"\n---\n## Context Groups (chunk {chunk['chunk_id']})\n")
                for ctx in related_ctx:
                    ctx_name = ctx.get("name", "?")
                    parts.append(f"\n### Context Group: {ctx_name}\n")
                    for var in ctx.get("variables", ctx.get("items", [])):
                        vname = var.get("name", "?")
                        vval = var.get("value", "")
                        vtype = var.get("type", "")
                        vprompt = var.get("prompt_value", "")
                        line = f"- `{vname}` ({vtype}): `{vval}`"
                        if vprompt and vprompt != vval:
                            line += f" (prompt: `{vprompt}`)"
                        parts.append(line + "\n")

            # Manifest for this chunk
            manifest = chunk_data.get("manifest", {})
            if manifest:
                parts.append(f"\n---\n## Manifest (chunk {chunk['chunk_id']})\n")
                for section, data in manifest.items():
                    if data:
                        parts.append(f"\n### {section}\n```json\n{json.dumps(data, indent=2)}\n```\n")

        return parts, job_count

    async def _preprocess_scan_data(
        self, project_id: str, staging_dir: Path, platform: str = "talend",
    ) -> list[str]:
        """Pre-process raw job JSONs into structured markdown summaries.

        Reads scan data via ScannerService and writes per-group markdown files
        to staging_dir/_preprocess/{group}.md. Also bundles analysis prompts
        and reference docs for sub-agents.

        Returns list of group names that were preprocessed.
        """
        preprocess_dir = staging_dir / "_preprocess"
        preprocess_dir.mkdir(parents=True, exist_ok=True)
        preprocess_dir.chmod(0o777)

        scanner = ScannerService()
        output_dir = str(config.intodoc_output_dir / project_id)

        # Get chunk definitions
        chunk_defs = scanner.get_chunk_definitions(output_dir)
        if "error" in chunk_defs:
            logger.warning("Pre-process: %s", chunk_defs["error"])
            return []

        # Group chunks by group name
        groups: dict[str, list[dict]] = {}
        for chunk in chunk_defs["chunks"]:
            group = chunk["group"]
            groups.setdefault(group, []).append(chunk)

        processed_groups: list[str] = []
        for group, chunks in sorted(groups.items()):
            total_jobs = sum(c["job_count"] for c in chunks)

            # Split large groups (>40 jobs) into parts for parallel Phase 2
            if total_jobs > 40:
                cumulative = 0
                split_idx = len(chunks)
                target = total_jobs // 2
                for i, c in enumerate(chunks):
                    cumulative += c["job_count"]
                    if cumulative >= target:
                        split_idx = i + 1
                        break
                chunk_slices = [
                    (f"{group}_part1", chunks[:split_idx]),
                    (f"{group}_part2", chunks[split_idx:]),
                ]
                logger.info(
                    "Splitting group %s (%d jobs) into 2 parts at chunk %d",
                    group, total_jobs, split_idx,
                )
            else:
                chunk_slices = [(group, chunks)]

            for slice_name, slice_chunks in chunk_slices:
                if not slice_chunks:
                    continue
                slice_jobs = sum(c["job_count"] for c in slice_chunks)
                header = (
                    f"# Pre-processed Scan Data: {slice_name}\n"
                    f"Chunks: {len(slice_chunks)} | Jobs: {slice_jobs}\n"
                )
                body_parts, actual_jobs = self._format_chunk_markdown(
                    scanner, output_dir, slice_chunks,
                )
                content = header + "".join(body_parts)
                (preprocess_dir / f"{slice_name}.md").write_text(
                    content, encoding="utf-8",
                )
                processed_groups.append(slice_name)
                logger.info(
                    "Pre-processed group %s: %d bytes, %d jobs",
                    slice_name, len(content), actual_jobs,
                )

        # --- Bundle analysis guide for Phase 2 sub-agents ---
        prompts_data = scanner.get_analysis_prompts(platform)
        if "prompts" in prompts_data:
            if prompts_data.get("consolidated"):
                # New path: single ANALYSIS_GUIDE.md
                guide_content = list(prompts_data["prompts"].values())[0]
                (preprocess_dir / "_analysis_guide.md").write_text(
                    guide_content, encoding="utf-8",
                )
                logger.info(
                    "Bundled analysis guide (%s): %d bytes",
                    platform, len(guide_content),
                )
            else:
                # Legacy fallback: bundle individual .txt prompt files
                MAX_PROMPT_BUNDLE_BYTES = 200_000
                header = (
                    "# Analysis Prompts\n\n"
                    "Use these structured prompts to guide your analysis "
                    "of each group's scan data.\n\n"
                )
                entries = []
                for name, content in sorted(prompts_data["prompts"].items()):
                    entries.append(f"---\n## {name}\n\n{content}\n\n")

                parts: list[list[str]] = [[]]
                part_size = 0
                for text in entries:
                    text_bytes = len(text.encode("utf-8"))
                    if part_size + text_bytes > MAX_PROMPT_BUNDLE_BYTES and parts[-1]:
                        parts.append([])
                        part_size = 0
                    parts[-1].append(text)
                    part_size += text_bytes

                for i, part in enumerate(parts):
                    suffix = f"_{i + 1}" if len(parts) > 1 else ""
                    bundle = header + "".join(part)
                    (preprocess_dir / f"_analysis_prompts{suffix}.md").write_text(
                        bundle, encoding="utf-8",
                    )
                    logger.info(
                        "Bundled analysis prompts%s (%s): %d bytes",
                        suffix, platform, len(bundle),
                    )
        else:
            logger.warning(
                "Analysis prompts NOT bundled for %s: %s",
                platform, prompts_data.get("error", "unknown"),
            )

        # --- Bundle Phase 3 guide for cross-group synthesis ---
        phase3_data = scanner.get_phase3_guide(platform)
        if "content" in phase3_data:
            (preprocess_dir / "_phase3_guide.md").write_text(
                phase3_data["content"], encoding="utf-8",
            )
            logger.info(
                "Bundled Phase 3 guide (%s): %d bytes",
                platform, len(phase3_data["content"]),
            )
        else:
            logger.warning(
                "Phase 3 guide NOT bundled for %s: %s",
                platform, phase3_data.get("error", "unknown"),
            )

        # --- Bundle reference docs per Phase 3 agent ---
        MAX_REF_BUNDLE_BYTES = 80_000  # ~20K tokens, safely under 25K token limit
        # Resolve from source tree (not config.project_root which may be /opt/)
        # __file__ -> services/ -> mcp_server/ -> src/ -> mcp-server/ -> knowledge_base/
        _source_root = Path(__file__).resolve().parent.parent.parent.parent.parent
        ref_dir = _source_root / "intodoc" / "reference-docs"
        if ref_dir.exists():
            agent_ref_map = {
                "_ref_core": [
                    "00_EXECUTIVE_SUMMARY", "01_PROJECT_OVERVIEW",
                    "02_ARCHITECTURE_INVENTORY", "03_DATA_FLOWS_LINEAGE",
                ],
                "_ref_business_ops": [
                    "04_BUSINESS_LOGIC", "05_OPERATIONS_RISK",
                    "06_ENVIRONMENT_EXTERNAL",
                ],
                "_ref_patterns_user": [
                    "07_SECURITY_PERFORMANCE", "08_PATTERNS_STANDARDS",
                    "09_RUNBOOK", "10_ONBOARDING_FAQ",
                    "11_MIGRATION_ASSESSMENT",
                ],
            }
            for bundle_name, doc_names in agent_ref_map.items():
                header = (
                    f"# Reference Docs Bundle: {bundle_name}\n\n"
                    "These are example reference docs showing the expected "
                    "format, depth, and structure for each document type.\n\n"
                )
                entries = []
                for dn in doc_names:
                    ref_file = ref_dir / f"{dn}.md"
                    if ref_file.exists():
                        entries.append(f"---\n## {dn}\n\n{ref_file.read_text(encoding='utf-8')}\n\n")

                # Split into parts that stay under the size limit
                parts: list[list[str]] = [[]]
                part_size = 0
                for text in entries:
                    text_bytes = len(text.encode("utf-8"))
                    if part_size + text_bytes > MAX_REF_BUNDLE_BYTES and parts[-1]:
                        parts.append([])
                        part_size = 0
                    parts[-1].append(text)
                    part_size += text_bytes

                for i, part in enumerate(parts):
                    suffix = f"_{i + 1}" if len(parts) > 1 else ""
                    bundle_content = header + "".join(part)
                    (preprocess_dir / f"{bundle_name}{suffix}.md").write_text(
                        bundle_content, encoding="utf-8",
                    )
                    logger.info(
                        "Bundled %s%s: %d bytes", bundle_name, suffix, len(bundle_content),
                    )
        else:
            logger.warning("Reference docs dir not found: %s", ref_dir)

        return processed_groups

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

        # Absolute paths for the prompt (subprocess cwd differs from config.project_root)
        staging_abs = str(staging_dir)
        logs_abs = str(output_dir / "logs")

        # Look up the project's platform from the DB
        from mcp_server.db.session import async_session_factory
        from mcp_server.services.project_registry import ProjectRegistry

        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            project = await registry.get_project(project_id)
            platform = project.platform if project else "talend"

        # Pre-process scan data into structured markdown (saves ~15 min of agent parsing)
        await self.log_manager.emit(
            project_id, "Pre-processing scan data...", level="info",
        )
        preprocess_groups = await self._preprocess_scan_data(project_id, staging_dir, platform)
        await self.log_manager.emit(
            project_id,
            f"Pre-processed {len(preprocess_groups)} groups: {', '.join(preprocess_groups)}",
            level="info",
        )

        # Compute project metrics for dynamic depth scaling
        scanner = ScannerService()
        scan_output_dir = str(config.intodoc_output_dir / project_id)
        chunk_defs = scanner.get_chunk_definitions(scan_output_dir)
        total_jobs = chunk_defs.get("total_jobs", 0)
        total_groups = len(preprocess_groups)

        # Compute scaled analysis target from preprocessed data size
        total_preprocess_bytes = sum(
            f.stat().st_size
            for f in (staging_dir / "_preprocess").glob("*.md")
            if not f.name.startswith("_")  # exclude guide/ref bundles
        )
        analysis_target_kb = max(5, min(40, total_preprocess_bytes // 2048))
        logger.info(
            "Analysis target: %dKB (from %d bytes preprocessed data)",
            analysis_target_kb, total_preprocess_bytes,
        )

        # Determine which analysis guide file the sub-agents should read
        analysis_guide_file = "_analysis_guide.md"
        if not (staging_dir / "_preprocess" / "_analysis_guide.md").exists():
            analysis_guide_file = "_analysis_prompts*.md"  # legacy fallback

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
{hindsight_phase1}   - All data is pre-processed and ready in {staging_abs}/_preprocess/.
   - Do NOT call get_scan_status, get_chunks, fetch_json, fetch_chunk, or any MCP tools.
     Everything needed is pre-bundled in the files below.
   - Directories {staging_abs}/, {staging_abs}/_analysis/, and logs/ are pre-created.
   - Pre-processed scan data: {staging_abs}/_preprocess/{{group}}.md for each group.
     If a group was too large, it is split into {{group}}_part1.md and {{group}}_part2.md.
     These files contain ALL job components, params, tMap expressions, SQL, connections, context vars,
     and manifest data in readable markdown format.
   - Analysis guide: {staging_abs}/_preprocess/{analysis_guide_file}
   - Phase 3 synthesis guide: {staging_abs}/_preprocess/_phase3_guide.md
   - Reference doc templates: {staging_abs}/_preprocess/_ref_*.md files per Phase 3 agent (may be split with _1, _2 suffixes -- read ALL matching files).

PHASE 2 -- GROUP ANALYSIS (parallel sub-agents):
   Spawn one sub-agent per group (all in parallel). Each sub-agent:
   1. Reads {staging_abs}/_preprocess/{analysis_guide_file} for analysis guidance.
   2. Reads {staging_abs}/_preprocess/{{group}}.md for ALL scan data.
      If the group is split ({{group}}_part1.md and {{group}}_part2.md exist), read BOTH parts.
   3. Analyzes the pre-processed data across ALL dimensions defined in the analysis guide.
   4. Writes a RICH structured analysis file to {staging_abs}/_analysis/{{group}}.md (target: ~{analysis_target_kb}KB).
   5. Returns a ~2KB navigation summary to the orchestrator (group name, job count, key findings, section list).

   If a group is split into parts (e.g. USA_part1.md and USA_part2.md), spawn one sub-agent per part,
   then merge their analyses into a single {staging_abs}/_analysis/{{group}}.md.

   The analysis file MUST contain ALL sections defined in the analysis guide ({analysis_guide_file}).
   Read the guide for detailed instructions on what to extract for each section.
   Be EXHAUSTIVE. Include every expression, every rule, every column. Depth over brevity.

   IMPORTANT for sub-agents:
   - Read the _preprocess/{{group}}.md file for your group -- it has ALL the data you need.
   - Read {staging_abs}/_preprocess/{analysis_guide_file} for structured analysis guidance.
   - Do NOT call fetch_chunk(), fetch_json, get_analysis_prompts(), get_reference_docs(), or any MCP tools.
   - Do NOT use TodoWrite, mkdir, ls, wc, or ASCII validation scripts.
   - Do NOT read .agent/ docs, CLAUDE.md, or mcp-workflow.md.
   - PROHIBITED: Do NOT read any files outside {staging_abs}/.
     Do NOT use Glob, Read, or Bash to access intodoc-output/, data/repos/, or any other directory.
     ALL scan data is pre-bundled in _preprocess/. Reading raw files is redundant and wastes time.
   - Write the analysis file using the Write tool to {staging_abs}/_analysis/{{group}}.md.

PHASE 2.5 -- GATE VALIDATION (before Phase 3):
   After ALL Phase 2 sub-agents return, validate the analysis output:
   1. Run: Bash ls -la {staging_abs}/_analysis/
   2. For each preprocessed group, verify its _analysis/{{group}}.md exists and is non-empty.
   3. If any analysis file is MISSING or under 1KB:
      - Log which group(s) failed.
      - Re-spawn a sub-agent for that group ONLY (same instructions as Phase 2). Max 1 retry per group.
   4. After retries, if any group still has no analysis: proceed with available data but log a WARNING
      listing the groups without analysis. Phase 3 agents should note incomplete data in their docs.
   5. Only proceed to Phase 3 once this gate passes.

PHASE 3 -- WRITE DOCS (parallel sub-agents by theme):
   After ALL Phase 2 sub-agents complete, spawn 3 doc-assembly sub-agents in parallel.
   Each Phase 3 agent reads the FULL _analysis/{{group}}.md files directly (no condensation step).
   Each doc agent also reads its REFERENCE DOC BUNDLE from _preprocess/ for format templates.
   Each Phase 3 agent also reads {staging_abs}/_preprocess/_phase3_guide.md for cross-group
   synthesis guidance (executive summary structure, cross-region comparison dimensions,
   migration assessment methodology).
   Each doc agent writes final .md files to {staging_abs}/.

   AUTO-FALLBACK: If total analysis size exceeds 400KB (check with: Bash du -sh {staging_abs}/_analysis/),
   each Phase 3 agent should read only the analysis sections relevant to its docs (use grep/head/tail).
   For most projects (~10 groups, ~300KB analysis), agents read ALL analysis files in full.

   The 3 doc-assembly agents:
   | Agent          | Analysis Input                          | Reference bundle          | Writes                                                                    |
   |----------------|-----------------------------------------|---------------------------|---------------------------------------------------------------------------|
   | Core           | ALL _analysis/{{group}}.md files        | _ref_core*.md             | 00_EXECUTIVE_SUMMARY, 01_PROJECT_OVERVIEW, 02_ARCHITECTURE_INVENTORY, 03_DATA_FLOWS_LINEAGE |
   | Business+Ops   | ALL _analysis/{{group}}.md files        | _ref_business_ops*.md     | 04_BUSINESS_LOGIC, 05_OPERATIONS_RISK, 06_ENVIRONMENT_EXTERNAL            |
   | Patterns+User  | ALL _analysis/{{group}}.md files        | _ref_patterns_user*.md    | 07_SECURITY_PERFORMANCE, 08_PATTERNS_STANDARDS*, 09_RUNBOOK, 10_ONBOARDING_FAQ, 11_MIGRATION_ASSESSMENT |
   *08_PATTERNS_STANDARDS cross-region section only if 2+ groups exist.

   Each sub-agent prompt MUST include:
   - "Read ALL files in {staging_abs}/_analysis/ for your analysis data."
   - "Read ALL _ref_{{agent}}*.md files in {staging_abs}/_preprocess/ for format reference templates."
   - "Read {staging_abs}/_preprocess/_phase3_guide.md for cross-group synthesis guidance."
   - Do NOT call get_reference_docs() or get_analysis_prompts() -- use the pre-bundled files.

   DEPTH GUIDANCE:
   This project has {total_jobs} jobs across {total_groups} groups.

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

   ADAPTIVE CATALOG:
   - Always generate: 00-06, 09-11
   - Generate 07 if security/performance issues found
   - Generate 08 cross-region section only if 2+ groups exist
   - Skip any doc with genuinely no content (but this should be rare with the consolidated structure)

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
   2. Required docs (must exist and be >500 bytes): 00_EXECUTIVE_SUMMARY, 01_PROJECT_OVERVIEW,
      02_ARCHITECTURE_INVENTORY, 03_DATA_FLOWS_LINEAGE, 04_BUSINESS_LOGIC, 05_OPERATIONS_RISK,
      09_RUNBOOK, 10_ONBOARDING_FAQ, 11_MIGRATION_ASSESSMENT.
   3. Optional docs (generate if content warrants): 06_ENVIRONMENT_EXTERNAL, 07_SECURITY_PERFORMANCE,
      08_PATTERNS_STANDARDS.
   4. If any REQUIRED doc is missing or under 500 bytes:
      - Identify which Phase 3 agent was responsible (Core, Business+Ops, or Patterns+User).
      - Re-spawn ONLY that agent with the same instructions. Max 1 retry per agent.
   5. After retries, proceed to Phase 4 with whatever docs exist. Log any still-missing docs as WARNINGS.

PHASE 4 -- CLEANUP & PUBLISH:
   - Delete the {staging_abs}/_analysis/ directory and all its contents.
   - Delete the {staging_abs}/_preprocess/ directory and all its contents.
   - Write {staging_abs}/index.json mapping docs to groups.
   - Write {logs_abs}/run_{project_id}_{{timestamp}}.log.
   - Do NOT call insert_to_rag -- the server will push docs to RAG automatically after successful generation.
{hindsight_phase4}
PROHIBITED ACTIONS:
   - Do NOT use TodoWrite tool.
   - Do NOT run ASCII/Unicode validation scripts.
   - Do NOT read .agent/ docs or CLAUDE.md or mcp-workflow.md.
   - Do NOT create _scratch/ directories or other intermediate files (the _analysis/ and _preprocess/ directories are allowed).
   - Do NOT run mkdir, ls, wc for verification -- directories are pre-created.
   - Do NOT call get_analysis_prompts(), get_reference_docs(), get_scan_status, get_chunks, fetch_json, fetch_chunk, trigger_scan, or any other MCP/fetch tools.
     ALL scan data, prompts, and reference docs are pre-bundled in _preprocess/.
   - Only final .md docs, _analysis/, and _preprocess/ files go to {staging_abs}/.

FILE READING GUIDANCE (prevents cascading failures):
   - For files > 500 lines, use `Bash cat file | head -N` or `tail -n +M | head -N` instead of the Read tool.
   - Never issue more than 3 parallel Read calls -- if one fails, ALL siblings cascade-fail.
   - For JSON files, use `python3 -c "import json; ..."` with json.load() to extract specific fields.
   - The _preprocess/ markdown files are designed to be readable without chunking.
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
        _subprocess_cwd = Path(__file__).resolve().parent.parent.parent.parent.parent
        logger.info(f"Spawning Claude Code: {cmd[0]} -p '...' --dangerously-skip-permissions  cwd={_subprocess_cwd}")
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
                cwd=str(_subprocess_cwd),
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
                usage["cost_usd"] = result_event.get("total_cost_usd")
                # Tokens are nested under usage{} — include cache tokens in total
                re_usage = result_event.get("usage", {})
                usage["input_tokens"] = (
                    re_usage.get("input_tokens", 0)
                    + re_usage.get("cache_read_input_tokens", 0)
                    + re_usage.get("cache_creation_input_tokens", 0)
                )
                usage["output_tokens"] = re_usage.get("output_tokens", 0)
                # Extract model name from modelUsage
                model_usage = result_event.get("modelUsage", {})
                if model_usage:
                    usage["model_name"] = next(iter(model_usage.keys()), "claude")

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
