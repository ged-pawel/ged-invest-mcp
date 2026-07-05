"""Ged Invest MCP server.

A single MCP server that hosts a growing set of construction tools. Each tool
domain lives in its own submodule and registers its tools via a `register(mcp)`
function, so adding new tool domains later is straightforward.

Run locally (stdio) - for Claude Desktop / Cursor:
    python -m ged_invest_mcp.server
    ged-invest-mcp

Run over HTTP - for ChatGPT (custom connector) and remote clients:
    python -m ged_invest_mcp.server --http                # 0.0.0.0:8000/mcp
    python -m ged_invest_mcp.server --http --port 9000
    python -m ged_invest_mcp.server --transport sse       # legacy SSE transport
"""

from __future__ import annotations

import argparse
import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from . import formwork

mcp = FastMCP("ged-invest")


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> PlainTextResponse:
    """Liveness probe for cloud hosts (Render/Railway/Fly)."""
    return PlainTextResponse("ok")


# Register tool domains. Add new domains here as the server grows.
formwork.register(mcp)


def main() -> None:
    """Entry point - selects the transport (stdio by default, or HTTP/SSE)."""
    parser = argparse.ArgumentParser(
        prog="ged-invest-mcp",
        description="Ged Invest MCP server (construction tools).",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "sse"],
        default="stdio",
        help="Transport: stdio (Claude/Cursor), http (ChatGPT/remote), sse (legacy).",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Shortcut for --transport http (streamable HTTP).",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST", "0.0.0.0"),
        help="Host for HTTP/SSE mode (default: $HOST or 0.0.0.0).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8000")),
        help="Port for HTTP/SSE mode (default: $PORT or 8000).",
    )
    args = parser.parse_args()

    # Cloud hosts (Render, Railway, Fly, Cloud Run) inject $PORT and often want
    # HTTP; allow selecting the transport via $MCP_TRANSPORT too.
    transport = "http" if args.http else os.environ.get("MCP_TRANSPORT", args.transport)

    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    mcp.settings.host = args.host
    mcp.settings.port = args.port

    # FastMCP defaults to host=127.0.0.1 and enables localhost-only DNS rebinding
    # protection. On cloud hosts the public Host header (e.g. *.onrender.com) would
    # then be rejected with HTTP 421. Reconfigure before starting HTTP transport.
    allowed_raw = os.environ.get("MCP_ALLOWED_HOSTS", "").strip()
    if allowed_raw:
        hosts = [h.strip() for h in allowed_raw.split(",") if h.strip()]
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
        )
    else:
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        )
    mcp._session_manager = None  # lazy init; must pick up new security settings

    if transport == "http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="sse")


if __name__ == "__main__":
    main()
