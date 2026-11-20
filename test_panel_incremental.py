# -*- coding: utf-8 -*-
"""Offline checks for incremental panel updates and the Actions ML backend guard."""

import os
import unittest
from datetime import timedelta
from unittest import mock

import pandas as pd

import ml_predictor_5y as mlp


def _bars(ticker, days, closes):
    return pd.DataFrame({
        "Date": pd.to_datetime(days),
        "Ticker": [ticker] * len(days),
        "Open": closes,
        "High": closes,
        "Low": closes,
        "Close": closes,
        "Volume": [100.0] * len(days),
    })


class IncrementalPanelTests(unittest.TestCase):
    def test_stitch_keeps_older_history_and_replaces_the_overlap(self):
        old = _bars("NVDA", ["2026-11-17", "2026-11-18", "2026-11-19"], [10.0, 11.0, 12.0])
        fetched = _bars("NVDA", ["2026-11-18", "2026-11-19", "2026-11-20"], [11.0, 12.1, 13.0])
        merged = mlp._stitch_symbol_history(old, fetched)
        got = list(pd.to_datetime(merged["Date"]).dt.strftime("%Y-%m-%d"))
        self.assertEqual(got, ["2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20"])
        self.assertEqual(float(merged.loc[merged["Date"] == "2026-11-17", "Close"].iloc[0]), 10.0)
        self.assertEqual(float(merged.loc[merged["Date"] == "2026-11-19", "Close"].iloc[0]), 12.1)
        self.assertFalse(mlp._closes_diverged(old, fetched))

    def test_split_scale_is_detected_from_older_overlap(self):
        days = ["2026-11-17", "2026-11-18", "2026-11-19"]
        old = _bars("NVDA", days, [100.0, 102.0, 101.0])
        new = _bars("NVDA", days, [10.0, 10.2, 10.1])
        self.assertTrue(mlp._closes_diverged(old, new))

    def test_fresh_symbol_does_not_download(self):
        day = mlp._symbol_expected_day("NVDA")
        frame = _bars("NVDA", [day.strftime("%Y-%m-%d")], [100.0])

        def _boom(*_args, **_kwargs):
            raise AssertionError("fresh cache must not download")

        with mock.patch.object(mlp, "_fetch_single_ticker", _boom):
            out, n_fetched = mlp._incremental_refresh_frame(frame, ["NVDA"], "2021-11-01")
        self.assertEqual(n_fetched, 0)
        self.assertEqual(len(out), 1)

    def test_lagging_symbol_stitches_the_tail_only(self):
        expected = mlp._get_latest_expected_trading_day()
        older = expected - timedelta(days=10)
        frame = _bars("NVDA", [older.strftime("%Y-%m-%d")], [50.0])
        fetched = _bars(
            "NVDA",
            [older.strftime("%Y-%m-%d"), expected.strftime("%Y-%m-%d")],
            [50.0, 55.0],
        )
        with mock.patch.object(mlp, "_fetch_single_ticker", return_value=fetched):
            out, n_fetched = mlp._incremental_refresh_frame(frame, ["NVDA"], "2021-11-01")
        self.assertEqual(n_fetched, 1)
        days = list(pd.to_datetime(out["Date"]).dt.strftime("%Y-%m-%d"))
        self.assertEqual(days[0], older.strftime("%Y-%m-%d"))
        self.assertEqual(days[-1], expected.strftime("%Y-%m-%d"))
        self.assertEqual(float(out.iloc[0]["Close"]), 50.0)
        self.assertEqual(float(out.iloc[-1]["Close"]), 55.0)

    def test_actions_refuses_numpy_fallback(self):
        with mock.patch.object(mlp, "lgb", None):
            with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
                with self.assertRaises(RuntimeError):
                    mlp._refuse_actions_numpy_fallback()
            with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": ""}, clear=False):
                mlp._refuse_actions_numpy_fallback()


if __name__ == "__main__":
    unittest.main()
