"""Slice 4: reusable state has to prove it belongs to this run.

``output/``, ``notes/`` and ``notes/integration_model.json`` were preserved
across every build of a staging directory with nothing tying them to the run
they were about. A second run over the same directory inherited the first
run's drafts, the first run's notes and the first run's accumulated
integration model, then published them as its own -- and the receipt said
nothing, because nothing knew.

Two halves, and both have to hold or the fix is worse than the defect:

* an EXACT match preserves, because a restart's whole point is that finished
  work survives and discarding it would make every restart start over;
* anything else starts clean AND NAMES THE FIELD. "State discarded" sends an
  operator to diff two hashes; "the goal changed" is actionable.

The identity is deliberately a mirror of
``providers/coding_agent/models.py::InvestigationFingerprints``, down to
``differences()`` returning field names. A third spelling of "is this
checkpoint mine" is how two of them end up disagreeing.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from mcp_server.config import config
from mcp_server.providers.agent_workspace import contract, resume
from mcp_server.providers.agent_workspace.resume import ResumeIdentity
from mcp_server.providers.agent_workspace.workspace import build_workspace
from tests.test_agent_workspace.workspace_fixture import approval_tree, design, write_scan

_GOAL = "Document this estate."


def _identity(**overrides: str) -> ResumeIdentity:
    base = {
        "project_id": "PROJECT",
        "scan_digest": "sha256:aaa",
        "goal": _GOAL,
        "method_digest": "method-1",
        "knowledge_model_version": "integration-knowledge-v1",
    }
    base.update(overrides)
    return ResumeIdentity.build(**base)  # type: ignore[arg-type]


def _build(staging: Path, *, goal: str = _GOAL) -> object:
    return build_workspace(project_id="PROJECT", staging_dir=staging, platform="talend", goal=goal)


def _with_agent_work(staging: Path) -> None:
    (staging / contract.OUTPUT / "00_OVERVIEW.md").write_text("# Done\n", encoding="utf-8")
    (staging / contract.AGENT_PROGRESS).write_text("half way\n", encoding="utf-8")
    (staging / contract.INTEGRATION_MODEL).write_text(
        json.dumps({"schema_version": "integration-knowledge-v1", "integrations": ["mine"]}),
        encoding="utf-8",
    )


def _tamper_stored_identity(staging: Path, field: str) -> None:
    """Change ONE field of the persisted identity, leaving the rest alone.

    A real mismatch reaches here the same way -- a different goal, a rescan,
    an edited instruction file -- and this isolates the field without needing
    to reproduce whatever upstream change would have moved it.
    """

    path = staging / contract.RESUME_IDENTITY
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored[field] = "0" * 64
    path.write_text(json.dumps(stored), encoding="utf-8")


# --- the value object --------------------------------------------------------


class TestResumeIdentity:
    def test_identical_inputs_produce_an_identical_identity(self) -> None:
        assert _identity() == _identity()
        assert _identity().differences(_identity()) == []

    @pytest.mark.parametrize(
        ("argument", "value", "field"),
        [
            ("project_id", "OTHER", "project"),
            ("scan_digest", "sha256:bbb", "scan"),
            ("goal", "Document only the invoicing chain.", "goal"),
            ("method_digest", "method-2", "method"),
            ("knowledge_model_version", "integration-knowledge-v2", "knowledge_model"),
        ],
    )
    def test_each_input_moves_exactly_its_own_field(
        self, argument: str, value: str, field: str
    ) -> None:
        assert _identity().differences(_identity(**{argument: value})) == [field]

    def test_the_contract_version_is_a_field_of_its_own(self) -> None:
        other = ResumeIdentity.build(
            project_id="PROJECT",
            scan_digest="sha256:aaa",
            goal=_GOAL,
            method_digest="method-1",
            contract_version="agent-workspace-v2",
            knowledge_model_version="integration-knowledge-v1",
        )
        assert _identity().differences(other) == ["contract"]

    def test_the_goal_is_whitespace_normalised(self) -> None:
        """Reflowing a prompt is not a new run."""

        assert _identity(goal="  Document   this\nestate. ") == _identity()

    def test_it_is_frozen_and_rejects_unknown_fields(self) -> None:
        identity = _identity()
        with pytest.raises(Exception):
            identity.project = "x"  # type: ignore[misc]
        with pytest.raises(Exception):
            ResumeIdentity(**{**identity.model_dump(), "extra": "x"})

    def test_it_stores_digests_and_never_the_goal_itself(self, tmp_path: Path) -> None:
        """A goal is client prose and the method is ~120 KB. Neither is state."""

        path = tmp_path / "identity.json"
        _identity(goal="Document the ORDERS chain for ACME").save(path)
        body = path.read_text(encoding="utf-8")
        assert "ACME" not in body
        assert "ORDERS" not in body

    @pytest.mark.parametrize("body", ["", "{", '{"project": "nope"}', "[]"])
    def test_an_unusable_stored_identity_reads_as_absent(self, tmp_path: Path, body: str) -> None:
        """Corrupt, truncated and missing are one answer: it proves nothing."""

        path = tmp_path / "identity.json"
        path.write_text(body, encoding="utf-8")
        assert ResumeIdentity.load(path) is None

    def test_it_mirrors_the_predecessor_lane_s_fingerprints(self) -> None:
        from mcp_server.providers.coding_agent.models import InvestigationFingerprints

        assert hasattr(InvestigationFingerprints, "differences")
        assert hasattr(ResumeIdentity, "differences")
        assert InvestigationFingerprints.model_config["frozen"] is True
        assert ResumeIdentity.model_config["frozen"] is True


# --- the decision, on a real build -------------------------------------------


class TestReconciliationOnABuild:
    def test_an_exact_match_preserves_drafts_notes_and_the_model(
        self, workspace: Path, scan_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config, "runtime_evidence_enabled", False)
        _with_agent_work(workspace)
        receipt = _build(workspace, goal="Document it.")
        assert (workspace / contract.OUTPUT / "00_OVERVIEW.md").read_text() == "# Done\n"
        assert (workspace / contract.AGENT_PROGRESS).read_text() == "half way\n"
        assert "mine" in (workspace / contract.INTEGRATION_MODEL).read_text()
        assert not [notice for notice in receipt.notices if "discarded" in notice]

    def test_a_changed_goal_starts_clean_and_says_so(
        self, workspace: Path, scan_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config, "runtime_evidence_enabled", False)
        _with_agent_work(workspace)
        receipt = _build(workspace, goal="Document only the invoicing chain.")
        assert not (workspace / contract.OUTPUT / "00_OVERVIEW.md").exists()
        assert not (workspace / contract.AGENT_PROGRESS).exists()
        assert "mine" not in (workspace / contract.INTEGRATION_MODEL).read_text()
        assert [notice for notice in receipt.notices if "the goal changed" in notice]

    def test_a_rescan_starts_clean_and_says_so(
        self, workspace: Path, scan_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Drafts written against last week's estate are not evidence about this one."""

        monkeypatch.setattr(config, "runtime_evidence_enabled", False)
        _with_agent_work(workspace)

        scan_dir = scan_root / "PROJECT"
        contexts = scan_dir / "scan" / "contexts.json"
        contexts.write_text('[{"name": "acceptance"}]', encoding="utf-8")
        approval = json.loads((scan_dir / "publication_approval.json").read_text())
        approval["tree"] = approval_tree(scan_dir)
        (scan_dir / "publication_approval.json").write_text(json.dumps(approval, indent=2))

        receipt = _build(workspace, goal="Document it.")
        assert not (workspace / contract.OUTPUT / "00_OVERVIEW.md").exists()
        assert [notice for notice in receipt.notices if "the approved scan changed" in notice]

    @pytest.mark.parametrize(
        ("field", "phrase"),
        [
            ("method", "the instructions and investigation method"),
            ("contract", "the workspace contract version"),
            ("knowledge_model", "the integration-model schema"),
            ("project", "the project"),
        ],
    )
    def test_every_other_field_also_starts_clean_and_names_itself(
        self,
        workspace: Path,
        scan_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        field: str,
        phrase: str,
    ) -> None:
        monkeypatch.setattr(config, "runtime_evidence_enabled", False)
        _with_agent_work(workspace)
        _tamper_stored_identity(workspace, field)
        receipt = _build(workspace, goal="Document it.")
        assert not (workspace / contract.OUTPUT / "00_OVERVIEW.md").exists()
        assert [notice for notice in receipt.notices if phrase in notice], receipt.notices

    def test_state_with_no_identity_at_all_is_discarded(
        self, workspace: Path, scan_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The state every pre-slice-4 staging directory is in."""

        monkeypatch.setattr(config, "runtime_evidence_enabled", False)
        _with_agent_work(workspace)
        (workspace / contract.RESUME_IDENTITY).unlink()
        receipt = _build(workspace, goal="Document it.")
        assert not (workspace / contract.OUTPUT / "00_OVERVIEW.md").exists()
        assert [
            notice
            for notice in receipt.notices
            if "no resume identity records which run produced it" in notice
        ]

    def test_a_first_build_announces_no_discard(
        self, scan_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing was thrown away, and saying so is noise in a read receipt."""

        monkeypatch.setattr(config, "runtime_evidence_enabled", False)
        receipt = _build(tmp_path / "fresh")
        assert not [notice for notice in receipt.notices if "discarded" in notice]

    def test_the_identity_is_written_where_the_contract_says(self, workspace: Path) -> None:
        stored = json.loads((workspace / contract.RESUME_IDENTITY).read_text())
        assert sorted(stored) == [
            "contract",
            "goal",
            "knowledge_model",
            "method",
            "project",
            "scan",
        ]
        assert contract.RESUME_IDENTITY.parts[0] == contract.CONTROL.name

    def test_the_identity_is_outside_the_evidence_digest(self, workspace: Path) -> None:
        """It changes with the run; the evidence digest must not.

        ``publish`` re-hashes the evidence after the agent has run and compares
        it with the builder's receipt. Folding Nexus's own control state into
        that baseline would make every run's digest unique for reasons that
        have nothing to do with tampering.
        """

        from mcp_server.providers.agent_workspace.workspace import evidence_digest

        before = evidence_digest(workspace)
        (workspace / contract.RESUME_IDENTITY).write_text("{}", encoding="utf-8")
        assert evidence_digest(workspace) == before

    def test_a_symlinked_output_directory_is_removed_rather_than_followed(
        self, workspace: Path, scan_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Clearing must not delete somebody else's tree through a link."""

        import os
        import shutil

        monkeypatch.setattr(config, "runtime_evidence_enabled", False)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "keep.md").write_text("# Keep\n", encoding="utf-8")
        shutil.rmtree(workspace / contract.OUTPUT)
        os.symlink(elsewhere, workspace / contract.OUTPUT)

        _build(workspace, goal="A different goal entirely.")
        assert (elsewhere / "keep.md").is_file()
        assert (workspace / contract.OUTPUT).is_dir()
        assert not (workspace / contract.OUTPUT).is_symlink()


# --- where the check may and may not run -------------------------------------


_LANE = Path(__file__).resolve().parents[2] / "src" / "mcp_server" / "providers" / "agent_workspace"


def test_only_the_workspace_builder_reconciles_the_identity() -> None:
    """An internal restart shares the workspace ON PURPOSE.

    The runner composes a fresh session over the same directory and must find
    the previous attempt's output where it left it. Running this check per
    attempt would discard exactly the work a restart exists to keep, so the
    call site is pinned: once, inside ``build_workspace``'s helper, and
    nowhere in the runner.
    """

    callers = [
        path.name
        for path in sorted(_LANE.glob("*.py"))
        if path.name not in {"resume.py"}
        and "resume.reconcile(" in path.read_text(encoding="utf-8")
    ]
    assert callers == ["workspace.py"]

    source = (_LANE / "workspace.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    call_sites = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_apply_resume_identity"
    ]
    assert len(call_sites) == 1

    build = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "build_workspace"
    )
    assert any(node is call_sites[0] for node in ast.walk(build))

    assert "resume" not in (_LANE / "runner.py").read_text(encoding="utf-8").split("\n\n")[0]


