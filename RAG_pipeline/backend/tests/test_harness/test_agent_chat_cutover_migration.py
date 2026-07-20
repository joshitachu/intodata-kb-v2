from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import MagicMock

from sqlalchemy import UniqueConstraint

from src.db.models.conversation import ConversationMessage
from src.db.models.harness import HarnessAgentTurnAuditRow


def test_068_aligns_idempotency_constraints_and_audit_timestamps(monkeypatch) -> None:
    path = Path(__file__).parents[2] / "alembic/versions/068_agent_chat_cutover_audit.py"
    spec = spec_from_file_location("agent_chat_cutover_migration", path)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)

    migration.upgrade()

    assert migration.revision == "068_agent_chat_cutover_audit"
    assert migration.down_revision == "067_unified_conversation_state"
    unique_calls = {
        call.args[0]: tuple(call.args[2])
        for call in op.create_unique_constraint.call_args_list
    }
    assert unique_calls == {
        "uq_conversation_messages_client_turn": (
            "conversation_id",
            "client_turn_id",
        ),
        "uq_conversation_messages_in_reply_to": ("in_reply_to_message_id",),
    }
    audit_call = next(
        call
        for call in op.create_table.call_args_list
        if call.args[0] == "harness_agent_turn_audits"
    )
    audit_columns = {item.name for item in audit_call.args[1:] if hasattr(item, "name")}
    assert {"created_at", "updated_at"} <= audit_columns
    assert "updated_at" in HarnessAgentTurnAuditRow.__table__.c
    model_uniques = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in ConversationMessage.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert model_uniques["uq_conversation_messages_client_turn"] == (
        "conversation_id",
        "client_turn_id",
    )
    assert model_uniques["uq_conversation_messages_in_reply_to"] == (
        "in_reply_to_message_id",
    )
