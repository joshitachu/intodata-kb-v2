from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import MagicMock


def test_067_adds_state_claim_transition_and_safe_continuation_backfill(monkeypatch) -> None:
    path = Path(__file__).parents[2] / "alembic/versions/067_unified_conversation_state.py"
    spec = spec_from_file_location("unified_conversation_state_migration", path)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)

    migration.upgrade()

    assert migration.revision == "067_unified_conversation_state"
    assert migration.down_revision == "066_workflow_child_ledger"
    assert {call.args[0] for call in op.create_table.call_args_list} == {
        "harness_conversation_states",
        "harness_conversation_turn_claims",
        "harness_conversation_state_transitions",
    }
    active_index = next(
        call
        for call in op.create_index.call_args_list
        if call.args[0] == "uq_harness_conversation_turn_claim_active_state"
    )
    postgres_predicate = str(active_index.kwargs["postgresql_where"])
    sqlite_predicate = str(active_index.kwargs["sqlite_where"])
    assert postgres_predicate == sqlite_predicate
    assert "effect_started" in postgres_predicate
    assert "recovery_required" in postgres_predicate
    assert "now()" not in postgres_predicate

    backfills = [str(call.args[0]) for call in op.execute.call_args_list]
    state_backfill = next(item for item in backfills if "task_candidates" in item)
    assert "jsonb_array_elements_text" in state_backfill
    assert "envelope::jsonb -> 'missing_slots'" in state_backfill
    assert "jsonb_typeof(envelope::jsonb -> 'missing_slots') = 'array'" in state_backfill
    assert "length(missing.field) BETWEEN 1 AND 80" in state_backfill
    assert "focus_rank <= 30" in state_backfill
    assert (
        "left(nullif(envelope::jsonb -> 'plan' ->> 'objective', ''), 2000)"
        in state_backfill
    )
    assert "bounded_options" in state_backfill
    assert "LIMIT 20" in state_backfill
    assert "harness_pack_drafts" in state_backfill
    assert "harness_workflow_runs" in state_backfill
    assert "ON CONFLICT (conversation_id, owner_user_id) DO NOTHING" in state_backfill
