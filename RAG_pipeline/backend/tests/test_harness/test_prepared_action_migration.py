from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import MagicMock


def test_077_adds_nullable_prepared_action_to_conversation_state(monkeypatch) -> None:
    path = (
        Path(__file__).parents[2]
        / "alembic/versions/077_prepared_conversation_actions.py"
    )
    spec = spec_from_file_location("prepared_action_migration", path)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)

    migration.upgrade()

    assert migration.revision == "077_prepared_actions"
    assert migration.down_revision == "076_agent_trace_explorer"
    call = op.add_column.call_args
    assert call.args[0] == "harness_conversation_states"
    assert call.args[1].name == "prepared_action"
    assert call.args[1].nullable is True
