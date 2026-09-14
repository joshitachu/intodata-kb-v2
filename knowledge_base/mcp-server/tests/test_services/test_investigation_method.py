"""Tests for the versioned investigation-method library.

The properties worth pinning are the ones whose failure would be invisible:

* the method a run stages must be provably the reviewed text -- a guide edited
  without updating its digest must fail the load, not stage quietly;
* a non-Talend scan must receive NO Talend method, because wrong method reads as
  authority and produces confident absences (`routes/`, `.item`, `tRunJob` do
  not exist on Workato);
* re-materialisation must be a no-op, or a resumed run churns staging and
  invalidates the provider prefix cache these files sit in;
* the Nexus adaptation guide must stay ASCII (Windows cp1252);
* the copied guides must still match the upstream checkout at the pinned
  revision, so a local edit cannot masquerade as an import.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

import mcp_server
from mcp_server.config import config
from mcp_server.providers.custom.pipeline.phases.phase4_cleanup import PUBLISHED_STAGING_DIRS
from mcp_server.services.investigation_method import (
    METHOD_MANIFEST_FILENAME,
    METHODS_DIRNAME,
    STAGED_MANIFEST_FILENAME,
    STAGED_METHOD_DIRNAME,
    InvestigationMethodError,
    load_investigation_method,
    materialize_investigation_method,
    method_prompt_index,
    methods_root,
    staged_method_dir,
)

#: The Integration Suite revision the Talend method was adapted from.
EXPECTED_REVISION = "0c68b22633a6d067fcc30e29dd18e86d9ea6d24f"

EXPECTED_METHOD_VERSION = "talend-method-1"

#: The five verbatim guides plus the one Nexus-authored adaptation.
EXPECTED_FILES = {
    "profile.md": "profile",
    "parsing-core.md": "core",
    "parsing-di-jobs.md": "di",
    "parsing-esb-routes.md": "esb",
    "platform-behaviour.md": "behaviour",
    "nexus-adaptation.md": "nexus-adaptation",
}

#: Only these five are copied from upstream; the adaptation guide is ours.
VERBATIM_FILES = tuple(name for name, role in EXPECTED_FILES.items() if role != "nexus-adaptation")

REPO_DATA = Path(mcp_server.__file__).resolve().parents[2] / "data"
REPO_METHODS = REPO_DATA / METHODS_DIRNAME

UPSTREAM_CHECKOUT = Path.home() / "Downloads" / "vw" / "integration-suite"
UPSTREAM_TALEND = UPSTREAM_CHECKOUT / "templates" / "integration" / "platforms" / "talend"


@pytest.fixture()
def method_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A writable copy of the shipped method library, wired via config.data_dir."""
    data_dir = tmp_path / "data"
    shutil.copytree(REPO_METHODS, data_dir / METHODS_DIRNAME)
    monkeypatch.setattr(config, "data_dir", data_dir)
    assert methods_root() == data_dir / METHODS_DIRNAME
    return data_dir / METHODS_DIRNAME / "talend"


# --- provenance ---------------------------------------------------------------


def test_pinned_revision_and_version_are_recorded() -> None:
    method = load_investigation_method("talend")
    assert method is not None
    assert method.platform == "talend"
    assert method.method_version == EXPECTED_METHOD_VERSION
    assert method.source.name == "integration-suite"
    assert method.source.revision == EXPECTED_REVISION
    assert method.revision == EXPECTED_REVISION
    assert method.source.adapted_on == "2026-09-09"
    # The import is logged, and the log names the revision it came from.
    assert method.update_log
    assert method.update_log[0].revision == EXPECTED_REVISION


def test_every_expected_file_is_listed_with_its_role_and_a_real_digest() -> None:
    method = load_investigation_method("talend")
    assert method is not None
    assert {item.name: item.role for item in method.files} == EXPECTED_FILES
    assert method.root is not None
    for item in method.files:
        path = method.root / item.name
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item.sha256
        # Every role carries a prompt-usable purpose line.
        assert item.purpose and item.purpose != ""


def test_platform_lookup_is_case_insensitive() -> None:
    assert load_investigation_method("Talend") is not None
    assert load_investigation_method("  TALEND ") is not None


# --- fail closed on tampering -------------------------------------------------


def test_edited_guide_fails_the_load_closed(method_library: Path) -> None:
    target = method_library / "parsing-core.md"
    target.write_text(target.read_text(encoding="utf-8") + "\nlocal edit\n", encoding="utf-8")

    with pytest.raises(InvestigationMethodError) as excinfo:
        load_investigation_method("talend")
    message = str(excinfo.value)
    assert "parsing-core.md" in message
    assert "sha256 mismatch" in message


