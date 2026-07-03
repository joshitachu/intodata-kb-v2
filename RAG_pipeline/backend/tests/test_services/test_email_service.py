"""Tests for email_service HTML rendering, escaping, and SMTP wiring."""

from __future__ import annotations

from email import message_from_bytes
from email import policy
from unittest.mock import MagicMock, patch

import pytest

from src.config import settings
from src.services import email_service


def _captured_html(mock_smtp: MagicMock) -> str:
    """Pull the HTML alternative body out of the EmailMessage that was sent."""
    sent_message = mock_smtp.return_value.__enter__.return_value.send_message.call_args[0][0]
    payload = sent_message.as_bytes()
    parsed = message_from_bytes(payload, policy=policy.default)
    for part in parsed.walk():
        if part.get_content_type() == "text/html":
            return part.get_content()
    raise AssertionError("No text/html part found in sent message")


def _captured_plain(mock_smtp: MagicMock) -> str:
    sent_message = mock_smtp.return_value.__enter__.return_value.send_message.call_args[0][0]
    parsed = message_from_bytes(sent_message.as_bytes(), policy=policy.default)
    for part in parsed.walk():
        if part.get_content_type() == "text/plain":
            return part.get_content()
    raise AssertionError("No text/plain part found in sent message")


@pytest.fixture
def smtp_configured(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.test.local", raising=False)
    monkeypatch.setattr(settings, "smtp_from_address", "noreply@intodata.eu", raising=False)
    monkeypatch.setattr(settings, "smtp_from_name", "IntoData Nexus", raising=False)
    monkeypatch.setattr(settings, "smtp_port", 587, raising=False)
    monkeypatch.setattr(settings, "smtp_use_tls", True, raising=False)
    monkeypatch.setattr(settings, "public_app_url", "https://nexus.example.com", raising=False)
    monkeypatch.setattr(settings, "app_env", "development", raising=False)


def test_shell_renders_brand_header_and_footer():
    html = email_service._shell("Title", "<p>body</p>", preview_text="inbox preview")
    assert "<!DOCTYPE html>" in html
    assert "IntoData Nexus" in html
    assert "intodata-logo.png" in html
    assert "inbox preview" in html
    assert "&copy;" in html
    assert "All rights reserved" in html
    assert "transactional message" in html


def test_h_escapes_user_supplied_strings():
    assert email_service._h("<script>alert(1)</script>") == (
        "&lt;script&gt;alert(1)&lt;/script&gt;"
    )
    assert email_service._h('"quoted" & ampersand') == "&quot;quoted&quot; &amp; ampersand"
    assert email_service._h(None) == ""


def test_invite_email_contains_button_and_url(smtp_configured):
    with patch("src.services.email_service.smtplib.SMTP") as mock_smtp:
        url = email_service.send_invite_email("user@example.com", "tok123")
    assert url.endswith("/onboarding?token=tok123")
    html = _captured_html(mock_smtp)
    assert "Set up my account" in html
    assert url in html
    assert "expires in 48 hours" in html
    assert "IntoData Nexus" in html


def test_login_otp_email_contains_code_and_branding(smtp_configured):
    with patch("src.services.email_service.smtplib.SMTP") as mock_smtp:
        email_service.send_login_otp_email("user@example.com", "123456")
    html = _captured_html(mock_smtp)
    assert "123456" in html
    assert "10 minutes" in html
    assert "verification code" in html.lower() or "sign-in" in html.lower()
    plain = _captured_plain(mock_smtp)
    assert "123456" in plain


def test_invite_otp_email_contains_code_and_button(smtp_configured):
    with patch("src.services.email_service.smtplib.SMTP") as mock_smtp:
        email_service.send_invite_otp_email("user@example.com", "987654")
    html = _captured_html(mock_smtp)
    assert "987654" in html
    assert "Continue account setup" in html
    assert "/onboarding" in html


def test_rejection_email_escapes_malicious_reason(smtp_configured):
    malicious = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
    with patch("src.services.email_service.smtplib.SMTP") as mock_smtp:
        email_service.send_rejection_email("user@example.com", reason=malicious)
    html = _captured_html(mock_smtp)
    # Dangerous substrings from the payload must be escaped, not raw.
    assert "<script>alert(2)</script>" not in html
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html
    plain = _captured_plain(mock_smtp)
    # Plain fallback keeps the original text (no HTML context), which is intentional.
    assert "<script>" in plain


def test_rejection_email_without_reason(smtp_configured):
    with patch("src.services.email_service.smtplib.SMTP") as mock_smtp:
        email_service.send_rejection_email("user@example.com")
    html = _captured_html(mock_smtp)
    assert "not approved" in html
    assert "Reviewer note" not in html


def test_admin_access_request_email_escapes_name_and_email(smtp_configured):
    name = 'Evil"><script>alert(1)</script>'
    email = "user@example.com"
    with patch("src.services.email_service.smtplib.SMTP") as mock_smtp:
        email_service.send_admin_access_request_email(
            "admin@example.com", email, name
        )
    html = _captured_html(mock_smtp)
    assert "<script>" not in html
    assert "Review request" in html
    assert "admin/access-requests" in html
    # Plain body is plaintext and intentionally does not include the name;
    # the raw value would be safe there since no HTML parser consumes it.
    plain = _captured_plain(mock_smtp)
    assert email in plain
    assert "/admin/access-requests" in plain


def test_test_email_renders_branding(smtp_configured):
    with patch("src.services.email_service.smtplib.SMTP") as mock_smtp:
        email_service.send_test_email("user@example.com")
    html = _captured_html(mock_smtp)
    assert "Email delivery is working" in html or "working" in html.lower()


def test_logo_url_uses_public_app_url(smtp_configured):
    assert email_service._logo_url() == "https://nexus.example.com/intodata-logo.png"


def test_invite_url_strips_trailing_slash():
    from src.config import settings as real_settings

    original = real_settings.public_app_url
    try:
        real_settings.public_app_url = "https://x.example.com/"
        assert email_service.invite_url("abc").startswith("https://x.example.com/onboarding?token=abc")
    finally:
        real_settings.public_app_url = original