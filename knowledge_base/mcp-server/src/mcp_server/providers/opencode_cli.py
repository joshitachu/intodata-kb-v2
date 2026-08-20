"""OpenCode CLI provider for the modern three-phase documentation workflow.

The coordinator performs the same deterministic preprocessing as every other
provider.  This adapter then keeps one OpenCode session alive across three
messages (Gather, Analyze, Produce), running the existing deterministic legacy
handoffs between them.  Task prompts come from the current custom-pipeline
renderers; the retired Claude CLI mega-prompt is never used.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from types import SimpleNamespace
from typing import Any

from mcp_server.config import config
from mcp_server.providers.base import AgentResult, DocGenContext, DocGenerationProvider, LogCallback

logger = logging.getLogger(__name__)

PHASES = ("gather", "analyze", "produce")
_READ_ONLY_TOOL_PREFIXES = ("read", "get", "list", "search", "fetch", "query", "find")
_SENSITIVE_EVENT_KEYS = frozenset(
    {
        "prompt", "reasoning", "content", "payload", "config", "env",
        "environment", "token", "api_key", "authorization", "secret",
    }
)


class OpenCodeCompatibilityError(RuntimeError):
    """Raised when the configured OpenCode runtime cannot execute safely."""


class OpenCodeAttemptError(RuntimeError):
    """Raised when Nexus tries to dispatch the same orchestration step twice."""


@dataclass
class OpenCodeAttemptLedger:
    """Persist the Nexus-owned dispatch count for one generation run."""

    staging_dir: Path
    attempts: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        path = self.staging_dir / "_audit" / "opencode_attempt_ledger.json"
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            persisted = payload.get("attempts") if isinstance(payload, dict) else None
            if isinstance(persisted, dict) and all(
                isinstance(key, str) and isinstance(value, int)
                for key, value in persisted.items()
            ):
                self.attempts.update(persisted)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return

    def dispatch(self, target: str) -> None:
        count = self.attempts.get(target, 0) + 1
        if count > 1:
            raise OpenCodeAttemptError(f"OpenCode target {target!r} exceeds max_attempts=1")
        self.attempts[target] = count
        path = self.staging_dir / "_audit" / "opencode_attempt_ledger.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"max_nexus_attempts": 1, "attempts": self.attempts}, indent=2),
            encoding="utf-8",
        )

    def release_unstarted(self, target: str) -> None:
        """Do not count a launcher failure as a model attempt."""

        if self.attempts.pop(target, None) is None:
            return
        path = self.staging_dir / "_audit" / "opencode_attempt_ledger.json"
        path.write_text(
            json.dumps({"max_nexus_attempts": 1, "attempts": self.attempts}, indent=2),
            encoding="utf-8",
        )


@dataclass(frozen=True)
class OpenCodeTaskContract:
    """One bounded task the OpenCode orchestrator must complete."""

    phase: str
    target: str
    prompt_artifact: str
    evidence_artifacts: tuple[str, ...]
    output_artifact: str
    allowed_tools: tuple[str, ...] = ()
    prompt: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_wire(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "phase": self.phase,
            "target": self.target,
            "prompt_artifact": self.prompt_artifact,
            "prompt_sha256": _prompt_hash(self.prompt),
            "evidence_artifacts": list(self.evidence_artifacts),
            "output_artifact": self.output_artifact,
            "allowed_tools": list(self.allowed_tools),
            "nexus_max_attempts": 1,
        }
        if self.prompt is not None:
            data["prompt"] = self.prompt
        if self.metadata:
            data["metadata"] = self.metadata
        return data


@dataclass
class _PhaseExecution:
    session_id: str | None
    events: list[dict[str, Any]]
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0


def _prompt_hash(prompt: str | None) -> str | None:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt is not None else None


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "task"


def parse_mcp_read_allowlist(raw: str) -> dict[str, tuple[str, ...]]:
    """Parse the operator's bounded read-tool policy."""

    try:
        decoded = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise OpenCodeCompatibilityError("invalid OpenCode MCP allowlist JSON") from exc
    if not isinstance(decoded, dict):
        raise OpenCodeCompatibilityError("OpenCode MCP allowlist must be an object")
    allowlist: dict[str, tuple[str, ...]] = {}
    for server, tools in decoded.items():
        if not isinstance(server, str) or not isinstance(tools, list) or not all(
            isinstance(tool, str) for tool in tools
        ):
            raise OpenCodeCompatibilityError(
                "OpenCode MCP allowlist entries must be string tool lists"
            )
        rejected = [
            tool for tool in tools
            if not tool.lower().startswith(_READ_ONLY_TOOL_PREFIXES)
        ]
        if rejected:
            raise OpenCodeCompatibilityError(
                f"OpenCode MCP tools must be read-only; rejected {server}: {', '.join(rejected)}"
            )
        allowlist[server] = tuple(tools)
    return allowlist


