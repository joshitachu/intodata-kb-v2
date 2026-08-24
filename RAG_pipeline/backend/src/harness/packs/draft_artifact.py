"""The in-progress artifact of a chat-authored pack: one ``plugin.yaml`` + files.

This replaces ``PackArtifactV3``. The old artifact was a second, weaker pack
format: it could only append documents to the standard set (``harness.py``
routed it an EMPTY ``_doc_section_bundle_map``, so no analysis reached it) and it
could never change what the pipeline looked at. Everything it expressed is a
strict subset of ``plugin.yaml``'s ``docs:`` block.

So a draft now carries the real thing -- the manifest text a pack is made of --
and MCP stays the only authority on whether it is valid. The split is
deliberate:

* **This module** validates STRUCTURE, synchronously, for chat and editor
  feedback: does it parse, does it have the blocks a pack needs, does it stay
  inside the user authority tier. Cheap, no network, safe to run on every
  keystroke-sized mutation.
* **MCP** validates ADMISSIBILITY at save time, through the real
  ``load_plugin_manifest``. That is the gate on persistence.

Local validation is therefore allowed to be more permissive than MCP, never
less: it must not accept something MCP would reject on authority grounds (that
is the security-relevant direction), but it need not reproduce MCP's full
loader.
"""

from __future__ import annotations

import re
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_MANIFEST_CHARS = 200_000
MAX_FILE_CHARS = 200_000

#: Blocks that alter pipeline execution, provenance, or low-level evidence
#: plumbing. Mirrors the Pack Architect authority boundary. A user-authored pack may
#: never carry these -- the server owns them. Refused here as well as in MCP,
#: because a draft is model output and the authority boundary should not depend
#: on a single check surviving a refactor.
FOUNDATION_ONLY_KEYS = frozenset(
    {
        "certification",
        "evidence_agents",
        "evidence_profiles",
        "governance",
        "layer",
        "schema_version",
        "steps",
    }
)

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,79}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_DOC_NAME_RE = re.compile(r"^[A-Z0-9][A-Z0-9_]{2,79}$")
_FILE_RE = re.compile(r"^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._/-]{0,199}\.(md|yaml|yml)$")


class PackDraftInvalid(ValueError):
    """The draft is not a structurally usable pack. Carries display errors."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors) or "Pack draft is invalid")
        self.errors = errors


class PackDraftArtifact(BaseModel):
    """A ``plugin.yaml`` manifest plus the pack files that accompany it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_yaml: str = Field(min_length=1, max_length=MAX_MANIFEST_CHARS)
    files: dict[str, str] = Field(default_factory=dict)

    @field_validator("files")
    @classmethod
    def _bounded_safe_files(cls, value: dict[str, str]) -> dict[str, str]:
        for path, content in value.items():
            if not _FILE_RE.fullmatch(path) or path.startswith("/"):
                raise ValueError(f"unsafe pack file path: {path[:80]}")
            if len(content) > MAX_FILE_CHARS:
                raise ValueError(f"pack file is too large: {path[:80]}")
        return value

    # -- derived views over the manifest -------------------------------------
    #
    # Parsed on access rather than stored, so the manifest text stays the single
    # source of truth. A draft whose YAML does not parse still round-trips
    # through storage; it simply reports invalid.

    def manifest(self) -> dict[str, Any]:
        """The manifest as a mapping. Raises :class:`PackDraftInvalid`."""
        try:
            loaded = yaml.safe_load(self.manifest_yaml)
        except yaml.YAMLError as exc:
            raise PackDraftInvalid([f"manifest is not valid YAML: {_yaml_reason(exc)}"]) from exc
        if not isinstance(loaded, dict):
            raise PackDraftInvalid(["manifest must be a YAML mapping"])
        return loaded

    @property
    def name(self) -> str:
        try:
            value = self.manifest().get("name")
        except PackDraftInvalid:
            return ""
        return value if isinstance(value, str) else ""

    @property
    def version(self) -> str:
        try:
            value = self.manifest().get("version")
        except PackDraftInvalid:
            return ""
        return value if isinstance(value, str) else ""

    @property
    def doc_names(self) -> tuple[str, ...]:
        """Declared output documents, in manifest order."""
        try:
            docs = self.manifest().get("docs")
        except PackDraftInvalid:
            return ()
        if not isinstance(docs, list):
            return ()
        return tuple(
            item["name"]
            for item in docs
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        )

    def audience_render(self) -> dict[str, str]:
        """Bounded presentation dimensions, for render/provider metadata.

        Replaces ``normalized_audience(PackArtifactV3)``. Only the closed-enum
        dimensions travel; ``roles`` is bounded free text and is deliberately
        not coerced into a presentation enum.
        """
        try:
            audience = self.manifest().get("audience")
        except PackDraftInvalid:
            return {}
        if not isinstance(audience, dict):
            return {}
        return {
            key: value
            for key in ("tone", "technical_depth", "output_organization")
            if isinstance(value := audience.get(key), str) and value
        }

    def structural_errors(self) -> list[str]:
        """Everything that makes this draft unusable as a pack, for display."""
        try:
            manifest = self.manifest()
        except PackDraftInvalid as exc:
            return exc.errors

        errors: list[str] = []

        # Authority boundary first: it is the security-relevant check, and a
        # violating draft must never be reported as merely "incomplete".
        for key in sorted(FOUNDATION_ONLY_KEYS & {str(k) for k in manifest}):
            errors.append(f"a pack you author cannot set the server-owned field: {key}")

        name = manifest.get("name")
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            errors.append("name must be a lowercase kebab-case identifier")

        version = manifest.get("version")
        if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
            errors.append("version must look like 1.0.0")

        description = manifest.get("description")
        if not isinstance(description, str) or len(description.strip()) < 12:
            errors.append("description must be a specific one-line summary")

        errors.extend(_doc_errors(manifest.get("docs")))
        errors.extend(_lens_errors(manifest.get("lens")))

        return errors[:20]


def _doc_errors(docs: Any) -> list[str]:
    if not isinstance(docs, list) or not docs:
        return ["docs must declare at least one output document"]
    errors: list[str] = []
    seen: set[str] = set()
    for entry in docs:
        if not isinstance(entry, dict):
            errors.append("each docs entry must be a mapping")
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not _DOC_NAME_RE.fullmatch(name):
            errors.append("each document name must be UPPER_SNAKE_CASE")
            continue
        if name in seen:
            errors.append(f"duplicate document name: {name}")
        seen.add(name)
    return errors


def _lens_errors(lens: Any) -> list[str]:
    """A lens is optional; a malformed one is not.

    A pack with no lens behaves exactly as packs did before the block existed,
    so absence is legitimate and must not be reported as an error.
    """
    if lens is None:
        return []
    if not isinstance(lens, dict):
        return ["lens must be a mapping"]
    sections = lens.get("sections")
    if sections is None:
        return []
    if not isinstance(sections, dict):
        return ["lens.sections must be a mapping"]
    errors: list[str] = []
    mode = sections.get("mode")
    if mode is not None and mode not in {"extend", "replace"}:
        errors.append("lens.sections.mode must be extend or replace")
    declare = sections.get("declare")
    if declare is not None:
        if not isinstance(declare, list):
            errors.append("lens.sections.declare must be a list")
    return errors


def _yaml_reason(exc: yaml.YAMLError) -> str:
    mark = getattr(exc, "problem_mark", None)
    problem = getattr(exc, "problem", None) or "could not be parsed"
    if mark is not None:
        return f"{problem} at line {mark.line + 1}"
    return str(problem)
