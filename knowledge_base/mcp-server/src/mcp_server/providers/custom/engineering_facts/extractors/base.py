"""Extractor strategy interface for EngineeringFacts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from mcp_server.providers.custom.models import FactsManifest
from mcp_server.providers.custom.engineering_facts.models import EngineeringFacts


class EngineeringFactExtractor(ABC):
    """Common interface for platform-specific EngineeringFacts extraction."""

    platform: str = "generic"

    @abstractmethod
    def extract(self, project_id: str, scan: dict[str, Any], facts_manifest: FactsManifest) -> EngineeringFacts:
        """Adapt native scan/source data into the shared EngineeringFacts model."""
