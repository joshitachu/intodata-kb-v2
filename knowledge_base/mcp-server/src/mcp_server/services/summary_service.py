"""AI-powered job summary generation using Ollama."""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import re
from pathlib import Path

import httpx
import ollama as ollama_lib

from mcp_server.config import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy singleton Ollama client (mirrors RAG_pipeline pattern)
# ---------------------------------------------------------------------------

_ollama_client: ollama_lib.AsyncClient | None = None
_ollama_lock = asyncio.Lock()


async def _get_ollama_client() -> ollama_lib.AsyncClient:
    global _ollama_client
    if _ollama_client is None:
        async with _ollama_lock:
            if _ollama_client is None:
                _ollama_client = ollama_lib.AsyncClient(
                    host=config.ollama_base_url,
                    timeout=httpx.Timeout(60.0, connect=5.0),
                )
    return _ollama_client


# ---------------------------------------------------------------------------
# Internal components to skip when describing the flow
# ---------------------------------------------------------------------------

_INTERNAL_COMPONENTS: frozenset[str] = frozenset({
    "tPrejob", "tPostjob", "tLogRow", "tSetGlobalVar", "tContextLoad",
    "tFixedFlowInput", "tFlowToIterate", "tFileInputDelimited",
    "tFileOutputDelimited", "tUnite", "tReplicate",
})

_SKIP_CONTEXT_PREFIXES = ("CODEGENERATION", "SCREEN", "NB_LINE", "ERROR")


