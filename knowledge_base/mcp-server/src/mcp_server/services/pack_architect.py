"""Persistent, single-call conversational authoring for plugin Packs.

Each user turn is accepted immediately and persisted before one direct model
request proposes a compact Pack design.  The server renders the canonical
YAML/Markdown files from that design, applies them atomically, and runs the
normal Pack loader and deterministic validator.  Pack authoring deliberately
does not use OpenCode: a bounded declarative artifact does not benefit from a
coding agent's per-file tool loop.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from json_repair import repair_json
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mcp_server.config import config
from mcp_server.providers.custom.models import ModelOptions
from mcp_server.providers.custom.providers import build_model_provider
from mcp_server.services import instance_settings_service
from mcp_server.services.pack_validation import validate_generated_pack_files
from mcp_server.services.plugin_schema import PluginPackManifest, load_plugin_manifest

logger = logging.getLogger(__name__)


class PackArchitectError(RuntimeError):
    """A safe error from an interactive Pack Architect session."""


class PackArchitectNotFound(PackArchitectError):
    pass


class PackArchitectConflict(PackArchitectError):
    pass


@dataclass(frozen=True, slots=True)
class PackArchitectSnapshot:
    id: str
    status: str
    revision: int
    source_pack_id: int | None
    platform: str | None
    model_id: str
    messages: tuple[dict[str, str], ...]
    manifest_yaml: str
    files: dict[str, str]
    validation_errors: tuple[str, ...]
    turn_status: str
    turn_stage: str | None
    turn_detail: str | None
    turn_started_at: str | None
    turn_finished_at: str | None
    turn_error: str | None

    @property
    def complete(self) -> bool:
        return (
            self.turn_status != "running"
            and bool(self.manifest_yaml)
            and not self.validation_errors
        )


_SESSION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SAFE_PLATFORM_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ALLOWED_EXTENSIONS = frozenset({".md", ".yaml", ".yml"})
_MAX_FILE_CHARS = 200_000
_MAX_BATCH_CHARS = 2_000_000
_LOCKS: dict[str, asyncio.Lock] = {}
_TASKS: dict[str, asyncio.Task[None]] = {}

_SYSTEM_PROMPT = """\
You are Nexus Pack Architect, a dedicated editor for one plugin Pack. The
server gives you the current Pack design or manifest, conversation, validation
findings, and the user's latest request. Return a compact design; the server
will deterministically render YAML and Markdown. You cannot inspect a codebase,
call tools, publish, deploy, or access anything outside this supplied Pack.

Return exactly this shape:
{
  "assistant_message": "short user-facing summary or one question",
  "needs_clarification": false,
  "design": {
    "name": "lowercase-hyphenated-name",
    "description": "what this Pack accomplishes",
    "analysis_policy": "bounded",
    "investigation": false,
    "scorecard": false,
    "focus_include": ["sql_queries", "source_summary"],
    "focus_exclude": ["binary", "static_assets", "generated_files"],
    "sections_mode": "replace",
    "sections": [{"id": "SECTION_ID", "question": "precise evidence question"}],
    "requirements": "standing evidence and uncertainty rules",
    "documents": [{
      "name": "DOCUMENT_NAME",
      "purpose": "who uses it and why",
      "phase2_sections": ["SECTION_ID"],
      "required_sections": ["Heading"],
      "required_evidence": ["source-backed facts"],
      "required_outputs": [],
      "per_unit": false,
      "instructions": "short document-specific writing guidance"
    }],
    "audience": {
      "roles": ["Integration engineer"],
      "tone": "neutral",
      "technical_depth": "deep",
      "output_organization": "sectioned"
    },
    "phase2_guidance": "short analysis guidance",
    "phase3_guidance": "short document guidance",
    "platform_guidance": "short additive guidance for the selected platform"
  }
}

Rules:
- Preserve the complete current design unless the latest request changes it.
- If intent is genuinely ambiguous, set needs_clarification=true, omit design,
  and ask exactly one concise question. Otherwise include the complete design.
- Valid focus_include values: sql_queries, ddl_schema, orm_entities,
  db_connections, config_references, routes, handlers, auth, source_summary.
