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
6. Patch affected Phase 3 (group-scoped) docs by heading replacement;
   G2/G2a validate; push to RAG.
7. Patch affected Phase 3 SUMMARY/cross-cutting docs (Living-Document
   Reingestion Slice 3) -- documents with no owning group, e.g.
   ``09_EXECUTIVE_SUMMARY`` (``GroupDocDependency.cross_cutting_docs()``).
   ``SynthesisImpactMapper`` maps each patched group's delta to the
   specific ``## Heading`` sections of the summary doc that reference it;
   only those headings are rewritten (same heading-replacement + G2/G2a
   mechanism as step 6, plus G5 and a removal-completeness check). A
   summary document that cannot be safely mapped is left untouched and
   reported in ``PatchResult.summary["synthesis_fallback_docs"]`` for
   scoped-local re-derivation of that document alone -- never a whole-repo
   rebuild. See ``services/synthesis_impact_mapper.py``.

Any per-group failure falls back to scoped_regen for that group.  Patch-
succeeded groups are never re-patched.

Guardrails
----------
G2  BR-{GROUP}-NNN tag sets in non-rewritten Phase 3 sections (group-scoped
    AND summary docs) are byte-identical before and after.
G2a ``## Evidence Register`` is never added to headings_to_rewrite.
G3  ``group_analysis_is_complete`` + size + GROUP_SUMMARY presence run
    BEFORE any write.  On failure: write nothing, fall back.
G4  ``max_tokens`` capped to ``2000 * len(sections_to_rewrite)`` (Phase 2)
    and ``2500 * len(headings_to_rewrite)`` (Phase 3) to prevent truncation.
G5  Validate-against-diff (Living-Document Reingestion Slice 2): every
    mechanically-checkable fact (unit/component/param name, config key,
    connection name) the model's rewritten sections assert must be
    reconcilable with the group's field diff or the section's pre-existing
    content.  Runs BEFORE G3, BEFORE any write.  On failure: write nothing,
    raise with ``reason_code="diff_fidelity_failed"``, fall back to
    scoped_regen for that group.  See ``services/diff_fidelity_guard.py``.
    Reused verbatim (per-heading) for summary-doc amendment.
