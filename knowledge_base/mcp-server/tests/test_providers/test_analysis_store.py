"""Analysis store: one reader, two layouts (plan slice 1).

The store's whole purpose is that a consumer cannot tell whether a group's
analysis is stored as one flat markdown file with headings inside it or as a
directory of section files.  So almost every test below is written twice, once
per layout, against the SAME logical analysis -- an assertion that holds for one
layout and not the other is a migration bug waiting for slices 2-5.

The two failure shapes that motivated the module are pinned directly:

* ``{group}_summary.md`` counted as a group (the live ``_detect_resume_state``
  defect: 15 analyses reported as 30, five groups never analysed),
* a transform that resolves to nothing returning empty instead of raising (the
  run-258 defect: ``## COMPONENTS`` where ``### COMPONENTS`` was expected, zero
  sections matched, ~95% of the evidence dropped in silence).
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_server.providers.custom.analysis_parser import (
    PHASE2_SECTION_NAMES,
    parse_phase2_sections,
)
from mcp_server.providers.custom.analysis_store import (
    AnalysisLayout,
    AnalysisPathError,
    SectionContractError,
    canonical_section_names,
    group_dir,
    layout_of,
    list_groups,
    list_sections,
    read_group_analysis,
    read_section,
    read_summary,
    write_section,
    write_summary,
)

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
REPO_ROOT = Path(__file__).resolve().parents[4]
OUTPUT_ROOT = REPO_ROOT / "knowledge_base" / "output"

#: The run this slice was specified against (15 of 20 groups analysed).  Phase 4
#: cleanup plus the staging -> docs swap destroy ``_analysis/`` on a completed
#: run, so this path is present only while a run is in flight or was preserved.
RUN_259_ANALYSIS = OUTPUT_ROOT / "ettm_fix" / "docs_staging" / "_analysis"

#: Preserved staging from other real runs, used when run 259's is gone.  Both
#: are genuine model output, not fixtures.
REAL_ANALYSIS_DIRS = (
    RUN_259_ANALYSIS,
    OUTPUT_ROOT / "ettm_TEST_Claude" / "docs_staging" / "_analysis",
    OUTPUT_ROOT / "werkorder" / "ETTM" / "docs_staging" / "_analysis",
)

#: One logical analysis, stored two ways by the fixtures below.
LOGICAL_SECTIONS: dict[str, str] = {
    "COMPONENTS": (
        "- tRESTClient_1 -- calls the 4PS OData endpoint\n"
        "- tMap_1 -- projects the order rows\n"
    ),
    "EXECUTION_FLOW": "tFileInputDelimited_1 -> tMap_1 -> tRESTClient_1\n",
    "TRANSFORMATION_DETAIL": (
        "`out.stamp = TalendDate.getDate(\"yyyy-MM-dd\")`\n\n"
        "```java\noutput_row.id = input_row.id;\n```\n"
    ),
    "SOURCE_SQL": "```sql\nSELECT id, name FROM dbo.Orders\n```\n",
    "RISK_FLAGS": "#### HIGH\n- hardcoded endpoint\n\n#### LOW\n- verbose logging\n",
}


# ── fixtures: the same analysis in both layouts ─────────────────────────────


def _flat_text(sections: dict[str, str]) -> str:
    """A flat analysis file.

    Deliberately starts at the first section heading: any preamble would land in
    ``UNCLASSIFIED_OR_EXTRA_FINDINGS`` (a documented asymmetry, pinned by its own
    test below) and would make the two layouts legitimately unequal here.
    """
    return "\n".join(f"### {name}\n\n{body}" for name, body in sections.items())


def write_flat_group(analysis_dir: Path, group: str, sections: dict[str, str]) -> Path:
    analysis_dir.mkdir(parents=True, exist_ok=True)
    path = analysis_dir / f"{group}.md"
    path.write_text(_flat_text(sections), encoding="utf-8")
    (analysis_dir / f"{group}_summary.md").write_text(
        f"# {group} summary\n\nnavigation aid\n", encoding="utf-8"
    )
    return path


def write_directory_group(analysis_dir: Path, group: str, sections: dict[str, str]) -> Path:
    for name, body in sections.items():
        write_section(analysis_dir, group, name, body)
    write_summary(analysis_dir, group, f"# {group} summary\n\nnavigation aid\n")
    return group_dir(analysis_dir, group)


@pytest.fixture()
def flat_dir(tmp_path: Path) -> Path:
    analysis = tmp_path / "flat" / "_analysis"
    write_flat_group(analysis, "fourps", LOGICAL_SECTIONS)
    return analysis


@pytest.fixture()
def directory_dir(tmp_path: Path) -> Path:
    analysis = tmp_path / "structured" / "_analysis"
    write_directory_group(analysis, "fourps", LOGICAL_SECTIONS)
    return analysis


# ── 1. the two layouts are indistinguishable ────────────────────────────────


def test_layout_of_reports_what_is_on_disk(flat_dir: Path, directory_dir: Path) -> None:
    assert layout_of(flat_dir, "fourps") is AnalysisLayout.FLAT
    assert layout_of(directory_dir, "fourps") is AnalysisLayout.DIRECTORY
    assert layout_of(flat_dir, "nosuchgroup") is AnalysisLayout.ABSENT


def test_a_directory_wins_over_a_leftover_flat_file(tmp_path: Path) -> None:
    """A migrated project keeps its old flat file until slice 5 deletes it."""
    analysis = tmp_path / "_analysis"
    write_flat_group(analysis, "fourps", {"COMPONENTS": "stale\n"})
    write_directory_group(analysis, "fourps", {"COMPONENTS": "current\n"})
    assert layout_of(analysis, "fourps") is AnalysisLayout.DIRECTORY
    assert read_section(analysis, "fourps", "COMPONENTS") == "current\n"


def test_both_layouts_list_the_same_sections(flat_dir: Path, directory_dir: Path) -> None:
    flat = list_sections(flat_dir, "fourps")
    structured = list_sections(directory_dir, "fourps")
    assert flat == structured
    # ...and the order is the contract's, not the filesystem's or the file's.
    assert flat == tuple(
        name for name in PHASE2_SECTION_NAMES if name in set(LOGICAL_SECTIONS)
    )


def test_both_layouts_read_equivalent_whole_analyses(
    flat_dir: Path, directory_dir: Path
) -> None:
    """Equivalent under the parser every prose consumer already uses.

    Not byte-identical: a flat file is returned verbatim while a directory is
    rendered.  What must hold is that a consumer parsing the result gets the
    same sections with the same bodies.
    """
    flat_text = read_group_analysis(flat_dir, "fourps")
    structured_text = read_group_analysis(directory_dir, "fourps")
    assert parse_phase2_sections(flat_text) == parse_phase2_sections(structured_text)
    assert set(parse_phase2_sections(structured_text)) == set(LOGICAL_SECTIONS)
    # The rendered directory carries every section body verbatim.
    for body in LOGICAL_SECTIONS.values():
        assert body.strip() in structured_text


def test_both_layouts_read_the_same_section_bodies(
    flat_dir: Path, directory_dir: Path
) -> None:
    for name, body in LOGICAL_SECTIONS.items():
        assert read_section(flat_dir, "fourps", name) == body.strip()
        assert read_section(directory_dir, "fourps", name) == body
    # A section name is matched by the contract's normalisation, not by case.
    assert read_section(directory_dir, "fourps", "components") == (
        LOGICAL_SECTIONS["COMPONENTS"]
    )


def test_both_layouts_read_the_group_summary(flat_dir: Path, directory_dir: Path) -> None:
    assert read_summary(flat_dir, "fourps") == "# fourps summary\n\nnavigation aid\n"
    assert read_summary(directory_dir, "fourps") == "# fourps summary\n\nnavigation aid\n"
    assert read_summary(flat_dir, "nosuchgroup") is None
    # The summary is a navigation aid, never part of the analysis itself.
    assert "navigation aid" not in read_group_analysis(directory_dir, "fourps")
    assert "navigation aid" not in read_group_analysis(flat_dir, "fourps")


# ── 2. {group}_summary.md is not a group ────────────────────────────────────


def test_list_groups_does_not_count_summaries_in_either_layout(tmp_path: Path) -> None:
    flat = tmp_path / "flat"
    structured = tmp_path / "structured"
    for index in range(15):
        write_flat_group(flat, f"group{index:02d}", LOGICAL_SECTIONS)
        write_directory_group(structured, f"group{index:02d}", LOGICAL_SECTIONS)

    expected = tuple(f"group{index:02d}" for index in range(15))
    # 15 analyses + 15 summaries reported as 30 is the live resume defect.
    assert len(list(flat.glob("*.md"))) == 30
    assert list_groups(flat) == expected
    assert list_groups(structured) == expected


def test_list_groups_ignores_internal_artifacts(tmp_path: Path) -> None:
    analysis = tmp_path / "_analysis"
    write_flat_group(analysis, "afas", LOGICAL_SECTIONS)
    write_directory_group(analysis, "maximo", LOGICAL_SECTIONS)
    for name in (
        "_index.md",
        "_bundle_core_a.md",
        "_doc_pack_00_AGENT_BRIEF.md",
        "_runtime_profile.md",
        "_cross_cutting_facts.md",
    ):
        (analysis / name).write_text("internal\n", encoding="utf-8")
    (analysis / "_coverage_units").mkdir()
    (analysis / "_coverage_units" / "afas.json").write_text("{}", encoding="utf-8")
    (analysis / "_coverage_report.json").write_text("{}", encoding="utf-8")

    assert list_groups(analysis) == ("afas", "maximo")


def test_a_summary_without_an_analysis_is_not_a_group(tmp_path: Path) -> None:
    """There is nothing to read, so reporting it as analysed would be the bug."""
    analysis = tmp_path / "_analysis"
    analysis.mkdir(parents=True)
    (analysis / "utils_summary.md").write_text("orphan summary\n", encoding="utf-8")
    assert list_groups(analysis) == ()


def test_list_groups_on_a_missing_directory_is_empty(tmp_path: Path) -> None:
    assert list_groups(tmp_path / "nothing_here") == ()


# ── 3. absent and empty are different facts ─────────────────────────────────


def test_a_missing_section_is_none_in_both_layouts(
    flat_dir: Path, directory_dir: Path
) -> None:
    assert read_section(flat_dir, "fourps", "SCHEDULING") is None
    assert read_section(directory_dir, "fourps", "SCHEDULING") is None
    assert read_section(flat_dir, "nosuchgroup", "COMPONENTS") is None


def test_an_empty_section_file_reads_as_empty_not_absent(tmp_path: Path) -> None:
    """"The writer found no SQL" and "nobody looked" are different facts."""
    analysis = tmp_path / "_analysis"
    write_directory_group(analysis, "fourps", {"COMPONENTS": "one\n", "SOURCE_SQL": ""})
    assert read_section(analysis, "fourps", "SOURCE_SQL") == ""
    assert read_section(analysis, "fourps", "SOURCE_SQL") is not None
    assert "SOURCE_SQL" in list_sections(analysis, "fourps")


def test_asymmetry_an_empty_flat_section_cannot_be_represented(tmp_path: Path) -> None:
    """Pinned because slices 2-5 will meet it: flat loses "declared but empty".

    ``parse_phase2_sections`` drops a section whose body is blank, so the flat
    layout cannot distinguish an empty section from a missing one.  The
    directory layout can, and the test above proves it does.
    """
    analysis = tmp_path / "_analysis"
    write_flat_group(analysis, "fourps", {"COMPONENTS": "one\n", "SOURCE_SQL": ""})
    assert read_section(analysis, "fourps", "SOURCE_SQL") is None
    assert "SOURCE_SQL" not in list_sections(analysis, "fourps")


def test_asymmetry_flat_preamble_becomes_unclassified(tmp_path: Path) -> None:
    """A flat file's title/preamble has no position in the directory layout."""
    analysis = tmp_path / "_analysis"
    analysis.mkdir(parents=True)
    (analysis / "fourps.md").write_text(
        "# Analysis: fourps Group\n\n## Executive Summary\n\nfourteen designs\n\n"
        + _flat_text({"COMPONENTS": "one\n"}),
        encoding="utf-8",
    )
    sections = list_sections(analysis, "fourps")
    assert "COMPONENTS" in sections
    assert "UNCLASSIFIED_OR_EXTRA_FINDINGS" in sections
    assert "fourteen designs" in (
        read_section(analysis, "fourps", "UNCLASSIFIED_OR_EXTRA_FINDINGS") or ""
    )


