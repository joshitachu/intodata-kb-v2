"""Heading recognition must not depend on the level the writer chose.

Every test here is derived from one verified production failure (run 258,
project ``ettm_TEST_Claude``): the Phase 2 guide asked for ``### COMPONENTS``,
the model wrote ``## COMPONENTS`` and ``## 4. TRANSFORMATION_DETAIL``, and the
h3-only parser returned ``{}`` -- indistinguishable from a document with no
sections.  ~95% of the run's evidence was dropped with no warning anywhere.

The three properties under test, in order of how expensive they were:

* level-independence (the bug);
* exact matching (the near-miss fix: substring matching maps ``LOW`` onto
  ``EXECUTION_FLOW`` because "LOW" is inside "FLOW");
* the h4 guard (the regression a naive "accept any level" rewrite introduces:
  ``#### HIGH`` inside ``RISK_FLAGS`` must not end ``RISK_FLAGS``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mcp_server.providers.custom.analysis_parser import (
    ADDITIONAL_FINDINGS_SECTION,
    PHASE2_SECTION_NAMES,
    parse_phase2_sections,
)
from mcp_server.providers.custom.section_parser import (
    SectionContractError,
    allcaps_heading_name,
    heading_parts,
    iter_lines_outside_fences,
    known_section_at_any_level,
    normalise_heading,
    parse_named_sections,
)

# The real failing artifact. Present on the machine the failure was diagnosed
# on; absent in CI and on a clean checkout, hence the skip guard on the test
# that reads it. RUN_258_EXCERPT below reproduces its heading shapes verbatim so
# the property is pinned even when the file is gone.
RUN_258_ANALYSIS = (
    Path(__file__).resolve().parents[3]  # knowledge_base/
    / "output"
    / "ettm_TEST_Claude"
    / "docs_staging"
    / "_analysis"
    / "afas.md"
)

# Heading shapes copied from that file: h2 section names, h3 prose sub-headings
# ("Job: x", "CRITICAL Risks"), h4 enumerated risk entries.
RUN_258_EXCERPT = """# Analysis Report: afas

**Group:** afas

---

## Executive Summary

The `afas` group consists of 2 analyzed Talend job designs.

---

## COMPONENTS

### Job: afasLeaveRegistrationReader

| Component | Type |
|-----------|------|
| tMap_1 | tMap |

## EXECUTION_FLOW

### afasLeaveRegistrationReader

1. tRouteInput_2 receives the route input.

## 4. TRANSFORMATION_DETAIL

### afasLeaveRegistrationReader: tMap_1

Builds the SOAP envelope.

## RISK_FLAGS

### CRITICAL Risks

#### CR-001: Hardcoded SOAP Envelope with Token
- **Severity:** CRITICAL

### HIGH Risks

