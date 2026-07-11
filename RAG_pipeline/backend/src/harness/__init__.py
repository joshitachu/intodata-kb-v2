"""Generic Nexus harness contracts and persistence boundary."""

from __future__ import annotations

ProjectScope = list[str] | None


class HarnessRunNotFoundError(Exception):
    """Raised when no in-scope workflow run exists."""


class UnknownCapabilityError(Exception):
    """Raised when a requested capability is not registered."""


class CapabilityDisabledError(Exception):
    """Raised when a known capability is disabled or unavailable."""


class CapabilityExecutionDeniedError(Exception):
    """Raised when a capability execution request violates Nexus policy."""


from src.harness.registry import CapabilityRegistry, default_capability_registry  # noqa: E402
from src.harness.repository import HarnessWorkflowRepository  # noqa: E402
from src.harness.service import HarnessWorkflowService  # noqa: E402
from src.harness.executor import HarnessCapabilityExecutor  # noqa: E402
from src.harness.manifest import build_runtime_capability_manifest  # noqa: E402

__all__ = [
    "CapabilityDisabledError",
    "CapabilityExecutionDeniedError",
    "CapabilityRegistry",
    "HarnessRunNotFoundError",
    "HarnessCapabilityExecutor",
    "HarnessWorkflowRepository",
    "HarnessWorkflowService",
    "ProjectScope",
    "UnknownCapabilityError",
    "build_runtime_capability_manifest",
    "default_capability_registry",
]
