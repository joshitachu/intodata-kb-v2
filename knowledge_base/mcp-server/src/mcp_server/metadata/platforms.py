"""Platform metadata registry.

This is UI/API metadata only. Scanner plugin loading remains in
``intodoc.platforms.registry``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PlatformDescriptor:
    id: str
    label: str
    unit_label: str
    unit_label_plural: str
    badge_color: str
    accent_color: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


_PLATFORMS: tuple[PlatformDescriptor, ...] = (
    PlatformDescriptor(
        id="talend",
        label="Talend",
        unit_label="Job",
        unit_label_plural="Jobs",
        badge_color="#4285F4",
        accent_color="#4285F4",
    ),
    PlatformDescriptor(
        id="workato",
        label="Workato",
        unit_label="Recipe",
        unit_label_plural="Recipes",
        badge_color="#F97316",
        accent_color="#F97316",
    ),
    PlatformDescriptor(
        id="lobster",
        label="Lobster",
        unit_label="Profile",
        unit_label_plural="Profiles",
        badge_color="#10B981",
        accent_color="#10B981",
    ),
    PlatformDescriptor(
        id="generic",
        label="Generic",
        unit_label="Component",
        unit_label_plural="Components",
        badge_color="#64748B",
        accent_color="#64748B",
    ),
)


def list_platforms() -> list[PlatformDescriptor]:
    return list(_PLATFORMS)


def get_platform_descriptor(platform: str) -> PlatformDescriptor:
    normalized = platform.lower().strip()
    for descriptor in _PLATFORMS:
        if descriptor.id == normalized:
            return descriptor
    available = ", ".join(sorted(p.id for p in _PLATFORMS))
    raise ValueError(f"Unknown platform '{platform}'. Available: {available}")


def supported_platform_ids() -> frozenset[str]:
    return frozenset(p.id for p in _PLATFORMS)


def is_supported_platform(platform: str) -> bool:
    return platform in supported_platform_ids()
