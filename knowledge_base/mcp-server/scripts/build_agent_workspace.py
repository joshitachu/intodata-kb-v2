#!/usr/bin/env python3
"""Stage an ``agent-workspace-v1`` workspace for a project. No model calls.

This is the dry run for the whole lane: it exercises the real builder against
a real scan and produces the exact tree an agent would be handed, so a
workspace defect is found for free instead of at provider-token prices.

    python scripts/build_agent_workspace.py <project_id> <dest> [--print-invocation]

``--print-invocation`` additionally prints the request the runner would hand a
provider adapter -- the workspace root, the writable paths, the tool set --
without constructing one.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from mcp_server.providers.agent_workspace.contract import (
    DEFAULT_TOOLS,
    AgentRunRequest,
    WorkspaceUnavailableError,
    writable_globs,
)
from mcp_server.providers.agent_workspace.workspace import build_workspace


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project_id")
    parser.add_argument("dest", type=Path)
    parser.add_argument(
        "--platform",
        default="talend",
        help="scan platform; selects the semantics guide (default: talend)",
    )
    parser.add_argument(
        "--goal",
        default="",
        help="the goal written into GOAL.md; the lane default is used when empty",
    )
    parser.add_argument(
        "--print-invocation",
        action="store_true",
        help="also print the provider request and permission map",
    )
    args = parser.parse_args()

    try:
        receipt = build_workspace(
            project_id=args.project_id,
            staging_dir=args.dest,
            platform=args.platform,
            goal=args.goal,
        )
    except WorkspaceUnavailableError as exc:
        print(f"workspace unavailable: {exc}")
        return 2

    print(json.dumps(json.loads(receipt.model_dump_json()), indent=2))

    if args.print_invocation:
        request = AgentRunRequest.for_workspace(
            args.dest.resolve(),
            run_id="dry-run",
            project_id=args.project_id,
        )
        print()
        print(
            json.dumps(
                {
                    "request": json.loads(request.model_dump_json()),
                    "allowed_tools": list(DEFAULT_TOOLS),
                    "permissions": writable_globs(args.dest.resolve()),
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
