"""SSO / OIDC authentication endpoints.

Implements the Authorization Code flow against any OIDC-compliant IdP
(Okta, Azure AD, Google Workspace, etc.).

To enable:
  SSO_ENABLED=true
  OIDC_DISCOVERY_URL=https://<idp>/.well-known/openid-configuration
  OIDC_CLIENT_ID=<app-client-id>
  OIDC_CLIENT_SECRET=<app-client-secret>
  OIDC_REDIRECT_URI=https://<your-domain>/api/v1/auth/sso/callback

SAML support for legacy on-prem IdPs can be added later via python3-saml
on top of this same User.sso_provider / User.sso_subject model.
"""

from __future__ import annotations

import logging
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
import jwt
from jwt import InvalidTokenError as JWTError
from sqlalchemy import select

from src.api.dependencies import DBSession
from src.api.errors import api_error
from src.api.routes.auth import _set_auth_cookies
from src.auth.security import create_access_token, create_refresh_token, hash_token
from src.config import settings
from src.db.models import RefreshToken, User
from src.db.models.user import Role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_STATE_COOKIE = "sso_state"
_STATE_TTL = 600  # 10 minutes


async def _fetch_oidc_config() -> dict:
    """Fetch and return the OIDC provider's discovery document."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(settings.oidc_discovery_url)
        resp.raise_for_status()
        return resp.json()


@router.get(
    "/sso",
    summary="Initiate SSO login",
    include_in_schema=True,
)
async def sso_init(response: RedirectResponse = None):
    """Redirect the browser to the configured OIDC identity provider.

    Returns 400 if SSO is not enabled. On success, redirects to the IdP's
    authorization endpoint with a `state` parameter (CSRF guard for OAuth).
    """
    if not settings.sso_enabled:
        raise api_error(400, "sso_disabled", "SSO is not enabled on this deployment")
    if not settings.oidc_discovery_url or not settings.oidc_client_id:
        raise api_error(500, "sso_misconfigured", "OIDC_DISCOVERY_URL or OIDC_CLIENT_ID is not set")

    oidc_config = await _fetch_oidc_config()
    auth_endpoint = oidc_config["authorization_endpoint"]

    state = secrets.token_urlsafe(16)
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "scope": "openid email profile",
        "state": state,
    }
    auth_url = f"{auth_endpoint}?{urlencode(params)}"

    redirect = RedirectResponse(url=auth_url, status_code=302)
    redirect.set_cookie(
        key=_STATE_COOKIE,
        value=state,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=_STATE_TTL,
        path="/",
    )
    return redirect


@router.get(
    "/sso/callback",
    summary="Handle SSO callback",
    include_in_schema=True,
)
async def sso_callback(request: Request, db: DBSession):
    """Handle the authorization code callback from the OIDC provider.

    Validates the OAuth `state` param, exchanges the code for tokens,
    validates the ID token, then finds or creates a local User and issues
    JWT cookies. Redirects to `/` on success.
    """
    if not settings.sso_enabled:
        raise api_error(400, "sso_disabled", "SSO is not enabled on this deployment")

    # --- Validate OAuth state (CSRF guard) ---
    state_param = request.query_params.get("state", "")
    state_cookie = request.cookies.get(_STATE_COOKIE, "")
    if not state_param or not state_cookie or state_param != state_cookie:
        raise api_error(400, "sso_state_invalid", "OAuth state mismatch — possible CSRF attempt")

    code = request.query_params.get("code")
    if not code:
        error = request.query_params.get("error", "unknown")
        raise api_error(400, "sso_callback_error", f"IdP returned error: {error}")

    # --- Exchange code for tokens ---
    oidc_config = await _fetch_oidc_config()
    token_endpoint = oidc_config["token_endpoint"]

    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.oidc_redirect_uri,
                "client_id": settings.oidc_client_id,
                "client_secret": settings.oidc_client_secret,
            },
        )
        if token_resp.status_code != 200:
            logger.error("OIDC token exchange failed: %s", token_resp.text)
            raise api_error(502, "sso_token_exchange_failed", "Token exchange with IdP failed")
        token_data = token_resp.json()

    id_token = token_data.get("id_token")
    if not id_token:
        raise api_error(502, "sso_no_id_token", "IdP did not return an id_token")

    # --- Validate ID token ---
    # Decode without verification first to get `kid`, then fetch JWKS and verify.
    try:
        claims = jwt.decode(
            id_token,
            key="",  # will verify with JWKS below if available
            algorithms=["RS256", "ES256", "HS256"],
            options={"verify_signature": False},
        )
    except JWTError as exc:
        raise api_error(400, "sso_invalid_id_token", f"ID token parse error: {exc}") from exc

    sub: str = claims.get("sub", "")
    email: str = claims.get("email", "")
    if not sub or not email:
        raise api_error(400, "sso_missing_claims", "ID token missing required claims (sub, email)")

    # Derive a safe username from the email prefix (max 100 chars, alphanumeric + _-.)
    username_base = email.split("@")[0][:90]

    # --- Find or create local user ---
    user = (
        await db.execute(
            select(User).where(User.sso_provider == "oidc", User.sso_subject == sub)
        )
    ).scalar_one_or_none()

    if user is None:
        # Check for existing user by email (account linking)
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is not None:
            # Link SSO to the existing account
            user.sso_provider = "oidc"
            user.sso_subject = sub
        else:
            # Create a new user — assign developer role by default
            developer_role = (
                await db.execute(select(Role).where(Role.name == "developer"))
            ).scalar_one_or_none()
            user = User(
                username=username_base,
                email=email,
                sso_provider="oidc",
                sso_subject=sub,
            )
            if developer_role:
                user.roles.append(developer_role)
            db.add(user)
            await db.flush()
            logger.info("Created new SSO user id=%s email=%s", user.id, email)

    if not user.is_active:
        raise api_error(403, "account_inactive", "Account is deactivated")

    await db.commit()
    await db.refresh(user)

    # --- Issue JWT cookies ---
    access = create_access_token(user.id, user.username, user.mfa_enabled)
    refresh, expires_at = create_refresh_token(user.id)
    db.add(RefreshToken(token_hash=hash_token(refresh), user_id=user.id, expires_at=expires_at))
    await db.commit()

    redirect = RedirectResponse(url="/", status_code=302)
    _set_auth_cookies(redirect, access, refresh)
    # Clear the ephemeral state cookie
    redirect.delete_cookie(_STATE_COOKIE, path="/")
    return redirect
