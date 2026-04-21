"""Unit tests for ``creavy_ads.tools.mutate.enable_campaign``.

Focus:
    - Spend-cap gate always runs first; refuses when cap is missing.
    - Already-ENABLED short-circuits.
    - Mutate URL/payload matches Google REST v23 spec.
    - Missing campaign surfaces a failure envelope.
"""

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from creavy_ads.tools import mutate as mutate_module


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class EnableCampaignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_client = MagicMock(name="GoogleAdsClient")
        # Two search() calls happen in success path:
        #   1. spend-cap query — return one APPROVED row
        #   2. campaign pre-check — return non-enabled campaign
        def _search(customer_id, query):
            if "FROM account_budget" in query:
                return {"results": [{"accountBudget": {"status": "APPROVED"}}]}
            if "FROM campaign" in query:
                return {"results": [{
                    "campaign": {
                        "id": "555",
                        "name": "CREAVY — UA Search v1",
                        "status": "PAUSED",
                    }
                }]}
            return {"results": []}
        self.fake_client.search.side_effect = _search
        self.fake_client.mutate.return_value = {"results": []}

        patcher = patch.object(mutate_module, "GoogleAdsClient", return_value=self.fake_client)
        self.mock_client_cls = patcher.start()
        self.addCleanup(patcher.stop)

    def test_spend_cap_missing_refuses(self) -> None:
        def _search(customer_id, query):
            if "FROM account_budget" in query:
                return {"results": []}  # no cap
            return {"results": []}
        self.fake_client.search.side_effect = _search

        result = _run(mutate_module.enable_campaign(
            customer_id="9873186703",
            campaign_id="555",
        ))
        self.assertFalse(result["success"])
        self.assertIn("refused", result["warnings"][0])
        self.fake_client.mutate.assert_not_called()

    def test_happy_path_validate_only(self) -> None:
        result = _run(mutate_module.enable_campaign(
            customer_id="9873186703",
            campaign_id="555",
        ))
        self.assertTrue(result["success"])
        self.assertTrue(result["validate_only"])

        kwargs = self.fake_client.mutate.call_args.kwargs
        self.assertEqual(kwargs["resource"], "campaigns")
        ops = kwargs["operations"]
        self.assertEqual(ops[0]["update"]["status"], "ENABLED")
        self.assertEqual(ops[0]["updateMask"], "status")
        self.assertTrue(kwargs["validate_only"])
        # Spend-cap detail appended to warnings.
        self.assertTrue(any("spend-cap ok" in w for w in result["warnings"]))

    def test_already_enabled_short_circuits(self) -> None:
        def _search(customer_id, query):
            if "FROM account_budget" in query:
                return {"results": [{"accountBudget": {"status": "APPROVED"}}]}
            return {"results": [{
                "campaign": {"id": "555", "status": "ENABLED"}
            }]}
        self.fake_client.search.side_effect = _search

        result = _run(mutate_module.enable_campaign(
            customer_id="9873186703",
            campaign_id="555",
        ))
        self.assertTrue(result["success"])
        self.assertIn("already ENABLED", result["warnings"][0])
        self.fake_client.mutate.assert_not_called()

    def test_campaign_not_found(self) -> None:
        def _search(customer_id, query):
            if "FROM account_budget" in query:
                return {"results": [{"accountBudget": {"status": "APPROVED"}}]}
            return {"results": []}
        self.fake_client.search.side_effect = _search

        result = _run(mutate_module.enable_campaign(
            customer_id="9873186703",
            campaign_id="999",
        ))
        self.assertFalse(result["success"])
        self.assertIn("not found", result["warnings"][0])
        self.fake_client.mutate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
