"""Read complete staged collections, including byte-sharded ones.

The workspace writer replaces an oversized JSON list with a small index whose
``parts`` contain every original row.  Consumers must not each invent their
own interpretation of that index: one consumer treating it as a dictionary
and another requiring a list turns a scale feature into silent data loss.

This module is deliberately small and provider-neutral.  It validates the
index, confines every part to the directory that owns it, and reconciles the
declared row count before returning any rows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class StagedDataError(RuntimeError):
    """A staged JSON collection is missing, malformed, or incomplete."""


def read_json(path: Path) -> Any:
    """Read one JSON value or raise a client-safe structural error."""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StagedDataError(f"staged JSON is unreadable: {path.name}") from exc


def read_complete_list(path: Path) -> list[Any]:
    """Return every row from a list or from its byte-sharded index.

    A split index is data, not authority.  Absolute paths, traversal, symlinks,
    duplicate parts, non-list part payloads, and a wrong row count are refused.
    No partial collection is returned.
    """

    payload = read_json(path)
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict) or payload.get("split") is not True:
        raise StagedDataError(f"staged collection has the wrong shape: {path.name}")

    row_count = payload.get("row_count")
    parts = payload.get("parts")
    if not isinstance(row_count, int) or row_count < 0:
        raise StagedDataError(f"split collection has no valid row count: {path.name}")
    if not isinstance(parts, list) or (row_count and not parts):
        raise StagedDataError(f"split collection has no valid parts list: {path.name}")

    root = path.parent.resolve()
    rows: list[Any] = []
    seen: set[Path] = set()
    for raw_part in parts:
        if not isinstance(raw_part, str) or not raw_part.strip():
            raise StagedDataError(f"split collection names an invalid part: {path.name}")
        relative = Path(raw_part)
        if relative.is_absolute() or ".." in relative.parts:
            raise StagedDataError(f"split collection part escapes its directory: {path.name}")
        part = path.parent / relative
        resolved = part.resolve()
        if not resolved.is_relative_to(root) or part.is_symlink() or resolved in seen:
            raise StagedDataError(f"split collection names an unsafe part: {path.name}")
        seen.add(resolved)
        part_payload = read_json(part)
        if not isinstance(part_payload, list):
            raise StagedDataError(f"split collection part is not a list: {part.name}")
        rows.extend(part_payload)

    if len(rows) != row_count:
        raise StagedDataError(
            f"split collection {path.name} declares {row_count} row(s) but carries {len(rows)}"
        )
    return rows
