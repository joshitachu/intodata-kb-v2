"""The post-Phase-2 attribution guard.

Batching several groups into one Phase 2 worker re-introduces a verified
failure: an agent holding several groups' evidence attributes a component that
is real for group A to group B. In the custom lane that produced
``tMSSqlOutput_1`` -- real only for ``AssetCheck_GetLocation`` -- attributed to
a sibling that actually uses ``tDBOutput_1``. Checking against the COMBINED
evidence of the batch lets it pass, because the name is real somewhere in the
batch; every test below is about the per-group check that does not.

The calibration test at the bottom runs against ETTM run 259's real analyses,
which were written one-agent-per-group -- the UNBATCHED baseline. A guard that
is noisy there is useless as a batching safety net, so its expected finding
count is zero, pinned.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mcp_server.services.group_attribution_guard import (
    ASSESSMENT_DIRNAME,
    MODE_DECLARED,
    MODE_INFERRED,
    ORIGIN_DECLARED,
    ORIGIN_INFERRED,
    REPORT_FILENAME,
    analysis_group_for_slice,
    analysis_group_names,
    attribution_warnings,
    check_group_attribution,
    evidence_files_for_group,
    published_documents,
    read_provenance_entries,
    write_group_attribution_report,
)

_OUTPUT = Path(__file__).resolve().parents[3] / "output"

#: Run 259's staging. NOT on disk any more: run 260 reused the same project
#: directory, and a successful run's Phase 4 removes ``_analysis/`` and
#: ``_preprocess/`` and swaps ``docs_staging/`` to ``docs/``. The property this
#: test pinned survives only as the report that run wrote --
#: ``output/ettm_fix/docs/_assessment/group_attribution.json``, recording
#: ``analyses_checked: 15, finding_count: 0``. ``RUN_258_STAGING`` below is the
#: live replacement: same estate, same lane, and it still has its evidence.
RUN_259_STAGING = _OUTPUT / "ettm_fix" / "docs_staging"

#: Run 258's real staging: 20 group analyses, nine published documents, and the
#: evidence both were written from. The run whose documents named components
#: that do not exist, so it is the fixture with something to find.
RUN_258_STAGING = _OUTPUT / "ettm_TEST_Claude" / "docs_staging"

#: Real provenance sidecars, written by run 260 under the schema this slice
#: extends. Used to measure how far the declared lane reaches on output that
#: was produced before the ``components`` field existed.
RUN_260_PROVENANCE = _OUTPUT / "ettm_fix" / "docs" / "_provenance"

#: Two clean real stagings on other estates, as a noise check for the document
#: lane: 20 and 24 groups, nine documents each, zero findings expected.
CLEAN_STAGINGS = (
    _OUTPUT / "werkorder" / "ETTM" / "docs_staging",
    _OUTPUT / "werkorder" / "EnterpriseServiceBus" / "docs_staging",
    _OUTPUT / "TEST_V2" / "part1" / "docs_staging",
)


def _staging(tmp_path: Path, evidence: dict[str, str], analyses: dict[str, str]) -> Path:
    staging = tmp_path / "docs_staging"
    (staging / "_preprocess").mkdir(parents=True)
    (staging / "_analysis").mkdir(parents=True)
    for name, text in evidence.items():
        (staging / "_preprocess" / f"{name}.md").write_text(text, encoding="utf-8")
    for name, text in analyses.items():
        (staging / "_analysis" / f"{name}.md").write_text(text, encoding="utf-8")
    return staging


def _document(staging: Path, name: str, text: str) -> None:
    (staging / f"{name}.md").write_text(text, encoding="utf-8")


def _sidecar(staging: Path, name: str, entries: list[dict]) -> None:
    provenance = staging / "_provenance"
    provenance.mkdir(exist_ok=True)
    (provenance / f"{name}.json").write_text(
        json.dumps({"document": name, "entries": entries}), encoding="utf-8"
    )


def _entry(group: str, *, components=None, unit=None, claim="a claim") -> dict:
    entry = {
        "claim": claim,
        "group": group,
        "unit": unit,
        "source": f"_analysis/{group}/COMPONENTS.md",
        "verbatim": True,
    }
    if components is not None:
        entry["components"] = list(components)
    return entry


def _job(name: str, *components: str) -> str:
    lines = [f"## Talend Job: {name}\n"]
    lines += [f"#### {c} ({c.rsplit('_', 1)[0]})\n" for c in components]
    return "".join(lines)


# --------------------------------------------------------------------------
# 1. The defect batching re-introduces
# --------------------------------------------------------------------------


def test_a_sibling_groups_component_is_misattributed_not_absent(tmp_path: Path) -> None:
    """The run-51 failure at group scale: the name is real in the batch, which
    is exactly why a combined-evidence check does not catch it."""
    staging = _staging(
        tmp_path,
        {
            "Orders": _job("OrderLoad", "tMSSqlOutput_1", "tMap_1"),
            "Invoices": _job("InvoiceLoad", "tDBOutput_1", "tMap_1"),
        },
        {
            # The Invoices worker wrote Orders' component into Invoices' analysis.
            "Invoices": "InvoiceLoad writes through tMSSqlOutput_1 and tMap_1.",
            "Orders": "OrderLoad writes through tMSSqlOutput_1.",
        },
    )
    report = check_group_attribution(staging)

    assert report.analyses_checked == 2
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert (finding.group, finding.component) == ("Invoices", "tMSSqlOutput_1")
    assert finding.classification == "misattributed"
    assert finding.present_in == ("Orders",)
    assert "only present in Orders" in finding.message()


def test_a_component_in_no_groups_evidence_is_the_worse_defect(tmp_path: Path) -> None:
    """Two different defects, and the report must not conflate them."""
    staging = _staging(
        tmp_path,
        {"Orders": _job("OrderLoad", "tMap_1")},
        {"Orders": "OrderLoad uses tMap_1 and tSetGlobalVar_3."},
    )
    report = check_group_attribution(staging)

    assert [f.component for f in report.absent] == ["tSetGlobalVar_3"]
    assert not report.misattributed
    assert "exists nowhere in the scan" in report.findings[0].message()


def test_a_clean_analysis_reports_nothing(tmp_path: Path) -> None:
    staging = _staging(
        tmp_path,
        {"Orders": _job("OrderLoad", "tMSSqlOutput_1", "tMap_1")},
        {"Orders": "OrderLoad: tMap_1 -> tMSSqlOutput_1."},
    )
    report = check_group_attribution(staging)
    assert report.ok
    assert report.findings == ()


def test_a_cross_group_reference_is_reported_but_named_as_such(tmp_path: Path) -> None:
    """A group that genuinely calls another group's design has a reason to name
    its components. That is a signal, not a verdict, so the report says where
    the component lives rather than calling it invented."""
    staging = _staging(
        tmp_path,
        {
            "Callers": _job("Caller", "tRunJob_1"),
            "Shared": _job("SharedWriter", "tFileOutputDelimited_1"),
        },
        {"Callers": "Caller invokes SharedWriter, whose tFileOutputDelimited_1 writes the file."},
    )
    report = check_group_attribution(staging)
    assert [f.classification for f in report.findings] == ["misattributed"]
    assert report.findings[0].present_in == ("Shared",)


# --------------------------------------------------------------------------
# 2. Which file is which group's evidence
# --------------------------------------------------------------------------


def test_a_prefix_sharing_sibling_is_not_treated_as_evidence(tmp_path: Path) -> None:
    """``glob(f"{group}*.md")`` makes forSuppliersFourps.md part of
    forSuppliers's evidence. Both are real, separate ETTM groups, and that glob
    is precisely the cross-group leak this module exists to detect."""
    staging = _staging(
        tmp_path,
        {
            "forSuppliers": _job("A", "tMap_1"),
            "forSuppliersFourps": _job("B", "tRest_9"),
        },
        {"forSuppliers": "A uses tMap_1 and tRest_9."},
    )
    files = evidence_files_for_group(staging / "_preprocess", "forSuppliers")
    assert [f.name for f in files] == ["forSuppliers.md"]

    report = check_group_attribution(staging)
    assert [f.component for f in report.findings] == ["tRest_9"]
    assert report.findings[0].present_in == ("forSuppliersFourps",)


def test_a_split_group_is_checked_against_both_parts(tmp_path: Path) -> None:
    staging = _staging(
        tmp_path,
        {"VRI_part1": _job("A", "tMap_1"), "VRI_part2": _job("B", "tJava_7")},
        {"VRI": "A uses tMap_1; B uses tJava_7."},
    )
    assert [f.stem for f in evidence_files_for_group(staging / "_preprocess", "VRI")] == [
        "VRI_part1",
        "VRI_part2",
    ]
    assert check_group_attribution(staging).ok


def test_a_case_collision_slice_is_its_own_group(tmp_path: Path) -> None:
    """``monitoring`` cannot share a path with ``Monitoring`` on a
    case-insensitive filesystem, so it is staged as ``monitoring_part_ci1`` and
    run 259 wrote ``_analysis/monitoring_part_ci1.md`` for it. Stripping that
    suffix re-creates the collision it was minted to avoid."""
    staging = _staging(
        tmp_path,
        {"Monitoring": _job("A", "tMap_1"), "monitoring_part_ci1": _job("b", "tJava_2")},
        {"monitoring_part_ci1": "b uses tJava_2."},
    )
    files = evidence_files_for_group(staging / "_preprocess", "monitoring_part_ci1")
    assert [f.stem for f in files] == ["monitoring_part_ci1"]
    assert check_group_attribution(staging).ok


def test_a_collided_group_that_also_split_keeps_its_nested_parts(tmp_path: Path) -> None:
    staging = _staging(
        tmp_path,
        {"g_part_ci1_1": _job("A", "tMap_1"), "g_part_ci1_2": _job("B", "tJava_2")},
        {"g_part_ci1": "A: tMap_1. B: tJava_2."},
    )
    assert [
        f.stem for f in evidence_files_for_group(staging / "_preprocess", "g_part_ci1")
    ] == ["g_part_ci1_1", "g_part_ci1_2"]
    assert check_group_attribution(staging).ok


@pytest.mark.parametrize(
    ("slice_name", "expected"),
    [
        ("Orders", "Orders"),
        ("VRI_part1", "VRI"),
        ("VRI_part2", "VRI"),
        ("monitoring_part_ci1", "monitoring_part_ci1"),
        ("g_part_ci1_2", "g_part_ci1"),
        # Not a slice suffix: a group really named like this keeps its name.
        ("Orders_v2", "Orders_v2"),
    ],
)
def test_slice_to_analysis_group(slice_name: str, expected: str) -> None:
    assert analysis_group_for_slice(slice_name) == expected


def test_an_analysis_with_no_evidence_file_is_unchecked_not_clean(tmp_path: Path) -> None:
    """An empty allowed set disables the component check entirely, so a group
    whose evidence is missing must be reported rather than counted clean."""
    staging = _staging(tmp_path, {}, {"Ghost": "Ghost uses tMap_1."})
    report = check_group_attribution(staging)
    assert report.groups_without_evidence == ("Ghost",)
    assert report.findings == ()
    assert "1 unchecked (no evidence file)" in report.summary_line()


def test_summaries_and_internal_artifacts_are_not_group_analyses(tmp_path: Path) -> None:
    staging = _staging(
        tmp_path,
        {"Orders": _job("A", "tMap_1")},
        {
            "Orders": "A uses tMap_1.",
            "Orders_summary": "A uses tInvented_9.",
            "_bundle_core": "tInvented_8 everywhere",
            "_shared_library": "tInvented_7 everywhere",
        },
    )
    assert analysis_group_names(staging / "_analysis") == ["Orders"]
    assert check_group_attribution(staging).ok


# --------------------------------------------------------------------------
# 3. Advisory, never a gate
# --------------------------------------------------------------------------


def test_the_report_is_written_and_says_it_is_advisory(tmp_path: Path) -> None:
    staging = _staging(
        tmp_path,
        {"Orders": _job("A", "tMap_1")},
        {"Orders": "A uses tMap_1 and tGhost_4."},
    )
    report = check_group_attribution(staging)
    path = write_group_attribution_report(staging, report)

    assert path == staging / ASSESSMENT_DIRNAME / REPORT_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["advisory"] is True
    assert payload["ok"] is False
    assert payload["absent_count"] == 1
    assert payload["findings"][0]["component"] == "tGhost_4"
    assert payload["group_errors"]["Orders"]
    # The per-group error text is the custom lane's, not a second wording.
    assert "do not exist in" in payload["group_errors"]["Orders"][0]


def test_warnings_lead_with_the_worse_defect(tmp_path: Path) -> None:
    staging = _staging(
        tmp_path,
        {"Orders": _job("A", "tMap_1"), "Other": _job("B", "tRest_2")},
        {"Orders": "A uses tMap_1, tRest_2 and tGhost_4."},
    )
    report = check_group_attribution(staging)
    lines = [m for m, _ in attribution_warnings(report)]
    assert "exists nowhere" in lines[1]
    assert "only present in Other" in lines[2]
    assert all(level == "warning" for _, level in attribution_warnings(report))


def test_a_clean_run_says_so_at_info_level(tmp_path: Path) -> None:
    staging = _staging(
        tmp_path, {"Orders": _job("A", "tMap_1")}, {"Orders": "A uses tMap_1."}
    )
    lines = attribution_warnings(check_group_attribution(staging))
    assert [level for _, level in lines] == ["info"]
    assert "clean across 1 group analyses" in lines[0][0]


def test_nothing_to_check_is_silent(tmp_path: Path) -> None:
    """The custom lane deletes ``_analysis/`` in its own Phase 4, so this
    finalizer runs there with nothing to look at. It must not warn about it."""
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    report = check_group_attribution(staging)
    assert report.analyses_checked == 0
    assert attribution_warnings(report) == []
    assert not (staging / ASSESSMENT_DIRNAME).exists()


def test_an_unreadable_analysis_does_not_raise(tmp_path: Path) -> None:
    staging = _staging(tmp_path, {"Orders": _job("A", "tMap_1")}, {})
    (staging / "_analysis" / "Orders.md").mkdir()  # a directory where a file is expected
    report = check_group_attribution(staging)
    assert report.analyses_checked == 0 or report.ok


# --------------------------------------------------------------------------
# 4. Calibration against the unbatched baseline
# --------------------------------------------------------------------------


@pytest.mark.skipif(not RUN_259_STAGING.is_dir(), reason="run-259 artifacts absent")
def test_the_guard_is_silent_on_run_259s_one_agent_per_group_analyses() -> None:
    """15 real group analyses, each written by a sub-agent that held exactly one
    group's evidence. A guard that fires here would be measuring the writing
    style rather than the batching, and would be useless as a safety net."""
    report = check_group_attribution(RUN_259_STAGING)

    assert report.analyses_checked == 15
    assert report.groups_without_evidence == ()
    assert report.findings == (), [f.message() for f in report.findings[:10]]


# --------------------------------------------------------------------------
# 5. Documents: declared references, with the prose regex as the fallback
# --------------------------------------------------------------------------


def test_a_declared_reference_is_checked_against_the_declared_groups_evidence(
    tmp_path: Path,
) -> None:
    """The run-51 failure at document scale, and the shape this slice adds: the
    writer states which group a claim came from, so the check is set membership
    against that group's own evidence -- not a regex over the document."""
    staging = _staging(
        tmp_path,
        {
            "Orders": _job("OrderLoad", "tMSSqlOutput_1"),
            "Invoices": _job("InvoiceLoad", "tDBOutput_1"),
        },
        {"Orders": "OrderLoad writes through tMSSqlOutput_1."},
    )
    _document(staging, "02_COMPONENT_INVENTORY", "InvoiceLoad writes the ledger.")
    _sidecar(
        staging,
        "02_COMPONENT_INVENTORY",
        [_entry("Invoices", components=["tMSSqlOutput_1"], claim="InvoiceLoad writes")],
    )

    report = check_group_attribution(staging)

    assert report.document_modes == {"02_COMPONENT_INVENTORY": MODE_DECLARED}
    assert len(report.document_findings) == 1
    finding = report.document_findings[0]
    assert (finding.group, finding.component) == ("Invoices", "tMSSqlOutput_1")
    assert finding.origin == ORIGIN_DECLARED
    assert finding.classification == "misattributed"
    assert finding.present_in == ("Orders",)
    assert "DECLARED as evidence from group Invoices" in finding.message()
    assert 'for the claim "InvoiceLoad writes"' in finding.message()


