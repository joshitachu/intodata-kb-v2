"""Prompt builders for bounded custom-harness calls."""

from __future__ import annotations

from pathlib import Path

from mcp_server.providers.custom.constants import GROUP_SUMMARY_SECTION
from mcp_server.providers.custom.doc_catalog import load_doc_catalog
from mcp_server.providers.custom.models import FactsManifest
from mcp_server.platforms import get_platform_strategy
from mcp_server.providers.custom.prompt_packs import ANALYSIS_SECTION_NAMES
from mcp_server.providers.custom.prompt_renderer import get_renderer
from mcp_server.services.pack_loader import InstructionPack
from mcp_server.services.nexus_execution_contract import presentation_instructions_for_phase
from mcp_server.schemas import NexusPresentation


def facts_summary(facts: FactsManifest) -> str:
    groups = ", ".join(f"{name}={count}" for name, count in sorted(facts.groups.items()))
    units = "\n".join(
        f"- {u.name} | group={u.group} | type={u.unit_type} | components={u.component_count}"
        for u in facts.units
    )
    return (
        f"Platform: {facts.platform}\n"
        f"Canonical unit label: {facts.canonical_unit_label}\n"
        f"Canonical unit count: {facts.unit_count}\n"
        f"Groups: {groups}\n\n"
        f"Canonical units:\n{units}\n"
    )


def platform_analysis_requirements(facts: FactsManifest) -> str:
    try:
        return get_platform_strategy(facts.platform).analysis_requirements()
    except ValueError:
        return get_platform_strategy("generic").analysis_requirements()


def platform_documentation_obligations(facts: FactsManifest) -> str:
    try:
        obligations = get_platform_strategy(facts.platform).documentation_obligations()
    except ValueError:
        obligations = get_platform_strategy("generic").documentation_obligations()
    lines = []
    for category, terms in obligations.items():
        lines.append(f"- {category}: {', '.join(terms)}")
    return "\n".join(lines)


def _platform_section_depth(facts: FactsManifest) -> str:
    """Per-section extraction requirements loaded from template files."""
    return get_renderer().render_platform_section_depth(facts.platform)


def _resolve_preamble(
    plugin_pack_dir: Path | None,
    pack_filename: str,
    instruction_pack: InstructionPack | None,
    instruction_pack_key: str,
    default: str,
) -> str:
    """Return the preamble for a prompt, checking plugin pack files first."""
    if plugin_pack_dir:
        override = plugin_pack_dir / "prompts" / pack_filename
        if override.exists():
            return override.read_text(encoding="utf-8").rstrip()
    if instruction_pack:
        return instruction_pack.preambles.get(instruction_pack_key, default).rstrip()
    return default


_PHASE2_PREAMBLE_DEFAULT = """\
You are generating Phase 2 group analysis. Your output is the SOLE factual foundation for
every documentation file produced in Phase 3. If you omit a finding here, it will be absent
from every document. Be EXHAUSTIVE. Include every expression, every rule, every column, every risk.
Depth over brevity. Never summarize when you can enumerate."""


