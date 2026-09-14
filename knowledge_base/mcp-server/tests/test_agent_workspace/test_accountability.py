"""Slice 5: the publication gate, and the line between structure and prose.

Plan section 8 fixes what may be checked: structural truth, never wording. So
every test here either proves that an id-based or count-based defect BLOCKS, or
proves that something which would require reading a document's text is absent
from the module entirely.

The run this exists to refuse: a session that exited zero having classified six
of a hundred and forty-two designs, with an integration model that compiled to
zero integrations, published as a success.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp_server.providers.agent_workspace import contract
from mcp_server.providers.agent_workspace.accountability import (
    ACCOUNTABILITY_ARTIFACT,
    MAX_SUBJECTS,
    evaluate_accountability,
    write_accountability_artifact,
)
from mcp_server.providers.agent_workspace.analysis import MIN_ANALYSIS_BYTES, AnalysisCoverage
from mcp_server.providers.agent_workspace.completion import ReceiptReport, read_receipt, reconcile
from mcp_server.providers.agent_workspace.integration_model import (
    Integration,
    IntegrationKnowledgeBase,
    Journey,
    JourneyStep,
)
from mcp_server.providers.agent_workspace.ledger import Classification, LedgerReport

DOC = "INTEGRATION__orders.md"
PUBLISHED = frozenset({DOC, "reference/designs.md"})


# --------------------------------------------------------------------------
# Scaffolding
# --------------------------------------------------------------------------


def _classification(
    unit_id: str,
    disposition: str,
    *,
    reason: str = "stated",
    integrations: tuple[str, ...] = (),
) -> Classification:
    return Classification(
        unit_id=unit_id,
        disposition=disposition,
        reason=reason,
        integration_ids=integrations,
        line_number=1,
    )


def _ledger(**overrides: object) -> LedgerReport:
    classified = overrides.pop("classified", {})  # type: ignore[assignment]
    return LedgerReport(classified=dict(classified), **overrides)  # type: ignore[arg-type]


def _settled_ledger() -> LedgerReport:
    return _ledger(
        classified={
            "u1": _classification("u1", "material", integrations=("int-0",)),
            "u2": _classification("u2", "utility", reason="a date helper"),
        },
        counts={"material": 1, "utility": 1},
    )


def _integration(
    *,
    identifier: str = "int-0",
    journeys: list[Journey] | None = None,
    documents: list[str] | None = None,
    evidence_ref_ids: list[str] | None = None,
) -> Integration:
    return Integration(
        id=identifier,
        label="Orders exchange",
        state="partial",
        document_paths=[DOC] if documents is None else documents,
        evidence_ref_ids=evidence_ref_ids or [],
        journeys=[_journey()] if journeys is None else journeys,
    )


def _journey(
    *,
    identifier: str = "jny-0",
    sequence: int | None = 1,
    documents: list[str] | None = None,
) -> Journey:
    return Journey(
        id=identifier,
        label="Create order",
        state="partial",
        document_paths=documents or [],
        steps=[JourneyStep(id=f"{identifier}-step", sequence=sequence)],
    )


def _knowledge(*integrations: Integration) -> IntegrationKnowledgeBase:
    return IntegrationKnowledgeBase(integrations=list(integrations or (_integration(),)))


def _complete_receipt(staging: Path, ledger: LedgerReport, **overrides: object) -> ReceiptReport:
    payload: dict = {
        "scope_mode": "whole_estate",
        "coverage_digest": ledger.digest(),
        "candidate_counts": dict(ledger.counts),
        "unreviewed_count": len(ledger.unreviewed),
        "unresolved_count": len(ledger.unresolved),
        "integration_count": 1,
        "journey_count": 1,
        "document_paths": [DOC],
        "limitations": [],
        "complete": True,
    }
    payload.update(overrides)
    path = staging / contract.COMPLETION_RECEIPT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return read_receipt(staging)


#: ``None`` is a MEANINGFUL argument here -- it is "there is no model" -- so
#: the helper cannot use it to mean "give me the default one".
_UNSET = object()


def _evaluate(
    staging: Path,
    *,
    ledger: LedgerReport | None = None,
    knowledge: object = _UNSET,
    receipt: ReceiptReport | None = None,
    scope_mode: str = "whole_estate",
    published: frozenset[str] = PUBLISHED,
    analysis: AnalysisCoverage | None = None,
):
    resolved_ledger = _settled_ledger() if ledger is None else ledger
    return evaluate_accountability(
        staging_dir=staging,
        ledger=resolved_ledger,
        knowledge=_knowledge() if knowledge is _UNSET else knowledge,  # type: ignore[arg-type]
        receipt=_complete_receipt(staging, resolved_ledger) if receipt is None else receipt,
        scope_mode=scope_mode,
        published_documents=published,
        analysis=analysis,
    )


def _analysis(
    *,
    groups: tuple[str, ...] = ("billing", "orders"),
    written: dict[str, int] | None = None,
    missing: tuple[str, ...] = (),
    thin: tuple[str, ...] = (),
) -> AnalysisCoverage:
    return AnalysisCoverage(
        groups=groups,
        written={name: 4000 for name in groups} if written is None else written,
        missing=missing,
        thin=thin,
    )


def _codes(report) -> set[str]:
    return {finding.code for finding in report.findings}


def _blocking(report) -> set[str]:
    return {finding.code for finding in report.blocking}


def _message(report, code: str) -> str:
    return next(finding.message for finding in report.findings if finding.code == code)


# --------------------------------------------------------------------------
# The run that must pass
# --------------------------------------------------------------------------


def test_a_fully_accounted_run_passes_clean(tmp_path: Path) -> None:
    report = _evaluate(tmp_path)

    assert report.findings == ()
    assert report.ok is True
    assert report.log_line() == "accountability: every structural check passed"


# --------------------------------------------------------------------------
# The semantic handshake
# --------------------------------------------------------------------------


def test_a_normal_exit_with_no_receipt_blocks(tmp_path: Path) -> None:
    report = _evaluate(tmp_path, receipt=read_receipt(tmp_path))

    assert _blocking(report) == {"receipt_missing"}
    assert "session stopped" in _message(report, "receipt_missing")
    assert report.ok is False


def test_a_receipt_claiming_incomplete_is_taken_at_its_word(tmp_path: Path) -> None:
    ledger = _settled_ledger()
    receipt = _complete_receipt(tmp_path, ledger, complete=False)

    report = _evaluate(tmp_path, ledger=ledger, receipt=receipt)

    assert _blocking(report) == {"receipt_missing"}
    assert "NOT" in _message(report, "receipt_missing")


def test_an_unreadable_receipt_is_malformed_and_not_also_missing(tmp_path: Path) -> None:
    """Two different fixes, so two different findings -- and never both."""

    path = tmp_path / contract.COMPLETION_RECEIPT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{", encoding="utf-8")

    report = _evaluate(tmp_path, receipt=read_receipt(tmp_path))

    assert _blocking(report) == {"receipt_malformed"}
    assert "receipt_missing" not in _codes(report)


def test_a_discrepancy_is_advisory_and_says_so(tmp_path: Path) -> None:
    """The agent counted what it knew when it wrote the receipt. A restart
    moves the truth underneath it, and that must not block a finished run."""

    ledger = _settled_ledger()
    receipt = reconcile(
        _complete_receipt(tmp_path, ledger, integration_count=99),
        ledger=ledger,
        knowledge=_knowledge(),
        scope_mode="whole_estate",
    )

    report = _evaluate(tmp_path, ledger=ledger, receipt=receipt)

    assert _codes(report) == {"receipt_discrepancy"}
    assert report.ok is True
    assert "advisory" in report.log_line()


# --------------------------------------------------------------------------
# Scope accountability
# --------------------------------------------------------------------------


def test_the_run_250_shape_is_refused(tmp_path: Path) -> None:
    """A hundred and forty-two candidates with six classified is 136
    outstanding, and an integration model that compiled to nothing."""

    classified = {
        f"u{index}": _classification(f"u{index}", "material", integrations=("int-0",))
        for index in range(6)
    }
    ledger = _ledger(
        classified=classified,
        unreviewed=tuple(f"u{index}" for index in range(6, 142)),
        counts={"material": 6},
    )

    report = _evaluate(
        tmp_path,
        ledger=ledger,
        knowledge=IntegrationKnowledgeBase(integrations=[]),
    )

    assert "unreviewed_candidates" in _blocking(report)
    assert "no_integrations" in _blocking(report)
    assert "136 parsed design(s)" in _message(report, "unreviewed_candidates")
    assert report.ok is False


def test_unreviewed_blocks_a_whole_estate_run_and_only_reports_a_focused_one(
    tmp_path: Path,
) -> None:
    """Plan section 1: a focused run accounts for its RESOLVED CONNECTED SCOPE,
    and nothing deterministic can compute that subgraph -- so it is reported,
    not enforced. Demanding estate-wide classification from a focused run is
    what turned every focused request back into estate-wide work."""

    ledger = _ledger(
        classified={"u1": _classification("u1", "material", integrations=("int-0",))},
        unreviewed=("u2", "u3"),
    )

    whole = _evaluate(tmp_path, ledger=ledger)
    focused = _evaluate(tmp_path, ledger=ledger, scope_mode="focused")

    assert "unreviewed_candidates" in _blocking(whole)
    assert "unreviewed_candidates" not in _blocking(focused)
    assert "unreviewed_candidates" in {item.code for item in focused.advisory}
    assert focused.ok is True


def test_an_unrecognised_scope_value_takes_the_wider_obligation(tmp_path: Path) -> None:
    """An unstated scope must never buy a weaker gate. Same direction
    ``guide.scope_mode_for_goal`` fails in."""

    ledger = _ledger(
        classified={"u1": _classification("u1", "material", integrations=("int-0",))},
        unreviewed=("u2",),
    )

    for value in ("", "WHOLE_ESTATE", "Focused", "partial"):
        report = _evaluate(tmp_path, ledger=ledger, scope_mode=value)
        assert "unreviewed_candidates" in _blocking(report), value


def test_an_unresolved_disposition_blocks(tmp_path: Path) -> None:
    ledger = _ledger(
        classified={
            "u1": _classification("u1", "material", integrations=("int-0",)),
            "u2": _classification("u2", "unresolved", reason="two candidate callers"),
        }
    )

    report = _evaluate(tmp_path, ledger=ledger)

    assert "unresolved_candidates" in _blocking(report)
    assert _message(report, "unresolved_candidates").startswith("1 design(s)")


def test_a_disposition_that_ends_an_obligation_needs_a_reason(tmp_path: Path) -> None:
    ledger = _ledger(
        classified={"u1": _classification("u1", "material", integrations=("int-0",))},
        missing_reason=("u5", "u6"),
    )

    report = _evaluate(tmp_path, ledger=ledger)

    message = _message(report, "missing_reason")
    assert "missing_reason" in _blocking(report)
    # The vocabulary comes from the contract, never from a copy in this module.
    for disposition in contract.DISPOSITION_REQUIRES_REASON:
        assert disposition in message
    assert "never wording" in message


def test_a_material_design_must_name_its_integration(tmp_path: Path) -> None:
    ledger = _ledger(
        classified={"u1": _classification("u1", "material")},
        missing_link=("u1",),
    )

    report = _evaluate(tmp_path, ledger=ledger)

    assert "missing_integration_link" in _blocking(report)
    for disposition in contract.DISPOSITION_REQUIRES_MODEL_LINK:
        assert disposition in _message(report, "missing_integration_link")


def test_a_classification_naming_an_absent_integration_blocks(tmp_path: Path) -> None:
    """Two ids compared for exact equality. No name is matched, because plan
    section 8 forbids failing over a slightly different job name."""

    ledger = _ledger(
        classified={"u1": _classification("u1", "material", integrations=("int-ghost",))}
    )

    report = _evaluate(tmp_path, ledger=ledger)

    assert "dangling_integration_link" in _blocking(report)
    finding = next(
        item for item in report.findings if item.code == "dangling_integration_link"
    )
    assert finding.subjects == ("u1 -> int-ghost",)


def test_a_stale_classified_id_is_advisory(tmp_path: Path) -> None:
    """A typo'd id costs the run nothing: the population is recomputed from the
    evidence rather than taken from the agent-writable roster."""

    ledger = _ledger(
        classified={"u1": _classification("u1", "material", integrations=("int-0",))},
        unknown=("u404",),
    )

    report = _evaluate(tmp_path, ledger=ledger)

    assert _codes(report) == {"unknown_candidate"}
    assert report.ok is True


# --------------------------------------------------------------------------
# The analysis layer
#
# Run 252: seven documents written off the index with six of a hundred and
# forty-two design files opened. The analysis layer is the step that run
# skipped, and these two findings are the only thing standing between a
# repetition of it and a published deliverable.
# --------------------------------------------------------------------------


def test_a_fully_written_analysis_layer_produces_no_finding(tmp_path: Path) -> None:
    report = _evaluate(tmp_path, analysis=_analysis())

    assert report.findings == ()
    assert report.ok is True


def test_no_analysis_coverage_emits_neither_analysis_finding(tmp_path: Path) -> None:
    """``None`` means the caller computed nothing, not that nothing was written.

    Deriving a finding from an empty report would refuse every run whose caller
    has not been wired yet -- a gate that fails closed on its own absence."""

    report = _evaluate(tmp_path, analysis=None)

    assert "analysis_missing" not in _codes(report)
    assert "analysis_thin" not in _codes(report)
    assert report.ok is True


def test_a_group_with_no_analysis_blocks_a_whole_estate_run(tmp_path: Path) -> None:
    coverage = _analysis(
        groups=("billing", "orders", "stock"),
        written={"orders": 4000},
        missing=("billing", "stock"),
    )

    report = _evaluate(tmp_path, analysis=coverage)

    assert "analysis_missing" in _blocking(report)
    message = _message(report, "analysis_missing")
    assert message.startswith("2 group(s) have no analysis file")
    assert "billing, stock" in message
    assert contract.ANALYSIS_DIR.as_posix() in message
    # The message has to tell an operator what to do, not only what is wrong.
    assert "THROUGH the analysis layer" in message


def test_a_focused_run_reports_a_missing_analysis_rather_than_blocking(
    tmp_path: Path,
) -> None:
    """Same rule as ``unreviewed_candidates``. A focused investigation owes its
    resolved connected scope, and nothing deterministic can compute that
    subgraph -- so demanding an analysis file for every group of the estate
    would turn every focused request back into estate-wide work."""

    coverage = _analysis(written={"orders": 4000}, missing=("billing",))

    report = _evaluate(tmp_path, analysis=coverage, scope_mode="focused")

    assert "analysis_missing" not in _blocking(report)
    assert "analysis_missing" in {item.code for item in report.advisory}
    assert "reported rather than required" in _message(report, "analysis_missing")
    assert report.ok is True


def test_an_unrecognised_scope_value_takes_the_wider_analysis_obligation(
    tmp_path: Path,
) -> None:
    """An unstated scope must never buy a weaker gate, and the two scope-varying
    checks must not be able to disagree about what 'focused' means."""

    coverage = _analysis(written={"orders": 4000}, missing=("billing",))

    for value in ("", "WHOLE_ESTATE", "Focused", "partial"):
        report = _evaluate(tmp_path, analysis=coverage, scope_mode=value)
        assert "analysis_missing" in _blocking(report), value


def test_a_thin_analysis_file_is_advisory_and_says_what_the_floor_is(
    tmp_path: Path,
) -> None:
    coverage = _analysis(written={"billing": 120, "orders": 4000}, thin=("billing",))

    report = _evaluate(tmp_path, analysis=coverage)

    assert _codes(report) == {"analysis_thin"}
    assert report.ok is True
    message = _message(report, "analysis_thin")
    assert str(MIN_ANALYSIS_BYTES) in message
    assert "not a measure of quality" in message


def test_a_thin_file_stays_advisory_even_when_a_missing_one_blocks(
    tmp_path: Path,
) -> None:
    """Two different states with two different costs. A written-but-short file
    is work that landed; a missing one is work that did not."""

    coverage = _analysis(
        groups=("billing", "orders", "stock"),
        written={"billing": 40, "orders": 4000},
        missing=("stock",),
        thin=("billing",),
    )

    report = _evaluate(tmp_path, analysis=coverage)

    assert _blocking(report) == {"analysis_missing"}
    assert "analysis_thin" in {item.code for item in report.advisory}


def test_the_analysis_findings_name_a_bounded_number_of_groups(tmp_path: Path) -> None:
    groups = tuple(f"group{index:03d}" for index in range(40))
    coverage = _analysis(groups=groups, written={}, missing=groups, thin=())

    report = _evaluate(tmp_path, analysis=coverage)
    finding = next(item for item in report.findings if item.code == "analysis_missing")

    assert len(finding.subjects) == MAX_SUBJECTS
    assert finding.message.startswith("40 group(s)")
    assert f"+{40 - MAX_SUBJECTS} more" in finding.message


def test_the_analysis_findings_carry_no_file_contents(tmp_path: Path) -> None:
    """Group names, a relative path and counts. Nothing here has ever opened an
    analysis file, and the gate is handed byte counts precisely so it cannot."""

    coverage = _analysis(written={"orders": 12}, missing=("billing",), thin=("orders",))

    report = _evaluate(tmp_path, analysis=coverage)

    for code in ("analysis_missing", "analysis_thin"):
        message = _message(report, code)
        assert "notes/analysis" in message
        assert ".md" not in message.replace("<group>.md", "")


# --------------------------------------------------------------------------
# The model
# --------------------------------------------------------------------------


def test_material_candidates_with_no_model_block_and_the_message_says_which(
    tmp_path: Path,
) -> None:
    absent = _evaluate(tmp_path, knowledge=None)
    assert "model_missing" in _blocking(absent)
    assert "there is no integration model" in _message(absent, "model_missing")

    model_path = tmp_path / contract.INTEGRATION_MODEL
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_text("{ broken", encoding="utf-8")
    unloadable = _evaluate(tmp_path, knowledge=None)
    assert "could not be loaded" in _message(unloadable, "model_missing")


def test_no_material_candidates_and_no_model_is_not_a_defect(tmp_path: Path) -> None:
    """An estate of utilities is a legitimate answer."""

    ledger = _ledger(
        classified={"u1": _classification("u1", "utility", reason="a date helper")},
        counts={"utility": 1},
    )

    report = _evaluate(tmp_path, ledger=ledger, knowledge=None)

    assert report.findings == ()
    assert report.ok is True


def test_a_structurally_invalid_model_blocks(tmp_path: Path) -> None:
    knowledge = _knowledge(_integration(evidence_ref_ids=["ev-missing"]))

    report = _evaluate(tmp_path, knowledge=knowledge)

    assert "model_invalid" in _blocking(report)
    finding = next(item for item in report.findings if item.code == "model_invalid")
    assert finding.subjects == ("integrations/int-0",)
    assert "dangling_evidence_ref" in finding.message


def test_zero_integrations_with_material_candidates_blocks(tmp_path: Path) -> None:
    report = _evaluate(tmp_path, knowledge=IntegrationKnowledgeBase(integrations=[]))

    assert "no_integrations" in _blocking(report)


def test_an_integration_needs_a_journey_with_an_established_order(tmp_path: Path) -> None:
    """``Journey.ordered_steps`` returns EVERY step and merely sorts the
    unplaceable ones last, so a length check on it would pass an integration
    that established no order at all. The ``sequence`` field is the test."""

    no_journey = _evaluate(tmp_path, knowledge=_knowledge(_integration(journeys=[])))
    unplaceable = _knowledge(_integration(journeys=[_journey(sequence=None)]))
    unordered = _evaluate(tmp_path, knowledge=unplaceable)

    assert "integration_without_journey" in _blocking(no_journey)
    assert "integration_without_journey" in _blocking(unordered)
    assert unplaceable.integrations[0].journeys[0].ordered_steps  # the trap


def test_an_integration_must_name_a_document_the_publisher_accepted(
    tmp_path: Path,
) -> None:
    unlinked = _evaluate(tmp_path, knowledge=_knowledge(_integration(documents=[])))
    refused = _evaluate(
        tmp_path, knowledge=_knowledge(_integration(documents=["NEVER_PUBLISHED.md"]))
    )

    assert "integration_without_document" in _blocking(unlinked)
    assert "integration_without_document" in _blocking(refused)


def test_a_journey_level_document_link_counts(tmp_path: Path) -> None:
    """Requiring the link on the integration as well would fail a correctly
    documented estate over where the agent chose to record the path."""

    knowledge = _knowledge(
        _integration(documents=[], journeys=[_journey(documents=[DOC])])
    )

    report = _evaluate(tmp_path, knowledge=knowledge)

    assert report.findings == ()


def test_an_output_prefixed_path_is_the_same_file_but_case_is_never_folded(
    tmp_path: Path,
) -> None:
    prefixed = _evaluate(
        tmp_path, knowledge=_knowledge(_integration(documents=[f"output/{DOC}"]))
    )
    recased = _evaluate(
        tmp_path, knowledge=_knowledge(_integration(documents=[DOC.lower()]))
    )

    assert prefixed.findings == ()
    assert "integration_without_document" in _blocking(recased)


# --------------------------------------------------------------------------
# What the findings may say
# --------------------------------------------------------------------------


def test_subjects_are_bounded_and_the_message_states_the_true_total(
    tmp_path: Path,
) -> None:
    ledger = _ledger(
        classified={"u1": _classification("u1", "material", integrations=("int-0",))},
        unreviewed=tuple(f"u{index:03d}" for index in range(40)),
    )

    report = _evaluate(tmp_path, ledger=ledger)
    finding = next(item for item in report.findings if item.code == "unreviewed_candidates")

    assert len(finding.subjects) == MAX_SUBJECTS
    assert finding.message.startswith("40 parsed design(s)")
    assert f"+{40 - MAX_SUBJECTS} more" in finding.message


def test_no_message_carries_an_agents_free_text(tmp_path: Path) -> None:
    """Messages are a relative path, an id or a count. The reason an agent
    wrote is prose from a client's estate and never reaches a log line."""

    secret = "the connection string in the dispatcher was left in the note"
    ledger = _ledger(
        classified={
            "u1": _classification("u1", "excluded", reason=secret),
            "u2": _classification("u2", "unresolved", reason=secret),
        },
        missing_reason=("u3",),
        unreviewed=("u4",),
        unknown=("u5",),
    )

    report = _evaluate(tmp_path, ledger=ledger, knowledge=None)

    assert report.findings
    for finding in report.findings:
        assert secret not in finding.message
        assert all(secret not in subject for subject in finding.subjects)


