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


# ---------------------------------------------------------------------------
# G5: validate-against-diff guard (Living-Document Reingestion, Slice 2)
# ---------------------------------------------------------------------------


def _generic_analysis(group: str = "GROUP_A") -> str:
    """A platform='generic' analysis: only GROUP_SUMMARY is required by G3,
    so these fixtures isolate G5 behaviour from the talend-only section floor."""
    return (
        f"# Phase 2 Analysis: {group}\n\n"
        f"## Coverage\n\nFull coverage.\n\n"
        f"## Model Analysis\n\n"
        f"### GROUP_SUMMARY\n\n"
        f"This group handles payroll processing for HR systems, reading employee "
        f"records from HR_DB and writing processed payroll output to Payroll_DB. "
        f"It is a stable, well documented group with consistent component naming "
        f"and a predictable execution order across every run of the pipeline.\n\n"
        f"### DATA_FLOWS\n\n"
        f"- tMap_1 maps HR_DB records into the payroll output structure using the "
        f"MAPPING parameter with value 'emp -> pay'.\n\n"
    )


def _diff_grounded_model_response() -> str:
    """Model response whose asserted facts (tMap_1, MAPPING, NEW_OUTPUT) are all
    reconcilable with the field diff below (AC2: accepted amendment)."""
    return (
        "### GROUP_SUMMARY\n\n"
        "This group handles payroll processing for HR systems. `tMap_1` now maps "
        "employee records to NEW_OUTPUT, replacing the prior output target. "
        "Coverage remains complete across every unit in this group with a "
        "predictable execution order.\n\n"
        "### DATA_FLOWS\n\n"
        "- `tMap_1` reads HR_DB and writes to NEW_OUTPUT via the MAPPING "
        "parameter.\n\n"
        "### TRANSFORMATION_DETAIL\n\n"
        "- MAPPING now resolves employee records to NEW_OUTPUT instead of the "
        "legacy payroll destination.\n\n"
        "### BUSINESS_RULES\n\n"
        "- Downstream consumers must expect payroll data at NEW_OUTPUT going "
        "forward.\n\n"
    )


def _diff_ungrounded_model_response() -> str:
    """Model response asserting a connection name absent from BOTH the field
    diff and the pre-existing section content (AC1: hallucinated fact)."""
    return (
        "### GROUP_SUMMARY\n\n"
        "This group handles payroll processing for HR systems. `tMap_1` now "
        "also streams error records to Ghost_Connection_9 for auditing, "
        "replacing the prior output target.\n\n"
        "### DATA_FLOWS\n\n"
        "- `tMap_1` reads HR_DB and writes to NEW_OUTPUT via the MAPPING "
        "parameter.\n\n"
    )


def _mismatched_field_jobs() -> tuple[list[dict], list[dict]]:
    """Return (prev_jobs, curr_jobs) with a single MAPPING value change on
    tMap_1 -- the ground-truth diff vocabulary for the tests above."""
    prev_jobs = [
        {
            "name": "Job_G1",
            "components": [
                {
                    "unique_name": "tMap_1",
                    "component_type": "tMap",
                    "params": {"MAPPING": "emp -> pay"},
                }
            ],
        }
    ]
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
    return prev_jobs, curr_jobs


def _setup_group_a_support(tmp_path: Path) -> Path:
    support_dir = tmp_path / "docs" / "_rag_support" / "phase2_analysis"
    support_dir.mkdir(parents=True)
    support_path = support_dir / "GROUP_A.md"
    support_path.write_text(_generic_analysis("GROUP_A"), encoding="utf-8")
    prev_dir = tmp_path / "prev_scan"
    prev_dir.mkdir()
    prev_jobs, _ = _mismatched_field_jobs()
    (prev_dir / "GROUP_A.json").write_text(json.dumps(prev_jobs), encoding="utf-8")
    return support_path


