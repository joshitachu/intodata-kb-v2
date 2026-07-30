from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib

import pytest
from starlette.testclient import TestClient

from mcp_server.experiments.domain.evidence import EvidenceItem, EvidenceLocator, Subject
from mcp_server.experiments.domain.sources import (
    RedactionState,
    SourceSnapshot,
    SourceSnapshotContent,
    SourceSnapshotItem,
)
from mcp_server.experiments.infrastructure.mcp_transport import (
    TaskServerRegistry,
    TransportAuthorizationDenied,
    create_experiment_mcp_transport,
)
from mcp_server.experiments.mcp_tools.artifact_tools import AllowedArtifact, TaskArtifactWriter
from mcp_server.experiments.mcp_tools.auth import TaskTokenIssuer, TaskTokenVerifier
from mcp_server.experiments.mcp_tools.budgets import ToolBudgetLimits
from mcp_server.experiments.mcp_tools.config_compiler import NEXUS_EXPERIMENT_TOOLS
from mcp_server.experiments.mcp_tools.evidence_tools import ImmutableTaskEvidence, TaskContractView
from mcp_server.experiments.mcp_tools.server import ExperimentTaskToolServer


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
KEY = b"s5-transport-unit-test-key"


def _server(tmp_path, *, task_id: str) -> ExperimentTaskToolServer:
    content = b"approved immutable transport evidence"
    digest = hashlib.sha256(content).hexdigest()
    source_item = SourceSnapshotItem(
        id=f"source-{task_id}",
        source_descriptor_id="descriptor-1",
        logical_identity=f"{task_id}.txt",
        source_kind="reference_document",
        source_role="implementation",
        capabilities=frozenset({"searchable_text"}),
        locator=f"{task_id}.txt",
        raw_sha256=digest,
        extracted_sha256=digest,
        media_type="text/plain",
        size_bytes=len(content),
        extracted_size_bytes=len(content),
        trust_level="trusted",
        redaction_state=RedactionState.APPROVED,
        adapter_id="test",
        adapter_version="1",
    )
    snapshot = SourceSnapshot.build(
        snapshot_id=f"snapshot-{task_id}",
        items=(source_item,),
        contents=(SourceSnapshotContent(source_item.id, content, digest, "text/plain", len(content)),),
    )
    evidence = EvidenceItem(
        id=f"evidence-{task_id}",
        source_snapshot_item_id=source_item.id,
        kind="note",
        locator=EvidenceLocator(source_item.id, source_item.locator, digest),
        subject_ids=("subject-a",),
    )
    context = ImmutableTaskEvidence(
        project_id="project-a",
        experiment_id="experiment-a",
        task_id=task_id,
        snapshot=snapshot,
        evidence_items=(evidence,),
        subjects=(Subject("subject-a", "generic.unit", "A"),),
        relationships=(),
        contract=TaskContractView(
            task_id=task_id,
            output_names=frozenset({"answer.md"}),
            allowed_tools=frozenset(NEXUS_EXPERIMENT_TOOLS),
        ),
    )
    output_root = tmp_path / task_id
    output_root.mkdir()
    return ExperimentTaskToolServer.for_task(
        context=context,
        writer=TaskArtifactWriter(
            root=output_root,
            allowed_artifacts=(AllowedArtifact("answer.md", "text/markdown", 128),),
        ),
        verifier=TaskTokenVerifier(keys={"v1": KEY}),
        limits=ToolBudgetLimits(max_calls=8, max_read_bytes=128, max_result_bytes=4096, max_write_bytes=128),
    )


def _token(*, task_id: str) -> str:
    return TaskTokenIssuer(key_version="v1", signing_key=KEY).issue(
        project_id="project-a",
        experiment_id="experiment-a",
        task_id=task_id,
        source_roles=frozenset({"implementation"}),
        subject_ids=frozenset({"subject-a"}),
        allowed_tools=frozenset(NEXUS_EXPERIMENT_TOOLS),
        budgets={"max_calls": 8, "max_read_bytes": 128, "max_result_bytes": 4096, "max_write_bytes": 128},
        now=NOW,
        ttl=timedelta(minutes=5),
    )


def _transport(*servers: ExperimentTaskToolServer):
    registry = TaskServerRegistry(TaskTokenVerifier(keys={"v1": KEY}))
    for server in servers:
        registry.register(server)
    return create_experiment_mcp_transport(registry)


