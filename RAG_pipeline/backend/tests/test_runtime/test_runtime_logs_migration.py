"""Contract checks for the reversible runtime-store migrations.

These load a revision module directly and drive it against a mock `op`, rather
than running it against a database: the local Postgres holds real mirrored
customer data, and a migration test that touches it is a migration test nobody
will run.
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import MagicMock

_VERSIONS = Path(__file__).parents[2] / "alembic/versions"


def _load_migration(filename: str, module_name: str):
    path = _VERSIONS / filename
    spec = spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_module():
    return _load_migration("060_runtime_logs_storage.py", "runtime_logs_storage_migration")


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


# --- 099: captured provider evidence ----------------------------------------

_EVIDENCE_TABLES = ("runtime_executions", "runtime_job_runs", "runtime_plan_runs")
_EVIDENCE_COLUMNS = {"raw_state": 64, "misfire_reason": 64, "execution_type": 32}


def _evidence_migration():
    return _load_migration(
        "099_runtime_execution_evidence.py", "runtime_execution_evidence_migration"
    )


def _added(op) -> dict[tuple[str, str], object]:
    return {(call.args[0], call.args[1].name): call.args[1] for call in op.add_column.call_args_list}


class TestRuntimeExecutionEvidenceMigration:
    def test_upgrade_adds_every_evidence_column_to_every_run_table(self, monkeypatch):
        migration = _evidence_migration()
        op = MagicMock()
        monkeypatch.setattr(migration, "op", op)

        migration.upgrade()

        added = _added(op)
        assert set(added) == {
            (table, column) for table in _EVIDENCE_TABLES for column in _EVIDENCE_COLUMNS
        }
        for (table, name), column in added.items():
            assert column.nullable is True, f"{table}.{name} must be nullable for existing rows"
            assert column.type.length == _EVIDENCE_COLUMNS[name]

    def test_upgrade_leaves_the_state_column_alone(self, monkeypatch):
        """String(20) already fits `terminated` (10 chars); widening it would be
        an unnecessary rewrite of a 42k-row table."""
        migration = _evidence_migration()
        op = MagicMock()
        monkeypatch.setattr(migration, "op", op)

        migration.upgrade()

        assert op.alter_column.call_args_list == []
        assert op.execute.call_args_list == []

    def test_downgrade_drops_exactly_what_upgrade_added(self, monkeypatch):
        migration = _evidence_migration()
        up_op = MagicMock()
        monkeypatch.setattr(migration, "op", up_op)
        migration.upgrade()

        down_op = MagicMock()
        monkeypatch.setattr(migration, "op", down_op)
        migration.downgrade()

        dropped = {call.args for call in down_op.drop_column.call_args_list}
        assert dropped == set(_added(up_op))

    def test_downgrade_destroys_no_rows(self, monkeypatch):
        """Reversible means reversible: only the added columns go."""
        migration = _evidence_migration()
        op = MagicMock()
        monkeypatch.setattr(migration, "op", op)

        migration.downgrade()

        assert op.execute.call_args_list == []
        assert op.drop_table.call_args_list == []

    def test_orm_models_declare_the_same_columns(self):
        """The migration and the ORM are two statements of one schema.

        Drift between them is invisible until a query fails in production
        against a column SQLAlchemy believes exists, so assert them equal.
        """
        from src.db.models.runtime_data import (
            RuntimeExecutionRow,
            RuntimeJobRunRow,
            RuntimePlanRunRow,
        )

        for model in (RuntimeExecutionRow, RuntimeJobRunRow, RuntimePlanRunRow):
            for name, length in _EVIDENCE_COLUMNS.items():
                column = model.__table__.columns[name]
                assert column.nullable is True
                assert column.type.length == length
            # `state` must still fit every RuntimeExecutionState member.
            from src.runtime.models import RuntimeExecutionState

            state_length = model.__table__.columns["state"].type.length
            assert state_length >= max(len(state.value) for state in RuntimeExecutionState)

    def test_revision_chains_onto_the_current_head(self):
        migration = _evidence_migration()
        assert migration.revision == "099_runtime_execution_evidence"
        assert migration.down_revision == "098_runtime_route_observability"
