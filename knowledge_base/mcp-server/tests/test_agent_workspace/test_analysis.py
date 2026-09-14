"""The analysis layer: the obligation that makes the depth guidance binding.

The failure being corrected (run 252): the agent read roughly 21 KB of method,
classified 142 designs in about ninety seconds having opened six design files,
and wrote its documents straight from the index. The guidance was read and it
did not change the behaviour, so the fix is not a longer prompt -- it is an
obligation the prose layer cannot be reached around.

Two properties are pinned hardest here, because losing either one turns the
obligation back into a suggestion:

* the GROUP POPULATION is recomputed from read-only evidence, so deleting every
  analysis file -- they live under agent-writable ``notes/`` -- changes nothing;
* names are matched EXACTLY against what the filesystem reports, so one file
  cannot satisfy two groups that differ only in letter case.

All fixture names are invented. No client identifier appears in this file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mcp_server.providers.agent_workspace import contract
from mcp_server.providers.agent_workspace.analysis import (
    ANALYSIS_ARTIFACT,
    MAX_NAMED,
    MIN_ANALYSIS_BYTES,
    AnalysisCoverage,
    groups_from_workspace,
    read_analysis_coverage,
    write_analysis_artifact,
)


# --------------------------------------------------------------------------
# Fixture construction
# --------------------------------------------------------------------------


def _unit(unit_id: str, *, group: str = "orders", **extra) -> dict:
    row = {
        "view": "units",
        "unit_id": unit_id,
        "display_name": unit_id.rsplit("/", 1)[-1],
        "kind": "job",
        "group": group,
    }
    row.update(extra)
    return row


def _navigation(staging: Path, rows: list[dict], *, shard: str = "part_001.jsonl") -> None:
    directory = staging / contract.NAVIGATION / "units"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / shard).write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def _estate(staging: Path) -> None:
    """Three designs across two groups. Small enough to reason about by hand."""

    _navigation(
        staging,
        [
            _unit("orders/LoadCustomer", group="orders"),
            _unit("orders/LoadCustomers", group="orders"),
            _unit("billing/InvoiceSync", group="billing"),
        ],
    )


def _analysis(staging: Path, name: str, *, size: int = 900) -> Path:
    path = staging / contract.ANALYSIS_DIR / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * size, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# The population
# --------------------------------------------------------------------------


def test_the_group_population_is_recomputed_from_the_navigation_view(tmp_path: Path) -> None:
    _estate(tmp_path)

    assert groups_from_workspace(tmp_path) == ("billing", "orders")
    assert read_analysis_coverage(tmp_path).groups == ("billing", "orders")


def test_deleting_every_analysis_file_does_not_shrink_the_obligation(tmp_path: Path) -> None:
    """The one architectural rule. ``notes/`` is agent-writable; this is not.

    A population derived from the files being checked is a population of
    whatever the agent wrote, and a gate computed from it would report a single
    analysis file as full coverage.
    """

    _estate(tmp_path)
    _analysis(tmp_path, "orders")
    _analysis(tmp_path, "billing")
    before = read_analysis_coverage(tmp_path)
    assert before.missing == ()

    for name in ("orders", "billing"):
        (tmp_path / contract.ANALYSIS_DIR / f"{name}.md").unlink()

    after = read_analysis_coverage(tmp_path)
    assert after.groups == before.groups
    assert after.missing == ("billing", "orders")
    assert after.written == {}


def test_groups_differing_only_in_case_stay_two_groups(tmp_path: Path) -> None:
    """Case folding would merge them, and they are two real groups."""

    _navigation(
        tmp_path,
        [_unit("a/Extract", group="Billing"), _unit("b/Extract", group="billing")],
    )

    coverage = read_analysis_coverage(tmp_path)

    assert coverage.groups == ("Billing", "billing")
    assert coverage.missing == ("Billing", "billing")
    assert any("differ from another only in letter case" in note for note in coverage.notices)


def test_a_group_and_its_plural_stay_two_groups(tmp_path: Path) -> None:
    """``LoadCustomer`` beside ``LoadCustomers`` is two separately-scheduled
    things on real estates, and a prefix match credits one for the other."""

    _navigation(
        tmp_path,
        [_unit("a/One", group="LoadCustomer"), _unit("b/Two", group="LoadCustomers")],
    )
    _analysis(tmp_path, "LoadCustomer")

    coverage = read_analysis_coverage(tmp_path)

    assert coverage.groups == ("LoadCustomer", "LoadCustomers")
    assert coverage.missing == ("LoadCustomers",)
    assert coverage.written == {"LoadCustomer": 900}


def test_a_missing_navigation_view_is_an_empty_population_not_an_error(
    tmp_path: Path,
) -> None:
    assert groups_from_workspace(tmp_path) == ()

    coverage = read_analysis_coverage(tmp_path)

    assert coverage.groups == ()
    assert coverage.missing == ()
    assert coverage.written == {}
    assert any("holds no rows" in note for note in coverage.notices)


def test_groups_are_read_across_every_shard(tmp_path: Path) -> None:
    _navigation(tmp_path, [_unit("a/One", group="orders")], shard="part_001.jsonl")
    _navigation(tmp_path, [_unit("b/Two", group="billing")], shard="part_002.jsonl")

    assert groups_from_workspace(tmp_path) == ("billing", "orders")


def test_a_malformed_navigation_row_is_skipped_and_the_rest_survive(tmp_path: Path) -> None:
    directory = tmp_path / contract.NAVIGATION / "units"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "part_001.jsonl").write_text(
        "{not json\n"
        + '["a list", "not an object"]\n'
        + "\n"
        + json.dumps(_unit("a/One", group="orders"))
        + "\n",
        encoding="utf-8",
    )

    assert groups_from_workspace(tmp_path) == ("orders",)


def test_designs_carrying_no_group_are_reported_rather_than_dropped_in_silence(
    tmp_path: Path,
) -> None:
    """Zero groups on a real estate and zero groups on an empty one look
    identical from the group list alone, and the first means the obligation
    quietly evaporated."""

    _navigation(
        tmp_path,
        [_unit("a/One", group=""), _unit("b/Two", group="orders")],
    )

    coverage = read_analysis_coverage(tmp_path)

    assert coverage.groups == ("orders",)
    assert any("carry no group name" in note for note in coverage.notices)


# --------------------------------------------------------------------------
# What the agent wrote
# --------------------------------------------------------------------------


def test_a_written_group_is_neither_missing_nor_thin(tmp_path: Path) -> None:
    _estate(tmp_path)
    _analysis(tmp_path, "orders", size=4000)
    _analysis(tmp_path, "billing", size=1200)

    coverage = read_analysis_coverage(tmp_path)

    assert coverage.written == {"billing": 1200, "orders": 4000}
    assert coverage.missing == ()
    assert coverage.thin == ()
    assert coverage.unexpected == ()
    assert coverage.substantial == ("billing", "orders")


def test_a_group_with_no_file_is_missing(tmp_path: Path) -> None:
    _estate(tmp_path)
    _analysis(tmp_path, "orders")

    coverage = read_analysis_coverage(tmp_path)

    assert coverage.missing == ("billing",)
    assert coverage.written == {"orders": 900}


def test_the_floor_is_the_legacy_thin_bundle_threshold(tmp_path: Path) -> None:
    """500 bytes is the number ``EvidenceQualityGate.THIN_THRESHOLD_BYTES``
    used on ``_preprocess/{group}.md``. A thin file is still a WRITTEN file --
    the two findings it drives have different severities."""

    assert MIN_ANALYSIS_BYTES == 500

    _estate(tmp_path)
    _analysis(tmp_path, "orders", size=MIN_ANALYSIS_BYTES - 1)
    _analysis(tmp_path, "billing", size=MIN_ANALYSIS_BYTES)

    coverage = read_analysis_coverage(tmp_path)

    assert coverage.thin == ("orders",)
    assert coverage.missing == ()
    assert set(coverage.written) == {"orders", "billing"}
    assert coverage.substantial == ("billing",)


def test_an_empty_file_is_thin_rather_than_absent(tmp_path: Path) -> None:
    _estate(tmp_path)
    _analysis(tmp_path, "orders", size=0)

    coverage = read_analysis_coverage(tmp_path)

    assert coverage.written["orders"] == 0
    assert coverage.thin == ("orders",)
    assert coverage.missing == ("billing",)


def test_a_file_naming_no_group_is_reported_and_credits_nothing(tmp_path: Path) -> None:
    _estate(tmp_path)
    _analysis(tmp_path, "orders")
    _analysis(tmp_path, "stock")

    coverage = read_analysis_coverage(tmp_path)

    assert coverage.unexpected == ("stock",)
    assert coverage.missing == ("billing",)
    assert "stock" not in coverage.written


def test_a_file_whose_case_differs_from_the_group_satisfies_nothing(tmp_path: Path) -> None:
    """The reason names are matched against ``iterdir`` output rather than
    probed with ``is_file()``: on a case-insensitive filesystem the probe
    answers True and one file clears two groups' obligations."""

    _navigation(
        tmp_path,
        [_unit("a/Extract", group="Billing"), _unit("b/Extract", group="billing")],
    )
    _analysis(tmp_path, "Billing")

    coverage = read_analysis_coverage(tmp_path)

    # Recorded rather than asserted: the answer differs by platform, and it is
    # exactly the mistake ``_analysis_files`` refuses to make.
    naive_probe = (tmp_path / contract.ANALYSIS_DIR / "billing.md").is_file()
    assert coverage.missing == ("billing",), f"naive probe answered {naive_probe}"
    assert coverage.written == {"Billing": 900}
    assert coverage.unexpected == ()


