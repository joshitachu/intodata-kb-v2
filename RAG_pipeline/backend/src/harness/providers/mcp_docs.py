"""MCP documentation-generation provider.

This adapter is deliberately thin: it triggers the existing MCP `/generate`
workflow and returns a safe summary. MCP remains responsible for project access,
generation orchestration, staging, publishing, and run records.
"""

from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass
from typing import Any

import httpx

from src.config import settings


_SECRET_PATTERNS = [
    re.compile(r"(?i)\bAuthorization\s*[:=]\s*Bearer\s+[A-Za-z0-9._+/=\-]+"),
    re.compile(r"(?i)\b(access_token|refresh_token|api[_-]?key|token|secret)=([^&\s]+)"),
    re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._+/=\-]{10,}"),
]
_MAX_SAFE_ERROR_CHARS = 240
_PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
_MARKDOWN_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\.md$", re.I)


@dataclass(frozen=True)
class McpDocumentationGenerationResult:
    project_id: str
    status: str
    message: str
    status_path: str
    docs_path: str


@dataclass(frozen=True)
class McpDocumentationGenerationStatus:
    project_id: str
    found: bool
    run_id: int | None
    status: str
    started_at: str | None
    completed_at: str | None
    doc_count: int
    quality_score: float | None
    run_verdict: str | None
    client_ready: bool | None
    failed_gates: list[str]
    error_message: str | None
    status_path: str
    docs_path: str


@dataclass(frozen=True)
class McpProjectDocInfo:
    name: str
    type: str
    size_bytes: int
    has_pdf: bool
    modified_at: str


@dataclass(frozen=True)
class McpProjectInfo:
    id: str
    display_name: str
    platform: str
    source_type: str
    status: str
    doc_count: int
    job_count: int
    plugin_pack_id: int | None


@dataclass(frozen=True)
class McpDocumentationPack:
    """Safe metadata for an installed MCP document-generation pack."""

    id: int
    name: str
    version: str
    description: str | None


@dataclass(frozen=True)
class McpDocumentationPackDocumentDraft:
    name: str
    sections: list[str]
    instructions: str | None = None


@dataclass(frozen=True)
class McpEvidenceDocUpdateResult:
    project_id: str
    filename: str
    status: str
    message: str
    workflow_run_id: str | None
    evidence_refs: list[str]
    before_sha: str
    after_sha: str
    receipt_path: str
    docs_path: str
    rag_result: dict[str, Any]
    version_id: str
    section: str | None
    revert_path: str
    reconciliation_status: str


@dataclass(frozen=True)
class McpDocumentVersion:
    project_id: str
    filename: str
    sha256: str


@dataclass(frozen=True)
class McpEvidenceDocRevertResult:
    project_id: str
    filename: str
    status: str
    message: str
    workflow_run_id: str | None
    before_sha: str
    after_sha: str
    version_id: str
    reverted_receipt_path: str
    receipt_path: str
    docs_path: str
    rag_result: dict[str, Any]
    reconciliation_status: str


class McpDocumentationProviderError(Exception):
    """Provider failure whose message is safe for chat/UI surfaces."""

    def __init__(self, safe_message: str) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


