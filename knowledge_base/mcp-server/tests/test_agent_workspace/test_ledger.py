"""Slice 2: the durable coverage ledger, and the rule that makes it binding.

The failure being corrected: a run over a 142-design estate opened six design
files, classified nothing, and was published as complete. An advisory count
would not have stopped it; a register whose population the agent cannot shrink
does.

Two properties are pinned hardest here, because losing either one turns the
register back into an advisory count:

* the POPULATION is recomputed from read-only evidence, so deleting the roster
  file -- which lives under agent-writable ``notes/`` -- changes nothing;
* nothing on the agent-input path raises, so one typo cannot fail a run.

All fixture names are invented. No client identifier appears in this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp_server.providers.agent_workspace import contract
from mcp_server.providers.agent_workspace.ledger import (
    DISPOSITIONS,
    LEDGER_ARTIFACT,
    MAX_MALFORMED,
    MIN_REASON_CHARS,
    CandidateRecord,
    read_ledger,
    roster_from_workspace,
    seed_roster,
    write_summary,
)


# --------------------------------------------------------------------------
# Fixture construction
# --------------------------------------------------------------------------


def _unit(unit_id: str, *, display_name: str = "", group: str = "orders", **extra) -> dict:
    row = {
        "view": "units",
        "unit_id": unit_id,
        "display_name": display_name or unit_id.rsplit("/", 1)[-1],
        "kind": "job",
        "group": group,
        "component_count": 12,
        "entry_point": False,
        "evidence": f"scan/jobs/{group}/{unit_id.rsplit('/', 1)[-1]}.json",
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
            _unit("orders/LoadCustomer", group="orders", entry_point=True),
            _unit("orders/LoadCustomers", group="orders"),
            _unit("billing/InvoiceSync", group="billing", component_count=41),
        ],
    )


def _log(staging: Path, lines: list[str]) -> None:
    path = staging / contract.COVERAGE_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def _classify(unit_id: str, disposition: str, **extra) -> str:
    payload: dict = {"unit_id": unit_id, "disposition": disposition}
    payload.update(extra)
    return json.dumps(payload)


# --------------------------------------------------------------------------
# The population
# --------------------------------------------------------------------------


def test_population_is_every_parsed_design_with_its_routing_facts(tmp_path):
    _estate(tmp_path)

    population = roster_from_workspace(tmp_path)

    assert [record.unit_id for record in population] == [
        "billing/InvoiceSync",
        "orders/LoadCustomer",
        "orders/LoadCustomers",
    ]
    by_id = {record.unit_id: record for record in population}
    assert by_id["orders/LoadCustomer"].entry_point is True
    assert by_id["billing/InvoiceSync"].component_count == 41
    assert by_id["billing/InvoiceSync"].evidence == "scan/jobs/billing/InvoiceSync.json"
    assert isinstance(population[0], CandidateRecord)


def test_deleting_the_roster_file_does_not_shrink_what_the_run_owes(tmp_path):
    """The one architectural rule. ``notes/`` is agent-writable; this is not.

    The roster is a convenience. If an agent could shrink the population by
    editing it, the register would be a control file the controlled party
    owns, which controls nothing.
    """

    _estate(tmp_path)
    assert seed_roster(tmp_path) == 3
    before = read_ledger(tmp_path)

    (tmp_path / contract.COVERAGE_ROSTER).unlink()
    assert not (tmp_path / contract.COVERAGE_ROSTER).exists()

    after = read_ledger(tmp_path)
    assert [record.unit_id for record in after.population] == [
        record.unit_id for record in before.population
    ]
    assert after.unreviewed == before.unreviewed
    assert len(after.unreviewed) == 3


def test_truncating_the_roster_to_one_row_does_not_shrink_the_population(tmp_path):
    _estate(tmp_path)
    seed_roster(tmp_path)

    roster = tmp_path / contract.COVERAGE_ROSTER
    kept = roster.read_text(encoding="utf-8").splitlines()[0]
    roster.write_text(kept + "\n", encoding="utf-8")

    report = read_ledger(tmp_path)
    assert len(report.population) == 3
    assert len(report.unreviewed) == 3


def test_a_design_and_its_plural_stay_two_candidates(tmp_path):
    """``LoadCustomer`` and ``LoadCustomers`` are separately-scheduled designs."""

    _estate(tmp_path)
    report = read_ledger(tmp_path)

    assert "orders/LoadCustomer" in report.unreviewed
    assert "orders/LoadCustomers" in report.unreviewed


def test_groups_differing_only_in_case_stay_two_groups(tmp_path):
    """Case folding would merge them, and they are two real groups."""

    _navigation(
        tmp_path,
        [
            _unit("a/Extract", group="Billing"),
            _unit("b/Extract", group="billing"),
        ],
    )

    population = roster_from_workspace(tmp_path)
    assert {record.group for record in population} == {"Billing", "billing"}
    assert len(population) == 2


def test_a_missing_navigation_view_is_an_empty_population_not_an_error(tmp_path):
    assert roster_from_workspace(tmp_path) == ()
    report = read_ledger(tmp_path)
    assert report.population == ()
    assert report.unreviewed == ()
    assert report.counts == dict.fromkeys(DISPOSITIONS, 0)


def test_rows_are_read_across_every_shard(tmp_path):
    _navigation(tmp_path, [_unit("orders/One")], shard="part_001.jsonl")
    _navigation(tmp_path, [_unit("orders/Two")], shard="part_002.jsonl")

    assert [record.unit_id for record in roster_from_workspace(tmp_path)] == [
        "orders/One",
        "orders/Two",
    ]


# --------------------------------------------------------------------------
# The roster file
# --------------------------------------------------------------------------


def test_seed_roster_writes_one_json_object_per_line(tmp_path):
    _estate(tmp_path)

    assert seed_roster(tmp_path) == 3

    lines = (tmp_path / contract.COVERAGE_ROSTER).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    rows = [json.loads(line) for line in lines]
    assert all(isinstance(row, dict) for row in rows)
    assert rows[0]["unit_id"] == "billing/InvoiceSync"
    assert set(rows[0]) == {
        "unit_id",
        "display_name",
        "group",
        "kind",
        "entry_point",
        "component_count",
        "evidence",
    }


def test_the_roster_is_byte_stable_regardless_of_shard_order(tmp_path):
    """Identical evidence in a different order must produce identical bytes."""

    first = tmp_path / "first"
    second = tmp_path / "second"
    rows = [
        _unit("orders/LoadCustomer", group="orders"),
        _unit("orders/LoadCustomers", group="orders"),
        _unit("billing/InvoiceSync", group="billing"),
    ]
    _navigation(first, rows)
    _navigation(second, list(reversed(rows)))

    seed_roster(first)
    seed_roster(second)

    assert (first / contract.COVERAGE_ROSTER).read_bytes() == (
        second / contract.COVERAGE_ROSTER
    ).read_bytes()


def test_seeding_twice_rewrites_the_same_bytes(tmp_path):
    _estate(tmp_path)
    seed_roster(tmp_path)
    once = (tmp_path / contract.COVERAGE_ROSTER).read_bytes()
    seed_roster(tmp_path)
    assert (tmp_path / contract.COVERAGE_ROSTER).read_bytes() == once


def test_seed_roster_on_an_empty_estate_writes_an_empty_file(tmp_path):
    assert seed_roster(tmp_path) == 0
    assert (tmp_path / contract.COVERAGE_ROSTER).read_text(encoding="utf-8") == ""


# --------------------------------------------------------------------------
# Reading the agent's log
# --------------------------------------------------------------------------


def test_an_absent_log_leaves_every_candidate_unreviewed(tmp_path):
    _estate(tmp_path)

    report = read_ledger(tmp_path)

    assert len(report.unreviewed) == 3
    assert report.classified == {}
    assert report.malformed == ()
    assert report.counts == dict.fromkeys(DISPOSITIONS, 0)


def test_classified_candidates_leave_the_unreviewed_set(tmp_path):
    _estate(tmp_path)
    _log(
        tmp_path,
        [
            _classify("orders/LoadCustomer", "material", integration_ids=["int-intake"]),
            _classify("billing/InvoiceSync", "excluded", reason="decommissioned in 2024"),
        ],
    )

    report = read_ledger(tmp_path)

    assert report.unreviewed == ("orders/LoadCustomers",)
    assert set(report.classified) == {"orders/LoadCustomer", "billing/InvoiceSync"}
    assert report.counts["material"] == 1
    assert report.counts["excluded"] == 1


def test_the_last_line_for_a_unit_wins(tmp_path):
    """Revision is an append, not a read-modify-write of a giant object."""

    _estate(tmp_path)
    _log(
        tmp_path,
        [
            _classify("orders/LoadCustomer", "utility", reason="looked like a helper job"),
            _classify("orders/LoadCustomer", "material", integration_ids=["int-intake"]),
        ],
    )

    report = read_ledger(tmp_path)

    entry = report.classified["orders/LoadCustomer"]
    assert entry.disposition == "material"
    assert entry.line_number == 2
    assert report.counts["material"] == 1
    assert report.counts["utility"] == 0


def test_an_unknown_unit_id_is_reported_and_credits_nothing(tmp_path):
    """A typo'd id must not clear an obligation it does not name."""

    _estate(tmp_path)
    _log(tmp_path, [_classify("orders/LoadCustmer", "material", integration_ids=["int-a"])])

    report = read_ledger(tmp_path)

    assert report.unknown == ("orders/LoadCustmer",)
    assert len(report.unreviewed) == 3


