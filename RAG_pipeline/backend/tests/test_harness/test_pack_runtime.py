from __future__ import annotations

import json
import datetime
import hashlib

import httpx
import pytest

from src.db.models import User
from src.db.models.harness_pack import (
    HarnessPackActivationSnapshotRow,
    HarnessPrivateAnalysisPackRow,
    HarnessPrivateAnalysisPackVersionRow,
)
from src.harness.manifest import build_runtime_capability_manifest
from src.harness.models import (
    DOCUMENTATION_GENERATION_CAPABILITY,
    PACK_CREATE_CAPABILITY,
    PACK_DRAFT_CONFIGURE_CAPABILITY,
    PACK_DRAFT_CREATE_CAPABILITY,
)
from src.harness.capability_handlers import friendly_pack_configuration_patch
from src.harness.executor import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    HarnessCapabilityExecutor,
)
from src.harness.packs.models import PackArtifactV3, PackManifest, PackScope, PackSnapshot, PackType
from src.harness.packs.project_service import PackServiceError, ProjectPackService
from src.harness.packs.runtime import (
    PackRuntimeError,
    compile_governed_pack_registry,
    compile_resolved_pack_registry,
)
from src.harness.registry import base_capability_registry
from src.harness.semantic_tools import build_semantic_tool_snapshot
from src.harness.packs.models import ResolvedPackSet
from src.harness.providers.mcp_docs import McpDocumentationProvider


def _style_payload(pack_id: str, audience: str) -> dict:
    return {
        "id": pack_id,
        "name": "Presentation",
        "version": "1.0.0",
        "presentation": {"audience": audience, "tone": "concise"},
    }


def test_agent_pack_contract_is_small_and_translates_to_valid_v3_artifact() -> None:
    assert PACK_CREATE_CAPABILITY.input_schema["required"] == [
        "name",
        "purpose",
        "audience",
        "focus_components",
        "output_documents",
    ]
    assert "artifact_patch" not in PACK_DRAFT_CONFIGURE_CAPABILITY.input_schema["properties"]

    snapshot = build_semantic_tool_snapshot(base_capability_registry())
    assert snapshot.tool_for_name("create_pack") is not None
    assert snapshot.tool_for_name("create_pack_draft") is None
    assert snapshot.tool_for_name("configure_pack_draft") is not None
    assert snapshot.tool_for_name("update_pack_draft") is None

    patch = friendly_pack_configuration_patch(
        {
            "draft_id": 41,
            "expected_revision": 1,
            "purpose": "Create Talend-to-Azure migrations.",
            "audience": {
                "label": "Engineering and SRE team leads",
                "roles": ["Engineering team leads", "SRE team leads"],
                "desired_outcome": "Plan the migration",
                "seniority": "manager",
                "domain_familiarity": "working",
                "detail_level": "detailed",
            },
            "focus_components": ["tRunJob"],
            "output_documents": [{"title": "Talend Migration Guide"}],
        }
    )
    artifact = PackArtifactV3.model_validate_json(json.dumps(patch))
    assert artifact.analyses[0].id == "trunjob_analysis_1"
    assert artifact.evidence_requirements[0].id == "trunjob_evidence_1"
    assert len(artifact.output_documents) == 1
    assert [section.order for section in artifact.output_documents[0].sections] == [1, 2, 3, 4, 5]


