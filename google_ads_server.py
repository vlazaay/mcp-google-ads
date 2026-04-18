"""Backwards-compatibility shim for the upstream entrypoint.

The implementation moved into the `creavy_ads/` package. Re-exporting
the public names here keeps existing call sites working:

    uv run google_ads_server.py
    python google_ads_server.py
    from google_ads_server import format_customer_id, list_accounts, ...

For new code, import from `creavy_ads` directly.
"""

# Re-export the FastMCP server instance and entrypoint.
from creavy_ads.server import main, mcp

# Re-export auth helpers (used by tests and downstream callers).
from creavy_ads.auth import (
    format_customer_id,
    get_credentials,
    get_headers,
    get_oauth_credentials,
    get_service_account_credentials,
)

# Re-export config constants for backwards compatibility.
from creavy_ads.config import (
    API_VERSION,
    GOOGLE_ADS_AUTH_TYPE,
    GOOGLE_ADS_CREDENTIALS_PATH,
    GOOGLE_ADS_DEVELOPER_TOKEN,
    GOOGLE_ADS_LOGIN_CUSTOMER_ID,
    SCOPES,
)

# Force tool registration so `google_ads_server.list_accounts` etc. resolve.
from creavy_ads.tools import (  # noqa: F401
    assets,
    creatives,
    read_queries,
    resources_and_prompts,
)

# Re-export tool callables for tests that do `google_ads_server.list_accounts(...)`.
from creavy_ads.tools.assets import (  # noqa: E402
    analyze_image_assets,
    download_image_asset,
    get_asset_usage,
    get_image_assets,
)
from creavy_ads.tools.creatives import (  # noqa: E402
    get_account_currency,
    get_ad_creatives,
)
from creavy_ads.tools.read_queries import (  # noqa: E402
    execute_gaql_query,
    get_ad_performance,
    get_campaign_performance,
    list_accounts,
    list_resources,
    run_gaql,
)


if __name__ == "__main__":
    main()
