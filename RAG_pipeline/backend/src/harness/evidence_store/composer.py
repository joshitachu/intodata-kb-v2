"""Deterministic, safe composition for operational investigations."""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field

from .models import EvidenceSnapshot, EvidenceSourceClass

_SPACE_RE = re.compile(r"\s+")
_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|authorization|cookie|api[_-]?key)\b"
    r"\s*[:=]\s*([^\s,;]+)"
)


class InvestigationConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class InvestigationSourceSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    title: str
    summary: str
    uri: str | None = None


class InvestigationArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    url: str
    kind: str | None = None


class InvestigationAnswer(BaseModel):
    """Stable answer shape safe for chat, API, and workflow receipts."""

    model_config = ConfigDict(frozen=True)

    direct_status: str
    likely_cause: str
    confidence: InvestigationConfidence
    confidence_score: float = Field(ge=0, le=1)
    qualifications: list[str] = Field(default_factory=list)
    next_action: str
    source_summary: list[InvestigationSourceSummary] = Field(default_factory=list)
    missing_required_sources: list[str] = Field(default_factory=list)
    artifacts: list[InvestigationArtifact] = Field(default_factory=list)
    answered: bool = False

    @property
    def has_supported_status(self) -> bool:
        return self.direct_status != (
            "The current status could not be established from the available evidence."
        )

    @property
    def has_supported_cause(self) -> bool:
        return self.likely_cause != (
            "The available evidence does not establish a likely cause."
        )

    def as_text(self) -> str:
        sections: list[str] = []
        if self.has_supported_status:
            sections.append(f"Status: {self.direct_status}")
        if self.has_supported_cause:
            sections.append(f"Likely cause: {self.likely_cause}")
        if self.has_supported_status or self.has_supported_cause:
            sections.append(f"Confidence: {self.confidence.value}")
        else:
            sections.append(
                "I could not determine the current execution status or root cause from "
                "the available evidence."
            )
        useful_qualifications = [
            item
            for item in self.qualifications
            if not item.startswith("A complete investigation requires ")
            and item != "No source supplied a supported cause claim."
        ]
        if useful_qualifications:
            sections.append("Qualifications: " + " ".join(useful_qualifications))
        sections.append(f"Next action: {self.next_action}")
        if self.missing_required_sources:
            sections.append(
                "Unavailable evidence: " + ", ".join(self.missing_required_sources) + "."
            )
        return "\n".join(sections)


class OperationalAnswerComposer:
    """Compose claims without exposing evidence payloads or executor identifiers."""

    def compose(
        self,
        evidence: Iterable[EvidenceSnapshot],
        *,
        missing_required_sources: Iterable[str] = (),
        artifacts: Iterable[InvestigationArtifact | dict[str, str | None]] = (),
    ) -> InvestigationAnswer:
        items = classify_evidence_claims(list(evidence))
        missing = sorted({_safe_text(item) for item in missing_required_sources if item})
        forbidden = {
            item.capability_id
            for item in items
            if item.capability_id and len(item.capability_id) >= 3
        }

        status_claims = _claims(items, {"status", "current_status", "execution_status"})
        cause_claims = _claims(items, {"cause", "likely_cause", "root_cause"})
        action_claims = _claims(items, {"next_action", "recommended_action"})
        status_value = _status_value(status_claims, forbidden, evidence=items)
        cause_value = _claim_value(cause_claims, forbidden)
        if cause_value is None and not cause_claims:
            cause_value = _inferred_cause(items, forbidden)
        direct_status = status_value or (
            "The current status could not be established from the available evidence."
        )
        likely_cause = cause_value or (
            "The available evidence does not establish a likely cause."
        )

        conflicting_keys = sorted(
            {
                _safe_text(item.claim_key or "this claim", forbidden)
                for item in items
                if item.conflict_group
            }
        )
        qualifications: list[str] = []
        if conflicting_keys:
            qualifications.append(
                "Sources conflict about " + ", ".join(conflicting_keys) + "."
            )
            qualifications.extend(_conflict_value_qualifications(items, forbidden))
        stale_sources = sorted(
            {_source_label(item.source_class) for item in items if item.freshness < 0.5}
        )
        if stale_sources:
            qualifications.append(
                "Some evidence may be stale: " + ", ".join(stale_sources) + "."
            )
        if missing:
            qualifications.append(
                "A complete investigation requires " + ", ".join(missing) + "."
            )
        if not cause_claims:
            qualifications.append("No source supplied a supported cause claim.")

        score = _confidence_score(items, missing=bool(missing), conflicts=bool(conflicting_keys))
        confidence = (
            InvestigationConfidence.HIGH
            if score >= 0.75
            else InvestigationConfidence.MEDIUM
            if score >= 0.5
            else InvestigationConfidence.LOW
        )
        next_action = _claim_value(action_claims, forbidden)
        if not next_action and missing:
            next_action = f"Connect or restore {missing[0]} and rerun the investigation."
        elif not next_action and conflicting_keys:
            next_action = "Verify the conflicting claim against the most recent authoritative source."
        elif not next_action:
            next_action = "Validate the finding against the affected execution before remediation."

        summaries = _source_summaries(items, forbidden)
        safe_artifacts = _safe_artifacts(artifacts, forbidden)
        return InvestigationAnswer(
            direct_status=direct_status,
            likely_cause=likely_cause,
            confidence=confidence,
            confidence_score=score,
            qualifications=qualifications,
            next_action=next_action,
            source_summary=summaries,
            missing_required_sources=missing,
            artifacts=safe_artifacts,
            answered=bool(status_value and cause_value and not missing),
        )


