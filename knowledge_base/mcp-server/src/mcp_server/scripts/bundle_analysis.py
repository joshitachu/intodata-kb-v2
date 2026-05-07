#!/usr/bin/env python3
"""Bundle analysis files per Phase 3 agent.

Usage: python3 bundle_analysis.py <staging_dir> <total_jobs> [large_threshold]

Written by MCP server preprocessing; executed by the orchestrator agent
between Phase 2.6 and Phase 3.  Creates per-agent analysis bundles in
<staging_dir>/_analysis/_bundle_<agent>.md so that each Phase 3 agent
reads ONE pre-filtered file instead of grepping through all group files.

Bundle size is capped at MAX_BUNDLE_BYTES (~15k tokens) to prevent context
window overflow.  If a bundle would exceed this after filtering, it is
auto-split into _bundle_<agent>_a.md and _bundle_<agent>_b.md.

Also writes:
  _analysis/_index.md     -- navigation table (group, jobs, token estimate, flags)
"""
from __future__ import annotations

import sys
from pathlib import Path

# The 15 canonical section names from ANALYSIS_GUIDE.md
ALL_SECTIONS = frozenset({
    "COMPONENTS", "EXECUTION_FLOW", "DATA_FLOWS", "TRANSFORMATION_DETAIL",
    "BUSINESS_RULES", "SCHEMA_DICTIONARY", "SOURCE_SQL", "EXTERNAL_SYSTEMS",
    "ERROR_HANDLING", "CUSTOM_CODE", "ENVIRONMENT_CONFIG", "SCHEDULING",
    "NAMING_PATTERNS", "CROSS_GROUP_REFS", "RISK_FLAGS",
})

# Section relevance per agent.  None = include all sections.
RELEVANCE: dict[str, dict[str, set[str]] | None] = {
    "core": {
        "high": {
            "COMPONENTS", "EXECUTION_FLOW", "DATA_FLOWS",
            "TRANSFORMATION_DETAIL", "SCHEMA_DICTIONARY", "SOURCE_SQL",
        },
        "medium": {"EXTERNAL_SYSTEMS", "SCHEDULING"},
    },
    "business_ops": {
        "high": {
            "BUSINESS_RULES", "TRANSFORMATION_DETAIL", "CUSTOM_CODE",
            "ERROR_HANDLING", "RISK_FLAGS",
        },
        "medium": {
            "ENVIRONMENT_CONFIG", "EXTERNAL_SYSTEMS",
            "CROSS_GROUP_REFS", "SCHEDULING",
        },
    },
    "patterns_user": None,  # broad context needed for docs 07-11
    "patterns": {
        "high": {
            "NAMING_PATTERNS", "RISK_FLAGS", "CUSTOM_CODE", "ERROR_HANDLING",
        },
        "medium": {
            "COMPONENTS", "DATA_FLOWS", "EXTERNAL_SYSTEMS", "BUSINESS_RULES",
        },
    },
    "user": {
        "high": {
            "ERROR_HANDLING", "ENVIRONMENT_CONFIG",
            "SCHEDULING", "EXECUTION_FLOW",
        },
        "medium": {
            "COMPONENTS", "DATA_FLOWS", "CROSS_GROUP_REFS",
            "RISK_FLAGS", "BUSINESS_RULES",
        },
    },
}

# Hard cap per bundle: ~15k tokens (1 token ≈ 4 chars, 60KB ≈ 15k tokens)
# This prevents context window overflow and forces progressive truncation.
MAX_BUNDLE_BYTES = 60_000


def normalize_section_name(header: str) -> str:
    name = header.strip().upper().replace(" ", "_").replace("-", "_")
    for known in ALL_SECTIONS:
        if known in name or name in known:
            return known
    return name


def parse_sections(content: str) -> list[tuple[str, str]]:
    """Split markdown content on ``### `` headers.

    Returns (section_name, section_text) pairs; section_text includes the header.
    """
    sections: list[tuple[str, str]] = []
    current_name = "_PREAMBLE"
    current_lines: list[str] = []
    for line in content.splitlines(keepends=True):
        if line.startswith("### "):
            if current_lines:
                sections.append((current_name, "".join(current_lines)))
            current_name = normalize_section_name(line[4:].strip())
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_name, "".join(current_lines)))
    return sections


