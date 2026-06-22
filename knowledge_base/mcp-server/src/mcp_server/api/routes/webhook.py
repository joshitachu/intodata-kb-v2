"""GitHub webhook handler - HMAC-verified, no JWT required."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from mcp_server.config import config
from mcp_server.db.session import async_session_factory
from mcp_server.services.auto_doc_update import AutoDocUpdateService
from mcp_server.services.git_reingestion import (  # noqa: F401 - re-exported for helper tests
    ChangedFile,
    parse_name_status_diff as _parse_name_status_diff,
    resolve_changed_groups as _resolve_changed_groups,
)
from mcp_server.services.git_remote_service import (
    canonical_remote_key,
    pull_remote_ff_only,
    strip_url_credentials,
)
from mcp_server.services.log_manager import LogManager
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.scanner_service import ScannerService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhook"])

_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)


@router.post("/webhook/github/webhook")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle GitHub push/pull_request events.

    Verifies HMAC-SHA256 signature, looks up the project by clone_url, then
    triggers a background scan for pull requests or diff-only doc patch for pushes.
    """
    # Read raw bytes first; HMAC must be computed before JSON parsing.
    raw_body = await request.body()

    # Verify GitHub signature. A local-only bypass exists for quick tunnel tests;
    # production keeps the HMAC requirement.
    actual_sig = request.headers.get("X-Hub-Signature-256") or request.headers.get(
        "X-GitHub-Signature-256",
        "",
    )
    if not config.dev_webhook_skip_signature or actual_sig:
        secret = config.github_webhook_secret
        if not secret:
            raise HTTPException(status_code=503, detail="Webhook secret not configured")

        expected_sig = "sha256=" + hmac.new(
            secret.encode(), raw_body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(actual_sig, expected_sig):
            raise HTTPException(status_code=401, detail="Invalid signature")

    body = _parse_github_payload(raw_body)
    repo = body.get("repository", {})
    clone_url = repo.get("clone_url", "")

    if not clone_url:
        return {"status": "ignored", "reason": "missing_clone_url"}

    event = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery")

    if event == "ping":
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            project = await _find_project_by_clone_url(registry, clone_url)
            if project:
                await registry.record_webhook_observation(
                    project.id,
                    _webhook_observation(
                        event=event,
                        clone_url=clone_url,
                        delivery_id=delivery_id,
                        project_id=project.id,
                        status="matched",
                    ),
                )
                await db.commit()

        if project:
            log_mgr = request.app.state.log_manager
            await log_mgr.emit(
                project.id,
                f"GitHub webhook ping received for {strip_url_credentials(clone_url)}",
                level="info",
            )
            return {
                "status": "ping_received",
                "registered": True,
                "project_id": project.id,
            }

        return {"status": "ping_received", "registered": False}

    if event not in ("push", "pull_request"):
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            project = await _find_project_by_clone_url(registry, clone_url)
            if project:
                await registry.record_webhook_observation(
                    project.id,
                    _webhook_observation(
                        event=event,
                        clone_url=clone_url,
                        delivery_id=delivery_id,
                        project_id=project.id,
                        status="ignored",
                        reason="unsupported_event",
                    ),
                    error="unsupported_event",
                    set_error=True,
                )
                await db.commit()
        return {"status": "ignored", "reason": "unsupported_event", "event": event}

    # Find project by repo_url
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await _find_project_by_clone_url(registry, clone_url)

        if not project:
            # Check if this is a plugin pack repo
            pack_id = await _find_plugin_pack_by_clone_url(db, clone_url)
            if pack_id is not None and event == "push":
                background_tasks.add_task(_pull_plugin_pack_background, pack_id)
                return {"status": "pack_update_triggered", "plugin_pack_id": pack_id}
            return {
                "status": "ignored",
                "reason": "unregistered_repo",
                "repo_url": strip_url_credentials(clone_url),
            }

        project_id = project.id
        local_path = project.local_path
        platform = project.platform
        is_push = event == "push"
        before_sha = body.get("before", "") if is_push else ""
        after_sha = body.get("after", "") if is_push else ""
        is_first_push = before_sha == "0" * 40

        if is_push and not _valid_push_shas(before_sha, after_sha):
            await registry.record_webhook_observation(
                project.id,
                _webhook_observation(
                    event=event,
                    clone_url=clone_url,
                    delivery_id=delivery_id,
                    project_id=project.id,
                    status="rejected",
                    reason="invalid_sha_payload",
                    before_sha=before_sha,
                    after_sha=after_sha,
                ),
                error="invalid_sha_payload",
                set_error=True,
            )
            await db.commit()
            return {
                "status": "ignored",
                "reason": "invalid_sha_payload",
                "project_id": project.id,
            }

        await registry.record_webhook_observation(
            project.id,
            _webhook_observation(
                event=event,
                clone_url=clone_url,
                delivery_id=delivery_id,
                project_id=project.id,
                status="queued",
                before_sha=before_sha if is_push else None,
                after_sha=after_sha if is_push else None,
                is_first_push=is_first_push if is_push else None,
            ),
        )
        if event == "pull_request":
            await registry.update_status(project.id, "scanning")
            run = await registry.create_scan_run(project.id, triggered_by="webhook")
            await db.commit()
            run_id = run.id
        else:
            await db.commit()
            run_id = 0

    log_mgr = request.app.state.log_manager
    background_tasks.add_task(
        _webhook_background,
        project_id, local_path, platform, run_id, log_mgr,
        is_push=is_push,
        before_sha=before_sha,
        after_sha=after_sha,
        is_first_push=is_first_push,
    )

    return {"status": "webhook_triggered", "project_id": project_id}


async def _find_plugin_pack_by_clone_url(db, clone_url: str) -> int | None:
    """Return the id of a plugin pack whose repo_url matches clone_url, or None."""
    from sqlalchemy import select
    from mcp_server.db.models import PluginPack

    clone_key = canonical_remote_key(clone_url)
    result = await db.execute(select(PluginPack).where(PluginPack.repo_url.isnot(None)))
    packs = result.scalars().all()
    for pack in packs:
        if pack.repo_url and canonical_remote_key(pack.repo_url) == clone_key:
            return pack.id
    return None


def _parse_github_payload(raw_body: bytes) -> dict:
    """Parse GitHub JSON or form-encoded webhook payloads."""
    try:
        return json.loads(raw_body)
    except json.JSONDecodeError:
        form = parse_qs(raw_body.decode("utf-8", errors="replace"))
        payload = form.get("payload", [""])[0]
        if payload:
            return json.loads(payload)
        raise


async def _pull_plugin_pack_background(pack_id: int) -> None:
    """Background: git pull a plugin pack and update its last_commit_sha."""
    from pathlib import Path as _Path
    from sqlalchemy import select as _select
    from mcp_server.db.models import PluginPack as _PluginPack
    from mcp_server.services.plugin_schema import load_plugin_manifest as _load

    async with async_session_factory() as db:
        result = await db.execute(_select(_PluginPack).where(_PluginPack.id == pack_id))
        pack = result.scalar_one_or_none()
        if not pack or not pack.repo_url:
            return

        try:
            await pull_remote_ff_only(pack.path)
            new_sha = await _git_rev_parse_head(pack.path)
            pack.last_commit_sha = new_sha

            try:
                manifest = _load(_Path(pack.path))
                pack.version = manifest.version
                pack.description = manifest.description or pack.description
            except Exception:
                pass

            await db.commit()
            logger.info("Plugin pack %r updated to %s via webhook", pack.name, new_sha[:8])
        except Exception:
            logger.exception("Failed to pull plugin pack %r (id=%d)", pack.name, pack_id)


async def _find_project_by_clone_url(registry: ProjectRegistry, clone_url: str):
    """Find a registered project whose repo URL matches GitHub's clone_url."""
    projects = await registry.list_projects()
    clone_key = canonical_remote_key(clone_url)
    for project in projects:
        if not project.repo_url:
            continue
        if canonical_remote_key(project.repo_url) == clone_key:
            return project
    return None


async def _git_rev_parse_head(local_path: str) -> str:
    from mcp_server.services.git_remote_service import run_git

    return await run_git(local_path, "rev-parse", "HEAD", strip=True)


def _valid_push_shas(before_sha: str, after_sha: str) -> bool:
    return bool(
        isinstance(before_sha, str)
        and isinstance(after_sha, str)
        and _SHA_RE.fullmatch(before_sha)
        and _SHA_RE.fullmatch(after_sha)
    )


def _webhook_observation(
    *,
    event: str,
    clone_url: str,
    delivery_id: str | None,
    project_id: str | None,
    status: str,
    reason: str | None = None,
    before_sha: str | None = None,
    after_sha: str | None = None,
    is_first_push: bool | None = None,
) -> dict[str, object]:
    observations = ["webhook_received"]
    if project_id:
        observations.append("project_matched")
    if reason:
        observations.append(reason)
    payload: dict[str, object | None] = {
        "event": event or "unknown",
        "delivery_id": delivery_id,
        "repo_url": strip_url_credentials(clone_url),
        "canonical_repo": canonical_remote_key(clone_url),
        "registered": bool(project_id),
        "project_id": project_id,
        "status": status,
        "reason": reason,
        "observations": observations,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "before_sha": before_sha,
        "after_sha": after_sha,
        "is_first_push": is_first_push,
    }
    return {key: value for key, value in payload.items() if value is not None}


async def _webhook_background(
    project_id: str,
    local_path: str,
    platform: str,
    run_id: int,
    log_mgr: LogManager,
    *,
    is_push: bool,
    before_sha: str,
    after_sha: str,
    is_first_push: bool,
):
    """Background: pull request scan or push diff-only doc patch."""
    if is_push:
        await AutoDocUpdateService(log_mgr=log_mgr).run(
            project_id=project_id,
            before_sha=before_sha,
            after_sha=after_sha,
            trigger="webhook",
        )
        return

    # Pull request events keep the existing scan-only behavior; code is not merged.
    scanner = ScannerService(log_manager=log_mgr)
    log_mgr.reset_log_file(project_id)
    result = await scanner.run_scan(
        project_id=project_id,
        repo_path=local_path,
        platform=platform,
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
        else:
            await registry.update_status(
                project_id, "error", error_message=result.get("error")
            )
            await registry.complete_scan_run(run_id, status="failed")
        await db.commit()
