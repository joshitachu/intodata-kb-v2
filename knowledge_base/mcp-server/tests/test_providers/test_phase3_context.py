import hashlib
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from mcp_server.config import config
from mcp_server.providers.custom.phase3_context import (
    FACT_MAP_VIEWS,
    _bounded_values,
    _markdown_table_rows,
    build_fact_map_view,
    compact_change_impact_matrix,
)


def _change_impact_source(path: Path, rows: int = 120) -> Path:
    lines = [
        "# Change Impact Matrix",
        "",
        "|changed_entity|impacted_entity|check|confidence|evidence|",
        "|---|---|---|---|---|",
    ]
    for index in range(rows):
        lines.append(
            f"|setting_{index}|unit_{index % 10}|Review shared configuration.|0.8|jobs/unit_{index % 10}.item|"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# change_impact_matrix -- 08_CHANGE_PLAYBOOK bytes are pinned
# ---------------------------------------------------------------------------


def test_compact_change_impact_matrix_groups_repetitive_rows(tmp_path: Path) -> None:
    source = _change_impact_source(tmp_path / "change_impact_matrix.md")

    compact = compact_change_impact_matrix(source, tmp_path / "views", max_bytes=4_000)

    assert compact != source
    assert compact.stat().st_size < source.stat().st_size
    assert compact.stat().st_size <= 4_000
    text = compact.read_text(encoding="utf-8")
    assert "Source rows: 120" in text
    assert "Unique change targets: 120" in text
    assert "Review shared configuration." in text
    assert "+110 more" in text
    assert "`change_impact_matrix.md`" in text


def test_small_change_impact_matrix_remains_the_evidence_source(tmp_path: Path) -> None:
    source = tmp_path / "change_impact_matrix.md"
    source.write_text(
        "|changed_entity|impacted_entity|check|confidence|evidence|\n"
        "|---|---|---|---|---|\n"
        "|setting|unit|Review configuration.|0.9|unit.item|\n",
        encoding="utf-8",
    )

    selected = compact_change_impact_matrix(source, tmp_path / "views", max_bytes=4_000)

    assert selected == source
    assert not (tmp_path / "views").exists()


# The exact bytes 08_CHANGE_PLAYBOOK receives, captured from the implementation
# that predates the fact-map view registry. The registry SUBSUMED that function
# (it is now one entry in `FACT_MAP_VIEWS`, rendered by the generic grouped
# builder), and this pin is the proof the subsumption changed nothing: the
# document's prompt prefix is cached on these bytes.
_CHANGE_IMPACT_PINNED_SHA256 = (
    "77caf2bb70964dca11d3a5e11455bf098799d4fed44cc9c62209307681eb2cc2"
)
_CHANGE_IMPACT_PINNED_BYTES = 755


def test_change_impact_view_bytes_are_pinned(tmp_path: Path) -> None:
    source = _change_impact_source(tmp_path / "change_impact_matrix.md")

    compact = compact_change_impact_matrix(source, tmp_path / "views", max_bytes=4_000)

    payload = compact.read_bytes()
    assert len(payload) == _CHANGE_IMPACT_PINNED_BYTES
    assert hashlib.sha256(payload).hexdigest() == _CHANGE_IMPACT_PINNED_SHA256


def test_change_impact_view_is_reached_through_the_registry(tmp_path: Path) -> None:
    """The registry entry and the named entry point must not drift apart."""
    source = _change_impact_source(tmp_path / "change_impact_matrix.md", rows=4_000)

    direct = compact_change_impact_matrix(
        source, tmp_path / "a", max_bytes=config.phase3_change_impact_view_bytes
    )
    routed = build_fact_map_view(source, tmp_path / "b")

    assert routed.name == direct.name
    assert routed.read_bytes() == direct.read_bytes()


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_bounded_values_breaks_casefold_ties_verbatim() -> None:
    """Regression: sorting a set on `casefold` alone left ties to hash order.

    `sorted` is stable, so two values that casefold equal kept SET ITERATION
    order, which varies with string hash randomisation between processes.
    Measured on a real change-impact matrix: four runs over identical input
    produced three different views -- a silent prompt-cache miss every time.
    """
    assert _bounded_values({"Vise2AssetCheck", "vise2assetcheck"}, 5) == (
        "Vise2AssetCheck; vise2assetcheck"
    )
    assert _bounded_values({"vise2assetcheck", "Vise2AssetCheck"}, 5) == (
        "Vise2AssetCheck; vise2assetcheck"
    )


_DETERMINISM_PROBE = textwrap.dedent(
    """
    import hashlib, sys, tempfile
    from pathlib import Path
    from mcp_server.providers.custom.phase3_context import build_fact_map_view

    source = Path(sys.argv[1])
    out = Path(tempfile.mkdtemp())
    print(hashlib.sha256(build_fact_map_view(source, out).read_bytes()).hexdigest())
    """
)


@pytest.mark.parametrize("name", sorted(FACT_MAP_VIEWS))
def test_views_are_byte_stable_across_processes(tmp_path: Path, name: str) -> None:
    """Two processes with different hash seeds must produce identical views.

    Same process twice proves nothing: PYTHONHASHSEED is fixed for a process,
    so set iteration order is stable within it. The seed is what varies in
    production, one worker to the next.
    """
    source = _synthetic_map(tmp_path / name)
    probe = tmp_path / "probe.py"
    probe.write_text(_DETERMINISM_PROBE, encoding="utf-8")

    digests = set()
    for seed in ("0", "1", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        result = subprocess.run(
            [sys.executable, str(probe), str(source)],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        digests.add(result.stdout.strip())
    assert len(digests) == 1, f"{name} view is not byte-stable: {digests}"


# ---------------------------------------------------------------------------
# The registry as a whole
# ---------------------------------------------------------------------------

# Column layouts mirror `engineering_facts.writers.markdown_writer` plus the
# `sql_usage` register, so a synthetic source exercises the real parse path.
_SYNTHETIC_COLUMNS: dict[str, list[str]] = {
    "change_impact_matrix.md": ["changed_entity", "impacted_entity", "check", "confidence"],
    "dependency_map.md": ["source", "target", "dependency_type", "confidence"],
    "known_risks.md": [
        "severity", "affected_entity", "risk_type", "name", "remediation", "confidence",
    ],
    "config_runtime_map.md": ["config_type", "key", "name", "value_hint", "confidence"],
    "data_flow_map.md": ["source", "target", "transformation", "schema_hint", "confidence"],
    "architecture_map.md": ["name", "layer", "component", "boundary", "confidence"],
    "api_surface.md": ["method", "route", "handler", "name", "confidence"],
    "test_coverage_map.md": [
        "test_file", "covered_entity", "gap", "suggested_scenario", "confidence",
    ],
}


def _synthetic_map(path: Path, rows: int = 3_000) -> Path:
    """Write an oversized artifact in the real shape of ``path.name``."""
    if path.name == "sql_usage.md":
        lines = ["# SQL Usage", "", f"Records: {rows}  Tables: 40  Sources: 12", ""]
        for index in range(rows):
            lines.extend(
                [
                    f"## SQL-{index:06X} - unit_{index % 40}",
                    f"Source: src/unit_{index % 40}.item  Group: group_{index % 7}  "
                    "Connection: UNKNOWN  Status: PARSED",
                    "",
                    "| Table | Aliases | Selected fields | Join fields | Filter fields |"
                    " Order/group fields | All fields |",
                    "|-------|---------|-----------------|-------------|---------------|"
                    "--------------------|------------|",
                    f"| table_{index % 40} | t | col_a | col_b | col_c | col_d | col_a, col_b |",
                    "",
                    "```sql",
                    f"SELECT col_a FROM table_{index % 40} WHERE col_c = {index}",
                    "```",
                    "",
                ]
            )
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return path

    columns = _SYNTHETIC_COLUMNS[path.name]
    header = "|" + "|".join(columns + ["evidence"]) + "|"
    separator = "|" + "|".join("---" for _ in columns + ["evidence"]) + "|"
    if path.name == "dependency_map.md":
        # The grouped shape `markdown_writer._table(group_by=...)` emits.
        lines = [f"# {path.stem}", ""]
        per_type = max(1, rows // 8)
        for kind in ["calls_job"] + [f"rel_type_{i}" for i in range(7)]:
            lines.extend([f"## {kind}", "", f"{per_type} rows.", "", header, separator])
            lines.extend(
                "|" + "|".join(
                    [f"src_{i}", f"tgt_{i % 50}", kind, "1.0", f"jobs/unit_{i % 50}.item"]
                ) + "|"
                for i in range(per_type)
            )
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    lines = [f"# {path.stem}", "", header, separator]
    for index in range(rows):
        cells = [f"{column}_{index % 40}" for column in columns[:-1]]
        cells.append("0.8")
        cells.append(f"jobs/unit_{index % 50}.item")
        lines.append("|" + "|".join(cells) + "|")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("name", sorted(FACT_MAP_VIEWS))
def test_every_view_is_bounded_and_names_its_artifact_of_record(
    tmp_path: Path, name: str
) -> None:
    source = _synthetic_map(tmp_path / name)
    cap = FACT_MAP_VIEWS[name].max_bytes()

    view = build_fact_map_view(source, tmp_path / "views")

    assert view != source, f"{name} was not reduced"
    text = view.read_text(encoding="utf-8")
    # A view must say WHICH artifact holds the full content...
    assert f"`{name}`" in text, f"{name} view does not name its artifact of record"
    # ...and that it is itself a reduction. The change-impact view says so with
    # its title and its "bounded view" footer; the others carry an explicit
    # REDUCTION banner.
    assert "Compact" in text or "REDUCTION" in text
    # The cap is soft by exactly the trailing omission note, which is appended
    # after the byte-fit check (pinned behaviour, see the change-impact view).
    assert view.stat().st_size <= cap + 1_024


def test_unregistered_artifact_still_cannot_exceed_the_shared_threshold(
    tmp_path: Path,
) -> None:
    source = tmp_path / "some_future_map.md"
    source.write_text("x" * (config.phase3_shared_context_file_bytes * 3), encoding="utf-8")

    view = build_fact_map_view(source, tmp_path / "views")

    assert view != source
    assert view.stat().st_size <= config.phase3_shared_context_file_bytes
    assert "REDUCTION" in view.read_text(encoding="utf-8")


def test_bounded_aggregate_passes_through_untouched(tmp_path: Path) -> None:
    """`engineering_facts.md` is already the aggregate; do not re-reduce it."""
    source = tmp_path / "engineering_facts.md"
    source.write_text("# Engineering Facts\n" + "row\n" * 2_000, encoding="utf-8")
    assert source.stat().st_size < config.phase3_shared_context_file_bytes

    assert build_fact_map_view(source, tmp_path / "views") == source


def test_dependency_view_defers_calls_job_to_the_aggregate(tmp_path: Path) -> None:
    """No double coverage: the complete edge list lives in engineering_facts."""
    source = _synthetic_map(tmp_path / "dependency_map.md")

    text = build_fact_map_view(source, tmp_path / "views").read_text(encoding="utf-8")

    assert "### calls_job -- 0 of" in text
    assert "engineering_facts.md" in text
    # Every other relation is still represented, with counts.
    assert "### rel_type_0 -- " in text
    assert text.count("### ") == 8


def test_markdown_table_rows_parses_grouped_maps(tmp_path: Path) -> None:
    """A grouped map repeats its column header per section; each is its own."""
    text = (
        "# Dependency Map\n\n"
        "## calls_job\n\n2 rows.\n\n|source|target|\n|---|---|\n|a|b|\n|c|d|\n\n"
        "## contains\n\n1 rows.\n\n|source|target|\n|---|---|\n|e|f|\n"
    )

    rows = _markdown_table_rows(text)

    assert rows == [
        {"source": "a", "target": "b"},
        {"source": "c", "target": "d"},
        {"source": "e", "target": "f"},
    ]
