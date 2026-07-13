"""Credential redaction engine -- runs on raw text BEFORE XML parsing."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from intodoc.config import RedactionLevel


class CredentialFilter:
    """Redact sensitive data from raw file text.

    This MUST be applied before any parsing or LLM exposure.
    Platform-agnostic -- works for any platform.
    """

    # ── XML namespace / standard domains that should NEVER be redacted ──
    _SAFE_DOMAINS: frozenset[str] = frozenset({
        "w3.org", "omg.org", "eclipse.org", "xml.org", "xmlsoap.org",
        "apache.org", "sun.com", "java.com", "oracle.com", "microsoft.com",
        "xmlns.com", "talend.com", "talend.org", "schema.org", "purl.org",
        "lobster-world.com",
    })

    # xmlns declarations are structural XML identifiers, not data: redacting
    # their URIs breaks namespaced attribute lookups in every downstream
    # parser (e.g. xsi:type resolution). Their values are masked before any
    # pattern runs and restored verbatim afterwards.
    _XMLNS_DECL_RE = re.compile(
        r"\bxmlns(?::[A-Za-z_][\w.\-]*)?\s*=\s*(\"[^\"]*\"|'[^']*')"
    )

    # ── Known TLDs for FQDN matching ────────────────────────────────────
    _TLDS: str = (
        r"com|net|org|io|cloud|local|internal|dev|app|info|biz|co|eu|nl|de|uk|fr|be"
    )

    # Lobster-specific: only apply to .xml files (avoid false positives in non-XML text)
    _LOBSTER_BASE64_RE = re.compile(r"(<value>)([A-Za-z0-9+/]{8,}={0,2})(</value>)")

    # ── Compiled patterns per level ─────────────────────────────────────
    # Each entry: (regex, replacement, min_level)
    _BASE_PATTERNS: list[tuple[re.Pattern[str], str, RedactionLevel]] = [
        # Talend encrypted passwords
        (re.compile(r"enc:system\.encryption\.key\.v\d+:[^\s\"'<>]+"),
         "[ENCRYPTED_PASSWORD]", RedactionLevel.STANDARD),
        # id_Password field values in XML
        (re.compile(
            r'(<elementParameter\s+field="[^"]*"\s+name="id_Password"\s+value=")[^"]*(")',
            re.IGNORECASE,
        ), r'\1[PASSWORD_REDACTED]\2', RedactionLevel.STANDARD),
        # Alternative password param ordering
        (re.compile(
            r'(<elementParameter\s+[^>]*name="id_Password"[^>]*value=")[^"]*(")',
            re.IGNORECASE,
        ), r'\1[PASSWORD_REDACTED]\2', RedactionLevel.STANDARD),
        # IP addresses (but not version-like patterns e.g. 1.2.3)
        (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
         "[SERVER_IP]", RedactionLevel.STANDARD),
        # JDBC URLs with embedded credentials
        (re.compile(r"jdbc:[a-zA-Z]+://[^\s\"'<>]*:[^\s\"'<>]*@[^\s\"'<>]+"),
         "[CONNECTION_STRING_REDACTED]", RedactionLevel.STANDARD),
        # Generic connection strings with user:pass@
        (re.compile(r"://[^\s\"'<>]+:[^\s\"'<>]+@[^\s\"'<>]+"),
         "[CONNECTION_STRING_REDACTED]", RedactionLevel.STANDARD),
        # Credential file paths (always, even at standard)
        (re.compile(
            r"[^\s\"'<>]*(Credentials|gcp\.json|service_account|\.pem|\.key)[^\s\"'<>]*",
            re.IGNORECASE,
        ), "[CREDENTIAL_PATH]", RedactionLevel.STANDARD),
        # X509 certificate blocks
        (re.compile(r"-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----"),
         "", RedactionLevel.STANDARD),
        # Private key blocks
        (re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----"),
         "", RedactionLevel.STANDARD),

        # ── New standard-level patterns ─────────────────────────────
        # Email addresses
        (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
         "[EMAIL_REDACTED]", RedactionLevel.STANDARD),
        # URLs with protocol -- handled by _filter_urls() so safe domains
        # (XML namespace hosts) are excluded at runtime, like FQDNs below.
        # FQDNs -- matched against known TLDs, safe domains excluded at runtime
        # This is a placeholder; actual FQDN filtering uses _filter_fqdns()

        # ── HTTP authentication schemes ─────────────────────────────────────
        # Full Authorization header value — must come before bare Bearer pattern
        (re.compile(r"(?i)\bAuthorization\s*[:=]\s*\S+\s+[A-Za-z0-9._+/=\-]{10,}"),
         "[AUTH_HEADER_REDACTED]", RedactionLevel.STANDARD),
        # Bearer tokens (standalone, without Authorization prefix)
        (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._+/=\-]{10,}"),
         "[BEARER_TOKEN_REDACTED]", RedactionLevel.STANDARD),
        # Basic auth (base64 encoded user:pass)
        (re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]{10,}"),
         "[BASIC_AUTH_REDACTED]", RedactionLevel.STANDARD),
        # Custom API key header values
        (re.compile(
            r"(?i)\b(?:X-API-Key|X-Auth-Token|Api-Key|X-Access-Token|X-Token|X-Client-Secret)\s*[:=]\s*[A-Za-z0-9._+/=\-]{10,}"
        ), "[API_KEY_REDACTED]", RedactionLevel.STANDARD),

        # ── JWT tokens (three-part base64url) ────────────────────────────────
        (re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
         "[JWT_REDACTED]", RedactionLevel.STANDARD),

        # ── Source-control platform tokens ───────────────────────────────────
        # GitHub PATs (ghp_, gho_, ghs_, ghr_, ghu_, ghx_) and fine-grained PATs
        (re.compile(r"\bgh[phosrux]_[A-Za-z0-9_]{10,}\b|\bgithub_pat_[A-Za-z0-9_]{10,}\b"),
         "[GITHUB_TOKEN_REDACTED]", RedactionLevel.STANDARD),
        # GitLab PATs / project tokens
        (re.compile(r"\bglpat-[A-Za-z0-9_\-]{10,}\b|\bglptt-[A-Za-z0-9_\-]{10,}\b"),
         "[GITLAB_TOKEN_REDACTED]", RedactionLevel.STANDARD),
        # NPM access tokens
        (re.compile(r"\bnpm_[A-Za-z0-9]{20,}\b"),
         "[NPM_TOKEN_REDACTED]", RedactionLevel.STANDARD),
        # PyPI API tokens
        (re.compile(r"\bpypi-[A-Za-z0-9_\-]{40,}\b"),
         "[PYPI_TOKEN_REDACTED]", RedactionLevel.STANDARD),

        # ── Cloud provider credentials ────────────────────────────────────────
        # AWS access key IDs (AKIA, ASIA, AROA, ANPA, ANVA, AIDA prefixes)
        (re.compile(r"\b(?:AKIA|ASIA|AROA|ANPA|ANVA|AIDA)[0-9A-Z]{16}\b"),
         "[AWS_ACCESS_KEY_REDACTED]", RedactionLevel.STANDARD),
        # AWS secret access keys (keyword-anchored to avoid false positives)
        (re.compile(
            r"(?i)\baws[_\-]?secret[_\-]?access[_\-]?key\s*[:=]\s*[\"']?[A-Za-z0-9+/]{40}[\"']?"
        ), "[AWS_SECRET_KEY_REDACTED]", RedactionLevel.STANDARD),
        # GCP / Google API keys (AIzaSy prefix)
        (re.compile(r"\bAIzaSy[A-Za-z0-9_\-]{33}\b"),
         "[GCP_API_KEY_REDACTED]", RedactionLevel.STANDARD),
        # Firebase server keys (AAAA prefix)
        (re.compile(r"\bAAAA[A-Za-z0-9_\-]{7}:[A-Za-z0-9_\-]{140}\b"),
         "[FIREBASE_KEY_REDACTED]", RedactionLevel.STANDARD),
        # Azure storage account keys
        (re.compile(r"(?i)\bAccountKey\s*=\s*[A-Za-z0-9+/]{40,}={0,2}"),
         "[AZURE_ACCOUNT_KEY_REDACTED]", RedactionLevel.STANDARD),
        # Databricks personal access tokens
        (re.compile(r"\bdapi[a-f0-9]{32}\b"),
         "[DATABRICKS_TOKEN_REDACTED]", RedactionLevel.STANDARD),

        # ── AI / LLM provider keys ────────────────────────────────────────────
        # OpenAI / Anthropic / generic sk- keys
        (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}\b"),
         "[LLM_API_KEY_REDACTED]", RedactionLevel.STANDARD),

        # ── Communication & SaaS provider keys ───────────────────────────────
        # Slack bot/user/app tokens
        (re.compile(r"\bxox[bpaos]-[0-9A-Za-z\-]{10,}\b"),
         "[SLACK_TOKEN_REDACTED]", RedactionLevel.STANDARD),
        # Stripe keys (live and test, secret and publishable)
        (re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{10,}\b"),
         "[STRIPE_KEY_REDACTED]", RedactionLevel.STANDARD),
        # SendGrid API keys
        (re.compile(r"\bSG\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
         "[SENDGRID_KEY_REDACTED]", RedactionLevel.STANDARD),
        # Twilio SIDs / auth tokens
        (re.compile(r"\b(?:AC|SK)[a-f0-9]{32}\b"),
         "[TWILIO_KEY_REDACTED]", RedactionLevel.STANDARD),
        # Mailgun API keys
        (re.compile(r"\bkey-[a-f0-9]{32}\b"),
         "[MAILGUN_KEY_REDACTED]", RedactionLevel.STANDARD),
        # HubSpot private app tokens
        (re.compile(r"\bpat-[a-z]{2}-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
         "[HUBSPOT_TOKEN_REDACTED]", RedactionLevel.STANDARD),

        # ── Lobster-specific patterns ────────────────────────────────
        # OBF-encoded passwords (Lobster obfuscation format)
        (re.compile(r"OBF:[a-zA-Z0-9+/=]+"),
         "[OBF_PASSWORD_REDACTED]", RedactionLevel.STANDARD),
        # Base64-encoded value tags (Lobster filter constants) — XML-only, guarded in filter_text
        (_LOBSTER_BASE64_RE,
         r"\1[BASE64_REDACTED]\3", RedactionLevel.STRICT),

        # ── Strict-level patterns ───────────────────────────────────
        # Windows file paths (C:\..., D:\...)
        (re.compile(r"[A-Za-z]:\\(?:[^\s\"'<>\\]+\\)*[^\s\"'<>\\]*"),
         "[FILE_PATH_REDACTED]", RedactionLevel.STRICT),
        # Unix sensitive paths (/home/, /opt/, /var/, /etc/, /srv/, /tmp/)
        (re.compile(r"/(?:home|opt|var|etc|srv|tmp|usr/local)/[^\s\"'<>]*"),
         "[FILE_PATH_REDACTED]", RedactionLevel.STRICT),
    ]

    # URL pattern (applied separately for safe-domain exclusion)
    _URL_RE = re.compile(
        r"(?:https?|amqps?|s?ftp|tcp)://[^\s\"'<>]+",
        re.IGNORECASE,
    )
    _URL_HOST_RE = re.compile(
        r"^[a-z][a-z0-9+.\-]*://(?:[^/@\s]*@)?([^/:?#\s]+)",
        re.IGNORECASE,
    )

    # FQDN pattern (applied separately for safe-domain exclusion)
    _FQDN_RE = re.compile(
        r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+("
        + r"com|net|org|io|cloud|local|internal|dev|app|info|biz|co|eu|nl|de|uk|fr|be"
        + r")\b",
        re.IGNORECASE,
    )

    # Key names whose values should be redacted (case-insensitive match)
    _SENSITIVE_KEYS = frozenset({
        "password", "secret", "token", "apikey", "api_key",
        "bearer", "client_secret", "private_key", "access_key",
        "authorization", "x-api-key", "x-auth-token", "x-access-token",
    })

    # Pattern for XML-style key=value where key is sensitive
    _SENSITIVE_KV = re.compile(
        r'((?:name|field|key)\s*=\s*"(?:' +
        "|".join(_SENSITIVE_KEYS) +
        r')"[^>]*value\s*=\s*")[^"]*(")',
        re.IGNORECASE,
    )

    # ── Variable name patterns that indicate sensitive values ───────
    _SENSITIVE_VAR_SUFFIXES = (
        "_password", "_pass", "_pwd", "_secret", "_token", "_apikey",
        "_api_key", "_bearer", "_client_secret", "_private_key",
        "_access_key", "_credentials",
    )
    _SENSITIVE_VAR_EXACT = frozenset({
        "password", "secret", "token", "apikey", "api_key",
        "bearer", "client_secret", "private_key", "access_key",
    })
    _INFRA_VAR_SUFFIXES = (
        "_user", "_username", "_host", "_hostname", "_server",
        "_url", "_endpoint", "_broker", "_uri",
    )

    def __init__(
        self,
        level: RedactionLevel = RedactionLevel.STANDARD,
        config_path: Path | None = None,
    ) -> None:
        self._level = level
        self._audit: dict[str, list[str]] = defaultdict(list)  # category -> [sha256 hashes]
        self._custom_patterns: list[tuple[re.Pattern[str], str, RedactionLevel]] = []
        self._custom_safe_domains: set[str] = set()
        self._custom_sensitive_vars: list[str] = []

        if config_path and config_path.exists():
            self._load_custom_config(config_path)

        # Build level-filtered pattern list
        self._active_patterns = [
            (pat, repl) for pat, repl, min_level in self._BASE_PATTERNS
            if self._level_includes(min_level)
        ] + [
            (pat, repl) for pat, repl, min_level in self._custom_patterns
            if self._level_includes(min_level)
        ]

    @property
    def level(self) -> RedactionLevel:
        return self._level

    def _level_includes(self, min_level: RedactionLevel) -> bool:
        """Check if current level includes patterns at min_level."""
        order = {RedactionLevel.STANDARD: 0, RedactionLevel.STRICT: 1, RedactionLevel.PARANOID: 2}
        return order[self._level] >= order[min_level]

    def _load_custom_config(self, path: Path) -> None:
        """Load custom redaction rules from .intodoc-redact.yaml."""
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
        except Exception:
            return

        # Custom regex patterns
        for entry in data.get("patterns", []):
            if "regex" in entry and "replacement" in entry:
                level_str = entry.get("level", "standard")
                try:
                    level = RedactionLevel(level_str)
                except ValueError:
                    level = RedactionLevel.STANDARD
                try:
                    compiled = re.compile(entry["regex"])
                    self._custom_patterns.append((compiled, entry["replacement"], level))
                except re.error:
                    pass

        # Custom safe domains
        for domain in data.get("safe_domains", []):
            if isinstance(domain, str):
                self._custom_safe_domains.add(domain.lower())

        # Custom sensitive variable name patterns
        for var_pat in data.get("sensitive_vars", []):
            if isinstance(var_pat, str):
                self._custom_sensitive_vars.append(var_pat.lower())

    def _record_redaction(self, category: str, original: str) -> None:
        """Record a redaction for the audit report (hash only, never raw value)."""
        h = hashlib.sha256(original.encode("utf-8", errors="replace")).hexdigest()[:16]
        self._audit[category].append(h)

    def _is_safe_domain(self, fqdn: str) -> bool:
        """Check if an FQDN is a safe (XML namespace / standard) domain."""
        fqdn_lower = fqdn.lower()
        for safe in self._SAFE_DOMAINS | self._custom_safe_domains:
            if fqdn_lower == safe or fqdn_lower.endswith("." + safe):
                return True
        return False

    def _filter_fqdns(self, text: str) -> str:
        """Replace FQDNs with [HOSTNAME], preserving safe domains."""
        def _replace_fqdn(m: re.Match[str]) -> str:
            fqdn = m.group(0)
            if self._is_safe_domain(fqdn):
                return fqdn
            self._record_redaction("fqdn", fqdn)
            return "[HOSTNAME]"
        return self._FQDN_RE.sub(_replace_fqdn, text)

    def _filter_urls(self, text: str) -> str:
        """Replace URLs with [URL_REDACTED], preserving safe-domain hosts."""
        def _replace_url(m: re.Match[str]) -> str:
            url = m.group(0)
            host_match = self._URL_HOST_RE.match(url)
            if host_match and self._is_safe_domain(host_match.group(1)):
                return url
            self._record_redaction("url_redacted", url)
            return "[URL_REDACTED]"
        return self._URL_RE.sub(_replace_url, text)

    def _mask_xmlns_decls(self, text: str) -> tuple[str, list[str]]:
        """Replace xmlns declaration values with placeholders before redaction."""
        masked: list[str] = []

        def _mask(m: re.Match[str]) -> str:
            masked.append(m.group(1))
            decl = m.group(0)
            return decl[: -len(m.group(1))] + f'"__INTODOC_XMLNS_{len(masked) - 1}__"'

        return self._XMLNS_DECL_RE.sub(_mask, text), masked

    @staticmethod
    def _restore_xmlns_decls(text: str, masked: list[str]) -> str:
        """Restore xmlns declaration values masked by _mask_xmlns_decls."""
        for i, value in enumerate(masked):
            text = text.replace(f'"__INTODOC_XMLNS_{i}__"', value)
        return text

    def filter_text(self, text: str, filename: str = "") -> str:
        """Apply all redaction patterns to raw text. Returns cleaned text."""
        stripped = text.lstrip()
        is_xml = filename.lower().endswith(".xml") or (
            stripped.startswith("<?xml") or stripped.startswith("<")
        )
        xmlns_values: list[str] = []
        if is_xml:
            text, xmlns_values = self._mask_xmlns_decls(text)
        for pattern, replacement in self._active_patterns:
            if pattern is self._LOBSTER_BASE64_RE and not is_xml:
                continue
            # Use a wrapper to track audit for patterns with simple replacements
            def _make_repl(pat: re.Pattern[str], repl: str):  # noqa: E301
                def _repl(m: re.Match[str]) -> str:
                    original = m.group(0)
                    # Determine category from replacement tag
                    cat = repl.strip("[]").lower() if repl.startswith("[") else "other"
                    self._record_redaction(cat, original)
                    # Handle backreferences
                    if "\\" in repl or r"\1" in repl:
                        return m.expand(repl)
                    return repl
                return _repl

            text = pattern.sub(_make_repl(pattern, replacement), text)

        # Sensitive key-value pairs
        def _kv_repl(m: re.Match[str]) -> str:
            self._record_redaction("sensitive_kv", m.group(0))
            return m.group(1) + "[REDACTED]" + m.group(2)

        text = self._SENSITIVE_KV.sub(_kv_repl, text)

        # URL and FQDN filtering (standard+), safe domains preserved
        if self._level_includes(RedactionLevel.STANDARD):
            text = self._filter_urls(text)
            text = self._filter_fqdns(text)

        if xmlns_values:
            text = self._restore_xmlns_decls(text, xmlns_values)

        return text

    def is_sensitive_var_name(self, name: str) -> bool:
        """Check if a context variable name indicates a sensitive value.

        Returns True for password/secret vars at all levels,
        and for infra vars (_user, _host, etc.) at strict+ levels.
        """
        name_lower = name.lower()

        # Always sensitive: password, secret, token vars
        if name_lower in self._SENSITIVE_VAR_EXACT:
            return True
        for suffix in self._SENSITIVE_VAR_SUFFIXES:
            if name_lower.endswith(suffix):
                return True

        # id_Password type (Talend convention)
        if name_lower == "id_password":
            return True

        # Custom sensitive var patterns
        for pat in self._custom_sensitive_vars:
            if pat in name_lower:
                return True

        # Infrastructure vars: only at strict+
        if self._level_includes(RedactionLevel.STRICT):
            for suffix in self._INFRA_VAR_SUFFIXES:
                if name_lower.endswith(suffix):
                    return True

        return False

    def has_credentials(self, text: str) -> bool:
        """Quick check if text likely contains credentials."""
        for pattern, _ in self._active_patterns:
            if pattern.search(text):
                return True
        if self._SENSITIVE_KV.search(text):
            return True
        if self._level_includes(RedactionLevel.STANDARD):
            for m in self._URL_RE.finditer(text):
                host_match = self._URL_HOST_RE.match(m.group(0))
                if not (host_match and self._is_safe_domain(host_match.group(1))):
                    return True
            for m in self._FQDN_RE.finditer(text):
                if not self._is_safe_domain(m.group(0)):
                    return True
        return False

    def get_audit_report(self) -> dict[str, Any]:
        """Return audit data: category counts + hashes. Never raw values."""
        return {
            "redaction_level": self._level.value,
            "summary": {cat: len(hashes) for cat, hashes in sorted(self._audit.items())},
            "total_redactions": sum(len(h) for h in self._audit.values()),
            "details": {
                cat: {"count": len(hashes), "hashes": hashes}
                for cat, hashes in sorted(self._audit.items())
            },
        }

    def reset_audit(self) -> None:
        """Clear the audit trail (e.g., between files)."""
        self._audit.clear()
