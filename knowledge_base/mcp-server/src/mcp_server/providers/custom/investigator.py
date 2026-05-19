"""Controlled investigator loop for custom provider runs.

The model can ask for a small set of read-only actions. Python owns path
validation, execution, evidence validation, and artifact writing.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from mcp_server.config import config
from mcp_server.providers.custom.models import (
    FactsManifest,
    InvestigationEvidence,
    InvestigationReport,
    ModelOptions,
    ProviderCallLog,
)
from mcp_server.providers.custom.prompts import facts_summary, platform_analysis_requirements
from mcp_server.providers.custom.providers import ModelProvider, _estimate_tokens


ALLOWED_ACTIONS = {"read_file", "search_text", "list_dir", "finish"}
GENERIC_OBSERVATIONS = {
    "scanner detail is limited",
    "needs review",
    "not enough information",
    "no details available",
    "generic",
}


@dataclass(frozen=True)
class InvestigatorLimits:
    max_rounds: int = 18
    max_invalid_retries: int = 2
    max_read_bytes: int = 12_000
    max_search_results: int = 25
    timeout_s: float = 240.0


class InvestigatorError(RuntimeError):
    """Raised when investigation cannot produce valid evidence."""


InvestigationError = InvestigatorError


def parse_json_action(text: str) -> dict[str, Any]:
    """Parse a model JSON action, tolerating fenced JSON blocks."""

    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvestigatorError(f"invalid JSON action: {exc}") from exc
    if not isinstance(parsed, dict):
        raise InvestigatorError("JSON action must be an object")
    action = parsed.get("action")
    if action not in ALLOWED_ACTIONS:
        raise InvestigatorError(f"unsupported action: {action!r}")
    return parsed


class StagingToolbox:
    """Read-only tools scoped to the staging directory."""

    def __init__(self, staging_dir: Path, limits: InvestigatorLimits | None = None) -> None:
        self.staging_dir = staging_dir.resolve()
        self.limits = limits or InvestigatorLimits()

    def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        name = action["action"]
        if name == "read_file":
            return self._read_file(action)
        if name == "search_text":
            return self._search_text(action)
        if name == "list_dir":
            return self._list_dir(action)
        raise InvestigatorError(f"toolbox cannot execute action: {name}")

    def resolve_inside_staging(self, value: str | None) -> Path:
        raw = value or "."
        candidate = Path(raw)
        if candidate.is_absolute():
            path = candidate.resolve()
        else:
            path = (self.staging_dir / candidate).resolve()
        try:
            path.relative_to(self.staging_dir)
        except ValueError as exc:
            raise InvestigatorError(f"path outside staging is blocked: {raw}") from exc
        return path

    def relative_path(self, path: Path) -> str:
        return path.resolve().relative_to(self.staging_dir).as_posix()

    def _read_file(self, action: dict[str, Any]) -> dict[str, Any]:
        path = self.resolve_inside_staging(str(action.get("path") or ""))
        if not path.is_file():
            raise InvestigatorError(f"read_file target is not a file: {self.relative_path(path)}")
        offset = max(0, int(action.get("offset") or 0))
        max_bytes = min(self.limits.max_read_bytes, max(1, int(action.get("max_bytes") or self.limits.max_read_bytes)))
        data = path.read_bytes()
        chunk = data[offset:offset + max_bytes]
        text = chunk.decode("utf-8", errors="replace")
        line_start = data[:offset].decode("utf-8", errors="ignore").count("\n") + 1
        line_end = line_start + text.count("\n")
        return {
            "ok": True,
            "action": "read_file",
            "path": self.relative_path(path),
            "offset": offset,
            "bytes_returned": len(chunk),
            "truncated": offset + max_bytes < len(data),
            "line_range": f"{line_start}-{line_end}",
            "content": text,
        }

    def _search_text(self, action: dict[str, Any]) -> dict[str, Any]:
        query = str(action.get("query") or "")
        if not query:
            raise InvestigatorError("search_text requires query")
        root = self.resolve_inside_staging(str(action.get("path") or "."))
        if not root.exists():
            raise InvestigatorError(f"search_text path does not exist: {self.relative_path(root)}")
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        results: list[dict[str, Any]] = []
        for path in files:
            if len(results) >= self.limits.max_search_results:
                break
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    results.append({
                        "path": self.relative_path(path),
                        "line": lineno,
                        "snippet": line[:500],
                    })
                    if len(results) >= self.limits.max_search_results:
                        break
        return {
            "ok": True,
            "action": "search_text",
            "query": query,
            "result_count": len(results),
            "results": results,
        }

    def _list_dir(self, action: dict[str, Any]) -> dict[str, Any]:
        path = self.resolve_inside_staging(str(action.get("path") or "."))
        if not path.is_dir():
            raise InvestigatorError(f"list_dir target is not a directory: {self.relative_path(path)}")
        entries = []
        for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:200]:
            entries.append({
                "name": child.name,
                "path": self.relative_path(child),
                "type": "dir" if child.is_dir() else "file",
                "bytes": child.stat().st_size if child.is_file() else None,
            })
        return {"ok": True, "action": "list_dir", "path": self.relative_path(path), "entries": entries}


class EvidenceValidator:
    """Validate model-finished evidence against facts and staging."""

    def __init__(self, facts: FactsManifest, toolbox: StagingToolbox) -> None:
        self.facts = facts
        self.toolbox = toolbox
        self.unit_names = facts.unit_names()

    def validate_report(self, group: str, payload: dict[str, Any], allowed_units: set[str]) -> InvestigationReport:
        raw_items = payload.get("evidence")
        if not isinstance(raw_items, list):
            raise InvestigatorError("finish action requires evidence list")
        skipped = payload.get("skipped_units") or {}
        if not isinstance(skipped, dict):
            raise InvestigatorError("skipped_units must be an object when present")

        evidence: list[InvestigationEvidence] = []
        errors: list[str] = []
        for idx, item in enumerate(raw_items):
            if not isinstance(item, dict):
                errors.append(f"evidence[{idx}] must be an object")
                continue
            try:
                ev = InvestigationEvidence.model_validate(item)
                self._validate_item(ev, allowed_units)
                evidence.append(ev)
            except (ValidationError, InvestigatorError) as exc:
                errors.append(f"evidence[{idx}]: {exc}")

        clean_skipped: dict[str, str] = {}
        for unit, reason in skipped.items():
            if unit not in allowed_units:
                errors.append(f"skipped unit is not in this group: {unit}")
            elif not str(reason).strip():
                errors.append(f"skipped unit missing reason: {unit}")
            else:
                clean_skipped[str(unit)] = str(reason).strip()

        covered = {ev.unit_name for ev in evidence}
        missing = sorted(allowed_units - covered - set(clean_skipped))
        if missing:
            errors.append(f"missing investigation evidence for units: {', '.join(missing[:20])}")
        if errors:
            raise InvestigatorError("; ".join(errors))
        return InvestigationReport(
            group=group,
            source_groups=[group],
            evidence=evidence,
            skipped_units=dict(sorted(clean_skipped.items())),
        )

    def _validate_item(self, ev: InvestigationEvidence, allowed_units: set[str]) -> None:
        if ev.unit_name not in self.unit_names:
            raise InvestigatorError(f"unknown canonical unit: {ev.unit_name}")
        if ev.unit_name not in allowed_units:
            raise InvestigatorError(f"unit is not assigned to this investigation: {ev.unit_name}")
        source = self.toolbox.resolve_inside_staging(ev.source_file)
        if not source.is_file():
            raise InvestigatorError(f"source file does not exist: {ev.source_file}")
        if self.toolbox.relative_path(source) != ev.source_file:
            raise InvestigatorError(f"source_file must be staging-relative canonical path: {self.toolbox.relative_path(source)}")
        if not (ev.line_range or ev.chunk_ref):
            raise InvestigatorError("line_range or chunk_ref is required")
        snippet = (ev.snippet or "").strip()
        observation = ev.observation.strip()
        if not snippet:
            raise InvestigatorError("snippet is required")
        if len(snippet) < 8:
            raise InvestigatorError("snippet is too short to validate")
        if len(observation) < 20 or observation.lower() in GENERIC_OBSERVATIONS:
            raise InvestigatorError("observation is too generic")


class SingleShotInvestigator:
    """Single-call investigator for models that cannot run a reliable multi-turn action loop.

    Python reads the group file; the model only extracts evidence from pre-loaded content.
    One API call per chunk, up to 2 retries on validation failure.
    """

    def __init__(
        self,
        provider: ModelProvider,
        staging_dir: Path,
        facts: FactsManifest,
        *,
        limits: InvestigatorLimits | None = None,
    ) -> None:
        self.provider = provider
        self.toolbox = StagingToolbox(staging_dir, limits)
        self.validator = EvidenceValidator(facts, self.toolbox)
        self.facts = facts

    async def run(
        self,
        group: str,
        *,
        group_file: Path,
        context_files: list[Path],
        allowed_units: list[str],
        provider_options: dict[str, Any] | None = None,
    ) -> tuple[InvestigationReport, list[ProviderCallLog]]:
        content = group_file.read_text(encoding="utf-8", errors="replace")[:60_000]
        source_ref = self.toolbox.relative_path(group_file)
        prompt = self._build_prompt(group, source_ref, content, allowed_units)
        calls: list[ProviderCallLog] = []

        for attempt in range(3):
            result = await self.provider.complete(
                prompt,
                options=ModelOptions(
                    max_tokens=config.investigation_max_tokens,
                    temperature=0.0,
                    retries=1,
                    timeout_s=180.0,
                    provider_options=provider_options or {},
                    prefill="{",
                ),
            )
            calls.append(ProviderCallLog(
                phase="investigation",
                target=f"{group}:attempt{attempt + 1}",
                result=result,
            ))
            if not result.success:
                raise InvestigatorError(f"model call failed: {result.raw_error}")
            try:
                payload = json.loads(_extract_json_object(result.text))
                if not isinstance(payload, dict):
                    raise InvestigatorError(
                        f"model returned {type(payload).__name__} instead of an object "
                        '- expected {"evidence": [...]}'
                    )
                report = self.validator.validate_report(group, payload, set(allowed_units))
                report.tool_rounds = attempt + 1
                report.invalid_retries = attempt
                report.input_tokens = sum(c.result.input_tokens for c in calls)
                report.output_tokens = sum(c.result.output_tokens for c in calls)
                return report, calls
            except (InvestigatorError, json.JSONDecodeError, ValueError) as exc:
                preview = result.text[:300].replace("\n", " ") if result.text else "<empty>"
                if attempt == 2:
                    raise InvestigatorError(
                        f"evidence extraction failed after 3 attempts: {exc} | model response preview: {preview}"
                    ) from exc
                prompt += (
                    f"\n\nPrevious attempt failed validation. Fix these errors and retry:\n{exc}"
                    "\n\nReturn corrected JSON only."
                )

        raise InvestigatorError("unreachable")

    def _build_prompt(
        self,
        group: str,
        source_ref: str,
        content: str,
        allowed_units: list[str],
    ) -> str:
        units_list = "\n".join(f"- {u}" for u in allowed_units)
        return f"""\
