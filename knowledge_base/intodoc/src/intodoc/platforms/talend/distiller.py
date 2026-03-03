"""Talend distiller — strip UI noise and prepare LLM-ready chunks."""

from __future__ import annotations

import hashlib
import json

from rich.console import Console

from intodoc.models import (
    AnalysisChunk,
    Component,
    ComponentParam,
    ContextGroup,
    ContextVariable,
    ParsedJob,
    ProjectManifest,
    ScanResult,
)

console = Console()

# ── Filter constants ────────────────────────────────────────────────

# Names to unconditionally strip
STRIP_PARAMS = frozenset({
    # UI / visual
    "HINT", "HELP", "COMMENT", "PREVIEW", "IMAGE",
    "COMP_DEFAULT_FILE_DIR", "CONNECTION_FORMAT",
    # Property / schema metadata
    "REPOSITORY_PROPERTY_TYPE", "REPOSITORY_SCHEMA_TYPE",
    "PROPERTY:REPOSITORY_PROPERTY_TYPE", "PROPERTY:REPOSITORY_SCHEMA_TYPE",
    "SCHEMA:SCHEMA_TYPE", "UNIQUE_NAME",
    "CHANGE_HASH_AND_EQUALS_FOR_BIGDECIMAL",
    # Stats / monitoring
    "TSTATCATCHER_STATS",
    # Boilerplate defaults (same on nearly every component)
    "MAPPING", "RESULT_SIZE", "TEMPORARY_DATASET_NAME",
    "BASE_FILE_PATH", "MEMORY_HEAP_MAX_SIZE",
    "FLUSHONROW_NUM",
    # Redacted placeholders (no information content)
    "[CREDENTIAL_PATH]", "AUTH_PASSWORD",
    # Formatting defaults (always the same across all jobs)
    "DECIMAL_SEPARATOR", "THOUSANDS_SEPARATOR", "ROWSEPARATOR", "CSVROWSEPARATOR",
    "FIELDSEPARATOR", "FIELD_DELIMITER", "ESCAPE_CHAR", "TEXT_ENCLOSURE",
    "ENCODING", "ENCODING:ENCODING_TYPE",
    # Internal Talend IDs / wiring
    "FAMILY", "SCHEMA_OPT_NUM",
    "SCHEMA_REJECT:SCHEMA_TYPE", "SCHEMA:REPOSITORY_SCHEMA_TYPE",
    "QUERYSTORE:QUERYSTORE_TYPE", "TRIGGER_INPUT_1:COMPONENT_LIST",
    "module.main.schema:SCHEMA_TYPE",
    # API endpoint defaults (always same URL)
    "REGION_ENDPOINT_BQ", "REGION_ENDPOINT_GS",
    # Boilerplate defaults always same value
    "NB_ROWS", "NB_RANDOM", "HEADER", "SPLIT_EVERY", "STREAMNAME",
    "FILENAMETEXT", "CONTEXT_NAME", "RECEIVE_TIMEOUT_DESC",
    # Dev-machine paths / internal settings
    "TEMP_DIR",
    # Loop defaults (always 1/true)
    "FROM", "STEP", "ITERATION", "WHILELOOP", "DECLARATION",
})

# Param name prefixes to strip
STRIP_PREFIXES = (
    "offsetLabel", "posX", "posY", "sizeX", "sizeY",
)

# Substrings in param name that indicate noise
STRIP_SUBSTRINGS = ("TOKEN", "SECRET", "WARNING")

# Default / boilerplate values to always strip
STRIP_VALUES = frozenset({
    "false", "0", "auto", "utf-8", "iso-8859-15", "2000000",
})

# CHECK (boolean) params to KEEP — all others stripped
KEEP_CHECK_PARAMS = frozenset({
    "DIE_ON_ERROR", "USE_BATCH_SIZE", "ENABLE_PARALLEL_EXECUTION",
    "USE_EXISTING_CONNECTION", "APPEND_MODE",
    "CREATE_TABLE_IF_NOT_EXISTS", "CREATE_TABLE_IF_NOT_EXIST",
    "USE_LEGACY_SQL",
})