# --------------------------------------------------------------------------
# Malformed input: recorded, never raised
# --------------------------------------------------------------------------


def test_malformed_lines_are_recorded_and_the_good_ones_survive(tmp_path):
    _estate(tmp_path)
    _log(
        tmp_path,
        [
            "{not json at all",
            '["a", "list", "not an object"]',
            '{"disposition": "material"}',
            _classify("orders/LoadCustomer", "importnat"),
            "",
            "   ",
            _classify("billing/InvoiceSync", "operational", reason="monitoring job, no data"),
        ],
    )

    report = read_ledger(tmp_path)

    assert set(report.classified) == {"billing/InvoiceSync"}
    assert len(report.malformed) == 4
    assert report.malformed[0] == "line 1: not valid JSON"
    assert report.malformed[1] == "line 2: not a JSON object"
    assert report.malformed[2] == "line 3: no unit_id"
    assert "line 4: unknown disposition 'importnat'" in report.malformed[3]


def test_invalid_utf8_does_not_raise_and_other_lines_survive(tmp_path):
    _estate(tmp_path)
    path = tmp_path / contract.COVERAGE_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    good = _classify("orders/LoadCustomer", "material", integration_ids=["int-intake"])
    path.write_bytes(
        b'{"unit_id": "\xff\xfe", "disposition": "material"}\n' + good.encode() + b"\n"
    )

    report = read_ledger(tmp_path)

    assert "orders/LoadCustomer" in report.classified
    assert any("not valid UTF-8" in note for note in report.malformed)


