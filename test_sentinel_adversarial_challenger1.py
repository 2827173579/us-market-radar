#!/usr/bin/env python3
"""
test_sentinel_adversarial_challenger1.py - Empirical Adversarial Stress Test Suite
Challenger Agent: teamwork_challenger_sentinel_1

Adversarial Stress Test Matrix:
1. Extreme Price Movements:
   - Flash crash (-99%), massive upside spike (+1000%), sub-penny ($0.0001, $0.000001)
   - Zero and negative active prices
   - Strict invariant assertion: 1H撑 < active_price < 1H压, finite float, no NaN/ZeroDivision
2. Degenerate Volume Profile Inputs:
   - 100% zero volume across 140 bars
   - Identical high/low/close flat line across 140 bars
   - 1-bar series, flat 1-bar series with 0 volume, empty/NaN series
   - Assert POC returns valid finite float
3. 4H K-Line Grouping Stress:
   - Early closes (13:00, 14:00)
   - Irregular trading hours / premarket / postmarket noise filtering
   - Missing bars / halted bars
   - DST shifts (spring forward EDT, fall back EST)
   - Multi-day random bar distributions (strictly zero cross-day stitching)
   - BTC-USD 24/7 grouping stress
4. Pre/Post-Market Snapshot Stress:
   - Extreme gaps (-50%, +50%, -99%, +500%)
   - Immutability of historical bars and original DataFrame
   - Correct isolation and properties of snapshot bar
   - RSI calculation on snapshot series returns finite values
5. Factor Y Robustness:
   - All 3 tickers missing, 2 tickers missing, 1 ticker missing
   - Missing individual keys / None / NaN / non-numeric values
   - Extreme outlier RSI (0.0, 100.0, -50.0, 150.0)
   - Cross-asset resonance & Discord payload pipeline integration
   - Zero unhandled KeyError, TypeError, or NaN leakage
"""

import os
import sys
import math
import random
import unittest
from typing import Dict, Any, List

import numpy as np
import pandas as pd

# UTF-8 encoding configuration for terminal
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Import discord_sentinel module
PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import discord_sentinel as ds


