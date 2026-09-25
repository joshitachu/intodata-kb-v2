"""Versioned, digest-verified investigation methods.

A documentation run hands a model two very different kinds of text: evidence
about THIS codebase, and method -- how to read a Talend export at all. The
second kind used to be inline prompt prose, which meant it had no version, no
provenance and no way to tell whether the guide a run used was the guide someone
reviewed.

This module makes method a versioned artifact:

* it lives on disk under ``data/investigation_methods/<platform>/``, plus
  ``_shared/`` for the platform-agnostic half;
* ``method.yaml`` records where it came from (upstream repository and revision),
  what each file is for, and the sha256 of every file **as stored in this
  repository**;
* the loader verifies every digest and **fails closed**. A guide edited without
  updating its digest is an error, not a silent drift. Method that a model is
  told to treat as authority must be provably the reviewed text.

Three boundaries are deliberate.

**Parsing method is per platform, never shared.** Talend parsing guidance handed
to a Workato or generic scan is confidently wrong instruction, which is worse
than none: ``routes/``, ``.item`` pairs and ``tRunJob`` do not exist there, so a
model following it reports absences that are artefacts of the wrong method.
``load_investigation_method`` returns ``None`` for any platform without its own
directory, and ``materialize_investigation_method`` then stages no platform
guides.

**Vocabulary and self-check are platform-agnostic, and every run gets them.**
``_shared/`` holds the confidence model (Basis / Ceiling / Aggregation) and the
pre-delivery validation checklist. Neither describes an artifact format -- both
upstream files declare ``applies to: all platforms`` -- and every platform guide
grades its findings in exactly that vocabulary. Filing them under ``talend/``
would have left a Workato run with two bad options: miss the grading vocabulary
entirely, or inherit Talend parsing rules in order to get it. So they live in
their own namespace, and the shared method is staged for EVERY platform,
including the platforms that have no directory of their own.

``_shared`` is not a platform. ``load_investigation_method("_shared")`` returns
``None`` and ``available_platforms()`` does not list it, because a caller asking
for a platform's parsing method must never be handed grading vocabulary in its
place -- that is the same class of wrong-method error as handing Workato the
Talend guides. The rule is general, not a name check: any directory whose name
starts with ``_`` is a non-platform method namespace.

**Staging only.** The materialised copies land in ``<staging>/_method/<platform>/``
and ``<staging>/_method/_shared/``. ``_method`` is NOT in
``phase4_cleanup.PUBLISHED_STAGING_DIRS``, so Phase 4 deletes it before
publication. Method is prompt input, not a deliverable; the durable record of
which method a run used is the manifest digest set, not a copy of the guides in
every customer's document tree.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Literal

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from mcp_server.config import config

logger = logging.getLogger(__name__)

#: Directory name under ``config.data_dir`` holding one subdirectory per platform,
#: plus the ``_shared`` namespace.
METHODS_DIRNAME = "investigation_methods"

#: Where a materialised method lands inside the run's staging directory.
#: Staging-only by design -- see the module docstring.
STAGED_METHOD_DIRNAME = "_method"

#: Manifest filename inside a method source directory.
METHOD_MANIFEST_FILENAME = "method.yaml"

#: Manifest filename written beside the staged copy.
STAGED_MANIFEST_FILENAME = "manifest.json"

#: Directory name of the platform-agnostic method namespace. Not a platform --
#: see the module docstring.
SHARED_METHOD_KEY = "_shared"

MethodFileRole = Literal[
    "profile",
    "core",
    "di",
    "esb",
    "behaviour",
    "nexus-adaptation",
    # Platform-agnostic roles. Only `_shared` carries these; a platform manifest
    # declaring one would be restating vocabulary that already exists once.
    "confidence",
    "validation",
    # Assessment reference catalogues, also platform-agnostic. These are the
    # five files the Phase 2.6 scorecard is written against. They lived
    # unpinned under ``src/mcp_server/templates/`` until 2026-09-25, which made
    # this module the SECOND mechanism owning the method layer rather than the
    # only one; they are here so there is one.
    "scoring-rubric",
    "anti-patterns",
    "red-flags",
    "security-checklist",
    "pattern-library",
]

#: Roles a consumer resolves BY ROLE rather than by filename, because the file
#: it wants is defined by what the file is for. Keyed this way so an upstream
#: rename is a manifest edit and not a code change -- the same argument that
#: keys ``_ROLE_PURPOSE`` on role. ``red-flags.md`` and ``security-checklist.md``
#: already carry names upstream no longer uses, so this is not hypothetical.
SCORECARD_ROLES: tuple[str, ...] = (
    "scoring-rubric",
    "anti-patterns",
    "red-flags",
    "security-checklist",
    "pattern-library",
)

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
    "confidence": (
        "The Basis/Ceiling/Aggregation vocabulary every finding is graded with. "
        "Defines when a claim may be called Confirmed and what a locus is. "
        "Applies to every platform."
    ),
    "validation": (
        "The pre-delivery and peer-review self-check applied before a document "
        "is considered done. Applies to every platform."
    ),
    "scoring-rubric": (
        "Assessment reference: the dimensions and 1-5 level criteria the "
        "scorecard is scored against. Reference catalogue, not a parsing guide "
        "and not evidence about this estate."
    ),
    "anti-patterns": (
        "Assessment reference: the AP-xxx-nnn anti-pattern catalogue, with "
        "detection hints. Cite by ID; verify each against this estate's own "
        "evidence before reporting it."
    ),
    "red-flags": (
        "Assessment reference: the RF-xxx-nnn catalogue of issues that always "
        "warrant attention. Cite by ID; verify before reporting."
    ),
    "security-checklist": (
        "Assessment reference: the structured security review checklist. A "
        "check it lists that this estate's evidence cannot answer is Unknown, "
        "not a pass."
    ),
    "pattern-library": (
        "Assessment reference: the catalogue of known good patterns, for "
        "judging architecture and design quality. Reference catalogue only."
    ),
}

#: Roles whose one-line purpose is unknown fall back to this rather than to "".
_UNKNOWN_PURPOSE = "Platform investigation guidance."

#: Prompt-index ordering, by role. Each rank is an argument, not a preference:
#:
#: 0 ``nexus-adaptation`` -- the only file that reconciles the upstream guides'
#:   procedural instructions (write a file, run a shell, fetch a URL) with this
#:   runtime. A model that reads a parsing guide before it reads the adaptation
#:   follows instructions that cannot work here, so nothing may precede it.
#: 1 ``confidence`` -- the grading vocabulary. Every guide below it tells the
#:   model to record a finding as Confirmed, Inferred or Unknown, and those
#:   words have precise definitions with a locus requirement attached. A guide
#:   read before the vocabulary it grades in is read with the model's own
#:   plausible-sounding definitions substituted in, which is exactly how a
#:   design-recovery inference ends up rendered as Confirmed.
#: 2 ``validation`` -- the self-check a finished document must pass. It is
#:   applied last but listed before the parsing guides because it states what
#:   "done" means, and work aimed at unstated acceptance criteria gets redone.
#: 3 everything else -- the platform guides, in manifest order.
#: 4 the assessment reference catalogues. Ranked explicitly rather than left on
#:   the default, because the default puts shared files BEFORE platform files
#:   (the method tiebreak below), which would list five reference catalogues
#:   ahead of the guides that say how to read the exports at all. They are
#:   looked up when scoring, not read to learn the estate, so they go last.
#:
#: Ties break on method (shared before platform) and then manifest position, so
#: the order is total and byte-stable: this text sits in a cached prompt prefix.
_PROMPT_ROLE_RANK: dict[str, int] = {
    "nexus-adaptation": 0,
    "confidence": 1,
    "validation": 2,
    **{role: 4 for role in SCORECARD_ROLES},
}
_DEFAULT_PROMPT_RANK = 3

#: Method-level tiebreak for roles that share a rank. Shared first: a file with
#: no declared rank is likelier to be vocabulary than a parsing guide.
_SHARED_METHOD_RANK = 0
_PLATFORM_METHOD_RANK = 1


class InvestigationMethodError(RuntimeError):
    """A method directory exists but cannot be trusted.

    Raised rather than degraded on purpose. Every other failure in the runtime
    lane degrades, because a missing runtime fact makes a document thinner. A
    method whose bytes do not match its manifest makes a document *confidently
    wrong* while still looking provenanced, so this one fails closed.
    """


class InvestigationMethodFile(BaseModel):
    """One file of a method, with the digest that proves it is the reviewed text.

    ``revision`` and ``divergence`` exist because provenance is a property of a
    FILE, not of a directory. A method directory can legitimately hold files
    with different origins -- ``_shared`` holds two verbatim upstream copies and
    five catalogues that match no upstream revision -- and a single
    directory-level ``source.revision`` covering all of them is a claim a
    reviewer running the same diff would find false. Overstated provenance is
    worse than an admitted gap: it is the thing that stops anyone checking.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    role: MethodFileRole
    sha256: str = Field(min_length=64, max_length=64)
    #: Upstream revision these exact bytes came from. Empty means "the method's
    #: own ``source.revision``"; any other value, including the literal
    #: ``unknown``, overrides it for this file alone.
    revision: str = ""
    #: How this file differs from upstream, when it does. Free text, and the
    #: only honest answer when the bytes match no upstream revision.
    divergence: str = ""

    @property
    def purpose(self) -> str:
        return _ROLE_PURPOSE.get(self.role, _UNKNOWN_PURPOSE)

    def resolved_revision(self, method_revision: str) -> str:
        """The revision claim that applies to THIS file."""
        return self.revision or method_revision


