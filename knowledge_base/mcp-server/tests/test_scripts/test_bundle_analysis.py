"""Tests for the standalone ``scripts/bundle_analysis.py`` bundler.

The script is copied verbatim into ``{staging}/_preprocess/_bundle_analysis.py``
and executed by the orchestrator agent with a bare ``python3``, so it is loaded
here by path rather than imported as a package module -- the test must exercise
exactly the file that ships.

The regression under test is run 258: every Phase 2 analysis put its canonical
section names on ``## `` (often ``## 4. TRANSFORMATION_DETAIL``) while the
parser split on ``### `` only, so ~95% of the analysis was silently dropped and
Phase 3 wrote documentation with no evidence behind it.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "mcp_server"
    / "scripts"
    / "bundle_analysis.py"
)


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_bundle_analysis_under_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bundle_analysis = _load_script()

DETAIL_MARKER = "tMap_1 maps AFAS.Employee.number -> 4PS.Resource.externalId"
LOW_RISK_MARKER = "LR-001 no context variables are declared"


def _section_body(label: str, lines: int = 12) -> str:
    return "".join(f"- {label} evidence line {i}: xyzzy filler content\n" for i in range(lines))


def _group_analysis(name: str, *, numbered: bool = True, level: str = "##") -> str:
    """A group analysis shaped like the real Phase 2 output."""
    sections = [
        "COMPONENTS",
        "EXECUTION_FLOW",
        "DATA_FLOWS",
        "TRANSFORMATION_DETAIL",
        "BUSINESS_RULES",
        "SCHEMA_DICTIONARY",
        "SOURCE_SQL",
        "EXTERNAL_SYSTEMS",
        "ERROR_HANDLING",
        "CUSTOM_CODE",
        "ENVIRONMENT_CONFIG",
        "SCHEDULING",
        "NAMING_PATTERNS",
        "CROSS_GROUP_REFS",
        "RISK_FLAGS",
    ]
    parts = [f"# Analysis Report: {name}\n\n", "## Executive Summary\n\nPreamble prose.\n\n"]
    for index, section in enumerate(sections, start=1):
        prefix = f"{index}. " if numbered else ""
        parts.append(f"{level} {prefix}{section}\n\n")
        if section == "TRANSFORMATION_DETAIL":
            parts.append(f"### {name}: tMap_1\n\n{DETAIL_MARKER}\n\n")
        if section == "RISK_FLAGS":
            # Sub-headings that are NOT canonical section names.
            parts.append(f"### LOW\n\n#### {LOW_RISK_MARKER}\n\n")
        parts.append(_section_body(section))
        parts.append("\n")
    return "".join(parts)


def _staging(tmp_path: Path, groups: dict[str, str]) -> Path:
    staging = tmp_path / "docs_staging"
    analysis = staging / "_analysis"
    analysis.mkdir(parents=True)
    (staging / "_assessment").mkdir(parents=True)
    for group, content in groups.items():
        (analysis / f"{group}.md").write_text(content, encoding="utf-8")
    return staging


def _section_files(name: str) -> dict[str, str]:
    """The DIRECTORY layout of the same analysis: one file per section.

    The bodies carry no section heading -- the file name is the section, which
    is the whole point of the layout.
    """
    files = {section: _section_body(section) for section in bundle_analysis.ALL_SECTIONS}
    files["TRANSFORMATION_DETAIL"] = (
        f"{name}: tMap_1\n\n{DETAIL_MARKER}\n\n" + files["TRANSFORMATION_DETAIL"]
    )
    files["RISK_FLAGS"] = f"#### LOW\n\n{LOW_RISK_MARKER}\n\n" + files["RISK_FLAGS"]
    return files


def _write_dir_group(
    analysis: Path,
    group: str,
    files: dict[str, str],
    summary: str | None = None,
) -> Path:
    """Write ``_analysis/{group}/{SECTION}.md`` (+ ``_summary.md``)."""
    directory = analysis / group
    directory.mkdir(parents=True, exist_ok=True)
    for section, body in files.items():
        (directory / f"{section}.md").write_text(body, encoding="utf-8")
    if summary is not None:
        (directory / "_summary.md").write_text(summary, encoding="utf-8")
    return directory


def _dir_staging(
    tmp_path: Path,
    groups: dict[str, dict[str, str]],
    summaries: dict[str, str] | None = None,
) -> Path:
    staging = _staging(tmp_path, {})
    for group, files in groups.items():
        _write_dir_group(
            staging / "_analysis", group, files, (summaries or {}).get(group)
        )
    return staging


def _run_main(staging: Path, total_jobs: int = 10, threshold: int = 200) -> None:
    argv = sys.argv
    sys.argv = ["bundle_analysis.py", str(staging), str(total_jobs), str(threshold)]
    try:
        bundle_analysis.main()
    finally:
        sys.argv = argv


def _parse(content: str) -> dict[str, str]:
    return {name: text for name, text in bundle_analysis.parse_sections(content)}


# --------------------------------------------------------------------------
# 1. The run-258 regression itself.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("level", ["##", "###", "####"])
def test_canonical_sections_are_recognised_at_any_heading_level(level: str) -> None:
    parsed = _parse(_group_analysis("afas", level=level))
    assert "TRANSFORMATION_DETAIL" in parsed
    assert DETAIL_MARKER in parsed["TRANSFORMATION_DETAIL"]
    # ALL_SECTIONS is an ordered tuple (it is the concatenation order for the
    # directory layout, and a set there would be process-dependent).
    assert set(parsed) >= set(bundle_analysis.ALL_SECTIONS)


def test_numeric_prefix_is_stripped_from_section_names() -> None:
    assert bundle_analysis.section_name_for_heading("## 4. TRANSFORMATION_DETAIL") == (
        "TRANSFORMATION_DETAIL"
    )
    assert bundle_analysis.section_name_for_heading("## 10) CUSTOM_CODE") == "CUSTOM_CODE"
    assert bundle_analysis.section_name_for_heading("### RISK FLAGS") == "RISK_FLAGS"


def test_h2_numbered_analysis_reaches_the_core_bundle(tmp_path: Path) -> None:
    """The exact run-258 loss: ``## 4. TRANSFORMATION_DETAIL`` must reach ``core``."""
    staging = _staging(tmp_path, {"afas": _group_analysis("afas")})
    _run_main(staging)

    core = (staging / "_analysis" / "_bundle_core.md").read_text(encoding="utf-8")
    assert DETAIL_MARKER in core
    assert "## 4. TRANSFORMATION_DETAIL" in core
    # ...and the sections core does not want are still filtered out.
    assert "NAMING_PATTERNS evidence line" not in core


