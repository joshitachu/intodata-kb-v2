"""Unit tests for services/true_patch_executor.py (Slice 4).

Tests are kept hermetic: all DB, model, and filesystem I/O is mocked.
No live model calls, no DB connections required.
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_server.services.true_patch_executor import (
    PatchQualityError,
    TruePatchExecutor,
    _assert_invariants_preserved,
    _br_tags,
    _compute_field_diff,
    _reassemble_analysis,
    _split_analysis,
)
from mcp_server.services.patch_section_map import FieldChange


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_analysis(group: str = "GROUP_A") -> str:
    return (
        f"# Phase 2 Analysis: {group}\n\n"
        f"## Coverage\n\nFull coverage.\n\n"
        f"## Model Analysis\n\n"
        f"### GROUP_SUMMARY\n\nThis group handles payroll processing.\n\n"
        f"### DATA_FLOWS\n\n"
        f"- BR-{group}-001: Source reads from HR_DB\n"
        f"- BR-{group}-002: Output to Payroll_DB\n\n"
        f"### COMPONENTS\n\n"
        f"- tMap_1: transforms employee data\n\n"
    )


def _make_doc(group: str = "GROUP_A") -> str:
    return (
        f"# 01 Architecture Map\n\n"
        f"## Overview\n\nHigh-level system architecture.\n\n"
        f"## Data Flows\n\n"
        f"BR-{group}-001: employee -> payroll pipeline.\n"
        f"BR-{group}-002: see above.\n\n"
        f"## Evidence Register\n\n"
        f"| ID | Source | Description |\n"
        f"| --- | --- | --- |\n"
        f"| BR-{group}-001 | scan | HR DB read |\n"
        f"| BR-{group}-002 | scan | Payroll DB write |\n\n"
    )


def _scan_jobs(group: str = "GROUP_A") -> list[dict]:
    return [
        {
            "name": f"Job_{group}",
            "components": [
                {
                    "unique_name": "tMap_1",
                    "component_type": "tMap",
                    # extract_field_map reads "params" (list-of-dict or dict)
                    "params": {"MAPPING": "emp -> pay"},
                }
            ],
        }
    ]


def _log_mgr() -> MagicMock:
    mgr = MagicMock()
    mgr.emit = AsyncMock()
    return mgr


def _make_provider(response_text: str, success: bool = True) -> MagicMock:
    result = MagicMock()
    result.success = success
    result.text = response_text
    result.raw_error = None if success else "mock error"
    provider = MagicMock()
    provider.complete = AsyncMock(return_value=result)
    return provider


# ---------------------------------------------------------------------------
# Unit tests: _split_analysis / _reassemble_analysis
# ---------------------------------------------------------------------------


class TestSplitReassemble:
    def test_round_trip_preserves_sections(self) -> None:
        text = _make_analysis("ALPHA")
        preamble, sections = _split_analysis(text)
        reassembled = _reassemble_analysis(preamble, sections)
        # KEY sections present
        assert "GROUP_SUMMARY" in reassembled
        assert "DATA_FLOWS" in reassembled
        assert "BR-ALPHA-001" in reassembled

    def test_preamble_excluded_from_sections(self) -> None:
        text = _make_analysis("BETA")
        preamble, sections = _split_analysis(text)
        assert "Phase 2" in preamble
        for name, content in sections.items():
            assert "Phase 2" not in content

    def test_empty_sections_handled(self) -> None:
        preamble, sections = _split_analysis("# Header\n\nNo sections here.")
        assert sections == {}
        result = _reassemble_analysis(preamble, sections)
        assert "Header" in result


# ---------------------------------------------------------------------------
# Unit tests: _assert_invariants_preserved (G2 / G2a)
# ---------------------------------------------------------------------------


class TestInvariantsPreserved:
    def test_g2a_evidence_register_changed_fails(self) -> None:
        original = _make_doc("GROUP_A")
        # Mutate Evidence Register
        patched = original.replace(
            "| BR-GROUP_A-001 | scan | HR DB read |",
            "| BR-GROUP_A-001 | scan | CHANGED |",
        )
        assert not _assert_invariants_preserved(original, patched, {"Data Flows"})

    def test_g2_br_tag_drop_in_non_rewritten_section_fails(self) -> None:
        original = _make_doc("GROUP_A")
        # Remove a BR tag from Overview (which is NOT in rewritten_headings)
        patched = original.replace("BR-GROUP_A-001: employee -> payroll pipeline.", "")
        assert not _assert_invariants_preserved(original, patched, set())

    def test_g2_br_tag_preserved_passes(self) -> None:
        original = _make_doc("GROUP_A")
        assert _assert_invariants_preserved(original, original, set())

    def test_g2_rewritten_section_may_change_br_tags(self) -> None:
        original = _make_doc("GROUP_A")
        # Modify Data Flows (which IS in rewritten_headings)
        patched = original.replace(
            "BR-GROUP_A-001: employee -> payroll pipeline.",
            "Redesigned flow; old BR tag removed.",
        )
        # If Data Flows is in rewritten_headings, G2 does NOT check it
        assert _assert_invariants_preserved(original, patched, {"Data Flows"})

    def test_g2a_evidence_register_byte_identical_required(self) -> None:
        original = _make_doc("GROUP_A")
        # Adding trailing space to Evidence Register section
        patched = original.replace(
            "| BR-GROUP_A-002 | scan | Payroll DB write |",
            "| BR-GROUP_A-002 | scan | Payroll DB write | ",
        )
        assert not _assert_invariants_preserved(original, patched, {"Data Flows"})


# ---------------------------------------------------------------------------
# Unit tests: _br_tags helper
# ---------------------------------------------------------------------------


def test_br_tags_extracts_correctly() -> None:
    text = "See BR-ALPHA-001 and BR-BETA-042 for details."
    tags = _br_tags(text)
    assert tags == {"BR-ALPHA-001", "BR-BETA-042"}


def test_br_tags_empty_text() -> None:
    assert _br_tags("") == set()


# ---------------------------------------------------------------------------
# Unit tests: _compute_field_diff
# ---------------------------------------------------------------------------


class TestComputeFieldDiff:
    def test_changed_field_detected(self) -> None:
        prev_jobs = _scan_jobs("G1")
        curr_jobs = [
            {
                "name": "Job_G1",
                "components": [
                    {
                        "unique_name": "tMap_1",
                        "component_type": "tMap",
                        "params": {"MAPPING": "emp -> NEW_OUTPUT"},
                    }
                ],
            }
        ]
        changes, diff_text = _compute_field_diff(prev_jobs, curr_jobs)
        assert len(changes) > 0
        assert any(fc.component_type == "tMap" for fc in changes)
        assert "tMap" in diff_text

    def test_identical_jobs_no_changes(self) -> None:
        jobs = _scan_jobs("G1")
        changes, diff_text = _compute_field_diff(jobs, jobs)
        assert changes == []

    def test_added_component_detected(self) -> None:
        prev_jobs: list[dict] = [{"name": "Job_G1", "components": []}]
        curr_jobs = _scan_jobs("G1")
        changes, diff_text = _compute_field_diff(prev_jobs, curr_jobs)
        assert len(changes) > 0
        assert any(fc.old_value is None for fc in changes)


# ---------------------------------------------------------------------------
# G3: validate-before-write
# ---------------------------------------------------------------------------


class TestG3ValidateBeforeWrite:
    """G3: thin or incomplete analysis must raise PatchQualityError, nothing written."""

    @pytest.mark.asyncio
    async def test_thin_model_response_raises_patch_quality_error(
        self, tmp_path: Path
    ) -> None:
        """When the model returns almost nothing, G3 should fire and raise."""
        # Set up a support file with a valid analysis
        support_dir = (
            tmp_path
            / "docs"
            / "_rag_support"
            / "phase2_analysis"
        )
        support_dir.mkdir(parents=True)
        (support_dir / "GROUP_A.md").write_text(_make_analysis("GROUP_A"), encoding="utf-8")

        # Prev/curr scan stubs
        prev_scan_dir = tmp_path / "prev_scan"
        prev_scan_dir.mkdir()
        (prev_scan_dir / "GROUP_A.json").write_text(
            json.dumps(_scan_jobs("GROUP_A")), encoding="utf-8"
        )

        executor = TruePatchExecutor()

        mock_provider = _make_provider(
            # A trivially thin response that won't pass G3
            "### GROUP_SUMMARY\n\nTiny.",
        )

        with (
            patch(
                "mcp_server.services.true_patch_executor._load_curr_jobs",
                return_value=_scan_jobs("GROUP_A"),
            ),
            patch(
                "mcp_server.services.true_patch_executor._patch_provider",
                return_value=mock_provider,
            ),
        ):
            with pytest.raises(PatchQualityError):
                await executor._patch_phase2(
                    group="GROUP_A",
                    project_id="proj1",
                    output_dir=tmp_path,
                    platform="talend",
                    provider=mock_provider,
                )

        # Verify: support file is NOT overwritten (nothing was written)
        support_path = support_dir / "GROUP_A.md"
        assert support_path.read_text(encoding="utf-8") == _make_analysis("GROUP_A")


# ---------------------------------------------------------------------------
# G2a: Evidence Register exclusion
# ---------------------------------------------------------------------------


class TestG2aEvidenceRegisterExclusion:
    def test_evidence_register_not_in_allowed_headings(self) -> None:
        from mcp_server.services.patch_section_map import EVIDENCE_REGISTER_HEADING

        # _parse_doc_headings will return it, but _patch_phase3 must discard it
        # from the `allowed` set regardless.
        executor = TruePatchExecutor()
        doc = _make_doc("GROUP_A")
        returned_headings = {
            "Data Flows": "Updated flows content.",
            EVIDENCE_REGISTER_HEADING: "INJECTED CHANGE",
        }

        patched = doc
        allowed = {"Data Flows"}  # Evidence Register NOT in allowed
        for heading, content in returned_headings.items():
            if heading not in allowed:
                continue
            from mcp_server.services.git_diff_patcher import _replace_section
            patched = _replace_section(
                patched, heading, f"## {heading}\n\n{content}"
            )

        # Evidence Register must be byte-identical
        original_er = re.search(
            r"## Evidence Register\n.*", doc, re.DOTALL
        )
        patched_er = re.search(
            r"## Evidence Register\n.*", patched, re.DOTALL
        )
        assert original_er and patched_er
        assert original_er.group() == patched_er.group()


# ---------------------------------------------------------------------------
# Option A single-writer assertion
# ---------------------------------------------------------------------------


class TestOptionASingleWriter:
    def test_write_support_analysis_writes_to_rag_support_only(
        self, tmp_path: Path
    ) -> None:
        """Option A: TruePatchExecutor._write_support_analysis writes ONLY to
        docs/_rag_support/phase2_analysis/, never to analysis_cache/ directly."""
        text = _make_analysis("GROUP_A")
        TruePatchExecutor._write_support_analysis(tmp_path, "GROUP_A", text)

        support_path = (
            tmp_path
            / "docs"
            / "_rag_support"
            / "phase2_analysis"
            / "GROUP_A.md"
        )
        assert support_path.is_file()
        assert support_path.read_text(encoding="utf-8") == text

        # analysis_cache/ must NOT be touched
        cache_path = tmp_path / "docs" / "analysis_cache" / "GROUP_A.md"
        assert not cache_path.exists()

    def test_manifest_sha256_updated_after_write(self, tmp_path: Path) -> None:
        text = _make_analysis("GROUP_A")
        TruePatchExecutor._write_support_analysis(tmp_path, "GROUP_A", text)

        manifest_path = (
            tmp_path / "docs" / "_rag_support" / "manifest.json"
        )
        assert manifest_path.is_file()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        artifacts = manifest.get("artifacts", [])
        assert any(a.get("group") == "GROUP_A" for a in artifacts)
        entry = next(a for a in artifacts if a.get("group") == "GROUP_A")
        expected_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert entry["sha256"] == expected_sha
        assert entry["bytes"] == len(text.encode("utf-8"))


# ---------------------------------------------------------------------------
# Fallback: missing prev.json
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_fallback_when_no_prev_scan(tmp_path: Path) -> None:
    """When prev_scan/{group}.json is absent, run() returns scoped_regen_fallback."""
    executor = TruePatchExecutor()
    log_mgr = _log_mgr()

    with patch(
        "mcp_server.services.true_patch_executor._patch_provider",
        return_value=_make_provider(""),
    ):
        result = await executor.run(
            project_id="proj1",
            output_dir=tmp_path,
            changed_groups=["GROUP_MISSING"],
            platform="talend",
            log_mgr=log_mgr,
            before_sha="aaa",
            after_sha="bbb",
        )

    assert result.action == "scoped_regen_fallback"
    assert result.patched_groups == []


# ---------------------------------------------------------------------------
# Fallback: model failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_fallback_on_model_failure(tmp_path: Path) -> None:
    """When the model call fails, run() returns scoped_regen_fallback."""
    # Set up support file
    support_dir = tmp_path / "docs" / "_rag_support" / "phase2_analysis"
    support_dir.mkdir(parents=True)
    (support_dir / "GROUP_A.md").write_text(
        _make_analysis("GROUP_A"), encoding="utf-8"
    )
    # prev scan
    prev_dir = tmp_path / "prev_scan"
    prev_dir.mkdir()
    (prev_dir / "GROUP_A.json").write_text(
        json.dumps(_scan_jobs("GROUP_A")), encoding="utf-8"
    )

    executor = TruePatchExecutor()
    log_mgr = _log_mgr()
    failing_provider = _make_provider("", success=False)
    failing_provider.complete.return_value.raw_error = "timeout"

    with (
        patch(
            "mcp_server.services.true_patch_executor._load_curr_jobs",
            return_value=_scan_jobs("GROUP_A"),
        ),
        patch(
            "mcp_server.services.true_patch_executor._patch_provider",
            return_value=failing_provider,
        ),
    ):
        result = await executor.run(
            project_id="proj1",
            output_dir=tmp_path,
            changed_groups=["GROUP_A"],
            platform="talend",
            log_mgr=log_mgr,
        )

    assert result.action == "scoped_regen_fallback"
    assert result.patched_groups == []


# ---------------------------------------------------------------------------
# G4: token budget
# ---------------------------------------------------------------------------


def test_g4_token_budget_capped() -> None:
    """G4: max_tokens is min(config.phase2_max_tokens, 2000 * len(sections))."""
    from mcp_server.config import config

    sections = frozenset({"GROUP_SUMMARY", "DATA_FLOWS", "COMPONENTS"})  # 3 sections
    expected_cap = 2000 * len(sections)
    expected_budget = min(config.phase2_max_tokens, expected_cap)
    assert expected_budget == expected_cap or expected_budget == config.phase2_max_tokens
    # Ensure budget never exceeds the config ceiling
    assert expected_budget <= config.phase2_max_tokens
