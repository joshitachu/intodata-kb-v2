"""Per-target deterministic evidence bundles built from the intodoc scan cache.

Phase 3 doc writers previously received unit evidence only through Phase 2's
LLM narration (doc packs) plus large unfiltered derived maps; verbatim code,
tMap expressions, and SQL were buried or re-told with loss. This module
extracts a per-unit evidence bundle straight from the scan cache with no LLM
in the path: what the scanner captured is what the writer reads.

Bundle contract:
- verbatim evidence (code / tMap expressions / SQL / context values) is never
  trimmed before derived niceties (wiring tables, param detail);
- every context-variable row carries provenance (job-embedded snapshot vs
  shared repository context group vs Default environment) plus an explicit
  divergence flag when the two sources disagree;
- the bundle records which component names and evidence categories it holds
  so fabrication validators can check doc output against it.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp_server.providers.custom.source_context import redact_inline_credentials

if TYPE_CHECKING:  # avoid a runtime import cycle (noncode_evidence imports this module)
    from mcp_server.providers.custom.noncode_evidence import NonCodeAssociationPlan
    from mcp_server.providers.custom.runtime_evidence import RuntimeEvidenceStore

logger = logging.getLogger("mcp_server.providers.custom.target_evidence")

# Component references like tJavaRow_1 / tMSSqlInput_2 — the shape used across
# Talend scan output and the generated docs.
COMPONENT_REF_RE = re.compile(r"\bt[A-Z][A-Za-z0-9]*_\d+\b")

# Params worth quoting verbatim in the component inventory. Everything not
# matched here is scanner noise for documentation purposes (screen offsets,
# label positions, repository ids, ...). Public: also reused by the job graph
# service (services/job_graph_service.py) for node param summaries.
HIGH_SIGNAL_PARAM_NAMES = frozenset({
    "DIE_ON_ERROR", "FAILON", "DIE_ON_CHILD_ERROR", "TRANSMIT_WHOLE_CONTEXT",
    "QUERY", "TABLE", "TABLE_ACTION", "DATA_ACTION", "COMMIT_EVERY",
    "USE_BATCH_SIZE", "BATCH_SIZE", "USE_SHARED_CONNECTION",
    "FILE_NAME", "FILENAME", "DESTINATION", "SOURCE_DERECTORY",
    "ENABLE_COPY_DIRECTORY", "REMOVE_FILE", "REPLACE_FILE", "CREATE_DIRECTORY",
    "AS_STRING", "CONDITION", "PROPERTIES", "DBNAME", "SCHEMA_DB",
    "HOST", "PORT", "USER", "METHOD", "ENCODING",
})
HIGH_SIGNAL_PARAM_PATTERN = re.compile(
    r"(URL|ENDPOINT|URI|PATH|DIRECTORY|FOLDER|TRIGGER|SCHEDULE|CRON)", re.IGNORECASE
)
_CODE_PARAM_NAMES = ("CODE_START", "CODE_MAIN", "CODE", "CODE_END", "CODE_ONCE", "IMPORT")

# Evidence categories a bundle can hold; fabrication validators key off these.
CATEGORY_CODE = "code"
CATEGORY_TMAP = "tmap"
CATEGORY_SQL = "sql"
CATEGORY_CONTEXT = "context"


@dataclass(frozen=True)
class TargetEvidence:
    """Result of building one per-target bundle."""

    path: Path
    unit_name: str
    component_refs: frozenset[str]
    categories: frozenset[str]
    truncated: bool
    # Deterministic protected-fact ledger (P1.5): scanner facts the doc must
    # copy verbatim and may never recompute. Keys are fact names, values the
    # exact rendered strings from the bundle's PROTECTED SCANNER FACTS table.
    protected_facts: dict[str, str] = field(default_factory=dict)


def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)


def _load_jobs(scan_output_dir: Path) -> list[dict[str, Any]]:
    """Load all job dicts from the split scan dir, falling back to the mono file."""
    scan_dir = scan_output_dir / "scan"
    jobs: list[dict[str, Any]] = []
    jobs_dir = scan_dir / "jobs"
    if jobs_dir.exists():
        for group_dir in sorted(p for p in jobs_dir.iterdir() if p.is_dir()):
            for path in sorted(group_dir.glob("*.json")):
                try:
                    jobs.append(json.loads(path.read_text(encoding="utf-8", errors="replace")))
                except (json.JSONDecodeError, OSError) as exc:
                    logger.warning("target_evidence: unreadable job file %s: %s", path, exc)
        return jobs
    mono = scan_output_dir / "scan_result.json"
    if mono.exists():
        try:
            data = json.loads(mono.read_text(encoding="utf-8", errors="replace"))
            return list(data.get("jobs", []) or [])
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("target_evidence: unreadable scan_result.json: %s", exc)
    return []


def _load_shared_contexts(scan_output_dir: Path) -> list[dict[str, Any]]:
    path = scan_output_dir / "scan" / "contexts.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return list(data) if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("target_evidence: unreadable contexts.json: %s", exc)
        return []


def param_map(component: dict[str, Any]) -> dict[str, str]:
    """Return a component's params as a first-wins name->value map (public: shared with job graph service)."""
    out: dict[str, str] = {}
    for param in component.get("params", []) or []:
        name = str(param.get("name") or "")
        if name and name not in out:
            out[name] = str(param.get("value") or "")
    return out


