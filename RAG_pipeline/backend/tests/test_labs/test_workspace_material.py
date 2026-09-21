"""The labs workspace as the place uploaded material becomes files on disk.

Same contract as the guidance tests next door: `ensure_labs_workspace` is handed
a `ProjectMaterial` built here, never one compiled from the database. Whether the
compiler picked the right documents is the compiler's test to fail; what is
tested here is that whatever it picked lands at the path `workspace_relpath`
names, is recognisable as an extracted copy, and -- the part that actually
protects an answer -- stops existing the moment it stops being current.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.harness.guidance.material import (
    MaterialEntry,
    MaterialKind,
    ProjectMaterial,
    workspace_relpath,
)
from src.harness.guidance.models import (
    GuidanceKind,
    GuidanceLayer,
    GuidanceRule,
    ResolvedGuidance,
)
from src.harness.guidance.renderer import MANIFEST_SECTION_HEADING
from src.labs.workspace import ensure_labs_workspace

CONVERSATION_ID = 8123
PROJECT = "acme_landscape"
MCP_TOKEN = "labs-test-token"

POLICY_TEXT = "Every invoice exchange is reconciled before it is reported.\n"
METHOD_TEXT = "1. Read the koppelvlak contract.\n2. Check the last PROD run.\n"


@pytest.fixture
def docs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point both workspace roots at throwaway directories.

    Identical to the guidance suite's fixture, and deliberately a copy rather
    than a shared import: these two files test one module from two angles, and a
    shared conftest would make a change to either suite's setup a change to the
    other's meaning.
    """

    monkeypatch.setenv("LABS_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    root = tmp_path / "published"
    monkeypatch.setenv("LABS_DOCS_ROOT", str(root))
    return root


def entry(
    *,
    document_id: int,
    filename: str,
    role: str,
    kind: MaterialKind,
    materialized: bool = True,
) -> MaterialEntry:
    return MaterialEntry(
        document_id=document_id,
        filename=filename,
        role=role,
        kind=kind,
        char_count=len(POLICY_TEXT),
        materialized=materialized,
    )


CONSTRAINT = entry(
    document_id=41,
    filename="werkorder_analyse.pdf",
    role="policy",
    kind=MaterialKind.CONSTRAINT,
)
METHOD = entry(
    document_id=52,
    filename="nexus_testbevindingen.docx",
    role="guidance",
    kind=MaterialKind.METHOD,
)
REFERENCE = entry(
    document_id=63,
    filename="esb-naming-conventions.md",
    role="reference",
    kind=MaterialKind.REFERENCE,
)


def material(*entries: MaterialEntry, overflow: int = 0) -> ProjectMaterial:
    return ProjectMaterial(
        project_id=PROJECT,
        entries=tuple(entries),
        overflow_count=overflow,
        cache_key="test",
    )


def snapshot(workspace: Path) -> dict[str, bytes]:
    """Every regular file in the workspace, by relative path."""

    files: dict[str, bytes] = {}
    for path in sorted(workspace.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        files[str(path.relative_to(workspace))] = path.read_bytes()
    return files


async def ensure(
    *,
    project_id: str | None = PROJECT,
    guidance: ResolvedGuidance | None = None,
    material: ProjectMaterial | None = None,
    material_texts: dict[int, str] | None = None,
) -> Path:
    return Path(
        await ensure_labs_workspace(
            conversation_id=CONVERSATION_ID,
            project_id=project_id,
            mcp_token=MCP_TOKEN,
            guidance=guidance,
            material=material,
            material_texts=material_texts,
        )
    )


def project_dir(workspace: Path) -> Path:
    return workspace / ".nexus" / "project"


@pytest.mark.usefixtures("docs_root")
async def test_constraint_is_written_under_references_with_its_provenance():
    workspace = await ensure(
        material=material(CONSTRAINT), material_texts={41: POLICY_TEXT}
    )

    path = project_dir(workspace) / workspace_relpath(CONSTRAINT)
    assert path == project_dir(workspace) / "references" / "41-werkorder-analyse.md"
    body = path.read_text(encoding="utf-8")
    # The header has to answer "is this something a person wrote here, or a copy
    # of something that lives elsewhere?" before the agent quotes any of it.
    assert body.startswith("> Extracted copy of uploaded document 41")
    assert "role: policy" in body
    assert "werkorder_analyse.pdf" in body
    assert POLICY_TEXT.strip() in body


@pytest.mark.usefixtures("docs_root")
async def test_method_is_written_as_a_skill():
    workspace = await ensure(material=material(METHOD), material_texts={52: METHOD_TEXT})

    path = project_dir(workspace) / workspace_relpath(METHOD)
    # The document id prefixes the slug here as it does under references/: two
    # method documents whose filenames reduce to the same stem would otherwise
    # share one path, and the manifest would advertise both while only one text
    # was ever written.
    assert path == (
        project_dir(workspace) / "skills" / "52-nexus-testbevindingen" / "SKILL.md"
    )
    body = path.read_text(encoding="utf-8")
    assert body.startswith("> Extracted copy of uploaded document 52")
    assert "role: guidance" in body
    assert "Read the koppelvlak contract." in body


@pytest.mark.usefixtures("docs_root")
async def test_unmaterialized_entry_writes_no_file():
    # Present in the manifest, absent from the tree: the document is real and
    # searchable, its text simply is not stored for reading whole.
    search_only = entry(
        document_id=77,
        filename="huge-standard.pdf",
        role="standard",
        kind=MaterialKind.CONSTRAINT,
        materialized=False,
    )

    workspace = await ensure(
        material=material(search_only), material_texts={77: POLICY_TEXT}
    )

    assert not (project_dir(workspace) / "references").exists()
    assert (project_dir(workspace) / "MANIFEST.md").is_file()


@pytest.mark.usefixtures("docs_root")
async def test_mountable_entry_without_text_writes_no_file_and_does_not_raise():
    # The repository said this document is mountable but its text did not come
    # back. An empty file would tell the agent the document is empty, which is a
    # different and worse claim than "search it" -- which the manifest makes.
    workspace = await ensure(material=material(CONSTRAINT), material_texts={})

    assert not (project_dir(workspace) / workspace_relpath(CONSTRAINT)).exists()
    assert not (project_dir(workspace) / "references").exists()
    assert (project_dir(workspace) / "MANIFEST.md").is_file()


@pytest.mark.usefixtures("docs_root")
async def test_manifest_on_disk_matches_what_agents_md_carries():
    # Two copies of one render, not two renders: `AGENTS.md` is what is always in
    # context, `MANIFEST.md` is the copy that costs nothing until it is opened.
    workspace = await ensure(
        material=material(CONSTRAINT, METHOD, REFERENCE),
        material_texts={41: POLICY_TEXT, 52: METHOD_TEXT},
    )

    manifest = (project_dir(workspace) / "MANIFEST.md").read_text(encoding="utf-8")
    agents_md = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert MANIFEST_SECTION_HEADING in manifest
    assert manifest.strip() in agents_md
    assert agents_md.count(MANIFEST_SECTION_HEADING) == 1
    assert "- `.nexus/project/MANIFEST.md`" in agents_md


@pytest.mark.usefixtures("docs_root")
async def test_a_document_that_disappears_takes_its_file_with_it():
    # The failure this whole sweep exists for: a policy that was withdrawn is
    # still on disk next turn, the agent reads it, and cites it with confidence.
    workspace = await ensure(
        material=material(CONSTRAINT, METHOD),
        material_texts={41: POLICY_TEXT, 52: METHOD_TEXT},
    )
    assert (project_dir(workspace) / workspace_relpath(CONSTRAINT)).is_file()
    assert (project_dir(workspace) / workspace_relpath(METHOD)).is_file()

    await ensure(material=material(METHOD), material_texts={52: METHOD_TEXT})

    assert not (project_dir(workspace) / workspace_relpath(CONSTRAINT)).exists()
    assert (project_dir(workspace) / workspace_relpath(METHOD)).is_file()


@pytest.mark.usefixtures("docs_root")
async def test_a_role_change_leaves_nothing_behind_at_the_old_path():
    # Re-roling moves a document between the two owned directories. Nothing about
    # the new write touches the old path, so only the sweep can remove it.
    workspace = await ensure(
        material=material(REFERENCE), material_texts={63: POLICY_TEXT}
    )
    old_path = project_dir(workspace) / workspace_relpath(REFERENCE)
    assert old_path.is_file()

    reroled = REFERENCE.model_copy(update={"role": "guidance", "kind": MaterialKind.METHOD})
    await ensure(material=material(reroled), material_texts={63: METHOD_TEXT})

    assert not old_path.exists()
    assert not (project_dir(workspace) / "references").exists()
    assert (project_dir(workspace) / workspace_relpath(reroled)).is_file()


@pytest.mark.usefixtures("docs_root")
async def test_no_material_removes_everything_it_previously_mounted():
    # `material=None` is the compile-failed fallback, and it resolves toward
    # showing nothing rather than showing a copy whose source may be gone.
    workspace = await ensure(
        material=material(CONSTRAINT, METHOD),
        material_texts={41: POLICY_TEXT, 52: METHOD_TEXT},
    )

    await ensure(material=None)

    assert not (project_dir(workspace) / "references").exists()
    assert not (project_dir(workspace) / "skills").exists()
    assert not (project_dir(workspace) / "MANIFEST.md").exists()


@pytest.mark.usefixtures("docs_root")
async def test_no_material_leaves_the_guidance_tree_exactly_as_it_was():
    # The guidance suite describes a workspace built without material. If passing
    # `material=None` added so much as an empty directory, that description would
    # have quietly stopped being true.
    workspace = await ensure(guidance=None, material=None)

    assert (workspace / ".nexus" / "org" / "RULES.md").is_file()
    assert (workspace / ".nexus" / "thread" / "README.md").is_file()
    assert not (project_dir(workspace) / "MANIFEST.md").exists()
    assert not (project_dir(workspace) / "references").exists()
    assert not (project_dir(workspace) / "skills").exists()
    assert "MANIFEST.md" not in (workspace / "AGENTS.md").read_text(encoding="utf-8")


@pytest.mark.usefixtures("docs_root")
async def test_second_identical_call_is_byte_identical():
    texts = {41: POLICY_TEXT, 52: METHOD_TEXT}
    workspace = await ensure(material=material(CONSTRAINT, METHOD), material_texts=texts)
    first = snapshot(workspace)

    await ensure(material=material(CONSTRAINT, METHOD), material_texts=texts)

    assert snapshot(workspace) == first


async def test_the_sweep_leaves_the_guidance_layer_and_the_docs_mount_alone(
    docs_root: Path,
):
    # The sweep runs inside `.nexus/project/`, which it shares with `RULES.md`,
    # and inside a workspace that holds a symlink out to the client's published
    # documentation. Neither is its to remove, and the turn that removes every
    # mounted file is the one where a sweep reaching too wide would show.
    published = docs_root / PROJECT / "docs"
    published.mkdir(parents=True)
    (published / "OVERVIEW.md").write_text("# Overview\n", encoding="utf-8")
    guidance = ResolvedGuidance(
        project_id=PROJECT,
        project_rules=(
            GuidanceRule(
                id=2,
                layer=GuidanceLayer.PROJECT,
                kind=GuidanceKind.RULE,
                project_id=PROJECT,
                content="Name the source system whenever an answer crosses two systems.",
            ),
        ),
    )

    workspace = await ensure(
        guidance=guidance,
        material=material(CONSTRAINT),
        material_texts={41: POLICY_TEXT},
    )
    assert (project_dir(workspace) / "RULES.md").is_file()

    await ensure(guidance=guidance, material=None)

    assert (project_dir(workspace) / "RULES.md").is_file()
    assert not (project_dir(workspace) / "references").exists()
    # `docs` used to be a symlink out to the published documentation and this
    # test pinned that it survived the sweep. The mount has since been retired
    # -- documentation is reached through the project-scoped MCP tools, and a
    # symlink out of the conversation would need broad external-directory read
    # access. So the property worth pinning is the opposite one: the sweep runs
    # in a workspace root that no longer holds a mount, and must not invent one
    # or leave a dangling link behind.
    assert not (workspace / "docs").exists()
    assert not (workspace / "docs").is_symlink()
