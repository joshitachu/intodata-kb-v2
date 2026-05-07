"""Provider abstraction for doc generation.

DocGenerationProvider is the ABC every backend must implement.
DocGenContext holds the preprocessed state the coordinator builds before
calling into the provider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Awaitable


@dataclass
class AgentResult:
    """Normalised result returned by every provider."""
    success: bool
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    duration_ms: int | None = None
    duration_api_ms: int | None = None
    num_turns: int | None = None
    session_id: str | None = None
    model_name: str = "claude"
    write_model_name: str | None = None
    template_snapshot: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Return a plain dict compatible with the legacy result format."""
        return {
            "success": self.success,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "duration_ms": self.duration_ms,
            "duration_api_ms": self.duration_api_ms,
            "num_turns": self.num_turns,
            "session_id": self.session_id,
            "model_name": self.model_name,
            "write_model_name": self.write_model_name,
            "template_snapshot": self.template_snapshot,
        }


@dataclass
class DocGenContext:
    """All preprocessed state the provider needs to run doc generation.

    Built by ClaudeService before the provider is invoked so that every
    provider gets the same structured input regardless of its backend.
    """
    project_id: str
    staging_dir: Path
    staging_abs: str
    logs_abs: str
    preprocess_groups: list[str]
    total_jobs: int
    total_groups: int
    effective_model: str
    write_model: str
    template_snapshot: dict
    platform: str
    analysis_guide_file: str
    resume_state: dict | None = None


# Type alias for the log callback passed to providers
LogCallback = Callable[[str, str], Awaitable[None]]


class DocGenerationProvider(ABC):
    """Abstract base class for doc generation backends."""

    @abstractmethod
    async def run(
        self,
        ctx: DocGenContext,
        log_cb: LogCallback,
    ) -> AgentResult:
        """Execute doc generation for the given context.

        Parameters
        ----------
        ctx:    preprocessed state (groups, staging paths, model config, etc.)
        log_cb: async callback(project_id, message, level) for streaming logs
        """
        ...