def test_parse_sections_is_lossless(tmp_path: Path) -> None:
    content = _group_analysis("fourps")
    assert "".join(text for _, text in bundle_analysis.parse_sections(content)) == content


# --------------------------------------------------------------------------
# 2. Substring matching is gone.
# --------------------------------------------------------------------------


def test_low_subheading_is_not_matched_to_a_flow_section() -> None:
    """``### LOW`` used to resolve to EXECUTION_FLOW or DATA_FLOWS ("LOW" in "FLOW")."""
    assert bundle_analysis.section_name_for_heading("### LOW") is None
    assert bundle_analysis.section_name_for_heading("### HIGH") is None
    assert bundle_analysis.section_name_for_heading("### MEDIUM") is None
    assert bundle_analysis.section_name_for_heading("### CRITICAL Risks") is None
    assert bundle_analysis.section_name_for_heading("### MockApi Execution Flow") is None
    assert bundle_analysis.section_name_for_heading("### Job: afasReader") is None


def test_non_canonical_subheadings_stay_inside_their_parent_section() -> None:
    parsed = _parse(_group_analysis("afas"))
    assert LOW_RISK_MARKER in parsed["RISK_FLAGS"]
    assert "EXECUTION_FLOW" in parsed
    assert LOW_RISK_MARKER not in parsed["EXECUTION_FLOW"]
    assert LOW_RISK_MARKER not in parsed["DATA_FLOWS"]


def test_headings_inside_fenced_code_blocks_are_not_boundaries() -> None:
    content = (
        "# Report\n\n"
        "## CUSTOM_CODE\n\n"
        "```python\n"
        "## COMPONENTS\n"
        "print('still custom code')\n"
        "```\n"
        "trailing custom code prose\n"
    )
    parsed = _parse(content)
    assert "COMPONENTS" not in parsed
    assert "trailing custom code prose" in parsed["CUSTOM_CODE"]