# ── 4. a transform that loses everything must be loud ───────────────────────


def test_a_directory_with_no_known_section_raises(tmp_path: Path) -> None:
    analysis = tmp_path / "_analysis"
    directory = analysis / "fourps"
    directory.mkdir(parents=True)
    (directory / "notes.md").write_text("x" * 400, encoding="utf-8")
    (directory / "scratch.md").write_text("y" * 400, encoding="utf-8")

    for call in (
        lambda: read_group_analysis(analysis, "fourps"),
        lambda: list_sections(analysis, "fourps"),
        lambda: read_section(analysis, "fourps", "COMPONENTS"),
    ):
        with pytest.raises(SectionContractError) as excinfo:
            call()
        # The message must name what it saw, or the operator cannot act on it.
        assert "notes.md" in str(excinfo.value)


def test_an_empty_group_directory_raises_on_read(tmp_path: Path) -> None:
    analysis = tmp_path / "_analysis"
    (analysis / "fourps").mkdir(parents=True)
    with pytest.raises(SectionContractError, match="holds no section file"):
        read_group_analysis(analysis, "fourps")
    # A LISTING of an empty directory is not a lie, so it does not raise.
    assert list_sections(analysis, "fourps") == ()


def test_a_flat_analysis_with_no_known_heading_raises(tmp_path: Path) -> None:
    """The literal run-258 shape, in the layout that produced it."""
    analysis = tmp_path / "_analysis"
    analysis.mkdir(parents=True)
    (analysis / "fourps.md").write_text(
        "# Analysis\n\n## Findings\n\n" + ("detail line\n" * 60), encoding="utf-8"
    )
    with pytest.raises(SectionContractError):
        list_sections(analysis, "fourps")


