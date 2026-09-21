"""The workspace layout as a value, and the writer as its only consumer.

The two suites next door test what ends up on disk. This one tests the thing
that decides it: `project_workspace_files` is a pure function from (project,
guidance, material, texts, docs) to the complete set of files and their content,
and `ensure_labs_workspace` is supposed to be nothing more than that projection
plus `write_text`.

Two properties carry the refactor, and both are asserted rather than described:

* The projection touches no filesystem at all, which is what lets a read-only
  API show a user the worktree for a conversation that has not happened.
* The directory the writer produces is exactly the projection -- same paths, no
  extras, nothing left behind from a previous turn. The moment those can differ,
  the view a user is shown stops being the workspace an agent is given, which is
  the failure the whole extraction exists to prevent.
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
    estimate_tokens,
)
from src.harness.guidance.renderer import ORG_INVARIANTS, ORG_SECTION_HEADING
from src.harness.packs.models import StyleAudience, StylePresentation
from src.labs.workspace import (
    ORIGIN_ASSEMBLED,
    ORIGIN_DOCUMENT_PREFIX,
    ORIGIN_MANIFEST,
    ORIGIN_ORG_LAYER,
    ORIGIN_PERSON_LAYER,
    ORIGIN_PROJECT_LAYER,
    ORIGIN_SYSTEM_PROMPT,
    ORIGIN_THREAD,
    WorkspaceFile,
    always_loaded_tokens,
    ensure_labs_workspace,
    project_workspace_files,
)

CONVERSATION_ID = 9314
PROJECT = "acme_landscape"
MCP_TOKEN = "labs-projection-token"

POLICY_TEXT = "Every invoice exchange is reconciled before it is reported.\n"
METHOD_TEXT = "1. Read the koppelvlak contract.\n2. Check the last PROD run.\n"

CONSTRAINT = MaterialEntry(
    document_id=41,
    filename="werkorder_analyse.pdf",
    role="policy",
    kind=MaterialKind.CONSTRAINT,
    char_count=len(POLICY_TEXT),
    materialized=True,
)
METHOD = MaterialEntry(
    document_id=52,
    filename="nexus_testbevindingen.docx",
    role="guidance",
    kind=MaterialKind.METHOD,
    char_count=len(METHOD_TEXT),
    materialized=True,
)


@pytest.fixture
def docs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point both workspace roots at throwaway directories.

    A copy of the fixture the guidance and material suites use, deliberately:
    these files test one module from three angles, and a shared conftest would
    make a change to any one suite's setup a change to the others' meaning.
    """

    monkeypatch.setenv("LABS_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    root = tmp_path / "published"
    monkeypatch.setenv("LABS_DOCS_ROOT", str(root))
    return root


def guidance() -> ResolvedGuidance:
    return ResolvedGuidance(
        project_id=PROJECT,
        user_id=7,
        project_rules=(
            GuidanceRule(
                id=2,
                layer=GuidanceLayer.PROJECT,
                kind=GuidanceKind.RULE,
                project_id=PROJECT,
                content="Treat the nightly reconciliation job as in scope for invoices.",
            ),
        ),
        person_style=StylePresentation(audience=StyleAudience.EXECUTIVE),
    )


def material(*entries: MaterialEntry) -> ProjectMaterial:
    return ProjectMaterial(project_id=PROJECT, entries=tuple(entries), cache_key="test")


def project(
    *,
    project_id: str | None = PROJECT,
    resolved: ResolvedGuidance | None = None,
    entries: ProjectMaterial | None = None,
    texts: dict[int, str] | None = None,
    docs_mounted: bool = False,
) -> tuple[WorkspaceFile, ...]:
    return project_workspace_files(
        project_id=project_id,
        guidance=resolved,
        material=entries,
        material_texts=texts,
        docs_mounted=docs_mounted,
    )


def populated() -> tuple[WorkspaceFile, ...]:
    return project(
        resolved=guidance(),
        entries=material(CONSTRAINT, METHOD),
        texts={41: POLICY_TEXT, 52: METHOD_TEXT},
    )


def on_disk(workspace: Path) -> set[str]:
    """Every regular file in the workspace, as workspace-relative POSIX paths.

    Symlinks are skipped rather than followed: `docs` points outside the
    workspace and its contents are not ours to enumerate.
    """

    return {
        path.relative_to(workspace).as_posix()
        for path in workspace.rglob("*")
        if not path.is_symlink() and path.is_file()
    }


# -- purity ------------------------------------------------------------------


def test_the_projection_creates_nothing_on_disk(tmp_path: Path, monkeypatch) -> None:
    # The property the read-only API rests on. A projection that quietly created
    # a directory would make "show me this workspace" a write, and a GET that
    # writes is a GET that can be replayed into a full disk.
    monkeypatch.setenv("LABS_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("LABS_DOCS_ROOT", str(tmp_path))

    files = populated()

    assert files
    assert list(tmp_path.iterdir()) == []


def test_the_projection_is_deterministic() -> None:
    # Two calls with the same inputs must produce the same tuple in the same
    # order. A UI that re-fetches and sees the list reshuffle reads as a
    # workspace that changed when nothing did.
    assert populated() == populated()


def test_ordering_is_role_then_agents_then_levels_then_thread_then_manifest_then_material() -> None:
    paths = [file.path for file in populated()]

    assert paths == [
        ".nexus/SYSTEM_PROMPT.md",
        "AGENTS.md",
        ".nexus/org/RULES.md",
        ".nexus/project/RULES.md",
        ".nexus/user/PREFERENCES.md",
        ".nexus/thread/README.md",
        ".nexus/project/MANIFEST.md",
        ".nexus/project/references/41-werkorder-analyse.md",
        ".nexus/project/skills/52-nexus-testbevindingen/SKILL.md",
    ]


# -- what may never be projected ---------------------------------------------


def test_opencode_json_is_never_projected() -> None:
    # It carries the MCP bearer, and this tuple is returned verbatim to a
    # browser by the workspace endpoint. Every shape of input, including the
    # empty one, has to leave it out.
    for files in (populated(), project(), project(project_id=None)):
        assert all(file.path != "opencode.json" for file in files)


def test_no_projected_file_carries_the_mcp_bearer(docs_root: Path) -> None:
    # The stronger form of the rule above: not "opencode.json is absent" but
    # "the secret it holds is nowhere in the projection", which is what a
    # reviewer actually cares about.
    del docs_root
    body = "\n".join(file.content for file in populated())

    assert MCP_TOKEN not in body
    assert "Bearer" not in body
    assert "Authorization" not in body


# -- always_loaded -----------------------------------------------------------


def test_always_loaded_is_set_per_file_by_what_agents_md_inlines() -> None:
    flags = {file.path: file.always_loaded for file in populated()}

    assert flags == {
        # In front of the model on every turn.
        ".nexus/SYSTEM_PROMPT.md": True,
        "AGENTS.md": True,
        ".nexus/org/RULES.md": True,
        ".nexus/project/RULES.md": True,
        ".nexus/user/PREFERENCES.md": True,
        ".nexus/project/MANIFEST.md": True,
        # Read only if the agent decides to.
        ".nexus/thread/README.md": False,
        ".nexus/project/references/41-werkorder-analyse.md": False,
        ".nexus/project/skills/52-nexus-testbevindingen/SKILL.md": False,
    }


def test_every_always_loaded_file_is_a_copy_of_agents_md_or_the_role() -> None:
    # `always_loaded` is a claim about the model's context, not a label. Two
    # files earn it independently -- the system prompt, which reaches the model
    # as its role through `opencode.json`, and `AGENTS.md`, which OpenCode loads
    # unprompted. Every other one earns it only by being a byte-copy of a
    # section of `AGENTS.md`, and if that stops holding the flag becomes a lie
    # told to a user reading a token budget.
    files = populated()
    agents_md = next(file for file in files if file.path == "AGENTS.md")
    system_prompt = next(file for file in files if file.origin == ORIGIN_SYSTEM_PROMPT)

    for file in files:
        if not file.always_loaded or file in (agents_md, system_prompt):
            continue
        assert file.content.strip() in agents_md.content, file.path


def test_the_role_and_the_context_share_no_text() -> None:
    # The whole point of the split: an instruction that appears in both is paid
    # for twice on every call and drifts the day one copy is edited.
    files = populated()
    agents_md = next(file for file in files if file.path == "AGENTS.md")
    system_prompt = next(file for file in files if file.origin == ORIGIN_SYSTEM_PROMPT)

    prompt_paragraphs = {
        block.strip()
        for block in system_prompt.content.split("\n\n")
        if len(block.strip()) > 80
    }
    assert prompt_paragraphs
    for paragraph in prompt_paragraphs:
        assert paragraph not in agents_md.content


def test_always_loaded_tokens_counts_the_role_and_the_assembled_file_once() -> None:
    # Summing every always-loaded file would double-count the copies and report
    # roughly twice the real per-turn cost. Both independent files count.
    files = populated()
    agents_md = next(file for file in files if file.path == "AGENTS.md")
    system_prompt = next(file for file in files if file.origin == ORIGIN_SYSTEM_PROMPT)
    naive_sum = sum(estimate_tokens(file.content) for file in files if file.always_loaded)

    assert always_loaded_tokens(files) == estimate_tokens(
        agents_md.content
    ) + estimate_tokens(system_prompt.content)
    assert always_loaded_tokens(files) > 0
    assert always_loaded_tokens(files) < naive_sum


# -- origins -----------------------------------------------------------------


def test_origin_names_what_produced_each_file() -> None:
    origins = {file.path: file.origin for file in populated()}

    assert origins == {
        ".nexus/SYSTEM_PROMPT.md": ORIGIN_SYSTEM_PROMPT,
        "AGENTS.md": ORIGIN_ASSEMBLED,
        ".nexus/org/RULES.md": ORIGIN_ORG_LAYER,
        ".nexus/project/RULES.md": ORIGIN_PROJECT_LAYER,
        ".nexus/user/PREFERENCES.md": ORIGIN_PERSON_LAYER,
        ".nexus/thread/README.md": ORIGIN_THREAD,
        ".nexus/project/MANIFEST.md": ORIGIN_MANIFEST,
        ".nexus/project/references/41-werkorder-analyse.md": f"{ORIGIN_DOCUMENT_PREFIX}41",
        ".nexus/project/skills/52-nexus-testbevindingen/SKILL.md": (
            f"{ORIGIN_DOCUMENT_PREFIX}52"
        ),
    }


# -- degenerate inputs -------------------------------------------------------


def test_empty_guidance_still_projects_the_org_invariants() -> None:
    # The safety floor is code-owned, so "the guidance lookup failed" must still
    # produce the organization block in full -- in `AGENTS.md` and in the file
    # that mirrors it.
    files = project()
    by_path = {file.path: file.content for file in files}

    assert by_path[".nexus/org/RULES.md"].startswith(ORG_SECTION_HEADING)
    for invariant in ORG_INVARIANTS:
        assert invariant in by_path[".nexus/org/RULES.md"]
        assert invariant in by_path["AGENTS.md"]


def test_empty_guidance_projects_no_project_or_person_file() -> None:
    paths = {file.path for file in project()}

    assert ".nexus/project/RULES.md" not in paths
    assert ".nexus/user/PREFERENCES.md" not in paths
    assert ".nexus/thread/README.md" in paths


def test_a_mountable_entry_without_text_is_not_projected() -> None:
    # An empty file would tell the agent the document is empty; the manifest
    # already tells it to search instead.
    paths = {file.path for file in project(entries=material(CONSTRAINT), texts={})}

    assert ".nexus/project/MANIFEST.md" in paths
    assert not any(path.startswith(".nexus/project/references/") for path in paths)


def test_no_material_projects_no_manifest_and_no_mounted_files() -> None:
    paths = {file.path for file in project(resolved=guidance(), entries=None)}

    assert ".nexus/project/MANIFEST.md" not in paths
    assert not any(path.startswith(".nexus/project/skills/") for path in paths)


def test_docs_mounted_changes_only_agents_md() -> None:
    mounted = {file.path: file.content for file in project(docs_mounted=True)}
    absent = {file.path: file.content for file in project(docs_mounted=False)}

    assert mounted.keys() == absent.keys()
    assert "There is NO `docs/` directory" in absent["AGENTS.md"]
    assert "- `docs/`" in mounted["AGENTS.md"]
    assert {k: v for k, v in mounted.items() if k != "AGENTS.md"} == {
        k: v for k, v in absent.items() if k != "AGENTS.md"
    }


def test_docs_fallback_never_points_at_a_search_that_skips_the_mount() -> None:
    """`docs/` is a symlink, and the search tools do not descend one while walking.

    Measured in a live workspace whose `docs` holds 474 files:
    ``rg --files --glob='docs/**' .`` returned 0 and ``rg -c Talend .`` returned
    0, while the same searches with ``docs`` named as an explicit path returned
    474 and 214. OpenCode's `glob` and `grep` are both ripgrep-backed and pass no
    ``--follow``, so guidance that names either of them without also insisting on
    the path hands the agent a confident empty result from the one step that
    exists to catch a tool gap -- which is what produced a wrong client-facing
    answer.
    """

    agents_md = {file.path: file.content for file in project(docs_mounted=True)}["AGENTS.md"]

    # The fallback order is the one that was measured to work.
    assert "`list` with path `docs`" in agents_md
    assert "`grep` with path `docs`" in agents_md
    assert "`read` on `docs/<filename>`" in agents_md
    # The trap is named, not merely avoided: `glob`/`grep` are still reachable.
    assert "`docs/` is a mount" in agents_md
    assert "returns NOTHING from the documentation" in agents_md
    assert "never evidence that the documentation is silent" in agents_md


# -- the writer consumes the projection --------------------------------------


@pytest.mark.usefixtures("docs_root")
async def test_ensure_writes_exactly_the_projected_set_and_nothing_else() -> None:
    # The assertion that makes the extraction worth doing: whatever a user is
    # shown is what an agent is given, file for file. `opencode.json` is the one
    # permitted extra, and it is extra precisely because it is the file a UI
    # must never see.
    workspace = Path(
        await ensure_labs_workspace(
            conversation_id=CONVERSATION_ID,
            project_id=PROJECT,
            mcp_token=MCP_TOKEN,
            guidance=guidance(),
            material=material(CONSTRAINT, METHOD),
            material_texts={41: POLICY_TEXT, 52: METHOD_TEXT},
        )
    )

    projected = {file.path for file in populated()}

    assert on_disk(workspace) == projected | {"opencode.json"}


@pytest.mark.usefixtures("docs_root")
async def test_written_content_is_byte_identical_to_the_projection() -> None:
    workspace = Path(
        await ensure_labs_workspace(
            conversation_id=CONVERSATION_ID,
            project_id=PROJECT,
            mcp_token=MCP_TOKEN,
            guidance=guidance(),
            material=material(CONSTRAINT, METHOD),
            material_texts={41: POLICY_TEXT, 52: METHOD_TEXT},
        )
    )

    for file in populated():
        assert (workspace / file.path).read_text(encoding="utf-8") == file.content


@pytest.mark.usefixtures("docs_root")
async def test_the_sweep_removes_everything_the_projection_stopped_emitting() -> None:
    # A withdrawn policy, a level that went quiet and a manifest that emptied,
    # all in one turn. Each of them presents identically to the sweep -- as a
    # path that stopped being projected -- which is why one comparison covers
    # all three.
    workspace = Path(
        await ensure_labs_workspace(
            conversation_id=CONVERSATION_ID,
            project_id=PROJECT,
            mcp_token=MCP_TOKEN,
            guidance=guidance(),
            material=material(CONSTRAINT, METHOD),
            material_texts={41: POLICY_TEXT, 52: METHOD_TEXT},
        )
    )
    assert ".nexus/project/skills/52-nexus-testbevindingen/SKILL.md" in on_disk(workspace)

    await ensure_labs_workspace(
        conversation_id=CONVERSATION_ID,
        project_id=PROJECT,
        mcp_token=MCP_TOKEN,
        guidance=None,
        material=None,
    )

    projected = {file.path for file in project()}
    assert on_disk(workspace) == projected | {"opencode.json"}
    # And the directories the mounted files lived in are gone, not left empty.
    assert not (workspace / ".nexus" / "project" / "skills").exists()
    assert not (workspace / ".nexus" / "project" / "references").exists()


@pytest.mark.usefixtures("docs_root")
async def test_the_sweep_leaves_files_outside_the_projection_alone() -> None:
    # The sweep's bound is what stops it from reasoning about "files I did not
    # write" over the whole workspace, where `opencode.json` and the docs mount
    # both live.
    workspace = Path(
        await ensure_labs_workspace(
            conversation_id=CONVERSATION_ID,
            project_id=PROJECT,
            mcp_token=MCP_TOKEN,
            guidance=guidance(),
            material=material(CONSTRAINT),
            material_texts={41: POLICY_TEXT},
        )
    )
    sentinel = workspace / ".nexus" / "project" / "operator-note.txt"
    sentinel.write_text("not the projection's to delete\n", encoding="utf-8")

    await ensure_labs_workspace(
        conversation_id=CONVERSATION_ID,
        project_id=PROJECT,
        mcp_token=MCP_TOKEN,
        guidance=guidance(),
        material=None,
    )

    assert sentinel.is_file()
    assert (workspace / "opencode.json").is_file()


@pytest.mark.usefixtures("docs_root")
async def test_the_written_workspace_still_keeps_the_bearer_out_of_the_projection(
    docs_root: Path,
) -> None:
    # `opencode.json` holds the token on disk, where the agent's own runtime
    # needs it. The point of the projection is that reading it back gives a UI
    # the workspace without that file -- so the token is on disk and nowhere in
    # what the API can return.
    del docs_root
    workspace = Path(
        await ensure_labs_workspace(
            conversation_id=CONVERSATION_ID,
            project_id=PROJECT,
            mcp_token=MCP_TOKEN,
            guidance=guidance(),
            material=None,
        )
    )

    assert MCP_TOKEN in (workspace / "opencode.json").read_text(encoding="utf-8")
    assert all(MCP_TOKEN not in file.content for file in project(resolved=guidance()))


@pytest.mark.usefixtures("docs_root")
async def test_a_reroled_document_leaves_nothing_at_its_old_path() -> None:
    # Re-roling moves a document between the two owned directories. Nothing
    # about the new write touches the old path, so only the sweep can remove it.
    workspace = Path(
        await ensure_labs_workspace(
            conversation_id=CONVERSATION_ID,
            project_id=PROJECT,
            mcp_token=MCP_TOKEN,
            material=material(CONSTRAINT),
            material_texts={41: POLICY_TEXT},
        )
    )
    old = workspace / ".nexus" / "project" / workspace_relpath(CONSTRAINT)
    assert old.is_file()

    reroled = CONSTRAINT.model_copy(
        update={"role": "guidance", "kind": MaterialKind.METHOD}
    )
    await ensure_labs_workspace(
        conversation_id=CONVERSATION_ID,
        project_id=PROJECT,
        mcp_token=MCP_TOKEN,
        material=material(reroled),
        material_texts={41: METHOD_TEXT},
    )

    assert not old.exists()
    assert (workspace / ".nexus" / "project" / workspace_relpath(reroled)).is_file()