# --------------------------------------------------------------------------
# 3. Determinism across processes (the frozenset iteration-order bug).
# --------------------------------------------------------------------------


def _digest_bundles(analysis_dir: Path) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(analysis_dir.glob("_bundle_*.md"))
    }


def test_bundles_are_byte_identical_across_processes(tmp_path: Path) -> None:
    groups = {name: _group_analysis(name) for name in ("afas", "fourps", "maximo")}
    digests = []
    for run, seed in enumerate(("0", "1", "12345")):
        staging = _staging(tmp_path / f"run{run}", groups)
        env = dict(os.environ, PYTHONHASHSEED=seed)
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(staging), "10", "200"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        digests.append(_digest_bundles(staging / "_analysis"))

    assert digests[0], "no bundles written"
    assert digests[0] == digests[1] == digests[2]


# --------------------------------------------------------------------------
# 4/5. Retention guard.
# --------------------------------------------------------------------------


def _lopsided_analysis() -> str:
    """Almost everything lives in sections ``core`` does not want."""
    parts = ["# Analysis Report: lopsided\n\n"]
    for section in ("COMPONENTS", "DATA_FLOWS"):
        parts.append(f"## {section}\n\n{_section_body(section, lines=2)}\n")
    for section in ("RISK_FLAGS", "BUSINESS_RULES", "CUSTOM_CODE", "NAMING_PATTERNS"):
        parts.append(f"## {section}\n\n{_section_body(section, lines=200)}\n")
    return "".join(parts)


def test_filtered_bundle_below_the_floor_raises(tmp_path: Path) -> None:
    staging = _staging(tmp_path, {"lopsided": _lopsided_analysis()})
    all_sections = {"lopsided": bundle_analysis.parse_sections(_lopsided_analysis())}
    retained, total = bundle_analysis.measure_retention("core", ["lopsided"], all_sections)
    assert retained < 0.5 * total

    with pytest.raises(bundle_analysis.BundleRetentionError) as excinfo:
        bundle_analysis.write_bundles(
            "core",
            ["lopsided"],
            all_sections,
            total,
            {},
            None,
            staging / "_analysis",
        )
    error = excinfo.value
    assert error.agent == "core"
    assert (error.retained_bytes, error.input_bytes) == (retained, total)
    assert str(retained) in str(error) and str(total) in str(error)
    # Nothing was written for the broken agent.
    assert not (staging / "_analysis" / "_bundle_core.md").exists()


def test_unfiltered_bundle_is_exempt_from_the_floor(tmp_path: Path) -> None:
    staging = _staging(tmp_path, {"lopsided": _lopsided_analysis()})
    all_sections = {"lopsided": bundle_analysis.parse_sections(_lopsided_analysis())}
    _retained, total = bundle_analysis.measure_retention(
        "patterns_user", ["lopsided"], all_sections
    )
    written = bundle_analysis.write_bundles(
        "patterns_user",
        ["lopsided"],
        all_sections,
        total,
        {},
        None,
        staging / "_analysis",
    )
    assert written == ["_bundle_patterns_user.md"]


def test_the_run_258_parser_would_have_tripped_the_guard(tmp_path: Path) -> None:
    """Sanity: the guard fires on the shape the broken parser produced."""
    content = _group_analysis("afas")
    broken: list[tuple[str, str]] = []
    current_name, current_lines = "_PREAMBLE", []
    for line in content.splitlines(keepends=True):
        if line.startswith("### "):
            if current_lines:
                broken.append((current_name, "".join(current_lines)))
            # The old normaliser's junk output: never a canonical name here.
            current_name = "MOCKAPI_EXECUTION_FLOW"
            current_lines = [line]
        else:
            current_lines.append(line)
    broken.append((current_name, "".join(current_lines)))

    all_sections = {"afas": broken}
    retained, total = bundle_analysis.measure_retention("core", ["afas"], all_sections)
    assert retained / total < 0.5
    # SectionRecognitionError, not the blunter retention floor: zero canonical
    # names in substantive input is the exact run-258 signature, and it is
    # checked first precisely because it cannot false-positive on a valid run.
    with pytest.raises(bundle_analysis.SectionRecognitionError):
        bundle_analysis.write_bundles(
            "core", ["afas"], all_sections, total, {}, None, _staging(tmp_path, {}) / "_analysis"
        )


