"""RBAC enforcement tests for protected project and Pack-management routes.

Two invariants are verified for every protected endpoint:
  1. Non-superadmin user with no project assignment → 403.
  2. Superadmin → not blocked by RBAC (may get 4xx for other reasons, never 401/403).
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

# (method, path, json_body, test_id)
# Covers every route that calls require_project_access.
_ROUTES = [
    # --- Path-based project_id ---
    ("GET",    "/mcp/status/p1",                              None,  "status"),
    ("GET",    "/mcp/projects/p1/usage",                      None,  "usage"),
    ("GET",    "/mcp/scan-runs/p1",                           None,  "scan_runs"),
    ("GET",    "/mcp/projects/p1/docs/test.md/content",       None,  "doc_content"),
    ("GET",    "/mcp/generate-runs/p1",                       None,  "generate_runs"),
    ("GET",    "/mcp/generate-run-detail/p1/1",               None,  "generate_run_detail"),
    ("GET",    "/mcp/compare-runs/p1?run_a=1&run_b=2",        None,  "compare_runs"),
    ("GET",    "/mcp/projects/p1/quality-history",            None,  "quality_history"),
    ("GET",    "/mcp/projects/p1/questionnaire",              None,  "questionnaire"),
    ("GET",    "/mcp/projects/p1/reviews",                    None,  "reviews_get"),
    ("GET",    "/mcp/logs/p1",                                None,  "logs_list"),
    ("GET",    "/mcp/logs/p1/p1_test.log",                    None,  "log_content"),
    ("GET",    "/mcp/projects/p1/docs",                       None,  "docs_list"),
    ("GET",    "/mcp/projects/p1/docs/test.md",               None,  "doc_download"),
    (
        "POST",
        "/mcp/projects/p1/docs/test.md/evidence-update",
        {"evidence_text": "Evidence from chat", "evidence_refs": ["chat_prompt"]},
        "doc_evidence_update",
    ),
    (
        "POST",
        "/mcp/projects/p1/docs/test.md/evidence-revert",
        {"receipt_path": "_harness_doc_updates/update.receipt.json"},
        "doc_evidence_revert",
    ),
    ("GET",    "/mcp/projects/p1/graph",                      None,  "graph"),
    ("GET",    "/mcp/projects/p1/jobs/myjob/detail",          None,  "job_detail"),
    ("GET",    "/mcp/projects/p1/jobs/myjob/graph",           None,  "job_graph"),
    (
        "GET",
        "/mcp/projects/p1/lineage/field?job=myjob&table=row1&column=x",
        None,
        "lineage_field",
    ),
    (
        "GET",
        "/mcp/projects/p1/lineage/job?job=myjob",
        None,
        "lineage_job",
    ),
    ("GET",    "/mcp/projects/p1/github-webhook/status",      None,  "webhook_status"),
    ("GET",    "/mcp/projects/p1/assessment",                 None,  "assessment_get"),
    ("POST",   "/mcp/projects/p1/generate-pdfs",              None,  "generate_pdfs"),
    ("POST",   "/mcp/projects/p1/generate-assessment",        None,  "generate_assessment"),
    ("POST",   "/mcp/projects/p1/github-webhook/register",    None,  "webhook_register"),
    ("POST",   "/mcp/projects/p1/git-updates/repair-latest",  None,  "git_repair"),
    ("POST",   "/mcp/projects/p1/generate-summaries",         None,  "generate_summaries"),
    ("PATCH",  "/mcp/projects/p1",                            {},    "project_patch"),
    ("DELETE", "/mcp/projects/p1",                            None,  "project_delete"),
    ("DELETE", "/mcp/projects/p1/github-webhook",             None,  "webhook_delete"),
    (
        "PATCH",
        "/mcp/projects/p1/github-token",
        {"github_token": "ghp_test"},
        "github_token",
    ),
    (
        "POST",
        "/mcp/projects/p1/reviews",
        {
            "run_id": 0,
            "reviewer": "tester",
            "overall": 5,
            "questions": [],
            "feedback": "",
            "reviewed_at": "2026-01-01T00:00:00Z",
        },
        "reviews_post",
    ),
    # --- Body-based project_id ---
    ("POST", "/mcp/scan",         {"project_id": "p1", "force": False},                   "scan"),
    ("POST", "/mcp/generate",     {"project_id": "p1"},                                    "generate"),
    (
        "POST",
        "/mcp/plugins",
        {"repo_url": "https://git.example.test/packs.git", "repo_branch": "main"},
        "plugin_register",
    ),
    (
        "POST",
        "/mcp/plugins/discover",
        {"repo_url": "https://git.example.test/packs.git"},
        "plugin_discover",
    ),
    (
        "POST",
        "/mcp/plugins/branch-files",
        {"repo_url": "https://git.example.test/packs.git", "repo_branch": "main"},
        "plugin_branch_files",
    ),
    (
        "POST",
        "/mcp/plugins/branch-file",
        {
            "repo_url": "https://git.example.test/packs.git",
            "repo_branch": "main",
            "file_path": "plugin.yaml",
        },
        "plugin_branch_file",
    ),
    ("PATCH", "/mcp/plugins/1", {"pull": True}, "plugin_pull"),
    ("PATCH", "/mcp/projects/p1/plugin", {"plugin_pack_id": None},                           "project_plugin"),
    ("POST", "/mcp/push-to-rag",  {"project_id": "p1"},                                    "push_to_rag"),
    ("POST", "/mcp/reset-status", {"project_id": "p1"},                                    "reset_status"),
]

_IDS = [r[3] for r in _ROUTES]
_PARAMS = [(r[0], r[1], r[2]) for r in _ROUTES]


@pytest.fixture
async def non_owner_client(session_factory, monkeypatch, tmp_path):
    """Client authenticated as user 2 — not a superadmin, no project assignments."""
    from mcp_server.config import config

    monkeypatch.setattr(config, "data_dir", tmp_path / "data")
    monkeypatch.setattr(config, "project_root", tmp_path / "project_root")
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc_output")

    (tmp_path / "data" / "logs").mkdir(parents=True)
    (tmp_path / "data" / "uploads").mkdir(parents=True)
    (tmp_path / "data" / "repos").mkdir(parents=True)
    (tmp_path / "project_root" / "output").mkdir(parents=True)

    import mcp_server.api.routes.projects as projects_mod
    import mcp_server.api.routes.generate_runs as generate_runs_mod
    import mcp_server.api.routes.plugins as plugins_mod
    import mcp_server.api.routes.webhook as webhook_mod
    import mcp_server.api.permissions as permissions_mod
    import mcp_server.db.session as session_mod
    import mcp_server.services.git_poller as git_poller_mod
    import mcp_server.services.git_reingestion as git_reingestion_mod
    import mcp_server.services.git_diff_patcher as git_diff_patcher_mod

    monkeypatch.setattr(session_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(projects_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(generate_runs_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(plugins_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(webhook_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(permissions_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(git_poller_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(git_reingestion_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(git_diff_patcher_mod, "async_session_factory", session_factory)

    # User 2: authenticated but no superadmin flag, no project assignments.
    async with session_factory() as db:
        await db.execute(text(
            "INSERT INTO users (id, username, is_superadmin) VALUES (2, 'noowner', 0)"
        ))
        await db.commit()

    from fastapi import Request
    from mcp_server.api.app import app, require_auth
    from mcp_server.services.log_manager import LogManager

    async def _mock_non_owner(request: Request) -> dict:
        request.state.auth_payload = {"sub": "2", "type": "access", "username": "noowner"}
        return request.state.auth_payload

    app.dependency_overrides[require_auth] = _mock_non_owner
    app.state.log_manager = LogManager()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.mark.parametrize("method,path,body", _PARAMS, ids=_IDS)
async def test_non_owner_gets_403(non_owner_client, method, path, body):
    """Every protected route must deny a user who is not assigned to the project."""
    resp = await non_owner_client.request(method, path, json=body)
    assert resp.status_code == 403, (
        f"{method} {path}: expected 403, got {resp.status_code} — {resp.text[:300]}"
    )

@pytest.mark.parametrize("method,path,body", _PARAMS, ids=_IDS)
async def test_superadmin_passes_rbac(client, method, path, body):
    """Superadmin must clear every RBAC gate (4xx for missing data is fine, 401/403 is not)."""
    resp = await client.request(method, path, json=body)
    assert resp.status_code not in (401, 403), (
        f"{method} {path}: superadmin should not be RBAC-blocked, got {resp.status_code} — {resp.text[:300]}"
    )