def test_missing_guide_fails_the_load_closed(method_library: Path) -> None:
    (method_library / "platform-behaviour.md").unlink()

    with pytest.raises(InvestigationMethodError) as excinfo:
        load_investigation_method("talend")
    assert "platform-behaviour.md" in str(excinfo.value)
    assert "missing from disk" in str(excinfo.value)


def test_unlisted_guide_fails_the_load_closed(method_library: Path) -> None:
    """An unlisted file would be staged with no provenance and read as reviewed."""
    (method_library / "parsing-extra.md").write_text("# invented method\n", encoding="utf-8")

    with pytest.raises(InvestigationMethodError) as excinfo:
        load_investigation_method("talend")
    assert "parsing-extra.md" in str(excinfo.value)
    assert "not listed in the manifest" in str(excinfo.value)


def test_manifest_platform_must_match_its_directory(method_library: Path) -> None:
    manifest = method_library / METHOD_MANIFEST_FILENAME
    raw = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    raw["platform"] = "workato"
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(InvestigationMethodError) as excinfo:
        load_investigation_method("talend")
    assert "declares platform 'workato'" in str(excinfo.value)


def test_method_directory_without_a_manifest_fails_closed(method_library: Path) -> None:
    (method_library / METHOD_MANIFEST_FILENAME).unlink()

    with pytest.raises(InvestigationMethodError) as excinfo:
        load_investigation_method("talend")
    assert METHOD_MANIFEST_FILENAME in str(excinfo.value)


def test_manifest_entry_may_not_escape_its_directory(method_library: Path) -> None:
    manifest = method_library / METHOD_MANIFEST_FILENAME
    raw = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    raw["files"].append({"name": "../../doc_catalog.yaml", "role": "core", "sha256": "0" * 64})
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(InvestigationMethodError) as excinfo:
        load_investigation_method("talend")
    assert "not a plain filename" in str(excinfo.value)


# --- platform scoping ---------------------------------------------------------


@pytest.mark.parametrize("platform", ["workato", "lobster", "generic", "mulesoft", "", "  "])
def test_platforms_without_a_method_load_and_stage_nothing(
    platform: str, tmp_path: Path
) -> None:
    assert load_investigation_method(platform) is None
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    assert materialize_investigation_method(staging, platform) is None
    assert method_prompt_index(staging, platform) == ""
    # Nothing at all, not even an empty directory: an empty `_method/` invites a
    # model to go looking for guides that do not exist.
    assert not (staging / STAGED_METHOD_DIRNAME).exists()


# --- materialisation ----------------------------------------------------------


def test_materialize_copies_every_file_plus_a_manifest(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    target = materialize_investigation_method(staging, "talend")

    assert target == staging / STAGED_METHOD_DIRNAME / "talend"
    assert target == staged_method_dir(staging, "talend")

    method = load_investigation_method("talend")
    assert method is not None and method.root is not None
    for item in method.files:
        assert (target / item.name).read_bytes() == (method.root / item.name).read_bytes()

    manifest = target / STAGED_MANIFEST_FILENAME
    assert manifest.is_file()
    payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))  # JSON is a YAML subset
    assert payload["platform"] == "talend"
    assert payload["method_version"] == EXPECTED_METHOD_VERSION
    assert payload["source"]["revision"] == EXPECTED_REVISION
    assert {entry["name"]: entry["role"] for entry in payload["files"]} == EXPECTED_FILES
    assert {entry["sha256"] for entry in payload["files"]} == {
        item.sha256 for item in method.files
    }
    assert sorted(payload["staged_files"]) == sorted(EXPECTED_FILES)

    # No stray temp files left behind by the atomic writer.
    assert not list(target.glob("*.tmp"))


def test_materialize_is_idempotent(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    first = materialize_investigation_method(staging, "talend")
    assert first is not None
    before = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(first.iterdir())
    }

    second = materialize_investigation_method(staging, "talend")
    assert second == first
    after = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(second.iterdir())
    }
    # Byte-identical AND untouched: a rewrite would invalidate a prefix cache
    # even though the content matched.
    assert after == before


