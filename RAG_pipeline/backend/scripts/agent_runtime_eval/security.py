"""Secret-shape detection for authored fixtures, traces, and public reports."""

from __future__ import annotations

import json
import re
from typing import Any


SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(
        r"(?i)\b(cookie|authorization|api[_-]?key|access_token|refresh_token)\b"
        r"\s*[:=]\s*(?:bearer\s+)?[^\s,;\"}]+"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{12,}=*"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
)


def contains_secret_shape(value: Any) -> bool:
    serialized = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return any(pattern.search(serialized) for pattern in SECRET_PATTERNS)


def require_sanitized(value: Any, *, label: str) -> None:
    if contains_secret_shape(value):
        raise ValueError(f"secret-shaped content detected in {label}")
