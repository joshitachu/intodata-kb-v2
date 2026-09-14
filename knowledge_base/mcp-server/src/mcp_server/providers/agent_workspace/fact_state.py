"""The closed vocabulary for how well a parsed fact is established (PRD 5.1).

Seven states, and only seven. Packs and agents may INTERPRET them -- decide
that a ``dynamic`` cadence is worth a paragraph and a ``not-observed`` one a
line -- but may not redefine them, add to them, or collapse two into one. The
enum is therefore closed by design: it is a ``StrEnum`` so a state serialises
to the PRD's own spelling and compares equal to it, and nothing here accepts
a free-form string as a state without going through :func:`parse_state`.

Why a vocabulary at all, when the scanner already says ``same_root`` or
``unresolved``? Because the scanner speaks several dialects. A dependency edge
says ``resolution: same_root``; a channel says ``channel_binding: literal`` or
``dynamic: true``; a context variable says nothing at all and carries a
``[URL_REDACTED]`` marker as its VALUE; a mapping row says ``classification:
dynamic``. An agent reading five spellings of "we could not pin this down"
treats them as five different things. One vocabulary, applied by the index
builder at the boundary, is what makes ``grep '"state": "unresolved"'`` return
every unresolved fact in the estate rather than one dialect's share of them.

The two helpers exist because two failures are on record:

* **Redaction was read as absence.** Generated documentation asserted that
  configuration was unavailable while the scan held it (recorded in
  ``knowledge_base/CLAUDE.md`` as a verified finding). On a real production
  ``scan/contexts.json`` the redaction descriptors and the value markers do
  not agree with each other: a base-URL variable carries a redaction
  descriptor per environment, while the client-secret variable beside it
  carries the ``[REDACTED]`` marker in every environment and an EMPTY
  descriptor map. A consumer keyed on descriptors alone reports the secret as
  a configured literal; one keyed on values alone reports the URL as a
  literal. :func:`state_of_value` reads both.
* **A derived fact inherited the strongest input, not the weakest.** A
  relationship built from one exact call edge and one dynamic target is not
  exact. :func:`weakest` is the combiner every derived index row goes through,
  and it raises on an empty input rather than inventing a default.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum


class FactState(StrEnum):
    """How well a fact is established. Closed; see the module docstring."""

    #: Statically determined and fully read by the parser.
    EXACT = "exact"
    #: Partly established: the parser read some of it and says so.
    PARTIAL = "partial"
    #: Exists, but its value is decided at run time (a ``context.x`` reference,
    #: a ``globalMap`` lookup, a concatenation). The shape is known; the value
    #: is not.
    DYNAMIC = "dynamic"
    #: A reference the parser could not bind to a target it knows about.
    UNRESOLVED = "unresolved"
    #: A construct the parser does not model. It exists and runs; the evidence
    #: cannot describe it.
    UNSUPPORTED = "unsupported"
    #: Established and WITHHELD. The fact exists and was read; its value is
    #: not published. Never to be confused with absence.
    REDACTED = "redacted"
    #: Looked for and not seen. The only state that means "nothing".
    NOT_OBSERVED = "not-observed"


#: Strongest first. Used by :func:`weakest`; the position is the strength.
#:
#: ``redacted`` ranks directly below ``exact`` on purpose: a redacted value is
#: one the parser read exactly and this lane declines to show, so a derived
#: fact that rests on it is as well-founded as an exact one and merely cannot
#: be quoted. Ranking it below ``dynamic`` or ``unresolved`` would turn every
#: relationship that touches a redacted URL into a weaker claim than one that
#: touches a ``context.x`` reference, which is backwards.
STRENGTH_ORDER: tuple[FactState, ...] = (
    FactState.EXACT,
    FactState.REDACTED,
    FactState.PARTIAL,
    FactState.DYNAMIC,
    FactState.UNRESOLVED,
    FactState.UNSUPPORTED,
    FactState.NOT_OBSERVED,
)

_STRENGTH: dict[FactState, int] = {state: rank for rank, state in enumerate(STRENGTH_ORDER)}

#: One line per state, for the index manifest. Kept here, beside the enum, so
#: the vocabulary and its gloss cannot drift apart.
STATE_MEANINGS: dict[str, str] = {
    FactState.EXACT.value: "statically determined and fully read by the parser",
    FactState.PARTIAL.value: "partly established; the parser read some of it and says so",
    FactState.DYNAMIC.value: (
        "exists, but the value is decided at run time (context reference, "
        "globalMap lookup, concatenation)"
    ),
    FactState.UNRESOLVED.value: "a reference the parser could not bind to a known target",
    FactState.UNSUPPORTED.value: "a construct the parser does not model; it exists and runs",
    FactState.REDACTED.value: (
        "established and withheld: the value was read and is not published. Never absence."
    ),
    FactState.NOT_OBSERVED.value: "looked for and not seen; the only state that means nothing",
}


def parse_state(value: object) -> FactState | None:
    """The state spelled by ``value``, or ``None`` when it is not one.

    Exact spelling only. ``"Exact"``, ``"not_observed"`` and ``"resolved"`` are
    all ``None``: the vocabulary is closed, and a lenient parser is how a
    second spelling gets into a file and then into a grep that misses it.
    """

    if isinstance(value, FactState):
        return value
    if not isinstance(value, str):
        return None
    try:
        return FactState(value)
    except ValueError:
        return None


def weakest(states: Iterable[FactState]) -> FactState:
    """The least-established state among ``states``.

    The combiner for every derived row: a relationship inferred from an exact
    connection and an unresolved target is unresolved, not exact. Raises
    ``ValueError`` on an empty input -- a derived fact with no inputs has no
    state, and defaulting one would be exactly the silent invention this
    module exists to stop.
    """

    ranked = sorted((state for state in states), key=lambda state: _STRENGTH[state])
    if not ranked:
        raise ValueError("weakest() needs at least one state; a derived fact has inputs")
    return ranked[-1]


# ---------------------------------------------------------------------------
# Scanner dialects
# ---------------------------------------------------------------------------

#: Resolution words the scanner uses for a reference it bound. Read off
#: ``scan/manifest.json`` (``resolution``, ``reference_source``) and the exact
#: dependency tables: ``same_root`` on every one of the 275 edges of the
#: reference estate, ``repository_id`` on context links, ``identity`` as the
#: reference source. The cross-root spellings come from the scanner's own
#: vocabulary for composite bundles.
_RESOLVED_WORDS: frozenset[str] = frozenset(
    {
        "same_root",
        "cross_root",
        "support_root",
        "repository_id",
        "identity",
        "resolved",
        "literal",
        "built-in",
    }
)
_DYNAMIC_WORDS: frozenset[str] = frozenset({"dynamic"})
_UNSUPPORTED_WORDS: frozenset[str] = frozenset({"unsupported", "not_modelled", "not_modeled"})


def state_from_resolution(resolution: object, *, resolved: bool | None = None) -> FactState:
    """Map a scanner resolution word onto the vocabulary.

    ``resolved`` is the STRUCTURAL answer when the caller has one -- an edge
    sitting in ``dependencies.edges`` rather than ``dependencies.unresolved``
    is resolved whatever word it carries -- and it wins over the word, except
    that a ``dynamic`` word stays dynamic: a ``tRunJob`` with
    ``USE_DYNAMIC_JOB`` may have bound a default target and still picks its
    real one at run time.

    An unknown non-empty word maps to ``unresolved``. That is the conservative
    direction: a scanner release that introduces a new resolved spelling
    makes a few rows read weaker than they are, which a reader can check,
    rather than stronger than they are, which nobody would.
    """

    word = str(resolution or "").strip().casefold()
    if word in _DYNAMIC_WORDS:
        return FactState.DYNAMIC
    if resolved is True:
        return FactState.EXACT
    if resolved is False:
        return FactState.UNRESOLVED
    if not word:
        return FactState.NOT_OBSERVED
    if word in _RESOLVED_WORDS:
        return FactState.EXACT
    if word in _UNSUPPORTED_WORDS:
        return FactState.UNSUPPORTED
    return FactState.UNRESOLVED


#: The scanner's redaction markers, as written into a VALUE slot:
#: ``[REDACTED]``, ``[URL_REDACTED]``, ``[HOST_REDACTED]``. Matched anywhere in
#: the value -- a partially redacted value is a redacted value, and the safe
#: direction is to say so.
_REDACTION_MARKER_RE = re.compile(r"\[(?:[A-Z0-9]+_)*REDACTED(?:_[A-Z0-9]+)*\]")

#: Run-time-decided expression shapes. The first four terms are the scanner's
#: own literal-channel test (``enterprise/workspace.py::_literal_channel``),
#: kept identical so the two lanes agree on what "dynamic" means; the rest are
#: the Talend expression forms that reach a parameter value without one of
#: those four (a typed ``globalMap`` cast, a row-column reference).
_DYNAMIC_RE = re.compile(r"context\.|globalMap|\+|\$\{|\(String\)|\brow\d*\.")


def is_redaction_marker(value: object) -> bool:
    """True when ``value`` carries one of the scanner's redaction markers."""

    return isinstance(value, str) and _REDACTION_MARKER_RE.search(value) is not None


def state_of_value(value: object, *, redacted: bool = False) -> FactState:
    """The state of one configuration or parameter VALUE, never the value itself.

    ``redacted`` is the caller's out-of-band knowledge (a redaction descriptor
    for this environment, a sensitive-name rule). It and the in-band marker
    are both honoured, because on the reference estate they disagree: the
    URL variables carry descriptors and the secret carries only the marker.

    Order matters. Redaction is checked before emptiness so that a redacted
    value can never read as ``not-observed`` -- that inversion is the verified
    historical defect this function exists to prevent.
    """

    if redacted or is_redaction_marker(value):
        return FactState.REDACTED
    if value is None:
        return FactState.NOT_OBSERVED
    text = str(value).strip()
    if text in {"", '""', "''"}:
        return FactState.NOT_OBSERVED
    if _DYNAMIC_RE.search(text):
        return FactState.DYNAMIC
    return FactState.EXACT
