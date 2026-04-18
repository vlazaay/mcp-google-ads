"""Unit tests for ``creavy_ads.tools.mutate.pause_campaign``.

Goals:
    1. Confirm the GAQL pre-check is issued before any mutate call.
    2. Confirm the mutate URL and payload shape match Google Ads REST
       v19 spec for a campaigns:mutate update.
    3. Confirm validate_only=True is the default and is plumbed
       through to the HTTP layer.
    4. Confirm the "already PAUSED" no-op short-circuits the mutate.
    5. Confirm a "campaign not found" pre-check surfaces as a failure
       envelope.

Tests use ``unittest`` from the stdlib so no extra dev dependency is
required. They mock ``creavy_ads.tools.mutate.GoogleAdsClient`` so that
neither auth nor the network is touched.
"""

import asyncio
import unittest
from unittest.mock import MagicMock, patch

# Importing the module triggers `@mcp.tool()` registration. We pull the
# undecorated coroutine out of FastMCP's registry so that we can call it
# directly with kwargs in tests.
from creavy_ads.tools import mutate as mutate_module
from creavy_ads.server import mcp


def _get_pause_campaign_callable():
    """Resolve the underlying coroutine for the registered MCP tool.

    FastMCP wraps registered functions; the original coroutine is still
    importable from the module, so we just call it directly.
    """
    return mutate_module.pause_campaign


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class PauseCampaignTests(unittest.TestCase):
    def setUp(self) -> None:
        # A fresh fake client per test — pre-canned to a non-paused
        # campaign so that the mutate code path runs by default.
        self.fake_client = MagicMock(name="GoogleAdsClient")
        self.fake_client.search.return_value = {
            "results": [{
                "campaign": {
                    "id": "555",
                    "name": "CREAVY — UA Search v1",
                    "status": "ENABLED",
                }
            }]
        }
        # Default mutate response: success, no resource name (validate_only).
        self.fake_client.mutate.return_value = {"results": []}

        patcher = patch.object(mutate_module, "GoogleAdsClient", return_value=self.fake_client)
        self.mock_client_cls = patcher.start()
        self.addCleanup(patcher.stop)

    def test_default_is_validate_only(self) -> None:
        result = _run(_get_pause_campaign_callable()(
            customer_id="987-318-6703",
            campaign_id="555",
        ))
        self.assertTrue(result["validate_only"])
        # Mutate was called and validate_only=True went through.
        self.fake_client.mutate.assert_called_once()
        kwargs = self.fake_client.mutate.call_args.kwargs
        self.assertTrue(kwargs["validate_only"])

    def test_pre_check_query_runs_first(self) -> None:
        _run(_get_pause_campaign_callable()(
            customer_id="9873186703",
            campaign_id="555",
        ))
        # search() called exactly once with the expected GAQL shape.
        self.fake_client.search.assert_called_once()
        args = self.fake_client.search.call_args.args
        self.assertEqual(args[0], "9873186703")
        self.assertIn("FROM campaign", args[1])
        self.assertIn("campaign.id = 555", args[1])

    def test_mutate_url_components_and_payload(self) -> None:
        _run(_get_pause_campaign_callable()(
            customer_id="9873186703",
            campaign_id="555",
        ))
        kwargs = self.fake_client.mutate.call_args.kwargs
        self.assertEqual(kwargs["customer_id"], "9873186703")
        self.assertEqual(kwargs["resource"], "campaigns")

        ops = kwargs["operations"]
        self.assertEqual(len(ops), 1)
        op = ops[0]
        self.assertIn("update", op)
        self.assertEqual(
            op["update"]["resourceName"],
            "customers/9873186703/campaigns/555",
        )
        self.assertEqual(op["update"]["status"], "PAUSED")
        self.assertEqual(op["updateMask"], "status")

    def test_already_paused_short_circuits(self) -> None:
        self.fake_client.search.return_value = {
            "results": [{
                "campaign": {
                    "id": "555",
                    "name": "CREAVY — UA Search v1",
                    "status": "PAUSED",
                }
            }]
        }
        result = _run(_get_pause_campaign_callable()(
            customer_id="9873186703",
            campaign_id="555",
        ))
        self.assertTrue(result["success"])
        self.assertIn("already PAUSED", result["warnings"][0])
        self.fake_client.mutate.assert_not_called()
        self.assertEqual(
            result["resource_names"],
            ["customers/9873186703/campaigns/555"],
        )

    def test_campaign_not_found_returns_failure_envelope(self) -> None:
        self.fake_client.search.return_value = {"results": []}
        result = _run(_get_pause_campaign_callable()(
            customer_id="9873186703",
            campaign_id="999",
        ))
        self.assertFalse(result["success"])
        self.assertIn("not found", result["warnings"][0])
        self.fake_client.mutate.assert_not_called()

    def test_http_error_normalised_into_envelope(self) -> None:
        self.fake_client.mutate.return_value = {
            "error": "{\"error\":{\"code\":403,\"message\":\"forbidden\"}}",
            "status_code": 403,
        }
        result = _run(_get_pause_campaign_callable()(
            customer_id="9873186703",
            campaign_id="555",
        ))
        self.assertFalse(result["success"])
        self.assertIn("HTTP 403", result["warnings"][0])
        self.assertEqual(result["resource_names"], [])

    def test_partial_failure_extracted(self) -> None:
        self.fake_client.mutate.return_value = {
            "results": [{"resourceName": "customers/9873186703/campaigns/555"}],
            "partialFailureError": {
                "details": [{
                    "errors": [{
                        "errorCode": {"requestError": "INVALID"},
                        "message": "something is off",
                        "location": {"fieldPathElements": [{"fieldName": "status"}]},
                    }]
                }]
            },
        }
        result = _run(_get_pause_campaign_callable()(
            customer_id="9873186703",
            campaign_id="555",
        ))
        self.assertTrue(result["success"])
        self.assertEqual(len(result["partial_failures"]), 1)
        self.assertEqual(result["partial_failures"][0]["message"], "something is off")


if __name__ == "__main__":
    unittest.main()