def test_a_non_list_integration_ids_is_noted_not_fatal(tmp_path):
    _estate(tmp_path)
    _log(tmp_path, [_classify("orders/LoadCustomer", "material", integration_ids={"a": 1})])

    report = read_ledger(tmp_path)

    assert "orders/LoadCustomer" in report.classified
    assert report.classified["orders/LoadCustomer"].integration_ids == ()
    assert any("integration_ids is not a list" in note for note in report.malformed)
    # The link is genuinely missing, so the obligation still stands.
    assert report.missing_link == ("orders/LoadCustomer",)


def test_a_bare_string_integration_id_is_accepted(tmp_path):
    """A predictable slip. Rejecting it would report a linked candidate as unlinked."""

    _estate(tmp_path)
    _log(
        tmp_path,
        [
            _classify("orders/LoadCustomer", "material", integration_ids="int-intake"),
            _classify("orders/LoadCustomers", "material", integration_id="int-delta"),
        ],
    )

    report = read_ledger(tmp_path)

    assert report.classified["orders/LoadCustomer"].integration_ids == ("int-intake",)
    assert report.classified["orders/LoadCustomers"].integration_ids == ("int-delta",)
    assert report.missing_link == ()


def test_integration_ids_are_deduplicated_in_first_seen_order(tmp_path):
    """Never a set. Set iteration order is not stable across processes, and this
    tuple reaches the artifact and, through it, anything bound to the run."""

    _estate(tmp_path)
    _log(
        tmp_path,
        [
            _classify(
                "orders/LoadCustomer",
                "material",
                integration_ids=["int-b", "int-a", "int-b", "", None, ["nested"]],
            )
        ],
    )

    report = read_ledger(tmp_path)

    assert report.classified["orders/LoadCustomer"].integration_ids == ("int-b", "int-a")


