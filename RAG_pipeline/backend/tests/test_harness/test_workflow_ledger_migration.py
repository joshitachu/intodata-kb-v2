from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import MagicMock


def test_066_creates_exact_child_ledger_and_conflict_safe_legacy_backfill(monkeypatch) -> None:
    path = Path(__file__).parents[2] / "alembic/versions/066_workflow_child_ledger.py"
    spec = spec_from_file_location("workflow_child_ledger_migration", path)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)

    migration.upgrade()

    assert migration.revision == "066_workflow_child_ledger"
    assert migration.down_revision == "065_detached_pack_drafts"
    created = {call.args[0] for call in op.create_table.call_args_list}
    assert created == {
        "harness_workflow_child_receipts",
        "harness_workflow_child_observations",
    }
    receipt_constraints = {
        item.name
        for item in op.create_table.call_args_list[0].args[1:]
        if getattr(item, "name", None)
    }
    assert {
        "uq_harness_workflow_child_parent_provider_kind",
        "uq_harness_workflow_child_provider_external_run",
        "uq_harness_workflow_child_cancellation_idempotency",
    } <= receipt_constraints
    backfill = str(op.execute.call_args.args[0])
    assert "runtime_metadata -> 'external_generation' ->> 'run_id'" in backfill
    assert "external_reference_count = 1" in backfill
    assert "ON CONFLICT DO NOTHING" in backfill
