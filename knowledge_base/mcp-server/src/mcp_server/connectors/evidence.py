"""Bounded normalization and redaction for untrusted provider observations."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from typing import Any, Mapping

from .domain import EvidenceEnvelope, EvidenceItem


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET_KEY = re.compile(
    r"(?:password|secret|token|authorization|api[_-]?key|cookie|credential)", re.I
)
_BEARER = re.compile(r"\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}", re.I)
_URL_USERINFO = re.compile(r"(https?://)[^\s/@:]+(?::[^\s/@]*)?@", re.I)
_JDBC_USERINFO = re.compile(r"(jdbc:[a-z0-9+.-]+://)[^\s/@:]+(?::[^\s/@]*)?@", re.I)
_SECRET_ASSIGNMENT = re.compile(
    r"\b(password|passwd|pwd|secret|token|authorization|api[_-]?key|access[_-]?key|client[_-]?secret)"
    r"(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.I,
)
_LONG_TOKEN = re.compile(r"\b(?:eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9._-]+|[A-Za-z0-9+/=_-]{32,})\b")


def _redact_secret_assignment(match: re.Match[str]) -> str:
    return f"{match.group(1)}{match.group(2)}[REDACTED]"


def redact_value(value: Any, *, max_string_chars: int = 2_000) -> tuple[Any, int, bool]:
    """Return a JSON-safe bounded value, redaction count, and truncation flag."""
    if value is None or isinstance(value, (bool, int, float)):
        return value, 0, False
    if isinstance(value, str):
        cleaned = _CONTROL.sub(" ", value)
        redacted_count = 0
        for pattern in (_BEARER, _URL_USERINFO, _JDBC_USERINFO, _LONG_TOKEN):
            cleaned, count = pattern.subn("[REDACTED]", cleaned)
            redacted_count += count
        # Run assignments after compound bearer/URL forms so an
        # ``Authorization: Bearer <short-token>`` cannot leave the token behind
        # when only the first word is consumed as the assignment value.
        cleaned, count = _SECRET_ASSIGNMENT.subn(_redact_secret_assignment, cleaned)
        redacted_count += count
        truncated = len(cleaned) > max_string_chars
        return cleaned[:max_string_chars], redacted_count, truncated
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        count = 0
        truncated = False
        for raw_key, raw_value in value.items():
            key = str(raw_key)[:128]
            if _SECRET_KEY.search(key):
                out[key] = "[REDACTED]"
                count += 1
                continue
            item, item_count, item_truncated = redact_value(
                raw_value, max_string_chars=max_string_chars
            )
            out[key] = item
            count += item_count
            truncated = truncated or item_truncated
        return out, count, truncated
    if isinstance(value, (list, tuple)):
        out: list[Any] = []
        count = 0
        truncated = False
        for item in value[:100]:
            normalized, item_count, item_truncated = redact_value(
                item, max_string_chars=max_string_chars
            )
            out.append(normalized)
            count += item_count
            truncated = truncated or item_truncated
        return out, count, truncated or len(value) > 100
    return "[UNSUPPORTED_VALUE]", 0, True


def normalize_event_fields(
    fields: Mapping[str, Any], approved_fields: set[str]
) -> tuple[dict[str, Any], int, bool]:
    """Preserve only source-profile-approved fields from an event."""
    filtered = {key: value for key, value in fields.items() if key in approved_fields}
    normalized, redaction_count, truncated = redact_value(filtered)
    assert isinstance(normalized, dict)
    return normalized, redaction_count, truncated


def make_opaque_reference(run_id: int, source_id: str, provider_locator: str) -> str:
    """Stable only within one run; never return source document/index identifiers."""
    digest = hashlib.sha256(f"{run_id}:{source_id}:{provider_locator}".encode()).hexdigest()
    # This prefix is part of the facade's public semantic contract.  It lets
    # ``get_evidence_item`` distinguish a server-issued opaque reference from
    # an arbitrary provider document identifier without exposing the latter.
    return f"evref_{digest[:32]}"


def finalize_envelope(envelope: EvidenceEnvelope, *, max_bytes: int = 131_072) -> EvidenceEnvelope:
    """Redact, cap, and digest a provider-neutral envelope before any caller sees it."""
    items: list[EvidenceItem] = []
    redaction_count = int(envelope.redaction.get("count", 0))
    truncated = bool(envelope.truncation.get("applied", False))
    # Leave a small serialization headroom for summary and metadata.
    item_limit = max(1, min(len(envelope.items), 100))
    for item in envelope.items[:item_limit]:
        fields, count, item_truncated = redact_value(item.fields)
        assert isinstance(fields, dict)
        items.append(
            EvidenceItem(reference=item.reference, observed_at=item.observed_at, fields=fields)
        )
        redaction_count += count
        truncated = truncated or item_truncated
    if len(envelope.items) > item_limit:
        truncated = True
    summary, summary_count, summary_truncated = redact_value(envelope.summary)
    metadata, metadata_count, metadata_truncated = redact_value(envelope.provider_metadata)
    assert isinstance(summary, dict) and isinstance(metadata, dict)
    redaction_count += summary_count + metadata_count
    truncated = truncated or summary_truncated or metadata_truncated
    candidate = replace(
        envelope,
        items=tuple(items),
        summary=summary,
        provider_metadata=metadata,
        redaction={"applied": redaction_count > 0, "count": redaction_count},
        truncation={"applied": truncated},
        digest="",
    )
    payload = envelope_to_dict(candidate)
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    if len(encoded) > max_bytes:
        candidate = replace(
            candidate, items=(), truncation={"applied": True, "reason": "byte_limit"}
        )
        payload = envelope_to_dict(candidate)
        encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    return replace(candidate, digest=f"sha256:{hashlib.sha256(encoded).hexdigest()}")


def envelope_to_dict(envelope: EvidenceEnvelope) -> dict[str, Any]:
    return {
        "evidence_id": envelope.evidence_id,
        "run_id": envelope.run_id,
        "project_id": envelope.project_id,
        "connector_instance_id": envelope.connector_instance_id,
        "source_id": envelope.source_id,
        "capability_id": envelope.capability_id,
        "question": envelope.question,
        "entity_refs": list(envelope.entity_refs),
        "observed_at": envelope.observed_at.isoformat(),
        "source_window": dict(envelope.source_window),
        "items": [
            {
                "reference": item.reference,
                "observed_at": item.observed_at.isoformat() if item.observed_at else None,
                "fields": dict(item.fields),
            }
            for item in envelope.items
        ],
        "summary": dict(envelope.summary),
        "limitations": [
            {
                "code": limitation.code,
                "message": limitation.message,
                "retryable": limitation.retryable,
            }
            for limitation in envelope.limitations
        ],
        "redaction": dict(envelope.redaction),
        "truncation": dict(envelope.truncation),
        "provider_metadata": dict(envelope.provider_metadata),
    }
