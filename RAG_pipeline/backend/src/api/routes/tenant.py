"""Per-deployment tenant identity (public, no auth).

One-VPS-per-client model: each deployment's `.env` sets `TENANT_NAME`,
`TENANT_SLUG`, and `TENANT_ENVIRONMENT`. The frontend fetches this once at
boot and threads it through the sidebar wordmark, browser title, login page,
and onboarding wizard chrome.

No auth required — the login page needs to render branded BEFORE the user
signs in.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from src.config import settings

router = APIRouter(prefix="/tenant", tags=["tenant"])


class TenantConfigResponse(BaseModel):
    name: str
    slug: str
    environment: str  # development | staging | production


@router.get("/config", response_model=TenantConfigResponse)
async def get_tenant_config() -> TenantConfigResponse:
    return TenantConfigResponse(
        name=settings.tenant_name,
        slug=settings.tenant_slug,
        environment=settings.tenant_environment,
    )
