from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from sqlalchemy import select

from src.db.models import Conversation, ConversationMessage, User
from src.db.models.harness import HarnessAgentTurnAuditRow
from src.harness import agent_chat_runtime
from src.harness.agent_chat_runtime import execute_production_agent_chat_turn
from src.harness.cohort import CohortDecision, RuntimeCohort, stable_user_bucket

pytestmark = pytest.mark.asyncio


_GROUNDED_ANSWER = (
    "Nexus can search your indexed documentation, read runtime evidence, and "
    "explain project structure.\n"
    '```nexus-grounding\n{"answer_status": "grounded", "citations": [], '
    '"disclosures": []}\n```'
)


class _ScriptedChatModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # noqa: ANN001, ANN201
        del tools, tool_choice, kwargs
        return self


def _install_v2(monkeypatch) -> None:
    def fake_model(_settings):  # noqa: ANN001
        return _ScriptedChatModel(responses=[AIMessage(content=_GROUNDED_ANSWER)])

    def fake_select(*, user_id, transport, permissions, project_scope, runtime):  # noqa: ANN001
        del transport, permissions, project_scope, runtime
        return CohortDecision(
            cohort=RuntimeCohort.V2_READONLY_PRIMARY,
            cohort_source="flag_readonly_canary",
            cohort_bucket=stable_user_bucket(user_id),
            reason_code="readonly_allowlist",
        )

    monkeypatch.setattr(agent_chat_runtime, "build_readonly_primary_chat_model", fake_model)
    monkeypatch.setattr(agent_chat_runtime, "_select_cohort", fake_select)


async def _run_turn(db_session, *, title, client_turn_id, transport, monkeypatch):
    _install_v2(monkeypatch)
    user = User(username=f"v2-{client_turn_id}", email=f"{client_turn_id}@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(title=title, user_id=user.id)
    db_session.add(conversation)
    await db_session.commit()
    await db_session.refresh(conversation, ["messages"])
    execution = await execute_production_agent_chat_turn(
        db=db_session,
        qdrant=None,  # type: ignore[arg-type]
        conversation=conversation,
        query="What can you do?",
        images=None,
        client_turn_id=client_turn_id,
        requested_project_id=None,
        user_id=user.id,
        user_groups=[],
        project_scope=None,
        permissions=None,
        access_token=None,
        transport=transport,
        model=None,
    )
    return execution, user, conversation


async def test_v2_grounded_answer_persists_additive_structured_metadata(
    db_session, monkeypatch
) -> None:
    execution, user, _ = await _run_turn(
        db_session,
        title="V2 grounded",
        client_turn_id="v2-turn-1",
        transport="nonstream",
        monkeypatch=monkeypatch,
    )
    structured = execution.assistant_message.structured_answer
    assert structured["response_type"] == "agent_response_v2_grounded"
    assert structured["answer_status"] == "grounded"
    assert "search your indexed documentation" in structured["content"]
    assert structured["attribution"]["model_profile"] == "kimi/kimi-k2.5"
    # No fact-ID renderer artifact leaked into the visible content.
    assert "nexus-grounding" not in execution.assistant_message.content

    audit = (await db_session.execute(select(HarnessAgentTurnAuditRow))).scalar_one()
    assert audit.cohort_source == "flag_readonly_canary"
    assert audit.cohort_bucket == stable_user_bucket(user.id)


async def test_transport_parity_persisted_structured_answer_is_identical(
    db_session, monkeypatch
) -> None:
    stream_exec, _, _ = await _run_turn(
        db_session,
        title="V2 stream",
        client_turn_id="v2-stream",
        transport="stream",
        monkeypatch=monkeypatch,
    )
    nonstream_exec, _, _ = await _run_turn(
        db_session,
        title="V2 nonstream",
        client_turn_id="v2-nonstream",
        transport="nonstream",
        monkeypatch=monkeypatch,
    )
    stream_bytes = json.dumps(stream_exec.assistant_message.structured_answer, sort_keys=True)
    nonstream_bytes = json.dumps(
        nonstream_exec.assistant_message.structured_answer, sort_keys=True
    )
    assert stream_bytes == nonstream_bytes
    assert stream_exec.assistant_message.content == nonstream_exec.assistant_message.content


async def test_get_conversation_reads_back_v2_grounded_and_ignores_unknown(
    db_session, monkeypatch
) -> None:
    from src.api.routes.conversations import get_conversation

    user = User(
        username="v2-readback", email="v2-readback@example.com", is_superadmin=True
    )
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(title="Readback", user_id=user.id)
    db_session.add(conversation)
    await db_session.flush()
    grounded_payload = {
        "response_type": "agent_response_v2_grounded",
        "content": "Two projects exist.",
        "answer_status": "grounded",
        "citations": [{"claim_key": "count", "evidence_refs": ["ev-1"]}],
        "disclosures": [],
        "attribution": {"runtime_release": "r", "model_profile": "kimi/kimi-k2.5"},
    }
    db_session.add_all(
        [
            ConversationMessage(
                conversation_id=conversation.id,
                role="assistant",
                content="Two projects exist.",
                structured_answer=grounded_payload,
            ),
            ConversationMessage(
                conversation_id=conversation.id,
                role="assistant",
                content="Legacy answer with an unknown shape.",
                structured_answer={"response_type": "some_future_type", "x": 1},
            ),
        ]
    )
    await db_session.commit()

    detail = await get_conversation(conversation.id, db_session, user, None)
    by_content = {message.content: message for message in detail.messages}
    grounded = by_content["Two projects exist."]
    assert grounded.structured_answer == grounded_payload
    # Unknown future response types are ignored (old clients see nothing).
    unknown = by_content["Legacy answer with an unknown shape."]
    assert unknown.structured_answer is None
