"""API boundary for the generation dry run (GET /projects/{id}/generation-plan).

The endpoint's job is to answer honestly and never to block: an operator who
opens the generate dialog must be told what the run will cost, and a project
with no scan, a corrupt scan, or a single sub-project must still get a usable
dialog rather than an error.
"""

from __future__ import annotations

import json
from pathlib import Path

ZIP = ".__intodoc_zip__"


async def _register_zip_project(client, project_id: str) -> None:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("source.txt", "content")
    buffer.seek(0)
    response = await client.post(
        "/mcp/upload-zip",
        files={"file": ("initial.zip", buffer, "application/zip")},
        data={"project_id": project_id, "platform": "talend"},
    )
    assert response.status_code == 200


def _write_scan(project_id: str, units: list[tuple[str, str, str]]) -> None:
    from mcp_server.config import config

    scan_dir = config.intodoc_output_dir / project_id
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / "scan_result.json").write_text(
        json.dumps({
            "platform": "talend",
            "jobs": [
                {
                    "name": name,
                    "job_id": name,
                    "path": f"process/{name}.item",
                    "group": group,
                    "source_root": source_root,
                    "components": [],
                }
                for name, group, source_root in units
            ],
        }),
        encoding="utf-8",
    )


def _publish_docs(project_id: str, subdir: str, count: int = 2) -> Path:
    from mcp_server.config import config

    docs = config.project_root / "output" / project_id / subdir / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        (docs / f"0{index}_DOC.md").write_text(f"# doc {index}\n", encoding="utf-8")
    return docs


async def test_plan_for_a_project_with_no_scan_degrades(client) -> None:
    await _register_zip_project(client, "plan-noscan")

    response = await client.get("/mcp/projects/plan-noscan/generation-plan")

    assert response.status_code == 200
    body = response.json()
    assert body["scan_available"] is False
    assert body["composite"] is False
    assert body["partitions"] == []
    assert body["will_generate"] == 1
    assert body["will_reuse"] == 0
    assert body["global_invalidation"] is None


async def test_plan_for_a_corrupt_scan_degrades(client) -> None:
    from mcp_server.config import config

    await _register_zip_project(client, "plan-corrupt")
    scan_dir = config.intodoc_output_dir / "plan-corrupt"
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / "scan_result.json").write_text("{ not json", encoding="utf-8")

    response = await client.get("/mcp/projects/plan-corrupt/generation-plan")

    assert response.status_code == 200
    assert response.json()["scan_available"] is False


async def test_plan_for_a_single_sub_project_hides_the_preview(client) -> None:
    await _register_zip_project(client, "plan-solo")
    _write_scan("plan-solo", [("u1", "Flows", "bundle/ROOT")])

    body = (await client.get("/mcp/projects/plan-solo/generation-plan")).json()

    assert body["scan_available"] is True
    assert body["composite"] is False
    assert body["partitions"] == []
    assert body["partition_count"] == 1
    assert body["will_generate"] == 1


async def test_plan_for_a_composite_project_lists_every_sub_project(
    client, monkeypatch
) -> None:
    from mcp_server.services import partition_orchestrator as po

    await _register_zip_project(client, "plan-multi")
    _write_scan(
        "plan-multi",
        [
            ("a1", "Flows", f"B/Alpha{ZIP}/A"),
            ("a2", "Logging", f"B/Alpha{ZIP}/REFERENCES"),
            ("b1", "Flows", f"B/Beta{ZIP}/B"),
        ],
    )
    # Digests are stubbed: this test is about the endpoint's contract, not the
    # fingerprinter (which has its own suite).
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-1", "Beta": "b-1"}
    )
    # Alpha is up to date: recorded digest matches AND docs are on disk.
    _publish_docs("plan-multi", "Alpha")
    po._write_partition_state(
        "plan-multi",
        {
            "Alpha": {
                "partition_fingerprint": "a-1",
                "doc_count": 2,
                "generated_at": "2026-08-01T10:00:00+00:00",
            }
        },
    )

    body = (await client.get("/mcp/projects/plan-multi/generation-plan")).json()

    assert body["scan_available"] is True
    assert body["composite"] is True
    assert body["partition_count"] == 2
    assert body["will_generate"] == 1
    assert body["will_reuse"] == 1
    by_name = {p["name"]: p for p in body["partitions"]}
    assert by_name["Alpha"]["action"] == "reuse"
    assert by_name["Alpha"]["reason"] == "unchanged"
    assert by_name["Alpha"]["doc_count"] == 2
    assert by_name["Alpha"]["last_generated_at"] == "2026-08-01T10:00:00+00:00"
    assert by_name["Alpha"]["unit_count"] == 2
    assert by_name["Beta"]["action"] == "generate"
    assert by_name["Beta"]["reason"] == "new"
    assert by_name["Beta"]["subdir"] == "Beta"
    assert by_name["Beta"]["last_generated_at"] is None
    assert any("Cross-project references" in note for note in body["notes"])


