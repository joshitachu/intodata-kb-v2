"""Shared redaction for provider-supplied runtime error text.

Provider error strings reach two very different destinations -- the sync health
record and, since documentation evidence, an LLM prompt -- so the filter lives
here rather than in either consumer.  One implementation means one place to
strengthen when a new secret shape turns up in provider output.
"""

from __future__ import annotations

import re

from src.runtime.insights.parser import redact_credentials

MAX_ERROR_SUMMARY = 240
_TOKENISH_RE = re.compile(
    r"(?i)\b(bearer|token|secret|password|passwd|pwd|api[_-]?key)\b\s*[:=]?\s*\S*"
)


def sanitize_sync_error(value: str | None) -> str | None:
    """Bound and redact failure text before persistence or API exposure."""
    if not value:
        return None
    compact = " ".join(value.replace("\n", " ").split())
    redacted = redact_credentials(_TOKENISH_RE.sub("<redacted>", compact))
    if len(redacted) > MAX_ERROR_SUMMARY:
        return redacted[: MAX_ERROR_SUMMARY - 3] + "..."
    return redacted
