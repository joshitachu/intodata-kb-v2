"""Validated, file-backed connector type catalog."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import ConnectorProviderDefinition
from .providers import ConnectorProvider, ConnectorProviderRegistry

CONNECTOR_CATALOG_PATH = Path(__file__).parent / "config" / "providers.json"


class ConnectorCatalogError(ValueError):
    """Raised when trusted connector catalog data is invalid or unsafe."""


def load_connector_catalog(path: Path = CONNECTOR_CATALOG_PATH) -> tuple[ConnectorProviderDefinition, ...]:
    if path.suffix.lower() != ".json" or not path.is_file() or path.is_symlink():
        raise ConnectorCatalogError("connector catalog must be a regular JSON file")
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise TypeError("connector catalog must contain a JSON array")
        definitions = tuple(
            ConnectorProviderDefinition.model_validate_json(json.dumps(item), strict=True)
            for item in payload
        )
    except (OSError, TypeError, ValidationError, json.JSONDecodeError) as exc:
        raise ConnectorCatalogError(f"invalid connector catalog: {exc}") from exc
    keys = [definition.provider_key for definition in definitions]
    if not definitions or len(keys) != len(set(keys)):
        raise ConnectorCatalogError("connector provider keys must be present and unique")
    return definitions


def default_connector_provider_registry() -> ConnectorProviderRegistry:
    registry = ConnectorProviderRegistry()
    for definition in load_connector_catalog():
        registry.register(definition, _load_adapter(definition.adapter_ref))
    return registry


def _load_adapter(adapter_ref: str) -> ConnectorProvider:
    module_name, separator, class_name = adapter_ref.rpartition(".")
    if not separator or not module_name.startswith("src.harness.connectors."):
        raise ConnectorCatalogError("connector adapter must be inside src.harness.connectors")
    try:
        adapter_type = getattr(importlib.import_module(module_name), class_name)
        adapter = adapter_type()
    except (AttributeError, ImportError, TypeError) as exc:
        raise ConnectorCatalogError(f"could not load connector adapter {adapter_ref}") from exc
    for method in ("validate_config", "health", "read"):
        if not callable(getattr(adapter, method, None)):
            raise ConnectorCatalogError(f"connector adapter {adapter_ref} is missing {method}")
    return adapter
