"""Unit tests for ``creavy_ads.tools.mutate.update_campaign_bid``."""

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from creavy_ads.tools import mutate as mutate_module


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class UpdateCampaignBidTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_client = MagicMock(name="GoogleAdsClient")
        self.fake_client.mutate.return_value = {"results": []}
        # Default: plenty of conversions so auto strategies pass the gate.
        def _search(cid, query):
            if "metrics.conversions" in query:
                return {"results": [{"metrics": {"conversions": 35}}]}
            return {"results": []}
        self.fake_client.search.side_effect = _search

        patcher = patch.object(mutate_module, "GoogleAdsClient", return_value=self.fake_client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_invalid_strategy_rejected(self) -> None:
        result = _run(mutate_module.update_campaign_bid(
            customer_id="9873186703",
            campaign_id="555",
            bid_strategy="TARGET_ROAS",
        ))
        self.assertFalse(result["success"])
        self.assertIn("bid_strategy must be one of", result["warnings"][0])
        self.fake_client.mutate.assert_not_called()

    def test_manual_cpc_requires_ceiling(self) -> None:
        result = _run(mutate_module.update_campaign_bid(
            customer_id="9873186703",
            campaign_id="555",
            bid_strategy="MANUAL_CPC",
        ))
        self.assertFalse(result["success"])
        self.assertIn("cpc_bid_ceiling_micros", result["warnings"][0])
        self.fake_client.mutate.assert_not_called()

    def test_manual_cpc_payload(self) -> None:
        result = _run(mutate_module.update_campaign_bid(
            customer_id="9873186703",
            campaign_id="555",
            bid_strategy="MANUAL_CPC",
            cpc_bid_ceiling_micros=400_000,
        ))
        self.assertTrue(result["success"])
        kwargs = self.fake_client.mutate.call_args.kwargs
        op = kwargs["operations"][0]
        self.assertEqual(op["update"]["bidCeilingMicros"], "400000")
        self.assertIn("bid_ceiling_micros", op["updateMask"])

    def test_target_cpa_requires_amount(self) -> None:
        result = _run(mutate_module.update_campaign_bid(
            customer_id="9873186703",
            campaign_id="555",
            bid_strategy="TARGET_CPA",
        ))
        self.assertFalse(result["success"])
        self.assertIn("target_cpa_micros", result["warnings"][0])
        self.fake_client.mutate.assert_not_called()

    def test_auto_strategy_blocked_without_conversions(self) -> None:
        def _search(cid, query):
            if "metrics.conversions" in query:
                return {"results": [{"metrics": {"conversions": 4}}]}
            return {"results": []}
        self.fake_client.search.side_effect = _search
        result = _run(mutate_module.update_campaign_bid(
            customer_id="9873186703",
            campaign_id="555",
            bid_strategy="MAXIMIZE_CONVERSIONS",
        ))
        self.assertFalse(result["success"])
        self.assertIn("refused", result["warnings"][0])
        self.fake_client.mutate.assert_not_called()

    def test_auto_strategy_ok_with_conversions(self) -> None:
        result = _run(mutate_module.update_campaign_bid(
            customer_id="9873186703",
            campaign_id="555",
            bid_strategy="TARGET_CPA",
            target_cpa_micros=8_000_000,
        ))
        self.assertTrue(result["success"])
        kwargs = self.fake_client.mutate.call_args.kwargs
        op = kwargs["operations"][0]
        self.assertEqual(op["update"]["targetCpa"]["targetCpaMicros"], "8000000")
        self.assertIn("target_cpa.target_cpa_micros", op["updateMask"])
        self.assertTrue(any("conversions check ok" in w for w in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
