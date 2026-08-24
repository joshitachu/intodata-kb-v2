"""Standalone Phase 2 section parser, extracted from harness.py.

CustomHarnessProvider._parse_phase2_sections delegates here so that
TruePatchExecutor and future callers can import the parser without
pulling in the full harness dependency tree.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from mcp_server.providers.custom.prompt_packs import ANALYSIS_SECTION_NAMES
from mcp_server.providers.custom.constants import (
    CROSS_JOB_FINDINGS_SECTION,
    GROUP_SUMMARY_SECTION,
    LEGACY_CROSS_CHUNK_SECTION,
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
) -> dict[str, str]:
    """Parse a Phase 2 analysis text into a dict of {section_name: content}.

    Recognises all-caps h3 headings (``### SECTION_NAME``). All-caps sub-headings
    inside a section (e.g. ``#### HIGH`` inside RISK_FLAGS) do not break the owning
    section.  Content for unrecognised headings accumulates under
    ``UNCLASSIFIED_OR_EXTRA_FINDINGS``.

    The returned dict includes only non-empty sections.  The heading line itself is
    NOT included in the section content (caller must add ``### NAME`` back when
    reassembling).

    When a ``contract`` is given, its section universe replaces the module
    constant: for ``mode: replace`` packs the recognised names are exactly the
    declared sections plus the structural floor -- no union with the legacy 16
    (the analysis_parser.py:47 pivot of docs/one-pack-program.md).
    """
    base_names: tuple[str, ...] = (
        contract.ordered_names if contract is not None else PHASE2_SECTION_NAMES
    )
    section_names: set[str] = set(base_names) | extra_section_names
    sections: dict[str, list[str]] = {name: [] for name in section_names}
    current: str = ADDITIONAL_FINDINGS_SECTION
    heading_re = re.compile(r"^### ([A-Z][A-Z0-9_ ]+)\s*$")

    for line in text.splitlines():
        match = heading_re.match(line.strip())
        if match:
            candidate = re.sub(
                r"[^A-Z0-9_]+", "_", match.group(1).strip().upper()
            ).strip("_")
            if candidate == "ADDITIONAL_FINDINGS":
                candidate = ADDITIONAL_FINDINGS_SECTION
            if candidate in section_names:
                current = candidate
                continue
            current = ADDITIONAL_FINDINGS_SECTION
        sections.setdefault(current, []).append(line)

    return {
        name: "\n".join(lines).strip()
        for name, lines in sections.items()
        if "\n".join(lines).strip()
    }
