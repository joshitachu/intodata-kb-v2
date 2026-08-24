"""Unit tests for `lineage_service.build_job_lineage` (job/group lineage rollup).

Synthetic entries (raw dicts, same shape as `scan/lineage.json`) plus a
synthetic `manifest.json` exercise the selection and aggregation rules
directly. The real werkorder lineage file is 2.6 MB and its manifest 1.4 MB --
neither belongs in a unit test; the fixtures here reproduce the two properties
that actually break callers:

* the two naming spaces (short design names and long hash-suffixed composite
  ids) living in one file, so job matching must be exact,
* group paths stored as long source paths, so group resolution has to work off
  a segment the operator would actually type.

Route-level coverage (RBAC, 422s, the two distinguishable 404s) lives in
`tests/test_api/test_lineage_job.py`.
"""

from __future__ import annotations

import json

import pytest

from mcp_server.schemas import LineageErrorDetail
from mcp_server.services.lineage_service import (
    EXPRESSION_MAX_CHARS,
    LineageDataUnavailableError,
    LineageQueryError,
    LineageSelectionEmptyError,
    build_job_lineage,
)

PROJECT_ID = "rollupproj"

# Two naming spaces, as in a real composite scan.
JOB_SHORT = "RHM2Smartflow_Employee"
JOB_SHORT_2 = "RHM2Smartflow_Workorders"
JOB_COMPOSITE = "VolkerVessels_Workorder_Talend_exports_VQ_RHM2SF___intod-31f9ad59e878"
JOB_OTHER = "Smartflow2RHM_Realization"

GROUP_PATH = "VolkerVessels_Workorder/Talend exports/VQ_RHM2SF.__intodoc_zip__/VQ_RHM2SF/RHM2Smartflow"
GROUP_PATH_OTHER = (
    "VolkerVessels_Workorder/Talend exports/VQ_RHM2SF.__intodoc_zip__/VQ_RHM2SF/Smartflow2RHM"
)
GROUP_PATH_ESB = (
    "VolkerVessels_Workorder/Talend exports/"
    "EnterpriseServiceBus.__intodoc_zip__/ENTERPRISESERVICEBUS/esb_main"
)
JOB_ESB = "Esb_Dispatch"


def _entry(
    *,
    job_name: str,
    target_table: str,
    target_column: str,
    source_table: str = "",
    source_column: str = "",
    source_component: str = "",
    target_component: str = "tMap_1",
    confidence: str = "certain",
    resolved: bool = True,
    dynamic_refs: list[str] | None = None,
    transformations: list[dict] | None = None,
) -> dict:
    if transformations is None:
        transformations = [
            {
                "transformation_type": "direct_map",
                "expression": f"{source_table}.{source_column}" if source_table else "",
                "human_explanation": "",
            }
        ]
    return {
        "source_system": "",
        "source_table": source_table,
        "source_column": source_column,
        "source_component": source_component,
        "source_job_name": "",
        "target_table": target_table,
        "target_column": target_column,
        "target_component": target_component,
        "job_name": job_name,
        "confidence": confidence,
        "resolved": resolved,
        "dynamic_refs": dynamic_refs or [],
        "transformations": transformations,
    }


def _write_lineage(output_dir, entries: list[dict], project_id: str = PROJECT_ID) -> None:
    scan_dir = output_dir / project_id / "scan"
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / "lineage.json").write_text(
        json.dumps({"entries": entries, "unmapped_columns": [], "summary": {}}),
        encoding="utf-8",
    )


def _write_manifest(output_dir, job_groups: dict[str, str], project_id: str = PROJECT_ID) -> None:
    """Write a manifest whose `job_groups` is the real flat {job: group_path} shape."""
    scan_dir = output_dir / project_id / "scan"
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / "manifest.json").write_text(
        json.dumps({"job_groups": job_groups, "job_calls": {}}), encoding="utf-8"
    )


