from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mcp_server.connectors.broker import BrokerDenied, CapabilityBroker
from mcp_server.connectors.grants import CapabilityGrant, CapabilityGrantStore, ConnectorBudget, GrantDenied
from mcp_server.connectors.mcp_facade import (
    FacadeBridge,
    FacadeInputError,
    RunScopedMcpFacade,
)
from mcp_server.connectors.planner import plan_external_evidence_tasks
from mcp_server.connectors.reconciliation import ReconciliationError, reconcile_findings
from mcp_server.providers.custom.models import FactsManifest, UnitFact


CAPABILITIES = {
    "runtime.events.aggregate": {"read_only": True, "phase_eligibility": ["gather"]},
    "runtime.events.search": {"read_only": True, "phase_eligibility": ["gather"]},
    "runtime.events.write": {"read_only": False, "phase_eligibility": ["gather"]},
}


def _grant() -> CapabilityGrant:
    return CapabilityGrant.new(
        run_id=42,
        project_id="project-a",
        user_id="u1",
        connector_instance_ids={"con_1"},
        source_capabilities={"src_1": {"runtime.events.aggregate", "runtime.events.search"}},
        capability_ids={"runtime.events.aggregate", "runtime.events.search"},
        budget=ConnectorBudget(max_calls=2, max_items=5, max_response_bytes=10_000, max_evidence_bytes=20_000),
    )


async def _executor(grant, capability_id, source_id, request):
    return {
        "evidence_id": "ev_runtime_1",
        "run_id": grant.run_id,
        "project_id": grant.project_id,
        "connector_instance_id": "con_1",
        "source_id": source_id,
        "capability_id": capability_id,
        "question": request.get("question", "describe"),
        "items": [],
        "summary": {"count": 3},
        "limitations": [],
        "digest": "sha256:" + "a" * 64,
    }


@pytest.mark.asyncio
async def test_broker_requires_trusted_metadata_and_enforces_scope_and_nonce():
    grants = CapabilityGrantStore()
    grant = _grant()
    token = grants.issue(grant)
    broker = CapabilityBroker(grants=grants, capability_metadata=CAPABILITIES, executor=_executor)

    result = await broker.execute(
        token, facade_nonce="facade-one", run_id=42, project_id="project-a", phase="gather",
        capability_id="runtime.events.aggregate", source_id="src_1",
        request={"question": "How often does UnitA fail?", "time_window": {"from": "a", "to": "b"}},
    )
    assert result["evidence_id"] == "ev_runtime_1"
    assert broker.receipts[0].status == "accepted"
    assert "question" in broker.receipts[0].query_summary

    with pytest.raises(BrokerDenied, match="nonce replayed"):
        await broker.execute(
            token, facade_nonce="facade-two", run_id=42, project_id="project-a", phase="gather",
            capability_id="runtime.events.search", source_id="src_1", request={},
        )
    with pytest.raises(BrokerDenied):
        await broker.execute(
            token, facade_nonce="facade-one", run_id=42, project_id="project-a", phase="analyze",
            capability_id="runtime.events.search", source_id="src_1", request={},
        )


@pytest.mark.asyncio
async def test_mcp_facade_exposes_semantic_tools_only_and_rejects_raw_dsl():
    grants = CapabilityGrantStore()
    grant = _grant()
    token = grants.issue(grant)
    broker = CapabilityBroker(grants=grants, capability_metadata=CAPABILITIES, executor=_executor)
    facade = RunScopedMcpFacade(broker=broker, token=token, run_id=42, project_id="project-a")

    assert [tool.name for tool in facade.list_tools()] == [
        "aggregate_runtime_events", "search_runtime_events"
    ]
    with pytest.raises(FacadeInputError, match="unsupported semantic input"):
        await facade.call("aggregate_runtime_events", {"source_id": "src_1", "dsl": {"match_all": {}}})
    with pytest.raises(BrokerDenied):
        await facade.call("get_evidence_item", {"evidence_ref": "evref_unissued"})


