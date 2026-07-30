"""OpenCode provider/model payload mapping kept outside runtime-neutral contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OpenCodeModelRoute:
    provider_id: str
    model_id: str

    def as_payload(self) -> dict[str, str]:
        return {"providerID": self.provider_id, "modelID": self.model_id}


def to_opencode_model_route(provider_id: str, model_id: str) -> OpenCodeModelRoute:
    """Map validated generic strings to the OpenCode 1.18.4 HTTP payload shape."""

    provider_id = provider_id.strip()
    model_id = model_id.strip()
    if not provider_id or not model_id:
        raise ValueError("provider_id and model_id must both be non-empty")
    return OpenCodeModelRoute(provider_id=provider_id, model_id=model_id)
