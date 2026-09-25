"""Tests for the runtime measurement-provenance gate.

Two properties are being pinned, and the second matters as much as the first:

1. A published runtime figure without its measurement reference (window +
   denominator or register) is reported, and a figure no probe returned is
   reported.
2. Ordinary scan-derived prose -- job counts, component counts, coverage
   percentages, configuration values -- is NOT reported. A gate that fires on
   "the project has 429 jobs" is a gate an operator switches off, at which
   point it protects nothing.
"""

from __future__ import annotations

from mcp_server.providers.custom.measurement_provenance import (
    find_runtime_measurement_claims,
    ledger_figures,
    ledger_records_a_probe,
    measurement_provenance_errors,
    measurement_reference,
)

from tests.test_providers.method_source import method

#: One entry in the shape the shipped CLI actually writes
#: (``scripts/runtime_tools.py::_ledger_entry`` + ``digest_result``): the
#: request, the outcome, and the result's SHAPE -- never its values.
SHIPPED_LEDGER_ENTRY = {
    "ts": "2026-09-16T10:00:00Z",
    "command": "call",
    "tool": "job_reliability",
    "project_id": "p",
    "config_source": "sidecar",
    "arguments": {"job_name": "Maximo2RHM", "window_days": 30},
    "overrode_project_id": False,
    "success": True,
    "error_code": None,
    "duration_ms": 412,
    "digest": {
        "type": "object",
        "keys": ["runs", "failures", "failure_rate"],
        "key_count": 3,
        "counts": {},
    },
}

# The shape a correctly-referenced claim has: register, window, denominator.
REFERENCED = (
    "Over the last 30 days the PROD execution register in TMC recorded 17 "
    "failed runs out of 2,122 executions, a 0.8% failure rate."
)

# What the probe that produced it wrote to _assessment/runtime_probe_ledger.jsonl.
LEDGER_OK = [
    {
        "tool": "job_reliability",
        "arguments": {"job_name": "Maximo2RHM", "window_days": 30, "environment": "PROD"},
        "result": {"runs": 2122, "failures": 17, "failure_rate": 0.008},
    }
]


# --------------------------------------------------------------------------- #
# Reference check -- what it catches
# --------------------------------------------------------------------------- #


def test_properly_referenced_claim_passes():
    # Non-vacuous: the sentence IS detected as a runtime claim, and clears.
    assert [c.kind for c in find_runtime_measurement_claims(REFERENCED)] == ["rate"]
    assert measurement_provenance_errors(REFERENCED) == []


def test_bare_rate_claim_is_rejected():
    # The verified failure: the same estate reads as 0.8% or 60.7% depending on
    # the register and the denominator, and this sentence names neither.
    errors = measurement_provenance_errors("The Maximo interface fails 23% of the time.")
    assert len(errors) == 1
    assert "no time window" in errors[0]
    assert "no denominator and no named register" in errors[0]
    assert "0.087%" in errors[0] and "30.8%" in errors[0]


def test_window_without_denominator_is_rejected():
    errors = measurement_provenance_errors(
        "Over the last 30 days the interface failed 23% of the time."
    )
    assert len(errors) == 1
    assert "no time window" not in errors[0]
    assert "no denominator and no named register" in errors[0]


def test_register_without_window_is_rejected():
    errors = measurement_provenance_errors(
        "The TMC execution register shows 785 of 3,392 job runs failing."
    )
    assert len(errors) == 1
    assert "no time window" in errors[0]


def test_register_and_window_without_a_denominator_is_accepted():
    # A named register plus a window is the weaker of the two accepted forms:
    # the register is what makes the denominator recoverable by the reader.
    text = (
        "Over the last 30 days the TMC execution register reports a 0.8% "
        "failure rate for this interface."
    )
    assert [c.kind for c in find_runtime_measurement_claims(text)] == ["rate"]
    assert measurement_provenance_errors(text) == []


def test_duration_percentile_claim_needs_a_reference():
    errors = measurement_provenance_errors("The p95 execution duration is 42 s.")
    assert len(errors) == 1
    assert "duration" in errors[0]


