"""Runtime execution evidence for Phase 3 documents.

A scan says what a job is written to do. This module says what it actually did:
how often it ran, in which environments, how long it took, and how it failed.
The facts come from the RAG backend's runtime mirror (TMC today) through one
bounded read endpoint, and are rendered deterministically -- no LLM tool calls,
no agentic retrieval -- exactly like the scan-fact bundle they are appended to.

Four deliberate constraints:

* **Aggregates only.** The endpoint returns counts, percentiles, redacted failure
  headlines, diagnostic-coverage counts and at most three evidence-backed
  incident insights per unit. Raw log lines never cross the boundary. A document
  needs to explain how a job behaves, not replay its output.
* **Absence is stated, never implied.** Units with no runtime data are recorded
  so a document can say "no runtime data was available" instead of leaving a
  reader to infer from silence that a job never runs. Every fetch returns a
  store; the store always carries a mode. There is no silent nothing.
* **Never a gate.** A runtime mirror that is down, unconfigured, or mid-migration
  degrades the documents, it does not fail the run.
* **Pure.** This module touches no filesystem and imports nothing from the
  harness. It returns text and payloads; the pipeline decides where they land.
  That is what keeps the two engines from diverging again.

Two identifier keys, always
---------------------------
``FactsManifest.unit_names()`` returns ``job_id or display_name`` -- the job id
whenever the scanner assigned one. TMC schedules tasks under the *display* name.
Verified in werkorder: 429 job rows, 374 distinct display names, 79 of which
have ``job_id != name``; a live call sending manifest names matched 4 units and
one sending display names matched 5. The unit lost by the manifest-key-only call
was ``Maximo2Quintiq_Workorder_Route`` -- the ESB route that motivated
:data:`_ROUTE_DURATION_LABEL`. So the fetch sends the UNION of both keys and
reconciles the response back onto the manifest unit name. This is not fuzzy
matching: resolution stays exact string equality, we simply send both of the
exact strings we hold. ``build_unit_index`` in ``target_evidence.py`` does the
same two-key reconciliation on the scan side.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote

import httpx

from mcp_server.config import config

logger = logging.getLogger(__name__)


# --- Staging-relative paths (imported by the wiring package) -----------------

# The rollup lives in `_analysis/` with every other deterministic evidence tier.
#
# Two naming constraints, both load-bearing:
#   * the filename must NOT contain the substring `_evidence_`, because
#     `phase3_slices._trim_priority` promotes any such file to trim rank 0 as a
#     per-doc target bundle; the rollup earns rank 0 by being named explicitly,
#     not by colliding with another convention;
#   * `_analysis/` is removed by Phase 4 cleanup, so the rollup never publishes.
RUNTIME_PROFILE_MD = "_analysis/_runtime_profile.md"

# The fetched payload cache. `_transient/` and NOT `_evidence/`: Phase 4 cleanup
# is a denylist of `_preprocess`, `_analysis` and `_transient`, and the swap to
# `docs/` is a whole-directory rename -- anything else would publish the cached
# payload, with its provider failure text, into `output/{pid}/docs/` forever.
# `_evidence/` is additionally an existing namespace with a different meaning.
RUNTIME_CACHE_JSON = "_transient/runtime/runtime_evidence.json"

# Read by `api/routes/generate_runs.py` off the PUBLISHED docs tree, so unlike
# the two above it must survive Phase 4 and the swap: `_assessment/` does.
RUNTIME_COVERAGE_FILENAME = "runtime_evidence_coverage.json"
RUNTIME_COVERAGE_JSON = f"_assessment/{RUNTIME_COVERAGE_FILENAME}"

RUNTIME_REDACTION_FLAGS_FILENAME = "runtime_redaction_flags.json"
RUNTIME_REDACTION_FLAGS_JSON = f"_assessment/{RUNTIME_REDACTION_FLAGS_FILENAME}"

# Bumped when a key is removed or its meaning changes. Added keys do not bump:
# every reader is required to tolerate unknown keys.
RUNTIME_COVERAGE_SCHEMA_VERSION = 1

# The endpoint's own `units` cap (a FastAPI `max_length`). Exceeding it returns
# 422, which is why the client batches instead of the server raising the cap.
SERVER_MAX_UNITS_PER_CALL = 200

DOC_EVIDENCE_PATH = "/api/v1/runtime/doc-evidence"


# Talend serializes DI Jobs and ESB Routes into the same item format, and a
# Route is a long-running listener rather than a batch job: werkorder's
# `Maximo2Quintiq_Workorder_Route` legitimately ran for 76 days in one
# execution. Reporting that under "run duration" reads as a defect, so routes
# get their own wording. Classification is scanner-computed (`is_esb_route`),
# never inferred here from a name suffix.
_ROUTE_DURATION_LABEL = "Uptime per start"
_JOB_DURATION_LABEL = "Run duration"

# Duration percentiles below this many successful runs describe an anecdote, not
# a distribution. Verified: one ESB route reports p50 = 13.2 days over five
# successes, which a reader would take as a performance finding.
_DURATION_SAMPLE_FLOOR = 30

# Cadence classification floors (R11).
_CADENCE_MIN_STARTS = 20
_CADENCE_MAX_DISPERSION = 0.5
_CADENCE_TOLERANCE = 0.10

_COMMON_INTERVALS_S: tuple[tuple[int, str], ...] = (
    (60, "1 min"),
    (120, "2 min"),
    (300, "5 min"),
    (600, "10 min"),
    (900, "15 min"),
    (1_800, "30 min"),
    (3_600, "1 h"),
    (7_200, "2 h"),
    (10_800, "3 h"),
    (14_400, "4 h"),
    (21_600, "6 h"),
    (28_800, "8 h"),
    (43_200, "12 h"),
    (86_400, "1 day"),
    (604_800, "1 week"),
)


class RuntimeMode(str, Enum):
    """What the runtime lane produced for this run.

    Deliberately three values, not five. `partial` and `scope_mismatch` are
    independent booleans on the store because they are orthogonal axes: a fetch
    that retrieved two of three batches AND shows a 99% orphan ratio has no
    single correct enum value, and forcing one loses whichever fact the picker
    ranked second.
    """

    DATA = "data"
    OPTED_OUT = "opted_out"
    UNAVAILABLE = "unavailable"


class AliasKind(str, Enum):
    """Which of a unit's two identifiers the runtime source answered to."""

    UNIT_NAME = "unit_name"
    DISPLAY_NAME = "display_name"


@dataclass(frozen=True)
class RuntimeUnitRequest:
    """One scanned unit, addressed by both identifiers it may be known under.

    `unit_name` is the harness identity (`FactsManifest.unit_names()`, i.e.
    `job_id or display_name`) and is what the store keys on, because it is what
    every consumer downstream looks up with. `display_name` is what a human
    named the job in Studio and what TMC therefore schedules it under.
    """

    unit_name: str
    display_name: str = ""

    @property
    def aliases(self) -> tuple[str, ...]:
        """Both keys, deduplicated, manifest identity first."""
        keys = [self.unit_name]
        if self.display_name and self.display_name != self.unit_name:
            keys.append(self.display_name)
        return tuple(keys)


def as_unit_requests(units: Sequence[Any]) -> tuple[RuntimeUnitRequest, ...]:
    """Normalise a caller's unit list into two-key requests.

    Accepts `RuntimeUnitRequest`, `(unit_name, display_name)` pairs, bare
    strings, and anything exposing `.name` / `.display_name` (which is exactly
    `UnitFact`, so the caller can pass `manifest.units` straight through).
    Bare strings are honoured rather than rejected so an older call site keeps
    working -- it simply gets single-key behaviour, which is what it had.
    """
    out: list[RuntimeUnitRequest] = []
    seen: set[str] = set()
    for item in units:
        if isinstance(item, RuntimeUnitRequest):
            request = item
        elif isinstance(item, str):
            request = RuntimeUnitRequest(unit_name=item)
        elif isinstance(item, (tuple, list)) and item:
            unit_name = str(item[0] or "")
            display = str(item[1] or "") if len(item) > 1 else ""
            request = RuntimeUnitRequest(unit_name=unit_name, display_name=display)
        else:
            unit_name = str(getattr(item, "name", "") or "")
            display = str(getattr(item, "display_name", "") or "")
            request = RuntimeUnitRequest(unit_name=unit_name, display_name=display)
        if not request.unit_name or request.unit_name in seen:
            continue
        seen.add(request.unit_name)
        out.append(request)
    return tuple(out)


# --- Redaction verification (R13 / A7) ---------------------------------------
#
# The RAG backend owns normalisation (`src/runtime/redaction.py`: URIs and hosts
# keep their scheme and lose their identity, execution UUIDs are stripped,
# component names are deliberately preserved because the component-echo
# fabrication validator depends on them). This side cannot import across the
# service boundary and must not ship a second, subtly different normaliser --
# two filters that disagree is worse than one that is occasionally coarse.
#
# So this is a VERIFIER: it asserts the text arriving here already looks
# redacted, and when it does not it drops the offending failure line and records
# a flag. Never the run (R17). The checks are deliberately narrow -- a false
# positive costs one failure headline, and a false negative is a customer
# identifier in a third-party prompt.

_PLACEHOLDERS = ("<host>", "<bucket>", "<path>", "<redacted>", "<execution-id>", "<flagged>")

