"""Slice 1: the approval boundary, and the ways it was walked around.

Three bypasses were reproduced against the previous builder, and every
assertion here exists because one of them shipped:

1. **A symlink was invisible.** ``_tree_relative_paths`` skipped symlinks
   while walking the scan, and the scanner skips them while writing the
   receipt, so a link planted afterwards was in neither the ``added`` nor the
   ``removed`` set. An arbitrary host file was staged into the workspace and
   ``verify_scan_is_publishable`` returned with ``notices == []``.
2. **The membership rule came out of the receipt it was supposed to bind.**
   Narrowing ``roots`` to ``["scan/jobs"]`` left every estate-level collection
   outside the walk and therefore unverified, while ``_stage_scan`` still
   staged it by fixed filename. (That one is pinned in ``test_workspace.py``
   beside the rest of the receipt checks.)
3. **A recognised filename was a licence to read.** An optional collection
   absent from the receipt was opened anyway, because the code asked the
   filesystem rather than the approval.

The shape of the fix is what makes these testable at all: ONE class owns
open, hash, compare and parse, so "no path bypasses verification" is a
property of one object instead of an invariant spread over six call sites.
The last test in this module is the static half of that claim -- the same
idiom ``test_contract.py`` and ``test_no_partitioning.py`` use, and for the
same reason: "nothing else opens the scan" is not observable at runtime.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path

import pytest

from mcp_server.providers.agent_workspace.approved_reader import (
    MAX_APPROVED_ARTIFACT_BYTES,
    ApprovedArtifactReader,
    approved_tree_paths,
    read_root_of_trust,
)
from mcp_server.providers.agent_workspace.contract import WorkspaceUnavailableError

_SECRET = "kirk/Sup3rSecret!99"


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@pytest.fixture
def scan_dir(tmp_path: Path) -> Path:
    """A scan directory with two approved artifacts and one unapproved one."""

    root = tmp_path / "PROJECT"
    (root / "scan" / "jobs" / "g").mkdir(parents=True)
    (root / "scan" / "contexts.json").write_text('[{"name": "prod"}]', encoding="utf-8")
    (root / "scan" / "jobs" / "g" / "A.json").write_text('{"name": "A"}', encoding="utf-8")
    (root / "scan" / "unapproved.json").write_text(f'{{"password": "{_SECRET}"}}', encoding="utf-8")
    return root


@pytest.fixture
def reader(scan_dir: Path) -> ApprovedArtifactReader:
    digests = {
        relative: _digest((scan_dir / relative).read_bytes())
        for relative in ("scan/contexts.json", "scan/jobs/g/A.json")
    }
    return ApprovedArtifactReader(scan_dir, digests)


# --- membership is the entry criterion ---------------------------------------


class TestOnlyApprovedPathsAreReadable:
    def test_an_approved_artifact_is_read_and_parsed(self, reader: ApprovedArtifactReader) -> None:
        assert reader.read_json("scan/contexts.json") == [{"name": "prod"}]

    def test_a_file_the_receipt_does_not_name_is_refused(
        self, reader: ApprovedArtifactReader
    ) -> None:
        """It exists, it parses, and it is still not evidence.

        This is the third bypass in its simplest form: the builder used to ask
        ``source.is_file()``, which is a question about the disk rather than
        about the approval.
        """

        with pytest.raises(WorkspaceUnavailableError, match="not covered by the publication"):
            reader.read_json("scan/unapproved.json")

    def test_has_asks_the_receipt_and_never_the_filesystem(
        self, reader: ApprovedArtifactReader
    ) -> None:
        assert reader.has("scan/contexts.json")
        assert not reader.has("scan/unapproved.json")
        assert not reader.has("scan/never_existed.json")

    def test_the_approved_surface_is_exactly_the_receipt(
        self, reader: ApprovedArtifactReader
    ) -> None:
        assert reader.approved_paths == {"scan/contexts.json", "scan/jobs/g/A.json"}

    @pytest.mark.parametrize(
        "relative",
        [
            "/etc/hosts",
            "../../etc/hosts",
            "scan/../../etc/hosts",
            "",
            "   ",
        ],
    )
    def test_an_absolute_path_or_a_traversal_is_refused(
        self, reader: ApprovedArtifactReader, relative: str
    ) -> None:
        with pytest.raises(WorkspaceUnavailableError, match="unsafe artifact path|not covered"):
            reader.read_bytes(relative)


# --- what may be opened ------------------------------------------------------


class TestOnlyRegularNonLinkedFilesAreOpened:
    def test_a_symlinked_artifact_is_refused_even_when_its_digest_matches(
        self, scan_dir: Path, tmp_path: Path
    ) -> None:
        """The refusal is on the LINK, not on the bytes.

        An attacker who can plant a link can also make the receipt agree with
        the target -- the receipt is a file on disk. A digest-only check
        therefore passes on exactly the case that matters, so the structural
        check has to come first and has to be independent of it.
        """

        outside = tmp_path / "outside.json"
        outside.write_text(f'{{"password": "{_SECRET}"}}', encoding="utf-8")
        planted = scan_dir / "scan" / "xslt.json"
        os.symlink(outside, planted)

        reader = ApprovedArtifactReader(scan_dir, {"scan/xslt.json": _digest(outside.read_bytes())})
        with pytest.raises(WorkspaceUnavailableError, match="is a symlink") as caught:
            reader.read_bytes("scan/xslt.json")
        assert _SECRET not in str(caught.value)

    def test_an_internal_symlink_is_refused_too(self, scan_dir: Path) -> None:
        """Pointing at another approved artifact is still not that artifact."""

        target = scan_dir / "scan" / "contexts.json"
        planted = scan_dir / "scan" / "xslt.json"
        os.symlink(target, planted)
        reader = ApprovedArtifactReader(scan_dir, {"scan/xslt.json": _digest(target.read_bytes())})
        with pytest.raises(WorkspaceUnavailableError, match="is a symlink"):
            reader.read_bytes("scan/xslt.json")

    def test_a_symlinked_parent_directory_is_refused(self, scan_dir: Path, tmp_path: Path) -> None:
        """The variant a per-file check misses: the whole subtree moved."""

        elsewhere = tmp_path / "elsewhere" / "h"
        elsewhere.mkdir(parents=True)
        shard = elsewhere / "B.json"
        shard.write_text(f'{{"password": "{_SECRET}"}}', encoding="utf-8")
        os.symlink(elsewhere, scan_dir / "scan" / "jobs" / "h")

        reader = ApprovedArtifactReader(
            scan_dir, {"scan/jobs/h/B.json": _digest(shard.read_bytes())}
        )
        with pytest.raises(WorkspaceUnavailableError, match="parent directory") as caught:
            reader.read_bytes("scan/jobs/h/B.json")
        assert _SECRET not in str(caught.value)

    def test_a_non_regular_file_is_refused(self, scan_dir: Path) -> None:
        """A fifo blocks forever, and its bytes are nobody's approved bytes."""

        fifo = scan_dir / "scan" / "xslt.json"
        os.mkfifo(fifo)
        reader = ApprovedArtifactReader(scan_dir, {"scan/xslt.json": _digest(b"[]")})
        with pytest.raises(WorkspaceUnavailableError, match="not a regular file|plain file"):
            reader.read_bytes("scan/xslt.json")

    def test_a_directory_under_an_approved_name_is_refused(self, scan_dir: Path) -> None:
        (scan_dir / "scan" / "xslt.json").mkdir()
        reader = ApprovedArtifactReader(scan_dir, {"scan/xslt.json": _digest(b"[]")})
        with pytest.raises(WorkspaceUnavailableError, match="missing|not a regular file"):
            reader.read_bytes("scan/xslt.json")

    def test_a_resolved_artifact_must_stay_inside_the_scan_root(
        self, scan_dir: Path, tmp_path: Path
    ) -> None:
        """Belt and braces beside the symlink checks, and cheap.

        Every path that leaves the scan root today does so through a link, so
        this is the check that keeps holding if some future construct gets
        there another way.
        """

        from mcp_server.providers.agent_workspace.approved_reader import resolves_outside

        assert resolves_outside(scan_dir, "../outside.json")
        assert not resolves_outside(scan_dir, "scan/contexts.json")


