"""The two licence verifier copies are byte-identical.

Backend and mcp-server run in separate environments (and are headed for
separate repositories), so the verifier is mirrored rather than shared. A copy
that drifts would let one sub-system accept a licence the other refuses. Skips
when the mcp-server tree is absent (a split checkout).
"""

from __future__ import annotations

from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[2]
REPO = HERE.parents[4]
MCP = REPO / "knowledge_base" / "mcp-server"

PAIRS = [
    (BACKEND / "src" / "licensing" / "core.py", MCP / "src" / "mcp_server" / "licensing" / "core.py"),
    (BACKEND / "src" / "licensing" / "keys.py", MCP / "src" / "mcp_server" / "licensing" / "keys.py"),
    (HERE.parent / "golden_vectors.json", MCP / "tests" / "test_licensing" / "golden_vectors.json"),
]


@pytest.mark.parametrize("ours,theirs", PAIRS, ids=lambda p: p.name)
def test_mirror_is_identical(ours: Path, theirs: Path) -> None:
    if not MCP.exists():
        pytest.skip("mcp-server tree not present (split checkout)")
    assert ours.read_bytes() == theirs.read_bytes(), (
        f"{theirs} has drifted from {ours}; copy the file across unchanged"
    )
