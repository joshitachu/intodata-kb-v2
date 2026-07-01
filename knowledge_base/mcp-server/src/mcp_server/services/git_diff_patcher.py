"""Diff-only Git webhook documentation patching."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from json_repair import repair_json

from mcp_server.config import config
from mcp_server.db.session import async_session_factory
from mcp_server.model_strategies.registry import create_model_provider, normalize_model_id
from mcp_server.providers.custom.models import ModelOptions
from mcp_server.services.log_manager import LogManager
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.rag_bridge import RagBridge, rag_push_succeeded
from mcp_server.services.git_remote_service import (
    fetch_remote,
    git_subprocess_env,
    pull_remote_ff_only,
)
from mcp_server.utils.error_sanitizer import sanitize_git_error

logger = logging.getLogger(__name__)

IGNORED_PATH_PATTERNS = (
    re.compile(r"(^|/)\.github/"),
    re.compile(r"(^|/)README(\.[^/]*)?$", re.IGNORECASE),
    re.compile(r"(^|/)readme(\.[^/]*)?$", re.IGNORECASE),
    re.compile(r"\.lock$", re.IGNORECASE),
    re.compile(r"(^|/)(package-lock\.json|pnpm-lock\.yaml)$", re.IGNORECASE),
    re.compile(r"(^|/)(test|tests|__tests__|spec)/"),
    re.compile(r"\.(test|spec)\.[A-Za-z0-9]+$"),
    re.compile(r"\.(md|txt)$", re.IGNORECASE),
    # Infrastructure / build files — never contain integration source
    re.compile(r"(^|/)Dockerfile(\.[^/]*)?$"),
    re.compile(r"(^|/)docker-compose[^/]*\.ya?ml$", re.IGNORECASE),
    re.compile(r"(^|/)\.env[^/]*$"),
    re.compile(r"(^|/)Makefile$"),
    re.compile(r"\.(toml)$", re.IGNORECASE),
    re.compile(r"(^|/)[^/]*\.sh$"),
)

DOC_SECTION_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$", re.MULTILINE)
GENERIC_IMPACT_TERMS = {
    "api",
    "app",
    "board",
    "handler",
    "handlers",
    "main",
    "pattern",
    "patterns",
    "quest",
    "questboard",
    "readme",
    "src",
    "test",
    "tests",
    "validation",
}

OUTCOME_SKIP = "skip"
OUTCOME_BOUNDED_PATCH = "bounded_patch"
OUTCOME_TARGETED_REGEN = "targeted_regen"
OUTCOME_FULL_REGEN = "full_regen"
OUTCOME_MANUAL_REVIEW = "manual_review"
OUTCOME_FAILED = "failed"
NORMALIZED_OUTCOMES = {
    OUTCOME_SKIP,
    OUTCOME_BOUNDED_PATCH,
    OUTCOME_TARGETED_REGEN,
    OUTCOME_FULL_REGEN,
    OUTCOME_MANUAL_REVIEW,
    OUTCOME_FAILED,
}
_ACTION_OUTCOMES = {
    "skipped": OUTCOME_SKIP,
    "patched": OUTCOME_BOUNDED_PATCH,
    "true_patch": OUTCOME_BOUNDED_PATCH,
    "targeted_regen": OUTCOME_TARGETED_REGEN,
    "full_regen": OUTCOME_FULL_REGEN,
    "manual_review": OUTCOME_MANUAL_REVIEW,
    "failed": OUTCOME_FAILED,
}


@dataclass
class FileChange:
    status: str
    path: str
    previous_path: str | None = None


@dataclass
class DocSection:
    filename: str
    heading: str
    content: str


@dataclass
class DiffPatchResult:
    action: str
    summary: dict[str, Any]
    error: str | None = None
    patched_docs: list[str] = field(default_factory=list)
    advance_commit: bool = False


@dataclass(frozen=True)
class DiffChunk:
    index: int
    paths: list[str]
    text: str


@dataclass(frozen=True)
class DocObligation:
    id: str
    kind: str
    description: str
    required_terms: tuple[str, ...] = ()
    stale_terms: tuple[str, ...] = ()
    source_paths: tuple[str, ...] = ()
    required: bool = True

    def to_prompt(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "description": self.description,
            "required_terms": list(self.required_terms),
            "stale_terms": list(self.stale_terms),
            "source_paths": list(self.source_paths),
            "required": self.required,
        }


def normalized_outcome(action: str) -> str:
    """Map legacy internal actions to the operator-facing outcome vocabulary."""
    return _ACTION_OUTCOMES.get(action, OUTCOME_FAILED)


def reason_code_for(action: str, reason: str | None = None) -> str:
    """Return a stable operator-facing reason code for Git update summaries."""
    outcome = normalized_outcome(action)
    lowered = (reason or "").lower()
    if "first push" in lowered or "initial manual scan" in lowered:
        return "missing_baseline"
    if "only ignored" in lowered or "non-doc-impacting" in lowered:
        return "non_doc_change"
    if "no changed files" in lowered:
        return "no_changed_files"
    if "no relevant changed hunks" in lowered:
        return "no_relevant_hunks"
    if "no existing docs found" in lowered:
        return "missing_docs"
    if "could not be mapped" in lowered:
        return "unmapped_diff"
    if "could not be narrowed" in lowered:
        return "unsafe_scope"
    if "too large" in lowered or "max summarized patch" in lowered:
        return "unsafe_scope"
    if "validation failed" in lowered or "required obligations" in lowered:
        return "validation_failed"
    if "rag push failed" in lowered or "rag sync failed" in lowered:
        return "rag_sync_failed"
    if "invalid json" in lowered:
        return "invalid_agent_output"
    if "patch model failed" in lowered:
        return "patch_model_failed"
    if "confidence too low" in lowered:
        return "low_confidence"
    if "no doc edits" in lowered or "no applicable doc edits" in lowered:
        return "no_doc_edits"
    if "targeted regeneration failed" in lowered:
        return "regen_failed"
    if "true_patch_succeeded" in lowered:
        return "true_patch_succeeded"
    if outcome == OUTCOME_SKIP:
        return "skip"
    if outcome == OUTCOME_BOUNDED_PATCH:
        return "bounded_patch_succeeded"
    if outcome == OUTCOME_TARGETED_REGEN:
        return "targeted_regen_succeeded"
    if outcome == OUTCOME_FULL_REGEN:
        return "full_regen_succeeded"
    if outcome == OUTCOME_MANUAL_REVIEW:
        return "manual_review"
    return "unexpected_error"


def _summary_with_outcome(
    summary: dict[str, Any],
    *,
    action: str,
    reason: str | None = None,
    decision_strategy: str | None = None,
    reason_code: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    final = dict(summary)
    final.update(extra)
    final["action"] = action
    final["outcome"] = normalized_outcome(action)
    if decision_strategy:
        final["decision_strategy"] = decision_strategy
    else:
        final.setdefault("decision_strategy", final.get("strategy") or final.get("mode"))
    if reason is not None:
        final["reason"] = reason
    final["reason_code"] = reason_code or reason_code_for(action, final.get("reason"))
    return final


async def run_webhook_diff_update(
    *,
    project_id: str,
    before_sha: str,
    after_sha: str,
    log_mgr: LogManager,
    trigger: str = "webhook",
    impact_plan: dict[str, Any] | None = None,
) -> bool:
    """Patch current docs from a small Git diff without running full generation."""
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            return False
        local_path = project.local_path
        doc_generation_provider = project.doc_generation_provider
        github_token_enc = getattr(project, "github_token_enc", None)

    await log_mgr.emit(project_id, f"GitHub push detected: {before_sha[:12]}..{after_sha[:12]}", "info")

    result = await _run_diff_patch(
        project_id=project_id,
        local_path=local_path,
        before_sha=before_sha,
        after_sha=after_sha,
        doc_generation_provider=doc_generation_provider,
        log_mgr=log_mgr,
        trigger=trigger,
        github_token_enc=github_token_enc,
        impact_plan=impact_plan,
    )

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        await registry.update_git_update_summary(
            project_id,
            result.summary,
            error=result.error,
        )
        if result.advance_commit and result.error is None:
            await registry.update_last_ingested_commit(project_id, after_sha)
            await registry.update_status(project_id, "ready", error_message=None)
        elif result.action == "skipped" and result.error is None:
            await registry.update_status(project_id, "ready", error_message=None)
        elif result.action == "manual_review":
            await registry.update_status(project_id, "ready", error_message=result.error)
        else:
            await registry.update_status(project_id, "error", error_message=result.error)
        await db.commit()

    return result.error is None


async def _run_diff_patch(
    *,
    project_id: str,
    local_path: str,
    before_sha: str,
    after_sha: str,
    doc_generation_provider: str | None,
    log_mgr: LogManager,
    trigger: str,
    github_token_enc: str | None = None,
    impact_plan: dict[str, Any] | None = None,
) -> DiffPatchResult:
    base_summary = {
        "trigger": trigger,
        "mode": str(impact_plan.get("mode") or "tool_loop_agent") if impact_plan else "diff_patch",
        "before": before_sha,
        "after": after_sha,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "changed_files": [],
        "affected_docs": [],
        "required_facts": [],
        "stale_claims": [],
        "strategy": impact_plan.get("strategy") if impact_plan else None,
        "confidence": impact_plan.get("confidence") if impact_plan else None,
        "fallback_used": False,
        "matched_docs": [],
        "patched_docs": [],
        "touched_docs": [],
        "validation_errors": [],
        "repair_attempts": 0,
        "action": "unknown",
        "outcome": "unknown",
        "reason_code": None,
        "decision_strategy": impact_plan.get("strategy") if impact_plan else "diff_patch",
    }
    if impact_plan:
        base_summary["affected_docs"] = _coerce_string_list(impact_plan.get("affected_docs"))
        base_summary["required_facts"] = _coerce_string_list(impact_plan.get("required_facts"))
        base_summary["stale_claims"] = _coerce_string_list(impact_plan.get("stale_claims"))
        base_summary["reason"] = str(impact_plan.get("reason") or "")

    try:
        if not before_sha or before_sha == "0" * 40:
            return _manual(base_summary, "First push requires initial manual scan/generate")

        if impact_plan and impact_plan.get("strategy") == "manual_review":
            return _manual(base_summary, str(impact_plan.get("reason") or "Impact agent requested manual review"))
        if impact_plan and impact_plan.get("strategy") == "targeted_regen":
            return _manual(base_summary, str(impact_plan.get("reason") or "Impact agent requested targeted regeneration"))

        await fetch_remote(local_path, github_token_enc, git_func=_git)
        name_status = await _git(local_path, "diff", "--name-status", before_sha, after_sha)
        changes = parse_name_status(name_status)
        base_summary["changed_files"] = [c.path for c in changes]
        await log_mgr.emit(project_id, f"Diff impact: {len(changes)} changed file(s)", "info")

        decision = _preflight(changes)
        if decision:
            return await _manual_or_skip(
                project_id,
                local_path,
                after_sha,
                base_summary,
                decision,
                log_mgr,
                github_token_enc=github_token_enc,
            )

        full_diff_text = await _git(
            local_path,
            "diff",
            f"--unified={config.git_diff_patch_context_lines}",
            before_sha,
            after_sha,
            "--",
            *[c.path for c in changes],
        )
        chunks = _chunk_diff(full_diff_text, changes)
        base_summary["diff_chunks"] = [
            {"index": chunk.index, "paths": chunk.paths, "chars": len(chunk.text)}
            for chunk in chunks
        ]
        if not chunks:
            return await _manual_or_skip(
                project_id,
                local_path,
                after_sha,
                base_summary,
                ("skipped", "No relevant changed hunks in push"),
                log_mgr,
                github_token_enc=github_token_enc,
            )
        obligations = _build_doc_obligations(changes, full_diff_text)
        base_summary["obligations"] = [obligation.to_prompt() for obligation in obligations]

        docs_dir = config.project_root / "output" / project_id / "docs"
        if not docs_dir.exists():
            return _manual(base_summary, "No existing docs found to patch")

        candidate_sections = await asyncio.to_thread(
            _match_doc_sections, docs_dir, changes, full_diff_text
        )
        base_summary["candidate_docs"] = sorted({s.filename for s in candidate_sections})
        planned_docs = _coerce_string_list(impact_plan.get("affected_docs") if impact_plan else None)
        preferred_docs = planned_docs or _preferred_docs_for_diff(changes, full_diff_text)
        sections = await asyncio.to_thread(
            lambda: _select_doc_sections(
                candidate_sections,
                limit=config.git_diff_patch_max_docs,
                preferred_docs=preferred_docs,
                docs_dir=docs_dir,
            )
        )
        matched_docs = sorted({s.filename for s in sections})
        base_summary["matched_docs"] = matched_docs
        if not candidate_sections:
            return _manual(
                {
                    **base_summary,
                    "suggested_doc_update": {
                        "status": "pending",
                        "reason": "No existing section matched the changed hunks",
                        "changed_files": base_summary["changed_files"],
                    },
                },
                "Changed code could not be mapped to existing doc sections",
            )
        if not sections:
            return _manual(
                base_summary,
                "Changed code could not be narrowed to a bounded set of doc sections",
            )

        provider = _patch_provider(doc_generation_provider)
        if len(chunks) > config.git_diff_patch_max_chunks:
            if len(chunks) > config.git_diff_patch_max_summary_chunks:
                return _manual(
                    base_summary,
                    f"Diff requires {len(chunks)} chunks; max summarized patch is {config.git_diff_patch_max_summary_chunks}",
                )
            summarized = await _summarize_chunks_for_patch(provider, chunks, obligations)
            base_summary["summarized_patch"] = True
            base_summary["summarized_patch_reason"] = (
                f"Diff had {len(chunks)} chunks; compacted before patching"
            )
            base_summary["diff_summary_outputs"] = summarized["outputs"]
            chunks = summarized["chunks"]

        estimated_patch_tokens = _estimate_patch_tokens(chunks, sections, obligations)
        if estimated_patch_tokens > config.git_diff_patch_max_input_tokens:
            if len(chunks) > config.git_diff_patch_max_summary_chunks:
                return _manual(
                    {**base_summary, "estimated_input_tokens": estimated_patch_tokens},
                    f"Patch prompts too large ({estimated_patch_tokens} estimated tokens)",
                )
            summarized = await _summarize_chunks_for_patch(provider, chunks, obligations)
            base_summary["summarized_patch"] = True
            base_summary["summarized_patch_reason"] = (
                f"Estimated patch prompts were {estimated_patch_tokens} tokens; compacted before patching"
            )
            base_summary["diff_summary_outputs"] = summarized["outputs"]
            chunks = summarized["chunks"]

        patches: list[dict[str, str]] = []
        agent_outputs: list[dict[str, Any]] = []
        obligations_satisfied: set[str] = set()
        obligations_unsatisfied: set[str] = set()
        total_estimated_tokens = 0
        total_input_tokens = 0
        total_output_tokens = 0
        model_name: str | None = None

        for chunk in chunks:
            for section in sections:
                section_obligations = _obligations_for_section(obligations, section)
                patch_prompt = _build_patch_prompt(chunk, [section], section_obligations)
                estimated_tokens = max(1, len(patch_prompt) // 4)
                total_estimated_tokens += estimated_tokens
                if total_estimated_tokens > config.git_diff_patch_max_input_tokens:
                    return _manual(
                        {**base_summary, "estimated_input_tokens": total_estimated_tokens},
                        f"Patch prompts too large ({total_estimated_tokens} estimated tokens)",
                    )

                output_cap = _output_token_budget(section, section_obligations)
                result = await provider.complete(
                    patch_prompt,
                    options=ModelOptions(max_tokens=output_cap, temperature=0.0, timeout_s=240.0, retries=1),
                )
                model_name = result.model_name
                total_input_tokens += result.input_tokens or 0
                total_output_tokens += result.output_tokens or 0
                if not result.success:
                    return _failed(base_summary, f"Patch model failed: {result.raw_error}")

                try:
                    parsed = _parse_agent_json(result.text)
                except (json.JSONDecodeError, ValueError) as e:
                    return _manual(base_summary, f"Patch agent returned invalid JSON: {e}")
                agent_outputs.append(_summarize_agent_output(parsed, chunk, section, output_cap, result.output_tokens))
                obligations_satisfied.update(_coerce_string_list(parsed.get("obligations_satisfied")))
                obligations_unsatisfied.update(_coerce_string_list(parsed.get("obligations_unsatisfied")))
                confidence = _agent_confidence(parsed)
                if confidence < config.git_diff_patch_min_confidence:
                    return _manual(
                        {**base_summary, "agent_outputs": agent_outputs},
                        f"Patch confidence too low ({confidence:.2f})",
                    )
                if _hit_output_cap(result.output_tokens, output_cap) and not _required_obligations_satisfied(
                    section_obligations,
                    parsed,
                ):
                    return _manual(
                        {**base_summary, "agent_outputs": agent_outputs},
                        f"Patch model hit output cap ({output_cap}) before satisfying required obligations",
                    )
                patches.extend(parsed.get("edits", []))

        base_summary["model"] = model_name
        base_summary["estimated_input_tokens"] = total_estimated_tokens
        base_summary["input_tokens"] = total_input_tokens
        base_summary["output_tokens"] = total_output_tokens
        base_summary["agent_outputs"] = agent_outputs
        base_summary["obligations_satisfied"] = sorted(obligations_satisfied)
        base_summary["obligations_unsatisfied"] = sorted(obligations_unsatisfied)

        if not patches:
            return _manual(
                base_summary,
                "Patch agent returned no doc edits for a doc-relevant code diff",
            )

        patched_docs, originals = await asyncio.to_thread(
            _apply_patches_with_backup, docs_dir, patches
        )
        if not patched_docs:
            return _manual(base_summary, "Patch model returned no applicable doc edits")
        validation_errors = await asyncio.to_thread(
            lambda: _validate_obligations(
                docs_dir,
                obligations,
                candidate_sections,
                patched_docs,
                obligations_satisfied | _infer_satisfied_obligations(docs_dir, obligations, patched_docs),
            )
        )
        if validation_errors:
            await asyncio.to_thread(_restore_docs, docs_dir, originals)
            return _manual(
                {
                    **base_summary,
                    "patched_docs": patched_docs,
                    "validation_errors": validation_errors,
                },
                "Patch validation failed: " + "; ".join(validation_errors[:3]),
            )

        bridge = RagBridge(log_manager=log_mgr)
        rag_result = await bridge.push_docs(
            project_id=project_id,
            output_dir=str(docs_dir),
            filenames=set(patched_docs),
        )
        if rag_result.get("errors"):
            _restore_docs(docs_dir, originals)
            return _failed(
                {
                    **base_summary,
                    "patched_docs": patched_docs,
                    "touched_docs": patched_docs,
                    "rag_result": rag_result,
                },
                f"RAG push failed: {rag_result['errors']}",
            )
        if not rag_push_succeeded(rag_result, expected_count=len(patched_docs)):
            _restore_docs(docs_dir, originals)
            return _failed(
                {
                    **base_summary,
                    "patched_docs": patched_docs,
                    "touched_docs": patched_docs,
                    "rag_result": rag_result,
                },
                "RAG sync failed: not all patched docs were pushed",
            )

        await _git(local_path, "pull", "--ff-only")
        await log_mgr.emit(
            project_id,
            f"Patched docs from Git diff: {', '.join(patched_docs)}",
            "info",
        )
        summary = _summary_with_outcome(
            base_summary,
            action="patched",
            patched_docs=patched_docs,
            touched_docs=patched_docs,
            rag_result=rag_result,
        )
        return DiffPatchResult(
            action="patched",
            summary=summary,
            patched_docs=patched_docs,
            advance_commit=True,
        )
    except Exception as e:
        logger.exception("Git diff patch failed for %s", project_id)
        return _failed(base_summary, str(e))


async def _run_git(local_path: str, *args: str, timeout: float = 60.0, strip: bool = False) -> str:
    """Shared async git subprocess helper. Used directly and re-exported to git_poller."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=local_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
        env=git_subprocess_env(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError(f"git {' '.join(args)} timed out after {timeout:.0f}s")
    if proc.returncode != 0:
        detail = stderr.decode().strip() or stdout.decode().strip()
        raise RuntimeError(sanitize_git_error(detail or f"git {' '.join(args)} failed"))
    result = stdout.decode()
    return result.strip() if strip else result


async def _git(local_path: str, *args: str) -> str:
    return await _run_git(local_path, *args)


def parse_name_status(output: str) -> list[FileChange]:
    changes: list[FileChange] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0][:1]
        if status in {"R", "C"} and len(parts) >= 3:
            changes.append(FileChange(status=status, previous_path=parts[1], path=parts[2]))
        elif len(parts) >= 2:
            changes.append(FileChange(status=status, path=parts[1]))
    return changes


