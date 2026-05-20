"""Bounded internal agent runtime for custom documentation generation.

The runtime is intentionally small: providers still do the language work, while
Python owns contracts, scoped evidence access, budgets, persistence, and status.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from mcp_server.config import config
from mcp_server.providers.custom.models import FactsManifest, ModelResult


AgentPhase = Literal["phase2_group", "phase2_merge", "scorecard", "phase3_doc", "repair"]


class ToolBudget(BaseModel):
    max_calls: int = 12
    max_bytes: int = 80_000
    max_result_bytes: int = 12_000


class InputBudget(BaseModel):
    soft_tokens: int = 80_000
    hard_tokens: int = 120_000


class AgentTaskContract(BaseModel):
    phase: AgentPhase
    target_id: str
    allowed_units: list[str] = Field(default_factory=list)
    allowed_groups: list[str] = Field(default_factory=list)
    allowed_categories: list[str] = Field(default_factory=list)
    starter_context_files: list[str] = Field(default_factory=list)
    tool_budget: ToolBudget = Field(default_factory=ToolBudget)
    input_budget: InputBudget = Field(default_factory=InputBudget)
    expected_artifact: str
    attempt: int = 1
    max_attempts: int = 2
    validation_gaps: list[str] = Field(default_factory=list)

    def safe_target_id(self) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", self.target_id)


class ToolCallRecord(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    bytes: int = 0
    error: str | None = None


class AgentTaskResult(BaseModel):
    accepted: bool
    status: Literal["accepted", "rejected", "incomplete"]
    incomplete_reason: str | None = None
    output_path: str | None = None
    validation_report_path: str | None = None
    result: ModelResult


class ScopedEvidenceTools:
    """Evidence lookup functions constrained by a single task contract."""

    def __init__(self, staging_dir: Path, facts: FactsManifest, contract: AgentTaskContract) -> None:
        self.staging_dir = staging_dir
        self.facts = facts
        self.contract = contract
        self.calls: list[ToolCallRecord] = []
        self.bytes_returned = 0

    def get_unit_evidence(self, unit_name: str, categories: list[str] | None = None, limit: int = 20) -> str:
        self._check_unit(unit_name)
        return self._record(
            "get_unit_evidence",
            {"unit_name": unit_name, "categories": categories or [], "limit": limit},
            lambda: self._search(unit_name, categories, limit),
        )

    def get_group_evidence(self, group_name: str, categories: list[str] | None = None, limit: int = 40) -> str:
        self._check_group(group_name)
        return self._record(
            "get_group_evidence",
            {"group_name": group_name, "categories": categories or [], "limit": limit},
            lambda: self._search(group_name, categories, limit),
        )

    def get_doc_gap_evidence(
        self,
        doc_name: str,
        missing_units: list[str],
        missing_categories: list[str] | None = None,
    ) -> str:
        for unit in missing_units:
            self._check_unit(unit)
        return self._record(
            "get_doc_gap_evidence",
            {
                "doc_name": doc_name,
                "missing_units": missing_units,
                "missing_categories": missing_categories or [],
            },
            lambda: self._search(" ".join([doc_name, *missing_units]), missing_categories, 60),
        )

    def search_evidence(
        self,
        query: str,
        scope: str | None = None,
        categories: list[str] | None = None,
        limit: int = 20,
    ) -> str:
        if scope and scope not in set(self.contract.allowed_units + self.contract.allowed_groups + [self.contract.target_id]):
            raise ValueError(f"scope outside task contract: {scope}")
        return self._record(
            "search_evidence",
            {"query": query, "scope": scope, "categories": categories or [], "limit": limit},
            lambda: self._search(" ".join([scope or "", query]), categories, limit),
        )

    def get_fact_table(self, table_name: str, filters: dict[str, Any] | None = None, limit: int = 50) -> str:
        allowed = {"facts_manifest", "units", "groups", "component_counts", "key_systems"}
        if table_name not in allowed:
            raise ValueError(f"fact table not allowed: {table_name}")
        return self._record(
            "get_fact_table",
            {"table_name": table_name, "filters": filters or {}, "limit": limit},
            lambda: self._fact_table(table_name, filters or {}, limit),
        )

    def _record(self, tool: str, args: dict[str, Any], func: Callable[[], str]) -> str:
        if len(self.calls) >= self.contract.tool_budget.max_calls:
            raise RuntimeError("tool call budget exceeded")
        try:
            text = func()
            raw = text.encode("utf-8")
            if len(raw) > self.contract.tool_budget.max_result_bytes:
                text = text[: self.contract.tool_budget.max_result_bytes] + "\n\n[truncated by tool result byte budget]"
            size = len(text.encode("utf-8"))
            if self.bytes_returned + size > self.contract.tool_budget.max_bytes:
                raise RuntimeError("tool byte budget exceeded")
            self.bytes_returned += size
            self.calls.append(ToolCallRecord(tool=tool, args=args, ok=True, bytes=size))
            return text
        except Exception as exc:
            self.calls.append(ToolCallRecord(tool=tool, args=args, ok=False, error=str(exc)))
            raise

    def _check_unit(self, unit_name: str) -> None:
        allowed = set(self.contract.allowed_units)
        if allowed and unit_name not in allowed:
            raise ValueError(f"unit outside task contract: {unit_name}")

    def _check_group(self, group_name: str) -> None:
        allowed = set(self.contract.allowed_groups)
        if allowed and group_name not in allowed:
            raise ValueError(f"group outside task contract: {group_name}")

    def _check_categories(self, categories: list[str] | None) -> None:
        allowed = set(self.contract.allowed_categories)
        if allowed:
            outside = sorted(set(categories or []) - allowed)
            if outside:
                raise ValueError(f"categories outside task contract: {', '.join(outside)}")

    def _search(self, query: str, categories: list[str] | None, limit: int) -> str:
        self._check_categories(categories)
        terms = [t.lower() for t in re.findall(r"[A-Za-z0-9_.-]+", query) if len(t) > 1]
        category_terms = [c.lower() for c in categories or []]
        candidates = self._evidence_files()
        results: list[str] = []
        for path in candidates:
            text = path.read_text(encoding="utf-8", errors="replace")
            lower = text.lower()
            if terms and not any(term in lower for term in terms):
                continue
            if category_terms and not any(term in lower for term in category_terms):
                continue
            snippet = self._snippet(text, terms or category_terms)
            results.append(f"Source: {path.relative_to(self.staging_dir).as_posix()}\n{snippet}")
            if len(results) >= max(1, limit):
                break
        return "\n\n---\n\n".join(results) if results else "No scoped evidence found."

    def _fact_table(self, table_name: str, filters: dict[str, Any], limit: int) -> str:
        if table_name == "facts_manifest":
            data: Any = self.facts.model_dump()
        elif table_name == "units":
            rows = [u.model_dump() for u in self.facts.units]
            for key, value in filters.items():
                rows = [row for row in rows if row.get(key) == value]
            data = rows[:limit]
        elif table_name == "groups":
            data = dict(list(self.facts.groups.items())[:limit])
        elif table_name == "component_counts":
            data = dict(list(self.facts.component_counts.items())[:limit])
        else:
            data = self.facts.key_systems[:limit]
        return json.dumps(data, indent=2)

    def _evidence_files(self) -> list[Path]:
        names = [
            "engineering_facts.md",
            "dependency_map.md",
            "architecture_map.md",
            "data_flow_map.md",
            "api_surface.md",
            "config_runtime_map.md",
            "known_risks.md",
            "test_coverage_map.md",
            "change_impact_matrix.md",
            "facts_manifest.json",
            "documentation_obligations.json",
        ]
        files = [self.staging_dir / name for name in names]
        files.extend(sorted((self.staging_dir / "_investigation").glob("*.md")))
        files.extend(sorted((self.staging_dir / "_analysis").glob("*.md")))
        return [path for path in files if path.exists() and path.is_file()]

    @staticmethod
    def _snippet(text: str, terms: list[str]) -> str:
        lower = text.lower()
        index = 0
        for term in terms:
            pos = lower.find(term)
            if pos >= 0:
                index = pos
                break
        start = max(0, index - 900)
        end = min(len(text), index + 2200)
        return text[start:end].strip()


class AgentTaskRuntime:
    """Persisted task runner used by the custom harness."""

    def __init__(self, staging_dir: Path, facts: FactsManifest) -> None:
        self.staging_dir = staging_dir
        self.facts = facts

    def task_dir(self, contract: AgentTaskContract) -> Path:
        return self.staging_dir / "_agent_tasks" / contract.phase / contract.safe_target_id()

    def accepted_output(self, contract: AgentTaskContract) -> str | None:
        task_dir = self.task_dir(contract)
        status_path = task_dir / "status.json"
        output_path = task_dir / "output.md"
        if not status_path.exists() or not output_path.exists():
            return None
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if status.get("status") == "accepted":
            return output_path.read_text(encoding="utf-8", errors="replace")
        return None

    async def run(
        self,
        contract: AgentTaskContract,
        dispatch: Callable[[AgentTaskContract, ScopedEvidenceTools], Awaitable[ModelResult]],
        validate: Callable[[str], dict[str, Any]],
    ) -> AgentTaskResult:
        task_dir = self.task_dir(contract)
        task_dir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        result = ModelResult(success=False, raw_error="task did not run")
        output = ""
        validation: dict[str, Any] = {"ok": False, "errors": ["task did not run"]}
        tools = ScopedEvidenceTools(self.staging_dir, self.facts, contract)
        conversation_attempts: list[dict[str, Any]] = []
        tool_ledgers: list[dict[str, Any]] = []
        provider_attempts: list[dict[str, Any]] = []
        incomplete_reason: str | None = None
        attempts = max(1, contract.max_attempts)
        for attempt in range(1, attempts + 1):
            attempt_contract = contract.model_copy(update={"attempt": attempt})
            if attempt > 1:
                attempt_contract.validation_gaps = list(validation.get("errors") or [])
            attempt_dir = task_dir / "attempts" / f"{attempt:02d}"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            (task_dir / "contract.json").write_text(attempt_contract.model_dump_json(indent=2), encoding="utf-8")
            (attempt_dir / "contract.json").write_text(attempt_contract.model_dump_json(indent=2), encoding="utf-8")
            tools = ScopedEvidenceTools(self.staging_dir, self.facts, attempt_contract)
            result = await dispatch(attempt_contract, tools)
            output = result.text.strip()
            (task_dir / "output.md").write_text(output, encoding="utf-8")
            (attempt_dir / "output.md").write_text(output, encoding="utf-8")
            validation = validate(output)
            (attempt_dir / "validation_report.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")
            attempt_tool_ledger = [call.model_dump() for call in tools.calls]
            provider_tool_ledger = result.metadata.get("tool_call_ledger") or []
            tool_ledgers.append({
                "attempt": attempt,
                "runtime_tools": attempt_tool_ledger,
                "provider_tools": provider_tool_ledger,
            })
            conversation_attempts.append({
                "attempt": attempt,
                "validation_gaps": attempt_contract.validation_gaps,
                "provider_trace": result.metadata.get("conversation_trace") or [],
            })
            provider_attempts.append({
                "attempt": attempt,
                "model_name": result.model_name,
                "success": result.success,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "latency_ms": result.latency_ms,
                "raw_error": result.raw_error,
                "metadata": result.metadata,
            })
            incomplete_reason = self._incomplete_reason(result, validation, attempt, attempts)
            if result.success and validation.get("ok", False):
                incomplete_reason = None
                break

        accepted = bool(result.success and validation.get("ok", False))
        status = "accepted" if accepted else ("incomplete" if incomplete_reason else "rejected")
        (task_dir / "validation_report.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")
        (task_dir / "tool_call_ledger.json").write_text(json.dumps(tool_ledgers, indent=2), encoding="utf-8")
        (task_dir / "conversation_trace.json").write_text(json.dumps(conversation_attempts, indent=2), encoding="utf-8")
        (task_dir / "provider_metrics.json").write_text(
            json.dumps(
                {
                    "model_name": result.model_name,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "latency_ms": result.latency_ms,
                    "runtime_ms": int((time.monotonic() - started) * 1000),
                    "attempts": provider_attempts,
                    "metadata": result.metadata,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (task_dir / "status.json").write_text(
            json.dumps(
                {
                    "status": status,
                    "accepted": accepted,
                    "incomplete_reason": incomplete_reason,
                    "updated_at": time.time(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return AgentTaskResult(
            accepted=accepted,
            status=status,
            incomplete_reason=incomplete_reason,
            output_path=str(task_dir / "output.md"),
            validation_report_path=str(task_dir / "validation_report.json"),
            result=result,
        )

    @staticmethod
    def _incomplete_reason(
        result: ModelResult,
        validation: dict[str, Any],
        attempt: int,
        attempts: int,
    ) -> str | None:
        if result.metadata.get("incomplete_reason"):
            return str(result.metadata["incomplete_reason"])
        if result.metadata.get("finish_reason") in {"length", "max_tokens"}:
            return "length"
        failure_kind = result.metadata.get("failure_kind")
        if failure_kind == "budget_exceeded":
            return "budget_exhaustion"
        if not result.success:
            return None
        if not validation.get("ok", False) and attempt >= attempts:
            return "validation_failure"
        return None


def default_tool_budget() -> ToolBudget:
    return ToolBudget(
        max_calls=config.agent_max_tool_calls,
        max_bytes=config.agent_max_tool_bytes,
        max_result_bytes=config.agent_max_tool_result_bytes,
    )


def default_input_budget() -> InputBudget:
    return InputBudget(
        soft_tokens=config.core_context_soft_target_tokens,
        hard_tokens=config.core_context_hard_ceiling_tokens,
    )
