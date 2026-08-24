"""The personal pack lane: anyone may author, only the author may see.

Two lanes, one format. A personal pack is usable and editable by its owner and
invisible to everybody else; it becomes shared by being published as a branch
and merged upstream by a human. These tests pin the properties that would be
quietly wrong in the worst way if they broke:

* privacy -- another user must not be able to list, read, edit, delete, publish
  or SELECT someone else's pack, and must get 404 rather than 403 (whether a
  private pack exists is not their business);
* usability -- a personal pack must actually be selectable for generation, or
  the whole lane is decorative;
* publishing asks, it does not grant -- pushing a branch must not flip the pack
  to shared or certified.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi import Request
from sqlalchemy import select, text

from mcp_server.api.routes import plugins as plugins_mod
from mcp_server.db.models import PluginPack

PACK_YAML = """\
name: my-audit
version: 0.1.0
description: A personal pack.
lens:
  sections:
    mode: replace
    declare:
      - id: FINDINGS
        question: what did you find
docs:
  - name: AUDIT_REPORT
    phase2_sections: [FINDINGS]
"""

AI_FILES = {
    "README.md": """\
# Audit report pack

This pack produces AUDIT_REPORT from the FINDINGS analysis section for a
security reviewer. It keeps every conclusion tied to source evidence and makes
unknowns explicit. Nexus owns extraction and platform-analysis policy; the
files in this pack add output focus without replacing that evidence floor.

Review advisory validation results after each run, then tune the phase and
document instructions when the report needs a different emphasis.
""",
    "examples/output-style.md": """\
# Format and style example

All values below are fictional and demonstrate presentation only. A completed
report starts with a short evidence-backed summary, then uses a finding table
with columns for finding, source path, observed condition, consequence, and
confidence. It ends with a source register mapping each material claim to the
exact file or symbol that supports it. Missing evidence is described plainly
and is never converted into a guessed implementation detail.
""",
    "prompts/phase2.md": """\
# Analysis guidance

Answer FINDINGS with concrete conditions present in supplied source evidence.
For each condition, record the affected component, exact source location,
observed behaviour, and bounded consequence. Distinguish direct observations
from missing context and never infer production exposure, ownership, or
exploitability. Nexus platform-analysis policy remains authoritative.
""",
    "prompts/phase3.md": """\
# Writing guidance

Write a concise technical report for a security reviewer. Lead with supported
findings, use tables where the same fields repeat, and cite a source location
for every material claim. Keep directly observed behaviour separate from
unknown context. When evidence is absent, state which artifact would be needed
instead of filling the gap with an assumption.
""",
    "prompts/docs/AUDIT_REPORT.md": """\
# AUDIT_REPORT writer instruction

