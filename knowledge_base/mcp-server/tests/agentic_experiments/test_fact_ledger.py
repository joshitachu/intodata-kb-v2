from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.experiments.application.fact_ledger import (
    compile_fact_ledger,
    validate_document_claims,
    validate_evidence_claims,
    validate_fact_ledger_preflight,
    validate_protected_document_facts,
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
        "jobs": 2,
        "routes": 0,
        "components": 4,
        "connections": 3,
        "source_files_present": None,
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


def test_fact_ledger_rejects_duplicate_normalized_scanner_paths(tmp_path: Path) -> None:
    trusted = _write(
        tmp_path / "trusted.json",
        {"name": "TrustedJob", "group": "Trusted", "components": []},
    )
    injected = _write(
        tmp_path / "injected.json",
        {"name": "InjectedJob", "group": "Injected", "components": []},
    )

    with pytest.raises(ValueError, match="unique after normalization"):
        compile_fact_ledger(
            (
                ("current-intodoc-scan:scan/jobs/Trusted.json", trusted),
                ("caller-controlled:scan/jobs/Trusted.json", injected),
            )
        )
    with pytest.raises(QuickCandidateError, match="collide after normalization"):
        preflight_grounded_sources(
            (
                ("current-intodoc-scan:scan/jobs/Trusted.json", trusted),
                ("caller-controlled:scan/jobs/Trusted.json", injected),
            ),
            max_source_items=10,
            max_source_bytes=1024,
        )


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


def test_fact_ledger_protects_talend_kinds_sources_connections_and_calls(
    tmp_path: Path,
) -> None:
    route = _write(
        tmp_path / "route.json",
        {
            "name": "OrdersRoute",
            "group": "Orders",
            "source_root": "WRAPPER/TALEND",
            "path": "Orders/OrdersRoute_0.1.item",
            "source_design": "routes",
            "is_esb_route": True,
            "components": [
                {
                    "component_type": "cTalendJob",
                    "params": [
                        {
                            "name": "SELECTED_JOB_NAME",
                            "value": "OrdersJob",
                        }
                    ],
                },
                {"component_type": "cJMS"},
            ],
            "connections": [{}, {}],
        },
    )
    job = _write(
        tmp_path / "job.json",
        {
            "name": "OrdersJob",
            "group": "Orders",
            "source_root": "WRAPPER/TALEND",
            "path": "Orders/OrdersJob_0.1.item",
            "source_design": "process",
            "is_esb_route": False,
            "components": [{"component_type": "tMap"}],
            "connections": [{}],
        },
    )
    files_index = _write(
        tmp_path / "files-index.json",
        [
            {"relative_path": "WRAPPER/TALEND/Orders/OrdersJob_0.1.item"},
        ],
    )
    meta = _write(
        tmp_path / "meta.json",
        {
            "platform": "talend",
            "stats": {
                "job_count": 1,
                "route_count": 1,
                "talend_roots": [
                    {
                        "id": "WRAPPER/TALEND",
                        "path": "WRAPPER/TALEND",
                        "label": "Orders",
                        "technical_label": "TALEND",
                        "role": "execution",
                    }
                ],
            },
        },
    )

    ledger = compile_fact_ledger(
        (
            ("scan:scan/jobs/Orders/OrdersRoute.json", route),
            ("scan:scan/jobs/Orders/OrdersJob.json", job),
            ("scan:scan/files_index.json", files_index),
            ("scan:scan/meta.json", meta),
        ),
        project_id="orders",
    )

    assert ledger["project"]["id"] == "orders"
    assert ledger["totals"]["jobs"] == 1
    assert ledger["totals"]["routes"] == 1
    assert ledger["totals"]["components"] == 3
    assert ledger["totals"]["connections"] == 3
    assert ledger["totals"]["source_files_present"] == 2
    by_name = {item["name"]: item for item in ledger["canonical_units"]}
    assert by_name["OrdersRoute"]["kind"] == "route"
    assert by_name["OrdersRoute"]["source_present"] is True
    assert by_name["OrdersRoute"]["source_presence_evidence"] == "canonical_scanner_unit"
    assert by_name["OrdersRoute"]["called_units"] == ["OrdersJob"]
    assert by_name["OrdersJob"]["kind"] == "job"
    assert ledger["call_graph"] == [
        {
            "caller": "OrdersRoute",
            "callee_count": 1,
            "callees": ["OrdersJob"],
            "source": "sources/scan/manifest.json",
        }
    ]

    document_report = validate_protected_document_facts(
        {
            "02_COMPONENT_INVENTORY.md": (
                "# Inventory\n\n"
                "The project comprises 2 Routes and 0 Jobs.\n\n"
                "## OrdersRoute\n\n"
                "| Source on disk | Not found |\n"
                "| Component count | 9 |\n"
                "| Connection count | 8 |\n"
            )
        },
        ledger,
    )
    assert document_report["status"] == "repair_required"
    assert {issue["code"] for issue in document_report["issues"]} == {
        "project_job_count_mismatch",
        "project_route_count_mismatch",
        "source_presence_mismatch",
        "unit_component_count_mismatch",
        "unit_connection_count_mismatch",
    }

    evidence_report = validate_evidence_claims(
        {
            "structure.json": {
                "canonical_units": [
                    {
                        "name": "OrdersRoute",
                        "kind": "job",
                        "component_count": 9,
                        "connection_count": 8,
                        "source_present": False,
                    }
                ],
                "missing_source_artifacts": ["OrdersJob"],
            }
        },
        ledger,
    )
    assert evidence_report["status"] == "repair_required"
    assert evidence_report["issue_count"] == 5


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


def test_grounded_source_preflight_warns_on_unredacted_secret_without_echoing_it(
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

    _, report = preflight_grounded_sources(
        (
            ("scan:scan/jobs/default/JobA.json", job),
            ("scan:scan_result.json", scan_result),
            ("scan:scan/manifest.json", manifest),
            ("scan:redaction_report.json", receipt),
        ),
        max_source_items=10,
        max_source_bytes=100_000,
    )

    assert report["status"] == "pass"
    assert report["secret_finding_count"] == 1
    assert report["secret_block_count"] == 0
    assert secret_marker not in json.dumps(report)


def test_generic_text_preflight_warns_on_credentials_without_echo(
    tmp_path: Path,
) -> None:
    marker = "do-not-echo-this-secret"
    source = tmp_path / "runtime.env"
    source.write_text(f"CLIENT_SECRET={marker}\n", encoding="utf-8")

    _, report = preflight_grounded_sources(
        (("upload:runtime.env", source),),
        max_source_items=10,
        max_source_bytes=100_000,
    )

    assert report["status"] == "pass"
    assert report["secret_finding_count"] == 1
    assert report["secret_block_count"] == 0
    assert marker not in json.dumps(report)


def test_grounded_source_preflight_records_ambiguous_sensitive_fields_as_warnings(
    tmp_path: Path,
) -> None:
    """Talend context names and explanatory values must not block a scan."""

    scan_result = _write(
        tmp_path / "result.bin",
        {
            "jobs": [{"name": "JobA", "components": []}],
            "context_groups": [
                {
                    "name": "database",
                    "variables": [
                        {
                            "name": "password",
                            "value": "redacted by the scanner policy",
                        }
                    ],
                }
            ],
        },
    )
    receipt = _write(
        tmp_path / "receipt.bin",
        {"redaction_level": "standard", "total_redactions": 1, "summary": {"x": 1}},
    )

    _, report = preflight_grounded_sources(
        (
            ("scan:scan_result.json", scan_result),
            ("scan:redaction_report.json", receipt),
        ),
        max_source_items=10,
        max_source_bytes=100_000,
    )

    assert report["status"] == "pass"
    assert report["secret_finding_count"] == 1
    assert report["secret_block_count"] == 0
    assert report["secret_warning_count"] == 1
    assert report["secret_warning_categories"] == ["named_sensitive_value"]


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


@pytest.mark.parametrize(
    "placeholder",
    [
        "[REDACTED]",
        "[PASSWORD_REDACTED]",
        "[API_KEY_REDACTED]",
        "[BEARER_TOKEN_REDACTED]",
    ],
)
def test_output_gate_allows_exact_redaction_placeholders(placeholder: str) -> None:
    assert (
        reject_secret_like_output(
            {
                "candidate/orchestration/evidence.json": json.dumps(
                    {"clientSecret": placeholder}
                )
            }
        )
        == ()
    )


def test_output_gate_rejects_modified_redaction_placeholder() -> None:
    with pytest.raises(QuickCandidateError, match="secret-safety"):
        reject_secret_like_output(
            {
                "candidate/orchestration/evidence.json": json.dumps(
                    {"clientSecret": "[PASSWORD_REDACTED]-actual"}
                )
            }
        )


def test_output_gate_ignores_colon_inside_evidence_citation_tag() -> None:
    """A prose sentence stating no credentials are exposed must not be blocked
    just because it cites its source via ``[evidence:HASH]`` -- the colon
    inside that citation tag is not a key:value credential assignment, even
    though the sentence discusses credentials to say none are present."""

    assert (
        reject_secret_like_output(
            {
                "candidate/docs/05_RUNTIME_CONFIG_AND_EXTERNALS.md": (
                    "- Deployed configuration values hidden or redacted from the "
                    "frozen scan (credential values, hosts, endpoints, DB targets, "
                    "queue names in use) "
                    "[evidence:01126f29f05c961dc362001d30fe85282262d59c7127776f4f87bcf9ebe359bf]."
                )
            }
        )
        == ()
    )


def test_output_gate_still_blocks_real_secret_next_to_a_citation_tag() -> None:
    marker = "hunter2xyz123"

    with pytest.raises(QuickCandidateError, match="secret-safety"):
        reject_secret_like_output(
            {
                "candidate/docs/05_RUNTIME_CONFIG_AND_EXTERNALS.md": (
                    f"db_password: {marker} [evidence:01126f29f05c961dc362001d30fe85282262d59c7127776f4f87bcf9ebe359bf]"
                )
            }
        )


def test_output_gate_warns_on_documented_credential_references() -> None:
    warnings = reject_secret_like_output(
        {
            "candidate/intermediates/operations.md": (
                "output_row.Password: documented source field\n"
                "output_row.Password = input_row.Password;\n"
                'String encoded = globalMap.get("password");\n'
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
        issue["subject"] == "tJava" and issue["claimed"] == 7 and issue["expected"] == 2
        for issue in report["issues"]
    )
    assert any(
        issue["subject"] == "PrimaryDb" and issue["claimed"] == 4 and issue["expected"] == 1
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
        issue["code"] == "protected_count_mismatch" and issue["subject"] == "GetLocation"
        for issue in report["issues"]
    )


def test_claim_checker_does_not_cross_attribute_a_second_examples_caller_or_count(
    tmp_path: Path,
) -> None:
    """A single citation sentence may bundle more than one caller/count example.

    Reproduces a verified run-51 false-positive shape: one sentence cites
    "<caller A> calls 6 units, <caller B> calls 6" as evidence and then
    separately states the real project-wide canonical unit total. The
    checker must not read caller B's name as a callee of caller A (the
    numbers after "calls" belong to a callee-count example, not a directed
    edge), and must not read the "6" from that callee-count example as a
    claim about the project-wide canonical unit count just because the word
    "canonical" appears later in the same line.
    """

    scan = _write(
        tmp_path / "scan_result.json",
        {
            "jobs": [
                {
                    "name": "AssetCheck_Main_Job",
                    "group": "AssetCheck",
                    "trunjob_refs": [{"child_job": "GetOrder"}],
                },
                {"name": "GetOrder", "group": "AssetCheck"},
                {"name": "VISE_SP_VIP_OTL", "group": "VISE"},
            ]
        },
    )
    ledger = compile_fact_ledger((("scan:scan_result.json", scan),))
    assert ledger["totals"]["canonical_units"] == 3

    bundled_sentence = (
        "Accepted structure packet documents the interface call structure as "
        "implemented-ledger truth (191 job_call_edges; e.g., `AssetCheck_Main_Job` "
        "calls 6 units, `VISE_SP_VIP_OTL` calls 6) and documents 3/3 canonical "
        "units source-present."
    )
    clean_report = validate_document_claims(
        {"00_AGENT_BRIEF.md": bundled_sentence}, ledger
    )
    assert clean_report == {
        "schema_version": 1,
        "status": "pass",
        "issue_count": 0,
        "issues": [],
    }

    # A real edge in the same bundled shape is still recognised as a match.
    real_edge_report = validate_document_claims(
        {
            "00_AGENT_BRIEF.md": (
                "`AssetCheck_Main_Job` calls GetOrder, `VISE_SP_VIP_OTL` calls nothing."
            )
        },
        ledger,
    )
    assert real_edge_report["issue_count"] == 0

    # Genuine mismatches in the same document must still be caught: a wrong
    # edge stated plainly, and a wrong project-wide canonical unit count.
    wrong_report = validate_document_claims(
        {
            "00_AGENT_BRIEF.md": (
                "AssetCheck_Main_Job calls VISE_SP_VIP_OTL directly.\n"
                "The project contains exactly 5 jobs.\n"
            )
        },
        ledger,
    )
    wrong_codes = {issue["code"] for issue in wrong_report["issues"]}
    assert "call_edge_mismatch" in wrong_codes
    assert any(
        issue["code"] == "call_edge_mismatch"
        and issue["subject"] == "AssetCheck_Main_Job"
        and issue["claimed"] == "VISE_SP_VIP_OTL"
        for issue in wrong_report["issues"]
    )
    assert any(
        issue["code"] == "protected_count_mismatch"
        and issue["subject"] == "project canonical units"
        and issue["claimed"] == 5
        and issue["expected"] == 3
        for issue in wrong_report["issues"]
    )


def test_claim_checker_treats_case_differing_group_names_as_distinct_subjects(
    tmp_path: Path,
) -> None:
    """Two real projects can have group names that differ only by case.

    Reproduces a verified false-positive shape where a case-insensitive
    subject match let ``AssetCheck2VISE`` and ``AssetCheck2Vise`` -- two
    distinct groups with distinct component counts -- collide, so one
    group's correct count was checked against the other's protected fact.
    """

    scan = _write(
        tmp_path / "scan_result.json",
        {
            "jobs": [
                {
                    "name": "RouteA",
                    "group": "AssetCheck2VISE",
                    "components": [{"component_type": "cRest"}, {"component_type": "cRest"}],
                },
                *(
                    {"name": f"RouteVise{i}", "group": "AssetCheck2VISE", "components": []}
                    for i in range(5)
                ),
                {
                    "name": "JobB",
                    "group": "AssetCheck2Vise",
                    "components": [{"component_type": "tJava"}],
                },
                *(
                    {"name": f"JobVise{i}", "group": "AssetCheck2Vise", "components": []}
                    for i in range(5)
                ),
            ]
        },
    )
    ledger = compile_fact_ledger((("scan:scan_result.json", scan),))
    facts = {
        (item["kind"], item["subject"]): item["value"]
        for item in ledger["protected_facts"]
    }
    assert facts[("group_unit_count", "AssetCheck2VISE")] == 6
    assert facts[("group_unit_count", "AssetCheck2Vise")] == 6
    assert facts[("group_component_count", "AssetCheck2VISE")] == 2
    assert facts[("group_component_count", "AssetCheck2Vise")] == 1

    clean_report = validate_document_claims(
        {
            "01_ARCHITECTURE_MAP.md": (
                "Interface-area groups adjacent to the AssetCheck process group: "
                "AssetCheck2VISE (6 routes / 2 components); "
                "AssetCheck2Vise (6 jobs / 1 components)."
            )
        },
        ledger,
    )
    assert clean_report == {
        "schema_version": 1,
        "status": "pass",
        "issue_count": 0,
        "issues": [],
    }

    wrong_report = validate_document_claims(
        {
            "01_ARCHITECTURE_MAP.md": (
                "Interface-area groups adjacent to the AssetCheck process group: "
                "AssetCheck2VISE (6 routes / 999 components); "
                "AssetCheck2Vise (6 jobs / 1 components)."
            )
        },
        ledger,
    )
    assert any(
        issue["code"] == "protected_count_mismatch"
        and issue["subject"] == "AssetCheck2VISE"
        and issue["claimed"] == 999
        and issue["expected"] == 2
        for issue in wrong_report["issues"]
    )
    assert not any(
        issue["subject"] == "AssetCheck2Vise" for issue in wrong_report["issues"]
    )