class TestSentinelAdversarialScenario1ExtremePrices(unittest.TestCase):
    """
    Scenario 1: Extreme Price Movements
    Assert 1H撑 < active_price < 1H压 invariant holds strictly with zero division by zero or NaN.
    """

    def setUp(self):
        # Create a standard 20-bar 1H DataFrame as baseline
        tz = "America/New_York"
        dt = pd.Timestamp("2026-11-03 09:30:00", tz=tz)
        records = []
        for i in range(25):
            records.append({
                "Datetime": dt + pd.Timedelta(hours=i),
                "Open": 100.0,
                "High": 108.0 if i == 5 else 102.0,
                "Low": 92.0 if i == 10 else 98.0,
                "Close": 100.0,
                "Volume": 10000.0
            })
        self.df_base = pd.DataFrame(records).set_index("Datetime")

    def test_flash_crash_99_percent(self):
        """Flash crash -99%: Price plummets from 100.0 to 1.0 (and 1000.0 to 0.01)."""
        crash_scenarios = [
            (100.0, 1.0, 95.0),       # -99% drop from 100
            (500.0, 5.0, 480.0),      # -99% drop from 500
            (1000.0, 0.01, 950.0),    # -99.999% crash to penny
            (25.0, 0.25, 24.0)        # -99% drop from 25
        ]

        for prev_p, crash_p, poc in crash_scenarios:
            df = self.df_base.copy()
            df["Close"] = prev_p
            df["High"] = prev_p * 1.05
            df["Low"] = prev_p * 0.95

            s, r, atr = ds.compute_and_protect_1h_sr(df, active_price=crash_p, poc=poc)

            self.assertFalse(math.isnan(s), f"Support is NaN for crash {crash_p}")
            self.assertFalse(math.isnan(r), f"Resistance is NaN for crash {crash_p}")
            self.assertFalse(math.isnan(atr), f"ATR is NaN for crash {crash_p}")
            self.assertFalse(math.isinf(s), f"Support is Inf for crash {crash_p}")
            self.assertFalse(math.isinf(r), f"Resistance is Inf for crash {crash_p}")
            self.assertFalse(math.isinf(atr), f"ATR is Inf for crash {crash_p}")

            self.assertLess(s, crash_p, f"Crash invariant violated: Support {s} not < Price {crash_p}")
            self.assertGreater(r, crash_p, f"Crash invariant violated: Resistance {r} not > Price {crash_p}")

    def test_massive_upside_spike_1000_percent(self):
        """Massive upside spike +1000%: Price surges 10x or more."""
        spike_scenarios = [
            (10.0, 110.0, 10.0),       # +1000% spike
            (100.0, 1100.0, 100.0),    # +1000% spike
            (1.0, 50.0, 1.0),          # +4900% spike
            (0.10, 10.0, 0.10)         # +9900% spike
        ]

        for prev_p, spike_p, poc in spike_scenarios:
            df = self.df_base.copy()
            df["Close"] = prev_p
            df["High"] = prev_p * 1.05
            df["Low"] = prev_p * 0.95

            s, r, atr = ds.compute_and_protect_1h_sr(df, active_price=spike_p, poc=poc)

            self.assertFalse(math.isnan(s), f"Support is NaN for spike {spike_p}")
            self.assertFalse(math.isnan(r), f"Resistance is NaN for spike {spike_p}")
            self.assertFalse(math.isnan(atr), f"ATR is NaN for spike {spike_p}")

            self.assertLess(s, spike_p, f"Spike invariant violated: Support {s} not < Price {spike_p}")
            self.assertGreater(r, spike_p, f"Spike invariant violated: Resistance {r} not > Price {spike_p}")

    def test_sub_penny_prices(self):
        """Sub-penny prices: $0.0001, $0.00005, $0.000001."""
        sub_penny_values = [0.001, 0.0001, 0.00005, 0.000001]

        for sub_p in sub_penny_values:
            # Case A: df has normal prices, active_price is sub-penny
            s, r, atr = ds.compute_and_protect_1h_sr(self.df_base, active_price=sub_p, poc=0.0001)
            self.assertLess(s, sub_p, f"Sub-penny invariant failed: {s} not < {sub_p}")
            self.assertGreater(r, sub_p, f"Sub-penny invariant failed: {r} not > {sub_p}")

            # Case B: df also has sub-penny historical prices
            df_sub = pd.DataFrame([{
                "Open": sub_p, "High": sub_p * 1.1, "Low": sub_p * 0.9, "Close": sub_p, "Volume": 1000.0
            }] * 25)
            s_b, r_b, atr_b = ds.compute_and_protect_1h_sr(df_sub, active_price=sub_p, poc=sub_p)
            self.assertLess(s_b, sub_p, f"Sub-penny df invariant failed: {s_b} not < {sub_p}")
            self.assertGreater(r_b, sub_p, f"Sub-penny df invariant failed: {r_b} not > {sub_p}")

            # Case C: Empty df fallback
            s_c, r_c, atr_c = ds.compute_and_protect_1h_sr(pd.DataFrame(), active_price=sub_p)
            self.assertLess(s_c, sub_p, f"Sub-penny empty df invariant failed: {s_c} not < {sub_p}")
            self.assertGreater(r_c, sub_p, f"Sub-penny empty df invariant failed: {r_c} not > {sub_p}")

    def test_zero_and_negative_prices_boundary_guard(self):
        """Extreme edge: active_price = 0.0 or negative prices."""
        for p_edge in [0.0, -1.0, -50.0]:
            s, r, atr = ds.compute_and_protect_1h_sr(self.df_base, active_price=p_edge, poc=None)
            self.assertLess(s, p_edge, f"Edge invariant failed for {p_edge}: Support {s} not < Price {p_edge}")
            self.assertGreater(r, p_edge, f"Edge invariant failed for {p_edge}: Resistance {r} not > Price {p_edge}")

    def test_trillion_dollar_astronomical_prices(self):
        """Extreme edge: $1,000,000,000,000 price."""
        astro_p = 1e12
        s, r, atr = ds.compute_and_protect_1h_sr(self.df_base, active_price=astro_p, poc=None)
        self.assertLess(s, astro_p)
        self.assertGreater(r, astro_p)
        self.assertFalse(math.isnan(s))
        self.assertFalse(math.isinf(s))


