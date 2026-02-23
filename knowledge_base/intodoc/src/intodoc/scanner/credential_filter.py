"""Credential redaction engine — runs on raw text BEFORE XML parsing."""

from __future__ import annotations

import re


class CredentialFilter:
    """Redact sensitive data from raw file text.

    This MUST be applied before any parsing or LLM exposure.
    Platform-agnostic — works for any platform.
    """

    # Compiled patterns: (regex, replacement)
    _PATTERNS: list[tuple[re.Pattern, str]] = [
        # Talend encrypted passwords
        (re.compile(r"enc:system\.encryption\.key\.v\d+:[^\s\"'<>]+"), "[ENCRYPTED_PASSWORD]"),
        # id_Password field values in XML
        (re.compile(
            r'(<elementParameter\s+field="[^"]*"\s+name="id_Password"\s+value=")[^"]*(")',
            re.IGNORECASE,
        ), r'\1[PASSWORD_REDACTED]\2'),
        # Alternative password param ordering
        (re.compile(
            r'(<elementParameter\s+[^>]*name="id_Password"[^>]*value=")[^"]*(")',
            re.IGNORECASE,
        ), r'\1[PASSWORD_REDACTED]\2'),
        # IP addresses
        (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "[SERVER_IP]"),
        # JDBC URLs with embedded credentials
        (re.compile(r"jdbc:[a-zA-Z]+://[^\s\"'<>]*:[^\s\"'<>]*@[^\s\"'<>]+"), "[CONNECTION_STRING_REDACTED]"),
        # Generic connection strings with user:pass@
        (re.compile(r"://[^\s\"'<>]+:[^\s\"'<>]+@[^\s\"'<>]+"), "[CONNECTION_STRING_REDACTED]"),
        # Credential file paths
        (re.compile(r"[^\s\"'<>]*(Credentials|gcp\.json|service_account|\.pem|\.key)[^\s\"'<>]*", re.IGNORECASE), "[CREDENTIAL_PATH]"),
        # X509 certificate blocks
        (re.compile(r"-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----"), ""),
        # Private key blocks
        (re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----"), ""),
    ]

    # Key names whose values should be redacted (case-insensitive match)
    _SENSITIVE_KEYS = frozenset({
        "password", "secret", "token", "apikey", "api_key",
        "bearer", "client_secret", "private_key", "access_key",
    })

    # Pattern for XML-style key=value where key is sensitive
    _SENSITIVE_KV = re.compile(
        r'((?:name|field|key)\s*=\s*"(?:' +
        "|".join(_SENSITIVE_KEYS) +
        r')"[^>]*value\s*=\s*")[^"]*(")',
        re.IGNORECASE,
    )

    def filter_text(self, text: str) -> str:
        """Apply all redaction patterns to raw text. Returns cleaned text."""
        for pattern, replacement in self._PATTERNS:
            text = pattern.sub(replacement, text)
        text = self._SENSITIVE_KV.sub(r"\1[REDACTED]\2", text)
        return text

    def has_credentials(self, text: str) -> bool:
        """Quick check if text likely contains credentials."""
        for pattern, _ in self._PATTERNS:
            if pattern.search(text):
                return True
        return bool(self._SENSITIVE_KV.search(text))
