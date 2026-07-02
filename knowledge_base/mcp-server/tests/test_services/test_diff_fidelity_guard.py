"""Unit tests for services/diff_fidelity_guard.py (G5, Slice 2).

Pure module -- no I/O, no mocking required.
"""
from __future__ import annotations

from mcp_server.services.diff_fidelity_guard import (
    diff_vocabulary,
    extract_mechanical_facts,
    find_unreconciled_facts,
)
from mcp_server.services.patch_section_map import FieldChange


def _field_change(
    job: str = "Job_G1",
    unique: str = "tMap_1",
    param: str = "MAPPING",
    old: str | None = "emp -> pay",
    new: str | None = "emp -> NEW_OUTPUT",
    ctype: str = "tMap",
) -> FieldChange:
    return FieldChange(
        field_path=f"({job}, {unique}, {param})",
        old_value=old,
        new_value=new,
        component_type=ctype,
    )


# ---------------------------------------------------------------------------
# extract_mechanical_facts
# ---------------------------------------------------------------------------


class TestExtractMechanicalFacts:
    def test_backtick_span_extracted(self) -> None:
        assert "tMap_1" in extract_mechanical_facts("Component `tMap_1` was updated.")

    def test_component_style_token_extracted(self) -> None:
        assert "tMap_1" in extract_mechanical_facts("tMap_1 now writes to Payroll_DB.")

    def test_allcaps_token_extracted(self) -> None:
        assert "DB_HOST" in extract_mechanical_facts("Reads the DB_HOST config key.")

    def test_plain_prose_yields_no_facts(self) -> None:
        text = "This group handles payroll processing for the finance team."
        assert extract_mechanical_facts(text) == set()

    def test_empty_text_yields_no_facts(self) -> None:
        assert extract_mechanical_facts("") == set()

    def test_br_tag_citation_does_not_leak_group_name_as_a_fact(self) -> None:
        """A new BR-{GROUP}-NNN evidence citation is not itself a mechanical
        fact (it's governed by the separate G2 invariant guard); the embedded
        group-name fragment must not be extracted and treated as an assertion,
        or a legitimately new citation number would false-positive-reject."""
        facts = extract_mechanical_facts(
            "See BR-GROUP_A-001 for details on this new rule."
        )
        assert "GROUP_A" not in facts

    def test_br_tag_stripped_but_other_facts_in_same_text_still_found(self) -> None:
        facts = extract_mechanical_facts(
            "See BR-GROUP_A-001: `tMap_1` now writes to Payroll_DB."
        )
        assert "GROUP_A" not in facts
        assert "tMap_1" in facts


# ---------------------------------------------------------------------------
# diff_vocabulary
# ---------------------------------------------------------------------------


class TestDiffVocabulary:
    def test_includes_job_component_param_names(self) -> None:
        vocab = diff_vocabulary([_field_change()])
        assert {"Job_G1", "tMap_1", "MAPPING"} <= vocab

    def test_includes_old_and_new_values(self) -> None:
        vocab = diff_vocabulary([_field_change(old="emp -> pay", new="emp -> NEW_OUTPUT")])
        assert "emp -> pay" in vocab
        assert "emp -> NEW_OUTPUT" in vocab

    def test_none_values_not_added(self) -> None:
        vocab = diff_vocabulary([_field_change(old=None, new="emp -> pay")])
        assert None not in vocab

    def test_empty_field_changes_yields_empty_vocab(self) -> None:
        assert diff_vocabulary([]) == set()


# ---------------------------------------------------------------------------
# find_unreconciled_facts
# ---------------------------------------------------------------------------


class TestFindUnreconciledFacts:
    def test_fact_grounded_in_diff_reconciles(self) -> None:
        """A fact that is part of the diff (e.g. the changed component name)
        is reconciled -- not flagged."""
        bad = find_unreconciled_facts(
            rewritten_text="tMap_1 now maps employee records to NEW_OUTPUT.",
            original_text="",
            field_changes=[_field_change()],
        )
        assert bad == set()

    def test_fact_present_in_original_text_reconciles(self) -> None:
        """A carryover fact already in the pre-existing section content is
        reconciled even if it is not part of this diff."""
        bad = find_unreconciled_facts(
            rewritten_text="tOldComponent_9 remains unchanged in this flow.",
            original_text="tOldComponent_9 handles legacy routing.",
            field_changes=[_field_change()],
        )
        assert bad == set()

    def test_fact_absent_from_diff_and_original_is_unreconciled(self) -> None:
        """A fact the amendment invents -- not in the diff, not previously in
        the document -- is rejected (AC1: hallucinated field/connection name)."""
        bad = find_unreconciled_facts(
            rewritten_text="Now connects to Invented_Connection_9 for output.",
            original_text="This group handles payroll processing.",
            field_changes=[_field_change()],
        )
        assert "Invented_Connection_9" in bad

    def test_plain_prose_amendment_has_no_unreconciled_facts(self) -> None:
        """An amendment with no mechanically-checkable facts at all passes
        trivially (AC2: reconcilable/no-fact amendments are accepted)."""
        bad = find_unreconciled_facts(
            rewritten_text="This section now reads more clearly.",
            original_text="This section was previously terse.",
            field_changes=[_field_change()],
        )
        assert bad == set()

    def test_multiple_facts_partial_reconciliation(self) -> None:
        bad = find_unreconciled_facts(
            rewritten_text="tMap_1 and Ghost_Component_5 both changed.",
            original_text="",
            field_changes=[_field_change()],
        )
        assert bad == {"Ghost_Component_5"}
        assert "tMap_1" not in bad
