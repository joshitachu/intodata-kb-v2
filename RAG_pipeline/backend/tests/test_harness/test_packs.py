import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.harness.packs import (
    FIXTURE_PACK_DIRECTORY,
    PackActivation,
    PackCatalog,
    PackCatalogError,
    PackLoadError,
    PackManifest,
    PackReferenceSets,
    PackType,
    load_pack_directory,
    load_pack_file,
    manifest_hash,
)
from src.config import settings
from src.harness.registry import default_capability_registry
from src.harness.executor import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    HarnessCapabilityExecutor,
)


ALLOWED_REFERENCES = PackReferenceSets(
    capability_ids=frozenset(
        {
            "mcp.docs.generate_project_documentation",
            "mcp.docs.get_generation_status",
            "mcp.docs.list_project_docs",
            "mcp.docs.read_project_doc",
            "nexus.docs.list_documentation_packs",
            "nexus.workflows.cancel",
            "rag.docs.search_evidence",
            "rag.docs.answer_question",
            "rag.docs.read_document",
            "rag.docs.read_document_text",
            "nexus.metadata.query",
            "nexus.conversations.read_summary_facts",
            "mcp.docs.update_from_evidence",
            "mcp.docs.revert_evidence_update",
            "nexus.workflow.operational_investigation",
            "runtime.tools.query_evidence",
            "runtime.stored.query_evidence",
            "runtime.logs.search_evidence",
            "external.connector.read_evidence",
            "nexus.workflow.onboard_project",
            "mcp.projects.list_source_options",
            "mcp.projects.attach_git_source",
            "mcp.projects.attach_uploaded_zip_source",
            "mcp.projects.attach_uploaded_zip_source",
            "mcp.projects.trigger_scan",
            "mcp.projects.read_status",
            "mcp.projects.list_active_workflows",
            "mcp.projects.publish_index",
            "topology.landscape.read_structure",
        }
    ),
    connector_provider_keys=frozenset({"fixture_knowledge"}),
    documentation_provider_refs=frozenset({"mcp.docs.default"}),
    evidence_types=frozenset({"document", "runtime_incident", "connector_item"}),
    source_kinds=frozenset({"documentation", "runtime", "connector"}),
)


def fixture_catalog() -> PackCatalog:
    return PackCatalog.from_directory(
        FIXTURE_PACK_DIRECTORY,
        allowed_references=ALLOWED_REFERENCES,
    )


def minimal_manifest(pack_id: str, *, includes: tuple[str, ...] = ()) -> PackManifest:
    return PackManifest(
        id=pack_id,
        name=pack_id,
        version="1.0.0",
        pack_type=PackType.DOMAIN,
        includes=includes,
    )


def validate_json_payload(payload: dict) -> PackManifest:
    return PackManifest.model_validate_json(json.dumps(payload), strict=True)


def test_fixture_manifests_load_and_compose_documentation_provider_by_reference():
    manifests = load_pack_directory(FIXTURE_PACK_DIRECTORY)
    assert [manifest.id for manifest in manifests] == [
        "nexus.data-integration",
        "nexus.documentation",
    ]

    resolved = fixture_catalog().resolve(
        PackActivation(enabled_pack_ids=frozenset({"nexus.data-integration"}))
    )
    assert [manifest.id for manifest in resolved.manifests] == [
        "nexus.data-integration",
        "nexus.documentation",
    ]
    documentation = next(
        manifest for manifest in resolved.manifests if manifest.id == "nexus.documentation"
    )
    assert documentation.documentation_provider_ref == "mcp.docs.default"
    assert "endpoint" not in documentation.model_dump_json()


def test_activation_is_separate_and_changes_resolved_workflow_options():
    catalog = fixture_catalog()
    documentation = catalog.resolve(
        PackActivation(enabled_pack_ids=frozenset({"nexus.documentation"}))
    )
    integration = catalog.resolve(
        PackActivation(enabled_pack_ids=frozenset({"nexus.data-integration"}))
    )

    documentation_labels = {
        workflow.label for manifest in documentation.manifests for workflow in manifest.workflows
    }
    integration_labels = {
        workflow.label for manifest in integration.manifests for workflow in manifest.workflows
    }
    assert "Investigate integration failure" not in documentation_labels
    assert "Investigate integration failure" in integration_labels
    assert documentation.snapshot.active_pack_ids == ("nexus.documentation",)
    assert integration.snapshot.active_pack_ids == ("nexus.data-integration",)


def test_hashes_and_snapshots_are_deterministic_across_input_order():
    manifests = load_pack_directory(FIXTURE_PACK_DIRECTORY)
    forward = PackCatalog(manifests, allowed_references=ALLOWED_REFERENCES)
    reverse = PackCatalog(reversed(manifests), allowed_references=ALLOWED_REFERENCES)
    activation = PackActivation(
        enabled_pack_ids=frozenset({"nexus.documentation", "nexus.data-integration"})
    )

    first = forward.resolve(activation).snapshot
    second = reverse.resolve(activation).snapshot
    assert first == second
    assert first.snapshot_hash == forward.resolve(activation).snapshot.snapshot_hash
    assert all(len(item.content_hash) == 64 for item in first.resolved_packs)


def test_manifest_hash_uses_content_not_source_format(tmp_path: Path):
    original = load_pack_file(FIXTURE_PACK_DIRECTORY / "documentation.json")
    reformatted = tmp_path / "reformatted.json"
    reformatted.write_text(json.dumps(original.model_dump(mode="json"), indent=8), encoding="utf-8")
    assert manifest_hash(load_pack_file(reformatted)) == manifest_hash(original)


