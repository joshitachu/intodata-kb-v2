"""Unit tests for the pure ImpactRouter (Living-Document Reingestion, Slice 1)."""

from __future__ import annotations

from mcp_server.services.group_doc_dependency import GroupDocDependency
from mcp_server.services.impact_router import ImpactRouter, RegenPlan


def _dep(
    group_to_docs: dict[str, set[str]],
    cross_cutting: set[str],
) -> GroupDocDependency:
    doc_to_groups: dict[str, set[str]] = {}
    for group, docs in group_to_docs.items():
        for doc in docs:
            doc_to_groups.setdefault(doc, set()).add(group)
    return GroupDocDependency(group_to_docs, doc_to_groups, set(cross_cutting))


def test_manifest_missing_returns_flagged_empty_plan() -> None:
    plan = ImpactRouter.plan({"orders"}, None)
    assert plan.manifest_missing is True
    assert plan.group_scoped_docs == []
    assert plan.summary_docs == []
    assert plan.targeted_docs == []


def test_empty_dirty_set_returns_empty_plan() -> None:
    dep = _dep({"orders": {"02_ORDERS"}}, cross_cutting={"09_EXECUTIVE_SUMMARY"})
    plan = ImpactRouter.plan(set(), dep)
    assert plan.manifest_missing is False
    assert plan.targeted_docs == []


def test_single_dirty_group_selects_only_its_docs_plus_summary() -> None:
    dep = _dep(
        {"orders": {"02_ORDERS"}, "invoices": {"03_INVOICES"}},
        cross_cutting={"09_EXECUTIVE_SUMMARY", "01_ARCHITECTURE_MAP"},
    )
    plan = ImpactRouter.plan({"orders"}, dep)
    assert plan.group_scoped_docs == ["02_ORDERS"]
    assert plan.summary_docs == ["01_ARCHITECTURE_MAP", "09_EXECUTIVE_SUMMARY"]
    assert plan.targeted_docs == [
        "01_ARCHITECTURE_MAP",
        "02_ORDERS",
        "09_EXECUTIVE_SUMMARY",
    ]
    # The untouched group's doc is absent.
    assert "03_INVOICES" not in plan.targeted_docs


def test_two_dirty_groups_union_group_docs() -> None:
    dep = _dep(
        {"orders": {"02_ORDERS"}, "invoices": {"03_INVOICES"}},
        cross_cutting={"09_EXECUTIVE_SUMMARY"},
    )
    plan = ImpactRouter.plan({"orders", "invoices"}, dep)
    assert plan.group_scoped_docs == ["02_ORDERS", "03_INVOICES"]
    assert plan.summary_docs == ["09_EXECUTIVE_SUMMARY"]


def test_summary_docs_absent_when_dirty_set_empty() -> None:
    dep = _dep({"orders": {"02_ORDERS"}}, cross_cutting={"09_EXECUTIVE_SUMMARY"})
    assert ImpactRouter.plan(set(), dep).summary_docs == []


def test_cross_cutting_doc_not_double_counted_as_group_scoped() -> None:
    # A group whose pack also feeds a cross-cutting doc: that doc appears only in
    # summary_docs, never in group_scoped_docs.
    dep = _dep(
        {"orders": {"02_ORDERS", "09_EXECUTIVE_SUMMARY"}},
        cross_cutting={"09_EXECUTIVE_SUMMARY"},
    )
    plan = ImpactRouter.plan({"orders"}, dep)
    assert plan.group_scoped_docs == ["02_ORDERS"]
    assert plan.summary_docs == ["09_EXECUTIVE_SUMMARY"]


def test_groups_mapping_to_no_docs_and_no_summary_yield_empty_targeted() -> None:
    dep = _dep({"orders": set()}, cross_cutting=set())
    plan = ImpactRouter.plan({"orders"}, dep)
    assert plan.manifest_missing is False
    assert plan.targeted_docs == []


def test_falsy_group_names_are_ignored() -> None:
    dep = _dep({"orders": {"02_ORDERS"}}, cross_cutting=set())
    plan = ImpactRouter.plan({"", "orders"}, dep)
    assert plan.group_scoped_docs == ["02_ORDERS"]


def test_regen_plan_targeted_docs_is_sorted_union() -> None:
    plan = RegenPlan(group_scoped_docs=["b", "a"], summary_docs=["c", "a"])
    assert plan.targeted_docs == ["a", "b", "c"]