def _conflict_value_qualifications(
    items: list[EvidenceSnapshot], forbidden: set[str]
) -> list[str]:
    """Describe bounded conflicting values without dumping evidence payloads."""
    groups: dict[str, list[EvidenceSnapshot]] = {}
    for item in items:
        if item.conflict_group and item.claim_value:
            groups.setdefault(item.conflict_group, []).append(item)
    details: list[str] = []
    for values in groups.values():
        observations = []
        for item in values[:4]:
            source = _source_label(item.source_class)
            value = _safe_text(item.claim_value or "unknown", forbidden)
            observation = f"{source} reported {value}"
            if observation not in observations:
                observations.append(observation)
        if len(observations) > 1:
            details.append("Observed values: " + "; ".join(observations) + ".")
    return details[:3]


def _claims(
    items: list[EvidenceSnapshot], accepted: set[str]
) -> list[EvidenceSnapshot]:
    return [
        item
        for item in items
        if item.claim_key and item.claim_value and _matches_claim(item.claim_key, accepted)
    ]


def _matches_claim(claim_key: str, accepted: set[str]) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", claim_key.strip().casefold()).strip("_")
    return any(normalized == key or normalized.endswith(f"_{key}") for key in accepted)


def _claim_value(items: list[EvidenceSnapshot], forbidden: set[str]) -> str | None:
    if not items:
        return None
    values = {_safe_text(item.claim_value or "", forbidden) for item in items}
    values.discard("")
    return sorted(values)[0] if len(values) == 1 else None


def _status_value(
    items: list[EvidenceSnapshot],
    forbidden: set[str],
    *,
    evidence: list[EvidenceSnapshot],
) -> str | None:
    """Select the most operationally significant status, not alphabetically."""
    candidates: list[tuple[EvidenceSnapshot, int, str]] = []
    for item in evidence:
        if item.source_class not in {
            EvidenceSourceClass.CURRENT_RUNTIME,
            EvidenceSourceClass.CONNECTOR,
        }:
            continue
        derived = _derive_status(f"{item.claim_value or ''} {item.title} {item.summary}")
        if derived is not None:
            candidates.append((item, derived[0], derived[1]))
    if not candidates:
        return _claim_value(items, forbidden)

    def score(candidate: tuple[EvidenceSnapshot, int, str]) -> tuple[int, float, float, float]:
        item, level, _ = candidate
        source_weight = 2 if item.source_class is EvidenceSourceClass.CURRENT_RUNTIME else 1
        return (level * source_weight, item.authority, item.freshness, item.confidence)

    _, _, status = max(candidates, key=score)
    return _safe_text(status, forbidden) or None


def _derive_status(value: str) -> tuple[int, str] | None:
    text = value.strip().casefold()
    rate = re.search(r"failure rate\s+([0-9]+(?:\.[0-9]+)?)", text)
    streak = re.search(r"failure streak\s+([0-9]+)", text)
    if rate or streak:
        if (rate and float(rate.group(1)) > 0) or (streak and int(streak.group(1)) > 0):
            return (5, "Degraded")
        return (1, "Healthy")
    statuses = (
        (6, "Failed", ("failed", "failure", "error", "timed out", "timeout")),
        (5, "Degraded", ("degraded",)),
        (4, "Warning", ("warning",)),
        (3, "Running", ("running",)),
        (2, "Pending", ("pending",)),
        (1, "Healthy", ("healthy",)),
        (1, "Success", ("success", "succeeded", "completed")),
    )
    return next(
        ((rank, label) for rank, label, tokens in statuses if any(token in text for token in tokens)),
        None,
    )


