"""Platform plugin registry — load plugins by name."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from intodoc.platforms.base import PlatformPlugin

# Map of platform name → module path + class name
_PLATFORM_MAP: dict[str, tuple[str, str]] = {
    "talend": ("intodoc.platforms.talend", "TalendPlugin"),
    "generic": ("intodoc.platforms.generic", "GenericPlugin"),
    "workato": ("intodoc.platforms.workato", "WorkatoPlugin"),
}


def get_platform(name: str) -> PlatformPlugin:
    """Load and return a platform plugin by name.

    Raises:
        ValueError: If the platform name is not registered.
    """
    name = name.lower().strip()
    if name not in _PLATFORM_MAP:
        available = ", ".join(sorted(_PLATFORM_MAP.keys()))
        raise ValueError(f"Unknown platform '{name}'. Available: {available}")

    module_path, class_name = _PLATFORM_MAP[name]
    module = import_module(module_path)
    cls = getattr(module, class_name)
    return cls()


def list_platforms() -> list[str]:
    """Return list of available platform names."""
    return sorted(_PLATFORM_MAP.keys())
