from pathlib import Path

from mcp_server.providers.custom.phase3_context import compact_change_impact_matrix


def test_compact_change_impact_matrix_groups_repetitive_rows(tmp_path: Path) -> None:
    source = tmp_path / "change_impact_matrix.md"
    rows = [
        "# Change Impact Matrix",
        "",
        "|changed_entity|impacted_entity|check|confidence|evidence|",
        "|---|---|---|---|---|",
    ]
    for index in range(120):
        rows.append(
            f"|setting_{index}|unit_{index % 10}|Review shared configuration.|0.8|jobs/unit_{index % 10}.item|"
        )
    source.write_text("\n".join(rows) + "\n", encoding="utf-8")

    compact = compact_change_impact_matrix(
        source,
        tmp_path / "views",
        max_bytes=4_000,
    )

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

    selected = compact_change_impact_matrix(
        source,
        tmp_path / "views",
        max_bytes=4_000,
    )

    assert selected == source
    assert not (tmp_path / "views").exists()