def test_an_unknown_section_file_is_kept_beside_the_known_ones(tmp_path: Path) -> None:
    """Never dropped: an unrecognised name is reported, after the contract's."""
    analysis = tmp_path / "_analysis"
    write_directory_group(analysis, "fourps", {"COMPONENTS": "one\n"})
    (group_dir(analysis, "fourps") / "OPEN_QUESTIONS.md").write_text(
        "who owns the endpoint?\n", encoding="utf-8"
    )
    assert list_sections(analysis, "fourps") == ("COMPONENTS", "OPEN_QUESTIONS")
    assert "who owns the endpoint?" in read_group_analysis(analysis, "fourps")


def test_two_files_that_normalise_to_one_section_are_both_read(tmp_path: Path) -> None:
    """Name drift must not silently cost a section one of its two files.

    ``source sql.md`` and ``SOURCE_SQL.md`` are distinct filenames on every
    filesystem (case-insensitive ones included) and normalise to one section.
    """
    analysis = tmp_path / "_analysis"
    directory = analysis / "fourps"
    directory.mkdir(parents=True)
    (directory / "SOURCE_SQL.md").write_text("first\n", encoding="utf-8")
    (directory / "source sql.md").write_text("second\n", encoding="utf-8")
    assert len(list(directory.iterdir())) == 2

    body = read_section(analysis, "fourps", "SOURCE_SQL") or ""
    assert "first" in body and "second" in body
    assert list_sections(analysis, "fourps") == ("SOURCE_SQL",)


