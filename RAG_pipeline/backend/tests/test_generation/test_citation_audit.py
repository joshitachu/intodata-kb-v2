"""Whether a `[N]` in an answer resolves to a reference the model was given.

Nothing read these markers back before. A `[7]` in an answer built from four
references rendered with the same brackets and the same styling as a real
citation, and a reader who tried to follow it found nothing -- having already
believed it.

These tests pin the two halves of the fix: the detection must be narrow enough
not to manufacture warnings out of ordinary prose, and a real dangling marker
must reach the reader rather than only the log.
"""

from __future__ import annotations

import logging

from src.generation.citation_audit import (
    CITATION_REPAIR_NOTICE_PREFIX,
    CitationAudit,
    append_citation_repair_notice,
    audit_citations,
    cited_documents,
    log_citation_audit,
)
from src.schemas import DocumentSource


def _reference(number: int, *, document_id: int | None = None) -> DocumentSource:
    return DocumentSource(
        document_id=document_id if document_id is not None else number,
        filename=f"doc{number}.md",
        score=0.9,
        text="body",
        reference_number=number,
        chunk_indexes=[0],
    )


# --- Detection --------------------------------------------------------------


def test_markers_resolve_against_the_offered_reference_list() -> None:
    audit = audit_citations(
        "Orders flow to the warehouse [1] and are logged [3].",
        [_reference(1), _reference(2), _reference(3)],
    )

    assert audit.resolved == (1, 3)
    assert audit.dangling == ()
    assert audit.unused == (2,)


def test_a_marker_with_no_reference_is_dangling() -> None:
    audit = audit_citations("The job runs nightly [7].", [_reference(1)])

    assert audit.dangling == (7,)
    assert audit.has_dangling
    assert audit.cited_anything is False


def test_a_marker_is_reported_once_however_often_it_is_used() -> None:
    audit = audit_citations("[9] and again [9] and [9].", [_reference(1)])

    assert audit.dangling == (9,)


def test_subscripts_and_indexing_are_not_citations() -> None:
    # An answer about code is full of `rows[0]`. Reading those as fabricated
    # citations would put a false warning on a correct answer.
    audit = audit_citations(
        "The mapping reads rows[0] and args[1] before writing.", [_reference(1)]
    )

    assert audit.dangling == ()
    assert audit.resolved == ()


def test_code_blocks_and_inline_spans_carry_no_citations() -> None:
    answer = (
        "Config below.\n\n```python\nvalues[2] = load()[3]\n```\n\n"
        "Inline `buffer[4]` too, and a real one [1]."
    )

    audit = audit_citations(answer, [_reference(1)])

    assert audit.resolved == (1,)
    assert audit.dangling == ()


def test_a_year_is_not_a_reference_number() -> None:
    audit = audit_citations("Introduced in [2024] per the memo.", [_reference(1)])

    assert audit.dangling == ()


def test_reference_number_zero_is_never_valid() -> None:
    # References are assigned from 1. A `[0]` is either an index or a hallucination.
    audit = audit_citations("See [0].", [_reference(1)])

    assert audit.dangling == (0,)


# --- Repair -----------------------------------------------------------------


def test_the_notice_names_the_unresolved_markers() -> None:
    audit = audit_citations("Claim A [4]. Claim B [5].", [_reference(1)])
    repaired = append_citation_repair_notice("Claim A [4]. Claim B [5].", audit)

    assert "[4], [5]" in repaired
    assert "unverified" in repaired
    # The model's own text is preserved: repair is additive, never destructive.
    assert repaired.startswith("Claim A [4]. Claim B [5].")


def test_a_clean_answer_is_returned_untouched() -> None:
    audit = audit_citations("Grounded claim [1].", [_reference(1)])

    assert append_citation_repair_notice("Grounded claim [1].", audit) == (
        "Grounded claim [1]."
    )


def test_the_notice_is_appended_at_most_once() -> None:
    audit = CitationAudit(dangling=(4,))
    once = append_citation_repair_notice("Claim [4].", audit)
    twice = append_citation_repair_notice(once, audit)

    assert twice == once
    assert once.count(CITATION_REPAIR_NOTICE_PREFIX.strip()) == 1


# --- What the sources panel shows -------------------------------------------


def test_only_the_cited_references_are_returned() -> None:
    references = [_reference(1), _reference(2), _reference(3)]
    audit = audit_citations("Only this one matters [2].", references)

    assert [doc.reference_number for doc in cited_documents(references, audit)] == [2]


def test_an_uncited_answer_still_shows_what_was_retrieved() -> None:
    # Small talk, or a model that ignored the citation rule. Blanking the panel
    # in the second case hides the evidence from the one person able to notice.
    references = [_reference(1), _reference(2)]
    audit = audit_citations("Hello, how can I help?", references)

    assert cited_documents(references, audit) == references


# --- The record -------------------------------------------------------------


def test_a_dangling_citation_is_logged_at_error(caplog) -> None:  # noqa: ANN001
    with caplog.at_level(logging.INFO, logger="src.generation.citation_audit"):
        log_citation_audit(CitationAudit(resolved=(1,), dangling=(9,)), offered=1)

    records = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert records, "fabricated provenance must be recoverable from the log"
    assert "dangling" in records[0].getMessage()


def test_a_clean_audit_is_logged_at_info(caplog) -> None:  # noqa: ANN001
    with caplog.at_level(logging.INFO, logger="src.generation.citation_audit"):
        log_citation_audit(CitationAudit(resolved=(1,), unused=(2,)), offered=2)

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
