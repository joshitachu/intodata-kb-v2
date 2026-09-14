"""Versioned, digest-verified platform investigation methods.

A documentation run hands a model two very different kinds of text: evidence
about THIS codebase, and method -- how to read a Talend export at all. The
second kind used to be inline prompt prose, which meant it had no version, no
provenance and no way to tell whether the guide a run used was the guide someone
reviewed.

This module makes method a versioned artifact:

* it lives on disk under ``data/investigation_methods/<platform>/``;
* ``method.yaml`` records where it came from (upstream repository and revision),
  what each file is for, and the sha256 of every file **as stored in this
  repository**;
* the loader verifies every digest and **fails closed**. A guide edited without
  updating its digest is an error, not a silent drift. Method that a model is
  told to treat as authority must be provably the reviewed text.

Two boundaries are deliberate.

**Per platform, never shared.** Talend parsing guidance handed to a Workato or
generic scan is confidently wrong instruction, which is worse than none:
``routes/``, ``.item`` pairs and ``tRunJob`` do not exist there, so a model
following it reports absences that are artefacts of the wrong method.
``load_investigation_method`` returns ``None`` for any platform without its own
directory and ``materialize_investigation_method`` then stages nothing.

**Staging only.** The materialised copy lands in ``<staging>/_method/<platform>/``,
which is NOT in ``phase4_cleanup.PUBLISHED_STAGING_DIRS``, so Phase 4 deletes it
before publication. Method is prompt input, not a deliverable; the durable record
of which method a run used is the manifest digest set, not a copy of the guides
in every customer's document tree.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from mcp_server.config import config

logger = logging.getLogger(__name__)

#: Directory name under ``config.data_dir`` holding one subdirectory per platform.
METHODS_DIRNAME = "investigation_methods"

#: Where a materialised method lands inside the run's staging directory.
#: Staging-only by design -- see the module docstring.
STAGED_METHOD_DIRNAME = "_method"

#: Manifest filename inside a method source directory.
METHOD_MANIFEST_FILENAME = "method.yaml"

#: Manifest filename written beside the staged copy.
STAGED_MANIFEST_FILENAME = "manifest.json"

MethodFileRole = Literal["profile", "core", "di", "esb", "behaviour", "nexus-adaptation"]

#: One line per role, for an agent prompt. Keyed on role rather than filename so
#: an upstream rename does not silently drop a guide's description.
_ROLE_PURPOSE: dict[str, str] = {
    "profile": (
        "What the platform is, the artifact-format gate, and which landscape "
        "fields are recoverable from an export versus runtime-only."
    ),
    "core": (
        "Workspace layout, artifact types, context variables, properties "
        "parsing, and inactive-item filtering. Shared by DI and ESB."
    ),
    "di": (
        "Process jobs: t* components, schemas, tMap, tRunJob, dynamic SQL and "
        "custom-code lineage, and the DI call graph."
    ),
    "esb": (
        "Routes, joblets and routelets: c* Camel components, cTalendJob "
        "bindings, shared-channel inference, and the ESB call graph."
    ),
    "behaviour": (
        "Runtime and component behaviour, and the platform facilities that "
        "pre-empt a finding. Not a parsing guide."
    ),
    "nexus-adaptation": (
        "READ FIRST. How these guides map onto a Nexus run: the read-only "
        "_workspace/ layout, scanner-computed facts, redaction rules, how "
        "output is submitted, and which parts of the method do not apply."
    ),
}

#: Roles whose one-line purpose is unknown fall back to this rather than to "".
_UNKNOWN_PURPOSE = "Platform investigation guidance."

#: Prompt-index ordering. The Nexus adaptation goes FIRST because it is the only
#: file that reconciles the upstream guides' procedural instructions -- write a
#: file, run a shell, fetch a URL -- with this runtime. A model that reads a
#: parsing guide before it reads the adaptation follows instructions that cannot
#: work here. Manifest order decides the rest.
_PROMPT_ROLE_RANK: dict[str, int] = {"nexus-adaptation": 0}
_DEFAULT_PROMPT_RANK = 1


class InvestigationMethodError(RuntimeError):
    """A method directory exists but cannot be trusted.

    Raised rather than degraded on purpose. Every other failure in the runtime
    lane degrades, because a missing runtime fact makes a document thinner. A
    method whose bytes do not match its manifest makes a document *confidently
    wrong* while still looking provenanced, so this one fails closed.
    """


class InvestigationMethodFile(BaseModel):
    """One file of a method, with the digest that proves it is the reviewed text."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    role: MethodFileRole
    sha256: str = Field(min_length=64, max_length=64)

    @property
    def purpose(self) -> str:
        return _ROLE_PURPOSE.get(self.role, _UNKNOWN_PURPOSE)


