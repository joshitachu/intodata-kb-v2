"""Model metadata registry for dashboard discovery."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ModelDescriptor:
    id: str
    provider: str
    label: str
    supports_analysis: bool
    supports_writing: bool
    is_default_analysis: bool = False
    is_default_investigation: bool = False
    is_default_writing: bool = False
    is_local: bool = False
    # Whether the running instance has what it needs to use this model (local, or
    # a credential env var is set). Cloud models with no key show unavailable.
    available: bool = True
    unavailable_reason: str | None = None
    # Whether the RAG chat surface may offer this model (excludes CLI runtimes).
    rag_capable: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def list_models(
    *,
    default_analysis_model: str,
    default_investigation_model: str | None = None,
    default_writing_model: str,
) -> list[ModelDescriptor]:
    from mcp_server.model_strategies import list_model_descriptors
    from mcp_server.model_strategies.catalogue import is_model_available, unavailable_reason

    models: list[ModelDescriptor] = []
    for descriptor in list_model_descriptors():
        models.append(
            ModelDescriptor(
                id=descriptor.id,
                provider=descriptor.provider,
                label=descriptor.label,
                supports_analysis=descriptor.supports_analysis,
                supports_writing=descriptor.supports_writing,
                is_default_analysis=descriptor.id == default_analysis_model,
                is_default_investigation=descriptor.id == default_investigation_model,
                is_default_writing=descriptor.id == default_writing_model,
                is_local=descriptor.is_local,
                available=is_model_available(descriptor),
                unavailable_reason=unavailable_reason(descriptor),
                rag_capable=descriptor.rag_capable,
            )
        )
    return models
