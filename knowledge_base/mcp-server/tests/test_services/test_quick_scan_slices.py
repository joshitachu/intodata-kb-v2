"""Token-cost optimisations to the claude-cli / opencode-cli generation lane.

Three measured wastes, all verified against run 258 of project ``ettm_TEST_Claude``:

1. ``_assessment/quick_scan.md`` is 34.3 KB and was read in full by each of 20
   Phase 2 sub-agents (688 KB) so each could use its own ~1.9 KB of flags.
2. Phase 2.6 spawns a scoring sub-agent that reads EVERY group analysis (520 KB)
   to write one advisory file.  ``config.docgen_llm_scorecard_enabled`` already
   defaults to False after that phase was measured in the custom lane at ~0.8M
   tokens with contract gaps -- but this lane hardcoded Phase 2.6 into its
   prompt, so the flag had no effect here.
3. The staged files the model reads were not themselves ASCII-clean, and the
   model mirrors what it is shown: ``quick_scan.md`` emitted a U+2014 em dash,
   and ``_doc_templates.md`` stated the "no Unicode box-drawing" rule by
   printing the box-drawing characters.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from mcp_server.config import config
from mcp_server.providers.base import DocGenContext
from mcp_server.providers.claude_cli import ClaudeCliProvider
from mcp_server.services.claude_service import DocGenerationCoordinator

RUN_258_PREPROCESS = (
    Path(__file__).resolve().parents[3]
    / "output"
    / "ettm_TEST_Claude"
    / "docs_staging"
    / "_preprocess"
)


def _staging_with_groups(tmp_path: Path, groups: dict[str, str]) -> Path:
    staging = tmp_path / "docs_staging"
    (staging / "_preprocess").mkdir(parents=True)
    for name, content in groups.items():
        (staging / "_preprocess" / f"{name}.md").write_text(content, encoding="utf-8")
    return staging


def _job_block(job: str, components: int, *, error_handler: bool = False) -> str:
    lines = [f"## Talend Job: {job}\n\n"]
    for index in range(components):
        comp = "tLogCatcher_1 (tLogCatcher)" if error_handler and index == 0 else f"tMap_{index} (tMap)"
        lines.append(f"#### {comp}\n")
        lines.append("- context.someVar\n")
    return "".join(lines)


# --------------------------------------------------------------------------
# 1. Per-group quick-scan slices
# --------------------------------------------------------------------------


def test_every_group_gets_a_slice_even_with_no_findings(tmp_path: Path) -> None:
    groups = {
        "busy": _job_block("bigJob", 80),
        "quiet": "## Talend Job: tiny\n\n#### tJava_1 (tJava)\n- context.x\n",
    }
    staging = _staging_with_groups(tmp_path, groups)
    DocGenerationCoordinator._run_quick_scan(staging, sorted(groups))

    slice_dir = staging / "_assessment" / "quick_scan"
    assert {p.stem for p in slice_dir.glob("*.md")} == set(groups)


def test_a_slice_carries_only_its_own_group(tmp_path: Path) -> None:
    groups = {
        "alpha": _job_block("alphaGod", 90),
        "beta": _job_block("betaGod", 95),
    }
    staging = _staging_with_groups(tmp_path, groups)
    DocGenerationCoordinator._run_quick_scan(staging, sorted(groups))

    alpha = (staging / "_assessment" / "quick_scan" / "alpha.md").read_text()
    assert "alphaGod" in alpha
    assert "betaGod" not in alpha, "a slice must not leak a sibling group's findings"


def test_a_group_with_no_findings_says_so_rather_than_being_empty(tmp_path: Path) -> None:
    """Absence must be stated. An empty file reads as a scan failure."""
    groups = {"quiet": "## Talend Job: tiny\n\n#### tJava_1 (tJava)\n- context.x\n"}
    staging = _staging_with_groups(tmp_path, groups)
    DocGenerationCoordinator._run_quick_scan(staging, ["quiet"])

    text = (staging / "_assessment" / "quick_scan" / "quiet.md").read_text()
    assert text.strip(), "slice must never be empty"
    assert "No anti-pattern heuristic fired" in text
    assert "real result" in text


def test_slices_are_built_from_findings_not_by_reparsing_the_report(tmp_path: Path) -> None:
    """Deleting the roll-up must not change the slices.

    The slices are written from the in-memory findings. Re-reading our own
    generated markdown to rebuild them is the coupling that cost run 258 its
    evidence, so this pins that the dependency does not exist.
    """
    groups = {"alpha": _job_block("alphaGod", 90)}
    first = _staging_with_groups(tmp_path / "a", groups)
    DocGenerationCoordinator._run_quick_scan(first, ["alpha"])
    expected = (first / "_assessment" / "quick_scan" / "alpha.md").read_text()

    second = _staging_with_groups(tmp_path / "b", groups)
    DocGenerationCoordinator._run_quick_scan(second, ["alpha"])
    (second / "_assessment" / "quick_scan.md").unlink()
    assert (second / "_assessment" / "quick_scan" / "alpha.md").read_text() == expected


def test_slices_are_deterministic(tmp_path: Path) -> None:
    groups = {"alpha": _job_block("alphaGod", 90), "beta": _job_block("betaJob", 12)}
    runs = []
    for run in range(2):
        staging = _staging_with_groups(tmp_path / f"r{run}", groups)
        DocGenerationCoordinator._run_quick_scan(staging, sorted(groups))
        runs.append(
            {
                p.name: p.read_text()
                for p in sorted((staging / "_assessment" / "quick_scan").glob("*.md"))
            }
        )
    assert runs[0] == runs[1]


@pytest.mark.skipif(not RUN_258_PREPROCESS.is_dir(), reason="run-258 artifacts absent")
def test_measured_saving_on_the_real_estate(tmp_path: Path) -> None:
    """The reason this change exists, measured rather than asserted."""
    staging = tmp_path / "docs_staging"
    (staging / "_preprocess").mkdir(parents=True)
    for src in RUN_258_PREPROCESS.glob("*.md"):
        shutil.copy2(src, staging / "_preprocess" / src.name)

    groups = sorted(
        p.stem for p in (staging / "_preprocess").glob("*.md") if not p.name.startswith("_")
    )
    DocGenerationCoordinator._run_quick_scan(staging, groups)

    full = (staging / "_assessment" / "quick_scan.md").stat().st_size
    slices = list((staging / "_assessment" / "quick_scan").glob("*.md"))
    assert len(slices) == len(groups) == 20

    before = full * len(groups)          # every agent read the whole report
    after = sum(p.stat().st_size for p in slices)
    assert after < before * 0.10, (
        f"expected >90% reduction, got {100 * (before - after) / before:.0f}% "
        f"({before} -> {after} bytes)"
    )


# --------------------------------------------------------------------------
# 2. The scorecard flag actually applies to this lane
# --------------------------------------------------------------------------


def _ctx(staging: Path) -> DocGenContext:
    return DocGenContext(
        project_id="p",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(staging / "logs"),
        preprocess_groups=["alpha", "beta"],
        total_jobs=142,
        total_groups=2,
        effective_model="m",
        write_model="m",
        platform="talend",
        analysis_guide_file="_analysis_guide.md",
        enterprise_mode=False,
        resume_state=None,
        scoped_docs=None,
    )


@pytest.mark.parametrize("enabled", [True, False])
def test_phase_26_follows_the_config_flag(tmp_path: Path, monkeypatch, enabled: bool) -> None:
    monkeypatch.setattr(config, "docgen_llm_scorecard_enabled", enabled, raising=False)
    prompt = ClaudeCliProvider._build_prompt(_ctx(tmp_path))
    assert "{phase26_block}" not in prompt, "template slot left unsubstituted"
    if enabled:
        assert "PHASE 2.6 -- SCORE & ASSESS" in prompt
        assert "PHASE 2.6 -- SKIPPED" not in prompt
    else:
        assert "PHASE 2.6 -- SKIPPED" in prompt
        assert "PHASE 2.6 -- SCORE & ASSESS" not in prompt


def test_skip_block_forbids_inventing_scores(tmp_path: Path, monkeypatch) -> None:
    """Removing the scorecard must not mean documents make numbers up."""
    monkeypatch.setattr(config, "docgen_llm_scorecard_enabled", False, raising=False)
    prompt = ClaudeCliProvider._build_prompt(_ctx(tmp_path))
    assert "Do not invent a number" in prompt
    assert "scorecard-derived dimensions" in prompt


def test_omitted_marker_states_absence_explicitly(tmp_path: Path) -> None:
    DocGenerationCoordinator._write_omitted_scorecard_marker(tmp_path, ["a", "b"], 142)
    text = (tmp_path / "_assessment" / "scorecard.md").read_text()
    assert "SCORECARD OMITTED" in text
    assert "142 designs across 2 groups" in text
    assert "Do not invent a number" in text


def test_omitted_marker_carries_nothing_a_client_may_not_see(tmp_path: Path) -> None:
    """The marker is read by doc writers, so its wording lands in client prose.

    Verified leak: the previous wording embedded the operator flag name, and
    published client documents (07_QUALITY_SECURITY_RISK_SCORECARD.md and
    09_EXECUTIVE_SUMMARY.md of a real run) contain the sentence verbatim,
    internal environment variable and all. Anything a writer might copy has to
    be client-safe, and the marker now also tells it not to copy at all.
    """
    DocGenerationCoordinator._write_omitted_scorecard_marker(tmp_path, ["a"], 7)
    text = (tmp_path / "_assessment" / "scorecard.md").read_text()

    assert "MCP_DOCGEN_LLM_SCORECARD_ENABLED" not in text
    assert not re.search(r"\bMCP_[A-Z0-9_]+\b", text), "no internal flag name may appear"
    assert "Do not copy this notice" in text

    from mcp_server.services.client_language_guard import leakage_findings

    assert leakage_findings(text) == []


# --------------------------------------------------------------------------
# 3. What we stage must be ASCII -- the model mirrors what it is shown
# --------------------------------------------------------------------------


def test_generated_quick_scan_files_are_ascii(tmp_path: Path) -> None:
    groups = {"alpha": _job_block("alphaGod", 90), "beta": _job_block("betaJob", 4)}
    staging = _staging_with_groups(tmp_path, groups)
    DocGenerationCoordinator._run_quick_scan(staging, sorted(groups))
    for path in (staging / "_assessment").rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        offenders = {f"U+{ord(c):04X}" for c in text if ord(c) > 127}
        assert not offenders, f"{path.name} contains {offenders}"


def test_doc_templates_state_the_ascii_rule_without_violating_it(tmp_path: Path) -> None:
    """The rule used to be written using the characters it forbids."""
    preprocess = tmp_path / "_preprocess"
    preprocess.mkdir(parents=True)
    DocGenerationCoordinator._write_doc_templates(preprocess)
    text = (preprocess / "_doc_templates.md").read_text(encoding="utf-8")
    offenders = {f"U+{ord(c):04X}" for c in text if ord(c) > 127}
    assert not offenders, f"_doc_templates.md contains {offenders}"
    assert "ASCII" in text


def test_prompt_is_ascii_and_points_at_the_per_group_slice(tmp_path: Path) -> None:
    prompt = ClaudeCliProvider._build_prompt(_ctx(tmp_path))
    assert not any(ord(c) > 127 for c in prompt)
    assert "quick_scan/{group}.md" in prompt
