"""MCP tool modules.

Each submodule imports `mcp` from `creavy_ads.server` and registers
its tools via `@mcp.tool()` / `@mcp.resource()` / `@mcp.prompt()` at
import-time. Import these modules from `creavy_ads.server.main()` to
perform registration.
"""
