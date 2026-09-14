"""The closed fact-state vocabulary (PRD 5.1) and its two helpers.

What these tests pin: the seven spellings and nothing else; that ``weakest``
never invents a state; that a redacted value can never be read as absent,
whichever of the scanner's two redaction signals is present; and that the
structural resolved/unresolved answer wins over the scanner's word.
"""

from __future__ import annotations

from enum import Enum, StrEnum

import pytest

from mcp_server.providers.agent_workspace import fact_state
from mcp_server.providers.agent_workspace.fact_state import (
    STATE_MEANINGS,
    STRENGTH_ORDER,
    FactState,
    is_redaction_marker,
    parse_state,
    state_from_resolution,
    state_of_value,
    weakest,
)

PRD_STATES = (
    "exact",
    "partial",
    "dynamic",
    "unresolved",
    "unsupported",
    "redacted",
    "not-observed",
)


class TestVocabulary:
    def test_exactly_the_prd_states_and_no_others(self) -> None:
        assert sorted(state.value for state in FactState) == sorted(PRD_STATES)
        assert len(FactState) == 7

    def test_a_state_is_its_prd_spelling(self) -> None:
        assert FactState.NOT_OBSERVED == "not-observed"
        assert f"{FactState.REDACTED}" == "redacted"
        assert str(FactState.EXACT) == "exact"

    def test_every_state_has_a_meaning_and_a_strength(self) -> None:
        assert set(STATE_MEANINGS) == set(PRD_STATES)
        assert set(STRENGTH_ORDER) == set(FactState)
        assert len(STRENGTH_ORDER) == len(set(STRENGTH_ORDER))

    def test_the_module_exports_no_second_vocabulary(self) -> None:
        """One enum. A sibling enum here is how a second spelling starts."""

        enums = [
            name
            for name, value in vars(fact_state).items()
            if isinstance(value, type) and issubclass(value, Enum) and value is not StrEnum
        ]
        assert enums == ["FactState"]


class TestParse:
    @pytest.mark.parametrize("spelling", PRD_STATES)
    def test_exact_spellings_parse(self, spelling: str) -> None:
        assert parse_state(spelling) is FactState(spelling)

    @pytest.mark.parametrize("bad", ["Exact", "not_observed", "resolved", "", None, 3, "same_root"])
    def test_anything_else_is_not_a_state(self, bad: object) -> None:
        assert parse_state(bad) is None

    def test_a_state_parses_to_itself(self) -> None:
        assert parse_state(FactState.DYNAMIC) is FactState.DYNAMIC


class TestWeakest:
    def test_exact_alone_is_exact(self) -> None:
        assert weakest([FactState.EXACT]) is FactState.EXACT

    def test_one_dynamic_input_makes_the_derived_fact_dynamic(self) -> None:
        assert weakest([FactState.EXACT, FactState.DYNAMIC, FactState.EXACT]) is FactState.DYNAMIC

    def test_unresolved_beats_dynamic_and_not_observed_beats_everything(self) -> None:
        assert weakest([FactState.DYNAMIC, FactState.UNRESOLVED]) is FactState.UNRESOLVED
        assert weakest(list(FactState)) is FactState.NOT_OBSERVED

    def test_redacted_is_stronger_than_partial(self) -> None:
        """Redacted is established-and-withheld, not half-known."""

        assert weakest([FactState.REDACTED, FactState.PARTIAL]) is FactState.PARTIAL
        assert weakest([FactState.EXACT, FactState.REDACTED]) is FactState.REDACTED

    def test_empty_input_raises_instead_of_defaulting(self) -> None:
        with pytest.raises(ValueError, match="at least one state"):
            weakest([])

    def test_order_independent(self) -> None:
        states = [FactState.PARTIAL, FactState.EXACT, FactState.UNSUPPORTED, FactState.DYNAMIC]
        assert weakest(states) is weakest(list(reversed(states))) is FactState.UNSUPPORTED


class TestResolution:
    @pytest.mark.parametrize(
        "word", ["same_root", "cross_root", "repository_id", "identity", "resolved", "SAME_ROOT"]
    )
    def test_scanner_resolved_words_are_exact(self, word: str) -> None:
        assert state_from_resolution(word) is FactState.EXACT

    def test_unresolved_and_unknown_words_are_unresolved(self) -> None:
        assert state_from_resolution("unresolved") is FactState.UNRESOLVED
        assert state_from_resolution("some_new_scanner_word") is FactState.UNRESOLVED

    def test_empty_word_is_not_observed_and_dynamic_is_dynamic(self) -> None:
        assert state_from_resolution("") is FactState.NOT_OBSERVED
        assert state_from_resolution(None) is FactState.NOT_OBSERVED
        assert state_from_resolution("dynamic") is FactState.DYNAMIC

    def test_the_structural_answer_wins_over_the_word(self) -> None:
        assert state_from_resolution("weird", resolved=True) is FactState.EXACT
        assert state_from_resolution("same_root", resolved=False) is FactState.UNRESOLVED
        assert state_from_resolution("", resolved=True) is FactState.EXACT

    def test_except_that_dynamic_stays_dynamic(self) -> None:
        assert state_from_resolution("dynamic", resolved=True) is FactState.DYNAMIC


class TestValueState:
    @pytest.mark.parametrize("marker", ["[REDACTED]", "[URL_REDACTED]", "[HOST_REDACTED]"])
    def test_a_scanner_marker_is_redacted_never_absent(self, marker: str) -> None:
        assert is_redaction_marker(marker)
        assert state_of_value(marker) is FactState.REDACTED

    def test_a_partially_redacted_value_is_redacted(self) -> None:
        assert state_of_value('"[URL_REDACTED]/path"') is FactState.REDACTED

    def test_an_out_of_band_redaction_flag_is_honoured_even_on_a_clear_value(self) -> None:
        """The descriptor and the marker disagree on real estates; both count."""

        assert state_of_value("plain-looking-value", redacted=True) is FactState.REDACTED
        assert state_of_value("", redacted=True) is FactState.REDACTED

    @pytest.mark.parametrize("empty", [None, "", "   ", '""', "''"])
    def test_empty_is_not_observed(self, empty: object) -> None:
        assert state_of_value(empty) is FactState.NOT_OBSERVED

    @pytest.mark.parametrize(
        "expression",
        [
            "context.url",
            'context.base + "/x"',
            '(String) globalMap.get("k")',
            '"${in.header.x}"',
            "row1.field",
        ],
    )
    def test_runtime_decided_expressions_are_dynamic(self, expression: str) -> None:
        assert state_of_value(expression) is FactState.DYNAMIC

    @pytest.mark.parametrize("literal", ["30", '"orders.out"', "true", '"/services/x"'])
    def test_literals_are_exact(self, literal: str) -> None:
        assert state_of_value(literal) is FactState.EXACT

    def test_non_string_values_are_stringified(self) -> None:
        assert state_of_value(30) is FactState.EXACT
        assert state_of_value(False) is FactState.EXACT
