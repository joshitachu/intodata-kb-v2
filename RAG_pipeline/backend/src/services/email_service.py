from __future__ import annotations

import html
import logging
import smtplib
from email.utils import make_msgid
from email.message import EmailMessage

from src.config import settings

logger = logging.getLogger(__name__)


class EmailSendError(RuntimeError):
    pass


# Brand palette — matches the existing Tailwind primary (#1d4ed8) and the
# frontend login page chrome. Kept inline so email clients that strip <style>
# still render correctly.
_BRAND_PRIMARY = "#1d4ed8"
_BRAND_PRIMARY_DARK = "#1e40af"
_BRAND_TEXT = "#0f172a"
_BRAND_MUTED = "#475569"
_BRAND_BG = "#f8fafc"
_BRAND_CARD = "#ffffff"
_BRAND_BORDER = "#e2e8f0"
_BRAND_CODE_BG = "#f1f5f9"

_FONT_STACK = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
    "'Helvetica Neue', Arial, sans-serif"
)

_FOOTER_YEAR = "2026"


def invite_url(token: str) -> str:
    base = settings.public_app_url.rstrip("/")
    return f"{base}/onboarding?token={token}"


def _configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from_address)


def _h(value: str | None) -> str:
    """HTML-escape any value rendered into a template."""
    if value is None:
        return ""
    return html.escape(value, quote=True)


def _logo_url() -> str:
    base = settings.public_app_url.rstrip("/")
    return f"{base}/intodata-logo.png"


def _support_email() -> str:
    return settings.smtp_from_address or "support@intodata.eu"


def _shell(title: str, body_html: str, preview_text: str = "") -> str:
    """Render the full HTML document for an email.

    `preview_text` is the inbox preview line (hidden via zero-size div).
    `body_html` is the inner card content (already escaped where needed).
    """
    logo = _logo_url()
    support = _h(_support_email())
    product = _h(settings.smtp_from_name or "IntoData Nexus")
    year = _FOOTER_YEAR
    preview = _h(preview_text) if preview_text else ""

    return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta charset="utf-8">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light only">
