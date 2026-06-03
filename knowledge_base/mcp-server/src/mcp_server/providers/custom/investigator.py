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
_INVESTIGATION_RELEVANCE_RE = re.compile(
    r"\b("
    r"select|insert|update|delete|merge|from|join|where|group\s+by|order\s+by|"
    r"create\s+table|alter\s+table|foreign\s+key|primary\s+key|"
    r"connectionstring|opendbconnection|sqlconnection|dbconnection|ormlite|dapper|"
    r"dbcontext|entity|table|repository|controller|service|endpoint|route|"
    r"appsettings|configurationmanager|getconnectionstring|registerconnection"
    r")\b",
    re.IGNORECASE,
)
_WINDOW_CHARS = 7_500
_MAX_CHUNK_CONTEXT_CHARS = 55_000
_MAX_INVESTIGATION_CONTEXT_CHARS = 120_000


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


def _relevance_window_text(chunk_name: str, text: str, unit_names: list[str]) -> str:
    """Return merged evidence windows from a chunk instead of a blind prefix.

    Source chunks can be larger than the model budget. Prefix clipping loses
    late SQL/API/config evidence; relevance windows keep source-local context
    around matched evidence terms and target unit names, preserving line numbers.
    """

    spans: list[tuple[int, int]] = []
    for match in _INVESTIGATION_RELEVANCE_RE.finditer(text):
        spans.append(_window_span(len(text), match.start(), match.end()))
    for unit in unit_names:
        if not unit:
            continue
        for match in re.finditer(re.escape(unit), text, re.IGNORECASE):
            spans.append(_window_span(len(text), match.start(), match.end()))

    if not spans:
        body = text[: min(len(text), _MAX_CHUNK_CONTEXT_CHARS)]
        return f"--- {chunk_name} ---\n{body}"

    merged = _merge_spans(spans)
    sections: list[str] = [f"--- {chunk_name} relevance windows ---"]
    total = sum(len(section) for section in sections)
    for start, end in merged:
        if total >= _MAX_CHUNK_CONTEXT_CHARS:
            sections.append(f"[chunk context capped at {_MAX_CHUNK_CONTEXT_CHARS} chars]")
            break
        body = text[start:end].strip()
        if not body:
            continue
        line_start = text[:start].count("\n") + 1
        line_end = line_start + body.count("\n")
        remaining = _MAX_CHUNK_CONTEXT_CHARS - total
        block = f"\n[lines {line_start}-{line_end}, chars {start}-{end}]\n{body[:remaining]}"
        sections.append(block)
        total += len(block)
    return "\n".join(sections)