def test_a_declared_reference_that_exists_nowhere_keeps_the_absent_split(
    tmp_path: Path,
) -> None:
    staging = _staging(tmp_path, {"Orders": _job("OrderLoad", "tMap_1")}, {})
    _document(staging, "00_AGENT_BRIEF", "OrderLoad is the entry point.")
    _sidecar(staging, "00_AGENT_BRIEF", [_entry("Orders", components=["tGhost_4"])])

    report = check_group_attribution(staging)

    assert [f.classification for f in report.document_findings] == ["absent"]
    assert report.document_findings[0].origin == ORIGIN_DECLARED
    assert "exists nowhere in the scan" in report.document_findings[0].message()


def test_the_document_text_is_not_read_when_provenance_is_declared(
    tmp_path: Path,
) -> None:
    """The point of the slice. The document names a component that exists
    nowhere, but the sidecar does not rest any claim on it, so the declared lane
    does not report it -- verification follows the declaration, not the prose."""
    staging = _staging(tmp_path, {"Orders": _job("OrderLoad", "tMap_1")}, {})
    _document(staging, "00_AGENT_BRIEF", "Prose mentioning tNeverScanned_8 in passing.")
    _sidecar(staging, "00_AGENT_BRIEF", [_entry("Orders", components=["tMap_1"])])

    report = check_group_attribution(staging)

    assert report.document_modes == {"00_AGENT_BRIEF": MODE_DECLARED}
    assert report.document_findings == ()
    assert report.documents[0].checked_refs == 1
    assert report.documents[0].inferred_refs == 0


