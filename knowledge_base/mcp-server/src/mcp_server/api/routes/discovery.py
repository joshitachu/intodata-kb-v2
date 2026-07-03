"""Discovery endpoints for dashboard metadata."""

from __future__ import annotations

from fastapi import APIRouter

from mcp_server.config import config
from mcp_server.metadata.models import list_models
from mcp_server.metadata.platforms import list_platforms
from mcp_server.model_strategies import resolve_generation_models
from mcp_server.schemas import ModelListResponse, PlatformListResponse
from mcp_server.services import instance_settings_service

router = APIRouter(tags=["discovery"])


@router.get("/platforms", response_model=PlatformListResponse)
async def get_platforms() -> PlatformListResponse:
    return PlatformListResponse(
        platforms=[p.to_dict() for p in list_platforms()]
    )


@router.get("/models", response_model=ModelListResponse)
async def get_models() -> ModelListResponse:
    # Honor per-instance overrides so the dashboard's default markers reflect
    # the live effective model after a superadmin save.
    analysis_default = await instance_settings_service.get_value(
        "doc_gen_analysis_model", default=config.doc_generation_model
    )
    write_default = await instance_settings_service.get_value(
        "doc_gen_write_model", default=config.doc_generation_write_model
    )
    defaults = resolve_generation_models(
        provider_type=config.doc_generation_provider,
        model=None,
        write_model=None,
        default_provider=config.doc_generation_provider,
        default_analysis_model=analysis_default or config.doc_generation_model,
        default_write_model=write_default or config.doc_generation_write_model,
        default_claude_cli_model=config.claude_model,
    )
    return ModelListResponse(
        models=[
            m.to_dict()
            for m in list_models(
                default_analysis_model="claude-cli" if defaults.uses_claude_cli else defaults.analysis_model,
                default_investigation_model=config.doc_generation_investigation_model or defaults.analysis_model,
                default_writing_model=defaults.write_model,
            )
        ]
    )
