from src.runtime.providers.base import find_non_ascii_credential_field

SCHEMA = [
    {"name": "api_token", "type": "secret", "label": "API token", "required": True},
    {"name": "base_url", "type": "url", "label": "Base URL", "required": True},
]


def test_find_non_ascii_credential_field_returns_none_for_clean_credentials():
    assert find_non_ascii_credential_field(SCHEMA, {"api_token": "abc123", "base_url": "https://x.example.com"}) is None


def test_find_non_ascii_credential_field_detects_homoglyph():
    # "Р" (U+0420, Cyrillic Er) looks identical to Latin "P" but is a different code point.
    credentials = {"api_token": "toРken", "base_url": "https://x.example.com"}

    assert find_non_ascii_credential_field(SCHEMA, credentials) == "API token"


def test_find_non_ascii_credential_field_ignores_missing_and_non_string_values():
    credentials = {"api_token": None, "base_url": 123}

    assert find_non_ascii_credential_field(SCHEMA, credentials) is None
