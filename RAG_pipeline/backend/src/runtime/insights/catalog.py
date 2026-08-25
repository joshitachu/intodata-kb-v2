"""Data-driven failure-pattern catalog (Strategy/Registry pattern).

The catalog is the single source of truth for three pieces of failure knowledge
that used to be hardcoded as inline branches in parser.py / builder.py:

  1. Which error-code families are recognised (Oracle, SQLSTATE/Postgres, MySQL,
     SQL Server, Salesforce, HTTP 4xx/5xx, a generic ``[A-Z]{2,}-\\d+`` form) and
     how a code is lifted out of a raw message.
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

from pydantic import BaseModel, ConfigDict, model_validator

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
      * ``code_family`` equals the family that produced the code (see below);
      * ``code_pattern`` (regex) matches the extracted ``error_code``;
      * ``exception_contains`` is a substring of ``exception_class``;
      * every entry in ``message_contains`` (lowercased) appears in the message.
    Constraints left as None / empty impose no requirement. Among all matching
    patterns the one with the highest ``specificity`` wins (ties resolve to the
    earlier-declared entry).

    ``code_family`` is enforced only when the caller supplies the extracted
    family to :meth:`matches`. It was previously declarative metadata that
    nothing checked, which let a platform-specific pattern claim a bare numeric
    code lifted from an unrelated platform — e.g. ``mysql.generic``
    (``code_pattern=^(ER_|\\d+$)``) rendering "MySQL error 200" for a Google
    BigQuery fault. A pattern that names a family must not claim another
    family's code.
    """

    model_config = ConfigDict(frozen=True)

    pattern_id: str
    code_family: str | None = None
    # Additional family names that denote the SAME platform as ``code_family``.
    # One platform may need several extraction families because its codes have
    # several surface forms (MySQL: named ``ER_*`` and bare numeric), and a
    # pattern written for the platform must accept all of them.
    code_family_aliases: tuple[str, ...] = ()
    code_pattern: str | None = None
    exception_contains: str | None = None
    message_contains: tuple[str, ...] = ()
    # Tokens that must appear in the combined evidence (exception class OR
    # message). Needed for platform signals that survive in only one of the two
    # depending on the caller: the builder reduces an exception to its SIMPLE
    # class name, so a package qualifier like "org.mule.runtime" is visible only
    # in the message text, while a direct catalog caller may pass the qualified
    # name and no message. Checking the union matches in both cases.
    evidence_contains: tuple[str, ...] = ()
    root_cause_template: str
    next_checks: tuple[str, ...] = ()
    specificity: int = 0

    @model_validator(mode="after")
    def _family_pattern_requires_code_pattern(self) -> "FailurePattern":
        """A family-scoped pattern must also constrain the code shape.

        ``code_family`` is only enforced when the caller supplies the extracted
        family. A pattern whose ONLY constraint is ``code_family`` would
        therefore match everything for any caller that omits it — becoming a
        silent catch-all that outranks the real fallback. Rejecting the
        combination at construction makes that class of mistake impossible to
        introduce, here or in a custom catalog.
        """
        if self.code_family is not None and self.code_pattern is None:
            raise ValueError(
                f"FailurePattern {self.pattern_id!r} declares code_family "
                f"{self.code_family!r} but no code_pattern; a family-scoped "
                "pattern must also constrain the code shape"
            )
        return self

    def accepted_families(self) -> frozenset[str]:
        if self.code_family is None:
            return frozenset()
        return frozenset((self.code_family, *self.code_family_aliases))

    def matches(
        self,
        *,
        error_code: str | None,
        exception_class: str | None,
        message: str,
        error_family: str | None = None,
    ) -> bool:
        accepted = self.accepted_families()
        if accepted:
            # A platform-code pattern is meaningless without a code, and when the
            # caller supplied the family it must agree.
            if error_code is None:
                return False
            if error_family is not None and error_family not in accepted:
                return False
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
        if self.evidence_contains:
            evidence = f"{exception_class or ''}\n{message}".lower()
            if not all(token in evidence for token in self.evidence_contains):
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
        error_family: str | None = None,
    ) -> FailurePattern | None:
        """Return the most specific matching pattern, or the fallback entry.

        Never returns None when a fallback pattern (no constraints) is registered,
        so callers do not need to special-case a missing match.

        Pass ``error_family`` (from :meth:`extract_error_code`) to enforce
        family agreement — without it, a pattern declaring ``code_family`` can
        claim a same-shaped code from a different platform.
        """
        candidates = [
            p
            for p in self.patterns
            if p.matches(
                error_code=error_code,
                exception_class=exception_class,
                message=message,
                error_family=error_family,
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
    # Salesforce states its fault code in an unambiguous quoted form, so it needs
    # no context anchor and is safe to try early.
    CodeFamily(
        name="salesforce",
        pattern=re.compile(r"exceptionCode='([A-Z][A-Z0-9_]+)'"),
        code_group=1,
    ),
    # Word-anchored: without \b this matched mid-token, so a Karaf config key
    # ending "..._SERVER_HOST" yielded the bogus MySQL code "ER_HOST".
    CodeFamily(name="mysql", pattern=re.compile(r"\bER_[A-Z][A-Z0-9_]+")),
    CodeFamily(
        name="mysql_numeric",
        pattern=re.compile(r"(?i)\bmysql\b[^\n]*?\b(?:error\s+code|errno)\s+(\d{4})\b"),
        code_group=1,
        requires_context=True,
    ),
    CodeFamily(
        name="postgres",
        pattern=re.compile(r"(?i)\bSQLSTATE[\s:=]*([0-9A-Z]{5})\b"),
        code_group=1,
        requires_context=True,
    ),
    # SQL Server / jTDS reports "Msg 8152" or "SQL Server error 2627". Anchored on
    # an explicit Msg/error token so an arbitrary 4-digit number in prose is never
    # promoted to an error code.
    CodeFamily(
        name="sqlserver",
        pattern=re.compile(r"(?i)\b(?:msg|error\s+number|error\s+code)\s+(\d{3,5})\b"),
        code_group=1,
        requires_context=True,
        context_pattern=re.compile(
            r"(?i)(sql\s*server|jtds|sqlsrv|mssql|batchupdateexception)"
        ),
    ),
    CodeFamily(
        # Only 4xx/5xx are faults. The previous \d{3} form lifted a 2xx success
        # status out of a response line and presented it as an error code, which
        # then cascaded into a wrong-platform root cause.
        name="http",
        pattern=re.compile(r"(?i)\b(?:HTTP|status(?:\s+code)?)[\s:/]*\d?\.?\d?\s*([45]\d{2})\b"),
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
        code_family_aliases=("mysql_numeric",),
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
        code_family_aliases=("mysql_numeric",),
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
    # --- SQL Server / jTDS (Talend's tMSSql* components) --------------------
    FailurePattern(
        pattern_id="sqlserver.truncation",
        message_contains=("data truncation",),
        root_cause_template=(
            "Target column too narrow for the incoming value: SQL Server rejected the "
            "batch with a data-truncation error ({exception_class}). An upstream field "
            "now exceeds the declared width of the destination column."
        ),
        next_checks=(
            "Identify which column overflowed — enable per-row error output on the failing component",
            "Compare the source field length against the target column definition",
            "Check whether an upstream system widened the field or started sending longer values",
            "Widen the target column or truncate/validate explicitly before the write",
        ),
        specificity=34,
    ),
    FailurePattern(
        pattern_id="sqlserver.truncation_named",
        message_contains=("would be truncated in table",),
        root_cause_template=(
            "Target column too narrow for the incoming value: SQL Server reported the "
            "truncation and named the offending table/column."
        ),
        next_checks=(
            "Compare the named column's declared width against the incoming value length",
            "Widen the target column or validate/truncate before the write",
            "Check whether the upstream field definition changed",
        ),
        specificity=36,
    ),
    FailurePattern(
        pattern_id="sqlserver.pk_violation",
        message_contains=("violation of primary key",),
        root_cause_template=(
            "Duplicate key rejected by SQL Server: the write attempted a row whose key "
            "already exists in the target table."
        ),
        next_checks=(
            "Check whether the source delivered duplicate keys in this batch",
            "Confirm whether the load should upsert rather than insert",
            "Look for a re-run that replayed an already-loaded batch",
        ),
        specificity=32,
    ),
    FailurePattern(
        pattern_id="sqlserver.deadlock",
        message_contains=("deadlock",),
        root_cause_template=(
            "SQL Server chose this transaction as a deadlock victim: it contended with "
            "another session for the same resources and was rolled back."
        ),
        next_checks=(
            "Identify the competing workload running at the same time",
            "Review batch size and transaction scope on the writing component",
            "Consider retry-on-deadlock or reordering the write to reduce contention",
        ),
        specificity=32,
    ),
    FailurePattern(
        pattern_id="sqlserver.connection_reset",
        message_contains=("connection reset by peer",),
        root_cause_template=(
            "Database connection dropped mid-write ({exception_class}): the server or an "
            "intermediary closed the socket while the component was sending data."
        ),
        next_checks=(
            "Check the database server for restarts, failovers, or resource pressure at this time",
            "Review idle-timeout and max-packet settings on the connection and any firewall in the path",
            "Confirm whether large batch writes are exceeding a network or server limit",
        ),
        specificity=30,
    ),
    FailurePattern(
        # Provider-neutral form observed when JDBC/Oracle-style components emit
        # the transport diagnosis without a vendor error code or exception class.
        pattern_id="database.connection_reset",
        message_contains=("io error", "connection reset"),
        root_cause_template=(
            "The database connection was reset during the operation; the runtime "
            "lost its established network session before the component completed."
        ),
        next_checks=(
            "Check database availability and listener health at the failure times",
            "Review network, firewall, proxy, and idle-connection resets between the runtime and database",
            "Compare the failure times with database restarts, failovers, or maintenance",
        ),
        specificity=29,
    ),
    # --- Google BigQuery (tBigQueryOutput / tBQBE) --------------------------
    FailurePattern(
        pattern_id="bigquery.source_not_found",
        exception_contains="BigQueryException",
        message_contains=("not found",),
        root_cause_template=(
            "BigQuery load failed because the referenced source object does not exist: "
            "the staged file, URI, or table named in the job could not be resolved."
        ),
        next_checks=(
            "Confirm the staged file was written to the expected bucket path before this job ran",
            "Check whether the upstream export step succeeded or produced a different filename",
            "Verify the service account can list and read the referenced object",
        ),
        specificity=34,
    ),
    FailurePattern(
        pattern_id="bigquery.invalid_dataset",
        exception_contains="BigQueryException",
        message_contains=("invalid dataset id",),
        root_cause_template=(
            "BigQuery rejected the dataset identifier as malformed: dataset IDs allow only "
            "alphanumerics, underscores and dashes, so a stray space or punctuation in the "
            "configured name fails before any data is written."
        ),
        next_checks=(
            "Correct the dataset name in the job or context variable — check for a stray space",
            "Verify the context/environment supplying the dataset name for this run",
            "Confirm the dataset exists in the target Google Cloud project",
        ),
        specificity=34,
    ),
    FailurePattern(
        pattern_id="bigquery.generic",
        exception_contains="BigQueryException",
        root_cause_template="BigQuery rejected the operation ({exception_class}).",
        next_checks=("Review the BigQuery error text and the job configuration",),
        specificity=12,
    ),
    FailurePattern(
        pattern_id="google.api_error",
        exception_contains="GoogleJsonResponseException",
        root_cause_template=(
            "Google API call was rejected ({exception_class}): the request reached the "
            "service but was refused."
        ),
        next_checks=(
            "Review the Google API error body for the specific reason",
            "Verify the service-account credential and its IAM roles for this API",
            "Check the request quota and any org policy constraint on the target project",
        ),
        specificity=26,
    ),
    # --- Salesforce (tSalesforceInput / tSalesforceOutput) ------------------
    FailurePattern(
        pattern_id="salesforce.invalid_field",
        code_family="salesforce",
        code_pattern=r"^INVALID_FIELD$",
        root_cause_template=(
            "Salesforce rejected the query: it selects a field that does not exist on the "
            "target object, so the SOQL failed to compile. A field was renamed, removed, or "
            "is not visible to this integration user."
        ),
        next_checks=(
            "Compare the selected field list against the object's current schema in Salesforce",
            "Check whether the field was renamed or deleted, or is a custom field missing its __c suffix",
            "Confirm the integration user's field-level security grants read on every selected field",
        ),
        specificity=34,
    ),
    FailurePattern(
        pattern_id="salesforce.auth",
        code_family="salesforce",
        code_pattern=r"^(INVALID_LOGIN|INVALID_SESSION_ID|INVALID_GRANT)$",
        root_cause_template=(
            "Salesforce rejected the integration credential ({error_code}): the session or "
            "login was refused."
        ),
        next_checks=(
            "Verify the integration user's credential, security token, and OAuth grant",
            "Check whether the password or token was rotated, or the user was deactivated or locked",
            "Confirm the login IP range and any connected-app restriction",
        ),
        specificity=34,
    ),
    FailurePattern(
        pattern_id="salesforce.generic",
        code_family="salesforce",
        code_pattern=r"^[A-Z][A-Z0-9_]+$",
        root_cause_template="Salesforce API rejected the request ({error_code}).",
        next_checks=("Review the Salesforce fault code and the request payload",),
        specificity=12,
    ),
    # --- ActiveMQ / JMS transport (Talend ESB message broker) ---------------
    FailurePattern(
        pattern_id="activemq.broker_unreachable",
        message_contains=("failed to connect to", "tcp://"),
        root_cause_template=(
            "Message broker unreachable{attempts}: the runtime could not open a connection "
            "to any configured ActiveMQ transport URL, so nothing could be published or "
            "consumed."
        ),
        next_checks=(
            "Check whether the broker process is running and listening on its transport port",
            "Confirm every host in the failover URL list is resolvable and reachable from the runtime host",
            "Review broker connection limits and any firewall change in this window",
            "Check for a broker failover or restart at this time",
        ),
        specificity=33,
    ),
    FailurePattern(
        pattern_id="activemq.transport_interrupted",
        message_contains=("failed, attempting to automatically reconnect",),
        root_cause_template=(
            "Broker transport dropped mid-session: the connection to the message broker "
            "failed and the client began automatic reconnection."
        ),
        next_checks=(
            "Check the broker log for a restart, failover, or resource limit at this time",
            "Review network stability between the runtime host and the broker",
            "Confirm whether messages were redelivered or lost during the gap",
        ),
        specificity=31,
    ),
    # --- CXF / SOAP web services --------------------------------------------
    FailurePattern(
        pattern_id="soap.operation_not_found",
        message_contains=("no method was found for the wsdl operation",),
        root_cause_template=(
            "SOAP contract mismatch: the request named a WSDL operation the service does not "
            "expose, so the endpoint rejected it before any business logic ran. The service "
            "contract and the caller have diverged."
        ),
        next_checks=(
            "Compare the operation name in the request against the service's current WSDL",
            "Check whether the provider republished its WSDL with renamed or removed operations",
            "Confirm the caller targets the intended service version and namespace",
        ),
        specificity=34,
    ),
    FailurePattern(
        pattern_id="cxf.interceptor_fault",
        message_contains=("interceptor for", "has thrown exception"),
        root_cause_template=(
            "Web-service call failed inside the CXF interceptor chain ({exception_class}): the "
            "request or response could not be processed on the way to the endpoint."
        ),
        next_checks=(
            "Check the target endpoint's availability and TLS configuration",
            "Review the interceptor chain for the failing phase in the log detail",
            "Verify the payload matches the contract the endpoint expects",
        ),
        specificity=28,
    ),
    # --- Concrete Java/Talend failures observed in the alitalend TMC source -
    FailurePattern(
        pattern_id="filesystem.access_denied",
        exception_contains="FileNotFoundException",
        message_contains=("access is denied",),
        root_cause_template=(
            "The runtime could not open the required file because the operating system "
            "denied access ({exception_class})."
        ),
        next_checks=(
            "Verify that the runtime service account can read and write the reported path",
            "Check whether the directory exists and is not locked by another process",
            "Review recent service-account or filesystem permission changes",
        ),
        specificity=32,
    ),
    FailurePattern(
        pattern_id="network.unknown_host",
        exception_contains="UnknownHostException",
        root_cause_template=(
            "DNS resolution failed for a downstream host ({exception_class}); the runtime "
            "could not translate the configured host name to an address."
        ),
        next_checks=(
            "Verify the configured host name and DNS record",
            "Resolve the host from the runtime engine network",
            "Check DNS, proxy, and environment-specific endpoint configuration changes",
        ),
        specificity=30,
    ),
    FailurePattern(
        pattern_id="sharepoint.server_exception",
        exception_contains="SPException",
        evidence_contains=("microsoft.sharepoint.spexception",),
        root_cause_template=(
            "SharePoint rejected the operation with {exception_class}; the provider's "
            "fault message contains the server-side error detail."
        ),
        next_checks=(
            "Review the SharePoint fault code and message retained with this incident",
            "Verify the target list, item, and service-account permissions",
            "Check whether the SharePoint API contract or target resource changed",
        ),
        specificity=30,
    ),
    FailurePattern(
        pattern_id="soap.fault",
        exception_contains="SOAPException",
        root_cause_template=(
            "The web-service operation failed with {exception_class}; the runtime reported "
            "a SOAP processing or fault response rather than a successful call."
        ),
        next_checks=(
            "Review the retained SOAP fault message and response status",
            "Validate the endpoint, operation, authentication, and request contract",
            "Check the downstream service logs at the failure time",
        ),
        specificity=22,
    ),
    FailurePattern(
        pattern_id="java.null_pointer",
        exception_contains="NullPointerException",
        root_cause_template=(
            "The failing component raised {exception_class}; the runtime proves a null "
            "reference symptom but does not identify which input or value was null."
        ),
        next_checks=(
            "Inspect nullable inputs, context variables, and lookup results entering the failing component",
            "Compare the failed component's input shape with the most recent successful execution",
            "Add an explicit null guard or diagnostic output before the failing component",
        ),
        specificity=20,
    ),
    # --- Camel / Talend ESB error handler -----------------------------------
    FailurePattern(
        pattern_id="camel.delivery_exhausted",
        message_contains=("exhausted after delivery attempt",),
        root_cause_template=(
            "Message delivery gave up after exhausting its retry budget{attempts}: every "
            "attempt to reach the downstream endpoint failed ({exception_class})."
        ),
        next_checks=(
            "Check whether the downstream endpoint was reachable and healthy during this window",
            "Confirm host '{host}' resolves and its port is open from the runtime host",
            "Review the route's redelivery policy and whether failed messages reached a dead-letter queue",
        ),
        specificity=29,
    ),
    FailurePattern(
        # Platform-neutral connectivity failure. Kept ABOVE mule.connectivity in
        # specificity so a bare java.net.ConnectException from Talend/Camel is not
        # described as a MuleSoft fault.
        pattern_id="network.connect_failure",
        exception_contains="ConnectException",
        root_cause_template=(
            "Could not establish a connection to the target system ({exception_class}): the "
            "host refused the connection, was unreachable, or did not answer in time."
        ),
        next_checks=(
            "Check that the target service is listening and healthy",
            "Confirm host '{host}' is resolvable and reachable from the runtime host",
            "Review firewall, proxy, and DNS changes around the failure window",
        ),
        specificity=27,
    ),
    FailurePattern(
        # Gated on a Mule signal in the exception's qualified name: the previous
        # bare "ConnectException" constraint claimed every Talend and Camel
        # connectivity fault and rendered MuleSoft-specific advice for them.
        # Ranked ABOVE network.connect_failure because an explicitly Mule-qualified
        # exception is strictly stronger evidence of platform than a bare class name.
        pattern_id="mule.connectivity",
        exception_contains="ConnectException",
        evidence_contains=("mule",),
        root_cause_template=(
            "MuleSoft connector could not reach its target ({exception_class}): "
            "the connection to the downstream system failed or timed out."
        ),
        next_checks=(
            "Check connectivity from the Mule runtime to the target system",
            "Verify the connector configuration (host, port, credentials)",
            "Review the target system availability and firewall rules",
        ),
        specificity=30,
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