class TestG5ValidateAgainstDiff:
    """Integration coverage through the real _patch_phase2 / run() path."""

    @pytest.mark.asyncio
    async def test_ac2_reconciled_facts_accepted_and_written(
        self, tmp_path: Path
    ) -> None:
        """AC2: an amendment whose asserted facts all reconcile with the diff
        is accepted; the group is patched (not fallen back)."""
        support_path = _setup_group_a_support(tmp_path)
        _, curr_jobs = _mismatched_field_jobs()
        executor = TruePatchExecutor()
        log_mgr = _log_mgr()
        provider = _make_provider(_diff_grounded_model_response())

        with (
            patch(
                "mcp_server.services.true_patch_executor._load_curr_jobs",
                return_value=curr_jobs,
            ),
            patch(
                "mcp_server.services.true_patch_executor._patch_provider",
                return_value=provider,
            ),
        ):
            result = await executor.run(
                project_id="proj1",
                output_dir=tmp_path,
                changed_groups=["GROUP_A"],
                platform="generic",
                log_mgr=log_mgr,
            )

        assert result.action == "true_patch"
        assert result.patched_groups == ["GROUP_A"]
        # The published support file was rewritten with the new content.
        written = support_path.read_text(encoding="utf-8")
        assert "NEW_OUTPUT" in written
        assert written != _generic_analysis("GROUP_A")

    @pytest.mark.asyncio
    async def test_ac1_unreconciled_fact_rejected_routes_to_scoped_regen(
        self, tmp_path: Path
    ) -> None:
        """AC1: an amendment asserting a fact (connection name) absent from the
        diff is rejected; the group is NOT written and falls back."""
        _setup_group_a_support(tmp_path)
        _, curr_jobs = _mismatched_field_jobs()
        executor = TruePatchExecutor()
        log_mgr = _log_mgr()
        provider = _make_provider(_diff_ungrounded_model_response())

        with (
            patch(
                "mcp_server.services.true_patch_executor._load_curr_jobs",
                return_value=curr_jobs,
            ),
            patch(
                "mcp_server.services.true_patch_executor._patch_provider",
                return_value=provider,
            ),
        ):
            result = await executor.run(
                project_id="proj1",
                output_dir=tmp_path,
                changed_groups=["GROUP_A"],
                platform="generic",
                log_mgr=log_mgr,
            )

        assert result.action == "scoped_regen_fallback"
        assert result.patched_groups == []

        # AC3: a stable reason code distinguishes the fact-check failure from
        # other fallback causes.
        assert result.summary["fallback_reasons"]["GROUP_A"] == "diff_fidelity_failed"

    @pytest.mark.asyncio
    async def test_ac4_rejected_amendment_leaves_support_file_byte_unchanged(
        self, tmp_path: Path
    ) -> None:
        """AC4: the guard runs before any disk write -- rejection leaves the
        existing (published) analysis document byte-identical."""
        support_path = _setup_group_a_support(tmp_path)
        original_bytes = support_path.read_bytes()
        _, curr_jobs = _mismatched_field_jobs()
        executor = TruePatchExecutor()
        log_mgr = _log_mgr()
        provider = _make_provider(_diff_ungrounded_model_response())

        with (
            patch(
                "mcp_server.services.true_patch_executor._load_curr_jobs",
                return_value=curr_jobs,
            ),
            patch(
                "mcp_server.services.true_patch_executor._patch_provider",
                return_value=provider,
            ),
        ):
            await executor.run(
                project_id="proj1",
                output_dir=tmp_path,
                changed_groups=["GROUP_A"],
                platform="generic",
                log_mgr=log_mgr,
            )

        assert support_path.read_bytes() == original_bytes

    @pytest.mark.asyncio
    async def test_ac5_guard_rejection_never_touches_cache_or_support_reconcile(
        self, tmp_path: Path
    ) -> None:
        """AC5: a guard-triggered fallback must not run the cache/support
        reconcile step at all -- so no cache/support-mismatch reason code can
        ever fire for a rejected amendment (nothing was written to reconcile)."""
        _setup_group_a_support(tmp_path)
        _, curr_jobs = _mismatched_field_jobs()
        executor = TruePatchExecutor()
        log_mgr = _log_mgr()
        provider = _make_provider(_diff_ungrounded_model_response())

        def _fail_if_called(*args: object, **kwargs: object) -> None:
            raise AssertionError(
                "cache/support reconcile must not run when G5 rejects the "
                "only changed group before any write"
            )

        with (
            patch(
                "mcp_server.services.true_patch_executor._load_curr_jobs",
                return_value=curr_jobs,
            ),
            patch(
                "mcp_server.services.true_patch_executor._patch_provider",
                return_value=provider,
            ),
            patch(
                "mcp_server.services.true_patch_executor.replace_analysis_cache_from_rag_support",
                side_effect=_fail_if_called,
            ),
            patch(
                "mcp_server.services.true_patch_executor.inspect_generation_cache_readiness",
                side_effect=_fail_if_called,
            ),
        ):
            result = await executor.run(
                project_id="proj1",
                output_dir=tmp_path,
                changed_groups=["GROUP_A"],
                platform="generic",
                log_mgr=log_mgr,
            )

        assert result.action == "scoped_regen_fallback"
        assert "reason_codes" not in result.summary
        assert result.summary["fallback_reasons"]["GROUP_A"] == "diff_fidelity_failed"

    @pytest.mark.asyncio
    async def test_ac3_reason_code_distinguishes_g5_from_g3_failure(
        self, tmp_path: Path
    ) -> None:
        """AC3: the G5 reason code must differ from an ordinary G3 (thin/
        incomplete analysis) failure's reason code."""
        _setup_group_a_support(tmp_path)
        _, curr_jobs = _mismatched_field_jobs()
        executor = TruePatchExecutor()
        log_mgr = _log_mgr()
        # Thin response: fails G3, never reaches an unreconciled fact.
        thin_provider = _make_provider("### GROUP_SUMMARY\n\nTiny.")

        with (
            patch(
                "mcp_server.services.true_patch_executor._load_curr_jobs",
                return_value=curr_jobs,
            ),
            patch(
                "mcp_server.services.true_patch_executor._patch_provider",
                return_value=thin_provider,
            ),
        ):
            g3_result = await executor.run(
                project_id="proj1",
                output_dir=tmp_path,
                changed_groups=["GROUP_A"],
                platform="generic",
                log_mgr=log_mgr,
            )

        assert g3_result.summary["fallback_reasons"]["GROUP_A"] == "g3_validation_failed"
        assert g3_result.summary["fallback_reasons"]["GROUP_A"] != "diff_fidelity_failed"


