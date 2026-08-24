"""Validate and materialize code/runtime evidence reconciliation artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


class ReconciliationError(ValueError):
    """A model finding is not safely linked to issued runtime evidence."""


_CLASSIFICATIONS = frozenset({"corroborates", "operational_context", "discrepancy", "inconclusive"})
_NEGATIVE_WORDS = re.compile(r"\b(no |none |not present|never |does not |did not |absence|absent)\b", re.I)


@dataclass(frozen=True)
class ReconciledFinding:
    evidence_id: str
    entity_refs: tuple[str, ...]
    classification: str
    statement: str
    explanation: str


def _mapping(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if not isinstance(value, dict):
        raise ReconciliationError("evidence envelope must be an object")
    return value


def issued_evidence_index(envelopes: Iterable[Any], *, run_id: int | str, project_id: str) -> dict[str, dict[str, Any]]:
    """Build the only accepted evidence-ID index from broker-issued envelopes."""

    index: dict[str, dict[str, Any]] = {}
    for raw in envelopes:
        envelope = _mapping(raw)
        evidence_id = envelope.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id.startswith("ev_"):
            continue
        if envelope.get("run_id") != run_id or envelope.get("project_id") != project_id:
            continue
        index[evidence_id] = envelope
    return index


def reconcile_findings(
    payload: Mapping[str, Any],
    *,
    issued: Mapping[str, Mapping[str, Any]],
    canonical_entity_refs: set[str],
) -> tuple[list[ReconciledFinding], list[str]]:
    """Fail closed on unknown evidence/entities and unsupported negative claims."""

    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise ReconciliationError("connector findings require a findings array")
    if len(findings) > 20:
        raise ReconciliationError("connector findings exceed the bounded limit")
    accepted: list[ReconciledFinding] = []
    for raw in findings:
        if not isinstance(raw, Mapping):
            raise ReconciliationError("connector finding must be an object")
        evidence_id = raw.get("evidence_id")
        classification = raw.get("classification")
        refs = raw.get("entity_refs")
        statement = raw.get("statement")
        explanation = raw.get("explanation")
        if not isinstance(evidence_id, str) or evidence_id not in issued:
            raise ReconciliationError("connector finding references unissued evidence")
        if classification not in _CLASSIFICATIONS:
            raise ReconciliationError("connector finding classification is invalid")
        if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) for ref in refs):
            raise ReconciliationError("connector finding requires canonical entity references")
        if not set(refs) <= canonical_entity_refs:
            raise ReconciliationError("connector finding references an unknown code entity")
        if not isinstance(statement, str) or not statement.strip() or len(statement) > 1000:
            raise ReconciliationError("connector finding statement is missing or oversized")
        if not isinstance(explanation, str) or not explanation.strip() or len(explanation) > 1500:
            raise ReconciliationError("connector finding explanation is missing or oversized")
        envelope = issued[evidence_id]
        limitations = envelope.get("limitations") or []
        no_items = not envelope.get("items") and not envelope.get("summary")
        if classification != "inconclusive" and (no_items or limitations):
            raise ReconciliationError("limited or empty evidence may only yield an inconclusive finding")
        if classification != "inconclusive" and _NEGATIVE_WORDS.search(statement):
            raise ReconciliationError("runtime evidence cannot support an unqualified negative conclusion")
        accepted.append(ReconciledFinding(
            evidence_id=evidence_id,
            entity_refs=tuple(refs),
            classification=classification,
            statement=statement.strip(),
            explanation=explanation.strip(),
        ))
    limitations = payload.get("limitations", [])
    if not isinstance(limitations, list) or not all(isinstance(item, str) for item in limitations):
        raise ReconciliationError("connector limitations must be string codes")
    return accepted, [item[:100] for item in limitations[:20]]


def materialize_reconciled_evidence(
    staging_dir: Path,
    *,
    findings: Iterable[ReconciledFinding],
    limitations: Iterable[str],
    run_id: int | str,
    project_id: str,
    artifact_dir: Path | None = None,
) -> Path:
    """Write accepted claims only; raw/excerpt provider data never enters this artifact."""

    root = artifact_dir or staging_dir / "_connector_evidence"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "accepted_findings.json"
    payload = {
        "run_id": run_id,
        "project_id": project_id,
        "findings": [asdict(finding) for finding in findings],
        "limitations": list(limitations),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def write_claim_linkage_report(
    staging_dir: Path,
    *,
    findings: Iterable[ReconciledFinding],
    receipts: Iterable[Mapping[str, Any]],
) -> Path:
    """Durable audit provenance with IDs/digests/counts, not vendor payloads."""

    receipt_by_evidence = {
        str(receipt.get("evidence_id")): {
            key: receipt.get(key)
            for key in ("invocation_id", "evidence_id", "evidence_digest", "capability_id", "source_id", "status", "latency_ms", "limitations")
        }
        for receipt in receipts if receipt.get("evidence_id")
    }
    payload = {
        "claims": [
            {"evidence_id": finding.evidence_id, "entity_refs": list(finding.entity_refs), "classification": finding.classification,
             "receipt": receipt_by_evidence.get(finding.evidence_id)}
            for finding in findings
        ]
    }
    root = staging_dir / "_audit"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "connector_claim_linkage.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
