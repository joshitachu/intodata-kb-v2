"""Tests for bounded structural living-document impact planning."""

from __future__ import annotations

from mcp_server.services.group_doc_dependency import GroupDocDependency
from mcp_server.services.living_docs.contracts import RagMutationKind
from mcp_server.services.living_docs.structural_impact import (
    StructuralChange,
    StructuralChangeKind,
    StructuralImpactPlanner,
)


def _dependency() -> GroupDocDependency:
    return GroupDocDependency(
        group_to_docs={
            "payments": {"04_RULES", "02_INVENTORY"},
            "orders": {"03_ORDERS", "02_INVENTORY"},
            "legacy_payments": {"04_RULES", "02_INVENTORY"},
        },
        doc_to_groups={
            "04_RULES": {"payments", "legacy_payments"},
            "03_ORDERS": {"orders"},
            "02_INVENTORY": {"payments", "orders", "legacy_payments"},
        },
        cross_cutting={"02_INVENTORY"},
    )


def _sections() -> dict[str, tuple[str, ...]]:
    return {"02_INVENTORY": ("Jobs",)}


def test_added_unit_plans_only_its_group_and_isolated_cross_cutting_section() -> None:
    plan = StructuralImpactPlanner.plan(
        [StructuralChange(StructuralChangeKind.UNIT_ADDED, group="payments", unit="settle_job")],
        _dependency(),
        synthesis_sections=_sections(),
    )

    assert not plan.held
    assert plan.changed_groups == ("payments",)
    assert plan.analysis_groups == ("payments",)
    assert plan.affected_docs == ("02_INVENTORY", "04_RULES")
    assert {target.key for target in plan.targets} == {
        "canonical_document:docs/02_INVENTORY.md#synthesis:Jobs",
        "canonical_document:docs/04_RULES.md#group:payments",
        "internal_analysis:analysis/payments.md#group:payments",
    }
    assert plan.rag_mutations == ()
    assert all("03_ORDERS" not in target.artifact_id for target in plan.targets)


def test_deleted_unit_emits_stale_identity_and_alias_deletions() -> None:
    plan = StructuralImpactPlanner.plan(
        [StructuralChange(StructuralChangeKind.UNIT_DELETED, group="payments", unit="retired_job")],
        _dependency(),
        synthesis_sections=_sections(),
    )

    assert not plan.held
    mutations = {(mutation.logical_id, mutation.mutation, mutation.alias_of) for mutation in plan.rag_mutations}
    assert mutations == {
        ("entities/payments/retired_job", RagMutationKind.DELETE, None),
        ("aliases/entities/payments/retired_job", RagMutationKind.DELETE, None),
    }


def test_renamed_unit_removes_old_identity_and_records_one_way_alias() -> None:
    plan = StructuralImpactPlanner.plan(
        [
            StructuralChange(
                StructuralChangeKind.UNIT_RENAMED,
                group="payments",
                unit="settlement_job",
                previous_unit="settle_job",
            )
        ],
        _dependency(),
        synthesis_sections=_sections(),
    )

    assert not plan.held
    mutations = {(mutation.logical_id, mutation.mutation, mutation.alias_of) for mutation in plan.rag_mutations}
    assert ("entities/payments/settle_job", RagMutationKind.DELETE, None) in mutations
    assert ("aliases/entities/payments/settle_job", RagMutationKind.ALIAS, "entities/payments/settlement_job") in mutations
    # The old entity itself is deleted; only a separate alias key remains.
    assert not any(
        logical_id == "entities/payments/settle_job" and mutation is RagMutationKind.ALIAS
        for logical_id, mutation, _ in mutations
    )


def test_renamed_group_uses_old_provenance_and_removes_old_analysis_identity() -> None:
    plan = StructuralImpactPlanner.plan(
        [
            StructuralChange(
                StructuralChangeKind.GROUP_RENAMED,
                group="payments",
                previous_group="legacy_payments",
            )
        ],
        _dependency(),
        synthesis_sections=_sections(),
    )

    assert not plan.held
    assert plan.changed_groups == ("legacy_payments", "payments")
    mutations = {(mutation.logical_id, mutation.mutation, mutation.alias_of) for mutation in plan.rag_mutations}
    assert ("analysis/legacy_payments.md", RagMutationKind.DELETE, None) in mutations
    assert ("aliases/analysis/legacy_payments.md", RagMutationKind.ALIAS, "analysis/payments.md") in mutations


def test_unmapped_cross_cutting_document_is_held_without_any_candidate_effect() -> None:
    plan = StructuralImpactPlanner.plan(
        [StructuralChange(StructuralChangeKind.UNIT_ADDED, group="payments", unit="settle_job")],
        _dependency(),
        synthesis_sections={},
    )

    assert plan.held
    assert plan.reason_code == "synthesis_mapping_required"
    assert plan.targets == ()
    assert plan.rag_mutations == ()


def test_missing_direct_provenance_is_held_not_widened_to_unrelated_docs() -> None:
    plan = StructuralImpactPlanner.plan(
        [StructuralChange(StructuralChangeKind.GROUP_ADDED, group="new_group")],
        _dependency(),
        synthesis_sections=_sections(),
    )

    assert plan.held
    assert plan.reason_code == "traceability_required"
    assert plan.affected_docs == ()
    assert plan.targets == ()
