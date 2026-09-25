"""Every path inside an agent workspace, owned by exactly one module.

Repository pattern. Nothing outside this module may build a workspace path by
string concatenation; call sites ask for a typed accessor.

Why this is a module and not a convention: the failure this lane replaces
happened because three modules (``services/claude_service.py``,
``providers/claude_cli.py``, and the since-deleted ``scripts/bundle_analysis.py``)
each carried their own idea of the same layout and of the section contract
inside it -- the bundler was removed precisely because it parsed model prose for
section headings, which is the seam that can fail silently. A producer
that wrote ``## COMPONENTS`` and a consumer that read ``### COMPONENTS`` were
free to disagree for months because no single object could be asked "what is
the shape here". One owner makes that disagreement a compile-time impossibility
rather than a code-review hope.

Two invariants this module encodes structurally:

* **The evidence lane is addressable in full.** ``scan/`` is a byte-exact
  mirror of the scanner's approved artifact tree plus the monolithic
  ``scan_result.json``. ``indexes/`` exists to help an agent *find* an
  artifact; it never gates which artifact may be opened.
* **Deterministic and agent-writable lanes are separate.** A rebuild replaces
  :data:`DETERMINISTIC_DIRS` wholesale and never touches
  :data:`AGENT_WRITABLE_DIRS`, so the workspace can persist per project across
  runs (plan decision 3) without a rebuild silently eating the agent's memory.

Path safety is centralised here too: :meth:`WorkspaceLayout.path` is the only
join, and it refuses to leave the workspace root. A project id or a scanner
relative path is untrusted input as far as the filesystem is concerned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

#: Bumped when the staged tree changes shape in a way a consumer must notice.
WORKSPACE_SCHEMA_VERSION = 1

#: Default directory name under a data root that holds one subtree per project.
#: Slice 2 decides the data root; the *name* stays owned here with every other
#: path fragment.
WORKSPACES_DIRNAME = "workspaces"

# ── Top-level directories (plan "Design", workspace layout block) ────────────

KNOWLEDGE_DIR = PurePosixPath("knowledge")
SKILLS_DIR = PurePosixPath("skills")
RULES_DIR = PurePosixPath("rules")
POLICIES_DIR = PurePosixPath("policies")
TEMPLATES_DIR = PurePosixPath("templates")
PACKS_DIR = PurePosixPath("packs")
FACTS_DIR = PurePosixPath("facts")
SCAN_DIR = PurePosixPath("scan")
INDEXES_DIR = PurePosixPath("indexes")
EVIDENCE_DIR = PurePosixPath("evidence")
NOTES_DIR = PurePosixPath("notes")
FINDINGS_DIR = PurePosixPath("findings")
INVESTIGATIONS_DIR = PurePosixPath("investigations")
AGENT_STATE_DIR = PurePosixPath("agent-state")
OUTPUT_DIR = PurePosixPath("output")

# ── Second-level directories ────────────────────────────────────────────────

FACTS_MAPPINGS_DIR = FACTS_DIR / "mappings"
SCAN_JOBS_DIR = SCAN_DIR / "jobs"
EVIDENCE_CODE_DIR = EVIDENCE_DIR / "code"
EVIDENCE_RUNTIME_DIR = EVIDENCE_DIR / "runtime"
EVIDENCE_EXTERNAL_DIR = EVIDENCE_DIR / "external"
INVESTIGATIONS_ACTIVE_DIR = INVESTIGATIONS_DIR / "active"
INVESTIGATIONS_COMPLETED_DIR = INVESTIGATIONS_DIR / "completed"

# ── Files ───────────────────────────────────────────────────────────────────

PROJECT_DOC = PurePosixPath("PROJECT.md")
AGENT_GUIDE_DOC = PurePosixPath("AGENT_GUIDE.md")
BUILD_RECEIPT = PurePosixPath("BUILD_RECEIPT.json")

FACTS_UNITS = FACTS_DIR / "units.json"
FACTS_DEPENDENCIES = FACTS_DIR / "dependencies.json"
FACTS_INTERFACES = FACTS_DIR / "interfaces.json"
FACTS_CONFIGURATION = FACTS_DIR / "configuration.json"
FACTS_SCAN_COVERAGE = FACTS_DIR / "scan_coverage.json"
FACTS_COVERAGE_LEDGER = FACTS_DIR / "coverage_ledger.md"
FACTS_LIMITATIONS = FACTS_DIR / "limitations.md"
FACTS_MAPPINGS_INDEX = FACTS_MAPPINGS_DIR / "index.md"

INDEX_ESTATE = INDEXES_DIR / "estate.md"
INDEX_SYSTEMS = INDEXES_DIR / "systems.md"
INDEX_DEPENDENCIES = INDEXES_DIR / "dependencies.md"
INDEX_UNRESOLVED = INDEXES_DIR / "unresolved.md"

EVIDENCE_README = EVIDENCE_DIR / "README.md"

#: The monolithic scan inside the workspace. It is the artifact of record for
#: anything the sharded tree reduces (verified on the ETTM estate: the scanner's
#: ``scan/routines.json`` carries non-system routines only -- 19 of 37).
SCAN_RESULT_BLOB = SCAN_DIR / "scan_result.json"
#: The scanner's own shard index, mirrored. ``manifest.job_files`` maps a design
#: key to its shard path; the workspace consumes that map rather than
#: re-deriving shard filenames, because a second copy of a naming rule drifts.
SCAN_MANIFEST = SCAN_DIR / "manifest.json"

# ── Lane classification ─────────────────────────────────────────────────────

#: Rebuilt wholesale on every build. Deterministic, never agent-written.
DETERMINISTIC_DIRS: tuple[PurePosixPath, ...] = (SCAN_DIR, FACTS_DIR, INDEXES_DIR)

#: Never touched by a rebuild: this is where the workspace's memory lives.
AGENT_WRITABLE_DIRS: tuple[PurePosixPath, ...] = (
    KNOWLEDGE_DIR,
    NOTES_DIR,
    FINDINGS_DIR,
    INVESTIGATIONS_DIR,
    AGENT_STATE_DIR,
    OUTPUT_DIR,
)

#: Method text. Slice 1 creates the directories; Slice 2 fills them.
METHOD_DIRS: tuple[PurePosixPath, ...] = (
    SKILLS_DIR,
    RULES_DIR,
    POLICIES_DIR,
    TEMPLATES_DIR,
    PACKS_DIR,
)

#: Lanes whose every file must carry a provenance header naming its artifact of
#: record. See ``builder.verify_no_silent_truncation``.
DERIVED_DIRS: tuple[PurePosixPath, ...] = (FACTS_DIR, INDEXES_DIR)

#: Every directory the builder creates, in a stable order.
ALL_DIRS: tuple[PurePosixPath, ...] = (
    KNOWLEDGE_DIR,
    SKILLS_DIR,
    RULES_DIR,
    POLICIES_DIR,
    TEMPLATES_DIR,
    PACKS_DIR,
    FACTS_DIR,
    FACTS_MAPPINGS_DIR,
    SCAN_DIR,
    SCAN_JOBS_DIR,
    INDEXES_DIR,
    EVIDENCE_DIR,
    EVIDENCE_CODE_DIR,
    EVIDENCE_RUNTIME_DIR,
    EVIDENCE_EXTERNAL_DIR,
    NOTES_DIR,
    FINDINGS_DIR,
    INVESTIGATIONS_DIR,
    INVESTIGATIONS_ACTIVE_DIR,
    INVESTIGATIONS_COMPLETED_DIR,
    AGENT_STATE_DIR,
    OUTPUT_DIR,
)

_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


class WorkspacePathError(ValueError):
    """A path would leave the workspace, or a segment is unusable."""


def safe_segment(value: str, fallback: str = "unnamed") -> str:
    """A single filesystem segment derived from untrusted text.

    Collapses anything outside ``[A-Za-z0-9._-]`` and refuses the two segments
    that are navigation rather than names.
    """
    cleaned = _SAFE_SEGMENT.sub("_", (value or "").strip()).strip("._-")
    if not cleaned or cleaned in {".", ".."}:
        return fallback
    return cleaned[:120]


def workspaces_dir(data_dir: Path) -> Path:
    """The parent directory holding one workspace subtree per project."""
    return Path(data_dir) / WORKSPACES_DIRNAME


@dataclass(frozen=True)
class WorkspaceLayout:
    """Typed accessors for every path in one project's workspace."""

    root: Path
    project_id: str

    @classmethod
    def for_project(cls, workspaces_dir_path: Path, project_id: str) -> WorkspaceLayout:
        segment = safe_segment(project_id, fallback="")
        if not segment:
            raise WorkspacePathError(f"project_id is not usable as a directory name: {project_id!r}")
        return cls(root=Path(workspaces_dir_path) / segment, project_id=project_id)

    # ── the single join ────────────────────────────────────────────────────
    def path(self, relative: str | PurePosixPath | Path) -> Path:
        """Absolute path for a workspace-relative location, containment checked."""
        text = PurePosixPath(str(relative).replace("\\", "/"))
        if text.is_absolute():
            raise WorkspacePathError(f"workspace paths are relative: {relative!r}")
        if any(part == ".." for part in text.parts):
            raise WorkspacePathError(f"workspace path escapes the root: {relative!r}")
        return self.root.joinpath(*text.parts)

    def relative(self, path: Path) -> str:
        """Workspace-relative POSIX form of an absolute path inside the root."""
        try:
            return Path(path).relative_to(self.root).as_posix()
        except ValueError as exc:  # pragma: no cover - programming error
            raise WorkspacePathError(f"{path} is not inside {self.root}") from exc

    def contains(self, path: Path) -> bool:
        try:
            Path(path).relative_to(self.root)
        except ValueError:
            return False
        return True

    # ── directories ────────────────────────────────────────────────────────
    @property
    def knowledge(self) -> Path:
        return self.path(KNOWLEDGE_DIR)

    @property
    def skills(self) -> Path:
        return self.path(SKILLS_DIR)

    @property
    def rules(self) -> Path:
        return self.path(RULES_DIR)

    @property
    def policies(self) -> Path:
        return self.path(POLICIES_DIR)

    @property
    def templates(self) -> Path:
        return self.path(TEMPLATES_DIR)

    @property
    def packs(self) -> Path:
        return self.path(PACKS_DIR)

    @property
    def facts(self) -> Path:
        return self.path(FACTS_DIR)

    @property
    def facts_mappings(self) -> Path:
        return self.path(FACTS_MAPPINGS_DIR)

    @property
    def scan(self) -> Path:
        return self.path(SCAN_DIR)

    @property
    def scan_jobs(self) -> Path:
        return self.path(SCAN_JOBS_DIR)

    @property
    def indexes(self) -> Path:
        return self.path(INDEXES_DIR)

    @property
    def evidence(self) -> Path:
        return self.path(EVIDENCE_DIR)

    @property
    def evidence_code(self) -> Path:
        return self.path(EVIDENCE_CODE_DIR)

    @property
    def evidence_runtime(self) -> Path:
        return self.path(EVIDENCE_RUNTIME_DIR)

    @property
    def evidence_external(self) -> Path:
        return self.path(EVIDENCE_EXTERNAL_DIR)

    @property
    def notes(self) -> Path:
        return self.path(NOTES_DIR)

    @property
    def findings(self) -> Path:
        return self.path(FINDINGS_DIR)

    @property
    def investigations(self) -> Path:
        return self.path(INVESTIGATIONS_DIR)

    @property
    def investigations_active(self) -> Path:
        return self.path(INVESTIGATIONS_ACTIVE_DIR)

    @property
    def investigations_completed(self) -> Path:
        return self.path(INVESTIGATIONS_COMPLETED_DIR)

    @property
    def agent_state(self) -> Path:
        return self.path(AGENT_STATE_DIR)

    @property
    def output(self) -> Path:
        return self.path(OUTPUT_DIR)

    # ── files ──────────────────────────────────────────────────────────────
    @property
    def project_doc(self) -> Path:
        return self.path(PROJECT_DOC)

    @property
    def agent_guide(self) -> Path:
        return self.path(AGENT_GUIDE_DOC)

    @property
    def build_receipt(self) -> Path:
        return self.path(BUILD_RECEIPT)

    @property
    def facts_units(self) -> Path:
        return self.path(FACTS_UNITS)

    @property
    def facts_dependencies(self) -> Path:
        return self.path(FACTS_DEPENDENCIES)

    @property
    def facts_interfaces(self) -> Path:
        return self.path(FACTS_INTERFACES)

    @property
    def facts_configuration(self) -> Path:
        return self.path(FACTS_CONFIGURATION)

    @property
    def facts_scan_coverage(self) -> Path:
        return self.path(FACTS_SCAN_COVERAGE)

    @property
    def facts_coverage_ledger(self) -> Path:
        return self.path(FACTS_COVERAGE_LEDGER)

    @property
    def facts_limitations(self) -> Path:
        return self.path(FACTS_LIMITATIONS)

    @property
    def facts_mappings_index(self) -> Path:
        return self.path(FACTS_MAPPINGS_INDEX)

    @property
    def index_estate(self) -> Path:
        return self.path(INDEX_ESTATE)

    @property
    def index_systems(self) -> Path:
        return self.path(INDEX_SYSTEMS)

    @property
    def index_dependencies(self) -> Path:
        return self.path(INDEX_DEPENDENCIES)

    @property
    def index_unresolved(self) -> Path:
        return self.path(INDEX_UNRESOLVED)

    @property
    def evidence_readme(self) -> Path:
        return self.path(EVIDENCE_README)

    @property
    def scan_blob(self) -> Path:
        return self.path(SCAN_RESULT_BLOB)

    @property
    def scan_manifest(self) -> Path:
        return self.path(SCAN_MANIFEST)

    # ── parameterised files ────────────────────────────────────────────────
    def scan_shard(self, relative_to_scan_dir: str) -> Path:
        """Absolute path of a mirrored scanner artifact.

        ``relative_to_scan_dir`` is the scanner's own relative path, either
        ``scan/jobs/<group>/<design>.json`` (as the approval receipt states it)
        or ``jobs/<group>/<design>.json`` (as ``manifest.job_files`` states it).
        Both forms resolve to the same file so no caller has to normalise.
        """
        text = str(relative_to_scan_dir).replace("\\", "/").lstrip("/")
        text = text.removeprefix("scan/")
        return self.path(SCAN_DIR / text)

    def scan_shard_relative(self, relative_to_scan_dir: str) -> str:
        """Workspace-relative form of :meth:`scan_shard`."""
        return self.relative(self.scan_shard(relative_to_scan_dir))

    def mapping_table(self, table_id: str) -> Path:
        return self.path(FACTS_MAPPINGS_DIR / f"{safe_segment(table_id, 'table')}.json")

    def all_directories(self) -> tuple[Path, ...]:
        return tuple(self.path(name) for name in ALL_DIRS)

    def deterministic_directories(self) -> tuple[Path, ...]:
        return tuple(self.path(name) for name in DETERMINISTIC_DIRS)

    def agent_writable_directories(self) -> tuple[Path, ...]:
        return tuple(self.path(name) for name in AGENT_WRITABLE_DIRS)

    def derived_directories(self) -> tuple[Path, ...]:
        return tuple(self.path(name) for name in DERIVED_DIRS)
