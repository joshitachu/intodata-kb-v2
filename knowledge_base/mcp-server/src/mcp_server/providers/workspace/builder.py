"""Deterministically stage a per-project agent workspace from an intodoc scan.

No LLM, no network, no clock. Given the same scan output directory this module
produces byte-identical bytes, which is what makes the workspace auditable.

The governing rule (plan decision 4)
------------------------------------
Deterministic code may **index, address, hash, redact and verify**. It may
never **pre-select and discard**. A derived view may exist only alongside the
original, must name the artifact of record, and the original must stay
addressable.

That is not a slogan here, it is enforced: every file under ``facts/`` and
``indexes/`` carries a machine-readable provenance header, and
:func:`verify_no_silent_truncation` runs at the end of every build and refuses
to return a workspace where a derived artifact cannot account for what it
dropped. The failure this replaces was a bundler that kept 25 KB of a 520 KB
analysis and said nothing; in this layout that artifact could not be written.

Why ``scan/`` is staged SHARDED (plan open question 2)
------------------------------------------------------
Sharded, and the monolith as well.

* The scanner already writes both representations, and they were measured to
  agree: on the ETTM estate all 142 ``scan/jobs/<group>/<design>.json`` shards
  are element-for-element identical to the corresponding entries of
  ``scan_result.json``. Sharding is therefore lossless for designs, and it is
  the scanner's own stated expectation -- its approval receipt hashes the shard
  tree precisely because "the four aggregates are not the bytes a documentation
  workspace stages".
* Sharded means an agent can open one design for 30 KB instead of paying 22 MB
  to answer a question about one job. A tool-mediated read of the monolith
  would put a Python pre-selector back between the evidence and the agent,
  which is the architecture that failed.
* The monolith is staged **too**, because sharding is not lossless for
  everything: ``scan/routines.json`` holds non-system routines only (19 of 37
  on ETTM) and ``schema_definitions`` has no shard at all. Dropping the blob
  would make the shards the only copy of a reduced set -- exactly the discard
  the invariant forbids. ``facts/scan_coverage.json`` records the measured
  per-key comparison rather than asserting coverage.

Fail-closed inputs
------------------
Staging requires the scanner's ``publication_approval.json``: ``approved`` must
be true, every final verification must have passed, and the approval tree's own
digest must recompute. Every staged file is verified against the digest in that
receipt, and the membership rule (roots plus excluded prefixes) is read FROM the
receipt rather than re-stated here -- the scanner's writer explicitly warns that
a second copy of the rule drifts. ``scan/sanitized/`` and ``scan/evidence/`` are
excluded by that rule and are never staged: the scanner's own comment is that
neither "may ever reach a documentation agent".
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from mcp_server.providers.workspace.layout import (
    DERIVED_DIRS,
    WORKSPACE_SCHEMA_VERSION,
    WorkspaceLayout,
)

#: Bumped when generated content changes; recorded in the build receipt so a
#: stale workspace is identifiable without guessing.
BUILDER_VERSION = 1

APPROVAL_FILENAME = "publication_approval.json"
SCAN_RESULT_FILENAME = "scan_result.json"

#: The one approval-tree digest algorithm this builder knows how to recompute.
#: A receipt announcing anything else is refused rather than trusted.
APPROVAL_TREE_ALGORITHM = "sha256-relpath-pairs-1"

_PROVENANCE_MARKER = "nexus-workspace-provenance"
_PROVENANCE_JSON_KEY = "_provenance"

MIRROR = "mirror"
INDEX = "index"
PROJECTION = "projection"
GUIDE = "guide"
_DERIVED_KINDS = {INDEX, PROJECTION}

#: ``scan_result.json`` top-level key -> the shard that mirrors it, relative to
#: the scanner's ``scan/`` directory. Declared because it is the scanner's
#: documented layout (``intodoc/src/intodoc/cli.py::_save_scan_result``); the
#: build MEASURES whether each claim holds and records the answer.
_KEY_TO_SHARD: tuple[tuple[str, str], ...] = (
    ("capability_ledger", "capability_ledger.json"),
    ("context_groups", "contexts.json"),
    ("files", "files_index.json"),
    ("manifest", "manifest.json"),
    ("mapping_accounting", "mapping_accounting.json"),
    ("metadata_connections", "connections.json"),
    ("routines", "routines.json"),
    ("services", "services.json"),
    ("structures", "structures.json"),
    ("tdm_maps", "tdm_maps.json"),
    ("xslt_transforms", "xslt.json"),
)

#: Keys the scanner folds into ``scan/meta.json``.
_META_KEYS: tuple[str, ...] = (
    "platform",
    "repo_root",
    "scanned_at",
    "source_inventory",
    "stats",
    "warnings",
)

_JOBS_KEY = "jobs"


class WorkspaceBuildError(RuntimeError):
    """The workspace cannot be staged, or what was staged cannot be trusted."""


class ScanApprovalError(WorkspaceBuildError):
    """The scan is not approved for publication to an agent."""


# ── primitives ──────────────────────────────────────────────────────────────


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _canonical_digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _element_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (list, tuple, dict, set)):
        return len(value)
    return 1


def ascii_text(value: Any) -> str:
    """ASCII form of arbitrary text (house rule 4: ASCII-only Markdown).

    Non-ASCII is escaped with the standard Python backslash form (``\\xb7``,
    ``\\u4e2d``), never replaced with ``?``. An escape is reversible and keeps
    the codepoint recoverable; a ``?`` silently destroys the one thing an index
    exists to preserve -- the ability to identify the artifact again.
    """
    return str(value if value is not None else "").encode("ascii", "backslashreplace").decode("ascii")


def _cell(value: Any) -> str:
    return ascii_text(value).replace("|", "\\|").replace("\n", " ").strip()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


# ── provenance ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Provenance:
    """What a generated artifact was derived from, and what it kept.

    ``derivation_kind``:

    * ``mirror``     -- byte-identical copy of the artifact of record.
    * ``index``      -- navigation only. Carries no evidence of its own, so it
      can never be the only copy of anything.
    * ``projection`` -- carries content forward. Must account for elements:
      ``retained_elements < source_elements`` is legal only when
      ``dropped_elements_addressable_at`` names a staged artifact that still
      holds the complete set.
    * ``guide``      -- authored workspace documentation, not derived evidence.
    """

    derivation_kind: str
    artifact_of_record: tuple[str, ...] = ()
    source_elements: int | None = None
    retained_elements: int | None = None
    dropped_elements_addressable_at: tuple[str, ...] = ()
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["artifact_of_record"] = list(self.artifact_of_record)
        payload["dropped_elements_addressable_at"] = list(self.dropped_elements_addressable_at)
        return payload

    @classmethod
    def from_dict(cls, payload: Any) -> Provenance | None:
        if not isinstance(payload, dict) or not payload.get("derivation_kind"):
            return None
        return cls(
            derivation_kind=str(payload.get("derivation_kind") or ""),
            artifact_of_record=tuple(str(x) for x in payload.get("artifact_of_record") or ()),
            source_elements=payload.get("source_elements"),
            retained_elements=payload.get("retained_elements"),
            dropped_elements_addressable_at=tuple(
                str(x) for x in payload.get("dropped_elements_addressable_at") or ()
            ),
            note=str(payload.get("note") or ""),
        )

    def header_lines(self) -> list[str]:
        """Human/LLM visible statement of the same facts as the comment block."""
        roots = ", ".join(f"`{ascii_text(x)}`" for x in self.artifact_of_record) or "(none)"
        lines = [
            f"> DERIVED VIEW ({ascii_text(self.derivation_kind)}). "
            f"Artifact of record: {roots}.",
            "> The artifact of record is staged in full and stays addressable; this file never",
            "> replaces it. Generated deterministically -- do not edit.",
        ]
        if self.source_elements is not None:
            lines.append(
                f"> Elements: {self.retained_elements} of {self.source_elements} retained."
            )
        if self.dropped_elements_addressable_at:
            where = ", ".join(f"`{ascii_text(x)}`" for x in self.dropped_elements_addressable_at)
            lines.append(f"> Dropped elements remain addressable at: {where}.")
        if self.note:
            lines.append(f"> {ascii_text(self.note)}")
        return lines

    def comment(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=True)
        return f"<!-- {_PROVENANCE_MARKER}: {payload} -->"


def read_provenance(path: Path) -> Provenance | None:
    """Provenance of a staged artifact, or ``None`` when it declares none."""
    if path.suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if isinstance(payload, dict):
            return Provenance.from_dict(payload.get(_PROVENANCE_JSON_KEY))
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            for _ in range(5):
                line = handle.readline()
                if not line:
                    break
                marker = f"<!-- {_PROVENANCE_MARKER}: "
                if line.startswith(marker):
                    raw = line[len(marker):].rsplit("-->", 1)[0].strip()
                    try:
                        return Provenance.from_dict(json.loads(raw))
                    except json.JSONDecodeError:
                        return None
    except OSError:
        return None
    return None


def write_markdown(path: Path, provenance: Provenance, body_lines: list[str]) -> None:
    """Write an ASCII markdown artifact with its provenance stated twice.

    Once machine-readable (the comment) and once in plain sight (the header
    block). A reader that only sees the rendered document still learns that it
    is looking at a derived view and where the evidence of record lives.
    """
    lines = [provenance.comment(), ""] + provenance.header_lines() + [""] + list(body_lines)
    text = "\n".join(lines).rstrip("\n") + "\n"
    if not text.isascii():
        raise WorkspaceBuildError(f"generated markdown is not ASCII: {path}")
    _atomic_write(path, text)


def write_json_artifact(path: Path, provenance: Provenance, payload: dict[str, Any]) -> None:
    body = {_PROVENANCE_JSON_KEY: provenance.to_dict()}
    body.update(payload)
    _atomic_write(path, json.dumps(body, sort_keys=True, indent=2, ensure_ascii=True) + "\n")


# ── approval ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScanApproval:
    """The scanner's publication receipt, verified."""

    schema_version: int
    artifacts: dict[str, str]
    tree_files: dict[str, str]
    tree_digest: str
    roots: tuple[str, ...]
    excluded_prefixes: tuple[str, ...]
    allow_redaction_failures: bool


