"""Tests for scan warning capture across all four parser platforms.

Each platform's parser must:
1. Append a ScanWarning to ScanResult.warnings when a file fails to parse
2. Use the correct phase label
3. Record a relative path (not absolute)
4. Survive a JSON round-trip with warnings intact
5. Report an empty list (never missing) for a clean scan
"""

from __future__ import annotations

from pathlib import Path

import pytest

from intodoc.models import ScanResult, ScanWarning
from intodoc.platforms.generic.parser import GenericParser
from intodoc.platforms.lobster.parser import LobsterParser
from intodoc.platforms.talend.parser import TalendParser
from intodoc.platforms.workato.parser import WorkatoParser
from intodoc.scanner.credential_filter import CredentialFilter


# ── ScanWarning model + round-trip ──────────────────────────────────


def test_scan_result_warnings_default_empty():
    """A freshly-constructed ScanResult has warnings=[] (never missing)."""
    result = ScanResult(platform="talend", repo_root="/tmp/foo")
    assert result.warnings == []


def test_scan_result_warnings_round_trip():
    """ScanResult.warnings survives model_dump_json → model_validate_json."""
    warning = ScanWarning(
        kind="parse_error",
        file="process/broken.item",
        message="XML parse error at line 3",
        phase="job",
    )
    original = ScanResult(
        platform="talend",
        repo_root="/tmp/foo",
        warnings=[warning],
    )
    blob = original.model_dump_json()
    restored = ScanResult.model_validate_json(blob)
    assert len(restored.warnings) == 1
    assert restored.warnings[0].kind == "parse_error"
    assert restored.warnings[0].file == "process/broken.item"
    assert restored.warnings[0].phase == "job"
    assert "XML parse error" in restored.warnings[0].message


# ── Talend parser ───────────────────────────────────────────────────


def _minimal_talend_project(root: Path) -> Path:
    """Build a minimal Talend project structure under `root`.

    Returns the talend project directory. Creates process/ and metadata/ dirs
    so _find_talend_root() succeeds without requiring real .item files.
    """
    project = root / "MINIMAL_TALEND"
    (project / "process").mkdir(parents=True)
    (project / "metadata" / "connections").mkdir(parents=True)
    (project / "context").mkdir(parents=True)
    return project


def test_talend_parser_captures_malformed_job(tmp_path: Path):
    """A malformed .item under process/ generates exactly one ScanWarning
    with phase='job' and kind='parse_error'."""
    project = _minimal_talend_project(tmp_path)
    bad_file = project / "process" / "BROKEN.item"
    bad_file.write_text("<not-xml", encoding="utf-8")

    parser = TalendParser(CredentialFilter())
    result = parser.parse(tmp_path)

    job_warnings = [w for w in result.warnings if w.phase == "job"]
    assert len(job_warnings) == 1
    w = job_warnings[0]
    assert w.kind == "parse_error"
    assert w.file.endswith("BROKEN.item")
    assert not w.file.startswith("/"), "file path should be relative, got absolute"


def test_talend_parser_captures_malformed_metadata_connection(tmp_path: Path):
    """A malformed metadata/connections/*.item records phase='metadata_index'."""
    project = _minimal_talend_project(tmp_path)
    bad_file = project / "metadata" / "connections" / "BROKEN.item"
    bad_file.write_text("<not-xml", encoding="utf-8")

    parser = TalendParser(CredentialFilter())
    result = parser.parse(tmp_path)

    meta_warnings = [w for w in result.warnings if w.phase == "metadata_index"]
    assert len(meta_warnings) == 1
    assert meta_warnings[0].kind == "parse_error"


def test_talend_parser_clean_project_has_empty_warnings(tmp_path: Path):
    """An empty-but-valid Talend project yields warnings=[] (not missing)."""
    _minimal_talend_project(tmp_path)
    parser = TalendParser(CredentialFilter())
    result = parser.parse(tmp_path)
    assert result.warnings == []


def test_talend_parser_missing_project_structure_warns(tmp_path: Path):
    """A directory with no Talend structure surfaces a missing_file warning."""
    parser = TalendParser(CredentialFilter())
    result = parser.parse(tmp_path)
    assert any(w.kind == "missing_file" for w in result.warnings)


