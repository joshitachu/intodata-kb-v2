from __future__ import annotations

import httpx
import pytest

from src.harness.providers.mcp_docs import (
    McpDocumentationProvider,
    McpDocumentationProviderError,
)


async def test_trigger_generation_posts_minimal_payload_and_forwards_cookie() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["payload"] = request.read()
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(
            200,
            json={
                "status": "generation_triggered",
                "message": "Doc generation started for fastapi-e2e",
            },
        )

    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp/",
        transport=httpx.MockTransport(handler),
    )

    result = await provider.trigger_generation(project_id="fastapi-e2e", access_token="user-jwt")

    assert seen["path"] == "/mcp/generate"
    assert seen["payload"] == b'{"project_id":"fastapi-e2e"}'
    assert seen["cookie"] == "access_token=user-jwt"
    assert result.status == "generation_triggered"
    assert result.message == "Doc generation started for fastapi-e2e"
    assert result.status_path == "/mcp/generate-runs/fastapi-e2e"


async def test_trigger_generation_passes_only_explicit_options() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = request.read()
        return httpx.Response(200, json={"status": "generation_triggered", "message": "started"})

    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(handler),
    )

    await provider.trigger_generation(
        project_id="p1",
        investigation_model="anthropic/claude-sonnet-4-5",
        write_model="openai/gpt-5",
        plugin_pack_id=7,
        resume=False,
    )

    assert captured["json"] == (
        b'{"project_id":"p1","investigation_model":"anthropic/claude-sonnet-4-5",'
        b'"write_model":"openai/gpt-5","plugin_pack_id":7,"resume":false}'
    )


async def test_list_documentation_packs_returns_safe_metadata_without_paths() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(
            200,
            json=[
                {
                    "id": 7,
                    "name": "release-branch",
                    "manifest_name": "Talend migration",
                    "version": "2.1.0",
                    "description": "Talend to Azure documentation instructions.",
                    "path": "/private/plugin-packs/talend-migration",
                }
            ],
        )

    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(handler),
    )

    packs = await provider.list_documentation_packs(access_token="user-jwt")

    assert seen == {"path": "/mcp/plugins", "cookie": "access_token=user-jwt"}
    assert packs[0].id == 7
    assert packs[0].name == "Talend migration"
    assert packs[0].version == "2.1.0"
    assert not hasattr(packs[0], "path")


async def test_project_documentation_pack_get_and_set_forward_cookie() -> None:
    seen: list[tuple[str, str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.read()))
        if request.method == "GET":
            return httpx.Response(200, json={"projects": [{"id": "p1", "plugin_pack_id": 7}], "total": 1})
        return httpx.Response(200, json={"project_id": "p1", "plugin_pack_id": 8})

    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(handler),
    )

    assert await provider.get_project_documentation_pack_id(project_id="p1", access_token="jwt") == 7
    assert await provider.set_project_documentation_pack(project_id="p1", plugin_pack_id=8, access_token="jwt") == 8
    assert seen == [
        ("GET", "/mcp/projects", b""),
        ("PATCH", "/mcp/projects/p1/plugin", b'{"plugin_pack_id":8}'),
    ]


@pytest.mark.parametrize("plugin_pack_id", [0, -1, True, "7"])
async def test_trigger_generation_rejects_invalid_plugin_pack_override(plugin_pack_id: object) -> None:
    provider = McpDocumentationProvider(base_url="http://mcp.internal/mcp")

    with pytest.raises(McpDocumentationProviderError, match="Invalid documentation pack selection"):
        await provider.trigger_generation(project_id="p1", plugin_pack_id=plugin_pack_id)  # type: ignore[arg-type]


