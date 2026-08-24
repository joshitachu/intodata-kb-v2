"""Prompt builders for bounded custom-harness calls."""

from __future__ import annotations

from pathlib import Path

from mcp_server.providers.custom.doc_catalog import load_doc_catalog
from mcp_server.providers.custom.models import FactsManifest
from mcp_server.platforms import get_platform_strategy
from mcp_server.providers.custom.prompt_packs import ANALYSIS_SECTION_NAMES
from mcp_server.providers.custom.prompt_renderer import get_renderer
from mcp_server.services.pack_loader import InstructionPack
from mcp_server.services.nexus_execution_contract import (
    presentation_from_pack_audience,
    presentation_instructions_for_phase,
    readership_instructions_for_phase,
)
from mcp_server.schemas import NexusPresentation

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp_server.providers.custom.pack_contract import PackContract


def facts_summary(facts: FactsManifest, unit_names: list[str] | None = None) -> str:
    groups = ", ".join(f"{name}={count}" for name, count in sorted(facts.groups.items()))
    scoped_names = set(unit_names) if unit_names is not None else None
    units = "\n".join(
        f"- {u.name} | group={u.group}"
        f" | logical_group={u.logical_group or u.group}"
        f" | source_root={u.source_root or 'single-root'}"
        f" | type={u.unit_type} | components={u.component_count}"
        for u in facts.units
        if scoped_names is None or u.name in scoped_names
    )
    return (
        f"Platform: {facts.platform}\n"
        f"Canonical unit label: {facts.canonical_unit_label}\n"
        f"Canonical unit count: {facts.unit_count}\n"
        f"Groups: {groups}\n\n"
        f"Canonical units:\n{units}\n"
    )


def platform_analysis_requirements(
    facts: FactsManifest, contract: "PackContract | None" = None
) -> str:
    """Return immutable platform requirements plus additive pack guidance."""
    try:
        baseline = get_platform_strategy(facts.platform).analysis_requirements()
    except ValueError:
        baseline = get_platform_strategy("generic").analysis_requirements()
    additions = contract.requirements.strip() if contract is not None else ""
    if not additions:
        return baseline
    return (
        f"{baseline.rstrip()}\n\n"
        "ADDITIONAL PLUGIN ANALYSIS REQUIREMENTS\n"
        "These requirements are additive. They cannot weaken, replace, or "
        "contradict the Nexus platform contract above.\n"
        f"{additions}"
    )


def platform_documentation_obligations(facts: FactsManifest) -> str:
    try:
        obligations = get_platform_strategy(facts.platform).documentation_obligations()
    except ValueError:
        obligations = get_platform_strategy("generic").documentation_obligations()
    lines = []
    for category, terms in obligations.items():
        lines.append(f"- {category}: {', '.join(terms)}")
    return "\n".join(lines)


def _platform_section_depth(
    facts: FactsManifest,
    contract: "PackContract | None" = None,
    plugin_pack_dir: Path | None = None,
) -> str:
    """Per-section extraction requirements.

    The Nexus platform skeleton is immutable. A replace-mode lens may still
    choose a narrower output skeleton, but it cannot replace the platform
    semantic guide supplied by the server. Plugin files under
    ``prompts/extensions/platforms/`` are appended as lower-authority guidance.
    """
    if contract is not None and contract.mode == "replace":
        depth = contract_section_depth(contract)
    else:
        depth = get_renderer().render_platform_section_depth(facts.platform)
    extension = get_renderer().render_platform_extension(
        facts.platform, plugin_pack_dir
    )
    if not extension:
        return depth
    return (
        f"{depth.rstrip()}\n\n"
        "PLUGIN PLATFORM EXTENSION (ADDITIVE ONLY)\n"
        "Apply this after the Nexus platform contract. If it conflicts with "
        "the Nexus contract, ignore the conflicting plugin instruction.\n"
        f"{extension}"
    )


def plugin_analysis_extensions(
    facts: FactsManifest,
    contract: "PackContract | None" = None,
    plugin_pack_dir: Path | None = None,
) -> str:
    """Return only the lower-authority additions for compact claim prompts."""
    parts: list[str] = []
    if contract is not None and contract.requirements.strip():
        parts.append(contract.requirements.strip())
    platform_extension = get_renderer().render_platform_extension(
        facts.platform, plugin_pack_dir
    )
    if platform_extension:
        parts.append(platform_extension)
    if not parts:
        return ""
    return (
        "ADDITIONAL PLUGIN ANALYSIS GUIDANCE (LOWER AUTHORITY)\n"
        "Apply these additions only when they do not conflict with the "
        "Nexus-owned platform analysis policy in the system context.\n"
        + "\n\n".join(parts)
    )