class InvestigationMethodSource(BaseModel):
    """Where the method text came from, so a reviewer can diff it upstream."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    repository: str = ""
    revision: str = ""
    #: Subdirectory within ``repository`` the files were taken from. Optional,
    #: because the first import recorded provenance at repository granularity;
    #: a reviewer diffing two directories that came from different subtrees of
    #: one repository cannot find the upstream file without it.
    path: str = ""
    adapted_on: str = ""


class InvestigationMethodUpdate(BaseModel):
    """One import or re-import of the upstream method."""

    model_config = ConfigDict(extra="forbid")

    revision: str = ""
    date: str = ""
    #: Accepts ``change:`` as well as ``note:``. One canonical field with two
    #: accepted spellings, rather than two fields: consumers read ``.note`` and
    #: cannot silently miss a log entry that happened to use the other word.
    #: ``extra="forbid"`` still rejects anything outside this pair, so a genuine
    #: typo in a manifest is still a load failure.
    note: str = Field(default="", validation_alias=AliasChoices("note", "change"))


class InvestigationMethod(BaseModel):
    """A loaded, digest-verified method for one platform, or the shared method.

    ``platform`` carries the directory key either way; for the shared method it
    is ``_shared``, which is a namespace and not a platform. Use ``is_shared``
    rather than comparing the string at a call site.
    """

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
    def is_shared(self) -> bool:
        """True for the platform-agnostic method, false for a platform's own."""
        return _platform_key(self.platform) == SHARED_METHOD_KEY

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
                    # Resolved, not raw: the staged manifest is read on its own,
                    # away from the manifest that produced it, so a blank field
                    # there reads as "same as the method" only if you still have
                    # the method to compare against.
                    "revision": item.resolved_revision(self.source.revision),
                    **({"divergence": item.divergence} if item.divergence else {}),
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


