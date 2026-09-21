"""Custom async documentation-generation harness.

Python owns phase sequencing, coverage validation, artifact checks, and cleanup.
The model is only asked to write bounded markdown sections from supplied files.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import shutil
import time
from dataclasses import dataclass, field as dc_field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

from mcp_server.config import config
from mcp_server.providers.base import AgentResult, DocGenContext, DocGenerationProvider, LogCallback
from mcp_server.services.pack_loader import InstructionPack
from mcp_server.services.plugin_schema import load_plugin_manifest
from mcp_server.providers.custom.investigator import (
    ChunkManifestInvestigator,
    InvestigationError,
    InvestigatorLimits,
    SingleShotInvestigator,
    investigation_summary,
    write_investigation_artifacts,
)
from mcp_server.providers.custom.engineering_facts import EngineeringFactsService
from mcp_server.providers.custom.evidence_agents import (
    EvidenceAgentRunner,
    EvidenceRegistryBuilder,
    resolve_evidence_agents,
)
from mcp_server.providers.custom.debug_artifacts import (
    debug_dir,
    debug_write,
    pipeline_debug_enabled,
)
from mcp_server.providers.custom.transient_artifacts import transient_dir, transient_write
from mcp_server.providers.custom.evidence_bundles import CATEGORY_PATTERNS, EvidenceBundleBuilder
from mcp_server.providers.custom.doc_slice_merge import (
    dedupe_document,
    duplicate_heading_errors,
    merge_sectioned_doc_outputs,
    merge_sliced_doc_outputs,
)
from mcp_server.providers.custom.fabrication_validators import (
    availability_claim_errors,
    component_reference_errors,
    extract_component_refs,
    job_scoped_component_reference_errors,
    protected_fact_errors,
    unit_count_claim_errors,
)
from mcp_server.providers.custom.runtime_evidence import (
    RUNTIME_CACHE_JSON,
    RUNTIME_COVERAGE_JSON,
    RUNTIME_PROFILE_MD,
    RUNTIME_REDACTION_FLAGS_JSON,
    RuntimeEvidenceStore,
    RuntimeMode,
    fetch_runtime_evidence,
    project_rollup,
)
from mcp_server.providers.custom.target_evidence import (
    TargetEvidence,
    TargetEvidenceBuilder,
    build_unit_index,
    filter_fact_table,
    load_jobs,
)
from mcp_server.providers.custom.noncode_evidence import (
    NonCodeAssociationPlan,
    NonCodeLimits,
    build_noncode_context,
)
from mcp_server.providers.custom.talend_intelligence import write_talend_intelligence
from mcp_server.providers.custom.agent_runtime import (
    AgentTaskContract,
    AgentTaskRuntime,
    ScopedEvidenceTools,
    default_input_budget,
    default_tool_budget,
)
from mcp_server.providers.custom.dag import (
    build_execution_graph,
    deterministic_execution_plan,
    validate_execution_plan,
)
from mcp_server.providers.custom.models import (
    CoverageReport,
    ExecutionPlan,
    HarnessRunState,
    InvestigationReport,
    ModelOptions,
    ModelResult,
    ProviderCallLog,
)
from mcp_server.providers.custom.doc_catalog import load_doc_catalog
from mcp_server.providers.custom.pack_contract import (
    SECTION_COVERAGE_FILENAME,
    PackContract,
    contract_from_plugin_manifest,
    default_contract,
)
from mcp_server.providers.custom.pipeline.warning_collector import WarningCode
from mcp_server.providers.custom.prompt_packs import (
    ANALYSIS_SECTION_NAMES,
    REQUIRED_SECTION_NAMES,
    materialize_prompt_packs,
)
from mcp_server.providers.custom.prompts import (
    doc_prompt,
    facts_summary,
    group_analysis_prompt,
)
from mcp_server.providers.custom.providers import ModelProvider, build_model_provider
from mcp_server.providers.custom.repair_planner import (
    REPAIR_TOKEN_CAP,
    deterministic_phase2_section_patch,
    estimate_repair_tokens,
)
from mcp_server.providers.custom.validators import (
    build_facts_manifest,
    group_analysis_is_complete,
    missing_required_analysis_sections,
    normalize_analysis_guide_headings,
    validate_analysis_guide_sections,
    validate_investigation_coverage,
    validate_analysis_depth,
    validate_analysis_coverage,
    validate_doc_depth,
    validate_docs,
    validate_final_redaction,
    validate_pack_quality,
    validate_source_provenance,
    validate_usefulness_rubric,
    build_documentation_obligation_map,
    scanned_config_files,
    covered_source_files,
    strip_config_contradiction_sentences,
    EVIDENCE_REGISTER_SECTION_RE,
)
from mcp_server.services.model_pricing import calculate_total_cost
from mcp_server.services.generation_run_state import record_manifest_chunks
from mcp_server.providers.custom.constants import (
    CROSS_JOB_FINDINGS_SECTION,
    GROUP_SUMMARY_SECTION,
    LEGACY_CROSS_CHUNK_SECTION,
)
from mcp_server.providers.custom.evidence_tasks import (
    EvidenceTask,
    count_evidence_tasks,
    job_section_texts,
    plan_evidence_tasks,
)
from mcp_server.providers.custom.phase2_context import build_phase2_context_packet
from mcp_server.providers.custom.phase3_context import build_fact_map_view
from mcp_server.providers.custom.phase3_slices import (
    Phase3PlanInputs,
    Phase3TaskPlan,
    plan_phase3_document,
    plan_phase3_slices,
)
from mcp_server.providers.custom.unit_naming import resolve_unit_display_names
from mcp_server.providers.custom.phase2_claims import (
    ClaimTaskSpec,
    Phase2ClaimBatch,
    apply_claim_floors,
    claim_task_prompt,
    parse_claim_batch,
    plan_claim_tasks,
    render_claims_as_markdown,
    salvage_claim_batch,
    split_claim_task,
    unknown_batch,
    validate_claim_batch,
    write_claim_ledger,
)


LARGE_CONTEXT_ARTIFACTS = {
    "engineering_facts.json",
    "config_runtime_map.json",
}

ADDITIONAL_FINDINGS_SECTION = "UNCLASSIFIED_OR_EXTRA_FINDINGS"
PHASE2_SECTION_NAMES = tuple(ANALYSIS_SECTION_NAMES) + (
    GROUP_SUMMARY_SECTION,
    CROSS_JOB_FINDINGS_SECTION,
    LEGACY_CROSS_CHUNK_SECTION,
    ADDITIONAL_FINDINGS_SECTION,
)
BASELINE_BUNDLE_SECTIONS = {GROUP_SUMMARY_SECTION}

# Maps evidence profile category strings -> Phase 2 section names.
# Used to enforce evidence_profiles from plugin.yaml in Phase 2.8 doc bundles.
_BUNDLE_CATEGORY_TO_PHASE2_SECTIONS: dict[str, set[str]] = {
    "environment_config": {"ENVIRONMENT_CONFIG"},
    "external_systems": {"EXTERNAL_SYSTEMS"},
    "business_rules": {"BUSINESS_RULES", "TRANSFORMATION_DETAIL"},
    "data_flows": {"DATA_FLOWS", "SCHEMA_DICTIONARY"},
    "components": {"COMPONENTS", "EXECUTION_FLOW"},
    "risk_flags": {"RISK_FLAGS"},
    "custom_code": {"CUSTOM_CODE"},
    "source_sql": {"SOURCE_SQL"},
    "scheduling": {"SCHEDULING"},
    "error_handling": {"ERROR_HANDLING"},
    "naming_patterns": {"NAMING_PATTERNS"},
    "cross_group_refs": {"CROSS_GROUP_REFS"},
}


def _guide_section_names(guide_path: Path) -> frozenset[str]:
    """Return all-caps section names from a plugin ANALYSIS_GUIDE not already in PHASE2_SECTION_NAMES.

    Any heading level counts: a guide author writing ``## INJECTION_SINKS`` declares
    the same section as ``### INJECTION_SINKS``, and the parser this feeds now
    recognises the name at any level too.  Name extraction is the permissive side of
    the pair (a missed name means an unrecognised section downstream), so an
    enumerated heading such as ``## 4. INJECTION_SINKS`` is accepted here.
    """
    from mcp_server.providers.custom.section_parser import allcaps_heading_name

    if not guide_path.exists():
        return frozenset()
    names: set[str] = set()
    for line in guide_path.read_text(encoding="utf-8", errors="replace").splitlines():
        candidate = allcaps_heading_name(line, allow_enumerated=True)
        if candidate and candidate not in PHASE2_SECTION_NAMES:
            names.add(candidate)
    return frozenset(names)


def _evidence_register_section(text: str) -> str | None:
    match = EVIDENCE_REGISTER_SECTION_RE.search(text)
    if not match:
        return None

    section = match.group(0).strip()
    lines = section.splitlines()
    table_start = next((index for index, line in enumerate(lines) if line.lstrip().startswith("|")), None)
    if table_start is None:
        return section

    table_lines: list[str] = []
    for line in lines[table_start:]:
        if not line.lstrip().startswith("|"):
            break
        table_lines.append(line.rstrip())
    if len(table_lines) < 2:
        return section
    return f"{lines[0].strip()}\n\n" + "\n".join(table_lines).rstrip()


def _parse_register_rows(text: str) -> list[list[str]]:
    """Return Evidence Register data rows as lists of 5 cell strings."""
    match = EVIDENCE_REGISTER_SECTION_RE.search(text)
    if not match:
        return []
    lines = [line for line in match.group("body").splitlines() if line.strip()]
    table_start = next((i for i, line in enumerate(lines) if line.lstrip().startswith("|")), None)
    if table_start is None or table_start + 2 >= len(lines):
        return []
    rows = []
    for line in lines[table_start + 2:]:
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 5:
            rows.append(cells)
    return rows


def _build_register_section(rows: list[list[str]]) -> str:
    """Build a ## Evidence Register markdown section from parsed row lists."""
    header = "| Topic | Units / Scope | Evidence Source | Source Artifact / Path | Confidence |"
    separator = "|---|---|---|---|---|"
    row_lines = [f"| {' | '.join(row)} |" for row in rows]
    return "## Evidence Register\n\n" + "\n".join([header, separator, *row_lines])


# Synthesis docs cite sibling generated docs in their Evidence Register, not raw scanner paths.
_SYNTHESIS_DOCS = frozenset({"09_EXECUTIVE_SUMMARY.md", "07_QUALITY_SECURITY_RISK_SCORECARD.md"})

_SQL_NOISE = frozenset({
    "SELECT", "FROM", "WHERE", "JOIN", "INNER", "OUTER", "LEFT", "RIGHT", "FULL",
    "CROSS", "ON", "AND", "OR", "NOT", "NULL", "TRUE", "FALSE", "INTO", "VALUES",
    "UPDATE", "DELETE", "INSERT", "CREATE", "ALTER", "DROP", "TABLE", "VIEW", "INDEX",
    "ORDER", "GROUP", "BY", "HAVING", "DISTINCT", "TOP", "WITH", "UNION", "ALL",
    "CASE", "WHEN", "THEN", "ELSE", "END", "AS", "SET", "EXEC", "CAST", "CONVERT",
    "NOLOCK", "WITH", "BEGIN", "COMMIT", "ROLLBACK", "DECLARE", "RETURN", "GOTO",
    "DATAAREAID", "PARTITION", "RECID",  # AX ubiquitous system columns — not distinctive
})

_IDENT_SQL_TABLE = re.compile(
    r'\b(?:FROM|JOIN|INTO|UPDATE)\s+(?:\[?\w+\]?\.)?\[?([A-Za-z_]\w{2,})\]?\b',
    re.IGNORECASE,
)
_IDENT_CONNECTION = re.compile(
    r'(?:RegisterConnection|OpenDbConnection|connectionStringName|connectionString\s+name)\s*[=(,"\']+\s*["\']?(\w{2,})',
    re.IGNORECASE,
)
_IDENT_URL = re.compile(r'(?:Route|route|path|endpoint|url)\s*[=("\']+\s*["\']?(/[\w/{}\[\]-]{3,})', re.IGNORECASE)
_IDENT_CAMEL = re.compile(r'\b([A-Z][a-z]{2,}(?:[A-Z][a-z]{2,})+)\b')
_IDENT_ALLCAPS = re.compile(r'\b([A-Z][A-Z0-9_]{3,})\b')
_IDENT_CONFIG_KEY = re.compile(r'(?:appSettings|key|setting)\s*[=\["\']+\s*["\']?([\w.:\-]{4,})["\']', re.IGNORECASE)


def _extract_chunk_identifiers(text: str, max_items: int = 25) -> list[str]:
    """Extract distinctive named identifiers from chunk text for manifest hints.

    Pulls SQL table names, connection names, URL paths, CamelCase class/method names,
    ALL_CAPS constants, and config keys. Works for any platform or language.
    Returns a deduplicated list ranked by specificity (longer = more specific).
    """
    seen: set[str] = set()

    def _add(val: str) -> None:
        v = val.strip().strip("[]\"'`")
        if len(v) >= 3 and v not in _SQL_NOISE and v.upper() not in _SQL_NOISE:
            seen.add(v)

    for m in _IDENT_SQL_TABLE.finditer(text):
        _add(m.group(1).upper())
    for m in _IDENT_CONNECTION.finditer(text):
        _add(m.group(1))
    for m in _IDENT_URL.finditer(text):
        _add(m.group(1))
    for m in _IDENT_CAMEL.finditer(text):
        if 6 <= len(m.group(1)) <= 50:
            _add(m.group(1))
    for m in _IDENT_ALLCAPS.finditer(text):
        if m.group(1) not in _SQL_NOISE and len(m.group(1)) >= 4:
            _add(m.group(1))
    for m in _IDENT_CONFIG_KEY.finditer(text):
        _add(m.group(1))

    # Rank: longer identifiers are more specific; deduplicate case-insensitively
    ranked = sorted(seen, key=len, reverse=True)
    deduped: list[str] = []
    lower_seen: set[str] = set()
    for item in ranked:
        low = item.lower()
        if low not in lower_seen:
            lower_seen.add(low)
            deduped.append(item)
        if len(deduped) >= max_items:
            break
    return deduped


def _empty_group_stub(group: str, units: list[str]) -> str:
    """Produce a minimal but structurally valid Phase 2 analysis for a group whose
    preprocess content is below the investigation threshold (data-only / empty source).

    The stub satisfies the coverage, depth, and guide-sections gates without any LLM call.
    """
    covered = "\n".join(
        f"- SKIPPED: {u} -- supplied preprocessing packet contains too little material for analysis"
        for u in units
    )
    unit_lines = "\n".join(
        f"- {u}: packet-limited; source extraction status is not established by this artifact" for u in units
    )
    return (
        f"# Phase 2 Analysis: {group}\n\n"
        "## Coverage\n\n"
        f"{covered}\n\n"
        "## Model Analysis\n\n"
        "### GROUP_SUMMARY\n\n"
        f"Evidence-packet gap for `{group}`. The supplied preprocessing artifact contains too "
        "little material for analysis. This does not establish that the source or scanner lacked it.\n\n"
        f"Units:\n{unit_lines}\n"
    )


def _partial_group_stub(group: str, units: list[str], reason: str) -> str:
    """Structurally valid PARTIAL Phase 2 analysis for a group whose analysis
    failed (budget exhaustion, provider failure, unexpected crash).

    Salvage-first (P0.2): the failed group is marked PARTIAL instead of
    aborting the run. The stub carries coverage markers with an explicit
    analysis-unavailable statement per unit (mirroring the claims path's
    unknown batches) but deliberately omits the required analysis sections so
    ``group_analysis_is_complete`` stays False and a resume run retries the
    group instead of reusing the stub.
    """
    covered = "\n".join(f"- COVERED: {u}" for u in units)
    unit_lines = "\n".join(
        f"- {u}: analysis unavailable in this run; retry via resume" for u in units
    )
    return (
        f"# Phase 2 Analysis: {group}\n\n"
        "## Coverage\n\n"
        f"{covered}\n\n"
        "## Model Analysis\n\n"
        f"PARTIAL: Phase 2 analysis for group `{group}` did not complete in this "
        f"run ({reason}). Deterministic scanner evidence remains the source of "
        "record; documents referencing this group carry an evidence notice.\n\n"
        f"Units:\n{unit_lines}\n"
    )


def _groups_from_errors(errors: list[str], known_groups: set[str]) -> set[str]:
    """Extract group names explicitly mentioned in validator error strings.

    Parses patterns like 'Phase 2 analysis for group demo-data missing...'
    Returns only names that are in the known_groups set.
    """
    found: set[str] = set()
    for err in errors:
        m = re.search(r"for group\s+[`']?(\S+?)[`']?[\s,.]", err)
        if m and m.group(1) in known_groups:
            found.add(m.group(1))
    return found


def _line_marks_table_ddl_only(line: str, table: str, short: str) -> bool:
    """True when a single table-specific row/line marks that table as DDL-only."""

    if not re.search(r"\b(?:DDL[_ -]?ONLY|PRESENT_IN_DDL_ONLY|DDL only)\b", line, re.IGNORECASE):
        return False
    # Avoid broad summary prose such as "All other DDL-only tables".
    if not line.lstrip().startswith("|") and len(line) > 300:
        return False

    cells = [cell.strip(" `") for cell in line.strip().strip("|").split("|")] if "|" in line else [line]
    table_names = {
        table.lower(),
        short.lower(),
        f"dbo.{short}".lower(),
    }
    for cell in cells[:3]:
        normalized = cell.strip("[]` ").lower()
        if normalized in table_names:
            return True
    return False


_SQL_DML_RE = re.compile(
    r"\bselect\b.{0,500}?\bfrom\b|insert\s+into\b|update\b.{0,200}?\bset\b|delete\s+from\b|merge\s+into\b",
    re.IGNORECASE | re.DOTALL,
)


def _fact_has_sql_content(item: dict[str, Any]) -> bool:
    """Return True only when a usage fact carries actual SQL DML, not just a 'query'-keyed config param."""
    if _SQL_DML_RE.search(str(item.get("name") or "")):
        return True
    for ev in item.get("evidence", []) or []:
        if isinstance(ev, dict) and _SQL_DML_RE.search(str(ev.get("excerpt") or "")):
            return True
    return False


