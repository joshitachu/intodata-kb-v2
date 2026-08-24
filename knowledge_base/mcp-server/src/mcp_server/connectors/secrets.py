"""Connector-specific access to the existing Fernet encryption boundary."""

from __future__ import annotations

from mcp_server.utils.encryption import decrypt_token, encrypt_token

from .errors import ConnectorError


class ConnectorSecretService:
    """Credentials are write-only outside the short provider-call lifetime."""

    def encrypt(self, plaintext: str) -> str:
        if not isinstance(plaintext, str) or not plaintext.strip():
            raise ValueError("Connector credential is required")
        try:
            return encrypt_token(plaintext)
        except Exception as exc:
            raise ConnectorError("encryption_unavailable", "Connector credential encryption is unavailable") from exc

    def decrypt_for_provider(self, ciphertext: str) -> str:
        try:
            return decrypt_token(ciphertext)
        except Exception as exc:
            raise ConnectorError("credential_unavailable", "Connector credential could not be decrypted") from exc


connector_secrets = ConnectorSecretService()
