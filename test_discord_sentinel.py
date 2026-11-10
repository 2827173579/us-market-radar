#!/usr/bin/env python3
"""
test_discord_sentinel.py - Automated Unit & Invariant Test Suite for Discord Sentinel

Authoritative references:
- ORIGINAL_REQUEST.md (§R1, §R2, §R3, §R4, §R5, §Acceptance Criteria)
- PROJECT.md (§Architecture, §Feature Inventory, §Interface Contracts)
- TEST_INFRA.md (§Test Philosophy, §Test Tiers 1-4, §Acceptance Criteria)
- Explorer Handoffs: sentinel_1 (Engineering), sentinel_2 (Quant/POC/RSI), sentinel_3 (Discord/Linkage)

Test Tiers Covered:
- Tier 1: 5 Core Engineering Corrections Invariants
  1. test_4h_aggregation_no_cross_day_stitching
  2. test_4h_aggregation_early_close_and_intraday
  3. test_prepost_market_snapshot_does_not_mutate_historical_bars
  4. test_1h_support_resistance_strict_boundary_protection
  5. test_batch_fetch_and_btc_usd_bypass
- Tier 2: Quantitative Engine & State Machine Invariants
  6. test_volume_profile_poc_calculation
  7. test_rsi_state_machine_transitions
  8. test_defense_factor_y_and_divergence
  9. test_3x_mirror_resonance
- Tier 3: Discord Embed Formatting & Anti-Grayblock Invariants
  10. test_embed_native_markdown_no_codeblocks
  11. test_discord_payload_character_limits
- Tier 4: Radar Daemon Linkage & Safety Invariants
  12. test_radar_linkage_async_and_isolated
- Universe & Environment Invariants:
  13. test_universe_structure_and_completeness
  14. test_env_webhook_configuration

Usage:
  python test_discord_sentinel.py
  python -m unittest test_discord_sentinel.py
"""

import os
import sys
import math
import time
import unittest
from unittest.mock import MagicMock, patch
from typing import Dict, Any, List, Optional, Tuple

import numpy as np
import pandas as pd

# Ensure UTF-8 output on Windows terminal
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Configure project paths
PROJECT_DIR = os.path.abspath(r"C:\Users\User\.gemini\antigravity-ide\scratch\us-market-radar")
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import discord_sentinel as ds


# ==============================================================================
# Synthetic Fixture Generators (Deterministic, Offline, Zero-Network)
# ==============================================================================

def create_synthetic_rth_1h_bars(days: int = 2, start_date: str = "2026-11-03", base_price: float = 100.0) -> pd.DataFrame:
    """
    Generate synthetic US RTH (09:30~16:00 ET) 1H bars.
    Each trading day contains exactly 7 1H bars starting at:
    09:30, 10:30, 11:30, 12:30, 13:30, 14:30, 15:30 (America/New_York).
    """
    tz = "America/New_York"
    records = []
    dt_start = pd.Timestamp(start_date, tz=tz)
    
    current_dt = dt_start
    days_generated = 0
    p = base_price
    
    while days_generated < days:
        if current_dt.weekday() < 5:  # Monday to Friday
            times = [
                (9, 30), (10, 30), (11, 30), (12, 30),
                (13, 30), (14, 30), (15, 30)
            ]
            for h, m in times:
                bar_dt = current_dt.replace(hour=h, minute=m, second=0, microsecond=0)
                # Deterministic smooth price movement
                open_p = p
                high_p = p + 2.0
                low_p = p - 1.5
                close_p = p + 0.5
                vol = 10000.0 + (h * 500.0)
                records.append({
                    "Datetime": bar_dt,
                    "Open": open_p,
                    "High": high_p,
                    "Low": low_p,
                    "Close": close_p,
                    "Volume": vol
                })
                p = close_p
            days_generated += 1
        current_dt += pd.Timedelta(days=1)
        
    df = pd.DataFrame(records).set_index("Datetime")
    return df


def create_synthetic_crypto_1h_bars(days: int = 2, start_date: str = "2026-11-03", base_price: float = 75000.0) -> pd.DataFrame:
    """
    Generate synthetic Crypto 24/7 1H bars (24 bars per day, UTC).
    """
    records = []
    dt_start = pd.Timestamp(start_date, tz="UTC")
    p = base_price
    
    for d in range(days):
        day_dt = dt_start + pd.Timedelta(days=d)
        for h in range(24):
            bar_dt = day_dt.replace(hour=h, minute=0, second=0, microsecond=0)
            records.append({
                "Datetime": bar_dt,
                "Open": p,
                "High": p + 150.0,
                "Low": p - 120.0,
                "Close": p + 30.0,
                "Volume": 250.0
            })
            p += 30.0
            
    df = pd.DataFrame(records).set_index("Datetime")
    return df


