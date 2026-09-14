"""Lossless, bounded semantic scopes over the atomic landscape ledger.

Exact source records remain deterministic artifacts. Model sessions receive
coherent unit concerns and attest which records they reviewed; that attestation
does not certify that their interpretation is correct.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from mcp_server.providers.custom.knowledge_contracts import (
    LANDSCAPE_ROOT,
    KnowledgeManifest,
    KnowledgePlan,
    KnowledgeRequirement,
    KnowledgeTask,
    digest,
    write_json,
)

PLANNER_VERSION = "semantic-scopes-v2"

_QUESTIONS = {
    "identity": (
        "Explain what this unit is in the exported design. Do not infer business "
        "purpose, ownership, inputs, or outputs from its name alone."
    ),
    "component_behavior": (
        "Explain the supplied components together: schemas, endpoints, embedded "
        "code, conditions, side effects, and failure handling."
    ),
    "execution_flow": (
        "Reconstruct execution order, row flows, branches, triggers, and error "
        "propagation from every supplied connection."
    ),
    "transformation": (
        "Explain every supplied mapping rule, including derivations, joins, "
        "lookups, filters, null handling, defaults, and rejects."
    ),
    "configuration": (
        "Explain supplied configuration, environment differences, overrides, "
        "and effects. Do not infer deployed values from exported defaults."
    ),
    "dependencies": (
        "Explain every supplied call and dependency, passed context, and caller "
        "effects. Keep unresolved targets explicit."
    ),
    "routine_behavior": (
        "Explain complete referenced routine behavior and caller-specific "
        "effects, including conditions, transformations, and failures."
    ),
    "runtime_behavior": (
        "Explain supplied runtime observations within their environment and time "
        "boundary. Missing observations do not prove non-execution."
    ),
    "documented_context": (
        "Explain supplied documented intent and keep contradictions with the "
        "implemented design visible."
    ),
}


def _payload_bytes(evidence: dict[str, Any], evidence_ids: Iterable[str]) -> int:
    records = [evidence[eid].model_dump(mode="json") for eid in evidence_ids]
    return len(
        (json.dumps({"evidence": records}, sort_keys=True, indent=2, ensure_ascii=True) + "\n")
        .encode("utf-8")
    )


def _resolved_target(value: Any) -> Iterable[str]:
    """Yield scanner-declared resolved targets without name-based matching."""
    if isinstance(value, dict):
        target = value.get("resolved_job_id")
        resolution = str(value.get("resolution") or "resolved")
        if isinstance(target, str) and target and resolution not in {"unresolved", "ambiguous"}:
            yield target
        for nested in value.values():
            yield from _resolved_target(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _resolved_target(nested)


def _unit_order(
    requirements: list[KnowledgeRequirement], evidence: dict[str, Any]
) -> tuple[list[str], dict[str, list[str]]]:
    """Order same-group connected components; never invent cross-group edges."""
    group_by_unit = {requirement.unit_id: requirement.group for requirement in requirements}
    adjacency: dict[str, set[str]] = {unit_id: set() for unit_id in group_by_unit}
    for requirement in requirements:
        if requirement.concern != "dependencies":
            continue
        for evidence_id in requirement.evidence_ids:
            try:
                payload = json.loads(evidence[evidence_id].text)
            except (KeyError, TypeError, json.JSONDecodeError):
                continue
            for target in _resolved_target(payload):
                if target in group_by_unit and group_by_unit[target] == requirement.group:
                    adjacency[requirement.unit_id].add(target)
                    adjacency[target].add(requirement.unit_id)

    ordered: list[str] = []
    connected: dict[str, list[str]] = {}
    seen: set[str] = set()
    for group in sorted(set(group_by_unit.values())):
        for start in sorted(unit for unit, owner in group_by_unit.items() if owner == group):
            if start in seen:
                continue
            component: list[str] = []
            pending = [start]
            seen.add(start)
            while pending:
                current = pending.pop(0)
                component.append(current)
                for target in sorted(adjacency[current]):
                    if target not in seen:
                        seen.add(target)
                        pending.append(target)
            ordered.extend(component)
            for unit_id in component:
                connected[unit_id] = [peer for peer in component if peer != unit_id]
    return ordered, connected


def prepare_semantic_scopes(
    *,
    staging_dir: Path,
    atomic_plan: KnowledgePlan,
    max_scope_evidence_bytes: int = 400_000,
    evidence_page_bytes: int = 24_000,
    max_units_per_scope: int = 6,
) -> KnowledgePlan:
    """Create coherent scopes while retaining and assigning every evidence ID."""
    if max_scope_evidence_bytes < 1024 or evidence_page_bytes < 1024:
        raise ValueError("scope and page budgets must be at least 1024 bytes")
    if evidence_page_bytes > max_scope_evidence_bytes:
        raise ValueError("evidence page budget cannot exceed scope budget")
    if max_units_per_scope < 1:
        raise ValueError("unit limit must be positive")

    source_manifest = atomic_plan.manifest
    evidence = source_manifest.evidence
    referenced = {
        evidence_id
        for requirement in source_manifest.requirements
        for evidence_id in requirement.evidence_ids
    }
    unknown = referenced - set(evidence)
    orphaned = set(evidence) - referenced
    if unknown or orphaned:
        raise ValueError(
            "atomic evidence ledger is inconsistent: "
            f"{len(unknown)} missing and {len(orphaned)} orphaned evidence IDs"
        )
    for evidence_id in evidence:
        if _payload_bytes(evidence, [evidence_id]) > max_scope_evidence_bytes:
            raise ValueError(
                f"evidence {evidence_id} exceeds the semantic scope budget; "
                "re-fragment the atomic evidence"
            )

    retained_atomic: list[KnowledgeRequirement] = []
    by_concern: dict[tuple[str, str, str], list[KnowledgeRequirement]] = defaultdict(list)
    for requirement in source_manifest.requirements:
        replace_explanation = bool(requirement.evidence_ids)
        retained_atomic.append(
            requirement.model_copy(
                update={
                    "explanation_required": (
                        False if replace_explanation else requirement.explanation_required
                    )
                }
            )
        )
        if replace_explanation:
            by_concern[
                (requirement.group, requirement.unit_id, requirement.concern)
            ].append(requirement)

    semantic_by_unit: dict[str, list[KnowledgeRequirement]] = defaultdict(list)
    semantic_receipt: dict[str, dict[str, Any]] = {}
    for (group, unit_id, concern), constituents in sorted(by_concern.items()):
        all_ids = list(
            dict.fromkeys(
                evidence_id
                for requirement in constituents
                for evidence_id in requirement.evidence_ids
            )
        )
        chunks: list[list[str]] = []
        chunk: list[str] = []
        for evidence_id in all_ids:
            candidate = [*chunk, evidence_id]
            if chunk and _payload_bytes(evidence, candidate) > max_scope_evidence_bytes:
                chunks.append(chunk)
                chunk = []
            chunk.append(evidence_id)
        if chunk:
            chunks.append(chunk)

        for ordinal, evidence_ids in enumerate(chunks, start=1):
            selected = set(evidence_ids)
            related = [
                requirement
                for requirement in constituents
                if selected.intersection(requirement.evidence_ids)
            ]
            requirement_id = "sem-" + digest(
                {
                    "planner": PLANNER_VERSION,
                    "snapshot": atomic_plan.snapshot_id,
                    "unit": unit_id,
                    "concern": concern,
                    "evidence_ids": evidence_ids,
                }
            )[:24]
            complete = all(
                requirement.extraction_status == "extracted" for requirement in related
            ) and all(evidence[evidence_id].complete for evidence_id in evidence_ids)
            question = _QUESTIONS.get(
                concern, f"Explain all supplied {concern} evidence and its effects."
            )
            semantic_requirement = KnowledgeRequirement(
                requirement_id=requirement_id,
                unit_id=unit_id,
                group=group,
                concern=concern,
                description=(
                    f"{question} Concern segment {ordinal}/{len(chunks)}. Review every "
                    "assigned evidence record; unsupported interpretations remain unknown."
                ),
                evidence_ids=evidence_ids,
                extraction_status="extracted" if complete else "partial",
                gap_stage="" if complete else "extraction",
                gap_reason=(
                    "" if complete else "One or more source fragments are partial."
                ),
                mandatory=any(requirement.mandatory for requirement in related),
                exact_preservation=False,
                explanation_required=True,
                output_destinations=list(
                    dict.fromkeys(
                        destination
                        for requirement in related
                        for destination in requirement.output_destinations
                    )
                ),
            )
            semantic_by_unit[unit_id].append(semantic_requirement)
            semantic_receipt[requirement_id] = {
                "atomic_requirement_ids": [
                    requirement.requirement_id for requirement in related
                ],
                "required_review_evidence_ids": evidence_ids,
                "semantic_review": "not_performed",
            }

    unit_order, related_units = _unit_order(source_manifest.requirements, evidence)
    batches: list[list[KnowledgeRequirement]] = []
    current: list[KnowledgeRequirement] = []
    for unit_id in unit_order:
        for requirement in semantic_by_unit.get(unit_id, []):
            candidate = [*current, requirement]
            candidate_units = {item.unit_id for item in candidate}
            candidate_evidence = list(
                dict.fromkeys(
                    evidence_id
                    for item in candidate
                    for evidence_id in item.evidence_ids
                )
            )
            crosses_group = current and requirement.group != current[0].group
            if current and (
                crosses_group
                or len(candidate_units) > max_units_per_scope
                or _payload_bytes(evidence, candidate_evidence)
                > max_scope_evidence_bytes
            ):
                batches.append(current)
                current = []
            current.append(requirement)
    if current:
        batches.append(current)

    root = LANDSCAPE_ROOT / "semantic_tasks"
    tasks: list[KnowledgeTask] = []
    for batch in batches:
        evidence_ids = list(
            dict.fromkeys(
                evidence_id
                for requirement in batch
                for evidence_id in requirement.evidence_ids
            )
        )
        task_id = "scope-" + digest(
            {
                "planner": PLANNER_VERSION,
                "snapshot": atomic_plan.snapshot_id,
                "requirement_ids": [
                    requirement.requirement_id for requirement in batch
                ],
                "page_bytes": evidence_page_bytes,
            }
        )[:24]
        task = KnowledgeTask(
            task_id=task_id,
            snapshot_id=atomic_plan.snapshot_id,
            group=batch[0].group,
            unit_ids=list(dict.fromkeys(requirement.unit_id for requirement in batch)),
            requirement_ids=[
                requirement.requirement_id for requirement in batch
            ],
            evidence_ids=evidence_ids,
            input_artifact=(root / f"{task_id}.json").as_posix(),
            output_artifact=(
                LANDSCAPE_ROOT / "claims" / f"{task_id}.json"
            ).as_posix(),
        )

        page_ids: list[list[str]] = []
        page: list[str] = []
        for evidence_id in evidence_ids:
            candidate = [*page, evidence_id]
            if page and _payload_bytes(evidence, candidate) > evidence_page_bytes:
                page_ids.append(page)
                page = []
            page.append(evidence_id)
        if page:
            page_ids.append(page)
        pages: list[dict[str, Any]] = []
        for page_number, ids in enumerate(page_ids, start=1):
            path = root / task_id / f"evidence-{page_number:04d}.json"
            write_json(
                staging_dir / path,
                {"evidence": [evidence[evidence_id].model_dump(mode="json") for evidence_id in ids]},
            )
            actual_bytes = (staging_dir / path).stat().st_size
            pages.append(
                {
                    "path": path.as_posix(),
                    "evidence_ids": ids,
                    "evidence_bytes": actual_bytes,
                    "over_page_target": actual_bytes > evidence_page_bytes,
                }
            )
        page_paths = [page_info["path"] for page_info in pages]
        write_json(
            staging_dir / task.input_artifact,
            {
                "planner_version": PLANNER_VERSION,
                "task": task.model_dump(mode="json"),
                "requirements": [
                    requirement.model_dump(mode="json") for requirement in batch
                ],
                "evidence_artifacts": page_paths,
                "evidence_pages": pages,
                "required_review_evidence_ids": evidence_ids,
                "related_units": {
                    unit_id: related_units.get(unit_id, [])
                    for unit_id in task.unit_ids
                },
                "grouping_basis": (
                    "scanner-resolved same-group dependencies, then canonical "
                    "unit order; grouping does not transfer ownership"
                ),
                "instructions": (
                    "Read every explicitly listed evidence artifact and report all "
                    "reviewed evidence IDs. Cite decisive source substrings. A review "
                    "attestation does not certify semantic correctness. Do not infer "
                    "business purpose from identifiers."
                ),
            },
        )
        tasks.append(task)

    assigned = {
        evidence_id for task in tasks for evidence_id in task.evidence_ids
    }
    assignment_count = sum(len(task.evidence_ids) for task in tasks)
    if assigned != set(evidence):
        raise ValueError(
            "semantic scope planning did not assign the complete evidence ledger"
        )

    semantic_requirements = [
        requirement
        for unit_id in unit_order
        for requirement in semantic_by_unit.get(unit_id, [])
    ]
    manifest = KnowledgeManifest(
        snapshot_id=atomic_plan.snapshot_id,
        project_id=source_manifest.project_id,
        requirements=[*retained_atomic, *semantic_requirements],
        evidence=evidence,
    )
    plan = KnowledgePlan(
        snapshot_id=atomic_plan.snapshot_id, tasks=tasks, manifest=manifest
    )
    write_json(staging_dir / LANDSCAPE_ROOT / "requirements.json", manifest)
    write_json(staging_dir / LANDSCAPE_ROOT / "semantic_requirements.json", manifest)
    write_json(staging_dir / LANDSCAPE_ROOT / "semantic_plan.json", plan)
    write_json(
        staging_dir / LANDSCAPE_ROOT / "semantic_scope_receipt.json",
        {
            "planner_version": PLANNER_VERSION,
            "snapshot_id": plan.snapshot_id,
            "atomic_requirement_count": len(retained_atomic),
            "semantic_requirement_count": len(semantic_requirements),
            "scope_count": len(tasks),
            "retained_evidence_count": len(evidence),
            "assigned_evidence_count": len(assigned),
            "evidence_assignment_count": assignment_count,
            "repeated_evidence_assignments": assignment_count - len(assigned),
            "requirements": semantic_receipt,
            "semantic_review": "not_performed",
        },
    )
    write_json(
        staging_dir / LANDSCAPE_ROOT / "semantic_unit_index.json",
        {
            "units": {
                unit_id: {
                    "group": next(
                        requirement.group
                        for requirement in source_manifest.requirements
                        if requirement.unit_id == unit_id
                    ),
                    "related_units": related_units.get(unit_id, []),
                    "semantic_requirement_ids": [
                        requirement.requirement_id
                        for requirement in semantic_by_unit.get(unit_id, [])
                    ],
                    "task_ids": [
                        task.task_id for task in tasks if unit_id in task.unit_ids
                    ],
                }
                for unit_id in unit_order
            }
        },
    )
    return plan