def test_a_document_with_no_sidecar_still_gets_the_prose_check(tmp_path: Path) -> None:
    """Skipping a document because its writer omitted a file would be worse
    than the prose dependency being removed, so the fallback stays."""
    staging = _staging(tmp_path, {"Orders": _job("OrderLoad", "tMap_1")}, {})
    _document(staging, "00_AGENT_BRIEF", "OrderLoad uses tMap_1 and tGhost_4.")

    report = check_group_attribution(staging)

    assert report.document_modes == {"00_AGENT_BRIEF": MODE_INFERRED}
    assert [f.component for f in report.document_findings] == ["tGhost_4"]
    assert report.document_findings[0].origin == ORIGIN_INFERRED
    assert "no sidecar was written" in report.documents[0].note


def test_a_sidecar_that_declares_no_component_falls_back_too(tmp_path: Path) -> None:
    """Run 260's sidecars are exactly this shape: real entries, no declared
    component. A run that produced them must not report itself as verified."""
    staging = _staging(tmp_path, {"Orders": _job("OrderLoad", "tMap_1")}, {})
    _document(staging, "00_AGENT_BRIEF", "OrderLoad uses tMap_1 and tGhost_4.")
    _sidecar(staging, "00_AGENT_BRIEF", [_entry("Orders", unit="OrderLoad")])

    report = check_group_attribution(staging)

    assert report.document_modes == {"00_AGENT_BRIEF": MODE_INFERRED}
    assert report.documents[0].entries == 1
    assert report.documents[0].declared_entries == 0
    assert "sidecar declared no component reference" in report.documents[0].note
    assert [f.component for f in report.document_findings] == ["tGhost_4"]


