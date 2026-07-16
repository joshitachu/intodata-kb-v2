"""Contract checks for the reversible runtime-log storage migration."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import MagicMock


def _migration_module():
    path = Path(__file__).parents[2] / "alembic/versions/060_runtime_logs_storage.py"
    spec = spec_from_file_location("runtime_logs_storage_migration", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_archives_orphaned_legacy_project_rows_before_deleting_them(monkeypatch):
    migration = _migration_module()
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)
    monkeypatch.setattr(op, "batch_alter_table", MagicMock())
    # The batch context is unrelated to the consolidation contract; use a no-op.
    op.batch_alter_table.return_value.__enter__.return_value = MagicMock()

    migration.upgrade()

    commands = [call.args[0] for call in op.execute.call_args_list]
    first_archive = commands.index("INSERT INTO runtime_mirror_backup_executions SELECT * FROM runtime_executions WHERE project_id = 'IntodataStreams'")
    first_delete = commands.index("DELETE FROM runtime_incident_evidence WHERE project_id = 'IntodataStreams'")
    assert first_archive < first_delete
    assert "DELETE FROM runtime_executions WHERE project_id = 'IntodataStreams'" in commands


def test_migration_downgrade_restores_archived_rows(monkeypatch):
    migration = _migration_module()
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)
    monkeypatch.setattr(op, "batch_alter_table", MagicMock())
    op.batch_alter_table.return_value.__enter__.return_value = MagicMock()

    migration.downgrade()

    commands = [call.args[0] for call in op.execute.call_args_list]
    assert "INSERT INTO runtime_executions SELECT * FROM runtime_mirror_backup_executions" in commands
    assert "DROP TABLE runtime_mirror_backup_executions" in commands