def test_a_near_miss_filename_is_named_so_the_slip_is_obvious(tmp_path: Path) -> None:
    _navigation(tmp_path, [_unit("a/Extract", group="billing")])
    _analysis(tmp_path, "Billing")

    coverage = read_analysis_coverage(tmp_path)

    assert coverage.missing == ("billing",)
    assert coverage.unexpected == ("Billing",)
    assert any("only in letter case" in note for note in coverage.notices)


# --------------------------------------------------------------------------
# Nothing on this path raises
# --------------------------------------------------------------------------


def test_an_absent_analysis_directory_is_every_group_missing(tmp_path: Path) -> None:
    _estate(tmp_path)

    coverage = read_analysis_coverage(tmp_path)

    assert coverage.missing == ("billing", "orders")
    assert coverage.written == {}
    assert any("does not exist" in note for note in coverage.notices)


def test_a_directory_named_like_an_analysis_file_is_not_a_file(tmp_path: Path) -> None:
    _estate(tmp_path)
    (tmp_path / contract.ANALYSIS_DIR / "orders.md").mkdir(parents=True)

    coverage = read_analysis_coverage(tmp_path)

    assert coverage.missing == ("billing", "orders")
    assert any("<group>.md convention" in note for note in coverage.notices)


def test_an_entry_that_cannot_be_measured_is_reported_as_absent(tmp_path: Path) -> None:
    """Crediting a file whose size could not be read is the one direction that
    would turn a defect into a pass."""

    _estate(tmp_path)
    directory = tmp_path / contract.ANALYSIS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(directory / "nowhere.md", directory / "orders.md")
    except (OSError, NotImplementedError):  # pragma: no cover - platform dependent
        pytest.skip("this platform does not allow creating a symlink")

    coverage = read_analysis_coverage(tmp_path)

    assert coverage.missing == ("billing", "orders")
    assert coverage.written == {}
    assert any("could not be measured" in note for note in coverage.notices)


