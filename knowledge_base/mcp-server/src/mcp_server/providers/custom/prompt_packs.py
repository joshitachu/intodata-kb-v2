"""Legacy prompt-pack adaptation for the custom harness.

The custom harness owns orchestration, retries, and file output. This module
reuses the rich Claude CLI prompt corpus as a semantic policy layer by
materializing harness-safe prompt packs for each phase.
"""

from __future__ import annotations

from pathlib import Path

from mcp_server.providers.custom.constants import ESB_ROUTES_SECTION


REQUIRED_SECTION_NAMES: tuple[str, ...] = (
    "COMPONENTS",
    "EXECUTION_FLOW",
    "DATA_FLOWS",
    "TRANSFORMATION_DETAIL",
    "BUSINESS_RULES",
    "SCHEMA_DICTIONARY",
    "EXTERNAL_SYSTEMS",
    "ERROR_HANDLING",
    "ENVIRONMENT_CONFIG",
    "SCHEDULING",
    "NAMING_PATTERNS",
    "CROSS_GROUP_REFS",
    "RISK_FLAGS",
)

# Only required when the group actually contains SQL queries, custom code, or ESB
# routes. ESB_ROUTES is prompted for on every Talend run (conditionally worded in
# the platform section) but only enforced by the validator when the scanner flagged
# at least one job as is_esb_route=true.
CONDITIONAL_SECTION_NAMES: tuple[str, ...] = (
    "SOURCE_SQL",
    "CUSTOM_CODE",
    ESB_ROUTES_SECTION,
)

ANALYSIS_SECTION_NAMES: tuple[str, ...] = REQUIRED_SECTION_NAMES + CONDITIONAL_SECTION_NAMES

SCORECARD_SOURCE_FILES: tuple[str, ...] = (
    "_scoring_rubric.md",
    "_anti_patterns.md",
    "_red_flags.md",
    "_security_checklist.md",
    "_pattern_library.md",
)

_BUNDLE_REF_GLOBS: dict[str, tuple[str, ...]] = {
    "core": ("_ref_core*.md",),
    "business_ops": ("_ref_business_ops*.md",),
    "patterns_user": ("_ref_patterns*.md", "_ref_user*.md"),
}



def materialize_prompt_packs(staging_dir: Path, analysis_guide_file: str) -> dict[str, object]:
    """Write harness-safe prompt packs and return their paths."""

    preprocess_dir = staging_dir / "_preprocess"
    pack_dir = staging_dir / "_assessment" / "prompt_packs"
    pack_dir.mkdir(parents=True, exist_ok=True)

    phase2_sources = _phase2_sources(preprocess_dir, analysis_guide_file)
    phase2_path = _write_pack(pack_dir / "_analysis_prompt_pack.md", phase2_sources)

    scorecard_sources = [
        preprocess_dir / name for name in SCORECARD_SOURCE_FILES
        if (preprocess_dir / name).exists()
    ]
    scorecard_path = _write_pack(pack_dir / "_scorecard_prompt_pack.md", scorecard_sources)

    phase3_paths: dict[str, Path] = {}
    for bundle, globs in _BUNDLE_REF_GLOBS.items():
        sources = []
        phase3_guide = preprocess_dir / "_phase3_guide.md"
        if phase3_guide.exists():
            sources.append(phase3_guide)
        doc_templates = preprocess_dir / "_doc_templates.md"
        if doc_templates.exists():
            sources.append(doc_templates)
        for pattern in globs:
            sources.extend(sorted(preprocess_dir.glob(pattern)))
        path = _write_pack(pack_dir / f"_phase3_prompt_pack_{bundle}.md", sources)
        if path is not None:
            phase3_paths[bundle] = path

    critic_path = _write_pack(
        pack_dir / "_critic_prompt_pack.md",
        [path for _, path in sorted(phase3_paths.items())],
    )

    return {
        "phase2": phase2_path,
        "phase26": scorecard_path,
        "phase3": phase3_paths,
        "phase35": critic_path,
    }


def _phase2_sources(preprocess_dir: Path, analysis_guide_file: str) -> list[Path]:
    if analysis_guide_file:
        guide = preprocess_dir / analysis_guide_file
        if guide.exists():
            return [guide]
    return sorted(preprocess_dir.glob("_analysis_prompts*.md"))


def _write_pack(path: Path, sources: list[Path]) -> Path | None:
    existing_sources = [source for source in sources if source.exists()]
    if not existing_sources:
        return None

    parts: list[str] = []
    for source in existing_sources:
        body = source.read_text(encoding="utf-8", errors="replace")
        parts.append(f"## Source: {source.name}")
        parts.append("")
        parts.append(body.strip())
        parts.append("")
    path.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
    return path
