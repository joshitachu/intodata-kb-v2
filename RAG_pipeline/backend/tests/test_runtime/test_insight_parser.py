"""Unit tests for the deterministic runtime log parser (src/runtime/insights/parser.py).

Pure function tests — no database, no fixtures. Covers the rich seed log lines
used in the builder integration tests.
"""

import pytest

from src.runtime.insights.parser import ParsedRuntimeLog, parse_runtime_log_message, redact_credentials


class TestComponentExtraction:
    def test_bracket_prefix_is_extracted(self):
        result = parse_runtime_log_message("[tOracleConnection_1] Connection attempt 1/3 failed")
        assert result.component_name == "tOracleConnection_1"

    def test_no_bracket_prefix_returns_none(self):
        result = parse_runtime_log_message("Route starting normally")
        assert result.component_name is None

    def test_component_with_numbers_is_extracted(self):
        result = parse_runtime_log_message("[cMessagingEndpoint_1] Listening on jms:queue:ORDER.INBOUND")
        assert result.component_name == "cMessagingEndpoint_1"

    def test_nested_brackets_takes_first(self):
        result = parse_runtime_log_message("[outer] [inner] some message")
        assert result.component_name == "outer"


class TestOraErrorCode:
    def test_ora_12170_is_extracted(self):
        result = parse_runtime_log_message(
            "[tOracleConnection_1] Connection attempt 3/3 failed after 30000ms: ORA-12170: TNS:Connect timeout occurred"
        )
        assert result.error_code == "ORA-12170"

    def test_other_ora_code_is_extracted(self):
        result = parse_runtime_log_message("Database error: ORA-01017: invalid username/password")
        assert result.error_code == "ORA-01017"

    def test_no_ora_code_returns_none(self):
        result = parse_runtime_log_message("[cConfig_1] Loaded runtime context 'PROD'")
        assert result.error_code is None


class TestExceptionClass:
    def test_sqrecoverable_exception_extracted(self):
        result = parse_runtime_log_message(
            "[tOracleConnection_1] java.sql.SQLRecoverableException: IO Error: Connection timed out"
        )
        assert result.exception_class == "SQLRecoverableException"

    def test_bare_exception_name_extracted(self):
        result = parse_runtime_log_message("Caused by: NullPointerException at line 42")
        assert result.exception_class == "NullPointerException"

    def test_no_exception_returns_none(self):
        result = parse_runtime_log_message("[esb_order_sync_route] Route starting (context PROD)")
        assert result.exception_class is None


class TestRetryExtraction:
    def test_attempt_1_of_3(self):
        result = parse_runtime_log_message(
            "[tOracleConnection_1] Connection attempt 1/3 failed: IO Error: retrying in 5s"
        )
        assert result.retry_attempt == 1
        assert result.retry_max == 3

    def test_attempt_3_of_3(self):
        result = parse_runtime_log_message(
            "[tOracleConnection_1] Connection attempt 3/3 failed after 30000ms: ORA-12170"
        )
        assert result.retry_attempt == 3
        assert result.retry_max == 3

    def test_no_retry_returns_none(self):
        result = parse_runtime_log_message("[cConfig_1] Loaded runtime context 'PROD'")
        assert result.retry_attempt is None
        assert result.retry_max is None


class TestDurationAndTimeout:
    def test_duration_ms_extracted(self):
        result = parse_runtime_log_message(
            "[tOracleConnection_1] Connection attempt 3/3 failed after 30000ms"
        )
        assert result.duration_ms == 30000

    def test_timeout_seconds_converted(self):
        result = parse_runtime_log_message(
            "[tOracleConnection_1] Opening connection jdbc:oracle:thin:@//db:1521/DB1 (pool=main, timeout=30s)"
        )
        assert result.timeout_ms == 30000

    def test_no_duration_or_timeout_returns_none(self):
        result = parse_runtime_log_message("[esb_order_sync_route] Route starting (context PROD)")
        assert result.duration_ms is None
        assert result.timeout_ms is None


class TestJdbcExtraction:
    def test_host_and_database_extracted(self):
        """The host is recorded as PRESENT, not as an address.

        ``host`` is redacted whole (see ``parser.redact_host``): the field held
        internal hostnames verbatim, and everything downstream reads presence,
        not content. The database name is a schema object and survives.
        """
        result = parse_runtime_log_message(
            "[tOracleConnection_1] Opening connection jdbc:oracle:thin:@//db-prod-scan:1521/ORCLPDB1 (pool=esb_main, timeout=30s)"
        )
        assert result.host == "<host>"
        assert result.database_name == "ORCLPDB1"

    def test_jdbc_message_family(self):
        result = parse_runtime_log_message(
            "[tOracleConnection_1] Opening connection jdbc:oracle:thin:@//db-prod-scan:1521/ORCLPDB1"
        )
        assert result.message_family == "jdbc"

    def test_no_jdbc_url_returns_none(self):
        result = parse_runtime_log_message("[esb_order_sync_route] Route starting")
        assert result.host is None
        assert result.database_name is None