def truncate_section(text: str, max_lines: int = 30) -> str:
    lines = text.splitlines(keepends=True)
    if len(lines) <= max_lines + 1:
        return text
    omitted = len(lines) - max_lines - 1
    return "".join(lines[: max_lines + 1]) + f"\n... ({omitted} lines omitted)\n"


def _load_summary(analysis_dir: Path, group: str) -> str:
    """Read {group}_summary.md if present; else return empty string."""
    p = analysis_dir / f"{group}_summary.md"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def _build_metadata_section(
    groups: list[str],
    summaries: dict[str, str],
    total_bytes: int,
    quick_scan_path: Path | None,
) -> str:
    """Build the METADATA and SUMMARIES header block for a bundle."""
    parts = [
        "## METADATA\n",
        f"Groups: {', '.join(sorted(groups))}\n",
        f"Total analysis size: {total_bytes / 1024:.0f}KB\n\n",
    ]

    if quick_scan_path and quick_scan_path.exists():
        # Include critical flags from quick scan
        qs = quick_scan_path.read_text(encoding="utf-8")
        critical_lines = [
            ln for ln in qs.splitlines()
            if any(tag in ln for tag in ("CRITICAL", "HIGH", "god_profile", "hardcoded"))
        ][:20]
        if critical_lines:
            parts.append("**Quick scan highlights:**\n")
            for ln in critical_lines:
                parts.append(f"  {ln}\n")
            parts.append("\n")

    parts.append("## SUMMARIES\n")
    for group in sorted(groups):
        s = summaries.get(group, "")
        if s:
            parts.append(f"\n### {group}\n{s}\n")
        else:
            parts.append(f"\n### {group}\n(no summary available — see DETAIL section)\n")

    parts.append("\n---\n\n## DETAIL\n\n")
    return "".join(parts)


def _build_bundle_content(
    agent: str,
    groups: list[str],
    all_sections: dict[str, list[tuple[str, str]]],
    total_bytes: int,
    summaries: dict[str, str],
    quick_scan_path: Path | None,
) -> str:
    rel = RELEVANCE.get(agent)
    include_all = rel is None
    high = rel["high"] if rel else set()
    medium = rel["medium"] if rel else set()

    header = f"# Analysis Bundle: {agent}\n"
    metadata = _build_metadata_section(list(groups), summaries, total_bytes, quick_scan_path)

    def _collect(
        full_high: bool = True,
        full_medium: bool = True,
        include_medium: bool = True,
    ) -> str:
        detail_parts: list[str] = []
        for group in sorted(groups):
            detail_parts.append(f"\n## Group: {group}\n\n")
            for sec_name, sec_text in all_sections.get(group, []):
                if sec_name == "_PREAMBLE":
                    detail_parts.append(sec_text)
                elif include_all:
                    detail_parts.append(sec_text + "\n")
                elif sec_name in high:
                    detail_parts.append(
                        (sec_text if full_high else truncate_section(sec_text)) + "\n"
                    )
                elif sec_name in medium and include_medium:
                    detail_parts.append(
                        (sec_text if full_medium else truncate_section(sec_text)) + "\n"
                    )
        return header + metadata + "".join(detail_parts)

    content = _collect()
    if len(content.encode("utf-8")) <= MAX_BUNDLE_BYTES or include_all:
        return content

    content = _collect(full_medium=False)
    if len(content.encode("utf-8")) <= MAX_BUNDLE_BYTES:
        return content

    return _collect(include_medium=False)