@pytest.mark.asyncio
async def test_facade_bridge_keeps_grant_in_parent_and_exposes_only_scoped_tools(tmp_path: Path):
    grants = CapabilityGrantStore()
    grant = _grant()
    token = grants.issue(grant)
    broker = CapabilityBroker(grants=grants, capability_metadata=CAPABILITIES, executor=_executor)
    facade = RunScopedMcpFacade(broker=broker, token=token, run_id=42, project_id="project-a")
    bridge = FacadeBridge(facade, tmp_path / "connector.sock")

    class _Writer:
        payload = b""

        def write(self, value):
            self.payload += value

        async def drain(self):
            return None

        def close(self):
            return None

        async def wait_closed(self):
            return None

    async def call(payload):
        reader = asyncio.StreamReader()
        reader.feed_data(json.dumps(payload).encode() + b"\n")
        reader.feed_eof()
        writer = _Writer()
        await bridge._handle(reader, writer)
        return json.loads(writer.payload)

    listing = await call({"token": token, "method": "list_tools"})
    assert [tool["name"] for tool in listing["tools"]] == ["aggregate_runtime_events", "search_runtime_events"]
    result = await call({
        "token": token, "method": "call", "name": "aggregate_runtime_events",
        "arguments": {"source_id": "src_1", "question": "UnitA failures", "time_window": {"from": "2026-01-01T00:00:00Z", "to": "2026-01-02T00:00:00Z"}},
    })
    assert result["result"]["evidence_id"] == "ev_runtime_1"


def test_grant_revoke_and_budget_fail_closed():
    store = CapabilityGrantStore()
    grant = _grant()
    token = store.issue(grant)
    store.revoke(grant.grant_id)
    with pytest.raises(GrantDenied, match="revoked"):
        store.get(token)


def test_planner_uses_code_owned_entity_ids_and_bounds_tasks(tmp_path: Path):
    facts = FactsManifest(
        project_id="project-a", platform="generic", canonical_unit_label="units", unit_count=2,
        groups={"A": 1, "B": 1},
        units=[UnitFact(name="UnitA", group="A", path="a.py", unit_type="code"), UnitFact(name="UnitB", group="B", path="b.py", unit_type="code")],
    )
    (tmp_path / "facts_manifest.json").write_text("{}")
    tasks = plan_external_evidence_tasks(
        staging_dir=tmp_path, facts=facts, preprocess_groups=["A", "B"],
        capabilities=["runtime.events.aggregate"], max_tasks=1,
    )
    assert len(tasks) == 1
    assert tasks[0].entity_refs == ("unit:UnitA",)
    assert "aggregate" in tasks[0].prompt()


def test_reconciliation_rejects_unknown_entities_evidence_and_negative_claims():
    issued = {
        "ev_1": {"run_id": 42, "project_id": "project-a", "items": [{"x": 1}], "summary": {}, "limitations": []}
    }
    valid = {
        "findings": [{"evidence_id": "ev_1", "entity_refs": ["unit:UnitA"], "classification": "corroborates", "statement": "Observed UnitA timeout events.", "explanation": "Aggregate count was non-zero."}],
        "limitations": [],
    }
    accepted, _ = reconcile_findings(valid, issued=issued, canonical_entity_refs={"unit:UnitA"})
    assert accepted[0].classification == "corroborates"

    invalid = {**valid, "findings": [{**valid["findings"][0], "evidence_id": "ev_other"}]}
    with pytest.raises(ReconciliationError, match="unissued"):
        reconcile_findings(invalid, issued=issued, canonical_entity_refs={"unit:UnitA"})
    negative = {**valid, "findings": [{**valid["findings"][0], "statement": "No UnitA events exist."}]}
    with pytest.raises(ReconciliationError, match="negative"):
        reconcile_findings(negative, issued=issued, canonical_entity_refs={"unit:UnitA"})
