"""Golden-file test: lineage extraction against a real scan fixture.

`Maximo2AssetCheck_SendLocation.json` is a verbatim copy of a real,
credential-filtered per-job scan output from the `reverse_engineer_VW`
project (S0 spike corpus) -- committed under tests/fixtures/lineage/ so
this test is hermetic and does not depend on a mutable intodoc-output/
directory being present. Expected values below were independently
verified by hand against the tMap's real expressions (see the S4 session
notes) before being pinned here.
"""

import json
from pathlib import Path

import pytest

from intodoc.lineage import build_lineage_matrix
from intodoc.models import LineageConfidence, ParsedJob, ScanResult
from intodoc.scanner.credential_filter import CredentialFilter

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "lineage" / "Maximo2AssetCheck_SendLocation.json"


@pytest.fixture
def vw_job() -> ParsedJob:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return ParsedJob.model_validate(data)


@pytest.fixture
def vw_matrix(vw_job):
    scan_result = ScanResult(platform="talend", repo_root="/fake/vw", jobs=[vw_job])
    return build_lineage_matrix(scan_result, CredentialFilter())


class TestGoldenVWLineage:
    """Maximo2AssetCheck_SendLocation has one tMap (tMap_1): input row17 ->
    output logVariables, wired row17: tStatCatcher_1 -> tMap_1 and
    logVariables: tMap_1 -> tSetGlobalVar_4 (verified against connections.json
    in the fixture)."""

    def test_entry_count_matches_hand_verified_fan_out(self, vw_matrix):
        # Message(2: message,origin via Var) + Description(2: message,origin,
        # direct - no Var) + Severity(1: message via Var) + LogCode(2: message,
        # origin via Var) + Values(2: message,origin via Var) = 9.
        assert len(vw_matrix.entries) == 9
        assert vw_matrix.summary["total_entries"] == 9
        assert vw_matrix.summary["resolved_entries"] == 9
        assert vw_matrix.summary["unresolved_entries"] == 0
        assert vw_matrix.summary["tmap_components_processed"] == 1
        assert vw_matrix.summary["jobs_with_tmap"] == 1

    def test_all_entries_resolve_row17_to_tstatcatcher(self, vw_matrix):
        # Every entry's single input table is row17; connections.json wires
        # row17: tStatCatcher_1 -> tMap_1, so every entry's source component
        # must be tStatCatcher_1, regardless of target column.
        for entry in vw_matrix.entries:
            assert entry.source_table == "row17"
            assert entry.source_component == "tStatCatcher_1"
            assert entry.target_table == "logVariables"
            assert entry.target_component == "tSetGlobalVar_4"

    def test_no_entry_reaches_certain_confidence(self, vw_matrix):
        # Every real expression here is a ternary (direct or via Var) --
        # none is a clean single bare passthrough end to end, so nothing
        # should be over-claimed as CERTAIN (architecture doc D2 caution).
        assert all(e.confidence == LineageConfidence.LIKELY for e in vw_matrix.entries)

    def test_message_column_var_chained_through_ternary(self, vw_matrix):
        message_entries = [e for e in vw_matrix.entries if e.target_column == "Message"]
        assert {e.source_column for e in message_entries} == {"message", "origin"}
        for e in message_entries:
            assert e.resolved is True
            assert e.dynamic_refs == []
            # Var hop (the ternary defining Var.Message) recorded before the
            # final "Var.Message" passthrough hop.
            types = [t.transformation_type for t in e.transformations]
            assert types == ["expression", "direct_map"]
            assert e.transformations[-1].expression == "Var.Message"

    def test_description_column_direct_ternary_flags_globalmap_dynamic(self, vw_matrix):
        # Description does NOT go through Var -- it references globalMap.get
        # directly (verified real expression) and must surface as dynamic,
        # never silently dropped, while still resolving its static refs.
        desc_entries = [e for e in vw_matrix.entries if e.target_column == "Description"]
        assert {e.source_column for e in desc_entries} == {"message", "origin"}
        for e in desc_entries:
            assert e.resolved is True
            assert e.dynamic_refs == ["globalMap.get"]
            assert e.transformations[0].transformation_type == "expression"

    def test_values_column_flags_routine_call_and_globalmap_dynamic(self, vw_matrix):
        # Values' Var expression calls Long.toString(...) and globalMap.get(...)
        # twice (verified real expression) -- both recorded as dynamic.
        values_entries = [e for e in vw_matrix.entries if e.target_column == "Values"]
        assert {e.source_column for e in values_entries} == {"message", "origin"}
        for e in values_entries:
            assert e.dynamic_refs == ["Long.toString", "globalMap.get"]

    def test_no_credentials_in_output(self, vw_matrix):
        cred_filter = CredentialFilter()
        dumped = vw_matrix.model_dump_json()
        assert not cred_filter.has_credentials(dumped)

    def test_no_unmapped_columns(self, vw_matrix):
        # Both row17 and logVariables are wired in this job's connections;
        # nothing should be reported as unresolved wiring.
        assert vw_matrix.unmapped_columns == []