def test_the_malformed_list_is_bounded_and_says_how_many_more(tmp_path):
    _estate(tmp_path)
    _log(tmp_path, ["{broken"] * (MAX_MALFORMED + 7))

    report = read_ledger(tmp_path)

    assert len(report.malformed) == MAX_MALFORMED + 1
    assert report.malformed[-1] == "... and 7 more malformed line(s) were skipped"


def test_a_hostile_disposition_value_cannot_produce_an_unbounded_diagnostic(tmp_path):
    _estate(tmp_path)
    _log(tmp_path, [_classify("orders/LoadCustomer", "x" * 5000)])

    report = read_ledger(tmp_path)

    assert len(report.malformed) == 1
    assert len(report.malformed[0]) < 200


# --------------------------------------------------------------------------
# What the dispositions owe
# --------------------------------------------------------------------------


def test_a_disposition_that_ends_an_obligation_needs_a_concrete_reason(tmp_path):
    _estate(tmp_path)
    _log(
        tmp_path,
        [
            _classify("orders/LoadCustomer", "excluded", reason="n/a"),
            _classify("orders/LoadCustomers", "test-or-obsolete", reason=""),
            _classify("billing/InvoiceSync", "utility", reason="date helper, no business journey"),
        ],
    )

    report = read_ledger(tmp_path)

    assert report.missing_reason == ("orders/LoadCustomer", "orders/LoadCustomers")
    assert "billing/InvoiceSync" not in report.missing_reason
    assert MIN_REASON_CHARS > len("n/a")


def test_work_asserting_dispositions_need_no_reason(tmp_path):
    _estate(tmp_path)
    _log(
        tmp_path,
        [
            _classify("orders/LoadCustomer", "material", integration_ids=["int-intake"]),
            _classify("orders/LoadCustomers", "shared-component"),
        ],
    )

    report = read_ledger(tmp_path)

    assert report.missing_reason == ()


def test_material_must_name_the_integration_that_covers_it(tmp_path):
    _estate(tmp_path)
    _log(
        tmp_path,
        [
            _classify("orders/LoadCustomer", "material"),
            _classify("orders/LoadCustomers", "material", integration_ids=["int-delta"]),
            _classify("billing/InvoiceSync", "shared-component"),
        ],
    )

    report = read_ledger(tmp_path)

    assert report.missing_link == ("orders/LoadCustomer",)


def test_unresolved_is_a_legitimate_answer_that_is_still_reported(tmp_path):
    _estate(tmp_path)
    _log(
        tmp_path,
        [
            _classify(
                "orders/LoadCustomer",
                "unresolved",
                reason="calls a design the scan never resolved",
            )
        ],
    )

    report = read_ledger(tmp_path)

    assert report.unresolved == ("orders/LoadCustomer",)
    assert report.counts["unresolved"] == 1
    # Unresolved and unreviewed are different states and are counted apart.
    assert "orders/LoadCustomer" not in report.unreviewed
    assert len(report.unreviewed) == 2


