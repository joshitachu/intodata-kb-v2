"""Registry for self-service runtime source providers."""

from __future__ import annotations

from src.runtime.providers.base import RuntimeSourceProvider
from src.runtime.providers.elastic import provider as elastic_provider
from src.runtime.providers.mock import provider as mock_provider
from src.runtime.providers.talend_tmc import provider as tmc_provider

_PROVIDERS: dict[str, RuntimeSourceProvider] = {
    mock_provider.provider_key(): mock_provider,
    tmc_provider.provider_key(): tmc_provider,
    elastic_provider.provider_key(): elastic_provider,
}


def all_source_providers() -> list[RuntimeSourceProvider]:
    return list(_PROVIDERS.values())


def get_source_provider(provider_key: str) -> RuntimeSourceProvider | None:
    return _PROVIDERS.get(provider_key)
