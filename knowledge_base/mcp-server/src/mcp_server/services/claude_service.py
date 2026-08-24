"""Claude service — coordinator for doc generation.

Handles preprocessing, resume detection, and context building, then
delegates execution to the configured DocGenerationProvider.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time

from pathlib import Path

from sqlalchemy import select

from mcp_server.config import config
from mcp_server.db.models import PluginPack
from mcp_server.services import instance_settings_service
from mcp_server.db.session import async_session_factory
from mcp_server.model_strategies import resolve_generation_models
from mcp_server.providers import get_provider
from mcp_server.providers.base import DocGenContext
from mcp_server.services.extraction_cache import (
    compute_extraction_key,
    extraction_cache_dir,
    restore_extraction,
)
from mcp_server.services.generation_run_state import (
    GenerationLockError,
    LockHeartbeat,
    acquire_generation_lock,
    persist_nexus_execution_receipt,
    refresh_generation_lock,
    release_generation_lock,
    write_run_manifest,
)
from mcp_server.providers.custom.source_context import (
    materialize_direct_redacted_source,
    redact_inline_credentials,
)
from mcp_server.services.log_manager import LogManager
from mcp_server.services.plugin_runner import build_plugin_runner
from mcp_server.services.pack_governance import PackGovernanceDenied, pack_governance
from mcp_server.services.nexus_execution_contract import build_nexus_execution_receipt
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.scanner_service import ScannerService
from mcp_server.schemas import NexusPackSnapshot, NexusPresentation

logger = logging.getLogger(__name__)

_TALEND_NOISE_PARAMS: frozenset[str] = frozenset({
    "PREVIEW",                          # .bmp thumbnail file path
    "CONNECTION_FORMAT",                # always 'row'
    "USE_SINGLEMODE", "USE_INTABLE", "USE_INLINECONTENT",
    "LKUP_PARALLELIZE", "LEVENSHTEIN", "JACCARD",
    "ENABLE_AUTO_CONVERT_TYPE", "ROWS_BUFFER_SIZE",
    "CHANGE_HASH_AND_EQUALS_FOR_BIGDECIMAL",
    "LINK_STYLE", "TABLE_PRINT", "BASIC_MODE",
    "JDK_VERSION", "JSON_PATH_VERSION", "USE_LOOP_AS_ROOT",
    "CODEGENERATION",
})

_WORKATO_DATAPILL_RE = re.compile(r"#\{_\('([^']+)'\)([^}]*)\}")
_DESIGN_HEADING_RE = re.compile(
    r"^## (?:(?:Talend )?(?:Job|Route)|Artifact):\s*(.+?)\s*$",
)


def _render_component(comp: dict, platform: str, parts: list[str]) -> None:
    params = comp.get("params", [])
    regular_params: list[dict] = []
    filter_params: list[dict] = []
    if isinstance(params, list):
        for p in params:
            if p.get("field_type") == "FILTER":
                filter_params.append(p)
            else:
                regular_params.append(p)

    for p in regular_params:
        pname = p.get("name", "")
        pval = p.get("value", "")
        if not pval or pname in ("UNIQUE_NAME",):
            continue
        if pname in _TALEND_NOISE_PARAMS:
            continue
        val_str = str(pval)
        if len(val_str) > config.max_param_val_chars:
            val_str = val_str[:config.max_param_val_chars] + f"... [{len(val_str)} chars]"
        parts.append(f"- **{pname}**: `{val_str}`\n")

    if filter_params and platform == "lobster":
        parts.append("\n**Filter Chain (Transformation Logic)**\n\n")
        parts.append("| Step | Filter ID | Expression |\n")
        parts.append("|------|-----------|------------|\n")
        for idx, fp in enumerate(filter_params, 1):
            fid = fp.get("name", "").replace("filter_", "")
            fexpr = fp.get("value", "").replace("|", "\\|")
            parts.append(f"| {idx} | {fid} | `{fexpr}` |\n")
        parts.append("\n")

    if platform == "workato":
        dp_rows: list[tuple[str, str, str]] = []
        for src_param in (p for p in regular_params if p.get("name") in ("steps", "input")):
            raw = str(src_param.get("value", ""))
            for match in _WORKATO_DATAPILL_RE.finditer(raw):
                ref_path = match.group(1)
                formula = match.group(2).strip()
                parts_ref = ref_path.split(".")
                if len(parts_ref) >= 3:
                    src_step, src_field = parts_ref[1], ".".join(parts_ref[2:])
                elif len(parts_ref) == 2:
                    src_step, src_field = parts_ref[0], parts_ref[1]
                else:
                    src_step, src_field = "", ref_path
                dp_rows.append((src_step, src_field, formula))
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

    for direction, key in [("Incoming", "incoming_connections"), ("Outgoing", "outgoing_connections")]:
        conns = comp.get(key, [])
        if conns:
            parts.append(f"- {direction} connections: {json.dumps(conns)}\n")
    ref = comp.get("metadata_connection_ref")
    if ref:
        parts.append(f"- Metadata connection: `{ref}`\n")


def _render_tmap_data(tmap_data: list[dict], parts: list[str], extract_source_hint: object) -> None:
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

        if var_entries:
            parts.append("\n**Var Table**\n\n")
            parts.append("| Variable | Expression | Source Hint |\n")
            parts.append("|----------|------------|-------------|\n")
            for ve in var_entries:
                col = ve.get("column_name", "")
                expr = ve.get("expression", "")
                hint = extract_source_hint(expr)  # type: ignore[operator]
                expr_safe = expr.replace("|", "\\|")
                parts.append(f"| {col} | `{expr_safe}` | {hint} |\n")
            parts.append("\n")

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
                hint = extract_source_hint(expr)  # type: ignore[operator]
                expr_safe = expr.replace("|", "\\|")
                parts.append(f"| {col} | `{expr_safe}` | {hint} |\n")
            parts.append("\n")


def _render_chunk_extras(chunk_data: dict, chunk_id: str, parts: list[str]) -> None:
    related_conns = chunk_data.get("related_connections", [])
    if related_conns:
        parts.append(f"\n---\n## Connections (chunk {chunk_id})\n")
        for conn in related_conns:
            conn_name = conn.get("name", "?")
            parts.append(f"\n### Connection: {conn_name}\n")
            for k, v in conn.items():
                if k == "name":
                    continue
                parts.append(f"- **{k}**: `{v}`\n")

    related_ctx = chunk_data.get("related_contexts", [])
    if related_ctx:
        parts.append(f"\n---\n## Context Groups (chunk {chunk_id})\n")
        for ctx in related_ctx:
            ctx_name = ctx.get("name", "?")
            parts.append(f"\n### Context Group: {ctx_name}\n")
            source_project = ctx.get("source_project", "")
            source_root = ctx.get("source_root", "")
            source_role = ctx.get("source_role", "")
            if source_project:
                parts.append(f"- **Source project**: `{source_project}`\n")
            if source_root:
                role_suffix = f" ({source_role})" if source_role else ""
                parts.append(f"- **Source root**: `{source_root}`{role_suffix}\n")
            if ctx.get("purpose"):
                parts.append(f"- **Purpose**: {ctx['purpose']}\n")
            if ctx.get("description"):
                parts.append(f"- **Description**: {ctx['description']}\n")
            for var in ctx.get("variables", ctx.get("items", [])):
                vname = var.get("name", "?")
                vtype = var.get("type", "")
                values = var.get("values")
                if isinstance(values, dict):
                    rendered_values = json.dumps(values, ensure_ascii=False, sort_keys=True)
                else:
                    rendered_values = str(var.get("value", ""))
                line = f"- `{vname}` ({vtype}): `{rendered_values}`"
                parts.append(line + "\n")

    manifest = chunk_data.get("manifest", {})
    if manifest:
        parts.append(f"\n---\n## Manifest (chunk {chunk_id})\n")
        for section, data in manifest.items():
            if data:
                parts.append(f"\n### {section}\n```json\n{json.dumps(data, indent=2)}\n```\n")



class DocGenerationCoordinator:
    """Orchestrates multi-phase documentation generation: preprocessing, provider dispatch, staging swap, and RAG push."""

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
        unit_allowlist: set[str] | None = None,
    ) -> tuple[list[str], int]:
        """Process chunks into markdown parts. Returns (parts, job_count)."""
        parts: list[str] = []
        job_count = 0

        for chunk in chunks:
            chunk_data = scanner.read_chunk_data(
                output_dir, chunk["chunk_id"], unit_allowlist=unit_allowlist,
            )
            if "error" in chunk_data:
                parts.append(f"\n## Chunk {chunk['chunk_id']} — ERROR: {chunk_data['error']}\n")
                continue

            for job in chunk_data.get("jobs", []):
                job_count += 1
                job_id = job.get("job_id") or job.get("name", "unknown")
                display_name = job.get("name", "unknown")
                source_root = job.get("source_root", "")
                artifact_kind = str(job.get("artifact_kind") or "job")
                is_talend = platform.lower() == "talend"
                artifact_label = (
                    "Talend Route"
                    if artifact_kind == "route"
                    else "Talend Job"
                )
                section_label = artifact_label if is_talend else "Job"
                heading = (
                    f"\n---\n## {section_label}: {job_id}\n"
                    f"\n- Artifact type: **{artifact_label if is_talend else 'Executable unit'}**\n"
                )
                if job_id != display_name or source_root:
                    heading += f"\n- Display name: `{display_name}`\n"
                    if source_root:
                        heading += f"- Source root: `{source_root}`\n"
                parts.append(heading)

                if artifact_kind == "route":
                    parts.append(
                        "- Analyze as message mediation: triggers/endpoints, channels, "
                        "Camel patterns, invoked Jobs, and error paths.\n"
                    )
                else:
                    parts.append(
                        "- Analyze as data processing: inputs, transformations, business "
                        "rules, outputs, and callers.\n"
                    )

                components = job.get("components", [])
                if components:
                    parts.append("\n### Components\n")
                    for comp in components:
                        ctype = comp.get("component_type", "?")
                        cname = comp.get("unique_name", "?")
                        parts.append(f"\n#### {cname} ({ctype})\n")
                        _render_component(comp, platform, parts)

                tmap_data = job.get("tmap_data", [])
                if tmap_data and platform == "talend":
                    _render_tmap_data(tmap_data, parts, DocGenerationCoordinator._extract_source_hint)

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

                routines = job.get("raw_routines", [])
                if routines:
                    parts.append(f"\n### Routines used: {', '.join(routines)}\n")

            _render_chunk_extras(chunk_data, chunk["chunk_id"], parts)

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
        total_jobs: int = 0,
        unit_allowlist: set[str] | None = None,
    ) -> list[str]:
        """Pre-process raw job JSONs into structured markdown summaries.

        Reads scan data via ScannerService and writes per-group markdown files
        to staging_dir/_preprocess/{group}.md. Also bundles analysis prompts,
        reference docs, and scoring templates for sub-agents.

        ``unit_allowlist`` (composite sub-project scoping) restricts the
        preprocess to the partition's own canonical unit names: chunk
        definitions, chunk payloads, group files and the design inventory then
        contain ONLY that sub-project's units. Shared-name REFERENCES jobs are
        safe because canonical job ids are unique per zip (see
        ``ScannerService.get_chunk_definitions``). ``None`` => full scan,
        unchanged behaviour.

        Returns list of group names.
        """
        preprocess_dir = staging_dir / "_preprocess"
        preprocess_dir.mkdir(parents=True, exist_ok=True)
        preprocess_dir.chmod(0o755)

        scanner = ScannerService()
        output_dir = str(config.intodoc_output_dir / project_id)

        processed_groups: list[str] = []
        direct_source = (
            platform == "generic"
            and config.source_context_mode.lower() == "direct_redacted"
        )
        if direct_source:
            processed_groups, source_coverage = materialize_direct_redacted_source(
                scan_output_dir=Path(output_dir),
                preprocess_dir=preprocess_dir,
                inline_max_bytes=config.direct_source_inline_max_bytes,
                chunk_bytes=config.direct_source_chunk_bytes,
            )
            logger.info(
                "Direct redacted source preprocessing: %d groups, %d files, %d redactions",
                len(processed_groups),
                source_coverage.get("summary", {}).get("total_files", 0),
                source_coverage.get("summary", {}).get("redactions", 0),
            )
        else:
            # Get chunk definitions (scoped to the partition when allowlisted)
            chunk_defs = scanner.get_chunk_definitions(
                output_dir, unit_allowlist=unit_allowlist,
            )
            if "error" in chunk_defs:
                raise RuntimeError(f"Failed to get chunk definitions: {chunk_defs['error']}")

            # Group chunks by group name
            groups: dict[str, list[dict]] = {}
            for chunk in chunk_defs["chunks"]:
                group = chunk["group"]
                groups.setdefault(group, []).append(chunk)

            # Case-insensitive filesystems (macOS/Windows dev hosts) map group
            # names differing only in case (Talend allows e.g. VISE2AssetCheck
            # and Vise2AssetCheck side by side) onto the SAME {group}.md path,
            # silently clobbering the earlier group's preprocess file and
            # losing its units from Phase 1/2. Disambiguate every collider
            # after the first with a "_part_ci{k}" slice base: the existing
            # slice->group resolvers (harness._group_units, validators.
            # _groups_for_analysis_file, quick-scan globbing) already map any
            # "{group}_part*" slice back to its group, and the suffix keeps
            # paths distinct even case-insensitively. Non-colliding groups are
            # byte-identical to before.
            slice_base: dict[str, str] = {}
            lower_buckets: dict[str, list[str]] = {}
            for group in sorted(groups):
                lower_buckets.setdefault(group.lower(), []).append(group)
            for bucket in lower_buckets.values():
                slice_base[bucket[0]] = bucket[0]
                for k, group in enumerate(bucket[1:], start=1):
                    slice_base[group] = f"{group}_part_ci{k}"
                    logger.warning(
                        "Group %r collides case-insensitively with %r — "
                        "writing its preprocess slice as %r",
                        group, bucket[0], slice_base[group],
                    )

            for group, chunks in sorted(groups.items()):
                total_jobs = sum(c["job_count"] for c in chunks)
                base = slice_base[group]

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
                    if base == group:
                        part_names = (f"{group}_part1", f"{group}_part2")
                    else:
                        # Collision-suffixed base already contains "_part";
                        # nested split parts stay under the same unique base.
                        part_names = (f"{base}_1", f"{base}_2")
                    chunk_slices = [
                        (part_names[0], chunks[:split_idx]),
                        (part_names[1], chunks[split_idx:]),
                    ]
                    logger.info(
                        "Splitting group %s (%d jobs) into 2 parts at chunk %d",
                        group, total_jobs, split_idx,
                    )
                else:
                    chunk_slices = [(base, chunks)]

                for slice_name, slice_chunks in chunk_slices:
                    if not slice_chunks:
                        continue
                    slice_jobs = sum(c["job_count"] for c in slice_chunks)

                    # Compact design inventory at top — always fits in first
                    # ~2KB so truncation never hides the complete unit list.
                    all_job_names: list[str] = []
                    for c in slice_chunks:
                        all_job_names.extend(c.get("job_names", []))
                    is_talend = platform.lower() == "talend"
                    unit_plural = "Talend designs" if is_talend else "jobs"
                    inventory_title = "Talend Design Inventory" if is_talend else "Job Inventory"
                    column_title = "Design ID" if is_talend else "Job Name"
                    inventory_lines = [
                        f"# Pre-processed Scan Data: {slice_name}\n",
                        f"Chunks: {len(slice_chunks)} | {unit_plural}: {slice_jobs}\n\n",
                        f"## {inventory_title} ({slice_jobs} {unit_plural})\n\n",
                        f"| {column_title} | Components |\n",
                        "|----------|------------|\n",
                    ]
                    for jn in all_job_names:
                        inventory_lines.append(f"| {jn} | (see detail below) |\n")
                    inventory_lines.append("\n---\n\n")
                    header = "".join(inventory_lines)

                    body_parts, actual_jobs = self._format_chunk_markdown(
                        scanner, output_dir, slice_chunks, platform=platform,
                        unit_allowlist=unit_allowlist,
                    )
                    content = header + "".join(body_parts)
                    # Defence-in-depth for scan caches produced before the
                    # scanner-side credential-param fix: inline credential
                    # query-params / RAW() values in verbatim code strings are
                    # masked here so no downstream consumer (Phase 1/2 tasks,
                    # evidence bundles built from these files) ever sees them.
                    content, inline_redactions = redact_inline_credentials(content)
                    if inline_redactions:
                        logger.warning(
                            "Preprocess group %s: masked %d inline credential "
                            "parameter(s) left by an older scan cache",
                            slice_name, inline_redactions,
                        )
                    out_path = preprocess_dir / f"{slice_name}.md"
                    out_path.write_text(content, encoding="utf-8")
                    if len(content) > 100_000:
                        logger.warning(
                            "Pre-processed group %s is large (%d bytes) — "
                            "LLM context may truncate job detail; consider splitting",
                            slice_name, len(content),
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
                    if part_size + text_bytes > config.max_prompt_bundle_bytes and parts[-1]:
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
        max_ref_bundle_bytes = config.max_ref_bundle_bytes  # ~20K tokens, safely under 25K token limit
        # Resolve from source tree (not config.project_root which may be /opt/)
        # __file__ -> services/ -> mcp_server/ -> src/ -> mcp-server/ -> knowledge_base/
        _source_root = Path(__file__).resolve().parent.parent.parent.parent.parent
        ref_dir = _source_root / "intodoc" / "reference-docs"
        if ref_dir.exists():
            agent_ref_map = {
                "_ref_core": [
                    "00_AGENT_BRIEF", "01_ARCHITECTURE_MAP",
                    "02_COMPONENT_INVENTORY", "03_DATA_FLOWS_AND_LINEAGE",
                ],
                "_ref_business_ops": [
                    "04_BUSINESS_RULES_AND_TRANSFORMATIONS",
                    "05_RUNTIME_CONFIG_AND_EXTERNALS",
                    "06_OPERATIONS_TESTING_AND_DEBUGGING",
                ],
                "_ref_user": [
                    "07_QUALITY_SECURITY_RISK_SCORECARD",
                    "08_CHANGE_PLAYBOOK",
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
                    if part_size + text_bytes > max_ref_bundle_bytes and parts[-1]:
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

        # --- Write doc templates for Phase 3 agents ---
        self._write_doc_templates(preprocess_dir)

        return processed_groups

    # ------------------------------------------------------------------ #
    # Phase 1.5 — lightweight quick scan (no LLM, Python regex only)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _run_quick_scan(staging_dir: Path, groups: list[str]) -> None:
        """Scan pre-processed markdown files for common anti-patterns.

        Writes _assessment/quick_scan.md with a priority-ranked list of
        findings per group.  Phase 2 agents read this to calibrate depth.
        This is intentionally fast — regex only, no model calls.
        """
        preprocess_dir = staging_dir / "_preprocess"
        assessment_dir = staging_dir / "_assessment"
        assessment_dir.mkdir(parents=True, exist_ok=True)

        # Patterns: (flag_name, severity, regex_pattern)
        PATTERNS = [
            ("hardcoded_credential", "CRITICAL",
             re.compile(
                 r"(?:password|passwd|pwd|secret|api_?key|token)[^:\n]*:\s*[\"'` ]?(?!context\.|row\.|Var\.)[A-Za-z0-9!@#$%^&*_\-]{4,}",
                 re.IGNORECASE,
             )),
            ("literal_ip_address", "HIGH",
             re.compile(r"\b(?:192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)\b")),
            ("missing_error_handler", "HIGH",
             None),  # checked per-job via component count heuristic
            ("god_profile", "HIGH",
             None),  # checked per-job via component count heuristic
            ("hardcoded_env_value", "MEDIUM",
             re.compile(r'\b(?:localhost|127\.0\.0\.1|http://localhost)\b', re.IGNORECASE)),
            ("missing_retry_logic", "MEDIUM",
             None),  # checked per-job
            ("no_context_vars", "LOW",
             None),  # checked per-job
        ]

        cred_re = PATTERNS[0][2]
        ip_re = PATTERNS[1][2]
        env_re = PATTERNS[4][2]

        findings: dict[str, list[tuple[str, str, str]]] = {}  # group -> [(severity, flag, detail)]

        for group in groups:
            # Account for split groups (e.g. DEU_part1, DEU_part2 → group DEU)
            candidate_files = list(preprocess_dir.glob(f"{group}*.md"))
            if not candidate_files:
                continue

            group_flags: list[tuple[str, str, str]] = []
            job_component_counts: dict[str, int] = {}
            job_tmap_counts: dict[str, int] = {}
            job_error_handler: dict[str, bool] = {}
            job_has_context_var: dict[str, bool] = {}
            current_job: str | None = None

            for md_file in candidate_files:
                try:
                    content = md_file.read_text(encoding="utf-8")
                except OSError:
                    continue

                for line in content.splitlines():
                    # Track the current executable design without conflating a
                    # Talend Route with a Talend Job.
                    heading_match = _DESIGN_HEADING_RE.match(line)
                    if heading_match:
                        current_job = heading_match.group(1).strip()
                        job_component_counts.setdefault(current_job, 0)
                        job_tmap_counts.setdefault(current_job, 0)
                        job_error_handler.setdefault(current_job, False)
                        job_has_context_var.setdefault(current_job, False)
                    elif current_job:
                        # Count components
                        if line.startswith("#### ") and "(" in line:
                            job_component_counts[current_job] = (
                                job_component_counts.get(current_job, 0) + 1
                            )
                            # Count tMap components
                            if "tMap" in line:
                                job_tmap_counts[current_job] = (
                                    job_tmap_counts.get(current_job, 0) + 1
                                )
                            # Detect error handling components
                            if any(ec in line for ec in ("tLogCatcher", "tDie", "tWarn", "tRun")):
                                job_error_handler[current_job] = True

                        # Detect context variable usage
                        if "context." in line.lower():
                            job_has_context_var[current_job] = True

                    # Credential patterns
                    if cred_re and cred_re.search(line):
                        detail = line.strip()[:100]
                        group_flags.append(("CRITICAL", "hardcoded_credential", detail))

                    # Literal IP addresses
                    if ip_re and ip_re.search(line):
                        detail = line.strip()[:80]
                        group_flags.append(("HIGH", "literal_ip_address", detail))

                    # Localhost/hardcoded env
                    if env_re and env_re.search(line):
                        detail = line.strip()[:80]
                        group_flags.append(("MEDIUM", "hardcoded_env_value", detail))

            # Per-job heuristic checks
            for job, count in job_component_counts.items():
                if count > config.qs_god_profile_threshold:
                    group_flags.append(
                        ("HIGH", "god_profile", f"{job}: {count} components")
                    )
                elif count > config.qs_large_job_threshold:
                    group_flags.append(
                        ("MEDIUM", "large_job", f"{job}: {count} components")
                    )

            for job, count in job_tmap_counts.items():
                if count > config.qs_complex_tmap_threshold:
                    group_flags.append(
                        ("HIGH", "complex_tmap", f"{job}: {count} tMap steps")
                    )

            for job, has_handler in job_error_handler.items():
                if not has_handler and job_component_counts.get(job, 0) > 3:
                    group_flags.append(
                        ("HIGH", "missing_error_handler", f"{job}: no tLogCatcher/tDie/tWarn")
                    )

            for job, has_ctx in job_has_context_var.items():
                if not has_ctx and job_component_counts.get(job, 0) > 2:
                    group_flags.append(
                        ("LOW", "no_context_vars", f"{job}: no context.* references found")
                    )

            if group_flags:
                findings[group] = group_flags

        # Sort groups by severity score
        sev_score = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
        group_scores: list[tuple[int, str]] = []
        for group, flags in findings.items():
            score = sum(sev_score.get(s, 0) for s, _, _ in flags)
            group_scores.append((score, group))
        group_scores.sort(reverse=True)

        lines: list[str] = [
            "# Quick Scan Report\n\n",
            "Generated by Phase 1.5 lightweight scan (no LLM).\n",
            "Phase 2 agents: read this to calibrate analysis depth.\n\n",
            "## Priority Ranking\n\n",
            "| Priority | Group | Critical | High | Medium | Low |\n",
            "|----------|-------|----------|------|--------|-----|\n",
        ]
        for rank, (score, group) in enumerate(group_scores, 1):
            flags = findings[group]
            c = sum(1 for s, _, _ in flags if s == "CRITICAL")
            h = sum(1 for s, _, _ in flags if s == "HIGH")
            m = sum(1 for s, _, _ in flags if s == "MEDIUM")
            lo = sum(1 for s, _, _ in flags if s == "LOW")
            lines.append(f"| {rank} | {group} | {c} | {h} | {m} | {lo} |\n")

        # Groups with no findings
        no_findings = [g for g in groups if g not in findings]
        if no_findings:
            for g in sorted(no_findings):
                lines.append(f"| — | {g} | 0 | 0 | 0 | 0 |\n")

        lines.append("\n## Findings Detail\n\n")
        for _, group in group_scores:
            flags = findings[group]
            lines.append(f"### {group}\n\n")
            for severity, flag, detail in sorted(flags, key=lambda x: -sev_score.get(x[0], 0)):
                lines.append(f"- **{severity}** `{flag}`: {detail}\n")
            lines.append("\n")

        output_path = assessment_dir / "quick_scan.md"
        output_path.write_text("".join(lines), encoding="utf-8")
        logger.info(
            "Quick scan complete: %d groups flagged, %d total findings — %s",
            len(findings), sum(len(f) for f in findings.values()), output_path,
        )

    # ------------------------------------------------------------------ #
    # _doc_templates.md — written to _preprocess/ for Phase 3 agents
    # ------------------------------------------------------------------ #

    @staticmethod
    def _write_doc_templates(preprocess_dir: Path) -> None:
        """Write strict document section specs so Phase 3 agents have a stable template.

        This prevents post-hoc patching by defining required sections, optional
        sections, and naming conventions upfront.
        """
        content = """\
