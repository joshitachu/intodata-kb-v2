"""What the labs lane advertises to the model, by name.

The MCP tool name is derived from a capability's `semantic_name`, so that field
is not documentation -- it is the identifier the model reasons with. Two of them
were wrong in ways that produced wrong answers, and this is where the corrected
names are pinned.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.harness.registry import (
    base_capability_registry,
    default_capability_registry,
)
from src.harness.models import CapabilityDefinition, HarnessProviderType, HarnessRiskTier
from src.labs.mcp_bridge import (
    _DEFAULT_ALLOWLIST,
    _LABS_EXCLUDED_CAPABILITY_IDS,
    LabsToolNameCollision,
    _tool_descriptor,
    _tool_name_map,
    _visible_capabilities,
)
from src.labs.contracts import LabsTurnScope


def _allowlisted() -> list:
    registry = base_capability_registry()
    return [registry.get(capability_id) for capability_id in sorted(_DEFAULT_ALLOWLIST)]


def test_the_lane_advertises_a_whole_document_read() -> None:
    # Its absence is what forced every completeness question through similarity
    # chunks: neither existing path worked for an uploaded project.
    assert "rag.docs.read_document_text" in _DEFAULT_ALLOWLIST
    names = _tool_name_map(_allowlisted())
    assert names["read_document"] == "rag.docs.read_document_text"


def test_indexed_and_generated_document_tools_have_distinct_names() -> None:
    names = _tool_name_map(_allowlisted())

    assert names["search_documentation"] == "rag.docs.search_evidence"
    assert names["search_within_document"] == "rag.docs.read_document"
    assert names["read_document"] == "rag.docs.read_document_text"
    assert names["list_generated_documents"] == "mcp.docs.list_project_docs"
    assert names["read_generated_document"] == "mcp.docs.read_project_doc"
    assert (
        names["start_documentation_generation"]
        == "mcp.docs.generate_project_documentation"
    )


def test_every_tool_publishes_the_shared_observation_output_schema() -> None:
    for capability in _allowlisted():
        descriptor = _tool_descriptor(capability, capability.semantic_name)

        assert descriptor["outputSchema"]["required"] == ["capability_id", "status"]
        assert "status" in descriptor["outputSchema"]["properties"]


def test_the_lane_never_advertises_topology() -> None:
    capability_id = "topology.landscape.read_structure"

    assert capability_id not in _DEFAULT_ALLOWLIST
    assert capability_id in _LABS_EXCLUDED_CAPABILITY_IDS
    assert "read_topology_structure" not in _tool_name_map(_allowlisted())


def test_the_wildcard_override_cannot_restore_topology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LABS_CAPABILITY_ALLOWLIST", "*")
    runtime = SimpleNamespace(registry=base_capability_registry())
    scope = LabsTurnScope(
        user_id=1,
        conversation_id=1,
        user_groups=[],
        project_scope=["acme"],
        permissions=frozenset(),
        access_token=None,
        workspace_dir="/tmp/labs",
    )

    visible = _visible_capabilities(runtime, scope)

    assert "topology.landscape.read_structure" not in {
        capability.id for capability in visible
    }


def test_healthy_project_bound_connector_capabilities_do_not_need_static_ids() -> None:
    capability = CapabilityDefinition(
        id="connector.instance_42.connector_http_elastic_search",
        label="Search customer index",
        semantic_name="search_customer_index_connector_42",
        provider_type=HarnessProviderType.SOURCE,
        provider_id="nexus.connector",
        action="read_connector_evidence",
        input_schema={"type": "object", "properties": {}},
        output_type="evidence_items",
        risk_tier=HarnessRiskTier.READ_ONLY,
    )
    runtime = SimpleNamespace(
        registry=SimpleNamespace(list=lambda enabled_only=True: [capability])
    )
    scope = LabsTurnScope(
        user_id=1,
        conversation_id=1,
        user_groups=[],
        project_scope=["acme"],
        permissions=frozenset(),
        access_token=None,
        workspace_dir="/tmp/labs",
    )

    assert _visible_capabilities(runtime, scope) == [capability]


def test_the_within_document_search_no_longer_calls_itself_a_read() -> None:
    names = _tool_name_map(_allowlisted())

    assert names["search_within_document"] == "rag.docs.read_document"
    assert names["read_document"] == "rag.docs.read_document_text"


def test_the_capability_id_is_unchanged_so_history_stays_valid() -> None:
    # Only what the MODEL sees was renamed. The id is persisted in conversation
    # history, named in the executor's project-canonicalization set and in pack
    # fixtures; renaming it would invalidate all three.
    registry = base_capability_registry()

    assert registry.get("rag.docs.read_document").semantic_name == "search_within_document"


def test_no_two_advertised_capabilities_collide_on_a_tool_name() -> None:
    # A collision routes a call to the wrong executor binding, so the bridge
    # raises rather than shadowing one capability with another.
    try:
        _tool_name_map(_allowlisted())
    except LabsToolNameCollision as exc:  # pragma: no cover - the assertion is the point
        pytest.fail(str(exc))


def test_every_advertised_capability_still_exists() -> None:
    # The allowlist is a list of ids, so a renamed or removed capability would
    # otherwise silently shrink the catalogue by one tool.
    registry = base_capability_registry()
    known = {capability.id for capability in registry.list()}

    assert _DEFAULT_ALLOWLIST <= known


def test_every_advertised_capability_survives_the_active_pack_filter() -> None:
    """Registering a capability is not enough to make it reachable.

    The runtime builds from `default_capability_registry`, which compiles the
    base registry down to the capability ids named in the active pack fixtures.
    A capability absent from those fixtures is registered, handled, allowlisted
    -- and never advertised to any model, with nothing anywhere reporting the
    gap. That is exactly what happened to `rag.docs.read_document_text` on the
    first pass.
    """

    compiled = {capability.id for capability in default_capability_registry().list()}
    missing = sorted(_DEFAULT_ALLOWLIST - compiled)

    assert not missing, (
        "allowlisted but filtered out by the active packs: " + ", ".join(missing)
    )
