"""Layout resolution for flat and partitioned project documentation trees.

Regression origin: a partitioned project (composite ZIP upload -- one
sub-project per partition) reported 81 documents in the project picker, which
counts the ``{subproject}/{name}.md`` rows the orchestrator pushes to RAG, and
listed ZERO in the doc viewer, which globbed the flat ``output/{pid}/docs``
directory that a partitioned project never has.
"""

from __future__ import annotations

import pytest

from mcp_server.config import config
from mcp_server.services import doc_layout
from mcp_server.services.doc_layout import (
    PUBLISHED,
    STAGING,
    doc_locations,
    is_safe_path_segment,
    resolve_doc_location,
)
from mcp_server.services.pdf_service import PdfService

DOC_NAMES = ["00_AGENT_BRIEF.md", "09_EXECUTIVE_SUMMARY.md"]


@pytest.fixture
def output_root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "project_root", tmp_path)
    root = tmp_path / "output"
    root.mkdir()
    return root


def _write_docs(docs_dir, names=DOC_NAMES):
    docs_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (docs_dir / name).write_text(f"# {name}\n", encoding="utf-8")


def test_flat_project_yields_one_unlabelled_location(output_root):
    _write_docs(output_root / "flatproj" / "docs")

    locations = doc_locations("flatproj")

    assert [loc.subproject for loc in locations] == [None]
    assert locations[0].docs_dir == output_root / "flatproj" / "docs"


def test_partitioned_project_yields_one_location_per_subproject(output_root):
    for sub in ("VQ_MQ2RHM", "Assetcheck", "ETTM_part1"):
        _write_docs(output_root / "werk" / sub / "docs")

    locations = doc_locations("werk")

    # Sorted case-insensitively for a stable picker order.
    assert [loc.subproject for loc in locations] == [
        "Assetcheck",
        "ETTM_part1",
        "VQ_MQ2RHM",
    ]


def test_sibling_artifact_dirs_are_not_subprojects(output_root):
    """The partition orchestrator writes these beside the sub-project trees."""
    _write_docs(output_root / "werk" / "Assetcheck" / "docs")
    for reserved in ("topology", "logs", "experiments", "supporting-evidence"):
        _write_docs(output_root / "werk" / reserved / "docs")
    _write_docs(output_root / "werk" / "_internal" / "docs")

    assert [loc.subproject for loc in doc_locations("werk")] == ["Assetcheck"]


def test_partitions_shadow_a_stale_flat_tree(output_root):
    """A project re-uploaded as a composite keeps its pre-partition docs on disk.

    Listing them next to current ones with no sub-project label is how an
    operator reads a superseded document believing it is live.
    """
    _write_docs(output_root / "werk" / "docs", ["OLD_SINGLE_RUN.md"])
    _write_docs(output_root / "werk" / "Assetcheck" / "docs")

    assert [loc.subproject for loc in doc_locations("werk")] == ["Assetcheck"]


def test_project_with_no_output_yields_nothing(output_root):
    assert doc_locations("never_generated") == []


def test_symlinked_subproject_is_not_listed(output_root, tmp_path):
    """It would advertise documents that get_doc_path then refuses to serve."""
    _write_docs(output_root / "werk" / "Assetcheck" / "docs")
    outside = tmp_path / "outside"
    _write_docs(outside / "docs")
    (output_root / "werk" / "Escape").symlink_to(outside)

    assert [loc.subproject for loc in doc_locations("werk")] == ["Assetcheck"]
    assert PdfService().get_doc_path(
        "werk", DOC_NAMES[0], subproject="Escape"
    ) is None


def test_staging_source_is_resolved_independently(output_root):
    _write_docs(output_root / "werk" / "ETTM" / "docs_staging")
    _write_docs(output_root / "werk" / "Assetcheck" / "docs")

    published = [loc.subproject for loc in doc_locations("werk", source=PUBLISHED)]
    staging = [loc.subproject for loc in doc_locations("werk", source=STAGING)]

    assert published == ["Assetcheck"]
    assert staging == ["ETTM"]


def test_unknown_source_is_rejected(output_root):
    with pytest.raises(ValueError):
        doc_locations("werk", source="drafts")


class TestResolveDocLocation:
    def test_bare_name_resolves_only_for_a_flat_project(self, output_root):
        _write_docs(output_root / "flatproj" / "docs")
        _write_docs(output_root / "werk" / "Assetcheck" / "docs")

        assert resolve_doc_location("flatproj", None) is not None
        # Ambiguous in a partitioned project -- must be refused, not guessed.
        assert resolve_doc_location("werk", None) is None

    def test_known_subproject_resolves(self, output_root):
        _write_docs(output_root / "werk" / "Assetcheck" / "docs")

        location = resolve_doc_location("werk", "Assetcheck")

        assert location is not None
        assert location.docs_dir == output_root / "werk" / "Assetcheck" / "docs"

    def test_unknown_subproject_is_refused(self, output_root):
        _write_docs(output_root / "werk" / "Assetcheck" / "docs")

        assert resolve_doc_location("werk", "NotAPartition") is None

    @pytest.mark.parametrize(
        "segment", ["../../etc", "..", "a/b", "a\\b", ".hidden", ""]
    )
    def test_traversal_segments_are_refused(self, output_root, segment):
        _write_docs(output_root / "werk" / "Assetcheck" / "docs")

        assert not is_safe_path_segment(segment)
        assert resolve_doc_location("werk", segment) is None


