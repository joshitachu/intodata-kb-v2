"""Deterministic log message parser for runtime insight extraction.

Pure functions: no I/O, no side effects. Returns None/empty for fields that
cannot be reliably extracted — never invents causes or fills uncertain values.

Redaction
---------
Every field this parser returns that carries provider PROSE or an ADDRESS is
redacted before it leaves, because each one is persisted into
``runtime_enriched_events`` and flows on into text an LLM reads. Keyword
credential filtering is not sufficient and never was: measured over the stored
corpus it fires on zero rows, while what passed through verbatim was entity
SHAPE — a customer object-store bucket (``gs://<customer>-talend/...``), internal
FQDNs (``https://talend-acc.<internal>/...``), a customer SaaS host and an Azure
tenant UUID. ``redact_log_text`` therefore composes the keyword filter with
:func:`src.runtime.redaction.redact_entities`, and the two structurally-typed
address fields get whole-value treatment (:func:`redact_endpoint`,
:func:`redact_host`) because part of an address is still an address.

What survives is the diagnosis: component names (``tMSSqlConnection_2``),
Java/.NET class names (``java.net.SocketException``), URI schemes, error codes
and field/table names. See ``src.runtime.redaction`` for why each is structurally
protected rather than protected by luck.

The parser does not store raw log messages wholesale; it returns the extracted
structural signals plus ``causal_message``, the single diagnostic line that the
emitting runtime itself wrote to explain the fault. That line is redacted before
it leaves the parser and IS persisted — it is the only field that carries the
runtime's own words, and reconstructing an equivalent sentence from tokens loses
information the log already stated.

Dialect dispatch
----------------
Runtimes prefix their log lines differently, and a single regex library tuned to
one prefix silently yields nothing on the others. Line shape is therefore
resolved by an ordered tuple of ``_DIALECTS`` (Strategy): the first dialect whose
head pattern matches owns component/job/retry/endpoint attribution and nominates
the diagnostic remainder. Shared signal extraction (exception resolution, error
code, JDBC, field violations) then runs over the whole message regardless of
dialect. Adding a runtime is a new ``_Dialect`` entry, not an edit to
``parse_runtime_log_message``.

The four shapes observed in stored runtime data:

  tmc_status  ``...: Execution <id> failed with code 1 and error Job stopped with
              errors or unable to run. Exception in component <comp> (<job>) <exc>: <detail>``
              Talend Management Console execution-status line. Richest shape:
              names the component AND the job, so it is tried first.
  bracket     ``[<comp>] <detail>`` — Talend ESB / Karaf route logs.
  camel       ``Failed delivery for (MessageId: ...). Exhausted after delivery
              attempt: <n> caught: <chain> invoking <url>`` — Camel error handler.
  talend      ``<tComponent_n> <detail>`` — Talend Studio job stdout; the component
              is the bare leading token, with no delimiter at all.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable

from src.runtime.insights.catalog import CATALOG

# `redact_credentials` lives in the leaf `src.runtime.redaction` module and is
# re-exported here: importing it FROM this module drags in
# `src.runtime.insights.__init__` -> `insights.repository` -> `store.repository`,
# which imports `redaction` back, and the cycle then resolved or blew up
# depending on which module the interpreter loaded first. Callers keep the
# familiar `from src.runtime.insights.parser import redact_credentials`.
#
# The import direction is one-way and must stay that way: `redaction` is a LEAF
# and imports nothing from `src.runtime`, so `parser -> redaction` can never
# close a cycle no matter which module an interpreter loads first.
from src.runtime.redaction import (  # noqa: F401
    HOST_PLACEHOLDER,
    redact_credentials,
    redact_entities,
)

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
# ActiveMQ: "after: 1 attempt(s)". States the total tried, not a ratio.
_RETRY_AFTER_RE = re.compile(r"after:\s*(\d+)\s*attempt", re.IGNORECASE)
_DURATION_MS_RE = re.compile(r"(\d+)ms\b")
_TIMEOUT_S_RE = re.compile(r"timeout[= ](\d+)s\b", re.IGNORECASE)
_TIMEOUT_MS_RE = re.compile(r"timeout[= ](\d+)ms\b", re.IGNORECASE)
# Oracle thin-driver form: jdbc:oracle:thin:@//host:port/service
_JDBC_AT_URL_RE = re.compile(r"jdbc:[^:]+:[^@]*@//([^:/\s]+):\d+/(\w+)")
# Driver-authority form with no userinfo marker: jdbc:jtds:sqlserver://host:port/db,
# jdbc:postgresql://host:port/db, jdbc:mysql://host:port/db. jTDS (Talend's SQL
# Server driver) has no "@" at all, which is why the Oracle-only pattern above
# extracted nothing from real Talend logs.
_JDBC_AUTHORITY_URL_RE = re.compile(
    r"jdbc:(?:[a-z0-9]+:)?[a-z0-9]+://([^:/;\s]+)(?::\d+)?/([A-Za-z0-9_$-]+)"
)
_HTTP_URL_RE = re.compile(r"https?://[^\s,)\]]+")
_HTTP_HOST_RE = re.compile(r"https?://([^\s:/]+)")
_JMS_QUEUE_RE = re.compile(r"jms:queue:([^\s(,\]]+)", re.IGNORECASE)
_SELECT_TABLE_RE = re.compile(r"\bSELECT(?:\s+\w+)?\s+on\s+(\w+)", re.IGNORECASE)
# Field violations are intentionally conservative: a field name must be
# explicitly labelled by the emitting runtime.  Capturing arbitrary quoted text
# would turn values/payload data into invented lineage evidence.
_NULL_FIELD_RE = re.compile(
    r"(?i)\b(?:field|column|attribute)\s+['\"`]?([A-Za-z_][A-Za-z0-9_.-]*)['\"`]?\s+(?:is|was|cannot be)\s+null\b"
)
_REQUIRED_FIELD_RE = re.compile(
    r"(?i)\b(?:required\s+)?(?:field|column|attribute)\s+['\"`]?([A-Za-z_][A-Za-z0-9_.-]*)['\"`]?\s+(?:is\s+)?(?:missing|required)\b"
)
_SQL_NULL_COLUMN_RE = re.compile(
    r"(?i)\bcannot insert (?:the value )?null into column ['\"`]?([A-Za-z_][A-Za-z0-9_.-]*)['\"`]?"
)
_TYPE_FIELD_RE = re.compile(
    r"(?i)\b(?:field|column|attribute)\s+['\"`]?([A-Za-z_][A-Za-z0-9_.-]*)['\"`]?\s+(?:expected|expects|must be)\s+([A-Za-z][A-Za-z0-9_ <>\[\]-]{0,100})"
)
# SQL Server names the table and column only on newer servers; the bare
# "Data truncation" form (jTDS/Talend) names neither, so the violation KIND is
# recorded without a field name rather than guessing which column overflowed.
_SQLSERVER_TRUNC_COLUMN_RE = re.compile(
    r"(?i)truncated in table\s+['\"`]?([A-Za-z0-9_.\[\]]+)['\"`]?\s*,\s*column\s+['\"`]?([A-Za-z0-9_.\[\]]+)['\"`]?"
)
_TRUNCATION_RE = re.compile(
    r"(?i)\b(?:data truncation|string or binary data would be truncated)\b"
)
# Salesforce SOQL compile failure names the offending field and the entity.
_SF_NO_SUCH_COLUMN_RE = re.compile(
    r"(?i)No such column\s+['\"`]?([A-Za-z0-9_]+)['\"`]?\s+on entity\s+['\"`]?([A-Za-z0-9_]+)['\"`]?"
)
_SF_EXCEPTION_CODE_RE = re.compile(r"exceptionCode='([A-Z_]+)'")
# BigQuery states the offending object in prose; both forms are explicit enough
# to attribute without guessing.
_BQ_INVALID_DATASET_RE = re.compile(r"(?i)Invalid dataset ID\s+\"([^\"]+)\"")
_BQ_NOT_FOUND_URI_RE = re.compile(r"(?i)Not found:\s+(?:Files|URI|Table|Dataset)\s+(\S+)")
_GCS_URI_RE = re.compile(r"gs://[^\s,)\]]+")

# ---------------------------------------------------------------------------
# Message families
# ---------------------------------------------------------------------------

_FAMILY_JDBC = "jdbc"
_FAMILY_JMS = "jms"
_FAMILY_JAVA_EXCEPTION = "java_exception"
_FAMILY_SQLSERVER = "sqlserver"
_FAMILY_BIGQUERY = "bigquery"
_FAMILY_SALESFORCE = "salesforce"
_FAMILY_CAMEL = "camel"
_FAMILY_HTTP = "http"

# Field-violation vocabulary. "truncation" and "unknown_field" join the original
# three: both are explicitly stated by the emitting runtime, and both are
# actionable in a way that a bare exception class is not.
_VIOLATION_NULL = "null_rejected"
_VIOLATION_MISSING = "missing_required"
_VIOLATION_TYPE = "type_mismatch"
_VIOLATION_TRUNCATION = "truncation"
_VIOLATION_UNKNOWN_FIELD = "unknown_field"


# ---------------------------------------------------------------------------
# Redaction of prompt-facing values
# ---------------------------------------------------------------------------
#
# One composed helper, used at every point where provider log text becomes a
# stored or prompt-facing value. Having a single entry point is the whole point:
# the reason `endpoint` shipped 10.4k rows of customer buckets and internal
# hostnames is that each assignment site independently chose `redact_credentials`
# and none of them was wrong on its own terms.

# Returned instead of the input when redaction itself fails. Degradation over
# failure: a value that could not be proven safe is not emitted, and no caller in
# the ingest path is ever aborted by a redaction bug.
REDACTION_FAILED_PLACEHOLDER = "<redacted>"

# Any authority still standing after `redact_entities` has run. The generic URI
# rule keys on `scheme://`, which Talend's Oracle driver URL does not have:
# `jdbc:oracle:thin:@//db-prod-scan:1521/ORCLPDB1` puts `:@` between the scheme
# chain and the `//`, so both the URI rule and the FQDN rule missed it and the
# single-label internal host survived intact (verified in the stored corpus).
# Applied to the `endpoint` field only, where every `//authority` is by
# definition an address. The lookahead keeps the pass idempotent.
_RESIDUAL_AUTHORITY_RE = re.compile(r"//(?!<host>|<bucket>)[^\s/?#]+")

# `redact_entities` is not idempotent over its OWN output. Its URI rule stops the
# authority at `>` (part of the delimiter class that keeps trailing punctuation
# out of a match), so a second pass over `https://<host>/...` reads the authority
# as `<host` with no path, re-emits `https://<host>`, and leaves the orphaned `>`
# behind: `https://<host>>/...`, growing by one character per pass.
#
# That matters here and not in the doc-evidence lane because this lane redacts
# twice by design -- once on write so nothing identifying is stored, once on read
# so the rows written before the fix are covered too. Collapsing the artefact
# makes the composition a true no-op on already-clean text, which is the property
# the read path is entitled to assume.
#
# The underlying non-idempotency is a one-character fix in `src.runtime.redaction`
# (admit `>` inside the URI remainder) and belongs to that module's owner.
_PLACEHOLDER_ARTEFACT_RE = re.compile(r"(<host>|<bucket>)>+")


def redact_log_text(text: str | None) -> str | None:
    """Redact credentials AND identifying entity shapes from provider log text.

    The standard treatment for any free-text value derived from a provider log
    line that is persisted or sent to a model. ``None``/empty passes through
    unchanged so callers keep "absent" distinct from "redacted".

    Idempotent: re-running it over already-redacted text is a no-op, which is
    what makes it safe to apply on the read path as well as the write path.

    Never raises. Both underlying passes are pure regex over ``str`` and cannot
    fail for a string input, but a non-string slipping in from an adapter must
    degrade to a placeholder rather than take down an ingest batch.
    """
    if not text:
        return text
    try:
        redacted = redact_entities(redact_credentials(str(text)))
        return _PLACEHOLDER_ARTEFACT_RE.sub(r"\1", redacted)
    except Exception:  # pragma: no cover - defensive; see docstring
        return REDACTION_FAILED_PLACEHOLDER


def redact_endpoint(value: str | None) -> str | None:
    """Redact a value whose entire content is an endpoint address.

    ``redact_log_text`` plus a flattening of any authority the shared URI rule
    could not see. Structurally-typed, so it can be stricter than the free-text
    pass without losing diagnosis: the scheme, the driver chain and the object
    path all survive, and only the host/bucket identity is normalised.
    """
    redacted = redact_log_text(value)
    if not redacted:
        return redacted
    return _RESIDUAL_AUTHORITY_RE.sub(f"//{HOST_PLACEHOLDER}", redacted)


def redact_host(value: str | None) -> str | None:
    """Normalise a value whose entire content is a host.

    Whole-value, not shape-matched. ``redact_entities`` recognises a host by dot
    structure, so it correctly flattens ``mas.manage.infranl.almhosting.com`` but
    leaves the single-label internal names this field is full of — ``SRVA0673``,
    ``srvo0667``, ``db-prod-scan`` — untouched. A field that holds nothing but an
    address does not need shape inference: the address IS the value.

    The field is kept rather than dropped because presence, not content, is what
    the pipeline reads from it (``_infer_message_family``, ``_derive_confidence``,
    ``builder._carries_no_signal``, and the catalog's ``{host}`` slot). Returning
    the placeholder preserves every one of those decisions exactly.
    """
    if not value:
        return value
    return HOST_PLACEHOLDER


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


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


def _first_line(message: str) -> str:
    return message.split("\n", 1)[0].strip()


# Fault phrasings that are self-describing without an exception class or a
# component prefix. These lines ARE the diagnosis, so they become the causal
# message directly. Deliberately a closed list of explicit failure statements:
# matching on a bare "failed" token would promote routine status chatter.
_SELF_DESCRIBING_FAULT_RES = (
    re.compile(r"(?i)^Failed to connect to\s"),
    re.compile(r"(?i)^No method was found for the WSDL operation\b"),
    re.compile(r"(?i)^Interceptor for .*has thrown exception\b"),
    re.compile(r"(?i)^Transport \(.*\) failed\b"),
    re.compile(r"(?i)^Could not (?:connect|resolve|open)\b"),
)


def _self_describing_fault(message: str) -> str | None:
    """Return the leading line when it states a fault in its own right."""
    head = _first_line(message)
    if any(pattern.search(head) for pattern in _SELF_DESCRIBING_FAULT_RES):
        return head
    return None


# ---------------------------------------------------------------------------
# Causal-sentence selection
# ---------------------------------------------------------------------------
#
# Resolving the ROOT EXCEPTION and choosing the READABLE SENTENCE are two
# different jobs, and conflating them loses the diagnosis. The deepest
# ``Caused by:`` is by definition the root exception, so it is right for
# ``exception_class``. It is frequently the LEAST informative sentence in the
# message, because a wrapped transport failure bottoms out in a generic status:
#
#   tBigQueryOutput_2_tBQBE Invalid dataset ID "test_ zaf_del_clean". Dataset IDs
#       must be alphanumeric (plus underscores and dashes) ...   <- the diagnosis
#   ...
#   Caused by: ...GoogleJsonResponseException: 400 Bad Request   <- the root class
#
# Candidates are therefore scored for informativeness and the best one wins.
# Scoring is additive over explicit, individually testable signals — never a
# length heuristic, which would rank a stack frame above a one-line diagnosis.
#
# Crucially the candidate SET is unchanged: only lines the dialect or the
# exception resolver already nominated are eligible. Scanning the message for
# "interesting-looking" text would promote payload values into false evidence.

# A stack frame describes where, never what.
_STACK_FRAME_RE = re.compile(r"^\s*at\s+\S+\(")
# "<ExcClass>: 404 Not Found" / "Caused by: <ExcClass>: 400 Bad Request" — a bare
# protocol status with no statement of what was wrong.
_BARE_STATUS_RE = re.compile(
    r"(?i)^(?:caused by:\s*)?(?:[\w.$]*\w(?:Exception|Error))\s*:\s*\d{3}\s+[\w\s]{0,24}$"
)
# "<ExcClass>" or "<ExcClass>:" with no detail following the colon.
_CLASS_ONLY_RE = re.compile(
    r"(?i)^(?:caused by:\s*)?(?:[\w.$]*\w(?:Exception|Error))\s*:?\s*$"
)
# A quoted identifier is the runtime naming the offending object: the single
# strongest signal that a line states a specific fault.
_QUOTED_IDENT_RE = re.compile(r"[\"'`]([^\"'`]{2,})[\"'`]")
# Explicit statements of a violated rule or an absent object.
_CONSTRAINT_VOCAB_RE = re.compile(
    r"(?i)\b(?:invalid|must be|must not|cannot|can't|too long|too large|exceeds?|"
    r"out of range|not found|no such|unknown|missing|required|denied|refused|"
    r"unauthori[sz]ed|forbidden|truncat\w*|duplicate|violat\w*|not permitted|"
    r"does not exist|already exists|timed out|timeout|unreachable)\b"
)
# Names a concrete endpoint or a package-qualified symbol.
_NAMES_OBJECT_RE = re.compile(r"(?:[a-z][a-z0-9+.-]*://|jdbc:|\b\w+(?:\.\w+){2,}\b)")


def _informativeness(line: str) -> int:
    """Score how much a candidate line explains the fault. Higher is better.

    Deterministic and additive. Negative scores mark lines that are structurally
    incapable of being a diagnosis (stack frames, bare protocol statuses), so a
    genuinely informative sibling always outranks them.
    """
    text = line.strip()
    if not text:
        return -10
    score = 0
    if _STACK_FRAME_RE.match(text):
        score -= 6
    if _BARE_STATUS_RE.match(text):
        score -= 3
    if _CLASS_ONLY_RE.match(text):
        score -= 2
    if _QUOTED_IDENT_RE.search(text):
        score += 3
    if _CONSTRAINT_VOCAB_RE.search(text):
        score += 2
    if _NAMES_OBJECT_RE.search(text):
        score += 1
    return score


def _choose_causal_message(candidates: Iterable[str | None]) -> str | None:
    """Pick the most informative candidate sentence.

    Ties resolve to the earlier candidate, so the caller's ordering remains the
    tie-break and behaviour is unchanged wherever scores are equal.
    """
    best: str | None = None
    best_score = 0
    for candidate in candidates:
        if not candidate:
            continue
        text = candidate.strip()
        if not text:
            continue
        score = _informativeness(text)
        if best is None or score > best_score:
            best, best_score = text, score
    return best


# ---------------------------------------------------------------------------
# Dialect dispatch
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _DialectResult:
    """Head-of-line attribution produced by one dialect.

    ``diagnostic`` is the runtime's own explanation with the transport//status
    preamble stripped — the text a human would read to understand the fault.
    """

    dialect: str
    component_name: str | None = None
    job_name: str | None = None
    diagnostic: str | None = None
    retry_attempt: int | None = None
    retry_max: int | None = None


# TMC execution-status line. The trailing segment is the payload:
#   "Exception in component tMSSqlOutput_5 (Maximo2RHM_Workorders) java.sql.Batch...: Data truncation"
_TMC_EXCEPTION_IN_COMPONENT_RE = re.compile(
    r"Exception in component\s+(?P<component>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?:\((?P<job>[^)]*)\))?\s*(?P<detail>.*)",
    re.DOTALL,
)
_TMC_STATUS_HEAD_RE = re.compile(
    r"(?i)Execution\s+\S+\s+failed with code\s+\d+"
)
# Camel dead-letter/error-handler line.
_CAMEL_DELIVERY_RE = re.compile(
    r"(?i)Failed delivery for\s*\(.*?\)\.\s*"
    r"Exhausted after delivery attempt:\s*(?P<attempt>\d+)\s*caught:\s*(?P<detail>.*)",
    re.DOTALL,
)
# Talend Studio component prefix: a bare leading token following the
# t<Name>[_<n>][_<suffix>] convention (tMSSqlOutput_5, tBigQueryOutput_1_tBQBE).
# Anchored and requiring an initial "t" + uppercase so ordinary prose openings
# ("Route starting", "Error starting activator") can never match.
_TALEND_COMPONENT_TOKEN_RE = re.compile(
    r"^(t[A-Z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+)(?=\s|$)"
)
# Any leading token, used ONLY to test membership of a known-component inventory.
# Compound subjob-prefixed keys (Create_Business_Central_Token_v2_1_tExtract...)
# do not follow the convention above and are resolvable only by inventory match.
_LEADING_TOKEN_RE = re.compile(r"^(\S+)(?=\s|$)")


def _dialect_tmc_status(message: str) -> _DialectResult | None:
    """TMC execution-status line: names both the component and the job."""
    match = _TMC_EXCEPTION_IN_COMPONENT_RE.search(message)
    if match is None:
        return None
    # Require the surrounding status preamble so a prose mention of the phrase
    # in some other runtime's log cannot hijack attribution.
    if _TMC_STATUS_HEAD_RE.search(message) is None:
        return None
    detail = (match.group("detail") or "").strip()
    job = (match.group("job") or "").strip() or None
    return _DialectResult(
        dialect="tmc_status",
        component_name=match.group("component"),
        job_name=job,
        diagnostic=_first_line(detail) or None,
    )


def _dialect_bracket(message: str) -> _DialectResult | None:
    """Talend ESB / Karaf form: ``[component] detail``."""
    match = _BRACKET_COMPONENT_RE.match(message)
    if match is None:
        return None
    remainder = message[match.end() :].strip()
    return _DialectResult(
        dialect="bracket",
        component_name=match.group(1),
        diagnostic=_first_line(remainder) or None,
    )


def _dialect_camel(message: str) -> _DialectResult | None:
    """Camel error handler: retry count is stated, not implied."""
    match = _CAMEL_DELIVERY_RE.search(message)
    if match is None:
        return None
    attempt = int(match.group("attempt"))
    detail = _first_line((match.group("detail") or "").strip())
    return _DialectResult(
        dialect="camel",
        diagnostic=detail or None,
        # "Exhausted after delivery attempt: 3" states the terminal attempt,
        # which is by definition also the configured maximum.
        retry_attempt=attempt,
        retry_max=attempt,
    )


def _dialect_talend_component(
    message: str, known_components: frozenset[str] | None
) -> _DialectResult | None:
    """Talend Studio stdout: bare leading component token, no delimiter."""
    head = _first_line(message)
    match = _TALEND_COMPONENT_TOKEN_RE.match(head)
    component: str | None = None
    if match is not None:
        component = match.group(1)
    elif known_components:
        # Compound/subjob-prefixed keys are only safe to claim when the runtime
        # itself reported that exact component for this project.
        token_match = _LEADING_TOKEN_RE.match(head)
        if token_match is not None and token_match.group(1) in known_components:
            component = token_match.group(1)
    if component is None:
        return None
    remainder = head[len(component) :].strip()
    return _DialectResult(
        dialect="talend_component",
        component_name=component,
        diagnostic=remainder or None,
    )


def _resolve_dialect(
    message: str, known_components: frozenset[str] | None
) -> _DialectResult | None:
    """Return the first matching dialect's attribution, or None.

    Order is significance, not convenience: the TMC status line carries the most
    attribution (component + job), so it wins over the bare component prefix that
    the same fault also produces on a separate line.
    """
    for dialect in (_dialect_tmc_status, _dialect_bracket, _dialect_camel):
        result = dialect(message)
        if result is not None:
            return result
    return _dialect_talend_component(message, known_components)


@dataclasses.dataclass
class ParsedRuntimeLog:
    """Structural signals parsed from one log message. All fields optional — None
    means the signal was not present or could not be extracted reliably."""

    component_name: str | None = None
    job_name: str | None = None
    error_code: str | None = None
    error_family: str | None = None
    exception_class: str | None = None
    retry_attempt: int | None = None
    retry_max: int | None = None
    duration_ms: int | None = None
    timeout_ms: int | None = None
    # Redacted address fields. `endpoint` keeps its scheme/driver/object path and
    # loses the authority; `host` is the `<host>` placeholder whenever a host was
    # present at all. Both are persisted, so "was there an address" survives and
    # "whose address" does not.
    endpoint: str | None = None
    host: str | None = None
    database_name: str | None = None
    table_name: str | None = None
    queue_name: str | None = None
    field_name: str | None = None
    # null_rejected | missing_required | type_mismatch | truncation |
    # unknown_field.  None means no explicit field-level diagnostic was present.
    field_violation_kind: str | None = None
    expected_type: str | None = None
    message_family: str | None = None
    confidence: str | None = None
    # Which dialect claimed the line. Diagnostic value: a population of rows with
    # dialect=None is the signal that a runtime's log shape is unrecognised.
    dialect: str | None = None
    # The runtime's own diagnostic sentence (redacted): the deepest causal line
    # of a "Caused by:" chain, else the dialect's stripped diagnostic. Persisted —
    # this is the only field carrying the log's own words.
    causal_message: str | None = None


def parse_runtime_log_message(
    message: str, known_components: Iterable[str] | None = None
) -> ParsedRuntimeLog:
    """Extract structural signals from a runtime log message string.

    Conservative extraction: returns None for a field when the signal is absent
    or ambiguous. Does not invent causes. ``endpoint``, ``host`` and
    ``causal_message`` are redacted before returning — credentials AND
    identifying entity shape, not credentials alone (see the module docstring).

    ``known_components`` is the authoritative component inventory for the
    project (``runtime_component_metrics.component_key``). Supplying it lets the
    Talend dialect resolve compound subjob-prefixed component keys that no
    convention regex can safely claim; omitting it degrades to convention-only
    matching, never to guessing.
    """
    result = ParsedRuntimeLog()
    inventory = frozenset(known_components) if known_components else None

    # --- Head-of-line attribution (dialect) --------------------------------
    dialect = _resolve_dialect(message, inventory)
    if dialect is not None:
        result.dialect = dialect.dialect
        result.component_name = dialect.component_name
        result.job_name = dialect.job_name
        result.retry_attempt = dialect.retry_attempt
        result.retry_max = dialect.retry_max

    # Error code: delegate family matching to the catalog (Oracle, MySQL,
    # Postgres/SQLSTATE, HTTP, SQL Server, generic). Conservative — benign lines
    # yield None.
    code, family = CATALOG.extract_error_code(message)
    if code is not None:
        result.error_code = code
        result.error_family = family

    # Exception class — resolve the deepest causal exception (root of a
    # "Caused by:" chain or the failure-attributed line), not the outermost.
    exc_class, causal_line = _resolve_causal_exception(message)
    if exc_class is not None:
        result.exception_class = exc_class

    # Causal message: the most informative of the lines already nominated by the
    # exception resolver, the dialect, and the self-describing-fault check. The
    # root exception line is NOT automatically preferred — see the scoring notes
    # above: a wrapped failure often bottoms out in a contentless status.
    diagnostic = dialect.diagnostic if dialect is not None else None
    if diagnostic and causal_line:
        # The exception resolver works line-wise, so on a single-line status
        # record it returns the transport preamble along with the fault. When the
        # dialect already isolated a shorter fragment of that same line, they are
        # the same text — collapse to the tighter one rather than scoring both.
        if diagnostic in causal_line and len(diagnostic) < len(causal_line):
            causal_line = None
    # Candidate order is the previous fixed precedence, retained as the tie-break:
    # a candidate displaces the old winner only by scoring strictly higher.
    causal = _choose_causal_message(
        (causal_line, diagnostic, _self_describing_fault(message))
    )
    if causal:
        result.causal_message = redact_log_text(causal)

    # Retry attempt / max — only when the dialect did not already state it.
    if result.retry_attempt is None:
        retry_m = _RETRY_RE.search(message)
        after_m = _RETRY_AFTER_RE.search(message)
        if retry_m:
            result.retry_attempt = int(retry_m.group(1))
            result.retry_max = int(retry_m.group(2))
        elif after_m:
            attempts = int(after_m.group(1))
            result.retry_attempt = attempts
            result.retry_max = attempts

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

    _extract_endpoints(message, result)
    _extract_field_violation(message, result)
    _infer_message_family(result)
    _derive_confidence(result)
    return result


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _extract_endpoints(message: str, result: ParsedRuntimeLog) -> None:
    """Populate endpoint/host/database/table/queue from connection evidence.

    Ordered by specificity: an explicit JDBC URL beats an HTTP URL beats a
    storage URI, because the more specific form also identifies the database.

    ``endpoint`` and ``host`` are ADDRESSES and are normalised on the way out
    (:func:`redact_endpoint`, :func:`redact_host`). ``database_name`` /
    ``table_name`` / ``queue_name`` are deliberately NOT: they are single-token
    schema object names inside an already-anonymised system, they are the
    diagnosis itself ("Invalid dataset ID ..."), and running the entity pass over
    them would buy nothing while risking a lowercase dotted object name being
    read as an FQDN.
    """
    jdbc_m = _JDBC_AT_URL_RE.search(message) or _JDBC_AUTHORITY_URL_RE.search(message)
    if jdbc_m:
        result.host = redact_host(jdbc_m.group(1))
        result.database_name = jdbc_m.group(2)
        result.endpoint = redact_endpoint(message[jdbc_m.start() : jdbc_m.end()])

    if result.endpoint is None:
        http_m = _HTTP_URL_RE.search(message)
        if http_m:
            result.endpoint = redact_endpoint(http_m.group(0))
            host_m = _HTTP_HOST_RE.search(message)
            if host_m and result.host is None:
                result.host = redact_host(host_m.group(1))

    # BigQuery states the offending object explicitly.
    bq_dataset = _BQ_INVALID_DATASET_RE.search(message)
    if bq_dataset and result.database_name is None:
        result.database_name = bq_dataset.group(1)
    bq_missing = _BQ_NOT_FOUND_URI_RE.search(message)
    if bq_missing and result.endpoint is None:
        result.endpoint = redact_endpoint(bq_missing.group(1))
    if result.endpoint is None:
        gcs_m = _GCS_URI_RE.search(message)
        if gcs_m:
            result.endpoint = redact_endpoint(gcs_m.group(0))

    jms_m = _JMS_QUEUE_RE.search(message)
    if jms_m:
        result.queue_name = jms_m.group(1)

    sel_m = _SELECT_TABLE_RE.search(message)
    if sel_m:
        result.table_name = sel_m.group(1)


def _extract_field_violation(message: str, result: ParsedRuntimeLog) -> None:
    """Populate field_name / field_violation_kind / expected_type.

    Ordering is by evidential strength. A named table+column from the target
    database is the strongest; a bare "Data truncation" names no column at all,
    so the KIND is recorded with ``field_name`` left None rather than guessing
    which column overflowed — an invented column name would become false lineage
    evidence downstream.
    """
    sqlserver_trunc = _SQLSERVER_TRUNC_COLUMN_RE.search(message)
    sf_column = _SF_NO_SUCH_COLUMN_RE.search(message)
    sql_null_m = _SQL_NULL_COLUMN_RE.search(message)
    null_m = _NULL_FIELD_RE.search(message)
    required_m = _REQUIRED_FIELD_RE.search(message)
    type_m = _TYPE_FIELD_RE.search(message)

    if sqlserver_trunc:
        if result.table_name is None:
            result.table_name = sqlserver_trunc.group(1)
        result.field_name = sqlserver_trunc.group(2)
        result.field_violation_kind = _VIOLATION_TRUNCATION
    elif sf_column:
        result.field_name = sf_column.group(1)
        result.field_violation_kind = _VIOLATION_UNKNOWN_FIELD
        if result.table_name is None:
            result.table_name = sf_column.group(2)
    elif sql_null_m:
        result.field_name = sql_null_m.group(1)
        result.field_violation_kind = _VIOLATION_NULL
    elif null_m:
        result.field_name = null_m.group(1)
        result.field_violation_kind = _VIOLATION_NULL
    elif required_m:
        result.field_name = required_m.group(1)
        result.field_violation_kind = _VIOLATION_MISSING
    elif type_m:
        result.field_name = type_m.group(1)
        result.field_violation_kind = _VIOLATION_TYPE
        result.expected_type = type_m.group(2).strip()
    elif _TRUNCATION_RE.search(message):
        # Kind without a name: the runtime stated the violation class but not
        # the column.
        result.field_violation_kind = _VIOLATION_TRUNCATION


def _infer_message_family(result: ParsedRuntimeLog) -> None:
    """Classify the message by the platform that produced the fault.

    Platform-specific families are decided before the generic ones so a BigQuery
    or Salesforce fault is not filed as a bare java_exception.
    """
    exc = result.exception_class or ""
    if exc in ("BigQueryException", "GoogleJsonResponseException") or result.error_family == "bigquery":
        result.message_family = _FAMILY_BIGQUERY
    elif result.error_family == "salesforce" or "Fault" in exc and result.field_violation_kind == _VIOLATION_UNKNOWN_FIELD:
        result.message_family = _FAMILY_SALESFORCE
    elif result.error_family == "sqlserver" or exc == "BatchUpdateException":
        result.message_family = _FAMILY_SQLSERVER
    elif result.dialect == "camel":
        result.message_family = _FAMILY_CAMEL
    elif result.endpoint or result.host or result.database_name:
        result.message_family = _FAMILY_JDBC if result.database_name else _FAMILY_HTTP
    elif result.queue_name:
        result.message_family = _FAMILY_JMS
    elif result.exception_class:
        result.message_family = _FAMILY_JAVA_EXCEPTION


def _derive_confidence(result: ParsedRuntimeLog) -> None:
    """Confidence: how many strong signals were found.

    Unchanged in spirit from the original single-dialect implementation — a
    strong signal is still only an error code or an exception class. Structural
    signals (retry, timeout, host, queue, field) merely promote a line that also
    named its component.
    """
    strong = sum(1 for v in (result.error_code, result.exception_class) if v is not None)
    has_structural = any(
        v is not None
        for v in (
            result.retry_attempt,
            result.timeout_ms,
            result.host,
            result.queue_name,
            result.field_name,
            result.field_violation_kind,
        )
    )
    if strong >= 2:
        result.confidence = "high"
    elif strong == 1 or (result.component_name and has_structural):
        result.confidence = "medium"
    elif result.component_name or result.duration_ms is not None:
        result.confidence = "low"
