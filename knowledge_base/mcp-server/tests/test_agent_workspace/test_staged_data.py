"""Complete-list reader used by every scaled workspace consumer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.providers.agent_workspace.staged_data import (
    StagedDataError,
    read_complete_list,
)


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_reads_an_unsplit_collection(tmp_path: Path) -> None:
    path = tmp_path / "rows.json"
    _write(path, [{"id": 1}, {"id": 2}])
    assert read_complete_list(path) == [{"id": 1}, {"id": 2}]


def test_reassembles_every_split_row_in_order(tmp_path: Path) -> None:
    path = tmp_path / "rows.json"
    _write(tmp_path / "rows" / "part_001.json", [{"id": 1}, {"id": 2}])
    _write(tmp_path / "rows" / "part_002.json", [{"id": 3}])
    _write(
        path,
        {
            "split": True,
            "row_count": 3,
            "parts": ["rows/part_001.json", "rows/part_002.json"],
        },
    )
    assert read_complete_list(path) == [{"id": 1}, {"id": 2}, {"id": 3}]


@pytest.mark.parametrize(
    ("index", "message"),
    [
        ({"split": True, "row_count": 2, "parts": ["../outside.json"]}, "escapes"),
        ({"split": True, "row_count": 2, "parts": ["rows/part.json"]}, "declares 2"),
        ({"split": True, "row_count": -1, "parts": []}, "row count"),
    ],
)
def test_refuses_unsafe_or_incomplete_split_indexes(
    tmp_path: Path, index: dict[str, object], message: str
) -> None:
    _write(tmp_path / "outside.json", [{"id": 0}])
    _write(tmp_path / "rows" / "part.json", [{"id": 1}])
    path = tmp_path / "rows.json"
    _write(path, index)
    with pytest.raises(StagedDataError, match=message):
        read_complete_list(path)