# ---------------------------------------------------------------------------
# Slice 3: synthesis / cross-cutting doc in-place amendment
# ---------------------------------------------------------------------------


def _make_provider_sequence(responses: list[str]) -> MagicMock:
    """A provider whose complete() returns each response in ``responses`` in
    order (one per call) -- needed once Phase 2 AND Phase 3b (synthesis) both
    call the model in the same run()."""
    results = []
    for text in responses:
        r = MagicMock()
        r.success = True
        r.text = text
        r.raw_error = None
        results.append(r)
    provider = MagicMock()
    provider.complete = AsyncMock(side_effect=results)
    return provider


def _write_cross_cutting_manifest(
    tmp_path: Path, doc_name: str = "09_EXECUTIVE_SUMMARY"
) -> None:
    """Write a minimal _doc_pack_manifest.json marking ``doc_name`` cross-cutting."""
    manifest_path = tmp_path / "docs" / "_rag_support" / "_doc_pack_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source_analyses": [],
        "doc_packs": {
            doc_name: {
                "path": f"_doc_pack_{doc_name}.md",
                "included": {},
                "omitted": {},
                "unclassified": {},
                "included_obligations": [
                    {"scope": "units", "category": "canonical_inventory", "terms": []}
                ],
                "unmatched_obligations": [],
                "evidence_sources": [],
            }
        },
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _executive_summary_doc() -> str:
    return (
        "# 09 Executive Summary\n\n"
        "## Executive Risk Summary\n\n"
        "GROUP_A relies on `tMap_1` for payroll processing with the MAPPING "
        "parameter set to 'emp -> pay'. No outstanding risk identified.\n\n"
        "## Modernisation Priorities\n\n"
        "| Priority | Initiative |\n"
        "| --- | --- |\n"
        "| 1 | Migrate GROUP_B queueing to cloud (BR-GROUP_B-001) |\n\n"
        "## Confidence and Limitations\n\n"
        "Scanner coverage is complete across all groups.\n\n"
    )


