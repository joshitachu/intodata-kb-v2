from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import MagicMock


def test_071_creates_non_executable_plan_tables(monkeypatch) -> None:
    path = Path(__file__).parents[2] / "alembic/versions/071_agent_plans.py"
    spec = spec_from_file_location("agent_plans_migration", path)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)
    migration.upgrade()
    assert migration.down_revision == "070_agent_timestamp_parity"
    assert {call.args[0] for call in op.create_table.call_args_list} == {"harness_agent_plans", "harness_agent_plan_steps", "harness_agent_plan_events"}
