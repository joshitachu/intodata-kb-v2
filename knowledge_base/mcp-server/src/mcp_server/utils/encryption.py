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


def inject_plaintext_token(repo_url: str, token: str | None) -> str:
    """Return repo_url with a plaintext token injected as HTTPS credentials (in-memory only).

    This is the single URL-building implementation. Both the PAT path
    (``inject_token``) and the GitHub App path (minted installation tokens) funnel
    through here so credential injection behaves identically regardless of source.
    """
    if not token:
        return repo_url

    parsed = urlparse(repo_url)
    if parsed.scheme not in ("http", "https"):
        return repo_url

    authed = parsed._replace(netloc=f"{token}@{parsed.hostname}" + (f":{parsed.port}" if parsed.port else ""))
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