def test_cadence_claim_needs_a_reference():
    errors = measurement_provenance_errors("The job runs every 30 minutes.")
    assert len(errors) == 1
    assert "cadence" in errors[0]


def test_referenced_cadence_passes():
    text = (
        "Across the last 30 days of TMC execution history the job ran every "
        "30 minutes in PROD, based on 1,440 runs."
    )
    assert [c.kind for c in find_runtime_measurement_claims(text)] == ["cadence"]
    assert measurement_provenance_errors(text) == []


# --------------------------------------------------------------------------- #
# Reference check -- what it must never catch
# --------------------------------------------------------------------------- #


def test_scan_derived_counts_are_not_flagged():
    text = (
        "The project has 429 jobs across 20 groups. OrderLoad_Daily has 12 "
        "components and 3 tMap expressions, and the target table carries 44 "
        "columns."
    )
    assert find_runtime_measurement_claims(text) == []
    assert measurement_provenance_errors(text) == []


def test_scan_derived_percentages_are_not_flagged():
    text = (
        "55% of designs are documented, 12% of designs fail the naming "
        "convention, and 85% of the runtime configuration values are "
        "environment-specific."
    )
    assert measurement_provenance_errors(text) == []


def test_static_failure_language_is_not_a_runtime_count():
    # "2 points of failure" is not two failures; only a closed adjective list
    # may sit between the number and the runtime noun.
    text = "2 points of failure remain, and 15% of designs have a single point of failure."
    assert measurement_provenance_errors(text) == []


def test_configured_retry_counts_are_not_runtime_counts():
    assert measurement_provenance_errors("The job has 4 retries configured.") == []


def test_designs_run_without_error_handling_is_not_a_rate_claim():
    # Bare "run" is a verb in static-analysis prose; only "runs", "executions"
    # and friends mark a unit as runtime.
    assert (
        measurement_provenance_errors(
            "85% of designs run without any error-handling component."
        )
        == []
    )


# --------------------------------------------------------------------------- #
# Scope: where the reference may live
# --------------------------------------------------------------------------- #


def test_table_row_inherits_caption_window_and_register():
    text = (
        "## Interface reliability\n"
        "\n"
        "Measured over the last 30 days against the TMC execution register "
        "(PROD only):\n"
        "\n"
        "| Interface  | Runs  | Failures | Failure rate |\n"
        "|------------|-------|----------|--------------|\n"
        "| Maximo2RHM | 2,122 | 17       | 0.8%         |\n"
    )
    claims = find_runtime_measurement_claims(text)
    assert len(claims) == 1
    assert claims[0].figures == ("0.8", "2,122", "17")
    assert measurement_provenance_errors(text) == []


def test_table_row_without_a_caption_is_rejected():
    text = (
        "| Interface  | Failure rate |\n"
        "|------------|--------------|\n"
        "| Maximo2RHM | 23.1%        |\n"
    )
    errors = measurement_provenance_errors(text)
    assert len(errors) == 1
    assert "no time window" in errors[0]


def test_scan_table_is_not_flagged():
    text = (
        "| Job          | Components | Columns |\n"
        "|--------------|------------|---------|\n"
        "| OrderLoad    | 12         | 44      |\n"
    )
    assert measurement_provenance_errors(text) == []


def test_a_claim_never_borrows_a_reference_from_the_line_below_it():
    # A bullet list where only the SECOND bullet is referenced. Inheriting
    # forwards would let one honest line launder every bare figure near it.
    text = (
        "- The Maximo interface fails 23% of the time.\n"
        "- Over the last 30 days the TMC execution register recorded 785 of "
        "3,392 job runs as failed.\n"
    )
    errors = measurement_provenance_errors(text)
    assert len(errors) == 1
    assert "23" in errors[0]


def test_a_claim_may_inherit_a_reference_stated_before_it():
    text = (
        "All runtime figures below cover the last 30 days of the TMC execution "
        "register, PROD only. The Maximo interface failed 17 of 2,122 "
        "executions.\n"
    )
    assert [c.kind for c in find_runtime_measurement_claims(text)] == ["count"]
    assert measurement_provenance_errors(text) == []


# --------------------------------------------------------------------------- #
# Ledger cross-check
# --------------------------------------------------------------------------- #


