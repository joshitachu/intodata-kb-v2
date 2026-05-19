"""Entry point: --mcp for MCP stdio server, --serve for FastAPI REST API."""

from __future__ import annotations

import argparse
import asyncio
import os


def main() -> None:
    parser = argparse.ArgumentParser(prog="mcp_server")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--mcp", action="store_true", help="Run as MCP stdio server")
    group.add_argument("--serve", action="store_true", help="Run as FastAPI REST API")
    group.add_argument(
        "--backfill-costs",
        action="store_true",
        help="Backfill GenerateRun.cost_usd from configured model pricing",
    )
    # Default host stays 0.0.0.0 so the Dockerized MCP API can bind inside its
    # container. Production compose deployments publish the port on 127.0.0.1
    # and expose public webhook traffic through the same-host reverse proxy.
    parser.add_argument("--host", default="0.0.0.0", help="API host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8002, help="API port (default: 8002)")
    parser.add_argument(
        "--static-dir",
        default=None,
        help="Path to dashboard dist/ directory to serve static files",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable Uvicorn auto-reload for local development",
    )
    parser.add_argument(
        "--project-id",
        default=None,
        help="Optional project id for --backfill-costs",
    )
    args = parser.parse_args()

    if args.mcp:
        from mcp_server.mcp.server import run_mcp_server
        run_mcp_server()
    elif args.backfill_costs:
        async def _backfill() -> int:
            from mcp_server.db.session import async_session_factory
            from mcp_server.services.project_registry import ProjectRegistry

            async with async_session_factory() as db:
                registry = ProjectRegistry(db)
                updated = await registry.backfill_generate_run_costs(args.project_id)
                await db.commit()
                return updated

        updated = asyncio.run(_backfill())
        scope = f" for project {args.project_id!r}" if args.project_id else ""
        print(f"Backfilled {updated} generate run cost(s){scope}.")
    else:
        if args.static_dir:
            os.environ["DASHBOARD_DIST_DIR"] = args.static_dir

        import uvicorn
        from mcp_server.api.app import app  # noqa: F401
        uvicorn.run(
            "mcp_server.api.app:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )


if __name__ == "__main__":
    main()
