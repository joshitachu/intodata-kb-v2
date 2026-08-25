"""Entity-shape redaction for runtime error text bound for an LLM prompt.

The keyword filters were never the gap.  Measured over the 827 real failure
rows in the runtime mirror there are zero credentials, zero IPv4 literals and
zero email addresses -- but a customer-identifying object-store bucket and an
internal hosting FQDN pass through verbatim.  The cases below are the shapes
that corpus actually contains, plus the identifiers that must survive.

The preservation tests are not politeness: the documentation pipeline's
component-echo validator rejects any `tXxx_N` reference absent from the
evidence an agent was given, so redacting a component name here converts a real
runtime fact into a fabrication false-positive downstream.
"""

import pytest

from src.runtime.redaction import (
    MAX_ERROR_SUMMARY,
    redact_entities,
    sanitize_doc_evidence_error,
    sanitize_sync_error,
    strip_execution_ids,
)

# Verbatim from the live mirror, with the customer identifiers left in: these
# are exactly the strings that reached a prompt before this filter existed.
_REAL_GCS_FAILURE = (
    "Execution 2909d999-35ea-483b-a0ac-61d3c0a1dc35 failed with code 1 and error "
    "Job stopped with errors or unable to run. Exception in component "
    "tBigQueryOutput_1_tBQBE (VJP_Raw_itemApplicationEntries) "
    "com.google.cloud.bigquery.BigQueryException: Not found: Files "
    "gs://vaxxinova-talend/jpn_vjp_rawitemApp"
)
_REAL_FQDN_FAILURE = (
    "Exception in component tRESTClient_1 (Maximo2RHM_Workorders) "
    "javax.ws.rs.ProcessingException: java.net.SocketException: "
    "SocketException invoking https://mas.manage.infranl.almhosting"
)
_REAL_SQL_FAILURE = (
    "Exception in component tMSSqlConnection_2 (RHM2Smartflow_Workorders) "
    "java.sql.SQLException: Network error IOException: Connection timed out: connect"
)


class TestUriNormalisation:
    def test_bucket_uri_keeps_the_scheme_and_loses_the_customer(self):
        out = redact_entities("Not found: Files gs://vaxxinova-talend/jpn_vjp_raw/part-0.avro")
        assert out == "Not found: Files gs://<bucket>/<path>"

    def test_http_uri_keeps_the_scheme_and_loses_the_host_and_path(self):
        out = redact_entities("POST https://api.partner.example.com/v2/orders returned 500")
        assert out == "POST https://<host>/... returned 500"

    def test_uri_without_a_path_does_not_invent_one(self):
        assert redact_entities("invoking https://mas.manage.infranl.almhosting") == (
            "invoking https://<host>"
        )

    def test_multi_part_driver_scheme_survives(self):
        """`jdbc:jtds:sqlserver` names the driver -- that IS the diagnosis."""
        out = redact_entities("jdbc:jtds:sqlserver://SRVSQL07.corp.acme.nl:1433/WerkorderDB failed")
        assert out == "jdbc:jtds:sqlserver://<host>/... failed"


class TestBareHostRedaction:
    def test_bare_fqdn_becomes_a_placeholder(self):
        assert redact_entities("Connection refused to db-prod-01.internal.acme.example.com:1433") == (
            "Connection refused to <host>:1433"
        )

    def test_two_label_host_with_a_known_suffix(self):
        assert redact_entities("timeout contacting mailrelay.internal") == (
            "timeout contacting <host>"
        )

    def test_ipv4_literal_becomes_a_placeholder(self):
        assert redact_entities("cannot reach 10.42.7.19:8080") == "cannot reach <host>:8080"

    @pytest.mark.parametrize(
        "text",
        [
            "java.sql.SQLException",
            "com.google.cloud.bigquery.BigQueryException",
            "com.sun.xml.messaging.saaj.SOAPExceptionImpl",
            "javax.ws.rs.ProcessingException",
            "java.lang.NullPointerException",
            "org.apache.camel.CamelExecutionException",
        ],
    )
    def test_qualified_exception_classes_are_never_mistaken_for_hosts(self, text):
        """The exception class is the fault; an address is not."""
        assert redact_entities(text) == text

    @pytest.mark.parametrize("text", ["Web.config", "application.properties", "scan_result.json"])
    def test_filenames_are_not_hosts(self, text):
        assert redact_entities(text) == text


