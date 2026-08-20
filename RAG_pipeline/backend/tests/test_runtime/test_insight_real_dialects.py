"""Extraction tests against the log dialects real runtimes actually emit.

Every message body here is copied from stored runtime data (werkorder / cdc /
alitalend), reduced only in length. The original parser was calibrated against the
seeded fixtures in ``src/runtime/store/seed.py`` — which use a ``[component]``
prefix and Oracle-style JDBC URLs — and consequently extracted almost nothing from
these shapes: 143 component names out of 36,727 stored rows. These tests pin the
real shapes so that regression cannot recur silently.

The NOISE cases matter as much as the positive ones: a parser that invents a
component or an error code for an infrastructure warning produces confidently
wrong incidents, which is worse than no incident at all.
"""

import pytest

from src.runtime.insights.catalog import CATALOG, FailurePattern
from src.runtime.insights.parser import parse_runtime_log_message

# --- Verbatim message bodies (shortened stack traces) ----------------------

SQLSERVER_TRUNCATION = (
    "tMSSqlOutput_5 Data truncation\n"
    "java.sql.BatchUpdateException: Data truncation\n"
    "\tat net.sourceforge.jtds.jdbc.JtdsStatement.executeBatch(JtdsStatement.java:1069)\n"
    "\tat vq_mx2rhm.maximo2rhm_workorders_0_4.Maximo2RHM_Workorders."
    "tHashInput_1Process(Maximo2RHM_Workorders.java:16023)"
)
TMC_STATUS_LINE = (
    "68a5a9da85be54b2bec60f70:17.5/082a7954-905f-494d-b531-fe394e274320: "
    "Execution 082a7954-905f-494d-b531-fe394e274320 failed with code 1 and error "
    "Job stopped with errors or unable to run. Exception in component tMSSqlOutput_5 "
    "(Maximo2RHM_Workorders) java.sql.BatchUpdateException: Data truncation"
)
BIGQUERY_INVALID_DATASET = (
    'tBigQueryOutput_2_tBQBE Invalid dataset ID "test_ zaf_del_clean". Dataset IDs must be '
    "alphanumeric (plus underscores and dashes) and must be at most 1024 characters long.\n"
    'com.google.cloud.bigquery.BigQueryException: Invalid dataset ID "test_ zaf_del_clean".'
)
BIGQUERY_NOT_FOUND = (
    "tBigQueryOutput_1_tBQBE Not found: Files "
    "gs://vaxxinova-talend/jpn_vjp_rawitemApplicationEntriesDelta.csv\n"
    "com.google.cloud.bigquery.BigQueryException: Not found: Files "
    "gs://vaxxinova-talend/jpn_vjp_rawitemApplicationEntriesDelta.csv"
)
SALESFORCE_INVALID_FIELD = (
    "tSalesforceInput_1 [InvalidFieldFault [ApiQueryFault [ApiFault  "
    "exceptionCode='INVALID_FIELD'\n exceptionMessage='\n"
    "UserPermissionsSFContentUser, UserPermissionsKnowledgeUser\n"
    "ERROR at Row:1:Column:1146\n"
    "No such column 'UserPermissionsKnowledgeUser' on entity 'User'."
)
SQLSERVER_CONN_RESET = "tMSSqlConnection_2 I/O Error: Connection reset by peer: socket write error"
CAMEL_EXHAUSTED = (
    "Failed delivery for (MessageId: ID:SRVA0971-51409-1783919758034-5:182:1:1:1 on "
    "ExchangeId: ED10F3456ED4EA8-00000000000001B7). Exhausted after delivery attempt: 3 "
    "caught: javax.ws.rs.ProcessingException: java.net.ConnectException: ConnectException "
    "invoking https://SRVA0673:9200/talend-esb-monitoring-alias/_doc: Connection timed out: connect"
)
SEEDED_ORACLE = (
    "[tOracleConnection_1] Connection attempt 3/3 failed after 30000ms: "
    "ORA-12170: TNS:Connect timeout occurred"
)

