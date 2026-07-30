"""Single-session OpenCode documentation generation from frozen legacy inputs."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
from pathlib import Path, PurePosixPath
import shutil
from time import monotonic
from typing import Any, TYPE_CHECKING

from mcp_server.experiments.application.run_legacy_replay_candidate import (
    _REPLAY_TASKS,
    _require_phase_one_skip,
)
from mcp_server.experiments.application.run_quick_candidate import (
    QuickCandidateError,
    QuickCandidateResult,
    _has_completed_assistant,
    _latest_assistant,
    _safe_source_relative_path,
    _usage,
)
from mcp_server.experiments.infrastructure.opencode.client import OpenCodeHttpClient
from mcp_server.experiments.infrastructure.storage.artifact_store import (
    ArtifactStore,
    StoredArtifact,
)
from mcp_server.experiments.infrastructure.storage.canonical_json import (
    canonical_json_bytes,
)

if TYPE_CHECKING:
    from mcp_server.experiments.application.grounded_pack import (
        GroundedExecutionPack,
    )


_INTERMEDIATE_OUTPUTS = (
    "phase2/005_AssetCheck_Interface.md",
    "phase2/005_Maximo2AssetCheck.md",
    "phase2_6/006_scorecard.md",
)


class AgenticLegacyOpenCodeRunner:
    """Let one OpenCode agent investigate and execute the frozen legacy workflow."""

    def __init__(
        self,
        *,
        client: OpenCodeHttpClient,
        output_root: Path,
        project_root: Path,
        workspace_root: Path,
        runtime_workspace_root: Path = Path("/runtime/workspaces"),
        timeout_seconds: int = 3600,
    ) -> None:
        self._client = client
        self._output_root = Path(output_root)
        self._project_root = Path(project_root)
        self._workspace_root = Path(workspace_root)
        self._runtime_workspace_root = Path(runtime_workspace_root)
        self._timeout_seconds = timeout_seconds

    async def run(
        self,
        *,
        project_id: str,
        experiment_id: str,
        source_items: tuple[tuple[str, Path], ...],
        provider_id: str,
        model_id: str,
        output_names: tuple[str, ...],
    ) -> QuickCandidateResult:
        if not source_items:
            raise QuickCandidateError("agentic candidate requires frozen scanner sources")
        health = await self._client.health()
        if str(health.get("version") or "") != "1.18.4":
            raise QuickCandidateError("OpenCode runtime version is not pinned to 1.18.4")
        docs_root = self._project_root / "output" / project_id / "docs"
        _require_phase_one_skip(docs_root)
        expected_outputs = tuple(
            task.output_name for task in _REPLAY_TASKS if task.output_name is not None
        )
        if output_names != expected_outputs:
            raise QuickCandidateError(
                "agentic output contract does not match the preserved Phase 3 run"
            )

        workspace, input_receipts = await asyncio.to_thread(
            _prepare_agentic_workspace,
            workspace_root=self._workspace_root,
            experiment_id=experiment_id,
            source_items=source_items,
            docs_root=docs_root,
            output_names=output_names,
        )
        started = monotonic()
        session = await self._client.create_session(
            title=f"Nexus agentic legacy-equivalence {project_id} {experiment_id}",
            directory=str(self._runtime_workspace_root / experiment_id),
        )
        await self._client.prompt_async(
            session.session_id,
            payload={
                "model": {"providerID": provider_id, "modelID": model_id},
                "parts": [{"type": "text", "text": _agent_prompt()}],
                "tools": {
                    "read": True,
                    "glob": True,
                    "grep": True,
                    "edit": True,
                    "write": True,
                },
            },
            idempotency_key=f"{experiment_id}:agentic-documentation",
        )
        messages = await self._wait_for_messages(session.session_id)
        _latest_assistant(messages)
        documents, intermediates = await asyncio.to_thread(
            _read_candidate_outputs,
            workspace,
            output_names,
        )
        usage = _usage(messages)
        tool_summary = _tool_summary(messages)
        store = ArtifactStore(
            output_root=self._output_root,
            project_id=project_id,
            experiment_id=experiment_id,
        )
        artifacts: list[StoredArtifact] = []
        for name in output_names:
            artifacts.append(
                store.write_bytes(
                    branch="candidate",
                    logical_name=f"docs/{name}",
                    relative_path=f"docs/{name}",
                    content=documents[name].encode("utf-8"),
                    media_type="text/markdown",
                    kind="document",
                )
            )
        for relative_name in _INTERMEDIATE_OUTPUTS:
            artifacts.append(
                store.write_bytes(
                    branch="candidate",
                    logical_name=f"intermediates/{relative_name}",
                    relative_path=f"intermediates/{relative_name}",
                    content=intermediates[relative_name].encode("utf-8"),
                    media_type="text/markdown",
                    kind="analysis",
                )
            )
        receipt = {
            "schema_version": 1,
            "execution_mode": "single-session-agentic-legacy-equivalence",
            "project_id": project_id,
            "experiment_id": experiment_id,
            "runtime": "opencode/1.18.4",
            "provider_id": provider_id,
            "model_id": model_id,
            "phase1": {
                "status": "skipped",
                "reason": "Direct path: Phase 1 skipped for small project.",
            },
            "allowed_tools": ["read", "glob", "grep", "edit", "write"],
            "tool_summary": tool_summary,
            "input_receipts": input_receipts,
            "usage": usage,
            "session_id": session.session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        receipt_artifact = store.write_bytes(
            branch="candidate",
            logical_name="receipts/agentic_execution.json",
            relative_path="receipts/agentic_execution.json",
            content=canonical_json_bytes(receipt),
            media_type="application/json",
            kind="receipt",
        )
        artifacts.append(receipt_artifact)
        finalized = store.finalize_branch(
            branch="candidate",
            manifest={
                "schema_version": 1,
                "experiment_id": experiment_id,
                "project_id": project_id,
                "runtime": "opencode/1.18.4",
                "provider_id": provider_id,
                "model_id": model_id,
                "execution_mode": "single-session-agentic-legacy-equivalence",
                "publishes": False,
                "artifacts": [
                    {
                        "logical_name": item.logical_name,
                        "relative_path": item.relative_path,
                        "sha256": item.sha256,
                        "size_bytes": item.size_bytes,
                        "kind": item.kind,
                    }
                    for item in artifacts
                ],
                "usage": usage,
                "tool_summary": tool_summary,
            },
        )
        return QuickCandidateResult(
            artifacts=tuple(artifacts),
            manifest_sha256=finalized.manifest_sha256,
            session_id=session.session_id,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_read_tokens=usage["cache_read_tokens"],
            billed_cost_usd=usage["billed_cost_usd"],
            elapsed_ms=int((monotonic() - started) * 1000),
        )

    async def _wait_for_messages(self, session_id: str) -> list[dict[str, Any]]:
        deadline = monotonic() + self._timeout_seconds
        while monotonic() < deadline:
            messages = [dict(item) for item in await self._client.messages(session_id)]
            if _has_completed_assistant(messages):
                return messages
            await asyncio.sleep(1)
        raise QuickCandidateError("OpenCode agentic documentation generation timed out")


def _prepare_agentic_workspace(
    *,
    workspace_root: Path,
    experiment_id: str,
    source_items: tuple[tuple[str, Path], ...],
    docs_root: Path,
    output_names: tuple[str, ...],
    orchestrated: bool = False,
    grounded: bool = False,
    grounded_pack: GroundedExecutionPack | None = None,
    fact_ledger: dict[str, Any] | None = None,
    source_preflight: dict[str, Any] | None = None,
) -> tuple[Path, list[dict[str, Any]]]:
    if grounded and not orchestrated:
        raise QuickCandidateError("grounded workspace requires orchestration")
    if grounded and (
        grounded_pack is None
        or fact_ledger is None
        or source_preflight is None
    ):
        raise QuickCandidateError(
            "grounded workspace requires a validated execution pack and preflight"
        )
    if "/" in experiment_id or "\\" in experiment_id or experiment_id in {"", ".", ".."}:
        raise QuickCandidateError("invalid experiment workspace identity")
    workspace = workspace_root / experiment_id
    workspace.mkdir(parents=True, exist_ok=False)
    workspace.chmod(0o777)
    source_root = workspace / "sources"
    workflow_root = workspace / "legacy_workflow"
    docs_output = workspace / "candidate" / "docs"
    intermediate_output = workspace / "candidate" / "intermediates"
    orchestration_output = workspace / "candidate" / "orchestration"
    output_directories = [source_root, workflow_root, docs_output, intermediate_output]
    if orchestrated:
        output_directories.append(orchestration_output)
    for path in output_directories:
        path.mkdir(parents=True)
    (workspace / "candidate").chmod(0o777)
    docs_output.chmod(0o777)
    intermediate_output.chmod(0o777)
    if orchestrated:
        orchestration_output.chmod(0o777)
    receipts: list[dict[str, Any]] = []

    source_inventory: list[str] = []
    for index, (logical_identity, source_path) in enumerate(source_items, start=1):
        relative = _safe_source_relative_path(logical_identity, index)
        destination = source_root.joinpath(*relative.parts)
        _copy_immutable(source_path, destination)
        source_inventory.append(f"- `sources/{relative.as_posix()}`")
        receipts.append(_file_receipt("scanner_source", destination, workspace))

    workflow_inventory: list[str] = []
    workflow_tasks = (
        _REPLAY_TASKS
        if not grounded or grounded_pack.workflow_mode == "legacy_replay"
        else ()
    )
    for task in workflow_tasks:
        source_dir = (
            docs_root
            / "_debug"
            / "model_inputs"
            / task.phase
            / task.directory_name
        )
        ledger_dir = (
            docs_root
            / "_model_inputs"
            / task.phase
            / task.directory_name
        )
        destination_dir = workflow_root / task.phase / task.directory_name
        for name, source in (
            ("prompt.md", source_dir / "prompt.md"),
            ("context_bundle.md", source_dir / "context_bundle.md"),
            ("request.json", source_dir / "request.json"),
            ("manifest.json", ledger_dir / "manifest.json"),
        ):
            destination = destination_dir / name
            _copy_immutable(source, destination)
            receipts.append(_file_receipt("legacy_model_input", destination, workspace))
        workflow_inventory.append(
            f"- `{task.phase}/{task.directory_name}`"
            + (f" → `candidate/docs/{task.output_name}`" if task.output_name else "")
        )

    required_docs = "\n".join(f"- `candidate/docs/{name}`" for name in output_names)
    intermediate_names = (
        tuple(
            path.removeprefix("candidate/intermediates/")
            for path in grounded_pack.owned_artifacts
            if path.startswith("candidate/intermediates/")
        )
        if grounded
        else _INTERMEDIATE_OUTPUTS
    )
    required_intermediates = "\n".join(
        f"- `candidate/intermediates/{name}`" for name in intermediate_names
    )
    if grounded:
        agent_instructions = _grounded_agent_instructions(
            required_intermediates,
            required_docs,
        )
    elif orchestrated:
        agent_instructions = _orchestrated_agent_instructions(
            required_intermediates,
            required_docs,
        )
    else:
        agent_instructions = _single_agent_instructions(
            required_intermediates,
            required_docs,
        )
    (workspace / "AGENTS.md").write_text(agent_instructions, encoding="utf-8")
    (workspace / "SOURCES.md").write_text(
        "# Frozen scanner sources\n\n"
        + "\n".join(source_inventory)
        + "\n\n# Preserved workflow calls\n\n"
        + "\n".join(workflow_inventory)
        + "\n",
        encoding="utf-8",
    )
    if grounded:
        workflow_instructions = _grounded_workflow_instructions()
    elif orchestrated:
        workflow_instructions = _orchestrated_workflow_instructions()
    else:
        workflow_instructions = _single_agent_workflow_instructions()
    (workspace / "WORKFLOW.md").write_text(
        workflow_instructions,
        encoding="utf-8",
    )
    instruction_names = ["AGENTS.md", "SOURCES.md", "WORKFLOW.md"]
    if orchestrated:
        (workspace / "ORCHESTRATION_CONTRACT.json").write_bytes(
            canonical_json_bytes(
                {
                    "execution_plan": {
                        "required_fields": [
                            "source_types",
                            "canonical_units",
                            "partitions",
                            "agent_assignments",
                            "document_owners",
                            "acceptance_checks",
                        ],
                        "field_types": {
                            "source_types": ["array", "object"],
                            "canonical_units": ["array"],
                            "partitions": ["array", "object"],
                            "agent_assignments": ["array", "object"],
                            "document_owners": ["object"],
                            "acceptance_checks": ["array", "object"],
                        },
                        "minimum_cardinality": {
                            "source_types": 1,
                            "canonical_units": 1,
                            "agent_assignments": 1,
                            "document_owners": len(output_names),
                        },
                    },
                    "evidence_registry": {
                        "required_fields": ["claims"],
                        "claim_fields": [
                            "claim_id",
                            "statement",
                            "scope",
                            "evidence_locators",
                            "confidence",
                            "contradictions",
                            "used_by_documents",
                        ],
                    },
                    "coverage_ledger": {
                        "required_fields": [
                            "canonical_units",
                            "concerns",
                            "document_coverage",
                            "gaps",
                        ]
                    },
                    "review_report": {
                        "required_sections": [
                            "Factual review",
                            "Coverage review",
                            "Cross-document consistency",
                            "Secret and evidence-path review",
                            "Resolved findings",
                            "Final verdict",
                        ]
                    },
                }
            )
        )
        instruction_names.append("ORCHESTRATION_CONTRACT.json")
    if grounded:
        from mcp_server.experiments.application.grounded_pack import (
            grounded_pack_payload,
        )

        pack_path = workspace / "GROUNDED_EXECUTION_PACK.json"
        pack_path.write_bytes(
            canonical_json_bytes(grounded_pack_payload(grounded_pack))
        )
        preflight_path = workspace / "SOURCE_PREFLIGHT.json"
        preflight_path.write_bytes(canonical_json_bytes(source_preflight))
        fact_ledger_path = workspace / "FACT_LEDGER.json"
        fact_ledger_path.write_bytes(
            canonical_json_bytes(fact_ledger)
        )
        receipts.extend(
            (
                _file_receipt("grounded_execution_pack", pack_path, workspace),
                _file_receipt("source_preflight", preflight_path, workspace),
                _file_receipt(
                    "deterministic_fact_ledger",
                    fact_ledger_path,
                    workspace,
                ),
            )
        )
        _write_role_packets(
            workspace=workspace,
            pack=grounded_pack,
            source_inventory=tuple(
                item.removeprefix("- `sources/").removesuffix("`")
                for item in source_inventory
            ),
            workflow_tasks=workflow_tasks,
        )
        instruction_names.extend(
            [
                "FACT_LEDGER.json",
                "SOURCE_PREFLIGHT.json",
                "GROUNDED_EXECUTION_PACK.json",
            ]
        )
    for name in instruction_names:
        (workspace / name).chmod(0o444)
    _lock_input_tree(source_root)
    _lock_input_tree(workflow_root)
    return workspace, receipts


def _single_agent_instructions(
    required_intermediates: str,
    required_docs: str,
) -> str:
    return (
        "# Nexus agentic documentation experiment\n\n"
        "You are the sole documentation agent in an isolated, non-publishing A/B "
        "workspace. The files under `sources/` and `legacy_workflow/` are immutable "
        "approved inputs. Use read, glob, and grep extensively to investigate them. "
        "Write only beneath `candidate/`. Do not modify or replace any input.\n\n"
        "Execute the preserved workflow requirements rather than inventing a shorter "
        "documentation recipe. The exact rendered legacy prompts are the instruction "
        "source for each analysis and document. The context bundles preserve the "
        "evidence those prompts received. You may navigate, cross-reference, and "
        "select evidence agentically, but must satisfy every factual constraint, "
        "coverage obligation, template rule, and output requirement in those files.\n\n"
        "Phase 1 was skipped by the baseline and must remain skipped. Produce your own "
        "two Phase 2 analyses, then your own scorecard, and use those candidate "
        "intermediates together with the preserved evidence when writing Phase 3. "
        "Re-open and review every output. Fix omissions, contradictions, thin "
        "sections, and unsupported claims before finishing. Unknown facts must be "
        "marked unknown. Never claim publication or RAG ingestion.\n\n"
        "## Required intermediate outputs\n\n"
        f"{required_intermediates}\n\n"
        "## Required final outputs\n\n"
        f"{required_docs}\n"
    )


def _orchestrated_agent_instructions(
    required_intermediates: str,
    required_docs: str,
) -> str:
    return (
        "# Nexus orchestrated documentation experiment\n\n"
        "You are the primary documentation orchestrator in an isolated, "
        "non-publishing A/B workspace. Inputs beneath `sources/` and "
        "`legacy_workflow/` are immutable. Write only beneath `candidate/`; never "
        "modify inputs, publish documents, or claim RAG ingestion.\n\n"
        "Do not perform the whole workflow alone. First inventory the available source "
        "types, canonical units, relationships, preserved prompts, and output "
        "obligations. Then create `candidate/orchestration/execution_plan.json` before "
        "drafting. Use the Task tool to delegate bounded work to the allowlisted "
        "`nexus-doc-*` specialists. Dispatch independent evidence and writing tasks in "
        "parallel where possible, with at most six active child tasks. Use no nested "
        "subagents.\n\n"
        "Select agents based on the discovered inputs and requested outputs, not on a "
        "hardcoded number of jobs or a Talend-only partition. Evidence specialists and "
        "analysts return scoped findings; you consolidate them into an evidence "
        "registry and coverage ledger before document writing. Assign exactly one "
        "writer owner to each final document. After drafting, invoke an evidence "
        "referee and independent reviewers, repair concrete defects, and record the "
        "final review.\n\n"
        "Every factual claim must distinguish confirmed, inferred, and unknown. Every "
        "universal claim such as all/every/none/exactly must enumerate or otherwise "
        "prove its complete canonical scope. Preserve per-unit detail whenever the "
        "document obligation requires it. Use durable source-relative evidence "
        "locators, reconcile disagreements, and never reproduce secret values.\n\n"
        "Phase 1 was skipped by the baseline and remains skipped. The exact preserved "
        "prompts are the instruction and document contracts; the context bundles are "
        "approved evidence, not necessarily correct conclusions. Produce your own "
        "Phase 2 analyses and scorecard before Phase 3.\n\n"
        "## Required orchestration outputs\n\n"
        "- `candidate/orchestration/execution_plan.json`\n"
        "- `candidate/orchestration/evidence_registry.json`\n"
        "- `candidate/orchestration/coverage_ledger.json`\n"
        "- `candidate/orchestration/review_report.md`\n\n"
        "## Required intermediate outputs\n\n"
        f"{required_intermediates}\n\n"
        "## Required final outputs\n\n"
        f"{required_docs}\n"
    )


def _grounded_agent_instructions(
    required_intermediates: str,
    required_docs: str,
) -> str:
    return (
        "# Nexus grounded documentation experiment\n\n"
        "You are the primary orchestrator in an isolated, non-publishing A/B "
        "workspace. Inputs beneath `sources/`, `legacy_workflow/`, and "
        "`FACT_LEDGER.json` are immutable. Write only beneath `candidate/`; never "
        "publish documents or claim RAG ingestion.\n\n"
        "`FACT_LEDGER.json` is deterministic and authoritative for canonical units, "
        "groups, component counts, component-type counts, job-call edges, and "
        "connection usage. Do not recount or override those facts with model "
        "inference. Preserved context bundles may contain earlier model conclusions; "
        "where they disagree with the ledger, use the ledger and record the conflict.\n\n"
        "Read `GROUNDED_EXECUTION_PACK.json` and dispatch exactly its declared roles, "
        "dependencies, ownership, parallelism, model policy, and repair limits. Each "
        "child must read only its immutable `role_inputs/<role>/PACKET.json` and the "
        "allowed inputs listed there. Reuse dependency artifacts; no role may inspect "
        "the whole scan merely because it is present in the workspace. Use no nested "
        "subagents. The primary orchestrator owns only the primary artifacts declared "
        "by the pack and bounded targeted repairs.\n\n"
        "Document-family writers must respect the pack's non-overlapping ownership. Execute the "
        "exact preserved Phase 2, scorecard, and Phase 3 prompts. Phase 1 was skipped "
        "by the baseline and remains skipped. Every universal claim must cite a "
        "protected fact or enumerate its complete scope. Static absence is never proof "
        "of runtime absence. Treat `NEED_AUTH=false` as disabled framework-level "
        "authentication, not as proof that downstream custom checks are bypassed.\n\n"
        "Before finishing, review every number against `FACT_LEDGER.json`, resolve "
        "cross-document contradictions, mark runtime unknowns, and avoid secret "
        "values. A deterministic checker will reject mismatched protected facts and "
        "may request one direct repair pass without additional subagents.\n\n"
        "## Required orchestration outputs\n\n"
        "- `candidate/orchestration/execution_plan.json`\n"
        "- `candidate/orchestration/evidence_registry.json`\n"
        "- `candidate/orchestration/coverage_ledger.json`\n"
        "- `candidate/orchestration/review_report.md`\n\n"
        "## Required intermediate outputs\n\n"
        f"{required_intermediates}\n\n"
        "## Required final outputs\n\n"
        f"{required_docs}\n"
    )


def _single_agent_workflow_instructions() -> str:
    return (
        "# Execution order\n\n"
        "1. Read `AGENTS.md` and `SOURCES.md` completely.\n"
        "2. Inspect the scanner sources with glob, grep, and targeted reads.\n"
        "3. Confirm `_investigation/_summary.md` evidence inside the preserved "
        "contexts says Phase 1 was skipped; do not create a Phase 1 output.\n"
        "4. Execute both `legacy_workflow/phase2/*/prompt.md` instruction sets using "
        "their matching context bundles and relevant scanner evidence. Write the two "
        "required Phase 2 intermediate files.\n"
        "5. Execute `legacy_workflow/phase2.6/006_scorecard/prompt.md`, using the "
        "preserved context plus your candidate Phase 2 analyses. Write the scorecard.\n"
        "6. Execute every `legacy_workflow/phase3/*/prompt.md`. Use the matching "
        "context bundle, scanner evidence, and your candidate intermediates. Write "
        "the exact required document path shown in `AGENTS.md`.\n"
        "7. Use glob/read/grep to audit completeness and internal consistency across "
        "all ten documents. Repair them with edit/write as needed.\n"
        "8. Finish only when every required intermediate and final file exists and is "
        "substantive. Return only a short completion summary in chat; the artifacts "
        "must be in files.\n"
    )


def _orchestrated_workflow_instructions() -> str:
    return (
        "# Orchestrated execution order\n\n"
        "1. Read `AGENTS.md`, `SOURCES.md`, and every preserved manifest completely.\n"
        "2. Inventory source types and canonical units with targeted glob/grep/read. "
        "Write a provider-neutral execution plan with partitions, agent assignments, "
        "dependencies, budgets, document owners, and acceptance checks.\n"
        "3. Delegate multiple bounded source/unit investigations to "
        "`nexus-doc-evidence`; delegate cross-cutting concerns to "
        "`nexus-doc-analyst`. Parallelize independent scopes.\n"
        "4. Consolidate child findings into `evidence_registry.json` and "
        "`coverage_ledger.json`. The ledger must cover every canonical unit against "
        "architecture, components, lineage, business logic, configuration, operations, "
        "security/quality, and each required document.\n"
        "5. Delegate the registry and ledger to `nexus-doc-referee`. Resolve all "
        "contradictions, unproven counts, universal claims, missing units, secret "
        "exposure, and weak evidence paths before writing.\n"
        "6. Produce both Phase 2 analyses and the scorecard using their exact preserved "
        "prompts and the approved evidence. Phase 1 remains skipped.\n"
        "7. Assign each Phase 3 output to exactly one `nexus-doc-writer`; writers may "
        "run in parallel and must use the exact matching prompt and approved evidence.\n"
        "8. Delegate factual and cross-document review to at least two independent "
        "`nexus-doc-reviewer` tasks. Record defects and resolutions in "
        "`review_report.md`, then make only targeted repairs.\n"
        "9. Recheck all required files, all canonical-unit coverage, durable evidence "
        "locators, internal consistency, and redaction. Finish only after the review "
        "report records a final pass or explicitly resolved findings.\n"
    )


def _grounded_workflow_instructions() -> str:
    return (
        "# Grounded execution order\n\n"
        "1. Read `AGENTS.md`, `SOURCES.md`, `WORKFLOW.md`, "
        "`ORCHESTRATION_CONTRACT.json`, and `FACT_LEDGER.json` completely.\n"
        "2. Read `GROUNDED_EXECUTION_PACK.json`. Write the execution plan before "
        "delegating and dispatch exactly the declared role graph within its concurrency "
        "budget. Give each role only its `role_inputs/<role>/PACKET.json` contract.\n"
        "3. Evidence roles must return their exact owned artifacts with source-relative "
        "locators, unknowns, and conflicts with preserved context.\n"
        "4. Analysis roles consume only their packet plus accepted dependency outputs "
        "and use the fact ledger for all protected counts.\n"
        "5. Consolidate only the primary artifacts owned by the orchestrator and keep "
        "the evidence registry and coverage ledger synchronized.\n"
        "6. Dispatch document-family writers for their non-overlapping pack ownership. "
        "Each uses matching prompts, accepted dependency evidence, and deterministic facts.\n"
        "7. Dispatch the declared reviewer over the claim matrix and documents. Repair its "
        "concrete factual, coverage, confidence, security-semantics, and consistency "
        "findings directly; do not spawn more sessions.\n"
        "8. Recheck every protected number against `FACT_LEDGER.json`, every pack-required "
        "file, every canonical unit in the component inventory, evidence paths, "
        "redaction, and the required review sections. Finish only with substantive "
        "files and a recorded final verdict.\n"
    )


def _write_role_packets(
    *,
    workspace: Path,
    pack: GroundedExecutionPack,
    source_inventory: tuple[str, ...],
    workflow_tasks: tuple[Any, ...],
) -> None:
    """Create immutable, budgeted role input manifests.

    Raw approved files remain at durable evidence paths, but every child contract
    names only its packet and dependencies. This prevents the orchestration prompt
    from handing the complete scan/context bundle to every role.
    """

    role_root = workspace / "role_inputs"
    role_root.mkdir()
    evidence_roles = [role for role in pack.roles if role.stage == "evidence"]
    source_assignments: dict[str, list[str]] = {
        role.id: [] for role in pack.roles
    }
    if source_inventory and not evidence_roles:
        raise QuickCandidateError(
            "grounded pack has scanner sources but no evidence role"
        )
    for index, relative in enumerate(source_inventory):
        owner = evidence_roles[index % len(evidence_roles)]
        source_assignments[owner.id].append(f"sources/{relative}")

    analysis_roles = [role for role in pack.roles if role.stage == "analysis"]
    analysis_tasks = [
        task for task in workflow_tasks if task.phase in {"phase2", "phase2.6"}
    ]
    workflow_assignments: dict[str, list[str]] = {
        role.id: [] for role in pack.roles
    }
    for index, task in enumerate(analysis_tasks):
        if analysis_roles:
            workflow_assignments[
                analysis_roles[index % len(analysis_roles)].id
            ].append(f"legacy_workflow/{task.phase}/{task.directory_name}")
    for role in pack.roles:
        if role.stage == "writing":
            owned_docs = {
                path.removeprefix("candidate/docs/")
                for path in role.owns
                if path.startswith("candidate/docs/")
            }
            workflow_assignments[role.id].extend(
                f"legacy_workflow/{task.phase}/{task.directory_name}"
                for task in workflow_tasks
                if task.output_name in owned_docs
            )

    for role in pack.roles:
        allowed_inputs = sorted(
            {
                "FACT_LEDGER.json",
                "GROUNDED_EXECUTION_PACK.json",
                *source_assignments[role.id],
                *workflow_assignments[role.id],
                *(
                    path
                    for dependency_id in role.depends_on
                    for dependency in pack.roles
                    if dependency.id == dependency_id
                    for path in dependency.owns
                ),
            }
        )
        context_bytes = sum(
            _workspace_input_size(workspace, path)
            for path in allowed_inputs
            if (workspace / path).exists()
        )
        if context_bytes > role.max_context_bytes:
            raise QuickCandidateError(
                f"grounded role context budget exceeded: {role.id}"
            )
        packet_dir = role_root / role.id
        packet_dir.mkdir()
        packet = packet_dir / "PACKET.json"
        packet.write_bytes(
            canonical_json_bytes(
                {
                    "schema_version": 1,
                    "role_id": role.id,
                    "agent": role.agent,
                    "stage": role.stage,
                    "depends_on": list(role.depends_on),
                    "owns": list(role.owns),
                    "allowed_inputs": allowed_inputs,
                    "allowed_tool_roots": list(role.source_globs),
                    "context_bytes": context_bytes,
                    "max_context_bytes": role.max_context_bytes,
                    "model_policy": role.model_policy,
                }
            )
        )
        packet.chmod(0o444)
        packet_dir.chmod(0o555)
    role_root.chmod(0o555)


def _workspace_input_size(workspace: Path, relative: str) -> int:
    path = workspace / relative
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        return sum(
            item.stat().st_size
            for item in path.rglob("*")
            if item.is_file() and not item.is_symlink()
        )
    return 0


def _copy_immutable(source: Path, destination: Path) -> None:
    if not source.is_file() or source.is_symlink():
        raise QuickCandidateError(f"agentic input is not a regular file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(0o444)


def _lock_input_tree(root: Path) -> None:
    for directory in sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        directory.chmod(0o555)
    root.chmod(0o555)


def _file_receipt(kind: str, path: Path, workspace: Path) -> dict[str, Any]:
    content = path.read_bytes()
    return {
        "kind": kind,
        "path": path.relative_to(workspace).as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
    }


def _agent_prompt() -> str:
    return (
        "Read AGENTS.md, SOURCES.md, and WORKFLOW.md completely, then execute the "
        "documentation workflow. Use glob, grep, and read to investigate the frozen "
        "scanner sources and every preserved prompt/context set. Create the required "
        "candidate intermediate analyses, scorecard, and ten final documents with "
        "write/edit. Review and repair the full bundle before finishing. Do not paste "
        "the documents into chat and do not finish until every required file exists."
    )


def _read_candidate_outputs(
    workspace: Path,
    output_names: tuple[str, ...],
) -> tuple[dict[str, str], dict[str, str]]:
    documents = _read_exact_markdown_directory(
        workspace / "candidate" / "docs",
        output_names,
        label="document",
    )
    intermediates: dict[str, str] = {}
    root = workspace / "candidate" / "intermediates"
    for relative_name in _INTERMEDIATE_OUTPUTS:
        relative = PurePosixPath(relative_name)
        path = root.joinpath(*relative.parts)
        if not path.is_file() or path.is_symlink():
            raise QuickCandidateError(
                f"OpenCode candidate intermediate is missing: {relative_name}"
            )
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise QuickCandidateError(
                f"OpenCode candidate intermediate is empty: {relative_name}"
            )
        intermediates[relative_name] = text + "\n"
    return documents, intermediates


def _read_exact_markdown_directory(
    root: Path,
    expected_names: tuple[str, ...],
    *,
    label: str,
) -> dict[str, str]:
    actual = {
        path.name for path in root.iterdir() if path.is_file() and not path.is_symlink()
    }
    if actual != set(expected_names):
        missing = sorted(set(expected_names) - actual)
        unexpected = sorted(actual - set(expected_names))
        raise QuickCandidateError(
            f"OpenCode candidate {label} files mismatch; "
            f"missing={missing}, unexpected={unexpected}"
        )
    result: dict[str, str] = {}
    for name in expected_names:
        text = (root / name).read_text(encoding="utf-8").strip()
        if not text:
            raise QuickCandidateError(f"OpenCode candidate {label} is empty: {name}")
        result[name] = text + "\n"
    return result


def _tool_summary(messages: list[dict[str, Any]]) -> dict[str, Any]:
    names: Counter[str] = Counter()
    for message in messages:
        for part in message.get("parts", []):
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "").replace("_", "-").lower()
            if part_type not in {"tool", "tool-call"}:
                continue
            name = (
                part.get("tool")
                or part.get("toolName")
                or part.get("name")
                or "unknown"
            )
            names[str(name)] += 1
    return {"total_calls": sum(names.values()), "by_tool": dict(sorted(names.items()))}