def test_counts_always_carry_every_disposition(tmp_path):
    _estate(tmp_path)
    _log(tmp_path, [_classify("orders/LoadCustomer", "material", integration_ids=["int-a"])])

    report = read_ledger(tmp_path)

    assert set(report.counts) == set(DISPOSITIONS)
    assert len(DISPOSITIONS) == 7
    assert report.counts["material"] == 1
    assert all(report.counts[name] == 0 for name in DISPOSITIONS if name != "material")


# --------------------------------------------------------------------------
# Digest and artifact
# --------------------------------------------------------------------------


def test_the_digest_identifies_the_classification_state_only(tmp_path):
    """Rewording a reason must not invalidate a receipt bound to the digest."""

    _estate(tmp_path)
    _log(tmp_path, [_classify("orders/LoadCustomer", "excluded", reason="retired last year")])
    first = read_ledger(tmp_path).digest()

    _log(
        tmp_path,
        [_classify("orders/LoadCustomer", "excluded", reason="decommissioned, no schedule")],
    )
    assert read_ledger(tmp_path).digest() == first

    _log(tmp_path, [_classify("orders/LoadCustomer", "material", integration_ids=["int-a"])])
    assert read_ledger(tmp_path).digest() != first


def test_the_digest_is_stable_across_reads_and_is_a_sha256(tmp_path):
    _estate(tmp_path)
    _log(
        tmp_path,
        [
            _classify("orders/LoadCustomer", "material", integration_ids=["int-a"]),
            _classify("billing/InvoiceSync", "operational", reason="alerting job, no payload"),
        ],
    )

    digest = read_ledger(tmp_path).digest()
    assert read_ledger(tmp_path).digest() == digest
    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")


def test_write_summary_lands_in_the_audit_record_out_of_client_reach(tmp_path):
    _estate(tmp_path)
    _log(tmp_path, [_classify("orders/LoadCustomer", "material", integration_ids=["int-a"])])
    report = read_ledger(tmp_path)

    path = write_summary(tmp_path, report)

    assert path == tmp_path / LEDGER_ARTIFACT
    assert LEDGER_ARTIFACT.parts[0].startswith("_")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["digest"] == report.digest()
    assert payload["population_total"] == 3
    assert payload["unreviewed"] == list(report.unreviewed)
    assert set(payload["counts"]) == set(DISPOSITIONS)
    assert "facts/navigation/units" in payload["population_source"]
    assert payload["classifications"][0]["unit_id"] == "orders/LoadCustomer"


def test_write_summary_is_best_effort(tmp_path):
    """An audit artifact that can fail a run would be a second gate."""

    _estate(tmp_path)
    report = read_ledger(tmp_path)
    (tmp_path / LEDGER_ARTIFACT.parts[0]).write_text("not a directory", encoding="utf-8")

    assert write_summary(tmp_path, report) is None


def test_the_artifact_is_ascii_even_when_the_estate_is_not(tmp_path):
    # Escaped so this source file stays ASCII while the staged names do not.
    name = "orders/Rel\u00e8ve"
    _navigation(tmp_path, [_unit(name, group="orders")])
    _log(tmp_path, [_classify(name, "excluded", reason="retired r\u00e9gion feed")])

    path = write_summary(tmp_path, read_ledger(tmp_path))

    assert path is not None
    path.read_text(encoding="ascii")


def test_a_long_reason_is_clipped_in_the_artifact_but_not_in_the_report(tmp_path):
    _estate(tmp_path)
    reason = "y" * 2000
    _log(tmp_path, [_classify("orders/LoadCustomer", "excluded", reason=reason)])
    report = read_ledger(tmp_path)

    assert report.classified["orders/LoadCustomer"].reason == reason
    payload = report.to_dict()
    assert len(payload["classifications"][0]["reason"]) < 500


def test_the_log_line_reads_without_this_module_open(tmp_path):
    _estate(tmp_path)
    _log(tmp_path, [_classify("orders/LoadCustomer", "material", integration_ids=["int-a"])])

    line = read_ledger(tmp_path).log_line()

    assert "1 of 3 candidates classified" in line
    assert "2 unreviewed" in line