def create_synthetic_market_analysis(include_resonance: bool = True, include_divergence: bool = True) -> Dict[str, Any]:
    """
    Generate comprehensive synthetic market analysis covering all 51 tickers + Factor Y.
    Used for Discord embed layout and character limit testing.
    """
    analysis = {
        "timestamp": "2026-11-10 09:30:00",
        "session": "REGULAR",
        "tickers": {},
        "factor_y": {
            "price": 77.61,
            "rsi_4h": 61.1 if include_divergence else 48.0,
            "rsi_1d": 55.0,
            "poc": 74.82,
            "support": 76.77,
            "resistance": 78.96,
            "status": "[安全持股]"
        },
        "resonance_alerts": [],
        "divergence_alerts": [],
        "dual_extremes": []
    }
    
    # Populate all 51 tickers
    for sym in ds.ALL_SENTINEL_TICKERS:
        if sym in ("BTC", "BTC-USD"):
            p, poc, s, r = 75984.0, 75485.0, 75442.0, 80782.0
            r4, r1d = 30.5, 38.0
            status = "[🔥 双周期见底]"
        elif sym == "SOXL":
            p, poc, s, r = 40.90, 40.52, 40.59, 76.71
            r4, r1d = 28.5, 36.0
            status = "[🔥 双周期见底]" if include_resonance else "[安全持股]"
        elif sym == "SOXS":
            p, poc, s, r = 81.16, 42.16, 50.74, 81.45
            r4, r1d = 71.0, 68.0
            status = "[🛑 双周期触顶]" if include_resonance else "[安全持股]"
        elif sym == "TQQQ":
            p, poc, s, r = 50.49, 50.11, 50.25, 64.75
            r4, r1d = 30.0, 37.0
            status = "[🔥 双周期见底]" if include_resonance else "[安全持股]"
        elif sym == "SQQQ":
            p, poc, s, r = 49.47, 36.76, 39.78, 49.63
            r4, r1d = 69.5, 66.0
            status = "[🛑 双周期触顶]" if include_resonance else "[安全持股]"
        elif sym == "SOXX":
            p, poc, s, r = 130.93, 130.75, 130.75, 157.54
            r4, r1d = 21.5 if include_divergence else 45.0, 39.0
            status = "[⚡ 4H超卖]"
        else:
            p, poc, s, r = 150.00, 148.50, 147.00, 155.00
            r4, r1d = 50.0, 52.0
            status = "[安全持股]"
            
        analysis["tickers"][sym] = {
            "price": p,
            "rsi_4h": r4,
            "rsi_1d": r1d,
            "poc": poc,
            "support": s,
            "resistance": r,
            "status": status,
            "display_name": "BTC" if sym == "BTC-USD" else sym
        }
        
        if "见底" in status or "触顶" in status:
            analysis["dual_extremes"].append(f"{sym}(见底)" if "见底" in status else f"{sym}(触顶)")
            
    if include_resonance:
        analysis["resonance_alerts"].append(
            "🔥 【SOXL×SOXS 底部共振】SOXL超卖(技术性成交密集区:$40.52) + SOXS触顶！"
        )
        analysis["resonance_alerts"].append(
            "🔥 【TQQQ×SQQQ 底部共振】TQQQ超卖(技术性成交密集区:$50.11) + SQQQ触顶！"
        )
        
    if include_divergence:
        analysis["divergence_alerts"].append(
            "🟡 【因子Y×芯片·左侧背离】因子Y高位拥挤，芯片超卖见底，留意高低切反抽！\n"
            "📊 芯片 4H_RSI:21.5 vs 因子Y 4H_RSI:61.1"
        )
        
    return analysis


# ==============================================================================
# Tier 1 Tests: 5 Core Engineering Corrections Invariants
# ==============================================================================