def test_streamable_fastmcp_exposes_only_exact_native_tool_schemas(tmp_path):
    transport = _transport(_server(tmp_path, task_id="task-a"))
    tools = asyncio.run(transport.mcp.list_tools())

    assert transport.mcp.settings.json_response is True
    assert transport.mcp.settings.stateless_http is True
    assert transport.mcp.settings.streamable_http_path == "/"
    assert {tool.name for tool in tools} == set(NEXUS_EXPERIMENT_TOOLS)
    assert {
        tool.name: {
            "properties": {key: value["type"] for key, value in tool.inputSchema["properties"].items()},
            "required": tool.inputSchema["required"],
            "type": tool.inputSchema["type"],
        }
        for tool in tools
    } == {
        "nexus_experiment_get_contract": {"properties": {"operation_id": "string"}, "required": ["operation_id"], "type": "object"},
        "nexus_experiment_list_evidence": {"properties": {"operation_id": "string"}, "required": ["operation_id"], "type": "object"},
        "nexus_experiment_search_evidence": {"properties": {"query": "string", "operation_id": "string"}, "required": ["query", "operation_id"], "type": "object"},
        "nexus_experiment_read_evidence": {"properties": {"evidence_id": "string", "max_bytes": "integer", "operation_id": "string"}, "required": ["evidence_id", "max_bytes", "operation_id"], "type": "object"},
        "nexus_experiment_traverse_relationships": {"properties": {"subject_id": "string", "operation_id": "string"}, "required": ["subject_id", "operation_id"], "type": "object"},
        "nexus_experiment_write_artifact": {"properties": {"logical_name": "string", "media_type": "string", "content": "string", "operation_id": "string"}, "required": ["logical_name", "media_type", "content", "operation_id"], "type": "object"},
        "nexus_experiment_submit_task": {"properties": {"operation_id": "string"}, "required": ["operation_id"], "type": "object"},
    }


def test_transport_denies_missing_bearer_and_cross_task_registry_routing(tmp_path):
    server_a = _server(tmp_path, task_id="task-a")
    transport = _transport(server_a)

    with pytest.raises(TransportAuthorizationDenied, match="missing bearer"):
        transport.invoke(
            authorization=None,
            tool_name="nexus_experiment_get_contract",
            request={},
            operation_id="missing-token",
            now=NOW,
        )
    with pytest.raises(TransportAuthorizationDenied, match="no registered task server"):
        transport.invoke(
            authorization=f"Bearer {_token(task_id='task-b')}",
            tool_name="nexus_experiment_get_contract",
            request={},
            operation_id="cross-task",
            now=NOW,
        )


def test_authorized_transport_contract_write_and_submit_flow(tmp_path):
    transport = _transport(_server(tmp_path, task_id="task-a"))
    authorization = f"Bearer {_token(task_id='task-a')}"

    contract = transport.invoke(
        authorization=authorization,
        tool_name="nexus_experiment_get_contract",
        request={},
        operation_id="contract",
        now=NOW,
    )
    artifact = transport.invoke(
        authorization=authorization,
        tool_name="nexus_experiment_write_artifact",
        request={"logical_name": "answer.md", "media_type": "text/markdown", "content": "completed"},
        operation_id="artifact",
        now=NOW,
    )
    submitted = transport.invoke(
        authorization=authorization,
        tool_name="nexus_experiment_submit_task",
        request={},
        operation_id="submit",
        now=NOW,
    )

    assert contract["task_id"] == "task-a"
    assert artifact["logical_name"] == "answer.md"
    assert submitted == {"status": "submitted", "task_id": "task-a"}


def test_streamable_http_tool_call_extracts_the_bearer_from_request_context(tmp_path):
    transport = _transport(_server(tmp_path, task_id="task-a"))
    issued_at = datetime.now(timezone.utc)
    token = TaskTokenIssuer(key_version="v1", signing_key=KEY).issue(
        project_id="project-a",
        experiment_id="experiment-a",
        task_id="task-a",
        source_roles=frozenset({"implementation"}),
        subject_ids=frozenset({"subject-a"}),
        allowed_tools=frozenset(NEXUS_EXPERIMENT_TOOLS),
        budgets={"max_calls": 8, "max_read_bytes": 128, "max_result_bytes": 4096, "max_write_bytes": 128},
        now=issued_at,
        ttl=timedelta(minutes=5),
    )
    headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Host": "127.0.0.1:8000",
    }
    with TestClient(transport.asgi_app()) as client:
        initialized = client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "transport-test", "version": "1"},
                },
            },
        )
        response = client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "nexus_experiment_get_contract",
                    "arguments": {"operation_id": "http-contract"},
                },
            },
        )

    assert initialized.status_code == 200
    assert response.status_code == 200
    assert response.json()["result"]["structuredContent"]["task_id"] == "task-a"
