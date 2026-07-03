"""Data-driven failure-pattern catalog (Strategy/Registry pattern).

The catalog is the single source of truth for three pieces of failure knowledge
that used to be hardcoded as inline branches in parser.py / builder.py:

  1. Which error-code families are recognised (Oracle, SQLSTATE/Postgres, MySQL,
     HTTP, a generic ``[A-Z]{2,}-\\d+`` form) and how a code is lifted out of a
     raw message.
  2. The human root-cause template for a matched failure pattern.
  3. The ordered next-checks for that pattern.

Adding support for a new platform or failure family is a data change: append a
``CodeFamily`` and/or a ``FailurePattern`` to the defaults below (or to a custom
catalog list) and add a test. No control-flow edits to parser.py or builder.py
are required.

The catalog performs no I/O and holds no credentials. Callers redact values
before persistence or LLM input (see ``parser.redact_credentials``).
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Error-code families
# ---------------------------------------------------------------------------


class CodeFamily(BaseModel):
    """A recognised error-code family and how to extract its code from a message.

    ``requires_context`` marks families whose surface form is ambiguous on its own
    (bare numeric codes, 3-digit HTTP status). For those, ``pattern`` must include
    an anchoring token (e.g. ``SQLSTATE``, ``error code``) so a benign INFO line
    can never be misread as an error code. ``code_group`` is the regex group that
    holds the bare code; group 0 (the whole match) is used when None.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    name: str
    pattern: re.Pattern[str]
    code_group: int | None = None
    requires_context: bool = False
    context_pattern: re.Pattern[str] | None = None

    def extract(self, message: str) -> str | None:
        if self.context_pattern is not None and self.context_pattern.search(message) is None:
            return None
        m = self.pattern.search(message)
        if not m:
            return None
        group = self.code_group if self.code_group is not None else 0
        return m.group(group)


# ---------------------------------------------------------------------------
# Failure patterns
# ---------------------------------------------------------------------------


class FailurePattern(BaseModel):
    """One catalog entry mapping a matched signal set to cause text + next checks.

    A pattern matches when every declared constraint holds:
      * ``code_pattern`` (regex) matches the extracted ``error_code``;
      * ``exception_contains`` is a substring of ``exception_class``;
      * every entry in ``message_contains`` (lowercased) appears in the message.
    Constraints left as None / empty impose no requirement. Among all matching
    patterns the one with the highest ``specificity`` wins (ties resolve to the
    earlier-declared entry).
    """

    model_config = ConfigDict(frozen=True)

    pattern_id: str
    code_family: str | None = None
    code_pattern: str | None = None
    exception_contains: str | None = None
    message_contains: tuple[str, ...] = ()
    root_cause_template: str
    next_checks: tuple[str, ...] = ()
    specificity: int = 0

    def matches(
        self,
        *,
        error_code: str | None,
        exception_class: str | None,
        message: str,
    ) -> bool:
        if self.code_pattern is not None:
            if error_code is None or re.search(self.code_pattern, error_code) is None:
                return False
        if self.exception_contains is not None:
            if exception_class is None or self.exception_contains not in exception_class:
                return False
        if self.message_contains:
            lowered = message.lower()
            if not all(token in lowered for token in self.message_contains):
                return False
        return True


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class FailurePatternCatalog(BaseModel):
    """Registry of code families + failure patterns with a pure match API."""

    model_config = ConfigDict(frozen=True)

    families: tuple[CodeFamily, ...]
    patterns: tuple[FailurePattern, ...]
    fallback_id: str = "generic.unknown"

    def extract_error_code(self, message: str) -> tuple[str | None, str | None]:
        """Return ``(error_code, family_name)`` for the first family that matches.

        Families are tried in declared order; the first match wins. Returns
        ``(None, None)`` when no family matches so benign lines stay code-free.
        """
        for family in self.families:
            code = family.extract(message)
            if code is not None:
                return code, family.name
        return None, None

    def match(
        self,
        error_code: str | None,
        exception_class: str | None,
        message: str,
    ) -> FailurePattern | None:
        """Return the most specific matching pattern, or the fallback entry.

        Never returns None when a fallback pattern (no constraints) is registered,
        so callers do not need to special-case a missing match.
        """
        candidates = [
            p
            for p in self.patterns
            if p.matches(
                error_code=error_code,
                exception_class=exception_class,
                message=message,
            )
        ]
        if not candidates:
            return self._fallback()
        return max(candidates, key=lambda p: p.specificity)

    def _fallback(self) -> FailurePattern | None:
        for pattern in self.patterns:
            if pattern.pattern_id == self.fallback_id:
                return pattern
        return None

    @staticmethod
    def render_root_cause(
        pattern: FailurePattern,
        *,
        error_code: str | None,
        exception_class: str | None,
        host: str | None,
        retry_max: int | None,
    ) -> str:
        attempts = f" after {retry_max} attempts" if retry_max else ""
        return pattern.root_cause_template.format(
            error_code=error_code or "",
            exception_class=exception_class or "",
            host=host or "",
            attempts=attempts,
        )

    @staticmethod
    def render_next_checks(
        pattern: FailurePattern,
        *,
        host: str | None,
        error_code: str | None = None,
        exception_class: str | None = None,
    ) -> list[str]:
        checks: list[str] = []
        for raw in pattern.next_checks:
            if "{host}" in raw:
                if host:
                    checks.append(
                        raw.format(
                            host=host,
                            error_code=error_code or "",
                            exception_class=exception_class or "",
                        )
                    )
                continue
            checks.append(
                raw.format(
                    host=host or "",
                    error_code=error_code or "",
                    exception_class=exception_class or "",
                )
            )
        return checks

    @classmethod
    def default(cls) -> FailurePatternCatalog:
        return cls(families=_DEFAULT_FAMILIES, patterns=_DEFAULT_PATTERNS)


