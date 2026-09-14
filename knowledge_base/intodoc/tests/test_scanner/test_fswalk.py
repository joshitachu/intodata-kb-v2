"""Traversal must survive an unreadable directory and must not match by case.

Both behaviours come from one real customer upload: a Docker Desktop macOS bind
mount intermittently returned ENOENT for a directory that resolved fine moments
later, which aborted a 7,295-file scan; and a data folder named ``Process`` was
registered as a Talend execution root because that mount is case-insensitive.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from intodoc.platforms.talend.parser import (
    TalendParser,
    _has_supported_talend_artifacts,
    _is_talend_root,
)
from intodoc.scanner.credential_filter import CredentialFilter
from intodoc.scanner.fswalk import (
    case_variant_names,
    child_dir,
    child_dirs,
    walk_dirs,
    walk_files,
)


def _fail_scandir(monkeypatch, target: Path, *, times: int | None = None) -> list[str]:
    """Make ``os.scandir`` fail for exactly one directory. Returns the call log."""
    real = os.scandir
    calls: list[str] = []

    def fake(path=".", *args, **kwargs):
        if Path(str(path)) == target:
            calls.append(str(path))
            if times is None or len(calls) <= times:
                raise FileNotFoundError(2, "No such file or directory", str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", fake)
    return calls


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / "a.xsl").write_text("<x/>", encoding="utf-8")
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "b.xsl").write_text("<x/>", encoding="utf-8")
    (tmp_path / "top.xsl").write_text("<x/>", encoding="utf-8")
    return tmp_path


def test_persistently_unreadable_directory_is_reported_and_the_walk_continues(tree, monkeypatch):
    calls = _fail_scandir(monkeypatch, tree / "bad")
    seen: list[tuple[Path, OSError]] = []
    found = sorted(
        p.name for p in walk_files(tree, patterns=("*.xsl",), on_error=lambda d, e: seen.append((d, e)))
    )
    assert found == ["a.xsl", "top.xsl"], "the readable subtree must still be walked"
    assert [d for d, _ in seen] == [tree / "bad"]
    assert isinstance(seen[0][1], FileNotFoundError)
    assert len(calls) == 2, "one retry before giving up"


def test_transient_failure_is_retried_and_loses_nothing(tree, monkeypatch):
    _fail_scandir(monkeypatch, tree / "bad", times=1)
    seen: list[Path] = []
    found = sorted(
        p.name for p in walk_files(tree, patterns=("*.xsl",), on_error=lambda d, e: seen.append(d))
    )
    assert found == ["a.xsl", "b.xsl", "top.xsl"], "the retry must recover the real files"
    assert seen == [], "a recovered directory is not a warning"


def test_walk_dirs_and_child_dirs_tolerate_the_same_failure(tree, monkeypatch):
    _fail_scandir(monkeypatch, tree / "bad")
    assert sorted(p.name for p in walk_dirs(tree)) == ["bad", "good"]
    assert sorted(p.name for p in child_dirs(tree)) == ["bad", "good"]
    _fail_scandir(monkeypatch, tree)
    assert list(walk_files(tree)) == []
    assert child_dirs(tree) == []


def test_directory_matching_is_case_exact(tmp_path):
    root = tmp_path / "WorkOrderData"
    (root / "Process" / "Processed").mkdir(parents=True)
    # The real defect: on a case-insensitive mount `(root / "process").is_dir()`
    # is True, so a customer data folder became a Talend root.
    assert child_dir(root, "process") is None
    assert child_dir(root, "Process") is not None
    assert _is_talend_root(root) is False
    assert case_variant_names(root, "process") == ["Process"]

    real = tmp_path / "RealProject"
    (real / "process" / "Orders").mkdir(parents=True)
    assert _is_talend_root(real) is True
    assert child_dir(real, "process", "Orders") is not None
    assert child_dir(real, "process", "orders") is None


def test_support_project_detection_is_case_exact(tmp_path):
    # Two separate roots on purpose: this suite also runs on case-insensitive
    # filesystems, where creating `code/routines` beside `Code/Routines` in one
    # directory is refused outright -- the same property that caused the bug.
    wrong = tmp_path / "Support"
    (wrong / "Code" / "Routines").mkdir(parents=True)
    assert _has_supported_talend_artifacts(wrong) is False

    right = tmp_path / "RealSupport"
    (right / "code" / "routines").mkdir(parents=True)
    assert _has_supported_talend_artifacts(right) is True


def _talend_project(tmp_path: Path) -> Path:
    root = tmp_path / "PROJECT"
    (root / "process" / "Orders").mkdir(parents=True)
    (root / "talend.project").write_text(
        '<?xml version="1.0"?><xmi:XMI xmlns:xmi="http://www.omg.org/XMI" '
        'xmlns:TalendProperties="http://www.talend.org/properties">'
        '<TalendProperties:Project label="p" technicalLabel="P"/></xmi:XMI>',
        encoding="utf-8",
    )
    item = root / "process" / "Orders" / "Load_0.1.item"
    item.write_text(
        '<?xml version="1.0"?><talendfile:ProcessType '
        'xmlns:talendfile="platform:/resource/org.talend.model/model/TalendFile.xsd">'
        '<node componentName="tMSSqlInput"><elementParameter name="UNIQUE_NAME" value="tMSSqlInput_1"/>'
        "</node></talendfile:ProcessType>",
        encoding="utf-8",
    )
    item.with_suffix(".properties").write_text(
        '<?xml version="1.0"?><xmi:XMI xmlns:xmi="http://www.omg.org/XMI" '
        'xmlns:TalendProperties="http://www.talend.org/properties">'
        '<TalendProperties:Property id="_g" label="Load" version="0.1"/></xmi:XMI>',
        encoding="utf-8",
    )
    return root


def test_scan_survives_an_unreadable_directory_and_names_the_gap(tmp_path, monkeypatch):
    root = _talend_project(tmp_path)
    (root / "Structures" / "Orders").mkdir(parents=True)
    (root / "Structures" / "Orders" / "S.xml").write_text("<root/>", encoding="utf-8")
    _fail_scandir(monkeypatch, root / "Structures" / "Orders")

    result = TalendParser(CredentialFilter()).parse(tmp_path)

    assert [job.name for job in result.jobs] == ["Load"], "the scan must still complete"
    unreadable = [w for w in result.warnings if w.kind == "unreadable_directory"]
    assert unreadable, "the skipped subtree must be reported"
    assert "Structures/Orders" in unreadable[0].file.replace(os.sep, "/")
    assert "absent from this scan" in unreadable[0].message


def test_case_variant_artifact_directory_is_warned_not_silently_skipped(tmp_path):
    root = _talend_project(tmp_path)
    (root / "Routes" / "Inbound").mkdir(parents=True)

    result = TalendParser(CredentialFilter()).parse(tmp_path)

    variants = [w for w in result.warnings if w.kind == "case_variant_directory"]
    assert variants, "a differently-cased Talend directory must be reported"
    assert any("Routes" in w.message and "routes" in w.message for w in variants)
    assert all(job.source_design == "process" for job in result.jobs)