def _is_safe_key(key: str) -> bool:
    """Whether ``key`` is one path component, and so cannot escape its root.

    The same rule ``_verify_digests`` applies to a manifest's filenames, applied
    to the caller-supplied platform: the key is joined onto both the method
    library root and the run's staging directory, so a separator in it reads or
    writes outside either.
    """
    return bool(key) and "/" not in key and "\\" not in key and key not in {".", ".."}


def _is_platform_key(key: str) -> bool:
    """Whether ``key`` names a real platform rather than a method namespace.

    An underscore prefix marks a namespace -- currently only ``_shared``. Keyed
    on the prefix rather than on the one name so a future namespace cannot leak
    into platform lookup by being added to the library and forgotten here.
    """
    return _is_safe_key(key) and not key.startswith("_")


def _method_label(key: str) -> str:
    """How an error message should name the method it is complaining about."""
    if key == SHARED_METHOD_KEY:
        return "Shared investigation method"
    return f"Investigation method for platform '{key}'"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def available_platforms() -> tuple[str, ...]:
    """The platforms that have a method directory, sorted.

    Method namespaces (``_shared``) are excluded: they are not platforms and a
    caller enumerating platforms would otherwise offer ``_shared`` as one, then
    look up parsing guidance that does not exist there.

    Listing only, so it does not verify digests -- a directory appearing here is
    a claim that a method exists, not that it is loadable. ``load_*`` is where
    trust is established, and it fails closed.
    """
    root = methods_root()
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            path.name
            for path in root.iterdir()
            if path.is_dir()
            and _is_platform_key(_platform_key(path.name))
            and (path / METHOD_MANIFEST_FILENAME).is_file()
        )
    )


