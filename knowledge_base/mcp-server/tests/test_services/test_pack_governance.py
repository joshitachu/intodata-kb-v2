"""Pack Governance V1 policy tests independent of HTTP wiring."""

from __future__ import annotations

import json
from pathlib import Path

from mcp_server.config import config
from mcp_server.db.models import PluginPack
from mcp_server.services.pack_governance import PackGovernanceService, _tree_sha256


def _write_pack(path: Path, *, certification: str | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    lines = [
        "name: governed-pack",
        "version: 1.0.0",
        "steps:",
        "  - id: generate-docs",
        "    type: harness",
    ]
    if certification:
        lines.extend(["certification:", f"  provenance: {certification}"])
    (path / "plugin.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def test_explicit_legacy_bundle_is_only_migration_eligible_when_configured(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    pack_dir = project_root / "plugin_packs" / "governed-pack"
    _write_pack(pack_dir)
    monkeypatch.setattr(config, "project_root", project_root)
    monkeypatch.setattr(
        config,
        "pack_governance_trusted_sources_json",
        json.dumps([
            {
                "kind": "bundled",
                "manifest_name": "governed-pack",
                "provenance": "release",
                "allow_legacy_migration": True,
            }
        ]),
    )
    pack = PluginPack(
        name="governed-pack", manifest_name="governed-pack", version="1.0.0",
        path=str(pack_dir), auto_update=False, origin="legacy",
        certification_status="legacy_untrusted",
    )

    decision = await PackGovernanceService().evaluate(pack)

    assert decision.certified is True
    assert decision.origin == "bundled"
    assert decision.content_sha256 is not None


async def test_persisted_legacy_baseline_remains_certified_on_subsequent_evaluation(
    tmp_path, monkeypatch
):
    project_root = tmp_path / "project"
    pack_dir = project_root / "plugin_packs" / "governed-pack"
    _write_pack(pack_dir)
    monkeypatch.setattr(config, "project_root", project_root)
    monkeypatch.setattr(
        config,
        "pack_governance_trusted_sources_json",
        json.dumps([
            {
                "kind": "bundled",
                "manifest_name": "governed-pack",
                "provenance": "release",
                "allow_legacy_migration": True,
            }
        ]),
    )
    pack = PluginPack(
        name="governed-pack", manifest_name="governed-pack", version="1.0.0",
        path=str(pack_dir), auto_update=False, origin="legacy",
        certification_status="legacy_untrusted",
    )
    service = PackGovernanceService()

    migrated = await service.evaluate(pack)
    pack.origin = migrated.origin
    pack.certification_status = "certified"
    pack.certification_provenance = migrated.provenance
    pack.content_sha256 = migrated.content_sha256

    subsequent = await service.evaluate(pack)

    assert subsequent.certified is True
    assert subsequent.provenance == "release"


async def test_persisted_legacy_baseline_rejects_source_provenance_or_digest_mismatch(
    tmp_path, monkeypatch
):
    project_root = tmp_path / "project"
    pack_dir = project_root / "plugin_packs" / "governed-pack"
    _write_pack(pack_dir)
    monkeypatch.setattr(config, "project_root", project_root)
    monkeypatch.setattr(
        config,
        "pack_governance_trusted_sources_json",
        json.dumps([
            {
                "kind": "bundled",
                "manifest_name": "governed-pack",
                "provenance": "release",
                "allow_legacy_migration": True,
            }
        ]),
    )
    pack = PluginPack(
        name="governed-pack", manifest_name="governed-pack", version="1.0.0",
        path=str(pack_dir), auto_update=False, origin="bundled",
        certification_status="certified", certification_provenance="release",
        content_sha256=_tree_sha256(pack_dir),
    )
    service = PackGovernanceService()

    pack.certification_provenance = "other-release"
    wrong_provenance = await service.evaluate(pack)
    assert wrong_provenance.certified is False

    pack.certification_provenance = "release"
    pack.content_sha256 = "0" * 64
    wrong_digest = await service.evaluate(pack)
    assert wrong_digest.certified is False

    monkeypatch.setattr(
        config,
        "pack_governance_trusted_sources_json",
        json.dumps([
            {
                "kind": "bundled",
                "manifest_name": "governed-pack",
                "provenance": "other-release",
                "allow_legacy_migration": True,
            }
        ]),
    )
    wrong_source = await service.evaluate(pack)
    assert wrong_source.certified is False


async def test_certified_bundle_detects_direct_content_edit(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    pack_dir = project_root / "plugin_packs" / "governed-pack"
    _write_pack(pack_dir, certification="release")
    monkeypatch.setattr(config, "project_root", project_root)
    monkeypatch.setattr(
        config,
        "pack_governance_trusted_sources_json",
        json.dumps([{"kind": "bundled", "manifest_name": "governed-pack", "provenance": "release"}]),
    )
    pack = PluginPack(
        name="governed-pack", manifest_name="governed-pack", version="1.0.0",
        path=str(pack_dir), auto_update=False, origin="bundled",
        certification_status="certified", certification_provenance="release",
        content_sha256=_tree_sha256(pack_dir),
    )

    before = await PackGovernanceService().evaluate(pack)
    (pack_dir / "prompts.md").write_text("unreviewed", encoding="utf-8")
    after = await PackGovernanceService().evaluate(pack)

    assert before.certified is True
    assert after.certified is False
    assert "changed outside reviewed pull-request" in (after.blocked_reason or "")


async def test_git_pack_requires_matching_configured_ref_and_manifest_provenance(tmp_path, monkeypatch):
    pack_dir = tmp_path / "git-pack"
    _write_pack(pack_dir, certification="release")
    monkeypatch.setattr(
        config,
        "pack_governance_trusted_sources_json",
        json.dumps([
            {
                "kind": "git",
                "repo_url": "https://git.example.test/platform/packs.git",
                "refs": ["release/*"],
                "provenance": "release",
            }
        ]),
    )
    pack = PluginPack(
        name="governed-pack", manifest_name="governed-pack", version="1.0.0",
        path=str(pack_dir), repo_url="https://git.example.test/platform/packs.git",
        repo_branch="release/v1", last_commit_sha="a" * 40, auto_update=True,
        origin="git", certification_status="certified", certification_provenance="release",
        content_sha256=_tree_sha256(pack_dir),
    )

    accepted = await PackGovernanceService().evaluate(pack, verify_integrity=False)
    pack.repo_branch = "feature/unsafe"
    rejected = await PackGovernanceService().evaluate(pack, verify_integrity=False)

    assert accepted.certified is True
    assert rejected.certified is False
    assert "not certified" in (rejected.blocked_reason or "")
