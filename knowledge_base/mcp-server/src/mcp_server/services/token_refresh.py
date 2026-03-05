"""Auto-refresh Claude Code OAuth tokens before they expire.

The Claude Code CLI stores OAuth credentials in ~/.claude/.credentials.json.
When running as a non-interactive systemd service, the CLI cannot refresh
expired tokens on its own. This module handles that automatically.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

ANTHROPIC_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
REFRESH_MARGIN_MS = 5 * 60 * 1000  # refresh 5 minutes before expiry
_CREDS_PATH = Path.home() / ".claude" / ".credentials.json"


def ensure_token_fresh() -> bool:
    """Check if the Claude OAuth token is fresh; refresh if expired.

    Returns True if the token is valid (or was successfully refreshed),
    False if refresh failed.
    """
    if not _CREDS_PATH.exists():
        logger.warning("No Claude credentials file found at %s", _CREDS_PATH)
        return False

    try:
        creds = json.loads(_CREDS_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to read credentials: %s", e)
        return False

    oauth = creds.get("claudeAiOauth")
    if not oauth:
        logger.warning("No claudeAiOauth section in credentials")
        return False

    now_ms = int(time.time() * 1000)
    expires_at = oauth.get("expiresAt", 0)

    if expires_at > now_ms + REFRESH_MARGIN_MS:
        # Token still valid
        return True

    # Token expired or expiring soon — refresh it
    refresh_token = oauth.get("refreshToken")
    if not refresh_token:
        logger.error("No refresh token available — manual re-login required")
        return False

    logger.info("OAuth token expired or expiring soon, refreshing...")

    try:
        resp = httpx.post(
            ANTHROPIC_TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLAUDE_CLIENT_ID,
            },
            timeout=30.0,
        )
    except httpx.HTTPError as e:
        logger.error("Token refresh request failed: %s", e)
        return False

    if resp.status_code != 200:
        logger.error(
            "Token refresh failed (HTTP %d): %s", resp.status_code, resp.text[:300]
        )
        return False

    data = resp.json()
    oauth["accessToken"] = data["access_token"]
    oauth["refreshToken"] = data["refresh_token"]
    oauth["expiresAt"] = now_ms + data.get("expires_in", 28800) * 1000

    try:
        _CREDS_PATH.write_text(json.dumps(creds))
        logger.info("OAuth token refreshed successfully (new expiry in %ds)", data.get("expires_in", "?"))
    except OSError as e:
        logger.error("Failed to write refreshed credentials: %s", e)
        return False

    return True
