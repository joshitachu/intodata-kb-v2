"""Deterministic incompatible-claim grouping."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import UTC, datetime

from .models import EvidenceSnapshot


def group_conflicts(evidence: list[EvidenceSnapshot]) -> list[EvidenceSnapshot]:
    """Annotate same-claim observations when their normalized values disagree."""
    claims: dict[str, list[EvidenceSnapshot]] = defaultdict(list)
    for item in evidence:
        if item.claim_key and item.claim_value is not None:
            claims[_claim_scope(item)].append(item)

    replacements: dict[tuple[str, str], EvidenceSnapshot] = {}
    for claim_key, items in claims.items():
        values = {_normalize(item.claim_value or "") for item in items}
        if len(values) < 2:
            continue
        identities = sorted(f"{item.source_class.value}:{item.source_id}" for item in items)
        digest = hashlib.sha256((claim_key + "|" + "|".join(identities)).encode()).hexdigest()[:20]
        group = f"conflict:{digest}"
        ids = sorted(item.id for item in items if item.id is not None)
        for item in items:
            refs = [item_id for item_id in ids if item_id != item.id]
            replacements[(item.source_class.value, item.source_id)] = item.model_copy(
                update={"conflict_group": group, "conflict_refs": refs}
            )
    return [replacements.get((item.source_class.value, item.source_id), item) for item in evidence]


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


def _claim_scope(item: EvidenceSnapshot) -> str:
    """Conflict only observations about the same entity/environment/window."""
    entity = _normalize(item.entity_ref or item.project_ref or "project")
    environment = _normalize(str(item.metadata.get("environment") or "default"))
    window = str(item.metadata.get("time_window") or "").strip()
    if not window and item.observed_at is not None:
        observed = item.observed_at.astimezone(UTC)
        window = datetime(observed.year, observed.month, observed.day, tzinfo=UTC).date().isoformat()
    return "|".join((_normalize(item.claim_key or ""), entity, environment, _normalize(window or "current")))
