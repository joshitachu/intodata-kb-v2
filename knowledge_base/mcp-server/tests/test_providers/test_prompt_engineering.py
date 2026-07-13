"""Slice 4: Structural and rendering tests for the revised Phase 2 / Phase 3 prompt templates.

These tests verify:
1. Both templates render without unresolved ${SLOT} tokens.
2. Each template is ASCII-only (no non-ASCII characters).
3. Phase 2 template contains the anti-filler instruction.
4. Phase 2 template contains the output skeleton / required-section checklist.
5. Phase 2 template contains the unit enumeration obligation.
6. Phase 3 template contains the heading-gate rejection language.
7. Phase 3 template contains the doc-pack traceability rule.
8. Phase 3 template contains the filler suppression instruction.
9. Phase 3 template contains the depth floor instruction.
10. Plugin pack override for phase2.md still wins over the default template.
11. No regressions in existing Evidence Register requirement tests.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from mcp_server.providers.custom.models import FactsManifest, UnitFact
from mcp_server.providers.custom.prompt_packs import ANALYSIS_SECTION_NAMES
from mcp_server.providers.custom.prompt_renderer import PromptRenderer
from mcp_server.providers.custom.prompts import doc_prompt, group_analysis_prompt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_facts(names: list[str], platform: str = "generic") -> FactsManifest:
    return FactsManifest(
        project_id="test-proj",
        platform=platform,
        canonical_unit_label="jobs",
        unit_count=len(names),
        groups={"G": len(names)},
        units=[UnitFact(name=n, group="G") for n in names],
    )


def _template_root() -> Path:
    """Return the data/prompt_templates directory relative to this file."""
    here = Path(__file__).resolve()
    # tests/test_providers -> tests -> knowledge_base/mcp-server -> data/prompt_templates
    root = here.parent.parent.parent / "data" / "prompt_templates"
    assert root.is_dir(), f"Template root not found: {root}"
    return root


def _render_phase2(facts: FactsManifest) -> str:
    return group_analysis_prompt(facts, "G")


def _render_phase3(facts: FactsManifest, doc_name: str = "03_DATA_FLOWS_AND_LINEAGE") -> str:
    return doc_prompt(facts, doc_name)


# ---------------------------------------------------------------------------
# Phase 2 template tests
# ---------------------------------------------------------------------------

class TestPhase2Template:
    def test_no_filler_instruction_present(self) -> None:
        """Template must contain the anti-filler instruction."""
        facts = _make_facts(["UnitA", "UnitB"])
        prompt = _render_phase2(facts)
        assert "Do not write" in prompt or "filler word" in prompt or "acknowledgement" in prompt

    def test_filler_words_forbidden_text(self) -> None:
        """Template must explicitly forbid 'Okay' / 'Sure' filler."""
        facts = _make_facts(["UnitA"])
        prompt = _render_phase2(facts)
        lower = prompt.lower()
        # The instruction says not to write those words -- the instruction text itself contains them
        assert "okay" in lower or "sure" in lower  # present as examples of forbidden words

    def test_section_skeleton_all_required_sections(self) -> None:
        """Rendered prompt must contain every ANALYSIS_SECTION_NAMES entry as a ### heading."""
        facts = _make_facts(["UnitA", "UnitB"])
        prompt = _render_phase2(facts)
        for section in ANALYSIS_SECTION_NAMES:
            assert f"### {section}" in prompt, f"Missing section skeleton: ### {section}"

    def test_group_summary_section_present(self) -> None:
        """### GROUP_SUMMARY must appear in the rendered Phase 2 prompt."""
        facts = _make_facts(["UnitA"])
        prompt = _render_phase2(facts)
        assert "### GROUP_SUMMARY" in prompt

    def test_unit_enumeration_obligation(self) -> None:
        """Rendered prompt must contain the unit enumeration obligation text."""
        facts = _make_facts(["UnitA", "UnitB", "UnitC"])
        prompt = _render_phase2(facts)
        # The obligation instructs the model to name every unit in COMPONENTS
        assert "UNIT ENUMERATION OBLIGATION" in prompt or "every unit listed here MUST appear" in prompt

    def test_unit_names_injected(self) -> None:
        """All unit names must appear in the rendered prompt."""
        units = ["Alpha", "Beta", "Gamma"]
        facts = _make_facts(units)
        prompt = _render_phase2(facts)
        for name in units:
            assert name in prompt, f"Unit name missing from prompt: {name}"

    def test_ascii_only(self) -> None:
        """The rendered Phase 2 prompt must contain only printable ASCII characters."""
        facts = _make_facts(["UnitA", "UnitB"])
        prompt = _render_phase2(facts)
        non_ascii = [c for c in prompt if ord(c) > 127]
        assert not non_ascii, f"Non-ASCII characters found: {non_ascii[:10]}"

    def test_no_unresolved_slots(self) -> None:
        """After rendering, no ${SLOT} tokens should remain in the output."""
        facts = _make_facts(["UnitA"])
        prompt = _render_phase2(facts)
        remaining = re.findall(r"\$\{[A-Z_]+\}", prompt)
        assert not remaining, f"Unresolved slots in Phase 2 prompt: {remaining}"

    def test_mandatory_section_structure_heading(self) -> None:
        """Template must contain 'MANDATORY SECTION STRUCTURE' or equivalent checklist header."""
        facts = _make_facts(["UnitA"])
        prompt = _render_phase2(facts)
        assert "MANDATORY SECTION STRUCTURE" in prompt

    def test_section_completeness_enforcement_text(self) -> None:
        """Template must instruct the model not to omit or rename sections."""
        facts = _make_facts(["UnitA"])
        prompt = _render_phase2(facts)
        lower = prompt.lower()
        assert "do not omit" in lower or "do not rename" in lower or "missing or empty heading" in lower

    def test_depth_floor_instruction(self) -> None:
        """Phase 2 prompt must include a depth floor / minimum content instruction."""
        facts = _make_facts(["UnitA"])
        prompt = _render_phase2(facts)
        lower = prompt.lower()
        assert "at least 3" in lower or "too thin" in lower

    def test_talend_platform_renders_without_error(self) -> None:
        """Phase 2 renders correctly for the talend platform (uses talend.md section file)."""
        facts = _make_facts(["Job1", "Job2"], platform="talend")
        prompt = _render_phase2(facts)
        assert "### COMPONENTS" in prompt
        remaining = re.findall(r"\$\{[A-Z_]+\}", prompt)
        assert not remaining, f"Unresolved slots (talend): {remaining}"


