#!/usr/bin/env python3
"""PCBGenius — D9 LIVE COMPONENT CATALOG (feature #14): tests.

Offline tests for the catalog search fallback. Two queries must return ranked,
relevant, IN-STOCK results in the contract shape:

    {"results": [{"mpn","manufacturer","specs","stock","price","package"}, ...]}

Runs with the pure-Python fallback (no Meilisearch/Typesense, no network),
so it works anywhere with just stdlib.
"""

from __future__ import annotations

import unittest

from index import build  # same package dir, no __init__ needed here


KEY_ROWS = {"mpn", "manufacturer", "specs", "stock", "price", "package"}


class TestCatalogSearch(unittest.TestCase):
    def setUp(self):
        self.catalog = build()  # seeded with the offline sample catalog

    def _assert_contract(self, payload, query):
        self.assertIn("results", payload)
        self.assertIsInstance(payload["results"], list)
        self.assertTrue(payload["results"], f"no results for query: {query!r}")
        for row in payload["results"]:
            self.assertTrue(KEY_ROWS <= set(row), f"row missing keys: {row}")

    def test_query_buck_regulator(self):
        """Query 1: 'buck 3A regulator' -> top hit is an in-stock buck."""
        payload = self.catalog.search("buck 3A regulator")
        self._assert_contract(payload, "buck 3A regulator")
        results = payload["results"]

        top = results[0]
        # Ranked: the in-stock buck (MP1584EN) must outrank the out-of-stock
        # buck (LM2596-5.0) even though both are relevant.
        self.assertEqual(top["mpn"], "MP1584EN")
        self.assertGreater(top["stock"], 0)
        self.assertIn("buck", " ".join(top["specs"].values()) + top["package"] + top["mpn"])
        # first result must be in-stock
        self.assertGreater(results[0]["stock"], 0,
                           msg="top ranked result should be in-stock")

    def test_query_capacitor(self):
        """Query 2: '100nF capacitor 0603' -> relevant in-stock ceramic cap."""
        payload = self.catalog.search("100nF capacitor 0603")
        self._assert_contract(payload, "100nF capacitor 0603")
        results = payload["results"]

        top = results[0]
        # C0603C104K5RACTU (KEMET, in stock) must outrank GRM188... (Murata, 0
        # stock) because relevance ties are broken by availability.
        self.assertEqual(top["mpn"], "C0603C104K5RACTU")
        self.assertGreater(top["stock"], 0)
        self.assertIn("capacitance", top["specs"])

    def test_no_result_returns_empty_list(self):
        payload = self.catalog.search("zqwyx-nonexistent-part")
        self.assertEqual(payload["results"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)