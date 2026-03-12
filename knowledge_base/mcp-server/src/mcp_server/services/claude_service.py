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
        self, project_id: str, staging_dir: Path,
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

        # --- Bundle analysis prompts for Phase 2 sub-agents ---
        prompts_dir = (
            config.project_root / "intodoc" / "src" / "intodoc"
            / "platforms" / "talend" / "prompts"
        )
        if prompts_dir.exists():
            prompt_parts = [
                "# Analysis Prompts\n\n"
                "Use these structured prompts to guide your analysis "
                "of each group's scan data.\n\n"
            ]
            for pfile in sorted(prompts_dir.glob("*.txt")):
                prompt_parts.append(f"---\n## {pfile.stem}\n\n")
                prompt_parts.append(pfile.read_text(encoding="utf-8"))
                prompt_parts.append("\n\n")
            prompt_bundle = "".join(prompt_parts)
            (preprocess_dir / "_analysis_prompts.md").write_text(
                prompt_bundle, encoding="utf-8",
            )
            logger.info("Bundled analysis prompts: %d bytes", len(prompt_bundle))

        # --- Bundle reference docs per Phase 3 agent ---
        ref_dir = config.project_root / "intodoc" / "reference-docs"
        if ref_dir.exists():
            agent_ref_map = {
                "_ref_core": [
                    "00_PROJECT_OVERVIEW", "01_COMPONENT_INVENTORY",
                    "02_ARCHITECTURE", "03_EXECUTION_LOGIC",
                ],
                "_ref_data_ops": [
                    "04_DATA_FLOWS", "07_ERROR_HANDLING",
                    "08_STATE_SIDE_EFFECTS", "09_CUSTOM_CODE",
                    "10_BUSINESS_LOGIC", "11_KNOWN_ISSUES",
                    "16_SCHEMA_DICTIONARY", "17_DATA_QUALITY",
                ],
                "_ref_config_user": [
                    "05_ENVIRONMENT_CONFIG", "06_EXTERNAL_SYSTEMS",
                    "12_CHANGE_LOG", "13_RUNBOOK", "14_GLOSSARY_FAQ",
                    "15_ONBOARDING", "18_NAMING_CONVENTIONS",
                    "19_SCHEDULING_SLA", "20_SECURITY_ACCESS",
                    "21_PERFORMANCE", "22_CROSS_REGION",
                    "23_REUSABILITY", "25_PREVENTIVE_ANALYSIS",
                ],
            }
            for bundle_name, doc_names in agent_ref_map.items():
                bundle_parts = [
                    f"# Reference Docs Bundle: {bundle_name}\n\n"
                    "These are example reference docs showing the expected "
                    "format, depth, and structure for each document type.\n\n"
                ]
                for dn in doc_names:
                    ref_file = ref_dir / f"{dn}.md"
                    if ref_file.exists():
                        bundle_parts.append(f"---\n## {dn}\n\n")
                        bundle_parts.append(ref_file.read_text(encoding="utf-8"))
                        bundle_parts.append("\n\n")
                bundle_content = "".join(bundle_parts)
                (preprocess_dir / f"{bundle_name}.md").write_text(
                    bundle_content, encoding="utf-8",
                )
                logger.info("Bundled %s: %d bytes", bundle_name, len(bundle_content))

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

        # Pre-process scan data into structured markdown (saves ~15 min of agent parsing)
        await self.log_manager.emit(
            project_id, "Pre-processing scan data...", level="info",
        )
        preprocess_groups = await self._preprocess_scan_data(project_id, staging_dir)
        await self.log_manager.emit(
            project_id,
            f"Pre-processed {len(preprocess_groups)} groups: {', '.join(preprocess_groups)}",
            level="info",
        )

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
   - Pre-processed scan data is available in output/{project_id}/docs_staging/_preprocess/{{group}}.md for each group.
     If a group was too large, it is split into {{group}}_part1.md and {{group}}_part2.md.
     These files contain ALL job components, params, tMap expressions, SQL, connections, context vars,
     and manifest data in readable markdown format. Sub-agents should read THESE instead of calling fetch_chunk().
   - Analysis prompts are pre-bundled in output/{project_id}/docs_staging/_preprocess/_analysis_prompts.md.
   - Reference doc templates are pre-bundled per Phase 3 agent:
     - output/{project_id}/docs_staging/_preprocess/_ref_core.md (for Core agent)
     - output/{project_id}/docs_staging/_preprocess/_ref_data_ops.md (for Data+Ops agent)
     - output/{project_id}/docs_staging/_preprocess/_ref_config_user.md (for Config+User agent)

