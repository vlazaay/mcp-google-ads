"""Unit tests for ``creavy_ads.tools.mutate.create_responsive_search_ad``."""

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from creavy_ads.tools import mutate as mutate_module


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _valid_headlines(n: int = 3):
    return [f"Headline {i}" for i in range(1, n + 1)]


def _valid_descriptions(n: int = 2):
    return [f"Description {i} with enough length" for i in range(1, n + 1)]


class CreateResponsiveSearchAdTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_client = MagicMock(name="GoogleAdsClient")
        self.fake_client.mutate.return_value = {
            "results": [{"resourceName": "customers/9873186703/adGroupAds/aaa"}]
        }
        patcher = patch.object(mutate_module, "GoogleAdsClient", return_value=self.fake_client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_too_few_headlines_rejected(self) -> None:
        result = _run(mutate_module.create_responsive_search_ad(
            customer_id="9873186703",
            ad_group_id="4242",
            headlines=["Only one"],
            descriptions=_valid_descriptions(),
            final_urls=["https://creavy.agency/"],
        ))
        self.assertFalse(result["success"])
        self.assertTrue(any("headlines:" in w for w in result["warnings"]))

    def test_headline_too_long_rejected(self) -> None:
        result = _run(mutate_module.create_responsive_search_ad(
            customer_id="9873186703",
            ad_group_id="4242",
            headlines=["x" * 31, "ok", "ok2"],
            descriptions=_valid_descriptions(),
            final_urls=["https://creavy.agency/"],
        ))
        self.assertFalse(result["success"])
        self.assertTrue(any("31 chars" in w for w in result["warnings"]))

    def test_description_too_long_rejected(self) -> None:
        result = _run(mutate_module.create_responsive_search_ad(
            customer_id="9873186703",
            ad_group_id="4242",
            headlines=_valid_headlines(),
            descriptions=["x" * 91, "ok"],
            final_urls=["https://creavy.agency/"],
        ))
        self.assertFalse(result["success"])
        self.assertTrue(any("91 chars" in w for w in result["warnings"]))

    def test_cyrillic_path_rejected(self) -> None:
        result = _run(mutate_module.create_responsive_search_ad(
            customer_id="9873186703",
            ad_group_id="4242",
            headlines=_valid_headlines(),
            descriptions=_valid_descriptions(),
            final_urls=["https://creavy.agency/"],
            path1="кий",
        ))
        self.assertFalse(result["success"])
        self.assertTrue(any("path1" in w and "ASCII" in w for w in result["warnings"]))

    def test_no_final_url_rejected(self) -> None:
        result = _run(mutate_module.create_responsive_search_ad(
            customer_id="9873186703",
            ad_group_id="4242",
            headlines=_valid_headlines(),
            descriptions=_valid_descriptions(),
            final_urls=[],
        ))
        self.assertFalse(result["success"])

    def test_pinned_field_invalid_rejected(self) -> None:
        headlines = [
            {"text": "a", "pinned_field": "HEADLINE_99"},
            "b", "c",
        ]
        result = _run(mutate_module.create_responsive_search_ad(
            customer_id="9873186703",
            ad_group_id="4242",
            headlines=headlines,
            descriptions=_valid_descriptions(),
            final_urls=["https://creavy.agency/"],
        ))
        self.assertFalse(result["success"])
        self.assertTrue(any("pinned_field" in w for w in result["warnings"]))

    def test_happy_path_payload(self) -> None:
        headlines = [
            {"text": "CREAVY", "pinned_field": "HEADLINE_1"},
            "веб-студія Київ",
            "Сайти під ключ",
            "Ads + SEO",
        ]
        descriptions = [
            "Розробка сайтів і лендінгів",
            "Сайт готовий за 2 тижні",
        ]
        result = _run(mutate_module.create_responsive_search_ad(
            customer_id="9873186703",
            ad_group_id="4242",
            headlines=headlines,
            descriptions=descriptions,
            final_urls=["https://creavy.agency/"],
            path1="kyiv",
            path2="web-studio",
        ))
        self.assertTrue(result["success"])
        kwargs = self.fake_client.mutate.call_args.kwargs
        op = kwargs["operations"][0]["create"]
        self.assertEqual(op["adGroup"], "customers/9873186703/adGroups/4242")
        self.assertEqual(op["status"], "PAUSED")
        rsa = op["ad"]["responsiveSearchAd"]
        # First headline is pinned.
        self.assertEqual(rsa["headlines"][0]["pinnedField"], "HEADLINE_1")
        self.assertEqual(rsa["headlines"][0]["text"], "CREAVY")
        # Unpinned headlines don't carry a pinnedField key.
        self.assertNotIn("pinnedField", rsa["headlines"][1])
        self.assertEqual(rsa["path1"], "kyiv")
        self.assertEqual(rsa["path2"], "web-studio")
        self.assertEqual(op["ad"]["finalUrls"], ["https://creavy.agency/"])
        self.assertEqual(kwargs["resource"], "adGroupAds")


if __name__ == "__main__":
    unittest.main()