def _chunk_diff(diff_text: str, changes: list[FileChange]) -> list[DiffChunk]:
    """Split a unified diff into bounded file chunks for the patch agent."""
    file_parts = re.split(r"(?=^diff --git )", diff_text, flags=re.MULTILINE)
    if not file_parts:
        return []

    chunks: list[DiffChunk] = []
    current_parts: list[str] = []
    current_paths: list[str] = []

    def flush() -> None:
        nonlocal current_parts, current_paths
        if current_parts:
            chunks.append(
                DiffChunk(
                    index=len(chunks) + 1,
                    paths=sorted(set(current_paths)),
                    text="\n".join(part.rstrip() for part in current_parts if part.strip()).strip(),
                )
            )
            current_parts = []
            current_paths = []

    fallback_paths = [c.path for c in changes]
    for raw_part in file_parts:
        part = raw_part.strip()
        if not part:
            continue
        paths = _paths_in_diff_part(part) or fallback_paths
        if len(part) > config.git_diff_patch_max_chars:
            flush()
            chunks.append(
                DiffChunk(
                    index=len(chunks) + 1,
                    paths=paths,
                    text=part[: config.git_diff_patch_max_chars],
                )
            )
            continue
        current_len = sum(len(p) for p in current_parts) + len(part)
        if current_parts and current_len > config.git_diff_patch_max_chars:
            flush()
        current_parts.append(part)
        current_paths.extend(paths)

    flush()
    return [chunk for chunk in chunks if chunk.text]


