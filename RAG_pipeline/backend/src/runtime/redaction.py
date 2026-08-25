"""Shared redaction for provider-supplied runtime error text.

Provider error strings reach two very different destinations -- the sync health
record and, since documentation evidence, an LLM prompt -- so the filter lives
here rather than in either consumer.  One implementation means one place to
strengthen when a new secret shape turns up in provider output.

Keyword filters are not enough
------------------------------
Measured over the 827 real failure rows held in the runtime mirror there are
zero credentials, zero IPv4 literals and zero email addresses -- the keyword
filters below never fire.  What DOES pass through verbatim is entity shape:
a customer-identifying object-store bucket (``gs://<customer>-talend/...``) and
an internal hosting FQDN (``https://mas.<internal-host>``).  Neither is a
secret and neither is caught by looking for the word "password", yet both
identify a customer estate inside a prompt sent to a third-party model.

Redaction therefore normalises entity SHAPES as well as keywords:

* URIs keep their scheme and lose their identity -- ``gs://bucket/path`` ->
  ``gs://<bucket>/<path>``, ``https://host/whatever`` -> ``https://<host>/...``.
  "A GCS object was not found" is the diagnosis; the bucket name is not.
* Bare FQDNs and IPv4 literals become ``<host>``.
* Execution UUIDs are stripped from prompt-facing text only
  (:func:`sanitize_doc_evidence_error`): they are pure noise, they cost tokens,
  and they make one recurring failure read as many distinct ones.  The stored
  record keeps them, because that is the id an operator needs to pull the run.

What is deliberately PRESERVED
------------------------------
Component names (``tMSSqlConnection_2``, ``tBigQueryOutput_1_tBQBE``) carry the
whole diagnosis, and the documentation pipeline's component-echo fabrication
validator rejects any ``tXxx_N`` reference that is absent from the evidence it
was given -- redacting one here would turn a real runtime fact into a
fabrication false-positive.  Java/.NET qualified class names
(``java.sql.SQLException``) are preserved for the same reason: they are the
fault, not an address.  Both are protected structurally rather than by luck --
see :func:`_looks_like_host` -- and pinned by tests.

Module layering
---------------
This module is a LEAF: it imports nothing from ``src.runtime`` and everything
in ``src.runtime`` may import it.  ``redact_credentials`` lives here rather than
in ``insights.parser`` for exactly that reason -- importing it from the parser
pulls in ``src.runtime.insights.__init__`` -> ``insights.repository`` ->
``store.repository``, which imports this module back, so the cycle resolved or
failed purely on which module an interpreter happened to load first.
``insights.parser`` re-exports the name, so every existing import path is
unchanged.
"""

from __future__ import annotations

import re

MAX_ERROR_SUMMARY = 240

_TOKENISH_RE = re.compile(
    r"(?i)\b(bearer|token|secret|password|passwd|pwd|api[_-]?key)\b\s*[:=]?\s*\S*"
)
_CREDENTIAL_RE = re.compile(
    r"(?i)(password|passwd|pwd|token|secret|api[_\-]?key)\s*[=:]\s*\S+",
)
# Inline URL/JDBC userinfo: scheme://user:pass@host or jdbc:...user/pass@host.
# Requires a trailing "@host" so plain "host:port" (no userinfo) is never touched.
_USERINFO_RE = re.compile(r"[^\s:/@]+[:/][^\s:/@]+@(?=/*[^\s/@])")


def redact_credentials(text: str) -> str:
    """Replace credential patterns with <redacted>.

    Covers both keyword forms (password=..., token:...) and inline URL/JDBC
    userinfo (user:pass@host, user/pass@host). Userinfo is stripped first so a
    secret embedded in a connection string never survives into a persisted field.
    """
    redacted = _USERINFO_RE.sub("<redacted>@", text)
    return _CREDENTIAL_RE.sub(r"\1=<redacted>", redacted)

