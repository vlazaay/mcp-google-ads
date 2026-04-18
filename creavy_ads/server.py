"""FastMCP server entrypoint for the CREAVY Ads MCP package.

The `mcp` instance is created at import-time so that tool modules can
do `from creavy_ads.server import mcp` and register their `@mcp.tool()`
decorators at their own import-time.

`main()` then imports the tool modules (lazily, to avoid a circular
import during `server.py`'s own top-level execution) and starts the
configured transport. Transport is selected via the `MCP_TRANSPORT`
environment variable (default: "stdio", also supports "sse").
"""

import logging
import os

from mcp.server.fastmcp import FastMCP

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('google_ads_server')


def _build_mcp() -> FastMCP:
    """Construct a FastMCP instance.

    For SSE transport, FastMCP needs host/port configured on the
    constructor (not on `.run()`), so we read env vars here and pass
    them in. For stdio transport the host/port are ignored but harmless.
    """
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("MCP_PORT", "8765"))
    except ValueError:
        port = 8765
    return FastMCP(
        "google-ads-server",
        host=host,
        port=port,
        dependencies=[
            "google-auth-oauthlib",
            "google-auth",
            "requests",
            "python-dotenv",
        ],
    )


mcp = _build_mcp()


def _register_tools() -> None:
    """Import tool modules so their `@mcp.tool()` decorators run.

    Done inside a function (not at module top-level) to avoid a
    circular import: tool modules do `from creavy_ads.server import mcp`.
    """
    # Importing for side-effects (decorator registration).
    from creavy_ads.tools import (  # noqa: F401
        assets,
        creatives,
        mutate,
        read_queries,
        resources_and_prompts,
    )


def main() -> None:
    """Register tools and start the MCP server on the configured transport."""
    _register_tools()
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport == "sse":
        logger.info(
            "Starting MCP server on SSE transport at %s:%s",
            mcp.settings.host,
            mcp.settings.port,
        )
        mcp.run(transport="sse")
    else:
        logger.info("Starting MCP server on stdio transport")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
