"""Parse per-component plain-English descriptions from generated docs.

The topology Level-2 component graph shows raw component IDs (``tSetGlobalVar_1``)
that mean nothing to a human. The already-generated, already-credential-filtered
``02_COMPONENT_INVENTORY.md`` doc holds a plain-English description for every
component in a per-job table. This module surfaces that text so the graph can
show it as a node tooltip and inspector line -- no new LLM calls, read-only.

Anti-fabrication posture (matches the rest of the platform): a missing,
stale, or unparseable doc yields ``{}``. Callers default every component to
an empty description; text is only ever the verbatim (whitespace-collapsed,
markdown-noise-stripped) doc cell, never synthesized.

Defensiveness: column headers and column count vary between generated docs, so
the table's ``Component ID`` and description columns are located by
case-insensitive header substring match (never by hardcoded index). Cell
splitting respects escaped pipes (``\\|``) that appear inside description prose.
"""

from __future__ import annotations

import logging
import re

from mcp_server.config import config

logger = logging.getLogger(__name__)

# Descriptions are display strings, not evidence -- keep them bounded.
_MAX_DESCRIPTION_LEN = 300

# Header cells that identify the component-id column, most specific first.
# The first hint that matches any header cell wins (priority order), so
# "component id" is preferred over a bare "id" and never collides with "Type".
_ID_HEADER_HINTS: tuple[str, ...] = (
    "component id",
    "unique name",
    "component",
    "unit name",
    "id",
    "name",
)

# Header cells that identify the human-readable description column. "Role" is
# the concise description in current Talend inventory docs; the alternatives
# cover format drift across generated docs. "Key Properties / Notes" is
# intentionally excluded -- it is extra detail, not the concise description.
_DESC_HEADER_HINTS: tuple[str, ...] = (
    "role",
    "description",
    "purpose",
    "summary",
    "function",
)

# A markdown table separator cell is only dashes/colons, e.g. ``:---:``.
_SEPARATOR_CELL_RE = re.compile(r"^:?-{2,}:?$")
# Split a table row on unescaped pipes only (``\|`` stays inside a cell).
_UNESCAPED_PIPE_RE = re.compile(r"(?<!\\)\|")
_HTML_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def _inventory_path(project_id: str):
    """Resolve ``02_COMPONENT_INVENTORY.md``, mirroring the ``docs`` ->
    ``docs_staging`` fallback used elsewhere in the projects route."""
    docs_dir = config.project_root / "output" / project_id / "docs"
    if not docs_dir.exists():
        docs_dir = config.project_root / "output" / project_id / "docs_staging"
    return docs_dir / "02_COMPONENT_INVENTORY.md"


def _extract_job_section(content: str, job_name: str) -> str | None:
    """Return the body under this job's heading, bounded by the next
    sibling-or-higher heading (same technique as
    ``projects._extract_job_doc_sections``).

    Matches both the ``### Job: {name} (N components)`` inventory heading and a
    bare ``#### {name}`` heading. The word boundary after the name keeps
    ``sjItem_OnRamp`` from matching ``sjItem_OnRamp2``.
    """
    name_re = re.escape(job_name)
    heading_re = re.compile(
        rf"^(#{{2,4}})\s*(?:Job:\s*)?{name_re}\b.*$",
        re.MULTILINE,
    )
    any_heading_re = re.compile(r"^#{1,6}\s", re.MULTILINE)

    match = heading_re.search(content)
    if not match:
        return None
    depth = len(match.group(1))
    tail = content[match.end():]

    end = len(tail)
    for h in any_heading_re.finditer(tail):
        hashes = 0
        pos = h.start()
        while pos < len(tail) and tail[pos] == "#":
            hashes += 1
            pos += 1
        if hashes <= depth:
            end = h.start()
            break
    return tail[:end]


def _split_row(line: str) -> list[str]:
    """Split a markdown table row into trimmed cells, honoring escaped pipes."""
    cells = _UNESCAPED_PIPE_RE.split(line)
    # Drop the empty leading/trailing cells produced by the outer pipes.
    if cells and cells[0].strip() == "":
        cells = cells[1:]
    if cells and cells[-1].strip() == "":
        cells = cells[:-1]
    return [c.strip() for c in cells]


def _find_column(headers: list[str], hints: tuple[str, ...]) -> int | None:
    """Index of the first header cell matching a hint, in hint priority order."""
    lowered = [h.lower() for h in headers]
    for hint in hints:
        for idx, cell in enumerate(lowered):
            if hint in cell:
                return idx
    return None


def _clean_id(text: str) -> str:
    """Normalize a component-id cell (strip backticks/bold, collapse space)."""
    text = text.replace("`", "").replace("**", "")
    return _WHITESPACE_RE.sub(" ", text).strip()


def _clean_description(text: str) -> str:
    """Strip markdown noise, collapse whitespace, and bound the length."""
    text = text.replace("\\|", "|")  # un-escape pipes kept inside the cell
    text = text.replace("`", "")
    text = _HTML_BREAK_RE.sub(" ", text)
    text = text.replace("**", "").replace("__", "")
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if len(text) > _MAX_DESCRIPTION_LEN:
        text = text[: _MAX_DESCRIPTION_LEN - 3].rstrip() + "..."
    return text


def _parse_table(section: str) -> dict[str, str]:
    """Parse the first component table in a job section into
    ``{component_id: description}``. Returns ``{}`` when no table with both an
    id column and a description column is present."""
    lines = section.splitlines()
    result: dict[str, str] = {}

    id_col: int | None = None
    desc_col: int | None = None
    in_table = False

    for line in lines:
        stripped = line.strip()
        is_row = stripped.startswith("|")

        if not is_row:
            # A blank/non-table line ends the table once we have started one.
            if in_table:
                break
            continue

        cells = _split_row(stripped)
        if not cells:
            continue

        if id_col is None:
            # Looking for the header row: needs both an id and a desc column.
            candidate_id = _find_column(cells, _ID_HEADER_HINTS)
            candidate_desc = _find_column(cells, _DESC_HEADER_HINTS)
            if (
                candidate_id is not None
                and candidate_desc is not None
                and candidate_id != candidate_desc
            ):
                id_col, desc_col = candidate_id, candidate_desc
                in_table = True
            continue

        # Data row. Skip the separator row (all dashes/colons).
        if all(_SEPARATOR_CELL_RE.match(c) for c in cells if c):
            continue
        if id_col >= len(cells) or desc_col is None or desc_col >= len(cells):
            continue

        component_id = _clean_id(cells[id_col])
        if not component_id or _SEPARATOR_CELL_RE.match(component_id):
            continue
        description = _clean_description(cells[desc_col])
        if description:
            # First occurrence wins; do not overwrite with later stray rows.
            result.setdefault(component_id, description)

    return result


def parse_component_descriptions(project_id: str, job_name: str) -> dict[str, str]:
    """Return ``{component_id: description}`` for one job, parsed from the
    project's ``02_COMPONENT_INVENTORY.md``.

    Never raises. Returns ``{}`` when the doc, the job section, or a usable
    component table is absent or unparseable -- the anti-fabrication default.
    """
    inventory_path = _inventory_path(project_id)
    if not inventory_path.exists():
        return {}
    try:
        content = inventory_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Unreadable component inventory %s: %s", inventory_path, exc)
        return {}

    section = _extract_job_section(content, job_name)
    if not section:
        return {}
    return _parse_table(section)