_CREDENTIAL_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_\-]?key|bearer)\b\s*[=:]\s*(?!<)\S+"
)
_USERINFO_RE = re.compile(r"[^\s:/@]+[:/][^\s:/@]+@(?=/*[^\s/@])")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
# Any scheme://authority whose authority is not already a placeholder. Covers
# bucket URIs (gs://, s3://) and plain hosts alike -- both are identity.
_URI_AUTHORITY_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*(?::[a-z][a-z0-9+.\-]*)*://(?!<)\S")
_DOTTED_TOKEN_RE = re.compile(
    r"\b[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?)+"
)
# A dotted lowercase token led by one of these is a package path, not a host:
# hostnames put the suffix last, packages put it first.
_PACKAGE_ROOTS = frozenset(
    {
        "java", "javax", "jakarta", "com", "org", "net", "io", "sun", "oracle",
        "microsoft", "system", "android", "scala", "kotlin", "edu", "gov",
    }
)
_FILE_SUFFIXES = frozenset(
    {
        "xml", "json", "yaml", "yml", "txt", "log", "md", "csv", "tsv", "jar",
        "war", "ear", "zip", "gz", "tar", "sql", "properties", "cfg", "conf",
        "ini", "html", "htm", "exe", "dll", "class", "py", "js", "ts", "sh",
        "bat", "item", "jks", "pem",
    }
)
_HOST_SUFFIXES = frozenset(
    {
        "com", "net", "org", "io", "co", "eu", "nl", "be", "de", "fr", "uk",
        "us", "local", "localdomain", "internal", "intra", "corp", "lan",
        "cloud", "dev", "app",
    }
)

_FLAG_EXCERPT_CHARS = 120


@dataclass(frozen=True)
class RuntimeRedactionFlag:
    """One failure headline dropped because it did not look redacted."""

    unit_name: str
    kind: str
    # The offending span is replaced before it is stored, so the flag record is
    # itself safe to write to `_assessment/` and to read in a review.
    excerpt: str

    def to_json(self) -> dict[str, Any]:
        return {"unit_name": self.unit_name, "kind": self.kind, "excerpt": self.excerpt}

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "RuntimeRedactionFlag":
        return cls(
            unit_name=str(payload.get("unit_name") or ""),
            kind=str(payload.get("kind") or "unknown"),
            excerpt=str(payload.get("excerpt") or ""),
        )


def _looks_like_host(token: str) -> bool:
    """Conservative bare-FQDN test, mirroring the backend's structural rules."""
    labels = token.split(".")
    if len(labels) < 2 or not all(labels):
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


def redaction_violation(text: str) -> tuple[str, str] | None:
    """Return `(kind, masked_excerpt)` when `text` still carries an identifier.

    Order matters: credentials first (the worst), then addresses. Only the first
    violation is reported -- the line is dropped either way, so enumerating the
    rest buys nothing and costs excerpt space.
    """
    if not text:
        return None
    checks: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("credential", _CREDENTIAL_RE),
        ("userinfo", _USERINFO_RE),
        ("email", _EMAIL_RE),
        ("ipv4", _IPV4_RE),
        ("uri_authority", _URI_AUTHORITY_RE),
    )
    for kind, pattern in checks:
        match = pattern.search(text)
        if match:
            return kind, _mask(text, match.start(), match.end())
    lowered_scan = _DOTTED_TOKEN_RE.finditer(text.lower())
    for match in lowered_scan:
        token = match.group(0)
        if any(token in placeholder for placeholder in _PLACEHOLDERS):
            continue
        if _looks_like_host(token):
            return "fqdn", _mask(text, match.start(), match.end())
    return None


def _mask(text: str, start: int, end: int) -> str:
    masked = f"{text[:start]}<flagged>{text[end:]}"
    if len(masked) > _FLAG_EXCERPT_CHARS:
        return masked[: _FLAG_EXCERPT_CHARS - 3] + "..."
    return masked


# --- Cadence classification (R11) --------------------------------------------


@dataclass(frozen=True)
class CadenceClassification:
    """A deterministic reading of how often a unit actually starts.

    Never derived by a model. Verified real medians snap onto configured cron
    intervals (3600 s, 1801 s, 900 s) while the MEAN gap for the same job is
    2204 s -- a number nobody configured, which is exactly what a model asked to
    "estimate the frequency" would produce.
    """

    label: str
    interval_seconds: int | None
    sample_size: int
    # gaps | insufficient_sample | dispersed | no_gap_data
    basis: str

    def to_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "interval_seconds": self.interval_seconds,
            "sample_size": self.sample_size,
            "basis": self.basis,
        }


def _quantile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile over a pre-sorted sequence."""
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    position = (len(values) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(values[low])
    return float(values[low] + (values[high] - values[low]) * (position - low))


def classify_cadence(
    gap_seconds: Sequence[float] | None = None,
    *,
    starts: int = 0,
    environments: Sequence[str] = (),
    gap_p25: float | None = None,
    gap_p50: float | None = None,
    gap_p75: float | None = None,
    gap_sample_size: int | None = None,
) -> CadenceClassification:
    """Classify observed cadence from inter-run gaps.

    Emits a named interval only when all three hold: the median is within 10% of
    a common interval, at least `_CADENCE_MIN_STARTS` starts were observed, and
    IQR/median stays under `_CADENCE_MAX_DISPERSION`. Anything else is `ad hoc`
    with the start count, because "we cannot tell" is a fact and a made-up
    interval is not.

    Multi-environment degradation is REPORTED, not hidden. `runtime_jobs` is
    unique on `(project_id, source_key, job_id)` rather than on name, so figures
    aggregated over a name blend DEV, TEST and PROD; an hourly PROD schedule
    plus ad-hoc DEV runs legitimately fails the dispersion gate. Saying "ad hoc"
    and stopping would blame the job for a property of the aggregate, so the
    label names the environments it blends.
    """
    envs = tuple(str(env) for env in environments if env)
    blend = ""
    if len(envs) > 1:
        blend = (
            f"; figures blend {len(envs)} environments ({', '.join(envs)}), whose "
            "schedules cannot be separated from this aggregate"
        )

    if gap_p50 is not None:
        median = float(gap_p50)
        p25 = float(gap_p25) if gap_p25 is not None else median
        p75 = float(gap_p75) if gap_p75 is not None else median
        sample = int(gap_sample_size if gap_sample_size is not None else max(starts - 1, 0))
    elif gap_seconds:
        ordered = sorted(float(gap) for gap in gap_seconds if gap is not None and gap > 0)
        if not ordered:
            return CadenceClassification(
                label=_no_gap_label(starts, blend), interval_seconds=None,
                sample_size=0, basis="no_gap_data",
            )
        median = _quantile(ordered, 0.5)
        p25 = _quantile(ordered, 0.25)
        p75 = _quantile(ordered, 0.75)
        sample = len(ordered)
    else:
        return CadenceClassification(
            label=_no_gap_label(starts, blend), interval_seconds=None,
            sample_size=0, basis="no_gap_data",
        )

    observed_starts = max(starts, sample + 1)
    if observed_starts < _CADENCE_MIN_STARTS:
        return CadenceClassification(
            label=(
                f"ad hoc ({observed_starts} starts; below the "
                f"{_CADENCE_MIN_STARTS}-start floor for cadence classification){blend}"
            ),
            interval_seconds=None,
            sample_size=sample,
            basis="insufficient_sample",
        )
    if median <= 0:
        return CadenceClassification(
            label=f"ad hoc ({observed_starts} starts){blend}",
            interval_seconds=None, sample_size=sample, basis="dispersed",
        )
    dispersion = (p75 - p25) / median
    if dispersion >= _CADENCE_MAX_DISPERSION:
        return CadenceClassification(
            label=(
                f"ad hoc ({observed_starts} starts; gaps vary too widely to be a "
                f"schedule, IQR/median {dispersion:.2f}){blend}"
            ),
            interval_seconds=None,
            sample_size=sample,
            basis="dispersed",
        )
    for interval, name in _COMMON_INTERVALS_S:
        if abs(median - interval) <= interval * _CADENCE_TOLERANCE:
            return CadenceClassification(
                label=(
                    f"every {name} (median gap {median:.0f} s over "
                    f"{observed_starts} starts){blend}"
                ),
                interval_seconds=interval,
                sample_size=sample,
                basis="gaps",
            )
    return CadenceClassification(
        label=(
            f"regular but non-standard (median gap {median:.0f} s over "
            f"{observed_starts} starts; no common interval within "
            f"{int(_CADENCE_TOLERANCE * 100)}%){blend}"
        ),
        interval_seconds=None,
        sample_size=sample,
        basis="gaps",
    )


def _no_gap_label(starts: int, blend: str) -> str:
    return (
        f"not classified ({starts} starts observed; the runtime endpoint does not "
        f"expose per-run start times, so no inter-run gap distribution is available){blend}"
    )


# --- Unit evidence ------------------------------------------------------------


@dataclass(frozen=True)
class RuntimeLogQuality:
    """How much of a unit's failure population has useful log evidence."""

    failed_executions: int = 0
    executions_with_logs: int = 0
    diagnosed_executions: int = 0
    capture_ceiling_reached: int = 0

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> "RuntimeLogQuality":
        raw = payload or {}
        return cls(
            failed_executions=int(raw.get("failed_executions") or 0),
            executions_with_logs=int(raw.get("executions_with_logs") or 0),
            diagnosed_executions=int(raw.get("diagnosed_executions") or 0),
            capture_ceiling_reached=int(raw.get("capture_ceiling_reached") or 0),
        )

    def to_json(self) -> dict[str, int]:
        return {
            "failed_executions": self.failed_executions,
            "executions_with_logs": self.executions_with_logs,
            "diagnosed_executions": self.diagnosed_executions,
            "capture_ceiling_reached": self.capture_ceiling_reached,
        }


