# -*- coding: utf-8 -*-
"""Regression tests for IFindFetcher realtime quote source labeling.

iFind 成功取数时不应再被误标为 FALLBACK（曾导致报告误报"降级兜底"）。
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock

if "fake_useragent" not in sys.modules:
    sys.modules["fake_useragent"] = MagicMock()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.ifind_fetcher import IFindFetcher
from data_provider.realtime_types import RealtimeSource


def _ifind_tool_response():
    """模拟 iFind fund_highfreq_quotes 的真实返回结构。"""
    header = ["code", "name", "time", "最新价", "最高", "最低", "涨跌", "涨跌幅", "成交量", "成交额"]
    row = ["512070", "证券保险ETF易方达", "2026-08-04 15:00:00", 0.804, 0.813, 0.800, -0.009, -1.11, 6079500, 489000000]
    payload = {
        "data": json.dumps(
            {
                "tables": [
                    header,
                    row,
                ]
            },
            ensure_ascii=False,
        )
    }
    return json.dumps(payload, ensure_ascii=False)


class IFindFetcherSourceTestCase(unittest.TestCase):
    def setUp(self):
        self.fetcher = IFindFetcher()

    def test_realtime_quote_source_is_ifind_not_fallback(self):
        """iFind 成功返回行情时 source 应为 ifind，而非 fallback。"""
        self.fetcher._call_tool = MagicMock(return_value=_ifind_tool_response())

        quote = self.fetcher.get_realtime_quote("512070")

        self.assertIsNotNone(quote)
        assert quote is not None
        self.assertEqual(quote.source, RealtimeSource.IFIND)
        self.assertEqual(quote.source.value, "ifind")
        self.assertEqual(quote.price, 0.804)
        self.assertEqual(quote.code, "512070")

    def test_realtime_quote_empty_tool_response_returns_none(self):
        self.fetcher._call_tool = MagicMock(return_value="")

        self.assertIsNone(self.fetcher.get_realtime_quote("512070"))


if __name__ == "__main__":
    unittest.main()
