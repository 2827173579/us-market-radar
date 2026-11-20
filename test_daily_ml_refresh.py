"""Daily AI panel refresh: pyarrow is required, a failed child is not success, and a stale snapshot is not mixed with live quotes."""

import os
import unittest
from datetime import datetime
from unittest import mock

import pandas as pd

import radar_cloud_sync as sync
from create_futu_terminal import ai_daily_snapshot_is_stale, equity_cycle_bar_is_stale
import ml_predictor_5y as mlp


ROOT = os.path.dirname(os.path.abspath(__file__))


class DailyRefreshContractTests(unittest.TestCase):
    def test_requirements_install_pyarrow_for_the_actions_image(self):
        with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as handle:
            requirements = handle.read()
        with open(os.path.join(ROOT, ".github/workflows/market_radar.yml"), encoding="utf-8") as handle:
            workflow = handle.read()
        self.assertIn("pyarrow", requirements)
        self.assertIn("pip install -r requirements.txt", workflow)
        self.assertIn("python-version: '3.10'", workflow)

    def test_publish_list_includes_panel_artifacts(self):
        required = {
            "data_cache/latest_ai_predictions.json",
            "data_cache/ml_panel_5y.parquet",
            "data_cache/ml_extended_panel.parquet",
            "data_cache/.ml_panel_max_date",
            "data_cache/.ml_btc_utc_date",
            "data_cache/.last_daily_ml_refresh",
            "predict_radar_5y.html",
        }
        self.assertTrue(required.issubset(set(sync.DAILY_PANEL_GIT_PATHS)))
        self.assertTrue(required.issubset(set(sync.RADAR_PUBLISH_PATHS)))
        with open(os.path.join(ROOT, "integrated_market_radar.py"), encoding="utf-8") as handle:
            radar = handle.read()
        self.assertIn("radar_cloud_sync.RADAR_PUBLISH_PATHS", radar)
        self.assertIn("daily_ml_refresh_succeeded", radar)
        self.assertIn("_exit_if_daily_ml_refresh_failed()", radar)
        self.assertIn("maybe_refresh_daily_ml_panel()", radar)

    def test_nonzero_predictor_is_never_a_successful_refresh(self):
        self.assertFalse(sync.daily_ml_refresh_succeeded(1, "2026-11-19", "2026-11-19", "2026-11-19", "2026-11-19"))
        self.assertFalse(sync.daily_ml_refresh_succeeded(2, "2026-11-19", "2026-11-19", "2026-11-19", "2026-11-19"))
        self.assertTrue(sync.daily_ml_refresh_succeeded(0, "2026-11-19", "", "", "2026-11-19"))
        self.assertTrue(sync.daily_ml_refresh_succeeded(0, "", "2026-11-19", "", "2026-11-19"))
        self.assertFalse(sync.daily_ml_refresh_succeeded(0, "2026-11-17", "2026-11-17", "2026-11-17", "2026-11-19"))
        self.assertFalse(sync.daily_ml_refresh_succeeded(0, "", "", "", "2026-11-19"))
        self.assertFalse(sync.daily_ml_refresh_succeeded(0, "2026-11-19", "2026-11-19", "2026-11-19", ""))

    def test_stale_daily_snapshot_is_not_treated_as_current(self):
        stale = {"macro_weather": {"latest_date": "2026-11-17", "status": "🌧️ 暴雨空仓"}}
        current = {"macro_weather": {"latest_date": "2026-11-19"}}
        self.assertTrue(ai_daily_snapshot_is_stale(stale, "2026-11-19"))
        self.assertFalse(ai_daily_snapshot_is_stale(current, "2026-11-19"))
        self.assertTrue(ai_daily_snapshot_is_stale({"macro_weather": {}}, "2026-11-19"))
        self.assertFalse(ai_daily_snapshot_is_stale(stale, ""))
        self.assertTrue(equity_cycle_bar_is_stale("2026-11-17", "2026-11-19"))
        self.assertFalse(equity_cycle_bar_is_stale("2026-11-19", "2026-11-19"))
        self.assertFalse(equity_cycle_bar_is_stale("", "2026-11-19"))


