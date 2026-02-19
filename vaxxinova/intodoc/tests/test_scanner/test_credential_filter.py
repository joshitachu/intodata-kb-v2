"""Tests for credential filter."""

from intodoc.scanner.credential_filter import CredentialFilter


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
    assert result == text


def test_has_credentials_detects():
    f = CredentialFilter()
    assert f.has_credentials("enc:system.encryption.key.v1:abc123")
    assert not f.has_credentials("just some normal text here")