def classify_evidence_claims(items: list[EvidenceSnapshot]) -> list[EvidenceSnapshot]:
    """Add deterministic claim semantics before conflict grouping and synthesis."""
    classified: list[EvidenceSnapshot] = []
    for item in items:
        if item.claim_key and item.claim_value:
            classified.append(item)
            continue
        text = f"{item.title} {item.summary}".casefold()
        metadata_status = str(item.metadata.get("status") or "").strip()
        claim_key: str | None = None
        claim_value: str | None = None
        derived_status = (
            _derive_status(text)
            if item.source_class
            in {EvidenceSourceClass.CURRENT_RUNTIME, EvidenceSourceClass.CONNECTOR}
            else None
        )
        if derived_status is not None:
            claim_key = "execution_status"
            claim_value = derived_status[1]
        elif metadata_status:
            claim_key = "current_status"
            claim_value = metadata_status
        else:
            explicit = re.search(r"\b(?:sync|execution|job)?\s*status\s+(?:is|:)\s*([a-z_-]+)", text)
            if explicit:
                claim_key = "current_status"
                claim_value = explicit.group(1).replace("_", " ").title()
        classified.append(
            item.model_copy(update={"claim_key": claim_key, "claim_value": claim_value})
            if claim_key and claim_value
            else item
        )
    return classified


def _inferred_cause(items: list[EvidenceSnapshot], forbidden: set[str]) -> str | None:
    candidates = [
        item
        for item in items
        if re.search(
            r"\b(ora-\d+|connection[^.]{0,80}(?:timed? out|timeout)|database[^.]{0,80}(?:timed? out|timeout))\b",
            item.summary,
            re.IGNORECASE,
        )
    ]
    if not candidates:
        return None
    chosen = max(candidates, key=lambda item: (item.authority, item.freshness, item.confidence))
    return _compact_evidence_summary(chosen.summary, forbidden)


def _confidence_score(
    items: list[EvidenceSnapshot], *, missing: bool, conflicts: bool
) -> float:
    if not items:
        return 0.0
    quality = sum(
        (item.confidence + item.authority + item.freshness) / 3 for item in items
    ) / len(items)
    if missing:
        quality = min(quality, 0.45)
    if conflicts:
        quality = min(quality, 0.49)
    return round(max(0.0, min(1.0, quality)), 2)


def _source_label(source: EvidenceSourceClass) -> str:
    return {
        EvidenceSourceClass.GENERATED_DOC: "generated documentation",
        EvidenceSourceClass.INDEXED_DOC: "indexed documentation",
        EvidenceSourceClass.CURRENT_RUNTIME: "current runtime",
        EvidenceSourceClass.SOURCE_TOPOLOGY: "source topology",
        EvidenceSourceClass.CONNECTOR: "connector/log evidence",
    }[source]


def _compact_evidence_summary(value: str, forbidden: set[str]) -> str:
    stripped = value.strip()
    try:
        parsed = json.loads(stripped)
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, (dict, list)):
        return "Structured source details were omitted."
    text = _safe_text(value, forbidden)
    if len(text) <= 120:
        return text
    sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
    return sentence if len(sentence) <= 120 else text[:117] + "..."


def _source_summaries(
    items: list[EvidenceSnapshot], forbidden: set[str]
) -> list[InvestigationSourceSummary]:
    """Keep citations useful without replaying retrieved document chunks into chat."""
    summaries: list[InvestigationSourceSummary] = []
    seen: set[tuple[EvidenceSourceClass, str]] = set()
    per_source: dict[EvidenceSourceClass, int] = {}
    for item in items:
        compact = _compact_evidence_summary(item.summary, forbidden)
        identity = (item.source_class, compact.casefold())
        if identity in seen or per_source.get(item.source_class, 0) >= 3:
            continue
        seen.add(identity)
        per_source[item.source_class] = per_source.get(item.source_class, 0) + 1
        summaries.append(
            InvestigationSourceSummary(
                source=_source_label(item.source_class),
                title=_safe_text(item.title, forbidden)[:180],
                summary=compact,
                uri=_safe_uri(item.uri, forbidden),
            )
        )
        if len(summaries) >= 5:
            break
    return summaries


def _safe_text(value: str, forbidden: set[str] | None = None) -> str:
    text = _SECRET_RE.sub(lambda match: f"{match.group(1)}=[redacted]", str(value))
    for token in sorted(forbidden or (), key=len, reverse=True):
        text = text.replace(token, "[internal reference]")
    return _SPACE_RE.sub(" ", text).strip()[:1000]


def _safe_uri(value: str | None, forbidden: set[str] | None = None) -> str | None:
    if not value or not value.startswith(("/", "https://", "http://")):
        return None
    if any(token in value for token in forbidden or ()):
        return None
    return value[:1000]


def _safe_artifacts(
    artifacts: Iterable[InvestigationArtifact | dict[str, str | None]],
    forbidden: set[str],
) -> list[InvestigationArtifact]:
    result: list[InvestigationArtifact] = []
    for raw in artifacts:
        item = raw if isinstance(raw, InvestigationArtifact) else InvestigationArtifact(**raw)
        uri = _safe_uri(item.url, forbidden)
        if not uri:
            continue
        result.append(
            InvestigationArtifact(
                label=_safe_text(item.label, forbidden),
                url=uri,
                kind=_safe_text(item.kind, forbidden) if item.kind else None,
            )
        )
    return result[:20]
