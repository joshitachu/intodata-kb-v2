from __future__ import annotations

import datetime

import pytest
from sqlalchemy import select

from src.db.models import User
from src.db.models.harness_pack import HarnessCustomPackVersionRow, HarnessOrganizationGovernancePolicyRow
from src.harness.packs.models import PackScope, PackStackSelection, PackTrust, StyleAudience
from src.harness.packs.project_service import PackServiceError, PackValidationError, ProjectPackService
from src.harness.registry import base_capability_registry


def style_payload(pack_id: str, *, version: str = "1.0.0", audience: str = "engineering") -> dict:
    return {
        "id": pack_id,
        "name": pack_id.replace("style.", "").replace("-", " ").title(),
        "version": version,
        "presentation": {"audience": audience},
    }


def governed_selection(*style_ids: str) -> PackStackSelection:
    return PackStackSelection(
        foundation_pack_ids=frozenset({"nexus.documentation"}),
        functional_pack_ids=frozenset({"nexus.data-integration"}),
        style_pack_ids=frozenset(style_ids),
    )


async def test_private_and_project_style_versions_are_immutable(db_session) -> None:
    user = User(username="style-owner", email="style-owner@example.com")
    db_session.add(user)
    await db_session.flush()
    service = ProjectPackService(db_session, base_capability_registry())

    private = await service.create_style(
        scope=PackScope.USER, payload=style_payload("style.private-report"), user_id=user.id
    )
    private_v2 = await service.save_style_version(
        scope=PackScope.USER,
        pack_id=private.manifest.id,
        payload=style_payload(private.manifest.id, version="1.0.1", audience="business"),
        user_id=user.id,
    )
    project = await service.create_style(
        scope=PackScope.PROJECT,
        project_id="project-packs",
        payload=style_payload("style.project-report"),
        user_id=user.id,
    )
    project_v2 = await service.save_style_version(
        scope=PackScope.PROJECT,
        project_id="project-packs",
        pack_id=project.manifest.id,
        payload=style_payload(project.manifest.id, version="1.0.1", audience="operations"),
        user_id=user.id,
    )

    assert private_v2.manifest.version == "1.0.1"
    assert private_v2.manifest.style and private_v2.manifest.style.audience is StyleAudience.BUSINESS
    assert project_v2.manifest.version == "1.0.1"
    assert project_v2.manifest.style and project_v2.manifest.style.audience is StyleAudience.OPERATIONS
    with pytest.raises(PackValidationError, match="greater"):
        await service.save_style_version(
            scope=PackScope.USER,
            pack_id=private.manifest.id,
            payload=style_payload(private.manifest.id, version="1.0.1"),
            user_id=user.id,
        )


async def test_style_creation_rejects_semantic_content(db_session) -> None:
    user = User(username="style-reject", email="style-reject@example.com")
    db_session.add(user)
    await db_session.flush()
    service = ProjectPackService(db_session, base_capability_registry())
    payload = style_payload("style.semantic-attempt")
    payload["workflows"] = [{"id": "unsafe.workflow"}]

    with pytest.raises(PackValidationError, match="accept only"):
        await service.create_style(scope=PackScope.USER, payload=payload, user_id=user.id)


async def test_legacy_custom_pack_is_quarantined_from_activation(db_session) -> None:
    user = User(username="legacy-pack", email="legacy-pack@example.com")
    db_session.add(user)
    await db_session.flush()
    db_session.add(HarnessCustomPackVersionRow(
        project_id="project-packs",
        pack_id="custom.legacy-domain",
        version="1.0.0",
        content_hash="a" * 64,
        manifest={"id": "custom.legacy-domain", "semantic": "old broad content"},
        governance_state="legacy",
        created_by_user_id=user.id,
        created_at=datetime.datetime.now(datetime.UTC),
    ))
    await db_session.commit()
    service = ProjectPackService(db_session, base_capability_registry())

    with pytest.raises(PackServiceError, match="legacy or unknown"):
        await service.activate(
            project_id="project-packs",
            workflow_key="documentation.generate",
            active_pack_ids=["custom.legacy-domain"],
            user_id=user.id,
        )


