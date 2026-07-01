"""True Patch Mode executor for Smart Reingestion Slice 4.

Replaces the legacy ``bounded_patch`` path (which patched Phase 3 docs
directly from raw git diff text, never updating Phase 2 analysis).

True patch lane flow
--------------------
1. Compute field-level diff (prev vs curr scan JSON) per group.
2. Derive Phase 2 sections to rewrite (platform-aware component map).
3. Call model for targeted Phase 2 patch; merge sections; G3 validate.
4. Write ONLY to ``docs/_rag_support/phase2_analysis/{group}.md``
   (Option A -- single-writer ADR preserved).
5. Call ``replace_analysis_cache_from_rag_support()`` ONCE to rebuild the
   flat ``analysis_cache/`` (sole ADR writer; no crash window).
6. Patch affected Phase 3 docs by heading replacement; G2/G2a validate;
   push to RAG.

Any per-group failure falls back to scoped_regen for that group.  Patch-
succeeded groups are never re-patched.

Guardrails
----------
G2  BR-{GROUP}-NNN tag sets in non-rewritten Phase 3 sections are byte-
    identical before and after.
G2a ``## Evidence Register`` is never added to headings_to_rewrite.
G3  ``group_analysis_is_complete`` + size + GROUP_SUMMARY presence run
    BEFORE any write.  On failure: write nothing, fall back.
G4  ``max_tokens`` capped to ``2000 * len(sections_to_rewrite)`` (Phase 2)
    and ``2500 * len(headings_to_rewrite)`` (Phase 3) to prevent truncation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp_server.config import config
from mcp_server.providers.custom.analysis_parser import (
    PHASE2_SECTION_NAMES,
    parse_phase2_sections,
)
from mcp_server.providers.custom.models import ModelOptions
from mcp_server.providers.custom.prompt_renderer import PromptRenderer
from mcp_server.providers.custom.validators import group_analysis_is_complete
from mcp_server.services.git_diff_patcher import _patch_provider, _replace_section
from mcp_server.services.log_manager import LogManager
from mcp_server.services.patch_section_map import (
    EVIDENCE_REGISTER_HEADING,
    FieldChange,
    docs_for_sections,
    sections_for_delta,
)
from mcp_server.services.rag_support_package import (
    MANIFEST_FILENAME,
    PHASE2_ANALYSIS_SUBDIR,
    RAG_SUPPORT_DIRNAME,
    inspect_generation_cache_readiness,
    replace_analysis_cache_from_rag_support,
)
from mcp_server.services.rag_bridge import RagBridge, rag_push_succeeded

logger = logging.getLogger(__name__)

# THIN floor used by CorrectnessGate / G3 validation
_THIN_FLOOR_BYTES: int = 500  # matches EvidenceQualityGate.THIN_THRESHOLD_BYTES


# ---------------------------------------------------------------------------
# Scan job helpers (private to this module)
# ---------------------------------------------------------------------------


def _load_prev_jobs(output_dir: Path, group: str) -> list[dict] | None:
    """Return prev-scan job list for ``group``, or None if the file is absent."""
    path = output_dir / "prev_scan" / f"{group}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("TruePatch: cannot read prev scan for %s: %s", group, exc)
        return None


def _load_curr_jobs(project_id: str, group: str) -> list[dict]:
    """Return current scan job list for ``group`` from the intodoc output dir."""
    jobs_dir = config.intodoc_output_dir / project_id / "scan" / "jobs" / group
    if not jobs_dir.is_dir():
        return []
    jobs: list[dict] = []
    for f in sorted(jobs_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                jobs.append(data)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "TruePatch: cannot read curr scan file %s for %s: %s",
                f.name, group, exc,
            )
    return jobs


def _component_type_map_for(jobs: list[dict]) -> dict[tuple[str, str], str]:
    """Return {(job_name, unique_name): component_type} for all components."""
    result: dict[tuple[str, str], str] = {}
    for job in jobs:
        if not isinstance(job, dict):
            continue
        job_name = str(job.get("name") or "")
        for comp in (job.get("components") or []):
            if not isinstance(comp, dict):
                continue
            unique_name = str(comp.get("unique_name") or "")
            comp_type = str(comp.get("component_type") or "")
            if job_name and unique_name:
                result[(job_name, unique_name)] = comp_type
    return result


def _compute_field_diff(
    prev_jobs: list[dict],
    curr_jobs: list[dict],
) -> tuple[list[FieldChange], str]:
    """Return (field_changes, human-readable diff text).

    ``field_changes`` carries component_type for ``sections_for_delta``.
    ``diff_text`` is passed verbatim into the Phase 2 patch prompt.
    """
    from mcp_server.services.group_delta_classifier import extract_field_map

    prev_fields = extract_field_map(prev_jobs)
    curr_fields = extract_field_map(curr_jobs)
    curr_comp_types = _component_type_map_for(curr_jobs)
    prev_comp_types = _component_type_map_for(prev_jobs)

    changes: list[FieldChange] = []
    diff_lines: list[str] = []

    for key, curr_val in curr_fields.items():
        job_name, unique_name, param_name = key
        ct = curr_comp_types.get((job_name, unique_name))
        if key in prev_fields:
            if prev_fields[key] != curr_val:
                changes.append(FieldChange(
                    field_path=f"({job_name}, {unique_name}, {param_name})",
                    old_value=prev_fields[key],
                    new_value=curr_val,
                    component_type=ct,
                ))
                ct_tag = f" [type={ct}]" if ct else ""
                diff_lines.append(
                    f"Changed{ct_tag}: job={job_name!r} component={unique_name!r}"
                    f" param={param_name!r}: {prev_fields[key]!r} -> {curr_val!r}"
                )
        else:
            changes.append(FieldChange(
                field_path=f"({job_name}, {unique_name}, {param_name})",
                old_value=None,
                new_value=curr_val,
                component_type=ct,
            ))
            ct_tag = f" [type={ct}]" if ct else ""
            diff_lines.append(
                f"Added{ct_tag}: job={job_name!r} component={unique_name!r}"
                f" param={param_name!r}: (absent) -> {curr_val!r}"
            )

    # Params removed from stable units
    for key in sorted(set(prev_fields) - set(curr_fields)):
        job_name, unique_name, param_name = key
        ct = prev_comp_types.get((job_name, unique_name))
        changes.append(FieldChange(
            field_path=f"({job_name}, {unique_name}, {param_name})",
            old_value=prev_fields[key],
            new_value=None,
            component_type=ct,
        ))
        ct_tag = f" [type={ct}]" if ct else ""
        diff_lines.append(
            f"Removed{ct_tag}: job={job_name!r} component={unique_name!r}"
            f" param={param_name!r}: {prev_fields[key]!r} -> (removed)"
        )

    diff_text = "\n".join(diff_lines) or "(no field changes detected)"
    return changes, diff_text


# ---------------------------------------------------------------------------
# Analysis text helpers
# ---------------------------------------------------------------------------


def _split_analysis(text: str) -> tuple[str, dict[str, str]]:
    """Split a Phase 2 analysis text into (preamble, sections).

    ``preamble`` is everything before the first ``### SECTION_NAME`` heading
    (i.e. the ``# Phase 2 Analysis:`` header, ``## Coverage`` block, and
    ``## Model Analysis`` wrapper opening line).
    ``sections`` maps section_name -> content (heading line excluded).
    """
    h3_re = re.compile(r"^### [A-Z]", re.MULTILINE)
    m = h3_re.search(text)
    if m:
        preamble = text[: m.start()].rstrip()
        body = text[m.start():]
    else:
        preamble = text.rstrip()
        body = ""
    sections = parse_phase2_sections(body) if body else {}
    return preamble, sections


def _reassemble_analysis(preamble: str, sections: dict[str, str]) -> str:
    """Reassemble a full Phase 2 analysis from preamble + section dict.

    Emits sections in ``PHASE2_SECTION_NAMES`` order.  Adds ``### NAME``
    headings back (they were stripped by ``parse_phase2_sections``).
    """
    emitted: set[str] = set()
    parts: list[str] = [preamble]
    for name in PHASE2_SECTION_NAMES:
        if name in sections:
            parts.append(f"\n\n### {name}\n\n{sections[name]}")
            emitted.add(name)
    for name, content in sections.items():
        if name not in emitted:
            parts.append(f"\n\n### {name}\n\n{content}")
    return "".join(parts).strip() + "\n"


def _analysis_text_is_valid(text: str, platform: str) -> bool:
    """G3 inline validation -- avoids writing to disk unless temp file is needed.

    Replicates the logic of ``group_analysis_is_complete`` on a string.
    Falls back to a temp file for the full check.
    """
    if len(text.encode("utf-8")) <= _THIN_FLOOR_BYTES:
        return False
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    try:
        return group_analysis_is_complete(tmp_path, platform, require_sections=True)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Doc-level invariant helpers (G2 / G2a)
# ---------------------------------------------------------------------------


def _extract_h2_sections(text: str) -> dict[str, str]:
    """Return {heading_title: full section text} for each ``## Heading``."""
    h2_re = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
    headings = list(h2_re.finditer(text))
    if not headings:
        return {}
    result: dict[str, str] = {}
    for idx, m in enumerate(headings):
        title = m.group(1).strip()
        start = m.start()
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
        result[title] = text[start:end]
    return result


def _br_tags(text: str) -> set[str]:
    return set(re.findall(r"\bBR-[A-Za-z0-9_-]+-\d+\b", text))


def _assert_invariants_preserved(
    original_doc: str,
    patched_doc: str,
    rewritten_headings: set[str],
) -> bool:
    """Return True when G2 and G2a invariants hold between original and patched.

    G2:  For every h2 section NOT in ``rewritten_headings``, the BR-tag set
         must be identical before and after.
    G2a: ``## Evidence Register`` must be byte-identical (regardless of
         whether it appears in ``rewritten_headings``).
    """
    orig_sections = _extract_h2_sections(original_doc)
    patched_sections = _extract_h2_sections(patched_doc)

    for title, orig_content in orig_sections.items():
        if title == EVIDENCE_REGISTER_HEADING:
            if orig_content != patched_sections.get(title, ""):
                logger.warning(
                    "TruePatch G2a: Evidence Register changed after patch"
                )
                return False
            continue
        if title in rewritten_headings:
            continue
        if _br_tags(orig_content) != _br_tags(patched_sections.get(title, "")):
            logger.warning(
                "TruePatch G2: BR-tag violation in non-rewritten section %r", title
            )
            return False
    return True


# ---------------------------------------------------------------------------
# Manifest entry update (support the Option A atomic write)
# ---------------------------------------------------------------------------


def _update_manifest_entry(output_dir: Path, group: str, data: bytes) -> None:
    """Atomically update the group's sha256/bytes entry in manifest.json."""
    manifest_path = output_dir / "docs" / RAG_SUPPORT_DIRNAME / MANIFEST_FILENAME
    manifest: dict = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}

    artifacts: list[dict] = manifest.get("artifacts") or []
    sha = hashlib.sha256(data).hexdigest()
    byte_count = len(data)
    found = False
    for art in artifacts:
        if art.get("group") == group:
            art["sha256"] = sha
            art["bytes"] = byte_count
            found = True
            break
    if not found:
        artifacts.append({
            "path": f"{PHASE2_ANALYSIS_SUBDIR}/{group}.md",
            "source_path": f"_analysis/{group}.md",
            "source_layer": "technical_analysis",
            "artifact_type": "phase2_group_analysis",
            "visibility": "internal",
            "group": group,
            "bytes": byte_count,
            "sha256": sha,
        })
    manifest["artifacts"] = artifacts

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    tmp.replace(manifest_path)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class PatchQualityError(RuntimeError):
    """Raised when a Phase 2 patch fails G3 validation (write nothing, fall back)."""


