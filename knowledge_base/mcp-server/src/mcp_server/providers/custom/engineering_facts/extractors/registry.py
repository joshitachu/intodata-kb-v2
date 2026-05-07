"""Registry for EngineeringFacts extractor strategies."""

from __future__ import annotations

from mcp_server.providers.custom.engineering_facts.extractors.base import EngineeringFactExtractor


class ExtractorRegistry:
    """Maps platform names to extractor strategies with generic fallback."""

    def __init__(self) -> None:
        self._extractors: dict[str, EngineeringFactExtractor] = {}

    def register(self, extractor: EngineeringFactExtractor) -> None:
        self._extractors[extractor.platform.lower()] = extractor

    def get(self, platform: str | None) -> EngineeringFactExtractor:
        key = (platform or "generic").lower()
        return self._extractors.get(key) or self._extractors["generic"]


def default_registry() -> ExtractorRegistry:
    from mcp_server.providers.custom.engineering_facts.extractors.generic import GenericFactExtractor
    from mcp_server.providers.custom.engineering_facts.extractors.talend import TalendFactExtractor
    from mcp_server.providers.custom.engineering_facts.extractors.workato import WorkatoFactExtractor

    registry = ExtractorRegistry()
    registry.register(GenericFactExtractor())
    registry.register(TalendFactExtractor())
    registry.register(WorkatoFactExtractor())
    return registry
