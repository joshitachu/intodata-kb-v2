"""Slice 3: the completion handshake, and the three states it has to keep apart.

Run 250 exited zero after opening six of a hundred and forty-two designs, and
the runner read that exit as success. A process exit code says nothing about an
investigation, so the agent has to make the claim itself -- and Nexus has to be
able to tell "no claim was made" from "a claim was made and cannot be read"
from "a claim was made and the numbers have moved since".

The other half of these tests is the constraint: a discrepancy is NOT a lie.
The receipt records what the agent knew when it wrote the file, and a restart
moves the truth underneath it, so every reconciliation message names both
numbers and attributes each one to its source.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp_server.providers.agent_workspace import contract
from mcp_server.providers.agent_workspace.completion import (
    REQUIRED_KEYS,
    read_receipt,
    reconcile,
)
from mcp_server.providers.agent_workspace.integration_model import (
    Integration,
    IntegrationKnowledgeBase,
    Journey,
    JourneyStep,
)
from mcp_server.providers.agent_workspace.ledger import Classification, LedgerReport


def _receipt(staging: Path, payload: object) -> Path:
    path = staging / contract.COMPLETION_RECEIPT
    path.parent.mkdir(parents=True, exist_ok=True)
    body = payload if isinstance(payload, str) else json.dumps(payload)
    path.write_text(body, encoding="utf-8")
    return path


def _claim(**overrides: object) -> dict:
    payload: dict = {
        "scope_mode": "whole_estate",
        "coverage_digest": "0" * 64,
        "candidate_counts": {"material": 2, "utility": 1},
        "unreviewed_count": 0,
        "unresolved_count": 0,
        "integration_count": 1,
        "journey_count": 1,
        "document_paths": ["INTEGRATION__orders.md"],
        "limitations": ["no runtime mirror was connected"],
        "complete": True,
        "notes": "every candidate is accounted for",
    }
    payload.update(overrides)
    return payload


def _classification(
    unit_id: str, disposition: str, *, integrations: tuple[str, ...] = ()
) -> Classification:
    return Classification(
        unit_id=unit_id,
        disposition=disposition,
        reason="stated",
        integration_ids=integrations,
        line_number=1,
    )


def _ledger(**overrides: object) -> LedgerReport:
    classified = overrides.pop("classified", {})  # type: ignore[assignment]
    return LedgerReport(classified=dict(classified), **overrides)  # type: ignore[arg-type]


def _knowledge(*, journeys: int = 1, integrations: int = 1) -> IntegrationKnowledgeBase:
    built = []
    for index in range(integrations):
        built.append(
            Integration(
                id=f"int-{index}",
                label=f"Orders exchange {index}",
                state="partial",
                journeys=[
                    Journey(
                        id=f"jny-{index}-{number}",
                        label=f"Create {number}",
                        state="partial",
                        steps=[JourneyStep(id=f"step-{index}-{number}", sequence=1)],
                    )
                    for number in range(journeys)
                ],
            )
        )
    return IntegrationKnowledgeBase(integrations=built)


# --------------------------------------------------------------------------
# Absent, malformed, present: three states, never two
# --------------------------------------------------------------------------


def test_an_absent_receipt_is_not_an_error(tmp_path: Path) -> None:
    """The normal unfinished case. An operator must be able to read the log and
    see that the agent never claimed to be done, which is a different sentence
    from "the agent claimed to be done and lied"."""

    report = read_receipt(tmp_path)

    assert report.present is False
    assert report.errors == ()
    assert report.claim is None
    assert report.usable is False
    assert "absent" in report.log_line()


def test_invalid_json_is_malformed_and_names_no_content(tmp_path: Path) -> None:
    _receipt(tmp_path, '{"complete": tru')

    report = read_receipt(tmp_path)

    assert report.present is True
    assert report.usable is False
    assert len(report.errors) == 1
    # Position only. The receipt sits beside an investigation's notes and this
    # message reaches a run log.
    assert "line 1" in report.errors[0]
    assert "tru" not in report.errors[0]


def test_a_non_object_root_is_malformed(tmp_path: Path) -> None:
    _receipt(tmp_path, [1, 2, 3])

    report = read_receipt(tmp_path)

    assert report.errors == ("it is not a JSON object",)


def test_an_empty_file_is_malformed_not_absent(tmp_path: Path) -> None:
    """Started and not finished is a different story from never started."""

    _receipt(tmp_path, "")

    report = read_receipt(tmp_path)

    assert report.present is True
    assert report.errors == ("it is empty",)


def test_undecodable_bytes_do_not_raise(tmp_path: Path) -> None:
    path = tmp_path / contract.COMPLETION_RECEIPT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"complete": "\xff\xfe"}')

    report = read_receipt(tmp_path)

    assert report.present is True
    assert report.errors and "UnicodeDecodeError" in report.errors[0]


def test_an_oversized_file_is_refused_before_it_is_read(tmp_path: Path) -> None:
    """A receipt is a handful of counts. Reading a megabyte to find that out is
    how a control file becomes a denial of service."""

    _receipt(tmp_path, {"complete": True, "notes": "x" * (300 * 1024)})

    report = read_receipt(tmp_path)

    assert report.present is True
    assert report.errors and "ceiling" in report.errors[0]


def test_every_required_key_is_named_when_missing(tmp_path: Path) -> None:
    _receipt(tmp_path, {"scope_mode": "whole_estate"})

    report = read_receipt(tmp_path)

    assert report.claim is None
    for key in REQUIRED_KEYS:
        assert any(key in error for error in report.errors), key


def test_a_thin_but_valid_receipt_is_usable(tmp_path: Path) -> None:
    """Only the four binding keys are required. A missing optional field is a
    thinner claim, not a malformed one -- the gate recomputes the numbers."""

    _receipt(
        tmp_path,
        {
            "coverage_digest": "abc",
            "unreviewed_count": 0,
            "unresolved_count": 0,
            "complete": True,
        },
    )

    report = read_receipt(tmp_path)

    assert report.errors == ()
    assert report.usable is True
    assert report.claim is not None
    assert report.claim.candidate_counts == {}
    assert report.claim.document_paths == ()


def test_a_receipt_claiming_incomplete_parses_but_is_not_usable(tmp_path: Path) -> None:
    _receipt(tmp_path, _claim(complete=False))

    report = read_receipt(tmp_path)

    assert report.errors == ()
    assert report.claim is not None and report.claim.complete is False
    assert report.usable is False
    assert "not complete" in report.log_line()


def test_the_assertion_accepts_the_spellings_a_model_actually_writes(tmp_path: Path) -> None:
    for value, expected in (("true", True), ("YES", True), ("false", False), ("no", False)):
        _receipt(tmp_path, _claim(complete=value))
        report = read_receipt(tmp_path)
        assert report.errors == (), value
        assert report.claim is not None and report.claim.complete is expected, value


def test_a_present_but_uncoercible_field_is_an_error(tmp_path: Path) -> None:
    _receipt(tmp_path, _claim(unreviewed_count="many", limitations=5))

    report = read_receipt(tmp_path)

    assert report.claim is None
    assert any("unreviewed_count" in error for error in report.errors)
    assert any("limitations" in error for error in report.errors)


def test_a_boolean_is_never_read_as_a_count(tmp_path: Path) -> None:
    """``bool`` is an ``int`` in Python, so ``True`` would silently read as 1."""

    _receipt(tmp_path, _claim(unresolved_count=True))

    report = read_receipt(tmp_path)

    assert report.claim is None
    assert any("unresolved_count" in error for error in report.errors)


def test_agent_prose_is_bounded_on_the_way_in(tmp_path: Path) -> None:
    """``notes`` and ``limitations`` are free text that Nexus then persists."""

    _receipt(
        tmp_path,
        _claim(
            notes="n" * 10_000,
            limitations=[f"{index}: " + "l" * 900 for index in range(80)],
        ),
    )

    report = read_receipt(tmp_path)

    assert report.claim is not None
    assert len(report.claim.notes) == 2_000
    assert len(report.claim.limitations) == 20
    assert all(len(item) <= 300 for item in report.claim.limitations)


def test_candidate_counts_drop_keys_that_are_not_dispositions(tmp_path: Path) -> None:
    """The keys are agent-invented and reach a log line through
    reconciliation, so a key that is a sentence is not a disposition. A
    negative value drops rather than clamping: reading -1 as 0 would make a
    defective receipt agree with an empty ledger."""

    _receipt(
        tmp_path,
        _claim(
            candidate_counts={
                "material": 4,
                "a whole sentence explaining the estate": 2,
                "utility": "3",
                "operational": -1,
            }
        ),
    )

    report = read_receipt(tmp_path)

    assert report.claim is not None
    assert report.claim.candidate_counts == {"material": 4, "utility": 3}


def test_a_negative_count_is_uncoercible_not_clamped(tmp_path: Path) -> None:
    _receipt(tmp_path, _claim(unreviewed_count=-1))

    report = read_receipt(tmp_path)

    assert report.claim is None
    assert any("unreviewed_count" in error for error in report.errors)


def test_a_single_string_where_a_list_belongs_is_accepted(tmp_path: Path) -> None:
    _receipt(tmp_path, _claim(document_paths="INTEGRATION__orders.md"))

    report = read_receipt(tmp_path)

    assert report.claim is not None
    assert report.claim.document_paths == ("INTEGRATION__orders.md",)


# --------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------


def test_an_aligned_receipt_reconciles_clean(tmp_path: Path) -> None:
    knowledge = _knowledge()
    ledger = _ledger(
        classified={
            "u1": _classification("u1", "material", integrations=("int-0",)),
            "u2": _classification("u2", "material", integrations=("int-0",)),
            "u3": _classification("u3", "utility"),
        },
        counts={"material": 2, "utility": 1},
    )
    _receipt(tmp_path, _claim(coverage_digest=ledger.digest()))

    report = reconcile(
        read_receipt(tmp_path),
        ledger=ledger,
        knowledge=knowledge,
        scope_mode="whole_estate",
    )

    assert report.discrepancies == ()
    assert report.usable is True


def test_a_disagreement_names_both_numbers_and_both_sources(tmp_path: Path) -> None:
    """The wording matters. The agent counted what it knew when it wrote the
    receipt; a restart changes the truth, and that is not dishonesty."""

    ledger = _ledger(unreviewed=("u7", "u8", "u9"))
    _receipt(tmp_path, _claim(unreviewed_count=0, coverage_digest=ledger.digest()))

    report = reconcile(
        read_receipt(tmp_path), ledger=ledger, knowledge=None, scope_mode="whole_estate"
    )

    assert report.discrepancies == (
        "the receipt states 0 unreviewed candidate(s); the ledger now holds 3",
    )
    # A discrepancy does not by itself make the claim unusable: the gate blocks
    # on the recomputed side, never on the claim.
    assert report.usable is True


def test_an_unresolved_disposition_is_reconciled_against_the_ledgers_own_view(
    tmp_path: Path,
) -> None:
    ledger = _ledger(
        classified={
            "u1": _classification("u1", "unresolved"),
            "u2": _classification("u2", "material", integrations=("int-0",)),
        }
    )
    _receipt(tmp_path, _claim(unresolved_count=0, coverage_digest=ledger.digest()))

    report = reconcile(
        read_receipt(tmp_path), ledger=ledger, knowledge=None, scope_mode="whole_estate"
    )

    assert any("unresolved candidate(s)" in item for item in report.discrepancies)
    assert ledger.unresolved == ("u1",)


def test_a_stale_coverage_digest_is_reported_without_quoting_the_estate(
    tmp_path: Path,
) -> None:
    ledger = _ledger(classified={"u1": _classification("u1", "material", integrations=("i",))})
    _receipt(tmp_path, _claim(coverage_digest="f" * 64))

    report = reconcile(
        read_receipt(tmp_path), ledger=ledger, knowledge=None, scope_mode="whole_estate"
    )

    message = next(item for item in report.discrepancies if "coverage digest" in item)
    assert "f" * 12 in message
    assert ledger.digest()[:12] in message
    # Truncated: a full pair of hashes on a log line is unreadable.
    assert ledger.digest() not in message


def test_a_receipt_without_a_coverage_digest_is_a_valid_claim(tmp_path: Path) -> None:
    """The digest is optional, and it had to become so.

    ``LedgerReport.digest()`` is a sha256 computed by Nexus, and nothing writes
    it anywhere the agent can read WHILE THE SESSION RUNS -- ``write_summary``
    is called by the runner after the agent has stopped. Requiring it meant
    requiring a value the agent could only invent: empty (meaningless) or wrong
    (an advisory discrepancy on every real run, which is how an operator learns
    to ignore that warning).

    Nothing is lost. The binding it was meant to provide is stronger on the
    other side -- the gate re-reads the ledger from the digest-protected
    evidence instead of trusting any number in this file.
    """

    payload = _claim()
    payload.pop("coverage_digest", None)
    _receipt(tmp_path, payload)

    report = read_receipt(tmp_path)

    assert report.present is True
    assert report.errors == ()
    assert report.usable is True
    assert "coverage_digest" not in REQUIRED_KEYS

    # An absent digest is silence, not a disagreement.
    ledger = _ledger(classified={"u1": _classification("u1", "material", integrations=("i",))})
    reconciled = reconcile(report, ledger=ledger, knowledge=None, scope_mode="whole_estate")
    assert not [item for item in reconciled.discrepancies if "coverage digest" in item]


def test_model_counts_are_only_compared_when_a_model_was_loaded(tmp_path: Path) -> None:
    ledger = _ledger()
    _receipt(
        tmp_path,
        _claim(integration_count=9, journey_count=9, coverage_digest=ledger.digest()),
    )

    without = reconcile(
        read_receipt(tmp_path), ledger=ledger, knowledge=None, scope_mode="whole_estate"
    )
    with_model = reconcile(
        read_receipt(tmp_path),
        ledger=ledger,
        knowledge=_knowledge(integrations=2, journeys=1),
        scope_mode="whole_estate",
    )

    assert without.discrepancies == ()
    assert any("integration(s)" in item for item in with_model.discrepancies)
    assert any("journey(s)" in item for item in with_model.discrepancies)


def test_per_disposition_counts_are_compared_only_where_both_sides_speak(
    tmp_path: Path,
) -> None:
    ledger = _ledger(
        classified={"u1": _classification("u1", "material", integrations=("i",))},
        counts={"material": 1, "utility": 0},
    )
    _receipt(
        tmp_path,
        _claim(candidate_counts={"material": 5, "excluded": 3}, coverage_digest=ledger.digest()),
    )

    report = reconcile(
        read_receipt(tmp_path), ledger=ledger, knowledge=None, scope_mode="whole_estate"
    )

    assert any("'material'" in item for item in report.discrepancies)
    # 'excluded' is absent from the ledger's counts here: "the agent did not
    # break its count down that way" is not a disagreement about the estate.
    assert not any("'excluded'" in item for item in report.discrepancies)


def test_a_scope_mode_disagreement_is_reported_and_an_unstated_one_is_not(
    tmp_path: Path,
) -> None:
    ledger = _ledger()
    _receipt(tmp_path, _claim(scope_mode="focused", coverage_digest=ledger.digest()))
    stated = reconcile(
        read_receipt(tmp_path), ledger=ledger, knowledge=None, scope_mode="whole_estate"
    )

    _receipt(tmp_path, _claim(scope_mode="", coverage_digest=ledger.digest()))
    unstated = reconcile(
        read_receipt(tmp_path), ledger=ledger, knowledge=None, scope_mode="whole_estate"
    )

    assert any("scope mode" in item for item in stated.discrepancies)
    assert unstated.discrepancies == ()


def test_reconciling_an_absent_or_malformed_receipt_is_a_no_op(tmp_path: Path) -> None:
    """Inventing disagreements with a file that could not be read would bury
    the one finding that matters."""

    ledger = _ledger(unreviewed=("u1",))

    absent = reconcile(
        read_receipt(tmp_path), ledger=ledger, knowledge=None, scope_mode="whole_estate"
    )
    assert absent.present is False and absent.discrepancies == ()

    _receipt(tmp_path, "not json at all")
    malformed = reconcile(
        read_receipt(tmp_path), ledger=ledger, knowledge=None, scope_mode="whole_estate"
    )
    assert malformed.errors and malformed.discrepancies == ()


def test_the_claim_serialises_without_the_agents_free_text(tmp_path: Path) -> None:
    """``notes`` is prose. It is read and bounded so a caller can log the fact
    that it exists, and it is not carried into any artifact."""

    _receipt(tmp_path, _claim(notes="internal path /srv/scan/raw and a hunch"))

    claim = read_receipt(tmp_path).claim

    assert claim is not None and claim.notes
    assert "notes" not in claim.to_dict()


def test_discrepancies_are_bounded(tmp_path: Path) -> None:
    ledger = _ledger(
        unreviewed=tuple(f"u{index}" for index in range(50)),
        counts={name: index for index, name in enumerate("abcdefghij")},
    )
    _receipt(
        tmp_path,
        _claim(
            candidate_counts={name: 999 for name in "abcdefghij"},
            coverage_digest="z" * 64,
        ),
    )

    report = reconcile(
        read_receipt(tmp_path), ledger=ledger, knowledge=None, scope_mode="focused"
    )

    assert 0 < len(report.discrepancies) <= 20