# Infrastructure noise that must NOT yield a component or an error code.
NOISE_CONTENTLESS = "Failed request"
NOISE_KARAF = "WARNING: package org.apache.karaf.specs.locator not in java.base"
NOISE_PROPERTY = (
    "!!! PROPERTY NOT FOUND !!!: the key "
    "'org.talend.remote.jobserver.server.TalendJobServer.COMMAND_SERVER_HOST' "
    "can't be found in the JobServer properties file"
)
NOISE_WIN32 = (
    "COM exception querying Win32_Processor, which might not be on your system. "
    "Error was -2147023781: A system shutdown is in progress.(HRESULT: 8007045b)"
)
NOISE_ACTIVATOR = "Error starting activator"


def _match(message: str):
    """Parse then match, exactly as the builder does."""
    parsed = parse_runtime_log_message(message)
    # Mirrors the builder: match on the trimmed diagnosis PLUS the untrimmed line.
    evidence = "\n".join(part for part in (parsed.causal_message, message) if part)
    pattern = CATALOG.match(
        parsed.error_code, parsed.exception_class, evidence, parsed.error_family
    )
    return parsed, pattern


class TestDialectResolution:
    @pytest.mark.parametrize(
        ("message", "dialect", "component"),
        [
            (SQLSERVER_TRUNCATION, "talend_component", "tMSSqlOutput_5"),
            (TMC_STATUS_LINE, "tmc_status", "tMSSqlOutput_5"),
            (BIGQUERY_INVALID_DATASET, "talend_component", "tBigQueryOutput_2_tBQBE"),
            (BIGQUERY_NOT_FOUND, "talend_component", "tBigQueryOutput_1_tBQBE"),
            (SALESFORCE_INVALID_FIELD, "talend_component", "tSalesforceInput_1"),
            (SQLSERVER_CONN_RESET, "talend_component", "tMSSqlConnection_2"),
            (CAMEL_EXHAUSTED, "camel", None),
            (SEEDED_ORACLE, "bracket", "tOracleConnection_1"),
        ],
    )
    def test_dialect_and_component(self, message, dialect, component):
        parsed = parse_runtime_log_message(message)
        assert parsed.dialect == dialect
        assert parsed.component_name == component

    def test_tmc_status_line_also_yields_the_job_name(self):
        """The job name is the join key toward the scanned static topology."""
        parsed = parse_runtime_log_message(TMC_STATUS_LINE)
        assert parsed.job_name == "Maximo2RHM_Workorders"

    def test_compound_component_key_resolved_via_inventory(self):
        message = "Create_Business_Central_Token_v2_1_tExtractJSONFields_1 unexpected token"
        assert parse_runtime_log_message(message).component_name is None
        parsed = parse_runtime_log_message(
            message, {"Create_Business_Central_Token_v2_1_tExtractJSONFields_1"}
        )
        assert parsed.component_name == "Create_Business_Central_Token_v2_1_tExtractJSONFields_1"

    def test_inventory_never_invents_a_component_for_prose(self):
        parsed = parse_runtime_log_message("Route starting normally", {"tMSSqlOutput_5"})
        assert parsed.component_name is None


