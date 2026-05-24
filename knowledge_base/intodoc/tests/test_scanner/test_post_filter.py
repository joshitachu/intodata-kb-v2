"""Tests for post-parse structural filter."""

from intodoc.config import RedactionLevel
from intodoc.models import (
    ComponentParam,
    Component,
    ContextGroup,
    ContextVariable,
    MetadataConnection,
    ParsedJob,
    ScanResult,
)
from intodoc.scanner.credential_filter import CredentialFilter
from intodoc.scanner.post_filter import filter_scan_result


def _make_scan_result(**kwargs) -> ScanResult:
    """Helper to create a minimal ScanResult for testing."""
    defaults = {
        "platform": "talend",
        "repo_root": "/test",
    }
    defaults.update(kwargs)
    return ScanResult(**defaults)


# ── MetadataConnection redaction ────────────────────────────────────


def test_connection_host_always_redacted():
    cf = CredentialFilter(level=RedactionLevel.STANDARD)
    result = _make_scan_result(metadata_connections=[
        MetadataConnection(
            xmi_id="c1", name="MSSQL_prod", connection_type="MSSQL",
            host="079talend02.keune.com", port="1433", database="BioDB",
        ),
    ])
    filter_scan_result(result, cf)

    conn = result.metadata_connections[0]
    assert conn.host == "[HOST_REDACTED]"
    # Database preserved at standard level
    assert conn.database == "BioDB"


def test_connection_database_redacted_strict():
    cf = CredentialFilter(level=RedactionLevel.STRICT)
    result = _make_scan_result(metadata_connections=[
        MetadataConnection(
            xmi_id="c1", name="BQ_prod", connection_type="BigQuery",
            host="bigquery.googleapis.com", database="vaxxinova-dwh",
            schema_name="staging",
        ),
    ])
    filter_scan_result(result, cf)

    conn = result.metadata_connections[0]
    assert conn.host == "[HOST_REDACTED]"
    assert conn.database == "[DATABASE_REDACTED]"
    assert conn.schema_name == "[SCHEMA_REDACTED]"


def test_connection_file_path_redacted():
    cf = CredentialFilter(level=RedactionLevel.STANDARD)
    result = _make_scan_result(metadata_connections=[
        MetadataConnection(
            xmi_id="c1", name="FileConn", connection_type="File",
            file_path="/data/exports/vaxxinova.csv",
        ),
    ])
    filter_scan_result(result, cf)

    conn = result.metadata_connections[0]
    assert conn.file_path == "[FILE_PATH_REDACTED]"


def test_connection_name_preserved():
    """Connection name (e.g., 'MSSQL_prod') is structural, not sensitive."""
    cf = CredentialFilter(level=RedactionLevel.STRICT)
    result = _make_scan_result(metadata_connections=[
        MetadataConnection(xmi_id="c1", name="MSSQL_prod", connection_type="MSSQL"),
    ])
    filter_scan_result(result, cf)

    assert result.metadata_connections[0].name == "MSSQL_prod"


def test_connection_type_preserved():
    """Connection type (MSSQL, BigQuery, etc.) is structural."""
    cf = CredentialFilter(level=RedactionLevel.STRICT)
    result = _make_scan_result(metadata_connections=[
        MetadataConnection(xmi_id="c1", name="x", connection_type="BigQuery"),
    ])
    filter_scan_result(result, cf)

    assert result.metadata_connections[0].connection_type == "BigQuery"


# ── Context variable redaction ──────────────────────────────────────


def test_password_type_variable_redacted():
    cf = CredentialFilter(level=RedactionLevel.STANDARD)
    result = _make_scan_result(context_groups=[
        ContextGroup(name="Default", variables=[
            ContextVariable(
                name="db_pass", type="id_Password",
                values={"Default": "super_secret_123"},
            ),
        ]),
    ])
    filter_scan_result(result, cf)

    var = result.context_groups[0].variables[0]
    assert var.values["Default"] == "[PASSWORD_REDACTED]"