def test_the_reference_shelf_is_compiled_after_the_clear(tmp_path: Path) -> None:
    """Order, not coincidence: the shelf lives in ``output/``.

    ``registers.compile_reference_registers`` writes ``output/reference/``, so
    a clear that ran after it would delete the shelf the receipt has just
    counted. The assertion is on the source order because the failure is
    silent -- the run publishes with an empty register directory.
    """

    source = (_LANE / "workspace.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    build = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "build_workspace"
    )
    resume_line = next(
        node.lineno
        for node in ast.walk(build)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_apply_resume_identity"
    )
    compile_line = next(
        node.lineno
        for node in ast.walk(build)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "compile_reference_registers"
    )
    assert resume_line < compile_line


def test_the_schema_version_is_read_off_the_model_class() -> None:
    from mcp_server.providers.agent_workspace.integration_model import (
        IntegrationKnowledgeBase,
    )

    assert resume.integration_model_schema_version() == IntegrationKnowledgeBase().schema_version


def test_a_rebuilt_reference_shelf_is_not_mistaken_for_agent_work(
    scan_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``output/reference/`` is the compiler's, on every build and publication.

    Counting it as reusable state would make a first build report that it had
    discarded work nobody did.
    """

    monkeypatch.setattr(config, "runtime_evidence_enabled", False)
    staging = tmp_path / "twice"
    _build(staging)
    assert (staging / contract.OUTPUT / "reference").is_dir()
    (staging / contract.RESUME_IDENTITY).unlink()
    receipt = _build(staging)
    assert not [notice for notice in receipt.notices if "discarded" in notice]


def test_a_scan_change_is_detected_even_when_no_filename_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The digest is over paths AND hashes, so an edit in place still moves it."""

    root = tmp_path / "intodoc-output"
    root.mkdir()
    scan_dir = write_scan(root, designs=[design("A", group="g")])
    monkeypatch.setattr(config, "intodoc_output_dir", root)
    monkeypatch.setattr(config, "runtime_evidence_enabled", False)

    staging = tmp_path / "staging"
    _build(staging)
    first = json.loads((staging / contract.RESUME_IDENTITY).read_text())["scan"]

    (scan_dir / "scan" / "contexts.json").write_text('[{"name": "acc"}]', encoding="utf-8")
    approval = json.loads((scan_dir / "publication_approval.json").read_text())
    approval["tree"] = approval_tree(scan_dir)
    (scan_dir / "publication_approval.json").write_text(json.dumps(approval, indent=2))

    _build(staging)
    second = json.loads((staging / contract.RESUME_IDENTITY).read_text())["scan"]
    assert first != second