def load_jobs(scan_output_dir: Path) -> list[dict[str, Any]]:
    """Public wrapper over the split/mono scan-cache job loader."""
    return _load_jobs(scan_output_dir)


def build_unit_index(jobs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Resolve every scan unit by the identity the harness passes downstream.

    ``build_facts_manifest`` sets ``UnitFact.name = job_id or display_name``, so
    a unit is addressed by its unique ``job_id`` when the scanner assigned one
    (display names duplicate across composite export roots) and by display name
    only for older scans lacking ``job_id``. Indexing by display name first and
    ``job_id`` last means the unique id always wins over a duplicated name.

    Public helper: shared by ``TargetEvidenceBuilder`` and the non-code Tier-3
    association so both key units the same way.
    """
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for job in jobs:
        job_id = str(job.get("job_id") or "")
        if job_id:
            by_id.setdefault(job_id, job)
        name = str(job.get("name") or "")
        if name:
            by_name.setdefault(name, job)
    return {**by_name, **by_id}


def component_code(component: dict[str, Any]) -> str:
    """Prefer the top-level code field (new scans); fall back to CODE params."""
    top = component.get("code")
    if isinstance(top, str) and top.strip():
        return top
    params = param_map(component)
    parts = []
    for name in _CODE_PARAM_NAMES:
        value = params.get(name, "")
        # Commented-out import placeholders are not code; including them as
        # "the code" is exactly the misleading signal that broke run 50 docs.
        if name == "IMPORT" and value.strip().startswith("//"):
            continue
        if value.strip():
            parts.append(value)
    return "\n".join(parts)


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def extract_component_refs(text: str) -> set[str]:
    return set(COMPONENT_REF_RE.findall(text or ""))


class TargetEvidenceBuilder:
    """Builds `_analysis/_evidence_{doc}.md` bundles for unit-scoped Phase 3 docs."""

    def __init__(
        self,
        scan_output_dir: Path,
        staging_dir: Path,
        max_bytes: int,
        *,
        noncode_plan: "NonCodeAssociationPlan | None" = None,
        noncode_section_bytes: int = 0,
        runtime_store: "RuntimeEvidenceStore | None" = None,
        runtime_section_bytes: int = 0,
    ) -> None:
        self.scan_output_dir = scan_output_dir
        self.staging_dir = staging_dir
        self.max_bytes = max_bytes
        # Observed production behaviour for this unit (run counts, outcomes,
        # latency, redacted failure headlines). Prefetched once per run by the
        # harness -- this builder is synchronous and must not do I/O.
        self._runtime_store = runtime_store
        self._runtime_section_bytes = runtime_section_bytes
        # Optional Tier-3 documented-context (non-code) attachment. Additive
        # redacted intent appended after the verbatim evidence; bounded by its
        # own byte budget and dropped before any verbatim evidence, never ahead.
        self._noncode_plan = noncode_plan
        self._noncode_section_bytes = noncode_section_bytes
        self._jobs: list[dict[str, Any]] | None = None
        self._shared: list[dict[str, Any]] | None = None
        self._job_index: dict[str, dict[str, Any]] | None = None

    # ── lazy caches ──────────────────────────────────────────────────

    def _all_jobs(self) -> list[dict[str, Any]]:
        if self._jobs is None:
            self._jobs = _load_jobs(self.scan_output_dir)
        return self._jobs

    def _shared_contexts(self) -> list[dict[str, Any]]:
        if self._shared is None:
            self._shared = _load_shared_contexts(self.scan_output_dir)
        return self._shared

    def _job_lookup(self) -> dict[str, dict[str, Any]]:
        """Resolve a unit by the identity the harness actually passes.

        `build_facts_manifest` sets ``UnitFact.name = job_id or display_name``
        (validators.py), so the harness hands us the unique ``job_id`` when the
        scanner assigned one (it must, since display names are duplicated across
        export roots) and the display name only for older scans where ``job_id``
        is absent. Indexing by ``job_id`` first and ``name`` second matches both
        cases; job_id keys win over display-name keys so duplicated names never
        shadow the unique id.
        """
        if self._job_index is None:
            self._job_index = build_unit_index(self._all_jobs())
        return self._job_index

    def _is_esb_route(self, unit_name: str) -> bool:
        """Whether this unit is a long-running ESB route rather than a batch job.

        Scanner-computed (`is_esb_route`), never inferred from the name -- an
        `r` prefix or `_Route` suffix is a convention, not a guarantee.

        A scan can hold several entries under one unit name (different exported
        versions of the same artifact; werkorder carries
        `Maximo2Quintiq_Workorder_Route` twice, flagged False and True). ANY
        flagged entry makes this a route: mislabelling a route as a batch job
        publishes a 76-day "run duration", while the converse merely omits a
        clarifying note.
        """
        return any(
            bool(job.get("is_esb_route"))
            for job in self._all_jobs()
            if str(job.get("name") or "") == unit_name
        )

    def _job_by_unit(self, unit_name: str) -> dict[str, Any] | None:
        return self._job_lookup().get(unit_name)

    # ── section renderers ────────────────────────────────────────────

    @staticmethod
    def protected_facts(unit_name: str, job: dict[str, Any]) -> dict[str, str]:
        """Deterministic protected-fact ledger for one unit (P1.5).

        These are scanner facts a document must copy verbatim and may never
        recompute by model inference. Rendered into the bundle's PROTECTED
        SCANNER FACTS table and checked by ``protected_fact_errors``.
        """
        components = job.get("components", []) or []
        inactive = sorted(
            str(c.get("unique_name") or "")
            for c in components
            if c.get("active") is False
        )
        return {
            "kind": str(job.get("kind") or job.get("type") or "job"),
            "group": str(job.get("group") or "Global"),
            "version": str(job.get("version") or "unknown"),
            "component_count": str(len(components)),
            "connection_count": str(len(job.get("connections", []) or [])),
            "inactive_components": ", ".join(inactive) or "none",
            "project_source_path": str(job.get("path") or "unknown"),
        }

    @classmethod
    def _render_header(cls, unit_name: str, job: dict[str, Any]) -> str:
        facts = cls.protected_facts(unit_name, job)
        fact_rows = "\n".join(
            f"| {name} | {_escape_cell(value)} |" for name, value in facts.items()
        )
        lines = [
            f"# Target Evidence: {unit_name}",
            "",
            "Deterministically extracted from the intodoc scan cache (no model in",
            "the path). All values are verbatim as captured by the scanner, after",
            "credential redaction. Treat this file as the source of truth for this",
            "unit's technical facts; the analysis doc pack is interpretation.",
            "",
            f"- Unit: {unit_name}",
            f"- Group: {job.get('group') or 'Global'}",
            f"- Source artifact: {job.get('path') or 'unknown'}",
            f"- Version: {job.get('version') or 'unknown'}",
            f"- Component count: {len(job.get('components', []) or [])}",
            "",
            "## PROTECTED SCANNER FACTS",
            "",
            "Copy this table verbatim into the document's Protected Scanner Facts",
            "section. Never recompute, round, or restate these values.",
            "",
            "| fact | value |",
            "|---|---|",
            fact_rows,
        ]
        return "\n".join(lines)

    def _render_callers_callees(self, unit_name: str, job: dict[str, Any]) -> str:
        callees = sorted({
            str(ref.get("child_job") or "")
            for ref in job.get("trunjob_refs", []) or []
            if ref.get("child_job")
        })
        caller_lines: list[str] = []
        for other in self._all_jobs():
            other_name = str(other.get("name") or "")
            if other_name == unit_name:
                continue
            children = {str(r.get("child_job") or "") for r in other.get("trunjob_refs", []) or []}
            if unit_name in children:
                runjob_names = sorted(
                    str(c.get("unique_name") or "")
                    for c in other.get("components", []) or []
                    if str(c.get("component_type") or "") == "tRunJob"
                )
                suffix = f" (tRunJob components: {', '.join(runjob_names)})" if runjob_names else ""
                caller_lines.append(f"- {other_name}{suffix}")
        lines = ["## CALLERS AND CALLEES", ""]
        lines.append("Called by:")
        lines.extend(sorted(caller_lines) or ["- (none found in scan)"])
        lines.append("")
        lines.append("Calls (tRunJob children):")
        lines.extend([f"- {c}" for c in callees] or ["- (none)"])
        return "\n".join(lines)

    @staticmethod
    def _render_inventory(job: dict[str, Any], include_params: bool) -> str:
        lines = ["## COMPONENT INVENTORY", ""]
        for comp in job.get("components", []) or []:
            unique = str(comp.get("unique_name") or "")
            ctype = str(comp.get("component_type") or "")
            lines.append(f"- {unique} ({ctype})")
            if not include_params:
                continue
            params = param_map(comp)
            for name in sorted(params):
                if name in ("QUERY",) or name in _CODE_PARAM_NAMES:
                    continue  # quoted verbatim in their own sections
                if name in HIGH_SIGNAL_PARAM_NAMES or HIGH_SIGNAL_PARAM_PATTERN.search(name):
                    value = params[name]
                    if value.strip():
                        lines.append(f"  - {name} = {_escape_cell(value)}")
        return "\n".join(lines)

    @staticmethod
    def _render_code(job: dict[str, Any]) -> str:
        blocks: list[str] = []
        for comp in job.get("components", []) or []:
            code = component_code(comp)
            if not code.strip():
                continue
            unique = str(comp.get("unique_name") or "")
            ctype = str(comp.get("component_type") or "")
            blocks.append(f"### {unique} ({ctype})\n\n```java\n{code.strip()}\n```")
        if not blocks:
            return ""
        return "## CUSTOM CODE (VERBATIM)\n\n" + "\n\n".join(blocks)

    @staticmethod
    def _render_tmap(job: dict[str, Any]) -> str:
        blocks: list[str] = []
        for tmap in job.get("tmap_data", []) or []:
            entry_lines: list[str] = []
            for entry in (tmap.get("var_entries", []) or []) + (tmap.get("entries", []) or []):
                expression = str(entry.get("expression") or "").strip()
                if not expression:
                    continue
                table = str(entry.get("table_name") or "")
                column = str(entry.get("column_name") or "")
                entry_lines.append(f"### {table}.{column}\n\n```java\n{expression}\n```")
            if entry_lines:
                blocks.extend(entry_lines)
        if not blocks:
            return ""
        return "## TMAP EXPRESSIONS (VERBATIM)\n\n" + "\n\n".join(blocks)

    @staticmethod
    def _render_sql(job: dict[str, Any]) -> str:
        blocks: list[str] = []
        for comp in job.get("components", []) or []:
            params = param_map(comp)
            query = params.get("QUERY", "")
            if len(query.strip()) < 10:
                continue
            unique = str(comp.get("unique_name") or "")
            blocks.append(f"### {unique}\n\n```sql\n{query.strip()}\n```")
        if not blocks:
            return ""
        return "## SQL QUERIES (VERBATIM)\n\n" + "\n\n".join(blocks)

    def _referenced_shared_groups(self, job: dict[str, Any]) -> list[dict[str, Any]]:
        """Return only the shared context groups this unit actually references.

        Each job-embedded context var carries the ``repository_context_id`` of
        the repository context group it originates from; that id matches a shared
        group's ``context_id``. A unit typically references a handful of groups,
        not the whole repository, so scoping to referenced groups keeps a
        unit's bundle from carrying every shared group's variables (163 groups /
        ~1185 vars on the werkorder scan) it never uses -- bloat that crowded out
        the verbatim evidence and pushed every bundle past the byte budget.

        Older scan formats express no group linkage (no ``repository_context_id``
        on vars, no ``context_id`` on groups). When no linkage id is present we
        cannot scope, so we fall back to the full shared set -- these scans are
        small and dropping shared context would lose divergence detection.
        """
        referenced_ids: set[str] = set()
        for group in job.get("context_groups", []) or []:
            for var in group.get("variables", []) or []:
                rid = str(var.get("repository_context_id") or "")
                if rid:
                    referenced_ids.add(rid)
        shared = self._shared_contexts()
        if not referenced_ids:
            return shared
        return [g for g in shared if str(g.get("context_id") or "") in referenced_ids]

    def _render_context(self, job: dict[str, Any]) -> str:
        shared_index: dict[str, list[dict[str, Any]]] = {}
        for group in self._referenced_shared_groups(job):
            for var in group.get("variables", []) or []:
                shared_index.setdefault(str(var.get("name") or ""), []).append({
                    "group": str(group.get("name") or ""),
                    "values": dict(var.get("values", {}) or {}),
                })

        lines = [
            "## CONTEXT VARIABLES (PER ENVIRONMENT, WITH PROVENANCE)",
            "",
            "Provenance legend: 'job-embedded' values are the snapshot stored inside",
            "this job's own export; 'shared group' values come from the repository-",
            "level context group of the same variable name. When both exist and",
            "disagree, the row is flagged DIVERGES -- document both, never mix them.",
            "",
            "| variable | type | values per environment | provenance |",
            "|---|---|---|---|",
        ]
        seen_vars: set[str] = set()
        for group in job.get("context_groups", []) or []:
            group_name = str(group.get("name") or "inline")
            provenance = "job-embedded" if group_name == "inline" else f"job-embedded (group '{group_name}')"
            for var in group.get("variables", []) or []:
                var_name = str(var.get("name") or "")
                seen_vars.add(var_name)
                var_type = str(var.get("type") or "")
                values = dict(var.get("values", {}) or {})
                rendered = ", ".join(f"{env}={_escape_cell(str(val))}" for env, val in values.items())
                flags: list[str] = []
                for shared in shared_index.get(var_name, []):
                    diverging = [
                        env for env, val in values.items()
                        if env in shared["values"] and str(shared["values"][env]) != str(val)
                    ]
                    if diverging:
                        flags.append(
                            f"DIVERGES from shared group '{shared['group']}' on {', '.join(sorted(diverging))}"
                        )
                provenance_cell = provenance + ("; " + "; ".join(flags) if flags else "")
                lines.append(f"| {var_name} | {var_type} | {rendered} | {provenance_cell} |")

        shared_only_rows: list[str] = []
        for var_name in sorted(shared_index):
            if var_name in seen_vars:
                continue
            for shared in shared_index[var_name]:
                rendered = ", ".join(
                    f"{env}={_escape_cell(str(val))}" for env, val in shared["values"].items()
                )
                shared_only_rows.append(
                    f"| {var_name} | | {rendered} | shared group '{shared['group']}' (not embedded in this job) |"
                )
        if shared_only_rows:
            lines.append("")
            lines.append("Shared-group variables not embedded in this job's export:")
            lines.append("")
            lines.append("| variable | type | values per environment | provenance |")
            lines.append("|---|---|---|---|")
            lines.extend(shared_only_rows)
        return "\n".join(lines)

    @staticmethod
    def _render_connections(job: dict[str, Any]) -> str:
        rows = []
        for conn in job.get("connections", []) or []:
            source = str(conn.get("source") or "")
            target = str(conn.get("target") or "")
            connector = str(conn.get("connector_name") or "")
            if source and target:
                rows.append(f"| {source} | {connector} | {target} |")
        if not rows:
            return ""
        return "\n".join([
            "## COMPONENT CONNECTIONS",
            "",
            "| source | trigger/flow | target |",
            "|---|---|---|",
            *rows,
        ])

    # ── assembly ─────────────────────────────────────────────────────

    def build(self, doc_name: str, unit_name: str) -> TargetEvidence | None:
        job = self._job_by_unit(unit_name)
        if job is None:
            logger.warning("target_evidence: unit %s not found in scan cache", unit_name)
            return None

        code_md = self._render_code(job)
        tmap_md = self._render_tmap(job)
        sql_md = self._render_sql(job)
        context_md = self._render_context(job)
        callers_md = self._render_callers_callees(unit_name, job)
        connections_md = self._render_connections(job)
        inventory_md = self._render_inventory(job, include_params=True)
        header_md = self._render_header(unit_name, job)
        noncode_md = ""
        if self._noncode_plan is not None and self._noncode_section_bytes > 0:
            noncode_md = self._noncode_plan.unit_section(
                unit_name, max_bytes=self._noncode_section_bytes
            ) or ""
        runtime_md = ""
        if self._runtime_store is not None and self._runtime_section_bytes > 0:
            runtime_md = self._runtime_store.section_for(
                unit_name,
                is_esb_route=self._is_esb_route(unit_name),
                max_bytes=self._runtime_section_bytes,
            )

        def _assemble(
            inventory: str, connections: str, noncode: str, runtime: str = ""
        ) -> str:
            parts = [
                header_md, callers_md, inventory, code_md, tmap_md, sql_md,
                context_md, connections, noncode, runtime,
            ]
            return "\n\n".join(p for p in parts if p) + "\n"

        truncated = False
        # Trim order (least to most important): derived connections, then param
        # detail, then the additive Tier-3 non-code section, and only then --
        # last resort -- ship verbatim evidence alone. Verbatim code/SQL/tMap/
        # context is never trimmed ahead of the documented-context section.
        content = _assemble(inventory_md, connections_md, noncode_md, runtime_md)
        if len(content.encode("utf-8")) > self.max_bytes:
            content = _assemble(inventory_md, "", noncode_md, runtime_md)
        if len(content.encode("utf-8")) > self.max_bytes:
            # Runtime facts are compact and high-signal, so they survive the
            # derived-detail trims above; they still go before any verbatim
            # scanner evidence is touched.
            content = _assemble(inventory_md, "", noncode_md)
        if len(content.encode("utf-8")) > self.max_bytes:
            content = _assemble(self._render_inventory(job, include_params=False), "", noncode_md)
        if len(content.encode("utf-8")) > self.max_bytes:
            content = _assemble(self._render_inventory(job, include_params=False), "", "")
        if len(content.encode("utf-8")) > self.max_bytes:
            # Verbatim evidence alone exceeds the budget: keep it and say so —
            # an over-budget honest bundle beats a trimmed misleading one.
            truncated = True
            content += (
                "\n\n> NOTE: this bundle exceeds the configured evidence byte budget"
                f" ({self.max_bytes}); derived sections were dropped but verbatim"
                " evidence is kept in full.\n"
            )
            logger.warning(
                "target_evidence: bundle for %s exceeds budget (%d bytes)",
                unit_name, len(content.encode("utf-8")),
            )

        # Defence-in-depth for scan caches produced before the scanner-side
        # credential-param fix: inline credential query-params / RAW() values
        # in verbatim code strings are masked before the bundle can reach a
        # doc-writer agent. Narrow by construction (query-string context or
        # the Camel RAW() wrapper), so verbatim code evidence stays intact.
        content, inline_redactions = redact_inline_credentials(content)
        if inline_redactions:
            logger.warning(
                "target_evidence: masked %d inline credential parameter(s) in "
                "the %s bundle (older scan cache)", inline_redactions, unit_name,
            )

        analysis_dir = self.staging_dir / "_analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        path = analysis_dir / f"_evidence_{_safe_name(doc_name)}.md"
        path.write_text(content, encoding="utf-8")

        categories = set()
        if code_md:
            categories.add(CATEGORY_CODE)
        if tmap_md:
            categories.add(CATEGORY_TMAP)
        if sql_md:
            categories.add(CATEGORY_SQL)
        if context_md:
            categories.add(CATEGORY_CONTEXT)
        component_refs = extract_component_refs(content) | {
            str(c.get("unique_name") or "") for c in job.get("components", []) or []
        }
        return TargetEvidence(
            path=path,
            unit_name=unit_name,
            component_refs=frozenset(r for r in component_refs if r),
            categories=frozenset(categories),
            truncated=truncated,
            protected_facts=self.protected_facts(unit_name, job),
        )


def filter_fact_table(source: Path, unit_name: str, dest_dir: Path, max_bytes: int) -> Path | None:
    """Write a unit-filtered copy of a pipe-table fact map next to the analysis.

    Keeps headings, table headers/separators, and rows mentioning the unit
    (boundary-aware so 'AssetCheck_GetLocation' does not match
    'AssetCheck_GetLocations'). Returns the filtered path, the original path
    when the file has no pipe table to filter, or None when unreadable.
    """
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    unit_re = re.compile(rf"\b{re.escape(unit_name)}(?![A-Za-z0-9])")
    separator_re = re.compile(r"\|[\s:|-]+\|?")
    kept: list[str] = []
    saw_table_row = False
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            kept.append(line)
            continue
        if stripped.startswith("|"):
            saw_table_row = True
            is_separator = separator_re.fullmatch(stripped) is not None
            next_stripped = lines[index + 1].strip() if index + 1 < len(lines) else ""
            is_column_header = separator_re.fullmatch(next_stripped) is not None
            if is_separator or is_column_header or unit_re.search(line):
                kept.append(line)
            continue
        if not saw_table_row:
            kept.append(line)
    if not saw_table_row:
        return source
    filtered = "\n".join(kept).rstrip() + "\n"
    encoded = filtered.encode("utf-8")
    if len(encoded) > max_bytes:
        filtered = encoded[:max_bytes].decode("utf-8", errors="ignore")
        filtered += "\n\n> NOTE: filtered map truncated at configured byte cap.\n"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"_filtered_{_safe_name(unit_name)}_{source.name}"
    dest.write_text(filtered, encoding="utf-8")
    return dest
