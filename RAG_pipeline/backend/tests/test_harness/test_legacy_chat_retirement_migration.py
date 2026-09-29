from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import MagicMock


def test_069_fails_closed_before_dropping_legacy_continuations(monkeypatch) -> None:
    path = (
        Path(__file__).parents[2]
        / "alembic/versions/069_retire_legacy_chat_continuations.py"
    )
    spec = spec_from_file_location("legacy_chat_retirement_migration", path)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)

    migration.upgrade()

    assert migration.revision == "069_retire_legacy_chat_paths"
    assert migration.down_revision == "068_agent_chat_cutover_audit"
    sql = "\n".join(str(call.args[0]) for call in op.execute.call_args_list)
    assert "malformed active envelope" in sql
    assert "ConversationState parity mismatch" in sql
    assert "referenced task authority missing" in sql
    assert "missing_slots" in sql
    assert "requested_inputs" in sql
    assert "task_ref_id" in sql
    assert "workflow_run_id" in sql
    assert "draft_id" in sql
    op.drop_table.assert_called_once_with("harness_conversation_continuations")


def test_blank_operator_retirement_template_is_explicitly_deployment_blocking() -> None:
    repository_root = Path(__file__).parents[4]
    template = (
        repository_root
        / "RAG_pipeline/docs/agent-chat-retirement-evidence-template.md"
    ).read_text()
    runbook = (
        repository_root / "RAG_pipeline/docs/agent-chat-cutover-runbook.md"
    ).read_text()

    assert "status: template_unverified" in template
    assert "approved_for_slice_8_deployment: false" in template
    assert "Decision: **BLOCKED — TEMPLATE ONLY**" in template
    assert "The blank template is not evidence and must fail review." in runbook
