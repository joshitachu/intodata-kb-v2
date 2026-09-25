"""Tests for the platform-agnostic (`_shared`) investigation method.

The shared method is the half of the library that is NOT about an artifact
format: the confidence vocabulary every finding is graded in, and the checklist
a document is measured against before it is called done. Its properties differ
from a platform method's in exactly two ways, and both are worth pinning:

* it reaches EVERY platform, including the ones with no directory of their own.
  A Workato or generic run that silently grades findings with no definition of
  `Confirmed` is the failure this namespace exists to prevent, and it is
  invisible -- the documents still look graded;
* it is NOT a platform. A caller enumerating platforms, or looking one up by
  name, must never be handed grading vocabulary where parsing guidance belongs;
  that is the same wrong-method error as handing Workato the Talend guides.

Everything else is deliberately identical to a platform method, and is tested
here to prove it stayed identical: the same digest verification, and the same
fail-closed contract. A confidence model whose bytes cannot be proved is worse
than none -- it is the file a model cites when it calls an inference `Confirmed`.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

import pytest
import yaml

import mcp_server
from mcp_server.config import config
from mcp_server.services.investigation_method import (
    METHOD_MANIFEST_FILENAME,
    METHODS_DIRNAME,
    SCORECARD_ROLES,
    SHARED_METHOD_KEY,
    STAGED_MANIFEST_FILENAME,
    STAGED_METHOD_DIRNAME,
    InvestigationMethodError,
    available_platforms,
    load_investigation_method,
    load_shared_method,
    materialize_investigation_method,
    materialize_shared_method,
    method_prompt_index,
    shared_method_paths_by_role,
    staged_method_dir,
    staged_method_dirs,
    staged_shared_method_dir,
)

EXPECTED_METHOD_VERSION = "shared-method-2"

#: Same Integration Suite revision as the Talend guides -- the two directories
#: must not be able to describe different methods.
EXPECTED_REVISION = "0c68b22633a6d067fcc30e29dd18e86d9ea6d24f"

#: Copied VERBATIM from upstream at ``EXPECTED_REVISION``. These are the files
#: the method-level ``source:`` block describes.
VERBATIM_FILES = {
    "confidence-model.md": "confidence",
    "validation-checklist.md": "validation",
}

#: The five assessment catalogues, consolidated here on 2026-09-25 from the
#: UNPINNED copy under ``src/mcp_server/templates/``. Two sources for one method
#: layer, and nothing saying which won; that is the drift this directory exists
#: to make impossible, so it had to own these too.
#:
#: Their bytes match NO upstream revision -- each was diffed at import against
#: both ``templates/integration/method/<name>.md`` at ``EXPECTED_REVISION`` and
#: the pre-refactor ``templates/integration/<name>.md`` at 686977cfaad0. They
#: therefore carry ``revision: unknown`` and a ``divergence:`` note per file.
DIVERGENT_FILES = {
    "scoring-rubric.md": "scoring-rubric",
    "anti-patterns.md": "anti-patterns",
    "red-flags.md": "red-flags",
    "security-checklist.md": "security-checklist",
    "pattern-library.md": "pattern-library",
}

EXPECTED_FILES = {**VERBATIM_FILES, **DIVERGENT_FILES}

#: Platforms with no method directory of their own. Each must still receive the
#: shared method.
PLATFORMS_WITHOUT_A_METHOD = ("workato", "lobster", "generic", "mulesoft")

REPO_METHODS = Path(mcp_server.__file__).resolve().parents[2] / "data" / METHODS_DIRNAME


@pytest.fixture()
def method_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A writable copy of the shipped method library, wired via config.data_dir.

    Returns the `_shared` directory, which is what these tests tamper with.
    """
    data_dir = tmp_path / "data"
    shutil.copytree(REPO_METHODS, data_dir / METHODS_DIRNAME)
    monkeypatch.setattr(config, "data_dir", data_dir)
    return data_dir / METHODS_DIRNAME / SHARED_METHOD_KEY


# --- loading and provenance ---------------------------------------------------


def test_shared_method_loads_and_verifies() -> None:
    method = load_shared_method()
    assert method is not None
    assert method.platform == SHARED_METHOD_KEY
    assert method.is_shared
    assert method.method_version == EXPECTED_METHOD_VERSION
    assert {item.name: item.role for item in method.files} == EXPECTED_FILES

    assert method.root is not None
    for item in method.files:
        path = method.root / item.name
        assert path.is_file()
        # The digest the manifest claims is the digest of the bytes on disk.
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item.sha256
        # Both new roles carry a prompt-usable purpose line; a role added to the
        # Literal without one renders as the generic fallback and tells a model
        # nothing about why it is being asked to read the file.
        assert item.purpose
        assert "Platform investigation guidance." not in item.purpose