Turn the routed FINDINGS evidence into a prioritized finding register. Each
entry must include the observed condition, affected component, source path or
symbol, consequence bounded by evidence, and confidence. Add a source register
that maps every material claim back to evidence. State unresolved questions
explicitly and never invent production configuration, owners, or exposure.
""",
}


REPO_MCP_SERVER = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _packs_on_disk(tmp_path, monkeypatch):
    """Keep authored packs inside the test's tmp_path, never the real data dir.

    The REAL starter skeleton is copied in rather than a stub, so
    "a new pack gets the starter" is asserted against what actually ships.
    """
    from mcp_server.config import config

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        REPO_MCP_SERVER / "data" / "swap_pack_starter", data_dir / "swap_pack_starter"
    )
    monkeypatch.setattr(config, "data_dir", data_dir)
    return tmp_path


class _acting_as:
    """Temporarily authenticate the shared client as another user.

    Restores the previous override rather than popping it: the ``client``
    fixture installs its own, and popping would leave the client
    unauthenticated for the remainder of the test.
    """

    def __init__(self, user_id: str, username: str) -> None:
        self._override = _as_user(user_id, username)

    def __enter__(self):
        from mcp_server.api.app import app, require_auth

        self._app, self._key = app, require_auth
        self._previous = app.dependency_overrides.get(require_auth)
        app.dependency_overrides[require_auth] = self._override
        return self

    def __exit__(self, *_exc):
        if self._previous is None:
            self._app.dependency_overrides.pop(self._key, None)
        else:
            self._app.dependency_overrides[self._key] = self._previous
        return False


def _as_user(user_id: str, username: str):
    async def _mock(request: Request) -> dict:
        request.state.auth_payload = {
            "sub": user_id,
            "type": "access",
            "username": username,
        }
        return request.state.auth_payload

    return _mock


async def _add_second_user() -> None:
    async with plugins_mod.async_session_factory() as db:
        await db.execute(
            text("INSERT INTO users (id, username, is_superadmin) VALUES (9, 'other', 0)")
        )
        await db.commit()


async def _create(client, name: str = "My Audit", **kwargs) -> dict:
    response = await client.post(
        "/mcp/plugins/personal",
        json={"name": name, "manifest_yaml": PACK_YAML, **kwargs},
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Authoring
# ---------------------------------------------------------------------------


async def test_authored_pack_is_personal_usable_and_editable(client):
    body = await _create(client)

    assert body["personal"] is True
    assert body["usable"] is True, "a pack you cannot run is not worth authoring"
    assert body["mutable"] is True, "your own pack must be editable"
    assert body["certification_status"] == "personal"
    assert body["origin"] == "personal"
    # Certification means "a reviewer merged this upstream", which has not
    # happened -- so it must not claim otherwise.
    assert body["repo_url"] is None
    assert "path" not in body, "local installation paths must never be exposed"


async def test_authored_pack_is_a_real_pack_tree_under_git(client):
    """Publishing later is a push, not an export -- so it must be a repo now."""
    body = await _create(client)
    async with plugins_mod.async_session_factory() as db:
        pack = (
            await db.execute(select(PluginPack).where(PluginPack.id == body["id"]))
        ).scalar_one()
    pack_dir = Path(pack.path)
    assert (pack_dir / "plugin.yaml").is_file()
    assert (pack_dir / ".git").is_dir()
    assert pack.owner_user_id == 1


async def test_extra_files_are_written(client):
    body = await _create(
        client,
        files={
            "prompts/phase2.md": "Look for X.",
            "prompts/docs/AUDIT_REPORT.md": "Write it like this.",
        },
    )
    listing = await client.get(f"/mcp/plugins/{body['id']}/files")
    assert listing.status_code == 200
    assert "prompts/phase2.md" in listing.json()
    assert "prompts/docs/AUDIT_REPORT.md" in listing.json()


async def test_pack_architect_can_refine_an_existing_personal_pack_in_place(client):
    from mcp_server.config import config

    pack = await _create(
        client,
        files=AI_FILES,
        inherit_starter=False,
    )
    started = await client.post(
        "/mcp/plugins/architect/sessions",
        json={"pack_id": pack["id"]},
    )
    assert started.status_code == 201, started.text
    session = started.json()
    assert session["source_pack_id"] == pack["id"]
    assert session["manifest_yaml"] == PACK_YAML
    assert session["files"] == AI_FILES
    assert session["complete"] is True

    marker = "\nAI refined marker retained in the saved Pack.\n"
    workspace = (
        config.data_dir / "pack_architect_sessions" / session["id"] / "pack"
    )
    readme = workspace / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + marker, encoding="utf-8")

    saved = await client.post(
        f"/mcp/plugins/architect/sessions/{session['id']}/save",
        json={"name": pack["name"], "expected_revision": session["revision"]},
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["id"] == pack["id"], "AI refinement must not create a duplicate Pack"

    file_response = await client.get(f"/mcp/plugins/{pack['id']}/files/README.md")
    assert file_response.status_code == 200
    assert "AI refined marker" in file_response.json()["content"]


async def test_pack_architect_cannot_seed_from_another_users_private_pack(client):
    pack = await _create(client)
    await _add_second_user()

    with _acting_as("9", "other"):
        response = await client.post(
            "/mcp/plugins/architect/sessions",
            json={"pack_id": pack["id"]},
        )

    assert response.status_code == 404


async def test_new_pack_gets_the_whole_starter_not_just_the_manifest(client):
    """Per-document prompts are where output quality comes from.

    A new pack that arrives as a bare plugin.yaml leaves the author needing to
    know that `prompts/docs/<DOC>.md` exists at all. Ship the skeleton.
    """
    response = await client.post("/mcp/plugins/personal", json={"name": "From Starter"})
    assert response.status_code == 201, response.text
    files = (await client.get(f"/mcp/plugins/{response.json()['id']}/files")).json()
    assert "plugin.yaml" in files
    assert any(f.startswith("prompts/") for f in files), files


async def test_caller_files_win_over_the_starter_skeleton(client):
    body = await _create(
        client, name="Own Prompts", files={"prompts/phase2.md": "MY OWN PROMPT"}
    )
    file = await client.get(f"/mcp/plugins/{body['id']}/files/prompts/phase2.md")
    assert file.status_code == 200
    assert file.json()["content"] == "MY OWN PROMPT"


async def test_complete_ai_pack_does_not_inherit_unrelated_starter_files(client):
    response = await client.post(
        "/mcp/plugins/personal",
        json={
            "name": "AI Audit",
            "manifest_yaml": PACK_YAML,
            "files": AI_FILES,
            "inherit_starter": False,
        },
    )
    assert response.status_code == 201, response.text

    files = set((await client.get(f"/mcp/plugins/{response.json()['id']}/files")).json())
    assert files == {"plugin.yaml", *AI_FILES}
    assert "prompts/docs/SINGLE_SUMMARY.md" not in files
    assert "examples/single-summary-example.md" not in files


async def test_incomplete_ai_pack_is_rejected_before_it_is_written(client):
    response = await client.post(
        "/mcp/plugins/personal",
        json={
            "name": "Incomplete AI Audit",
            "manifest_yaml": PACK_YAML,
            "files": {"prompts/phase2.md": AI_FILES["prompts/phase2.md"]},
            "inherit_starter": False,
        },
    )
    assert response.status_code == 422
    assert "Incomplete generated pack" in response.json()["detail"]
    assert "README.md" in response.json()["detail"]

    listing = await client.get("/mcp/plugins")
    assert listing.json() == [], "a rejected AI pack must leave no trace"


async def test_creation_rejects_platform_analysis_replacement_file(client):
    response = await client.post(
        "/mcp/plugins/personal",
        json={
            "name": "Unsafe Platform Override",
            "manifest_yaml": PACK_YAML,
            "files": {
                "prompts/phase2_platform_sections/talend.md": "replace the Nexus guide"
            },
        },
    )
    assert response.status_code == 422
    assert "platform analysis prompts are immutable" in response.json()["detail"]


async def test_invalid_manifest_is_rejected_before_anything_is_written(client):
    response = await client.post(
        "/mcp/plugins/personal",
        json={
            "name": "Broken",
            # replace mode with no declared sections: a real validation error.
            "manifest_yaml": "name: b\nversion: '1.0'\nlens:\n  sections:\n    mode: replace\n",
        },
    )
    assert response.status_code == 422
    assert "declared section" in response.json()["detail"]

    listing = await client.get("/mcp/plugins")
    assert listing.json() == [], "a rejected pack must leave no trace"


async def test_disallowed_file_extension_is_rejected(client):
    response = await client.post(
        "/mcp/plugins/personal",
        json={
            "name": "Sneaky",
            "manifest_yaml": PACK_YAML,
            "files": {"run.sh": "#!/bin/sh\nrm -rf /"},
        },
    )
    assert response.status_code == 422


async def test_duplicate_name_for_the_same_user_is_rejected(client):
    await _create(client)
    response = await client.post(
        "/mcp/plugins/personal", json={"name": "My Audit", "manifest_yaml": PACK_YAML}
    )
    assert response.status_code == 409


async def test_two_users_may_each_own_a_pack_of_the_same_name(client):
    """Per-owner uniqueness: names are personal, not a global namespace."""
    await _add_second_user()
    first = await _create(client)

    with _acting_as("9", "other"):
        second = await _create(client)

    assert first["id"] != second["id"]
    assert first["name"] == second["name"]


# ---------------------------------------------------------------------------
# Privacy
# ---------------------------------------------------------------------------


async def test_personal_pack_is_invisible_to_other_users(client):
    """The property that must never regress: nobody else can see or touch it.

    404 rather than 403 throughout -- a 403 would confirm the pack exists.
    """
    await _add_second_user()
    body = await _create(client)
    pack_id = body["id"]

    with _acting_as("9", "other"):
        assert (await client.get("/mcp/plugins")).json() == []

        for method, path, payload in [
            ("GET", f"/mcp/plugins/{pack_id}/files", None),
            ("GET", f"/mcp/plugins/{pack_id}/files/plugin.yaml", None),
            ("PUT", f"/mcp/plugins/{pack_id}/files/plugin.yaml", {"content": PACK_YAML}),
            ("DELETE", f"/mcp/plugins/{pack_id}", None),
            ("POST", f"/mcp/plugins/{pack_id}/publish", {}),
        ]:
            response = await client.request(method, path, json=payload)
            assert response.status_code == 404, (
                f"{method} {path} leaked another user's pack: {response.status_code}"
            )

    # And the owner still sees it, so the filter is not simply hiding everything.
    assert [p["id"] for p in (await client.get("/mcp/plugins")).json()] == [pack_id]


async def test_another_users_pack_cannot_be_selected_for_a_project(client):
    """Project access is broader than pack ownership; selection must respect both."""
    await _add_second_user()
    body = await _create(client)

    await client.post(
        "/mcp/add-repo", json={"url": "zip", "project_id": "shared-proj", "platform": "talend"}
    )
    async with plugins_mod.async_session_factory() as db:
        await db.execute(
            text(
                "INSERT INTO project_assignments (user_id, project_id, is_owner) "
                "VALUES (9, 'shared-proj', 0)"
            )
        )
        await db.commit()

    with _acting_as("9", "other"):
        response = await client.patch(
            "/mcp/projects/shared-proj/plugin", json={"plugin_pack_id": body["id"]}
        )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Editing and selection
# ---------------------------------------------------------------------------


async def test_owner_can_edit_and_the_edit_is_committed(client):
    body = await _create(client)
    updated = PACK_YAML.replace("version: 0.1.0", "version: 0.2.0")

    response = await client.put(
        f"/mcp/plugins/{body['id']}/files/plugin.yaml", json={"content": updated}
    )
    assert response.status_code == 200

    listing = (await client.get("/mcp/plugins")).json()
    assert listing[0]["version"] == "0.2.0", "manifest metadata must follow the edit"
    assert listing[0]["mutable"] is True, "editing must not lock the pack"
    assert listing[0]["usable"] is True, "editing must not disable the pack"


async def test_invalid_manifest_edit_is_rejected(client):
    body = await _create(client)
    response = await client.put(
        f"/mcp/plugins/{body['id']}/files/plugin.yaml",
        json={"content": "lens:\n  focus:\n    include: [not_a_category]\n"},
    )
    assert response.status_code == 422


async def test_personal_pack_can_be_selected_for_generation(client):
    """Without this the lane is decorative: authoring must lead to running."""
    body = await _create(client)
    await client.post(
        "/mcp/add-repo", json={"url": "zip", "project_id": "mine", "platform": "talend"}
    )
    response = await client.patch(
        "/mcp/projects/mine/plugin", json={"plugin_pack_id": body["id"]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["plugin_pack_id"] == body["id"]


async def test_owner_can_delete_and_the_project_selection_is_cleared(client):
    body = await _create(client)
    await client.post(
        "/mcp/add-repo", json={"url": "zip", "project_id": "todelete", "platform": "talend"}
    )
    await client.patch("/mcp/projects/todelete/plugin", json={"plugin_pack_id": body["id"]})

    async with plugins_mod.async_session_factory() as db:
        pack = (
            await db.execute(select(PluginPack).where(PluginPack.id == body["id"]))
        ).scalar_one()
        pack_dir = Path(pack.path)

    assert (await client.delete(f"/mcp/plugins/{body['id']}")).status_code == 204
    assert (await client.get("/mcp/plugins")).json() == []
    assert not pack_dir.exists(), "content must not outlive the row"

    # A dangling plugin_pack_id would break every later generate for the project.
    from mcp_server.db.models import Project

    async with plugins_mod.async_session_factory() as db:
        project = (
            await db.execute(select(Project).where(Project.id == "todelete"))
        ).scalar_one()
    assert project.plugin_pack_id is None


async def test_personal_pack_cannot_be_pulled(client):
    body = await _create(client)
    response = await client.patch(f"/mcp/plugins/{body['id']}", json={"pull": True})
    assert response.status_code == 422
    assert "no upstream" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Publishing asks; merging grants
# ---------------------------------------------------------------------------


async def test_publish_pushes_a_branch_and_opens_a_pull_request(client, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "pack_repo_token", "test-token")
    monkeypatch.setattr(
        config, "swap_pack_starter_repo_url", "https://github.com/acme/packs"
    )

    pushed: list[tuple] = []
    real_git = plugins_mod._git_output

    async def fake_git(cwd: str, *args: str, timeout: float = 120.0):
        if args and args[0] == "push":
            pushed.append(args)
            return ""
        return await real_git(cwd, *args, timeout=timeout)

    opened: list[dict] = []

    async def fake_pr(**kwargs):
        opened.append(kwargs)
        return "https://github.com/acme/packs/pull/7"

    monkeypatch.setattr(plugins_mod, "_git_output", fake_git)
    monkeypatch.setattr(plugins_mod, "_open_pull_request", fake_pr)

    body = await _create(client)
    response = await client.post(f"/mcp/plugins/{body['id']}/publish", json={})
    assert response.status_code == 200, response.text
    published = response.json()

    assert published["branch"] == "pack/my-audit"
    assert published["pull_request_url"] == "https://github.com/acme/packs/pull/7"
    assert published["note"] is None
    assert len(published["commit_sha"]) == 40
    assert pushed, "the branch must actually be pushed"
    assert opened[0]["owner_repo"] == "acme/packs"
    assert opened[0]["base"] == "main"
    assert opened[0]["branch"] == "pack/my-audit"
    # The push URL carries the token; the response must not.
    assert "test-token" not in response.text

    # THE point: publishing asks. It does not make the pack shared or certified.
    listing = (await client.get("/mcp/plugins")).json()
    assert listing[0]["personal"] is True
    assert listing[0]["certification_status"] == "personal"
    async with plugins_mod.async_session_factory() as db:
        pack = (
            await db.execute(select(PluginPack).where(PluginPack.id == body["id"]))
        ).scalar_one()
    assert pack.owner_user_id == 1, "publishing must not reassign ownership"


async def test_publish_reports_a_pushed_branch_even_when_the_pr_fails(client, monkeypatch):
    """A failed PR is not a failed publish: the branch is already up there."""
    from mcp_server.config import config

    monkeypatch.setattr(config, "pack_repo_token", "test-token")
    real_git = plugins_mod._git_output

    async def fake_git(cwd: str, *args: str, timeout: float = 120.0):
        if args and args[0] == "push":
            return ""
        return await real_git(cwd, *args, timeout=timeout)

    async def failed_pr(**_kwargs):
        return None

    monkeypatch.setattr(plugins_mod, "_git_output", fake_git)
    monkeypatch.setattr(plugins_mod, "_open_pull_request", failed_pr)

    body = await _create(client)
    response = await client.post(f"/mcp/plugins/{body['id']}/publish", json={})
    assert response.status_code == 200
    assert response.json()["pull_request_url"] is None
    assert "pushed" in response.json()["note"]


async def test_publish_resolves_a_github_app_installation_token(client, monkeypatch):
    """The App lane must actually read the installation records it is given.

    Verified defect: `list_app_installations` returns normalized records
    ({installation_id, account_login}); this code read GitHub's raw shape
    (installation["account"]["login"], ["id"]), matched nothing, fell through to
    the absent PAT, and reported "publishing is not configured" on an instance
    where the App was installed and working.
    """
    from mcp_server.config import config

    monkeypatch.setattr(config, "pack_repo_token", "")
    monkeypatch.setattr(
        config, "swap_pack_starter_repo_url", "https://github.com/acme/packs"
    )
    monkeypatch.setattr(plugins_mod, "github_app_configured", lambda: True)

    async def installations():
        return [
            {"installation_id": 999, "account_login": "someone-else"},
            {"installation_id": 155, "account_login": "AcMe"},  # case-insensitive
        ]

    minted: list[int] = []

    async def mint(installation_id: int):
        minted.append(installation_id)
        return "app-token"   # a str, NOT a (token, expiry) tuple

    monkeypatch.setattr(plugins_mod, "list_app_installations", installations)
    monkeypatch.setattr(plugins_mod, "mint_installation_token", mint)

    token = await plugins_mod._pack_repo_push_token()
    assert token == "app-token"
    assert minted == [155], "must mint for the installation owning the pack repo"


async def test_publish_falls_back_to_the_pat_when_no_installation_matches(
    client, monkeypatch
):
    from mcp_server.config import config

    monkeypatch.setattr(config, "pack_repo_token", "pat-token")
    monkeypatch.setattr(
        config, "swap_pack_starter_repo_url", "https://github.com/acme/packs"
    )
    monkeypatch.setattr(plugins_mod, "github_app_configured", lambda: True)

    async def installations():
        return [{"installation_id": 1, "account_login": "unrelated"}]

    monkeypatch.setattr(plugins_mod, "list_app_installations", installations)
    assert await plugins_mod._pack_repo_push_token() == "pat-token"


async def test_publish_without_credentials_says_so_instead_of_doing_nothing(
    client, monkeypatch
):
    from mcp_server.config import config

    monkeypatch.setattr(config, "pack_repo_token", "")
    monkeypatch.setattr(plugins_mod, "github_app_configured", lambda: False)

    body = await _create(client)

    async def should_not_push(*_args, **_kwargs):
        raise AssertionError("nothing may be pushed without a resolved token")

    monkeypatch.setattr(plugins_mod, "_git_output", should_not_push)
    response = await client.post(f"/mcp/plugins/{body['id']}/publish", json={})
    assert response.status_code == 503
    assert "MCP_PACK_REPO_TOKEN" in response.json()["detail"]


async def test_publish_names_the_cause_when_the_credential_is_refused(
    client, monkeypatch
):
    """Verified failure: the shipped GitHub App had `contents: read`, so clone
    and discovery worked and only push failed, reported as a bare 502.

    The raised text here is what production ACTUALLY raises -- ``run_git``
    sanitizes before raising, so a test feeding raw git stderr passes against a
    handler that fails live. That is exactly what happened the first time.
    """
    from mcp_server.config import config

    monkeypatch.setattr(config, "pack_repo_token", "test-token")
    body = await _create(client)
    real_git = plugins_mod._git_output

    async def denied(cwd: str, *args: str, timeout: float = 120.0):
        if args and args[0] == "push":
            # Verbatim what run_git raises for this case: sanitized, bucketed.
            raise RuntimeError("error_type: authentication_failed")
        return await real_git(cwd, *args, timeout=timeout)

    monkeypatch.setattr(plugins_mod, "_git_output", denied)

    response = await client.post(f"/mcp/plugins/{body['id']}/publish", json={})
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert "Contents: Read and write" in detail
    assert "Nothing was pushed" in detail
    assert "MCP_PACK_REPO_TOKEN" in detail


async def test_publish_still_reports_unexpected_push_failures_as_such(
    client, monkeypatch
):
    """Only permission denials get the specific message; nothing else is
    swallowed into a misleading one."""
    from mcp_server.config import config

    monkeypatch.setattr(config, "pack_repo_token", "test-token")
    body = await _create(client)
    real_git = plugins_mod._git_output

    async def unreachable(cwd: str, *args: str, timeout: float = 120.0):
        if args and args[0] == "push":
            raise RuntimeError("fatal: could not resolve host: github.com")
        return await real_git(cwd, *args, timeout=timeout)

    monkeypatch.setattr(plugins_mod, "_git_output", unreachable)
    response = await client.post(f"/mcp/plugins/{body['id']}/publish", json={})
    assert response.status_code == 502
    assert "Contents: Read and write" not in response.json()["detail"]


def test_sanitized_git_errors_are_classified_the_way_run_git_emits_them():
    """Pin the coupling: run_git sanitizes, so the handler must recognise the
    sanitizer's own output, not raw git stderr."""
    from mcp_server.utils.error_sanitizer import sanitize_git_error

    raw = (
        "remote: Permission to acme/packs.git denied to nexus-app[bot].\n"
        "fatal: unable to access "
        "'https://x-access-token:ghs_SECRET@github.com/acme/packs/': 403"
    )
    sanitized = sanitize_git_error(raw)
    assert "ghs_SECRET" not in sanitized, "the sanitizer must strip the token"
    assert plugins_mod._is_credential_rejected(sanitized)
    assert not plugins_mod._is_credential_rejected(
        sanitize_git_error("fatal: could not resolve host: github.com")
    )


