"""Fail-closed trust and deterministic-ledger preflight for Grounded execution."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

from mcp_server.experiments.application.fact_ledger import (
    compile_fact_ledger,
    validate_fact_ledger_preflight,
)
from mcp_server.experiments.application.run_quick_candidate import QuickCandidateError


_SENSITIVE_KEY = re.compile(
    r"(?:^|[^a-z0-9])(?:password|passwd|pwd|secret|token|api[_-]?key|"
    r"credential|private[_-]?key|aes[_-]?key)(?:$|[^a-z0-9])",
    re.IGNORECASE,
)
_CAMEL_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|apiKey|accessToken|clientSecret|privateKey|aesKey)$",
    re.IGNORECASE,
)
_URL_USERINFO = re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE)
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_APPROVED_PLACEHOLDERS = frozenset(
    {
        "",
        "[redacted]",
        "[url_redacted]",
        "[credential_redacted]",
        "unknown",
        "default",
        "null",
        "none",
    }
)


def preflight_grounded_sources(
    source_items: tuple[tuple[str, Path], ...],
    *,
    max_source_items: int,
    max_source_bytes: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate approved scanner content and compile a trusted fact ledger.

    Error detail contains only categories and logical paths; raw values never
    leave this boundary.
    """

    if not source_items:
        raise QuickCandidateError("grounded preflight requires frozen sources")
    if len(source_items) > max_source_items:
        raise QuickCandidateError("grounded source item budget exceeded")

    total_bytes = 0
    json_sources: list[tuple[str, Any]] = []
    receipt_approved = False
    findings: list[dict[str, str]] = []
    for logical_identity, physical_path in source_items:
        relative = _logical_relative_path(logical_identity)
        try:
            size = physical_path.stat().st_size
        except OSError as exc:
            raise QuickCandidateError(
                "grounded source preflight could not read frozen content"
            ) from exc
        total_bytes += size
        if total_bytes > max_source_bytes:
            raise QuickCandidateError("grounded source byte budget exceeded")
        if PurePosixPath(relative).suffix.casefold() != ".json":
            _collect_text_source_findings(
                physical_path,
                path=relative,
                findings=findings,
            )
            continue
        try:
            payload = json.loads(physical_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QuickCandidateError(
                f"grounded source is not valid JSON: {relative}"
            ) from exc
        json_sources.append((relative, payload))
        if relative.endswith(("redaction_report.json", "redaction.json")):
            receipt_approved = receipt_approved or _approved_redaction_receipt(payload)
            continue
        _collect_unredacted_findings(payload, path=relative, findings=findings)

    scanner_json_present = any(
        path.startswith("scan/") or path.endswith("scan_result.json")
        for path, _ in json_sources
    )
    if scanner_json_present and not receipt_approved:
        raise QuickCandidateError(
            "grounded source trust preflight requires an approved redaction receipt"
        )
    if findings:
        categories = sorted({finding["category"] for finding in findings})
        paths = sorted({finding["path"] for finding in findings})
        raise QuickCandidateError(
            "grounded source trust preflight rejected possible unredacted credentials "
            f"(categories={','.join(categories)}; files={','.join(paths[:10])})"
        )

    ledger = compile_fact_ledger(source_items)
    ledger_report = validate_fact_ledger_preflight(ledger)
    if ledger_report["status"] != "pass":
        codes = sorted(
            {
                str(item.get("code") or "unknown")
                for item in ledger_report["issues"]
                if isinstance(item, dict)
            }
        )
        raise QuickCandidateError(
            "grounded fact-ledger preflight failed: " + ", ".join(codes)
        )
    return ledger, {
        "schema_version": 1,
        "status": "pass",
        "source_item_count": len(source_items),
        "source_bytes": total_bytes,
        "json_source_count": len(json_sources),
        "redaction_receipt_approved": receipt_approved,
        "secret_finding_count": 0,
        "ledger": ledger_report,
    }


def reject_secret_like_output(
    outputs: dict[str, str],
) -> tuple[dict[str, str], ...]:
    """Validate output secrets, returning descriptive warnings and blocking real secrets.

    Documentation commonly mentions credential fields and SQL columns. Those
    references are useful evidence and must not be treated as leaked values.
    High-confidence secret material remains fail-closed.
    """

    findings: list[dict[str, str]] = []
    for name, text in outputs.items():
        if name.casefold().endswith(".json"):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None
            if payload is not None:
                _collect_unredacted_findings(
                    payload,
                    path=name,
                    findings=findings,
                )
        if _URL_USERINFO.search(text) or _PRIVATE_KEY.search(text):
            findings.append(
                {"path": name, "category": "secret_material", "severity": "block"}
            )
        for line in text.splitlines():
            if ":" not in line and "=" not in line:
                continue
            key, value = re.split(r"[:=]", line, maxsplit=1)
            if _is_sensitive_key(key) and not _safe_sensitive_value(value):
                findings.append(
                    {
                        "path": name,
                        "category": "credential_assignment",
                        "severity": (
                            "block" if _looks_like_secret_value(value) else "warning"
                        ),
                    }
                )
                break
    blockers = [item for item in findings if item.get("severity") == "block"]
    if blockers:
        raise QuickCandidateError(
            "grounded candidate output failed the secret-safety gate"
        )
    return tuple(
        {
            "path": item["path"],
            "category": item["category"],
            "severity": "warning",
        }
        for item in findings
    )


def _approved_redaction_receipt(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("approved") is True or value.get("status") == "approved":
        return True
    summary = value.get("summary")
    total = value.get("total_redactions")
    return (
        value.get("redaction_level") in {"standard", "strict"}
        and isinstance(total, int)
        and not isinstance(total, bool)
        and total >= 0
        and isinstance(summary, dict)
        and all(
            isinstance(key, str)
            and isinstance(count, int)
            and not isinstance(count, bool)
            and count >= 0
            for key, count in summary.items()
        )
        and sum(summary.values()) == total
    )


def _collect_unredacted_findings(
    value: Any,
    *,
    path: str,
    findings: list[dict[str, str]],
) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_unredacted_findings(item, path=path, findings=findings)
        return
    if not isinstance(value, dict):
        if isinstance(value, str) and (
            _URL_USERINFO.search(value) or _PRIVATE_KEY.search(value)
        ):
            findings.append(
                {
                    "path": path,
                    "category": "embedded_secret_material",
                    "severity": "block",
                }
            )
        return

    for key, nested in value.items():
        key_text = str(key)
        if _is_sensitive_key(key_text):
            for scalar in _scalars(nested):
                if isinstance(scalar, str) and not _safe_sensitive_value(scalar):
                    findings.append(
                        {
                            "path": path,
                            "category": "sensitive_key_value",
                            "severity": (
                                "block"
                                if _looks_like_secret_value(scalar)
                                else "warning"
                            ),
                        }
                    )
                    break
        _collect_unredacted_findings(nested, path=path, findings=findings)

    name = value.get("name")
    if isinstance(name, str) and _is_sensitive_key(name):
        for scalar in _scalars(value.get("values", value.get("value"))):
            if isinstance(scalar, str) and not _safe_sensitive_value(scalar):
                findings.append(
                    {
                        "path": path,
                        "category": "named_sensitive_value",
                        "severity": (
                            "block"
                            if _looks_like_secret_value(scalar)
                            else "warning"
                        ),
                    }
                )
                break


def _collect_text_source_findings(
    source: Path,
    *,
    path: str,
    findings: list[dict[str, str]],
) -> None:
    """Inspect textual generic inputs without ever surfacing their values."""

    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise QuickCandidateError(
            "grounded source preflight could not read frozen content"
        ) from exc
    if b"\x00" in raw[:8192]:
        return
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return
    if _URL_USERINFO.search(text) or _PRIVATE_KEY.search(text):
        findings.append({"path": path, "category": "embedded_secret_material"})
    for line in text.splitlines():
        if ":" not in line and "=" not in line:
            continue
        key, value = re.split(r"[:=]", line, maxsplit=1)
        if _is_sensitive_key(key) and not _safe_sensitive_value(value):
            findings.append({"path": path, "category": "sensitive_key_value"})
            break


def _safe_sensitive_value(value: str) -> bool:
    normalized = value.strip().strip("`'\"").casefold()
    if normalized in _APPROVED_PLACEHOLDERS:
        return True
    return (
        normalized.startswith(("context.", "${", "{{", "env.", "system.getenv("))
        or normalized.endswith((".password", ".secret", ".token", ".api_key"))
        or "encrypted" in normalized
        or "redacted" in normalized
    )


def _looks_like_secret_value(value: str) -> bool:
    """Identify likely concrete values without blocking explanatory prose."""

    normalized = value.strip().strip("`'\"")
    if not normalized or _safe_sensitive_value(normalized):
        return False
    if _URL_USERINFO.search(normalized) or _PRIVATE_KEY.search(normalized):
        return True
    if re.search(
        r"(?:\b(?:input_row|output_row|globalMap|context)\b|"
        r"[A-Za-z_]\w*\s*\.\s*[A-Za-z_]|[;(){}])",
        normalized,
    ):
        return False
    # A compact, opaque assignment is materially different from SQL, a field
    # path, or a sentence describing a credential-bearing column.
    if any(char.isspace() for char in normalized):
        return False
    if any(marker in normalized.casefold() for marker in ("select ", "from ", "where ", "uses_", "uses ")):
        return False
    return len(normalized) >= 12 and not normalized.casefold().startswith(("<", "["))


def _is_sensitive_key(value: str) -> bool:
    folded = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).casefold()
    return bool(_SENSITIVE_KEY.search(folded) or _CAMEL_SENSITIVE_KEY.search(value))


def _scalars(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [item for nested in value.values() for item in _scalars(nested)]
    if isinstance(value, list):
        return [item for nested in value for item in _scalars(nested)]
    return [value]


def _logical_relative_path(logical_identity: str) -> str:
    return logical_identity.split(":", 1)[-1].strip().replace("\\", "/")