class InvestigationMethodSource(BaseModel):
    """Where the method text came from, so a reviewer can diff it upstream."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    repository: str = ""
    revision: str = ""
    adapted_on: str = ""


class InvestigationMethodUpdate(BaseModel):
    """One import or re-import of the upstream method."""

    model_config = ConfigDict(extra="forbid")

    revision: str = ""
    date: str = ""
    note: str = ""


class InvestigationMethod(BaseModel):
    """A loaded, digest-verified method for one platform."""

    model_config = ConfigDict(extra="forbid")

    platform: str = Field(min_length=1)
    method_version: str = Field(min_length=1)
    source: InvestigationMethodSource = Field(default_factory=InvestigationMethodSource)
    files: list[InvestigationMethodFile] = Field(min_length=1)
    update_log: list[InvestigationMethodUpdate] = Field(default_factory=list)
    #: Absolute directory the files were verified in. Populated by the loader,
    #: not by the manifest, so a manifest can never point the reader elsewhere.
    root: Path | None = None

    @property
    def revision(self) -> str:
        """The upstream revision this method was adapted from."""
        return self.source.revision

    @property
    def filenames(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.files)

    def metadata_payload(self) -> dict[str, object]:
        """Method provenance plus file digests, for the staged manifest.

        ``root`` is deliberately excluded: it is a path on the machine that ran
        generation and means nothing to a reader of the staged copy.
        """
        return {
            "platform": self.platform,
            "method_version": self.method_version,
            "source": self.source.model_dump(mode="json"),
            "files": [
                {
                    "name": item.name,
                    "role": item.role,
                    "sha256": item.sha256,
                    "purpose": item.purpose,
                }
                for item in self.files
            ],
            "update_log": [entry.model_dump(mode="json") for entry in self.update_log],
        }


def methods_root() -> Path:
    """Root of the method library.

    Resolved through ``config.data_dir`` on every call rather than captured at
    import time, so a test can monkeypatch ``config.data_dir`` and a deployment
    can relocate the data directory without a code change.
    """
    return config.data_dir / METHODS_DIRNAME


def _platform_key(platform: str) -> str:
    return (platform or "").strip().lower()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_investigation_method(platform: str) -> InvestigationMethod | None:
    """Load and verify the method for ``platform``.

    Returns ``None`` when the platform has no method directory -- the normal
    case for Workato, Lobster and generic scans. Raises
    ``InvestigationMethodError`` when a directory exists but its contents do not
    match its manifest, because a partially-trustworthy method is not usable.
    """
    key = _platform_key(platform)
    if not key:
        return None

    root = methods_root() / key
    if not root.is_dir():
        return None

    manifest_path = root / METHOD_MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise InvestigationMethodError(
            f"Investigation method for platform '{key}' has no {METHOD_MANIFEST_FILENAME}: "
            f"{manifest_path}. A method directory without a manifest has no provenance "
            f"and no digests, so its files cannot be verified."
        )

    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise InvestigationMethodError(
            f"Investigation method manifest is not valid YAML: {manifest_path}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise InvestigationMethodError(
            f"Investigation method manifest must be a YAML mapping: {manifest_path}"
        )

    try:
        method = InvestigationMethod.model_validate({**raw, "root": root})
    except Exception as exc:  # pydantic ValidationError, kept as one failure mode
        raise InvestigationMethodError(
            f"Investigation method manifest is invalid: {manifest_path}: {exc}"
        ) from exc

    if _platform_key(method.platform) != key:
        raise InvestigationMethodError(
            f"Investigation method manifest declares platform '{method.platform}' but "
            f"lives in directory '{key}': {manifest_path}. The directory is what a "
            f"caller looks up by, so a disagreement would stage the wrong platform's "
            f"method."
        )

    _verify_digests(method, root, manifest_path)
    return method


def _verify_digests(method: InvestigationMethod, root: Path, manifest_path: Path) -> None:
    """Fail closed on any disagreement between the manifest and the directory."""
    problems: list[str] = []
    seen: set[str] = set()

    for item in method.files:
        if item.name in seen:
            problems.append(f"'{item.name}' is listed more than once")
            continue
        seen.add(item.name)

        path = root / item.name
        # A manifest entry naming a path outside its own directory would let a
        # method stage arbitrary repository files under a provenanced name.
        if "/" in item.name or "\\" in item.name or item.name in {".", ".."}:
            problems.append(f"'{item.name}' is not a plain filename inside the method directory")
            continue
        if not path.is_file():
            problems.append(f"'{item.name}' is listed in the manifest but missing from disk")
            continue
        actual = _digest(path)
        if actual != item.sha256:
            problems.append(
                f"'{item.name}' sha256 mismatch: manifest {item.sha256}, file {actual}"
            )

    # The reverse direction matters just as much. An unlisted guide dropped into
    # the directory would be staged with no provenance and no digest, reading to
    # a model as though it had been reviewed.
    unlisted = sorted(
        path.name
        for path in root.glob("*.md")
        if path.is_file() and path.name not in seen
    )
    problems.extend(
        f"'{name}' is present on disk but not listed in the manifest" for name in unlisted
    )

    if problems:
        raise InvestigationMethodError(
            f"Investigation method for platform '{method.platform}' failed verification "
            f"({manifest_path}): "
            + "; ".join(problems)
            + ". Recompute the sha256 values in the manifest after editing a method file; "
            "do not weaken this check."
        )


def staged_method_dir(staging_dir: Path, platform: str) -> Path:
    """Where ``materialize_investigation_method`` puts the method for ``platform``."""
    return Path(staging_dir) / STAGED_METHOD_DIRNAME / _platform_key(platform)


def materialize_investigation_method(staging_dir: Path, platform: str) -> Path | None:
    """Copy the verified method for ``platform`` into the run's staging directory.

    Returns the staged directory, or ``None`` when the platform has no method.
    Idempotent: a rerun over byte-identical output writes nothing, so a resumed
    run does not churn the staging tree or invalidate a provider prefix cache
    that includes these files.
    """
    method = load_investigation_method(platform)
    if method is None:
        logger.debug("No investigation method for platform %r; staging nothing", platform)
        return None
    root = method.root
    if root is None:  # defensive: the loader always sets it
        raise InvestigationMethodError(
            f"Investigation method for platform '{method.platform}' has no resolved root"
        )

    target = staged_method_dir(staging_dir, method.platform)
    target.mkdir(parents=True, exist_ok=True)

    for item in method.files:
        _write_if_changed_bytes(target / item.name, (root / item.name).read_bytes())

    manifest = {
        "schema_version": 1,
        **method.metadata_payload(),
        "staged_files": [item.name for item in method.files],
    }
    _write_if_changed_bytes(
        target / STAGED_MANIFEST_FILENAME,
        (json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode("utf-8"),
    )
    return target


def _write_if_changed_bytes(path: Path, payload: bytes) -> None:
    """Write only when the bytes differ, then atomically."""
    if path.is_file() and path.read_bytes() == payload:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def method_prompt_index(staging_dir: Path, platform: str) -> str:
    """An ASCII bullet list of the STAGED method files, for an agent prompt.

    Built from what is actually on disk, not from the manifest alone: a prompt
    that points a model at a file the workspace does not contain spends a tool
    call to discover the platform lied to it. Returns "" when nothing is staged.
    """
    method = load_investigation_method(platform)
    if method is None:
        return ""
    target = staged_method_dir(staging_dir, method.platform)
    if not target.is_dir():
        return ""

    relative_root = f"{STAGED_METHOD_DIRNAME}/{_platform_key(method.platform)}"
    ordered = sorted(
        enumerate(method.files),
        key=lambda pair: (
            _PROMPT_ROLE_RANK.get(pair[1].role, _DEFAULT_PROMPT_RANK),
            pair[0],
        ),
    )
    lines: list[str] = []
    for _, item in ordered:
        if not (target / item.name).is_file():
            continue
        lines.append(f"- {relative_root}/{item.name} [{item.role}] -- {item.purpose}")
    if not lines:
        return ""

    header = (
        f"{method.platform.upper()} INVESTIGATION METHOD ({method.method_version}, "
        f"adapted from {method.source.name or 'upstream'} "
        f"{method.source.revision[:12] or 'unknown revision'})"
    )
    body = [
        header,
        "",
        (
            "These files are method, not evidence. They describe how to read this "
            "platform's exports; they say nothing about this project."
        ),
        "",
        *lines,
    ]
    text = "\n".join(body)
    # The whole corpus this renders from is ASCII, so a non-ASCII byte here means
    # an upstream file changed shape. Say so rather than emitting it: generated
    # documents target Windows cp1252.
    return text.encode("ascii", errors="replace").decode("ascii")