def test_reading_an_absent_group_raises_file_not_found(tmp_path: Path) -> None:
    analysis = tmp_path / "_analysis"
    analysis.mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="nosuchgroup"):
        read_group_analysis(analysis, "nosuchgroup")


# ── 5. determinism ──────────────────────────────────────────────────────────


_PROBE = """
import json, sys
from pathlib import Path
from mcp_server.providers.custom.analysis_store import (
    list_groups, list_sections, read_group_analysis,
)
analysis = Path(sys.argv[1])
print(json.dumps({
    "groups": list(list_groups(analysis)),
    "sections": {g: list(list_sections(analysis, g)) for g in list_groups(analysis)},
    "text": {g: read_group_analysis(analysis, g) for g in list_groups(analysis)},
}))
"""


def _probe(analysis_dir: Path, hash_seed: str) -> dict:
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = hash_seed
    environment["PYTHONPATH"] = str(SRC_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, str(analysis_dir)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("layout", ["flat", "directory"])
def test_reads_are_byte_stable_across_processes(tmp_path: Path, layout: str) -> None:
    """A different hash seed must not change one byte.

    ``parse_phase2_sections`` seeds its result dict from a SET of section names,
    so section ORDER out of the flat layout is process-dependent unless the store
    sorts explicitly.  This repo has already shipped one artifact that was not
    byte-stable across processes for exactly that reason, silently voiding a
    provider prompt cache; a same-process comparison cannot see it.
    """
    analysis = tmp_path / "_analysis"
    writer = write_flat_group if layout == "flat" else write_directory_group
    for index in range(4):
        writer(analysis, f"group{index}", LOGICAL_SECTIONS)

    first = _probe(analysis, "0")
    second = _probe(analysis, "12345")
    assert first == second
    assert first["groups"] == list(list_groups(analysis))
    for group in first["groups"]:
        assert first["sections"][group] == list(list_sections(analysis, group))
        assert first["text"][group] == read_group_analysis(analysis, group)


# ── 6. path safety ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "group",
    ["..", "../escape", "sub/dir", "/etc/passwd", "", "   ", ".hidden", "a\\b", "x\x00y"],
)
def test_a_group_name_cannot_escape_the_analysis_directory(
    tmp_path: Path, group: str
) -> None:
    for call in (
        lambda: layout_of(tmp_path, group),
        lambda: group_dir(tmp_path, group),
        lambda: read_section(tmp_path, group, "COMPONENTS"),
        lambda: write_section(tmp_path, group, "COMPONENTS", "x"),
    ):
        with pytest.raises(AnalysisPathError):
            call()
    assert not (tmp_path.parent / "escape.md").exists()


