"""CREAVY Google Ads MCP package.

Refactored from the upstream cohnen/mcp-google-ads monolith
(`google_ads_server.py`) into a package layout so that mutate-side
tools can be added cleanly without bloating a single file.

Backwards-compatibility: importing `google_ads_server` still works via
the shim at the repo root.
"""

from creavy_ads.server import mcp, main

__all__ = ["mcp", "main"]
