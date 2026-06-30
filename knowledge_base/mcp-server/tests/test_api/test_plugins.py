"""Tests for plugin pack branch registry helpers and routes."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from mcp_server.api.routes import plugins as plugins_mod


def _write_pack(path: Path, *, name: str = "starter", version: str = "1.0.0") -> None:
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


def test_zip_member_is_symlink_detects_unix_symlink_metadata():
    info = zipfile.ZipInfo("prompts/link.md")
    info.external_attr = (0o120777 << 16)

    assert plugins_mod._zip_member_is_symlink(info) is True


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

    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "single-summary-doc"
    assert body["manifest_name"] == "swap-pack-starter"
    assert body["repo_branch"] == "single-summary-doc"
    assert body["last_commit_sha"] == "a" * 40


async def test_discover_marks_registered_branch(client, monkeypatch, tmp_path):
    async def fake_clone(repo_url: str, repo_branch: str):
        pack_dir = tmp_path / "clone" / repo_branch
        _write_pack(pack_dir, name="swap-pack-starter", version="0.2.0")
        return pack_dir, "b" * 40

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

    monkeypatch.setattr(plugins_mod, "_clone_pack", fake_clone)
    monkeypatch.setattr(plugins_mod, "_discover_github_pack_branches", fake_discover)

    register_resp = await client.post(
        "/mcp/plugins",
        json={
            "repo_url": "https://github.com/example/swap-pack-starter.git",
            "repo_branch": "single-summary-doc",
        },
    )
    assert register_resp.status_code == 201

    discover_resp = await client.post(
        "/mcp/plugins/discover",
        json={"repo_url": "https://github.com/example/swap-pack-starter"},
    )

    assert discover_resp.status_code == 200
    branch = discover_resp.json()["branches"][0]
    assert branch["registered"] is True
    assert branch["registered_pack_id"] == register_resp.json()["id"]