@pytest.mark.parametrize("section", ["", "  ", "...", "###", "/", ".."])
def test_a_section_name_that_names_nothing_is_rejected(
    tmp_path: Path, section: str
) -> None:
    with pytest.raises(AnalysisPathError):
        write_section(tmp_path, "fourps", section, "x")
    with pytest.raises(AnalysisPathError):
        read_section(tmp_path, "fourps", section)


@pytest.mark.parametrize(
    ("section", "expected"),
    [
        ("source sql", "SOURCE_SQL.md"),
        ("../COMPONENTS", "COMPONENTS.md"),
        ("a/b", "A_B.md"),
        ("4. TRANSFORMATION_DETAIL", "TRANSFORMATION_DETAIL.md"),
    ],
)
def test_a_section_name_is_normalised_before_it_reaches_the_filesystem(
    tmp_path: Path, section: str, expected: str
) -> None:
    """Only ``[A-Z0-9_]`` survives the contract's normalisation.

    That is what makes traversal unreachable for section names -- separators and
    dots collapse -- and it is the same rule the heading parser applies, so
    ``source sql``, ``4. SOURCE_SQL`` and ``SOURCE_SQL`` are one section and not
    three.
    """
    # A known section so the "no known section" invariant is not what fires
    # here; ``a/b`` normalises to a perfectly safe name that is not on the
    # contract, and that case is covered by its own test above.
    write_section(tmp_path, "fourps", "COMPONENTS", "one\n")

    path = write_section(tmp_path, "fourps", section, "SELECT 1\n")
    assert path == tmp_path / "fourps" / expected
    assert path.resolve().parent == (tmp_path / "fourps").resolve()
    assert read_section(tmp_path, "fourps", section) == "SELECT 1\n"