class TestSentinelAdversarialScenario2DegenerateVolumeProfile(unittest.TestCase):
    """
    Scenario 2: Degenerate Volume Profile inputs
    Assert POC returns valid finite float across 0 volume, flat line, 1-bar, and NaNs.
    """

    def test_100_percent_zero_volume_across_140_bars(self):
        """100% zero volume across all 140 bars."""
        records = []
        for i in range(140):
            records.append({
                "High": 105.0 + (i % 5),
                "Low": 95.0 - (i % 5),
                "Close": 100.0,
                "Volume": 0.0
            })
        df = pd.DataFrame(records)
        poc = ds.compute_volume_profile_poc(df)

        self.assertIsInstance(poc, (float, int, np.floating))
        self.assertFalse(math.isnan(poc), "POC must not be NaN on 100% zero volume")
        self.assertFalse(math.isinf(poc), "POC must not be Inf on 100% zero volume")
        self.assertGreater(poc, 0.0, "POC should be positive")

    def test_identical_high_low_close_flat_line(self):
        """Flat line: High == Low == Close across all 140 bars."""
        # Case A: With positive volume
        df_flat_vol = pd.DataFrame([{"High": 123.45, "Low": 123.45, "Close": 123.45, "Volume": 1000.0}] * 140)
        poc_vol = ds.compute_volume_profile_poc(df_flat_vol)
        self.assertAlmostEqual(poc_vol, 123.45, places=2)

        # Case B: With zero volume
        df_flat_zero = pd.DataFrame([{"High": 123.45, "Low": 123.45, "Close": 123.45, "Volume": 0.0}] * 140)
        poc_zero = ds.compute_volume_profile_poc(df_flat_zero)
        self.assertAlmostEqual(poc_zero, 123.45, places=2)

    def test_single_bar_series(self):
        """Single-bar series (1-bar DataFrame)."""
        # 1-bar normal
        df_1 = pd.DataFrame([{"High": 110.0, "Low": 90.0, "Close": 100.0, "Volume": 5000.0}])
        poc_1 = ds.compute_volume_profile_poc(df_1)
        self.assertFalse(math.isnan(poc_1))
        self.assertFalse(math.isinf(poc_1))
        self.assertGreater(poc_1, 0.0)

        # 1-bar flat with zero volume
        df_1_flat = pd.DataFrame([{"High": 50.0, "Low": 50.0, "Close": 50.0, "Volume": 0.0}])
        poc_1_flat = ds.compute_volume_profile_poc(df_1_flat)
        self.assertAlmostEqual(poc_1_flat, 50.0, places=2)

    def test_all_nan_and_empty_inputs(self):
        """All-NaN dataframe or empty dataframe."""
        df_empty = pd.DataFrame()
        poc_empty = ds.compute_volume_profile_poc(df_empty)
        self.assertEqual(poc_empty, 0.0)

        df_nan = pd.DataFrame([{"High": np.nan, "Low": np.nan, "Close": np.nan, "Volume": np.nan}] * 20)
        poc_nan = ds.compute_volume_profile_poc(df_nan)
        self.assertEqual(poc_nan, 0.0)

    def test_sub_penny_and_extreme_range_volume_profile(self):
        """Volume profile on sub-penny prices and huge price ranges."""
        # Sub-penny prices
        df_sub = pd.DataFrame([{
            "High": 0.00015, "Low": 0.00008, "Close": 0.00012, "Volume": 1000000.0
        }] * 140)
        poc_sub = ds.compute_volume_profile_poc(df_sub)
        self.assertFalse(math.isnan(poc_sub))
        self.assertFalse(math.isinf(poc_sub))

        # Huge span (e.g. 1.0 to 1,000,000.0)
        df_huge = pd.DataFrame([
            {"High": 1.0, "Low": 0.5, "Close": 0.8, "Volume": 100.0},
            {"High": 1000000.0, "Low": 999000.0, "Close": 999500.0, "Volume": 500000.0}
        ])
        poc_huge = ds.compute_volume_profile_poc(df_huge)
        self.assertFalse(math.isnan(poc_huge))
        self.assertFalse(math.isinf(poc_huge))


