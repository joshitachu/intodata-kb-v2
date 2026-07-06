"""Tests for token encryption and credential URL injection."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from mcp_server.utils import encryption


@pytest.fixture
def fernet_key(monkeypatch):
    from mcp_server.config import config

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(config, "token_encryption_key", key)
    return key


def test_inject_plaintext_token_builds_userinfo_url():
    out = encryption.inject_plaintext_token("https://github.com/acme/repo.git", "ghs_tok")
    assert out == "https://ghs_tok@github.com/acme/repo.git"


def test_inject_plaintext_token_preserves_port():
    out = encryption.inject_plaintext_token("https://ghe.example.com:8443/acme/repo.git", "tok")
    assert out == "https://tok@ghe.example.com:8443/acme/repo.git"


def test_inject_plaintext_token_noop_without_token():
    url = "https://github.com/acme/repo.git"
    assert encryption.inject_plaintext_token(url, None) == url
    assert encryption.inject_plaintext_token(url, "") == url


def test_inject_plaintext_token_ignores_non_http_scheme():
    url = "git@github.com:acme/repo.git"
    assert encryption.inject_plaintext_token(url, "tok") == url


def test_encrypt_decrypt_roundtrip(fernet_key):
    enc = encryption.encrypt_token("secret-pat")
    assert enc != "secret-pat"
    assert encryption.decrypt_token(enc) == "secret-pat"


def test_inject_token_decrypts_then_injects(fernet_key):
    enc = encryption.encrypt_token("ghp_pat")
    out = encryption.inject_token("https://github.com/acme/repo.git", enc)
    assert out == "https://ghp_pat@github.com/acme/repo.git"


def test_inject_token_noop_without_token():
    url = "https://github.com/acme/repo.git"
    assert encryption.inject_token(url, None) == url