#### HR-001: Missing Error Handling
- **Severity:** HIGH
"""

_H2_SECTION_RE = re.compile(r"^## (?:\d+(?:\.\d+)*[.)]\s*)?([A-Z][A-Z0-9_ ]+)$")


def _to_h3(text: str) -> str:
    """Rewrite every all-caps h2 section heading as the h3 the guide asked for."""
    out: list[str] = []
    for line in text.splitlines():
        match = _H2_SECTION_RE.match(line.strip())
        out.append(f"### {match.group(1)}" if match else line)
    return "\n".join(out)


# --------------------------------------------------------------------------
# 1. the actual failure: h2 output must parse as the h3 output would have
# --------------------------------------------------------------------------


def test_h2_analysis_parses_identically_to_the_h3_form_it_should_have_been():
    h2_result = parse_phase2_sections(RUN_258_EXCERPT)
    h3_result = parse_phase2_sections(_to_h3(RUN_258_EXCERPT))

    assert h2_result == h3_result
    # and it is not the trivially-equal empty case that started all this
    assert {"COMPONENTS", "EXECUTION_FLOW", "TRANSFORMATION_DETAIL", "RISK_FLAGS"} <= set(
        h2_result
    )
    assert "tMap_1" in h2_result["COMPONENTS"]
    assert "SOAP envelope" in h2_result["TRANSFORMATION_DETAIL"]


def test_h2_analysis_is_not_swallowed_by_unclassified():
    """The old parser put the ENTIRE document under the catch-all."""
    result = parse_phase2_sections(RUN_258_EXCERPT)
    catch_all = result.get(ADDITIONAL_FINDINGS_SECTION, "")
    # only the title + executive summary preamble may land there
    assert "Executive Summary" in catch_all
    assert "tMap_1" not in catch_all
    assert len(catch_all) < len(RUN_258_EXCERPT) // 3


@pytest.mark.skipif(
    not RUN_258_ANALYSIS.exists(), reason="run-258 artifact not present on this machine"
)
def test_real_run_258_analysis_file_parses_to_the_same_sections_as_its_h3_form():
    text = RUN_258_ANALYSIS.read_text(encoding="utf-8")
    h2_sections = parse_phase2_sections(text)
    h3_sections = parse_phase2_sections(_to_h3(text))

    assert set(h2_sections) == set(h3_sections)
    # the 13 required Talend sections, as written by the model at h2
    assert {
        "COMPONENTS",
        "EXECUTION_FLOW",
        "DATA_FLOWS",
        "TRANSFORMATION_DETAIL",
        "BUSINESS_RULES",
        "RISK_FLAGS",
    } <= set(h2_sections)


# --------------------------------------------------------------------------
# 2. normalisation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("### COMPONENTS", "COMPONENTS"),
        ("## COMPONENTS", "COMPONENTS"),
        ("# COMPONENTS", "COMPONENTS"),
        ("###### COMPONENTS", "COMPONENTS"),
        ("## 4. TRANSFORMATION_DETAIL", "TRANSFORMATION_DETAIL"),
        ("## 10. TRANSFORMATION_DETAIL", "TRANSFORMATION_DETAIL"),
        ("## 2) TRANSFORMATION_DETAIL", "TRANSFORMATION_DETAIL"),
        ("## 4.1. TRANSFORMATION_DETAIL", "TRANSFORMATION_DETAIL"),
        ("  ###   COMPONENTS   ", "COMPONENTS"),
        ("### Cross Job Findings", "CROSS_JOB_FINDINGS"),
        ("### source sql", "SOURCE_SQL"),
    ],
)
def test_normalise_heading(line, expected):
    assert normalise_heading(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        "",
        "Just prose.",
        "####### seven hashes is not a heading",
        "#no space",
        "## ---",
        "- ### COMPONENTS",
    ],
)
def test_normalise_heading_rejects_non_headings(line):
    assert normalise_heading(line) is None


def test_heading_parts_reports_the_level():
    assert heading_parts("#### HIGH") == (4, "HIGH")
    assert heading_parts("## 4. TRANSFORMATION_DETAIL") == (2, "4. TRANSFORMATION_DETAIL")
    assert heading_parts("not a heading") is None


# --------------------------------------------------------------------------
# 3. exact matching -- never substring, in either direction
# --------------------------------------------------------------------------


def test_low_does_not_match_execution_flow():
    known = {"EXECUTION_FLOW", "DATA_FLOWS", "RISK_FLAGS"}
    assert known_section_at_any_level("### LOW", known) is None
    assert normalise_heading("### LOW") == "LOW"


@pytest.mark.parametrize(
    "line",
    ["### LOW", "#### LOW", "## LOW", "### FLOW", "### HIGH", "### SQL", "### DATA"],
)
def test_substring_shaped_headings_never_resolve_to_a_longer_section(line):
    known = set(PHASE2_SECTION_NAMES)
    resolved = known_section_at_any_level(line, known)
    assert resolved is None or resolved == normalise_heading(line)


def test_longer_heading_does_not_match_a_shorter_known_name():
    # containment the other way round: known name inside the heading name
    assert known_section_at_any_level("### RISK_FLAGS_SUMMARY", {"RISK_FLAGS"}) is None


# --------------------------------------------------------------------------
# 4. the h4 guard -- a naive "any level" rewrite regresses this
# --------------------------------------------------------------------------


def test_h4_allcaps_subheading_does_not_break_the_owning_section():
    text = (
        "### RISK_FLAGS\n\nSome risk.\n\n"
        "#### HIGH\n\nHigh risk detail.\n"
        "#### LOW\n\nLow detail.\n"
        "##### CRITICAL\n\nCritical detail."
    )
    result = parse_phase2_sections(text)
    assert "High risk detail" in result["RISK_FLAGS"]
    assert "Low detail" in result["RISK_FLAGS"]
    assert "Critical detail" in result["RISK_FLAGS"]
    assert result.get(ADDITIONAL_FINDINGS_SECTION, "") == ""


def test_non_allcaps_h3_subheading_does_not_break_the_owning_section():
    text = "## COMPONENTS\n\n### Job: orders\n\nDetail about orders.\n### CRITICAL Risks\n\nMore."
    result = parse_phase2_sections(text)
    assert "Detail about orders" in result["COMPONENTS"]
    assert "More." in result["COMPONENTS"]
    assert result.get(ADDITIONAL_FINDINGS_SECTION, "") == ""


def test_unknown_allcaps_h3_still_closes_the_section():
    """Preserved legacy behaviour: an unknown ``### NAME`` diverts to the catch-all."""
    text = "### COMPONENTS\n\ncomp content\n\n### SOMETHING_ELSE\n\nstray content"
    result = parse_phase2_sections(text)
    assert result["COMPONENTS"] == "comp content"
    assert "stray content" in result[ADDITIONAL_FINDINGS_SECTION]