class TestDiscordSentinelTier1(unittest.TestCase):
    """
    Tier 1: 5 Core Engineering Corrections Invariants:
    1. 4H aggregation no cross-day stitching
    2. 4H aggregation early close and intraday
    3. Pre/post-market snapshot does not mutate historical bars
    4. 1H S/R strict boundary protection
    5. Batch fetch and BTC-USD Finnhub bypass
    """

    def test_4h_aggregation_no_cross_day_stitching(self):
        """
        [Engineering Correction 1] 4H K-line aggregation must group strictly by Date.
        US RTH consists of 7 1H bars per day:
        - Bar 1: 09:30..12:30 (first 4 hours)
        - Bar 2: 13:30..15:30 (next 3 hours)
        Cross-day stitching (e.g. index 3..6 + index 7) is strictly forbidden.
        """
        # Generate 2 full trading days of 1H data (14 bars total)
        df_1h = create_synthetic_rth_1h_bars(days=2, start_date="2026-11-03", base_price=100.0)
        self.assertEqual(len(df_1h), 14, "Expected 14 1H bars across 2 trading days")

        # Execute 4H aggregation
        df_4h = ds.aggregate_1h_to_4h_df(df_1h, ticker="SPY")
        
        # Invariant 1: Exactly 4 bars produced (2 bars per day)
        self.assertEqual(len(df_4h), 4, f"Expected exactly 4 4H bars across 2 days, got {len(df_4h)}")
        
        # Invariant 2: Timestamps match exact expected session openings
        # Day 1: Bar 1 at 09:30, Bar 2 at 13:30
        day1_b1_dt = df_4h.index[0]
        day1_b2_dt = df_4h.index[1]
        self.assertEqual(day1_b1_dt.hour, 9, "Day 1 Bar 1 must start at 09:30")
        self.assertEqual(day1_b1_dt.minute, 30)
        self.assertEqual(day1_b2_dt.hour, 13, "Day 1 Bar 2 must start at 13:30")
        self.assertEqual(day1_b2_dt.minute, 30)
        
        # Invariant 3: Bar 1 and Bar 2 must belong to the exact same calendar date
        self.assertEqual(day1_b1_dt.date(), day1_b2_dt.date(), "Bar 1 and Bar 2 must belong to Day 1")
        
        # Day 2: Bar 1 at 09:30, Bar 2 at 13:30
        day2_b1_dt = df_4h.index[2]
        day2_b2_dt = df_4h.index[3]
        self.assertEqual(day2_b1_dt.date(), day2_b2_dt.date(), "Bar 3 and Bar 4 must belong to Day 2")
        self.assertNotEqual(day1_b1_dt.date(), day2_b1_dt.date(), "Day 1 and Day 2 dates must differ")

        # Invariant 4: Mathematical OHLCV aggregation correctness
        # Day 1 Bar 1 (chunk of 09:30, 10:30, 11:30, 12:30)
        d1_chunk1 = df_1h.iloc[0:4]
        self.assertAlmostEqual(df_4h.iloc[0]["Open"], d1_chunk1["Open"].iloc[0])
        self.assertAlmostEqual(df_4h.iloc[0]["High"], d1_chunk1["High"].max())
        self.assertAlmostEqual(df_4h.iloc[0]["Low"], d1_chunk1["Low"].min())
        self.assertAlmostEqual(df_4h.iloc[0]["Close"], d1_chunk1["Close"].iloc[-1])
        self.assertAlmostEqual(df_4h.iloc[0]["Volume"], d1_chunk1["Volume"].sum())

        # Day 1 Bar 2 (chunk of 13:30, 14:30, 15:30)
        d1_chunk2 = df_1h.iloc[4:7]
        self.assertAlmostEqual(df_4h.iloc[1]["Open"], d1_chunk2["Open"].iloc[0])
        self.assertAlmostEqual(df_4h.iloc[1]["High"], d1_chunk2["High"].max())
        self.assertAlmostEqual(df_4h.iloc[1]["Low"], d1_chunk2["Low"].min())
        self.assertAlmostEqual(df_4h.iloc[1]["Close"], d1_chunk2["Close"].iloc[-1])
        self.assertAlmostEqual(df_4h.iloc[1]["Volume"], d1_chunk2["Volume"].sum())

        # Invariant 5: Crypto 24/7 grouping (BTC-USD)
        # 1 day of crypto = 24 bars. Grouped into 6 4-hour chunks (00, 04, 08, 12, 16, 20)
        df_crypto_1h = create_synthetic_crypto_1h_bars(days=1, start_date="2026-11-03", base_price=75000.0)
        df_crypto_4h = ds.aggregate_1h_to_4h_df(df_crypto_1h, ticker="BTC-USD")
        self.assertEqual(len(df_crypto_4h), 6, "Expected 6 4H bars per day for BTC-USD")
        for b_dt in df_crypto_4h.index:
            self.assertEqual(b_dt.date(), pd.Timestamp("2026-11-03").date(), "All crypto bars must remain within date")

    def test_4h_aggregation_early_close_and_intraday(self):
        """
        [Engineering Correction 1 Edge Cases]
        - Half-day session (e.g. 13:00 close): Only Bar 1 emitted, Bar 2 omitted without crashing.
        - In-progress session (e.g. 11:00 AM): Bar 1 dynamically formed, Bar 2 omitted without crashing.
        - Empty DataFrame: returns empty DataFrame gracefully.
        """
        # Case A: Half-day session (bars 09:30, 10:30, 11:30, 12:30 only)
        df_full = create_synthetic_rth_1h_bars(days=1, start_date="2026-11-27", base_price=100.0)
        df_half_day = df_full.iloc[0:4]  # 4 bars only
        res_half = ds.aggregate_1h_to_4h_df(df_half_day, ticker="SPY")
        self.assertEqual(len(res_half), 1, "Half-day session should produce exactly 1 4H bar")
        self.assertEqual(res_half.index[0].hour, 9)
        self.assertFalse(res_half.isnull().any().any(), "No NaN values in half-day aggregation")

        # Case B: In-progress session at 11:00 AM (bars 09:30 and 10:30 only)
        df_in_progress = df_full.iloc[0:2]
        res_in_prog = ds.aggregate_1h_to_4h_df(df_in_progress, ticker="SPY")
        self.assertEqual(len(res_in_prog), 1, "In-progress session should produce 1 dynamic 4H bar")
        self.assertAlmostEqual(res_in_prog.iloc[0]["Close"], df_in_progress["Close"].iloc[-1])

        # Case C: Empty input
        res_empty = ds.aggregate_1h_to_4h_df(pd.DataFrame(), ticker="SPY")
        self.assertTrue(res_empty.empty, "Empty input should return empty dataframe")

    def test_prepost_market_snapshot_does_not_mutate_historical_bars(self):
        """
        [Engineering Correction 2] Outside RTH (<09:30 or >16:00 ET), yesterday's
        closed 15:30 bar must remain 100% untouched.
        A temporary snapshot bar is appended at the sequence end to compute real-time RSI.
        """
        df_bars = create_synthetic_rth_1h_bars(days=1, start_date="2026-11-06", base_price=100.0)
        closed_last_bar = df_bars.iloc[-1].copy()
        original_len = len(df_bars)
        
        realtime_price = 108.50  # Pre-market surge of +8.5%
        
        # Scenario 1: PRE_MARKET session
        df_calc = ds.append_realtime_snapshot_bar(df_bars, realtime_price=realtime_price, current_session="PRE_MARKET")
        
        # Invariant 1: Original dataframe is NOT mutated in place
        self.assertEqual(len(df_bars), original_len, "Original dataframe length must be untouched")
        self.assertAlmostEqual(df_bars.iloc[-1]["Close"], closed_last_bar["Close"], places=4)
        
        # Invariant 2: In calculation dataframe, length is original_len + 1
        self.assertEqual(len(df_calc), original_len + 1, "Calculation dataframe must have 1 snapshot bar appended")
        
        # Invariant 3: Yesterday's closed 15:30 bar (second to last) remains completely identical
        hist_bar = df_calc.iloc[-2]
        self.assertAlmostEqual(hist_bar["Open"], closed_last_bar["Open"])
        self.assertAlmostEqual(hist_bar["High"], closed_last_bar["High"])
        self.assertAlmostEqual(hist_bar["Low"], closed_last_bar["Low"])
        self.assertAlmostEqual(hist_bar["Close"], closed_last_bar["Close"])
        
        # Invariant 4: Appended snapshot bar properties
        snapshot = df_calc.iloc[-1]
        self.assertAlmostEqual(snapshot["Close"], realtime_price, places=2)
        self.assertAlmostEqual(snapshot["Open"], closed_last_bar["Close"], places=2)
        self.assertAlmostEqual(snapshot["High"], max(closed_last_bar["Close"], realtime_price), places=2)
        self.assertAlmostEqual(snapshot["Low"], min(closed_last_bar["Close"], realtime_price), places=2)
        self.assertAlmostEqual(snapshot["Volume"], 0.0)

        # Scenario 2: POST_MARKET session behaves identically
        df_post = ds.append_realtime_snapshot_bar(df_bars, realtime_price=92.0, current_session="POST_MARKET")
        self.assertEqual(len(df_post), original_len + 1)
        self.assertAlmostEqual(df_post.iloc[-2]["Close"], closed_last_bar["Close"])
        self.assertAlmostEqual(df_post.iloc[-1]["Close"], 92.0)

        # Scenario 3: REGULAR session does NOT append snapshot bar
        df_reg = ds.append_realtime_snapshot_bar(df_bars, realtime_price=105.0, current_session="REGULAR")
        self.assertEqual(len(df_reg), original_len, "REGULAR session must not append synthetic snapshot bar")

        # Scenario 4: Zero price deviation does not create redundant bar
        df_no_dev = ds.append_realtime_snapshot_bar(df_bars, realtime_price=closed_last_bar["Close"], current_session="PRE_MARKET")
        self.assertEqual(len(df_no_dev), original_len, "Zero deviation should not append redundant snapshot bar")

    def test_1h_support_resistance_strict_boundary_protection(self):
        """
        [Engineering Correction 3] Invariant: 1H撑 < active_price < 1H压 must hold 100% strictly.
        - Breakdown (low_20h >= active_price): 1H撑 = min(POC, active_price - 1.5 * ATR_1H) < active_price.
        - Breakout (high_20h <= active_price): 1H压 = active_price + 1.5 * ATR_1H > active_price.
        - Flash crash / gap stress: Invariant preserved across extreme values.
        """
        # Create 20 1H bars with low around 95.0, high around 105.0
        records = []
        tz = "America/New_York"
        dt = pd.Timestamp("2026-11-03 09:30:00", tz=tz)
        for i in range(20):
            records.append({
                "Datetime": dt + pd.Timedelta(hours=i),
                "Open": 100.0,
                "High": 105.0 if i == 5 else 102.0,
                "Low": 95.0 if i == 10 else 98.0,
                "Close": 100.0,
                "Volume": 10000.0
            })
        df_20h = pd.DataFrame(records).set_index("Datetime")
        
        # Case 1: Normal inside range
        active_price = 100.0
        s, r, atr = ds.compute_and_protect_1h_sr(df_20h, active_price=active_price, poc=98.0)
        self.assertTrue(s < active_price < r, f"Expected {s} < {active_price} < {r}")
        self.assertGreater(atr, 0.0)

        # Case 2: Breakdown gap-down (active_price = 90.0 < low_20h = 95.0, POC = 98.0 > active_price)
        active_crash = 90.0
        s_down, r_down, atr_down = ds.compute_and_protect_1h_sr(df_20h, active_price=active_crash, poc=98.0)
        self.assertTrue(s_down < active_crash < r_down, f"Breakdown violated: {s_down} < {active_crash} < {r_down}")
        # When POC (98) is above active_price (90), POC candidate falls back to active_price - 1.5*ATR
        expected_s = round(active_crash - 1.5 * atr_down, 2)
        self.assertAlmostEqual(s_down, expected_s, delta=0.05, msg="Breakdown support must use active_price - 1.5 * ATR")

        # Case 3: Breakdown with valid lower POC (active_price = 92.0, POC = 88.0)
        active_p3 = 92.0
        poc_lower = 88.0
        s_poc, r_poc, atr_poc = ds.compute_and_protect_1h_sr(df_20h, active_price=active_p3, poc=poc_lower)
        self.assertTrue(s_poc < active_p3 < r_poc)
        expected_s_poc = round(min(poc_lower, active_p3 - 1.5 * atr_poc), 2)
        self.assertAlmostEqual(s_poc, expected_s_poc, delta=0.05)

        # Case 4: Breakout gap-up (active_price = 112.0 > high_20h = 105.0)
        active_surge = 112.0
        s_up, r_up, atr_up = ds.compute_and_protect_1h_sr(df_20h, active_price=active_surge, poc=98.0)
        self.assertTrue(s_up < active_surge < r_up, f"Breakout violated: {s_up} < {active_surge} < {r_up}")
        expected_r = round(active_surge + 1.5 * atr_up, 2)
        self.assertAlmostEqual(r_up, expected_r, delta=0.05, msg="Breakout resistance must use active_price + 1.5 * ATR")

        # Case 5: Adversarial Stress Test (Flash crash to 10.0 and pump to 1000.0)
        for extreme_p in [5.0, 10.0, 500.0, 2000.0]:
            s_ext, r_ext, _ = ds.compute_and_protect_1h_sr(df_20h, active_price=extreme_p, poc=None)
            self.assertTrue(s_ext < extreme_p < r_ext, f"Extreme invariant failed for {extreme_p}: {s_ext} < {extreme_p} < {r_ext}")

        # Case 6: Degenerate input (empty or single bar dataframe)
        s_deg, r_deg, _ = ds.compute_and_protect_1h_sr(pd.DataFrame(), active_price=100.0)
        self.assertTrue(s_deg < 100.0 < r_deg, "Degenerate input must still satisfy boundary invariant")

    def test_batch_fetch_and_btc_usd_bypass(self):
        """
        [Engineering Correction 4] Finnhub 429 Protection & BTC-USD Bypass:
        - 51 tickers must be fetched via multi-threaded batch download without Finnhub 429 vulnerabilities.
        - BTC-USD must bypass the Finnhub quote endpoint (which returns 0/null) and rely on yfinance.
        """
        tickers = ["SPY", "QQQ", "SOXL", "BTC-USD"]
        
        # Mock yfinance batch response
        dummy_df_1h = create_synthetic_rth_1h_bars(days=2)
        dummy_df_1d = create_synthetic_rth_1h_bars(days=2)
        
        with patch("yfinance.download") as mock_yf, \
             patch("requests.get") as mock_req:
            
            mock_yf.side_effect = [dummy_df_1h, dummy_df_1d]
            
            data = ds.fetch_sentinel_market_data(tickers)
            
            # Invariant 1: yfinance was called for batch fetch
            self.assertGreaterEqual(mock_yf.call_count, 1, "Expected yfinance batch download to be called")
            
            # Invariant 2: Finnhub quote endpoint was NEVER called for BTC-USD
            for call in mock_req.call_args_list:
                args, kwargs = call
                url = args[0] if args else kwargs.get("url", "")
                self.assertNotIn("symbol=BTC-USD", url, "Finnhub quote endpoint must not be queried for BTC-USD")
                self.assertNotIn("symbol=BTC", url, "Finnhub quote endpoint must not be queried for BTC")


