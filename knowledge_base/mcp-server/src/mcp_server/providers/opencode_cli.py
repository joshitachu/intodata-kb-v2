"""OpenCode transport adapter for the canonical Claude CLI workflow.

OpenCode deliberately owns no documentation planning.  The coordinator prepares
the same staging workspace as ``claude-cli`` and this adapter sends Claude's
single four-phase prompt to one OpenCode session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from mcp_server.config import config
from mcp_server.providers.base import AgentResult, DocGenContext, DocGenerationProvider, LogCallback
from mcp_server.providers.claude_cli import ClaudeCliProvider
from mcp_server.utils.stream_frames import iter_frames

logger = logging.getLogger(__name__)

_ROUTE_ENV_CREDENTIALS: dict[str, tuple[str, ...]] = {
    "openrouter/": ("OPENROUTER_API_KEY", "MCP_DOC_GENERATION_OPENROUTER_API_KEY"),
}
_PROCESS_CLEANUP_TIMEOUT_SECONDS = 5
_CREDENTIAL_NAME_MARKERS = ("key", "token", "secret", "password", "credential", "authorization")
# One `--format json` event carries a whole tool result -- a subagent report or
# a file read -- so its size is set by the model, not by the protocol.  These
# events are telemetry (usage, session id, turn counts); the documentation
# itself reaches staging through file writes.  Read them without a separator
# limit and drop only an event too large to be a real one, so a verbose tool
# result degrades accounting instead of failing the run.
_MAX_EVENT_BYTES = 16 * 1024 * 1024
_MAX_STDERR_LINE_BYTES = 1024 * 1024
# Raise the transport buffer above asyncio's 64 KiB default so large events are
# drained in fewer passes.  It is no longer a ceiling on frame size.
_STREAM_BUFFER_BYTES = 4 * 1024 * 1024


class OpenCodeCompatibilityError(RuntimeError):
    """Raised when the operator-pinned OpenCode runtime cannot be used."""


def _route_credential(model_route: str) -> tuple[str, str] | None:
    """Resolve the one environment credential OpenCode needs for a route."""
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


def _config_credential_values(value: object, *, parent_key: str = "") -> list[str]:
    """Extract secret-shaped values from OpenCode JSON configuration."""
    if isinstance(value, dict):
        found: list[str] = []
        for key, nested in value.items():
            name = str(key).casefold()
            if isinstance(nested, str) and any(marker in name for marker in _CREDENTIAL_NAME_MARKERS):
                found.append(nested.strip())
            else:
                found.extend(_config_credential_values(nested, parent_key=name))
        return found
    if isinstance(value, list):
        return [item for nested in value for item in _config_credential_values(nested, parent_key=parent_key)]
    if isinstance(value, str) and any(marker in parent_key for marker in _CREDENTIAL_NAME_MARKERS):
        return [value.strip()]
    return []


def _safe_stderr(text: str, runtime_env: dict[str, str] | None = None) -> str:
    """Keep useful diagnostics without allowing transport credentials into logs."""
    values = [config.opencode_go_api_key.strip()]
    for names in _ROUTE_ENV_CREDENTIALS.values():
        values.extend((os.environ.get(name) or "").strip() for name in names)
    # OpenCode inherits the service environment for provider compatibility.  If
    # it echoes one of the conventional credential variables, redact that too.
    for name, value in (runtime_env or {}).items():
        upper_name = name.upper()
        if any(marker.upper() in upper_name for marker in _CREDENTIAL_NAME_MARKERS):
            values.append(value.strip())
    for config_text in ((runtime_env or {}).get("OPENCODE_CONFIG_CONTENT", ""),):
        try:
            values.extend(_config_credential_values(json.loads(config_text)))
        except (json.JSONDecodeError, TypeError):
            pass
    config_path = (runtime_env or {}).get("OPENCODE_CONFIG", "").strip()
    if config_path:
        try:
            values.extend(_config_credential_values(json.loads(Path(config_path).read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    for value in values:
        if len(value) >= 6:
            text = text.replace(value, "[redacted]")
    return text[-8000:]


class OpenCodeCliProvider(DocGenerationProvider):
    """Run the Claude-compatible documentation session through OpenCode."""

    def __init__(self, command: str | None = None, model_route: str | None = None, **_: Any) -> None:
        self.command = command or config.opencode_command
        self.model_route = (model_route or config.opencode_model).strip()

    @staticmethod
    def _build_prompt(ctx: DocGenContext) -> str:
        """Keep the generation contract structurally identical to Claude's."""
        return ClaudeCliProvider._build_prompt(ctx)

    @staticmethod
    def _timeout_seconds(ctx: DocGenContext) -> int:
        """Wall-clock budget for the whole run, scaled to the estate.

        The work scales with the number of groups -- one Phase 2 sub-agent each,
        dispatched a few at a time -- so a flat budget is wrong at both ends: it
        is absurdly generous for a 4-job project and impossible for a large one.

        Verified (ETTM run 259, 20 groups): 15 group analyses took ~20 minutes
        and the flat 1800 s cap would have killed the run with 5 groups and all
        of Phase 3 outstanding. The failure would have surfaced as
        "timed out after 1800s", which reads as a hang rather than as a budget
        that never fitted the work.
        """
        groups = max(0, int(getattr(ctx, "total_groups", 0) or 0))
        scaled = (
            config.opencode_timeout_seconds
            + config.opencode_timeout_per_group_seconds * groups
        )
        return max(1, min(scaled, config.opencode_timeout_max_seconds))

    @staticmethod
    def _runtime_env() -> dict[str, str]:
        env = os.environ.copy()
        # Service accounts in containers sometimes have an unusable HOME.
        home = env.get("HOME", "").strip()
        xdg_config_home = env.get("XDG_CONFIG_HOME", "").strip()
        if (not home or home == "/nonexistent") and xdg_config_home:
            env["HOME"] = str(Path(xdg_config_home).parent)
        env["PYTHONUTF8"] = "1"
        return env

    def _execution_env(self, state_root: Path) -> dict[str, str]:
        env = self._runtime_env()
        for name in ("data", "state", "config"):
            (state_root / name).mkdir(parents=True, exist_ok=True)
        cache_home = self._shared_cache_home()
        env.update(
            {
                "XDG_DATA_HOME": str(state_root / "data"),
                "XDG_STATE_HOME": str(state_root / "state"),
                "XDG_CACHE_HOME": str(cache_home),
                "XDG_CONFIG_HOME": str(state_root / "config"),
                "HOME": str(state_root),
            }
        )
        if config.opencode_config_path.strip():
            env["OPENCODE_CONFIG"] = config.opencode_config_path.strip()
        if self.model_route.startswith("opencode-go/"):
            key = config.opencode_go_api_key.strip()
            if not key:
                raise OpenCodeCompatibilityError("OpenCode Go requires MCP_OPENCODE_GO_API_KEY")
            auth_path = state_root / "data" / "opencode" / "auth.json"
            auth_path.parent.mkdir(parents=True, exist_ok=True)
            auth_path.write_text(
                json.dumps({"opencode-go": {"type": "api", "key": key}}, separators=(",", ":")),
                encoding="utf-8",
            )
            auth_path.chmod(0o600)
            env.pop("MCP_OPENCODE_GO_API_KEY", None)
        else:
            credential = _route_credential(self.model_route)
            if credential is not None:
                env[credential[0]] = credential[1]
        if self.model_route.startswith("ollama/"):
            model_id = self.model_route.split("/", 1)[1]
            try:
                inline_config = json.loads(env.get("OPENCODE_CONFIG_CONTENT") or "{}")
            except json.JSONDecodeError as exc:
                raise OpenCodeCompatibilityError("OPENCODE_CONFIG_CONTENT must be valid JSON") from exc
            if not isinstance(inline_config, dict):
                raise OpenCodeCompatibilityError("OPENCODE_CONFIG_CONTENT must contain a JSON object")
            providers = inline_config.setdefault("provider", {})
            if not isinstance(providers, dict):
                raise OpenCodeCompatibilityError("OpenCode inline provider configuration must be an object")
            ollama = providers.setdefault("ollama", {})
            if not isinstance(ollama, dict):
                raise OpenCodeCompatibilityError("OpenCode Ollama provider configuration must be an object")
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
    def _shared_cache_home() -> Path:
        """Create a service-owned cache shared across isolated sessions."""
        cache_home = Path(config.data_dir) / "opencode_cache"
        cache_home.mkdir(parents=True, exist_ok=True)
        cache_home.chmod(0o700)
        probe = cache_home / ".nexus_write_probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return cache_home

    def _command(self, executable: str, ctx: DocGenContext) -> list[str]:
        command = [executable, "run", "--format", "json"]
        if self.model_route:
            command.extend(["--model", self.model_route])
        command.extend(["--dir", str(ctx.staging_dir)])
        if config.opencode_primary_agent:
            command.extend(["--agent", config.opencode_primary_agent])
        if config.opencode_auto_approve:
            command.append("--auto")
        return command

    def _preflight(self) -> str:
        if not config.opencode_version:
            raise OpenCodeCompatibilityError("MCP_OPENCODE_VERSION must pin the OpenCode runtime")
        try:
            self._shared_cache_home()
        except OSError as exc:
            raise OpenCodeCompatibilityError(
                f"OpenCode cache directory is not writable by the service account: {exc}"
            ) from exc
        executable = shutil.which(self.command)
        if not executable:
            raise OpenCodeCompatibilityError(f"OpenCode CLI not found: {self.command}")
        try:
            result = subprocess.run(
                [executable, "--version"], capture_output=True, text=True, timeout=10,
                check=False, env=self._runtime_env(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OpenCodeCompatibilityError("OpenCode version preflight failed") from exc
        actual = result.stdout.strip()
        if result.returncode != 0 or actual != config.opencode_version:
            detail = actual or result.stderr.strip() or f"exit code {result.returncode}"
            raise OpenCodeCompatibilityError(
                f"OpenCode runtime version mismatch (expected {config.opencode_version!r}, got {detail[:200]!r})"
            )
        return executable

    @staticmethod
    def _usage(event: dict[str, Any]) -> tuple[int, int, float]:
        """Read the documented JSON-event usage variants without assuming a provider."""
        usage = event.get("usage") or event.get("tokens") or {}
        if not isinstance(usage, dict):
            usage = {}
        nested = event.get("part")
        if isinstance(nested, dict):
            nested_usage = nested.get("usage") or nested.get("tokens")
            if isinstance(nested_usage, dict):
                usage = {**usage, **nested_usage}
            for key in ("cost", "costUSD", "cost_usd"):
                if key in nested and key not in usage:
                    usage[key] = nested[key]
        def integer(*names: str) -> int:
            for name in names:
                value = usage.get(name)
                if isinstance(value, (int, float)):
                    return int(value)
            return 0
        def number(*names: str) -> float:
            for name in names:
                value = usage.get(name)
                if isinstance(value, (int, float)):
                    return float(value)
            return 0.0
        return integer("input", "inputTokens", "prompt_tokens"), integer("output", "outputTokens", "completion_tokens"), number("cost", "costUSD", "cost_usd")

    @staticmethod
    def _event_summary(event: dict[str, Any]) -> tuple[str | None, str]:
        event_type = str(event.get("type") or event.get("event") or "")
        part = event.get("part")
        if isinstance(part, dict):
            event_type = str(part.get("type") or event_type)
            if event_type == "error":
                reason = part.get("error") or part.get("message") or event.get("message") or "OpenCode reported an error"
                return f"OpenCode error: {str(reason)[:500]}", "error"
            if event_type in {"text", "text-delta"}:
                text = str(part.get("text") or part.get("delta") or "").strip()
                return (f"OpenCode: {text[:500]}" if text else None, "info")
            if event_type in {"tool", "tool-call", "tool_use"}:
                return f"OpenCode tool: {part.get('tool') or part.get('name') or 'unknown'}", "info"
        if event_type in {"error", "session.error"}:
            reason = event.get("error") or event.get("message") or "OpenCode reported an error"
            return f"OpenCode error: {str(reason)[:500]}", "error"
        if event_type in {"text", "message"}:
            text = str(event.get("text") or event.get("message") or "").strip()
            return (f"OpenCode: {text[:500]}" if text else None, "info")
        return None, "info"

    @staticmethod
    def _is_finished_turn(event: dict[str, Any]) -> bool:
        if str(event.get("type") or "") == "step_finish":
            return True
        part = event.get("part")
        return isinstance(part, dict) and str(part.get("type") or "") == "step-finish"

    @staticmethod
    async def _stop_process(process: asyncio.subprocess.Process) -> None:
        """Terminate and reap the session group, including OpenCode descendants."""
        if process.returncode is not None:
            await process.wait()
            return
        if os.name == "posix" and process.pid is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=_PROCESS_CLEANUP_TIMEOUT_SECONDS)
        except TimeoutError:
            if os.name == "posix" and process.pid is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            await process.wait()

    async def run(self, ctx: DocGenContext, log_cb: LogCallback) -> AgentResult:
        started = time.monotonic()
        try:
            # subprocess.run blocks; off the event loop so the API keeps serving.
            executable = await asyncio.to_thread(self._preflight)
        except OpenCodeCompatibilityError as exc:
            return AgentResult(success=False, error=str(exc), model_name=self.model_route or "opencode")

        prompt = self._build_prompt(ctx)
        # Do not put runtime state below staging: Phase 4 deliberately keeps
        # only documentation artifacts there before the atomic publication.
        state_root = Path(tempfile.mkdtemp(prefix="nexus-opencode-"))
        input_tokens = output_tokens = 0
        cost_usd = 0.0
        num_turns = 0
        session_id: str | None = None
        errors: list[str] = []
        process: asyncio.subprocess.Process | None = None
        reader_tasks: list[asyncio.Task[Any]] = []
        completion_future: asyncio.Future[list[Any]] | None = None
        try:
            execution_env = self._execution_env(state_root)
            process = await asyncio.create_subprocess_exec(
                *self._command(executable, ctx), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=str(ctx.staging_dir), env=execution_env,
                start_new_session=os.name == "posix",
                limit=_STREAM_BUFFER_BYTES,
            )
            assert process.stdin is not None
            process.stdin.write(prompt.encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()

            assert process.stdout is not None and process.stderr is not None
            async def report_oversized_event(size: int) -> None:
                await log_cb(
                    ctx.project_id,
                    f"Dropped an OpenCode event of {size} bytes (above the "
                    f"{_MAX_EVENT_BYTES}-byte event limit); token accounting for "
                    "that event is lost.",
                    "warning",
                )

            async def consume_stdout() -> None:
                nonlocal input_tokens, output_tokens, cost_usd, session_id, num_turns
                async for raw in iter_frames(
                    process.stdout,
                    max_frame_bytes=_MAX_EVENT_BYTES,
                    on_oversized=report_oversized_event,
                ):
                    try:
                        event = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    try:
                        candidate_session_id = event.get("sessionID") or event.get("session_id")
                        if session_id is None and isinstance(candidate_session_id, str) and candidate_session_id:
                            session_id = candidate_session_id
                        inp, out, cost = self._usage(event)
                        input_tokens += inp
                        output_tokens += out
                        cost_usd += cost
                        message, level = self._event_summary(event)
                        if message:
                            message = _safe_stderr(message, execution_env)
                            await log_cb(ctx.project_id, message, level)
                        event_type = str(event.get("type") or event.get("event") or "")
                        part = event.get("part")
                        part_type = str(part.get("type") or "") if isinstance(part, dict) else ""
                        if event_type in {"error", "session.error"} or part_type == "error":
                            errors.append(message or "OpenCode error")
                        if self._is_finished_turn(event):
                            num_turns += 1
                    except Exception:
                        # This lane is telemetry.  The documentation itself is
                        # written to staging by the session's file tools, so an
                        # unexpected event shape costs accounting, not output.
                        logger.warning(
                            "Skipped an unreadable OpenCode event for %s", ctx.project_id,
                            exc_info=True,
                        )

            async def consume_stderr() -> str:
                retained = ""
                async for raw in iter_frames(
                    process.stderr, max_frame_bytes=_MAX_STDERR_LINE_BYTES,
                ):
                    line = _safe_stderr(
                        raw.decode("utf-8", errors="replace"), execution_env,
                    ).rstrip()
                    if line:
                        retained = f"{retained}\n{line}"[-8000:]
                        await log_cb(ctx.project_id, f"[stderr] {line[:500]}", "warning")
                return retained

            reader_tasks = [asyncio.create_task(consume_stdout()), asyncio.create_task(consume_stderr())]
            completion_future = asyncio.gather(*reader_tasks, process.wait())
            run_timeout = self._timeout_seconds(ctx)
            await log_cb(
                ctx.project_id,
                f"OpenCode run budget: {run_timeout}s "
                f"({run_timeout // 60} min) for {ctx.total_groups} groups",
                "info",
            )
            completed = await asyncio.wait_for(
                completion_future, timeout=run_timeout,
            )
            stderr = str(completed[1])
            returncode = int(completed[2])
            (ctx.staging_dir / "_audit").mkdir(parents=True, exist_ok=True)
            (ctx.staging_dir / "_audit" / "opencode_usage.json").write_text(
                json.dumps({"session_id": session_id, "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost_usd}, indent=2), encoding="utf-8"
            )
            if returncode != 0 or errors:
                reason = errors[-1] if errors else (stderr.strip() or f"OpenCode exited {returncode}")
                return AgentResult(success=False, error=reason[:1000], input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost_usd, duration_ms=int((time.monotonic() - started) * 1000), session_id=session_id, model_name=self.model_route or "opencode")
            return AgentResult(success=True, input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost_usd, duration_ms=int((time.monotonic() - started) * 1000), num_turns=num_turns, session_id=session_id, model_name=self.model_route or "opencode")
        except TimeoutError:
            if process is not None:
                await self._stop_process(process)
            if reader_tasks:
                await asyncio.gather(*reader_tasks, return_exceptions=True)
            stderr_reason = ""
            for task in reader_tasks[1:2]:
                if task.done() and not task.cancelled():
                    try:
                        stderr_reason = str(task.result()).strip()
                    except Exception:
                        pass
            suffix = f": {stderr_reason[-1000:]}" if stderr_reason else ""
            return AgentResult(success=False, error=f"OpenCode timed out after {config.opencode_timeout_seconds}s{suffix}", input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost_usd, duration_ms=int((time.monotonic() - started) * 1000), session_id=session_id, model_name=self.model_route or "opencode")
        except asyncio.CancelledError:
            if process is not None:
                await self._stop_process(process)
            raise
        except OpenCodeCompatibilityError as exc:
            return AgentResult(
                success=False, error=str(exc), input_tokens=input_tokens,
                output_tokens=output_tokens, cost_usd=cost_usd,
                duration_ms=int((time.monotonic() - started) * 1000),
                session_id=session_id, model_name=self.model_route or "opencode",
            )
        except OSError as exc:
            return AgentResult(success=False, error=f"OpenCode launch failed: {exc}", model_name=self.model_route or "opencode")
        finally:
            if completion_future is not None:
                if not completion_future.done():
                    completion_future.cancel()
                await asyncio.gather(completion_future, return_exceptions=True)
            for task in reader_tasks:
                if not task.done():
                    task.cancel()
            if reader_tasks:
                await asyncio.gather(*reader_tasks, return_exceptions=True)
            if process is not None and process.returncode is None:
                await self._stop_process(process)
            shutil.rmtree(state_root, ignore_errors=True)


# A single-turn completion needs NO tool: its context is inline. OpenCode's
# `"*"` key is the catch-all and also covers MCP tool ids, so it switches off
# anything an operator's OPENCODE_CONFIG adds. Every known key is ALSO denied by
# name, because the inline block is merged over the operator's file and a
# specific `read: allow` there must not survive beside the wildcard. The empty
# working directory is a second layer, not the policy.
_COMPLETION_DENIED_PERMISSIONS = (
    "read", "edit", "write", "patch", "glob", "grep", "list", "lsp", "bash",
    "task", "todowrite", "todoread", "webfetch", "websearch", "codesearch",
    "skill", "question", "external_directory",
)


class OpenCodeCompletionProvider:
    """One bounded, non-agentic completion over the OpenCode transport.

    Adapter from OpenCode's session CLI to the ``ModelProvider.complete``
    contract the topology builder's ``BoundedModelTopologyLLM`` drives -- the
    same role ``ClaudeCliModelProvider`` plays for Claude. Everything that makes
    an OpenCode session safe is reused from :class:`OpenCodeCliProvider` rather
    than copied: the pinned-version preflight, isolated HOME/XDG state, route
    credentials, credential redaction and process-group cleanup.

    Differences from a documentation session, all deliberate:

    * the prompt carries its context inline, so the session needs no file tools;
    * it runs in an EMPTY temporary directory, never in staging;
    * EVERY tool is denied through the inline OpenCode configuration (the
      ``"*"`` catch-all, which also covers MCP tool ids, plus each known key);
    * the answer is the session's assistant text, not files it wrote.
    """

    supports_tools: bool = False
    provider_name = "opencode-cli"

    def __init__(self, command: str | None = None, model_route: str | None = None) -> None:
        self._session = OpenCodeCliProvider(command=command, model_route=model_route)
        self.model_name = self._session.model_route or "opencode"

    @staticmethod
    def _inline_context(context_files: list[Path] | None) -> str:
        parts: list[str] = []
        for path in context_files or []:
            try:
                parts.append(f"\n\n--- CONTEXT FILE: {path.name} ---\n")
                parts.append(path.read_text(encoding="utf-8"))
            except OSError as exc:
                parts.append(f"\n\n--- CONTEXT FILE ERROR: {path.name} ({exc}) ---\n")
        return "".join(parts)

    @staticmethod
    def _deny_tools(env: dict[str, str]) -> None:
        try:
            inline = json.loads(env.get("OPENCODE_CONFIG_CONTENT") or "{}")
        except json.JSONDecodeError as exc:
            raise OpenCodeCompatibilityError("OPENCODE_CONFIG_CONTENT must be valid JSON") from exc
        if not isinstance(inline, dict):
            raise OpenCodeCompatibilityError("OPENCODE_CONFIG_CONTENT must contain a JSON object")
        # Replaced, not merged: OpenCode evaluates the LAST matching rule, so an
        # inherited allow placed after the wildcard would re-open that tool.
        inline["permission"] = {
            "*": "deny",
            **{name: "deny" for name in _COMPLETION_DENIED_PERMISSIONS},
        }
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(inline, separators=(",", ":"))

    @staticmethod
    def _collect_text(event: dict[str, Any], texts: dict[str, str], order: list[str]) -> None:
        """Accumulate assistant text; full ``text`` parts replace, deltas append."""
        part = event.get("part")
        if not isinstance(part, dict):
            return
        kind = str(part.get("type") or "")
        if kind not in {"text", "text-delta"}:
            return
        key = str(part.get("id") or f"_{len(order)}")
        if key not in texts:
            texts[key] = ""
            order.append(key)
        if kind == "text":
            texts[key] = str(part.get("text") or "")
        else:
            texts[key] += str(part.get("delta") or part.get("text") or "")

    async def complete(
        self,
        prompt: str,
        context_files: list[Path] | None = None,
        options: Any = None,
    ) -> Any:
        from mcp_server.providers.custom.models import ModelOptions, ModelResult

        opts = options or ModelOptions()
        full_prompt = prompt + self._inline_context(context_files)
        if opts.system_prompt:
            full_prompt = f"{opts.system_prompt}\n\n---\n\n{full_prompt}"
        started = time.monotonic()

        def failure(reason: str, **usage: int) -> ModelResult:
            return ModelResult(
                success=False,
                model_name=self.model_name,
                latency_ms=int((time.monotonic() - started) * 1000),
                raw_error=reason[:1000],
                metadata={"provider": self.provider_name},
                **usage,
            )

        try:
            # subprocess.run blocks; off the event loop so the API keeps serving.
            executable = await asyncio.to_thread(self._session._preflight)
        except OpenCodeCompatibilityError as exc:
            return failure(str(exc))

        state_root = Path(tempfile.mkdtemp(prefix="nexus-opencode-completion-"))
        workdir = state_root / "work"
        workdir.mkdir()
        process: asyncio.subprocess.Process | None = None
        texts: dict[str, str] = {}
        order: list[str] = []
        errors: list[str] = []
        input_tokens = output_tokens = 0
        env: dict[str, str] = {}
        try:
            env = self._session._execution_env(state_root)
            self._deny_tools(env)
            command = [executable, "run", "--format", "json"]
            if self._session.model_route:
                command.extend(["--model", self._session.model_route])
            command.extend(["--dir", str(workdir)])
            process = await asyncio.create_subprocess_exec(
                *command, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=str(workdir), env=env,
                start_new_session=os.name == "posix",
                limit=_STREAM_BUFFER_BYTES,
            )
            assert process.stdin is not None and process.stdout is not None
            assert process.stderr is not None
            stdin = process.stdin

            async def send_prompt() -> None:
                # Inside the timeout: an inlined prompt can be several hundred
                # KB, and a child that stops reading must not hang the build.
                stdin.write(full_prompt.encode("utf-8"))
                await stdin.drain()
                stdin.close()

            async def consume_stdout() -> None:
                nonlocal input_tokens, output_tokens
                async for raw in iter_frames(process.stdout, max_frame_bytes=_MAX_EVENT_BYTES):
                    try:
                        event = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    inp, out, _cost = OpenCodeCliProvider._usage(event)
                    input_tokens += inp
                    output_tokens += out
                    message, level = OpenCodeCliProvider._event_summary(event)
                    if level == "error":
                        errors.append(_safe_stderr(message or "OpenCode error", env))
                    self._collect_text(event, texts, order)

            async def consume_stderr() -> str:
                retained = ""
                async for raw in iter_frames(process.stderr, max_frame_bytes=_MAX_STDERR_LINE_BYTES):
                    retained = f"{retained}\n{raw.decode('utf-8', errors='replace')}"[-4000:]
                return _safe_stderr(retained, env)

            _, _, stderr, returncode = await asyncio.wait_for(
                asyncio.gather(
                    send_prompt(), consume_stdout(), consume_stderr(), process.wait()
                ),
                timeout=max(1.0, float(opts.timeout_s)),
            )
            text = "".join(texts[key] for key in order).strip()
            if returncode != 0 or errors or not text:
                reason = (
                    errors[-1] if errors
                    else str(stderr).strip() or f"OpenCode exited {returncode} with no text"
                )
                return failure(reason, input_tokens=input_tokens, output_tokens=output_tokens)
            return ModelResult(
                success=True,
                text=text,
                model_name=self.model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=int((time.monotonic() - started) * 1000),
                metadata={"provider": self.provider_name},
            )
        except TimeoutError:
            return failure(
                f"OpenCode completion timed out after {opts.timeout_s}s",
                input_tokens=input_tokens, output_tokens=output_tokens,
            )
        except OpenCodeCompatibilityError as exc:
            return failure(str(exc))
        except OSError as exc:
            return failure(f"OpenCode launch failed: {exc}")
        finally:
            if process is not None and process.returncode is None:
                await OpenCodeCliProvider._stop_process(process)
            shutil.rmtree(state_root, ignore_errors=True)