class McpDocumentationProvider:
    """HTTP client for triggering MCP documentation generation."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        configured_base_url = base_url if base_url is not None else settings.mcp_docs_base_url
        self._base_url = configured_base_url.strip().rstrip("/")
        self._timeout = timeout if timeout is not None else settings.mcp_docs_request_timeout
        self._transport = transport

    async def trigger_generation(
        self,
        *,
        project_id: str,
        access_token: str | None = None,
        investigation_model: str | None = None,
        model: str | None = None,
        write_model: str | None = None,
        plugin_pack_id: int | None = None,
        nexus_pack_ids: list[str] | None = None,
        resume: bool | None = None,
        mode: str | None = None,
    ) -> McpDocumentationGenerationResult:
        """Trigger MCP doc generation for a project.

        Optional model/plugin values are passed through only when a caller
        already has them. The access token is forwarded as MCP's `access_token`
        cookie so MCP performs the final project-access check.
        """
        if not self._base_url:
            raise McpDocumentationProviderError(
                "Documentation generation provider is not configured."
            )
        project_id = _validated_project_id(project_id)

        payload: dict[str, Any] = {"project_id": project_id}
        _set_if_present(payload, "investigation_model", investigation_model)
        _set_if_present(payload, "model", model)
        _set_if_present(payload, "write_model", write_model)
        if plugin_pack_id is not None:
            payload["plugin_pack_id"] = _validated_plugin_pack_id(plugin_pack_id)
        if nexus_pack_ids is not None:
            if not all(isinstance(pack_id, str) and pack_id for pack_id in nexus_pack_ids):
                raise McpDocumentationProviderError("Invalid selected documentation packs.")
            payload["nexus_pack_ids"] = sorted(set(nexus_pack_ids))
        _set_if_present(payload, "resume", resume)
        _set_if_present(payload, "mode", mode)

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                cookies={"access_token": access_token} if access_token else None,
            ) as client:
                response = await client.post(
                    "/generate",
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            reconciled = await self._reconcile_generation_after_timeout(
                project_id=project_id,
                access_token=access_token,
            )
            if reconciled is not None:
                return reconciled
            raise McpDocumentationProviderError(
                f"Documentation generation provider timed out after {self._timeout:g}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise McpDocumentationProviderError(
                _safe_error("Documentation generation provider could not be reached", str(exc))
            ) from exc

        if response.status_code >= 400:
            raise McpDocumentationProviderError(
                _safe_error(
                    f"Documentation generation provider returned HTTP {response.status_code}",
                    _response_error_text(response),
                )
            )

        data = _json_object(response)
        status = str(data.get("status") or "generation_triggered")
        message = _safe_text(
            str(data.get("message") or f"Documentation generation started for {project_id}")
        )
        return McpDocumentationGenerationResult(
            project_id=project_id,
            status=status,
            message=message,
            status_path=f"/mcp/generate-runs/{project_id}",
            docs_path=f"/projects/{project_id}/docs",
        )

    async def list_project_docs(
        self,
        *,
        project_id: str,
        access_token: str | None = None,
        source: str = "published",
    ) -> list[McpProjectDocInfo]:
        """List generated docs metadata from MCP without exposing file contents."""
        if not self._base_url:
            raise McpDocumentationProviderError(
                "Documentation provider is not configured."
            )
        project_id = _validated_project_id(project_id)
        if source not in {"published", "staging"}:
            raise McpDocumentationProviderError("Invalid documentation source.")

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                cookies={"access_token": access_token} if access_token else None,
            ) as client:
                response = await client.get(
                    f"/projects/{project_id}/docs",
                    params={"source": source},
                )
        except httpx.TimeoutException as exc:
            raise McpDocumentationProviderError(
                f"Documentation provider timed out after {self._timeout:g}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise McpDocumentationProviderError(
                _safe_error("Documentation provider could not be reached", str(exc))
            ) from exc

        if response.status_code >= 400:
            raise McpDocumentationProviderError(
                _safe_error(
                    f"Documentation provider returned HTTP {response.status_code}",
                    _response_error_text(response),
                )
            )

        data = _json_object(response)
        docs = data.get("docs")
        if not isinstance(docs, list):
            raise McpDocumentationProviderError(
                "Documentation provider returned an invalid response."
            )
        result: list[McpProjectDocInfo] = []
        for item in docs:
            if not isinstance(item, dict):
                continue
            result.append(
                McpProjectDocInfo(
                    name=str(item.get("name") or ""),
                    type=str(item.get("type") or ""),
                    size_bytes=int(item.get("size_bytes") or 0),
                    has_pdf=bool(item.get("has_pdf")),
                    modified_at=str(item.get("modified_at") or ""),
                )
            )
        return [doc for doc in result if doc.name]

    async def get_generation_status(
        self,
        *,
        project_id: str,
        access_token: str | None = None,
    ) -> McpDocumentationGenerationStatus:
        """Return the newest authoritative MCP generation-run receipt."""
        if not self._base_url:
            raise McpDocumentationProviderError(
                "Documentation provider is not configured."
            )
        project_id = _validated_project_id(project_id)
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                cookies={"access_token": access_token} if access_token else None,
            ) as client:
                response = await client.get(f"/generate-runs/{project_id}")
        except httpx.TimeoutException as exc:
            raise McpDocumentationProviderError(
                f"Documentation provider timed out after {self._timeout:g}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise McpDocumentationProviderError(
                _safe_error("Documentation provider could not be reached", str(exc))
            ) from exc

        if response.status_code >= 400:
            raise McpDocumentationProviderError(
                _safe_error(
                    f"Documentation provider returned HTTP {response.status_code}",
                    _response_error_text(response),
                )
            )
        data = _json_object(response)
        runs = data.get("runs")
        if not isinstance(runs, list):
            raise McpDocumentationProviderError(
                "Documentation provider returned an invalid response."
            )
        latest = next((item for item in runs if isinstance(item, dict)), None)
        status_path = f"/mcp/generate-runs/{project_id}"
        docs_path = f"/projects/{project_id}/docs"
        if latest is None:
            return McpDocumentationGenerationStatus(
                project_id=project_id,
                found=False,
                run_id=None,
                status="not_started",
                started_at=None,
                completed_at=None,
                doc_count=0,
                quality_score=None,
                run_verdict=None,
                client_ready=None,
                failed_gates=[],
                error_message=None,
                status_path=status_path,
                docs_path=docs_path,
            )

        gate_results = latest.get("gate_results")
        failed_gates = _failed_generation_gates(gate_results)
        client_ready = _client_ready(gate_results, latest.get("run_verdict"))
        return McpDocumentationGenerationStatus(
            project_id=str(latest.get("project_id") or project_id),
            found=True,
            run_id=_optional_int(latest.get("id")),
            status=str(latest.get("status") or "unknown").lower(),
            started_at=_optional_text(latest.get("started_at")),
            completed_at=_optional_text(latest.get("completed_at")),
            doc_count=int(latest.get("doc_count") or 0),
            quality_score=_optional_float(latest.get("quality_score")),
            run_verdict=_optional_text(latest.get("run_verdict")),
            client_ready=client_ready,
            failed_gates=failed_gates,
            error_message=(
                _safe_text(str(latest.get("error_message")))
                if latest.get("error_message")
                else None
            ),
            status_path=status_path,
            docs_path=docs_path,
        )

    async def list_projects(
        self,
        *,
        access_token: str | None = None,
    ) -> list[McpProjectInfo]:
        """List MCP projects visible to the current user."""
        if not self._base_url:
            raise McpDocumentationProviderError(
                "Documentation provider is not configured."
            )

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                cookies={"access_token": access_token} if access_token else None,
            ) as client:
                response = await client.get("/projects")
        except httpx.TimeoutException as exc:
            raise McpDocumentationProviderError(
                f"Documentation provider timed out after {self._timeout:g}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise McpDocumentationProviderError(
                _safe_error("Documentation provider could not be reached", str(exc))
            ) from exc

        if response.status_code >= 400:
            raise McpDocumentationProviderError(
                _safe_error(
                    f"Documentation provider returned HTTP {response.status_code}",
                    _response_error_text(response),
                )
            )

        data = _json_object(response)
        projects = data.get("projects")
        if not isinstance(projects, list):
            raise McpDocumentationProviderError(
                "Documentation provider returned an invalid response."
            )
        result: list[McpProjectInfo] = []
        for item in projects:
            if not isinstance(item, dict):
                continue
            project_id = str(item.get("id") or "").strip()
            if not project_id:
                continue
            result.append(
                McpProjectInfo(
                    id=project_id,
                    display_name=str(item.get("display_name") or project_id),
                    platform=str(item.get("platform") or ""),
                    source_type=str(item.get("source_type") or ""),
                    status=str(item.get("status") or ""),
                    doc_count=int(item.get("doc_count") or 0),
                    job_count=int(item.get("job_count") or 0),
                    plugin_pack_id=_optional_positive_int(item.get("plugin_pack_id")),
                )
            )
        return result

    async def list_documentation_packs(
        self, *, access_token: str | None = None
    ) -> list[McpDocumentationPack]:
        """List installed MCP plugin packs without exposing their filesystem paths."""
        if not self._base_url:
            raise McpDocumentationProviderError("Documentation provider is not configured.")
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                cookies={"access_token": access_token} if access_token else None,
            ) as client:
                response = await client.get("/plugins")
        except httpx.TimeoutException as exc:
            raise McpDocumentationProviderError(
                f"Documentation provider timed out after {self._timeout:g}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise McpDocumentationProviderError(
                _safe_error("Documentation provider could not be reached", str(exc))
            ) from exc
        if response.status_code >= 400:
            raise McpDocumentationProviderError(
                _safe_error(
                    f"Documentation provider returned HTTP {response.status_code}",
                    _response_error_text(response),
                )
            )
        data = _json_array(response)
        packs: list[McpDocumentationPack] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            pack_id = _optional_positive_int(item.get("id"))
            raw_name = item.get("manifest_name") or item.get("name")
            version = item.get("version")
            if pack_id is None or not isinstance(raw_name, str) or not raw_name.strip() or not isinstance(version, str) or not version.strip():
                continue
            description = item.get("description")
            packs.append(
                McpDocumentationPack(
                    id=pack_id,
                    name=_safe_text(raw_name),
                    version=_safe_text(version),
                    description=_safe_text(description) if isinstance(description, str) and description.strip() else None,
                )
            )
        return packs

    async def get_project_documentation_pack_id(
        self, *, project_id: str, access_token: str | None = None
    ) -> int | None:
        project_id = _validated_project_id(project_id)
        project = next(
            (item for item in await self.list_projects(access_token=access_token) if item.id == project_id),
            None,
        )
        if project is None:
            raise McpDocumentationProviderError("Documentation project was not found.")
        return project.plugin_pack_id

    async def create_documentation_pack(
        self,
        *,
        name: str,
        description: str | None,
        documents: list[McpDocumentationPackDocumentDraft],
        access_token: str | None = None,
    ) -> McpDocumentationPack:
        """Create a reusable local IntoDoc documentation Pack through MCP."""
        if not self._base_url:
            raise McpDocumentationProviderError("Documentation provider is not configured.")
        payload = {
            "name": name,
            "description": description,
            "documents": [
                {
                    "name": document.name,
                    "sections": document.sections,
                    "instructions": document.instructions,
                }
                for document in documents
            ],
        }
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                cookies={"access_token": access_token} if access_token else None,
            ) as client:
                response = await client.post(
                    "/documentation-packs",
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise McpDocumentationProviderError(
                f"Documentation provider timed out after {self._timeout:g}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise McpDocumentationProviderError(
                _safe_error("Documentation provider could not be reached", str(exc))
            ) from exc
        if response.status_code >= 400:
            raise McpDocumentationProviderError(
                _safe_error(
                    f"Documentation provider returned HTTP {response.status_code}",
                    _response_error_text(response),
                )
            )
        data = _json_object(response)
        pack_id = _optional_positive_int(data.get("id"))
        raw_name = data.get("manifest_name") or data.get("name")
        version = data.get("version")
        if (
            pack_id is None
            or not isinstance(raw_name, str)
            or not raw_name.strip()
            or not isinstance(version, str)
            or not version.strip()
        ):
            raise McpDocumentationProviderError(
                "Documentation provider returned an invalid response."
            )
        raw_description = data.get("description")
        return McpDocumentationPack(
            id=pack_id,
            name=_safe_text(raw_name),
            version=_safe_text(version),
            description=(
                _safe_text(raw_description)
                if isinstance(raw_description, str) and raw_description.strip()
                else None
            ),
        )

    async def set_project_documentation_pack(
        self,
        *,
        project_id: str,
        plugin_pack_id: int | None,
        access_token: str | None = None,
    ) -> int | None:
        """Persist the project's MCP plugin-pack default for future generation runs."""
        if not self._base_url:
            raise McpDocumentationProviderError("Documentation provider is not configured.")
        project_id = _validated_project_id(project_id)
        if plugin_pack_id is not None:
            plugin_pack_id = _validated_plugin_pack_id(plugin_pack_id)
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                cookies={"access_token": access_token} if access_token else None,
            ) as client:
                response = await client.patch(
                    f"/projects/{project_id}/plugin",
                    json={"plugin_pack_id": plugin_pack_id},
                )
        except httpx.TimeoutException as exc:
            raise McpDocumentationProviderError(
                f"Documentation provider timed out after {self._timeout:g}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise McpDocumentationProviderError(
                _safe_error("Documentation provider could not be reached", str(exc))
            ) from exc
        if response.status_code >= 400:
            raise McpDocumentationProviderError(
                _safe_error(
                    f"Documentation provider returned HTTP {response.status_code}",
                    _response_error_text(response),
                )
            )
        data = _json_object(response)
        if data.get("project_id") != project_id:
            raise McpDocumentationProviderError("Documentation provider returned an invalid response.")
        returned_pack_id = data.get("plugin_pack_id")
        if returned_pack_id is None:
            return None
        returned_pack_id = _optional_positive_int(returned_pack_id)
        if returned_pack_id is None:
            raise McpDocumentationProviderError("Documentation provider returned an invalid response.")
        return returned_pack_id

    async def update_doc_from_evidence(
        self,
        *,
        project_id: str,
        filename: str,
        evidence_text: str,
        evidence_refs: list[str] | None = None,
        workflow_run_id: str | None = None,
        section: str | None = None,
        expected_base_sha: str | None = None,
        access_token: str | None = None,
    ) -> McpEvidenceDocUpdateResult:
        """Ask MCP to update one generated markdown doc from explicit evidence."""
        if not self._base_url:
            raise McpDocumentationProviderError(
                "Documentation provider is not configured."
            )
        project_id = _validated_project_id(project_id)
        filename = _validated_markdown_filename(filename)
        payload = {
            "evidence_text": evidence_text,
            "evidence_refs": evidence_refs or [],
            "workflow_run_id": workflow_run_id,
        }
        _set_if_present(payload, "section", section)
        _set_if_present(payload, "expected_base_sha", expected_base_sha)

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                cookies={"access_token": access_token} if access_token else None,
            ) as client:
                response = await client.post(
                    f"/projects/{project_id}/docs/{filename}/evidence-update",
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise McpDocumentationProviderError(
                f"Documentation provider timed out after {self._timeout:g}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise McpDocumentationProviderError(
                _safe_error("Documentation provider could not be reached", str(exc))
            ) from exc

        if response.status_code >= 400:
            raise McpDocumentationProviderError(
                _safe_error(
                    f"Documentation provider returned HTTP {response.status_code}",
                    _response_error_text(response),
                )
            )

        data = _json_object(response)
        return McpEvidenceDocUpdateResult(
            project_id=str(data.get("project_id") or project_id),
            filename=str(data.get("filename") or filename),
            status=str(data.get("status") or "updated"),
            message=_safe_text(str(data.get("message") or "Documentation updated from provided evidence.")),
            workflow_run_id=(
                str(data["workflow_run_id"])
                if data.get("workflow_run_id") is not None
                else None
            ),
            evidence_refs=[
                str(item)
                for item in data.get("evidence_refs", [])
                if item is not None
            ],
            before_sha=str(data.get("before_sha") or ""),
            after_sha=str(data.get("after_sha") or ""),
            receipt_path=str(data.get("receipt_path") or ""),
            docs_path=str(data.get("docs_path") or f"/projects/{project_id}/docs/{filename}"),
            rag_result=data.get("rag_result") if isinstance(data.get("rag_result"), dict) else {},
            version_id=str(data.get("version_id") or data.get("after_sha") or ""),
            section=_optional_text(data.get("section")),
            revert_path=str(data.get("revert_path") or ""),
            reconciliation_status=str(data.get("reconciliation_status") or "indexed"),
        )

    async def get_document_version(
        self,
        *,
        project_id: str,
        filename: str,
        access_token: str | None = None,
    ) -> McpDocumentVersion:
        """Read one authorized document and derive the proposal base version."""
        if not self._base_url:
            raise McpDocumentationProviderError("Documentation provider is not configured.")
        project_id = _validated_project_id(project_id)
        filename = _validated_markdown_filename(filename)
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                cookies={"access_token": access_token} if access_token else None,
            ) as client:
                response = await client.get(f"/projects/{project_id}/docs/{filename}")
        except httpx.TimeoutException as exc:
            raise McpDocumentationProviderError(
                f"Documentation provider timed out after {self._timeout:g}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise McpDocumentationProviderError(
                _safe_error("Documentation provider could not be reached", str(exc))
            ) from exc
        if response.status_code >= 400:
            raise McpDocumentationProviderError(
                _safe_error(
                    f"Documentation provider returned HTTP {response.status_code}",
                    _response_error_text(response),
                )
            )
        content_type = response.headers.get("content-type", "").lower()
        if "application/json" in content_type:
            data = _json_object(response)
            content = data.get("content")
        else:
            content = response.text
        if not isinstance(content, str) or not content:
            raise McpDocumentationProviderError(
                "Documentation provider returned an invalid response."
            )
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return McpDocumentVersion(
            project_id=project_id,
            filename=filename,
            sha256=f"sha256:{digest}",
        )

    async def revert_doc_evidence_update(
        self,
        *,
        project_id: str,
        filename: str,
        receipt_path: str,
        expected_base_sha: str | None = None,
        workflow_run_id: str | None = None,
        access_token: str | None = None,
    ) -> McpEvidenceDocRevertResult:
        if not self._base_url:
            raise McpDocumentationProviderError("Documentation provider is not configured.")
        project_id = _validated_project_id(project_id)
        filename = _validated_markdown_filename(filename)
        if not receipt_path or ".." in receipt_path or receipt_path.startswith(("/", "http:" , "https:")):
            raise McpDocumentationProviderError("Invalid documentation update receipt.")
        payload = {
            "receipt_path": receipt_path,
            "expected_base_sha": expected_base_sha,
            "workflow_run_id": workflow_run_id,
        }
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                cookies={"access_token": access_token} if access_token else None,
            ) as client:
                response = await client.post(
                    f"/projects/{project_id}/docs/{filename}/evidence-revert",
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise McpDocumentationProviderError(
                f"Documentation provider timed out after {self._timeout:g}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise McpDocumentationProviderError(
                _safe_error("Documentation provider could not be reached", str(exc))
            ) from exc
        if response.status_code >= 400:
            raise McpDocumentationProviderError(
                _safe_error(
                    f"Documentation provider returned HTTP {response.status_code}",
                    _response_error_text(response),
                )
            )
        data = _json_object(response)
        return McpEvidenceDocRevertResult(
            project_id=str(data.get("project_id") or project_id),
            filename=str(data.get("filename") or filename),
            status=str(data.get("status") or "reverted"),
            message=_safe_text(str(data.get("message") or "Documentation update reverted.")),
            workflow_run_id=_optional_text(data.get("workflow_run_id")),
            before_sha=str(data.get("before_sha") or ""),
            after_sha=str(data.get("after_sha") or ""),
            version_id=str(data.get("version_id") or data.get("after_sha") or ""),
            reverted_receipt_path=str(data.get("reverted_receipt_path") or receipt_path),
            receipt_path=str(data.get("receipt_path") or ""),
            docs_path=str(data.get("docs_path") or f"/projects/{project_id}/docs/{filename}"),
            rag_result=data.get("rag_result") if isinstance(data.get("rag_result"), dict) else {},
            reconciliation_status=str(data.get("reconciliation_status") or "indexed"),
        )

    async def _reconcile_generation_after_timeout(
        self,
        *,
        project_id: str,
        access_token: str | None,
    ) -> McpDocumentationGenerationResult | None:
        """Resolve an ambiguous POST timeout against MCP's durable run ledger."""
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=min(self._timeout, 5.0),
                transport=self._transport,
                cookies={"access_token": access_token} if access_token else None,
            ) as client:
                response = await client.get(f"/generate-runs/{project_id}")
        except httpx.HTTPError:
            return None
        if response.status_code >= 400:
            return None
        try:
            data = response.json()
        except ValueError:
            return None
        runs = data.get("runs") if isinstance(data, dict) else None
        if not isinstance(runs, list):
            return None
        active = next(
            (
                item
                for item in runs
                if isinstance(item, dict)
                and str(item.get("status") or "").lower()
                in {"queued", "pending", "running", "completed", "ready"}
            ),
            None,
        )
        if active is None:
            return None
        return McpDocumentationGenerationResult(
            project_id=project_id,
            status="generation_reconciled",
            message="Documentation generation was accepted; Nexus reconciled its status after a delayed response.",
            status_path=f"/mcp/generate-runs/{project_id}",
            docs_path=f"/projects/{project_id}/docs",
        )


