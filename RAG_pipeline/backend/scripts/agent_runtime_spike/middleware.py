"""Mandatory interception and safe event identity for every spike tool call."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage

from .models import SpikeEvent, SpikeRunIdentity


class EventLedger:
    def __init__(self, identity: SpikeRunIdentity, *, sequence_start: int = 0) -> None:
        self.identity = identity
        self.events: list[SpikeEvent] = []
        self.sequence_start = sequence_start

    def append(self, kind: str, **safe_payload: Any) -> SpikeEvent:
        sequence = self.sequence_start + len(self.events) + 1
        event = SpikeEvent(
            event_id=f"{self.identity.agent_run_id}:evt:{sequence:06d}",
            run_id=self.identity.agent_run_id,
            sequence=sequence,
            kind=kind,
            safe_payload=safe_payload,
        )
        self.events.append(event)
        return event


class MandatorySpikeMiddleware(AgentMiddleware):
    """Records complete proposals and refuses an unobserved tool execution."""

    def __init__(self, identity: SpikeRunIdentity, ledger: EventLedger) -> None:
        self.identity = identity
        self.ledger = ledger
        self.proposed_call_ids: list[str] = []
        self.intercepted_call_ids: list[str] = []

    async def awrap_model_call(self, request, handler):  # noqa: ANN001, ANN201
        choice = "auto" if request.tools else "none"
        return await handler(request.override(tool_choice=choice))

    async def aafter_model(self, state, runtime):  # noqa: ANN001, ANN201
        del runtime
        if not state.get("messages"):
            return None
        message = state["messages"][-1]
        if not isinstance(message, AIMessage):
            return None
        for call in message.tool_calls:
            call_id = str(call.get("id") or "")
            if not call_id or call_id in self.proposed_call_ids:
                continue
            self.proposed_call_ids.append(call_id)
            self.ledger.append("tool.proposed", call_id=call_id, semantic_name=call["name"])
        return None

    async def awrap_tool_call(self, request, handler):  # noqa: ANN001, ANN201
        call_id = str(request.tool_call.get("id") or "")
        if call_id and call_id not in self.proposed_call_ids:
            for message in request.state.get("messages", []):
                if not isinstance(message, AIMessage):
                    continue
                if any(str(call.get("id") or "") == call_id for call in message.tool_calls):
                    self.proposed_call_ids.append(call_id)
                    self.ledger.append(
                        "tool.proposal_restored",
                        call_id=call_id,
                        semantic_name=request.tool_call["name"],
                    )
                    break
        if not call_id or call_id not in self.proposed_call_ids:
            return ToolMessage(
                content='{"status":"denied","code":"middleware_proposal_missing"}',
                tool_call_id=call_id or "missing-call-id",
                status="error",
            )
        self.intercepted_call_ids.append(call_id)
        self.ledger.append(
            "tool.intercepted",
            call_id=call_id,
            semantic_name=request.tool_call["name"],
            policy="allowed_harmless_fixture_read",
        )
        result = await handler(request)
        self.ledger.append(
            "tool.completed",
            call_id=call_id,
            semantic_name=request.tool_call["name"],
            status="succeeded" if getattr(result, "status", "success") != "error" else "failed",
        )
        return result