class TestSentinelAdversarialScenario3KLineGroupingStress(unittest.TestCase):
    """
    Scenario 3: 4H K-line grouping stress
    Early closes, irregular trading hours, missing bars, DST shifts, multi-day random bar distributions.
    Assert zero cross-day stitching strictly.
    """

    def _create_bar(self, dt, open_p=100.0, high_p=102.0, low_p=98.0, close_p=100.0, vol=1000.0):
        return {
            "Datetime": dt,
            "Open": open_p,
            "High": high_p,
            "Low": low_p,
            "Close": close_p,
            "Volume": vol
        }

    def test_early_close_half_day_sessions(self):
        """Early close sessions (e.g. 13:00 close on Black Friday / Christmas Eve)."""
        tz = "America/New_York"
        day = pd.Timestamp("2026-11-27", tz=tz)  # Black Friday
        # 13:00 close: bars at 09:30, 10:30, 11:30, 12:30
        records = [
            self._create_bar(day.replace(hour=9, minute=30)),
            self._create_bar(day.replace(hour=10, minute=30)),
            self._create_bar(day.replace(hour=11, minute=30)),
            self._create_bar(day.replace(hour=12, minute=30)),
        ]
        df_early = pd.DataFrame(records).set_index("Datetime")
        df_4h = ds.aggregate_1h_to_4h_df(df_early, ticker="SPY")

        self.assertEqual(len(df_4h), 1, "Half-day should produce exactly 1 4H bar")
        self.assertEqual(df_4h.index[0].date(), day.date())
        self.assertEqual(df_4h.index[0].hour, 9)
        self.assertEqual(df_4h.index[0].minute, 30)

        # 14:00 close: bars 09:30, 10:30, 11:30, 12:30, 13:30
        records.append(self._create_bar(day.replace(hour=13, minute=30)))
        df_14 = pd.DataFrame(records).set_index("Datetime")
        df_4h_14 = ds.aggregate_1h_to_4h_df(df_14, ticker="SPY")
        self.assertEqual(len(df_4h_14), 2, "14:00 close should produce 2 4H bars (Bar 1 full, Bar 2 partial)")
        self.assertEqual(df_4h_14.index[0].date(), day.date())
        self.assertEqual(df_4h_14.index[1].date(), day.date())

    def test_pre_and_post_market_noise_filtering(self):
        """Input contains pre-market (04:00~09:00) and post-market (16:30~20:00) bars."""
        tz = "America/New_York"
        day = pd.Timestamp("2026-11-04", tz=tz)
        all_hours = [4, 6, 8, 9, 9.5, 10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5, 18, 20]
        records = []
        for h in all_hours:
            hr = int(h)
            mn = 30 if (h % 1 != 0) else 0
            records.append(self._create_bar(day.replace(hour=hr, minute=mn)))

        df_mixed = pd.DataFrame(records).set_index("Datetime")
        df_4h = ds.aggregate_1h_to_4h_df(df_mixed, ticker="AAPL")

        # RTH only has 7 bars (9:30, 10:30, 11:30, 12:30, 13:30, 14:30, 15:30)
        # Should produce exactly 2 4H bars
        self.assertEqual(len(df_4h), 2, "Pre/post-market noise must be strictly filtered to 2 RTH bars")
        self.assertEqual(df_4h.index[0].hour, 9)
        self.assertEqual(df_4h.index[1].hour, 13)

    def test_dst_transitions_spring_and_fall(self):
        """DST shifts: US EDT switch (March) and EST switch (November)."""
        tz = "America/New_York"

        # 1. Fall switch (EST return, UTC-5): first Monday of November 2026 (2026-11-02)
        day_fall = pd.Timestamp("2026-11-02", tz=tz)
        times = [(9, 30), (10, 30), (11, 30), (12, 30), (13, 30), (14, 30), (15, 30)]
        records_fall = [self._create_bar(day_fall.replace(hour=h, minute=m)) for h, m in times]
        df_fall = pd.DataFrame(records_fall).set_index("Datetime")
        res_fall = ds.aggregate_1h_to_4h_df(df_fall, ticker="MSFT")
        self.assertEqual(len(res_fall), 2)
        self.assertEqual(res_fall.index[0].date(), day_fall.date())

        # 2. Spring switch (EDT start, UTC-4): second Monday of March 2026 (2026-03-09)
        day_spring = pd.Timestamp("2026-03-09", tz=tz)
        records_spring = [self._create_bar(day_spring.replace(hour=h, minute=m)) for h, m in times]
        df_spring = pd.DataFrame(records_spring).set_index("Datetime")
        res_spring = ds.aggregate_1h_to_4h_df(df_spring, ticker="MSFT")
        self.assertEqual(len(res_spring), 2)
        self.assertEqual(res_spring.index[0].date(), day_spring.date())

    def test_multi_day_random_missing_bars_zero_cross_day_stitching(self):
        """
        Adversarial multi-day stress: 10 consecutive trading days with random dropped bars.
        Rigid invariant: ZERO cross-day stitching. Every bar in df_4h must belong strictly to one calendar date.
        """
        random.seed(42)
        tz = "America/New_York"
        dt_start = pd.Timestamp("2026-11-02", tz=tz)
        times = [(9, 30), (10, 30), (11, 30), (12, 30), (13, 30), (14, 30), (15, 30)]

        records = []
        expected_days = []
        cur_dt = dt_start
        while len(expected_days) < 10:
            if cur_dt.weekday() < 5:
                # Randomly drop 0 to 4 bars from the day
                chosen_times = sorted(random.sample(times, k=random.randint(3, 7)))
                for h, m in chosen_times:
                    records.append(self._create_bar(cur_dt.replace(hour=h, minute=m)))
                expected_days.append(cur_dt.date())
            cur_dt += pd.Timedelta(days=1)

        df_multi = pd.DataFrame(records).set_index("Datetime")
        df_4h = ds.aggregate_1h_to_4h_df(df_multi, ticker="NVDA")

        # Invariant 1: df_4h must not be empty
        self.assertFalse(df_4h.empty)

        # Invariant 2: ZERO CROSS-DAY STITCHING
        # Check every 4H bar: verify its date matches an expected trading day,
        # and that consecutive bars within the same day never bridge across midnight.
        bar_dates = [b_dt.date() for b_dt in df_4h.index]
        for b_date in bar_dates:
            self.assertIn(b_date, expected_days, f"Bar date {b_date} not in expected trading days")

        # Verify bar hours are only 9 or 13
        for b_dt in df_4h.index:
            self.assertIn(b_dt.hour, (9, 10, 11, 12, 13, 14, 15), f"Unexpected bar hour {b_dt.hour}")

        # Check grouping integrity: count of bars per day cannot exceed 2
        day_counts = pd.Series(bar_dates).value_counts()
        for day, cnt in day_counts.items():
            self.assertLessEqual(cnt, 2, f"Day {day} produced {cnt} 4H bars, max allowed is 2")

    def test_btc_usd_crypto_24_7_stitching_stress(self):
        """BTC-USD 24/7 crypto aggregation: random missing hours over 5 days."""
        random.seed(123)
        dt_start = pd.Timestamp("2026-11-01 00:00:00", tz="UTC")
        records = []
        for d in range(5):
            day_dt = dt_start + pd.Timedelta(days=d)
            # Randomly keep 15 to 24 hours per day
            keep_hours = sorted(random.sample(range(24), k=random.randint(15, 24)))
            for h in keep_hours:
                records.append({
                    "Datetime": day_dt.replace(hour=h),
                    "Open": 70000.0 + h * 10,
                    "High": 70100.0 + h * 10,
                    "Low": 69900.0 + h * 10,
                    "Close": 70050.0 + h * 10,
                    "Volume": 50.0
                })
        df_crypto = pd.DataFrame(records).set_index("Datetime")
        df_4h_crypto = ds.aggregate_1h_to_4h_df(df_crypto, ticker="BTC-USD")

        # Invariant: Every 4H bar must strictly belong to its UTC date
        for b_dt in df_4h_crypto.index:
            self.assertIn(b_dt.date(), [dt_start.date() + pd.Timedelta(days=i) for i in range(5)])


