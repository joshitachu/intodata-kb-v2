"""Deep Dive service with swappable engines.

The default engine is a read-only repo agent. The legacy Claude Code CLI path is
kept as an explicit fallback/debug engine behind MCP_DEEP_DIVE_ENGINE=claude_cli.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
import traceback
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
from typing import Any, Protocol

from mcp_server.config import config
from mcp_server.model_strategies import normalize_model_id
from mcp_server.providers.custom.models import ModelOptions, ModelResult
from mcp_server.providers.custom.providers import ModelProvider, build_model_provider
from mcp_server.services import instance_settings_service
from mcp_server.services.token_refresh import ensure_token_fresh

logger = logging.getLogger(__name__)


DeepDiveEvent = dict[str, Any]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolResult:
    ok: bool
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    truncated: bool = False

    def as_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "content": self.content,
                "metadata": self.metadata,
                "truncated": self.truncated,
            },
            ensure_ascii=False,
        )


@dataclass
class AgentState:
    session_id: str
    project_id: str
    messages: list[dict[str, str]] = field(default_factory=list)
    inspected_files: set[str] = field(default_factory=set)
    tool_calls: int = 0


class DeepDiveEngine(Protocol):
    async def query(
        self,
        project_id: str,
        question: str,
        session_id: str | None = None,
    ) -> AsyncGenerator[DeepDiveEvent, None]:
        ...


class RepoToolbox:
    """Read-only tools scoped to one project repo and approved output dirs."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self.repo_root = (config.repos_dir / project_id).resolve()
        self.scan_root = (config.intodoc_output_dir / project_id).resolve()
        self.docs_root = (config.project_root / "output" / project_id / "docs").resolve()
        self.staging_root = (config.project_root / "output" / project_id / "docs_staging").resolve()

    @staticmethod
    def specs() -> list[ToolSpec]:
        return [
            ToolSpec(
                name="list_files",
                description="List files under the project repository.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "default": ""},
                        "max_depth": {"type": "integer", "default": 2},
                    },
                },
            ),
            ToolSpec(
                name="read_file",
                description="Read a bounded line range from a project repository file.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer", "default": 1},
                        "max_lines": {"type": "integer", "default": 200},
                    },
                    "required": ["path"],
                },
            ),
            ToolSpec(
                name="search_repo",
                description=(
                    "Search raw repository files with ripgrep-style literal search. "
                    "Use glob to filter by extension (e.g. '*.item', '*.xml', '*.py'). "
                    "Set files_only=true to get just the matching file paths without content — "
                    "useful for 'which files contain X' before deciding what to read."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "path": {"type": "string", "default": ""},
                        "glob": {"type": "string"},
                        "max_results": {"type": "integer", "default": 50},
                        "files_only": {"type": "boolean", "default": False},
                    },
                    "required": ["query"],
                },
            ),
            ToolSpec(
                name="file_info",
                description="Return metadata for a repository file or directory.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
            ToolSpec(
                name="read_scan_summary",
                description="Read a compact summary of intodoc scan output for this project.",
                parameters={"type": "object", "properties": {}},
            ),
            ToolSpec(
                name="read_generated_doc",
                description="Read a generated markdown doc for this project by filename.",
                parameters={
                    "type": "object",
                    "properties": {"filename": {"type": "string"}},
                    "required": ["filename"],
                },
            ),
            ToolSpec(
                name="get_scan_connections",
                description=(
                    "Return all database and API connections extracted by the scanner — "
                    "name, type (e.g. JDBC, SQL Server, Oracle), host reference, port, "
                    "database, and driver class. Use this first for any question about "
                    "what external systems this codebase connects to."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            ToolSpec(
                name="get_context_group",
                description=(
                    "Return the context variables for a named context group, with their "
                    "values per environment (Default, PROD, TEST, etc.). Use this to resolve "
                    "context.* references found in connections or job parameters."
                ),
                parameters={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            ),
            ToolSpec(
                name="search_scan_jobs",
                description=(
                    "Search jobs by name, group, component type, or context group reference. "
                    "Returns matching job names, file paths, component types, and context groups. "
                    "Use this to find which jobs use a specific component (e.g. 'tJDBCInput', "
                    "'tBigQueryInput') or a specific connection, without reading individual files."
                ),
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            ),
        ]

    def run(self, name: str, args: dict[str, Any], state: AgentState) -> ToolResult:
        try:
            if name == "list_files":
                return self.list_files(str(args.get("path") or ""), int(args.get("max_depth") or 2))
            if name == "read_file":
                result = self.read_file(
                    str(args.get("path") or ""),
                    int(args.get("start_line") or 1),
                    int(args.get("max_lines") or 200),
                )
                if result.ok:
                    state.inspected_files.add(str(args.get("path") or ""))
                return result
            if name == "search_repo":
                return self.search_repo(
                    str(args.get("query") or ""),
                    str(args.get("path") or ""),
                    str(args["glob"]) if args.get("glob") else None,
                    int(args.get("max_results") or config.deep_dive_max_search_results),
                    bool(args.get("files_only") or False),
                )
            if name == "file_info":
                return self.file_info(str(args.get("path") or ""))
            if name == "read_scan_summary":
                return self.read_scan_summary()
            if name == "read_generated_doc":
                return self.read_generated_doc(str(args.get("filename") or ""))
            if name == "get_scan_connections":
                return self.get_scan_connections()
            if name == "get_context_group":
                return self.get_context_group(str(args.get("name") or ""))
            if name == "search_scan_jobs":
                return self.search_scan_jobs(str(args.get("query") or ""))
            return ToolResult(False, f"Unknown tool: {name}")
        except Exception as exc:
            return ToolResult(False, f"{type(exc).__name__}: {exc}")

    def _resolve_repo_path(self, path: str) -> Path:
        if not self.repo_root.exists():
            raise FileNotFoundError(f"project repository not found: {self.repo_root}")
        raw = Path(path or ".")
        if raw.is_absolute():
            raise ValueError("absolute paths are not allowed")
        target = (self.repo_root / raw).resolve()
        if target != self.repo_root and self.repo_root not in target.parents:
            raise ValueError("path escapes project repository")
        return target

    @staticmethod
    def _bounded_text(text: str) -> tuple[str, bool]:
        limit = max(1_000, config.deep_dive_max_file_bytes)
        if len(text.encode("utf-8", errors="ignore")) <= limit:
            return text, False
        encoded = text.encode("utf-8", errors="ignore")[:limit]
        return encoded.decode("utf-8", errors="ignore") + "\n\n[TRUNCATED]", True

    def list_files(self, path: str = "", max_depth: int = 2) -> ToolResult:
        root = self._resolve_repo_path(path)
        if not root.exists():
            return ToolResult(False, f"not found: {path}")
        if root.is_file():
            rel = root.relative_to(self.repo_root).as_posix()
            return ToolResult(True, rel, {"count": 1})
        max_depth = max(0, min(max_depth, 8))
        rows: list[str] = []
        base_depth = len(root.relative_to(self.repo_root).parts) if root != self.repo_root else 0
        for current, dirs, files in os.walk(root):
            current_path = Path(current)
            depth = len(current_path.relative_to(self.repo_root).parts) - base_depth
            dirs[:] = [d for d in sorted(dirs) if not d.startswith(".") and d not in {"node_modules", "__pycache__"}]
            if depth >= max_depth:
                dirs[:] = []
            for name in sorted(files):
                if name.startswith("."):
                    continue
                rows.append((current_path / name).relative_to(self.repo_root).as_posix())
                if len(rows) >= config.deep_dive_max_search_results:
                    return ToolResult(True, "\n".join(rows), {"count": len(rows)}, truncated=True)
        return ToolResult(True, "\n".join(rows) or "(empty)", {"count": len(rows)})

    def read_file(self, path: str, start_line: int = 1, max_lines: int = 200) -> ToolResult:
        target = self._resolve_repo_path(path)
        if not target.exists() or not target.is_file():
            return ToolResult(False, f"not a file: {path}")
        start_line = max(1, start_line)
        max_lines = max(1, min(max_lines, 500))
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        selected = lines[start_line - 1 : start_line - 1 + max_lines]
        numbered = "\n".join(f"{idx}: {line}" for idx, line in enumerate(selected, start=start_line))
        text, truncated = self._bounded_text(numbered)
        return ToolResult(
            True,
            text or "(empty)",
            {
                "path": target.relative_to(self.repo_root).as_posix(),
                "start_line": start_line,
                "returned_lines": len(selected),
                "total_lines": len(lines),
            },
            truncated=truncated or start_line - 1 + max_lines < len(lines),
        )

    def search_repo(
        self,
        query: str,
        path: str = "",
        glob: str | None = None,
        max_results: int = 50,
        files_only: bool = False,
    ) -> ToolResult:
        if not query.strip():
            return ToolResult(False, "query is required")
        target = self._resolve_repo_path(path)
        max_results = max(1, min(max_results, config.deep_dive_max_search_results))
        rg = shutil.which("rg")
        rows: list[str] = []
        if rg:
            if files_only:
                cmd = [rg, "--files-with-matches", "--color", "never", "-F", query, target.as_posix()]
            else:
                cmd = [rg, "--line-number", "--no-heading", "--color", "never", "-F", query, target.as_posix()]
            if glob:
                cmd[1:1] = ["-g", glob]
            completed = subprocess.run(
                cmd,
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=min(config.deep_dive_timeout_s, 30.0),
                check=False,
            )
            for line in completed.stdout.splitlines():
                if line:
                    # Make paths relative when files_only
                    if files_only:
                        try:
                            line = str(Path(line).relative_to(self.repo_root))
                        except ValueError:
                            pass
                    rows.append(line)
                if len(rows) >= max_results:
                    break
        else:
            seen_files: set[str] = set()
            for file in target.rglob("*") if target.is_dir() else [target]:
                if not file.is_file() or file.name.startswith("."):
                    continue
                try:
                    content = file.read_text(encoding="utf-8", errors="replace")
                    rel = file.relative_to(self.repo_root).as_posix()
                    if files_only:
                        if query in content and rel not in seen_files:
                            seen_files.add(rel)
                            rows.append(rel)
                            if len(rows) >= max_results:
                                break
                    else:
                        for idx, line in enumerate(content.splitlines(), 1):
                            if query in line:
                                rows.append(f"{rel}:{idx}:{line}")
                                if len(rows) >= max_results:
                                    break
                except OSError:
                    continue
                if len(rows) >= max_results:
                    break
        return ToolResult(True, "\n".join(rows) or "(no matches)", {"count": len(rows)}, truncated=len(rows) >= max_results)

    def file_info(self, path: str) -> ToolResult:
        target = self._resolve_repo_path(path)
        if not target.exists():
            return ToolResult(False, f"not found: {path}")
        stat = target.stat()
        return ToolResult(
            True,
            json.dumps(
                {
                    "path": target.relative_to(self.repo_root).as_posix() if target != self.repo_root else ".",
                    "type": "directory" if target.is_dir() else "file",
                    "bytes": stat.st_size,
                    "modified": int(stat.st_mtime),
                },
                indent=2,
            ),
        )

    def read_scan_summary(self) -> ToolResult:
        scan = self.scan_root / "scan_result.json"
        if not scan.exists():
            return ToolResult(False, f"scan_result.json not found under {self.scan_root}")
        data = json.loads(scan.read_text(encoding="utf-8", errors="replace"))
        jobs = data.get("jobs") or []
        groups: dict[str, int] = {}
        for job in jobs:
            group = job.get("group") or "Global"
            groups[group] = groups.get(group, 0) + 1
        summary = {
            "platform": data.get("platform"),
            "repo_root": data.get("repo_root"),
            "job_count": len(jobs),
            "groups": groups,
            "warnings": data.get("warnings") or [],
        }
        return ToolResult(True, json.dumps(summary, indent=2), {"source": scan.as_posix()})

    def read_generated_doc(self, filename: str) -> ToolResult:
        raw = Path(filename)
        if raw.is_absolute() or ".." in raw.parts:
            return ToolResult(False, "invalid filename")
        candidates = [self.docs_root / raw, self.staging_root / raw]
        for target in candidates:
            resolved = target.resolve()
            root = self.docs_root if self.docs_root in resolved.parents or resolved == self.docs_root else self.staging_root
            if resolved.exists() and resolved.is_file() and resolved.suffix.lower() == ".md":
                text, truncated = self._bounded_text(resolved.read_text(encoding="utf-8", errors="replace"))
                return ToolResult(True, text, {"source": resolved.as_posix()}, truncated=truncated)
        return ToolResult(False, f"generated markdown doc not found: {filename}")

    def get_scan_connections(self) -> ToolResult:
        """Return all metadata connections extracted by the scanner."""
        scan = self.scan_root / "scan_result.json"
        if not scan.exists():
            return ToolResult(False, f"scan_result.json not found under {self.scan_root}")
        data = json.loads(scan.read_text(encoding="utf-8", errors="replace"))
        connections = data.get("metadata_connections") or []
        if not connections:
            return ToolResult(True, "No metadata connections found in scan output.", {"count": 0})
        # Strip internal xmi_id and empty fields to reduce noise
        cleaned = [
            {k: v for k, v in c.items() if k != "xmi_id" and v not in (None, "", {}, [])}
            for c in connections
        ]
        text, truncated = self._bounded_text(json.dumps(cleaned, indent=2, ensure_ascii=False))
        return ToolResult(True, text, {"count": len(connections)}, truncated=truncated)

    def get_context_group(self, name: str) -> ToolResult:
        """Return context variables for a named group, with per-environment values."""
        if not name.strip():
            return ToolResult(False, "name is required")
        scan = self.scan_root / "scan_result.json"
        if not scan.exists():
            return ToolResult(False, f"scan_result.json not found under {self.scan_root}")
        data = json.loads(scan.read_text(encoding="utf-8", errors="replace"))
        groups = data.get("context_groups") or []
        # Exact match first, then case-insensitive partial
        match = next((g for g in groups if g.get("name") == name), None)
        if not match:
            match = next((g for g in groups if name.lower() in (g.get("name") or "").lower()), None)
        if not match:
            available = [g.get("name", "") for g in groups[:30]]
            return ToolResult(False, f"Context group {name!r} not found. Available: {available}")
        variables = match.get("variables") or []
        text, truncated = self._bounded_text(json.dumps(match, indent=2, ensure_ascii=False))
        return ToolResult(
            True, text,
            {"name": match.get("name"), "variable_count": len(variables)},
            truncated=truncated,
        )

    def search_scan_jobs(self, query: str) -> ToolResult:
        """Search jobs by name, group, component type, or context group reference."""
        if not query.strip():
            return ToolResult(False, "query is required")
        scan = self.scan_root / "scan_result.json"
        if not scan.exists():
            return ToolResult(False, f"scan_result.json not found under {self.scan_root}")
        data = json.loads(scan.read_text(encoding="utf-8", errors="replace"))
        jobs = data.get("jobs") or []
        q = query.lower()
        results: list[dict[str, Any]] = []
        for job in jobs:
            name_lower = (job.get("name") or "").lower()
            group_lower = (job.get("group") or "").lower()
            subgroup_lower = (job.get("subgroup") or "").lower()
            component_types = [(c.get("type") or "").lower() for c in (job.get("components") or [])]
            ctx_groups = [(cg or "").lower() for cg in (job.get("context_groups") or [])]
            if (
                q in name_lower
                or q in group_lower
                or q in subgroup_lower
                or any(q in ct for ct in component_types)
                or any(q in cg for cg in ctx_groups)
            ):
                results.append({
                    "name": job.get("name"),
                    "path": job.get("path"),
                    "group": job.get("group"),
                    "subgroup": job.get("subgroup") or None,
                    "components": [c.get("type") for c in (job.get("components") or [])[:15]],
                    "context_groups": (job.get("context_groups") or [])[:10],
                })
            if len(results) >= config.deep_dive_max_search_results:
                break
        if not results:
            return ToolResult(True, f"No jobs found matching {query!r}.", {"count": 0, "total_jobs": len(jobs)})
        text, truncated = self._bounded_text(json.dumps(results, indent=2, ensure_ascii=False))
        return ToolResult(True, text, {"count": len(results), "total_jobs": len(jobs)}, truncated=truncated)

    @staticmethod
    def build_project_briefing(project_id: str) -> dict[str, Any]:
        """Build a concise project briefing from scan output — injected into the system prompt.

        Reads scan_result.json and the docs directory. No model call, no tool call.
        Gives the agent platform/structure awareness before turn 1.
        """
        scan = config.intodoc_output_dir / project_id / "scan_result.json"
        docs_dir = config.project_root / "output" / project_id / "docs"
        briefing: dict[str, Any] = {"project_id": project_id}

        if scan.exists():
            try:
                data = json.loads(scan.read_text(encoding="utf-8", errors="replace"))
                jobs = data.get("jobs") or []
                groups: dict[str, int] = {}
                for job in jobs:
                    g = job.get("group") or "Global"
                    groups[g] = groups.get(g, 0) + 1

                connections = data.get("metadata_connections") or []
                briefing.update({
                    "platform": data.get("platform", "unknown"),
                    "job_count": len(jobs),
                    "groups": groups,
                    "connection_count": len(connections),
                    "connections": [
                        {"name": c.get("name"), "type": c.get("connection_type")}
                        for c in connections
                    ],
                    "context_group_count": len(data.get("context_groups") or []),
                    "context_group_names": [
                        g.get("name") for g in (data.get("context_groups") or [])[:30]
                    ],
                })
            except Exception:
                pass

        if docs_dir.exists():
            briefing["generated_docs"] = sorted(
                f.name for f in docs_dir.glob("*.md") if f.name[0].isdigit()
            )

        return briefing


def _provider_config(model_id: str) -> dict[str, str | None]:
    model_id = normalize_model_id(model_id)
    provider = model_id.partition("/")[0]
    if provider == "deepseek":
        return {
            "api_key": config.doc_generation_deepseek_api_key or config.doc_generation_api_key,
            "base_url": config.doc_generation_deepseek_base_url or config.doc_generation_base_url,
        }
    if provider in {"openai", "anthropic", "google"}:
        return {"api_key": None, "base_url": None}
    return {
        "api_key": config.doc_generation_api_key,
        "base_url": config.doc_generation_base_url,
    }


class RepoAgentEngine:
    """Small OpenHarness-style read-only repo agent."""

    def __init__(self, provider: ModelProvider | None = None) -> None:
        self._sessions: dict[str, AgentState] = {}
        # Provider is rebuilt per query so a runtime model override from the
        # instance settings table takes effect immediately without restart.
        # `provider` arg is preserved for tests / explicit injection.
        self._injected_provider = provider

    def _build_provider(self, model_id: str) -> ModelProvider:
        provider_config = _provider_config(model_id)
        return build_model_provider(
            model_id,
            api_key=config.deep_dive_api_key or provider_config["api_key"],
            base_url=config.deep_dive_base_url or provider_config["base_url"],
            ollama_base_url=config.ollama_base_url,
        )

    async def _resolve_model_id(self) -> str:
        override = await instance_settings_service.get_value(
            "deep_dive_model", default=config.deep_dive_model
        )
        return normalize_model_id(override or config.deep_dive_model)

    async def query(
        self,
        project_id: str,
        question: str,
        session_id: str | None = None,
    ) -> AsyncGenerator[DeepDiveEvent, None]:
        start = time.monotonic()
        if not session_id:
            session_id = str(uuid.uuid4())
        state = self._sessions.setdefault(session_id, AgentState(session_id=session_id, project_id=project_id))
        if state.project_id != project_id:
            state = AgentState(session_id=session_id, project_id=project_id)
            self._sessions[session_id] = state

        model_id = await self._resolve_model_id()
        provider = self._injected_provider or self._build_provider(model_id)

        yield {"type": "session", "data": session_id}
        yield {"type": "status", "data": f"Analyzing project {project_id} with {model_id}..."}

        toolbox = RepoToolbox(project_id)
        state.messages.append({"role": "user", "content": question})
        briefing = RepoToolbox.build_project_briefing(project_id)
        transcript = self._initial_transcript(project_id, question, toolbox, briefing)
        total_input = total_output = 0

        for turn in range(max(1, config.deep_dive_max_tool_calls) + 1):
            result = await provider.complete(
                transcript,
                options=ModelOptions(
                    max_tokens=config.deep_dive_max_answer_tokens,
                    temperature=0.1,
                    timeout_s=config.deep_dive_timeout_s,
                    retries=0,
                ),
            )
            total_input += result.input_tokens
            total_output += result.output_tokens
            if not result.success:
                if "stop_reason=max_tokens" in (result.raw_error or "") and result.text:
                    answer = self._strip_internal_transcript(result.text).strip()
                    if answer:
                        state.messages.append({"role": "assistant", "content": answer})
                        yield {"type": "token", "data": answer}
                        yield {"type": "done", "data": {"duration_ms": int((time.monotonic() - start) * 1000)}}
                        return
                yield {"type": "error", "data": f"Model failed: {result.raw_error}"}
                yield {"type": "done", "data": {"duration_ms": int((time.monotonic() - start) * 1000)}}
                return

            decision = self._parse_decision(result.text)
            if decision.get("final") is not None:
                answer = self._strip_internal_transcript(str(decision["final"])).strip()
                if not answer:
                    yield {"type": "error", "data": "Deep Dive ended without a final answer."}
                    yield {"type": "done", "data": {"duration_ms": int((time.monotonic() - start) * 1000)}}
                    return
                state.messages.append({"role": "assistant", "content": answer})
                yield {"type": "token", "data": answer}
                yield {
                    "type": "usage",
                    "data": {
                        "model": getattr(provider, "model_name", model_id),
                        "provider": getattr(provider, "provider_name", "custom"),
                        "input_tokens": total_input,
                        "output_tokens": total_output,
                        "duration_ms": int((time.monotonic() - start) * 1000),
                    },
                }
                yield {"type": "done", "data": {"duration_ms": int((time.monotonic() - start) * 1000)}}
                return

            tool_calls = self._decision_tool_calls(decision)
            if not tool_calls:
                answer = self._strip_internal_transcript(str(result.text or "")).strip()
                if not answer:
                    yield {"type": "error", "data": "Deep Dive ended without a final answer."}
                    yield {"type": "done", "data": {"duration_ms": int((time.monotonic() - start) * 1000)}}
                    return
                state.messages.append({"role": "assistant", "content": answer})
                yield {"type": "token", "data": answer}
                yield {"type": "done", "data": {"duration_ms": int((time.monotonic() - start) * 1000)}}
                return

            tool_results: list[dict[str, Any]] = []
            for tool_name, args in tool_calls:
                if state.tool_calls >= config.deep_dive_max_tool_calls:
                    async for event in self._force_final_answer(
                        self._cap_transcript(transcript),
                        start,
                        total_input,
                        total_output,
                        state,
                        provider=provider,
                        model_id=model_id,
                        reason=f"Tool budget reached after {state.tool_calls} repository inspections.",
                    ):
                        yield event
                    return

                state.tool_calls += 1
                yield {"type": "tool_use", "data": f"{tool_name}({json.dumps(args, ensure_ascii=False)[:300]})"}
                tool_result = await asyncio.to_thread(toolbox.run, tool_name, args, state)
                tool_results.append(
                    {
                        "tool": tool_name,
                        "arguments": args,
                        "result": json.loads(tool_result.as_json()),
                    }
                )

            transcript += (
                "\n\nMODEL_DECISION:\n"
                f"{json.dumps(decision, ensure_ascii=False)}\n\n"
                "TOOL_RESULTS:\n"
                f"{json.dumps(tool_results, ensure_ascii=False)}\n"
            )
            transcript = self._prune_transcript(transcript)

        async for event in self._force_final_answer(
            self._cap_transcript(transcript),
            start,
            total_input,
            total_output,
            state,
            provider=provider,
            model_id=model_id,
            reason="Deep Dive reached its reasoning turn limit.",
        ):
            yield event

    async def _force_final_answer(
        self,
        transcript: str,
        start: float,
        total_input: int,
        total_output: int,
        state: AgentState,
        *,
        provider: ModelProvider,
        model_id: str,
        reason: str,
    ) -> AsyncGenerator[DeepDiveEvent, None]:
        yield {"type": "status", "data": "Preparing answer from inspected repository evidence..."}
        final_prompt = f"""\
{transcript}

FINAL_SYNTHESIS_REQUIRED:
{reason}

You must now answer the user's question using only the evidence already present above.
Do not call tools. Do not output JSON, XML, MODEL_DECISION, TOOL_RESULTS, or tool-call syntax.
Return only the user-facing markdown answer. If the evidence is incomplete, say what was inspected and what remains unknown.
"""
        result = await provider.complete(
            final_prompt,
            options=ModelOptions(
                max_tokens=config.deep_dive_max_answer_tokens,
                temperature=0.1,
                timeout_s=config.deep_dive_timeout_s,
                retries=0,
            ),
        )
        total_input += result.input_tokens
        total_output += result.output_tokens

        answer_text: str | None = None
        if result.success and result.text.strip():
            answer_text = self._strip_internal_transcript(result.text).strip()
        elif "stop_reason=max_tokens" in (result.raw_error or "") and result.text.strip():
            answer_text = self._strip_internal_transcript(result.text).strip()

        if answer_text:
            state.messages.append({"role": "assistant", "content": answer_text})
            yield {"type": "token", "data": answer_text}
            yield {
                "type": "usage",
                "data": {
                    "model": getattr(provider, "model_name", model_id),
                    "provider": getattr(provider, "provider_name", "custom"),
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                    "duration_ms": int((time.monotonic() - start) * 1000),
                },
            }
            yield {"type": "done", "data": {"duration_ms": int((time.monotonic() - start) * 1000)}}
            return

        yield {"type": "error", "data": f"{reason} The model did not produce a usable final answer."}
        yield {"type": "done", "data": {"duration_ms": int((time.monotonic() - start) * 1000)}}

    @staticmethod
    def _initial_transcript(
        project_id: str,
        question: str,
        toolbox: RepoToolbox,
        briefing: dict[str, Any] | None = None,
    ) -> str:
        tools = [
            {"name": spec.name, "description": spec.description, "parameters": spec.parameters}
            for spec in toolbox.specs()
        ]

        briefing_block = ""
        if briefing:
            briefing_block = f"""
Project context (already loaded — no tool call needed for this information):
{json.dumps(briefing, indent=2, ensure_ascii=False)}
"""

        return f"""\
You are a read-only repository analysis agent for project {project_id!r}.
{briefing_block}
Navigation strategy — follow this order:
1. For questions about external systems, databases, or APIs: call get_scan_connections() first.
   Use get_context_group(name) to resolve the actual host/port/database values.
2. For questions about which jobs do something: call search_scan_jobs(query) with a component
   type (e.g. "tJDBCInput"), connection name, or keyword. Returns file paths directly.
3. For code-level questions: use search_repo(query, glob="*.ext", files_only=true) to find
   which files are relevant, then read_file(path, start_line, max_lines) to read only the
   section you need. Prefer targeted reads over reading whole files.
4. Batch independent lookups in one turn: {{"tool_calls": [...]}}
5. Stop as soon as you have sufficient evidence — do not keep reading once the answer is clear.

Do not guess or infer from training data. Cite the actual file path or scan data field in your answer.

Available tools:
{json.dumps(tools, indent=2)}

Respond with exactly one JSON object and no markdown fences.

To call a single tool:
{{"tool": "search_repo", "arguments": {{"query": "tJDBCInput", "glob": "*.item", "files_only": true}}}}

To call multiple tools in one turn:
{{"tool_calls": [{{"tool": "get_scan_connections", "arguments": {{}}}}, {{"tool": "list_files", "arguments": {{"path": "", "max_depth": 2}}}}]}}

To finish:
{{"final": "Markdown answer citing file paths or scan fields."}}

Do not emit XML, HTML, <function_calls>, or prose around tool calls. Only JSON is valid.

User question: {question}
"""

    @staticmethod
    def _parse_decision(text: str) -> dict[str, Any]:
        stripped = text.strip()
        if not stripped:
            return {"final": "The model returned an empty response before using any evidence."}
        try:
            data = json.loads(stripped)
            return data if isinstance(data, dict) else {"final": stripped}
        except json.JSONDecodeError:
            function_tool_call = RepoAgentEngine._parse_function_tool_call(stripped)
            if function_tool_call:
                return function_tool_call
            json_candidate = RepoAgentEngine._extract_first_json_object(stripped)
            if json_candidate:
                try:
                    data = json.loads(json_candidate)
                    return data if isinstance(data, dict) else {"final": stripped}
                except json.JSONDecodeError:
                    pass
        xml_tool_calls = RepoAgentEngine._parse_xml_tool_calls(stripped)
        if xml_tool_calls:
            return {"tool_calls": xml_tool_calls}
        return {"final": stripped}

    @staticmethod
    def _strip_internal_transcript(text: str) -> str:
        stripped = text.strip()
        for marker in ("TOOL_RESULTS:", "MODEL_DECISION:", "<function_calls>", '{"tool_calls"', '{"tool"'):
            idx = stripped.find(marker)
            if idx == 0:
                return ""
        return stripped

    @staticmethod
    def _extract_first_json_object(text: str) -> str | None:
        start = text.find("{")
        if start < 0:
            return None

        depth = 0
        in_string = False
        escaped = False
        for idx in range(start, len(text)):
            char = text[idx]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : idx + 1]
        return None

    @staticmethod
    def _decision_tool_calls(decision: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        if isinstance(decision.get("tool"), str):
            args = decision.get("arguments") if isinstance(decision.get("arguments"), dict) else {}
            return [(str(decision["tool"]), args)]

        raw_calls = decision.get("tool_calls")
        if not isinstance(raw_calls, list):
            return []

        calls: list[tuple[str, dict[str, Any]]] = []
        for call in raw_calls:
            if not isinstance(call, dict) or not isinstance(call.get("tool"), str):
                continue
            args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
            calls.append((str(call["tool"]), args))
        return calls

    @staticmethod
    def _parse_function_tool_call(text: str) -> dict[str, Any] | None:
        match = re.search(r"^\s*([a-zA-Z_][\w]*)\s*\(\s*(\{.*\})\s*\)\s*$", text, re.DOTALL)
        if not match:
            return None
        try:
            args = json.loads(match.group(2))
        except json.JSONDecodeError:
            return None
        if not isinstance(args, dict):
            return None
        return {"tool": match.group(1), "arguments": args}

    @staticmethod
    def _parse_xml_tool_calls(text: str) -> list[dict[str, Any]]:
        if "<invoke" not in text or "<parameter" not in text:
            return []

        calls: list[dict[str, Any]] = []
        invoke_pattern = re.compile(
            r"<invoke\b(?P<attrs>[^>]*)>(?P<body>.*?)</invoke>",
            re.DOTALL | re.IGNORECASE,
        )
        param_pattern = re.compile(
            r"<parameter\b(?P<attrs>[^>]*)>(?P<value>.*?)</parameter>",
            re.DOTALL | re.IGNORECASE,
        )

        for invoke_match in invoke_pattern.finditer(text):
            name = RepoAgentEngine._xml_attr(invoke_match.group("attrs"), "name")
            if not name:
                continue
            args: dict[str, Any] = {}
            for param_match in param_pattern.finditer(invoke_match.group("body")):
                param_name = RepoAgentEngine._xml_attr(param_match.group("attrs"), "name")
                if not param_name:
                    continue
                raw_value = unescape(re.sub(r"<[^>]+>", "", param_match.group("value"))).strip()
                args[param_name] = RepoAgentEngine._coerce_tool_arg(param_name, raw_value)
            calls.append({"tool": name, "arguments": args})
        return calls

    @staticmethod
    def _xml_attr(attrs: str, name: str) -> str | None:
        match = re.search(rf'\b{re.escape(name)}=["\']([^"\']+)["\']', attrs)
        return unescape(match.group(1)) if match else None

    @staticmethod
    def _coerce_tool_arg(name: str, value: str) -> Any:
        if name in {"max_depth", "max_lines", "max_results", "start_line"}:
            try:
                return int(value)
            except ValueError:
                return value
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
        return value

    @staticmethod
    def _estimate_transcript_tokens(transcript: str) -> int:
        return len(transcript) // 4

    @staticmethod
    def _prune_transcript(transcript: str, target_tokens: int = 150_000) -> str:
        """Drop content of old TOOL_RESULTS blocks when transcript approaches context limit.

        Keeps the system prompt + question, all MODEL_DECISION lines (small, preserve reasoning
        trail), and the last 3 TOOL_RESULTS blocks verbatim. Older results are stubbed out.
        """
        if RepoAgentEngine._estimate_transcript_tokens(transcript) <= target_tokens:
            return transcript

        marker = "\n\nMODEL_DECISION:\n"
        parts = transcript.split(marker)
        # parts[0] = system prompt + question; parts[1:] = per-turn (decision + results)
        if len(parts) <= 4:  # header + ≤3 turns — nothing to prune without destroying evidence
            return transcript

        header = parts[0]
        turns = parts[1:]
        keep_intact = 3
        tool_results_marker = "\n\nTOOL_RESULTS:\n"

        pruned: list[str] = []
        for i, turn in enumerate(turns):
            if i >= len(turns) - keep_intact:
                pruned.append(turn)
            elif tool_results_marker in turn:
                decision_part = turn.split(tool_results_marker)[0]
                pruned.append(decision_part + tool_results_marker + "[pruned]\n")
            else:
                pruned.append(turn)

        return marker.join([header] + pruned)

    @staticmethod
    def _cap_transcript(transcript: str, max_tokens: int = 120_000) -> str:
        """Hard cap for force-synthesis calls: keep the first 2 KB (system prompt + question)
        and the most recent tail so the model always has the original question plus latest evidence.
        """
        max_chars = max_tokens * 4
        if len(transcript) <= max_chars:
            return transcript
        header = transcript[:2_000]
        tail = transcript[-(max_chars - 2_000):]
        return header + "\n\n[...earlier tool results pruned to fit context window...]\n\n" + tail


_QA_PROMPT = """You are a code analyst for project '{project_id}'. Answer this question by reading the scan data:

**Question:** {question}

Use MCP tools strategically:
1. get_scan_status('{project_id}') — verify data exists
2. get_chunks('{project_id}') — see project structure (groups, jobs)
3. fetch_json('{project_id}', 'manifest') — job relationships
4. fetch_json('{project_id}', 'connections') — database/API connections
5. fetch_chunk('{project_id}', chunk_id) — specific job details

Rules:
- Read actual scan data before answering. Don't guess.
- Only fetch what you need — don't load all chunks unless required.
- Reference actual job names, components, and connections.
- Use markdown formatting.
- If the data doesn't answer the question, say so.
- Keep findings focused and factual."""


class ClaudeCliDeepDiveEngine:
    """Legacy Deep Dive engine using Claude Code CLI."""

    def __init__(self) -> None:
        self._text_streamed_via_delta = False

    async def query(
        self,
        project_id: str,
        question: str,
        session_id: str | None = None,
    ) -> AsyncGenerator[DeepDiveEvent, None]:
        start_time = time.monotonic()
        self._text_streamed_via_delta = False
        prompt = _QA_PROMPT.format(project_id=project_id, question=question)

        if not ensure_token_fresh():
            yield {"type": "error", "data": "Claude OAuth token expired and refresh failed. Run 'claude login' as the service user to re-authenticate."}
            return

        resolved_cmd = shutil.which(config.claude_command)
        if not resolved_cmd:
            yield {"type": "error", "data": f"Claude CLI not found: {config.claude_command}"}
            return

        is_continuation = session_id is not None
        if not session_id:
            session_id = str(uuid.uuid4())
        yield {"type": "session", "data": session_id}

        cmd = [
            resolved_cmd,
            "-p",
            prompt,
            "--dangerously-skip-permissions",
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-turns",
            "50",
        ]
        if is_continuation:
            cmd.extend(["--session-id", session_id, "--continue"])

        yield {"type": "status", "data": f"Analyzing project {project_id} with Claude CLI..."}
        proc: subprocess.Popen | None = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                env={**os.environ, "PYTHONUTF8": "1"},
                cwd=str(config.project_root),
            )

            async def _drain_stderr(stream) -> str:
                lines: list[str] = []
                while True:
                    line = await asyncio.to_thread(stream.readline)
                    if not line:
                        break
                    stripped = line.rstrip()
                    if stripped:
                        lines.append(stripped)
                        logger.debug("[deep-dive stderr] %s", stripped[:200])
                return "\n".join(lines)

            assert proc.stderr is not None
            stderr_task = asyncio.create_task(_drain_stderr(proc.stderr))
            assert proc.stdout is not None
            while True:
                line = await asyncio.to_thread(proc.stdout.readline)
                if not line:
                    break
                stripped = line.rstrip()
                if not stripped:
                    continue
                try:
                    event = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                async for sse_event in self._convert_event(event):
                    yield sse_event

            returncode = await asyncio.to_thread(proc.wait)
            stderr_text = await stderr_task
            if stderr_text and returncode != 0:
                logger.warning("Deep dive stderr: %s", stderr_text[:500])
            duration_ms = int((time.monotonic() - start_time) * 1000)
            if returncode != 0:
                yield {"type": "error", "data": f"Claude Code exited with code {returncode}"}
            else:
                yield {"type": "done", "data": {"duration_ms": duration_ms}}
        except Exception as exc:
            logger.error("Deep dive error [%s]: %s\n%s", type(exc).__name__, exc, traceback.format_exc())
            yield {"type": "error", "data": f"Deep dive failed: {type(exc).__name__}: {exc}"}
        finally:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    await asyncio.to_thread(proc.wait, timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

    async def _convert_event(self, event: dict) -> AsyncGenerator[DeepDiveEvent, None]:
        etype = event.get("type", "")
        if etype == "content_block_start":
            block = event.get("content_block", {})
            if block.get("type") == "tool_use":
                yield {"type": "tool_use", "data": f"{block.get('name', '?')}(...)"}
            elif block.get("type") == "thinking":
                yield {"type": "thinking_start", "data": ""}
        elif etype == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta" and delta.get("text"):
                self._text_streamed_via_delta = True
                yield {"type": "token", "data": delta["text"]}
            elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                yield {"type": "thinking", "data": delta["thinking"]}
        elif etype == "assistant":
            message = event.get("message", {})
            for block in message.get("content", []):
                if block.get("type") == "text" and not self._text_streamed_via_delta and block.get("text"):
                    yield {"type": "token", "data": block["text"]}
                elif block.get("type") == "tool_use":
                    inp = block.get("input", {})
                    summary = ", ".join(f"{k}={repr(v)[:80]}" for k, v in list(inp.items())[:4])
                    yield {"type": "tool_use", "data": f"{block.get('name', '?')}({summary})"}
        elif etype == "result":
            result_data: dict[str, Any] = {}
            for src, dst in (
                ("cost_usd", "cost_usd"),
                ("total_input_tokens", "input_tokens"),
                ("total_output_tokens", "output_tokens"),
                ("duration_ms", "duration_ms"),
            ):
                if event.get(src) is not None:
                    result_data[dst] = event[src]
            if result_data:
                yield {"type": "usage", "data": result_data}
            if event.get("result"):
                yield {"type": "token", "data": event["result"]}
        elif etype == "system" and event.get("message"):
            yield {"type": "status", "data": event["message"]}


class DeepDiveService:
    """Public service preserving the existing SSE event contract."""

    def __init__(self, engine: DeepDiveEngine | None = None) -> None:
        self.engine = engine or self._build_engine()

    @staticmethod
    def _build_engine() -> DeepDiveEngine:
        engine = config.deep_dive_engine.strip().lower()
        if engine == "repo_agent":
            return RepoAgentEngine()
        if engine == "claude_cli":
            return ClaudeCliDeepDiveEngine()
        if config.deep_dive_allow_claude_cli_fallback:
            logger.warning("Unsupported deep dive engine %r; falling back to claude_cli", engine)
            return ClaudeCliDeepDiveEngine()
        raise ValueError(f"Unsupported MCP_DEEP_DIVE_ENGINE={config.deep_dive_engine!r}")

    async def query(
        self,
        project_id: str,
        question: str,
        session_id: str | None = None,
    ) -> AsyncGenerator[DeepDiveEvent, None]:
        async for event in self.engine.query(project_id, question, session_id=session_id):
            yield event