class TestCausalMessageIsTheRuntimesOwnWords:
    """The whole point: keep the sentence the runtime wrote."""

    def test_bigquery_dataset_diagnosis_is_preserved(self):
        parsed = parse_runtime_log_message(BIGQUERY_INVALID_DATASET)
        assert parsed.causal_message is not None
        assert 'Invalid dataset ID "test_ zaf_del_clean"' in parsed.causal_message

    def test_truncation_diagnosis_is_preserved(self):
        parsed = parse_runtime_log_message(SQLSERVER_TRUNCATION)
        assert parsed.causal_message is not None
        assert "Data truncation" in parsed.causal_message

    def test_tmc_status_preamble_is_stripped_from_the_diagnosis(self):
        parsed = parse_runtime_log_message(TMC_STATUS_LINE)
        assert parsed.causal_message == "java.sql.BatchUpdateException: Data truncation"
        assert "failed with code" not in parsed.causal_message

    def test_causal_message_is_credential_redacted(self):
        parsed = parse_runtime_log_message(
            "tMSSqlConnection_1 java.sql.SQLException: login failed for "
            "jdbc:jtds:sqlserver://scott:tiger@dbhost:1433/MAXIMO"
        )
        assert parsed.causal_message is not None
        assert "tiger" not in parsed.causal_message
        assert "scott" not in parsed.causal_message


class TestPlatformSignals:
    def test_jtds_sqlserver_jdbc_url_yields_host_and_database(self):
        """The Oracle-only ``@//`` pattern extracted nothing from jTDS URLs."""
        parsed = parse_runtime_log_message(
            "tMSSqlConnection_1 Opening connection jdbc:jtds:sqlserver://SRVDB01:1433/MAXIMO"
        )
        assert parsed.host == "SRVDB01"
        assert parsed.database_name == "MAXIMO"

    def test_bigquery_dataset_recorded_as_database(self):
        assert parse_runtime_log_message(BIGQUERY_INVALID_DATASET).database_name == (
            "test_ zaf_del_clean"
        )

    def test_camel_states_its_retry_budget(self):
        parsed = parse_runtime_log_message(CAMEL_EXHAUSTED)
        assert parsed.retry_attempt == 3
        assert parsed.retry_max == 3

    def test_camel_endpoint_host_extracted(self):
        assert parse_runtime_log_message(CAMEL_EXHAUSTED).host == "SRVA0673"

    @pytest.mark.parametrize(
        ("message", "family"),
        [
            (SQLSERVER_TRUNCATION, "sqlserver"),
            (BIGQUERY_INVALID_DATASET, "bigquery"),
            (BIGQUERY_NOT_FOUND, "bigquery"),
            (SALESFORCE_INVALID_FIELD, "salesforce"),
            (CAMEL_EXHAUSTED, "camel"),
        ],
    )
    def test_message_family(self, message, family):
        assert parse_runtime_log_message(message).message_family == family


class TestFieldViolations:
    def test_bare_truncation_records_kind_without_inventing_a_column(self):
        """jTDS names no column; guessing one would become false lineage evidence."""
        parsed = parse_runtime_log_message(SQLSERVER_TRUNCATION)
        assert parsed.field_violation_kind == "truncation"
        assert parsed.field_name is None

    def test_named_truncation_records_table_and_column(self):
        parsed = parse_runtime_log_message(
            "tMSSqlOutput_1 String or binary data would be truncated in table "
            "'dbo.WORKORDER', column 'DESCRIPTION'."
        )
        assert parsed.field_violation_kind == "truncation"
        assert parsed.field_name == "DESCRIPTION"
        assert parsed.table_name == "dbo.WORKORDER"

    def test_salesforce_unknown_field_is_named(self):
        parsed = parse_runtime_log_message(SALESFORCE_INVALID_FIELD)
        assert parsed.field_name == "UserPermissionsKnowledgeUser"
        assert parsed.field_violation_kind == "unknown_field"