def _approval_tree_digest(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(files):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(files[relative].encode("utf-8"))
        digest.update(b"\n")
    return f"sha256:{digest.hexdigest()}"


def load_scan_approval(scan_output_dir: Path) -> ScanApproval:
    """Read and verify ``publication_approval.json``; fail closed on any doubt.

    Absence is not "unredacted maybe"; it means no one authorised this scan to
    reach an agent, and an unauthorised scan is not staged. Mirrors
    ``enterprise/workspace.py::stage_sanitized_workspace``, which refuses a scan
    with no sanitized manifest or a manifest reporting residual findings.
    """
    path = Path(scan_output_dir) / APPROVAL_FILENAME
    if not path.is_file():
        raise ScanApprovalError(
            f"{path} is missing: this scan carries no publication approval. "
            "Rescan with the current intodoc before building a workspace."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScanApprovalError(f"{path} is unreadable or malformed: {exc}") from exc
    if not isinstance(payload, dict):
        raise ScanApprovalError(f"{path} is malformed: expected an object")
    if payload.get("approved") is not True:
        raise ScanApprovalError(
            f"{path} does not approve publication (approved={payload.get('approved')!r}); "
            "refusing to stage unapproved scan evidence."
        )
    verifications = payload.get("verifications")
    if not isinstance(verifications, dict) or not verifications:
        raise ScanApprovalError(f"{path} records no redaction verifications")
    failed = sorted(
        name for name, result in verifications.items()
        if not isinstance(result, dict) or result.get("passed") is not True
    )
    if failed:
        raise ScanApprovalError(
            f"{path} is approved but reports failed verifications for: {', '.join(failed)}"
        )
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict) or SCAN_RESULT_FILENAME not in artifacts:
        raise ScanApprovalError(f"{path} does not bind a digest for {SCAN_RESULT_FILENAME}")
    tree = payload.get("tree")
    if not isinstance(tree, dict):
        raise ScanApprovalError(f"{path} carries no agent-eligible artifact tree")
    if str(tree.get("algorithm") or "") != APPROVAL_TREE_ALGORITHM:
        raise ScanApprovalError(
            f"{path} tree algorithm {tree.get('algorithm')!r} is not {APPROVAL_TREE_ALGORITHM!r}"
        )
    files = tree.get("files")
    if not isinstance(files, dict) or not files:
        raise ScanApprovalError(f"{path} tree lists no files")
    tree_files = {str(k): str(v) for k, v in files.items()}
    recomputed = _approval_tree_digest(tree_files)
    if recomputed != str(tree.get("digest") or ""):
        raise ScanApprovalError(
            f"{path} tree digest does not recompute "
            f"(receipt {tree.get('digest')!r}, recomputed {recomputed!r})"
        )
    declared_count = tree.get("file_count")
    if isinstance(declared_count, int) and declared_count != len(tree_files):
        raise ScanApprovalError(
            f"{path} tree declares {declared_count} files but lists {len(tree_files)}"
        )
    return ScanApproval(
        schema_version=int(payload.get("schema_version") or 0),
        artifacts={str(k): str(v) for k, v in artifacts.items()},
        tree_files=dict(sorted(tree_files.items())),
        tree_digest=recomputed,
        roots=tuple(str(x) for x in tree.get("roots") or ()),
        excluded_prefixes=tuple(str(x) for x in tree.get("excluded_prefixes") or ()),
        allow_redaction_failures=bool(payload.get("allow_redaction_failures")),
    )


# ── staging the scan mirror ─────────────────────────────────────────────────


@dataclass
class StagedScan:
    files: dict[str, str] = field(default_factory=dict)
    byte_count: int = 0
    blob_digest: str = ""
    blob_bytes: int = 0
    unapproved_present: list[str] = field(default_factory=list)


def _stage_scan_mirror(
    layout: WorkspaceLayout, scan_output_dir: Path, approval: ScanApproval
) -> StagedScan:
    scan_output_dir = Path(scan_output_dir)
    resolved_root = scan_output_dir.resolve()
    staged = StagedScan()

    if f"scan/{SCAN_RESULT_FILENAME}" in approval.tree_files:
        raise WorkspaceBuildError(
            "the approval tree already contains scan/scan_result.json; the workspace would "
            "stage two different files to one path"
        )

    target = layout.scan
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    for relative, expected in approval.tree_files.items():
        if not relative.startswith("scan/"):
            raise WorkspaceBuildError(
                f"approval tree entry outside the scan root is not stageable: {relative}"
            )
        source = scan_output_dir / relative
        if source.is_symlink() or not source.is_file():
            raise WorkspaceBuildError(f"approved artifact is missing or not a regular file: {relative}")
        if not source.resolve().is_relative_to(resolved_root):
            raise WorkspaceBuildError(f"approved artifact escapes the scan directory: {relative}")
        actual = _sha256_file(source)
        if actual != expected:
            raise WorkspaceBuildError(
                f"approved artifact has changed since approval: {relative} "
                f"(receipt {expected}, on disk {actual})"
            )
        destination = layout.scan_shard(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        staged.files[layout.relative(destination)] = actual
        staged.byte_count += destination.stat().st_size

    blob_source = scan_output_dir / SCAN_RESULT_FILENAME
    if blob_source.is_symlink() or not blob_source.is_file():
        raise WorkspaceBuildError(f"{SCAN_RESULT_FILENAME} is missing from {scan_output_dir}")
    blob_digest = _sha256_file(blob_source)
    if blob_digest != approval.artifacts[SCAN_RESULT_FILENAME]:
        raise WorkspaceBuildError(
            f"{SCAN_RESULT_FILENAME} has changed since approval "
            f"(receipt {approval.artifacts[SCAN_RESULT_FILENAME]}, on disk {blob_digest})"
        )
    shutil.copyfile(blob_source, layout.scan_blob)
    staged.blob_digest = blob_digest
    staged.blob_bytes = layout.scan_blob.stat().st_size
    staged.files[layout.relative(layout.scan_blob)] = blob_digest
    staged.byte_count += staged.blob_bytes

    # Anything under the approved roots that the receipt does not cover is
    # reported and NOT staged. Failing the build would make an editor's stray
    # .DS_Store fatal; staging it would let a post-approval file pose as
    # approved evidence. Reporting it does neither.
    approved_relative = set(approval.tree_files)
    for root in approval.roots or ("scan",):
        base = scan_output_dir / root
        if not base.is_dir():
            continue
        for candidate in sorted(base.rglob("*")):
            if candidate.is_symlink() or not candidate.is_file():
                continue
            relative = candidate.relative_to(scan_output_dir).as_posix()
            if any(relative.startswith(prefix) for prefix in approval.excluded_prefixes):
                continue
            if relative not in approved_relative:
                staged.unapproved_present.append(relative)
    staged.unapproved_present.sort()
    return staged


# ── coverage accounting ─────────────────────────────────────────────────────


def _load_staged_json(layout: WorkspaceLayout, relative: str) -> Any:
    path = layout.scan_shard(relative)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceBuildError(f"staged scan artifact is unreadable: {relative}: {exc}") from exc


def _shard_coverage(
    layout: WorkspaceLayout, blob: dict[str, Any], shards: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Measured per-key comparison of the monolith against the sharded mirror.

    This is the artifact that makes "nothing was silently dropped" checkable
    instead of assertable. It is measured on the STAGED bytes, so it describes
    the workspace rather than the builder's intentions.
    """
    blob_relative = layout.relative(layout.scan_blob)
    entries: list[dict[str, Any]] = []
    covered: set[str] = set()

    for key, shard_name in _KEY_TO_SHARD:
        covered.add(key)
        staged_value = _load_staged_json(layout, shard_name)
        source_value = blob.get(key)
        staged_path = layout.scan_shard_relative(shard_name)
        source_elements = _element_count(source_value)
        staged_elements = _element_count(staged_value)
        identical = _canonical_digest(staged_value) == _canonical_digest(source_value)
        entries.append({
            "key": key,
            "staged_path": staged_path,
            "source_elements": source_elements,
            "staged_elements": staged_elements,
            "complete": staged_elements >= source_elements,
            "identical": identical,
            "artifact_of_record": blob_relative,
            "note": (
                "" if identical
                else "shard differs from the monolith; the monolith is the evidence of record"
            ),
        })

    covered.add(_JOBS_KEY)
    blob_jobs = blob.get(_JOBS_KEY) or []
    blob_by_key = {
        str(job.get("job_id") or job.get("name") or ""): job
        for job in blob_jobs if isinstance(job, dict)
    }
    matching = sum(
        1 for key, shard in shards.items()
        if key in blob_by_key and _canonical_digest(shard) == _canonical_digest(blob_by_key[key])
    )
    entries.append({
        "key": _JOBS_KEY,
        "staged_path": layout.relative(layout.scan_jobs) + "/",
        "source_elements": len(blob_jobs),
        "staged_elements": len(shards),
        "complete": len(shards) >= len(blob_jobs),
        "identical": matching == len(blob_jobs) == len(shards),
        "artifact_of_record": blob_relative,
        "note": f"{matching} of {len(blob_jobs)} designs are element-identical to their shard",
    })

    meta = _load_staged_json(layout, "meta.json")
    meta = meta if isinstance(meta, dict) else {}
    meta_path = layout.scan_shard_relative("meta.json")
    for key in _META_KEYS:
        covered.add(key)
        source_value = blob.get(key)
        staged_value = meta.get(key)
        identical = _canonical_digest(staged_value) == _canonical_digest(source_value)
        entries.append({
            "key": key,
            "staged_path": meta_path,
            "source_elements": _element_count(source_value),
            "staged_elements": _element_count(staged_value),
            "complete": _element_count(staged_value) >= _element_count(source_value),
            "identical": identical,
            "artifact_of_record": blob_relative,
            "note": (
                "" if identical
                else "serialisation differs from the monolith; the monolith is the evidence of record"
            ),
        })

    for key in sorted(set(blob) - covered):
        entries.append({
            "key": key,
            "staged_path": blob_relative,
            "source_elements": _element_count(blob.get(key)),
            "staged_elements": _element_count(blob.get(key)),
            "complete": True,
            "identical": True,
            "artifact_of_record": blob_relative,
            "note": "no dedicated shard; the staged monolith is the only copy",
        })

    entries.sort(key=lambda entry: entry["key"])
    return entries


# ── facts ───────────────────────────────────────────────────────────────────


def _load_shards(layout: WorkspaceLayout, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every design shard, addressed through the scanner's own ``job_files`` map."""
    job_files = manifest.get("job_files")
    if not isinstance(job_files, dict) or not job_files:
        raise WorkspaceBuildError(
            "scan/manifest.json carries no job_files index; the workspace cannot address designs"
        )
    shards: dict[str, dict[str, Any]] = {}
    for key in sorted(job_files):
        relative = str(job_files[key])
        path = layout.scan_shard(relative)
        if not path.is_file():
            raise WorkspaceBuildError(f"design shard named by the manifest is not staged: {relative}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceBuildError(f"design shard is unreadable: {relative}: {exc}") from exc
        if not isinstance(payload, dict):
            raise WorkspaceBuildError(f"design shard is not an object: {relative}")
        shards[str(key)] = payload
    return shards


def _write_units(
    layout: WorkspaceLayout, manifest: dict[str, Any], shards: dict[str, dict[str, Any]]
) -> None:
    job_files = manifest.get("job_files") or {}
    groups = manifest.get("job_groups") or {}
    kinds = manifest.get("artifact_kinds") or {}
    units = []
    for key in sorted(shards):
        shard = shards[key]
        units.append({
            "unit_id": key,
            "name": shard.get("name") or key,
            "job_id": shard.get("job_id") or "",
            "group": str(groups.get(key) or shard.get("group") or ""),
            "subgroup": str(shard.get("subgroup") or ""),
            "artifact_kind": str(kinds.get(key) or shard.get("artifact_kind") or ""),
            "artifact_role": str(shard.get("artifact_role") or ""),
            "source_root": str(shard.get("source_root") or ""),
            "source_design": str(shard.get("source_design") or ""),
            "path": str(shard.get("path") or ""),
            "version": str(shard.get("version") or ""),
            "active": shard.get("active"),
            "is_esb_route": bool(shard.get("is_esb_route")),
            "esb_component_types": sorted(str(x) for x in shard.get("esb_component_types") or ()),
            "scan_quality": str(shard.get("scan_quality") or ""),
            "component_count": _element_count(shard.get("components")),
            "connection_count": _element_count(shard.get("connections")),
            "tmap_count": _element_count(shard.get("tmap_data")),
            "txmlmap_count": _element_count(shard.get("txmlmap_data")),
            "trunjob_ref_count": _element_count(shard.get("trunjob_refs")),
            "artifact_ref_count": _element_count(shard.get("artifact_refs")),
            "context_group_names": sorted(
                str(group.get("name") or "")
                for group in shard.get("context_groups") or ()
                if isinstance(group, dict)
            ),
            "shard": layout.scan_shard_relative(str(job_files.get(key) or "")),
        })
    write_json_artifact(
        layout.facts_units,
        Provenance(
            derivation_kind=PROJECTION,
            artifact_of_record=(
                layout.relative(layout.scan_jobs),
                layout.relative(layout.scan_manifest),
            ),
            source_elements=len(shards),
            retained_elements=len(units),
            note=(
                "One row per design: identity, location and counts only. Component bodies, "
                "tMap expressions and SQL are NOT here -- open the shard named in `shard`."
            ),
        ),
        {"schema_version": "workspace-units-1", "units": units},
    )


def _write_dependencies(layout: WorkspaceLayout, manifest: dict[str, Any]) -> None:
    relations = [r for r in manifest.get("artifact_relations") or () if isinstance(r, dict)]
    resources = [r for r in manifest.get("resource_refs") or () if isinstance(r, dict)]
    write_json_artifact(
        layout.facts_dependencies,
        Provenance(
            derivation_kind=PROJECTION,
            artifact_of_record=(layout.relative(layout.scan_manifest),),
            source_elements=len(relations) + len(resources),
            retained_elements=len(relations) + len(resources),
            note="Every scanner-declared relation, resolved and unresolved alike; nothing filtered.",
        ),
        {
            "schema_version": "workspace-dependencies-1",
            "relations": relations,
            "resource_refs": resources,
            "job_calls": manifest.get("job_calls") or {},
            "job_called_by": manifest.get("job_called_by") or {},
            "unresolved_job_calls": manifest.get("unresolved_job_calls") or {},
            "job_routine_links": manifest.get("job_routine_links") or {},
            "unresolved_routine_refs": manifest.get("unresolved_routine_refs") or {},
            "routines_used_by": manifest.get("routines_used_by") or {},
        },
    )


def _write_interfaces(layout: WorkspaceLayout, manifest: dict[str, Any]) -> None:
    endpoints = [e for e in manifest.get("artifact_endpoints") or () if isinstance(e, dict)]
    channels = [c for c in manifest.get("artifact_channels") or () if isinstance(c, dict)]
    resources = [r for r in manifest.get("artifact_resources") or () if isinstance(r, dict)]
    by_artifact: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for kind, rows in (("endpoints", endpoints), ("channels", channels), ("resources", resources)):
        for row in rows:
            artifact = str(row.get("artifact") or "")
            by_artifact.setdefault(artifact, {}).setdefault(kind, []).append(row)
    write_json_artifact(
        layout.facts_interfaces,
        Provenance(
            derivation_kind=PROJECTION,
            artifact_of_record=(layout.relative(layout.scan_manifest),),
            source_elements=len(endpoints) + len(channels) + len(resources),
            retained_elements=len(endpoints) + len(channels) + len(resources),
            note="Regrouped by design; every row also appears in the flat lists.",
        ),
        {
            "schema_version": "workspace-interfaces-1",
            "endpoints": endpoints,
            "channels": channels,
            "resources": resources,
            "by_artifact": {key: by_artifact[key] for key in sorted(by_artifact)},
        },
    )


def _write_configuration(
    layout: WorkspaceLayout,
    manifest: dict[str, Any],
    contexts: list[dict[str, Any]],
    connections: list[dict[str, Any]],
) -> None:
    write_json_artifact(
        layout.facts_configuration,
        Provenance(
            derivation_kind=PROJECTION,
            artifact_of_record=(
                layout.scan_shard_relative("contexts.json"),
                layout.scan_shard_relative("connections.json"),
                layout.relative(layout.scan_manifest),
            ),
            source_elements=len(contexts) + len(connections),
            retained_elements=len(contexts) + len(connections),
            note=(
                "Values are the scanner's redacted values. A placeholder such as "
                "[PASSWORD_REDACTED] never proves two endpoints are the same system."
            ),
        ),
        {
            "schema_version": "workspace-configuration-1",
            "context_groups": contexts,
            "repository_connections": connections,
            "context_used_by": manifest.get("context_used_by") or {},
            "context_metadata": manifest.get("context_metadata") or {},
            "job_context_links": manifest.get("job_context_links") or {},
            "job_contexts_used": manifest.get("job_contexts_used") or {},
            "unresolved_context_refs": manifest.get("unresolved_context_refs") or {},
            "connection_used_by": manifest.get("connection_used_by") or {},
            "job_connections_used": manifest.get("job_connections_used") or {},
        },
    )


def _write_mappings(layout: WorkspaceLayout, mapping_accounting: list[dict[str, Any]]) -> int:
    """One addressable file per mapper, verbatim.

    Deliberately a verbatim copy of the scanner record rather than a rendered
    markdown table. ``enterprise/workspace.py::render_mapping_table`` already
    renders one, but importing it drags ``providers/custom/__init__.py`` -- and
    therefore the 7,876-line ``harness.py`` -- into this lane, which the plan's
    risk section names as the coupling that made the last two architectures
    impossible to remove. A verbatim shard needs no renderer and loses nothing.
    """
    tables = [t for t in mapping_accounting if isinstance(t, dict)]
    rows: list[str] = [
        "# Mapping accounting index",
        "",
        f"{len(tables)} mapper(s). Each row links the complete, untruncated record.",
        "",
        "| Table | Design | Component | Kind | Offered | Written | Rows | File |",
        "|---|---|---|---|---|---|---|---|",
    ]
    written = 0
    destinations: dict[Path, str] = {}
    for table in sorted(tables, key=lambda t: str(t.get("table_id") or "")):
        table_id = str(table.get("table_id") or "")
        if not table_id:
            raise WorkspaceBuildError(
                "mapping accounting contains a record with no table_id; it would not be "
                "addressable, and dropping it is not an option"
            )
        destination = layout.mapping_table(table_id)
        if destination in destinations:
            # Two ids that sanitise to one filename would have the second copy
            # overwrite the first: a silent discard with no trace.
            raise WorkspaceBuildError(
                f"mapper ids {destinations[destination]!r} and {table_id!r} both stage to "
                f"{layout.relative(destination)}"
            )
        destinations[destination] = table_id
        table_rows = table.get("rows") or []
        write_json_artifact(
            destination,
            Provenance(
                derivation_kind=PROJECTION,
                artifact_of_record=(layout.scan_shard_relative("mapping_accounting.json"),),
                source_elements=len(table_rows),
                retained_elements=len(table_rows),
                note=f"Verbatim copy of the accounting record for mapper {ascii_text(table_id)}.",
            ),
            {"schema_version": "workspace-mapping-table-1", "table": table},
        )
        written += 1
        rows.append(
            f"| {_cell(table_id)} | {_cell(table.get('unit_id'))} | "
            f"{_cell(table.get('component_name'))} | {_cell(table.get('mapper_kind'))} | "
            f"{_cell(table.get('offered_fields'))} | {_cell(table.get('written_fields'))} | "
            f"{len(table_rows)} | `{_cell(layout.relative(destination))}` |"
        )
    write_markdown(
        layout.facts_mappings_index,
        Provenance(
            derivation_kind=INDEX,
            artifact_of_record=(layout.scan_shard_relative("mapping_accounting.json"),),
            source_elements=len(tables),
            retained_elements=written,
            note="Navigation only. Field-level rows live in the per-mapper files.",
        ),
        rows,
    )
    return written


def _write_coverage_ledger(layout: WorkspaceLayout) -> None:
    write_markdown(
        layout.facts_coverage_ledger,
        Provenance(
            derivation_kind=INDEX,
            artifact_of_record=(layout.relative(layout.scan_blob),),
            note="Populated by the read ledger in Slice 3; empty is honest, not a gap in evidence.",
        ),
        [
            "# Coverage ledger",
            "",
            "**Not yet populated.**",
            "",
            "This file will report what the agent ACTUALLY READ, as recorded by the read ledger",
            "(plan decision 5), against what was staged. Coverage is never inferred from what a",
            "builder put on disk: an artifact nobody opened is a reportable gap, not a covered one.",
            "",
            "Until Slice 3 wires the ledger, no coverage claim may cite this file. What IS already",
            "checkable is staging completeness -- see `facts/scan_coverage.json`, which compares the",
            "sharded mirror against the monolith key by key.",
        ],
    )


def _write_limitations(
    layout: WorkspaceLayout, coverage: list[dict[str, Any]], warnings: list[str]
) -> None:
    lines = [
        "# Known limitations of this workspace",
        "",
        "**Not yet populated with analysis limitations.** Those arrive with the read ledger",
        "(Slice 3) and the run itself. What follows is what the deterministic build measured.",
        "",
        "## Staging limitations",
        "",
        "- `scan/sanitized/` (the redacted mirror of the source tree) and `scan/evidence/`",
        "  (routine source fragments) are excluded by the scanner's own approval rule and are",
        "  NOT staged here. `evidence/code/` is therefore empty in this slice.",
        "- `facts/` carries identity, counts and relations only. Component bodies, tMap",
        "  expressions and SQL live in the design shards under `scan/jobs/` and nowhere else",
        "  in this workspace; read the shard, do not infer from the fact tables.",
        "",
        "## Measured reductions in the sharded mirror",
        "",
    ]
    reductions = [e for e in coverage if not e.get("complete")]
    divergences = [e for e in coverage if e.get("complete") and not e.get("identical")]
    if reductions:
        lines += [
            "| Key | Shard | In monolith | In shard | Artifact of record |",
            "|---|---|---|---|---|",
        ]
        for entry in reductions:
            lines.append(
                f"| {_cell(entry['key'])} | `{_cell(entry['staged_path'])}` | "
                f"{entry['source_elements']} | {entry['staged_elements']} | "
                f"`{_cell(entry['artifact_of_record'])}` |"
            )
        lines += [
            "",
            "Each reduction above is addressable in full in the named artifact of record.",
        ]
    else:
        lines.append("None: every sharded key carries at least as many elements as the monolith.")
    lines += ["", "## Serialisation differences (not data loss)", ""]
    if divergences:
        for entry in divergences:
            lines.append(
                f"- `{_cell(entry['key'])}`: element counts agree, bytes do not. "
                f"{_cell(entry.get('note'))}"
            )
    else:
        lines.append("None.")
    lines += ["", "## Build warnings", ""]
    lines += [f"- {_cell(w)}" for w in warnings] if warnings else ["None."]
    write_markdown(
        layout.facts_limitations,
        Provenance(
            derivation_kind=INDEX,
            artifact_of_record=(layout.relative(layout.scan_blob),),
            note="Deterministic build observations; analysis limitations are added by later slices.",
        ),
        lines,
    )


# ── indexes ─────────────────────────────────────────────────────────────────


def _write_estate_index(
    layout: WorkspaceLayout, manifest: dict[str, Any], shards: dict[str, dict[str, Any]]
) -> None:
    groups = manifest.get("job_groups") or {}
    kinds = manifest.get("artifact_kinds") or {}
    job_files = manifest.get("job_files") or {}
    by_group: dict[str, list[str]] = {}
    for key in sorted(shards):
        group = str(groups.get(key) or shards[key].get("group") or "Global")
        by_group.setdefault(group, []).append(key)
    roots = manifest.get("source_roots") or []
    lines = [
        "# Estate index",
        "",
        f"Source roots: {len(roots)}. Groups: {len(by_group)}. Designs: {len(shards)}.",
        "",
        "Every design below is staged in full at the shard path in the last column. This index",
        "helps you FIND a design; it never decides which you may open.",
        "",
    ]
    for root in roots:
        label = root.get("label") if isinstance(root, dict) else str(root)
        identifier = root.get("id") if isinstance(root, dict) else str(root)
        lines.append(f"- Source root `{_cell(identifier)}` ({_cell(label)})")
    lines.append("")
    for group in sorted(by_group):
        members = by_group[group]
        lines += [
            f"## {_cell(group)} ({len(members)} designs)",
            "",
            "| Design | Kind | Components | Shard |",
            "|---|---|---|---|",
        ]
        for key in members:
            shard = shards[key]
            lines.append(
                f"| {_cell(key)} | {_cell(kinds.get(key) or shard.get('artifact_kind'))} | "
                f"{_element_count(shard.get('components'))} | "
                f"`{_cell(layout.scan_shard_relative(str(job_files.get(key) or '')))}` |"
            )
        lines.append("")
    write_markdown(
        layout.index_estate,
        Provenance(
            derivation_kind=INDEX,
            artifact_of_record=(
                layout.relative(layout.scan_manifest),
                layout.relative(layout.scan_jobs),
            ),
            source_elements=len(shards),
            retained_elements=len(shards),
            note="Navigation. Every design in the estate appears exactly once.",
        ),
        lines,
    )


def _write_systems_index(layout: WorkspaceLayout, manifest: dict[str, Any]) -> None:
    external = manifest.get("job_external_systems") or {}
    by_system: dict[str, list[str]] = {}
    for design, systems in sorted(external.items()):
        for system in systems or ():
            by_system.setdefault(str(system), []).append(str(design))
    endpoints = [e for e in manifest.get("artifact_endpoints") or () if isinstance(e, dict)]
    channels = [c for c in manifest.get("artifact_channels") or () if isinstance(c, dict)]
    endpoint_types: dict[str, int] = {}
    for endpoint in endpoints:
        key = f"{endpoint.get('endpoint_type')}/{endpoint.get('direction')}"
        endpoint_types[key] = endpoint_types.get(key, 0) + 1
    channel_types: dict[str, int] = {}
    for channel in channels:
        key = f"{channel.get('channel_type')}/{channel.get('direction')}"
        channel_types[key] = channel_types.get(key, 0) + 1
    lines = [
        "# Systems index",
        "",
        "Scanner-declared external systems and interface types. No inference: a system appears",
        "here only because the scanner attributed it to a design.",
        "",
        "## External systems by design count",
        "",
        "| System | Designs | Design list |",
        "|---|---|---|",
    ]
    for system in sorted(by_system, key=lambda s: (-len(by_system[s]), s)):
        members = sorted(by_system[system])
        lines.append(
            f"| {_cell(system)} | {len(members)} | {_cell(', '.join(members))} |"
        )
    lines += ["", "## Endpoint types", "", "| Type / direction | Count |", "|---|---|"]
    for key in sorted(endpoint_types):
        lines.append(f"| {_cell(key)} | {endpoint_types[key]} |")
    lines += ["", "## Channel types", "", "| Type / direction | Count |", "|---|---|"]
    for key in sorted(channel_types):
        lines.append(f"| {_cell(key)} | {channel_types[key]} |")
    lines += [
        "",
        "Full rows, including descriptors and component names, are in `facts/interfaces.json`.",
    ]
    write_markdown(
        layout.index_systems,
        Provenance(
            derivation_kind=INDEX,
            artifact_of_record=(layout.relative(layout.scan_manifest),),
            source_elements=len(endpoints) + len(channels),
            retained_elements=len(endpoints) + len(channels),
            note="Counts and grouping only; every underlying row is in facts/interfaces.json.",
        ),
        lines,
    )


def _write_dependencies_index(layout: WorkspaceLayout, manifest: dict[str, Any]) -> None:
    relations = [r for r in manifest.get("artifact_relations") or () if isinstance(r, dict)]
    by_type: dict[str, list[dict[str, Any]]] = {}
    for relation in relations:
        by_type.setdefault(str(relation.get("relation_type") or "unknown"), []).append(relation)
    lines = [
        "# Dependency index",
        "",
        f"{len(relations)} scanner-declared relations across {len(by_type)} relation types.",
        "Every relation is listed; none is summarised away.",
        "",
    ]
    for relation_type in sorted(by_type):
        rows = sorted(
            by_type[relation_type],
            key=lambda r: (str(r.get("source") or ""), str(r.get("target") or "")),
        )
        lines += [
            f"## {_cell(relation_type)} ({len(rows)})",
            "",
            "| Source | Target | Resolution | Provenance | Component |",
            "|---|---|---|---|---|",
        ]
        for row in rows:
            lines.append(
                f"| {_cell(row.get('source'))} | {_cell(row.get('target'))} | "
                f"{_cell(row.get('resolution'))} | {_cell(row.get('provenance'))} | "
                f"{_cell(row.get('component_name'))} |"
            )
        lines.append("")
    write_markdown(
        layout.index_dependencies,
        Provenance(
            derivation_kind=PROJECTION,
            artifact_of_record=(layout.relative(layout.scan_manifest),),
            source_elements=len(relations),
            retained_elements=len(relations),
            note="Every relation row is rendered; grouping is presentation, not selection.",
        ),
        lines,
    )


def _write_unresolved_index(layout: WorkspaceLayout, manifest: dict[str, Any]) -> None:
    relations = [r for r in manifest.get("artifact_relations") or () if isinstance(r, dict)]
    unresolved_relations = [
        r for r in relations if str(r.get("resolution") or "").lower() in {"unresolved", "ambiguous", ""}
    ]
    resources = [
        r for r in manifest.get("resource_refs") or ()
        if isinstance(r, dict) and str(r.get("resolution") or "").lower() != "resolved"
    ]
    context_refs = manifest.get("unresolved_context_refs") or {}
    routine_refs = manifest.get("unresolved_routine_refs") or {}
    job_calls = manifest.get("unresolved_job_calls") or {}
    lines = [
        "# Unresolved references",
        "",
        "What the scanner could NOT resolve. This is a filtered view by design: the complete",
        "relation list is in `facts/dependencies.json` and the evidence of record is",
        "`scan/manifest.json`. Nothing here is removed from either.",
        "",
        f"- Unresolved or ambiguous relations: {len(unresolved_relations)}",
        f"- Unresolved resource references: {len(resources)}",
        f"- Designs with unresolved context references: {len(context_refs)}",
        f"- Designs with unresolved routine references: {len(routine_refs)}",
        f"- Designs with unresolved job calls: {len(job_calls)}",
        "",
    ]
    if unresolved_relations:
        lines += [
            "## Relations",
            "",
            "| Source | Declared target | Type | Resolution |",
            "|---|---|---|---|",
        ]
        for row in sorted(
            unresolved_relations,
            key=lambda r: (str(r.get("source") or ""), str(r.get("target") or "")),
        ):
            lines.append(
                f"| {_cell(row.get('source'))} | {_cell(row.get('target'))} | "
                f"{_cell(row.get('relation_type'))} | {_cell(row.get('resolution'))} |"
            )
        lines.append("")
    if resources:
        lines += ["## Resource references", "", "| Design | Component | Type | Reason |", "|---|---|---|---|"]
        for row in sorted(resources, key=lambda r: str(r.get("artifact") or "")):
            lines.append(
                f"| {_cell(row.get('artifact'))} | {_cell(row.get('component_name'))} | "
                f"{_cell(row.get('resource_type'))} | {_cell(row.get('reason'))} |"
            )
        lines.append("")
    for title, mapping in (
        ("Context references", context_refs),
        ("Routine references", routine_refs),
        ("Job calls", job_calls),
    ):
        if not mapping:
            continue
        lines += [f"## {title}", "", "| Design | Unresolved |", "|---|---|"]
        for design in sorted(mapping):
            lines.append(f"| {_cell(design)} | {_cell(json.dumps(mapping[design], sort_keys=True))} |")
        lines.append("")
    write_markdown(
        layout.index_unresolved,
        Provenance(
            derivation_kind=INDEX,
            artifact_of_record=(layout.relative(layout.scan_manifest),),
            note=(
                "Deliberately partial: it lists only unresolved items. The complement is not "
                "discarded -- it is in facts/dependencies.json and scan/manifest.json."
            ),
        ),
        lines,
    )


# ── workspace documents ─────────────────────────────────────────────────────


_PROJECT_DOC_SEED = """# Project

**Not yet written.** This file is the agent's own description of what this project is:
what it integrates, for whom, and what matters about it. It is created once and
maintained by the agent; a rebuild of the workspace never overwrites it.

Write what you can support with evidence from `scan/`. Leave the rest empty rather
than plausible.
"""


def _write_agent_guide(layout: WorkspaceLayout) -> None:
    lines = [
        "# Agent guide",
        "",
        "How this workspace is laid out and what each folder is FOR. It is not a catalogue of",
        "contents -- `indexes/` is that.",
        "",
        "## The one rule",
        "",
        "`scan/` is the evidence of record and it is addressable in full, always. Everything in",
        "`facts/` and `indexes/` is a derived view that states, in its own header, which artifact",
        "it came from. If a derived view and a shard disagree, the shard wins and the disagreement",
        "is a finding worth recording.",
        "",
        "No file in this workspace forbids you from reading another. If an index does not answer",
        "your question, open the shard.",
        "",
        "## Folders",
        "",
        "| Folder | Written by | What it is |",
        "|---|---|---|",
        "| `scan/` | builder | Byte-exact mirror of the approved scan. Sharded designs under "
        "`scan/jobs/<group>/<design>.json`, plus `scan/scan_result.json` as the monolith of record. |",
        "| `facts/` | builder | Deterministic tables derived from the scan: units, dependencies, "
        "interfaces, configuration, per-mapper accounting, coverage and limitations. |",
        "| `indexes/` | builder | Navigation: estate, systems, dependencies, unresolved. |",
        "| `evidence/` | later slices | `code/`, `runtime/`, `external/`. Empty in this build. |",
        "| `skills/` | pack | The method: how to investigate, analyse and produce. |",
        "| `rules/` | pack | Grounding, evidence policy, source of truth. |",
        "| `policies/` | pack | Tool access, write permissions, publication policy. |",
        "| `templates/` | pack | Document shapes. |",
        "| `packs/` | pack | Method variants. |",
        "| `knowledge/` | agent | Key notes, decisions, lessons, terminology, open questions. "
        "Survives across runs. |",
        "| `notes/` | agent | Working memory for this run. |",
        "| `findings/` | agent | Findings for this run. |",
        "| `investigations/` | agent | `active/` and `completed/`. `completed/` survives. |",
        "| `agent-state/` | agent | Current goal, todo, checkpoint, recent actions. Resume reads this. |",
        "| `output/` | agent | The documents. Published by the staging swap. |",
        "",
        "A rebuild replaces `scan/`, `facts/` and `indexes/` wholesale and never touches the",
        "agent-written folders or `PROJECT.md`.",
        "",
        "## Conventions",
        "",
        "- Generated Markdown is ASCII-only. A non-ASCII character in a name is escaped with the",
        "  standard Python backslash form (`\\xNN`, `\\uNNNN`) so the codepoint is recoverable",
        "  rather than replaced by `?`. The shard holds the true bytes.",
        "- Every generated file starts with a machine-readable provenance comment naming its",
        "  artifact of record and, for content-carrying views, how many elements it kept.",
        "- Redacted values are placeholders such as `[PASSWORD_REDACTED]`. A placeholder never",
        "  proves that two endpoints are the same system.",
        "",
        "## Where the real evidence is",
        "",
        "- One design, in full: `scan/jobs/<group>/<design>.json` -- components, connections,",
        "  tMap data, context groups, sub-job calls.",
        "- The estate: `scan/manifest.json`.",
        "- Anything a shard reduces: `scan/scan_result.json`. `facts/scan_coverage.json` records",
        "  the measured comparison, key by key.",
    ]
    write_markdown(
        layout.agent_guide,
        Provenance(derivation_kind=GUIDE, note="Authored guidance, not derived evidence."),
        lines,
    )


def _write_evidence_readme(layout: WorkspaceLayout) -> None:
    text = "\n".join([
        "# Evidence lanes",
        "",
        "`code/`, `runtime/` and `external/` are reserved and EMPTY in this build.",
        "",
        "They are empty for a stated reason, not by omission:",
        "",
        "- `code/`: the scanner's redacted source mirror lives at `scan/sanitized/` in the scan",
        "  output directory and is excluded from the agent-eligible artifact tree by the",
        "  scanner's own approval rule, together with `scan/evidence/`. Staging either one here",
        "  would put bytes in front of an agent that the scan never approved for that use.",
        "  Component bodies, tMap expressions and SQL ARE available, inside the design shards",
        "  under `scan/jobs/`.",
        "- `runtime/`: runtime evidence (TMC and Elastic mirrors) is fetched per run, not staged",
        "  by the deterministic builder.",
        "- `external/`: non-code project material is not part of this slice.",
        "",
        "Nothing routes through this folder yet. Do not infer absence of evidence from it.",
        "",
    ])
    if not text.isascii():  # pragma: no cover - defensive
        raise WorkspaceBuildError("evidence README is not ASCII")
    _atomic_write(layout.evidence_readme, text)


# ── verification ────────────────────────────────────────────────────────────


def _iter_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file())


def verify_no_silent_truncation(layout: WorkspaceLayout) -> list[str]:
    """Return every way this workspace could be hiding a dropped fact.

    Re-reads the built workspace from disk -- it never trusts the builder's
    in-memory state -- so it is equally valid as a post-build gate and as a
    standalone audit of a workspace someone else produced.

    The five rules, and the failure each one exists for:

    1. **Mirror integrity.** A staged scan artifact whose bytes no longer match
       the approved digest. This is the "25 KB of 520 KB" shape applied to
       evidence: a truncated copy that still looks like the original.
    2. **Provenance.** Any file under ``facts/`` or ``indexes/`` that does not
       name its artifact of record. The bundler wrote exactly such a file.
    3. **Element accounting.** A projection that kept fewer elements than its
       source without naming where the rest stay addressable.
    4. **Reachability.** A design in the staged manifest that no fact table or
       index leads to. An unreachable design is a silently dropped one.
    5. **Coverage.** An incomplete shard whose declared artifact of record is
       not actually staged -- a promise of addressability that is not kept.
    """
    problems: list[str] = []
    receipt_path = layout.build_receipt
    if not receipt_path.is_file():
        return [f"missing build receipt: {layout.relative(receipt_path)}"]
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"unreadable build receipt: {exc}"]

    # 1 ── mirror integrity
    staged_files = (receipt.get("scan") or {}).get("files") or {}
    if not isinstance(staged_files, dict) or not staged_files:
        problems.append("build receipt records no staged scan files")
    for relative in sorted(staged_files):
        path = layout.path(relative)
        if not path.is_file():
            problems.append(f"staged scan artifact is missing: {relative}")
            continue
        actual = _sha256_file(path)
        if actual != str(staged_files[relative]):
            problems.append(
                f"staged scan artifact does not match its approved digest: {relative} "
                f"(approved {staged_files[relative]}, on disk {actual})"
            )

    # 2/3 ── provenance and element accounting in the derived lanes
    for lane in DERIVED_DIRS:
        for path in _iter_files(layout.path(lane)):
            relative = layout.relative(path)
            if path.stat().st_size == 0:
                problems.append(f"derived artifact is empty: {relative}")
                continue
            provenance = read_provenance(path)
            if provenance is None:
                problems.append(f"derived artifact declares no provenance: {relative}")
                continue
            if provenance.derivation_kind not in _DERIVED_KINDS:
                problems.append(
                    f"derived artifact has kind {provenance.derivation_kind!r}, expected one of "
                    f"{sorted(_DERIVED_KINDS)}: {relative}"
                )
            if not provenance.artifact_of_record:
                problems.append(f"derived artifact names no artifact of record: {relative}")
            for source in provenance.artifact_of_record:
                source_path = layout.path(source.rstrip("/"))
                if not source_path.exists():
                    problems.append(
                        f"artifact of record is not staged: {relative} cites {source}"
                    )
                elif not source.startswith("scan/"):
                    problems.append(
                        f"artifact of record is itself derived: {relative} cites {source}"
                    )
            if provenance.derivation_kind != PROJECTION:
                continue
            if provenance.source_elements is None or provenance.retained_elements is None:
                problems.append(f"projection without element accounting: {relative}")
                continue
            if provenance.retained_elements < provenance.source_elements:
                if not provenance.dropped_elements_addressable_at:
                    problems.append(
                        f"projection dropped elements without saying where they remain "
                        f"addressable: {relative} kept {provenance.retained_elements} of "
                        f"{provenance.source_elements}"
                    )
                for source in provenance.dropped_elements_addressable_at:
                    if not layout.path(source.rstrip("/")).exists():
                        problems.append(
                            f"projection points dropped elements at an artifact that is not "
                            f"staged: {relative} -> {source}"
                        )

    # 4 ── reachability: every design in the staged manifest is findable
    manifest_path = layout.scan_manifest
    units_path = layout.facts_units
    estate_path = layout.index_estate
    if manifest_path.is_file() and units_path.is_file() and estate_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            units_payload = json.loads(units_path.read_text(encoding="utf-8"))
            estate_text = estate_path.read_text(encoding="utf-8")
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"cannot verify design reachability: {exc}")
        else:
            job_files = manifest.get("job_files") or {}
            rows = {
                str(row.get("unit_id")): row
                for row in units_payload.get("units") or ()
                if isinstance(row, dict)
            }
            for key in sorted(job_files):
                row = rows.get(str(key))
                if row is None:
                    problems.append(f"design is not reachable from facts/units.json: {key}")
                    continue
                shard = str(row.get("shard") or "")
                if not shard or not layout.path(shard).is_file():
                    problems.append(f"design row points at a missing shard: {key} -> {shard!r}")
                if ascii_text(key) not in estate_text:
                    problems.append(f"design is not reachable from indexes/estate.md: {key}")
            extra = sorted(set(rows) - {str(k) for k in job_files})
            if extra:
                problems.append(
                    f"facts/units.json lists designs the manifest does not: {', '.join(extra)}"
                )
    else:
        problems.append("cannot verify design reachability: manifest, units or estate is missing")

    # 5 ── coverage promises are kept
    coverage_path = layout.facts_scan_coverage
    if coverage_path.is_file():
        try:
            coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"cannot read facts/scan_coverage.json: {exc}")
        else:
            for entry in coverage.get("keys") or ():
                if not isinstance(entry, dict) or entry.get("complete"):
                    continue
                record = str(entry.get("artifact_of_record") or "")
                if not record:
                    problems.append(
                        f"scan coverage reports an incomplete shard with no artifact of record: "
                        f"{entry.get('key')}"
                    )
                elif not layout.path(record).exists():
                    problems.append(
                        f"scan coverage names an artifact of record that is not staged: "
                        f"{entry.get('key')} -> {record}"
                    )
    else:
        problems.append("facts/scan_coverage.json is missing")

    return problems


