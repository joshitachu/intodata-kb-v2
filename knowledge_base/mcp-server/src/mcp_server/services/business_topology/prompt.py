"""Prompt contract for the additive business-topology synthesis task."""

from __future__ import annotations

import json
from pathlib import Path

from mcp_server.providers.custom.prompt_renderer import get_renderer
from mcp_server.services.business_topology.models import BusinessTopology


def business_topology_prompt(
    *,
    project_id: str,
    scan_fingerprint: str,
    plugin_pack_dir: Path | None = None,
) -> str:
    """Render the Phase 3.7 synthesis contract.

    The body lives in ``data/prompt_templates/phase37_business_topology.md`` so
    a pack can replace it like any other phase prompt. It was an inline
    f-string, which made Phase 3.7 the one LLM phase no pack could reach.
    """
    return get_renderer().render("phase37_business_topology.md", {
        "PROJECT_ID": project_id,
        "SCAN_FINGERPRINT": scan_fingerprint,
        "SCHEMA": json.dumps(BusinessTopology.model_json_schema(), indent=2),
    }, pack_dir=plugin_pack_dir)


def flow_extraction_prompt(
    *,
    subproject: str,
    plugin_pack_dir: Path | None = None,
) -> str:
    """Render the Stage A flow-extraction contract.

    Stage A asks only for the architecture in business language; ids, statuses,
    artifact hashes and coverage are derived in
    :mod:`mcp_server.services.business_topology.assembly`. The template carries
    no ``SCHEMA`` slot on purpose -- injecting the full published JSON schema is
    what pulled the model back into emitting bookkeeping it cannot get right.
    """

    return get_renderer().render(
        "phase37_flow_extraction.md",
        {"SUBPROJECT": subproject},
        pack_dir=plugin_pack_dir,
    )
