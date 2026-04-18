"""Thin HTTP client wrapping auth + headers for Google Ads REST.

Introduced during the package-structure refactor to give future
mutate-side code a single place to hang retry/timeout/logging
concerns.

NOTE: the existing read-only tools in `creavy_ads.tools.*` still call
`get_credentials()` / `get_headers()` and `requests.post()` inline,
exactly as they did in the monolith. The refactor commit deliberately
does not touch that code path; `GoogleAdsClient` is infrastructure
for the upcoming mutate tools and is not yet used by any registered
tool.
"""

import logging

import requests

from creavy_ads.auth import format_customer_id, get_credentials, get_headers
from creavy_ads.config import API_VERSION

logger = logging.getLogger('google_ads_server')


class GoogleAdsClient:
    """Minimal wrapper around credentials + HTTP headers for Google Ads.

    Attributes:
        creds: Google auth credentials (OAuth or service account).
        headers: Dict of HTTP headers including dev token and bearer.
    """

    def __init__(self, creds=None):
        self.creds = creds if creds is not None else get_credentials()
        self.headers = get_headers(self.creds)

    def search(self, customer_id: str, query: str) -> dict:
        """Execute a GAQL query via the `googleAds:search` endpoint.

        Args:
            customer_id: Google Ads customer ID (any format accepted by
                `format_customer_id`).
            query: A GAQL query string.

        Returns:
            The parsed JSON response body.

        Raises:
            requests.HTTPError: on non-2xx response.
        """
        formatted_customer_id = format_customer_id(customer_id)
        url = (
            f"https://googleads.googleapis.com/{API_VERSION}"
            f"/customers/{formatted_customer_id}/googleAds:search"
        )
        response = requests.post(url, headers=self.headers, json={"query": query})
        response.raise_for_status()
        return response.json()