@pytest.fixture
def project(tmp_path):
    """Three jobs across two groups, spanning both naming spaces."""
    entries = [
        _entry(
            job_name=JOB_SHORT,
            source_table="row1",
            source_column="row1/EmployeeNr",
            source_component="tDBInput_1",
            target_table="out1",
            target_column="out1/EmployeeNr",
        ),
        _entry(
            job_name=JOB_SHORT_2,
            source_table="row1",
            source_column="row1/WorkorderNr",
            source_component="tDBInput_2",
            target_table="out1",
            target_column="out1/WorkorderNr",
        ),
        _entry(
            job_name=JOB_COMPOSITE,
            source_table="row9",
            source_column="row9/Ref",
            source_component="tDBInput_9",
            target_table="LogInfo",
            target_column="LogInfo/Ref",
        ),
        _entry(
            job_name=JOB_OTHER,
            source_table="row3",
            source_column="row3/Hours",
            source_component="tDBInput_3",
            target_table="Realization",
            target_column="Realization/Hours",
        ),
    ]
    _write_lineage(tmp_path, entries)
    _write_manifest(
        tmp_path,
        {
            JOB_SHORT: GROUP_PATH,
            JOB_SHORT_2: GROUP_PATH,
            JOB_COMPOSITE: GROUP_PATH,
            JOB_OTHER: GROUP_PATH_OTHER,
            JOB_ESB: GROUP_PATH_ESB,
        },
    )
    return tmp_path


class TestGroupRollup:
    def test_group_leaf_segment_resolves_every_job_in_the_group(self, project):
        result = build_job_lineage(PROJECT_ID, project, group="RHM2Smartflow")
        assert result.jobs == sorted([JOB_SHORT, JOB_SHORT_2, JOB_COMPOSITE])
        assert result.total_edges == 3

    def test_group_matching_is_case_insensitive(self, project):
        result = build_job_lineage(PROJECT_ID, project, group="rhm2smartflow")
        assert result.jobs == sorted([JOB_SHORT, JOB_SHORT_2, JOB_COMPOSITE])

    def test_full_group_path_resolves(self, project):
        result = build_job_lineage(PROJECT_ID, project, group=GROUP_PATH)
        assert result.jobs == sorted([JOB_SHORT, JOB_SHORT_2, JOB_COMPOSITE])

    def test_mid_path_segment_resolves_and_unions_sibling_groups(self, project):
        """`VQ_RHM2SF` is a zip name and a directory -- it spans both groups."""
        result = build_job_lineage(PROJECT_ID, project, group="VQ_RHM2SF")
        assert result.jobs == sorted([JOB_SHORT, JOB_SHORT_2, JOB_COMPOSITE, JOB_OTHER])

    def test_leaf_tier_wins_over_mid_path_tier(self, project):
        """A leaf hit must not be diluted by groups where the name is mid-path.

        `RHM2Smartflow` is the leaf of one group only; if tiers were unioned
        instead of ordered, nothing would change here -- so assert the negative
        case that proves ordering: `Smartflow2RHM`'s job must stay out.
        """
        result = build_job_lineage(PROJECT_ID, project, group="RHM2Smartflow")
        assert JOB_OTHER not in result.jobs

    def test_group_whose_jobs_have_no_lineage_entries_raises_selection_empty(self, project):
        """`esb_main` resolves via the manifest, but `Esb_Dispatch` has no entries."""
        with pytest.raises(LineageSelectionEmptyError) as exc:
            build_job_lineage(PROJECT_ID, project, group="esb_main")
        assert "esb_main" in str(exc.value)

    def test_unknown_group_raises_selection_empty(self, project):
        with pytest.raises(LineageSelectionEmptyError):
            build_job_lineage(PROJECT_ID, project, group="NoSuchGroup")

    def test_missing_manifest_degrades_to_selection_empty_not_error(self, tmp_path):
        _write_lineage(tmp_path, [_entry(job_name=JOB_SHORT, target_table="t", target_column="c")])
        with pytest.raises(LineageSelectionEmptyError):
            build_job_lineage(PROJECT_ID, tmp_path, group="RHM2Smartflow")


