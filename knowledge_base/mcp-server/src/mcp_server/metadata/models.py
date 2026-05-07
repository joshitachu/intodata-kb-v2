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
    is_default_writing: bool = False
    is_local: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def list_models(
    *,
    default_analysis_model: str,
    default_writing_model: str,
) -> list[ModelDescriptor]:
    from mcp_server.model_strategies import list_model_descriptors

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
                is_default_writing=descriptor.id == default_writing_model,
                is_local=descriptor.is_local,
            )
        )
    return models