def _set_if_present(payload: dict[str, Any], key: str, value: Any | None) -> None:
    if value is not None:
        payload[key] = value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    parsed = _optional_int(value)
    return parsed if parsed is not None and parsed > 0 else None


def _validated_plugin_pack_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise McpDocumentationProviderError("Invalid documentation pack selection.")
    return value


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _failed_generation_gates(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    failed: list[str] = []
    candidates = value.get("gates") if isinstance(value.get("gates"), dict) else value
    for name, result in candidates.items():
        if name in {"client_ready", "ready", "summary"}:
            continue
        passed: bool | None = None
        if isinstance(result, bool):
            passed = result
        elif isinstance(result, dict):
            raw = result.get("passed", result.get("ok"))
            if isinstance(raw, bool):
                passed = raw
            elif str(result.get("status") or "").lower() in {"failed", "error", "blocked"}:
                passed = False
        if passed is False:
            failed.append(str(name))
    return sorted(failed)


def _client_ready(gate_results: Any, run_verdict: Any) -> bool | None:
    if isinstance(gate_results, dict):
        for key in ("client_ready", "ready"):
            if isinstance(gate_results.get(key), bool):
                return gate_results[key]
    verdict = str(run_verdict or "").lower()
    if verdict in {"passed", "pass", "ready", "approved"}:
        return True
    if verdict in {"failed", "fail", "blocked", "rejected"}:
        return False
    return None


def _validated_project_id(value: str) -> str:
    project_id = value.strip()
    if not _PROJECT_ID_PATTERN.fullmatch(project_id):
        raise McpDocumentationProviderError(
            "Invalid project scope. Use letters, numbers, hyphens, or underscores."
        )
    return project_id


def _validated_markdown_filename(value: str) -> str:
    filename = value.strip()
    if (
        not _MARKDOWN_FILENAME_PATTERN.fullmatch(filename)
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
    ):
        raise McpDocumentationProviderError(
            "Invalid documentation filename. Choose one markdown file by name."
        )
    return filename


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise McpDocumentationProviderError(
            "Documentation generation provider returned an invalid response."
        ) from exc
    if not isinstance(data, dict):
        raise McpDocumentationProviderError(
            "Documentation generation provider returned an invalid response."
        )
    return data


def _json_array(response: httpx.Response) -> list[Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise McpDocumentationProviderError(
            "Documentation provider returned an invalid response."
        ) from exc
    if not isinstance(data, list):
        raise McpDocumentationProviderError(
            "Documentation provider returned an invalid response."
        )
    return data


def _response_error_text(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, str):
            return detail
        if detail is not None:
            return str(detail)
    return response.text


def _safe_error(prefix: str, detail: str) -> str:
    safe_detail = _safe_text(detail)
    if not safe_detail:
        return f"{prefix}."
    return f"{prefix}: {safe_detail}"


def _safe_text(value: str) -> str:
    sanitized = value.replace("\n", " ").replace("\r", " ")
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub(_redact_match, sanitized)
    sanitized = re.sub(r"https?://[^\s]+", "[URL_REDACTED]", sanitized)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if len(sanitized) > _MAX_SAFE_ERROR_CHARS:
        sanitized = sanitized[: _MAX_SAFE_ERROR_CHARS - 1].rstrip() + "..."
    return sanitized


def _redact_match(match: re.Match[str]) -> str:
    if match.lastindex and match.lastindex >= 1:
        return f"{match.group(1)}=[REDACTED]"
    return "[REDACTED]"