class TestJobMatching:
    def test_exact_job_match(self, project):
        result = build_job_lineage(PROJECT_ID, project, jobs=[JOB_SHORT])
        assert result.jobs == [JOB_SHORT]
        assert result.total_edges == 1

    def test_case_insensitive_exact_fallback(self, project):
        result = build_job_lineage(PROJECT_ID, project, jobs=[JOB_SHORT.upper()])
        assert result.jobs == [JOB_SHORT]

    def test_long_composite_job_name_matches_exactly(self, project):
        result = build_job_lineage(PROJECT_ID, project, jobs=[JOB_COMPOSITE])
        assert result.jobs == [JOB_COMPOSITE]

    def test_unknown_job_excluded_from_jobs_list(self, project):
        result = build_job_lineage(PROJECT_ID, project, jobs=[JOB_SHORT, "NoSuchJob"])
        assert result.jobs == [JOB_SHORT]

    def test_substring_of_a_real_job_never_matches(self, project):
        """Prefix/substring matching would attribute one integration to another."""
        with pytest.raises(LineageSelectionEmptyError):
            build_job_lineage(PROJECT_ID, project, jobs=["RHM2Smartflow"])

    def test_case_colliding_jobs_both_resolve(self, tmp_path):
        """Two real jobs differing only in case: dropping either would hide one."""
        _write_lineage(
            tmp_path,
            [
                _entry(job_name="Vise2AssetCheck", target_table="a", target_column="a1"),
                _entry(job_name="VISE2AssetCheck", target_table="b", target_column="b1"),
            ],
        )
        result = build_job_lineage(PROJECT_ID, tmp_path, jobs=["vise2assetcheck"])
        assert result.jobs == ["VISE2AssetCheck", "Vise2AssetCheck"]

    def test_job_and_group_are_unioned(self, project):
        result = build_job_lineage(PROJECT_ID, project, jobs=[JOB_OTHER], group="RHM2Smartflow")
        assert result.jobs == sorted([JOB_SHORT, JOB_SHORT_2, JOB_COMPOSITE, JOB_OTHER])


class TestOrderingAndPaging:
    @pytest.fixture
    def mixed(self, tmp_path):
        """Unresolved rows written FIRST, so file order alone would fail the test."""
        entries = [
            _entry(
                job_name=JOB_SHORT,
                target_table="out1",
                target_column=f"out1/dyn{i}",
                resolved=False,
                confidence="dynamic",
                dynamic_refs=["context.Setting"],
                transformations=[
                    {
                        "transformation_type": "dynamic",
                        "expression": 'globalMap.get("x")',
                        "human_explanation": "",
                    }
                ],
            )
            for i in range(6)
        ] + [
            _entry(
                job_name=JOB_SHORT,
                source_table="row1",
                source_column=f"row1/c{i}",
                source_component="tDBInput_1",
                target_table="out1",
                target_column=f"out1/c{i}",
            )
            for i in range(6)
        ]
        _write_lineage(tmp_path, entries)
        return tmp_path

    def test_resolved_edges_come_first(self, mixed):
        result = build_job_lineage(PROJECT_ID, mixed, jobs=[JOB_SHORT], limit=6)
        assert result.total_edges == 12
        assert all(edge.resolved for edge in result.edges)
        assert result.has_more is True

    def test_second_page_holds_the_unresolved_tail(self, mixed):
        result = build_job_lineage(PROJECT_ID, mixed, jobs=[JOB_SHORT], offset=6, limit=6)
        assert result.returned == 6
        assert not any(edge.resolved for edge in result.edges)
        assert result.has_more is False

    def test_paging_is_stable_across_calls_with_different_offsets(self, mixed):
        whole = build_job_lineage(PROJECT_ID, mixed, jobs=[JOB_SHORT], limit=200)
        first = build_job_lineage(PROJECT_ID, mixed, jobs=[JOB_SHORT], offset=0, limit=5)
        second = build_job_lineage(PROJECT_ID, mixed, jobs=[JOB_SHORT], offset=5, limit=5)
        third = build_job_lineage(PROJECT_ID, mixed, jobs=[JOB_SHORT], offset=10, limit=5)

        stitched = [e.model_dump() for e in first.edges + second.edges + third.edges]
        assert stitched == [e.model_dump() for e in whole.edges]
        # No row appears twice and none is skipped: 12 distinct target columns.
        assert len({row["target_column"] for row in stitched}) == 12

    def test_offset_past_the_end_returns_an_empty_page_not_an_error(self, mixed):
        result = build_job_lineage(PROJECT_ID, mixed, jobs=[JOB_SHORT], offset=500, limit=50)
        assert result.returned == 0
        assert result.edges == []
        assert result.total_edges == 12
        assert result.has_more is False


