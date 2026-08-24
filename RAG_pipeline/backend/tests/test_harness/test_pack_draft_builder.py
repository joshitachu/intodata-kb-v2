"""Pack draft lifecycle after the collapse to one format.

A draft now carries a ``plugin.yaml`` manifest, and saving persists it THROUGH
MCP rather than into a second registry here. These tests pin the parts that are
easy to regress in that arrangement: the draft table stays scratch space, the
authority boundary is enforced locally as well as in MCP, and a failed save
leaves nothing half-written.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import func, select

from src.db.models import Conversation, User
from src.db.models.harness_pack import HarnessPackDraftRow
from src.harness.packs.draft_artifact import PackDraftArtifact
from src.harness.packs.draft_service import (
    DraftConflictError,
    DraftValidationError,
    PackDraftService,
)
from src.harness.providers.mcp_packs import McpPackProvider
from src.harness.registry import base_capability_registry


MANIFEST = """\
name: supply-chain-disruption
version: 0.1.0
description: Assess supply-chain disruption and support allocation decisions.
lens:
  focus:
    include: [source_summary, config_references]
  sections:
    mode: replace
    declare:
      - id: EXPOSURE
        question: which suppliers and nodes are exposed
      - id: TIME_TO_IMPACT
        question: how long until interruption
audience:
  roles: [regional procurement director]
  tone: neutral
  technical_depth: moderate
docs:
  - name: EXECUTIVE_DISRUPTION_BRIEF
    phase2_sections: [EXPOSURE]
  - name: SUPPLIER_RISK_SCORECARD
    phase2_sections: [TIME_TO_IMPACT]
  - name: MITIGATION_ACTION_PLAN
