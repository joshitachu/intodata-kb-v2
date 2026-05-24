"""Sanitize subprocess/git error messages before sending them to clients.

Raw git/subprocess output can contain credential tokens (embedded in URLs),
absolute file paths with internal project names, and remote host details.
This module classifies known patterns into safe error codes and strips
anything that remains.
"""

from __future__ import annotations

import re

# Ordered: first match wins for classification
_CLASSIFICATION_PATTERNS: list[tuple[str, str]] = [
    # Auth failures
    (r"authentication failed", "error_type: authentication_failed"),
    (r"invalid username or token", "error_type: authentication_failed"),
    (r"bad credentials", "error_type: authentication_failed"),
    (r"password authentication is not supported", "error_type: authentication_failed"),
    (r"remote: invalid username", "error_type: authentication_failed"),
    (r"\b401\b", "error_type: authentication_failed"),
    (r"\b403\b", "error_type: authentication_failed"),
    # Ownership / permission
    (r"dubious ownership", "error_type: repository_ownership_error"),
    # Repository not found / access denied
    (r"repository not found", "error_type: repository_not_found"),
    (r"not found", "error_type: repository_not_found"),
    # Timeout
    (r"timed? ?out", "error_type: timeout"),
    # Network
    (r"could not resolve host", "error_type: network_error"),
    (r"connection refused", "error_type: network_error"),
    (r"connection timed? ?out", "error_type: network_error"),
]

_SUPPRESS_PATTERNS: list[str] = [
    r"safe\.directory",          # git safe.directory hint — no value to user
    r"^hint:",                   # git hint lines
]

_URL_RE = re.compile(r"https?://\S+|git@[^\s:]+:[^\s]+")
_PATH_RE = re.compile(r"(/[^\s]+){2,}")  # two or more path segments


def sanitize_git_error(text: str) -> str:
    """Return a client-safe version of *text*.

    Classifies known sensitive patterns into error codes. For anything that
    does not match a classification, strips embedded URLs and absolute paths.
    Empty strings and None-like values are passed through unchanged.
    """
    if not text:
        return text

    lower = text.lower()

    for pattern, code in _CLASSIFICATION_PATTERNS:
        if re.search(pattern, lower):
            return code

    # Not a classified error — strip sensitive substrings line by line
    sanitized_lines: list[str] = []
    for line in text.splitlines():
        lower_line = line.lower().strip()
        if any(re.search(p, lower_line) for p in _SUPPRESS_PATTERNS):
            continue
        line = _URL_RE.sub("[url]", line)
        line = _PATH_RE.sub("[path]", line)
        sanitized_lines.append(line)

    return "\n".join(sanitized_lines).strip()


def sanitize_multiline_git_error(lines: list[str]) -> str:
    """Sanitize a list of raw output lines and return a single safe string.

    Classifies the entire block first (if any line triggers a classification
    the whole message collapses to that code). Otherwise strips line by line.
    """
    combined = "\n".join(lines)
    return sanitize_git_error(combined)