def _paths_in_diff_part(diff_part: str) -> list[str]:
    first_line = diff_part.splitlines()[0] if diff_part.splitlines() else ""
    match = re.match(r"diff --git a/(.+?) b/(.+)$", first_line)
    if not match:
        return []
    paths = [match.group(1), match.group(2)]
    return sorted({p for p in paths if p != "/dev/null"})


def _preflight(changes: list[FileChange]) -> tuple[str, str] | None:
    if not changes:
        return ("skipped", "No changed files in push")
    doc_relevant = [c for c in changes if not _is_ignored_path(c.path)]
    if not doc_relevant:
        return ("skipped", "Only ignored/non-doc-impacting files changed")
    return None


def _is_ignored_path(path: str) -> bool:
    return any(pattern.search(path) for pattern in IGNORED_PATH_PATTERNS)


async def _manual_or_skip(
    project_id: str,
    local_path: str,
    after_sha: str,
    summary: dict[str, Any],
    decision: tuple[str, str],
    log_mgr: LogManager,
    github_token_enc: str | None = None,
) -> DiffPatchResult:
    action, reason = decision
    if action == "skipped":
        await pull_remote_ff_only(local_path, github_token_enc, git_func=_git)
        await log_mgr.emit(project_id, f"Git diff skipped: {reason}", "info")
        return DiffPatchResult(
            action="skipped",
            summary=_summary_with_outcome(summary, action="skipped", reason=reason),
            advance_commit=True,
        )
    return _manual(summary, reason)