async def test_governed_runtime_keeps_style_out_of_semantic_manifest_and_uses_full_stack_ids(
    db_session,
) -> None:
    user = User(username="runtime-style", email="runtime-style@example.com")
    db_session.add(user)
    await db_session.flush()
    service = ProjectPackService(db_session, base_capability_registry())
    style = await service.create_style(
        scope=PackScope.USER,
        payload=_style_payload("style.runtime", "engineering"),
        user_id=user.id,
    )
    activation = await service.activate(
        project_id="runtime-style",
        workflow_key="documentation.generate",
        user_id=user.id,
        active_pack_ids=["nexus.data-integration", style.manifest.id],
    )
    compiled = await service.resolve_governed_for_execution(
        "runtime-style", "documentation.generate"
    )
    assert compiled is not None

    registry = compile_governed_pack_registry(base_capability_registry(), compiled)
    capability = registry.require_available(DOCUMENTATION_GENERATION_CAPABILITY.id)
    assert (
        registry.require_available(PACK_CREATE_CAPABILITY.id).metadata["agent_control_plane"]
        is True
    )
    assert registry.require_available(PACK_DRAFT_CREATE_CAPABILITY.id).metadata["runtime_hidden"]
    full_ids = [
        activation.compiled_snapshot.foundation_pack_id,
        *activation.compiled_snapshot.functional_pack_ids,
        *activation.compiled_snapshot.style_pack_ids,
    ]

    assert capability.metadata["nexus_pack_ids"] == full_ids
    assert capability.metadata["nexus_capability_pack_ids"] == ["nexus.documentation"]
    assert "packs" not in capability.metadata["nexus_pack_context"]
    assert capability.metadata["nexus_pack_presentation"] == style.manifest.style.model_dump(
        mode="json"
    )
    manifest = build_runtime_capability_manifest(registry)
    assert "packs" not in manifest.application_context
    assert "audience" not in str(manifest.application_context)

    payload: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload.update(json.loads(request.read()))
        return httpx.Response(200, json={"status": "generation_triggered", "message": "started"})

    observation = await HarnessCapabilityExecutor(
        registry,
        docs_provider=McpDocumentationProvider(
            base_url="http://mcp.internal/mcp", transport=httpx.MockTransport(handler)
        ),
    ).execute(
        CapabilityExecutionRequest(
            capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
            arguments={"project_id": "runtime-style"},
        ),
        CapabilityExecutionContext(
            user_id=user.id,
            project_scope=["runtime-style"],
            permissions={"manage_projects"},
        ),
    )

    assert observation.succeeded
    assert payload["nexus_pack_ids"] == full_ids
    assert payload["nexus_pack_snapshot"] == activation.compiled_snapshot.model_dump(mode="json")
    assert payload["nexus_presentation"] == style.manifest.style.model_dump(mode="json")
    assert "nexus_private_analysis_packs" not in payload


def _supply_chain_artifact() -> PackArtifactV3:
    return PackArtifactV3.model_validate_json(
        json.dumps(
            {
                "schema_version": 3,
                "purpose": "Assess supply-chain disruption exposure.",
                "analyses": [
                    {
                        "id": "risk",
                        "label": "Disruption Risk",
                        "purpose": "Assess supplier continuity.",
                    }
                ],
                "evidence_requirements": [
                    {"id": "supplier", "label": "Supplier Evidence", "required": True, "weight": 80}
                ],
                "output_documents": [
                    {
                        "id": "supplier-risk",
                        "title": "Supplier Risk",
                        "sections": [{"id": "exposure", "heading": "Exposure", "order": 1}],
                    },
                    {
                        "id": "cold-chain",
                        "title": "Cold-Chain Readiness",
                        "sections": [
                            {"id": "controls", "heading": "Cold-Chain Controls", "order": 1}
                        ],
                    },
                    {
                        "id": "contingency",
                        "title": "Contingency Plan",
                        "sections": [
                            {"id": "actions", "heading": "Contingency Actions", "order": 1}
                        ],
                    },
                ],
                "scoring_dimensions": [{"id": "continuity", "label": "Continuity", "weight": 60}],
                "capability_requirements": [
                    {
                        "capability_id": "connector.procurement.read",
                        "required": False,
                        "purpose": "Supplement procurement evidence.",
                    }
                ],
                "audience": {
                    "label": "Cold-chain procurement audience",
                    "render": {"seniority": "manager", "detail_level": "detailed"},
                },
            }
        )
    )