async def test_http_failure_raises_sanitized_message() -> None:
    secret = "sk-r33-super-secret-token"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={
                "detail": (
                    f"Authorization: Bearer {secret} failed at "
                    f"http://mcp.internal/mcp/generate?access_token={secret}"
                )
            },
        )

    provider = McpDocumentationProvider(
        base_url=f"http://mcp.internal/mcp?api_key={secret}",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(McpDocumentationProviderError) as exc_info:
        await provider.trigger_generation(project_id="p1", access_token=secret)

    message = exc_info.value.safe_message
    assert "HTTP 500" in message
    assert secret not in message
    assert "Authorization" not in message
    assert "http://mcp.internal" not in message
    assert "[REDACTED]" in message or "[URL_REDACTED]" in message


async def test_timeout_raises_sanitized_timeout_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(
            "timeout while calling http://mcp.internal/mcp/generate?access_token=secret-token",
            request=request,
        )

    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        timeout=0.25,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(McpDocumentationProviderError) as exc_info:
        await provider.trigger_generation(project_id="p1", access_token="secret-token")

    assert exc_info.value.safe_message == "Documentation generation provider timed out after 0.25s."


async def test_timeout_reconciles_an_already_accepted_generation() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.method == "POST":
            raise httpx.ReadTimeout("late response", request=request)
        return httpx.Response(
            200,
            json={"runs": [{"id": 42, "status": "running"}], "total": 1},
        )

    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        timeout=0.25,
        transport=httpx.MockTransport(handler),
    )

    result = await provider.trigger_generation(project_id="p1")

    assert result.status == "generation_reconciled"
    assert seen == ["/mcp/generate", "/mcp/generate-runs/p1"]


async def test_get_generation_status_returns_latest_authoritative_receipt() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/mcp/generate-runs/p1"
        return httpx.Response(
            200,
            json={
                "runs": [
                    {
                        "id": 41,
                        "project_id": "p1",
                        "status": "completed",
                        "started_at": "2026-07-11T10:00:00Z",
                        "completed_at": "2026-07-11T10:05:00Z",
                        "doc_count": 10,
                        "quality_score": 3.2,
                        "run_verdict": "failed",
                        "gate_results": {
                            "client_ready": False,
                            "gates": {
                                "coverage": {"passed": True},
                                "evidence_quality": {"passed": False},
                            },
                        },
                    },
                    {"id": 40, "project_id": "p1", "status": "failed"},
                ],
                "total": 2,
            },
        )

    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(handler),
    )

    result = await provider.get_generation_status(project_id="p1")

    assert result.run_id == 41
    assert result.status == "completed"
    assert result.doc_count == 10
    assert result.quality_score == 3.2
    assert result.client_ready is False
    assert result.failed_gates == ["evidence_quality"]


async def test_get_generation_status_distinguishes_no_run_from_no_docs() -> None:
    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"runs": [], "total": 0})
        ),
    )

    result = await provider.get_generation_status(project_id="p1")

    assert result.found is False
    assert result.status == "not_started"
    assert result.doc_count == 0


async def test_document_version_is_derived_from_authorized_document_content() -> None:
    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"filename": "04_RUNBOOK.md", "content": "# Runbook\n"},
            )
        ),
    )

    version = await provider.get_document_version(
        project_id="p1", filename="04_RUNBOOK.md"
    )

    assert version.sha256.startswith("sha256:")
    assert len(version.sha256) == 71


async def test_document_version_accepts_mcp_raw_markdown_response() -> None:
    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                text="# Runbook\n",
                headers={"content-type": "text/markdown; charset=utf-8"},
            )
        ),
    )

    version = await provider.get_document_version(
        project_id="p1", filename="04_RUNBOOK.md"
    )

    assert version.sha256.startswith("sha256:")
    assert len(version.sha256) == 71


async def test_revert_update_returns_versioned_receipt() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/04_RUNBOOK.md/evidence-revert")
        return httpx.Response(
            200,
            json={
                "project_id": "p1",
                "filename": "04_RUNBOOK.md",
                "status": "reverted",
                "message": "Restored prior version.",
                "before_sha": "sha256:new",
                "after_sha": "sha256:old",
                "version_id": "sha256:old",
                "reverted_receipt_path": "_harness_doc_updates/update.json",
                "receipt_path": "_harness_doc_updates/revert.json",
                "docs_path": "/projects/p1/docs/04_RUNBOOK.md",
                "rag_result": {"pushed": 1},
                "reconciliation_status": "indexed",
            },
        )

    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(handler),
    )
    result = await provider.revert_doc_evidence_update(
        project_id="p1",
        filename="04_RUNBOOK.md",
        receipt_path="_harness_doc_updates/update.json",
        expected_base_sha="sha256:new",
    )

    assert result.status == "reverted"
    assert result.version_id == "sha256:old"
    assert result.reconciliation_status == "indexed"


@pytest.mark.parametrize("project_id", ["../admin", "project/a", "project%2Fa", ""])
async def test_provider_rejects_unsafe_project_path_segments(project_id: str) -> None:
    provider = McpDocumentationProvider(base_url="http://mcp.internal/mcp")

    with pytest.raises(McpDocumentationProviderError, match="Invalid project scope"):
        await provider.list_project_docs(project_id=project_id)