def load_investigation_method(platform: str) -> InvestigationMethod | None:
    """Load and verify the platform-specific method for ``platform``.

    Returns ``None`` when the platform has no method directory -- the normal
    case for Workato, Lobster and generic scans -- and for ``_shared``, which is
    a namespace rather than a platform (use ``load_shared_method``). Raises
    ``InvestigationMethodError`` when a directory exists but its contents do not
    match its manifest, because a partially-trustworthy method is not usable.

    This returning ``None`` does NOT mean the run gets no method: the shared
    confidence and validation files apply to every platform and are loaded and
    staged separately.
    """
    key = _platform_key(platform)
    if not _is_platform_key(key):
        return None
    return _load_method(key)


def load_shared_method() -> InvestigationMethod | None:
    """Load and verify the platform-agnostic method.

    Same code path, same digest verification and the same fail-closed contract
    as a platform method -- vocabulary a model grades findings against has to be
    provably the reviewed text for exactly the reason a parsing guide does.

    Returns ``None`` only when the directory is absent altogether. That is a
    deployment defect (the library ships with the repository) rather than a
    configuration choice, and it is reported as a warning instead of an
    exception because the shared files are additive: without them a run grades
    findings less consistently, whereas raising would block every documentation
    run on every platform. A directory that exists and does not verify still
    fails closed.
    """
    return _load_method(SHARED_METHOD_KEY)


def shared_method_paths_by_role() -> dict[str, Path]:
    """Verified on-disk paths of the shared method files, keyed by role.

    The lookup a consumer needs when it wants a specific method FILE rather
    than the whole staged directory -- ``_preprocess_scan_data`` bundling the
    five assessment catalogues for the scorecard is the case this exists for.
    Keyed on role, not filename, for the reason ``SCORECARD_ROLES`` states.

    Goes through ``load_shared_method`` so the caller inherits digest
    verification: these bytes reach a model as authority, and a caller reading
    the directory directly would be a third mechanism owning the method layer,
    which is the defect this consolidation removed. Returns ``{}`` when the
    library is absent; a present-but-unverifiable one still raises.
    """
    method = load_shared_method()
    if method is None or method.root is None:
        return {}
    return {item.role: method.root / item.name for item in method.files}