PHASE 2 -- GROUP ANALYSIS (parallel sub-agents):
   Spawn one sub-agent per group (all in parallel). Each sub-agent:
   1. Receives: project_id, group name, chunk_ids, manifest, connections.
   2. Reads output/{project_id}/docs_staging/_preprocess/_analysis_prompts.md for analysis guidance.
      Do NOT call get_analysis_prompts() -- this file contains all prompts pre-bundled.
   3. Reads output/{project_id}/docs_staging/_preprocess/{{group}}.md for ALL scan data.
      This file contains the FULL pre-processed data -- do NOT call fetch_chunk().
      If the group is split ({{group}}_part1.md and {{group}}_part2.md exist), read BOTH parts.
   4. Analyzes the pre-processed data across ALL dimensions using the prompt guidance.
   5. Writes a RICH structured analysis file to output/{project_id}/docs_staging/_analysis/{{group}}.md (target: 15-40KB).
   6. Returns a ~2KB navigation summary to the orchestrator (group name, job count, key findings, section list).

   If a group is split into parts (e.g. USA_part1.md and USA_part2.md), spawn one sub-agent per part,
   then merge their analyses into a single output/{project_id}/docs_staging/_analysis/{{group}}.md.
   The merge agent reads both part analyses and combines them into one unified analysis file.

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
   - Read the _preprocess/{{group}}.md file for your group -- it has ALL the data you need.
   - Read _preprocess/_analysis_prompts.md for structured analysis guidance.
   - Do NOT call fetch_chunk() -- the pre-processed files contain the same data in readable format.
   - Do NOT call fetch_json for manifest/connections -- use what was passed inline.
   - Do NOT call get_analysis_prompts() or get_reference_docs() -- use the pre-bundled files.
   - Do NOT use TodoWrite, mkdir, ls, wc, or ASCII validation scripts.
   - Do NOT read .agent/ docs, CLAUDE.md, or mcp-workflow.md.
   - Write the analysis file using the Write tool to output/{project_id}/docs_staging/_analysis/{{group}}.md.
   - Be EXHAUSTIVE. Include every expression, every rule, every column. Depth over brevity.

PHASE 2.5 -- ANALYSIS CONDENSATION (orchestrator task, NOT a sub-agent):
   After ALL Phase 2 sub-agents complete, the orchestrator creates per-agent briefing files.
   This is critical for performance -- each Phase 3 agent reads ~100-150KB instead of 600KB+.

   For each analysis file in output/{project_id}/docs_staging/_analysis/:
   - Read it using: Bash cat output/{project_id}/docs_staging/_analysis/{{group}}.md

   Then create THREE briefing files by extracting ONLY the relevant sections from ALL group analyses:

   1. output/{project_id}/docs_staging/_analysis/_briefing_core.md
      Extract from each group: ## HEADER, ## COMPONENTS, ## EXECUTION_FLOW, ## DATA_FLOWS
      (Core agent writes: 00_PROJECT_OVERVIEW, 01_COMPONENT_INVENTORY, 02_ARCHITECTURE, 03_EXECUTION_LOGIC)

   2. output/{project_id}/docs_staging/_analysis/_briefing_data_ops.md
      Extract from each group: ## HEADER, ## DATA_FLOWS, ## SCHEMA_DICTIONARY, ## SOURCE_SQL,
      ## ERROR_HANDLING, ## CUSTOM_CODE, ## BUSINESS_RULES, ## RISK_FLAGS
      (Data+Ops agent writes: 04_DATA_FLOWS, 07_ERROR_HANDLING, 08_STATE_SIDE_EFFECTS,
       09_CUSTOM_CODE, 10_BUSINESS_LOGIC, 11_KNOWN_ISSUES, 16_SCHEMA_DICTIONARY, 17_DATA_QUALITY)

   3. output/{project_id}/docs_staging/_analysis/_briefing_config_user.md
      Extract from each group: ## HEADER, ## ENVIRONMENT_CONFIG, ## EXTERNAL_SYSTEMS,
      ## SCHEDULING, ## NAMING_PATTERNS, ## CROSS_GROUP_REFS, ## RISK_FLAGS, ## BUSINESS_RULES
      (Config+User agent writes: 05_ENVIRONMENT_CONFIG, 06_EXTERNAL_SYSTEMS, 12_CHANGE_LOG,
       13_RUNBOOK, 14_GLOSSARY_FAQ, 15_ONBOARDING, 18_NAMING_CONVENTIONS, 19_SCHEDULING_SLA,
       20_SECURITY_ACCESS, 21_PERFORMANCE, 22_CROSS_REGION, 23_REUSABILITY, 25_PREVENTIVE_ANALYSIS)

   How to extract sections efficiently:
   Use a Bash python3 one-liner for each analysis file to extract sections. Example approach:
   ```
   python3 -c "
   import re, sys
   text = open(sys.argv[1]).read()
   sections = re.split(r'(?=^## )', text, flags=re.MULTILINE)
   wanted = {{'HEADER','COMPONENTS','EXECUTION_FLOW','DATA_FLOWS'}}
   for s in sections:
       m = re.match(r'## (\w+)', s)
       if m and m.group(1) in wanted:
           print(s)
   " output/{project_id}/docs_staging/_analysis/GROUP.md
   ```
   Prefix each extracted block with "### GROUP_NAME" so Phase 3 agents know the source group.
   Write each briefing file using the Write tool.