# --- bytes, digests and parsing ----------------------------------------------


class TestTheBytesAreTheOnesThatWereHashed:
    def test_a_changed_artifact_is_refused(self, scan_dir: Path, reader) -> None:
        (scan_dir / "scan" / "contexts.json").write_text(
            f'[{{"password": "{_SECRET}"}}]', encoding="utf-8"
        )
        with pytest.raises(WorkspaceUnavailableError, match="approved digest") as caught:
            reader.read_json("scan/contexts.json")
        assert _SECRET not in str(caught.value)

    def test_one_read_opens_the_artifact_exactly_once(
        self, scan_dir: Path, reader, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Hash-then-reopen is two different files whenever anything races it.

        Counting opens is the only way to assert this from the outside: the
        second read would return the attacker's bytes, and every digest in the
        receipt would still describe the first ones.
        """

        opens: list[str] = []
        real_open = os.open

        def counting_open(path, flags, *args, **kwargs):
            opens.append(str(path))
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", counting_open)
        reader.read_json("scan/contexts.json")
        assert [path for path in opens if path.endswith("contexts.json")] == [
            str(scan_dir / "scan" / "contexts.json")
        ]

    def test_json_is_parsed_from_the_verified_buffer(self) -> None:
        """The static half: ``read_json`` must go through ``read_bytes``."""

        from mcp_server.providers.agent_workspace import approved_reader

        source = Path(approved_reader.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        body = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "read_json"
        )
        segment = ast.get_source_segment(source, body) or ""
        assert "self.read_bytes(" in segment
        assert "open(" not in segment

    def test_an_artifact_that_is_not_json_is_refused_without_quoting_it(
        self, scan_dir: Path
    ) -> None:
        payload = f"not json, and it holds {_SECRET}".encode()
        (scan_dir / "scan" / "contexts.json").write_bytes(payload)
        reader = ApprovedArtifactReader(scan_dir, {"scan/contexts.json": _digest(payload)})
        with pytest.raises(WorkspaceUnavailableError, match="could not be read as JSON") as caught:
            reader.read_json("scan/contexts.json")
        assert _SECRET not in str(caught.value)

    def test_an_oversize_artifact_is_refused(self, scan_dir: Path) -> None:
        payload = b"[" + b"0," * 5_000 + b"0]"
        (scan_dir / "scan" / "contexts.json").write_bytes(payload)
        reader = ApprovedArtifactReader(
            scan_dir, {"scan/contexts.json": _digest(payload)}, max_bytes=1_000
        )
        with pytest.raises(WorkspaceUnavailableError, match="ceiling"):
            reader.read_bytes("scan/contexts.json")

    def test_the_default_ceiling_leaves_room_for_a_real_scan(self) -> None:
        """21 MB is the largest artifact this lane is documented to handle."""

        assert MAX_APPROVED_ARTIFACT_BYTES > 21_000_000

    def test_verify_streams_and_refuses_the_same_things(self, scan_dir: Path, reader) -> None:
        reader.verify("scan/contexts.json")
        (scan_dir / "scan" / "contexts.json").write_text("[]", encoding="utf-8")
        with pytest.raises(WorkspaceUnavailableError, match="approved digest"):
            reader.verify("scan/contexts.json")

    def test_the_approval_digest_moves_with_any_declared_byte(self, scan_dir: Path) -> None:
        first = ApprovedArtifactReader(scan_dir, {"scan/contexts.json": "sha256:a"})
        second = ApprovedArtifactReader(scan_dir, {"scan/contexts.json": "sha256:b"})
        third = ApprovedArtifactReader(
            scan_dir, {"scan/contexts.json": "sha256:a", "scan/x.json": "sha256:a"}
        )
        assert first.approval_digest != second.approval_digest
        assert first.approval_digest != third.approval_digest
        assert (
            first.approval_digest
            == ApprovedArtifactReader(scan_dir, {"scan/contexts.json": "sha256:a"}).approval_digest
        )


# --- the walk ----------------------------------------------------------------


class TestTheMembershipWalk:
    def test_a_planted_symlink_is_reported_rather_than_skipped(self, scan_dir: Path) -> None:
        """The defect itself. Both sides skipped links, so neither saw one."""

        os.symlink("/etc/hosts", scan_dir / "scan" / "xslt.json")
        found, links = approved_tree_paths(scan_dir)
        assert "scan/xslt.json" not in found
        assert links == ["scan/xslt.json"]

    def test_the_excluded_prefixes_are_this_builders_own(self, scan_dir: Path) -> None:
        (scan_dir / "scan" / "sanitized").mkdir()
        (scan_dir / "scan" / "sanitized" / "Orders.item").write_text("<xml/>", encoding="utf-8")
        os.symlink("/etc/hosts", scan_dir / "scan" / "sanitized" / "planted.json")
        found, links = approved_tree_paths(scan_dir)
        assert not [path for path in found if path.startswith("scan/sanitized/")]
        assert links == []


# --- the root of trust -------------------------------------------------------


class TestRootOfTrust:
    def test_a_receipt_is_read_with_the_structural_guards(self, scan_dir: Path) -> None:
        (scan_dir / "publication_approval.json").write_text('{"approved": true}', encoding="utf-8")
        assert read_root_of_trust(scan_dir, "publication_approval.json") == {"approved": True}

    def test_a_symlinked_receipt_is_refused(self, scan_dir: Path, tmp_path: Path) -> None:
        """Nothing can verify the receipt, so its shape is all there is."""

        forged = tmp_path / "forged.json"
        forged.write_text('{"approved": true}', encoding="utf-8")
        os.symlink(forged, scan_dir / "publication_approval.json")
        with pytest.raises(WorkspaceUnavailableError, match="has no publication_approval.json"):
            read_root_of_trust(scan_dir, "publication_approval.json")

    def test_an_absent_receipt_carries_the_callers_remedy(self, scan_dir: Path) -> None:
        with pytest.raises(WorkspaceUnavailableError, match="Rescan the project"):
            read_root_of_trust(
                scan_dir, "publication_approval.json", absent_hint="Rescan the project."
            )


# --- the static guarantee ----------------------------------------------------

_LANE = Path(__file__).resolve().parents[2] / "src" / "mcp_server" / "providers" / "agent_workspace"

#: Every name the builder holds a scan-output directory under. A path built
#: from one of these is a read that did not go through the reader.
_SCAN_DIRECTORY_NAMES = frozenset({"scan_dir", "scan_root", "scan_output_dir"})

_OPENING_CALLS = frozenset({"open", "read_text", "read_bytes", "read_json", "load", "loads"})


def _staging_source() -> tuple[str, ast.Module]:
    source = (_LANE / "workspace.py").read_text(encoding="utf-8")
    return source, ast.parse(source)


def test_no_staging_path_opens_scanner_output_outside_the_reader() -> None:
    """The property the whole slice rests on, asserted statically.

    "Everything goes through the reader" is not observable at runtime -- a
    missed call site simply works, and works on unverified bytes. So the check
    is on the source: ``workspace.py`` may PASS ``scan_dir`` to the reader and
    to ``build_exact_tables``, and may never join a path onto it.

    ``providers/custom/enterprise/workspace.py::build_exact_tables`` is the
    one exception and it is a deliberate, narrow one: it takes a directory
    because it is shared with the legacy lane, it opens exactly the four
    artifacts in ``workspace._EXACT_TABLE_INPUTS``, and every one of them is
    re-proved through the reader immediately before the call.
    """

    source, tree = _staging_source()
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
            continue
        base = node
        while isinstance(base, ast.BinOp) and isinstance(base.op, ast.Div):
            base = base.left
        if isinstance(base, ast.Name) and base.id in _SCAN_DIRECTORY_NAMES:
            offenders.append(f"line {node.lineno}: {ast.get_source_segment(source, node)}")
    assert offenders == [], "workspace.py builds a path from the scan directory: " + "; ".join(
        offenders
    )


def test_no_file_opening_call_in_the_stager_mentions_the_scan_directory() -> None:
    """The same property from the other side, so an alias cannot slip through."""

    source, tree = _staging_source()
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.attr
            if isinstance(func, ast.Attribute)
            else (func.id if isinstance(func, ast.Name) else "")
        )
        if name not in _OPENING_CALLS:
            continue
        segment = ast.get_source_segment(source, node) or ""
        # `reader.read_json(...)` is the whole point; it is not an open.
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id == "reader":
                continue
        if any(token in segment for token in _SCAN_DIRECTORY_NAMES):
            offenders.append(f"line {node.lineno}: {segment}")
    assert offenders == [], "a staging read names the scan directory: " + "; ".join(offenders)


@pytest.mark.parametrize(
    "function",
    ["_stage_scan", "_stage_designs", "render_parser_limitations", "_stageable_relative_paths"],
)
def test_every_evidence_reader_takes_the_reader_rather_than_a_directory(function: str) -> None:
    """A directory argument is the shape the bypasses had. Pin the seam."""

    _source, tree = _staging_source()
    node = next(
        item
        for item in ast.walk(tree)
        if isinstance(item, ast.FunctionDef) and item.name == function
    )
    arguments = {argument.arg for argument in node.args.args}
    assert "reader" in arguments, f"{function} does not receive the approved-artifact reader"


def test_the_receipt_constants_live_with_the_reader() -> None:
    """One home for the approval rule, so the two copies cannot drift."""

    source = (_LANE / "approved_reader.py").read_text(encoding="utf-8")
    for name in (
        "APPROVAL_ROOTS",
        "APPROVAL_EXCLUDED_PREFIXES",
        "APPROVAL_TREE_ALGORITHM",
        "REQUIRED_APPROVED_ARTIFACTS",
        "REQUIRED_APPROVED_TREE_FILES",
    ):
        assert f"{name}:" in source or f"{name} =" in source, name


def test_the_owned_rule_still_matches_the_scanner_that_writes_it() -> None:
    """The rule is OWNED here, which only works if it is also RIGHT here.

    Parsed out of the scanner rather than imported: ``intodoc`` is a separate
    distribution and is deliberately not installed in this environment, the
    same reason ``contract.CODE_PARAM_NAMES`` is a checked copy.
    """

    from mcp_server.providers.agent_workspace.approved_reader import (
        APPROVAL_EXCLUDED_PREFIXES,
        APPROVAL_ROOTS,
        APPROVAL_TREE_ALGORITHM,
    )

    cli = Path(__file__).resolve().parents[3] / "intodoc" / "src" / "intodoc" / "cli.py"
    if not cli.is_file():
        pytest.skip("the scanner source tree is not checked out beside the MCP server")
    tree = ast.parse(cli.read_text(encoding="utf-8"))
    found: dict[str, object] = {}
    for node in ast.walk(tree):
        target = (
            node.target
            if isinstance(node, ast.AnnAssign)
            else (node.targets[0] if isinstance(node, ast.Assign) and node.targets else None)
        )
        if isinstance(target, ast.Name) and target.id in {
            "_AGENT_ARTIFACT_ROOTS",
            "_AGENT_ARTIFACT_EXCLUDED_PREFIXES",
            "_APPROVAL_TREE_ALGORITHM",
        }:
            found[target.id] = ast.literal_eval(node.value)
    assert tuple(found["_AGENT_ARTIFACT_ROOTS"]) == APPROVAL_ROOTS
    assert tuple(found["_AGENT_ARTIFACT_EXCLUDED_PREFIXES"]) == APPROVAL_EXCLUDED_PREFIXES
    assert found["_APPROVAL_TREE_ALGORITHM"] == APPROVAL_TREE_ALGORITHM


# --- end to end, on a real build ---------------------------------------------


def test_a_symlink_planted_at_an_optional_collection_stops_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reproduced failure, from the operator's end.

    ``scan/xslt.json`` is legitimately absent on an estate with no
    stylesheets, so the NAME is free. Before this slice the link was staged,
    ``verify_scan_is_publishable`` returned ACCEPTED, and the receipt carried
    no notice at all.
    """

    from mcp_server.config import config
    from mcp_server.providers.agent_workspace.workspace import build_workspace
    from tests.test_agent_workspace.workspace_fixture import design, write_scan

    root = tmp_path / "intodoc-output"
    root.mkdir()
    scan_dir = write_scan(root, designs=[design("A", group="g")], omit=("scan/xslt.json",))
    host_file = tmp_path / "host_secret.json"
    host_file.write_text(f'{{"password": "{_SECRET}"}}', encoding="utf-8")
    os.symlink(host_file, scan_dir / "scan" / "xslt.json")

    monkeypatch.setattr(config, "intodoc_output_dir", root)
    monkeypatch.setattr(config, "runtime_evidence_enabled", False)
    staging = tmp_path / "staging"
    with pytest.raises(WorkspaceUnavailableError, match="no longer matches its publication"):
        build_workspace(project_id="PROJECT", staging_dir=staging, platform="talend", goal="g")
    assert not staging.exists()


def test_a_design_the_approval_does_not_cover_is_not_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``job_files`` is data, even after the manifest's own digest checks out.

    A value pointing at the sanitized source mirror resolved inside ``scan/``
    and passed the containment check, so the old staging path opened it. The
    tree excludes that prefix on purpose, so the reader has no digest for it
    and refuses -- and the design is recorded as unstaged rather than dropped.
    """

    from mcp_server.config import config
    from mcp_server.providers.agent_workspace.workspace import build_workspace
    from tests.test_agent_workspace.workspace_fixture import approval_tree, design, write_scan

    root = tmp_path / "intodoc-output"
    root.mkdir()
    scan_dir = write_scan(root, designs=[design("A", group="g"), design("B", group="g")])
    (scan_dir / "scan" / "sanitized").mkdir()
    (scan_dir / "scan" / "sanitized" / "B.json").write_text(
        f'{{"name": "B", "password": "{_SECRET}"}}', encoding="utf-8"
    )
    manifest_path = scan_dir / "scan" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["job_files"]["B"] = "sanitized/B.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    approval = json.loads((scan_dir / "publication_approval.json").read_text())
    approval["artifacts"]["scan/manifest.json"] = _digest(manifest_path.read_bytes())
    approval["tree"] = approval_tree(scan_dir)
    (scan_dir / "publication_approval.json").write_text(json.dumps(approval, indent=2))

    monkeypatch.setattr(config, "intodoc_output_dir", root)
    monkeypatch.setattr(config, "runtime_evidence_enabled", False)
    receipt = build_workspace(
        project_id="PROJECT", staging_dir=tmp_path / "staging", platform="talend", goal="g"
    )
    assert receipt.design_count == 1
    assert any("not covered by the publication approval" in notice for notice in receipt.notices)
    staged = [
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "staging" / "scan" / "jobs").rglob("*.json")
    ]
    assert not any(_SECRET in text for text in staged)