class TestTotals:
    def test_confidence_totals_count_every_bucket(self, tmp_path):
        entries = [
            _entry(job_name=JOB_SHORT, target_table="t", target_column="c1", confidence="certain"),
            _entry(job_name=JOB_SHORT, target_table="t", target_column="c2", confidence="likely"),
            _entry(
                job_name=JOB_SHORT, target_table="t", target_column="c3",
                confidence="dynamic", resolved=False,
            ),
            _entry(job_name=JOB_SHORT, target_table="t", target_column="c4", confidence="inferred"),
            _entry(
                job_name=JOB_SHORT, target_table="t", target_column="c5",
                confidence="opaque", resolved=False,
            ),
        ]
        _write_lineage(tmp_path, entries)
        result = build_job_lineage(PROJECT_ID, tmp_path, jobs=[JOB_SHORT])

        assert result.totals.entries == 5
        assert result.totals.resolved == 3
        assert result.totals.unresolved == 2
        assert result.totals.confidence.model_dump() == {
            "certain": 1, "likely": 1, "dynamic": 1, "inferred": 1, "opaque": 1
        }

    def test_unrecognised_confidence_is_bucketed_under_opaque(self, tmp_path):
        _write_lineage(
            tmp_path,
            [_entry(job_name=JOB_SHORT, target_table="t", target_column="c", confidence="brand_new")],
        )
        result = build_job_lineage(PROJECT_ID, tmp_path, jobs=[JOB_SHORT])
        assert result.totals.confidence.opaque == 1
        # The raw value is never lost on the edge itself.
        assert result.edges[0].confidence == "brand_new"

    def test_totals_count_entries_while_edges_flatten_transformations(self, tmp_path):
        _write_lineage(
            tmp_path,
            [
                _entry(
                    job_name=JOB_SHORT,
                    source_table="row1",
                    source_column="row1/c",
                    target_table="out1",
                    target_column="out1/c",
                    transformations=[
                        {"transformation_type": "expression", "expression": "Var.c",
                         "human_explanation": ""},
                        {"transformation_type": "direct_map", "expression": "row1.c",
                         "human_explanation": ""},
                    ],
                )
            ],
        )
        result = build_job_lineage(PROJECT_ID, tmp_path, jobs=[JOB_SHORT])
        assert result.totals.entries == 1
        assert result.total_edges == 2
        assert {e.transformation_type for e in result.edges} == {"expression", "direct_map"}

    def test_entry_without_transformations_still_yields_one_row(self, tmp_path):
        _write_lineage(
            tmp_path,
            [
                _entry(
                    job_name=JOB_SHORT, source_table="row1", source_column="row1/c",
                    target_table="out1", target_column="out1/c", transformations=[],
                )
            ],
        )
        result = build_job_lineage(PROJECT_ID, tmp_path, jobs=[JOB_SHORT])
        assert result.total_edges == 1
        assert result.edges[0].transformation_type == ""
        assert result.edges[0].expression == ""

    def test_identical_entries_are_deduplicated(self, tmp_path):
        duplicate = _entry(
            job_name=JOB_SHORT, source_table="row1", source_column="row1/c",
            target_table="out1", target_column="out1/c",
        )
        _write_lineage(tmp_path, [duplicate, dict(duplicate)])
        result = build_job_lineage(PROJECT_ID, tmp_path, jobs=[JOB_SHORT])
        assert result.totals.entries == 2  # verbatim entry count, auditable vs the scan
        assert result.total_edges == 1  # but one distinct edge

    def test_target_tables_are_distinct_sorted_and_capped(self, tmp_path):
        _write_lineage(
            tmp_path,
            [
                _entry(job_name=JOB_SHORT, target_table=f"T{i:03d}", target_column="c")
                for i in range(60)
            ],
        )
        result = build_job_lineage(PROJECT_ID, tmp_path, jobs=[JOB_SHORT])
        assert len(result.target_tables) == 40
        assert result.target_tables == sorted(result.target_tables)
        assert result.target_tables[0] == "T000"
        # Capping the summary must not cap the data.
        assert result.total_edges == 60