def test_shared_method_records_where_it_came_from() -> None:
    """Pinned to the same upstream revision as the platform guides."""
    method = load_shared_method()
    assert method is not None
    assert method.source.name == "integration-suite"
    assert method.source.revision == EXPECTED_REVISION
    assert method.revision == EXPECTED_REVISION
    # The subtree matters here: the shared files come from a different path in
    # the same repository as the Talend guides, so repository alone would not
    # let a reviewer find the upstream original.
    assert method.source.path
    assert method.source.adapted_on

    assert method.update_log
    entry = method.update_log[0]
    assert entry.revision == EXPECTED_REVISION
    assert entry.date
    # The manifest spells this key `change:`; it must land on the one canonical
    # field, or a consumer reading `.note` silently sees an unexplained import.
    assert len(entry.note.split()) >= 15


def test_a_file_that_matches_no_upstream_revision_says_so_instead_of_claiming_one() -> None:
    """The five assessment catalogues were diffed at import and every one
    DIFFERS from upstream, at the pinned revision and at the only earlier one.

    The manifest has a single directory-level ``source.revision``. Left alone,
    it would have covered these five with a provenance claim a reviewer running
    the same diff would find false -- and overstated provenance is worse than an
    admitted gap, because it is the thing that stops anyone checking. So
    provenance is per FILE: ``revision: unknown`` overrides the method-level
    value, and ``divergence`` records what the diff actually showed.
    """
    method = load_shared_method()
    assert method is not None
    by_name = {item.name: item for item in method.files}

    for name in VERBATIM_FILES:
        item = by_name[name]
        assert item.revision == ""  # inherits the method's revision
        assert item.resolved_revision(method.source.revision) == EXPECTED_REVISION
        assert item.divergence == ""

    for name in DIVERGENT_FILES:
        item = by_name[name]
        assert item.resolved_revision(method.source.revision) == "unknown"
        assert item.resolved_revision(method.source.revision) != EXPECTED_REVISION
        # Not a bare flag: a reviewer has to be able to reproduce the diff.
        assert len(item.divergence.split()) >= 20
        assert EXPECTED_REVISION in item.divergence


