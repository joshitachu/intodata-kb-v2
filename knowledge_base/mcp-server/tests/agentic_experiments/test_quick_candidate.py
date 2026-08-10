from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from mcp_server.experiments.application.run_agentic_legacy_candidate import (
    AgenticLegacyOpenCodeRunner,
    _INTERMEDIATE_OUTPUTS,
)
from mcp_server.experiments.application.grounded_pack import (
    load_grounded_execution_pack,
)
from mcp_server.experiments.application.run_legacy_replay_candidate import (
    LegacyReplayOpenCodeRunner,
    _REPLAY_TASKS,
)
from mcp_server.experiments.application.run_orchestrated_candidate import (
    GroundedOpenCodeRunner,
    OrchestratedOpenCodeRunner,
    _read_orchestration_outputs,
    _reject_reproduced_secrets,
)
from mcp_server.experiments.application.run_quick_candidate import (
    QuickCandidateError,
    QuickOpenCodeCandidateRunner,
    _has_completed_assistant,
    _usage,
)
from mcp_server.experiments.infrastructure.opencode.client import OpenCodeHttpClient
from mcp_server.experiments.infrastructure.recipes.loader import load_recipe


def _grounded_pack():
    root = Path(__file__).parents[2] / "data" / "agentic_recipes"
    return load_grounded_execution_pack(
        load_recipe(root / "alitalend_legacy_equivalence" / "recipe.yaml")
    )


def _write_pack_artifacts(
    root: Path,
    *,
    missing: str | None = None,
) -> None:
    for relative in _grounded_pack().required_artifacts:
        if relative == missing:
            continue
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}" if path.suffix == ".json" else "# Complete\n", encoding="utf-8")


