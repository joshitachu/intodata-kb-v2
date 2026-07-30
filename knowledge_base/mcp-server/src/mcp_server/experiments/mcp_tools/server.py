"""Pure in-process MCP tool dispatcher; HTTP transport is deliberately out of scope."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping

from mcp_server.experiments.mcp_tools.artifact_tools import TaskArtifactWriter
from mcp_server.experiments.mcp_tools.auth import TaskTokenClaims, TaskTokenError, TaskTokenVerifier, token_audit_fields
from mcp_server.experiments.mcp_tools.budgets import BudgetUsage, CumulativeBudgetLedger, ToolBudgetExceeded, ToolBudgetLimits
from mcp_server.experiments.mcp_tools.config_compiler import NEXUS_EXPERIMENT_TOOLS
from mcp_server.experiments.mcp_tools.evidence_tools import (
    ImmutableTaskEvidence,
    ToolScopeDenied,
    get_contract,
    list_evidence,
    read_evidence,
    search_evidence,
    traverse_relationships,
)
from mcp_server.experiments.mcp_tools.ledger import ToolAuditLedger


class ToolInvocationDenied(PermissionError):
    pass


@dataclass(slots=True)
class ExperimentTaskToolServer:
    context: ImmutableTaskEvidence
    writer: TaskArtifactWriter
    verifier: TaskTokenVerifier
    budget_ledger: CumulativeBudgetLedger
    audit_ledger: ToolAuditLedger = field(default_factory=ToolAuditLedger)
    _submitted: bool = False
    _operation_results: dict[str, tuple[str, str, Mapping[str, Any]]] = field(default_factory=dict)

    @classmethod
    def for_task(
        cls,
        *,
        context: ImmutableTaskEvidence,
        writer: TaskArtifactWriter,
        verifier: TaskTokenVerifier,
        limits: ToolBudgetLimits,
    ) -> "ExperimentTaskToolServer":
        return cls(context=context, writer=writer, verifier=verifier, budget_ledger=CumulativeBudgetLedger(limits))

    def invoke(
        self,
        *,
        bearer_token: str,
        tool_name: str,
        request: Mapping[str, Any],
        operation_id: str,
        now=None,
    ) -> Mapping[str, Any]:
        try:
            claims = self.verifier.verify(
                bearer_token,
                now=now,
                project_id=self.context.project_id,
                experiment_id=self.context.experiment_id,
                task_id=self.context.task_id,
            )
            self._authorize_tool(claims, tool_name)
            self._authorize_token_budgets(claims)
            request_digest = _request_digest(request)
            cached = self._operation_results.get(operation_id)
            if cached is not None:
                if cached[0] != tool_name or cached[1] != request_digest:
                    raise ToolInvocationDenied("reused operation id has a different tool request")
                return cached[2]
            if tool_name == "nexus_experiment_write_artifact":
                response, content = self._prepare_artifact_write(request)
                usage = BudgetUsage(
                    calls=1,
                    result_bytes=_json_bytes(response),
                    write_bytes=len(content),
                )
                self.budget_ledger.consume(operation_id=operation_id, delta=usage)
                self.writer.write(
                    logical_name=str(response["logical_name"]),
                    media_type=str(response["media_type"]),
                    content=content,
                )
                self._audit_accept(claims, tool_name, operation_id, usage)
                self._operation_results[operation_id] = (tool_name, request_digest, response)
                return response
            if tool_name == "nexus_experiment_submit_task":
                response = self._prepare_submit()
                usage = BudgetUsage(calls=1, result_bytes=_json_bytes(response))
                self.budget_ledger.consume(operation_id=operation_id, delta=usage)
                self._submitted = True
                self._audit_accept(claims, tool_name, operation_id, usage)
                self._operation_results[operation_id] = (tool_name, request_digest, response)
                return response
            response, usage = self._dispatch(claims, tool_name, request)
            usage = BudgetUsage(
                calls=usage.calls,
                read_bytes=usage.read_bytes,
                result_bytes=_json_bytes(response),
                write_bytes=usage.write_bytes,
            )
            self.budget_ledger.consume(operation_id=operation_id, delta=usage)
            self._audit_accept(claims, tool_name, operation_id, usage)
            self._operation_results[operation_id] = (tool_name, request_digest, response)
            return response
        except (TaskTokenError, ToolScopeDenied, ToolBudgetExceeded, ToolInvocationDenied, ValueError, PermissionError) as exc:
            self.audit_ledger.append(
                tool_name=tool_name,
                outcome="denied",
                operation_id=operation_id,
                detail={"reason": str(exc), "request_keys": sorted(str(key) for key in request)},
            )
            raise ToolInvocationDenied(str(exc)) from exc

    def _authorize_tool(self, claims: TaskTokenClaims, tool_name: str) -> None:
        if (
            tool_name not in NEXUS_EXPERIMENT_TOOLS
            or tool_name not in claims.allowed_tools
            or tool_name not in self.context.contract.allowed_tools
        ):
            raise ToolInvocationDenied("tool is not authorized for this task token")

    def _authorize_token_budgets(self, claims: TaskTokenClaims) -> None:
        token_limits = ToolBudgetLimits.from_claims(dict(claims.budgets))
        if token_limits != self.budget_ledger.limits:
            raise ToolInvocationDenied("task token budget scope does not match task budget ledger")

    def _prepare_artifact_write(self, request: Mapping[str, Any]) -> tuple[Mapping[str, Any], bytes]:
        logical_name = _required_string(request, "logical_name")
        media_type = _required_string(request, "media_type")
        content = _required_string(request, "content").encode("utf-8")
        if logical_name not in self.context.contract.output_names:
            raise ToolInvocationDenied("artifact is outside the task output contract")
        return self.writer.preview(logical_name=logical_name, media_type=media_type, content=content), content

    def _prepare_submit(self) -> Mapping[str, Any]:
        if self._submitted:
            return {"status": "submitted", "idempotent": True}
        missing = sorted(name for name in self.context.contract.output_names if not self.writer.exists(name))
        if missing:
            raise ToolInvocationDenied("required task artifacts are missing")
        return {"status": "submitted", "task_id": self.context.task_id}

    def _audit_accept(
        self,
        claims: TaskTokenClaims,
        tool_name: str,
        operation_id: str,
        usage: BudgetUsage,
    ) -> None:
        self.audit_ledger.append(
            tool_name=tool_name,
            outcome="accepted",
            operation_id=operation_id,
            detail={
                **token_audit_fields(claims),
                "usage": {
                    "calls": usage.calls,
                    "read_bytes": usage.read_bytes,
                    "result_bytes": usage.result_bytes,
                    "write_bytes": usage.write_bytes,
                },
            },
        )

    def _dispatch(
        self,
        claims: TaskTokenClaims,
        tool_name: str,
        request: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], BudgetUsage]:
        if tool_name == "nexus_experiment_get_contract":
            return get_contract(self.context, claims), BudgetUsage(calls=1)
        if tool_name == "nexus_experiment_list_evidence":
            result = {"items": list_evidence(self.context, claims)}
            return result, BudgetUsage(calls=1)
        if tool_name == "nexus_experiment_search_evidence":
            result = {"items": search_evidence(self.context, claims, query=_required_string(request, "query"))}
            scanned_bytes = sum(
                content.size_bytes
                for content in self.context.snapshot.contents
                for item in self.context.snapshot.items
                if item.id == content.item_id and item.source_role in claims.source_roles
            )
            return result, BudgetUsage(calls=1, read_bytes=scanned_bytes)
        if tool_name == "nexus_experiment_read_evidence":
            result = read_evidence(
                self.context,
                claims,
                evidence_id=_required_string(request, "evidence_id"),
                max_bytes=_positive_int(request, "max_bytes"),
            )
            return result, BudgetUsage(calls=1, read_bytes=int(result["returned_bytes"]))
        if tool_name == "nexus_experiment_traverse_relationships":
            result = {"relationships": traverse_relationships(self.context, claims, subject_id=_required_string(request, "subject_id"))}
            return result, BudgetUsage(calls=1)
        if tool_name == "nexus_experiment_write_artifact":
            raise ToolInvocationDenied("artifact writes must be prepared before dispatch")
        if tool_name == "nexus_experiment_submit_task":
            raise ToolInvocationDenied("task submission must be prepared before dispatch")
        raise ToolInvocationDenied("unsupported Nexus experiment tool")


def _required_string(request: Mapping[str, Any], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _positive_int(request: Mapping[str, Any], key: str) -> int:
    value = request.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{key} must be a positive integer")
    return value


def _json_bytes(value: Mapping[str, Any]) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _request_digest(request: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
