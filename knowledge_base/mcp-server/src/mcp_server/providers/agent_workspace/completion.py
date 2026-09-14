"""The completion handshake: what the agent CLAIMS, read back and reconciled.

Plan section 7, in one sentence: **a provider process ending normally means the
session stopped, not that the goal was completed.** Run 250 exited zero after
opening six of a hundred and forty-two designs and writing five short dossiers,
and the runner read that exit as success. Nothing in a process exit code is a
statement about an investigation, so the agent has to make that statement
itself, in a file, and Nexus has to check it against the workspace.

Three states, and keeping them apart is the whole job of this module:

* **Absent** -- ``present=False``, no errors. The NORMAL unfinished case. The
  session stopped without the agent claiming anything, which is resumable
  incomplete work and not a defect in the agent's honesty. An operator reading
  the log must be able to tell this apart from the next two at a glance, which
  is why absence never produces an ``errors`` entry.
* **Malformed** -- present and unreadable. The agent tried to claim something
  and the claim cannot be read. Also incomplete, but for a different reason and
  with a different fix.
* **Present and reconciled** -- a readable claim, compared against the state
  recomputed from the ledger, the integration model and the workspace.

**A discrepancy is not a lie.** The receipt records what the agent knew at the
moment it wrote the file; a restart, a later classification or a repaired model
changes the truth underneath it. So :func:`reconcile` words every difference as
a disagreement between two sources with both numbers named, never as an
accusation -- and the blocking decision in ``accountability.py`` is taken on the
RECOMPUTED state, never on the claim. The claim is the handshake; the ledger and
the model are the evidence.

**This module never raises.** It is called on the failure path as well as the
success path, and a completion checker that throws on a malformed receipt turns
"the agent wrote bad JSON" into "the run crashed".

The wire format the agent writes to ``notes/completion.json``::

    {
      "scope_mode": "whole_estate",
      "coverage_digest": "<the ledger digest the claim is bound to>",
      "candidate_counts": {"material": 12, "utility": 30, "excluded": 4},
      "unreviewed_count": 0,
      "unresolved_count": 0,
      "integration_count": 7,
      "journey_count": 19,
      "document_paths": ["INTEGRATION__orders.md"],
      "limitations": ["runtime evidence was not connected"],
      "complete": true,
      "notes": "free text"
    }

Three keys are REQUIRED -- ``unreviewed_count``, ``unresolved_count`` and
``complete``. Those are the scope assertion plus the claim itself; a receipt
without them is not a claim about this run's scope. Every other key defaults
when absent, because the plan's risk list calls out "classification becomes an
escape hatch" and not "the agent forgot a field": a thin receipt is still a
claim, and the gate recomputes the numbers anyway.

``coverage_digest`` is OPTIONAL, and was briefly required. It could not be:
:func:`ledger.LedgerReport.digest` is a sha256 over the classification pairs,
computed by Nexus, and there is no artifact holding it while the session is
running -- ``write_summary`` is called by the runner AFTER the agent stops. So
a required digest could only ever be satisfied by a value the agent invented,
which would either be empty (meaningless) or wrong (an advisory discrepancy on
every real run, which is how an operator learns to ignore that warning). It is
reconciled when supplied and ignored when not. Nothing is lost by that: the
binding it was meant to provide is already stronger on the other side, because
the gate re-reads the ledger from the digest-protected evidence rather than
trusting any number in this file.

``notes`` and ``limitations`` are agent PROSE. They are bounded on the way in
and they never reach a client-facing document -- the only artifact this lane
writes is under ``_audit/``, which ``publish._is_visible`` refuses.

Deterministic Python. No model, no network, no config.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp_server.providers.agent_workspace import contract

if TYPE_CHECKING:  # typing only: this module is HANDED a ledger and a model,
    # it never loads either one, and ``ledger`` is owned by another slice.
    from mcp_server.providers.agent_workspace.integration_model import IntegrationKnowledgeBase
    from mcp_server.providers.agent_workspace.ledger import LedgerReport

logger = logging.getLogger(__name__)

#: The keys without which the file is not a claim about this run. See the
#: module docstring for why ``coverage_digest`` is NOT among them: the agent
#: has no way to obtain it during the session, so requiring it would reject
#: every honest receipt.
REQUIRED_KEYS: tuple[str, ...] = (
    "unreviewed_count",
    "unresolved_count",
    "complete",
)

#: A receipt is a handful of counts. Anything larger is not a receipt, and
#: reading it into memory before deciding that is how a control file becomes a
#: denial of service.
_MAX_RECEIPT_BYTES = 256 * 1024

# Bounds on agent-authored text. Every one of these caps a field that the agent
# writes freely and that Nexus then persists or logs.
_MAX_NOTES_CHARS = 2_000
_MAX_LIMITATIONS = 20
_MAX_LIMITATION_CHARS = 300
_MAX_DOCUMENT_PATHS = 200
_MAX_PATH_CHARS = 300
_MAX_COUNT_KEYS = 40
_MAX_SCOPE_MODE_CHARS = 40
_MAX_DIGEST_CHARS = 128
_MAX_ERRORS = 20
_MAX_DISCREPANCIES = 20

#: Disposition-shaped keys only. ``candidate_counts`` is a dict the agent
#: invents the keys of, and those keys are echoed into reconciliation messages
#: that reach a log; a key that is a sentence is not a disposition.
_COUNT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")

#: Accepted spellings of the assertion. JSON booleans are what the instructions
#: ask for; the four strings are the shapes a language model actually produces
#: often enough that refusing them would report "malformed" for a run that
#: plainly said what it meant.
_TRUE_WORDS = frozenset({"true", "yes"})
_FALSE_WORDS = frozenset({"false", "no"})


@dataclass(frozen=True)
class CompletionClaim:
    """What the agent asserted, normalised and bounded. Never authoritative."""

    scope_mode: str
    coverage_digest: str
    candidate_counts: dict[str, int]
    unreviewed_count: int
    unresolved_count: int
    integration_count: int
    journey_count: int
    document_paths: tuple[str, ...]
    limitations: tuple[str, ...]
    complete: bool
    notes: str

    def to_dict(self) -> dict:
        return {
            "scope_mode": self.scope_mode,
            "coverage_digest": self.coverage_digest,
            "candidate_counts": dict(sorted(self.candidate_counts.items())),
            "unreviewed_count": self.unreviewed_count,
            "unresolved_count": self.unresolved_count,
            "integration_count": self.integration_count,
            "journey_count": self.journey_count,
            "document_paths": list(self.document_paths),
            "limitations": list(self.limitations),
            "complete": self.complete,
        }


@dataclass(frozen=True)
class ReceiptReport:
    """The receipt as read, and the two ways it can be wrong.

    ``errors`` and ``discrepancies`` are deliberately separate lists. An error
    is a defect in the FILE -- it cannot be read, so nothing it says counts. A
    discrepancy is a disagreement between two sources that were both correct
    when they were written. Merging them would make "the agent wrote invalid
    JSON" and "the agent counted before the last restart" the same finding.
    """

    present: bool
    claim: CompletionClaim | None = None
    #: The receipt itself is malformed.
    errors: tuple[str, ...] = ()
    #: The claim disagrees with recomputed truth.
    discrepancies: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        """Present, parsed, and claiming the goal is complete."""

        return self.present and not self.errors and self.claim is not None and self.claim.complete

    def to_dict(self) -> dict:
        return {
            "present": self.present,
            "usable": self.usable,
            "claim": self.claim.to_dict() if self.claim is not None else None,
            "errors": list(self.errors),
            "discrepancies": list(self.discrepancies),
        }

    def log_line(self) -> str:
        if not self.present:
            return (
                "completion receipt: absent -- the session stopped without the agent "
                "claiming the goal was met"
            )
        if self.errors:
            return f"completion receipt: unreadable ({len(self.errors)} defect(s))"
        claim = self.claim
        assertion = "complete" if claim is not None and claim.complete else "not complete"
        return (
            f"completion receipt: the agent asserts {assertion}"
            f" ({len(self.discrepancies)} disagreement(s) with the recomputed state)"
        )


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def read_receipt(staging_dir: Path) -> ReceiptReport:
    """Read ``notes/completion.json``. Absence is not an error.

    Never raises. Every failure -- unreadable file, invalid JSON, a non-object
    at the root, a present-but-uncoercible field -- becomes an ``errors`` entry,
    because this is called on the failure path too.
    """

    path = Path(staging_dir) / contract.COMPLETION_RECEIPT
    try:
        if not path.is_file():
            # THE NORMAL UNFINISHED CASE. No error, deliberately.
            return ReceiptReport(present=False)
        size = path.stat().st_size
    except OSError as exc:
        return ReceiptReport(
            present=True, errors=(f"it could not be opened ({type(exc).__name__})",)
        )

    if size > _MAX_RECEIPT_BYTES:
        return ReceiptReport(
            present=True,
            errors=(
                f"it is {size} bytes, above the {_MAX_RECEIPT_BYTES}-byte ceiling for a receipt",
            ),
        )

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return ReceiptReport(present=True, errors=(f"it could not be read ({type(exc).__name__})",))

    if not text.strip():
        return ReceiptReport(present=True, errors=("it is empty",))

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        # Position, never the offending text: this message reaches a log, and
        # the receipt sits beside an investigation's notes.
        return ReceiptReport(
            present=True,
            errors=(f"it is not valid JSON (line {exc.lineno}, column {exc.colno})",),
        )

    if not isinstance(payload, dict):
        return ReceiptReport(present=True, errors=("it is not a JSON object",))

    errors: list[str] = []
    for key in REQUIRED_KEYS:
        if key not in payload:
            errors.append(f"it does not state {key!r}")

    claim = _claim_from(payload, errors)
    if errors:
        # A claim built from a file with defects is not a claim. Keeping the
        # partial object would let a caller read `claim.complete` off a receipt
        # that never said so.
        return ReceiptReport(present=True, errors=tuple(errors[:_MAX_ERRORS]))
    return ReceiptReport(present=True, claim=claim)


def _claim_from(payload: dict[str, Any], errors: list[str]) -> CompletionClaim | None:
    """Coerce every field, appending to ``errors`` for anything uncoercible.

    Absent optional fields default. A field that is PRESENT and cannot be read
    is an error: the agent meant to say something and Nexus cannot tell what.
    """

    complete = _as_bool(payload.get("complete"), "complete", errors)
    return CompletionClaim(
        scope_mode=_as_text(payload.get("scope_mode"), "scope_mode", _MAX_SCOPE_MODE_CHARS, errors),
        coverage_digest=_as_text(
            payload.get("coverage_digest"), "coverage_digest", _MAX_DIGEST_CHARS, errors
        ),
        candidate_counts=_as_counts(payload.get("candidate_counts"), errors),
        unreviewed_count=_as_int(payload.get("unreviewed_count"), "unreviewed_count", errors),
        unresolved_count=_as_int(payload.get("unresolved_count"), "unresolved_count", errors),
        integration_count=_as_int(payload.get("integration_count"), "integration_count", errors),
        journey_count=_as_int(payload.get("journey_count"), "journey_count", errors),
        document_paths=_as_strings(
            payload.get("document_paths"),
            "document_paths",
            _MAX_DOCUMENT_PATHS,
            _MAX_PATH_CHARS,
            errors,
        ),
        limitations=_as_strings(
            payload.get("limitations"),
            "limitations",
            _MAX_LIMITATIONS,
            _MAX_LIMITATION_CHARS,
            errors,
        ),
        complete=complete,
        notes=_as_text(payload.get("notes"), "notes", _MAX_NOTES_CHARS, errors),
    )


def _as_bool(value: Any, field: str, errors: list[str]) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    errors.append(f"{field!r} is not a true/false assertion")
    return False


def _as_int(value: Any, field: str, errors: list[str]) -> int:
    if value is None:
        return 0
    # `bool` is an `int` in Python and would silently read as 0 or 1. A
    # negative is uncoercible rather than clamped: reading -1 as 0 turns a
    # defective receipt into a receipt that agrees with an empty ledger.
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    errors.append(f"{field!r} is not a count")
    return 0


def _as_text(value: Any, field: str, limit: int, errors: list[str]) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        errors.append(f"{field!r} is not text")
        return ""
    return value.strip()[:limit]


def _as_strings(
    value: Any, field: str, max_items: int, max_chars: int, errors: list[str]
) -> tuple[str, ...]:
    if value is None:
        return ()
    # A single string where a list belongs is the commonest shape mistake and
    # loses nothing by being accepted.
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        errors.append(f"{field!r} is not a list")
        return ()
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()[:max_chars]
        if text and text not in cleaned:
            cleaned.append(text)
        if len(cleaned) >= max_items:
            break
    return tuple(cleaned)


def _as_counts(value: Any, errors: list[str]) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append("'candidate_counts' is not an object")
        return {}
    counts: dict[str, int] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not _COUNT_KEY_RE.match(key):
            continue
        # An unreadable VALUE drops that key rather than failing the receipt:
        # this breakdown is advisory reconciliation material, and the gate
        # counts the ledger. Never clamped -- see ``_as_int``.
        if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
            counts[key] = raw
        elif isinstance(raw, str) and raw.strip().isdigit():
            counts[key] = int(raw.strip())
        if len(counts) >= _MAX_COUNT_KEYS:
            break
    return counts


# --------------------------------------------------------------------------
# Reconciliation
#
# Every message here names BOTH numbers and attributes each to its source. The
# agent counted what it knew when it wrote the file; a restart, a late
# classification or a repaired model moves the truth underneath it. The gate
# blocks on the recomputed side, so a discrepancy is information for an
# operator, not a verdict on the agent.
# --------------------------------------------------------------------------


def reconcile(
    report: ReceiptReport,
    *,
    ledger: LedgerReport,
    knowledge: IntegrationKnowledgeBase | None,
    scope_mode: str,
) -> ReceiptReport:
    """Return ``report`` with ``discrepancies`` filled in.

    A no-op when there is nothing to reconcile: an absent or malformed receipt
    makes no claims, and inventing disagreements with a file that could not be
    read would bury the one finding that matters.
    """

    claim = report.claim
    if not report.present or report.errors or claim is None:
        return report

    found: list[str] = []

    if claim.scope_mode and claim.scope_mode != scope_mode:
        found.append(
            f"the receipt was written for scope mode {claim.scope_mode!r}; "
            f"this run is {scope_mode!r}"
        )

    actual_digest = _ledger_digest(ledger)
    if actual_digest and claim.coverage_digest and claim.coverage_digest != actual_digest:
        # Digests, not contents. The two halves are truncated because a full
        # pair of hashes on a log line is unreadable and the operator only ever
        # needs "same or not".
        found.append(
            "the receipt is bound to coverage digest "
            f"{_short(claim.coverage_digest)}; the ledger now reads {_short(actual_digest)}"
        )

    actual_unreviewed = len(ledger.unreviewed)
    if claim.unreviewed_count != actual_unreviewed:
        found.append(
            f"the receipt states {claim.unreviewed_count} unreviewed candidate(s); "
            f"the ledger now holds {actual_unreviewed}"
        )

    actual_unresolved = len(ledger.unresolved)
    if claim.unresolved_count != actual_unresolved:
        found.append(
            f"the receipt states {claim.unresolved_count} unresolved candidate(s); "
            f"the ledger now holds {actual_unresolved}"
        )

    found.extend(_count_discrepancies(claim.candidate_counts, dict(ledger.counts)))

    if knowledge is not None:
        actual_integrations = len(knowledge.integrations)
        if claim.integration_count != actual_integrations:
            found.append(
                f"the receipt states {claim.integration_count} integration(s); "
                f"the model holds {actual_integrations}"
            )
        actual_journeys = sum(len(item.journeys) for item in knowledge.integrations)
        if claim.journey_count != actual_journeys:
            found.append(
                f"the receipt states {claim.journey_count} journey(s); "
                f"the model holds {actual_journeys}"
            )

    return replace(report, discrepancies=tuple(found[:_MAX_DISCREPANCIES]))


def _count_discrepancies(claimed: dict[str, int], actual: dict[str, int]) -> list[str]:
    """Per-disposition disagreements, bounded and in a stable order.

    Only dispositions one side or the other actually names are compared: an
    absent key means "the agent did not break its count down that way", which
    is not a disagreement about the estate.
    """

    messages: list[str] = []
    for key in sorted(set(claimed) | set(actual)):
        if key not in claimed or key not in actual:
            continue
        if claimed[key] != actual[key]:
            messages.append(
                f"the receipt counts {claimed[key]} {key!r} candidate(s); "
                f"the ledger counts {actual[key]}"
            )
        if len(messages) >= 5:
            messages.append("(further per-disposition differences not listed)")
            break
    return messages


def _ledger_digest(ledger: LedgerReport) -> str:
    """The ledger's digest, or an empty string.

    Guarded because ``digest()`` reads the ledger's own state and this module
    is on the failure path: a receipt check that raises while explaining why a
    run is incomplete replaces a usable diagnosis with a stack trace.
    """

    try:
        return str(ledger.digest() or "")
    except Exception:  # pragma: no cover - defensive, see the docstring
        logger.warning("The coverage ledger could not produce a digest for reconciliation")
        return ""


def _short(digest: str) -> str:
    return digest[:12] if len(digest) > 12 else digest