def _manual(summary: dict[str, Any], reason: str) -> DiffPatchResult:
    return DiffPatchResult(
        action="manual_review",
        summary=_summary_with_outcome(summary, action="manual_review", reason=reason),
        error=f"Manual review required: {reason}",
    )


def _failed(summary: dict[str, Any], reason: str) -> DiffPatchResult:
    return DiffPatchResult(
        action="failed",
        summary=_summary_with_outcome(summary, action="failed", reason=reason),
        error=reason,
    )


def _match_doc_sections(docs_dir: Path, changes: list[FileChange], diff_text: str) -> list[DocSection]:
    terms = _impact_terms(changes, diff_text)
    scored_sections: list[tuple[int, DocSection]] = []
    for md_file in sorted(docs_dir.glob("*.md")):
        if not md_file.name[0].isdigit():
            continue
        text = md_file.read_text(encoding="utf-8", errors="replace")
        for section in _sections_in_doc(md_file.name, text):
            score = _section_match_score(section, terms)
            if score > 0:
                scored_sections.append((score, section))
    scored_sections.sort(key=lambda item: (-item[0], item[1].filename, item[1].heading))
    return [section for _, section in scored_sections]


def _select_doc_sections(
    sections: list[DocSection],
    limit: int,
    *,
    preferred_docs: list[str] | None = None,
    docs_dir: Path | None = None,
) -> list[DocSection]:
    selected: list[DocSection] = []
    seen_docs: set[str] = set()

    by_filename: dict[str, list[DocSection]] = {}
    for section in sections:
        by_filename.setdefault(section.filename, []).append(section)

    for filename in preferred_docs or []:
        if filename in seen_docs:
            continue
        section = by_filename.get(filename, [None])[0]
        if section is None and docs_dir is not None:
            section = _fallback_section_for_doc(docs_dir, filename)
        if section is None:
            continue
        selected.append(section)
        seen_docs.add(filename)
        if len(seen_docs) >= limit:
            return selected

    for section in sections:
        if section.filename in seen_docs:
            continue
        selected.append(section)
        seen_docs.add(section.filename)
        if len(seen_docs) >= limit:
            break
    return selected


