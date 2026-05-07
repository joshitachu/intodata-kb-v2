"""TOTP / 2FA helpers.

Wraps pyotp for code verification, generates provisioning URIs for QR-code
enrollment, and manages single-use backup codes. Uses bcrypt to hash backup
codes (same primitive as password_hash) so a DB leak never exposes the codes
themselves.
"""

from __future__ import annotations

import secrets
import string
from typing import Iterable

import bcrypt
import pyotp

from src.config import settings


BACKUP_CODE_COUNT = 10
BACKUP_CODE_ALPHABET = string.ascii_uppercase + string.digits
BACKUP_CODE_LENGTH = 10


def generate_totp_secret() -> str:
    """Generate a fresh base32 TOTP secret."""
    return pyotp.random_base32()


def provisioning_uri(secret: str, account_name: str) -> str:
    """Build an otpauth:// URI for QR-code enrollment.

    Issuer defaults to the configured tenant name so the code appears in the
    authenticator app as e.g. "Nexus (alice@acme.com)".
    """
    issuer = settings.tenant_name or "IntoData"
    return pyotp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=issuer)


def verify_totp(secret: str, code: str, *, valid_window: int = 1) -> bool:
    """Verify a 6-digit TOTP code.

    `valid_window=1` allows a 30s clock-drift window on either side, which is
    the pyotp default. Codes must be 6 digits.
    """
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=valid_window)


def generate_backup_codes(count: int = BACKUP_CODE_COUNT) -> list[str]:
    """Generate `count` random, human-readable backup codes."""
    return [
        "".join(secrets.choice(BACKUP_CODE_ALPHABET) for _ in range(BACKUP_CODE_LENGTH))
        for _ in range(count)
    ]


def hash_backup_codes(codes: Iterable[str]) -> list[dict]:
    """Hash each backup code with bcrypt. Returns storage-ready list of dicts."""
    return [
        {
            "hash": bcrypt.hashpw(c.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
            "used": False,
        }
        for c in codes
    ]


def consume_backup_code(stored: list[dict] | None, code: str) -> tuple[bool, list[dict] | None]:
    """Try to match `code` against unused entries in `stored`.

    On match, marks the entry as used and returns (True, updated_list).
    On miss, returns (False, stored) so the caller can store-or-not as it sees fit.
    The caller is responsible for persisting the updated list.
    """
    if not stored:
        return False, stored
    code_bytes = code.strip().upper().encode("utf-8")
    for entry in stored:
        if entry.get("used"):
            continue
        try:
            if bcrypt.checkpw(code_bytes, entry["hash"].encode("utf-8")):
                entry["used"] = True
                return True, stored
        except (ValueError, KeyError):
            continue
    return False, stored