G6  Removal completeness (Living-Document Reingestion Slice 3, summary docs
    only): when a group's delta fully removes a unit, none of that unit's
    identifiers may survive into a rewritten summary-doc heading.  On
    failure: discard the rewrite, leave the summary doc untouched, report
    it for scoped-local re-derivation.  See
    ``services/synthesis_impact_mapper.verify_removal``.
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
from mcp_server.services.diff_fidelity_guard import find_unreconciled_facts
from mcp_server.services.git_diff_patcher import _patch_provider, _replace_section
from mcp_server.services.group_doc_dependency import (
    DOC_PACK_MANIFEST_FILENAME,
    GroupDocDependency,
)
from mcp_server.services.log_manager import LogManager
from mcp_server.services.patch_section_map import (
    EVIDENCE_REGISTER_HEADING,
    FieldChange,
    docs_for_sections,
    extract_h2_sections,
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
from mcp_server.services.synthesis_impact_mapper import (
    SynthesisImpactMapper,
    doc_references_group,
    removed_unit_identifiers,
    verify_removal,
)

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


def _load_curr_jobs(
    project_id: str,
    group: str,
    *,
    scan_output_dir: Path | None = None,
) -> list[dict]:
    """Return current scan jobs, optionally from an isolated candidate scan.

    Existing callers keep the published project scan as their source.  The
    living-document coordinator passes its candidate output root so a patch can
    be fully derived and validated before it touches the active revision.
    """
    scan_root = scan_output_dir or (config.intodoc_output_dir / project_id)
    jobs_dir = scan_root / "scan" / "jobs" / group
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
    orig_sections = extract_h2_sections(original_doc)
    patched_sections = extract_h2_sections(patched_doc)

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
    """Raised when a Phase 2 patch fails a pre-write quality gate (G3 or G5).

    ``reason_code`` is a stable machine-readable cause distinct from the
    free-text message, so callers can tell a G5 diff-fidelity rejection
    apart from a G3 completeness failure or a precondition failure (missing
    scan data, missing support file, model error) without string-matching.
    Write nothing on this error; the caller falls back to scoped_regen.
    """

    def __init__(self, message: str, *, reason_code: str = "g3_validation_failed") -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class PatchResult:
    """Outcome of a ``TruePatchExecutor.run()`` call."""

    action: str  # "true_patch" | "scoped_regen_fallback" | "failed"
    patched_groups: list[str]
    patched_docs: list[str]
    summary: dict[str, Any]
    advance_commit: bool
    error: str | None = None


@dataclass(frozen=True)
class _Phase2Patch:
    """Internal result of :meth:`TruePatchExecutor._patch_phase2`.

    Carries ``field_changes`` and ``removed_identifiers`` forward so the
    Slice-3 summary-doc amendment step does not need to reload or recompute
    the field diff -- the same diff already validated by G3/G5 is reused
    for the synthesis impact mapping.
    """

    text: str
    sections: frozenset[str]
    field_changes: list[FieldChange]
    removed_identifiers: frozenset[str]


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
        scan_output_dir: Path | None = None,
        publish_rag: bool = True,
    ) -> PatchResult:
        """Execute the true patch lane for ``changed_groups``.

        Returns a ``PatchResult``; never raises.
        """
        provider = _patch_provider(self._doc_generation_provider)
        group_analyses: dict[str, str] = {}       # group -> merged text (after G3)
        group_sections: dict[str, frozenset[str]] = {}  # group -> sections_to_rewrite
        # group -> validated field diff + fully-removed unit identifiers, carried
        # forward for the Slice-3 summary-doc synthesis impact mapping (Phase 3b)
        # so it is never recomputed.
        group_field_changes: dict[str, list[FieldChange]] = {}
        group_removed_ids: dict[str, frozenset[str]] = {}
        fallback_groups: list[str] = []
        # group -> stable reason_code (e.g. "diff_fidelity_failed", "g3_validation_failed").
        # Distinguishes a G5 fact-check rejection from every other fallback cause
        # without string-matching the free-text error (Slice 2 AC3).
        fallback_reasons: dict[str, str] = {}

        # ── Phase 2 patches (per group) ────────────────────────────────────
        for group in changed_groups:
            await log_mgr.emit(
                project_id, f"TruePatch: Phase 2 patch for group {group!r}", "info"
            )
            try:
                patch = await self._patch_phase2(
                    group=group,
                    project_id=project_id,
                    output_dir=output_dir,
                    scan_output_dir=scan_output_dir,
                    platform=platform,
                    provider=provider,
                )
                group_analyses[group] = patch.text
                group_sections[group] = patch.sections
                group_field_changes[group] = patch.field_changes
                group_removed_ids[group] = patch.removed_identifiers
            except PatchQualityError as exc:
                logger.warning(
                    "TruePatch quality gate failure for %s (reason_code=%s, fallback): %s",
                    group, exc.reason_code, exc,
                )
                await log_mgr.emit(
                    project_id,
                    f"TruePatch: quality gate {exc.reason_code!r} failed for {group!r}; "
                    "routing to scoped_regen",
                    "warning",
                )
                fallback_groups.append(group)
                fallback_reasons[group] = exc.reason_code
            except Exception as exc:
                logger.exception("TruePatch: Phase 2 failed for group %s", group)
                await log_mgr.emit(
                    project_id,
                    f"TruePatch: Phase 2 error for {group!r} ({exc}); routing to scoped_regen",
                    "warning",
                )
                fallback_groups.append(group)
                fallback_reasons[group] = "phase2_patch_error"

        if not group_analyses:
            return PatchResult(
                action="scoped_regen_fallback",
                patched_groups=[],
                patched_docs=[],
                summary={
                    "fallback_groups": fallback_groups,
                    "fallback_reasons": fallback_reasons,
                    "reason": "all groups failed quality gates",
                },
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
                    fallback_reasons.setdefault(group, "phase3_patch_error")
                    continue

                if not _assert_invariants_preserved(current_text, new_text, set(headings)):
                    logger.warning(
                        "TruePatch: G2 violation for doc %s group %s; restoring",
                        doc_name, group,
                    )
                    group_p3_fail.add(group)
                    fallback_reasons.setdefault(group, "g2_invariant_violation")
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

        # ── Phase 3b: summary/cross-cutting doc amendment (Slice 3) ────────
        # Documents with no owning group (GroupDocDependency.cross_cutting_docs(),
        # e.g. 09_EXECUTIVE_SUMMARY) are amended in place from the SAME validated
        # group deltas, instead of being left stale or force-regenerated. A
        # document that cannot be safely expressed as a section-level edit is
        # left byte-unchanged and reported in synthesis_fallback_docs for
        # scoped-local re-derivation of that document ALONE -- never the whole
        # affected set, never a whole-repo rebuild (RD-05).
        synthesis_patched_docs: list[str] = []
        synthesis_fallback_docs: dict[str, str] = {}  # doc_name -> reason_code

        dependency = GroupDocDependency.load(
            docs_dir / RAG_SUPPORT_DIRNAME / DOC_PACK_MANIFEST_FILENAME
        )
        summary_docs = sorted(dependency.cross_cutting_docs()) if dependency else []

        for doc_name in summary_docs:
            doc_path = docs_dir / f"{doc_name}.md"
            if not doc_path.is_file():
                continue

            current_text = doc_path.read_text(encoding="utf-8", errors="replace")
            doc_modified = False
            doc_reason: str | None = None

            for group in patched_groups_so_far:
                field_changes = group_field_changes.get(group, [])
                if not field_changes:
                    continue
                if not doc_references_group(current_text, group, field_changes):
                    # This document's current content has nothing to do with this
                    # group's change -- nothing to amend, and not a fallback signal.
                    continue

                removed_ids = group_removed_ids.get(group, frozenset())
                mapping = SynthesisImpactMapper.map_sections(
                    doc_text=current_text,
                    group=group,
                    field_changes=field_changes,
                    removed_identifiers=removed_ids,
                )
                if not mapping.mapped:
                    logger.warning(
                        "TruePatch: synthesis doc %s references group %s but no "
                        "section could be isolated for amendment; routing to "
                        "scoped-local re-derivation of this document",
                        doc_name, group,
                    )
                    doc_reason = "synthesis_mapping_unavailable"
                    break

                headings = sorted(mapping.headings)
                updated_excerpt = self._extract_sections(
                    group_analyses[group], group_sections.get(group, frozenset())
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
                        is_synthesis=True,
                        removed_identifiers=mapping.removed_identifiers,
                    )
                except Exception as exc:
                    logger.warning(
                        "TruePatch: synthesis Phase 3 failed for doc %s group %s: %s",
                        doc_name, group, exc,
                    )
                    doc_reason = "phase3_patch_error"
                    break

                if not _assert_invariants_preserved(current_text, new_text, mapping.headings):
                    logger.warning(
                        "TruePatch: G2 violation amending synthesis doc %s for group %s",
                        doc_name, group,
                    )
                    doc_reason = "g2_invariant_violation"
                    break

                # G5 (reused): each rewritten heading must be fact-grounded in
                # this group's diff or the heading's own prior content.
                new_headings = extract_h2_sections(new_text)
                prior_headings = extract_h2_sections(current_text)
                unreconciled: dict[str, list[str]] = {}
                for heading in headings:
                    bad = find_unreconciled_facts(
                        rewritten_text=new_headings.get(heading, ""),
                        original_text=prior_headings.get(heading, ""),
                        field_changes=field_changes,
                    )
                    if bad:
                        unreconciled[heading] = sorted(bad)
                if unreconciled:
                    logger.warning(
                        "TruePatch: G5 diff-fidelity failure amending synthesis "
                        "doc %s for group %s: %s",
                        doc_name, group, unreconciled,
                    )
                    doc_reason = "diff_fidelity_failed"
                    break

                # G6: removed units must not survive into the rewritten headings.
                residual: set[str] = set()
                for heading in headings:
                    residual |= verify_removal(
                        new_headings.get(heading, ""), mapping.removed_identifiers
                    )
                if residual:
                    logger.warning(
                        "TruePatch: G6 removal-completeness failure amending "
                        "synthesis doc %s for group %s: residual identifiers %s",
                        doc_name, group, sorted(residual),
                    )
                    doc_reason = "removal_incomplete"
                    break

                current_text = new_text
                doc_modified = True

            if doc_reason is not None:
                # Conservative: any rejection discards the WHOLE document's
                # amendment for this run (no partial/half-amended write) and
                # routes exactly this one document to scoped-local re-derivation.
                synthesis_fallback_docs[doc_name] = doc_reason
                continue

            if doc_modified:
                await asyncio.to_thread(doc_path.write_text, current_text, "utf-8")
                synthesis_patched_docs.append(f"{doc_name}.md")

        patched_docs.extend(
            d for d in synthesis_patched_docs if d not in patched_docs
        )

        # ── Legacy RAG push ────────────────────────────────────────────────
        # Candidate-based patching deliberately disables this branch.  Its
        # coordinator stages canonical and internal artifacts under an inactive
        # knowledge revision and activates both layers together after the
        # candidate tree has validated.
        if patched_docs and publish_rag:
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
            f"docs={patched_docs} fallback={fallback_groups} "
            f"synthesis_fallback={sorted(synthesis_fallback_docs)}",
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
                "fallback_reasons": fallback_reasons,
                "synthesis_patched_docs": synthesis_patched_docs,
                # doc_name (no .md) -> reason_code; documents the mapper could not
                # safely amend. NOT auto-regenerated by this executor (RD-05: never
                # whole-repo) -- left byte-unchanged and reported here for a
                # scoped-local re-derivation of exactly these documents.
                "synthesis_fallback_docs": synthesis_fallback_docs,
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
        scan_output_dir: Path | None = None,
        platform: str,
        provider,
    ) -> _Phase2Patch:
        """Patch the Phase 2 analysis for one group.

        Returns a :class:`_Phase2Patch` (merged text, sections rewritten, the
        validated field diff, and any fully-removed unit identifiers) so the
        Slice-3 summary-doc amendment step can reuse the same diff without
        recomputing it. Raises PatchQualityError (with a distinguishing
        ``reason_code``) if a precondition, G5 diff-fidelity, or G3
        completeness check fails; the caller routes to scoped_regen
        fallback.  NOTHING is written to disk here; writing is deferred to
        _write_support_analysis.
        """
        # 1. Load scan data
        prev_jobs = await asyncio.to_thread(_load_prev_jobs, output_dir, group)
        if not prev_jobs:
            raise PatchQualityError(
                f"No (or empty) prev scan for group {group!r}",
                reason_code="missing_prev_scan",
            )
        curr_jobs = await asyncio.to_thread(
            _load_curr_jobs,
            project_id,
            group,
            scan_output_dir=scan_output_dir,
        )
        if not curr_jobs:
            raise PatchQualityError(
                f"Empty curr scan for group {group!r}",
                reason_code="missing_curr_scan",
            )

        # 2. Field diff
        field_changes, field_diff_text = await asyncio.to_thread(
            _compute_field_diff, prev_jobs, curr_jobs
        )
        if not field_changes:
            raise PatchQualityError(
                f"No field changes found for group {group!r}",
                reason_code="no_field_changes",
            )

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
                f"No published support analysis for group {group!r} at {support_path}",
                reason_code="missing_support_analysis",
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
                f"Model call failed for group {group!r}: {result.raw_error}",
                reason_code="model_call_failed",
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

        # 8. G5 validate-against-diff -- reject hallucinated facts BEFORE G3/write.
        # Only the sections the model actually rewrote are checked; each is compared
        # against its own pre-existing content plus the group's field diff vocabulary.
        unreconciled: dict[str, list[str]] = {}
        for name in sections_to_rewrite:
            if name not in model_sections:
                continue
            bad = find_unreconciled_facts(
                rewritten_text=model_sections[name],
                original_text=existing_sections.get(name, ""),
                field_changes=field_changes,
            )
            if bad:
                unreconciled[name] = sorted(bad)
        if unreconciled:
            raise PatchQualityError(
                f"G5 diff-fidelity failure for group {group!r}: "
                f"unreconciled facts not present in field diff or prior content: "
                f"{unreconciled}",
                reason_code="diff_fidelity_failed",
            )

        # 9. G3 validate-before-return
        if not await asyncio.to_thread(_analysis_text_is_valid, merged_text, platform):
            raise PatchQualityError(
                f"G3 validation failed for group {group!r}: "
                "merged analysis thin, incomplete, or missing GROUP_SUMMARY",
                reason_code="g3_validation_failed",
            )

        removed_ids = removed_unit_identifiers(prev_jobs, curr_jobs)
        return _Phase2Patch(
            text=merged_text,
            sections=sections_to_rewrite,
            field_changes=field_changes,
            removed_identifiers=removed_ids,
        )

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
        is_synthesis: bool = False,
        removed_identifiers: frozenset[str] = frozenset(),
    ) -> str:
        """Patch one Phase 3 doc for the given group.

        Returns the patched doc text (caller validates invariants before writing).

        ``is_synthesis`` (Slice 3): when True, renders the summary/cross-cutting
        doc prompt variant instead of the group-scoped one -- same heading-
        replacement mechanism, but the prompt frames the doc as holistic prose
        covering ALL groups (amend only this group's parts) and instructs
        removal of ``removed_identifiers`` rather than merely restating them.
        Group-scoped callers are unaffected (default False -- identical
        behaviour and template to before this slice).
        """
        if not headings_to_rewrite:
            return existing_doc

        template_name = "phase3_synthesis_patch.md" if is_synthesis else "phase3_patch.md"
        slots = {
            "DOC_NAME": doc_name,
            "GROUP": group,
            "PLATFORM": platform,
            "HEADINGS_TO_REWRITE": "\n".join(f"- {h}" for h in headings_to_rewrite),
            "UPDATED_ANALYSIS_SECTIONS": updated_analysis_sections,
            "EXISTING_DOC": existing_doc,
        }
        if is_synthesis:
            slots["REMOVED_IDENTIFIERS"] = (
                "\n".join(f"- {i}" for i in sorted(removed_identifiers))
                if removed_identifiers
                else "(none)"
            )
        prompt = self._renderer.render(template_name, slots)
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
