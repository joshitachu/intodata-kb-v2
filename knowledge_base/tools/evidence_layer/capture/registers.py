"""Capture configuration registers (xlsx / csv table exports).

Why this matters: when an integration platform keeps its routing in database
tables rather than in code, a code-only scan cannot reconstruct the landscape
at all. On one real export the sender/receiver/mapping/endpoint wiring for
every ESB interface existed *only* in a table dump -- 70 workbooks the scanner
never opened.

Redaction is not optional here and runs before anything else. These files
bypassed every existing credential filter precisely because nothing ever opened
them, and on real data they carry working production credentials: API keys,
Basic-auth headers, JDBC passwords, and a user table whose "hashed" passwords
were a reversible encoding. A value classified as a credential never leaves
this module -- not into the layer, not into a log, not into a report. Only its
presence, column and length are recorded, which is what a reviewer needs in
order to act.

Table shapes are inferred, not configured: a fixed table-name list would work
on exactly one platform.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from ..models import (
    ArtifactRecord, Confidence, InterfaceRecord, Unresolved,
)

# ---------------------------------------------------------------------------
# Redaction -- first, unconditionally.
# ---------------------------------------------------------------------------

_CRED_COLUMN = re.compile(
    r"(pass(word|wd)?|secret|token|api[_-]?key|apikey|authoriz|bearer|salt"
    r"|credential|wachtwoord|_pw$|^pw$|privatekey|clientsecret)",
    re.IGNORECASE,
)
_CRED_VALUE = re.compile(
    r"(Basic\s+[A-Za-z0-9+/=]{16,}|Bearer\s+[A-Za-z0-9._\-]{16,}"
    r"|password=[^;&\s]+|pwd=[^;&\s]+|:\/\/[^:\/\s]+:[^@\s]+@)",
    re.IGNORECASE,
)
_REDACTED = "[CREDENTIAL_REDACTED]"


@dataclass(frozen=True)
class CredentialFinding:
    """A credential was present. The value is not carried."""

    source: str
    table: str
    column: str
    row_index: int
    value_length: int
    detected_by: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source, "table": self.table, "column": self.column,
            "row_index": self.row_index, "value_length": self.value_length,
            "detected_by": self.detected_by,
        }


def _redact(
    table: str, source: str, header: list[str], rows: list[list[Any]]
) -> tuple[list[list[Any]], list[CredentialFinding]]:
    findings: list[CredentialFinding] = []
    cred_cols = {
        i for i, h in enumerate(header) if h and _CRED_COLUMN.search(str(h))
    }
    out: list[list[Any]] = []
    for r_i, row in enumerate(rows):
        new_row: list[Any] = []
        for c_i, cell in enumerate(row):
            text = "" if cell is None else str(cell)
            by = ""
            if c_i in cred_cols and text:
                by = "column_name"
            elif text and _CRED_VALUE.search(text):
                by = "value_shape"
            if by:
                findings.append(CredentialFinding(
                    source=source, table=table,
                    column=str(header[c_i]) if c_i < len(header) else f"col{c_i}",
                    row_index=r_i, value_length=len(text), detected_by=by,
                ))
                new_row.append(_REDACTED)
            else:
                new_row.append(cell)
        out.append(new_row)
    return out, findings


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _read_workbook(path: Path) -> Iterator[tuple[str, list[str], list[list[Any]]]]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            if not rows:
                continue
            header = [("" if h is None else str(h)) for h in rows[0]]
            yield ws.title, header, rows[1:]
    finally:
        wb.close()


def _read_csv(path: Path) -> Iterator[tuple[str, list[str], list[list[Any]]]]:
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        reader = list(csv.reader(fh))
    if reader:
        yield path.stem, reader[0], reader[1:]


# ---------------------------------------------------------------------------
# Shape inference
# ---------------------------------------------------------------------------

# Anchored rather than substring: a bare `to` or `from` matches half the
# columns in any schema. Measured cost of getting this wrong on real data --
# `EmailFrom` / `EmailAddressTo` in a 15,476-row mail queue turned that one
# table into 15,476 "interfaces", swamping the 22 real ones.
_SENDER = re.compile(
    r"(messagesender|^sender|_sender|sourcesystem|^source_?system|^from_?system"
    r"|producer|publisher|^van_?systeem)", re.I)
_RECEIVER = re.compile(
    r"(messagereceiver|^receiver|_receiver|targetsystem|^target_?system"
    r"|^to_?system|consumer|subscriber|^naar_?systeem|destinationsystem)", re.I)
_MAPPING = re.compile(r"(mapping|transform|xsl|template)", re.I)
_NAME = re.compile(r"(name|naam|label|exchange|interface|title)", re.I)
_STEPS = re.compile(r"(step|processflow|sequence|chain)", re.I)
_ENDPOINT = re.compile(r"(url|uri|endpoint|queue|topic|koppelvlak)", re.I)

# A field that is an address, not a system identity.
_ADDRESSY = re.compile(r"(e-?mail|address|adres|phone|telefoon|mailbox)", re.I)

# A routing register names a handful of systems across a modest number of
# rows. A transactional table does neither, and that difference is what
# separates the two without hardcoding table names.
_MAX_DISTINCT_SYSTEMS = 60
_MAX_REGISTER_ROWS = 5000


# A key column is never the human-readable label, even when its name happens
# to contain a label-ish word: `esb_exchange_id` matches /exchange/ and would
# otherwise be picked as the exchange's name.
_ID_COLUMN = re.compile(r"(^id$|_id$|guid|uuid)", re.I)


def _idx(
    header: list[str], pattern: re.Pattern[str], *, skip_ids: bool = False
) -> int | None:
    fallback: int | None = None
    for i, h in enumerate(header):
        if not h or not pattern.search(h):
            continue
        if skip_ids and _ID_COLUMN.search(h):
            if fallback is None:
                fallback = i
            continue
        return i
    return fallback


@dataclass
class _Table:
    source: str
    name: str
    header: list[str]
    rows: list[list[Any]]

    def pk_column(self) -> str:
        return self.header[0] if self.header else ""

    def lookup(self) -> dict[str, str]:
        """id -> best human-readable label for that row."""
        if not self.header:
            return {}
        label_i = _idx(self.header, _NAME)
        if label_i is None:
            label_i = 1 if len(self.header) > 1 else 0
        out: dict[str, str] = {}
        for row in self.rows:
            if not row:
                continue
            key = "" if row[0] is None else str(row[0]).strip()
            if not key:
                continue
            val = row[label_i] if label_i < len(row) else None
            out[key] = ("" if val is None else str(val).strip()) or key
        return out


def _resolve(
    tables: dict[str, _Table], owner: str, values: set[str]
) -> dict[str, str]:
    """Resolve a foreign-key column to its target table's label lookup.

    Resolved by VALUE OVERLAP, not by column name. Name-based inference only
    works where the column repeats the target table's name, and real schemas
    routinely do not: a column called ``messageSender`` holding ids from a
    ``systems`` table is invisible to it, which leaves the whole register
    reading as raw integers.

    Overlap is robust instead: whichever table's primary key contains (almost)
    all of this column's values is the referent. Ties go to the smallest
    table, since a lookup table is small and a transaction table is not. A
    miss leaves the raw id in place rather than inventing a name.
    """
    values = {v for v in values if v}
    if not values:
        return {}

    best: tuple[float, int, str] | None = None
    for tname, t in tables.items():
        if tname == owner or not t.header:
            continue
        keys = {
            str(r[0]).strip() for r in t.rows
            if r and r[0] not in (None, "")
        }
        if not keys:
            continue
        covered = len(values & keys) / len(values)
        if covered < 0.8:
            continue
        candidate = (covered, -len(keys), tname)
        if best is None or candidate > best:
            best = candidate
    return tables[best[2]].lookup() if best else {}


def capture_registers(
    root: Path, repo_root: Path
) -> tuple[list[ArtifactRecord], list[InterfaceRecord], list[CredentialFinding],
           list[Unresolved]]:
    """Read every table export under ``root``; redact, inventory, then infer."""
    artifacts: list[ArtifactRecord] = []
    interfaces: list[InterfaceRecord] = []
    creds: list[CredentialFinding] = []
    unresolved: list[Unresolved] = []
    tables: dict[str, _Table] = {}

    candidates = [
        p for p in sorted(root.rglob("*"))
        if p.is_file() and p.suffix.lower() in (".xlsx", ".xlsm", ".csv")
        and not any(part.startswith((".", "~$")) for part in p.parts)
    ]

    for path in candidates:
        rel = path.relative_to(repo_root).as_posix()
        reader = _read_csv if path.suffix.lower() == ".csv" else _read_workbook
        try:
            sheets = list(reader(path))
        except Exception as exc:  # a corrupt workbook is data, not a crash
            unresolved.append(Unresolved("unreadable_register", rel, str(exc), rel))
            continue

        for sheet, header, rows in sheets:
            if not header or not any(header):
                continue
            safe_rows, findings = _redact(sheet, rel, header, rows)
            creds.extend(findings)
            tname = f"{path.stem}::{sheet}" if sheet != path.stem else path.stem
            tables[tname] = _Table(rel, tname, header, safe_rows)
            artifacts.append(ArtifactRecord(
                artifact_id=f"register:{rel}#{sheet}",
                name=tname,
                kind="register",
                path=rel,
                purpose=f"{len(safe_rows)} rows",
                component_types=tuple(h for h in header if h),
                notes=("contains redacted credential columns"
                       if findings else ""),
            ))

    # Interface-register shape. Four conditions, all necessary: a table that
    # merely has from/to columns is usually a transaction log, not a register.
    for tname, t in tables.items():
        s_i, r_i = _idx(t.header, _SENDER), _idx(t.header, _RECEIVER)
        if s_i is None or r_i is None:
            continue
        if _ADDRESSY.search(t.header[s_i]) or _ADDRESSY.search(t.header[r_i]):
            continue
        if len(t.rows) > _MAX_REGISTER_ROWS:
            unresolved.append(Unresolved(
                "register_shape_rejected", tname,
                f"{len(t.rows)} rows exceeds the {_MAX_REGISTER_ROWS}-row "
                "ceiling for a routing register; treated as transactional",
                t.source,
            ))
            continue
        distinct_s = {str(r[s_i]).strip() for r in t.rows
                      if len(r) > s_i and r[s_i] not in (None, "")}
        distinct_r = {str(r[r_i]).strip() for r in t.rows
                      if len(r) > r_i and r[r_i] not in (None, "")}
        if (len(distinct_s) > _MAX_DISTINCT_SYSTEMS
                or len(distinct_r) > _MAX_DISTINCT_SYSTEMS):
            unresolved.append(Unresolved(
                "register_shape_rejected", tname,
                f"{len(distinct_s)} distinct senders / {len(distinct_r)} "
                "receivers looks like participant data, not a system register",
                t.source,
            ))
            continue

        m_i = _idx(t.header, _MAPPING)
        n_i = _idx(t.header, _NAME, skip_ids=True)
        st_i = _idx(t.header, _STEPS)
        e_i = _idx(t.header, _ENDPOINT)
        # A register also says something ABOUT the hop -- its name, its
        # mapping, its steps or its endpoint. A table with only two system
        # columns and nothing else is not describing an interface.
        if all(i is None for i in (m_i, n_i, st_i, e_i)):
            unresolved.append(Unresolved(
                "register_shape_rejected", tname,
                "sender and receiver columns present but nothing describing "
                "the hop (no name, mapping, steps or endpoint column)",
                t.source,
            ))
            continue

        def cell(row: list[Any], i: int | None) -> str:
            if i is None or i >= len(row) or row[i] is None:
                return ""
            return str(row[i]).strip()

        def column_values(i: int | None) -> set[str]:
            if i is None:
                return set()
            return {cell(r, i) for r in t.rows if cell(r, i)}

        s_lookup = _resolve(tables, tname, column_values(s_i))
        r_lookup = _resolve(tables, tname, column_values(r_i))
        m_lookup = _resolve(tables, tname, column_values(m_i))
        st_lookup = _resolve(tables, tname, column_values(st_i))

        for row in t.rows:
            if not row:
                continue
            raw_s, raw_r = cell(row, s_i), cell(row, r_i)
            if not raw_s and not raw_r:
                continue
            rid = cell(row, 0)
            steps_raw = cell(row, st_i)
            steps = st_lookup.get(steps_raw, steps_raw)
            interfaces.append(InterfaceRecord(
                interface_id=f"register:{tname}#{rid or len(interfaces)}",
                source_system=s_lookup.get(raw_s, raw_s) or "unknown",
                target_system=r_lookup.get(raw_r, raw_r) or "unknown",
                origin="register",
                mapping_ref=m_lookup.get(cell(row, m_i), cell(row, m_i)),
                label=cell(row, n_i),
                endpoint=cell(row, e_i),
                steps=tuple(x.strip() for x in steps.split(",") if x.strip()),
                confidence=Confidence.CERTAIN,
                notes=f"from {tname}",
            ))

    if candidates and not interfaces:
        unresolved.append(Unresolved(
            "no_interface_register_detected",
            root.relative_to(repo_root).as_posix() if root != repo_root else ".",
            f"read {len(candidates)} table export(s) but none had both a "
            "sender-like and a receiver-like column",
        ))
    return artifacts, interfaces, creds, unresolved
