"""Deterministic log message parser for runtime insight extraction.

Pure functions: no I/O, no side effects. Returns None/empty for fields that
cannot be reliably extracted — never invents causes or fills uncertain values.

Credential redaction is applied to any extracted values that might embed
connection strings (endpoint). The parser does not store or return raw log
messages; only the extracted structural signals are returned.
"""

from __future__ import annotations

import dataclasses
import re

# ---------------------------------------------------------------------------
# Regex library
# ---------------------------------------------------------------------------

_BRACKET_COMPONENT_RE = re.compile(r"^\[([^\]]+)\]")
_ORA_CODE_RE = re.compile(r"(ORA-\d+)")
_EXCEPTION_CLASS_RE = re.compile(r"([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*Exception)")
_RETRY_RE = re.compile(r"attempt\s+(\d+)/(\d+)", re.IGNORECASE)
_DURATION_MS_RE = re.compile(r"(\d+)ms\b")
_TIMEOUT_S_RE = re.compile(r"timeout[= ](\d+)s\b", re.IGNORECASE)
_TIMEOUT_MS_RE = re.compile(r"timeout[= ](\d+)ms\b", re.IGNORECASE)
_JDBC_URL_RE = re.compile(r"jdbc:[^:]+:[^@]*@//([^:/\s]+):\d+/(\w+)")
_JMS_QUEUE_RE = re.compile(r"jms:queue:([^\s(,\]]+)", re.IGNORECASE)
_SELECT_TABLE_RE = re.compile(r"\bSELECT(?:\s+\w+)?\s+on\s+(\w+)", re.IGNORECASE)
_CREDENTIAL_RE = re.compile(
    r"(?i)(password|passwd|pwd|token|secret|api[_\-]?key)\s*[=:]\s*\S+",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def redact_credentials(text: str) -> str:
    """Replace credential-pattern values with <redacted>."""
    return _CREDENTIAL_RE.sub(r"\1=<redacted>", text)


@dataclasses.dataclass
class ParsedRuntimeLog:
    """Structural signals parsed from one log message. All fields optional — None
    means the signal was not present or could not be extracted reliably."""

    component_name: str | None = None
    error_code: str | None = None
    exception_class: str | None = None
    retry_attempt: int | None = None
    retry_max: int | None = None
    duration_ms: int | None = None
    timeout_ms: int | None = None
    endpoint: str | None = None
    host: str | None = None
    database_name: str | None = None
    table_name: str | None = None
    queue_name: str | None = None
    message_family: str | None = None
    confidence: str | None = None


def parse_runtime_log_message(message: str) -> ParsedRuntimeLog:
    """Extract structural signals from a runtime log message string.

    Conservative extraction: returns None for a field when the signal is absent
    or ambiguous. Does not invent causes. Credential-like values in extracted
    endpoint strings are redacted before being stored in the result.
    """
    result = ParsedRuntimeLog()

    # Component name: bracket prefix at start of message
    comp_m = _BRACKET_COMPONENT_RE.match(message)
    if comp_m:
        result.component_name = comp_m.group(1)

    # Oracle error code
    ora_m = _ORA_CODE_RE.search(message)
    if ora_m:
        result.error_code = ora_m.group(1)

    # Exception class — prefer most specific (shortest non-qualified name last)
    exc_matches = _EXCEPTION_CLASS_RE.findall(message)
    if exc_matches:
        # Prefer fully-qualified names (contain ".") for clarity; fall back to bare name.
        qualified = [m for m in exc_matches if "." in m]
        result.exception_class = qualified[0].split(".")[-1] if qualified else exc_matches[0]

    # Retry attempt / max
    retry_m = _RETRY_RE.search(message)
    if retry_m:
        result.retry_attempt = int(retry_m.group(1))
        result.retry_max = int(retry_m.group(2))

    # Duration in milliseconds (first occurrence)
    dur_m = _DURATION_MS_RE.search(message)
    if dur_m:
        result.duration_ms = int(dur_m.group(1))

    # Timeout value — prefer explicit ms, fall back to seconds converted to ms
    tmo_ms = _TIMEOUT_MS_RE.search(message)
    tmo_s = _TIMEOUT_S_RE.search(message)
    if tmo_ms:
        result.timeout_ms = int(tmo_ms.group(1))
    elif tmo_s:
        result.timeout_ms = int(tmo_s.group(1)) * 1000

    # JDBC URL: extract host and database name; redact full URL before storing
    jdbc_m = _JDBC_URL_RE.search(message)
    if jdbc_m:
        result.host = jdbc_m.group(1)
        result.database_name = jdbc_m.group(2)
        raw_endpoint = message[jdbc_m.start():jdbc_m.end()]
        result.endpoint = redact_credentials(raw_endpoint)

    # JMS queue name
    jms_m = _JMS_QUEUE_RE.search(message)
    if jms_m:
        result.queue_name = jms_m.group(1)

    # SELECT target table
    sel_m = _SELECT_TABLE_RE.search(message)
    if sel_m:
        result.table_name = sel_m.group(1)

    # Message family inference
    if result.endpoint or result.host or result.database_name:
        result.message_family = "jdbc"
    elif result.queue_name:
        result.message_family = "jms"
    elif result.exception_class:
        result.message_family = "java_exception"

    # Confidence: how many strong signals were found
    strong = sum(
        1 for v in (result.error_code, result.exception_class) if v is not None
    )
    has_structural = any(
        v is not None
        for v in (
            result.retry_attempt,
            result.timeout_ms,
            result.host,
            result.queue_name,
        )
    )
    if strong >= 2:
        result.confidence = "high"
    elif strong == 1 or (result.component_name and has_structural):
        result.confidence = "medium"
    elif result.component_name or result.duration_ms is not None:
        result.confidence = "low"

    return result