# ── 7. write/read round trip ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "content",
    [
        "```sql\nSELECT id FROM dbo.Orders WHERE x = '1'\n```\n",
        "no trailing newline",
        "",
        "trailing whitespace   \n\n\n",
        "mixed \r\n line endings\n",
        "non-ascii in model output: · →\n",
    ],
)
def test_write_then_read_is_byte_exact(tmp_path: Path, content: str) -> None:
    """The store must not edit the model's bytes -- no added heading, no newline."""
    path = write_section(tmp_path, "fourps", "SOURCE_SQL", content)
    assert path.read_bytes() == content.encode("utf-8")
    assert read_section(tmp_path, "fourps", "SOURCE_SQL") == content


def test_write_summary_round_trips(tmp_path: Path) -> None:
    write_summary(tmp_path, "fourps", "# fourps\n\n- 29 designs\n")
    assert read_summary(tmp_path, "fourps") == "# fourps\n\n- 29 designs\n"
    assert (tmp_path / "fourps" / "_summary.md").is_file()


def test_written_sections_are_readable_as_a_whole_analysis(tmp_path: Path) -> None:
    for name, body in LOGICAL_SECTIONS.items():
        write_section(tmp_path, "fourps", name, body)
    assert set(parse_phase2_sections(read_group_analysis(tmp_path, "fourps"))) == set(
        LOGICAL_SECTIONS
    )
    assert list_groups(tmp_path) == ("fourps",)


def test_the_contract_is_not_redefined_here(tmp_path: Path) -> None:
    """One section list, imported -- a second copy is how the two drift."""
    assert canonical_section_names() is PHASE2_SECTION_NAMES


# ── 8. the real estate ──────────────────────────────────────────────────────


def _real_analysis_dirs() -> list[Path]:
    return [path for path in REAL_ANALYSIS_DIRS if path.is_dir()]


@pytest.mark.skipif(
    not RUN_259_ANALYSIS.is_dir(),
    reason="run-259 staging is not present (Phase 4 + the docs swap destroy it)",
)
def test_run_259_reports_fifteen_groups_not_thirty() -> None:
    """The exact count the resume defect got wrong: 15 analyses, 30 files."""
    groups = list_groups(RUN_259_ANALYSIS)
    assert len(groups) == 15, groups
    for group in groups:
        sections = list_sections(RUN_259_ANALYSIS, group)
        assert sections, group
        assert read_group_analysis(RUN_259_ANALYSIS, group).strip()


@pytest.mark.skipif(not _real_analysis_dirs(), reason="no preserved run staging on this machine")
@pytest.mark.parametrize("analysis_dir", _real_analysis_dirs(), ids=lambda p: p.parts[-3])
def test_real_model_output_reads_end_to_end(analysis_dir: Path) -> None:
    """Every group of a real run reads, and summaries are never counted.

    Real Phase 2 output, not a fixture: these files are where ``## COMPONENTS``
    was written for a ``### COMPONENTS`` contract, so they are the only evidence
    that the store survives the heading shapes the model actually produces.
    """
    groups = list_groups(analysis_dir)
    assert groups

    flat_analyses = {
        path.stem
        for path in analysis_dir.glob("*.md")
        if not path.name.startswith("_") and not path.stem.endswith("_summary")
    }
    directory_analyses = {
        path.name
        for path in analysis_dir.iterdir()
        if path.is_dir() and not path.name.startswith(("_", "."))
    }
    assert set(groups) == flat_analyses | directory_analyses
    assert not any(group.endswith("_summary") for group in groups)

    for group in groups:
        sections = list_sections(analysis_dir, group)
        assert sections, f"{group} resolved to no section"
        known = [name for name in sections if name in set(PHASE2_SECTION_NAMES)]
        assert known, f"{group} resolved to no KNOWN section: {sections}"
        text = read_group_analysis(analysis_dir, group)
        assert text.strip()
        for name in known:
            body = read_section(analysis_dir, group, name)
            assert body is not None