def group_analysis_prompt(
    facts: FactsManifest,
    group: str,
    pack: InstructionPack | None = None,
    plugin_pack_dir: "Path | None" = None,
    required_docs: "list[str] | None" = None,
    task_units: "list[str] | None" = None,
    missing_evidence_units: "list[str] | None" = None,
    multi_task: bool = False,
) -> str:
    """Build the Phase 2 analysis prompt.

    When ``task_units`` is given the enumeration obligation is scoped to exactly
    those units (the ones whose evidence is physically in the task's context).
    ``missing_evidence_units`` are assigned units with no evidence anywhere in
    the scan; the prompt instructs the model to state what is missing for them
    instead of reconstructing. Without ``task_units`` the obligation covers the
    whole group (single-task groups and repair paths).
    """
    group_unit_names = [u.name for u in facts.units if u.group == group or group.startswith(f"{u.group}_part")]
    if not group_unit_names:
        group_unit_names = [u.name for u in facts.units if u.group == group]
    unit_names = task_units if task_units is not None else group_unit_names
    missing_names = missing_evidence_units or []
    missing_block = ""
    if missing_names:
        missing_block = (
            "\nThe following assigned units have NO evidence sections anywhere in the scan. "
            "For each, write its name and state exactly what evidence is missing. Do NOT "
            "infer or reconstruct their components, code, or configuration:\n"
            + "\n".join(f"- {name}" for name in missing_names)
            + "\n"
        )
    task_scope_note = ""
    if multi_task:
        task_scope_note = (
            f"\nThis task covers a subset of group {group}; the remaining units are analyzed by "
            "parallel tasks over their own evidence, and the group-level summary is synthesized "
            "separately. Analyze ONLY the units listed above. Do NOT describe, summarize, or "
            "invent analysis for any other unit, even if other unit names appear in shared "
            "inventories, dependency lists, or routine maps inside the evidence. Keep your "
            "GROUP_SUMMARY scoped to the units listed above.\n"
        )
    section_headings = "\n".join(f"- `### {name}`" for name in (*ANALYSIS_SECTION_NAMES, GROUP_SUMMARY_SECTION))
    preamble = _resolve_preamble(
        plugin_pack_dir, "phase2.md",
        pack, "phase2_preamble",
        _PHASE2_PREAMBLE_DEFAULT,
    )
    effective_docs = required_docs or load_doc_catalog().required_docs
    return get_renderer().render("phase2_analysis.md", {
        "PREAMBLE": preamble,
        "GROUP": group,
        "DOC_COUNT": str(len(effective_docs)),
        "DOC_LIST": "\n".join(f"- {doc}.md" for doc in effective_docs),
        "UNIT_LIST": "\n".join(f"- {name}" for name in unit_names),
        "TASK_UNIT_COUNT": str(len(unit_names)),
        "MISSING_EVIDENCE_BLOCK": missing_block,
        "TASK_SCOPE_NOTE": task_scope_note,
        "FACTS_SUMMARY": facts_summary(facts),
        "PLATFORM_ANALYSIS_REQUIREMENTS": platform_analysis_requirements(facts),
        "SECTION_HEADINGS": section_headings,
        "PLATFORM_SECTION_DEPTH": _platform_section_depth(facts),
        "CANONICAL_UNIT_COUNT": str(facts.unit_count),
        "CANONICAL_UNIT_LABEL": facts.canonical_unit_label,
    })


def phase2_section_repair_prompt(facts: FactsManifest, group: str, missing_sections: list[str]) -> str:
    unit_names = [u.name for u in facts.units if u.group == group or group.startswith(f"{u.group}_part")]
    if not unit_names:
        unit_names = [u.name for u in facts.units if u.group == group]
    sections = "\n".join(f"- `### {name}`" for name in missing_sections)
    return f"""\
The previous Phase 2 analysis for group `{group}` was incomplete.

Generate ONLY the missing sections listed below, in this exact order and with these exact headings.
Do not repeat sections that are not listed. Do not add a new title or coverage block.

Missing required sections:
{sections}

Canonical units in this group -- mention EVERY one by exact name where relevant:
{chr(10).join(f"- {name}" for name in unit_names)}

Run facts:
{facts_summary(facts)}

Platform evidence contract:
{platform_analysis_requirements(facts)}

Use only the supplied scanner facts, investigation evidence, and EngineeringFacts context.
Each returned section must be substantive. If source detail is absent, name the affected unit and state exactly what is missing.
"""


