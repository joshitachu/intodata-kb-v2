"""Tests for pack_loader (US-106)."""
from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server.services.pack_loader import InstructionPack, PackLoader


def _make_pack(tmp_path: Path, name: str = "test", version: str | None = None) -> Path:
    pack_dir = tmp_path / name
    pack_dir.mkdir()
    if version is not None:
        (pack_dir / "version.txt").write_text(version)
    return pack_dir


def test_pack_loader_reads_preamble_files(tmp_path: Path):
    pack_dir = _make_pack(tmp_path, version="v2.0.0")
    (pack_dir / "phase2_preamble.md").write_text("Phase 2 custom preamble.")

    pack = PackLoader(pack_dir).load()

    assert pack.version == "v2.0.0"
    assert "phase2_preamble" in pack.preambles
    assert pack.preambles["phase2_preamble"] == "Phase 2 custom preamble."


def test_pack_loader_falls_back_to_default_when_file_missing(tmp_path: Path):
    pack_dir = _make_pack(tmp_path)
    pack = PackLoader(pack_dir).load()

    loader = PackLoader(pack_dir)
    result = loader.get(pack, "nonexistent_phase", "fallback text")

    assert result == "fallback text"


def test_pack_loader_version_from_directory_name(tmp_path: Path):
    pack_dir = _make_pack(tmp_path, name="experimental-v3")

    pack = PackLoader(pack_dir).load()

    assert pack.version == "experimental-v3"


def test_custom_harness_uses_pack_preamble_when_provided(tmp_path: Path):
    from mcp_server.providers.custom.prompts import group_analysis_prompt
    from mcp_server.providers.custom.models import FactsManifest, UnitFact

    pack_dir = _make_pack(tmp_path, version="v1.0.0")
    custom_preamble = "CUSTOM PHASE2 PREAMBLE"
    (pack_dir / "phase2_preamble.md").write_text(custom_preamble)
    pack = PackLoader(pack_dir).load()

    unit = UnitFact(name="Recipe One", group="Recipes")
    facts = FactsManifest(
        project_id="test",
        platform="workato",
        canonical_unit_label="recipes",
        unit_count=1,
        units=[unit],
        groups={"Recipes": 1},
    )

    prompt = group_analysis_prompt(facts, "Recipes", pack=pack)

    assert custom_preamble in prompt
    assert "Recipe One" in prompt
