"""Structural citations are suppressed in prose on the topology canvas.

Why this lives in code and not only in the prompt: `GroundingValidator` rejects a
materially factual grounded answer that cites nothing, so a model told "do not
cite" is simultaneously told "cite or your answer is discarded" and hedges by
citing in both the grounding block and the prose. The read-only cohort prompt has
forbidden evidence ids in visible prose since before this change and was ignored
anyway. The validator reads the structured citation block, never the prose, so
removing references from the prose after validation costs no citation integrity.

Every `observed_*` case below is real output from project `werkorder` topology
threads, kept verbatim so a regression reproduces the actual defect rather than a
paraphrase of it.
"""

from __future__ import annotations

import pytest

from src.harness.readonly_runtime import _humanize_visible_content


def suppressed(text: str) -> str:
    return _humanize_visible_content(text, [], suppress_structural=True)


def labelled(text: str) -> str:
    return _humanize_visible_content(text, [])


class TestObservedDefects:
    """The exact strings the canvas rendered, one test per shape."""

    def test_observed_friendly_label_is_removed(self) -> None:
        # Five of eight observed answers used this form. Suppressing raw keys
        # alone would have missed the dominant case entirely.
        assert suppressed("Outputs to tDBOutput_5 (from the project topology)") == (
            "Outputs to tDBOutput_5"
        )

    def test_observed_label_repeated_in_every_table_row_is_removed(self) -> None:
        row = "| `WPL2` | `Qualification` | Direct map (from the project topology) |"

        assert suppressed(row) == "| `WPL2` | `Qualification` | Direct map |"

    def test_observed_raw_evidence_key_is_removed(self) -> None:
        assert suppressed("Direct map (from topology:werkorder)") == "Direct map"

    def test_observed_opaque_node_id_is_removed_without_eating_bold(self) -> None:
        # The node id came wrapped in its own emphasis markers, immediately after
        # a legitimately bold cell. An earlier implementation cleaned emphasis
        # globally and stripped the neighbouring '**' too.
        cell = (
            "| **Talend-CDC** | Integration platform — "
            "*from topology node landscape-application:483a53d8856431914a25* |"
        )

        assert suppressed(cell) == "| **Talend-CDC** | Integration platform |"

    def test_no_dangling_lead_in_survives(self) -> None:
        """Removing the reference must take "(from" and "(evidence_id:" with it."""
        for text in (
            "Direct map (from the project topology)",
            "Direct map (evidence_id: topology:werkorder)",
            "Direct map [source: topology:werkorder]",
        ):
            result = suppressed(text)
            assert result == "Direct map", result
            for debris in ("(", "from", "evidence", "source"):
                assert debris not in result


class TestWhatMustSurvive:
    """Suppression is narrow. These are not citations and must be untouched."""

    def test_document_citations_are_kept(self) -> None:
        # The user asked for zero provenance on structure while keeping it for
        # RAG: a document is not on screen, so its citation is load-bearing.
        text = "cdc_pack has 7 jobs [1] and uses tMap [2][5]."

        assert suppressed(text) == text

    def test_log_labels_are_kept(self) -> None:
        # A log line is a present-tense claim about a real execution and is not
        # on the canvas; unlabelled it reads as published structure.
        text = "It last ran at 04:12 (from logs)."

        assert suppressed(text) == text

    def test_a_mixed_bracket_keeps_its_non_topology_reference(self) -> None:
        result = suppressed("Two sources disagree (topology:werkorder and doc:12:3)")

        assert "doc:12:3" in result
        assert "topology:" not in result

    def test_plain_language_uncertainty_is_kept(self) -> None:
        # The distinction between a fact and a guess is not provenance.
        text = "This one is inferred: the scan sees traffic but not its contents."

        assert suppressed(text) == text

    def test_markdown_emphasis_is_kept(self) -> None:
        result = suppressed("The **VQ_Quintiq2MX** job maps it (from the project topology).")

        assert result == "The **VQ_Quintiq2MX** job maps it."


class TestTheLabelIsOnlyACitationInsideBrackets:
    """The four words are an ordinary noun phrase as well as a citation.

    The pattern used to delete "the project topology" anywhere it appeared, with
    an OPTIONAL lead-in, so it matched the phrase as a sentence subject and as an
    object of an ordinary preposition. Every sentence below is one a model would
    reasonably write -- the guidance actively asks for the first, since "that is
    not described at this level" is a correct answer -- and every one of them came
    out broken.

    A bracket is the signal that distinguishes a citation from a sentence: prose
    the model parenthesised is prose it considered detachable. Outside one, a
    redundant mention costs four words; deleting it costs the sentence.
    """

    @pytest.mark.parametrize(
        "text",
        [
            # Subject deleted: "does not describe this stream at the landscape level."
            "The project topology does not describe this stream at the landscape level.",
            # Sentence beheaded: ", three designs implement it."
            "From the project topology, three designs implement it.",
            # Words joined: "The flow is confirmedand in the runtime records."
            "The flow is confirmed in the project topology and in the runtime records.",
            # Words joined: "This is notat all."
            "This is not in the project topology at all.",
        ],
    )
    def test_the_label_survives_in_running_prose(self, text: str) -> None:
        assert suppressed(text) == text

    def test_the_label_is_still_removed_inside_a_bracket(self) -> None:
        assert suppressed("Maximo sends work orders to 4PS (from the project topology).") == (
            "Maximo sends work orders to 4PS."
        )

    def test_a_machine_id_is_still_removed_from_running_prose(self) -> None:
        """Ids are never legitimate prose, so the bracket rule does not apply.

        The lead-in goes with them -- "Read via read_topology_structure for the
        detail" must not become "Readfor the detail", which is what taking the
        whitespace from both sides of the match produced.
        """

        assert suppressed("Read via read_topology_structure for the detail.") == (
            "Read for the detail."
        )
        assert suppressed(
            "Confirmed *from topology node landscape-system:sysmaximo* in the scan."
        ) == "Confirmed in the scan."


class TestSurfaceScoping:
    """Off the canvas the label is the only thing separating structure from docs."""

    @pytest.mark.parametrize(
        "text",
        [
            "Outputs to tDBOutput_5 (from the project topology)",
            "Direct map (from topology:werkorder)",
        ],
    )
    def test_default_still_labels_rather_than_suppresses(self, text: str) -> None:
        result = labelled(text)

        assert "the project topology" in result
        # And the raw key never reaches a user on either surface.
        assert "topology:werkorder" not in result

    def test_suppression_is_off_by_default(self) -> None:
        text = "Direct map (from the project topology)"

        assert labelled(text) != suppressed(text)
        assert _humanize_visible_content(text, []) == labelled(text)


class TestIdempotence:
    def test_suppressing_twice_changes_nothing_further(self) -> None:
        """A grounding correction round re-humanizes an already-humanized answer."""
        once = suppressed(
            "| `WPL2` | `Qualification` | Direct map (from the project topology) |"
        )

        assert suppressed(once) == once
