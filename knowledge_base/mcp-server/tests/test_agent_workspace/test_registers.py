"""Deterministic client-facing registers for the independent agent lane."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.providers.agent_workspace.integration_model import (
    Integration,
    IntegrationKnowledgeBase,
    Journey,
    Participant,
)
from mcp_server.providers.agent_workspace.registers import (
    ReferenceRegisterError,
    compile_reference_registers,
)


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _split_mapping_accounting(workspace: Path) -> None:
    path = workspace / "scan" / "mapping_accounting.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    midpoint = max(1, len(rows) // 2)
    parts = path.with_suffix("")
    _write(parts / "part_001.json", rows[:midpoint])
    _write(parts / "part_002.json", rows[midpoint:])
    _write(
        path,
        {
            "split": True,
            "row_count": len(rows),
            "parts": [
                "mapping_accounting/part_001.json",
                "mapping_accounting/part_002.json",
            ],
        },
    )


def _workspace(root: Path) -> Path:
    workspace = root / "workspace"
    (workspace / "output").mkdir(parents=True)
    _write(
        workspace / "facts" / "units.json",
        {
            "units": {
                "OrderLoad": {
                    "display_name": "Order Load",
                    "kind": "job",
                    "role": "orchestrator",
                    "group": "orders",
                    "version": "1.0",
                    "component_count": 3,
                    "entry_point": True,
                    "callees": ["OrderWrite"],
                    "callers": [],
                    "error_handling_constructs": ["REJECT"],
                    "scan_quality": "complete",
                    "scan_path": "scan/jobs/orders/OrderLoad.json",
                },
                "OrderWrite": {
                    "display_name": "Order Write",
                    "kind": "job",
                    "role": "worker",
                    "group": "orders",
                    "version": "1.0",
                    "component_count": 2,
                    "entry_point": False,
                    "callees": [],
                    "callers": ["OrderLoad"],
                    "error_handling_constructs": [],
                    "scan_quality": "complete",
                    "workspace_path": "_workspace/orders.item",
                },
            }
        },
    )
    _write(
        workspace / "facts" / "dependencies.json",
        {
            "edges": [
                {
                    "source": "OrderLoad",
                    "target": "OrderWrite",
                    "declared_target": "OrderWrite",
                    "relation_type": "job_calls_job",
                    "resolution": "repository_id",
                    "component_name": "tRunJob_1",
                    "reference_source": "scan/jobs/orders/OrderLoad.json",
                }
            ],
            "unresolved": [
                {
                    "source": "OrderLoad",
                    "target": "",
                    "declared_target": "ArchiveMissing",
                    "relation_type": "job_calls_job",
                    "resolution": "unresolved",
                }
            ],
            "roots": ["OrderLoad"],
            "leaves": ["OrderWrite"],
            "routine_links": {"OrderWrite": [{"routine": "NormalizeOrder"}]},
        },
    )
    _write(
        workspace / "facts" / "interfaces.json",
        {
            "by_unit": {
                "OrderLoad": [
                    {
                        "kind": "channel",
                        "type": "jms",
                        "direction": "inbound",
                        "component_name": "cJMS_1",
                        "channel": "orders.in",
                    },
                    {
                        "kind": "endpoint",
                        "type": "rest",
                        "direction": "outbound",
                        "component_name": "tRESTClient_1",
                        "descriptor": {"url": "https://user:secret@example.test/orders"},
                    },
                ],
                "OrderWrite": [],
            },
            "channels": [
                {
                    "artifact": "OrderLoad",
                    "direction": "input",
                    "channel_type": "jms",
                    "component_name": "cJMS_1",
                    "channel": "orders.in",
                    "dynamic": False,
                }
            ],
        },
    )
    _write(
        workspace / "facts" / "configuration.json",
        {
            "context_groups": [
                {
                    "key": "Orders",
                    "variables": [
                        {"name": "mode", "type": "String", "values": {"DEV": "strict"}},
                        {
                            "name": "clientSecret",
                            "type": "Password",
                            "values": {"DEV": "DO_NOT_LEAK"},
                        },
                        {
                            "name": "endpoint",
                            "type": "String",
                            "values": {"DEV": "https://user:password@example.test"},
                        },
                    ],
                    "used_by": ["OrderLoad"],
                }
            ],
            "inline_contexts": {},
            "repository_connections": [
                {
                    "name": "OrdersDb",
                    "connection_type": "database",
                    "used_by": ["OrderWrite"],
                    "source_root": "scan/internal",
                }
            ],
            "unresolved_context_refs": {"OrderLoad": [{"name": "missing"}]},
        },
    )
    _write(
        workspace / "scan" / "mapping_accounting.json",
        [
            {
                "table_id": "table-b",
                "unit_id": "OrderWrite",
                "component_name": "tMap_2",
                "mapper_kind": "tmap",
                "offered_fields": 1,
                "written_fields": 1,
                "mapped_from_input": 1,
                "constant_or_generated": 0,
                "lookup_derived": 0,
                "dynamic_unresolved": 0,
                "rows": [
                    {
                        "output_table": "out",
                        "output_field": "status",
                        "expression": "row.status == null ? 'NEW' : row.status",
                        "classification": "expression",
                        "source_refs": ["row.status"],
                        "dynamic_refs": [],
                    }
                ],
                "unwritten_offered": [],
            },
            {
                "table_id": "table-a",
                "unit_id": "OrderLoad",
                "component_name": "tMap_1",
                "mapper_kind": "tmap",
                "offered_fields": 2,
                "written_fields": 3,
                "mapped_from_input": 2,
                "constant_or_generated": 0,
                "lookup_derived": 0,
                "dynamic_unresolved": 0,
                "rows": [
                    {
                        "output_table": "out",
                        "output_field": "orderId",
                        "expression": "row.id",
                        "classification": "direct_map",
                        "source_refs": ["row.id"],
                        "dynamic_refs": [],
                    },
                    {
                        "output_table": "out",
                        "output_field": "amount",
                        "expression": "row.amount * 100",
                        "classification": "expression",
                        "source_refs": ["row.amount"],
                        "dynamic_refs": [],
                    },
                ],
                "unwritten_offered": ["row.debug"],
            },
        ],
    )
    _write(
        workspace / "scan" / "capability_ledger.json",
        {
            "constructs": [
                {
                    "category": "mapping",
                    "construct": "tMap",
                    "status": "supported",
                    "count": 2,
                    "note": "",
                },
                {
                    "category": "schema",
                    "construct": "dynamic",
                    "status": "dynamic",
                    "count": 1,
                    "note": "See facts/internal.json",
                },
            ]
        },
    )
    _write(
        workspace / "scan" / "inventory.json",
        [
            {"path": "PROJECT/a.item", "kind": "job", "outcome": "parsed", "reason": "supported"},
            {
                "path": "PROJECT/x.bin",
                "kind": "unknown",
                "outcome": "unsupported",
                "reason": "unknown_type",
            },
        ],
    )
    _write(
        workspace / "runtime" / "evidence.json",
        {
            "schema_version": 1,
            "window_days": 90,
            "mode": "data",
            "requested_units": 2,
            "batches": 1,
            "failed_batches": 0,
            "partial": False,
            "scope_mismatch": False,
            "by_unit": {
                "OrderLoad": {
                    "job_name": "OrderLoad",
                    "environments": ["PROD"],
                    "total_runs": 25,
                    "success_runs": 23,
                    "failed_runs": 2,
                    "unfinished_runs": 0,
                    "failure_rate": 0.08,
                    "current_failure_streak": 0,
                    "first_run_at": "2026-01-01T00:00:00Z",
                    "last_run_at": "2026-03-31T00:00:00Z",
                    "runs_per_day": 0.28,
                    "p50_duration_ms": 1200,
                    "p95_duration_ms": 3100,
                    "max_duration_ms": 4900,
                    "plans": ["nightly"],
                    "recent_failures": [
                        {
                            "failed_at": "2026-03-14T00:00:00Z",
                            "error_summary": "The target rejected an invalid status.",
                            "occurrences": 2,
                        }
                    ],
                    "incident_insights": [],
                }
            },
            "unmatched_units": ["OrderWrite"],
            "unretrieved_units": [],
            "orphan_runtime_jobs": [],
            "alias_conflicts": [],
            "redaction_flags": [],
        },
    )
    parser_note = workspace / "facts" / "parser_limitations.md"
    parser_note.write_text(
        "**2 custom-code body/bodies were withheld from inside the parsed components and routines of 1 staged artifact(s).**\n",
        encoding="utf-8",
    )
    return workspace


class TestCompleteRegisters:
    def test_writes_complete_reconciled_reference_shelf(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)

        receipt = compile_reference_registers(workspace, mapping_part_bytes=1024)

        assert receipt.unit_count == 2
        assert receipt.dependency_count == 1
        assert receipt.unresolved_dependency_count == 1
        assert receipt.interface_count == 2
        assert receipt.channel_count == 1
        assert receipt.configuration_variable_count == 3
        assert receipt.mapping_table_count == 2
        assert receipt.mapping_row_count == 3
        assert receipt.runtime_unit_count == 1
        assert len(receipt.reconciliation_warnings) == 1
        reference = workspace / "output" / "reference"
        assert (reference / "README.md").is_file()
        assert (reference / "mappings.md").is_file()
        assert (reference / "runtime.md").is_file()
        assert len(list(reference.glob("mappings_part_*.md"))) == 2

    def test_split_mapping_accounting_remains_complete(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        _split_mapping_accounting(workspace)

        receipt = compile_reference_registers(workspace, mapping_part_bytes=1024)

        assert receipt.mapping_table_count == 2
        assert receipt.mapping_row_count == 3

    def test_split_inventory_remains_complete(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        inventory_path = workspace / "scan" / "inventory.json"
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        _write(workspace / "scan" / "inventory" / "part_001.json", inventory[:1])
        _write(workspace / "scan" / "inventory" / "part_002.json", inventory[1:])
        _write(
            inventory_path,
            {
                "split": True,
                "row_count": len(inventory),
                "parts": ["inventory/part_001.json", "inventory/part_002.json"],
            },
        )

        compile_reference_registers(workspace)

        limitations = (workspace / "output" / "reference" / "limitations.md").read_text()
        assert "unknown_type" in limitations

    def test_every_exact_mapping_row_is_published_once(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        compile_reference_registers(workspace, mapping_part_bytes=1024)
        text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((workspace / "output" / "reference").glob("mappings_part_*.md"))
        )

        exact_rows = [line for line in text.splitlines() if line.startswith(("| 1 |", "| 2 |"))]
        assert len(exact_rows) == 3
        assert sum("| orderId | row.id |" in line for line in exact_rows) == 1
        assert sum("| amount | row.amount * 100 |" in line for line in exact_rows) == 1
        assert (
            sum(
                "| status | row.status == null ? 'NEW' : row.status |" in line
                for line in exact_rows
            )
            == 1
        )
        assert text.count("## Mapping table ") == 2

    def test_outputs_are_deterministic_and_remove_stale_parts(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        compile_reference_registers(workspace, mapping_part_bytes=1024)
        reference = workspace / "output" / "reference"
        first = {path.name: path.read_bytes() for path in reference.iterdir()}
        (reference / "mappings_part_999.md").write_text("stale")

        compile_reference_registers(workspace, mapping_part_bytes=1024)

        second = {path.name: path.read_bytes() for path in reference.iterdir()}
        assert first == second
        assert "mappings_part_999.md" not in second

    def test_catalogue_is_compiled_from_the_shared_integration_model(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        IntegrationKnowledgeBase(
            integrations=[
                Integration(
                    id="integration:orders",
                    label="Order exchange",
                    state="exact",
                    capability="Orders",
                    participants=[
                        Participant(id="participant:a", label="ERP", state="exact"),
                        Participant(id="participant:b", label="CRM", state="exact"),
                    ],
                    journeys=[
                        Journey(
                            id="journey:orders",
                            label="Publish orders",
                            state="exact",
                            purpose="Makes accepted orders available to CRM.",
                            participant_ids=["participant:a", "participant:b"],
                        )
                    ],
                )
            ]
        ).save(workspace / "notes" / "integration_model.json")

        receipt = compile_reference_registers(workspace)
        catalogue = (workspace / "output" / "reference" / "integrations.md").read_text()

        assert receipt.integration_count == 1
        assert receipt.journey_count == 1
        assert "Order exchange" in catalogue
        assert "ERP -&gt; CRM" in catalogue

    def test_registers_cover_rows_not_only_names(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        compile_reference_registers(workspace)
        reference = workspace / "output" / "reference"

        assert "OrderLoad" in (reference / "implementing_units.md").read_text()
        assert "ArchiveMissing" in (reference / "dependencies.md").read_text()
        interfaces = (reference / "interfaces_and_channels.md").read_text()
        assert "orders.in" in interfaces
        assert "configured details withheld" in interfaces
        runtime = (reference / "runtime.md").read_text()
        assert "OrderLoad" in runtime
        assert "The target rejected an invalid status." in runtime
        assert "OrderWrite" in runtime
        limitations = (reference / "limitations.md").read_text()
        assert "unknown_type" in limitations
        assert "excluded for 2 block(s)" in limitations
        assert "declares 3 written field(s) but carries 2 row(s)" in limitations


class TestSafetyBoundary:
    def test_secrets_descriptors_and_internal_paths_are_not_published(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        compile_reference_registers(workspace)
        published = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (workspace / "output" / "reference").glob("*.md")
        )

        for forbidden in (
            "DO_NOT_LEAK",
            "user:password",
            "user:secret",
            "scan/jobs",
            "scan/internal",
            "facts/internal",
            "_workspace",
        ):
            assert forbidden not in published
        assert "clientSecret" in published
        assert "redacted" in published
        assert "strict" not in published
        assert "https://" not in published
        assert "DEV=configured" in published
        assert "DO_NOT_LEAK" not in published

    def test_refuses_output_outside_workspace_output(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        with pytest.raises(ReferenceRegisterError, match="workspace/output"):
            compile_reference_registers(workspace, output_dir=tmp_path / "escape")

    def test_refuses_a_mapping_without_stable_id(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        _write(workspace / "scan" / "mapping_accounting.json", [{"rows": []}])
        with pytest.raises(ReferenceRegisterError, match="stable table_id"):
            compile_reference_registers(workspace)

    def test_withholds_a_constant_credential_mapping_value(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        tables = json.loads((workspace / "scan" / "mapping_accounting.json").read_text())
        tables[0]["rows"].append(
            {
                "output_table": "out",
                "output_field": "apiToken",
                "expression": "literal-token-that-must-not-be-published",
                "classification": "constant",
                "source_refs": [],
                "dynamic_refs": [],
            }
        )
        tables[0]["written_fields"] = 2
        _write(workspace / "scan" / "mapping_accounting.json", tables)

        compile_reference_registers(workspace)
        published = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (workspace / "output" / "reference").glob("mappings_part_*.md")
        )
        assert "literal-token-that-must-not-be-published" not in published
        assert "[credential value withheld]" in published

    def test_refuses_malformed_rows_instead_of_silently_dropping_them(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        tables = json.loads((workspace / "scan" / "mapping_accounting.json").read_text())
        tables[0]["rows"].append("not a row")
        _write(workspace / "scan" / "mapping_accounting.json", tables)

        with pytest.raises(ReferenceRegisterError, match="malformed row"):
            compile_reference_registers(workspace)
