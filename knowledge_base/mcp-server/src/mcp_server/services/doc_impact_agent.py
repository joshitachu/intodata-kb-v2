"""Structured planning agent for Git auto-documentation updates."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from mcp_server.config import config
from mcp_server.model_strategies.registry import create_model_provider
from mcp_server.providers.custom.models import ModelOptions
from mcp_server.services.quick_change_scanner import QuickScanReport

logger = logging.getLogger(__name__)

ImpactStrategy = Literal["skip", "bounded_patch", "targeted_regen", "manual_review"]
VALID_STRATEGIES: set[str] = {"skip", "bounded_patch", "targeted_regen", "manual_review"}


@dataclass(frozen=True)
class DocImpactPlan:
    strategy: ImpactStrategy
    affected_docs: list[str] = field(default_factory=list)
    required_facts: list[str] = field(default_factory=list)
    stale_claims: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "affected_docs": self.affected_docs,
            "required_facts": self.required_facts,
            "stale_claims": self.stale_claims,
            "confidence": self.confidence,
            "reason": self.reason,
        }


class DocImpactAgent:
    """Ask one internal model call for a strict JSON documentation-impact plan."""

    async def plan(
        self,
        *,
        quick_scan: QuickScanReport,
        diff_chunks: list[dict[str, Any]],
        doc_generation_provider: str | None,
    ) -> DocImpactPlan:
        prompt = self._build_prompt(quick_scan, diff_chunks)
        try:
            result = await self._provider(doc_generation_provider).complete(
                prompt,
                options=ModelOptions(max_tokens=2_000, temperature=0.0, timeout_s=120.0, retries=1),
            )
            if not result.success:
                raise ValueError(result.raw_error or "impact model failed")
            return parse_impact_plan(result.text)
        except Exception as exc:
            logger.warning("Doc impact agent fell back to heuristic plan: %s", exc)
            return heuristic_impact_plan(quick_scan, reason=f"impact agent fallback: {exc}")

    def _provider(self, doc_generation_provider: str | None):
        model_id = (
            doc_generation_provider
            if doc_generation_provider and "/" in doc_generation_provider
            else config.doc_generation_write_model
        )
        return create_model_provider(
            model_id,
            api_key=config.doc_generation_api_key,
            base_url=config.doc_generation_base_url,
            ollama_base_url=config.ollama_base_url,
        )

    def _build_prompt(self, quick_scan: QuickScanReport, diff_chunks: list[dict[str, Any]]) -> str:
        return (
            "You are an internal documentation impact planner. Return strict JSON only.\n"
            "Choose one strategy: skip, bounded_patch, targeted_regen, manual_review.\n"
            "Use bounded_patch only when selected existing docs can be safely patched from the diff.\n"
            "Use targeted_regen when new modules, broad architecture changes, or low confidence require regeneration.\n"
            "Use skip only when docs/RAG do not need content changes.\n\n"
            "Required JSON schema:\n"
            "{\"strategy\":\"bounded_patch\",\"affected_docs\":[\"04_BUSINESS_RULES_AND_TRANSFORMATIONS.md\"],"
            "\"required_facts\":[\"fact to add\"],\"stale_claims\":[\"claim to remove\"],"
            "\"confidence\":0.0,\"reason\":\"short reason\"}\n\n"
            f"Quick scan:\n{json.dumps(quick_scan.to_dict(), ensure_ascii=False, indent=2)}\n\n"
            f"Diff chunk summary:\n{json.dumps(diff_chunks, ensure_ascii=False, indent=2)}"
        )


def parse_impact_plan(text: str) -> DocImpactPlan:
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```(?:json)?\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("impact plan must be a JSON object")
    strategy = data.get("strategy")
    if strategy not in VALID_STRATEGIES:
        raise ValueError("impact plan missing valid strategy")
    confidence = _confidence(data.get("confidence"))
    affected_docs = _string_list(data.get("affected_docs"))
    if strategy == "bounded_patch" and not affected_docs:
        raise ValueError("bounded_patch requires affected_docs")
    return DocImpactPlan(
        strategy=strategy,  # type: ignore[arg-type]
        affected_docs=affected_docs,
        required_facts=_string_list(data.get("required_facts")),
        stale_claims=_string_list(data.get("stale_claims")),
        confidence=confidence,
        reason=str(data.get("reason") or ""),
    )


def heuristic_impact_plan(quick_scan: QuickScanReport, *, reason: str = "heuristic impact plan") -> DocImpactPlan:
    if not quick_scan.doc_relevant_files:
        return DocImpactPlan(strategy="skip", confidence=0.95, reason=reason)
    if "new_source_files" in quick_scan.likely_doc_impact and len(quick_scan.new_source_files) > config.git_diff_patch_max_files:
        return DocImpactPlan(
            strategy="targeted_regen",
            required_facts=quick_scan.new_source_files[:10],
            confidence=0.65,
            reason=reason,
        )
    docs = _preferred_docs(quick_scan)
    return DocImpactPlan(
        strategy="bounded_patch",
        affected_docs=docs,
        required_facts=(quick_scan.symbols + quick_scan.new_source_files + quick_scan.module_folders)[:20],
        stale_claims=quick_scan.readme_numeric_claims[:10],
        confidence=0.75,
        reason=reason,
    )


def _preferred_docs(quick_scan: QuickScanReport) -> list[str]:
    impacts = set(quick_scan.likely_doc_impact)
    docs: list[str] = []

    def add(name: str) -> None:
        if name not in docs:
            docs.append(name)

    if "architecture_or_patterns" in impacts:
        add("08_CHANGE_PLAYBOOK.md")
        add("01_ARCHITECTURE_MAP.md")
        add("00_AGENT_BRIEF.md")
    if "api_surface" in impacts:
        add("03_DATA_FLOWS_AND_LINEAGE.md")
        add("04_BUSINESS_RULES_AND_TRANSFORMATIONS.md")
    if "runtime_config" in impacts:
        add("05_RUNTIME_CONFIG_AND_EXTERNALS.md")
        add("06_OPERATIONS_TESTING_AND_DEBUGGING.md")
    if not docs:
        add("04_BUSINESS_RULES_AND_TRANSFORMATIONS.md")
    return docs[: config.git_diff_patch_max_docs]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        raise ValueError("impact plan missing confidence")
