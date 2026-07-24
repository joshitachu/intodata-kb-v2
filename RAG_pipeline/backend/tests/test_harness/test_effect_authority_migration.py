from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import MagicMock


def test_072_adds_exact_approval_and_effect_authority(monkeypatch) -> None:
    path = Path(__file__).parents[2] / "alembic/versions/072_agent_effect_authority.py"
    spec = spec_from_file_location("effect_authority_migration", path)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)
    migration.upgrade()
    assert migration.down_revision == "071_agent_plans"
    assert {call.args[0] for call in op.create_table.call_args_list} == {
        "harness_approval_proposals",
        "harness_effect_operations",
    }
    assert {call.args[1].name for call in op.add_column.call_args_list} == {
        "effect_operation_id",
        "capability_receipt_ref",
        "workflow_run_id",
        "started_at",
        "finished_at",
    }
