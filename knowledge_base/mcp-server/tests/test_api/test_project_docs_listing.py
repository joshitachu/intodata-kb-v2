"""Doc listing / preview / download across flat and partitioned projects.

Regression: the viewer listed zero documents for a partitioned project (the
picker, fed by the RAG document count, said 81) because the endpoint globbed the
flat ``output/{pid}/docs`` directory that a composite upload never creates.
"""

from __future__ import annotations

from mcp_server.config import config
from mcp_server.services.project_registry import ProjectRegistry

DOC_NAMES = ["00_AGENT_BRIEF.md", "09_EXECUTIVE_SUMMARY.md"]


async def _register(session_factory, project_id: str) -> None:
    async with session_factory() as session:
        await ProjectRegistry(session).add_project(
            project_id,
            str(config.project_root / "repos" / project_id),
            platform="talend",
            source_type="local",
        )
        await session.commit()


def _write_docs(project_id: str, subproject: str | None, *, source: str = "docs") -> None:
    base = config.project_root / "output" / project_id
    docs_dir = (base / subproject / source) if subproject else (base / source)
    docs_dir.mkdir(parents=True, exist_ok=True)
    for name in DOC_NAMES:
        marker = subproject or "root"
        (docs_dir / name).write_text(f"# {marker} {name}\n", encoding="utf-8")


async def test_flat_project_lists_docs_with_no_subprojects(client, session_factory):
    await _register(session_factory, "flatproj")
    _write_docs("flatproj", None)

    body = (await client.get("/mcp/projects/flatproj/docs")).json()

    assert body["total"] == 2
    assert body["subprojects"] == []
    assert all(d["subproject"] is None for d in body["docs"])


async def test_partitioned_project_lists_every_subprojects_docs(client, session_factory):
    await _register(session_factory, "werk")
    for sub in ("VQ_MQ2RHM", "Assetcheck"):
        _write_docs("werk", sub)

    body = (await client.get("/mcp/projects/werk/docs")).json()

    # The defect returned total=0 here while the picker advertised the RAG count.
    assert body["total"] == 4
    assert body["subprojects"] == ["Assetcheck", "VQ_MQ2RHM"]
    assert {(d["subproject"], d["name"]) for d in body["docs"]} == {
        (sub, name) for sub in ("Assetcheck", "VQ_MQ2RHM") for name in DOC_NAMES
    }


async def test_staging_source_is_listed_per_subproject(client, session_factory):
    await _register(session_factory, "werk")
    _write_docs("werk", "ETTM", source="docs_staging")

    body = (await client.get("/mcp/projects/werk/docs?source=staging")).json()

    assert body["subprojects"] == ["ETTM"]
    assert body["total"] == 2


async def test_download_resolves_the_named_subproject(client, session_factory):
    await _register(session_factory, "werk")
    for sub in ("VQ_MQ2RHM", "Assetcheck"):
        _write_docs("werk", sub)

    res = await client.get(
        "/mcp/projects/werk/docs/00_AGENT_BRIEF.md?subproject=VQ_MQ2RHM"
    )

    assert res.status_code == 200
    # Every sub-project holds a file of this name; the right one must come back.
    assert "VQ_MQ2RHM" in res.text


async def test_download_without_a_subproject_is_refused_when_partitioned(
    client, session_factory
):
    await _register(session_factory, "werk")
    _write_docs("werk", "Assetcheck")

    res = await client.get("/mcp/projects/werk/docs/00_AGENT_BRIEF.md")

    # Silently serving an arbitrary partition's copy is the failure mode to avoid.
    assert res.status_code == 404


async def test_download_stays_unqualified_for_a_flat_project(client, session_factory):
    await _register(session_factory, "flatproj")
    _write_docs("flatproj", None)

    res = await client.get("/mcp/projects/flatproj/docs/00_AGENT_BRIEF.md")

    assert res.status_code == 200
    assert "root" in res.text


async def test_download_rejects_an_unknown_subproject(client, session_factory):
    await _register(session_factory, "werk")
    _write_docs("werk", "Assetcheck")

    res = await client.get(
        "/mcp/projects/werk/docs/00_AGENT_BRIEF.md?subproject=NotAPartition"
    )

    assert res.status_code == 404


async def test_content_preview_resolves_the_named_subproject(client, session_factory):
    await _register(session_factory, "werk")
    for sub in ("VQ_MQ2RHM", "Assetcheck"):
        _write_docs("werk", sub)

    res = await client.get(
        "/mcp/projects/werk/docs/00_AGENT_BRIEF.md/content?subproject=Assetcheck"
    )

    assert res.status_code == 200
    body = res.json()
    assert body["subproject"] == "Assetcheck"
    assert "Assetcheck" in body["content"]


async def test_content_preview_rejects_traversal(client, session_factory):
    await _register(session_factory, "werk")
    _write_docs("werk", "Assetcheck")

    res = await client.get(
        "/mcp/projects/werk/docs/00_AGENT_BRIEF.md/content?subproject=../../etc"
    )

    assert res.status_code in (400, 404)
