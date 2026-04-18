"""Unit tests for ``creavy_ads.tools.mutate.update_campaign_budget``."""

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from creavy_ads.tools import mutate as mutate_module


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class UpdateCampaignBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_client = MagicMock(name="GoogleAdsClient")
        # Current budget = 50 UAH/day
        self.fake_client.search.return_value = {
            "results": [{
                "campaignBudget": {
                    "id": "777",
                    "amountMicros": "50000000",
                    "name": "CREAVY — daily",
                }
            }]
        }
        self.fake_client.mutate.return_value = {"results": []}

        patcher = patch.object(mutate_module, "GoogleAdsClient", return_value=self.fake_client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rejects_non_positive_amount(self) -> None:
        result = _run(mutate_module.update_campaign_budget(
            customer_id="9873186703",
            campaign_budget_id="777",
            amount_micros=0,
        ))
        self.assertFalse(result["success"])
        self.assertIn("positive integer", result["warnings"][0])
        self.fake_client.mutate.assert_not_called()

    def test_small_change_within_threshold(self) -> None:
        # 50 -> 60 UAH is +20%, within the +/-50% guard.
        result = _run(mutate_module.update_campaign_budget(
            customer_id="9873186703",
            campaign_budget_id="777",
            amount_micros=60_000_000,
        ))
        self.assertTrue(result["success"])
        kwargs = self.fake_client.mutate.call_args.kwargs
        self.assertEqual(kwargs["resource"], "campaignBudgets")
        op = kwargs["operations"][0]
        self.assertEqual(
            op["update"]["resourceName"],
            "customers/9873186703/campaignBudgets/777",
        )
        self.assertEqual(op["update"]["amountMicros"], "60000000")
        self.assertEqual(op["updateMask"], "amount_micros")
        # Audit line first
        self.assertIn("before=50000000", result["warnings"][0])
        self.assertIn("after=60000000", result["warnings"][0])

    def test_large_change_without_force_refuses(self) -> None:
        # 50 -> 120 UAH is +140%, well above +50%.
        result = _run(mutate_module.update_campaign_budget(
            customer_id="9873186703",
            campaign_budget_id="777",
            amount_micros=120_000_000,
        ))
        self.assertFalse(result["success"])
        self.assertTrue(any("refused" in w and "force=True" in w for w in result["warnings"]))
        self.fake_client.mutate.assert_not_called()

    def test_large_change_with_force_applies(self) -> None:
        result = _run(mutate_module.update_campaign_budget(
            customer_id="9873186703",
            campaign_budget_id="777",
            amount_micros=120_000_000,
            force=True,
        ))
        self.assertTrue(result["success"])
        self.fake_client.mutate.assert_called_once()

    def test_missing_budget(self) -> None:
        self.fake_client.search.return_value = {"results": []}
        result = _run(mutate_module.update_campaign_budget(
            customer_id="9873186703",
            campaign_budget_id="999",
            amount_micros=60_000_000,
        ))
        self.assertFalse(result["success"])
        self.assertIn("not found", result["warnings"][0])
        self.fake_client.mutate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
