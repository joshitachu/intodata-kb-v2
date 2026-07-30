"""Immutable, token-scoped evidence and relationship access for experiment tasks."""

from __future__ import annotations

from dataclasses import dataclass

from mcp_server.experiments.domain.evidence import EvidenceItem, Relationship, Subject
from mcp_server.experiments.domain.sources import RedactionState, SourceSnapshot
from mcp_server.experiments.mcp_tools.auth import TaskTokenClaims


class ToolScopeDenied(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class TaskContractView:
    task_id: str
    output_names: frozenset[str]
    allowed_tools: frozenset[str]


@dataclass(frozen=True, slots=True)
class ImmutableTaskEvidence:
    project_id: str
    experiment_id: str
    task_id: str
    snapshot: SourceSnapshot
    evidence_items: tuple[EvidenceItem, ...]
    subjects: tuple[Subject, ...]
    relationships: tuple[Relationship, ...]
    contract: TaskContractView

    def __post_init__(self) -> None:
        if not all((self.project_id, self.experiment_id, self.task_id)):
            raise ValueError("immutable evidence must bind project, experiment, and task")
        if self.contract.task_id != self.task_id:
            raise ValueError("contract task id must match immutable evidence task id")
        if self.snapshot.security_state is not RedactionState.APPROVED:
            raise ValueError("task evidence requires an approved source snapshot")
        _assert_unique((item.id for item in self.snapshot.items), "snapshot item ids")
        _assert_unique((content.item_id for content in self.snapshot.contents), "snapshot content ids")
        _assert_unique((item.id for item in self.evidence_items), "evidence ids")
        _assert_unique((subject.id for subject in self.subjects), "subject ids")
        snapshot_items = {item.id: item for item in self.snapshot.items}
        contents = {content.item_id: content for content in self.snapshot.contents}
        if set(snapshot_items) != set(contents):
            raise ValueError("snapshot item/content identities must match")
        subject_ids = {subject.id for subject in self.subjects}
        evidence_ids = {item.id for item in self.evidence_items}
        for evidence in self.evidence_items:
            snapshot_item = snapshot_items.get(evidence.source_snapshot_item_id)
            content = contents.get(evidence.source_snapshot_item_id)
            if snapshot_item is None or content is None:
                raise ValueError("evidence must reference immutable snapshot content")
            if (
                evidence.locator.source_snapshot_item_id != snapshot_item.id
                or evidence.locator.content_sha256 != content.extracted_sha256
                or snapshot_item.extracted_sha256 != content.extracted_sha256
            ):
                raise ValueError("evidence locator/content hash does not match source snapshot")
            if not set(evidence.subject_ids).issubset(subject_ids):
                raise ValueError("evidence references an unknown subject")
        for relationship in self.relationships:
            if not {relationship.source_subject_id, relationship.target_subject_id}.issubset(subject_ids):
                raise ValueError("relationship references an unknown subject")
            if not set(relationship.evidence_refs).issubset(evidence_ids):
                raise ValueError("relationship references unknown evidence")


def get_contract(context: ImmutableTaskEvidence, claims: TaskTokenClaims) -> dict[str, object]:
    _assert_task(context, claims)
    return {
        "allowed_tools": sorted(context.contract.allowed_tools),
        "output_names": sorted(context.contract.output_names),
        "task_id": context.task_id,
    }


def list_evidence(context: ImmutableTaskEvidence, claims: TaskTokenClaims) -> list[dict[str, object]]:
    _assert_task(context, claims)
    return [_evidence_summary(item, context, claims) for item in context.evidence_items if _allowed_evidence(item, context, claims)]


def search_evidence(
    context: ImmutableTaskEvidence,
    claims: TaskTokenClaims,
    *,
    query: str,
) -> list[dict[str, object]]:
    if not query.strip():
        raise ValueError("evidence search query must not be blank")
    needle = query.casefold()
    output: list[dict[str, object]] = []
    for item in context.evidence_items:
        if not _allowed_evidence(item, context, claims):
            continue
        content = _content_for(item, context)
        if needle in content.extracted_bytes.decode("utf-8", errors="replace").casefold():
            output.append(_evidence_summary(item, context, claims))
    return output


def read_evidence(
    context: ImmutableTaskEvidence,
    claims: TaskTokenClaims,
    *,
    evidence_id: str,
    max_bytes: int,
) -> dict[str, object]:
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    item = _evidence_by_id(context, evidence_id)
    if not _allowed_evidence(item, context, claims):
        raise ToolScopeDenied("evidence is outside task token scope")
    content = _content_for(item, context)
    bounded = content.extracted_bytes[:max_bytes]
    return {
        "content": bounded.decode("utf-8", errors="replace"),
        "content_sha256": content.extracted_sha256,
        "evidence_id": item.id,
        "locator": item.locator.locator,
        "media_type": content.media_type,
        "returned_bytes": len(bounded),
        "truncated": len(bounded) < len(content.extracted_bytes),
    }


def traverse_relationships(
    context: ImmutableTaskEvidence,
    claims: TaskTokenClaims,
    *,
    subject_id: str,
) -> list[dict[str, object]]:
    _assert_task(context, claims)
    if subject_id not in claims.subject_ids:
        raise ToolScopeDenied("start subject is outside task token scope")
    output: list[dict[str, object]] = []
    for relationship in context.relationships:
        if subject_id not in {relationship.source_subject_id, relationship.target_subject_id}:
            continue
        if not {relationship.source_subject_id, relationship.target_subject_id}.issubset(claims.subject_ids):
            continue
        output.append(
            {
                "evidence_refs": list(relationship.evidence_refs),
                "predicate": relationship.predicate,
                "source_subject_id": relationship.source_subject_id,
                "target_subject_id": relationship.target_subject_id,
            }
        )
    return output


def _assert_task(context: ImmutableTaskEvidence, claims: TaskTokenClaims) -> None:
    if (
        claims.project_id != context.project_id
        or claims.experiment_id != context.experiment_id
        or claims.task_id != context.task_id
    ):
        raise ToolScopeDenied("task token does not match immutable task evidence")


def _allowed_evidence(item: EvidenceItem, context: ImmutableTaskEvidence, claims: TaskTokenClaims) -> bool:
    _assert_task(context, claims)
    snapshot_item = next((candidate for candidate in context.snapshot.items if candidate.id == item.source_snapshot_item_id), None)
    if snapshot_item is None or snapshot_item.source_role not in claims.source_roles:
        return False
    return bool(item.subject_ids) and set(item.subject_ids).issubset(claims.subject_ids)


def _evidence_summary(item: EvidenceItem, context: ImmutableTaskEvidence, claims: TaskTokenClaims) -> dict[str, object]:
    if not _allowed_evidence(item, context, claims):
        raise ToolScopeDenied("evidence is outside task token scope")
    return {
        "evidence_id": item.id,
        "kind": item.kind,
        "locator": item.locator.locator,
        "source_snapshot_item_id": item.source_snapshot_item_id,
        "subject_ids": list(item.subject_ids),
    }


def _evidence_by_id(context: ImmutableTaskEvidence, evidence_id: str) -> EvidenceItem:
    item = next((candidate for candidate in context.evidence_items if candidate.id == evidence_id), None)
    if item is None:
        raise ToolScopeDenied("unknown evidence id")
    return item


def _content_for(item: EvidenceItem, context: ImmutableTaskEvidence):
    content = next((candidate for candidate in context.snapshot.contents if candidate.item_id == item.source_snapshot_item_id), None)
    if content is None:
        raise ToolScopeDenied("evidence content is absent from immutable snapshot")
    return content


def _assert_unique(values, label: str) -> None:
    collected = tuple(values)
    if any(not value for value in collected) or len(collected) != len(set(collected)):
        raise ValueError(f"{label} must be non-blank and unique")
