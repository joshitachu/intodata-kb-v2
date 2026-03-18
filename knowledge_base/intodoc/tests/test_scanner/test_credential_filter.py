"""Tests for credential filter -- both existing patterns and new enhancements."""

from pathlib import Path
from textwrap import dedent

import pytest

from intodoc.config import RedactionLevel
from intodoc.scanner.credential_filter import CredentialFilter


# ── Existing pattern tests (regression) ─────────────────────────────


def test_encrypted_password_redacted():
    f = CredentialFilter()
    text = 'value="enc:system.encryption.key.v1:DxjMAtbVkm0XorVCWFYLz0QmhoXXPvStdgzqQI1Io2uAUlaRylNsiTNQH41ORrjY"'
    result = f.filter_text(text)
    assert "enc:system.encryption.key.v1" not in result
    assert "[ENCRYPTED_PASSWORD]" in result


def test_ip_address_redacted():
    f = CredentialFilter()
    text = 'ServerName="192.168.1.100"'
    result = f.filter_text(text)
    assert "192.168.1.100" not in result
    assert "[SERVER_IP]" in result


def test_jdbc_url_redacted():
    f = CredentialFilter()
    text = 'url="jdbc:sqlserver://admin:secretpass@myserver.com:1433/mydb"'
    result = f.filter_text(text)
    assert "secretpass" not in result
    assert "[CONNECTION_STRING_REDACTED]" in result


def test_credential_path_redacted():
    f = CredentialFilter()
    text = 'value="/path/to/gcp.json"'
    result = f.filter_text(text)
    assert "gcp.json" not in result
    assert "[CREDENTIAL_PATH]" in result


def test_certificate_stripped():
    f = CredentialFilter()
    text = 'before-----BEGIN CERTIFICATE-----\nMIIBxTCC\n-----END CERTIFICATE-----after'
    result = f.filter_text(text)
    assert "MIIB" not in result


def test_normal_text_untouched():
    f = CredentialFilter()
    text = '<elementParameter field="TEXT" name="DBNAME" value="Biovet"/>'
    result = f.filter_text(text)
    # DBNAME/Biovet don't match any pattern
    assert "Biovet" in result


def test_has_credentials_detects():
    f = CredentialFilter()
    assert f.has_credentials("enc:system.encryption.key.v1:abc123")
    assert not f.has_credentials("just some normal text here no dots")


# ── New patterns: Email ─────────────────────────────────────────────


def test_email_redacted():
    f = CredentialFilter()
    text = 'value="salesforce.integration@vaxxinova.com"'
    result = f.filter_text(text)
    assert "vaxxinova.com" not in result
    assert "[EMAIL_REDACTED]" in result


def test_email_in_xml():
    f = CredentialFilter()
    text = '<param name="FROM" value="admin@company.org"/>'
    result = f.filter_text(text)
    assert "admin@company.org" not in result


# ── New patterns: URL ───────────────────────────────────────────────


def test_http_url_redacted():
    f = CredentialFilter()
    text = 'value="http://079talend02.keune.com:8161/api/message"'
    result = f.filter_text(text)
    assert "079talend02" not in result
    assert "keune.com" not in result
    assert "[URL_REDACTED]" in result


def test_https_url_redacted():
    f = CredentialFilter()
    text = 'endpoint="https://my-api.example.com/v2/data"'
    result = f.filter_text(text)
    assert "example.com" not in result
    assert "[URL_REDACTED]" in result


def test_amqp_url_redacted():
    f = CredentialFilter()
    text = 'broker="amqp://broker.internal:5672/vhost"'
    result = f.filter_text(text)
    assert "broker.internal" not in result
    assert "[URL_REDACTED]" in result


# ── New patterns: FQDN ──────────────────────────────────────────────


def test_fqdn_redacted():
    f = CredentialFilter()
    text = 'host="079talend02.keune.com"'
    result = f.filter_text(text)
    assert "keune.com" not in result
    assert "[HOSTNAME]" in result


def test_fqdn_with_subdomain():
    f = CredentialFilter()
    text = 'server="db-prod.vaxxinova.internal"'
    result = f.filter_text(text)
    assert "vaxxinova.internal" not in result
    assert "[HOSTNAME]" in result


def test_fqdn_safe_domain_preserved():
    """XML namespace domains like w3.org should NOT be redacted."""
    f = CredentialFilter()
    text = 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
    # The URL will be redacted, but we check that w3.org alone isn't touched
    # when it appears without protocol
    text2 = 'schema at www.w3.org standard'
    result = f.filter_text(text2)
    assert "w3.org" in result


def test_fqdn_eclipse_preserved():
    f = CredentialFilter()
    text = 'xmlns:emf="http://www.eclipse.org/emf"'
    # Without protocol prefix
    text2 = 'from eclipse.org model'
    result = f.filter_text(text2)
    assert "eclipse.org" in result


# ── New patterns: File paths (strict only) ──────────────────────────


def test_windows_path_redacted_strict():
    f = CredentialFilter(level=RedactionLevel.STRICT)
    text = r'value="C:\talend\data\vaxxinova-dwh-8c18bd136cf5.json"'
    result = f.filter_text(text)
    assert "vaxxinova" not in result
    assert "[FILE_PATH_REDACTED]" in result


def test_windows_path_not_redacted_standard():
    f = CredentialFilter(level=RedactionLevel.STANDARD)
    text = r'value="C:\talend\data\output.csv"'
    result = f.filter_text(text)
    # Standard does NOT redact windows file paths (unless they match credential patterns)
    assert r"C:\talend" in result