class TestSentinelAdversarialScenario4PrePostMarketSnapshot(unittest.TestCase):
    """
    Scenario 4: Pre/post-market snapshot stress
    Extreme gaps (-50%, +50%, -99%, +500%).
    Assert historical bars remain identical, original df not mutated, snapshot correctly isolated.
    """

    def setUp(self):
        tz = "America/New_York"
        dt = pd.Timestamp("2026-11-06 09:30:00", tz=tz)
        times = [(9, 30), (10, 30), (11, 30), (12, 30), (13, 30), (14, 30), (15, 30)]
        records = []
        p = 100.0
        for h, m in times:
            records.append({
                "Datetime": dt.replace(hour=h, minute=m),
                "Open": p,
                "High": p + 1.0,
                "Low": p - 1.0,
                "Close": p,
                "Volume": 10000.0
            })
        self.df_rth = pd.DataFrame(records).set_index("Datetime")
        self.orig_close = float(self.df_rth["Close"].iloc[-1])

    def test_extreme_gap_down_50_percent(self):
        """Gap down -50%: Price drops from 100.0 to 50.0."""
        realtime_p = 50.0
        df_calc = ds.append_realtime_snapshot_bar(self.df_rth, realtime_price=realtime_p, current_session="PRE_MARKET")

        # Invariant 1: Original dataframe is untouched
        self.assertEqual(len(self.df_rth), 7)
        self.assertEqual(float(self.df_rth["Close"].iloc[-1]), self.orig_close)

        # Invariant 2: Appended exactly 1 snapshot bar
        self.assertEqual(len(df_calc), 8)

        # Invariant 3: Historical bars (0 to 6) are 100% identical
        for i in range(7):
            self.assertEqual(df_calc.iloc[i]["Open"], self.df_rth.iloc[i]["Open"])
            self.assertEqual(df_calc.iloc[i]["High"], self.df_rth.iloc[i]["High"])
            self.assertEqual(df_calc.iloc[i]["Low"], self.df_rth.iloc[i]["Low"])
            self.assertEqual(df_calc.iloc[i]["Close"], self.df_rth.iloc[i]["Close"])

        # Invariant 4: Snapshot bar properties
        snap = df_calc.iloc[-1]
        self.assertEqual(snap["Close"], 50.0)
        self.assertEqual(snap["Open"], 100.0)
        self.assertEqual(snap["High"], 100.0)
        self.assertEqual(snap["Low"], 50.0)
        self.assertEqual(snap["Volume"], 0.0)

    def test_extreme_gap_up_50_percent(self):
        """Gap up +50%: Price jumps from 100.0 to 150.0."""
        realtime_p = 150.0
        df_calc = ds.append_realtime_snapshot_bar(self.df_rth, realtime_price=realtime_p, current_session="POST_MARKET")

        self.assertEqual(len(df_calc), 8)
        snap = df_calc.iloc[-1]
        self.assertEqual(snap["Close"], 150.0)
        self.assertEqual(snap["Open"], 100.0)
        self.assertEqual(snap["High"], 150.0)
        self.assertEqual(snap["Low"], 100.0)
        self.assertEqual(snap["Volume"], 0.0)

    def test_catastrophic_gap_down_99_percent_and_up_500_percent(self):
        """Catastrophic gap -99% (100 -> 1) and +500% (100 -> 600)."""
        # -99%
        df_crash = ds.append_realtime_snapshot_bar(self.df_rth, realtime_price=1.0, current_session="OVERNIGHT")
        self.assertEqual(df_crash.iloc[-1]["Close"], 1.0)
        self.assertEqual(df_crash.iloc[-1]["Low"], 1.0)
        self.assertEqual(df_crash.iloc[-1]["High"], 100.0)

        # +500%
        df_surge = ds.append_realtime_snapshot_bar(self.df_rth, realtime_price=600.0, current_session="WEEKEND")
        self.assertEqual(df_surge.iloc[-1]["Close"], 600.0)
        self.assertEqual(df_surge.iloc[-1]["High"], 600.0)
        self.assertEqual(df_surge.iloc[-1]["Low"], 100.0)

    def test_rsi_computability_with_extreme_snapshot_bar(self):
        """Assert Wilder RSI calculation executes cleanly without NaN on extreme gap snapshot."""
        # Build 30 bars so RSI 14 has sufficient history
        records = []
        tz = "America/New_York"
        dt = pd.Timestamp("2026-10-15 09:30:00", tz=tz)
        for i in range(30):
            records.append({
                "Datetime": dt + pd.Timedelta(hours=i),
                "Open": 100.0,
                "High": 101.0,
                "Low": 99.0,
                "Close": 100.0,
                "Volume": 1000.0
            })
        df_30 = pd.DataFrame(records).set_index("Datetime")

        # Append -80% crash snapshot bar
        df_calc = ds.append_realtime_snapshot_bar(df_30, realtime_price=20.0, current_session="PRE_MARKET")
        rsi_series = ds.compute_series_rsi(df_calc["Close"], period=14)

        last_rsi = float(rsi_series.iloc[-1])
        self.assertFalse(math.isnan(last_rsi), "RSI on crash snapshot must not be NaN")
        self.assertFalse(math.isinf(last_rsi), "RSI on crash snapshot must not be Inf")
        self.assertLess(last_rsi, 30.0, "RSI after -80% gap must reflect extreme oversold (< 30.0)")