def _load_method(key: str) -> InvestigationMethod | None:
    """Load and digest-verify the method directory named ``key``."""
    if not _is_safe_key(key):
        return None

    root = methods_root() / key
    if not root.is_dir():
        if key == SHARED_METHOD_KEY:
            logger.warning(
                "Shared investigation method directory is missing: %s. Runs will carry no "
                "confidence vocabulary or validation checklist.",
                root,
            )
        return None

    manifest_path = root / METHOD_MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise InvestigationMethodError(
            f"{_method_label(key)} has no {METHOD_MANIFEST_FILENAME}: "
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
            f"{_method_label(_platform_key(method.platform))} failed verification "
            f"({manifest_path}): "
            + "; ".join(problems)
            + ". Recompute the sha256 values in the manifest after editing a method file; "
            "do not weaken this check."
        )


def staged_method_dir(staging_dir: Path, platform: str) -> Path:
    """Where ``materialize_investigation_method`` puts the method for ``platform``.

    Rejects anything that is not a single path component. ``Path(x) / ""`` is
    ``x``, so a blank key silently returned the ``_method`` ROOT -- which now
    holds the shared method, and which a caller would then treat as one
    platform's directory, or write into alongside it. A key containing a
    separator would land outside the staging tree entirely.
    """
    key = _platform_key(platform)
    if not _is_safe_key(key):
        raise ValueError(
            f"staged_method_dir requires a platform name that is a single path "
            f"component; got {platform!r}, which does not resolve to a method "
            f"directory under '{STAGED_METHOD_DIRNAME}'."
        )
    return Path(staging_dir) / STAGED_METHOD_DIRNAME / key


def staged_shared_method_dir(staging_dir: Path) -> Path:
    """Where the platform-agnostic method is staged."""
    return staged_method_dir(staging_dir, SHARED_METHOD_KEY)


def materialize_investigation_method(staging_dir: Path, platform: str) -> Path | None:
    """Stage the method a run of ``platform`` should read.

    Always stages the shared method, and additionally the platform's own guides
    when it has any. A run on a platform with no directory of its own therefore
    still receives the confidence vocabulary and the validation checklist --
    those files describe no artifact format, so there is no platform for which
    they are the wrong method.

    Returns the staged PLATFORM directory, or ``None`` when the platform has no
    guides of its own. ``None`` means "no platform-specific parsing guidance",
    NOT "nothing was staged"; callers that need every staged path should use
    ``staged_method_dirs``.

    Idempotent: a rerun over byte-identical output writes nothing, so a resumed
    run does not churn the staging tree or invalidate a provider prefix cache
    that includes these files.
    """
    materialize_shared_method(staging_dir)

    method = load_investigation_method(platform)
    if method is None:
        logger.debug(
            "No platform-specific investigation method for %r; shared method staged only",
            platform,
        )
        return None
    return _materialize(staging_dir, method)


def materialize_shared_method(staging_dir: Path) -> Path | None:
    """Stage the platform-agnostic method into the run's staging directory.

    Returns ``None`` only when the shared method is absent from the library;
    a present-but-unverifiable one raises, as it does for a platform.
    """
    method = load_shared_method()
    if method is None:
        return None
    return _materialize(staging_dir, method)


def staged_method_dirs(staging_dir: Path, platform: str) -> tuple[Path, ...]:
    """Every method directory staged for this run, shared first.

    Filtered to what is actually on disk, so a caller listing the workspace
    cannot point a model at a directory ``materialize_*`` chose not to create.
    """
    candidates = [staged_shared_method_dir(staging_dir)]
    # A blank or non-component platform is "no platform", not a directory to look
    # for. Asking for its staged path is an error, and this is a listing helper.
    if _is_safe_key(_platform_key(platform)):
        candidates.append(staged_method_dir(staging_dir, platform))

    seen: list[Path] = []
    for path in candidates:
        if path.is_dir() and path not in seen:
            seen.append(path)
    return tuple(seen)


