"""Contract checks for Pack Governance V1 migration 061."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _migration_module():
    path = Path(__file__).parents[2] / "alembic/versions/061_pack_governance.py"
    spec = spec_from_file_location("pack_governance_migration", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_quarantines_legacy_rows_and_creates_governance_storage(monkeypatch) -> None:
    migration = _migration_module()
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)
    batch = MagicMock()
    op.batch_alter_table.return_value.__enter__.return_value = batch

    migration.upgrade()

    added = [call.args[0].name for call in batch.add_column.call_args_list]
    assert "governance_state" in added
    assert {"stack_selection", "compiled_stack_snapshot", "compiled_stack_hash", "compiled_presentation"} <= set(added)
    created = {call.args[0] for call in op.create_table.call_args_list}
    assert "harness_organization_governance_policies" in created
    assert "harness_style_pack_versions" in created
    assert op.bulk_insert.call_args.args[1][0]["organization_id"] == "default"
    assert op.bulk_insert.call_args.args[1][0]["policy"] == migration._DEFAULT_POLICY


def test_downgrade_removes_governance_storage_without_rewriting_legacy_content(monkeypatch) -> None:
    migration = _migration_module()
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)
    batch = MagicMock()
    op.batch_alter_table.return_value.__enter__.return_value = batch

    migration.downgrade()

    dropped_tables = {call.args[0] for call in op.drop_table.call_args_list}
    assert dropped_tables == {"harness_style_pack_versions", "harness_organization_governance_policies"}
    dropped_columns = [call.args[0] for call in batch.drop_column.call_args_list]
    assert "governance_state" in dropped_columns
    assert "compiled_stack_snapshot" in dropped_columns


def test_retirement_upgrade_drops_the_governed_pack_stack(monkeypatch) -> None:
    path = Path(__file__).parents[2] / "alembic/versions/090_retire_governed_pack_stack.py"
    spec = spec_from_file_location("retire_governed_pack_stack", path)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)

    migration.upgrade()

    statements = [str(call.args[0]) for call in op.execute.call_args_list]
    assert statements == [
        'DROP TABLE IF EXISTS "harness_project_pack_activations" CASCADE',
        'DROP TABLE IF EXISTS "harness_pack_activation_snapshots" CASCADE',
        'DROP TABLE IF EXISTS "harness_style_pack_versions" CASCADE',
        'DROP TABLE IF EXISTS "harness_organization_governance_policies" CASCADE',
    ]


def test_retirement_downgrade_requires_archival_restore() -> None:
    path = Path(__file__).parents[2] / "alembic/versions/090_retire_governed_pack_stack.py"
    spec = spec_from_file_location("retire_governed_pack_stack_downgrade", path)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)

    with pytest.raises(NotImplementedError, match="cannot be restored"):
        migration.downgrade()
