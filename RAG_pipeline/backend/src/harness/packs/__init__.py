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

from src.harness.packs.project_service import (  # noqa: E402
    DEFAULT_WORKFLOW_KEY,
    PackServiceError,
    ProjectPackService,
)

__all__ = [
    "FIXTURE_PACK_DIRECTORY",
    "PackActivation",
    "PackCatalog",
    "PackCatalogError",
    "PackLoadError",
    "PackServiceError",
    "PackManifest",
    "PackReferenceSets",
    "PackSnapshot",
    "PackRecommendation",
    "ProjectPackActivation",
    "PackType",
    "PackView",
    "ResolvedPackSet",
    "ProjectPackService",
    "DEFAULT_WORKFLOW_KEY",
    "load_pack_directory",
    "load_pack_file",
    "manifest_hash",
]