def _materialize(staging_dir: Path, method: InvestigationMethod) -> Path:
    """Copy one verified method into staging, with a manifest beside it."""
    root = method.root
    if root is None:  # defensive: the loader always sets it
        raise InvestigationMethodError(
            f"{_method_label(_platform_key(method.platform))} has no resolved root"
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


def _method_credit(method: InvestigationMethod) -> str:
    """Version and upstream provenance, for a prompt header."""
    return (
        f"{method.method_version}, adapted from {method.source.name or 'upstream'} "
        f"{method.source.revision[:12] or 'unknown revision'}"
    )


def method_prompt_index(staging_dir: Path, platform: str) -> str:
    """An ASCII bullet list of the STAGED method files, for an agent prompt.

    Covers the shared method and the platform's own, in one list ordered by
    ``_PROMPT_ROLE_RANK``. One list rather than two sections because the order
    is the point: the ranking says which file has to be read before which, and
    splitting it by origin directory would put that argument back in the
    reader's hands.

    Built from what is actually on disk, not from the manifest alone: a prompt
    that points a model at a file the workspace does not contain spends a tool
    call to discover the platform lied to it. Returns "" when nothing is staged.
    """
    staged: list[tuple[int, InvestigationMethod]] = []
    shared = load_shared_method()
    if shared is not None:
        staged.append((_SHARED_METHOD_RANK, shared))
    platform_method = load_investigation_method(platform)
    if platform_method is not None:
        staged.append((_PLATFORM_METHOD_RANK, platform_method))

    rows: list[tuple[int, int, int, str]] = []
    rendered: dict[int, InvestigationMethod] = {}
    for method_rank, method in staged:
        target = staged_method_dir(staging_dir, method.platform)
        if not target.is_dir():
            continue
        relative_root = f"{STAGED_METHOD_DIRNAME}/{_platform_key(method.platform)}"
        for position, item in enumerate(method.files):
            if not (target / item.name).is_file():
                continue
            rows.append(
                (
                    _PROMPT_ROLE_RANK.get(item.role, _DEFAULT_PROMPT_RANK),
                    method_rank,
                    position,
                    f"- {relative_root}/{item.name} [{item.role}] -- {item.purpose}",
                )
            )
            rendered[method_rank] = method

    if not rows:
        return ""
    lines = [row[3] for row in sorted(rows)]

    staged_platform = rendered.get(_PLATFORM_METHOD_RANK)
    staged_shared = rendered.get(_SHARED_METHOD_RANK)

    header: list[str] = []
    if staged_platform is not None:
        header.append(
            f"{staged_platform.platform.upper()} INVESTIGATION METHOD "
            f"({_method_credit(staged_platform)})"
        )
        if staged_shared is not None:
            header.append(f"WITH SHARED METHOD ({_method_credit(staged_shared)})")
    else:
        # Say the platform guide is absent rather than leaving it to be
        # discovered. A model that expects one and cannot find it reports the
        # workspace as incomplete, or goes looking for it at the cost of a tool
        # call; there is nothing to find, and the files below are the method.
        header.append(f"SHARED INVESTIGATION METHOD ({_method_credit(staged_shared)})")
        header.append(
            f"No platform-specific method exists for '{_platform_key(platform) or 'unknown'}'; "
            f"the files below are platform-agnostic and are the whole method for this run."
        )

    describes = (
        "how to read this platform's exports, how to grade what you find, and how "
        "to check a document before calling it done"
        if staged_platform is not None
        else "how to grade what you find, and how to check a document before calling it done"
    )
    body = [
        *header,
        "",
        (
            f"These files are method, not evidence. They describe {describes}; "
            f"they say nothing about this project."
        ),
        "",
        "Read them in the order listed.",
        "",
        *lines,
    ]
    text = "\n".join(body)
    # The whole corpus this renders from is ASCII, so a non-ASCII byte here means
    # an upstream file changed shape. Say so rather than emitting it: generated
    # documents target Windows cp1252.
    return text.encode("ascii", errors="replace").decode("ascii")