# ── build ───────────────────────────────────────────────────────────────────


def scan_fingerprint(approval_tree_digest: str, scan_result_digest: str) -> str:
    """The one identity of the scan a workspace was built from.

    Path-independent, so two copies of one scan fingerprint identically. Slice 4
    needs exactly this to mark ``knowledge/`` entries for review when the scan
    underneath them changes; it lives here so that slice does not invent a second
    definition of "the same scan".
    """
    digest = hashlib.sha256()
    digest.update(approval_tree_digest.encode("utf-8"))
    digest.update(b"\0")
    digest.update(scan_result_digest.encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


@dataclass(frozen=True)
class WorkspaceBuildReceipt:
    layout: WorkspaceLayout
    scan_file_count: int
    scan_byte_count: int
    design_count: int
    mapping_table_count: int
    approval_tree_digest: str
    scan_result_digest: str
    scan_fingerprint: str
    warnings: tuple[str, ...]


def build_workspace(
    *,
    project_id: str,
    scan_output_dir: Path,
    workspaces_dir: Path,
) -> WorkspaceBuildReceipt:
    """Stage ``workspaces_dir/<project_id>/`` from an approved intodoc scan.

    Deterministic: same scan in, identical bytes out. Nothing here reads a
    clock, a random source or the environment.

    Idempotent: ``scan/``, ``facts/`` and ``indexes/`` are replaced; the
    agent-written folders and ``PROJECT.md`` are left exactly as they were, so
    a rebuild after a rescan cannot erase the workspace's memory.
    """
    scan_output_dir = Path(scan_output_dir)
    layout = WorkspaceLayout.for_project(Path(workspaces_dir), project_id)
    approval = load_scan_approval(scan_output_dir)

    warnings: list[str] = []
    if approval.allow_redaction_failures:
        warnings.append(
            "the scan was approved with --allow-redaction-failures: redaction findings were "
            "tolerated, so treat quoted values as potentially sensitive"
        )

    layout.root.mkdir(parents=True, exist_ok=True)
    for directory in (layout.facts, layout.indexes):
        if directory.exists():
            shutil.rmtree(directory)
    for directory in layout.all_directories():
        directory.mkdir(parents=True, exist_ok=True)

    staged = _stage_scan_mirror(layout, scan_output_dir, approval)
    if staged.unapproved_present:
        warnings.append(
            f"{len(staged.unapproved_present)} file(s) under the scan roots are not covered by the "
            f"approval receipt and were NOT staged: {', '.join(staged.unapproved_present[:5])}"
        )

    manifest = _load_staged_json(layout, "manifest.json")
    if not isinstance(manifest, dict):
        raise WorkspaceBuildError("scan/manifest.json is missing or malformed after staging")
    shards = _load_shards(layout, manifest)

    blob = json.loads(layout.scan_blob.read_text(encoding="utf-8"))
    if not isinstance(blob, dict):
        raise WorkspaceBuildError("scan_result.json is not an object")
    coverage = _shard_coverage(layout, blob, shards)
    del blob

    contexts = _load_staged_json(layout, "contexts.json")
    connections = _load_staged_json(layout, "connections.json")
    mapping_accounting = _load_staged_json(layout, "mapping_accounting.json")

    _write_units(layout, manifest, shards)
    _write_dependencies(layout, manifest)
    _write_interfaces(layout, manifest)
    _write_configuration(
        layout,
        manifest,
        [c for c in contexts or () if isinstance(c, dict)],
        [c for c in connections or () if isinstance(c, dict)],
    )
    mapping_count = _write_mappings(
        layout, [t for t in mapping_accounting or () if isinstance(t, dict)]
    )
    write_json_artifact(
        layout.facts_scan_coverage,
        Provenance(
            derivation_kind=PROJECTION,
            artifact_of_record=(layout.relative(layout.scan_blob),),
            source_elements=len(coverage),
            retained_elements=len(coverage),
            note=(
                "Measured comparison of the sharded mirror against the monolith, key by key. "
                "An incomplete shard names the artifact that still holds the full set."
            ),
        ),
        {"schema_version": "workspace-scan-coverage-1", "keys": coverage},
    )
    _write_coverage_ledger(layout)

    _write_estate_index(layout, manifest, shards)
    _write_systems_index(layout, manifest)
    _write_dependencies_index(layout, manifest)
    _write_unresolved_index(layout, manifest)

    _write_agent_guide(layout)
    _write_evidence_readme(layout)
    if not layout.project_doc.exists():
        _atomic_write(layout.project_doc, _PROJECT_DOC_SEED)

    reductions = [entry["key"] for entry in coverage if not entry.get("complete")]
    if reductions:
        warnings.append(
            "sharded mirror reduces "
            + ", ".join(sorted(reductions))
            + "; the monolith scan/scan_result.json is the artifact of record for those keys"
        )
    warnings.sort()
    _write_limitations(layout, coverage, warnings)

    _atomic_write(
        layout.build_receipt,
        json.dumps(
            {
                "schema_version": WORKSPACE_SCHEMA_VERSION,
                "builder_version": BUILDER_VERSION,
                "project_id": project_id,
                "approval": {
                    "schema_version": approval.schema_version,
                    "tree_digest": approval.tree_digest,
                    "tree_file_count": len(approval.tree_files),
                    "roots": list(approval.roots),
                    "excluded_prefixes": list(approval.excluded_prefixes),
                    "allow_redaction_failures": approval.allow_redaction_failures,
                },
                "scan": {
                    "file_count": len(staged.files),
                    "byte_count": staged.byte_count,
                    "scan_result_digest": staged.blob_digest,
                    "scan_fingerprint": scan_fingerprint(approval.tree_digest, staged.blob_digest),
                    "files": dict(sorted(staged.files.items())),
                    "unapproved_present": list(staged.unapproved_present),
                },
                "counts": {
                    "designs": len(shards),
                    "mapping_tables": mapping_count,
                    "coverage_keys": len(coverage),
                },
                "warnings": warnings,
            },
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
        )
        + "\n",
    )

    problems = verify_no_silent_truncation(layout)
    if problems:
        raise WorkspaceBuildError(
            "workspace failed its own no-silent-truncation check:\n- " + "\n- ".join(problems)
        )

    return WorkspaceBuildReceipt(
        layout=layout,
        scan_file_count=len(staged.files),
        scan_byte_count=staged.byte_count,
        design_count=len(shards),
        mapping_table_count=mapping_count,
        approval_tree_digest=approval.tree_digest,
        scan_result_digest=staged.blob_digest,
        scan_fingerprint=scan_fingerprint(approval.tree_digest, staged.blob_digest),
        warnings=tuple(warnings),
    )