def _write_legacy_replay_fixture(project_root: Path, project_id: str) -> None:
    docs = project_root / "output" / project_id / "docs"
    investigation = docs / "_investigation"
    investigation.mkdir(parents=True)
    (investigation / "_summary.md").write_text(
        "# Investigation Summary\n\nDirect path: Phase 1 skipped for small project.\n",
        encoding="utf-8",
    )
    for index, task in enumerate(_REPLAY_TASKS, start=1):
        debug = docs / "_debug" / "model_inputs" / task.phase / task.directory_name
        ledger = docs / "_model_inputs" / task.phase / task.directory_name
        debug.mkdir(parents=True)
        ledger.mkdir(parents=True)
        prompt = f"EXACT PROMPT {index}: {task.directory_name}"
        context = '{"jobs":[{"name":"JobA"}]}'
        context_path = "evidence/scan_result.json"
        (debug / "prompt.md").write_text(prompt + "\n", encoding="utf-8")
        (debug / "context_bundle.md").write_text(
            f"# Model Input Bundle: {task.phase} / {task.directory_name}\n\n"
            f"## Prompt\n\n{prompt}\n\n"
            f"## Context Files\n\n### 1. {context_path}\n\n{context}\n",
            encoding="utf-8",
        )
        (debug / "request.json").write_text(
            json.dumps(
                {
                    "model": (
                        "deepseek-v4-flash"
                        if task.directory_name == "005_AssetCheck_Interface"
                        else "deepseek-v4-pro"
                    ),
                    "max_tokens": 32000,
                    "temperature": 0.1,
                }
            ),
            encoding="utf-8",
        )
        (ledger / "manifest.json").write_text(
            json.dumps(
                {
                    "context_files": [
                        {
                            "index": 1,
                            "path": context_path,
                            "bytes": len(context.encode("utf-8")),
                            "exists": True,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )


@pytest.mark.asyncio
async def test_agentic_legacy_candidate_uses_one_tool_enabled_session(
    tmp_path: Path,
) -> None:
    project_id = "alitalend"
    experiment_id = "00000000-0000-4000-8000-000000000125"
    project_root = tmp_path / "project-root"
    workspace_root = tmp_path / "workspaces"
    _write_legacy_replay_fixture(project_root, project_id)
    scan = tmp_path / "scan_result.json"
    redaction = tmp_path / "redaction_report.json"
    scan.write_text('{"jobs":[{"name":"JobA"}]}', encoding="utf-8")
    redaction.write_text('{"approved":true}', encoding="utf-8")
    prompt_payloads: list[dict[str, object]] = []
    create_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_count
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True, "version": "1.18.4"})
        if request.url.path == "/session":
            create_count += 1
            assert request.url.params["directory"] == (
                f"/runtime/workspaces/{experiment_id}"
            )
            return httpx.Response(200, json={"id": "agentic-session-1"})
        if request.url.path.endswith("/prompt_async"):
            prompt_payloads.append(json.loads(request.content))
            candidate = workspace_root / experiment_id / "candidate"
            for task in _REPLAY_TASKS:
                if task.output_name:
                    path = candidate / "docs" / task.output_name
                    path.write_text(
                        f"# {task.output_name}\n\nSubstantive evidence.",
                        encoding="utf-8",
                    )
            for relative_name in _INTERMEDIATE_OUTPUTS:
                path = candidate / "intermediates" / relative_name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    f"# {relative_name}\n\nSubstantive analysis.",
                    encoding="utf-8",
                )
            return httpx.Response(204)
        if request.url.path.endswith("/message"):
            return httpx.Response(
                200,
                json=[
                    {
                        "info": {
                            "role": "assistant",
                            "time": {"completed": 1},
                            "tokens": {"input": 100, "output": 10},
                            "cost": 0.001,
                        },
                        "parts": [
                            {"type": "tool", "tool": "grep"},
                            {"type": "step-finish", "reason": "tool-calls"},
                        ],
                    },
                    {
                        "info": {
                            "role": "assistant",
                            "time": {"completed": 2},
                            "tokens": {"input": 200, "output": 20},
                            "cost": 0.002,
                        },
                        "parts": [
                            {"type": "text", "text": "All artifacts reviewed."},
                            {"type": "step-finish", "reason": "stop"},
                        ],
                    },
                ],
            )
        raise AssertionError(f"unexpected OpenCode request: {request.url.path}")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await AgenticLegacyOpenCodeRunner(
        client=OpenCodeHttpClient(
            base_url="http://opencode.test",
            username="opencode",
            password="test-only-password",
            client=http_client,
        ),
        output_root=project_root / "output",
        project_root=project_root,
        workspace_root=workspace_root,
        timeout_seconds=2,
    ).run(
        project_id=project_id,
        experiment_id=experiment_id,
        source_items=(
            ("current-intodoc-scan:scan_result.json", scan),
            ("current-intodoc-scan:redaction_report.json", redaction),
        ),
        provider_id="deepseek",
        model_id="deepseek-v4-pro",
        output_names=tuple(
            task.output_name for task in _REPLAY_TASKS if task.output_name is not None
        ),
    )
    await http_client.aclose()

    assert create_count == 1
    assert len(prompt_payloads) == 1
    assert prompt_payloads[0]["model"] == {
        "providerID": "deepseek",
        "modelID": "deepseek-v4-pro",
    }
    assert prompt_payloads[0]["tools"] == {
        "read": True,
        "glob": True,
        "grep": True,
        "edit": True,
        "write": True,
    }
    orchestration_prompt = str(prompt_payloads[0]["parts"]).casefold()
    assert "glob, grep, and read" in orchestration_prompt
    assert "ten final documents" in orchestration_prompt
    workspace = workspace_root / experiment_id
    assert (workspace / "sources" / "scan_result.json").is_file()
    assert (
        workspace
        / "legacy_workflow"
        / "phase2"
        / "005_AssetCheck_Interface"
        / "prompt.md"
    ).read_text(encoding="utf-8").startswith("EXACT PROMPT 1")
    assert result.input_tokens == 300
    assert result.output_tokens == 30
    assert sum(item.kind == "document" for item in result.artifacts) == 10
    assert sum(item.kind == "analysis" for item in result.artifacts) == 3
    receipt_path = (
        project_root
        / "output"
        / project_id
        / "experiments"
        / experiment_id
        / "candidate"
        / "receipts"
        / "agentic_execution.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["execution_mode"] == "single-session-agentic-legacy-equivalence"
    assert receipt["tool_summary"] == {
        "total_calls": 1,
        "by_tool": {"grep": 1},
    }
    assert len(receipt["input_receipts"]) == 54


@pytest.mark.asyncio
async def test_orchestrated_candidate_requires_and_receipts_child_sessions(
    tmp_path: Path,
) -> None:
    project_id = "alitalend"
    experiment_id = "00000000-0000-4000-8000-000000000126"
    project_root = tmp_path / "project-root"
    workspace_root = tmp_path / "workspaces"
    _write_legacy_replay_fixture(project_root, project_id)
    scan = tmp_path / "scan_result.json"
    scan.write_text('{"jobs":[{"name":"JobA"}]}', encoding="utf-8")
    prompt_payloads: list[dict[str, object]] = []
    output_names = tuple(
        task.output_name for task in _REPLAY_TASKS if task.output_name is not None
    )

    def complete_message(*, input_tokens: int, output_tokens: int) -> list[dict]:
        return [
            {
                "info": {
                    "role": "assistant",
                    "time": {"completed": 1},
                    "tokens": {"input": input_tokens, "output": output_tokens},
                    "cost": 0.001,
                },
                "parts": [
                    {"type": "text", "text": "Completed."},
                    {"type": "step-finish", "reason": "stop"},
                ],
            }
        ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True, "version": "1.18.4"})
        if request.url.path == "/session":
            return httpx.Response(200, json={"id": "orchestrator-session"})
        if request.url.path.endswith("/prompt_async"):
            prompt_payloads.append(json.loads(request.content))
            candidate = workspace_root / experiment_id / "candidate"
            for task in _REPLAY_TASKS:
                if task.output_name:
                    (candidate / "docs" / task.output_name).write_text(
                        f"# {task.output_name}\n\nEvidence-grounded document.",
                        encoding="utf-8",
                    )
            for relative_name in _INTERMEDIATE_OUTPUTS:
                path = candidate / "intermediates" / relative_name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    f"# {relative_name}\n\nEvidence-grounded analysis.",
                    encoding="utf-8",
                )
            orchestration = candidate / "orchestration"
            (orchestration / "execution_plan.json").write_text(
                json.dumps(
                    {
                        "source_types": ["intodoc_scan"],
                        "canonical_units": ["JobA"],
                        "partitions": [{"units": ["JobA"]}],
                        "agent_assignments": [{"agent": "nexus-doc-evidence"}],
                        "document_owners": {
                            name: f"writer-{index}"
                            for index, name in enumerate(output_names, start=1)
                        },
                        "acceptance_checks": ["all outputs"],
                    }
                ),
                encoding="utf-8",
            )
            (orchestration / "evidence_registry.json").write_text(
                json.dumps(
                    {
                        "claims": [
                            {
                                "claim_id": "claim-1",
                                "statement": "JobA exists",
                                "scope": ["JobA"],
                                "evidence_locators": ["sources/scan_result.json"],
                                "confidence": "confirmed",
                                "contradictions": [],
                                "used_by_documents": list(output_names),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (orchestration / "coverage_ledger.json").write_text(
                json.dumps(
                    {
                        "canonical_units": ["JobA"],
                        "concerns": ["architecture"],
                        "document_coverage": {
                            name: ["JobA"] for name in output_names
                        },
                        "gaps": [],
                    }
                ),
                encoding="utf-8",
            )
            (orchestration / "review_report.md").write_text(
                "# Final review\n\n"
                "## Factual review\n\nPass.\n\n"
                "## Coverage review\n\nPass.\n\n"
                "## Cross-document consistency\n\nPass.\n\n"
                "## Secret and evidence-path review\n\nPass.\n\n"
                "## Resolved findings\n\nAll resolved.\n\n"
                "## Final verdict\n\nPass.\n",
                encoding="utf-8",
            )
            return httpx.Response(204)
        if request.url.path == "/session/orchestrator-session/children":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": f"child-{index}",
                        "parentID": "orchestrator-session",
                        "title": f"specialist {index}",
                    }
                    for index in range(1, 4)
                ],
            )
        if request.url.path == "/session/orchestrator-session/message":
            message = complete_message(input_tokens=100, output_tokens=20)[0]
            message["parts"].insert(0, {"type": "tool", "tool": "task"})
            return httpx.Response(200, json=[message])
        if request.url.path.startswith("/session/child-") and request.url.path.endswith(
            "/message"
        ):
            return httpx.Response(
                200,
                json=complete_message(input_tokens=10, output_tokens=5),
            )
        raise AssertionError(f"unexpected OpenCode request: {request.url.path}")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await OrchestratedOpenCodeRunner(
        client=OpenCodeHttpClient(
            base_url="http://opencode.test",
            username="opencode",
            password="test-only-password",
            client=http_client,
        ),
        output_root=project_root / "output",
        project_root=project_root,
        workspace_root=workspace_root,
        timeout_seconds=2,
    ).run(
        project_id=project_id,
        experiment_id=experiment_id,
        source_items=(("current-intodoc-scan:scan_result.json", scan),),
        provider_id="deepseek",
        model_id="deepseek-v4-pro",
        output_names=output_names,
    )
    await http_client.aclose()

    assert prompt_payloads[0]["agent"] == "nexus-doc-orchestrator"
    assert prompt_payloads[0]["tools"]["task"] is True
    assert result.input_tokens == 130
    assert result.output_tokens == 35
    assert sum(item.kind == "document" for item in result.artifacts) == 10
    assert sum(item.logical_name.startswith("orchestration/") for item in result.artifacts) == 4
    receipt_path = (
        project_root
        / "output"
        / project_id
        / "experiments"
        / experiment_id
        / "candidate"
        / "receipts"
        / "orchestrated_execution.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["execution_mode"] == (
        "multi-session-orchestrated-legacy-equivalence"
    )
    assert receipt["subagent_depth"] == 1
    assert len(receipt["child_sessions"]) == 3
    assert receipt["tool_summary"]["by_tool"]["task"] == 1


@pytest.mark.asyncio
async def test_grounded_candidate_repairs_facts_with_bounded_sessions(
    tmp_path: Path,
) -> None:
    project_id = "alitalend"
    experiment_id = "00000000-0000-4000-8000-000000000127"
    project_root = tmp_path / "project-root"
    workspace_root = tmp_path / "workspaces"
    _write_legacy_replay_fixture(project_root, project_id)
    scan = tmp_path / "scan_result.json"
    scan.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "name": "JobA",
                        "group": "GroupA",
                        "components": [],
                    }
                ],
                "stats": {"total_jobs": 1},
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"job_groups": {"JobA": "GroupA"}}),
        encoding="utf-8",
    )
    receipt = tmp_path / "redaction_report.json"
    receipt.write_text(
        json.dumps(
            {
                "redaction_level": "strict",
                "total_redactions": 0,
                "summary": {},
            }
        ),
        encoding="utf-8",
    )
    output_names = tuple(
        task.output_name for task in _REPLAY_TASKS if task.output_name is not None
    )
    prompt_payloads: list[dict[str, object]] = []

    def complete_message(input_tokens: int, output_tokens: int) -> dict:
        return {
            "info": {
                "role": "assistant",
                "time": {"completed": input_tokens},
                "tokens": {"input": input_tokens, "output": output_tokens},
                "cost": 0.001,
            },
            "parts": [
                {"type": "text", "text": "Completed."},
                {"type": "step-finish", "reason": "stop"},
            ],
        }

    def write_candidate(*, repaired: bool) -> None:
        candidate = workspace_root / experiment_id / "candidate"
        for task in _REPLAY_TASKS:
            if task.output_name:
                count = 1 if repaired else 2
                (candidate / "docs" / task.output_name).write_text(
                    f"# {task.output_name}\n\n"
                    f"JobA is documented. The project contains exactly {count} jobs.\n",
                    encoding="utf-8",
                )
        for relative_name in _INTERMEDIATE_OUTPUTS:
            path = candidate / "intermediates" / relative_name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"# {relative_name}\n\nEvidence-grounded analysis for JobA.",
                encoding="utf-8",
            )
        orchestration = candidate / "orchestration"
        (orchestration / "execution_plan.json").write_text(
            json.dumps(
                {
                    "source_types": {"intodoc_scan": ["scan_result.json"]},
                    "canonical_units": ["JobA"],
                    "partitions": [{"units": ["JobA"]}],
                    "agent_assignments": {
                        "evidence-a": "nexus-doc-evidence",
                        "evidence-b": "nexus-doc-evidence",
                        "analysis-a": "nexus-doc-analyst",
                        "analysis-b": "nexus-doc-analyst",
                        "writer-a": "nexus-doc-pack-writer",
                        "writer-b": "nexus-doc-pack-writer",
                        "review": "nexus-doc-reviewer",
                    },
                    "document_owners": {
                        name: ("writer-a" if index < 5 else "writer-b")
                        for index, name in enumerate(output_names)
                    },
                    "acceptance_checks": ["fact ledger", "all outputs"],
                }
            ),
            encoding="utf-8",
        )
        (orchestration / "evidence_registry.json").write_text(
            json.dumps(
                {
                    "claims": [
                        {
                            "claim_id": "claim-1",
                            "statement": "JobA exists",
                            "scope": ["JobA"],
                            "evidence_locators": ["sources/scan_result.json"],
                            "confidence": "confirmed",
                            "contradictions": [],
                            "used_by_documents": list(output_names),
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (orchestration / "coverage_ledger.json").write_text(
            json.dumps(
                {
                    "canonical_units": ["JobA"],
                    "concerns": ["architecture", "operations"],
                    "document_coverage": {
                        name: ["JobA"] for name in output_names
                    },
                    "gaps": [],
                }
            ),
            encoding="utf-8",
        )
        (orchestration / "review_report.md").write_text(
            "# Review\n\n"
            "## Factual review\n\nPass.\n\n"
            "## Coverage review\n\nPass.\n\n"
            "## Cross-document consistency\n\nPass.\n\n"
            "## Secret and evidence-path review\n\nPass.\n\n"
            "## Resolved findings\n\nFact repair recorded.\n\n"
            "## Final verdict\n\nPass.\n",
            encoding="utf-8",
        )
        evidence_packets = orchestration / "evidence_packets"
        evidence_packets.mkdir(exist_ok=True)
        (evidence_packets / "structure.json").write_text(
            json.dumps({"claims": []}),
            encoding="utf-8",
        )
        (evidence_packets / "runtime.json").write_text(
            json.dumps({"claims": []}),
            encoding="utf-8",
        )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True, "version": "1.18.4"})
        if request.url.path == "/session":
            return httpx.Response(200, json={"id": "grounded-orchestrator"})
        if request.url.path.endswith("/prompt_async"):
            payload = json.loads(request.content)
            prompt_payloads.append(payload)
            write_candidate(repaired=len(prompt_payloads) == 2)
            return httpx.Response(204)
        if request.url.path == "/session/grounded-orchestrator/children":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": f"grounded-child-{index}",
                        "parentID": "grounded-orchestrator",
                        "title": f"bounded specialist {index}",
                    }
                    for index in range(1, 8)
                ],
            )
        if request.url.path == "/session/grounded-orchestrator/message":
            messages = [complete_message(100, 20)]
            if len(prompt_payloads) > 1:
                messages.append(complete_message(30, 10))
            return httpx.Response(200, json=messages)
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        if request.url.path.startswith(
            "/session/grounded-child-"
        ) and request.url.path.endswith("/message"):
            return httpx.Response(200, json=[complete_message(10, 5)])
        raise AssertionError(f"unexpected OpenCode request: {request.url.path}")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await GroundedOpenCodeRunner(
        client=OpenCodeHttpClient(
            base_url="http://opencode.test",
            username="opencode",
            password="test-only-password",
            client=http_client,
        ),
        output_root=project_root / "output",
        project_root=project_root,
        workspace_root=workspace_root,
        timeout_seconds=2,
        execution_pack=_grounded_pack(),
    ).run(
        project_id=project_id,
        experiment_id=experiment_id,
        source_items=(
            ("current-intodoc-scan:scan_result.json", scan),
            ("current-intodoc-scan:scan/manifest.json", manifest),
            ("current-intodoc-scan:redaction_report.json", receipt),
        ),
        provider_id="deepseek",
        model_id="deepseek-v4-pro",
        output_names=output_names,
    )
    await http_client.aclose()

    assert len(prompt_payloads) == 2
    assert prompt_payloads[0]["agent"] == "nexus-doc-grounded-orchestrator"
    assert prompt_payloads[0]["tools"]["task"] is True
    assert prompt_payloads[1]["tools"]["task"] is False
    assert result.input_tokens == 200
    assert result.output_tokens == 65
    workspace = workspace_root / experiment_id
    assert (workspace / "FACT_LEDGER.json").is_file()
    claim_report = json.loads(
        (
            workspace
            / "candidate"
            / "orchestration"
            / "deterministic_claim_report.json"
        ).read_text(encoding="utf-8")
    )
    assert claim_report["status"] == "pass"
    assert sum(item.kind == "deterministic-fact-ledger" for item in result.artifacts) == 1
    assert sum(item.kind == "deterministic-claim-check" for item in result.artifacts) == 1
    receipt = json.loads(
        (
            project_root
            / "output"
            / project_id
            / "experiments"
            / experiment_id
            / "candidate"
            / "receipts"
            / "grounded_execution.json"
        ).read_text(encoding="utf-8")
    )
    assert receipt["execution_mode"] == (
        "grounded-lean-orchestrated-legacy-equivalence"
    )
    assert len(receipt["child_sessions"]) == 7
    assert receipt["deterministic_claim_check"]["status"] == "pass"