# ---------------------------------------------------------------------------
# Phase 3 template tests
# ---------------------------------------------------------------------------

class TestPhase3Template:
    def test_per_job_prompt_scope_is_explicit(self) -> None:
        facts = _make_facts(["JobA", "JobB"], platform="talend")
        prompt = doc_prompt(facts, "01_INTERFACE_TEMPLATE__G__JobA", scoped_unit="JobA")
        assert "exactly one scanned job" in prompt
        assert "`JobA`" in prompt
        assert "any other job" in prompt

    def test_filler_suppression_instruction(self) -> None:
        """Rendered Phase 3 prompt must contain the filler suppression instruction."""
        facts = _make_facts(["UnitA"])
        prompt = _render_phase3(facts)
        lower = prompt.lower()
        assert "filler" in lower or "acknowledgement" in lower or "meta-commentary" in lower

    def test_heading_gate_rejection_language(self) -> None:
        """Rendered prompt must state that missing headings cause rejection."""
        facts = _make_facts(["UnitA"])
        prompt = _render_phase3(facts)
        lower = prompt.lower()
        assert "rejected" in lower or "rejection" in lower

    def test_doc_pack_traceability_rule(self) -> None:
        """Rendered prompt must contain the doc-pack traceability rule."""
        facts = _make_facts(["UnitA"])
        prompt = _render_phase3(facts)
        assert "DOC-PACK TRACEABILITY RULE" in prompt or "doc-pack" in prompt.lower()

    def test_depth_floor_instruction(self) -> None:
        """Rendered Phase 3 prompt must include a depth floor instruction."""
        facts = _make_facts(["UnitA"])
        prompt = _render_phase3(facts)
        lower = prompt.lower()
        assert "at least 3" in lower or "too thin" in lower

    def test_evidence_register_requirement_preserved(self) -> None:
        """Existing Evidence Register requirement must still be present (regression guard)."""
        facts = _make_facts(["UnitA"])
        prompt = _render_phase3(facts)
        assert "## Evidence Register" in prompt
        assert "| Topic | Units / Scope | Evidence Source | Source Artifact / Path | Confidence |" in prompt

    def test_mandatory_headings_block_present(self) -> None:
        """Rendered prompt must contain the MANDATORY HEADINGS block with doc headings."""
        facts = _make_facts(["UnitA"])
        prompt = _render_phase3(facts, doc_name="02_COMPONENT_INVENTORY")
        assert "MANDATORY HEADINGS" in prompt
        # doc_catalog headings for 02_COMPONENT_INVENTORY
        assert "## Canonical Unit Inventory" in prompt

    def test_doc_name_slot_resolved(self) -> None:
        """DOC_NAME slot must be resolved in rendered Phase 3 prompt."""
        facts = _make_facts(["UnitA"])
        prompt = _render_phase3(facts, doc_name="04_BUSINESS_RULES_AND_TRANSFORMATIONS")
        assert "04_BUSINESS_RULES_AND_TRANSFORMATIONS" in prompt
        remaining = re.findall(r"\$\{[A-Z_]+\}", prompt)
        assert not remaining, f"Unresolved slots: {remaining}"

    def test_ascii_only(self) -> None:
        """The rendered Phase 3 prompt must contain only printable ASCII characters."""
        facts = _make_facts(["UnitA", "UnitB"])
        prompt = _render_phase3(facts)
        non_ascii = [c for c in prompt if ord(c) > 127]
        assert not non_ascii, f"Non-ASCII characters found: {non_ascii[:10]}"

    def test_no_unresolved_slots(self) -> None:
        """After rendering, no ${SLOT} tokens should remain in Phase 3 output."""
        facts = _make_facts(["UnitA"])
        prompt = _render_phase3(facts, doc_name="06_OPERATIONS_TESTING_AND_DEBUGGING")
        remaining = re.findall(r"\$\{[A-Z_]+\}", prompt)
        assert not remaining, f"Unresolved slots in Phase 3 prompt: {remaining}"

    def test_newly_added_unit_obligation(self) -> None:
        """Prompt must state that canonical units absent from doc-pack must still be named."""
        facts = _make_facts(["UnitA"])
        prompt = _render_phase3(facts)
        lower = prompt.lower()
        assert "do not omit the unit" in lower or "omit" in lower or "newly added" in lower


