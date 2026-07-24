from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import MagicMock


def test_076_adds_safe_agent_trace_fields(monkeypatch) -> None:
    path = Path(__file__).parents[2] / "alembic/versions/076_agent_trace_explorer.py"
    spec = spec_from_file_location("agent_trace_migration", path)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)
    migration.upgrade()
    assert migration.down_revision == "075_plan_step_timestamp_parity"
    columns = [call.args[1].name for call in op.add_column.call_args_list]
    assert "call_ref" in columns
    assert "argument_shape" in columns
    assert "validation_errors" in columns
    assert "reason_code" in columns
