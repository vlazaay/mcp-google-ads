"""Thin HTTP client wrapping auth + headers for Google Ads REST.

Introduced during the package-structure refactor to give future
mutate-side code a single place to hang retry/timeout/logging
concerns.

The read-only tools in `creavy_ads.tools.read_queries` (and friends)
still call `get_credentials()` / `get_headers()` inline — that code
path was deliberately left alone during the refactor. All write/
mutate tools in `creavy_ads.tools.mutate` route through this client.
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

    def mutate(
        self,
        customer_id: str,
        resource: str,
        operations: list[dict],
        validate_only: bool = True,
        partial_failure: bool = True,
        response_content_type: str = "RESOURCE_NAME_ONLY",
    ) -> dict:
        """Execute a mutate against a Google Ads resource collection.

        Thin wrapper around
        ``POST /{API_VERSION}/customers/{id}/{resource}:mutate``. Does
        exactly one HTTP call — no retries, no side effects, no waiting.
        Callers (the `@mcp.tool()` methods in
        `creavy_ads.tools.mutate`) are responsible for read-before-write
        checks and for normalising the response envelope.

        Args:
            customer_id: Google Ads customer ID in any format accepted
                by ``format_customer_id``.
            resource: Resource collection name in the URL path (e.g.
                ``"campaigns"``, ``"campaignBudgets"``,
                ``"campaignCriteria"``, ``"adGroups"``,
                ``"adGroupAds"``).
            operations: List of operation payloads (each a dict with
                ``create`` / ``update`` / ``remove`` and, for updates,
                an ``updateMask``).
            validate_only: If True (default), Google validates the
                request but does not apply it. ALL mutate tools must
                keep this as the default until an explicit apply is
                requested.
            partial_failure: If True (default), a single bad operation
                in a multi-op call does not kill the rest.
            response_content_type: ``"RESOURCE_NAME_ONLY"`` (cheap,
                default) or ``"MUTABLE_RESOURCE"`` (returns the full
                mutated object — useful for diffs, costs more bytes).

        Returns:
            The parsed JSON response body on 2xx. On non-2xx, a
            synthetic dict ``{"error": <text>, "status_code": <int>}``
            so callers do not have to catch requests.HTTPError.
        """
        formatted_customer_id = format_customer_id(customer_id)
        url = (
            f"https://googleads.googleapis.com/{API_VERSION}"
            f"/customers/{formatted_customer_id}/{resource}:mutate"
        )
        payload = {
            "operations": operations,
            "partialFailure": partial_failure,
            "validateOnly": validate_only,
            "responseContentType": response_content_type,
        }
        logger.info(
            "mutate %s resource=%s ops=%d validate_only=%s partial_failure=%s",
            formatted_customer_id,
            resource,
            len(operations),
            validate_only,
            partial_failure,
        )
        response = requests.post(url, headers=self.headers, json=payload)
        if response.ok:
            return response.json() or {}
        return {"error": response.text, "status_code": response.status_code}
