"""Replayed conversation history must not launder an unevidenced answer.

The observed failure: a turn asserted that two systems which do not exist in the
project were present in its landscape. Three turns later those systems were
restated as fact, with their own business objects and directions, and the
project's totals had silently shifted to accommodate them. A clean thread asked
the identical questions and answered correctly, which is what identifies history
-- rather than the artifact or the tools -- as the carrier.

Nothing distinguished that answer from an evidence-backed one once it was
replayed: history is assembled as role/content text, so a fabrication and a
grounded citation arrive in the same shape. The flag the UI already renders as
"No current-turn evidence" is the discriminator, and this is it applied to the
prompt so the badge and the model can never disagree.
"""

from __future__ import annotations

from src.harness.agent_loop_context import (
    _UNVERIFIED_REPLAY_NOTE,
    _is_zero_evidence,
    _replayed_content,
)


class _Message:
    def __init__(self, role: str, content: str, structured_answer=None) -> None:
        self.role = role
        self.content = content
        self.structured_answer = structured_answer


def _answer(content: str, *, zero_evidence: bool) -> _Message:
    return _Message(
        "assistant",
        content,
        {"evidence_summary": {"zero_evidence": zero_evidence}},
    )


class TestUnevidencedAnswersAreMarked:
    def test_a_zero_evidence_answer_is_marked_when_replayed(self) -> None:
        replayed = _replayed_content(
            _answer("Salesforce sends Opportunities to SAP.", zero_evidence=True)
        )

        assert replayed.startswith(_UNVERIFIED_REPLAY_NOTE)
        # Marked, NOT dropped: the thread's pronoun resolution is good and
        # depends on continuity, so removing the turn would break something that
        # works to fix something that does not.
        assert "Salesforce sends Opportunities to SAP." in replayed

    def test_an_evidenced_answer_is_replayed_verbatim(self) -> None:
        message = _answer("Maximo sends Invoices to 4PS.", zero_evidence=False)

        assert _replayed_content(message) == "Maximo sends Invoices to 4PS."

    def test_a_user_message_is_never_marked(self) -> None:
        """A question cannot be unevidenced -- it is not a claim."""
        message = _Message("user", "which streams run to 4PS?")
        message.structured_answer = {"evidence_summary": {"zero_evidence": True}}

        assert _replayed_content(message) == "which streams run to 4PS?"

    def test_a_missing_flag_reads_as_evidenced(self) -> None:
        """Absent provenance is not proof of fabrication.

        Marking a sound answer as unverified would teach the model to distrust
        its own grounded work, which is a worse failure than the one this fixes.
        """
        assert _is_zero_evidence(_Message("assistant", "text")) is False
        assert _is_zero_evidence(_Message("assistant", "text", {})) is False
        assert _is_zero_evidence(_Message("assistant", "text", {"evidence_summary": None})) is False
        assert (
            _is_zero_evidence(_Message("assistant", "text", {"evidence_summary": {}})) is False
        )

    def test_a_non_boolean_flag_is_not_treated_as_true(self) -> None:
        """Persisted JSON is not schema-checked here, so identity-compare."""
        for value in ("true", 1, [], {}):
            message = _Message(
                "assistant", "text", {"evidence_summary": {"zero_evidence": value}}
            )
            assert _is_zero_evidence(message) is False

    def test_the_note_tells_the_model_to_re_look_up_rather_than_repeat(self) -> None:
        assert "Do not repeat any of them as fact" in _UNVERIFIED_REPLAY_NOTE
        assert "look the subject up again" in _UNVERIFIED_REPLAY_NOTE