@pytest.mark.asyncio
async def test_grounded_preflight_failure_creates_no_opencode_session_or_prompt(
    tmp_path: Path,
) -> None:
    scan = tmp_path / "unsafe.bin"
    scan.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "name": "JobA",
                        "components": [],
                        "context": {"clientSecret": "unsafe-test-marker"},
                    }
                ],
                "stats": {"total_jobs": 1},
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.bin"
    manifest.write_text(
        json.dumps({"job_groups": {"JobA": "default"}}),
        encoding="utf-8",
    )
    receipt = tmp_path / "receipt.bin"
    receipt.write_text(
        json.dumps(
            {
                "redaction_level": "strict",
                "total_redactions": 0,
                "summary": {},
            }
        ),
        encoding="utf-8",
    )
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(500)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    runner = GroundedOpenCodeRunner(
        client=OpenCodeHttpClient(
            base_url="http://opencode.test",
            username="opencode",
            password="test-only-password",
            client=http_client,
        ),
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=tmp_path / "workspaces",
        timeout_seconds=1,
        execution_pack=_grounded_pack(),
    )

    with pytest.raises(
        QuickCandidateError,
        match="preflight rejected",
    ):
        await runner.run(
            project_id="example",
            experiment_id="00000000-0000-4000-8000-000000000128",
            source_items=(
                ("scan:scan_result.json", scan),
                ("scan:scan/manifest.json", manifest),
                ("scan:redaction_report.json", receipt),
            ),
            provider_id="provider-a",
            model_id="model-b",
            output_names=tuple(
                task.output_name
                for task in _REPLAY_TASKS
                if task.output_name is not None
            ),
        )
    await http_client.aclose()

    assert requests == []


