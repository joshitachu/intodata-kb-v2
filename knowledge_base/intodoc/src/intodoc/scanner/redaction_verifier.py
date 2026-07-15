"""Post-scan redaction verification gate.

Defense in depth: after both redaction layers (text-level CredentialFilter,
structural post_filter) have run, independently verify the ScanResult holds
no residual secrets BEFORE any output is written. A single missed regex must
never flow silently into published artifacts again.

The report NEVER contains raw values -- only names, locations, categories
and SHA-256 hash prefixes.
"""

from __future__ import annotations

import hashlib
from typing import Any

from intodoc.models import ScanResult
from intodoc.scanner.credential_filter import CredentialFilter


class RedactionVerificationError(RuntimeError):
    """Raised when the scan output still contains suspected secrets."""


def _hash_prefix(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def _is_placeholder(value: str) -> bool:
    """True for redaction placeholders like [PASSWORD_REDACTED], [HOSTNAME]."""
    v = value.strip()
    return v.startswith("[") and v.endswith("]")


def _finding(category: str, location: str, name: str, value: str) -> dict[str, str]:
    return {
        "category": category,
        "location": location,
        "name": name,
        "value_sha256": _hash_prefix(value),
    }


def _check_named_value(
    findings: list[dict[str, str]],
    cred_filter: CredentialFilter,
    location: str,
    name: str,
    value: str,
    var_type: str = "",
) -> None:
    """Flag a name/value pair whose NAME marks it sensitive but whose value survived."""
    if not value or _is_placeholder(value):
        return
    if var_type and var_type.lower() in ("id_password", "password"):
        findings.append(_finding("unredacted_password_type", location, name, value))
    elif cred_filter.is_sensitive_var_name(name):
        findings.append(_finding("unredacted_sensitive_name", location, name, value))


def _walk_strings(node: Any, path: str, out: list[tuple[str, str]]) -> None:
    """Collect (path, string) leaves from a model_dump tree."""
    if isinstance(node, str):
        out.append((path, node))
    elif isinstance(node, dict):
        for k, v in node.items():
            _walk_strings(v, f"{path}/{k}", out)
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            _walk_strings(v, f"{path}/{i}", out)


def verify_scan_result(
    result: ScanResult,
    cred_filter: CredentialFilter,
) -> dict[str, Any]:
    """Verify a filtered ScanResult carries no residual secrets.

    Checks:
    1. No context variable / component param whose NAME is sensitive (per
       the token-aware matcher) still carries a non-placeholder value.
    2. No id_Password-typed value survives unredacted.
    3. Leaf-string sweep with the same detectors the filter uses: anything
       detected now, by construction, escaped the filter.

    Returns a report dict with `passed`, `findings`, and
    `residual_pattern_counts`. Raw values never appear in the report.
    """
    findings: list[dict[str, str]] = []
    checked_values = 0

    # 1+2: name/type-driven checks on structured fields
    for cg in result.context_groups:
        for var in cg.variables:
            for env, val in var.values.items():
                checked_values += 1
                _check_named_value(
                    findings, cred_filter,
                    f"context_groups/{cg.name}/{var.name}/{env}",
                    var.name, val, var.type,
                )

    for job in result.jobs:
        for cg in job.context_groups:
            for var in cg.variables:
                for env, val in var.values.items():
                    checked_values += 1
                    _check_named_value(
                        findings, cred_filter,
                        f"jobs/{job.name}/context/{cg.name}/{var.name}/{env}",
                        var.name, val, var.type,
                    )
        for comp in job.components:
            for p in comp.params:
                checked_values += 1
                _check_named_value(
                    findings, cred_filter,
                    f"jobs/{job.name}/components/{comp.unique_name}/{p.name}",
                    p.name, p.value,
                )

    for conn in result.metadata_connections:
        for key, val in conn.additional_params.items():
            if isinstance(val, str):
                checked_values += 1
                _check_named_value(
                    findings, cred_filter,
                    f"metadata_connections/{conn.name}/{key}",
                    key, val,
                )

    # 3: leaf-string sweep with the filter's own detectors. Walk the model
    # dump so values are raw (not JSON-escaped) strings.
    leaves: list[tuple[str, str]] = []
    _walk_strings(result.model_dump(mode="json"), "", leaves)
    residual: dict[str, int] = {}
    residual_locations: list[str] = []
    for path, text in leaves:
        if len(text) < 8 or _is_placeholder(text):
            continue
        detected = cred_filter.detect(text)
        if detected:
            for cat, count in detected.items():
                residual[cat] = residual.get(cat, 0) + count
            if len(residual_locations) < 50:
                residual_locations.append(path)

    passed = not findings and not residual
    return {
        "passed": passed,
        "checked_values": checked_values,
        "checked_string_leaves": len(leaves),
        "findings": findings,
        "residual_pattern_counts": residual,
        "residual_locations": residual_locations,
    }
