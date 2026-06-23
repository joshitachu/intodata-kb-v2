"""Blocking correctness gates for Git-triggered doc reingestion.

Two deterministic, model-independent gates run against the freshly generated
staging tree BEFORE the `docs_staging/ -> docs/` atomic swap and before the
ingested-commit SHA is advanced:

1. Missing-unit gate (LOOSE) — every canonical unit owned by a changed/added
   group must appear (whole-word, case-insensitive name match) in at least one
   generated doc. No provenance map exists in this slice, so coverage is checked
   against *all* published docs rather than a unit's mapped doc.
2. Empty-analysis gate — every required (changed) group's Phase 2 analysis must
   be substantive: present, above the THIN floor, and complete per
   ``group_analysis_is_complete``.

The gate is a pure evaluator (no LLM, no I/O beyond reading the staging tree) so
its verdict is identical across all configured models. Depth / polish /
provenance / enterprise gates remain advisory and are untouched here.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from pydantic import BaseModel, Field

from mcp_server.providers.custom.models import FactsManifest
from mcp_server.providers.custom.pipeline.evidence_quality_gate import EvidenceQualityGate
from mcp_server.providers.custom.validators import group_analysis_is_complete
from mcp_server.services.group_doc_dependency import GroupDocDependency
from mcp_server.services.rag_support_package import (
    PHASE2_ANALYSIS_SUBDIR,
    RAG_SUPPORT_DIRNAME,
)

logger = logging.getLogger(__name__)

THIN_FLOOR_BYTES: int = EvidenceQualityGate.THIN_THRESHOLD_BYTES


class GateOutcome(BaseModel):
    """Result of evaluating the blocking correctness gates."""

    passed: bool
    evaluated: bool
    missing_units: list[str] = Field(default_factory=list)
    empty_analysis_groups: list[str] = Field(default_factory=list)
    failing_groups: list[str] = Field(default_factory=list)
    reason: str | None = None

    def to_summary(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "evaluated": self.evaluated,
            "missing_units": list(self.missing_units),
            "empty_analysis_groups": list(self.empty_analysis_groups),
            "failing_groups": list(self.failing_groups),
            "reason": self.reason,
        }


class CorrectnessGate:
    """Evaluate the two blocking correctness gates against a staging tree.

    Scope is restricted to the groups that changed in the triggering push. An
    empty ``changed_groups`` set (first push or unresolved diff) makes the gate a
    no-op pass: this slice does not gate the onboarding full-baseline path.
    """

    def __init__(
        self,
        *,
        changed_groups: set[str],
        require_sections: bool = True,
        dependency: GroupDocDependency | None = None,
    ) -> None:
        self._changed_groups = {g for g in changed_groups if g}
        self._require_sections = require_sections
        # When a provenance map is present (Slice 3), a changed unit must appear
        # in its MAPPED doc(s), not just SOME doc. Without a map, fall back to the
        # loose "unit in some published doc" check (Slice 1 behavior).
        self._dependency = dependency

    def evaluate(self, staging_dir: Path, facts: FactsManifest) -> GateOutcome:
        if not self._changed_groups:
            return GateOutcome(
                passed=True,
                evaluated=False,
                reason="no changed groups; gate not applicable",
            )

        missing_units = self._missing_units(staging_dir, facts)
        empty_groups = self._empty_analysis_groups(staging_dir, facts.platform)

        failing_groups = sorted(
            set(empty_groups) | self._groups_for_units(facts, missing_units)
        )
        passed = not missing_units and not empty_groups
        reason: str | None = None
        if not passed:
            parts: list[str] = []
            if missing_units:
                parts.append(
                    f"{len(missing_units)} changed unit(s) absent from generated docs: "
                    + ", ".join(missing_units[:5])
                )
            if empty_groups:
                parts.append(
                    f"{len(empty_groups)} changed group(s) with empty/placeholder analysis: "
                    + ", ".join(empty_groups[:5])
                )
            reason = "; ".join(parts)

        return GateOutcome(
            passed=passed,
            evaluated=True,
            missing_units=missing_units,
            empty_analysis_groups=empty_groups,
            failing_groups=failing_groups,
            reason=reason,
        )

    def _missing_units(self, staging_dir: Path, facts: FactsManifest) -> list[str]:
        changed_units = [u for u in facts.units if u.group in self._changed_groups]
        if not changed_units:
            return []
        if self._dependency is None:
            # Loose fallback: a changed unit is covered if it appears in ANY doc.
            published_text = self._published_doc_text(staging_dir)
            missing = [
                unit.name
                for unit in changed_units
                if not self._name_present(unit.name, published_text)
            ]
            return sorted(set(missing))

        # Tightened: a changed unit must appear in one of its MAPPED docs
        # (the docs its group feeds, plus the always-regenerated cross-cutting
        # set). A unit appearing only in some unrelated doc is NOT credited.
        cross_cutting = self._dependency.cross_cutting_docs()
        doc_text_cache: dict[str, str] = {}
        missing: list[str] = []
        for unit in changed_units:
            mapped = self._dependency.docs_for_group(unit.group) | cross_cutting
            mapped_text = self._mapped_doc_text(staging_dir, mapped, doc_text_cache)
            if not self._name_present(unit.name, mapped_text):
                missing.append(unit.name)
        return sorted(set(missing))

    @staticmethod
    def _name_present(name: str, text: str) -> bool:
        return bool(re.search(r"\b" + re.escape(name) + r"\b", text, re.IGNORECASE))

    @staticmethod
    def _mapped_doc_text(
        staging_dir: Path, doc_names: set[str], cache: dict[str, str]
    ) -> str:
        parts: list[str] = []
        for doc in sorted(doc_names):
            if doc not in cache:
                path = staging_dir / f"{doc}.md"
                cache[doc] = (
                    path.read_text(encoding="utf-8", errors="replace")
                    if path.is_file()
                    else ""
                )
            parts.append(cache[doc])
        return "\n".join(parts)

    def _empty_analysis_groups(self, staging_dir: Path, platform: str) -> list[str]:
        empty: list[str] = []
        for group in sorted(self._changed_groups):
            path = self._analysis_path(staging_dir, group)
            if path is None:
                empty.append(group)
                continue
            if path.stat().st_size <= THIN_FLOOR_BYTES:
                empty.append(group)
                continue
            if not group_analysis_is_complete(
                path, platform, require_sections=self._require_sections
            ):
                empty.append(group)
        return empty

    @staticmethod
    def _analysis_path(staging_dir: Path, group: str) -> Path | None:
        """Locate a group's Phase 2 analysis in the staging tree.

        Phase 4 cleanup packages the analyses into ``_rag_support/`` and deletes
        ``_analysis/`` before the swap, so the published copy lives under
        ``_rag_support/phase2_analysis/``. Fall back to ``_analysis/`` for direct
        phase invocations where cleanup has not yet run.
        """
        packaged = (
            staging_dir / RAG_SUPPORT_DIRNAME / PHASE2_ANALYSIS_SUBDIR / f"{group}.md"
        )
        if packaged.is_file():
            return packaged
        legacy = staging_dir / "_analysis" / f"{group}.md"
        if legacy.is_file():
            return legacy
        return None

    @staticmethod
    def _groups_for_units(facts: FactsManifest, unit_names: list[str]) -> set[str]:
        wanted = set(unit_names)
        return {unit.group for unit in facts.units if unit.name in wanted and unit.group}

    @staticmethod
    def _published_doc_text(staging_dir: Path) -> str:
        return "\n".join(
            p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(staging_dir.glob("*.md"))
        )


def load_facts_manifest(staging_dir: Path) -> FactsManifest | None:
    """Load the facts manifest the harness writes into the staging tree."""
    path = staging_dir / "facts_manifest.json"
    if not path.is_file():
        return None
    try:
        return FactsManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        logger.warning("Failed to load facts manifest at %s: %s", path, exc, exc_info=True)
        return None
