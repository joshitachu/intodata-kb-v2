"""Group-to-doc provenance index built from the persisted doc-pack manifest.

Implements the Repository pattern: `GroupDocDependency.load()` reads
`docs/_rag_support/_doc_pack_manifest.json` (written by Phase 2.8 and carried
into docs/ by `build_rag_support_package`) and exposes a bi-directional
group<->doc index plus cross-cutting detection.

Missing or corrupt manifests return None so callers fall back to full regen
(that fallback is Slice 3's responsibility, not this module).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# The manifest filename copied from _analysis/ into _rag_support/ by
# build_rag_support_package(). Slice 3 locates it at:
#   output/{project_id}/docs/_rag_support/_doc_pack_manifest.json
DOC_PACK_MANIFEST_FILENAME = "_doc_pack_manifest.json"

# Obligation category that marks a doc as consuming the all-groups canonical
# unit inventory. Detected from manifest data -- never hardcoded doc names.
_CANONICAL_INVENTORY_CATEGORY = "canonical_inventory"


class GroupDocDependency:
    """Bi-directional group<->doc provenance index.

    Build with `GroupDocDependency.load(manifest_path)`.  All methods return
    empty collections when the index has no data for the queried key; they
    never raise on missing keys.
    """

    def __init__(
        self,
        group_to_docs: dict[str, set[str]],
        doc_to_groups: dict[str, set[str]],
        cross_cutting: set[str],
    ) -> None:
        self._group_to_docs = group_to_docs
        self._doc_to_groups = doc_to_groups
        self._cross_cutting = cross_cutting

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def docs_for_group(self, group: str) -> set[str]:
        """Docs whose evidence pack drew from the given group analysis."""
        return set(self._group_to_docs.get(group, set()))

    def groups_for_doc(self, doc: str) -> set[str]:
        """Groups whose analysis contributed to the given doc."""
        return set(self._doc_to_groups.get(doc, set()))

    def cross_cutting_docs(self) -> set[str]:
        """Docs that consume the all-groups canonical unit inventory.

        These must be regenerated whenever any group changes, regardless of
        which specific groups were touched.
        """
        return set(self._cross_cutting)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, manifest_path: Path) -> "GroupDocDependency | None":
        """Load and invert the persisted doc-pack manifest.

        Returns None when the manifest is absent or unparseable so callers
        can fall back to full regen gracefully.
        """
        if not manifest_path.is_file():
            return None

        try:
            raw = manifest_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            logger.warning(
                "GroupDocDependency: could not parse manifest at %s",
                manifest_path,
                exc_info=True,
            )
            return None

        doc_packs = data.get("doc_packs")
        if not isinstance(doc_packs, dict):
            logger.warning(
                "GroupDocDependency: manifest at %s has no 'doc_packs' dict",
                manifest_path,
            )
            return cls({}, {}, set())

        return cls._build(doc_packs)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _build(cls, doc_packs: dict[str, object]) -> "GroupDocDependency":
        group_to_docs: dict[str, set[str]] = {}
        doc_to_groups: dict[str, set[str]] = {}
        cross_cutting: set[str] = set()

        for doc_name, pack in doc_packs.items():
            if not isinstance(pack, dict):
                continue

            # Invert included: {group_file.md: [sections]} -> group->doc index.
            included = pack.get("included")
            if isinstance(included, dict):
                for group_file, _sections in included.items():
                    # Strip .md suffix so the index key matches the group name
                    # used everywhere else (e.g. "scripts", not "scripts.md").
                    group = group_file[:-3] if group_file.endswith(".md") else group_file
                    group_to_docs.setdefault(group, set()).add(doc_name)
                    doc_to_groups.setdefault(doc_name, set()).add(group)

            # Detect cross-cutting: doc has the canonical_inventory obligation
            # in its included_obligations (all-groups unit inventory consumers).
            obligations = pack.get("included_obligations")
            if isinstance(obligations, list):
                for obligation in obligations:
                    if (
                        isinstance(obligation, dict)
                        and obligation.get("category") == _CANONICAL_INVENTORY_CATEGORY
                    ):
                        cross_cutting.add(doc_name)
                        break

        return cls(group_to_docs, doc_to_groups, cross_cutting)