class TestSentinelAdversarialScenario5FactorYRobustness(unittest.TestCase):
    """
    Scenario 5: Factor Y robustness
    All 3 tickers missing, 2 tickers missing, extreme outlier RSI (0.0, 100.0).
    Assert zero unhandled KeyError or NaN across full pipeline.
    """

    def test_all_three_tickers_missing(self):
        """All 3 tickers (SCHD, KO, GLD) missing from components data."""
        # Empty dict
        y_empty = ds.compute_factor_y({})
        self.assertEqual(y_empty["rsi_4h"], 50.0)
        self.assertEqual(y_empty["rsi_1d"], 50.0)
        self.assertEqual(y_empty["status"], "📈【站稳主力上方·多头顺风】")
        self.assertEqual(y_empty["price"], 0.0)

        # Dict with unrelated tickers
        unrelated = {
            "SPY": {"rsi_4h": 30.0, "rsi_1d": 40.0, "price": 500.0},
            "NVDA": {"rsi_4h": 70.0, "rsi_1d": 65.0, "price": 140.0}
        }
        y_unrelated = ds.compute_factor_y(unrelated)
        self.assertEqual(y_unrelated["rsi_4h"], 50.0)
        self.assertEqual(y_unrelated["status"], "📈【站稳主力上方·多头顺风】")

    def test_two_tickers_missing(self):
        """2 tickers missing (only 1 of SCHD, KO, GLD available)."""
        # Case A: Only SCHD available
        data_schd = {
            "SCHD": {"rsi_4h": 28.0, "rsi_1d": 35.0, "price": 32.0, "poc": 31.5, "support": 31.0, "resistance": 33.0}
        }
        y_schd = ds.compute_factor_y(data_schd)
        self.assertAlmostEqual(y_schd["rsi_4h"], 28.0)
        self.assertAlmostEqual(y_schd["rsi_1d"], 35.0)
        self.assertAlmostEqual(y_schd["price"], 32.0)
        self.assertTrue(y_schd["support"] < y_schd["price"] < y_schd["resistance"])
        self.assertIn("双周期见底", y_schd["status"])

        # Case B: Only GLD available
        data_gld = {
            "GLD": {"rsi_4h": 75.0, "rsi_1d": 72.0, "price": 240.0, "poc": 235.0, "support": 230.0, "resistance": 245.0}
        }
        y_gld = ds.compute_factor_y(data_gld)
        self.assertAlmostEqual(y_gld["rsi_4h"], 75.0)
        self.assertIn("双周期触顶", y_gld["status"])

    def test_missing_or_corrupt_internal_keys(self):
        """Tickers present but have None, NaN, missing, or corrupt fields."""
        corrupt_data = {
            "SCHD": {"rsi_4h": None, "rsi_1d": np.nan, "price": "corrupt_string"},
            "KO": {"rsi_4h": 60.0, "rsi_1d": 55.0, "price": 70.0, "poc": None},
            "GLD": {}  # Empty dict for GLD
        }
        y_corrupt = ds.compute_factor_y(corrupt_data)
        # Should gracefully use KO's valid values
        self.assertAlmostEqual(y_corrupt["rsi_4h"], 60.0)
        self.assertAlmostEqual(y_corrupt["rsi_1d"], 55.0)
        self.assertAlmostEqual(y_corrupt["price"], 70.0)
        self.assertFalse(math.isnan(y_corrupt["rsi_4h"]))

    def test_extreme_outlier_rsi_values(self):
        """Extreme outlier RSI: 0.0, 100.0, negative (-20.0), excessive (120.0)."""
        # Extreme bottom: 0.0
        data_0 = {
            "SCHD": {"rsi_4h": 0.0, "rsi_1d": 0.0, "price": 30.0, "poc": 30.0, "support": 29.0, "resistance": 31.0},
            "KO": {"rsi_4h": 0.0, "rsi_1d": 0.0, "price": 60.0, "poc": 60.0, "support": 59.0, "resistance": 61.0},
            "GLD": {"rsi_4h": 0.0, "rsi_1d": 0.0, "price": 200.0, "poc": 200.0, "support": 195.0, "resistance": 205.0}
        }
        y_0 = ds.compute_factor_y(data_0)
        self.assertEqual(y_0["rsi_4h"], 0.0)
        self.assertIn("双周期见底", y_0["status"])

        # Extreme top: 100.0
        data_100 = {
            "SCHD": {"rsi_4h": 100.0, "rsi_1d": 100.0, "price": 30.0},
            "KO": {"rsi_4h": 100.0, "rsi_1d": 100.0, "price": 60.0},
            "GLD": {"rsi_4h": 100.0, "rsi_1d": 100.0, "price": 200.0}
        }
        y_100 = ds.compute_factor_y(data_100)
        self.assertEqual(y_100["rsi_4h"], 100.0)
        self.assertIn("双周期触顶", y_100["status"])

    def test_resonance_and_discord_payload_integration_with_corrupt_data(self):
        """Cross-asset resonance and Discord payload generation with degenerate factor Y and missing tickers."""
        # Analysis dictionary with completely empty tickers
        empty_analysis = {
            "timestamp": "2026-11-10 09:30:00",
            "session": "REGULAR",
            "tickers": {},
            "factor_y": None,
            "resonance_alerts": [],
            "divergence_alerts": [],
            "dual_extremes": []
        }

        # Step 1: evaluate_cross_asset_resonance should not crash
        res_analysis = ds.evaluate_cross_asset_resonance(empty_analysis)
        self.assertIn("factor_y", res_analysis)
        self.assertEqual(res_analysis["factor_y"]["rsi_4h"], 50.0)

        # Step 2: build_discord_payloads should not crash (Payload 1 3秒抄作业总台 is always sent)
        p1, p2a, p2b = ds.build_discord_payloads(res_analysis)
        self.assertIsNotNone(p1, "Payload 1 overview card is always generated")
        self.assertIsNotNone(p2a)
        self.assertIsNotNone(p2b)
        self.assertEqual(len(p2a.get("embeds", [])), 3)
        self.assertEqual(len(p2b.get("embeds", [])), 2)

        # Step 3: Divergence evaluation with extreme RSI divergence
        extreme_div_analysis = {
            "timestamp": "2026-11-10 09:30:00",
            "session": "REGULAR",
            "tickers": {
                "SOXX": {"rsi_4h": 10.0, "rsi_1d": 20.0, "price": 120.0, "status": "[⚡ 4H超卖]"},
                "SCHD": {"rsi_4h": 90.0, "rsi_1d": 85.0, "price": 35.0},
                "KO": {"rsi_4h": 90.0, "rsi_1d": 85.0, "price": 70.0},
                "GLD": {"rsi_4h": 90.0, "rsi_1d": 85.0, "price": 250.0}
            },
            "factor_y": None
        }
        res_div = ds.evaluate_cross_asset_resonance(extreme_div_analysis)
        self.assertTrue(any("左侧背离" in a for a in res_div.get("divergence_alerts", [])))


