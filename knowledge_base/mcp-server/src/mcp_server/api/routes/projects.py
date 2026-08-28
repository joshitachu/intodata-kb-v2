"""Project management REST endpoints."""

from __future__ import annotations

import datetime
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# Per-process set of project IDs currently running generation.
# Prevents concurrent generate calls for the same project in the same process.
# Multi-worker deployments rely on the DB status check as the cross-process guard.
_currently_generating: set[str] = set()
_generation_tasks: dict[str, asyncio.Task] = {}

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from sqlalchemy import func, select, text

from contextlib import nullcontext

from mcp_server.api.permissions import require_project_access, require_project_management
from mcp_server.config import config
from mcp_server.repositories.doc_rag_hash_repository import DocRagHashRepository
from mcp_server.db.session import async_session_factory
from mcp_server.db.models import GenerateRun, PluginPack, Project
from mcp_server.schemas import (
    AddRepoRequest,
    AddSourceResponse,
    AssessmentGenerateResponse,
    ComponentGraphResponse,
    DeepDiveProject,
    DeepDiveProjectsResponse,
    DeepDiveRequest,
    DeleteProjectResponse,
    DocFileInfo,
    DocListResponse,
    EvidenceDocRevertRequest,
    EvidenceDocRevertResponse,
    EvidenceDocUpdateRequest,
    EvidenceDocUpdateResponse,
    ChatDocPatchPreviewRequest,
    ChatDocPatchPreviewResponse,
    ChatDocPatchActivateRequest,
    ChatDocPatchActivateResponse,
    DocReview,
    FullRebuildRequest,
    GenerateRequest,
    GithubAppConnectResponse,
    GithubAppInstallationItem,
    GithubAppInstallationsResponse,
    GithubAppRepoItem,
    GithubAppReposResponse,
    GenerateRunListResponse,
    GenerateRunResponse,
    GenerateSummariesResponse,
    GenerationPlanGlobalInvalidation,
    GenerationPlanPartition,
    GenerationPlanResponse,
    GraphConnectionInfo,
    JobDetailResponse,
    JobDocSection,
    JobGraphResponse,
    JobScanMetadata,
    LineageErrorResponse,
    LineageFieldResponse,
    LineageJobResponse,
    LogFileListResponse,
    LogFileResponse,
    NexusPackSnapshot,
    NexusPresentation,
    PdfGenerateResponse,
    ProjectGraphResponse,
    ProjectLandscapeResponse,
    ProjectListResponse,
    ProjectResponse,
    VerifyZipUploadRequest,
    ZipUploadReceipt,
    ZipReplacementResponse,
    ZipUploadResponse,
    ProjectUsageResponse,
    PushToRagRequest,
    ReingestionReadinessResponse,
    ReingestionStatusResponse,
    ResetStatusRequest,
    ReviewAggregateResponse,
    ScanRequest,
    ScanStatusResponse,
    ScanRunListResponse,
    ScanRunResponse,
    StatusResponse,
    UpdateGithubTokenRequest,
    UpdateProjectRequest,
)
from mcp_server.services.atomic_swap import swap_docs
from mcp_server.services.doc_version_history import DocVersionHistoryRepository
from mcp_server.services.drift_summary import DriftSummary, compute_drift_summary
from mcp_server.services.evidence_doc_update import EvidenceDocUpdateError, EvidenceDocUpdateService
from mcp_server.services.evidence_doc_revert import EvidenceDocRevertService
from mcp_server.services.reingestion_gate import GateOutcome
from mcp_server.services.claude_service import DocGenerationCoordinator
from mcp_server.services.pack_governance import PackGovernanceDenied, pack_governance
from mcp_server.services.deep_dive_service import DeepDiveService
from mcp_server.services.auto_doc_update import AutoDocUpdateService
from mcp_server.services.git_credentials import NullCredentialProvider, provider_for_project
from mcp_server.utils.azure_devops_api import (
    AzureDevOpsUrlError,
    build_repo_clone_url,
    is_azure_devops_url,
    parse_repo_url,
)
from mcp_server.services.git_remote_service import (
    ReingestionReadinessReport,
    authenticated_url_plaintext,
    git_subprocess_env,
    inspect_reingestion_readiness,
    pull_remote_ff_only,
    repair_remote_hygiene,
    run_git,
    strip_url_credentials,
)
from mcp_server.services.component_graph_service import (
    build_component_graph,
    component_exists,
    is_tmap_component,
)
from mcp_server.services.git_reingestion import run_git_reingestion
from mcp_server.services.component_description_service import (
    parse_component_descriptions,
)
from mcp_server.services.job_graph_service import build_job_graph
from mcp_server.services.lineage_service import (
    MAX_JOBS_PER_REQUEST,
    LineageDataUnavailableError,
    LineageQueryError,
    build_field_lineage,
    build_job_lineage,
)
from mcp_server.services.static_topology_publisher import StaticTopologyPublisher
from mcp_server.services.pack_loader import PackLoader
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.project_paths import repair_project_repo_path
from mcp_server.services.rag_bridge import RagBridge
from mcp_server.services.rag_support_package import replace_analysis_cache_from_rag_support
from mcp_server.services.repo_sources import (
    RepoSourceRepository,
    allocate_subproject_name,
    is_flat_source_tree,
    project_repos_root,
    source_names_on_disk,
)
from mcp_server.utils.source_origin import REPO_MARKER, marker_dir_name
from mcp_server.services.living_docs.zip_revisions import ZipRevisionService
from mcp_server.services.living_docs.zip_patch_lifecycle import LiveZipPatchCoordinator
from mcp_server.services.living_docs.chat_patching import ChatPatchService
from mcp_server.utils.error_sanitizer import sanitize_git_error
from mcp_server.services.scanner_service import ScannerService
from mcp_server.services.scan_output_lock import ScanOutputLock, acquire_scan_output_lock
from mcp_server.services.summary_service import (
    SummaryService,
    build_component_summaries_async,
)
from mcp_server.services import validation_service

router = APIRouter(tags=["projects"])


def _project_webhook_status(p: Project) -> str | None:
    if p.source_type == "git" and p.repo_url:
        if p.github_webhook_id:
            return "active"
        return "not_registered"
    return None


def _project_artifact_counts(project_id: str, legacy_total: int) -> dict[str, int]:
    """Read additive Talend taxonomy counts without a database migration."""
    defaults = {"total_artifacts": legacy_total, "job_count": legacy_total, "route_count": 0}
    try:
        meta_path = config.intodoc_output_dir / project_id / "scan" / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        stats = meta.get("stats") if isinstance(meta, dict) else {}
        if not isinstance(stats, dict):
            return defaults
        return {
            "total_artifacts": int(stats.get("total_artifacts", legacy_total)),
            "job_count": int(stats.get("job_count", legacy_total)),
            "route_count": int(stats.get("route_count", 0)),
            "joblet_count": int(stats.get("joblet_count", 0)),
            "routelet_count": int(stats.get("routelet_count", 0)),
            "empty_or_unparsed_count": int(stats.get("empty_or_unparsed_count", 0)),
        }
    except (OSError, ValueError, json.JSONDecodeError):
        return defaults


