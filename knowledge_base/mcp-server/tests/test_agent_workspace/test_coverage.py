"""Slice 8: the documentation-reach gauge, and the guarantee that it is only a gauge.

Run 250 published eight documents and reported success; establishing that the
narrative described roughly fifteen of a hundred and thirty-nine designs took a
three-agent investigation. One deterministic count belongs on the run log.

The other half of these tests is the constraint plan decision 10 and PRD 10.2 /
15.3 put on that count: it may not block, shape or be read by publication, it is
not a completion criterion, and it is NOT the agent-owned coverage register at
`notes/coverage.json`. The lane exists because prose-to-identifier comparisons
were failing finished runs.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp_server.providers.agent_workspace import contract
from mcp_server.providers.agent_workspace.coverage import (
    REACH_ARTIFACT,
    measure_documentation_reach,
    write_reach_artifact,
)


def _navigation(staging: Path, view: str, rows: list[dict]) -> None:
    directory = staging / contract.NAVIGATION / view
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "part_001.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )


def _estate(staging: Path) -> None:
    _navigation(
        staging,
        "units",
        [
            {"view": "units", "unit_id": "u1", "display_name": "Orders_Load"},
            {"view": "units", "unit_id": "u2", "display_name": "Orders_LoadDelta"},
            {"view": "units", "unit_id": "u3", "display_name": "Invoices_Sync"},
        ],
    )
    _navigation(
        staging,
        "channel_pairings",
        [
            {"view": "channel_pairings", "channel": "queue.orders"},
            {"view": "channel_pairings", "channel": "queue.invoices"},
        ],
    )
    _navigation(
        staging,
        "routine_usage",
        [
            {"view": "routine_usage", "unit_id": "u1", "routine": "DateUtils"},
            {"view": "routine_usage", "unit_id": "u2", "routine": "DateUtils"},
            {"view": "routine_usage", "unit_id": "u3", "routine": "VaultLookup"},
        ],
    )
    _navigation(
        staging,
        "mappings",
        [
            {"view": "mappings", "table_id": "t1", "unit_id": "u1",
             "display_name": "Orders_Load", "written_fields": 40},
            {"view": "mappings", "table_id": "t2", "unit_id": "u2",
             "display_name": "Orders_LoadDelta", "written_fields": 30},
            {"view": "mappings", "table_id": "t3", "unit_id": "u3",
             "display_name": "Invoices_Sync", "written_fields": 30},
        ],
    )


def _by_name(report) -> dict:
    return {item.name: item for item in report.classes}


def test_mentioned_and_sectioned_are_different_counts(tmp_path):
    _estate(tmp_path)
    documents = {
        "00_OVERVIEW.md": (
            "# Overview\n\n"
            "## Orders_Load\n\n"
            "The nightly load reads from queue.orders and writes the order header.\n"
            "It calls Orders_LoadDelta afterwards and shares nothing with "
            "Invoices_Sync.\n"
        )
    }

    report = measure_documentation_reach(tmp_path, documents)
    classes = _by_name(report)

    # All three designs are MENTIONED; only the one with a heading of its own
    # is sectioned. That distinction is the whole signal a thin run produces.
    assert classes["designs"].parsed == 3
    assert classes["designs"].mentioned == 3
    assert classes["designs"].sectioned == 1
    assert classes["channels"].parsed == 2
    assert classes["channels"].mentioned == 1
    assert classes["routines"].parsed == 2  # distinct routines, not usages
    assert classes["routines"].mentioned == 0


def test_a_longer_design_name_is_not_credited_to_its_prefix(tmp_path):
    """``FieldApp_GetLocation`` and ``FieldApp_GetLocations`` are both real.

    A substring match credits one for the other, which is how a coverage number
    ends up higher than the documentation.
    """

    _estate(tmp_path)
    report = measure_documentation_reach(
        tmp_path, {"00_OVERVIEW.md": "# Overview\n\nOnly Orders_LoadDelta is covered.\n"}
    )
    classes = _by_name(report)
    assert classes["designs"].mentioned == 1


def test_mapping_rows_are_attributed_through_their_owning_design(tmp_path):
    _estate(tmp_path)
    report = measure_documentation_reach(
        tmp_path,
        {"00_OVERVIEW.md": "# Overview\n\n## Orders_Load\n\nRow-by-row detail.\n"},
    )
    classes = _by_name(report)

    assert classes["mapping_rows"].parsed == 100
    assert classes["mapping_rows"].sectioned == 40
    assert classes["mapping_rows"].mentioned == 40


def test_compiler_authored_registers_are_excluded_from_the_measurement(tmp_path):
    """Otherwise every run reads as full coverage and the gauge reports nothing.

    ``output/reference/`` is compiler-authored and complete by construction:
    it contains every design, channel, routine and mapping row. Measuring
    against it would say a run with no narrative at all covered 100%.
    """

    _estate(tmp_path)
    registers = "\n".join(["# Units", "Orders_Load", "Orders_LoadDelta", "Invoices_Sync"])
    report = measure_documentation_reach(
        tmp_path,
        {
            "reference/units.md": registers,
            "reference/channels.md": "queue.orders\nqueue.invoices\n",
            "00_OVERVIEW.md": "# Overview\n\nNothing in particular.\n",
        },
    )
    classes = _by_name(report)

    assert classes["designs"].mentioned == 0
    assert report.compiler_documents == ("reference/channels.md", "reference/units.md")
    assert report.agent_documents == ("00_OVERVIEW.md",)


def test_a_run_with_no_agent_document_says_so_rather_than_reporting_zero(tmp_path):
    _estate(tmp_path)
    report = measure_documentation_reach(tmp_path, {"reference/units.md": "Orders_Load\n"})
    assert any("no agent-authored document" in notice for notice in report.notices)


def test_an_absent_navigation_tree_is_a_notice_and_not_an_exception(tmp_path):
    report = measure_documentation_reach(tmp_path, {"00_OVERVIEW.md": "# Overview\n"})
    assert all(item.parsed == 0 for item in report.classes)
    assert report.notices


def test_a_malformed_shard_line_is_skipped_without_failing(tmp_path):
    directory = tmp_path / contract.NAVIGATION / "units"
    directory.mkdir(parents=True)
    (directory / "part_001.jsonl").write_text(
        '{"unit_id": "u1", "display_name": "Orders_Load"}\nnot json\n[]\n',
        encoding="utf-8",
    )
    report = measure_documentation_reach(tmp_path, {"00_OVERVIEW.md": "Orders_Load\n"})
    assert _by_name(report)["designs"].parsed == 1


def test_the_artifact_is_written_where_no_client_surface_can_see_it(tmp_path):
    _estate(tmp_path)
    report = measure_documentation_reach(tmp_path, {"00_OVERVIEW.md": "# Overview\n\n## Orders_Load\n"})
    path = write_reach_artifact(tmp_path, report)

    assert path == tmp_path / REACH_ARTIFACT
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["measured_over"] == "agent-authored documents only"
    assert {item["class"] for item in payload["classes"]} == {
        "designs",
        "channels",
        "routines",
        "mapping_rows",
    }
    # Underscore-prefixed, so no client document, no RAG layer, no PDF export.
    assert REACH_ARTIFACT.parts[0].startswith("_")


def test_the_log_line_reads_without_the_module_open_beside_it(tmp_path):
    _estate(tmp_path)
    report = measure_documentation_reach(tmp_path, {"00_OVERVIEW.md": "# Overview\n\n## Orders_Load\n"})
    line = report.log_line()
    assert line.startswith("documentation reach: ")
    assert "designs: 1 of 3 designs mentioned, 1 with a heading" in line
    assert "routines" in line


# -- the constraint ------------------------------------------------------


def test_nothing_in_publication_reads_the_gauge():
    """Plan decision 10: it cannot block, shape or be read by a publication.

    Checked by reading the source rather than by behaviour, because the failure
    mode is a future edit that wires it in, not a bug in today's control flow.
    """

    import inspect

    from mcp_server.providers.agent_workspace import publish

    source = inspect.getsource(publish)
    # The MODULE and its entry points, not the bare word. `publish.py`
    # legitimately names `notes/coverage/` in `WORKSPACE_PATH_MARKERS` -- the
    # list of workspace paths a CLIENT document must not cite -- which has
    # nothing to do with reading the gauge. A substring check turned adding a
    # leak marker into a failure of the "publication never reads the gauge"
    # rule, which is how an over-broad guard trains people to weaken it.
    for forbidden in (
        "from mcp_server.providers.agent_workspace import coverage",
        "agent_workspace.coverage",
        "measure_documentation_reach",
        "DocumentationReachReport",
        "write_reach_artifact",
        "documentation_reach",
    ):
        assert forbidden not in source, forbidden

    from mcp_server.providers.agent_workspace import runner

    runner_source = inspect.getsource(runner)
    # The gauge is measured and logged, and its result is never consulted.
    assert "measure_documentation_reach" in runner_source
    for forbidden in (
        "if report.",
        "report.classes and",
        "not report",
        "coverage_gauge.measure_documentation_reach(ctx.staging_dir, outcome.documents).",
    ):
        assert forbidden not in runner_source, forbidden


def test_the_gauge_computes_no_quota_and_no_document_requirement():
    """No word count, no document count, no required-section list."""

    import inspect

    from mcp_server.providers.agent_workspace import coverage

    source = inspect.getsource(coverage)
    for forbidden in ("min_lines", "required_docs", "word_count", "MIN_", "raise "):
        assert forbidden not in source, forbidden


def test_the_gauge_is_not_the_agent_owned_coverage_register(tmp_path):
    """PRD 10.1 gives the AGENT a register at ``notes/coverage.json``.

    That artifact classifies integration-bearing candidates and feeds the
    agent's own completion decision. This one measures published prose against
    the parse and decides nothing. Two artifacts, two owners, two questions --
    so they must not share a path, a directory or a filename, and Nexus must
    never write into ``notes/``.
    """

    import inspect

    from mcp_server.providers.agent_workspace import contract, coverage

    assert REACH_ARTIFACT.parts[0] != contract.NOTES.name
    assert REACH_ARTIFACT.name != "coverage.json"
    assert REACH_ARTIFACT == Path("_audit") / "documentation_reach.json"

    source = inspect.getsource(coverage)
    assert "contract.NOTES" not in source
    assert "notes/" not in source.replace("``notes/``", "").replace(
        "`notes/coverage.json`", ""
    ).replace("notes/coverage.json", "")


def test_the_artifact_states_in_its_own_text_that_it_decides_nothing(tmp_path):
    """The number outlives whoever computed it.

    PRD 10.2: documentation does not require one document per candidate, one
    section per job, every candidate in prose, or an exact string match against
    scanner names. PRD 15.3 forbids rejecting a document on a job-name
    occurrence count. Both of those are exactly what a reader would otherwise
    do with this file.
    """

    _estate(tmp_path)
    report = measure_documentation_reach(tmp_path, {"00_OVERVIEW.md": "# Overview\n"})
    payload = json.loads(
        write_reach_artifact(tmp_path, report).read_text(encoding="utf-8")
    )

    assert payload["artifact"] == "nexus-documentation-reach"
    disclaimer = payload["not_a_completion_criterion"]
    assert "not a completion criterion" in disclaimer
    assert "not a gate" in disclaimer
    assert "one document per candidate" in disclaimer
