"""AC5 proof (Slice 5, RD-02): no automatic path may ever set
``full_rederive=True`` on ``run_git_reingestion`` -- only the explicit
operator manual-full-rebuild trigger may.

Two complementary proofs, per plan.md / critique.md (Attack 1):

1. Static: an AST scan of every production module in ``src/mcp_server`` that
   calls ``run_git_reingestion`` -- not a text/grep match, so a future
   refactor that passes ``full_rederive=some_computed_flag`` (rather than
   the literal ``True``) is caught too, closing the hole a naive
   ``grep "full_rederive=True"`` would miss (critique.md Attack 1: the real
   risk is a call site that *sometimes* computes True, which grep for the
   literal cannot see; walking the AST and flagging ANY call site that
   passes the ``full_rederive`` keyword at all, outside the one approved
   site, catches that).
2. Behavioral: an injected legacy ``full_regen`` decision cannot reach
   ``run_git_reingestion`` at all. It is converted to a terminal held result
   before a generation tool can run.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import mcp_server.services.git_reingestion as git_reingestion_module

SRC_ROOT = Path(inspect.getfile(git_reingestion_module)).resolve().parent.parent

# The ONE production call site allowed to pass `full_rederive` (at all, any
# value) to `run_git_reingestion`: the Slice 5 manual-rebuild trigger route.
# Every other call site in the codebase must omit the keyword entirely,
# which (by Python's keyword-default semantics) permanently pins it to the
# function's own default (False) unless this allowlist is deliberately
# extended by a human reviewing this exact test.
ALLOWED_FULL_REDERIVE_CALL_SITES: frozenset[tuple[str, str]] = frozenset(
    {
        ("api/routes/projects.py", "trigger_manual_full_rebuild"),
    }
)


def _iter_python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _call_target_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _find_full_rederive_call_sites(root: Path) -> list[tuple[str, str]]:
    """Return (relative_path, enclosing_function_name) for every call in the
    source tree that passes a `full_rederive` keyword argument to ANY
    function named `run_git_reingestion` -- OR that passes the bare name
    `run_git_reingestion` as an argument (e.g.
    ``background_tasks.add_task(run_git_reingestion, ..., full_rederive=...)``,
    this codebase's actual deferred-call idiom -- see
    ``repair_latest_git_update`` / ``trigger_manual_full_rebuild``) together
    with a `full_rederive` keyword on that OUTER call.

    By call-site name, not full symbol resolution -- deliberately
    conservative: a false positive just means an extra allowlist entry to
    review; a false negative would defeat the proof, so this errs toward
    over-flagging.
    """
    hits: list[tuple[str, str]] = []
    for path in _iter_python_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue

        # Map each Call node to its nearest enclosing function/method name
        # (or "<module>" for module-level calls) via a simple ancestor walk.
        parent_function: dict[int, str] = {}

        def visit(node: ast.AST, current_function: str) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                current_function = node.name
            if isinstance(node, ast.Call):
                parent_function[id(node)] = current_function
            for child in ast.iter_child_nodes(node):
                visit(child, current_function)

        visit(tree, "<module>")

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            has_full_rederive_kwarg = any(kw.arg == "full_rederive" for kw in node.keywords)
            direct_target = _call_target_name(node) == "run_git_reingestion"
            passes_by_reference = any(
                isinstance(arg, ast.Name) and arg.id == "run_git_reingestion"
                for arg in node.args
            )

            if (direct_target or passes_by_reference) and has_full_rederive_kwarg:
                rel = str(path.relative_to(root))
                hits.append((rel, parent_function.get(id(node), "<module>")))
    return hits


def test_full_rederive_keyword_is_passed_from_exactly_one_allowed_call_site():
    hits = _find_full_rederive_call_sites(SRC_ROOT)
    unexpected = [h for h in hits if h not in ALLOWED_FULL_REDERIVE_CALL_SITES]
    assert not unexpected, (
        "A new call site passes `full_rederive` to run_git_reingestion outside "
        "the allowed manual-rebuild trigger -- this would create an automatic "
        "(or ambiguous) full-rebuild path, forbidden by RD-02. Found: "
        f"{unexpected}"
    )
    assert set(hits) == ALLOWED_FULL_REDERIVE_CALL_SITES, (
        "Expected exactly the Slice 5 manual-rebuild trigger to pass "
        f"full_rederive; found {hits}. If the trigger was renamed/moved, "
        "update ALLOWED_FULL_REDERIVE_CALL_SITES deliberately."
    )


def test_default_full_rederive_parameter_is_false():
    """Belt-and-suspenders: even if some future call site omits the keyword
    (relying on the default), the default itself must stay False -- an
    automatic path could otherwise flip the default and bypass Attack 1's
    per-call-site scan entirely."""
    sig = inspect.signature(git_reingestion_module.run_git_reingestion)
    assert sig.parameters["full_rederive"].default is False


async def test_empty_automatic_target_scope_is_held_before_reingestion(
    session_factory, monkeypatch, tmp_path
):
    """The old ``None means every document`` convention cannot reach the
    automatic reingestion helper."""
    import mcp_server.services.auto_doc_update as auto_doc_update_module
    from mcp_server.config import config
    from mcp_server.services.auto_doc_update import AutoDocAgentRuntime, AutoDocUpdateContext
    from mcp_server.services.reingestion_router import RoutingDecision

    monkeypatch.setattr(config, "project_root", tmp_path)

    async def fake_run_git_reingestion(project_id, before_sha, after_sha, **kwargs):
        raise AssertionError("automatic full regeneration must not be dispatched")

    monkeypatch.setattr(auto_doc_update_module, "run_git_reingestion", fake_run_git_reingestion)

    class DummyLogManager:
        async def emit(self, *args, **kwargs) -> None:
            return None

    runtime = AutoDocAgentRuntime(log_mgr=DummyLogManager())
    ctx = AutoDocUpdateContext(
        project_id="proj",
        local_path=str(tmp_path / "repo"),
        doc_generation_provider=None,
        github_token_enc=None,
        last_ingested_commit_sha="a" * 40,
    )
    decision = RoutingDecision(
        strategy="scoped_regen",
        affected_docs=[],
        changed_groups=["A"],
        reason="affected set wider than breadth threshold",
    )

    result = await runtime._scoped_regen(
        ctx,
        "a" * 40,
        "b" * 40,
        "webhook",
        {"changed_groups": ["A"]},
        decision,
        "test: empty automatic scope",
        targeted_docs=None,
    )

    assert result.action == "held"
    assert result.summary["reason_code"] == "empty_target_scope"
