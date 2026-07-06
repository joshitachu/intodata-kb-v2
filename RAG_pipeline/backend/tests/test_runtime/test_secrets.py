import pytest
from cryptography.fernet import Fernet

from src.config import settings
from src.runtime import secrets


def test_encrypt_decrypt_round_trip(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "source_credentials_encryption_key", key)
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous", "")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_id", "primary")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous_id", "previous")

    ciphertext = secrets.encrypt(b'{"api_token":"secret"}')

    assert ciphertext != b'{"api_token":"secret"}'
    assert secrets.decrypt(ciphertext, secrets.PRIMARY_KEY_ID) == b'{"api_token":"secret"}'


def test_lazy_rotation_reencrypts_previous_key(monkeypatch):
    previous = Fernet.generate_key().decode()
    current = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "source_credentials_encryption_key", previous)
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous", "")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_id", "v1")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous_id", "previous")
    old_ciphertext = secrets.encrypt(b"payload")

    monkeypatch.setattr(settings, "source_credentials_encryption_key", current)
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous", previous)
    monkeypatch.setattr(settings, "source_credentials_encryption_key_id", "v2")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous_id", "v1")
    result = secrets.decrypt_and_rotate_if_needed(old_ciphertext, "v1")

    assert result.plaintext == b"payload"
    assert result.rotated is True
    assert result.encryption_key_id == "v2"
    assert result.ciphertext != old_ciphertext
    assert secrets.decrypt(result.ciphertext, "v2") == b"payload"


def test_missing_key_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "source_credentials_encryption_key", "")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous", "")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_id", "primary")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous_id", "previous")

    with pytest.raises(secrets.RuntimeCredentialSecretError):
        secrets.encrypt(b"payload")