def test_sensitive_name_variable_redacted():
    cf = CredentialFilter(level=RedactionLevel.STANDARD)
    result = _make_scan_result(context_groups=[
        ContextGroup(name="Default", variables=[
            ContextVariable(
                name="api_token", type="String",
                values={"Default": "tok_abc123"},
            ),
        ]),
    ])
    filter_scan_result(result, cf)

    var = result.context_groups[0].variables[0]
    assert var.values["Default"] == "[REDACTED]"


def test_infra_var_redacted_strict():
    cf = CredentialFilter(level=RedactionLevel.STRICT)
    result = _make_scan_result(context_groups=[
        ContextGroup(name="Default", variables=[
            ContextVariable(
                name="activemq_user", type="String",
                values={"Default": "admin"},
            ),
        ]),
    ])
    filter_scan_result(result, cf)

    var = result.context_groups[0].variables[0]
    assert var.values["Default"] == "[REDACTED]"


def test_infra_var_preserved_standard():
    cf = CredentialFilter(level=RedactionLevel.STANDARD)
    result = _make_scan_result(context_groups=[
        ContextGroup(name="Default", variables=[
            ContextVariable(
                name="activemq_user", type="String",
                values={"Default": "admin"},
            ),
        ]),
    ])
    filter_scan_result(result, cf)

    # Standard level does NOT redact infra vars like _user
    var = result.context_groups[0].variables[0]
    assert var.values["Default"] == "admin"


def test_paranoid_redacts_all_values():
    cf = CredentialFilter(level=RedactionLevel.PARANOID)
    result = _make_scan_result(context_groups=[
        ContextGroup(name="Default", variables=[
            ContextVariable(
                name="batch_size", type="int",
                values={"Default": "1000"},
            ),
            ContextVariable(
                name="output_format", type="String",
                values={"Default": "csv"},
            ),
        ]),
    ])
    filter_scan_result(result, cf)

    for var in result.context_groups[0].variables:
        assert var.values["Default"] == "[REDACTED]"


def test_normal_var_text_filtered():
    """Non-sensitive variables still get text-level pattern matching."""
    cf = CredentialFilter(level=RedactionLevel.STANDARD)
    result = _make_scan_result(context_groups=[
        ContextGroup(name="Default", variables=[
            ContextVariable(
                name="some_config", type="String",
                values={"Default": "http://server.example.com/api"},
            ),
        ]),
    ])
    filter_scan_result(result, cf)

    var = result.context_groups[0].variables[0]
    assert "example.com" not in var.values["Default"]


# ── Job component param filtering ──────────────────────────────────


def test_job_component_params_filtered():
    cf = CredentialFilter(level=RedactionLevel.STANDARD)
    result = _make_scan_result(jobs=[
        ParsedJob(
            name="test_job", path="/jobs/test",
            components=[
                Component(
                    unique_name="tRest_1", component_type="tRESTClient",
                    params=[
                        ComponentParam(
                            name="URL",
                            value="http://internal.server.com:8080/api",
                            field_type="TEXT",
                        ),
                    ],
                ),
            ],
        ),
    ])
    filter_scan_result(result, cf)

    param = result.jobs[0].components[0].params[0]
    assert "internal.server.com" not in param.value


def test_component_type_preserved():
    """Component type names must never be redacted."""
    cf = CredentialFilter(level=RedactionLevel.STRICT)
    result = _make_scan_result(jobs=[
        ParsedJob(
            name="test_job", path="/jobs/test",
            components=[
                Component(unique_name="tMSSql_1", component_type="tMSSqlInput"),
                Component(unique_name="tBQ_1", component_type="tBigQueryOutput"),
            ],
        ),
    ])
    filter_scan_result(result, cf)

    assert result.jobs[0].components[0].component_type == "tMSSqlInput"
    assert result.jobs[0].components[1].component_type == "tBigQueryOutput"


# ── Job-level context groups ────────────────────────────────────────


