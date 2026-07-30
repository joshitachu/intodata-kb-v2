"""Canonical JSON serialization used for stable experiment receipts."""

from __future__ import annotations

import json
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON deterministically and reject lossy/non-standard values."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_json_text(value: Any) -> str:
    """Return canonical UTF-8 JSON as text for intentionally human-readable receipts."""

    return canonical_json_bytes(value).decode("utf-8")