def _split_groups(groups: list[str]) -> tuple[list[str], list[str]]:
    """Split group list in half for bundle overflow handling."""
    mid = max(1, len(groups) // 2)
    return sorted(groups)[:mid], sorted(groups)[mid:]


def write_bundles(
    agent: str,
    groups: list[str],
    all_sections: dict[str, list[tuple[str, str]]],
    total_bytes: int,
    summaries: dict[str, str],
    quick_scan_path: Path | None,
    analysis_dir: Path,
) -> list[str]:
    """Write bundle file(s) for an agent. Returns list of written filenames."""
    content = _build_bundle_content(
        agent, groups, all_sections, total_bytes, summaries, quick_scan_path,
    )
    size = len(content.encode("utf-8"))
    written: list[str] = []

    if size <= MAX_BUNDLE_BYTES or len(groups) <= 1:
        path = analysis_dir / f"_bundle_{agent}.md"
        path.write_text(content, encoding="utf-8")
        print(f"Bundle {agent}: {size / 1024:.1f}KB")
        written.append(path.name)
    else:
        # Auto-split: divide groups between _a and _b
        groups_a, groups_b = _split_groups(groups)
        for suffix, grp_list in [("a", groups_a), ("b", groups_b)]:
            c = _build_bundle_content(
                agent, grp_list, all_sections, total_bytes, summaries, quick_scan_path,
            )
            p = analysis_dir / f"_bundle_{agent}_{suffix}.md"
            p.write_text(c, encoding="utf-8")
            kb = len(c.encode("utf-8")) / 1024
            print(f"Bundle {agent}_{suffix}: {kb:.1f}KB ({len(grp_list)} groups)")
            written.append(p.name)

    return written


def write_index(
    groups: list[str],
    all_sections: dict[str, list[tuple[str, str]]],
    summaries: dict[str, str],
    quick_scan_path: Path | None,
    analysis_dir: Path,
) -> None:
    """Write _analysis/_index.md for Phase 3 agents to navigate quickly."""
    lines = [
        "# Analysis Index\n\n",
        "Phase 3 agents: read this first to locate sections you need.\n",
        "Load `_index.md`, then load only the specific group detail you need.\n\n",
        "| Group | Summary File | Analysis File | Quick Scan Flags |\n",
        "|-------|-------------|---------------|------------------|\n",
    ]

    qs_flags: dict[str, list[str]] = {}
    if quick_scan_path and quick_scan_path.exists():
        for ln in quick_scan_path.read_text(encoding="utf-8").splitlines():
            for g in groups:
                if g in ln and any(t in ln for t in ("CRITICAL", "HIGH", "god_profile", "hardcoded")):
                    qs_flags.setdefault(g, []).append(ln.strip()[:80])

    for group in sorted(groups):
        summary_file = f"{group}_summary.md" if summaries.get(group) else "—"
        analysis_file = f"{group}.md"
        flags = "; ".join(qs_flags.get(group, [])[:3]) or "—"
        lines.append(f"| {group} | {summary_file} | {analysis_file} | {flags} |\n")

    (analysis_dir / "_index.md").write_text("".join(lines), encoding="utf-8")
    print(f"Index written: {len(groups)} groups")


def main() -> None:
    staging_dir = Path(sys.argv[1])
    total_jobs = int(sys.argv[2])
    large_threshold = int(sys.argv[3]) if len(sys.argv) > 3 else 200

    analysis_dir = staging_dir / "_analysis"
    quick_scan_path = staging_dir / "_assessment" / "quick_scan.md"

    files = sorted(
        f for f in analysis_dir.glob("*.md")
        if not f.name.startswith("_") and not f.stem.endswith("_summary")
    )

    if not files:
        print("WARNING: No analysis files found in", analysis_dir)
        sys.exit(0)

    # Parse all analysis files + load per-group summaries
    all_sections: dict[str, list[tuple[str, str]]] = {}
    summaries: dict[str, str] = {}
    total_bytes = 0
    for f in files:
        content = f.read_text(encoding="utf-8")
        total_bytes += len(content.encode("utf-8"))
        all_sections[f.stem] = parse_sections(content)
        summaries[f.stem] = _load_summary(analysis_dir, f.stem)

    groups = list(all_sections.keys())

    is_large = total_jobs >= large_threshold
    agents = (
        ["core", "business_ops", "patterns", "user"]
        if is_large
        else ["core", "business_ops", "patterns_user"]
    )

    all_written: list[str] = []
    for agent in agents:
        written = write_bundles(
            agent, groups, all_sections, total_bytes, summaries, quick_scan_path, analysis_dir,
        )
        all_written.extend(written)

    write_index(groups, all_sections, summaries, quick_scan_path, analysis_dir)

    print(
        f"Total: {total_bytes / 1024:.1f}KB, agents: {len(agents)}, "
        f"large_project: {is_large}, bundles: {len(all_written)}"
    )


if __name__ == "__main__":
    main()