# ── Workato parser ──────────────────────────────────────────────────


def test_workato_parser_captures_malformed_recipe(tmp_path: Path):
    """A malformed .recipe.json records phase='recipe'.

    Note: the file has a .recipe.json suffix so the walker's content-based
    classifier picks it up despite the JSON being broken.
    """
    # Manifest signals Workato root to _find_workato_root()
    (tmp_path / "manifest.json").write_text('{"assets": []}', encoding="utf-8")
    # Good recipe so the asset_registry has "recipe" entries to parse
    good = tmp_path / "good.recipe.json"
    good.write_text(
        '{"name": "good", "code": "{}", "config": []}', encoding="utf-8"
    )
    # Broken recipe that survives classification (we add it manually if needed)
    bad = tmp_path / "bad.recipe.json"
    bad.write_text("{not valid json", encoding="utf-8")

    parser = WorkatoParser(CredentialFilter())
    parser.parse(tmp_path)

    # The broken recipe may not be classified because _build_asset_registry
    # skips files whose JSON doesn't parse. Force-inject it into the registry
    # to exercise the warning path in _parse_all_recipes.
    parser._asset_registry.setdefault("recipe", []).append(bad)
    parser._warnings = []
    parser._warn_root = tmp_path
    parser._parse_all_recipes(tmp_path)

    recipe_warnings = [w for w in parser._warnings if w.phase == "recipe"]
    assert len(recipe_warnings) == 1
    assert recipe_warnings[0].kind == "parse_error"
    assert recipe_warnings[0].file.endswith("bad.recipe.json")


def test_workato_parser_clean_project_has_empty_warnings(tmp_path: Path):
    """A Workato export with only valid files yields warnings=[]."""
    (tmp_path / "manifest.json").write_text('{"assets": []}', encoding="utf-8")
    parser = WorkatoParser(CredentialFilter())
    result = parser.parse(tmp_path)
    assert result.warnings == []


# ── Lobster parser ──────────────────────────────────────────────────


def test_lobster_parser_captures_malformed_profile(tmp_path: Path):
    """A malformed <datawizardprofile> XML records phase='profile'."""
    bad = tmp_path / "broken.xml"
    bad.write_text("<datawizardprofile<not-xml", encoding="utf-8")

    parser = LobsterParser(CredentialFilter())
    result = parser.parse(tmp_path)

    profile_warnings = [w for w in result.warnings if w.phase == "profile"]
    assert len(profile_warnings) == 1
    assert profile_warnings[0].kind == "parse_error"
    assert profile_warnings[0].file.endswith("broken.xml")


def test_lobster_parser_clean_project_has_empty_warnings(tmp_path: Path):
    """An empty dir (no profiles at all) yields warnings=[]."""
    parser = LobsterParser(CredentialFilter())
    result = parser.parse(tmp_path)
    assert result.warnings == []


# ── Generic parser ──────────────────────────────────────────────────


def test_generic_parser_captures_malformed_yaml(tmp_path: Path):
    """A malformed .yaml config file records phase='connection'.

    This is the site that was previously fully silent — no console output,
    nothing in ScanResult. The warning now surfaces it.
    """
    pytest.importorskip("yaml")  # skip if PyYAML isn't installed in test env

    # Put a broken yaml in a subdir so classification sees it as a CONFIG file.
    sub = tmp_path / "service"
    sub.mkdir()
    (sub / "database.yaml").write_text(
        "host: localhost\n  bad indent: [unclosed\n", encoding="utf-8"
    )

    parser = GenericParser(CredentialFilter())
    result = parser.parse(tmp_path)

    conn_warnings = [w for w in result.warnings if w.phase == "connection"]
    assert len(conn_warnings) >= 1
    assert conn_warnings[0].kind == "parse_error"
    assert conn_warnings[0].file.endswith("database.yaml")


def test_generic_parser_clean_project_has_empty_warnings(tmp_path: Path):
    """A Generic project with no broken files yields warnings=[]."""
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    parser = GenericParser(CredentialFilter())
    result = parser.parse(tmp_path)
    assert result.warnings == []
