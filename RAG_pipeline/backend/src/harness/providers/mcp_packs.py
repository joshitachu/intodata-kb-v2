"""Thin, safety-bounded adapter for MCP plugin-pack authoring.

There is exactly one pack format in Nexus -- ``plugin.yaml``, owned by the MCP
service (``mcp_server/services/plugin_schema.py``). This adapter is how the RAG
side reaches it, so chat-driven pack authoring and the Pack Workbench end up in
the same lane instead of each growing their own format.

The MCP service stays the only authority: it validates the manifest through the
real pack loader, owns the personal/shared lanes, and keys personal packs on the
caller's user id (taken from the forwarded access token's ``sub``). Nothing here
parses or rewrites a manifest -- an invalid draft is the MCP author's error to
report, not ours to paper over.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.config import settings

_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
_PACK_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{2,79}$")
_SAFE_PLATFORM_RE = re.compile(r"^[a-z][a-z0-9_-]{0,49}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
# Mirrors the MCP file-editor allowlist: a pack is a manifest plus markdown.
_PACK_FILE_RE = re.compile(r"^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._/-]{0,199}\.(md|yaml|yml)$")
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bAuthorization\s*[:=]\s*(?:Bearer|Basic)\s+\S+"),
    re.compile(r"(?i)\b(access_token|refresh_token|api[_-]?key|token|secret)=([^&\s]+)"),
    re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._+/=\-]{10,}"),
)
_MAX_SAFE_TEXT_CHARS = 240
_MAX_MANIFEST_CHARS = 200_000
_MAX_CURRENT_MANIFEST_CHARS = _MAX_MANIFEST_CHARS
_MAX_PACK_FILE_CHARS = 200_000


@dataclass(frozen=True)
class McpPackDraft:
    """An authored but unsaved ``plugin.yaml`` plus its companion files."""

    manifest_yaml: str
    name: str
    description: str | None = None
    #: User-facing summary returned by the Pack author for the completed turn.
    notes: str | None = None
    #: Relative path -> content. Stands alone; creation must not inherit the starter.
    files: dict[str, str] = field(default_factory=dict)
    complete: bool = True


@dataclass(frozen=True)
class McpPack:
    """A registered pack, personal or shared."""

    id: int
    name: str
    version: str
    description: str | None
    origin: str
    personal: bool
    usable: bool
    certification_status: str
    repo_url: str | None = None
    repo_branch: str | None = None


@dataclass(frozen=True)
class McpPackPublication:
    """The result of pushing a personal pack toward the shared repository."""

    branch: str
    repo_url: str
    commit_sha: str
    pull_request_url: str | None = None
    note: str | None = None


class McpPackProviderError(Exception):
    """Provider failure whose message is safe for workflow and UI surfaces."""

    def __init__(self, safe_message: str) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


class McpPackProvider:
    """Call MCP's authoritative pack registry without duplicating it."""

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

    async def draft_pack(
        self,
        *,
        intent: str,
        platform: str | None = None,
        current_manifest: str | None = None,
        current_files: dict[str, str] | None = None,
        access_token: str | None = None,
    ) -> McpPackDraft:
        """Author a complete ``plugin.yaml`` from a plain-language brief.

        Nothing is persisted. The caller reviews the draft and then calls
        :meth:`create_personal_pack`, which is the only write.

        ``current_manifest`` makes this a refinement of the manifest the author
        already has, so a multi-turn chat conversation does not restart the pack
        on every instruction.
        """
        create_payload: dict[str, Any] = {}
        if platform is not None:
            create_payload["platform"] = _validate_platform(platform)
        if current_manifest is not None and current_manifest.strip():
            create_payload["manifest_yaml"] = _validate_current_manifest(current_manifest)
        if current_files:
            create_payload["files"] = _validate_pack_files(current_files)
        session = await self._request(
            "POST",
            "/plugins/architect/sessions",
            access_token=access_token,
            payload=create_payload,
        )
        session_id = session.get("id")
        revision = session.get("revision")
        if not isinstance(session_id, str) or not isinstance(revision, int):
            raise McpPackProviderError("MCP Pack Architect returned an invalid session.")
        data = await self._request(
            "POST",
            f"/plugins/architect/sessions/{session_id}/messages",
            access_token=access_token,
            payload={"message": _validate_intent(intent), "expected_revision": revision},
        )
        deadline = time.monotonic() + self._timeout
        while data.get("turn_status") == "running":
            if time.monotonic() >= deadline:
                raise McpPackProviderError(
                    f"MCP Pack Architect turn did not finish within {self._timeout:g}s."
                )
            await asyncio.sleep(0.25)
            data = await self._request(
                "GET",
                f"/plugins/architect/sessions/{session_id}",
                access_token=access_token,
            )
        if data.get("turn_status") == "failed":
            detail = _optional_safe_text(data.get("turn_error"))
            raise McpPackProviderError(detail or "MCP Pack Architect could not complete the turn.")
        manifest_yaml = data.get("manifest_yaml")
        name = data.get("name")
        if not isinstance(manifest_yaml, str) or not manifest_yaml.strip():
            raise McpPackProviderError("MCP pack provider returned an empty manifest.")
        if not isinstance(name, str) or not name.strip():
            raise McpPackProviderError("MCP pack provider returned an unnamed pack.")
        return McpPackDraft(
            manifest_yaml=manifest_yaml,
            name=name.strip(),
            description=_optional_safe_text(data.get("description")),
            notes=_optional_safe_text(
                next(
                    (
                        item.get("content")
                        for item in reversed(data.get("messages", []))
                        if isinstance(item, dict) and item.get("role") == "assistant"
                    ),
                    None,
                )
            ),
            files=_validate_pack_files(data.get("files")),
            complete=data.get("complete") is True,
        )

    async def create_personal_pack(
        self,
        *,
        name: str,
        manifest_yaml: str | None = None,
        description: str | None = None,
        files: dict[str, str] | None = None,
        inherit_starter: bool = True,
        platform: str | None = None,
        access_token: str | None = None,
    ) -> McpPack:
        """Persist a pack against the caller's own account.

        ``inherit_starter`` must be False for an AI-authored draft: the author
        emits a complete file set, and inheriting the starter on top of it would
        leak an unrelated prompt or example into the new pack.
        """
        payload: dict[str, Any] = {
            "name": _validate_pack_name(name),
            "inherit_starter": bool(inherit_starter),
        }
        if manifest_yaml is not None:
            payload["manifest_yaml"] = _validate_manifest_yaml(manifest_yaml)
        if description is not None:
            payload["description"] = _validate_description(description)
        if files:
            payload["files"] = _validate_pack_files(files)
        if platform is not None:
            payload["platform"] = _validate_platform(platform)
        data = await self._request(
            "POST", "/plugins/personal", access_token=access_token, payload=payload
        )
        return _pack_from_payload(data)

    async def list_packs(self, *, access_token: str | None = None) -> list[McpPack]:
        """Every pack visible to the caller: shared, plus their own."""
        data = await self._request(
            "GET", "/plugins", access_token=access_token, expect="list"
        )
        return [_pack_from_payload(item) for item in data if isinstance(item, dict)]

    async def select_project_pack(
        self,
        *,
        project_id: str,
        pack_id: int | None,
        access_token: str | None = None,
    ) -> None:
        """Bind a project to the pack it generates with. ``None`` clears it.

        This endpoint sits under ``/projects/{id}/plugin`` rather than
        ``/plugins/...`` -- MCP owns project state, not the pack registry.
        """
        await self._request(
            "PATCH",
            f"/projects/{_validate_project_id(project_id)}/plugin",
            access_token=access_token,
            payload={"plugin_pack_id": _validate_pack_id(pack_id)},
        )

    async def publish_pack(
        self,
        *,
        pack_id: int,
        branch: str | None = None,
        title: str | None = None,
        body: str | None = None,
        access_token: str | None = None,
    ) -> McpPackPublication:
        """Push a personal pack as a branch and open a pull request.

        This ASKS for shared status; a human merge is what grants it.
        """
        payload: dict[str, Any] = {}
        if branch is not None:
            payload["branch"] = _validate_branch(branch)
        if title is not None:
            payload["title"] = _validate_description(title, limit=200)
        if body is not None:
            payload["body"] = _validate_description(body, limit=10_000)
        data = await self._request(
            "POST",
            f"/plugins/{_require_pack_id(pack_id)}/publish",
            access_token=access_token,
            payload=payload,
        )
        branch_name = data.get("branch")
        repo_url = data.get("repo_url")
        commit_sha = data.get("commit_sha")
        if not all(isinstance(value, str) and value for value in (branch_name, repo_url, commit_sha)):
            raise McpPackProviderError("MCP pack provider returned an invalid publication.")
        pull_request_url = data.get("pull_request_url")
        return McpPackPublication(
            branch=branch_name,
            repo_url=repo_url,
            commit_sha=commit_sha,
            pull_request_url=(
                pull_request_url if isinstance(pull_request_url, str) and pull_request_url else None
            ),
            note=_optional_safe_text(data.get("note")),
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        access_token: str | None,
        payload: dict[str, Any] | None = None,
        expect: str = "dict",
    ) -> Any:
        if not self._base_url:
            raise McpPackProviderError("MCP pack provider is not configured.")
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                cookies={"access_token": access_token} if access_token else None,
            ) as client:
                response = await client.request(method, path, json=payload)
        except httpx.TimeoutException as exc:
            raise McpPackProviderError(
                f"MCP pack provider timed out after {self._timeout:g}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise McpPackProviderError("MCP pack provider could not be reached.") from exc

        if response.status_code >= 400:
            detail = _response_error_text(response)
            suffix = f": {detail}" if detail else "."
            raise McpPackProviderError(
                f"MCP pack provider returned HTTP {response.status_code}{suffix}"
            )
        if response.status_code == 204 or not response.content:
            return {} if expect == "dict" else []
        try:
            data = response.json()
        except ValueError as exc:
            raise McpPackProviderError("MCP pack provider returned an invalid response.") from exc
        if expect == "list":
            if not isinstance(data, list):
                raise McpPackProviderError("MCP pack provider returned an invalid response.")
            return data
        if not isinstance(data, dict):
            raise McpPackProviderError("MCP pack provider returned an invalid response.")
        return data


def _pack_from_payload(data: dict[str, Any]) -> McpPack:
    pack_id = data.get("id")
    name = data.get("name")
    version = data.get("version")
    if (
        isinstance(pack_id, bool)
        or not isinstance(pack_id, int)
        or pack_id <= 0
        or not isinstance(name, str)
        or not name
        or not isinstance(version, str)
        or not version
    ):
        raise McpPackProviderError("MCP pack provider returned an invalid pack.")
    return McpPack(
        id=pack_id,
        name=_safe_text(name),
        version=_safe_text(version),
        description=_optional_safe_text(data.get("description")),
        origin=_safe_field(data.get("origin"), default="unknown"),
        personal=data.get("personal") is True,
        usable=data.get("usable") is not False,
        certification_status=_safe_field(
            data.get("certification_status"), default="untrusted"
        ),
        repo_url=data.get("repo_url") if isinstance(data.get("repo_url"), str) else None,
        repo_branch=(
            data.get("repo_branch") if isinstance(data.get("repo_branch"), str) else None
        ),
    )


def _validate_intent(value: str) -> str:
    if not isinstance(value, str):
        raise McpPackProviderError("Invalid pack brief.")
    cleaned = value.strip()
    if len(cleaned) < 3:
        raise McpPackProviderError("Invalid pack brief.")
    return cleaned


def _validate_pack_name(value: str) -> str:
    if not isinstance(value, str) or not _PACK_NAME_RE.fullmatch(value.strip()):
        raise McpPackProviderError("Invalid pack name.")
    return value.strip()


def _validate_current_manifest(value: str) -> str:
    if not isinstance(value, str) or len(value) > _MAX_CURRENT_MANIFEST_CHARS:
        raise McpPackProviderError("Invalid current pack manifest.")
    return value


def _validate_manifest_yaml(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_MANIFEST_CHARS:
        raise McpPackProviderError("Invalid pack manifest.")
    return value


def _validate_description(value: str, *, limit: int = 1_000) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise McpPackProviderError("Invalid pack description.")
    return value


def _validate_platform(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_PLATFORM_RE.fullmatch(value):
        raise McpPackProviderError("Invalid project platform.")
    return value


def _validate_branch(value: str) -> str:
    if not isinstance(value, str) or not _BRANCH_RE.fullmatch(value):
        raise McpPackProviderError("Invalid publication branch.")
    return value


def _validate_project_id(value: str) -> str:
    if not isinstance(value, str) or not _PROJECT_ID_RE.fullmatch(value):
        raise McpPackProviderError("Invalid project ID.")
    return value


def _validate_pack_id(value: int | None) -> int | None:
    if value is None:
        return None
    return _require_pack_id(value)


def _require_pack_id(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**63 - 1:
        raise McpPackProviderError("Invalid pack ID.")
    return value


def _validate_pack_files(value: Any) -> dict[str, str]:
    """Bound the file set before it crosses the service boundary.

    Path traversal and unexpected extensions are refused here as well as in MCP:
    a draft is model output, and the caller should not be able to turn a pack
    into an arbitrary file write by round-tripping it through this adapter.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise McpPackProviderError("Invalid pack file set.")
    files: dict[str, str] = {}
    for path, content in value.items():
        if (
            not isinstance(path, str)
            or not _PACK_FILE_RE.fullmatch(path)
            or path.startswith("/")
            or not isinstance(content, str)
            or len(content) > _MAX_PACK_FILE_CHARS
        ):
            raise McpPackProviderError("Invalid pack file set.")
        files[path] = content
    return files


def _response_error_text(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return _safe_text(response.text)
    if not isinstance(data, dict) or not isinstance(data.get("detail"), str):
        return ""
    return _safe_text(data["detail"])


def _safe_field(value: Any, *, default: str = "") -> str:
    return _safe_text(value) if isinstance(value, str) and value else default


def _optional_safe_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return _safe_text(value)


def _safe_text(value: str) -> str:
    sanitized = value.replace("\n", " ").replace("\r", " ")
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    if len(sanitized) > _MAX_SAFE_TEXT_CHARS:
        return sanitized[: _MAX_SAFE_TEXT_CHARS - 1].rstrip() + "..."
    return sanitized
