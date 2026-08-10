"""Slice 2 -- per-unit verbatim evidence extractor and Phase 1 wiring.

Covers:
(a) the extractor emits verbatim code / SQL / tMap / context for a unit that
    has them, and never trims verbatim evidence before derived niceties;
(b) staged bundles carry a text extension (never ``.bin``) and are readable
    through the in-repo scoped-evidence reader;
(c) the Phase 1 evidence role's packet now includes per-unit verbatim evidence
    and its prompt encodes the CODE / RUNTIME / DOCUMENTED hierarchy.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp_server.experiments.application.fact_ledger import compile_fact_ledger
from mcp_server.experiments.application.grounded_pack import GroundedRole
from mcp_server.experiments.application.run_staged_grounded_candidate import (
    UNIT_EVIDENCE_INDEX_NAME,
    _has_verbatim_unit_evidence,
    _role_prompt,
    _UnitEvidencePlan,
)
from mcp_server.experiments.application.unit_evidence import (
    CATEGORY_CODE,
    CATEGORY_CONTEXT,
    CATEGORY_SQL,
    CATEGORY_TMAP,
    UnitEvidenceExtractor,
)
from mcp_server.experiments.infrastructure.openai_agents.scoped_evidence import (
    ScopedGroundedEvidence,
)


_CODE = "output_row.location = input_row.id * 2; // custom routine"
_SQL = "SELECT id, name FROM Location WHERE active = 1"
_TMAP_EXPR = 'row1.first + " " + row1.last'


def _unit() -> dict:
    return {
        "name": "AssetCheck_GetLocation",
        "group": "AssetCheck",
        "path": "AssetCheck/AssetCheck_GetLocation_0.1.item",
        "version": "0.1",
        "components": [
            {
                "unique_name": "tJavaRow_1",
                "component_type": "tJavaRow",
                "code": _CODE,
            },
            {
                "unique_name": "tMSSqlInput_1",
                "component_type": "tMSSqlInput",
                "params": [{"name": "QUERY", "value": _SQL}],
            },
        ],
        "tmap_data": [
            {
                "var_entries": [
                    {
                        "table_name": "out",
                        "column_name": "fullName",
                        "expression": _TMAP_EXPR,
                    }
                ],
                "entries": [],
            }
        ],
        "context_groups": [
            {
                "name": "inline",
                "variables": [
                    {
                        "name": "db_host",
                        "type": "String",
                        "values": {"DEV": "dev.example", "PROD": "prod.example"},
                    }
                ],
            }
        ],
        "connections": [
            {"source": "tMSSqlInput_1", "target": "tJavaRow_1", "connector_name": "row1"}
        ],
        "trunjob_refs": [],
    }


def _scanner_sources(tmp_path: Path) -> tuple[tuple[str, Path], ...]:
    job_path = tmp_path / "get-location.json"
    job_path.write_text(json.dumps(_unit()), encoding="utf-8")
    meta_path = tmp_path / "meta.json"
    meta_path.write_text(
        json.dumps({"platform": "talend", "stats": {"job_count": 1, "route_count": 0}}),
        encoding="utf-8",
    )
    return (
        ("scan:scan/jobs/AssetCheck/AssetCheck_GetLocation.json", job_path),
        ("scan:scan/meta.json", meta_path),
    )


# (a) -----------------------------------------------------------------------


def test_extractor_emits_verbatim_code_sql_tmap_and_context() -> None:
    bundle = UnitEvidenceExtractor().extract(
        _unit(),
        callers=("AssetCheck_Interface",),
        callees=("AssetCheck_WriteLog",),
    )

    assert bundle.unit_name == "AssetCheck_GetLocation"
    assert _CODE in bundle.text
    assert _SQL in bundle.text
    assert _TMAP_EXPR in bundle.text
    # verbatim section headers present
    assert "## CUSTOM CODE (VERBATIM)" in bundle.text
    assert "## SQL QUERIES (VERBATIM)" in bundle.text
    assert "## TMAP EXPRESSIONS (VERBATIM)" in bundle.text
    # context provenance + per-environment values
    assert "job-embedded" in bundle.text
    assert "DEV=dev.example" in bundle.text and "PROD=prod.example" in bundle.text
    # callers / callees threaded from the fact ledger call graph
    assert "AssetCheck_Interface" in bundle.text
    assert "AssetCheck_WriteLog" in bundle.text
    assert bundle.categories == frozenset(
        {CATEGORY_CODE, CATEGORY_SQL, CATEGORY_TMAP, CATEGORY_CONTEXT}
    )
    assert "tJavaRow_1" in bundle.component_refs
    assert bundle.truncated is False


def test_verbatim_evidence_is_never_trimmed_before_derived_niceties() -> None:
    # A byte budget smaller than the bundle must still keep every verbatim
    # block; only the honest over-budget marker is added.
    bundle = UnitEvidenceExtractor(max_bytes=200).extract(_unit())

    assert bundle.truncated is True
    assert _CODE in bundle.text
    assert _SQL in bundle.text
    assert _TMAP_EXPR in bundle.text
    assert "exceeds the configured evidence byte budget" in bundle.text


# (b) -----------------------------------------------------------------------


def _evidence_packet(tmp_path: Path) -> tuple[Path, _UnitEvidencePlan]:
    source_items = _scanner_sources(tmp_path)
    ledger = compile_fact_ledger(source_items, project_id="assetcheck")
    role_root = tmp_path / "roles" / "evidence-structure"
    (role_root / "input").mkdir(parents=True)
    # ScopedGroundedEvidence requires the ledger in the packet.
    (role_root / "input" / "FACT_LEDGER.json").write_text(
        json.dumps(ledger, indent=2), encoding="utf-8"
    )
    plan = _UnitEvidencePlan.from_frozen_sources(source_items, ledger)
    plan.stage(role_root, list(source_items))
    return role_root, plan


def test_staged_bundle_uses_text_extension_and_is_reader_visible(tmp_path: Path) -> None:
    role_root, _plan = _evidence_packet(tmp_path)

    evidence_dir = role_root / "input" / "unit_evidence"
    staged = list(evidence_dir.glob("*"))
    assert len(staged) == 1
    bundle_file = staged[0]
    assert bundle_file.suffix == ".md"
    assert not any(path.suffix == ".bin" for path in evidence_dir.rglob("*"))

    index = json.loads((role_root / "input" / UNIT_EVIDENCE_INDEX_NAME).read_text())
    assert index["unit_count"] == 1
    entry = index["units"][0]
    assert entry["unit"] == "AssetCheck_GetLocation"
    assert entry["relative_path"] == "unit_evidence/AssetCheck_GetLocation.md"
    assert set(entry["categories"]) == {
        CATEGORY_CODE,
        CATEGORY_SQL,
        CATEGORY_TMAP,
        CATEGORY_CONTEXT,
    }

    # The role's own scoped reader (the only evidence surface a role has) can
    # both list and read the staged verbatim bundle.
    evidence = ScopedGroundedEvidence.from_packet(role_root, max_context_bytes=5_000_000)
    logical = "input/unit_evidence/AssetCheck_GetLocation.md"
    listed = {item["logical_name"] for item in evidence.list()["items"]}
    assert logical in listed
    read = evidence.read(evidence_id=logical, max_bytes=evidence.max_read_bytes)
    assert _CODE in read["content"]
    assert _SQL in read["content"]
    assert read["truncated"] is False


# (c) -----------------------------------------------------------------------


def _evidence_role() -> GroundedRole:
    return GroundedRole(
        id="evidence-structure",
        agent="nexus-doc-evidence",
        stage="evidence",
        owns=("candidate/orchestration/evidence_packets/structure.json",),
        depends_on=(),
        source_globs=("FACT_LEDGER.json",),
        max_context_bytes=134_217_728,
    )


def test_phase1_packet_and_prompt_encode_the_evidence_hierarchy(tmp_path: Path) -> None:
    role_root, _plan = _evidence_packet(tmp_path)
    assert _has_verbatim_unit_evidence(role_root) is True

    prompt = _role_prompt(_evidence_role(), verbatim_unit_evidence=True)
    assert "input/unit_evidence/" in prompt
    assert "UNIT_EVIDENCE_INDEX.json" in prompt
    # CODE is the single source of truth
    assert "CODE is the single source of truth" in prompt
    # RUNTIME validates but is not available -> unknown
    assert "NOT AVAILABLE" in prompt
    assert "static evidence is not runtime proof" in prompt.casefold()
    # Documented mappings add context but never override the code
    assert "never override the code" in prompt
    # The hierarchy survives the bounded repair prompt too.
    repair_prompt = _role_prompt(
        _evidence_role(), verbatim_unit_evidence=True, repair=True
    )
    assert "CODE is the single source of truth" in repair_prompt


def test_prompt_hierarchy_is_absent_without_staged_unit_evidence() -> None:
    prompt = _role_prompt(_evidence_role(), verbatim_unit_evidence=False)
    assert "CODE is the single source of truth" not in prompt
    # base grounded language is unchanged
    assert "Static evidence is not runtime proof." in prompt
