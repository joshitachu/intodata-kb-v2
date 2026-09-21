"""Tests for the _rag_support/ package builder and analysis_cache restore."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from mcp_server.services.rag_support_package import (
    MANIFEST_SCHEMA_VERSION,
    build_entity_aliases,
    build_rag_support_package,
    collect_internal_artifacts,
    inspect_generation_cache_readiness,
    internal_doc_filename,
    replace_analysis_cache_from_rag_support,
)


def _make_staging(tmp_path: Path) -> Path:
    staging = tmp_path / "docs_staging"
    analysis = staging / "_analysis"
    analysis.mkdir(parents=True)
    (analysis / "A.md").write_text("# Group A analysis", encoding="utf-8")
    (analysis / "B.md").write_text("# Group B analysis", encoding="utf-8")
    (analysis / "C.md").write_text("# Not in manifest", encoding="utf-8")
    (analysis / "_doc_pack_01_ARCHITECTURE_MAP.md").write_text("pack", encoding="utf-8")
    (analysis / "_bundle_core.md").write_text("bundle", encoding="utf-8")
    (analysis / "_coverage_report.json").write_text("{}", encoding="utf-8")
    return staging


def test_build_copies_only_manifest_groups(tmp_path: Path) -> None:
    staging = _make_staging(tmp_path)

    manifest = build_rag_support_package(
        staging, ["A", "B"], project_id="proj", platform="talend"
    )

    package_dir = staging / "_rag_support" / "phase2_analysis"
    assert sorted(p.name for p in package_dir.iterdir()) == ["A.md", "B.md"]
    assert manifest["missing_groups"] == []
    assert (staging / "_rag_support" / "manifest.json").exists()


def test_build_manifest_metadata(tmp_path: Path) -> None:
    staging = _make_staging(tmp_path)

    build_rag_support_package(staging, ["B", "A"], project_id="proj", platform="talend")

    manifest = json.loads(
        (staging / "_rag_support" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert manifest["project_id"] == "proj"
    assert manifest["platform"] == "talend"
    assert [a["group"] for a in manifest["artifacts"]] == ["A", "B"]

    entry = manifest["artifacts"][0]
    content = (staging / "_analysis" / "A.md").read_bytes()
    assert entry["path"] == "phase2_analysis/A.md"
    assert entry["source_path"] == "_analysis/A.md"
    assert entry["source_layer"] == "technical_analysis"
    assert entry["artifact_type"] == "phase2_group_analysis"
    assert entry["visibility"] == "internal"
    assert entry["bytes"] == len(content)
    assert entry["sha256"] == hashlib.sha256(content).hexdigest()


def test_build_packages_case_collision_part_ci_slices(tmp_path: Path) -> None:
    # Case-colliding Talend groups: _preprocess_scan_data writes the later
    # collider's slice as {group}_part_ci{k}.md, and Phase 2 analyses follow
    # that naming (verified on the werkorder Assetcheck run, where the
    # _part_ci analysis was dropped from the package entirely).
    staging = tmp_path / "docs_staging"
    analysis = staging / "_analysis"
    analysis.mkdir(parents=True)
    (analysis / "VISE2AssetCheck.md").write_text("# upper analysis", encoding="utf-8")
    (analysis / "Vise2AssetCheck_part_ci1.md").write_text(
        "# collider analysis", encoding="utf-8"
    )

    manifest = build_rag_support_package(
        staging,
        ["VISE2AssetCheck", "Vise2AssetCheck"],
        project_id="proj",
        platform="talend",
    )

    package_dir = staging / "_rag_support" / "phase2_analysis"
    assert sorted(p.name for p in package_dir.iterdir()) == [
        "VISE2AssetCheck.md",
        "Vise2AssetCheck_part_ci1.md",
    ]
    assert manifest["missing_groups"] == []
    by_group = {a["group"]: a for a in manifest["artifacts"]}
    assert by_group["VISE2AssetCheck"]["path"] == "phase2_analysis/VISE2AssetCheck.md"
    assert by_group["Vise2AssetCheck"]["path"] == (
        "phase2_analysis/Vise2AssetCheck_part_ci1.md"
    )
    assert by_group["Vise2AssetCheck"]["source_path"] == (
        "_analysis/Vise2AssetCheck_part_ci1.md"
    )
    # Contents must come from each group's OWN slice, not the case-colliding
    # sibling that Path lookup would resolve to on macOS/Windows.
    assert (package_dir / "Vise2AssetCheck_part_ci1.md").read_text(
        encoding="utf-8"
    ) == "# collider analysis"


def test_build_packages_split_part_slices(tmp_path: Path) -> None:
    # Large groups split into {group}_part1/{group}_part2 preprocess slices;
    # each slice gets its own analysis and both belong to the group.
    staging = tmp_path / "docs_staging"
    analysis = staging / "_analysis"
    analysis.mkdir(parents=True)
    (analysis / "USA_part1.md").write_text("# part 1", encoding="utf-8")
    (analysis / "USA_part2.md").write_text("# part 2", encoding="utf-8")

    manifest = build_rag_support_package(
        staging, ["USA"], project_id="proj", platform="talend"
    )

    package_dir = staging / "_rag_support" / "phase2_analysis"
    assert sorted(p.name for p in package_dir.iterdir()) == [
        "USA_part1.md",
        "USA_part2.md",
    ]
    assert manifest["missing_groups"] == []
    assert [(a["group"], a["path"]) for a in manifest["artifacts"]] == [
        ("USA", "phase2_analysis/USA_part1.md"),
        ("USA", "phase2_analysis/USA_part2.md"),
    ]


def test_build_excludes_part_files_of_nonmanifest_groups(tmp_path: Path) -> None:
    staging = _make_staging(tmp_path)
    analysis = staging / "_analysis"
    (analysis / "Zed_part_ci1.md").write_text("# not a manifest group", encoding="utf-8")
    (analysis / "C_part1.md").write_text("# C is not in the manifest", encoding="utf-8")

    manifest = build_rag_support_package(
        staging, ["A", "B"], project_id="proj", platform="talend"
    )

    package_dir = staging / "_rag_support" / "phase2_analysis"
    assert sorted(p.name for p in package_dir.iterdir()) == ["A.md", "B.md"]
    assert {a["group"] for a in manifest["artifacts"]} == {"A", "B"}


def test_build_part_slice_owned_by_longest_matching_group(tmp_path: Path) -> None:
    # "A_particular_part1" prefix-matches both "A" (via "A_part...") and
    # "A_particular"; the longest (most specific) group must own the slice.
    staging = tmp_path / "docs_staging"
    analysis = staging / "_analysis"
    analysis.mkdir(parents=True)
    (analysis / "A.md").write_text("# A", encoding="utf-8")
    (analysis / "A_particular_part1.md").write_text("# specific", encoding="utf-8")

    manifest = build_rag_support_package(
        staging, ["A", "A_particular"], project_id="proj", platform="talend"
    )

    by_path = {a["path"]: a["group"] for a in manifest["artifacts"]}
    assert by_path["phase2_analysis/A_particular_part1.md"] == "A_particular"
    assert by_path["phase2_analysis/A.md"] == "A"
    assert manifest["missing_groups"] == []


def test_cache_restore_preserves_part_ci_names(tmp_path: Path) -> None:
    # End-to-end naming contract: _part_ci slice filenames flow verbatim from
    # the published package into the flat analysis_cache/.
    output_dir = tmp_path / "output" / "proj"
    package_dir = output_dir / "docs" / "_rag_support" / "phase2_analysis"
    package_dir.mkdir(parents=True)
    (package_dir / "VISE2AssetCheck.md").write_text("# upper", encoding="utf-8")
    (package_dir / "Vise2AssetCheck_part_ci1.md").write_text("# collider", encoding="utf-8")

    assert replace_analysis_cache_from_rag_support(output_dir) is True

    cache_dir = output_dir / "analysis_cache"
    assert sorted(p.name for p in cache_dir.iterdir()) == [
        "VISE2AssetCheck.md",
        "Vise2AssetCheck_part_ci1.md",
    ]


def test_build_records_missing_groups_without_fallback(tmp_path: Path) -> None:
    staging = _make_staging(tmp_path)

    manifest = build_rag_support_package(
        staging, ["A", "GHOST"], project_id="proj", platform="talend"
    )

    assert manifest["missing_groups"] == ["GHOST"]
    package_dir = staging / "_rag_support" / "phase2_analysis"
    assert sorted(p.name for p in package_dir.iterdir()) == ["A.md"]


def test_build_removes_stale_package_files(tmp_path: Path) -> None:
    staging = _make_staging(tmp_path)
    stale_dir = staging / "_rag_support" / "phase2_analysis"
    stale_dir.mkdir(parents=True)
    (stale_dir / "OLD_GROUP.md").write_text("stale", encoding="utf-8")

    build_rag_support_package(staging, ["A"], project_id="proj", platform="talend")

    assert sorted(p.name for p in stale_dir.iterdir()) == ["A.md"]


def test_cache_restore_from_support_package(tmp_path: Path) -> None:
    output_dir = tmp_path / "output" / "proj"
    package_dir = output_dir / "docs" / "_rag_support" / "phase2_analysis"
    package_dir.mkdir(parents=True)
    (package_dir / "A.md").write_text("# Group A analysis", encoding="utf-8")
    (package_dir / "B.md").write_text("# Group B analysis", encoding="utf-8")

    restored = replace_analysis_cache_from_rag_support(output_dir)

    assert restored is True
    cache_dir = output_dir / "analysis_cache"
    assert sorted(p.name for p in cache_dir.iterdir()) == ["A.md", "B.md"]
    assert (cache_dir / "A.md").read_text(encoding="utf-8") == "# Group A analysis"


def test_cache_restore_replaces_stale_cache(tmp_path: Path) -> None:
    output_dir = tmp_path / "output" / "proj"
    cache_dir = output_dir / "analysis_cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "OLD.md").write_text("stale", encoding="utf-8")
    package_dir = output_dir / "docs" / "_rag_support" / "phase2_analysis"
    package_dir.mkdir(parents=True)
    (package_dir / "A.md").write_text("fresh", encoding="utf-8")

    restored = replace_analysis_cache_from_rag_support(output_dir)

    assert restored is True
    assert sorted(p.name for p in cache_dir.iterdir()) == ["A.md"]


# --------------------------------------------------------------------------- #
# The per-section DIRECTORY layout (slice 2 changed what Phase 2 writes)
# --------------------------------------------------------------------------- #

def _write_directory_group(analysis: Path, group: str, sections: dict[str, str]) -> None:
    (analysis / group).mkdir(parents=True, exist_ok=True)
    for name, body in sections.items():
        (analysis / group / f"{name}.md").write_text(body, encoding="utf-8")


def test_build_packages_a_directory_layout_group_as_one_artifact(
    tmp_path: Path,
) -> None:
    """The regression a glob would ship: ``_analysis/*.md`` matches nothing in
    the directory layout, so a complete estate would publish an empty package
    and the run would look successful."""
    staging = tmp_path / "docs_staging"
    analysis = staging / "_analysis"
    analysis.mkdir(parents=True)
    _write_directory_group(
        analysis,
        "fourps",
        {
            "COMPONENTS": "tDBOutput_1 writes SALESTABLE.",
            "SOURCE_SQL": "SELECT 1",
            "_summary": "one-line summary",
        },
    )

    manifest = build_rag_support_package(
        staging, ["fourps"], project_id="proj", platform="talend"
    )

    package_dir = staging / "_rag_support" / "phase2_analysis"
    assert [p.name for p in package_dir.iterdir()] == ["fourps.md"]
    assert manifest["missing_groups"] == []

    entry = manifest["artifacts"][0]
    assert entry["group"] == "fourps"
    assert entry["layout"] == "directory"
    assert entry["source_path"] == "_analysis/fourps/"
    assert entry["sections"] == ["COMPONENTS", "SOURCE_SQL"]

    text = (package_dir / "fourps.md").read_text(encoding="utf-8")
    assert "### COMPONENTS" in text and "### SOURCE_SQL" in text
    assert "tDBOutput_1 writes SALESTABLE." in text
    # The summary is a navigation aid in both layouts and has never been part
    # of the package; the directory layout must not smuggle it in.
    assert "one-line summary" not in text


def test_build_packages_both_layouts_in_one_estate(tmp_path: Path) -> None:
    """A resumed project has flat groups from before the migration next to
    directory groups written after it. Neither may be dropped."""
    staging = tmp_path / "docs_staging"
    analysis = staging / "_analysis"
    analysis.mkdir(parents=True)
    (analysis / "legacy.md").write_text("# legacy analysis\n", encoding="utf-8")
    (analysis / "legacy_summary.md").write_text("summary", encoding="utf-8")
    _write_directory_group(analysis, "migrated", {"COMPONENTS": "tMap_1"})

    manifest = build_rag_support_package(
        staging, ["legacy", "migrated"], project_id="proj", platform="talend"
    )

    package_dir = staging / "_rag_support" / "phase2_analysis"
    assert sorted(p.name for p in package_dir.iterdir()) == [
        "legacy.md",
        "migrated.md",
    ]
    layouts = {a["group"]: a["layout"] for a in manifest["artifacts"]}
    assert layouts == {"legacy": "flat", "migrated": "directory"}
    # A flat slice is copied byte-for-byte: anything hashing the package
    # against _analysis/ (readiness, the incremental cache) compares equal.
    assert (package_dir / "legacy.md").read_bytes() == (
        analysis / "legacy.md"
    ).read_bytes()


def test_build_packages_a_directory_part_ci_slice(tmp_path: Path) -> None:
    """Case-colliding groups keep their ``_part_ci{k}`` slice name as a
    DIRECTORY name too, and the slice->group resolution is unchanged."""
    staging = tmp_path / "docs_staging"
    analysis = staging / "_analysis"
    analysis.mkdir(parents=True)
    _write_directory_group(analysis, "Monitoring", {"COMPONENTS": "upper"})
    _write_directory_group(analysis, "monitoring_part_ci1", {"COMPONENTS": "collider"})

    manifest = build_rag_support_package(
        staging,
        ["Monitoring", "monitoring_part_ci1"],
        project_id="proj",
        platform="talend",
    )

    by_group = {a["group"]: a["path"] for a in manifest["artifacts"]}
    assert by_group == {
        "Monitoring": "phase2_analysis/Monitoring.md",
        "monitoring_part_ci1": "phase2_analysis/monitoring_part_ci1.md",
    }
    assert manifest["missing_groups"] == []


def test_a_group_summary_directory_file_is_not_a_group(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    analysis = staging / "_analysis"
    analysis.mkdir(parents=True)
    (analysis / "A.md").write_text("# A", encoding="utf-8")
    (analysis / "A_summary.md").write_text("summary of A", encoding="utf-8")

    manifest = build_rag_support_package(
        staging, ["A"], project_id="proj", platform="talend"
    )

    package_dir = staging / "_rag_support" / "phase2_analysis"
    assert [p.name for p in package_dir.iterdir()] == ["A.md"]
    assert [a["group"] for a in manifest["artifacts"]] == ["A"]


def test_an_unreadable_group_is_contained_and_the_rest_publishes(
    tmp_path: Path,
) -> None:
    """A directory holding no recognisable section raises SectionContractError
    by design (lost evidence must be loud). Losing the other 19 groups over it
    would be the fail-closed behaviour this lane has been removing."""
    staging = tmp_path / "docs_staging"
    analysis = staging / "_analysis"
    analysis.mkdir(parents=True)
    _write_directory_group(analysis, "good", {"COMPONENTS": "tMap_1"})
    _write_directory_group(analysis, "broken", {"notes": "free-form, no section"})

    manifest = build_rag_support_package(
        staging, ["good", "broken"], project_id="proj", platform="talend"
    )

    package_dir = staging / "_rag_support" / "phase2_analysis"
    assert [p.name for p in package_dir.iterdir()] == ["good.md"]
    assert [a["group"] for a in manifest["artifacts"]] == ["good"]
    assert [f["group"] for f in manifest["failed_slices"]] == ["broken"]
    assert "SectionContractError" in manifest["failed_slices"][0]["error"]


def test_the_manifest_records_internal_language_residue(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    analysis = staging / "_analysis"
    analysis.mkdir(parents=True)
    (analysis / "A.md").write_text(
        "Data source: /_preprocess/A.md\nBased on our analysis, nightly.\n",
        encoding="utf-8",
    )

    manifest = build_rag_support_package(
        staging, ["A"], project_id="proj", platform="talend"
    )

    entry = manifest["artifacts"][0]
    assert entry["internal_language_findings"] == 2
    assert entry["internal_language_redactable"] == 1
    assert manifest["internal_language"]["findings"] == 2
    assert manifest["internal_language"]["redactable"] == 1
    assert manifest["internal_language"]["enforced_at"] == "collect_internal_artifacts"
    # Diagnostics only: the bytes on disk are the incremental-regen cache and
    # stay verbatim.
    assert "_preprocess" in (
        staging / "_rag_support" / "phase2_analysis" / "A.md"
    ).read_text(encoding="utf-8")


def test_cache_restore_removes_cache_when_no_package(tmp_path: Path) -> None:
    output_dir = tmp_path / "output" / "proj"
    cache_dir = output_dir / "analysis_cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "OLD.md").write_text("stale", encoding="utf-8")
    (output_dir / "docs").mkdir()

    restored = replace_analysis_cache_from_rag_support(output_dir)

    assert restored is False
    assert not cache_dir.exists()


def test_cache_restore_treats_empty_package_as_absent(tmp_path: Path) -> None:
    output_dir = tmp_path / "output" / "proj"
    cache_dir = output_dir / "analysis_cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "OLD.md").write_text("stale", encoding="utf-8")
    package_dir = output_dir / "docs" / "_rag_support" / "phase2_analysis"
    package_dir.mkdir(parents=True)

    restored = replace_analysis_cache_from_rag_support(output_dir)

    assert restored is False
    assert not cache_dir.exists(), (
        "an empty package must not leave an empty cache dir — "
        "incremental regen must fall back to full regeneration"
    )


def test_cache_readiness_distinguishes_support_package_from_flat_cache(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "output" / "proj"
    docs = output_dir / "docs"
    support = docs / "_rag_support" / "phase2_analysis"
    cache = output_dir / "analysis_cache"
    support.mkdir(parents=True)
    cache.mkdir(parents=True)
    (docs / "01_ARCHITECTURE_MAP.md").write_text("# Architecture", encoding="utf-8")
    (support / "A.md").write_text("# A analysis", encoding="utf-8")
    (cache / "A.md").write_text("# A analysis", encoding="utf-8")

    readiness = inspect_generation_cache_readiness(
        output_dir,
        has_baseline=True,
        changed_groups={"A"},
    )

    assert readiness.has_docs is True
    assert readiness.has_rag_support is True
    assert readiness.has_analysis_cache is True
    assert readiness.rag_support_analysis_count == 1
    assert readiness.analysis_cache_count == 1
    assert readiness.targeted_regen_ready is True
    assert readiness.requires_full_rebaseline is False
    assert readiness.reason_codes == []


def test_cache_readiness_requires_rebaseline_when_support_is_missing(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "output" / "proj"
    docs = output_dir / "docs"
    cache = output_dir / "analysis_cache"
    docs.mkdir(parents=True)
    cache.mkdir(parents=True)
    (docs / "01_ARCHITECTURE_MAP.md").write_text("# Architecture", encoding="utf-8")
    (cache / "A.md").write_text("# A cache", encoding="utf-8")

    readiness = inspect_generation_cache_readiness(
        output_dir,
        has_baseline=True,
        changed_groups={"A"},
    )

    assert readiness.has_docs is True
    assert readiness.has_analysis_cache is True
    assert readiness.has_rag_support is False
    assert readiness.targeted_regen_ready is False
    assert readiness.requires_full_rebaseline is True
    assert readiness.reason_codes == ["missing_rag_support"]


def test_cache_readiness_detects_stale_flat_cache(tmp_path: Path) -> None:
    output_dir = tmp_path / "output" / "proj"
    docs = output_dir / "docs"
    support = docs / "_rag_support" / "phase2_analysis"
    cache = output_dir / "analysis_cache"
    support.mkdir(parents=True)
    cache.mkdir(parents=True)
    (docs / "01_ARCHITECTURE_MAP.md").write_text("# Architecture", encoding="utf-8")
    (support / "A.md").write_text("# Fresh analysis", encoding="utf-8")
    (cache / "A.md").write_text("# Stale analysis", encoding="utf-8")

    readiness = inspect_generation_cache_readiness(
        output_dir,
        has_baseline=True,
        changed_groups={"A"},
    )

    assert readiness.has_rag_support is True
    assert readiness.has_analysis_cache is True
    assert readiness.cache_support_match is False
    assert readiness.targeted_regen_ready is False
    assert readiness.requires_full_rebaseline is True
    assert readiness.reason_codes == ["analysis_cache_rag_support_mismatch"]


def test_cache_restore_makes_targeted_readiness_usable(tmp_path: Path) -> None:
    output_dir = tmp_path / "output" / "proj"
    docs = output_dir / "docs"
    package_dir = docs / "_rag_support" / "phase2_analysis"
    package_dir.mkdir(parents=True)
    (docs / "01_ARCHITECTURE_MAP.md").write_text("# Architecture", encoding="utf-8")
    (package_dir / "A.md").write_text("# Group A analysis", encoding="utf-8")

    assert replace_analysis_cache_from_rag_support(output_dir) is True

    readiness = inspect_generation_cache_readiness(
        output_dir,
        has_baseline=True,
        changed_groups={"A"},
    )
    assert readiness.targeted_regen_ready is True
    assert readiness.analysis_cache_count == 1
    assert readiness.rag_support_analysis_count == 1


# ---------------------------------------------------------------------------
# Internal artifact discovery (Slice 3)
# ---------------------------------------------------------------------------


def _make_docs_tree(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    pkg = docs / "_rag_support" / "phase2_analysis"
    pkg.mkdir(parents=True)
    (pkg / "Item.md").write_text("# Item analysis", encoding="utf-8")
    (pkg / "Training%7CJasmijn.md").write_text("# Training analysis", encoding="utf-8")
    (docs / "_rag_support" / "manifest.json").write_text(
        json.dumps(
            {
                "artifacts": [
                    {"path": "phase2_analysis/Item.md", "group": "Item"},
                    {"path": "phase2_analysis/Training%7CJasmijn.md", "group": "Training|Jasmijn"},
                ]
            }
        ),
        encoding="utf-8",
    )
    facts = docs / "_facts"
    facts.mkdir()
    (facts / "engineering_facts.md").write_text("# Facts", encoding="utf-8")
    (facts / "engineering_facts.json").write_text("{}", encoding="utf-8")
    (facts / "entity_index.csv").write_text(
        "id,entity_type,name,path,unit_name,confidence,evidence\n"
        'e1,component,tDBOutput_1,Item/Out/job.item,sjItem_OffRamp_DB,0.95,"[]"\n'
        'e2,component,tDBOutput_1,Item/Out/job.item,sjItem_OffRamp_DB,0.80,"[]"\n'
        'e3,table,SALESTABLE,Item/Out/job.item,sjItem_OffRamp_DB,1.0,"[]"\n',
        encoding="utf-8",
    )
    (facts / "usage_index.csv").write_text(
        "id,usage_type,name,entity_id,location,confidence,evidence\n"
        'u1,configured,connection_Demo_Login,,tDBOutput_1,0.85,"[]"\n',
        encoding="utf-8",
    )
    assessment = docs / "_assessment"
    assessment.mkdir()
    (assessment / "scorecard.md").write_text("# Scorecard", encoding="utf-8")
    slices = docs / "_evidence_registry" / "doc_slices"
    slices.mkdir(parents=True)
    (slices / "01_ARCHITECTURE_MAP.md").write_text("# Slice", encoding="utf-8")
    (slices / "empty.md").write_text("", encoding="utf-8")
    (docs / "01_OVERVIEW.md").write_text("# Visible doc", encoding="utf-8")
    return docs


def test_internal_doc_filename_encodes_unsafe_characters() -> None:
    assert internal_doc_filename("technical_analysis", "Training|Jasmijn.md") == (
        "_internal/technical_analysis/Training%7CJasmijn.md"
    )
    assert internal_doc_filename("scorecard", "scorecard.md") == (
        "_internal/scorecard/scorecard.md"
    )
    assert "/" not in internal_doc_filename("support_maps", "a/b.md").removeprefix(
        "_internal/support_maps/"
    )


def test_collect_internal_artifacts_layers_and_metadata(tmp_path: Path) -> None:
    docs = _make_docs_tree(tmp_path)

    artifacts = collect_internal_artifacts(docs, project_id="proj", run_id=209)

    by_layer: dict[str, list[dict]] = {}
    for a in artifacts:
        by_layer.setdefault(a["metadata"]["source_layer"], []).append(a)

    assert sorted(by_layer) == [
        "evidence_slices", "scorecard", "support_maps", "technical_analysis",
    ]
    assert len(by_layer["technical_analysis"]) == 2
    assert len(by_layer["support_maps"]) == 1, "only .md from _facts, never csv/json"
    assert len(by_layer["evidence_slices"]) == 1, "empty slice files are dropped"

    item = next(a for a in by_layer["technical_analysis"] if a["metadata"]["group"] == "Item")
    assert item["filename"] == "_internal/technical_analysis/Item.md"
    assert item["metadata"]["visibility"] == "internal"
    assert item["metadata"]["artifact_type"] == "phase2_group_analysis"
    assert item["metadata"]["artifact_path"] == "_rag_support/phase2_analysis/Item.md"
    assert item["metadata"]["project_id"] == "proj"
    assert item["metadata"]["run_id"] == 209

    scorecard = by_layer["scorecard"][0]
    assert scorecard["filename"] == "_internal/scorecard/scorecard.md"
    assert scorecard["metadata"]["artifact_type"] == "scorecard"

    names = {a["filename"] for a in artifacts}
    assert not any("01_OVERVIEW" in n for n in names), "visible docs are never internal"


def test_collect_redacts_internal_language_out_of_the_analysis_layer(
    tmp_path: Path,
) -> None:
    """Slice 8, enforced. Marking the layer internal is not enough: a rung-2
    escalation merges these chunks as auxiliary sources that receive reference
    numbers and are citable, so the text can be quoted back to a client."""
    docs = tmp_path / "docs"
    pkg = docs / "_rag_support" / "phase2_analysis"
    pkg.mkdir(parents=True)
    pkg.joinpath("Item.md").write_text(
        "Risk calibration: from /_assessment/quick_scan.md (Priority 18/20).\n"
        "tDBOutput_1 writes SALESTABLE nightly.\n",
        encoding="utf-8",
    )

    artifact = collect_internal_artifacts(docs, project_id="proj")[0]

    assert artifact["metadata"]["source_layer"] == "technical_analysis"
    assert artifact["metadata"]["visibility"] == "internal"
    assert artifact["metadata"]["internal_language_redactions"] == 1
    assert "_assessment" not in artifact["content"]
    assert "quick_scan.md" not in artifact["content"]
    # ...and the evidence that makes this layer worth publishing survives.
    assert "tDBOutput_1 writes SALESTABLE nightly." in artifact["content"]
    # The file on disk is untouched: it is also the incremental-regen cache.
    assert "_assessment" in pkg.joinpath("Item.md").read_text(encoding="utf-8")


def test_collect_does_not_redact_the_deterministic_support_layers(
    tmp_path: Path,
) -> None:
    """``_facts/engineering_facts.md`` is a Python-rendered map whose SUBJECT
    is artifact names. Blanking them would not remove leakage, it would remove
    the content."""
    docs = tmp_path / "docs"
    facts = docs / "_facts"
    facts.mkdir(parents=True)
    body = "Source: dependency_map.md, generated from scan_result.json\n"
    facts.joinpath("engineering_facts.md").write_text(body, encoding="utf-8")

    artifact = collect_internal_artifacts(docs, project_id="proj")[0]

    assert artifact["metadata"]["source_layer"] == "support_maps"
    assert artifact["content"] == body
    assert "internal_language_redactions" not in artifact["metadata"]


def test_collect_internal_artifacts_missing_layers_is_noop(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    assert collect_internal_artifacts(docs, project_id="proj") == []


def test_build_entity_aliases_dedupes_and_sorts(tmp_path: Path) -> None:
    docs = _make_docs_tree(tmp_path)

    aliases = build_entity_aliases(docs)

    keys = [(a["alias"], a["entity_type"], a["source_artifact"]) for a in aliases]
    assert keys == [
        ("connection_Demo_Login", "configured", "usage_index.csv"),
        ("SALESTABLE", "table", "entity_index.csv"),
        ("tDBOutput_1", "component", "entity_index.csv"),
    ]
    tdb = next(a for a in aliases if a["alias"] == "tDBOutput_1")
    assert tdb["confidence"] == 0.95, "duplicate rows keep the highest confidence"
    assert tdb["alias_normalized"] == "tdboutput_1"
    assert tdb["unit_name"] == "sjItem_OffRamp_DB"
    usage = next(a for a in aliases if a["alias"] == "connection_Demo_Login")
    assert usage["source_path"] == "tDBOutput_1"


def test_build_entity_aliases_missing_csvs_is_noop(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    assert build_entity_aliases(docs) == []


# ---------------------------------------------------------------------------
# Slice 2: _doc_pack_manifest.json persistence
# ---------------------------------------------------------------------------

def test_build_copies_doc_pack_manifest(tmp_path: Path) -> None:
    staging = _make_staging(tmp_path)
    doc_pack_data = {"source_analyses": ["A.md"], "doc_packs": {"01_ARCH": {"path": "_doc_pack_01_ARCH.md"}}}
    (staging / "_analysis" / "_doc_pack_manifest.json").write_text(
        json.dumps(doc_pack_data), encoding="utf-8"
    )

    build_rag_support_package(staging, ["A", "B"], project_id="proj", platform="talend")

    dest = staging / "_rag_support" / "_doc_pack_manifest.json"
    assert dest.is_file(), "_doc_pack_manifest.json must be copied into _rag_support/"
    assert json.loads(dest.read_text(encoding="utf-8")) == doc_pack_data


def test_build_ignores_missing_doc_pack_manifest(tmp_path: Path) -> None:
    # Phase 2.8 may not run on every resume; the copy is optional.
    staging = _make_staging(tmp_path)
    assert not (staging / "_analysis" / "_doc_pack_manifest.json").exists()

    # Must succeed without error.
    build_rag_support_package(staging, ["A", "B"], project_id="proj", platform="talend")

    assert not (staging / "_rag_support" / "_doc_pack_manifest.json").exists()
