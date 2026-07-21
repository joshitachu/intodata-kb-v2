from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import MagicMock


def test_062_adds_private_draft_and_exact_version_receipt_storage(monkeypatch) -> None:
    path = Path(__file__).parents[2] / "alembic/versions/062_pack_draft_builder.py"
    spec = spec_from_file_location("pack_draft_builder_migration", path)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)
    batch = MagicMock()
    op.batch_alter_table.return_value.__enter__.return_value = batch

    migration.upgrade()

    created = {call.args[0] for call in op.create_table.call_args_list}
    assert {"harness_pack_drafts", "harness_private_analysis_packs", "harness_private_analysis_pack_versions", "harness_private_analysis_pack_shares", "harness_pack_preview_receipts", "harness_pack_promotion_proposals"} <= created
    assert "private_version_receipts" in [call.args[0].name for call in batch.add_column.call_args_list]


def test_063_aligns_private_pack_tables_with_base_timestamps(monkeypatch) -> None:
    path = Path(__file__).parents[2] / "alembic/versions/063_pack_builder_timestamp_parity.py"
    spec = spec_from_file_location("pack_builder_timestamp_parity_migration", path)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)

    migration.upgrade()

    added = {(call.args[0], call.args[1].name) for call in op.add_column.call_args_list}
    assert added == {(table_name, "updated_at") for table_name in migration._TABLES}


def test_064_adds_owner_private_reference_blobs_and_exact_bindings(monkeypatch) -> None:
    path = Path(__file__).parents[2] / "alembic/versions/064_pack_reference_attachments.py"
    spec = spec_from_file_location("pack_reference_attachments_migration", path)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)
    batch = MagicMock()
    op.batch_alter_table.return_value.__enter__.return_value = batch

    migration.upgrade()

    created = {call.args[0] for call in op.create_table.call_args_list}
    assert {"harness_pack_attachment_blobs", "harness_pack_attachment_bindings"} <= created
    assert {call.args[0].name for call in batch.add_column.call_args_list} == {
        "target_private_pack_id", "base_private_pack_version_id"
    }


def test_065_allows_detached_drafts_and_adds_bounded_discovery_context(monkeypatch) -> None:
    path = Path(__file__).parents[2] / "alembic/versions/065_detach_pack_drafts_from_conversations.py"
    spec = spec_from_file_location("detach_pack_drafts_migration", path)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)
    batch = MagicMock()
    op.batch_alter_table.return_value.__enter__.return_value = batch

    migration.upgrade()

    alter = batch.alter_column.call_args.args
    assert alter[0] == "conversation_id"
    assert alter[2:] == ()
    assert batch.alter_column.call_args.kwargs["nullable"] is True
    assert [call.args[0].name for call in batch.add_column.call_args_list] == [
        "discovery_context"
    ]
