"""Focused tests for deterministic adaptive OpenCode logical scopes."""

from __future__ import annotations

import os

from mcp_server.providers.custom.engineering_facts.models import (
    DependencyFact,
    EngineeringFacts,
    EntityFact,
)
from mcp_server.providers.custom.models import FactsManifest, UnitFact
from mcp_server.providers.custom.partition_planner import Partition
from mcp_server.providers.opencode_scopes import (
    SCOPES_RELATIVE_PATH,
    LogicalScopePlan,
    ensure_scope_plan,
    plan_logical_scopes,
)


def _facts(
    specs: list[tuple[str, str, str]],
    *,
    project_id: str = "scope-test",
) -> FactsManifest:
    units = [
        UnitFact(
            name=name,
            display_name=name,
            group=group,
            source_root=source_root,
            path=f"{source_root}/{name}.xml",
        )
        for name, group, source_root in specs
    ]
    groups: dict[str, int] = {}
    for unit in units:
        groups[unit.group] = groups.get(unit.group, 0) + 1
    return FactsManifest(
        project_id=project_id,
        platform="talend",
        canonical_unit_label="jobs",
        unit_count=len(units),
        groups=groups,
        units=units,
    )


def _partition(name: str, facts: FactsManifest, source_root: str) -> Partition:
    units = [unit for unit in facts.units if unit.source_root == source_root]
    groups = tuple(sorted({unit.group for unit in units}))
    return Partition(
        name=name,
        source_roots=(source_root,),
        unit_names=tuple(sorted(unit.name for unit in units)),
        unit_count=len(units),
        groups=groups,
        group_count=len(groups),
        call_budget=1,
        budget_fits=True,
    )


def test_tiny_project_yields_one_scope() -> None:
    facts = _facts(
        [
            ("job_a", "alpha", "root"),
            ("job_b", "beta", "root"),
            ("job_c", "beta", "root"),
        ]
    )

    plan = plan_logical_scopes(
        facts,
        max_units_per_scope=40,
        max_groups_per_scope=6,
    )

    assert plan.scope_count == 1
    assert plan.scopes[0].groups == ("alpha", "beta")
    assert plan.scopes[0].unit_names == ("job_a", "job_b", "job_c")


def test_large_project_adds_scopes_and_respects_both_ceilings() -> None:
    facts = _facts(
        [
            (f"job_{group}_{index}", group, "root")
            for group in ("a", "b", "c", "d")
            for index in range(3)
        ]
    )

    plan = plan_logical_scopes(
        facts,
        max_units_per_scope=4,
        max_groups_per_scope=2,
    )

    assert plan.scope_count > 1
    assert all(scope.unit_count <= 4 for scope in plan.scopes)
    assert all(scope.group_count <= 2 for scope in plan.scopes)
    assert sorted(name for scope in plan.scopes for name in scope.unit_names) == sorted(
        unit.name for unit in facts.units
    )


def test_composite_partitions_never_mix_and_scale_independently() -> None:
    facts = _facts(
        [
            ("a_1", "alpha", "root-a"),
            ("a_2", "alpha", "root-a"),
            ("a_3", "beta", "root-a"),
            ("b_1", "gamma", "root-b"),
            ("b_2", "gamma", "root-b"),
            ("b_3", "delta", "root-b"),
        ]
    )
    partitions = (
        _partition("part-a", facts, "root-a"),
        _partition("part-b", facts, "root-b"),
    )

    plan = plan_logical_scopes(
        facts,
        partitions=partitions,
        max_units_per_scope=2,
        max_groups_per_scope=2,
    )

    assert plan.partition_count == 2
    assert plan.scope_count == 4
    for scope in plan.scopes:
        expected_root = "root-a" if scope.partition_name == "part-a" else "root-b"
        assert scope.source_roots == (expected_root,)
        assert all(
            unit.source_root == expected_root
            for unit in facts.units
            if unit.name in scope.unit_names
        )


def test_dependency_linked_groups_share_scope_when_capacity_allows() -> None:
    facts = _facts(
        [
            ("job_a", "alpha", "root"),
            ("job_b", "beta", "root"),
            ("job_c", "charlie", "root"),
            ("job_d", "delta", "root"),
        ]
    )
    engineering = EngineeringFacts(
        project_id=facts.project_id,
        platform=facts.platform,
        dependencies=[
            DependencyFact(
                id="dep-a-b",
                kind="dependency",
                name="job_a calls job_b",
                source="job_a",
                target="job_b",
            )
        ],
    )

    plan = plan_logical_scopes(
        facts,
        engineering_facts=engineering,
        max_units_per_scope=2,
        max_groups_per_scope=2,
    )

    linked_scope = next(scope for scope in plan.scopes if "job_a" in scope.unit_names)
    assert linked_scope.unit_names == ("job_a", "job_b")
    assert linked_scope.internal_dependency_edges == 1
    assert plan.dependency_edges_considered == 1