# ==============================================================================
# Tier 2 Tests: Quantitative Engine & State Machine Invariants
# ==============================================================================

class TestDiscordSentinelTier2(unittest.TestCase):
    """
    Tier 2: Quantitative Engine & State Machine Invariants:
    6. Volume Profile POC calculation (140 bars, 25 bins, Typical Price)
    7. RSI state machine transitions (5 states, V3.2 cooldown & VWAP reclaim)
    8. Defense Factor Y and Divergence Alert (mean SCHD/KO/GLD, SOXX >= 25)
    9. 3X mirror resonance (SOXL x SOXS, TQQQ x SQQQ)
    """

    def test_volume_profile_poc_calculation(self):
        """
        Volume Profile POC: 140 1H bars, 25 bins, Typical Price (H+L+C)/3, volume accumulation.
        The POC must equal the midpoint of the bin with the highest accumulated volume.
        """
        # Construct 140 bars with a distinct volume spike around price 100.0
        records = []
        tz = "America/New_York"
        dt = pd.Timestamp("2026-10-15 09:30:00", tz=tz)
        
        # Overall price range: 80.0 to 120.0 (span = 40.0, bin width = 40/25 = 1.6)
        # We put 90% of all volume in the 99.0 ~ 101.0 region
        for i in range(140):
            if 60 <= i <= 80:
                h, l, c = 101.0, 99.0, 100.0
                v = 500000.0  # Massive institutional volume concentration
            elif i < 60:
                h, l, c = 85.0, 80.0, 82.0
                v = 1000.0
            else:
                h, l, c = 120.0, 115.0, 118.0
                v = 1000.0
                
            records.append({
                "Datetime": dt + pd.Timedelta(hours=i),
                "Open": c,
                "High": h,
                "Low": l,
                "Close": c,
                "Volume": v
            })
        df_140 = pd.DataFrame(records).set_index("Datetime")
        
        poc = ds.compute_volume_profile_poc(df_140)
        
        # Winning bin should center around 100.0
        self.assertAlmostEqual(poc, 100.0, delta=2.0, msg=f"POC {poc} must match winning volume bin around 100.0")

        # Edge case 1: Zero-volume across all bars (uniform time-at-price fallback)
        df_zero_vol = df_140.copy()
        df_zero_vol["Volume"] = 0.0
        poc_zero = ds.compute_volume_profile_poc(df_zero_vol)
        self.assertFalse(math.isnan(poc_zero), "Zero volume must not produce NaN")
        self.assertGreater(poc_zero, 0.0, "Zero volume POC must be positive")

        # Edge case 2: Flat price line (High == Low == Close == 100.0)
        df_flat = df_140.copy()
        df_flat["High"] = 100.0
        df_flat["Low"] = 100.0
        df_flat["Close"] = 100.0
        poc_flat = ds.compute_volume_profile_poc(df_flat)
        self.assertAlmostEqual(poc_flat, 100.0, places=2, msg="Flat price POC must equal the constant price")

        # Edge case 3: Short history (< 140 bars, e.g. 10 bars)
        df_short = df_140.tail(10)
        poc_short = ds.compute_volume_profile_poc(df_short)
        self.assertFalse(math.isnan(poc_short), "Short history POC must not produce NaN")

    def test_rsi_state_machine_transitions(self):
        """
        6 级真话状态机 (彻底废除笼统的 [安全持股]):
        1. 🔥【双周期见底·重点低吸】:
           - 若处于暴跌冷却期: 🔥【双周期见底·⚠️暴跌冷却中·等企稳】 / 🔥【双周期见底·✅右侧企稳】
           - 若非冷却: price >= poc -> 🔥【双周期见底·黄金低吸区】; price < poc -> 🔥【双周期见底·超跌博反弹】
        2. 🚀【突破成功·右侧走强】: 突破前 20 根 1H 前高或放量穿越成本
        3. 🎯【回踩主力成本·盯紧企稳】: 现价在 POC 上方 0% ~ +1.8%
        4. 🛑【双周期触顶·分批止盈】: 4H_RSI >= 68 and 1D_RSI >= 65
        5. 📈【站稳主力上方·多头顺风】: 现价 > POC * 1.018
        6. ⚠️【跌破主力成本·水下弱势】: 现价 < POC (严禁标为安全持股)
        """
        # Case 1: Standard Dual-Cycle Bottom (Price < POC -> 超跌博反弹)
        st1 = ds.evaluate_rsi_state(rsi_4h=30.0, rsi_1d=38.0, price=100.0, poc=110.0, is_cooling=False, price_gt_vwap=False)
        self.assertEqual(st1, "🔥【双周期见底·超跌博反弹】")

        # Case 2: POC Proximity Bottom (Price >= POC within 1.5% and 4H_RSI <= 35 -> 黄金低吸区)
        st2 = ds.evaluate_rsi_state(rsi_4h=34.0, rsi_1d=50.0, price=101.0, poc=100.0, is_cooling=False, price_gt_vwap=False)
        self.assertEqual(st2, "🔥【双周期见底·黄金低吸区】")

        # Case 3: V3.2 3-Day Crash Cooling Observation (Below VWAP)
        st3 = ds.evaluate_rsi_state(rsi_4h=29.0, rsi_1d=35.0, price=100.0, poc=110.0, is_cooling=True, price_gt_vwap=False)
        self.assertEqual(st3, "🔥【双周期见底·⚠️暴跌冷却中·等企稳】")

        # Case 4: V3.2 3-Day Crash VWAP Reclaim (Above VWAP)
        st4 = ds.evaluate_rsi_state(rsi_4h=29.0, rsi_1d=35.0, price=105.0, poc=110.0, is_cooling=True, price_gt_vwap=True)
        self.assertEqual(st4, "🔥【双周期见底·✅右侧企稳】")

        # Case 5: Dual-Cycle Top (Overbought)
        st5 = ds.evaluate_rsi_state(rsi_4h=72.0, rsi_1d=68.0, price=100.0, poc=100.0, is_cooling=False, price_gt_vwap=False)
        self.assertEqual(st5, "🛑【双周期触顶·分批止盈】")

        # Case 6: Breakout Success (Price > 20H High)
        st6 = ds.evaluate_rsi_state(rsi_4h=58.0, rsi_1d=55.0, price=112.0, poc=100.0, prev_20_high=110.0)
        self.assertEqual(st6, "🚀【突破成功·右侧走强】")

        # Case 7: Pullback to POC Support (Tightened: <= 0.8% and 4H_RSI <= 48)
        st7_tight = ds.evaluate_rsi_state(rsi_4h=45.0, rsi_1d=50.0, price=100.5, poc=100.0)
        self.assertEqual(st7_tight, "🎯【回踩主力成本·盯紧企稳】")
        # Beyond 0.8% or RSI > 48 falls back to bull tailwind
        st7_loose = ds.evaluate_rsi_state(rsi_4h=52.0, rsi_1d=50.0, price=101.2, poc=100.0)
        self.assertEqual(st7_loose, "📈【站稳主力上方·多头顺风】")

        # Case 8: Bull Tailwind (> POC * 1.018)
        st8 = ds.evaluate_rsi_state(rsi_4h=55.0, rsi_1d=52.0, price=108.0, poc=100.0)
        self.assertEqual(st8, "📈【站稳主力上方·多头顺风】")

        # Case 9: Bear Underwater (< POC)
        st9 = ds.evaluate_rsi_state(rsi_4h=48.0, rsi_1d=45.0, price=95.0, poc=100.0)
        self.assertEqual(st9, "⚠️【跌破主力成本·水下弱势】")

        # Edge Case: Wilder RSI all positive bars -> 100.0 (no NaN)
        s_pos = pd.Series([100.0 + i * 2.0 for i in range(25)])
        rsi_pos = ds.compute_series_rsi(s_pos, 14)
        self.assertFalse(np.isnan(rsi_pos.iloc[-1]))
        self.assertGreater(rsi_pos.iloc[-1], 90.0)

    def test_defense_factor_y_and_divergence(self):
        """
        Defense Factor Y & Macro Divergence:
        - Factor Y = mean(4H_RSI of SCHD, KO, GLD)
        - Missing component fallback (1 or 2 missing still computes valid mean)
        - Divergence Alert triggers when: Factor Y 4H_RSI - SOXX 4H_RSI >= 25.0 and SOXX 4H_RSI <= 32.0
        """
        # Subtest A: Factor Y mathematical mean and fallbacks
        full_data = {
            "SCHD": {"rsi_4h": 60.0, "price": 35.0, "poc": 34.0, "support": 34.5, "resistance": 36.0},
            "KO": {"rsi_4h": 55.0, "price": 95.0, "poc": 94.0, "support": 94.2, "resistance": 96.5},
            "GLD": {"rsi_4h": 65.0, "price": 375.0, "poc": 370.0, "support": 372.0, "resistance": 380.0}
        }
        res_y = ds.compute_factor_y(full_data)
        self.assertAlmostEqual(res_y["rsi_4h"], 60.0, places=2, msg="Mean of (60, 55, 65) is 60.0")
        self.assertAlmostEqual(res_y["price"], (35.0 + 95.0 + 375.0) / 3.0, places=2)
        self.assertTrue(res_y["support"] < res_y["price"] < res_y["resistance"])

        # Partial fallback (GLD missing)
        partial_data = {
            "SCHD": {"rsi_4h": 60.0, "price": 35.0, "poc": 34.0, "support": 34.5, "resistance": 36.0},
            "KO": {"rsi_4h": 50.0, "price": 95.0, "poc": 94.0, "support": 94.2, "resistance": 96.5}
        }
        res_partial = ds.compute_factor_y(partial_data)
        self.assertAlmostEqual(res_partial["rsi_4h"], 55.0, places=2, msg="Mean of available (60, 50) is 55.0")

        # Subtest B: Divergence Alert evaluation
        # Trigger condition: Factor Y = 61.1, SOXX = 21.5 -> diff = 39.6 >= 25, SOXX <= 32
        analysis_div = create_synthetic_market_analysis(include_resonance=False, include_divergence=True)
        res_alerts = ds.evaluate_cross_asset_resonance(analysis_div)
        
        has_div_alert = any("左侧背离" in a for a in res_alerts.get("divergence_alerts", []))
        self.assertTrue(has_div_alert, "Divergence alert must trigger when Factor Y - SOXX >= 25 and SOXX <= 32")

        # Negative test: Factor Y = 45.0, SOXX = 30.0 -> diff = 15.0 < 25
        analysis_no_div = create_synthetic_market_analysis(include_resonance=False, include_divergence=False)
        analysis_no_div["factor_y"]["rsi_4h"] = 45.0
        analysis_no_div["tickers"]["SOXX"]["rsi_4h"] = 30.0
        res_no_div = ds.evaluate_cross_asset_resonance(analysis_no_div)
        self.assertEqual(len(res_no_div.get("divergence_alerts", [])), 0, "Divergence must not trigger below threshold")

    def test_3x_mirror_resonance(self):
        """
        3X Leveraged Mirror Resonance:
        - SOXL bottom + SOXS top -> ⚡【SOXL×SOXS 底部共振】
        - TQQQ bottom + SQQQ top -> ⚡【TQQQ×SQQQ 底部共振】
        - SOXL top + SOXS bottom -> 🛑 【SOXL×SOXS 顶部共振】
        """
        analysis = create_synthetic_market_analysis(include_resonance=True, include_divergence=False)
        res = ds.evaluate_cross_asset_resonance(analysis)
        
        resonance_list = res.get("resonance_alerts", [])
        soxl_soxs_found = any("SOXL×SOXS 底部共振" in a for a in resonance_list)
        tqqq_sqqq_found = any("TQQQ×SQQQ 底部共振" in a for a in resonance_list)
        
        self.assertTrue(soxl_soxs_found, "Expected SOXL x SOXS bottom resonance alert")
        self.assertTrue(tqqq_sqqq_found, "Expected TQQQ x SQQQ bottom resonance alert")

        # Inverse test: Top resonance
        analysis_top = create_synthetic_market_analysis(include_resonance=False, include_divergence=False)
        analysis_top["tickers"]["SOXL"]["status"] = "[🛑 双周期触顶]"
        analysis_top["tickers"]["SOXL"]["rsi_4h"] = 72.0
        analysis_top["tickers"]["SOXS"]["status"] = "[🔥 双周期见底]"
        analysis_top["tickers"]["SOXS"]["rsi_4h"] = 28.0
        res_top = ds.evaluate_cross_asset_resonance(analysis_top)
        
        top_found = any("SOXL×SOXS 顶部共振" in a for a in res_top.get("resonance_alerts", []))
        self.assertTrue(top_found, "Expected SOXL x SOXS top resonance alert")