# Document Templates

Phase 3 agents: read this BEFORE writing any document.
These templates define the required structure for every output file.
Do NOT add sections not listed here without a clear content reason.
Do NOT rename sections.

---

## 00_AGENT_BRIEF.md
Required sections (in order):
1. Project Orientation
2. Documentation Map
3. Onboarding
4. Common Tasks
5. Known Traps
6. Glossary

## 01_ARCHITECTURE_MAP.md
Required sections:
1. Topology
2. Entry Points
3. Dependencies
4. Boundaries

---

## 02_COMPONENT_INVENTORY.md
Required sections:
1. Canonical Unit Inventory
2. Source Artifacts
3. Components
4. Dependencies
5. Scanner-Limited Evidence

---

## 03_DATA_FLOWS_AND_LINEAGE.md
Required sections:
1. Data Flow
2. Lineage
3. Transformation
4. Validation

---

## 04_BUSINESS_RULES_AND_TRANSFORMATIONS.md
Required sections:
1. Business Rules
2. Transformation
3. Hardcoded Values
4. Impact

---

## 05_RUNTIME_CONFIG_AND_EXTERNALS.md
Required sections:
1. Runtime Config
2. External Systems
3. Credentials
4. Environment

---

## 06_OPERATIONS_TESTING_AND_DEBUGGING.md
Required sections:
1. Run
2. Debug
3. Verify
4. Recovery
5. Testing

