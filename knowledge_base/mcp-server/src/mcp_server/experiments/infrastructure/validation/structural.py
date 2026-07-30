"""Bounded deterministic Markdown structure measurements."""

from __future__ import annotations

import re
from collections.abc import Iterable

from mcp_server.experiments.domain.evaluation import StructuralMetrics


_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_WORD = re.compile(r"[\w]+(?:['’-][\w]+)?", re.UNICODE)
_TABLE_SEPARATOR = re.compile(r"^\s*\|?(?:\s*:?-{1,}:?\s*\|)+\s*$")


def _normalise_heading(value: str) -> str:
    return " ".join(value.casefold().split())


def _visible_lines(markdown: str) -> list[str]:
    visible: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if not in_fence:
            visible.append(line)
    return visible


def measure_markdown(
    markdown: str | None, *, required_sections: Iterable[str] | None = None
) -> StructuralMetrics:
    """Measure one document without interpreting its prose or executing Markdown."""
    required = None if required_sections is None else tuple(required_sections)
    if required is not None and len({_normalise_heading(item) for item in required}) != len(required):
        raise ValueError("required_sections must be unique after normalisation")
    if markdown is None:
        return StructuralMetrics(
            document_count=0,
            heading_count=0,
            section_count=0,
            word_count=0,
            line_count=0,
            table_count=0,
            required_section_count=None if required is None else len(required),
            required_section_covered_count=None if required is None else 0,
        )

    lines = _visible_lines(markdown)
    headings = [match.group(2) for line in lines if (match := _HEADING.match(line))]
    table_count = sum(1 for line in lines if _TABLE_SEPARATOR.match(line))
    covered = None
    if required is not None:
        observed = {_normalise_heading(heading) for heading in headings}
        covered = sum(_normalise_heading(section) in observed for section in required)
    return StructuralMetrics(
        document_count=1,
        heading_count=len(headings),
        section_count=len(headings),
        word_count=len(_WORD.findall("\n".join(lines))),
        line_count=len(markdown.splitlines()),
        table_count=table_count,
        required_section_count=None if required is None else len(required),
        required_section_covered_count=covered,
    )