@pytest.mark.parametrize(
    "path", ["/etc/passwd", "../secret.md", "docs\\secret.md", "docs//secret.md", "."]
)
def test_artifact_paths_must_be_safe_and_relative(path: str):
    payload = minimal_manifest("test.path").model_dump(mode="json")
    payload["artifact_templates"] = [
        {
            "id": "test.artifact",
            "label": "Artifact",
            "relative_path": path,
            "media_type": "text/markdown",
        }
    ]
    with pytest.raises(ValidationError, match="artifact path"):
        validate_json_payload(payload)


@pytest.mark.parametrize("field", ["imports", "credentials", "endpoints", "executable_code"])
def test_strict_schema_rejects_forbidden_declaration_fields(field: str):
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PackManifest.model_validate(
            {
                **minimal_manifest("test.extra").model_dump(mode="python"),
                field: ["forbidden"],
            }
        )


def test_strict_schema_rejects_coerced_fields():
    payload = minimal_manifest("test.strict").model_dump(mode="python")
    payload["schema_version"] = "1"
    with pytest.raises(ValidationError):
        PackManifest.model_validate(payload)


def test_loader_rejects_invalid_json_and_non_json_files(tmp_path: Path):
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not json", encoding="utf-8")
    with pytest.raises(PackLoadError, match="invalid pack file"):
        load_pack_file(invalid)

    yaml_file = tmp_path / "pack.yaml"
    yaml_file.write_text("id: test.yaml", encoding="utf-8")
    with pytest.raises(PackLoadError, match="unsupported pack format"):
        load_pack_file(yaml_file)


def test_directory_loader_rejects_symlinked_pack_files(tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_text("{}", encoding="utf-8")
    (tmp_path / "linked.json").symlink_to(source)
    with pytest.raises(PackLoadError, match="symbolic link.*linked.json"):
        load_pack_directory(tmp_path)


def test_unknown_includes_and_cycles_fail_with_useful_errors():
    with pytest.raises(PackCatalogError, match="includes unknown pack.*missing"):
        PackCatalog([minimal_manifest("test.root", includes=("test.missing",))])

    first = minimal_manifest("test.first", includes=("test.second",))
    second = minimal_manifest("test.second", includes=("test.first",))
    with pytest.raises(
        PackCatalogError, match=r"include cycle: test.first -> test.second -> test.first"
    ):
        PackCatalog([second, first])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("documentation_provider_ref", "mcp.docs.missing", "documentation provider"),
        ("workflow_capability", "capability.missing", "capability"),
        ("connector_provider", "provider_missing", "connector provider"),
    ],
)
def test_passed_allowed_sets_reject_unknown_external_references(
    field: str,
    value: str,
    message: str,
):
    payload = minimal_manifest("test.references").model_dump(mode="json")
    if field == "documentation_provider_ref":
        payload[field] = value
    elif field == "workflow_capability":
        payload["workflows"] = [
            {
                "id": "test.workflow",
                "label": "Test",
                "description": "Test workflow",
                "capability_ids": [value],
                "entity_type_ids": [],
                "connector_requirement_ids": [],
                "artifact_template_ids": [],
            }
        ]
    else:
        payload["connector_requirements"] = [
            {
                "id": "test.connector",
                "provider_key": value,
                "capability_ids": [],
                "required": True,
            }
        ]
    with pytest.raises(PackCatalogError, match=message):
        PackCatalog([validate_json_payload(payload)], allowed_references=ALLOWED_REFERENCES)


def test_resolved_workflow_references_must_exist_in_active_include_closure():
    payload = minimal_manifest("test.workflow-pack").model_dump(mode="json")
    payload["workflows"] = [
        {
            "id": "test.workflow",
            "label": "Test",
            "description": "Test workflow",
            "capability_ids": [],
            "entity_type_ids": ["test.missing-entity"],
            "connector_requirement_ids": [],
            "artifact_template_ids": [],
        }
    ]
    with pytest.raises(PackCatalogError, match="unknown entity reference.*test.missing-entity"):
        PackCatalog([validate_json_payload(payload)])


def test_catalog_views_are_sorted_safe_and_do_not_expose_policy_details():
    views = fixture_catalog().views()
    assert [view.id for view in views] == ["nexus.data-integration", "nexus.documentation"]
    assert views[0].workflow_labels == (
        "Onboard project source",
        "Investigate integration failure",
        "Explore integration topology",
    )
    dumped = views[0].model_dump(mode="json")
    assert "policy_hints" not in dumped
    assert "connector_requirements" not in dumped


def test_unknown_active_pack_is_rejected():
    with pytest.raises(PackCatalogError, match="unknown active pack.*test.missing"):
        fixture_catalog().resolve(PackActivation(enabled_pack_ids=frozenset({"test.missing"})))


def test_swapping_active_pack_changes_executable_capabilities_without_code(monkeypatch):
    monkeypatch.setattr(settings, "nexus_active_packs", "nexus.documentation")

    capability_ids = {
        item.id for item in default_capability_registry().list(enabled_only=True)
    }

    assert "mcp.docs.generate_project_documentation" in capability_ids
    assert "nexus.workflow.operational_investigation" not in capability_ids
    assert "nexus.workflow.onboard_project" not in capability_ids


async def test_pack_disabled_capability_is_denied_by_executor_too(monkeypatch):
    monkeypatch.setattr(settings, "nexus_active_packs", "nexus.documentation")
    registry = default_capability_registry()

    observation = await HarnessCapabilityExecutor(registry).execute(
        CapabilityExecutionRequest(
            capability_id="nexus.workflow.operational_investigation",
            arguments={"question": "why failed", "project_id": "project-a"},
        ),
        CapabilityExecutionContext(user_id=1, project_scope=["project-a"]),
    )

    assert observation.status == "denied"
    assert "Unknown capability" in (observation.error_message or "")
