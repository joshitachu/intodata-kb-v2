"""GitHub REST API helpers for webhook management."""

from __future__ import annotations

import re
import logging

import httpx

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_OWNER_REPO_RE = re.compile(r"github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?$")


def _parse_owner_repo(repo_url: str) -> tuple[str, str]:
    m = _OWNER_REPO_RE.search(repo_url)
    if not m:
        raise ValueError(f"Cannot parse owner/repo from URL: {repo_url!r}")
    return m.group(1), m.group(2)


async def register_webhook(
    repo_url: str, token: str, webhook_url: str, secret: str
) -> int:
    """Create a push+pull_request webhook. Returns the webhook ID from GitHub."""
    owner, repo = _parse_owner_repo(repo_url)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_GITHUB_API}/repos/{owner}/{repo}/hooks",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json={
                "name": "web",
                "active": True,
                "events": ["push", "pull_request"],
                "config": {
                    "url": webhook_url,
                    "content_type": "json",
                    "secret": secret,
                    "insecure_ssl": "0",
                },
            },
        )
    if resp.status_code == 422:
        # Already exists — find the existing one
        existing_id = await _find_existing_webhook(owner, repo, token, webhook_url)
        if existing_id:
            return existing_id
    resp.raise_for_status()
    return resp.json()["id"]


async def _find_existing_webhook(owner: str, repo: str, token: str, webhook_url: str) -> int | None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_GITHUB_API}/repos/{owner}/{repo}/hooks",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
    if not resp.is_success:
        return None
    for hook in resp.json():
        if hook.get("config", {}).get("url") == webhook_url:
            return hook["id"]
    return None


async def delete_webhook(repo_url: str, token: str, webhook_id: int) -> None:
    owner, repo = _parse_owner_repo(repo_url)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.delete(
            f"{_GITHUB_API}/repos/{owner}/{repo}/hooks/{webhook_id}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
    if resp.status_code == 404:
        return  # Already gone
    resp.raise_for_status()


async def check_webhook(repo_url: str, token: str, webhook_id: int) -> bool:
    """Return True if the webhook still exists and is active on GitHub."""
    owner, repo = _parse_owner_repo(repo_url)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_GITHUB_API}/repos/{owner}/{repo}/hooks/{webhook_id}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
    if resp.status_code == 404:
        return False
    resp.raise_for_status()
    return resp.json().get("active", False)