# ── 9. the module's own dependency graph ────────────────────────────────────


def _is_type_checking_guard(node: ast.AST) -> bool:
    """``if TYPE_CHECKING:`` / ``if typing.TYPE_CHECKING:`` -- not a runtime import."""
    if not isinstance(node, ast.If):
        return False
    test = node.test
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _first_party_module_imports(path: Path) -> set[str]:
    """Every ``mcp_server.*`` name a module imports AT RUNTIME.

    ``if TYPE_CHECKING:`` bodies are skipped: ``analysis_parser`` imports
    ``pack_contract`` there for one annotation, and counting it would make the
    closure look heavier than the interpreter ever makes it.
    """
    names: set[str] = set()

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if _is_type_checking_guard(child):
                for orelse in child.orelse:  # the else branch DOES run
                    visit(orelse)
                continue
            if isinstance(child, ast.Import):
                names.update(alias.name for alias in child.names)
            elif isinstance(child, ast.ImportFrom) and child.module and not child.level:
                names.add(child.module)
                names.update(f"{child.module}.{alias.name}" for alias in child.names)
            visit(child)

    visit(ast.parse(path.read_text(encoding="utf-8")))
    return {name for name in names if name.startswith("mcp_server.")}


def _module_file(name: str) -> Path | None:
    candidate = SRC_ROOT / Path(*name.split(".")).with_suffix(".py")
    return candidate if candidate.is_file() else None


def test_the_store_does_not_depend_on_the_harness() -> None:
    """The 7,876-line harness must not be in this module's own import closure.

    Measured limitation, reported rather than papered over: importing ANY module
    under ``mcp_server.providers.custom`` executes that package's ``__init__``,
    which imports ``harness`` unconditionally, so ``harness`` is in
    ``sys.modules`` after importing this store no matter what it depends on.
    That package ``__init__`` is outside this slice's file ownership.  What is
    checkable here -- and what the later slices need -- is that the store's own
    transitive import graph never reaches the harness.
    """
    start = SRC_ROOT / "mcp_server" / "providers" / "custom" / "analysis_store.py"
    seen: set[str] = set()
    modules: set[str] = set()
    queue = [start]
    while queue:
        current = queue.pop()
        for name in sorted(_first_party_module_imports(current)):
            if name in seen:
                continue
            seen.add(name)
            module_file = _module_file(name)
            if module_file is not None:
                modules.add(name)
                queue.append(module_file)

    assert "mcp_server.providers.custom.harness" not in modules, sorted(modules)
    assert not any("harness" in name for name in seen), sorted(seen)
    # The closure is small on purpose; it is what makes the store reusable.
    assert "mcp_server.providers.custom.analysis_parser" in modules
    assert modules == {
        "mcp_server.providers.custom.analysis_parser",
        "mcp_server.providers.custom.constants",
        "mcp_server.providers.custom.prompt_packs",
        "mcp_server.providers.custom.section_parser",
    }, sorted(modules)


def test_importing_the_store_is_hash_seed_independent(tmp_path: Path) -> None:
    """The canonical order is a tuple, not a set, in every process."""
    script = (
        "from mcp_server.providers.custom.analysis_store import canonical_section_names\n"
        "print(','.join(canonical_section_names()))\n"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(SRC_ROOT)
    digests = set()
    for seed in ("0", "1", "9999"):
        environment["PYTHONHASHSEED"] = seed
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        digests.add(hashlib.sha256(result.stdout.encode("utf-8")).hexdigest())
    assert len(digests) == 1