- Valid focus_exclude values: binary, static_assets, generated_files.
- analysis_policy is bounded or run. sections_mode is extend or replace.
- tone is neutral, concise, or explanatory. technical_depth is summary,
  standard, or deep. output_organization is direct_answer, sectioned, or
  incident_report.
- A Pack may contain as many purposeful documents, files, analysis sections,
  and audience roles as its purpose requires. Never impose an arbitrary count.
- Section and document IDs are uppercase with underscores. Every document's
  phase2_sections must reference declared section IDs.
- Keep descriptions, questions, purposes, and guidance concise but substantive.
- Make evidence requirements and uncertainty rules explicit. Never invent
  platform facts not supplied by the user.
- Output JSON only. Do not wrap it in Markdown fences.
"""


class _ArchitectSection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    question: str = Field(min_length=8, max_length=500)


class _ArchitectDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    purpose: str = Field(min_length=8, max_length=1000)
    phase2_sections: list[str] = Field(default_factory=list)
    required_sections: list[str] = Field(min_length=1)
    required_evidence: list[str] = Field(default_factory=list)
    required_outputs: list[str] = Field(default_factory=list)
    per_unit: bool = False
    instructions: str = Field(default="", max_length=4000)


class _ArchitectAudience(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    roles: list[str] = Field(default_factory=list)
    tone: Literal["neutral", "concise", "explanatory"] = "neutral"
    technical_depth: Literal["summary", "standard", "deep"] = "standard"
    output_organization: Literal["direct_answer", "sectioned", "incident_report"] = (
        "sectioned"
    )


class _ArchitectDesign(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    description: str = Field(min_length=8, max_length=1000)
    analysis_policy: Literal["bounded", "run"] = "bounded"
    investigation: bool = False
    scorecard: bool = False
    focus_include: list[str] = Field(default_factory=list)
    focus_exclude: list[str] = Field(default_factory=list)
    sections_mode: Literal["extend", "replace"] = "replace"
    sections: list[_ArchitectSection] = Field(min_length=1)
    requirements: str = Field(min_length=8, max_length=4000)
    documents: list[_ArchitectDocument] = Field(min_length=1)
    audience: _ArchitectAudience = Field(default_factory=_ArchitectAudience)
    phase2_guidance: str = Field(default="", max_length=4000)
    phase3_guidance: str = Field(default="", max_length=4000)
    platform_guidance: str = Field(default="", max_length=4000)


class _ArchitectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    assistant_message: str = Field(min_length=1, max_length=2000)
    needs_clarification: bool = False
    design: _ArchitectDesign | None = None


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _root() -> Path:
    path = config.data_dir / "pack_architect_sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session_root(session_id: str) -> Path:
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise PackArchitectNotFound("Pack Architect session not found.")
    return _root() / session_id


def _metadata_path(session_id: str) -> Path:
    return _session_root(session_id) / "session.json"


def _write_metadata(session_id: str, payload: dict[str, Any]) -> None:
    payload.pop("opencode_session_id", None)
    path = _metadata_path(session_id)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _read_metadata(session_id: str, owner_user_id: int) -> dict[str, Any]:
    path = _metadata_path(session_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackArchitectNotFound("Pack Architect session not found.") from exc
    if not isinstance(payload, dict) or payload.get("owner_user_id") != owner_user_id:
        raise PackArchitectNotFound("Pack Architect session not found.")

    # Remove runtime-only state left by the retired Pack-specific OpenCode path.
    legacy_state = _session_root(session_id) / "opencode"
    migrated = payload.pop("opencode_session_id", None) is not None
    if legacy_state.exists():
        shutil.rmtree(legacy_state, ignore_errors=True)
        migrated = True
    if migrated:
        _write_metadata(session_id, payload)
    return payload


def _workspace(session_id: str) -> Path:
    return _session_root(session_id) / "pack"


def _read_workspace(session_id: str) -> tuple[str, dict[str, str]]:
    workspace = _workspace(session_id)
    manifest_path = workspace / "plugin.yaml"
    manifest_yaml = manifest_path.read_text(encoding="utf-8") if manifest_path.is_file() else ""
    files: dict[str, str] = {}
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or path == manifest_path or ".git" in path.parts:
            continue
        relative = path.relative_to(workspace).as_posix()
        if path.suffix in _ALLOWED_EXTENSIONS:
            files[relative] = path.read_text(encoding="utf-8")
    return manifest_yaml, files


def _validation(session_id: str, platform: str | None) -> tuple[str, dict[str, str], tuple[str, ...]]:
    manifest_yaml, files = _read_workspace(session_id)
    if not manifest_yaml.strip():
        return manifest_yaml, files, ("plugin.yaml is missing",)
    try:
        manifest = load_plugin_manifest(_workspace(session_id))
    except Exception as exc:  # noqa: BLE001 - the manifest loader raises parser-specific errors
        return manifest_yaml, files, (f"Invalid plugin.yaml: {exc}",)
    return manifest_yaml, files, tuple(
        validate_generated_pack_files(manifest, files, platform=platform)
    )


def _optional_text(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _snapshot(session_id: str, payload: dict[str, Any]) -> PackArchitectSnapshot:
    manifest, files, errors = _validation(session_id, payload.get("platform"))
    messages = tuple(
        {"role": str(item.get("role") or "assistant"), "content": str(item.get("content") or "")}
        for item in payload.get("messages", [])
        if isinstance(item, dict)
    )
    return PackArchitectSnapshot(
        id=session_id,
        status=str(payload.get("status") or "active"),
        revision=int(payload.get("revision") or 1),
        source_pack_id=(
            int(payload["source_pack_id"])
            if isinstance(payload.get("source_pack_id"), int)
            else None
        ),
        platform=payload.get("platform") if isinstance(payload.get("platform"), str) else None,
        model_id=str(payload.get("model_id") or ""),
        messages=messages,
        manifest_yaml=manifest,
        files=files,
        validation_errors=errors,
        turn_status=str(payload.get("turn_status") or "idle"),
        turn_stage=_optional_text(payload, "turn_stage"),
        turn_detail=_optional_text(payload, "turn_detail"),
        turn_started_at=_optional_text(payload, "turn_started_at"),
        turn_finished_at=_optional_text(payload, "turn_finished_at"),
        turn_error=_optional_text(payload, "turn_error"),
    )


async def _model_id() -> str:
    default = os.environ.get("MCP_PACK_AUTHOR_MODEL", "").strip() or "kimi/kimi-k2.6"
    return (
        await instance_settings_service.get_value("pack_author_model", default=default)
        or default
    ).strip()


def _safe_target(workspace: Path, relative: str) -> Path:
    candidate = Path(relative.replace("\\", "/"))
    target = (workspace / candidate).resolve()
    if (
        not relative
        or candidate.is_absolute()
        or ".." in candidate.parts
        or target.suffix not in _ALLOWED_EXTENSIONS
        or workspace.resolve() not in target.parents
    ):
        raise PackArchitectError(f"The Pack author proposed an unsafe file path: {relative[:100]}")
    return target


def _seed_artifact(
    workspace: Path,
    *,
    manifest_yaml: str | None,
    files: dict[str, str] | None,
) -> None:
    if manifest_yaml is not None:
        (workspace / "plugin.yaml").write_text(manifest_yaml, encoding="utf-8")
    for relative, content in (files or {}).items():
        target = _safe_target(workspace, relative)
        if target.name == "plugin.yaml":
            raise PackArchitectError("Supply plugin.yaml through manifest_yaml, not files.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _turn_prompt(
    snapshot: PackArchitectSnapshot, metadata: dict[str, Any], latest_message: str
) -> str:
    transcript = list(snapshot.messages[:-1])
    current_design = metadata.get("design")
    current_pack: dict[str, Any] | None = None
    current_guidance: dict[str, str] | None = None
    if not isinstance(current_design, dict):
        try:
            loaded = yaml.safe_load(snapshot.manifest_yaml)
            current_pack = loaded if isinstance(loaded, dict) else None
        except yaml.YAMLError:
            current_pack = None
        # A saved Pack may contain hand-tuned guidance that is not represented
        # in plugin.yaml. Give the first refinement turn a bounded semantic view
        # so the Architect preserves those choices instead of silently replacing
        # them with generic text. Later turns use the compact persisted design.
        remaining = 50_000
        current_guidance = {}
        for path, content in sorted(snapshot.files.items()):
            if remaining <= 0:
                break
            excerpt = content[: min(6_000, remaining)]
            current_guidance[path] = excerpt
            remaining -= len(excerpt)
    payload = {
        "platform": snapshot.platform,
        "conversation_before_latest_request": transcript,
        "deterministic_validation_errors": list(snapshot.validation_errors),
        "current_design": current_design if isinstance(current_design, dict) else None,
        "current_manifest_when_no_design_exists": current_pack,
        "current_guidance_when_no_design_exists": current_guidance,
        "latest_user_request": latest_message,
    }
    return "AUTHOR THIS PACK TURN FROM THE FOLLOWING SERVER-SUPPLIED STATE:\n" + json.dumps(
        payload, ensure_ascii=False
    )


def _parse_turn_output(text: str) -> _ArchitectResponse:
    try:
        value = json.loads(repair_json(text))
        response = _ArchitectResponse.model_validate(value)
    except (Exception, ValidationError) as exc:
        raise PackArchitectError(
            "The selected Pack author model did not return a valid Pack design."
        ) from exc
    if response.needs_clarification:
        if response.design is not None:
            raise PackArchitectError("The Pack author returned both a question and a Pack design.")
        return response
    if response.design is None:
        raise PackArchitectError("The selected Pack author model omitted the Pack design.")
    return response


def _bullets(values: list[str], *, fallback: str) -> str:
    selected = values or [fallback]
    return "\n".join(f"- {value}" for value in selected)


def _render_design(
    design: _ArchitectDesign, platform: str | None
) -> list[tuple[str, str]]:
    section_ids = {section.id for section in design.sections}
    unknown = sorted(
        {
            section_id
            for document in design.documents
            for section_id in document.phase2_sections
            if section_id not in section_ids
        }
    )
    if unknown:
        raise PackArchitectError(
            "The Pack design routes documents to undeclared sections: " + ", ".join(unknown)
        )
    duplicate_sections = len(section_ids) != len(design.sections)
    document_names = {document.name for document in design.documents}
    duplicate_documents = len(document_names) != len(design.documents)
    if duplicate_sections or duplicate_documents:
        raise PackArchitectError("The Pack design contains duplicate section or document IDs.")

    manifest: dict[str, Any] = {
        "name": design.name,
        "version": "1.0.0",
        "description": design.description,
        "phases": {
            "investigation": "run" if design.investigation else "skip",
            "analysis": design.analysis_policy,
            "scorecard": "run" if design.scorecard else "skip",
            "doc_bundles": "run",
        },
        "lens": {
            "focus": {
                "include": design.focus_include,
                "exclude": design.focus_exclude,
            },
            "sections": {
                "mode": design.sections_mode,
                "declare": [section.model_dump() for section in design.sections],
            },
            "requirements": design.requirements,
        },
        "docs": [
            {
                "name": document.name,
                "phase2_sections": document.phase2_sections,
                "required_sections": document.required_sections,
                "required_evidence": document.required_evidence,
                "required_outputs": document.required_outputs,
                "per_unit": document.per_unit,
            }
            for document in design.documents
        ],
        "audience": design.audience.model_dump(),
        "quality_gates": {
            "mode": "advisory",
            "min_doc_bytes": {document.name: 1000 for document in design.documents},
        },
    }
    # Validate the exact manifest before any workspace mutation.
    PluginPackManifest.model_validate(manifest)
    manifest_yaml = yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)

    section_lines = "\n".join(
        f"- `{section.id}` — {section.question}" for section in design.sections
    )
    document_lines = "\n".join(
        f"- `{document.name}` — {document.purpose}"
        + (" One file is produced per scanned unit." if document.per_unit else "")
        for document in design.documents
    )
    role_text = ", ".join(design.audience.roles) or "the intended project stakeholders"
    readme = f"""# {design.name}

