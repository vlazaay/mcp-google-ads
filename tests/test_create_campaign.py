"""Unit tests for ``creavy_ads.tools.mutate.create_campaign``."""

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from creavy_ads.tools import mutate as mutate_module


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_client_with_sequence(*step_returns):
    """Build a fake client whose mutate() returns the given responses in order.

    One item per expected mutate call (budget, campaign, criteria).
    """
    client = MagicMock(name="GoogleAdsClient")
    client.mutate.side_effect = list(step_returns)
    return client


class CreateCampaignTests(unittest.TestCase):
    def test_blank_name_rejected(self) -> None:
        fake = _make_client_with_sequence()
        with patch.object(mutate_module, "GoogleAdsClient", return_value=fake):
            result = _run(mutate_module.create_campaign(
                customer_id="9873186703",
                name="   ",
                daily_budget_micros=50_000_000,
            ))
        self.assertFalse(result["success"])
        self.assertTrue(any("name is required" in w for w in result["warnings"]))
        fake.mutate.assert_not_called()

    def test_non_positive_budget_rejected(self) -> None:
        fake = _make_client_with_sequence()
        with patch.object(mutate_module, "GoogleAdsClient", return_value=fake):
            result = _run(mutate_module.create_campaign(
                customer_id="9873186703",
                name="CREAVY — UA",
                daily_budget_micros=0,
            ))
        self.assertFalse(result["success"])
        fake.mutate.assert_not_called()

    def test_manual_cpc_requires_ceiling_in_config(self) -> None:
        fake = _make_client_with_sequence()
        with patch.object(mutate_module, "GoogleAdsClient", return_value=fake):
            result = _run(mutate_module.create_campaign(
                customer_id="9873186703",
                name="CREAVY — UA",
                daily_budget_micros=50_000_000,
                # config provides only partial overrides; cpc_bid_ceiling_micros missing
                config={"bid_strategy": "MANUAL_CPC", "cpc_bid_ceiling_micros": 0},
            ))
        self.assertFalse(result["success"])
        self.assertTrue(any("cpc_bid_ceiling_micros" in w for w in result["warnings"]))
        fake.mutate.assert_not_called()

    def test_happy_path_three_step_sequence(self) -> None:
        budget_resp = {"results": [{"resourceName": "customers/9873186703/campaignBudgets/777"}]}
        campaign_resp = {"results": [{"resourceName": "customers/9873186703/campaigns/555"}]}
        criteria_resp = {
            "results": [
                {"resourceName": "customers/9873186703/campaignCriteria/555~1"},
                {"resourceName": "customers/9873186703/campaignCriteria/555~2"},
                {"resourceName": "customers/9873186703/campaignCriteria/555~3"},
            ]
        }
        fake = _make_client_with_sequence(budget_resp, campaign_resp, criteria_resp)
        with patch.object(mutate_module, "GoogleAdsClient", return_value=fake):
            result = _run(mutate_module.create_campaign(
                customer_id="9873186703",
                name="CREAVY — UA Search v1",
                daily_budget_micros=50_000_000,
                config={"cpc_bid_ceiling_micros": 400_000},
            ))
        self.assertTrue(result["success"])
        self.assertEqual(fake.mutate.call_count, 3)

        # Step 1 — budget.
        call_budget = fake.mutate.call_args_list[0].kwargs
        self.assertEqual(call_budget["resource"], "campaignBudgets")
        self.assertEqual(call_budget["operations"][0]["create"]["amountMicros"], "50000000")

        # Step 2 — campaign.
        call_campaign = fake.mutate.call_args_list[1].kwargs
        self.assertEqual(call_campaign["resource"], "campaigns")
        camp = call_campaign["operations"][0]["create"]
        self.assertEqual(camp["status"], "PAUSED")
        self.assertEqual(camp["advertisingChannelType"], "SEARCH")
        self.assertEqual(camp["campaignBudget"], "customers/9873186703/campaignBudgets/777")
        self.assertEqual(camp["bidCeilingMicros"], "400000")

        # Step 3 — criteria: 1 geo + 2 langs = 3 ops.
        call_crit = fake.mutate.call_args_list[2].kwargs
        self.assertEqual(call_crit["resource"], "campaignCriteria")
        self.assertEqual(len(call_crit["operations"]), 3)
        first = call_crit["operations"][0]["create"]
        self.assertEqual(first["campaign"], "customers/9873186703/campaigns/555")
        self.assertEqual(first["location"]["geoTargetConstant"], "geoTargetConstants/1012959")
        langs = [op["create"]["language"]["languageConstant"] for op in call_crit["operations"][1:]]
        self.assertIn("languageConstants/1002", langs)
        self.assertIn("languageConstants/1000", langs)

        # Resource names aggregated from all three steps.
        self.assertIn("customers/9873186703/campaignBudgets/777", result["resource_names"])
        self.assertIn("customers/9873186703/campaigns/555", result["resource_names"])
        self.assertEqual(
            sum(1 for rn in result["resource_names"] if "campaignCriteria" in rn),
            3,
        )

    def test_step_2_failure_surfaces_orphan_budget(self) -> None:
        budget_resp = {"results": [{"resourceName": "customers/9873186703/campaignBudgets/777"}]}
        campaign_resp_err = {"error": '{"code":400,"msg":"bad"}', "status_code": 400}
        fake = _make_client_with_sequence(budget_resp, campaign_resp_err)
        with patch.object(mutate_module, "GoogleAdsClient", return_value=fake):
            result = _run(mutate_module.create_campaign(
                customer_id="9873186703",
                name="CREAVY — UA",
                daily_budget_micros=50_000_000,
                config={"cpc_bid_ceiling_micros": 400_000},
            ))
        self.assertFalse(result["success"])
        # Budget resource name still reported for cleanup visibility.
        self.assertIn("customers/9873186703/campaignBudgets/777", result["resource_names"])
        self.assertTrue(any("needs cleanup" in w or "cleanup" in w for w in result["warnings"]))
        # Step 3 was NOT attempted.
        self.assertEqual(fake.mutate.call_count, 2)

    def test_validate_only_synthesises_placeholder_refs(self) -> None:
        # In validate_only mode Google returns empty results. We should still
        # be able to build step 2 and step 3 requests with placeholder refs.
        budget_resp = {"results": []}
        campaign_resp = {"results": []}
        criteria_resp = {"results": []}
        fake = _make_client_with_sequence(budget_resp, campaign_resp, criteria_resp)
        with patch.object(mutate_module, "GoogleAdsClient", return_value=fake):
            result = _run(mutate_module.create_campaign(
                customer_id="9873186703",
                name="CREAVY — UA Validate",
                daily_budget_micros=50_000_000,
                config={"cpc_bid_ceiling_micros": 400_000},
                validate_only=True,
            ))
        self.assertTrue(result["success"])
        # Step 2 payload references the placeholder budget.
        step2 = fake.mutate.call_args_list[1].kwargs
        self.assertEqual(
            step2["operations"][0]["create"]["campaignBudget"],
            "customers/9873186703/campaignBudgets/VALIDATE_ONLY",
        )
        # Step 3 uses placeholder campaign ref.
        step3 = fake.mutate.call_args_list[2].kwargs
        self.assertEqual(
            step3["operations"][0]["create"]["campaign"],
            "customers/9873186703/campaigns/VALIDATE_ONLY",
        )


if __name__ == "__main__":
    unittest.main()
