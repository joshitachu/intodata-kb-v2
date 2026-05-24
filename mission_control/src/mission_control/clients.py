"""Per-client config: VPS URL and control key for remote triggering.

Config file: mission_control/clients.json
Format:
{
  "koopmans": {
    "control_key": "some-secret-control-key"
  }
}

The mcp_url is read from the heartbeat payload (sent by the VPS itself).
The control_key must be configured here and must match MCP_OPS_CONTROL_KEY
on the target VPS.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent.parent / "clients.json"


def _config_path() -> Path:
    import os
    return Path(os.environ.get("MC_CLIENTS_CONFIG", str(_DEFAULT_PATH)))


def load_client_config(slug: str) -> dict[str, Any]:
    path = _config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data.get(slug, {})
    except Exception as exc:
        logger.warning("Failed to read clients.json: %s", exc)
        return {}