@pytest.mark.asyncio
async def test_grounded_idle_parent_reconciles_complete_owned_artifacts(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    _write_pack_artifacts(workspace)
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/session/parent/message":
            return httpx.Response(200, json=[])
        if request.url.path == "/session/parent/children":
            return httpx.Response(
                200,
                json=[
                    {"id": f"child-{index}", "parentID": "parent"}
                    for index in range(7)
                ],
            )
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    runner = GroundedOpenCodeRunner(
        client=OpenCodeHttpClient(
            base_url="http://opencode.test",
            username="opencode",
            password="password",
            client=http_client,
        ),
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=tmp_path / "workspaces",
        timeout_seconds=1,
        execution_pack=_grounded_pack(),
    )

    messages, children = await runner._wait_for_grounded_artifacts(
        "parent",
        workspace=workspace,
        provider_id="provider-a",
        model_id="model-b",
        experiment_id="experiment-a",
    )
    await http_client.aclose()

    assert messages == []
    assert len(children) == 7
    assert not any(path.endswith("/prompt_async") for path in requests)


@pytest.mark.asyncio
async def test_grounded_missing_artifact_retry_is_targeted_and_bounded(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    missing = "candidate/docs/09_EXECUTIVE_SUMMARY.md"
    _write_pack_artifacts(workspace, missing=missing)
    prompts: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/session/parent/message":
            return httpx.Response(200, json=[])
        if request.url.path == "/session/parent/children":
            return httpx.Response(
                200,
                json=[
                    {"id": f"child-{index}", "parentID": "parent"}
                    for index in range(7)
                ],
            )
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        if request.url.path == "/session/parent/prompt_async":
            prompts.append(json.loads(request.content))
            path = workspace / missing
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Recovered executive summary\n", encoding="utf-8")
            return httpx.Response(204)
        raise AssertionError(request.url.path)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    runner = GroundedOpenCodeRunner(
        client=OpenCodeHttpClient(
            base_url="http://opencode.test",
            username="opencode",
            password="password",
            client=http_client,
        ),
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=tmp_path / "workspaces",
        timeout_seconds=2,
        execution_pack=_grounded_pack(),
    )

    await runner._wait_for_grounded_artifacts(
        "parent",
        workspace=workspace,
        provider_id="provider-a",
        model_id="model-b",
        experiment_id="experiment-a",
    )
    await http_client.aclose()

    assert len(prompts) == 1
    assert prompts[0]["tools"]["task"] is False
    prompt_text = prompts[0]["parts"][0]["text"]
    assert missing in prompt_text
    assert "writer-operations" in prompt_text


def test_orchestrated_candidate_rejects_reproduced_sensitive_values(
    tmp_path: Path,
) -> None:
    scan = tmp_path / "scan_result.json"
    scan.write_text(
        json.dumps(
            {
                "context_groups": [
                    {
                        "variables": [
                            {
                                "name": "ServicePassword",
                                "values": {"PROD": "test-secret-value"},
                            }
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        RuntimeError,
        match="reproduced a sensitive source value",
    ):
        _reject_reproduced_secrets(
            (("scan", scan),),
            {"doc.md": "Password: test-secret-value"},
            {},
            {},
        )


@pytest.mark.parametrize(
    "placeholder",
    [
        "[REDACTED]",
        "[PASSWORD_REDACTED]",
        "[API_KEY_REDACTED]",
        "[BEARER_TOKEN_REDACTED]",
    ],
)
def test_orchestrated_candidate_allows_redaction_placeholders(
    tmp_path: Path,
    placeholder: str,
) -> None:
    scan = tmp_path / "scan_result.json"
    scan.write_text(
        json.dumps(
            {
                "context_groups": [
                    {
                        "variables": [
                            {
                                "name": "ServicePassword",
                                "values": {"PROD": placeholder},
                            }
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    _reject_reproduced_secrets(
        (("scan", scan),),
        {"doc.md": f"Credential value: {placeholder}"},
        {},
        {},
    )


def test_orchestrated_candidate_rejects_modified_redaction_placeholder(
    tmp_path: Path,
) -> None:
    value = "[PASSWORD_REDACTED]-actual"
    scan = tmp_path / "scan_result.json"
    scan.write_text(
        json.dumps(
            {
                "context_groups": [
                    {
                        "variables": [
                            {
                                "name": "ServicePassword",
                                "values": {"PROD": value},
                            }
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="reproduced a sensitive source value"):
        _reject_reproduced_secrets(
            (("scan", scan),),
            {"doc.md": f"Credential value: {value}"},
            {},
            {},
        )


def test_grounded_orchestration_rejects_nonexistent_evidence_locator(
    tmp_path: Path,
) -> None:
    root = tmp_path / "candidate" / "orchestration"
    root.mkdir(parents=True)
    outputs = ("operations_runbook.md",)
    (root / "execution_plan.json").write_text(
        json.dumps(
            {
                "source_types": ["runtime_export"],
                "canonical_units": ["Shipment"],
                "partitions": ["all"],
                "agent_assignments": ["evidence"],
                "document_owners": {"operations_runbook.md": "writer"},
                "acceptance_checks": ["evidence"],
            }
        ),
        encoding="utf-8",
    )
    (root / "evidence_registry.json").write_text(
        json.dumps(
            {
                "claims": [
                    {
                        "claim_id": "shipment-1",
                        "statement": "Shipment exists",
                        "scope": ["Shipment"],
                        "evidence_locators": ["sources/missing.json"],
                        "confidence": "confirmed",
                        "contradictions": [],
                        "used_by_documents": ["operations_runbook.md"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (root / "coverage_ledger.json").write_text(
        json.dumps(
            {
                "canonical_units": ["Shipment"],
                "concerns": ["operations"],
                "document_coverage": {"operations_runbook.md": ["Shipment"]},
                "gaps": [],
            }
        ),
        encoding="utf-8",
    )
    (root / "review_report.md").write_text(
        "\n".join(
            f"## {section}\n\nPass."
            for section in (
                "Factual review",
                "Coverage review",
                "Cross-document consistency",
                "Secret and evidence-path review",
                "Resolved findings",
                "Final verdict",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        QuickCandidateError,
        match="locator does not exist",
    ):
        _read_orchestration_outputs(
            tmp_path,
            outputs,
            validate_evidence_locators=True,
        )


@pytest.mark.asyncio
async def test_legacy_replay_uses_exact_frozen_inputs_and_recorded_model_routes(
    tmp_path: Path,
) -> None:
    project_id = "alitalend"
    experiment_id = "00000000-0000-4000-8000-000000000124"
    project_root = tmp_path / "project-root"
    _write_legacy_replay_fixture(project_root, project_id)
    prompt_payloads: list[dict[str, object]] = []
    session_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal session_count
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True, "version": "1.18.4"})
        if request.url.path == "/session":
            session_count += 1
            return httpx.Response(200, json={"id": f"replay-{session_count}"})
        if request.url.path.endswith("/prompt_async"):
            prompt_payloads.append(json.loads(request.content))
            return httpx.Response(204)
        if request.url.path.endswith("/message"):
            session_id = request.url.path.split("/")[2]
            return httpx.Response(
                200,
                json=[
                    {
                        "info": {
                            "role": "assistant",
                            "time": {"completed": 1},
                            "tokens": {
                                "input": 100,
                                "output": 20,
                                "cache": {"read": 5},
                            },
                            "cost": 0.01,
                        },
                        "parts": [
                            {"type": "text", "text": f"# Output {session_id}\n\nEvidence."},
                            {"type": "step-finish", "reason": "stop"},
                        ],
                    }
                ],
            )
        raise AssertionError(f"unexpected OpenCode request: {request.url.path}")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await LegacyReplayOpenCodeRunner(
        client=OpenCodeHttpClient(
            base_url="http://opencode.test",
            username="opencode",
            password="test-only-password",
            client=http_client,
        ),
        output_root=project_root / "output",
        project_root=project_root,
        timeout_seconds=2,
    ).run(
        project_id=project_id,
        experiment_id=experiment_id,
        provider_id="deepseek",
        output_names=tuple(
            task.output_name for task in _REPLAY_TASKS if task.output_name is not None
        ),
    )
    await http_client.aclose()

    assert len(prompt_payloads) == 13
    assert prompt_payloads[0]["model"] == {
        "providerID": "deepseek",
        "modelID": "deepseek-v4-flash",
    }
    assert all(
        payload["model"]
        == {"providerID": "deepseek", "modelID": "deepseek-v4-pro"}
        for payload in prompt_payloads[1:]
    )
    assert all(payload["tools"] == {} for payload in prompt_payloads)
    first_prompt = prompt_payloads[0]["parts"][0]["text"]  # type: ignore[index]
    assert first_prompt.count("EXACT PROMPT 1") == 1
    assert "--- CONTEXT FILE: scan_result.json ---" in first_prompt
    assert '{"jobs":[{"name":"JobA"}]}' in first_prompt
    assert result.input_tokens == 1300
    assert result.output_tokens == 260
    assert result.cache_read_tokens == 65
    assert result.billed_cost_usd == "0.13"
    assert sum(artifact.kind == "document" for artifact in result.artifacts) == 10
    assert sum(artifact.kind == "analysis" for artifact in result.artifacts) == 3
    receipt = (
        project_root
        / "output"
        / project_id
        / "experiments"
        / experiment_id
        / "candidate"
        / "receipts"
        / "legacy_replay.json"
    )
    receipt_value = json.loads(receipt.read_text(encoding="utf-8"))
    assert receipt_value["phase1"]["status"] == "skipped"
    assert len(receipt_value["tasks"]) == 13
    assert receipt_value["tasks"][0]["prompt_sha256"]


@pytest.mark.asyncio
async def test_quick_candidate_uses_frozen_sources_and_only_writes_candidate_branch(
    tmp_path: Path,
) -> None:
    source_one = tmp_path / "scan_result.json"
    source_two = tmp_path / "redaction_report.json"
    source_one.write_text('{"jobs":[{"name":"JobA"}]}', encoding="utf-8")
    source_two.write_text('{"approved":true}', encoding="utf-8")
    output_names = ("00_AGENT_BRIEF.md", "09_EXECUTIVE_SUMMARY.md")
    prompt_payload: dict[str, object] = {}
    workspace_root = tmp_path / "workspaces"
    experiment_id = "00000000-0000-4000-8000-000000000123"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True, "version": "1.18.4"})
        if request.url.path == "/session":
            assert request.url.params["directory"] == f"/runtime/workspaces/{experiment_id}"
            return httpx.Response(200, json={"id": "session-quick-1"})
        if request.url.path == "/session/session-quick-1/prompt_async":
            import json

            prompt_payload.update(json.loads(request.content))
            docs = workspace_root / experiment_id / "candidate" / "docs"
            (docs / "00_AGENT_BRIEF.md").write_text("# Purpose\n\nBrief.", encoding="utf-8")
            (docs / "09_EXECUTIVE_SUMMARY.md").write_text(
                "# Executive Summary\n\nSummary.",
                encoding="utf-8",
            )
            return httpx.Response(204)
        if request.url.path == "/session/session-quick-1/message":
            return httpx.Response(
                200,
                json=[
                    {
                        "info": {
                            "role": "assistant",
                            "time": {"completed": 1},
                            "tokens": {
                                "input": 123,
                                "output": 45,
                                "cache": {"read": 6},
                            },
                            "cost": 0.0012,
                        },
                        "parts": [
                            {
                                "type": "text",
                                "text": "Created and reviewed every required document.",
                            }
                        ],
                    }
                ],
            )
        raise AssertionError(f"unexpected OpenCode request: {request.url.path}")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = OpenCodeHttpClient(
        base_url="http://opencode.test",
        username="opencode",
        password="test-only-password",
        client=http_client,
    )
    result = await QuickOpenCodeCandidateRunner(
        client=client,
        output_root=tmp_path / "output",
        workspace_root=workspace_root,
        timeout_seconds=2,
    ).run(
        project_id="project-a",
        experiment_id=experiment_id,
        source_items=(
            ("current-intodoc-scan:scan_result.json", source_one),
            ("current-intodoc-scan:redaction_report.json", source_two),
        ),
        output_names=output_names,
        output_requirements=(
            ("00_AGENT_BRIEF.md", ("Purpose",)),
            ("09_EXECUTIVE_SUMMARY.md", ("Executive Summary",)),
        ),
        recipe_guidance="Investigate before writing.",
        provider_id="deepseek",
        model_id="deepseek-v4-flash",
    )
    await http_client.aclose()

    assert prompt_payload["model"] == {
        "providerID": "deepseek",
        "modelID": "deepseek-v4-flash",
    }
    prompt = prompt_payload["parts"][0]["text"]  # type: ignore[index]
    assert "read, glob, and grep" in prompt
    assert "JobA" not in prompt
    assert prompt_payload["tools"] == {
        "read": True,
        "glob": True,
        "grep": True,
        "edit": True,
        "write": True,
    }
    assert (workspace_root / experiment_id / "sources" / "scan_result.json").read_text(
        encoding="utf-8"
    ) == '{"jobs":[{"name":"JobA"}]}'
    agent_instructions = (workspace_root / experiment_id / "AGENTS.md").read_text(
        encoding="utf-8"
    )
    assert "required sections: Purpose" in agent_instructions
    assert "Investigate before writing." in agent_instructions
    assert result.input_tokens == 123
    assert result.output_tokens == 45
    assert result.cache_read_tokens == 6
    assert result.billed_cost_usd == "0.0012"
    experiment_root = (
        tmp_path
        / "output"
        / "project-a"
        / "experiments"
        / "00000000-0000-4000-8000-000000000123"
    )
    assert sorted(path.name for path in (experiment_root / "candidate" / "docs").iterdir()) == [
        "00_AGENT_BRIEF.md",
        "09_EXECUTIVE_SUMMARY.md",
    ]
    assert (experiment_root / "candidate" / ".finalized.json").is_file()
    assert not (tmp_path / "output" / "project-a" / "docs").exists()


def test_tool_call_checkpoint_is_not_candidate_completion() -> None:
    messages = [
        {
            "info": {"role": "assistant", "time": {"completed": 1}},
            "parts": [{"type": "step-finish", "reason": "tool-calls"}],
        }
    ]

    assert _has_completed_assistant(messages) is False

    messages.append(
        {
            "info": {"role": "assistant", "time": {"completed": 2}},
            "parts": [{"type": "step-finish", "reason": "stop"}],
        }
    )

    assert _has_completed_assistant(messages) is True


def test_usage_sums_every_assistant_turn() -> None:
    messages = [
        {
            "info": {
                "role": "assistant",
                "time": {"completed": 1},
                "tokens": {"input": 100, "output": 10, "cache": {"read": 4}},
                "cost": 0.001,
            },
            "parts": [{"type": "step-finish", "reason": "tool-calls"}],
        },
        {
            "info": {
                "role": "assistant",
                "time": {"completed": 2},
                "tokens": {"input": 200, "output": 20, "cache": {"read": 6}},
                "cost": 0.002,
            },
            "parts": [{"type": "step-finish", "reason": "stop"}],
        },
    ]

    assert _usage(messages) | {"captured_at": "ignored"} == {
        "input_tokens": 300,
        "output_tokens": 30,
        "cache_read_tokens": 10,
        "billed_cost_usd": "0.003",
        "captured_at": "ignored",
    }
