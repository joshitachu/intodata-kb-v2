"""Bind the ``[N]`` markers a model wrote to the references it was given.

``SYSTEM_PROMPT`` rule 3 tells the model that every factual claim must cite a
source ``[N]``, and until this module existed nothing on the production path ever
read those markers back. The consequences were both directions of wrong:

* A ``[7]`` in an answer built from four references rendered as a citation. It
  looks exactly like a real one -- same brackets, same superscript styling -- and
  a reader who tries to follow it finds nothing, having already believed it.
* ``QueryResponse.sources`` was every merged document, cited or not, so the
  sources panel asserted provenance for documents the answer never used.

What this module does NOT do is entailment: it cannot tell whether reference 3
actually says what the sentence next to ``[3]`` claims. That check does not exist
anywhere in this codebase. Existence-checking is the honest subset -- it is
deterministic, needs no model call and no API key, and it catches the failure
mode that is provably present. Anything stronger is a separate piece of work and
should not be faked by a lexical-overlap heuristic dressed up as verification.

Repair is additive, never destructive. The answer streams to the user token by
token, so by the time the markers can be checked the text is already on screen;
rewriting it would make the live text and the persisted text disagree. The same
reasoning ``append_truncation_notice`` documents applies here, and the same shape
of fix follows from it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from src.schemas import DocumentSource

logger = logging.getLogger(__name__)

#: A citation marker. Bounded to three digits: a reference list is capped at
#: ``settings.max_result_documents``, so `[2024]` is a year in prose, not a
#: citation, and treating it as a dangling one would manufacture a warning.
#:
#: The negative lookbehind rejects subscripts and indexing -- ``rows[0]``,
#: ``args[1]`` -- which are ordinary text in an answer about code and are not
#: citations. A marker legitimately follows a space, a word plus space, or
#: punctuation, never an identifier character.
_CITATION_RE = re.compile(r"(?<![\w\]])\[(\d{1,3})\]")

#: Fenced blocks and inline spans are quoted material: an array index or a
#: config snippet inside one is not a claim and carries no citation.
_FENCED_CODE_RE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")

CITATION_REPAIR_NOTICE_PREFIX = (
    "\n\n---\n\n"
    "**⚠ Some citations in this answer do not resolve.**"
)


@dataclass(frozen=True, slots=True)
class CitationAudit:
    """Which reference numbers an answer used, and which of them were real."""

    #: Markers that resolve to a reference, in first-use order.
    resolved: tuple[int, ...] = ()
    #: Markers with no matching reference. These are fabricated provenance.
    dangling: tuple[int, ...] = ()
    #: References the model was offered and never used.
    unused: tuple[int, ...] = ()

    @property
    def has_dangling(self) -> bool:
        return bool(self.dangling)

    @property
    def cited_anything(self) -> bool:
        return bool(self.resolved)


def _prose_only(answer: str) -> str:
    """Strip code from an answer so only claim-bearing prose is scanned."""

    without_fences = _FENCED_CODE_RE.sub(" ", answer)
    return _INLINE_CODE_RE.sub(" ", without_fences)


def audit_citations(
    answer: str, document_sources: Sequence[DocumentSource]
) -> CitationAudit:
    """Resolve every ``[N]`` in ``answer`` against the offered reference list.

    Pure and deterministic. ``document_sources`` is the same list whose
    ``reference_number`` fields were interpolated into the prompt, so a marker is
    valid exactly when some offered document carries that number.
    """

    available = {
        document.reference_number
        for document in document_sources
        if document.reference_number
    }

    resolved: list[int] = []
    dangling: list[int] = []
    for match in _CITATION_RE.finditer(_prose_only(answer)):
        number = int(match.group(1))
        bucket = resolved if number in available else dangling
        if number not in bucket:
            bucket.append(number)

    unused = tuple(sorted(available - set(resolved)))
    return CitationAudit(
        resolved=tuple(resolved), dangling=tuple(dangling), unused=unused
    )


def citation_repair_notice(audit: CitationAudit) -> str:
    """The visible notice for an answer that cited references it was not given."""

    markers = ", ".join(f"[{number}]" for number in audit.dangling)
    return (
        f"{CITATION_REPAIR_NOTICE_PREFIX} "
        f"{markers} {'point' if len(audit.dangling) != 1 else 'points'} to no "
        "listed source, so the claim next to it is unsupported by the material "
        "below. Treat those statements as unverified."
    )


def append_citation_repair_notice(answer: str, audit: CitationAudit) -> str:
    """Append the dangling-citation notice, once.

    Idempotent on the prefix rather than on the whole string: the marker list
    varies, but a path that marks twice would still be caught.

    The model's text is NOT rstripped first, unlike ``append_truncation_notice``.
    The streaming caller publishes ``repaired[len(answer):]`` as one final delta,
    and ``harness/evidence.py`` asserts byte equality between the streamed answer
    and the persisted one -- so trimming here would make the two differ by
    exactly the whitespace that was trimmed and log a spurious divergence error.
    The notice opens with its own blank line and rule, so the trim bought nothing
    visually in the first place.
    """

    if not audit.has_dangling:
        return answer
    if CITATION_REPAIR_NOTICE_PREFIX.strip() in answer:
        return answer
    return answer + citation_repair_notice(audit)


def cited_documents(
    document_sources: Sequence[DocumentSource], audit: CitationAudit
) -> list[DocumentSource]:
    """The subset of references the answer actually used.

    Returns the full list when the answer cited nothing. That is not a fallback
    for convenience: an answer with no markers is either small talk (where an
    empty panel is right and the caller decides) or a model that ignored the
    citation rule, and silently blanking the panel in the second case would hide
    the retrieved evidence from the one person able to notice the omission.
    """

    if not audit.cited_anything:
        return list(document_sources)
    cited = set(audit.resolved)
    return [
        document
        for document in document_sources
        if document.reference_number in cited
    ]


def log_citation_audit(audit: CitationAudit, *, offered: int) -> None:
    """Record the binding result. Dangling citations are an ERROR, not a note.

    A fabricated reference number is the model asserting provenance that does not
    exist; it is the same class of failure as an ungrounded claim and has to be
    recoverable from the log rather than inferred from a user complaint.
    """

    if audit.has_dangling:
        logger.error(
            "Citation audit: %d dangling marker(s) %s against %d offered "
            "reference(s); resolved=%s",
            len(audit.dangling),
            list(audit.dangling),
            offered,
            list(audit.resolved),
        )
        return
    logger.info(
        "Citation audit: %d/%d references cited, %d unused",
        len(audit.resolved),
        offered,
        len(audit.unused),
    )


__all__ = [
    "CITATION_REPAIR_NOTICE_PREFIX",
    "CitationAudit",
    "append_citation_repair_notice",
    "audit_citations",
    "citation_repair_notice",
    "cited_documents",
    "log_citation_audit",
]