You are extracting structured evidence from integration scan data.

Scan data for group: {group}
Source file: {source_ref}

=== SCAN DATA ===
{content}
=== END SCAN DATA ===

Extract evidence for EVERY unit listed below from the scan data above.
If a unit truly has no data, add it to skipped_units with a specific reason.

Units to cover:
{units_list}

Return ONLY a valid JSON object (no markdown fences, no explanation):

{{
  "evidence": [
    {{
      "unit_name": "exact unit name from the list above",
      "category": "short label describing the evidence type (e.g. data flow, business logic, error handling, security, orchestration)",
      "source_file": "{source_ref}",
      "line_range": "approximate line range e.g. 10-25",
      "snippet": "exact short excerpt from the scan data (minimum 8 chars)",
      "observation": "specific concrete finding about this unit (minimum 20 chars)"
    }}
  ],
  "skipped_units": {{
    "UnitName": "specific reason this unit has no usable data in the scan"
  }}
}}

Rules:
- Every unit must appear in evidence OR skipped_units
- snippet must be an exact excerpt from the scan data above
- observation must be specific, not generic phrases like "needs review" or "no details available"
- source_file must be exactly: {source_ref}
- line_range is required (approximate is fine)
- Multiple evidence items per unit are encouraged
"""


def _extract_json_object(text: str) -> str:
    """Extract a JSON object from model output, tolerating markdown fences and leading text.

    Falls back to json-repair for LLM-generated syntax errors (missing commas, etc.).
    """
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw.strip())
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in model response")
    candidate = raw[start : end + 1]
    try:
        json.loads(candidate)
        return candidate
    except json.JSONDecodeError:
        from json_repair import repair_json  # type: ignore[import-untyped]
        repaired = repair_json(candidate)
        json.loads(repaired)  # validate — raises JSONDecodeError if still broken
        return repaired


def write_investigation_artifacts(report: InvestigationReport, investigation_dir: Path) -> tuple[Path, Path]:
    investigation_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", report.group)
    json_path = investigation_dir / f"{safe_name}.json"
    md_path = investigation_dir / f"{safe_name}.md"
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    lines = [
        f"# Investigation Evidence: {report.group}",
        "",
        f"Tool rounds: {report.tool_rounds}",
        f"Validated evidence items: {len(report.evidence)}",
        "",
        "## Evidence",
        "",
    ]
    for ev in report.evidence:
        loc = ev.line_range or ev.chunk_ref or "unknown"
        lines.extend([
            f"### {ev.unit_name} - {ev.category}",
            "",
            f"- Source: `{ev.source_file}` ({loc})",
            f"- Observation: {ev.observation}",
            f"- Snippet: `{ev.snippet}`",
            "",
        ])
    if report.skipped_units:
        lines.extend(["## Skipped Units", ""])
        lines.extend(f"- {unit}: {reason}" for unit, reason in sorted(report.skipped_units.items()))
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def investigation_summary(reports: list[InvestigationReport]) -> str:
    evidence_count = sum(len(report.evidence) for report in reports)
    tokens_in = sum(report.input_tokens for report in reports)
    tokens_out = sum(report.output_tokens for report in reports)
    return (
        f"Investigation reports: {len(reports)}\n"
        f"Validated evidence items: {evidence_count}\n"
        f"Investigator tokens: input={tokens_in}, output={tokens_out}\n"
    )


def count_text_tokens(text: str) -> int:
    return _estimate_tokens(text)
