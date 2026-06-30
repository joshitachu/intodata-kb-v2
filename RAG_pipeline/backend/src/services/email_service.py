from __future__ import annotations

import logging
import smtplib
from email.utils import make_msgid
from email.message import EmailMessage

from src.config import settings

logger = logging.getLogger(__name__)


class EmailSendError(RuntimeError):
    pass


def invite_url(token: str) -> str:
    base = settings.public_app_url.rstrip("/")
    return f"{base}/onboarding?token={token}"


def _configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from_address)


def _base_message(to_email: str, subject: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_address}>"
    message["To"] = to_email
    message["Reply-To"] = settings.smtp_from_address
    message["Message-ID"] = make_msgid(domain=settings.smtp_from_address.split("@")[-1])
    return message


def _send(message: EmailMessage, purpose: str, to_email: str) -> None:
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except Exception as exc:  # pragma: no cover - depends on external SMTP
        logger.exception("Failed to send %s email to %s", purpose, to_email)
        raise EmailSendError(f"{purpose.title()} email could not be sent") from exc


def send_invite_email(to_email: str, token: str) -> str:
    """Token-based magic link invite email."""
    url = invite_url(token)
    if not _configured():
        logger.warning("SMTP is not configured. Invite link for %s: %s", to_email, url)
        if settings.app_env == "production":
            raise EmailSendError("SMTP is not configured")
        return url

    message = _base_message(to_email, "Your access to IntoData Nexus has been approved")
    message.set_content(
        "Your access request has been approved!\n\n"
        "Click the link below to finish setting up your account. "
        "You'll be asked to fill in your profile (name, job title, intended use) "
        "and then set up two-factor authentication to secure your account.\n\n"
        f"Set up your account: {url}\n\n"
        "This link expires in 48 hours.\n"
    )
    message.add_alternative(
        "<p>Your access request has been approved!</p>"
        "<p>Click the button below to finish setting up your account. "
        "You'll fill in a few profile details and then set up two-factor authentication.</p>"
        f"<p><a href=\"{url}\" style=\"display:inline-block;padding:10px 20px;"
        "background:#1d4ed8;color:#fff;text-decoration:none;border-radius:6px;"
        "font-weight:600\">Set up my account</a></p>"
        "<p style=\"color:#6b7280;font-size:13px\">This link expires in 48 hours. "
        "If you weren't expecting this, you can ignore it.</p>",
        subtype="html",
    )

    _send(message, "invite", to_email)
    return url


def send_login_otp_email(to_email: str, code: str) -> None:
    if not _configured():
        if settings.app_env == "production":
            raise EmailSendError("SMTP is not configured")
        logger.warning("SMTP is not configured. Login code for %s: %s", to_email, code)
        return

    message = _base_message(to_email, "Your IntoData Nexus sign-in code")
    message.set_content(
        "Use this verification code to sign in to IntoData Nexus:\n\n"
        f"{code}\n\n"
        "The code expires in 10 minutes. If you did not request it, you can ignore this email.\n"
    )
    message.add_alternative(
        "<p>Use this verification code to sign in to IntoData Nexus:</p>"
        f"<p style=\"font-size:24px;letter-spacing:4px;font-weight:700\">{code}</p>"
        "<p>The code expires in 10 minutes. If you did not request it, you can ignore this email.</p>",
        subtype="html",
    )
    _send(message, "login code", to_email)


