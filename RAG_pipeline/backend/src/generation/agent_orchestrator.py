"""Constrained read-only agent tracing for conversational RAG.

This module does not grant the model arbitrary tool access. It provides a
small, backend-owned registry of allowed read-only tool steps so the existing
RAG pipeline can expose explicit orchestration decisions to logs and SSE.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

MAX_AGENT_STEPS = 3


class AgentStepStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


class AgentTool(str, Enum):
    RECALL_MEMORY = "recall_memory"
    REUSE_PREVIOUS_SOURCES = "reuse_previous_sources"
    FETCH_METADATA = "fetch_metadata"
    SUMMARIZE_DOCUMENT = "summarize_document"
    SEARCH_DOCUMENTS = "search_documents"
    FINAL_ANSWER = "final_answer"


TOOL_LABELS: dict[AgentTool, str] = {
    AgentTool.RECALL_MEMORY: "Recalling memory",
    AgentTool.REUSE_PREVIOUS_SOURCES: "Checking previous sources",
    AgentTool.FETCH_METADATA: "Fetching metadata",
    AgentTool.SUMMARIZE_DOCUMENT: "Summarizing document",
    AgentTool.SEARCH_DOCUMENTS: "Searching documents",
    AgentTool.FINAL_ANSWER: "Composing answer",
}


@dataclass
class AgentStep:
    id: str
    label: str
    status: str

    def model_dump(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
        }


class AgentTrace:
    """Bounded list of read-only agent tool steps."""

    def __init__(self, max_steps: int = MAX_AGENT_STEPS) -> None:
        self.max_steps = max_steps
        self.steps: list[AgentStep] = []

    def add(
        self,
        tool: AgentTool,
        status: AgentStepStatus = AgentStepStatus.COMPLETED,
        label: str | None = None,
    ) -> None:
        if len(self.steps) >= self.max_steps:
            return
        self.steps.append(
            AgentStep(
                id=tool.value,
                label=label or TOOL_LABELS[tool],
                status=status.value,
            )
        )

    def complete(self, tool: AgentTool, label: str | None = None) -> None:
        self.add(tool, AgentStepStatus.COMPLETED, label=label)

    def skipped(self, tool: AgentTool, label: str | None = None) -> None:
        self.add(tool, AgentStepStatus.SKIPPED, label=label)