class RecentBarSpliceTests(unittest.TestCase):
    def test_short_window_fills_a_long_download_that_stopped_early(self):
        base = pd.DataFrame({
            "Date": pd.to_datetime(["2026-11-17", "2026-11-18"]),
            "Ticker": ["LITE", "LITE"],
            "Open": [1.0, 1.0],
            "High": [1.2, 1.2],
            "Low": [0.9, 0.9],
            "Close": [1.1, 1.05],
            "Volume": [100.0, 110.0],
        })
        recent = pd.DataFrame({
            "Open": [1.0, 1.0],
            "High": [1.3, 1.4],
            "Low": [0.95, 0.96],
            "Close": [1.2, 1.25],
            "Volume": [120.0, 130.0],
        }, index=pd.to_datetime(["2026-11-18", "2026-11-19"]))
        recent.index.name = "Date"
        with mock.patch.object(mlp.yf, "download", return_value=recent) as download:
            merged = mlp._splice_recent_daily_bars(base, "LITE", datetime(2026, 11, 19).date())
        download.assert_called_once()
        self.assertEqual(pd.to_datetime(merged["Date"]).max().date().isoformat(), "2026-11-19")
        self.assertEqual(len(merged), 3)
        nov18 = merged.loc[merged["Date"] == pd.Timestamp("2026-11-18"), "Close"].iloc[0]
        self.assertEqual(float(nov18), 1.2)

    def test_splice_leaves_a_current_frame_unchanged(self):
        base = pd.DataFrame({
            "Date": pd.to_datetime(["2026-11-19"]),
            "Ticker": ["QQQ"],
            "Open": [1.0],
            "High": [1.0],
            "Low": [1.0],
            "Close": [1.0],
            "Volume": [1.0],
        })
        with mock.patch.object(mlp.yf, "download") as download:
            merged = mlp._splice_recent_daily_bars(base, "QQQ", datetime(2026, 11, 19).date())
        download.assert_not_called()
        self.assertEqual(len(merged), 1)

    def test_nan_close_uses_last_trade_only_inside_the_fetched_range(self):
        base = pd.DataFrame({
            "Date": pd.to_datetime(["2026-11-18"]),
            "Ticker": ["VRT",],
            "Open": [10.0],
            "High": [11.0],
            "Low": [9.0],
            "Close": [10.5],
            "Volume": [100.0],
        })
        recent = pd.DataFrame({
            "Open": [10.0, 10.2],
            "High": [11.0, 12.0],
            "Low": [9.0, 9.5],
            "Close": [10.5, float("nan")],
            "Volume": [100.0, 120.0],
        }, index=pd.to_datetime(["2026-11-18", "2026-11-19"]))
        recent.index.name = "Date"
        ticker = mock.Mock()
        ticker.fast_info = {"lastPrice": 9.8}
        with mock.patch.object(mlp.yf, "download", return_value=recent), \
             mock.patch.object(mlp.yf, "Ticker", return_value=ticker):
            merged = mlp._splice_recent_daily_bars(base, "VRT", datetime(2026, 11, 19).date())
        self.assertEqual(pd.to_datetime(merged["Date"]).max().date().isoformat(), "2026-11-19")
        close = float(merged.loc[merged["Date"] == pd.Timestamp("2026-11-19"), "Close"].iloc[0])
        self.assertEqual(close, 9.8)

        outside = recent.copy()
        ticker.fast_info = {"lastPrice": 50.0}
        with mock.patch.object(mlp.yf, "download", return_value=outside), \
             mock.patch.object(mlp.yf, "Ticker", return_value=ticker):
            rejected = mlp._splice_recent_daily_bars(base, "VRT", datetime(2026, 11, 19).date())
        self.assertEqual(pd.to_datetime(rejected["Date"]).max().date().isoformat(), "2026-11-18")


class MacroSessionDateTests(unittest.TestCase):
    def test_latest_date_follows_the_us_session_not_btc(self):
        frame = pd.DataFrame({
            "Ticker": ["QQQ", "NVDA", "BTC-USD"],
            "Date": pd.to_datetime(["2026-11-19", "2026-11-19", "2026-11-20"]),
        })
        self.assertEqual(mlp.macro_weather_session_date(frame), "2026-11-19")
        self.assertEqual(mlp.macro_weather_session_date(frame.iloc[0:0]), "")


if __name__ == "__main__":
    unittest.main()