def test_job_context_groups_filtered():
    cf = CredentialFilter(level=RedactionLevel.STANDARD)
    result = _make_scan_result(jobs=[
        ParsedJob(
            name="test_job", path="/jobs/test",
            context_groups=[
                ContextGroup(name="Default", variables=[
                    ContextVariable(
                        name="db_password", type="id_Password",
                        values={"Default": "secret123"},
                    ),
                ]),
            ],
        ),
    ])
    filter_scan_result(result, cf)

    var = result.jobs[0].context_groups[0].variables[0]
    assert var.values["Default"] == "[PASSWORD_REDACTED]"


# ── Audit integration ──────────────────────────────────────────────


def test_post_filter_audit_recorded():
    cf = CredentialFilter(level=RedactionLevel.STANDARD)
    result = _make_scan_result(
        metadata_connections=[
            MetadataConnection(
                xmi_id="c1", name="x", connection_type="MSSQL",
                host="prod-server.internal",
            ),
        ],
        context_groups=[
            ContextGroup(name="Default", variables=[
                ContextVariable(
                    name="api_secret", type="String",
                    values={"Default": "s3cret"},
                ),
            ]),
        ],
    )
    filter_scan_result(result, cf)

    report = cf.get_audit_report()
    assert report["total_redactions"] > 0


# ── ComponentParam name-based redaction ────────────────────────────


def test_component_param_sensitive_name_redacted():
    """Param named 'api_key' must be redacted unconditionally regardless of value format."""
    cf = CredentialFilter(level=RedactionLevel.STANDARD)
    result = _make_scan_result(jobs=[
        ParsedJob(
            name="test_job", path="/jobs/test",
            components=[
                Component(
                    unique_name="tRest_1", component_type="tRESTClient",
                    params=[
                        ComponentParam(name="api_key", value="prod-internal-custom-xyz123", field_type="TEXT"),
                    ],
                ),
            ],
        ),
    ])
    filter_scan_result(result, cf)

    param = result.jobs[0].components[0].params[0]
    assert param.value == "[REDACTED]"


def test_component_param_auth_token_name_redacted():
    cf = CredentialFilter(level=RedactionLevel.STANDARD)
    result = _make_scan_result(jobs=[
        ParsedJob(
            name="test_job", path="/jobs/test",
            components=[
                Component(
                    unique_name="tRest_1", component_type="tRESTClient",
                    params=[
                        ComponentParam(name="auth_token", value="some-internal-token-value", field_type="TEXT"),
                    ],
                ),
            ],
        ),
    ])
    filter_scan_result(result, cf)

    param = result.jobs[0].components[0].params[0]
    assert param.value == "[REDACTED]"


def test_component_param_bearer_value_redacted_by_pattern():
    """Param with generic name but Bearer token value must be caught by text filter."""
    cf = CredentialFilter(level=RedactionLevel.STANDARD)
    result = _make_scan_result(jobs=[
        ParsedJob(
            name="test_job", path="/jobs/test",
            components=[
                Component(
                    unique_name="tRest_1", component_type="tRESTClient",
                    params=[
                        ComponentParam(name="HEADER_VALUE_1", value="Bearer xyz123abcdefghijklmno", field_type="TEXT"),
                    ],
                ),
            ],
        ),
    ])
    filter_scan_result(result, cf)

    param = result.jobs[0].components[0].params[0]
    assert "xyz123abcdefghijklmno" not in param.value
    assert "[BEARER_TOKEN_REDACTED]" in param.value


def test_component_param_nonsensitive_name_preserved():
    """Params with non-sensitive names and non-credential values must not be redacted."""
    cf = CredentialFilter(level=RedactionLevel.STANDARD)
    result = _make_scan_result(jobs=[
        ParsedJob(
            name="test_job", path="/jobs/test",
            components=[
                Component(
                    unique_name="tRest_1", component_type="tRESTClient",
                    params=[
                        ComponentParam(name="MAX_ROWS", value="1000", field_type="TEXT"),
                        ComponentParam(name="OUTPUT_FORMAT", value="json", field_type="TEXT"),
                    ],
                ),
            ],
        ),
    ])
    filter_scan_result(result, cf)

    params = result.jobs[0].components[0].params
    assert params[0].value == "1000"
    assert params[1].value == "json"
