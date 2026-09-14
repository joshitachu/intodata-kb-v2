"""Slice 2: deterministic navigation, and the properties that make it a VIEW.

Every assertion here fails silently in production. A row that points at a
renamed file, a shard split by row count instead of bytes, an index that
enumerates every unit, a set iterated without sorting -- none of them raises,
and all of them cost model time or a prefix-cache miss on a paid run.

The fixtures are written by hand rather than derived from a built workspace:
these are properties of the projection, and a fixture-derived expectation is
how a test ends up asserting what the code happens to do.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.providers.agent_workspace import navigation
from mcp_server.providers.agent_workspace.contract import (
    NAVIGATION,
    NAVIGATION_SHARD_BYTES,
    WorkspaceUnavailableError,
)


def _source(**overrides) -> navigation.NavigationSource:
    base = {
        "units": {
            "OrdersLoad": {
                "display_name": "OrdersLoad",
                "kind": "job",
                "role": "standalone",
                "group": "orders",
                "component_count": 4,
                "component_types": ["tMap", "tFileInputDelimited"],
                "entry_point": True,
                "callers": [],
                "callees": ["OrdersStage"],
                "mapping_table_ids": ["t1"],
                "scan_path": "scan/jobs/orders/OrdersLoad.json",
            },
            "OrdersStage": {
                "display_name": "OrdersStage",
                "kind": "job",
                "group": "orders",
                "callers": ["OrdersLoad"],
                "callees": [],
                "scan_path": "scan/jobs/orders/OrdersStage.json",
            },
        },
        "dependencies": {
            "edges": [
                {
                    "source": "OrdersLoad",
                    "target": "OrdersStage",
                    "declared_target": "OrdersStage",
                    "relation_type": "job_calls_job",
                    "resolution": "repository_id",
                }
            ],
            "unresolved": [
                {
                    "source": "OrdersLoad",
                    "target": "",
                    "declared_target": "ArchiveJob",
                    "relation_type": "job_calls_job",
                    "resolution": "unresolved",
                }
            ],
            "routine_links": {"OrdersLoad": [{"routine": "Vault"}]},
        },
        "interfaces": {
            "by_unit": {
                "OrdersLoad": [
                    {
                        "kind": "channel",
                        "type": "file",
                        "direction": "inbound",
                        "component_name": "tFileInputDelimited_1",
                        "channel": "orders.csv",
                        "descriptor": {"path": "/mnt/in/orders.csv", "encoding": "UTF-8"},
                    }
                ],
                "OrdersStage": [],
            },
            "channels": [
                {
                    "artifact": "OrdersStage",
                    "channel": "orders.csv",
                    "channel_type": "file",
                    "direction": "outbound",
                    "dynamic": False,
                    "channel_descriptor": {"path": "/mnt/out/orders.csv"},
                },
                {
                    "artifact": "OrdersLoad",
                    "channel": "orders.csv",
                    "channel_type": "file",
                    "direction": "inbound",
                    "dynamic": False,
                    "channel_descriptor": {"path": "/mnt/in/orders.csv"},
                },
            ],
        },
        "configuration": {
            "context_groups": [
                {
                    "key": "Orders",
                    "name": "Orders",
                    "source_root": "PROJECT",
                    "source_role": "primary",
                    "environments": ["Default", "Production"],
                    "variables": [
                        {
                            "name": "api_password",
                            "type": "id_Password",
                            "values": {"Production": "never-copy-this-secret"},
                            "redacted_environments": ["Production"],
                        }
                    ],
                    "used_by": ["OrdersLoad"],
                    "metadata": {"raw": "never-copy-metadata"},
                }
            ],
            "inline_contexts": {
                "OrdersStage": [
                    {
                        "name": "Default",
                        "variables": [
                            {
                                "name": "batch_size",
                                "type": "id_Integer",
                                "values": {"Default": "500"},
                            }
                        ],
                    }
                ]
            },
            "repository_connections": [
                {
                    "name": "OrdersDB",
                    "connection_type": "database",
                    "source_root": "PROJECT",
                    "used_by": ["OrdersLoad", "OrdersStage"],
                    "password": "never-copy-connection-secret",
                }
            ],
        },
        "mapping_tables": [
            {
                "table_id": "t1",
                "unit_id": "OrdersLoad",
                "component_name": "tMap_1",
                "mapper_kind": "tmap",
                "offered_fields": 12,
                "written_fields": 9,
            }
        ],
        "mapping_paths": {"t1": "facts/mappings/OrdersLoad__tMap_1.md"},
    }
    base.update(overrides)
    return navigation.NavigationSource(**base)


def _rows(workspace: Path, view: str) -> list[dict]:
    directory = workspace / NAVIGATION / view
    rows: list[dict] = []
    for shard in sorted(directory.glob("part_*.jsonl")):
        rows.extend(json.loads(line) for line in shard.read_text().splitlines())
    return rows


def test_load_source_reassembles_split_mapping_accounting(tmp_path: Path) -> None:
    """Large estates keep mapping navigation instead of silently becoming empty."""

    facts = tmp_path / "facts"
    scan = tmp_path / "scan"
    facts.mkdir()
    scan.mkdir()
    for name in ("units.json", "dependencies.json", "interfaces.json", "configuration.json"):
        (facts / name).write_text("{}", encoding="utf-8")
    parts = scan / "mapping_accounting"
    parts.mkdir()
    (parts / "part_001.json").write_text(
        json.dumps([{"table_id": "map:one", "rows": []}]), encoding="utf-8"
    )
    (parts / "part_002.json").write_text(
        json.dumps([{"table_id": "map:two", "rows": []}]), encoding="utf-8"
    )
    (scan / "mapping_accounting.json").write_text(
        json.dumps(
            {
                "split": True,
                "row_count": 2,
                "parts": [
                    "mapping_accounting/part_001.json",
                    "mapping_accounting/part_002.json",
                ],
            }
        ),
        encoding="utf-8",
    )

    source = navigation.load_source(tmp_path, {})

    assert [row["table_id"] for row in source.mapping_tables] == ["map:one", "map:two"]


# --- rows resolve to evidence -------------------------------------------------


class TestRowsRoundTrip:
    def test_a_unit_row_carries_its_staged_design_file(self, tmp_path: Path) -> None:
        navigation.write_navigation(tmp_path, _source())
        rows = {row["unit_id"]: row for row in _rows(tmp_path, "units")}
        assert rows["OrdersLoad"]["evidence"] == "scan/jobs/orders/OrdersLoad.json"

    def test_a_mapping_row_carries_the_collision_resolved_staged_path(self, tmp_path: Path) -> None:
        """The stated slice requirement, and the one with a real failure behind it.

        Two mappers differing only in case are ONE file on macOS and Windows,
        so ``workspace._mapping_table_name`` renames the second. A row built
        from ``<design>__<mapper>.md`` would name a path that does not exist,
        which is worse than no row: it teaches the agent the index lies.
        """

        source = _source(mapping_paths={"t1": "facts/mappings/OrdersLoad__tMap_1_2.md"})
        navigation.write_navigation(tmp_path, source)
        row = _rows(tmp_path, "mappings")[0]
        assert row["evidence"] == "facts/mappings/OrdersLoad__tMap_1_2.md"
        assert row["unit_evidence"] == "scan/jobs/orders/OrdersLoad.json"

    def test_a_mapping_with_no_rendered_table_still_names_an_existing_file(
        self, tmp_path: Path
    ) -> None:
        navigation.write_navigation(tmp_path, _source(mapping_paths={}))
        assert _rows(tmp_path, "mappings")[0]["evidence"] == "scan/mapping_accounting.json"

    def test_a_unit_row_lists_only_mapping_tables_that_were_staged(self, tmp_path: Path) -> None:
        navigation.write_navigation(tmp_path, _source(mapping_paths={}))
        rows = {row["unit_id"]: row for row in _rows(tmp_path, "units")}
        assert rows["OrdersLoad"]["mapping_tables"] == []

    def test_an_unresolved_edge_is_a_row_and_says_so(self, tmp_path: Path) -> None:
        """An unresolved `tRunJob` is a real call a document has to account for."""

        navigation.write_navigation(tmp_path, _source())
        rows = _rows(tmp_path, "dependencies")
        unresolved = [row for row in rows if not row["resolved"]]
        assert [row["declared_target"] for row in unresolved] == ["ArchiveJob"]
        assert unresolved[0]["target_evidence"] == ""

    def test_routine_links_have_their_own_usage_view(self, tmp_path: Path) -> None:
        navigation.write_navigation(tmp_path, _source())
        assert all(
            row["relation_type"] != "job_uses_routine" for row in _rows(tmp_path, "dependencies")
        )
        routine = _rows(tmp_path, "routine_usage")[0]
        assert routine["routine"] == "Vault"
        assert routine["routine_evidence"] == "scan/routines.json"

    def test_channel_pairings_separate_producers_and_consumers(self, tmp_path: Path) -> None:
        navigation.write_navigation(tmp_path, _source())
        row = _rows(tmp_path, "channel_pairings")[0]
        assert row["producers"] == ["OrdersStage"]
        assert row["consumers"] == ["OrdersLoad"]
        assert row["paired"] is True
        assert "channel_descriptor" not in row
        assert "/mnt/" not in json.dumps(row)

    def test_configuration_navigation_carries_names_but_never_values(self, tmp_path: Path) -> None:
        navigation.write_navigation(tmp_path, _source())
        rows = _rows(tmp_path, "configuration")
        assert {row["kind"] for row in rows} == {
            "context_group",
            "inline_context",
            "repository_connection",
        }
        assert next(row for row in rows if row["kind"] == "context_group")["used_by"] == [
            "OrdersLoad"
        ]
        raw = json.dumps(rows)
        assert "api_password" in raw
        assert "never-copy-this-secret" not in raw
        assert "never-copy-metadata" not in raw
        assert "never-copy-connection-secret" not in raw

    def test_an_interface_row_routes_without_carrying_the_descriptor(self, tmp_path: Path) -> None:
        """The row's job is to route. `facts/interfaces.json` holds the rest.

        ``descriptor`` is a free-form dict of unbounded size and is the widest
        surface in the fact tables; a projection optimised for grep should not
        be the place it gets a second copy.
        """

        navigation.write_navigation(tmp_path, _source())
        row = _rows(tmp_path, "interfaces")[0]
        assert row["name"] == "orders.csv"
        assert row["unit_evidence"] == "scan/jobs/orders/OrdersLoad.json"
        assert "descriptor" not in row
        assert "/mnt/in/orders.csv" not in json.dumps(row)


# --- shape, sharding, determinism --------------------------------------------


class TestShardsAndManifest:
    def test_every_line_is_one_complete_record(self, tmp_path: Path) -> None:
        navigation.write_navigation(tmp_path, _source())
        for view in navigation.NAVIGATION_VIEW_ORDER:
            for shard in (tmp_path / NAVIGATION / view).glob("*.jsonl"):
                for line in shard.read_text().splitlines():
                    assert json.loads(line)["view"] == view

    def test_a_small_estate_uses_one_shard(self, tmp_path: Path) -> None:
        navigation.write_navigation(tmp_path, _source())
        assert [p.name for p in sorted((tmp_path / NAVIGATION / "units").iterdir())] == [
            "part_001.jsonl"
        ]

    def test_shards_split_on_bytes_not_row_count(self, tmp_path: Path) -> None:
        """A row-count ceiling gives 40 KB shards here and 40 MB shards next door."""

        units = {
            f"Unit{index:04d}": {
                "display_name": f"Unit{index:04d}",
                "group": "g",
                "component_types": [f"tComponentWithAVeryLongName{n}" for n in range(40)],
                "scan_path": f"scan/jobs/g/Unit{index:04d}.json",
            }
            for index in range(400)
        }
        navigation.write_navigation(tmp_path, _source(units=units, mapping_tables=[]))
        shards = sorted((tmp_path / NAVIGATION / "units").glob("*.jsonl"))
        assert len(shards) > 1
        for shard in shards:
            assert shard.stat().st_size <= NAVIGATION_SHARD_BYTES
        assert len(_rows(tmp_path, "units")) == 400

    def test_a_view_with_no_rows_is_absent_and_the_manifest_says_so(self, tmp_path: Path) -> None:
        """An empty directory reads as "nothing of this kind exists"."""

        navigation.write_navigation(
            tmp_path, _source(interfaces={}, mapping_tables=[], mapping_paths={})
        )
        assert not (tmp_path / NAVIGATION / "interfaces").exists()
        manifest = json.loads((tmp_path / NAVIGATION / "manifest.json").read_text())
        views = {view["name"]: view for view in manifest["views"]}
        assert views["interfaces"]["present"] is False
        assert views["interfaces"]["rows"] == 0
        assert "absent_because" in views["interfaces"]

    def test_the_manifest_names_every_view_its_shards_and_its_canonical_file(
        self, tmp_path: Path
    ) -> None:
        navigation.write_navigation(tmp_path, _source())
        manifest = json.loads((tmp_path / NAVIGATION / "manifest.json").read_text())
        views = {view["name"]: view for view in manifest["views"]}
        assert tuple(views) == navigation.NAVIGATION_VIEW_ORDER
        for name, view in views.items():
            assert view["resolves_to"]
            for shard in view["shards"]:
                assert (tmp_path / shard).is_file(), name

    def test_two_builds_of_one_source_are_byte_identical(self, tmp_path: Path) -> None:
        """A churned view invalidates every provider prefix cache downstream."""

        first, second = tmp_path / "a", tmp_path / "b"
        navigation.write_navigation(first, _source())
        navigation.write_navigation(second, _source())
        for path in sorted((first / NAVIGATION).rglob("*")):
            if path.is_file():
                mirror = second / path.relative_to(first)
                assert path.read_bytes() == mirror.read_bytes(), path

    def test_the_row_counts_are_returned_for_every_view(self, tmp_path: Path) -> None:
        counts = navigation.write_navigation(tmp_path, _source())
        assert set(counts) == set(navigation.NAVIGATION_VIEW_ORDER)
        assert counts["units"] == 2
        assert counts["dependencies"] == 2
        assert counts["routine_usage"] == 1
        assert counts["channel_pairings"] == 1
        assert counts["configuration"] == 3
        assert counts["mappings"] == 1

    def test_evidence_paths_cannot_escape_the_safe_workspace(self, tmp_path: Path) -> None:
        source = _source(
            units={
                "OrdersLoad": {
                    "display_name": "OrdersLoad",
                    "scan_path": "../../raw/source.xml",
                }
            },
            dependencies={},
            interfaces={},
            configuration={},
            mapping_tables=[],
            mapping_paths={},
        )
        # One exception family, and it names its stage: the runner catches
        # `WorkspaceUnavailableError` and nothing else, so a bare `ValueError`
        # from here reached the operator as an unqualified "Generation failed".
        with pytest.raises(
            WorkspaceUnavailableError, match="unsafe navigation evidence path"
        ) as caught:
            navigation.write_navigation(tmp_path, source)
        assert caught.value.stage == "navigation"

    def test_a_hostile_name_cannot_break_a_record(self, tmp_path: Path) -> None:
        units = {
            'Ord"ers\nLoad': {
                "display_name": "a|b\nc",
                "group": "g\n|",
                "scan_path": "scan/jobs/g/x.json",
            }
        }
        navigation.write_navigation(
            tmp_path, _source(units=units, dependencies={}, interfaces={}, mapping_tables=[])
        )
        rows = _rows(tmp_path, "units")
        assert len(rows) == 1
        assert rows[0]["unit_id"] == 'Ord"ers\nLoad'

    def test_a_non_ascii_name_stays_greppable(self, tmp_path: Path) -> None:
        """A shard is searched with `grep`, not parsed, to find a name.

        JSON's default `ensure_ascii=True` would write `R\\u00e9cup` and a grep
        for the name the agent read would return nothing -- a silent failure
        on any estate whose designs are not named in pure ASCII.
        """

        units = {
            "Recuperation": {
                "display_name": "Récupération",
                "group": "gestion",
                "scan_path": "scan/jobs/gestion/Recuperation.json",
            }
        }
        navigation.write_navigation(
            tmp_path, _source(units=units, dependencies={}, interfaces={}, mapping_tables=[])
        )
        raw = (tmp_path / NAVIGATION / "units" / "part_001.jsonl").read_text(encoding="utf-8")
        assert "Récupération" in raw
        assert "\\u00e9" not in raw


# --- INDEX.md -----------------------------------------------------------------


class TestIndex:
    def _index(self, tmp_path: Path, source=None) -> str:
        source = source if source is not None else _source()
        rows = navigation.write_navigation(tmp_path, source)
        return navigation.render_index(
            project_id="PROJECT",
            platform="talend",
            workspace=tmp_path,
            source=source,
            navigation_rows=rows,
        )

    def test_it_keeps_the_heading_the_house_rules_point_at(self, tmp_path: Path) -> None:
        """`facts/coverage_ledger.md` folded in here; its heading did not move."""

        assert "## What this scan found" in self._index(tmp_path)

    def test_it_states_populations_and_the_cover_or_declare_rule(self, tmp_path: Path) -> None:
        body = self._index(tmp_path)
        assert "| units (designs) | 2 |" in body
        assert "limitations_and_unknowns.md" in body

    def test_it_grows_with_groups_and_never_enumerates_units(self, tmp_path: Path) -> None:
        """The risk the plan names: a root index becoming the next giant prompt."""

        units = {
            f"Unit{index:04d}": {
                "display_name": f"Unit{index:04d}",
                "group": f"group{index % 3}",
                "scan_path": f"scan/jobs/g/Unit{index:04d}.json",
            }
            for index in range(300)
        }
        body = self._index(tmp_path, _source(units=units, mapping_tables=[], mapping_paths={}))
        assert "| `group0` | 100 |" in body
        assert "Unit0007" not in body
        assert len(body) < 10_000

    def test_it_routes_a_question_to_a_view_and_a_canonical_file(self, tmp_path: Path) -> None:
        body = self._index(tmp_path)
        assert "## Where to look for what" in body
        assert "`facts/navigation/units/` (2 rows)" in body
        assert "`scan/jobs/<group>/<design>.json`" in body

    def test_an_absent_view_is_reported_as_absent_not_as_a_path(self, tmp_path: Path) -> None:
        body = self._index(tmp_path, _source(interfaces={}, mapping_tables=[], mapping_paths={}))
        assert "no rows on this estate" in body
        assert "`facts/navigation/interfaces/`" not in body

    def test_it_prescribes_no_worker_count_and_no_reading_regime(self, tmp_path: Path) -> None:
        """Plan decision 2: completeness is an output obligation, not a regime.

        The coverage ledger this replaces said "Delegate: a worker per group
        or per integration, each reading its own units in full". That is a
        reading prescription and it is exactly what this lane removes.
        """

        body = self._index(tmp_path).casefold()
        for phrase in ("a worker per", "one worker", "read every", "in full"):
            assert phrase not in body

    def test_it_carries_no_channel_value_or_configuration_value(self, tmp_path: Path) -> None:
        """Populations only. Values live in files the agent opens deliberately."""

        body = self._index(tmp_path)
        assert "orders.csv" not in body
        assert "/mnt/in/orders.csv" not in body

    def test_it_is_ascii_and_table_safe(self, tmp_path: Path) -> None:
        units = {
            "u": {
                "display_name": "x",
                "group": "pipe|group—dash",
                "scan_path": "scan/jobs/g/x.json",
            }
        }
        body = self._index(
            tmp_path, _source(units=units, dependencies={}, interfaces={}, mapping_tables=[])
        )
        body.encode("ascii")
        # The pipe is escaped so it cannot open a column; the em dash is folded
        # by `instructions.ascii_only` rather than replaced with a `?`.
        assert "`pipe\\|group--dash`" in body

    def test_it_is_byte_stable(self, tmp_path: Path) -> None:
        first = self._index(tmp_path / "a")
        second = self._index(tmp_path / "b")
        assert first == second


def test_an_unknown_view_has_no_silent_builder() -> None:
    with pytest.raises(WorkspaceUnavailableError, match="no builder") as caught:
        navigation._build_rows("invented", _source())
    assert caught.value.stage == "navigation"


_DETERMINISM_PROBE = """
import hashlib, json, sys, tempfile
from pathlib import Path
from mcp_server.providers.agent_workspace import navigation
from mcp_server.providers.agent_workspace.contract import NAVIGATION