def _preferred_docs_for_diff(changes: list[FileChange], diff_text: str) -> list[str]:
    paths = " ".join(c.path for c in changes).lower()
    lowered_diff = diff_text.lower()
    preferred: list[str] = []

    def add(filename: str) -> None:
        if filename not in preferred:
            preferred.append(filename)

    if "/patterns/" in paths or "design patterns" in lowered_diff:
        add("08_CHANGE_PLAYBOOK.md")
        add("01_ARCHITECTURE_MAP.md")
        add("00_AGENT_BRIEF.md")

    if "/controllers/" in paths or "/services/" in paths:
        add("04_BUSINESS_RULES_AND_TRANSFORMATIONS.md")
        add("03_DATA_FLOWS_AND_LINEAGE.md")

    if "/middleware/" in paths or "exception" in lowered_diff:
        add("06_OPERATIONS_TESTING_AND_DEBUGGING.md")
        add("07_QUALITY_SECURITY_RISK_SCORECARD.md")

    return preferred


def _fallback_section_for_doc(docs_dir: Path, filename: str) -> DocSection | None:
    path = docs_dir / filename
    if not path.exists() or not path.name[0].isdigit():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    sections = _sections_in_doc(filename, text)
    if not sections:
        return None

    preferred_headings = {
        "08_CHANGE_PLAYBOOK.md": ("Change Impact", "Inspect"),
        "01_ARCHITECTURE_MAP.md": ("Topology", "Dependencies"),
        "00_AGENT_BRIEF.md": ("Known Traps", "Documentation Map"),
    }
    for wanted in preferred_headings.get(filename, ()):
        for section in sections:
            if section.heading == wanted:
                return section
    return sections[0]


def _impact_terms(changes: list[FileChange], diff_text: str) -> list[str]:
    terms: set[str] = set()
    for change in changes:
        path = Path(change.path)
        terms.add(change.path)
        terms.add(path.name)
        terms.add(path.stem)
        for part in path.parts:
            if len(part) >= 4 and part.lower() not in GENERIC_IMPACT_TERMS:
                terms.add(part)
    for pattern in (
        r"\b(?:class|function|def|interface|type|const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"\b([A-Z][A-Za-z0-9]+(?:Service|Controller|Repository|Handler|Component|Page|View|Model))\b",
        r"\b(Chain of Responsibility)\b",
    ):
        for match in re.finditer(pattern, diff_text):
            terms.add(match.group(1))
    return sorted(t for t in terms if len(t) >= 4 and t.lower() not in GENERIC_IMPACT_TERMS)


def _build_doc_obligations(changes: list[FileChange], diff_text: str) -> list[DocObligation]:
    obligations: list[DocObligation] = []
    seen: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()

    def add(
        kind: str,
        description: str,
        *,
        required_terms: tuple[str, ...] = (),
        stale_terms: tuple[str, ...] = (),
        source_paths: tuple[str, ...] = (),
        required: bool = True,
    ) -> None:
        if len(obligations) >= config.git_diff_patch_max_obligations:
            return
        valid = _valid_count_claim_term if kind == "changed_count" else _valid_obligation_term
        normalized_required = tuple(_dedupe_preserve([term for term in required_terms if valid(term)]))
        normalized_stale = tuple(_dedupe_preserve([term for term in stale_terms if valid(term)]))
        if not normalized_required and not normalized_stale:
            return
        key = (kind, normalized_required, normalized_stale)
        if key in seen:
            return
        seen.add(key)
        obligations.append(
            DocObligation(
                id=f"obl-{len(obligations) + 1:02d}",
                kind=kind,
                description=description,
                required_terms=normalized_required[:8],
                stale_terms=normalized_stale[:6],
                source_paths=source_paths[:6],
                required=required,
            )
        )

    changed_by_path = {change.path: change for change in changes}
    grouped_added_source_paths = _group_added_source_paths(changes)
    grouped_paths = {path for paths in grouped_added_source_paths.values() for path in paths}
    for parent, paths in sorted(grouped_added_source_paths.items()):
        group_terms: list[str] = [*Path(parent).parts[-3:]]
        for path in paths:
            group_terms.extend(_terms_from_path(path))
        add(
            "added_source_group",
            f"New source group {parent} ({len(paths)} files) should be reflected in relevant documentation when it changes documented behavior.",
            required_terms=tuple(group_terms),
            source_paths=tuple(paths),
        )

    for path, status in ((path, changed_by_path[path].status) for path in sorted(changed_by_path)):
        stem_terms = _terms_from_path(path)
        if status == "A" and _looks_like_test_path(path):
            add(
                "added_test",
                f"New test file {path} should be reflected in test or coverage documentation when relevant.",
                required_terms=stem_terms,
                source_paths=(path,),
            )
        elif status == "A" and path not in grouped_paths and not _is_ignored_path(path):
            add(
                "added_source_file",
                f"New source file {path} should be reflected in relevant documentation when it changes documented behavior.",
                required_terms=stem_terms,
                source_paths=(path,),
            )
        elif status == "D" and not _is_ignored_path(path):
            add(
                "removed_source_file",
                f"Removed source file {path} should not remain documented as current behavior.",
                stale_terms=stem_terms,
                source_paths=(path,),
            )
        elif status == "R":
            previous = changed_by_path[path].previous_path or ""
            add(
                "renamed_file",
                f"Renamed file {previous} -> {path} should be reflected in relevant documentation.",
                required_terms=_terms_from_path(path),
                stale_terms=_terms_from_path(previous),
                source_paths=tuple(p for p in (previous, path) if p),
            )

    for heading in re.findall(r"^\+#{1,6}\s+(.+?)\s*$", diff_text, flags=re.MULTILINE):
        terms = _important_terms(heading)
        add(
            "added_heading",
            f"New documented concept heading should be reflected where existing generated docs summarize this area: {heading}",
            required_terms=terms,
        )

    for old_line, new_line in _changed_claim_lines(diff_text):
        old_numbers = tuple(re.findall(r"\b\d+\b", old_line))
        new_numbers = tuple(re.findall(r"\b\d+\b", new_line))
        if old_numbers and new_numbers and old_numbers != new_numbers:
            shared_terms = _shared_claim_terms(old_line, new_line)
            add(
                "changed_count",
                f"Numeric documentation claim changed from {old_numbers} to {new_numbers}; generated docs should reflect the new claim.",
                required_terms=tuple([*new_numbers, *shared_terms]),
                stale_terms=tuple([*old_numbers, *shared_terms]),
            )

    for symbol in _added_symbols(diff_text)[:8]:
        add(
            "added_symbol",
            f"New symbol {symbol} should be reflected in relevant documentation when it changes documented behavior.",
            required_terms=(symbol,),
            required=False,
        )

    return obligations