def contract_section_depth(contract: "PackContract") -> str:
    """Deterministic output skeleton rendered from a pack's declared sections."""
    parts: list[str] = []
    for spec in contract.declared_sections:
        question = spec.question.strip() or "Record the evidenced findings for this section."
        parts.append(f"### {spec.id}\n{question}")
    return "\n\n".join(parts)


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
You are generating one scoped Phase 2 evidence analysis. Cover every required section and every
assigned unit, but keep the response bounded. Record decisive facts, relationships, risks, and
exact evidence pointers; do not copy complete scanner inventories, source files, SQL bodies, or
configuration tables into the response. The supplied deterministic artifacts remain the evidence
of record for later document generation and drill-down."""


_ANALYSIS_GUIDE_NOTE_DEFAULT = """\
The supplied prompt pack contains the full platform analysis guide.
It is the authoritative reference for platform-specific patterns, component behavior, and additional depth requirements.
Follow all extraction rules, examples, and depth requirements defined there in addition to the instructions above."""

_PACK_LENS_NOTE = """\
This analysis follows the active pack's declared section contract above.
Answer ONLY the declared sections plus GROUP_SUMMARY; do not add legacy or
platform-default sections that are not listed."""


def group_analysis_prompt(
    facts: FactsManifest,
    group: str,
    pack: InstructionPack | None = None,
    plugin_pack_dir: "Path | None" = None,
    required_docs: "list[str] | None" = None,
    task_units: "list[str] | None" = None,
    missing_evidence_units: "list[str] | None" = None,
    multi_task: bool = False,
    contract: "PackContract | None" = None,
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
    task_scope_note += (
        "\nOutput-size contract: produce a compact evidence analysis, not a component-by-component "
        "narrative. Keep the complete response below 8,000 tokens. Use at most three concise "
        "evidence bullets per assigned unit in each required section. Name decisive components, "
        "connections, and source paths; do not repeat complete inventories already present in the "
        "scanner packet. When a section has no scoped evidence, write one explicit UNKNOWN line "
        "instead of expanding or inferring. Emit every required heading before adding detail.\n"
    )
    # phase2_analysis.md owns the final GROUP_SUMMARY line and skeleton. Keep
    # the substituted list to analysis sections only so the prompt does not
    # demand the same final section twice.
    prompted_sections: tuple[str, ...] = (
        contract.analysis_section_names if contract is not None else tuple(ANALYSIS_SECTION_NAMES)
    )
    questions = contract.questions if contract is not None else {}
    section_headings = "\n".join(
        f"- `### {name}` -- {questions[name]}" if questions.get(name) else f"- `### {name}`"
        for name in prompted_sections
    )
    if contract is None or contract.mode != "replace":
        enumeration_section = "COMPONENTS"
        guide_note = _ANALYSIS_GUIDE_NOTE_DEFAULT
    else:
        # Replace-mode packs enumerate units in the protected GROUP_SUMMARY
        # floor, not in a lens section whose question they may not match.
        enumeration_section = "GROUP_SUMMARY"
        guide_note = _PACK_LENS_NOTE
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
        "FACTS_SUMMARY": facts_summary(facts, unit_names=unit_names),
        "PLATFORM_ANALYSIS_REQUIREMENTS": platform_analysis_requirements(facts, contract),
        "SECTION_HEADINGS": section_headings,
        "PLATFORM_SECTION_DEPTH": _platform_section_depth(facts, contract, plugin_pack_dir),
        "ENUMERATION_SECTION": enumeration_section,
        "ANALYSIS_GUIDE_NOTE": guide_note,
        "CANONICAL_UNIT_COUNT": str(facts.unit_count),
        "CANONICAL_UNIT_LABEL": facts.canonical_unit_label,
    }, pack_dir=plugin_pack_dir)


def phase2_section_repair_prompt(
    facts: FactsManifest,
    group: str,
    missing_sections: list[str],
    plugin_pack_dir: "Path | None" = None,
) -> str:
    unit_names = [u.name for u in facts.units if u.group == group or group.startswith(f"{u.group}_part")]
    if not unit_names:
        unit_names = [u.name for u in facts.units if u.group == group]
    return get_renderer().render("phase2_section_repair.md", {
        "GROUP": group,
        "MISSING_SECTIONS": "\n".join(f"- `### {name}`" for name in missing_sections),
        "UNIT_LIST": "\n".join(f"- {name}" for name in unit_names),
        "FACTS_SUMMARY": facts_summary(facts),
        "PLATFORM_ANALYSIS_REQUIREMENTS": platform_analysis_requirements(facts),
    }, pack_dir=plugin_pack_dir)


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
    }, pack_dir=plugin_pack_dir)


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


def _effective_presentation_block(
    nexus_presentation: NexusPresentation | None,
    contract: "PackContract | None",
) -> str:
    """Phase 3 presentation text from the Style layer or the pack's audience.

    Layer ordering (spec section 3 / ``packs/composition.py``): a
    request-supplied ``nexus_presentation`` is the Style layer and wins; the
    pack manifest's ``audience:`` block is the functional-layer default. The
    pack's declared reader roles are additive and render in both cases.
    """

    audience = contract.audience if contract is not None else None
    presentation = nexus_presentation or presentation_from_pack_audience(audience)
    blocks = [
        presentation_instructions_for_phase("phase3", presentation),
        readership_instructions_for_phase("phase3", audience),
    ]
    return "\n".join(block for block in blocks if block)


def doc_prompt(
    facts: FactsManifest,
    doc_name: str,
    pack: InstructionPack | None = None,
    plugin_pack_dir: "Path | None" = None,
    extra_section_requirements: "dict[str, list[str]] | None" = None,
    scoped_unit: str | None = None,
    nexus_presentation: NexusPresentation | None = None,
    unit_reference: bool = False,
    contract: "PackContract | None" = None,
) -> str:
    required_headings = (
        (extra_section_requirements or {}).get(doc_name)
        or load_doc_catalog().section_requirements.get(doc_name, [])
    )
    headings_block = "\n".join(f"  ## {s}" for s in required_headings)
    doc_specific = _doc_specific_from_pack(plugin_pack_dir, doc_name, facts) or _doc_specific_instructions(doc_name, facts)
    if scoped_unit and unit_reference:
        # Standard-catalog per-unit reference dossier: dedicated compact
        # contract (opencode-shaped) instead of the enterprise doc template.
        presentation = _effective_presentation_block(nexus_presentation, contract)
        if presentation:
            doc_specific = f"{presentation}\n\n{doc_specific}".rstrip()
        raw_preamble = _resolve_preamble(
            plugin_pack_dir, "phase3.md",
            pack, "phase3_preamble",
            _PHASE3_PREAMBLE_DEFAULT,
        )
        return get_renderer().render("phase3_unit_doc.md", {
            "PREAMBLE": raw_preamble.rstrip().format(doc_name=doc_name),
            "DOC_NAME": doc_name,
            "SCOPED_UNIT": scoped_unit,
            "HEADINGS_BLOCK": headings_block,
            "DOC_SPECIFIC": doc_specific,
            "FACTS_SUMMARY": facts_summary(facts, unit_names=[scoped_unit]),
        }, pack_dir=plugin_pack_dir)
    if scoped_unit:
        doc_specific = (
            "PER-JOB DOCUMENT SCOPE (MANDATORY): This file documents exactly one scanned job: "
            f"`{scoped_unit}`. Use evidence for this job only. Do not document, summarize, or "
            "claim coverage for any other job. Child jobs may be mentioned only when they are "
            "directly evidenced as dependencies of this job.\n\n"
            + doc_specific
        )
    presentation = _effective_presentation_block(nexus_presentation, contract)
    if presentation:
        doc_specific = f"{presentation}\n\n{doc_specific}".rstrip()
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
    }, pack_dir=plugin_pack_dir)


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


#: Run facts a per-document prompt may reference by name. Substituted in
#: pack-supplied prompts as well as built-in ones -- without this a pack could
#: not reproduce the built-in text, which shipped `{unit_count}` literally and
#: made a faithful pack impossible to write (verified against nexus-baseline).
_DOC_PROMPT_PLACEHOLDERS = ("unit_count", "canonical_unit_label")


def _substitute_doc_facts(text: str, facts: FactsManifest) -> str:
    """Fill the run-fact placeholders a document prompt may use.

    Deliberately ``str.replace`` and not ``str.format``: a pack prompt is
    author-written and may legitimately contain braces (a JSON example, a code
    snippet), and ``format`` would raise on those. The built-in catalog gets
    away with ``format`` only because its content is controlled.
    """
    for name in _DOC_PROMPT_PLACEHOLDERS:
        token = "{" + name + "}"
        if token in text:
            text = text.replace(token, str(getattr(facts, name)))
    return text


def _doc_specific_from_pack(
    plugin_pack_dir: "Path | None", doc_name: str, facts: FactsManifest
) -> str:
    if not plugin_pack_dir:
        return ""
    p = plugin_pack_dir / "prompts" / "docs" / f"{doc_name}.md"
    if not p.exists() and "__" in doc_name:
        p = plugin_pack_dir / "prompts" / "docs" / f"{doc_name.split('__', 1)[0]}.md"
    if not p.exists():
        return ""
    # `.strip()` then one trailing newline: the built-in instructions come from
    # a YAML block scalar and always end with exactly one, so normalizing to the
    # same shape lets a pack reproduce the built-in prompt byte for byte. Doing
    # it here rather than changing the built-in path keeps every existing
    # no-pack run's prompt unchanged.
    return _substitute_doc_facts(p.read_text(encoding="utf-8").strip(), facts) + "\n"


def _doc_specific_instructions(doc_name: str, facts: FactsManifest) -> str:
    text = load_doc_catalog().doc_specific_instructions_for(doc_name)
    if not text and "__" in doc_name:
        # Per-unit expanded docs ({base}__{group}__{unit}) inherit the base
        # catalog entry's instructions.
        text = load_doc_catalog().doc_specific_instructions_for(doc_name.split("__", 1)[0])
    if doc_name == "00_AGENT_BRIEF" and text:
        text = text.format(unit_count=facts.unit_count, canonical_unit_label=facts.canonical_unit_label)
    if doc_name == "01_ARCHITECTURE_MAP" and facts.platform.lower() != "talend" and text:
        # Strip the Talend-specific connection table block for non-Talend platforms.
        talend_marker = "\n      TALEND CONNECTION SEMANTIC TABLE:"
        idx = text.find(talend_marker)
        if idx != -1:
            text = text[:idx].rstrip() + "\n"
    return text
