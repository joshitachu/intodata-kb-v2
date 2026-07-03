"""Loader for the canonical model catalogue (``data/model_catalogue.json``).

The JSON file is the single source of truth for which models document generation
and RAG chat offer. Provider client construction stays in ``strategies.py`` (code);
only the descriptor DATA lives in the file, so ids/labels/capabilities can be
edited without touching code and both surfaces stay in sync.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from mcp_server.model_strategies.strategies import ModelDescriptorData


def _resolve_catalogue_path() -> Path:
    # Source layout: mcp-server/data/model_catalogue.json
    src_layout = Path(__file__).resolve().parent.parent.parent.parent / "data" / "model_catalogue.json"
    if src_layout.exists():
        return src_layout
    # Installed layout (Docker): WORKDIR /app, data/ copied there.
    app_data = Path("/app/data/model_catalogue.json")
    if app_data.exists():
        return app_data
    cwd_data = Path.cwd() / "data" / "model_catalogue.json"
    if cwd_data.exists():
        return cwd_data
    raise FileNotFoundError(
        f"model_catalogue.json not found at {src_layout}, {app_data}, or {cwd_data}"
    )


@lru_cache(maxsize=1)
def load_catalogue() -> tuple[ModelDescriptorData, ...]:
    """Load the real-provider descriptors from the JSON catalogue (cached)."""
    raw = json.loads(_resolve_catalogue_path().read_text(encoding="utf-8"))
    out: list[ModelDescriptorData] = []
    for entry in raw.get("models", []):
        out.append(
            ModelDescriptorData(
                id=entry["id"],
                provider=entry["provider"],
                label=entry["label"],
                supports_analysis=bool(entry.get("supports_analysis", True)),
                supports_writing=bool(entry.get("supports_writing", True)),
                is_local=bool(entry.get("is_local", False)),
                credential_env=tuple(entry.get("credential_env", ())),
                rag_capable=bool(entry.get("rag_capable", True)),
            )
        )
    return tuple(out)


def is_model_available(descriptor: ModelDescriptorData) -> bool:
    """A model is available when it is local, needs no credential, or at least
    one of its credential env vars is set in the environment."""
    if descriptor.is_local or not descriptor.credential_env:
        return True
    return any(os.environ.get(name) for name in descriptor.credential_env)


def unavailable_reason(descriptor: ModelDescriptorData) -> str | None:
    if is_model_available(descriptor):
        return None
    names = " or ".join(descriptor.credential_env)
    return f"No credentials configured (set {names})"