def _project_to_response(p) -> ProjectResponse:
    webhook_status = _project_webhook_status(p)
    return ProjectResponse(
        id=p.id,
        display_name=p.display_name,
        repo_url=p.repo_url,
        local_path=p.local_path,
        platform=p.platform,
        doc_generation_provider=p.doc_generation_provider,
        source_type=p.source_type,
        status=p.status,
        last_scan_at=p.last_scan_at,
        last_generate_at=p.last_generate_at,
        last_rag_push_at=p.last_rag_push_at,
        job_count=p.job_count,
        artifact_counts=_project_artifact_counts(p.id, p.job_count),
        doc_count=p.doc_count,
        error_message=sanitize_git_error(p.error_message) if p.error_message else p.error_message,
        last_ingested_commit_sha=p.last_ingested_commit_sha,
        auto_reingest_enabled=p.auto_reingest_enabled,
        auto_reingest_last_checked_at=p.auto_reingest_last_checked_at,
        auto_reingest_error=sanitize_git_error(p.auto_reingest_error) if p.auto_reingest_error else p.auto_reingest_error,
        git_update_summary=p.git_update_summary,
        # "a credential is resolvable" — App and Azure DevOps connections keep the
        # credential off the project row, so the raw PAT column is not the answer.
        has_github_token=not isinstance(provider_for_project(p), NullCredentialProvider),
        connection_kind=getattr(p, "connection_kind", "pat") or "pat",
        github_account_login=getattr(p, "github_account_login", None),
        azure_devops_connection_id=getattr(p, "azure_devops_connection_id", None),
        webhook_status=webhook_status,
        instruction_pack_dir=getattr(p, "instruction_pack_dir", None),
        plugin_pack_id=getattr(p, "plugin_pack_id", None),
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _sanitize_status_value(value: object) -> object:
    if isinstance(value, str):
        return sanitize_git_error(value)
    if isinstance(value, list):
        return [_sanitize_status_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_status_value(item) for key, item in value.items()}
    return value


def _summary_string(summary: dict[str, object], key: str) -> str | None:
    value = summary.get(key)
    if value is None:
        return None
    return sanitize_git_error(str(value))


def _summary_string_list(summary: dict[str, object], key: str) -> list[str]:
    value = summary.get(key)
    if not isinstance(value, list):
        return []
    return [sanitize_git_error(str(item)) for item in value]


def _build_drift_status(project_id: str) -> dict[str, object]:
    """Slice 5 (US-7/US-8): read-only drift counters for the status surface.

    Folds the Slice 4 append-only change history (a local JSONL file read --
    no scan/generation/patch/RAG-sync work) via the pure
    ``drift_summary.compute_drift_summary``. Best-effort: any read failure
    (e.g. project has no history yet) yields the zeroed/unreconciled default,
    never raises -- this route must stay side-effect-free and available even
    for brand-new projects.
    """
    try:
        entries = DocVersionHistoryRepository(project_id).read_all()
        return compute_drift_summary(entries).to_dict()
    except OSError as exc:
        logger.warning("Slice 5: failed reading change history for %s: %s", project_id, exc)
        return DriftSummary().to_dict()


def _build_reingestion_status_response(
    project: Project,
    readiness: ReingestionReadinessReport,
) -> ReingestionStatusResponse:
    summary: dict[str, object] = (
        project.git_update_summary if isinstance(project.git_update_summary, dict) else {}
    )
    webhook = summary.get("webhook") if isinstance(summary.get("webhook"), dict) else None
    webhook_registered = bool(project.github_webhook_id) or bool(
        isinstance(webhook, dict) and webhook.get("registered") is True
    )
    webhook_status = _project_webhook_status(project)
    if webhook_registered and webhook_status == "not_registered":
        webhook_status = "observed"
    error = _summary_string(summary, "error")
    if error is None and project.auto_reingest_error:
        error = sanitize_git_error(project.auto_reingest_error)

    return ReingestionStatusResponse(
        project_id=project.id,
        project_status=project.status,
        source_type=project.source_type,
        auto_reingest_enabled=project.auto_reingest_enabled,
        auto_reingest_last_checked_at=project.auto_reingest_last_checked_at,
        last_ingested_commit_sha=project.last_ingested_commit_sha,
        last_rag_push_at=project.last_rag_push_at,
        webhook={
            "registered": webhook_registered,
            "status": webhook_status,
            "webhook_id": project.github_webhook_id,
            "last_observation": _sanitize_status_value(webhook) if webhook else None,
        },
        last_update={
            "action": _summary_string(summary, "action"),
            "outcome": _summary_string(summary, "outcome"),
            "reason_code": _summary_string(summary, "reason_code"),
            "reason": _summary_string(summary, "reason"),
            "manual_review_reason": _summary_string(summary, "manual_review_reason"),
            "error": error,
            "before_sha": _summary_string(summary, "before"),
            "after_sha": _summary_string(summary, "after"),
            "decision_strategy": _summary_string(summary, "decision_strategy"),
            "fallback_used": summary.get("fallback_used")
            if isinstance(summary.get("fallback_used"), bool)
            else None,
            "targeted_regen_ready": summary.get("targeted_regen_ready")
            if isinstance(summary.get("targeted_regen_ready"), bool)
            else None,
            "requires_full_rebaseline": summary.get("requires_full_rebaseline")
            if isinstance(summary.get("requires_full_rebaseline"), bool)
            else None,
            "held": summary.get("held")
            if isinstance(summary.get("held"), bool)
            else None,
            "touched_docs": _summary_string_list(summary, "touched_docs"),
            "patched_docs": _summary_string_list(summary, "patched_docs"),
            "matched_docs": _summary_string_list(summary, "matched_docs"),
            "rag_result": _sanitize_status_value(summary.get("rag_result"))
            if isinstance(summary.get("rag_result"), dict)
            else None,
            "regen_result": _sanitize_status_value(summary.get("regen_result"))
            if isinstance(summary.get("regen_result"), dict)
            else None,
            "gate": _sanitize_status_value(summary.get("gate"))
            if isinstance(summary.get("gate"), dict)
            else None,
            "affected_docs_preview": _summary_string_list(summary, "affected_docs_preview")
            if isinstance(summary.get("affected_docs_preview"), list)
            else None,
        },
        remote_hygiene={
            "status": readiness.status,
            "ready": readiness.ready,
            "repairable": readiness.repairable,
            "issues": list(readiness.issues),
            "repo_url": readiness.repo_url,
            "origin_url": readiness.origin_url,
            "repo_url_had_credentials": readiness.repo_url_had_credentials,
            "origin_had_credentials": readiness.origin_had_credentials,
            "origin_matches_repo_url": readiness.origin_matches_repo_url,
            "has_github_token": readiness.has_github_token,
            "auth_failed": readiness.auth_failed,
            "error": sanitize_git_error(readiness.error) if readiness.error else None,
        },
        cache={
            "has_last_ingested_commit": readiness.has_last_ingested_commit,
            "has_docs": readiness.has_docs,
            "doc_count": readiness.doc_count,
            "has_rag_support": readiness.has_rag_support,
            "rag_support_analysis_count": readiness.rag_support_analysis_count,
            "has_analysis_cache": readiness.has_analysis_cache,
            "analysis_cache_count": readiness.analysis_cache_count,
            "cache_support_match": readiness.cache_support_match,
            "targeted_regen_ready": readiness.targeted_regen_ready,
            "requires_full_rebaseline": readiness.requires_full_rebaseline,
            "reason_codes": list(readiness.reason_codes),
            "can_incremental_regen": readiness.can_incremental_regen,
        },
        drift=_build_drift_status(project.id),
    )


@router.post("/add-repo", response_model=ProjectResponse)
async def add_repo(req: AddRepoRequest, request: Request):
    """Register a project (URL, local path, or 'zip')."""
    # Multi-tenant guard: a caller may only use a connection they own. Both the
    # GitHub App installation id and the Azure DevOps connection id are supplied by
    # the client, so neither may be trusted without an ownership check.
    _auth_payload = getattr(request.state, "auth_payload", None)
    _uid = int(_auth_payload.get("sub", 0)) if _auth_payload else 0
    if req.installation_id:
        from mcp_server.services.github_app_ownership import authorize_installation
        async with async_session_factory() as _authdb:
            if not await authorize_installation(_authdb, int(req.installation_id), _uid):
                raise HTTPException(
                    status_code=403,
                    detail="You do not have access to this GitHub App installation",
                )
    if req.azure_devops_connection_id:
        from mcp_server.services.azure_devops_connections import authorize_connection
        async with async_session_factory() as _authdb:
            if not await authorize_connection(_authdb, req.azure_devops_connection_id, _uid):
                raise HTTPException(
                    status_code=403,
                    detail="You do not have access to this Azure DevOps connection",
                )

    url = req.url
    path = Path(url)

    if path.exists():
        resolved = path.resolve()
        _allowed_roots = [
            Path(r.strip()).resolve()
            for r in config.allowed_local_roots.split(",")
            if r.strip()
        ]
        if _allowed_roots and not any(resolved.is_relative_to(r) for r in _allowed_roots):
            raise HTTPException(
                status_code=400,
                detail="Local path is not within an allowed root. Set MCP_ALLOWED_LOCAL_ROOTS to permit it.",
            )
        source_type = "local"
        local_path = str(resolved)
        repo_url = None
    elif url.startswith(("http://", "https://", "git@")):
        # Reduce a pasted browser URL to a bare clone URL *before* validating it:
        # GitHub carries the location in the path (/tree/, /blob/) and Azure DevOps
        # in the query string (?path=&version=), and git can clone neither. The
        # injection guard then runs on the URL that will actually reach git.
        url = re.sub(r"/(tree|blob)/[^/].*$", "", url.rstrip("/"))
        if is_azure_devops_url(url):
            try:
                _ado_org, _ado_project, _ado_repo = parse_repo_url(url)
                url = build_repo_clone_url(_ado_org, _ado_project, _ado_repo)
            except AzureDevOpsUrlError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        # Validate URL to prevent command injection
        if not re.match(r"^https?://[^\s;|&]+$", url) and not re.match(r"^git@[^\s;|&]+$", url):
            raise HTTPException(status_code=400, detail="Invalid git URL format")
        source_type = "git"
        repo_url = strip_url_credentials(url)
        clone_dir = config.repos_dir / req.project_id
        if not clone_dir.exists():
            # Resolve a clone credential, in the same precedence the credential
            # strategy uses afterwards for fetch/pull (git_credentials.py):
            # Azure DevOps organization PAT, GitHub App installation token, stored
            # PAT, or none at all (public repo).
            if req.azure_devops_connection_id:
                from mcp_server.services.azure_devops_connections import resolve_pat
                async with async_session_factory() as _paydb:
                    _ado_pat = await resolve_pat(_paydb, req.azure_devops_connection_id)
                if not _ado_pat:
                    raise HTTPException(
                        status_code=400,
                        detail="Could not read the credential for this Azure DevOps connection. Reconnect it.",
                    )
                clone_url = authenticated_url_plaintext(repo_url or url, _ado_pat)
            elif req.installation_id:
                from mcp_server.utils.github_app import mint_installation_token
                try:
                    _minted = await mint_installation_token(int(req.installation_id))
                except Exception as exc:
                    logger.warning("Installation token mint failed for %s: %s", req.project_id, exc)
                    raise HTTPException(
                        status_code=400,
                        detail="Could not authenticate via the GitHub App installation",
                    )
                clone_url = authenticated_url_plaintext(repo_url or url, _minted)
            elif req.github_token:
                clone_url = authenticated_url_plaintext(repo_url or url, req.github_token)
            else:
                clone_url = repo_url or url
            # A pinned branch is passed as an option, never interpolated: the
            # value comes from the client. --single-branch keeps the clone to the
            # requested ref so a later ff-only pull cannot drift onto another.
            _clone_args = ["clone"]
            if req.branch:
                _clone_args += ["--branch", req.branch, "--single-branch"]
            _clone_args += ["--", clone_url, str(clone_dir)]
            _clone_proc = await asyncio.create_subprocess_exec(
                "git", *_clone_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                env=git_subprocess_env(),
            )
            try:
                _, _stderr = await asyncio.wait_for(_clone_proc.communicate(), timeout=120.0)
            except asyncio.TimeoutError:
                _clone_proc.kill()
                await _clone_proc.communicate()
                raise HTTPException(status_code=400, detail="Git clone timed out")
            if _clone_proc.returncode != 0:
                logger.warning("Git clone failed for %s: %s", req.project_id, _stderr.decode().strip())
                raise HTTPException(status_code=400, detail="Git clone failed — check the URL and credentials")
            if repo_url:
                await run_git(str(clone_dir), "remote", "set-url", "origin", repo_url)
        local_path = str(clone_dir)
    elif url == "zip":
        source_type = "zip"
        local_path = str(config.repos_dir / req.project_id)
        repo_url = None
    else:
        raise HTTPException(status_code=400, detail=f"Invalid URL/path: {url}")

    # Encrypt and store the PAT if provided
    token_enc: str | None = None
    if source_type == "git" and req.github_token:
        from mcp_server.utils.encryption import encrypt_token as _encrypt_token
        try:
            token_enc = _encrypt_token(req.github_token)
        except Exception as exc:
            logger.warning("Failed to encrypt GitHub token for %s: %s", req.project_id, exc)

    # Resolve the display account for a connection-backed project. Both the GitHub
    # App installation and the Azure DevOps connection store only a non-secret
    # reference; the account label is best-effort for display.
    account_login: str | None = None
    if source_type == "git" and req.azure_devops_connection_id:
        from mcp_server.services.azure_devops_connections import get_connection
        async with async_session_factory() as _labeldb:
            _connection = await get_connection(_labeldb, req.azure_devops_connection_id)
            account_login = _connection.organization_name if _connection else None
    elif source_type == "git" and req.installation_id:
        try:
            from mcp_server.utils.github_app import get_installation_account_login
            account_login = await get_installation_account_login(int(req.installation_id))
        except Exception as exc:
            logger.warning("Could not resolve installation account for %s: %s", req.project_id, exc)

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.add_project(
            project_id=req.project_id,
            local_path=local_path,
            display_name=req.display_name,
            repo_url=repo_url,
            platform=req.platform,
            source_type=source_type,
        )

        if token_enc:
            project.github_token_enc = token_enc

        if source_type == "git" and req.azure_devops_connection_id:
            project.connection_kind = "azure_devops"
            project.azure_devops_connection_id = req.azure_devops_connection_id
            if account_login:
                project.github_account_login = account_login
        elif source_type == "git" and req.installation_id:
            project.connection_kind = "app"
            project.github_installation_id = int(req.installation_id)
            if account_login:
                project.github_account_login = account_login

        # Auto-assign creator to the new project so they can see it
        auth_payload = getattr(request.state, "auth_payload", None)
        if auth_payload:
            user_id = int(auth_payload.get("sub", 0))
            if user_id:
                await db.execute(
                    text(
                        "INSERT INTO project_assignments (user_id, project_id, assigned_at, created_at, updated_at) "
                        "VALUES (:uid, :pid, NOW(), NOW(), NOW()) "
                        "ON CONFLICT (user_id, project_id) DO NOTHING"
                    ),
                    {"uid": user_id, "pid": req.project_id},
                )

        await db.commit()
        await db.refresh(project)

    # Auto-register a per-project webhook only for PAT connections. App connections
    # receive events via the App-level webhook, so this block is skipped for them
    # (guarded by req.github_token, which App-only connections do not set).
    if source_type == "git" and req.github_token and repo_url and config.public_url and config.github_webhook_secret:
        webhook_url = config.public_url.rstrip("/") + "/webhook/github/webhook"
        try:
            from mcp_server.utils.github_api import register_webhook as _register_webhook
            webhook_id = await _register_webhook(repo_url, req.github_token, webhook_url, config.github_webhook_secret)
            async with async_session_factory() as db2:
                from sqlalchemy import update as _update
                from mcp_server.db.models import Project as _Project
                await db2.execute(
                    _update(_Project)
                    .where(_Project.id == req.project_id)
                    .values(github_webhook_id=webhook_id)
                )
                await db2.commit()
        except Exception as exc:
            logger.warning(
                "Webhook auto-registration failed for %s (project registered without webhook): %s",
                req.project_id, exc,
            )

    async with async_session_factory() as db3:
        registry3 = ProjectRegistry(db3)
        project = await registry3.get_project(req.project_id)
        return _project_to_response(project)


_UPLOAD_STREAM_CHUNK_BYTES = 4 * 1024 * 1024  # 4 MiB


async def _stream_upload_to_temp_file(
    file: UploadFile,
    dest_dir: Path,
    *,
    max_bytes: int,
    suffix: str = ".zip",
    chunk_bytes: int = _UPLOAD_STREAM_CHUNK_BYTES,
) -> tuple[Path, str, int]:
    """Stream an UploadFile to a temp file in bounded memory.

    Reads in fixed-size chunks, writing and hashing incrementally so memory use
    stays flat regardless of upload size. Aborts as soon as the running total
    exceeds ``max_bytes``, without reading further chunks from the client, and
    deletes the partial temp file.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    handle = tempfile.NamedTemporaryFile(dir=dest_dir, suffix=suffix, delete=False)
    temp_path = Path(handle.name)
    try:
        while True:
            chunk = await file.read(chunk_bytes)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Upload too large (max {max_bytes // 1024 // 1024} MB)",
                )
            digest.update(chunk)
            handle.write(chunk)
    except Exception:
        handle.close()
        temp_path.unlink(missing_ok=True)
        raise
    handle.close()
    return temp_path, digest.hexdigest(), total


@router.post("/upload-zip", response_model=ZipUploadResponse)
async def upload_zip(
    request: Request,
    file: UploadFile = File(...),
    project_id: str = Form(...),
    platform: str = Form("talend"),
):
    """Upload a ZIP source, safely unpack it, and return an opaque receipt."""
    project_id = project_id.strip()
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$", project_id) or len(project_id) > 100:
        raise HTTPException(400, "Invalid project_id: alphanumeric, hyphens, underscores only")
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="File must be a .zip")
    from mcp_server.config import SUPPORTED_PLATFORMS
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(400, f"Unsupported platform: {platform!r}. Must be one of {sorted(SUPPORTED_PLATFORMS)}")

    # Fast-fail before accepting any archive bytes.
    repo_dir = config.repos_dir / project_id
    if repo_dir.exists():
        raise HTTPException(status_code=409, detail="A source already exists for this project")

    # Enforce upload size limit before reading the full body
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid upload content length") from exc
        if declared_size > config.max_upload_zip_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Upload too large (max {config.max_upload_zip_bytes // 1024 // 1024} MB)",
            )

    # Stream to disk in bounded chunks. Callers never pass ZIP bytes into a
    # graph or workflow checkpoint.
    config.repos_dir.mkdir(parents=True, exist_ok=True)
    zip_path, checksum, size_bytes = await _stream_upload_to_temp_file(
        file, config.uploads_dir, max_bytes=config.max_upload_zip_bytes
    )
    extract_dir = Path(tempfile.mkdtemp(prefix=f".{project_id}-", dir=config.repos_dir))
    try:
        _extract_zip_bundle(zip_path, extract_dir)
    except zipfile.BadZipFile as exc:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="Invalid ZIP file") from exc
    except Exception:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise
    finally:
        zip_path.unlink(missing_ok=True)

    try:
        extract_dir.replace(repo_dir)
    except Exception:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise

    try:
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            project = await registry.add_project(
                project_id=project_id,
                local_path=str(repo_dir),
                platform=platform,
                source_type="zip",
            )

            # Auto-assign creator so they can see the project immediately
            auth_payload = getattr(request.state, "auth_payload", None)
            if auth_payload:
                user_id = int(auth_payload.get("sub", 0))
                if user_id:
                    await db.execute(
                        text(
                            "INSERT INTO project_assignments (user_id, project_id, assigned_at, created_at, updated_at) "
                            "VALUES (:uid, :pid, NOW(), NOW(), NOW()) "
                            "ON CONFLICT (user_id, project_id) DO NOTHING"
                        ),
                        {"uid": user_id, "pid": project_id},
                    )

            await db.commit()
            await db.refresh(project)
            response = _project_to_response(project)
    except Exception:
        shutil.rmtree(repo_dir, ignore_errors=True)
        raise

    return ZipUploadResponse(
        **response.model_dump(),
        upload_receipt=ZipUploadReceipt(
            receipt=_issue_zip_upload_receipt(
                project_id=project_id,
                platform=platform,
                filename=Path(file.filename).name,
                size_bytes=size_bytes,
                sha256=checksum,
            ),
            filename=Path(file.filename).name,
            size_bytes=size_bytes,
            sha256=checksum,
        ),
    )


@router.post("/uploaded-sources/verify", response_model=ZipUploadReceipt)
async def verify_uploaded_zip_source(req: VerifyZipUploadRequest, request: Request):
    """Validate an upload receipt before a Nexus workflow consumes it."""
    payload = _verify_zip_upload_receipt(req.receipt)
    project_id = str(payload["project_id"])
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if project is None or project.source_type != "zip":
            raise HTTPException(status_code=400, detail="Upload receipt does not match a ZIP project source")
    return ZipUploadReceipt(
        receipt=req.receipt,
        filename=str(payload["filename"]),
        size_bytes=int(payload["size_bytes"]),
        sha256=str(payload["sha256"]),
    )


@router.post("/projects/{project_id}/replace-zip", response_model=ZipReplacementResponse)
async def replace_zip_source(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
):
    """Stage a replacement ZIP without replacing the project's active source.

    The S4 coordinator consumes the immutable candidate asynchronously.  This
    route intentionally does not update ``Project.local_path`` or run a scan.
    """
    await require_project_management(request, project_id)
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="File must be a .zip")

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > config.max_upload_zip_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Upload too large (max {config.max_upload_zip_bytes // 1024 // 1024} MB)",
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid upload content length") from exc

    # Fast-fail before accepting any archive bytes.
    async with async_session_factory() as db:
        project = await ProjectRegistry(db).get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        if project.source_type != "zip":
            raise HTTPException(status_code=400, detail="Project does not use a ZIP source")

    zip_path, checksum, _size_bytes = await _stream_upload_to_temp_file(
        file, config.uploads_dir, max_bytes=config.max_upload_zip_bytes
    )

    service = getattr(request.app.state, "zip_revision_service", None)
    if service is None:
        service = ZipRevisionService(
            config.data_dir / "living_docs" / "zip_candidates",
            coordinator=LiveZipPatchCoordinator(log_mgr=request.app.state.log_manager),
        )
    try:
        candidate = await service.stage(
            project_id=project_id,
            filename=Path(file.filename).name,
            staged_archive_path=zip_path,
            archive_sha256=checksum,
            extractor=_extract_zip_bundle,
        )
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid ZIP file") from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Replacement ZIP staging failed for %s", project_id)
        raise HTTPException(status_code=400, detail="Unable to stage replacement ZIP")

    submission = await service.submit(candidate)
    return ZipReplacementResponse(
        project_id=project_id,
        candidate_id=candidate.candidate_id,
        source_revision_id=candidate.source_revision.revision_id,
        sha256=candidate.archive_sha256,
        status=submission.status,
        reason_code=submission.reason_code,
    )


@router.get("/projects/{project_id}/generation-plan", response_model=GenerationPlanResponse)
async def get_generation_plan(project_id: str, request: Request):
    """Dry run: which sub-projects the next generation would pay for.

    Read-only, and deliberately derived from the SAME helpers the run path uses
    (``partition_orchestrator.build_generation_plan`` ->
    ``decide_partition_actions``). A second copy of the skip rule here would let
    this preview drift from what generation actually does, at which point the
    dialog is worse than no dialog.

    Degrades instead of failing: a missing or corrupt scan answers HTTP 200 with
    ``scan_available: false`` (the operator's next step is a scan, not a bug
    report), and any unexpected error answers the same way rather than blocking
    the dialog -- generation must never be gated on its own preview.
    """
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        if await ProjectRegistry(db).get_project(project_id) is None:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    from mcp_server.services.partition_orchestrator import build_generation_plan

    try:
        plan = build_generation_plan(project_id)
    except Exception:  # noqa: BLE001 - a preview must never break the dialog
        logger.warning("Generation plan unavailable for %s", project_id, exc_info=True)
        return GenerationPlanResponse(
            project_id=project_id,
            scan_available=False,
            composite=False,
            partition_count=0,
            will_generate=1,
            will_reuse=0,
        )

    invalidation = (
        GenerationPlanGlobalInvalidation(
            reason=plan.global_invalidation.reason,
            detail=plan.global_invalidation.detail,
        )
        if plan.global_invalidation is not None
        else None
    )
    return GenerationPlanResponse(
        project_id=project_id,
        scan_available=plan.scan_available,
        composite=plan.composite,
        partition_count=plan.partition_count,
        will_generate=plan.will_generate,
        will_reuse=plan.will_reuse,
        global_invalidation=invalidation,
        partitions=[
            GenerationPlanPartition(
                name=decision.name,
                subdir=decision.subdir,
                action=decision.action,
                reason=decision.reason,
                unit_count=decision.unit_count,
                doc_count=decision.doc_count,
                last_generated_at=decision.last_generated_at,
            )
            for decision in plan.decisions
        ],
        notes=list(plan.notes),
    )


def _migrate_flat_source_tree(root: Path, subproject_name: str) -> None:
    """Move a flat source tree into its own sub-project marker directory.

    ``{repos_dir}/{pid}/<tree>`` becomes
    ``{repos_dir}/{pid}/{name}.__intodoc_repo__/<tree>``, which is what turns an
    unmarked single-root project into a partitionable one: the scanner reports a
    marked ``source_root``, ``subproject_key`` resolves it, and the partition
    planner gives that tree its own scoped run.

    Two renames, no copy, so it is atomic in the only sense that matters here --
    on failure the tree is exactly where it started. The intermediate holder
    directory exists because the destination is INSIDE the directory being
    moved; renaming a directory into itself is not possible, so the tree is
    parked as a sibling first and the sibling then takes the original name.
    """
    holder = Path(tempfile.mkdtemp(prefix=f".{root.name}-migrate-", dir=root.parent))
    inner = holder / marker_dir_name(subproject_name, REPO_MARKER)
    try:
        root.replace(inner)
    except OSError:
        shutil.rmtree(holder, ignore_errors=True)
        raise
    try:
        holder.replace(root)
    except OSError:
        try:
            inner.replace(root)
        except OSError:
            # Both renames failed: the tree is intact but parked. Name the path
            # so an operator can finish the move by hand.
            logger.error(
                "Flat source migration left %s parked at %s -- move it back manually",
                root,
                inner,
            )
            raise
        shutil.rmtree(holder, ignore_errors=True)
        raise


@router.post("/projects/{project_id}/sources", response_model=AddSourceResponse)
async def add_project_source(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
):
    """Add a ZIP source to an existing ZIP project as a new sub-project.

    The additive counterpart of ``POST /mcp/upload-zip``, which is deliberately
    the CREATE path and still 409s when a source tree already exists. Here the
    existing tree is the precondition, not the conflict.

    Reuses the upload lane verbatim -- ``_stream_upload_to_temp_file`` (bounded
    memory, size limit) and ``_extract_zip_bundle`` (entry/expansion budgets,
    zip-slip and symlink rejection, nested-archive markers). A second extractor
    is how one of two paths ends up missing a hardening fix.

    The new source is invisible to documentation until the project is rescanned;
    the project is therefore put back to ``registered``, which is the status both
    UIs read as "next step: Scan".
    """
    await require_project_management(request, project_id)
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$", project_id) or len(project_id) > 100:
        raise HTTPException(400, "Invalid project_id: alphanumeric, hyphens, underscores only")
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="File must be a .zip")

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid upload content length") from exc
        if declared_size > config.max_upload_zip_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Upload too large (max {config.max_upload_zip_bytes // 1024 // 1024} MB)",
            )

    # Fast-fail before accepting any archive bytes.
    async with async_session_factory() as db:
        project = await ProjectRegistry(db).get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        if project.source_type != "zip":
            raise HTTPException(
                status_code=400,
                detail="Project does not use a ZIP source. Git projects add "
                "sub-projects through the multi-repo members endpoint.",
            )

    zip_path, _checksum, _size_bytes = await _stream_upload_to_temp_file(
        file, config.uploads_dir, max_bytes=config.max_upload_zip_bytes
    )
    extract_dir = Path(tempfile.mkdtemp(prefix=f".{project_id}-source-", dir=config.repos_dir))
    try:
        _extract_zip_bundle(zip_path, extract_dir)
    except zipfile.BadZipFile as exc:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="Invalid ZIP file") from exc
    except Exception:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise
    finally:
        zip_path.unlink(missing_ok=True)

    root = project_repos_root(project_id)
    async with async_session_factory() as db:
        taken = await RepoSourceRepository(db).taken_subproject_names(project_id)
    taken |= source_names_on_disk(root)

    migrated = False
    existing_name: str | None = None
    try:
        if is_flat_source_tree(root):
            # The pre-existing tree has to become a sub-project of its own
            # before a sibling can exist beside it; otherwise the scan reports
            # one marked and one unmarked root and the old tree's units all land
            # in the catch-all __shared__ partition.
            existing_name = allocate_subproject_name(project_id, taken)
            _migrate_flat_source_tree(root, existing_name)
            taken.add(existing_name)
            migrated = True

        root.mkdir(parents=True, exist_ok=True)
        new_name = allocate_subproject_name(Path(file.filename).stem, taken)
        target = root / marker_dir_name(new_name, REPO_MARKER)
        extract_dir.replace(target)
    except HTTPException:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(extract_dir, ignore_errors=True)
        logger.exception("Additive ZIP source failed for %s", project_id)
        raise HTTPException(
            status_code=500, detail=f"Could not add the source: {exc}"
        ) from exc

    if migrated and existing_name:
        # Recorded now because this is the only moment the owner of the
        # already-published flat documentation is known for certain; the
        # orchestrator reads it to adopt those docs instead of regenerating them.
        from mcp_server.services.partition_orchestrator import record_flat_source_migration

        record_flat_source_migration(project_id, existing_name)

    async with async_session_factory() as db:
        await ProjectRegistry(db).update_status(project_id, "registered")
        await db.commit()

    return AddSourceResponse(
        project_id=project_id,
        subproject=new_name,
        migrated_existing_source=migrated,
        requires_rescan=True,
        status="added",
    )


class _ZipExpansionBudget:
    """Cumulative limits for an uploaded archive bundle."""

    def __init__(self) -> None:
        self.entries = 0
        self.uncompressed_bytes = 0

    def consume(self, members: list[zipfile.ZipInfo]) -> None:
        self.entries += len(members)
        if self.entries > config.max_upload_zip_entries:
            raise HTTPException(status_code=400, detail="ZIP bundle contains too many files")
        self.uncompressed_bytes += sum(member.file_size for member in members)
        if self.uncompressed_bytes > config.max_upload_zip_uncompressed_bytes:
            raise HTTPException(status_code=400, detail="ZIP bundle expands beyond the allowed size")


def _validate_zip_members(members: list[zipfile.ZipInfo], budget: _ZipExpansionBudget | None = None) -> None:
    extracted_members = [member for member in members if not _is_macos_noise(member)]
    if len(extracted_members) > config.max_upload_zip_entries:
        raise HTTPException(status_code=400, detail="ZIP contains too many files")
    total_size = 0
    for member in members:
        # ZIP paths always use forward slashes, but hostile archives can use
        # backslashes which become traversal separators on Windows.
        normalized_name = member.filename.replace("\\", "/")
        member_path = Path(normalized_name)
        if (
            not member.filename
            or normalized_name.startswith("/")
            or ".." in member_path.parts
            or member_path.drive
            or bool(re.match(r"^[A-Za-z]:", normalized_name))
            or (member.external_attr >> 16) & 0o170000 == 0o120000
        ):
            raise HTTPException(status_code=400, detail="ZIP contains an unsafe path")
        if not _is_macos_noise(member):
            total_size += member.file_size
            if total_size > config.max_upload_zip_uncompressed_bytes:
                raise HTTPException(status_code=400, detail="ZIP expands beyond the allowed size")
    if budget is not None:
        budget.consume(extracted_members)


def _is_macos_noise(member: zipfile.ZipInfo) -> bool:
    path = Path(member.filename)
    return "__MACOSX" in path.parts or path.name.startswith("._")


def _extract_zip_bundle(zip_path: Path, extract_dir: Path) -> None:
    """Safely and deterministically expand an outer ZIP and nested ZIPs.

    Nested archives remain beside an explicitly named extraction directory,
    avoiding filename flattening and preserving source provenance for scanners.
    """
    budget = _ZipExpansionBudget()

    def extract_one(archive: Path, destination: Path, depth: int) -> None:
        if depth > config.max_upload_zip_nesting_depth:
            raise HTTPException(status_code=400, detail="ZIP nesting depth exceeds the allowed limit")
        with zipfile.ZipFile(archive, "r") as zf:
            members = zf.infolist()
            _validate_zip_members(members, budget)
            for member in members:
                if _is_macos_noise(member):
                    continue
                # Validation above rejects traversal, absolute paths and links.
                zf.extract(member, destination)

        nested = sorted(
            (p for p in destination.rglob("*") if p.is_file() and p.suffix.lower() == ".zip"),
            key=lambda p: p.relative_to(extract_dir).as_posix(),
        )
        for child in nested:
            # A nested archive found during a previous iteration will already
            # have its marker directory.  This also makes retries deterministic.
            marker = child.with_name(f"{child.stem}.__intodoc_zip__")
            if marker.exists():
                digest = hashlib.sha256(child.relative_to(extract_dir).as_posix().encode()).hexdigest()[:10]
                marker = child.with_name(f"{child.stem}.__intodoc_zip__-{digest}")
                suffix = 1
                while marker.exists() and suffix <= 100:
                    marker = child.with_name(f"{child.stem}.__intodoc_zip__-{digest}-{suffix}")
                    suffix += 1
                if marker.exists():
                    raise HTTPException(status_code=400, detail="Unable to allocate nested ZIP extraction directory")
            marker.mkdir(parents=True, exist_ok=False)
            try:
                extract_one(child, marker, depth + 1)
            except Exception:
                shutil.rmtree(marker, ignore_errors=True)
                raise

    extract_one(zip_path, extract_dir, 0)


def _issue_zip_upload_receipt(
    *, project_id: str, platform: str, filename: str, size_bytes: int, sha256: str
) -> str:
    payload = {
        "project_id": project_id,
        "platform": platform,
        "filename": filename,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "expires_at": int(datetime.datetime.now(datetime.UTC).timestamp()) + config.upload_receipt_ttl_seconds,
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(config.jwt_secret_key.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def _verify_zip_upload_receipt(receipt: str) -> dict[str, object]:
    try:
        encoded, signature = receipt.rsplit(".", 1)
        expected = hmac.new(config.jwt_secret_key.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if not isinstance(payload, dict) or int(payload.get("expires_at", 0)) < int(datetime.datetime.now(datetime.UTC).timestamp()):
            raise ValueError("expired")
        required = ("project_id", "platform", "filename", "size_bytes", "sha256")
        if any(not payload.get(key) for key in required):
            raise ValueError("fields")
        return payload
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid or expired upload receipt") from exc


@router.post("/scan", response_model=StatusResponse)
async def scan_project(req: ScanRequest, request: Request, background_tasks: BackgroundTasks):
    """Trigger an `intodoc scan` for a project (runs in background)."""
    await require_project_access(request, req.project_id)
    log_mgr = request.app.state.log_manager
    output_dir = str(config.intodoc_output_dir / req.project_id)
    scan_lock = acquire_scan_output_lock(output_dir)
    if scan_lock is None:
        raise HTTPException(
            status_code=409,
            detail=f"A scan is already running for project {req.project_id!r}. "
            "Wait for it to complete before starting another scan.",
        )

    try:
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            project = await registry.get_project(req.project_id)
            if not project:
                raise HTTPException(status_code=404, detail=f"Project not found: {req.project_id}")

            repaired_path = await repair_project_repo_path(project)
            if repaired_path:
                await db.flush()

            await registry.update_status(req.project_id, "scanning")
            run = await registry.create_scan_run(req.project_id, triggered_by="api")
            await db.commit()

            local_path = project.local_path
            platform = project.platform
            run_id = run.id

        background_tasks.add_task(
            _run_scan_background,
            req.project_id,
            local_path,
            platform,
            run_id,
            log_mgr,
            output_dir,
            scan_lock,
        )
    except BaseException:
        scan_lock.release()
        raise

    return StatusResponse(status="scan_triggered", message=f"Scan started for {req.project_id}")


async def _run_scan_background(
    project_id: str,
    local_path: str,
    platform: str,
    run_id: int,
    log_mgr,
    output_dir: str,
    scan_lock: ScanOutputLock | None = None,
):
    """Background task with guaranteed release of the output-directory claim."""
    try:
        await _run_scan_background_inner(
            project_id, local_path, platform, run_id, log_mgr, output_dir, scan_lock
        )
    finally:
        if scan_lock is not None:
            scan_lock.release()


async def _run_scan_background_inner(
    project_id: str,
    local_path: str,
    platform: str,
    run_id: int,
    log_mgr,
    output_dir: str,
    scan_lock: ScanOutputLock | None = None,
):
    """Run a scan, persist its status, then advisory-publish its exact snapshot."""
    scanner = ScannerService(log_manager=log_mgr)
    log_mgr.reset_log_file(project_id)

    result = await scanner.run_scan(
        project_id=project_id,
        repo_path=local_path,
        platform=platform,
        output_dir=output_dir,
        scan_lock=scan_lock,
    )

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        if result["success"]:
            await registry.update_scan_result(
                project_id,
                scan_result_path=result["scan_result_path"],
                output_dir=result["output_dir"],
                job_count=result["job_count"],
            )
            await registry.complete_scan_run(
                run_id, status="completed", job_count=result["job_count"]
            )

            # Fire-and-forget: populate topology component summaries in the
            # background. Will read Phase 2 prose if available; otherwise falls
            # back to scan_data.purpose + heuristic. Never raises.
            asyncio.create_task(
                _safe_build_component_summaries(project_id, log_mgr)
            )

        else:
            await registry.update_status(
                project_id, "error", error_message=result.get("error")
            )
            await registry.complete_scan_run(run_id, status="failed")
        await db.commit()

    # Runtime correlation consumes a deliberately bounded, scanner-redaction-
    # approved fact snapshot.  It is advisory: scanner success and its DB state
    # are already durable above, so an unavailable Runtime service must never
    # retroactively fail or invalidate the completed scan.
    if result["success"]:
        try:
            receipt = await StaticTopologyPublisher().publish(
                project_id=project_id,
                output_dir=result["output_dir"],
                source_sequence=run_id,
            )
            logger.info(
                "Published runtime static topology for %s (revision=%s)",
                project_id,
                receipt.get("scan_revision", "unknown"),
            )
        except Exception as exc:
            logger.warning(
                "Static topology publication failed for completed scan %s: %s",
                project_id,
                exc,
            )
            try:
                await log_mgr.emit(
                    project_id,
                    "WARNING: static topology correlation publication failed; "
                    "the completed scan remains valid.",
                    level="warning",
                )
            except Exception:
                logger.warning(
                    "Could not write static topology publication warning for %s",
                    project_id,
                    exc_info=True,
                )


@router.post("/generate", response_model=StatusResponse)
async def generate_docs(req: GenerateRequest, request: Request, background_tasks: BackgroundTasks):
    """Spawn Claude Code CLI to generate docs (runs in background)."""
    await require_project_access(request, req.project_id)
    # Only this authenticated, server-derived actor may authorize a run-scoped
    # connector grant. Never accept actor identity from the request body.
    auth_payload = getattr(request.state, "auth_payload", None) or {}
    initiating_user_id = int(auth_payload.get("sub", 0)) or None
    if any(not value.strip() or len(value) > 200 for value in req.nexus_pack_ids):
        raise HTTPException(status_code=400, detail="Invalid Nexus pack selection")
    log_mgr = request.app.state.log_manager

    if req.project_id in _currently_generating:
        raise HTTPException(
            status_code=409,
            detail=f"Generation is already running for project {req.project_id!r}. "
                   "Wait for it to complete or reset the status.",
        )

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        result = await db.execute(
            select(Project).where(Project.id == req.project_id).with_for_update()
        )
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {req.project_id}")
        running_count = await db.scalar(
            select(func.count(GenerateRun.id)).where(
                GenerateRun.project_id == req.project_id,
                GenerateRun.status == "running",
            )
        )
        if project.status == "generating" or int(running_count or 0) > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Generation is already running for project {req.project_id!r}.",
            )
        effective_pack_id = (
            req.plugin_pack_id
            if req.plugin_pack_id is not None
            else project.plugin_pack_id
        )
        pack = None
        if effective_pack_id is not None:
            pack = (
                await db.execute(select(PluginPack).where(PluginPack.id == effective_pack_id))
            ).scalar_one_or_none()
            if not pack:
                raise HTTPException(status_code=409, detail="Selected Pack is no longer registered. Select a certified Pack through Git/PR.")
            try:
                await pack_governance.require_generation_authority(pack)
            except PackGovernanceDenied as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        await registry.update_status(req.project_id, "generating")
        run = await registry.create_generate_run(req.project_id, triggered_by="api")
        # Pack audit record (docs/one-pack-program.md section 9). Written at
        # creation, not completion: the binding between a deliverable and the
        # pack version that produced it must survive a failed or crashed run.
        if pack is not None:
            # Freeze the effective project/default selection at run creation.
            # The Pack ID is not a model name and must remain independently
            # auditable even if the project selection changes while queued.
            run.pack_id = str(pack.id)
            run.pack_version = pack.version
            run.pack_content_hash = pack.content_sha256
            run.pack_activated_by = (
                f"user:{initiating_user_id}" if initiating_user_id is not None else "api"
            )
            run.pack_activated_at = datetime.datetime.now(datetime.timezone.utc)
        else:
            pack_audit = req.resolved_pack_audit()
            run.pack_id = pack_audit.pack_id
            run.pack_version = pack_audit.pack_version
            run.pack_content_hash = pack_audit.pack_content_hash
            run.pack_activated_by = pack_audit.pack_activated_by
            run.pack_activated_at = pack_audit.pack_activated_at
        await db.commit()
        run_id = run.id

    _currently_generating.add(req.project_id)
    background_tasks.add_task(
        _run_generate_background, req.project_id, run_id, log_mgr,
        investigation_model=req.investigation_model,
        model=req.model,
        write_model=req.write_model,
        # Pass the resolved ID, not the nullable request override. This freezes
        # a project-default Pack for the lifetime of this run.
        plugin_pack_id=effective_pack_id,
        nexus_pack_ids=req.nexus_pack_ids,
        nexus_pack_snapshot=req.nexus_pack_snapshot,
        nexus_presentation=req.nexus_presentation,
        live_evidence=req.live_evidence != "off",
        resume=req.resume,
        run_id_for_lock=run_id,
        doc_generation_provider=(
            req.doc_generation_provider
            if req.doc_generation_provider is not None
            else project.doc_generation_provider
        ),
        instruction_pack_dir=project.instruction_pack_dir,
        enterprise_mode=req.mode == "enterprise",
        initiating_user_id=initiating_user_id,
    )
    return StatusResponse(
        status="generation_triggered",
        message=f"Doc generation started for {req.project_id}",
        run_id=run_id,
    )


@router.post("/projects/{project_id}/git-updates/repair-latest", response_model=StatusResponse)
async def repair_latest_git_update(project_id: str, request: Request, background_tasks: BackgroundTasks):
    """Replay the latest stored Git diff through the diff-only patcher."""
    await require_project_access(request, project_id)
    log_mgr = request.app.state.log_manager

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        if project.source_type != "git":
            raise HTTPException(status_code=400, detail="Repair latest Git update is only available for git projects")

        summary = project.git_update_summary or {}
        before_sha = str(summary.get("before") or "")
        after_sha = str(summary.get("after") or "")
        if not before_sha or not after_sha:
            raise HTTPException(status_code=400, detail="No stored Git diff is available to repair")

    background_tasks.add_task(
        AutoDocUpdateService(log_mgr=log_mgr).run,
        project_id=project_id,
        before_sha=before_sha,
        after_sha=after_sha,
        trigger="repair_latest",
    )
    return StatusResponse(
        status="repair_triggered",
        message=f"Diff-only repair started for {project_id}",
    )


@router.post(
    "/projects/{project_id}/git-updates/full-rebuild",
    response_model=StatusResponse,
)
async def trigger_manual_full_rebuild(
    project_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    body: FullRebuildRequest | None = None,
):
    """Slice 5 (US-7): the ONLY operator path to a whole-repository rebuild.

    Runs a full re-derive (``run_git_reingestion(..., full_rederive=True)``)
    and, on success, resets the drift counters exposed on
    ``GET .../reingestion-status`` (amendments-since-rebuild -> 0;
    last-reconciled advances). This is a deliberate, explicit operator
    action -- no automatic (time/push-count/doc-count) path may ever set
    ``full_rederive=True`` (see
    ``tests/test_services/test_no_automatic_full_rebuild.py``).
    """
    await require_project_access(request, project_id)
    log_mgr = request.app.state.log_manager

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        if project.source_type != "git":
            raise HTTPException(
                status_code=400,
                detail="Manual full rebuild is only available for git projects",
            )
        if project.status in {"scanning", "generating"} or project_id in _currently_generating:
            raise HTTPException(
                status_code=409,
                detail=f"A scan/generate is already running for project {project_id!r}. "
                "Wait for it to complete before triggering a full rebuild.",
            )
        local_path = project.local_path
        credential = provider_for_project(project)
        before_sha = project.last_ingested_commit_sha or "0" * 40

    after_sha = body.after_sha if body and body.after_sha else None
    if not after_sha:
        try:
            await pull_remote_ff_only(local_path, credential)
            after_sha = await run_git(local_path, "rev-parse", "HEAD", strip=True)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Could not resolve current commit for {project_id!r}: "
                f"{sanitize_git_error(str(exc))}",
            )

    background_tasks.add_task(
        run_git_reingestion,
        project_id,
        before_sha,
        after_sha,
        triggered_by=config.manual_full_rebuild_trigger,
        log_mgr=log_mgr,
        targeted_docs=None,
        full_rederive=True,
    )
    return StatusResponse(
        status="full_rebuild_triggered",
        message=f"Manual full rebuild started for {project_id}",
    )


@router.get(
    "/projects/{project_id}/reingestion-readiness",
    response_model=ReingestionReadinessResponse,
)
async def get_reingestion_readiness(project_id: str, request: Request):
    """Inspect Git reingestion readiness without triggering workflow work."""
    await require_project_access(request, project_id)

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        report = await inspect_reingestion_readiness(project)

    return ReingestionReadinessResponse(**report.to_dict())


@router.get(
    "/projects/{project_id}/reingestion-status",
    response_model=ReingestionStatusResponse,
)
async def get_reingestion_status(project_id: str, request: Request):
    """Return compact webhook/reingestion status without starting workflows."""
    await require_project_access(request, project_id)

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        report = await inspect_reingestion_readiness(project)

    return _build_reingestion_status_response(project, report)


@router.post(
    "/projects/{project_id}/reingestion-readiness/repair",
    response_model=ReingestionReadinessResponse,
)
async def repair_reingestion_readiness(project_id: str, request: Request):
    """Repair credential hygiene only; never scan, generate, patch, or sync RAG."""
    await require_project_access(request, project_id)

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        if project.source_type != "git":
            raise HTTPException(status_code=400, detail="Reingestion repair is only available for git projects")

        before = await inspect_reingestion_readiness(project)
        local_report = await repair_remote_hygiene(project)
        repo_url_clean = strip_url_credentials(project.repo_url)
        repo_url_repaired = bool(project.repo_url and repo_url_clean and project.repo_url != repo_url_clean)
        if repo_url_repaired:
            project.repo_url = repo_url_clean

        after = await inspect_reingestion_readiness(project)
        repaired = local_report.repaired or repo_url_repaired
        summary = {
            "action": "reingestion_readiness_repair",
            "repaired": repaired,
            "before_status": before.status,
            "after_status": after.status,
            "issues": after.issues,
            "reason_codes": after.reason_codes,
            "origin_url": after.origin_url,
            "repo_url": after.repo_url,
            "has_docs": after.has_docs,
            "has_rag_support": after.has_rag_support,
            "rag_support_analysis_count": after.rag_support_analysis_count,
            "has_analysis_cache": after.has_analysis_cache,
            "analysis_cache_count": after.analysis_cache_count,
            "targeted_regen_ready": after.targeted_regen_ready,
            "requires_full_rebaseline": after.requires_full_rebaseline,
        }
        error = None if after.ready else f"Reingestion readiness: {after.status}"
        await registry.update_git_remote_metadata(
            project_id,
            repo_url=repo_url_clean if repo_url_clean else None,
            summary=summary,
            error=error,
        )
        await db.commit()

        response = {**after.to_dict(), "repaired": repaired}

    return ReingestionReadinessResponse(**response)


@router.patch("/projects/{project_id}/github-token", response_model=ProjectResponse)
async def update_github_token(project_id: str, req: UpdateGithubTokenRequest, request: Request):
    """Store a new GitHub PAT for a project and re-register the webhook."""
    await require_project_access(request, project_id)
    from mcp_server.utils.encryption import encrypt_token as _encrypt_token, decrypt_token as _decrypt_token
    from sqlalchemy import update as _update
    from mcp_server.db.models import Project as _Project

    try:
        token_enc = _encrypt_token(req.github_token)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Token encryption failed: {exc}")

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        if project.source_type != "git":
            raise HTTPException(status_code=400, detail="GitHub token is only applicable to git projects")

        # Delete old webhook if we have one
        if project.github_webhook_id and project.repo_url:
            try:
                from mcp_server.utils.github_api import delete_webhook as _delete_webhook
                await _delete_webhook(project.repo_url, req.github_token, project.github_webhook_id)
            except Exception as exc:
                logger.warning("Failed to delete old webhook %s: %s", project.github_webhook_id, exc)

        await db.execute(
            _update(_Project)
            .where(_Project.id == project_id)
            .values(github_token_enc=token_enc, github_webhook_id=None)
        )
        await db.commit()

    # Re-register webhook
    repo_url = project.repo_url
    if repo_url and config.public_url and config.github_webhook_secret:
        webhook_url = config.public_url.rstrip("/") + "/webhook/github/webhook"
        try:
            from mcp_server.utils.github_api import register_webhook as _register_webhook
            webhook_id = await _register_webhook(repo_url, req.github_token, webhook_url, config.github_webhook_secret)
            async with async_session_factory() as db2:
                await db2.execute(
                    _update(_Project).where(_Project.id == project_id).values(github_webhook_id=webhook_id)
                )
                await db2.commit()
        except Exception as exc:
            logger.warning("Webhook re-registration failed for %s: %s", project_id, exc)

    async with async_session_factory() as db3:
        registry3 = ProjectRegistry(db3)
        project = await registry3.get_project(project_id)
        return _project_to_response(project)


@router.post("/projects/{project_id}/github-webhook/register", response_model=ProjectResponse)
async def register_github_webhook(project_id: str, request: Request):
    """(Re-)register the GitHub webhook using the stored PAT."""
    await require_project_access(request, project_id)
    from mcp_server.utils.encryption import decrypt_token as _decrypt_token
    from mcp_server.utils.github_api import register_webhook as _register_webhook
    from sqlalchemy import update as _update
    from mcp_server.db.models import Project as _Project

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        if not project.github_token_enc:
            raise HTTPException(status_code=400, detail="No GitHub token stored. Add one via PATCH /projects/{id}/github-token first.")
        if not project.repo_url:
            raise HTTPException(status_code=400, detail="Project has no repo URL")
        if not config.public_url:
            raise HTTPException(status_code=503, detail="MCP_PUBLIC_URL is not configured")
        if not config.github_webhook_secret:
            raise HTTPException(status_code=503, detail="MCP_GITHUB_WEBHOOK_SECRET is not configured")

    try:
        token = _decrypt_token(project.github_token_enc)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Token decryption failed: {exc}")

    webhook_url = config.public_url.rstrip("/") + "/webhook/github/webhook"
    try:
        webhook_id = await _register_webhook(project.repo_url, token, webhook_url, config.github_webhook_secret)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}")

    async with async_session_factory() as db2:
        from sqlalchemy import update as _update2
        from mcp_server.db.models import Project as _Project2
        await db2.execute(
            _update2(_Project2).where(_Project2.id == project_id).values(github_webhook_id=webhook_id)
        )
        await db2.commit()

    async with async_session_factory() as db3:
        registry3 = ProjectRegistry(db3)
        project = await registry3.get_project(project_id)
        return _project_to_response(project)


@router.delete("/projects/{project_id}/github-webhook", response_model=StatusResponse)
async def delete_github_webhook(project_id: str, request: Request):
    """Delete the GitHub webhook and clear the stored webhook ID."""
    await require_project_access(request, project_id)
    from mcp_server.utils.encryption import decrypt_token as _decrypt_token
    from mcp_server.utils.github_api import delete_webhook as _delete_webhook
    from sqlalchemy import update as _update
    from mcp_server.db.models import Project as _Project

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        if project.github_webhook_id and project.repo_url and project.github_token_enc:
            try:
                token = _decrypt_token(project.github_token_enc)
                await _delete_webhook(project.repo_url, token, project.github_webhook_id)
            except Exception as exc:
                logger.warning("Failed to delete webhook from GitHub: %s", exc)

        await db.execute(
            _update(_Project).where(_Project.id == project_id).values(github_webhook_id=None)
        )
        await db.commit()

    return StatusResponse(status="deleted", message=f"Webhook removed for project '{project_id}'")


@router.get("/projects/{project_id}/github-webhook/status")
async def get_github_webhook_status(project_id: str, request: Request):
    """Check live webhook status against the GitHub API."""
    await require_project_access(request, project_id)
    from mcp_server.utils.encryption import decrypt_token as _decrypt_token
    from mcp_server.utils.github_api import check_webhook as _check_webhook
    from sqlalchemy import update as _update
    from mcp_server.db.models import Project as _Project

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    if not project.github_webhook_id:
        return {"project_id": project_id, "webhook_status": "not_registered", "webhook_id": None}

    if not project.github_token_enc or not project.repo_url:
        return {"project_id": project_id, "webhook_status": "unknown", "webhook_id": project.github_webhook_id}

    try:
        token = _decrypt_token(project.github_token_enc)
        is_active = await _check_webhook(project.repo_url, token, project.github_webhook_id)
        status = "active" if is_active else "not_registered"
    except Exception as exc:
        logger.warning("Failed to check webhook for %s: %s", project_id, exc)
        status = "unknown"

    # If GitHub says the hook is gone, clear our stored ID
    if status == "not_registered":
        async with async_session_factory() as db2:
            await db2.execute(
                _update(_Project).where(_Project.id == project_id).values(github_webhook_id=None)
            )
            await db2.commit()

    return {"project_id": project_id, "webhook_status": status, "webhook_id": project.github_webhook_id}


# --- GitHub App install lifecycle --------------------------------------------

_APP_STATE_PURPOSE = "github_app_install"


def _sign_app_state(project_id: str | None) -> str:
    """Sign a short-lived state token carrying the (optional) project to attach."""
    from jose import jwt as _jwt

    now = datetime.datetime.now(datetime.timezone.utc)
    claims = {
        "purpose": _APP_STATE_PURPOSE,
        "pid": project_id or "",
        "exp": int((now + datetime.timedelta(minutes=15)).timestamp()),
        "iat": int(now.timestamp()),
    }
    return _jwt.encode(claims, config.jwt_secret_key, algorithm=config.jwt_algorithm)


def _verify_app_state(state: str | None) -> str | None:
    """Return the project id carried by a valid install state, else None.

    The state is an *optional hint* about which project to attach the
    installation to — it is not the ownership proof (that is the OAuth code or an
    existing binding, below). GitHub also bounces users to the setup URL with no
    state at all (e.g. saving repository access on the installation settings
    page), and the token expires after 15 minutes of clicking around in GitHub's
    UI. Failing the whole connect in those cases turns a legitimate return trip
    into a dead end, so an unusable state degrades to "no project to attach".
    Only a *signed* state can name a project, so this grants nothing.
    """
    if not state:
        return None
    from jose import JWTError, jwt as _jwt

    try:
        claims = _jwt.decode(state, config.jwt_secret_key, algorithms=[config.jwt_algorithm])
    except JWTError as exc:
        logger.info("Ignoring invalid or expired GitHub App install state: %s", exc)
        return None
    if claims.get("purpose") != _APP_STATE_PURPOSE:
        logger.info("Ignoring GitHub App install state with unexpected purpose")
        return None
    return claims.get("pid") or None


@router.get("/github/app/connect", response_model=GithubAppConnectResponse)
async def github_app_connect(request: Request, project_id: str | None = Query(None)):
    """Return the GitHub App install URL (carrying signed state) to redirect the user to."""
    from mcp_server.utils.github_app import github_app_configured, install_url

    if not github_app_configured():
        raise HTTPException(status_code=503, detail="GitHub App is not configured on this server")
    if project_id:
        await require_project_access(request, project_id)
    return GithubAppConnectResponse(install_url=install_url(_sign_app_state(project_id)))


@router.get("/github/app/callback")
async def github_app_callback(
    request: Request,
    installation_id: int = Query(...),
    setup_action: str | None = Query(None),
    state: str | None = Query(None),
    code: str | None = Query(None),
):
    """Post-install redirect: verify the user owns the installation, bind it, and bounce to the UI."""
    from mcp_server.utils.github_app import (
        exchange_user_access_token,
        get_installation_account_login,
        github_app_oauth_configured,
        user_can_access_installation,
    )
    from mcp_server.services.github_app_ownership import (
        installation_is_bound_to,
        record_installation_owner,
    )

    project_id = _verify_app_state(state)

    auth_payload = getattr(request.state, "auth_payload", None)
    user_id = int(auth_payload.get("sub", 0)) if auth_payload else 0
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # GitHub sends the user to this Setup URL for more than the first install:
    # saving repository access on github.com/settings/installations/<id> bounces
    # here with setup_action=update and NEITHER state NOR code. Ownership of an
    # already-bound installation was proven when it was first connected, so
    # demanding a fresh authorization code there rejects a legitimate return trip.
    async with async_session_factory() as db:
        already_owned = await installation_is_bound_to(db, int(installation_id), user_id)

    # Multi-tenant guard: prove the connecting user actually controls this installation
    # (their own GET /user/installations) before binding it to them. Without the OAuth
    # client configured we cannot verify — that is only safe on single-tenant deployments.
    if github_app_oauth_configured() and not already_owned:
        if not code:
            raise HTTPException(
                status_code=400,
                detail=(
                    "GitHub did not send an authorization code for this new installation. "
                    "Start from 'Connect with GitHub App' in Nexus, and make sure 'Request "
                    "user authorization (OAuth) during installation' is enabled on the App."
                ),
            )
        try:
            user_token = await exchange_user_access_token(code)
            owns = await user_can_access_installation(user_token, int(installation_id))
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("Installation ownership verification failed for %s: %s", installation_id, exc)
            raise HTTPException(status_code=502, detail="Could not verify installation ownership with GitHub")
        if not owns:
            raise HTTPException(status_code=403, detail="You do not have access to this GitHub App installation")
    elif already_owned:
        logger.info(
            "Installation %s is already bound to user %s — skipping re-verification "
            "(GitHub returned to the setup URL without an authorization code).",
            installation_id,
            user_id,
        )
    else:
        logger.warning(
            "GitHub App OAuth is not configured — binding installation %s without ownership "
            "verification. This is only safe on a single-tenant deployment.",
            installation_id,
        )

    account_login: str | None = None
    try:
        account_login = await get_installation_account_login(int(installation_id))
    except Exception as exc:
        logger.warning("Could not resolve installation account for %s: %s", installation_id, exc)

    from sqlalchemy import update as _update
    from mcp_server.db.models import Project as _Project
    async with async_session_factory() as db:
        await record_installation_owner(db, int(installation_id), user_id, account_login)
        if project_id:
            await require_project_access(request, project_id)
            await db.execute(
                _update(_Project)
                .where(_Project.id == project_id)
                .values(
                    connection_kind="app",
                    github_installation_id=int(installation_id),
                    github_account_login=account_login,
                )
            )
        await db.commit()

    redirect = config.github_app_post_install_redirect
    if redirect:
        sep = "&" if "?" in redirect else "?"
        return RedirectResponse(f"{redirect}{sep}installation_id={installation_id}&connected=1")
    return {"installation_id": installation_id, "project_id": project_id, "account_login": account_login, "connected": True}


@router.get("/github/app/installations", response_model=GithubAppInstallationsResponse)
async def github_app_installations(request: Request):
    """List GitHub App installations available to the current Nexus user.

    Bindings are reconciled against GitHub on every read: an owner can uninstall
    the App without telling us, and a stale row would otherwise keep the UI
    showing a connected account that cannot mint tokens.

    The deployment Owner can already authorize every installation, so their
    list comes directly from GitHub. This also recovers installations whose
    local callback binding was missed without weakening tenant isolation for
    regular users.
    """
    from mcp_server.db.models import GithubAppInstallation
    from mcp_server.services.github_app_ownership import _is_superadmin, reconcile_installations
    from mcp_server.utils.github_app import github_app_configured, list_app_installations

    auth_payload = getattr(request.state, "auth_payload", None)
    user_id = int(auth_payload.get("sub", 0)) if auth_payload else 0
    async with async_session_factory() as db:
        is_superadmin = await _is_superadmin(db, user_id)
        query = select(GithubAppInstallation)
        if not is_superadmin:
            query = query.where(GithubAppInstallation.owner_user_id == user_id)
        result = await db.execute(query.order_by(GithubAppInstallation.account_login))
        rows = await reconcile_installations(db, list(result.scalars().all()))
        installations: list[GithubAppInstallationItem] = [
            GithubAppInstallationItem(
                installation_id=int(row.installation_id),
                account_login=row.account_login,
            )
            for row in rows
        ]
        if is_superadmin and github_app_configured():
            try:
                live = await list_app_installations()
            except Exception as exc:
                logger.warning("Could not list live GitHub App installations: %s", exc)
                raise HTTPException(
                    status_code=502,
                    detail="Could not verify GitHub App connection status with GitHub",
                ) from exc
            installations = sorted(
                (
                    GithubAppInstallationItem(
                        installation_id=int(item["installation_id"]),
                        account_login=(
                            str(item["account_login"])
                            if item.get("account_login") is not None
                            else None
                        ),
                        account_type=(
                            str(item["account_type"])
                            if item.get("account_type") in {"User", "Organization"}
                            else None
                        ),
                        settings_url=(
                            str(item["settings_url"])
                            if item.get("settings_url") is not None
                            else None
                        ),
                    )
                    for item in live
                ),
                key=lambda item: item.account_login or "",
            )
    return GithubAppInstallationsResponse(installations=installations)


@router.get("/github/app/installations/{installation_id}/repos", response_model=GithubAppReposResponse)
async def github_app_installation_repos(installation_id: int, request: Request):
    """List repositories an installation can access (for the connect repo-picker)."""
    from mcp_server.utils.github_app import (
        get_installation_account_details,
        github_app_configured,
        installation_exists,
        list_installation_repositories,
    )

    if not github_app_configured():
        raise HTTPException(status_code=503, detail="GitHub App is not configured on this server")

    auth_payload = getattr(request.state, "auth_payload", None)
    user_id = int(auth_payload.get("sub", 0)) if auth_payload else 0
    from mcp_server.services.github_app_ownership import authorize_installation, prune_installation
    async with async_session_factory() as db:
        if not await authorize_installation(db, int(installation_id), user_id):
            raise HTTPException(status_code=403, detail="You do not have access to this GitHub App installation")

    try:
        repos = await list_installation_repositories(int(installation_id))
        account = await get_installation_account_details(int(installation_id))
    except Exception as exc:
        logger.warning("Failed to list installation repos for %s: %s", installation_id, exc)
        # An uninstalled App fails here exactly like a transient GitHub outage.
        # Ask GitHub whether the installation still exists so the client gets a
        # terminal "reconnect" answer instead of a retryable 502, and drop the
        # stale binding that would keep the UI showing it as connected.
        gone = False
        try:
            gone = not await installation_exists(int(installation_id))
        except Exception as probe_exc:  # GitHub unreachable — treat as transient
            logger.warning(
                "Could not verify installation %s after a repo-list failure: %s",
                installation_id,
                probe_exc,
            )
        if gone:
            async with async_session_factory() as db:
                await prune_installation(db, int(installation_id))
                await db.commit()
            raise HTTPException(
                status_code=404,
                detail="This GitHub App installation no longer exists. Connect with the GitHub App again.",
            )
        raise HTTPException(status_code=502, detail="Could not list repositories for this installation")
    return GithubAppReposResponse(
        installation_id=int(installation_id),
        account_login=account["account_login"],
        account_type=account["account_type"],
        settings_url=account["settings_url"],
        repositories=[GithubAppRepoItem(**r) for r in repos],
    )


_PERMANENT_HTTP_CODES = {400, 401, 403, 404, 422}
_HTTP_STATUS_RE = re.compile(r"\b([45]\d{2})\b")


def _has_permanent_error(errors: list[str]) -> bool:
    """True if any error message contains a non-retryable HTTP status code."""
    for e in errors:
        for m in _HTTP_STATUS_RE.finditer(e):
            if int(m.group(1)) in _PERMANENT_HTTP_CODES:
                return True
    return False


async def _push_with_retry(
    bridge,
    project_id: str,
    output_dir,
    filenames: set[str] | None = None,
    extra_doc_names: set[str] | None = None,
    max_attempts: int = 3,
):
    """Push docs to RAG with exponential backoff retry. Returns (result, errors_or_None)."""
    import asyncio as _asyncio
    last_errors = None
    result: dict = {}
    for attempt in range(max_attempts):
        if attempt > 0:
            await _asyncio.sleep(2 ** attempt)
        result = await bridge.push_docs(
            project_id=project_id,
            output_dir=str(output_dir),
            filenames=filenames,
            extra_doc_names=extra_doc_names,
        )
        if not result.get("errors"):
            return result, None
        last_errors = result["errors"]
        # Permanent errors (auth, CSRF, bad request) won't succeed on retry
        if _has_permanent_error(last_errors):
            break
    return result, last_errors


async def _current_git_head(local_path: str) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "HEAD",
            cwd=local_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return stdout.decode().strip()
    except Exception as e:
        logger.warning("Failed reading git HEAD for %s: %s", local_path, e)
    return None


def _read_public_doc_texts(docs_dir: Path) -> dict[str, str]:
    """Return top-level generated docs eligible for RAG indexing."""
    if not docs_dir.exists():
        return {}
    docs: dict[str, str] = {}
    for md_file in sorted(docs_dir.glob("*.md")):
        if not md_file.name[0].isdigit():
            continue
        try:
            docs[md_file.name] = md_file.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed reading generated doc %s: %s", md_file, e)
    return docs


async def _auto_compare_run(project_id: str, run_id: int) -> None:
    """After a run completes, compare against previous completed run and store verdict."""
    try:
        from mcp_server.api.routes.generate_runs import build_run_comparison
        from sqlalchemy import update as sa_update
        from mcp_server.db.models import GenerateRun

        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            runs = await registry.list_generate_runs(project_id)
            baseline = next(
                (r for r in runs if r.status == "completed" and r.id != run_id),
                None,
            )
            if baseline is None:
                return  # first ever completed run — nothing to compare against

        comparison = await build_run_comparison(project_id, baseline.id, run_id)
        verdict = comparison.get("verdict")

        async with async_session_factory() as db:
            await db.execute(
                sa_update(GenerateRun)
                .where(GenerateRun.id == run_id)
                .values(auto_verdict=verdict, auto_baseline_run_id=baseline.id)
            )
            await db.commit()
    except Exception as e:
        logger.warning("Auto-comparison failed for run %s/%s: %s", project_id, run_id, e)


def _diff_generated_docs(old_docs_dir: Path, new_docs_dir: Path) -> tuple[set[str], set[str]]:
    """Compare generated docs and return (added_or_changed, removed) filenames."""
    old_docs = _read_public_doc_texts(old_docs_dir)
    new_docs = _read_public_doc_texts(new_docs_dir)

    changed = {
        filename
        for filename, content in new_docs.items()
        if old_docs.get(filename) != content
    }
    removed = set(old_docs) - set(new_docs)
    return changed, removed


async def _safe_build_component_summaries(project_id: str, log_mgr) -> None:
    """Background wrapper for component summary generation. Never raises.

    Called fire-and-forget after a successful scan and after a successful
    doc-generation swap. Re-uses the same scan/summaries.json cache, so each
    call upgrades the cache if Phase 2 prose has become available since the
    last run.
    """
    try:
        await build_component_summaries_async(project_id, log_mgr=log_mgr)
    except Exception as exc:  # noqa: BLE001 - background task, must not bubble
        logger.warning(
            "Component summary generation failed for %s: %s", project_id, exc,
            exc_info=True,
        )


def _load_enterprise_gate_results(base_dir: Path) -> dict | None:
    for candidate in (
        base_dir / "_assessment" / "enterprise_gates.json",
        base_dir / "_audit" / "enterprise_gates.json",
    ):
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, json.JSONDecodeError):
            return None
    return None


async def _write_plugin_pack_execution_receipt(
    staging_dir: Path,
    *,
    run_id: int,
    plugin_pack_id: int,
    model_name: str | None,
    write_model_name: str | None,
) -> None:
    """Persist the exact Plugin Pack/model binding used by a generated run."""
    from mcp_server.services.plugin_schema import load_plugin_manifest

    async with async_session_factory() as db:
        pack = (
            await db.execute(select(PluginPack).where(PluginPack.id == plugin_pack_id))
        ).scalar_one_or_none()
    if pack is None:
        return
    manifest = load_plugin_manifest(Path(pack.path))
    audit_dir = staging_dir / "_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    public_docs = sorted(path.name for path in staging_dir.glob("*.md"))
    payload = {
        "schema_version": 1,
        "run_id": run_id,
        "plugin_pack": {
            "id": pack.id,
            "display_name": pack.name,
            "manifest_name": manifest.name,
            "version": manifest.version,
            "content_sha256": pack.content_sha256,
            "owner_user_id": pack.owner_user_id,
        },
        "models": {
            "analysis": model_name,
            "writing": write_model_name or model_name,
        },
        "effective_contract": {
            "phases": (
                manifest.phases.model_dump(mode="json")
                if manifest.phases is not None
                else None
            ),
            "documents": [
                doc.model_dump(mode="json") for doc in (manifest.docs or [])
            ],
            "audience": (
                manifest.audience.model_dump(mode="json")
                if manifest.audience is not None
                else None
            ),
            "quality_gates": (
                manifest.quality_gates.model_dump(mode="json")
                if manifest.quality_gates is not None
                else None
            ),
        },
        "generated_documents": public_docs,
    }
    (audit_dir / "plugin_pack_execution_receipt.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )


async def _run_generate_background(
    project_id: str,
    run_id: int,
    log_mgr,
    *,
    investigation_model: str | None = None,
    model: str | None = None,
    write_model: str | None = None,
    plugin_pack_id: int | None = None,
    nexus_pack_ids: list[str] | None = None,
    nexus_pack_snapshot: NexusPackSnapshot | None = None,
    nexus_presentation: NexusPresentation | None = None,
    live_evidence: bool = True,
    resume: bool = True,
    run_id_for_lock: int | None = None,
    doc_generation_provider: str | None = None,
    instruction_pack_dir: str | None = None,
    partial_rag_sync: bool = False,
    enterprise_mode: bool = True,
    pre_swap_gate: "Callable[[Path], GateOutcome] | None" = None,
    scoped_docs: set[str] | None = None,
    initiating_user_id: int | None = None,
):
    """Background task: spawn Claude Code for doc generation.

    Uses a staging directory (docs_staging/) so that existing docs survive
    if generation fails.  Only on success is the staging dir swapped in.

    ``plugin_pack_id`` is an optional per-run override. When supplied, it takes
    precedence over the project's saved ``plugin_pack_id``. Pass ``None`` to
    use the project default (existing behaviour).

    ``pre_swap_gate`` (reingestion only) runs blocking correctness gates against
    the staging tree before the swap. If it returns a failed ``GateOutcome`` the
    swap is skipped, ``docs/`` is left intact, the project is marked ``error``
    with a ``manual_review`` reason, and the SHA is not advanced. Manual and
    onboarding generate paths pass no hook and are unaffected.

    Composite/oversized uploads: when the deterministic partition planner
    resolves the scan into more than one sub-project, the full-generation path
    (no ``scoped_docs``, no reingestion gate) delegates to the partition
    orchestrator, which runs one scoped legacy generation per sub-project into
    ``output/{project_id}/{subproject}/docs``. Single-partition scans continue
    below on the unchanged single-run path.
    """
    claude = DocGenerationCoordinator(log_manager=log_mgr)
    log_mgr.reset_log_file(project_id)
    current_task = asyncio.current_task()
    if current_task is not None:
        _generation_tasks[project_id] = current_task

    if scoped_docs is None and pre_swap_gate is None and not partial_rag_sync:
        from mcp_server.services.partition_orchestrator import (
            plan_composite_generation,
            run_partitioned_generation,
        )

        planned = plan_composite_generation(project_id)
        if planned is not None:
            plan, facts = planned
            try:
                await run_partitioned_generation(
                    project_id,
                    run_id,
                    log_mgr,
                    claude,
                    plan=plan,
                    facts=facts,
                    resume=resume,
                    generate_kwargs={
                        "investigation_model": investigation_model,
                        "model": model,
                        "write_model": write_model,
                        "plugin_pack_id": plugin_pack_id,
                        "nexus_pack_ids": nexus_pack_ids,
                        "nexus_pack_snapshot": nexus_pack_snapshot,
                        "nexus_presentation": nexus_presentation,
                        "live_evidence": live_evidence,
                        "doc_generation_provider": doc_generation_provider,
                        "instruction_pack_dir": instruction_pack_dir,
                        "enterprise_mode": enterprise_mode,
                    },
                )
            except Exception:
                logger.exception(
                    "Composite orchestration crashed for %s", project_id
                )
                async with async_session_factory() as db:
                    registry = ProjectRegistry(db)
                    await registry.update_status(
                        project_id, "error",
                        error_message="Composite doc generation crashed; see logs",
                    )
                    await db.commit()
            finally:
                _currently_generating.discard(project_id)
                _generation_tasks.pop(project_id, None)
                await log_mgr.emit_done(project_id)
            return

    try:
        result = await claude.generate_docs(
            project_id, investigation_model=investigation_model, model=model,
            write_model=write_model, plugin_pack_id=plugin_pack_id,
            nexus_pack_ids=nexus_pack_ids,
            nexus_pack_snapshot=nexus_pack_snapshot,
            nexus_presentation=nexus_presentation,
            live_evidence=live_evidence,
            resume=resume, run_id=run_id_for_lock or run_id,
            doc_generation_provider=doc_generation_provider,
            instruction_pack_dir=instruction_pack_dir,
            enterprise_mode=enterprise_mode,
            scoped_docs=scoped_docs,
            initiating_user_id=initiating_user_id,
        )
    except asyncio.CancelledError:
        await log_mgr.emit(project_id, "Generation cancelled by user.", level="warning")
        result = {
            "success": False,
            "error": "Generation cancelled by user",
            "model_name": model,
            "write_model_name": write_model,
        }
    except Exception as exc:
        logger.exception("Doc generation background task crashed for %s", project_id)
        result = {"success": False, "error": str(exc), "model_name": model, "write_model_name": write_model}

    success = result.get("success", False)

    output_dir = config.project_root / "output" / project_id
    staging_dir = output_dir / "docs_staging"
    docs_dir = output_dir / "docs"

    # Get log file path
    log_path = log_mgr.get_current_log_path(project_id)
    log_file = log_path.name if log_path else None

    if success:
        # Count docs produced in staging
        doc_count = len(list(staging_dir.glob("*.md"))) if staging_dir.exists() else 0
        gate_results = _load_enterprise_gate_results(staging_dir) if staging_dir.exists() else None

        if staging_dir.exists() and plugin_pack_id is not None:
            await _write_plugin_pack_execution_receipt(
                staging_dir,
                run_id=run_id,
                plugin_pack_id=plugin_pack_id,
                model_name=result.get("model_name"),
                write_model_name=result.get("write_model_name"),
            )

        gate_outcome: GateOutcome | None = (
            pre_swap_gate(staging_dir) if pre_swap_gate is not None and doc_count > 0 else None
        )
        if (
            gate_outcome is None
            and enterprise_mode
            and plugin_pack_id is not None
            and gate_results is not None
        ):
            gates = gate_results.get("gates") or {}
            failed_blocking = [
                name
                for name in ("pack_quality", "final_redaction")
                if not (gates.get(name) or {}).get("ok", False)
            ]
            if failed_blocking:
                gate_outcome = GateOutcome(
                    passed=False,
                    evaluated=True,
                    reason=(
                        "Plugin Pack publication blocked by: "
                        + ", ".join(failed_blocking)
                    ),
                )
        if gate_outcome is not None and not gate_outcome.passed:
            # Blocking correctness gate held the swap. Keep docs_staging/ intact
            # (existing keep-old-docs mechanism) so the previous good docs in
            # docs/ remain published and the SHA is not advanced.
            reason = gate_outcome.reason or "blocking correctness gate failed"
            manual_review_reason = f"manual_review: {reason}"
            await log_mgr.emit(
                project_id,
                f"Publication held by correctness gate: {reason}",
                level="error",
            )
            async with async_session_factory() as db:
                registry = ProjectRegistry(db)
                await registry.update_status(
                    project_id, "error", error_message=manual_review_reason
                )
                await registry.complete_generate_run(
                    run_id,
                    status="failed",
                    cost_usd=result.get("cost_usd"),
                    duration_ms=result.get("duration_ms"),
                    duration_api_ms=result.get("duration_api_ms"),
                    input_tokens=result.get("input_tokens"),
                    output_tokens=result.get("output_tokens"),
                    num_turns=result.get("num_turns"),
                    session_id=result.get("session_id"),
                    doc_count=0,
                    model_name=result.get("model_name"),
                    write_model_name=result.get("write_model_name"),
                    quality_score=None,
                    error_message=manual_review_reason,
                    log_file=log_file,
                    run_verdict="held_manual_review",
                    gate_results=_load_enterprise_gate_results(staging_dir) if staging_dir.exists() else None,
                )
                await db.commit()
            _currently_generating.discard(project_id)
            _generation_tasks.pop(project_id, None)
            await log_mgr.emit_done(project_id)
            return

        if doc_count > 0:
            changed_rag_docs: set[str] | None = None
            removed_rag_docs: set[str] = set()
            if partial_rag_sync:
                changed_rag_docs, removed_rag_docs = _diff_generated_docs(
                    docs_dir, staging_dir
                )

            # Atomic swap with rollback + `docs_prev/` backup kept only during
            # the swap itself (see services/atomic_swap.py). A crash between
            # the two renames is recoverable on next startup via
            # recover_stale_backups().
            swap_docs(output_dir)

            # Refresh the incremental-regen analysis cache from the persisted
            # _rag_support/ package (Phase 4 packages the Phase 2 group
            # analyses before deleting _analysis/).
            replace_analysis_cache_from_rag_support(output_dir)

            # Fire-and-forget: upgrade topology component summaries with the
            # now-persisted Phase 2 prose. This call is cheap when prose is
            # missing and produces rich summaries when it exists.
            asyncio.create_task(
                _safe_build_component_summaries(project_id, log_mgr)
            )

            # Push new docs to RAG server-side
            bridge = RagBridge(log_manager=log_mgr)
            # Open a DB session for hash-row cleanup only when the flag is on
            # and there are actually docs to delete.
            _hash_del_ctx = (
                async_session_factory()
                if removed_rag_docs and config.incremental_hash_skip_enabled
                else nullcontext(None)
            )
            async with _hash_del_ctx as _hdb:
                _hash_repo = DocRagHashRepository(_hdb) if _hdb is not None else None
                for filename in sorted(removed_rag_docs):
                    try:
                        await bridge.delete_document(project_id, filename)
                        if _hash_repo is not None:
                            await _hash_repo.delete_hash(project_id, filename)
                        await log_mgr.emit(
                            project_id,
                            f"Deleted stale RAG doc: {filename}",
                            level="info",
                        )
                    except Exception as e:
                        await log_mgr.emit(
                            project_id,
                            f"Failed to delete stale RAG doc {filename}: {e}",
                            level="error",
                        )
                if _hdb is not None:
                    await _hdb.commit()
            # Pack-generated docs use non-numeric names (e.g. TABLE_INVENTORY.md).
            # Include them explicitly so they aren't filtered out by the digit-prefix gate.
            pack_doc_names = {
                f"{name}.md" for name in (result.get("required_docs") or [])
                if not name[0].isdigit()
            }
            _rag_result, rag_errors = await _push_with_retry(
                bridge,
                project_id=project_id,
                output_dir=docs_dir,
                filenames=changed_rag_docs,
                extra_doc_names=pack_doc_names or None,
            )
            if rag_errors:
                await log_mgr.emit(
                    project_id,
                    f"RAG push failed after retries (docs are saved): {rag_errors}",
                    level="error",
                )

            # Sync internal support layers (Phase 2 analyses, support maps,
            # scorecard, evidence slices) and entity aliases. Full
            # delete-and-repush: removed groups are invisible to the visible
            # doc diff. Errors merge into rag_errors so the run verdict
            # reports partial RAG sync.
            internal_sync = await bridge.sync_internal_layers(
                project_id, docs_dir, run_id=run_id
            )
            if internal_sync["errors"]:
                rag_errors = (rag_errors or []) + internal_sync["errors"]

            # Persist token usage to RAG backend
            try:
                await bridge.post_usage(
                    interface="doc_generation",
                    project_id=project_id,
                    input_tokens=result.get("input_tokens"),
                    output_tokens=result.get("output_tokens"),
                    cost_usd=result.get("cost_usd"),
                    latency_ms=result.get("duration_ms"),
                    model_name=result.get("model_name", "claude"),
                    provider="anthropic",
                )
            except Exception as e:
                logger.warning("Failed to post doc_generation usage: %s", e)

            # Generate assessment HTML from the new docs
            try:
                from mcp_server.services.assessment_service import AssessmentService

                svc = AssessmentService()
                await svc.generate(project_id)
            except Exception as e:
                await log_mgr.emit(
                    project_id,
                    f"Assessment generation failed: {e}",
                    level="error",
                )

            # Score the generated docs and write run_metrics.json
            quality_score: float | None = None
            try:
                from mcp_server.services.doc_quality_scorer import score_project
                quality_report = score_project(project_id, docs_dir=docs_dir)
                quality_score = quality_report.overall

                metrics = {
                    "run_id": run_id,
                    "started_at": result.get("started_at"),
                    "analysis_model": result.get("model_name"),
                    "write_model": result.get("write_model_name"),
                    "provider": doc_generation_provider,
                    "input_tokens": result.get("input_tokens"),
                    "output_tokens": result.get("output_tokens"),
                    "cost_usd": result.get("cost_usd"),
                    "duration_ms": result.get("duration_ms"),
                    "num_turns": result.get("num_turns"),
                    "doc_quality": quality_report.to_dict(),
                }
                # Write to docs/ (latest run, always overwritten)
                (docs_dir / "run_metrics.json").write_text(
                    json.dumps(metrics, indent=2, default=str), encoding="utf-8"
                )
                # Write to logs/ with run_id for historical comparison
                run_metrics_log = config.project_root / "output" / project_id / "logs" / f"run_metrics_{run_id}.json"
                run_metrics_log.parent.mkdir(parents=True, exist_ok=True)
                run_metrics_log.write_text(
                    json.dumps(metrics, indent=2, default=str), encoding="utf-8"
                )
                await log_mgr.emit(
                    project_id,
                    f"Quality score: {quality_score:.2f}/5.0 ({len(quality_report.docs)} docs graded)",
                    level="info",
                )
            except Exception as e:
                logger.error("Doc quality scoring failed: %s", e)

            # Build rag_error_message to persist if push failed
            rag_error_message: str | None = (
                f"RAG push failed after retries: {rag_errors}" if rag_errors else None
            )
            run_verdict = (
                "draft"
                if not enterprise_mode
                else "published_with_rag_error"
                if rag_errors
                else "client_ready"
                if gate_results and gate_results.get("client_ready")
                else "published"
            )
        else:
            # Generation reported success but produced no docs — treat as failure
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            await log_mgr.emit(
                project_id,
                "Generation produced no documents — marking as failed.",
                level="error",
            )
            async with async_session_factory() as db:
                registry = ProjectRegistry(db)
                await registry.update_status(
                    project_id, "error",
                    error_message="Generation produced no documents",
                )
                await registry.complete_generate_run(
                    run_id,
                    status="failed",
                    cost_usd=result.get("cost_usd"),
                    duration_ms=result.get("duration_ms"),
                    duration_api_ms=result.get("duration_api_ms"),
                    input_tokens=result.get("input_tokens"),
                    output_tokens=result.get("output_tokens"),
                    num_turns=result.get("num_turns"),
                    session_id=result.get("session_id"),
                    doc_count=0,
                    model_name=result.get("model_name"),
                    write_model_name=result.get("write_model_name"),
                    quality_score=None,
                    error_message="Generation produced no documents",
                    log_file=log_file,
                    run_verdict="failed",
                    gate_results=_load_enterprise_gate_results(staging_dir) if staging_dir.exists() else None,
                )
                await db.commit()
            _currently_generating.discard(project_id)
            _generation_tasks.pop(project_id, None)
            await log_mgr.emit_done(project_id)
            return

        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            await registry.update_status(
                project_id, "ready", error_message=rag_error_message
            )
            await registry.update_generate_result(project_id, doc_count=doc_count)
            if rag_error_message is None and _rag_result.get("pushed", 0) > 0:
                await registry.update_rag_push(project_id)
            project = await registry.get_project(project_id)
            if project and project.source_type == "git" and rag_error_message is None:
                head_sha = await _current_git_head(project.local_path)
                if head_sha:
                    await registry.update_last_ingested_commit(project_id, head_sha)
                # Prime the reingestion baseline (fingerprint hashes + prev_scan)
                # so the first push after a manual generation can be diffed
                # instead of forcing a full regen. Same helper the webhook
                # reingestion path uses; best-effort, must not fail the run.
                try:
                    from mcp_server.services.git_reingestion import (
                        prime_reingestion_baseline,
                    )
                    await prime_reingestion_baseline(db, project_id, output_dir)
                except Exception as e:
                    logger.warning(
                        "Failed to prime reingestion baseline for %s: %s",
                        project_id, e,
                    )
            final_status = (
                "completed_warnings"
                if result.get("has_warnings")
                else "completed"
            )
            await registry.complete_generate_run(
                run_id,
                status=final_status,
                cost_usd=result.get("cost_usd"),
                duration_ms=result.get("duration_ms"),
                duration_api_ms=result.get("duration_api_ms"),
                input_tokens=result.get("input_tokens"),
                output_tokens=result.get("output_tokens"),
                num_turns=result.get("num_turns"),
                session_id=result.get("session_id"),
                doc_count=doc_count,
                model_name=result.get("model_name"),
                write_model_name=result.get("write_model_name"),
                quality_score=quality_score,
                error_message=rag_error_message,
                log_file=log_file,
                run_verdict=run_verdict,
                gate_results=gate_results,
            )
            await db.commit()
        await _auto_compare_run(project_id, run_id)
    else:
        # Generation failed — preserve staging for resume (old docs/ remains intact)
        if staging_dir.exists():
            logger.info(
                "Generation failed — staging preserved for resume: %s", staging_dir,
            )
            await log_mgr.emit(
                project_id,
                "Generation failed but staging is preserved. Click Generate again to resume from checkpoint.",
                level="warning",
            )

        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            await registry.update_status(
                project_id, "error", error_message=result.get("error") or "Doc generation failed"
            )
            # Keep existing doc_count — old docs are still on disk
            await registry.complete_generate_run(
                run_id,
                status="failed",
                cost_usd=result.get("cost_usd"),
                duration_ms=result.get("duration_ms"),
                duration_api_ms=result.get("duration_api_ms"),
                input_tokens=result.get("input_tokens"),
                output_tokens=result.get("output_tokens"),
                num_turns=result.get("num_turns"),
                session_id=result.get("session_id"),
                doc_count=0,
                model_name=result.get("model_name"),
                write_model_name=result.get("write_model_name"),
                error_message=result.get("error") or "Doc generation failed",
                log_file=log_file,
                run_verdict="failed",
                gate_results=_load_enterprise_gate_results(staging_dir) if staging_dir.exists() else None,
            )
            await db.commit()

    # Signal SSE subscribers that generation is done
    _currently_generating.discard(project_id)
    _generation_tasks.pop(project_id, None)
    await log_mgr.emit_done(project_id)


async def _sync_internal_layers_for_locations(
    project_id: str, locations: list, log_mgr
) -> dict:
    """Replace a project's internal RAG layers across all its doc trees.

    Dispatches on layout because the two cases need different routines: a flat
    project uses ``RagBridge.sync_internal_layers`` unchanged, while a
    partitioned one must delete the internal set ONCE and then push every
    sub-project with a ``{subproject}/`` prefix -- calling the single-tree
    version per partition would wipe each previous partition's layers.
    """
    from mcp_server.services.doc_layout import project_output_root
    from mcp_server.services.partition_orchestrator import (
        sync_internal_layers_for_subprojects,
    )

    bridge = RagBridge(log_manager=log_mgr)
    if len(locations) == 1 and locations[0].subproject is None:
        return await bridge.sync_internal_layers(project_id, locations[0].docs_dir)

    return await sync_internal_layers_for_subprojects(
        project_id,
        project_output_root(project_id),
        [(location.subproject, None) for location in locations],
        log_mgr,
    )


@router.post("/push-to-rag", response_model=StatusResponse)
async def push_to_rag(req: PushToRagRequest, request: Request):
    """Push generated .md files from output dir to RAG pipeline."""
    await require_project_access(request, req.project_id)
    log_mgr = request.app.state.log_manager

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(req.project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {req.project_id}")

        from mcp_server.services.doc_layout import doc_locations, project_output_root

        locations = doc_locations(req.project_id)
        if not locations:
            raise HTTPException(
                status_code=404,
                detail=f"No docs found under {project_output_root(req.project_id)}",
            )

        bridge = RagBridge(log_manager=log_mgr)
        result: dict = {"pushed": 0, "skipped": 0, "errors": []}
        for location in locations:
            # Include any non-numbered docs (plugin pack outputs like
            # TABLE_INVENTORY.md), computed per tree -- packs may differ between
            # sub-projects.
            pack_docs = {
                f.name for f in location.docs_dir.glob("*.md")
                if not f.name[0].isdigit() and not f.name.startswith("_")
            }
            part = await bridge.push_docs(
                project_id=req.project_id,
                output_dir=str(location.docs_dir),
                extra_doc_names=pack_docs or None,
                # Sub-project docs are stored in RAG under a "{subproject}/"
                # prefix by partition_orchestrator._push_partition_docs; a manual
                # re-push must use the same names or it creates a duplicate,
                # unprefixed copy of every document instead of repairing one.
                filename_prefix=location.subproject,
                # Manual push is the operator recovery path: force a full re-embed
                # so any out-of-band vector-store drift is repaired regardless of
                # hashes.
                force=True,
            )
            result["pushed"] += part["pushed"]
            result["skipped"] += part["skipped"]
            result["errors"].extend(
                f"{location.subproject}/{e}" if location.subproject else e
                for e in part["errors"]
            )

        internal_sync = await _sync_internal_layers_for_locations(
            req.project_id, locations, log_mgr
        )
        total_errors = len(result["errors"]) + len(internal_sync["errors"])

        if (result["pushed"] > 0 or internal_sync["pushed"] > 0) and total_errors == 0:
            await registry.update_rag_push(req.project_id)

        await db.commit()

    return StatusResponse(
        status="completed",
        message=(
            f"Pushed {result['pushed']} docs and {internal_sync['pushed']} internal "
            f"artifacts ({internal_sync['aliases']} aliases) to RAG. Errors: {total_errors}"
        ),
    )


@router.get("/status/{project_id}", response_model=ScanStatusResponse)
async def get_status(project_id: str, request: Request):
    """Get project scan status."""
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        cache_age = None
        if project.last_scan_at:
            delta = datetime.datetime.now(datetime.timezone.utc) - project.last_scan_at
            cache_age = round(delta.total_seconds() / 60, 1)

        # Job-count delta: previous completed scan's job_count (if any).
        # Current job_count lives on project.job_count, which is the latest
        # completed scan. Find the one before that.
        previous_job_count: int | None = None
        job_delta: int | None = None
        from mcp_server.db.models import ScanRun  # noqa: WPS433 -- local import keeps startup cost down
        from sqlalchemy import select

        prev_rows = (
            await db.execute(
                select(ScanRun.job_count)
                .where(ScanRun.project_id == project_id)
                .where(ScanRun.status == "completed")
                .order_by(ScanRun.started_at.desc())
                .limit(2)
            )
        ).scalars().all()
        if len(prev_rows) >= 2:
            previous_job_count = int(prev_rows[1])
            job_delta = int(prev_rows[0]) - previous_job_count

        # Read warnings from the most recent scan_result.json, if present.
        warnings: list[dict] = []
        warnings_count = 0
        try:
            scan_result_path = config.intodoc_output_dir / project_id / "scan_result.json"
            if scan_result_path.is_file():
                data = json.loads(scan_result_path.read_text(encoding="utf-8"))
                raw_warnings = data.get("warnings") or []
                if isinstance(raw_warnings, list):
                    # Full count reflects reality; list is capped at 50 for payload size.
                    warnings_count = len(raw_warnings)
                    warnings = [w for w in raw_warnings if isinstance(w, dict)][:50]
        except (OSError, json.JSONDecodeError):
            # Never let a broken scan_result.json hide the rest of the status payload.
            warnings = []
            warnings_count = 0

        return ScanStatusResponse(
            project_id=project.id,
            status=project.status,
            last_scan_at=project.last_scan_at,
            cache_age_minutes=cache_age,
            job_count=project.job_count,
            doc_count=project.doc_count,
            error_message=project.error_message,
            previous_job_count=previous_job_count,
            job_delta=job_delta,
            warnings=warnings,
            warnings_count=warnings_count,
        )


async def _get_user_permission_codenames(db, user_id: int) -> set[str]:
    """Fetch permission codenames for a user via raw SQL (avoids importing RAG models)."""
    result = await db.execute(text("""
        SELECT DISTINCT p.codename
        FROM permissions p
        JOIN role_permissions rp ON rp.permission_id = p.id
        JOIN user_roles ur ON ur.role_id = rp.role_id
        WHERE ur.user_id = :uid
    """), {"uid": user_id})
    return {row[0] for row in result.fetchall()}


async def _get_user_is_superadmin(db, user_id: int) -> bool:
    result = await db.execute(
        text("SELECT is_superadmin FROM users WHERE id = :uid"),
        {"uid": user_id},
    )
    row = result.fetchone()
    return bool(row and row[0])


async def _get_assigned_project_ids(db, user_id: int) -> set[str]:
    result = await db.execute(
        text("SELECT project_id FROM project_assignments WHERE user_id = :uid"),
        {"uid": user_id},
    )
    return {row[0] for row in result.fetchall()}


@router.get("/projects", response_model=ProjectListResponse)
async def list_projects(request: Request):
    """List projects, filtered by assignment if user lacks view_all_projects."""
    auth_payload = getattr(request.state, "auth_payload", None)

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        all_projects = await registry.list_projects()

        # If no auth payload, return all (shouldn't happen due to require_auth)
        if not auth_payload:
            return ProjectListResponse(
                projects=[_project_to_response(p) for p in all_projects],
                total=len(all_projects),
            )

        user_id = int(auth_payload.get("sub", 0))
        is_superadmin = await _get_user_is_superadmin(db, user_id)

        if is_superadmin:
            return ProjectListResponse(
                projects=[_project_to_response(p) for p in all_projects],
                total=len(all_projects),
            )

        perms = await _get_user_permission_codenames(db, user_id)
        if "view_all_projects" in perms:
            return ProjectListResponse(
                projects=[_project_to_response(p) for p in all_projects],
                total=len(all_projects),
            )

        # Filter to assigned projects only
        assigned_ids = await _get_assigned_project_ids(db, user_id)
        filtered = [p for p in all_projects if p.id in assigned_ids]
        return ProjectListResponse(
            projects=[_project_to_response(p) for p in filtered],
            total=len(filtered),
        )


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: str, req: UpdateProjectRequest, request: Request):
    """Update editable project fields (display_name, platform)."""
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.update_project(
            project_id,
            display_name=req.display_name,
            platform=req.platform,
            doc_generation_provider=req.doc_generation_provider,
            instruction_pack_dir=req.instruction_pack_dir,
        )
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        await db.commit()
        await db.refresh(project)
        return _project_to_response(project)


@router.delete("/projects/{project_id}", response_model=DeleteProjectResponse)
async def delete_project(project_id: str, request: Request):
    """Permanently delete a project and all associated data.

    Cleans up: RAG documents/vectors (best-effort), filesystem artifacts, DB records.
    Blocked if project is currently scanning or generating.
    """
    await require_project_access(request, project_id)

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        if project.status in ("scanning", "generating"):
            raise HTTPException(
                status_code=409,
                detail=f"Cannot delete project while status is '{project.status}'. "
                       "Reset the status first or wait for the operation to complete.",
            )

        # 1. RAG cleanup (best-effort)
        rag_cleaned = False
        rag_error = None
        try:
            bridge = RagBridge()
            result = await bridge.delete_project_documents(project_id)
            rag_cleaned = True
            logger.info("RAG cleanup for %s: %s", project_id, result)
        except Exception as e:
            rag_error = str(e)
            logger.warning("RAG cleanup failed for %s (continuing): %s", project_id, e)

        # 2. Filesystem cleanup
        files_cleaned = True
        dirs_to_remove = [
            config.repos_dir / project_id,
            config.intodoc_output_dir / project_id,
            config.project_root / "output" / project_id,
        ]
        for d in dirs_to_remove:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
                logger.info("Removed directory: %s", d)

        # Remove log files matching {project_id}_*.log
        for log_file in config.logs_dir.glob(f"{project_id}_*.log"):
            try:
                log_file.unlink()
                logger.info("Removed log file: %s", log_file)
            except OSError as e:
                logger.warning("Failed to remove log file %s: %s", log_file, e)
                files_cleaned = False

        # 3. DB cleanup (cascade handles scan_runs, generate_runs)
        deleted = await registry.delete_project(project_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        await db.commit()

    logger.info("Deleted project %s (rag=%s, files=%s)", project_id, rag_cleaned, files_cleaned)
    return DeleteProjectResponse(
        status="deleted",
        message=f"Project '{project_id}' has been permanently deleted.",
        details={
            "rag_cleaned": rag_cleaned,
            "files_cleaned": files_cleaned,
            "rag_error": rag_error,
        },
    )


@router.get("/projects/{project_id}/usage", response_model=ProjectUsageResponse)
async def get_project_usage(project_id: str, request: Request):
    """Get aggregated usage stats (tokens, cost, run counts) for a project."""
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        # Aggregate generate runs
        gen_result = await db.execute(text("""
            SELECT
                COUNT(*)::int AS total,
                COUNT(*) FILTER (WHERE status = 'completed')::int AS completed,
                COALESCE(SUM(input_tokens), 0)::bigint AS input_tokens,
                COALESCE(SUM(output_tokens), 0)::bigint AS output_tokens,
                COALESCE(SUM(cost_usd), 0.0) AS cost_usd
            FROM generate_runs
            WHERE project_id = :pid
        """), {"pid": project_id})
        gen_row = gen_result.fetchone()

        # Aggregate scan runs
        scan_result = await db.execute(text("""
            SELECT
                COUNT(*)::int AS total,
                COUNT(*) FILTER (WHERE status = 'completed')::int AS completed
            FROM scan_runs
            WHERE project_id = :pid
        """), {"pid": project_id})
        scan_row = scan_result.fetchone()

        return ProjectUsageResponse(
            project_id=project_id,
            total_generate_runs=gen_row[0] if gen_row else 0,
            completed_generate_runs=gen_row[1] if gen_row else 0,
            total_input_tokens=int(gen_row[2]) if gen_row else 0,
            total_output_tokens=int(gen_row[3]) if gen_row else 0,
            total_cost_usd=float(gen_row[4]) if gen_row else 0.0,
            total_scan_runs=scan_row[0] if scan_row else 0,
            completed_scan_runs=scan_row[1] if scan_row else 0,
        )


@router.get("/scan-runs/{project_id}", response_model=ScanRunListResponse)
async def list_scan_runs(project_id: str, request: Request):
    """List all scan runs for a project (newest first)."""
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        runs = await registry.list_scan_runs(project_id)
        return ScanRunListResponse(
            runs=[
                ScanRunResponse(
                    id=r.id,
                    project_id=r.project_id,
                    triggered_by=r.triggered_by,
                    status=r.status,
                    started_at=r.started_at,
                    completed_at=r.completed_at,
                    job_count=r.job_count,
                    log_file=r.log_file,
                )
                for r in runs
            ],
            total=len(runs),
        )


@router.get("/projects/{project_id}/docs/{filename}/content")
async def get_doc_content(
    project_id: str,
    filename: str,
    request: Request,
    source: str = Query("published", pattern="^(published|staging)$"),
    subproject: str | None = Query(
        None,
        description=(
            "Sub-project the document belongs to. Required for a partitioned "
            "project; omit it for a flat project."
        ),
    ),
):
    """Return raw markdown content for inline preview."""
    from mcp_server.services.doc_layout import is_safe_path_segment, resolve_doc_location

    await require_project_access(request, project_id)
    if not filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="Only .md files can be previewed")
    if not is_safe_path_segment(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    location = resolve_doc_location(project_id, subproject, source=source)
    if location is None:
        label = f"{subproject}/{filename}" if subproject else filename
        raise HTTPException(status_code=404, detail=f"File not found: {label}")

    file_path = location.docs_dir / filename
    if not file_path.exists():
        label = f"{subproject}/{filename}" if subproject else filename
        raise HTTPException(status_code=404, detail=f"File not found: {label}")

    content = file_path.read_text(encoding="utf-8", errors="replace")
    return {"filename": filename, "subproject": location.subproject, "content": content}


@router.post(
    "/projects/{project_id}/docs/{filename}/chat-patch/preview",
    response_model=ChatDocPatchPreviewResponse,
)
async def preview_chat_doc_patch(
    project_id: str,
    filename: str,
    req: ChatDocPatchPreviewRequest,
    request: Request,
):
    """Freeze an exact, reviewable chat patch without changing docs or RAG."""
    await require_project_access(request, project_id)
    if not config.living_doc_patching_enabled:
        raise HTTPException(status_code=409, detail="Living-document patching is disabled")
    auth_payload = getattr(request.state, "auth_payload", None) or {}
    user_id = int(auth_payload.get("sub", 0))
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        proposal = await ChatPatchService(
            log_manager=getattr(request.app.state, "log_manager", None)
        ).preview(
            project_id=project_id,
            filename=filename,
            section=req.section,
            replacement_text=req.replacement_text,
            evidence_refs=req.evidence_refs,
            assertion_kind=req.assertion_kind,
            requested_by_user_id=user_id,
        )
    except EvidenceDocUpdateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.safe_message) from exc
    return ChatDocPatchPreviewResponse(
        proposal_id=proposal.proposal_id,
        project_id=proposal.project_id,
        filename=proposal.filename,
        section=proposal.section,
        assertion_kind=proposal.assertion_kind,
        base_document_sha256=f"sha256:{proposal.base_document_sha256}",
        base_knowledge_revision_id=proposal.base_knowledge_revision_id,
        evidence_refs=list(proposal.evidence_refs),
        patch_plan_hash=proposal.patch_plan_hash,
        compact_diff=proposal.compact_diff,
        changed_scope=list(proposal.changed_scope),
        estimated_input_tokens=proposal.estimated_input_tokens,
        estimated_output_tokens=proposal.estimated_output_tokens,
        expires_at=proposal.expires_at,
        requires_owner_approval=proposal.assertion_kind == "editorial",
    )


@router.post(
    "/projects/{project_id}/docs/{filename}/chat-patch/activate",
    response_model=ChatDocPatchActivateResponse,
)
async def activate_chat_doc_patch(
    project_id: str,
    filename: str,
    req: ChatDocPatchActivateRequest,
    request: Request,
):
    """Consume one exact approval and activate its candidate revision."""
    await require_project_access(request, project_id)
    if not config.living_doc_patching_enabled:
        raise HTTPException(status_code=409, detail="Living-document patching is disabled")
    auth_payload = getattr(request.state, "auth_payload", None) or {}
    user_id = int(auth_payload.get("sub", 0))
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    # Activation is the content-writing authority boundary. The RAG workflow
    # also owner-binds its interrupt, but MCP must fail closed when this endpoint
    # is called directly or when role information is ambiguous.
    await require_project_management(request, project_id)
    service = ChatPatchService(log_manager=getattr(request.app.state, "log_manager", None))
    try:
        proposal = service.get_proposal(req.proposal_id)
        if proposal.project_id != project_id or proposal.filename != filename:
            raise EvidenceDocUpdateError(404, "Documentation patch proposal not found.")
        result = await service.activate(
            proposal_id=req.proposal_id,
            expected_patch_plan_hash=req.patch_plan_hash,
            approver_user_id=user_id,
            editorial_approval_authorized=proposal.assertion_kind == "editorial",
        )
    except EvidenceDocUpdateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.safe_message) from exc
    return ChatDocPatchActivateResponse(
        proposal_id=result.proposal_id,
        candidate_id=result.candidate_id,
        knowledge_revision_id=result.knowledge_revision_id,
        filename=result.filename,
        before_sha=result.before_sha,
        after_sha=result.after_sha,
        assertion_kind=result.assertion_kind,
        changed_logical_ids=list(result.changed_logical_ids),
        embedded_content_count=result.embedded_content_count,
        reused_content_count=result.reused_content_count,
    )


@router.post(
    "/projects/{project_id}/docs/{filename}/evidence-update",
    response_model=EvidenceDocUpdateResponse,
)
async def update_doc_from_evidence(
    project_id: str,
    filename: str,
    req: EvidenceDocUpdateRequest,
    request: Request,
):
    """Append explicit harness evidence to one published markdown doc and re-sync RAG."""
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    svc = EvidenceDocUpdateService(log_manager=getattr(request.app.state, "log_manager", None))
    try:
        result = await svc.update_from_evidence(
            project_id=project_id,
            filename=filename,
            evidence_text=req.evidence_text,
            evidence_refs=req.evidence_refs,
            workflow_run_id=req.workflow_run_id,
            section=req.section,
            expected_base_sha=req.expected_base_sha,
        )
    except EvidenceDocUpdateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.safe_message) from exc
    return EvidenceDocUpdateResponse(**result.to_dict())


@router.post(
    "/projects/{project_id}/docs/{filename}/evidence-revert",
    response_model=EvidenceDocRevertResponse,
)
async def revert_doc_evidence_update(
    project_id: str,
    filename: str,
    req: EvidenceDocRevertRequest,
    request: Request,
):
    """Restore a prior evidence-update version and re-sync it to RAG."""
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    svc = EvidenceDocRevertService(log_manager=getattr(request.app.state, "log_manager", None))
    try:
        result = await svc.revert_update(
            project_id=project_id,
            filename=filename,
            receipt_path=req.receipt_path,
            expected_base_sha=req.expected_base_sha,
            workflow_run_id=req.workflow_run_id,
        )
    except EvidenceDocUpdateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.safe_message) from exc
    return EvidenceDocRevertResponse(**result.to_dict())


@router.post("/reset-status", response_model=StatusResponse)
async def reset_status(req: ResetStatusRequest, request: Request):
    """Reset a stuck generating/scanning project back to error state."""
    await require_project_access(request, req.project_id)
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(req.project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {req.project_id}")

        if project.status not in ("generating", "scanning"):
            raise HTTPException(
                status_code=400,
                detail=f"Project status is '{project.status}', not generating/scanning. Nothing to reset.",
            )

        was_generating = project.status == "generating"
        cancelled_live_task = False
        if was_generating:
            task = _generation_tasks.pop(req.project_id, None)
            if task is not None and not task.done():
                task.cancel()
                cancelled_live_task = True
            _currently_generating.discard(req.project_id)

        await registry.update_status(
            req.project_id,
            "error",
            error_message="Generation cancelled by user" if was_generating else "Manually reset by user",
        )

        # Mark any running GenerateRun/ScanRun records as failed
        from sqlalchemy import update
        from mcp_server.db.models import GenerateRun, ScanRun

        now = datetime.datetime.now(datetime.timezone.utc)
        await db.execute(
            update(GenerateRun)
            .where(GenerateRun.project_id == req.project_id, GenerateRun.status == "running")
            .values(
                status="failed",
                completed_at=now,
                error_message="Generation cancelled by user" if was_generating else "Manually reset by user",
                run_verdict="failed",
            )
        )
        await db.execute(
            update(ScanRun)
            .where(ScanRun.project_id == req.project_id, ScanRun.status == "running")
            .values(status="failed", completed_at=now)
        )

        await db.commit()

    if cancelled_live_task:
        log_mgr = request.app.state.log_manager
        await log_mgr.emit(req.project_id, "Generation cancellation requested. Stopping active task.", level="warning")
        await log_mgr.emit_done(req.project_id)
        return StatusResponse(status="cancelled", message=f"Generation for '{req.project_id}' was cancelled.")
    return StatusResponse(status="reset", message=f"Project '{req.project_id}' status reset to error.")


def _run_to_response(r) -> GenerateRunResponse:
    return GenerateRunResponse(
        id=r.id,
        project_id=r.project_id,
        triggered_by=r.triggered_by,
        partition_name=getattr(r, "partition_name", None),
        status=r.status,
        started_at=r.started_at,
        completed_at=r.completed_at,
        cost_usd=r.cost_usd,
        duration_ms=r.duration_ms,
        duration_api_ms=r.duration_api_ms,
        input_tokens=r.input_tokens,
        output_tokens=r.output_tokens,
        num_turns=r.num_turns,
        session_id=r.session_id,
        doc_count=r.doc_count,
        error_message=r.error_message,
        log_file=r.log_file,
        model_name=getattr(r, "model_name", None),
        write_model_name=getattr(r, "write_model_name", None),
        quality_score=getattr(r, "quality_score", None),
        auto_verdict=getattr(r, "auto_verdict", None),
        auto_baseline_run_id=getattr(r, "auto_baseline_run_id", None),
        run_verdict=getattr(r, "run_verdict", None),
        gate_results=getattr(r, "gate_results", None),
    )


@router.get("/generate-runs/{project_id}", response_model=GenerateRunListResponse)
async def list_generate_runs(project_id: str, request: Request):
    """List all generation runs for a project (newest first)."""
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        runs = await registry.list_generate_runs(project_id)
        return GenerateRunListResponse(
            runs=[_run_to_response(r) for r in runs],
            total=len(runs),
        )


@router.get("/generate-run-detail/{project_id}/{run_id}")
async def get_generate_run(project_id: str, run_id: int, request: Request):
    """Return a single generation run with per-doc quality breakdown from run_metrics.json."""
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        runs = await registry.list_generate_runs(project_id)
        run = next((r for r in runs if r.id == run_id), None)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found for project {project_id}")

    base = _run_to_response(run).model_dump()

    metrics_path = config.project_root / "output" / project_id / "logs" / f"run_metrics_{run_id}.json"
    if metrics_path.exists():
        try:
            base["run_metrics"] = json.loads(metrics_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    return base


@router.get("/compare-runs/{project_id}")
async def compare_generate_runs(
    project_id: str,
    run_a: int,
    run_b: int,
    request: Request,
    quality_drop_pct: float = 5.0,
    size_change_pct: float = 10.0,
):
    """Side-by-side comparison of two generation runs by quality, cost, and token usage."""
    await require_project_access(request, project_id)
    from mcp_server.api.routes.generate_runs import build_run_comparison

    return await build_run_comparison(
        project_id,
        run_a,
        run_b,
        quality_drop_pct=quality_drop_pct,
    )


_QUALITY_REGRESSION_THRESHOLD_PCT = 5.0
_COST_REGRESSION_THRESHOLD_PCT = 20.0
_DURATION_REGRESSION_THRESHOLD_PCT = 20.0


@router.get("/projects/{project_id}/quality-history")
async def get_quality_history(project_id: str, request: Request):
    """Return time-series quality/cost/duration metrics for all completed runs of a project."""
    await require_project_access(request, project_id)
    metrics_dir = config.project_root / "output" / project_id / "logs"
    entries = []
    if metrics_dir.exists():
        for path in sorted(metrics_dir.glob("run_metrics_*.json")):
            try:
                m = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            entries.append({
                "run_id": m.get("run_id"),
                "started_at": m.get("started_at"),
                "analysis_model": m.get("analysis_model"),
                "write_model": m.get("write_model"),
                "quality": (m.get("doc_quality") or {}).get("overall"),
                "cost_usd": m.get("cost_usd"),
                "duration_ms": m.get("duration_ms"),
                "regression": False,
                "quality_delta_pct": None,
                "cost_regression": False,
                "cost_delta_pct": None,
                "duration_regression": False,
                "duration_delta_pct": None,
            })

    for i, e in enumerate(entries):
        if i == 0:
            continue
        prev = entries[i - 1]

        prev_q = prev["quality"]
        curr_q = e["quality"]
        if prev_q is not None and curr_q is not None and prev_q > 0:
            drop_pct = ((prev_q - curr_q) / prev_q) * 100
            e["regression"] = drop_pct > _QUALITY_REGRESSION_THRESHOLD_PCT
            e["quality_delta_pct"] = round(-drop_pct, 2)

        prev_c = prev["cost_usd"]
        curr_c = e["cost_usd"]
        if prev_c is not None and curr_c is not None and prev_c > 0:
            cost_change_pct = ((curr_c - prev_c) / prev_c) * 100
            e["cost_regression"] = cost_change_pct > _COST_REGRESSION_THRESHOLD_PCT
            e["cost_delta_pct"] = round(cost_change_pct, 2)

        prev_d = prev["duration_ms"]
        curr_d = e["duration_ms"]
        if prev_d is not None and curr_d is not None and prev_d > 0:
            dur_change_pct = ((curr_d - prev_d) / prev_d) * 100
            e["duration_regression"] = dur_change_pct > _DURATION_REGRESSION_THRESHOLD_PCT
            e["duration_delta_pct"] = round(dur_change_pct, 2)

    return {"project_id": project_id, "history": entries, "total": len(entries)}


# --- Validation (US-105) ---

@router.get("/projects/{project_id}/questionnaire")
async def get_questionnaire(project_id: str, request: Request, run_id: int = 0):
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
    pack_dir = getattr(project, "instruction_pack_dir", None) if project else None
    return validation_service.generate_questionnaire(project_id, run_id, instruction_pack_dir=pack_dir)


@router.post("/projects/{project_id}/reviews", status_code=201)
async def submit_review(project_id: str, review: DocReview, request: Request):
    await require_project_access(request, project_id)
    if not (1 <= review.overall <= 10):
        raise HTTPException(status_code=422, detail="overall must be 1–10")
    for q in review.questions:
        if not (1 <= q.score <= 5):
            raise HTTPException(status_code=422, detail=f"score for '{q.id}' must be 1–5")
    output_root = config.project_root / "output"
    path = validation_service.save_review(output_root, project_id, review)
    return {"status": "saved", "path": str(path)}


@router.get("/projects/{project_id}/reviews", response_model=ReviewAggregateResponse)
async def get_reviews(project_id: str, request: Request, run_id: int | None = None):
    await require_project_access(request, project_id)
    output_root = config.project_root / "output"
    return validation_service.aggregate_reviews(output_root, project_id, run_id)


@router.get("/instruction-pack")
async def get_instruction_pack_metadata():
    """Return active instruction pack metadata for dashboard visibility."""
    pack_dir = config.project_root / config.instruction_pack_dir
    pack = PackLoader(pack_dir).load()
    return {
        "path": str(pack.path),
        "relative_path": config.instruction_pack_dir,
        "version": pack.version,
        "exists": pack.path.exists(),
        "is_local": pack.path.is_dir(),
        "supported": pack.path.exists() and pack.path.is_dir(),
        "preambles": [
            {
                "key": key,
                "bytes": len(value.encode("utf-8")),
                "chars": len(value),
            }
            for key, value in sorted(pack.preambles.items())
        ],
    }


@router.get("/logs/{project_id}", response_model=LogFileListResponse)
async def list_log_files(project_id: str, request: Request):
    """List available log files for a project."""
    await require_project_access(request, project_id)
    logs_dir = config.logs_dir
    files: list[LogFileResponse] = []
    for log_path in sorted(logs_dir.glob(f"{project_id}_*.log"), reverse=True):
        # Extract timestamp from filename: {project_id}_{YYYYMMDD_HHMMSS}.log
        name = log_path.stem  # e.g. "myproject_20260224_143000"
        ts_part = name[len(project_id) + 1:]  # e.g. "20260224_143000"
        files.append(LogFileResponse(
            filename=log_path.name,
            project_id=project_id,
            timestamp=ts_part,
            size_bytes=log_path.stat().st_size,
        ))
    return LogFileListResponse(files=files, total=len(files))


@router.get("/logs/{project_id}/{filename}")
async def get_log_content(project_id: str, filename: str, request: Request):
    """Return the content of a specific log file."""
    await require_project_access(request, project_id)
    # Sanitize filename to prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    log_path = config.logs_dir / filename
    if not log_path.exists() or not filename.startswith(project_id):
        raise HTTPException(status_code=404, detail="Log file not found")
    content = log_path.read_text(encoding="utf-8", errors="replace")
    return {"filename": filename, "content": content}


# --- Documentation (PDF) ---


@router.get("/projects/{project_id}/docs", response_model=DocListResponse)
async def list_project_docs(
    project_id: str,
    request: Request,
    source: str = Query("published", pattern="^(published|staging)$"),
):
    """List all documentation files for a project with metadata."""
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    if source == "published":
        from mcp_server.services.pdf_service import PdfService

        svc = PdfService()
        docs = svc.list_docs(project_id)
    else:
        from mcp_server.services.doc_layout import STAGING, doc_locations
        from mcp_server.services.doc_quality_scorer import is_published_doc_filename

        docs = []
        for location in doc_locations(project_id, source=STAGING):
            for md_file in sorted(location.docs_dir.glob("*.md")):
                if not is_published_doc_filename(md_file.name):
                    continue
                stat = md_file.stat()
                docs.append({
                    "name": md_file.name,
                    "subproject": location.subproject,
                    "type": "md",
                    "size_bytes": stat.st_size,
                    "has_pdf": False,
                    "modified_at": datetime.datetime.fromtimestamp(
                        stat.st_mtime,
                        tz=datetime.timezone.utc,
                    ).isoformat(),
                })

    # Preserve first-seen order (the listing order of doc_locations) rather than
    # re-sorting: partitions are already returned in stable display order.
    subprojects = list(
        dict.fromkeys(d["subproject"] for d in docs if d.get("subproject"))
    )
    return DocListResponse(
        project_id=project_id,
        docs=[DocFileInfo(**d) for d in docs],
        total=len(docs),
        subprojects=subprojects,
    )


@router.get("/projects/{project_id}/docs/{filename}")
async def download_doc(
    project_id: str,
    filename: str,
    request: Request,
    subproject: str | None = Query(
        None,
        description=(
            "Sub-project the document belongs to. Required for a partitioned "
            "project, where every sub-project emits the same document names; "
            "omit it for a flat project."
        ),
    ),
):
    """Download a specific doc file (md or pdf)."""
    await require_project_access(request, project_id)
    # Validate extension
    if not filename.endswith((".md", ".pdf")):
        raise HTTPException(status_code=400, detail="Only .md and .pdf files are supported")

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    from mcp_server.services.pdf_service import PdfService

    svc = PdfService()
    path = svc.get_doc_path(project_id, filename, subproject=subproject)
    if not path:
        label = f"{subproject}/{filename}" if subproject else filename
        raise HTTPException(status_code=404, detail=f"File not found: {label}")

    if filename.endswith(".md"):
        # Markdown documents are small enough to return atomically.  Serving them via
        # FileResponse made the response vulnerable to an upstream proxy cancelling a
        # streamed body after the Content-Length header had already been emitted.  The
        # browser then received a 200 response with a short body and surfaced it as an
        # Internal Server Error.  PDFs remain streamed below.
        return Response(
            content=path.read_bytes(),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return FileResponse(
        path=str(path),
        media_type="application/pdf",
        filename=filename,
    )


@router.post("/projects/{project_id}/generate-pdfs", response_model=PdfGenerateResponse)
async def generate_pdfs(project_id: str, request: Request):
    """Generate PDF files for all markdown docs in a project."""
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    from mcp_server.services.pdf_service import PdfService

    svc = PdfService()
    result = svc.convert_project(project_id)

    status = "completed" if result["failed"] == 0 else "partial"
    if result["converted"] == 0 and result["failed"] > 0:
        status = "failed"

    return PdfGenerateResponse(
        project_id=project_id,
        status=status,
        converted=result["converted"],
        failed=result["failed"],
        errors=result["errors"],
    )


# --- Assessment ---


@router.post(
    "/projects/{project_id}/generate-assessment",
    response_model=AssessmentGenerateResponse,
)
async def generate_assessment(project_id: str, request: Request):
    """Generate a self-contained HTML assessment page from project docs."""
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    from mcp_server.services.assessment_service import AssessmentService

    svc = AssessmentService()
    result = await svc.generate(project_id)

    return AssessmentGenerateResponse(**result)


@router.get("/projects/{project_id}/assessment")
async def get_assessment(project_id: str, request: Request):
    """Serve the generated assessment HTML page."""
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    html_path = config.project_root / "output" / project_id / "assessment" / "index.html"
    if not html_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Assessment not generated yet. Call POST /generate-assessment first.",
        )

    return FileResponse(
        path=str(html_path),
        media_type="text/html",
    )


# --- Project Graph (Topology) ---


def _derive_systems_from_job_json(job_data: dict, platform: str) -> list[str]:
    """Derive external systems from per-job JSON at request time.

    Used for old scans that predate the job_external_systems manifest field.
    """
    from mcp_server.platforms import get_platform_strategy

    try:
        return get_platform_strategy(platform).derive_external_systems(job_data)
    except ValueError:
        return []


def _component_flow_direction(job_data: dict, component_name: str) -> str:
    """Backfill Route endpoint direction for scans created before this fact."""
    connections = job_data.get("connections", [])
    has_incoming = any(
        connection.get("target") == component_name
        for connection in connections
        if isinstance(connection, dict)
    )
    has_outgoing = any(
        connection.get("source") == component_name
        for connection in connections
        if isinstance(connection, dict)
    )
    if has_outgoing and not has_incoming:
        return "input"
    if has_incoming and not has_outgoing:
        return "output"
    if has_incoming and has_outgoing:
        return "both"
    return "unknown"


def _component_resource_direction(component_type: str) -> str:
    """Backfill repository-resource access from a Talend component name."""
    normalized = component_type.lower()
    if "input" in normalized and "output" in normalized:
        return "both"
    if "input" in normalized:
        return "input"
    if "output" in normalized:
        return "output"
    return "unknown"


def _derive_direction(path: str, job_name: str) -> str:
    """Derive data-flow direction from job path or naming conventions."""
    path_lower = path.lower().replace("\\", "/")
    # Path-based: Account/In/... or Account/Out/...
    if "/in/" in path_lower:
        return "OnRamp"
    if "/out/" in path_lower:
        return "OffRamp"
    # Name-based conventions
    name_lower = job_name.lower()
    is_on = "onramp" in name_lower or "_onramp_" in name_lower
    is_off = (
        "offramp" in name_lower
        or "_offramp_" in name_lower
        or "_return_" in name_lower
    )
    if is_on and is_off:
        return "Bidirectional"
    if is_on:
        return "OnRamp"
    if is_off:
        return "OffRamp"
    return ""


def _derive_node_summary(
    job_name: str,
    component_types: list[str],
    external_systems: list[str],
    direction: str,
) -> str:
    """Lightweight summary for graph nodes when no cached AI summary exists.

    Delegates to ``summary_service.heuristic_summary`` so that the topology
    fallback is shared between the request-time graph endpoint and the
    async background extractor. Returns ``""`` when the available signal is
    too thin to say anything meaningful — the JobDetailPanel then renders an
    empty state instead of a repetitive template sentence.
    """
    from mcp_server.services.summary_service import heuristic_summary
    return heuristic_summary(job_name, direction, external_systems, component_types)


_DOC_SUMMARY_HEADINGS = {
    "data flow", "data flows", "lineage", "architecture", "inventory",
    "business logic", "business rules", "overview", "purpose",
}

_MD_NOISE_RE = re.compile(r"\*\*|^\|.*\|$|^[-=]+$|^#+\s*", re.MULTILINE)


def _derive_job_summary(
    job_name: str,
    doc_sections: list[JobDocSection],
    component_types: list[str],
    external_systems: list[str],
) -> str:
    """Rich summary from doc sections for the detail panel.

    Returns ``""`` if no paragraph looks like real purpose prose. This avoids
    dumping metadata blobs (Module / Functions / Entry point bullets from
    02_COMPONENT_INVENTORY.md) into the topology panel.
    """
    for section in doc_sections:
        heading_lower = section.heading.lower()
        if not any(kw in heading_lower for kw in _DOC_SUMMARY_HEADINGS):
            continue
        paragraphs = section.content.split("\n\n")
        for para in paragraphs:
            if job_name not in para and job_name.lower() not in para.lower():
                continue
            clean = _MD_NOISE_RE.sub("", para).strip()
            clean = re.sub(r"\s+", " ", clean)
            if len(clean) < 20:
                continue
            # Reject metadata-dump paragraphs (e.g. component entries that
            # only enumerate Module / Functions / Entry point / Dependencies).
            # These are not purpose prose; they describe structure.
            if re.search(r"\bModule\b.*\bEntry point\b", clean):
                continue
            if re.search(r"\bFunctions?\b.*\bDependencies\b", clean):
                continue
            # The job name should appear early in the cleaned text (first
            # ~80 chars) — that's a strong signal the paragraph is about
            # the job, not a table that happens to mention it.
            head = clean[:80].lower()
            if job_name.lower() not in head:
                continue
            # Take first 2-3 sentences
            sentences = re.split(r"(?<=[.!?])\s+", clean)
            candidate = " ".join(sentences[:3]).strip()
            if len(candidate) < 20 or len(candidate) > 400:
                continue
            return candidate

    # Final fallback: synthesize a one-liner from scan signals so the panel
    # shows *something* rather than dumping a metadata blob.
    from mcp_server.services.summary_service import heuristic_summary
    direction = "OnRamp" if any(
        s.lower() in {"crm", "commerce", "http api", "geocoding"} for s in external_systems
    ) else ""
    return heuristic_summary(job_name, direction, external_systems, component_types)


def _project_doc_dirs(project_id: str) -> list[Path]:
    """Every documentation directory to search for a project, published first.

    Readers that look a job up in the generated docs must cover all of them: a
    partitioned project keeps one tree per sub-project, so a single
    ``output/{pid}/docs`` lookup finds nothing at all. Falls back to the staging
    trees when nothing is published yet, preserving the previous
    ``docs`` -> ``docs_staging`` behaviour.
    """
    from mcp_server.services.doc_layout import PUBLISHED, STAGING, doc_locations

    locations = doc_locations(project_id, source=PUBLISHED) or doc_locations(
        project_id, source=STAGING
    )
    return [location.docs_dir for location in locations]


# Matches a "**Purpose**: ..." line and captures content up to the next blank
# line or the next bold field (e.g. "**Component Count**:").
_PURPOSE_LINE_RE = re.compile(
    r"^\s*\*\*Purpose\*?\*?\s*:?\s*(.+?)(?=\n\s*\n|\n\s*\*\*|\Z)",
    re.MULTILINE | re.DOTALL,
)


def _extract_job_purpose_from_inventory(
    project_id: str, job_name: str
) -> str | None:
    """Return the verbatim ``**Purpose**`` line for a job from
    ``02_COMPONENT_INVENTORY.md``, or ``None`` if not found.

    Per-job descriptions live in the inventory doc as
    ``#### {job_name}`` followed by a ``**Purpose**: ...`` paragraph.
    Substring-grepping paragraphs misses this because the Purpose line
    does not repeat the job name. We must parse the heading boundary.
    """
    # A partitioned project has one inventory per sub-project and the job lives
    # in exactly one of them; scan them in display order and take the first hit.
    content = ""
    for docs_dir in _project_doc_dirs(project_id):
        inventory_path = docs_dir / "02_COMPONENT_INVENTORY.md"
        if not inventory_path.exists():
            continue
        try:
            candidate = inventory_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if re.search(rf"^#{{2,4}}\s*{re.escape(job_name)}\s*$", candidate, re.MULTILINE):
            content = candidate
            break
    if not content:
        return None

    # Find the job's heading at any depth (####, ###, ##) and capture the
    # content from the heading up to the next sibling-or-higher heading.
    name_re = re.escape(job_name)
    section_re = re.compile(
        rf"^(#{{2,4}})\s*{name_re}\s*$(.*?)(?=^#{{1,4}}\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = section_re.search(content)
    if not match:
        return None
    body = match.group(2)

    purpose_match = _PURPOSE_LINE_RE.search(body)
    if not purpose_match:
        return None
    purpose = purpose_match.group(1).strip()
    purpose = re.sub(r"\s+", " ", purpose)
    # Keep the first 2-3 sentences max for brevity
    sentences = re.split(r"(?<=[.!?])\s+", purpose)
    return " ".join(sentences[:3]).strip() or None


def _extract_job_doc_sections(
    project_id: str, job_name: str
) -> list[JobDocSection]:
    """Return doc sections whose heading matches the job name exactly,
    bounded by the next sibling-or-higher heading.

    Replaces the previous ``## ``-only split which dragged in the entire
    inventory section for any job click.
    """
    docs_dirs = _project_doc_dirs(project_id)
    if not docs_dirs:
        return []

    name_re = re.escape(job_name)
    # Match a heading whose line is the job name (allowing trailing junk)
    section_re = re.compile(
        rf"^(#{{2,4}})\s*{name_re}\b(.*)$",
        re.MULTILINE,
    )
    any_heading_re = re.compile(r"^#{1,6}\s", re.MULTILINE)

    sections: list[JobDocSection] = []
    for md_path in [
        path for docs_dir in docs_dirs for path in sorted(docs_dir.glob("*.md"))
    ]:
        try:
            content = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for m in section_re.finditer(content):
            depth = len(m.group(1))
            heading_line = m.group(0).lstrip("#").strip()
            # Find the end: next heading at same depth or shallower
            tail = content[m.end():]
            end = len(tail)
            for h in any_heading_re.finditer(tail):
                hash_count = 0
                pos = h.start()
                while pos < len(tail) and tail[pos] == "#":
                    hash_count += 1
                    pos += 1
                if hash_count <= depth:
                    end = h.start()
                    break
            body = tail[:end].strip()
            if body:
                doc_label = md_path.stem.replace("_", " ").lstrip("0123456789 ")
                sections.append(
                    JobDocSection(
                        heading=f"{doc_label} - {heading_line}",
                        content=body,
                    )
                )

    return sections


@router.get("/projects/{project_id}/graph", response_model=ProjectGraphResponse)
async def get_project_graph(project_id: str, request: Request):
    """Return manifest + connections data for topology visualization.

    Reads external systems from manifest.job_external_systems (populated by
    the scanner). Falls back to runtime derivation for old scans.
    """
    await require_project_access(request, project_id)

    # Look up platform for fallback derivation on old scans
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
    platform = project.platform if project else "talend"

    scan_dir = config.intodoc_output_dir / project_id / "scan"
    manifest_path = scan_dir / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail=f"No scan data for project: {project_id}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Load connections.json
    connections: list[GraphConnectionInfo] = []
    connections_path = scan_dir / "connections.json"
    if connections_path.exists():
        raw = json.loads(connections_path.read_text(encoding="utf-8"))
        seen: set[str] = set()
        for c in raw:
            name = c.get("name", "")
            if name and name not in seen:
                seen.add(name)
                connections.append(GraphConnectionInfo(
                    name=name,
                    connection_type=c.get("connection_type", ""),
                    host=c.get("host", ""),
                    database=c.get("database", ""),
                ))

    # Check if docs exist for this project (any tree: flat or per sub-project)
    from mcp_server.services.doc_layout import doc_locations

    has_docs = any(
        any(location.docs_dir.glob("*.md"))
        for location in doc_locations(project_id)
    )

    # Read external systems from manifest (populated by scanner)
    job_external_systems: dict[str, list[str]] = manifest.get("job_external_systems", {})
    needs_runtime_derivation = not job_external_systems

    # Merge coarse external-system participation with precise repository and
    # Route-channel dependencies. A Talend Route can have both (for example a
    # REST endpoint feeding a named JMS destination); choosing only one loses
    # the architectural relationship the topology is meant to explain.
    job_connections_used: dict[str, list[str]] = manifest.get("job_connections_used", {})
    connection_used_by: dict[str, list[str]] = manifest.get("connection_used_by", {})

    # Load AI-generated summaries (empty dict if not yet generated)
    ai_summaries = SummaryService.load_summaries(project_id)

    job_groups: dict[str, str] = manifest.get("job_groups", {})
    artifact_channels = [
        channel
        for channel in manifest.get("artifact_channels", [])
        if isinstance(channel, dict)
    ]
    artifact_endpoints = [
        endpoint
        for endpoint in manifest.get("artifact_endpoints", [])
        if isinstance(endpoint, dict)
    ]
    artifact_resources = [
        resource
        for resource in manifest.get("artifact_resources", [])
        if isinstance(resource, dict)
    ]
    resource_keys = {
        (
            resource.get("artifact"),
            resource.get("component_name"),
            resource.get("resource"),
        )
        for resource in artifact_resources
    }
    jobs_dir = scan_dir / "jobs"
    job_metadata: dict[str, JobScanMetadata] = {}
    all_system_names: set[str] = set()

    for job_name, group in job_groups.items():
        ext_systems = job_external_systems.get(job_name, [])

        # Extract component types from per-job JSON
        comp_types: list[str] = []
        comp_count = 0
        purpose = ""
        author = ""
        modification_date = ""
        job_data: dict | None = None

        job_files: dict[str, str] = manifest.get("job_files", {}) or {}
        relative_job_file = job_files.get(job_name, "")
        job_path = scan_dir / relative_job_file if relative_job_file else jobs_dir / group / f"{job_name}.json"
        if job_path.exists():
            try:
                job_data = json.loads(job_path.read_text(encoding="utf-8"))
                purpose = job_data.get("purpose", "")
                author = job_data.get("author", "")
                modification_date = job_data.get("modification_date", "")
                components = job_data.get("components", [])
                comp_count = len(components)
                seen_types: set[str] = set()
                for comp in components:
                    ctype = comp.get("component_type", "")
                    if ctype and ctype not in seen_types:
                        seen_types.add(ctype)
                        comp_types.append(ctype)
            except (json.JSONDecodeError, OSError):
                pass

        # Backward-compatible topology enrichment. Existing scans already
        # retain component graphs, so direction can be recovered without
        # requiring the user to upload the ZIP again.
        if job_data:
            for channel in artifact_channels:
                if (
                    channel.get("artifact") == job_name
                    and not channel.get("direction")
                ):
                    channel["direction"] = _component_flow_direction(
                        job_data, str(channel.get("component_name", "")),
                    )
            for endpoint in artifact_endpoints:
                if (
                    endpoint.get("artifact") == job_name
                    and not endpoint.get("direction")
                ):
                    endpoint["direction"] = _component_flow_direction(
                        job_data, str(endpoint.get("component_name", "")),
                    )
            for component in job_data.get("components", []):
                if not isinstance(component, dict):
                    continue
                resource = component.get("metadata_connection_ref")
                if not resource:
                    continue
                resource_key = (
                    job_name,
                    component.get("unique_name"),
                    resource,
                )
                if resource_key in resource_keys:
                    continue
                artifact_resources.append({
                    "artifact": job_name,
                    "resource_type": "repository_connection",
                    "resource": resource,
                    "direction": _component_resource_direction(
                        str(component.get("component_type", "")),
                    ),
                    "component_name": component.get("unique_name", ""),
                    "provenance": component.get("component_type", ""),
                })
                resource_keys.add(resource_key)

        # Fallback: derive systems at request time for old scans
        if needs_runtime_derivation and not ext_systems and job_data:
            ext_systems = _derive_systems_from_job_json(job_data, platform)

        job_path_str = str(job_path) if job_path.exists() else ""
        direction = _derive_direction(job_path_str, job_name)
        # Prefer AI summary, fall back to heuristic
        node_summary = ai_summaries.get(job_name) or _derive_node_summary(
            job_name, comp_types, ext_systems, direction,
        )

        job_metadata[job_name] = JobScanMetadata(
            purpose=purpose,
            author=author,
            modification_date=modification_date,
            component_count=comp_count,
            component_types=comp_types,
            subgroup=group,
            has_docs=has_docs,
            external_systems=ext_systems,
            summary=node_summary,
            direction=direction,
            artifact_kind=(job_data or {}).get(
                "artifact_kind", manifest.get("artifact_kinds", {}).get(job_name, "job"),
            ),
            artifact_role=(job_data or {}).get("artifact_role", "standalone"),
        )

        # Add external-system edges without replacing precise Route channels.
        if ext_systems:
            existing = set(job_connections_used.get(job_name, []))
            existing.update(ext_systems)
            job_connections_used[job_name] = sorted(existing)
            for sys_name in ext_systems:
                all_system_names.add(sys_name)
                if sys_name not in connection_used_by:
                    connection_used_by[sys_name] = []
                if job_name not in connection_used_by[sys_name]:
                    connection_used_by[sys_name].append(job_name)

    # Publish the merged view and add synthetic connection metadata for coarse
    # systems. Named channel nodes remain self-describing in the manifest.
    manifest["job_connections_used"] = job_connections_used
    manifest["connection_used_by"] = connection_used_by
    manifest["artifact_channels"] = artifact_channels
    manifest["artifact_endpoints"] = artifact_endpoints
    manifest["artifact_resources"] = artifact_resources

    if all_system_names:
        existing_conn_names = {c.name for c in connections}
        for sys_name in sorted(all_system_names):
            if sys_name not in existing_conn_names:
                connections.append(GraphConnectionInfo(
                    name=sys_name,
                    connection_type="derived",
                ))

    artifact_kinds = manifest.get("artifact_kinds", {}) or {}
    artifact_counts = {
        "total_artifacts": len(job_groups),
        "job_count": sum(kind == "job" for kind in artifact_kinds.values()),
        "route_count": sum(kind == "route" for kind in artifact_kinds.values()),
        "joblet_count": sum(kind == "joblet" for kind in artifact_kinds.values()),
        "routelet_count": sum(kind == "routelet" for kind in artifact_kinds.values()),
    }
    # Historic manifests have no taxonomy. Their legacy designs remain Jobs
    # rather than appearing as an unexplained zero-count project.
    if not artifact_kinds:
        artifact_counts["job_count"] = len(job_groups)

    return ProjectGraphResponse(
        project_id=project_id,
        manifest=manifest,
        connections=connections,
        job_metadata=job_metadata,
        artifact_counts=artifact_counts,
    )


@router.get(
    "/projects/{project_id}/landscape",
    response_model=ProjectLandscapeResponse,
)
async def get_project_landscape(project_id: str, request: Request):
    """Return scanner facts plus the current validated business overlay.

    The endpoint is deliberately read-only. It never derives identities at
    request time: scanner facts come from ``scan/landscape.json`` and the
    optional business view comes from the atomically published documentation
    run. A missing, invalid, or stale overlay never hides the technical graph.
    """
    await require_project_access(request, project_id)

    landscape_path = (
        config.intodoc_output_dir / project_id / "scan" / "landscape.json"
    )
    if not landscape_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No landscape data for project: {project_id}",
        )

    try:
        payload = json.loads(landscape_path.read_text(encoding="utf-8"))
        # Older scans may still contain the retired deterministic semantic
        # projection. Never serve those filename-based proposals as business
        # truth; the agent-authored artifact below is the only semantic layer.
        payload.pop("semantic_topology", None)
        business_topology, topology_state, topology_evidence = _load_published_business_topology(
            project_id
        )
        payload["business_topology"] = business_topology
        payload["topology_evidence"] = topology_evidence
        payload["topology_state"] = topology_state
        return ProjectLandscapeResponse.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.error(
            "Invalid landscape artifact for project %s: %s",
            project_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail="Landscape data is invalid; rescan the project",
        ) from exc


def _load_published_business_topology(
    project_id: str,
) -> tuple[dict | None, dict, dict]:
    """Load only a validated topology for the exact current scan fingerprint."""

    topology_dir = (
        config.project_root / "output" / project_id / "docs" / "_topology"
    )
    topology_path = topology_dir / "business_topology.json"
    report_path = topology_dir / "validation_report.json"
    missing_state = {
        "available": False,
        "validated": False,
        "stale": False,
        "source": "none",
        "message": "Business topology has not been generated for this project.",
    }
    if not topology_path.is_file():
        # Composite uploads publish per-partition topologies under
        # output/{pid}/{subproject}/docs/_topology/ and have no upload-level
        # docs/ tree. Merge them into one org-level topology on request so the
        # landscape endpoint returns a valid object instead of null.
        composite = _load_composite_business_topology(project_id)
        if composite is not None:
            return composite
        return None, missing_state, {}

    invalid_state = {
        "available": True,
        "validated": False,
        "stale": False,
        "source": "documentation_topology_agent",
        "message": "The latest business topology did not pass validation.",
    }
    if not report_path.is_file():
        return None, invalid_state, {}

    try:
        topology = json.loads(topology_path.read_text(encoding="utf-8"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(topology, dict) or not isinstance(report, dict):
            return None, invalid_state, {}
        if report.get("validated") is not True:
            return None, invalid_state, {}

        from mcp_server.services.business_topology import (
            BusinessTopology,
            compute_scan_fingerprint,
            load_business_topology_evidence,
        )
        from mcp_server.services.business_topology.validation import (
            validate_business_topology,
        )

        # Treat the publication report as necessary but not sufficient: a
        # manually edited or partially copied artifact must still satisfy the
        # strict contract at read time.
        topology_model = BusinessTopology.model_validate(topology)

        current_fingerprint = compute_scan_fingerprint(
            config.intodoc_output_dir / project_id
        )
        topology_fingerprint = str(
            topology.get("scan_fingerprint")
            or (topology.get("metadata") or {}).get("scan_fingerprint")
            or ""
        )
        report_fingerprint = str(report.get("scan_fingerprint") or "")
        if report.get("project_id") != project_id:
            return None, invalid_state, {}
        if (
            not topology_fingerprint
            or topology_fingerprint != current_fingerprint
            or report_fingerprint != current_fingerprint
        ):
            return None, {
                "available": True,
                "validated": False,
                "stale": True,
                "source": "documentation_topology_agent",
                "message": (
                    "Business topology belongs to an older scan; regenerate "
                    "documentation to refresh it."
                ),
            }, {}

        bundle = load_business_topology_evidence(topology_dir)
        if bundle.scan_fingerprint != current_fingerprint:
            return None, invalid_state, {}
        # Curated, evidence-grounded overlays are authored from the merged
        # claim ledger and carry exact scanner artifact identities.  They are
        # schema-validated above, but may intentionally use approved business
        # evidence that is not present in the deterministic scanner ledger.
        # Keep the normal strict revalidation gate for generated topologies;
        # allow only this explicit overlay marker through so its L2 artifact
        # provenance is not discarded as an empty landscape fallback.
        if topology.get("metadata", {}).get("generated_by") != "analyst_evidence_grounded":
            supplied_coverage = dict(topology.get("coverage") or {})
            fresh_report = validate_business_topology(
                topology_model,
                bundle=bundle,
                project_id=project_id,
            )
            if not fresh_report.validated:
                return None, invalid_state, {}
            recalculated_coverage = topology_model.coverage.model_dump(mode="json")
            if any(
                recalculated_coverage.get(key) != value
                for key, value in supplied_coverage.items()
            ):
                return None, invalid_state, {}

        return topology, {
            "available": True,
            "validated": True,
            "stale": False,
            "source": "documentation_topology_agent",
            "message": "Validated business topology is available.",
        }, _load_topology_evidence_catalog(topology_dir, topology)
    except (OSError, json.JSONDecodeError, ValueError, FileNotFoundError) as exc:
        logger.warning(
            "Business topology unavailable for project %s: %s",
            project_id,
            type(exc).__name__,
        )
        return None, invalid_state, {}


def _load_composite_business_topology(
    project_id: str,
) -> tuple[dict | None, dict, dict] | None:
    """Build+serve the org-level topology for a composite (multi-ZIP) upload.

    Returns ``None`` when the project is not a composite upload (so the caller
    falls back to its ``missing`` state). Otherwise returns the standard
    ``(topology, state, evidence)`` triple. The merged object is deterministic
    (union of validated per-partition topologies + the cross-project index) and
    is persisted under ``output/{pid}/topology/`` so a later reload -- or an
    operator inspecting the artifact -- sees the same JSON that was served.
    """

    from mcp_server.services.business_topology import (
        build_composite_org_topology,
        compute_scan_fingerprint,
    )

    topology_dir = config.project_root / "output" / project_id / "topology"
    current_fingerprint = compute_scan_fingerprint(
        config.intodoc_output_dir / project_id
    )

    # Prefer an analyst-curated overlay when present and current. The curated
    # file is the deterministic merge with real system roles + named streams
    # written back in place (same schema, same scan_fingerprint). It exists only
    # for projects an analyst has enriched; everything else falls through to the
    # deterministic merge below.
    curated_path = topology_dir / "business_topology.curated.json"
    if curated_path.is_file():
        try:
            curated = json.loads(curated_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "Unreadable curated business topology for %s: %s", project_id, exc
            )
            curated = None
        if isinstance(curated, dict):
            curated_fp = str(curated.get("scan_fingerprint") or "")
            if curated_fp and curated_fp == current_fingerprint:
                return curated, {
                    "available": True,
                    "validated": True,
                    "stale": False,
                    "source": "analyst_curated",
                    "message": (
                        "Analyst-curated organization business topology is "
                        "available."
                    ),
                }, _composite_topology_evidence(
                    project_id, curated, topology_dir, current_fingerprint
                )
            logger.info(
                "Curated topology for %s ignored (fingerprint mismatch: "
                "curated=%s current=%s)",
                project_id,
                curated_fp[:12],
                current_fingerprint[:12],
            )

    try:
        topology_model = build_composite_org_topology(
            project_id, project_root=config.project_root
        )
    except Exception as exc:  # noqa: BLE001 - additive overlay must not 500 the graph
        logger.warning(
            "Composite business topology build failed for %s: %s",
            project_id,
            type(exc).__name__,
        )
        return None
    if topology_model is None:
        return None

    topology = topology_model.model_dump(mode="json")

    topology_fingerprint = str(topology.get("scan_fingerprint") or "")
    if not topology_fingerprint or topology_fingerprint != current_fingerprint:
        return None, {
            "available": True,
            "validated": False,
            "stale": True,
            "source": "composite_org_merge",
            "message": (
                "Organization topology belongs to an older scan; regenerate "
                "documentation to refresh it."
            ),
        }, {}

    # Persist the served artifact (best-effort; serving never depends on it).
    try:
        topology_dir.mkdir(parents=True, exist_ok=True)
        (topology_dir / "business_topology.json").write_text(
            json.dumps(topology, indent=2), encoding="utf-8"
        )
        (topology_dir / "validation_report.json").write_text(
            json.dumps(
                {
                    "validated": True,
                    "scan_fingerprint": current_fingerprint,
                    "project_id": project_id,
                    "errors": [],
                    "warnings": topology.get("coverage", {}).get("notes", []),
                    "generated_by": "composite_org_merge",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning(
            "Could not persist composite topology for %s: %s", project_id, exc
        )

    return topology, {
        "available": True,
        "validated": True,
        "stale": False,
        "source": "composite_org_merge",
        "message": "Organization-level business topology is available.",
    }, _composite_topology_evidence(
        project_id, topology, topology_dir, current_fingerprint
    )


def _composite_topology_evidence(
    project_id: str,
    topology: dict,
    topology_dir: Path,
    scan_fingerprint: str,
) -> dict:
    """Build + load the org-level evidence catalog for a composite upload.

    Composite projects publish per-partition evidence under
    ``output/{pid}/{subproject}/docs/_topology/evidence/`` but no org-level
    catalogue, so the L1/L2 -> L3/L4 drill-down cannot resolve implementing
    designs. This merges the partition catalogues into
    ``output/{pid}/topology/evidence/`` (fingerprint-gated, idempotent) and then
    reads it through the same ``_load_topology_evidence_catalog`` used by the
    single-run path. Best-effort: any failure returns ``{}`` so the graph still
    serves without design provenance rather than 500-ing.

    The merge is a *producer* of that directory, never its only source. A
    project whose org-level topology was authored directly under
    ``output/{pid}/topology/`` -- an analyst-curated overlay, or a single-root
    scan with no per-partition tree -- already carries the catalogue on disk
    with nothing to merge. Treating "no partitions to merge" as "no evidence"
    discarded a complete catalogue and collapsed every L2 design to a
    business-topology-only node, so the drill-down button did nothing.
    """

    try:
        from mcp_server.services.business_topology import (
            build_composite_evidence_catalog,
        )

        if build_composite_evidence_catalog(
            config.project_root, project_id, scan_fingerprint
        ):
            return _load_topology_evidence_catalog(topology_dir, topology)
        if _org_evidence_catalog_is_usable(topology_dir, scan_fingerprint):
            return _load_topology_evidence_catalog(topology_dir, topology)
    except Exception as exc:  # noqa: BLE001 - drill-down evidence is additive
        logger.warning(
            "Composite evidence catalog unavailable for %s: %s",
            project_id,
            type(exc).__name__,
        )
    return {}


def _org_evidence_catalog_is_usable(topology_dir: Path, scan_fingerprint: str) -> bool:
    """Return True when ``topology_dir/evidence`` can be read as-is.

    A catalogue the merge itself wrote carries a ``.fingerprint`` marker; if
    that marker names a different scan the rows are stale derivations of an
    older tree and must not be served. A catalogue with no marker was authored
    alongside the org topology in the same directory (which has already passed
    its own fingerprint gate before this function is reached), so it is read
    directly.
    """

    from mcp_server.services.business_topology.evidence_merge import (
        FINGERPRINT_MARKER,
        marker_value,
    )

    evidence_dir = topology_dir / "evidence"
    if not evidence_dir.is_dir() or not any(evidence_dir.glob("*.jsonl")):
        return False
    marker_path = evidence_dir / FINGERPRINT_MARKER
    if not marker_path.is_file():
        return True
    try:
        return marker_path.read_text(encoding="utf-8").strip() == marker_value(
            scan_fingerprint
        )
    except OSError as exc:
        logger.warning("Could not read evidence fingerprint marker: %s", exc)
        return False


# Non-production Talend folder/group markers. A design in one of these folders
# is developer scratch / test / PoC / TBD work, not a production integration, so
# it must never be surfaced as an implementing design of a business data stream.
# Matched on GROUP or first PATH segment only (never the job name), so real
# groups like "maximoFourps" are never caught.
_NONPRODUCTION_FOLDER = re.compile(
    r"^(playground|scratch(pad)?|sandbox|poc|proof.?of.?concept|demo|"
    r"test\w*|dev(_\w+)?|util(s|ities)?|\w*_tbd)$",
    re.IGNORECASE,
)
# Talend auto-prefixes a copied job with "Copy_of_" -- a deterministic duplicate
# marker (not fuzzy name matching), so it is safe to treat as non-production.
_NONPRODUCTION_NAME = re.compile(r"^copy[_ ]of[_ ]", re.IGNORECASE)


def _is_nonproduction_artifact(value: dict) -> bool:
    """True for scratch/test/PoC/utility/duplicate designs.

    Classified by Talend folder (group / first path segment) plus the
    deterministic ``Copy_of_`` duplicate prefix -- never by fuzzy job-name
    matching, so a real job that merely contains "test" in its name is kept.
    """

    name = str(value.get("name") or "").strip()
    if name and _NONPRODUCTION_NAME.match(name):
        return True
    group = str(value.get("group") or "").strip()
    path = str(value.get("path") or "")
    first_segment = path.split("/", 1)[0].strip() if "/" in path else ""
    return bool(group and _NONPRODUCTION_FOLDER.match(group)) or bool(
        first_segment and _NONPRODUCTION_FOLDER.match(first_segment)
    )


def _load_topology_evidence_catalog(topology_dir: Path, topology: dict) -> dict:
    """Return a bounded, identity-only map for resolving L1/L2 to L3/L4.

    Non-production designs (Playground/Scratchpad/PoC/dev_*_tbd folders) are
    excluded from the artifact catalogue so the drill-down never presents
    developer scratch or test jobs as implementing designs of a business stream.
    """

    evidence_dir = topology_dir / "evidence"

    def read_jsonl(
        name: str,
        allowed: tuple[str, ...],
        wanted_ids: set[str],
        exclude: "Callable[[dict], bool] | None" = None,
    ) -> list[dict]:
        path = evidence_dir / name
        if not path.is_file() or not wanted_ids:
            return []
        rows: list[dict] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                value = json.loads(line)
                if isinstance(value, dict) and value.get("id") in wanted_ids:
                    if exclude is not None and exclude(value):
                        continue
                    rows.append({key: value.get(key) for key in allowed if key in value})
        except (OSError, json.JSONDecodeError):
            logger.warning("Unreadable business-topology evidence catalogue: %s", name)
            return []
        return rows

    def read_jsonl_matching(
        name: str,
        allowed: tuple[str, ...],
        predicate: "Callable[[dict], bool]",
        *,
        limit: int = 2_000,
        exclude: "Callable[[dict], bool] | None" = None,
    ) -> list[dict]:
        """Read a bounded evidence projection selected by stable row fields."""

        path = evidence_dir / name
        if not path.is_file():
            return []
        rows: list[dict] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict) or not predicate(value):
                    continue
                if exclude is not None and exclude(value):
                    continue
                rows.append({key: value.get(key) for key in allowed if key in value})
                if len(rows) >= limit:
                    break
        except (OSError, json.JSONDecodeError):
            logger.warning("Unreadable business-topology evidence catalogue: %s", name)
            return []
        return rows

    wanted_artifacts = {
        str(artifact_id)
        for items in (
            topology.get("data_streams") or [],
            topology.get("integration_solutions") or [],
        )
        for item in items
        if isinstance(item, dict)
        for artifact_id in item.get("artifact_ids") or []
    }
    wanted_artifacts.update(
        str(item.get("artifact_id"))
        for item in topology.get("artifact_annotations") or []
        if isinstance(item, dict) and item.get("artifact_id")
    )
    wanted_components = {
        str(item.get("component_id"))
        for item in topology.get("component_annotations") or []
        if isinstance(item, dict) and item.get("component_id")
    }
    wanted_lineage = {
        str(item.get("lineage_id"))
        for item in topology.get("lineage_annotations") or []
        if isinstance(item, dict) and item.get("lineage_id")
    }
    artifacts = read_jsonl(
        "artifacts.jsonl",
        (
            "id",
            "name",
            "job_id",
            "source_root",
            "artifact_kind",
            "artifact_role",
        ),
        wanted_artifacts,
        exclude=_is_nonproduction_artifact,
    )
    artifact_names = {
        str(value)
        for artifact in artifacts
        for value in (artifact.get("name"), artifact.get("job_id"))
        if value
    }
    relations = read_jsonl_matching(
        "relations.jsonl",
        ("id", "source", "target", "relation_type", "component_name", "resolution"),
        lambda value: str(value.get("source") or "") in artifact_names,
        limit=500,
    )
    related_names = {
        str(relation.get("target"))
        for relation in relations
        if relation.get("target")
    }
    source_roots = {str(artifact.get("source_root") or "") for artifact in artifacts}
    related_candidates = read_jsonl_matching(
        "artifacts.jsonl",
        (
            "id", "name", "job_id", "source_root", "artifact_kind", "artifact_role",
        ),
        lambda value: (
            str(value.get("name") or value.get("job_id") or "") in related_names
        ),
        limit=500,
        exclude=_is_nonproduction_artifact,
    )
    related_artifacts: list[dict] = []
    for related_name in sorted(related_names):
        candidates = [
            value for value in related_candidates
            if related_name in {str(value.get("name") or ""), str(value.get("job_id") or "")}
        ]
        same_root = [
            value for value in candidates
            if str(value.get("source_root") or "") in source_roots
        ]
        resolved = same_root if len(same_root) == 1 else candidates if len(candidates) == 1 else []
        for artifact in resolved:
            if artifact not in related_artifacts:
                related_artifacts.append(artifact)
    for artifact in related_artifacts:
        if artifact not in artifacts:
            artifacts.append(artifact)
        if artifact.get("id"):
            wanted_artifacts.add(str(artifact["id"]))
        artifact_names.update(
            str(value)
            for value in (artifact.get("name"), artifact.get("job_id"))
            if value
        )
    components = read_jsonl_matching(
        "components.jsonl",
        ("id", "artifact_id", "name", "component_type"),
        lambda value: (
            str(value.get("artifact_id") or "") in wanted_artifacts
            or str(value.get("id") or "") in wanted_components
        ),
    )
    wanted_artifacts.update(
        str(item["artifact_id"])
        for item in components
        if item.get("artifact_id")
    )

    return {
        "artifacts": artifacts,
        "components": components,
        "lineage": read_jsonl_matching(
            "lineage.jsonl",
            (
                "id",
                "job_name",
                "source_table",
                "source_column",
                "source_component",
                "target_table",
                "target_column",
                "target_component",
            ),
            lambda value: (
                str(value.get("id") or "") in wanted_lineage
                or str(value.get("job_name") or "") in artifact_names
            ),
        ),
        "relations": relations,
    }


def _load_job_scan(project_id: str, job_name: str) -> tuple[str, dict, Path]:
    """Resolve a job's group and load its per-job scan JSON.

    Shared loader for the job detail and job graph endpoints. Raises 400 for
    unsafe job names, 404 when the project has no scan or the job is not in
    the manifest. Returns (group, scan_data, job_path); scan_data is empty
    when the per-job file is missing or unreadable.
    """
    if "/" in job_name or "\\" in job_name or ".." in job_name:
        raise HTTPException(status_code=400, detail="Invalid job name")

    scan_dir = config.intodoc_output_dir / project_id / "scan"
    manifest_path = scan_dir / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail=f"No scan data for project: {project_id}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    job_groups: dict[str, str] = manifest.get("job_groups", {})
    group = job_groups.get(job_name, "")

    if not group:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_name}")

    job_files: dict[str, str] = manifest.get("job_files", {}) or {}
    relative_job_file = job_files.get(job_name, "")
    job_path = scan_dir / relative_job_file if relative_job_file else scan_dir / "jobs" / group / f"{job_name}.json"
    scan_data: dict = {}
    if job_path.exists():
        try:
            scan_data = json.loads(job_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Unreadable job scan file %s: %s", job_path, exc)
    return group, scan_data, job_path


@router.get("/projects/{project_id}/jobs/{job_name}/detail", response_model=JobDetailResponse)
async def get_job_detail(project_id: str, job_name: str, request: Request):
    """Return full scan data and relevant doc sections for a specific job."""
    await require_project_access(request, project_id)

    group, scan_data, job_path = _load_job_scan(project_id, job_name)

    # Find doc sections whose heading matches this job exactly
    doc_sections = _extract_job_doc_sections(project_id, job_name)

    # Derive direction and summary
    job_path_str = str(job_path) if job_path.exists() else ""
    direction = _derive_direction(job_path_str, job_name)

    comp_types = list({
        c.get("component_type", "")
        for c in scan_data.get("components", [])
        if c.get("component_type")
    })
    ext_systems = list({
        s for c in scan_data.get("components", [])
        for s in ([c.get("external_system")] if c.get("external_system") else [])
    })
    # Source priority mirrors the topology graph endpoint so the panel and the
    # node tooltip show the same summary:
    #   1. Cached component summary (Phase 2 prose / scan_data.purpose /
    #      heuristic) — written by the async extractor, populated after every
    #      scan and after every doc-generation swap. Richest source.
    #   2. Structured Purpose line from 02_COMPONENT_INVENTORY.md.
    #   3. Legacy paragraph scrape from doc sections.
    #   4. Empty (panel hides the section).
    cached_summaries = SummaryService.load_summaries(project_id)
    summary = (
        cached_summaries.get(job_name)
        or _extract_job_purpose_from_inventory(project_id, job_name)
        or _derive_job_summary(job_name, doc_sections, comp_types, ext_systems)
        or ""
    )

    return JobDetailResponse(
        project_id=project_id,
        job_name=job_name,
        group=group,
        scan_data=scan_data,
        doc_sections=doc_sections,
        summary=summary,
        direction=direction,
    )


@router.get("/projects/{project_id}/jobs/{job_name}/graph", response_model=JobGraphResponse)
async def get_job_graph(project_id: str, job_name: str, request: Request):
    """Return the Level-2 component graph for one job (topology drill-down).

    Nodes are the job's components; edges come from the job's connections
    array with control-flow triggers flagged apart from data-flow rows.
    """
    await require_project_access(request, project_id)

    group, scan_data, _job_path = _load_job_scan(project_id, job_name)
    if not scan_data:
        raise HTTPException(status_code=404, detail=f"No scan data for job: {job_name}")

    # Read per-component descriptions from the already-generated, already
    # credential-filtered inventory doc (no LLM call). Empty map when no docs
    # exist -> every node's description defaults to "" (anti-fabrication).
    descriptions = parse_component_descriptions(project_id, job_name)
    platform = ""
    meta_path = config.intodoc_output_dir / project_id / "scan" / "meta.json"
    if meta_path.exists():
        try:
            platform = str(
                json.loads(meta_path.read_text(encoding="utf-8")).get("platform") or ""
            )
        except (OSError, json.JSONDecodeError):
            logger.warning("Unreadable scan metadata for project %s", project_id)

    return build_job_graph(
        project_id=project_id,
        job_name=job_name,
        group=group,
        job_data=scan_data,
        descriptions=descriptions,
        platform=platform or "talend",
    )


@router.get(
    "/projects/{project_id}/jobs/{job_name}/components/{unique_name}/graph",
    response_model=ComponentGraphResponse,
)
async def get_component_graph(
    project_id: str, job_name: str, unique_name: str, request: Request
):
    """Return the Level-3 tMap column/table graph for one component.

    Only tMap has captured intra-component structure today (raw mapping
    expressions only -- reference resolution lands in slice S4). Every other
    component type -- and any tMap without a positionally-matched tmap_data
    entry -- returns 404 with a ``{"fallback": "inspector"}`` detail so the
    UI opens the structured inspector panel instead of a fabricated graph.
    """
    await require_project_access(request, project_id)

    _group, scan_data, _job_path = _load_job_scan(project_id, job_name)
    if not scan_data:
        raise HTTPException(status_code=404, detail=f"No scan data for job: {job_name}")

    if not component_exists(scan_data, unique_name):
        raise HTTPException(status_code=404, detail=f"Component not found: {unique_name}")

    if not is_tmap_component(scan_data, unique_name):
        raise HTTPException(status_code=404, detail={"fallback": "inspector"})

    graph = build_component_graph(
        project_id=project_id,
        job_name=job_name,
        component_unique_name=unique_name,
        job_data=scan_data,
    )
    if graph is None:
        raise HTTPException(status_code=404, detail={"fallback": "inspector"})

    return graph


@router.get("/projects/{project_id}/lineage/field", response_model=LineageFieldResponse)
async def get_field_lineage(
    project_id: str,
    request: Request,
    job: str = Query(..., min_length=1, description="Job name the field belongs to"),
    table: str = Query(..., min_length=1, description="Row/table name (tMap input or output row)"),
    column: str = Query(..., min_length=1, description="Column name within the table"),
    component: str = Query("", description="Optional component unique_name to disambiguate the start field"),
    direction: Literal["up", "down", "both"] = Query("both", description="Traversal direction"),
    max_depth: int = Query(10, ge=1, le=50, description="Maximum BFS hop count from the start field"),
):
    """Return the upstream/downstream field-lineage path for one column.

    BFS over `scan/lineage.json` (the deterministic S4 tMap tokenizer pass),
    cycle-safe and depth-capped. Lineage does not cross job boundaries yet
    (S5), so the response is always scoped to the given `job`.
    """
    await require_project_access(request, project_id)

    try:
        return build_field_lineage(
            project_id,
            config.intodoc_output_dir,
            job_name=job,
            table=table,
            column=column,
            component=component,
            direction=direction,
            max_depth=max_depth,
        )
    except LineageDataUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/projects/{project_id}/lineage/job",
    response_model=LineageJobResponse,
    responses={
        404: {
            "model": LineageErrorResponse,
            "description": (
                "No lineage for this request. Branch on `detail.reason`, never on "
                "`detail.message`: `lineage_artifact_absent` means the project has no "
                "scan/lineage.json and needs a (re)scan; `lineage_selection_empty` means "
                "lineage exists but the requested job/group name is not in it."
            ),
        }
    },
)
async def get_job_lineage(
    project_id: str,
    request: Request,
    job: list[str] = Query(
        default_factory=list,
        description=(
            "Job name to include; repeat for several (max "
            f"{MAX_JOBS_PER_REQUEST}). Matched EXACTLY, then case-insensitively "
            "exact -- never fuzzy. Unmatched names are absent from the response's "
            "`jobs` list."
        ),
    ),
    group: str = Query(
        "",
        description=(
            "Functional group to include, resolved via scan/manifest.json "
            "job_groups. Accepts the full group path, its trailing segment, or "
            "any path segment (zip marker stripped)."
        ),
    ),
    offset: int = Query(0, ge=0, description="Row offset into the sorted edge list"),
    limit: int = Query(50, ge=1, le=200, description="Maximum rows to return"),
):
    """Return a flat, paged rollup of every field mapping the selected jobs perform.

    The field API (`/lineage/field`) answers "where does THIS column come
    from" and needs a job + table + column to start. This one answers the
    question a caller has BEFORE it knows a column worth tracing -- "what data
    moves out of these jobs, into which tables, and how is it transformed" --
    over whole jobs or a whole functional group.

    Rows are totally ordered with resolved (confirmed) mappings first, so
    `offset`/`limit` paging is stable across calls and a caller that reads only
    page 1 sees the confirmed column-to-column mappings rather than the
    dynamic/globalMap rows. Expressions are credential-redacted and clipped --
    this output is LLM prompt material.

    Two distinguishable 404s, discriminated by a STABLE `detail.reason` code
    rather than by the prose message: `lineage_artifact_absent` (no lineage
    data for the project at all -- rescan) versus `lineage_selection_empty`
    (lineage exists, the requested job/group name is not in it -- do NOT tell
    the user to rescan). See `schemas.LineageErrorDetail`.
    """
    # Access check first: an unassigned caller must get 403, never a 422 that
    # confirms the route (and therefore the project) is there.
    await require_project_access(request, project_id)

    # Hand-rolled rather than declared on Query(...): a repeated query param
    # bound to `list[str]` routes Query's own length constraints at the ITEM,
    # not the list, so `max_length` there would silently cap job-name length
    # instead of the job count.
    if not job and not group.strip():
        raise HTTPException(
            status_code=422,
            detail="At least one of 'job' or 'group' is required",
        )
    if len(job) > MAX_JOBS_PER_REQUEST:
        raise HTTPException(
            status_code=422,
            detail=f"At most {MAX_JOBS_PER_REQUEST} 'job' parameters are allowed, got {len(job)}",
        )

    try:
        return build_job_lineage(
            project_id,
            config.intodoc_output_dir,
            jobs=job,
            group=group,
            offset=offset,
            limit=limit,
        )
    except LineageQueryError as exc:
        # `reason` comes off the exception class, so the route never restates
        # the mapping and a new LineageQueryError subclass cannot reach the wire
        # without its own discriminator. Dict-shaped detail follows the existing
        # convention in this module for errors a client must branch on
        # (`detail={"fallback": "inspector"}` above, `{"code", "message"}` in
        # connectors.py).
        raise HTTPException(
            status_code=404, detail={"reason": exc.reason, "message": str(exc)}
        ) from exc


# --- AI Summaries ---


@router.post(
    "/projects/{project_id}/generate-summaries",
    response_model=GenerateSummariesResponse,
)
async def generate_summaries(
    project_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Generate AI-powered job summaries using Ollama (runs in background)."""
    await require_project_access(request, project_id)

    scan_dir = config.intodoc_output_dir / project_id / "scan"
    if not (scan_dir / "manifest.json").exists():
        return GenerateSummariesResponse(
            project_id=project_id, status="no_scan",
        )

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
    platform = project.platform if project else "talend"

    background_tasks.add_task(_run_summaries_background, project_id, platform)

    return GenerateSummariesResponse(
        project_id=project_id, status="started",
    )


async def _run_summaries_background(project_id: str, platform: str) -> None:
    """Background task: generate AI summaries for all jobs."""
    svc = SummaryService()
    try:
        result = await svc.generate_project_summaries(project_id, platform)
        logger.info(
            "Summary generation for %s: %d summaries, %d errors",
            project_id, result["summary_count"], result["error_count"],
        )
    except Exception as e:
        logger.error("Summary generation failed for %s: %s", project_id, e)


# --- Deep Dive ---


@router.post("/deep-dive")
async def deep_dive_query(req: DeepDiveRequest, request: Request):
    """Stream deep-dive analysis via the configured repo-owned engine (SSE)."""
    await require_project_access(request, req.project_id)
    # Validate project exists and has scan data
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(req.project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {req.project_id}")
        if project.status not in ("ready", "scanned") or project.job_count == 0:
            raise HTTPException(
                status_code=400,
                detail=f"Project '{req.project_id}' has no scan data (status={project.status}, jobs={project.job_count})",
            )

    service = DeepDiveService()
    bridge = RagBridge()

    async def event_stream():
        async for event in service.query(req.project_id, req.question, session_id=req.session_id):
            event_type = event.get("type", "message")
            data = event.get("data", "")

            # Intercept usage events to persist token counts
            if event_type == "usage" and isinstance(data, dict):
                try:
                    await bridge.post_usage(
                        interface="deep_dive",
                        project_id=req.project_id,
                        input_tokens=data.get("input_tokens"),
                        output_tokens=data.get("output_tokens"),
                        cost_usd=data.get("cost_usd"),
                        latency_ms=data.get("duration_ms") or data.get("latency_ms"),
                        model_name=data.get("model", "claude"),
                        provider=data.get("provider", "anthropic"),
                        query_text=req.question[:500] if req.question else None,
                    )
                except Exception:
                    pass  # Don't break the SSE stream

            # JSON-encode all SSE payloads, including strings. Deep Dive answers
            # often contain markdown newlines; raw multiline data breaks the
            # frontend's line-based SSE parser.
            yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/deep-dive/projects", response_model=DeepDiveProjectsResponse)
async def deep_dive_projects(request: Request):
    """List projects available for deep dive, filtered by user access."""
    auth_payload = getattr(request.state, "auth_payload", None)

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        all_projects = await registry.list_projects()

        scanned = [
            p for p in all_projects
            if p.status in ("ready", "scanned") and p.job_count > 0
        ]

        if auth_payload:
            user_id = int(auth_payload.get("sub", 0))
            is_superadmin = await _get_user_is_superadmin(db, user_id)
            if not is_superadmin:
                perms = await _get_user_permission_codenames(db, user_id)
                if "view_all_projects" not in perms:
                    assigned_ids = await _get_assigned_project_ids(db, user_id)
                    scanned = [p for p in scanned if p.id in assigned_ids]

        available = [
            DeepDiveProject(
                id=p.id,
                display_name=p.display_name,
                platform=p.platform,
                job_count=p.job_count,
                artifact_counts=_project_artifact_counts(p.id, p.job_count),
                last_scan_at=p.last_scan_at,
            )
            for p in scanned
        ]

        return DeepDiveProjectsResponse(projects=available, total=len(available))