def test_unknown_allcaps_h2_is_content_not_a_section_break():
    """h2 is where the model puts its own narrative headings; it must not divert."""
    text = "## COMPONENTS\n\ncomp content\n\n## SOMETHING ELSE\n\nstill component prose"
    result = parse_phase2_sections(text)
    assert "still component prose" in result["COMPONENTS"]
    assert result.get(ADDITIONAL_FINDINGS_SECTION, "") == ""


def test_allcaps_heading_name_level_and_enumeration_rules():
    assert allcaps_heading_name("### COMPONENTS", min_level=3, max_level=3) == "COMPONENTS"
    assert allcaps_heading_name("#### HIGH", min_level=3, max_level=3) is None
    assert allcaps_heading_name("### Job: orders") is None
    assert allcaps_heading_name("## 4. COMPONENTS") is None
    assert allcaps_heading_name("## 4. COMPONENTS", allow_enumerated=True) == "COMPONENTS"
    assert allcaps_heading_name("#### HIGH") == "HIGH"


# --------------------------------------------------------------------------
# 5. strict mode -- lose everything, raise; never return empty
# --------------------------------------------------------------------------


_NO_KNOWN_SECTIONS = (
    "# Analysis Report: orders\n\n"
    + "## Overview\n\nThe writer ignored the section contract entirely and wrote "
    "free prose about the orders group, at length, with no section name anywhere "
    "in the document that the parser has ever heard of.\n\n" * 4
)


def test_strict_raises_when_a_substantive_document_yields_no_known_section():
    assert len(re.sub(r"\s+", "", _NO_KNOWN_SECTIONS)) > 200
    with pytest.raises(SectionContractError):
        parse_phase2_sections(_NO_KNOWN_SECTIONS, strict=True)


def test_default_is_not_strict_so_no_existing_caller_changes_behaviour():
    result = parse_phase2_sections(_NO_KNOWN_SECTIONS)
    assert set(result) == {ADDITIONAL_FINDINGS_SECTION}


def test_strict_tolerates_a_short_document():
    """Below the substance floor, "no sections" is a plausible truth, not a defect."""
    assert parse_phase2_sections("# Notes\n\nNothing to report.", strict=True) != {}


def test_strict_passes_once_one_known_section_matched_at_any_level():
    text = _NO_KNOWN_SECTIONS + "\n## COMPONENTS\n\ntMap_1 maps orders.\n"
    result = parse_phase2_sections(text, strict=True)
    assert result["COMPONENTS"] == "tMap_1 maps orders."


def test_parse_named_sections_strict_raises_instead_of_returning_empty():
    known = {"COMPONENTS", "RISK_FLAGS"}
    assert parse_named_sections(_NO_KNOWN_SECTIONS, known) == {}
    with pytest.raises(SectionContractError) as excinfo:
        parse_named_sections(_NO_KNOWN_SECTIONS, known, strict=True)
    # the message must name what it saw and what it wanted, or the operator is
    # back to guessing why the evidence vanished
    assert "OVERVIEW" in str(excinfo.value)
    assert "COMPONENTS" in str(excinfo.value)


# --------------------------------------------------------------------------
# 6. the generic splitter
# --------------------------------------------------------------------------