{design.description}

## What the Pack analyzes

This Pack applies a {design.analysis_policy} analysis pass and requires every claim to remain
traceable to supplied Nexus evidence. Missing or conflicting evidence must be reported explicitly.

{section_lines}

## Documents produced

{document_lines}

## Audience and use

Write for {role_text}, using a {design.audience.tone} tone and
{design.audience.technical_depth} technical depth. The output organization is
`{design.audience.output_organization}`. Extraction and platform policy remain Nexus-owned;
this Pack only narrows attention and controls the document contract.

## Evidence rules

{design.requirements}
"""
    phase2 = f"""# Phase 2 Pack Guidance

Analyze only evidence supplied by Nexus. Answer every declared question with exact component,
file, job, query, configuration, or route names when available. Connect derived claims to their
source and mark absent, ambiguous, or contradictory evidence instead of guessing.

## Declared analysis contract

{section_lines}

## Standing requirements

{design.requirements}

## Additional guidance

{design.phase2_guidance or 'Prioritize concrete evidence chains and actionable findings over generic commentary.'}
"""
    phase3 = f"""# Phase 3 Pack Guidance

Produce source-grounded Markdown for {role_text}. Use a {design.audience.tone} tone,
{design.audience.technical_depth} technical depth, and
`{design.audience.output_organization}` organization. Preserve uncertainty labels and evidence
references from analysis. Never turn an unsupported inference into a fact.

