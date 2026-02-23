"""Entry point: --mcp for MCP stdio server, --serve for FastAPI REST API."""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="mcp_server")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--mcp", action="store_true", help="Run as MCP stdio server")
    group.add_argument("--serve", action="store_true", help="Run as FastAPI REST API on :8001")
    parser.add_argument("--host", default="0.0.0.0", help="API host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8001, help="API port (default: 8001)")
    args = parser.parse_args()

    if args.mcp:
        from mcp_server.mcp.server import run_mcp_server
        run_mcp_server()
    else:
        import uvicorn
        from mcp_server.api.app import app  # noqa: F401
        uvicorn.run(
            "mcp_server.api.app:app",
            host=args.host,
            port=args.port,
            reload=True,
        )


if __name__ == "__main__":
    main()
