"""Unit tests for the deterministic locus gate.

Every citation shape asserted here was copied out of a real run's
``_analysis/`` (pluripharm, 2026-09-25), not invented: the gate is only worth
having if it parses what the model actually emits.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.providers.custom.locus_gate import (
    FAILURE_STATUSES,
    LocusGateReport,
    citations_in,
    classify_analysis_file,
    is_internal_analysis_file,
    locus_gate_messages,
    validate_loci,
    write_locus_gate_report,
)


def _staging(tmp_path: Path, evidence: dict[str, int], analyses: dict[str, str]) -> Path:
    """Build a staging tree: ``evidence`` maps a path to a line count."""
    staging = tmp_path / "docs_staging"
    for relative, lines in evidence.items():
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(f"line {n}" for n in range(1, lines + 1)) + "\n")
    for name, body in analyses.items():
        target = staging / "_analysis" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    (staging / "_analysis").mkdir(parents=True, exist_ok=True)
    return staging


# --------------------------------------------------------------------------- #
# The `_analysis/` naming rule (FIX 4)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "name,expected",
    [
        ("_shared_library.md", "internal"),
        ("_anything_else.md", "internal"),
        ("Collectors.md", "group_analysis"),
        ("Collectors_summary.md", "group_summary"),
        ("Collectors/SOURCE_SQL.md", "group_section"),
        ("_scratch/Collectors.md", "internal"),
    ],
)
def test_classification_of_analysis_files(name: str, expected: str) -> None:
    assert classify_analysis_file(name) == expected


def test_underscore_files_are_never_group_analyses() -> None:
    """The rule is positional, so it needs no allowlist of known artifacts."""
    assert is_internal_analysis_file("_shared_library.md")
    assert is_internal_analysis_file("_a_file_nobody_has_written_yet.md")
    assert not is_internal_analysis_file("Collectors.md")


def test_shared_library_digest_is_not_counted_as_a_group(tmp_path: Path) -> None:
    staging = _staging(
        tmp_path,
        {"_preprocess/G.md": 50},
        {
            "G.md": "## COMPONENTS\n- a. Locus: `_preprocess/G.md:1-10`.\n",
            "G_summary.md": "- See `G.md`.\n",
            "_shared_library.md": "# Shared library\n- Locus: `_preprocess/G.md:1-5`.\n",
        },
    )
    report = validate_loci(staging)

    assert report.files_scanned == 3
    assert report.group_analyses == ["G.md"]
    assert report.internal_files == ["_shared_library.md"]
    assert report.group_summaries == ["G_summary.md"]
    # The bug this rule exists to stop: three files, one group.
    assert len(report.group_analyses) == 1


def test_summary_without_an_analysis_is_reported_but_does_not_fail(tmp_path: Path) -> None:
    staging = _staging(
        tmp_path,
        {"_preprocess/Processors_part1.md": 50},
        {"Processors_summary.md": "- Design count: 98.\n- See the analysis.\n"},
    )
    report = validate_loci(staging)

    assert report.group_analyses == []
    assert report.group_summaries_without_analysis == ["Processors"]
    # Coverage is an observation here, not this gate's verdict.
    assert report.ok is True


# --------------------------------------------------------------------------- #
# Grammar -- the shapes a real run emits
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "citation,expected_ranges",
    [
        ("`_preprocess/G.md:41-80`", [(41, 80)]),
        ("`_preprocess/G.md:42`", [(42, 42)]),
        ("`_preprocess/G.md:1-10,20-30,40-50`", [(1, 10), (20, 30), (40, 50)]),
        ("`_preprocess/G.md`, lines 41-80", [(41, 80)]),
        ("`_preprocess/G.md` lines 41-80", [(41, 80)]),
        ("`_preprocess/G.md`, lines 41-80, 90-99", [(41, 80), (90, 99)]),
        ("`_preprocess/G.md` lines 41-80 and 90-99", [(41, 80), (90, 99)]),
        ("`_preprocess/G.md:90-EOF`", [(90, 100)]),
        ("`_preprocess/G.md:1-10, 20-30`", [(1, 10), (20, 30)]),
    ],
)
def test_real_citation_shapes_parse(
    tmp_path: Path, citation: str, expected_ranges: list[tuple[int, int]]
) -> None:
    staging = _staging(
        tmp_path,
        {"_preprocess/G.md": 100},
        {"G.md": f"## COMPONENTS\n- a claim. Basis: Confirmed. Locus: {citation}.\n"},
    )
    (parsed,) = citations_in(staging, "G.md")

    assert parsed.status == "resolved"
    assert [(r.start, r.end) for r in parsed.ranges] == expected_ranges

    report = validate_loci(staging)
    assert report.ok is True
    assert report.citations == 1
    assert report.resolved_ranges == len(expected_ranges)


def test_file_only_citation_is_weaker_not_broken(tmp_path: Path) -> None:
    staging = _staging(
        tmp_path,
        {"_preprocess/G.md": 100},
        {"G.md": "## COMPONENTS\n- a claim. Basis: `_preprocess/G.md`.\n"},
    )
    report = validate_loci(staging)

    assert report.file_only_citations == 1
    assert report.ranges == 0
    assert report.ok is True
    assert report.by_status["file_only"] == 1


# --------------------------------------------------------------------------- #
# The three checks
# --------------------------------------------------------------------------- #

def test_missing_file_out_of_range_and_degenerate_are_all_caught(tmp_path: Path) -> None:
    staging = _staging(
        tmp_path,
        {"_preprocess/G.md": 5},
        {
            "G.md": (
                "## COMPONENTS\n"
                "- ok. Locus: `_preprocess/G.md:1-3`.\n"
                "- past the end. Locus: `_preprocess/G.md:4-99`.\n"
                "- backwards. Locus: `_preprocess/G.md:5-2`.\n"
                "- zero. Locus: `_preprocess/G.md:0-2`.\n"
                "- gone. Locus: `_preprocess/Nope.md:1-2`.\n"
                "## BUSINESS_RULES\n"
                "- **BR-G-002**: y. Basis: `_preprocess/G.md`, lines 2-4, 900-901.\n"
            )
        },
    )
    report = validate_loci(staging)

    assert report.ok is False
    statuses = sorted(c.status for c in report.findings)
    assert statuses == ["degenerate", "degenerate", "missing_file", "out_of_range", "out_of_range"]
    assert report.by_status["resolved"] == 1


def test_eof_endpoint_resolves_to_the_last_line(tmp_path: Path) -> None:
    staging = _staging(
        tmp_path,
        {"_preprocess/G.md": 40},
        {
            "G.md": (
                "## COMPONENTS\n"
                "- in bounds. Locus: `_preprocess/G.md:30-EOF`.\n"
                "- start past the end. Locus: `_preprocess/G.md:41-EOF`.\n"
            )
        },
    )
    report = validate_loci(staging)

    assert report.eof_endpoints == 2
    assert [c.status for c in report.findings] == ["degenerate"]


# --------------------------------------------------------------------------- #
# Structured findings: file, section, claim id, citation, what failed
# --------------------------------------------------------------------------- #

def test_finding_carries_file_section_claim_and_citation(tmp_path: Path) -> None:
    staging = _staging(
        tmp_path,
        {"_preprocess/Collectors.md": 100},
        {
            "Collectors.md": (
                "# Collectors - Talend Group Analysis\n"
                "\n"
                "## COMPONENTS\n"
                "- fine. Locus: `_preprocess/Collectors.md:1-20`.\n"
                "\n"
                "## BUSINESS_RULES\n"
                "- **BR-COL-012** Expression: `remove(i)`. "
                "Basis/locus: `_preprocess/Collectors.md` lines 7524-8164.\n"
            )
        },
    )
    report = validate_loci(staging)

    (finding,) = report.findings
    assert finding.file == "Collectors.md"
    assert finding.section == "BUSINESS_RULES"
    assert finding.claim_id == "BR-COL-012"
    assert "7524-8164" in finding.citation
    assert finding.status == "out_of_range"
    assert "file has 100 lines" in finding.detail
    assert finding.line == 7
    assert "BR-COL-012" in finding.finding_line()


def test_claim_id_pattern_ignores_lookalikes(tmp_path: Path) -> None:
    """``ISO-8601-1`` is not a claim id; only BR/AP/RF are."""
    staging = _staging(
        tmp_path,
        {"_preprocess/G.md": 10},
        {"G.md": "## SCHEMA\n- ISO-8601-1 timestamps. Locus: `_preprocess/G.md:1-99`.\n"},
    )
    report = validate_loci(staging)

    (finding,) = report.findings
    assert finding.claim_id == ""


# --------------------------------------------------------------------------- #
# Bare-path resolution
# --------------------------------------------------------------------------- #

def test_bare_filename_resolves_against_preprocess_first(tmp_path: Path) -> None:
    """Measured on a real run: `_preprocess` first puts 104/104 bare ranges in
    bounds; `_analysis` first puts 100/104 out of bounds."""
    staging = _staging(
        tmp_path,
        {"_preprocess/Staging.md": 9514},
        {"Staging.md": "## COMPONENTS\n- a. Locus: `Staging.md:8754-EOF`.\n"},
    )
    report = validate_loci(staging)

    assert report.bare_path_citations == 1
    assert report.ok is True
    assert report.by_status["resolved"] == 1


def test_bare_filename_that_exists_nowhere_is_a_missing_file(tmp_path: Path) -> None:
    staging = _staging(
        tmp_path,
        {"_preprocess/Processors_part1.md": 50},
        {"Processors_summary.md": "- See `Processors.md`; part-specific evidence.\n"},
    )
    report = validate_loci(staging)

    (finding,) = report.findings
    assert finding.status == "missing_file"
    assert finding.path == "Processors.md"
    assert report.ok is False


# --------------------------------------------------------------------------- #
# Boundaries and robustness
# --------------------------------------------------------------------------- #

def test_citation_cannot_escape_the_staging_boundary(tmp_path: Path) -> None:
    """METHOD.md section 2 makes the staging directory an absolute boundary.

    A citation is untrusted model output. All three escape shapes must be
    MATCHED by the grammar and then REJECTED by resolution -- being unmatched
    is also safe, but it is safe by silence, and the citation would vanish
    from the report instead of being reported as unresolvable.
    """
    (tmp_path / "secret.md").write_text("nope\n")
    staging = _staging(
        tmp_path,
        {"_preprocess/G.md": 10},
        {
            "G.md": (
                "## COMPONENTS\n"
                "- relative escape. Locus: `../secret.md:1-1`.\n"
                "- absolute path. Locus: `/etc/passwd.md:1-1`.\n"
                "- escape through a real dir. Locus: `_preprocess/../../secret.md:1-1`.\n"
                "- legitimate. Locus: `_preprocess/G.md:1-5`.\n"
            )
        },
    )
    report = validate_loci(staging)

    assert len(report.findings) == 3, "every escape shape must be reported, not skipped"
    assert {f.status for f in report.findings} == {"outside_workspace"}
    assert {f.resolved_path for f in report.findings} == {""}
    assert [f.path for f in report.findings] == [
        "../secret.md",
        "/etc/passwd.md",
        "_preprocess/../../secret.md",
    ]
    # The legitimate citation beside them still resolves.
    assert report.by_status["resolved"] == 1
    assert report.ok is False


def test_boundary_rejection_never_reads_outside_the_workspace(tmp_path: Path) -> None:
    """The escaping path must not even be opened for a line count."""
    outside = tmp_path / "secret.md"
    outside.write_text("\n".join(str(n) for n in range(1, 1000)) + "\n")
    staging = _staging(
        tmp_path,
        {"_preprocess/G.md": 10},
        {"G.md": "## COMPONENTS\n- a. Locus: `../secret.md:1-999`.\n"},
    )

    (parsed,) = citations_in(staging, "G.md")

    assert parsed.status == "outside_workspace"
    # target_lines stays 0: a real read of secret.md would have set it to 999,
    # and an in-bounds verdict against an out-of-tree file is the failure mode.
    assert parsed.target_lines == 0
    assert parsed.ranges == []


def test_missing_analysis_directory_is_an_empty_report(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    report = validate_loci(staging)

    assert report.ok is True
    assert report.files_scanned == 0
    assert "no file+line loci cited" in report.summary_line()


def test_report_round_trips_through_json(tmp_path: Path) -> None:
    staging = _staging(
        tmp_path,
        {"_preprocess/G.md": 10},
        {"G.md": "## COMPONENTS\n- a. Locus: `_preprocess/G.md:1-99`.\n"},
    )
    report = validate_loci(staging)
    path = write_locus_gate_report(staging, report)

    assert path == staging / "_assessment" / "locus_gate.json"
    payload = json.loads(path.read_text())
    assert LocusGateReport.model_validate(payload).model_dump() == report.model_dump()
    assert payload["advisory"] is True
    assert payload["not_checked"], "the report must state what it does not check"


def test_messages_are_advisory_only(tmp_path: Path) -> None:
    staging = _staging(
        tmp_path,
        {"_preprocess/G.md": 10},
        {"G.md": "## COMPONENTS\n- a. Locus: `_preprocess/G.md:1-99`.\n"},
    )
    report = validate_loci(staging)
    messages = locus_gate_messages(report)

    assert report.ok is False
    assert {level for _message, level in messages} <= {"info", "warning"}
    assert any("locus_gate.json" in message for message, _level in messages)


def test_failure_statuses_are_exactly_the_ones_that_move_ok() -> None:
    assert "file_only" not in FAILURE_STATUSES
    assert FAILURE_STATUSES == {
        "missing_file",
        "out_of_range",
        "degenerate",
        "outside_workspace",
        "unparsed_range",
    }


def test_large_analysis_does_not_blow_up(tmp_path: Path) -> None:
    """Guard against catastrophic backtracking on the range-list grammar."""
    body = "## COMPONENTS\n" + "".join(
        f"- claim {n}. Locus: `_preprocess/G.md:{n}-{n + 5}`.\n" for n in range(1, 2000)
    )
    body += "- pathological: `_preprocess/G.md:" + ",".join("1-2" for _ in range(300)) + "`.\n"
    staging = _staging(tmp_path, {"_preprocess/G.md": 5000}, {"G.md": body})

    report = validate_loci(staging)

    assert report.citations == 2000
    assert report.ranges == 1999 + 300
    assert report.ok is True