class SummaryService:
    """Generate AI summaries for scanned jobs using Ollama."""

    def _build_component_flow(self, job_data: dict) -> str:
        """Walk the component connection graph to produce a readable flow."""
        components = job_data.get("components", [])
        connections = job_data.get("connections", [])

        # Build lookup: unique_name -> component_type + label
        comp_map: dict[str, str] = {}
        for c in components:
            ctype = c.get("component_type", "")
            label = ""
            for p in c.get("params", []):
                if p.get("name") == "LABEL" and p.get("value"):
                    label = p["value"]
                    break
            display = ctype
            if label and label != ctype:
                display = f"{ctype} ({label})"
            comp_map[c.get("unique_name", "")] = display

        if not connections:
            # No connection graph; just list notable component types
            notable = [
                c.get("component_type", "")
                for c in components
                if c.get("component_type", "") not in _INTERNAL_COMPONENTS
            ]
            return " -> ".join(dict.fromkeys(notable)[:8]) if notable else ""

        # Build adjacency from FLOW connections only
        flow_conns = [
            c for c in connections
            if c.get("connector_name") in ("FLOW", "MAIN", "LOOKUP", "REJECT")
        ]
        if not flow_conns:
            flow_conns = connections

        # Find roots (sources not targeted by anyone)
        targets = {c["target"] for c in flow_conns if "target" in c}
        sources = {c["source"] for c in flow_conns if "source" in c}
        roots = sources - targets
        if not roots:
            roots = sources

        # BFS to build ordered chain
        adj: dict[str, list[str]] = {}
        for c in flow_conns:
            src = c.get("source", "")
            tgt = c.get("target", "")
            if src and tgt:
                adj.setdefault(src, []).append(tgt)

        visited: set[str] = set()
        chain: list[str] = []
        queue = list(roots)
        while queue and len(chain) < 12:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            display = comp_map.get(node, node)
            # Skip internal/noise components in the flow description
            ctype = display.split(" (")[0] if " (" in display else display
            if ctype not in _INTERNAL_COMPONENTS:
                chain.append(display)
            for neighbor in adj.get(node, []):
                if neighbor not in visited:
                    queue.append(neighbor)

        return " -> ".join(chain) if chain else ""

    def _extract_context_vars(self, job_data: dict) -> list[str]:
        """Extract meaningful context variable names from the job."""
        result: list[str] = []
        for group in job_data.get("context_groups", []):
            for var in group.get("variables", []):
                name = var.get("name", "")
                vtype = var.get("type", "")
                if not name:
                    continue
                # Skip password/credential vars and noise
                if vtype == "id_Password":
                    continue
                if any(name.upper().startswith(p) for p in _SKIP_CONTEXT_PREFIXES):
                    continue
                # Skip vars with only empty values
                values = var.get("values", {})
                if all(not v or v == '""' for v in values.values()):
                    continue
                result.append(name)
        return result[:10]

    def _build_job_prompt(
        self,
        job_data: dict,
        direction: str,
        external_systems: list[str],
    ) -> str:
        """Build a compact prompt from job scan data."""
        job_name = job_data.get("name", "unknown")
        flow = self._build_component_flow(job_data)
        ctx_vars = self._extract_context_vars(job_data)

        # Build concise component type list
        comp_types = list(dict.fromkeys(
            c.get("component_type", "")
            for c in job_data.get("components", [])
            if c.get("component_type")
        ))

        parts = [
            f"Job: {job_name}",
            f"Direction: {direction or 'unknown'}",
        ]
        if external_systems:
            parts.append(f"External systems: {', '.join(external_systems)}")
        if flow:
            parts.append(f"Component flow: {flow}")
        elif comp_types:
            parts.append(f"Components: {', '.join(comp_types[:10])}")
        if ctx_vars:
            parts.append(f"Context variables: {', '.join(ctx_vars)}")

        # tRunJob references
        trunjob_refs = job_data.get("trunjob_refs", [])
        if trunjob_refs:
            refs = [r.get("target_job", "") for r in trunjob_refs if r.get("target_job")]
            if refs:
                parts.append(f"Calls sub-jobs: {', '.join(refs[:5])}")

        job_context = "\n".join(parts)

        return (
            "You are a technical writer summarizing integration jobs.\n"
            "Given the following job metadata, write exactly 1-2 sentences "
            "describing what this job does in plain English. "
            "Focus on: what data moves, from where to where, and why.\n"
            "Do NOT mention component names (like tMap, tJavaRow). "
            "Use business terms (e.g. 'reads item data from Business Central').\n"
            "Reply with ONLY the summary, no preamble.\n\n"
            f"{job_context}"
        )

    async def _summarize_one_job(
        self,
        job_name: str,
        job_data: dict,
        direction: str,
        ext_systems: list[str],
        semaphore: asyncio.Semaphore,
    ) -> tuple[str, str]:
        """Summarize a single job. Returns (job_name, summary)."""
        prompt = self._build_job_prompt(job_data, direction, ext_systems)

        try:
            async with semaphore:
                client = await _get_ollama_client()
                response = await client.chat(
                    model=config.ollama_summary_model,
                    messages=[{"role": "user", "content": prompt}],
                    options={"num_ctx": 4096, "temperature": 0.3},
                )
                raw = response["message"]["content"].strip()

                # Clean: ASCII-only, max 300 chars
                clean = raw.encode("ascii", errors="replace").decode("ascii")
                clean = re.sub(r"\s+", " ", clean).strip()
                if len(clean) > 300:
                    # Cut at last sentence boundary
                    truncated = clean[:300]
                    last_period = truncated.rfind(".")
                    if last_period > 100:
                        clean = truncated[: last_period + 1]
                    else:
                        clean = truncated.rstrip() + "..."

                return job_name, clean

        except Exception as e:
            logger.warning("Summary failed for %s: %s", job_name, e)
            # Fallback heuristic summary
            fallback = self._heuristic_summary(job_name, direction, ext_systems)
            return job_name, fallback

    def _heuristic_summary(
        self, job_name: str, direction: str, ext_systems: list[str],
    ) -> str:
        """Simple fallback when Ollama is unavailable."""
        dir_verb = {
            "OnRamp": "Ingests data from",
            "OffRamp": "Sends data to",
            "Bidirectional": "Syncs data with",
        }.get(direction, "Connects to")

        if ext_systems:
            systems = " and ".join(ext_systems[:3])
            return f"{dir_verb} {systems}."
        return ""

    async def generate_project_summaries(
        self, project_id: str, platform: str = "talend",
    ) -> dict:
        """Generate AI summaries for all jobs in a project.

        Returns dict with generation stats and the summaries dict.
        """
        scan_dir = config.intodoc_output_dir / project_id / "scan"
        manifest_path = scan_dir / "manifest.json"

        if not manifest_path.exists():
            return {"status": "no_scan", "summary_count": 0, "error_count": 0, "errors": []}

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        job_groups: dict[str, str] = manifest.get("job_groups", {})
        job_external_systems: dict[str, list[str]] = manifest.get("job_external_systems", {})

        if not job_groups:
            return {"status": "no_scan", "summary_count": 0, "error_count": 0, "errors": []}

        jobs_dir = scan_dir / "jobs"
        semaphore = asyncio.Semaphore(config.ollama_summary_concurrency)
        tasks: list[asyncio.Task] = []

        for job_name, group in job_groups.items():
            job_path = jobs_dir / group / f"{job_name}.json"
            if not job_path.exists():
                continue

            try:
                job_data = json.loads(job_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            ext_systems = job_external_systems.get(job_name, [])

            # Derive direction (same logic as projects.py)
            path_str = str(job_path)
            direction = self._derive_direction(path_str, job_name)

            # If no external systems from manifest, derive from job data
            if not ext_systems:
                from mcp_server.api.routes.projects import _derive_systems_from_job_json
                ext_systems = _derive_systems_from_job_json(job_data, platform)

            tasks.append(
                asyncio.create_task(
                    self._summarize_one_job(
                        job_name, job_data, direction, ext_systems, semaphore,
                    )
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        summaries: dict[str, str] = {}
        errors: list[str] = []
        for result in results:
            if isinstance(result, Exception):
                errors.append(str(result))
                continue
            name, summary = result
            if summary:
                summaries[name] = summary

        # Write summaries.json
        output = {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "model": config.ollama_summary_model,
            "job_count": len(summaries),
            "summaries": summaries,
        }
        summaries_path = scan_dir / "summaries.json"
        summaries_path.write_text(
            json.dumps(output, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
        logger.info(
            "Generated %d summaries for %s (%d errors)",
            len(summaries), project_id, len(errors),
        )

        return {
            "status": "completed",
            "summary_count": len(summaries),
            "error_count": len(errors),
            "errors": errors[:10],
        }

    @staticmethod
    def _derive_direction(path: str, job_name: str) -> str:
        """Derive data-flow direction (mirrors projects.py logic)."""
        path_lower = path.lower().replace("\\", "/")
        if "/in/" in path_lower:
            return "OnRamp"
        if "/out/" in path_lower:
            return "OffRamp"
        name_lower = job_name.lower()
        is_on = "onramp" in name_lower or "_onramp_" in name_lower
        is_off = (
            "offramp" in name_lower
            or "_offramp_" in name_lower
            or "_return_" in name_lower
        )
        if is_on and is_off:
            return "Bidirectional"
        if is_on:
            return "OnRamp"
        if is_off:
            return "OffRamp"
        return ""

    @staticmethod
    def load_summaries(project_id: str) -> dict[str, str]:
        """Load cached summaries.json for a project. Returns empty dict on failure."""
        summaries_path = (
            config.intodoc_output_dir / project_id / "scan" / "summaries.json"
        )
        if not summaries_path.exists():
            return {}
        try:
            data = json.loads(summaries_path.read_text(encoding="utf-8"))
            return data.get("summaries", {})
        except (json.JSONDecodeError, OSError, KeyError):
            return {}


# ---------------------------------------------------------------------------
# Component summaries (topology graph)
# ---------------------------------------------------------------------------
#
# Topology nodes are surfaced with a one-line "What This Component Does" field.
# Source priority for each job's summary:
#
#   1. Phase 2 group analysis prose  — written during doc generation, lives in
#      output/<project>/docs/_rag_support/phase2_analysis/<group>.md after the
#      docs_staging -> docs swap. Best signal: authored for this exact purpose.
#   2. scan_data.purpose              — intodoc's stored purpose string per job.
#   3. Improved heuristic             — connection flow shape + job name + system
#      names, with no repeating template.
#   4. Ollama (gemma3:12b)            — last-resort LLM pass if no source above
#      yields a usable sentence. Costs real time per job.
#
# All paths are deterministic and run async after scan / after Phase 2 swap.
# Results are persisted to scan/summaries.json (same file as SummaryService).
# The topology graph endpoint already reads from this file via load_summaries().

_PHASE2_HEADING_RE = re.compile(r"^#{1,4}\s+GROUP_SUMMARY\b", re.IGNORECASE | re.MULTILINE)
_PHASE2_UNIT_RE = re.compile(r"^#{2,5}\s+Unit:\s*[`'\"]?([^\n`'\"]+)[`'\"]?", re.IGNORECASE | re.MULTILINE)
_PHASE2_COVERAGE_RE = re.compile(r"^#{1,4}\s+Coverage\s*$", re.IGNORECASE | re.MULTILINE)
_PHASE2_PURPOSE_RE = re.compile(
    r"\*\*Purpose\*\*\s*[:\-]\s*(.+?)(?=\n\s*\n|\n\s*\*\*|\n\s*[-*+]|\Z)",
    re.DOTALL,
)
_PHASE2_MD_NOISE_RE = re.compile(r"^\s*[-*+]\s+|^\|.*\|$|^[-=]+$|^#+\s*", re.MULTILINE)


def _extract_first_useful_sentences(text: str, max_sentences: int = 2, max_chars: int = 300) -> str:
    """Return the first N substantive sentences from a Phase 2 prose blob."""
    if not text:
        return ""
    # Strip table rows / list bullets / heading markers for a clean read.
    cleaned = _PHASE2_MD_NOISE_RE.sub("", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    out: list[str] = []
    total = 0
    for sentence in sentences:
        s = sentence.strip()
        if len(s) < 20:
            continue
        out.append(s)
        total += len(s)
        if len(out) >= max_sentences or total >= max_chars:
            break
    joined = " ".join(out)
    if len(joined) > max_chars:
        joined = joined[:max_chars].rsplit(".", 1)[0] + "."
    return joined


def extract_phase2_summary_for_job(group_md: str, job_name: str) -> str:
    """Extract a 1-2 sentence summary for ``job_name`` from a Phase 2 group .md file.

    Phase 2 group .md files follow two common shapes:

    1. **Per-job Unit subsections** (``scripts.md`` shape) — a top-level
       ``## Coverage`` section listing covered jobs, followed by
       ``## Model Analysis`` with ``#### Unit: <name>`` subsections.
    2. **Group COMPONENTS bullet list** (``config.md`` / ``root.md`` shape) —
       a single ``### COMPONENTS`` section with bullet entries whose first
       item is ``- **Unit**: `<name>``` followed by ``**Purpose**: ...``.

    The extractor strips Coverage metadata, then tries in order: a matching
    ``#### Unit:`` subsection, a bullet-list entry whose **Unit** label
    matches, then the GROUP_SUMMARY section as a last resort.
    """
    if not group_md or not job_name:
        return ""

    # Strip out the Coverage metadata section so its bullets don't get matched
    # as "sentences" by the extractor when a job name appears there.
    coverage_match = _PHASE2_COVERAGE_RE.search(group_md)
    if coverage_match:
        tail = group_md[coverage_match.end():]
        tail = re.split(r"^#{1,4}\s+", tail, maxsplit=1, flags=re.MULTILINE)
        prose_without_coverage = (tail[1] if len(tail) > 1 else "")
    else:
        prose_without_coverage = group_md

    job_lower = job_name.lower()

    # Preferred: a `#### Unit: <job_name>` (or similar) subsection.
    unit_sections = re.split(r"(?=^#{2,5}\s+Unit:\s+)", prose_without_coverage, flags=re.MULTILINE)
    for section in unit_sections:
        match = _PHASE2_UNIT_RE.search(section)
        if match and match.group(1).strip().lower() == job_lower:
            purpose = _PHASE2_PURPOSE_RE.search(section)
            if purpose:
                cleaned = re.sub(r"\s+", " ", purpose.group(1)).strip()
                return _extract_first_useful_sentences(cleaned, max_sentences=2, max_chars=280)
            body = _PHASE2_MD_NOISE_RE.sub(" ", section)
            body = re.sub(r"\s+", " ", body).strip()
            extracted = _extract_first_useful_sentences(body, max_sentences=2, max_chars=280)
            if extracted:
                return extracted

    # Fallback: a COMPONENTS-style bullet entry for this job. The entry
    # starts with ``- **Unit**: `<name>``` and contains a ``**Purpose**: ...``
    # line within the next ~30 lines.
    purpose_for_unit = _find_purpose_for_unit_in_bullets(prose_without_coverage, job_lower)
    if purpose_for_unit:
        return _extract_first_useful_sentences(
            re.sub(r"\s+", " ", purpose_for_unit).strip(),
            max_sentences=2, max_chars=280,
        )

    # Final fallback: find any **Purpose**: ... line whose preceding context
    # (~600 chars back) mentions this job. Catches the "Detailed analysis
    # for <job>" pattern where Purpose lives in a non-bullet subsection.
    purpose_with_context = _find_nearest_purpose_to_job(prose_without_coverage, job_lower)
    if purpose_with_context:
        return _extract_first_useful_sentences(
            re.sub(r"\s+", " ", purpose_with_context).strip(),
            max_sentences=2, max_chars=280,
        )

    # Last resort: any section whose body explicitly names the job, skipping
    # one-line sections.
    sections = re.split(r"(?=^#{1,4}\s+)", prose_without_coverage, flags=re.MULTILINE)
    for section in sections:
        if job_lower not in section.lower():
            continue
        body = _PHASE2_MD_NOISE_RE.sub(" ", section)
        body = re.sub(r"\s+", " ", body).strip()
        if len(body) < 80:
            continue
        extracted = _extract_first_useful_sentences(body, max_sentences=2, max_chars=280)
        if extracted:
            return extracted

    # Note: we deliberately do NOT fall back to GROUP_SUMMARY here. That
    # section describes the whole group, not this specific job, and surfacing
    # it as a job summary is misleading. If no per-job prose exists the
    # downstream priority chain falls back to scan_data.purpose -> heuristic,
    # and the JobDetailPanel hides the section when the final string is empty.

    return ""


def _find_purpose_for_unit_in_bullets(prose: str, job_lower: str) -> str | None:
    """Locate the ``**Purpose**: ...`` line for ``job_lower`` in a COMPONENTS-style
    bullet list. The bullet entry starts with ``- **Unit**: `<name>``` and the
    Purpose line must appear before the next blank line or the next bullet
    entry begins.
    """
    pattern = re.compile(
        r"-\s+\*\*Unit\*\*\s*[:\-]\s*[`'\"]?" + re.escape(job_lower) + r"[`'\"]?",
        re.IGNORECASE,
    )
    match = pattern.search(prose)
    if not match:
        return None
    # Read forward from the match end up to the next blank line + bullet,
    # the next heading, or end of file — whichever comes first.
    chunk = prose[match.end():]
    lines: list[str] = []
    for line in chunk.splitlines():
        # Stop at next bullet entry (different job starts).
        if line.lstrip().startswith("- **Unit**"):
            break
        # Stop at any heading.
        if re.match(r"^#{1,6}\s+", line):
            break
        # Stop at blank line that precedes a new bullet.
        if not line.strip() and lines and lines[-1].strip().startswith("**"):
            break
        lines.append(line)
    purpose_match = _PHASE2_PURPOSE_RE.search("\n".join(lines))
    if purpose_match:
        return purpose_match.group(1).strip()
    return None


def _find_nearest_purpose_to_job(prose: str, job_lower: str, context_chars: int = 600) -> str | None:
    """Find a ``**Purpose**: ...`` line in the prose where the preceding
    ``context_chars`` characters contain ``job_lower``.

    Used as a last-resort fallback for files (e.g. ``root.md``) where each
    job's Purpose lives in a non-bullet subsection like
    ``**Detailed analysis for <job>**:`` followed by ``- **Purpose**: ...``.
    """
    best: tuple[int, str] | None = None
    for purpose_match in _PHASE2_PURPOSE_RE.finditer(prose):
        start = purpose_match.start()
        window = prose[max(0, start - context_chars):start]
        if job_lower in window.lower():
            # Prefer the FIRST Purpose whose preceding context mentions the job
            # (closest match). Update only if this is earlier than what we have.
            if best is None or start < best[0]:
                best = (start, purpose_match.group(1).strip())
    return best[1] if best else None


def heuristic_summary(
    job_name: str,
    direction: str,
    external_systems: list[str],
    component_types: list[str],
) -> str:
    """Improved non-LLM fallback for one-line component summaries.

    Differs from the legacy ``_derive_node_summary`` heuristic: no fixed
    "Reads from X. using Y, Z" template; output varies by job name and the
    actual flow shape. Returns ``""`` when the available signal is too thin to
    say anything meaningful (caller renders an empty state instead).
    """
    verb = {
        "OnRamp": "ingests",
        "OffRamp": "writes",
        "Bidirectional": "syncs",
    }.get(direction, "moves data for")

    systems = [s for s in external_systems if s][:3]
    notable_types = [
        t for t in component_types
        if t and t[:1].isalpha() and not t.startswith("tPrejob")
        and not t.startswith("tPostjob")
        and not t.startswith("tLog")
        and not t.startswith("tSetGlobal")
        and not t.startswith("tContext")
        and not t.startswith("tFixed")
        and not t.startswith("tFlowTo")
        and not t.startswith("tFileInput")
        and not t.startswith("tFileOutput")
    ][:3]

    if not systems and not notable_types:
        # Too thin to say anything useful; let the empty state render.
        return ""

    parts: list[str] = []
    if systems:
        if len(systems) == 1:
            parts.append(f"{verb} {systems[0]}")
        else:
            parts.append(f"{verb} {' and '.join(systems)}")
    else:
        parts.append(verb)

    if notable_types:
        if len(notable_types) == 1:
            parts.append(f"using {notable_types[0]}")
        else:
            parts.append(f"using {', '.join(notable_types[:-1])} and {notable_types[-1]}")

    sentence = " ".join(parts).strip()
    # Light touch-up: avoid "ingests X using Y" without a preposition.
    sentence = sentence.replace("ingests using", "processes with")
    sentence = sentence.replace("writes using", "loads via")
    sentence = sentence.replace("syncs using", "exchanges data via")
    if not sentence.endswith("."):
        sentence += "."
    return sentence


def _write_summaries_file(project_id: str, summaries: dict[str, str], source: str) -> None:
    """Persist the topology summaries cache. Overwrites the file atomically."""
    scan_dir = config.intodoc_output_dir / project_id / "scan"
    scan_dir.mkdir(parents=True, exist_ok=True)
    summaries_path = scan_dir / "summaries.json"
    payload = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "model": source,
        "job_count": len(summaries),
        "summaries": summaries,
    }
    tmp_path = summaries_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    tmp_path.replace(summaries_path)


def _scan_data_summary(project_id: str, job_groups: dict[str, str]) -> dict[str, str]:
    """Fall back to scan_data.purpose strings for each job."""
    out: dict[str, str] = {}
    jobs_dir = config.intodoc_output_dir / project_id / "scan" / "jobs"
    for job_name, group in job_groups.items():
        job_path = jobs_dir / group / f"{job_name}.json"
        if not job_path.exists():
            continue
        try:
            data = json.loads(job_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        purpose = (data.get("purpose") or "").strip()
        if purpose:
            extracted = _extract_first_useful_sentences(purpose, max_sentences=2, max_chars=280)
            if extracted:
                out[job_name] = extracted
    return out


def _heuristic_summaries(project_id: str, job_groups: dict[str, str], job_external_systems: dict[str, list[str]]) -> dict[str, str]:
    """Generate heuristic summaries for jobs that the prose + scan paths missed."""
    out: dict[str, str] = {}
    jobs_dir = config.intodoc_output_dir / project_id / "scan" / "jobs"
    for job_name, group in job_groups.items():
        job_path = jobs_dir / group / f"{job_name}.json"
        direction = SummaryService._derive_direction(str(job_path), job_name)
        ext = job_external_systems.get(job_name, [])
        comp_types: list[str] = []
        if job_path.exists():
            try:
                job_data = json.loads(job_path.read_text(encoding="utf-8"))
                seen: set[str] = set()
                for c in job_data.get("components", []):
                    ctype = c.get("component_type", "")
                    if ctype and ctype not in seen:
                        seen.add(ctype)
                        comp_types.append(ctype)
            except (json.JSONDecodeError, OSError):
                pass
        sentence = heuristic_summary(job_name, direction, ext, comp_types)
        if sentence:
            out[job_name] = sentence
    return out


def _phase2_prose_summaries(project_id: str, job_groups: dict[str, str]) -> dict[str, str]:
    """Pull per-job 1-2 sentence summaries from the persisted Phase 2 prose.

    Phase 2 group analyses are packaged into ``output/<project>/docs/_rag_support/phase2_analysis/``
    during Phase 4 cleanup and carried into ``docs/`` by the atomic swap
    (see ``rag_support_package.build_rag_support_package``).

    Reads each group file at most once, then extracts a purpose per job in
    that group. Previously this skipped re-extraction for jobs sharing a
    group, which only populated the first job per group.
    """
    # Use the canonical dirnames so we don't drift if either is renamed.
    from mcp_server.services.rag_support_package import (
        PHASE2_ANALYSIS_SUBDIR,
        RAG_SUPPORT_DIRNAME,
    )

    rag_dir = (
        config.project_root
        / "output"
        / project_id
        / "docs"
        / RAG_SUPPORT_DIRNAME
        / PHASE2_ANALYSIS_SUBDIR
    )
    if not rag_dir.exists():
        return {}

    out: dict[str, str] = {}
    jobs_by_group: dict[str, list[str]] = {}
    for job_name, group in job_groups.items():
        jobs_by_group.setdefault(group, []).append(job_name)

    for group, jobs in jobs_by_group.items():
        group_md_path = rag_dir / f"{group}.md"
        if not group_md_path.exists():
            continue
        try:
            group_md = group_md_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for job_name in jobs:
            sentence = extract_phase2_summary_for_job(group_md, job_name)
            if sentence:
                out[job_name] = sentence
    return out


async def build_component_summaries_async(
    project_id: str,
    log_mgr=None,
) -> dict[str, str]:
    """Build component summaries for the topology graph.

    Reads from Phase 2 prose (if available) -> scan_data.purpose -> improved
    heuristic. Persists to ``scan/summaries.json``. Safe to call repeatedly:
    each call rewrites the cache.

    Returns the final summary dict.
    """
    scan_dir = config.intodoc_output_dir / project_id / "scan"
    manifest_path = scan_dir / "manifest.json"
    if not manifest_path.exists():
        return {}

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    job_groups: dict[str, str] = manifest.get("job_groups", {}) or {}
    job_external_systems: dict[str, list[str]] = manifest.get("job_external_systems", {}) or {}
    if not job_groups:
        return {}

    summaries: dict[str, str] = {}
    source = "none"

    phase2 = _phase2_prose_summaries(project_id, job_groups)
    if phase2:
        summaries.update(phase2)
        source = "phase2_prose"

    if len(summaries) < len(job_groups):
        scan_purpose = _scan_data_summary(project_id, job_groups)
        for name, sentence in scan_purpose.items():
            summaries.setdefault(name, sentence)
        if scan_purpose and source == "none":
            source = "scan_data.purpose"

    if len(summaries) < len(job_groups):
        heuristic = _heuristic_summaries(project_id, job_groups, job_external_systems)
        for name, sentence in heuristic.items():
            summaries.setdefault(name, sentence)
        if heuristic and source == "none":
            source = "heuristic"

    if not summaries:
        return {}

    _write_summaries_file(project_id, summaries, source)
    logger.info(
        "Built %d/%d component summaries for %s (source=%s)",
        len(summaries), len(job_groups), project_id, source,
    )
    if log_mgr is not None:
        try:
            await log_mgr.emit(
                project_id,
                f"Component summaries updated: {len(summaries)}/{len(job_groups)} jobs "
                f"(source={source}).",
                level="info",
            )
        except Exception:
            pass
    return summaries