def _window_span(text_len: int, start: int, end: int) -> tuple[int, int]:
    half = _WINDOW_CHARS // 2
    return max(0, start - half), min(text_len, end + half)


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if not merged or start > merged[-1][1] + 500:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _bounded_join(parts: list[str], max_chars: int) -> str:
    kept: list[str] = []
    total = 0
    for part in parts:
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(part) > remaining:
            kept.append(part[:remaining])
            total += remaining
            break
        kept.append(part)
        total += len(part)
    return "\n\n".join(kept)


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

    def collect_partial_evidence(
        self,
        payload: dict[str, Any],
        units_needed: set[str],
        *,
        default_chunk_ref: str | None = None,
    ) -> tuple[list[InvestigationEvidence], dict[str, str], list[str], list[str]]:
        """Validate present evidence items without raising on missing units.

        Returns (valid_evidence, valid_skipped, missing_units, item_errors).
        Caller accumulates across attempts and retries only missing_units.
        """
        raw_items = payload.get("evidence")
        if not isinstance(raw_items, list):
            return [], {}, sorted(units_needed), ["finish action requires evidence list"]

        skipped_raw = payload.get("skipped_units") or {}
        if not isinstance(skipped_raw, dict):
            skipped_raw = {}

        valid_evidence: list[InvestigationEvidence] = []
        item_errors: list[str] = []

        for idx, item in enumerate(raw_items):
            if not isinstance(item, dict):
                item_errors.append(f"evidence[{idx}] must be an object")
                continue
            try:
                normalized = dict(item)
                if default_chunk_ref and not normalized.get("chunk_ref"):
                    normalized["chunk_ref"] = default_chunk_ref
                if default_chunk_ref and not normalized.get("source_file"):
                    normalized["source_file"] = default_chunk_ref
                ev = InvestigationEvidence.model_validate(normalized)
                self._validate_item(ev, units_needed)
                valid_evidence.append(ev)
            except (ValidationError, InvestigatorError) as exc:
                item_errors.append(f"evidence[{idx}]: {exc}")

        valid_skipped: dict[str, str] = {}
        for unit, reason in skipped_raw.items():
            if unit in units_needed and str(reason).strip():
                valid_skipped[str(unit)] = str(reason).strip()

        covered = {ev.unit_name for ev in valid_evidence} | set(valid_skipped)
        missing = sorted(units_needed - covered)
        return valid_evidence, valid_skipped, missing, item_errors

    def validate_report(
        self,
        group: str,
        payload: dict[str, Any],
        allowed_units: set[str],
        *,
        default_chunk_ref: str | None = None,
    ) -> InvestigationReport:
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
                normalized = dict(item)
                if default_chunk_ref and not normalized.get("chunk_ref"):
                    normalized["chunk_ref"] = default_chunk_ref
                if default_chunk_ref and not normalized.get("source_file"):
                    normalized["source_file"] = default_chunk_ref
                ev = InvestigationEvidence.model_validate(normalized)
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
        if ev.source_file:
            source = self.toolbox.resolve_inside_staging(ev.source_file)
            if not source.is_file():
                raise InvestigatorError(f"source file does not exist: {ev.source_file}")
            if self.toolbox.relative_path(source) != ev.source_file:
                raise InvestigatorError(f"source_file must be staging-relative canonical path: {self.toolbox.relative_path(source)}")
        if ev.chunk_ref:
            chunk = self.toolbox.resolve_inside_staging(ev.chunk_ref)
            if not chunk.is_file():
                raise InvestigatorError(f"chunk_ref file does not exist: {ev.chunk_ref}")
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
        preamble: str | None = None,
    ) -> None:
        self.provider = provider
        self.toolbox = StagingToolbox(staging_dir, limits)
        self.validator = EvidenceValidator(facts, self.toolbox)
        self.facts = facts
        self._preamble = preamble

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

        # Pre-flight: if the preprocess file has no meaningful content, skip all
        # units immediately rather than burning 3 LLM retries on empty source data.
        if len(content.strip()) < config.min_investigation_content_bytes:
            return InvestigationReport(
                group=group,
                source_groups=[group],
                evidence=[],
                skipped_units={u: "no source content in scanner output" for u in allowed_units},
                tool_rounds=0,
                invalid_retries=0,
                input_tokens=0,
                output_tokens=0,
            ), []

        calls: list[ProviderCallLog] = []

        accumulated_evidence: list[InvestigationEvidence] = []
        accumulated_skipped: dict[str, str] = {}
        units_needed = list(allowed_units)
        repair_context: str | None = None
        last_exc: Exception | None = None

        for attempt in range(3):
            prompt = self._build_prompt(
                group, source_ref, content, units_needed, repair_context=repair_context
            )
            model_result = await self.provider.complete(
                prompt,
                options=ModelOptions(
                    max_tokens=config.investigation_max_tokens,
                    temperature=0.0,
                    retries=1,
                    timeout_s=180.0,
                    provider_options=provider_options or {},
                    prefill="{",
                    json_mode=True,
                ),
            )
            calls.append(ProviderCallLog(
                phase="investigation",
                target=f"{group}:attempt{attempt + 1}",
                result=model_result,
            ))
            if not model_result.success:
                raise InvestigatorError(f"model call failed: {model_result.raw_error}")

            try:
                payload = json.loads(_extract_json_object(model_result.text))
                if not isinstance(payload, dict):
                    raise InvestigatorError(
                        f"model returned {type(payload).__name__} instead of an object "
                        '- expected {"evidence": [...]}'
                    )
            except (InvestigatorError, json.JSONDecodeError, ValueError) as exc:
                last_exc = exc
                preview = model_result.text[:300].replace("\n", " ") if model_result.text else "<empty>"
                if attempt == 2:
                    raise InvestigatorError(
                        f"evidence extraction failed after 3 attempts: {exc} | preview: {preview}"
                    ) from exc
                # JSON parse failure — reset to full unit list and carry error context
                units_needed = list(allowed_units)
                repair_context = f"JSON parse failed: {exc}\nReturn corrected JSON only."
                continue

            new_evidence, new_skipped, missing, item_errors = self.validator.collect_partial_evidence(
                payload,
                set(units_needed),
                default_chunk_ref=source_ref,
            )
            accumulated_evidence.extend(new_evidence)
            accumulated_skipped.update(new_skipped)

            if not missing:
                report = InvestigationReport(
                    group=group,
                    source_groups=[group],
                    evidence=accumulated_evidence,
                    skipped_units=dict(sorted(accumulated_skipped.items())),
                    tool_rounds=attempt + 1,
                    invalid_retries=attempt,
                    input_tokens=sum(c.result.input_tokens for c in calls),
                    output_tokens=sum(c.result.output_tokens for c in calls),
                )
                return report, calls

            # Targeted repair: retry only for missing units (cap at 15).
            # Include validation errors from this attempt so the model knows why
            # the evidence was rejected, not just which units were missed.
            units_needed = missing[:15]
            repair_context = (
                f"Previous attempt missed these units: {', '.join(units_needed)}\n"
                + (f"Validation errors: {'; '.join(item_errors[:5])}\n" if item_errors else "")
                + "Return evidence ONLY for the units listed above, fixing the validation errors."
            )
            last_exc = InvestigatorError(
                f"missing units after attempt {attempt + 1}: {', '.join(units_needed)}"
            )

        # Auto-skip any units that remain unresolved after all retries rather than
        # raising a hard error. The harness already catches InvestigatorError and
        # continues, but this avoids the WARNING log and wasted retries on units
        # that simply aren't in the chunk (e.g. false-positive unit assignments from
        # substring matching in import statements).
        still_missing = sorted(
            set(units_needed) - {ev.unit_name for ev in accumulated_evidence} - set(accumulated_skipped)
        )
        if still_missing:
            for unit in still_missing:
                accumulated_skipped[unit] = "not evidenced in chunk after maximum retries — unit may be in a different chunk or have no extractable content here"
        return InvestigationReport(
            group=group,
            source_groups=[group],
            evidence=accumulated_evidence,
            skipped_units=dict(sorted(accumulated_skipped.items())),
            tool_rounds=3,
            invalid_retries=3,
            input_tokens=sum(c.result.input_tokens for c in calls),
            output_tokens=sum(c.result.output_tokens for c in calls),
        ), calls

    def _build_prompt(
        self,
        group: str,
        source_ref: str,
        content: str,
        allowed_units: list[str],
        *,
        repair_context: str | None = None,
    ) -> str:
        units_list = "\n".join(f"- {u}" for u in allowed_units)
        base = f"""\
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
      "chunk_ref": "{source_ref}",
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
- chunk_ref must be exactly: {source_ref}
- source_file may be {source_ref} unless the scan data names a more specific original source artifact
- line_range is required (approximate is fine)
- Multiple evidence items per unit are encouraged
- If the scan data is a pure data file (JSON/XML data without source code, logic, or
  configuration values — e.g. a fixture file, test dataset, or empty schema stub),
  list ALL units in skipped_units with reason "data-only file: no source code in scanner output"
"""
        if self._preamble:
            base = f"{self._preamble.rstrip()}\n\n{base}"
        if repair_context:
            return base + f"\n\n{repair_context}"
        return base