_SCORECARD_PREAMBLE_DEFAULT = """\
Generate a structured assessment scorecard from the supplied Phase 2 analysis files and investigation evidence.
Use ONLY the supplied analysis files, investigation evidence, and this facts manifest.
Follow the scoring rubric, anti-pattern catalog, red-flag catalog, and security checklist
in the supplied scorecard prompt pack exactly -- do not invent scoring criteria.

Confidence classification -- annotate every dimension score with HIGH, MEDIUM, or LOW:
- Architecture and connectivity (structural, code-confirmed from static analysis): HIGH
- Operational and reliability (error handling, recovery, monitoring): LOW -- requires runtime data, mark as (INFERRED)
- Security (credential handling, auth patterns): MEDIUM -- static evidence necessary but not sufficient
- Business and platform context (SLA, ownership, operational procedures): LOW without stakeholder input, mark as (INFERRED)

For every LOW score add the line: "Requires runtime validation to confirm."
Prefix the overall score with: "Preliminary X.X/5.0 -- expect up to 10% adjustment after runtime validation.\""""


def scorecard_prompt(
    facts: FactsManifest,
    pack: InstructionPack | None = None,
    plugin_pack_dir: "Path | None" = None,
) -> str:
    preamble = _resolve_preamble(
        plugin_pack_dir, "scorecard.md",
        pack, "scorecard_preamble",
        _SCORECARD_PREAMBLE_DEFAULT,
    )
    first_group_name = (facts.groups and list(facts.groups.keys())[0]) or "group"
    return get_renderer().render("scorecard.md", {
        "PREAMBLE": preamble,
        "FIRST_GROUP_NAME": first_group_name,
        "FACTS_SUMMARY": facts_summary(facts),
        "PLATFORM_ANALYSIS_REQUIREMENTS": platform_analysis_requirements(facts),
        "CANONICAL_UNIT_COUNT": str(facts.unit_count),
        "CANONICAL_UNIT_LABEL": facts.canonical_unit_label,
    })


_PHASE3_PREAMBLE_DEFAULT = """\
Generate `{doc_name}.md` from the supplied validated analysis bundle and scorecard.
You are writing durable context for coding assistants, integration engineers, and developers
working across codebases and integration platforms. The document must help a future assistant
analyze, plan, debug, refactor, assess risk, and make changes without rediscovering the project.

Follow the Phase 3 synthesis guide, document templates, and reference docs in the supplied context.
Use supplied EngineeringFacts artifacts as normalized evidence where relevant:
- SQL/table/field docs: prioritize `sql_usage` for runtime usage and `schema_dictionary` for DDL column metadata
- 01_ARCHITECTURE_MAP and 02_COMPONENT_INVENTORY: prioritize `architecture_map` and `dependency_map`
- 03_DATA_FLOWS_AND_LINEAGE: prioritize `entity_index`, `usage_index`, and `data_flow_map`
- 05_RUNTIME_CONFIG_AND_EXTERNALS: prioritize `api_surface` and `config_runtime_map`
- 06_OPERATIONS_TESTING_AND_DEBUGGING: prioritize `test_coverage_map` and `known_risks`
- 08_CHANGE_PLAYBOOK: prioritize `change_impact_matrix`, `dependency_map`, and `known_risks`
Cite EngineeringFacts artifact names when they support a claim. Do not invent relationships,
routes, fields, flows, runtime settings, risks, tests, or operational procedures not present in evidence.
If `_source_coverage.json` or redacted source bundles are supplied, treat those source files as the
evidence of record. Do not say a covered file is missing/unavailable/not scanned; values marked
`[REDACTED]` are intentionally hidden secrets."""