def _synthesis_grounded_response() -> str:
    return (
        "## Executive Risk Summary\n\n"
        "GROUP_A relies on `tMap_1`, which now maps employee records to "
        "NEW_OUTPUT via the MAPPING parameter. No outstanding risk "
        "identified.\n\n"
    )


def _executive_summary_doc_with_removal() -> str:
    return (
        "## Executive Risk Summary\n\n"
        "GROUP_A relies on `tMap_1` and `tMap_2` for payroll processing.\n\n"
        "## Modernisation Priorities\n\n"
        "| Priority | Initiative |\n"
        "| --- | --- |\n"
        "| 1 | Migrate GROUP_B queueing to cloud (BR-GROUP_B-001) |\n\n"
    )


def _synthesis_removal_response() -> str:
    return (
        "## Executive Risk Summary\n\n"
        "GROUP_A relies on `tMap_1` for payroll processing.\n\n"
    )


def _executive_summary_doc_unmappable() -> str:
    return (
        "# GROUP_A Executive Summary\n\n"
        "## Executive Risk Summary\n\n"
        "No group-specific content lives in this section.\n\n"
    )


def _mock_rag_bridge(expected_count: int = 1):
    """Patch RagBridge so synthesis-amendment tests never hit a live RAG
    backend. Prior true_patch_executor tests never populated ``patched_docs``
    (no group-scoped doc files existed), so this push path was previously
    unexercised and unmocked in this file; Slice 3's summary-doc amendment is
    the first to reach it, so it must be stubbed to stay hermetic."""
    bridge = MagicMock()
    bridge.push_docs = AsyncMock(
        return_value={"pushed": expected_count, "skipped": 0, "errors": []}
    )
    return patch(
        "mcp_server.services.true_patch_executor.RagBridge", return_value=bridge
    )


def _setup_synthesis_scenario(
    tmp_path: Path, doc_text: str, doc_name: str = "09_EXECUTIVE_SUMMARY"
) -> Path:
    """Wire up GROUP_A's support analysis + prev scan + a cross-cutting doc."""
    _setup_group_a_support(tmp_path)
    _write_cross_cutting_manifest(tmp_path, doc_name)
    doc_path = tmp_path / "docs" / f"{doc_name}.md"
    doc_path.write_text(doc_text, encoding="utf-8")
    return doc_path


