from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from mcp_server.experiments.application.grounded_pack import (
    GroundedExecutionPack,
    load_grounded_execution_pack,
)
from mcp_server.experiments.application.run_quick_candidate import (
    QuickCandidateError,
)
from mcp_server.experiments.application.run_staged_grounded_candidate import (
    GroundedOpenCodeRunner,
)
from mcp_server.experiments.infrastructure.opencode.client import (
    OpenCodeHttpClient,
)
from mcp_server.experiments.infrastructure.recipes.loader import load_recipe


def _pack() -> GroundedExecutionPack:
    root = Path(__file__).parents[2] / "data" / "agentic_recipes"
    return load_grounded_execution_pack(
        load_recipe(root / "logistics_operations" / "recipe.yaml")
    )


def _complete_message() -> list[dict[str, object]]:
    return [
        {
            "info": {
                "role": "assistant",
                "time": {"completed": 1},
                "tokens": {"input": 10, "output": 5},
                "cost": 0.001,
            },
            "parts": [
                {"type": "tool", "tool": "read"},
                {"type": "step-finish", "reason": "stop"},
            ],
        }
    ]


def _write_role_outputs(
    root: Path,
    role_id: str,
    *,
    unsafe_evidence: bool = False,
) -> None:
    if role_id == "shipment-evidence":
        path = root / "candidate/orchestration/evidence_packets/shipments.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            (
                '{"claims":[],"clientSecret":"unsafe-test-marker"}'
                if unsafe_evidence
                else '{"claims":[]}'
            ),
            encoding="utf-8",
        )
    elif role_id == "operations-analysis":
        path = root / "candidate/intermediates/operations_analysis.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Analysis\n\nGrounded static analysis.\n", encoding="utf-8")
    elif role_id == "operations-writer":
        for name, title in (
            ("operations_runbook.md", "Operations"),
            ("exception_inventory.md", "Exceptions"),
        ):
            path = root / "candidate/docs" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"# {title}\n\nGrounded in the frozen source set.\n",
                encoding="utf-8",
            )
    elif role_id == "operations-reviewer":
        path = root / "candidate/orchestration/review_report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(
                (
                    "## Factual review",
                    "Passed.",
                    "## Coverage review",
                    "Passed.",
                    "## Cross-document consistency",
                    "Passed.",
                    "## Secret and evidence-path review",
                    "Passed.",
                    "## Resolved findings",
                    "None.",
                    "## Final verdict",
                    "Pass.",
                )
            ),
            encoding="utf-8",
        )


def _client(
    handler: httpx.MockTransport,
) -> tuple[OpenCodeHttpClient, httpx.AsyncClient]:
    http_client = httpx.AsyncClient(transport=handler)
    return (
        OpenCodeHttpClient(
            base_url="http://opencode.test",
            username="opencode",
            password="test-password",
            client=http_client,
        ),
        http_client,
    )


@pytest.mark.asyncio
async def test_staged_grounded_contains_packets_and_dispatches_pack_roles(
    tmp_path: Path,
) -> None:
    experiment_id = "00000000-0000-4000-8000-000000000201"
    workspace_root = tmp_path / "workspaces"
    source = tmp_path / "shipment-events.txt"
    source.write_text("shipment A arrived\n", encoding="utf-8")
    sessions: dict[str, str] = {}
    prompts: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/session":
            directory = parse_qs(request.url.query.decode())["directory"][0]
            role_id = directory.rsplit("/", 1)[-1]
            session_id = f"session-{role_id}"
            sessions[session_id] = role_id
            return httpx.Response(200, json={"id": session_id})
        if request.url.path.endswith("/prompt_async"):
            session_id = request.url.path.split("/")[2]
            role_id = sessions[session_id]
            prompts.append(json.loads(request.content))
            _write_role_outputs(
                workspace_root / experiment_id / "roles" / role_id,
                role_id,
            )
            return httpx.Response(204)
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=_complete_message())
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    client, http_client = _client(httpx.MockTransport(handler))
    result = await GroundedOpenCodeRunner(
        client=client,
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=workspace_root,
        execution_pack=_pack(),
        timeout_seconds=2,
    ).run(
        project_id="logistics",
        experiment_id=experiment_id,
        source_items=(("upload:events/shipment-events.txt", source),),
        provider_id="provider-a",
        model_id="model-b",
        output_names=("operations_runbook.md", "exception_inventory.md"),
    )
    await http_client.aclose()

    assert len(prompts) == 4
    assert all(prompt["tools"]["task"] is False for prompt in prompts)
    assert all(
        prompt["model"] == {"providerID": "provider-a", "modelID": "model-b"}
        for prompt in prompts
    )
    roles_root = workspace_root / experiment_id / "roles"
    assert (
        roles_root
        / "shipment-evidence"
        / "input"
        / "sources"
        / "events"
        / "shipment-events.txt"
    ).is_file()
    assert not list(
        (roles_root / "operations-analysis" / "input").rglob(
            "shipment-events.txt"
        )
    )
    assert (
        roles_root
        / "operations-analysis"
        / "input"
        / "accepted"
        / "candidate"
        / "orchestration"
        / "evidence_packets"
        / "shipments.json"
    ).is_file()
    receipt = next(
        artifact
        for artifact in result.artifacts
        if artifact.logical_name == "receipts/grounded_execution.json"
    )
    payload = json.loads(
        (
            tmp_path
            / "output"
            / "logistics"
            / "experiments"
            / experiment_id
            / receipt.relative_path
        ).read_text(encoding="utf-8")
    )
    assert payload["execution_mode"] == "grounded-nexus-staged-pack"
    assert len(payload["role_sessions"]) == 4