def test_size_driven_split_does_not_trip_the_guard(tmp_path: Path) -> None:
    """A bundle split for MAX_BUNDLE_BYTES has still selected all its evidence."""
    groups = {}
    for i in range(8):
        name = f"grp{i}"
        parts = [f"# Analysis Report: {name}\n\n"]
        for section in ("COMPONENTS", "EXECUTION_FLOW", "DATA_FLOWS", "TRANSFORMATION_DETAIL"):
            parts.append(f"## {section}\n\n{_section_body(f'{name} {section}', lines=120)}\n")
        groups[name] = "".join(parts)

    staging = _staging(tmp_path, groups)
    all_sections = {g: bundle_analysis.parse_sections(c) for g, c in groups.items()}
    total = sum(len(c.encode("utf-8")) for c in groups.values())
    assert total > bundle_analysis.MAX_BUNDLE_BYTES

    written = bundle_analysis.write_bundles(
        "core",
        list(groups),
        all_sections,
        total,
        {},
        None,
        staging / "_analysis",
    )
    assert written == ["_bundle_core_a.md", "_bundle_core_b.md"]
    combined = "".join(
        (staging / "_analysis" / name).read_text(encoding="utf-8") for name in written
    )
    for group in groups:
        assert f"{group} TRANSFORMATION_DETAIL evidence line 0" in combined


def test_retention_is_measured_across_all_parts_not_per_part(tmp_path: Path) -> None:
    """Per-part measurement would report ~50% for each half of a two-way split."""
    groups = {f"grp{i}": _group_analysis(f"grp{i}") for i in range(6)}
    all_sections = {g: bundle_analysis.parse_sections(c) for g, c in groups.items()}
    whole, whole_total = bundle_analysis.measure_retention("core", list(groups), all_sections)
    half, half_total = bundle_analysis.measure_retention("core", list(groups)[:3], all_sections)
    assert whole_total == pytest.approx(2 * half_total, rel=0.05)
    assert whole / whole_total == pytest.approx(half / half_total, rel=0.01)


# --------------------------------------------------------------------------
# End-to-end
# --------------------------------------------------------------------------


def test_main_writes_bundles_and_index_and_stays_ascii(tmp_path: Path) -> None:
    groups = {name: _group_analysis(name) for name in ("afas", "fourps")}
    staging = _staging(tmp_path, groups)
    _run_main(staging)

    analysis = staging / "_analysis"
    written = sorted(p.name for p in analysis.glob("_bundle_*.md"))
    assert written == [
        "_bundle_business_ops.md",
        "_bundle_core.md",
        "_bundle_patterns_user.md",
    ]
    assert (analysis / "_index.md").exists()

    for path in list(analysis.glob("_bundle_*.md")) + [analysis / "_index.md"]:
        text = path.read_text(encoding="utf-8")
        offending = {ch for ch in text if ord(ch) > 127} - {
            ch for ch in "".join(groups.values()) if ord(ch) > 127
        }
        assert not offending, f"{path.name} introduced non-ASCII {offending!r}"


def test_retention_floor_sits_below_the_legitimate_band() -> None:
    """The floor must not fire on correct focusing.

    Measured on the 20 real run-258 analyses: a correct parse gives the filtered
    agents 52.7% / 59.1% / 74.0% / 76.6%, while the broken parse gave 8.2% /
    2.3% / 1.9% / 2.7%.  The floor belongs in the gap.  An earlier 0.50 floor sat
    inside the legitimate band -- ``core`` deliberately excludes RISK_FLAGS
    (17.6% of these analyses), so a risk-heavy project could legitimately fall
    under it and hard-fail a good run.
    """
    assert bundle_analysis.MIN_RETENTION_RATIO < 0.50
    assert bundle_analysis.MIN_RETENTION_RATIO > 0.082  # above the worst broken parse


