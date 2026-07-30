from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.experiments.application.fact_ledger import (
    compile_fact_ledger,
    validate_document_claims,
    validate_fact_ledger_preflight,
)
from mcp_server.experiments.application.grounded_preflight import (
    preflight_grounded_sources,
    reject_secret_like_output,
)
from mcp_server.experiments.application.run_quick_candidate import QuickCandidateError


def _write(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_fact_ledger_compiles_authoritative_generic_scanner_counts(
    tmp_path: Path,
) -> None:
    job_a = _write(
        tmp_path / "scan" / "jobs" / "GroupA" / "JobA.json",
        {
            "name": "JobA",
            "group": "GroupA",
            "version": "1.0",
            "components": [
                {"unique_name": "tRunJob_1", "component_type": "tRunJob"},
                {"unique_name": "tJava_1", "component_type": "tJava"},
            ],
            "connections": [{}, {}],
            "trunjob_refs": [{"child_job": "JobB"}],
        },
    )
    job_b = _write(
        tmp_path / "scan" / "jobs" / "GroupA" / "JobB.json",
        {
            "name": "JobB",
            "group": "GroupA",
            "version": "1.0",
            "components": [
                {"unique_name": "tJava_2", "component_type": "tJava"},
                {
                    "unique_name": "tFileCopy_1",
                    "component_type": "tFileCopy",
                    "active": False,
                },
            ],
            "connections": [{}],
            "trunjob_refs": [],
        },
    )
    manifest = _write(
        tmp_path / "scan" / "manifest.json",
        {
            "job_calls": {"JobA": ["JobB"]},
            "connection_used_by": {"PrimaryDb": ["JobA", "JobB"]},
        },
    )

    ledger = compile_fact_ledger(
        (
            ("scan:scan/jobs/GroupA/JobA.json", job_a),
            ("scan:scan/jobs/GroupA/JobB.json", job_b),
            ("scan:scan/manifest.json", manifest),
        )
    )

    assert ledger["authority"] == "deterministic-frozen-scanner-facts"
    assert ledger["totals"] == {
        "canonical_units": 2,
        "groups": 1,
        "components": 4,
        "job_call_edges": 1,
        "connection_usage_edges": 2,
        "inactive_components": 1,
    }
    assert ledger["groups"] == [
        {
            "name": "GroupA",
            "unit_count": 2,
            "component_count": 4,
            "units": ["JobA", "JobB"],
        }
    ]
    assert ledger["component_types"] == [
        {"name": "tFileCopy", "count": 1, "units": {"JobB": 1}},
        {"name": "tJava", "count": 2, "units": {"JobA": 1, "JobB": 1}},
        {"name": "tRunJob", "count": 1, "units": {"JobA": 1}},
    ]
    assert ledger["connections"][0]["job_count"] == 2
    assert ledger["call_graph"][0]["callee_count"] == 1


def test_fact_ledger_uses_logical_json_identity_for_frozen_bin_content(
    tmp_path: Path,
) -> None:
    frozen_job = _write(
        tmp_path / "content" / "job.bin",
        {
            "name": "ShipmentPlanner",
            "group": "Logistics",
            "version": "2026.7",
            "components": [{"component_type": "route"}],
        },
    )
    frozen_result = _write(
        tmp_path / "content" / "result.bin",
        {
            "jobs": [
                {
                    "name": "ShipmentPlanner",
                    "group": "Logistics",
                    "version": "2026.7",
                    "components": [{"component_type": "route"}],
                }
            ],
            "stats": {"total_jobs": 1},
        },
    )
    frozen_manifest = _write(
        tmp_path / "content" / "manifest.bin",
        {"job_groups": {"ShipmentPlanner": "Logistics"}},
    )

    ledger = compile_fact_ledger(
        (
            ("scan:scan/jobs/Logistics/ShipmentPlanner.json", frozen_job),
            ("scan:scan_result.json", frozen_result),
            ("scan:scan/manifest.json", frozen_manifest),
        )
    )

    assert ledger["totals"]["canonical_units"] == 1
    assert ledger["totals"]["components"] == 1
    assert validate_fact_ledger_preflight(ledger)["status"] == "pass"


@pytest.mark.parametrize(
    ("split_payload", "result_payload", "manifest_payload", "expected_code"),
    (
        (
            {"name": "JobA", "components": []},
            {
                "jobs": [{"name": "JobB", "components": []}],
                "stats": {"total_jobs": 1},
            },
            {"job_groups": {"JobA": "default"}},
            "split_embedded_unit_mismatch",
        ),
        (
            {"name": "JobA"},
            {
                "jobs": [{"name": "JobA"}],
                "stats": {"total_jobs": 1},
            },
            {"job_groups": {"JobA": "default"}},
            "incomplete_component_inventory",
        ),
        (
            {"name": "JobA", "components": []},
            {
                "jobs": [{"name": "JobA", "components": []}],
                "stats": {"total_jobs": 2},
            },
            {"job_groups": {"JobA": "default"}},
            "scanner_total_reconciliation_mismatch",
        ),
        (
            {"name": "JobA", "components": []},
            {
                "jobs": [{"name": "JobA", "components": []}],
                "stats": {"total_jobs": 1},
            },
            {"job_groups": {"JobB": "default"}},
            "manifest_unit_reconciliation_mismatch",
        ),
    ),
)
def test_fact_ledger_preflight_fails_closed_on_inconsistent_scanner_inputs(
    tmp_path: Path,
    split_payload: object,
    result_payload: object,
    manifest_payload: object,
    expected_code: str,
) -> None:
    ledger = compile_fact_ledger(
        (
            (
                "scan:scan/jobs/default/JobA.json",
                _write(tmp_path / "job.bin", split_payload),
            ),
            (
                "scan:scan_result.json",
                _write(tmp_path / "result.bin", result_payload),
            ),
            (
                "scan:scan/manifest.json",
                _write(tmp_path / "manifest.bin", manifest_payload),
            ),
        )
    )

    report = validate_fact_ledger_preflight(ledger)

    assert report["status"] == "failed"
    assert expected_code in {issue["code"] for issue in report["issues"]}


def test_grounded_source_preflight_rejects_unredacted_secret_without_echoing_it(
    tmp_path: Path,
) -> None:
    secret_marker = "example-sensitive-marker"
    job = _write(
        tmp_path / "job.bin",
        {
            "name": "JobA",
            "components": [],
            "context": {"clientSecret": secret_marker},
        },
    )
    scan_result = _write(
        tmp_path / "result.bin",
        {
            "jobs": [{"name": "JobA", "components": []}],
            "stats": {"total_jobs": 1},
        },
    )
    manifest = _write(
        tmp_path / "manifest.bin",
        {"job_groups": {"JobA": "default"}},
    )
    receipt = _write(
        tmp_path / "receipt.bin",
        {"redaction_level": "strict", "total_redactions": 0, "summary": {}},
    )

    with pytest.raises(QuickCandidateError) as error:
        preflight_grounded_sources(
            (
                ("scan:scan/jobs/default/JobA.json", job),
                ("scan:scan_result.json", scan_result),
                ("scan:scan/manifest.json", manifest),
                ("scan:redaction_report.json", receipt),
            ),
            max_source_items=10,
            max_source_bytes=100_000,
        )

    assert "unredacted credentials" in str(error.value)
    assert secret_marker not in str(error.value)


def test_generic_text_preflight_rejects_credentials_without_echo(
    tmp_path: Path,
) -> None:
    marker = "do-not-echo-this-secret"
    source = tmp_path / "runtime.env"
    source.write_text(f"CLIENT_SECRET={marker}\n", encoding="utf-8")

    with pytest.raises(QuickCandidateError) as error:
        preflight_grounded_sources(
            (("upload:runtime.env", source),),
            max_source_items=10,
            max_source_bytes=100_000,
        )

    assert "unredacted credentials" in str(error.value)
    assert marker not in str(error.value)


def test_output_gate_rejects_sensitive_values_in_minified_json() -> None:
    marker = "do-not-echo-this-secret"

    with pytest.raises(QuickCandidateError) as error:
        reject_secret_like_output(
            {
                "candidate/orchestration/evidence.json": json.dumps(
                    {"claims": [], "clientSecret": marker}
                )
            }
        )

    assert "secret-safety" in str(error.value)
    assert marker not in str(error.value)


def test_output_gate_warns_on_documented_credential_references() -> None:
    warnings = reject_secret_like_output(
        {
            "candidate/intermediates/operations.md": (
                "output_row.Password: documented source field\n"
                "output_row.Password = input_row.Password;\n"
                "String encoded = globalMap.get(\"password\");\n"
                "SELECT Guid, Password, Salt FROM APIUSERS\n"
                "Credential propagation is reviewed in the scorecard.\n"
            )
        }
    )

    assert warnings
    assert all(item["severity"] == "warning" for item in warnings)
    assert {item["category"] for item in warnings} == {"credential_assignment"}


def test_claim_checker_rejects_counts_semantics_and_missing_units(
    tmp_path: Path,
) -> None:
    scan = _write(
        tmp_path / "scan_result.json",
        {"jobs": [{"name": "JobA", "group": "GroupA"}]},
    )
    ledger = compile_fact_ledger((("scan:scan_result.json", scan),))
    ledger["protected_facts"].extend(
        [
            {
                "fact_id": "component_type.tJava.count",
                "kind": "component_type_count",
                "subject": "tJava",
                "value": 2,
            },
            {
                "fact_id": "connection.PrimaryDb.job_count",
                "kind": "connection_job_count",
                "subject": "PrimaryDb",
                "value": 1,
            },
        ]
    )
    documents = {
        "00_AGENT_BRIEF.md": (
            "# Brief\n\nThe project contains exactly 3 jobs.\n\n"
            "NEED_AUTH=false bypasses downstream custom checks.\n"
        ),
        "02_COMPONENT_INVENTORY.md": (
            "# Inventory\n\n| Type | Count |\n|---|---:|\n| tJava | 7 |\n\n"
            "PrimaryDb is used by 4 jobs.\n"
        ),
    }

    report = validate_document_claims(documents, ledger)

    assert report["status"] == "repair_required"
    assert {issue["code"] for issue in report["issues"]} == {
        "protected_count_mismatch",
        "need_auth_semantics",
        "missing_canonical_units",
    }
    assert any(
        issue["subject"] == "project canonical units"
        and issue["claimed"] == 3
        and issue["expected"] == 1
        for issue in report["issues"]
    )
    assert any(
        issue["subject"] == "tJava"
        and issue["claimed"] == 7
        and issue["expected"] == 2
        for issue in report["issues"]
    )
    assert any(
        issue["subject"] == "PrimaryDb"
        and issue["claimed"] == 4
        and issue["expected"] == 1
        for issue in report["issues"]
    )


def test_claim_checker_accepts_grounded_counts_and_qualified_unknowns(
    tmp_path: Path,
) -> None:
    scan = _write(
        tmp_path / "scan_result.json",
        {"jobs": [{"name": "JobA", "group": "GroupA"}]},
    )
    ledger = compile_fact_ledger((("scan:scan_result.json", scan),))
    documents = {
        "00_AGENT_BRIEF.md": "# Brief\n\nThe project contains exactly 1 job.\n",
        "02_COMPONENT_INVENTORY.md": "# Inventory\n\nJobA is the canonical unit.\n",
        "06_OPERATIONS_TESTING_AND_DEBUGGING.md": (
            "# Operations\n\nNo scheduling metadata was found in the static scan.\n"
        ),
    }

    report = validate_document_claims(documents, ledger)

    assert report == {
        "schema_version": 1,
        "status": "pass",
        "issue_count": 0,
        "issues": [],
    }


def test_claim_checker_uses_exact_subjects_versions_edges_and_runtime_boundaries(
    tmp_path: Path,
) -> None:
    scan = _write(
        tmp_path / "scan_result.json",
        {
            "jobs": [
                {
                    "name": "GetLocation",
                    "group": "Api",
                    "version": "1.0",
                    "components": [{"component_type": "handler"}],
                },
                {
                    "name": "GetLocations",
                    "group": "Api",
                    "version": "2.0",
                    "components": [
                        {"component_type": "handler"},
                        {"component_type": "mapper"},
                    ],
                },
            ],
            "stats": {"total_jobs": 2},
        },
    )
    manifest = _write(
        tmp_path / "manifest.json",
        {
            "job_groups": {
                "GetLocation": "Api",
                "GetLocations": "Api",
            },
            "job_calls": {},
        },
    )
    ledger = compile_fact_ledger(
        (
            ("scan:scan_result.json", scan),
            ("scan:scan/manifest.json", manifest),
        )
    )

    report = validate_document_claims(
        {
            "02_COMPONENT_INVENTORY.md": (
                "# Inventory\n\n"
                "GetLocation has 1 component.\n"
                "GetLocations has 2 components and version 9.9.\n"
                "GetLocation calls GetLocations.\n"
            ),
            "07_QUALITY_SECURITY_RISK_SCORECARD.md": (
                "# Risk\n\nAny client can invoke the endpoint.\n"
            ),
        },
        ledger,
    )

    codes = {issue["code"] for issue in report["issues"]}
    assert "unit_version_mismatch" in codes
    assert "call_edge_mismatch" in codes
    assert "network_reachability_overclaim" in codes
    assert not any(
        issue["code"] == "protected_count_mismatch"
        and issue["subject"] == "GetLocation"
        for issue in report["issues"]
    )