class TestCredentialSafetyAndClipping:
    def test_expression_is_clipped_to_the_ceiling(self, tmp_path):
        long_expression = "row1.a + " * 400
        assert len(long_expression) > EXPRESSION_MAX_CHARS
        _write_lineage(
            tmp_path,
            [
                _entry(
                    job_name=JOB_SHORT, target_table="out1", target_column="out1/c",
                    transformations=[
                        {"transformation_type": "expression", "expression": long_expression,
                         "human_explanation": ""}
                    ],
                )
            ],
        )
        result = build_job_lineage(PROJECT_ID, tmp_path, jobs=[JOB_SHORT])
        expression = result.edges[0].expression
        assert len(expression) == EXPRESSION_MAX_CHARS
        assert expression.endswith("...")

    def test_short_expression_is_not_clipped_or_marked(self, tmp_path):
        _write_lineage(
            tmp_path,
            [
                _entry(
                    job_name=JOB_SHORT, source_table="row1", source_column="row1/c",
                    target_table="out1", target_column="out1/c",
                )
            ],
        )
        result = build_job_lineage(PROJECT_ID, tmp_path, jobs=[JOB_SHORT])
        assert result.edges[0].expression == "row1.row1/c"

    def test_inline_credentials_are_redacted(self, tmp_path):
        leaky = '"ftp://host/path?password=RAW(sup3rs3cr3t)&fileName=" + context.file'
        _write_lineage(
            tmp_path,
            [
                _entry(
                    job_name=JOB_SHORT, target_table="out1", target_column="out1/uri",
                    transformations=[
                        {"transformation_type": "expression", "expression": leaky,
                         "human_explanation": ""}
                    ],
                )
            ],
        )
        result = build_job_lineage(PROJECT_ID, tmp_path, jobs=[JOB_SHORT])
        expression = result.edges[0].expression
        assert "sup3rs3cr3t" not in expression
        assert "REDACTED-CREDENTIAL" in expression
        # Structure survives: the doc can still say a password is configured.
        assert "password=" in expression

    def test_redaction_runs_before_clipping_so_a_long_expression_cannot_leak(self, tmp_path):
        """A secret sitting past the clip boundary must be masked, not truncated.

        Clip-then-redact would cut `password=RAW(...` mid-literal, the regex
        would stop matching, and the surviving prefix of the secret would ship.
        """
        padding = "x" * (EXPRESSION_MAX_CHARS - 30)
        leaky = f'"{padding}?password=RAW(sup3rs3cr3t)&more=1"'
        _write_lineage(
            tmp_path,
            [
                _entry(
                    job_name=JOB_SHORT, target_table="out1", target_column="out1/uri",
                    transformations=[
                        {"transformation_type": "expression", "expression": leaky,
                         "human_explanation": ""}
                    ],
                )
            ],
        )
        result = build_job_lineage(PROJECT_ID, tmp_path, jobs=[JOB_SHORT])
        expression = result.edges[0].expression
        assert len(expression) <= EXPRESSION_MAX_CHARS
        for prefix_len in range(4, len("sup3rs3cr3t") + 1):
            assert "sup3rs3cr3t"[:prefix_len] not in expression

    def test_human_explanation_is_redacted_too(self, tmp_path):
        _write_lineage(
            tmp_path,
            [
                _entry(
                    job_name=JOB_SHORT, target_table="out1", target_column="out1/uri",
                    transformations=[
                        {
                            "transformation_type": "expression",
                            "expression": "",
                            "human_explanation": "connects with ?apikey=RAW(abc123secret)",
                        }
                    ],
                )
            ],
        )
        result = build_job_lineage(PROJECT_ID, tmp_path, jobs=[JOB_SHORT])
        assert "abc123secret" not in result.edges[0].human_explanation
        assert "REDACTED-CREDENTIAL" in result.edges[0].human_explanation


