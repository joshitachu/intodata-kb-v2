"""Causal-sentence selection and context association.

Two distinct failures are pinned here.

1. Resolving the ROOT EXCEPTION and choosing the READABLE SENTENCE are different
   jobs. The deepest ``Caused by:`` is by definition the root exception, but a
   wrapped transport failure bottoms out in a generic protocol status, so taking
   that line as the sentence discards the diagnosis. Verbatim from stored ``cdc``
   data, the fault was surfaced as "400 Bad Request" while the message's own first
   line named the offending dataset and the rule it broke.

2. The parser is per-line, so a fault line with no text of its own extracted
   nothing even when the neighbouring line explained it. Stored ``werkorder`` data
   contains 1,344 such lines: a CXF client logs the diagnosis as a warning and then
   a bare "Failed request" as the error.

Association is deliberately narrow, and the negative cases below carry the weight:
lending a sentence too eagerly would attach one fault's explanation to an
unrelated error, which reads as authoritative and is wrong.
"""

import dataclasses

import pytest

from src.runtime.insights.builder import (
    DIALECT_CONTEXT,
    associate_context_signals,
    normalize_severity,
)
from src.runtime.insights.parser import (
    _informativeness,
    parse_runtime_log_message,
)

# --- Verbatim message bodies ----------------------------------------------

# The Caused-by chain bottoms out in a bare status; line 1 holds the diagnosis.
BIGQUERY_WRAPPED = (
    'tBigQueryOutput_2_tBQBE Invalid dataset ID "test_ zaf_del_clean". Dataset IDs '
    "must be alphanumeric (plus underscores and dashes) and must be at most 1024 "
    "characters long.\n"
    'com.google.cloud.bigquery.BigQueryException: Invalid dataset ID "test_ zaf_del_clean".\n'
    "\tat com.google.cloud.bigquery.spi.v2.HttpBigQueryRpc.translate(HttpBigQueryRpc.java:115)\n"
    "Caused by: com.google.api.client.googleapis.json.GoogleJsonResponseException: "
    "400 Bad Request"
)
CXF_INTERCEPTOR_WARNING = (
    "Interceptor for {https://SRVA0673:9200/talend-esb-monitoring-alias}WebClient "
    "has thrown exception, unwinding now"
)
# 15 characters including the trailing space, exactly as stored.
CONTENTLESS_ERROR = "Failed request "
ORACLE_SEEDED_TIMEOUT = (
    "[tOracleConnection_1] Connection attempt 3/3 failed after 30000ms: "
    "ORA-12170: TNS:Connect timeout occurred"
)


@dataclasses.dataclass
class _FakeLogRow:
    """Minimal stand-in: association reads only ``severity``."""

    severity: str


def _rows(*severities: str) -> list[_FakeLogRow]:
    return [_FakeLogRow(severity=s) for s in severities]


class TestInformativeness:
    """The scorer must rank on stated meaning, never on length."""

    def test_a_bare_protocol_status_scores_below_a_stated_rule(self):
        status = (
            "Caused by: com.google.api.client.googleapis.json."
            "GoogleJsonResponseException: 400 Bad Request"
        )
        diagnosis = (
            'Invalid dataset ID "test_ zaf_del_clean". Dataset IDs must be alphanumeric'
        )
        assert _informativeness(diagnosis) > _informativeness(status)

    def test_a_stack_frame_never_outranks_prose(self):
        frame = (
            "\tat com.google.cloud.bigquery.spi.v2.HttpBigQueryRpc."
            "translate(HttpBigQueryRpc.java:115)"
        )
        # Longer than the diagnosis, so a length heuristic would pick it.
        assert len(frame) > len("Data truncation")
        assert _informativeness(frame) < _informativeness("Data truncation")

    def test_an_exception_class_with_no_detail_is_penalised(self):
        assert _informativeness("java.sql.BatchUpdateException:") < 0

    def test_an_empty_candidate_is_rejected_outright(self):
        assert _informativeness("   ") < 0


class TestCausalSentenceSelection:
    def test_the_wrapped_bigquery_fault_yields_the_line_that_states_the_rule(self):
        parsed = parse_runtime_log_message(BIGQUERY_WRAPPED)

        assert parsed.causal_message is not None
        assert "Invalid dataset ID" in parsed.causal_message
        assert "400 Bad Request" not in parsed.causal_message

    def test_the_root_exception_class_is_still_the_deepest_caused_by(self):
        """The sentence changed; the exception resolution must not."""
        parsed = parse_runtime_log_message(BIGQUERY_WRAPPED)

        assert parsed.exception_class == "GoogleJsonResponseException"

    def test_the_seeded_oracle_dialect_is_unchanged(self):
        """Regression guard: this resolves oracle.net.timeout at high confidence."""
        parsed = parse_runtime_log_message(ORACLE_SEEDED_TIMEOUT)

        assert parsed.error_code == "ORA-12170"
        assert parsed.causal_message == (
            "Connection attempt 3/3 failed after 30000ms: ORA-12170: "
            "TNS:Connect timeout occurred"
        )

    def test_a_contentless_line_yields_no_sentence_on_its_own(self):
        parsed = parse_runtime_log_message(CONTENTLESS_ERROR)

        assert parsed.causal_message is None
        assert parsed.dialect is None


