"""Deterministic, bounded evidence views for Phase 3 narrative documents."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path


def compact_change_impact_matrix(
    source: Path,
    output_dir: Path,
    *,
    max_bytes: int,
) -> Path:
    """Return a bounded, grouped view of a large change-impact Markdown table.

    The complete deterministic matrix remains the evidence of record. The view
    groups repetitive rows by their prescribed check so a narrative writer sees
    representative targets, affected entities, evidence paths, confidence, and
    exact coverage counts without being prompted to copy thousands of rows.
    """

    if max_bytes <= 0 or source.stat().st_size <= max_bytes:
        return source

    rows = _markdown_table_rows(source.read_text(encoding="utf-8", errors="replace"))
    if not rows:
        return source

    grouped: dict[tuple[str, str], dict[str, set[str] | int]] = defaultdict(
        lambda: {"changed": set(), "impacted": set(), "evidence": set(), "rows": 0}
    )
    for row in rows:
        check = row.get("check", "").strip() or "Review the supplied change evidence."
        confidence = row.get("confidence", "").strip() or "UNKNOWN"
        group = grouped[(check, confidence)]
        _add(group["changed"], row.get("changed_entity", ""))
        _add(group["impacted"], row.get("impacted_entity", ""))
        _add(group["evidence"], row.get("evidence", ""))
        group["rows"] = int(group["rows"]) + 1

    ordered_groups = sorted(
        grouped.items(),
        key=lambda item: (-int(item[1]["rows"]), item[0][0].casefold(), item[0][1]),
    )
    unique_changed = {
        row.get("changed_entity", "").strip()
        for row in rows
        if row.get("changed_entity", "").strip()
    }
    unique_impacted = {
        row.get("impacted_entity", "").strip()
        for row in rows
        if row.get("impacted_entity", "").strip()
    }
    lines = [
        "# Compact Change Impact Evidence View",
        "",
        f"- Evidence of record: `{source.name}`",
        f"- Source rows: {len(rows)}",
        f"- Unique change targets: {len(unique_changed)}",
        f"- Unique affected entities: {len(unique_impacted)}",
        f"- Grouped review checks: {len(ordered_groups)}",
        "- Usage: synthesize a prioritized playbook; do not reproduce the full matrix.",
        "",
        "| Review check | Change targets (examples) | Affected entities (examples) | Evidence examples | Confidence | Source rows |",
        "|---|---|---|---|---|---:|",
    ]
    included = 0
    for (check, confidence), group in ordered_groups:
        candidate = "|".join(
            (
                "",
                _cell(check),
                _cell(_bounded_values(group["changed"], 10)),
                _cell(_bounded_values(group["impacted"], 14)),
                _cell(_bounded_values(group["evidence"], 4)),
                _cell(confidence),
                str(group["rows"]),
                "",
            )
        )
        projected = "\n".join((*lines, candidate, "")).encode("utf-8")
        if len(projected) > max_bytes:
            break
        lines.append(candidate)
        included += 1

    omitted = len(ordered_groups) - included
    if omitted:
        omitted_rows = sum(int(group["rows"]) for _, group in ordered_groups[included:])
        lines.extend(
            (
                "",
                f"> {omitted} lower-frequency review-check groups are not expanded in this "
                f"bounded view. Their {omitted_rows} rows remain in `{source.name}`.",
            )
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "change_impact_matrix.compact.md"
    output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output


def _markdown_table_rows(text: str) -> list[dict[str, str]]:
    table_lines = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    if len(table_lines) < 3:
        return []
    headers = _split_row(table_lines[0])
    separator = _split_row(table_lines[1])
    if not headers or not all(set(cell) <= {"-", ":", " "} for cell in separator):
        return []
    rows: list[dict[str, str]] = []
    for line in table_lines[2:]:
        values = _split_row(line)
        if len(values) == len(headers):
            rows.append(dict(zip(headers, values, strict=True)))
    return rows


def _split_row(line: str) -> list[str]:
    return [cell.strip().replace("\\|", "|") for cell in line.strip().strip("|").split("|")]


def _add(values: set[str] | int, value: str) -> None:
    if isinstance(values, set) and value.strip():
        values.add(value.strip())


def _bounded_values(values: set[str] | int, limit: int) -> str:
    if not isinstance(values, set):
        return ""
    ordered = sorted(values, key=str.casefold)
    suffix = f"; +{len(ordered) - limit} more" if len(ordered) > limit else ""
    return "; ".join(ordered[:limit]) + suffix


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()