# ---------------------------------------------------------------------------
# Plugin pack override tests
# ---------------------------------------------------------------------------

class TestPluginPackOverride:
    def test_plugin_phase2_override_takes_precedence(self, tmp_path: Path) -> None:
        """A plugin pack with prompts/phase2.md overrides the default template body."""
        pack_dir = tmp_path / "pack"
        (pack_dir / "prompts").mkdir(parents=True)
        override_text = "PLUGIN PHASE2 OVERRIDE -- do not copy content from default template"
        (pack_dir / "prompts" / "phase2.md").write_text(override_text, encoding="utf-8")

        facts = _make_facts(["UnitA"])
        prompt = group_analysis_prompt(facts, "G", plugin_pack_dir=pack_dir)
        # The preamble is taken from the plugin file, then the rest of the template body follows
        assert "PLUGIN PHASE2 OVERRIDE" in prompt

    def test_default_template_used_when_no_pack_override(self, tmp_path: Path) -> None:
        """Without prompts/phase2.md, the default template body is used."""
        pack_dir = tmp_path / "pack_no_override"
        pack_dir.mkdir()

        facts = _make_facts(["UnitA"])
        prompt = group_analysis_prompt(facts, "G", plugin_pack_dir=pack_dir)
        # Default template contains the new anti-filler header
        assert "MANDATORY SECTION STRUCTURE" in prompt