async def test_publish_rejects_an_unsafe_branch_name(client, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "pack_repo_token", "test-token")

    body = await _create(client)

    async def should_not_push(*_args, **_kwargs):
        raise AssertionError("a rejected branch name must never reach git")

    monkeypatch.setattr(plugins_mod, "_git_output", should_not_push)
    response = await client.post(
        f"/mcp/plugins/{body['id']}/publish", json={"branch": "--upload-pack=evil"}
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Prompt slots: what the authoring UI is allowed to believe
# ---------------------------------------------------------------------------


async def test_prompt_slots_are_listed_without_their_bodies(client):
    """The list is fetched on every editor render; the bodies are ~90 KB.

    Also pins that the route resolves at all: ``/plugins/prompt-slots`` sits
    next to ``/plugins/{pack_id}/...``, and if it were declared after the
    id-scoped routes a future non-int pack_id would shadow it.
    """
    response = await client.get("/mcp/plugins/prompt-slots")
    assert response.status_code == 200, response.text
    slots = response.json()
    assert slots
    for slot in slots:
        assert "content" not in slot
        assert slot["kind"] in {"preamble", "body", "document", "platform_extension"}
    paths = [s["path"] for s in slots]
    assert "prompts/templates/phase3_doc.md" in paths
    assert "prompts/phase2.md" in paths
    assert "prompts/extensions/platforms/talend.md" in paths
    assert "prompts/phase2_platform_sections/talend.md" not in paths
    # Both scorecard slots, which is the collision the two namespaces exist for.
    assert "prompts/scorecard.md" in paths
    assert "prompts/templates/scorecard.md" in paths


async def test_prompt_slot_body_is_served_for_seeding_an_override(client):
    response = await client.get("/mcp/plugins/prompt-slots/prompts/templates/phase3_doc.md")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "body"
    assert body["content"].strip()
    assert body["default_bytes"] == len(body["content"])
    on_disk = (
        REPO_MCP_SERVER / "data" / "prompt_templates" / "phase3_doc.md"
    ).read_text(encoding="utf-8")
    # Byte-identical: an override replaces the built-in, so a stale seed would
    # silently revert whatever changed since.
    assert body["content"] == on_disk


async def test_unknown_prompt_slot_is_404_and_says_where_to_look(client):
    response = await client.get("/mcp/plugins/prompt-slots/prompts/templates/nope.md")
    assert response.status_code == 404
    assert "prompt-slots" in response.json()["detail"]


async def test_prompt_slot_path_cannot_escape_the_catalog(client):
    """The lookup is an allowlist match, not a filesystem join."""
    for attempt in (
        "prompts/../../../../etc/passwd",
        "../../plugin.yaml",
        "prompts/templates/phase2_patch.md",  # real template, deliberately unreachable
    ):
        response = await client.get(f"/mcp/plugins/prompt-slots/{attempt}")
        assert response.status_code == 404, f"{attempt} -> {response.status_code}"


async def test_a_body_override_saved_through_the_editor_lands_in_the_pack(client):
    """End to end: seed a body, write it, and it is a real file in the tree.

    The write path already accepted arbitrary relative paths, so this pins that
    ``prompts/templates/`` specifically survives the round trip and appears in
    the file listing the editor renders.
    """
    pack = await _create(client)
    seed = await client.get("/mcp/plugins/prompt-slots/prompts/templates/phase3_doc.md")
    content = seed.json()["content"].replace(
        "Generate", "Generate (audit variant)", 1
    )

    write = await client.put(
        f"/mcp/plugins/{pack['id']}/files/prompts/templates/phase3_doc.md",
        json={"content": content},
    )
    assert write.status_code == 200, write.text

    listing = await client.get(f"/mcp/plugins/{pack['id']}/files")
    assert "prompts/templates/phase3_doc.md" in listing.json()

    read_back = await client.get(
        f"/mcp/plugins/{pack['id']}/files/prompts/templates/phase3_doc.md"
    )
    assert read_back.json()["content"] == content


async def test_editor_rejects_platform_analysis_replacement_file(client):
    pack = await _create(client)
    response = await client.put(
        f"/mcp/plugins/{pack['id']}/files/"
        "prompts/phase2_platform_sections/talend.md",
        json={"content": "replace the Nexus Talend guide"},
    )
    assert response.status_code == 422
    assert "immutable" in response.json()["detail"].lower()
    assert "prompts/extensions/platforms" in response.json()["detail"]
