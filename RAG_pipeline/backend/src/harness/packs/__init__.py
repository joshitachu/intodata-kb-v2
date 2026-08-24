"""Nexus orchestration pack definitions and activation catalog."""

from pathlib import Path

from src.harness.packs.catalog import PackCatalog, PackCatalogError, manifest_hash
from src.harness.packs.loader import PackLoadError, load_pack_directory, load_pack_file
from src.harness.packs.models import (
    PackActivation,
    PackManifest,
    PackReferenceSets,
    PackSnapshot,
    PackRecommendation,
    ProjectPackActivation,
    PackType,
    PackView,
    ResolvedPackSet,
)

FIXTURE_PACK_DIRECTORY = Path(__file__).with_name("fixtures")

__all__ = [
    "FIXTURE_PACK_DIRECTORY",
    "PackActivation",
    "PackCatalog",
    "PackCatalogError",
    "PackLoadError",
    "PackManifest",
    "PackReferenceSets",
    "PackSnapshot",
    "PackRecommendation",
    "ProjectPackActivation",
    "PackType",
    "PackView",
    "ResolvedPackSet",
    "load_pack_directory",
    "load_pack_file",
    "manifest_hash",
]