# ==============================================================================
# Tier 3 Tests: Discord Embed Formatting & Anti-Grayblock Invariants
# ==============================================================================

class TestDiscordSentinelTier3(unittest.TestCase):
    """
    Tier 3: Discord Embed Formatting & Character Limit Invariants:
    10. Native Discord Markdown with zero grey codeblocks (```text or ```)
    11. Discord payload character limits (Message 1 <= 2000, 2A <= 5500, 2B <= 5500, max 10 embeds)
    """

    def test_embed_native_markdown_no_codeblocks(self):
        """
        [Anti-Grayblock Invariant]
        Strictly zero occurrences of triple backticks (``` or ```text) across all
        embed descriptions, titles, and fields. Native bold tags (**sym**) must be used.
        """
        analysis = create_synthetic_market_analysis(include_resonance=True, include_divergence=True)
        payload_1, payload_2a, payload_2b = ds.build_discord_payloads(analysis)
        
        all_payloads = [p for p in [payload_1, payload_2a, payload_2b] if p]
        self.assertGreaterEqual(len(all_payloads), 2, "Expected at least Payload 2A and 2B")
        
        for p_idx, payload in enumerate(all_payloads):
            embeds = payload.get("embeds", [])
            for e_idx, embed in enumerate(embeds):
                title = embed.get("title", "")
                desc = embed.get("description", "")
                footer = embed.get("footer", {}).get("text", "")
                
                # Check for forbidden codeblocks
                self.assertNotIn("```", desc, f"Payload {p_idx} Embed {e_idx} contains forbidden ``` codeblock in description")
                self.assertNotIn("```text", desc, f"Payload {p_idx} Embed {e_idx} contains forbidden ```text in description")
                self.assertNotIn("```", title, f"Payload {p_idx} Embed {e_idx} contains ``` in title")
                self.assertNotIn("```", footer, f"Payload {p_idx} Embed {e_idx} contains ``` in footer")
                
                # Check for fields if any
                for field in embed.get("fields", []):
                    self.assertNotIn("```", field.get("name", ""))
                    self.assertNotIn("```", field.get("value", ""))
                    
                # Verify native bold formatting exists in descriptions containing tickers
                if "现:" in desc:
                    self.assertIn("**", desc, f"Payload {p_idx} Embed {e_idx} should use native **bold** markdown")

    def test_discord_payload_character_limits(self):
        """
        [Payload Limit Invariant]
        Discord webhook constraints:
        - Max 10 embeds per message.
        - Max 4,096 characters per embed description.
        - Max 6,000 characters total across all fields in a single message payload.
        To ensure stability, Sentinel enforces safe ceiling <= 5,500 chars for Part 2A & Part 2B.
        """
        analysis = create_synthetic_market_analysis(include_resonance=True, include_divergence=True)
        payload_1, payload_2a, payload_2b = ds.build_discord_payloads(analysis)
        
        def calculate_payload_chars(payload: dict) -> int:
            total = 0
            for embed in payload.get("embeds", []):
                total += len(embed.get("title", ""))
                total += len(embed.get("description", ""))
                total += len(embed.get("footer", {}).get("text", ""))
                for f in embed.get("fields", []):
                    total += len(f.get("name", "")) + len(f.get("value", ""))
            return total

        # Payload 1 (Strong Alert)
        if payload_1:
            p1_embeds = payload_1.get("embeds", [])
            self.assertLessEqual(len(p1_embeds), 10, "Payload 1 exceeds 10 embeds")
            p1_chars = calculate_payload_chars(payload_1)
            self.assertLessEqual(p1_chars, 2000, f"Payload 1 chars {p1_chars} exceeds 2000")

        # Payload 2A (Tiers 1, 2, 3: 3 embeds consolidated, no 1/2 or 2/2)
        p2a_embeds = payload_2a.get("embeds", [])
        self.assertEqual(len(p2a_embeds), 3, f"Payload 2A should contain 3 embeds (Tier 1 + Tier 2 + Tier 3), got {len(p2a_embeds)}")
        self.assertLessEqual(len(p2a_embeds), 10, "Payload 2A exceeds 10 embeds limit")
        for embed in p2a_embeds:
            self.assertLessEqual(len(embed.get("description", "")), 4096, "Embed description exceeds 4096")
        p2a_chars = calculate_payload_chars(payload_2a)
        self.assertLessEqual(p2a_chars, 5500, f"Payload 2A total chars {p2a_chars} exceeds safe limit 5500 (Discord ceiling 6000)")

        # Payload 2B (Tiers 4, 5: 2 embeds consolidated, no 1/2 or 2/2)
        p2b_embeds = payload_2b.get("embeds", [])
        self.assertEqual(len(p2b_embeds), 2, f"Payload 2B should contain 2 embeds (Tier 4 + Tier 5), got {len(p2b_embeds)}")
        self.assertLessEqual(len(p2b_embeds), 10, "Payload 2B exceeds 10 embeds limit")
        for embed in p2b_embeds:
            self.assertLessEqual(len(embed.get("description", "")), 4096, "Embed description exceeds 4096")
        p2b_chars = calculate_payload_chars(payload_2b)
        self.assertLessEqual(p2b_chars, 5500, f"Payload 2B total chars {p2b_chars} exceeds safe limit 5500 (Discord ceiling 6000)")

        # Total embeds across Part 2A and 2B must be exactly 5
        self.assertEqual(len(p2a_embeds) + len(p2b_embeds), 5, "Message 2 total embeds across 2A and 2B must be 5")