class TestComponentNamesArePreserved:
    """R12: runtime evidence is a legitimate component-echo source.

    A component name that a hostname or identifier rule ate would be rejected by
    the downstream fabrication validator as invented, turning a true production
    fact into a false positive.  Every component shape present in the live
    corpus is pinned here.
    """

    @pytest.mark.parametrize(
        "component",
        [
            "tMSSqlConnection_2",
            "tMSSqlRow_1",
            "tMSSqlOutput_5",
            "tWebServiceInput_1",
            "tSetGlobalVar_3",
            "tBigQueryOutput_1_tBQBE",
            "tBigQueryOutput_2_tBQBE",
            "tSalesforceInput_1",
            "tRESTClient_1",
            "tSOAP_1",
            "tRunJob_1",
            "tDBConnection_2",
        ],
    )
    def test_component_survives_every_filter(self, component):
        text = f"Exception in component {component} (SomeJob_Name) java.lang.RuntimeException"
        assert component in redact_entities(text)
        assert component in sanitize_doc_evidence_error(text)

    def test_job_names_survive(self):
        """Job names are the join key back to the scan; losing one loses the row."""
        out = sanitize_doc_evidence_error(_REAL_SQL_FAILURE)
        assert "RHM2Smartflow_Workorders" in out
        assert "tMSSqlConnection_2" in out


class TestExecutionIds:
    def test_stripped_from_prompt_facing_text(self):
        out = sanitize_doc_evidence_error(
            "Failed to acquire lock for d09002d9-6197-4ff5-9d0e-550391a3678c"
        )
        assert out == "Failed to acquire lock for <execution-id>"

    def test_kept_in_the_stored_record(self):
        """The operator's handle back into the source system stays intact."""
        out = sanitize_sync_error(
            "Failed to acquire lock for d09002d9-6197-4ff5-9d0e-550391a3678c"
        )
        assert out == "Failed to acquire lock for d09002d9-6197-4ff5-9d0e-550391a3678c"

    def test_identical_failures_collapse_to_identical_text(self):
        """Two occurrences of one fault must not read as two different faults."""
        first = sanitize_doc_evidence_error(
            "Execution e8af87b7-3bc8-4ce9-b0de-389247eb0b74 failed with code 1"
        )
        second = sanitize_doc_evidence_error(
            "Execution 78c3e990-162c-48e5-a67d-d21234b7775b failed with code 1"
        )
        assert first == second

    def test_stripping_precedes_truncation(self):
        """Removing 22 characters of noise must buy back diagnostic text."""
        long_tail = "A" * (MAX_ERROR_SUMMARY - 60)
        text = f"Execution e8af87b7-3bc8-4ce9-b0de-389247eb0b74 failed: {long_tail} TAILMARKER"
        assert "TAILMARKER" in sanitize_doc_evidence_error(text)
        assert "TAILMARKER" not in sanitize_sync_error(text)


class TestRealCorpusRows:
    def test_gcs_bucket_never_reaches_a_prompt(self):
        out = sanitize_doc_evidence_error(_REAL_GCS_FAILURE)
        assert "vaxxinova" not in out
        assert "tBigQueryOutput_1_tBQBE" in out
        assert "BigQueryException" in out
        assert "Not found" in out

    def test_internal_fqdn_never_reaches_a_prompt(self):
        out = sanitize_doc_evidence_error(_REAL_FQDN_FAILURE)
        assert "infranl" not in out and "almhosting" not in out
        assert "https://<host>" in out
        assert "tRESTClient_1" in out
        assert "java.net.SocketException" in out

    def test_credentials_still_go(self):
        out = sanitize_doc_evidence_error(
            "Connect failed for user admin password=hunter2 at db.internal"
        )
        assert "hunter2" not in out
        assert "<redacted>" in out
        assert "<host>" in out


class TestBoundaries:
    def test_none_and_empty_stay_none(self):
        assert sanitize_doc_evidence_error(None) is None
        assert sanitize_doc_evidence_error("") is None
        assert sanitize_sync_error(None) is None

    def test_output_is_bounded(self):
        assert len(sanitize_doc_evidence_error("x" * 5000)) == MAX_ERROR_SUMMARY

    def test_redaction_output_stays_ascii(self):
        """Anything reaching a generated document must survive cp1252."""
        out = sanitize_doc_evidence_error(_REAL_GCS_FAILURE)
        out.encode("ascii")

    def test_strip_execution_ids_is_case_insensitive(self):
        assert strip_execution_ids("D09002D9-6197-4FF5-9D0E-550391A3678C") == "<execution-id>"
