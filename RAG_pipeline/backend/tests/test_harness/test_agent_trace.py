from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ValidationError

from src.db.models import Conversation, ConversationMessage, User
from src.harness.agent_runs import AgentRunService
from src.harness.agent_trace import AgentTraceService, safe_argument_shape, safe_validation_errors

def test_safe_argument_shape_never_retains_values() -> None:
    shape = safe_argument_shape({
        "name": "talend migration",
        "artifact_patch": {"purpose": "private purpose", "items": ["a", "b"]},
        "token": "super-secret",
    })
    encoded = json.dumps(shape)
    assert "talend migration" not in encoded
    assert "private purpose" not in encoded
    assert "super-secret" not in encoded
    assert shape["name"] == "string"
    assert shape["token"] == "redacted"


def test_safe_validation_errors_keep_path_and_code_only() -> None:
    class Input(BaseModel):
        project_id: str

    with pytest.raises(ValidationError) as caught:
        Input.model_validate({})
    diagnostics = safe_validation_errors(caught.value)
    assert diagnostics == [{
        "path": ["project_id"], "code": "missing",
        "message": "Required field is missing.",
    }]


@pytest.mark.asyncio
async def test_trace_service_projects_ordered_safe_steps(db_session) -> None:
    user = User(username="trace-user", email="trace@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(user_id=user.id, title="Trace")
    db_session.add(conversation)
    await db_session.flush()
    message = ConversationMessage(conversation_id=conversation.id, role="user", content="secret prompt")
    db_session.add(message)
    await db_session.flush()
    runs = AgentRunService(db_session)
    run, _ = await runs.start_or_load(
        conversation_id=conversation.id, owner_user_id=user.id,
        user_message_id=message.id, runtime_cohort="v2_effect_primary",
        transport="stream", budget={}, objective_summary="secret prompt",
    )
    await runs.record_step(
        run_id=run.run_id, sequence=1, kind="routing",
        reason_code="server_selected_runtime_cohort",
        observation_status="running",
    )
    await runs.record_step(
        run_id=run.run_id, sequence=2, kind="tool",
        capability_id="mcp.docs.get_generation_status",
        semantic_name="get_documentation_generation_status",
        call_ref="call-1", attempt=1, reason_code="model_selected_capability",
        arguments_hash="a" * 64,
        argument_shape={"name": "string", "artifact_patch": {"purpose": "string"}},
        validation_errors=[{"path": ["artifact_patch"], "code": "missing", "message": "Required field is missing."}],
        observation_status="validation_failed",
    )
    detail = await AgentTraceService(db_session).get_trace(run.run_id)
    assert detail is not None
    assert detail.objective_summary is None
    assert detail.steps[0].observation_status == "selected"
    assert detail.steps[0].details["routing"]["label"] == "Full governed agent runtime"
    assert detail.steps[1].call_ref == "call-1"
    assert detail.steps[1].validation_errors[0]["path"] == ["artifact_patch"]
    capability = detail.steps[1].details["capability"]
    assert capability["risk_tier"] == "read_only"
    assert capability["input_schema"]["fields"][0] == {
        "name": "project_id", "type": "string", "required": True,
        "minLength": 1, "maxLength": 255,
    }
    assert "secret prompt" not in detail.model_dump_json()