# Any `scheme://rest` form, including multi-part driver schemes
# (`jdbc:jtds:sqlserver://...`).  The remainder stops at whitespace or a
# sentence/bracket delimiter so surrounding prose survives.
# The separator is captured rather than assumed to be `://`: JDBC URLs put the
# credential marker before it (`jdbc:oracle:thin:@//db-prod-scan:1521/ORCLPDB1`),
# and a pattern hard-coded to `://` skips them entirely -- leaking the database
# host, which is exactly the shape this module exists to remove.  Preserving the
# separator keeps the fault legible: "the Oracle thin client could not reach its
# host" survives, the host does not.
_URI_RE = re.compile(
    r"(?i)\b([a-z][a-z0-9+.\-]*(?::[a-z][a-z0-9+.\-]*)*)(:@?//)([^\s,;)\]}>'\"]*)"
)

# Schemes whose authority component is a storage bucket rather than a host.
# Labelling it `<bucket>` keeps the reader oriented: "the bucket was wrong" and
# "the host was unreachable" are different findings.
_BUCKET_SCHEMES = frozenset(
    {"gs", "gcs", "s3", "s3a", "s3n", "abfs", "abfss", "wasb", "wasbs", "az", "oss", "cos"}
)

_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# Candidate dotted token.  Deliberately permissive: the decision of whether a
# match is a host is made by `_looks_like_host`, not by the pattern, because the
# discriminating rules are about label CASE and vocabulary, which a regex
# expresses badly.
_DOTTED_TOKEN_RE = re.compile(
    r"\b[A-Za-z0-9_](?:[A-Za-z0-9_\-]*[A-Za-z0-9_])?"
    r"(?:\.[A-Za-z0-9_](?:[A-Za-z0-9_\-]*[A-Za-z0-9_])?)+"
)

# A dotted token whose FIRST label is one of these is a package path, not a
# host: hostnames read `service.example.com`, packages read `com.example.service`.
# Combined with the uppercase-label rule this keeps every Java/.NET exception
# class in the corpus intact.
_PACKAGE_ROOTS = frozenset(
    {
        "java",
        "javax",
        "jakarta",
        "com",
        "org",
        "net",
        "io",
        "sun",
        "oracle",
        "microsoft",
        "system",
        "android",
        "scala",
        "kotlin",
        "edu",
        "gov",
    }
)

# A two-label token is only a host when its last label is a plausible suffix.
# Without this, `config.xml` and `Data.truncation` would both become `<host>`.
_HOST_SUFFIXES = frozenset(
    {
        "com",
        "net",
        "org",
        "io",
        "co",
        "eu",
        "nl",
        "be",
        "de",
        "fr",
        "uk",
        "us",
        "local",
        "localdomain",
        "internal",
        "intra",
        "corp",
        "lan",
        "cloud",
        "dev",
        "app",
    }
)

# Last labels that mark a filename rather than a domain.
_FILE_SUFFIXES = frozenset(
    {
        "xml",
        "json",
        "yaml",
        "yml",
        "txt",
        "log",
        "md",
        "csv",
        "tsv",
        "jar",
        "war",
        "ear",
        "zip",
        "gz",
        "tar",
        "sql",
        "properties",
        "cfg",
        "conf",
        "ini",
        "html",
        "htm",
        "exe",
        "dll",
        "class",
        "py",
        "js",
        "ts",
        "sh",
        "bat",
        "item",
        "jks",
        "pem",
    }
)

_EXECUTION_ID_RE = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)

HOST_PLACEHOLDER = "<host>"
BUCKET_PLACEHOLDER = "<bucket>"
EXECUTION_ID_PLACEHOLDER = "<execution-id>"

# Placeholder stems without their closing `>`, because `_URI_RE`'s remainder
# class stops there.  Used only to recognise already-redacted text.
_PLACEHOLDER_PREFIXES = (HOST_PLACEHOLDER[:-1], BUCKET_PLACEHOLDER[:-1])


def _normalise_uri(match: re.Match[str]) -> str:
    """Keep the scheme, drop the authority and the path."""
    scheme = match.group(1)
    separator = match.group(2)
    remainder = match.group(3)
    if remainder.startswith(_PLACEHOLDER_PREFIXES):
        # Already redacted.  The remainder pattern stops at `>`, so a second
        # pass over `https://<host>/...` would match only `<host`, emit
        # `https://<host>`, and leave the original `>/...` behind -- growing one
        # `>` per pass.  Redaction is applied at more than one boundary
        # (parser, builder, read path), so it has to be idempotent.
        return match.group(0)
    authority, path_separator, _path = remainder.partition("/")
    is_bucket = scheme.split(":", 1)[0].casefold() in _BUCKET_SCHEMES
    label = BUCKET_PLACEHOLDER if is_bucket else HOST_PLACEHOLDER
    if not authority or not path_separator:
        # `https://host` with nothing after it -- inventing a path would state
        # more than the source did.
        return f"{scheme}{separator}{label}"
    if is_bucket:
        return f"{scheme}{separator}{label}/<path>"
    return f"{scheme}{separator}{label}/..."