class OpenCodeCliProvider(DocGenerationProvider):
    """Run modern legacy prompts through one continued OpenCode session."""

    def __init__(
        self,
        *,
        command: str | None = None,
        model_route: str | None = None,
        plugin_pack_dir: Path | None = None,
    ) -> None:
        self.command = command or config.opencode_command
        self.model_route = (model_route or config.opencode_model).strip()
        self.plugin_pack_dir = plugin_pack_dir

    def _legacy_harness(self, facts: Any):
        """Create legacy deterministic helpers without a callable model provider."""

        from mcp_server.providers.custom.harness import CustomHarnessProvider

        marker = SimpleNamespace(model_name=self.model_route or "opencode-default")
        harness = CustomHarnessProvider(marker, plugin_pack_dir=self.plugin_pack_dir)
        harness._expand_per_job_docs(facts)
        return harness

    @staticmethod
    def _facts_for_context(ctx: DocGenContext):
        from mcp_server.providers.custom.validators import build_facts_manifest

        facts = build_facts_manifest(ctx.project_id, config.intodoc_output_dir / ctx.project_id)
        if ctx.partition is not None:
            from mcp_server.providers.custom.partition_planner import filter_manifest

            facts = filter_manifest(facts, ctx.partition)
        return facts

    @staticmethod
    def _runtime_env() -> dict[str, str]:
        """Return an OpenCode-safe environment for system service accounts."""

        env = dict(os.environ)
        home = env.get("HOME", "").strip()
        xdg_config_home = env.get("XDG_CONFIG_HOME", "").strip()
        if (not home or home == "/nonexistent") and xdg_config_home:
            env["HOME"] = str(Path(xdg_config_home).parent)
        env["PYTHONUTF8"] = "1"
        return env

    def _execution_env(self) -> dict[str, str]:
        """Add model connector configuration needed by the selected route."""

        env = self._runtime_env()
        if not self.model_route.startswith("ollama/"):
            return env

        model_id = self.model_route.split("/", 1)[1]
        try:
            inline_config = json.loads(env.get("OPENCODE_CONFIG_CONTENT") or "{}")
        except json.JSONDecodeError as exc:
            raise OpenCodeCompatibilityError(
                "OPENCODE_CONFIG_CONTENT must be valid JSON"
            ) from exc
        if not isinstance(inline_config, dict):
            raise OpenCodeCompatibilityError(
                "OPENCODE_CONFIG_CONTENT must contain a JSON object"
            )

        providers = inline_config.setdefault("provider", {})
        if not isinstance(providers, dict):
            raise OpenCodeCompatibilityError(
                "OpenCode inline provider configuration must be an object"
            )
        ollama = providers.setdefault("ollama", {})
        if not isinstance(ollama, dict):
            raise OpenCodeCompatibilityError(
                "OpenCode Ollama provider configuration must be an object"
            )
        ollama.setdefault("npm", "@ai-sdk/openai-compatible")
        ollama.setdefault("name", "Ollama (local)")
        options = ollama.setdefault("options", {})
        if not isinstance(options, dict):
            raise OpenCodeCompatibilityError(
                "OpenCode Ollama provider options must be an object"
            )
        options.setdefault("baseURL", config.opencode_ollama_base_url)
        models = ollama.setdefault("models", {})
        if not isinstance(models, dict):
            raise OpenCodeCompatibilityError(
                "OpenCode Ollama provider models must be an object"
            )
        models.setdefault(model_id, {"name": model_id})
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(inline_config, separators=(",", ":"))
        return env

    def _preflight(self) -> tuple[str, dict[str, tuple[str, ...]]]:
        if not config.opencode_version:
            raise OpenCodeCompatibilityError("MCP_OPENCODE_VERSION must pin the OpenCode runtime")
        resolved = shutil.which(self.command)
        if not resolved:
            raise OpenCodeCompatibilityError(f"OpenCode CLI not found: {self.command}")
        try:
            result = subprocess.run(
                [resolved, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                env=self._runtime_env(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OpenCodeCompatibilityError("OpenCode version preflight failed") from exc
        actual = result.stdout.strip()
        if result.returncode != 0 or actual != config.opencode_version:
            diagnostic = actual
            if not diagnostic:
                diagnostic = next(
                    (
                        line.strip()
                        for line in getattr(result, "stderr", "").splitlines()
                        if line.strip()
                    ),
                    f"exit code {result.returncode}",
                )
            raise OpenCodeCompatibilityError(
                "OpenCode runtime version mismatch "
                f"(expected {config.opencode_version!r}, got {diagnostic[:200]!r})"
            )
        return resolved, parse_mcp_read_allowlist(config.opencode_mcp_read_allowlist_json)

    def _session_exists(self, resolved: str, session_id: str) -> bool:
        """Return whether the persisted OpenCode runtime still knows a session."""

        env = self._execution_env()
        if config.opencode_config_path:
            env["OPENCODE_CONFIG"] = config.opencode_config_path
        try:
            result = subprocess.run(
                [
                    resolved,
                    "session",
                    "list",
                    "--format",
                    "json",
                    "--max-count",
                    "1000",
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 and session_id in result.stdout

    def _prepare_run(self, ctx: DocGenContext):
        from mcp_server.providers.custom.engineering_facts import EngineeringFactsService
        from mcp_server.providers.custom.models import HarnessRunState
        from mcp_server.providers.custom.prompt_packs import materialize_prompt_packs

        facts = self._facts_for_context(ctx)
        facts.write(ctx.staging_dir / "facts_manifest.json")
        EngineeringFactsService().extract_and_write(
            project_id=ctx.project_id,
            scan_output_dir=config.intodoc_output_dir / ctx.project_id,
            facts_manifest=facts,
            staging_dir=ctx.staging_dir,
            unit_allowlist=(facts.unit_names() if ctx.partition is not None else None),
        )
        materialize_prompt_packs(ctx.staging_dir, ctx.analysis_guide_file)
        harness = self._legacy_harness(facts)
        harness._apply_private_analysis_packs(ctx)
        state = HarnessRunState(project_id=ctx.project_id, facts_manifest=facts)
        harness._write_cross_cutting_facts(ctx, state)
        return facts, harness, state

    def _gather_contracts(
        self,
        ctx: DocGenContext,
        facts: Any,
        harness: Any,
        allowlist: dict[str, tuple[str, ...]],
    ) -> list[OpenCodeTaskContract]:
        from mcp_server.providers.custom.evidence_tasks import plan_evidence_tasks
        from mcp_server.providers.custom.investigator import SingleShotInvestigator
        from mcp_server.providers.custom.models import InvestigationReport
        from mcp_server.providers.custom.investigator import write_investigation_artifacts

        if harness._phase_policy("investigation") == "skip" or (
            facts.unit_count <= config.small_project_threshold and self.plugin_pack_dir is None
        ):
            return []

        preamble = ""
        if self.plugin_pack_dir is not None:
            preamble_path = self.plugin_pack_dir / "prompts" / "investigation.md"
            if preamble_path.exists():
                preamble = preamble_path.read_text(encoding="utf-8").rstrip()
        prompt_builder = SimpleNamespace(_preamble=preamble)
        raw_dir = ctx.staging_dir / "_transient" / "opencode" / "investigation_raw"
        task_dir = ctx.staging_dir / "_transient" / "opencode" / "evidence_tasks"
        contracts: list[OpenCodeTaskContract] = []
        connector_tools = tuple(
            f"{server}:{tool}" for server, tools in allowlist.items() for tool in tools
        )

        for group in ctx.preprocess_groups:
            group_file = ctx.staging_dir / "_preprocess" / f"{group}.md"
            group_units = [unit.name for unit in facts.units if unit.group == group]
            if not group_file.exists():
                continue
            content = group_file.read_text(encoding="utf-8", errors="replace")
            if len(content.strip()) < config.min_investigation_content_bytes:
                report = InvestigationReport(
                    group=group,
                    source_groups=[group],
                    skipped_units={
                        unit: "no source content in scanner output" for unit in group_units
                    },
                )
                write_investigation_artifacts(report, ctx.staging_dir / "_investigation")
                continue
            tasks = plan_evidence_tasks(
                group,
                group_file,
                group_units,
                task_dir,
                config.phase2_task_evidence_bytes,
            )
            for task in tasks:
                source_ref = task.path.relative_to(ctx.staging_dir).as_posix()
                output = raw_dir / f"{_safe_name(task.task_id)}.json"
                output_artifact = output.relative_to(ctx.staging_dir).as_posix()
                template_artifact, validation_spec, validation_receipt, validation_command = self._write_gather_guard(
                    ctx,
                    task_id=task.task_id,
                    output_artifact=output_artifact,
                    allowed_units=task.all_units,
                    source_ref=source_ref,
                )
                contracts.append(
                    OpenCodeTaskContract(
                        phase="gather",
                        target=f"investigation:{task.task_id}",
                        prompt_artifact="authoritative:SingleShotInvestigator._build_prompt",
                        prompt=SingleShotInvestigator._build_prompt(
                            prompt_builder,
                            group,
                            source_ref,
                            task.path.read_text(encoding="utf-8", errors="replace")[:60_000],
                            task.all_units,
                        ),
                        evidence_artifacts=(
                            source_ref,
                            "facts_manifest.json",
                            template_artifact,
                            validation_spec,
                        ),
                        output_artifact=output_artifact,
                        allowed_tools=connector_tools,
                        metadata={
                            "group": group,
                            "task_id": task.task_id,
                            "allowed_units": task.all_units,
                            "source_ref": source_ref,
                            "template_artifact": template_artifact,
                            "validation_spec": validation_spec,
                            "validation_receipt": validation_receipt,
                            "validation_command": validation_command,
                        },
                    )
                )
        return contracts

    @staticmethod
    def _write_gather_validator(ctx: DocGenContext) -> str:
        """Materialize a local, legacy-equivalent validation command for Gather.

        This is intentionally a workspace script rather than an MCP tool: it
        gives the OpenCode agent immediate, deterministic feedback without
        adding another Nexus model dispatch or altering the legacy prompt.
        """

        root = ctx.staging_dir / "_transient" / "opencode"
        root.mkdir(parents=True, exist_ok=True)
        path = root / "validate_gather_output.py"
        if not path.exists():
            path.write_text(
                """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

from mcp_server.providers.custom.investigator import EvidenceValidator, StagingToolbox, _extract_json_object
from mcp_server.providers.custom.models import FactsManifest

workspace = Path.cwd().resolve()
if len(sys.argv) != 3:
    raise SystemExit("usage: validate_gather_output.py OUTPUT_PATH SPEC_PATH")
output_path = (workspace / sys.argv[1]).resolve()
spec_path = (workspace / sys.argv[2]).resolve()
if workspace not in output_path.parents or workspace not in spec_path.parents:
    raise SystemExit("output and spec must be inside the workspace")
spec = json.loads(spec_path.read_text(encoding="utf-8"))
payload = json.loads(_extract_json_object(output_path.read_text(encoding="utf-8")))
facts = FactsManifest.model_validate_json((workspace / "facts_manifest.json").read_text(encoding="utf-8"))
validator = EvidenceValidator(facts, StagingToolbox(workspace))
report = validator.validate_report(
    str(spec["task_id"]), payload, set(spec["allowed_units"]),
    default_chunk_ref=str(spec["source_ref"]),
)
receipt_path = (workspace / str(spec["validation_receipt"])).resolve()
if workspace not in receipt_path.parents:
    raise SystemExit("validation receipt must be inside the workspace")
receipt_path.write_text(json.dumps({
    "task_id": spec["task_id"],
    "evidence_count": len(report.evidence),
    "validated": True,
}, indent=2) + "\\n", encoding="utf-8")
print(f"validated {len(report.evidence)} evidence item(s) for {spec['task_id']}")
""",
                encoding="utf-8",
            )
        return path.relative_to(ctx.staging_dir).as_posix()

    def _write_gather_guard(
        self,
        ctx: DocGenContext,
        *,
        task_id: str,
        output_artifact: str,
        allowed_units: list[str],
        source_ref: str,
    ) -> tuple[str, str, str, str]:
        """Write the schema template and validator spec for one evidence task."""

        guard_dir = ctx.staging_dir / "_transient" / "opencode" / "gather_guard"
        guard_dir.mkdir(parents=True, exist_ok=True)
        safe_task_id = _safe_name(task_id)
        template = guard_dir / f"{safe_task_id}.template.json"
        spec = guard_dir / f"{safe_task_id}.validation.json"
        receipt = guard_dir / f"{safe_task_id}.validated.json"
        template.write_text(
            json.dumps({"evidence": [], "skipped_units": {}}, indent=2) + "\n",
            encoding="utf-8",
        )
        spec.write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "allowed_units": allowed_units,
                    "source_ref": source_ref,
                    "validation_receipt": receipt.relative_to(ctx.staging_dir).as_posix(),
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        validator = self._write_gather_validator(ctx)
        template_ref = template.relative_to(ctx.staging_dir).as_posix()
        spec_ref = spec.relative_to(ctx.staging_dir).as_posix()
        command = f"python {validator} {output_artifact} {spec_ref}"
        receipt_ref = receipt.relative_to(ctx.staging_dir).as_posix()
        return template_ref, spec_ref, receipt_ref, command

    def _analyze_contracts(self, ctx: DocGenContext, facts: Any, harness: Any) -> list[OpenCodeTaskContract]:
        from mcp_server.providers.custom.prompts import group_analysis_prompt, scorecard_prompt

        required_docs = list(harness._required_docs)
        contracts: list[OpenCodeTaskContract] = []
        if harness._phase_policy("analysis") != "skip":
            for group in facts.groups:
                context = [
                    "facts_manifest.json",
                    f"_preprocess/{group}.md",
                    f"_investigation/{_safe_name(group)}.md",
                ]
                contracts.append(
                    OpenCodeTaskContract(
                        phase="analyze",
                        target=f"analysis:{group}",
                        prompt_artifact="authoritative:group_analysis_prompt",
                        prompt=group_analysis_prompt(
                            facts,
                            group,
                            plugin_pack_dir=self.plugin_pack_dir,
                            required_docs=required_docs,
                        ),
                        evidence_artifacts=tuple(
                            path for path in context if (ctx.staging_dir / path).exists()
                        ),
                        output_artifact=f"_analysis/{group}.md",
                    )
                )
        if harness._phase_policy("scorecard") != "skip":
            contracts.append(
                OpenCodeTaskContract(
                    phase="analyze",
                    target="scorecard",
                    prompt_artifact="authoritative:scorecard_prompt",
                    prompt=scorecard_prompt(facts, plugin_pack_dir=self.plugin_pack_dir),
                    evidence_artifacts=(
                        "facts_manifest.json", "_analysis", "_investigation",
                        "_assessment/prompt_packs/_scorecard_prompt_pack.md",
                    ),
                    output_artifact="_transient/opencode/scorecard_raw.md",
                )
            )
        return contracts

    def _doc_contract(self, ctx: DocGenContext, facts: Any, harness: Any, doc_name: str) -> OpenCodeTaskContract:
        from mcp_server.providers.custom.prompts import doc_prompt

        context_files = harness._phase3_context_files(ctx, doc_name)
        scorecard = ctx.staging_dir / "_assessment" / "scorecard.md"
        if scorecard.exists() and not harness._pack_contract_mode:
            context_files.append(scorecard)
        return OpenCodeTaskContract(
            phase="produce",
            target=f"document:{doc_name}",
            prompt_artifact="authoritative:doc_prompt",
            prompt=doc_prompt(
                facts,
                doc_name,
                plugin_pack_dir=self.plugin_pack_dir,
                extra_section_requirements=harness._doc_section_requirements_extra,
                scoped_unit=harness._per_job_doc_units.get(doc_name),
                nexus_presentation=ctx.nexus_presentation,
                private_analysis_instructions=harness._private_analysis_instructions.get(doc_name, ""),
                unit_reference=(
                    doc_name in harness._per_job_doc_units and harness._plugin_manifest is None
                ),
            ),
            evidence_artifacts=tuple(
                path.relative_to(ctx.staging_dir).as_posix()
                for path in context_files
                if path.exists()
            ),
            output_artifact=f"{doc_name}.md",
        )

    def _topology_contract(self, ctx: DocGenContext) -> OpenCodeTaskContract:
        from mcp_server.services.business_topology import load_business_topology_evidence
        from mcp_server.services.business_topology.context import build_topology_context_files
        from mcp_server.services.business_topology.prompt import business_topology_prompt

        bundle = load_business_topology_evidence(ctx.staging_dir / "_topology")
        context_files = build_topology_context_files(
            bundle,
            ctx.staging_dir,
            byte_cap=config.topology_context_evidence_bytes,
        )
        return OpenCodeTaskContract(
            phase="produce",
            target="business_topology",
            prompt_artifact="authoritative:business_topology_prompt",
            prompt=business_topology_prompt(
                project_id=ctx.project_id,
                scan_fingerprint=bundle.scan_fingerprint,
            ),
            evidence_artifacts=tuple(
                path.relative_to(ctx.staging_dir).as_posix()
                for path in context_files
                if path.exists()
            ),
            output_artifact="_topology/business_topology.json",
        )

    def _write_contracts(
        self,
        ctx: DocGenContext,
        step: str,
        contracts: list[OpenCodeTaskContract],
    ) -> str:
        root = ctx.staging_dir / "_transient" / "opencode"
        root.mkdir(parents=True, exist_ok=True)
        contract_dir = root / f"{_safe_name(step)}_contracts"
        contract_dir.mkdir(parents=True, exist_ok=True)
        index: list[dict[str, Any]] = []
        for position, contract in enumerate(contracts, start=1):
            contract_file = contract_dir / (
                f"{position:03d}__{_safe_name(contract.target)}.json"
            )
            contract_file.write_text(
                json.dumps(contract.to_wire(), indent=2), encoding="utf-8"
            )
            evidence_sizes = []
            for artifact in contract.evidence_artifacts:
                artifact_path = ctx.staging_dir / artifact
                try:
                    evidence_sizes.append(artifact_path.stat().st_size)
                except OSError:
                    evidence_sizes.append(0)
            index.append(
                {
                    "target": contract.target,
                    "contract_artifact": contract_file.relative_to(
                        ctx.staging_dir
                    ).as_posix(),
                    "output_artifact": contract.output_artifact,
                    # Workload hints let OpenCode choose an appropriate execution
                    # topology without loading every prompt/evidence file into the
                    # orchestrator context first.
                    "contract_bytes": contract_file.stat().st_size,
                    "evidence_artifact_count": len(contract.evidence_artifacts),
                    "evidence_bytes": sum(evidence_sizes),
                }
            )
        path = root / f"{_safe_name(step)}_contracts.json"
        path.write_text(
            json.dumps(index, indent=2),
            encoding="utf-8",
        )
        return path.relative_to(ctx.staging_dir).as_posix()

    def _phase_prompt(
        self,
        phase: str,
        step: str,
        contract_path: str,
        contract_count: int,
        allowlist: dict[str, tuple[str, ...]],
    ) -> str:
        connector_policy = ""
        if phase == "gather":
            connector_policy = (
                "Configured MCP tools may be used only when their server:tool identifier is listed "
                "in a task's allowed_tools. Treat connector results as supplementary evidence and "
                "never override scanner facts. For each Gather contract, read template_artifact and "
                "write output_artifact as that exact JSON object shape (with evidence and skipped_units), "
                "never as a generic job summary. After writing it, run validation_command from the "
                "contract. A Gather task is incomplete until that command exits successfully. "
            )
        return (
            f"Continue the Nexus documentation run in user-visible phase {phase.upper()} "
            f"(step {step}). Work only in the current workspace. "
            f"Read {contract_path}; it is an ordered index of {contract_count} task contract(s). "
            "Act as the OpenCode orchestration agent and choose the execution topology dynamically. "
            "The index exposes contract_bytes, evidence_artifact_count, and evidence_bytes so you can "
            "judge workload before opening task content. For a small workload that fits comfortably "
            "in context, completing contracts directly is valid. When the workload is large, contains "
            "many independent contracts, or would crowd the orchestrator context, use OpenCode's native "
            "sub-agents to isolate work and fan out only as much as is useful. Do not use a fixed worker "
            "count, and avoid needless delegation, redundant work, and unbounded nested fan-out. The "
            "current step already separates cross-step dependencies; contracts in this index may be "
            "completed independently unless a contract explicitly says otherwise. Keep orchestration "
            "context efficient: when delegating, pass the contract_artifact path instead of copying its "
            "prompt or evidence into the parent conversation. Whether handled directly or delegated, "
            "complete every contract exactly once: read that contract, follow its exact prompt without "
            "paraphrasing, use only its listed evidence_artifacts, do not search/grep/glob or explore "
            "other workspace files, and write output_artifact. Confirm the required output exists and, "
            "for Gather, its "
            "validation_command succeeded and validation_receipt exists before continuing. "
            "Do not put Markdown fences around JSON outputs. Do not publish, deploy, change "
            "credentials, or write outside the workspace. Do not repeat a task that Nexus has "
            "already listed once. If a required task cannot complete, stop and report failure. "
            + connector_policy
            + "When every listed output exists, briefly confirm completion and stop."
        )

    def _command(self, resolved: str, ctx: DocGenContext, session_id: str | None = None) -> list[str]:
        cmd = [resolved, "run", "--format", "json"]
        if self.model_route:
            cmd.extend(["--model", self.model_route])
        cmd.extend(["--dir", str(ctx.staging_dir)])
        if config.opencode_primary_agent:
            cmd.extend(["--agent", config.opencode_primary_agent])
        if session_id:
            cmd.extend(["--session", session_id])
        if config.opencode_auto_approve:
            cmd.append("--auto")
        return cmd

    @staticmethod
    def _parse_json_event(event: dict[str, Any]) -> dict[str, Any] | None:
        kind = str(event.get("type") or "")
        if kind not in {"step_start", "text", "step_finish", "error"}:
            return None
        normalized: dict[str, Any] = {"type": kind}
        session_id = event.get("sessionID") or event.get("session_id")
        if isinstance(session_id, str):
            normalized["session_id"] = session_id
        part = event.get("part")
        if isinstance(part, dict) and isinstance(part.get("type"), str):
            normalized["part_type"] = part["type"]
        if kind == "error":
            normalized["has_error"] = True
        return normalized

    @staticmethod
    def _event_usage(event: dict[str, Any]) -> tuple[int, int, float]:
        part = event.get("part")
        if not isinstance(part, dict):
            return 0, 0, 0.0
        tokens = part.get("tokens")
        if not isinstance(tokens, dict):
            tokens = {}
        input_tokens = int(tokens.get("input") or 0)
        output_tokens = int(tokens.get("output") or 0)
        return input_tokens, output_tokens, float(part.get("cost") or 0.0)

    @staticmethod
    def _live_event_summary(
        event: dict[str, Any],
        *,
        phase: str,
        step: str,
        staging_dir: Path,
    ) -> tuple[str, str] | None:
        """Return a safe UI log line without exposing prompts or model content."""

        prefix = f"OpenCode {phase}/{step}"
        kind = str(event.get("type") or "")
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        part_type = str(part.get("type") or "")
        if kind == "step_start" or part_type == "step-start":
            return f"{prefix}: model turn started", "info"
        if kind == "step_finish" or part_type == "step-finish":
            tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
            cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
            return (
                f"{prefix}: model turn complete "
                f"(input={int(tokens.get('input') or 0)} "
                f"output={int(tokens.get('output') or 0)} "
                f"reasoning={int(tokens.get('reasoning') or 0)} "
                f"cache_read={int(cache.get('read') or 0)} "
                f"cache_write={int(cache.get('write') or 0)})",
                "info",
            )
        if kind == "error":
            return f"{prefix}: OpenCode emitted an error event (details redacted)", "error"
        if kind == "text" or part_type == "text":
            return f"{prefix}: agent response received", "info"

        tool = event.get("tool") or part.get("tool")
        state = event.get("state") or part.get("state")
        if not isinstance(tool, str) or not isinstance(state, dict):
            return None
        status = str(state.get("status") or "updated")
        target = ""
        tool_input = state.get("input")
        if isinstance(tool_input, dict) and tool in {"read", "write", "edit"}:
            raw_path = tool_input.get("filePath") or tool_input.get("path")
            if isinstance(raw_path, str):
                try:
                    resolved = Path(raw_path).resolve()
                    if resolved == staging_dir.resolve() or staging_dir.resolve() in resolved.parents:
                        target = f" {resolved.relative_to(staging_dir.resolve()).as_posix()}"
                except (OSError, ValueError):
                    target = ""
        return f"{prefix}: tool {tool} {status}{target}", "info"

    async def _execute_step(
        self,
        *,
        resolved: str,
        ctx: DocGenContext,
        phase: str,
        step: str,
        contracts: list[OpenCodeTaskContract],
        allowlist: dict[str, tuple[str, ...]],
        session_id: str | None,
        ledger: OpenCodeAttemptLedger,
        log_cb: LogCallback,
    ) -> _PhaseExecution:
        step_started = time.monotonic()
        ledger_target = f"step:{step}"
        ledger.dispatch(ledger_target)
        contract_path = self._write_contracts(ctx, step, contracts)
        prompt = self._phase_prompt(phase, step, contract_path, len(contracts), allowlist)
        env = self._execution_env()
        if config.opencode_config_path:
            env["OPENCODE_CONFIG"] = config.opencode_config_path
        cmd = self._command(resolved, ctx, session_id)
        await log_cb(ctx.project_id, f"OpenCode {phase}: {step} started", "info")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(ctx.staging_dir),
            env=env,
            limit=4 * 1024 * 1024,
        )

        events: list[dict[str, Any]] = []
        actual_session = session_id
        input_tokens = output_tokens = 0
        cost_usd = 0.0
        saw_error = False
        stderr_seen = False

        async def consume_stdout() -> None:
            nonlocal actual_session, input_tokens, output_tokens, cost_usd, saw_error
            assert proc.stdout is not None
            while raw_line := await proc.stdout.readline():
                try:
                    raw_event = json.loads(raw_line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                parsed = self._parse_json_event(raw_event)
                if parsed is not None:
                    events.append(parsed)
                    actual_session = parsed.get("session_id") or actual_session
                    saw_error = saw_error or parsed["type"] == "error"
                used_in, used_out, used_cost = self._event_usage(raw_event)
                input_tokens += used_in
                output_tokens += used_out
                cost_usd += used_cost
                summary = self._live_event_summary(
                    raw_event,
                    phase=phase,
                    step=step,
                    staging_dir=ctx.staging_dir,
                )
                if summary is not None:
                    await log_cb(ctx.project_id, summary[0], summary[1])

        async def consume_stderr() -> None:
            nonlocal stderr_seen
            assert proc.stderr is not None
            while raw_line := await proc.stderr.readline():
                if raw_line.strip():
                    stderr_seen = True

        stdout_task = asyncio.create_task(consume_stdout())
        stderr_task = asyncio.create_task(consume_stderr())
        assert proc.stdin is not None
        proc.stdin.write(prompt.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        try:
            await asyncio.wait_for(
                proc.wait(),
                timeout=max(1, config.opencode_timeout_seconds),
            )
        except (TimeoutError, asyncio.CancelledError):
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                proc.kill()
                await proc.wait()
            await asyncio.gather(stdout_task, stderr_task)
            raise
        await asyncio.gather(stdout_task, stderr_task)
        if stderr_seen:
            await log_cb(ctx.project_id, "[opencode] stderr emitted (content redacted)", "warning")
        if proc.returncode != 0:
            if not events:
                ledger.release_unstarted(ledger_target)
            raise OpenCodeCompatibilityError(
                f"OpenCode {step} exited with code {proc.returncode}"
            )
        if saw_error:
            raise OpenCodeCompatibilityError(f"OpenCode {step} emitted an error event")
        if not actual_session:
            raise OpenCodeCompatibilityError(f"OpenCode {step} returned no session identifier")
        required_artifacts = [contract.output_artifact for contract in contracts]
        if phase == "gather":
            required_artifacts.extend(
                str(contract.metadata["validation_receipt"])
                for contract in contracts
                if contract.metadata.get("validation_receipt")
            )
        missing = [
            artifact for artifact in required_artifacts
            if not (ctx.staging_dir / artifact).is_file()
        ]
        if missing:
            raise OpenCodeCompatibilityError(
                f"OpenCode {step} did not write required outputs or validation receipts: "
                f"{', '.join(missing[:5])}"
            )
        await log_cb(ctx.project_id, f"OpenCode {phase}: {step} complete", "info")
        return _PhaseExecution(
            session_id=actual_session,
            events=events,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=int((time.monotonic() - step_started) * 1000),
        )

    def _record_execution(
        self,
        state: Any,
        phase: str,
        step: str,
        execution: _PhaseExecution,
    ) -> None:
        """Mirror OpenCode turns into the legacy provider-call audit schema."""

        from mcp_server.providers.custom.models import ModelResult, ProviderCallLog

        state.calls.append(
            ProviderCallLog(
                phase=phase,
                target=step,
                result=ModelResult(
                    success=True,
                    model_name=self.model_route or "opencode-default",
                    input_tokens=execution.input_tokens,
                    output_tokens=execution.output_tokens,
                    latency_ms=execution.latency_ms,
                    metadata={
                        "runtime": "opencode-cli",
                        "session_id": execution.session_id,
                        "cost_usd": execution.cost_usd,
                    },
                ),
            )
        )

    @staticmethod
    def _write_usage_receipts(audit_dir: Path, receipts: list[dict[str, Any]]) -> None:
        """Persist safe per-step usage even when a later validation gate fails."""

        (audit_dir / "opencode_usage.json").write_text(
            json.dumps(receipts, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _read_usage_receipts(audit_dir: Path) -> list[dict[str, Any]]:
        path = audit_dir / "opencode_usage.json"
        if not path.is_file():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    @staticmethod
    def _contracts_complete(
        ctx: DocGenContext,
        phase: str,
        contracts: list[OpenCodeTaskContract],
    ) -> bool:
        required = [contract.output_artifact for contract in contracts]
        if phase == "gather":
            required.extend(
                str(contract.metadata["validation_receipt"])
                for contract in contracts
                if contract.metadata.get("validation_receipt")
            )
        return all((ctx.staging_dir / artifact).is_file() for artifact in required)

    def _finalize_gather(
        self,
        ctx: DocGenContext,
        facts: Any,
        harness: Any,
        state: Any,
        contracts: list[OpenCodeTaskContract],
    ) -> None:
        from mcp_server.providers.custom.investigator import (
            EvidenceValidator,
            StagingToolbox,
            _extract_json_object,
            investigation_summary,
            write_investigation_artifacts,
        )
        from mcp_server.providers.custom.models import InvestigationReport
        from mcp_server.providers.custom.validators import validate_investigation_coverage

        reports_by_group: dict[str, list[InvestigationReport]] = defaultdict(list)
        validator = EvidenceValidator(facts, StagingToolbox(ctx.staging_dir))
        for contract in contracts:
            raw = (ctx.staging_dir / contract.output_artifact).read_text(
                encoding="utf-8", errors="replace"
            )
            try:
                payload = json.loads(_extract_json_object(raw))
                report = validator.validate_report(
                    str(contract.metadata["task_id"]),
                    payload,
                    set(contract.metadata["allowed_units"]),
                    default_chunk_ref=str(contract.metadata["source_ref"]),
                )
            except Exception as exc:
                raise OpenCodeCompatibilityError(
                    f"OpenCode investigation output is invalid for {contract.target}: {exc}"
                ) from exc
            group = str(contract.metadata["group"])
            reports_by_group[group].append(report)

        investigation_dir = ctx.staging_dir / "_investigation"
        for group in ctx.preprocess_groups:
            reports = reports_by_group.get(group, [])
            if reports:
                merged = harness._merge_investigation_reports(group, reports)
                write_investigation_artifacts(merged, investigation_dir)
                state.investigation_reports.append(merged)
                continue
            existing = investigation_dir / f"{_safe_name(group)}.json"
            if existing.exists():
                state.investigation_reports.append(
                    InvestigationReport.model_validate_json(existing.read_text(encoding="utf-8"))
                )

        if harness._phase_policy("investigation") == "skip" or (
            facts.unit_count <= config.small_project_threshold and self.plugin_pack_dir is None
        ):
            investigation_dir.mkdir(parents=True, exist_ok=True)
            (investigation_dir / "_summary.md").write_text(
                "# Investigation Summary\n\nInvestigation skipped by the current legacy policy.\n",
                encoding="utf-8",
            )
            return
        coverage = validate_investigation_coverage(state.investigation_reports, facts)
        investigation_dir.mkdir(parents=True, exist_ok=True)
        (investigation_dir / "_coverage_report.json").write_text(
            coverage.model_dump_json(indent=2), encoding="utf-8"
        )
        if not coverage.ok or coverage.coverage_count != facts.unit_count:
            raise OpenCodeCompatibilityError(
                "OpenCode investigation coverage failed: "
                + "; ".join(coverage.errors[:3] or coverage.missing_units[:3])
            )
        (investigation_dir / "_summary.md").write_text(
            "# Investigation Summary\n\n" + investigation_summary(state.investigation_reports),
            encoding="utf-8",
        )

    async def _finalize_analyze(
        self,
        ctx: DocGenContext,
        facts: Any,
        harness: Any,
        state: Any,
        log_cb: LogCallback,
    ) -> None:
        from mcp_server.providers.custom.pipeline.evidence_quality_gate import EvidenceQualityGate
        from mcp_server.providers.custom.validators import (
            validate_analysis_coverage,
            validate_analysis_depth,
            validate_analysis_guide_sections,
            validate_scorecard_contract,
        )
        from mcp_server.services.business_topology import build_business_topology_evidence

        analysis_dir = ctx.staging_dir / "_analysis"
        if harness._phase_policy("analysis") == "skip":
            harness._phase2_pack_contract_records(ctx, state)
        else:
            # The authoritative Phase 2 prompt returns only the model-analysis
            # body. Preserve the legacy Python-owned coverage wrapper and
            # structured coverage receipt before running its validators.
            for group in facts.groups:
                path = analysis_dir / f"{group}.md"
                if not path.exists():
                    raise OpenCodeCompatibilityError(
                        f"OpenCode analysis output is missing for group {group}"
                    )
                body = path.read_text(encoding="utf-8", errors="replace").strip()
                units = [unit.name for unit in facts.units if unit.group == group]
                if body.startswith(f"# Phase 2 Analysis: {group}") and (
                    "\n## Model Analysis\n" in body
                ):
                    # Resume can re-enter deterministic validation after a later
                    # phase failed. Do not wrap finalized analysis twice.
                    harness._write_phase2_coverage_record(ctx, group, units, path)
                    continue
                coverage_block = "\n".join(f"- COVERED: {unit}" for unit in units)
                path.write_text(
                    f"# Phase 2 Analysis: {group}\n\n"
                    "## Coverage\n\n"
                    f"{coverage_block}\n\n"
                    "## Model Analysis\n\n"
                    f"{body}\n",
                    encoding="utf-8",
                )
                harness._write_phase2_coverage_record(ctx, group, units, path)
        coverage = validate_analysis_coverage(
            analysis_dir, facts, use_structured=harness._plugin_mode
        )
        depth = validate_analysis_depth(
            analysis_dir,
            ctx.staging_dir / "_preprocess",
            facts,
            use_structured=harness._plugin_mode,
        )
        guide = validate_analysis_guide_sections(
            analysis_dir,
            facts,
            require_group_summary=not harness._plugin_mode,
        )
        for name, report in (
            ("_coverage_report.json", coverage),
            ("_depth_report.json", depth),
            ("_guide_sections_report.json", guide),
        ):
            (analysis_dir / name).write_text(report.model_dump_json(indent=2), encoding="utf-8")
        if coverage.coverage_count == 0 and facts.unit_count:
            raise OpenCodeCompatibilityError(
                "OpenCode analysis covered zero canonical units: "
                + "; ".join(coverage.errors[:3])
            )
        if not depth.ok:
            state.warnings.extend(depth.errors)
        if not guide.ok:
            state.warnings.extend(guide.errors)

        raw_scorecard = ctx.staging_dir / "_transient" / "opencode" / "scorecard_raw.md"
        assessment = ctx.staging_dir / "_assessment"
        assessment.mkdir(parents=True, exist_ok=True)
        if raw_scorecard.exists():
            text = raw_scorecard.read_text(encoding="utf-8", errors="replace").strip()
            check = validate_scorecard_contract(text)
            if not check["ok"]:
                state.warnings.extend(check["errors"])
            (assessment / "scorecard.md").write_text(
                "# Assessment Scorecard\n\n"
                f"Canonical inventory: {facts.unit_count} {facts.canonical_unit_label}.\n\n"
                f"{text}\n",
                encoding="utf-8",
            )
        elif harness._phase_policy("scorecard") == "skip" and self.plugin_pack_dir is None:
            harness._write_omitted_scorecard_marker(ctx, state)

        if harness._phase_policy("doc_bundles") != "skip":
            harness._phase28_bundles(ctx, facts)
        harness._write_documentation_obligations(ctx, facts)
        await EvidenceQualityGate().run(ctx, state, log_cb)
        build_business_topology_evidence(
            project_id=ctx.project_id,
            scan_output_dir=config.intodoc_output_dir / ctx.project_id,
            staging_dir=ctx.staging_dir,
            unit_allowlist=(facts.unit_names() if ctx.partition is not None else None),
        )

    async def _finalize_produce(
        self,
        ctx: DocGenContext,
        facts: Any,
        harness: Any,
        state: Any,
        log_cb: LogCallback,
    ) -> list[str]:
        from mcp_server.providers.custom.evidence_traceability import EvidenceTraceabilityValidator
        from mcp_server.providers.custom.models import CoverageReport
        from mcp_server.providers.custom.pipeline.phases.phase3_docs import _thin_bundle_notice
        from mcp_server.providers.custom.pipeline.helpers import HarnessHelpersImpl
        from mcp_server.providers.custom.pipeline.phases.phase4_cleanup import Phase4Cleanup
        from mcp_server.providers.custom.validators import (
            validate_doc_depth,
            validate_docs,
            validate_source_provenance,
        )
        from mcp_server.services.business_topology import (
            build_deterministic_topology,
            load_business_topology_evidence,
            parse_validate_repair_business_topology,
            validate_business_topology,
        )

        required = list(harness._required_docs)
        for doc_name in required:
            path = ctx.staging_dir / f"{doc_name}.md"
            if path.exists():
                path.write_text(
                    harness._final_doc_content(
                        state,
                        doc_name,
                        path.read_text(encoding="utf-8", errors="replace"),
                    ),
                    encoding="utf-8",
                )
        if state.thin_bundle_groups:
            notice = _thin_bundle_notice(state.thin_bundle_groups)
            for doc_name in required:
                path = ctx.staging_dir / f"{doc_name}.md"
                if path.exists():
                    path.write_text(
                        notice + "\n\n" + path.read_text(encoding="utf-8"),
                        encoding="utf-8",
                    )

        if state.investigation_reports:
            tracer = EvidenceTraceabilityValidator(
                investigation_reports=state.investigation_reports,
                facts=facts,
                investigation_dir=ctx.staging_dir / "_investigation",
            )
            trace_root = ctx.staging_dir / "_assessment"
            for doc_name in required:
                path = ctx.staging_dir / f"{doc_name}.md"
                if not path.exists():
                    continue
                report = tracer.validate(
                    doc_name, path.read_text(encoding="utf-8", errors="replace")
                )
                if report.phantom_absences:
                    (trace_root / f"traceability_{doc_name}.json").write_text(
                        json.dumps(
                            {
                                "doc_name": doc_name,
                                "phantom_absences": [
                                    {
                                        "unit_name": item.unit_name,
                                        "matched_phrase": item.matched_phrase,
                                        "evidence_path": item.evidence_path,
                                    }
                                    for item in report.phantom_absences
                                ],
                            },
                            indent=2,
                        ),
                        encoding="utf-8",
                    )

        harness._reconcile_sql_usage_classification(ctx)
        assessment = ctx.staging_dir / "_assessment"
        doc_report = validate_docs(
            ctx.staging_dir,
            facts,
            required_docs=required,
            enforce_default_contract=not harness._plugin_mode,
        )
        depth_report = validate_doc_depth(
            ctx.staging_dir,
            facts,
            required_docs=required,
            enforce_default_contract=not harness._plugin_mode,
        )
        (assessment / "doc_validation_report.json").write_text(
            doc_report.model_dump_json(indent=2), encoding="utf-8"
        )
        (assessment / "doc_depth_report.json").write_text(
            depth_report.model_dump_json(indent=2), encoding="utf-8"
        )
        if not doc_report.ok:
            raise OpenCodeCompatibilityError(
                "OpenCode required-document validation failed: "
                + "; ".join(doc_report.errors[:3])
            )
        if not depth_report.ok:
            state.warnings.extend(depth_report.errors)

        provenance: CoverageReport | None = None
        if ctx.enterprise_mode and harness._pack_contract_mode:
            provenance = CoverageReport(
                ok=True,
                coverage_count=facts.unit_count,
                expected_count=facts.unit_count,
                warnings=["Default source provenance gate skipped for plugin-pack contract run."],
            )
        elif ctx.enterprise_mode:
            provenance = validate_source_provenance(
                ctx.staging_dir,
                facts,
                required_docs=required,
                enforce_default_contract=not harness._plugin_mode,
            )
        if provenance is not None:
            (assessment / "source_provenance_report.json").write_text(
                provenance.model_dump_json(indent=2), encoding="utf-8"
            )

        topology_bundle = load_business_topology_evidence(ctx.staging_dir / "_topology")
        topology_path = ctx.staging_dir / "_topology" / "business_topology.json"
        topology = None
        report = None
        if topology_path.exists():
            topology, report, notes = parse_validate_repair_business_topology(
                topology_path.read_text(encoding="utf-8", errors="replace"),
                bundle=topology_bundle,
                project_id=ctx.project_id,
            )
            if topology is not None and notes:
                topology_path.write_text(topology.model_dump_json(indent=2), encoding="utf-8")
        if topology is None or report is None or not report.validated:
            topology = build_deterministic_topology(topology_bundle, project_id=ctx.project_id)
            report = validate_business_topology(
                topology, bundle=topology_bundle, project_id=ctx.project_id
            )
            topology_path.write_text(topology.model_dump_json(indent=2), encoding="utf-8")
            (ctx.staging_dir / "_topology" / "state.json").write_text(
                json.dumps({"state": "deterministic_fallback"}, indent=2), encoding="utf-8"
            )
        (ctx.staging_dir / "_topology" / "validation_report.json").write_text(
            report.model_dump_json(indent=2), encoding="utf-8"
        )
        if not report.validated:
            raise OpenCodeCompatibilityError("OpenCode business topology validation failed")

        helpers = HarnessHelpersImpl(harness)
        gate_report = harness._enterprise_gate_report(ctx, state, provenance=provenance)
        await harness._write_audit_bundle(ctx, state, gate_report)
        await Phase4Cleanup(helpers, provider_id="opencode-cli").run(ctx, state, log_cb)
        return required

    async def run(self, ctx: DocGenContext, log_cb: LogCallback) -> AgentResult:
        audit_dir = ctx.staging_dir / "_audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        ledger = OpenCodeAttemptLedger(ctx.staging_dir)
        started = int(time.time() * 1000)
        all_events: list[dict[str, Any]] = []
        phase_receipts = (
            self._read_usage_receipts(audit_dir)
            if ctx.resume_state is not None
            else []
        )
        session_id = next(
            (
                str(receipt["session_id"])
                for receipt in reversed(phase_receipts)
                if receipt.get("session_id")
            ),
            None,
        )
        active_phase: str | None = None
        active_step: str | None = None
        total_input = sum(int(item.get("input_tokens") or 0) for item in phase_receipts)
        total_output = sum(int(item.get("output_tokens") or 0) for item in phase_receipts)
        total_cost = sum(float(item.get("cost_usd") or 0.0) for item in phase_receipts)
        turns = len(phase_receipts)

        def record_execution(phase: str, step: str, execution: _PhaseExecution) -> None:
            """Record model-reported usage before deterministic validation runs."""

            nonlocal session_id, total_input, total_output, total_cost, turns, active_phase, active_step
            session_id = execution.session_id
            all_events.extend(execution.events)
            total_input += execution.input_tokens
            total_output += execution.output_tokens
            total_cost += execution.cost_usd
            turns += 1
            phase_receipts.append({
                "phase": phase,
                "step": step,
                "status": "model_completed",
                "session_id": execution.session_id,
                "input_tokens": execution.input_tokens,
                "output_tokens": execution.output_tokens,
                "cost_usd": execution.cost_usd,
                "latency_ms": execution.latency_ms,
            })
            self._write_usage_receipts(audit_dir, phase_receipts)
            self._record_execution(state, phase, step, execution)
            active_phase = None
            active_step = None

        try:
            resolved, allowlist = self._preflight()
            if session_id and not self._session_exists(resolved, session_id):
                await log_cb(
                    ctx.project_id,
                    "OpenCode RESUME: prior runtime session is unavailable; "
                    "continuing incomplete work in a fresh orchestrator session",
                    "info",
                )
                session_id = None
            facts, harness, state = self._prepare_run(ctx)

            gather = self._gather_contracts(ctx, facts, harness, allowlist)
            if ctx.resume_state is not None and self._contracts_complete(
                ctx, "gather", gather
            ):
                await log_cb(
                    ctx.project_id,
                    "OpenCode RESUME: gather outputs already complete; validating checkpoint",
                    "info",
                )
            else:
                active_phase, active_step = "gather", "gather"
                execution = await self._execute_step(
                    resolved=resolved,
                    ctx=ctx,
                    phase="gather",
                    step="gather",
                    contracts=gather,
                    allowlist=allowlist,
                    session_id=session_id,
                    ledger=ledger,
                    log_cb=log_cb,
                )
                record_execution("gather", "gather", execution)
            self._finalize_gather(ctx, facts, harness, state, gather)

            analyze = self._analyze_contracts(ctx, facts, harness)
            if ctx.resume_state is not None and self._contracts_complete(
                ctx, "analyze", analyze
            ):
                await log_cb(
                    ctx.project_id,
                    "OpenCode RESUME: analysis outputs already complete; validating checkpoint",
                    "info",
                )
            else:
                active_phase, active_step = "analyze", "analyze"
                execution = await self._execute_step(
                    resolved=resolved,
                    ctx=ctx,
                    phase="analyze",
                    step="analyze",
                    contracts=analyze,
                    allowlist=allowlist,
                    session_id=session_id,
                    ledger=ledger,
                    log_cb=log_cb,
                )
                record_execution("analyze", "analyze", execution)
            await self._finalize_analyze(ctx, facts, harness, state, log_cb)

            docs = list(harness._required_docs)
            executive = "09_EXECUTIVE_SUMMARY"
            unit_docs = [
                doc for doc in docs
                if doc in harness._per_job_doc_units and doc != executive
            ]
            group_docs = [
                doc for doc in docs
                if doc not in harness._per_job_doc_units and doc != executive
            ]
            produce_batches: list[tuple[str, list[str] | None]] = []
            if unit_docs:
                produce_batches.append(("produce_unit_docs", unit_docs))
            if group_docs:
                produce_batches.append(("produce_group_docs", group_docs))
            if executive in docs and not harness._plugin_mode:
                produce_batches.append(("produce_executive_summary", [executive]))
            produce_batches.append(("produce_topology", None))

            for step, doc_names in produce_batches:
                # Build each batch only after the previous one has completed so
                # unit-doc digests and completed-doc context are actually present.
                contracts = (
                    [self._topology_contract(ctx)]
                    if doc_names is None
                    else [self._doc_contract(ctx, facts, harness, doc) for doc in doc_names]
                )
                if ctx.resume_state is not None and self._contracts_complete(
                    ctx, "produce", contracts
                ):
                    await log_cb(
                        ctx.project_id,
                        f"OpenCode RESUME: {step} outputs already complete; skipping model work",
                        "info",
                    )
                else:
                    active_phase, active_step = "produce", step
                    execution = await self._execute_step(
                        resolved=resolved,
                        ctx=ctx,
                        phase="produce",
                        step=step,
                        contracts=contracts,
                        allowlist=allowlist,
                        session_id=session_id,
                        ledger=ledger,
                        log_cb=log_cb,
                    )
                    record_execution("produce", step, execution)
                if step == "produce_unit_docs":
                    harness._build_unit_docs_digest(ctx, unit_docs)

            required = await self._finalize_produce(ctx, facts, harness, state, log_cb)
            receipts = [
                {"phase": phase, "status": "verified", "session_id": session_id}
                for phase in PHASES
            ]
            (audit_dir / "opencode_phase_receipts.json").write_text(
                json.dumps(receipts, indent=2), encoding="utf-8"
            )
            (audit_dir / "opencode_events.json").write_text(
                json.dumps(all_events, indent=2), encoding="utf-8"
            )
            return AgentResult(
                success=True,
                input_tokens=total_input,
                output_tokens=total_output,
                cost_usd=total_cost or None,
                duration_ms=int(time.time() * 1000) - started,
                num_turns=turns,
                session_id=session_id,
                model_name=self.model_route or "opencode-default",
                write_model_name=self.model_route or "opencode-default",
                required_docs=required,
                has_warnings=bool(state.warnings),
            )
        except asyncio.CancelledError:
            if active_phase is not None:
                phase_receipts.append({
                    "phase": active_phase,
                    "step": active_step,
                    "status": "cancelled",
                    "usage_available": False,
                    "note": "OpenCode was stopped before it emitted a final usage event.",
                })
            self._write_usage_receipts(audit_dir, phase_receipts)
            raise
        except (OpenCodeCompatibilityError, OpenCodeAttemptError) as exc:
            error = str(exc)
        except Exception as exc:
            logger.exception("OpenCode adapter failure")
            error = f"OpenCode provider error [{type(exc).__name__}]"
        (audit_dir / "opencode_failure.json").write_text(
            json.dumps({"error": error, "attempts": ledger.attempts}, indent=2),
            encoding="utf-8",
        )
        self._write_usage_receipts(audit_dir, phase_receipts)
        await log_cb(ctx.project_id, error, "error")
        return AgentResult(
            success=False,
            error=error,
            model_name=self.model_route or "opencode-default",
            write_model_name=self.model_route or "opencode-default",
            session_id=session_id,
            duration_ms=int(time.time() * 1000) - started,
        )