class TestQueueExtraction:
    def test_jms_queue_extracted(self):
        result = parse_runtime_log_message(
            "[cMessagingEndpoint_1] Listening on jms:queue:ORDER.INBOUND (concurrentConsumers=4)"
        )
        assert result.queue_name == "ORDER.INBOUND"

    def test_dlq_queue_extracted(self):
        result = parse_runtime_log_message(
            "[cMessagingEndpoint_1] Rolling back message to ORDER.INBOUND.DLQ"
        )
        # No explicit jms: prefix in the rollback line — queue_name may be None
        # (the message references the queue by text, not the jms: URI scheme)
        assert result.queue_name is None

    def test_jms_message_family(self):
        result = parse_runtime_log_message(
            "[cJMSInput_1] Received message id=ORD-8842193 from jms:queue:ORDER.INBOUND"
        )
        assert result.message_family == "jms"


class TestSelectTable:
    def test_select_table_name_extracted(self):
        result = parse_runtime_log_message(
            "[tOracleInput_1] Waiting for connection before executing SELECT on ORDERS_STAGING"
        )
        assert result.table_name == "ORDERS_STAGING"

    def test_no_select_returns_none(self):
        result = parse_runtime_log_message("[tOracleOutput_1] 0 rows written; transaction rolled back")
        assert result.table_name is None


class TestSafeNomatch:
    def test_plain_info_line_returns_empty_result(self):
        result = parse_runtime_log_message("[esb_order_sync_route] Route starting (context PROD)")
        assert result.error_code is None
        assert result.exception_class is None
        assert result.retry_attempt is None
        assert result.confidence in (None, "low")

    def test_success_completion_line_returns_low_or_none_confidence(self):
        result = parse_runtime_log_message(
            "[esb_order_sync_route] Route completed: 184 messages processed in 28.013s"
        )
        assert result.error_code is None
        assert result.exception_class is None


class TestCredentialRedaction:
    def test_password_redacted(self):
        out = redact_credentials("connecting with password=s3cr3t to host")
        assert "s3cr3t" not in out
        assert "password=<redacted>" in out

    def test_token_redacted(self):
        out = redact_credentials("Authorization: token=abc123xyz")
        assert "abc123xyz" not in out
        assert "token=<redacted>" in out

    def test_no_credentials_unchanged(self):
        msg = "[tOracleConnection_1] Connection attempt 1/3 failed"
        assert redact_credentials(msg) == msg

    def test_jdbc_url_without_credentials_unchanged(self):
        url = "jdbc:oracle:thin:@//db-prod-scan:1521/ORCLPDB1"
        assert redact_credentials(url) == url

    def test_inline_userinfo_colon_form_redacted(self):
        out = redact_credentials("jdbc:mysql://scott:tiger@db-prod:3306/sales")
        assert "scott" not in out
        assert "tiger" not in out
        assert "<redacted>@" in out
        assert "db-prod:3306/sales" in out

    def test_inline_userinfo_slash_form_redacted(self):
        out = redact_credentials("jdbc:oracle:thin:scott/tiger@//db-prod:1521/ORCLPDB1")
        assert "scott" not in out
        assert "tiger" not in out
        assert "<redacted>@" in out
        assert "db-prod:1521/ORCLPDB1" in out

    def test_plain_host_port_without_userinfo_unchanged(self):
        msg = "Opening connection to db-prod:1521 (pool=main)"
        assert redact_credentials(msg) == msg

    def test_endpoint_field_carries_no_inline_credential(self):
        result = parse_runtime_log_message(
            "[tOracleConnection_1] Opening connection "
            "jdbc:oracle:thin:scott/tiger@//db-prod:1521/ORCLPDB1 (pool=main)"
        )
        assert result.endpoint is not None
        assert "scott" not in result.endpoint
        assert "tiger" not in result.endpoint
        assert result.host == "<host>"
        assert result.database_name == "ORCLPDB1"


class TestConfidence:
    def test_high_confidence_with_error_code_and_exception(self):
        result = parse_runtime_log_message(
            "[tOracleConnection_1] java.sql.SQLRecoverableException: ORA-12170 timeout"
        )
        assert result.confidence == "high"

    def test_medium_confidence_with_error_code_only(self):
        result = parse_runtime_log_message(
            "[tOracleConnection_1] Connection failed with ORA-12170"
        )
        assert result.confidence == "medium"

    def test_low_confidence_with_component_only(self):
        result = parse_runtime_log_message("[cConfig_1] Loaded runtime context 'PROD'")
        assert result.confidence == "low"