---

## 07_QUALITY_SECURITY_RISK_SCORECARD.md
Required sections:
1. Score
2. Confidence
3. Evidence
4. Red Flags
5. Quick Wins
6. Unknowns

---

## 08_CHANGE_PLAYBOOK.md
Required sections:
1. Change Impact
2. Inspect
3. Test
4. Rollback

---

## Naming & Formatting Rules
- All filenames: exactly as shown above
- Every doc MUST have a table of contents as the first section
- Use plain ASCII tables only (no Unicode box-drawing: no ─, │, ┼)
- Business rule tags: BR-{GROUP}-NNN (e.g. BR-DEU-001)
- Risk severity: CRITICAL / HIGH / MEDIUM / LOW (all caps)
- Job references: always include full job name, not just partial
- Component references: include platform-specific identifiers where present
- Evidence Register confidence cells must start with plain unformatted HIGH / MEDIUM / LOW text;
  do not bold, italicize, quote, or wrap that leading token in backticks; notes may follow it
"""
        (preprocess_dir / "_doc_templates.md").write_text(content, encoding="utf-8")
        logger.info("Wrote _doc_templates.md (%d bytes)", len(content))

    async def generate_docs(
        self,
        project_id: str,
        investigation_model: str | None = None,
        model: str | None = None,
        write_model: str | None = None,
        plugin_pack_id: int | None = None,
        nexus_pack_ids: list[str] | None = None,
        nexus_pack_snapshot: NexusPackSnapshot | None = None,
        nexus_presentation: NexusPresentation | None = None,
        live_evidence: bool = True,
        resume: bool = True,
        run_id: int | None = None,
        doc_generation_provider: str | None = None,
        instruction_pack_dir: str | None = None,
        enterprise_mode: bool | None = None,
        scoped_docs: set[str] | None = None,
        partition=None,
        initiating_user_id: int | None = None,
    ) -> dict:
        """Spawn Claude Code CLI to generate docs for a project.

        When resume=True (default), checks for existing docs_staging/ artifacts
        and skips already-completed phases. Set resume=False for a clean run.

        ``plugin_pack_id`` is an optional per-run override. When supplied it
        takes precedence over the project's saved ``plugin_pack_id``. Pass
        ``None`` (the default) to keep the existing per-project behaviour.

        ``partition`` (a ``partition_planner.Partition``) scopes this run to one
        composite sub-project: preprocessing, the facts manifest, coverage and
        the call budget see only that partition's units, and all output lands
        under ``output/{project_id}/{subproject}/`` (its own docs_staging,
        generation lock and atomic swap). ``None`` => whole-project run with
        today's layout, unchanged.

        Returns dict with success, cost_usd, duration_ms, input_tokens, etc.
        """
        from mcp_server.providers.custom.partition_planner import (
            WHOLE_PROJECT_PARTITION,
            partition_output_subdir,
        )

        output_dir = config.project_root / "output" / project_id
        unit_allowlist: set[str] | None = None
        if partition is not None:
            subdir = partition_output_subdir(partition.name)
            if subdir is not None:
                output_dir = output_dir / subdir
            if partition.name != WHOLE_PROJECT_PARTITION:
                unit_allowlist = set(partition.unit_names)

        # Use a staging directory so old docs survive if generation fails
        staging_dir = output_dir / "docs_staging"
        try:
            acquire_generation_lock(
                staging_dir,
                project_id=project_id,
                run_id=run_id,
                stale_after_seconds=config.doc_generation_lock_stale_seconds,
            )
        except GenerationLockError as exc:
            return {
                "success": False,
                "error": str(exc),
                "model_name": model or config.doc_generation_model,
                "write_model_name": write_model or config.doc_generation_write_model,
            }

        write_run_manifest(
            staging_dir,
            run_id=run_id,
            project_id=project_id,
            phase="startup",
            status="running",
            extra={"resume_requested": resume, "enterprise_mode": config.doc_generation_enterprise_mode_default if enterprise_mode is None else enterprise_mode},
        )

        # Detect resume state before deciding whether to wipe staging
        resume_state: dict | None = None
        if resume and staging_dir.exists():
            resume_state = self._detect_resume_state(staging_dir)

        # A scoped reingestion (scoped_docs set) arrives with incremental staging
        # already prepared by setup_incremental_staging: the unchanged groups'
        # analyses are restored under _analysis/ and only the changed groups were
        # removed. Wiping here would delete those restored analyses and force a
        # full Phase 1/2 regeneration of every group. Preserve them.
        if resume_state is None and scoped_docs is None:
            # Fresh run — wipe any leftover staging, preserving the active lock.
            for child in list(staging_dir.iterdir()) if staging_dir.exists() else []:
                if child.name == ".generation.lock":
                    continue
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            write_run_manifest(
                staging_dir,
                run_id=run_id,
                project_id=project_id,
                phase="startup",
                status="running",
                extra={"fresh_staging": True},
            )

        for d in [
            staging_dir,
            staging_dir / "_analysis",
            staging_dir / "_assessment",
            output_dir / "logs",
        ]:
            d.mkdir(parents=True, exist_ok=True)
            d.chmod(0o777)

        try:
            nexus_execution_receipt = build_nexus_execution_receipt(
                pack_ids=nexus_pack_ids or [],
                snapshot=nexus_pack_snapshot,
                presentation=nexus_presentation,
            )
            persist_nexus_execution_receipt(staging_dir, nexus_execution_receipt)
        except Exception as exc:
            write_run_manifest(
                staging_dir,
                run_id=run_id,
                project_id=project_id,
                phase="startup",
                status="failed",
                extra={"error": str(exc)},
            )
            release_generation_lock(staging_dir, run_id=run_id)
            return {
                "success": False,
                "error": str(exc),
                "model_name": model or config.doc_generation_model,
                "write_model_name": write_model or config.doc_generation_write_model,
            }

        # Absolute paths for the prompt (subprocess cwd differs from config.project_root)
        staging_abs = str(staging_dir)
        logs_abs = str(output_dir / "logs")

        # Look up the project's platform from the DB
        plugin_pack_dir: Path | None = None
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            project = await registry.get_project(project_id)
            if project is None:
                raise RuntimeError(
                    f"Project {project_id!r} not found in database — cannot determine platform"
                )
            platform = project.platform
            effective_plugin_pack_id = (
                plugin_pack_id
                if plugin_pack_id is not None
                else getattr(project, "plugin_pack_id", None)
            )
            if effective_plugin_pack_id is not None:
                pack_row = (
                    await db.execute(
                        select(PluginPack).where(PluginPack.id == effective_plugin_pack_id)
                    )
                ).scalar_one_or_none()
                if pack_row is None:
                    error = (
                        "Selected Pack is no longer registered; governed generation requires "
                        "a certified Git/PR Pack."
                    )
                    write_run_manifest(
                        staging_dir,
                        run_id=run_id,
                        project_id=project_id,
                        phase="startup",
                        status="failed",
                        extra={"error": error},
                    )
                    release_generation_lock(staging_dir, run_id=run_id)
                    return {
                        "success": False,
                        "error": error,
                        "model_name": model or config.doc_generation_model,
                        "write_model_name": write_model or config.doc_generation_write_model,
                    }
                try:
                    await pack_governance.require_generation_authority(pack_row)
                except PackGovernanceDenied as exc:
                    # This is the TOCTOU check after the queued route check.
                    error = str(exc)
                    write_run_manifest(
                        staging_dir,
                        run_id=run_id,
                        project_id=project_id,
                        phase="startup",
                        status="failed",
                        extra={"error": error},
                    )
                    release_generation_lock(staging_dir, run_id=run_id)
                    return {
                        "success": False,
                        "error": error,
                        "model_name": model or config.doc_generation_model,
                        "write_model_name": write_model or config.doc_generation_write_model,
                    }
                plugin_pack_dir = Path(pack_row.path)

        # Compute project metrics early (needed for preprocessing decisions)
        scanner = ScannerService()
        scan_output_dir = str(config.intodoc_output_dir / project_id)
        chunk_defs = scanner.get_chunk_definitions(
            scan_output_dir, unit_allowlist=unit_allowlist,
        )
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

        # Universal extraction cache (services/extraction_cache.py). _preprocess/
        # is a pure function of the scan artifacts + the preprocess/redaction
        # code version + platform + the partition unit allowlist, and never of
        # the pack, so a second pack over the same project reuses this layer
        # instead of re-running the deterministic extraction.
        #
        # No extra staleness check is needed here: compute_extraction_key is
        # built on compute_scan_fingerprint, which hashes scan_result.json,
        # scan/manifest.json, scan/lineage.json and scan/landscape.json
        # (business_topology/evidence.py::_SCAN_ARTIFACTS). Any change to the
        # scan therefore produces a DIFFERENT key and a cache MISS, so the
        # mtime-based stale-scan invalidation above stays authoritative and
        # cannot be defeated by a restore.
        extraction_key = ""
        restored_groups: list[str] | None = None
        if (
            not skip_preprocess
            # A scoped reingestion prepares staging deliberately (unchanged
            # groups' analyses restored, changed groups removed) — mirror the
            # fresh-staging guard above and never overwrite that.
            and scoped_docs is None
            and config.extraction_cache_enabled
        ):
            extraction_key = compute_extraction_key(
                Path(scan_output_dir),
                platform=platform,
                unit_allowlist=unit_allowlist,
            )
            if extraction_key:
                try:
                    restored_groups = restore_extraction(
                        extraction_cache_dir(output_dir), extraction_key, staging_dir,
                    )
                except OSError as exc:
                    logger.warning("Extraction cache restore failed: %s", exc)
                    restored_groups = None

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
        elif restored_groups is not None:
            await self.log_manager.emit(
                project_id,
                f"Extraction cache HIT ({extraction_key[:12]}) — reused "
                f"{len(restored_groups)} pre-processed group bundle(s), "
                "skipping preprocessing",
                level="info",
            )
            preprocess_groups = restored_groups
        else:
            await self.log_manager.emit(
                project_id, "Pre-processing scan data...", level="info",
            )
            preprocess_groups = await self._preprocess_scan_data(
                project_id, staging_dir, platform,
                total_jobs=total_jobs,
                unit_allowlist=unit_allowlist,
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
            "partition": partition.name if partition is not None else None,
            "model": model or config.doc_generation_model,
            "scan_mtime": scan_manifest_path.stat().st_mtime if scan_manifest_path.exists() else 0,
            "created_at": time.time(),
        }, indent=2), encoding="utf-8")

        total_groups = len(preprocess_groups)

        # Phase 1.5 — lightweight quick scan (Python regex, no LLM)
        # Only run on fresh preprocess (not when resuming with existing scan)
        assessment_dir = staging_dir / "_assessment"
        assessment_dir.mkdir(parents=True, exist_ok=True)
        quick_scan_path = assessment_dir / "quick_scan.md"
        if not skip_preprocess or not quick_scan_path.exists():
            self._run_quick_scan(staging_dir, preprocess_groups)

        # Determine which analysis guide file sub-agents should read
        analysis_guide_file = "_analysis_guide.md"
        if not (staging_dir / "_preprocess" / "_analysis_guide.md").exists():
            analysis_guide_file = "_analysis_prompts*.md"  # legacy fallback

        # Resolve provider and models through the shared model strategy registry.
        # Per-instance overrides from the dashboard Models tab take precedence
        # over the env-var defaults so superadmins can change the effective
        # model at runtime without restarting the service.
        analysis_default = await instance_settings_service.get_value(
            "doc_gen_analysis_model", default=config.doc_generation_model
        )
        write_default = await instance_settings_service.get_value(
            "doc_gen_write_model", default=config.doc_generation_write_model
        )
        model_resolution = resolve_generation_models(
            provider_type=doc_generation_provider,
            model=model,
            write_model=write_model,
            default_provider=config.doc_generation_provider,
            default_analysis_model=analysis_default or config.doc_generation_model,
            default_write_model=write_default or config.doc_generation_write_model,
            default_claude_cli_model=config.claude_model,
        )
        resolved_provider = model_resolution.provider_type
        effective_model = model_resolution.analysis_model
        effective_write_model = model_resolution.write_model

        connector_runtime = None
        if (
            model_resolution.uses_opencode_cli
            and config.opencode_connectors_enabled
            and run_id is not None
            and initiating_user_id is not None
        ):
            # The connector service owns DB snapshotting and credentials.  The
            # OpenCode provider receives only opaque run authority.  Any
            # preflight failure is supplementary-evidence degradation, never a
            # reason to switch on connectors for a legacy/non-user run.
            try:
                from mcp_server.connectors.service import ConnectorService

                connector_runtime = await ConnectorService.create_opencode_runtime(
                    generate_run_id=run_id,
                    project_id=project_id,
                    user_id=initiating_user_id,
                    # ``resume`` is a caller preference; only an actual
                    # recovered staging state must reuse an old snapshot.
                    # Fresh runs commonly use resume=True by default.
                    resume=resume_state is not None,
                )
            except Exception:
                logger.warning(
                    "Connector preflight failed for OpenCode run %s/%s; continuing without live evidence",
                    project_id,
                    run_id,
                    exc_info=True,
                )

        # Build DocGenContext and hand off to the configured provider
        ctx = DocGenContext(
            project_id=project_id,
            staging_dir=staging_dir,
            staging_abs=staging_abs,
            logs_abs=logs_abs,
            preprocess_groups=preprocess_groups,
            total_jobs=total_jobs,
            total_groups=total_groups,
            effective_model=effective_model,
            write_model=effective_write_model,
            platform=platform,
            analysis_guide_file=analysis_guide_file,
            enterprise_mode=config.doc_generation_enterprise_mode_default if enterprise_mode is None else enterprise_mode,
            resume_state=resume_state,
            scoped_docs=scoped_docs,
            partition=partition,
            nexus_presentation=nexus_presentation,
            live_evidence=live_evidence,
            run_id=run_id,
            connector_runtime=connector_runtime,
        )

        provider = get_provider(
            provider_type=resolved_provider,
            model=effective_model,
            investigation_model=investigation_model,
            write_model=effective_write_model,
            log_manager=self.log_manager,
            instruction_pack_dir=instruction_pack_dir,
            plugin_pack_dir=plugin_pack_dir,
        )

        if plugin_pack_dir is not None and not model_resolution.uses_opencode_cli:
            provider = build_plugin_runner(
                plugin_pack_dir,
                inner_provider=provider,
                model_provider=getattr(provider, "model_provider", None),
            )
            await self.log_manager.emit(
                project_id,
                f"Using plugin pack: {plugin_pack_dir.name} (v{provider.manifest.version})",
                level="info",
            )

        resolved_main_model = getattr(provider, "model_provider", None)
        resolved_investigation_model = getattr(provider, "investigation_provider", None)
        resolved_write_model_obj = getattr(provider, "write_provider", None)
        investigation_model_name = getattr(
            resolved_investigation_model,
            "model_name",
            investigation_model or config.doc_generation_investigation_model or effective_model,
        )
        main_model_name = getattr(resolved_main_model, "model_name", effective_model)
        write_model_name = getattr(resolved_write_model_obj, "model_name", effective_write_model)
        if model_resolution.uses_opencode_cli:
            # OpenCode owns its adaptive phase sessions and uses one selected
            # route throughout; the legacy per-phase model defaults are not
            # consulted by this provider.
            investigation_model_name = effective_model
            main_model_name = effective_model
            write_model_name = effective_model
        provider_details = (
            f"provider={resolved_provider} "
            f"phase1={investigation_model_name} "
            f"phase2={main_model_name} "
            f"phase3={write_model_name}"
        )
        await self.log_manager.emit(
            project_id,
            f"Resolved doc generation routing: {provider_details}",
            level="info",
        )

        async def _log_cb(pid: str, message: str, level: str = "info") -> None:
            await self.log_manager.emit(pid, message, level=level)

        try:
            refresh_generation_lock(staging_dir, run_id=run_id)
            write_run_manifest(
                staging_dir,
                run_id=run_id,
                project_id=project_id,
                phase="provider",
                status="running",
                extra={"provider": resolved_provider, "model": effective_model, "write_model": effective_write_model},
            )
            async with LockHeartbeat(staging_dir, run_id=run_id):
                agent_result = await provider.run(ctx, _log_cb)
            result = agent_result.to_dict()
            write_run_manifest(
                staging_dir,
                run_id=run_id,
                project_id=project_id,
                phase="provider",
                status="completed" if result.get("success") else "failed",
                extra={"error": result.get("error")},
            )
            return result
        except Exception as exc:
            write_run_manifest(
                staging_dir,
                run_id=run_id,
                project_id=project_id,
                phase="provider",
                status="failed",
                extra={"error": str(exc)},
            )
            return {
                "success": False,
                "error": str(exc),
                "model_name": effective_model,
                "write_model_name": effective_write_model,
            }
        finally:
            release_generation_lock(staging_dir, run_id=run_id)