async def test_private_styles_are_owner_scoped_and_project_styles_are_project_scoped(db_session) -> None:
    owner = User(username="private-owner", email="private-owner@example.com")
    other = User(username="private-other", email="private-other@example.com")
    db_session.add_all([owner, other])
    await db_session.flush()
    service = ProjectPackService(db_session, base_capability_registry())
    owner_style = await service.create_style(
        scope=PackScope.USER, payload=style_payload("style.owner-private"), user_id=owner.id
    )
    owner_shared = await service.create_style(
        scope=PackScope.USER, payload=style_payload("style.private-shared"), user_id=owner.id
    )
    other_style = await service.create_style(
        scope=PackScope.USER, payload=style_payload("style.private-shared"), user_id=other.id
    )
    await service.create_style(
        scope=PackScope.PROJECT,
        project_id="project-a",
        payload=style_payload("style.private-shared"),
        user_id=owner.id,
    )

    visible_to_other = await service.available("project-b", other.id)
    assert other_style.manifest.id in {item.id for item in visible_to_other}
    project_shared = next(
        item for item in await service.style_packs(
            scope=PackScope.PROJECT, project_id="project-a", user_id=owner.id
        )
        if item.manifest.name == owner_shared.manifest.name
    )
    assert owner_shared.manifest.id in {item.id for item in await service.available("project-b", owner.id)}
    assert project_shared.manifest.id not in {item.id for item in visible_to_other}
    assert len({owner_shared.manifest.id, other_style.manifest.id, project_shared.manifest.id}) == 3
    activated = await service.activate(
        project_id="project-a",
        workflow_key="documentation.generate",
        selection=governed_selection(owner_shared.manifest.id, project_shared.manifest.id),
        user_id=owner.id,
    )
    assert set(activated.selection.style_pack_ids) == {
        owner_shared.manifest.id, project_shared.manifest.id
    }
    with pytest.raises(PackServiceError, match="unknown active pack"):
        await service.activate(
            project_id="project-b",
            workflow_key="documentation.generate",
            selection=governed_selection(owner_style.manifest.id),
            user_id=other.id,
        )


async def test_activation_compiles_and_persists_exact_governed_stack(db_session) -> None:
    user = User(username="stack-owner", email="stack-owner@example.com")
    db_session.add(user)
    await db_session.flush()
    service = ProjectPackService(db_session, base_capability_registry())
    style = await service.create_style(
        scope=PackScope.USER, payload=style_payload("style.engineering-report"), user_id=user.id
    )

    activated = await service.activate(
        project_id="project-packs",
        workflow_key="documentation.generate",
        selection=governed_selection(style.manifest.id),
        user_id=user.id,
    )
    hydrated = await service.resolve_governed_for_execution("project-packs", "documentation.generate")

    assert activated.compiled_snapshot.snapshot_hash
    assert activated.selection.style_pack_ids == frozenset({style.manifest.id})
    assert hydrated is not None
    assert hydrated.snapshot == activated.compiled_snapshot
    assert hydrated.presentation == style.manifest.style
    assert [manifest.id for manifest in hydrated.manifests] == [
        "nexus.data-integration", "nexus.documentation", style.manifest.id
    ]


async def test_legacy_active_ids_derives_required_foundation_from_policy_and_include_closure(db_session) -> None:
    user = User(username="legacy-activation", email="legacy-activation@example.com")
    db_session.add(user)
    await db_session.flush()
    service = ProjectPackService(db_session, base_capability_registry())

    activated = await service.activate(
        project_id="project-packs",
        workflow_key="documentation.generate",
        active_pack_ids=["nexus.data-integration"],
        user_id=user.id,
    )

    assert activated.selection.foundation_pack_ids == frozenset({"nexus.documentation"})
    assert activated.selection.functional_pack_ids == frozenset({"nexus.data-integration"})
    assert activated.compiled_snapshot.foundation_pack_id == "nexus.documentation"


async def test_unapproved_functional_pack_is_rejected_by_persisted_org_policy(db_session) -> None:
    user = User(username="policy-owner", email="policy-owner@example.com")
    db_session.add(user)
    await db_session.flush()
    service = ProjectPackService(db_session, base_capability_registry())
    policy = await service.organization_policy()
    row = await db_session.scalar(
        select(HarnessOrganizationGovernancePolicyRow).where(
            HarnessOrganizationGovernancePolicyRow.organization_id == "default"
        )
    )
    assert row is not None
    payload = policy.model_dump(mode="json")
    payload["allowed_functional_pack_ids"] = []
    row.policy = payload
    await db_session.commit()

    with pytest.raises(PackServiceError, match="not allowed"):
        await service.activate(
            project_id="project-packs",
            workflow_key="documentation.generate",
            selection=governed_selection(),
            user_id=user.id,
        )


async def test_quality_threshold_marks_style_promotion_eligible_without_certification(db_session) -> None:
    user = User(username="quality-owner", email="quality-owner@example.com")
    db_session.add(user)
    await db_session.flush()
    service = ProjectPackService(db_session, base_capability_registry())
    style = await service.create_style(
        scope=PackScope.USER, payload=style_payload("style.quality-report"), user_id=user.id
    )

    scored = await service.record_style_quality(
        scope=PackScope.USER,
        pack_id=style.manifest.id,
        evaluator_user_id=user.id,
        quality_score=0.9,
        evaluation_score=0.91,
        evaluation_metadata={"suite": "style-eval-v1", "run_id": "eval-7"},
    )

    assert scored.promotion_eligible is True
    assert scored.review_status == "promotion_eligible"
    assert scored.manifest.governance is not None
    assert scored.manifest.governance.trust is PackTrust.UNREVIEWED
    assert scored.manifest.governance.phase.value == "style"
