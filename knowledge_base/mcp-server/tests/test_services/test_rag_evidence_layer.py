"""The Phase 2 analyses must survive the run as the RAG evidence layer.

Measured on run 258 (`ettm_TEST_Claude`), both corpora checked against the
scan:

=========================== ============ =================
metric                      Phase 3 docs Phase 2 analyses
=========================== ============ =================
size                              301 KB           518 KB
designs named                 105 (74%)        138 (97%)
components named               97 (19%)        343 (69%)
quoted code that is verbatim        ~0%              62%
=========================== ============ =================

The analyses are the stronger retrieval corpus by every one of those measures,
and on the claude-cli / opencode lane they were deleted at cleanup and never
reached RAG: `build_rag_support_package` was called only from the CUSTOM lane's
`pipeline/phases/phase4_cleanup`, so `RagBridge.sync_internal_layers` -- which
runs for every lane -- found nothing under `docs/_rag_support/`.

These tests pin the two things that make the fix real rather than plausible:

1. the package is built from BOTH Phase 2 layouts, in the last moment the
   analyses exist, and survives the atomic swap into `docs/` where the
   lane-agnostic collector finds it;
2. it is advisory and it is SKIPPED when there is nothing to package -- the
   custom lane has already built its own package and deleted `_analysis/` by
   the time this hook runs, and `build_rag_support_package` recreates the
   package directory from scratch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.providers.custom.analysis_store import (
    SectionContractError,
    write_section,
    write_summary,
)
from mcp_server.services import claude_service
from mcp_server.services.claude_service import DocGenerationCoordinator
from mcp_server.services.client_language_guard import REDACTED_CATEGORIES, leakage_findings
from mcp_server.services.rag_support_package import collect_internal_artifacts

_finalize = DocGenerationCoordinator._finalize_memory_and_language

_BODY = "# analysis\n" + ("x" * 2000)

#: Run 258's real staging tree, 20 flat group analyses. Present in a working
#: tree, absent in CI -- every test that uses it skips rather than failing.
RUN_258_ANALYSIS = (
    Path(__file__).resolve().parents[2].parent
    / "output"
    / "ettm_TEST_Claude"
    / "docs_staging"
    / "_analysis"
)


def _staging(tmp_path: Path, *, flat: list[str] = (), directory: list[str] = ()) -> Path:
    staging = tmp_path / "docs_staging"
    analysis = staging / "_analysis"
    preprocess = staging / "_preprocess"
    analysis.mkdir(parents=True)
    preprocess.mkdir(parents=True)
    for group in list(flat) + list(directory):
        (preprocess / f"{group}.md").write_text("evidence\n", encoding="utf-8")
    for group in flat:
        (analysis / f"{group}.md").write_text(_BODY, encoding="utf-8")
        (analysis / f"{group}_summary.md").write_text("summary\n", encoding="utf-8")
    for group in directory:
        write_section(analysis, group, "COMPONENTS", "tMSSqlInput_1 reads dbo.Orders.\n")
        write_section(analysis, group, "SOURCE_SQL", "SELECT OrderId FROM dbo.Orders\n")
        write_summary(analysis, group, "summary\n")
    return staging


def _run(staging: Path, tmp_path: Path, groups: list[str]) -> list[tuple[str, str]]:
    return _finalize(
        output_dir=tmp_path / "out",
        staging_dir=staging,
        memory_stage=None,
        groups=groups,
        success=True,
        project_id="proj",
        platform="talend",
    )


def _package(staging: Path) -> Path:
    return staging / "_rag_support" / "phase2_analysis"


# --------------------------------------------------------------------------- #
# The package is built before the evidence is destroyed
# --------------------------------------------------------------------------- #

def test_the_analyses_are_packaged_before_the_hook_deletes_them(
    tmp_path: Path,
) -> None:
    staging = _staging(tmp_path, flat=["afas", "fourps"])

    messages = _run(staging, tmp_path, ["afas", "fourps"])

    # The working evidence is gone, as it was before...
    assert not (staging / "_analysis").exists()
    assert not (staging / "_preprocess").exists()
    # ...and the analyses survive it.
    assert sorted(p.name for p in _package(staging).iterdir()) == [
        "afas.md",
        "fourps.md",
    ]
    assert (_package(staging) / "afas.md").read_text(encoding="utf-8") == _BODY
    line = next(t for t, _l in messages if t.startswith("RAG evidence layer:"))
    assert "2 group analysis artifact(s)" in line
    assert "technical_analysis" in line


def test_the_package_is_built_from_the_per_section_directory_layout(
    tmp_path: Path,
) -> None:
    staging = _staging(tmp_path, directory=["afas"])

    _run(staging, tmp_path, ["afas"])

    text = (_package(staging) / "afas.md").read_text(encoding="utf-8")
    assert "### COMPONENTS" in text
    assert "tMSSqlInput_1 reads dbo.Orders." in text
    manifest = json.loads(
        (staging / "_rag_support" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["artifacts"][0]["layout"] == "directory"
    assert manifest["artifacts"][0]["sections"] == ["COMPONENTS", "SOURCE_SQL"]


def test_a_group_with_no_analysis_is_named_not_silently_dropped(
    tmp_path: Path,
) -> None:
    staging = _staging(tmp_path, flat=["afas"])
    (staging / "_preprocess" / "utils.md").write_text("evidence\n", encoding="utf-8")

    messages = _run(staging, tmp_path, ["afas", "utils"])

    warnings = [t for t, level in messages if level == "warning"]
    assert any("No analysis to publish for: utils" in t for t in warnings)


def test_a_split_group_slice_is_packaged_under_its_own_name(tmp_path: Path) -> None:
    """``_preprocess/USA_part1.md`` and ``_part2`` are two slices of ONE
    analysis group, and ``analysis_group_for_slice`` is what maps them back.
    The coverage line and the package must ask for the same set."""
    staging = _staging(tmp_path, flat=["USA"])

    messages = _run(staging, tmp_path, ["USA_part1", "USA_part2"])

    assert [p.name for p in _package(staging).iterdir()] == ["USA.md"]
    assert not any("No analysis to publish" in t for t, _l in messages)


# --------------------------------------------------------------------------- #
# It never costs the run anything
# --------------------------------------------------------------------------- #

def test_the_custom_lanes_own_package_is_not_replaced_by_an_empty_one(
    tmp_path: Path,
) -> None:
    """The custom lane builds the package in its Phase 4 and deletes
    `_analysis/` itself, so by the time this hook runs there is nothing left
    to package. `build_rag_support_package` recreates the package directory
    from scratch, so running it anyway would destroy a good package."""
    staging = tmp_path / "docs_staging"
    package = _package(staging)
    package.mkdir(parents=True)
    (package / "fourps.md").write_text("# built by the custom lane\n", encoding="utf-8")

    messages = _run(staging, tmp_path, ["fourps"])

    assert [p.name for p in package.iterdir()] == ["fourps.md"]
    assert (package / "fourps.md").read_text(encoding="utf-8") == (
        "# built by the custom lane\n"
    )
    assert not any(t.startswith("RAG evidence layer:") for t, _l in messages)


@pytest.mark.parametrize(
    "error",
    [
        OSError("disk full"),
        # SectionContractError derives from Exception, not ValueError. This
        # step runs on the SUCCESS path inside generate_docs's try block, so a
        # narrow catch here would turn a run that produced its documents into
        # a failed one over a preservation step that delivers nothing.
        SectionContractError("no known analysis section in broken/"),
    ],
    ids=["oserror", "section_contract_error"],
)
def test_a_packaging_failure_is_a_warning_and_the_run_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    staging = _staging(tmp_path, flat=["afas"])

    def _boom(*_args: object, **_kwargs: object) -> dict:
        raise error

    monkeypatch.setattr(claude_service, "build_rag_support_package", _boom)

    messages = _run(staging, tmp_path, ["afas"])

    assert any(
        t == f"RAG evidence layer could not be built: {error}" and level == "warning"
        for t, level in messages
    )
    # The rest of the method still ran: the evidence is still cleaned up.
    assert not (staging / "_analysis").exists()


def test_a_failed_run_keeps_its_evidence_and_builds_no_package(
    tmp_path: Path,
) -> None:
    """A failed run's staging is what resume reads. Nothing is packaged and
    nothing is deleted."""
    staging = _staging(tmp_path, flat=["afas"])

    _finalize(
        output_dir=tmp_path / "out",
        staging_dir=staging,
        memory_stage=None,
        groups=["afas"],
        success=False,
        project_id="proj",
        platform="talend",
    )

    assert (staging / "_analysis" / "afas.md").is_file()
    assert not _package(staging).exists()


# --------------------------------------------------------------------------- #
# End to end: staging -> docs/ -> the lane-agnostic collector
# --------------------------------------------------------------------------- #

def test_the_package_reaches_the_internal_collector_through_the_atomic_swap(
    tmp_path: Path,
) -> None:
    """The claim this slice rests on: the publishing machinery already exists
    and is lane-agnostic. `swap_docs` renames staging onto `docs/`, and
    `collect_internal_artifacts` (called by `RagBridge.sync_internal_layers`
    for EVERY lane) reads it from there."""
    from mcp_server.services.atomic_swap import swap_docs

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    staging = _staging(output_dir, flat=["afas"])

    _finalize(
        output_dir=output_dir,
        staging_dir=staging,
        memory_stage=None,
        groups=["afas"],
        success=True,
        project_id="proj",
        platform="talend",
    )
    swap_docs(output_dir)

    artifacts = collect_internal_artifacts(
        output_dir / "docs", project_id="proj", run_id=258
    )
    analysis = [
        a for a in artifacts
        if a["metadata"]["source_layer"] == "technical_analysis"
    ]
    assert [a["filename"] for a in analysis] == ["_internal/technical_analysis/afas.md"]
    assert analysis[0]["metadata"]["group"] == "afas"
    assert analysis[0]["metadata"]["run_id"] == 258


# --------------------------------------------------------------------------- #
# Run 258's real analyses
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(
    not RUN_258_ANALYSIS.is_dir(), reason="run 258 staging tree not present"
)
def test_run_258s_real_analyses_package_completely(tmp_path: Path) -> None:
    """20 groups, 20 artifacts, and the `_bundle_*` / `_doc_pack_*` / `_index`
    runtime artifacts sharing that directory are never mistaken for one."""
    import shutil

    from mcp_server.services.rag_support_package import build_rag_support_package

    staging = tmp_path / "docs_staging"
    shutil.copytree(RUN_258_ANALYSIS, staging / "_analysis")
    groups = [
        "Global", "MessageLogging", "Misc", "Monitoring", "TestTools", "VRI",
        "afas", "afasFourps", "alertErrorsEmail", "forSuppliers",
        "forSuppliersFourps", "fourps", "fourpsForSuppliers", "fourpsMaximo",
        "manualFixJobs", "maximo", "maximoFourps", "mockApiService",
        "monitoring_part_ci1", "utils",
    ]

    manifest = build_rag_support_package(
        staging, groups, project_id="ettm_TEST_Claude", platform="talend"
    )

    assert len(manifest["artifacts"]) == 20
    assert manifest["missing_groups"] == []
    assert manifest["failed_slices"] == []
    assert all(a["layout"] == "flat" for a in manifest["artifacts"])
    assert sum(a["bytes"] for a in manifest["artifacts"]) > 500_000


@pytest.mark.skipif(
    not RUN_258_ANALYSIS.is_dir(), reason="run 258 staging tree not present"
)
def test_run_258s_analyses_leak_little_and_what_leaks_is_removed() -> None:
    """The number slice 8 turns on, measured rather than predicted.

    8 findings across 532 KB of analysis -- against 36 in the nine documents
    the SAME run published. The analyses are the cleaner corpus, which is the
    opposite of what the plan assumed, so the case for the rewrite is not
    volume: it is that "internal" does not mean "unquotable" downstream.

    Of the 8, five are mechanical and are removed: `/_preprocess/...`,
    `/_assessment/quick_scan.md` (which is one path carrying two markers) and
    `quick_scan.md` twice more. Three are left alone and reported -- two are
    `C:\\Users\\...\\Documents`, the VALUE of the client's own
    `fourps_subscriptionsBasePath` context variable, and one is a
    "**Generated:** Phase 2 Analysis" header line, which is prose.
    """
    from mcp_server.providers.custom import analysis_store
    from mcp_server.services.client_language_guard import redact_internal_language

    findings = []
    for group in analysis_store.list_groups(RUN_258_ANALYSIS):
        text = analysis_store.read_group_analysis(RUN_258_ANALYSIS, group)
        findings.extend(leakage_findings(text, document=group))

    assert len(findings) == 8, [
        f"{f.document}:{f.line} [{f.category}/{f.marker}]" for f in findings
    ]
    redactable = [f for f in findings if f.category in REDACTED_CATEGORIES]
    assert len(redactable) == 5
    assert sorted({f.category for f in findings} - REDACTED_CATEGORIES) == [
        "absolute_path",
        "self_reference",
    ]

    # None of the five survives the publication boundary.
    survivors = []
    for group in analysis_store.list_groups(RUN_258_ANALYSIS):
        text = analysis_store.read_group_analysis(RUN_258_ANALYSIS, group)
        redacted, _count = redact_internal_language(text)
        survivors.extend(
            f for f in leakage_findings(redacted, document=group)
            if f.category in REDACTED_CATEGORIES
        )
    assert survivors == []