# ==============================================================================
# Tier 4 Tests: Radar Daemon Linkage & Safety Invariants
# ==============================================================================

class TestDiscordSentinelTier4(unittest.TestCase):
    """
    Tier 4: Radar Daemon Linkage & Safety Invariants:
    12. Asynchronous execution in daemon thread & complete exception isolation
    """

    def test_radar_linkage_async_and_isolated(self):
        """
        [Radar Daemon Invariant]
        - Discord sentinel dispatch must run asynchronously in a daemon thread.
        - Network failures (timeouts, 500s, 429s) must be isolated with zero impact on caller.
        """
        # Test 1: Exception isolation in run_sentinel_cycle_safe
        with patch("requests.post") as mock_post:
            import requests
            mock_post.side_effect = requests.exceptions.ConnectTimeout("Discord endpoint timed out")
            
            # Calling run_sentinel_cycle_safe must catch and log, never re-raise
            try:
                ds.run_sentinel_cycle_safe(macro_data={}, stocks_sorted=[], weather_info={}, now_str="2026-11-10 09:30:00")
            except Exception as e:
                self.fail(f"run_sentinel_cycle_safe raised unhandled exception on network timeout: {e}")

        # Test 2: Daemon thread contract verification
        import threading
        launched_threads = []
        
        def spy_thread_start(thread_instance):
            launched_threads.append(thread_instance)

        # Check integrated_market_radar linkage helper if present or contract
        import integrated_market_radar as imr
        dispatch_fn = getattr(imr, "dispatch_discord_sentinel_async", None)
        
        if dispatch_fn:
            with patch.object(threading.Thread, "start", autospec=True) as mock_start:
                dispatch_fn(macro_data={}, stocks_sorted=[], weather_info={}, now_str="2026-11-10 09:30:00")
                self.assertGreaterEqual(mock_start.call_count, 1, "Expected background thread to be started")


