"""MCP tool modules.

Each submodule imports ``mcp`` from ``creavy_ads.server`` and registers
its tools via ``@mcp.tool()`` / ``@mcp.resource()`` / ``@mcp.prompt()``
at import-time. Import these modules from ``creavy_ads.server.main()``
to perform registration.

Submodules:
    read_queries:            read-only GAQL-backed tools (list_accounts,
                             execute_gaql_query, get_campaign_performance,
                             get_ad_performance, run_gaql, list_resources).
    creatives:               ad-creative helpers (get_ad_creatives, …).
    assets:                  image asset tools.
    resources_and_prompts:   MCP resource + prompt declarations.
    mutate:                  write tools (pause_campaign, …). All default
                             to validate_only=True.
"""