# CLOSED_LIST params to KEEP — all others stripped
KEEP_CLOSED_LIST_PARAMS = frozenset({
    "TABLE_ACTION", "DATA_ACTION", "ACTION_ON_DATA", "ACTION_ON_TABLE",
    "WRITE_MODE", "DBTYPE", "TYPE", "DB_VERSION",
    "DATA_WRITE_MODEL", "KEYS_MANAGEMENT",
})

# Structural components kept even when all params are stripped
STRUCTURAL_COMPONENTS = frozenset({
    "tPrejob", "tPostjob", "tUnite", "tFlowToIterate",
})


class TalendDistiller:
    """Strip Talend-specific noise and group jobs into analysis chunks."""

    STRIP_PARAMS = STRIP_PARAMS
    STRIP_PREFIXES = STRIP_PREFIXES
    STRIP_SUBSTRINGS = STRIP_SUBSTRINGS
    STRIP_VALUES = STRIP_VALUES
    KEEP_CHECK_PARAMS = KEEP_CHECK_PARAMS
    KEEP_CLOSED_LIST_PARAMS = KEEP_CLOSED_LIST_PARAMS

    def __init__(self) -> None:
        # Populated during distill() — params constant across all jobs
        self._constant_params: set[str] = set()

    def distill(self, scan_result: ScanResult) -> list[AnalysisChunk]:
        """Group jobs into LLM-sized chunks by group, stripping noise."""
        # ── Phase 3.5: Compute project-wide constant params ──────────
        self._constant_params = self._find_constant_params(scan_result)

        # Track raw token estimate using fast Pydantic serializer (3.1/3.2)
        raw_tokens = sum(len(j.model_dump_json()) for j in scan_result.jobs) // 4

        # Group jobs by group
        group_jobs: dict[str, list[ParsedJob]] = {}
        for job in scan_result.jobs:
            group = job.group or "Global"
            group_jobs.setdefault(group, []).append(job)

        chunks: list[AnalysisChunk] = []
        # Cache per-job token estimates to avoid re-serialization (3.1)
        job_token_cache: dict[int, int] = {}

        for group, jobs in sorted(group_jobs.items()):
            # Distill each job
            distilled_jobs = [self._distill_job(j) for j in jobs]

            # Compute per-job token estimates once (3.2)
            group_tokens = 0
            for dj in distilled_jobs:
                jid = id(dj)
                if jid not in job_token_cache:
                    job_token_cache[jid] = len(dj.model_dump_json()) // 4
                group_tokens += job_token_cache[jid]

            # If chunk is too large, split into sub-chunks
            if group_tokens > 40_000:
                sub_chunks = self._split_chunk(
                    group, distilled_jobs, scan_result, job_token_cache
                )
                chunks.extend(sub_chunks)
            else:
                chunk_id = f"{group}_all"
                content_bytes = b"".join(
                    dj.model_dump_json().encode() for dj in distilled_jobs
                )
                sha = hashlib.sha256(content_bytes).hexdigest()

                # Find related connections and contexts
                related_conns = self._match_connections_for_jobs(distilled_jobs, scan_result)
                related_ctxs = [
                    c for c in scan_result.context_groups
                    if group.lower() in c.name.lower()
                ]
                related_routines = self._match_routines_for_jobs(distilled_jobs, scan_result)

                chunks.append(AnalysisChunk(
                    chunk_id=chunk_id,
                    group=group,
                    jobs=distilled_jobs,
                    related_connections=related_conns,
                    related_contexts=related_ctxs,
                    related_routines=related_routines,
                    token_estimate=group_tokens,
                    sha256=sha,
                ))

        # ── Phase 3.3: Filter manifest per-chunk ─────────────────────
        for chunk in chunks:
            job_names = {j.name for j in chunk.jobs}
            chunk.manifest = self._filter_manifest(scan_result.manifest, job_names)

        # Deduplicate inline contexts that duplicate chunk-level contexts
        for chunk in chunks:
            self._dedup_contexts(chunk)

        # ── Phase 3.7: Compact context variable environments ─────────
        for chunk in chunks:
            self._compact_contexts(chunk)

        # ── Phase 3.6: Extract chunk-level defaults ──────────────────
        for chunk in chunks:
            self._extract_defaults(chunk)

        # Recompute token estimates after all compression passes
        for chunk in chunks:
            chunk.token_estimate = sum(
                len(j.model_dump_json()) // 4 for j in chunk.jobs
            )

        # Print reduction report
        distilled_tokens = sum(c.token_estimate for c in chunks)
        self._print_report(
            total_jobs=len(scan_result.jobs),
            raw_tokens=raw_tokens,
            distilled_tokens=distilled_tokens,
            chunk_count=len(chunks),
            constant_params_stripped=len(self._constant_params),
        )

        return chunks

    # ── Phase 3.5: Project-wide constant param detection ─────────────

    @staticmethod
    def _find_constant_params(scan_result: ScanResult) -> set[str]:
        """Find params that have only 1 unique value across ALL jobs.

        These carry zero information (the LLM can't distinguish jobs by them)
        and can be safely stripped from every component.

        Only triggers when the param appears on 3+ components (avoids
        false positives on small projects or single-job test cases).
        """
        param_value_sets: dict[str, set[str]] = {}
        param_occurrence_count: dict[str, int] = {}
        for job in scan_result.jobs:
            for comp in job.components:
                for p in comp.params:
                    val = str(p.value).strip().strip('"')
                    param_value_sets.setdefault(p.name, set()).add(val)
                    param_occurrence_count[p.name] = param_occurrence_count.get(p.name, 0) + 1

        return {
            name
            for name, vals in param_value_sets.items()
            if len(vals) <= 1 and param_occurrence_count.get(name, 0) >= 3
        }

    # ── Per-param filter ─────────────────────────────────────────────

    def _keep_param(self, p: ComponentParam) -> bool:
        """Decide whether a component parameter should be kept."""
        # 0. Strip project-wide constants (Phase 3.5)
        if p.name in self._constant_params:
            return False

        # 1. Strip None or empty
        if p.value is None or not str(p.value).strip().strip('"'):
            return False

        # 2. Strip by exact name
        if p.name in self.STRIP_PARAMS:
            return False

        # 3. Strip by prefix
        if any(p.name.startswith(pfx) for pfx in self.STRIP_PREFIXES):
            return False

        # 4. Strip by name substring
        name_upper = p.name.upper()
        if any(sub in name_upper for sub in self.STRIP_SUBSTRINGS):
            return False

        # 5. Strip default values
        val = str(p.value).strip().strip('"').lower()
        if val in self.STRIP_VALUES:
            return False

        # 6. Field-type filtering
        ft = (p.field_type or "").upper()
        if ft == "CHECK":
            return p.name in self.KEEP_CHECK_PARAMS
        if ft == "CLOSED_LIST":
            return p.name in self.KEEP_CLOSED_LIST_PARAMS

        # 7. Keep everything else (TEXT, MEMO, etc.)
        return True

    # ── Job distillation ──────────────────────────────────────────────

    def _distill_job(self, job: ParsedJob) -> ParsedJob:
        """Strip noise from a single job."""
        distilled_components: list[Component] = []
        for comp in job.components:
            clean = [p for p in comp.params if self._keep_param(p)]
            # Keep structural components even when empty
            if clean or comp.component_type in STRUCTURAL_COMPONENTS:
                distilled_components.append(
                    comp.model_copy(update={"params": clean})
                )

        return job.model_copy(update={"components": distilled_components})

    # ── Phase 3.3: Manifest filtering per-chunk ──────────────────────

    @staticmethod
    def _filter_manifest(
        manifest: ProjectManifest, job_names: set[str]
    ) -> ProjectManifest:
        """Return a manifest containing only edges involving the chunk's jobs."""
        return ProjectManifest(
            job_calls={k: v for k, v in manifest.job_calls.items() if k in job_names},
            job_called_by={k: v for k, v in manifest.job_called_by.items() if k in job_names},
            job_connections_used={k: v for k, v in manifest.job_connections_used.items() if k in job_names},
            connection_used_by={k: v for k, v in manifest.connection_used_by.items()
                                if any(j in job_names for j in v)},
            job_contexts_used={k: v for k, v in manifest.job_contexts_used.items() if k in job_names},
            routines_used_by={k: v for k, v in manifest.routines_used_by.items()
                              if any(j in job_names for j in v)},
            job_groups={k: v for k, v in manifest.job_groups.items() if k in job_names},
        )

    # ── Phase 3.6: Chunk-level param defaults ────────────────────────

    @staticmethod
    def _extract_defaults(chunk: AnalysisChunk) -> None:
        """Extract params where >90% of components share the same value.

        These become chunk-level defaults; only exceptions are kept on components.
        The defaults are stored in chunk stats for prompt builders to reference.
        """
        # Count value frequencies per param name across all components in the chunk
        param_counts: dict[str, dict[str, int]] = {}  # param_name → {value → count}
        total_components = 0

        for job in chunk.jobs:
            for comp in job.components:
                total_components += 1
                for p in comp.params:
                    param_counts.setdefault(p.name, {})
                    val = str(p.value).strip()
                    param_counts[p.name][val] = param_counts[p.name].get(val, 0) + 1

        if total_components < 5:
            return  # Too few components for meaningful defaults

        # Find params where >90% share the same value
        defaults: dict[str, str] = {}
        for name, value_counts in param_counts.items():
            total_occurrences = sum(value_counts.values())
            if total_occurrences < 5:
                continue
            top_value, top_count = max(value_counts.items(), key=lambda x: x[1])
            if top_count / total_occurrences >= 0.9:
                defaults[name] = top_value

        if not defaults:
            return

        # Strip default-value params from individual components; keep exceptions
        for job in chunk.jobs:
            for comp in job.components:
                comp.params = [
                    p for p in comp.params
                    if p.name not in defaults
                    or str(p.value).strip() != defaults[p.name]
                ]

    # ── Phase 3.7: Compact context variable environments ─────────────

    @staticmethod
    def _compact_contexts(chunk: AnalysisChunk) -> None:
        """Compact context variable environments.

        - If all environment values are identical → keep only one representative.
        - If the value is a context reference → keep only the reference.
        """
        all_ctx_groups = list(chunk.related_contexts)
        for job in chunk.jobs:
            all_ctx_groups.extend(job.context_groups)

        for cg in all_ctx_groups:
            for var in cg.variables:
                if not var.values:
                    continue
                unique_vals = set(var.values.values())
                if len(unique_vals) == 1:
                    # All envs have the same value — keep only one
                    representative_val = next(iter(unique_vals))
                    var.values = {"_default": representative_val}

    # ── Connection matching ─────────────────────────────────────────

    @staticmethod
    def _match_connections_for_jobs(
        jobs: list[ParsedJob], scan_result: ScanResult,
    ) -> list:
        """Match metadata connections to jobs via component REPOSITORY refs."""
        from intodoc.models import MetadataConnection

        ref_names: set[str] = set()
        for job in jobs:
            for comp in job.components:
                if comp.metadata_connection_ref:
                    ref_names.add(comp.metadata_connection_ref)

        matched: dict[str, MetadataConnection] = {}
        for conn in scan_result.metadata_connections:
            if conn.name in ref_names:
                matched[conn.name] = conn

        return list(matched.values())

    @staticmethod
    def _match_routines_for_jobs(
        jobs: list[ParsedJob], scan_result: ScanResult,
    ) -> list:
        """Match routines to jobs via job.raw_routines references."""
        from intodoc.models import Routine

        ref_names: set[str] = set()
        for job in jobs:
            ref_names.update(job.raw_routines)

        if not ref_names:
            return []

        return [r for r in scan_result.routines if r.name in ref_names and not r.is_system]

    # ── Context deduplication ─────────────────────────────────────────

    def _dedup_contexts(self, chunk: AnalysisChunk) -> None:
        """Remove inline job context_groups whose variables are already in chunk.related_contexts."""
        if not chunk.related_contexts:
            return

        # Build set of variable names already present at chunk level
        chunk_var_names: set[str] = set()
        for cg in chunk.related_contexts:
            for var in cg.variables:
                chunk_var_names.add(var.name)

        if not chunk_var_names:
            return

        for job in chunk.jobs:
            deduped: list = []
            for cg in job.context_groups:
                # Keep the group if it has any variable NOT already in chunk contexts
                remaining_vars = [
                    v for v in cg.variables if v.name not in chunk_var_names
                ]
                if remaining_vars:
                    deduped.append(cg.model_copy(update={"variables": remaining_vars}))
            job.context_groups = deduped

    # ── Chunking helpers ──────────────────────────────────────────────

    def _split_chunk(
        self,
        group: str,
        jobs: list[ParsedJob],
        scan_result: ScanResult,
        job_token_cache: dict[int, int],
    ) -> list[AnalysisChunk]:
        """Split a large group into smaller chunks (~30K tokens each)."""
        chunks: list[AnalysisChunk] = []
        current_jobs: list[ParsedJob] = []
        current_tokens = 0
        chunk_idx = 0

        for job in jobs:
            jid = id(job)
            if jid not in job_token_cache:
                job_token_cache[jid] = len(job.model_dump_json()) // 4
            job_tokens = job_token_cache[jid]

            if current_tokens + job_tokens > 30_000 and current_jobs:
                chunks.append(
                    self._make_chunk(group, chunk_idx, current_jobs, scan_result)
                )
                chunk_idx += 1
                current_jobs = []
                current_tokens = 0

            current_jobs.append(job)
            current_tokens += job_tokens

        if current_jobs:
            chunks.append(
                self._make_chunk(group, chunk_idx, current_jobs, scan_result)
            )

        return chunks

    def _make_chunk(
        self, group: str, idx: int, jobs: list[ParsedJob],
        scan_result: ScanResult,
    ) -> AnalysisChunk:
        """Create an AnalysisChunk from a list of jobs."""
        content_bytes = b"".join(j.model_dump_json().encode() for j in jobs)
        token_est = len(content_bytes) // 4
        return AnalysisChunk(
            chunk_id=f"{group}_{idx}",
            group=group,
            jobs=jobs,
            related_connections=self._match_connections_for_jobs(jobs, scan_result),
            related_contexts=[
                c for c in scan_result.context_groups
                if group.lower() in c.name.lower()
            ],
            related_routines=self._match_routines_for_jobs(jobs, scan_result),
            token_estimate=token_est,
            sha256=hashlib.sha256(content_bytes).hexdigest(),
        )

    # ── Reporting ─────────────────────────────────────────────────────

    def _print_report(
        self,
        total_jobs: int,
        raw_tokens: int,
        distilled_tokens: int,
        chunk_count: int,
        constant_params_stripped: int = 0,
    ) -> None:
        """Print before/after token reduction stats."""
        if raw_tokens == 0:
            return
        reduction_pct = (1 - distilled_tokens / raw_tokens) * 100
        avg_per_chunk = distilled_tokens // chunk_count if chunk_count else 0

        console.print()
        console.print("[bold cyan]--- Distiller Token Reduction Report ---[/bold cyan]")
        console.print(f"  Total jobs:              {total_jobs:,}")
        console.print(f"  Raw tokens:              {raw_tokens:,}")
        console.print(f"  Distilled tokens:        {distilled_tokens:,}")
        console.print(f"  Reduction:               {reduction_pct:.1f}%")
        console.print(f"  Chunks:                  {chunk_count}")
        console.print(f"  Avg tokens/chunk:        {avg_per_chunk:,}")
        if constant_params_stripped:
            console.print(f"  Constant params removed: {constant_params_stripped}")
        console.print("[bold cyan]----------------------------------------[/bold cyan]")
        console.print()