@pytest.mark.asyncio
async def test_staged_grounded_never_exposes_unsafe_intermediate_downstream(
    tmp_path: Path,
) -> None:
    experiment_id = "00000000-0000-4000-8000-000000000202"
    workspace_root = tmp_path / "workspaces"
    source = tmp_path / "safe.txt"
    source.write_text("safe source\n", encoding="utf-8")
    pack = replace(_pack(), repair_limit=0)
    created_roles: list[str] = []
    sessions: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/session":
            directory = parse_qs(request.url.query.decode())["directory"][0]
            role_id = directory.rsplit("/", 1)[-1]
            created_roles.append(role_id)
            sessions[f"session-{role_id}"] = role_id
            return httpx.Response(200, json={"id": f"session-{role_id}"})
        if request.url.path.endswith("/prompt_async"):
            session_id = request.url.path.split("/")[2]
            role_id = sessions[session_id]
            _write_role_outputs(
                workspace_root / experiment_id / "roles" / role_id,
                role_id,
                unsafe_evidence=True,
            )
            return httpx.Response(204)
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=_complete_message())
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    client, http_client = _client(httpx.MockTransport(handler))
    with pytest.raises(QuickCandidateError, match="secret-safety"):
        await GroundedOpenCodeRunner(
            client=client,
            output_root=tmp_path / "output",
            project_root=tmp_path,
            workspace_root=workspace_root,
            execution_pack=pack,
            timeout_seconds=2,
        ).run(
            project_id="logistics",
            experiment_id=experiment_id,
            source_items=(("upload:safe.txt", source),),
            provider_id="provider-a",
            model_id="model-b",
            output_names=("operations_runbook.md", "exception_inventory.md"),
        )
    await http_client.aclose()

    assert created_roles == ["shipment-evidence"]
    downstream = (
        workspace_root
        / experiment_id
        / "roles"
        / "operations-analysis"
        / "input"
        / "accepted"
    )
    assert not downstream.exists()


@pytest.mark.asyncio
async def test_staged_grounded_enforces_physical_role_packet_byte_budget(
    tmp_path: Path,
) -> None:
    experiment_id = "00000000-0000-4000-8000-000000000203"
    source = tmp_path / "large.txt"
    source.write_text("x" * 1024, encoding="utf-8")
    pack = _pack()
    evidence = replace(pack.roles[0], max_context_bytes=32)
    constrained = replace(pack, roles=(evidence, *pack.roles[1:]))
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        raise AssertionError(request.url.path)

    client, http_client = _client(httpx.MockTransport(handler))
    with pytest.raises(QuickCandidateError, match="physical role packet budget"):
        await GroundedOpenCodeRunner(
            client=client,
            output_root=tmp_path / "output",
            project_root=tmp_path,
            workspace_root=tmp_path / "workspaces",
            execution_pack=constrained,
            timeout_seconds=2,
        ).run(
            project_id="logistics",
            experiment_id=experiment_id,
            source_items=(("upload:large.txt", source),),
            provider_id="provider-a",
            model_id="model-b",
            output_names=("operations_runbook.md", "exception_inventory.md"),
        )
    await http_client.aclose()

    assert requests == ["/global/health"]


@pytest.mark.asyncio
async def test_staged_grounded_retries_only_the_rejected_role_within_pack_limit(
    tmp_path: Path,
) -> None:
    experiment_id = "00000000-0000-4000-8000-000000000204"
    workspace_root = tmp_path / "workspaces"
    source = tmp_path / "safe.txt"
    source.write_text("safe source\n", encoding="utf-8")
    sessions: dict[str, str] = {}
    prompt_roles: list[str] = []
    create_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_count
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/session":
            create_count += 1
            directory = parse_qs(request.url.query.decode())["directory"][0]
            role_id = directory.rsplit("/", 1)[-1]
            session_id = f"session-{create_count}"
            sessions[session_id] = role_id
            return httpx.Response(200, json={"id": session_id})
        if request.url.path.endswith("/prompt_async"):
            session_id = request.url.path.split("/")[2]
            role_id = sessions[session_id]
            prompt_roles.append(role_id)
            # The first evidence attempt is deliberately incomplete. Nexus
            # retries only that isolated role; no dependent can start yet.
            if not (
                role_id == "shipment-evidence"
                and prompt_roles.count(role_id) == 1
            ):
                _write_role_outputs(
                    workspace_root / experiment_id / "roles" / role_id,
                    role_id,
                )
            return httpx.Response(204)
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=_complete_message())
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    client, http_client = _client(httpx.MockTransport(handler))
    await GroundedOpenCodeRunner(
        client=client,
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=workspace_root,
        execution_pack=_pack(),
        timeout_seconds=2,
    ).run(
        project_id="logistics",
        experiment_id=experiment_id,
        source_items=(("upload:safe.txt", source),),
        provider_id="provider-a",
        model_id="model-b",
        output_names=("operations_runbook.md", "exception_inventory.md"),
    )
    await http_client.aclose()

    assert prompt_roles == [
        "shipment-evidence",
        "shipment-evidence",
        "operations-analysis",
        "operations-writer",
        "operations-reviewer",
    ]