# ---------------------------------------------------------------------------
# Default data
# ---------------------------------------------------------------------------

_DEFAULT_FAMILIES: tuple[CodeFamily, ...] = (
    CodeFamily(name="oracle", pattern=re.compile(r"ORA-\d+")),
    CodeFamily(name="mysql", pattern=re.compile(r"ER_[A-Z][A-Z0-9_]+")),
    CodeFamily(
        name="mysql_numeric",
        pattern=re.compile(r"(?i)\b(?:error\s+code|errno)\s+(\d{4})\b"),
        code_group=1,
        requires_context=True,
    ),
    CodeFamily(
        name="postgres",
        pattern=re.compile(r"(?i)\bSQLSTATE[\s:=]*([0-9A-Z]{5})\b"),
        code_group=1,
        requires_context=True,
    ),
    CodeFamily(
        name="http",
        pattern=re.compile(r"(?i)\b(?:HTTP|status(?:\s+code)?)[\s:/]*\d?\.?\d?\s*(\d{3})\b"),
        code_group=1,
        requires_context=True,
    ),
    CodeFamily(
        name="generic",
        pattern=re.compile(r"([A-Z]{2,}-\d+)"),
        code_group=1,
        requires_context=True,
        context_pattern=re.compile(r"(?i)\b(error|err|code|failure|failed|exception|fault)\b"),
    ),
)