class TestMissingLineageData:
    def test_missing_lineage_json_raises_data_unavailable(self, tmp_path):
        with pytest.raises(LineageDataUnavailableError) as exc:
            build_job_lineage(PROJECT_ID, tmp_path, jobs=[JOB_SHORT])
        assert "No lineage data for project" in str(exc.value)

    def test_unmatched_job_raises_a_different_error_with_a_different_message(self, project):
        with pytest.raises(LineageSelectionEmptyError) as exc:
            build_job_lineage(PROJECT_ID, project, jobs=["NoSuchJob"])
        message = str(exc.value)
        assert "No lineage entries for" in message
        assert "No lineage data for project" not in message
        assert "NoSuchJob" in message

    def test_selection_empty_is_not_a_subclass_of_data_unavailable(self):
        """The route maps both to 404; callers must still be able to tell them apart.

        Siblings under `LineageQueryError`, never a subclass pair -- `except
        LineageDataUnavailableError` must not swallow a selection miss.
        """
        assert not issubclass(LineageSelectionEmptyError, LineageDataUnavailableError)
        assert not issubclass(LineageDataUnavailableError, LineageSelectionEmptyError)
        assert issubclass(LineageDataUnavailableError, LineageQueryError)
        assert issubclass(LineageSelectionEmptyError, LineageQueryError)


class TestReasonCodes:
    """The machine-readable 404 discriminator.

    The RAG-side consumer turns these into opposite user-facing outcomes
    ("rescan the project" vs "that name is not in the scan"). Pinning the exact
    literals here is the point: renaming one is a breaking API change and has to
    fail loudly, not silently flip a downstream behaviour.
    """

    def test_data_unavailable_reason(self):
        assert LineageDataUnavailableError.reason == "lineage_artifact_absent"

    def test_selection_empty_reason(self):
        assert LineageSelectionEmptyError.reason == "lineage_selection_empty"

    def test_the_two_reasons_are_distinct(self):
        assert LineageDataUnavailableError.reason != LineageSelectionEmptyError.reason

    def test_reason_travels_on_the_raised_instance(self, project):
        """The route reads `exc.reason`, so it must be live on the instance."""
        with pytest.raises(LineageSelectionEmptyError) as exc:
            build_job_lineage(PROJECT_ID, project, jobs=["NoSuchJob"])
        assert exc.value.reason == "lineage_selection_empty"

        with pytest.raises(LineageDataUnavailableError) as exc2:
            build_job_lineage("nosuchproject", project, jobs=[JOB_SHORT])
        assert exc2.value.reason == "lineage_artifact_absent"

    def test_every_query_error_subclass_reason_is_declared_in_the_schema(self):
        """A new failure mode cannot ship without a declared discriminator.

        `LineageErrorDetail.reason` is a closed Literal that the consumer
        branches on. If a subclass is added here without extending that Literal,
        the consumer sees an unknown code and has no defined behaviour -- so
        catch it at test time.
        """
        declared = set(
            LineageErrorDetail.model_fields["reason"].annotation.__args__
        )

        def _subclasses(cls: type) -> set[type]:
            found: set[type] = set()
            for sub in cls.__subclasses__():
                found.add(sub)
                found |= _subclasses(sub)
            return found

        subclasses = _subclasses(LineageQueryError)
        assert subclasses, "expected at least the two known lineage error types"
        for subclass in subclasses:
            assert subclass.reason in declared, (
                f"{subclass.__name__}.reason={subclass.reason!r} is not declared in "
                f"LineageErrorDetail.reason ({sorted(declared)})"
            )

    def test_schema_validates_a_real_error_body(self):
        detail = LineageErrorDetail(
            reason=LineageSelectionEmptyError.reason, message="No lineage entries for ..."
        )
        assert detail.model_dump() == {
            "reason": "lineage_selection_empty",
            "message": "No lineage entries for ...",
        }