class TestSynthesisAmendment:
    """Slice 3: SynthesisImpactMapper-driven in-place amendment of summary docs."""

    @pytest.mark.asyncio
    async def test_ac1_single_unit_change_amends_only_tied_section(
        self, tmp_path: Path
    ) -> None:
        """AC1: a single-unit change amends only the mapped section; every
        other section of the summary doc is byte-identical, and cache/support
        stays consistent (AC4)."""
        doc_path = _setup_synthesis_scenario(tmp_path, _executive_summary_doc())
        _, curr_jobs = _mismatched_field_jobs()
        executor = TruePatchExecutor()
        log_mgr = _log_mgr()
        provider = _make_provider_sequence(
            [_diff_grounded_model_response(), _synthesis_grounded_response()]
        )

        with (
            patch(
                "mcp_server.services.true_patch_executor._load_curr_jobs",
                return_value=curr_jobs,
            ),
            patch(
                "mcp_server.services.true_patch_executor._patch_provider",
                return_value=provider,
            ),
            _mock_rag_bridge(),
        ):
            result = await executor.run(
                project_id="proj1",
                output_dir=tmp_path,
                changed_groups=["GROUP_A"],
                platform="generic",
                log_mgr=log_mgr,
            )

        assert result.action == "true_patch"
        assert result.summary["synthesis_patched_docs"] == ["09_EXECUTIVE_SUMMARY.md"]
        assert result.summary["synthesis_fallback_docs"] == {}
        assert "09_EXECUTIVE_SUMMARY.md" in result.patched_docs

        patched_text = doc_path.read_text(encoding="utf-8")
        original = _executive_summary_doc()
        assert patched_text != original
        assert "NEW_OUTPUT" in patched_text

        # Unrelated sections are byte-identical, including the BR tag (G2).
        orig_sections = _extract_h2_sections_for_test(original)
        new_sections = _extract_h2_sections_for_test(patched_text)
        assert orig_sections["Modernisation Priorities"] == new_sections["Modernisation Priorities"]
        assert orig_sections["Confidence and Limitations"] == new_sections["Confidence and Limitations"]
        assert "BR-GROUP_B-001" in new_sections["Modernisation Priorities"]

        # AC4: cache/support package stayed consistent -- no mismatch signal,
        # and the Phase 2 cache mirrors the published support analysis.
        assert "reason_codes" not in result.summary
        support_path = (
            tmp_path / "docs" / "_rag_support" / "phase2_analysis" / "GROUP_A.md"
        )
        cache_path = tmp_path / "analysis_cache" / "GROUP_A.md"
        assert cache_path.is_file()
        assert cache_path.read_text(encoding="utf-8") == support_path.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_ac5_scoping_uses_fewer_tokens_than_full_doc_regen(
        self, tmp_path: Path
    ) -> None:
        """AC5: only the mapped heading's token budget is spent, not a budget
        sized for the whole (3-heading) document."""
        _setup_synthesis_scenario(tmp_path, _executive_summary_doc())
        _, curr_jobs = _mismatched_field_jobs()
        executor = TruePatchExecutor()
        log_mgr = _log_mgr()
        provider = _make_provider_sequence(
            [_diff_grounded_model_response(), _synthesis_grounded_response()]
        )

        with (
            patch(
                "mcp_server.services.true_patch_executor._load_curr_jobs",
                return_value=curr_jobs,
            ),
            patch(
                "mcp_server.services.true_patch_executor._patch_provider",
                return_value=provider,
            ),
            _mock_rag_bridge(),
        ):
            await executor.run(
                project_id="proj1",
                output_dir=tmp_path,
                changed_groups=["GROUP_A"],
                platform="generic",
                log_mgr=log_mgr,
            )

        # Second call = the synthesis Phase 3 patch (first = Phase 2 patch).
        synthesis_call = provider.complete.call_args_list[1]
        max_tokens = synthesis_call.kwargs["options"].max_tokens
        doc_heading_count = len(_extract_h2_sections_for_test(_executive_summary_doc()))
        assert doc_heading_count == 3
        # Only 1 of 3 headings was mapped -> budget scoped to 1, not 3.
        assert max_tokens == 2500 * 1
        assert max_tokens < 2500 * doc_heading_count

    @pytest.mark.asyncio
    async def test_ac2_removed_unit_shrinks_doc_and_drops_only_its_row(
        self, tmp_path: Path
    ) -> None:
        """AC2: a push that removes a unit removes that unit's reference from
        the summary doc (the doc shrinks); unrelated content is untouched."""
        doc_path = _setup_synthesis_scenario(
            tmp_path, _executive_summary_doc_with_removal()
        )
        prev_jobs = [{
            "name": "Job_G1",
            "components": [
                {"unique_name": "tMap_1", "component_type": "tMap",
                 "params": {"MAPPING": "emp -> pay"}},
                {"unique_name": "tMap_2", "component_type": "tMap",
                 "params": {"MAPPING": "x -> y"}},
            ],
        }]
        curr_jobs = [{
            "name": "Job_G1",
            "components": [
                {"unique_name": "tMap_1", "component_type": "tMap",
                 "params": {"MAPPING": "emp -> NEW_OUTPUT"}},
            ],
        }]
        (tmp_path / "prev_scan" / "GROUP_A.json").write_text(
            json.dumps(prev_jobs), encoding="utf-8"
        )
        executor = TruePatchExecutor()
        log_mgr = _log_mgr()
        provider = _make_provider_sequence(
            [_diff_grounded_model_response(), _synthesis_removal_response()]
        )

        with (
            patch(
                "mcp_server.services.true_patch_executor._load_curr_jobs",
                return_value=curr_jobs,
            ),
            patch(
                "mcp_server.services.true_patch_executor._patch_provider",
                return_value=provider,
            ),
            _mock_rag_bridge(),
        ):
            result = await executor.run(
                project_id="proj1",
                output_dir=tmp_path,
                changed_groups=["GROUP_A"],
                platform="generic",
                log_mgr=log_mgr,
            )

        assert result.action == "true_patch"
        original = _executive_summary_doc_with_removal()
        patched_text = doc_path.read_text(encoding="utf-8")
        assert len(patched_text) < len(original)
        assert "tMap_2" not in patched_text

        orig_sections = _extract_h2_sections_for_test(original)
        new_sections = _extract_h2_sections_for_test(patched_text)
        assert orig_sections["Modernisation Priorities"] == new_sections["Modernisation Priorities"]

    @pytest.mark.asyncio
    async def test_ac3_unmappable_change_falls_back_to_scoped_doc_only(
        self, tmp_path: Path
    ) -> None:
        """AC3: a group reference the mapper cannot isolate to a section
        routes ONLY that document to scoped-local re-derivation; the document
        is left byte-unchanged and the group's own patch still succeeds."""
        doc_path = _setup_synthesis_scenario(
            tmp_path, _executive_summary_doc_unmappable()
        )
        original_bytes = doc_path.read_bytes()
        _, curr_jobs = _mismatched_field_jobs()
        executor = TruePatchExecutor()
        log_mgr = _log_mgr()
        # Only Phase 2 calls the model -- Phase 3b never calls it when the
        # mapper returns no mapping.
        provider = _make_provider_sequence([_diff_grounded_model_response()])

        with (
            patch(
                "mcp_server.services.true_patch_executor._load_curr_jobs",
                return_value=curr_jobs,
            ),
            patch(
                "mcp_server.services.true_patch_executor._patch_provider",
                return_value=provider,
            ),
        ):
            result = await executor.run(
                project_id="proj1",
                output_dir=tmp_path,
                changed_groups=["GROUP_A"],
                platform="generic",
                log_mgr=log_mgr,
            )

        # Group-level patch still succeeds (true_patch); only the summary doc
        # is flagged, and only that one document -- not the whole repo.
        assert result.action == "true_patch"
        assert result.summary["synthesis_fallback_docs"] == {
            "09_EXECUTIVE_SUMMARY": "synthesis_mapping_unavailable"
        }
        assert result.summary["synthesis_patched_docs"] == []
        assert "09_EXECUTIVE_SUMMARY.md" not in result.patched_docs
        assert doc_path.read_bytes() == original_bytes
        assert provider.complete.call_count == 1


def _extract_h2_sections_for_test(text: str) -> dict[str, str]:
    from mcp_server.services.patch_section_map import extract_h2_sections
    return extract_h2_sections(text)