def test_parse_named_sections_splits_at_any_level_and_drops_the_preamble():
    text = (
        "# Title\n\npreamble\n\n"
        "## COMPONENTS\n\ncomp body\n\n"
        "#### RISK_FLAGS\n\nrisk body\n"
    )
    assert parse_named_sections(text, {"COMPONENTS", "RISK_FLAGS"}) == {
        "COMPONENTS": "comp body",
        "RISK_FLAGS": "risk body",
    }


def test_parse_named_sections_reports_a_declared_but_empty_section():
    result = parse_named_sections(
        "## COMPONENTS\n\n## RISK_FLAGS\n\nrisk", {"COMPONENTS", "RISK_FLAGS"}
    )
    assert result == {"COMPONENTS": "", "RISK_FLAGS": "risk"}


def test_parse_named_sections_concatenates_a_repeated_section():
    text = "## COMPONENTS\n\nfirst\n\n## RISK_FLAGS\n\nrisk\n\n## COMPONENTS\n\nsecond\n"
    result = parse_named_sections(text, {"COMPONENTS", "RISK_FLAGS"})
    components = result["COMPONENTS"]
    assert components.split() == ["first", "second"]  # document order, no interleaving
    assert "risk" not in components
    assert result["RISK_FLAGS"] == "risk"


# --------------------------------------------------------------------------
# 7. determinism
# --------------------------------------------------------------------------


def test_repeated_parses_are_byte_identical():
    first = parse_phase2_sections(RUN_258_EXCERPT)
    for _ in range(5):
        assert parse_phase2_sections(RUN_258_EXCERPT) == first
    known = frozenset(PHASE2_SECTION_NAMES)
    generic = parse_named_sections(RUN_258_EXCERPT, known)
    for _ in range(5):
        assert parse_named_sections(RUN_258_EXCERPT, known) == generic
    assert list(parse_named_sections(RUN_258_EXCERPT, known)) == list(generic)


# --------------------------------------------------------------------------
# 8. fenced code blocks are not headings
#
# Recognising names at every level widened an existing hole: under the old
# h3-only rule a code sample containing "# COMPONENTS" was inert; under the new
# rule it would open a section and truncate the real one.  The standalone
# bundler (scripts/bundle_analysis.py) implements the same guard -- these tests
# pin the two implementations to the same behaviour.
# --------------------------------------------------------------------------


def test_heading_inside_backtick_fence_does_not_open_a_section():
    text = (
        "## COMPONENTS\nreal body\n\n```\n# COMPONENTS\n## RISK_FLAGS\n```\ntail\n"
    )
    result = parse_phase2_sections(text)
    assert "RISK_FLAGS" not in result
    assert "# COMPONENTS" in result["COMPONENTS"]  # sample retained verbatim
    assert "tail" in result["COMPONENTS"]


def test_backticks_do_not_close_a_tilde_fence():
    text = "## COMPONENTS\nbody\n~~~\n```\n## RISK_FLAGS\n~~~\ntail\n"
    assert "RISK_FLAGS" not in parse_phase2_sections(text)


def test_parse_named_sections_is_fence_aware_too():
    known = frozenset({"COMPONENTS", "RISK_FLAGS"})
    text = "## COMPONENTS\nbody\n```\n## RISK_FLAGS\n```\n"
    assert "RISK_FLAGS" not in parse_named_sections(text, known)


def test_iter_lines_outside_fences_marks_delimiters_as_fenced():
    text = "a\n```\nb\n```\nc"
    assert list(iter_lines_outside_fences(text)) == [
        ("a", False),
        ("```", True),
        ("b", True),
        ("```", True),
        ("c", False),
    ]


def test_real_run_258_analyses_contain_no_headings_inside_fences():
    """The parity fix must change nothing on the data it was derived from."""
    analysis_dir = RUN_258_ANALYSIS.parent
    if not analysis_dir.is_dir():
        pytest.skip(f"run-258 analysis not present at {analysis_dir}")
    for path in sorted(analysis_dir.glob("*.md")):
        if path.name.startswith("_") or path.name.endswith("_summary.md"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        fenced_headings = [
            line
            for line, in_fence in iter_lines_outside_fences(text)
            if in_fence and normalise_heading(line) is not None
        ]
        assert not fenced_headings, f"{path.name}: {fenced_headings[:3]}"