PHASE 3 -- WRITE DOCS (parallel sub-agents by theme):
   After Phase 2.5 completes, spawn 3 doc-assembly sub-agents in parallel.
   Each doc agent:
   1. Reads its BRIEFING file from _analysis/ (created in Phase 2.5) for relevant analysis data.
   2. Reads its REFERENCE DOC BUNDLE from _preprocess/ for format templates.
   3. Receives the manifest inline.
   4. Writes final .md files to output/{project_id}/docs_staging/.

   The 3 doc-assembly agents:
   | Agent       | Briefing                    | Reference bundle      | Writes                                                                              |
   |-------------|-----------------------------|-----------------------|--------------------------------------------------------------------------------------|
   | Core        | _briefing_core.md           | _ref_core.md          | 00_PROJECT_OVERVIEW, 01_COMPONENT_INVENTORY, 02_ARCHITECTURE, 03_EXECUTION_LOGIC     |
   | Data+Ops    | _briefing_data_ops.md       | _ref_data_ops.md      | 04_DATA_FLOWS, 07_ERROR_HANDLING, 08_STATE_SIDE_EFFECTS, 09_CUSTOM_CODE, 10_BUSINESS_LOGIC, 11_KNOWN_ISSUES, 16_SCHEMA_DICTIONARY, 17_DATA_QUALITY |
   | Config+User | _briefing_config_user.md    | _ref_config_user.md   | 05_ENVIRONMENT_CONFIG, 06_EXTERNAL_SYSTEMS, 12_CHANGE_LOG, 13_RUNBOOK, 14_GLOSSARY_FAQ, 15_ONBOARDING, 18_NAMING_CONVENTIONS, 19_SCHEDULING_SLA, 20_SECURITY_ACCESS, 21_PERFORMANCE, 22_CROSS_REGION*, 23_REUSABILITY, 25_PREVENTIVE_ANALYSIS |
   *22_CROSS_REGION only if 2+ groups exist.

   Each sub-agent prompt MUST include:
   - "Read output/{project_id}/docs_staging/_analysis/_briefing_{{agent}}.md for your analysis data."
   - "Read output/{project_id}/docs_staging/_preprocess/_ref_{{agent}}.md for format reference templates."
   - Do NOT call get_reference_docs() -- the pre-bundled files have all templates.
   - Do NOT read the full _analysis/{{group}}.md files -- your briefing has the relevant sections.

   Each doc agent MUST:
   - Cross-reference data from multiple groups to build comprehensive docs.
   - Match the depth and structure shown in reference doc templates from the bundle.
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
   - Delete the output/{project_id}/docs_staging/_preprocess/ directory and all its contents.
   - Write output/{project_id}/docs_staging/index.json mapping docs to groups.
   - Write output/{project_id}/logs/run_{project_id}_{{timestamp}}.log.
   - Do NOT call insert_to_rag -- the server will push docs to RAG automatically after successful generation.
{hindsight_phase4}
PROHIBITED ACTIONS:
   - Do NOT use TodoWrite tool.
   - Do NOT run ASCII/Unicode validation scripts.
   - Do NOT read .agent/ docs or CLAUDE.md or mcp-workflow.md.
   - Do NOT create _scratch/ directories or other intermediate files (the _analysis/ and _preprocess/ directories are allowed).
   - Do NOT run mkdir, ls, wc for verification -- directories are pre-created.
   - Do NOT call get_analysis_prompts() or get_reference_docs() -- use the pre-bundled files in _preprocess/.
   - Only final .md docs, _analysis/, and _preprocess/ files go to output/{project_id}/docs_staging/.

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