def _looks_like_host(token: str) -> bool:
    """Decide whether a dotted token is an address or a code identifier.

    The corpus makes this a real question: `mas.manage.infranl.almhosting` is an
    internal FQDN and `java.sql.SQLException` is the fault itself, and both are
    dotted lowercase-leading tokens.  Three structural rules separate them, in
    order of how much they protect:

    1. an uppercase label means a class or type name (every exception class in
       the corpus is caught here);
    2. a leading package root (`com`, `java`, `org`, ...) means a package path
       -- hostnames put the suffix last, packages put it first;
    3. an underscore in any label means it is not a hostname at all (DNS labels
       cannot contain one), which also protects identifiers such as
       `tBigQueryOutput_1_tBQBE`.

    Remaining requirements are ordinary domain shape: an alphabetic final label
    that is not a file extension, and either three-plus labels or a two-label
    form whose suffix is a recognised one.  An all-lowercase four-label package
    under an unlisted root (`de.acme.util.helper`) is a known and accepted false
    positive: losing a package path costs a reader far less than leaking a
    customer host costs the customer.
    """
    labels = token.split(".")
    if len(labels) < 2 or not all(labels):
        return False
    if any(character.isupper() for character in token):
        return False
    if "_" in token:
        return False
    if labels[0] in _PACKAGE_ROOTS:
        return False
    suffix = labels[-1]
    if not suffix.isalpha() or not 2 <= len(suffix) <= 24:
        return False
    if suffix in _FILE_SUFFIXES:
        return False
    if len(labels) >= 3:
        return True
    return suffix in _HOST_SUFFIXES


def _redact_bare_hosts(text: str) -> str:
    return _DOTTED_TOKEN_RE.sub(
        lambda match: HOST_PLACEHOLDER if _looks_like_host(match.group(0)) else match.group(0),
        text,
    )


def redact_entities(text: str) -> str:
    """Normalise identifying entity shapes, keeping the diagnostic content.

    URIs are handled before bare hosts so a URL host is replaced with its scheme
    intact rather than being eaten by the FQDN rule.
    """
    normalised = _URI_RE.sub(_normalise_uri, text)
    normalised = _IPV4_RE.sub(HOST_PLACEHOLDER, normalised)
    return _redact_bare_hosts(normalised)


def strip_execution_ids(text: str) -> str:
    """Replace execution UUIDs with a placeholder.

    Prompt-facing text only.  The id identifies nothing outside TMC, but every
    occurrence costs tokens and makes one recurring failure look like many.
    """
    return _EXECUTION_ID_RE.sub(EXECUTION_ID_PLACEHOLDER, text)


def _sanitize(value: str, *, strip_ids: bool) -> str:
    compact = " ".join(value.replace("\n", " ").split())
    redacted = redact_credentials(_TOKENISH_RE.sub("<redacted>", compact))
    redacted = redact_entities(redacted)
    if strip_ids:
        redacted = strip_execution_ids(redacted)
    if len(redacted) > MAX_ERROR_SUMMARY:
        return redacted[: MAX_ERROR_SUMMARY - 3] + "..."
    return redacted


def sanitize_sync_error(value: str | None) -> str | None:
    """Bound and redact failure text before persistence or API exposure.

    Execution ids are KEPT: this text is stored and shown to an operator, for
    whom the id is the handle used to pull the run out of the source system.
    """
    if not value:
        return None
    return _sanitize(value, strip_ids=False)


def sanitize_doc_evidence_error(value: str | None) -> str | None:
    """Redact failure text bound for an LLM prompt.

    Everything :func:`sanitize_sync_error` does, plus execution-id stripping.
    Truncation happens last so the id removal buys back characters of real
    diagnostic text rather than discarding them.
    """
    if not value:
        return None
    return _sanitize(value, strip_ids=True)
