"""The publication gate: structural accountability, and it IS a verdict.

``coverage.py`` is a gauge and says so in its own artifact. This module is the
opposite and has to say so just as plainly: a blocking finding here stops the
swap, keeps the draft in staging for a resume, and leaves the previously
published documentation exactly where it was.

Plan section 8 fixes what may be checked here:

    Do not fail because prose uses a slightly different job name or because
    regex cannot find a phrase.

So **every check in this module is id-based or count-based**. It compares a
classification's ``integration_id`` against an integration's ``id``; it
compares a model's ``document_paths`` against the paths the publisher actually
accepted; it counts candidates that carry no disposition. Nothing reads a
document's wording, and nothing compares a scanner identifier to prose. A run
was failed for exactly that -- run 241 was refused over the phrase
"Scanner-Limited", which our own instructions had told it to use as a heading --
and a finding that would need a document's text to evaluate does not belong
here at all.

The blocking set answers one question: *would publishing this be a claim that
is not true?*

* A run with no completion receipt never claimed to be finished (plan section
  7), so publishing it asserts something the agent did not.
* A design that no one classified is estate the run silently dropped.
* A group with no written analysis is a group the documents were written ABOUT
  rather than FROM. Run 252 wrote seven documents off the index having opened
  six of a hundred and forty-two design files; the analysis layer is the step
  that run skipped, and ``analysis.py`` recomputes which groups owe one.
* A ``material`` design naming an integration that does not exist in the model
  is a dangling promise -- the reader is sent somewhere empty.
* An integration with no ordered journey and no published document is a name
  in a catalogue with nothing behind it.

Two checks change severity with the run's scope. ``unreviewed_candidates`` and
``analysis_missing`` are BLOCKING on ``whole_estate`` and ADVISORY on
``focused``, because plan section 1 says a focused investigation accounts for
its *resolved connected scope* -- and nothing deterministic can compute that
subgraph. Demanding estate-wide classification from a focused run is the
behaviour that turned every focused request back into estate-wide work; silently
accepting an unclassified estate on a whole-estate run is how run 250 published
six designs out of a hundred and forty-two. An unrecognised scope value takes
the wider obligation, matching ``guide.scope_mode_for_goal``: an unstated scope
must never buy a weaker gate.

Messages are client-safe by construction -- a relative path, an id, a count.
Never a file's contents, never an agent's free text, because the artifact and
the run log are both read by people who are not holding this file open.

Deterministic Python. No model, no network, no config.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Literal, Sequence

from mcp_server.providers.agent_workspace import contract
from mcp_server.providers.agent_workspace.analysis import MIN_ANALYSIS_BYTES
from mcp_server.providers.agent_workspace.completion import ReceiptReport

if TYPE_CHECKING:  # typing only -- see ``completion.py`` for why.
    from mcp_server.providers.agent_workspace.analysis import AnalysisCoverage
    from mcp_server.providers.agent_workspace.integration_model import (
        Integration,
        IntegrationKnowledgeBase,
    )
    from mcp_server.providers.agent_workspace.ledger import LedgerReport

logger = logging.getLogger(__name__)

#: Where the verdict is persisted. Underscore-prefixed on purpose:
#: ``publish._is_visible`` and ``is_published_doc_filename`` both reject such a
#: segment, so a verdict about a client deliverable can never become part of
#: one by being forgotten in a cleanup list. Same pattern as
#: ``coverage.REACH_ARTIFACT``, and a different filename so the gauge and the
#: gate cannot be mistaken for each other.
ACCOUNTABILITY_ARTIFACT = Path("_audit") / "accountability.json"

#: Stated in the artifact, because it will outlive the person who wrote it --
#: and because the neighbouring artifact in the same directory says the exact
#: opposite about itself.
_IS_A_VERDICT = (
    "This IS a verdict. A blocking finding prevents publication: the draft is "
    "kept in staging for a resume and the previously published documentation "
    "is left unchanged. Advisory findings are reported and change nothing. "
    "Every check is based on identifiers and counts; none of them reads the "
    "wording of a document."
)

#: How many subjects a finding names before it starts counting instead. Ten is
#: enough to recognise a pattern and short enough for a log line; the message
#: always states the true total.
MAX_SUBJECTS = 10

_BLOCKING: Literal["blocking"] = "blocking"
_ADVISORY: Literal["advisory"] = "advisory"

#: The disposition that asserts documented integration behaviour, mirrored from
#: the contract so the gate and the ledger cannot describe different estates.
_MATERIAL = "material"


@dataclass(frozen=True)
class AccountabilityFinding:
    """One structural fact about the run, and what it costs."""

    code: str
    severity: Literal["blocking", "advisory"]
    message: str
    #: Bounded to :data:`MAX_SUBJECTS`. The message says how many more there
    #: are; a finding that names four hundred job ids is a finding nobody reads.
    subjects: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "subjects": list(self.subjects),
        }


@dataclass(frozen=True)
class AccountabilityReport:
    """The verdict. ``ok`` is the only thing publication may branch on."""

    findings: tuple[AccountabilityFinding, ...] = ()

    @property
    def blocking(self) -> tuple[AccountabilityFinding, ...]:
        return tuple(item for item in self.findings if item.severity == _BLOCKING)

    @property
    def advisory(self) -> tuple[AccountabilityFinding, ...]:
        return tuple(item for item in self.findings if item.severity == _ADVISORY)

    @property
    def ok(self) -> bool:
        return not self.blocking

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "artifact": "nexus-accountability-verdict",
            "this_is_a_verdict": _IS_A_VERDICT,
            "ok": self.ok,
            "blocking_count": len(self.blocking),
            "advisory_count": len(self.advisory),
            "findings": [item.to_dict() for item in self.findings],
        }

    def log_line(self) -> str:
        blocking = self.blocking
        advisory = self.advisory
        if blocking:
            codes = ", ".join(item.code for item in blocking[:5])
            more = f" (+{len(blocking) - 5} more)" if len(blocking) > 5 else ""
            return (
                f"accountability: publication blocked by {len(blocking)} check(s): "
                f"{codes}{more}"
            )
        if advisory:
            codes = ", ".join(item.code for item in advisory[:5])
            more = f" (+{len(advisory) - 5} more)" if len(advisory) > 5 else ""
            return (
                f"accountability: structural checks passed with {len(advisory)} "
                f"advisory finding(s): {codes}{more}"
            )
        return "accountability: every structural check passed"


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def evaluate_accountability(
    *,
    staging_dir: Path,
    ledger: LedgerReport,
    knowledge: IntegrationKnowledgeBase | None,
    receipt: ReceiptReport,
    scope_mode: str,
    published_documents: frozenset[str],
    analysis: AnalysisCoverage | None = None,
) -> AccountabilityReport:
    """Judge the run's structural accountability. Never raises.

    ``knowledge`` is ``None`` when there is no integration model to read, for
    any reason: never written, or written and unloadable. ``staging_dir`` is
    used to tell those two apart in the message, because the fix differs.

    ``analysis`` is ``None`` when no analysis coverage was computed, and then
    neither analysis finding is emitted. The gate is HANDED the coverage and
    never computes it -- same rule as the ledger and the model -- so a caller
    that has not wired it yet gets the previous behaviour rather than a finding
    derived from an empty report, which would refuse every run.
    """

    findings: list[AccountabilityFinding] = []
    receipt_path = contract.COMPLETION_RECEIPT.as_posix()
    classified = dict(ledger.classified)

    # --- the semantic handshake (plan section 7) --------------------------
    if receipt.present and receipt.errors:
        findings.append(
            AccountabilityFinding(
                code="receipt_malformed",
                severity=_BLOCKING,
                message=(
                    f"The completion receipt at {receipt_path} could not be read: "
                    + _joined(receipt.errors)
                    + ". An unreadable claim is resumable incomplete work, not a completion."
                ),
            )
        )
    elif not receipt.usable:
        # Deliberately NOT merged with the branch above. "No claim was made" and
        # "a claim was made and cannot be read" have different fixes, and an
        # operator has to be able to tell them apart from the log alone.
        if not receipt.present:
            message = (
                f"There is no completion receipt at {receipt_path}. A provider process "
                "ending normally means the session stopped, not that the goal was "
                "completed; the draft is kept for a resume."
            )
        else:
            message = (
                f"The completion receipt at {receipt_path} states the goal is NOT "
                "complete. That is an honest answer and it is taken at its word: the "
                "run is resumable incomplete work."
            )
        findings.append(
            AccountabilityFinding(
                code="receipt_missing", severity=_BLOCKING, message=message
            )
        )

    if receipt.discrepancies:
        findings.append(
            AccountabilityFinding(
                code="receipt_discrepancy",
                severity=_ADVISORY,
                message=(
                    "The completion receipt disagrees with the state recomputed now: "
                    + _joined(receipt.discrepancies)
                    + ". The agent counted what it knew when it wrote the receipt; a "
                    "restart or a later classification moves the truth underneath it."
                ),
            )
        )

    # --- scope accountability (plan section 1) ----------------------------
    #
    # BLOCKING on a whole-estate run, ADVISORY on a focused one. A focused
    # investigation owes its resolved connected scope, and no deterministic
    # rule here can compute that subgraph -- so it is reported, not enforced.
    # Anything other than an explicit "focused" takes the wider obligation, the
    # same direction ``guide.scope_mode_for_goal`` fails in.
    #
    # Resolved ONCE, for every check that varies with scope. Two checks now
    # depend on it, and a second copy of `scope_mode == "focused"` is a second
    # scoping rule free to drift from the first.
    focused = scope_mode == "focused"

    unreviewed = tuple(ledger.unreviewed)
    if unreviewed:
        findings.append(
            AccountabilityFinding(
                code="unreviewed_candidates",
                severity=_ADVISORY if focused else _BLOCKING,
                message=(
                    f"{len(unreviewed)} parsed design(s) carry no classification: "
                    + _named(unreviewed)
                    + (
                        ". This run is focused, so estate-wide classification is "
                        "reported rather than required -- the connected scope it owes "
                        "cannot be computed deterministically."
                        if focused
                        else ". A whole-estate run must end every parsed design in "
                        "exactly one explicit state."
                    )
                ),
                subjects=_subjects(unreviewed),
            )
        )

    # --- the analysis layer (run 252) -------------------------------------
    #
    # Handed in, never computed here, and absent when the caller did not
    # compute it. Both findings are file-existence and byte-count only: nothing
    # below opens a document or judges a sentence.
    if analysis is not None:
        analysis_dir = contract.ANALYSIS_DIR.as_posix()

        analysis_missing = tuple(analysis.missing)
        if analysis_missing:
            findings.append(
                AccountabilityFinding(
                    code="analysis_missing",
                    severity=_ADVISORY if focused else _BLOCKING,
                    message=(
                        f"{len(analysis_missing)} group(s) have no analysis file under "
                        f"{analysis_dir}/: "
                        + _named(analysis_missing)
                        + (
                            ". This run is focused, so the analysis layer is reported "
                            "rather than required -- the connected scope it owes cannot "
                            "be computed deterministically."
                            if focused
                            else ". A whole-estate run reaches its documents THROUGH the "
                            f"analysis layer, not around it: write {analysis_dir}/<group>.md "
                            "for each group named here -- what its designs do, what they "
                            "exchange, and what the evidence does not settle -- and the run "
                            "can publish."
                        )
                    ),
                    subjects=_subjects(analysis_missing),
                )
            )

        analysis_thin = tuple(analysis.thin)
        if analysis_thin:
            findings.append(
                AccountabilityFinding(
                    code="analysis_thin",
                    severity=_ADVISORY,
                    message=(
                        f"{len(analysis_thin)} analysis file(s) under {analysis_dir}/ are "
                        f"smaller than {MIN_ANALYSIS_BYTES} bytes: "
                        + _named(analysis_thin)
                        + ". Advisory: the floor is a guard against an empty stub, not a "
                        "measure of quality, and a file above it can still be shallow. A "
                        "file this short is usually a heading and nothing else."
                    ),
                    subjects=_subjects(analysis_thin),
                )
            )

    # ``LedgerReport.unresolved`` is the ledger's own definition of the
    # ``unresolved`` DISPOSITION. Recomputing it from ``classified`` here would
    # be a second definition, free to disagree with the count the ledger's
    # summary artifact publishes beside this verdict.
    unresolved = tuple(ledger.unresolved)
    if unresolved:
        findings.append(
            AccountabilityFinding(
                code="unresolved_candidates",
                severity=_BLOCKING,
                message=(
                    f"{len(unresolved)} design(s) were classified 'unresolved': "
                    + _named(unresolved)
                    + ". Unresolved is a legitimate answer and it keeps the run "
                    "incomplete by design; publishing over it would hide the gap."
                ),
                subjects=_subjects(unresolved),
            )
        )

    missing_reason = tuple(ledger.missing_reason)
    if missing_reason:
        findings.append(
            AccountabilityFinding(
                code="missing_reason",
                severity=_BLOCKING,
                message=(
                    f"{len(missing_reason)} design(s) end an obligation without a "
                    "concrete reason: "
                    + _named(missing_reason)
                    + ". Dispositions in "
                    + ", ".join(sorted(contract.DISPOSITION_REQUIRES_REASON))
                    + " reduce what the run owes, so an unjustified one is an escape "
                    "hatch. The reason is checked for existence only, never wording."
                ),
                subjects=_subjects(missing_reason),
            )
        )

    missing_link = tuple(ledger.missing_link)
    if missing_link:
        findings.append(
            AccountabilityFinding(
                code="missing_integration_link",
                severity=_BLOCKING,
                message=(
                    f"{len(missing_link)} design(s) classified "
                    + ", ".join(sorted(contract.DISPOSITION_REQUIRES_MODEL_LINK))
                    + " name no integration id: "
                    + _named(missing_link)
                    + ". A material design asserts documented integration behaviour, so "
                    "it must say which integration covers it."
                ),
                subjects=_subjects(missing_link),
            )
        )

    # --- the model, and what the classifications point at -----------------
    material = tuple(
        sorted(
            unit_id
            for unit_id, record in classified.items()
            if record.disposition == _MATERIAL
        )
    )

    if knowledge is None:
        if material:
            model_path = contract.INTEGRATION_MODEL.as_posix()
            exists = (Path(staging_dir) / contract.INTEGRATION_MODEL).is_file()
            findings.append(
                AccountabilityFinding(
                    code="model_missing",
                    severity=_BLOCKING,
                    message=(
                        f"{len(material)} design(s) are classified {_MATERIAL!r} but "
                        + (
                            f"the integration model at {model_path} could not be loaded."
                            if exists
                            else f"there is no integration model at {model_path}."
                        )
                        + " Material designs with no model is an estate with documented "
                        "behaviour and nothing that records it."
                    ),
                    subjects=_subjects(material),
                )
            )
    else:
        issues = knowledge.validate_structure()
        if issues:
            findings.append(
                AccountabilityFinding(
                    code="model_invalid",
                    severity=_BLOCKING,
                    message=(
                        f"The integration model has {len(issues)} structural issue(s): "
                        + _joined(
                            tuple(f"{issue.path}: {issue.code}" for issue in issues)
                        )
                        + "."
                    ),
                    subjects=_subjects(issue.path for issue in issues),
                )
            )

        if material and not knowledge.integrations:
            findings.append(
                AccountabilityFinding(
                    code="no_integrations",
                    severity=_BLOCKING,
                    message=(
                        f"{len(material)} design(s) are classified {_MATERIAL!r} and the "
                        "integration model holds zero integrations. This is the run-250 "
                        "shape: an investigation that compiled to nothing, published as "
                        "if it had."
                    ),
                    subjects=_subjects(material),
                )
            )

        known_ids = {integration.id for integration in knowledge.integrations}
        dangling = tuple(
            sorted(
                f"{unit_id} -> {integration_id}"
                for unit_id, record in classified.items()
                for integration_id in record.integration_ids
                if integration_id not in known_ids
            )
        )
        if dangling:
            findings.append(
                AccountabilityFinding(
                    code="dangling_integration_link",
                    severity=_BLOCKING,
                    message=(
                        f"{len(dangling)} classification(s) name an integration id that "
                        "is not in the model: "
                        + _named(dangling)
                        + ". Two ids are compared for exact equality; no name is matched."
                    ),
                    subjects=_subjects(dangling),
                )
            )

        without_journey = tuple(
            sorted(
                integration.id
                for integration in knowledge.integrations
                if not _has_ordered_journey(integration)
            )
        )
        if without_journey:
            findings.append(
                AccountabilityFinding(
                    code="integration_without_journey",
                    severity=_BLOCKING,
                    message=(
                        f"{len(without_journey)} integration(s) have no journey with an "
                        "established order: "
                        + _named(without_journey)
                        + ". An integration whose steps carry no sequence is a boundary "
                        "with no reconstructed path through it."
                    ),
                    subjects=_subjects(without_journey),
                )
            )

        publishable = {_document_key(name) for name in published_documents}
        without_document = tuple(
            sorted(
                integration.id
                for integration in knowledge.integrations
                if not _covering_documents(integration, publishable)
            )
        )
        if without_document:
            findings.append(
                AccountabilityFinding(
                    code="integration_without_document",
                    severity=_BLOCKING,
                    message=(
                        f"{len(without_document)} integration(s) name no published client "
                        "document: "
                        + _named(without_document)
                        + ". Paths are compared against what the publisher accepted, so "
                        "an integration documented only in a file that was refused reads "
                        "here as undocumented."
                    ),
                    subjects=_subjects(without_document),
                )
            )

    # --- reported, never enforced ----------------------------------------
    unknown = tuple(ledger.unknown)
    if unknown:
        findings.append(
            AccountabilityFinding(
                code="unknown_candidate",
                severity=_ADVISORY,
                message=(
                    f"{len(unknown)} classification(s) name a unit that is not in the "
                    "parsed population: "
                    + _named(unknown)
                    + ". Advisory: a stale id costs the run nothing, and the population "
                    "is recomputed from the evidence rather than taken from the roster."
                ),
                subjects=_subjects(unknown),
            )
        )

    return AccountabilityReport(findings=tuple(findings))


def write_accountability_artifact(
    staging_dir: Path, report: AccountabilityReport
) -> Path | None:
    """Persist the verdict into the run's audit record. Best effort.

    Best effort even though the verdict itself blocks: the DECISION is the
    return value of :func:`evaluate_accountability` and the caller already
    holds it. A run that is otherwise publishable must not be refused because a
    directory was not writable, and a run that is blocked is blocked whether or
    not the file lands.
    """

    path = Path(staging_dir) / ACCOUNTABILITY_ARTIFACT
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
    except OSError:
        logger.warning("Could not persist the agent-workspace accountability verdict")
        return None
    return path


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _has_ordered_journey(integration: Integration) -> bool:
    """At least one journey with at least one step whose position is known.

    Tested on ``step.sequence``, NOT on ``Journey.ordered_steps``:
    ``ordered_steps`` returns every step and merely sorts the ones with no
    sequence to the end, so an integration with a dozen unplaceable steps would
    pass a length check on it while having established no order at all. The
    field is what records that the order was established.
    """

    return any(
        any(step.sequence is not None for step in journey.steps)
        for journey in integration.journeys
    )


def _covering_documents(integration: Integration, publishable: set[str]) -> list[str]:
    """The integration's document paths that the publisher actually accepted.

    Journey-level ``document_paths`` count. A journey is the thing a reader was
    sent to, and requiring the link on the integration as well would fail a
    correctly documented estate over where the agent chose to record the path.
    """

    declared = list(integration.document_paths)
    for journey in integration.journeys:
        declared.extend(journey.document_paths)
    return [name for name in declared if _document_key(name) in publishable]


def _document_key(value: str) -> str:
    """Normalise a published-document reference for comparison.

    Strips a FORMAT difference only -- a leading ``./`` and the ``output/``
    prefix the agent naturally types for the same file. Case is never folded
    and no other normalisation happens: ``coverage.py`` records that two Talend
    groups differing in one letter's case are two groups, and a design and its
    plural are two designs. Merging names is how one gets credit for another.
    """

    text = value.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    while text.startswith("output/"):
        text = text[len("output/") :]
    return text


def _subjects(values: Iterable[str]) -> tuple[str, ...]:
    """At most :data:`MAX_SUBJECTS`, deduplicated, in a stable order."""

    return tuple(sorted(dict.fromkeys(values))[:MAX_SUBJECTS])


def _named(values: Sequence[str]) -> str:
    """``a, b, c (+7 more)``. The total is always visible."""

    shown = list(dict.fromkeys(values))
    head = ", ".join(shown[:MAX_SUBJECTS])
    if len(shown) > MAX_SUBJECTS:
        return f"{head} (+{len(shown) - MAX_SUBJECTS} more)"
    return head


def _joined(messages: Sequence[str], limit: int = 3) -> str:
    """The first few reasons, with the rest counted rather than dropped."""

    head = "; ".join(messages[:limit])
    if len(messages) > limit:
        return f"{head} (+{len(messages) - limit} more)"
    return head