def test_recognition_guard_ignores_trivially_small_input(tmp_path: Path) -> None:
    """A stub analysis with no canonical sections is not a failure."""
    stub = {"afas": [("_PREAMBLE", "generation failed for this group\n")]}
    written = bundle_analysis.write_bundles(
        "core", ["afas"], stub, 40, {}, None, tmp_path
    )
    assert written  # no raise


def test_recognition_guard_passes_when_one_canonical_section_exists(tmp_path: Path) -> None:
    """One real section is enough -- the guard detects total loss, not thinness."""
    sections = {
        "afas": [
            ("_PREAMBLE", "x" * 500),
            ("COMPONENTS", "y" * 500),
            ("MOCKAPI_EXECUTION_FLOW", "z" * 4000),
        ]
    }
    written = bundle_analysis.write_bundles(
        "core", ["afas"], sections, 5000, {}, None, tmp_path
    )
    assert written


# ==========================================================================
# 6. DIRECTORY LAYOUT -- bundling is file selection.
#
# Phase 2 writes _analysis/{group}/{SECTION}.md.  A section is a file named
# after it, so there is nothing to parse: list, keep the stems this agent
# wants, concatenate.  These tests pin that the selection is right, that it
# is deterministic, and that nothing on this path looks inside a file.
# ==========================================================================


def test_directory_group_reaches_its_agent_and_excluded_sections_do_not(
    tmp_path: Path,
) -> None:
    staging = _dir_staging(tmp_path, {"afas": _section_files("afas")})
    _run_main(staging)

    core = (staging / "_analysis" / "_bundle_core.md").read_text(encoding="utf-8")
    assert DETAIL_MARKER in core
    assert "### TRANSFORMATION_DETAIL" in core
    assert "COMPONENTS evidence line 0" in core
    # ...and the sections core does not want are absent, file and content alike.
    assert "NAMING_PATTERNS evidence line" not in core
    assert "### NAMING_PATTERNS" not in core
    assert LOW_RISK_MARKER not in core  # RISK_FLAGS belongs to business_ops

    business = (
        staging / "_analysis" / "_bundle_business_ops.md"
    ).read_text(encoding="utf-8")
    assert LOW_RISK_MARKER in business
    assert "SCHEMA_DICTIONARY evidence line" not in business


def test_directory_sections_are_concatenated_in_contract_order(tmp_path: Path) -> None:
    """Filesystem order is not an order.  ALL_SECTIONS is."""
    files = _section_files("afas")
    reversed_files = dict(reversed(list(files.items())))
    staging = _dir_staging(tmp_path, {"afas": reversed_files})
    _run_main(staging)

    bundle = (staging / "_analysis" / "_bundle_patterns_user.md").read_text(
        encoding="utf-8"
    )
    detail = bundle.split("## DETAIL", 1)[1]
    seen = [
        line[4:].strip()
        for line in detail.splitlines()
        if line.startswith("### ") and line[4:].strip() in set(bundle_analysis.ALL_SECTIONS)
    ]
    assert seen == list(bundle_analysis.ALL_SECTIONS)


def test_directory_path_never_parses_inside_a_section_file(tmp_path: Path) -> None:
    """A heading inside a section file is content, not a boundary."""
    files = _section_files("afas")
    files["SOURCE_SQL"] = (
        "## COMPONENTS\n\nthis line is inside SOURCE_SQL.md and stays there\n"
    )
    staging = _dir_staging(tmp_path, {"afas": files})

    sections = dict(
        bundle_analysis.load_directory_sections(staging / "_analysis" / "afas")
    )
    assert "this line is inside SOURCE_SQL.md" in sections["SOURCE_SQL"]
    assert "this line is inside SOURCE_SQL.md" not in sections["COMPONENTS"]


