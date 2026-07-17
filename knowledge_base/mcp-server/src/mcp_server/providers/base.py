"""Provider abstraction for doc generation.

DocGenerationProvider is the ABC every backend must implement.
DocGenContext holds the preprocessed state the coordinator builds before
calling into the provider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from mcp_server.schemas import NexusPresentation, NexusPrivateAnalysisPack


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
    required_docs: list[str] = field(default_factory=list)
    has_warnings: bool = False

    def to_dict(self) -> dict:
        """Return a plain dict compatible with the legacy result format."""
        return {
            "success": self.success,
            "error": self.error,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "duration_ms": self.duration_ms,
            "duration_api_ms": self.duration_api_ms,
            "num_turns": self.num_turns,
            "session_id": self.session_id,
            "model_name": self.model_name,
            "write_model_name": self.write_model_name,
            "required_docs": self.required_docs,
            "has_warnings": self.has_warnings,
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
    platform: str
    analysis_guide_file: str
    enterprise_mode: bool = True
    resume_state: dict | None = None
    # Doc-targeted Phase 3 (Slice 3). When set, only these document names
    # (no ``.md`` suffix) are regenerated; all other baseline docs are preserved
    # byte-stable. ``None`` => regenerate the full required-doc set.
    scoped_docs: set[str] | None = None
    # Governed Style Pack output. Only Phase 3 prompt rendering may consume it.
    nexus_presentation: "NexusPresentation | None" = None
    # Verified V3 declarations may only be consumed by Phase 3 document rendering.
    nexus_private_analysis_packs: tuple["NexusPrivateAnalysisPack", ...] = ()


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