def test_the_staged_manifest_carries_the_per_file_provenance(tmp_path: Path) -> None:
    """The staged copy is read on its own, away from the manifest that produced
    it, so a blank revision there is only interpretable if you still have the
    method to compare against."""
    staging = tmp_path / "docs_staging"
    assert materialize_shared_method(staging) is not None
    payload = yaml.safe_load(
        (staged_shared_method_dir(staging) / STAGED_MANIFEST_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    entries = {entry["name"]: entry for entry in payload["files"]}

    assert entries["confidence-model.md"]["revision"] == EXPECTED_REVISION
    assert "divergence" not in entries["confidence-model.md"]
    for name in DIVERGENT_FILES:
        assert entries[name]["revision"] == "unknown"
        assert entries[name]["divergence"]


def test_the_assessment_catalogues_have_exactly_one_home() -> None:
    """The defect this consolidation removed: two copies of one method layer,
    one pinned and one not, and nothing saying which won."""
    legacy = Path(mcp_server.__file__).resolve().parent / "templates"
    assert not sorted(legacy.glob("*.md")), (
        "assessment catalogues are back under src/mcp_server/templates/; the "
        "pinned library is the one source"
    )
    assert (legacy / "assessment.html").is_file(), "the Jinja report template stays"

    paths = shared_method_paths_by_role()
    for role in SCORECARD_ROLES:
        assert paths[role].is_file()


def test_shared_method_files_are_ascii_only() -> None:
    """Copied verbatim into every workspace; generated docs target cp1252."""
    method = load_shared_method()
    assert method is not None and method.root is not None
    for item in method.files:
        (method.root / item.name).read_bytes().decode("ascii")


def test_no_shared_method_file_names_a_client() -> None:
    """These files are copied verbatim into EVERY project's workspace.

    The same exposure already found once in ``nexus-adaptation.md``: one
    client's agent reading another client's design names on every run. These two
    files are upstream verbatim, so a hit here is a report to that team (or a
    decision to drop the file), not a local sed -- but it still has to be caught
    before it ships rather than after.
    """
    method = load_shared_method()
    assert method is not None and method.root is not None
    banned = re.compile(r"ettm|maximo|fourps|werkorder|vise2|assetcheck|alitalend", re.IGNORECASE)
    offenders = [
        f"{item.name} line {number}"
        for item in method.files
        for number, line in enumerate(
            (method.root / item.name).read_text(encoding="utf-8").splitlines(), 1
        )
        if banned.search(line)
    ]
    assert not offenders, offenders


# --- fail closed on tampering -------------------------------------------------


def test_edited_shared_file_fails_the_load_closed(method_library: Path) -> None:
    target = method_library / "confidence-model.md"
    target.write_text(target.read_text(encoding="utf-8") + "\nlocal edit\n", encoding="utf-8")

    with pytest.raises(InvestigationMethodError) as excinfo:
        load_shared_method()
    message = str(excinfo.value)
    assert "confidence-model.md" in message
    assert "sha256 mismatch" in message


def test_a_corrupted_shared_method_does_not_degrade(method_library: Path, tmp_path: Path) -> None:
    """The run stops; it does not continue with the vocabulary quietly missing.

    This is the property the whole namespace hangs on. Every other absence in
    this lane degrades -- a missing runtime fact makes a document thinner. An
    unverifiable confidence model makes a document *confidently wrong* while
    still looking graded, so it has to reach the caller as an exception on every
    entry point, and stage nothing on the way.
    """
    target = method_library / "validation-checklist.md"
    target.write_text("substituted checklist\n", encoding="utf-8")

    staging = tmp_path / "docs_staging"
    staging.mkdir()

    for call in (
        lambda: materialize_shared_method(staging),
        lambda: materialize_investigation_method(staging, "talend"),
        lambda: materialize_investigation_method(staging, "workato"),
        lambda: method_prompt_index(staging, "talend"),
    ):
        with pytest.raises(InvestigationMethodError):
            call()

    # Nothing staged at all: the shared method is staged first precisely so a
    # failure cannot leave a half-methodded workspace behind.
    assert not (staging / STAGED_METHOD_DIRNAME).exists()


def test_missing_shared_file_fails_the_load_closed(method_library: Path) -> None:
    (method_library / "validation-checklist.md").unlink()

    with pytest.raises(InvestigationMethodError) as excinfo:
        load_shared_method()
    assert "validation-checklist.md" in str(excinfo.value)
    assert "missing from disk" in str(excinfo.value)


def test_unlisted_shared_file_fails_the_load_closed(method_library: Path) -> None:
    """An unlisted file would be staged with no provenance and read as reviewed."""
    (method_library / "confidence-model-v2.md").write_text("# invented\n", encoding="utf-8")

    with pytest.raises(InvestigationMethodError) as excinfo:
        load_shared_method()
    assert "confidence-model-v2.md" in str(excinfo.value)
    assert "not listed in the manifest" in str(excinfo.value)


def test_shared_manifest_must_match_its_directory(method_library: Path) -> None:
    manifest = method_library / METHOD_MANIFEST_FILENAME
    raw = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    raw["platform"] = "talend"
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(InvestigationMethodError) as excinfo:
        load_shared_method()
    assert "declares platform 'talend'" in str(excinfo.value)


def test_an_absent_shared_library_degrades_instead_of_blocking_every_run(
    method_library: Path, tmp_path: Path
) -> None:
    """Absent is a deployment defect, not an untrustworthy file.

    The split is deliberate: a directory that exists and does not verify fails
    closed (above), because its bytes are claiming a provenance they do not
    have. A directory that is not there claims nothing -- raising would take
    every documentation run on every platform down with it.
    """
    shutil.rmtree(method_library)

    assert load_shared_method() is None
    staging = tmp_path / "docs_staging"
    assert materialize_shared_method(staging) is None
    # The platform method still stages, and its prompt index still renders.
    assert materialize_investigation_method(staging, "talend") is not None
    assert not staged_shared_method_dir(staging).exists()
    assert "talend-method-1" in method_prompt_index(staging, "talend")


# --- _shared is not a platform ------------------------------------------------


def test_shared_is_not_offered_as_a_selectable_platform() -> None:
    platforms = available_platforms()
    assert "talend" in platforms
    assert SHARED_METHOD_KEY not in platforms
    assert not any(name.startswith("_") for name in platforms)


@pytest.mark.parametrize("requested", ["_shared", "_SHARED", "  _Shared  "])
def test_asking_for_shared_as_a_platform_yields_no_parsing_method(requested: str) -> None:
    """A caller looking up a platform must not be handed vocabulary instead."""
    assert load_investigation_method(requested) is None


def test_any_underscore_directory_is_a_namespace_not_a_platform(method_library: Path) -> None:
    """The rule is the prefix, not the one name.

    A second namespace added to the library and forgotten in a name check would
    show up as a platform, and a run configured against it would load files that
    describe no artifact format as though they were parsing guidance.
    """
    namespace = method_library.parent / "_experimental"
    namespace.mkdir()
    (namespace / METHOD_MANIFEST_FILENAME).write_text(
        "platform: _experimental\nmethod_version: x-1\nfiles: []\n", encoding="utf-8"
    )

    assert "_experimental" not in available_platforms()
    assert "talend" in available_platforms()
    # Not even attempted: an invalid manifest there must not raise through a
    # platform lookup that should never have reached it.
    assert load_investigation_method("_experimental") is None


def test_materializing_shared_as_a_platform_stages_no_platform_dir(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    assert materialize_investigation_method(staging, SHARED_METHOD_KEY) is None
    # The shared method itself is still staged -- it is staged for every run.
    assert staged_shared_method_dir(staging).is_dir()


# --- materialisation ----------------------------------------------------------


def test_materialize_stages_the_shared_method_beside_the_platform_method(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "docs_staging"
    platform_dir = materialize_investigation_method(staging, "talend")

    assert platform_dir == staged_method_dir(staging, "talend")
    shared_dir = staged_shared_method_dir(staging)
    assert shared_dir == staging / STAGED_METHOD_DIRNAME / SHARED_METHOD_KEY
    assert shared_dir.is_dir()

    method = load_shared_method()
    assert method is not None and method.root is not None
    for item in method.files:
        assert (shared_dir / item.name).read_bytes() == (method.root / item.name).read_bytes()

    manifest = shared_dir / STAGED_MANIFEST_FILENAME
    assert manifest.is_file()
    payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))  # JSON is a YAML subset
    assert payload["platform"] == SHARED_METHOD_KEY
    assert payload["method_version"] == EXPECTED_METHOD_VERSION
    assert payload["source"]["revision"] == EXPECTED_REVISION
    assert {entry["name"]: entry["role"] for entry in payload["files"]} == EXPECTED_FILES
    assert sorted(payload["staged_files"]) == sorted(EXPECTED_FILES)
    # Every staged file carries its own purpose line, so the staged copy is
    # self-describing without the code that wrote it.
    assert all(entry["purpose"] for entry in payload["files"])

    assert not list(shared_dir.glob("*.tmp"))
    assert staged_method_dirs(staging, "talend") == (shared_dir, platform_dir)


@pytest.mark.parametrize("platform", PLATFORMS_WITHOUT_A_METHOD)
def test_a_platform_with_no_method_directory_still_gets_the_shared_method(
    platform: str, tmp_path: Path
) -> None:
    """The regression this namespace exists to prevent.

    Before `_shared`, these platforms staged nothing at all, so their runs
    graded findings against whatever definition of `Confirmed` the model
    supplied for itself.
    """
    staging = tmp_path / "docs_staging"

    # No platform guides: Talend parsing rules here would be confidently wrong.
    assert materialize_investigation_method(staging, platform) is None
    assert not staged_method_dir(staging, platform).exists()

    shared_dir = staged_shared_method_dir(staging)
    assert shared_dir.is_dir()
    assert sorted(path.name for path in shared_dir.glob("*.md")) == sorted(EXPECTED_FILES)
    assert (shared_dir / STAGED_MANIFEST_FILENAME).is_file()
    assert staged_method_dirs(staging, platform) == (shared_dir,)

    index = method_prompt_index(staging, platform)
    assert index
    assert index.isascii()
    for name, role in EXPECTED_FILES.items():
        assert f"{STAGED_METHOD_DIRNAME}/{SHARED_METHOD_KEY}/{name} [{role}] -- " in index
    # The absent platform guide is stated, not left to be discovered.
    assert f"No platform-specific method exists for '{platform}'" in index


@pytest.mark.parametrize("bad", ["", "   ", "../talend", "..", ".", "a/b", "a\\b"])
def test_a_platform_key_that_is_not_one_path_component_is_refused(
    bad: str, tmp_path: Path
) -> None:
    """The key is joined onto both the library root and the staging tree.

    `Path(x) / "" == x`, so a blank platform used to resolve to the `_method`
    ROOT -- invisible while that root was empty, but it now holds `_shared`, and
    a caller handed the root as one platform's directory would show a model the
    shared files under a platform's name, or write into it. A separator escapes
    the tree outright. Same rule the manifest's own filenames are held to.
    """
    staging = tmp_path / "docs_staging"
    materialize_investigation_method(staging, bad)

    with pytest.raises(ValueError):
        staged_method_dir(staging, bad)
    assert staged_method_dirs(staging, bad) == (staged_shared_method_dir(staging),)
    # Nothing was read from outside the library either.
    assert load_investigation_method(bad) is None
    assert [path.name for path in (staging / STAGED_METHOD_DIRNAME).iterdir()] == [
        SHARED_METHOD_KEY
    ]


def test_materialize_shared_is_idempotent(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    first = materialize_shared_method(staging)
    assert first is not None
    before = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in sorted(first.iterdir())
    }

    second = materialize_investigation_method(staging, "talend")
    assert second is not None
    after = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(staged_shared_method_dir(staging).iterdir())
    }
    # Byte-identical AND untouched: a rewrite would invalidate a prefix cache
    # even though the content matched.
    assert after == before


def test_materialize_repairs_a_tampered_staged_shared_copy(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    shared_dir = materialize_shared_method(staging)
    assert shared_dir is not None
    (shared_dir / "confidence-model.md").write_text("clobbered\n", encoding="utf-8")

    materialize_investigation_method(staging, "talend")
    method = load_shared_method()
    assert method is not None and method.root is not None
    assert (shared_dir / "confidence-model.md").read_bytes() == (
        method.root / "confidence-model.md"
    ).read_bytes()


# --- prompt index ordering ----------------------------------------------------


def _bullets(index: str) -> list[str]:
    return [line for line in index.splitlines() if line.startswith("- ")]


def test_prompt_index_puts_the_vocabulary_before_the_platform_guides(tmp_path: Path) -> None:
    """Adaptation, then confidence, then validation, then the platform guides.

    The order is an argument, not a preference: the adaptation is the only file
    that makes the others' procedures executable here, and every guide below the
    confidence model tells the model to grade a finding `Confirmed` or
    `Inferred` -- words that mean something specific, and that a model will
    otherwise supply its own plausible definitions for.
    """
    staging = tmp_path / "docs_staging"
    materialize_investigation_method(staging, "talend")

    bullets = _bullets(method_prompt_index(staging, "talend"))
    # Six Talend files, the two shared verbatim ones, and the five assessment
    # catalogues consolidated here in 2026-09.
    assert len(bullets) == 6 + len(EXPECTED_FILES)

    assert "nexus-adaptation.md" in bullets[0]
    assert f"{SHARED_METHOD_KEY}/confidence-model.md" in bullets[1]
    assert f"{SHARED_METHOD_KEY}/validation-checklist.md" in bullets[2]

    # Then the platform guides, uninterrupted.
    platform_span = bullets[3 : 3 + 5]
    assert all(f"{STAGED_METHOD_DIRNAME}/talend/" in line for line in platform_span)

    # The assessment catalogues trail everything. They are looked up when
    # scoring, not read to learn how to read an export, and leaving them on the
    # default rank would have listed all five AHEAD of the parsing guides --
    # shared files sort before platform files at equal rank.
    tail = bullets[3 + 5 :]
    assert {line.split("/")[-1].split(" ")[0] for line in tail} == set(DIVERGENT_FILES)

    positions = {
        name: next(i for i, line in enumerate(bullets) if name in line)
        for name in (
            "confidence-model.md", "profile.md", "parsing-core.md", "scoring-rubric.md",
        )
    }
    assert positions["confidence-model.md"] < positions["profile.md"]
    assert positions["confidence-model.md"] < positions["parsing-core.md"]
    assert positions["parsing-core.md"] < positions["scoring-rubric.md"]


def test_prompt_index_names_both_methods_and_their_versions(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    materialize_investigation_method(staging, "talend")

    index = method_prompt_index(staging, "talend")
    assert index.isascii()
    assert "TALEND INVESTIGATION METHOD (talend-method-1" in index
    assert f"WITH SHARED METHOD ({EXPECTED_METHOD_VERSION}" in index
    assert EXPECTED_REVISION[:12] in index


def test_prompt_index_skips_a_shared_file_that_is_not_on_disk(tmp_path: Path) -> None:
    """Built from the workspace, not from the manifest's promises."""
    staging = tmp_path / "docs_staging"
    shared_dir = materialize_shared_method(staging)
    assert shared_dir is not None
    (shared_dir / "validation-checklist.md").unlink()

    index = method_prompt_index(staging, "workato")
    assert "validation-checklist.md" not in index
    assert "confidence-model.md" in index


def test_prompt_index_is_empty_before_materialisation(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    assert method_prompt_index(staging, "workato") == ""
    assert method_prompt_index(staging, "talend") == ""