def test_directory_loading_does_not_call_the_flat_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The parser exists for the legacy layout only; it must not run here."""

    def _explode(_content: str) -> list[tuple[str, str]]:
        raise AssertionError("parse_sections must not run on the directory layout")

    monkeypatch.setattr(bundle_analysis, "parse_sections", _explode)
    staging = _dir_staging(tmp_path, {"afas": _section_files("afas")})
    _run_main(staging)
    assert (staging / "_analysis" / "_bundle_core.md").exists()


def test_summary_is_read_from_the_group_directory(tmp_path: Path) -> None:
    staging = _dir_staging(
        tmp_path,
        {"afas": _section_files("afas")},
        summaries={"afas": "14 designs, 3 risks, AFAS -> 4PS employee sync"},
    )
    _run_main(staging)

    core = (staging / "_analysis" / "_bundle_core.md").read_text(encoding="utf-8")
    assert "14 designs, 3 risks, AFAS -> 4PS employee sync" in core
    index = (staging / "_analysis" / "_index.md").read_text(encoding="utf-8")
    assert "afas/_summary.md" in index
    # The index must name what is on disk: there is no afas.md to open.
    assert "| afas.md |" not in index
    assert "afas/ (15 section files)" in index


def test_directory_group_falls_back_to_a_flat_summary(tmp_path: Path) -> None:
    """Mid-migration: sections in the directory, summary still beside it."""
    staging = _dir_staging(tmp_path, {"afas": _section_files("afas")})
    (staging / "_analysis" / "afas_summary.md").write_text(
        "carried over from the previous run\n", encoding="utf-8"
    )
    _run_main(staging)

    core = (staging / "_analysis" / "_bundle_core.md").read_text(encoding="utf-8")
    assert "carried over from the previous run" in core
    index = (staging / "_analysis" / "_index.md").read_text(encoding="utf-8")
    # The index names the file that exists, not the one the layout usually has.
    assert "afas_summary.md" in index
    assert "afas/_summary.md" not in index


def test_off_contract_section_file_is_retained_and_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A file Python cannot route is still evidence somebody wrote."""
    files = _section_files("afas")
    files["OPERATOR_NOTES"] = "handover note: the nightly window moved to 02:00\n"
    staging = _dir_staging(tmp_path, {"afas": files})
    _run_main(staging)

    out = capsys.readouterr().out
    assert "OPERATOR_NOTES.md" in out
    assert "not a section name on the contract" in out

    for name in ("_bundle_core.md", "_bundle_business_ops.md", "_bundle_patterns_user.md"):
        text = (staging / "_analysis" / name).read_text(encoding="utf-8")
        assert "the nightly window moved to 02:00" in text, name


def test_directory_bundles_are_byte_identical_across_processes(tmp_path: Path) -> None:
    groups = {name: _section_files(name) for name in ("afas", "fourps", "maximo")}
    digests = []
    for run, seed in enumerate(("0", "1", "12345")):
        staging = _dir_staging(tmp_path / f"run{run}", groups)
        env = dict(os.environ, PYTHONHASHSEED=seed)
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(staging), "10", "200"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        digests.append(_digest_bundles(staging / "_analysis"))

    assert digests[0], "no bundles written"
    assert digests[0] == digests[1] == digests[2]


# ==========================================================================
# 7. Mixed estate -- a project mid-migration has both layouts on disk.
# ==========================================================================


def test_mixed_estate_bundles_both_layouts(tmp_path: Path) -> None:
    staging = _staging(tmp_path, {"flatgrp": _group_analysis("flatgrp")})
    _write_dir_group(staging / "_analysis", "dirgrp", _section_files("dirgrp"))
    _run_main(staging)

    core = (staging / "_analysis" / "_bundle_core.md").read_text(encoding="utf-8")
    assert "## Group: flatgrp" in core and "## Group: dirgrp" in core
    assert "dirgrp: tMap_1" in core
    assert "flatgrp: tMap_1" in core

    index = (staging / "_analysis" / "_index.md").read_text(encoding="utf-8")
    assert "| flatgrp.md |" in index
    assert "dirgrp/ (15 section files)" in index


def test_directory_wins_when_both_layouts_exist_for_one_group(tmp_path: Path) -> None:
    """A migrated project keeps its old flat file until slice 5 removes it."""
    flat = _group_analysis("afas").replace(DETAIL_MARKER, "STALE FLAT CONTENT")
    staging = _staging(tmp_path, {"afas": flat})
    _write_dir_group(staging / "_analysis", "afas", _section_files("afas"))
    _run_main(staging)

    core = (staging / "_analysis" / "_bundle_core.md").read_text(encoding="utf-8")
    assert DETAIL_MARKER in core
    assert "STALE FLAT CONTENT" not in core
    assert core.count("## Group: afas") == 1