<meta name="supported-color-schemes" content="light">
<title>{_h(title)}</title>
<style>
  @media only screen and (max-width: 620px) {{
    .container {{ width: 100% !important; max-width: 100% !important; }}
    .px {{ padding-left: 20px !important; padding-right: 20px !important; }}
    .py {{ padding-top: 24px !important; padding-bottom: 24px !important; }}
    .h1 {{ font-size: 22px !important; line-height: 30px !important; }}
    .code {{ font-size: 28px !important; letter-spacing: 6px !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background:{_BRAND_BG};font-family:{_FONT_STACK};color:{_BRAND_TEXT};-webkit-text-size-adjust:100%;">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:{_BRAND_BG};">{preview}</div>
<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="background:{_BRAND_BG};">
  <tr>
    <td align="center" class="py" style="padding:40px 16px;">
      <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="600" class="container" style="width:600px;max-width:600px;">

        <!-- Brand header -->
        <tr>
          <td align="left" class="px" style="padding:0 32px 24px 32px;">
            <table role="presentation" border="0" cellpadding="0" cellspacing="0">
              <tr>
                <td valign="middle" style="padding-right:12px;">
                  <img src="{logo}" alt="{product}" width="32" height="32" style="display:block;border:0;outline:none;text-decoration:none;-ms-interpolation-mode:bicubic;">
                </td>
                <td valign="middle" style="font-family:{_FONT_STACK};font-size:15px;font-weight:600;color:{_BRAND_TEXT};letter-spacing:-0.1px;">
                  {product}
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Card -->
        <tr>
          <td class="px" style="padding:0 32px;">
            <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="background:{_BRAND_CARD};border:1px solid {_BRAND_BORDER};border-radius:12px;overflow:hidden;">
              <tr>
                <td class="px" style="padding:40px 40px 32px 40px;">
                  {body_html}
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td align="center" class="px" style="padding:24px 32px 0 32px;font-family:{_FONT_STACK};font-size:12px;line-height:18px;color:{_BRAND_MUTED};">
            <div style="margin-bottom:6px;">&copy; {year} IntoData Nederland BV. All rights reserved.</div>
            <div>This is a transactional message from {product}.</div>
            <div style="margin-top:6px;">Need help? Contact <a href="mailto:{support}" style="color:{_BRAND_PRIMARY};text-decoration:underline;">{support}</a>.</div>
          </td>
        </tr>

      </table>
    </td>
  </tr>
</table>
</body>
</html>"""


def _button(url: str, label: str) -> str:
    return (
        f'<table role="presentation" border="0" cellpadding="0" cellspacing="0" style="margin:24px 0 8px 0;">'
        f'<tr><td align="center" bgcolor="{_BRAND_PRIMARY}" style="border-radius:8px;">'
        f'<a href="{_h(url)}" target="_blank" rel="noopener" '
        f'style="display:inline-block;padding:13px 28px;font-family:{_FONT_STACK};'
        f'font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;'
        f'border-radius:8px;background:{_BRAND_PRIMARY};border:1px solid {_BRAND_PRIMARY_DARK};">'
        f'{_h(label)}</a></td></tr></table>'
    )


def _paragraph(text: str, muted: bool = False, extra: str = "") -> str:
    color = _BRAND_MUTED if muted else _BRAND_TEXT
    return (
        f'<p style="margin:0 0 16px 0;font-family:{_FONT_STACK};font-size:15px;'
        f'line-height:24px;color:{color};{extra}">{text}</p>'
    )


def _heading(text: str) -> str:
    return (
        f'<h1 class="h1" style="margin:0 0 16px 0;font-family:{_FONT_STACK};'
        f'font-size:24px;line-height:32px;font-weight:700;color:{_BRAND_TEXT};'
        f'letter-spacing:-0.3px;">{_h(text)}</h1>'
    )


def _muted_note(text: str) -> str:
    return (
        f'<p style="margin:16px 0 0 0;padding-top:16px;border-top:1px solid {_BRAND_BORDER};'
        f'font-family:{_FONT_STACK};font-size:12px;line-height:18px;color:{_BRAND_MUTED};">'
        f'{text}</p>'
    )


def _code_block(code: str, big: bool = False) -> str:
    font_size = "32px" if big else "28px"
    letter = "8px" if big else "6px"
    return (
        f'<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" '
        f'style="margin:8px 0 24px 0;">'
        f'<tr><td align="center" bgcolor="{_BRAND_CODE_BG}" '
        f'style="padding:20px;border:1px dashed {_BRAND_BORDER};border-radius:8px;">'
        f'<span class="code" style="font-family:\'SF Mono\',Menlo,Consolas,monospace;'
        f'font-size:{font_size};font-weight:700;letter-spacing:{letter};color:{_BRAND_TEXT};">'
        f'{_h(code)}</span></td></tr></table>'
    )


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

    plain = (
        "Your access request has been approved!\n\n"
        "Click the link below to finish setting up your account. "
        "You'll be asked to fill in your profile (name, job title, intended use) "
        "and then set up two-factor authentication to secure your account.\n\n"
        f"Set up your account: {url}\n\n"
        "This link expires in 48 hours.\n"
    )

    body = (
        _heading("Your access has been approved")
        + _paragraph(
            "Your request to join "
            f"<strong style='color:{_BRAND_TEXT};'>{_h(settings.smtp_from_name)}</strong>"
            " has been approved. Click the button below to finish setting up your account."
        )
        + _paragraph(
            "You'll fill in a few profile details and then set up two-factor authentication to keep your account secure."
        )
        + _button(url, "Set up my account")
        + _paragraph("Or copy and paste this link into your browser:", muted=True)
        + (
            f'<p style="margin:0 0 16px 0;word-break:break-all;font-family:'
            f'\'SF Mono\',Menlo,Consolas,monospace;font-size:12px;line-height:18px;'
            f'color:{_BRAND_PRIMARY};"><a href="{_h(url)}" style="color:{_BRAND_PRIMARY};">{_h(url)}</a></p>'
        )
        + _muted_note(
            "This link expires in 48 hours. "
            "If you weren't expecting this email, you can safely ignore it."
        )
    )

    message = _base_message(to_email, f"Your access to {settings.smtp_from_name} has been approved")
    message.set_content(plain)
    message.add_alternative(
        _shell("Your access has been approved", body, "Your access request has been approved"),
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

    plain = (
        "Use this verification code to sign in to IntoData Nexus:\n\n"
        f"{code}\n\n"
        "The code expires in 10 minutes. If you did not request it, you can ignore this email.\n"
    )

    body = (
        _heading("Your sign-in verification code")
        + _paragraph(
            f"Use the code below to finish signing in to <strong style='color:{_BRAND_TEXT};'>{_h(settings.smtp_from_name)}</strong>."
        )
        + _code_block(code, big=False)
        + _paragraph("Enter this code on the sign-in page to continue.", muted=True)
        + _muted_note(
            "This code expires in 10 minutes and can only be used once. "
            "If you didn't request it, you can safely ignore this email "
            "and no action will be taken on your account."
        )
    )

    message = _base_message(to_email, f"Your {settings.smtp_from_name} sign-in code")
    message.set_content(plain)
    message.add_alternative(
        _shell("Sign-in code", body, f"Your sign-in code: {code}"),
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

    plain = (
        "Your IntoData Nexus setup code:\n\n"
        f"{code}\n\n"
        f"Go here to continue: {setup_url}\n\n"
        "The code expires in 10 minutes. Your invitation remains valid for 48 hours.\n"
    )

    body = (
        _heading("Verify your account setup")
        + _paragraph(
            "Welcome to "
            f"<strong style='color:{_BRAND_TEXT};'>{_h(settings.smtp_from_name)}</strong>. "
            "Use the code below to continue setting up your account."
        )
        + _code_block(code, big=True)
        + _button(setup_url, "Continue account setup")
        + _muted_note(
            "This code expires in 10 minutes. "
            "Your invitation link remains valid for 48 hours."
        )
    )

    message = _base_message(to_email, f"Your {settings.smtp_from_name} setup code")
    message.set_content(plain)
    message.add_alternative(
        _shell("Account setup code", body, f"Your setup code: {code}"),
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

    safe_reason = _h(reason) if reason else ""

    plain = (
        "Your access request to IntoData Nexus was not approved at this time.\n"
    )
    if reason:
        plain += f"\nReason: {reason}\n"
    plain += "\nIf you think this is a mistake, reach out to your administrator.\n"

    reason_block = (
        f'<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" '
        f'style="margin:8px 0 24px 0;">'
        f'<tr><td style="padding:16px 20px;border-left:3px solid {_BRAND_PRIMARY};'
        f'background:{_BRAND_CODE_BG};border-radius:0 6px 6px 0;">'
        f'<div style="font-family:{_FONT_STACK};font-size:11px;font-weight:600;'
        f'letter-spacing:0.6px;text-transform:uppercase;color:{_BRAND_MUTED};margin-bottom:6px;">Reviewer note</div>'
        f'<div style="font-family:{_FONT_STACK};font-size:14px;line-height:22px;'
        f'color:{_BRAND_TEXT};">{safe_reason}</div></td></tr></table>'
        if safe_reason else ""
    )

    body = (
        _heading("Your access request was not approved")
        + _paragraph(
            "Unfortunately, your request to join "
            f"<strong style='color:{_BRAND_TEXT};'>{_h(settings.smtp_from_name)}</strong>"
            " was not approved at this time."
        )
        + reason_block
        + _paragraph(
            "If you believe this is a mistake, please reach out to your administrator.",
            muted=True,
        )
    )

    message = _base_message(to_email, f"Update on your {settings.smtp_from_name} access request")
    message.set_content(plain)
    message.add_alternative(
        _shell(
            "Access request update",
            body,
            "Your access request was not approved at this time.",
        ),
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

    safe_name = _h(requester_name) if requester_name else ""
    safe_email = _h(requester_email)
    who_plain = requester_email
    who_html = (
        f"<strong style='color:{_BRAND_TEXT};'>{safe_name}</strong> "
        f"<span style='color:{_BRAND_MUTED};'>&lt;{safe_email}&gt;</span>"
        if safe_name else f"<strong style='color:{_BRAND_TEXT};'>{safe_email}</strong>"
    )

    plain = (
        f"Account {who_plain} has requested access to {settings.smtp_from_name}.\n\n"
        f"Review and approve or reject the request here:\n{review_url}\n\n"
        "If you weren't expecting this, you can ignore this email.\n"
    )

    body = (
        _heading("A new access request needs your review")
        + _paragraph("The following account is requesting access:")
        + (
            f'<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" '
            f'style="margin:8px 0 24px 0;">'
            f'<tr><td style="padding:16px 20px;background:{_BRAND_CODE_BG};border-radius:8px;'
            f'border:1px solid {_BRAND_BORDER};">'
            f'<div style="font-family:{_FONT_STACK};font-size:15px;line-height:24px;'
            f'color:{_BRAND_TEXT};">{who_html}</div></td></tr></table>'
        )
        + _button(review_url, "Review request")
        + _muted_note(
            "If you weren't expecting this, you can ignore this email. "
            "The request will remain pending in your admin console until you act on it."
        )
    )

    message = _base_message(to_email, subject)
    message.set_content(plain)
    message.add_alternative(
        _shell(
            "New access request",
            body,
            f"{requester_email} requested access to {settings.smtp_from_name}",
        ),
        subtype="html",
    )
    _send(message, "admin access request", to_email)


def send_test_email(to_email: str) -> None:
    if not _configured():
        if settings.app_env == "production":
            raise EmailSendError("SMTP is not configured")
        logger.warning("SMTP is not configured. Test email target: %s", to_email)
        return

    plain = "Email delivery is configured for this IntoData Nexus deployment.\n"

    body = (
        _heading("Email delivery is working")
        + _paragraph(
            f"This confirms that SMTP is correctly configured for your "
            f"<strong style='color:{_BRAND_TEXT};'>{_h(settings.smtp_from_name)}</strong> deployment."
        )
        + _paragraph(
            "You can safely ignore this message. It was sent to verify outbound mail delivery.",
            muted=True,
        )
    )

    message = _base_message(to_email, f"{settings.smtp_from_name} email test")
    message.set_content(plain)
    message.add_alternative(
        _shell("Email test", body, "Email delivery is configured."),
        subtype="html",
    )

    _send(message, "test", to_email)