def test_entries_that_ignore_the_convention_are_noticed_not_counted(tmp_path: Path) -> None:
    _estate(tmp_path)
    directory = tmp_path / contract.ANALYSIS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "orders").write_text("no extension", encoding="utf-8")
    (directory / "billing.markdown").write_text("wrong extension", encoding="utf-8")
    (directory / ".md").write_text("no stem at all", encoding="utf-8")

    coverage = read_analysis_coverage(tmp_path)

    assert coverage.missing == ("billing", "orders")
    assert coverage.unexpected == ()
    notice = next(note for note in coverage.notices if "<group>.md convention" in note)
    assert "3 entry/entries" in notice


def test_a_notice_names_a_bounded_number_of_entries_and_states_the_total(
    tmp_path: Path,
) -> None:
    _estate(tmp_path)
    directory = tmp_path / contract.ANALYSIS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(MAX_NAMED + 5):
        (directory / f"scratch{index:03d}.txt").write_text("x", encoding="utf-8")

    coverage = read_analysis_coverage(tmp_path)

    notice = next(note for note in coverage.notices if "<group>.md convention" in note)
    assert f"{MAX_NAMED + 5} entry/entries" in notice
    assert "(+5 more)" in notice


def test_nothing_raises_on_a_completely_empty_workspace(tmp_path: Path) -> None:
    coverage = read_analysis_coverage(tmp_path)

    assert coverage == AnalysisCoverage(groups=(), notices=coverage.notices)


# --------------------------------------------------------------------------
# Determinism and the artifact
# --------------------------------------------------------------------------


