# -*- coding: utf-8 -*-
"""Unit tests for JP/KR Yahoo Finance market-review index mappings."""

import os
import sys
import unittest
from unittest.mock import MagicMock

import pandas as pd

if 'fake_useragent' not in sys.modules:
    sys.modules['fake_useragent'] = MagicMock()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def _make_mock_hist(close: float = 100.0, prev_close: float = 98.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            'Close': [prev_close, close],
            'Open': [prev_close - 1, close - 1],
            'High': [prev_close + 2, close + 2],
            'Low': [prev_close - 2, close - 2],
            'Volume': [1000.0, 1200.0],
        },
        index=pd.DatetimeIndex(['2026-03-26', '2026-03-27']),
    )


def _make_mock_yf(hist_df: pd.DataFrame):
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = hist_df
    mock_yf = MagicMock()
    mock_yf.Ticker.return_value = mock_ticker
    return mock_yf


class TestJpKrIndexMappings(unittest.TestCase):
    def setUp(self):
        from data_provider.yfinance_fetcher import YfinanceFetcher
        self.fetcher = YfinanceFetcher()

    def test_jp_indices_use_expected_yahoo_symbols(self):
        mock_yf = _make_mock_yf(pd.DataFrame())

        self.fetcher._get_jp_main_indices(mock_yf)

        ticker_calls = [call.args[0] for call in mock_yf.Ticker.call_args_list]
        self.assertEqual(ticker_calls, ['^N225', '^TOPX'])

    def test_kr_indices_use_expected_yahoo_symbols(self):
        mock_yf = _make_mock_yf(pd.DataFrame())

        self.fetcher._get_kr_main_indices(mock_yf)

        ticker_calls = [call.args[0] for call in mock_yf.Ticker.call_args_list]
        self.assertEqual(ticker_calls, ['^KS11', '^KQ11'])

    def test_jp_indices_return_expected_codes_when_data_available(self):
        result = self.fetcher._get_jp_main_indices(_make_mock_yf(_make_mock_hist()))

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual([item['code'] for item in result], ['N225', 'TOPX'])
        self.assertEqual([item['name'] for item in result], ['日经225', '东证指数'])

    def test_kr_indices_return_expected_codes_when_data_available(self):
        result = self.fetcher._get_kr_main_indices(_make_mock_yf(_make_mock_hist()))

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual([item['code'] for item in result], ['KS11', 'KQ11'])
        self.assertEqual([item['name'] for item in result], ['KOSPI', 'KOSDAQ'])

    def test_jp_kr_indices_return_none_when_all_empty(self):
        mock_yf = _make_mock_yf(pd.DataFrame())

        self.assertIsNone(self.fetcher._get_jp_main_indices(mock_yf))
        self.assertIsNone(self.fetcher._get_kr_main_indices(mock_yf))

    def test_kr_index_uses_fast_info_when_latest_close_is_null(self):
        """KOSPI 当日 Close 为 null 时，应回退到 fast_info 实时价，避免 nan。"""
        hist = pd.DataFrame(
            {
                'Close': [6595.45, float('nan')],
                'Open': [5657.79, 6358.27],
                'High': [6630.77, 6393.00],
                'Low': [5629.76, 6223.29],
                'Volume': [434400.0, 272959.0],
            },
            index=pd.DatetimeIndex(['2026-07-31', '2026-08-03']),
        )
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = hist
        mock_ticker.fast_info = {'last_price': 6257.45, 'previous_close': 5593.56}
        mock_yf = MagicMock()
        mock_yf.Ticker.return_value = mock_ticker

        result = self.fetcher._get_kr_main_indices(mock_yf)

        self.assertIsNotNone(result)
        assert result is not None
        kospi = next(item for item in result if item['code'] == 'KS11')
        self.assertEqual(kospi['current'], 6257.45)
        # 昨收应取 hist 前一交易日 6595.45（非空），而非 fast_info.previous_close
        self.assertEqual(kospi['prev_close'], 6595.45)
        # (6257.45 - 6595.45) / 6595.45 ≈ -5.12%
        self.assertAlmostEqual(kospi['change_pct'], -5.124, places=2)
        # 所有字段不得出现 nan
        for value in kospi.values():
            self.assertFalse(isinstance(value, float) and pd.isna(value))

    def test_kr_index_uses_previous_close_when_hist_has_single_row(self):
        """日韩指数 period=2d 只返回单日时，昨收应用 fast_info.previous_close，避免涨跌幅恒 0.00%。"""
        hist = pd.DataFrame(
            {
                'Close': [63957.53],
                'Open': [63995.28],
                'High': [64240.50],
                'Low': [62864.43],
                'Volume': [272959.0],
            },
            index=pd.DatetimeIndex(['2026-08-04']),
        )
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = hist
        mock_ticker.fast_info = {'last_price': 63957.53, 'previous_close': 63754.9}
        mock_yf = MagicMock()
        mock_yf.Ticker.return_value = mock_ticker

        result = self.fetcher._get_jp_main_indices(mock_yf)

        self.assertIsNotNone(result)
        assert result is not None
        n225 = next(item for item in result if item['code'] == 'N225')
        self.assertEqual(n225['current'], 63957.53)
        self.assertEqual(n225['prev_close'], 63754.9)
        # (63957.53 - 63754.9) / 63754.9 ≈ +0.32%
        self.assertAlmostEqual(n225['change_pct'], 0.318, places=2)


if __name__ == '__main__':
    unittest.main()
