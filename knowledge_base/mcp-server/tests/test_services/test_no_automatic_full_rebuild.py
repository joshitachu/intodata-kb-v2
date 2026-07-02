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
2. Behavioral: the deterministic router's automatic strategies
   (``scoped_regen`` / ``full_regen`` chosen by ``ReingestionRouter.decide``)
   drive ``_scoped_regen``, which must never forward ``full_rederive`` to
   ``run_git_reingestion`` -- verified by inspecting the actual call
   arguments at runtime, not just the source text.
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


async def test_scoped_regen_never_forwards_full_rederive_to_run_git_reingestion(
    session_factory, monkeypatch, tmp_path
):
    """Behavioral proof: the automatic router-driven regeneration path
    (_scoped_regen, reached from scoped_regen/full_regen router strategies)
    must never pass full_rederive at all -- confirmed by inspecting the
    real call arguments, not just asserting on source text."""
    import mcp_server.services.auto_doc_update as auto_doc_update_module
    from mcp_server.config import config
    from mcp_server.services.auto_doc_update import AutoDocAgentRuntime, AutoDocUpdateContext
    from mcp_server.services.reingestion_router import RoutingDecision

    monkeypatch.setattr(config, "project_root", tmp_path)

    # Satisfy the targeted-regen cache-readiness preflight so _scoped_regen
    # actually reaches run_git_reingestion rather than short-circuiting to
    # _manual (a vacuous pass would prove nothing).
    output_dir = tmp_path / "output" / "proj"
    docs = output_dir / "docs"
    support = docs / "_rag_support" / "phase2_analysis"
    cache = output_dir / "analysis_cache"
    support.mkdir(parents=True)
    cache.mkdir(parents=True)
    (docs / "01_ARCHITECTURE_MAP.md").write_text("# Architecture", encoding="utf-8")
    (support / "A.md").write_text("# Group A analysis", encoding="utf-8")
    (cache / "A.md").write_text("# Group A analysis", encoding="utf-8")

    captured: dict[str, object] = {}

    async def fake_run_git_reingestion(project_id, before_sha, after_sha, **kwargs):
        captured.update(kwargs)
        captured["before_sha"] = before_sha
        captured["after_sha"] = after_sha
        return True

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
        strategy="full_regen",
        affected_docs=["01_ARCHITECTURE_MAP"],
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
        "test: automatic full_regen strategy",
        targeted_docs=None,
    )

    assert captured, (
        "preflight must have passed and reached run_git_reingestion -- "
        f"got result.summary={result.summary!r} instead"
    )
    assert "full_rederive" not in captured, (
        "the automatic router-driven full_regen strategy must never forward "
        "full_rederive -- only the explicit manual-rebuild trigger may"
    )
