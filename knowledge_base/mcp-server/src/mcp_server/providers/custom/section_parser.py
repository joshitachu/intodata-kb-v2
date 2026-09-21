"""Deterministic markdown section-heading recognition.

INVARIANT: a transform that loses everything must raise, never return empty.

An empty result from a section splitter is indistinguishable from "this document
genuinely has no sections", so a parser that silently returns ``{}`` turns a
format drift into missing evidence that nothing downstream can detect.  Callers
that know their input must contain sections pass ``strict=True`` and get a
:class:`SectionContractError` instead of a plausible-looking empty dict.

The failure this module exists to prevent (verified, run 258 / project
``ettm_TEST_Claude``): the Phase 2 analysis guide asks the model for
``### COMPONENTS`` (h3, all caps).  The model wrote ``## COMPONENTS`` and
``## 4. TRANSFORMATION_DETAIL`` instead.  Every parser keyed on the literal
regex ``^### ([A-Z][A-Z0-9_ ]+)\\s*$`` matched zero sections and ~95% of the
run's evidence was dropped without a single warning.

HEADING RECOGNITION SPEC v1
---------------------------
* A candidate heading is a line matching ``^#{1,6}\\s+(.+?)\\s*$`` after
  ``str.strip()`` -- any level, h1 through h6.
* The captured title is normalised: uppercased, leading enumeration prefix
  (``4.``, ``10.``, ``2)``, ``4.1.``) removed, runs of characters outside
  ``[A-Z0-9_]`` collapsed to ``_``, leading/trailing ``_`` stripped.
  So ``## 4. TRANSFORMATION_DETAIL`` -> ``TRANSFORMATION_DETAIL`` and
  ``### COMPONENTS`` -> ``COMPONENTS``.
* A normalised name is matched against the caller's known-name set by EXACT
  equality.  NEVER substring containment in either direction: the parser this
  replaces used ``if known in name or name in known``, which mapped the heading
  ``LOW`` onto ``EXECUTION_FLOW`` (because "LOW" is inside "FLOW") and did so
  non-deterministically, since it iterated a frozenset.
* A heading-shaped line inside a fenced code block (``` or ~~~) is NOT a
  heading.  Recognising names at every level widened the blast radius of this:
  under the old h3-only rule a sample containing ``# COMPONENTS`` was inert,
  and under this one it would open a section and truncate the real one.  The
  run-258 analyses happen to contain no heading-shaped lines inside fences, so
  this changes no observed output -- it closes the hole the widening opened.

This module has no dependencies beyond the standard library so that every
caller -- harness phases, the patch lane, tests -- can share one implementation.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterator

__all__ = [
    "STRICT_MIN_CHARS",
    "SectionContractError",
    "allcaps_heading_name",
    "heading_parts",
    "is_substantive",
    "iter_lines_outside_fences",
    "known_section_at_any_level",
    "normalise_heading",
    "parse_named_sections",
]

# Below this many non-whitespace characters a document is too short for
# "no sections found" to be suspicious (a stub, a one-line error note).
STRICT_MIN_CHARS = 200

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
# Leading enumeration only: "4.", "10.", "2)", "4.1." -- a bare leading number
# with no punctuation is left alone so "2024 REVIEW" keeps its year.
_ENUM_PREFIX_RE = re.compile(r"^\d+(?:\.\d+)*[.)]\s*")
# The legacy shape: the author already wrote the name in caps.
_ALLCAPS_TITLE_RE = re.compile(r"^[A-Z][A-Z0-9_ ]+$")
_NON_NAME_RE = re.compile(r"[^A-Z0-9_]+")
# A fence opens with ``` or ~~~ and closes only on the SAME marker, so a ```
# sample inside a ~~~ block does not end it.
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


class SectionContractError(Exception):
    """Raised when a section transform would silently discard its whole input."""


def heading_parts(line: str) -> tuple[int, str] | None:
    """Return ``(level, raw_title)`` for a markdown heading line, else ``None``."""
    match = _HEADING_RE.match(line.strip())
    if match is None:
        return None
    return len(match.group(1)), match.group(2).strip()


def _normalise_title(title: str) -> str | None:
    upper = _ENUM_PREFIX_RE.sub("", title.strip().upper()).strip()
    name = _NON_NAME_RE.sub("_", upper).strip("_")
    return name or None


def normalise_heading(line: str) -> str | None:
    """Return the normalised section name for a heading line, else ``None``.

    ``None`` means "this line is not a heading" (or normalises to nothing at
    all, e.g. ``## ---``).  Heading LEVEL is deliberately ignored: which level
    the model chose is presentation, not meaning.
    """
    parts = heading_parts(line)
    if parts is None:
        return None
    return _normalise_title(parts[1])


def known_section_at_any_level(line: str, known: Collection[str]) -> str | None:
    """Return the normalised name if ``line`` is a heading naming a known section.

    Exact equality against ``known`` -- never substring matching.
    """
    name = normalise_heading(line)
    if name is None:
        return None
    return name if name in known else None


def allcaps_heading_name(
    line: str,
    *,
    min_level: int = 1,
    max_level: int = 6,
    allow_enumerated: bool = False,
) -> str | None:
    """Return the normalised name of an ALL-CAPS heading within a level range.

    This is the "the author shouted a section name" shape -- used to recognise
    names that are not on any known list (an unknown section in model output, a
    plugin-declared section in an ANALYSIS_GUIDE).  A title that is not already
    all-caps is not a section name; ``### Job: orders`` and ``### CRITICAL Risks``
    are prose sub-headings and must not be treated as section boundaries.

    ``allow_enumerated`` decides whether ``## 4. NAME`` counts.  It defaults to
    False for section-splitting callers, where wrongly accepting a heading
    TRUNCATES the section that owns it; name-harvesting callers that only build
    a candidate list can safely pass True.
    """
    parts = heading_parts(line)
    if parts is None:
        return None
    level, title = parts
    if not min_level <= level <= max_level:
        return None
    candidate = _ENUM_PREFIX_RE.sub("", title).strip() if allow_enumerated else title
    if not _ALLCAPS_TITLE_RE.match(candidate):
        return None
    return _normalise_title(title)


def is_substantive(text: str, min_chars: int = STRICT_MIN_CHARS) -> bool:
    """True when ``text`` carries more than ``min_chars`` non-whitespace characters."""
    return len(re.sub(r"\s+", "", text)) > min_chars


def iter_lines_outside_fences(text: str) -> Iterator[tuple[str, bool]]:
    """Yield ``(line, in_fence)`` for every line of ``text``.

    ``in_fence`` is True for the fence delimiters themselves and everything
    between them, so a caller can treat the whole block as literal content.
    Every section splitter must consult this before deciding a line is a
    heading; see the spec note in the module docstring.
    """
    marker: str | None = None
    for line in text.splitlines():
        match = _FENCE_RE.match(line)
        if match is not None:
            token = match.group(1)[0] * 3
            if marker is None:
                marker = token
                yield line, True
                continue
            if token == marker:
                marker = None
                yield line, True
                continue
        yield line, marker is not None


def parse_named_sections(
    text: str,
    known: Collection[str],
    *,
    strict: bool = False,
) -> dict[str, str]:
    """Split ``text`` into ``{known section name: content}``.

    A known name is recognised at ANY heading level (spec above).  The heading
    line itself is not part of the content; text before the first known heading
    is not part of any section and is dropped.  A section that appears twice has
    its bodies concatenated in document order.  A section whose body is empty is
    still reported (with ``""``) -- "declared but empty" is a different fact
    from "never written".

    With ``strict=True`` a substantive document that yields nothing raises
    :class:`SectionContractError` rather than returning ``{}``.
    """
    known_set = frozenset(known)
    sections: dict[str, list[str]] = {}
    observed: list[str] = []
    current: str | None = None

    for line, in_fence in iter_lines_outside_fences(text):
        if not in_fence:
            name = normalise_heading(line)
            if name is not None:
                observed.append(name)
                if name in known_set:
                    current = name
                    sections.setdefault(name, [])
                    continue
        if current is not None:
            sections[current].append(line)

    result = {name: "\n".join(lines).strip() for name, lines in sections.items()}
    if strict and not result and is_substantive(text):
        raise SectionContractError(_no_sections_message(text, known_set, observed))
    return result


def _no_sections_message(
    text: str,
    known: Collection[str],
    observed: Collection[str],
) -> str:
    seen = sorted(set(observed))
    return (
        "No known section heading was found in a "
        f"{len(text)}-character document. "
        f"Headings seen: {seen[:12] or 'none'}. "
        f"Expected one of: {sorted(known)[:12]}. "
        "The writer most likely used a different heading shape than the "
        "contract asked for -- do not treat this as an empty document."
    )