def send_invite_otp_email(to_email: str, code: str) -> str:
    setup_url = f"{settings.public_app_url.rstrip('/')}/onboarding"
    if not _configured():
        logger.warning("SMTP is not configured. Setup code for %s: %s", to_email, code)
        if settings.app_env == "production":
            raise EmailSendError("SMTP is not configured")
        return setup_url

    message = _base_message(to_email, "Your IntoData Nexus setup code")
    message.set_content(
        "Your IntoData Nexus setup code:\n\n"
        f"{code}\n\n"
        f"Go here to continue: {setup_url}\n\n"
        "The code expires in 10 minutes. Your invitation remains valid for 48 hours.\n"
    )
    message.add_alternative(
        "<p>Your IntoData Nexus setup code:</p>"
        f"<p style=\"font-size:32px;letter-spacing:6px;font-weight:700;font-family:monospace\">{code}</p>"
        f"<p><a href=\"{setup_url}\">Continue account setup</a></p>"
        "<p style=\"color:#6b7280;font-size:13px\">This code expires in 10 minutes. "
        "Your invitation remains valid for 48 hours.</p>",
        subtype="html",
    )
    _send(message, "setup code", to_email)
    return setup_url


def send_rejection_email(to_email: str, reason: str | None = None) -> None:
    if not _configured():
        if settings.app_env == "production":
            raise EmailSendError("SMTP is not configured")
        logger.warning("SMTP not configured. Rejection email for %s", to_email)
        return

    body = (
        "Your access request to IntoData Nexus was not approved at this time.\n"
    )
    if reason:
        body += f"\nReason: {reason}\n"
    body += "\nIf you think this is a mistake, reach out to your administrator.\n"

    message = _base_message(to_email, "Your IntoData Nexus access request")
    message.set_content(body)
    message.add_alternative(
        "<p>Your access request to IntoData Nexus was not approved at this time.</p>"
        + (f"<p><strong>Reason:</strong> {reason}</p>" if reason else "")
        + "<p style=\"color:#6b7280;font-size:13px\">If you think this is a mistake, "
        "reach out to your administrator.</p>",
        subtype="html",
    )
    _send(message, "rejection", to_email)


def send_admin_access_request_email(
    to_email: str, requester_email: str, requester_name: str | None
) -> None:
    """Notify a superadmin that someone has submitted an access request.

    The access request itself is already persisted and the in-app
    notification row is created before this is called; this email is a
    best-effort push so an admin who isn't logged in still learns about
    the request. SMTP failures are logged at the call site and must not
    fail the access request.
    """
    review_url = f"{settings.public_app_url.rstrip('/')}/admin/access-requests"
    subject = f"New access request for {settings.smtp_from_name}"

    if not _configured():
        logger.warning(
            "SMTP is not configured. Access request notification for %s from %s: %s",
            to_email,
            requester_email,
            review_url,
        )
        return

    who = requester_email
    if requester_name:
        who = f"{requester_name} <{requester_email}>"

    message = _base_message(to_email, subject)
    message.set_content(
        f"Account {who} has requested access to {settings.smtp_from_name}.\n\n"
        f"Review and approve or reject the request here:\n{review_url}\n\n"
        "If you weren't expecting this, you can ignore this email.\n"
    )
    message.add_alternative(
        f"<p>Account <strong>{who}</strong> has requested access to "
        f"<strong>{settings.smtp_from_name}</strong>.</p>"
        f"<p><a href=\"{review_url}\" style=\"display:inline-block;padding:10px 20px;"
        f"background:#1d4ed8;color:#fff;text-decoration:none;border-radius:6px;"
        f"font-weight:600\">Review request</a></p>"
        "<p style=\"color:#6b7280;font-size:13px\">If you weren't expecting this, "
        "you can ignore this email.</p>",
        subtype="html",
    )
    _send(message, "admin access request", to_email)


def send_test_email(to_email: str) -> None:
    if not _configured():
        if settings.app_env == "production":
            raise EmailSendError("SMTP is not configured")
        logger.warning("SMTP is not configured. Test email target: %s", to_email)
        return

    message = _base_message(to_email, "IntoData Nexus email test")
    message.set_content("Email delivery is configured for this IntoData Nexus deployment.\n")
    message.add_alternative(
        "<p>Email delivery is configured for this IntoData Nexus deployment.</p>",
        subtype="html",
    )

    _send(message, "test", to_email)
