from __future__ import annotations

import json
import logging
import threading

from mission_control.licensing.ledger import IssuedLicenceLedger, JsonlLedger, LedgerEntry


def _entry(lid: str, client: str = "acme") -> LedgerEntry:
    return LedgerEntry(licence_id=lid, client=client, plan="basic",
                       period_start="2026-10-01T00:00:00+00:00",
                       period_end="2026-11-01T00:00:00+00:00",
                       issued_at="2026-09-28T00:00:00+00:00", issued_by=None, kid="k",
                       push_ok=None, push_status_code=None)


def test_append_writes_one_json_object_per_line(tmp_path):
    ledger = JsonlLedger(tmp_path / "licences")
    assert isinstance(ledger, IssuedLicenceLedger)
    ledger.append(_entry("a"))
    ledger.append(_entry("b", client="other"))
    lines = ledger.path.read_text().splitlines()
    assert [json.loads(line)["licence_id"] for line in lines] == ["a", "b"]
    assert oct(ledger.path.stat().st_mode & 0o777) == "0o640"


def test_read_is_newest_first_and_filtered(tmp_path):
    ledger = JsonlLedger(tmp_path)
    for lid, client in [("1", "acme"), ("2", "other"), ("3", "acme")]:
        ledger.append(_entry(lid, client))
    assert [e.licence_id for e in ledger.list_for_client("acme")] == ["3", "1"]
    assert ledger.list_for_client("nobody") == []


def test_missing_file_reads_empty(tmp_path):
    assert JsonlLedger(tmp_path / "nope").list_for_client("acme") == []


def test_corrupt_lines_are_skipped_and_logged(tmp_path, caplog):
    ledger = JsonlLedger(tmp_path)
    ledger.append(_entry("1"))
    with ledger.path.open("a") as fh:
        fh.write("{not json\n")
        fh.write("[1, 2]\n")
        fh.write('{"client": "acme"}\n')  # missing licence_id
        fh.write("\n")
    ledger.append(_entry("2"))
    with caplog.at_level(logging.WARNING, logger="mission_control.licensing.ledger"):
        entries = ledger.list_for_client("acme")
    assert [e.licence_id for e in entries] == ["2", "1"]
    assert sum("skipped" in r.message for r in caplog.records) == 3


def test_concurrent_appends_do_not_interleave(tmp_path):
    ledger = JsonlLedger(tmp_path)
    threads = [threading.Thread(target=lambda i=i: [ledger.append(_entry(f"{i}-{n}"))
                                                     for n in range(25)]) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = ledger.path.read_text().splitlines()
    assert len(lines) == 200
    assert all(json.loads(line)["client"] == "acme" for line in lines)


def test_entry_has_no_token_field():
    assert "token" not in _entry("x").to_dict()