@dataclass(frozen=True)
class RuntimeIncidentInsight:
    """One recurring high/medium-confidence diagnosis derived from logs."""

    pattern_id: str
    confidence: str
    occurrences: int
    most_recent_at: str
    # `failed_component` is the originating component selected by the backend's
    # causal-chain analysis.  A later tDie/wrapper may report the terminal
    # failure without being its origin; keep that distinction explicit.
    failed_component: str = ""
    termination_component: str = ""
    error_code: str = ""
    exception_class: str = ""
    causal_message: str = ""
    root_cause_candidate: str = ""
    cause_origin: str = ""
    next_checks: tuple[str, ...] = ()

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "RuntimeIncidentInsight":
        return cls(
            pattern_id=str(payload.get("pattern_id") or ""),
            confidence=str(payload.get("confidence") or ""),
            occurrences=int(payload.get("occurrences") or 0),
            most_recent_at=str(payload.get("most_recent_at") or ""),
            failed_component=str(payload.get("failed_component") or ""),
            termination_component=str(payload.get("termination_component") or ""),
            error_code=str(payload.get("error_code") or ""),
            exception_class=str(payload.get("exception_class") or ""),
            causal_message=str(payload.get("causal_message") or ""),
            root_cause_candidate=str(payload.get("root_cause_candidate") or ""),
            cause_origin=str(payload.get("cause_origin") or ""),
            # Backend output is already bounded.  Keep a local hard cap as a
            # contract guard so a future provider cannot turn this compact
            # evidence card into a troubleshooting-manual/raw-log dump.
            next_checks=_bounded_strings(payload.get("next_checks"), limit=3),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "confidence": self.confidence,
            "occurrences": self.occurrences,
            "most_recent_at": self.most_recent_at,
            "failed_component": self.failed_component or None,
            "termination_component": self.termination_component or None,
            "error_code": self.error_code or None,
            "exception_class": self.exception_class or None,
            "causal_message": self.causal_message or None,
            "root_cause_candidate": self.root_cause_candidate or None,
            "cause_origin": self.cause_origin or None,
            "next_checks": list(self.next_checks),
        }


