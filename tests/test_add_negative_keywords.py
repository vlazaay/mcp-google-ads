"""Unit tests for ``creavy_ads.tools.mutate.add_negative_keywords``."""

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from creavy_ads.tools import mutate as mutate_module


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class AddNegativeKeywordsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_client = MagicMock(name="GoogleAdsClient")
        # Default: no existing negatives.
        self.fake_client.search.return_value = {"results": []}
        self.fake_client.mutate.return_value = {"results": []}

        patcher = patch.object(mutate_module, "GoogleAdsClient", return_value=self.fake_client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_invalid_match_type(self) -> None:
        result = _run(mutate_module.add_negative_keywords(
            customer_id="9873186703",
            campaign_id="555",
            keywords=["free"],
            match_type="FUZZY",
        ))
        self.assertFalse(result["success"])
        self.assertIn("invalid match_type", result["warnings"][0])
        self.fake_client.mutate.assert_not_called()

    def test_trims_and_dedups_input(self) -> None:
        _run(mutate_module.add_negative_keywords(
            customer_id="9873186703",
            campaign_id="555",
            keywords=["  free  ", "Free", "", "trial", "TRIAL"],
        ))
        kwargs = self.fake_client.mutate.call_args.kwargs
        ops = kwargs["operations"]
        # Expect 2 operations (free + trial), input case/whitespace normalised.
        self.assertEqual(len(ops), 2)
        texts = [op["create"]["keyword"]["text"] for op in ops]
        self.assertEqual(sorted(texts), ["free", "trial"])

    def test_operation_payload_shape(self) -> None:
        _run(mutate_module.add_negative_keywords(
            customer_id="9873186703",
            campaign_id="555",
            keywords=["free"],
            match_type="PHRASE",
        ))
        kwargs = self.fake_client.mutate.call_args.kwargs
        self.assertEqual(kwargs["resource"], "campaignCriteria")
        op = kwargs["operations"][0]["create"]
        self.assertEqual(op["campaign"], "customers/9873186703/campaigns/555")
        self.assertTrue(op["negative"])
        self.assertEqual(op["keyword"]["text"], "free")
        self.assertEqual(op["keyword"]["matchType"], "PHRASE")
        self.assertTrue(kwargs["validate_only"])

    def test_skips_existing_negatives(self) -> None:
        self.fake_client.search.return_value = {
            "results": [{
                "campaignCriterion": {
                    "keyword": {"text": "Free", "matchType": "EXACT"}
                }
            }]
        }
        result = _run(mutate_module.add_negative_keywords(
            customer_id="9873186703",
            campaign_id="555",
            keywords=["free", "trial"],
        ))
        kwargs = self.fake_client.mutate.call_args.kwargs
        ops = kwargs["operations"]
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["create"]["keyword"]["text"], "trial")
        self.assertTrue(any("skip duplicate" in w for w in result["warnings"]))

    def test_all_duplicates_short_circuits(self) -> None:
        self.fake_client.search.return_value = {
            "results": [
                {"campaignCriterion": {"keyword": {"text": "free", "matchType": "EXACT"}}},
                {"campaignCriterion": {"keyword": {"text": "trial", "matchType": "EXACT"}}},
            ]
        }
        result = _run(mutate_module.add_negative_keywords(
            customer_id="9873186703",
            campaign_id="555",
            keywords=["free", "trial"],
        ))
        self.fake_client.mutate.assert_not_called()
        self.assertTrue(result["success"])
        self.assertEqual(result["resource_names"], [])

    def test_exceeds_50_per_call_limit(self) -> None:
        keywords = [f"kw{i}" for i in range(55)]
        result = _run(mutate_module.add_negative_keywords(
            customer_id="9873186703",
            campaign_id="555",
            keywords=keywords,
        ))
        kwargs = self.fake_client.mutate.call_args.kwargs
        ops = kwargs["operations"]
        self.assertEqual(len(ops), 50)
        self.assertTrue(any("per-call limit" in w for w in result["warnings"]))

    def test_empty_after_trim_fails(self) -> None:
        result = _run(mutate_module.add_negative_keywords(
            customer_id="9873186703",
            campaign_id="555",
            keywords=["", "   ", "\t"],
        ))
        self.assertFalse(result["success"])
        self.assertIn("no non-empty keywords", result["warnings"][0])
        self.fake_client.mutate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
