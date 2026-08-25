from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter

from src.api.dependencies import CurrentUser
from src.runtime.models import RuntimeSourceTypeInfo
from src.runtime.providers.base import editable_config_fields
from src.runtime.source_registry import all_source_providers

router = APIRouter(prefix="/runtime", tags=["runtime-sources"])


class RuntimeSourceTypesResponse(BaseModel):
    source_types: list[RuntimeSourceTypeInfo]


@router.get("/source-types", response_model=RuntimeSourceTypesResponse)
async def list_runtime_source_types(_user: CurrentUser) -> RuntimeSourceTypesResponse:
    return RuntimeSourceTypesResponse(
        source_types=[
            RuntimeSourceTypeInfo(
                provider_key=provider.provider_key(),
                provider_label=provider.provider_label(),
                credential_schema=provider.credential_schema(),
                scope_selection=provider.scope_selection(),
                editable_config_fields=editable_config_fields(provider),
            )
            for provider in all_source_providers()
        ]
    )