@dataclass(frozen=True)
class RuntimeUnitEvidence:
    """One unit's runtime profile, as returned by the backend."""

    unit_name: str
    job_name: str
    source_key: str
    resolved_by: str
    environments: tuple[str, ...]
    total_runs: int
    success_runs: int
    failed_runs: int
    unfinished_runs: int
    failure_rate: float
    current_failure_streak: int
    first_run_at: str | None
    last_run_at: str | None
    runs_per_day: float | None
    p50_duration_ms: int | None
    p95_duration_ms: int | None
    max_duration_ms: int | None
    plans: tuple[str, ...]
    # (failed_at, error_summary, occurrences) -- distinct failure MODES, so a
    # count of 700 is one entry rather than 700 near-identical lines.
    recent_failures: tuple[tuple[str, str, int], ...]
    # Which of the unit's two keys the runtime source answered to, and the
    # literal string that matched. Recorded because the two-key fetch is the
    # single most fragile assumption in this lane: when a future scan changes
    # how it assigns `job_id`, the coverage artifact shows it immediately.
    matched_alias: str = ""
    alias_kind: str = AliasKind.UNIT_NAME.value
    # Optional, forward-compatible: the endpoint does not emit inter-run gaps
    # today, so cadence classification degrades to a stated "not classified".
    gap_p25_seconds: float | None = None
    gap_p50_seconds: float | None = None
    gap_p75_seconds: float | None = None
    gap_sample_size: int | None = None
    log_quality: RuntimeLogQuality = field(default_factory=RuntimeLogQuality)
    incident_insights: tuple[RuntimeIncidentInsight, ...] = ()

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "RuntimeUnitEvidence":
        failures = tuple(
            (
                str(item.get("failed_at") or ""),
                str(item.get("error_summary") or ""),
                int(item.get("occurrences") or 1),
            )
            for item in payload.get("recent_failures") or []
        )
        return cls(
            unit_name=str(payload.get("unit_name") or ""),
            job_name=str(payload.get("job_name") or ""),
            source_key=str(payload.get("source_key") or ""),
            resolved_by=str(payload.get("resolved_by") or ""),
            environments=tuple(str(env) for env in payload.get("environments") or ()),
            total_runs=int(payload.get("total_runs") or 0),
            success_runs=int(payload.get("success_runs") or 0),
            failed_runs=int(payload.get("failed_runs") or 0),
            unfinished_runs=int(payload.get("unfinished_runs") or 0),
            failure_rate=float(payload.get("failure_rate") or 0.0),
            current_failure_streak=int(payload.get("current_failure_streak") or 0),
            first_run_at=_as_text(payload.get("first_run_at")),
            last_run_at=_as_text(payload.get("last_run_at")),
            runs_per_day=payload.get("runs_per_day"),
            p50_duration_ms=payload.get("p50_duration_ms"),
            p95_duration_ms=payload.get("p95_duration_ms"),
            max_duration_ms=payload.get("max_duration_ms"),
            plans=tuple(str(plan) for plan in payload.get("plans") or ()),
            recent_failures=failures,
            matched_alias=str(payload.get("matched_alias") or ""),
            alias_kind=str(payload.get("alias_kind") or AliasKind.UNIT_NAME.value),
            gap_p25_seconds=_as_float(payload.get("gap_p25_seconds")),
            gap_p50_seconds=_as_float(payload.get("gap_p50_seconds")),
            gap_p75_seconds=_as_float(payload.get("gap_p75_seconds")),
            gap_sample_size=(
                int(payload["gap_sample_size"])
                if payload.get("gap_sample_size") is not None
                else None
            ),
            log_quality=RuntimeLogQuality.from_payload(payload.get("log_quality")),
            incident_insights=tuple(
                RuntimeIncidentInsight.from_payload(item)
                for item in payload.get("incident_insights") or ()
                if isinstance(item, Mapping)
            ),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "unit_name": self.unit_name,
            "job_name": self.job_name,
            "source_key": self.source_key,
            "resolved_by": self.resolved_by,
            "environments": list(self.environments),
            "total_runs": self.total_runs,
            "success_runs": self.success_runs,
            "failed_runs": self.failed_runs,
            "unfinished_runs": self.unfinished_runs,
            "failure_rate": self.failure_rate,
            "current_failure_streak": self.current_failure_streak,
            "first_run_at": self.first_run_at,
            "last_run_at": self.last_run_at,
            "runs_per_day": self.runs_per_day,
            "p50_duration_ms": self.p50_duration_ms,
            "p95_duration_ms": self.p95_duration_ms,
            "max_duration_ms": self.max_duration_ms,
            "plans": list(self.plans),
            "recent_failures": [
                {"failed_at": failed_at, "error_summary": summary, "occurrences": count}
                for failed_at, summary, count in self.recent_failures
            ],
            "matched_alias": self.matched_alias,
            "alias_kind": self.alias_kind,
            "gap_p25_seconds": self.gap_p25_seconds,
            "gap_p50_seconds": self.gap_p50_seconds,
            "gap_p75_seconds": self.gap_p75_seconds,
            "gap_sample_size": self.gap_sample_size,
            "log_quality": self.log_quality.to_json(),
            "incident_insights": [item.to_json() for item in self.incident_insights],
        }

    def cadence(self) -> CadenceClassification:
        return classify_cadence(
            starts=self.total_runs,
            environments=self.environments,
            gap_p25=self.gap_p25_seconds,
            gap_p50=self.gap_p50_seconds,
            gap_p75=self.gap_p75_seconds,
            gap_sample_size=self.gap_sample_size,
        )

    def days_silent(self, now: datetime | None = None) -> int | None:
        last = _parse_ts(self.last_run_at)
        if last is None:
            return None
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        return max(0, int((reference - last).total_seconds() // 86_400))


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _bounded_strings(value: Any, *, limit: int) -> tuple[str, ...]:
    """Read a bounded JSON string array; malformed provider data is ignored."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in value[:limit] if str(item).strip())


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text[:19])
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# --- Store --------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimeEvidenceStore:
    """Runtime evidence for a whole project, fetched once per run.

    `mode` says what the lane produced. `partial` and `scope_mismatch` are
    separate booleans deliberately (A4): they describe different axes and a run
    can be both.

    `by_unit` is keyed on the MANIFEST unit name in every case, whichever alias
    the runtime source actually answered to -- the caller looks units up with the
    identity it already holds, and the alias that resolved is recorded on the
    evidence rather than leaking into the key.
    """

    window_days: int
    by_unit: Mapping[str, RuntimeUnitEvidence] = field(default_factory=dict)
    # Asked about, lookup completed, no runtime record.
    unmatched_units: tuple[str, ...] = ()
    # Asked about, lookup did NOT complete (its batch failed). Distinct from
    # unmatched: "we do not know" is not "there is nothing".
    unretrieved_units: tuple[str, ...] = ()
    # Runtime jobs with no scanned counterpart. Merged across batches by
    # INTERSECTION -- see `_merge_orphans`.
    orphan_runtime_jobs: tuple[str, ...] = ()
    requested_units: int = 0
    batches: int = 0
    failed_batches: int = 0
    source_key: str = ""
    partial: bool = False
    scope_mismatch: bool = False
    mode: RuntimeMode = RuntimeMode.DATA
    redaction_flags: tuple[RuntimeRedactionFlag, ...] = ()
    # Environments the server actually read, when an operator narrowed the read
    # scope for this project. Empty means every mirrored environment. This is
    # carried and published rather than assumed: the same project read at PROD
    # only and read wholesale produce different failure rates and different
    # cadences, and a document that does not name its scope cannot be compared
    # with the one generated before the scope changed.
    read_environments: tuple[str, ...] = ()
    # Units whose two keys matched two DIFFERENT runtime jobs. Rare and worth
    # seeing: it means the estate holds a job named after another job's id.
    alias_conflicts: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.by_unit)

    # --- per-unit rendering (unit docs; A5 keeps the rollup off this path) ---

    def section_for(
        self,
        unit_name: str,
        *,
        is_esb_route: bool = False,
        max_bytes: int,
        now: datetime | None = None,
    ) -> str:
        """Render one unit's runtime section, or "" when there is nothing to say.

        An asked-about unit with no data renders an explicit no-data notice
        rather than nothing: a writer that sees no runtime section at all cannot
        tell the difference between "this job never runs" and "we did not look".
        """
        evidence = self.by_unit.get(unit_name)
        if evidence is None:
            if unit_name in self.unretrieved_units:
                return _render_absent(unit_name, self.window_days, "unretrieved")
            if unit_name in self.unmatched_units:
                reason = "unmatched" if self.mode is RuntimeMode.DATA else self.mode.value
                return _render_absent(unit_name, self.window_days, reason)
            return ""
        rendered = _render(evidence, self.window_days, is_esb_route=is_esb_route, now=now)
        if len(rendered.encode("utf-8")) > max_bytes:
            rendered = _render(
                evidence, self.window_days, is_esb_route=is_esb_route,
                include_failures=False, now=now,
            )
        if len(rendered.encode("utf-8")) > max_bytes:
            return ""
        return rendered

    # --- serialisation ------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": RUNTIME_COVERAGE_SCHEMA_VERSION,
            "window_days": self.window_days,
            "mode": self.mode.value,
            "partial": self.partial,
            "scope_mismatch": self.scope_mismatch,
            "requested_units": self.requested_units,
            "batches": self.batches,
            "failed_batches": self.failed_batches,
            "source_key": self.source_key,
            "by_unit": {name: item.to_json() for name, item in sorted(self.by_unit.items())},
            "unmatched_units": list(self.unmatched_units),
            "unretrieved_units": list(self.unretrieved_units),
            "orphan_runtime_jobs": list(self.orphan_runtime_jobs),
            "alias_conflicts": list(self.alias_conflicts),
            "read_environments": list(self.read_environments),
            "redaction_flags": [flag.to_json() for flag in self.redaction_flags],
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "RuntimeEvidenceStore":
        """Rehydrate a cached store. Total: a malformed field degrades, never raises."""
        raw_mode = str(payload.get("mode") or RuntimeMode.DATA.value)
        try:
            mode = RuntimeMode(raw_mode)
        except ValueError:
            mode = RuntimeMode.UNAVAILABLE
        raw_units = payload.get("by_unit")
        by_unit = (
            {
                str(name): RuntimeUnitEvidence.from_payload(item)
                for name, item in raw_units.items()
                if isinstance(item, Mapping)
            }
            if isinstance(raw_units, Mapping)
            else {}
        )
        return cls(
            window_days=int(payload.get("window_days") or config.runtime_evidence_window_days),
            by_unit=by_unit,
            unmatched_units=tuple(str(x) for x in payload.get("unmatched_units") or ()),
            unretrieved_units=tuple(str(x) for x in payload.get("unretrieved_units") or ()),
            orphan_runtime_jobs=tuple(str(x) for x in payload.get("orphan_runtime_jobs") or ()),
            requested_units=int(payload.get("requested_units") or 0),
            batches=int(payload.get("batches") or 0),
            failed_batches=int(payload.get("failed_batches") or 0),
            source_key=str(payload.get("source_key") or ""),
            partial=bool(payload.get("partial", False)),
            scope_mismatch=bool(payload.get("scope_mismatch", False)),
            mode=mode,
            redaction_flags=tuple(
                RuntimeRedactionFlag.from_json(flag)
                for flag in payload.get("redaction_flags") or ()
                if isinstance(flag, Mapping)
            ),
            alias_conflicts=tuple(str(x) for x in payload.get("alias_conflicts") or ()),
            read_environments=tuple(str(x) for x in payload.get("read_environments") or ()),
        )

    # --- artifacts the wiring package writes --------------------------------

    def coverage_payload(self) -> dict[str, Any]:
        """The `_assessment/runtime_evidence_coverage.json` body.

        Defined here, once, because the writer and the reader
        (`api/routes/generate_runs.py`) previously agreed only through prose in
        a plan document and disagreed in practice -- which is how a run shipped
        `available=true` beside `mode="unavailable"`. Both sides import this.
        """
        matched_runtime_jobs = {item.job_name for item in self.by_unit.values() if item.job_name}
        return {
            "schema_version": RUNTIME_COVERAGE_SCHEMA_VERSION,
            "mode": self.mode.value,
            "window_days": self.window_days,
            "units_requested": self.requested_units,
            "units_matched": len(self.by_unit),
            "matched": sorted(self.by_unit),
            "unmatched": sorted(self.unmatched_units),
            "unretrieved": sorted(self.unretrieved_units),
            "orphan_runtime_jobs": list(self.orphan_runtime_jobs),
            "orphan_ratio": _ratio(
                len(self.orphan_runtime_jobs),
                len(self.orphan_runtime_jobs) + len(matched_runtime_jobs),
            ),
            "match_ratio": _ratio(len(self.by_unit), self.requested_units),
            "partial": self.partial,
            "scope_mismatch": self.scope_mismatch,
            "batches": self.batches,
            "failed_batches": self.failed_batches,
            "source_key": self.source_key,
            "resolved_by_alias": {
                name: item.alias_kind for name, item in sorted(self.by_unit.items())
            },
            "alias_conflicts": list(self.alias_conflicts),
            # Empty means every mirrored environment. Recorded so a coverage
            # comparison between two runs can tell a narrowed read from a
            # shrinking estate.
            "read_environments": list(self.read_environments),
            "redaction_flag_count": len(self.redaction_flags),
        }

    def redaction_flags_payload(self) -> dict[str, Any]:
        """The `_assessment/runtime_redaction_flags.json` body.

        Returned rather than written: this module owns no filesystem. The wiring
        package owns staging writes, so it decides where and whether this lands.
        """
        return {
            "schema_version": RUNTIME_COVERAGE_SCHEMA_VERSION,
            "source_key": self.source_key,
            "flag_count": len(self.redaction_flags),
            "note": (
                "Each entry is a runtime failure headline that reached the "
                "documentation service still carrying an identifier shape "
                "(credential, address, host or URI authority). The line was "
                "dropped from the evidence; the run was not. Redaction itself "
                "happens in the RAG backend -- a flag here means that filter "
                "needs strengthening, not that this side failed."
            ),
            "flags": [flag.to_json() for flag in self.redaction_flags],
        }


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


# --- per-unit rendering -------------------------------------------------------


def _ms(value: int | None) -> str:
    """Human-scaled duration. ASCII only (Windows cp1252 output constraint)."""
    if value is None:
        return "n/a"
    seconds = value / 1000
    if seconds < 1:
        return f"{value} ms"
    if seconds < 90:
        return f"{seconds:.1f} s"
    minutes = seconds / 60
    if minutes < 90:
        return f"{minutes:.1f} min"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.1f} h"
    return f"{hours / 24:.1f} days"


def _date(value: str | None) -> str:
    return value[:10] if value else "n/a"


_ABSENT_REASONS = {
    "unmatched": (
        "The runtime source was queried and holds no execution record for this "
        "unit in the window."
    ),
    "unretrieved": (
        "The runtime lookup for this unit did NOT complete (its batch failed), "
        "so nothing is known either way."
    ),
    RuntimeMode.OPTED_OUT.value: (
        "Runtime evidence was switched off for this run, so no lookup was made."
    ),
    RuntimeMode.UNAVAILABLE.value: (
        "The runtime source could not be reached for this run, so no lookup "
        "result exists."
    ),
}


def _render_absent(unit_name: str, window_days: int, reason: str) -> str:
    detail = _ABSENT_REASONS.get(reason, _ABSENT_REASONS["unmatched"])
    return (
        "## RUNTIME EVIDENCE\n\n"
        f"No runtime execution data was available for `{unit_name}` in the last "
        f"{window_days} days.\n\n"
        f"{detail}\n\n"
        "State this explicitly if the document discusses operational behaviour. "
        "Absence of data is NOT evidence that the unit never runs -- the runtime "
        "source schedules deployed tasks, and a unit invoked by a parent job "
        "(tRunJob) or embedded as a joblet runs without ever appearing as one.\n"
    )


def _render(
    evidence: RuntimeUnitEvidence,
    window_days: int,
    *,
    is_esb_route: bool = False,
    include_failures: bool = True,
    now: datetime | None = None,
) -> str:
    duration_label = _ROUTE_DURATION_LABEL if is_esb_route else _JOB_DURATION_LABEL
    silent = evidence.days_silent(now)
    cadence = evidence.cadence()
    lines = [
        "## RUNTIME EVIDENCE",
        "",
        f"Observed production behaviour of `{evidence.job_name}` over the last "
        f"{window_days} days, from runtime source `{evidence.source_key}`.",
        "These are measured facts; do not recompute, estimate or round them.",
        "",
        "| Fact | Value |",
        "| --- | --- |",
    ]
    # Recency leads. A frequency figure read without it is actively misleading:
    # `runs_per_day` is computed over the OBSERVED span, so a job that ran 61
    # times a day and then stopped 54 days ago still reports 61.29.
    lines.append(f"| Last run | {_last_run_cell(evidence, silent)} |")
    lines.append(f"| Runs observed | {evidence.total_runs} |")
    lines.append(f"| Succeeded | {evidence.success_runs} |")
    lines.append(
        f"| Failed | {evidence.failed_runs} ({evidence.failure_rate * 100:.1f}%) |"
    )
    if evidence.current_failure_streak:
        lines.append(
            f"| Consecutive failures (most recent) | {evidence.current_failure_streak} |"
        )
    if evidence.environments:
        lines.append(f"| Environments | {', '.join(evidence.environments)} |")
    lines.append(f"| Observed cadence | {cadence.label} |")
    duration_note = _duration_cell_suffix(evidence)
    lines.append(
        f"| {duration_label} (median) | {_duration_cell(evidence, evidence.p50_duration_ms)} |"
    )
    lines.append(
        f"| {duration_label} (p95) | {_duration_cell(evidence, evidence.p95_duration_ms)} |"
    )
    lines.append(
        f"| {duration_label} (max) | {_duration_cell(evidence, evidence.max_duration_ms)} |"
    )
    lines.append(
        f"| Observed window | {_date(evidence.first_run_at)} to "
        f"{_date(evidence.last_run_at)} |"
    )
    lines.append("")
    if is_esb_route:
        lines.append(
            "> This unit is an ESB route (a long-running listener), so the "
            "duration figures describe how long one start stayed up, NOT how "
            "long a batch takes to process."
        )
        lines.append("")
    if duration_note:
        lines.append(duration_note)
        lines.append("")
    if len(evidence.environments) > 1:
        # A8: runtime jobs are unique on (project, source, job_id), not on name,
        # so one name can carry DEV, TEST and PROD executions. Blending them
        # silently would make a PROD failure rate look like a DEV one.
        lines.append(
            "> Every figure above AGGREGATES the environments listed "
            f"({', '.join(evidence.environments)}). This source does not "
            "separate them, so do not attribute any figure to one environment."
        )
        lines.append("")
    if evidence.unfinished_runs:
        lines.append(
            f"> Telemetry gap: {evidence.unfinished_runs} of the observed runs "
            "never reported completion. They are counted in the run total but "
            "carry no outcome and no duration, so they are excluded from the "
            "success, failure and latency figures."
        )
        lines.append("")
    lines.append(
        "> Latency is measured over completed, successful runs only. Runs that "
        "failed or never completed are counted above but excluded from the "
        "duration figures."
    )
    lines.append("")
    quality = evidence.log_quality
    if quality.failed_executions:
        lines.append("### Diagnostic evidence coverage")
        lines.append("")
        lines.append("| Fact | Value |")
        lines.append("| --- | --- |")
        lines.append(
            f"| Failed executions with logs | {quality.executions_with_logs} of "
            f"{quality.failed_executions} |"
        )
        lines.append(
            f"| Failed executions with a useful diagnosis | {quality.diagnosed_executions} "
            f"of {quality.failed_executions} |"
        )
        lines.append(
            f"| Executions reaching the log-capture ceiling | "
            f"{quality.capture_ceiling_reached} |"
        )
        lines.append("")
        if quality.executions_with_logs < quality.failed_executions:
            lines.append(
                "> Some failed executions have no captured log evidence. Do not infer "
                "a cause for those failures."
            )
            lines.append("")
        if quality.capture_ceiling_reached:
            lines.append(
                "> At least one execution reached the capture ceiling. Its retained "
                "evidence is bounded and may not represent every event in that run."
            )
            lines.append("")
    if include_failures and evidence.incident_insights:
        lines.append("### Evidence-backed operational insights")
        lines.append("")
        for index, insight in enumerate(evidence.incident_insights):
            times = "execution" if insight.occurrences == 1 else "executions"
            label = "Dominant recurring issue" if index == 0 else "Additional recurring issue"
            details = [insight.pattern_id]
            if insight.error_code:
                details.append(f"code {insight.error_code}")
            if insight.exception_class:
                details.append(insight.exception_class)
            lines.append(
                f"- **{label}:** {insight.occurrences} {times}, most recently "
                f"{_date(insight.most_recent_at)}: {_md(', '.join(details))} "
                f"({insight.confidence} confidence)."
            )
            if insight.failed_component and insight.termination_component:
                lines.append(
                    "  Causal chain: originating component "
                    f"`{_md(insight.failed_component)}` -> termination/reporting "
                    f"component `{_md(insight.termination_component)}`. The latter "
                    "ended or reported the failed run; do not describe it as the origin."
                )
            elif insight.failed_component:
                lines.append(
                    f"  Originating component: `{_md(insight.failed_component)}`."
                )
            elif insight.termination_component:
                lines.append(
                    "  Termination/reporting component: "
                    f"`{_md(insight.termination_component)}`; the originating component "
                    "was not identified."
                )
            if insight.causal_message:
                lines.append(f"  Observed runtime message: {_md(insight.causal_message)}")
            if insight.root_cause_candidate:
                lines.append(
                    "  Evidence-backed interpretation: "
                    f"{_md(insight.root_cause_candidate)}"
                )
            if insight.next_checks:
                lines.append(
                    "  Suggested operator checks: "
                    + "; ".join(_md(check) for check in insight.next_checks)
                    + "."
                )
        lines.append("")
        lines.append(
            "> These are bounded operational summaries, not raw logs. Preserve the "
            "confidence qualifier, the origin-versus-termination distinction, and "
            "do not strengthen an interpretation into a proven business root cause. "
            "Use suggested checks only in operational or troubleshooting guidance."
        )
        lines.append("")
    if include_failures and evidence.recent_failures:
        lines.append("### Distinct failure modes observed")
        lines.append("")
        for failed_at, summary, occurrences in evidence.recent_failures:
            times = "once" if occurrences == 1 else f"{occurrences} times"
            lines.append(f"- Seen {times}, most recently {_date(failed_at)}: {summary}")
        lines.append("")
        lines.append(
            "> Grouped by failure mode: one entry can represent many executions. "
            "Failure text is provider-reported and redacted. Quote it as an "
            "observed symptom; do not infer a root cause it does not state."
        )
        lines.append("")
    return "\n".join(lines)


def _md(value: str) -> str:
    """Keep bounded diagnostic text inside one Markdown row/list item."""
    return " ".join(str(value).replace("|", "\\|").split())


def _last_run_cell(evidence: RuntimeUnitEvidence, silent: int | None) -> str:
    if evidence.last_run_at is None:
        return "n/a"
    if silent is None:
        return _date(evidence.last_run_at)
    if silent == 0:
        return f"{_date(evidence.last_run_at)} (today)"
    day_word = "day" if silent == 1 else "days"
    return f"{_date(evidence.last_run_at)} ({silent} {day_word} ago)"


def _duration_cell(evidence: RuntimeUnitEvidence, value: int | None) -> str:
    if evidence.success_runs < _DURATION_SAMPLE_FLOOR:
        return f"insufficient sample (n={evidence.success_runs})"
    return _ms(value)


def _duration_cell_suffix(evidence: RuntimeUnitEvidence) -> str:
    if evidence.success_runs >= _DURATION_SAMPLE_FLOOR:
        return ""
    return (
        f"> Duration percentiles are withheld: {evidence.success_runs} successful "
        f"run(s) is below the {_DURATION_SAMPLE_FLOOR}-run floor, and a percentile "
        "over a handful of runs describes those runs rather than the job. Do not "
        "state a typical runtime for this unit."
    )


# --- Fetch --------------------------------------------------------------------


def plan_batches(
    aliases: Sequence[str],
    *,
    max_units: int | None = None,
    max_query_bytes: int | None = None,
    fixed_query_bytes: int = 0,
) -> list[list[str]]:
    """Split the alias list into request-sized batches.

    Bounded by count AND by encoded query length. The count bound is the
    server's `max_length` (over it: a 422). The byte bound is the request line:
    200 names at ~30 characters is ~7 KB of query string against a common 8 KB
    limit, and the 414 that follows used to degrade -- through one bare
    `except` -- into "this project has no runtime data at all".

    An alias that alone exceeds the byte budget still gets its own batch rather
    than being dropped: losing a unit silently is worse than one batch failing
    loudly, and per-batch isolation contains it.
    """
    limit = min(
        max_units if max_units is not None else config.runtime_evidence_batch_units,
        SERVER_MAX_UNITS_PER_CALL,
    )
    limit = max(1, limit)
    byte_budget = max(
        1,
        (max_query_bytes if max_query_bytes is not None else config.runtime_evidence_max_query_bytes)
        - fixed_query_bytes,
    )
    batches: list[list[str]] = []
    current: list[str] = []
    current_bytes = 0
    for alias in aliases:
        cost = len(f"&units={quote(alias, safe='')}".encode("utf-8"))
        if current and (len(current) >= limit or current_bytes + cost > byte_budget):
            batches.append(current)
            current = []
            current_bytes = 0
        current.append(alias)
        current_bytes += cost
    if current:
        batches.append(current)
    return batches


def _merge_orphans(per_batch: Sequence[Sequence[str]]) -> tuple[str, ...]:
    """Merge per-batch orphan lists by INTERSECTION.

    The endpoint computes orphans relative to the units in ONE call, so over a
    fixed runtime vocabulary `A` each successful batch `i` returns
    `O_i = A - M_i` and the true orphan set is `A - union(M_i) = intersection(O_i)`.
    Union would report every runtime job as an orphan the moment the unit list
    is split, which batching makes the normal case.

    A FAILED batch contributes nothing: it has no `M_i`, and treating it as
    `M_i = {}` (i.e. `O_i = A`) leaves the intersection unchanged only by luck
    and, worse, treating it as `O_i = {}` would erase every real orphan. Callers
    must pass successful batches only.
    """
    if not per_batch:
        return ()
    merged = set(per_batch[0])
    for batch in per_batch[1:]:
        merged &= set(batch)
    return tuple(sorted(merged))


def _apply_redaction_verification(
    evidence: RuntimeUnitEvidence,
) -> tuple[RuntimeUnitEvidence, list[RuntimeRedactionFlag]]:
    """Drop failure headlines that still look identifying; keep the unit.

    Applied at FETCH time, before anything is cached or rendered: the persisted
    payload must not carry text the render path would have refused, or a resume
    reading that cache reintroduces exactly what was filtered.
    """
    kept: list[tuple[str, str, int]] = []
    kept_insights: list[RuntimeIncidentInsight] = []
    flags: list[RuntimeRedactionFlag] = []
    for failed_at, summary, occurrences in evidence.recent_failures:
        violation = redaction_violation(summary)
        if violation is None:
            kept.append((failed_at, summary, occurrences))
            continue
        kind, excerpt = violation
        flags.append(
            RuntimeRedactionFlag(unit_name=evidence.unit_name, kind=kind, excerpt=excerpt)
        )
    for insight in evidence.incident_insights:
        violation = next(
            (
                found
                for value in (
                    insight.failed_component,
                    insight.termination_component,
                    insight.error_code,
                    insight.exception_class,
                    insight.causal_message,
                    insight.root_cause_candidate,
                    *insight.next_checks,
                )
                if value and (found := redaction_violation(value)) is not None
            ),
            None,
        )
        if violation is None:
            kept_insights.append(insight)
            continue
        kind, excerpt = violation
        flags.append(
            RuntimeRedactionFlag(unit_name=evidence.unit_name, kind=kind, excerpt=excerpt)
        )
    if not flags:
        return evidence, []
    return replace(
        evidence,
        recent_failures=tuple(kept),
        incident_insights=tuple(kept_insights),
    ), flags


async def fetch_runtime_evidence(
    project_id: str,
    units: Sequence[Any],
    *,
    live_evidence: bool = True,
) -> RuntimeEvidenceStore:
    """Fetch runtime evidence for a project. ALWAYS returns a store.

    Never `None` and never raises: absence is a mode, not an exception. A caller
    that still checks `is None` keeps working (the check simply never fires) and
    gets a store whose `mode` says what happened -- which is the whole point of
    R7, since a `None` return is indistinguishable from a lane that was never
    wired at all.

    `units` accepts `RuntimeUnitRequest`, `(unit_name, display_name)` pairs,
    `UnitFact`-shaped objects, or bare strings. Both identifier keys are sent
    (A1) and the response is reconciled back onto the manifest unit name.
    """
    requests = as_unit_requests(units)
    window_days = config.runtime_evidence_window_days

    if not live_evidence or not config.runtime_evidence_enabled:
        return RuntimeEvidenceStore(
            window_days=window_days,
            mode=RuntimeMode.OPTED_OUT,
            requested_units=len(requests),
            unmatched_units=tuple(request.unit_name for request in requests),
        )
    if not requests:
        return RuntimeEvidenceStore(
            window_days=window_days, mode=RuntimeMode.UNAVAILABLE, requested_units=0
        )

    # Alias -> the manifest units that claim it. One alias can back several
    # units in a composite scan where two sub-projects share a display name;
    # that is a recorded known limitation, not something to resolve here.
    alias_to_units: dict[str, list[str]] = {}
    aliases: list[str] = []
    for request in requests:
        for alias in request.aliases:
            if alias not in alias_to_units:
                alias_to_units[alias] = []
                aliases.append(alias)
            alias_to_units[alias].append(request.unit_name)

    base_params: list[tuple[str, str]] = [
        ("project_id", project_id),
        ("window_days", str(window_days)),
        ("max_failures_per_unit", str(config.runtime_evidence_max_failures)),
    ]
    fixed_bytes = len(
        "&".join(f"{key}={quote(value, safe='')}" for key, value in base_params).encode("utf-8")
    )
    batches = plan_batches(aliases, fixed_query_bytes=fixed_bytes)

    headers: dict[str, str] = {}
    if config.ingest_api_key:
        headers["Authorization"] = f"Bearer {config.ingest_api_key}"
    url = f"{config.rag_base_url.rstrip('/')}{DOC_EVIDENCE_PATH}"

    matched: dict[str, list[tuple[str, RuntimeUnitEvidence]]] = {}
    successful_orphan_lists: list[list[str]] = []
    retrieved_aliases: set[str] = set()
    failed_batches = 0
    observed_window: int | None = None
    read_environments: tuple[str, ...] = ()

    async with httpx.AsyncClient(timeout=config.runtime_evidence_timeout_s) as client:
        for batch in batches:
            params = list(base_params) + [("units", alias) for alias in batch]
            try:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                payload = response.json()
            except Exception:
                # Per-batch isolation. One 414, 422 or timeout loses that slice
                # of the estate, not the whole project's runtime evidence.
                failed_batches += 1
                logger.warning(
                    "runtime_evidence: batch of %d unit key(s) failed for project %s; "
                    "continuing with the remaining batches",
                    len(batch), project_id, exc_info=True,
                )
                continue
            retrieved_aliases.update(batch)
            if observed_window is None:
                observed_window = int(payload.get("window_days") or window_days)
            if not read_environments:
                scope_payload = payload.get("read_scope")
                if isinstance(scope_payload, dict):
                    read_environments = tuple(
                        str(name)
                        for name in scope_payload.get("environment_names") or []
                        if str(name).strip()
                    )
            successful_orphan_lists.append(
                [str(name) for name in payload.get("orphan_runtime_jobs") or []]
            )
            for item in payload.get("units") or []:
                alias = str(item.get("unit_name") or "")
                if not alias or alias not in alias_to_units:
                    continue
                for unit_name in alias_to_units[alias]:
                    kind = (
                        AliasKind.UNIT_NAME.value
                        if alias == unit_name
                        else AliasKind.DISPLAY_NAME.value
                    )
                    evidence = RuntimeUnitEvidence.from_payload(
                        {**item, "unit_name": unit_name, "matched_alias": alias, "alias_kind": kind}
                    )
                    matched.setdefault(unit_name, []).append((kind, evidence))

    if failed_batches and failed_batches == len(batches):
        return RuntimeEvidenceStore(
            window_days=window_days,
            mode=RuntimeMode.UNAVAILABLE,
            requested_units=len(requests),
            unretrieved_units=tuple(request.unit_name for request in requests),
            batches=len(batches),
            failed_batches=failed_batches,
            partial=True,
        )

    by_unit: dict[str, RuntimeUnitEvidence] = {}
    alias_conflicts: list[str] = []
    flags: list[RuntimeRedactionFlag] = []
    for unit_name, candidates in matched.items():
        # Manifest identity wins over display name when both resolved: it is the
        # unique key, and a display name can be shared across export roots.
        chosen = next(
            (item for kind, item in candidates if kind == AliasKind.UNIT_NAME.value),
            candidates[0][1],
        )
        if len({item.job_name for _kind, item in candidates}) > 1:
            alias_conflicts.append(unit_name)
        cleaned, unit_flags = _apply_redaction_verification(chosen)
        by_unit[unit_name] = cleaned
        flags.extend(unit_flags)

    unmatched: list[str] = []
    unretrieved: list[str] = []
    for request in requests:
        if request.unit_name in by_unit:
            continue
        if any(alias in retrieved_aliases for alias in request.aliases):
            unmatched.append(request.unit_name)
        else:
            unretrieved.append(request.unit_name)

    orphans = _merge_orphans(successful_orphan_lists)
    matched_runtime_jobs = {item.job_name for item in by_unit.values() if item.job_name}
    orphan_total = len(orphans) + len(matched_runtime_jobs)
    orphan_ratio = (len(orphans) / orphan_total) if orphan_total else 0.0
    scope_mismatch = (
        len(orphans) >= config.runtime_evidence_scope_mismatch_min_orphans
        and orphan_ratio >= config.runtime_evidence_scope_mismatch_ratio
    )
    source_key = next(
        (item.source_key for item in by_unit.values() if item.source_key), ""
    )

    store = RuntimeEvidenceStore(
        window_days=observed_window or window_days,
        by_unit=by_unit,
        unmatched_units=tuple(unmatched),
        unretrieved_units=tuple(unretrieved),
        orphan_runtime_jobs=orphans,
        requested_units=len(requests),
        batches=len(batches),
        failed_batches=failed_batches,
        source_key=source_key,
        partial=bool(failed_batches),
        scope_mismatch=scope_mismatch,
        mode=RuntimeMode.DATA,
        redaction_flags=tuple(flags),
        alias_conflicts=tuple(sorted(alias_conflicts)),
        read_environments=read_environments,
    )
    logger.info(
        "runtime_evidence: project %s matched %d/%d units over %d batch(es) "
        "(%d failed); %d orphan runtime job(s); %d unit(s) resolved by display name",
        project_id, len(by_unit), len(requests), len(batches), failed_batches,
        len(orphans),
        sum(1 for item in by_unit.values() if item.alias_kind == AliasKind.DISPLAY_NAME.value),
    )
    if flags:
        logger.warning(
            "runtime_evidence: dropped %d failure headline(s) for project %s that "
            "still carried an identifier shape; see %s",
            len(flags), project_id, RUNTIME_REDACTION_FLAGS_JSON,
        )
    return store


# --- Project rollup (section 3) ----------------------------------------------


@dataclass
class _Table:
    """One rollup table, trimmed by dropping WHOLE rows, never mid-row."""

    key: str
    heading: str
    preamble: tuple[str, ...]
    columns: tuple[str, ...]
    rows: list[str]
    empty_note: str
    trim_rank: int
    notes: tuple[str, ...] = ()
    omitted: int = 0

    def render(self) -> list[str]:
        out = [self.heading, ""]
        out.extend(self.preamble)
        if self.preamble:
            out.append("")
        if not self.rows:
            if self.omitted:
                out.append(
                    f"_All {self.omitted} row(s) omitted: the rollup byte budget was "
                    "reached. The counts above remain complete._"
                )
            else:
                out.append(self.empty_note)
            out.append("")
            return out
        out.append("| " + " | ".join(self.columns) + " |")
        out.append("| " + " | ".join("---" for _ in self.columns) + " |")
        out.extend(self.rows)
        if self.omitted:
            out.append("")
            out.append(
                f"_{self.omitted} further row(s) omitted to fit the rollup byte "
                "budget. Rows are ordered by significance, so the omitted ones "
                "rank lowest; they were not sampled or summarised._"
            )
        out.append("")
        out.extend(self.notes)
        if self.notes:
            out.append("")
        return out


def project_rollup(
    store: RuntimeEvidenceStore | None,
    *,
    mode: RuntimeMode | None = None,
    max_bytes: int | None = None,
    now: datetime | None = None,
    silent_days: int | None = None,
) -> str:
    """Render the project-level runtime rollup. NEVER returns "".

    Eligible for bounded operational/troubleshooting/risk/group-summary
    documents only (the harness owns that routing). Unit documents keep their
    own per-unit section: a project-wide failure census names components owned
    by other units, and a unit-doc writer echoing one is rejected by the
    component-echo fabrication validator as an invention, though the component
    is real. Mapping, lineage and business-rule documents do not receive it.

    Every path renders a mode, including "no store at all". A document that
    receives nothing cannot distinguish "this estate is idle" from "nobody
    asked", and that inference is exactly the failure this lane exists to stop.
    """
    ceiling = max_bytes if max_bytes is not None else config.phase3_runtime_profile_bytes
    effective_mode = mode or (store.mode if store is not None else RuntimeMode.UNAVAILABLE)
    if store is None:
        store = RuntimeEvidenceStore(
            window_days=config.runtime_evidence_window_days, mode=effective_mode
        )
    if effective_mode is not RuntimeMode.DATA:
        return _render_rollup_absent(store, effective_mode)
    return _render_rollup_data(store, ceiling, now, silent_days)


_ROLLUP_TITLE = "# RUNTIME EVIDENCE -- PROJECT ROLLUP"

_ROLLUP_INTRO = (
    "Observed production behaviour for this project, measured from the runtime "
    "mirror and computed deterministically. Interpret these figures; do NOT "
    "recompute, re-rank, estimate, round or extrapolate them, and do not derive "
    "a schedule or a typical duration that is not stated here."
)


def _render_rollup_absent(store: RuntimeEvidenceStore, mode: RuntimeMode) -> str:
    if mode is RuntimeMode.OPTED_OUT:
        headline = (
            "Runtime evidence was switched OFF for this run, so no runtime lookup "
            "was performed."
        )
    else:
        headline = (
            "The runtime source could not be read for this run "
            f"({store.failed_batches} of {store.batches} request batch(es) failed)."
            if store.batches
            else "The runtime source could not be read for this run."
        )
    return "\n".join(
        [
            _ROLLUP_TITLE,
            "",
            f"**Status: {mode.value}.** {headline}",
            "",
            f"Units in scope for this run: {store.requested_units}.",
            "",
            "Consequences for this document, which must be respected:",
            "",
            "- Do NOT state or imply how often anything runs, how long it takes, "
            "how reliable it is, or whether it is still in use.",
            "- Do NOT read this absence as evidence that jobs are idle, dormant "
            "or dead. Nothing was measured.",
            "- If the document discusses operational behaviour, say plainly that "
            "no runtime evidence was available for this generation.",
            "",
        ]
    )


def _render_rollup_data(
    store: RuntimeEvidenceStore,
    max_bytes: int,
    now: datetime | None,
    silent_days: int | None,
) -> str:
    threshold = (
        silent_days if silent_days is not None else config.runtime_evidence_silent_days
    )
    header = _rollup_header(store, threshold)
    tables = [
        _reliability_table(store, now),
        _failure_census_table(store),
        _log_quality_table(store),
        _incident_census_table(store),
        _schedule_table(store, now),
        _silent_table(store, now, threshold),
        _unobserved_table(store),
        _orphan_table(store),
    ]

    def assemble() -> str:
        lines = list(header)
        for table in tables:
            lines.extend(table.render())
        return "\n".join(lines).rstrip() + "\n"

    text = assemble()
    if len(text.encode("utf-8")) <= max_bytes:
        return text

    # Over budget: drop WHOLE rows, lowest-significance table first, never a
    # partial row and never a partial table header. A truncated markdown table
    # is read by a model as a complete one.
    droppable = sorted(tables, key=lambda table: table.trim_rank)
    while len(text.encode("utf-8")) > max_bytes:
        victim = next((table for table in droppable if table.rows), None)
        if victim is None:
            break
        victim.rows.pop()
        victim.omitted += 1
        text = assemble()
    return text


def _rollup_header(store: RuntimeEvidenceStore, silent_days: int) -> list[str]:
    matched_jobs = {item.job_name for item in store.by_unit.values() if item.job_name}
    orphan_total = len(store.orphan_runtime_jobs) + len(matched_jobs)
    orphan_ratio = (len(store.orphan_runtime_jobs) / orphan_total) if orphan_total else 0.0
    match_ratio = (
        len(store.by_unit) / store.requested_units if store.requested_units else 0.0
    )
    multi_env = sorted(
        {
            env
            for item in store.by_unit.values()
            if len(item.environments) > 1
            for env in item.environments
        }
    )
    lines = [
        _ROLLUP_TITLE,
        "",
        _ROLLUP_INTRO,
        "",
        "## 1. Coverage and confidence",
        "",
        "| Fact | Value |",
        "| --- | --- |",
        f"| Scanned units in scope | {store.requested_units} |",
        f"| Units with observed runtime data | {len(store.by_unit)} "
        f"({match_ratio * 100:.1f}% of scanned units) |",
        f"| Distinct runtime jobs matched | {len(matched_jobs)} |",
        f"| Runtime jobs with no scanned unit | {len(store.orphan_runtime_jobs)} "
        f"({orphan_ratio * 100:.1f}% of the runtime job list) |",
        f"| Observation window | last {store.window_days} days |",
        f"| Environments read | {', '.join(store.read_environments)} |"
        if store.read_environments
        else "| Environments read | every mirrored environment |",
        f"| Runtime source | {store.source_key or 'unknown'} |",
        f"| Request batches | {store.batches} ({store.failed_batches} failed) |",
    ]
    if store.unretrieved_units:
        lines.append(f"| Units with no lookup result | {len(store.unretrieved_units)} |")
    lines.append("")
    if store.partial:
        lines.append(
            f"> **Partial retrieval.** {store.failed_batches} of {store.batches} "
            "request batches failed, so this rollup describes only the units that "
            "were successfully looked up. Units listed as having no lookup result "
            "are UNKNOWN, not idle."
        )
        lines.append("")
    # R9: the coverage caveat is the single most important sentence here. Low
    # coverage is the normal, correct state for a tRunJob-heavy estate; a reader
    # who does not know that will read 5-of-374 as an estate that stopped.
    lines.append(
        "> **How to read coverage.** The runtime source lists SCHEDULED TASKS, "
        "not the artifact inventory. A unit invoked by a parent job (tRunJob) or "
        "embedded as a joblet executes constantly and never appears as a task of "
        "its own. Low coverage is therefore the expected shape of a scan that "
        "includes child jobs, and a unit's absence from the runtime source is "
        "NOT evidence that it does not run."
    )
    lines.append("")
    if store.orphan_runtime_jobs:
        # A3: state the asymmetry, do not accuse. A match-ratio rule flags a
        # correct scan (werkorder 5/374) and misses the one confirmed
        # mis-binding (alitalend 1/10 with 91/92 orphans), and no available
        # signal separates "the scan covers part of the estate" from "the
        # project binding is wrong". So both readings are offered, neither
        # asserted.
        emphasis = "**" if store.scope_mismatch else ""
        lines.append(
            f"> {emphasis}Scan/runtime asymmetry.{emphasis} "
            f"{len(store.by_unit)} of {store.requested_units} scanned units have "
            f"runtime data ({match_ratio * 100:.1f}%), and "
            f"{len(store.orphan_runtime_jobs)} of {orphan_total} runtime jobs have "
            f"no scanned counterpart ({orphan_ratio * 100:.1f}%). Two readings fit "
            "these numbers equally well: this scan may cover a deliberate subset "
            "of a larger deployed estate, or this project's scan and runtime "
            "source may describe different estates entirely. Do not assert either "
            "one; if the document discusses coverage, state both figures."
        )
        lines.append("")
    if multi_env:
        lines.append(
            "> **Environments are blended.** Runtime jobs are identified per "
            "environment, but figures for a unit seen in more than one aggregate "
            f"all of them ({', '.join(multi_env)}). No figure in this rollup may "
            "be attributed to a single environment."
        )
        lines.append("")
    shared = _shared_runtime_jobs(store)
    if shared:
        # Verified in werkorder: two scanned units (two export revisions of the
        # same Studio job) carry the same display name, so both resolve to
        # runtime job `Maximo2Quintiq_Workorder_Route`. Both rows are correct
        # individually and adding them together is not -- say so, or a summary
        # doubles the estate's execution count.
        lines.append(
            "> **Shared runtime jobs.** These scanned units share one runtime "
            "job with another unit, because the scan holds more than one "
            "revision under the same name: "
            + "; ".join(
                f"{job} <- {', '.join(units)}" for job, units in shared[:5]
            )
            + ". Their figures are the SAME executions reported twice, not "
            "additional ones. Never sum them."
        )
        lines.append("")
    if store.alias_conflicts:
        lines.append(
            f"> {len(store.alias_conflicts)} unit(s) matched two different runtime "
            "jobs under their two identifiers; the job matching the unit's own "
            "identity was used. Affected: "
            f"{', '.join(store.alias_conflicts[:10])}."
        )
        lines.append("")
    return lines


def _shared_runtime_jobs(store: RuntimeEvidenceStore) -> list[tuple[str, list[str]]]:
    """Runtime jobs claimed by more than one scanned unit, sorted by job name."""
    by_job: dict[str, list[str]] = {}
    for unit_name, item in sorted(store.by_unit.items()):
        if item.job_name:
            by_job.setdefault(item.job_name, []).append(unit_name)
    return [(job, units) for job, units in sorted(by_job.items()) if len(units) > 1]


def _reliability_table(store: RuntimeEvidenceStore, now: datetime | None) -> _Table:
    ordered = sorted(
        store.by_unit.values(),
        key=lambda item: (-item.failure_rate, -item.total_runs, item.unit_name),
    )
    rows = []
    for item in ordered:
        silent = item.days_silent(now)
        rows.append(
            f"| {item.unit_name} | {item.total_runs} | {item.failure_rate * 100:.1f}% | "
            f"{item.current_failure_streak} | "
            f"{', '.join(item.environments) or 'unknown'} | "
            f"{'n/a' if silent is None else silent} |"
        )
    return _Table(
        key="reliability",
        heading="## 2. Reliability ranking",
        preamble=(
            "Matched units ordered by failure rate, then by run count. "
            "Percentages are measured over the window, not estimated.",
        ),
        columns=("Unit", "Runs", "Failure %", "Current failure streak", "Environments", "Days silent"),
        rows=rows,
        empty_note="_No unit in this project has observed runtime data._",
        trim_rank=40,
    )


def _failure_census_table(store: RuntimeEvidenceStore) -> _Table:
    census: dict[str, dict[str, Any]] = {}
    for item in store.by_unit.values():
        for failed_at, summary, occurrences in item.recent_failures:
            if not summary:
                continue
            entry = census.setdefault(
                summary, {"occurrences": 0, "units": set(), "latest": ""}
            )
            entry["occurrences"] += occurrences
            entry["units"].add(item.unit_name)
            if failed_at > entry["latest"]:
                entry["latest"] = failed_at
    total = sum(int(entry["occurrences"]) for entry in census.values())
    ordered = sorted(
        census.items(),
        key=lambda pair: (-int(pair[1]["occurrences"]), -len(pair[1]["units"]), pair[0]),
    )
    rows = []
    for summary, entry in ordered:
        share = (int(entry["occurrences"]) / total * 100) if total else 0.0
        rows.append(
            f"| {summary} | {entry['occurrences']} | {share:.1f}% | "
            f"{len(entry['units'])} | {_date(str(entry['latest']))} |"
        )
    return _Table(
        key="failures",
        heading="## 3. Failure-mode census",
        preamble=(
            f"Every distinct failure mode observed across the project, "
            f"{total} failed execution(s) in total, ordered by how many "
            "executions each mode accounts for.",
        ),
        columns=("Failure mode", "Executions", "Share", "Units affected", "Most recent"),
        rows=rows,
        empty_note="_No failures were observed for any matched unit in the window._",
        trim_rank=60,
        notes=(
            "> Failure text is provider-reported and redacted. Quote it as an "
            "observed symptom; do not infer a root cause it does not state. The "
            "census covers the failure modes returned per unit (capped per unit "
            "by the runtime query), so a share is a share of the census above, "
            "not of every failure row ever recorded.",
        ),
    )


def _unique_runtime_evidence(store: RuntimeEvidenceStore) -> list[RuntimeUnitEvidence]:
    """One row per runtime job, so scan revisions do not double-count executions."""
    by_job: dict[str, RuntimeUnitEvidence] = {}
    for item in sorted(store.by_unit.values(), key=lambda value: value.unit_name):
        by_job.setdefault(item.job_name or item.unit_name, item)
    return list(by_job.values())


def _log_quality_table(store: RuntimeEvidenceStore) -> _Table:
    rows: list[str] = []
    for item in sorted(_unique_runtime_evidence(store), key=lambda value: value.unit_name):
        quality = item.log_quality
        if not quality.failed_executions:
            continue
        rows.append(
            f"| {item.unit_name} | {quality.failed_executions} | "
            f"{quality.executions_with_logs} | {quality.diagnosed_executions} | "
            f"{quality.capture_ceiling_reached} |"
        )
    return _Table(
        key="log-quality",
        heading="## 3b. Diagnostic evidence coverage",
        preamble=(
            "Coverage of failed executions by captured logs and by useful, "
            "non-generic diagnoses. Counts are metadata; raw logs are not included.",
        ),
        columns=("Unit", "Failures", "With logs", "Diagnosed", "At capture ceiling"),
        rows=rows,
        empty_note="_No matched unit failed in the observation window._",
        trim_rank=55,
    )


def _incident_census_table(store: RuntimeEvidenceStore) -> _Table:
    census: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in _unique_runtime_evidence(store):
        for insight in item.incident_insights:
            key = (
                insight.pattern_id,
                insight.failed_component,
                insight.termination_component,
                insight.root_cause_candidate,
            )
            entry = census.setdefault(
                key,
                {
                    "occurrences": 0,
                    "units": set(),
                    "latest": "",
                    "confidence": insight.confidence,
                    "next_checks": insight.next_checks,
                },
            )
            entry["occurrences"] += insight.occurrences
            entry["units"].add(item.unit_name)
            if insight.most_recent_at > entry["latest"]:
                entry["latest"] = insight.most_recent_at
    ordered = sorted(
        census.items(),
        key=lambda pair: (-int(pair[1]["occurrences"]), pair[0][0], pair[0][1]),
    )
    rows = [
        f"| {_md(pattern)} | {entry['occurrences']} | {len(entry['units'])} | "
        f"{entry['confidence']} | {_date(str(entry['latest']))} | "
        f"{_md(origin or 'n/a')} | {_md(termination or 'n/a')} | "
        f"{_md(candidate or 'n/a')} | "
        f"{_md('; '.join(entry['next_checks']) or 'n/a')} |"
        for (pattern, origin, termination, candidate), entry in ordered
    ]
    return _Table(
        key="incidents",
        heading="## 3c. Evidence-backed incident census",
        preamble=(
            "Recurring high/medium-confidence incident patterns derived "
            "deterministically from retained logs, bounded to the matched units.",
        ),
        columns=(
            "Pattern",
            "Executions",
            "Units",
            "Confidence",
            "Most recent",
            "Origin component",
            "Termination component",
            "Evidence-backed interpretation",
            "Suggested checks",
        ),
        rows=rows,
        empty_note="_No useful non-generic diagnosis was available for matched units._",
        trim_rank=65,
        notes=(
            "> Preserve the confidence qualifier and causal ordering. A termination "
            "component reports or ends the failed run; it is not necessarily the "
            "origin. Suggested checks belong in operational/troubleshooting guidance. "
            "These interpretations are not proof of a deeper business root cause.",
        ),
    )


def _schedule_table(store: RuntimeEvidenceStore, now: datetime | None) -> _Table:
    ordered = sorted(store.by_unit.values(), key=lambda item: item.unit_name)
    rows = []
    for item in ordered:
        silent = item.days_silent(now)
        rows.append(
            f"| {item.unit_name} | {item.cadence().label} | "
            f"{_date(item.first_run_at)} to {_date(item.last_run_at)} | "
            f"{'n/a' if silent is None else silent} | "
            f"{', '.join(item.environments) or 'unknown'} |"
        )
    return _Table(
        key="schedule",
        heading="## 4. Observed schedule",
        preamble=(
            "Cadence is classified deterministically from observed start times, "
            "never averaged: a mean gap lands on a number nobody configured.",
        ),
        columns=("Unit", "Observed cadence", "Observed window", "Days silent", "Environments"),
        rows=rows,
        empty_note="_No unit in this project has observed runtime data._",
        trim_rank=30,
    )


def _silent_table(
    store: RuntimeEvidenceStore, now: datetime | None, silent_days: int
) -> _Table:
    rows = []
    for item in sorted(store.by_unit.values(), key=lambda unit: unit.unit_name):
        silent = item.days_silent(now)
        if silent is None or silent < silent_days:
            continue
        rows.append(
            f"| {item.unit_name} | {_date(item.last_run_at)} | {silent} | "
            f"{item.total_runs} |"
        )
    return _Table(
        key="silent",
        heading=f"## 5a. Silent units (runtime data, nothing in {silent_days}+ days)",
        preamble=(
            "These units HAVE run inside the window and then stopped. That is a "
            "measured change in behaviour and is worth a sentence in the document.",
        ),
        columns=("Unit", "Last run", "Days silent", "Runs in window"),
        rows=rows,
        empty_note=(
            f"_Every matched unit ran within the last {silent_days} days._"
        ),
        trim_rank=50,
    )


def _unobserved_table(store: RuntimeEvidenceStore) -> _Table:
    rows = [f"| {name} |" for name in sorted(store.unmatched_units)]
    return _Table(
        key="unobserved",
        heading="## 5b. Unobserved units (scanned, not scheduled as a runtime task)",
        preamble=(
            "These units were scanned and the runtime source holds no task for "
            "them. This is the EXPECTED state for a child job invoked via tRunJob "
            "and for a joblet.",
        ),
        columns=("Unit",),
        rows=rows,
        empty_note="_Every scanned unit in scope has runtime data._",
        # Dropped FIRST under budget pressure, and by a wide margin the largest
        # table (werkorder: 423 rows). Its own note says it is not actionable,
        # and its full count survives in section 1 either way -- whereas the
        # orphan NAMES below are the mis-binding tripwire and cost far less.
        trim_rank=10,
        notes=(
            "> This list is NOT a dead-code list and must never be presented as "
            "one. A unit is a dead-code CANDIDATE only if it appears here AND no "
            "tRunJob edge references it; that second condition is not evaluated "
            "in this rollup. Stating that these units are unused would be a "
            "confident, wrong claim about most of them.",
        ),
    )


def _orphan_table(store: RuntimeEvidenceStore) -> _Table:
    rows = [f"| {name} |" for name in store.orphan_runtime_jobs]
    return _Table(
        key="orphans",
        heading="## 6. Orphan runtime jobs (running, no scanned code)",
        preamble=(
            "Jobs the runtime source executes in this project that match no "
            "scanned unit. Names only; no execution detail is available for code "
            "this scan does not contain.",
        ),
        columns=("Runtime job",),
        rows=rows,
        empty_note="_Every runtime job in this project matched a scanned unit._",
        trim_rank=20,
        notes=(
            "> Treat this as a coverage fact about the scan, not as a defect in "
            "any documented unit. See the scan/runtime asymmetry note in section 1.",
        ),
    )


def unit_names_for_request(units: Iterable[Any]) -> tuple[str, ...]:
    """Every alias that would be sent for `units`, in request order.

    Exposed for the dry-run harness and for tests that need to prove the union
    of both identifier keys is what goes on the wire.
    """
    aliases: list[str] = []
    seen: set[str] = set()
    for request in as_unit_requests(list(units)):
        for alias in request.aliases:
            if alias not in seen:
                seen.add(alias)
                aliases.append(alias)
    return tuple(aliases)


__all__ = [
    "AliasKind",
    "CadenceClassification",
    "DOC_EVIDENCE_PATH",
    "RUNTIME_CACHE_JSON",
    "RUNTIME_COVERAGE_FILENAME",
    "RUNTIME_COVERAGE_JSON",
    "RUNTIME_COVERAGE_SCHEMA_VERSION",
    "RUNTIME_PROFILE_MD",
    "RUNTIME_REDACTION_FLAGS_FILENAME",
    "RUNTIME_REDACTION_FLAGS_JSON",
    "RuntimeEvidenceStore",
    "RuntimeMode",
    "RuntimeRedactionFlag",
    "RuntimeUnitEvidence",
    "RuntimeUnitRequest",
    "SERVER_MAX_UNITS_PER_CALL",
    "as_unit_requests",
    "classify_cadence",
    "fetch_runtime_evidence",
    "plan_batches",
    "project_rollup",
    "redaction_violation",
    "unit_names_for_request",
]