def test_a_component_named_in_the_unit_field_counts_as_declared(
    tmp_path: Path,
) -> None:
    """``unit`` is a field the writer filled in, not prose. Reading it is what
    makes the declared lane reachable for sidecars written before the
    ``components`` field existed -- 13 of run 260's 46 entries."""
    staging = _staging(tmp_path, {"Orders": _job("OrderLoad", "tMap_1")}, {})
    _document(staging, "00_AGENT_BRIEF", "OrderLoad transforms the feed.")
    _sidecar(staging, "00_AGENT_BRIEF", [_entry("Orders", unit="OrderLoad/tGhost_4")])

    report = check_group_attribution(staging)

    assert report.document_modes == {"00_AGENT_BRIEF": MODE_DECLARED}
    assert [f.component for f in report.document_findings] == ["tGhost_4"]
    assert report.document_findings[0].origin == ORIGIN_DECLARED


def test_the_claim_text_is_never_mined_for_components(tmp_path: Path) -> None:
    """``claim`` is a quotation. Recovering structure from it would re-create
    the prose dependency inside the typed artifact that replaced it."""
    staging = _staging(tmp_path, {"Orders": _job("OrderLoad", "tMap_1")}, {})
    _document(staging, "00_AGENT_BRIEF", "OrderLoad transforms the feed.")
    _sidecar(
        staging,
        "00_AGENT_BRIEF",
        [_entry("Orders", claim="the tGhost_4 component writes the file")],
    )

    report = check_group_attribution(staging)
    assert report.document_modes == {"00_AGENT_BRIEF": MODE_INFERRED}
    assert report.document_findings == ()


