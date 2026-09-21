"""The generation memory as the workspace sees it.

`test_generation_memory.py` next door tests the reader: what parses, what is
flagged, what is reduced. This suite tests the four properties that only exist
once the extracts are part of a workspace, and each of them is a rule the
module docstring already states rather than a behaviour invented here:

* The extracts are reachable -- `AGENTS.md` names them, which is the whole
  point; a file nothing points at is the failure this feature exists to end.
* They are on-demand, not always-loaded, so a 30 KB question list costs one
  sentence a turn.
* They DISAPPEAR. A record that was emptied, deleted or grown past the ceiling
  must not leave an extract behind for the agent to read and cite with full
  confidence.
* They are read-only. Nothing the agent can do writes back into the record.
"""

from __future__ import annotations

import json
from fnmatch import fnmatch
from pathlib import Path

import pytest

from src.labs.generation_memory import (
    MEMORY_DIRNAME,
    MEMORY_SECTION_HEADING,
    read_generation_memory,
)
from src.labs.project_output import KNOWLEDGE_DIRNAME
from src.labs.workspace import (
    _PROJECT_DIR,
    _SWEPT_ROOTS,
    ORIGIN_GENERATION_PREFIX,
    _edit_permission,
    always_loaded_tokens,
    ensure_labs_workspace,
    labs_workspace_path,
    project_workspace_files,
)

CONVERSATION_ID = 9315
PROJECT = "acme_landscape"
MCP_TOKEN = "labs-memory-token"
FINGERPRINT = "50a547820030aaaa"

MARKER = "--- entries below; append only, never edit above this line ---"
PREAMBLE = f"# Open Questions\n\nHOW TO WRITE HERE\n\n  Append an entry.\n\n{MARKER}\n"


