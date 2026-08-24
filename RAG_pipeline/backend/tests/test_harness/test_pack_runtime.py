from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from src.harness.executor import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    HarnessCapabilityExecutor,
)
from src.harness.models import DOCUMENTATION_GENERATION_CAPABILITY
from src.harness.packs.models import PackManifest, PackSnapshot, PackType, ResolvedPackSet
from src.harness.packs.runtime import PackRuntimeError, compile_resolved_pack_registry
from src.harness.providers.mcp_docs import McpDocumentationProvider
from src.harness.registry import base_capability_registry


async def test_fixture_allowlist_never_forwards_a_second_pack_stack() -> None:
    registry = compile_resolved_pack_registry(
        base_capability_registry(),
        ResolvedPackSet(
            manifests=(
                PackManifest.model_validate_json(
                    (Path(__file__).parents[2] / "src/harness/packs/fixtures/documentation.json").read_text(
                        encoding="utf-8"
                    )
                ),
            ),
            snapshot=PackSnapshot(active_pack_ids=("nexus.documentation",), resolved_packs=(), snapshot_hash="a" * 64),
        ),
    )
    capability = registry.require_available(DOCUMENTATION_GENERATION_CAPABILITY.id)
    assert "nexus_pack_ids" not in capability.metadata
    assert "nexus_pack_snapshot" not in capability.metadata

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
        CapabilityExecutionContext(user_id=1, project_scope=["runtime-style"], permissions={"manage_projects"}),
    )

    assert observation.succeeded
    assert payload == {"project_id": "runtime-style", "resume": True}


def test_legacy_resolved_manifest_cannot_compile_into_fixture_runtime() -> None:
    legacy = PackManifest(schema_version=1, id="legacy.runtime", name="Legacy runtime", version="1.0.0", pack_type=PackType.DOMAIN)
    resolved = ResolvedPackSet(
        manifests=(legacy,),
        snapshot=PackSnapshot(active_pack_ids=("legacy.runtime",), resolved_packs=(), snapshot_hash="a" * 64),
    )

    with pytest.raises(PackRuntimeError, match="Legacy Pack manifests"):
        compile_resolved_pack_registry(base_capability_registry(), resolved)