{design.phase3_guidance or 'Keep conclusions specific, readable, and directly connected to the required evidence.'}
"""

    example_parts = [
        "# Fictional Output Style Example",
        "",
        "This illustrates structure and presentation only. Names and findings are fictional and must never be reused as evidence.",
    ]
    writes: list[tuple[str, str]] = [
        ("plugin.yaml", manifest_yaml),
        ("README.md", readme),
        ("prompts/phase2.md", phase2),
        ("prompts/phase3.md", phase3),
    ]
    for document in design.documents:
        evidence = _bullets(
            document.required_evidence,
            fallback="Source-backed facts connected to an exact evidence location",
        )
        outputs = _bullets(
            document.required_outputs,
            fallback="A clear conclusion with explicit uncertainty where evidence is incomplete",
        )
        headings = "\n".join(
            f"{index}. {heading}" for index, heading in enumerate(document.required_sections, 1)
        )
        prompt = f"""# {document.name} Instructions

Purpose: {document.purpose}

Write the document with these required headings, in this order:

{headings}

Required evidence:

{evidence}

Required outputs:

{outputs}

{document.instructions or 'Explain findings concretely, cite supplied evidence, and distinguish verified facts from uncertainty.'}
Do not invent facts, owners, runtime behavior, or remediation status.
"""
        writes.append((f"prompts/docs/{document.name}.md", prompt))
        example_parts.extend(["", f"## {document.name}", "", document.purpose])
        for heading in document.required_sections:
            example_parts.extend(
                [
                    "",
                    f"### {heading}",
                    "",
                    "Show concise, source-linked findings here. State uncertainty when the fictional evidence is incomplete.",
                ]
            )
    writes.append(("examples/output-style.md", "\n".join(example_parts) + "\n"))

    if design.investigation:
        writes.append(
            (
                "prompts/investigation.md",
                (
                    "# Investigation Guidance\n\nInvestigate the declared Pack questions using only supplied "
                    "source and runtime evidence. Record exact provenance for every claim and explicitly "
                    "mark gaps, conflicts, and unresolved uncertainty.\n"
                ),
            )
        )
    if design.scorecard:
        writes.append(
            (
                "prompts/scorecard.md",
                (
                    "# Scorecard Guidance\n\nScore only criteria supported by the declared analysis and "
                    "source evidence. Explain each rating, cite its evidence, and use an explicit unknown "
                    "state whenever the available material cannot justify a score.\n"
                ),
            )
        )
    if platform:
        writes.append(
            (
                f"prompts/extensions/platforms/{platform}.md",
                f"# {platform.title()} Additive Pack Guidance\n\nThis guidance is additive to Nexus "
                "platform policy and cannot replace or weaken it.\n\n"
                + (
                    design.platform_guidance
                    or "Apply the Pack questions to platform-specific jobs, components, configuration, and data-flow evidence when supplied."
                )
                + "\n",
            )
        )
    total_chars = sum(len(content) for _, content in writes)
    if total_chars > _MAX_BATCH_CHARS or any(len(content) > _MAX_FILE_CHARS for _, content in writes):
        raise PackArchitectError("The rendered Pack exceeds the technical artifact size limit.")
    return writes


def _apply_operations(session_id: str, writes: list[tuple[str, str]], deletes: list[str]) -> None:
    workspace = _workspace(session_id)
    session_root = _session_root(session_id)
    staged_parent = Path(tempfile.mkdtemp(prefix="pack-turn-", dir=session_root))
    staged = staged_parent / "pack"
    backup = session_root / f"pack-backup-{uuid.uuid4().hex}"
    try:
        shutil.copytree(workspace, staged)
        for relative, content in writes:
            target = _safe_target(staged, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        for relative in deletes:
            target = _safe_target(staged, relative)
            if target.is_file():
                target.unlink()
        os.replace(workspace, backup)
        try:
            os.replace(staged, workspace)
        except OSError:
            os.replace(backup, workspace)
            raise
        shutil.rmtree(backup, ignore_errors=True)
    finally:
        shutil.rmtree(staged_parent, ignore_errors=True)


async def _set_stage(
    session_id: str,
    owner_user_id: int,
    revision: int,
    stage: str,
    detail: str,
) -> dict[str, Any]:
    lock = _LOCKS.setdefault(session_id, asyncio.Lock())
    async with lock:
        payload = _read_metadata(session_id, owner_user_id)
        if int(payload.get("revision") or 0) != revision or payload.get("turn_status") != "running":
            raise PackArchitectConflict("The Pack Architect turn is no longer active.")
        payload["turn_stage"] = stage
        payload["turn_detail"] = detail
        payload["updated_at"] = _now()
        _write_metadata(session_id, payload)
        return payload


async def _finish_failed_turn(
    session_id: str,
    owner_user_id: int,
    revision: int,
    message: str,
) -> None:
    lock = _LOCKS.setdefault(session_id, asyncio.Lock())
    async with lock:
        try:
            payload = _read_metadata(session_id, owner_user_id)
        except PackArchitectNotFound:
            return
        if int(payload.get("revision") or 0) != revision:
            return
        payload["turn_status"] = "failed"
        payload["turn_stage"] = "failed"
        payload["turn_detail"] = "Your draft files were preserved."
        payload["turn_error"] = message
        payload["turn_finished_at"] = _now()
        payload["updated_at"] = payload["turn_finished_at"]
        _write_metadata(session_id, payload)


async def _execute_turn(
    session_id: str,
    owner_user_id: int,
    revision: int,
    message: str,
) -> None:
    try:
        payload = await _set_stage(
            session_id,
            owner_user_id,
            revision,
            "preparing",
            "Preparing the current Pack and validation findings.",
        )
        snapshot = _snapshot(session_id, payload)
        await _set_stage(
            session_id,
            owner_user_id,
            revision,
            "authoring",
            f"{snapshot.model_id} is designing the Pack contract and document set.",
        )
        provider = build_model_provider(snapshot.model_id)
        result = await provider.complete(
            _turn_prompt(snapshot, payload, message),
            options=ModelOptions(
                max_tokens=config.pack_author_max_output_tokens,
                temperature=0.0,
                timeout_s=float(config.pack_author_timeout_seconds),
                retries=0,
                json_mode=True,
                system_prompt=_SYSTEM_PROMPT,
            ),
        )
        if not result.success:
            raise PackArchitectError(
                "The selected Pack author model did not complete the Pack design."
            )
        response = _parse_turn_output(result.text)
        writes: list[tuple[str, str]] = []
        deletes: list[str] = []
        if response.design is not None:
            writes = _render_design(response.design, snapshot.platform)
            rendered_paths = {path for path, _content in writes}
            deletes = sorted(
                path for path in snapshot.files if path not in rendered_paths
            )
        await _set_stage(
            session_id,
            owner_user_id,
            revision,
            "applying",
            (
                f"Rendering and applying {len(writes)} canonical Pack file(s) atomically."
                if response.design is not None
                else "No files changed while waiting for your answer."
            ),
        )
        if response.design is not None:
            _apply_operations(session_id, writes, deletes)
        await _set_stage(
            session_id,
            owner_user_id,
            revision,
            "validating",
            "Checking manifest consistency, required guidance, and substantive content.",
        )
        lock = _LOCKS.setdefault(session_id, asyncio.Lock())
        async with lock:
            payload = _read_metadata(session_id, owner_user_id)
            if int(payload.get("revision") or 0) != revision:
                raise PackArchitectConflict("The Pack Architect session changed during the turn.")
            payload.setdefault("messages", []).append(
                {"role": "assistant", "content": response.assistant_message}
            )
            if response.design is not None:
                payload["design"] = response.design.model_dump(mode="json")
            payload["turn_status"] = "idle"
            payload["turn_stage"] = "complete"
            payload["turn_detail"] = (
                f"Rendered {len(writes)} file(s); deterministic validation is ready."
                if response.design is not None
                else "The Pack Architect needs your answer before changing the draft."
            )
            payload["turn_error"] = None
            payload["turn_finished_at"] = _now()
            payload["updated_at"] = payload["turn_finished_at"]
            _write_metadata(session_id, payload)
    except asyncio.CancelledError:
        await _finish_failed_turn(
            session_id, owner_user_id, revision, "The Pack Architect turn was cancelled."
        )
        raise
    except PackArchitectError as exc:
        await _finish_failed_turn(session_id, owner_user_id, revision, str(exc))
    except Exception:
        logger.exception("Pack Architect direct model turn failed")
        await _finish_failed_turn(
            session_id,
            owner_user_id,
            revision,
            "The selected Pack author model is unavailable. Your draft files were preserved.",
        )


def _task_finished(session_id: str, task: asyncio.Task[None]) -> None:
    if _TASKS.get(session_id) is task:
        _TASKS.pop(session_id, None)
    if not task.cancelled():
        _ = task.exception()


async def create_session(
    owner_user_id: int,
    *,
    source_pack_id: int | None = None,
    platform: str | None = None,
    manifest_yaml: str | None = None,
    files: dict[str, str] | None = None,
    inherit_starter: bool = True,
) -> PackArchitectSnapshot:
    if platform:
        platform = platform.strip().lower()
        if not _SAFE_PLATFORM_RE.fullmatch(platform):
            raise PackArchitectError("Platform may contain only letters, numbers, hyphens, or underscores.")
    session_id = uuid.uuid4().hex
    root = _session_root(session_id)
    workspace = _workspace(session_id)
    root.mkdir(parents=True, mode=0o700)
    starter = config.data_dir / "swap_pack_starter"
    workspace.mkdir(parents=True)
    if inherit_starter and starter.is_dir():
        for source in sorted(starter.rglob("*")):
            if not source.is_file() or source.suffix not in _ALLOWED_EXTENSIONS:
                continue
            relative = source.relative_to(starter)
            if any(part.startswith(".") for part in relative.parts):
                continue
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    _seed_artifact(workspace, manifest_yaml=manifest_yaml, files=files)
    now = _now()
    payload: dict[str, Any] = {
        "id": session_id,
        "owner_user_id": owner_user_id,
        "status": "active",
        "revision": 1,
        "source_pack_id": source_pack_id,
        "platform": platform,
        "model_id": await _model_id(),
        "messages": [],
        "turn_status": "idle",
        "turn_stage": None,
        "turn_detail": None,
        "turn_started_at": None,
        "turn_finished_at": None,
        "turn_error": None,
        "created_at": now,
        "updated_at": now,
    }
    _write_metadata(session_id, payload)
    return _snapshot(session_id, payload)


async def get_session(session_id: str, owner_user_id: int) -> PackArchitectSnapshot:
    payload = _read_metadata(session_id, owner_user_id)
    if payload.get("turn_status") == "running" and session_id not in _TASKS:
        await _finish_failed_turn(
            session_id,
            owner_user_id,
            int(payload.get("revision") or 0),
            "The Pack Architect turn was interrupted by a service restart. Your draft files were preserved.",
        )
        payload = _read_metadata(session_id, owner_user_id)
    return _snapshot(session_id, payload)


async def get_latest_session(owner_user_id: int) -> PackArchitectSnapshot:
    candidates: list[tuple[float, str]] = []
    for path in _root().glob("*/session.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (
                isinstance(payload, dict)
                and payload.get("owner_user_id") == owner_user_id
                and payload.get("status") == "active"
            ):
                candidates.append((path.stat().st_mtime, path.parent.name))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
    if not candidates:
        raise PackArchitectNotFound("No active Pack Architect session was found.")
    return await get_session(max(candidates)[1], owner_user_id)


async def run_turn(
    session_id: str,
    owner_user_id: int,
    message: str,
    *,
    expected_revision: int,
) -> PackArchitectSnapshot:
    cleaned = message.strip()
    if not cleaned:
        raise PackArchitectError("Tell the Pack Architect what to create or change.")
    lock = _LOCKS.setdefault(session_id, asyncio.Lock())
    async with lock:
        payload = _read_metadata(session_id, owner_user_id)
        if payload.get("status") != "active":
            raise PackArchitectConflict("This Pack Architect session is no longer active.")
        if payload.get("turn_status") == "running":
            raise PackArchitectConflict("The Pack Architect is already working on this draft.")
        if int(payload.get("revision") or 0) != expected_revision:
            raise PackArchitectConflict("The Pack Architect session changed; reload it and retry.")
        accepted_revision = expected_revision + 1
        payload.setdefault("messages", []).append({"role": "user", "content": cleaned})
        payload["revision"] = accepted_revision
        payload["turn_status"] = "running"
        payload["turn_stage"] = "queued"
        payload["turn_detail"] = "Turn accepted and saved. Preparing the Pack author request."
        payload["turn_started_at"] = _now()
        payload["turn_finished_at"] = None
        payload["turn_error"] = None
        payload["updated_at"] = payload["turn_started_at"]
        _write_metadata(session_id, payload)

    task = asyncio.create_task(
        _execute_turn(session_id, owner_user_id, accepted_revision, cleaned),
        name=f"pack-architect-{session_id}",
    )
    _TASKS[session_id] = task
    task.add_done_callback(lambda completed: _task_finished(session_id, completed))
    return _snapshot(session_id, payload)


async def cancel_turn(
    session_id: str, owner_user_id: int, *, expected_revision: int
) -> PackArchitectSnapshot:
    payload = _read_metadata(session_id, owner_user_id)
    if int(payload.get("revision") or 0) != expected_revision:
        raise PackArchitectConflict("The Pack Architect session changed; reload it and retry.")
    if payload.get("turn_status") != "running":
        return _snapshot(session_id, payload)
    task = _TASKS.get(session_id)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    else:
        await _finish_failed_turn(
            session_id, owner_user_id, expected_revision, "The Pack Architect turn was cancelled."
        )
    return _snapshot(session_id, _read_metadata(session_id, owner_user_id))


async def abandon_session(session_id: str, owner_user_id: int, *, expected_revision: int) -> None:
    payload = _read_metadata(session_id, owner_user_id)
    if int(payload.get("revision") or 0) != expected_revision:
        raise PackArchitectConflict("The Pack Architect session changed; reload it and retry.")
    if payload.get("turn_status") == "running":
        await cancel_turn(session_id, owner_user_id, expected_revision=expected_revision)
        payload = _read_metadata(session_id, owner_user_id)
    payload["status"] = "abandoned"
    payload["revision"] = expected_revision + 1
    payload["updated_at"] = _now()
    _write_metadata(session_id, payload)


async def mark_saved(
    session_id: str,
    owner_user_id: int,
    *,
    expected_revision: int,
    pack_id: int,
) -> None:
    payload = _read_metadata(session_id, owner_user_id)
    if int(payload.get("revision") or 0) != expected_revision:
        raise PackArchitectConflict("The Pack Architect session changed; reload it and retry.")
    if payload.get("turn_status") == "running":
        raise PackArchitectConflict("Wait for the Pack Architect turn to finish before saving.")
    payload["status"] = "saved"
    payload["revision"] = expected_revision + 1
    payload["saved_pack_id"] = pack_id
    payload["updated_at"] = _now()
    _write_metadata(session_id, payload)


def manifest_for_snapshot(snapshot: PackArchitectSnapshot) -> PluginPackManifest:
    if snapshot.turn_status == "running":
        raise PackArchitectConflict("Wait for the Pack Architect turn to finish before saving.")
    if snapshot.validation_errors:
        raise PackArchitectError("Pack is not ready to save: " + "; ".join(snapshot.validation_errors))
    return load_plugin_manifest(_workspace(snapshot.id))