def test_ledger_cross_check_catches_an_invented_figure():
    # The probe that ran returned 2,122 runs / 17 failures for PROD; the
    # document publishes a different estate's numbers.
    text = (
        "Over the last 30 days the TMC execution register recorded 785 of "
        "3,392 job runs as failed."
    )
    errors = measurement_provenance_errors(text, ledger=LEDGER_OK)
    assert len(errors) == 1
    assert "no probe" in errors[0]
    assert "3,392" in errors[0]


def test_ledger_cross_check_accepts_the_figures_a_probe_returned():
    assert measurement_provenance_errors(REFERENCED, ledger=LEDGER_OK) == []


def test_ledger_cross_check_tolerates_published_rounding():
    # The ledger holds the fraction 0.23076; the document publishes 23.1%.
    ledger = [{"tool": "job_reliability", "result": {"runs": 3392, "failures": 785, "failure_rate": 0.23076}}]
    text = (
        "Over the last 30 days the TMC execution register recorded 785 of "
        "3,392 job runs as failed, a 23.1% failure rate."
    )
    assert find_runtime_measurement_claims(text)[0].figures == ("3,392", "23.1")
    assert measurement_provenance_errors(text, ledger=ledger) == []


def test_ledger_none_skips_the_cross_check():
    text = (
        "Over the last 30 days the TMC execution register recorded 785 of "
        "3,392 job runs as failed."
    )
    assert measurement_provenance_errors(text, ledger=None) == []


def test_empty_ledger_is_a_different_fact_from_no_ledger():
    # [] means the ledger exists and no probe ran: nothing is provenanced.
    errors = measurement_provenance_errors(REFERENCED, ledger=[])
    assert len(errors) == 1
    assert "no probe that returned a value" in errors[0]


def test_a_failed_probe_provenances_nothing():
    ledger = [
        {
            "tool": "job_reliability",
            "error": "ConnectError: runtime mirror unreachable",
            "result": {"runs": 2122, "failures": 17},
        }
    ]
    assert ledger_figures(ledger) == set()
    errors = measurement_provenance_errors(REFERENCED, ledger=ledger)
    assert len(errors) == 1
    assert "no probe that returned a value" in errors[0]


def test_field_unmapped_probe_does_not_legitimise_a_published_zero():
    # Rule 3: a zero from "this field does not exist" and a zero from "it
    # exists and is empty" are different facts. The first may not provenance
    # the second.
    ledger = [
        {
            "tool": "failure_census",
            "result": {"failures": 0, "field_unmapped": True, "field": "error.code"},
        }
    ]
    assert ledger_figures(ledger) == set()
    errors = measurement_provenance_errors(
        "Over the last 30 days the TMC execution register recorded 0 failures "
        "across 2,122 runs.",
        ledger=ledger,
    )
    assert len(errors) == 1
    assert "no probe that returned a value" in errors[0]


def test_shape_only_ledger_does_not_accuse_the_document():
    # The shipped CLI records "keys and counts, never values" so that
    # _assessment/ never holds an unredacted payload. Probes DID run, so the
    # value-level check cannot be performed -- and must not be faked.
    assert ledger_records_a_probe([SHIPPED_LEDGER_ENTRY]) is True
    assert ledger_figures([SHIPPED_LEDGER_ENTRY]) == set()
    assert measurement_provenance_errors(REFERENCED, ledger=[SHIPPED_LEDGER_ENTRY]) == []


def test_shape_only_ledger_still_reports_when_no_probe_succeeded():
    failed = {**SHIPPED_LEDGER_ENTRY, "success": False, "error_code": "transport_error"}
    assert ledger_records_a_probe([failed]) is False
    errors = measurement_provenance_errors(REFERENCED, ledger=[failed])
    assert len(errors) == 1
    assert "no probe that returned a value" in errors[0]


def test_ledger_shape_digest_is_not_a_measurement_value():
    # A row count of 17 in the digest must not provenance a published "17
    # failures", and an HTTP 404 in error_code must not provenance anything.
    entry = {
        "tool": "t",
        "success": True,
        "error_code": 404,
        "digest": {"type": "array", "rows": 17, "item_keys": []},
    }
    assert ledger_figures([entry]) == set()