def test_the_fallback_cannot_call_a_cross_group_reference_misattributed(
    tmp_path: Path,
) -> None:
    """A document legitimately spans groups. Without a declaration saying which
    group a name was supposed to come from, only 'exists nowhere' is decidable,
    and claiming more would make the fallback noisy on correct documents."""
    staging = _staging(
        tmp_path,
        {"Orders": _job("OrderLoad", "tMap_1"), "Invoices": _job("Inv", "tDBOutput_1")},
        {},
    )
    _document(staging, "01_ARCHITECTURE_MAP", "tMap_1 feeds tDBOutput_1 downstream.")

    report = check_group_attribution(staging)
    assert report.document_findings == ()
    assert report.documents[0].inferred_refs == 2


def test_a_declared_group_with_no_evidence_is_unverifiable_not_clean(
    tmp_path: Path,
) -> None:
    staging = _staging(tmp_path, {"Orders": _job("OrderLoad", "tMap_1")}, {})
    _document(staging, "00_AGENT_BRIEF", "A brief.")
    _sidecar(staging, "00_AGENT_BRIEF", [_entry("Ghost", components=["tMap_1"])])

    report = check_group_attribution(staging)
    assert report.documents[0].unverifiable_groups == ("Ghost",)
    assert report.documents[0].checked_refs == 0
    assert report.document_findings == ()
    assert "had no evidence file" in report.document_summary_line()


