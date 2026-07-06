"""Fernet-based token encryption for GitHub PATs stored at rest."""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse


def _fernet():
    from cryptography.fernet import Fernet
    from mcp_server.config import config

    key = config.token_encryption_key
    if not key:
        raise RuntimeError(
            "MCP_TOKEN_ENCRYPTION_KEY is not set. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()


def inject_plaintext_token(repo_url: str, token: str | None, *, username: str | None = None) -> str:
    """Return repo_url with a plaintext token injected as HTTPS credentials (in-memory only).

    Single URL-building implementation for both credential sources. A PAT is used as
    the userinfo directly (``https://<pat>@host``). A GitHub App installation token
    must be paired with the ``x-access-token`` username
    (``https://x-access-token:<token>@host``) — GitHub rejects an installation token
    placed in the username position. Callers pass ``username`` for that case.
    """
    if not token:
        return repo_url

    parsed = urlparse(repo_url)
    if parsed.scheme not in ("http", "https"):
        return repo_url

    creds = f"{username}:{token}" if username else token
    authed = parsed._replace(netloc=f"{creds}@{parsed.hostname}" + (f":{parsed.port}" if parsed.port else ""))
    return urlunparse(authed)


def inject_token(repo_url: str, token_enc: str | None) -> str:
    """Return repo_url with a stored (encrypted) PAT injected as HTTPS credentials."""
    if not token_enc:
        return repo_url
    try:
        token = decrypt_token(token_enc)
    except Exception:
        return repo_url
    return inject_plaintext_token(repo_url, token)