class TestCatalogAttribution:
    @pytest.mark.parametrize(
        ("message", "pattern_id"),
        [
            (SQLSERVER_TRUNCATION, "sqlserver.truncation"),
            (TMC_STATUS_LINE, "sqlserver.truncation"),
            (BIGQUERY_INVALID_DATASET, "bigquery.invalid_dataset"),
            (BIGQUERY_NOT_FOUND, "bigquery.source_not_found"),
            (SALESFORCE_INVALID_FIELD, "salesforce.invalid_field"),
            (SQLSERVER_CONN_RESET, "sqlserver.connection_reset"),
            (CAMEL_EXHAUSTED, "camel.delivery_exhausted"),
            (SEEDED_ORACLE, "oracle.net.timeout"),
        ],
    )
    def test_real_faults_reach_a_specific_pattern(self, message, pattern_id):
        _parsed, pattern = _match(message)
        assert pattern is not None
        assert pattern.pattern_id == pattern_id

    def test_bigquery_fault_is_not_attributed_to_mysql(self):
        """The regression this work exists to fix.

        A 2xx status lifted from a response line became an "error code", which a
        family-blind ``mysql.generic`` then claimed — rendering "Execution
        terminated with MySQL error 200." for a BigQuery dataset-naming bug.
        """
        parsed, pattern = _match(BIGQUERY_INVALID_DATASET)
        assert parsed.error_code != "200"
        assert pattern is not None
        assert "mysql" not in pattern.pattern_id
        cause = CATALOG.render_root_cause(
            pattern,
            error_code=parsed.error_code,
            exception_class=parsed.exception_class,
            host=parsed.host,
            retry_max=parsed.retry_max,
        )
        assert "mysql" not in cause.lower()
        assert "dataset" in cause.lower()

    def test_http_success_status_is_never_an_error_code(self):
        for message in (
            "Response received: HTTP 200 OK",
            "Upload finished with status 204",
            "Redirected with HTTP 301",
        ):
            code, family = CATALOG.extract_error_code(message)
            assert code is None, message
            assert family is None, message

    def test_http_error_statuses_still_extract(self):
        assert CATALOG.extract_error_code("Downstream returned HTTP 503")[0] == "503"
        assert CATALOG.extract_error_code("Rejected with HTTP 401 Unauthorized")[0] == "401"

    def test_config_key_ending_in_server_host_is_not_a_mysql_code(self):
        """``..._SERVER_HOST`` matched the unanchored ``ER_[A-Z]+`` family mid-token."""
        code, family = CATALOG.extract_error_code(NOISE_PROPERTY)
        assert code is None
        assert family is None

    def test_talend_connect_exception_is_not_described_as_mulesoft(self):
        _parsed, pattern = _match(CAMEL_EXHAUSTED)
        cause = CATALOG.render_root_cause(
            pattern, error_code=None, exception_class="ConnectException", host="SRVA0673", retry_max=3
        )
        assert "mulesoft" not in cause.lower()

    def test_mule_qualified_exception_still_reaches_the_mule_pattern(self):
        """Only the message retains the package qualifier once the builder has
        reduced the exception to its simple name."""
        pattern = CATALOG.match(
            None,
            "ConnectException",
            "[db-connector] org.mule.runtime.api.connection.ConnectException: unreachable",
        )
        assert pattern is not None
        assert pattern.pattern_id == "mule.connectivity"


