"""One-shot pack creation from chat.

The point of this module is that the Workbench and chat go through the SAME
author and produce the SAME format. Before the collapse they did not: the
Workbench authored ``plugin.yaml`` through MCP while chat translated friendly
fields into a weaker JSON artifact.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from src.db.models import Conversation, ConversationMessage, User
from src.db.models.harness_pack import HarnessPackDraftRow
from src.harness.capability_runtime import CapabilityExecutionContext, CapabilityExecutionRequest
from src.harness.conversation_state import ConversationStateRepository
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.models import PACK_CREATE_CAPABILITY
from src.harness.packs.builder_workflow import PackBuilderWorkflow, PackGenerationResult
from src.harness.packs.draft_artifact import PackDraftArtifact
from src.harness.packs.draft_service import PackDraftService, SavedPackRecord
from src.harness.registry import base_capability_registry


MANIFEST = """\
name: talend-migration-diagnostic
version: 0.1.0
description: Diagnose a Talend estate ahead of an Azure migration.
lens:
  sections:
    mode: replace
    declare:
      - id: JOB_TOPOLOGY
        question: which jobs call which, via tRunJob
docs:
  - name: MIGRATION_PLAN
    phase2_sections: [JOB_TOPOLOGY]
"""


def _creation_arguments(name: str = "Talend Migration Diagnostic") -> dict[str, Any]:
    return {
        "name": name,
        "brief": (
            "Diagnose a Talend estate ahead of an Azure migration: map tRunJob "
            "call topology and produce a Talend-to-Azure Migration Plan."
        ),
    }


def _stub_save(pack_id: int = 31):
    async def save(self, *, draft_id, owner_user_id, expected_revision, **kwargs):  # noqa: ANN001, ANN202
        del kwargs
        row = await self._owned_active_draft(draft_id, owner_user_id)
        artifact = PackDraftArtifact.model_validate(row.artifact)
        row.status = "saved"
        row.revision += 1
        await self._db.flush()
        return SavedPackRecord(
            mcp_pack_id=pack_id,
            name=row.name,
            version="1.0.0",
            documents=artifact.doc_names,
            content_hash="c" * 64,
        )

    return save


async def _executor_context(db_session, suffix: str):
    owner = User(username=f"pack-create-{suffix}", email=f"pack-create-{suffix}@example.com")
    db_session.add(owner)
    await db_session.flush()
    conversation = Conversation(user_id=owner.id, title="Pack creation")
    db_session.add(conversation)
    await db_session.flush()
    message = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content="Create the Talend migration Pack.",
    )
    db_session.add(message)
    await db_session.flush()
    states = ConversationStateRepository(db_session)
    state = await states.get_or_create(conversation_id=conversation.id, owner_user_id=owner.id)
    claim = await states.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=owner.id,
        message_id=message.id,
        expected_version=state.version,
        idempotency_key=f"pack-create:{message.id}",
    )
    registry = base_capability_registry()
    executor = HarnessCapabilityExecutor(
        registry,
        pack_draft_service=PackDraftService(db_session, registry),
        conversation_state_repository=states,
    )
    context = CapabilityExecutionContext(
        user_id=owner.id,
        conversation_id=conversation.id,
        permissions={"manage_packs"},
        current_message_id=message.id,
        conversation_state_version=state.version,
        conversation_turn_claim_id=claim.id,
        conversation_turn_claim_token=claim.lease_token,
    )
    return executor, context, states


async def test_workbench_and_chat_share_one_author_and_one_format(
    db_session, monkeypatch
) -> None:
    generated = PackDraftArtifact(manifest_yaml=MANIFEST)
    calls: list[dict[str, Any]] = []

    async def generate(self, **kwargs):  # noqa: ANN001, ANN202
        del self
        calls.append(kwargs)
        return PackGenerationResult(artifact=generated, name="talend-migration-diagnostic")

    monkeypatch.setattr(PackBuilderWorkflow, "generate", generate)
    monkeypatch.setattr(PackDraftService, "save", _stub_save())

    workbench_owner = User(
        username="pack-workbench-shared", email="pack-workbench-shared@example.com"
    )
    db_session.add(workbench_owner)
    await db_session.flush()
    registry = base_capability_registry()
    draft_service = PackDraftService(db_session, registry)
    workbench_draft = await draft_service.create_draft(owner_user_id=workbench_owner.id)
    drafted, _ = await PackBuilderWorkflow().run_for_draft(
        service=draft_service,
        draft_id=workbench_draft.id,
        owner_user_id=workbench_owner.id,
        query="Build this Pack in the Workbench.",
    )

    executor, context, _ = await _executor_context(db_session, suffix="shared")
    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=PACK_CREATE_CAPABILITY.id,
            arguments=_creation_arguments(),
            idempotency_key="chat-rich-pack",
        ),
        context,
    )

    assert observation.status == "succeeded"
    # Same author, same stored shape on both routes.
    assert drafted.artifact == generated.model_dump(mode="json")
    assert observation.data["mcp_pack_id"] == 31
    assert observation.data["documents"] == ["MIGRATION_PLAN"]
    assert observation.data["name"] == _creation_arguments()["name"]

    assert len(calls) == 2
    # Workbench refines whatever the draft holds; chat creation starts fresh.
    assert calls[0]["current_artifact"] == {}
    assert calls[1].get("current_artifact") is None
    # The user's brief reaches the author verbatim -- chat invents no structure.
    assert "tRunJob" in calls[1]["query"]
    assert "Talend-to-Azure Migration Plan" in calls[1]["query"]


async def test_author_failure_persists_no_pack_and_no_draft(db_session, monkeypatch) -> None:
    async def failed(self, **kwargs):  # noqa: ANN001, ANN202
        del self, kwargs
        return PackGenerationResult(
            error_code="authoring_failed",
            error_message="The authoring model is unavailable. The draft was not changed.",
        )

    monkeypatch.setattr(PackBuilderWorkflow, "generate", failed)
    executor, context, _ = await _executor_context(db_session, suffix="failed")

    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=PACK_CREATE_CAPABILITY.id,
            arguments=_creation_arguments(),
            idempotency_key="chat-failed-pack",
        ),
        context,
    )

    assert observation.status == "failed"
    assert observation.error_code == "pack_generation_authoring_failed"
    # "No Pack was created" replaces the draft-oriented wording for one-shot creation.
    assert "No Pack was created" in observation.summary
    assert await db_session.scalar(select(func.count(HarnessPackDraftRow.id))) == 0


async def test_a_contract_violating_pack_persists_nothing(db_session, monkeypatch) -> None:
    async def invalid(self, **kwargs):  # noqa: ANN001, ANN202
        del self, kwargs
        return PackGenerationResult(
            error_code="invalid_output",
            error_message=(
                "The authored pack did not satisfy the pack contract "
                "(a pack you author cannot set the server-owned field: layer). "
                "The draft was not changed."
            ),
        )

    monkeypatch.setattr(PackBuilderWorkflow, "generate", invalid)
    executor, context, _ = await _executor_context(db_session, suffix="invalid")

    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=PACK_CREATE_CAPABILITY.id,
            arguments=_creation_arguments(),
            idempotency_key="chat-invalid-pack",
        ),
        context,
    )

    assert observation.status == "failed"
    assert observation.error_code == "pack_generation_invalid_output"
    assert observation.retryable is True
    assert await db_session.scalar(select(func.count(HarnessPackDraftRow.id))) == 0


async def test_save_and_receipt_roll_back_together(db_session, monkeypatch) -> None:
    """The draft row and the conversation receipt share one savepoint."""
    generated = PackDraftArtifact(manifest_yaml=MANIFEST)

    async def generate(self, **kwargs):  # noqa: ANN001, ANN202
        del self, kwargs
        return PackGenerationResult(artifact=generated, name="talend-migration-diagnostic")

    monkeypatch.setattr(PackBuilderWorkflow, "generate", generate)
    monkeypatch.setattr(PackDraftService, "save", _stub_save())

    executor, context, states = await _executor_context(db_session, suffix="rollback")

    async def exploding_record_effect(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("simulated receipt failure")

    monkeypatch.setattr(states, "record_effect", exploding_record_effect)

    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=PACK_CREATE_CAPABILITY.id,
            arguments=_creation_arguments(),
            idempotency_key="chat-rollback-pack",
        ),
        context,
    )

    assert observation.status == "failed"
    assert await db_session.scalar(select(func.count(HarnessPackDraftRow.id))) == 0
