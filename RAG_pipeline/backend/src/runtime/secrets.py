"""Encrypted runtime-source credential helpers.

The source credential path fails closed: callers must provide a primary Fernet
key before encrypting or decrypting. A previous key may be configured for a
rotation window; reads of old-keyed rows return re-encrypted ciphertext so the
repository can persist lazy rotation in the same transaction.
"""

from __future__ import annotations

from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

from src.config import settings

PRIMARY_KEY_ID = "primary"
PREVIOUS_KEY_ID = "previous"


class RuntimeCredentialSecretError(RuntimeError):
    """Raised when runtime source credentials cannot be encrypted or decrypted."""


@dataclass(frozen=True)
class CredentialRotationResult:
    plaintext: bytes
    ciphertext: bytes
    encryption_key_id: str
    rotated: bool


def _configured_keys() -> dict[str, str]:
    keys: dict[str, str] = {}
    if settings.source_credentials_encryption_key:
        keys[settings.source_credentials_encryption_key_id or PRIMARY_KEY_ID] = (
            settings.source_credentials_encryption_key
        )
    if settings.source_credentials_encryption_key_previous:
        keys[settings.source_credentials_encryption_key_previous_id or PREVIOUS_KEY_ID] = (
            settings.source_credentials_encryption_key_previous
        )
    return keys


def current_key_id() -> str:
    return settings.source_credentials_encryption_key_id or PRIMARY_KEY_ID


def resolve_key_id(key_id: str | None = None) -> str:
    """Return the requested key id, defaulting to the current primary key."""

    selected = key_id or current_key_id()
    if selected not in _configured_keys():
        raise RuntimeCredentialSecretError(f"Runtime source credential key is not configured: {selected}")
    return selected


def _fernet_for(key_id: str) -> Fernet:
    keys = _configured_keys()
    key = keys.get(key_id)
    if not key:
        raise RuntimeCredentialSecretError(f"Runtime source credential key is not configured: {key_id}")
    try:
        return Fernet(key.encode("utf-8"))
    except ValueError as e:
        raise RuntimeCredentialSecretError(f"Runtime source credential key is invalid: {key_id}") from e


def encrypt(plaintext: bytes, key_id: str | None = None) -> bytes:
    """Encrypt plaintext with the configured Fernet key."""

    return _fernet_for(resolve_key_id(key_id)).encrypt(plaintext)


def decrypt(ciphertext: bytes, key_id: str) -> bytes:
    """Decrypt ciphertext using the key recorded on the credential row."""

    try:
        return _fernet_for(resolve_key_id(key_id)).decrypt(ciphertext)
    except InvalidToken as e:
        raise RuntimeCredentialSecretError("Runtime source credential ciphertext could not be decrypted") from e


def decrypt_and_rotate_if_needed(ciphertext: bytes, key_id: str) -> CredentialRotationResult:
    """Decrypt and re-encrypt old-keyed rows with the current primary key."""

    plaintext = decrypt(ciphertext, key_id)
    active_key_id = current_key_id()
    if key_id == active_key_id:
        return CredentialRotationResult(
            plaintext=plaintext,
            ciphertext=ciphertext,
            encryption_key_id=active_key_id,
            rotated=False,
        )
    rotated_ciphertext = encrypt(plaintext, active_key_id)
    return CredentialRotationResult(
        plaintext=plaintext,
        ciphertext=rotated_ciphertext,
        encryption_key_id=active_key_id,
        rotated=True,
    )
