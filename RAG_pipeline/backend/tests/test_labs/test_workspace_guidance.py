"""The labs workspace as the place the guidance stack becomes visible on disk.

These tests exercise `ensure_labs_workspace` directly with a `ResolvedGuidance`
built here, never one compiled from the database. The workspace's contract is
"render whatever stack you are handed"; whether the compiler produced the right
stack is the compiler's test to fail, and coupling the two would mean a database
regression breaking the containment-boundary tests as well.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.harness.guidance.models import (
    GuidanceKind,
    GuidanceLayer,
    GuidanceRule,
    ResolvedGuidance,
)
from src.harness.guidance.renderer import (
    ORG_INVARIANTS,
    ORG_SECTION_HEADING,
    PROJECT_SECTION_PREFIX,
)
from src.harness.packs.models import StyleAudience, StylePresentation, StyleTone
from src.labs.workspace import ensure_labs_workspace, labs_workspace_path

CONVERSATION_ID = 4711
PROJECT = "acme_landscape"
MCP_TOKEN = "labs-test-token"

CONVERSATION_HEADING = "# This conversation"
PRESENTATION_HEADING = "## Presentation overlay"


@pytest.fixture
def docs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point both workspace roots at throwaway directories.

    `workspace.py` resolves its environment through `_env` on every call rather
    than binding it at import time, so setting the variables here is enough --
    verified by the tests below, which would find a `/tmp` workspace instead of a
    `tmp_path` one if that were not true.
    """

    monkeypatch.setenv("LABS_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    root = tmp_path / "published"
    monkeypatch.setenv("LABS_DOCS_ROOT", str(root))
    return root


def publish_docs(docs_root: Path, project_id: str) -> Path:
    """Create the published docs directory the workspace mounts as `docs`."""

    published = docs_root / project_id / "docs"
    published.mkdir(parents=True)
    (published / "OVERVIEW.md").write_text("# Overview\n", encoding="utf-8")
    return published


def populated_guidance() -> ResolvedGuidance:
    return ResolvedGuidance(
        project_id=PROJECT,
        user_id=7,
        org_rules=(
            GuidanceRule(
                id=1,
                layer=GuidanceLayer.ORG,
                kind=GuidanceKind.RULE,
                content="Name the source system whenever an answer crosses two systems.",
            ),
        ),
        project_rules=(
            GuidanceRule(
                id=2,
                layer=GuidanceLayer.PROJECT,
                kind=GuidanceKind.RULE,
                project_id=PROJECT,
                content="Treat the nightly reconciliation job as in scope for every invoice question.",
            ),
            GuidanceRule(
                id=3,
                layer=GuidanceLayer.PROJECT,
                kind=GuidanceKind.VOCABULARY,
                project_id=PROJECT,
                # Lists, not tuples: the shape a JSONB round-trip returns.
                payload={
                    "term": "koppelvlak",
                    "aliases": ["interface"],
                    "description": "one contracted exchange between two systems",
                },
            ),
        ),
        person_style=StylePresentation(
            audience=StyleAudience.EXECUTIVE, tone=StyleTone.CONCISE
        ),
    )


def snapshot(workspace: Path) -> dict[str, bytes]:
    """Every regular file in the workspace, by relative path.

    Symlinks are skipped rather than followed: `docs` points outside the
    workspace, and its contents are not ours to compare.
    """

    files: dict[str, bytes] = {}
    for path in sorted(workspace.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        files[str(path.relative_to(workspace))] = path.read_bytes()
    return files


async def ensure(
    *, project_id: str | None = PROJECT, guidance: ResolvedGuidance | None = None
) -> Path:
    return Path(
        await ensure_labs_workspace(
            conversation_id=CONVERSATION_ID,
            project_id=project_id,
            mcp_token=MCP_TOKEN,
            guidance=guidance,
        )
    )


@pytest.mark.usefixtures("docs_root")
async def test_workspace_lands_under_the_configured_root():
    # Also the premise of every test below: `LABS_WORKSPACE_ROOT` is read per
    # call, so monkeypatching the environment is enough. If it were bound at
    # import time this workspace would be the shared `/tmp` default instead.
    workspace = await ensure()

    assert workspace.parent == Path(os.environ["LABS_WORKSPACE_ROOT"]).resolve()
    assert workspace == Path(labs_workspace_path(CONVERSATION_ID))
    assert workspace.is_dir()


@pytest.mark.usefixtures("docs_root")
async def test_missing_guidance_still_writes_the_org_invariants():
    workspace = await ensure(guidance=None)

    rules = (workspace / ".nexus" / "org" / "RULES.md").read_text(encoding="utf-8")
    assert rules.startswith(ORG_SECTION_HEADING)
    for invariant in ORG_INVARIANTS:
        assert invariant in rules


@pytest.mark.usefixtures("docs_root")
async def test_missing_guidance_writes_no_project_or_user_file():
    workspace = await ensure(guidance=None)

    assert not (workspace / ".nexus" / "project" / "RULES.md").exists()
    assert not (workspace / ".nexus" / "user" / "PREFERENCES.md").exists()


@pytest.mark.usefixtures("docs_root")
async def test_populated_guidance_writes_project_and_user_files():
    workspace = await ensure(guidance=populated_guidance())

    project_rules = (workspace / ".nexus" / "project" / "RULES.md").read_text(
        encoding="utf-8"
    )
    assert f"{PROJECT_SECTION_PREFIX}{PROJECT}" in project_rules
    assert "nightly reconciliation job" in project_rules
    assert "koppelvlak" in project_rules
    assert "aliases: interface" in project_rules

    preferences = (workspace / ".nexus" / "user" / "PREFERENCES.md").read_text(
        encoding="utf-8"
    )
    assert PRESENTATION_HEADING in preferences
    assert "audience=executive" in preferences
    assert "tone=concise" in preferences


@pytest.mark.usefixtures("docs_root")
async def test_empty_project_layer_writes_no_project_file():
    # A project is bound and a person has a preference, but the project itself
    # has nothing to say. An empty `## Project:` heading would tell the model the
    # project has no rules worth honouring, so the file is not created at all.
    guidance = ResolvedGuidance(
        project_id=PROJECT,
        user_id=7,
        person_style=StylePresentation(audience=StyleAudience.ENGINEERING),
    )

    workspace = await ensure(guidance=guidance)

    assert not (workspace / ".nexus" / "project" / "RULES.md").exists()
    assert (workspace / ".nexus" / "user" / "PREFERENCES.md").is_file()
    assert (workspace / ".nexus" / "org" / "RULES.md").is_file()


@pytest.mark.usefixtures("docs_root")
async def test_thread_level_is_present_and_says_history_is_not_on_disk():
    workspace = await ensure(guidance=populated_guidance())

    readme = (workspace / ".nexus" / "thread" / "README.md").read_text(encoding="utf-8")
    assert "conversation history" in readme.lower()
    assert "transcript" in readme


@pytest.mark.usefixtures("docs_root")
async def test_second_call_updates_in_place_and_is_byte_identical():
    guidance = populated_guidance()
    workspace = await ensure(guidance=guidance)
    # Proof the second call updates rather than recreating: nothing else in the
    # directory may be collateral damage.
    sentinel = workspace / "sentinel.txt"
    sentinel.write_text("survives the next turn\n", encoding="utf-8")
    first = snapshot(workspace)

    again = await ensure(guidance=guidance)

    assert again == workspace
    assert sentinel.is_file()
    assert snapshot(workspace) == first


@pytest.mark.usefixtures("docs_root")
async def test_project_layer_is_removed_once_it_renders_empty():
    # The stale-file case the "omitted when empty" rule has to survive: a turn
    # that had project rules, followed by one that does not. Leaving the file
    # behind would have the agent reading rules that no longer apply.
    workspace = await ensure(guidance=populated_guidance())
    assert (workspace / ".nexus" / "project" / "RULES.md").is_file()

    await ensure(guidance=ResolvedGuidance(project_id=PROJECT))

    assert not (workspace / ".nexus" / "project" / "RULES.md").exists()
    assert (workspace / ".nexus" / "org" / "RULES.md").is_file()


@pytest.mark.usefixtures("docs_root")
async def test_agents_md_carries_the_guidance_and_the_per_turn_context():
    workspace = await ensure(guidance=populated_guidance())

    agents_md = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert ORG_SECTION_HEADING in agents_md
    assert f"{PROJECT_SECTION_PREFIX}{PROJECT}" in agents_md
    assert PRESENTATION_HEADING in agents_md
    assert "nightly reconciliation job" in agents_md
    # What stayed: the facts that differ per conversation.
    assert CONVERSATION_HEADING in agents_md
    assert f"project `{PROJECT}`" in agents_md
    assert "## Where to look when you need more" in agents_md
    assert ".nexus/project/RULES.md" in agents_md


@pytest.mark.usefixtures("docs_root")
async def test_the_invariant_instructions_moved_out_of_agents_md():
    # They are the agent's system ROLE now, not project material it is asked to
    # take into account. Duplicating them here would cost the tokens twice on
    # every call and drift the first time one copy is edited.
    workspace = await ensure(guidance=populated_guidance())

    agents_md = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    system_prompt = (workspace / ".nexus" / "SYSTEM_PROMPT.md").read_text(
        encoding="utf-8"
    )

    for moved in (
        "`output/` is the only place you can write.",
        "Write for the reader, not about your plumbing",
        "Name things precisely",
    ):
        assert moved in system_prompt
        assert moved not in agents_md


@pytest.mark.usefixtures("docs_root")
async def test_guidance_block_precedes_the_per_turn_context():
    # Ordering is the cacheability property, not formatting: the stable part of
    # the prefix has to come first, and the person layer last within it.
    workspace = await ensure(guidance=populated_guidance())

    agents_md = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    org = agents_md.index(ORG_SECTION_HEADING)
    project = agents_md.index(f"{PROJECT_SECTION_PREFIX}{PROJECT}")
    person = agents_md.index(PRESENTATION_HEADING)
    conversation = agents_md.index(CONVERSATION_HEADING)

    assert org == 0
    assert org < project < person < conversation


@pytest.mark.usefixtures("docs_root")
async def test_pointer_section_names_only_files_that_exist():
    # Telling the model to read a path that was never written costs it a failed
    # `read` and the credibility of the rest of the section.
    workspace = await ensure(guidance=None)

    agents_md = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert "- `.nexus/org/RULES.md`" in agents_md
    assert ".nexus/project/RULES.md" not in agents_md
    assert ".nexus/user/PREFERENCES.md" not in agents_md
    # No docs are published for this project either.
    assert "- `docs/`" not in agents_md


async def test_pointer_section_names_the_docs_mount_once_it_exists(docs_root: Path):
    publish_docs(docs_root, PROJECT)

    workspace = await ensure(guidance=populated_guidance())

    agents_md = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert "- `docs/`" in agents_md
    assert "- `.nexus/project/RULES.md`" in agents_md
    assert "- `.nexus/user/PREFERENCES.md`" in agents_md


@pytest.mark.usefixtures("docs_root")
async def test_agents_md_does_not_restate_the_invariants():
    workspace = await ensure(guidance=None)

    agents_md = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    for invariant in ORG_INVARIANTS:
        assert agents_md.count(invariant) == 1


async def test_docs_symlink_is_mounted_when_the_project_has_published_docs(
    docs_root: Path,
):
    published = publish_docs(docs_root, PROJECT)

    workspace = await ensure(guidance=populated_guidance())

    docs_link = workspace / "docs"
    assert docs_link.is_symlink()
    assert docs_link.resolve() == published.resolve()
    assert (docs_link / "OVERVIEW.md").is_file()
    assert "it is the FALLBACK" in (workspace / "AGENTS.md").read_text(
        encoding="utf-8"
    )


@pytest.mark.usefixtures("docs_root")
async def test_no_docs_symlink_when_the_project_has_none():
    workspace = await ensure(guidance=populated_guidance())

    assert not (workspace / "docs").exists()
    assert not (workspace / "docs").is_symlink()
    assert "There is NO `docs/` directory" in (workspace / "AGENTS.md").read_text(
        encoding="utf-8"
    )


async def test_docs_symlink_is_dropped_when_the_project_unbinds(docs_root: Path):
    publish_docs(docs_root, PROJECT)
    workspace = await ensure(guidance=populated_guidance())
    assert (workspace / "docs").is_symlink()

    await ensure(project_id=None, guidance=None)

    assert not (workspace / "docs").is_symlink()
    assert (workspace / ".nexus" / "org" / "RULES.md").is_file()