def _group_added_source_paths(changes: list[FileChange]) -> dict[str, list[str]]:
    by_parent: dict[str, list[str]] = {}
    for change in changes:
        if change.status != "A" or _looks_like_test_path(change.path) or _is_ignored_path(change.path):
            continue
        parent = str(Path(change.path).parent)
        by_parent.setdefault(parent, []).append(change.path)
    return {parent: sorted(paths) for parent, paths in by_parent.items() if len(paths) >= 2}


def _terms_from_path(path: str) -> tuple[str, ...]:
    parts = Path(path).parts
    terms: list[str] = []
    for part in parts[-4:]:
        stem = Path(part).stem
        if stem:
            terms.append(stem)
        terms.extend(_split_identifier(stem))
    return tuple(_dedupe_preserve(terms))


def _looks_like_test_path(path: str) -> bool:
    lowered = path.lower()
    return "/test" in lowered or "/tests/" in lowered or lowered.endswith(("test.cs", "tests.cs", ".test.ts", ".spec.ts"))


def _added_symbols(diff_text: str) -> list[str]:
    symbols: list[str] = []
    for line in diff_text.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for pattern in (
            r"\b(?:class|interface|record|struct|enum|function|def|type|const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"\b([A-Z][A-Za-z0-9]+(?:Service|Controller|Repository|Handler|Component|Page|View|Model|Context|Tests?))\b",
        ):
            symbols.extend(match.group(1) for match in re.finditer(pattern, line))
    return _dedupe_preserve(symbols)


def _changed_claim_lines(diff_text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    removed: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("-"):
            removed.append(line[1:].strip())
            continue
        if line.startswith("+") and removed:
            added = line[1:].strip()
            old = removed.pop(0)
            if _line_has_numeric_claim(old) and _line_has_numeric_claim(added):
                pairs.append((old, added))
            continue
        if line.startswith("@@") or not line.startswith(("-", "+")):
            removed = []
    return pairs


def _line_has_numeric_claim(line: str) -> bool:
    return bool(re.search(r"\b\d+\b", line)) and bool(re.search(r"[A-Za-z]", line))


def _shared_claim_terms(old_line: str, new_line: str) -> tuple[str, ...]:
    old_terms = set(_claim_context_terms(old_line))
    new_terms = set(_claim_context_terms(new_line))
    shared = list(old_terms & new_terms)
    shared.sort(key=lambda term: (term.lower() not in {"test", "tests", "pattern", "patterns", "design"}, term.lower()))
    return tuple(shared[:8])


def _claim_context_terms(line: str) -> tuple[str, ...]:
    words = [word.strip("`*_-. ") for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", line)]
    terms: list[str] = []
    for idx, word in enumerate(words):
        lowered = word.lower()
        if lowered in {"design", "pattern", "patterns", "test", "tests", "coverage", "unit", "units"}:
            terms.append(word)
        elif idx > 0 and re.fullmatch(r"\d+", words[idx - 1]):
            terms.append(word)
        elif len(word) >= 5 and lowered not in {"http", "https"}:
            terms.append(word)
    return tuple(_dedupe_preserve(terms))


def _important_terms(text: str) -> tuple[str, ...]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text)
    terms: list[str] = []
    for word in words:
        terms.append(word.strip("`*_-."))
        terms.extend(_split_identifier(word))
    return tuple(_dedupe_preserve(term for term in terms if _valid_obligation_term(term))[:10])


def _split_identifier(value: str) -> list[str]:
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value).replace("_", " ").replace("-", " ").split()
    return [part for part in parts if _valid_obligation_term(part)]


def _valid_obligation_term(term: str) -> bool:
    cleaned = term.strip("`*_-. ")
    return len(cleaned) >= 3 and cleaned.lower() not in GENERIC_IMPACT_TERMS


def _valid_count_claim_term(term: str) -> bool:
    cleaned = term.strip("`*_-. ")
    return (cleaned.isdigit() or len(cleaned) >= 2) and cleaned.lower() not in {"http", "https"}