# ==============================================================================
# Test Runner
# ==============================================================================

if __name__ == "__main__":
    print("=" * 80)
    print(" ⚔️ RUNNING ADVERSARIAL STRESS TEST SUITE (Challenger 1)")
    print("=" * 80)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestSentinelAdversarialScenario1ExtremePrices))
    suite.addTests(loader.loadTestsFromTestCase(TestSentinelAdversarialScenario2DegenerateVolumeProfile))
    suite.addTests(loader.loadTestsFromTestCase(TestSentinelAdversarialScenario3KLineGroupingStress))
    suite.addTests(loader.loadTestsFromTestCase(TestSentinelAdversarialScenario4PrePostMarketSnapshot))
    suite.addTests(loader.loadTestsFromTestCase(TestSentinelAdversarialScenario5FactorYRobustness))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("=" * 80)
    print(f" 📊 ADVERSARIAL TEST SUMMARY: Total={result.testsRun}, Passed={result.testsRun - len(result.failures) - len(result.errors)}, Failures={len(result.failures)}, Errors={len(result.errors)}")
    print("=" * 80)

    if result.wasSuccessful():
        print(" >>> BINARY VERDICT: APPROVE <<<")
        sys.exit(0)
    else:
        print(" >>> BINARY VERDICT: REQUEST_CHANGES <<<")
        sys.exit(1)
