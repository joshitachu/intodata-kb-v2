"""Per-unit verbatim evidence bundles for the Grounded experiments pipeline.

The Grounded roles previously reasoned only over ``FACT_LEDGER.json`` -- a
structural fact sheet (counts, call edges, component-type tallies) -- plus the
raw frozen scan JSON. That is why an investigation role could write "mapping
expressions ... unknown" and "content decode blocked" while a job file holding
hundreds of code routines sat, unread, in its packet.

This module is the experiments-side answer to that gap: it deterministically
extracts a per-unit VERBATIM evidence bundle straight from the frozen scan job
JSON (no model in the path) and produces readable Markdown that Nexus stages
into the role packet. It is intentionally standalone -- the ``experiments``
package must not import the legacy ``providers.custom`` pipeline (enforced by
the architecture-boundary tests) -- and is adapted from that pipeline's
``TargetEvidenceBuilder`` so both pipelines expose code the same way.

Bundle contract (identical intent to the legacy per-target bundle):
- verbatim evidence (code / tMap expressions / SQL / context values) is never
  trimmed before derived niceties (wiring tables, param detail); an over-budget
  bundle ships whole with a logged warning rather than silently dropping code;
- every context-variable row carries provenance (job-embedded snapshot vs
  shared repository context group) plus an explicit DIVERGES flag when the two
  disagree;
- the bundle records which component names and evidence categories it holds so
  callers can check downstream output against it.

Reusability: ``UnitEvidenceExtractor.extract`` takes one already-parsed unit
dict plus its callers/callees and optional shared context groups. Slice 3's
Phase 2 fan-out can call it per unit or per batch without any knowledge of the
staging layout.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any, Mapping, Sequence

logger = logging.getLogger("mcp_server.experiments.unit_evidence")

# Default per-unit verbatim byte budget. Matches the legacy pipeline's
# MCP_PHASE3_TARGET_EVIDENCE_BYTES default so both produce comparably deep
# evidence; overridable via MCP_GROUNDED_UNIT_EVIDENCE_BYTES (see config.py).
DEFAULT_UNIT_EVIDENCE_BYTES = 120_000

# Component references like tJavaRow_1 / tMSSqlInput_2 -- the shape used across
# Talend scan output and generated documentation.
COMPONENT_REF_RE = re.compile(r"\bt[A-Z][A-Za-z0-9]*_\d+\b")

# Params worth quoting verbatim in the component inventory. Everything not
# matched here is scanner noise for documentation purposes (screen offsets,
# label positions, repository ids, ...).
HIGH_SIGNAL_PARAM_NAMES = frozenset(
    {
        "DIE_ON_ERROR", "FAILON", "DIE_ON_CHILD_ERROR", "TRANSMIT_WHOLE_CONTEXT",
        "QUERY", "TABLE", "TABLE_ACTION", "DATA_ACTION", "COMMIT_EVERY",
        "USE_BATCH_SIZE", "BATCH_SIZE", "USE_SHARED_CONNECTION",
        "FILE_NAME", "FILENAME", "DESTINATION", "SOURCE_DERECTORY",
        "ENABLE_COPY_DIRECTORY", "REMOVE_FILE", "REPLACE_FILE", "CREATE_DIRECTORY",
        "AS_STRING", "CONDITION", "PROPERTIES", "DBNAME", "SCHEMA_DB",
        "HOST", "PORT", "USER", "METHOD", "ENCODING",
    }
)
HIGH_SIGNAL_PARAM_PATTERN = re.compile(
    r"(URL|ENDPOINT|URI|PATH|DIRECTORY|FOLDER|TRIGGER|SCHEDULE|CRON)", re.IGNORECASE
)
_CODE_PARAM_NAMES = ("CODE_START", "CODE_MAIN", "CODE", "CODE_END", "CODE_ONCE", "IMPORT")

# Evidence categories a bundle can hold.
CATEGORY_CODE = "code"
CATEGORY_TMAP = "tmap"
CATEGORY_SQL = "sql"
CATEGORY_CONTEXT = "context"


@dataclass(frozen=True)
class UnitEvidenceBundle:
    """One per-unit verbatim evidence bundle, ready to stage as readable text."""

    unit_name: str
    text: str
    categories: frozenset[str]
    component_refs: frozenset[str]
    truncated: bool


def safe_name(name: str) -> str:
    """Filesystem-safe rendering of a unit name for a staged bundle filename."""

    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)


def param_map(component: Mapping[str, Any]) -> dict[str, str]:
    """Return a component's params as a first-wins name->value map."""

    out: dict[str, str] = {}
    for param in component.get("params", []) or []:
        name = str(param.get("name") or "")
        if name and name not in out:
            out[name] = str(param.get("value") or "")
    return out