def _dedupe_preserve(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip("`*_-. ")
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _obligations_for_section(obligations: list[DocObligation], section: DocSection) -> list[DocObligation]:
    if not obligations:
        return []
    haystack = f"{section.filename}\n{section.heading}\n{section.content}".lower()
    matched: list[DocObligation] = []
    for obligation in obligations:
        terms = [*obligation.required_terms, *obligation.stale_terms]
        if any(term.lower() in haystack for term in terms):
            matched.append(obligation)
    return matched or obligations[: min(6, len(obligations))]


def _output_token_budget(section: DocSection, obligations: list[DocObligation]) -> int:
    section_tokens = max(1, len(section.content) // 4)
    if section_tokens > 10_000 or len(obligations) > 8:
        return config.git_diff_patch_output_tokens_large
    if section_tokens > 4_000 or len(obligations) > 3:
        return config.git_diff_patch_output_tokens_medium
    return config.git_diff_patch_output_tokens_small


def _sections_in_doc(filename: str, text: str) -> list[DocSection]:
    headings = list(DOC_SECTION_RE.finditer(text))
    if not headings:
        return [DocSection(filename=filename, heading="", content=text[:20_000])]
    sections: list[DocSection] = []
    for idx, heading in enumerate(headings):
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
        sections.append(
            DocSection(
                filename=filename,
                heading=heading.group(2).strip(),
                content=text[heading.start():end].strip()[:20_000],
            )
        )
    return sections


def _section_match_score(section: DocSection, terms: list[str]) -> int:
    content = section.content.lower()
    heading = section.heading.lower()
    score = 0
    for term in terms:
        lowered = term.lower()
        if lowered in heading:
            score += 6
        if lowered in content:
            score += 2
    return score


def _build_patch_prompt(
    chunk: DiffChunk,
    sections: list[DocSection],
    obligations: list[DocObligation] | None = None,
) -> str:
    section_payload = [
        {"filename": s.filename, "heading": s.heading, "current_markdown": s.content}
        for s in sections
    ]
    obligation_payload = [obligation.to_prompt() for obligation in obligations or []]
    return (
        "You are a bounded Diff Update Agent for project documentation.\n"
        "Rules:\n"
        "- Use only the supplied changed hunks and current doc sections.\n"
        "- Do not rewrite unrelated content.\n"
        "- Return strict JSON only, no markdown fences.\n"
        "- Each edit must replace exactly one supplied section heading.\n"
        "- If no documentation edit is needed, return an empty edits array.\n"
        "- If the current sections do not fit, do not invent a new broad document.\n\n"
        "- Satisfy every required obligation that applies to the supplied section.\n"
        "- Put satisfied obligation ids in obligations_satisfied and unsafe/missing ids in obligations_unsatisfied.\n\n"
        "JSON schema:\n"
        "{\"change_summary\":\"short summary\",\"inspected_context\":[\"bounded context used\"],"
        "\"affected_docs\":[\"04_BUSINESS_RULES_AND_TRANSFORMATIONS.md\"],"
        "\"obligations_satisfied\":[\"obl-01\"],\"obligations_unsatisfied\":[],"
        "\"edits\":[{\"filename\":\"04_BUSINESS_RULES_AND_TRANSFORMATIONS.md\",\"heading\":\"Existing Heading\","
        "\"replacement_markdown\":\"## Existing Heading\\n\\nUpdated content...\"}],"
        "\"confidence\":0.0}\n\n"
        f"Diff chunk {chunk.index}; changed paths: {json.dumps(chunk.paths)}\n"
        f"Changed hunks:\n{chunk.text}\n\n"
        f"Documentation obligations:\n{json.dumps(obligation_payload, ensure_ascii=False, indent=2)}\n\n"
        f"Current doc sections:\n{json.dumps(section_payload, ensure_ascii=False, indent=2)}"
    )


def _estimate_patch_tokens(
    chunks: list[DiffChunk],
    sections: list[DocSection],
    obligations: list[DocObligation],
) -> int:
    total = 0
    for chunk in chunks:
        for section in sections:
            section_obligations = _obligations_for_section(obligations, section)
            total += max(1, len(_build_patch_prompt(chunk, [section], section_obligations)) // 4)
    return total


async def _summarize_chunks_for_patch(
    provider,
    chunks: list[DiffChunk],
    obligations: list[DocObligation],
) -> dict[str, Any]:
    """Compress large raw diffs into doc-update facts before section patching."""
    summaries: list[str] = []
    outputs: list[dict[str, Any]] = []
    obligation_payload = [obligation.to_prompt() for obligation in obligations]
    for chunk in chunks:
        prompt = _build_diff_summary_prompt(chunk, obligation_payload)
        result = await provider.complete(
            prompt,
            options=ModelOptions(
                max_tokens=config.git_diff_patch_summary_output_tokens,
                temperature=0.0,
                timeout_s=240.0,
                retries=1,
            ),
        )
        if not result.success:
            raise RuntimeError(f"Diff summarization failed: {result.raw_error}")
        summary_text = result.text.strip()
        summaries.append(
            f"## Diff fact summary {chunk.index}\n"
            f"Changed paths: {', '.join(chunk.paths)}\n\n"
            f"{summary_text}"
        )
        outputs.append(
            {
                "chunk_index": chunk.index,
                "paths": chunk.paths,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "model": result.model_name,
            }
        )
    paths = sorted({path for chunk in chunks for path in chunk.paths})
    compact_text = "\n\n".join(summaries)
    return {
        "chunks": [DiffChunk(index=1, paths=paths, text=compact_text)],
        "outputs": outputs,
    }


def _build_diff_summary_prompt(chunk: DiffChunk, obligations: list[dict[str, Any]]) -> str:
    return (
        "You are compressing a Git diff for a documentation patch agent.\n"
        "Summarize only facts visible in the diff. Do not write final documentation.\n"
        "Keep names, paths, APIs, behavior changes, removed behavior, config changes, tests, "
        "data flow changes, and stale claims that generated docs may need to update.\n"
        "Use concise bullets grouped by changed path. Preserve exact identifiers and numbers.\n"
        "If a detail is uncertain, mark it as uncertain instead of inventing.\n\n"
        f"Documentation obligations:\n{json.dumps(obligations, ensure_ascii=False, indent=2)}\n\n"
        f"Diff chunk {chunk.index}; changed paths: {json.dumps(chunk.paths)}\n"
        f"Changed hunks:\n{chunk.text}"
    )


def _patch_provider(doc_generation_provider: str | None):
    model_id = (
        doc_generation_provider
        if doc_generation_provider and "/" in doc_generation_provider
        else config.doc_generation_write_model
    )
    model_id = normalize_model_id(model_id)
    provider_config = _provider_config(model_id)
    return create_model_provider(
        model_id,
        api_key=provider_config["api_key"],
        base_url=provider_config["base_url"],
        ollama_base_url=config.ollama_base_url,
    )


def _provider_config(model_id: str) -> dict[str, str | None]:
    model_id = normalize_model_id(model_id)
    provider = model_id.partition("/")[0]
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


def _parse_agent_json(text: str) -> dict[str, Any]:
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```(?:json)?\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        data = repair_json(body, return_objects=True)
    if not isinstance(data, dict):
        raise ValueError("Patch agent output must be a JSON object")
    edits = data.get("edits", [])
    if not isinstance(edits, list):
        data["edits"] = []
    else:
        data["edits"] = [edit for edit in edits if isinstance(edit, dict)]
    for key in ("obligations_satisfied", "obligations_unsatisfied"):
        value = data.get(key, [])
        data[key] = value if isinstance(value, list) else []
    return data


def _agent_confidence(data: dict[str, Any]) -> float:
    value = data.get("confidence")
    if value is None and data.get("edits"):
        return max(float(config.git_diff_patch_min_confidence), 0.85)
    if value is None:
        return 0.0
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _summarize_agent_output(
    data: dict[str, Any],
    chunk: DiffChunk,
    section: DocSection | None = None,
    output_cap: int | None = None,
    output_tokens: int | None = None,
) -> dict[str, Any]:
    return {
        "chunk_index": chunk.index,
        "paths": chunk.paths,
        "section": {"filename": section.filename, "heading": section.heading} if section else None,
        "change_summary": data.get("change_summary", ""),
        "inspected_context": data.get("inspected_context", []),
        "affected_docs": data.get("affected_docs", []),
        "obligations_satisfied": _coerce_string_list(data.get("obligations_satisfied")),
        "obligations_unsatisfied": _coerce_string_list(data.get("obligations_unsatisfied")),
        "edit_count": len(data.get("edits", [])),
        "confidence": _agent_confidence(data),
        "confidence_inferred": "confidence" not in data and bool(data.get("edits")),
        "output_cap": output_cap,
        "output_tokens": output_tokens,
        "hit_output_cap": _hit_output_cap(output_tokens, output_cap),
    }


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


def _hit_output_cap(output_tokens: int | None, output_cap: int | None) -> bool:
    if not output_tokens or not output_cap:
        return False
    return output_tokens >= int(output_cap * 0.98)


def _required_obligations_satisfied(obligations: list[DocObligation], parsed: dict[str, Any]) -> bool:
    required_ids = {obligation.id for obligation in obligations if obligation.required}
    if not required_ids:
        return True
    satisfied = set(_coerce_string_list(parsed.get("obligations_satisfied")))
    unsatisfied = set(_coerce_string_list(parsed.get("obligations_unsatisfied")))
    return bool(required_ids <= satisfied) and not bool(required_ids & unsatisfied)


def _apply_patches(docs_dir: Path, patches: list[dict[str, str]]) -> list[str]:
    patched, _ = _apply_patches_with_backup(docs_dir, patches)
    return patched


def _apply_patches_with_backup(docs_dir: Path, patches: list[dict[str, str]]) -> tuple[list[str], dict[str, str]]:
    patched: list[str] = []
    originals: dict[str, str] = {}
    for patch in patches:
        filename = patch.get("filename", "")
        heading = patch.get("heading", "")
        replacement = patch.get("replacement_markdown", "")
        if not filename or not replacement or "/" in filename or "\\" in filename:
            continue
        path = docs_dir / filename
        if not path.exists() or not path.name[0].isdigit():
            continue
        original = path.read_text(encoding="utf-8", errors="replace")
        if not _replacement_markdown_is_complete(original, heading, replacement):
            continue
        updated = _replace_section(original, heading, replacement)
        if updated != original:
            originals.setdefault(filename, original)
            path.write_text(updated, encoding="utf-8")
            patched.append(filename)
    return sorted(set(patched)), originals


def _restore_docs(docs_dir: Path, originals: dict[str, str]) -> None:
    for filename, original in originals.items():
        path = docs_dir / filename
        if path.exists() and path.name[0].isdigit():
            path.write_text(original, encoding="utf-8")


def _infer_satisfied_obligations(
    docs_dir: Path,
    obligations: list[DocObligation],
    patched_docs: list[str],
) -> set[str]:
    docs_text = "\n".join(
        (docs_dir / filename).read_text(encoding="utf-8", errors="replace")
        for filename in patched_docs
        if (docs_dir / filename).exists()
    )
    return {
        obligation.id
        for obligation in obligations
        if _obligation_is_reflected(obligation, docs_text)
    }


def _validate_obligations(
    docs_dir: Path,
    obligations: list[DocObligation],
    candidate_sections: list[DocSection],
    patched_docs: list[str],
    satisfied_obligation_ids: set[str],
) -> list[str]:
    if not obligations:
        return []

    errors: list[str] = []
    required_ids = {obligation.id for obligation in obligations if obligation.required}
    missing = sorted(required_ids - satisfied_obligation_ids)
    if missing:
        errors.append(f"Required obligations not satisfied: {', '.join(missing[:8])}")

    relevant_docs = sorted({section.filename for section in candidate_sections} | set(patched_docs))
    for obligation in obligations:
        if obligation.kind != "changed_count" or not obligation.required:
            continue
        stale_docs = _docs_with_stale_count_claim(docs_dir, relevant_docs, obligation)
        if stale_docs:
            errors.append(
                f"Stale numeric claim remains for {obligation.id} in {', '.join(stale_docs[:6])}"
            )

    return errors


def _obligation_is_reflected(obligation: DocObligation, text: str) -> bool:
    lowered = text.lower()
    required_terms = [term for term in obligation.required_terms if term]
    if not required_terms:
        return True
    if obligation.kind == "changed_count":
        numbers = [term for term in required_terms if term.isdigit()]
        words = [term for term in required_terms if not term.isdigit()]
        has_number = not numbers or any(re.search(rf"\b{re.escape(number)}\b", text) for number in numbers)
        has_word = not words or any(word.lower() in lowered for word in words)
        return has_number and has_word
    return any(term.lower() in lowered for term in required_terms)


def _docs_with_stale_count_claim(
    docs_dir: Path,
    filenames: list[str],
    obligation: DocObligation,
) -> list[str]:
    stale_numbers = [term for term in obligation.stale_terms if term.isdigit()]
    new_numbers = [term for term in obligation.required_terms if term.isdigit()]
    context_terms = [term for term in (*obligation.required_terms, *obligation.stale_terms) if not term.isdigit()]
    if not new_numbers or not context_terms:
        return []

    stale_docs: list[str] = []
    for filename in filenames:
        path = docs_dir / filename
        if not path.exists() or not path.name[0].isdigit():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lowered = text.lower()
        if not any(term.lower() in lowered for term in context_terms):
            continue
        has_stale = any(re.search(rf"\b{re.escape(number)}\b", text) for number in stale_numbers)
        has_conflicting_count = _contains_conflicting_count_line(text, new_numbers, context_terms)
        if not has_stale:
            has_stale = has_conflicting_count
        has_new = any(re.search(rf"\b{re.escape(number)}\b", text) for number in new_numbers)
        if not has_new or has_conflicting_count or _contains_stale_line(text, stale_numbers, context_terms):
            stale_docs.append(filename)
    return stale_docs


def _contains_conflicting_count_line(text: str, new_numbers: list[str], context_terms: list[str]) -> bool:
    new_number_set = set(new_numbers)
    for line in text.splitlines():
        lowered = line.lower()
        if not any(term.lower() in lowered for term in context_terms):
            continue
        numbers = re.findall(r"\b\d+\b", line)
        if any(number not in new_number_set for number in numbers):
            return True
    return False


def _contains_stale_line(text: str, stale_numbers: list[str], context_terms: list[str]) -> bool:
    for line in text.splitlines():
        lowered = line.lower()
        if not any(term.lower() in lowered for term in context_terms):
            continue
        if any(re.search(rf"\b{re.escape(number)}\b", line) for number in stale_numbers):
            return True
    return False


def _replacement_markdown_is_complete(text: str, heading: str, replacement: str) -> bool:
    if replacement.count("```") % 2 != 0:
        return False
    if replacement.count("`") % 2 != 0:
        return False

    headings = list(DOC_SECTION_RE.finditer(text))
    target = next((match for match in headings if match.group(2).strip() == heading), None)
    if target and not replacement.lstrip().startswith(target.group(0)):
        return False

    stripped_lines = [line for line in replacement.rstrip().splitlines() if line.strip()]
    if not stripped_lines:
        return False
    last_line = stripped_lines[-1]
    if last_line.endswith(("`", "\\", "| `")):
        return False
    if "|" in last_line and last_line.count("|") < 2:
        return False
    return True


def _replace_section(text: str, heading: str, replacement: str) -> str:
    headings = list(DOC_SECTION_RE.finditer(text))
    if not headings:
        return text
    target_idx = None
    for idx, match in enumerate(headings):
        if match.group(2).strip() == heading:
            target_idx = idx
            break
    if target_idx is None:
        return text
    start = headings[target_idx].start()
    end = headings[target_idx + 1].start() if target_idx + 1 < len(headings) else len(text)
    return text[:start] + replacement.rstrip() + "\n\n" + text[end:].lstrip()
