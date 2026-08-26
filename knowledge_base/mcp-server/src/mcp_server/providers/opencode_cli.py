"""OpenCode CLI provider for the modern three-phase documentation workflow.

The coordinator performs the same deterministic preprocessing as every other
provider. Gather and Analyze use bounded task sessions whose validated files are
the durable handoff; Produce consumes those deterministic legacy artifacts.
Task prompts come from the current custom-pipeline renderers; the retired Claude
CLI mega-prompt is never used.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from mcp_server.config import config
from mcp_server.providers.base import AgentResult, DocGenContext, DocGenerationProvider, LogCallback

logger = logging.getLogger(__name__)

PHASES = ("gather", "analyze", "produce")
_SENSITIVE_EVENT_KEYS = frozenset(
    {
        "prompt",
        "reasoning",
        "content",
        "payload",
        "config",
        "env",
        "environment",
        "token",
        "api_key",
        "authorization",
        "secret",
    }
)
_LEGACY_MUTATING_TOOL_PREFIXES = ("write", "edit", "delete", "update", "create", "publish")


class OpenCodeCompatibilityError(RuntimeError):
    """Raised when the configured OpenCode runtime cannot execute safely."""


class OpenCodeAttemptError(RuntimeError):
    """Raised when Nexus tries to dispatch the same orchestration step twice."""


# Diagnostics budget for a failed OpenCode step. The full raw error events land
# in ``_audit/opencode_errors.json``; only a short reason reaches the run log and
# the exception, because a provider message can quote a fragment of the request.
_MAX_LOG_REASON_CHARS = 300
_MAX_PERSISTED_ERROR_EVENTS = 20
_MAX_PERSISTED_STDERR_CHARS = 8000
# Keys an OpenCode/ai-sdk error event puts its human-readable text under.
_ERROR_TEXT_KEYS = ("message", "error", "name", "detail", "reason")
_ESTIMATED_BYTES_PER_TOKEN = 4
_GATHER_EXHAUSTED_GAP_REASON = "OpenCode scope ended before this unit was evidenced"


def _error_reason(event: Any, *, limit: int = _MAX_LOG_REASON_CHARS) -> str:
    """Return a compact one-line reason from an OpenCode error event.

    The event shape is provider-dependent (``error.error``, ``error.data.message``,
    a bare string, ...), so this walks the structure for the known text keys and
    takes the longest hit — in practice the actual provider message rather than an
    exception class name.
    """

    found: list[str] = []

    def walk(node: Any, depth: int) -> None:
        if depth > 6 or len(found) >= 8:
            return
        if isinstance(node, str):
            if node.strip():
                found.append(node.strip())
            return
        if isinstance(node, dict):
            for key in _ERROR_TEXT_KEYS:
                value = node.get(key)
                if isinstance(value, str) and value.strip():
                    found.append(value.strip())
            for key, value in node.items():
                if key in _ERROR_TEXT_KEYS and isinstance(value, str):
                    continue
                if isinstance(value, (dict, list)):
                    walk(value, depth + 1)
            return
        if isinstance(node, list):
            for item in node[:8]:
                walk(item, depth + 1)

    walk(event, 0)
    if not found:
        return ""
    reason = " ".join(max(found, key=len).split())
    return reason[:limit]


@dataclass(frozen=True)
class OpenCodePhaseConfig:
    """An isolated, per-invocation OpenCode config kept outside staging."""

    path: Path
    environment: dict[str, str]

    def cleanup(self) -> None:
        shutil.rmtree(self.path.parent, ignore_errors=True)


@dataclass
class OpenCodeAttemptLedger:
    """Persist Nexus-owned dispatch counts for one database generation run.

    Staging survives a failed run so a later user-triggered run can resume its
    accepted artifacts.  The attempt ceiling must therefore be scoped to
    ``run_id``; otherwise the old run's dispatch permanently prevents the new
    run from completing an interrupted step.
    """

    staging_dir: Path
    run_id: int | None = None
    attempts: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        path = self.staging_dir / "_audit" / "opencode_attempt_ledger.json"
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            persisted_run_id = payload.get("run_id") if isinstance(payload, dict) else None
            if self.run_id is not None and persisted_run_id != self.run_id:
                return
            persisted = payload.get("attempts") if isinstance(payload, dict) else None
            if isinstance(persisted, dict) and all(
                isinstance(key, str) and isinstance(value, int) for key, value in persisted.items()
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
            json.dumps(
                {
                    "max_nexus_attempts": 1,
                    "run_id": self.run_id,
                    "attempts": self.attempts,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def release_unstarted(self, target: str) -> None:
        """Do not count a launcher failure as a model attempt."""

        if self.attempts.pop(target, None) is None:
            return
        path = self.staging_dir / "_audit" / "opencode_attempt_ledger.json"
        path.write_text(
            json.dumps(
                {
                    "max_nexus_attempts": 1,
                    "run_id": self.run_id,
                    "attempts": self.attempts,
                },
                indent=2,
            ),
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


@dataclass(frozen=True)
class _ContractBatch:
    """One adaptively sized, fresh OpenCode session invocation."""

    index: int
    contracts: tuple[OpenCodeTaskContract, ...]
    estimated_input_tokens: int
    over_target: bool = False


def _prompt_hash(prompt: str | None) -> str | None:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt is not None else None


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "task"


def parse_mcp_read_allowlist(raw: str) -> dict[str, tuple[str, ...]]:
    """Parse an optional operator ceiling; it is never an authorization grant.

    Read-only and Gather eligibility are decided exclusively by trusted provider
    catalogue metadata in the capability broker.  The legacy field is rejected
    when it visibly contains a mutation name so an old operator setting cannot
    accidentally re-enable general MCP; that input hygiene is not authorization.
    """

    try:
        decoded = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise OpenCodeCompatibilityError("invalid OpenCode MCP allowlist JSON") from exc
    if not isinstance(decoded, dict):
        raise OpenCodeCompatibilityError("OpenCode MCP allowlist must be an object")
    allowlist: dict[str, tuple[str, ...]] = {}
    for server, tools in decoded.items():
        if (
            not isinstance(server, str)
            or not isinstance(tools, list)
            or not all(isinstance(tool, str) for tool in tools)
        ):
            raise OpenCodeCompatibilityError(
                "OpenCode MCP allowlist entries must be string tool lists"
            )
        if any(tool.lower().startswith(_LEGACY_MUTATING_TOOL_PREFIXES) for tool in tools):
            raise OpenCodeCompatibilityError("OpenCode MCP operator ceiling must be read-only")
        allowlist[server] = tuple(tools)
    return allowlist


# Model routes whose provider credential reaches OpenCode as a plain environment
# variable (the built-in provider reads the first name; the remaining names are
# accepted aliases, e.g. the MCP_-prefixed settings form). Routes needing a
# credential FILE (opencode-go) or inline provider config (ollama) are handled
# separately below.
_ROUTE_ENV_CREDENTIALS: dict[str, tuple[str, ...]] = {
    "openrouter/": ("OPENROUTER_API_KEY", "MCP_DOC_GENERATION_OPENROUTER_API_KEY"),
}


def _route_credential(model_route: str) -> tuple[str, str] | None:
    """Return ``(env_var, value)`` for an env-credentialed route, else ``None``.

    Raises when the route needs a credential that is not configured, so the run
    fails at setup instead of inside the OpenCode session.
    """

    for prefix, names in _ROUTE_ENV_CREDENTIALS.items():
        if not model_route.startswith(prefix):
            continue
        for name in names:
            value = (os.environ.get(name) or "").strip()
            if value:
                return names[0], value
        raise OpenCodeCompatibilityError(
            f"OpenCode route {model_route!r} requires {' or '.join(names)}"
        )
    return None


class OpenCodeCliProvider(DocGenerationProvider):
    """Run modern legacy prompts through adaptive OpenCode phase sessions."""

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

    def _execution_env(self, phase_config: OpenCodePhaseConfig | None = None) -> dict[str, str]:
        """Add model connector configuration needed by the selected route."""

        env = self._runtime_env()
        if phase_config is not None:
            env["OPENCODE_CONFIG"] = str(phase_config.path)
            env.update(phase_config.environment)
            # Go authentication is exposed only through the temporary 0600
            # auth file prepared for this phase, not duplicated in child env.
            env.pop("MCP_OPENCODE_GO_API_KEY", None)
        elif config.opencode_config_path:
            # This is retained for the connector-free path. Connector-enabled
            # invocations always receive a generated deny-by-default config.
            env["OPENCODE_CONFIG"] = config.opencode_config_path
        if phase_config is not None and env.get("OPENCODE_CONFIG_CONTENT"):
            try:
                inherited_inline = json.loads(env["OPENCODE_CONFIG_CONTENT"])
            except json.JSONDecodeError as exc:
                raise OpenCodeCompatibilityError(
                    "OPENCODE_CONFIG_CONTENT must be valid JSON"
                ) from exc
            if not isinstance(inherited_inline, dict):
                raise OpenCodeCompatibilityError(
                    "OPENCODE_CONFIG_CONTENT must contain a JSON object"
                )
            # This path also covers non-Ollama models: OpenCode can merge inline
            # config independently of the selected provider route.
            inherited_inline.pop("mcp", None)
            inherited_inline.pop("permission", None)
            inherited_inline.pop("agent", None)
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps(inherited_inline, separators=(",", ":"))
        if not self.model_route.startswith("ollama/"):
            return env

        model_id = self.model_route.split("/", 1)[1]
        try:
            inline_config = json.loads(env.get("OPENCODE_CONFIG_CONTENT") or "{}")
        except json.JSONDecodeError as exc:
            raise OpenCodeCompatibilityError("OPENCODE_CONFIG_CONTENT must be valid JSON") from exc
        if not isinstance(inline_config, dict):
            raise OpenCodeCompatibilityError("OPENCODE_CONFIG_CONTENT must contain a JSON object")
        if phase_config is not None:
            # A generated phase config is the complete permission/MCP boundary.
            # Preserve only provider construction data from a legacy inline
            # config; an inherited MCP or permission block must not reintroduce
            # tools that this invocation explicitly denied.
            inline_config.pop("mcp", None)
            inline_config.pop("permission", None)
            inline_config.pop("agent", None)

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
            raise OpenCodeCompatibilityError("OpenCode Ollama provider options must be an object")
        options.setdefault("baseURL", config.opencode_ollama_base_url)
        models = ollama.setdefault("models", {})
        if not isinstance(models, dict):
            raise OpenCodeCompatibilityError("OpenCode Ollama provider models must be an object")
        models.setdefault(model_id, {"name": model_id})
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(inline_config, separators=(",", ":"))
        return env

    @staticmethod
    def _connector_runtime(ctx: DocGenContext) -> Any | None:
        """Return injected run authority only when the feature flag is on.

        The generation service owns binding snapshots.  This provider does not
        query connector tables itself, which keeps legacy runs fully isolated.
        """

        if not getattr(config, "opencode_connectors_enabled", False):
            return None
        runtime = getattr(ctx, "connector_runtime", None)
        try:
            enabled = runtime is not None and bool(getattr(runtime, "enabled", False))
        except Exception:  # noqa: BLE001 - connector implementations are third-party adapters
            # An expired/revoked persisted snapshot is connector degradation,
            # not a reason to fail the source-backed documentation run.
            return None
        if not enabled:
            return None
        if (
            getattr(runtime, "run_id", None) != ctx.run_id
            or getattr(runtime, "project_id", None) != ctx.project_id
        ):
            return None
        return runtime

    @staticmethod
    def _connector_tools(runtime: Any, allowlist: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
        """Present only tools derived from the broker's trusted catalogue.

        The legacy environment allowlist can only reduce this catalogue when it
        is non-empty; it cannot create a capability or mark one read-only.
        """

        from mcp_server.connectors.mcp_facade import _CAPABILITY_TO_TOOL

        capabilities = tuple(runtime.broker.tool_catalogue(runtime.token))
        ceiling = {tool for tools in allowlist.values() for tool in tools}
        names = []
        for capability in capabilities:
            tool = _CAPABILITY_TO_TOOL.get(capability)
            if tool is None:
                continue
            identifier = f"nexus_run_evidence:{tool}"
            if (
                ceiling
                and capability not in ceiling
                and tool not in ceiling
                and identifier not in ceiling
            ):
                continue
            names.append(identifier)
        return tuple(names)

    def _phase_config(
        self,
        *,
        ctx: DocGenContext,
        phase: str,
        contracts: list[OpenCodeTaskContract],
        runtime: Any | None,
        investigation_facade: Any | None = None,
        artifact_facade: Any | None = None,
        exploration_artifacts: tuple[str, ...] | None = None,
    ) -> OpenCodePhaseConfig:
        """Generate an isolated deny-by-default config outside model staging."""

        root = Path(tempfile.mkdtemp(prefix="nexus-opencode-"))
        root.chmod(0o700)
        validation_commands = [
            str(contract.metadata["validation_command"])
            for contract in contracts
            if contract.metadata.get("validation_command")
        ]
        bash_policy: dict[str, str] = {"*": "deny"}
        for command in validation_commands:
            bash_policy[command] = "allow"
        workspace_root = ctx.staging_dir.resolve()
        workspace_glob = str((workspace_root / "**").resolve())
        if exploration_artifacts is None:
            # OpenCode passes paths to its tools relative to the active workspace
            # (for example ``_transient/opencode/gather_contracts.json``), while
            # the coordinator also needs to support absolute paths in tool calls.
            workspace_patterns = {"*": "deny", "**": "allow", workspace_glob: "allow"}
        else:
            # A Gather recovery pass already knows its unresolved units. Keep
            # native inspection available, but only over the authoritative
            # evidence files selected by the coordinator. This prevents the
            # recovery agent from investigating Nexus' audit/transient state.
            workspace_patterns = {"*": "deny"}
            for raw_artifact in exploration_artifacts:
                candidate = (workspace_root / raw_artifact).resolve()
                try:
                    relative = candidate.relative_to(workspace_root).as_posix()
                except ValueError as exc:
                    raise OpenCodeCompatibilityError(
                        f"OpenCode exploration artifact escapes staging: {raw_artifact}"
                    ) from exc
                workspace_patterns[relative] = "allow"
                workspace_patterns[str(candidate)] = "allow"
        search_patterns = (
            workspace_patterns
            if exploration_artifacts is not None
            else {"*": "deny", "**": "allow"}
        )
        output_patterns: dict[str, str] = {"*": "deny"}
        for contract in contracts:
            output = ctx.staging_dir / contract.output_artifact
            output_patterns[contract.output_artifact] = "allow"
            output_patterns[str(output.resolve())] = "allow"
        permissions: dict[str, Any] = {
            "*": "deny",
            "read": workspace_patterns,
            # Analyze and Produce can write only their declared deliverables.
            # This prevents concurrent sessions from mutating a sibling's
            # checkpoint while keeping the workspace fully readable.
            "edit": output_patterns,
            "glob": search_patterns,
            "grep": search_patterns,
            "list": search_patterns,
            "bash": bash_policy,
            "webfetch": "deny",
            "websearch": "deny",
            "external_directory": "deny",
        }
        if investigation_facade is not None or artifact_facade is not None:
            # Gather v2 is read/search native and write-through-tools only.  The
            # Analyze/Produce use the same boundary for their declared output
            # artifacts. Agents may explore freely inside sanitized staging,
            # but cannot bypass coordinator validation by editing files.
            permissions["edit"] = {"*": "deny"}
            permissions["bash"] = {"*": "deny"}
        payload: dict[str, Any] = {
            "$schema": "https://opencode.ai/config.json",
            "permission": permissions,
        }
        environment: dict[str, str] = {}
        # Every invocation gets its own writable data/state home, not just the
        # Go route that needs one for its auth file. Sharing the service
        # account's ambient home makes a run depend on whatever uid last wrote
        # there -- one `docker compose exec` as root leaves root-owned
        # `opencode/log` and `opencode/locks` behind and every later run dies
        # with `PermissionDenied: FileSystem.open (.../opencode.log)` before it
        # reaches the model. Both directories are per-phase scratch (log/,
        # repos/, locks/) with nothing worth carrying between phases, and they
        # are removed with the config root by `OpenCodePhaseConfig.cleanup`.
        data_home = root / "data"
        (data_home / "opencode").mkdir(parents=True, mode=0o700)
        environment["XDG_DATA_HOME"] = str(data_home)
        state_home = root / "state"
        state_home.mkdir(mode=0o700)
        environment["XDG_STATE_HOME"] = str(state_home)
        # The cache holds the models.json download and the runtime binary, so it
        # is worth keeping between runs. It is owned by whichever account the
        # service runs as because the service process is what creates it.
        try:
            cache_home = self._shared_cache_home()
        except OSError as exc:
            shutil.rmtree(root, ignore_errors=True)
            raise OpenCodeCompatibilityError(
                f"OpenCode cache directory is not usable: {exc}"
            ) from exc
        environment["XDG_CACHE_HOME"] = str(cache_home)

        if self.model_route.startswith("opencode-go/"):
            key = config.opencode_go_api_key.strip()
            if not key:
                shutil.rmtree(root, ignore_errors=True)
                raise OpenCodeCompatibilityError("OpenCode Go requires MCP_OPENCODE_GO_API_KEY")
            auth_path = data_home / "opencode" / "auth.json"
            auth_path.write_text(
                json.dumps(
                    {"opencode-go": {"type": "api", "key": key}},
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            auth_path.chmod(0o600)
        else:
            try:
                credential = _route_credential(self.model_route)
            except OpenCodeCompatibilityError:
                shutil.rmtree(root, ignore_errors=True)
                raise
            if credential is not None:
                environment[credential[0]] = credential[1]
        if investigation_facade is not None:
            payload["mcp"] = {
                "nexus_investigation": {
                    "type": "local",
                    "command": [
                        sys.executable,
                        "-m",
                        "mcp_server.providers.opencode_investigation_mcp",
                    ],
                    "environment": {
                        "NEXUS_INVESTIGATION_GRANT": str(investigation_facade.token),
                    },
                }
            }
            # OpenCode permission keys are matched directly against normalized
            # tool names. MCP tools are `<server>_<tool>`; there is no nested
            # `mcp` permission namespace. With the global `* = deny`, nesting
            # this wildcard under `mcp` silently connected the server while
            # hiding every tool from the model.
            permissions["nexus_investigation_*"] = "allow"
        elif artifact_facade is not None:
            payload["mcp"] = {
                "nexus_artifacts": {
                    "type": "local",
                    "command": [
                        sys.executable,
                        "-m",
                        "mcp_server.providers.opencode_artifact_mcp",
                    ],
                    "environment": {
                        "NEXUS_ARTIFACT_GRANT": str(artifact_facade.token),
                    },
                }
            }
            permissions["nexus_artifacts_*"] = "allow"
        elif phase == "gather" and runtime is not None:
            # The opaque grant is deliberately in a 0700 config outside the
            # workspace. It is neither a provider credential nor prompt content.
            payload["mcp"] = {
                "nexus_run_evidence": {
                    "type": "local",
                    "command": [sys.executable, "-m", "mcp_server.connectors.mcp_facade"],
                    "environment": {
                        "NEXUS_CONNECTOR_GRANT": str(runtime.token),
                        "NEXUS_CONNECTOR_RUN_ID": str(runtime.run_id),
                        "NEXUS_CONNECTOR_PROJECT_ID": str(runtime.project_id),
                    },
                }
            }
            permissions["nexus_run_evidence_*"] = "allow"
        path = root / "opencode.json"
        path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        path.chmod(0o600)
        return OpenCodePhaseConfig(path=path, environment=environment)

    @staticmethod
    def _shared_cache_home() -> Path:
        """Return the service-owned OpenCode cache directory, creating it.

        Created by the service process, so it is owned by whichever account runs
        the service. Raises ``OSError`` when it exists but cannot be written --
        the caller turns that into a setup-time failure rather than letting
        OpenCode die mid-run.
        """

        cache_home = Path(config.data_dir) / "opencode_cache"
        cache_home.mkdir(parents=True, exist_ok=True)
        probe = cache_home / ".nexus_write_probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return cache_home

    def _preflight(self) -> tuple[str, dict[str, tuple[str, ...]]]:
        if not config.opencode_version:
            raise OpenCodeCompatibilityError("MCP_OPENCODE_VERSION must pin the OpenCode runtime")
        try:
            self._shared_cache_home()
        except OSError as exc:
            # A non-writable cache surfaces as an opaque runtime PermissionDenied
            # once OpenCode is already streaming; name the path and owner here.
            raise OpenCodeCompatibilityError(
                f"OpenCode cache directory is not writable by the service account: {exc}"
            ) from exc
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

    async def run_bounded_contract(
        self,
        *,
        project_id: str,
        workspace: Path,
        contract: OpenCodeTaskContract,
        log_cb: LogCallback,
        step: str,
        prompt: str,
    ) -> AgentResult:
        """Execute one isolated OpenCode contract outside full generation.

        Incremental reingestion needs the same pinned runtime, deny-by-default
        permissions, artifact submission boundary, event accounting, and
        credential isolation as the three-phase generator.  This public seam
        deliberately exposes only a single contract and a single declared
        output; it does not run Gather/Analyze/Produce orchestration and grants
        no publication or RAG capability.
        """

        workspace = workspace.resolve()
        workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
        audit_dir = workspace / "_audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        ctx = DocGenContext(
            project_id=project_id,
            staging_dir=workspace,
            staging_abs=str(workspace),
            logs_abs=str(audit_dir),
            preprocess_groups=[],
            total_jobs=0,
            total_groups=0,
            effective_model=self.model_route or "opencode-default",
            write_model=self.model_route or "opencode-default",
            platform="generic",
            analysis_guide_file="",
            enterprise_mode=True,
        )
        started = time.monotonic()
        resolved, allowlist = self._preflight()
        execution = await self._execute_step(
            resolved=resolved,
            ctx=ctx,
            phase="produce",
            step=step,
            contracts=[contract],
            allowlist=allowlist,
            session_id=None,
            ledger=OpenCodeAttemptLedger(workspace),
            log_cb=log_cb,
            prompt_override=prompt,
        )
        events_path = audit_dir / "opencode_events.json"
        events_path.write_text(json.dumps(execution.events, indent=2), encoding="utf-8")
        self._write_usage_receipts(
            audit_dir,
            [
                {
                    "phase": "reingestion",
                    "step": step,
                    "status": "model_completed",
                    "session_id": execution.session_id,
                    "input_tokens": execution.input_tokens,
                    "output_tokens": execution.output_tokens,
                    "cost_usd": execution.cost_usd,
                    "latency_ms": execution.latency_ms,
                }
            ],
        )
        return AgentResult(
            success=True,
            input_tokens=execution.input_tokens,
            output_tokens=execution.output_tokens,
            cost_usd=execution.cost_usd,
            duration_ms=int((time.monotonic() - started) * 1000),
            duration_api_ms=execution.latency_ms,
            num_turns=1,
            session_id=execution.session_id,
            model_name=self.model_route or "opencode-default",
        )

    async def _prepare_run(self, ctx: DocGenContext, log_cb: LogCallback):
        """Stage the deterministic evidence tiers this engine's Phase 3 reads.

        Async because runtime evidence is fetched here. This engine reuses the
        custom harness's Phase 3 CONSUMER (`_phase3_context_files`), so any
        producer it skips silently degrades every document it writes: before
        this call the runtime lane was simply absent from OpenCode runs, with
        no log line and no coverage artifact to say so.
        """
        from mcp_server.providers.custom.engineering_facts import EngineeringFactsService
        from mcp_server.providers.custom.models import HarnessRunState
        from mcp_server.providers.custom.pipeline.helpers import HarnessHelpersImpl
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
        state = HarnessRunState(project_id=ctx.project_id, facts_manifest=facts)
        harness._write_cross_cutting_facts(ctx, state)
        # One producer for both engines (see HarnessHelpersImpl docstring).
        await HarnessHelpersImpl(harness).ensure_runtime_evidence(ctx, state, log_cb)
        return facts, harness, state

    def _gather_contracts(
        self,
        ctx: DocGenContext,
        facts: Any,
        harness: Any,
        allowlist: dict[str, tuple[str, ...]],
    ) -> list[OpenCodeTaskContract]:
        from mcp_server.providers.custom.evidence_tasks import plan_evidence_tasks
        from mcp_server.providers.custom.investigator import (
            SingleShotInvestigator,
            write_investigation_artifacts,
        )
        from mcp_server.providers.custom.models import InvestigationReport

        if harness._phase_policy("investigation") == "skip" or (
            facts.unit_count <= config.small_project_threshold and self.plugin_pack_dir is None
        ):
            return []

        preamble = ""
        if self.plugin_pack_dir is not None:
            preamble_path = self.plugin_pack_dir / "prompts" / "investigation.md"
            if preamble_path.exists():
                preamble = preamble_path.read_text(encoding="utf-8").rstrip()
        # Duck-typed stand-in for the investigator: this provider borrows the
        # unbound `_build_prompt` so its Phase 1 contract cannot drift from the
        # custom harness's. It must therefore carry every attribute that method
        # reads -- `_pack_dir` included, so a pack's
        # prompts/templates/phase1_extraction.md override applies here too.
        prompt_builder = SimpleNamespace(
            _preamble=preamble, _pack_dir=self.plugin_pack_dir
        )
        raw_dir = ctx.staging_dir / "_transient" / "opencode" / "investigation_raw"
        task_dir = ctx.staging_dir / "_transient" / "opencode" / "evidence_tasks"
        contracts: list[OpenCodeTaskContract] = []
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
                template_artifact, validation_spec, validation_receipt, validation_command = (
                    self._write_gather_guard(
                        ctx,
                        task_id=task.task_id,
                        output_artifact=output_artifact,
                        allowed_units=task.all_units,
                        source_ref=source_ref,
                    )
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
                        # Static investigation is intentionally connector-free.
                        # Runtime evidence gets a separate Gather sub-step after
                        # deterministic static finalization.
                        allowed_tools=(),
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

    def _external_evidence_contracts(
        self,
        ctx: DocGenContext,
        facts: Any,
        runtime: Any,
        allowlist: dict[str, tuple[str, ...]],
    ) -> list[OpenCodeTaskContract]:
        """Plan the dedicated live-evidence Gather sub-step after static Gather."""

        from mcp_server.connectors.planner import plan_external_evidence_tasks

        trusted_capabilities = tuple(runtime.broker.tool_catalogue(runtime.token))
        allowed_tools = self._connector_tools(runtime, allowlist)
        if not trusted_capabilities or not allowed_tools:
            return []
        tasks = plan_external_evidence_tasks(
            staging_dir=ctx.staging_dir,
            facts=facts,
            preprocess_groups=ctx.preprocess_groups,
            capabilities=trusted_capabilities,
            max_tasks=min(4, max(1, len(ctx.preprocess_groups))),
        )
        return [
            OpenCodeTaskContract(
                phase="gather",
                target=f"connector:{task.task_id}",
                prompt_artifact="authoritative:connector_external_evidence_task",
                prompt=task.prompt(),
                evidence_artifacts=task.evidence_artifacts,
                output_artifact=task.output_artifact,
                allowed_tools=allowed_tools,
                metadata={
                    "connector_external_evidence": True,
                    "group": task.group,
                    "entity_refs": list(task.entity_refs),
                    "question": task.question,
                },
            )
            for task in tasks
        ]

    def _finalize_external_evidence(
        self,
        ctx: DocGenContext,
        facts: Any,
        runtime: Any,
        contracts: list[OpenCodeTaskContract],
        *,
        limitation: str | None = None,
    ) -> Path:
        """Accept only reconciled claim links and hand them to Analyze as artifacts."""

        from mcp_server.connectors.reconciliation import (
            ReconciliationError,
            issued_evidence_index,
            materialize_reconciled_evidence,
            reconcile_findings,
            write_claim_linkage_report,
        )

        run_id = ctx.run_id
        if run_id is None:
            raise OpenCodeCompatibilityError("connector evidence requires a generation run ID")
        envelopes = list(getattr(runtime.broker, "evidence_by_id", {}).values())
        issued = issued_evidence_index(envelopes, run_id=run_id, project_id=ctx.project_id)
        canonical_refs = {f"unit:{unit.name}" for unit in facts.units}
        accepted = []
        limitations: list[str] = [limitation] if limitation else []
        for contract in contracts:
            output = ctx.staging_dir / contract.output_artifact
            if not output.exists():
                if limitation is None:
                    raise OpenCodeCompatibilityError(
                        f"OpenCode external evidence output is missing for {contract.target}"
                    )
                continue
            try:
                payload = json.loads(output.read_text(encoding="utf-8", errors="replace"))
                findings, task_limitations = reconcile_findings(
                    payload, issued=issued, canonical_entity_refs=canonical_refs
                )
            except (OSError, json.JSONDecodeError, ReconciliationError) as exc:
                if limitation is not None:
                    limitations.append(limitation)
                    continue
                raise OpenCodeCompatibilityError(
                    f"OpenCode external evidence output is invalid for {contract.target}: {exc}"
                ) from exc
            accepted.extend(findings)
            limitations.extend(task_limitations)
        path = materialize_reconciled_evidence(
            ctx.staging_dir,
            findings=accepted,
            limitations=limitations,
            run_id=run_id,
            project_id=ctx.project_id,
        )
        # Add group-scoped artifacts so Analyze receives only relevant accepted
        # evidence, never the whole runtime envelope catalogue.
        group_by_entity = {f"unit:{unit.name}": unit.group for unit in facts.units}
        for group in ctx.preprocess_groups:
            group_findings = [
                finding
                for finding in accepted
                if any(group_by_entity.get(ref) == group for ref in finding.entity_refs)
            ]
            materialize_reconciled_evidence(
                ctx.staging_dir,
                findings=group_findings,
                limitations=limitations,
                run_id=run_id,
                project_id=ctx.project_id,
                artifact_dir=ctx.staging_dir / "_connector_evidence" / _safe_name(group),
            )
        write_claim_linkage_report(
            ctx.staging_dir,
            findings=accepted,
            receipts=[item.to_safe_dict() for item in runtime.broker.receipts],
        )
        return path

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
            )
            + "\n",
            encoding="utf-8",
        )
        validator = self._write_gather_validator(ctx)
        template_ref = template.relative_to(ctx.staging_dir).as_posix()
        spec_ref = spec.relative_to(ctx.staging_dir).as_posix()
        command = f"python {validator} {output_artifact} {spec_ref}"
        receipt_ref = receipt.relative_to(ctx.staging_dir).as_posix()
        return template_ref, spec_ref, receipt_ref, command

    def _analyze_contracts(
        self, ctx: DocGenContext, facts: Any, harness: Any
    ) -> list[OpenCodeTaskContract]:
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
                connector_artifact = (
                    f"_connector_evidence/{_safe_name(group)}/accepted_findings.json"
                )
                if (ctx.staging_dir / connector_artifact).exists():
                    context.append(connector_artifact)
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
                        "facts_manifest.json",
                        "_analysis",
                        "_investigation",
                        "_assessment/prompt_packs/_scorecard_prompt_pack.md",
                    ),
                    output_artifact="_transient/opencode/scorecard_raw.md",
                )
            )
        return contracts

    def _doc_contract(
        self, ctx: DocGenContext, facts: Any, harness: Any, doc_name: str
    ) -> OpenCodeTaskContract:
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
                unit_reference=(
                    doc_name in harness._per_job_doc_units and harness._plugin_manifest is None
                ),
                # Mirror of the custom harness contract (One Pack item 8): the
                # pack's ``audience:`` block must reach this legacy provider's
                # Phase 3 prompt too, or the two paths diverge silently.
                contract=harness.pack_contract,
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
            contract_file = contract_dir / (f"{position:03d}__{_safe_name(contract.target)}.json")
            contract_file.write_text(json.dumps(contract.to_wire(), indent=2), encoding="utf-8")
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
                    "contract_artifact": contract_file.relative_to(ctx.staging_dir).as_posix(),
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

    @staticmethod
    def _artifact_size_bytes(path: Path) -> int:
        """Return the deterministic byte size of one declared evidence path."""

        try:
            if path.is_file():
                return path.stat().st_size
            if path.is_dir():
                total = 0
                for child in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
                    try:
                        if child.is_file():
                            total += child.stat().st_size
                    except OSError:
                        continue
                return total
        except OSError:
            pass
        return 0

    def _estimate_contract_input_tokens(
        self,
        ctx: DocGenContext,
        contract: OpenCodeTaskContract,
    ) -> int:
        """Estimate prompt, contract-envelope, and declared-evidence input cost."""

        wire = contract.to_wire()
        # Prompt bytes are counted explicitly. Keep only their digest in the
        # envelope estimate so the serialized contract is not double-counted.
        wire.pop("prompt", None)
        contract_bytes = len(
            json.dumps(wire, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        prompt_bytes = len((contract.prompt or "").encode("utf-8"))
        evidence_bytes = sum(
            self._artifact_size_bytes(ctx.staging_dir / artifact)
            for artifact in contract.evidence_artifacts
        )
        total_bytes = contract_bytes + prompt_bytes + evidence_bytes
        return max(1, (total_bytes + _ESTIMATED_BYTES_PER_TOKEN - 1) // _ESTIMATED_BYTES_PER_TOKEN)

    def _plan_contract_batches(
        self,
        ctx: DocGenContext,
        contracts: list[OpenCodeTaskContract],
        target_tokens: int | None = None,
    ) -> list[_ContractBatch]:
        """Pack contracts in stable order under the configured input target.

        Contract artifacts remain the validation and resume boundary. Batches
        only decide which ordered contracts share one fresh OpenCode session.
        An over-target contract is retained as a visible one-contract batch.
        """

        target = max(
            1,
            int(
                getattr(config, "opencode_batch_target_input_tokens", 80_000)
                if target_tokens is None
                else target_tokens
            ),
        )
        planned: list[tuple[list[OpenCodeTaskContract], int, bool]] = []
        current: list[OpenCodeTaskContract] = []
        current_tokens = 0

        def flush() -> None:
            nonlocal current, current_tokens
            if current:
                planned.append((current, current_tokens, current_tokens > target))
                current = []
                current_tokens = 0

        for contract in contracts:
            estimated = self._estimate_contract_input_tokens(ctx, contract)
            if estimated > target:
                flush()
                planned.append(([contract], estimated, True))
                continue
            if current and current_tokens + estimated > target:
                flush()
            current.append(contract)
            current_tokens += estimated
        flush()

        return [
            _ContractBatch(
                index=index,
                contracts=tuple(batch_contracts),
                estimated_input_tokens=estimated,
                over_target=over_target,
            )
            for index, (batch_contracts, estimated, over_target) in enumerate(
                planned, start=1
            )
        ]

    @staticmethod
    def _batch_plan_summary(batches: list[_ContractBatch]) -> str:
        return ",".join(
            f"{batch.index}:{batch.estimated_input_tokens}"
            f"{'!' if batch.over_target else ''}"
            for batch in batches
        )

    def _phase_prompt(
        self,
        phase: str,
        step: str,
        contract_path: str,
        contract_count: int,
        allowlist: dict[str, tuple[str, ...]],
        connector_tools: tuple[str, ...] = (),
    ) -> str:
        connector_policy = ""
        if phase == "gather" and (connector_tools or allowlist):
            connector_policy = (
                "Run-scoped Nexus MCP tools may be used only when their server:tool identifier is listed "
                "in a task's allowed_tools. Treat connector results as supplementary evidence and "
                "never override scanner facts. Follow each contract's exact output schema and any "
                "validation command; static investigation contracts use template_artifact and "
                "validation_command. Do not use a connector outside this sub-step. "
            )
        if phase in {"analyze", "produce"}:
            execution_protocol = (
                "For each contract, read the contract artifact and only its listed "
                "evidence_artifacts. Create the complete deliverable in memory, then call "
                "nexus_artifacts_submit_artifact with the contract's exact output_artifact "
                "and the full content. That successful tool response is the durable commit. "
                "Do not use write, edit, apply_patch, or shell redirection. If submission "
                "returns ok=false, correct the reported validation problem and submit again "
                "before moving to the next contract. "
            )
            exploration_policy = (
                "Use native read, grep, glob, and list tools when useful to follow evidence "
                "inside the sanitized workspace; choose what to inspect based on what you "
                "discover. Do not use exploratory bash commands. "
            )
        else:
            execution_protocol = (
                "For each contract, read the contract artifact and only its listed "
                "evidence_artifacts, then immediately use the write tool to create the exact "
                "output_artifact path. The file is the deliverable: do not merely return JSON "
                "in your response. After each output write, run that contract's exact "
                "validation_command and confirm its validation receipt exists before moving "
                "to the next contract. "
            )
            exploration_policy = (
                "Do not use search, grep, glob, list, or exploratory bash commands. "
            )
        return (
            f"Continue the Nexus documentation run in user-visible phase {phase.upper()} "
            f"(step {step}). Work only in the current workspace. "
            f"Read {contract_path}; it is an ordered index of {contract_count} task contract(s). "
            "Process the contracts sequentially in this one session; do not launch sub-agents or "
            "invent another execution topology. "
            + execution_protocol
            + exploration_policy
            + "Complete every contract exactly once, using its exact prompt without "
            "paraphrasing, and do not continue until all declared outputs are durably present. "
            "Do not put Markdown fences around JSON outputs. Do not publish, deploy, change "
            "credentials, or write outside the workspace. Do not repeat a task that Nexus has "
            "already listed once. If a required task cannot complete, stop and report failure. "
            + connector_policy
            + "When every listed output exists, briefly confirm completion and stop."
        )

    def _command(
        self,
        resolved: str,
        ctx: DocGenContext,
        session_id: str | None = None,
        phase_config: OpenCodePhaseConfig | None = None,
    ) -> list[str]:
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
                (
                    f"{prefix}: model turn complete "
                    f"(input={int(tokens.get('input') or 0)} "
                    f"output={int(tokens.get('output') or 0)} "
                    f"reasoning={int(tokens.get('reasoning') or 0)} "
                    f"cache_read={int(cache.get('read') or 0)} "
                    f"cache_write={int(cache.get('write') or 0)})"
                ),
                "info",
            )
        if kind == "error":
            reason = _error_reason(event)
            detail = f": {reason}" if reason else " (event carried no message)"
            return (
                (
                    f"{prefix}: OpenCode error event{detail} "
                    "[full event in _audit/opencode_errors.json]"
                ),
                "error",
            )
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
                    if (
                        resolved == staging_dir.resolve()
                        or staging_dir.resolve() in resolved.parents
                    ):
                        target = f" {resolved.relative_to(staging_dir.resolve()).as_posix()}"
                except (OSError, ValueError):
                    target = ""
        return f"{prefix}: tool {tool} {status}{target}", "info"

    @staticmethod
    def _record_step_failure(
        staging_dir: Path,
        *,
        phase: str,
        step: str,
        model_route: str,
        returncode: int | None,
        error_events: list[dict[str, Any]],
        stderr_text: str,
    ) -> str:
        """Append the raw failure record to ``_audit/opencode_errors.json``.

        A failed step used to leave nothing behind: the error event was reduced to
        a bare type, stderr was counted and dropped, and ``opencode_events.json``
        is only written on success. Returns the short reason for the exception
        message. Diagnostics must never mask the failure they describe, so every
        filesystem problem here is swallowed.
        """

        reason = ""
        for event in error_events:
            reason = _error_reason(event)
            if reason:
                break
        if not reason and stderr_text.strip():
            reason = " ".join(stderr_text.split())[:_MAX_LOG_REASON_CHARS]

        record = {
            "phase": phase,
            "step": step,
            "model_route": model_route,
            "returncode": returncode,
            "reason": reason,
            "error_events": error_events[:_MAX_PERSISTED_ERROR_EVENTS],
            "error_event_count": len(error_events),
            "stderr": stderr_text[:_MAX_PERSISTED_STDERR_CHARS],
            "stderr_truncated": len(stderr_text) > _MAX_PERSISTED_STDERR_CHARS,
        }
        path = staging_dir / "_audit" / "opencode_errors.json"
        try:
            existing: list[Any] = []
            if path.is_file():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    existing = loaded
            existing.append(record)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            pass
        return reason

    @staticmethod
    def _failure_message(base: str, reason: str) -> str:
        suffix = f": {reason}" if reason else " (no error event or stderr captured)"
        return f"{base}{suffix} [see _audit/opencode_errors.json]"

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
        runtime: Any | None = None,
        investigation_facade: Any | None = None,
        exploration_artifacts: tuple[str, ...] | None = None,
        prompt_override: str | None = None,
        require_outputs: bool = True,
    ) -> _PhaseExecution:
        step_started = time.monotonic()
        ledger_target = f"step:{step}"
        ledger.dispatch(ledger_target)
        contract_path = self._write_contracts(ctx, step, contracts)
        artifact_facade: Any | None = None
        if phase in {"analyze", "produce"}:
            from mcp_server.providers.opencode_artifact_mcp import (
                ArtifactSubmissionFacade,
            )

            artifact_facade = ArtifactSubmissionFacade(
                staging_dir=ctx.staging_dir,
                phase=phase,
                step=step,
                contracts=contracts,
            )
        phase_config = self._phase_config(
            ctx=ctx,
            phase=phase,
            contracts=contracts,
            runtime=runtime if phase == "gather" else None,
            investigation_facade=investigation_facade,
            artifact_facade=artifact_facade,
            exploration_artifacts=exploration_artifacts,
        )
        bridge: Any | None = None

        async def cleanup_phase_config() -> None:
            if bridge is not None:
                await bridge.close()
            phase_config.cleanup()

        if investigation_facade is not None:
            from mcp_server.connectors.mcp_facade import FacadeBridge

            bridge = FacadeBridge(
                investigation_facade,
                phase_config.path.parent / "investigation.sock",
            )
            await bridge.start()
            payload = json.loads(phase_config.path.read_text(encoding="utf-8"))
            payload["mcp"]["nexus_investigation"]["environment"][
                "NEXUS_INVESTIGATION_SOCKET"
            ] = str(bridge.socket_path)
            phase_config.path.write_text(
                json.dumps(payload, separators=(",", ":")), encoding="utf-8"
            )
        elif artifact_facade is not None:
            from mcp_server.connectors.mcp_facade import FacadeBridge

            bridge = FacadeBridge(
                artifact_facade,
                phase_config.path.parent / "artifacts.sock",
            )
            await bridge.start()
            payload = json.loads(phase_config.path.read_text(encoding="utf-8"))
            payload["mcp"]["nexus_artifacts"]["environment"][
                "NEXUS_ARTIFACT_SOCKET"
            ] = str(bridge.socket_path)
            phase_config.path.write_text(
                json.dumps(payload, separators=(",", ":")), encoding="utf-8"
            )
        elif phase == "gather" and runtime is not None:
            from mcp_server.connectors.mcp_facade import FacadeBridge, RunScopedMcpFacade

            facade = RunScopedMcpFacade(
                broker=runtime.broker,
                token=runtime.token,
                run_id=runtime.run_id,
                project_id=runtime.project_id,
            )
            bridge = FacadeBridge(facade, phase_config.path.parent / "connector.sock")
            await bridge.start()
            payload = json.loads(phase_config.path.read_text(encoding="utf-8"))
            payload["mcp"]["nexus_run_evidence"]["environment"]["NEXUS_CONNECTOR_SOCKET"] = str(
                bridge.socket_path
            )
            phase_config.path.write_text(
                json.dumps(payload, separators=(",", ":")), encoding="utf-8"
            )
        connector_tools = tuple(tool for contract in contracts for tool in contract.allowed_tools)
        prompt = prompt_override or self._phase_prompt(
            phase, step, contract_path, len(contracts), allowlist, connector_tools
        )
        env = self._execution_env(phase_config)
        cmd = self._command(resolved, ctx, session_id, phase_config)
        await log_cb(ctx.project_id, f"OpenCode {phase}: {step} started", "info")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(ctx.staging_dir),
                env=env,
                limit=4 * 1024 * 1024,
            )
        except Exception:
            await cleanup_phase_config()
            raise

        events: list[dict[str, Any]] = []
        actual_session = session_id
        input_tokens = output_tokens = 0
        cost_usd = 0.0
        saw_error = False
        stderr_seen = False
        # Raw error events and stderr are kept for the failure artifact only; the
        # normalized `events` list stays content-free for the success path.
        raw_errors: list[dict[str, Any]] = []
        stderr_chunks: list[str] = []

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
                    if parsed["type"] == "error":
                        saw_error = True
                        if len(raw_errors) < _MAX_PERSISTED_ERROR_EVENTS:
                            raw_errors.append(raw_event)
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
            captured = 0
            while raw_line := await proc.stderr.readline():
                if not raw_line.strip():
                    continue
                stderr_seen = True
                if captured < _MAX_PERSISTED_STDERR_CHARS:
                    text = raw_line.decode("utf-8", errors="replace")
                    stderr_chunks.append(text)
                    captured += len(text)

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
            await cleanup_phase_config()
            raise
        await asyncio.gather(stdout_task, stderr_task)
        stderr_text = "".join(stderr_chunks)
        if stderr_seen:
            await log_cb(
                ctx.project_id,
                "[opencode] stderr emitted (captured in _audit/opencode_errors.json on failure)",
                "warning",
            )

        def record_failure() -> str:
            return self._record_step_failure(
                ctx.staging_dir,
                phase=phase,
                step=step,
                model_route=self.model_route,
                returncode=proc.returncode,
                error_events=raw_errors,
                stderr_text=stderr_text,
            )

        if proc.returncode != 0:
            if not events:
                ledger.release_unstarted(ledger_target)
            reason = record_failure()
            await cleanup_phase_config()
            raise OpenCodeCompatibilityError(
                self._failure_message(
                    f"OpenCode {step} exited with code {proc.returncode}", reason
                )
            )
        if saw_error:
            reason = record_failure()
            await cleanup_phase_config()
            raise OpenCodeCompatibilityError(
                self._failure_message(f"OpenCode {step} emitted an error event", reason)
            )
        if not actual_session:
            reason = record_failure()
            await cleanup_phase_config()
            raise OpenCodeCompatibilityError(
                self._failure_message(f"OpenCode {step} returned no session identifier", reason)
            )
        required_artifacts: list[str] = []
        if require_outputs:
            required_artifacts = [contract.output_artifact for contract in contracts]
            if phase == "gather":
                required_artifacts.extend(
                    str(contract.metadata["validation_receipt"])
                    for contract in contracts
                    if contract.metadata.get("validation_receipt")
                )
        missing = [
            artifact
            for artifact in required_artifacts
            if not (ctx.staging_dir / artifact).is_file()
        ]
        if missing:
            await cleanup_phase_config()
            raise OpenCodeCompatibilityError(
                f"OpenCode {step} did not write required outputs or validation receipts: "
                f"{', '.join(missing[:5])}"
            )
        await log_cb(ctx.project_id, f"OpenCode {phase}: {step} complete", "info")
        result = _PhaseExecution(
            session_id=actual_session,
            events=events,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=int((time.monotonic() - step_started) * 1000),
        )
        await cleanup_phase_config()
        return result

    async def _execute_analyze_contract(
        self,
        *,
        resolved: str,
        ctx: DocGenContext,
        contract: OpenCodeTaskContract,
        allowlist: dict[str, tuple[str, ...]],
        ledger: OpenCodeAttemptLedger,
        log_cb: LogCallback,
        require_outputs: bool = True,
    ) -> tuple[str, _PhaseExecution]:
        """Compatibility helper for a one-contract Analyze invocation.

        Adaptive group work uses ``_execute_analyze_batch``. This remains the
        explicit one-contract path for callers that require a dependency
        barrier, while keeping artifacts rather than conversation state as the
        durable hand-off.
        """

        step = f"analyze_{_safe_name(contract.target)}"
        execution = await self._execute_step(
            resolved=resolved,
            ctx=ctx,
            phase="analyze",
            step=step,
            contracts=[contract],
            allowlist=allowlist,
            session_id=None,
            ledger=ledger,
            log_cb=log_cb,
            require_outputs=require_outputs,
        )
        return step, execution

    async def _execute_analyze_batch(
        self,
        *,
        resolved: str,
        ctx: DocGenContext,
        batch: _ContractBatch,
        allowlist: dict[str, tuple[str, ...]],
        ledger: OpenCodeAttemptLedger,
        log_cb: LogCallback,
        require_outputs: bool = True,
    ) -> tuple[str, _PhaseExecution]:
        """Execute an ordered group-analysis batch in one fresh session."""

        step = f"analyze_batch_{batch.index:03d}"
        execution = await self._execute_step(
            resolved=resolved,
            ctx=ctx,
            phase="analyze",
            step=step,
            contracts=list(batch.contracts),
            allowlist=allowlist,
            session_id=None,
            ledger=ledger,
            log_cb=log_cb,
            require_outputs=require_outputs,
        )
        return step, execution

    async def _execute_produce_batch(
        self,
        *,
        resolved: str,
        ctx: DocGenContext,
        step: str,
        contracts: list[OpenCodeTaskContract],
        allowlist: dict[str, tuple[str, ...]],
        ledger: OpenCodeAttemptLedger,
        log_cb: LogCallback,
        require_outputs: bool = True,
    ) -> _PhaseExecution:
        """Execute one Produce batch in a fresh, artifact-driven session.

        Each OpenCode invocation owns an isolated temporary data/state home that
        is removed when the invocation completes. A returned session id is
        therefore audit metadata only: attempting to continue it from the next
        invocation would point OpenCode at a session store that no longer
        exists. Produce dependencies are the validated files written by earlier
        batches, not conversation history.
        """

        return await self._execute_step(
            resolved=resolved,
            ctx=ctx,
            phase="produce",
            step=step,
            contracts=contracts,
            allowlist=allowlist,
            session_id=None,
            ledger=ledger,
            log_cb=log_cb,
            require_outputs=require_outputs,
        )

    @staticmethod
    def _missing_contracts(
        ctx: DocGenContext,
        contracts: list[OpenCodeTaskContract],
    ) -> list[OpenCodeTaskContract]:
        """Return contracts without a readable, non-empty durable output."""

        missing: list[OpenCodeTaskContract] = []
        for contract in contracts:
            path = ctx.staging_dir / contract.output_artifact
            try:
                complete = path.is_file() and path.stat().st_size > 0
            except OSError:
                complete = False
            if not complete:
                missing.append(contract)
        return missing

    async def _recover_missing_contracts(
        self,
        *,
        resolved: str,
        ctx: DocGenContext,
        phase: str,
        step_prefix: str,
        contracts: list[OpenCodeTaskContract],
        allowlist: dict[str, tuple[str, ...]],
        ledger: OpenCodeAttemptLedger,
        log_cb: LogCallback,
        on_complete: Callable[[str, _PhaseExecution], None],
    ) -> None:
        """Retry only missing outputs in bounded, one-contract fresh sessions."""

        pending = self._missing_contracts(ctx, contracts)
        max_followups = max(0, int(config.opencode_phase_max_followups))
        for attempt in range(1, max_followups + 1):
            if not pending:
                return
            await log_cb(
                ctx.project_id,
                f"OpenCode {phase} recovery {attempt}/{max_followups}: "
                f"{len(pending)} missing artifact(s), one focused session each",
                "warning",
            )
            concurrency_limit = (
                config.opencode_analyze_concurrency
                if phase == "analyze"
                else config.opencode_produce_concurrency
            )
            semaphore = asyncio.Semaphore(
                min(len(pending), max(1, int(concurrency_limit)))
            )

            async def recover(
                position: int,
                contract: OpenCodeTaskContract,
                attempt_number: int = attempt,
                limiter: asyncio.Semaphore = semaphore,
            ) -> None:
                async with limiter:
                    step = (
                        f"{step_prefix}_recovery_{attempt_number}_{position:03d}_"
                        f"{_safe_name(contract.target)}"
                    )
                    try:
                        execution = await self._execute_step(
                            resolved=resolved,
                            ctx=ctx,
                            phase=phase,
                            step=step,
                            contracts=[contract],
                            allowlist=allowlist,
                            session_id=None,
                            ledger=ledger,
                            log_cb=log_cb,
                            require_outputs=False,
                        )
                    except (OpenCodeCompatibilityError, OSError, TimeoutError) as exc:
                        await log_cb(
                            ctx.project_id,
                            f"OpenCode {phase} recovery for {contract.target} ended early: {exc}",
                            "warning",
                        )
                        return
                    on_complete(step, execution)

            await asyncio.gather(
                *(recover(position, contract) for position, contract in enumerate(pending, 1))
            )
            pending = self._missing_contracts(ctx, contracts)

        if pending:
            raise OpenCodeCompatibilityError(
                f"OpenCode {phase} recovery exhausted with missing outputs: "
                + ", ".join(contract.output_artifact for contract in pending[:5])
            )

    async def _execute_produce_contracts(
        self,
        *,
        resolved: str,
        ctx: DocGenContext,
        step: str,
        contracts: list[OpenCodeTaskContract],
        allowlist: dict[str, tuple[str, ...]],
        ledger: OpenCodeAttemptLedger,
        log_cb: LogCallback,
        on_complete: Callable[[str, _PhaseExecution], None],
        force: bool = False,
    ) -> None:
        """Run one Produce dependency stage with per-artifact recovery."""

        if force:
            for contract in contracts:
                (ctx.staging_dir / contract.output_artifact).unlink(missing_ok=True)
        pending = self._missing_contracts(ctx, contracts)
        if not pending:
            return

        batches = self._plan_contract_batches(ctx, pending)
        concurrency = min(
            len(batches),
            max(1, int(config.opencode_produce_concurrency)),
        )
        await log_cb(
            ctx.project_id,
            f"OpenCode produce plan ({step}): {len(pending)} remaining artifact(s), "
            f"{len(batches)} fresh session batch(es), concurrency={concurrency}, "
            f"target={max(1, int(config.opencode_batch_target_input_tokens))} tokens, "
            f"estimated=[{self._batch_plan_summary(batches)}] (! = over target)",
            "info",
        )
        semaphore = asyncio.Semaphore(concurrency)

        async def execute(batch: _ContractBatch) -> None:
            batch_step = f"{step}_batch_{batch.index:03d}"
            async with semaphore:
                try:
                    execution = await self._execute_produce_batch(
                        resolved=resolved,
                        ctx=ctx,
                        step=batch_step,
                        contracts=list(batch.contracts),
                        allowlist=allowlist,
                        ledger=ledger,
                        log_cb=log_cb,
                        require_outputs=False,
                    )
                except (OpenCodeCompatibilityError, OSError, TimeoutError) as exc:
                    await log_cb(
                        ctx.project_id,
                        f"OpenCode produce batch {batch_step} ended early: {exc}; "
                        "completed artifacts were preserved",
                        "warning",
                    )
                    return
                on_complete(batch_step, execution)

        await asyncio.gather(*(execute(batch) for batch in batches))
        await self._recover_missing_contracts(
            resolved=resolved,
            ctx=ctx,
            phase="produce",
            step_prefix=step,
            contracts=contracts,
            allowlist=allowlist,
            ledger=ledger,
            log_cb=log_cb,
            on_complete=on_complete,
        )

    @staticmethod
    def _document_contract_errors(
        ctx: DocGenContext,
        facts: Any,
        doc_names: list[str],
    ) -> dict[str, list[str]]:
        """Return deterministic assistant-contract errors for existing docs."""

        from mcp_server.providers.custom.validators import _assistant_doc_contract_errors

        selected = set(doc_names)
        errors: dict[str, list[str]] = defaultdict(list)
        for error in _assistant_doc_contract_errors(ctx.staging_dir, facts):
            match = re.match(r"Generated doc ([A-Za-z0-9_]+)\.md\b", error)
            if match and match.group(1) in selected:
                errors[match.group(1)].append(error)
        return dict(errors)

    async def _repair_produce_documents(
        self,
        *,
        resolved: str,
        ctx: DocGenContext,
        facts: Any,
        harness: Any,
        repair_step: str,
        doc_names: list[str],
        allowlist: dict[str, tuple[str, ...]],
        ledger: OpenCodeAttemptLedger,
        log_cb: LogCallback,
    ) -> list[tuple[str, _PhaseExecution]]:
        """Run one evidence-backed repair pass for structural doc violations.

        Presence is not semantic completion. This gate runs both after fresh
        Produce work and after resume skipped an existing artifact, preventing
        a permanently invalid checkpoint from failing at the very end forever.
        """

        errors = self._document_contract_errors(ctx, facts, doc_names)
        if not errors:
            return []
        await log_cb(
            ctx.project_id,
            "OpenCode produce validation found document contract gaps; starting one "
            "targeted repair session for " + ", ".join(sorted(errors)),
            "warning",
        )
        contracts: list[OpenCodeTaskContract] = []
        for doc_name in sorted(errors):
            original = self._doc_contract(ctx, facts, harness, doc_name)
            issue_text = "\n".join(f"- {item}" for item in errors[doc_name])
            contracts.append(
                OpenCodeTaskContract(
                    phase="produce",
                    target=f"repair_document:{doc_name}",
                    prompt_artifact="deterministic:assistant_doc_contract_repair",
                    prompt=(
                        f"Repair the existing `{doc_name}.md` in place. Preserve its supported "
                        "content and evidence pointers; make only the structural/content additions "
                        "needed to satisfy every deterministic validation error below. Read the "
                        "existing document first, then the supplied authoritative evidence pack. "
                        "Do not merely describe a patch: write the complete repaired document to "
                        f"`{doc_name}.md`. Validation errors:\n{issue_text}\n\n"
                        "Required terms are semantic obligations, not decorative keywords. Put them "
                        "in the relevant heading, field, or table column with evidence-backed values."
                    ),
                    evidence_artifacts=tuple(
                        dict.fromkeys((f"{doc_name}.md", *original.evidence_artifacts))
                    ),
                    output_artifact=f"{doc_name}.md",
                )
            )
        concurrency = min(
            len(contracts),
            max(1, int(config.opencode_produce_concurrency)),
        )
        semaphore = asyncio.Semaphore(concurrency)

        async def repair_one(
            position: int,
            contract: OpenCodeTaskContract,
        ) -> tuple[str, _PhaseExecution] | None:
            step = f"{repair_step}_{position:03d}_{_safe_name(contract.target)}"
            async with semaphore:
                try:
                    execution = await self._execute_produce_batch(
                        resolved=resolved,
                        ctx=ctx,
                        step=step,
                        contracts=[contract],
                        allowlist=allowlist,
                        ledger=ledger,
                        log_cb=log_cb,
                        require_outputs=False,
                    )
                except (OpenCodeCompatibilityError, OSError, TimeoutError) as exc:
                    await log_cb(
                        ctx.project_id,
                        f"OpenCode document repair for {contract.target} ended early: {exc}",
                        "warning",
                    )
                    return None
                return step, execution

        attempted = await asyncio.gather(
            *(repair_one(position, contract) for position, contract in enumerate(contracts, 1))
        )
        executions = [item for item in attempted if item is not None]
        remaining = self._document_contract_errors(ctx, facts, doc_names)
        if remaining:
            flattened = [item for values in remaining.values() for item in values]
            raise OpenCodeCompatibilityError(
                "OpenCode document contract repair remained invalid: "
                + "; ".join(flattened[:3])
            )
        return executions

    async def _execute_gather_contract(
        self,
        *,
        resolved: str,
        ctx: DocGenContext,
        contract: OpenCodeTaskContract,
        allowlist: dict[str, tuple[str, ...]],
        ledger: OpenCodeAttemptLedger,
        log_cb: LogCallback,
    ) -> tuple[str, _PhaseExecution]:
        """Compatibility helper for a one-contract Gather invocation.

        Normal static Gather work uses ``_execute_gather_batch``; validation and
        resume still operate on this contract's own artifacts and receipt.
        """

        step = f"gather_{_safe_name(contract.target)}"
        execution = await self._execute_step(
            resolved=resolved,
            ctx=ctx,
            phase="gather",
            step=step,
            contracts=[contract],
            allowlist=allowlist,
            session_id=None,
            ledger=ledger,
            log_cb=log_cb,
        )
        return step, execution

    async def _execute_gather_batch(
        self,
        *,
        resolved: str,
        ctx: DocGenContext,
        batch: _ContractBatch,
        allowlist: dict[str, tuple[str, ...]],
        ledger: OpenCodeAttemptLedger,
        log_cb: LogCallback,
    ) -> tuple[str, _PhaseExecution]:
        """Execute an ordered Gather batch in one fresh session."""

        step = f"gather_batch_{batch.index:03d}"
        execution = await self._execute_step(
            resolved=resolved,
            ctx=ctx,
            phase="gather",
            step=step,
            contracts=list(batch.contracts),
            allowlist=allowlist,
            session_id=None,
            ledger=ledger,
            log_cb=log_cb,
        )
        return step, execution

    async def _execute_gather_contracts(
        self,
        *,
        resolved: str,
        ctx: DocGenContext,
        contracts: list[OpenCodeTaskContract],
        allowlist: dict[str, tuple[str, ...]],
        ledger: OpenCodeAttemptLedger,
        log_cb: LogCallback,
        on_complete: Callable[[str, _PhaseExecution], None],
    ) -> None:
        """Pack and run unfinished Gather contracts with bounded concurrency.

        Contracts remain independent durable checkpoints. A successful batch is
        recorded immediately; on failure, unfinished sibling batch subprocesses
        are cancelled. A later run filters surviving validated contracts before
        planning a new set of adaptive batches.
        """

        pending: list[OpenCodeTaskContract] = []
        for contract in contracts:
            if ctx.resume_state is not None and self._contracts_complete(
                ctx, "gather", [contract]
            ):
                await log_cb(
                    ctx.project_id,
                    f"OpenCode RESUME: {contract.target} output already complete; "
                    "skipping model work",
                    "info",
                )
                continue
            pending.append(contract)

        if not pending:
            return

        batches = self._plan_contract_batches(ctx, pending)
        concurrency = min(
            len(batches), max(1, int(config.opencode_gather_concurrency))
        )
        await log_cb(
            ctx.project_id,
            f"OpenCode gather plan: {len(pending)} remaining contract(s), "
            f"{len(batches)} fresh session batch(es), concurrency={concurrency}, "
            f"target={max(1, int(config.opencode_batch_target_input_tokens))} tokens, "
            f"estimated=[{self._batch_plan_summary(batches)}] (! = over target)",
            "info",
        )
        semaphore = asyncio.Semaphore(concurrency)

        async def execute(batch: _ContractBatch) -> None:
            async with semaphore:
                step, execution = await self._execute_gather_batch(
                    resolved=resolved,
                    ctx=ctx,
                    batch=batch,
                    allowlist=allowlist,
                    ledger=ledger,
                    log_cb=log_cb,
                )
                # No await between accepted output and its durable usage receipt.
                on_complete(step, execution)

        tasks = [asyncio.create_task(execute(batch)) for batch in batches]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    @staticmethod
    def _investigation_scope_prompt(
        logical_scope: Any,
        evidence_scopes: list[Any],
        *,
        followup: bool,
        progress_by_scope: dict[str, Any] | None = None,
        recovery_artifacts: tuple[str, ...] = (),
    ) -> str:
        progress_by_scope = progress_by_scope or {}
        assignments = [
            {
                "scope_id": scope.scope_id,
                "group": scope.group,
                "allowed_units": list(
                    progress_by_scope[scope.scope_id].remaining_units
                    if followup and scope.scope_id in progress_by_scope
                    else scope.allowed_units
                ),
                "suggested_start": scope.source_ref,
            }
            for scope in evidence_scopes
        ]
        continuation = (
            "This is a recovery pass. The assignments contain only unresolved units. "
            "Call get_progress first, then work only on those units. Read or search only the "
            "coordinator-authorized recovery evidence artifacts listed below; Nexus runtime "
            "directories such as _audit, _transient, _assessment, and prior contracts are "
            "outside this recovery boundary and are irrelevant. Authorized recovery evidence:\n"
            + json.dumps(list(recovery_artifacts), indent=2)
            + "\n"
            if followup
            else "Call get_progress for each scope before beginning. "
        )
        exploration_policy = (
            "You have native read and targeted grep/glob/list access only for the authorized "
            "recovery evidence files; choose what to inspect within that boundary. "
            if followup
            else "You have native read, grep, glob, and list tools across the sanitized workspace; "
            "choose what to inspect based on what you discover. "
        )
        return (
            "Investigate the assigned Nexus project scope as a coding agent. "
            + exploration_policy
            + "Do not edit files and do not "
            "construct legacy JSON. Use nexus_investigation_submit_evidence in small batches as "
            "soon as findings are supported. For a unit that genuinely cannot be evidenced, use "
            "nexus_investigation_report_gap with a concrete reason. Validation errors are feedback: "
            "correct the submission and continue. Finish every listed scope with "
            "nexus_investigation_finish_scope, which only succeeds after every assigned unit is "
            "accounted for. "
            + continuation
            + "Useful deterministic starting points are facts_manifest.json, _preprocess/, "
            "engineering_facts.json, dependency_map.md, and config_runtime_map.md when present. "
            f"Logical scope: {logical_scope.scope_id}. Assignments:\n"
            + json.dumps(assignments, indent=2)
            + "\nDo not stop after merely describing a plan; explore, submit, check progress, and finish."
        )

    @staticmethod
    def _investigation_recovery_artifacts(
        ctx: DocGenContext,
        evidence_scopes: list[Any],
    ) -> tuple[str, ...]:
        """Return the authoritative files a focused Gather recovery may inspect."""

        candidates = [scope.source_ref for scope in evidence_scopes]
        candidates.extend(
            (
                "facts_manifest.json",
                "engineering_facts.json",
                "engineering_facts.md",
                "entity_index.csv",
                "usage_index.csv",
                "dependency_map.md",
                "config_runtime_map.json",
                "config_runtime_map.md",
                "architecture_map.md",
                "data_flow_map.md",
            )
        )
        workspace_root = ctx.staging_dir.resolve()
        allowed: list[str] = []
        for raw_artifact in dict.fromkeys(str(item) for item in candidates if item):
            candidate = (workspace_root / raw_artifact).resolve()
            try:
                relative = candidate.relative_to(workspace_root).as_posix()
            except ValueError:
                continue
            if candidate.is_file():
                allowed.append(relative)
        return tuple(allowed)

    async def _execute_gather_scopes(
        self,
        *,
        resolved: str,
        ctx: DocGenContext,
        facts: Any,
        contracts: list[OpenCodeTaskContract],
        ledger: OpenCodeAttemptLedger,
        log_cb: LogCallback,
        on_complete: Callable[[str, _PhaseExecution], None],
    ) -> Any:
        """Run adaptive, tool-driven investigation scopes with durable recovery.

        Session boundaries are a capacity choice, not the evidence boundary.
        Accepted tool submissions are persisted immediately. A crashed or
        incomplete agent gets a bounded fresh recovery session that sees the
        same progress, and any remainder becomes an explicit gap instead of
        cancelling successful sibling scopes.
        """

        from mcp_server.providers.opencode_evidence import (
            EvidenceScope,
            FinishScopeRequest,
            GetProgressRequest,
            OpenCodeEvidenceStore,
            ReportGapRequest,
        )
        from mcp_server.providers.opencode_investigation_mcp import InvestigationFacade
        from mcp_server.providers.opencode_scopes import ensure_scope_plan

        plan = ensure_scope_plan(
            ctx.staging_dir,
            facts,
            partitions=([ctx.partition] if ctx.partition is not None else None),
            max_units_per_scope=max(1, int(config.opencode_scope_max_units)),
            max_groups_per_scope=max(1, int(config.opencode_scope_max_groups)),
        )
        store = OpenCodeEvidenceStore(ctx.staging_dir, facts)
        units_by_name = {unit.name: unit for unit in facts.units}
        source_by_group = {
            str(contract.metadata.get("group")): str(contract.metadata.get("source_ref"))
            for contract in contracts
            if contract.metadata.get("group") and contract.metadata.get("source_ref")
        }

        def source_ref_for(group: str) -> str:
            preprocess = f"_preprocess/{group}.md"
            if (ctx.staging_dir / preprocess).is_file():
                return preprocess
            return source_by_group.get(group, preprocess)
        assignments: dict[str, list[Any]] = {}
        for logical in plan.scopes:
            evidence_scopes: list[Any] = []
            for group in logical.groups:
                unit_names = tuple(
                    name
                    for name in logical.unit_names
                    if name in units_by_name and units_by_name[name].group == group
                )
                if not unit_names:
                    continue
                scope_id = _safe_name(f"{logical.scope_id}__{group}")
                if len(scope_id) > 127:
                    digest = hashlib.sha256(scope_id.encode("utf-8")).hexdigest()[:12]
                    scope_id = f"{scope_id[:114]}_{digest}"
                scope = EvidenceScope(
                    scope_id=scope_id,
                    group=group,
                    allowed_units=unit_names,
                    source_ref=source_ref_for(group),
                )
                store.register_scope(scope)
                if ctx.resume_state is not None:
                    store.reopen_system_gaps(
                        scope.scope_id,
                        reason=_GATHER_EXHAUSTED_GAP_REASON,
                    )
                evidence_scopes.append(scope)
            assignments[logical.scope_id] = evidence_scopes

        pending = [
            logical
            for logical in plan.scopes
            if any(
                not store.get_progress(GetProgressRequest(scope_id=scope.scope_id)).complete
                for scope in assignments[logical.scope_id]
            )
        ]
        concurrency = min(
            len(pending) or 1,
            max(1, int(config.opencode_scope_concurrency)),
        )
        await log_cb(
            ctx.project_id,
            f"OpenCode Gather v2 plan: {facts.unit_count} unit(s), "
            f"{len(plan.scopes)} adaptive scope session(s), concurrency={concurrency}; "
            "native exploration + durable evidence tools",
            "info",
        )
        semaphore = asyncio.Semaphore(concurrency)

        async def execute(logical: Any) -> None:
            evidence_scopes = assignments[logical.scope_id]
            if not evidence_scopes:
                return
            async with semaphore:
                max_attempts = 1 + max(0, int(config.opencode_scope_max_followups))
                for attempt in range(max_attempts):
                    unfinished = [
                        scope
                        for scope in evidence_scopes
                        if not store.get_progress(
                            GetProgressRequest(scope_id=scope.scope_id)
                        ).complete
                    ]
                    if not unfinished:
                        return
                    progress_before = {
                        scope.scope_id: store.get_progress(
                            GetProgressRequest(scope_id=scope.scope_id)
                        )
                        for scope in unfinished
                    }
                    followup = attempt > 0
                    recovery_artifacts = (
                        self._investigation_recovery_artifacts(ctx, unfinished)
                        if followup
                        else None
                    )
                    suffix = "" if attempt == 0 else f"_recovery_{attempt}"
                    step = f"gather_scope_{_safe_name(logical.scope_id)}{suffix}"
                    facade = InvestigationFacade(store, {scope.scope_id for scope in unfinished})
                    try:
                        execution = await self._execute_step(
                            resolved=resolved,
                            ctx=ctx,
                            phase="gather",
                            step=step,
                            contracts=[],
                            allowlist={},
                            session_id=None,
                            ledger=ledger,
                            log_cb=log_cb,
                            investigation_facade=facade,
                            exploration_artifacts=recovery_artifacts,
                            prompt_override=self._investigation_scope_prompt(
                                logical,
                                unfinished,
                                followup=followup,
                                progress_by_scope=progress_before,
                                recovery_artifacts=recovery_artifacts or (),
                            ),
                            require_outputs=False,
                        )
                        on_complete(step, execution)
                    except (OpenCodeCompatibilityError, OSError, TimeoutError) as exc:
                        await log_cb(
                            ctx.project_id,
                            f"OpenCode Gather v2 scope {logical.scope_id} attempt "
                            f"{attempt + 1}/{max_attempts} ended early: {exc}; "
                            "accepted evidence was preserved",
                            "warning",
                        )
                    progress_after = {
                        scope.scope_id: store.get_progress(
                            GetProgressRequest(scope_id=scope.scope_id)
                        )
                        for scope in unfinished
                    }
                    remaining_before = sum(
                        len(progress.remaining_units) for progress in progress_before.values()
                    )
                    remaining_after = sum(
                        len(progress.remaining_units) for progress in progress_after.values()
                    )
                    evidence_delta = sum(
                        progress_after[scope_id].evidence_count - progress.evidence_count
                        for scope_id, progress in progress_before.items()
                    )
                    gap_delta = sum(
                        len(progress_after[scope_id].gap_units) - len(progress.gap_units)
                        for scope_id, progress in progress_before.items()
                    )
                    made_progress = any(
                        progress_after[scope_id].version > progress.version
                        for scope_id, progress in progress_before.items()
                    )
                    boundary = (
                        f", recovery_evidence_files={len(recovery_artifacts or ())}"
                        if followup
                        else ""
                    )
                    await log_cb(
                        ctx.project_id,
                        f"OpenCode Gather v2 scope {logical.scope_id} attempt "
                        f"{attempt + 1}/{max_attempts} durable progress: "
                        f"remaining={remaining_before}->{remaining_after}, "
                        f"evidence_delta={evidence_delta}, gap_delta={gap_delta}{boundary}",
                        "info" if made_progress else "warning",
                    )

                # Soft close: explicit gaps are safer and more useful than
                # throwing away every successful scope because one agent ended.
                gap_count = 0
                for scope in evidence_scopes:
                    progress = store.get_progress(GetProgressRequest(scope_id=scope.scope_id))
                    for unit_name in progress.remaining_units:
                        store.report_gap(
                            ReportGapRequest(
                                scope_id=scope.scope_id,
                                unit_name=unit_name,
                                reason=_GATHER_EXHAUSTED_GAP_REASON,
                            )
                        )
                        gap_count += 1
                    progress = store.get_progress(GetProgressRequest(scope_id=scope.scope_id))
                    if not progress.complete:
                        store.finish_scope(FinishScopeRequest(scope_id=scope.scope_id))
                if gap_count:
                    await log_cb(
                        ctx.project_id,
                        f"OpenCode Gather v2 scope {logical.scope_id} closed with "
                        f"{gap_count} explicit coverage gap(s); continuing with partial evidence",
                        "warning",
                    )

        await asyncio.gather(*(execute(logical) for logical in pending))
        return store

    async def _execute_analyze_contracts(
        self,
        *,
        resolved: str,
        ctx: DocGenContext,
        contracts: list[OpenCodeTaskContract],
        allowlist: dict[str, tuple[str, ...]],
        ledger: OpenCodeAttemptLedger,
        log_cb: LogCallback,
        on_complete: Callable[[str, _PhaseExecution], None],
        force: bool = False,
    ) -> None:
        """Run adaptive group batches, then scorecard as a dependency barrier."""

        pending_groups: list[OpenCodeTaskContract] = []
        pending_scorecards: list[OpenCodeTaskContract] = []
        for contract in contracts:
            if force:
                # Gather evidence changed, so an existing downstream artifact is
                # stale. Remove only this generated checkpoint before dispatch;
                # otherwise simple existence could falsely accept an old file
                # when the new session omits it.
                (ctx.staging_dir / contract.output_artifact).unlink(missing_ok=True)
            if not force and ctx.resume_state is not None and self._contracts_complete(
                ctx, "analyze", [contract]
            ):
                await log_cb(
                    ctx.project_id,
                    f"OpenCode RESUME: {contract.target} output already complete; "
                    "skipping model work",
                    "info",
                )
                continue
            if contract.target == "scorecard":
                pending_scorecards.append(contract)
            else:
                pending_groups.append(contract)

        batches = self._plan_contract_batches(ctx, pending_groups)
        if batches:
            concurrency = min(
                len(batches),
                max(1, int(config.opencode_analyze_concurrency)),
            )
            await log_cb(
                ctx.project_id,
                f"OpenCode analyze plan: {len(pending_groups)} remaining group contract(s), "
                f"{len(batches)} fresh session batch(es), concurrency={concurrency}, "
                f"target={max(1, int(config.opencode_batch_target_input_tokens))} tokens, "
                f"estimated=[{self._batch_plan_summary(batches)}] (! = over target)",
                "info",
            )
            semaphore = asyncio.Semaphore(concurrency)

            async def execute(batch: _ContractBatch) -> None:
                async with semaphore:
                    try:
                        step, execution = await self._execute_analyze_batch(
                            resolved=resolved,
                            ctx=ctx,
                            batch=batch,
                            allowlist=allowlist,
                            ledger=ledger,
                            log_cb=log_cb,
                            require_outputs=False,
                        )
                    except (OpenCodeCompatibilityError, OSError, TimeoutError) as exc:
                        await log_cb(
                            ctx.project_id,
                            f"OpenCode analyze batch {batch.index} ended early: {exc}; "
                            "completed artifacts were preserved",
                            "warning",
                        )
                        return
                    on_complete(step, execution)

            tasks = [asyncio.create_task(execute(batch)) for batch in batches]
            # A weak batch must not cancel healthy siblings. Every session is
            # allowed to settle, then only the still-missing contracts recover.
            await asyncio.gather(*tasks)
            await self._recover_missing_contracts(
                resolved=resolved,
                ctx=ctx,
                phase="analyze",
                step_prefix="analyze_groups",
                contracts=pending_groups,
                allowlist=allowlist,
                ledger=ledger,
                log_cb=log_cb,
                on_complete=on_complete,
            )

        # The scorecard reads the complete analysis directory. Never dispatch it
        # until all group batches have completed successfully.
        for scorecard in pending_scorecards:
            await log_cb(
                ctx.project_id,
                "OpenCode analyze: group batches complete; starting scorecard barrier",
                "info",
            )
            try:
                step, execution = await self._execute_analyze_contract(
                    resolved=resolved,
                    ctx=ctx,
                    contract=scorecard,
                    allowlist=allowlist,
                    ledger=ledger,
                    log_cb=log_cb,
                    require_outputs=False,
                )
            except (OpenCodeCompatibilityError, OSError, TimeoutError) as exc:
                await log_cb(
                    ctx.project_id,
                    f"OpenCode analyze scorecard ended early: {exc}; attempting focused recovery",
                    "warning",
                )
            else:
                on_complete(step, execution)
            await self._recover_missing_contracts(
                resolved=resolved,
                ctx=ctx,
                phase="analyze",
                step_prefix="analyze_scorecard",
                contracts=[scorecard],
                allowlist=allowlist,
                ledger=ledger,
                log_cb=log_cb,
                on_complete=on_complete,
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
        for artifact in required:
            path = ctx.staging_dir / artifact
            try:
                if not path.is_file() or path.stat().st_size <= 0:
                    return False
            except OSError:
                return False
        return True

    @staticmethod
    def _gather_evidence_fingerprint(ctx: DocGenContext) -> str:
        """Hash durable evidence meaning, excluding retry bookkeeping versions."""

        material: list[dict[str, Any]] = []
        scopes_root = ctx.staging_dir / "_evidence" / "scopes"
        if scopes_root.is_dir():
            for path in sorted(scopes_root.glob("*/state.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                material.append(
                    {
                        "scope": path.parent.name,
                        "evidence": payload.get("evidence", []),
                        "gaps": payload.get("gaps", {}),
                    }
                )
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

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
                "OpenCode analysis covered zero canonical units: " + "; ".join(coverage.errors[:3])
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
        from mcp_server.providers.custom.pipeline.helpers import HarnessHelpersImpl
        from mcp_server.providers.custom.pipeline.phases.phase3_docs import _thin_bundle_notice
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
                "OpenCode required-document validation failed: " + "; ".join(doc_report.errors[:3])
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
        ledger = OpenCodeAttemptLedger(ctx.staging_dir, run_id=ctx.run_id)
        started = int(time.time() * 1000)
        all_events: list[dict[str, Any]] = []
        phase_receipts = (
            self._read_usage_receipts(audit_dir) if ctx.resume_state is not None else []
        )
        last_session_id = next(
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
        connector_runtime: Any | None = None

        def record_execution(phase: str, step: str, execution: _PhaseExecution) -> None:
            """Record model-reported usage before deterministic validation runs."""

            nonlocal \
                last_session_id, \
                total_input, \
                total_output, \
                total_cost, \
                turns, \
                active_phase, \
                active_step
            last_session_id = execution.session_id
            all_events.extend(execution.events)
            total_input += execution.input_tokens
            total_output += execution.output_tokens
            total_cost += execution.cost_usd
            turns += 1
            phase_receipts.append(
                {
                    "phase": phase,
                    "step": step,
                    "status": "model_completed",
                    "session_id": execution.session_id,
                    "input_tokens": execution.input_tokens,
                    "output_tokens": execution.output_tokens,
                    "cost_usd": execution.cost_usd,
                    "latency_ms": execution.latency_ms,
                }
            )
            self._write_usage_receipts(audit_dir, phase_receipts)
            self._record_execution(state, phase, step, execution)
            active_phase = None
            active_step = None

        try:
            resolved, allowlist = self._preflight()
            facts, harness, state = await self._prepare_run(ctx, log_cb)
            gather_changed = False

            gather = self._gather_contracts(ctx, facts, harness, allowlist)
            if not gather:
                await log_cb(
                    ctx.project_id,
                    "OpenCode Gather skipped by the deterministic project policy",
                    "info",
                )
            elif (
                ctx.resume_state is not None
                and str(config.opencode_gather_mode).strip().lower() != "tools"
                and self._contracts_complete(ctx, "gather", gather)
            ):
                await log_cb(
                    ctx.project_id,
                    "OpenCode RESUME: gather outputs already complete; validating checkpoint",
                    "info",
                )
            else:
                gather_mode = str(config.opencode_gather_mode).strip().lower()
                if gather_mode == "tools":
                    from mcp_server.providers.opencode_evidence_export import (
                        export_legacy_gather_artifacts,
                    )

                    evidence_before = self._gather_evidence_fingerprint(ctx)
                    active_phase, active_step = "gather", "adaptive_scopes"
                    await self._execute_gather_scopes(
                        resolved=resolved,
                        ctx=ctx,
                        facts=facts,
                        contracts=gather,
                        ledger=ledger,
                        log_cb=log_cb,
                        on_complete=lambda step, execution: record_execution(
                            "gather", step, execution
                        ),
                    )
                    export_legacy_gather_artifacts(ctx.staging_dir, facts, gather)
                    gather_changed = (
                        self._gather_evidence_fingerprint(ctx) != evidence_before
                    )
                elif gather_mode == "contracts":
                    active_phase, active_step = "gather", "adaptive_batches"
                    await self._execute_gather_contracts(
                        resolved=resolved,
                        ctx=ctx,
                        contracts=gather,
                        allowlist=allowlist,
                        ledger=ledger,
                        log_cb=log_cb,
                        on_complete=lambda step, execution: record_execution(
                            "gather", step, execution
                        ),
                    )
                else:
                    raise OpenCodeCompatibilityError(
                        "MCP_OPENCODE_GATHER_MODE must be 'tools' or 'contracts'"
                    )
                active_phase = None
                active_step = None
            self._finalize_gather(ctx, facts, harness, state, gather)

            # Static Gather state is fully represented by validated artifacts.
            # Connector Gather starts fresh and must not inherit an arbitrary
            # task conversation.
            # Runtime evidence is deliberately a second Gather sub-step: static
            # investigation has now produced canonical code entities, while
            # Analyze/Produce have not yet received any connector authority.
            connector_runtime = self._connector_runtime(ctx)
            if connector_runtime is not None:
                external = self._external_evidence_contracts(
                    ctx, facts, connector_runtime, allowlist
                )
                try:
                    if external:
                        active_phase, active_step = "gather", "external_evidence"
                        execution = await self._execute_step(
                            resolved=resolved,
                            ctx=ctx,
                            phase="gather",
                            step="external_evidence",
                            contracts=external,
                            allowlist=allowlist,
                            session_id=None,
                            ledger=ledger,
                            log_cb=log_cb,
                            runtime=connector_runtime,
                        )
                        record_execution("gather", "external_evidence", execution)
                    self._finalize_external_evidence(ctx, facts, connector_runtime, external)
                except (OpenCodeCompatibilityError, OSError):
                    # Connector evidence is supplementary by default. Retain a
                    # sanitized limitation and continue with scanner evidence.
                    await log_cb(
                        ctx.project_id,
                        "OpenCode connector evidence unavailable; continuing without live evidence",
                        "warning",
                    )
                    self._finalize_external_evidence(
                        ctx,
                        facts,
                        connector_runtime,
                        external,
                        limitation="source_unavailable",
                    )
                finally:
                    manifest = connector_runtime.audit_manifest()
                    (audit_dir / "connector_run_manifest.json").write_text(
                        json.dumps(manifest, indent=2), encoding="utf-8"
                    )
                    connector_runtime.close()
                    connector_runtime = None

            analyze = self._analyze_contracts(ctx, facts, harness)
            active_phase, active_step = "analyze", "adaptive_batches"
            await self._execute_analyze_contracts(
                resolved=resolved,
                ctx=ctx,
                contracts=analyze,
                allowlist=allowlist,
                ledger=ledger,
                log_cb=log_cb,
                on_complete=lambda step, execution: record_execution(
                    "analyze", step, execution
                ),
                force=gather_changed,
            )
            active_phase = None
            active_step = None

            # Produce receives its own bounded contracts and evidence files.
            # Each Produce batch also starts fresh; validated files are the
            # cross-batch hand-off rather than ephemeral conversation history.
            await self._finalize_analyze(ctx, facts, harness, state, log_cb)

            docs = list(harness._required_docs)
            executive = "09_EXECUTIVE_SUMMARY"
            unit_docs = [
                doc for doc in docs if doc in harness._per_job_doc_units and doc != executive
            ]
            group_docs = [
                doc for doc in docs if doc not in harness._per_job_doc_units and doc != executive
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
                if (
                    not gather_changed
                    and ctx.resume_state is not None
                    and self._contracts_complete(
                        ctx, "produce", contracts
                    )
                ):
                    await log_cb(
                        ctx.project_id,
                        f"OpenCode RESUME: {step} outputs already complete; skipping model work",
                        "info",
                    )
                else:
                    active_phase, active_step = "produce", step
                    try:
                        await self._execute_produce_contracts(
                            resolved=resolved,
                            ctx=ctx,
                            step=step,
                            contracts=contracts,
                            allowlist=allowlist,
                            ledger=ledger,
                            log_cb=log_cb,
                            on_complete=lambda completed_step, execution: record_execution(
                                "produce", completed_step, execution
                            ),
                            force=gather_changed,
                        )
                    except OpenCodeCompatibilityError:
                        if doc_names is not None:
                            raise
                        await log_cb(
                            ctx.project_id,
                            "OpenCode topology output remained unavailable after recovery; "
                            "continuing to deterministic topology fallback",
                            "warning",
                        )
                    active_phase = None
                    active_step = None
                if step == "produce_unit_docs":
                    harness._build_unit_docs_digest(ctx, unit_docs)
                if doc_names is not None and not harness._plugin_mode:
                    # File existence is only a checkpoint, not proof that the
                    # document satisfies its semantic contract. Validate fresh
                    # and resumed artifacts at the point where they are made,
                    # then give OpenCode one narrowly scoped repair pass.
                    repair_step = f"{step}_contract_repair"
                    active_phase, active_step = "produce", repair_step
                    repairs = await self._repair_produce_documents(
                        resolved=resolved,
                        ctx=ctx,
                        facts=facts,
                        harness=harness,
                        repair_step=repair_step,
                        doc_names=doc_names,
                        allowlist=allowlist,
                        ledger=ledger,
                        log_cb=log_cb,
                    )
                    for completed_step, repair in repairs:
                        record_execution("produce", completed_step, repair)
                    if not repairs:
                        active_phase = None
                        active_step = None

            required = await self._finalize_produce(ctx, facts, harness, state, log_cb)
            receipts = [
                {"phase": phase, "status": "verified", "session_id": last_session_id}
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
                session_id=last_session_id,
                model_name=self.model_route or "opencode-default",
                write_model_name=self.model_route or "opencode-default",
                required_docs=required,
                has_warnings=bool(state.warnings),
            )
        except asyncio.CancelledError:
            if connector_runtime is not None:
                connector_runtime.close()
            if active_phase is not None:
                phase_receipts.append(
                    {
                        "phase": active_phase,
                        "step": active_step,
                        "status": "cancelled",
                        "usage_available": False,
                        "note": "OpenCode was stopped before it emitted a final usage event.",
                    }
                )
            self._write_usage_receipts(audit_dir, phase_receipts)
            raise
        except (OpenCodeCompatibilityError, OpenCodeAttemptError) as exc:
            if connector_runtime is not None:
                connector_runtime.close()
            error = str(exc)
        except Exception as exc:
            if connector_runtime is not None:
                connector_runtime.close()
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
            session_id=last_session_id,
            duration_ms=int(time.time() * 1000) - started,
        )