def component_code(component: Mapping[str, Any]) -> str:
    """Prefer the top-level code field (new scans); fall back to CODE params."""

    top = component.get("code")
    if isinstance(top, str) and top.strip():
        return top
    params = param_map(component)
    parts: list[str] = []
    for name in _CODE_PARAM_NAMES:
        value = params.get(name, "")
        # Commented-out import placeholders are not code; presenting them as
        # "the code" is exactly the misleading signal that produced fabricated
        # analyses in the legacy pipeline.
        if name == "IMPORT" and value.strip().startswith("//"):
            continue
        if value.strip():
            parts.append(value)
    return "\n".join(parts)


def extract_component_refs(text: str) -> set[str]:
    return set(COMPONENT_REF_RE.findall(text or ""))


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


class UnitEvidenceExtractor:
    """Build one verbatim Markdown evidence bundle per canonical unit."""

    def __init__(self, *, max_bytes: int = DEFAULT_UNIT_EVIDENCE_BYTES) -> None:
        if max_bytes < 1:
            raise ValueError("unit evidence byte budget must be positive")
        self.max_bytes = max_bytes

    # -- section renderers -------------------------------------------------

    @staticmethod
    def _render_header(unit_name: str, unit: Mapping[str, Any]) -> str:
        lines = [
            f"# Unit Evidence: {unit_name}",
            "",
            "Deterministically extracted from the frozen intodoc scan (no model in",
            "the path). All values are verbatim as captured by the scanner, after",
            "credential redaction. This bundle is the source of truth for this",
            "unit's implemented (code-level) facts.",
            "",
            f"- Unit: {unit_name}",
            f"- Group: {unit.get('group') or 'Global'}",
            f"- Source artifact: {unit.get('path') or 'unknown'}",
            f"- Version: {unit.get('version') or 'unknown'}",
            f"- Component count: {len(unit.get('components', []) or [])}",
        ]
        return "\n".join(lines)

    @staticmethod
    def _render_callers_callees(
        callers: Sequence[str],
        callees: Sequence[str],
    ) -> str:
        lines = ["## CALLERS AND CALLEES", ""]
        lines.append("Called by:")
        lines.extend([f"- {name}" for name in sorted(set(callers))] or ["- (none found in scan)"])
        lines.append("")
        lines.append("Calls (child units):")
        lines.extend([f"- {name}" for name in sorted(set(callees))] or ["- (none)"])
        return "\n".join(lines)

    @staticmethod
    def _render_inventory(unit: Mapping[str, Any], include_params: bool) -> str:
        lines = ["## COMPONENT INVENTORY", ""]
        for comp in unit.get("components", []) or []:
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
    def _render_code(unit: Mapping[str, Any]) -> str:
        blocks: list[str] = []
        for comp in unit.get("components", []) or []:
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
    def _render_tmap(unit: Mapping[str, Any]) -> str:
        blocks: list[str] = []
        for tmap in unit.get("tmap_data", []) or []:
            for entry in (tmap.get("var_entries", []) or []) + (tmap.get("entries", []) or []):
                expression = str(entry.get("expression") or "").strip()
                if not expression:
                    continue
                table = str(entry.get("table_name") or "")
                column = str(entry.get("column_name") or "")
                blocks.append(f"### {table}.{column}\n\n```java\n{expression}\n```")
        if not blocks:
            return ""
        return "## TMAP EXPRESSIONS (VERBATIM)\n\n" + "\n\n".join(blocks)

    @staticmethod
    def _render_sql(unit: Mapping[str, Any]) -> str:
        blocks: list[str] = []
        for comp in unit.get("components", []) or []:
            params = param_map(comp)
            query = params.get("QUERY", "")
            if len(query.strip()) < 10:
                continue
            unique = str(comp.get("unique_name") or "")
            blocks.append(f"### {unique}\n\n```sql\n{query.strip()}\n```")
        if not blocks:
            return ""
        return "## SQL QUERIES (VERBATIM)\n\n" + "\n\n".join(blocks)

    @staticmethod
    def _render_context(
        unit: Mapping[str, Any],
        shared_contexts: Sequence[Mapping[str, Any]],
    ) -> str:
        shared_index: dict[str, list[dict[str, Any]]] = {}
        for group in shared_contexts:
            for var in group.get("variables", []) or []:
                shared_index.setdefault(str(var.get("name") or ""), []).append(
                    {
                        "group": str(group.get("name") or ""),
                        "values": dict(var.get("values", {}) or {}),
                    }
                )

        lines = [
            "## CONTEXT VARIABLES (PER ENVIRONMENT, WITH PROVENANCE)",
            "",
            "Provenance legend: 'job-embedded' values are the snapshot stored inside",
            "this unit's own export; 'shared group' values come from the repository-",
            "level context group of the same variable name. When both exist and",
            "disagree, the row is flagged DIVERGES -- document both, never mix them.",
            "",
            "| variable | type | values per environment | provenance |",
            "|---|---|---|---|",
        ]
        seen_vars: set[str] = set()
        for group in unit.get("context_groups", []) or []:
            group_name = str(group.get("name") or "inline")
            provenance = (
                "job-embedded"
                if group_name == "inline"
                else f"job-embedded (group '{group_name}')"
            )
            for var in group.get("variables", []) or []:
                var_name = str(var.get("name") or "")
                seen_vars.add(var_name)
                var_type = str(var.get("type") or "")
                values = dict(var.get("values", {}) or {})
                rendered = ", ".join(
                    f"{env}={_escape_cell(str(val))}" for env, val in values.items()
                )
                flags: list[str] = []
                for shared in shared_index.get(var_name, []):
                    diverging = [
                        env
                        for env, val in values.items()
                        if env in shared["values"] and str(shared["values"][env]) != str(val)
                    ]
                    if diverging:
                        flags.append(
                            f"DIVERGES from shared group '{shared['group']}' on "
                            f"{', '.join(sorted(diverging))}"
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
                    f"| {var_name} | | {rendered} | shared group '{shared['group']}' "
                    "(not embedded in this unit) |"
                )
        if shared_only_rows:
            lines.append("")
            lines.append("Shared-group variables not embedded in this unit's export:")
            lines.append("")
            lines.append("| variable | type | values per environment | provenance |")
            lines.append("|---|---|---|---|")
            lines.extend(shared_only_rows)
        return "\n".join(lines)

    @staticmethod
    def _render_connections(unit: Mapping[str, Any]) -> str:
        rows = []
        for conn in unit.get("connections", []) or []:
            source = str(conn.get("source") or "")
            target = str(conn.get("target") or "")
            connector = str(conn.get("connector_name") or "")
            if source and target:
                rows.append(f"| {source} | {connector} | {target} |")
        if not rows:
            return ""
        return "\n".join(
            [
                "## COMPONENT CONNECTIONS",
                "",
                "| source | trigger/flow | target |",
                "|---|---|---|",
                *rows,
            ]
        )

    # -- assembly ----------------------------------------------------------

    def extract(
        self,
        unit: Mapping[str, Any],
        *,
        shared_contexts: Sequence[Mapping[str, Any]] = (),
        callers: Sequence[str] = (),
        callees: Sequence[str] = (),
    ) -> UnitEvidenceBundle:
        """Build the verbatim bundle for one already-parsed scan unit dict."""

        unit_name = str(unit.get("name") or "").strip()
        if not unit_name:
            raise ValueError("unit evidence requires a named scan unit")

        code_md = self._render_code(unit)
        tmap_md = self._render_tmap(unit)
        sql_md = self._render_sql(unit)
        context_md = self._render_context(unit, shared_contexts)
        callers_md = self._render_callers_callees(callers, callees)
        connections_md = self._render_connections(unit)
        inventory_md = self._render_inventory(unit, include_params=True)
        header_md = self._render_header(unit_name, unit)

        def _assemble(inventory: str, connections: str) -> str:
            parts = [
                header_md,
                callers_md,
                inventory,
                code_md,
                tmap_md,
                sql_md,
                context_md,
                connections,
            ]
            return "\n\n".join(p for p in parts if p) + "\n"

        truncated = False
        content = _assemble(inventory_md, connections_md)
        # Trim derived niceties first, verbatim evidence last (never silently).
        if len(content.encode("utf-8")) > self.max_bytes:
            content = _assemble(inventory_md, "")
        if len(content.encode("utf-8")) > self.max_bytes:
            content = _assemble(self._render_inventory(unit, include_params=False), "")
        if len(content.encode("utf-8")) > self.max_bytes:
            # Verbatim evidence alone exceeds the budget: keep it and say so --
            # an over-budget honest bundle beats a trimmed misleading one.
            truncated = True
            content += (
                "\n\n> NOTE: this bundle exceeds the configured evidence byte budget"
                f" ({self.max_bytes}); derived sections were dropped but verbatim"
                " evidence is kept in full.\n"
            )
            logger.warning(
                "unit_evidence: bundle for %s exceeds budget (%d bytes)",
                unit_name,
                len(content.encode("utf-8")),
            )

        categories: set[str] = set()
        if code_md:
            categories.add(CATEGORY_CODE)
        if tmap_md:
            categories.add(CATEGORY_TMAP)
        if sql_md:
            categories.add(CATEGORY_SQL)
        if context_md and ("job-embedded" in context_md or "shared group" in context_md):
            categories.add(CATEGORY_CONTEXT)
        component_refs = extract_component_refs(content) | {
            str(c.get("unique_name") or "") for c in unit.get("components", []) or []
        }
        return UnitEvidenceBundle(
            unit_name=unit_name,
            text=content,
            categories=frozenset(categories),
            component_refs=frozenset(r for r in component_refs if r),
            truncated=truncated,
        )