units = {
    f"Unit{i:03d}": {
        "display_name": f"Unit{i:03d}",
        "group": f"group{i % 4}",
        "component_types": [f"tComp{n}" for n in range(20)],
        "callers": [f"Unit{n:03d}" for n in range(i)],
        "scan_path": f"scan/jobs/g/Unit{i:03d}.json",
    }
    for i in range(60)
}
source = navigation.NavigationSource(units=units)
root = Path(tempfile.mkdtemp())
rows = navigation.write_navigation(root, source)
index = navigation.render_index(
    project_id="P", platform="talend", workspace=root, source=source, navigation_rows=rows
)
digest = hashlib.sha256(index.encode("utf-8"))
for path in sorted((root / NAVIGATION).rglob("*")):
    if path.is_file():
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
print(digest.hexdigest())
"""


def test_the_view_is_byte_stable_ACROSS_processes() -> None:
    """Same input, different `PYTHONHASHSEED`, identical bytes.

    A same-process rebuild cannot catch the defect class this guards: a set
    iterated without a total sort is stable within one interpreter and
    different in the next. That exact bug shipped once in this repo -- a
    fact-map view sorted on ``casefold`` alone, so two values differing only
    in case kept set-iteration order and four runs of identical input produced
    three different files, i.e. a silent provider prefix-cache miss on every
    call.
    """

    import os
    import subprocess
    import sys

    digests = set()
    for seed in ("0", "1", "524287"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        result = subprocess.run(
            [sys.executable, "-c", _DETERMINISM_PROBE],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        digests.add(result.stdout.strip())
    assert len(digests) == 1, digests