def test_dependency_entity_aliases_are_resolved_to_units() -> None:
    facts = _facts(
        [
            ("job_a", "alpha", "root"),
            ("job_b", "beta", "root"),
            ("job_c", "charlie", "root"),
        ]
    )
    engineering = EngineeringFacts(
        project_id=facts.project_id,
        platform=facts.platform,
        entities=[
            EntityFact(
                id="entity-a",
                kind="job",
                name="A entity",
                unit_name="job_a",
            ),
            EntityFact(
                id="entity-b",
                kind="job",
                name="B entity",
                unit_name="job_b",
            ),
        ],
        dependencies=[
            DependencyFact(
                id="dep-entities",
                kind="dependency",
                name="A calls B",
                source="entity-a",
                target="entity-b",
            )
        ],
    )

    plan = plan_logical_scopes(
        facts,
        engineering_facts=engineering,
        max_units_per_scope=2,
        max_groups_per_scope=2,
    )

    assert plan.dependency_edges_considered == 1
    assert any(scope.unit_names == ("job_a", "job_b") for scope in plan.scopes)


def test_ensure_uses_staged_engineering_dependencies_when_available(tmp_path) -> None:
    facts = _facts(
        [
            ("job_a", "alpha", "root"),
            ("job_b", "beta", "root"),
            ("job_c", "charlie", "root"),
        ]
    )
    engineering = EngineeringFacts(
        project_id=facts.project_id,
        platform=facts.platform,
        dependencies=[
            DependencyFact(
                id="dep-a-b",
                kind="dependency",
                name="a-b",
                source="job_a",
                target="job_b",
            )
        ],
    )
    (tmp_path / "engineering_facts.json").write_text(
        engineering.model_dump_json(indent=2),
        encoding="utf-8",
    )

    plan = ensure_scope_plan(
        tmp_path,
        facts,
        max_units_per_scope=2,
        max_groups_per_scope=2,
    )

    assert plan.dependency_edges_considered == 1
    assert any(scope.unit_names == ("job_a", "job_b") for scope in plan.scopes)


def test_oversized_group_splits_without_exceeding_unit_ceiling() -> None:
    facts = _facts([(f"job_{index}", "large", "root") for index in range(7)])

    plan = plan_logical_scopes(
        facts,
        max_units_per_scope=3,
        max_groups_per_scope=1,
    )

    assert plan.scope_count == 3
    assert all(scope.groups == ("large",) for scope in plan.scopes)
    assert all(scope.unit_count <= 3 for scope in plan.scopes)


def test_planning_is_deterministic_across_input_order() -> None:
    specs = [
        ("job_a", "alpha", "root"),
        ("job_b", "beta", "root"),
        ("job_c", "charlie", "root"),
    ]
    first = _facts(specs)
    second = _facts(list(reversed(specs)))
    dependencies = [
        DependencyFact(
            id="dep-a-b",
            kind="dependency",
            name="a-b",
            source="job_a",
            target="job_b",
        ),
        DependencyFact(
            id="dep-b-c",
            kind="dependency",
            name="b-c",
            source="job_b",
            target="job_c",
        ),
    ]
    engineering_a = EngineeringFacts(
        project_id=first.project_id,
        platform=first.platform,
        dependencies=dependencies,
    )
    engineering_b = EngineeringFacts(
        project_id=second.project_id,
        platform=second.platform,
        dependencies=list(reversed(dependencies)),
    )

    first_plan = plan_logical_scopes(
        first,
        engineering_facts=engineering_a,
        max_units_per_scope=2,
        max_groups_per_scope=2,
    )
    second_plan = plan_logical_scopes(
        second,
        engineering_facts=engineering_b,
        max_units_per_scope=2,
        max_groups_per_scope=2,
    )

    assert first_plan == second_plan
    assert first_plan.input_digest == second_plan.input_digest


def test_identical_resume_reuses_existing_scope_file_byte_for_byte(tmp_path) -> None:
    facts = _facts(
        [
            ("job_a", "alpha", "root"),
            ("job_b", "beta", "root"),
        ]
    )
    first = ensure_scope_plan(
        tmp_path,
        facts,
        max_units_per_scope=4,
        max_groups_per_scope=4,
    )
    path = tmp_path / SCOPES_RELATIVE_PATH
    original = path.read_bytes()
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))

    resumed = ensure_scope_plan(
        tmp_path,
        facts,
        max_units_per_scope=4,
        max_groups_per_scope=4,
    )

    assert resumed == first
    assert path.read_bytes() == original
    assert path.stat().st_mtime_ns == 1_000_000_000
    assert LogicalScopePlan.load_from_staging(tmp_path) == first


def test_changed_limits_replace_stale_resume_plan(tmp_path) -> None:
    facts = _facts(
        [
            ("job_a", "alpha", "root"),
            ("job_b", "beta", "root"),
            ("job_c", "charlie", "root"),
        ]
    )
    original = ensure_scope_plan(
        tmp_path,
        facts,
        max_units_per_scope=4,
        max_groups_per_scope=4,
    )

    replacement = ensure_scope_plan(
        tmp_path,
        facts,
        max_units_per_scope=1,
        max_groups_per_scope=1,
    )

    assert replacement.input_digest != original.input_digest
    assert replacement.scope_count == 3
    assert LogicalScopePlan.load_from_staging(tmp_path) == replacement


def test_corrupt_resume_plan_is_rebuilt(tmp_path) -> None:
    facts = _facts([("job_a", "alpha", "root")])
    path = tmp_path / SCOPES_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text("not-json", encoding="utf-8")

    plan = ensure_scope_plan(tmp_path, facts)

    assert plan.scope_count == 1
    assert LogicalScopePlan.load_from_staging(tmp_path) == plan
