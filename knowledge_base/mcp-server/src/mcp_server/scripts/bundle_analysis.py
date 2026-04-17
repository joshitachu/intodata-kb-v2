#!/usr/bin/env python3
"""Bundle analysis files per Phase 3 agent.

Usage: python3 bundle_analysis.py <staging_dir> <total_jobs> [large_threshold]

Written by MCP server preprocessing; executed by the orchestrator agent
between Phase 2.6 and Phase 3.  Creates per-agent analysis bundles in
<staging_dir>/_analysis/_bundle_<agent>.md so that each Phase 3 agent
reads ONE pre-filtered file instead of grepping through all group files.
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

MAX_BUNDLE_BYTES = 350_000  # ~117K tokens, safe budget after prompt overhead


def normalize_section_name(header: str) -> str:
    """Map a ``### Header Text`` value to a canonical section name."""
    name = header.strip().upper().replace(" ", "_").replace("-", "_")
    for known in ALL_SECTIONS:
        if known in name or name in known:
            return known
    return name


def parse_sections(content: str) -> list[tuple[str, str]]:
    """Split markdown content on ``### `` headers.

    Returns a list of ``(section_name, section_text)`` pairs where
    *section_text* includes the header line itself.
    """
    sections: list[tuple[str, str]] = []
    current_name = "_PREAMBLE"
    current_lines: list[str] = []

    for line in content.splitlines(keepends=True):
        if line.startswith("### "):
            if current_lines:
                sections.append((current_name, "".join(current_lines)))
            raw_name = line[4:].strip()
            current_name = normalize_section_name(raw_name)
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_name, "".join(current_lines)))

    return sections


def truncate_section(text: str, max_lines: int = 30) -> str:
    """Keep the header line plus the first *max_lines* of body."""
    lines = text.splitlines(keepends=True)
    if len(lines) <= max_lines + 1:
        return text
    omitted = len(lines) - max_lines - 1
    return (
        "".join(lines[: max_lines + 1])
        + f"\n... (truncated, {omitted} lines omitted)\n"
    )


def _build_bundle(
    agent: str,
    all_groups: dict[str, list[tuple[str, str]]],
    total_bytes: int,
) -> str:
    """Build a single agent's analysis bundle with progressive truncation."""
    rel = RELEVANCE.get(agent)
    include_all = rel is None or total_bytes < 400_000
    high = rel["high"] if rel else set()
    medium = rel["medium"] if rel else set()

    def _collect(
        full_high: bool = True,
        full_medium: bool = True,
        include_medium: bool = True,
    ) -> str:
        parts: list[str] = [f"# Analysis Bundle: {agent}\n"]
        parts.append(
            f"# Total analysis: {total_bytes / 1024:.0f}KB"
            f" across {len(all_groups)} groups\n\n"
        )
        for group, sections in sorted(all_groups.items()):
            parts.append(f"\n## Group: {group}\n\n")
            for sec_name, sec_text in sections:
                if sec_name == "_PREAMBLE":
                    parts.append(sec_text)
                elif include_all:
                    parts.append(sec_text + "\n")
                elif sec_name in high:
                    if full_high:
                        parts.append(sec_text + "\n")
                    else:
                        parts.append(truncate_section(sec_text) + "\n")
                elif sec_name in medium and include_medium:
                    if full_medium:
                        parts.append(sec_text + "\n")
                    else:
                        parts.append(truncate_section(sec_text) + "\n")
        return "".join(parts)

    content = _collect()
    if len(content.encode("utf-8")) <= MAX_BUNDLE_BYTES or include_all:
        return content

    # Phase 1: truncate MEDIUM sections to 30 lines per group
    content = _collect(full_medium=False)
    if len(content.encode("utf-8")) <= MAX_BUNDLE_BYTES:
        return content

    # Phase 2: drop MEDIUM entirely, keep only HIGH
    content = _collect(include_medium=False)
    return content


def main() -> None:
    staging_dir = Path(sys.argv[1])
    total_jobs = int(sys.argv[2])
    large_threshold = int(sys.argv[3]) if len(sys.argv) > 3 else 200

    analysis_dir = staging_dir / "_analysis"
    files = sorted(
        f for f in analysis_dir.glob("*.md") if not f.name.startswith("_")
    )

    if not files:
        print("WARNING: No analysis files found in", analysis_dir)
        sys.exit(0)

    # Parse all analysis files into sections
    all_groups: dict[str, list[tuple[str, str]]] = {}
    total_bytes = 0
    for f in files:
        content = f.read_text(encoding="utf-8")
        total_bytes += len(content.encode("utf-8"))
        all_groups[f.stem] = parse_sections(content)

    is_large = total_jobs >= large_threshold
    agents = (
        ["core", "business_ops", "patterns", "user"]
        if is_large
        else ["core", "business_ops", "patterns_user"]
    )

    for agent in agents:
        bundle = _build_bundle(agent, all_groups, total_bytes)
        bundle_bytes = len(bundle.encode("utf-8"))
        bundle_path = analysis_dir / f"_bundle_{agent}.md"
        bundle_path.write_text(bundle, encoding="utf-8")
        print(f"Bundle {agent}: {bundle_bytes / 1024:.1f}KB")

    print(
        f"Total: {total_bytes / 1024:.1f}KB, "
        f"agents: {len(agents)}, "
        f"large_project: {is_large}"
    )


if __name__ == "__main__":
    main()