class TestPdfServiceOnPartitionedProjects:
    def test_list_docs_labels_every_doc_with_its_subproject(self, output_root):
        for sub in ("Assetcheck", "VQ_MQ2RHM"):
            _write_docs(output_root / "werk" / sub / "docs")

        docs = PdfService().list_docs("werk")

        assert len(docs) == 4
        assert {(d["subproject"], d["name"]) for d in docs} == {
            ("Assetcheck", "00_AGENT_BRIEF.md"),
            ("Assetcheck", "09_EXECUTIVE_SUMMARY.md"),
            ("VQ_MQ2RHM", "00_AGENT_BRIEF.md"),
            ("VQ_MQ2RHM", "09_EXECUTIVE_SUMMARY.md"),
        }

    def test_list_docs_on_a_flat_project_leaves_subproject_null(self, output_root):
        _write_docs(output_root / "flatproj" / "docs")

        docs = PdfService().list_docs("flatproj")

        assert [d["subproject"] for d in docs] == [None, None]

    def test_internal_artifacts_stay_excluded(self, output_root):
        _write_docs(
            output_root / "werk" / "Assetcheck" / "docs",
            ["00_AGENT_BRIEF.md", "_manifest.md", "dependency_map.md"],
        )

        names = {d["name"] for d in PdfService().list_docs("werk")}

        assert names == {"00_AGENT_BRIEF.md"}

    def test_pdf_presence_is_read_from_the_partitions_own_pdf_dir(self, output_root):
        for sub in ("Assetcheck", "VQ_MQ2RHM"):
            _write_docs(output_root / "werk" / sub / "docs")
        pdf_dir = output_root / "werk" / "Assetcheck" / "pdf"
        pdf_dir.mkdir(parents=True)
        (pdf_dir / "00_AGENT_BRIEF.pdf").write_bytes(b"%PDF-")

        docs = PdfService().list_docs("werk")
        with_pdf = {(d["subproject"], d["name"]) for d in docs if d["has_pdf"]}

        # Identical doc names across sub-projects must not borrow each other's PDF.
        assert with_pdf == {("Assetcheck", "00_AGENT_BRIEF.md")}

    def test_get_doc_path_disambiguates_identical_names(self, output_root):
        for sub in ("Assetcheck", "VQ_MQ2RHM"):
            _write_docs(output_root / "werk" / sub / "docs")

        svc = PdfService()
        path = svc.get_doc_path("werk", "00_AGENT_BRIEF.md", subproject="VQ_MQ2RHM")

        assert path == output_root / "werk" / "VQ_MQ2RHM" / "docs" / "00_AGENT_BRIEF.md"

    def test_get_doc_path_refuses_a_bare_name_when_partitioned(self, output_root):
        _write_docs(output_root / "werk" / "Assetcheck" / "docs")

        assert PdfService().get_doc_path("werk", "00_AGENT_BRIEF.md") is None

    def test_get_doc_path_still_works_unqualified_when_flat(self, output_root):
        _write_docs(output_root / "flatproj" / "docs")

        path = PdfService().get_doc_path("flatproj", "00_AGENT_BRIEF.md")

        assert path == output_root / "flatproj" / "docs" / "00_AGENT_BRIEF.md"

    def test_get_doc_path_rejects_traversal_in_either_segment(self, output_root):
        _write_docs(output_root / "werk" / "Assetcheck" / "docs")
        svc = PdfService()

        assert svc.get_doc_path("werk", "../../../etc/passwd") is None
        assert svc.get_doc_path("werk", "00_AGENT_BRIEF.md", subproject="..") is None

    def test_sibling_project_prefix_is_not_treated_as_inside_the_root(
        self, output_root, monkeypatch
    ):
        """``/output/werk`` must not admit paths under ``/output/werkorder``."""
        _write_docs(output_root / "werk" / "docs")
        _write_docs(output_root / "werkorder" / "docs")

        # A resolved path is checked with is_relative_to, not string prefixing;
        # a name can never escape its own project directory.
        path = PdfService().get_doc_path("werk", "00_AGENT_BRIEF.md")
        assert path is not None
        assert path.parent.parent.name == "werk"

    def test_convert_project_covers_every_subproject(self, output_root, monkeypatch):
        for sub in ("Assetcheck", "VQ_MQ2RHM"):
            _write_docs(output_root / "werk" / sub / "docs")
        converted: list[str] = []
        monkeypatch.setattr(
            PdfService,
            "convert_file",
            lambda self, md_path: converted.append(
                f"{md_path.parent.parent.name}/{md_path.name}"
            ),
        )

        result = PdfService().convert_project("werk")

        assert result["converted"] == 4
        assert sorted(converted) == [
            "Assetcheck/00_AGENT_BRIEF.md",
            "Assetcheck/09_EXECUTIVE_SUMMARY.md",
            "VQ_MQ2RHM/00_AGENT_BRIEF.md",
            "VQ_MQ2RHM/09_EXECUTIVE_SUMMARY.md",
        ]

    def test_convert_project_reports_no_docs_dir_by_output_root(self, output_root):
        result = PdfService().convert_project("never_generated")

        assert result["converted"] == 0
        assert "never_generated" in result["errors"][0]


def test_project_output_root_is_the_single_path_builder(output_root):
    assert doc_layout.project_output_root("werk") == output_root / "werk"
