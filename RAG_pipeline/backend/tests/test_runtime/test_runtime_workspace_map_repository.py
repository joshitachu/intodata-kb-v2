from src.db.models import runtime_data  # noqa: F401
from src.runtime.store import RuntimeStoreRepository


async def test_workspace_project_map_crud_and_resolution(db_session):
    repo = RuntimeStoreRepository(db_session)
    source = await repo.create_source_config(project_id="default-project", source_type="mock", label="Mock")

    fallback_project, unmapped = await repo.resolve_project_id(source.id, "workspace-a")
    mapping = await repo.add_container_mapping(
        source_id=source.id,
        container_key="workspace-a",
        container_name="Workspace A",
        project_id="mapped-project",
        created_by="1",
    )
    mapped_project, mapped_unmapped = await repo.resolve_project_id(source.id, "workspace-a")
    mappings = await repo.list_mappings(source.id)
    await repo.delete_mapping(source.id, "workspace-a")
    deleted_project, deleted_unmapped = await repo.resolve_project_id(source.id, "workspace-a")

    assert fallback_project == "default-project"
    assert unmapped is True
    assert mapping.project_id == "mapped-project"
    assert mapped_project == "mapped-project"
    assert mapped_unmapped is False
    assert len(mappings) == 1
    assert deleted_project == "default-project"
    assert deleted_unmapped is True