# ==============================================================================
# Universe & Configuration Invariants
# ==============================================================================

class TestDiscordSentinelUniverseAndEnv(unittest.TestCase):
    """
    Universe & Environment Invariants:
    13. 51 tickers uniqueness and tier distribution
    14. .env webhook URL configuration
    """

    def test_universe_structure_and_completeness(self):
        """Verify the 5 Tiers universe contains exactly 51 unique tickers."""
        t1 = ds.SENTINEL_UNIVERSE_TIERS["TIER_1_3X"]
        t2 = ds.SENTINEL_UNIVERSE_TIERS["TIER_2_CORE"]
        t3 = ds.SENTINEL_UNIVERSE_TIERS["TIER_3_CHIPS"]
        t4 = ds.SENTINEL_UNIVERSE_TIERS["TIER_4_TECH"]
        t5 = ds.SENTINEL_UNIVERSE_TIERS["TIER_5_DEFENSE"]

        self.assertEqual(len(t1), 4, "Tier 1 must contain 4 tickers")
        self.assertEqual(len(t2), 12, "Tier 2 must contain 12 tickers")
        self.assertEqual(len(t3), 13, "Tier 3 must contain 13 tickers")
        self.assertEqual(len(t4), 13, "Tier 4 must contain 13 tickers")
        self.assertEqual(len(t5), 9, "Tier 5 must contain 9 tickers")

        all_tickers = t1 + t2 + t3 + t4 + t5
        self.assertEqual(len(all_tickers), 51, "Total tickers must equal 51")
        self.assertEqual(len(set(all_tickers)), 51, "All 51 tickers must be strictly unique (no duplicates)")

    def test_env_webhook_configuration(self):
        """Verify DISCORD_WEBHOOK_URL is configured in .env."""
        env_path = os.path.join(PROJECT_DIR, ".env")
        self.assertTrue(os.path.exists(env_path), f".env file must exist at {env_path}")
        
        with open(env_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        self.assertIn("DISCORD_WEBHOOK_URL=", content, ".env must contain DISCORD_WEBHOOK_URL")
        self.assertIn("discordapp.com/api/webhooks/", content, "Webhook URL must point to discordapp.com webhooks")


# ==============================================================================
# Test Runner & Reporting
# ==============================================================================

def run_sentinel_test_suite() -> unittest.TestResult:
    print("=" * 80)
    print(" 🚀 RUNNING DISCORD SENTINEL & ENGINEERING INVARIANTS TEST SUITE")
    print("=" * 80)
    
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    test_classes = [
        TestDiscordSentinelTier1,
        TestDiscordSentinelTier2,
        TestDiscordSentinelTier3,
        TestDiscordSentinelTier4,
        TestDiscordSentinelUniverseAndEnv
    ]
    
    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))
        
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    start_time = time.time()
    result = runner.run(suite)
    elapsed = time.time() - start_time
    
    print("\n" + "=" * 80)
    print(f" 📊 TEST EXECUTION SUMMARY (Elapsed: {elapsed:.3f}s)")
    print("=" * 80)
    print(f" Total Tests Run: {result.testsRun}")
    print(f" Passed:          {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f" Failures:        {len(result.failures)}")
    print(f" Errors:          {len(result.errors)}")
    print("=" * 80)
    
    return result


if __name__ == "__main__":
    res = run_sentinel_test_suite()
    sys.exit(0 if res.wasSuccessful() else 1)