@dataclass(frozen=True)
class PatchResult:
    """Outcome of a ``TruePatchExecutor.run()`` call."""

    action: str  # "true_patch" | "scoped_regen_fallback" | "failed"
    patched_groups: list[str]
    patched_docs: list[str]
    summary: dict[str, Any]
    advance_commit: bool
    error: str | None = None


# ---------------------------------------------------------------------------
# TruePatchExecutor
# ---------------------------------------------------------------------------


class TruePatchExecutor:
    """Field-level patch executor for the ``bounded_patch`` routing lane.

    Replaces ``_run_diff_patch`` for the patch lane.  Does NOT call
    ``run_git_reingestion`` -- the caller (auto_doc_update._true_patch) is
    responsible for fallback routing when ``result.action == "scoped_regen_fallback"``.

    NOTE: Patch mode is NOT safe for concurrent execution on the same project.
    ``AutoDocUpdateService`` serialises per-project at the webhook/poller entry
    point (one active run per project at any time).
    """

    def __init__(self, *, doc_generation_provider: str | None = None) -> None:
        self._doc_generation_provider = doc_generation_provider
        self._renderer = PromptRenderer()

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(
        self,
        *,
        project_id: str,
        output_dir: Path,
        changed_groups: list[str],
        platform: str,
        log_mgr: LogManager,
        before_sha: str = "",
        after_sha: str = "",
        trigger: str = "webhook",
    ) -> PatchResult:
        """Execute the true patch lane for ``changed_groups``.

        Returns a ``PatchResult``; never raises.
        """
        provider = _patch_provider(self._doc_generation_provider)
        group_analyses: dict[str, str] = {}       # group -> merged text (after G3)
        group_sections: dict[str, frozenset[str]] = {}  # group -> sections_to_rewrite
        fallback_groups: list[str] = []

        # ── Phase 2 patches (per group) ────────────────────────────────────
        for group in changed_groups:
            await log_mgr.emit(
                project_id, f"TruePatch: Phase 2 patch for group {group!r}", "info"
            )
            try:
                merged, sections = await self._patch_phase2(
                    group=group,
                    project_id=project_id,
                    output_dir=output_dir,
                    platform=platform,
                    provider=provider,
                )
                group_analyses[group] = merged
                group_sections[group] = sections
            except PatchQualityError as exc:
                logger.warning("TruePatch G3 failure for %s (fallback): %s", group, exc)
                await log_mgr.emit(
                    project_id,
                    f"TruePatch: G3 quality gate failed for {group!r}; routing to scoped_regen",
                    "warning",
                )
                fallback_groups.append(group)
            except Exception as exc:
                logger.exception("TruePatch: Phase 2 failed for group %s", group)
                await log_mgr.emit(
                    project_id,
                    f"TruePatch: Phase 2 error for {group!r} ({exc}); routing to scoped_regen",
                    "warning",
                )
                fallback_groups.append(group)

        if not group_analyses:
            return PatchResult(
                action="scoped_regen_fallback",
                patched_groups=[],
                patched_docs=[],
                summary={"fallback_groups": fallback_groups,
                         "reason": "all groups failed G3 validation"},
                advance_commit=False,
                error="All patch groups failed validation; scoped_regen required",
            )

        # ── Write support files (Option A) + cache reconcile ───────────────
        try:
            for group, text in group_analyses.items():
                await asyncio.to_thread(
                    self._write_support_analysis, output_dir, group, text
                )
            await asyncio.to_thread(
                replace_analysis_cache_from_rag_support, output_dir
            )
        except Exception as exc:
            logger.exception("TruePatch: support write or cache reconcile failed")
            return PatchResult(
                action="failed",
                patched_groups=[],
                patched_docs=[],
                summary={"reason": str(exc)},
                advance_commit=False,
                error=f"Support write failed: {exc}",
            )

        # ── Option A post-reconcile assertion ──────────────────────────────
        readiness = await asyncio.to_thread(
            inspect_generation_cache_readiness,
            output_dir,
            changed_groups=list(group_analyses),
        )
        if not readiness.cache_support_match:
            err = (
                f"TruePatch: cache/support mismatch after reconcile "
                f"(reason_codes={readiness.reason_codes})"
            )
            logger.error(err)
            await log_mgr.emit(project_id, err, "error")
            return PatchResult(
                action="scoped_regen_fallback",
                patched_groups=[],
                patched_docs=[],
                summary={"reason": err, "reason_codes": readiness.reason_codes},
                advance_commit=False,
                error=err,
            )

        patched_groups_so_far = list(group_analyses)

        # ── Phase 3 patches (per affected doc) ─────────────────────────────
        all_sections: frozenset[str] = frozenset().union(
            *(group_sections[g] for g in patched_groups_so_far)
        )
        docs_to_patch = docs_for_sections(all_sections)
        docs_dir = output_dir / "docs"
        patched_docs: list[str] = []
        p3_fallback: set[str] = set()

        for doc_name in docs_to_patch:
            doc_path = docs_dir / f"{doc_name}.md"
            if not doc_path.is_file():
                continue

            original_text = doc_path.read_text(encoding="utf-8", errors="replace")
            current_text = original_text

            doc_entry = self._get_doc_entry(doc_name)
            if doc_entry is None:
                continue

            headings = [
                h for h in doc_entry.required_sections
                if h != EVIDENCE_REGISTER_HEADING
            ]
            if not headings:
                continue

            doc_modified = False
            group_p3_fail: set[str] = set()

            for group in patched_groups_so_far:
                relevant_sections = group_sections.get(group, frozenset()) & set(
                    doc_entry.phase2_sections
                )
                if not relevant_sections:
                    continue

                updated_excerpt = self._extract_sections(
                    group_analyses[group], relevant_sections
                )
                try:
                    new_text = await self._patch_phase3(
                        doc_name=doc_name,
                        group=group,
                        platform=platform,
                        headings_to_rewrite=headings,
                        updated_analysis_sections=updated_excerpt,
                        existing_doc=current_text,
                        provider=provider,
                    )
                except Exception as exc:
                    logger.warning(
                        "TruePatch: Phase 3 failed for doc %s group %s: %s",
                        doc_name, group, exc,
                    )
                    group_p3_fail.add(group)
                    continue

                if not _assert_invariants_preserved(current_text, new_text, set(headings)):
                    logger.warning(
                        "TruePatch: G2 violation for doc %s group %s; restoring",
                        doc_name, group,
                    )
                    group_p3_fail.add(group)
                    continue

                current_text = new_text
                doc_modified = True

            if group_p3_fail:
                # Do NOT write the partial doc if any group's patch violated invariants.
                p3_fallback |= group_p3_fail
            elif doc_modified:
                await asyncio.to_thread(
                    doc_path.write_text, current_text, "utf-8"
                )
                patched_docs.append(f"{doc_name}.md")

        fallback_groups.extend(
            g for g in p3_fallback if g not in fallback_groups
        )

        # ── RAG push ───────────────────────────────────────────────────────
        if patched_docs:
            bridge = RagBridge(log_manager=log_mgr)
            try:
                rag_result = await bridge.push_docs(
                    project_id=project_id,
                    output_dir=str(docs_dir),
                    filenames=set(patched_docs),
                )
            except Exception as exc:
                logger.warning("TruePatch: RAG push raised: %s", exc)
                return PatchResult(
                    action="scoped_regen_fallback",
                    patched_groups=[],
                    patched_docs=[],
                    summary={"reason": f"RAG push failed: {exc}"},
                    advance_commit=False,
                    error=f"RAG push failed: {exc}",
                )
            if not rag_push_succeeded(rag_result, expected_count=len(patched_docs)):
                err = f"RAG sync incomplete: {rag_result.get('errors')}"
                return PatchResult(
                    action="scoped_regen_fallback",
                    patched_groups=[],
                    patched_docs=[],
                    summary={"reason": err},
                    advance_commit=False,
                    error=err,
                )

        final_action = (
            "true_patch" if not fallback_groups else "scoped_regen_fallback"
        )
        await log_mgr.emit(
            project_id,
            f"TruePatch: {final_action} | patched={patched_groups_so_far} "
            f"docs={patched_docs} fallback={fallback_groups}",
            "info",
        )
        return PatchResult(
            action=final_action,
            patched_groups=patched_groups_so_far,
            patched_docs=patched_docs,
            summary={
                "true_patch": True,
                "patched_groups": patched_groups_so_far,
                "patched_docs": patched_docs,
                "fallback_groups": fallback_groups,
                "reason_code": (
                    "true_patch_succeeded"
                    if final_action == "true_patch"
                    else "partial_patch_fallback"
                ),
            },
            advance_commit=(final_action == "true_patch"),
            error=(
                None
                if final_action == "true_patch"
                else f"Fallback groups: {fallback_groups}"
            ),
        )

    # ------------------------------------------------------------------
    # Phase 2
    # ------------------------------------------------------------------

    async def _patch_phase2(
        self,
        *,
        group: str,
        project_id: str,
        output_dir: Path,
        platform: str,
        provider,
    ) -> tuple[str, frozenset[str]]:
        """Patch the Phase 2 analysis for one group.

        Returns (merged_analysis_text, sections_to_rewrite).
        Raises PatchQualityError if G3 validation fails (caller routes to fallback).
        NOTHING is written to disk here; writing is deferred to _write_support_analysis.
        """
        # 1. Load scan data
        prev_jobs = await asyncio.to_thread(_load_prev_jobs, output_dir, group)
        if not prev_jobs:
            raise PatchQualityError(
                f"No (or empty) prev scan for group {group!r}"
            )
        curr_jobs = await asyncio.to_thread(_load_curr_jobs, project_id, group)
        if not curr_jobs:
            raise PatchQualityError(f"Empty curr scan for group {group!r}")

        # 2. Field diff
        field_changes, field_diff_text = await asyncio.to_thread(
            _compute_field_diff, prev_jobs, curr_jobs
        )
        if not field_changes:
            raise PatchQualityError(f"No field changes found for group {group!r}")

        # 3. Sections to rewrite
        sections_to_rewrite = sections_for_delta(platform, field_changes)

        # 4. Load existing analysis from published support file
        support_path = (
            output_dir
            / "docs"
            / RAG_SUPPORT_DIRNAME
            / PHASE2_ANALYSIS_SUBDIR
            / f"{group}.md"
        )
        if not support_path.is_file():
            raise PatchQualityError(
                f"No published support analysis for group {group!r} at {support_path}"
            )
        existing_analysis = await asyncio.to_thread(
            support_path.read_text, "utf-8", "replace"
        )
        preamble, existing_sections = _split_analysis(existing_analysis)

        # 5. Render prompt (G4: per-section token budget)
        prompt = self._renderer.render("phase2_patch.md", {
            "GROUP": group,
            "PLATFORM": platform,
            "SECTIONS_TO_REWRITE": "\n".join(f"- {s}" for s in sorted(sections_to_rewrite)),
            "FIELD_DIFF": field_diff_text,
            "EXISTING_ANALYSIS": existing_analysis,
        })
        max_tokens = min(config.phase2_max_tokens, 2000 * len(sections_to_rewrite))

        # 6. Call model
        result = await provider.complete(
            prompt,
            options=ModelOptions(
                max_tokens=max_tokens,
                temperature=0.0,
                timeout_s=240.0,
                retries=1,
            ),
        )
        if not result.success:
            raise PatchQualityError(
                f"Model call failed for group {group!r}: {result.raw_error}"
            )

        # 7. Parse and merge
        model_sections = parse_phase2_sections(result.text.strip())
        merged_sections = dict(existing_sections)
        for name in sections_to_rewrite:
            if name in model_sections:
                merged_sections[name] = model_sections[name]
            # Section in sections_to_rewrite but NOT returned: keep existing.

        # Discard any sections the model returned that are OUTSIDE sections_to_rewrite.
        for name in list(merged_sections):
            if name in model_sections and name not in sections_to_rewrite:
                # Restore to original (model is not permitted to rewrite this)
                merged_sections[name] = existing_sections.get(name, "")

        merged_text = _reassemble_analysis(preamble, merged_sections)

        # 8. G3 validate-before-return
        if not await asyncio.to_thread(_analysis_text_is_valid, merged_text, platform):
            raise PatchQualityError(
                f"G3 validation failed for group {group!r}: "
                "merged analysis thin, incomplete, or missing GROUP_SUMMARY"
            )

        return merged_text, sections_to_rewrite

    # ------------------------------------------------------------------
    # Phase 3
    # ------------------------------------------------------------------

    async def _patch_phase3(
        self,
        *,
        doc_name: str,
        group: str,
        platform: str,
        headings_to_rewrite: list[str],
        updated_analysis_sections: str,
        existing_doc: str,
        provider,
    ) -> str:
        """Patch one Phase 3 doc for the given group.

        Returns the patched doc text (caller validates invariants before writing).
        """
        if not headings_to_rewrite:
            return existing_doc

        prompt = self._renderer.render("phase3_patch.md", {
            "DOC_NAME": doc_name,
            "GROUP": group,
            "PLATFORM": platform,
            "HEADINGS_TO_REWRITE": "\n".join(f"- {h}" for h in headings_to_rewrite),
            "UPDATED_ANALYSIS_SECTIONS": updated_analysis_sections,
            "EXISTING_DOC": existing_doc,
        })
        max_tokens = min(config.phase3_max_tokens, 2500 * len(headings_to_rewrite))
        result = await provider.complete(
            prompt,
            options=ModelOptions(
                max_tokens=max_tokens,
                temperature=0.0,
                timeout_s=240.0,
                retries=1,
            ),
        )
        if not result.success:
            raise RuntimeError(
                f"Model call failed for doc {doc_name!r}: {result.raw_error}"
            )

        # Parse returned headings and apply via _replace_section (G2a: always exclude
        # Evidence Register from the allowed set even if the model returns it).
        allowed = set(headings_to_rewrite) - {EVIDENCE_REGISTER_HEADING}
        returned = self._parse_doc_headings(result.text.strip())
        patched = existing_doc
        for heading, content in returned.items():
            if heading not in allowed:
                continue  # Discard out-of-scope headings
            replacement = f"## {heading}\n\n{content}"
            patched = _replace_section(patched, heading, replacement)
        return patched

    # ------------------------------------------------------------------
    # Support file write (Option A -- single location)
    # ------------------------------------------------------------------

    @staticmethod
    def _write_support_analysis(output_dir: Path, group: str, text: str) -> None:
        """Write validated analysis to ``docs/_rag_support/phase2_analysis/{group}.md``.

        Does NOT write to ``analysis_cache/`` directly -- the cache is rebuilt by
        ``replace_analysis_cache_from_rag_support()`` in ``run()`` after all groups
        are written.  Atomic write-then-rename to prevent half-written files.
        """
        support_path = (
            output_dir
            / "docs"
            / RAG_SUPPORT_DIRNAME
            / PHASE2_ANALYSIS_SUBDIR
            / f"{group}.md"
        )
        support_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = support_path.with_suffix(".md.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(support_path)
        _update_manifest_entry(output_dir, group, text.encode("utf-8"))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_doc_entry(doc_name: str):
        from mcp_server.providers.custom.doc_catalog import load_doc_catalog
        catalog = load_doc_catalog()
        return next((e for e in catalog.docs if e.name == doc_name), None)

    @staticmethod
    def _extract_sections(analysis_text: str, sections: frozenset[str]) -> str:
        """Return markdown for only the requested sections (with headings)."""
        parsed = parse_phase2_sections(analysis_text)
        parts: list[str] = []
        for name in PHASE2_SECTION_NAMES:
            if name in sections and name in parsed:
                parts.append(f"### {name}\n\n{parsed[name]}")
        return "\n\n".join(parts)

    @staticmethod
    def _parse_doc_headings(text: str) -> dict[str, str]:
        """Parse model Phase 3 response into {heading_title: section_content}."""
        h2_re = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
        headings = list(h2_re.finditer(text))
        if not headings:
            return {}
        result: dict[str, str] = {}
        for idx, m in enumerate(headings):
            title = m.group(1).strip()
            start = m.end()
            end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
            result[title] = text[start:end].strip()
        return result