@pytest.fixture
def docs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Throwaway workspace and output roots.

    A copy of the fixture the three suites next door use, deliberately: these
    files test one module from several angles, and a shared conftest would make
    a change to any one suite's setup a change to the others' meaning.
    """

    monkeypatch.setenv("LABS_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    root = tmp_path / "published"
    monkeypatch.setenv("LABS_DOCS_ROOT", str(root))
    monkeypatch.delenv("LABS_GENERATION_MEMORY", raising=False)
    return root


def write_record(root: Path, files: dict[str, str]) -> Path:
    knowledge = root / PROJECT / KNOWLEDGE_DIRNAME
    knowledge.mkdir(parents=True, exist_ok=True)
    for filename, text in files.items():
        (knowledge / filename).write_text(text, encoding="utf-8")
    (knowledge / "_scan_snapshot.json").write_text(
        json.dumps(
            {
                "fingerprint": FINGERPRINT,
                "captured_at_iso": "2026-09-17T18:27:42Z",
                "run_id": 260,
            }
        ),
        encoding="utf-8",
    )
    return knowledge


def record_with(body: str, filename: str = "open-questions.md") -> dict[str, str]:
    return {
        filename: PREAMBLE
        + f"### 2026-09-01T00:00:00Z | author: agent | scan: {FINGERPRINT}\n{body}\n"
    }


def memory_for(root: Path, files: dict[str, str]):
    return read_generation_memory(write_record(root, files), project_id=PROJECT)


def on_disk(workspace: Path) -> set[str]:
    return {
        path.relative_to(workspace).as_posix()
        for path in workspace.rglob("*")
        if not path.is_symlink() and path.is_file()
    }


# -- the projection ----------------------------------------------------------


def test_each_extract_is_projected_under_its_own_directory(docs_root: Path) -> None:
    memory = memory_for(
        docs_root,
        {**record_with("where is the endpoint defined?"), **record_with("VISE2 != Vise2", "terminology.md")},
    )

    files = project_workspace_files(
        project_id=PROJECT,
        guidance=None,
        material=None,
        material_texts=None,
        docs_mounted=False,
        memory=memory,
    )

    paths = [file.path for file in files]
    assert f".nexus/project/{MEMORY_DIRNAME}/open-questions.md" in paths
    assert f".nexus/project/{MEMORY_DIRNAME}/terminology.md" in paths
    # Last in the tuple: it is the only tier nobody authored for the agent.
    assert paths[-2:] == [
        f".nexus/project/{MEMORY_DIRNAME}/open-questions.md",
        f".nexus/project/{MEMORY_DIRNAME}/terminology.md",
    ]


def test_an_extract_is_reachable_but_not_always_loaded(docs_root: Path) -> None:
    memory = memory_for(docs_root, record_with("x" * 4_000))

    files = project_workspace_files(
        project_id=PROJECT,
        guidance=None,
        material=None,
        material_texts=None,
        docs_mounted=False,
        memory=memory,
    )

    extract = next(file for file in files if MEMORY_DIRNAME in file.path)
    agents = next(file for file in files if file.path == "AGENTS.md")

    assert extract.always_loaded is False
    assert extract.origin == f"{ORIGIN_GENERATION_PREFIX}open-questions.md"
    # The body is NOT in the always-loaded block; the pointer to it is.
    assert "x" * 4_000 not in agents.content
    assert extract.path in agents.content


def test_agents_md_names_the_section_and_every_extract(docs_root: Path) -> None:
    # Reachability is the whole feature. An extract the agent is never told
    # about is exactly as useful as the record was before this change: not at
    # all.
    memory = memory_for(docs_root, record_with("open question body"))

    files = project_workspace_files(
        project_id=PROJECT,
        guidance=None,
        material=None,
        material_texts=None,
        docs_mounted=False,
        memory=memory,
    )
    agents = next(file for file in files if file.path == "AGENTS.md")

    assert MEMORY_SECTION_HEADING in agents.content
    assert f".nexus/project/{MEMORY_DIRNAME}/open-questions.md" in agents.content


def test_no_memory_adds_no_section(docs_root: Path) -> None:
    files = project_workspace_files(
        project_id=PROJECT,
        guidance=None,
        material=None,
        material_texts=None,
        docs_mounted=False,
        memory=None,
    )
    agents = next(file for file in files if file.path == "AGENTS.md")

    assert MEMORY_SECTION_HEADING not in agents.content
    assert all(MEMORY_DIRNAME not in file.path for file in files)


def test_the_projection_still_creates_nothing_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The property the read-only workspace API rests on, re-asserted with the
    # new input: `project_workspace_files` must stay pure even though the
    # memory it is handed came off a disk somewhere else.
    root = tmp_path / "published"
    monkeypatch.setenv("LABS_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LABS_DOCS_ROOT", str(root))
    memory = memory_for(root, record_with("body"))
    before = sorted(path.name for path in tmp_path.iterdir())

    project_workspace_files(
        project_id=PROJECT,
        guidance=None,
        material=None,
        material_texts=None,
        docs_mounted=False,
        memory=memory,
    )

    assert sorted(path.name for path in tmp_path.iterdir()) == before
    assert not (tmp_path / "workspaces").exists()


def test_extracts_do_not_change_the_always_loaded_token_count(docs_root: Path) -> None:
    # They are on-demand files. If they ever start counting, the number an
    # operator is shown stops describing what a turn actually pays.
    def files(memory: object) -> tuple:
        return project_workspace_files(
            project_id=PROJECT,
            guidance=None,
            material=None,
            material_texts=None,
            docs_mounted=False,
            memory=memory,  # type: ignore[arg-type]
        )

    memory = memory_for(docs_root, record_with("y" * 6_000))
    without = files(None)
    with_memory = files(memory)

    extra = always_loaded_tokens(with_memory) - always_loaded_tokens(without)
    # The section itself is inside AGENTS.md and does cost something; the 6 KB
    # body must not be part of it.
    assert 0 < extra < 200, extra


# -- writing it out, and sweeping it away ------------------------------------


async def test_the_written_workspace_is_exactly_the_projection(docs_root: Path) -> None:
    memory = memory_for(docs_root, record_with("open question body"))

    await ensure_labs_workspace(
        conversation_id=CONVERSATION_ID,
        project_id=PROJECT,
        mcp_token=MCP_TOKEN,
        memory=memory,
    )

    workspace = Path(labs_workspace_path(CONVERSATION_ID))
    projected = {
        file.path
        for file in project_workspace_files(
            project_id=PROJECT,
            guidance=None,
            material=None,
            material_texts=None,
            docs_mounted=False,
            memory=memory,
        )
    }
    assert on_disk(workspace) - {"opencode.json"} == projected
    extract = workspace / ".nexus" / "project" / MEMORY_DIRNAME / "open-questions.md"
    assert "open question body" in extract.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "second_record",
    [
        pytest.param(None, id="record_deleted"),
        pytest.param({"open-questions.md": PREAMBLE}, id="record_emptied"),
    ],
)
async def test_an_extract_disappears_when_the_record_does(
    docs_root: Path, second_record: dict[str, str] | None
) -> None:
    # The docstring's warning, tested: a mounted extract that stopped being
    # true is worse than none, because the agent reads it and cites it with
    # full confidence. Deletion and emptying both present to the sweep as a
    # path that stopped being projected, so one mechanism covers both.
    first = memory_for(docs_root, record_with("stale question"))
    await ensure_labs_workspace(
        conversation_id=CONVERSATION_ID,
        project_id=PROJECT,
        mcp_token=MCP_TOKEN,
        memory=first,
    )
    workspace = Path(labs_workspace_path(CONVERSATION_ID))
    extract = workspace / ".nexus" / "project" / MEMORY_DIRNAME / "open-questions.md"
    assert extract.is_file()

    second = None if second_record is None else memory_for(docs_root, second_record)

    await ensure_labs_workspace(
        conversation_id=CONVERSATION_ID,
        project_id=PROJECT,
        mcp_token=MCP_TOKEN,
        memory=second,
    )

    assert not extract.exists()
    assert not extract.parent.exists()
    agents = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert MEMORY_SECTION_HEADING not in agents


def test_the_memory_root_is_disjoint_from_the_material_roots() -> None:
    # The memory owns its own swept root. If it shared `references/` with the
    # uploaded documents, the sweep -- which deletes everything under a root
    # that the projection did not emit -- would have two writers in one
    # directory, and whichever ran second would delete the other's files.
    # Asserted against the real constants rather than by planting a file,
    # because the property is "these paths do not overlap", not "one
    # particular file survived one particular ordering".
    memory_root = f".nexus/{_PROJECT_DIR}/{MEMORY_DIRNAME}"

    assert memory_root in _SWEPT_ROOTS
    others = [root for root in _SWEPT_ROOTS if root != memory_root]
    assert others, "the material roots disappeared; this test is now vacuous"
    for other in others:
        assert not other.startswith(f"{memory_root}/")
        assert not memory_root.startswith(f"{other}/")


async def test_an_uploaded_document_and_an_extract_coexist(docs_root: Path) -> None:
    # The end-to-end form of the property above: both writers' files survive
    # one turn that emits both.
    from src.harness.guidance.material import (
        MaterialEntry,
        MaterialKind,
        ProjectMaterial,
    )

    policy_text = "Every invoice exchange is reconciled before it is reported.\n"
    entry = MaterialEntry(
        document_id=41,
        filename="policy.pdf",
        role="policy",
        kind=MaterialKind.CONSTRAINT,
        char_count=len(policy_text),
        materialized=True,
    )
    memory = memory_for(docs_root, record_with("body"))

    await ensure_labs_workspace(
        conversation_id=CONVERSATION_ID,
        project_id=PROJECT,
        mcp_token=MCP_TOKEN,
        material=ProjectMaterial(project_id=PROJECT, entries=(entry,), cache_key="k"),
        material_texts={41: policy_text},
        memory=memory,
    )

    workspace = Path(labs_workspace_path(CONVERSATION_ID))
    assert (workspace / ".nexus/project/references/41-policy.md").is_file()
    assert (
        workspace / f".nexus/project/{MEMORY_DIRNAME}/open-questions.md"
    ).is_file()


# -- read-only ---------------------------------------------------------------


def test_no_extract_path_is_writable(docs_root: Path) -> None:
    # The containment property. `edit` is the only key that governs writes in
    # this workspace, its catch-all is deny, and the only allow patterns are
    # under `output/`. An extract of a record this process does not own must
    # not be reachable by any of them.
    workspace = Path(labs_workspace_path(CONVERSATION_ID))
    permission = _edit_permission(workspace)
    memory = memory_for(docs_root, record_with("body"))

    assert permission["*"] == "deny"
    allowed = [pattern for pattern, action in permission.items() if action == "allow"]
    assert allowed

    resolved = workspace.resolve()
    for document in memory.documents:  # type: ignore[union-attr]
        absolute = resolved / ".nexus" / _PROJECT_DIR / document.relpath
        # Every spelling OpenCode could present the path as, built the same way
        # `_output_write_patterns` builds its own: absolute, then relative to
        # each ancestor, because the worktree it resolves is not knowable here.
        spellings = [absolute.as_posix()]
        for ancestor in (resolved, *resolved.parents):
            spellings.append(absolute.relative_to(ancestor).as_posix())

        for spelling in spellings:
            for pattern in allowed:
                assert not fnmatch(spelling, pattern), (spelling, pattern)


def test_the_extract_tells_the_reader_not_to_write_back(docs_root: Path) -> None:
    # Belt to the policy's braces. The record's own preamble is an instruction
    # to APPEND; it is dropped, and replaced with the opposite.
    memory = memory_for(docs_root, record_with("body"))

    body = memory.documents[0].body  # type: ignore[union-attr]

    assert "HOW TO WRITE HERE" not in body
    assert "you must not try to append to it" in body
    assert "Read-only extract" in body