def test_unix_path_redacted_strict():
    f = CredentialFilter(level=RedactionLevel.STRICT)
    text = 'path="/home/talend/jobs/etl.sh"'
    result = f.filter_text(text)
    assert "/home/talend" not in result
    assert "[FILE_PATH_REDACTED]" in result


def test_unix_opt_path_redacted_strict():
    f = CredentialFilter(level=RedactionLevel.STRICT)
    text = 'dir="/opt/talend/data"'
    result = f.filter_text(text)
    assert "/opt/talend" not in result


# ── False positive safety ───────────────────────────────────────────


def test_talend_component_names_preserved():
    """Talend component type names like tMSSqlInput must NOT be redacted."""
    f = CredentialFilter()
    components = ["tMSSqlInput", "tBigQueryOutput", "tRESTClient", "tMap", "tLogRow"]
    for comp in components:
        result = f.filter_text(comp)
        assert result == comp, f"{comp} was incorrectly redacted to {result}"


def test_xml_namespace_in_url_not_doubled():
    """URL pattern handles XML namespaces -- the URL is redacted but we don't crash."""
    f = CredentialFilter()
    text = 'xmlns="http://www.w3.org/2001/XMLSchema"'
    result = f.filter_text(text)
    # URL gets redacted, that's fine
    assert "[URL_REDACTED]" in result


def test_simple_numbers_not_redacted():
    """Version-like numbers should not trigger IP redaction."""
    f = CredentialFilter()
    text = 'version="7.3"'
    result = f.filter_text(text)
    assert result == text


# ── Redaction levels ────────────────────────────────────────────────


def test_level_standard_is_default():
    f = CredentialFilter()
    assert f.level == RedactionLevel.STANDARD


def test_level_strict():
    f = CredentialFilter(level=RedactionLevel.STRICT)
    assert f.level == RedactionLevel.STRICT


def test_level_paranoid():
    f = CredentialFilter(level=RedactionLevel.PARANOID)
    assert f.level == RedactionLevel.PARANOID


# ── Variable name sensitivity ──────────────────────────────────────


def test_sensitive_var_password():
    f = CredentialFilter()
    assert f.is_sensitive_var_name("activemq_password")
    assert f.is_sensitive_var_name("db_secret")
    assert f.is_sensitive_var_name("api_token")
    assert f.is_sensitive_var_name("id_password")


def test_sensitive_var_infra_strict():
    """Infrastructure vars are sensitive only at strict+."""
    f_standard = CredentialFilter(level=RedactionLevel.STANDARD)
    f_strict = CredentialFilter(level=RedactionLevel.STRICT)

    assert not f_standard.is_sensitive_var_name("activemq_user")
    assert not f_standard.is_sensitive_var_name("db_host")

    assert f_strict.is_sensitive_var_name("activemq_user")
    assert f_strict.is_sensitive_var_name("db_host")
    assert f_strict.is_sensitive_var_name("api_endpoint")
    assert f_strict.is_sensitive_var_name("broker_url")


def test_nonsensitive_var():
    f = CredentialFilter(level=RedactionLevel.STRICT)
    assert not f.is_sensitive_var_name("batch_size")
    assert not f.is_sensitive_var_name("max_retries")
    assert not f.is_sensitive_var_name("output_format")


# ── Audit report ────────────────────────────────────────────────────


def test_audit_report_counts():
    f = CredentialFilter()
    f.filter_text('host="079talend02.keune.com"')
    f.filter_text('email="user@example.com"')

    report = f.get_audit_report()
    assert report["redaction_level"] == "standard"
    assert report["total_redactions"] >= 2
    assert "hostname" in report["summary"] or "email_redacted" in report["summary"]


def test_audit_report_no_raw_values():
    """Audit report must NEVER contain raw sensitive values."""
    f = CredentialFilter()
    f.filter_text('secret_host="very-secret-server.internal"')

    report = f.get_audit_report()
    report_str = str(report)
    assert "very-secret-server" not in report_str


def test_audit_reset():
    f = CredentialFilter()
    f.filter_text('host="server.example.com"')
    assert f.get_audit_report()["total_redactions"] > 0

    f.reset_audit()
    assert f.get_audit_report()["total_redactions"] == 0


# ── Custom config ───────────────────────────────────────────────────


def test_custom_config_patterns(tmp_path: Path):
    config = tmp_path / ".intodoc-redact.yaml"
    config.write_text(dedent("""\
        patterns:
          - regex: "079talend\\\\d+"
            replacement: "[INTERNAL_SERVER]"
            level: standard
    """))

    f = CredentialFilter(config_path=config)
    text = 'server="079talend02"'
    result = f.filter_text(text)
    assert "079talend02" not in result
    assert "[INTERNAL_SERVER]" in result


def test_custom_safe_domains(tmp_path: Path):
    config = tmp_path / ".intodoc-redact.yaml"
    config.write_text(dedent("""\
        safe_domains:
          - salesforce.com
    """))

    f = CredentialFilter(config_path=config)
    # salesforce.com should be preserved
    text = 'domain at login.salesforce.com works'
    result = f.filter_text(text)
    assert "salesforce.com" in result


def test_custom_sensitive_vars(tmp_path: Path):
    config = tmp_path / ".intodoc-redact.yaml"
    config.write_text(dedent("""\
        sensitive_vars:
          - "project_id"
    """))

    f = CredentialFilter(config_path=config)
    assert f.is_sensitive_var_name("gcp_project_id")


def test_invalid_custom_config_ignored(tmp_path: Path):
    """Malformed YAML should not crash the filter."""
    config = tmp_path / ".intodoc-redact.yaml"
    config.write_text("not: [valid: yaml: {{")

    # Should not raise
    f = CredentialFilter(config_path=config)
    result = f.filter_text("normal text")
    assert result == "normal text"
