"""FastMCP server entrypoint for the CREAVY Ads MCP package.

The `mcp` instance is created at import-time so that tool modules can
do `from creavy_ads.server import mcp` and register their `@mcp.tool()`
decorators at their own import-time.

`main()` then imports the tool modules (lazily, to avoid a circular
import during `server.py`'s own top-level execution) and starts the
stdio transport.
"""

import logging

from mcp.server.fastmcp import FastMCP

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('google_ads_server')

mcp = FastMCP(
    "google-ads-server",
    dependencies=[
        "google-auth-oauthlib",
        "google-auth",
        "requests",
        "python-dotenv"
    ]
)


def _register_tools() -> None:
    """Import tool modules so their `@mcp.tool()` decorators run.

    Done inside a function (not at module top-level) to avoid a
    circular import: tool modules do `from creavy_ads.server import mcp`.
    """
    # Importing for side-effects (decorator registration).
    from creavy_ads.tools import (  # noqa: F401
        assets,
        creatives,
        read_queries,
        resources_and_prompts,
    )


def main() -> None:
    """Register tools and start the MCP server on stdio transport."""
    _register_tools()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