def _sql_usage_tables(sql_usage_path: Path) -> list[str]:
    if not sql_usage_path.exists():
        return []
    try:
        payload = json.loads(sql_usage_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    tables = {
        str(table.get("table") or "")
        for record in payload.get("records", []) or []
        if isinstance(record, dict)
        for table in record.get("tables", []) or []
        if isinstance(table, dict) and table.get("table")
    }
    return sorted(tables, key=lambda value: (-len(value), value.lower()))


def _mark_line_sql_used(line: str) -> str:
    """Replace DDL-only classification tokens on a table row with SQL-used status."""

    replacements = [
        (r"\bPRESENT_IN_DDL_ONLY\b", "USED_BY_CURRENT_CODE"),
        (r"\bDDL[_ -]?ONLY\b", "USED_BY_CURRENT_CODE"),
        (r"\bDDL only\b", "SQL usage"),
        (r"\bNone \(USED_BY_CURRENT_CODE\)\b", "SQL usage"),
    ]
    updated = line
    for pattern, repl in replacements:
        updated = re.sub(pattern, repl, updated, flags=re.IGNORECASE)
    # Common generated wording in DDL scripts: "None (PRESENT_IN_DDL_ONLY)".
    updated = re.sub(r"\bNone\s*\(\s*USED_BY_CURRENT_CODE\s*\)", "SQL usage", updated, flags=re.IGNORECASE)
    return updated


class FatalProviderError(RuntimeError):
    """A provider failure that will repeat identically for every remaining call.

    Credential and billing failures (HTTP 401/402, invalid API key, insufficient
    balance) are *account state*, not document content. Salvage-first publishing
    is correct for a content failure on one document; applied to a credit
    failure it publishes the whole remaining document set as ~800-byte stubs and
    reports the run as successful. Verified locally: a mid-run DeepSeek
    ``402 Payment Required ... Insufficient Balance`` produced a "complete"
    10-document set totalling 8,095 bytes that shipped as real documentation.

    Raised instead of salvaging. The Phase 3 batch runner catches
    ``BaseException`` around each document, so it re-raises this type
    explicitly -- see ``_run_batches``.
    """


# Evidence patterns that mean "the account cannot make the next call either".
# Matched against the provider's ``raw_error`` text ONLY, never the full
# metadata dump: metadata carries token counts, conversation traces and tool
# ledgers, so a bare ``\b402\b`` over that blob would fatally abort a healthy
# run on an ``input_tokens: 402``.
#
# 403 / "forbidden" / "permission denied" are deliberately EXCLUDED. Those are
# also raised by scoped resource and tool denials, which are local to one call
# and must not kill a healthy run; they keep the old salvage-and-continue
# disposition.
_FATAL_PROVIDER_EVIDENCE = re.compile(
    r"\b(?:401|402)\b"
    r"|payment\s+required"
    r"|insufficient\s+(?:balance|funds|credits?|quota)"
    r"|(?:credit|account)\s+balance"
    r"|billing"
    r"|(?:invalid|incorrect|missing|no)[\s_-]+api[\s_-]?key"
    r"|invalid[\s_-]authentication"
    r"|auth(?:entication)?\s+failed"
    r"|\bunauthorized\b"
    r"|(?:api\s?key|token|credential)s?\s+(?:has\s+)?expired"
    r"|expired[^.\n]{0,24}(?:api\s?key|token|credential)",
    re.IGNORECASE,
)


class FailureKind(str, Enum):
    CONTEXT_TOO_LARGE = "context_too_large"
    EMPTY_OUTPUT = "empty_output"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    AUTH_ERROR = "auth_error"
    UNKNOWN = "unknown"
    BUDGET_EXCEEDED = "budget_exceeded"
    INCOMPLETE = "incomplete"  # provider cut off mid-generation (output max_tokens hit)

    @property
    def is_retryable_with_fallback(self) -> bool:
        return self not in {
            FailureKind.CONTEXT_TOO_LARGE,
            FailureKind.BUDGET_EXCEEDED,
            FailureKind.INCOMPLETE,
        }


@dataclass(frozen=True)
class ToolPolicy:
    """Explicit per-phase tool-use policy replacing the implicit use_tools_for_core_generation()."""

    enabled: bool
    max_input_tokens: int
    max_calls: int


@dataclass(frozen=True)
class _ModelBudgetReservation:
    """One atomic reservation for a physical provider request."""

    call_index: int
    input_tokens: int
    output_tokens: int


class _PipelineLog:
    """Accumulates per-phase telemetry during a generation run and writes _pipeline_log.md."""

    def __init__(self) -> None:
        self._entries: list[dict] = []

    def add(
        self,
        phase: str,
        state: "HarnessRunState",
        calls_before: int,
        validation: str,
        notes: str = "",
    ) -> None:
        new_calls = state.calls[calls_before:]
        self._entries.append({
            "phase": phase,
            "calls": len(new_calls),
            "input_tokens": sum(c.result.input_tokens for c in new_calls),
            "output_tokens": sum(c.result.output_tokens for c in new_calls),
            "latency_ms": sum(c.result.latency_ms for c in new_calls),
            "validation": validation,
            "notes": notes[:80],
        })

    def flush(self, staging_dir: "Path") -> None:
        from mcp_server.providers.custom.debug_artifacts import debug_dir, debug_write, pipeline_debug_enabled
        if not pipeline_debug_enabled():
            return
        rows = [
            "| Phase | Calls | Tokens In | Tokens Out | Duration (ms) | Validation | Notes |",
            "|-------|-------|-----------|-----------|--------------|-----------|-------|",
        ]
        for e in self._entries:
            rows.append(
                f"| {e['phase']} | {e['calls']} | {e['input_tokens']} | {e['output_tokens']} | "
                f"{e['latency_ms']} | {e['validation']} | {e['notes']} |"
            )
        if not self._entries:
            rows.append("| - | - | - | - | - | - | - |")
        debug_write(
            debug_dir(staging_dir, "_pipeline_log.md"),
            "# Pipeline Log\n\n" + "\n".join(rows) + "\n",
        )


def estimate_input_tokens(prompt: str, context_files: list[Path] | None = None) -> int:
    """Cheap, stable estimate used for routing before model calls."""

    total_chars = len(prompt)
    for path in context_files or []:
        try:
            total_chars += path.stat().st_size
        except OSError:
            continue
        total_chars += len(path.name) + 32
    return max(1, total_chars // 4)


def context_bytes(context_files: list[Path] | None = None) -> int:
    total = 0
    for path in context_files or []:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def _json_object(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    match = re.search(r"\{.*\}", stripped, re.DOTALL)
    if not match:
        raise ValueError("no JSON object found")
    return match.group(0)


def _provider_supports_tools(provider: ModelProvider) -> bool:
    return bool(getattr(provider, "supports_tools", False))


def _with_tool_instructions(prompt: str, staging_dir: Path) -> str:
    """Prepend tool-use instructions to any existing prompt."""
    preamble = (
        "You have scoped tools available to read context and evidence:\n"
        "  list_artifacts(subdir=None) — list files in staging"
        " (pass '_preprocess', '_analysis', or '_assessment' to scope)\n"
        "  read_artifact(path) — read a file by path relative to staging root\n"
        "  search_evidence(query, scope=None, categories=None, limit=20) — search scoped evidence\n"
        "  get_unit_evidence(unit_name, categories=None, limit=20) — fetch scoped unit evidence\n"
        "  get_group_evidence(group_name, categories=None, limit=40) — fetch scoped group evidence\n"
        "  get_fact_table(table_name, filters=None, limit=50) — read structured fact tables\n"
        "\n"
        "Start by calling list_artifacts('_preprocess') to see what is available.\n"
        "Read the files you need to complete your task.\n"
        "Do NOT expect any files to be pre-loaded — fetch everything you need via tools.\n"
        "When you have finished reading and have produced your complete response, stop.\n\n"
    )
    return preamble + prompt


def _with_agent_contract(prompt: str, contract: AgentTaskContract | None) -> str:
    if contract is None:
        return prompt
    parts = [prompt]
    if contract.starter_context_files:
        parts.append(
            "Starter file hints. Read only what you need with tools:\n"
            + "\n".join(f"- {path}" for path in contract.starter_context_files)
        )
    if contract.allowed_units:
        parts.append("Allowed canonical units:\n" + "\n".join(f"- {unit}" for unit in contract.allowed_units))
    if contract.allowed_groups:
        parts.append("Allowed groups:\n" + "\n".join(f"- {group}" for group in contract.allowed_groups))
    if contract.allowed_categories:
        parts.append("Allowed evidence categories:\n" + "\n".join(f"- {cat}" for cat in contract.allowed_categories))
    if contract.validation_gaps:
        parts.append(
            "Previous attempt validation gaps to repair directly:\n"
            + "\n".join(f"- {gap}" for gap in contract.validation_gaps)
        )
    parts.append(f"Expected artifact: {contract.expected_artifact}. Attempt {contract.attempt}/{contract.max_attempts}.")
    return "\n\n".join(parts)


class CustomHarnessProvider(DocGenerationProvider):
    """Provider-agnostic Python harness with hard factual gates."""

    def __init__(
        self,
        model_provider: ModelProvider,
        write_provider: ModelProvider | None = None,
        investigation_provider: ModelProvider | None = None,
        fallback_provider: ModelProvider | None = None,
        pack: "InstructionPack | None" = None,
        plugin_pack_dir: "Path | None" = None,
        pack_contract: "PackContract | None" = None,
    ) -> None:
        self.model_provider = model_provider
        self.write_provider = write_provider or model_provider
        self.investigation_provider = investigation_provider or model_provider
        self.fallback_provider = fallback_provider
        self.pack = pack
        self.plugin_pack_dir = plugin_pack_dir
        # Resolved section/doc contract (One Pack program, decision D1).
        # Injected directly for DB-stored packs, compiled from plugin.yaml
        # (v1 or v2) for directory packs, or defaulted per-platform in run().
        self._pack_contract: "PackContract | None" = pack_contract

        _catalog = load_doc_catalog()
        # Per-unit catalog entries are expanded at run start (one doc per
        # scanned unit); the base entry name is never itself a document.
        self._required_docs: list[str] = [
            d.name for d in _catalog.docs if not d.per_unit
        ]
        _per_unit_base = {d.name for d in _catalog.per_unit_docs}
        self._doc_assignments: dict[str, list[str]] = {
            bundle: [d for d in docs if d not in _per_unit_base]
            for bundle, docs in _catalog.doc_assignments.items()
        }
        self._doc_bundle_map: dict[str, str] = dict(_catalog.doc_bundle_map)
        self._doc_section_bundle_map: dict[str, set[str]] = dict(_catalog.doc_section_bundle_map)
        self._doc_section_requirements_extra: dict[str, list[str]] = {}
        self._plugin_manifest = None
        self._plugin_doc_definitions: dict[str, object] = {}
        self._per_job_doc_units: dict[str, str] = {}
        # Per-doc deterministic evidence bundles (built lazily per run) and the
        # builder instance caching the loaded scan for the current staging dir.
        self._target_evidence: dict[str, TargetEvidence] = {}
        self._target_evidence_builder: TargetEvidenceBuilder | None = None
        # Observed production behaviour, fetched once at the start of Phase 3
        # (the evidence builder is synchronous and must not do network I/O).
        # `_runtime_evidence_key` is the once-per-run guard and is deliberately
        # IN MEMORY, keyed on (run_id, staging_dir): staging survives a resume,
        # so an on-disk existence check would make every resumed run adopt the
        # failed run's snapshot and pin `mode: unavailable` forever (A10).
        self._runtime_evidence: "RuntimeEvidenceStore | None" = None
        self._runtime_evidence_key: tuple[int | None, str] | None = None
        # Tier-3 non-code documented-context plan (built once per staging dir).
        # ``False`` means "attempted, unavailable" so we do not retry per doc.
        self._noncode_plan: NonCodeAssociationPlan | None | bool = None
        self._noncode_plan_staging: Path | None = None
        self._pack_evidence_paths: dict[str, dict[str, str]] = {}
        self._evidence_registry_paths: dict[str, str] = {}
        self._phase_model_overrides: dict[str, str] = {}
        self._phase_concurrency_overrides: dict[str, int] = {}

        if plugin_pack_dir:
            try:
                # One pack format, one compiler: the manifest IS the execution
                # contract, and contract_from_plugin_manifest derives the
                # section/doc contract from its ``lens`` and ``docs`` blocks.
                _manifest = load_plugin_manifest(plugin_pack_dir)
                if self._pack_contract is None:
                    self._pack_contract = contract_from_plugin_manifest(_manifest)
                self._plugin_manifest = _manifest
                if _manifest.phases is not None:
                    for _phase_name in ("investigation", "analysis", "scorecard", "doc_bundles"):
                        _m = _manifest.phases.model_for(_phase_name)
                        _c = _manifest.phases.concurrency_for(_phase_name)
                        if _m:
                            self._phase_model_overrides[_phase_name] = _m
                        if _c is not None:
                            self._phase_concurrency_overrides[_phase_name] = _c
                if _manifest.docs:
                    self._required_docs = [d.name for d in _manifest.docs]
                    self._plugin_doc_definitions = {d.name: d for d in _manifest.docs}
                    self._doc_assignments = {"custom": list(self._required_docs)}
                    self._doc_bundle_map = {d: "custom" for d in self._required_docs}
                    # A pack that routes nothing gets no routing (the historical
                    # behaviour: doc bundles receive the GROUP_SUMMARY floor).
                    # Declaring docs[].phase2_sections populates the map so each
                    # document's bundle carries exactly the analysis sections it
                    # is meant to be written from.
                    self._doc_section_bundle_map = {}
                    if self._pack_contract is not None:
                        contract_routing = {
                            spec.name: set(spec.phase2_sections)
                            for spec in self._pack_contract.docs
                            if spec.phase2_sections
                        }
                        if contract_routing:
                            self._doc_section_bundle_map = contract_routing
                    self._doc_section_requirements_extra = {
                        d.name: d.required_sections for d in _manifest.docs if d.required_sections
                    }
            except Exception as exc:
                logger.warning(
                    "[custom] plugin manifest load failed, continuing with defaults: %s",
                    exc,
                    exc_info=True,
                )
                self._pack_contract = pack_contract

    @property
    def _plugin_mode(self) -> bool:
        return self.plugin_pack_dir is not None or (
            self._pack_contract is not None and self._pack_contract.is_pack
        )

    @property
    def pack_contract(self) -> "PackContract":
        """The resolved contract for this run (default per-platform when no pack).

        Before ``run()`` resolves the facts manifest the platform is unknown;
        the generic default is returned so contract-driven helpers called in
        isolation (tests, tooling) keep the legacy non-Talend floor. Run-path
        code that has the platform at hand uses :meth:`_contract_for` instead.
        """
        if self._pack_contract is not None:
            return self._pack_contract
        return default_contract("generic")

    def _contract_for(self, platform: str) -> "PackContract":
        """Contract for a known platform: the active pack's, else the default.

        Used by run-path helpers that carry ``state.facts_manifest.platform``
        so direct calls (tests, repair tooling) outside ``run()`` still get
        the exact legacy platform-conditional enforcement.
        """
        if self._pack_contract is not None:
            return self._pack_contract
        return default_contract(platform)

    def _phase_policy(self, phase: str) -> str:
        """Return the execution policy for a harness phase.

        Resolution order:
        1. Explicit phases block in the manifest.
        2. Backward-compat: if evidence_profiles are present → skip all phases.
        3. No pack or no manifest → run all phases (enterprise default),
           except the advisory LLM scorecard, which is config-gated
           (R2.2: default off; a deterministic omitted-scorecard marker is
           written instead).
        """
        if not self._plugin_mode or self._plugin_manifest is None:
            if phase == "scorecard" and not config.docgen_llm_scorecard_enabled:
                return "skip"
            return "run"
        declared = getattr(self._plugin_manifest, "phases", None)
        if declared is not None:
            return declared.policy_for(phase)
        # backward compat: evidence_profiles present implies pack-contract (skip all)
        if getattr(self._plugin_manifest, "evidence_profiles", None):
            return "skip"
        return "run"

    def _pack_investigation_categories(self) -> set[str]:
        """Return the set of evidence categories the pack cares about, or empty set if all."""
        if not self._plugin_mode or self._plugin_manifest is None:
            return set()
        profiles = getattr(self._plugin_manifest, "evidence_profiles", {}) or {}
        cats: set[str] = set()
        for profile in profiles.values():
            cats.update(getattr(profile, "include", []))
        return cats

    def _chunk_has_pack_relevant_content(self, chunk_text: str) -> bool:
        """Return True if chunk text contains keywords matching any pack evidence category.

        Pack runs screen against their declared evidence profile categories.
        Enterprise runs (no pack) screen against all specific categories, excluding
        'source_summary' which matches nearly any file and would defeat the purpose.
        Chunks with no category match (DTO stubs, namespace files, CSS/UI) are skipped,
        saving ~15k input tokens per irrelevant LLM call.
        """
        cats = self._pack_investigation_categories()
        if not cats:
            # Enterprise run: use all specific categories as the default filter.
            # 'source_summary' is excluded because it matches almost any code file.
            return any(
                pattern.search(chunk_text)
                for cat, pattern in CATEGORY_PATTERNS.items()
                if cat != "source_summary"
            )
        return any(
            pattern.search(chunk_text)
            for cat, pattern in CATEGORY_PATTERNS.items()
            if cat in cats
        )

    @property
    def _pack_contract_mode(self) -> bool:
        """True when both investigation and analysis are skipped (pack-contract path)."""
        return (
            self._plugin_mode
            and self._phase_policy("investigation") == "skip"
            and self._phase_policy("analysis") == "skip"
        )

    def _provider_for_phase(self, phase: str) -> "ModelProvider":
        """Return the ModelProvider for a given phase, applying pack model overrides."""
        override_model = self._phase_model_overrides.get(phase)
        if not override_model:
            if phase == "investigation":
                return self.investigation_provider
            if phase in ("doc_write", "phase3"):
                return self.write_provider
            return self.model_provider
        if not hasattr(self, "_override_providers"):
            self._override_providers: dict[str, "ModelProvider"] = {}
        if override_model not in self._override_providers:
            from mcp_server.model_strategies import (
                normalize_model_id,
                provider_connection_config,
            )
            normalized = normalize_model_id(override_model)
            connection = provider_connection_config(normalized)
            self._override_providers[override_model] = build_model_provider(
                normalized,
                api_key=connection["api_key"],
                base_url=connection["base_url"],
                ollama_base_url=config.ollama_base_url,
            )
        return self._override_providers[override_model]

    def _concurrency_for_phase(self, phase: str, default: int) -> int:
        """Return concurrency for a phase, applying pack overrides."""
        return self._phase_concurrency_overrides.get(phase, default)

    def _tool_policy(self, provider: ModelProvider, phase: str) -> ToolPolicy:
        """Return the tool-use policy for a given provider/phase combination.

        Repair phases always run with tools (reduced budget) regardless of the
        base phase — phase2.repair and phase3.repair both need evidence retrieval.
        Phase 2 and scorecard core generation run without tools (bounded context only).
        Pack mode disables tools for Phase 2/3 core generation to keep generation bounded.
        """
        if not _provider_supports_tools(provider):
            return ToolPolicy(enabled=False, max_input_tokens=0, max_calls=0)
        # Repair check must come before phase-prefix checks: "phase2.repair" would
        # otherwise be caught by startswith("phase2") and silently disable tools.
        if "repair" in phase:
            return ToolPolicy(
                enabled=True,
                max_input_tokens=config.agent_phase3_tool_input_token_cap // 2,
                max_calls=max(1, config.agent_max_tool_calls // 2),
            )
        if phase.startswith("phase1.investigate"):
            return ToolPolicy(
                enabled=True,
                max_input_tokens=config.agent_phase2_tool_input_token_cap // 2,
                max_calls=max(1, config.agent_max_tool_calls // 2),
            )
        if phase.startswith("phase2.deep_dive"):
            return ToolPolicy(
                enabled=True,
                max_input_tokens=config.agent_phase2_tool_input_token_cap,
                max_calls=config.agent_max_tool_calls,
            )
        if phase in {"phase3.consistency", "consistency", "critic"}:
            return ToolPolicy(
                enabled=True,
                max_input_tokens=config.agent_phase3_tool_input_token_cap,
                max_calls=config.agent_max_tool_calls,
            )
        if phase.startswith("phase1") or phase.startswith("phase2") or phase in {"scorecard", "phase2.6"}:
            return ToolPolicy(enabled=False, max_input_tokens=0, max_calls=0)
        # Business topology is PUSH-evidence, not agentic: the high-signal scanner
        # evidence is injected directly into the model context (see
        # services/business_topology/context.py). Pulling it via budget-limited
        # tools was the verified starvation that made the model fabricate an
        # evidence-less topology; keep tools off so the tool budget can never be
        # the blocker for this single JSON synthesis call.
        if phase == "business_topology":
            return ToolPolicy(enabled=False, max_input_tokens=0, max_calls=0)
        if self._plugin_mode and phase.startswith(("phase2", "phase3")):
            return ToolPolicy(enabled=False, max_input_tokens=0, max_calls=0)
        # First-pass Phase 3 runs single-shot by default: the targeted doc pack reaches
        # the model via context_files and is primary/sufficient, so token cost scales
        # with pack size instead of an accumulating tool conversation. Repair phases
        # ("repair" in phase) are handled above and keep tools regardless. Set
        # agent_phase3_first_pass_tools=True to restore the agentic loop for first-pass docs.
        if phase.startswith("phase3") and not config.agent_phase3_first_pass_tools:
            return ToolPolicy(enabled=False, max_input_tokens=0, max_calls=0)
        # Phase 3 (tools opted back in) and other tool-capable phases
        return ToolPolicy(
            enabled=True,
            max_input_tokens=config.agent_phase3_tool_input_token_cap,
            max_calls=config.agent_max_tool_calls,
        )

    async def _prepare_execution_plan(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        facts_path: Path,
        log_cb: LogCallback,
    ) -> ExecutionPlan:
        """Create and persist the validated DAG execution plan for this run."""

        plans_dir = ctx.staging_dir / "_plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        fallback = deterministic_execution_plan(
            state.facts_manifest,
            required_docs=self._required_docs,
            preprocess_groups=list(ctx.preprocess_groups),
            investigation_policy=self._phase_policy("investigation"),
            analysis_policy=self._phase_policy("analysis"),
            scorecard_policy=self._phase_policy("scorecard"),
            doc_bundles_policy=self._phase_policy("doc_bundles"),
            plugin_mode=self._plugin_mode,
            small_project_threshold=config.small_project_threshold,
        )
        plan = fallback

        if config.agent_planner_enabled and config.agent_planner_mode.lower() == "llm":
            prompt = self._planner_prompt(ctx, state, fallback)
            opts = ModelOptions(
                max_tokens=config.agent_planner_max_tokens,
                temperature=0.0,
                retries=1,
                timeout_s=180.0,
                json_mode=config.agent_planner_require_valid_json,
                provider_options=self._provider_options(self.investigation_provider),
            )
            result = await self.investigation_provider.complete(
                prompt,
                [facts_path],
                opts,
            )
            state.calls.append(ProviderCallLog(phase="planner", target="execution_plan", result=result))
            await self._log_call_metrics(ctx, "planner:execution_plan", result, log_cb)
            if result.success:
                try:
                    candidate = ExecutionPlan.model_validate_json(_json_object(result.text))
                    candidate.mode = "llm"
                    candidate.planner_enabled = True
                    candidate.dag_enabled = True
                    candidate.required_docs = candidate.required_docs or list(self._required_docs)
                    candidate.graph = build_execution_graph(candidate, state.facts_manifest)
                    errors = validate_execution_plan(
                        candidate,
                        state.facts_manifest,
                        allowed_docs=self._required_docs,
                        max_nodes=config.agent_dag_max_nodes,
                        max_depth=config.agent_dag_max_depth,
                    )
                    if errors:
                        candidate.fallback_reason = "; ".join(errors[:5])
                        plan = fallback.model_copy(update={"fallback_reason": candidate.fallback_reason})
                    else:
                        plan = candidate
                except Exception as exc:
                    plan = fallback.model_copy(update={"fallback_reason": f"planner output invalid: {exc}"})
            else:
                plan = fallback.model_copy(update={"fallback_reason": result.raw_error or "planner call failed"})

        plan.dag_enabled = True
        if not plan.graph.nodes:
            plan.graph = build_execution_graph(plan, state.facts_manifest)
        errors = validate_execution_plan(
            plan,
            state.facts_manifest,
            allowed_docs=self._required_docs,
            max_nodes=config.agent_dag_max_nodes,
            max_depth=config.agent_dag_max_depth,
        )
        if errors and config.agent_dag_strict_validation:
            plan = fallback.model_copy(update={"fallback_reason": "; ".join(errors[:5])})
        (plans_dir / "execution_plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        (plans_dir / "execution_graph.json").write_text(plan.graph.model_dump_json(indent=2), encoding="utf-8")
        (plans_dir / "node_results.json").write_text("[]\n", encoding="utf-8")
        (plans_dir / "graph_events.jsonl").write_text("", encoding="utf-8")
        await log_cb(
            ctx.project_id,
            "[custom] DAG execution plan prepared: "
            f"mode={plan.mode}, analysis={plan.analysis_mode}, investigation={plan.investigation_mode}"
            + (f", fallback={plan.fallback_reason}" if plan.fallback_reason else ""),
            "info",
        )
        return plan

    def _planner_prompt(self, ctx: DocGenContext, state: HarnessRunState, fallback: ExecutionPlan) -> str:
        groups = "\n".join(
            f"- {group}: {count} units"
            for group, count in sorted(state.facts_manifest.groups.items())
        )
        docs = "\n".join(f"- {doc}" for doc in self._required_docs)
        fallback_json = fallback.model_dump_json(indent=2)
        return f"""\
You are planning a documentation-generation DAG for a bounded enterprise harness.
Return only JSON matching the ExecutionPlan shape implied by the fallback example.

Hard constraints:
- Use only the declared docs listed below.
- Do not omit validation_gate nodes from the graph.
- Choose deep analysis only for groups with material complexity or risk.
- Keep final validators and publish gates enabled.
- Python will reject unsafe or invalid plans.

Declared docs:
{docs}

Groups:
{groups or "- none"}

Project facts:
- Project: {state.facts_manifest.project_id}
- Platform: {state.facts_manifest.platform}
- Units: {state.facts_manifest.unit_count} {state.facts_manifest.canonical_unit_label}
- Plugin mode: {self._plugin_mode}
- Enterprise mode: {ctx.enterprise_mode}

Fallback plan example:
{fallback_json}
"""

    @staticmethod
    def _record_dag_node_result(
        ctx: DocGenContext,
        node_id: str,
        status: str,
        *,
        artifacts: list[str] | None = None,
        validation: str = "",
        error: str | None = None,
    ) -> None:
        path = ctx.staging_dir / "_plans" / "node_results.json"
        try:
            existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        except json.JSONDecodeError:
            existing = []
        existing.append({
            "node_id": node_id,
            "status": status,
            "artifacts": artifacts or [],
            "validation": validation,
            "error": error,
            "updated_at": time.time(),
        })
        path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        events = ctx.staging_dir / "_plans" / "graph_events.jsonl"
        with events.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(existing[-1]) + "\n")

    def _write_phase2_coverage_record(
        self,
        ctx: DocGenContext,
        group: str,
        units: list[str],
        analysis_path: Path,
        *,
        status: str = "completed",
    ) -> None:
        coverage_dir = ctx.staging_dir / "_analysis" / "_coverage_units"
        coverage_dir.mkdir(parents=True, exist_ok=True)
        try:
            rel_analysis = analysis_path.relative_to(ctx.staging_dir / "_analysis").as_posix()
        except ValueError:
            rel_analysis = analysis_path.name
        record = {
            "group": group,
            "expected_units": list(units),
            "analysis_file": rel_analysis,
            "status": status,
            "analysis_bytes": analysis_path.stat().st_size if analysis_path.exists() else 0,
        }
        (coverage_dir / f"{group}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")

    @staticmethod
    def _describe_provider(provider: ModelProvider) -> str:
        provider_name = getattr(provider, "provider_name", provider.__class__.__name__.lower())
        base_url = getattr(provider, "base_url", None)
        if base_url:
            return f"{provider_name}/{provider.model_name} @ {base_url}"
        return f"{provider_name}/{provider.model_name}"

    @staticmethod
    def _classify_failure(result: ModelResult) -> FailureKind | None:
        """Normalize provider-specific failures into harness retry decisions."""

        # Provider transport success is not content completeness.  Several
        # OpenAI-compatible APIs return non-empty text with ``success=true`` and
        # ``finish_reason=length``; accepting that before inspecting metadata
        # allowed truncated 32K outputs into deterministic assembly.
        finish_reason = (result.metadata or {}).get("finish_reason", "")
        incomplete_reason = (result.metadata or {}).get("incomplete_reason", "")
        if finish_reason in {"length", "max_tokens"} or incomplete_reason == "length":
            return FailureKind.INCOMPLETE

        if result.success and result.text.strip():
            return None

        raw_error = result.raw_error or ""
        metadata_text = json.dumps(result.metadata, sort_keys=True, default=str) if result.metadata else ""
        evidence = f"{raw_error}\n{metadata_text}".lower()

        def has_status(*codes: int) -> bool:
            return any(re.search(rf"\b{code}\b", evidence) for code in codes)

        # 402 belongs here with 401/403: a payment-required response is a
        # credential/billing failure, not a content failure. Before this it was
        # reachable only through the "payment required|billing" phrase, which a
        # provider returning `402 Payment Required :: {"message":"Insufficient
        # Balance"}` does not necessarily satisfy in the phrasing used.
        if has_status(401, 402, 403) or re.search(
            r"\b(unauthorized|forbidden|invalid api key|auth(?:entication)? failed|permission denied|"
            r"insufficient permissions|payment required|insufficient balance|insufficient funds|"
            r"insufficient credits?|billing)\b",
            evidence,
        ):
            return FailureKind.AUTH_ERROR

        if has_status(429) or re.search(
            r"\b(rate[_ -]?limit|too many requests|throttle|throttled|quota|capacity|"
            r"requests per minute|tokens per minute)\b",
            evidence,
        ):
            return FailureKind.RATE_LIMIT

        if has_status(413) or re.search(
            r"(context[_ -]?length[_ -]?exceeded|context length|maximum context|max context|"
            r"input (?:is )?too long|prompt too long|too many tokens|token limit|"
            r"maximum request length|request too large|payload too large|max prompt|"
            r"exceeds[^.\n]*(?:context|token|model))",
            evidence,
        ):
            return FailureKind.CONTEXT_TOO_LARGE

        if has_status(419):
            return FailureKind.UNKNOWN

        if has_status(408, 502, 503, 504) or re.search(
            r"\b(timeout|connection reset|connection refused|dns|socket|gateway timeout|"
            r"service unavailable|bad gateway|upstream error)\b",
            evidence,
        ):
            return FailureKind.NETWORK

        if result.success and (not result.text.strip() or result.output_tokens == 0):
            return FailureKind.EMPTY_OUTPUT

        # Detect output truncation: check metadata first, then fall through to text heuristics.
        # Metadata is populated by all provider implementations; text heuristics catch edge cases.
        if (
            re.search(
                r"incomplete:.*max.{0,10}tokens|max.{0,10}tokens.{0,20}reached",
                evidence,
                re.IGNORECASE,
            )
        ):
            return FailureKind.INCOMPLETE

        return FailureKind.UNKNOWN

    @classmethod
    def _is_fatal_provider_failure(cls, result: ModelResult) -> bool:
        """True when this failure will repeat identically for every later call.

        Narrower than ``FailureKind.AUTH_ERROR`` on purpose, in two ways:

        * only ``raw_error`` (the provider's own error text) is inspected --
          ``_classify_failure`` searches the JSON-dumped metadata too, which
          holds token counts and conversation traces where a bare status-code
          word-match is a coin flip. Wrong there costs a retry strategy; wrong
          here kills the run;
        * a bare 403 / "forbidden" / "permission denied" is NOT fatal. That
          shape is also produced by scoped resource and tool denials, which are
          local to one call. Those keep the salvage-and-continue disposition.
        """
        if result.success:
            return False
        if cls._classify_failure(result) is not FailureKind.AUTH_ERROR:
            return False
        return bool(_FATAL_PROVIDER_EVIDENCE.search(result.raw_error or ""))

    @staticmethod
    def _is_fatal_provider_error_text(text: str) -> bool:
        """``_is_fatal_provider_failure`` for failures that arrive as exceptions.

        Phase 2 group failures reach containment as ``BaseException`` objects
        whose message embeds the provider error, so the same credit/credential
        abort has to be reachable from a string.
        """
        return bool(_FATAL_PROVIDER_EVIDENCE.search(text or ""))

    @staticmethod
    def _failure_result(provider: ModelProvider, phase: str, target: str, message: str, kind: FailureKind) -> ModelResult:
        return ModelResult(
            success=False,
            model_name=getattr(provider, "model_name", ""),
            raw_error=message,
            metadata={"phase": phase, "target": target, "failure_kind": kind.value},
        )

    def _run_model_call_budget(self, state: HarnessRunState) -> int:
        # R2.1: when a work plan exists, the enforced ceiling is the configured
        # hard cap -- the plan sum is a reservation guide, never the ceiling.
        # Verified failure (Assetcheck, 73 units / 17 groups): the run enforced
        # the plan sum (179) and blocked Phase 3 at 180/179 while the true cap
        # (250) had ~71 calls unused; real consumption was ~197 < 250, so docs
        # 04-09 + topology shipped as stubs for no reason. Late-phase
        # starvation is prevented by the phase floors in _reserve_model_budget,
        # not by capping at the plan.
        if state.planned_call_budget is not None:
            return config.docgen_max_run_calls
        group_count = max(1, len(state.facts_manifest.groups))
        required_doc_count = max(1, len(self._required_docs))
        allowed_slices = max(1, config.docgen_allowed_slices_per_doc)
        dynamic_budget = (
            config.docgen_call_budget_base
            + group_count * config.docgen_call_budget_per_group
            + required_doc_count * allowed_slices
        )
        return min(
            config.docgen_max_run_calls,
            max(config.docgen_min_run_call_budget, dynamic_budget),
        )

    def _plan_call_budget(self, ctx: DocGenContext, state: HarnessRunState) -> dict[str, int]:
        """Size the run call plan from the deterministic work plan (P0.1/R2.1).

        Counts, before the first LLM call:
        - evidence-agent calls (one per resolved agent; 0 on standard runs
          unless docgen_default_evidence_agents_enabled);
        - Phase 1 investigation tasks (job-boundary bin packing; ~2 calls per
          group when the agentic chunk-manifest investigator is enabled);
        - Phase 2 claim tasks (bin packing with the per-task unit cap) plus one
          synthesis-headroom call per multi-task group, plus a repair-retry
          allowance (docgen_phase2_retry_allowance x tasks; verified ~29%
          retries pre-demotion);
        - Phase 2.6 scorecard: 0 when skipped; otherwise slices + reducer.
          Slices page over the per-group analysis outputs, ~3 groups per 4
          slices on the verified Assetcheck run (17 groups -> 12 slices + 1
          reducer), so the estimate is 1 + ceil(0.75 x groups) -- not a flat
          allowed_slices;
        - Phase 3: allowed_slices per group-level doc, 2 calls per per-unit doc
          (one write + one bounded repair);
        - a small constant for Phase 3.7 business topology.

        R2.1: ``planned`` is a guide; the ENFORCED ceiling is
        ``docgen_max_run_calls``. Phase 3 and topology get guaranteed floors
        (their planned call counts) recorded in ``state.planned_phase_floors``
        so earlier-phase overruns can never starve document writing.
        """
        preprocess_dir = ctx.staging_dir / "_preprocess"
        allowed_slices = max(1, config.docgen_allowed_slices_per_doc)
        evidence_agent_calls = len(
            resolve_evidence_agents(self._plugin_manifest, self.plugin_pack_dir).agents
        )
        phase1_tasks = 0
        phase2_tasks = 0
        phase2_synthesis = 0
        investigation_runs = self._phase_policy("investigation") != "skip" and (
            state.facts_manifest.unit_count > config.small_project_threshold
        )
        for group in ctx.preprocess_groups:
            group_file = preprocess_dir / f"{group}.md"
            group_units = self._group_units(state, group)
            if investigation_runs:
                if config.agent_phase1_agentic:
                    # Chunk-manifest investigator: ~2 calls per group
                    # (manifest planning + extraction).
                    phase1_tasks += 2
                else:
                    phase1_tasks += count_evidence_tasks(
                        group_file, group_units, config.phase2_task_evidence_bytes
                    )
            group_tasks = count_evidence_tasks(
                group_file,
                group_units,
                config.phase2_task_evidence_bytes,
                max_units_per_task=max(1, config.phase2_claim_units_per_task),
            )
            phase2_tasks += group_tasks
            if group_tasks > 1:
                phase2_synthesis += 1
        phase2_retries = math.ceil(
            phase2_tasks * max(0.0, config.docgen_phase2_retry_allowance)
        )
        unit_doc_count = len(self._per_job_doc_units)
        group_doc_count = max(0, len(self._required_docs) - unit_doc_count)
        phase3_calls = group_doc_count * allowed_slices + unit_doc_count * 2
        group_count = max(1, len(ctx.preprocess_groups))
        scorecard_calls = (
            1 + math.ceil(group_count * 3 / 4)
            if self._phase_policy("scorecard") != "skip"
            else 0
        )
        topology_calls = 2
        planned = (
            config.docgen_call_budget_base
            + evidence_agent_calls
            + phase1_tasks
            + phase2_tasks
            + phase2_retries
            + phase2_synthesis
            + scorecard_calls
            + phase3_calls
            + topology_calls
        )
        ceiling = config.docgen_max_run_calls
        state.planned_call_budget = max(config.docgen_min_run_call_budget, planned)
        state.planned_phase1_tasks = phase1_tasks
        state.planned_phase2_tasks = phase2_tasks
        # Guaranteed late-phase floors: never larger than what would leave the
        # earlier phases nothing (floors are clamped to the ceiling).
        state.planned_phase_floors = {
            "phase3": min(phase3_calls, ceiling),
            "business_topology": min(topology_calls, max(0, ceiling - phase3_calls)),
        }
        return {
            "evidence_agent_calls": evidence_agent_calls,
            "phase1_tasks": phase1_tasks,
            "phase2_tasks": phase2_tasks,
            "phase2_retries": phase2_retries,
            "phase2_synthesis": phase2_synthesis,
            "scorecard_calls": scorecard_calls,
            "phase3_calls": phase3_calls,
            "topology_calls": topology_calls,
            "planned": planned,
            "budget": state.planned_call_budget,
            "ceiling": ceiling,
            "phase3_floor": state.planned_phase_floors["phase3"],
            "topology_floor": state.planned_phase_floors["business_topology"],
        }

    def _doc_output_ceiling(self, doc_name: str, state: HarnessRunState) -> int:
        """Per-doc-type output token ceiling (P0.3 output governance).

        The built-in unit-reference dossier is intentionally small; a
        brief/summary is bounded; an inventory scales with unit count. Plugin
        ``per_unit`` documents are authored contracts, so multiplicity never
        implies a smaller output ceiling. Everything else keeps the phase-wide
        ceiling. Reservations use this per doc-task so one document can never
        starve the run's output budget.
        """
        # ``per_unit`` controls multiplicity, not document size.  The 6K
        # ceiling is appropriate for Nexus's built-in 10_UNIT_REFERENCE
        # dossier, but applying it to arbitrary plugin documents truncated
        # substantial contracts such as END_TO_END_LINEAGE before their final
        # required sections.  Plugin documents retain the normal Phase 3
        # safety ceiling and are governed by their manifest contract instead.
        if doc_name in self._per_job_doc_units and self._plugin_manifest is not None:
            return config.phase3_max_tokens
        if doc_name in self._per_job_doc_units:
            return max(1_200, config.docgen_unit_doc_output_tokens)
        base = doc_name.split("__", 1)[0]
        if base in {"00_AGENT_BRIEF", "09_EXECUTIVE_SUMMARY"}:
            return min(config.phase3_max_tokens, max(2_000, config.docgen_brief_output_tokens))
        if base == "02_COMPONENT_INVENTORY":
            scaled = (
                config.docgen_inventory_output_tokens_base
                + state.facts_manifest.unit_count
                * config.docgen_inventory_output_tokens_per_unit
            )
            return min(config.phase3_max_tokens, max(2_000, scaled))
        return config.phase3_max_tokens

    def _doc_expected_output_tokens(self, doc_name: str, state: HarnessRunState) -> int:
        """Expected OUTPUT size of one document (slicing-redesign driver).

        The per-doc-type ceiling (`_doc_output_ceiling`) is a safety cap, not
        an estimate: fixed-shape docs land around 7-16K deduplicated tokens
        regardless of evidence volume (verified across the werkorder run),
        while inventories and rule catalogs scale with rows-to-emit (unit
        count). The planner writes a doc in ONE call when this estimate fits
        `phase3_single_call_output_tokens`, and partitions the task by
        section/unit-range only when it does not.
        """
        if doc_name in self._per_job_doc_units and self._plugin_manifest is not None:
            base = doc_name.split("__", 1)[0]
            required_sections = self._plugin_required_sections(base)
            # Size the planning estimate from the authored document contract.
            # The provider still receives the Phase 3 safety ceiling above;
            # this estimate only decides whether the output planner should
            # treat the document as a normal substantial document.
            return min(
                config.phase3_max_tokens,
                max(
                    config.docgen_doc_expected_output_tokens,
                    2_000 + 2_000 * len(required_sections),
                ),
            )
        if doc_name in self._per_job_doc_units:
            return max(1_200, config.docgen_unit_doc_output_tokens)
        base = doc_name.split("__", 1)[0]
        if base in {"00_AGENT_BRIEF", "09_EXECUTIVE_SUMMARY"}:
            return max(2_000, config.docgen_brief_output_tokens)
        if base == "02_COMPONENT_INVENTORY":
            return max(
                2_000,
                config.docgen_inventory_output_tokens_base
                + state.facts_manifest.unit_count
                * config.docgen_inventory_output_tokens_per_unit,
            )
        if base == "04_BUSINESS_RULES_AND_TRANSFORMATIONS":
            return max(
                2_000,
                config.docgen_rules_output_tokens_base
                + state.facts_manifest.unit_count
                * config.docgen_rules_output_tokens_per_unit,
            )
        return max(2_000, config.docgen_doc_expected_output_tokens)

    def _phase3_plan_inputs(
        self, state: HarnessRunState, doc_name: str
    ) -> Phase3PlanInputs:
        """Doc-level facts for the output-sized Phase 3 planner."""
        required = (
            (self._doc_section_requirements_extra or {}).get(doc_name)
            or load_doc_catalog().section_requirements.get(doc_name.split("__", 1)[0], [])
        )
        unit_scoped: list[str] = []
        if doc_name not in self._per_job_doc_units:
            unit_scoped = load_doc_catalog().unit_scoped_sections_map.get(
                doc_name.split("__", 1)[0], []
            )
        return Phase3PlanInputs(
            doc_name=doc_name,
            expected_output_tokens=self._doc_expected_output_tokens(doc_name, state),
            single_call_output_tokens=min(
                config.phase3_max_tokens,
                max(2_000, config.phase3_single_call_output_tokens),
            ),
            required_sections=tuple(required),
            unit_scoped_sections=tuple(unit_scoped),
            units=tuple(
                (unit.name, unit.group) for unit in state.facts_manifest.units
            ),
        )

    def _run_input_token_budget(self, state: HarnessRunState) -> int:
        if config.docgen_max_run_input_tokens <= 0:
            return 0
        unit_count = max(state.facts_manifest.unit_count, len(state.facts_manifest.units))
        dynamic_budget = (
            config.docgen_input_token_budget_base
            + unit_count * config.docgen_input_token_budget_per_unit
            + len(self._required_docs) * config.docgen_input_token_budget_per_doc
        )
        return min(config.docgen_max_run_input_tokens, dynamic_budget)

    def _run_output_token_budget(self, state: HarnessRunState) -> int:
        # P0.1: sized per doc-task (sum of per-doc output ceilings), not per
        # unit. When the work plan is known, Phase 1/2 output allowances come
        # from the planned task counts times their per-call ceilings; the base
        # term keeps headroom for the scorecard, synthesis, and repairs. Each
        # Phase 3 doc contributes twice its own ceiling (slices/repairs reserve
        # per call and settle to actual usage). The configured cap stays a
        # runaway ceiling only, not a work bound.
        if state.planned_phase2_tasks is not None:
            phase12_allowance = (
                (state.planned_phase1_tasks or 0) * config.investigation_max_tokens
                + state.planned_phase2_tasks * config.phase2_claim_max_tokens
            )
            dynamic_budget = (
                config.docgen_output_token_budget_base
                + phase12_allowance
                + sum(
                    self._doc_output_ceiling(doc_name, state) * 2
                    for doc_name in self._required_docs
                )
            )
            return min(config.docgen_max_run_output_tokens, dynamic_budget)
        unit_count = max(state.facts_manifest.unit_count, len(state.facts_manifest.units))
        dynamic_budget = (
            config.docgen_output_token_budget_base
            + unit_count * config.docgen_output_token_budget_per_unit
            + len(self._required_docs) * config.docgen_output_token_budget_per_doc
        )
        return min(config.docgen_max_run_output_tokens, dynamic_budget)

    @staticmethod
    def _budget_class(phase: str) -> str:
        """Map a model-call phase string to its budget class.

        ``phase3`` covers doc writing, its repairs, and phase3.5 critics;
        ``business_topology`` covers Phase 3.7. Everything else shares the
        early-phase pool and may not consume another class's reserved floor.
        """
        if phase.startswith("phase3"):
            return "phase3"
        if phase == "business_topology":
            return "business_topology"
        return "shared"

    async def _reserve_model_budget(
        self,
        state: HarnessRunState,
        *,
        estimated_input_tokens: int,
        max_output_tokens: int,
        phase: str = "shared",
    ) -> tuple[_ModelBudgetReservation | None, str | None]:
        """Atomically reserve one request against run-level cost ceilings.

        R2.1 floor protection: a call is blocked only when it would exceed the
        hard ceiling MINUS the still-unused floors reserved for OTHER budget
        classes. Phase 3 doc calls therefore cannot be starved by Phase 1/2/
        scorecard overruns, and topology's small floor survives Phase 3.
        """

        input_reserve = max(
            1,
            math.ceil(
                estimated_input_tokens
                * max(1.0, config.docgen_token_estimate_safety_factor)
            ),
        )
        output_reserve = max(1, max_output_tokens)
        call_budget = self._run_model_call_budget(state)
        input_budget = self._run_input_token_budget(state)
        output_budget = self._run_output_token_budget(state)
        budget_class = self._budget_class(phase)
        async with state.budget_lock:
            consumed_calls = max(state.budget_consumed_calls, len(state.calls))
            consumed_input = max(state.budget_consumed_input_tokens, state.input_tokens)
            consumed_output = max(state.budget_consumed_output_tokens, state.output_tokens)
            call_index = consumed_calls + state.pending_model_calls + 1
            projected_input = consumed_input + state.pending_input_tokens + input_reserve
            projected_output = consumed_output + state.pending_output_tokens + output_reserve
            reserved_floor_for_others = sum(
                max(0, floor - state.floor_class_calls.get(cls, 0))
                for cls, floor in state.planned_phase_floors.items()
                if cls != budget_class
            )
            effective_budget = call_budget - reserved_floor_for_others
            if call_index > effective_budget:
                if reserved_floor_for_others > 0:
                    return None, (
                        f"model call budget would be exceeded: {call_index}/{effective_budget} "
                        f"({call_budget} ceiling minus {reserved_floor_for_others} calls "
                        "reserved for pending later phases)"
                    )
                return None, f"model call budget would be exceeded: {call_index}/{call_budget}"
            if input_budget > 0 and projected_input > input_budget:
                return None, (
                    "input token budget would be exceeded: "
                    f"{projected_input}/{input_budget} estimated tokens"
                )
            if projected_output > output_budget:
                return None, (
                    "output token budget would be exceeded: "
                    f"{projected_output}/{output_budget} reserved tokens"
                )
            state.pending_model_calls += 1
            state.pending_input_tokens += input_reserve
            state.pending_output_tokens += output_reserve
            # Every reservation is settled as exactly one consumed call
            # (settle runs in a finally), so counting at reserve time is an
            # accurate per-class call ledger for floor accounting.
            state.floor_class_calls[budget_class] = (
                state.floor_class_calls.get(budget_class, 0) + 1
            )
            return (
                _ModelBudgetReservation(
                    call_index=call_index,
                    input_tokens=input_reserve,
                    output_tokens=output_reserve,
                ),
                None,
            )

    @staticmethod
    async def _settle_model_budget(
        state: HarnessRunState,
        reservation: _ModelBudgetReservation,
        result: ModelResult | None,
    ) -> None:
        """Release a reservation and commit provider-reported usage atomically."""

        async with state.budget_lock:
            state.pending_model_calls = max(0, state.pending_model_calls - 1)
            state.pending_input_tokens = max(
                0, state.pending_input_tokens - reservation.input_tokens
            )
            state.pending_output_tokens = max(
                0, state.pending_output_tokens - reservation.output_tokens
            )
            state.budget_consumed_calls += 1
            if result is not None:
                state.budget_consumed_input_tokens += max(0, result.input_tokens)
                state.budget_consumed_output_tokens += max(0, result.output_tokens)

    def _doc_budget_key(self, target: str) -> str:
        for doc_name in self._required_docs:
            if target == doc_name or target.startswith(f"{doc_name}_"):
                return doc_name
        return target.split("_slice_", 1)[0].split("_retry_", 1)[0]

    async def _record_failure_policy(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        *,
        target: str,
        result: ModelResult,
        kind: FailureKind,
        log_cb: LogCallback,
    ) -> ModelResult:
        result.metadata["failure_kind"] = kind.value

        if kind in {FailureKind.EMPTY_OUTPUT, FailureKind.RATE_LIMIT, FailureKind.NETWORK} and config.docgen_adaptive_concurrency:
            state.adaptive_serial_generation = True

        if kind != FailureKind.EMPTY_OUTPUT:
            return result

        key = self._doc_budget_key(target)
        state.empty_output_counts[key] = state.empty_output_counts.get(key, 0) + 1
        state.total_empty_outputs += 1
        await log_cb(
            ctx.project_id,
            "[custom] empty output guard: "
            f"target={target} doc={key} doc_empty_outputs={state.empty_output_counts[key]}/"
            f"{config.docgen_empty_outputs_per_doc_limit} run_empty_outputs={state.total_empty_outputs}/"
            f"{config.docgen_empty_outputs_per_run_limit}",
            "warning",
        )

        if state.total_empty_outputs >= config.docgen_empty_outputs_per_run_limit:
            return result.model_copy(
                update={
                    "success": False,
                    "raw_error": (
                        "empty output run limit exceeded: "
                        f"{state.total_empty_outputs}/{config.docgen_empty_outputs_per_run_limit}; "
                        f"last target={target}"
                    ),
                }
            )

        if state.empty_output_counts[key] >= config.docgen_empty_outputs_per_doc_limit:
            return result.model_copy(
                update={
                    "success": False,
                    "raw_error": (
                        "empty output doc limit exceeded: "
                        f"{key} {state.empty_output_counts[key]}/"
                        f"{config.docgen_empty_outputs_per_doc_limit}"
                    ),
                }
            )

        return result

    async def run(self, ctx: DocGenContext, log_cb: LogCallback) -> AgentResult:
        from mcp_server.providers.custom.pipeline.helpers import HarnessHelpersImpl
        from mcp_server.providers.custom.pipeline.runner import PipelineRunner
        from mcp_server.providers.custom.pipeline.phases.phase1_investigation import Phase1Investigation
        from mcp_server.providers.custom.pipeline.phases.phase2_analysis import Phase2Analysis
        from mcp_server.providers.custom.pipeline.phases.phase26_scorecard import Phase26Scorecard
        from mcp_server.providers.custom.pipeline.phases.phase28_bundles import Phase28Bundles
        from mcp_server.providers.custom.pipeline.phases.phase3_docs import Phase3Docs
        from mcp_server.providers.custom.pipeline.phases.phase37_business_topology import (
            Phase37BusinessTopology,
        )
        from mcp_server.providers.custom.pipeline.phases.phase4_cleanup import Phase4Cleanup
        from mcp_server.providers.custom.pipeline.evidence_quality_gate import EvidenceQualityGate

        start_ms = int(time.time() * 1000)
        _pipeline_log = _PipelineLog()
        _phase_calls_start = 0

        # 09_EXECUTIVE_SUMMARY is enterprise-only — remove it for non-enterprise or pack runs.
        if not ctx.enterprise_mode or self._plugin_mode:
            self._required_docs = [d for d in self._required_docs if d != "09_EXECUTIVE_SUMMARY"]

        scan_output_dir = config.intodoc_output_dir / ctx.project_id
        try:
            facts = build_facts_manifest(ctx.project_id, scan_output_dir)
        except Exception as exc:
            logger.exception("[custom] build_facts_manifest failed for %s", ctx.project_id)
            raise RuntimeError(
                f"Scan output is missing or corrupt for project {ctx.project_id!r}. "
                "Re-run the scan and try again."
            ) from exc

        # Composite sub-project scoping: when this run is bound to one partition,
        # filter the facts manifest to that sub-project's units/groups before any
        # phase, coverage check, or call-budget computation runs. Downstream code
        # keys everything off ``state.facts_manifest`` so this single filter
        # scopes the entire pipeline. Non-partitioned runs are unaffected.
        if ctx.partition is not None:
            from mcp_server.providers.custom.partition_planner import filter_manifest

            scoped = filter_manifest(facts, ctx.partition)
            await log_cb(
                ctx.project_id,
                f"[custom] partition {ctx.partition.name!r}: "
                f"{scoped.unit_count}/{facts.unit_count} units, "
                f"{len(scoped.groups)}/{len(facts.groups)} groups",
                "info",
            )
            facts = scoped

        # Resolve the run's section contract now that the platform is known
        # (platform is a compiler input, not a runtime discriminator). No pack
        # selected => the built-in per-platform contract.
        if self._pack_contract is None or not self._pack_contract.is_pack:
            self._pack_contract = default_contract(facts.platform)
        elif self._pack_contract.platform != (facts.platform or "generic").lower():
            self._pack_contract = self._pack_contract.model_copy(
                update={"platform": (facts.platform or "generic").lower()}
            )

        state = HarnessRunState(project_id=ctx.project_id, facts_manifest=facts)
        facts_path = ctx.staging_dir / "facts_manifest.json"
        facts.write(facts_path)
        self._expand_per_job_docs(facts)

        # Partition-scoped runs must not leak sibling sub-projects' units into
        # scan-wide derived artifacts; every service below that re-reads the raw
        # scan gets the scoped unit allowlist. ``None`` => whole scan, unchanged.
        partition_allowlist: set[str] | None = (
            facts.unit_names() if ctx.partition is not None else None
        )

        # Talend-specific intelligence: ESB detection + activity flags.
        if facts.platform.lower() == "talend":
            try:
                scan_json_path = scan_output_dir / "scan_result.json"
                if scan_json_path.exists():
                    scan_result = json.loads(scan_json_path.read_text(encoding="utf-8", errors="replace"))
                    if partition_allowlist is not None:
                        scan_result = {
                            **scan_result,
                            "jobs": [
                                job for job in scan_result.get("jobs", []) or []
                                if str(job.get("job_id") or job.get("name") or "") in partition_allowlist
                            ],
                        }
                    write_talend_intelligence(scan_result, ctx.staging_dir)
            except Exception as exc:
                logger.warning("[custom] Talend intelligence extraction failed (non-fatal): %s", exc)

        _, engineering_artifacts = EngineeringFactsService().extract_and_write(
            project_id=ctx.project_id,
            scan_output_dir=scan_output_dir,
            facts_manifest=facts,
            staging_dir=ctx.staging_dir,
            unit_allowlist=partition_allowlist,
        )
        state.engineering_fact_artifacts = [
            path.name for path in engineering_artifacts if path.parent == ctx.staging_dir
        ]
        # Composite sub-project runs: surface this partition's deterministic
        # cross-project references (job-call edges, shared systems) as an
        # engineering-fact artifact so Phase 2/3 document them as external
        # links without loading any other sub-project's content.
        if ctx.partition is not None:
            self._materialize_cross_project_refs(ctx, state)
        materialize_prompt_packs(ctx.staging_dir, ctx.analysis_guide_file)
        if self._plugin_mode:
            self._prepare_pack_artifacts(ctx)
        await self._prepare_evidence_registry(ctx, state, log_cb)

        await log_cb(
            ctx.project_id,
            "[custom] resolved providers: "
            f"phase1={self._describe_provider(self.investigation_provider)} "
            f"phase2={self._describe_provider(self.model_provider)} "
            f"phase3={self._describe_provider(self.write_provider)}",
            "info",
        )
        await log_cb(
            ctx.project_id,
            f"[custom] facts manifest: {facts.unit_count} {facts.canonical_unit_label}, "
            f"{len(facts.groups)} groups",
            "info",
        )
        await log_cb(
            ctx.project_id,
            f"[custom] engineering facts artifacts: {len(state.engineering_fact_artifacts)} files",
            "info",
        )
        execution_plan: ExecutionPlan | None = None
        if config.agent_dag_enabled:
            execution_plan = await self._prepare_execution_plan(ctx, state, facts_path, log_cb)
            # Per-job pack documents are expanded after the facts manifest is known;
            # keep the execution plan aligned with that expanded document set.
            execution_plan.required_docs = list(self._required_docs)

        # Doc-targeted Phase 3 (Slice 3): narrow the required-doc set to the
        # scoped subset and preserve all unaffected baseline docs byte-stable.
        if ctx.scoped_docs is not None:
            await self._apply_scoped_docs(ctx, log_cb)

        # P0.1: size the run budget from the actual bin-packed work plan
        # (must run after per-job expansion and scoped-doc narrowing so the
        # required-doc set is final).
        budget_plan = self._plan_call_budget(ctx, state)
        await log_cb(
            ctx.project_id,
            "[custom] call budget plan: "
            f"agents={budget_plan['evidence_agent_calls']} "
            f"phase1={budget_plan['phase1_tasks']} phase2={budget_plan['phase2_tasks']} "
            f"retries={budget_plan['phase2_retries']} "
            f"synthesis={budget_plan['phase2_synthesis']} scorecard={budget_plan['scorecard_calls']} "
            f"phase3={budget_plan['phase3_calls']} topology={budget_plan['topology_calls']} "
            f"base={config.docgen_call_budget_base} planned={budget_plan['planned']} "
            f"ceiling={budget_plan['ceiling']} "
            f"phase3_floor={budget_plan['phase3_floor']} "
            f"topology_floor={budget_plan['topology_floor']} "
            f"output_budget={self._run_output_token_budget(state)}",
            "info",
        )
        # R2.2: standard runs with the LLM scorecard disabled publish a
        # deterministic omitted-scorecard marker so every consumer degrades
        # gracefully: the 07 doc and executive summary receive it as context,
        # and the enterprise doc-set validator (which requires a scorecard
        # file) passes without an LLM call. Plugin packs manage their own
        # scorecard policy and artifacts.
        if not self._plugin_mode and self._phase_policy("scorecard") == "skip":
            self._write_omitted_scorecard_marker(ctx, state)
            await log_cb(
                ctx.project_id,
                "[custom] Phase 2.6 LLM scorecard disabled "
                "(MCP_DOCGEN_LLM_SCORECARD_ENABLED=0); deterministic "
                "omitted-scorecard marker written",
                "info",
            )

        # ── Resolve investigation and analysis policies ───────────────────────
        investigation_policy = self._phase_policy("investigation")
        analysis_policy = self._phase_policy("analysis")
        if execution_plan is not None:
            investigation_policy = "skip" if execution_plan.investigation_mode == "skip" else "run"
            analysis_policy = {
                "skip": "skip",
                "direct": "bounded",
                "skeleton_plus_deep_dive": "dag_tiered",
                "full": "run",
            }[execution_plan.analysis_mode]

        # Pre-create investigation dir so Phase 1 skip path can write _summary.md
        investigation_dir = ctx.staging_dir / "_investigation"
        investigation_dir.mkdir(parents=True, exist_ok=True)

        # Build helpers and phase objects
        helpers = HarnessHelpersImpl(self)

        # Phase 1 skip path writes the _summary.md before the runner is called.
        # The runner will skip Phase1Investigation when investigation_mode == "skip".
        if investigation_policy == "skip":
            inv_reason = (
                "Pack-contract run: Phase 1 investigation is replaced by declared evidence profiles."
                if self._pack_contract_mode
                else f"Phase 1 skipped: investigation policy=skip (analysis={analysis_policy})."
            )
            (investigation_dir / "_summary.md").write_text(
                f"# Investigation Summary\n\n{inv_reason}\n", encoding="utf-8"
            )
            await log_cb(ctx.project_id, f"[custom] Phase 1 skipped ({analysis_policy=})", "info")
        else:
            use_direct_path = facts.unit_count <= config.small_project_threshold
            if use_direct_path:
                (investigation_dir / "_summary.md").write_text(
                    "# Investigation Summary\n\nDirect path: Phase 1 skipped for small project.\n",
                    encoding="utf-8",
                )
                await log_cb(
                    ctx.project_id,
                    f"[custom] direct path: {facts.unit_count} units ≤ {config.small_project_threshold} threshold; "
                    "skipping Phase 1 investigation",
                    "info",
                )

        # dag_execution_plan is None when DAG is disabled — used for Phase body logic that
        # records DAG node results.  runner_plan is always non-None — used by PipelineRunner
        # to drive Phase.should_skip() decisions.
        dag_execution_plan: ExecutionPlan | None = execution_plan  # real plan (may be None)

        if execution_plan is None:
            # Synthesise a minimal plan stub just for Phase.should_skip() decisions.
            # This plan never reaches Phase body DAG recording code.
            from mcp_server.providers.custom.models import ExecutionPlan as _EP
            use_direct_path = facts.unit_count <= config.small_project_threshold
            _skip_inv = investigation_policy == "skip" or use_direct_path
            _skip_scorecard = self._phase_policy("scorecard") == "skip"
            _skip_bundles = self._phase_policy("doc_bundles") == "skip"
            runner_plan = _EP(
                investigation_mode="skip" if _skip_inv else "full",
                analysis_mode="skip" if analysis_policy == "skip" else "full",
                scorecard=not _skip_scorecard,
                doc_bundles=not _skip_bundles,
                required_docs=list(self._required_docs),
            )
        else:
            runner_plan = execution_plan

        phases = [
            Phase1Investigation(helpers),
            Phase2Analysis(helpers, execution_plan=dag_execution_plan, analysis_policy=analysis_policy),
            Phase26Scorecard(helpers),
            Phase28Bundles(helpers),
            EvidenceQualityGate(),
            Phase3Docs(helpers, execution_plan=dag_execution_plan),
            Phase37BusinessTopology(helpers),
            Phase4Cleanup(helpers),
        ]

        try:
            phase_results = await PipelineRunner(phases).run(ctx, state, runner_plan, log_cb)

            # Post-run: collect failed groups from Phase 1 result for group pruning
            phase1_result = next((r for r in phase_results if r.phase_name == "phase1_investigation"), None)
            if phase1_result and dag_execution_plan is not None:
                self._record_dag_node_result(
                    ctx,
                    "investigation",
                    "skipped" if phase1_result.skipped else "passed",
                    artifacts=["_investigation/_summary.md"],
                    validation="investigation_coverage",
                )
            if phase1_result and not phase1_result.skipped:
                failed_groups: set[str] = set(phase1_result.metadata.get("failed_groups") or [])
                if failed_groups:
                    ctx.preprocess_groups = [g for g in ctx.preprocess_groups if g not in failed_groups]
                    await log_cb(
                        ctx.project_id,
                        f"[custom] WARNING: {len(failed_groups)} group(s) skipped due to investigation failure: "
                        f"{', '.join(sorted(failed_groups))}",
                        "warning",
                    )
                # validate investigation coverage after phase 1
                investigation_coverage = validate_investigation_coverage(state.investigation_reports, facts)
                debug_write(
                    debug_dir(ctx.staging_dir, "_investigation", "_coverage_report.json"),
                    investigation_coverage.model_dump_json(indent=2),
                )
                if not investigation_coverage.ok or investigation_coverage.coverage_count != facts.unit_count:
                    await log_cb(
                        ctx.project_id,
                        "[custom] WARNING: investigation coverage incomplete — proceeding to Phase 2: "
                        + ("; ".join(investigation_coverage.errors) or "partial coverage"),
                        "warning",
                    )
                else:
                    await log_cb(ctx.project_id, "[custom] Investigation coverage gate passed", "info")

            # Record DAG node results for phase 2 and 2.6 (Phase 3 and 4 record them in-phase)
            # Only when a real DAG plan exists (dag_execution_plan is not None).
            phase2_result = next((r for r in phase_results if r.phase_name == "phase2_analysis"), None)
            if phase2_result and not phase2_result.skipped and dag_execution_plan is not None:
                _ap = phase2_result.metadata.get("analysis_policy", analysis_policy)
                self._record_dag_node_result(
                    ctx,
                    "phase2_merge" if _ap == "dag_tiered" else "phase2",
                    "passed",
                    artifacts=["_analysis/*.md", "_analysis/_coverage_report.json", "_analysis/_depth_report.json"],
                    validation="analysis_coverage,analysis_depth,analysis_guide_sections",
                )
            phase26_result = next((r for r in phase_results if r.phase_name == "phase26_scorecard"), None)
            if phase26_result and dag_execution_plan is not None:
                self._record_dag_node_result(
                    ctx,
                    "scorecard",
                    "skipped" if phase26_result.skipped else "passed",
                    artifacts=["_assessment/scorecard.md"],
                    validation="scorecard_contract",
                )
            phase28_result = next((r for r in phase_results if r.phase_name == "phase28_bundles"), None)
            if phase28_result and dag_execution_plan is not None:
                self._record_dag_node_result(
                    ctx,
                    "doc_bundle",
                    "skipped" if phase28_result.skipped else "passed",
                    artifacts=["_analysis/_doc_pack_*.md"],
                )

            # Build pipeline log entries from results
            inv_result = next((r for r in phase_results if r.phase_name == "phase1_investigation"), None)
            _pipeline_log.add(
                "phase1", state, 0,
                "skip" if (inv_result and inv_result.skipped) else "pass",
            )
            _phase_calls_start = len(state.calls)
            _pipeline_log.flush(ctx.staging_dir)

        except Exception as exc:
            state.errors.append(str(exc))
            await log_cb(ctx.project_id, f"[custom] generation failed: {exc}", "error")
            try:
                _pipeline_log.add("error", state, _phase_calls_start, "fail", str(exc)[:80])
                _pipeline_log.flush(ctx.staging_dir)
            except Exception:
                pass
            return AgentResult(
                success=False,
                error=str(exc),
                model_name=self.model_provider.model_name,
                write_model_name=self.write_provider.model_name,
                input_tokens=state.input_tokens,
                output_tokens=state.output_tokens,
                duration_ms=int(time.time() * 1000) - start_ms,
            )

        self._write_phase_costs(ctx.staging_dir, state, log_cb)
        return AgentResult(
            success=True,
            has_warnings=state.warning_collector.has_warnings(),
            model_name=self.model_provider.model_name,
            write_model_name=(
                self.write_provider.model_name
                if self.write_provider.model_name != self.model_provider.model_name
                else None
            ),
            input_tokens=state.input_tokens,
            output_tokens=state.output_tokens,
            cost_usd=calculate_total_cost(
                (c.result.model_name, c.result.input_tokens, c.result.output_tokens)
                for c in state.calls
            ),
            duration_ms=int(time.time() * 1000) - start_ms,
            required_docs=list(self._required_docs),
        )

    def _write_phase_costs(
        self,
        staging_dir: Path,
        state: HarnessRunState,
        log_cb: LogCallback,
    ) -> None:
        """Aggregate real per-phase token costs and write _assessment/phase_costs.json.

        Always-on (not debug-gated): this is the per-phase token ledger used to
        see where a run's cost actually goes. Tokens are the provider-reported
        real counts from each call (the tool-loop sum, not the pre-call
        estimate). ``cache_read_tokens`` is the cache-hit portion of input
        (cheap); ``input_cache_miss = input_tokens - cache_read_tokens`` is the
        full-price portion — the lever that matters on non-caching providers.
        """
        phase_costs: dict[str, dict] = {}
        for call in state.calls:
            entry = phase_costs.setdefault(call.phase, {
                "input_tokens": 0, "input_cache_read_tokens": 0,
                "input_cache_miss_tokens": 0, "output_tokens": 0,
                "calls": 0, "latency_ms": 0,
            })
            inp = call.result.input_tokens or 0
            cache_read = call.result.cache_read_tokens or 0
            entry["input_tokens"] += inp
            entry["input_cache_read_tokens"] += cache_read
            entry["input_cache_miss_tokens"] += max(0, inp - cache_read)
            entry["output_tokens"] += call.result.output_tokens or 0
            entry["calls"] += 1
            entry["latency_ms"] += call.result.latency_ms or 0
        totals = {
            k: sum(p[k] for p in phase_costs.values())
            for k in ("input_tokens", "input_cache_read_tokens",
                      "input_cache_miss_tokens", "output_tokens", "calls", "latency_ms")
        } if phase_costs else {}
        report = {"by_phase": phase_costs, "totals": totals}
        out_path = staging_dir / "_assessment" / "phase_costs.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        # Warn operator if any phase exceeds configured threshold.
        for phase, costs in phase_costs.items():
            if costs["input_tokens"] > config.phase_cost_warning_input_tokens:
                logger.warning(
                    "[custom] phase %s input tokens (%d) exceeded warning threshold (%d)",
                    phase, costs["input_tokens"], config.phase_cost_warning_input_tokens,
                )

    async def _phase2_analysis(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        log_cb: LogCallback,
    ) -> None:
        preprocess_dir = ctx.staging_dir / "_preprocess"
        analysis_dir = ctx.staging_dir / "_analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        shared_context = self._phase2_shared_context(ctx, ctx.staging_dir / "facts_manifest.json")
        runtime = AgentTaskRuntime(ctx.staging_dir, state.facts_manifest)
        semaphore = asyncio.Semaphore(max(1, self._concurrency_for_phase("analysis", config.agent_phase2_concurrency)))

        async def _process_group(group: str) -> None:
            async with semaphore:
                await self._phase2_group_agent(
                    ctx,
                    state,
                    runtime,
                    group,
                    shared_context,
                    analysis_dir,
                    preprocess_dir,
                    log_cb,
                )

        gather_results = await asyncio.gather(
            *[_process_group(group) for group in ctx.preprocess_groups],
            return_exceptions=True,
        )
        await self._contain_phase2_group_failures(
            ctx, state, list(zip(ctx.preprocess_groups, gather_results)), log_cb
        )

    async def _contain_phase2_group_failures(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        results: list[tuple[str, object]],
        log_cb: LogCallback,
    ) -> None:
        """Salvage-first containment for per-group Phase 2 failures (P0.2).

        Replaces the fail-closed ``raise group_errors[0]`` chain: a failed
        group becomes a PARTIAL stub (coverage markers + explicit
        analysis-unavailable statements), joins ``state.thin_bundle_groups``
        (Phase 3 prepends the evidence notice) and ``state.partial_groups``,
        and the run continues so accepted sibling groups still ship. The stub
        intentionally fails ``group_analysis_is_complete`` so resume retries
        exactly the failed groups.
        """
        analysis_dir = ctx.staging_dir / "_analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        failed = [(g, r) for g, r in results if isinstance(r, BaseException)]
        if not failed:
            return
        # Same rule as Phase 3: a credential/billing failure is account state,
        # so containing it as a PARTIAL group means every later group and every
        # document fails identically and the run still reports success.
        for group, exc in failed:
            if self._is_fatal_provider_error_text(str(exc)):
                await log_cb(
                    ctx.project_id,
                    f"[custom] Phase 2 group {group} failed with a provider "
                    f"credential/billing error: {exc}; ABORTING the run rather "
                    "than containing it as PARTIAL -- fix the provider account "
                    "and resume.",
                    "error",
                )
                raise FatalProviderError(
                    f"provider credential/billing failure in Phase 2 group {group}: {exc}"
                )
        for group, exc in failed:
            await log_cb(
                ctx.project_id,
                f"[custom] Phase 2 group {group} failed ({exc}); marking PARTIAL "
                "and continuing -- resume retries this group",
                "error",
            )
            units = self._group_units(state, group)
            analysis_path = analysis_dir / f"{group}.md"
            if not analysis_path.exists() or analysis_path.stat().st_size == 0:
                analysis_path.write_text(
                    _partial_group_stub(group, units, str(exc)[:300]), encoding="utf-8"
                )
            self._write_phase2_coverage_record(
                ctx, group, units, analysis_path, status="partial"
            )
            if group not in state.thin_bundle_groups:
                state.thin_bundle_groups.append(group)
            if group not in state.partial_groups:
                state.partial_groups.append(group)
            state.warnings.append(
                f"Phase 2 group {group} is PARTIAL in this run: {str(exc)[:200]}"
            )
        await log_cb(
            ctx.project_id,
            f"[custom] Phase 2 salvage: {len(failed)}/{len(results)} group(s) "
            "PARTIAL; accepted groups proceed to Phase 3 with an evidence notice",
            "warning",
        )

    async def _record_section_coverage_gap(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        group: str,
        missing: list[str],
        contract: "PackContract",
        log_cb: LogCallback,
        *,
        phase: str = "phase2",
    ) -> None:
        """Record, quietly, that a declared section produced nothing.

        A pack asks questions of the evidence. Some of those questions have the
        answer "there is nothing here" -- a SQL-injection pack pointed at a job
        with no dynamic SQL SHOULD come back empty for that section, and that is
        a correct result, not a defect. So this is a debugging breadcrumb only:

        * the group keeps its real analysis (never a PARTIAL stub);
        * the gap lands in ``_assessment/pack_section_coverage.json`` with the
          question that went unanswered, so an author can tell "my question was
          wrong" from "this codebase genuinely has none of that";
        * the documents publish clean -- no notice, no warning code, no quality
          penalty, no ``completed_warnings`` run status.
        """
        missing_sorted = sorted({str(name) for name in missing})
        if not missing_sorted:
            return
        for record in state.section_coverage_gaps:
            if record.get("group") == group and record.get("phase") == phase:
                record["missing_sections"] = sorted(
                    set(record.get("missing_sections") or []) | set(missing_sorted)
                )
                break
        else:
            questions = contract.questions
            state.section_coverage_gaps.append(
                {
                    "pack_id": contract.pack_id,
                    "group": group,
                    "missing_sections": missing_sorted,
                    "questions": {
                        name: questions.get(name, "") for name in missing_sorted
                    },
                    "phase": phase,
                }
            )
        self._write_section_coverage_gaps(ctx.staging_dir, state)
        await log_cb(
            ctx.project_id,
            f"[custom] group {group} produced no content for declared section(s) "
            f"{', '.join(missing_sorted)} of pack {contract.pack_id}; publishing the "
            "analysis as found -- see _assessment/pack_section_coverage.json",
            "info",
        )

    @staticmethod
    def _write_section_coverage_gaps(
        staging_dir: Path, state: HarnessRunState
    ) -> None:
        assessment_dir = staging_dir / "_assessment"
        assessment_dir.mkdir(parents=True, exist_ok=True)
        (assessment_dir / SECTION_COVERAGE_FILENAME).write_text(
            json.dumps(state.section_coverage_gaps, indent=2), encoding="utf-8"
        )

    def _phase2_pack_contract_records(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
    ) -> None:
        analysis_dir = ctx.staging_dir / "_analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        profile_names = ", ".join(sorted(getattr(self._plugin_manifest, "evidence_profiles", {}) or {}))
        for group in state.facts_manifest.groups:
            group_units = self._group_units(state, group)
            coverage = "\n".join(f"- COVERED: {unit}" for unit in group_units)
            unit_lines = "\n".join(
                f"- {unit}: evidence is supplied through plugin evidence profile bundles."
                for unit in group_units
            )
            analysis_path = analysis_dir / f"{group}.md"
            analysis_path.write_text(
                f"# Phase 2 Analysis: {group}\n\n"
                "## Coverage\n\n"
                f"{coverage}\n\n"
                "## Model Analysis\n\n"
                "### GROUP_SUMMARY\n\n"
                "Pack-contract run. Broad enterprise Phase 2 model analysis is intentionally skipped; "
                f"declared evidence profiles drive generation instead: {profile_names or 'none'}.\n\n"
                "Units:\n"
                f"{unit_lines}\n",
                encoding="utf-8",
            )
            self._write_phase2_coverage_record(ctx, group, group_units, analysis_path)

    async def _phase2_group_agent(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        runtime: AgentTaskRuntime,
        group: str,
        shared_context: list[Path],
        analysis_dir: Path,
        preprocess_dir: Path,
        log_cb: LogCallback,
        phase_name: str = "phase2",
    ) -> None:
        """Run the claim-ledger Phase 2 architecture for one scanner group."""

        await self._phase2_group_claims(
            ctx,
            state,
            runtime,
            group,
            shared_context,
            analysis_dir,
            preprocess_dir,
            log_cb,
            phase_name=phase_name,
        )

    async def _phase2_group_claims(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        runtime: AgentTaskRuntime,
        group: str,
        shared_context: list[Path],
        analysis_dir: Path,
        preprocess_dir: Path,
        log_cb: LogCallback,
        phase_name: str = "phase2",
    ) -> None:
        """Extract validated claims and render the Phase 2 compatibility view.

        Rich scanner output remains on disk.  Each paid request receives one
        canonical unit task and one bounded documentation concern.  The
        accepted JSON claim ledger is authoritative; Markdown is a
        deterministic compatibility projection for the existing Phase 3
        bundle and validation pipeline.
        """

        analysis_path = analysis_dir / f"{group}.md"
        if (
            ctx.resume_state is not None or ctx.scoped_docs is not None
        ) and analysis_path.exists() and analysis_path.stat().st_size > 500:
            if group_analysis_is_complete(
                analysis_path,
                state.facts_manifest.platform,
                contract=self._contract_for(state.facts_manifest.platform),
            ):
                await log_cb(
                    ctx.project_id,
                    f"[custom] REUSE: skipping Phase 2 claims for {group} (accepted ledger view)",
                    "info",
                )
                self._write_phase2_coverage_record(
                    ctx, group, self._group_units(state, group), analysis_path
                )
                return

        group_units = self._group_units(state, group)
        if not group_units:
            raise RuntimeError(
                "Phase 2 scanner contract mismatch: preprocess group "
                f"{group!r} has no canonical units in facts_manifest"
            )
        group_file = preprocess_dir / f"{group}.md"
        if not group_file.exists():
            raise RuntimeError(
                f"Phase 2 source group file missing: "
                f"{group_file.relative_to(ctx.staging_dir).as_posix()}"
            )
        if group_file.stat().st_size < config.min_investigation_content_bytes:
            analysis_path.write_text(_empty_group_stub(group, group_units), encoding="utf-8")
            self._write_phase2_coverage_record(
                ctx, group, group_units, analysis_path, status="packet_limited"
            )
            return

        tasks = plan_evidence_tasks(
            group,
            group_file,
            group_units,
            analysis_dir,
            config.phase2_task_evidence_bytes,
            max_units_per_task=max(1, config.phase2_claim_units_per_task),
        )
        if not tasks:
            raise RuntimeError(f"Phase 2 evidence task planning produced no tasks for {group}")
        record_manifest_chunks(
            ctx.staging_dir, phase="phase2_claims", group=group, chunks=[task.path for task in tasks]
        )
        investigation_context = self._investigation_context(ctx, group)
        batches: list[Phase2ClaimBatch] = []
        planned_specs: list[ClaimTaskSpec] = []
        for task in tasks:
            planned_specs.extend(
                plan_claim_tasks(
                    task,
                    evidence_bytes=task.path.stat().st_size,
                    dense_threshold_bytes=config.phase2_dense_unit_bytes,
                    max_output_tokens=config.phase2_claim_max_tokens,
                    sections=self._contract_for(state.facts_manifest.platform).analysis_section_names,
                )
            )

        await log_cb(
            ctx.project_id,
            f"[custom] Phase 2 claim plan: {group} -> {len(tasks)} unit packet(s), "
            f"{len(planned_specs)} concern task(s)",
            "info",
        )
        for spec in planned_specs:
            batches.extend(
                await self._run_phase2_claim_spec(
                    ctx,
                    state,
                    runtime,
                    group,
                    spec,
                    shared_context,
                    investigation_context,
                    log_cb,
                    phase_name=phase_name,
                )
            )

        # Fail-loudly enforcement of the pack's declared-required sections on
        # the claims path. render_claims_as_markdown always emits a heading
        # (plus an UNKNOWN stub) for every contract section, so a heading
        # check can never detect an unanswered section here; the truthful
        # signal is the claim ledger -- a declared section with no non-unknown
        # claim got no answer. Groups the salvage path already
        # marked PARTIAL are skipped: their gap is a pipeline failure owned by
        # `_contain_phase2_group_failures` / the claim-spec salvage, and resume
        # retries them.
        claims_contract = self._contract_for(state.facts_manifest.platform)
        if claims_contract.is_pack and group not in state.partial_groups:
            satisfied_sections = {
                claim.section
                for batch in batches
                for claim in batch.claims
                if claim.kind != "unknown"
            }
            unanswered = [
                name
                for name in claims_contract.analysis_section_names
                if name not in satisfied_sections
            ]
            if unanswered:
                await self._record_section_coverage_gap(
                    ctx, state, group, unanswered, claims_contract, log_cb
                )

        claim_dir = analysis_dir / "_claims"
        ledger_path = claim_dir / f"{self._safe_name(group)}.jsonl"
        write_claim_ledger(
            ledger_path,
            group=group,
            unit_names=group_units,
            batches=batches,
        )
        model_analysis = render_claims_as_markdown(
            group=group,
            unit_names=group_units,
            batches=batches,
            sections=self._contract_for(state.facts_manifest.platform).analysis_section_names,
            relationship_section_names=self._contract_for(state.facts_manifest.platform).relationship_section_names,
        )
        coverage_block = "\n".join(f"- COVERED: {name}" for name in group_units)
        content = (
            f"# Phase 2 Analysis: {group}\n\n"
            "## Coverage\n\n"
            f"{coverage_block}\n\n"
            "## Model Analysis\n\n"
            f"{model_analysis}"
        )
        analysis_path.write_text(content, encoding="utf-8")
        receipt = {
            "schema_version": "phase2-claims-v1",
            "group": group,
            "unit_count": len(group_units),
            "physical_evidence_tasks": len(tasks),
            "planned_concern_tasks": len(planned_specs),
            "accepted_or_fallback_batches": len(batches),
            "claim_count": sum(len(batch.claims) for batch in batches),
            "unknown_claim_count": sum(
                claim.kind == "unknown" for batch in batches for claim in batch.claims
            ),
            "ledger": ledger_path.relative_to(ctx.staging_dir).as_posix(),
        }
        (claim_dir / f"{self._safe_name(group)}.receipt.json").write_text(
            json.dumps(receipt, indent=2), encoding="utf-8"
        )
        self._write_phase2_coverage_record(ctx, group, group_units, analysis_path)
        await log_cb(
            ctx.project_id,
            f"[custom] Phase 2 claims complete: {group} "
            f"({receipt['claim_count']} claims, {receipt['unknown_claim_count']} unknowns)",
            "info",
        )

    async def _run_phase2_claim_spec(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        runtime: AgentTaskRuntime,
        group: str,
        spec: ClaimTaskSpec,
        shared_context: list[Path],
        investigation_context: list[Path],
        log_cb: LogCallback,
        *,
        phase_name: str,
    ) -> list[Phase2ClaimBatch]:
        """Run one claim spec, recursively re-planning provider truncation."""

        allowed_units = spec.task.all_units
        evidence_slice = EvidenceRegistryBuilder(ctx.staging_dir).build_phase2_slice(
            spec.target_id, allowed_units
        )
        evidence_brief = ctx.staging_dir / "_evidence_registry" / "evidence_brief.md"
        packet = build_phase2_context_packet(
            staging_dir=ctx.staging_dir,
            facts=state.facts_manifest,
            group=group,
            task_id=spec.target_id,
            unit_names=allowed_units,
            task_evidence=spec.task.path,
            shared_context=[path for path in shared_context if path != evidence_brief],
            investigation_context=investigation_context,
            scoped_evidence=evidence_slice,
            scan_output_dir=config.intodoc_output_dir / ctx.project_id,
            max_bytes=config.phase2_task_context_bytes,
            max_scoped_artifact_bytes=config.phase2_filtered_map_bytes,
            artifact_names=spec.artifact_names,
            # The Nexus-owned platform analysis policy is materialized as a
            # prompt pack and promoted to the system channel before dispatch.
            # Plugins cannot suppress it, including in replace-mode lenses.
            include_prompt_pack=True,
            include_investigation=True,
        )
        context_files = list(packet.files)
        prompt = claim_task_prompt(
            facts=state.facts_manifest,
            group=group,
            spec=spec,
            plugin_pack_dir=self.plugin_pack_dir,
            contract=self._contract_for(state.facts_manifest.platform),
        )
        opts = ModelOptions(
            max_tokens=min(config.phase2_claim_max_tokens, spec.max_output_tokens),
            temperature=0.0,
            # One adapter-level retry is reserved for retryable transport or
            # rate-limit failures. Content-completeness failures (including
            # finish_reason=length) return immediately from the adapter.
            retries=1,
            timeout_s=300.0,
            json_mode=True,
            provider_options=self._provider_options(
                self.model_provider, phase="phase2_claim"
            ),
        )
        contract = AgentTaskContract(
            phase="phase2_group",
            target_id=spec.target_id,
            allowed_units=allowed_units,
            allowed_groups=[group],
            allowed_categories=self._default_evidence_categories(),
            starter_context_files=[
                path.relative_to(ctx.staging_dir).as_posix()
                for path in context_files
                if path.exists()
            ],
            tool_budget=default_tool_budget(),
            input_budget=default_input_budget(),
            expected_artifact="phase2 evidence claim JSON",
            max_attempts=min(2, config.agent_max_task_attempts),
        )
        try:
            task_evidence_text = spec.task.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            task_evidence_text = ""

        async def _floored(batch: Phase2ClaimBatch) -> list[Phase2ClaimBatch]:
            """Apply the deterministic per-claim demotion floor (R2.4)."""
            adjusted, notes = apply_claim_floors(batch, task_evidence_text or None)
            if notes:
                await log_cb(
                    ctx.project_id,
                    f"[custom] Phase 2 claim floor for {spec.target_id}: "
                    f"{len(notes)} claim(s) demoted/coerced "
                    f"(first: {notes[0]})",
                    "info",
                )
            return [adjusted]

        cached = runtime.accepted_output(contract) if ctx.resume_state is not None else None
        if cached is not None:
            try:
                return await _floored(
                    parse_claim_batch(
                        cached,
                        expected_group=group,
                        expected_task_id=spec.target_id,
                        expected_concern=spec.concern,
                    )
                )
            except (json.JSONDecodeError, ValueError):
                pass
        if ctx.resume_state is not None:
            status_path = runtime.task_dir(contract) / "status.json"
            try:
                prior_status = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                prior_status = {}
            if prior_status.get("incomplete_reason") == "length":
                children = split_claim_task(spec)
                if children is not None:
                    await log_cb(
                        ctx.project_id,
                        f"[custom] RESUME: reusing length signal for {spec.target_id}; "
                        "continuing with its smaller semantic children",
                        "info",
                    )
                    resumed_batches: list[Phase2ClaimBatch] = []
                    for child in children:
                        resumed_batches.extend(
                            await self._run_phase2_claim_spec(
                                ctx,
                                state,
                                runtime,
                                group,
                                child,
                                shared_context,
                                investigation_context,
                                log_cb,
                                phase_name=phase_name,
                            )
                        )
                    return resumed_batches

        async def _dispatch(
            contract_task: AgentTaskContract, tools: ScopedEvidenceTools
        ) -> ModelResult:
            # R2.6 cache-friendly retry composition: providers concatenate
            # prompt + context files into one user message, so appending the
            # validation-gap feedback to the PROMPT shifted every evidence
            # byte after it and invalidated the provider prefix cache for the
            # whole packet on the repair attempt. The prompt and the evidence
            # packet stay byte-stable; gap feedback is a transient file
            # appended at the TAIL (same pattern as the slice instruction).
            effective_context = context_files
            if contract_task.attempt > 1:
                gaps = "\n".join(
                    f"- {gap}" for gap in contract_task.validation_gaps
                ) or "- Previous JSON failed validation."
                repair_tail = transient_dir(
                    ctx.staging_dir, "claim_repair", phase_name
                ) / f"{self._safe_name(contract_task.target_id)}__attempt_{contract_task.attempt}.md"
                transient_write(
                    repair_tail,
                    "REPAIR INSTRUCTION: Return a complete corrected replacement "
                    f"JSON object. Fix only these validation gaps:\n{gaps}",
                )
                effective_context = [*context_files, repair_tail]
            return await self._complete_core_generation(
                ctx,
                state,
                provider=self.model_provider,
                phase=f"{phase_name}.claims",
                target=contract_task.target_id,
                prompt=prompt,
                context_files=effective_context,
                opts=opts,
                log_cb=log_cb,
                task_contract=contract_task,
                scoped_tools=tools,
            )

        allowed_refs = self._allowed_component_refs(context_files)

        def _validate(text: str) -> dict:
            report = validate_claim_batch(
                text,
                facts=state.facts_manifest,
                group=group,
                spec=spec,
                evidence_text=task_evidence_text or None,
            )
            return self._merge_component_ref_errors(
                report,
                text,
                allowed_refs,
                "this claim task's scoped evidence",
            )

        task_result = await runtime.run(contract, _dispatch, _validate)
        if task_result.accepted:
            return await _floored(
                parse_claim_batch(
                    task_result.result.text,
                    expected_group=group,
                    expected_task_id=spec.target_id,
                    expected_concern=spec.concern,
                )
            )

        if task_result.incomplete_reason == "length":
            children = split_claim_task(spec)
            if children is not None:
                await log_cb(
                    ctx.project_id,
                    f"[custom] Phase 2 claim task {spec.target_id} reached its output limit; "
                    f"re-planning into {children[0].target_id} and {children[1].target_id}",
                    "warning",
                )
                batches: list[Phase2ClaimBatch] = []
                for child in children:
                    batches.extend(
                        await self._run_phase2_claim_spec(
                            ctx,
                            state,
                            runtime,
                            group,
                            child,
                            shared_context,
                            investigation_context,
                            log_cb,
                            phase_name=phase_name,
                        )
                    )
                return batches

        reason = (
            task_result.incomplete_reason
            or task_result.result.raw_error
            or "claim validation failed"
        )
        # Round 3 salvage-first terminal path (verified failure: ONE invalid
        # claim structurally rejected the whole batch and blanked every unit
        # in the bin-packed task to UNKNOWN). When the final output parses as
        # JSON, keep its individually valid claims and drop only the
        # offenders; the honest unknown_batch remains the fallback for truly
        # unparseable output.
        final_text = (task_result.result.text or "").strip()
        if final_text:
            salvaged, salvage_notes = salvage_claim_batch(
                final_text,
                facts=state.facts_manifest,
                group=group,
                spec=spec,
                allowed_component_refs=allowed_refs,
            )
            if salvaged is not None and salvaged.claims:
                await log_cb(
                    ctx.project_id,
                    f"[custom] Phase 2 claim task {spec.target_id} rejected "
                    f"({reason}); salvaged {len(salvaged.claims)} valid claim(s), "
                    f"{len(salvage_notes)} adjustment(s)"
                    + (f" (first: {salvage_notes[0]})" if salvage_notes else ""),
                    "warning",
                )
                state.warnings.append(
                    f"Phase 2 claim scope {spec.target_id} was salvaged per-claim "
                    f"after terminal rejection ({reason}): "
                    f"{len(salvaged.claims)} claim(s) kept"
                )
                # The group stays PARTIAL: content was dropped, the operator
                # must see the gap, and resume retries the task.
                if group not in state.thin_bundle_groups:
                    state.thin_bundle_groups.append(group)
                if group not in state.partial_groups:
                    state.partial_groups.append(group)
                return await _floored(salvaged)
        await log_cb(
            ctx.project_id,
            f"[custom] Phase 2 claim task {spec.target_id} unavailable; "
            f"recording explicit pipeline-gap unknowns: {reason}",
            "warning",
        )
        state.warnings.append(
            f"Phase 2 claim scope {spec.target_id} did not complete ({reason}); "
            "scan evidence for its units exists — pipeline gap, not source absence"
        )
        # P0.2: a degraded claim scope marks its group PARTIAL so Phase 3
        # carries the evidence notice and the operator sees the gap.
        if group not in state.thin_bundle_groups:
            state.thin_bundle_groups.append(group)
        if group not in state.partial_groups:
            state.partial_groups.append(group)
        return [unknown_batch(group=group, spec=spec, reason=reason)]

    async def _phase2_group_agent_legacy(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        runtime: AgentTaskRuntime,
        group: str,
        shared_context: list[Path],
        analysis_dir: Path,
        preprocess_dir: Path,
        log_cb: LogCallback,
        phase_name: str = "phase2",
    ) -> None:
            analysis_path = analysis_dir / f"{group}.md"
            # Reuse an existing group analysis when resuming a prior run OR when this
            # is a scoped reingestion (incremental staging restored the unchanged
            # groups' analyses and deleted only the changed groups', so a present
            # analysis here == an unchanged group whose Phase 2 must not re-run).
            if (ctx.resume_state is not None or ctx.scoped_docs is not None) and analysis_path.exists() and analysis_path.stat().st_size > 500:
                if group_analysis_is_complete(
                    analysis_path,
                    state.facts_manifest.platform,
                    contract=self._contract_for(state.facts_manifest.platform),
                ):
                    await log_cb(ctx.project_id, f"[custom] REUSE: skipping Phase 2 for {group} (unchanged group; cached analysis)", "info")
                    self._write_phase2_coverage_record(ctx, group, self._group_units(state, group), analysis_path)
                    return
                await log_cb(ctx.project_id, f"[custom] REUSE: cached Phase 2 for {group} is incomplete, regenerating", "info")

            group_units = self._group_units(state, group)
            if not group_units:
                raise RuntimeError(
                    "Phase 2 scanner contract mismatch: preprocess group "
                    f"{group!r} has no canonical units in facts_manifest"
                )
            group_file = preprocess_dir / f"{group}.md"
            if not group_file.exists():
                raise RuntimeError(f"Phase 2 source group file missing: {group_file.relative_to(ctx.staging_dir).as_posix()}")
            opts = ModelOptions(
                max_tokens=min(config.phase2_max_tokens, config.phase2_task_max_tokens),
                temperature=0.1,
                retries=1,
                timeout_s=300.0,
                provider_options=self._provider_options(self.model_provider),
            )
            tasks = plan_evidence_tasks(
                group, group_file, group_units, analysis_dir, config.phase2_task_evidence_bytes
            )
            if not tasks:
                raise RuntimeError(f"Phase 2 evidence task planning produced no tasks for {group}")
            record_manifest_chunks(ctx.staging_dir, phase="phase2", group=group, chunks=[t.path for t in tasks])
            investigation_context = self._investigation_context(ctx, group)
            task_outputs: list[str] = []
            multi_task = len(tasks) > 1
            for task in tasks:
                if not task.path.exists():
                    raise RuntimeError(f"Phase 2 task evidence file missing: {task.path.relative_to(ctx.staging_dir).as_posix()}")
                prompt = group_analysis_prompt(
                    state.facts_manifest,
                    group,
                    pack=self.pack,
                    plugin_pack_dir=self.plugin_pack_dir,
                    required_docs=self._required_docs,
                    task_units=task.units if multi_task else None,
                    missing_evidence_units=task.missing_units if multi_task else None,
                    multi_task=multi_task,
                    contract=self._contract_for(state.facts_manifest.platform),
                )
                target = task.task_id
                allowed_units = task.all_units if multi_task else group_units
                evidence_slice = EvidenceRegistryBuilder(ctx.staging_dir).build_phase2_slice(
                    task.path.stem, allowed_units
                )
                evidence_brief = ctx.staging_dir / "_evidence_registry" / "evidence_brief.md"
                # Replace the broad evidence_brief in shared_context with the per-task slice so
                # the model gets targeted evidence (~20-50 KB) instead of the full brief (~50 KB).
                base_context = [f for f in shared_context if f != evidence_brief]
                task_evidence = (
                    [evidence_slice]
                    if evidence_slice is not None
                    else []
                )
                packet = build_phase2_context_packet(
                    staging_dir=ctx.staging_dir,
                    facts=state.facts_manifest,
                    group=group,
                    task_id=target,
                    unit_names=allowed_units,
                    task_evidence=task.path,
                    shared_context=base_context,
                    investigation_context=investigation_context,
                    scoped_evidence=task_evidence[0] if task_evidence else None,
                    scan_output_dir=config.intodoc_output_dir / ctx.project_id,
                    max_bytes=config.phase2_task_context_bytes,
                    max_scoped_artifact_bytes=config.phase2_filtered_map_bytes,
                )
                context_files = list(packet.files)
                contract = AgentTaskContract(
                    phase="phase2_group",
                    target_id=target,
                    allowed_units=allowed_units,
                    allowed_groups=[group],
                    allowed_categories=self._default_evidence_categories(),
                    starter_context_files=[p.relative_to(ctx.staging_dir).as_posix() for p in context_files if p.exists()],
                    tool_budget=default_tool_budget(),
                    input_budget=default_input_budget(),
                    expected_artifact="analysis markdown",
                    max_attempts=config.agent_max_task_attempts,
                )
                cached = runtime.accepted_output(contract) if ctx.resume_state is not None else None
                if cached is not None:
                    task_outputs.append(cached.strip())
                    await log_cb(ctx.project_id, f"[custom] RESUME: skipping Phase 2 task {target} (accepted)", "info")
                    continue

                async def _dispatch(contract_task: AgentTaskContract, tools: ScopedEvidenceTools) -> ModelResult:
                    effective_prompt = prompt
                    if contract_task.attempt > 1:
                        gaps = "\n".join(
                            f"- {gap}" for gap in contract_task.validation_gaps
                        ) or "- Previous output failed validation."
                        effective_prompt += (
                            "\n\nThis is the single bounded replacement attempt. Return a complete "
                            "replacement within the same compact output contract. Correct these "
                            f"validation gaps directly:\n{gaps}"
                        )
                    return await self._complete_core_generation(
                        ctx,
                        state,
                        provider=self.model_provider,
                        phase=phase_name,
                        target=contract_task.target_id,
                        prompt=effective_prompt,
                        context_files=context_files,
                        opts=opts,
                        log_cb=log_cb,
                        task_contract=contract_task,
                        scoped_tools=tools,
                    )

                # Component references the agent is allowed to cite: whatever its
                # own evidence (task file + investigation context) actually names.
                # Anything else is fabrication (verified failure mode: invented
                # component numbering for jobs outside the agent's evidence).
                allowed_refs = self._allowed_component_refs(context_files)
                # Bin-packed tasks (>1 job in one call) get job-scoped checking on
                # top of the task-wide fallback: a component real for one job in
                # the batch must not be accepted when attributed to a sibling job
                # in the same batch (verified failure mode, run 51: tMSSqlOutput_1
                # real for AssetCheck_GetLocation, misattributed to sibling
                # AssetCheck_GetWorkOrder, which actually uses tDBOutput_1).
                job_refs: dict[str, frozenset[str]] = {}
                if len(task.units) > 1:
                    for unit, unit_text in job_section_texts(task.path, task.units).items():
                        job_refs[unit] = frozenset(extract_component_refs(unit_text))
                task_result = await runtime.run(
                    contract,
                    _dispatch,
                    lambda text, units=contract.allowed_units, refs=allowed_refs, jrefs=job_refs: self._merge_component_ref_errors(
                        self._validate_phase2_task_output(
                            text,
                            units,
                            platform=state.facts_manifest.platform,
                            require_sections=True,
                            contract=self._contract_for(state.facts_manifest.platform),
                        ),
                        text,
                        refs,
                        "this task's evidence file",
                        job_refs=jrefs,
                    ),
                )
                if not task_result.accepted:
                    # Salvage-first (P0.2): an unaccepted task marks its group
                    # PARTIAL instead of aborting the run. Non-trivial output
                    # is kept flagged; otherwise the task's units get an
                    # explicit analysis-unavailable note. The group joins
                    # thin_bundle_groups so Phase 3 carries the evidence notice.
                    reason = (
                        task_result.incomplete_reason
                        or task_result.result.raw_error
                        or task_result.status
                    )
                    await log_cb(
                        ctx.project_id,
                        f"[custom] Phase 2 task {target} was not accepted ({reason}); "
                        "keeping partial output and continuing",
                        "warning",
                    )
                    salvage_text = task_result.result.text.strip()
                    if len(salvage_text.encode("utf-8")) >= 200:
                        task_outputs.append(
                            f"> PARTIAL (unvalidated) task output for {target}: {reason}\n\n"
                            + salvage_text
                        )
                    else:
                        unavailable = "\n".join(
                            f"- {u}: analysis unavailable in this run ({reason})"
                            for u in (task.all_units if multi_task else group_units)
                        )
                        task_outputs.append(
                            f"### GROUP_SUMMARY\n\nPARTIAL: task {target} produced no "
                            f"usable analysis.\n\n{unavailable}"
                        )
                    if group not in state.thin_bundle_groups:
                        state.thin_bundle_groups.append(group)
                    if group not in state.partial_groups:
                        state.partial_groups.append(group)
                    state.warnings.append(
                        f"Phase 2 task {target} is PARTIAL in this run: {str(reason)[:200]}"
                    )
                    continue
                task_outputs.append(task_result.result.text.strip())

            if multi_task:
                _guide_file = ctx.staging_dir / "_preprocess" / ctx.analysis_guide_file if ctx.analysis_guide_file else None
                _extra = _guide_section_names(_guide_file) if _guide_file else frozenset()
                assembled, summary_fallback = self._assemble_phase2_task_sections(
                    group, tasks, task_outputs, _extra, contract=self._contract_for(state.facts_manifest.platform)
                )
                await log_cb(
                    ctx.project_id,
                    f"[custom] Phase 2 deterministic assembly complete: {group} ({len(task_outputs)} tasks)",
                    "info",
                )
                model_analysis = await self._phase2_group_synthesis(
                    ctx,
                    state,
                    group,
                    group_units,
                    assembled,
                    summary_fallback,
                    shared_context,
                    investigation_context,
                    opts,
                    log_cb,
                )
            else:
                model_analysis = "\n\n---\n\n".join(task_outputs)
            model_analysis = await self._complete_phase2_sections(
                ctx,
                state,
                group,
                model_analysis,
                shared_context,
                investigation_context,
                group_file,
                opts,
                log_cb,
            )
            coverage_block = "\n".join(f"- COVERED: {name}" for name in group_units)
            content = (
                f"# Phase 2 Analysis: {group}\n\n"
                "## Coverage\n\n"
                f"{coverage_block}\n\n"
                "## Model Analysis\n\n"
                f"{model_analysis}\n"
            )
            (analysis_dir / f"{group}.md").write_text(content, encoding="utf-8")
            self._write_phase2_coverage_record(ctx, group, group_units, analysis_dir / f"{group}.md")
            await log_cb(ctx.project_id, f"[custom] Phase 2 complete: {group}", "info")

    @classmethod
    def _assemble_phase2_task_sections(
        cls,
        group: str,
        tasks: list[EvidenceTask],
        task_outputs: list[str],
        extra_section_names: frozenset[str] = frozenset(),
        contract: "PackContract | None" = None,
    ) -> tuple[str, str]:
        """Deterministically assemble per-job task outputs into one group analysis body.

        Pure selection: every body is verbatim LLM output, regrouped so each
        registered section appears under exactly one ``### SECTION`` heading with
        per-task provenance subheadings naming the units that task analyzed.

        GROUP_SUMMARY bodies are excluded from the assembly and returned as a
        pre-formatted fallback block: the group synthesis task writes the
        authoritative group-level GROUP_SUMMARY (and CROSS_JOB_FINDINGS); the
        fallback is used only if that synthesis call fails.
        """
        parsed_tasks = [
            cls._parse_phase2_sections(text, extra_section_names, contract=contract)
            for text in task_outputs
        ]
        provenance = [
            f"task {task.task_id}: {', '.join(task.all_units) or 'unscoped'}"
            for task in tasks
        ]
        parts: list[str] = [
            f"Deterministic assembly of {len(task_outputs)} per-job Phase 2 task analyses for group `{group}`.",
            "Task text is preserved verbatim under provenance subheadings naming the analyzed units.",
        ]
        summary_sections = {GROUP_SUMMARY_SECTION, CROSS_JOB_FINDINGS_SECTION, LEGACY_CROSS_CHUNK_SECTION}
        base_ordered = contract.ordered_names if contract is not None else PHASE2_SECTION_NAMES
        ordered_sections = tuple(base_ordered) + tuple(
            sorted(extra_section_names - set(base_ordered))
        )
        for section in ordered_sections:
            if section in summary_sections:
                continue
            section_parts: list[str] = []
            for label, sections in zip(provenance, parsed_tasks):
                body = sections.get(section, "").strip()
                if not body:
                    continue
                section_parts.append(f"#### {label}\n\n{body}")
            if section_parts:
                parts.append(f"### {section}\n\n" + "\n\n".join(section_parts))
        assembled = "\n\n".join(parts).rstrip() + "\n"

        summary_parts: list[str] = []
        for label, sections in zip(provenance, parsed_tasks):
            body = sections.get(GROUP_SUMMARY_SECTION, "").strip()
            if body:
                summary_parts.append(f"#### {label}\n\n{body}")
        summary_fallback = (
            f"### {GROUP_SUMMARY_SECTION}\n\n" + "\n\n".join(summary_parts)
            if summary_parts
            else ""
        )
        return assembled, summary_fallback

    @staticmethod
    def _parse_phase2_sections(
        text: str,
        extra_section_names: frozenset[str] = frozenset(),
        contract: "PackContract | None" = None,
    ) -> dict[str, str]:
        from mcp_server.providers.custom.analysis_parser import parse_phase2_sections
        return parse_phase2_sections(text, extra_section_names, contract=contract)

    async def _phase2_group_synthesis(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        group: str,
        group_units: list[str],
        assembled_analysis: str,
        summary_fallback: str,
        shared_context: list[Path],
        investigation_context: list[Path],
        opts: ModelOptions,
        log_cb: LogCallback,
    ) -> str:
        """One group-level synthesis call: writes the cross-job sections only.

        Per-job facts are carried verbatim by the deterministic assembly; the
        synthesis model reads the assembled sections and contributes ONLY
        CROSS_JOB_FINDINGS and the group-level GROUP_SUMMARY. If the call fails
        or omits GROUP_SUMMARY, the per-task summary fallback (verbatim LLM
        text under a structural heading) is used instead.
        """

        synthesis_input = transient_dir(
            ctx.staging_dir, f"phase2_synthesis_{self._safe_name(group)}.md"
        )
        summary_source = summary_fallback.strip() or (
            "### GROUP_SUMMARY\n\nCanonical units in this group:\n"
            + "\n".join(f"- {name}" for name in group_units)
        )
        transient_write(
            synthesis_input,
            "# Accepted Per-Task Phase 2 Summaries\n\n"
            f"{summary_source}\n",
        )
        prompt = (
            f"The supplied packet contains accepted scoped summaries and deterministic relationship "
            f"facts for group `{group}`. Write ONLY the two "
            "group-level markdown sections below, in this order, headed exactly as shown. Use only "
            "the supplied evidence. Preserve exact canonical unit names. Do not invent facts and do "
            "not restate the per-job sections.\n\n"
            f"`### {CROSS_JOB_FINDINGS_SECTION}`\n"
            "Shared systems, dependencies, repeated patterns, contradictions between jobs, "
            "cross-cutting risks, and end-to-end flows that span multiple jobs.\n\n"
            f"`### {GROUP_SUMMARY_SECTION}`\n"
            "A concise group-level index for Phase 3: units covered and units limited at a named evidence stage; top "
            "flows and entrypoints; external systems and runtime/config facts; business rules and "
            "transformations with evidence pointers; top risks, unknowns, and redacted vs missing vs "
            "distinctions among absent source, extraction, resolution, packet, and analysis gaps; "
            "deep evidence pointers to the assembled sections.\n\n"
            "Canonical units:\n"
            + "\n".join(f"- {name}" for name in group_units)
        )
        synthesis_opts = ModelOptions(
            max_tokens=min(config.phase2_synthesis_max_tokens, config.phase2_max_tokens),
            temperature=0.1,
            retries=1,
            timeout_s=opts.timeout_s,
            provider_options=opts.provider_options,
        )
        synthesis_packet = build_phase2_context_packet(
            staging_dir=ctx.staging_dir,
            facts=state.facts_manifest,
            group=group,
            task_id=f"{group}_synthesis",
            unit_names=group_units,
            task_evidence=synthesis_input,
            shared_context=shared_context,
            investigation_context=investigation_context,
            scoped_evidence=None,
            max_bytes=config.phase2_synthesis_context_bytes,
            max_scoped_artifact_bytes=config.phase2_filtered_map_bytes,
        )
        context_files = list(synthesis_packet.files)
        result = await self._complete_core_generation(
            ctx,
            state,
            provider=self.model_provider,
            phase="phase2.synthesis",
            target=f"{group}_synthesis",
            prompt=prompt,
            context_files=context_files,
            opts=synthesis_opts,
            log_cb=log_cb,
        )
        if not result.success:
            await log_cb(
                ctx.project_id,
                f"[custom] Phase 2 group synthesis failed for {group}; "
                f"using per-task summaries: {result.raw_error}",
                "warning",
            )
            return f"{assembled_analysis.rstrip()}\n\n{summary_fallback}\n" if summary_fallback else assembled_analysis
        synthesis = result.text.strip()
        if CROSS_JOB_FINDINGS_SECTION not in synthesis:
            synthesis = f"### {CROSS_JOB_FINDINGS_SECTION}\n\n{synthesis}"
        if GROUP_SUMMARY_SECTION not in synthesis and summary_fallback:
            synthesis = f"{synthesis}\n\n{summary_fallback}"
        await log_cb(ctx.project_id, f"[custom] Phase 2 group synthesis complete: {group}", "info")
        return f"{assembled_analysis.rstrip()}\n\n{synthesis}\n"

    async def _complete_phase2_sections(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        group: str,
        model_analysis: str,
        shared_context: list[Path],
        investigation_context: list[Path],
        group_file: Path,
        opts: ModelOptions,
        log_cb: LogCallback,
    ) -> str:
        """Normalize required guide headings without spending a repair LLM call.

        Contract-driven (recon S9): ``still_missing`` is computed against the
        active contract's required set, so a ``mode: replace`` pack whose
        declared sections are satisfied can never hit the raise below and be
        silently converted into a PARTIAL stub by
        ``_contain_phase2_group_failures``.

        The residual-miss handling is split by enforcement class:

        * ``hard_required_names`` (no-pack contract: the built-in platform set;
          pack contracts: the GROUP_SUMMARY floor) -- unchanged raise; the
          salvage machinery contains it exactly as today.
        * everything a pack declared for itself -- recorded via
          :meth:`_record_section_coverage_gap` and published as found. Routing
          these through the raise would let ``_contain_phase2_group_failures``
          replace real content with a PARTIAL stub while the run reports
          success -- the exact failure mode this split removes.
        """

        platform = state.facts_manifest.platform
        contract = self._contract_for(platform)
        patched, missing = deterministic_phase2_section_patch(
            model_analysis,
            platform=platform,
            group=group,
            contract=contract,
        )

        if missing:
            await log_cb(
                ctx.project_id,
                f"[custom] Phase 2 for {group} missing guide sections after first pass: "
                f"{', '.join(missing)}; applied deterministic structural patch",
                "warning",
            )
            still_missing = missing_required_analysis_sections(
                patched, platform, contract=contract
            )
            # ``hard_required_names`` is the whole required set for a no-pack run
            # (built-in Talend strictness, unchanged) and the protected floor
            # alone for any pack. So the built-in contract keeps exactly the
            # enforcement it always had, and nothing a pack declared for itself
            # can ever raise here.
            hard = set(contract.hard_required_names)
            hard_missing = [n for n in still_missing if n in hard]
            if hard_missing:
                raise RuntimeError(
                    f"Phase 2 analysis for group {group} missing required sections after deterministic patch: "
                    f"{', '.join(hard_missing)}"
                )

        # Record every declared section the output has no heading for -- not
        # just the required ones. The author's question is the unit of interest:
        # they need to know which of their questions came back empty, whether or
        # not they happened to mark it required.
        if contract.is_pack and contract.declared_sections:
            unanswered = [
                spec.id
                for spec in contract.declared_sections
                if f"### {spec.id}" not in patched
            ]
            if unanswered:
                await self._record_section_coverage_gap(
                    ctx, state, group, unanswered, contract, log_cb
                )
        return patched

    async def _phase2_analysis_direct(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        log_cb: LogCallback,
    ) -> None:
        """Lightweight Phase 2 for small projects.

        Runs one direct model call per group with the preprocess file fully inline.
        No AgentTaskRuntime, no tool loops. Uses the same prompt and validation
        infrastructure as the full path so the repair ladder and Phase 3 are
        unaffected.
        """
        preprocess_dir = ctx.staging_dir / "_preprocess"
        analysis_dir = ctx.staging_dir / "_analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        shared_context = self._phase2_shared_context(ctx, ctx.staging_dir / "facts_manifest.json")
        semaphore = asyncio.Semaphore(max(1, self._concurrency_for_phase("analysis", config.agent_phase2_concurrency)))

        async def _process_group(group: str) -> None:
            async with semaphore:
                await self._phase2_group_direct(
                    ctx, state, group, shared_context, analysis_dir, preprocess_dir, log_cb
                )

        gather_results = await asyncio.gather(
            *[_process_group(group) for group in ctx.preprocess_groups],
            return_exceptions=True,
        )
        await self._contain_phase2_group_failures(
            ctx, state, list(zip(ctx.preprocess_groups, gather_results)), log_cb
        )

    async def _phase2_analysis_dag_tiered(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        plan: ExecutionPlan,
        log_cb: LogCallback,
    ) -> None:
        """Planner-driven Phase 2: deterministic skeletons plus selected deep dives."""
        preprocess_dir = ctx.staging_dir / "_preprocess"
        analysis_dir = ctx.staging_dir / "_analysis"
        skeleton_dir = analysis_dir / "_skeleton"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        skeleton_dir.mkdir(parents=True, exist_ok=True)
        shared_context = self._phase2_shared_context(ctx, ctx.staging_dir / "facts_manifest.json")
        runtime = AgentTaskRuntime(ctx.staging_dir, state.facts_manifest)
        semaphore = asyncio.Semaphore(max(1, self._concurrency_for_phase("analysis", config.agent_phase2_concurrency)))

        async def _process_group(group: str) -> None:
            async with semaphore:
                group_plan = plan.groups.get(group)
                if group_plan is not None and group_plan.depth == "skip":
                    units = self._group_units(state, group)
                    target = analysis_dir / f"{group}.md"
                    target.write_text(_empty_group_stub(group, units), encoding="utf-8")
                    self._write_phase2_coverage_record(ctx, group, units, target, status="planner_skipped")
                    await log_cb(ctx.project_id, f"[custom] DAG Phase 2 skipped group {group}", "info")
                    return
                skeleton_path = skeleton_dir / f"{self._safe_name(group)}.json"
                skeleton_path.write_text(
                    json.dumps(self._phase2_skeleton_payload(ctx, state, group, group_plan), indent=2),
                    encoding="utf-8",
                )
                if group_plan is not None and group_plan.depth == "deep":
                    await self._phase2_group_agent(
                        ctx,
                        state,
                        runtime,
                        group,
                        shared_context,
                        analysis_dir,
                        preprocess_dir,
                        log_cb,
                        phase_name="phase2.deep_dive",
                    )
                else:
                    await self._phase2_group_direct(
                        ctx,
                        state,
                        group,
                        shared_context,
                        analysis_dir,
                        preprocess_dir,
                        log_cb,
                    )

        results = await asyncio.gather(
            *[_process_group(group) for group in ctx.preprocess_groups],
            return_exceptions=True,
        )
        await self._contain_phase2_group_failures(
            ctx, state, list(zip(ctx.preprocess_groups, results)), log_cb
        )

    def _phase2_skeleton_payload(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        group: str,
        group_plan,
    ) -> dict:
        group_file = ctx.staging_dir / "_preprocess" / f"{group}.md"
        text = group_file.read_text(encoding="utf-8", errors="replace") if group_file.exists() else ""
        categories = [
            cat for cat, pattern in CATEGORY_PATTERNS.items()
            if cat != "source_summary" and pattern.search(text)
        ]
        identifiers = _extract_chunk_identifiers(text)
        units = self._group_units(state, group)
        complexity = "high" if len(categories) >= 4 or len(units) > 10 or len(text) > 70_000 else "low"
        planned_depth = getattr(group_plan, "depth", "shallow") if group_plan is not None else "shallow"
        confidence = 0.8 if categories or len(text) >= config.min_investigation_content_bytes else 0.35
        return {
            "group": group,
            "units": units,
            "source_bytes": len(text.encode("utf-8")),
            "categories": categories,
            "identifiers": identifiers,
            "complexity": complexity,
            "planned_depth": planned_depth,
            "tool_budget": getattr(group_plan, "tool_budget", "none") if group_plan is not None else "none",
            "confidence": confidence,
            "reasons": getattr(group_plan, "reasons", []) if group_plan is not None else [],
            "deep_dive_recommended": complexity == "high" or confidence < 0.75,
        }

    async def _phase2_group_direct(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        group: str,
        shared_context: list[Path],
        analysis_dir: Path,
        preprocess_dir: Path,
        log_cb: LogCallback,
    ) -> None:
        """Single direct model call for one group (no tool loops)."""
        analysis_path = analysis_dir / f"{group}.md"
        # See _phase2_group_agent: reuse on resume OR scoped reingestion (unchanged
        # groups' analyses are restored by incremental staging).
        if (ctx.resume_state is not None or ctx.scoped_docs is not None) and analysis_path.exists() and analysis_path.stat().st_size > 500:
            if group_analysis_is_complete(
                analysis_path,
                state.facts_manifest.platform,
                contract=self._contract_for(state.facts_manifest.platform),
            ):
                await log_cb(ctx.project_id, f"[custom] REUSE: skipping Phase 2 for {group} (unchanged group; cached analysis)", "info")
                self._write_phase2_coverage_record(ctx, group, self._group_units(state, group), analysis_path)
                return
            await log_cb(ctx.project_id, f"[custom] REUSE: cached Phase 2 for {group} is incomplete, regenerating", "info")

        group_units = self._group_units(state, group)
        if not group_units:
            raise RuntimeError(
                "Phase 2 scanner contract mismatch: preprocess group "
                f"{group!r} has no canonical units in facts_manifest"
            )
        group_file = preprocess_dir / f"{group}.md"
        if not group_file.exists():
            raise RuntimeError(
                f"Phase 2 source group file missing: {group_file.relative_to(ctx.staging_dir).as_posix()}"
            )

        file_bytes = group_file.stat().st_size
        if file_bytes < config.min_investigation_content_bytes:
            analysis_path.write_text(_empty_group_stub(group, group_units), encoding="utf-8")
            self._write_phase2_coverage_record(ctx, group, group_units, analysis_path, status="packet_limited")
            await log_cb(
                ctx.project_id,
                f"[custom] Phase 2 direct: wrote evidence-packet-limited stub for {group}",
                "warning",
            )
            return

        # Standard generation has one Phase 2 evidence contract regardless of
        # whether the planner labels a project "small" or "enterprise".  This
        # avoids the historical cliff where small projects received one broad,
        # exhaustive prose request while larger projects used bounded tasks.
        # Explicit bounded plugin packs keep their authored Phase 2 contract.
        pack_phase2_override = (
            self.plugin_pack_dir / "prompts" / "phase2.md"
            if self.plugin_pack_dir and self._phase_policy("analysis") == "bounded"
            else None
        )
        if not (pack_phase2_override and pack_phase2_override.exists()):
            await self._phase2_group_agent(
                ctx,
                state,
                AgentTaskRuntime(ctx.staging_dir, state.facts_manifest),
                group,
                shared_context,
                analysis_dir,
                preprocess_dir,
                log_cb,
            )
            return

        # The direct path is an optimization, not a separate evidence contract.
        # If one group no longer fits a single bounded source packet, promote it
        # to the same unit-boundary planner used by enterprise/composite scans.
        if file_bytes > config.phase2_task_evidence_bytes:
            await log_cb(
                ctx.project_id,
                f"[custom] Phase 2 direct: promoting {group} to bounded unit tasks "
                f"({file_bytes} source bytes)",
                "info",
            )
            await self._phase2_group_agent(
                ctx,
                state,
                AgentTaskRuntime(ctx.staging_dir, state.facts_manifest),
                group,
                shared_context,
                analysis_dir,
                preprocess_dir,
                log_cb,
            )
            return

        opts = ModelOptions(
            max_tokens=min(config.phase2_max_tokens, config.phase2_task_max_tokens),
            temperature=0.1,
            retries=1,
            timeout_s=300.0,
            provider_options=self._provider_options(self.model_provider),
        )

        # bounded mode with a pack phase2.md: use it as the COMPLETE prompt.
        # Pack authors write exactly what they want analysed — no mandatory enterprise
        # section structure appended. Unit names and facts summary are always appended
        # so the model knows what it is covering.
        if pack_phase2_override and pack_phase2_override.exists():
            unit_list = "\n".join(f"- {u}" for u in group_units)
            prompt = (
                f"{pack_phase2_override.read_text(encoding='utf-8').rstrip()}\n\n"
                f"Group: {group}\n"
                f"Canonical units to cover:\n{unit_list}\n\n"
                f"Run facts:\n{facts_summary(state.facts_manifest)}\n"
            )
            await log_cb(
                ctx.project_id,
                f"[custom] Phase 2 bounded: using pack prompt override for {group}",
                "info",
            )
        else:
            prompt = group_analysis_prompt(
                state.facts_manifest, group,
                pack=self.pack,
                plugin_pack_dir=self.plugin_pack_dir,
                required_docs=self._required_docs,
                contract=self._contract_for(state.facts_manifest.platform),
            )

        investigation_context = self._investigation_context(ctx, group)
        evidence_slice = EvidenceRegistryBuilder(ctx.staging_dir).build_phase2_slice(
            f"direct_{self._safe_name(group)}", group_units
        )
        evidence_brief = ctx.staging_dir / "_evidence_registry" / "evidence_brief.md"
        packet = build_phase2_context_packet(
            staging_dir=ctx.staging_dir,
            facts=state.facts_manifest,
            group=group,
            task_id=f"direct_{group}",
            unit_names=group_units,
            task_evidence=group_file,
            shared_context=[path for path in shared_context if path != evidence_brief],
            investigation_context=investigation_context,
            scoped_evidence=evidence_slice,
            scan_output_dir=config.intodoc_output_dir / ctx.project_id,
            max_bytes=config.phase2_task_context_bytes,
            max_scoped_artifact_bytes=config.phase2_filtered_map_bytes,
        )
        context_files = list(packet.files)

        result = await self._complete_core_generation(
            ctx,
            state,
            provider=self.model_provider,
            phase="phase2",
            target=group,
            prompt=prompt,
            context_files=context_files,
            opts=opts,
            log_cb=log_cb,
        )
        if not result.success:
            raise RuntimeError(f"Phase 2 failed for {group}: {result.raw_error}")

        text = await self._complete_phase2_sections(
            ctx, state, group,
            result.text.strip(),
            shared_context, [], group_file, opts, log_cb,
        )
        validation = self._validate_phase2_task_output(
            text,
            group_units,
            platform=state.facts_manifest.platform,
            require_sections=True,
            contract=self._contract_for(state.facts_manifest.platform),
        )
        if not validation["ok"]:
            raise RuntimeError(
                f"Phase 2 direct output for {group} failed validation: "
                + "; ".join(validation["errors"][:10])
            )
        analysis_path.write_text(text, encoding="utf-8")
        self._write_phase2_coverage_record(ctx, group, group_units, analysis_path)
        await log_cb(ctx.project_id, f"[custom] Phase 2 direct complete: {group}", "info")

    async def _phase2_targeted_repair(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        report,
        log_cb: LogCallback,
    ) -> None:
        errors = report.errors[:10]
        missing_units = report.missing_units[:50]
        if not errors and not missing_units:
            return
        repair_causes = self._repair_root_causes(errors, missing_units) if config.agent_smart_repair_enabled else []
        if repair_causes:
            debug_write(
                debug_dir(ctx.staging_dir, "_assessment", "repair", "phase2_root_causes.json"),
                json.dumps(repair_causes, indent=2),
            )
        await log_cb(
            ctx.project_id,
            "[custom] Phase 2 targeted repair starting: "
            + ("; ".join(errors[:3]) or f"missing units: {', '.join(missing_units[:10])}"),
            "warning",
        )

        units_by_group: dict[str, list[str]] = {}
        if missing_units:
            for unit in state.facts_manifest.units:
                if unit.name in missing_units:
                    units_by_group.setdefault(unit.group, []).append(unit.name)
        else:
            # No specific missing units (e.g. guide-sections failure): extract the
            # affected group names from the error strings instead of repairing every group.
            error_groups = _groups_from_errors(errors, set(state.facts_manifest.groups))
            targets = error_groups if error_groups else set(state.facts_manifest.groups)
            for group in targets:
                units_by_group[group] = self._group_units(state, group)

        runtime = AgentTaskRuntime(ctx.staging_dir, state.facts_manifest)
        semaphore = asyncio.Semaphore(max(1, self._concurrency_for_phase("repair", config.agent_repair_concurrency)))

        async def _repair_group(group: str, units: list[str]) -> None:
            async with semaphore:
                target = ctx.staging_dir / "_analysis" / f"{group}.md"

                # If the group packet has no meaningful content, write a packet-limited
                # stub rather than dispatching a repair that would hallucinate.
                preprocess_file = ctx.staging_dir / "_preprocess" / f"{group}.md"
                preprocess_size = preprocess_file.stat().st_size if preprocess_file.exists() else 0
                if preprocess_size < config.min_investigation_content_bytes:
                    target.write_text(_empty_group_stub(group, units), encoding="utf-8")
                    self._write_phase2_coverage_record(ctx, group, units, target, status="packet_limited")
                    await log_cb(
                        ctx.project_id,
                        f"[custom] Phase 2 repair: wrote evidence-packet-limited stub for {group}",
                        "warning",
                    )
                    return

                context_files = [
                    p for p in [
                        ctx.staging_dir / "facts_manifest.json",
                        target,
                        ctx.staging_dir / "_investigation" / f"{self._safe_name(group)}.md",
                        ctx.staging_dir / "_investigation" / "_summary.md",
                        ctx.staging_dir / "_preprocess" / f"{group}.md",
                        ctx.staging_dir / "engineering_facts.md",
                        ctx.staging_dir / "known_risks.md",
                        ctx.staging_dir / "dependency_map.md",
                    ]
                    if p.exists()
                ]
                # Only surface errors that relate to this specific group; don't inject
                # another group's failures into this repair context.
                group_errors = [e for e in errors if group in e] or errors[:3]
                group_causes = [
                    cause for cause in repair_causes
                    if not cause.get("scope") or cause.get("scope") in {group, "global"}
                ]
                prompt = (
                    f"Repair only the listed Phase 2 analysis gaps for group `{group}`.\n\n"
                    "Rules:\n"
                    "- Write markdown sections that can be appended under `## Model Analysis`.\n"
                    "- Preserve exact canonical unit names.\n"
                    "- Use only supplied scanner, investigation, and EngineeringFacts evidence.\n"
                    "- If evidence is unavailable, name the evidenced failure stage. A packet omission is "
                    "not proof of a scanner or source gap.\n\n"
                    "Validator errors:\n"
                    + ("\n".join(f"- {error}" for error in group_errors) or "- None")
                    + "\n\nRoot-cause classification:\n"
                    + ("\n".join(f"- {cause['category']}: {cause['reason']}" for cause in group_causes) or "- None")
                    + "\n\nUnits to cover:\n"
                    + "\n".join(f"- {unit}" for unit in units)
                )
                context_files, repair_estimate = self._narrow_repair_context(prompt, context_files)
                if not context_files:
                    await log_cb(
                        ctx.project_id,
                        f"[custom] Phase 2 targeted repair skipped for {group}: no grounded context files",
                        "warning",
                    )
                    return
                if repair_estimate > REPAIR_TOKEN_CAP:
                    await log_cb(
                        ctx.project_id,
                        f"[custom] Phase 2 targeted repair skipped for {group}: "
                        f"estimated tokens {repair_estimate} exceed cap {REPAIR_TOKEN_CAP}",
                        "warning",
                    )
                    return
                opts = ModelOptions(
                    max_tokens=min(config.phase3_max_tokens, 12_000),
                    temperature=0.1,
                    retries=1,
                    timeout_s=240.0,
                    provider_options=self._provider_options(self.model_provider),
                )
                contract = AgentTaskContract(
                    phase="repair",
                    target_id=f"{group}_phase2_repair",
                    allowed_units=units,
                    allowed_groups=[group],
                    allowed_categories=self._default_evidence_categories(),
                    starter_context_files=[p.relative_to(ctx.staging_dir).as_posix() for p in context_files if p.exists()],
                    tool_budget=default_tool_budget(),
                    input_budget=default_input_budget(),
                    expected_artifact="repair markdown",
                    max_attempts=config.agent_max_repair_attempts,
                    validation_gaps=group_errors,
                )

                async def _dispatch(task: AgentTaskContract, tools: ScopedEvidenceTools) -> ModelResult:
                    return await self._complete_core_generation(
                        ctx,
                        state,
                        provider=self.model_provider,
                        phase="phase2.repair",
                        target=task.target_id,
                        prompt=prompt,
                        context_files=context_files,
                        opts=opts,
                        log_cb=log_cb,
                        task_contract=task,
                        scoped_tools=tools,
                    )

                task_result = await runtime.run(
                    contract,
                    _dispatch,
                    lambda text: self._validate_agent_markdown(text, units, min_bytes=80),
                )
                if not task_result.result.success:
                    await log_cb(ctx.project_id, f"[custom] Phase 2 targeted repair failed for {group}: {task_result.result.raw_error}", "error")
                    return
                existing = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
                coverage = "\n".join(f"- COVERED: {unit}" for unit in units)
                # Normalise headings before writing so variant forms (e.g. '### GROUP_SUMMARY: demo-data')
                # are converted to canonical form and don't trigger the guide-sections gate again.
                repair_text = normalize_analysis_guide_headings(
                    task_result.result.text.strip(),
                    state.facts_manifest.platform,
                    contract=self._contract_for(state.facts_manifest.platform),
                )
                target.write_text(
                    f"{existing.rstrip()}\n\n## Targeted Validator Repair\n\n{coverage}\n\n{repair_text}\n",
                    encoding="utf-8",
                )
                self._write_phase2_coverage_record(ctx, group, units, target)
                await log_cb(ctx.project_id, f"[custom] Phase 2 targeted repair appended to {target.name}", "info")

        await asyncio.gather(*[_repair_group(group, units) for group, units in units_by_group.items()])

    @staticmethod
    def _write_omitted_scorecard_marker(ctx: DocGenContext, state: HarnessRunState) -> None:
        """Write the deterministic marker replacing the disabled LLM scorecard (R2.2).

        Pure Python (no LLM, no invented scores): states canonical inventory
        facts and instructs downstream doc writers to mark scorecard-derived
        dimensions as not computed. Satisfies the enterprise doc-set validator
        (which requires a non-trivial ``_assessment/scorecard.md``) and feeds
        the 07 doc / executive summary context so they degrade explicitly
        instead of fabricating scores.
        """
        assessment_dir = ctx.staging_dir / "_assessment"
        assessment_dir.mkdir(parents=True, exist_ok=True)
        (assessment_dir / "scorecard.md").write_text(
            "# Assessment Scorecard\n\n"
            f"Canonical inventory: {state.facts_manifest.unit_count} "
            f"{state.facts_manifest.canonical_unit_label} across "
            f"{len(state.facts_manifest.groups)} groups.\n\n"
            "> **SCORECARD OMITTED:** no quality or risk scores were computed "
            "for this run. There are no dimension scores, ratings or rankings "
            "to cite.\n\n"
            "Writers: present risk and quality findings directly from the "
            "evidence you already have (RISK_FLAGS claims, engineering fact "
            "maps, known_risks) and mark scorecard-derived dimensions as not "
            "computed rather than inventing scores.\n\n"
            "Do not copy this notice, or any part of it, into a client "
            "document. State the absence in your own words, in client "
            "language.\n",
            encoding="utf-8",
        )

    async def _reduce_split_scorecard(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        slice_text: str,
        scorecard_pack: Path,
        log_cb: LogCallback,
    ) -> ModelResult:
        slice_path = transient_dir(ctx.staging_dir, "scorecard_slice_outputs.md")
        transient_write(slice_path, slice_text.strip() + "\n")
        prompt = (
            "Reduce the supplied Phase 2.6 scorecard slice outputs into one coherent global "
            "assessment scorecard. Preserve concrete evidence, exact unit names, anti-pattern IDs, "
            "red-flag IDs, and security checklist findings. Resolve duplicate or conflicting "
            "slice-level ratings into one global rating per dimension using the scoring rubric. "
            "Do not invent evidence. Write markdown only.\n\n"
            "Required final structure:\n"
            "1. Overall score with one-sentence justification\n"
            "2. Dimension table with architecture, data lineage, business logic, operations, "
            "security, performance, standards, migration\n"
            "3. Top risks ranked by severity\n"
            "4. Top strengths\n"
            "5. Anti-pattern findings\n"
            "6. Red flag findings\n"
            "7. Security checklist\n\n"
            f"Canonical inventory: {state.facts_manifest.unit_count} "
            f"{state.facts_manifest.canonical_unit_label}.\n"
        )
        context_files = [ctx.staging_dir / "facts_manifest.json", slice_path]
        if scorecard_pack.exists():
            context_files.append(scorecard_pack)
        else:
            context_files.extend(self._existing(ctx.staging_dir / "_preprocess", [
                "_scoring_rubric.md",
                "_anti_patterns.md",
                "_red_flags.md",
                "_security_checklist.md",
                "_pattern_library.md",
            ]))
        opts = ModelOptions(
            max_tokens=config.phase2_max_tokens,
            temperature=0.1,
            retries=1,
            timeout_s=300.0,
            provider_options=self._provider_options(self.model_provider),
        )
        await log_cb(ctx.project_id, "[custom] Phase 2.6 scorecard reducer starting", "info")
        result = await self._complete_core_generation(
            ctx,
            state,
            provider=self.model_provider,
            phase="phase2.6.reduce",
            target="scorecard",
            prompt=prompt,
            context_files=context_files,
            opts=opts,
            log_cb=log_cb,
        )
        if result.success:
            await log_cb(ctx.project_id, "[custom] Phase 2.6 scorecard reducer complete", "info")
        return result

    def _phase28_bundles(self, ctx: DocGenContext, facts) -> None:
        analysis_dir = ctx.staging_dir / "_analysis"
        investigation_text = "\n\n".join(
            p.read_text(encoding="utf-8")
            for p in sorted((ctx.staging_dir / "_investigation").glob("*.md"))
        )
        analysis_files = [
            p for p in sorted(analysis_dir.glob("*.md"))
            if not p.name.startswith("_") and "_chunk_" not in p.name
        ]
        manifest: dict[str, object] = {"source_analyses": [p.name for p in analysis_files], "doc_packs": {}}
        for doc_name in self._required_docs:
            section_names = self._bundle_sections_for_docs([doc_name])
            pack_text, pack_manifest = self._doc_evidence_pack_text(
                ctx,
                doc_name,
                analysis_files,
                section_names,
                investigation_text,
                facts,
            )
            pack_path = analysis_dir / f"_doc_pack_{doc_name}.md"
            pack_path.write_text(pack_text, encoding="utf-8")
            manifest["doc_packs"][doc_name] = {"path": pack_path.name, **pack_manifest}
        (analysis_dir / "_doc_pack_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def _write_documentation_obligations(self, ctx: DocGenContext, facts) -> None:
        obligation_map = build_documentation_obligation_map(facts, required_docs=self._required_docs)
        path = ctx.staging_dir / "documentation_obligations.json"
        path.write_text(json.dumps(obligation_map, indent=2), encoding="utf-8")

    def _bundle_sections_for_docs(self, doc_names: list[str]) -> set[str]:
        sections = set(BASELINE_BUNDLE_SECTIONS)
        # mode: replace packs route doc bundles exclusively through their
        # declared docs[].phase2_sections: the legacy evidence-profile ->
        # section map names sections that do not exist under the replaced
        # contract and would degrade every doc pack to an UNCLASSIFIED dump
        # (recon S13/S14).
        if self._pack_contract is not None and self._pack_contract.mode == "replace":
            for doc_name in doc_names:
                sections.update(self._doc_section_bundle_map.get(doc_name, set()))
            return sections
        for doc_name in doc_names:
            doc_def = self._pack_doc_definition(doc_name)
            profile_name = getattr(doc_def, "evidence_profile", None) if doc_def else None
            if profile_name and self._plugin_manifest:
                profiles = getattr(self._plugin_manifest, "evidence_profiles", {}) or {}
                profile = profiles.get(profile_name)
                if profile:
                    for cat in getattr(profile, "include", []):
                        sections.update(_BUNDLE_CATEGORY_TO_PHASE2_SECTIONS.get(cat, set()))
                    for cat in getattr(profile, "exclude", []):
                        sections.difference_update(_BUNDLE_CATEGORY_TO_PHASE2_SECTIONS.get(cat, set()))
                    continue
            # Fallback: catalog-defined routing
            sections.update(self._doc_section_bundle_map.get(doc_name, set()))
        return sections

    def _doc_evidence_pack_text(
        self,
        ctx: DocGenContext,
        doc_name: str,
        analysis_files: list[Path],
        section_names: set[str],
        investigation_text: str,
        facts,
    ) -> tuple[str, dict[str, object]]:
        _guide_file = ctx.staging_dir / "_preprocess" / ctx.analysis_guide_file if ctx.analysis_guide_file else None
        _extra = _guide_section_names(_guide_file) if _guide_file else frozenset()
        selected, manifest = self._targeted_analysis_bundle_parts(
            analysis_files, section_names, _extra, contract=self._contract_for(facts.platform)
        )
        included_obligations, unmatched_obligations, obligation_text = self._doc_obligation_context(facts, doc_name)
        groups = ", ".join(f"{group}={count}" for group, count in sorted(facts.groups.items())) or "none"
        units = "\n".join(
            f"- {unit.name} | group={unit.group} | path={unit.path or 'source path unavailable'} | "
            f"type={unit.unit_type} | systems={', '.join(unit.key_systems) or 'none'}"
            for unit in facts.units
        )
        systems = "\n".join(f"- {system}" for system in facts.key_systems) or "- None identified in scanner facts"
        source_coverage = ctx.staging_dir / "_preprocess" / "_source_coverage.json"
        coverage_note = source_coverage.relative_to(ctx.staging_dir).as_posix() if source_coverage.exists() else "No direct source coverage file"
        pack = (
            f"# Document Evidence Pack: {doc_name}\n\n"
            "This evidence pack is your PRIMARY scoped source for writing this document. It is not "
            "proof that every retained source fact is present. "
            "Write directly from the evidence below. Do NOT call tools to re-read analysis, source "
            "files, or artifacts that are already included in this pack — that content is here. "
            "Use a tool only to fill a specific, named gap you genuinely cannot answer from this pack.\n\n"
            "## Shared Project Scope\n\n"
            f"- Platform: {facts.platform}\n"
            f"- Canonical inventory: {facts.unit_count} {facts.canonical_unit_label}\n"
            f"- Groups: {groups}\n"
            f"- Source coverage: {coverage_note}\n\n"
            "## All Groups And Units\n\n"
            f"{units or '- No canonical units found'}\n\n"
            "## Cross-Group Reference Index\n\n"
            "Use the CROSS_GROUP_REFS excerpts below; open the full Phase 2 source files only for drill-down.\n\n"
            "## External Systems Summary\n\n"
            f"{systems}\n\n"
            "## Top Risks Summary\n\n"
            "Use the RISK_FLAGS excerpts below and `_assessment/scorecard.md` for ranked risk and quality scoring evidence.\n\n"
            "## Source Coverage And Scanner Limits\n\n"
            "Covered source files are evidence of record. Redacted values are intentionally hidden secrets, not missing files. "
            "Scanner-limited claims must name the limited artifact or section.\n\n"
            "## Documentation Obligations\n\n"
            f"{obligation_text}\n\n"
            "## Deep Evidence Pointers\n\n"
            + "\n".join(f"- `_analysis/{path.name}` full Phase 2 detail" for path in analysis_files)
            + "\n- `_assessment/scorecard.md` quantitative risk and quality scoring\n"
            "\n## Investigation Evidence Summaries\n\n"
            f"{investigation_text or 'No investigation summaries were written.'}\n\n"
            "## Doc-Specific Phase 2 Evidence\n\n"
            f"Included sections: {', '.join(sorted(section_names))}.\n\n"
            f"{selected}\n"
        )
        manifest["included_obligations"] = included_obligations
        manifest["unmatched_obligations"] = unmatched_obligations
        manifest["evidence_sources"] = [
            *[f"_analysis/{path.name}" for path in analysis_files],
            "_investigation/*.md",
            "_assessment/scorecard.md",
            "facts_manifest.json",
            "documentation_obligations.json",
        ]
        return pack, manifest

    def _doc_obligation_context(self, facts, doc_name: str) -> tuple[list[dict[str, object]], list[dict[str, object]], str]:
        obligation_map = build_documentation_obligation_map(facts, required_docs=self._required_docs)
        doc_targets = obligation_map.get("doc_targets", {}) if isinstance(obligation_map, dict) else {}
        included: list[dict[str, object]] = []
        unmatched: list[dict[str, object]] = []

        for category, terms in obligation_map.get("shared", {}).items():
            targets = doc_targets.get(category, self._required_docs)
            item = {
                "scope": "shared",
                "category": category,
                "terms": terms,
                "doc_targets": targets,
            }
            if doc_name in targets:
                included.append(item)
            else:
                unmatched.append(item)

        for category, terms in obligation_map.get("platform_specific", {}).items():
            targets = doc_targets.get(f"platform_specific.{category}", self._required_docs)
            item = {
                "scope": "platform_specific",
                "category": category,
                "terms": terms,
                "doc_targets": targets,
            }
            if doc_name in targets:
                included.append(item)
            else:
                unmatched.append(item)

        unit_targets = doc_targets.get("units", ["02_COMPONENT_INVENTORY", "00_AGENT_BRIEF", "08_CHANGE_PLAYBOOK"])
        unit_obligation = {
            "scope": "units",
            "category": "canonical_inventory",
            "terms": ["name", "group", "source artifact", "type", "key systems"],
            "doc_targets": unit_targets,
        }
        if doc_name in unit_targets:
            included.append(unit_obligation)
        else:
            unmatched.append(unit_obligation)

        lines = ["Included obligations for this document:"]
        lines.extend(
            f"- {item['scope']}.{item['category']}: {', '.join(str(term) for term in item['terms'])}"
            for item in included
        )
        if not included:
            lines.append("- None directly mapped; use shared project scope only.")
        lines.append("\nObligations mapped to other documents:")
        lines.extend(
            f"- {item['scope']}.{item['category']} -> {', '.join(str(doc) for doc in item['doc_targets'])}"
            for item in unmatched
        )
        return included, unmatched, "\n".join(lines)

    @classmethod
    def _targeted_analysis_bundle_text(
        cls,
        analysis_files: list[Path],
        section_names: set[str],
        extra_section_names: frozenset[str] = frozenset(),
        contract: "PackContract | None" = None,
    ) -> str:
        text, _ = cls._targeted_analysis_bundle_parts(
            analysis_files, section_names, extra_section_names, contract=contract
        )
        return text

    @classmethod
    def _targeted_analysis_bundle_parts(
        cls,
        analysis_files: list[Path],
        section_names: set[str],
        extra_section_names: frozenset[str] = frozenset(),
        contract: "PackContract | None" = None,
    ) -> tuple[str, dict[str, object]]:
        """Select the requested sections from each Phase 2 analysis file.

        Contract-driven (recon S14): iterates the contract's own ordered
        section universe, not the module constant, so pack-declared sections
        can be selected; without this every pack doc pack degraded to an
        unstructured UNCLASSIFIED dump.
        """
        ordered = contract.ordered_names if contract is not None else PHASE2_SECTION_NAMES
        ordered = tuple(ordered) + tuple(
            name for name in sorted(section_names) if name not in ordered
        )
        parts: list[str] = []
        manifest: dict[str, object] = {"included": {}, "omitted": {}, "unclassified": {}}
        for path in analysis_files:
            text = path.read_text(encoding="utf-8", errors="replace")
            sections = cls._parse_phase2_sections(text, extra_section_names, contract=contract)
            selected = [
                (section, sections[section])
                for section in ordered
                if section in section_names and sections.get(section)
            ]
            if not selected:
                selected = [(ADDITIONAL_FINDINGS_SECTION, text.strip())]
            selected_names = {name for name, _ in selected}
            manifest["included"][path.name] = [name for name, _ in selected]
            manifest["omitted"][path.name] = sorted(name for name in sections if name not in selected_names)
            if sections.get(ADDITIONAL_FINDINGS_SECTION):
                manifest["unclassified"][path.name] = True
            parts.append(f"### Source Analysis: {path.name}")
            for section, body in selected:
                parts.append(f"#### {section}\n\n{body.strip()}")
        return "\n\n".join(parts).rstrip(), manifest

    async def _apply_scoped_docs(self, ctx: DocGenContext, log_cb: LogCallback) -> None:
        """Restrict Phase 3 to the scoped doc subset; preserve the rest.

        For doc-targeted reingestion (Slice 3) only the docs in
        ``ctx.scoped_docs`` are regenerated. Every other doc in the required set
        is copied byte-for-byte from the prior published baseline (``docs/``)
        into the staging tree so the atomic swap preserves it unchanged. A
        scoped doc that has no baseline copy (e.g. a newly added catalog doc) is
        still regenerated. Phase 2 cache reuse for unchanged groups is
        unaffected (handled by resume state).
        """
        scoped = ctx.scoped_docs or set()
        baseline_docs_dir = config.project_root / "output" / ctx.project_id / "docs"
        preserved: list[str] = []
        regenerated: list[str] = []
        for doc in self._required_docs:
            if doc in scoped:
                regenerated.append(doc)
                continue
            baseline = baseline_docs_dir / f"{doc}.md"
            staged = ctx.staging_dir / f"{doc}.md"
            if baseline.is_file():
                shutil.copy2(baseline, staged)
                preserved.append(doc)
            else:
                # No prior baseline copy -> cannot preserve; regenerate to stay
                # safe (never silently drop a required doc).
                regenerated.append(doc)
        self._required_docs = [d for d in self._required_docs if d in regenerated]
        await log_cb(
            ctx.project_id,
            f"[custom] scoped Phase 3: regenerating {len(regenerated)} doc(s), "
            f"preserving {len(preserved)} from baseline",
            "info",
        )

    async def _phase3_docs(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        log_cb: LogCallback,
    ) -> None:
        scorecard = ctx.staging_dir / "_assessment" / "scorecard.md"
        # Whole-partition deterministic facts shared by every doc writer (and
        # every slice of a partitioned doc) so no two outputs can disagree on
        # scanner-derived counts.
        self._write_cross_cutting_facts(ctx, state)
        # Runtime evidence is produced by `_ensure_runtime_evidence`, called from
        # the Phase 3 phase object (`pipeline/phases/phase3_docs.py`) so both
        # engines go through one producer. It is idempotent per run, so this
        # driver does not repeat it.
        _doc_write_provider = self._provider_for_phase("doc_write")

        _calls_lock = asyncio.Lock()
        runtime = AgentTaskRuntime(ctx.staging_dir, state.facts_manifest)

        async def _write_one(doc_name: str) -> None:
            """Write one document. Salvage-first: NEVER raises for a per-doc
            failure -- a failed/rejected doc becomes a stub (or a truncated
            accepted doc on an output-cap hit), an ``_assessment`` record, and
            a resume marker (the task's non-accepted status), and the run
            continues. One bad document must not sink its accepted siblings.
            """
            doc_prompt_text = doc_prompt(
                state.facts_manifest,
                doc_name,
                pack=self.pack,
                plugin_pack_dir=self.plugin_pack_dir,
                extra_section_requirements=self._doc_section_requirements_extra,
                scoped_unit=self._per_job_doc_units.get(doc_name),
                nexus_presentation=ctx.nexus_presentation,
                unit_reference=(
                    doc_name in self._per_job_doc_units and self._plugin_manifest is None
                ),
                # Manifest ``audience:`` -> Phase 3 presentation (spec item 8).
                # ``ctx.nexus_presentation`` (the Style layer) still wins inside
                # ``doc_prompt``; the contract supplies the pack-owned default.
                contract=self.pack_contract,
            )
            opts = ModelOptions(
                max_tokens=self._doc_output_ceiling(doc_name, state),
                temperature=0.1,
                retries=1,
                timeout_s=300.0,
                provider_options=self._provider_options(_doc_write_provider),
            )
            context_files = self._phase3_context_files(ctx, doc_name)
            if scorecard.exists() and not self._pack_contract_mode:
                context_files.append(scorecard)
            contract = AgentTaskContract(
                phase="phase3_doc",
                target_id=doc_name,
                allowed_units=[unit.name for unit in state.facts_manifest.units],
                allowed_groups=list(state.facts_manifest.groups),
                allowed_categories=self._default_evidence_categories(),
                starter_context_files=[p.relative_to(ctx.staging_dir).as_posix() for p in context_files if p.exists()],
                tool_budget=default_tool_budget(),
                input_budget=default_input_budget(),
                expected_artifact="document markdown",
                max_attempts=config.agent_max_task_attempts,
            )
            cached = runtime.accepted_output(contract) if ctx.resume_state is not None else None
            if cached is not None:
                result = ModelResult(success=True, text=cached, model_name=_doc_write_provider.model_name)
                await log_cb(ctx.project_id, f"[custom] RESUME: skipping Phase 3 doc task {doc_name} (accepted)", "info")
            else:
                async def _dispatch(task: AgentTaskContract, tools: ScopedEvidenceTools) -> ModelResult:
                    return await self._complete_core_generation(
                        ctx,
                        state,
                        provider=_doc_write_provider,
                        phase="phase3",
                        target=task.target_id,
                        prompt=doc_prompt_text,
                        context_files=context_files,
                        opts=opts,
                        log_cb=log_cb,
                        calls_lock=_calls_lock,
                        task_contract=task,
                        scoped_tools=tools,
                        # Output-sized planning (slicing redesign). Without these
                        # inputs `_complete_core_generation` cannot size the plan
                        # by expected output and every over-ceiling document falls
                        # back to the R2.5 input-partitioned path, where each slice
                        # receives the byte-stable whole-document prompt (mandatory
                        # headings, "a response missing any heading is REJECTED")
                        # and writes the WHOLE document from its evidence chunk.
                        # That is the verified duplication driver: N restarts, then
                        # a lossy dedupe merge. This is the ONLY phase3 doc-write
                        # call site, so it is the only place the plan can be seeded.
                        phase3_inputs=self._phase3_plan_inputs(state, doc_name),
                    )

                task_result = await runtime.run(
                    contract,
                    _dispatch,
                    lambda text, doc=doc_name: self._validate_phase3_doc(text, doc, state),
                )
                result = task_result.result
            if not result.success and self._is_fatal_provider_failure(result):
                # Credit/credential failure: every remaining document would
                # fail identically and be stubbed. Abort instead -- a stubbed
                # "complete" document set is a worse artifact than no set.
                await log_cb(
                    ctx.project_id,
                    f"[custom] Phase 3 doc {doc_name} failed with a provider "
                    f"credential/billing error: {result.raw_error}; ABORTING the "
                    "run -- every remaining document would fail identically and "
                    "be published as a stub. Fix the provider account and "
                    "resume; already-accepted documents stay in staging.",
                    "error",
                )
                raise FatalProviderError(
                    f"provider credential/billing failure while writing {doc_name}: "
                    f"{result.raw_error}"
                )
            if not result.success:
                self._salvage_failed_doc(ctx, state, doc_name, result)
                await log_cb(
                    ctx.project_id,
                    f"[custom] Phase 3 doc {doc_name} did not complete "
                    f"({(result.metadata or {}).get('incomplete_reason') or 'failed'}): "
                    f"{result.raw_error}; salvage disposition="
                    f"{state.failed_docs[-1]['disposition']} -- run continues",
                    "warning",
                )
                return
            # Fast-fail near-empty output before expensive validators run on it.
            quick = self._validate_agent_markdown(result.text, [], min_bytes=150)
            if not quick["ok"]:
                doc_key = f"phase3/{doc_name}"
                state.empty_output_counts[doc_key] = state.empty_output_counts.get(doc_key, 0) + 1
                state.total_empty_outputs += 1
                await log_cb(
                    ctx.project_id,
                    f"[custom] Phase 3 near-empty output for {doc_name}: {'; '.join(quick['errors'])}",
                    "warning",
                )
            content = self._final_doc_content(state, doc_name, result.text)
            (ctx.staging_dir / f"{doc_name}.md").write_text(content, encoding="utf-8")
            await log_cb(ctx.project_id, f"[custom] Phase 3 doc complete: {doc_name}", "info")

        async def _run_batches(doc_names: list[str]) -> None:
            i = 0
            while i < len(doc_names):
                batch_size = 1 if state.adaptive_serial_generation else max(1, self._concurrency_for_phase("doc_write", config.agent_phase3_concurrency))
                batch = doc_names[i : i + batch_size]
                batch_results = await asyncio.gather(
                    *[_write_one(d) for d in batch], return_exceptions=True,
                )
                for doc_name, res in zip(batch, batch_results):
                    if isinstance(res, BaseException):
                        # `return_exceptions=True` turns every raise inside the
                        # batch into a value, so an abort MUST be re-raised here
                        # explicitly or it is silently downgraded to a stub --
                        # exactly the failure mode this guard exists to stop.
                        if isinstance(res, FatalProviderError):
                            raise res
                        # Unexpected exception (not a provider failure): same
                        # salvage path -- stub + record + continue.
                        await log_cb(
                            ctx.project_id,
                            f"[custom] Phase 3 doc write crashed for {doc_name}: {res} -- "
                            "writing stub, run continues",
                            "error",
                        )
                        self._salvage_failed_doc(
                            ctx, state, doc_name,
                            ModelResult(success=False, raw_error=str(res)),
                        )
                i += batch_size

        # Executive summary must be dispatched last — its context is the other completed docs.
        executive_summary_doc = "09_EXECUTIVE_SUMMARY"
        docs_without_summary = [d for d in self._required_docs if d != executive_summary_doc]
        has_executive_summary = executive_summary_doc in self._required_docs

        # Units-first wiring (P1.6): per-unit dossiers are written first; the
        # catalog/summary docs then synthesize from the finished unit docs (a
        # bounded deterministic digest) plus the analysis layer.
        unit_docs = [d for d in docs_without_summary if d in self._per_job_doc_units]
        group_docs = [d for d in docs_without_summary if d not in self._per_job_doc_units]

        if unit_docs:
            await _run_batches(unit_docs)
            digest_path = self._build_unit_docs_digest(ctx, unit_docs)
            if digest_path is not None:
                await log_cb(
                    ctx.project_id,
                    f"[custom] unit-docs digest written for summary synthesis: "
                    f"{digest_path.relative_to(ctx.staging_dir).as_posix()} "
                    f"({digest_path.stat().st_size} bytes)",
                    "info",
                )
        await _run_batches(group_docs)

        # Write executive summary last, after all other docs are complete.
        if has_executive_summary and not self._plugin_mode:
            await _run_batches([executive_summary_doc])

        if state.failed_docs:
            # Already written incrementally by `_salvage_failed_doc`; rewritten
            # here so the final artifact is complete even if a caller appended
            # to `state.failed_docs` without going through salvage.
            self._write_failed_docs_report(ctx, state)
            await log_cb(
                ctx.project_id,
                f"[custom] Phase 3 salvage summary: {len(state.failed_docs)} of "
                f"{len(self._required_docs)} docs shipped as stubs/truncated "
                "(see _assessment/failed_docs.json); accepted docs publish normally "
                "and a resume/regenerate retries the flagged ones",
                "warning",
            )

        # R2.1: doc dispatch is finished -- release the unused remainder of
        # the Phase 3 floor so later consumers (business topology, critics)
        # are not locked out of the shared pool by a reservation nothing will
        # claim anymore. (If this phase raises, the run aborts, so a release
        # on the failure path is moot.)
        state.planned_phase_floors.pop("phase3", None)

    def _salvage_failed_doc(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        doc_name: str,
        result: ModelResult,
    ) -> None:
        """Salvage-first disposition for one failed Phase 3 doc (P0.2/P0.3).

        Output-cap hits with usable text are truncate-flag-ACCEPTED; everything
        else becomes an explicit stub. Both are recorded in
        ``state.failed_docs`` (persisted to ``_assessment/failed_docs.json``)
        and both leave the agent task in a non-accepted status so a resume run
        retries exactly these documents.

        A provider credential/billing failure is NOT salvageable and raises
        ``FatalProviderError`` instead of writing a stub: it is account state,
        so every later document would land here too and the run would publish a
        full set of stubs under a success banner.
        """
        if self._is_fatal_provider_failure(result):
            raise FatalProviderError(
                f"provider credential/billing failure while writing {doc_name}: "
                f"{result.raw_error}"
            )
        reason = result.raw_error or "doc generation failed"
        incomplete = str((result.metadata or {}).get("incomplete_reason") or "")
        text = (result.text or "").strip()
        if text and incomplete == "length":
            disposition = "truncated_accepted"
            content = self._final_doc_content(state, doc_name, text).rstrip() + (
                "\n\n---\n\n> **TRUNCATION NOTICE:** generation reached this "
                "document's output ceiling; the content above is complete as "
                "written but the document may end early. A resume run will "
                "regenerate it in full.\n"
            )
        else:
            disposition = "stub"
            unit_name = self._per_job_doc_units.get(doc_name)
            scope_line = (
                f"- Scoped unit: `{unit_name}`\n" if unit_name else ""
            )
            content = (
                f"# {doc_name.replace('_', ' ').title()}\n\n"
                "> **GENERATION GAP:** this document could not be generated in "
                "this run and is published as a stub so the remaining "
                "documentation set ships. Trigger generation again to resume "
                "and fill this document.\n\n"
                f"- Failure: {reason}\n"
                f"{scope_line}"
                "- Deterministic evidence of record: `_analysis/` evidence "
                "bundles and the engineering fact maps remain available.\n"
            )
        (ctx.staging_dir / f"{doc_name}.md").write_text(content, encoding="utf-8")
        state.failed_docs.append({
            "doc": doc_name,
            "disposition": disposition,
            "reason": reason[:500],
            "incomplete_reason": incomplete or None,
        })
        # Persist immediately, not only in the end-of-phase summary: the
        # executive summary is dispatched BEFORE that summary write and has to
        # know which of its context documents are stubs.
        self._write_failed_docs_report(ctx, state)

    def _write_failed_docs_report(self, ctx: DocGenContext, state: HarnessRunState) -> None:
        """Publish the salvage ledger as artifacts, after every disposition.

        Two files, one source of truth (``state.failed_docs``):

        * ``_assessment/failed_docs.json`` -- the machine record (unchanged
          shape), now written incrementally rather than once at end of phase;
        * ``_analysis/_generation_gaps.md`` -- the same facts as a Phase 3
          context file, so a document that synthesizes from its siblings states
          the gap instead of writing confidently over half-empty inputs.
        """
        if not state.failed_docs:
            return
        report_path = ctx.staging_dir / "_assessment" / "failed_docs.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(state.failed_docs, indent=2), encoding="utf-8")

        stubs = [r for r in state.failed_docs if r.get("disposition") == "stub"]
        truncated = [r for r in state.failed_docs if r.get("disposition") != "stub"]
        lines = [
            "# Generation Gaps (authoritative)",
            "",
            "The documents listed here did NOT generate normally in this run. "
            "This file is deterministic Nexus bookkeeping, not model output.",
            "",
            "MANDATORY for any document that summarizes or synthesizes across "
            "the document set: state this coverage gap explicitly, name the "
            "missing documents, and do not present the summary as complete "
            "coverage of the estate. Never infer that a subject is absent, "
            "healthy or simple because its document is missing here.",
            "",
        ]
        if stubs:
            lines.append("## Not generated (published as stubs; no content)")
            lines.append("")
            lines.append("These documents were NOT supplied to you as context.")
            lines.append("")
            for record in stubs:
                lines.append(f"- `{record['doc']}` -- {str(record.get('reason') or '')[:200]}")
            lines.append("")
        if truncated:
            lines.append("## Generated but incomplete (truncated, content usable)")
            lines.append("")
            for record in truncated:
                lines.append(
                    f"- `{record['doc']}` ({record.get('disposition')}) -- "
                    f"{str(record.get('reason') or '')[:200]}"
                )
            lines.append("")
        gaps_path = ctx.staging_dir / "_analysis" / "_generation_gaps.md"
        gaps_path.parent.mkdir(parents=True, exist_ok=True)
        gaps_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    @staticmethod
    def _stubbed_doc_names(ctx: DocGenContext) -> set[str]:
        """Documents published as content-free stubs in this run.

        Read from ``_assessment/failed_docs.json`` rather than run state so
        every consumer -- both engines, and any later phase -- resolves the same
        answer from the artifact of record.
        """
        report_path = ctx.staging_dir / "_assessment" / "failed_docs.json"
        if not report_path.is_file():
            return set()
        try:
            records = json.loads(report_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return set()
        if not isinstance(records, list):
            return set()
        return {
            str(r.get("doc"))
            for r in records
            if isinstance(r, dict) and r.get("disposition") == "stub" and r.get("doc")
        }

    def _build_unit_docs_digest(self, ctx: DocGenContext, unit_docs: list[str]) -> Path | None:
        """Deterministic bounded digest of finished unit docs for summary docs.

        Per unit doc: the Overview section and the Protected Scanner Facts
        table (the fact ledger), bounded by ``phase3_unit_digest_bytes`` in
        total. Group/summary writers receive this digest so they synthesize
        FROM the finished unit docs instead of re-deriving from the analysis
        layer alone (the verified shallow-summary root cause).
        """
        budget = max(10_000, config.phase3_unit_digest_bytes)
        parts: list[str] = [
            "# Unit Docs Digest",
            "",
            "Deterministic extract of the finished per-unit reference docs "
            "(Overview + Protected Scanner Facts). Quote these facts as-is; "
            "never recompute them. The full dossiers ship alongside this "
            "document set.",
            "",
        ]
        used = sum(len(p.encode("utf-8")) + 1 for p in parts)
        truncated = 0
        # A stubbed unit doc has no Overview and no fact ledger -- digesting it
        # would put a GENERATION GAP banner in front of every summary writer as
        # if it were that unit's evidence. The gap is reported once, by
        # `_analysis/_generation_gaps.md`.
        stubbed = self._stubbed_doc_names(ctx)
        for doc_name in unit_docs:
            if doc_name in stubbed:
                continue
            doc_path = ctx.staging_dir / f"{doc_name}.md"
            if not doc_path.exists():
                continue
            text = doc_path.read_text(encoding="utf-8", errors="replace")
            unit = self._per_job_doc_units.get(doc_name, doc_name)
            extract = self._extract_unit_digest_sections(text)
            entry = f"## {unit}\n\n{extract}\n"
            entry_bytes = len(entry.encode("utf-8"))
            if used + entry_bytes > budget:
                truncated += 1
                continue
            parts.append(entry)
            used += entry_bytes
        if len(parts) <= 4:
            return None
        if truncated:
            parts.append(
                f"\n> NOTE: {truncated} unit extract(s) omitted at the digest "
                "byte budget; their full dossiers are in the document set."
            )
        digest_path = ctx.staging_dir / "_analysis" / "_unit_docs_digest.md"
        digest_path.parent.mkdir(parents=True, exist_ok=True)
        digest_path.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
        return digest_path

    @staticmethod
    def _extract_unit_digest_sections(text: str) -> str:
        """Pull the Overview + Protected Scanner Facts sections from a unit doc."""
        wanted = ("overview", "protected scanner facts")
        out: list[str] = []
        keep = False
        for line in text.splitlines():
            heading = re.match(r"^##\s+(.+?)\s*$", line)
            if heading is not None:
                keep = heading.group(1).strip().lower() in wanted
                continue
            if keep and line.strip():
                out.append(line)
        if out:
            return "\n".join(out)
        # Fallback: first 20 non-empty lines after the title.
        body = [ln for ln in text.splitlines()[1:] if ln.strip()]
        return "\n".join(body[:20])

    @staticmethod
    def _strip_llm_preamble(content: str) -> str:
        """Drop conversational lead-in emitted before the document's first heading.

        Models routinely prefix output with chatter like "Now I have all the
        evidence. Let me compose the document." followed by a horizontal rule.
        Final docs must start at their first heading. The 500-char cap keeps
        this from discarding substantial pre-heading content that would signal
        a different problem than a leaked preamble.
        """
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if not re.match(r"^#{1,6}\s", line):
                continue
            if i == 0:
                return content
            preamble = [ln for ln in lines[:i] if ln.strip() and ln.strip() != "---"]
            if preamble and sum(len(ln) for ln in preamble) <= 500:
                return "\n".join(lines[i:])
            return content
        return content

    def _final_doc_content(self, state: HarnessRunState, doc_name: str, body: str) -> str:
        content = self._strip_llm_preamble(body.strip())
        # Last-resort structural repair (deterministic selection, no new
        # prose): a document that still carries restarted/repeated structure
        # collapses to one copy of each section before publication.
        if duplicate_heading_errors(content):
            content = dedupe_document(content)
        canonical_title = doc_name.replace("_", " ").title()
        first_heading = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if not first_heading or canonical_title.lower() not in first_heading.group(1).lower():
            content = f"# {canonical_title}\n\n{content}"
        if doc_name == "02_COMPONENT_INVENTORY" and "## Canonical Unit Inventory" not in content:
            content = (
                f"{content.rstrip()}\n\n"
                "## Canonical Unit Inventory\n\n"
                f"Canonical inventory: {state.facts_manifest.unit_count} "
                f"{state.facts_manifest.canonical_unit_label}.\n\n"
                f"{self._unit_catalog_markdown(state)}"
            )
        return content.rstrip() + "\n"

    async def _phase35_critic_expander(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        log_cb: LogCallback,
    ) -> None:
        """Ask for bounded doc expansions and append only validated markdown."""

        assessment_dir = ctx.staging_dir / "_assessment"
        context_base = [
            ctx.staging_dir / "facts_manifest.json",
            assessment_dir / "scorecard.md",
            assessment_dir / "quick_scan.md",
            ctx.staging_dir / "_investigation" / "_summary.md",
        ]
        critic_pack = assessment_dir / "prompt_packs" / "_critic_prompt_pack.md"
        if critic_pack.exists():
            context_base.append(critic_pack)
        opts = ModelOptions(
            max_tokens=5000,
            temperature=0.0,
            retries=0,
            timeout_s=180.0,
            provider_options=self._provider_options(self.write_provider),
        )

        for family, doc_names in self._doc_assignments.items():
            critic_prompt_text = self._critic_prompt(state, family, doc_names)
            if getattr(self.write_provider, "supports_tools", False):
                result = await self.write_provider.complete_with_tools(
                    _with_tool_instructions(critic_prompt_text, ctx.staging_dir), ctx.staging_dir, opts
                )
            else:
                docs = [ctx.staging_dir / f"{doc}.md" for doc in doc_names if (ctx.staging_dir / f"{doc}.md").exists()]
                evidence = sorted((ctx.staging_dir / "_investigation").glob("*.md"))
                context_files = [p for p in [*context_base, *evidence, *docs] if p.exists()]
                result = await self.write_provider.complete(critic_prompt_text, context_files, opts)
            state.calls.append(ProviderCallLog(phase="phase3.5", target=family, result=result))
            await self._log_call_metrics(ctx, f"critic:{family}", result, log_cb)
            if not result.success:
                await log_cb(ctx.project_id, f"[custom] Critic skipped for {family}: {result.raw_error}", "warning")
                continue
            applied = self._apply_validated_expansions(ctx, state, result.text, set(doc_names))
            await log_cb(ctx.project_id, f"[custom] Critic expansions applied for {family}: {applied}", "info")

    async def _phase36_consistency(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        log_cb: LogCallback,
    ) -> None:
        """Write a cross-document consistency report before final validation gates."""
        docs = [
            ctx.staging_dir / f"{doc}.md"
            for doc in self._required_docs
            if (ctx.staging_dir / f"{doc}.md").exists()
        ]
        findings: list[dict] = []
        expected = state.facts_manifest.unit_count
        inventory_claim_re = re.compile(
            rf"\b(?:total|canonical inventory)[:\s]+(\d+)\s+{re.escape(state.facts_manifest.canonical_unit_label)}\b",
            re.IGNORECASE,
        )
        unit_names = state.facts_manifest.unit_names()
        for path in docs:
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in inventory_claim_re.finditer(text):
                claimed = int(match.group(1))
                if claimed != expected:
                    findings.append({
                        "type": "inventory_conflict",
                        "doc": path.name,
                        "message": f"Inventory claim {claimed} conflicts with facts manifest count {expected}.",
                        "critical": True,
                    })
            unknown_units = sorted({
                token for token in re.findall(r"\b[A-Za-z][A-Za-z0-9_.-]{2,}\b", text)
                if token.endswith((".item", ".recipe", ".xml")) and token not in unit_names
            })
            if unknown_units:
                findings.append({
                    "type": "unit_drift",
                    "doc": path.name,
                    "message": "Document mentions source-like units outside facts manifest: " + ", ".join(unknown_units[:10]),
                    "critical": False,
                })
        report = {
            "ok": not any(f.get("critical") for f in findings),
            "findings": findings,
            "docs_checked": [p.name for p in docs],
            "unit_count": expected,
        }
        debug_write(
            debug_dir(ctx.staging_dir, "_assessment", "consistency_report.json"),
            json.dumps(report, indent=2),
        )
        self._record_dag_node_result(
            ctx,
            "consistency",
            "passed" if report["ok"] else "failed",
            artifacts=[],
            validation="consistency",
        )
        if report["ok"]:
            await log_cb(ctx.project_id, "[custom] Phase 3.6 consistency gate passed", "info")
        else:
            await log_cb(
                ctx.project_id,
                "[custom] Phase 3.6 consistency findings: "
                + "; ".join(str(f["message"]) for f in findings[:3]),
                "warning",
            )

    def _narrow_repair_context(
        self,
        prompt: str,
        context_files: list[Path],
        *,
        token_cap: int = REPAIR_TOKEN_CAP,
    ) -> tuple[list[Path], int]:
        """Keep repair context under the repair cap before any model call."""

        existing = [path for path in context_files if path.exists()]
        selected: list[Path] = []
        for path in existing:
            candidate = [*selected, path]
            if estimate_repair_tokens(prompt, candidate) <= token_cap:
                selected.append(path)
        estimate = estimate_repair_tokens(prompt, selected)
        if estimate > token_cap:
            return [], estimate
        return selected, estimate

    def _write_repair_context_summary(
        self,
        ctx: DocGenContext,
        *,
        phase: str,
        target: str,
        context_files: list[Path],
        estimated_tokens: int,
    ) -> None:
        payload = {
            "phase": phase,
            "target": target,
            "estimated_tokens": estimated_tokens,
            "context_files": [
                path.relative_to(ctx.staging_dir).as_posix()
                if path.is_relative_to(ctx.staging_dir)
                else path.as_posix()
                for path in context_files
            ],
            "context_bytes": context_bytes(context_files),
            "token_cap": REPAIR_TOKEN_CAP,
        }
        safe_target = self._safe_name(target)
        debug_write(
            debug_dir(ctx.staging_dir, "_assessment", "repair", f"{phase}_{safe_target}_context.json"),
            json.dumps(payload, indent=2),
        )

    @staticmethod
    def _repair_root_causes(errors: list[str], missing_units: list[str]) -> list[dict]:
        causes: list[dict] = []
        if missing_units:
            causes.append({
                "category": "coverage",
                "scope": "global",
                "reason": "Validator reported missing canonical units: " + ", ".join(missing_units[:20]),
            })
        patterns = [
            ("provenance", ("evidence register", "source provenance", "source artifact", "confidence")),
            ("depth", ("too shallow", "depth", "actionable", "debug", "test", "change guidance")),
            ("contradiction", ("conflicting", "contradiction", "contradicts", "inventory claim")),
            ("format", ("markdown", "heading", "section", "table", "columns")),
            ("scanner_limited", ("scanner-limited", "scanner limited", "redacted", "unavailable")),
        ]
        for error in errors:
            lower = error.lower()
            category = "coverage"
            for candidate, needles in patterns:
                if any(needle in lower for needle in needles):
                    category = candidate
                    break
            doc_match = re.search(r"\b([0-9][0-9A-Za-z_]*\.md)\b", error)
            causes.append({
                "category": category,
                "scope": doc_match.group(1) if doc_match else "global",
                "reason": error,
            })
        return causes

    async def _complete_core_generation(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        *,
        provider: ModelProvider,
        phase: str,
        target: str,
        prompt: str,
        context_files: list[Path],
        opts: ModelOptions,
        log_cb: LogCallback,
        calls_lock: asyncio.Lock | None = None,
        task_contract: AgentTaskContract | None = None,
        scoped_tools: ScopedEvidenceTools | None = None,
        phase3_inputs: Phase3PlanInputs | None = None,
    ):
        """Run a Phase 2/3 model call with explicit bounded files and slice caching."""

        pack_content, context_files = self._extract_prompt_pack(context_files)
        if pack_content:
            existing_system = opts.system_prompt or ""
            separator = "\n\n" if existing_system else ""
            opts = opts.model_copy(update={
                "system_prompt": pack_content + separator + existing_system,
            })
        context_files = self._bounded_context_files(context_files)
        tool_policy = self._tool_policy(provider, phase)
        use_tools = tool_policy.enabled
        if "repair" in phase and context_files:
            use_tools = False
        if use_tools:
            prompt = _with_agent_contract(prompt, task_contract)
            # Plugin-mode Phase 3 never reaches here (tool_policy disabled at line 727).
            # complete_with_tools does not accept context_files, so the starter pack must
            # be injected into system_prompt which propagates through tool_opts.
            # Phase 2 calls have no _doc_pack_ files, so they still get hint-only dispatch.
            _STARTER_PACK_BYTE_CAP = 100 * 1024
            doc_packs = [f for f in context_files if "_doc_pack_" in f.name and f.exists()]
            if doc_packs:
                combined = "\n\n".join(f.read_text(encoding="utf-8", errors="replace") for f in doc_packs)
                if len(combined.encode("utf-8")) <= _STARTER_PACK_BYTE_CAP:
                    existing = opts.system_prompt or ""
                    sep = "\n\n" if existing else ""
                    opts = opts.model_copy(update={"system_prompt": existing + sep + combined})
            context_files = []
        estimate_prompt = (
            f"{opts.system_prompt}\n\n{prompt}" if opts.system_prompt else prompt
        )
        estimated = estimate_input_tokens(estimate_prompt, context_files)
        hard = config.core_context_hard_ceiling_tokens
        soft = config.core_context_soft_target_tokens

        # Slicing redesign: Phase 3 documents are planned by expected OUTPUT
        # size. Most doc types fit ONE call (their evidence is bounded to the
        # context ceiling by priority trimming); only genuinely large docs
        # (unit-scaled inventories / rule catalogs) are partitioned by
        # section/unit-range with a shared skeleton -- every slice writes ONLY
        # its assigned sections and units, so there are no whole-document
        # restarts to merge and no blind-sibling contradictions.
        sectioned: Phase3TaskPlan | None = None
        if phase3_inputs is not None and phase.startswith("phase3") and not use_tools:
            doc_plan = plan_phase3_document(
                ctx.staging_dir,
                phase=phase,
                target=target,
                prompt=estimate_prompt,
                context_files=context_files,
                hard_tokens=hard,
                soft_tokens=soft,
                shared_file_max_bytes=config.phase3_shared_context_file_bytes,
                inputs=phase3_inputs,
            )
            if doc_plan is not None and doc_plan.mode == "single":
                context_files = doc_plan.slices[0].files
                estimated = estimate_input_tokens(estimate_prompt, context_files)
                if doc_plan.trimmed_bytes:
                    self._record_context_trim_flag(
                        ctx, phase, target, doc_plan.trimmed_bytes, doc_plan.trimmed_sources
                    )
                    await log_cb(
                        ctx.project_id,
                        f"[custom] single-call plan for {phase}/{target}: trimmed "
                        f"{doc_plan.trimmed_bytes} evidence bytes from "
                        f"{', '.join(doc_plan.trimmed_sources)} to fit the context ceiling",
                        "warning",
                    )
            elif doc_plan is not None and doc_plan.mode == "sections":
                sectioned = doc_plan
                if doc_plan.trimmed_bytes:
                    self._record_context_trim_flag(
                        ctx, phase, target, doc_plan.trimmed_bytes, doc_plan.trimmed_sources
                    )
        if estimated <= hard and sectioned is None:
            result = await self._call_model_with_logging(
                ctx,
                state,
                provider=provider,
                phase=phase,
                target=target,
                prompt=prompt,
                context_files=context_files,
                opts=opts,
                log_cb=log_cb,
                calls_lock=calls_lock,
                scoped_tools=scoped_tools,
            )
            if (
                not result.success
                and self._classify_failure(result) == FailureKind.CONTEXT_TOO_LARGE
                and config.agent_max_split_depth > 0
                and context_files
            ):
                await log_cb(
                    ctx.project_id,
                    f"[custom] context limit failure for {phase} {target}; retrying with split context",
                    "warning",
                )
            else:
                return result

        # R2.5: Phase 3 documents get INPUT-PARTITIONED slices (like Phase 2
        # evidence tasks): each slice receives only its own portion of the
        # evidence plus a small byte-stable shared skeleton, so total input is
        # ~1x the pack split across slices instead of N x the pack, and every
        # unit's evidence is covered instead of being dropped at the slice
        # cap. The generic splitter remains the fallback for other phases and
        # for contexts the partitioner cannot help with.
        slice_instructions: list[str | None] | None = None
        if sectioned is not None:
            partitioned = [list(s.files) for s in sectioned.slices]
            slice_instructions = [s.instruction for s in sectioned.slices]
            slices = partitioned
        else:
            partitioned = (
                plan_phase3_slices(
                    ctx.staging_dir,
                    phase=phase,
                    target=target,
                    prompt=estimate_prompt,
                    context_files=context_files,
                    hard_tokens=hard,
                    soft_tokens=soft,
                    max_slices=max(1, config.docgen_allowed_slices_per_doc),
                    shared_file_max_bytes=config.phase3_shared_context_file_bytes,
                )
                if phase.startswith("phase3")
                else None
            )
            slices = partitioned if partitioned is not None else self._split_context_pack(
                ctx, phase, target, estimate_prompt, context_files, hard, soft
            )
        if phase in {"phase2", "phase2_group"} and len(slices) > config.agent_phase2_max_slices:
            raise RuntimeError(
                f"Context slicing exceeded agent_phase2_max_slices={config.agent_phase2_max_slices} "
                f"({len(slices)} slices for {phase}/{target}). Reduce context size before proceeding."
            )
        # P0.3: hard-enforce the per-doc slice allowance for doc-level phases.
        # Verified failure: 00_AGENT_BRIEF ran 13 slices (each reserving a full
        # output ceiling) and exhausted the run output budget. On cap-hit the
        # excess evidence slices are dropped, the assembled output is flagged,
        # and the call is ACCEPTED (truncate-flag-accept, never reject-abort).
        # Input-partitioned Phase 3 plans (R2.5) are exempt: their slice count
        # is already balanced for full coverage and each extra slice re-sends
        # only the shared skeleton, not the whole pack.
        allowed_slices = max(1, config.docgen_allowed_slices_per_doc)
        slice_cap_note: str | None = None
        if (
            partitioned is None
            and not phase.startswith(("phase2", "phase1"))
            and len(slices) > allowed_slices
        ):
            dropped = len(slices) - allowed_slices
            slices = slices[:allowed_slices]
            slice_cap_note = (
                f"> **SLICE CAP NOTICE:** context for `{target}` exceeded the "
                f"per-document slice allowance ({allowed_slices}); {dropped} "
                "evidence slice(s) were not processed in this run. Facts from "
                "the dropped slices remain in the deterministic evidence "
                "artifacts of record."
            )
            self._record_slice_cap_flag(ctx, phase, target, dropped, allowed_slices)
            await log_cb(
                ctx.project_id,
                f"[custom] slice cap enforced for {phase}/{target}: "
                f"{allowed_slices} of {allowed_slices + dropped} slices run; "
                "output will carry a slice-cap notice",
                "warning",
            )
        await log_cb(
            ctx.project_id,
            f"[custom] split decision: phase={phase} target={target} "
            f"original_est_tokens={estimated} split_count={len(slices)} "
            f"slice_est_tokens={[estimate_input_tokens(estimate_prompt, s) for s in slices]}",
            "info",
        )
        cache_dir = ctx.staging_dir / "_model_slices" / phase
        cache_dir.mkdir(parents=True, exist_ok=True)
        slice_concurrency = 1 if state.adaptive_serial_generation else max(1, config.core_slice_concurrency)
        semaphore = asyncio.Semaphore(slice_concurrency)

        async def _run_slice(idx: int, slice_files: list[Path]) -> tuple[int, str | None, list[str]]:
            slice_id = f"{target}_slice_{idx}"
            cache_file = cache_dir / f"{self._safe_name(slice_id)}.md"
            if ctx.resume_state is not None and cache_file.exists() and cache_file.stat().st_size > 100:
                await log_cb(ctx.project_id, f"[custom] RESUME: skipping {phase} {slice_id} (slice cache exists)", "info")
                return idx, cache_file.read_text(encoding="utf-8", errors="replace"), []
            # P2.7 cache-friendly composition: the prompt and the shared
            # (common) context files stay BYTE-STABLE across slices; the
            # slice-specific instruction is appended at the TAIL as the last
            # context file. Injecting the slice index into the prompt head --
            # the previous shape -- invalidated the provider prefix cache for
            # the entire shared bundle on every slice.
            slice_prompt = prompt
            tail = transient_dir(ctx.staging_dir, "context_slices", phase) / (
                f"{self._safe_name(slice_id)}__instruction.md"
            )
            if slice_instructions is not None and slice_instructions[idx - 1]:
                # Section-partitioned plan: the tail carries this part's
                # explicit section + unit assignment (write ONLY those).
                instruction_text = slice_instructions[idx - 1]
            else:
                instruction_text = (
                    f"SLICE INSTRUCTION: You are processing slice {idx} of {len(slices)} "
                    f"for target {target}. Use only this slice's evidence. Preserve exact "
                    "names and mark unresolved coverage with its evidenced failure stage. The slices "
                    "are merged by section afterwards: write only sections this slice's "
                    "evidence supports, do not restate content that needs no evidence, and "
                    + (
                        "include the title and Table of Contents."
                        if idx == 1
                        else "do NOT include a Table of Contents."
                    )
                )
            transient_write(tail, instruction_text)
            slice_files = [*slice_files, tail]
            async with semaphore:
                result = await self._call_model_with_logging(
                    ctx,
                    state,
                    provider=provider,
                    phase=phase,
                    target=slice_id,
                    prompt=slice_prompt,
                    context_files=slice_files,
                    opts=opts,
                    log_cb=log_cb,
                    calls_lock=calls_lock,
                )
            if not result.success:
                failure_kind = self._classify_failure(result) or FailureKind.UNKNOWN
                if failure_kind != FailureKind.CONTEXT_TOO_LARGE:
                    return idx, None, [f"{slice_id}: {failure_kind.value}: {result.raw_error}"]
                smaller = (
                    self._split_context_pack(
                        ctx,
                        phase,
                        slice_id,
                        slice_prompt,
                        slice_files,
                        max(16_000, hard // 2),
                        max(8_000, soft // 2),
                    )
                    if config.agent_max_split_depth > 1
                    else [slice_files]
                )
                if len(smaller) > 1:
                    await log_cb(
                        ctx.project_id,
                        f"[custom] retrying {phase} {slice_id} with {len(smaller)} smaller slices "
                        f"(failure_kind={failure_kind.value})",
                        "warning",
                    )
                    sub_outputs: list[str] = []
                    slice_failures: list[str] = []
                    for sub_idx, sub_files in enumerate(smaller, start=1):
                        async with semaphore:
                            retry_result = await self._call_model_with_logging(
                                ctx,
                                state,
                                provider=provider,
                                phase=phase,
                                target=f"{slice_id}_retry_{sub_idx}",
                                prompt=slice_prompt,
                                context_files=sub_files,
                                opts=opts.model_copy(update={"retries": 0}),
                                log_cb=log_cb,
                                calls_lock=calls_lock,
                                scoped_tools=scoped_tools,
                            )
                        if not retry_result.success:
                            retry_kind = self._classify_failure(retry_result) or FailureKind.UNKNOWN
                            slice_failures.append(
                                f"{slice_id}_retry_{sub_idx}: {retry_kind.value}: {retry_result.raw_error}"
                            )
                            continue
                        sub_outputs.append(retry_result.text.strip())
                    if not slice_failures and sub_outputs:
                        text = "\n\n---\n\n".join(sub_outputs)
                        cache_file.write_text(text, encoding="utf-8")
                        return idx, text, []
                    return idx, None, slice_failures
                return idx, None, [f"{slice_id}: {result.raw_error}"]
            cache_file.write_text(result.text.strip(), encoding="utf-8")
            return idx, result.text.strip(), []

        pre_calls_len = len(state.calls)
        slice_results = await asyncio.gather(*[
            _run_slice(idx, slice_files)
            for idx, slice_files in enumerate(slices, start=1)
        ])
        outputs: list[str] = []
        failures: list[str] = []
        for _, text, slice_failures in sorted(slice_results, key=lambda item: item[0]):
            if slice_failures:
                failures.extend(slice_failures)
            if text:
                outputs.append(text)

        new_calls = state.calls[pre_calls_len:]
        total_input_tokens = sum(c.result.input_tokens for c in new_calls)
        total_output_tokens = sum(c.result.output_tokens for c in new_calls)

        # Verified correctness bug: each document slice RESTARTS the whole
        # document from its own evidence page; concatenating restarts shipped
        # the same structure N times (VQ 01_ARCHITECTURE_MAP: 11 copies).
        # Phase 3 slice outputs are merged by section deterministically --
        # one title, one TOC, each H2 exactly once, duplicate blocks dropped.
        if phase.startswith("phase3") and len(outputs) > 1:
            if sectioned is not None:
                # Output-partitioned plan (slicing redesign): each slice wrote
                # its OWN distinct sections/units (no whole-document restarts),
                # so assemble heading-once in catalog section order rather than
                # deduping N restarts.
                assembled = merge_sectioned_doc_outputs(
                    outputs, preferred_order=sectioned.section_order
                )
            else:
                assembled = merge_sliced_doc_outputs(outputs)
            if slice_cap_note is not None:
                assembled = assembled.rstrip() + "\n\n" + slice_cap_note
        else:
            if slice_cap_note is not None:
                outputs.append(slice_cap_note)
            assembled = "\n\n---\n\n".join(outputs)
        if failures:
            return ModelResult(
                success=False,
                text=assembled,
                model_name=getattr(provider, "model_name", ""),
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                raw_error="; ".join(failures[:5]),
                metadata={"phase": phase, "target": target, "incomplete_slices": failures},
            )
        return ModelResult(
            success=True,
            text=assembled,
            model_name=getattr(provider, "model_name", ""),
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            metadata={"phase": phase, "target": target, "slices": len(slices)},
        )

    @staticmethod
    def _record_context_trim_flag(
        ctx: DocGenContext,
        phase: str,
        target: str,
        trimmed_bytes: int,
        trimmed_sources: list[str],
    ) -> None:
        """Append a context-trim event to _assessment/context_trim_flags.json.

        Mirrors ``_record_slice_cap_flag``. The call sites at the single/section
        plan boundary landed without this method, so every document whose
        evidence had to be trimmed to fit the context ceiling died on
        AttributeError and published as a GENERATION GAP stub -- the largest
        documents systematically, since they are the ones that get trimmed.
        Recording the flag is diagnostic only and must never be able to fail a
        document, so a write error is swallowed the same way a malformed
        existing file is.
        """
        path = ctx.staging_dir / "_assessment" / "context_trim_flags.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                entries = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(entries, list):
                    entries = []
            except (OSError, json.JSONDecodeError):
                entries = []
            entries.append({
                "phase": phase,
                "target": target,
                "trimmed_bytes": trimmed_bytes,
                "trimmed_sources": list(trimmed_sources or []),
                "at": time.time(),
            })
            path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        except OSError:
            logger.warning(
                "[custom] could not record context-trim flag for %s/%s", phase, target
            )

    @staticmethod
    def _record_slice_cap_flag(
        ctx: DocGenContext, phase: str, target: str, dropped: int, allowed: int
    ) -> None:
        """Append a slice-cap event to _assessment/slice_cap_flags.json."""
        path = ctx.staging_dir / "_assessment" / "slice_cap_flags.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(entries, list):
                entries = []
        except (OSError, json.JSONDecodeError):
            entries = []
        entries.append({
            "phase": phase,
            "target": target,
            "allowed_slices": allowed,
            "dropped_slices": dropped,
            "at": time.time(),
        })
        path.write_text(json.dumps(entries, indent=2), encoding="utf-8")

    async def _call_model_with_logging(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        *,
        provider: ModelProvider,
        phase: str,
        target: str,
        prompt: str,
        context_files: list[Path],
        opts: ModelOptions,
        log_cb: LogCallback,
        calls_lock: asyncio.Lock | None = None,
        scoped_tools: ScopedEvidenceTools | None = None,
    ):
        # One policy boundary owns provider failover semantics. Phase-specific
        # callers historically supplied retries=0 or retries=1, which caused a
        # transient API failure to reach the fallback before the expected three
        # same-provider retries had been exhausted.
        opts = opts.model_copy(
            update={"retries": max(0, config.doc_generation_api_retries)}
        )
        estimate_prompt = (
            f"{opts.system_prompt}\n\n{prompt}" if opts.system_prompt else prompt
        )
        estimated = estimate_input_tokens(estimate_prompt, context_files)
        total_bytes = context_bytes(context_files) + len(
            (opts.system_prompt or "").encode("utf-8")
        )
        budget_key = self._doc_budget_key(target)
        if state.total_empty_outputs >= config.docgen_empty_outputs_per_run_limit:
            message = (
                "empty output run limit already exceeded before model call: "
                f"{state.total_empty_outputs}/{config.docgen_empty_outputs_per_run_limit}; target={target}"
            )
            await log_cb(ctx.project_id, f"[custom] {message}", "error")
            return self._failure_result(provider, phase, target, message, FailureKind.EMPTY_OUTPUT)
        if state.empty_output_counts.get(budget_key, 0) >= config.docgen_empty_outputs_per_doc_limit:
            message = (
                "empty output doc limit already exceeded before model call: "
                f"{budget_key} {state.empty_output_counts.get(budget_key, 0)}/"
                f"{config.docgen_empty_outputs_per_doc_limit}; target={target}"
            )
            await log_cb(ctx.project_id, f"[custom] {message}", "error")
            return self._failure_result(provider, phase, target, message, FailureKind.EMPTY_OUTPUT)

        reservation, budget_error = await self._reserve_model_budget(
            state,
            estimated_input_tokens=estimated,
            max_output_tokens=opts.max_tokens,
            phase=phase,
        )
        if reservation is None:
            message = (
                f"run budget blocked {phase} {target}: {budget_error}; "
                f"groups={len(state.facts_manifest.groups)} "
                f"units={state.facts_manifest.unit_count} docs={len(self._required_docs)}"
            )
            await log_cb(ctx.project_id, f"[custom] {message}", "error")
            return self._failure_result(provider, phase, target, message, FailureKind.BUDGET_EXCEEDED)

        budget = self._run_model_call_budget(state)
        input_budget = self._run_input_token_budget(state)
        input_budget_label = str(input_budget) if input_budget > 0 else "unlimited"
        await log_cb(
            ctx.project_id,
            f"[custom] model call start: phase={phase} target={target} split_id={target} "
            f"est_input_tokens={estimated} context_files={len(context_files)} context_bytes={total_bytes} "
            f"api_retries={opts.retries} "
            f"call_budget={reservation.call_index}/{budget} "
            f"input_budget={state.budget_consumed_input_tokens + state.pending_input_tokens}/"
            f"{input_budget_label} output_budget="
            f"{state.budget_consumed_output_tokens + state.pending_output_tokens}/"
            f"{self._run_output_token_budget(state)}",
            "info",
        )
        result: ModelResult | None = None
        try:
            snapshot_dir, debug_snapshot_dir = self._write_model_input_snapshot(
                ctx,
                provider=provider,
                phase=phase,
                target=target,
                prompt=prompt,
                context_files=context_files,
                opts=opts,
                estimated_tokens=estimated,
                total_bytes=total_bytes,
                call_index=reservation.call_index,
            )
            tool_policy = self._tool_policy(provider, phase)
            if tool_policy.enabled:
                tool_opts = opts.model_copy(update={
                    "tool_input_token_cap": tool_policy.max_input_tokens,
                    "tool_phase": phase,
                    "tool_target": target,
                })
                result = await provider.complete_with_tools(
                    _with_tool_instructions(prompt, ctx.staging_dir),
                    ctx.staging_dir,
                    tool_opts,
                    scoped_tools=scoped_tools,
                )
            else:
                result = await provider.complete(prompt, context_files, opts)
        finally:
            await self._settle_model_budget(state, reservation, result)
        if result is None:
            raise RuntimeError(f"Provider returned no result for {phase}/{target}")
        self._write_model_response_snapshot(snapshot_dir, debug_snapshot_dir, result)
        failure_kind = self._classify_failure(result)
        if failure_kind is not None:
            raw_error = result.raw_error or (
                f"{failure_kind.value}: model returned no usable text"
                if failure_kind == FailureKind.EMPTY_OUTPUT
                else f"{failure_kind.value}: model call failed"
            )
            result = result.model_copy(
                update={
                    "success": False,
                    "raw_error": raw_error,
                    "metadata": {**result.metadata, "failure_kind": failure_kind.value},
                }
            )
            result = await self._record_failure_policy(
                ctx,
                state,
                target=target,
                result=result,
                kind=failure_kind,
                log_cb=log_cb,
            )
            # A length stop proves this exact task cannot fit its output ceiling.
            # Never repeat the same prompt/context: it doubles cost and normally
            # truncates at the same place. Phase planners must shrink evidence or
            # split the document before dispatch instead.
            if failure_kind == FailureKind.INCOMPLETE:
                await log_cb(
                    ctx.project_id,
                    f"[custom] output truncated ({self._describe_provider(provider)}); "
                    "identical retry suppressed; task requires bounded evidence or section planning",
                    "warning",
                )
            if (
                not result.success
                and self.fallback_provider is not None
                and failure_kind is not None
                and failure_kind.is_retryable_with_fallback
            ):
                await log_cb(
                    ctx.project_id,
                    f"[custom] primary failed after {opts.retries} retries "
                    f"({failure_kind.value}); switching to default fallback "
                    f"{self._describe_provider(self.fallback_provider)}",
                    "warning",
                )
                fallback_reservation, fallback_budget_error = await self._reserve_model_budget(
                    state,
                    estimated_input_tokens=estimated,
                    max_output_tokens=opts.max_tokens,
                    phase=phase,
                )
                if fallback_reservation is None:
                    await log_cb(
                        ctx.project_id,
                        f"[custom] fallback blocked by run budget: {fallback_budget_error}",
                        "warning",
                    )
                else:
                    # The fallback is a second physical request. Record the
                    # primary only after its replacement has passed the budget
                    # gate so a blocked fallback cannot duplicate usage.
                    primary_call = ProviderCallLog(
                        phase=phase, target=target, result=result
                    )
                    if calls_lock is None:
                        state.calls.append(primary_call)
                    else:
                        async with calls_lock:
                            state.calls.append(primary_call)
                    fallback_result: ModelResult | None = None
                    try:
                        fallback_policy = self._tool_policy(self.fallback_provider, phase)
                        if fallback_policy.enabled:
                            fallback_opts = opts.model_copy(update={
                                "tool_input_token_cap": fallback_policy.max_input_tokens,
                                "tool_phase": phase,
                                "tool_target": target,
                            })
                            fallback_result = await self.fallback_provider.complete_with_tools(
                                _with_tool_instructions(prompt, ctx.staging_dir),
                                ctx.staging_dir,
                                fallback_opts,
                                scoped_tools=scoped_tools,
                            )
                        else:
                            fallback_result = await self.fallback_provider.complete(
                                prompt, context_files, opts
                            )
                    finally:
                        await self._settle_model_budget(
                            state, fallback_reservation, fallback_result
                        )
                    if fallback_result is not None:
                        fallback_kind = self._classify_failure(fallback_result)
                        if fallback_kind is None:
                            await log_cb(
                                ctx.project_id,
                                f"[custom] fallback succeeded: {self._describe_provider(self.fallback_provider)} "
                                f"tokens={fallback_result.output_tokens}",
                                "info",
                            )
                            result = fallback_result
                        else:
                            result = fallback_result
                            failure_kind = fallback_kind
                            await log_cb(
                                ctx.project_id,
                                f"[custom] fallback also failed ({fallback_kind.value}): "
                                f"{self._describe_provider(self.fallback_provider)}",
                                "warning",
                            )
        call = ProviderCallLog(phase=phase, target=target, result=result)
        if calls_lock is None:
            state.calls.append(call)
        else:
            async with calls_lock:
                state.calls.append(call)
        await self._log_call_metrics(ctx, target, result, log_cb)
        return result

    def _write_model_input_snapshot(
        self,
        ctx: DocGenContext,
        *,
        provider: ModelProvider,
        phase: str,
        target: str,
        prompt: str,
        context_files: list[Path],
        opts: ModelOptions,
        estimated_tokens: int,
        total_bytes: int,
        call_index: int,
    ) -> tuple[Path, Path]:
        """Persist the per-call input manifest; full snapshots only in debug mode.

        The manifest (file list, sizes, options) is always written — it is the
        per-call ledger used to trace what reached each model call. The exact
        prompt/context/request/response payloads are forensics and land under
        _debug/model_inputs/ only when MCP_PIPELINE_DEBUG is set.
        """

        call_dir = f"{call_index:03d}_{self._safe_name(target)}"
        snapshot_dir = (
            ctx.staging_dir
            / "_model_inputs"
            / self._safe_name(phase)
            / call_dir
        )
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        debug_snapshot_dir = debug_dir(
            ctx.staging_dir, "model_inputs", self._safe_name(phase), call_dir
        )
        context_entries: list[dict[str, object]] = []
        for idx, path in enumerate(context_files, start=1):
            exists = path.exists()
            try:
                rel_path = path.relative_to(ctx.staging_dir).as_posix()
            except ValueError:
                rel_path = str(path)
            size = path.stat().st_size if exists else 0
            context_entries.append({
                "index": idx,
                "path": rel_path,
                "bytes": size,
                "exists": exists,
            })

        if pipeline_debug_enabled():
            bundle_parts = [
                f"# Model Input Bundle: {phase} / {target}",
                "",
                "## Prompt",
                "",
                prompt.rstrip(),
                "",
                "## Context Files",
            ]
            for entry, path in zip(context_entries, context_files):
                bundle_parts.extend(["", f"### {entry['index']}. {entry['path']}", ""])
                if entry["exists"]:
                    bundle_parts.append(path.read_text(encoding="utf-8", errors="replace").rstrip())
                else:
                    bundle_parts.append("[missing context file]")
            debug_write(debug_snapshot_dir / "prompt.md", prompt.rstrip() + "\n")
            debug_write(debug_snapshot_dir / "context_bundle.md", "\n".join(bundle_parts).rstrip() + "\n")
        manifest = {
            "phase": phase,
            "target": target,
            "provider": self._describe_provider(provider),
            "model": provider.model_name,
            "call_index": call_index,
            "estimated_input_tokens": estimated_tokens,
            "context_bytes": total_bytes,
            "system_prompt_bytes": len((opts.system_prompt or "").encode("utf-8")),
            "context_file_count": len(context_files),
            "options": {
                "max_tokens": opts.max_tokens,
                "temperature": opts.temperature,
                "timeout_s": opts.timeout_s,
                "retries": opts.retries,
            },
            "context_files": context_entries,
        }
        (snapshot_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        request = {
            "provider": self._describe_provider(provider),
            "model": provider.model_name,
            "phase": phase,
            "target": target,
            "uses_tools": _provider_supports_tools(provider),
            "message_count": 1,
            "context_file_count": len(context_files),
            "max_tokens": opts.max_tokens,
            "temperature": opts.temperature,
            "provider_options": opts.provider_options,
        }
        debug_write(debug_snapshot_dir / "request.json", json.dumps(request, indent=2))
        return snapshot_dir, debug_snapshot_dir

    @staticmethod
    def _write_model_response_snapshot(
        snapshot_dir: Path, debug_snapshot_dir: Path, result: ModelResult
    ) -> None:
        # Always-on: the REAL provider-reported tokens for this call (the
        # tool-loop sum, not the pre-call estimate in manifest.json). This is
        # what reconciles to the bill; cache_read is the cheap cache-hit
        # portion of input.
        usage = {
            "success": result.success,
            "input_tokens": result.input_tokens,
            "input_cache_read_tokens": result.cache_read_tokens,
            "input_cache_miss_tokens": max(0, result.input_tokens - result.cache_read_tokens),
            "output_tokens": result.output_tokens,
            "latency_ms": result.latency_ms,
            "retries": result.retries,
        }
        try:
            (snapshot_dir / "usage.json").write_text(
                json.dumps(usage, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("failed to write usage.json to %s: %s", snapshot_dir, exc, exc_info=True)
        # Full response payload remains debug-only forensics, so it goes under
        # _debug/ rather than alongside the always-on manifest/usage files.
        response = {
            **usage,
            "model_name": result.model_name,
            "raw_error": result.raw_error,
            "metadata": result.metadata,
            "text_preview": result.text[:1000],
        }
        debug_write(debug_snapshot_dir / "response.json", json.dumps(response, indent=2, default=str))

    @staticmethod
    def _bounded_context_files(context_files: list[Path]) -> list[Path]:
        seen: set[Path] = set()
        bounded: list[Path] = []
        for path in context_files:
            if path.name in LARGE_CONTEXT_ARTIFACTS:
                continue
            if path in seen or not path.exists():
                continue
            seen.add(path)
            bounded.append(path)
        return bounded

    def _split_context_pack(
        self,
        ctx: DocGenContext,
        phase: str,
        target: str,
        prompt: str,
        context_files: list[Path],
        hard_tokens: int,
        soft_tokens: int,
    ) -> list[list[Path]]:
        if estimate_input_tokens(prompt, context_files) <= hard_tokens:
            return [context_files]
        if not context_files:
            return [context_files]

        existing = [path for path in context_files if path.exists()]
        if not existing:
            return [context_files]

        prompt_estimate = estimate_input_tokens(prompt, [])
        if prompt_estimate >= hard_tokens:
            return [[]]

        # Keep small/instructional context with every slice, but always reserve
        # room for evidence.  The previous splitter only split the single
        # largest file; if the fixed remainder still exceeded the hard ceiling,
        # Phase 3 produced one 300k+ token "slice".  This packer instead treats
        # the remaining files as evidence to page through.
        common_budget = min(
            max(16_000, int(soft_tokens * 0.75)),
            max(1, hard_tokens - 8_000),
        )
        common: list[Path] = []
        evidence: list[Path] = []
        for path in existing:
            candidate = [*common, path]
            if estimate_input_tokens(prompt, candidate) <= common_budget:
                common.append(path)
            else:
                evidence.append(path)

        if not evidence:
            return [common]

        common_estimate = estimate_input_tokens(prompt, common)
        if common_estimate >= hard_tokens:
            common = []
            common_estimate = estimate_input_tokens(prompt, common)

        target_tokens = min(soft_tokens, hard_tokens)
        available_tokens = target_tokens - common_estimate - 512
        if available_tokens < 2_000:
            available_tokens = hard_tokens - common_estimate - 512
        available_bytes = max(512, available_tokens * 4)

        slices: list[list[Path]] = []
        for path in evidence:
            for chunk in self._split_file(ctx, phase, target, path, available_bytes):
                pack = [*common, chunk]
                if estimate_input_tokens(prompt, pack) <= hard_tokens:
                    slices.append(pack)
                else:
                    # Last-resort fallback: drop replicated common context for
                    # this chunk instead of emitting an over-budget call.
                    for smaller in self._split_file(
                        ctx,
                        phase,
                        f"{target}_{path.stem}",
                        chunk,
                        max(512, (hard_tokens - prompt_estimate - 512) * 4),
                    ):
                        slices.append([smaller])

        return slices or [common]

    @staticmethod
    def _split_file(ctx: DocGenContext, phase: str, target: str, path: Path, max_bytes: int) -> list[Path]:
        if not path.exists() or path.stat().st_size <= max_bytes:
            return [path]
        text = path.read_text(encoding="utf-8", errors="replace")
        split_dir = transient_dir(ctx.staging_dir, "context_slices", phase)
        chunks: list[Path] = []
        start = 0
        idx = 1
        overlap = min(2_000, max_bytes // 10)
        while start < len(text):
            end = min(len(text), start + max_bytes)
            newline = text.rfind("\n## ", start, end)
            if newline > start + max_bytes // 3:
                end = newline
            body = text[start:end]
            chunk = split_dir / f"{CustomHarnessProvider._safe_name(target)}_{path.stem}_slice_{idx}.md"
            transient_write(
                chunk,
                f"# Context slice {idx} from {path.name}\n\n{body}",
            )
            chunks.append(chunk)
            if end >= len(text):
                break
            start = max(0, end - overlap)
            idx += 1
        return chunks

    @staticmethod
    def _safe_name(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)

    def _enterprise_gate_report(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        provenance: "CoverageReport | None" = None,
    ) -> dict:
        from mcp_server.providers.custom.models import CoverageReport as _CR  # noqa: F401 — type-check only
        assessment_dir = ctx.staging_dir / "_assessment"
        assessment_dir.mkdir(parents=True, exist_ok=True)

        facts = state.facts_manifest
        reports = {
            "phase2_coverage": ctx.staging_dir / "_analysis" / "_coverage_report.json",
            "phase2_depth": ctx.staging_dir / "_analysis" / "_depth_report.json",
            "phase3_doc_coverage": assessment_dir / "doc_validation_report.json",
            "phase3_doc_depth": assessment_dir / "doc_depth_report.json",
        }

        def from_report(path: Path, remediation: str) -> dict:
            if not path.exists():
                return {"ok": False, "errors": [f"Missing gate report: {path.name}"], "remediation": remediation}
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                return {"ok": False, "errors": [f"Invalid gate report {path.name}: {exc}"], "remediation": remediation}
            ok = bool(payload.get("ok")) and int(payload.get("coverage_count", 0)) == int(payload.get("expected_count", facts.unit_count))
            return {
                "ok": ok,
                "coverage_count": payload.get("coverage_count"),
                "expected_count": payload.get("expected_count"),
                "errors": payload.get("errors", []),
                "warnings": payload.get("warnings", []),
                "remediation": None if ok else remediation,
            }

        redaction = validate_final_redaction(ctx.staging_dir)
        if provenance is None:
            provenance = validate_source_provenance(
                ctx.staging_dir,
                facts,
                required_docs=self._required_docs,
                enforce_default_contract=not self._plugin_mode,
            )
        usefulness = validate_usefulness_rubric(
            ctx.staging_dir,
            facts,
            required_docs=self._required_docs,
            enforce_default_contract=not self._plugin_mode,
        )
        reviewers = self._run_internal_reviewers(ctx, state)
        reviewer_ok = all(not finding.get("critical") for report in reviewers for finding in report["findings"])
        pack_quality_report = None
        if self._plugin_mode and self._plugin_manifest is not None:
            pack_quality_report = validate_pack_quality(ctx.staging_dir, self._plugin_manifest)
            (assessment_dir / "pack_quality_report.json").write_text(
                json.dumps(pack_quality_report, indent=2, default=str),
                encoding="utf-8",
            )

        quality_report = None
        quality_score = None
        if self._plugin_mode:
            quality_report = {
                "skipped": True,
                "reason": "Plugin-pack run validates against plugin manifest instead of default full-document quality rubric.",
            }
            quality_score = config.doc_generation_quality_threshold
        else:
            try:
                from mcp_server.services.doc_quality_scorer import score_project

                quality_report = score_project(ctx.project_id, docs_dir=ctx.staging_dir).to_dict()
                quality_score = quality_report["overall"]
            except Exception as exc:
                logger.warning("score_project failed for %s", ctx.project_id, exc_info=True)
                quality_report = {"error": str(exc)}

        gates = {
            "valid_fresh_scan": {
                "ok": (config.intodoc_output_dir / ctx.project_id / "scan_result.json").exists(),
                "errors": [] if (config.intodoc_output_dir / ctx.project_id / "scan_result.json").exists() else ["Missing scan_result.json"],
                "remediation": "Run a fresh scan before generating publishable docs.",
            },
            "complete_evidence_coverage": from_report(reports["phase2_coverage"], "Repair Phase 2 unit coverage gaps."),
            "phase2_analysis_depth": from_report(reports["phase2_depth"], "Repair shallow Phase 2 analysis with source-backed details."),
            "phase3_doc_coverage": from_report(reports["phase3_doc_coverage"], "Repair docs until every canonical unit and obligation is covered."),
            "phase3_doc_depth": from_report(reports["phase3_doc_depth"], "Expand shallow docs with actionable architecture, flow, debug, and change guidance."),
            "final_redaction": {
                "ok": redaction.ok,
                "errors": redaction.errors,
                "remediation": None if redaction.ok else "Remove or redact secret-like values from docs and persisted artifacts.",
            },
            "source_provenance": {
                "ok": provenance.ok,
                "coverage_count": provenance.coverage_count,
                "expected_count": provenance.expected_count,
                "errors": provenance.errors,
                "remediation": None if provenance.ok else "Tie important claims and unit entries to source artifacts or scanner evidence.",
            },
            "reviewer_approval": {
                "ok": reviewer_ok,
                "errors": [
                    finding["message"]
                    for report in reviewers
                    for finding in report["findings"]
                    if finding.get("critical")
                ],
                "remediation": None if reviewer_ok else "Address critical Product Owner, Technical, and QA/Security reviewer findings.",
            },
            "quality_threshold": {
                "ok": quality_score is not None and quality_score >= config.doc_generation_quality_threshold,
                "score": quality_score,
                "threshold": config.doc_generation_quality_threshold,
                "errors": [] if quality_score is not None else [str(quality_report.get("error", "quality scorer failed"))],
                "remediation": None if quality_score is not None and quality_score >= config.doc_generation_quality_threshold else "Increase document specificity, expected sections, evidence, and actionable workflows.",
            },
            "pack_quality": {
                "ok": True if pack_quality_report is None else bool(pack_quality_report.get("effective_ok")),
                "mode": None if pack_quality_report is None else pack_quality_report.get("mode"),
                "configured": False if pack_quality_report is None else pack_quality_report.get("configured", False),
                "errors": [] if pack_quality_report is None else pack_quality_report.get("errors", []),
                "warnings": [] if pack_quality_report is None else pack_quality_report.get("warnings", []),
                "remediation": (
                    None
                    if pack_quality_report is None or pack_quality_report.get("effective_ok")
                    else "Repair generated pack docs to satisfy plugin.yaml quality_gates."
                ),
            },
            "usefulness_rubric": {
                "ok": usefulness.ok,
                "errors": usefulness.errors,
                "remediation": None if usefulness.ok else "Add developer onboarding, change, flow, runtime, debugging, testing, rollback, and unknowns guidance.",
            },
            "rag_sync": {
                "ok": True,
                "errors": [],
                "remediation": None,
                "note": "Pre-publish gate; final RAG sync is recorded by the API background task after swap.",
            },
        }
        report = {
            "mode": "enterprise" if ctx.enterprise_mode else "draft",
            "client_ready": ctx.enterprise_mode and all(entry.get("ok") for entry in gates.values()),
            "gates": gates,
            "reviewers": reviewers,
            "quality": quality_report,
            "generated_at": time.time(),
        }
        (assessment_dir / "enterprise_gates.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        return report

    @staticmethod
    def _term_is_covered(text_lower: str, term: str) -> bool:
        """True if term is substantively present — in a heading, or mentioned more than once.
        A single bare mention (e.g. "no onboarding guidance found") does not count as covered."""
        if term not in text_lower:
            return False
        if any(term in line for line in text_lower.splitlines() if line.lstrip().startswith("#")):
            return True
        return text_lower.count(term) > 1

    def _run_internal_reviewers(self, ctx: DocGenContext, state: HarnessRunState) -> list[dict]:
        if self._plugin_mode:
            reports = [
                {
                    "reviewer": "Plugin Pack Reviewer",
                    "findings": [],
                    "note": "Plugin-pack run uses manifest-scoped publish gates; default full-document reviewer rubric is not blocking.",
                }
            ]
            for report in reports:
                debug_write(
                    debug_dir(ctx.staging_dir, "_assessment", "reviewers", f"{self._safe_name(report['reviewer'])}.json"),
                    json.dumps(report, indent=2),
                )
            return reports

        all_text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in sorted(ctx.staging_dir.glob("*.md"))
        ).lower()

        covered = self._term_is_covered

        def finding(message: str, critical: bool = True) -> dict:
            return {"message": message, "critical": critical}

        reports = [
            {
                "reviewer": "Product Owner",
                "findings": [] if all(covered(all_text, t) for t in ("onboarding", "common tasks", "change impact")) else [
                    finding("Docs do not yet prove client-useful onboarding, common tasks, and change-impact guidance.")
                ],
            },
            {
                "reviewer": "Technical Reviewer",
                "findings": [] if all(covered(all_text, t) for t in ("source artifact", "dependency", "debug", "rollback")) else [
                    finding("Docs lack enough source artifact, dependency, debug, or rollback detail for developer change work.")
                ],
            },
            {
                "reviewer": "QA/Security Reviewer",
                "findings": [] if all(covered(all_text, t) for t in ("evidence", "redacted", "unknown")) else [
                    finding("Docs lack explicit evidence, redaction posture, or unknowns needed for safe delivery.")
                ],
            },
        ]
        for report in reports:
            debug_write(
                debug_dir(ctx.staging_dir, "_assessment", "reviewers", f"{self._safe_name(report['reviewer'])}.json"),
                json.dumps(report, indent=2),
            )
        return reports

    async def _write_audit_bundle(self, ctx: DocGenContext, state: HarnessRunState, gate_report: dict) -> None:
        audit_dir = ctx.staging_dir / "_audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        hashes: dict[str, str] = {}
        try:
            import hashlib

            for path in sorted(ctx.staging_dir.glob("*.md")):
                hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            pass
        (audit_dir / "enterprise_gates.json").write_text(json.dumps(gate_report, indent=2, default=str), encoding="utf-8")
        (audit_dir / "final_doc_hashes.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")

    @staticmethod
    def _archive_engineering_artifacts(ctx: DocGenContext, state: HarnessRunState) -> list[str]:
        facts_dir = ctx.staging_dir / "_facts"
        archived: list[str] = []
        for name in sorted(state.engineering_fact_artifacts):
            source = ctx.staging_dir / name
            if not source.exists() or not source.is_file():
                continue
            facts_dir.mkdir(parents=True, exist_ok=True)
            target = facts_dir / name
            if target.exists():
                target.unlink()
            shutil.move(str(source), str(target))
            archived.append(target.relative_to(ctx.staging_dir).as_posix())
        return archived

    @staticmethod
    def _critic_prompt(state: HarnessRunState, family: str, doc_names: list[str]) -> str:
        return f"""\
You are a documentation critic. Compare the supplied docs against the facts manifest,
scorecard, investigation evidence, and reference docs. Return JSON only.

Required JSON shape:
{{"expansions":[{{"doc_name":"02_COMPONENT_INVENTORY","heading":"Evidence gap","markdown":"...","units":["Exact Unit"]}}]}}

Rules:
- Only expand weak or missing factual sections.
- Use exact canonical unit names from the facts manifest.
- Do not introduce counts that conflict with {state.facts_manifest.unit_count} {state.facts_manifest.canonical_unit_label}.
- Markdown must cite concrete investigation snippets or scanner observations.
- If nothing needs expansion, return {{"expansions":[]}}.

Doc family: {family}
Allowed docs:
{chr(10).join(f"- {doc}" for doc in doc_names)}
"""

    def _apply_validated_expansions(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        text: str,
        allowed_docs: set[str],
    ) -> int:
        raw = text.strip()
        if raw.startswith("```"):
            raw = raw.removeprefix("```json").removeprefix("```").strip()
            raw = raw.removesuffix("```").strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return 0
        expansions = data.get("expansions")
        if not isinstance(expansions, list):
            return 0
        unit_names = state.facts_manifest.unit_names()
        applied = 0
        for item in expansions:
            if not isinstance(item, dict):
                continue
            doc_name = str(item.get("doc_name") or "")
            if doc_name not in allowed_docs:
                continue
            path = ctx.staging_dir / f"{doc_name}.md"
            if not path.exists():
                continue
            markdown = str(item.get("markdown") or "").strip()
            heading = str(item.get("heading") or "Critic Expansion").strip()
            units = item.get("units") or []
            if not markdown or len(markdown) < 80 or not isinstance(units, list):
                continue
            if any(str(unit) not in unit_names for unit in units):
                continue
            if units and not any(str(unit) in markdown for unit in units):
                continue
            if str(state.facts_manifest.unit_count) not in markdown and "canonical inventory" in markdown.lower():
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            path.write_text(
                f"{content.rstrip()}\n\n## {heading}\n\n{markdown}\n",
                encoding="utf-8",
            )
            applied += 1
        return applied

    @staticmethod
    def _unit_catalog_markdown(state: HarnessRunState) -> str:
        lines = ["| Unit | Group | Type | Components |", "|------|-------|------|------------|"]
        for unit in state.facts_manifest.units:
            lines.append(
                f"| {unit.name} | {unit.group} | {unit.unit_type} | {unit.component_count} |"
            )
        return "\n".join(lines)

    @staticmethod
    def _cross_cutting_facts_markdown(state: HarnessRunState) -> str:
        """Deterministic whole-partition facts shared by every Phase 3 writer.

        Kills cross-slice contradictions at the source (verified failure: one
        doc set claimed 0, 1 AND 2 tLogCatcher jobs because each evidence
        slice was blind to the others): every doc call -- and every slice of a
        partitioned doc -- receives the same scanner-derived component census,
        group sizes, and key systems. Python renders manifest facts verbatim;
        no prose.
        """
        facts = state.facts_manifest
        units_containing: dict[str, int] = {}
        for unit in facts.units:
            for component_type in unit.component_types:
                units_containing[component_type] = units_containing.get(component_type, 0) + 1
        lines = [
            "# Cross-Cutting Facts (deterministic, whole partition)",
            "",
            "Scanner-derived facts of record for this partition. Quote these",
            "counts as-is; never recompute them. A claim that a component type",
            "is absent from the partition is FALSE if it appears in this census.",
            "",
            "## Partition Scope",
            "",
            f"- Canonical inventory: {facts.unit_count} {facts.canonical_unit_label}",
            "- Groups: "
            + (", ".join(f"{g}={n}" for g, n in sorted(facts.groups.items())) or "none"),
            "",
            "## Component Census",
            "",
            "| Component Type | Total Occurrences | Units Containing It |",
            "|----------------|-------------------|---------------------|",
        ]
        totals = dict(facts.component_counts)
        for component_type, unit_hits in sorted(units_containing.items()):
            totals.setdefault(component_type, 0)
        for component_type in sorted(totals):
            lines.append(
                f"| {component_type} | {totals[component_type]} | "
                f"{units_containing.get(component_type, 0)} |"
            )
        lines.extend([
            "",
            "## Key Systems (scanner landscape)",
            "",
        ])
        lines.extend(f"- {system}" for system in facts.key_systems)
        if not facts.key_systems:
            lines.append("- None identified in scanner facts")
        return "\n".join(lines) + "\n"

    def _write_cross_cutting_facts(self, ctx: DocGenContext, state: HarnessRunState) -> Path | None:
        """Write `_analysis/_cross_cutting_facts.md` once per run (never raises)."""
        try:
            path = ctx.staging_dir / "_analysis" / "_cross_cutting_facts.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self._cross_cutting_facts_markdown(state), encoding="utf-8")
            return path
        except OSError:
            return None

    @staticmethod
    def _load_investigation_artifact(investigation_dir: Path, chunk_name: str) -> InvestigationReport | None:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", chunk_name)
        path = investigation_dir / f"{safe_name}.json"
        if not path.exists():
            return None
        try:
            return InvestigationReport.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    @staticmethod
    def _merge_investigation_reports(
        group: str,
        reports: list[InvestigationReport],
    ) -> InvestigationReport:
        evidence = []
        skipped = {}
        source_groups = []
        tool_rounds = 0
        invalid_retries = 0
        input_tokens = 0
        output_tokens = 0
        seen = set()
        for report in reports:
            source_groups.extend(report.source_groups or [report.group])
            for ev in report.evidence:
                key = (
                    ev.unit_name,
                    ev.category,
                    ev.source_file,
                    ev.line_range,
                    ev.chunk_ref,
                    ev.snippet,
                )
                if key not in seen:
                    seen.add(key)
                    evidence.append(ev)
            skipped.update(report.skipped_units)
            tool_rounds += report.tool_rounds
            invalid_retries += report.invalid_retries
            input_tokens += report.input_tokens
            output_tokens += report.output_tokens
        return InvestigationReport(
            group=group,
            source_groups=sorted(set(source_groups)),
            evidence=evidence,
            skipped_units=dict(sorted(skipped.items())),
            tool_rounds=tool_rounds,
            invalid_retries=invalid_retries,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    @staticmethod
    def _investigation_context(ctx: DocGenContext, group: str) -> list[Path]:
        investigation_dir = ctx.staging_dir / "_investigation"
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in group)
        files = [investigation_dir / f"{safe}.md", investigation_dir / "_summary.md"]
        return [path for path in files if path.exists()]

    @staticmethod
    def _provider_options(provider: ModelProvider, *, phase: str = "extraction") -> dict:
        provider_name = getattr(provider, "provider_name", "")
        if provider_name == "ollama":
            return {"options": {"num_ctx": 32768}}
        if provider_name == "deepseek" and str(getattr(provider, "model_name", "")).startswith("deepseek-v4"):
            # DeepSeek V4 defaults to thinking mode. Make phase behavior
            # explicit so a vendor default cannot silently consume the final
            # answer budget. Only bounded, higher-level synthesis opts in.
            thinking_phases = {
                "phase2_synthesis",
                "business_topology",
                "project_synthesis",
            }
            return {
                "thinking": {
                    "type": "enabled" if phase in thinking_phases else "disabled"
                }
            }
        return {}

    @staticmethod
    def _default_evidence_categories() -> list[str]:
        return [
            "inventory",
            "components",
            "data_flow",
            "transformation",
            "business_rules",
            "external_systems",
            "error_handling",
            "environment_config",
            "risk_flags",
            "dependencies",
            "security",
            "operations",
        ]

    def _validate_phase3_doc(
        self, text: str, doc_name: str, state: HarnessRunState | None = None
    ) -> dict:
        """Markdown sanity plus fabrication checks against the target evidence bundle.

        Runs pre-acceptance so failures land in ``validation_gaps`` and drive
        the repair attempt. Unit-scoped docs additionally enforce the
        protected-fact ledger (P1.5: facts copied verbatim, never recomputed);
        group/summary docs get the unit-count recompute check.
        """
        report = self._validate_agent_markdown(text, [], min_bytes=200)
        required_sections = self._plugin_required_sections(doc_name)
        if required_sections:
            headings = {
                line.lstrip("#").strip().casefold()
                for line in text.splitlines()
                if line.lstrip().startswith("#")
            }
            missing = [
                section for section in required_sections
                if section.casefold() not in headings
            ]
            if missing:
                errors = list(report.get("errors") or [])
                errors.append(
                    "Missing required Pack sections: " + ", ".join(missing)
                )
                report = {**report, "ok": False, "errors": errors}
        # Post-assembly guard (verified failure: slice restarts shipped the
        # same document structure 11x): repeated H1/H2 structure is deniable
        # here because _final_doc_content dedupes deterministically -- but a
        # doc that still repeats structure after merge must drive a repair.
        structure_errors = duplicate_heading_errors(text)
        if structure_errors and duplicate_heading_errors(dedupe_document(text)):
            errors = list(report.get("errors") or []) + structure_errors
            report = {**report, "ok": not errors, "errors": errors}
        evidence = self._target_evidence.get(doc_name)
        if evidence is None:
            if state is not None and self._plugin_manifest is None:
                facts = state.facts_manifest
                # ADVISORY ONLY -- never blocks, never triggers a repair pass.
                # The check cannot reliably distinguish a project TOTAL from a
                # legitimate per-group / per-layer / route sub-count (verified:
                # it false-flagged "18 jobs in the fourps group", "16 routes
                # (16 jobs)", "MonitoringLogger used by ~20 jobs"), and jobs vs
                # routes vs units are used interchangeably in the prose. The
                # repair it used to force never fixed anything -- docs shipped
                # regardless -- so it only annotates now.
                count_notes = unit_count_claim_errors(
                    text,
                    unit_count=facts.unit_count,
                    canonical_unit_label=facts.canonical_unit_label,
                    allowed_counts=frozenset(facts.groups.values()),
                )
                if count_notes:
                    report = {
                        **report,
                        "warnings": list(report.get("warnings") or []) + count_notes,
                    }
            return report
        errors = list(report.get("errors") or [])
        errors.extend(
            component_reference_errors(
                text, evidence.component_refs, source_label="the target evidence bundle"
            )
        )
        errors.extend(availability_claim_errors(text, evidence.categories))
        if doc_name in self._per_job_doc_units and self._plugin_manifest is None:
            errors.extend(protected_fact_errors(text, evidence.protected_facts))
        return {**report, "ok": not errors, "errors": errors}

    def _plugin_required_sections(self, doc_name: str) -> list[str]:
        """Return the authored section contract for an expanded plugin doc."""
        if self._plugin_manifest is None:
            return []
        base = doc_name.split("__", 1)[0]
        required: list[str] = []
        for doc in getattr(self._plugin_manifest, "docs", None) or []:
            if getattr(doc, "name", "") == base:
                required.extend(
                    getattr(doc, "required_sections", None)
                    or getattr(doc, "sections", None)
                    or []
                )
                break
        quality_gates = getattr(self._plugin_manifest, "quality_gates", None)
        if quality_gates is not None:
            required.extend(
                (getattr(quality_gates, "required_sections", None) or {}).get(base, [])
            )
        # Preserve manifest order while avoiding duplicate repair instructions.
        return list(dict.fromkeys(required))

    @staticmethod
    def _merge_component_ref_errors(
        report: dict,
        text: str,
        allowed_refs: frozenset[str],
        source_label: str,
        job_refs: dict[str, frozenset[str]] | None = None,
    ) -> dict:
        """Append component-fabrication errors to an existing validation report.

        When ``job_refs`` is non-empty (a bin-packed task with more than one
        job), references are checked per job-attributed span against that
        job's own evidence, falling back to ``allowed_refs`` (the combined
        task evidence) only for content not attributable to a single job.
        Checking the whole output against the combined evidence alone would
        let a component real for one job in the batch pass when misattributed
        to a sibling job in the same batch -- the verified failure this
        closes (see ``job_scoped_component_reference_errors``).
        """
        if job_refs:
            ref_errors = job_scoped_component_reference_errors(
                text, job_refs, allowed_refs, source_label=source_label
            )
        else:
            ref_errors = component_reference_errors(text, allowed_refs, source_label=source_label)
        if not ref_errors:
            return report
        errors = list(report.get("errors") or []) + ref_errors
        return {**report, "ok": False, "errors": errors}

    @classmethod
    def _validate_phase2_task_output(
        cls,
        text: str,
        required_units: list[str],
        *,
        platform: str,
        require_sections: bool,
        contract: "PackContract | None" = None,
    ) -> dict:
        """Reject structurally incomplete task output before group assembly.

        A response may be non-empty and mention every unit while still omitting
        late required sections. Accepting that output simply moves the failure to
        synthesis, where missing evidence cannot safely be reconstructed.

        Contract-driven (recon S16): with a ``contract`` the required set is
        ``contract.hard_required_names`` (empty for v1 packs, GROUP_SUMMARY
        floor for v2 packs, the full legacy set for the default contract) and
        the parser recognises the contract's own sections. Pack-declared
        ``required: true`` sections are deliberately NOT enforced here: their
        absence is a pack-contract violation handled by the fail-loudly path
        (``_record_section_contract_failure``), and rejecting the task for it
        would route real analysis into the retry -> salvage-stub machinery.
        """

        report = cls._validate_agent_markdown(text, required_units)
        if not require_sections:
            return report
        if contract is not None:
            required: tuple[str, ...] = contract.hard_required_names
            if not required:
                return report
        else:
            required = (
                (*REQUIRED_SECTION_NAMES, GROUP_SUMMARY_SECTION)
                if platform == "talend"
                else (GROUP_SUMMARY_SECTION,)
            )
        parsed = cls._parse_phase2_sections(text, contract=contract)
        missing_sections = [name for name in required if name not in parsed]
        if not missing_sections:
            return report
        errors = list(report.get("errors") or [])
        errors.append(
            "artifact is missing required Phase 2 sections: "
            + ", ".join(missing_sections)
        )
        return {
            **report,
            "ok": False,
            "errors": errors,
            "missing_sections": missing_sections,
        }

    @staticmethod
    def _allowed_component_refs(files: list[Path]) -> frozenset[str]:
        """Union of component references present in the given evidence files."""
        refs: set[str] = set()
        for path in files:
            try:
                refs |= extract_component_refs(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
        return frozenset(refs)

    @staticmethod
    def _validate_agent_markdown(text: str, required_units: list[str], min_bytes: int = 500) -> dict:
        body = text.strip()
        errors: list[str] = []
        if len(body.encode("utf-8")) < min_bytes:
            errors.append(f"artifact too shallow: {len(body.encode('utf-8'))} bytes < {min_bytes}")
        missing = [unit for unit in required_units if unit not in body]
        if missing:
            errors.append(f"artifact does not mention assigned units: {', '.join(missing[:20])}")
        if not re.search(r"^#{1,4}\s+\S+", body, re.MULTILINE):
            errors.append("artifact has no markdown headings")
        return {"ok": not errors, "errors": errors, "missing_units": missing}

    @staticmethod
    def _existing(directory: Path, names: list[str]) -> list[Path]:
        return [directory / name for name in names if (directory / name).exists()]

    # Maps evidence bundle category names → investigation category name substrings.
    # The investigator prompt uses these as section/category labels in evidence items.
    _INV_CATEGORY_MAP: dict[str, set[str]] = {
        "sql_queries":       {"sql_query", "join_relationship", "fk_relationship", "cross_database_usage"},
        "ddl_schema":        {"ddl_artifact"},
        "orm_entities":      {"orm_call"},
        "db_connections":    {"db_connection"},
        "config_references": {"db_connection", "config"},
        "routes":            {"route", "endpoint"},
        "handlers":          {"handler", "listener"},
        "auth":              {"auth"},
        "source_summary":    set(),  # never useful for Phase 3 in pack mode
    }
    _INV_BUNDLE_CAP_CHARS = 40_000

    def _build_pack_investigation_bundle(
        self, ctx: DocGenContext, doc_name: str
    ) -> "Path | None":
        """Return a filtered, size-capped investigation evidence file for one pack doc.

        Reads all group investigation markdown files, keeps only evidence items whose
        category matches the doc's evidence profile, and writes a single bundle file
        capped at _INV_BUNDLE_CAP_CHARS. Returns None when no relevant evidence exists.
        """
        if self._plugin_manifest is None:
            return None
        doc_def = self._pack_doc_definition(doc_name)
        profile_name = getattr(doc_def, "evidence_profile", None) if doc_def is not None else None
        profiles = getattr(self._plugin_manifest, "evidence_profiles", {}) or {}
        profile = profiles.get(profile_name) if profile_name else None
        included_bundle_cats = set(getattr(profile, "include", [])) if profile else set()

        # Build the set of investigation category tokens to keep
        inv_cats: set[str] = set()
        for bc in included_bundle_cats:
            inv_cats.update(self._INV_CATEGORY_MAP.get(bc, set()))
        if not inv_cats:
            # No mapping — pass everything (unknown pack categories)
            inv_cats = None  # type: ignore[assignment]

        inv_dir = ctx.staging_dir / "_investigation"
        bundle_dir = inv_dir / "_bundles"
        bundle_dir.mkdir(exist_ok=True)
        bundle_path = bundle_dir / f"{doc_name}.md"

        lines: list[str] = [
            f"# Investigation Evidence: {doc_name}",
            "",
            f"Filtered categories: {', '.join(sorted(included_bundle_cats)) or 'all'}",
            f"Cap: {self._INV_BUNDLE_CAP_CHARS // 1000}k chars",
            "",
        ]
        total_items = 0
        total_chars = sum(len(ln) for ln in lines)

        for inv_file in sorted(inv_dir.glob("*.md")):
            if inv_file.name.startswith("_"):
                continue
            text = inv_file.read_text(encoding="utf-8", errors="replace")
            # Parse the investigation markdown: sections headed
            # "### UnitName - CATEGORY" contain one evidence item.
            current_heading: str | None = None
            current_block: list[str] = []

            def _flush(heading: str, block: list[str]) -> None:
                nonlocal total_items, total_chars
                if total_chars >= self._INV_BUNDLE_CAP_CHARS:
                    return
                # Extract category from heading: "UnitName - CATEGORY_NAME"
                cat_part = heading.split(" - ", 1)[-1].strip().lower().replace(" ", "_")
                if inv_cats is not None and not any(
                    cat_part.startswith(ic) or ic.startswith(cat_part)
                    for ic in inv_cats
                ):
                    return
                block_text = "\n".join(block)
                remaining = self._INV_BUNDLE_CAP_CHARS - total_chars
                lines.append(f"### {heading}")
                lines.append(block_text[:remaining])
                lines.append("")
                total_items += 1
                total_chars += len(heading) + len(block_text[:remaining]) + 4

            for line in text.splitlines():
                if line.startswith("### "):
                    if current_heading is not None:
                        _flush(current_heading, current_block)
                    current_heading = line[4:]
                    current_block = []
                elif current_heading is not None:
                    current_block.append(line)
            if current_heading is not None:
                _flush(current_heading, current_block)

            if total_chars >= self._INV_BUNDLE_CAP_CHARS:
                lines.append(f"*[Investigation bundle capped at {self._INV_BUNDLE_CAP_CHARS // 1000}k chars]*")
                break

        if total_items == 0:
            return None

        bundle_path.write_text("\n".join(lines), encoding="utf-8")
        return bundle_path

    def _reconcile_sql_usage_classification(self, ctx: DocGenContext) -> int:
        """Make SQL-used table classifications deterministic in generated docs.

        The model can write one section from DDL evidence and another from SQL
        usage evidence, leaving stitched docs with contradictions like
        `dbo.CUSTINVOICETRANS = PRESENT_IN_DDL_ONLY` despite sql_usage proving
        InvoiceFlatService reads it. This pass fixes only table-specific rows
        that already name a SQL-used table and already mark it DDL-only.
        """

        sql_tables = _sql_usage_tables(ctx.staging_dir / "sql_usage.json")
        if not sql_tables:
            return 0

        changes: list[dict[str, Any]] = []
        for path in sorted(ctx.staging_dir.glob("*.md")):
            if path.name in {"sql_usage.md", "schema_dictionary.md", "engineering_facts.md"}:
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            changed = False
            for idx, line in enumerate(lines):
                for table in sql_tables:
                    short = table.split(".")[-1]
                    if not _line_marks_table_ddl_only(line, table, short):
                        continue
                    new_line = _mark_line_sql_used(line)
                    if new_line != line:
                        lines[idx] = new_line
                        changed = True
                        changes.append({
                            "doc": path.name,
                            "line": idx + 1,
                            "table": table,
                            "before": line[:500],
                            "after": new_line[:500],
                        })
                    break
            if changed:
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        report = {
            "changes": changes,
            "changed_count": len(changes),
            "sql_table_count": len(sql_tables),
        }
        debug_write(
            debug_dir(ctx.staging_dir, "_assessment", "sql_usage_reconciliation.json"),
            json.dumps(report, indent=2),
        )
        return len(changes)

    def _validate_sql_usage_publication(self, ctx: DocGenContext) -> list[str]:
        """Block publishable docs when deterministic SQL evidence is lost."""

        sql_usage_path = ctx.staging_dir / "sql_usage.json"
        facts_path = ctx.staging_dir / "engineering_facts.json"
        errors: list[str] = []
        report: dict[str, Any] = {
            "ok": True,
            "checks": [],
            "errors": errors,
        }

        queried_facts = 0
        if facts_path.exists():
            try:
                facts_payload = json.loads(facts_path.read_text(encoding="utf-8"))
                queried_facts = sum(
                    1
                    for item in facts_payload.get("usage", []) or []
                    if isinstance(item, dict)
                    and (
                        (item.get("usage_type") == "queried" and _fact_has_sql_content(item))
                        or ":sql_" in str(item.get("id") or "")
                        or re.search(r"\b(select|insert|update|delete|merge)\b", str(item.get("name") or ""), re.I)
                    )
                )
            except (OSError, json.JSONDecodeError):
                queried_facts = 0

        records: list[dict[str, Any]] = []
        if sql_usage_path.exists():
            try:
                payload = json.loads(sql_usage_path.read_text(encoding="utf-8"))
                records = [item for item in payload.get("records", []) or [] if isinstance(item, dict)]
            except (OSError, json.JSONDecodeError):
                errors.append("SQL usage gate: sql_usage.json is present but invalid JSON")

        if queried_facts and not records:
            errors.append("SQL usage gate: engineering SQL/query facts exist but no structured SQL usage records were produced")

        sql_tables = sorted({
            str(table.get("table") or "")
            for record in records
            for table in record.get("tables", []) or []
            if isinstance(table, dict) and table.get("table")
        })
        report["checks"].append({
            "queried_facts": queried_facts,
            "sql_usage_records": len(records),
            "sql_tables": sql_tables,
        })

        if sql_tables:
            doc_lines: list[str] = []
            for path in sorted(ctx.staging_dir.glob("*.md")):
                if path.name in {"sql_usage.md", "schema_dictionary.md", "engineering_facts.md"}:
                    continue
                doc_lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
            for table in sql_tables:
                short = table.split(".")[-1]
                if any(_line_marks_table_ddl_only(line, table, short) for line in doc_lines):
                    errors.append(f"SQL usage gate: {table} has SQL usage evidence but final docs mark it as DDL-only")

        report["ok"] = not errors
        debug_write(
            debug_dir(ctx.staging_dir, "_assessment", "sql_usage_gate.json"),
            json.dumps(report, indent=2),
        )
        return errors

    def _prepare_pack_artifacts(self, ctx: DocGenContext) -> None:
        if self._plugin_manifest is None:
            return
        self._pack_evidence_paths = EvidenceBundleBuilder(ctx.staging_dir, self._plugin_manifest).build_all()
        plans_dir = ctx.staging_dir / "_plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        docs = getattr(self._plugin_manifest, "docs", None) or []
        for doc in docs:
            profile_name = getattr(doc, "evidence_profile", None)
            payload = {
                "doc": doc.name,
                "pack": {
                    "name": getattr(self._plugin_manifest, "name", ""),
                    "version": getattr(self._plugin_manifest, "version", ""),
                },
                "evidence_profile": profile_name,
                "evidence_bundle_paths": self._pack_evidence_paths.get(profile_name, {}) if profile_name else {},
                "required_sections": list(getattr(doc, "required_sections", []) or getattr(doc, "sections", []) or []),
                "required_evidence": list(getattr(doc, "required_evidence", []) or []),
                "required_outputs": list(getattr(doc, "required_outputs", []) or []),
                "unknown_policy": getattr(getattr(self._plugin_manifest, "validation", None), "unknown_policy", "explicit"),
                "validation_mode": getattr(getattr(self._plugin_manifest, "validation", None), "mode", "blocking"),
            }
            (plans_dir / f"{doc.name}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    async def _prepare_evidence_registry(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        log_cb: LogCallback,
    ) -> None:
        resolution = resolve_evidence_agents(self._plugin_manifest, self.plugin_pack_dir)
        if resolution.errors:
            raise ValueError("Invalid evidence agent configuration: " + "; ".join(resolution.errors))
        if not resolution.agents:
            return
        runner = EvidenceAgentRunner(ctx.staging_dir)
        provider = self._provider_for_phase("investigation")
        agent_results = []
        for agent in resolution.agents:
            run_result = await runner.run_agent(agent, provider)
            agent_results.append(run_result)
            state.calls.append(ProviderCallLog(phase="evidence_agents", target=agent.id, result=run_result.result))
            if run_result.errors:
                await log_cb(
                    ctx.project_id,
                    f"[custom] evidence agent {agent.id}: {len(run_result.claims)} claim(s), "
                    f"{len(run_result.errors)} validation issue(s)",
                    "warning",
                )
            else:
                await log_cb(
                    ctx.project_id,
                    f"[custom] evidence agent {agent.id}: {len(run_result.claims)} claim(s)",
                    "info",
                )
        self._evidence_registry_paths = EvidenceRegistryBuilder(ctx.staging_dir).build(
            resolution.agents,
            agent_results=agent_results,
        )

    def _pack_doc_definition(self, doc_name: str):
        return self._plugin_doc_definitions.get(doc_name)

    def _expand_per_job_docs(self, facts) -> None:
        """Expand documents marked per-unit into one file per scanned unit.

        Plugin packs use the ``per_job`` flag on their manifest docs (existing
        behavior, unchanged). Standard-catalog runs expand entries marked
        ``per_unit`` (e.g. 10_UNIT_REFERENCE) the same way: the per-unit docs
        become the Phase 3 unit contract, are written first, and the catalog
        summary docs synthesize from them (P1.4/P1.6).
        """
        if self._plugin_manifest is None:
            self._expand_catalog_per_unit_docs(facts)
            return
        base_docs = list(getattr(self._plugin_manifest, "docs", None) or [])
        if not any(getattr(doc, "per_job", False) for doc in base_docs):
            return

        expanded: list[object] = []
        self._per_job_doc_units = {}
        for base in base_docs:
            if not getattr(base, "per_job", False):
                expanded.append(base)
                continue
            for unit in facts.units:
                suffix = f"{self._safe_name(unit.group)}__{self._safe_name(unit.name)}"
                doc_name = f"{base.name}__{suffix}"
                expanded_doc = base.model_copy(update={"name": doc_name, "per_job": False})
                expanded.append(expanded_doc)
                self._per_job_doc_units[doc_name] = unit.name

        self._required_docs = [doc.name for doc in expanded]
        self._plugin_doc_definitions = {doc.name: doc for doc in expanded}
        self._doc_assignments = {"custom": list(self._required_docs)}
        self._doc_bundle_map = {doc: "custom" for doc in self._required_docs}
        self._doc_section_requirements_extra = {
            doc.name: list(getattr(doc, "required_sections", []) or []) for doc in expanded
        }
        # v2 packs: carry the base doc's Phase 2 section routing onto its
        # per-unit expansions so doc bundles keep the declared lens
        # (harness.py:704 fix, persisted through expansion -- recon D2).
        if self._pack_contract is not None and self._doc_section_bundle_map:
            base_routing = dict(self._doc_section_bundle_map)
            for doc in expanded:
                if doc.name in base_routing:
                    continue
                base_name = doc.name.split("__", 1)[0]
                if base_name in base_routing:
                    self._doc_section_bundle_map[doc.name] = set(base_routing[base_name])
        quality_gates = getattr(self._plugin_manifest, "quality_gates", None)
        if quality_gates is not None:
            base_sections = dict(getattr(quality_gates, "required_sections", {}) or {})
            base_min_bytes = dict(getattr(quality_gates, "min_doc_bytes", {}) or {})
            sections: dict[str, list[str]] = {}
            min_bytes: dict[str, int] = {}
            for base in base_docs:
                names = [doc.name for doc in expanded if doc.name == base.name or doc.name.startswith(f"{base.name}__")]
                for name in names:
                    if base.name in base_sections:
                        sections[name] = base_sections[base.name]
                    if base.name in base_min_bytes:
                        min_bytes[name] = base_min_bytes[base.name]
            updated_gates = quality_gates.model_copy(update={
                "required_sections": sections,
                "min_doc_bytes": min_bytes,
            })
            self._plugin_manifest = self._plugin_manifest.model_copy(update={
                "docs": expanded,
                "quality_gates": updated_gates,
            })

    def _expand_catalog_per_unit_docs(self, facts) -> None:
        """Expand standard-catalog ``per_unit`` entries into one doc per unit.

        Mirrors the plugin per_job expansion: the base catalog name is replaced
        in ``_required_docs`` by ``{base}__{group}__{unit}`` names; each carries
        the base entry's required sections (via the extra-requirements map the
        doc prompt already prefers) and bundle/phase2-section routing.
        """
        if not config.docgen_unit_docs_enabled:
            return
        per_unit_entries = load_doc_catalog().per_unit_docs
        if not per_unit_entries:
            return
        self._per_job_doc_units = {}
        expanded_required = list(self._required_docs)
        for entry in per_unit_entries:
            for unit in facts.units:
                suffix = f"{self._safe_name(unit.group)}__{self._safe_name(unit.name)}"
                expanded_name = f"{entry.name}__{suffix}"
                if expanded_name in expanded_required:
                    continue
                expanded_required.append(expanded_name)
                self._per_job_doc_units[expanded_name] = unit.name
                self._doc_section_requirements_extra[expanded_name] = list(entry.required_sections)
                self._doc_bundle_map[expanded_name] = entry.bundle
                self._doc_section_bundle_map[expanded_name] = set(entry.phase2_sections)
                self._doc_assignments.setdefault(entry.bundle, [])
                if expanded_name not in self._doc_assignments[entry.bundle]:
                    self._doc_assignments[entry.bundle].append(expanded_name)
        self._required_docs = expanded_required

    def _phase2_shared_context(self, ctx: DocGenContext, facts_path: Path) -> list[Path]:
        preprocess_dir = ctx.staging_dir / "_preprocess"
        pack_dir = ctx.staging_dir / "_assessment" / "prompt_packs"
        files: list[Path] = []
        source_coverage = preprocess_dir / "_source_coverage.json"
        if facts_path.exists():
            files.append(facts_path)
        if self._plugin_mode and self._plugin_manifest is not None:
            # Always include evidence bundles when profiles are declared.
            if getattr(self._plugin_manifest, "evidence_profiles", None):
                for profile_paths in self._pack_evidence_paths.values():
                    md = ctx.staging_dir / profile_paths.get("md", "")
                    if md.exists():
                        files.append(md)
            brief_rel = self._evidence_registry_paths.get("brief") or self._evidence_registry_paths.get("md")
            if brief_rel:
                brief = ctx.staging_dir / brief_rel
                if brief.exists():
                    files.append(brief)
            # Nexus owns the platform analysis policy. It is supplied for every
            # analysis run, including replace-mode lenses; a plugin controls
            # attention and additions, never Talend/Workato/etc. semantics.
            analysis_pack = pack_dir / "_analysis_prompt_pack.md"
            if analysis_pack.exists():
                files.append(analysis_pack)
            else:
                # Fail closed on policy transport: if materialization was
                # unavailable, include the source guide for every analysis
                # policy rather than letting a plugin run without Nexus's
                # platform semantics.
                files.extend(
                    self._phase2_analysis_guide_files(
                        preprocess_dir, ctx.analysis_guide_file
                    )
                )
            return files
        if not source_coverage.exists():
            files.extend(self._engineering_fact_context(ctx))
        quick_scan = ctx.staging_dir / "_assessment" / "quick_scan.md"
        if quick_scan.exists():
            files.append(quick_scan)
        # _source_coverage.json (139 KB) is project-wide metadata with no per-chunk value;
        # removed from static context to prevent 9x repetition across slices.
        brief_rel = self._evidence_registry_paths.get("brief") or self._evidence_registry_paths.get("md")
        if brief_rel:
            brief = ctx.staging_dir / brief_rel
            if brief.exists():
                files.append(brief)
        analysis_pack = pack_dir / "_analysis_prompt_pack.md"
        if analysis_pack.exists():
            files.append(analysis_pack)
        else:
            files.extend(
                self._phase2_analysis_guide_files(
                    preprocess_dir, ctx.analysis_guide_file
                )
            )
        return files

    @staticmethod
    def _phase2_analysis_guide_files(
        preprocess_dir: Path, analysis_guide_file: str
    ) -> list[Path]:
        """Resolve Phase 2 analysis guide source files (single source of truth).

        Mirrors ``prompt_packs._phase2_sources``: prefer the named guide if it
        exists, otherwise glob ``_analysis_prompts*.md``. The sentinel string
        ``_analysis_prompts*.md`` falls through to the glob branch by design.
        """

        if analysis_guide_file:
            guide = preprocess_dir / analysis_guide_file
            if guide.exists():
                return [guide]
            logger.warning("phase2 guide file missing, using glob fallback: %s", guide)
        return sorted(preprocess_dir.glob("_analysis_prompts*.md"))

    @staticmethod
    def _group_units(state: HarnessRunState, group: str) -> list[str]:
        units = [
            u.name for u in state.facts_manifest.units
            if u.group == group or group.startswith(f"{u.group}_part")
        ]
        if not units:
            units = [u.name for u in state.facts_manifest.units if u.group == group]
        return units

    def _resolve_source_root(self, ctx: DocGenContext) -> Path | None:
        """Locate the project's source tree for the non-code walk.

        The scan cache records the absolute ``repo_root`` the scanner walked;
        prefer it when present, else fall back to the standard uploaded-repo
        location. A missing source tree is not an error -- non-code context is
        additive -- so this returns ``None`` and the caller skips silently.
        """
        scan_output_dir = config.intodoc_output_dir / ctx.project_id
        scan_result = scan_output_dir / "scan_result.json"
        if scan_result.exists():
            try:
                repo_root = json.loads(
                    scan_result.read_text(encoding="utf-8", errors="replace")
                ).get("repo_root")
            except (json.JSONDecodeError, OSError):
                repo_root = None
            if repo_root and Path(repo_root).is_dir():
                return Path(repo_root)
        fallback = config.repos_dir / ctx.project_id
        return fallback if fallback.is_dir() else None

    def _ensure_noncode_plan(self, ctx: DocGenContext) -> NonCodeAssociationPlan | None:
        """Build (once per staging dir) the Tier-3 non-code association plan.

        Deterministic, no LLM in the path. Any failure is logged and degrades to
        no non-code context -- documentation still generates from scanner facts.
        """
        if self._noncode_plan_staging == ctx.staging_dir:
            return self._noncode_plan or None  # cached (plan or False)
        self._noncode_plan_staging = ctx.staging_dir
        self._noncode_plan = False
        source_root = self._resolve_source_root(ctx)
        if source_root is None:
            logger.info("noncode: no source tree for %s; skipping Tier-3 context", ctx.project_id)
            return None
        scan_output_dir = config.intodoc_output_dir / ctx.project_id
        scan_result = scan_output_dir / "scan_result.json"
        try:
            scan_files = json.loads(
                scan_result.read_text(encoding="utf-8", errors="replace")
            ).get("files", []) if scan_result.exists() else []
            units = build_unit_index(load_jobs(scan_output_dir))
            plan, extraction = build_noncode_context(
                source_root=source_root,
                scan_files=scan_files,
                units=units,
                limits=NonCodeLimits(),
                unit_token_df_threshold=config.noncode_unit_token_df_threshold,
                min_distinctive_overlap=config.noncode_min_distinctive_overlap,
            )
        except Exception:  # additive lane must never break doc generation
            logger.exception("noncode: Tier-3 context build failed for %s", ctx.project_id)
            return None
        if plan.total_items == 0:
            logger.info("noncode: no preparable non-code material for %s", ctx.project_id)
            return None
        self._noncode_plan = plan
        # Persist the project-tier section (full set) for the summary docs and a
        # skip ledger so caps/unsupported files are never a silent drop.
        analysis_dir = ctx.staging_dir / "_analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        project_section = plan.project_section(max_bytes=config.phase3_noncode_project_bytes)
        if project_section:
            (analysis_dir / "_noncode_project_context.md").write_text(
                project_section, encoding="utf-8"
            )
        if extraction.skipped:
            (analysis_dir / "_noncode_skipped.json").write_text(
                json.dumps(list(extraction.skipped), indent=2), encoding="utf-8"
            )
        logger.info(
            "noncode: %s prepared %d item(s), %d matched a unit, %d project-only, %d skipped",
            ctx.project_id, plan.total_items, len(plan.matched_item_ids),
            len(plan.project_only_ids), len(extraction.skipped),
        )
        return plan

    async def _ensure_runtime_evidence(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        log_cb: LogCallback,
    ) -> RuntimeEvidenceStore:
        """Produce the run's runtime-evidence artifacts, once. Never raises.

        This is the PRODUCER for both engines (R1/R3). It fetches once and
        writes files; every consumer -- the group/summary rollup attachment, the
        per-unit sections in ``TargetEvidenceBuilder``, the operator coverage
        endpoint -- reads those files by name. Holding the store as provider
        state and populating it inside one engine's phase driver is what let the
        OpenCode engine reuse the consumer and never the producer.

        Scope comes from ``state.facts_manifest.units`` (R2/A1), which is
        already partition-filtered in both engines and is the scoping authority
        everywhere else. Both identifier keys are sent: ``UnitFact.name`` is the
        scanner's ``job_id``, while the runtime source schedules tasks under the
        DISPLAY name, so passing the manifest units straight through is what
        makes the two-key request happen. The old ``_per_job_doc_units`` gate is
        gone -- it made the whole lane dead on default configuration, because
        that map is only populated when per-unit docs are switched on.

        Artifacts written (all relative to ``ctx.staging_dir``):

        * ``_transient/runtime/runtime_evidence.json`` -- the fetched payload,
          rehydrated by the synchronous evidence builder. ``_transient/`` because
          Phase 4 cleanup is a denylist and the swap is a whole-directory
          rename; anywhere else and the cached provider failure text publishes
          into ``output/{pid}/docs/`` forever (A7).
        * ``_analysis/_runtime_profile.md`` -- the project rollup. Also removed
          by Phase 4, so it never publishes.
        * ``_assessment/runtime_evidence_coverage.json`` -- written in ALL cases
          including a failed fetch (R7), because an operator must be able to
          tell "no runtime section" from "the lookup failed". Survives Phase 4
          and the swap; the shape is ``store.coverage_payload()`` (A12), with a
          ``run_scope`` block added here because only the wiring layer knows
          whether this was a full, scoped or partitioned run.
        * ``_assessment/runtime_redaction_flags.json`` -- only when flags exist.

        KNOWN LIMITATION (recorded, deliberately not fixed here): the
        living-docs patch lane (``services/true_patch_executor.py``) never reads
        ``_phase3_context_files``, so a patched document gains no runtime prose
        even though the coverage artifact beside it describes the whole project.
        """
        run_key = (getattr(ctx, "run_id", None), str(ctx.staging_dir))
        if self._runtime_evidence is not None and self._runtime_evidence_key == run_key:
            return self._runtime_evidence

        units = list(getattr(state.facts_manifest, "units", ()) or ())
        # `fetch_runtime_evidence` never raises and never returns None: absence
        # is a mode. The guard is for anything unforeseeable in the transport --
        # runtime absence must never be able to fail a run (R17).
        try:
            store = await fetch_runtime_evidence(
                ctx.project_id, units, live_evidence=ctx.live_evidence
            )
        except Exception:  # noqa: BLE001 - never a gate
            logger.exception(
                "runtime evidence: fetch raised for project %s; continuing without it",
                ctx.project_id,
            )
            store = RuntimeEvidenceStore(
                window_days=config.runtime_evidence_window_days,
                mode=RuntimeMode.UNAVAILABLE,
                requested_units=len(units),
                unretrieved_units=tuple(
                    str(getattr(unit, "name", "") or "") for unit in units
                ),
            )

        self._runtime_evidence = store
        self._runtime_evidence_key = run_key
        self._write_runtime_evidence_artifacts(ctx, store)
        await self._log_runtime_evidence(ctx, store, log_cb)
        return store

    def _runtime_run_scope(self, ctx: DocGenContext) -> dict[str, Any]:
        """What this coverage record actually describes.

        A scoped run (``scoped_docs``) narrows ``_required_docs`` but NOT the
        unit set, so the fetch legitimately covers the whole project while only
        a couple of documents are rewritten. Narrowing the fetch instead was
        rejected: the rollup's coverage denominator would then disagree with the
        sibling documents preserved byte-stable from the baseline, and the
        published artifact would describe a scope no reader can see. So the
        record stays project-wide and states the discrepancy here.
        """
        scoped = sorted(getattr(ctx, "scoped_docs", None) or ())
        partition = getattr(ctx, "partition", None)
        partition_name = str(getattr(partition, "name", "") or "") if partition is not None else ""
        if scoped:
            kind = "scoped_docs"
            note = (
                "Doc-targeted run: only the listed documents were regenerated, but "
                "the runtime lookup covers every unit in scope for the project. "
                "Documents preserved from the previous baseline are NOT described "
                "by this record."
            )
        elif partition_name:
            kind = "partition"
            note = (
                "Composite sub-project run: units, rollup and this record are "
                "scoped to this partition only. Sibling sub-projects write their "
                "own record under their own output directory."
            )
        else:
            kind = "full"
            note = "Full run: this record describes every document produced by it."
        return {
            "kind": kind,
            "scoped_docs": scoped,
            "partition": partition_name or None,
            "covers_all_documents": not scoped,
            "note": note,
        }

    def _write_runtime_evidence_artifacts(
        self, ctx: DocGenContext, store: RuntimeEvidenceStore
    ) -> None:
        """Stage the cache, the rollup, the coverage record and any flags.

        Each write is isolated: a failure to persist one artifact degrades that
        consumer, it does not lose the others and it never fails the run.
        """
        def _write(relative: str, text: str) -> None:
            try:
                path = ctx.staging_dir / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            except Exception:  # noqa: BLE001 - never a gate
                logger.exception(
                    "runtime evidence: could not write %s for project %s",
                    relative, ctx.project_id,
                )

        _write(RUNTIME_CACHE_JSON, json.dumps(store.to_json(), indent=2))
        _write(
            RUNTIME_PROFILE_MD,
            project_rollup(store, max_bytes=config.phase3_runtime_profile_bytes),
        )
        coverage = dict(store.coverage_payload())
        coverage["run_scope"] = self._runtime_run_scope(ctx)
        _write(RUNTIME_COVERAGE_JSON, json.dumps(coverage, indent=2))
        if store.redaction_flags:
            _write(
                RUNTIME_REDACTION_FLAGS_JSON,
                json.dumps(store.redaction_flags_payload(), indent=2),
            )

    async def _log_runtime_evidence(
        self, ctx: DocGenContext, store: RuntimeEvidenceStore, log_cb: LogCallback
    ) -> None:
        """Three distinguishable outcomes in the run log.

        Before this, two of the three failure states were silent: an operator
        reading a green run could not tell a deliberate code-only run from a
        backend that was unreachable from a lookup that genuinely found nothing.
        """
        if store.mode is RuntimeMode.OPTED_OUT:
            await log_cb(
                ctx.project_id,
                "[custom] runtime evidence: OPTED OUT -- live evidence is off for "
                f"this run, so no lookup was made for {store.requested_units} unit(s). "
                "Documents will state that no runtime data was available.",
                "info",
            )
            return
        if store.mode is RuntimeMode.UNAVAILABLE:
            await log_cb(
                ctx.project_id,
                "[custom] runtime evidence: UNAVAILABLE -- the runtime source could "
                f"not be read ({store.failed_batches}/{store.batches} request batch(es) "
                f"failed) for {store.requested_units} unit(s). Documents will state "
                "that no runtime data was available; they will NOT infer that jobs "
                "are idle.",
                "warning",
            )
            return
        await log_cb(
            ctx.project_id,
            f"[custom] runtime evidence: FETCHED {len(store.by_unit)}/"
            f"{store.requested_units} unit(s) have observed production data over the "
            f"last {store.window_days} days from source "
            f"'{store.source_key or 'unknown'}'; {len(store.orphan_runtime_jobs)} "
            "runtime job(s) have no scanned unit",
            "info",
        )
        if store.partial:
            await log_cb(
                ctx.project_id,
                f"[custom] runtime evidence: PARTIAL -- {store.failed_batches} of "
                f"{store.batches} request batches failed; "
                f"{len(store.unretrieved_units)} unit(s) have no lookup result and "
                "are unknown rather than idle",
                "warning",
            )
        if store.scope_mismatch:
            await log_cb(
                ctx.project_id,
                "[custom] runtime evidence: scan/runtime asymmetry -- "
                f"{len(store.orphan_runtime_jobs)} runtime job(s) have no scanned "
                "counterpart. Either this scan covers a subset of the deployed "
                "estate or the project binding is wrong; the rollup states both "
                "readings without asserting either.",
                "warning",
            )
        if store.redaction_flags:
            await log_cb(
                ctx.project_id,
                f"[custom] runtime evidence: dropped {len(store.redaction_flags)} "
                "failure headline(s) that still carried an identifier shape; see "
                f"{RUNTIME_REDACTION_FLAGS_JSON}",
                "warning",
            )

    def _build_target_evidence(self, ctx: DocGenContext, doc_name: str) -> TargetEvidence | None:
        """Build (and cache) the deterministic evidence bundle for a unit-scoped doc.

        Only docs mapped to a single unit (per-job expansion) get a bundle;
        group-level docs keep their existing context flow untouched.
        """
        if doc_name in self._target_evidence:
            return self._target_evidence[doc_name]
        unit_name = self._per_job_doc_units.get(doc_name)
        if not unit_name:
            return None
        if (
            self._target_evidence_builder is None
            or self._target_evidence_builder.staging_dir != ctx.staging_dir
        ):
            plan = self._ensure_noncode_plan(ctx)
            self._target_evidence_builder = TargetEvidenceBuilder(
                scan_output_dir=config.intodoc_output_dir / ctx.project_id,
                staging_dir=ctx.staging_dir,
                max_bytes=config.phase3_target_evidence_bytes,
                noncode_plan=plan or None,
                noncode_section_bytes=config.phase3_noncode_evidence_bytes,
                # No `runtime_store=`: the builder rehydrates the run's store
                # from `_transient/runtime/runtime_evidence.json` inside
                # `build()`. A store injected here freezes whatever was known
                # when the builder was first constructed for this staging dir.
                runtime_section_bytes=config.phase3_runtime_evidence_bytes,
            )
        evidence = self._target_evidence_builder.build(doc_name, unit_name)
        if evidence is not None:
            self._target_evidence[doc_name] = evidence
        return evidence

    @staticmethod
    def _runtime_rollup_relevant(doc_name: str) -> bool:
        """Whether project-level operational insight belongs in this document.

        Runtime evidence is fetched once for the run, but it must not therefore
        be pasted into every writer's context.  In particular, failure stories
        pollute mapping, lineage, business-rule, inventory and architecture
        documents without helping those documents fulfil their contract.

        Standard operational/risk/change/summary documents are explicit.  The
        keyword fallback supports plugin packs without making provider-specific
        assumptions.  Exclusions win when a custom title mixes concerns.
        """
        name = doc_name.split("__", 1)[0].upper()
        excluded = (
            "MAPPING",
            "BUSINESS_RULE",
            "BUSINESS-RULE",
            "DATA_FLOW",
            "LINEAGE",
            "SCHEMA",
            "COMPONENT_INVENTORY",
            "ARCHITECTURE",
        )
        if any(token in name for token in excluded):
            return False
        if name in {
            "06_OPERATIONS_TESTING_AND_DEBUGGING",
            "07_QUALITY_SECURITY_RISK_SCORECARD",
            "08_CHANGE_PLAYBOOK",
            "09_EXECUTIVE_SUMMARY",
        }:
            return True
        return any(
            token in name
            for token in (
                "OPERATION",
                "TROUBLESHOOT",
                "DEBUG",
                "SUPPORT",
                "RUNBOOK",
                "INCIDENT",
                "RELIABILITY",
                "HEALTH",
                "RISK",
                "RECOVERY",
                "MONITOR",
                "SUMMARY",
                "GROUP",
            )
        )

    def _phase3_context_files(self, ctx: DocGenContext, doc_name: str) -> list[Path]:
        # Executive summary reads from scorecard + completed docs, not from analysis.
        if doc_name == "09_EXECUTIVE_SUMMARY":
            scorecard = ctx.staging_dir / "_assessment" / "scorecard.md"
            files: list[Path] = [scorecard] if scorecard.exists() else []
            # A6: this branch returns BEFORE the shared-facts append below, so
            # without an explicit attachment the one document most likely to
            # make an unsupported reliability claim would be the one document
            # that never sees the runtime rollup. It is a summary doc, so the
            # A5 group/summary rule applies to it.
            runtime_profile = ctx.staging_dir / RUNTIME_PROFILE_MD
            if self._runtime_rollup_relevant(doc_name) and runtime_profile.exists():
                files.append(runtime_profile)
            # The gap ledger comes BEFORE the sibling documents: a summary
            # written over a partial set has to know the set is partial.
            # Verified failure: a run in which five of the summary's context
            # documents were ~800-byte stubs produced a confident ~5,200-word
            # executive summary containing zero occurrences of "stub" or
            # "GENERATION GAP" -- the reader had no way to see the hole.
            gaps = ctx.staging_dir / "_analysis" / "_generation_gaps.md"
            if gaps.exists():
                files.append(gaps)
            # Add all other completed docs as context (but not the summary
            # itself, and not documents that are stubs). A stub carries no
            # evidence -- only a GENERATION GAP banner -- so feeding it in buys
            # nothing and invites the writer to treat the subject as covered.
            stubbed = self._stubbed_doc_names(ctx)
            for other in sorted(self._required_docs):
                if other == "09_EXECUTIVE_SUMMARY" or other in stubbed:
                    continue
                doc_path = ctx.staging_dir / f"{other}.md"
                if doc_path.exists():
                    files.append(doc_path)
            return files

        preprocess_dir = ctx.staging_dir / "_preprocess"
        analysis_dir = ctx.staging_dir / "_analysis"
        pack_dir = ctx.staging_dir / "_assessment" / "prompt_packs"
        bundle = self._doc_bundle_map.get(doc_name, "core")
        files = []
        # Unit-scoped docs get the deterministic scan-fact bundle FIRST: raw
        # verbatim evidence outranks the analysis narration that follows it.
        target_evidence = self._build_target_evidence(ctx, doc_name)
        if target_evidence is not None:
            files.append(target_evidence.path)
        else:
            # Group/summary docs are not unit-scoped, so they get the project-tier
            # non-code documented context (the full prepared set, including items
            # that matched no single unit) instead of a per-unit bundle.
            self._ensure_noncode_plan(ctx)
            noncode_project = ctx.staging_dir / "_analysis" / "_noncode_project_context.md"
            if noncode_project.exists():
                files.append(noncode_project)
            # Units-first wiring (P1.6): summaries synthesize FROM the finished
            # unit docs. The bounded digest is written after the per-unit batch
            # completes, before any group/summary doc is dispatched.
            unit_digest = ctx.staging_dir / "_analysis" / "_unit_docs_digest.md"
            if unit_digest.exists():
                files.append(unit_digest)
            # Same reason as the executive summary: a group/summary doc that
            # synthesizes from the unit-doc digest must be told which units did
            # not produce one. Unit-scoped docs are excluded -- they describe a
            # single unit and never generalize over the set.
            gaps = ctx.staging_dir / "_analysis" / "_generation_gaps.md"
            if gaps.exists():
                files.append(gaps)
        # Whole-partition cross-cutting facts (deterministic census; small, so
        # it stays SHARED across any slicing) -- every doc writer sees the same
        # scanner-derived counts.
        cross_cutting = ctx.staging_dir / "_analysis" / "_cross_cutting_facts.md"
        if cross_cutting.exists():
            files.append(cross_cutting)
        # Provider prefix cache: the documentation obligations are the LAST
        # unconditionally-shared artifact on this path -- byte-identical for
        # every document of the run (93 KB on the measured composite run) --
        # so they belong in the byte-stable head, ahead of the first
        # per-document divergence. The runtime rollup below it is selected per
        # document (`_runtime_rollup_relevant`), so leaving obligations after
        # it split the cacheable head into two classes for no benefit. This is
        # a pure reordering: obligations are still added only on the
        # bounded/run-analysis path and are still absent from the pack path, so
        # WHICH files are sent is unchanged.
        pack_evidence_only = self._plugin_mode and self._phase_policy("analysis") == "skip"
        if not pack_evidence_only:
            obligations = ctx.staging_dir / "documentation_obligations.json"
            if obligations.exists():
                files.append(obligations)
        # Project-level runtime rollup, after the cross-cutting facts and the
        # obligations, and only for operational/risk/change/summary group
        # documents.
        # Mapping, lineage, business-rule, inventory and architecture writers do
        # not receive it. Unit-scoped docs are excluded on purpose: the rollup's
        # failure-mode census names components
        # owned by OTHER units, Phase 3 component-echo validates a unit doc
        # against its own bundle alone, and the writer would be rejected for
        # fabrication over a component that is real. Unit docs already carry
        # their own correctly-scoped per-unit section from the evidence bundle.
        if target_evidence is None and self._runtime_rollup_relevant(doc_name):
            runtime_profile = ctx.staging_dir / RUNTIME_PROFILE_MD
            if runtime_profile.exists():
                files.append(runtime_profile)
        # Pack path: analysis was skipped — Phase 3 writes from evidence bundle +
        # investigation output (when investigation: run).
        # bounded/run analysis falls through to the enterprise context path below.
        if pack_evidence_only:
            facts = ctx.staging_dir / "facts_manifest.json"
            if facts.exists():
                files.append(facts)
            # Include investigation evidence when investigation ran.
            # Pass a filtered bundle (categories relevant to this doc, capped at
            # 40k chars) rather than raw group files to keep Phase 3 context bounded.
            if self._phase_policy("investigation") == "run":
                inv_bundle = self._build_pack_investigation_bundle(ctx, doc_name)
                if inv_bundle is not None:
                    files.append(inv_bundle)
            # Evidence bundle
            doc_def = self._pack_doc_definition(doc_name)
            profile_name = getattr(doc_def, "evidence_profile", None) if doc_def is not None else None
            if profile_name:
                profile_paths = self._pack_evidence_paths.get(profile_name, {})
                evidence_md = ctx.staging_dir / profile_paths.get("md", "")
                if evidence_md.exists():
                    files.append(evidence_md)
            evidence_file = self._phase3_evidence_file(ctx, doc_name)
            if evidence_file is not None:
                files.append(evidence_file)
            plan = ctx.staging_dir / "_plans" / f"{doc_name}.json"
            if plan.exists():
                files.append(plan)
            prompt_pack = pack_dir / f"_phase3_prompt_pack_{bundle}.md"
            if prompt_pack.exists():
                files.append(prompt_pack)
            doc_prompt_file = pack_dir / f"_doc_prompt_{doc_name}.md"
            if doc_prompt_file.exists():
                files.append(doc_prompt_file)
            return files
        # bounded / run analysis: fall through to enterprise context path below,
        # which picks up _doc_pack_*.md from Phase 2 output.  (The obligations
        # are appended above, in the byte-stable head.)
        prompt_pack = pack_dir / f"_phase3_prompt_pack_{bundle}.md"
        if prompt_pack.exists():
            files.append(prompt_pack)
        else:
            logger.warning("phase3 prompt pack missing, falling back to guide files: %s", prompt_pack)
            phase3_guide = preprocess_dir / "_phase3_guide.md"
            if phase3_guide.exists():
                files.append(phase3_guide)
            doc_templates = preprocess_dir / "_doc_templates.md"
            if doc_templates.exists():
                files.append(doc_templates)
        doc_pack = analysis_dir / f"_doc_pack_{doc_name}.md"
        if doc_pack.exists():
            files.append(doc_pack)
        evidence_file = self._phase3_evidence_file(ctx, doc_name)
        if evidence_file is not None:
            files.append(evidence_file)
        source_coverage = preprocess_dir / "_source_coverage.json"
        if not source_coverage.exists():
            files.extend(
                self._engineering_fact_context(
                    ctx, doc_name, unit_name=self._per_job_doc_units.get(doc_name)
                )
            )
        if prompt_pack.exists():
            return files
        if source_coverage.exists():
            files.append(source_coverage)
        if bundle == "core":
            files.extend(sorted(preprocess_dir.glob("_ref_core*.md")))
        elif bundle == "business_ops":
            files.extend(sorted(preprocess_dir.glob("_ref_business_ops*.md")))
        else:
            files.extend(sorted(preprocess_dir.glob("_ref_patterns*.md")))
            files.extend(sorted(preprocess_dir.glob("_ref_user*.md")))
        return files

    @staticmethod
    def _extract_prompt_pack(context_files: list[Path]) -> tuple[str | None, list[Path]]:
        """Remove prompt-pack files from context_files and return their combined text.

        Prompt-pack files are static per call. Moving them to the provider's system_prompt
        channel enables provider prompt caching. Their bytes still count toward context and
        run-token budgets; the transport channel does not make them free.
        Returns (content_or_None, remaining_files).
        """
        pack_files = [f for f in context_files if "_prompt_pack" in f.name and f.suffix == ".md"]
        if not pack_files:
            return None, context_files
        remaining = [f for f in context_files if f not in pack_files]
        content = "\n\n".join(f.read_text(encoding="utf-8", errors="replace") for f in pack_files)
        return content.strip() or None, remaining

    @staticmethod
    def _phase3_evidence_file(ctx: DocGenContext, doc_name: str) -> Path | None:
        """Return the most-targeted evidence file available for this Phase 3 doc.

        Priority: per-doc slice → evidence_brief → None.
        The full evidence_registry.md (724 KB) is never returned here.
        """
        doc_slice = ctx.staging_dir / "_evidence_registry" / "doc_slices" / f"{doc_name}.md"
        if doc_slice.exists():
            return doc_slice
        evidence_brief = ctx.staging_dir / "_evidence_registry" / "evidence_brief.md"
        if evidence_brief.exists():
            return evidence_brief
        return None

    @staticmethod
    def _materialize_cross_project_refs(ctx: DocGenContext, state: HarnessRunState) -> None:
        """Stage this partition's cross-project references for Phase 2/3 + topology.

        Reads the deterministic upload-level cross-project index (written by the
        composite orchestrator before any partition run) and materializes:

        * ``cross_project_refs.md`` in the staging root -- registered as an
          engineering-fact artifact so both Phase 2 shared context and Phase 3
          doc context include it; the doc writers then NOTE external callers/
          callees and shared systems without other sub-projects' content.
        * ``_topology/cross_project_index.json`` -- consumed by the business
          topology starter pack so the sub-project L1/L2 overlay can describe
          its connective edges to sibling sub-projects.

        Missing index (e.g. a manually triggered partition run) is non-fatal:
        the run proceeds without cross-references. Never raises.
        """
        from mcp_server.providers.custom.partition_planner import (
            cross_project_index_path,
            render_cross_project_refs_md,
        )

        try:
            index_path = cross_project_index_path(ctx.project_id)
            if not index_path.exists():
                return
            index = json.loads(index_path.read_text(encoding="utf-8"))
            topology_dir = ctx.staging_dir / "_topology"
            topology_dir.mkdir(parents=True, exist_ok=True)
            (topology_dir / "cross_project_index.json").write_text(
                json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            # Size-split partitions (ETTM_part1 ...) reference the index at
            # their zip-level parent -- the connective layer is per zip.
            index_name = getattr(ctx.partition, "parent", None) or ctx.partition.name
            rendered = render_cross_project_refs_md(index, index_name)
            if rendered is None:
                return
            (ctx.staging_dir / "cross_project_refs.md").write_text(rendered, encoding="utf-8")
            if "cross_project_refs.md" not in state.engineering_fact_artifacts:
                state.engineering_fact_artifacts.append("cross_project_refs.md")
        except Exception as exc:  # noqa: BLE001 - additive context, never blocks a run
            logger.warning(
                "[custom] cross-project refs staging failed (non-fatal) for %s/%s: %s",
                ctx.project_id,
                getattr(ctx.partition, "name", "?"),
                exc,
            )

    # Derived fact maps that dwarf a single unit's share of them. For
    # unit-scoped docs these are delivered as unit-filtered copies so raw
    # evidence is not buried under project-wide tables (118 KB dependency
    # maps drowned verbatim code in production run 50).
    _UNIT_FILTERED_FACT_MAPS = frozenset({
        "dependency_map.md",
        "known_risks.md",
        "config_runtime_map.md",
        "sql_usage.md",
        "change_impact_matrix.md",
        "data_flow_map.md",
    })

    # Phase 2 analysis: lightweight markdown only -- large JSON indexes blow the
    # context window. These are NOT view-reduced: `phase2_context` already
    # bounds every one of them per task (`SCOPED_PROJECT_ARTIFACTS` +
    # `max_scoped_artifact_bytes`), and a unit-relevant scoped copy beats a
    # project-wide sample for an analysis task that owns specific units.
    _PHASE2_FACT_MAPS = (
        "engineering_facts.md",
        "sql_usage.md",
        "dependency_map.md",
        "known_risks.md",
        "config_runtime_map.md",
    )

    # EXPLICIT per-document fact-map routing. There is deliberately no
    # catch-all: an unlisted document (every pack-invented name) used to
    # silently inherit the three heaviest maps -- dependency_map, known_risks
    # and config_runtime_map -- and then spend its budget on whatever byte
    # prefix of them the context planner could afford.
    _DOC_FACT_MAPS: dict[str, tuple[str, ...]] = {
        "00_AGENT_BRIEF": (
            "engineering_facts.md",
            "sql_usage.md",
            "dependency_map.md",
            "known_risks.md",
            "config_runtime_map.md",
        ),
        "01_ARCHITECTURE_MAP": (
            "engineering_facts.md",
            "sql_usage.md",
            "architecture_map.md",
            "dependency_map.md",
        ),
        "02_COMPONENT_INVENTORY": (
            "engineering_facts.md",
            "sql_usage.md",
            "architecture_map.md",
            "dependency_map.md",
        ),
        "03_DATA_FLOWS_AND_LINEAGE": ("engineering_facts.md", "data_flow_map.md"),
        "04_BUSINESS_RULES_AND_TRANSFORMATIONS": (
            "engineering_facts.md",
            "sql_usage.md",
            "dependency_map.md",
            "known_risks.md",
            "config_runtime_map.md",
        ),
        "05_RUNTIME_CONFIG_AND_EXTERNALS": (
            "engineering_facts.md",
            "sql_usage.md",
            "api_surface.md",
            "config_runtime_map.md",
        ),
        "06_OPERATIONS_TESTING_AND_DEBUGGING": (
            "engineering_facts.md",
            "sql_usage.md",
            "test_coverage_map.md",
            "known_risks.md",
        ),
        "07_QUALITY_SECURITY_RISK_SCORECARD": (
            "engineering_facts.md",
            "sql_usage.md",
            "dependency_map.md",
            "known_risks.md",
            "config_runtime_map.md",
        ),
        "08_CHANGE_PLAYBOOK": (
            "engineering_facts.md",
            "sql_usage.md",
            "change_impact_matrix.md",
        ),
        # 09_EXECUTIVE_SUMMARY never reaches here -- `_phase3_context_files`
        # early-returns for it with the sibling documents and no fact maps.
        "10_UNIT_REFERENCE": (
            "engineering_facts.md",
            "sql_usage.md",
            "dependency_map.md",
            "known_risks.md",
            "config_runtime_map.md",
        ),
    }

    # An unrouted GROUP-LEVEL document -- a pack's own document name -- gets the
    # aggregate and the SQL register only. `engineering_facts.md` is itself a
    # complete deterministic aggregate that names every sibling map as evidence
    # of record, so an unrouted writer is pointed at the maps rather than handed
    # an arbitrary slab of them.
    _DEFAULT_DOC_FACT_MAPS = ("engineering_facts.md", "sql_usage.md")

    # An unrouted UNIT-SCOPED document keeps the historical set. The defect the
    # narrow default fixes is a group document spending its budget on a byte
    # prefix of a project-wide table; a unit doc never receives that table --
    # `filter_fact_table` hands it a unit-filtered copy already bounded by
    # `phase3_filtered_map_bytes`, whose rows are all about that one unit.
    # Narrowing here would take real, cheap, in-scope evidence away from every
    # pack's per-unit dossier.
    _DEFAULT_UNIT_FACT_MAPS = (
        "engineering_facts.md",
        "sql_usage.md",
        "dependency_map.md",
        "known_risks.md",
        "config_runtime_map.md",
    )

    @staticmethod
    def _engineering_fact_context(
        ctx: DocGenContext,
        doc_name: str | None = None,
        unit_name: str | None = None,
    ) -> list[Path]:
        cls = CustomHarnessProvider
        if doc_name is None:
            names = list(cls._PHASE2_FACT_MAPS)
        else:
            # Per-unit expansions are `{base}__{group}__{unit}` (see
            # `_expand_catalog_per_unit_docs`), so the base name is what routes.
            routed = cls._DOC_FACT_MAPS.get(doc_name) or cls._DOC_FACT_MAPS.get(
                doc_name.split("__", 1)[0]
            )
            if routed is None:
                routed = (
                    cls._DEFAULT_UNIT_FACT_MAPS
                    if unit_name is not None
                    else cls._DEFAULT_DOC_FACT_MAPS
                )
            names = list(routed)
        # Composite partition runs stage this small deterministic file; it only
        # exists when a cross-project index was available, so non-partitioned
        # runs are byte-identical.
        names.append("cross_project_refs.md")
        paths: list[Path] = []
        for name in names:
            path = ctx.staging_dir / name
            if path.exists():
                paths.append(path)
            else:
                # Not every platform produces every map (`sql_usage.md` and
                # `api_surface.md` exist on generic/lobster scans and not on
                # Talend ones). Absence is normal; silence about it is not.
                logger.debug(
                    "phase3 fact map not staged for %s: %s", doc_name or "phase2", name
                )
        if unit_name is not None:
            # Unit-scoped lane, unchanged: a per-unit filtered copy of the RAW
            # map beats a project-wide view, because the rows that matter to one
            # unit are exactly the rows a project-wide sample drops.
            filtered_paths: list[Path] = []
            for path in paths:
                if path.name in cls._UNIT_FILTERED_FACT_MAPS:
                    filtered = filter_fact_table(
                        path,
                        unit_name,
                        ctx.staging_dir / "_analysis",
                        config.phase3_filtered_map_bytes,
                    )
                    if filtered is not None:
                        filtered_paths.append(filtered)
                else:
                    filtered_paths.append(path)
            return filtered_paths
        if doc_name is None:
            return paths
        # GROUP-LEVEL DOCUMENT. Every oversized map is replaced by its
        # registered semantic view BEFORE the path enters `context_files`, so
        # `phase3_slices` never has to choose a byte prefix of a 1.75 MB table.
        view_dir = ctx.staging_dir / "_analysis" / "_phase3_views"
        return [build_fact_map_view(path, view_dir) for path in paths]

    async def _log_call_metrics(
        self,
        ctx: DocGenContext,
        target: str,
        result,
        log_cb: LogCallback,
    ) -> None:
        meta = result.metadata or {}
        estimated = meta.get("estimated_input_tokens") or result.input_tokens
        configured = (meta.get("provider_options") or {}).get("options", {}).get("num_ctx") or meta.get("configured_context")
        context_bytes = meta.get("context_bytes", 0)
        finish_reason = meta.get("finish_reason")
        finish = f" finish={finish_reason}" if finish_reason else ""
        failure = f" failure_kind={meta.get('failure_kind')}" if meta.get("failure_kind") else ""
        # R2.6 cache audit: surface the provider-reported cache-hit split per
        # call (aggregates live in _assessment/phase_costs.json). in= is the
        # real billed input when the provider reported usage.
        cache = ""
        if result.input_tokens:
            cache = (
                f" in={result.input_tokens}"
                f" cache_read={result.cache_read_tokens}"
                f" cache_miss={max(0, result.input_tokens - result.cache_read_tokens)}"
            )
        await log_cb(
            ctx.project_id,
            f"[custom] call metrics: {target} model={result.model_name} "
            f"in~{estimated} out={result.output_tokens} context_bytes={context_bytes} "
            f"ctx={configured or 'default'} latency_ms={result.latency_ms}{cache}{finish}{failure}",
            "info",
        )


def build_custom_provider(
    model_str: str,
    write_model_str: str | None = None,
    investigation_model_str: str | None = None,
    fallback_model_str: str | None = None,
    instruction_pack_dir: str | None = None,
    plugin_pack_dir: Path | None = None,
) -> CustomHarnessProvider:
    from mcp_server.model_strategies import normalize_model_id

    model_str = normalize_model_id(model_str)

    def _provider_config(model_id: str) -> dict[str, str | None]:
        from mcp_server.model_strategies import provider_connection_config

        return provider_connection_config(model_id)

    def _build_secondary(secondary_str: str | None, *, skip_if_same: bool = True) -> ModelProvider | None:
        secondary_str = normalize_model_id(secondary_str) if secondary_str else None
        if not secondary_str or (skip_if_same and secondary_str == model_str):
            return None
        provider_config = _provider_config(secondary_str)
        return build_model_provider(
            secondary_str,
            api_key=provider_config["api_key"],
            base_url=provider_config["base_url"],
            ollama_base_url=config.ollama_base_url,
        )

    from mcp_server.services.pack_loader import PackLoader
    resolved_pack_dir = instruction_pack_dir or config.instruction_pack_dir
    pack_dir = config.project_root / resolved_pack_dir
    pack = PackLoader(pack_dir).load() if pack_dir.exists() else None

    main = build_model_provider(
        model_str,
        **_provider_config(model_str),
        ollama_base_url=config.ollama_base_url,
    )
    return CustomHarnessProvider(
        main,
        _build_secondary(write_model_str),
        _build_secondary(investigation_model_str),
        _build_secondary(fallback_model_str, skip_if_same=False),
        pack=pack,
        plugin_pack_dir=plugin_pack_dir,
    )