def doc_prompt(
    facts: FactsManifest,
    doc_name: str,
    pack: InstructionPack | None = None,
    plugin_pack_dir: "Path | None" = None,
    extra_section_requirements: "dict[str, list[str]] | None" = None,
    scoped_unit: str | None = None,
    nexus_presentation: NexusPresentation | None = None,
    private_analysis_instructions: str = "",
) -> str:
    required_headings = (
        (extra_section_requirements or {}).get(doc_name)
        or load_doc_catalog().section_requirements.get(doc_name, [])
    )
    headings_block = "\n".join(f"  ## {s}" for s in required_headings)
    doc_specific = _doc_specific_from_pack(plugin_pack_dir, doc_name) or _doc_specific_instructions(doc_name, facts)
    if scoped_unit:
        doc_specific = (
            "PER-JOB DOCUMENT SCOPE (MANDATORY): This file documents exactly one scanned job: "
            f"`{scoped_unit}`. Use evidence for this job only. Do not document, summarize, or "
            "claim coverage for any other job. Child jobs may be mentioned only when they are "
            "directly evidenced as dependencies of this job.\n\n"
            + doc_specific
        )
    presentation = presentation_instructions_for_phase("phase3", nexus_presentation)
    if presentation:
        doc_specific = f"{presentation}\n\n{doc_specific}".rstrip()
    if private_analysis_instructions:
        doc_specific = f"{private_analysis_instructions}\n\n{doc_specific}".rstrip()
    raw_preamble = _resolve_preamble(
        plugin_pack_dir, "phase3.md",
        pack, "phase3_preamble",
        _PHASE3_PREAMBLE_DEFAULT,
    )
    preamble = raw_preamble.rstrip().format(doc_name=doc_name)
    examples_block = _examples_block(plugin_pack_dir)
    return get_renderer().render("phase3_doc.md", {
        "PREAMBLE": preamble,
        "DOC_NAME": doc_name,
        "PLATFORM": facts.platform,
        "CANONICAL_UNIT_COUNT": str(facts.unit_count),
        "CANONICAL_UNIT_LABEL": facts.canonical_unit_label,
        "HEADINGS_BLOCK": headings_block,
        "DOC_SPECIFIC": doc_specific,
        "PLATFORM_DOCUMENTATION_OBLIGATIONS": platform_documentation_obligations(facts),
        "PLATFORM_ANALYSIS_REQUIREMENTS": platform_analysis_requirements(facts),
        "FACTS_SUMMARY": facts_summary(facts),
        "EXAMPLES_BLOCK": examples_block,
    })


def _examples_block(plugin_pack_dir: Path | None) -> str:
    """Return example documents from the pack's examples/ folder, if any."""
    if not plugin_pack_dir:
        return ""
    examples_dir = plugin_pack_dir / "examples"
    if not examples_dir.exists():
        return ""
    parts: list[str] = ["\n\nEXAMPLE OUTPUT (for format and style reference only — do not copy content):"]
    for path in sorted(examples_dir.glob("*.md")):
        parts.append(f"\n--- Example: {path.name} ---\n{path.read_text(encoding='utf-8').strip()}")
    return "\n".join(parts) if len(parts) > 1 else ""


def _doc_specific_from_pack(plugin_pack_dir: "Path | None", doc_name: str) -> str:
    if not plugin_pack_dir:
        return ""
    p = plugin_pack_dir / "prompts" / "docs" / f"{doc_name}.md"
    if not p.exists() and "__" in doc_name:
        p = plugin_pack_dir / "prompts" / "docs" / f"{doc_name.split('__', 1)[0]}.md"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def _doc_specific_instructions(doc_name: str, facts: FactsManifest) -> str:
    text = load_doc_catalog().doc_specific_instructions_for(doc_name)
    if doc_name == "00_AGENT_BRIEF" and text:
        text = text.format(unit_count=facts.unit_count, canonical_unit_label=facts.canonical_unit_label)
    if doc_name == "01_ARCHITECTURE_MAP" and facts.platform.lower() != "talend" and text:
        # Strip the Talend-specific connection table block for non-Talend platforms.
        talend_marker = "\n      TALEND CONNECTION SEMANTIC TABLE:"
        idx = text.find(talend_marker)
        if idx != -1:
            text = text[:idx].rstrip() + "\n"
    return text
