"""Claude service — spawns Claude Code CLI for doc generation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
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
    def _extract_source_hint(expression: str) -> str:
        """Parse common tMap expression patterns to extract a source hint."""
        if not expression:
            return ""
        expr = expression.strip()
        # Direct field reference: row1.field, row2.field, etc.
        if expr.startswith("row") and "." in expr and " " not in expr.split(".", 1)[0]:
            return expr.split("(")[0] if "(" in expr else expr
        # Var reference
        if expr.startswith("Var."):
            return expr.split("(")[0] if "(" in expr else expr
        # Context variable
        if expr.startswith("context."):
            return expr
        # Constant string or number
        if expr.startswith('"') or expr.startswith("'"):
            return "constant"
        try:
            float(expr)
            return "constant"
        except (ValueError, TypeError):
            pass
        # Ternary or complex expression
        if "?" in expr and ":" in expr:
            return "conditional"
        return "expression"

    @staticmethod
    def _format_chunk_markdown(
        scanner: ScannerService, output_dir: str, chunks: list[dict],
        platform: str = "talend",
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

                        # Separate FILTER params from regular params (Lobster)
                        params = comp.get("params", [])
                        regular_params: list[dict] = []
                        filter_params: list[dict] = []
                        if isinstance(params, list):
                            for p in params:
                                if p.get("field_type") == "FILTER":
                                    filter_params.append(p)
                                else:
                                    regular_params.append(p)

                        # Render regular params
                        for p in regular_params:
                            pname = p.get("name", "")
                            pval = p.get("value", "")
                            if not pval or pname in ("UNIQUE_NAME", "CODEGENERATION"):
                                continue
                            if len(str(pval)) > 5000:
                                parts.append(f"- **{pname}**:\n```\n{pval}\n```\n")
                            else:
                                parts.append(f"- **{pname}**: `{pval}`\n")

                        # Render Lobster filter chains as structured table
                        if filter_params and platform == "lobster":
                            parts.append("\n**Filter Chain (Transformation Logic)**\n\n")
                            parts.append("| Step | Filter ID | Expression |\n")
                            parts.append("|------|-----------|------------|\n")
                            for idx, fp in enumerate(filter_params, 1):
                                fid = fp.get("name", "").replace("filter_", "")
                                fexpr = fp.get("value", "")
                                # Escape pipe chars for table
                                fexpr_safe = fexpr.replace("|", "\\|")
                                parts.append(f"| {idx} | {fid} | `{fexpr_safe}` |\n")
                            parts.append("\n")

                        # Render Workato datapill mappings as structured table
                        if platform == "workato":
                            datapill_re = re.compile(r"#\{_\('([^']+)'\)([^}]*)\}")
                            # Check the 'steps' param (JSON array of recipe steps)
                            steps_param = next(
                                (p for p in regular_params if p.get("name") == "steps"),
                                None,
                            )
                            # Also check 'input' params on individual action components
                            input_param = next(
                                (p for p in regular_params if p.get("name") == "input"),
                                None,
                            )
                            dp_rows: list[tuple[str, str, str]] = []
                            for src_param in [steps_param, input_param]:
                                if not src_param:
                                    continue
                                raw = str(src_param.get("value", ""))
                                for match in datapill_re.finditer(raw):
                                    ref_path = match.group(1)       # data.step_alias.field
                                    formula = match.group(2).strip() # .to_s, .round(2), etc.
                                    parts_ref = ref_path.split(".")
                                    if len(parts_ref) >= 3:
                                        src_step = parts_ref[1]
                                        src_field = ".".join(parts_ref[2:])
                                    elif len(parts_ref) == 2:
                                        src_step = parts_ref[0]
                                        src_field = parts_ref[1]
                                    else:
                                        src_step = ""
                                        src_field = ref_path
                                    formula_str = formula if formula else ""
                                    dp_rows.append((src_step, src_field, formula_str))
                            if dp_rows:
                                parts.append("\n**Datapill Mappings (Transformation Logic)**\n\n")
                                parts.append("| Source Step | Source Field | Formula |\n")
                                parts.append("|------------|-------------|----------|\n")
                                seen: set[tuple[str, str, str]] = set()
                                for row in dp_rows:
                                    if row in seen:
                                        continue
                                    seen.add(row)
                                    s_step, s_field, s_formula = row
                                    s_formula_safe = s_formula.replace("|", "\\|") if s_formula else "--"
                                    parts.append(f"| {s_step} | {s_field} | `{s_formula_safe}` |\n")
                                parts.append("\n")

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

                # --- Talend: Render tmap_data as structured tables ---
                tmap_data = job.get("tmap_data", [])
                if tmap_data and platform == "talend":
                    parts.append("\n### Transformation Detail (tMap Data)\n")
                    for tmap_idx, tmap in enumerate(tmap_data):
                        input_tables = tmap.get("input_tables", [])
                        output_tables = tmap.get("output_tables", [])
                        var_entries = tmap.get("var_entries", [])
                        entries = tmap.get("entries", [])

                        parts.append(f"\n**tMap #{tmap_idx + 1}**")
                        if input_tables:
                            parts.append(f" | Inputs: {', '.join(input_tables)}")
                        if output_tables:
                            parts.append(f" | Outputs: {', '.join(output_tables)}")
                        parts.append("\n")

                        # Var table
                        if var_entries:
                            parts.append("\n**Var Table**\n\n")
                            parts.append("| Variable | Expression | Source Hint |\n")
                            parts.append("|----------|------------|-------------|\n")
                            for ve in var_entries:
                                col = ve.get("column_name", "")
                                expr = ve.get("expression", "")
                                hint = ClaudeService._extract_source_hint(expr)
                                expr_safe = expr.replace("|", "\\|")
                                parts.append(f"| {col} | `{expr_safe}` | {hint} |\n")
                            parts.append("\n")

                        # Per-output-table entries
                        tables_seen: dict[str, list[dict]] = {}
                        for entry in entries:
                            tbl = entry.get("table_name", "output")
                            tables_seen.setdefault(tbl, []).append(entry)

                        for tbl_name, tbl_entries in tables_seen.items():
                            parts.append(f"\n**Output: {tbl_name}**\n\n")
                            parts.append("| Target Column | Expression | Source Hint |\n")
                            parts.append("|---------------|------------|-------------|\n")
                            for te in tbl_entries:
                                col = te.get("column_name", "")
                                expr = te.get("expression", "")
                                hint = ClaudeService._extract_source_hint(expr)
                                expr_safe = expr.replace("|", "\\|")
                                parts.append(f"| {col} | `{expr_safe}` | {hint} |\n")
                            parts.append("\n")

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

    @staticmethod
    def _detect_resume_state(staging_dir: Path) -> dict | None:
        """Inspect docs_staging/ for completed artifacts from a prior run.

        Returns None (fresh run) or a dict describing what already exists:
        - preprocess_exists: bool — _preprocess/ has group markdown files
        - analysis_files: list[str] — non-bundle .md files in _analysis/
        - scorecard_exists: bool — _assessment/scorecard.md exists
        - bundles_exist: bool — _analysis/_bundle_*.md files exist
        - completed_docs: list[str] — *.md files in staging root >500 bytes
        """
        if not staging_dir.exists():
            return None

        preprocess_dir = staging_dir / "_preprocess"
        analysis_dir = staging_dir / "_analysis"
        assessment_dir = staging_dir / "_assessment"

        # Check for any usable artifacts
        preprocess_files = (
            sorted(f.name for f in preprocess_dir.glob("*.md") if not f.name.startswith("_"))
            if preprocess_dir.exists() else []
        )
        analysis_files = (
            sorted(
                f.stem for f in analysis_dir.glob("*.md")
                if not f.name.startswith("_bundle_")
            )
            if analysis_dir.exists() else []
        )
        bundle_files = (
            list(analysis_dir.glob("_bundle_*.md"))
            if analysis_dir.exists() else []
        )
        scorecard_path = assessment_dir / "scorecard.md"
        scorecard_exists = scorecard_path.exists() and scorecard_path.stat().st_size > 100
        completed_docs = (
            sorted(
                f.name for f in staging_dir.glob("*.md")
                if f.stat().st_size > 500
            )
        )

        # If nothing useful exists, treat as fresh
        if not preprocess_files and not analysis_files and not completed_docs:
            return None

        state = {
            "preprocess_exists": len(preprocess_files) > 0,
            "preprocess_files": preprocess_files,
            "analysis_files": analysis_files,
            "scorecard_exists": scorecard_exists,
            "bundles_exist": len(bundle_files) > 0,
            "completed_docs": completed_docs,
        }
        logger.info("Resume state detected: %s", state)
        return state

    async def _preprocess_scan_data(
        self,
        project_id: str,
        staging_dir: Path,
        platform: str = "talend",
        template_profile_id: int | None = None,
        total_jobs: int = 0,
    ) -> tuple[list[str], dict[str, int]]:
        """Pre-process raw job JSONs into structured markdown summaries.

        Reads scan data via ScannerService and writes per-group markdown files
        to staging_dir/_preprocess/{group}.md. Also bundles analysis prompts,
        reference docs, and scoring templates for sub-agents.

        When template_profile_id is set, reads templates from DB via
        TemplateResolver. Otherwise falls back to filesystem.

        Returns (group_names, template_snapshot) where template_snapshot maps
        slug -> version_id for templates resolved from the DB.
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
                    scanner, output_dir, slice_chunks, platform=platform,
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
                "_ref_patterns": [
                    "07_SECURITY_PERFORMANCE", "08_PATTERNS_STANDARDS",
                ],
                "_ref_user": [
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

        # --- Bundle scoring/assessment templates for Phase 2.6 ---
        _templates_dir = Path(__file__).resolve().parent.parent / "templates"
        scoring_templates = {
            "_scoring_rubric.md": "scoring-rubric.md",
            "_anti_patterns.md": "anti-patterns.md",
            "_red_flags.md": "red-flags.md",
            "_security_checklist.md": "security-checklist.md",
            "_pattern_library.md": "pattern-library.md",
        }
        for dest_name, src_name in scoring_templates.items():
            src_path = _templates_dir / src_name
            if src_path.exists():
                content = src_path.read_text(encoding="utf-8")
                (preprocess_dir / dest_name).write_text(content, encoding="utf-8")
                logger.info("Bundled scoring template %s: %d bytes", dest_name, len(content))
            else:
                logger.warning("Scoring template not found: %s", src_path)

        # --- Copy analysis bundling script for Phase 2.8 ---
        _bundle_script = Path(__file__).resolve().parent.parent / "scripts" / "bundle_analysis.py"
        if _bundle_script.exists():
            shutil.copy2(_bundle_script, preprocess_dir / "_bundle_analysis.py")
            logger.info("Copied bundle_analysis.py to _preprocess/ (%d bytes)", _bundle_script.stat().st_size)
        else:
            logger.warning("bundle_analysis.py not found at %s", _bundle_script)

        # --- Override with DB templates if profile is set ---
        template_snapshot: dict[str, int] = {}
        if template_profile_id is not None:
            try:
                from mcp_server.db.session import async_session_factory as _sf
                from mcp_server.services.template_resolver import TemplateResolver

                async with _sf() as db:
                    resolver = TemplateResolver(db)
                    resolved = await resolver.resolve_all(
                        profile_id=template_profile_id,
                        project_id=project_id,
                        platform=platform,
                    )
                    template_snapshot = resolver.snapshot

                    # Overwrite files with DB content where available
                    slug_to_file: dict[str, str] = {
                        "orchestration/phase3_guide": "_phase3_guide.md",
                        "assessment/scoring_rubric": "_scoring_rubric.md",
                        "assessment/anti_patterns": "_anti_patterns.md",
                        "assessment/red_flags": "_red_flags.md",
                        "assessment/security_checklist": "_security_checklist.md",
                        "pattern/pattern_library": "_pattern_library.md",
                    }
                    for slug, filename in slug_to_file.items():
                        if slug in resolved:
                            (preprocess_dir / filename).write_text(
                                resolved[slug], encoding="utf-8",
                            )
                            logger.info(
                                "DB template override: %s -> %s (%d bytes)",
                                slug, filename, len(resolved[slug]),
                            )

                    # Analysis guide override -- use only the consolidated guide,
                    # not individual p01-p22 dimension prompts
                    guide_slug = f"analysis/{platform}_guide"
                    if guide_slug in resolved:
                        guide = resolved[guide_slug]
                        (preprocess_dir / "_analysis_guide.md").write_text(
                            guide, encoding="utf-8",
                        )
                        logger.info(
                            "DB template override: %s -> _analysis_guide.md (%d bytes)",
                            guide_slug, len(guide),
                        )

                    # Reference doc overrides
                    ref_map = {
                        "_ref_core": ["output/00_executive_summary", "output/01_project_overview",
                                      "output/02_architecture_inventory", "output/03_data_flows_lineage"],
                        "_ref_business_ops": ["output/04_business_logic", "output/05_operations_risk",
                                              "output/06_environment_external"],
                        "_ref_patterns": ["output/07_security_performance", "output/08_patterns_standards"],
                        "_ref_user": ["output/09_runbook", "output/10_onboarding_faq",
                                      "output/11_migration_assessment"],
                    }
                    for bundle_name, slugs in ref_map.items():
                        entries = []
                        for slug in slugs:
                            if slug in resolved:
                                entries.append(f"---\n## {slug}\n\n{resolved[slug]}\n\n")
                        if entries:
                            header = f"# Reference Docs Bundle: {bundle_name}\n\n"
                            bundle = header + "".join(entries)
                            (preprocess_dir / f"{bundle_name}.md").write_text(
                                bundle, encoding="utf-8",
                            )
                            logger.info(
                                "DB template override: %s (%d docs, %d bytes)",
                                bundle_name, len(entries), len(bundle),
                            )

                logger.info(
                    "Template resolution complete: %d templates from profile %d",
                    len(template_snapshot), template_profile_id,
                )
            except Exception as e:
                logger.warning(
                    "Template resolution failed, using filesystem fallback: %s", e,
                )
                template_snapshot = {}

        return processed_groups, template_snapshot

    async def generate_docs(
        self,
        project_id: str,
        template_profile_id: int | None = None,
        model: str | None = None,
        resume: bool = True,
    ) -> dict:
        """Spawn Claude Code CLI to generate docs for a project.

        When resume=True (default), checks for existing docs_staging/ artifacts
        and skips already-completed phases. Set resume=False for a clean run.

        Returns dict with success, cost_usd, duration_ms, input_tokens, etc.
        """
        output_dir = config.project_root / "output" / project_id

        # Use a staging directory so old docs survive if generation fails
        staging_dir = output_dir / "docs_staging"

        # Detect resume state before deciding whether to wipe staging
        resume_state: dict | None = None
        if resume and staging_dir.exists():
            resume_state = self._detect_resume_state(staging_dir)

        if resume_state is None:
            # Fresh run — wipe any leftover staging
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)

        for d in [
            staging_dir,
            staging_dir / "_analysis",
            staging_dir / "_assessment",
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

        # Compute project metrics early (needed for preprocessing decisions)
        scanner = ScannerService()
        scan_output_dir = str(config.intodoc_output_dir / project_id)
        chunk_defs = scanner.get_chunk_definitions(scan_output_dir)
        total_jobs = chunk_defs.get("total_jobs", 0)

        # Step 7: Stale scan detection — compare scan mtime against manifest
        manifest_path = staging_dir / "_manifest.json"
        if resume_state is not None:
            scan_dir = config.intodoc_output_dir / project_id / "scan"
            scan_manifest = scan_dir / "manifest.json"
            if scan_manifest.exists() and manifest_path.exists():
                try:
                    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest_ts = manifest_data.get("scan_mtime", 0)
                    current_scan_mtime = scan_manifest.stat().st_mtime
                    if current_scan_mtime > manifest_ts:
                        logger.info(
                            "Scan data changed since staging was created "
                            "(scan=%.0f > manifest=%.0f) — re-preprocessing",
                            current_scan_mtime, manifest_ts,
                        )
                        await self.log_manager.emit(
                            project_id,
                            "Scan data changed since last run — re-preprocessing (keeping analysis if groups match)",
                            level="warning",
                        )
                        # Invalidate preprocessing but keep analysis/docs
                        preprocess_dir = staging_dir / "_preprocess"
                        if preprocess_dir.exists():
                            shutil.rmtree(preprocess_dir, ignore_errors=True)
                        resume_state["preprocess_exists"] = False
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning("Could not check stale scan: %s", e)

        # Pre-process scan data (skip if resuming with valid preprocess files)
        skip_preprocess = (
            resume_state is not None
            and resume_state.get("preprocess_exists", False)
        )

        if skip_preprocess:
            await self.log_manager.emit(
                project_id,
                "RESUME: Skipping preprocessing (files already exist)",
                level="info",
            )
            # Recover group names from existing preprocess files
            preprocess_dir = staging_dir / "_preprocess"
            preprocess_groups = sorted(
                f.stem for f in preprocess_dir.glob("*.md")
                if not f.name.startswith("_")
            )
            template_snapshot: dict[str, int] = {}
            # Try to recover template_snapshot from manifest
            if manifest_path.exists():
                try:
                    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    template_snapshot = manifest_data.get("template_snapshot", {})
                except (json.JSONDecodeError, OSError):
                    pass
        else:
            await self.log_manager.emit(
                project_id, "Pre-processing scan data...", level="info",
            )
            preprocess_groups, template_snapshot = await self._preprocess_scan_data(
                project_id, staging_dir, platform, template_profile_id,
                total_jobs=total_jobs,
            )

        await self.log_manager.emit(
            project_id,
            f"Pre-processed {len(preprocess_groups)} groups: {', '.join(preprocess_groups)}",
            level="info",
        )

        # Write _manifest.json for stale scan detection and resume metadata
        scan_dir = config.intodoc_output_dir / project_id / "scan"
        scan_manifest_path = scan_dir / "manifest.json"
        manifest_path.write_text(json.dumps({
            "project_id": project_id,
            "platform": platform,
            "groups": preprocess_groups,
            "total_jobs": total_jobs,
            "model": model or config.doc_generation_model,
            "template_profile_id": template_profile_id,
            "template_snapshot": template_snapshot,
            "scan_mtime": scan_manifest_path.stat().st_mtime if scan_manifest_path.exists() else 0,
            "created_at": time.time(),
        }, indent=2), encoding="utf-8")

        total_groups = len(preprocess_groups)

        # Compute scaled analysis target from preprocessed data size
        total_preprocess_bytes = sum(
            f.stat().st_size
            for f in (staging_dir / "_preprocess").glob("*.md")
            if not f.name.startswith("_")  # exclude guide/ref bundles
        )
        analysis_target_kb = max(15, min(60, total_preprocess_bytes // 1024))
        logger.info(
            "Analysis target: %dKB (from %d bytes preprocessed data)",
            analysis_target_kb, total_preprocess_bytes,
        )

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
                "   | Agent          | Bundle                            | Reference bundle                     | Writes                                                                    |\n"
                "   |----------------|-----------------------------------|--------------------------------------|---------------------------------------------------------------------------||\n"
                "   | Core           | _analysis/_bundle_core.md         | _ref_core*.md                        | 00_EXECUTIVE_SUMMARY, 01_PROJECT_OVERVIEW, 02_ARCHITECTURE_INVENTORY, 03_DATA_FLOWS_LINEAGE |\n"
                "   | Business+Ops   | _analysis/_bundle_business_ops.md | _ref_business_ops*.md                | 04_BUSINESS_LOGIC, 05_OPERATIONS_RISK, 06_ENVIRONMENT_EXTERNAL            |\n"
                "   | Patterns       | _analysis/_bundle_patterns.md     | _ref_patterns*.md                    | 07_SECURITY_PERFORMANCE, 08_PATTERNS_STANDARDS*                           |\n"
                "   | User           | _analysis/_bundle_user.md         | _ref_user*.md                        | 09_RUNBOOK, 10_ONBOARDING_FAQ, 11_MIGRATION_ASSESSMENT                    |"
            )
            agent_retry_map = (
                "Core, Business+Ops, Patterns, or User"
            )
        else:
            agent_table = (
                "   The 3 doc-assembly agents:\n"
                "   | Agent          | Bundle                                 | Reference bundle                     | Writes                                                                    |\n"
                "   |----------------|----------------------------------------|--------------------------------------|---------------------------------------------------------------------------||\n"
                "   | Core           | _analysis/_bundle_core.md              | _ref_core*.md                        | 00_EXECUTIVE_SUMMARY, 01_PROJECT_OVERVIEW, 02_ARCHITECTURE_INVENTORY, 03_DATA_FLOWS_LINEAGE |\n"
                "   | Business+Ops   | _analysis/_bundle_business_ops.md      | _ref_business_ops*.md                | 04_BUSINESS_LOGIC, 05_OPERATIONS_RISK, 06_ENVIRONMENT_EXTERNAL            |\n"
                "   | Patterns+User  | _analysis/_bundle_patterns_user.md     | _ref_patterns*.md, _ref_user*.md     | 07_SECURITY_PERFORMANCE, 08_PATTERNS_STANDARDS*, 09_RUNBOOK, 10_ONBOARDING_FAQ, 11_MIGRATION_ASSESSMENT |"
            )
            agent_retry_map = (
                "Core, Business+Ops, or Patterns+User"
            )

        logger.info(
            "Depth tier: %s (x%.1f), agents: %d, large_project: %s",
            depth_tier, depth_multiplier, num_agents, is_large_project,
        )

        phase2_header = (
            "PHASE 2 -- GROUP ANALYSIS (parallel sub-agents):\n"
            "   Spawn one sub-agent per group (all in parallel). Each sub-agent:"
        )

        # Determine which analysis guide file the sub-agents should read
        analysis_guide_file = "_analysis_guide.md"
        if not (staging_dir / "_preprocess" / "_analysis_guide.md").exists():
            analysis_guide_file = "_analysis_prompts*.md"  # legacy fallback

        # Determine effective model (per-run override > config default)
        effective_model = model or config.doc_generation_model
        write_model = config.doc_generation_write_model

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

        # Build resume block if resuming from checkpoint
        resume_prompt_block = ""
        if resume_state is not None:
            analysis = resume_state.get("analysis_files", [])
            scorecard = resume_state.get("scorecard_exists", False)
            bundles = resume_state.get("bundles_exist", False)
            completed = resume_state.get("completed_docs", [])

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
            await self.log_manager.emit(
                project_id,
                f"RESUME: analysis={len(analysis)} groups, scorecard={'yes' if scorecard else 'no'}, "
                f"bundles={'yes' if bundles else 'no'}, docs={len(completed)}",
                level="info",
            )

        prompt = f"""{resume_prompt_block}Generate complete documentation for project '{project_id}'.

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

{phase2_header}
   1. Reads {staging_abs}/_preprocess/{analysis_guide_file} for analysis guidance.
   2. Reads {staging_abs}/_preprocess/{{group}}.md for ALL scan data.
      If the group is split ({{group}}_part1.md and {{group}}_part2.md exist), read BOTH parts.
   3. Analyzes the pre-processed data across ALL dimensions defined in the analysis guide.
   4. Writes a RICH structured analysis file to {staging_abs}/_analysis/{{group}}.md (MAXIMUM: {analysis_target_kb}KB -- prioritize depth over breadth when approaching the cap).
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
   Each Phase 3 agent reads its PRE-BUNDLED analysis file from {staging_abs}/_analysis/_bundle_{{agent}}.md.
   Do NOT read individual _analysis/{{group}}.md files -- the bundles already contain the relevant sections.
   Each Phase 3 agent also reads {staging_abs}/_assessment/scorecard.md for quantitative grounding.
   Each doc agent also reads its REFERENCE DOC BUNDLE from _preprocess/ for format templates.
   Each Phase 3 agent also reads {staging_abs}/_preprocess/_phase3_guide.md for cross-group
   synthesis guidance (executive summary structure, cross-region comparison dimensions,
   migration assessment methodology).
   Each doc agent writes final .md files to {staging_abs}/.

{agent_table}
   *08_PATTERNS_STANDARDS cross-region section only if 2+ groups exist.

   Each sub-agent prompt MUST include:
   - "Read {staging_abs}/_analysis/_bundle_{{your_agent}}.md for your pre-bundled analysis data."
   - "Read {staging_abs}/_assessment/scorecard.md for quantitative scores and evidence."
   - "Read ALL _ref_{{agent}}*.md files in {staging_abs}/_preprocess/ for format reference templates."
   - "Read {staging_abs}/_preprocess/_phase3_guide.md for cross-group synthesis guidance."
   - Do NOT call get_reference_docs() or get_analysis_prompts() -- use the pre-bundled files.
   - Do NOT read individual _analysis/{{group}}.md files -- use ONLY your bundle file.
   SCORECARD INTEGRATION:
   - Reference specific scores from the scorecard in your documents.
   - Example: "Security: 2/5 (see Assessment Scorecard for details)"
   - Include anti-pattern IDs and red flag IDs where relevant.
   - The Executive Summary (doc 00) MUST include the overall score and dimension summary table.
   - The Migration Assessment (doc 11) MUST reference the Migration Readiness score.
   - Operations & Risk (doc 05) MUST reference error handling and operations scores.

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
      - Identify which Phase 3 agent was responsible ({agent_retry_map}).
      - Re-spawn ONLY that agent with the same instructions. Max 1 retry per agent.
   5. After retries, proceed to Phase 4 with whatever docs exist. Log any still-missing docs as WARNINGS.

PHASE 4 -- CLEANUP & PUBLISH:
   - Delete the {staging_abs}/_analysis/ directory and all its contents.
   - Delete the {staging_abs}/_preprocess/ directory and all its contents.
   - KEEP {staging_abs}/_assessment/scorecard.md -- it is part of the final output.
   - Write {staging_abs}/index.json mapping docs to groups.
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
            "--model", effective_model,
            "--max-turns", "200",
            "--dangerously-skip-permissions",
            "--output-format", "stream-json",
            "--verbose",
        ]

        # Diagnostic logging — model, project metrics
        _subprocess_cwd = Path(__file__).resolve().parent.parent.parent.parent.parent
        logger.info(
            "Doc generation config: model=%s, write_model=%s, "
            "groups=%d, jobs=%d, max_turns=200",
            effective_model, write_model,
            total_groups, total_jobs,
        )
        logger.info(f"claude resolved path: {resolved_cmd}")
        logger.info(f"Spawning Claude Code: {cmd[0]} -p '...' --model {effective_model}  cwd={_subprocess_cwd}")
        resume_label = "resume" if resume_state is not None else "fresh"
        await self.log_manager.emit(
            project_id,
            f"Model: {effective_model} | Write model: {write_model} | "
            f"Groups: {total_groups} | Jobs: {total_jobs} | Mode: {resume_label}",
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
            env.pop("CLAUDECODE", None)  # Allow nested Claude Code subprocess
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
            usage: dict = {"success": returncode == 0, "model_name": effective_model}
            if template_snapshot:
                usage["template_snapshot"] = template_snapshot
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
