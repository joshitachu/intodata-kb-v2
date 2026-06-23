"""Deterministic, model-independent reingestion routing (Strategy pattern).

Python computes the AUTHORITATIVE affected-doc set and picks one of four
strategies from structural facts alone:

    changed files -> changed_groups (resolve_changed_groups)
                  -> docs_for_group union cross_cutting_docs (provenance map)
                  + added/removed-unit (structural) detection (quick scan)

Decision table (evaluated in order):

    no doc-relevant change                                   -> skip
    manifest missing / groups unresolved / affected empty    -> full_regen
    affected set wider than the breadth threshold            -> full_regen
    structural change (unit/file add or remove)              -> scoped_regen
    in-file edits only, affected set within the patch bound  -> bounded_patch
    in-file edits only, affected set above the patch bound   -> scoped_regen

An optional, NON-BLOCKING LLM "expand-only" pass (``expand``) may grow the set
with extra docs, but can never shrink it, drop to skip, or change the strategy.
It is disabled by default so routing is fully deterministic. Any failure,
timeout, or invalid output is discarded and the run proceeds on Python's floor.

The Strategy classes encode the DECISION (which docs, which lane); the runtime
in ``auto_doc_update`` maps a ``RoutingDecision`` to the concrete action
(skip / bounded patch / scoped or full reingest). Keeping decision and action
separate keeps a single named pattern without a god-object.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from mcp_server.config import config
from mcp_server.services.git_reingestion import ChangedFile, resolve_changed_groups
from mcp_server.services.group_doc_dependency import (
    DOC_PACK_MANIFEST_FILENAME,
    GroupDocDependency,
)
from mcp_server.services.quick_change_scanner import QuickScanReport
from mcp_server.services.rag_support_package import RAG_SUPPORT_DIRNAME

logger = logging.getLogger(__name__)

RoutingStrategyName = Literal["skip", "bounded_patch", "scoped_regen", "full_regen"]


class RoutingDecision(BaseModel):
    """Authoritative routing decision computed by the deterministic floor.

    ``affected_docs`` is the Python floor (mapped docs + cross-cutting). It is
    never shrunk by the optional LLM pass; ``expanded_docs`` records any docs
    the LLM added on top. Doc names carry no ``.md`` suffix (matching the
    harness ``_required_docs`` and the provenance index keys).
    """

    strategy: RoutingStrategyName
    affected_docs: list[str] = Field(default_factory=list)
    changed_groups: list[str] = Field(default_factory=list)
    structural: bool = False
    manifest_present: bool = False
    expanded_docs: list[str] = Field(default_factory=list)
    reason: str = ""

    def to_impact_dict(self) -> dict[str, Any]:
        """Render in the legacy impact-plan shape consumed by git_diff_patcher.

        The patcher operates on ``.md`` filenames, so ``affected_docs`` is
        rendered with the suffix at this boundary (the internal floor stays
        suffix-free to match the harness ``_required_docs`` and the provenance
        index).
        """
        return {
            "strategy": self.strategy,
            "affected_docs": [
                d if d.endswith(".md") else f"{d}.md" for d in self.affected_docs
            ],
            "required_facts": [],
            "stale_claims": [],
            "confidence": 1.0,
            "reason": self.reason,
        }


class ReingestionStrategy(ABC):
    """A routing lane. Concrete strategies are thin, stateless selectors."""

    name: RoutingStrategyName

    @abstractmethod
    def build(
        self,
        *,
        affected_docs: list[str],
        changed_groups: list[str],
        structural: bool,
        manifest_present: bool,
        reason: str,
    ) -> RoutingDecision:
        ...


class _SimpleStrategy(ReingestionStrategy):
    def __init__(self, name: RoutingStrategyName) -> None:
        self.name = name

    def build(
        self,
        *,
        affected_docs: list[str],
        changed_groups: list[str],
        structural: bool,
        manifest_present: bool,
        reason: str,
    ) -> RoutingDecision:
        return RoutingDecision(
            strategy=self.name,
            affected_docs=affected_docs,
            changed_groups=changed_groups,
            structural=structural,
            manifest_present=manifest_present,
            reason=reason,
        )


class SkipStrategy(_SimpleStrategy):
    def __init__(self) -> None:
        super().__init__("skip")


class BoundedPatchStrategy(_SimpleStrategy):
    def __init__(self) -> None:
        super().__init__("bounded_patch")


class ScopedRegenStrategy(_SimpleStrategy):
    def __init__(self) -> None:
        super().__init__("scoped_regen")


class FullRegenStrategy(_SimpleStrategy):
    def __init__(self) -> None:
        super().__init__("full_regen")


class ReingestionRouter:
    """Compute the authoritative routing decision from structural facts."""

    def __init__(
        self,
        *,
        full_regen_doc_threshold: int | None = None,
        max_patch_docs: int | None = None,
    ) -> None:
        self._full_regen_threshold = (
            full_regen_doc_threshold
            if full_regen_doc_threshold is not None
            else config.reingestion_full_regen_doc_threshold
        )
        self._max_patch_docs = (
            max_patch_docs if max_patch_docs is not None else config.git_diff_patch_max_docs
        )
        self._skip = SkipStrategy()
        self._bounded = BoundedPatchStrategy()
        self._scoped = ScopedRegenStrategy()
        self._full = FullRegenStrategy()

    def decide(
        self,
        *,
        project_id: str,
        changed_files: list[ChangedFile],
        quick_scan: QuickScanReport,
        output_dir: Path,
        is_first_push: bool = False,
    ) -> RoutingDecision:
        if not quick_scan.doc_relevant_files:
            return self._skip.build(
                affected_docs=[],
                changed_groups=[],
                structural=False,
                manifest_present=False,
                reason="No doc-relevant files changed",
            )

        changed_groups = sorted(
            resolve_changed_groups(project_id, changed_files, is_first_push)
        )
        structural = bool(
            quick_scan.new_source_files or quick_scan.deleted_source_files
        )

        dependency = self._load_dependency(output_dir)
        manifest_present = dependency is not None
        affected = self._affected_docs(dependency, changed_groups)

        if dependency is None or not changed_groups or not affected:
            return self._full.build(
                affected_docs=affected,
                changed_groups=changed_groups,
                structural=structural,
                manifest_present=manifest_present,
                reason=self._full_regen_reason(dependency, changed_groups, affected),
            )

        if len(affected) > self._full_regen_threshold:
            return self._full.build(
                affected_docs=affected,
                changed_groups=changed_groups,
                structural=structural,
                manifest_present=manifest_present,
                reason=(
                    f"Affected doc set ({len(affected)}) exceeds full-regen "
                    f"threshold ({self._full_regen_threshold})"
                ),
            )

        if structural:
            return self._scoped.build(
                affected_docs=affected,
                changed_groups=changed_groups,
                structural=True,
                manifest_present=manifest_present,
                reason="Structural change (unit add/remove) -> scoped regeneration",
            )

        if len(affected) <= self._max_patch_docs:
            return self._bounded.build(
                affected_docs=affected,
                changed_groups=changed_groups,
                structural=False,
                manifest_present=manifest_present,
                reason="In-file edits within bounded-patch scope",
            )

        return self._scoped.build(
            affected_docs=affected,
            changed_groups=changed_groups,
            structural=False,
            manifest_present=manifest_present,
            reason=(
                f"In-file edits affect {len(affected)} docs (> patch max "
                f"{self._max_patch_docs}) -> scoped regeneration"
            ),
        )

    async def expand(
        self,
        decision: RoutingDecision,
        *,
        diff_chunks: list[dict[str, Any]],
        all_docs: list[str],
        doc_generation_provider: str | None,
    ) -> RoutingDecision:
        """Optional, NON-BLOCKING expand-only semantic pass.

        Returns ``decision`` unchanged when disabled or on any failure. When it
        adds in-catalog docs the affected set grows (never shrinks); a
        bounded_patch that outgrows the patch bound is upgraded to scoped_regen
        (safe over-inclusion). The strategy is never downgraded and skip is
        never reachable from here.
        """
        if not config.reingestion_llm_expand_enabled:
            return decision
        if decision.strategy == "skip":
            return decision

        try:
            additions = await self._invoke_expander(
                decision=decision,
                diff_chunks=diff_chunks,
                all_docs=all_docs,
                doc_generation_provider=doc_generation_provider,
            )
        except Exception:
            # Documented fail-open boundary: a flaky/garbage/unavailable model
            # must never block or alter routing. Log and proceed on the floor.
            logger.warning(
                "Reingestion expand-only pass failed; using deterministic floor",
                exc_info=True,
            )
            return decision

        catalog = set(all_docs)
        floor = set(decision.affected_docs)
        extra = sorted((set(additions) & catalog) - floor)
        if not extra:
            return decision

        merged = sorted(floor | set(extra))
        strategy: RoutingStrategyName = decision.strategy
        if strategy == "bounded_patch" and len(merged) > self._max_patch_docs:
            strategy = "scoped_regen"
        return decision.model_copy(
            update={
                "strategy": strategy,
                "affected_docs": merged,
                "expanded_docs": extra,
                "reason": (
                    f"{decision.reason}; LLM expand added {len(extra)} doc(s)"
                ),
            }
        )

    async def _invoke_expander(
        self,
        *,
        decision: RoutingDecision,
        diff_chunks: list[dict[str, Any]],
        all_docs: list[str],
        doc_generation_provider: str | None,
    ) -> list[str]:
        from mcp_server.model_strategies.registry import (
            create_model_provider,
            normalize_model_id,
        )
        from mcp_server.providers.custom.models import ModelOptions

        model_id = normalize_model_id(
            doc_generation_provider
            if doc_generation_provider and "/" in doc_generation_provider
            else config.doc_generation_write_model
        )
        provider_config = _expander_provider_config(model_id)
        provider = create_model_provider(
            model_id,
            api_key=provider_config["api_key"],
            base_url=provider_config["base_url"],
            ollama_base_url=config.ollama_base_url,
        )
        prompt = self._expand_prompt(decision, diff_chunks, all_docs)
        result = await provider.complete(
            prompt,
            options=ModelOptions(
                max_tokens=1_000, temperature=0.0, timeout_s=60.0, retries=0
            ),
        )
        if not result.success:
            raise ValueError(result.raw_error or "expand model failed")
        return _parse_expand_docs(result.text)

    @staticmethod
    def _expand_prompt(
        decision: RoutingDecision,
        diff_chunks: list[dict[str, Any]],
        all_docs: list[str],
    ) -> str:
        return (
            "You are an expand-only documentation impact reviewer. The "
            "authoritative set of documents to regenerate has already been "
            "computed deterministically. You may ONLY suggest ADDITIONAL "
            "documents from the catalog that a code change semantically affects "
            "but that structural analysis missed. You cannot remove documents or "
            "change the strategy. Return strict JSON only.\n\n"
            'Schema: {"add_docs":["DOC_NAME"],"reason":"short reason"}\n'
            "Return an empty add_docs list if nothing extra is affected.\n\n"
            f"Document catalog (names without .md):\n{json.dumps(all_docs, ensure_ascii=False)}\n\n"
            f"Already-selected documents:\n{json.dumps(decision.affected_docs, ensure_ascii=False)}\n\n"
            f"Diff chunk summary:\n{json.dumps(diff_chunks, ensure_ascii=False)}"
        )

    @staticmethod
    def _affected_docs(
        dependency: GroupDocDependency | None, changed_groups: list[str]
    ) -> list[str]:
        if dependency is None:
            return []
        affected: set[str] = set()
        for group in changed_groups:
            affected |= dependency.docs_for_group(group)
        affected |= dependency.cross_cutting_docs()
        return sorted(affected)

    @staticmethod
    def _load_dependency(output_dir: Path) -> GroupDocDependency | None:
        manifest_path = (
            output_dir / "docs" / RAG_SUPPORT_DIRNAME / DOC_PACK_MANIFEST_FILENAME
        )
        return GroupDocDependency.load(manifest_path)

    @staticmethod
    def _full_regen_reason(
        dependency: GroupDocDependency | None,
        changed_groups: list[str],
        affected: list[str],
    ) -> str:
        if dependency is None:
            return "No provenance manifest (pre-feature baseline) -> full regeneration"
        if not changed_groups:
            return "Changed files did not resolve to any scan group -> full regeneration"
        return "Changed groups map to no docs -> full regeneration"


def _expander_provider_config(model_id: str) -> dict[str, str | None]:
    """Resolve API key / base URL for the optional expand-only model call.

    Mirrors the doc-generation provider resolution: hosted providers
    (openai/anthropic/google) use SDK-managed credentials; deepseek and the
    default (Ollama/local) read their respective config entries. No secret is
    hardcoded.
    """
    from mcp_server.model_strategies.registry import normalize_model_id

    provider = normalize_model_id(model_id).partition("/")[0]
    if provider == "deepseek":
        return {
            "api_key": config.doc_generation_deepseek_api_key or config.doc_generation_api_key,
            "base_url": config.doc_generation_deepseek_base_url or config.doc_generation_base_url,
        }
    if provider in {"openai", "anthropic", "google"}:
        return {"api_key": None, "base_url": None}
    return {
        "api_key": config.doc_generation_api_key,
        "base_url": config.doc_generation_base_url,
    }


def _parse_expand_docs(text: str) -> list[str]:
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```(?:json)?\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("expand output must be a JSON object")
    add_docs = data.get("add_docs")
    if not isinstance(add_docs, list):
        raise ValueError("expand output missing add_docs list")
    return [item for item in add_docs if isinstance(item, str) and item.strip()]