def test_materialize_repairs_a_tampered_staged_copy(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    target = materialize_investigation_method(staging, "talend")
    assert target is not None
    (target / "profile.md").write_text("clobbered\n", encoding="utf-8")

    materialize_investigation_method(staging, "talend")
    method = load_investigation_method("talend")
    assert method is not None and method.root is not None
    assert (target / "profile.md").read_bytes() == (method.root / "profile.md").read_bytes()


def test_staged_method_is_not_published(tmp_path: Path) -> None:
    """`_method/` must stay staging-only: method is prompt input, not a deliverable."""
    assert STAGED_METHOD_DIRNAME not in PUBLISHED_STAGING_DIRS


# --- prompt index -------------------------------------------------------------


def test_prompt_index_lists_the_staged_files_with_roles(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    materialize_investigation_method(staging, "talend")

    index = method_prompt_index(staging, "talend")
    assert index
    assert index.isascii()
    assert EXPECTED_METHOD_VERSION in index
    assert EXPECTED_REVISION[:12] in index
    for name, role in EXPECTED_FILES.items():
        assert f"{STAGED_METHOD_DIRNAME}/talend/{name} [{role}] -- " in index
    # One bullet per file, and every bullet is a relative staging path.
    bullets = [line for line in index.splitlines() if line.startswith("- ")]
    assert len(bullets) == len(EXPECTED_FILES)
    assert all(f"{STAGED_METHOD_DIRNAME}/talend/" in line for line in bullets)
    # The adaptation guide is FIRST: every other file's procedural instructions
    # (write a file, run a shell, fetch a URL) are wrong until it is read.
    assert "nexus-adaptation.md" in bullets[0]


def test_prompt_index_is_empty_before_materialisation(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    assert method_prompt_index(staging, "talend") == ""


def test_prompt_index_skips_a_file_that_is_not_on_disk(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    target = materialize_investigation_method(staging, "talend")
    assert target is not None
    (target / "platform-behaviour.md").unlink()

    index = method_prompt_index(staging, "talend")
    assert "platform-behaviour.md" not in index
    assert "parsing-core.md" in index


# --- content invariants -------------------------------------------------------


def test_nexus_adaptation_guide_is_ascii_only() -> None:
    method = load_investigation_method("talend")
    assert method is not None and method.root is not None
    body = (method.root / "nexus-adaptation.md").read_bytes()
    body.decode("ascii")  # raises UnicodeDecodeError on any non-ASCII byte


def test_nexus_adaptation_guide_states_the_run_contract() -> None:
    method = load_investigation_method("talend")
    assert method is not None and method.root is not None
    body = (method.root / "nexus-adaptation.md").read_text(encoding="ascii")

    # The workspace and the deterministic-fact layer. Retargeted for
    # agent-workspace-v1: no Talend source tree is staged any more, so the
    # adaptation names the SCAN's layout. `talend.project` still appears --
    # `profile.md` opens with a format gate on it and the adaptation has to say
    # where that gate was applied instead of just dropping the instruction.
    # `INDEX.md` and `facts/navigation/` are the navigation view; naming the
    # rollups without them sends a model into an estate-sized JSON file to
    # answer "which designs is this question about".
    for token in (
        "INDEX.md",
        "scan/jobs/",
        "scan/capability_ledger.json",
        "facts/units.json",
        "facts/mappings/",
        "facts/navigation/",
        "facts/parser_limitations.md",
        "runtime/",
        "talend.project",
    ):
        assert token in body, token

    # The guides are staged one file per guide and routed, not concatenated.
    assert "instructions/platform/README.md" in body
    assert "talend_semantics" not in body

    # Redaction, and the ban on joining identities over placeholders.
    for token in ("[PASSWORD_REDACTED]", "[URL_REDACTED]", "[HOSTNAME]"):
        assert token in body, token
    assert "Never establish identity by matching redacted values" in body

    # Already-computed scanner work, the output path, unknowns.
    assert "GUID" in body
    assert "Version dedup" in body
    # The agent writes its own files now. The submission tool is gone, and the
    # guide must not still describe it: an agent that goes looking for
    # `nexus_artifacts_submit_artifact` spends its budget on a tool that does
    # not exist and publishes nothing.
    assert "nexus_artifacts_submit_artifact" not in body
    assert "output/" in body and "notes/" in body
    assert "unknown:" in body and "locator:" in body
    # The parsing guides are staged nowhere and must be declared inapplicable
    # rather than left to be discovered as missing.
    assert "parsing-core.md" in body and "not staged" in body

    # The explicit non-applicability list.
    for token in (
        "scoring rubric",
        "phases 2 through 6",
        "bypassPermissions",
        "WebFetch",
        "assessment",
    ):
        assert token in body, token
    assert "What does NOT apply in Nexus" in body


def test_nexus_adaptation_guide_states_the_withheld_code_boundary() -> None:
    """The agent-facing projection carries no verbatim code body.

    The guide used to promise the opposite in two places -- "Java routines,
    with their code" in the layout and "verbatim `code`" in the
    already-computed table -- which is the worst possible pairing with house
    rule 2: a model told the bodies are there, finding them absent, reports
    the component as having no logic.
    """
    method = load_investigation_method("talend")
    assert method is not None and method.root is not None
    # Whitespace-normalised: the guide is hard-wrapped prose, so a phrase that
    # happens to straddle a line break must not read as a missing requirement.
    body = " ".join((method.root / "nexus-adaptation.md").read_text(encoding="ascii").split())

    assert "Java routines, with their code" not in body
    assert "and verbatim `code`" not in body

    assert "code_withheld" in body
    # The disclosure has to say what it costs, what survives, and what a
    # document may NOT conclude from it.
    assert "Mapping expressions are staged in full" in body
    assert "never conclude that a component has no logic because its body was withheld" in body
    assert "never reconstruct a body from its summary" in body

    # The parsing guides ARE staged now; only their procedures are
    # inapplicable. A guide that says it is absent while sitting next to the
    # reader is the same class of lie in the other direction.
    assert "not staged in this runtime" not in body
    assert "their reasoning still applies" in body


def test_every_adaptation_edit_is_recorded_in_the_update_log() -> None:
    """A digest change with no log entry is an unexplained edit.

    The manifest's digest check proves the bytes are the reviewed bytes; the
    log is what tells a reviewer which review they were reviewed in.

    Asserts the INVARIANT, not the wording of whichever entry happens to be
    last. It used to require ``code_withheld`` and ``instructions/platform/``
    in ``update_log[-1]``, which pinned the log's newest entry to the content
    of one particular edit -- so the next legitimate edit could not append
    without either failing this test or copying two unrelated phrases into its
    own note. A log that cannot be appended to stops being a log.
    """
    method = load_investigation_method("talend")
    assert method is not None
    assert len(method.update_log) >= 3
    for entry in method.update_log:
        assert entry.revision == EXPECTED_REVISION
        assert entry.date
        # Long enough to say what changed and why. A one-word note is an
        # unexplained edit wearing a log entry.
        assert len(entry.note.split()) >= 15, entry


def test_the_code_boundary_edit_is_still_recorded() -> None:
    """The specific review that introduced the withheld-code boundary.

    Kept as its own test and matched anywhere in the log rather than at its
    end, because it is a claim about a review that happened -- not a claim
    about what was reviewed most recently.
    """

    method = load_investigation_method("talend")
    assert method is not None
    notes = [entry.note for entry in method.update_log]
    assert any("code_withheld" in note for note in notes)
    assert any("instructions/platform/" in note for note in notes)


def test_no_staged_method_file_names_a_client() -> None:
    """These files are copied verbatim into EVERY project's workspace.

    VERIFIED LEAK: ``nexus-adaptation.md`` used two real customer job names as
    the unit-identity example and a real source group in the unknown-locator
    example, so one client's agent read another client's design names on every
    run. The Nexus-authored prose had been swept for exactly this before; the
    method asset had not, because it is data rather than code.

    Scoped to the Nexus-authored file. The five upstream guides are copied
    verbatim from the Integration Suite at a pinned revision and are not ours
    to edit -- a finding there is a report to that team, not a sed.
    """

    import re

    method = load_investigation_method("talend")
    assert method is not None and method.root is not None
    path = method.root / "nexus-adaptation.md"
    banned = re.compile(r"ettm|maximo|fourps|werkorder|vise2|assetcheck|alitalend", re.IGNORECASE)
    offenders = [
        f"line {number}"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if banned.search(line)
    ]
    assert not offenders, offenders


# --- upstream drift detector ---------------------------------------------------


def _upstream_head() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(UPSTREAM_CHECKOUT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def test_copied_guides_match_the_upstream_checkout() -> None:
    """Byte-identity against the pinned revision, when that checkout is present.

    Skipped rather than failed when the checkout is absent or has moved on: this
    is a drift detector for an unannounced local edit, not a build dependency.
    """
    if not UPSTREAM_TALEND.is_dir():
        pytest.skip(
            f"upstream Integration Suite checkout not present at {UPSTREAM_CHECKOUT}; "
            "byte-identity against the pinned revision cannot be verified here"
        )
    head = _upstream_head()
    if head is None:
        pytest.skip(
            f"{UPSTREAM_CHECKOUT} is not a readable git checkout; cannot confirm it is "
            f"at the pinned revision {EXPECTED_REVISION}"
        )
    if head != EXPECTED_REVISION:
        pytest.skip(
            f"local checkout is at {head}, method.yaml pins {EXPECTED_REVISION}; "
            "re-import deliberately (copy files, update revision + digests, append "
            "an update_log entry) rather than letting this test drift"
        )

    method = load_investigation_method("talend")
    assert method is not None and method.root is not None
    for name in VERBATIM_FILES:
        assert (method.root / name).read_bytes() == (UPSTREAM_TALEND / name).read_bytes(), (
            f"{name} differs from upstream at {EXPECTED_REVISION}: it was edited in place. "
            "Nexus-specific changes belong in nexus-adaptation.md."
        )
