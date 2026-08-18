"""Render-time canonical-unit-ID to display-name resolution.

Composite scans deduplicate colliding unit names into canonical hash IDs
(``VolkerVessels_Workorder_Talend_exports_EnterpriseService-34fb668381b6``)
whose real job name lives in the facts manifest's ``display_name``. Those IDs
are correct for the evidence/validation layer (they are unique per zip even
for shared display names) but must never ship in published documentation --
readers and the RAG layer need the real job names.

``resolve_unit_display_names`` deterministically replaces every canonical ID
whose display name differs with that display name, disambiguated with the
unit's group when the display name is not unique within the partition. It is
applied in ``_final_doc_content`` AFTER validation (fabrication validators
and coverage checks run on canonical names) and before publication.

Python substitutes manifest facts verbatim; it writes no prose (repo rule).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, Protocol


class _UnitLike(Protocol):
    name: str
    display_name: str
    group: str


def resolve_unit_display_names(text: str, units: Iterable[_UnitLike]) -> str:
    """Replace canonical unit IDs with manifest display names in doc text."""
    unit_list = [u for u in units if getattr(u, "display_name", "") and u.display_name != u.name]
    if not unit_list:
        return text
    display_counts = Counter(u.display_name for u in unit_list)
    # A canonical ID may also collide with another unit's plain display name;
    # count those too so the replacement stays unambiguous.
    all_display_counts = Counter(
        getattr(u, "display_name", "") or u.name for u in units
    )
    replacements: list[tuple[str, str]] = []
    for unit in unit_list:
        ambiguous = (
            display_counts[unit.display_name] > 1
            or all_display_counts[unit.display_name] > 1
        )
        replacement = (
            f"{unit.display_name} (group {unit.group})" if ambiguous else unit.display_name
        )
        replacements.append((unit.name, replacement))
    # Longest IDs first so an ID that is a prefix of another can never
    # clobber it; boundary-aware so `X_GetLocation` never matches inside
    # `X_GetLocations`.
    replacements.sort(key=lambda pair: len(pair[0]), reverse=True)
    for canonical, replacement in replacements:
        pattern = re.compile(
            r"(?<![A-Za-z0-9_-])" + re.escape(canonical) + r"(?![A-Za-z0-9_-])"
        )
        text = pattern.sub(lambda _m, _r=replacement: _r, text)
    return text