def test_the_report_is_byte_stable_regardless_of_shard_order(tmp_path: Path) -> None:
    """Identical evidence in a different order must produce identical bytes.

    ``coverage.py`` records a measured case where a fold-only sort made an
    artifact differ between processes; every key here is sorted on the exact
    string for that reason.
    """

    rows = [
        _unit("orders/LoadCustomer", group="orders"),
        _unit("billing/InvoiceSync", group="billing"),
        _unit("stock/Replenish", group="stock"),
    ]
    first = tmp_path / "first"
    second = tmp_path / "second"
    _navigation(first, rows)
    _navigation(second, list(reversed(rows)))
    for staging in (first, second):
        _analysis(staging, "orders")
        _analysis(staging, "billing", size=10)

    write_analysis_artifact(first, read_analysis_coverage(first))
    write_analysis_artifact(second, read_analysis_coverage(second))

    assert (first / ANALYSIS_ARTIFACT).read_bytes() == (second / ANALYSIS_ARTIFACT).read_bytes()


def test_two_reads_of_one_workspace_agree(tmp_path: Path) -> None:
    _estate(tmp_path)
    _analysis(tmp_path, "orders")

    assert read_analysis_coverage(tmp_path) == read_analysis_coverage(tmp_path)


def test_the_artifact_lands_in_the_audit_record_out_of_client_reach(tmp_path: Path) -> None:
    _estate(tmp_path)
    _analysis(tmp_path, "orders", size=120)
    coverage = read_analysis_coverage(tmp_path)

    path = write_analysis_artifact(tmp_path, coverage)

    assert path == tmp_path / ANALYSIS_ARTIFACT
    assert ANALYSIS_ARTIFACT.parts[0].startswith("_")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["group_total"] == 2
    assert payload["missing"] == ["billing"]
    assert payload["thin"] == ["orders"]
    assert payload["written"] == {"orders": 120}
    assert payload["minimum_bytes"] == MIN_ANALYSIS_BYTES
    assert "facts/navigation/units" in payload["population_source"]
    assert "notes/analysis" == payload["analysis_dir"]


def test_writing_the_artifact_is_best_effort(tmp_path: Path) -> None:
    """An audit artifact that can fail a run would be a second gate."""

    _estate(tmp_path)
    coverage = read_analysis_coverage(tmp_path)
    (tmp_path / ANALYSIS_ARTIFACT.parts[0]).write_text("not a directory", encoding="utf-8")

    assert write_analysis_artifact(tmp_path, coverage) is None


def test_the_artifact_is_ascii_even_when_the_estate_is_not(tmp_path: Path) -> None:
    # Escaped so this source file stays ASCII while the staged names do not.
    group = "R\u00e9gion"
    _navigation(tmp_path, [_unit("a/One", group=group)])
    _analysis(tmp_path, group)

    path = write_analysis_artifact(tmp_path, read_analysis_coverage(tmp_path))

    assert path is not None
    path.read_text(encoding="ascii")


def test_the_log_line_reads_without_this_module_open(tmp_path: Path) -> None:
    _estate(tmp_path)
    _analysis(tmp_path, "orders", size=40)

    line = read_analysis_coverage(tmp_path).log_line()

    assert "1 of 2 group(s) have an analysis note" in line
    assert "1 with none" in line
    assert f"1 under {MIN_ANALYSIS_BYTES} bytes" in line


def test_no_notice_or_field_carries_a_files_contents(tmp_path: Path) -> None:
    """Messages are a relative path, a group name or a count. The analysis an
    agent wrote is prose about a client's estate and never reaches a log line."""

    secret = "the dispatcher connection string was pasted into the note"
    _estate(tmp_path)
    path = tmp_path / contract.ANALYSIS_DIR / "orders.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret, encoding="utf-8")

    coverage = read_analysis_coverage(tmp_path)
    rendered = json.dumps(coverage.to_dict()) + coverage.log_line()

    assert secret not in rendered


def test_nothing_in_the_module_reads_an_analysis_files_text() -> None:
    """Byte counts, file existence and group names only. Plan section 8 forbids
    failing a run because prose uses a slightly different name, and a module
    that opens the note is one refactor away from judging its wording.

    Resolved from the module object, never from the working directory: a guard
    that silently reads nothing when pytest is invoked from elsewhere guards
    nothing."""

    from mcp_server.providers.agent_workspace import analysis

    source = Path(analysis.__file__).read_text(encoding="utf-8")
    body = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))

    # ``_read_jsonl`` reads the navigation shards, which are evidence, not
    # notes -- so the check is on the analysis directory specifically.
    assert "ANALYSIS_DIR" in body
    for forbidden in ("import re", "re.compile", ".search(", ".findall("):
        assert forbidden not in body, forbidden
    assert body.count("read_text(") == 1, "only the navigation shard reader may read a file"
