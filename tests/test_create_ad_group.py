"""Unit tests for ``creavy_ads.tools.mutate.create_ad_group``."""

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from creavy_ads.tools import mutate as mutate_module


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class CreateAdGroupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_client = MagicMock(name="GoogleAdsClient")
        self.fake_client.mutate.return_value = {
            "results": [{"resourceName": "customers/9873186703/adGroups/4242"}]
        }
        patcher = patch.object(mutate_module, "GoogleAdsClient", return_value=self.fake_client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_blank_name_rejected(self) -> None:
        result = _run(mutate_module.create_ad_group(
            customer_id="9873186703",
            campaign_id="555",
            name="   ",
            cpc_bid_micros=400_000,
        ))
        self.assertFalse(result["success"])
        self.fake_client.mutate.assert_not_called()

    def test_non_positive_bid_rejected(self) -> None:
        result = _run(mutate_module.create_ad_group(
            customer_id="9873186703",
            campaign_id="555",
            name="UA - Broad Match",
            cpc_bid_micros=0,
        ))
        self.assertFalse(result["success"])
        self.fake_client.mutate.assert_not_called()

    def test_invalid_status_rejected(self) -> None:
        result = _run(mutate_module.create_ad_group(
            customer_id="9873186703",
            campaign_id="555",
            name="UA",
            cpc_bid_micros=400_000,
            status="REMOVED",
        ))
        self.assertFalse(result["success"])
        self.fake_client.mutate.assert_not_called()

    def test_default_status_is_paused(self) -> None:
        _run(mutate_module.create_ad_group(
            customer_id="9873186703",
            campaign_id="555",
            name="  UA - Broad Match  ",
            cpc_bid_micros=400_000,
        ))
        kwargs = self.fake_client.mutate.call_args.kwargs
        self.assertEqual(kwargs["resource"], "adGroups")
        op = kwargs["operations"][0]["create"]
        self.assertEqual(op["status"], "PAUSED")
        self.assertEqual(op["type"], "SEARCH_STANDARD")
        self.assertEqual(op["name"], "UA - Broad Match")
        self.assertEqual(op["cpcBidMicros"], "400000")
        self.assertEqual(op["campaign"], "customers/9873186703/campaigns/555")

    def test_resource_name_propagated(self) -> None:
        result = _run(mutate_module.create_ad_group(
            customer_id="9873186703",
            campaign_id="555",
            name="UA",
            cpc_bid_micros=400_000,
        ))
        self.assertTrue(result["success"])
        self.assertIn("customers/9873186703/adGroups/4242", result["resource_names"])


if __name__ == "__main__":
    unittest.main()