@pytest.mark.parametrize("filename", ["../runbook.md", "docs/runbook.md", "runbook", "x%2Fy.md"])
async def test_provider_rejects_unsafe_document_filenames(filename: str) -> None:
    provider = McpDocumentationProvider(base_url="http://mcp.internal/mcp")

    with pytest.raises(McpDocumentationProviderError, match="Invalid documentation filename"):
        await provider.update_doc_from_evidence(
            project_id="p1",
            filename=filename,
            evidence_text="evidence",
        )


async def test_invalid_response_is_sanitized() -> None:
    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="not-json")
        ),
    )

    with pytest.raises(McpDocumentationProviderError) as exc_info:
        await provider.trigger_generation(project_id="p1")

    assert (
        exc_info.value.safe_message
        == "Documentation generation provider returned an invalid response."
    )


async def test_missing_base_url_fails_before_network_call() -> None:
    provider = McpDocumentationProvider(base_url=" ")

    with pytest.raises(McpDocumentationProviderError) as exc_info:
        await provider.trigger_generation(project_id="p1")

    assert exc_info.value.safe_message == "Documentation generation provider is not configured."


async def test_list_project_docs_returns_safe_metadata_and_forwards_cookie() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["query"] = request.url.query.decode()
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(
            200,
            json={
                "project_id": "p1",
                "docs": [
                    {
                        "name": "runbook.md",
                        "type": "md",
                        "size_bytes": 120,
                        "has_pdf": True,
                        "modified_at": "2026-07-10T10:00:00+00:00",
                    }
                ],
                "total": 1,
            },
        )

    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(handler),
    )

    docs = await provider.list_project_docs(project_id="p1", access_token="jwt-token")

    assert seen["path"] == "/mcp/projects/p1/docs"
    assert seen["query"] == "source=published"
    assert seen["cookie"] == "access_token=jwt-token"
    assert docs[0].name == "runbook.md"
    assert docs[0].has_pdf is True


async def test_list_projects_returns_visible_project_metadata_and_forwards_cookie() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(
            200,
            json={
                "projects": [
                    {
                        "id": "project-a",
                        "display_name": "Project A",
                        "platform": "generic",
                        "source_type": "github",
                        "status": "ready",
                        "doc_count": 9,
                        "job_count": 3,
                    }
                ],
                "total": 1,
            },
        )

    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(handler),
    )

    projects = await provider.list_projects(access_token="jwt-token")

    assert seen["path"] == "/mcp/projects"
    assert seen["cookie"] == "access_token=jwt-token"
    assert projects[0].id == "project-a"
    assert projects[0].display_name == "Project A"
    assert projects[0].doc_count == 9


async def test_update_doc_from_evidence_posts_payload_and_forwards_cookie() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["payload"] = request.read()
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(
            200,
            json={
                "project_id": "p1",
                "filename": "04_RUNBOOK.md",
                "status": "updated",
                "message": "Updated 04_RUNBOOK.md from provided evidence.",
                "workflow_run_id": "workflow-1",
                "evidence_refs": ["chat_prompt"],
                "before_sha": "sha256:before",
                "after_sha": "sha256:after",
                "receipt_path": "_harness_doc_updates/receipt.json",
                "docs_path": "/projects/p1/docs/04_RUNBOOK.md",
                "rag_result": {"pushed": 1, "skipped": 0, "errors": []},
            },
        )

    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(handler),
    )

    result = await provider.update_doc_from_evidence(
        project_id="p1",
        filename="04_RUNBOOK.md",
        evidence_text="Nightly sync moved to 02:00 UTC.",
        evidence_refs=["chat_prompt"],
        workflow_run_id="workflow-1",
        access_token="jwt-token",
    )

    assert seen["path"] == "/mcp/projects/p1/docs/04_RUNBOOK.md/evidence-update"
    assert (
        seen["payload"]
        == b'{"evidence_text":"Nightly sync moved to 02:00 UTC.",'
        b'"evidence_refs":["chat_prompt"],"workflow_run_id":"workflow-1"}'
    )
    assert seen["cookie"] == "access_token=jwt-token"
    assert result.status == "updated"
    assert result.filename == "04_RUNBOOK.md"
    assert result.rag_result["pushed"] == 1
