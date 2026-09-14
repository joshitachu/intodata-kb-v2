"""The seam itself, and the one fact in it that is copied from another package.

``contract.py`` is declarative, so most of it is pinned wherever it is used.
The exception is :data:`CODE_PARAM_NAMES`: it is a copy of a tuple that lives
in the scanner, and the scanner is a separate distribution that is deliberately
not installed here. There is no import to keep the two honest, so the check is
static -- the same answer ``test_no_partitioning`` reaches for the same reason.

Why it is worth a test at all: the projection strips these parameters because
their VALUE is a code body rather than a setting. A parameter the scanner
starts emitting and this tuple does not know about is staged verbatim, which is
the exact boundary the projection exists to hold, and it fails silently.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from mcp_server.providers.agent_workspace import contract

#: ``knowledge_base/intodoc/src/intodoc/models.py``, from this test file.
_SCANNER_MODELS = Path(__file__).resolve().parents[3] / "intodoc" / "src" / "intodoc" / "models.py"


def _scanner_code_param_names() -> tuple[str, ...]:
    """Read ``Component.CODE_PARAM_NAMES`` out of the scanner's source.

    Parsed rather than imported: ``intodoc`` is not a dependency of the MCP
    server and is absent from its environment.
    """

    tree = ast.parse(_SCANNER_MODELS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "Component":
            continue
        for statement in node.body:
            target = (
                statement.target
                if isinstance(statement, ast.AnnAssign)
                else (statement.targets[0] if isinstance(statement, ast.Assign) else None)
            )
            if isinstance(target, ast.Name) and target.id == "CODE_PARAM_NAMES":
                return tuple(ast.literal_eval(statement.value))
    raise AssertionError("Component.CODE_PARAM_NAMES was not found in the scanner models")


@pytest.mark.skipif(
    not _SCANNER_MODELS.is_file(),
    reason="the scanner source tree is not checked out beside the MCP server",
)
def test_the_code_bearing_parameter_names_still_match_the_scanner() -> None:
    assert set(contract.CODE_PARAM_NAMES) == set(_scanner_code_param_names())


def test_the_bootstrap_is_a_subset_of_what_the_workspace_declares() -> None:
    """Every startup file has to be one the builder actually writes."""

    roots = {path.as_posix() for path in contract.EVIDENCE_FILES}
    instructions = {
        (contract.INSTRUCTIONS / name).as_posix() for name in contract.INSTRUCTION_FILES
    }
    for path in contract.BOOTSTRAP_FILES:
        assert path.as_posix() in roots | instructions, path


def test_the_writable_roots_are_disjoint_from_the_evidence() -> None:
    """The digest excludes the writable roots; an overlap would make it unstable."""

    evidence = {path.as_posix() for path in contract.EVIDENCE_ROOTS}
    writable = {path.as_posix() for path in contract.WRITABLE_ROOTS}
    assert evidence.isdisjoint(writable)
    assert not any(
        path.as_posix().startswith(f"{root}/")
        for path in contract.EVIDENCE_FILES
        for root in writable
    )