class TestContextAssociation:
    def test_a_contentless_error_adopts_the_adjacent_warning(self):
        rows = _rows("warning", "error")
        parsed = [
            parse_runtime_log_message(CXF_INTERCEPTOR_WARNING),
            parse_runtime_log_message(CONTENTLESS_ERROR),
        ]

        adopted = associate_context_signals(rows, parsed)

        assert adopted == 1
        assert parsed[1].causal_message == parsed[0].causal_message
        assert "Interceptor for" in parsed[1].causal_message

    def test_the_borrowed_sentence_is_marked_as_inherited(self):
        """Provenance: a borrowed sentence must never look self-reported."""
        rows = _rows("warning", "error")
        parsed = [
            parse_runtime_log_message(CXF_INTERCEPTOR_WARNING),
            parse_runtime_log_message(CONTENTLESS_ERROR),
        ]

        associate_context_signals(rows, parsed)

        assert parsed[1].dialect == DIALECT_CONTEXT
        # The donor keeps its own provenance.
        assert parsed[0].dialect != DIALECT_CONTEXT

    def test_only_the_sentence_is_borrowed_never_the_diagnostic_signals(self):
        """exception_class and error_code drive the signature hash and the
        confidence tier. A line that reported neither must not inherit either."""
        rows = _rows("error", "error")
        donor = parse_runtime_log_message(
            "tMSSqlOutput_5 Data truncation\njava.sql.BatchUpdateException: Data truncation"
        )
        parsed = [donor, parse_runtime_log_message(CONTENTLESS_ERROR)]
        assert donor.exception_class == "BatchUpdateException"

        associate_context_signals(rows, parsed)

        assert parsed[1].causal_message is not None
        assert parsed[1].exception_class is None
        assert parsed[1].error_code is None
        assert parsed[1].component_name is None
        assert parsed[1].confidence is None

    def test_info_chatter_between_the_pair_is_not_a_boundary(self):
        rows = _rows("warning", "info", "error")
        parsed = [
            parse_runtime_log_message(CXF_INTERCEPTOR_WARNING),
            parse_runtime_log_message("Successfully connected to tcp://host:61616"),
            parse_runtime_log_message(CONTENTLESS_ERROR),
        ]

        assert associate_context_signals(rows, parsed) == 1
        assert "Interceptor for" in parsed[2].causal_message

    def test_a_line_that_spoke_for_itself_is_never_overwritten(self):
        rows = _rows("warning", "error")
        parsed = [
            parse_runtime_log_message(CXF_INTERCEPTOR_WARNING),
            parse_runtime_log_message(
                "tMSSqlOutput_5 Data truncation\n"
                "java.sql.BatchUpdateException: Data truncation"
            ),
        ]
        own_sentence = parsed[1].causal_message

        assert associate_context_signals(rows, parsed) == 0
        assert parsed[1].causal_message == own_sentence
        assert parsed[1].dialect != DIALECT_CONTEXT

    def test_a_contentless_donor_stops_the_search_rather_than_reaching_further_back(
        self,
    ):
        """The nearest fault line is the only donor considered. Reaching past it
        would attach an older, unrelated fault's explanation to this error."""
        rows = _rows("error", "error", "error")
        parsed = [
            parse_runtime_log_message(
                "tMSSqlOutput_5 Data truncation\n"
                "java.sql.BatchUpdateException: Data truncation"
            ),
            parse_runtime_log_message(CONTENTLESS_ERROR),
            parse_runtime_log_message(CONTENTLESS_ERROR),
        ]

        adopted = associate_context_signals(rows, parsed)

        # Row 1 adopts from row 0. Row 2's nearest fault donor is row 1, which is
        # itself context-derived, so row 2 stays empty.
        assert adopted == 1
        assert parsed[1].causal_message is not None
        assert parsed[2].causal_message is None

    def test_a_donor_beyond_the_lookback_window_is_not_reached(self):
        rows = _rows("warning", "info", "info", "info", "error")
        parsed = [
            parse_runtime_log_message(CXF_INTERCEPTOR_WARNING),
            *[parse_runtime_log_message("heartbeat ok") for _ in range(3)],
            parse_runtime_log_message(CONTENTLESS_ERROR),
        ]

        assert associate_context_signals(rows, parsed) == 0
        assert parsed[-1].causal_message is None

    def test_info_severity_lines_never_adopt(self):
        """Association explains faults. Borrowing onto routine chatter would
        manufacture errors that the runtime never reported."""
        rows = _rows("warning", "info")
        parsed = [
            parse_runtime_log_message(CXF_INTERCEPTOR_WARNING),
            parse_runtime_log_message("logs are sent"),
        ]

        assert associate_context_signals(rows, parsed) == 0
        assert parsed[1].causal_message is None

    def test_association_is_idempotent(self):
        """The backfill re-runs the build path; a second pass must not chain."""
        rows = _rows("warning", "error")
        parsed = [
            parse_runtime_log_message(CXF_INTERCEPTOR_WARNING),
            parse_runtime_log_message(CONTENTLESS_ERROR),
        ]

        first = associate_context_signals(rows, parsed)
        sentence = parsed[1].causal_message
        second = associate_context_signals(rows, parsed)

        assert (first, second) == (1, 0)
        assert parsed[1].causal_message == sentence


class TestSeverityNormalisationContract:
    """Association keys off canonical severities, so the aliases must hold."""

    @pytest.mark.parametrize("raw", ["ERROR", "severe", "Fatal", "critical"])
    def test_error_aliases(self, raw):
        assert normalize_severity(raw) == "error"

    @pytest.mark.parametrize("raw", ["WARN", "Warning"])
    def test_warning_aliases(self, raw):
        assert normalize_severity(raw) == "warning"