def test_a_broken_sidecar_names_its_own_defect(tmp_path: Path) -> None:
    """'The writer skipped provenance' and 'the writer wrote a broken file' are
    different failures, and only one of them is fixed by asking again."""
    staging = _staging(tmp_path, {"Orders": _job("OrderLoad", "tMap_1")}, {})
    _document(staging, "00_AGENT_BRIEF", "OrderLoad uses tMap_1.")
    (staging / "_provenance").mkdir()
    (staging / "_provenance" / "00_AGENT_BRIEF.json").write_text("{not json", encoding="utf-8")

    report = check_group_attribution(staging)
    assert report.document_modes == {"00_AGENT_BRIEF": MODE_INFERRED}
    assert "could not be read as JSON" in report.documents[0].note
    assert report.document_findings == ()


@pytest.mark.parametrize(
    "payload",
    ["[]", '{"entries": "nope"}', '{"entries": [{"unit": "x"}]}', '{"entries": []}'],
)
def test_a_sidecar_of_the_wrong_shape_never_raises(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "doc.json"
    path.write_text(payload, encoding="utf-8")
    entries, problem = read_provenance_entries(path)
    assert entries == ()
    assert problem


def test_a_declared_miss_outranks_an_inferred_one_in_the_log(tmp_path: Path) -> None:
    """A declared reference is the writer's own statement about where a claim
    came from, so it is the line the operator must read first."""
    staging = _staging(tmp_path, {"Orders": _job("OrderLoad", "tMap_1")}, {})
    _document(staging, "00_AGENT_BRIEF", "OrderLoad uses tMap_1 and tProseGhost_2.")
    _document(staging, "01_ARCHITECTURE_MAP", "The map.")
    _sidecar(
        staging, "01_ARCHITECTURE_MAP", [_entry("Orders", components=["tDeclaredGhost_1"])]
    )

    report = check_group_attribution(staging)
    lines = [m for m, _ in attribution_warnings(report)]
    declared = next(i for i, line in enumerate(lines) if "tDeclaredGhost_1" in line)
    inferred = next(i for i, line in enumerate(lines) if "tProseGhost_2" in line)
    assert declared < inferred


def test_the_document_lane_reports_its_own_coverage(tmp_path: Path) -> None:
    """A run where every document fell back to prose is unverified, not clean.
    Only the coverage number tells the two apart, so it is always logged."""
    staging = _staging(tmp_path, {"Orders": _job("OrderLoad", "tMap_1")}, {})
    _document(staging, "00_AGENT_BRIEF", "OrderLoad uses tMap_1.")
    _document(staging, "01_ARCHITECTURE_MAP", "OrderLoad uses tMap_1.")
    _sidecar(
        staging,
        "01_ARCHITECTURE_MAP",
        [_entry("Orders", components=["tMap_1"]), _entry("Orders", unit=None)],
    )

    report = check_group_attribution(staging)
    line = report.document_summary_line()
    assert "1/2 documents verified from declared provenance" in line
    assert "1 from prose (fallback)" in line
    assert "1/2 sidecar entries declared a component" in line
    assert ("document attribution", "info") == (line.split(":")[0].lower(), "info")
    assert any(
        message == line and level == "info" for message, level in attribution_warnings(report)
    )


def test_internal_artifacts_are_not_published_documents(tmp_path: Path) -> None:
    staging = _staging(tmp_path, {"Orders": _job("OrderLoad", "tMap_1")}, {})
    _document(staging, "00_AGENT_BRIEF", "OrderLoad uses tMap_1.")
    (staging / "_manifest.md").write_text("tGhost_9 everywhere", encoding="utf-8")

    assert [p.name for p in published_documents(staging)] == ["00_AGENT_BRIEF.md"]
    assert check_group_attribution(staging).document_findings == ()


def test_the_document_lane_is_silent_when_the_evidence_is_already_gone(
    tmp_path: Path,
) -> None:
    """The custom lane deletes ``_preprocess/`` in its own Phase 4 and lands
    here. An empty allowed set would pass everything; saying nothing is the
    correct answer, and 'clean' would be a lie."""
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    _document(staging, "00_AGENT_BRIEF", "OrderLoad uses tGhost_4.")

    report = check_group_attribution(staging)
    assert report.documents == ()
    assert report.document_findings == ()
    assert attribution_warnings(report) == []


def test_the_report_carries_both_lanes_and_says_which_ran(tmp_path: Path) -> None:
    staging = _staging(
        tmp_path,
        {"Orders": _job("OrderLoad", "tMap_1")},
        {"Orders": "OrderLoad uses tMap_1."},
    )
    _document(staging, "00_AGENT_BRIEF", "A brief.")
    _sidecar(staging, "00_AGENT_BRIEF", [_entry("Orders", components=["tGhost_4"])])

    payload = json.loads(
        write_group_attribution_report(
            staging, check_group_attribution(staging)
        ).read_text(encoding="utf-8")
    )

    assert payload["schema_version"] == 2
    assert payload["advisory"] is True
    assert payload["ok"] is False
    # The analysis lane is clean; the defect is in a document.
    assert payload["finding_count"] == 0
    assert payload["document_finding_count"] == 1
    assert payload["declared_finding_count"] == 1
    assert payload["document_modes"] == {"00_AGENT_BRIEF": MODE_DECLARED}
    assert payload["document_findings"][0]["origin"] == ORIGIN_DECLARED
    assert payload["document_findings"][0]["document"] == "00_AGENT_BRIEF"
    assert payload["document_errors"]["00_AGENT_BRIEF"]
    assert "declares that its claims rest on" in payload["document_errors"]["00_AGENT_BRIEF"][0]


def test_the_analysis_summary_line_does_not_speak_for_the_documents(
    tmp_path: Path,
) -> None:
    """One line per lane. A document defect that made the analysis line say the
    analyses were dirty would send the operator to the wrong evidence."""
    staging = _staging(
        tmp_path,
        {"Orders": _job("OrderLoad", "tMap_1")},
        {"Orders": "OrderLoad uses tMap_1."},
    )
    _document(staging, "00_AGENT_BRIEF", "OrderLoad uses tGhost_4.")

    report = check_group_attribution(staging)
    assert not report.ok
    assert "clean across 1 group analyses" in report.summary_line()
    assert "1 bad reference(s)" in report.document_summary_line()


# --------------------------------------------------------------------------
# 6. The analysis lane reads both Phase 2 layouts
# --------------------------------------------------------------------------


def test_a_per_section_group_directory_is_checked_like_a_flat_file(
    tmp_path: Path,
) -> None:
    """Slice 2 writes ``_analysis/{group}/{SECTION}.md``. A guard that still
    globbed ``*.md`` would count zero groups and report the run as clean."""
    staging = _staging(tmp_path, {"Orders": _job("OrderLoad", "tMap_1")}, {})
    group_dir = staging / "_analysis" / "Orders"
    group_dir.mkdir()
    (group_dir / "COMPONENTS.md").write_text(
        "OrderLoad uses tMap_1 and tGhost_4.", encoding="utf-8"
    )

    report = check_group_attribution(staging)
    assert analysis_group_names(staging / "_analysis") == ["Orders"]
    assert report.analyses_checked == 1
    assert [f.component for f in report.findings] == ["tGhost_4"]


def test_a_group_directory_with_no_recognised_section_is_reported_not_raised(
    tmp_path: Path,
) -> None:
    """``analysis_store`` raises there on purpose (a transform that loses
    everything must be loud). This guard must still never fail a run."""
    staging = _staging(tmp_path, {"Orders": _job("OrderLoad", "tMap_1")}, {})
    group_dir = staging / "_analysis" / "Orders"
    group_dir.mkdir()
    (group_dir / "notes.md").write_text("tGhost_4", encoding="utf-8")

    report = check_group_attribution(staging)
    assert report.analyses_checked == 1
    assert report.findings == ()


# --------------------------------------------------------------------------
# 7. Calibration against real runs, both lanes
#
# A guard that is noisy on good output gets switched off; one that cannot
# detect an injected fabrication is decoration. Every number below was measured
# on artifacts on disk, not chosen.
# --------------------------------------------------------------------------


@pytest.mark.skipif(not RUN_258_STAGING.is_dir(), reason="run-258 artifacts absent")
def test_run_258s_real_numbers_are_pinned_on_both_lanes() -> None:
    """Run 258: 20 real group analyses and the nine documents written from
    them. The analysis lane's 9 findings are the baseline this slice must not
    move; the document lane finds the three component names those documents
    used that exist nowhere in that run's evidence."""
    report = check_group_attribution(RUN_258_STAGING)

    assert (report.analyses_checked, len(report.findings)) == (20, 9)
    assert (len(report.misattributed), len(report.absent)) == (8, 1)
    assert report.groups_without_evidence == ()

    assert report.documents_checked == 9
    assert sorted(f.component for f in report.document_findings) == [
        "tFlowInput_1",
        "tSchematron_1",
        "tSchematron_2",
    ]
    # No sidecars existed in that run, so every document took the fallback --
    # which is what the coverage number is for.
    assert len(report.inferred_documents) == 9
    assert all(f.origin == ORIGIN_INFERRED for f in report.document_findings)


@pytest.mark.parametrize("staging", CLEAN_STAGINGS, ids=lambda p: p.parent.name)
def test_the_guard_is_silent_on_real_clean_stagings(staging: Path) -> None:
    """Three real estates (20, 24 and 11 groups; 9, 9 and 46 documents). A
    check that fires here is measuring writing style, not fabrication."""
    if not staging.is_dir():
        pytest.skip(f"{staging} absent")
    report = check_group_attribution(staging)

    assert report.analyses_checked > 0
    assert report.findings == (), [f.message() for f in report.findings[:10]]
    assert report.documents_checked > 0
    assert report.document_findings == (), [
        f.message() for f in report.document_findings[:10]
    ]


@pytest.mark.skipif(not RUN_258_STAGING.is_dir(), reason="run-258 artifacts absent")
def test_an_injected_fabrication_is_caught_in_real_output(tmp_path: Path) -> None:
    """The negative control, on a copy of run 258's real staging.
    ``tConvertType_1`` is real in ``maximo`` and not in ``forSuppliers``;
    ``tTotallyInvented_9`` is real nowhere."""
    staging = tmp_path / "docs_staging"
    shutil.copytree(RUN_258_STAGING, staging)
    baseline = len(check_group_attribution(staging).findings)

    analysis = staging / "_analysis" / "forSuppliers.md"
    analysis.write_text(
        analysis.read_text(encoding="utf-8")
        + "\n\nThe route converts via tConvertType_1 and tTotallyInvented_9.\n",
        encoding="utf-8",
    )
    # The same two names, declared this time, in a document's sidecar.
    _document(staging, "00_AGENT_BRIEF", "A brief that names nothing.")
    _sidecar(
        staging,
        "00_AGENT_BRIEF",
        [_entry("forSuppliers", components=["tConvertType_1", "tTotallyInvented_9"])],
    )

    report = check_group_attribution(staging)

    injected = {
        f.component: f
        for f in report.findings
        if f.component in {"tConvertType_1", "tTotallyInvented_9"}
    }
    assert len(report.findings) == baseline + 2
    assert injected["tConvertType_1"].classification == "misattributed"
    assert "maximo" in injected["tConvertType_1"].present_in
    assert injected["tTotallyInvented_9"].classification == "absent"

    declared = {f.component: f for f in report.declared_findings}
    assert set(declared) == {"tConvertType_1", "tTotallyInvented_9"}
    assert declared["tConvertType_1"].classification == "misattributed"
    assert declared["tTotallyInvented_9"].classification == "absent"
    assert report.document_modes["00_AGENT_BRIEF"] == MODE_DECLARED


@pytest.mark.skipif(not RUN_260_PROVENANCE.is_dir(), reason="run-260 sidecars absent")
def test_the_real_sidecars_parse_and_only_partly_reach_the_declared_lane() -> None:
    """Nine real sidecars from run 260, written before ``components`` existed.

    Measured: 46 entries, all parse, and 13 of them (28%) name a component --
    all of them through the ``unit`` field. The rest name a design, a Camel
    route component the shared ``tXxx_N`` regex cannot match, or nothing. So the
    declared lane IS reachable on today's output but does not cover it; the
    schema change is what lifts the number, and this test exists to make a
    regression in it visible rather than to freeze it."""
    total = declaring = 0
    for path in sorted(RUN_260_PROVENANCE.glob("*.json")):
        entries, problem = read_provenance_entries(path)
        assert not problem, f"{path.name}: {problem}"
        total += len(entries)
        declaring += sum(1 for entry in entries if entry.declares_components)

    assert total > 0
    assert 0 < declaring < total, (
        f"{declaring}/{total} entries declare a component; both ends matter -- "
        "zero means the declared lane is unreachable, all means the fallback "
        "is untested on real output"
    )
