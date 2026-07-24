from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _migration_module():  # noqa: ANN202
    path = (
        Path(__file__).parents[2]
        / "alembic/versions/075_agent_plan_step_timestamp_parity.py"
    )
    spec = spec_from_file_location("agent_plan_step_timestamp_parity", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_agent_plan_step_timestamp_parity_migration_is_linear() -> None:
    migration = _migration_module()

    assert migration.revision == "075_plan_step_timestamp_parity"
    assert migration.down_revision == "074_pack_capability_permission"


def test_agent_plan_step_timestamp_parity_adds_and_drops_both_columns(
    monkeypatch,
) -> None:
    migration = _migration_module()
    added: list[tuple[str, str]] = []
    dropped: list[tuple[str, str]] = []
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda table, column: added.append((table, column.name)),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_column",
        lambda table, column: dropped.append((table, column)),
    )

    migration.upgrade()
    migration.downgrade()

    assert added == [
        ("harness_agent_plan_steps", "created_at"),
        ("harness_agent_plan_steps", "updated_at"),
    ]
    assert dropped == [
        ("harness_agent_plan_steps", "updated_at"),
        ("harness_agent_plan_steps", "created_at"),
    ]