class TestHighVolumeEsbFaults:
    """The four shapes that dominate stored error/warning rows.

    Together these accounted for ~6,800 of 8,310 stored error+warning rows while
    producing no diagnosis at all, because none of them carries an exception class
    or a component prefix — the only two things the original parser looked for.
    """

    ACTIVEMQ_CONNECT = (
        "Failed to connect to [tcp://SRVA0971.werk.local:61616] after: 1 attempt(s) "
        "to connect. Reason: java.net.ConnectException: Connection refused"
    )
    ACTIVEMQ_TRANSPORT = (
        "Transport (tcp://SRVA0971.werk.local:61616) failed, attempting to "
        "automatically reconnect"
    )
    SOAP_NO_METHOD = (
        "No method was found for the WSDL operation "
        "{http://www.relatics.com/}Import"
    )
    CXF_INTERCEPTOR = (
        "Interceptor for {https://SRVA0673:9200/talend-esb-monitoring-alias}"
        "WebClient has thrown exception, unwinding now"
    )

    @pytest.mark.parametrize(
        ("message", "pattern_id"),
        [
            (ACTIVEMQ_CONNECT, "activemq.broker_unreachable"),
            (ACTIVEMQ_TRANSPORT, "activemq.transport_interrupted"),
            (SOAP_NO_METHOD, "soap.operation_not_found"),
            (CXF_INTERCEPTOR, "cxf.interceptor_fault"),
        ],
    )
    def test_reaches_a_specific_pattern(self, message, pattern_id):
        _parsed, pattern = _match(message)
        assert pattern is not None
        assert pattern.pattern_id == pattern_id

    @pytest.mark.parametrize(
        "message", [ACTIVEMQ_CONNECT, ACTIVEMQ_TRANSPORT, SOAP_NO_METHOD, CXF_INTERCEPTOR]
    )
    def test_self_describing_line_becomes_the_diagnosis(self, message):
        """No exception class and no component prefix, yet the line IS the fault."""
        assert parse_runtime_log_message(message).causal_message is not None

    def test_activemq_attempt_count_extracted(self):
        parsed = parse_runtime_log_message(self.ACTIVEMQ_CONNECT)
        assert parsed.retry_attempt == 1
        assert parsed.retry_max == 1

    def test_soap_root_cause_names_the_contract_mismatch(self):
        _parsed, pattern = _match(self.SOAP_NO_METHOD)
        cause = CATALOG.render_root_cause(
            pattern, error_code=None, exception_class=None, host=None, retry_max=None
        )
        assert "contract" in cause.lower()

    def test_camel_exchange_dump_is_not_treated_as_a_fault(self):
        """A payload dump accompanies a failure but does not state one.

        Promoting it would attach an incident to a line that names no cause.
        """
        dump = (
            "Exchange[ExchangePattern: InOnly, Headers: {CamelJmsDeliveryMode=2, "
            "CamelMessageTimestamp=1786339050477}, BodyType: String]"
        )
        assert parse_runtime_log_message(dump).causal_message is None


class TestNoiseIsNotDiagnosed:
    @pytest.mark.parametrize(
        "message",
        [NOISE_CONTENTLESS, NOISE_KARAF, NOISE_PROPERTY, NOISE_WIN32, NOISE_ACTIVATOR],
    )
    def test_infrastructure_noise_yields_no_component_and_no_code(self, message):
        parsed = parse_runtime_log_message(message)
        assert parsed.component_name is None
        assert parsed.error_code is None

    @pytest.mark.parametrize("message", [NOISE_CONTENTLESS, NOISE_KARAF, NOISE_WIN32])
    def test_infrastructure_noise_falls_through_to_the_fallback_pattern(self, message):
        _parsed, pattern = _match(message)
        assert pattern is not None
        assert pattern.pattern_id == "generic.unknown"


class TestCatalogInvariants:
    def test_family_scoped_pattern_must_constrain_the_code_shape(self):
        """Guards the footgun that made a family-only pattern a silent catch-all."""
        with pytest.raises(ValueError, match="code_pattern"):
            FailurePattern(
                pattern_id="broken.family_only",
                code_family="somewhere",
                root_cause_template="should never construct",
            )

    def test_every_default_pattern_satisfies_the_invariant(self):
        for pattern in CATALOG.patterns:
            if pattern.code_family is not None:
                assert pattern.code_pattern is not None, pattern.pattern_id

    def test_family_disagreement_blocks_a_platform_pattern(self):
        # Same code shape, different platform: the MySQL pattern must not claim it.
        pattern = CATALOG.match("1045", None, "access denied", "sqlserver")
        assert pattern is not None
        assert "mysql" not in pattern.pattern_id

    def test_mysql_numeric_family_alias_still_reaches_mysql_patterns(self):
        pattern = CATALOG.match("1045", None, "access denied", "mysql_numeric")
        assert pattern is not None
        assert pattern.pattern_id == "mysql.access_denied"
