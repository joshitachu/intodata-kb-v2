from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from mcp_server.api.routes.plugins import (
    _pack_registry_name,
    _parse_github_owner_repo,
    _parse_remote_branch_refs,
    _build_starter_zip,
    _zip_member_is_symlink,
    get_starter_pack,
)
from mcp_server.config import config
from mcp_server.services.plugin_schema import load_plugin_manifest


def _starter_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "swap_pack_starter"


def test_swap_pack_starter_manifest_is_valid() -> None:
    starter = _starter_dir()

    manifest = load_plugin_manifest(starter)

    assert manifest.name == "swap-pack-starter"
    assert manifest.steps[0].type == "harness"
    assert [doc.name for doc in manifest.docs or []] == [
        "01_SYSTEM_OVERVIEW",
        "02_INTEGRATION_INVENTORY",
        "03_OPERATIONS_PLAYBOOK",
    ]
    assert (starter / "AGENTS.md").exists()
    assert (starter / ".codex" / "skills" / "tune-swap-pack" / "SKILL.md").exists()


def test_build_starter_zip_includes_runtime_and_authoring_files() -> None:
    payload = _build_starter_zip(_starter_dir())

    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        names = set(zf.namelist())

    assert "swap-pack-starter/plugin.yaml" in names
    assert "swap-pack-starter/prompts/phase2.md" in names
    assert "swap-pack-starter/prompts/phase3.md" in names
    assert "swap-pack-starter/AGENTS.md" in names
    assert "swap-pack-starter/.codex/skills/tune-swap-pack/SKILL.md" in names
    assert "swap-pack-starter/.claude/commands/tune-pack.md" in names


def test_zip_member_is_symlink_detects_unix_symlink() -> None:
    info = zipfile.ZipInfo("link.md")
    info.external_attr = (0o120777 << 16)

    assert _zip_member_is_symlink(info)


def test_parse_github_owner_repo_accepts_https_and_ssh() -> None:
    assert _parse_github_owner_repo("https://github.com/example/swap-pack-starter.git") == (
        "example",
        "swap-pack-starter",
    )
    assert _parse_github_owner_repo("git@github.com:example/swap-pack-starter.git") == (
        "example",
        "swap-pack-starter",
    )


def test_pack_registry_name_uses_safe_branch_slug() -> None:
    name = _pack_registry_name("talend-docs", "feature/custom docs")

    assert name == "talend-docs@feature-custom-docs"
    assert "/" not in name


def test_parse_remote_branch_refs_skips_origin_head_and_preserves_branch_slashes() -> None:
    refs_output = "\n".join(
        [
            "HEAD\t1111111111111111111111111111111111111111",
            "main\t2222222222222222222222222222222222222222",
            "feature/single-summary-doc\t3333333333333333333333333333333333333333",
        ]
    )

    assert _parse_remote_branch_refs(refs_output) == [
        ("main", "2222222222222222222222222222222222222222"),
        ("feature/single-summary-doc", "3333333333333333333333333333333333333333"),
    ]


@pytest.mark.asyncio
async def test_get_starter_pack_returns_public_repo_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config,
        "swap_pack_starter_repo_url",
        "https://github.com/example/swap-pack-starter",
    )

    result = await get_starter_pack()

    assert result.zip_url == "/mcp/plugins/starter.zip"
    assert result.repo_url == "https://github.com/example/swap-pack-starter"
    assert result.clone_command == "git clone https://github.com/example/swap-pack-starter"
