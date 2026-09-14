"""The workspace contract, pinned.

Every assertion here is a property whose failure is silent at build time and
expensive at model-call time:

* a scan that is not provably redacted must never reach an agent, and the
  refusal must happen before a single byte is staged;
* the approval must bind the bytes the agent actually receives. Hashing four
  aggregates left every per-design shard free to change after the redaction
  verification that makes the approval mean anything;
* no source of any kind -- not as a file, and not embedded in parsed JSON,
  which is the hole a filename rule cannot see;
* nothing over the readability ceiling, no leading underscore -- the shape
  rules the agent's whole experience rests on;
* ``runtime/`` must STATE its absence. An empty directory reads as "this
  estate never runs", which is the Steven failure in a new place;
* ``facts/parser_limitations.md`` must name what could not be read, because a
  document that cannot see the gap denies it instead of disclosing it;
* ``AGENT_GUIDE.md`` must stay a map -- short, and enumerating no files.

The fixture is synthetic and tiny. The real 142-design estate is gitignored
client material and is exercised by
``scripts/build_agent_workspace.py`` as a manual dry run, not from here.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from mcp_server.config import config
from mcp_server.providers.agent_workspace.contract import (
    AGENT_GUIDE,
    CODE_PARAM_NAMES,
    CONTROL,
    GOAL,
    INDEX,
    INSTRUCTION_FILES,
    NAVIGATION_VIEWS,
    VERBATIM_CODE_FIELDS,
    WorkspaceUnavailableError,
)
from mcp_server.providers.agent_workspace.projection import WITHHELD_KEY
from mcp_server.providers.agent_workspace.workspace import (
    MAX_STAGED_FILE_BYTES,
    build_workspace,
    verify_scan_is_publishable,
)
from tests.test_agent_workspace.workspace_fixture import (
    approval_tree,
    design,
    write_scan,
)

#: A Talend custom-code body of the shape this lane must never hand over.
_CODE = (
    "String host = context.erp_host;\n"
    'globalMap.put("rowCount", count);\n'
    "return routines.Vault.lookup(host);\n"
)


def _reapprove(scan_dir: Path) -> None:
    """Re-state the artifact tree after a test edits the scan on purpose."""

    approval = json.loads((scan_dir / "publication_approval.json").read_text())
    approval["tree"] = approval_tree(scan_dir)
    (scan_dir / "publication_approval.json").write_text(json.dumps(approval, indent=2))


# --- the sanitisation gate ---------------------------------------------------


class TestSanitisationGate:
    def test_an_unapproved_scan_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "out"
        root.mkdir()
        scan_dir = write_scan(root, designs=[design("A", group="g")], approve=False)
        with pytest.raises(WorkspaceUnavailableError, match="does not approve"):
            verify_scan_is_publishable(scan_dir)

    def test_a_missing_approval_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "out"
        root.mkdir()
        scan_dir = write_scan(root, designs=[design("A", group="g")])
        (scan_dir / "publication_approval.json").unlink()
        with pytest.raises(WorkspaceUnavailableError, match="publication_approval.json"):
            verify_scan_is_publishable(scan_dir)

    def test_approval_must_bind_every_final_scan_artifact(self, tmp_path: Path) -> None:
        root = tmp_path / "out"
        root.mkdir()
        scan_dir = write_scan(root, designs=[design("A", group="g")])
        approval = json.loads((scan_dir / "publication_approval.json").read_text())
        approval["artifacts"].pop("scan_result.json")
        (scan_dir / "publication_approval.json").write_text(json.dumps(approval))

        with pytest.raises(WorkspaceUnavailableError, match="scan_result.json"):
            verify_scan_is_publishable(scan_dir)

    def test_approval_final_artifacts_cannot_escape_the_scan(self, tmp_path: Path) -> None:
        root = tmp_path / "out"
        root.mkdir()
        scan_dir = write_scan(root, designs=[design("A", group="g")])
        approval = json.loads((scan_dir / "publication_approval.json").read_text())
        approval["artifacts"]["../../outside.json"] = "sha256:0"
        (scan_dir / "publication_approval.json").write_text(json.dumps(approval))

        with pytest.raises(WorkspaceUnavailableError, match="unsafe final artifact"):
            verify_scan_is_publishable(scan_dir)

    def test_a_scan_modified_after_approval_is_refused(self, tmp_path: Path) -> None:
        """The digest is the whole point: approved bytes, not an approved run."""
        root = tmp_path / "out"
        root.mkdir()
        scan_dir = write_scan(root, designs=[design("A", group="g")])
        landscape = scan_dir / "scan" / "landscape.json"
        landscape.write_text(json.dumps({"systems": [{"name": "injected"}]}), encoding="utf-8")
        with pytest.raises(WorkspaceUnavailableError, match="approved digest"):
            verify_scan_is_publishable(scan_dir)

    def test_allowed_redaction_failures_are_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "out"
        root.mkdir()
        scan_dir = write_scan(root, designs=[design("A", group="g")])
        approval = json.loads((scan_dir / "publication_approval.json").read_text())
        approval["allow_redaction_failures"] = True
        (scan_dir / "publication_approval.json").write_text(json.dumps(approval))
        with pytest.raises(WorkspaceUnavailableError, match="redaction failures"):
            verify_scan_is_publishable(scan_dir)

    def test_a_failed_redaction_verification_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "out"
        root.mkdir()
        scan_dir = write_scan(root, designs=[design("A", group="g")])
        (scan_dir / "redaction_report.json").write_text(
            json.dumps({"verification": {"passed": False}}), encoding="utf-8"
        )
        with pytest.raises(WorkspaceUnavailableError, match="failed redaction"):
            verify_scan_is_publishable(scan_dir)

    def test_nothing_is_written_when_the_gate_refuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "out"
        root.mkdir()
        write_scan(root, designs=[design("A", group="g")], approve=False)
        monkeypatch.setattr(config, "intodoc_output_dir", root)
        target = tmp_path / "staging"
        with pytest.raises(WorkspaceUnavailableError):
            build_workspace(project_id="PROJECT", staging_dir=target, platform="talend", goal="x")
        assert not target.exists()

    def test_an_unscanned_project_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "out")
        with pytest.raises(WorkspaceUnavailableError, match="no scan output"):
            build_workspace(
                project_id="NOPE", staging_dir=tmp_path / "s", platform="talend", goal="x"
            )


# --- the approval binds every artifact the workspace stages ------------------


class TestApprovalTree:
    """The four-artifact map was not a binding on the bytes the agent gets.

    ``artifacts`` hashes ``scan_result.json``, ``scan/manifest.json``,
    ``scan/lineage.json`` and ``scan/landscape.json``. The workspace stages
    none of those four directly -- it stages each
    ``scan/jobs/<group>/<design>.json`` shard and the estate collections, so a
    shard could be edited after approval and the redaction verification that
    makes the approval meaningful had run against different bytes.
    """

    @staticmethod
    def _scan(root: Path) -> Path:
        root.mkdir()
        return write_scan(root, designs=[design("A", group="g"), design("B", group="g")])

    def test_a_receipt_without_a_tree_is_refused_rather_than_degraded(self, tmp_path: Path) -> None:
        """Fail-closed: "older scan" and "the binding was removed" are one file."""

        scan_dir = self._scan(tmp_path / "out")
        approval = json.loads((scan_dir / "publication_approval.json").read_text())
        approval.pop("tree")
        (scan_dir / "publication_approval.json").write_text(json.dumps(approval))
        with pytest.raises(WorkspaceUnavailableError, match="Rescan"):
            verify_scan_is_publishable(scan_dir)

    def test_a_mutated_job_shard_is_refused(self, tmp_path: Path) -> None:
        scan_dir = self._scan(tmp_path / "out")
        shard = scan_dir / "scan" / "jobs" / "g" / "A.json"
        payload = json.loads(shard.read_text())
        payload["purpose"] = "injected after approval"
        shard.write_text(json.dumps(payload))
        with pytest.raises(WorkspaceUnavailableError, match="scan/jobs/g/A.json"):
            verify_scan_is_publishable(scan_dir)

    def test_a_mutated_estate_collection_is_refused(self, tmp_path: Path) -> None:
        scan_dir = self._scan(tmp_path / "out")
        (scan_dir / "scan" / "contexts.json").write_text('[{"name": "injected"}]')
        with pytest.raises(WorkspaceUnavailableError, match="contexts.json"):
            verify_scan_is_publishable(scan_dir)

    def test_an_unapproved_file_the_builder_would_STAGE_is_refused(self, tmp_path: Path) -> None:
        """The real smuggling route: an optional collection absent at scan time.

        ``scan/xslt.json`` is in the staged set but is legitimately missing on
        an estate with no stylesheets. Planting one after approval puts bytes
        that no redaction verification ever saw straight into the workspace,
        and it is the only "added file" shape that can actually reach an agent.
        """

        root = tmp_path / "out"
        root.mkdir()
        scan_dir = write_scan(root, designs=[design("A", group="g")], omit=("scan/xslt.json",))
        (scan_dir / "scan" / "xslt.json").write_text('[{"name": "injected"}]')
        with pytest.raises(WorkspaceUnavailableError, match="not approved"):
            verify_scan_is_publishable(scan_dir)

    def test_an_unapproved_file_nothing_can_stage_is_a_notice_not_a_refusal(
        self, tmp_path: Path
    ) -> None:
        """``scan/`` is the scanner's directory, but not exclusively.

        ``services/summary_service.py`` writes ``scan/summaries.json`` there as
        a server-side cache, after approval and repeatedly. Treating every
        unapproved file as fatal broke EVERY project on the first real run --
        verified on ACME: 157 files on disk against 156 in the tree, the
        difference being exactly that cache.

        Reachability is what matters, and neither of these is reachable: a
        design shard is only ever opened via the digest-bound
        ``manifest.json::job_files`` index, and a collection only under a fixed
        filename. Both are recorded so an operator still sees them.
        """

        scan_dir = self._scan(tmp_path / "out")
        (scan_dir / "scan" / "summaries.json").write_text('{"cached": true}')
        (scan_dir / "scan" / "jobs" / "g" / "Smuggled.json").write_text("{}")

        notices: list[str] = []
        verify_scan_is_publishable(scan_dir, notices)

        assert notices, "an unapproved file must never pass silently"
        assert "2 file(s)" in notices[0]
        assert "not staged" in notices[0]

    def test_a_file_removed_after_approval_is_refused(self, tmp_path: Path) -> None:
        scan_dir = self._scan(tmp_path / "out")
        (scan_dir / "scan" / "jobs" / "g" / "B.json").unlink()
        with pytest.raises(WorkspaceUnavailableError, match="approved but missing"):
            verify_scan_is_publishable(scan_dir)

    def test_an_edited_receipt_fails_its_own_digest(self, tmp_path: Path) -> None:
        """Rewriting a hash in the receipt to match a tampered file is not enough."""

        scan_dir = self._scan(tmp_path / "out")
        shard = scan_dir / "scan" / "jobs" / "g" / "A.json"
        shard.write_text('{"name": "A", "purpose": "injected"}')
        approval = json.loads((scan_dir / "publication_approval.json").read_text())
        approval["tree"]["files"]["scan/jobs/g/A.json"] = (
            "sha256:" + hashlib.sha256(shard.read_bytes()).hexdigest()
        )
        (scan_dir / "publication_approval.json").write_text(json.dumps(approval))
        with pytest.raises(WorkspaceUnavailableError, match="own tree digest"):
            verify_scan_is_publishable(scan_dir)

    def test_a_path_escaping_the_scan_directory_is_refused(self, tmp_path: Path) -> None:
        """The receipt is a file on disk; every field in it is hostile-capable."""

        scan_dir = self._scan(tmp_path / "out")
        approval = json.loads((scan_dir / "publication_approval.json").read_text())
        approval["tree"]["files"]["../../etc/hosts"] = "sha256:0"
        approval["tree"]["file_count"] += 1
        (scan_dir / "publication_approval.json").write_text(json.dumps(approval))
        with pytest.raises(WorkspaceUnavailableError, match="outside the scan directory"):
            verify_scan_is_publishable(scan_dir)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("roots", ["scan/jobs"]),
            ("roots", ["scan", "../../etc"]),
            ("excluded_prefixes", []),
            ("excluded_prefixes", ["scan/jobs/"]),
        ],
    )
    def test_the_membership_rule_comes_from_this_builder_not_the_receipt(
        self, tmp_path: Path, field: str, value: list
    ) -> None:
        """The second reproduced bypass, and it did not need a symlink.

        ``roots`` and ``excluded_prefixes`` used to be READ from the receipt
        and then used to walk the disk. Narrowing ``roots`` to
        ``["scan/jobs"]`` therefore put every estate-level collection outside
        the walk: unbound, never compared, never re-hashed -- and still staged
        by ``_stage_scan``, which knows those filenames. Reproduced with a
        leaked password staged verbatim. A receipt claiming a rule this
        builder does not enforce is now refused rather than obeyed.
        """

        scan_dir = self._scan(tmp_path / "out")
        approval = json.loads((scan_dir / "publication_approval.json").read_text())
        approval["tree"][field] = value
        (scan_dir / "publication_approval.json").write_text(json.dumps(approval))
        with pytest.raises(WorkspaceUnavailableError, match="membership rule"):
            verify_scan_is_publishable(scan_dir)

    def test_an_unrecognised_algorithm_is_refused_not_skipped(self, tmp_path: Path) -> None:
        scan_dir = self._scan(tmp_path / "out")
        approval = json.loads((scan_dir / "publication_approval.json").read_text())
        approval["tree"]["algorithm"] = "something-else-v9"
        (scan_dir / "publication_approval.json").write_text(json.dumps(approval))
        with pytest.raises(WorkspaceUnavailableError, match="cannot recompute"):
            verify_scan_is_publishable(scan_dir)

    def test_the_excluded_roots_are_not_bound_and_do_not_refuse(self, tmp_path: Path) -> None:
        """`scan/sanitized/` is the source mirror; it is never agent-eligible.

        Binding it would double the hashing cost of every scan and every build
        for bytes that may not be staged under any circumstance.
        """

        scan_dir = self._scan(tmp_path / "out")
        (scan_dir / "scan" / "sanitized").mkdir()
        (scan_dir / "scan" / "sanitized" / "Orders.item").write_text("<xml/>")
        verify_scan_is_publishable(scan_dir)

    def test_a_mutated_shard_stops_the_build_before_anything_is_staged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scan_dir = self._scan(tmp_path / "out")
        (scan_dir / "scan" / "jobs" / "g" / "A.json").write_text('{"name": "A"}')
        monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "out")
        target = tmp_path / "staging"
        with pytest.raises(WorkspaceUnavailableError, match="modified after approval"):
            build_workspace(project_id="PROJECT", staging_dir=target, platform="talend", goal="x")
        assert not target.exists()


# --- the shape of the tree ---------------------------------------------------


class TestWorkspaceShape:
    def test_the_roots_are_exactly_the_contract(self, workspace: Path) -> None:
        assert {path.name for path in workspace.iterdir()} == {
            AGENT_GUIDE.name,
            GOAL.name,
            INDEX.name,
            "instructions",
            "scan",
            "facts",
            "index",
            "runtime",
            "notes",
            "output",
            # Nexus's own control state, never the agent's. Deliberately
            # underscore-prefixed (`contract.CONTROL`) so publication cannot
            # ship it by forgetting it in a cleanup list.
            CONTROL.name,
        }

    def test_no_talend_source_is_staged(self, workspace: Path) -> None:
        """The scan is the evidence. A `.item` here is the lane's core failure."""
        assert not list(workspace.rglob("*.item"))
        assert not list(workspace.rglob("*.properties"))

    def test_nothing_the_agent_reads_starts_with_an_underscore(self, workspace: Path) -> None:
        """The rule is about what the AGENT sees, not about the directory.

        An underscore means "internal, never shown to a client", which is a
        distinction the agent has no way to make -- so no evidence, method or
        deliverable path may carry one. ``_agent_control/`` is the deliberate
        exception and the reason the prefix exists at all: ``publish``'s
        visibility check rejects such a segment, so Nexus's control state
        cannot reach a client document by being forgotten somewhere.
        """

        offenders = [
            path.relative_to(workspace).as_posix()
            for path in workspace.rglob("_*")
            if not path.is_relative_to(workspace / CONTROL)
        ]
        assert offenders == []

    def test_no_staged_file_exceeds_the_readability_ceiling(self, workspace: Path) -> None:
        oversize = [
            (path.relative_to(workspace).as_posix(), path.stat().st_size)
            for path in workspace.rglob("*")
            if path.is_file() and path.stat().st_size > MAX_STAGED_FILE_BYTES
        ]
        assert oversize == []

    def test_the_monolithic_scan_and_the_fact_graph_are_not_staged(self, workspace: Path) -> None:
        assert not list(workspace.rglob("scan_result.json"))
        assert not list(workspace.rglob("fact_graph.json"))
        # `scan/sanitized/` is the source-tree mirror by another name.
        assert not (workspace / "scan" / "sanitized").exists()

    def test_designs_are_sharded_under_their_group(self, workspace: Path) -> None:
        groups = sorted(path.name for path in (workspace / "scan" / "jobs").iterdir())
        assert groups == ["invoices", "orders"]
        assert sorted(path.name for path in (workspace / "scan" / "jobs" / "orders").iterdir()) == [
            "OrdersLoad.json",
            "OrdersStage.json",
        ]

    def test_the_receipt_counts_what_landed(
        self, scan_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config, "runtime_evidence_enabled", False)
        receipt = build_workspace(
            project_id="PROJECT",
            staging_dir=tmp_path / "s2",
            platform="talend",
            goal="Document it.",
        )
        assert receipt.design_count == 3
        assert receipt.group_count == 2
        assert receipt.mapping_table_count == 1
        assert receipt.instruction_files == INSTRUCTION_FILES
        assert receipt.platform_guides
        assert set(receipt.navigation_rows) == set(NAVIGATION_VIEWS)
        assert receipt.navigation_rows["units"] == 3
        assert receipt.content_digest

    def test_two_builds_of_one_scan_are_byte_stable(
        self, scan_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A rebuild that churns the tree invalidates every provider prefix cache."""
        monkeypatch.setattr(config, "runtime_evidence_enabled", False)
        first = build_workspace(
            project_id="PROJECT", staging_dir=tmp_path / "a", platform="talend", goal="g"
        )
        second = build_workspace(
            project_id="PROJECT", staging_dir=tmp_path / "b", platform="talend", goal="g"
        )
        assert first.content_digest == second.content_digest

    def test_a_rebuild_preserves_the_agent_s_own_directories(
        self, workspace: Path, scan_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A restart's whole point is that finished work in `output/` survives.

        The goal is the SAME one the fixture built with, and that is now part
        of the assertion rather than an accident of the fixture: preservation
        is conditional on the resume identity matching, and this test used to
        rebuild with a different goal and still expect the drafts to survive.
        ``test_resume.py`` holds the mismatch half.
        """
        monkeypatch.setattr(config, "runtime_evidence_enabled", False)
        (workspace / "output" / "done.md").write_text("# Done\n", encoding="utf-8")
        (workspace / "notes" / "progress.md").write_text("half way\n", encoding="utf-8")
        build_workspace(
            project_id="PROJECT", staging_dir=workspace, platform="talend", goal="Document it."
        )
        assert (workspace / "output" / "done.md").read_text() == "# Done\n"
        assert (workspace / "notes" / "progress.md").read_text() == "half way\n"

    def test_a_non_ascii_name_survives_staging_unescaped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every shard is now reserialized rather than copied.

        JSON's default `ensure_ascii=True` would turn a real design name into
        `R\\u00e9cup...` in the staged bytes, so a grep for the name the agent
        read in `INDEX.md` would find nothing. The scanner writes raw UTF-8;
        so does this.
        """

        root = tmp_path / "intodoc-output"
        root.mkdir()
        accented = design("Recuperation", group="gestion")
        accented["name"] = "Récupération"
        write_scan(root, designs=[accented])
        monkeypatch.setattr(config, "intodoc_output_dir", root)
        monkeypatch.setattr(config, "runtime_evidence_enabled", False)
        target = tmp_path / "accented"
        build_workspace(project_id="PROJECT", staging_dir=target, platform="talend", goal="g")
        staged = list((target / "scan" / "jobs" / "gestion").glob("*.json"))
        assert len(staged) == 1, staged
        shard = staged[0].read_text(encoding="utf-8")
        assert "Récupération" in shard
        assert "\\u00e9" not in shard

    def test_an_oversize_collection_is_split_with_every_row_kept(
        self, scan_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The splitter writes from the payload it staged, not from a re-read.

        Reading back what was just written made "what was staged" and "what
        was split" two independently-derived answers to one question. The
        property that matters is unchanged and is what this asserts: the index
        replaces the file, the parts hold every row, and the rows are in
        order.
        """

        from mcp_server.providers.agent_workspace import workspace as workspace_module
        from mcp_server.providers.agent_workspace.staged_data import read_complete_list

        scan_dir = scan_root / "PROJECT"
        rows = [{"name": f"ctx_{index:04d}", "value": "x" * 40} for index in range(400)]
        (scan_dir / "scan" / "contexts.json").write_text(json.dumps(rows), encoding="utf-8")
        _reapprove(scan_dir)

        monkeypatch.setattr(config, "runtime_evidence_enabled", False)
        monkeypatch.setattr(workspace_module, "MAX_STAGED_FILE_BYTES", 4_000)
        receipt = build_workspace(
            project_id="PROJECT", staging_dir=tmp_path / "split", platform="talend", goal="g"
        )

        staged = tmp_path / "split" / "scan" / "contexts.json"
        index = json.loads(staged.read_text())
        assert index["split"] is True
        assert index["row_count"] == 400
        assert len(index["parts"]) > 1
        assert read_complete_list(staged) == rows
        assert any("was split into" in notice for notice in receipt.notices)

    def test_a_stale_evidence_file_does_not_survive_a_rebuild(
        self, workspace: Path, scan_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config, "runtime_evidence_enabled", False)
        stale = workspace / "scan" / "jobs" / "orders" / "DeletedDesign.json"
        stale.write_text("{}", encoding="utf-8")
        build_workspace(project_id="PROJECT", staging_dir=workspace, platform="talend", goal="g")
        assert not stale.exists()


# --- facts/ ------------------------------------------------------------------


class TestFacts:
    def test_mapping_tables_are_named_for_their_design_and_mapper(self, workspace: Path) -> None:
        assert [path.name for path in (workspace / "facts" / "mappings").iterdir()] == [
            "OrdersLoad__tMap_1.md"
        ]

    def test_the_fact_tables_point_at_files_that_exist_here(self, workspace: Path) -> None:
        """`build_exact_tables` writes `_workspace/...` paths for a tree we do not stage."""
        units = json.loads((workspace / "facts" / "units.json").read_text())["units"]
        for key, unit in units.items():
            assert "workspace_path" not in unit
            assert (workspace / unit["scan_path"]).is_file(), key

    def test_parser_limitations_names_what_could_not_be_read(self, workspace: Path) -> None:
        body = (workspace / "facts" / "parser_limitations.md").read_text()
        # The unparsed directory, the indexed-but-not-extracted file, and a
        # construct the scanner cannot resolve statically.
        assert "PROJ/resources" in body
        assert "xslt" in body
        assert "schema.dynamic" in body
        # And it says what that means, so a document discloses instead of denying.
        assert "not a statement about" in body


# --- runtime/ ----------------------------------------------------------------


class TestRuntime:
    def test_absence_is_stated_not_implied(self, workspace: Path) -> None:
        """An empty directory reads as "this estate never runs". It must not be one."""
        profile = (workspace / "runtime" / "profile.md").read_text()
        assert profile.strip()
        assert "Status:" in profile
        coverage = json.loads((workspace / "runtime" / "coverage.json").read_text())
        assert coverage["mode"] in {"data", "opted_out", "unavailable"}
        evidence = json.loads((workspace / "runtime" / "evidence.json").read_text())
        assert evidence["mode"] == coverage["mode"]

    def test_complete_runtime_evidence_is_not_limited_by_the_summary(
        self,
        scan_root: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from mcp_server.providers.agent_workspace import workspace as workspace_module
        from mcp_server.providers.custom.runtime_evidence import RuntimeEvidenceStore

        failures = [
            {
                "failed_at": f"2026-01-{(index % 28) + 1:02d}T00:00:00Z",
                "error_summary": f"redacted failure pattern {index}",
                "occurrences": index + 1,
            }
            for index in range(150)
        ]
        store = RuntimeEvidenceStore.from_json(
            {
                "window_days": 90,
                "mode": "data",
                "requested_units": 1,
                "by_unit": {
                    "A": {
                        "unit_name": "A",
                        "job_name": "A",
                        "total_runs": 200,
                        "success_runs": 50,
                        "failed_runs": 150,
                        "recent_failures": failures,
                    }
                },
            }
        )
        monkeypatch.setattr(workspace_module, "_fetch_runtime", lambda *_args: store)
        monkeypatch.setattr(config, "phase3_runtime_profile_bytes", 1_024)

        root = tmp_path / "runtime-complete"
        build_workspace(project_id="PROJECT", staging_dir=root, platform="talend", goal="g")

        evidence = json.loads((root / "runtime" / "evidence.json").read_text())
        assert evidence["by_unit"]["A"]["total_runs"] == 200
        assert len(evidence["by_unit"]["A"]["recent_failures"]) == 150

    def test_the_receipt_says_runtime_was_unavailable(
        self, scan_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config, "runtime_evidence_enabled", False)
        receipt = build_workspace(
            project_id="PROJECT", staging_dir=tmp_path / "s3", platform="talend", goal="g"
        )
        assert receipt.runtime_available is False
        assert any("runtime evidence" in notice for notice in receipt.notices)


# --- the source boundary, on the bytes that landed ---------------------------


@pytest.fixture
def coded_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A built workspace over a scan that carries real custom code.

    The shared fixture's designs are code-free, which is the wrong shape for
    this boundary: a projection that removed nothing would pass every
    assertion against it.
    """

    root = tmp_path / "intodoc-output"
    root.mkdir()
    load = design("OrdersLoad", group="orders")
    load["components"].append(
        {
            "unique_name": "tJava_1",
            "component_type": "tJava",
            "active": True,
            "declaration": "built_in",
            "code": _CODE,
            "params": [
                {"name": "CODE", "value": _CODE, "field_type": "MEMO_JAVA"},
                {"name": "QUERY", "value": "SELECT id FROM orders", "field_type": "MEMO_SQL"},
            ],
            "schemas": [],
        }
    )
    load["tmap_data"] = [
        {
            "component_name": "tMap_1",
            "input_tables": ["row1"],
            "output_tables": ["out"],
            "entries": [
                {
                    "table_name": "out",
                    "column_name": "total",
                    "expression": "row1.quantity * row1.unit_price",
                }
            ],
            "var_entries": [],
        }
    ]
    scan_dir = write_scan(root, designs=[load, design("InvoiceSync", group="invoices")])
    (scan_dir / "scan" / "routines.json").write_text(
        json.dumps(
            [
                {
                    "name": "Vault",
                    "routine_id": "Vault",
                    "is_system": False,
                    "source_complete": True,
                    "source_code": "public static String lookup(String k){ return context.vault_url; }",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    _reapprove(scan_dir)

    monkeypatch.setattr(config, "intodoc_output_dir", root)
    monkeypatch.setattr(config, "runtime_evidence_enabled", False)
    target = tmp_path / "coded-staging"
    build_workspace(
        project_id="PROJECT", staging_dir=target, platform="talend", goal="Document it."
    )
    return target


class TestSourceBoundary:
    def test_no_staged_json_holds_a_verbatim_code_body(self, coded_workspace: Path) -> None:
        """The rule is on bytes. A filename rule cannot see inside a shard."""

        offenders: list[str] = []
        for path in sorted(coded_workspace.rglob("*.json")):
            text = path.read_text(encoding="utf-8")
            for line in _CODE.splitlines():
                if line.strip() and line.strip() in text:
                    offenders.append(path.relative_to(coded_workspace).as_posix())
        assert offenders == []

    def test_the_code_fields_and_code_parameters_are_gone(self, coded_workspace: Path) -> None:
        shard = json.loads(
            (coded_workspace / "scan" / "jobs" / "orders" / "OrdersLoad.json").read_text()
        )
        component = next(c for c in shard["components"] if c["component_type"] == "tJava")
        for field in VERBATIM_CODE_FIELDS:
            assert field not in component
        assert [param["name"] for param in component["params"]] == ["QUERY"]
        assert not set(CODE_PARAM_NAMES) & {p["name"] for p in component["params"]}

    def test_the_removal_is_stated_where_the_reader_is(self, coded_workspace: Path) -> None:
        """A missing key reads as "this component has no code". It must not."""

        shard = json.loads(
            (coded_workspace / "scan" / "jobs" / "orders" / "OrdersLoad.json").read_text()
        )
        component = next(c for c in shard["components"] if c["component_type"] == "tJava")
        summary = component[WITHHELD_KEY]
        assert summary["withheld"] is True
        assert summary["characters"] > 0
        assert summary["references"]["context_variables"] == ["erp_host"]

    def test_a_routine_body_is_withheld_too(self, coded_workspace: Path) -> None:
        routines = json.loads((coded_workspace / "scan" / "routines.json").read_text())
        assert "source_code" not in routines[0]
        assert routines[0][WITHHELD_KEY]["references"]["context_variables"] == ["vault_url"]

    def test_mapping_expressions_survive(self, coded_workspace: Path) -> None:
        """The distinction the slice turns on, asserted on a real staged shard.

        An expression is a per-field parser extraction verified by the same
        redaction pass as everything else, and it is the field-level
        documentation. Removing it would make the lane useless; keeping a
        tJava body would make it unsafe.
        """

        shard = json.loads(
            (coded_workspace / "scan" / "jobs" / "orders" / "OrdersLoad.json").read_text()
        )
        entries = shard["tmap_data"][0]["entries"]
        assert entries[0]["expression"] == "row1.quantity * row1.unit_price"

    def test_settings_shaped_parameters_survive(self, coded_workspace: Path) -> None:
        shard = json.loads(
            (coded_workspace / "scan" / "jobs" / "orders" / "OrdersLoad.json").read_text()
        )
        component = next(c for c in shard["components"] if c["component_type"] == "tJava")
        assert component["params"][0]["value"] == "SELECT id FROM orders"

    def test_the_limitations_file_discloses_the_withholding_with_a_population(
        self, coded_workspace: Path
    ) -> None:
        body = (coded_workspace / "facts" / "parser_limitations.md").read_text()
        assert "## Custom code" in body
        assert "2 custom-code body/bodies" in body
        assert "custom code this evidence set does not carry" in body

    def test_an_estate_with_no_custom_code_says_so(self, workspace: Path) -> None:
        """Absence has to be stated too, or the agent cannot tell which it is."""

        body = (workspace / "facts" / "parser_limitations.md").read_text()
        assert "No component or routine in this scan carried a custom-code body" in body


# --- INDEX.md and facts/navigation/ ------------------------------------------


class TestIndexAndNavigation:
    def test_the_index_is_written_and_states_what_the_scan_found(self, workspace: Path) -> None:
        body = (workspace / INDEX).read_text()
        assert "## What this scan found" in body
        assert "| units (designs) | 3 |" in body

    def test_the_coverage_ledger_is_gone(self, workspace: Path) -> None:
        """One set of populations, in one place. Two shapes of it drift."""

        assert not (workspace / "facts" / "coverage_ledger.md").exists()

    def test_every_path_the_map_advertises_exists(self, workspace: Path) -> None:
        """``INDEX.md`` is a bootstrap file; a path in it that resolves to nothing is a lie.

        VERIFIED DEFECT: the routing table rendered ``index/candidates/`` as a
        directory while ``index_builder._write_shards`` writes the flat file
        ``index/candidates.jsonl``. Every index path in the map was wrong, in
        the one file the agent reads before it reads anything else -- so an
        agent following the map finds nothing at the advertised path and
        reasonably concludes the index does not exist.

        Written over EVERY backticked workspace path rather than over the index
        rows alone: the same class of error is available to the navigation
        views, the runtime files and the evidence roots, and pinning only the
        one that broke would leave the rest free to break next.
        """

        # The two paths the map tells the AGENT to create. They are correctly
        # absent from a freshly built workspace, and naming them here rather
        # than skipping all of `notes/` keeps `notes/coverage/roster.jsonl` --
        # which Nexus seeds and which therefore must exist -- inside the check.
        from mcp_server.providers.agent_workspace.contract import (
            COMPLETION_RECEIPT,
            COVERAGE_LOG,
        )

        agent_authored = {COMPLETION_RECEIPT.as_posix(), COVERAGE_LOG.as_posix()}

        body = (workspace / INDEX).read_text()
        # Backticked tokens that look like a workspace-relative path: a known
        # root, then either a glob or a concrete name. Prose and table headers
        # do not match, and nothing outside the workspace is asserted. A token
        # holding `<` is a template (`facts/mappings/<design>__<mapper>.md`),
        # which describes a naming convention rather than naming a file.
        roots = ("index/", "facts/", "scan/", "runtime/", "notes/", "instructions/")
        candidates = {
            token
            for token in re.findall(r"`([^`\s]+)`", body)
            if token.startswith(roots) and "<" not in token and token not in agent_authored
        }
        assert candidates, body
        missing = [
            token
            for token in sorted(candidates)
            if not (
                list(workspace.glob(token))
                if any(char in token for char in "*?[")
                else (workspace / token).exists()
            )
        ]
        assert not missing, missing

    def test_every_navigation_row_resolves_to_a_file_that_exists(self, workspace: Path) -> None:
        manifest = json.loads((workspace / "facts" / "navigation" / "manifest.json").read_text())
        seen = 0
        for view in manifest["views"]:
            for shard in view["shards"]:
                for line in (workspace / shard).read_text().splitlines():
                    row = json.loads(line)
                    assert (workspace / row["evidence"]).is_file(), row
                    seen += 1
        assert seen == sum(view["rows"] for view in manifest["views"])
        assert seen > 0

    def test_a_mapping_row_names_the_staged_table(self, workspace: Path) -> None:
        rows = [
            json.loads(line)
            for line in (workspace / "facts" / "navigation" / "mappings" / "part_001.jsonl")
            .read_text()
            .splitlines()
        ]
        assert rows[0]["evidence"] == "facts/mappings/OrdersLoad__tMap_1.md"
        assert (workspace / rows[0]["evidence"]).is_file()

    def test_the_index_is_part_of_the_integrity_baseline(self, workspace: Path) -> None:
        """The map the whole investigation routes through is evidence too."""

        from mcp_server.providers.agent_workspace.workspace import evidence_digest

        before = evidence_digest(workspace)
        (workspace / INDEX).write_text("# Nothing to see here\n", encoding="utf-8")
        assert evidence_digest(workspace) != before

    def test_a_navigation_shard_is_part_of_the_integrity_baseline(self, workspace: Path) -> None:
        from mcp_server.providers.agent_workspace.workspace import evidence_digest

        before = evidence_digest(workspace)
        shard = workspace / "facts" / "navigation" / "units" / "part_001.jsonl"
        shard.write_text(shard.read_text() + '{"view": "units"}\n', encoding="utf-8")
        assert evidence_digest(workspace) != before
