"""Standalone Phase 2 section parser, extracted from harness.py.

CustomHarnessProvider._parse_phase2_sections delegates here so that
TruePatchExecutor and future callers can import the parser without
pulling in the full harness dependency tree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_server.providers.custom.prompt_packs import ANALYSIS_SECTION_NAMES
from mcp_server.providers.custom.constants import (
    CROSS_JOB_FINDINGS_SECTION,
    GROUP_SUMMARY_SECTION,
    LEGACY_CROSS_CHUNK_SECTION,
)
from mcp_server.providers.custom.section_parser import (
    SectionContractError,
    allcaps_heading_name,
    is_substantive,
    iter_lines_outside_fences,
    normalise_heading,
)

if TYPE_CHECKING:
    from mcp_server.providers.custom.pack_contract import PackContract

ADDITIONAL_FINDINGS_SECTION = "UNCLASSIFIED_OR_EXTRA_FINDINGS"

PHASE2_SECTION_NAMES: tuple[str, ...] = (
    tuple(ANALYSIS_SECTION_NAMES)
    + (
        GROUP_SUMMARY_SECTION,
        CROSS_JOB_FINDINGS_SECTION,
        LEGACY_CROSS_CHUNK_SECTION,
        ADDITIONAL_FINDINGS_SECTION,
    )
)


def parse_phase2_sections(
    text: str,
    extra_section_names: frozenset[str] = frozenset(),
    *,
    contract: "PackContract | None" = None,
    strict: bool = False,
) -> dict[str, str]:
    """Parse a Phase 2 analysis text into a dict of {section_name: content}.

    A KNOWN section name starts its section at ANY heading level -- ``## COMPONENTS``
    and ``## 4. TRANSFORMATION_DETAIL`` are the same section as ``### COMPONENTS``.
    Which level the writer chose is presentation; the name is the contract.  (Before
    this, only ``### `` was recognised, and a run that wrote h2 lost ~95% of its
    evidence silently -- see ``section_parser`` for the verified case.)

    Everything else is unchanged, and two behaviours are load-bearing:

    * An UNKNOWN all-caps name at exactly ``### `` ends the current section and
      sends its content to ``UNCLASSIFIED_OR_EXTRA_FINDINGS``.
    * Any OTHER unrecognised heading -- h1/h2/h4-h6, or a non-all-caps ``### `` --
      does not change the current section; the line is content.  This is what keeps
      ``#### HIGH`` inside ``RISK_FLAGS`` from truncating ``RISK_FLAGS``, and a
      naive "any level" rewrite regresses it.

    The returned dict includes only non-empty sections.  The heading line itself is
    NOT included in the section content (caller must add ``### NAME`` back when
    reassembling).

    When a ``contract`` is given, its section universe replaces the module
    constant: for ``mode: replace`` packs the recognised names are exactly the
    declared sections plus the structural floor -- no union with the legacy 16
    (the analysis_parser.py:47 pivot of docs/one-pack-program.md).

    With ``strict=True`` a substantive document in which no known section was
    matched raises :class:`SectionContractError` instead of returning a result
    that reads as "this document had no sections".  Default False so that no
    existing caller changes behaviour.
    """
    base_names: tuple[str, ...] = (
        contract.ordered_names if contract is not None else PHASE2_SECTION_NAMES
    )
    section_names: set[str] = set(base_names) | extra_section_names
    sections: dict[str, list[str]] = {name: [] for name in section_names}
    current: str = ADDITIONAL_FINDINGS_SECTION
    matched_known = False

    for line, in_fence in iter_lines_outside_fences(text):
        if not in_fence:
            candidate = normalise_heading(line)
            if candidate is not None:
                if candidate == "ADDITIONAL_FINDINGS":
                    candidate = ADDITIONAL_FINDINGS_SECTION
                if candidate in section_names:
                    current = candidate
                    matched_known = True
                    continue
                # Unknown name: only a bare all-caps h3 closes the current section.
                if allcaps_heading_name(line, min_level=3, max_level=3) is not None:
                    current = ADDITIONAL_FINDINGS_SECTION
        sections.setdefault(current, []).append(line)

    if strict and not matched_known and is_substantive(text):
        raise SectionContractError(
            "No known Phase 2 section heading was found in a "
            f"{len(text)}-character analysis. Expected one of "
            f"{sorted(section_names)[:12]}. The writer used a heading shape the "
            "contract does not name -- this is lost evidence, not an empty analysis."
        )

    return {
        name: "\n".join(lines).strip()
        for name, lines in sections.items()
        if "\n".join(lines).strip()
    }