# ==========================================================================
# 8. Loud on total loss -- on every path.
#
# The previous behaviour was `print("WARNING: ..."); sys.exit(0)`: no bundles,
# exit 0, and Phase 3 pointed at _bundle_*.md files that do not exist.  That
# is the same defect class as the h3-only parse -- a transform that loses
# everything reporting success.
# ==========================================================================


def test_no_analysis_at_all_raises_instead_of_exiting_zero(tmp_path: Path) -> None:
    staging = _staging(tmp_path, {})
    with pytest.raises(bundle_analysis.MissingAnalysisError):
        _run_main(staging)


def test_no_analysis_at_all_fails_the_process(tmp_path: Path) -> None:
    staging = _staging(tmp_path, {})
    (staging / "_analysis" / "afas_summary.md").write_text("orphan\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(staging), "10", "200"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "No group analysis found" in result.stderr
    assert not list((staging / "_analysis").glob("_bundle_*.md"))
    assert not (staging / "_analysis" / "_index.md").exists()


def test_empty_group_directory_raises(tmp_path: Path) -> None:
    staging = _staging(tmp_path, {})
    (staging / "_analysis" / "afas").mkdir()
    with pytest.raises(bundle_analysis.SectionFileContractError) as excinfo:
        _run_main(staging)
    assert "afas/" in str(excinfo.value)


def test_group_directory_holding_only_a_summary_raises(tmp_path: Path) -> None:
    """``_summary.md`` is not a section; a group with only one was not analysed."""
    staging = _dir_staging(tmp_path, {"afas": {}}, summaries={"afas": "x"})
    with pytest.raises(bundle_analysis.SectionFileContractError):
        _run_main(staging)


def test_group_directory_with_no_contract_names_raises(tmp_path: Path) -> None:
    staging = _dir_staging(
        tmp_path, {"afas": {"notes": "a\n", "components": "b\n"}}
    )
    with pytest.raises(bundle_analysis.SectionFileContractError) as excinfo:
        _run_main(staging)
    message = str(excinfo.value)
    assert "notes.md" in message and "components.md" in message
    assert "lost evidence" in message


def test_one_empty_group_directory_does_not_publish_partial_bundles(
    tmp_path: Path,
) -> None:
    """A group nobody analysed stops the bundle, it does not quietly vanish."""
    staging = _dir_staging(tmp_path, {"afas": _section_files("afas")})
    (staging / "_analysis" / "fourps").mkdir()
    with pytest.raises(bundle_analysis.SectionFileContractError):
        _run_main(staging)
    assert not list((staging / "_analysis").glob("_bundle_*.md"))


def test_directory_retention_uses_the_same_floor(tmp_path: Path) -> None:
    """Bytes copied over bytes available -- identical arithmetic, both layouts."""
    lopsided = {
        section: _section_body(section, lines=2)
        for section in ("COMPONENTS", "DATA_FLOWS")
    }
    lopsided.update(
        {
            section: _section_body(section, lines=200)
            for section in ("RISK_FLAGS", "BUSINESS_RULES", "CUSTOM_CODE", "NAMING_PATTERNS")
        }
    )
    staging = _dir_staging(tmp_path, {"lopsided": lopsided})
    sections = {
        "lopsided": bundle_analysis.load_directory_sections(
            staging / "_analysis" / "lopsided"
        )
    }
    retained, total = bundle_analysis.measure_retention("core", ["lopsided"], sections)
    assert retained < bundle_analysis.MIN_RETENTION_RATIO * total
    with pytest.raises(bundle_analysis.BundleRetentionError):
        _run_main(staging)


# ==========================================================================
# 9. The real run-258 analyses (20 groups, ``## COMPONENTS`` headings).
#
# This is the exact shape that broke run 258, in both layouts: as it is on
# disk, and migrated to per-section directories.
# ==========================================================================

RUN_258_ANALYSIS = (
    Path(__file__).resolve().parents[3]
    / "output"
    / "ettm_TEST_Claude"
    / "docs_staging"
    / "_analysis"
)

requires_run_258 = pytest.mark.skipif(
    not RUN_258_ANALYSIS.is_dir(),
    reason=f"real run-258 analyses not present at {RUN_258_ANALYSIS}",
)


def _copy_run_258_flat(tmp_path: Path) -> Path:
    staging = _staging(tmp_path, {})
    analysis = staging / "_analysis"
    for path in sorted(RUN_258_ANALYSIS.glob("*.md")):
        if path.name.startswith("_"):
            continue
        (analysis / path.name).write_bytes(path.read_bytes())
    return staging


def _migrate_run_258_to_directories(tmp_path: Path) -> Path:
    """Rewrite the real flat analyses as ``{group}/{SECTION}.md``."""
    staging = _staging(tmp_path, {})
    analysis = staging / "_analysis"
    for path in sorted(RUN_258_ANALYSIS.glob("*.md")):
        if path.name.startswith("_"):
            continue
        group = path.stem
        text = path.read_text(encoding="utf-8")
        if group.endswith("_summary"):
            (analysis / path.name).write_bytes(path.read_bytes())
            continue
        files: dict[str, str] = {}
        for name, section_text in bundle_analysis.parse_sections(text):
            if name not in set(bundle_analysis.ALL_SECTIONS):
                continue
            body = section_text.split("\n", 1)[1] if "\n" in section_text else ""
            files[name] = files.get(name, "") + body
        assert files, f"{group}: no canonical sections to migrate"
        summary = (RUN_258_ANALYSIS / f"{group}_summary.md")
        _write_dir_group(
            analysis,
            group,
            files,
            summary.read_text(encoding="utf-8") if summary.is_file() else None,
        )
    return staging


def _retention_report(staging: Path) -> dict[str, float]:
    layouts = bundle_analysis.discover_groups(staging / "_analysis")
    sections = {
        group: bundle_analysis.load_group_sections(staging / "_analysis", group, layout)
        for group, layout in layouts.items()
    }
    ratios = {}
    for agent in ("core", "business_ops", "patterns", "user"):
        retained, total = bundle_analysis.measure_retention(
            agent, list(sections), sections
        )
        ratios[agent] = retained / total
    return ratios


@requires_run_258
def test_real_run_258_flat_analyses_still_bundle(tmp_path: Path) -> None:
    staging = _copy_run_258_flat(tmp_path)
    _run_main(staging, total_jobs=300)

    written = sorted(p.name for p in (staging / "_analysis").glob("_bundle_*.md"))
    assert written, "no bundles written from the real run-258 analyses"
    combined = "".join(
        (staging / "_analysis" / name).read_text(encoding="utf-8") for name in written
    )
    for group in bundle_analysis.discover_groups(staging / "_analysis"):
        assert f"## Group: {group}" in combined, group

    ratios = _retention_report(staging)
    assert all(value > bundle_analysis.MIN_RETENTION_RATIO for value in ratios.values())
    assert ratios["core"] > 0.40  # measured 52.7% on a correct parse


@requires_run_258
def test_real_run_258_migrated_to_directories_keeps_the_same_evidence(
    tmp_path: Path,
) -> None:
    flat = _copy_run_258_flat(tmp_path / "flat")
    migrated = _migrate_run_258_to_directories(tmp_path / "dir")
    _run_main(migrated, total_jobs=300)

    analysis = migrated / "_analysis"
    written = sorted(p.name for p in analysis.glob("_bundle_*.md"))
    assert written
    combined = "".join((analysis / name).read_text(encoding="utf-8") for name in written)
    groups = bundle_analysis.discover_groups(analysis)
    assert len(groups) == 20
    assert all(layout == "directory" for layout in groups.values())
    for group in groups:
        assert f"## Group: {group}" in combined, group

    flat_ratios = _retention_report(flat)
    dir_ratios = _retention_report(migrated)
    for agent, ratio in dir_ratios.items():
        assert ratio > bundle_analysis.MIN_RETENTION_RATIO, agent
        # Same evidence, same selection: the layouts must not disagree about
        # what an agent is owed.  (They are not byte-identical -- the directory
        # render drops each file's own preamble and orders sections
        # canonically -- so this is a band, not an equality.)
        assert abs(ratio - flat_ratios[agent]) < 0.10, (agent, ratio, flat_ratios[agent])