async def test_plan_names_a_shared_routine_change(client, monkeypatch) -> None:
    from mcp_server.services import partition_orchestrator as po

    await _register_zip_project(client, "plan-shared")
    _write_scan(
        "plan-shared",
        [("a1", "Flows", f"B/Alpha{ZIP}/A"), ("b1", "Flows", f"B/Beta{ZIP}/B")],
    )
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-2", "Beta": "b-2"}
    )
    monkeypatch.setattr(po, "_shared_artifact_digest", lambda d: "routines-2")
    _publish_docs("plan-shared", "Alpha")
    _publish_docs("plan-shared", "Beta")
    po._write_partition_state(
        "plan-shared",
        {
            "Alpha": {"partition_fingerprint": "a-1"},
            "Beta": {"partition_fingerprint": "b-1"},
            po.STATE_META_KEY: {"shared_artifact_digest": "routines-1"},
        },
    )

    body = (await client.get("/mcp/projects/plan-shared/generation-plan")).json()

    assert body["global_invalidation"]["reason"] == "shared_routine_changed"
    assert "routine" in body["global_invalidation"]["detail"].lower()
    assert body["will_generate"] == 2
    assert {p["reason"] for p in body["partitions"]} == {"shared_artifact_changed"}


async def test_plan_previews_flat_layout_adoption(client, monkeypatch) -> None:
    from mcp_server.config import config
    from mcp_server.services import partition_orchestrator as po

    await _register_zip_project(client, "plan-adopt")
    _write_scan(
        "plan-adopt",
        [("a1", "Flows", f"B/Alpha{ZIP}/A"), ("b1", "Flows", f"B/Beta{ZIP}/B")],
    )
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-1", "Beta": "b-1"}
    )
    # Documentation published before the project became composite.
    flat = config.project_root / "output" / "plan-adopt" / "docs"
    flat.mkdir(parents=True)
    (flat / "00_DOC.md").write_text("# flat\n", encoding="utf-8")
    po.record_flat_source_migration("plan-adopt", "Alpha")

    body = (await client.get("/mcp/projects/plan-adopt/generation-plan")).json()

    by_name = {p["name"]: p for p in body["partitions"]}
    assert by_name["Alpha"]["action"] == "reuse"
    assert by_name["Alpha"]["reason"] == "flat_layout_adoption"
    assert by_name["Alpha"]["doc_count"] == 1
    assert body["will_reuse"] == 1
    assert any("adopted" in note for note in body["notes"])
    # A dry run must not move anything.
    assert flat.exists()


async def test_plan_is_read_only(client, monkeypatch) -> None:
    """The dry run must not write durable state a run would rely on."""
    from mcp_server.services import partition_orchestrator as po

    await _register_zip_project(client, "plan-readonly")
    _write_scan(
        "plan-readonly",
        [("a1", "Flows", f"B/Alpha{ZIP}/A"), ("b1", "Flows", f"B/Beta{ZIP}/B")],
    )
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-1", "Beta": "b-1"}
    )

    assert (await client.get("/mcp/projects/plan-readonly/generation-plan")).status_code == 200

    assert not po._partition_state_path("plan-readonly").exists()


async def test_plan_404s_for_an_unknown_project(client) -> None:
    response = await client.get("/mcp/projects/does-not-exist/generation-plan")
    assert response.status_code == 404
