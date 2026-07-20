from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import MagicMock


def _load_migration():
    path = (
        Path(__file__).parents[2]
        / "alembic/versions/070_agent_timestamp_schema_parity.py"
    )
    spec = spec_from_file_location("agent_timestamp_schema_migration", path)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_070_adds_all_inherited_agent_timestamps(monkeypatch) -> None:
    migration = _load_migration()
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)

    migration.upgrade()

    assert migration.revision == "070_agent_timestamp_parity"
    assert migration.down_revision == "069_retire_legacy_chat_paths"
    added = [(call.args[0], call.args[1]) for call in op.add_column.call_args_list]
    assert [(table, column.name) for table, column in added] == [
        ("harness_conversation_turn_claims", "created_at"),
        ("harness_conversation_turn_claims", "updated_at"),
        ("harness_conversation_state_transitions", "updated_at"),
        ("harness_workflow_child_observations", "created_at"),
        ("harness_workflow_child_observations", "updated_at"),
    ]
    for _, column in added:
        assert column.nullable is False
        assert column.server_default is not None
        assert column.type.timezone is True


def test_070_downgrade_removes_only_reconciled_columns(monkeypatch) -> None:
    migration = _load_migration()
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)

    migration.downgrade()

    assert [call.args for call in op.drop_column.call_args_list] == [
        ("harness_workflow_child_observations", "updated_at"),
        ("harness_workflow_child_observations", "created_at"),
        ("harness_conversation_state_transitions", "updated_at"),
        ("harness_conversation_turn_claims", "updated_at"),
        ("harness_conversation_turn_claims", "created_at"),
    ]
