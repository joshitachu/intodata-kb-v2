"""Unit tests for the FailurePatternCatalog (src/runtime/insights/catalog.py).

Covers per-family error-code extraction, per-pattern matching, the conservative
no-match behavior on benign lines, and the "add a family = data only" property.
"""

import re

import pytest

from src.runtime.insights.catalog import (
    CATALOG,
    CodeFamily,
    FailurePattern,
    FailurePatternCatalog,
)


class TestErrorCodeExtraction:
    def test_oracle_code_extracted(self):
        code, family = CATALOG.extract_error_code(
            "[tOracleConnection_1] failed after 30000ms: ORA-12170: TNS:Connect timeout"
        )
        assert code == "ORA-12170"
        assert family == "oracle"

    def test_mysql_named_code_extracted(self):
        code, family = CATALOG.extract_error_code(
            "java.sql.SQLException: ER_ACCESS_DENIED_ERROR access denied for user"
        )
        assert code == "ER_ACCESS_DENIED_ERROR"
        assert family == "mysql"

    def test_mysql_numeric_requires_anchor(self):
        code, family = CATALOG.extract_error_code("MySQL error code 1045: access denied")
        assert code == "1045"
        assert family == "mysql_numeric"

    def test_postgres_sqlstate_extracted(self):
        code, family = CATALOG.extract_error_code(
            "org.postgresql.util.PSQLException: ... SQLSTATE 08006 connection reset"
        )
        assert code == "08006"
        assert family == "postgres"

    def test_http_status_extracted(self):
        code, family = CATALOG.extract_error_code("Downstream returned HTTP 503 Service Unavailable")
        assert code == "503"
        assert family == "http"

    def test_generic_coded_requires_error_context(self):
        code, family = CATALOG.extract_error_code("Job failed with FOO-42 fault")
        assert code == "FOO-42"
        assert family == "generic"

    @pytest.mark.parametrize(
        "benign",
        [
            "[esb_order_sync_route] Route starting (context PROD)",
            "[esb_order_sync_route] Route completed: 184 messages processed in 28.013s",
            "[cJMSInput_1] Received message id=ORD-8842193 (1.4 KB) from ORDER.INBOUND",
            "[cMessagingEndpoint_1] Rolling back message ORD-8842193 to ORDER.INBOUND.DLQ",
        ],
    )
    def test_benign_lines_yield_no_code(self, benign):
        code, family = CATALOG.extract_error_code(benign)
        assert code is None
        assert family is None


class TestPatternMatching:
    def test_oracle_net_timeout_pattern(self):
        pattern = CATALOG.match("ORA-12170", "SQLRecoverableException", "timeout")
        assert pattern is not None
        assert pattern.pattern_id == "oracle.net.timeout"
        cause = CATALOG.render_root_cause(
            pattern, error_code="ORA-12170", exception_class=None, host="db-prod", retry_max=3
        )
        assert "source database" in cause.lower()
        assert "timeout" in cause.lower()

    def test_oracle_non_net_falls_to_generic_oracle(self):
        pattern = CATALOG.match("ORA-00942", None, "table or view does not exist")
        assert pattern is not None
        assert pattern.pattern_id == "oracle.generic"

    def test_mysql_access_denied_specific(self):
        pattern = CATALOG.match("ER_ACCESS_DENIED_ERROR", None, "access denied")
        assert pattern is not None
        assert pattern.pattern_id == "mysql.access_denied"
        cause = CATALOG.render_root_cause(
            pattern, error_code="ER_ACCESS_DENIED_ERROR", exception_class=None, host=None, retry_max=None
        )
        assert "access denied" in cause.lower()

    def test_postgres_connection_specific(self):
        pattern = CATALOG.match("08006", None, "connection reset")
        assert pattern is not None
        assert pattern.pattern_id == "postgres.connection"
        cause = CATALOG.render_root_cause(
            pattern, error_code="08006", exception_class=None, host=None, retry_max=None
        )
        assert "postgresql" in cause.lower()

    def test_http_server_error_specific(self):
        pattern = CATALOG.match("503", None, "service unavailable")
        assert pattern is not None
        assert pattern.pattern_id == "http.server_error"

    def test_http_client_error_specific(self):
        pattern = CATALOG.match("404", None, "not found")
        assert pattern is not None
        assert pattern.pattern_id == "http.client_error"

    def test_mule_connectivity_specific(self):
        pattern = CATALOG.match(None, "org.mule.runtime.ConnectException", "connector")
        assert pattern is not None
        assert pattern.pattern_id == "mule.connectivity"
        cause = CATALOG.render_root_cause(
            pattern, error_code=None, exception_class="ConnectException", host=None, retry_max=None
        )
        assert "mulesoft" in cause.lower()

    def test_generic_coded_pattern(self):
        pattern = CATALOG.match("FOO-42", None, "failed with FOO-42")
        assert pattern is not None
        assert pattern.pattern_id == "generic.coded"

    def test_no_signal_returns_fallback_not_none(self):
        pattern = CATALOG.match(None, None, "something went wrong")
        assert pattern is not None
        assert pattern.pattern_id == "generic.unknown"

    def test_recoverable_exception_pattern(self):
        pattern = CATALOG.match(None, "SQLRecoverableException", "io error")
        assert pattern is not None
        assert pattern.pattern_id == "exception.recoverable"


class TestNextChecksRendering:
    def test_host_slot_omitted_when_no_host(self):
        pattern = CATALOG.match("ORA-12170", None, "timeout")
        checks = CATALOG.render_next_checks(pattern, host=None)
        assert all("{host}" not in c for c in checks)
        assert not any("None" in c for c in checks)

    def test_host_slot_filled_when_host_present(self):
        pattern = CATALOG.match("ORA-12170", None, "timeout")
        checks = CATALOG.render_next_checks(pattern, host="db-prod")
        assert any("db-prod" in c for c in checks)


class TestExtensibilityIsDataOnly:
    """Adding a failure family must be data only: append entries, no code edits."""

    def test_new_family_and_pattern_match_via_data(self):
        custom = FailurePatternCatalog(
            families=(
                CodeFamily(name="kafka", pattern=re.compile(r"KAFKA-\d+")),
                *CATALOG.families,
            ),
            patterns=(
                FailurePattern(
                    pattern_id="kafka.broker_unavailable",
                    code_family="kafka",
                    code_pattern=r"^KAFKA-100$",
                    root_cause_template="Kafka broker unavailable ({error_code}).",
                    next_checks=("Check broker reachability",),
                    specificity=30,
                ),
                *CATALOG.patterns,
            ),
        )
        code, family = custom.extract_error_code("Producer send failed: KAFKA-100 broker down")
        assert code == "KAFKA-100"
        assert family == "kafka"

        pattern = custom.match("KAFKA-100", None, "broker down")
        assert pattern is not None
        assert pattern.pattern_id == "kafka.broker_unavailable"
        cause = custom.render_root_cause(
            pattern, error_code="KAFKA-100", exception_class=None, host=None, retry_max=None
        )
        assert "kafka broker unavailable" in cause.lower()