def test_ledger_request_arguments_are_not_measurement_values():
    # The window argument (30) must not provenance a published "30%".
    ledger = [{"tool": "job_reliability", "arguments": {"window_days": 30}, "result": {}}]
    assert ledger_figures(ledger) == set()


def test_ok_false_entry_is_unusable():
    assert ledger_figures([{"tool": "t", "ok": False, "result": {"runs": 99}}]) == set()
    assert ledger_figures([{"tool": "t", "status": "timeout", "result": {"runs": 99}}]) == set()
    assert ledger_figures([{"tool": "t", "result": {"runs": 99}}]) == {99.0}


def test_numeric_strings_in_a_ledger_are_harvested():
    assert ledger_figures([{"tool": "t", "result": {"rate": "0.8%", "runs": "2,122"}}]) == {
        0.8,
        2122.0,
    }


# --------------------------------------------------------------------------- #
# Shape of the output
# --------------------------------------------------------------------------- #


def test_errors_are_capped():
    text = "\n\n".join(
        f"The {name} interface fails 23% of the time." for name in "abcdefghij"
    )
    errors = measurement_provenance_errors(text)
    assert len(errors) == 5


def test_reference_helper_reports_each_part():
    reference = measurement_reference(REFERENCED)
    assert reference.window == "last 30 days"
    assert reference.denominator == "out of 2,122"
    assert reference.register == "execution register"
    assert reference.satisfied is True
    assert reference.missing == ()


def test_execution_count_phrased_as_times_is_a_claim():
    assert (
        measurement_provenance_errors("The interface ran 2,122 times.")
        != []
    )
    assert (
        measurement_provenance_errors(
            "The interface ran 2,122 times in the last 30 days, 17 of them failing."
        )
        == []
    )


def test_static_invocation_counts_are_not_execution_counts():
    assert (
        measurement_provenance_errors(
            "The shared joblet is invoked 4 times across the estate."
        )
        == []
    )


# --------------------------------------------------------------------------- #
# The prompt and the gate must agree
# --------------------------------------------------------------------------- #


def test_the_method_carries_the_runtime_evidence_block():
    """The block moved from the prompt into ``data/generation_method/METHOD.md``
    when the 47 KB prompt collapsed to a run brief. The rules are unchanged, so
    the assertion followed them rather than being deleted with the f-string."""
    body = method()
    assert "RUNTIME EVIDENCE" in body
    for command in ("runtime_tools.py list", "runtime_tools.py call", "runtime_tools.py elastic"):
        assert command in body
    # The block must not contradict the standing ban on MCP tools.
    assert "a local script run with Bash, not an MCP tool" in body
    for rule in ("field_unmapped", "0.087%", "30.8%", "23.1%", "60.7%", "SCHEDULED TASKS"):
        assert rule in body


def test_the_methods_own_examples_behave_as_the_method_says():
    # The block teaches by example. If the gate disagrees with the example, one
    # of the two is wrong and the writer is caught between them.
    # The method wraps its prose, so compare against a whitespace-normalised copy.
    body = method()
    good = (
        "Over the last 30 days the PROD execution register recorded 17 failed "
        "runs out of 2,122"
    )
    good_prose = "The interface ran 2,122 times in the last 30 days, 17 of them failing"
    bad = "Fails 23% of the time"
    assert good in body and good_prose in body and bad in body
    assert measurement_provenance_errors(good + ".") == []
    assert measurement_provenance_errors(good_prose + ".") == []
    assert measurement_provenance_errors(bad + ".") != []


def test_empty_text_is_silent():
    assert measurement_provenance_errors("") == []
    assert measurement_provenance_errors("", ledger=[]) == []
    assert find_runtime_measurement_claims("") == []


def test_claim_records_its_line_and_kind():
    text = "# Runtime\n\nSome prose.\n\nThe job runs every 30 minutes.\n"
    claims = find_runtime_measurement_claims(text)
    assert [(c.line, c.kind, c.figures) for c in claims] == [(5, "cadence", ("30",))]
    assert claims[0].values == (30.0,)