def test_nothing_in_the_module_reads_a_documents_wording() -> None:
    """Plan section 8, enforced on the source. The gate is handed document
    NAMES and never document text; a run was failed for a heading our own
    instructions had told it to use.

    Resolved from the module object, never from the working directory: a guard
    that silently reads nothing when pytest is invoked from elsewhere guards
    nothing."""

    from mcp_server.providers.agent_workspace import accountability

    source = Path(accountability.__file__).read_text(encoding="utf-8")
    body = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )

    for forbidden in ("import re", "re.compile", "read_text(", ".search(", ".findall("):
        assert forbidden not in body, forbidden


def test_the_gate_never_raises_on_an_empty_run(tmp_path: Path) -> None:
    report = evaluate_accountability(
        staging_dir=tmp_path,
        ledger=LedgerReport(),
        knowledge=None,
        receipt=read_receipt(tmp_path),
        scope_mode="whole_estate",
        published_documents=frozenset(),
    )

    assert _blocking(report) == {"receipt_missing"}


# --------------------------------------------------------------------------
# The artifact
# --------------------------------------------------------------------------


def test_the_verdict_is_written_where_no_client_document_can_see_it(
    tmp_path: Path,
) -> None:
    report = _evaluate(tmp_path, receipt=read_receipt(tmp_path))

    written = write_accountability_artifact(tmp_path, report)

    assert written == tmp_path / ACCOUNTABILITY_ARTIFACT
    assert ACCOUNTABILITY_ARTIFACT.parts[0].startswith("_")
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["blocking_count"] == 1
    # The neighbouring artifact in this directory says the opposite about
    # itself; a reader must not have to guess which one is a gate.
    assert "IS a verdict" in payload["this_is_a_verdict"]
    assert payload["findings"][0]["code"] == "receipt_missing"


def test_an_unwritable_artifact_does_not_change_the_verdict(tmp_path: Path) -> None:
    """The decision is the return value of ``evaluate_accountability``; a
    directory that will not take a file must not publish a blocked run, and
    must not refuse a publishable one."""

    (tmp_path / ACCOUNTABILITY_ARTIFACT.parts[0]).write_text("not a directory")
    report = _evaluate(tmp_path)

    assert write_accountability_artifact(tmp_path, report) is None
    assert report.ok is True


def test_the_log_line_names_the_blocking_codes(tmp_path: Path) -> None:
    ledger = _ledger(
        classified={"u1": _classification("u1", "material")},
        unreviewed=("u2",),
        missing_link=("u1",),
    )

    report = _evaluate(tmp_path, ledger=ledger, knowledge=None, receipt=read_receipt(tmp_path))
    line = report.log_line()

    assert line.startswith("accountability: publication blocked by 4 check(s)")
    assert "receipt_missing" in line