async def test_private_analysis_activation_resolves_exact_artifact_and_reaches_provider(
    db_session,
) -> None:
    user = User(username="supply-chain-owner", email="supply-chain-owner@example.com")
    db_session.add(user)
    await db_session.flush()
    service = ProjectPackService(db_session, base_capability_registry())
    await service.activate(
        project_id="supply-chain",
        workflow_key="documentation.generate",
        user_id=user.id,
        active_pack_ids=["nexus.data-integration"],
    )
    artifact = _supply_chain_artifact()
    content_hash = hashlib.sha256(
        json.dumps(
            artifact.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    pack = HarnessPrivateAnalysisPackRow(
        pack_id="analysis.supply-chain",
        owner_user_id=user.id,
        name="Supply-Chain Disruption Assessment",
        created_at=datetime.datetime.now(datetime.UTC),
    )
    db_session.add(pack)
    await db_session.flush()
    version = HarnessPrivateAnalysisPackVersionRow(
        private_pack_id=pack.id,
        version="1.0.0",
        content_hash=content_hash,
        artifact=artifact.model_dump(mode="json"),
        receipt={},
        created_by_user_id=user.id,
        created_at=datetime.datetime.now(datetime.UTC),
    )
    db_session.add(version)
    await db_session.flush()
    base = await service._execution_snapshot_row("supply-chain", "documentation.generate")
    assert base is not None
    receipt = {
        "version_id": version.id,
        "pack_id": pack.pack_id,
        "version": version.version,
        "content_hash": content_hash,
        "audience_render": artifact.audience.render.model_dump(mode="json", exclude_none=True),
    }
    exact = HarnessPackActivationSnapshotRow(
        project_id=base.project_id,
        workflow_key=base.workflow_key,
        active_pack_ids=base.active_pack_ids,
        resolved_snapshot=base.resolved_snapshot,
        resolved_manifests=base.resolved_manifests,
        snapshot_hash="f" * 64,
        stack_selection=base.stack_selection,
        compiled_stack_snapshot=base.compiled_stack_snapshot,
        compiled_stack_hash=base.compiled_stack_hash,
        compiled_presentation=base.compiled_presentation,
        private_version_receipts=[receipt],
        created_by_user_id=user.id,
        created_at=datetime.datetime.now(datetime.UTC),
    )
    db_session.add(exact)
    await db_session.flush()
    activation = await service._activation_row("supply-chain", "documentation.generate")
    assert activation is not None
    activation.snapshot_id = exact.id
    await db_session.flush()

    compiled = await service.resolve_governed_for_execution(
        "supply-chain", "documentation.generate"
    )
    private = await service.resolve_private_analysis_for_execution(
        "supply-chain", "documentation.generate"
    )
    assert compiled is not None
    assert set(compiled.snapshot.functional_pack_ids) == {"nexus.data-integration"}
    assert [item.artifact.output_documents[0].id for item in private] == ["supplier-risk"]
    assert private[0].missing_optional_capability_ids == ("connector.procurement.read",)

    registry = compile_governed_pack_registry(
        base_capability_registry(), compiled, tuple(item.provider_payload() for item in private)
    )
    payload: dict[str, object] = {}
    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(
            lambda request: (
                payload.update(json.loads(request.read()))
                or httpx.Response(
                    200, json={"status": "generation_triggered", "message": "started"}
                )
            )
        ),
    )
    observation = await HarnessCapabilityExecutor(registry, docs_provider=provider).execute(
        CapabilityExecutionRequest(
            capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
            arguments={"project_id": "supply-chain"},
        ),
        CapabilityExecutionContext(
            user_id=user.id, project_scope=["supply-chain"], permissions={"manage_projects"}
        ),
    )
    assert observation.succeeded
    sent = payload["nexus_private_analysis_packs"]
    assert sent[0]["artifact"]["audience"]["label"] == "Cold-chain procurement audience"
    assert len(sent[0]["artifact"]["output_documents"]) == 3

    version.artifact["purpose"] = "Tampered artifact"
    await db_session.flush()
    with pytest.raises(PackServiceError, match="hash verification failed"):
        await service.resolve_private_analysis_for_execution(
            "supply-chain", "documentation.generate"
        )


def test_legacy_resolved_manifest_cannot_compile_into_deployment_runtime() -> None:
    legacy = PackManifest(
        schema_version=1,
        id="legacy.runtime",
        name="Legacy runtime",
        version="1.0.0",
        pack_type=PackType.DOMAIN,
    )
    resolved = ResolvedPackSet(
        manifests=(legacy,),
        snapshot=PackSnapshot(
            active_pack_ids=("legacy.runtime",),
            resolved_packs=(),
            snapshot_hash="a" * 64,
        ),
    )

    with pytest.raises(PackRuntimeError, match="Legacy Pack manifests"):
        compile_resolved_pack_registry(base_capability_registry(), resolved)