"""


def _artifact(manifest: str = MANIFEST) -> PackDraftArtifact:
    return PackDraftArtifact(manifest_yaml=manifest, files={"prompts/phase2.md": "body"})


def _packs(*, pack_id: int = 11, status: int = 201, fail: bool = False) -> McpPackProvider:
    """A gateway wired to a stand-in MCP, capturing what we send it."""

    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(422, json={"detail": "manifest rejected by the loader"})
        body = json.loads(request.content)
        handler.seen.append(body)  # type: ignore[attr-defined]
        return httpx.Response(
            status,
            json={
                "id": pack_id,
                "name": body["name"],
                "version": "1.0.0",
                "description": None,
                "origin": "personal",
                "personal": True,
                "usable": True,
                "certification_status": "legacy_untrusted",
            },
        )

    handler.seen = []  # type: ignore[attr-defined]
    provider = McpPackProvider(
        base_url="http://mcp.test/mcp", timeout=5.0, transport=httpx.MockTransport(handler)
    )
    provider.seen = handler.seen  # type: ignore[attr-defined]
    return provider


def _service(db, provider: McpPackProvider | None = None) -> PackDraftService:
    return PackDraftService(db, base_capability_registry(), provider or _packs())


async def _owner(db, username: str) -> User:
    owner = User(username=username, email=f"{username}@example.com")
    db.add(owner)
    await db.flush()
    return owner


async def test_saving_a_draft_persists_the_manifest_through_mcp(db_session) -> None:
    owner = await _owner(db_session, "pack-owner")
    conversation = Conversation(user_id=owner.id, title="Supply chain Pack")
    db_session.add(conversation)
    await db_session.flush()

    provider = _packs(pack_id=42)
    service = _service(db_session, provider)
    draft = await service.create_draft(
        conversation_id=conversation.id,
        owner_user_id=owner.id,
        name="Supply Chain Disruption",
    )
    draft = await service.replace_artifact(
        draft_id=draft.id,
        owner_user_id=owner.id,
        expected_revision=draft.revision,
        artifact=_artifact(),
    )
    assert draft.validation["valid"] is True
    assert draft.validation["documents"] == [
        "EXECUTIVE_DISRUPTION_BRIEF",
        "SUPPLIER_RISK_SCORECARD",
        "MITIGATION_ACTION_PLAN",
    ]

    saved = await service.save(
        draft_id=draft.id, owner_user_id=owner.id, expected_revision=draft.revision
    )

    assert saved.mcp_pack_id == 42
    assert saved.documents == (
        "EXECUTIVE_DISRUPTION_BRIEF",
        "SUPPLIER_RISK_SCORECARD",
        "MITIGATION_ACTION_PLAN",
    )
    assert saved.content_hash

    # The manifest travels verbatim, and starter inheritance is OFF so an
    # unrelated starter prompt cannot leak into an authored pack.
    sent = provider.seen[0]
    assert sent["manifest_yaml"] == MANIFEST
    assert sent["inherit_starter"] is False
    assert sent["files"] == {"prompts/phase2.md": "body"}


async def test_a_saved_draft_leaves_no_second_registry_behind(db_session) -> None:
    """MCP owns saved packs; the RAG side keeps only the draft row."""
    owner = await _owner(db_session, "single-registry-owner")
    service = _service(db_session)
    draft = await service.create_draft(owner_user_id=owner.id, name="One Registry")
    draft = await service.replace_artifact(
        draft_id=draft.id,
        owner_user_id=owner.id,
        expected_revision=draft.revision,
        artifact=_artifact(),
    )
    await service.save(
        draft_id=draft.id, owner_user_id=owner.id, expected_revision=draft.revision
    )

    row = await db_session.get(HarnessPackDraftRow, draft.id)
    assert row.status == "saved"
    assert set(row.artifact) == {"manifest_yaml", "files"}


async def test_upstream_rejection_does_not_mark_the_draft_saved(db_session) -> None:
    owner = await _owner(db_session, "rejected-owner")
    service = _service(db_session, _packs(fail=True))
    draft = await service.create_draft(owner_user_id=owner.id, name="Rejected Pack")
    draft = await service.replace_artifact(
        draft_id=draft.id,
        owner_user_id=owner.id,
        expected_revision=draft.revision,
        artifact=_artifact(),
    )

    with pytest.raises(DraftValidationError, match="could not be saved"):
        await service.save(
            draft_id=draft.id, owner_user_id=owner.id, expected_revision=draft.revision
        )

    row = await db_session.get(HarnessPackDraftRow, draft.id)
    assert row.status == "active"


async def test_atomic_creation_rolls_back_the_draft_row_on_failure(db_session) -> None:
    owner = await _owner(db_session, "atomic-pack-owner")
    service = _service(db_session)

    with pytest.raises(RuntimeError, match="simulated receipt failure"):
        async with service.atomic():
            await service.create_saved_pack(
                conversation_id=None,
                owner_user_id=owner.id,
                name="Atomic Supply Chain Pack",
                artifact=_artifact().model_dump(mode="json"),
            )
            raise RuntimeError("simulated receipt failure")

    assert await db_session.scalar(select(func.count(HarnessPackDraftRow.id))) == 0


async def test_draft_revisions_are_optimistic(db_session) -> None:
    owner = await _owner(db_session, "pack-revision")
    service = _service(db_session)
    draft = await service.create_draft(owner_user_id=owner.id)

    with pytest.raises(DraftConflictError):
        await service.replace_artifact(
            draft_id=draft.id,
            owner_user_id=owner.id,
            expected_revision=99,
            artifact=_artifact(),
        )


async def test_a_draft_cannot_carry_a_server_owned_field(db_session) -> None:
    """The authority boundary holds locally, not only inside MCP."""
    owner = await _owner(db_session, "authority-owner")
    service = _service(db_session)
    draft = await service.create_draft(owner_user_id=owner.id, name="Escalating Pack")

    draft = await service.replace_artifact(
        draft_id=draft.id,
        owner_user_id=owner.id,
        expected_revision=draft.revision,
        artifact=_artifact(MANIFEST + "layer: foundation\n"),
    )
    assert draft.validation["valid"] is False
    assert any("server-owned field" in e for e in draft.validation["errors"])

    with pytest.raises(DraftValidationError, match="invalid"):
        await service.save(
            draft_id=draft.id, owner_user_id=owner.id, expected_revision=draft.revision
        )


async def test_an_unparseable_manifest_reports_instead_of_saving(db_session) -> None:
    owner = await _owner(db_session, "broken-yaml-owner")
    service = _service(db_session)
    draft = await service.create_draft(owner_user_id=owner.id, name="Broken Pack")
    draft = await service.replace_artifact(
        draft_id=draft.id,
        owner_user_id=owner.id,
        expected_revision=draft.revision,
        artifact=PackDraftArtifact(manifest_yaml="name: [unclosed\n"),
    )
    assert draft.validation["valid"] is False
    assert "not valid YAML" in draft.validation["errors"][0]


async def test_a_blank_draft_is_invalid_but_storable(db_session) -> None:
    """Chat opens a draft before it has a manifest; that must not crash."""
    owner = await _owner(db_session, "blank-draft-owner")
    service = _service(db_session)
    draft = await service.create_draft(owner_user_id=owner.id)

    assert draft.artifact == {}
    assert draft.validation["valid"] is False
    assert draft.validation["errors"] == ["the pack has no manifest yet"]


async def test_detached_draft_saves_without_a_conversation(db_session) -> None:
    owner = await _owner(db_session, "detached-pack-owner")
    service = _service(db_session)

    draft = await service.create_draft(owner_user_id=owner.id, name="Detached Pack")
    assert draft.conversation_id is None
    draft = await service.replace_artifact(
        draft_id=draft.id,
        owner_user_id=owner.id,
        expected_revision=draft.revision,
        artifact=_artifact(),
    )
    saved = await service.save(
        draft_id=draft.id, owner_user_id=owner.id, expected_revision=draft.revision
    )
    assert saved.mcp_pack_id == 11


async def test_discovery_context_is_bounded_and_retains_original_brief(db_session) -> None:
    owner = await _owner(db_session, "discovery-pack-owner")
    service = _service(db_session)
    draft = await service.create_draft(owner_user_id=owner.id)

    for index in range(4):
        draft = await service.retain_discovery_turn(
            draft_id=draft.id,
            owner_user_id=owner.id,
            instruction=(
                f"Original supply-chain brief {index}" if index == 0 else f"Follow-up {index}"
            ),
            question=f"Clarification {index}?",
        )

    turns = draft.discovery_context["turns"]
    assert len(turns) == 3
    assert turns[0] == {
        "instruction": "Original supply-chain brief 0",
        "question": "Clarification 0?",
    }
    assert turns[-1] == {"instruction": "Follow-up 3", "question": "Clarification 3?"}
