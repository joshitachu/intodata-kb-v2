"""Tests for plugin pack branch registry helpers and routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from sqlalchemy import select, text

from mcp_server.api.routes import plugins as plugins_mod
from mcp_server.db.models import PluginPack
from mcp_server.services.pack_governance import PackGovernanceDecision


def _write_pack(
    path: Path,
    *,
    name: str = "starter",
    version: str = "1.0.0",
    certification: str | None = None,
) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "plugin.yaml").write_text(
        "\n".join(
            [
                f"name: {name}",
                f"version: {version}",
                "description: Test pack",
                "steps:",
                "  - id: generate-docs",
                "    type: harness",
                "",
            ]
            + (["certification:", f"  provenance: {certification}"] if certification else [])
        ),
        encoding="utf-8",
    )


def test_parse_remote_branch_refs_skips_head_and_preserves_slashes():
    refs = plugins_mod._parse_remote_branch_refs(
        "HEAD\t111\n"
        "main\t222\n"
        "feature/single-summary\t333\n"
        "bad-line\n"
    )

    assert refs == [
        ("main", "222"),
        ("feature/single-summary", "333"),
    ]


def test_list_editable_pack_files_includes_agent_skill_but_skips_git(tmp_path):
    pack_dir = tmp_path / "pack"
    _write_pack(pack_dir)
    (pack_dir / ".codex" / "skills" / "tune").mkdir(parents=True)
    (pack_dir / ".codex" / "skills" / "tune" / "SKILL.md").write_text("x", encoding="utf-8")
    (pack_dir / ".git").mkdir()
    (pack_dir / ".git" / "config").write_text("x", encoding="utf-8")

    assert plugins_mod._list_editable_pack_files(pack_dir) == [
        ".codex/skills/tune/SKILL.md",
        "plugin.yaml",
    ]


async def test_register_github_branch_stores_branch_metadata(client, monkeypatch, tmp_path):
    async def fake_clone(repo_url: str, repo_branch: str):
        pack_dir = tmp_path / "clone" / repo_branch
        _write_pack(pack_dir, name="swap-pack-starter", version="0.2.0")
        return pack_dir, "a" * 40

    monkeypatch.setattr(plugins_mod, "_clone_pack", fake_clone)

    resp = await client.post(
        "/mcp/plugins",
        json={
            "repo_url": "https://github.com/example/swap-pack-starter.git",
            "repo_branch": "single-summary-doc",
        },
    )

    assert resp.status_code == 409
    assert "trusted Pack source" in resp.json()["detail"]


async def test_configured_git_registration_returns_certified_catalog_entry(client, monkeypatch, tmp_path):
    from mcp_server.config import config

    repo_url = "https://git.example.test/platform/packs.git"
    monkeypatch.setattr(
        config,
        "pack_governance_trusted_sources_json",
        '[{"kind":"git","repo_url":"https://git.example.test/platform/packs.git",'
        '"refs":["main"],"provenance":"release"}]',
    )

    async def fake_clone(_repo_url: str, repo_branch: str):
        pack_dir = tmp_path / "clone" / repo_branch
        _write_pack(pack_dir, name="governed-pack", certification="release")
        return pack_dir, "a" * 40

    async def fake_certify(**_kwargs):
        return PackGovernanceDecision(
            True, True, "git", False, provenance="release", content_sha256="b" * 64
        )

    monkeypatch.setattr(plugins_mod, "_clone_pack", fake_clone)
    monkeypatch.setattr(plugins_mod.pack_governance, "certify_new_git_pack", fake_certify)
    monkeypatch.setattr(
        plugins_mod.pack_governance,
        "evaluate",
        lambda _pack: fake_certify(),
    )

    response = await client.post("/mcp/plugins", json={"repo_url": repo_url, "repo_branch": "main"})

    assert response.status_code == 201
    body = response.json()
    assert body["certification_status"] == "certified"
    assert body["origin"] == "git"
    assert "path" not in body


async def test_catalogue_does_not_report_blocked_persisted_certificate_as_certified(
    client, monkeypatch, tmp_path
):
    from mcp_server.config import config

    pack_dir = tmp_path / "blocked-pack"
    _write_pack(pack_dir)
    monkeypatch.setattr(
        config,
        "pack_governance_trusted_sources_json",
        '[{"kind":"bundled","manifest_name":"governed-pack",'
        '"provenance":"release","allow_legacy_migration":true}]',
    )
    async with plugins_mod.async_session_factory() as db:
        db.add(
            PluginPack(
                name="blocked-pack",
                manifest_name="governed-pack",
                version="1.0.0",
                path=str(pack_dir),
                auto_update=False,
                origin="bundled",
                certification_status="certified",
                certification_provenance="release",
                content_sha256="0" * 64,
            )
        )
        await db.commit()

    response = await client.get("/mcp/plugins")

    assert response.status_code == 200
    blocked = next(item for item in response.json() if item["name"] == "blocked-pack")
    assert blocked["certification_status"] != "certified"


async def test_discover_marks_registered_branch(client, monkeypatch, tmp_path):
    from mcp_server.config import config

    async def fake_discover(repo_url: str):
        return [
            plugins_mod.PluginPackBranchOut(
                branch="single-summary-doc",
                name="single-summary-doc",
                manifest_name="swap-pack-starter",
                version="0.2.0",
                description="Test pack",
                repo_url=repo_url,
                commit_sha="b" * 40,
            )
        ]

    monkeypatch.setattr(plugins_mod, "_discover_github_pack_branches", fake_discover)
    monkeypatch.setattr(
        config,
        "pack_governance_trusted_sources_json",
        '[{"kind":"git","repo_url":"https://github.com/example/swap-pack-starter",'
        '"refs":["single-summary-doc"],"provenance":"release"}]',
    )

    async with plugins_mod.async_session_factory() as db:
        db.add(PluginPack(
            name="single-summary-doc",
            manifest_name="swap-pack-starter",
            version="0.2.0",
            path=str(tmp_path / "legacy-pack"),
            repo_url="https://github.com/example/swap-pack-starter.git",
            repo_branch="single-summary-doc",
            last_commit_sha="b" * 40,
            auto_update=True,
        ))
        await db.commit()
        pack_id = (await db.execute(select(PluginPack.id))).scalar_one()

    discover_resp = await client.post(
        "/mcp/plugins/discover",
        json={"repo_url": "https://github.com/example/swap-pack-starter"},
    )

    assert discover_resp.status_code == 200
    branch = discover_resp.json()["branches"][0]
    assert branch["registered"] is True
    assert branch["registered_pack_id"] == pack_id


async def test_local_registration_is_forbidden(client):
    response = await client.post("/mcp/plugins", json={"path": "/tmp/pack"})
    assert response.status_code == 403
    assert "Git/PR" in response.json()["detail"]  # shared packs come from git only


async def test_unconfigured_remote_is_rejected_before_clone(client, monkeypatch):
    async def should_not_clone(*_args, **_kwargs):
        raise AssertionError("untrusted source must not be cloned")

    monkeypatch.setattr(plugins_mod, "_clone_pack", should_not_clone)
    response = await client.post(
        "/mcp/plugins",
        json={"repo_url": "https://git.example.test/packs.git", "repo_branch": "main"},
    )
    assert response.status_code == 409
    assert "trusted Pack source" in response.json()["detail"]


async def test_unconfigured_branch_preview_is_rejected_before_clone(client, monkeypatch):
    async def should_not_clone(*_args, **_kwargs):
        raise AssertionError("untrusted preview must not be cloned")

    monkeypatch.setattr(plugins_mod, "_clone_pack_branch_to", should_not_clone)
    response = await client.post(
        "/mcp/plugins/branch-files",
        json={"repo_url": "https://git.example.test/packs.git", "repo_branch": "main"},
    )
    assert response.status_code == 409


async def _add_project(client, project_id: str) -> None:
    response = await client.post(
        "/mcp/add-repo",
        json={"url": "zip", "project_id": project_id, "platform": "talend"},
    )
    assert response.status_code == 200


async def _add_untrusted_pack(path: Path) -> int:
    _write_pack(path, name="legacy-pack")
    async with plugins_mod.async_session_factory() as db:
        pack = PluginPack(
            name="legacy-pack",
            manifest_name="legacy-pack",
            version="1.0.0",
            path=str(path),
            description="legacy",
            auto_update=False,
            origin="legacy",
            certification_status="legacy_untrusted",
        )
        db.add(pack)
        await db.commit()
        await db.refresh(pack)
        return pack.id


async def test_assignment_rejects_untrusted_pack(client, tmp_path):
    await _add_project(client, "pack-assignment")
    pack_id = await _add_untrusted_pack(tmp_path / "legacy-pack")

    response = await client.patch(
        "/mcp/projects/pack-assignment/plugin", json={"plugin_pack_id": pack_id}
    )
    assert response.status_code == 409
    assert "not certified" in response.json()["detail"]


async def test_generation_rejects_untrusted_override(client, tmp_path):
    await _add_project(client, "pack-generation")
    pack_id = await _add_untrusted_pack(tmp_path / "legacy-generation-pack")

    response = await client.post(
        "/mcp/generate", json={"project_id": "pack-generation", "plugin_pack_id": pack_id}
    )
    assert response.status_code == 409
    assert "not certified" in response.json()["detail"]


async def test_project_member_with_access_can_select_pack_and_outsider_cannot(client):
    """Selecting a project's plugin pack requires project access, not ownership.

    A non-owner user assigned to the project may set the selection (plugin_pack_id
    is None here so the certification gate is not exercised); a user with no
    assignment is still rejected with 403.
    """
    await _add_project(client, "member-select")

    async with plugins_mod.async_session_factory() as db:
        await db.execute(text(
            "INSERT INTO users (id, username, is_superadmin) VALUES (2, 'member', 0)"
        ))
        await db.execute(text(
            "INSERT INTO users (id, username, is_superadmin) VALUES (3, 'outsider', 0)"
        ))
        await db.execute(text(
            "INSERT INTO project_assignments (user_id, project_id, is_owner) "
            "VALUES (2, 'member-select', 0)"
        ))
        await db.commit()

    from mcp_server.api.app import app, require_auth

    def _as(user_id: str, username: str):
        async def _mock(request: Request) -> dict:
            request.state.auth_payload = {"sub": user_id, "type": "access", "username": username}
            return request.state.auth_payload
        return _mock

    app.dependency_overrides[require_auth] = _as("2", "member")
    member_resp = await client.patch(
        "/mcp/projects/member-select/plugin", json={"plugin_pack_id": None}
    )

    app.dependency_overrides[require_auth] = _as("3", "outsider")
    outsider_resp = await client.patch(
        "/mcp/projects/member-select/plugin", json={"plugin_pack_id": None}
    )

    assert member_resp.status_code == 200, member_resp.text
    assert member_resp.json() == {"project_id": "member-select", "plugin_pack_id": None}
    assert outsider_resp.status_code == 403