_DEFAULT_PATTERNS: tuple[FailurePattern, ...] = (
    FailurePattern(
        pattern_id="oracle.net.timeout",
        code_family="oracle",
        code_pattern=r"^ORA-12",
        root_cause_template=(
            "Source database connection timeout{attempts} ({error_code}): "
            "network path to database host may be unreachable or Oracle listener unavailable."
        ),
        next_checks=(
            "Check network connectivity to database host",
            "Verify Oracle listener status and port availability",
            "Review connection timeout configuration",
            "Confirm database host '{host}' is reachable from application host",
        ),
        specificity=30,
    ),
    FailurePattern(
        pattern_id="oracle.generic",
        code_family="oracle",
        code_pattern=r"^ORA-",
        root_cause_template="Execution terminated with database error {error_code}.",
        next_checks=("Investigate error code {error_code}",),
        specificity=10,
    ),
    FailurePattern(
        pattern_id="mysql.access_denied",
        code_family="mysql",
        code_pattern=r"^(ER_ACCESS_DENIED_ERROR|1045)$",
        root_cause_template=(
            "MySQL access denied ({error_code}): the supplied database credentials "
            "were rejected or the account lacks grants for the target host."
        ),
        next_checks=(
            "Verify the MySQL username and password for this connection",
            "Confirm the account has grants for the source host and schema",
            "Check that the account is not host-restricted",
        ),
        specificity=30,
    ),
    FailurePattern(
        pattern_id="mysql.generic",
        code_family="mysql",
        code_pattern=r"^(ER_|\d+$)",
        root_cause_template="Execution terminated with MySQL error {error_code}.",
        next_checks=("Investigate MySQL error code {error_code}",),
        specificity=10,
    ),
    FailurePattern(
        pattern_id="postgres.connection",
        code_family="postgres",
        code_pattern=r"^08",
        root_cause_template=(
            "PostgreSQL connection failure (SQLSTATE {error_code}): the database "
            "could not be reached or the connection was reset during the operation."
        ),
        next_checks=(
            "Check network connectivity to the PostgreSQL host",
            "Verify the PostgreSQL server is accepting connections on its port",
            "Review pg_hba.conf and connection limits",
            "Confirm database host '{host}' is reachable from application host",
        ),
        specificity=30,
    ),
    FailurePattern(
        pattern_id="postgres.generic",
        code_family="postgres",
        code_pattern=r"^[0-9A-Z]{5}$",
        root_cause_template="Execution terminated with PostgreSQL error SQLSTATE {error_code}.",
        next_checks=("Investigate PostgreSQL SQLSTATE {error_code}",),
        specificity=10,
    ),
    FailurePattern(
        pattern_id="http.server_error",
        code_family="http",
        code_pattern=r"^5\d\d$",
        root_cause_template=(
            "Downstream HTTP service returned a server error ({error_code}): the "
            "remote endpoint failed to process the request."
        ),
        next_checks=(
            "Check the downstream service health and error logs",
            "Verify the endpoint URL and request payload",
            "Retry after confirming the downstream service has recovered",
        ),
        specificity=30,
    ),
    FailurePattern(
        pattern_id="http.auth",
        code_family="http",
        code_pattern=r"^(401|403)$",
        root_cause_template=(
            "Downstream HTTP service rejected the request ({error_code}): authentication "
            "or authorization failed for the configured integration credentials."
        ),
        next_checks=(
            "Verify the API credential, token, or client certificate configured for this job",
            "Confirm the service account is authorized for the requested endpoint",
            "Check whether the credential was rotated, expired, revoked, or scoped incorrectly",
        ),
        specificity=35,
    ),
    FailurePattern(
        pattern_id="http.client_error",
        code_family="http",
        code_pattern=r"^4\d\d$",
        root_cause_template=(
            "Downstream HTTP service returned a client error ({error_code}): the "
            "request was rejected as malformed, unauthorized, or not found."
        ),
        next_checks=(
            "Verify the request URL, headers, and authentication",
            "Check that the requested resource exists",
            "Review the downstream API contract for this endpoint",
        ),
        specificity=30,
    ),
    FailurePattern(
        pattern_id="mule.connectivity",
        exception_contains="ConnectException",
        root_cause_template=(
            "MuleSoft connector could not reach its target ({exception_class}): "
            "the connection to the downstream system failed or timed out."
        ),
        next_checks=(
            "Check connectivity from the Mule runtime to the target system",
            "Verify the connector configuration (host, port, credentials)",
            "Review the target system availability and firewall rules",
        ),
        specificity=25,
    ),
    FailurePattern(
        pattern_id="network.timeout",
        exception_contains="TimeoutException",
        root_cause_template=(
            "Network or downstream service timeout ({exception_class}): the target system did "
            "not respond before the runtime timeout window."
        ),
        next_checks=(
            "Check target service availability and latency from the runtime host",
            "Review timeout and retry settings for this integration",
            "Look for recent network, firewall, proxy, or DNS changes",
        ),
        specificity=28,
    ),
    FailurePattern(
        pattern_id="source.offline",
        exception_contains="OfflineException",
        root_cause_template=(
            "Runtime source or engine is unavailable ({exception_class}): the source reported "
            "offline/unavailable while this execution was running."
        ),
        next_checks=(
            "Check whether the runtime engine/source is online and accepting work",
            "Verify the source heartbeat or agent registration status",
            "Restart or reconnect the engine/source only after confirming no active run is stuck",
        ),
        specificity=28,
    ),
    FailurePattern(
        pattern_id="jms.broker",
        exception_contains="JMSException",
        root_cause_template=(
            "Message broker failure ({exception_class}): the queue or broker connection failed "
            "during message processing."
        ),
        next_checks=(
            "Check broker availability and queue depth",
            "Verify queue/topic name and broker credentials",
            "Inspect dead-letter queue and retry policy for the failed message",
        ),
        specificity=28,
    ),
    FailurePattern(
        pattern_id="resource.memory",
        exception_contains="OutOfMemoryError",
        root_cause_template=(
            "Runtime resource pressure ({exception_class}): the process ran out of memory or "
            "exceeded its configured runtime limit."
        ),
        next_checks=(
            "Check runtime memory limits, heap settings, and recent input volume",
            "Review whether this job loads large datasets into memory",
            "Scale the worker or split the workload before retrying high-volume runs",
        ),
        specificity=30,
    ),
    FailurePattern(
        pattern_id="resource.disk",
        message_contains=("no space left",),
        root_cause_template=(
            "Runtime resource pressure: disk or temporary storage was exhausted during execution."
        ),
        next_checks=(
            "Check free disk space on the runtime host and temporary directories",
            "Clean old working files or increase runtime storage",
            "Review whether this job writes unusually large intermediate files",
        ),
        specificity=26,
    ),
    FailurePattern(
        pattern_id="generic.coded",
        code_family="generic",
        code_pattern=r"^[A-Z]{2,}-\d+$",
        root_cause_template="Execution terminated with error {error_code}.",
        next_checks=("Investigate error code {error_code}",),
        specificity=5,
    ),
    FailurePattern(
        pattern_id="exception.recoverable",
        exception_contains="Recoverable",
        root_cause_template="Execution terminated with exception {exception_class}.",
        next_checks=("Retry execution to determine if failure is transient",),
        specificity=4,
    ),
    FailurePattern(
        # exception_contains="" gates this entry to any non-None exception_class
        # without constraining its value, so it never shadows generic.unknown.
        pattern_id="exception.generic",
        exception_contains="",
        root_cause_template="Execution terminated with exception {exception_class}.",
        next_checks=("Review execution log for error details",),
        specificity=2,
    ),
    FailurePattern(
        pattern_id="generic.unknown",
        root_cause_template="Execution terminated with errors; review log events for details.",
        next_checks=("Review execution log for error details",),
        specificity=0,
    ),
)


CATALOG: FailurePatternCatalog = FailurePatternCatalog.default()
"""Process-wide singleton; loaded once at import."""