class ChunkManifestInvestigator:
    """Two-phase investigator: model selects chunks from a manifest, Python fetches them.

    Phase 1 (~2k tokens): manifest call — model picks which chunks to read.
    Phase 2 (~15-20k tokens): extraction call — standard evidence extraction on selected chunks only.

    For a group with 18 chunks but 3-5 relevant ones this costs ~2 calls instead of ~18.
    """

    def __init__(
        self,
        provider: ModelProvider,
        staging_dir: Path,
        facts: FactsManifest,
        *,
        limits: InvestigatorLimits | None = None,
        preamble: str | None = None,
    ) -> None:
        self.provider = provider
        self.staging_dir = Path(staging_dir)
        self.toolbox = StagingToolbox(staging_dir, limits)
        self.validator = EvidenceValidator(facts, self.toolbox)
        self.facts = facts
        self._preamble = preamble

    async def run(
        self,
        group: str,
        *,
        chunks: list[Path],
        group_units: list[str],
        manifest: list[dict[str, Any]],
        group_file: Path,
        provider_options: dict[str, Any] | None = None,
    ) -> tuple[InvestigationReport, list[ProviderCallLog]]:
        """Run agentic investigation. manifest is pre-built by the caller."""
        calls: list[ProviderCallLog] = []

        if not manifest or not group_units:
            return InvestigationReport(
                group=group, source_groups=[group], evidence=[],
                skipped_units={u: "no chunks or units available" for u in group_units},
            ), []

        # Phase 1: manifest selection — cheap call, model picks chunk IDs.
        # 512 tokens was too small: finish=length truncation caused incomplete selections.
        # With 20+ chunks each chunk ID is ~8 tokens; 1024 gives safe headroom.
        selection_result = await self.provider.complete(
            self._build_selection_prompt(group, group_units, manifest),
            options=ModelOptions(
                max_tokens=1024,
                temperature=0.0,
                retries=1,
                timeout_s=60.0,
                provider_options=provider_options or {},
                prefill='{"fetch_ids":',
                json_mode=True,
            ),
        )
        calls.append(ProviderCallLog(
            phase="investigation",
            target=f"{group}:manifest_select",
            result=selection_result,
        ))

        valid_ids = {e["id"] for e in manifest}
        selected_ids: set[str] = set()
        if selection_result.success:
            try:
                payload = json.loads(_extract_json_object(selection_result.text))
                raw_ids = payload.get("fetch_ids") or []
                selected_ids = {str(i) for i in raw_ids if str(i) in valid_ids}
            except (json.JSONDecodeError, ValueError):
                pass
        if not selected_ids:
            selected_ids = valid_ids  # fallback: use all

        # Safety net: for every unit that appears in the manifest but isn't covered
        # by any selected chunk, force-add the chunks that contain it.
        # This guarantees agentic mode never produces worse unit coverage than sequential.
        unit_to_chunks: dict[str, list[str]] = {}
        for entry in manifest:
            for unit in entry.get("unit_names") or []:
                unit_to_chunks.setdefault(unit, []).append(entry["id"])

        covered_by_selection = {
            unit for unit, chunk_ids in unit_to_chunks.items()
            if any(cid in selected_ids for cid in chunk_ids)
        }
        forced_ids: set[str] = set()
        for unit in group_units:
            if unit not in covered_by_selection and unit in unit_to_chunks:
                forced_ids.update(unit_to_chunks[unit])

        if forced_ids:
            selected_ids = selected_ids | forced_ids

        # Phase 2: fetch selected chunks and run standard extraction.
        selected_chunks = [cf for cf in chunks if cf.stem in selected_ids and cf.exists()]
        if not selected_chunks:
            selected_chunks = [cf for cf in chunks if cf.exists()]

        parts: list[str] = []
        for cf in selected_chunks:
            try:
                text = cf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            windowed = _relevance_window_text(cf.stem, text, group_units)
            parts.append(windowed)
        combined_content = _bounded_join(parts, _MAX_INVESTIGATION_CONTEXT_CHARS)

        # Use the group preprocess file as chunk_ref default (exists in staging).
        try:
            source_ref = group_file.relative_to(self.staging_dir).as_posix()
        except ValueError:
            source_ref = f"_preprocess/{group}.md"

        # Split units into batches so each extraction call stays within the output
        # token limit. 72 units in one call hits 10k tokens and truncates; 20 per
        # batch keeps output well under the limit and runs batches in parallel.
        batch_size = config.agent_phase1_batch_size
        batches = [
            group_units[i:i + batch_size]
            for i in range(0, len(group_units), batch_size)
        ]

        batch_results = await asyncio.gather(*[
            self._extract_batch(group, batch, combined_content, source_ref, provider_options)
            for batch in batches
        ])

        accumulated_evidence: list[InvestigationEvidence] = []
        accumulated_skipped: dict[str, str] = {}
        for batch_evidence, batch_skipped, batch_calls in batch_results:
            accumulated_evidence.extend(batch_evidence)
            accumulated_skipped.update(batch_skipped)
            calls.extend(batch_calls)

        return InvestigationReport(
            group=group,
            source_groups=[group],
            evidence=accumulated_evidence,
            skipped_units=dict(sorted(accumulated_skipped.items())),
            tool_rounds=len(calls),
            invalid_retries=0,
            input_tokens=sum(c.result.input_tokens for c in calls),
            output_tokens=sum(c.result.output_tokens for c in calls),
        ), calls

    async def _extract_batch(
        self,
        group: str,
        units: list[str],
        content: str,
        source_ref: str,
        provider_options: dict[str, Any] | None,
    ) -> tuple[list[InvestigationEvidence], dict[str, str], list[ProviderCallLog]]:
        """Extract evidence for one batch of units. Retries up to 3 times on failure."""
        calls: list[ProviderCallLog] = []
        accumulated_evidence: list[InvestigationEvidence] = []
        accumulated_skipped: dict[str, str] = {}
        units_needed = list(units)
        repair_context: str | None = None

        for attempt in range(3):
            extract_result = await self.provider.complete(
                self._build_extraction_prompt(group, source_ref, content, units_needed, repair_context),
                options=ModelOptions(
                    max_tokens=config.investigation_max_tokens,
                    temperature=0.0,
                    retries=1,
                    timeout_s=180.0,
                    provider_options=provider_options or {},
                    prefill="{",
                    json_mode=True,
                ),
            )
            calls.append(ProviderCallLog(
                phase="investigation",
                target=f"{group}:batch_attempt{attempt + 1}",
                result=extract_result,
            ))

            if not extract_result.success:
                raise InvestigatorError(f"model call failed: {extract_result.raw_error}")

            try:
                payload = json.loads(_extract_json_object(extract_result.text))
            except (json.JSONDecodeError, ValueError) as exc:
                if attempt == 2:
                    raise InvestigatorError(f"evidence extraction failed: {exc}")
                units_needed = list(units)
                repair_context = f"JSON parse failed: {exc}\nReturn corrected JSON only."
                continue

            new_evidence, new_skipped, missing, item_errors = self.validator.collect_partial_evidence(
                payload, set(units_needed), default_chunk_ref=source_ref,
            )
            accumulated_evidence.extend(new_evidence)
            accumulated_skipped.update(new_skipped)

            if not missing:
                return accumulated_evidence, accumulated_skipped, calls

            units_needed = missing[:15]
            repair_context = (
                f"Previous attempt missed: {', '.join(units_needed)}\n"
                + (f"Validation errors: {'; '.join(item_errors[:5])}\n" if item_errors else "")
                + "Return evidence ONLY for the units listed above."
            )

        still_missing = sorted(
            set(units_needed) - {ev.unit_name for ev in accumulated_evidence} - set(accumulated_skipped)
        )
        for unit in still_missing:
            accumulated_skipped[unit] = (
                "not evidenced in selected chunks after retries"
                " — unit may be in an unselected chunk or have no extractable content"
            )
        return accumulated_evidence, accumulated_skipped, calls

    def _build_selection_prompt(self, group: str, group_units: list[str], manifest: list[dict]) -> str:
        units_list = "\n".join(f"- {u}" for u in group_units)
        return (
            f"You are planning an evidence investigation of integration source code.\n\n"
            f"Group: {group}\n"
            f"Units to investigate ({len(group_units)} total):\n{units_list}\n\n"
            f"There are {len(manifest)} preprocess chunks available. Select the minimum set needed to"
            " cover all units above.\n\n"
            "Each chunk entry has:\n"
            "- unit_names: scanner-detected unit names that appear in this chunk\n"
            "- categories: evidence categories present (sql_queries, db_connections, auth, etc.)\n"
            "- identifiers: actual named things extracted from the chunk — SQL table names,"
            " class names, connection names, URLs, config keys\n\n"
            "Selection strategy:\n"
            "- A chunk is needed if it contains a unit from the list above (check unit_names)\n"
            "- Use identifiers to decide between chunks: if you need evidence about a table or"
            " class, pick the chunk whose identifiers list contains that name\n"
            "- Skip chunks where unit_names is empty AND identifiers contains nothing relevant\n"
            "- When unsure, include the chunk — missing evidence is worse than reading one extra chunk\n\n"
            "=== CHUNK MANIFEST ===\n"
            + json.dumps(manifest, indent=2)
            + "\n=== END MANIFEST ===\n\n"
            'Return ONLY a JSON object: {"fetch_ids": ["id1", "id2"], "reasoning": "one sentence"}'
        )

    def _build_extraction_prompt(
        self,
        group: str,
        source_ref: str,
        content: str,
        units_needed: list[str],
        repair_context: str | None = None,
    ) -> str:
        units_list = "\n".join(f"- {u}" for u in units_needed)
        base = (
            f"You are extracting structured evidence from integration scan data.\n\n"
            f"Scan data for group: {group}\nSource file: {source_ref}\n\n"
            f"=== SCAN DATA ===\n{content}\n=== END SCAN DATA ===\n\n"
            "Extract evidence for EVERY unit listed below from the scan data above.\n"
            "If a unit truly has no data, add it to skipped_units with a specific reason.\n\n"
            f"Units to cover:\n{units_list}\n\n"
            "Return ONLY a valid JSON object:\n\n"
            "{\n"
            '  "evidence": [\n'
            "    {\n"
            '      "unit_name": "exact unit name from the list above",\n'
            '      "category": "short label (e.g. data flow, business logic, error handling)",\n'
            f'      "source_file": "{source_ref}",\n'
            f'      "chunk_ref": "{source_ref}",\n'
            '      "line_range": "approximate line range e.g. 10-25",\n'
            '      "snippet": "exact short excerpt from the scan data (minimum 8 chars)",\n'
            '      "observation": "specific concrete finding about this unit (minimum 20 chars)"\n'
            "    }\n"
            "  ],\n"
            '  "skipped_units": {"UnitName": "specific reason"}\n'
            "}\n\n"
            "Rules:\n"
            "- Every unit must appear in evidence OR skipped_units\n"
            "- snippet must be an exact excerpt from the scan data above\n"
            "- observation must be specific, not generic\n"
            f"- chunk_ref must be exactly: {source_ref}\n"
            "- line_range is required (approximate is fine)\n"
            "- Multiple evidence items per unit are encouraged\n"
        )
        if self._preamble:
            base = f"{self._preamble.rstrip()}\n\n{base}"
        if repair_context:
            base += f"\n\n{repair_context}"
        return base


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
