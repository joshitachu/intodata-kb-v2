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

from src.runtime.insights.catalog import CATALOG

# ---------------------------------------------------------------------------
# Regex library
# ---------------------------------------------------------------------------

_BRACKET_COMPONENT_RE = re.compile(r"^\[([^\]]+)\]")
# Java/.NET style class name ending in Exception or Error. Requires at least one
# capitalised qualifier before the suffix (e.g. SQLRecoverableException, IOError)
# so prose such as "IO Error" or a bare "Error" token is never mistaken for a
# class. A "." anywhere (package-qualified) also satisfies the qualifier.
_EXCEPTION_CLASS_RE = re.compile(
    r"((?:[A-Za-z][A-Za-z0-9_]*\.)*[A-Z][A-Za-z0-9_]*(?:Exception|Error))"
)
# A "Caused by:" marker introduces a deeper (root) exception in a wrapped trace.
# The deepest marker is the true fault; everything above it is a wrapper.
_CAUSED_BY_RE = re.compile(
    r"(?i)caused by:\s*((?:[A-Za-z][A-Za-z0-9_]*\.)*[A-Z][A-Za-z0-9_]*(?:Exception|Error))"
)
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
# Inline URL/JDBC userinfo: scheme://user:pass@host or jdbc:...user/pass@host.
# Requires a trailing "@host" so plain "host:port" (no userinfo) is never touched.
_USERINFO_RE = re.compile(r"[^\s:/@]+[:/][^\s:/@]+@(?=/*[^\s/@])")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def redact_credentials(text: str) -> str:
    """Replace credential patterns with <redacted>.

    Covers both keyword forms (password=..., token:...) and inline URL/JDBC
    userinfo (user:pass@host, user/pass@host). Userinfo is stripped first so a
    secret embedded in a connection string never survives into a persisted field.
    """
    redacted = _USERINFO_RE.sub("<redacted>@", text)
    return _CREDENTIAL_RE.sub(r"\1=<redacted>", redacted)


def _simple_class_name(qualified: str) -> str:
    return qualified.split(".")[-1]


def _resolve_causal_exception(message: str) -> tuple[str | None, str | None]:
    """Resolve the root exception of a (possibly multi-line) log message.

    Returns ``(simple_exception_class, causal_message)`` where ``causal_message``
    is the line that carries the true fault — used as a catalog message hint.

    Resolution order:
      1. Deepest ``Caused by:`` marker — the root of a wrapped stack trace. The
         outermost exception is the wrapper, not the fault, so the LAST marker
         wins (the previous ``qualified[0]`` logic incorrectly returned the
         wrapper).
      2. No ``Caused by:`` — pick the most specific single exception: prefer the
         exception sharing a line with an extractable error code (the
         failure-attributed line); else the last exception in the message
         (deepest position). Tie-break: first qualified simple name, else first
         bare match. Preserves the existing single-exception behaviour.
    """
    caused = list(_CAUSED_BY_RE.finditer(message))
    if caused:
        deepest = caused[-1]
        line = _line_containing(message, deepest.start())
        return _simple_class_name(deepest.group(1)), line

    exc_matches = list(_EXCEPTION_CLASS_RE.finditer(message))
    if not exc_matches:
        return None, None

    # Prefer the exception on a line that also carries an error code.
    coded = [m for m in exc_matches if _has_error_code(_line_containing(message, m.start()))]
    if coded:
        chosen = coded[-1]
        return _simple_class_name(chosen.group(0)), _line_containing(message, chosen.start())

    # Otherwise prefer the deepest (last) exception, but keep the legacy
    # tie-break: a qualified simple name is clearer than a bare name.
    qualified = [m for m in exc_matches if "." in m.group(0)]
    chosen = qualified[0] if qualified else exc_matches[0]
    return _simple_class_name(chosen.group(0)), _line_containing(message, chosen.start())


def _line_containing(message: str, index: int) -> str:
    start = message.rfind("\n", 0, index) + 1
    end = message.find("\n", index)
    if end == -1:
        end = len(message)
    return message[start:end].strip()


def _has_error_code(line: str) -> bool:
    code, _ = CATALOG.extract_error_code(line)
    return code is not None


@dataclasses.dataclass
class ParsedRuntimeLog:
    """Structural signals parsed from one log message. All fields optional — None
    means the signal was not present or could not be extracted reliably."""

    component_name: str | None = None
    error_code: str | None = None
    error_family: str | None = None
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
    # The deepest causal line (root of a "Caused by:" chain, or the
    # failure-attributed line). Transient hint for catalog matching — not persisted.
    causal_message: str | None = None


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

    # Error code: delegate family matching to the catalog (Oracle, MySQL,
    # Postgres/SQLSTATE, HTTP, generic). Conservative — benign lines yield None.
    code, family = CATALOG.extract_error_code(message)
    if code is not None:
        result.error_code = code
        result.error_family = family

    # Exception class — resolve the deepest causal exception (root of a
    # "Caused by:" chain or the failure-attributed line), not the outermost.
    exc_class, causal_message = _resolve_causal_exception(message)
    if exc_class is not None:
        result.exception_class = exc_class
        result.causal_message = causal_message

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