# ---------------------------------------------------------------------------
# Template file raw-content checks (independent of rendering)
# ---------------------------------------------------------------------------

class TestTemplateFileContent:
    def test_phase2_template_ascii_only(self) -> None:
        """Raw phase2_analysis.md template file must be ASCII-only."""
        tmpl = (_template_root() / "phase2_analysis.md").read_text(encoding="utf-8")
        non_ascii = [c for c in tmpl if ord(c) > 127]
        assert not non_ascii, f"Non-ASCII chars in phase2_analysis.md: {non_ascii[:10]}"

    def test_phase3_template_ascii_only(self) -> None:
        """Raw phase3_doc.md template file must be ASCII-only."""
        tmpl = (_template_root() / "phase3_doc.md").read_text(encoding="utf-8")
        non_ascii = [c for c in tmpl if ord(c) > 127]
        assert not non_ascii, f"Non-ASCII chars in phase3_doc.md: {non_ascii[:10]}"

    def test_phase2_template_has_anti_filler_header(self) -> None:
        """Raw phase2_analysis.md must start with the anti-filler block before ${PREAMBLE}."""
        tmpl = (_template_root() / "phase2_analysis.md").read_text(encoding="utf-8")
        # Anti-filler must precede the ${PREAMBLE} slot
        anti_filler_idx = tmpl.find("Do not echo")
        preamble_idx = tmpl.find("${PREAMBLE}")
        assert anti_filler_idx != -1, "Anti-filler instruction not found in phase2_analysis.md"
        assert anti_filler_idx < preamble_idx, "Anti-filler must appear before ${PREAMBLE}"

    def test_phase3_template_has_anti_filler_header(self) -> None:
        """Raw phase3_doc.md must start with the anti-filler block before ${PREAMBLE}."""
        tmpl = (_template_root() / "phase3_doc.md").read_text(encoding="utf-8")
        anti_filler_idx = tmpl.find("Do not echo")
        preamble_idx = tmpl.find("${PREAMBLE}")
        assert anti_filler_idx != -1, "Anti-filler instruction not found in phase3_doc.md"
        assert anti_filler_idx < preamble_idx, "Anti-filler must appear before ${PREAMBLE}"

    def test_phase2_unit_enumeration_obligation_present(self) -> None:
        """Raw phase2_analysis.md must contain the UNIT ENUMERATION OBLIGATION block."""
        tmpl = (_template_root() / "phase2_analysis.md").read_text(encoding="utf-8")
        assert "UNIT ENUMERATION OBLIGATION" in tmpl

    def test_phase3_doc_pack_traceability_rule_present(self) -> None:
        """Raw phase3_doc.md must contain the DOC-PACK TRACEABILITY RULE block."""
        tmpl = (_template_root() / "phase3_doc.md").read_text(encoding="utf-8")
        assert "DOC-PACK TRACEABILITY RULE" in tmpl

    def test_phase3_heading_rejection_language_present(self) -> None:
        """Raw phase3_doc.md must state that missing headings cause rejection."""
        tmpl = (_template_root() / "phase3_doc.md").read_text(encoding="utf-8")
        assert "REJECTED" in tmpl or "rejected" in tmpl

    def test_phase2_no_slots_in_static_text(self) -> None:
        """No unexpected ${SLOT} patterns should appear in the static body text of phase2_analysis.md."""
        tmpl = (_template_root() / "phase2_analysis.md").read_text(encoding="utf-8")
        # Known slots that must be present (they are rendered by prompts.py)
        known_slots = {
            "PREAMBLE", "GROUP", "DOC_COUNT", "DOC_LIST", "UNIT_LIST",
            "FACTS_SUMMARY", "PLATFORM_ANALYSIS_REQUIREMENTS", "SECTION_HEADINGS",
            "PLATFORM_SECTION_DEPTH", "CANONICAL_UNIT_COUNT", "CANONICAL_UNIT_LABEL",
        }
        found_slots = set(re.findall(r"\$\{([A-Z_]+)\}", tmpl))
        unknown = found_slots - known_slots
        assert not unknown, f"Unknown ${{}}\\ slots in phase2_analysis.md: {unknown}"
