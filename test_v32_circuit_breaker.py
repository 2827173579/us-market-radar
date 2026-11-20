#!/usr/bin/env python3
"""
test_v32_circuit_breaker.py - Comprehensive Unit & Scenario Test Suite for V3.2 Circuit Breaker and V3.1 Execution

Authoritative references:
- ORIGINAL_REQUEST.md (§R1, §R2, §Acceptance Criteria)
- PROJECT.md (§Architecture, §Feature Inventory, §Interface Contracts)

Scenarios Covered:
- Scenario 1 (Dead-cat Bounce Defense): Stock crashed in past 3 days (daily_ret <= -6.0% or gap_down <= -4.5%,
  Prev_Crash_3d == True). Today opens flat (+0.2%), 5m RSI rises with 5m green candle. Verify evaluate_intraday_entry
  freezes entry: intraday_entry_ready == False, is_cooling_halt == True, banner contains
  '⛔ 暴跌冷却期 (3日内曾大跌·等待日线企稳确认)'.
- Scenario 2 (Intraday False-Green Non-Unlock): Stock under 3-day cooling bounces +1.0% intraday with unclosed
  green candle, but does not meet strict unlock conditions. Verify cooling period remains frozen (is_cooling_halt == True).
- Scenario 3 (Intraday Valid Recovery Unlock & Condition a):
  * 3A: Stock under 3-day cooling surges >= +2.5% intraday, recovers >50% of previous crash day's red body,
        and holds above VWAP and EMA5. Verify cooling period unlocks (is_cooling_halt == False).
  * 3B: Condition (a): previous closed day confirmed right-side rebound candle (Prev_Is_Right_Turn == True)
        unlocks cooling period.
- Scenario 4 (High-to-Low Flush / 断头铡刀): Stock reached rth_high = 110.0, current price = 103.0 (flush <= -6.0%)
  and pct_change <= -1.0%. Verify evaluate_intraday_entry triggers is_crash_halt == True, and intraday_reason contains '断头铡刀防接刀'.
- Scenario 5 (ATR Dual-Track Adaptive Threshold):
  * Low-volatility stock (ATR% = 1.5%): verify crash_thresh == -3.8%.
  * Mid-volatility stock (ATR% = 2.2%): verify crash_thresh == -4.4%.
  * High-volatility stock (ATR% = 3.0%): verify crash_thresh == -5.2% (capped at -5.2%).
- Scenario 6 (Deadlock Unlock on Pre/Post-Market Reversal): Regular hours pct_change is -6.0% (below crash threshold),
  but post-market surges +8.0% (safe_ext = +8.0%, pct_change + safe_ext = +2.0% > -2.5%). Verify is_crash_halt == False.
- Scenario 7 (V3.1 9:30 Open Limit & Execution Sizing):
  * Optimal limit calculation: rth_close * 1.020 for 9:30 open, round(active_price * 1.005, 2) during market hours.
  * Gap-down cancel line: max(low_price, last * 0.985).
  * Structural stop loss: max(min(low_price - 0.5 * atr, last - 1.8 * atr), last * 0.932).
  * Relative strength filter (rs_60 >= -8.0) and V3.1 oversold bounce signal (min_rsi_3d <= 32.0, green candle >= +1.5%, vol_ratio >= 1.15).
- Indicator Computation Suite:
  * Materialization of Crash_3d, Prev_Crash_3d, Is_Right_Turn, Prev_Is_Right_Turn, Prev_Crash_Body, Prev_Crash_Close.

Usage:
  python test_v32_circuit_breaker.py
  python -m unittest test_v32_circuit_breaker.py
"""

import os
import sys
import math
import unittest
from typing import Dict, Any, Optional

import numpy as np
import pandas as pd

# Ensure stdout handles UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Configure project paths
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import integrated_market_radar as imr
from integrated_market_radar import (
    evaluate_intraday_entry,
    evaluate_stock_signal,
    compute_indicators,
)

try:
    from v31_strategy import compute_rolling_indicators
except Exception:
    compute_rolling_indicators = None


# ==============================================================================
# Mathematical Reference Functions (Authoritative Oracle)
# ==============================================================================
def calc_expected_atr_threshold(active_price: float, atr: float) -> float:
    """Derives dynamic crash threshold per ORIGINAL_REQUEST §R1.3:
    atr_pct = (atr / active_price * 100.0) if (atr > 0 and active_price > 0) else 3.0
    crash_thresh = -max(3.8, min(5.2, 2.0 * atr_pct))
    """
    atr_pct = (atr / active_price * 100.0) if (atr > 0 and active_price > 0) else 3.0
    return -max(3.8, min(5.2, 2.0 * atr_pct))


def calc_expected_flush_pct(active_price: float, rth_high: float) -> float:
    """Derives high-to-low flush percentage per ORIGINAL_REQUEST §R1.2:
    ((active_price - rth_high) / rth_high * 100.0)
    """
    return ((active_price - rth_high) / rth_high * 100.0) if (rth_high and rth_high > 0) else 0.0


def calc_expected_cancel_line(low_price: Optional[float], last: float) -> float:
    """Derives overnight gap-down cancel line per ORIGINAL_REQUEST §R2.1:
    max(low_price, last * 0.985)
    """
    sig_low = low_price if (low_price is not None and low_price > 0) else (last * 0.985)
    return max(sig_low, last * 0.985)


def calc_expected_structural_stop(low_price: Optional[float], last: float, atr: float) -> float:
    """Derives structural stop loss per ORIGINAL_REQUEST §R2.2:
    max(min(low_price - 0.5 * atr, last - 1.8 * atr), last * 0.932)
    """
    sig_low = low_price if (low_price is not None and low_price > 0) else last
    if atr > 0:
        return max(min(sig_low - 0.5 * atr, last - 1.8 * atr), last * 0.932)
    return last * 0.95


# ==============================================================================
# Test Helper: Call evaluate_intraday_entry matching Contract
# ==============================================================================
def run_evaluate_intraday_entry(
    active_price: float = 100.0,
    fib_low: float = 95.0,
    fib_high: float = 105.0,
    ma50: float = 90.0,
    is_pit: bool = True,
    is_stop: bool = False,
    dist_to_pit: float = 0.0,
    intra_info: Optional[Dict[str, Any]] = None,
    earnings_info: Optional[Dict[str, Any]] = None,
    rs_20d: float = 0.0,
    atr: float = 2.0,
    daily_rsi: float = 50.0,
    options_info: Optional[Dict[str, Any]] = None,
    pct_change: float = 0.0,
    ext_pct: Optional[float] = None,
    rth_high: Optional[float] = None,
    prev_crash_3d: bool = False,
    prev_is_right_turn: bool = False,
    ema5: float = 0.0,
    prev_crash_body: float = 0.0,
    prev_crash_close: float = 0.0,
    session_code: str = "REGULAR",
    ticker: str = "",
) -> Dict[str, Any]:
    """Helper to dispatch evaluate_intraday_entry according to PROJECT.md interface contract."""
    info = intra_info.copy() if intra_info is not None else {}
    if "vwap" not in info:
        info["vwap"] = active_price
    if "rth_close" not in info:
        info["rth_close"] = active_price
    if "rth_low" not in info:
        info["rth_low"] = active_price
    if "rth_high" not in info and rth_high is not None:
        info["rth_high"] = rth_high

    try:
        return evaluate_intraday_entry(
            active_price=active_price,
            fib_low=fib_low,
            fib_high=fib_high,
            ma50=ma50,
            is_pit=is_pit,
            is_stop=is_stop,
            dist_to_pit=dist_to_pit,
            intra_info=info,
            earnings_info=earnings_info,
            rs_20d=rs_20d,
            atr=atr,
            daily_rsi=daily_rsi,
            options_info=options_info,
            pct_change=pct_change,
            ext_pct=ext_pct,
            rth_high=rth_high,
            prev_crash_3d=prev_crash_3d,
            prev_is_right_turn=prev_is_right_turn,
            ema5=ema5,
            prev_crash_body=prev_crash_body,
            prev_crash_close=prev_crash_close,
            session_code=session_code,
            ticker=ticker,
        )
    except TypeError as te:
        if "unexpected keyword argument" in str(te):
            raise AssertionError(
                f"evaluate_intraday_entry() contract violation: {te}. "
                f"V3.2 requires signature to support [rth_high, prev_crash_3d, prev_is_right_turn, "
                f"ema5, prev_crash_body, prev_crash_close, session_code]."
            ) from te
        raise


# ==============================================================================
# Unit & Scenario Test Suite
# ==============================================================================
class TestV32CircuitBreaker(unittest.TestCase):
    """Test Suite verifying V3.2 Crash Circuit Breaker Upgrades & V3.1 Live Open Alignment."""

    # --------------------------------------------------------------------------
    # Scenario 1: Dead-cat Bounce Defense (3-Day Cooling Period Freeze)
    # --------------------------------------------------------------------------
    def test_scenario_1_dead_cat_bounce_defense(self):
        """Scenario 1: Stock crashed in past 3 days (Prev_Crash_3d == True).
        Today opens flat (+0.2%), 5m RSI turns up with a green candle.
        Verify evaluate_intraday_entry freezes entry:
        - intraday_entry_ready == False
        - is_cooling_halt == True
        - banner contains '⛔ 暴跌冷却期 (3日内曾大跌·等待日线企稳确认)'
        - optimal_limit == 0.0
        """
        intra_info = {
            "vwap": 100.0,
            "rsi_5m": 42.0,
            "prev_rsi_5m": 38.0,
            "min_rsi_5m_30m": 33.0,
            "rvol_5m": 1.3,
            "is_5m_green": True,
            "is_5m_higher_low": True,
            "rth_low": 99.8,
            "rth_close": 100.0,
        }

        res = run_evaluate_intraday_entry(
            active_price=100.2,
            pct_change=0.2,
            prev_crash_3d=True,
            prev_is_right_turn=False,
            ema5=101.5,
            prev_crash_body=6.0,
            prev_crash_close=100.0,
            is_pit=True,
            intra_info=intra_info,
        )

        self.assertFalse(
            res.get("intraday_entry_ready"),
            "Dead-cat bounce must NOT allow intraday entry when in 3-day cooling period",
        )
        self.assertTrue(
            res.get("is_cooling_halt"),
            "is_cooling_halt must be True for stock under 3-day cooling without stabilization",
        )
        self.assertIn(
            "⛔ 暴跌冷却期 (3日内曾大跌·等待日线企稳确认)",
            res.get("intraday_status_text", ""),
            "intraday_status_text must display the 3-day cooling banner",
        )
        self.assertEqual(
            res.get("optimal_limit"),
            0.0,
            "optimal_limit must be frozen to 0.0 during cooling halt",
        )

    # --------------------------------------------------------------------------
    # Scenario 2: Intraday False-Green Non-Unlock
    # --------------------------------------------------------------------------
    def test_scenario_2_intraday_false_green_non_unlock(self):
        """Scenario 2: Stock under 3-day cooling bounces +1.0% intraday with an unclosed green candle,
        but fails to satisfy strict unlock conditions (+2.5%, >50% body recovery, >VWAP & >EMA5).
        Verify cooling period remains frozen (is_cooling_halt == True).
        """
        intra_info = {
            "vwap": 100.8,
            "rsi_5m": 45.0,
            "prev_rsi_5m": 40.0,
            "min_rsi_5m_30m": 34.0,
            "rvol_5m": 1.1,
            "is_5m_green": True,
            "is_5m_higher_low": True,
            "rth_low": 99.5,
            "rth_close": 100.0,
        }

        res = run_evaluate_intraday_entry(
            active_price=101.0,
            pct_change=1.0,  # Below +2.5% threshold
            prev_crash_3d=True,
            prev_is_right_turn=False,
            ema5=102.0,
            prev_crash_body=6.0,
            prev_crash_close=100.0,
            is_pit=True,
            intra_info=intra_info,
        )

        self.assertTrue(
            res.get("is_cooling_halt"),
            "Weak +1.0% intraday bounce must NOT unlock 3-day cooling halt",
        )
        self.assertFalse(res.get("intraday_entry_ready"))
        self.assertEqual(res.get("optimal_limit"), 0.0)

    # --------------------------------------------------------------------------
    # Scenario 3: Intraday Valid Recovery Unlock & Condition (a)
    # --------------------------------------------------------------------------
    def test_scenario_3a_intraday_valid_recovery_unlock(self):
        """Scenario 3A: Stock under 3-day cooling surges >= +2.5% intraday,
        recovers >50% of previous crash day's red body, and holds above VWAP and EMA5.
        Verify cooling period unlocks (is_cooling_halt == False).
        """
        # Previous crash day: close was 100.0, red body was 4.0.
        # 50% recovery target: 100.0 + 0.5 * 4.0 = 102.0.
        # Today: active_price = 103.0 (+3.0% >= +2.5%), recovers >50% of body.
        # VWAP = 101.5 (price > VWAP), EMA5 = 102.0 (price > EMA5).
        intra_info = {
            "vwap": 101.5,
            "rsi_5m": 55.0,
            "prev_rsi_5m": 50.0,
            "min_rsi_5m_30m": 35.0,
            "rvol_5m": 1.5,
            "is_5m_green": True,
            "is_5m_higher_low": True,
            "rth_low": 99.5,
            "rth_close": 100.0,
        }

        res = run_evaluate_intraday_entry(
            active_price=103.0,
            pct_change=3.0,
            prev_crash_3d=True,
            prev_is_right_turn=False,
            ema5=102.0,
            prev_crash_body=4.0,
            prev_crash_close=100.0,
            is_pit=True,
            intra_info=intra_info,
        )

        self.assertFalse(
            res.get("is_cooling_halt"),
            "Valid intraday recovery (>=+2.5%, >50% red body, >VWAP, >EMA5) MUST unlock cooling halt",
        )
        self.assertFalse(
            res.get("is_crash_halt"),
            "Stock meeting valid intraday recovery should not be halted for crash",
        )

    def test_scenario_3b_condition_a_prior_day_right_turn_unlock(self):
        """Scenario 3B: Condition (a) Previous closed day confirmed right-side rebound
        candle (Prev_Is_Right_Turn == True).
        Verify cooling period unlocks (is_cooling_halt == False) even on a flat day (+0.4%).
        """
        intra_info = {
            "vwap": 100.0,
            "rsi_5m": 50.0,
            "prev_rsi_5m": 48.0,
            "min_rsi_5m_30m": 40.0,
            "rvol_5m": 1.0,
            "is_5m_green": True,
            "is_5m_higher_low": True,
            "rth_low": 99.8,
            "rth_close": 100.0,
        }

        res = run_evaluate_intraday_entry(
            active_price=100.4,
            pct_change=0.4,
            prev_crash_3d=True,
            prev_is_right_turn=True,  # Condition (a) satisfied
            ema5=100.0,
            prev_crash_body=5.0,
            prev_crash_close=99.0,
            is_pit=True,
            intra_info=intra_info,
        )

        self.assertFalse(
            res.get("is_cooling_halt"),
            "Condition (a) (Prev_Is_Right_Turn == True) MUST unlock cooling halt",
        )

    # --------------------------------------------------------------------------
    # Scenario 4: High-to-Low Flush / 断头铡刀
    # --------------------------------------------------------------------------
    def test_scenario_4_high_to_low_flush_executioners_blade(self):
        """Scenario 4: High-to-Low Flush ("Executioner's Blade" / 断头铡刀).
        Stock reached rth_high = 110.0, current price = 103.0 (flush = -6.36% <= -6.0%)
        and pct_change = -1.5% (<= -1.0%).
        Verify evaluate_intraday_entry triggers:
        - is_crash_halt == True
        - intraday_reason contains '断头铡刀防接刀'
        - optimal_limit == 0.0
        - intraday_entry_ready == False
        """
        rth_high = 110.0
        active_price = 103.0
        flush_pct = calc_expected_flush_pct(active_price, rth_high)
        self.assertLessEqual(flush_pct, -6.0, "Derived flush_pct must be <= -6.0%")

        intra_info = {
            "rth_high": rth_high,
            "vwap": 105.0,
            "rsi_5m": 35.0,
            "prev_rsi_5m": 38.0,
            "rvol_5m": 1.8,
            "is_5m_green": False,
            "rth_close": 104.57,
        }

        res = run_evaluate_intraday_entry(
            active_price=active_price,
            pct_change=-1.5,
            rth_high=rth_high,
            is_pit=True,
            intra_info=intra_info,
        )

        self.assertTrue(
            res.get("is_crash_halt"),
            "High-to-low flush <= -6.0% with pct_change <= -1.0% MUST trigger is_crash_halt",
        )
        self.assertIn(
            "断头铡刀防接刀",
            res.get("intraday_reason", ""),
            "Reason must state '断头铡刀防接刀'",
        )
        self.assertEqual(res.get("optimal_limit"), 0.0)
        self.assertFalse(res.get("intraday_entry_ready"))

    # --------------------------------------------------------------------------
    # Scenario 5: ATR Dual-Track Adaptive Threshold
    # --------------------------------------------------------------------------
    def test_scenario_5a_low_volatility_stock_atr(self):
        """Scenario 5A: Low-volatility stock (ATR% = 1.5%):
        crash_thresh == -3.8% (floor).
        Verify at pct_change = -3.7% is_crash_halt == False.
        Verify at pct_change = -3.9% is_crash_halt == True.
        """
        active_price = 100.0
        atr = 1.5  # atr_pct = 1.5% -> 2 * 1.5 = 3.0%, bounded by -3.8%
        thresh = calc_expected_atr_threshold(active_price, atr)
        self.assertAlmostEqual(thresh, -3.8, places=2)

        # Above threshold: not halted
        res_safe = run_evaluate_intraday_entry(
            active_price=active_price,
            atr=atr,
            pct_change=-3.7,
            is_pit=True,
        )
        self.assertFalse(
            res_safe.get("is_crash_halt"),
            "pct_change = -3.7% is above low-volatility threshold -3.8%, should not halt",
        )

        # Below threshold: halted
        res_halt = run_evaluate_intraday_entry(
            active_price=active_price,
            atr=atr,
            pct_change=-3.9,
            is_pit=True,
        )
        self.assertTrue(
            res_halt.get("is_crash_halt"),
            "pct_change = -3.9% is below low-volatility threshold -3.8%, must trigger halt",
        )

    def test_scenario_5b_mid_volatility_stock_atr(self):
        """Scenario 5B: Mid-volatility stock (ATR% = 2.2%):
        crash_thresh == -4.4% (2.0 * ATR%).
        Verify at pct_change = -4.3% is_crash_halt == False.
        Verify at pct_change = -4.5% is_crash_halt == True.
        """
        active_price = 100.0
        atr = 2.2  # atr_pct = 2.2% -> 2.0 * 2.2 = 4.4%
        thresh = calc_expected_atr_threshold(active_price, atr)
        self.assertAlmostEqual(thresh, -4.4, places=2)

        res_safe = run_evaluate_intraday_entry(
            active_price=active_price,
            atr=atr,
            pct_change=-4.3,
            is_pit=True,
        )
        self.assertFalse(res_safe.get("is_crash_halt"))

        res_halt = run_evaluate_intraday_entry(
            active_price=active_price,
            atr=atr,
            pct_change=-4.5,
            is_pit=True,
        )
        self.assertTrue(res_halt.get("is_crash_halt"))

    def test_scenario_5c_high_volatility_stock_atr(self):
        """Scenario 5C: High-volatility stock (ATR% = 3.0%):
        crash_thresh == -5.2% (capped at -5.2%).
        Verify at pct_change = -5.1% is_crash_halt == False.
        Verify at pct_change = -5.3% is_crash_halt == True.
        """
        active_price = 100.0
        atr = 3.0  # atr_pct = 3.0% -> 2.0 * 3.0 = 6.0%, capped at 5.2%
        thresh = calc_expected_atr_threshold(active_price, atr)
        self.assertAlmostEqual(thresh, -5.2, places=2)

        res_safe = run_evaluate_intraday_entry(
            active_price=active_price,
            atr=atr,
            pct_change=-5.1,
            is_pit=True,
        )
        self.assertFalse(res_safe.get("is_crash_halt"))

        res_halt = run_evaluate_intraday_entry(
            active_price=active_price,
            atr=atr,
            pct_change=-5.3,
            is_pit=True,
        )
        self.assertTrue(res_halt.get("is_crash_halt"))

    # --------------------------------------------------------------------------
    # Scenario 6: Deadlock Unlock on Pre/Post-Market Reversal
    # --------------------------------------------------------------------------
    def test_scenario_6_deadlock_unlock_on_pre_post_market_reversal(self):
        """Scenario 6: Deadlock Unlock on Pre/Post-Market Reversal.
        Regular hours pct_change is -6.0% (below crash threshold).
        Post-market surges +8.0% (safe_ext = +8.0%, pct_change + safe_ext = +2.0% > -2.5%).
        Verify is_crash_halt == False.
        """
        pct_change = -6.0
        ext_pct = 8.0  # Net is +2.0% > -2.5%, and safe_ext >= 3.0%
        self.assertGreater(pct_change + ext_pct, -2.5)
        self.assertGreaterEqual(ext_pct, 3.0)

        res = run_evaluate_intraday_entry(
            active_price=102.0,
            pct_change=pct_change,
            ext_pct=ext_pct,
            atr=2.0,
            is_pit=True,
        )

        self.assertFalse(
            res.get("is_crash_halt"),
            "Strong post-market reversal (+8.0%, net +2.0% > -2.5%) MUST unlock base crash deadlock",
        )

    # --------------------------------------------------------------------------
    # Scenario 7: V3.1 9:30 Open Limit & Execution Sizing
    # --------------------------------------------------------------------------
    def test_scenario_7a_optimal_limit_930_open_vs_rth(self):
        """Scenario 7A: V3.1 Optimal Limit Calculation:
        - 9:30 open (session_code != 'REGULAR'): round(rth_close * 1.020, 2)
        - Regular market hours (session_code == 'REGULAR'): round(active_price * 1.005, 2)
        """
        rth_close = 150.0
        active_price = 151.0

        # Case 1: Pre-market / 9:30 open execution
        res_open = run_evaluate_intraday_entry(
            active_price=active_price,
            is_pit=True,
            intra_info={"rth_close": rth_close, "vwap": active_price},
            session_code="PRE",
        )
        expected_open_limit = round(rth_close * 1.020, 2)  # 153.00
        self.assertEqual(
            res_open.get("optimal_limit"),
            expected_open_limit,
            f"9:30 open buy limit must be round(rth_close * 1.020, 2) = {expected_open_limit}",
        )
        self.assertIn(
            "9:30开盘限价上限",
            res_open.get("limit_note", ""),
        )

        # Case 2: Regular market hours
        res_rth = run_evaluate_intraday_entry(
            active_price=active_price,
            is_pit=True,
            intra_info={"rth_close": rth_close, "vwap": active_price},
            session_code="REGULAR",
        )
        expected_rth_limit = round(active_price * 1.005, 2)  # 151.76
        self.assertEqual(
            res_rth.get("optimal_limit"),
            expected_rth_limit,
            f"RTH buy limit must be round(active_price * 1.005, 2) = {expected_rth_limit}",
        )

    def test_scenario_7b_gap_down_cancel_line(self):
        """Scenario 7B: Overnight gap-down cancel line: max(low_price, last * 0.985)."""
        last = 100.0

        # Case 1: low_price is above last * 0.985 (e.g. 99.0 > 98.5)
        cancel_1 = calc_expected_cancel_line(99.0, last)
        self.assertEqual(cancel_1, 99.0)

        # Case 2: low_price is below last * 0.985 (e.g. 97.0 < 98.5)
        cancel_2 = calc_expected_cancel_line(97.0, last)
        self.assertEqual(cancel_2, 98.5)

        # Case 3: low_price is None -> fallback to last * 0.985
        cancel_3 = calc_expected_cancel_line(None, last)
        self.assertEqual(cancel_3, 98.5)

    def test_scenario_7c_structural_stop_loss_formula(self):
        """Scenario 7C: Structural stop loss formula:
        max(min(low_price - 0.5 * atr, last - 1.8 * atr), last * 0.932)
        """
        last = 100.0
        atr = 2.0
        # low_price - 0.5 * atr = 98.0 - 1.0 = 97.0
        # last - 1.8 * atr = 100.0 - 3.6 = 96.4
        # min(97.0, 96.4) = 96.4
        # last * 0.932 = 93.2 -> max(96.4, 93.2) = 96.4
        stop_1 = calc_expected_structural_stop(98.0, last, atr)
        self.assertAlmostEqual(stop_1, 96.4, places=2)

        # Hard stop floor at -6.8% (last * 0.932):
        # low = 90.0 -> low - 0.5*2 = 89.0, min(89.0, 96.4) = 89.0 < 93.2
        stop_floor = calc_expected_structural_stop(90.0, last, atr)
        self.assertAlmostEqual(stop_floor, 93.2, places=2)

    def test_scenario_7d_relative_strength_and_oversold_bounce(self):
        """Scenario 7D: Relative strength filter (rs_60 >= -8.0) and V3.1 oversold bounce signal
        (min_rsi_3d <= 32.0, green candle >= +1.5%, vol_ratio >= 1.15).
        """
        meta = {"cost": 0.0, "shares": 0.0}

        # Case 1: Stock has rs_60 = -10.0 (< -8.0), fails cond_bull_trend
        sig_weak_rs = evaluate_stock_signal(
            ticker="NVDA",
            last=120.0,
            ma50=115.0,
            bias=4.3,
            rsi=35.0,
            vol_ratio=1.3,
            fib_low=110.0,
            fib_high=130.0,
            meta=meta,
            atr=3.0,
            sma200=100.0,
            min_rsi_3d=30.0,
            prev_close=118.0,
            open_price=118.0,
            rs_60=-10.0,  # Below -8.0 threshold!
            low_price=117.0,
        )
        self.assertFalse(
            sig_weak_rs.get("is_pit"),
            "Stock with rs_60 < -8.0 must NOT trigger golden pit or oversold bounce",
        )

        # Case 2: V3.1 Oversold Bounce Signal
        # min_rsi_3d <= 32.0 (30.0), green candle >= +1.5% (120.0 vs 118.0 is +1.69%), vol_ratio >= 1.15 (1.30)
        sig_bounce = evaluate_stock_signal(
            ticker="NVDA",
            last=120.0,
            ma50=115.0,
            bias=4.3,
            rsi=35.0,
            vol_ratio=1.3,
            fib_low=110.0,
            fib_high=130.0,
            meta=meta,
            atr=3.0,
            sma200=100.0,
            min_rsi_3d=30.0,
            prev_close=118.0,
            open_price=118.0,
            rs_60=-5.0,  # >= -8.0
            low_price=117.0,
        )
        self.assertTrue(
            sig_bounce.get("is_pit"),
            "Valid V3.1 oversold bounce (min_rsi_3d<=32, gain>=+1.5%, vol>=1.15) MUST set is_pit=True",
        )
        self.assertIn(
            "🚀 极度超卖右侧反包",
            sig_bounce.get("signal", ""),
            "Signal text must contain '🚀 极度超卖右侧反包'",
        )

    # --------------------------------------------------------------------------
    # Indicator Computation Suite (compute_indicators & compute_rolling_indicators)
    # --------------------------------------------------------------------------
    def test_indicator_computation_crash_3d_and_right_turn(self):
        """Test compute_indicators generates Crash_3d, Prev_Crash_3d, Is_Right_Turn,
        Prev_Is_Right_Turn, Prev_Crash_Body, and Prev_Crash_Close while preserving Crash_5d.
        """
        dates = pd.date_range("2026-01-01", periods=10, freq="D")
        # Construct synthetic dataframe with a crash on day 4
        # Day 0: 100
        # Day 1: 100
        # Day 2: 100
        # Day 3: 100 (close=100)
        # Day 4: Open=99, Close=93 (daily_ret = -7.0% <= -6.0%) -> Crash!
        # Day 5: Open=93, Close=93.2 (flat +0.2%)
        # Day 6: Open=93.2, Close=94.5 (gain +1.4%)
        # Day 7: Open=94.5, Close=96.0 (right turn confirmed)
        # Day 8: Open=96.0, Close=96.5
        # Day 9: Open=96.5, Close=97.0
        df = pd.DataFrame(
            {
                "Open": [100.0, 100.0, 100.0, 100.0, 99.0, 93.0, 93.2, 94.5, 96.0, 96.5],
                "High": [101.0, 101.0, 101.0, 101.0, 99.5, 93.5, 94.8, 96.5, 97.0, 97.5],
                "Low": [99.0, 99.0, 99.0, 99.0, 92.5, 92.5, 93.0, 94.0, 95.5, 96.0],
                "Close": [100.0, 100.0, 100.0, 100.0, 93.0, 93.2, 94.5, 96.0, 96.5, 97.0],
                "Volume": [100000] * 10,
            },
            index=dates,
        )

        res_df = compute_indicators(df)

        # Verify Crash_5d exists for backward compatibility
        self.assertIn("Crash_5d", res_df.columns, "Crash_5d column must be preserved")

        # Verify V3.2 columns exist
        self.assertIn("Crash_3d", res_df.columns, "Crash_3d column must be present in compute_indicators")
        self.assertIn("Prev_Crash_3d", res_df.columns, "Prev_Crash_3d column must be present in compute_indicators")
        self.assertIn("Is_Right_Turn", res_df.columns, "Is_Right_Turn column must be present in compute_indicators")
        self.assertIn("Prev_Is_Right_Turn", res_df.columns, "Prev_Is_Right_Turn column must be present in compute_indicators")

        # Day 4 crashed: Crash_3d must be True on day 4, 5, 6
        self.assertTrue(res_df["Crash_3d"].iloc[4], "Day 4 must have Crash_3d == True")
        self.assertTrue(res_df["Crash_3d"].iloc[5], "Day 5 must have Crash_3d == True (within 3 days)")
        self.assertTrue(res_df["Crash_3d"].iloc[6], "Day 6 must have Crash_3d == True (within 3 days)")

        # Prev_Crash_3d must be True on Day 5, 6, 7 (shifted by 1)
        self.assertFalse(res_df["Prev_Crash_3d"].iloc[4], "Day 4 Prev_Crash_3d must be False (shift 1)")
        self.assertTrue(res_df["Prev_Crash_3d"].iloc[5], "Day 5 Prev_Crash_3d must be True")
        self.assertTrue(res_df["Prev_Crash_3d"].iloc[6], "Day 6 Prev_Crash_3d must be True")
        self.assertTrue(res_df["Prev_Crash_3d"].iloc[7], "Day 7 Prev_Crash_3d must be True")
        # Day 8 (4 days after Day 4 crash): Prev_Crash_Body must expire to 0.0 (limit=3)
        self.assertEqual(res_df["Prev_Crash_Body"].iloc[8], 0.0, "Prev_Crash_Body must expire after 3 days")

    def test_scenario_8_inverse_hedge_etf_isolation(self):
        """Scenario 8: Inverse/Volatility Decay Hedge ETFs (SQQQ, SOXS, VIXY) must never
        be recommended Golden Pit buy limits or halted by bull-market rallies.
        """
        for tk in ("SQQQ", "SOXS", "VIXY"):
            res = evaluate_intraday_entry(
                active_price=41.83,
                fib_low=35.83,
                fib_high=37.19,
                ma50=36.71,
                is_pit=False,
                is_stop=False,
                dist_to_pit=10.0,
                intra_info={"vwap": 41.63, "rth_low": 40.55, "rth_high": 42.96, "rth_close": 41.83},
                pct_change=-8.5,  # Market rally causing inverse ETF to drop -8.5%
                ema5=38.30,
                ticker=tk,
            )
            self.assertTrue(res.get("is_inverse_hedge"), f"{tk} must set is_inverse_hedge=True")
            self.assertFalse(res.get("is_crash_halt"), f"{tk} must NOT trigger is_crash_halt on market rally")
            self.assertFalse(res.get("is_cooling_halt"), f"{tk} must NOT trigger is_cooling_halt")
            self.assertFalse(res.get("intraday_entry_ready"), f"{tk} must NOT trigger 5m golden pit entry")
            self.assertNotIn("埋伏单", res.get("limit_note", ""), f"{tk} limit_note must not recommend golden pit limit order")

    def test_scenario_9_same_day_gap_down_cooling_and_rth_low_contract(self):
        """Scenario 9: Same-day gap-down (crash_3d=True, prev_crash_3d=False) with weak bounce (-2.5%)
        must remain halted in cooling/stabilization check even if prev_is_right_turn=True.
        Also verifies rth_low / low_price are returned in output dicts.
        """
        res = evaluate_intraday_entry(
            active_price=97.5,
            fib_low=95.0,
            fib_high=105.0,
            ma50=98.0,
            is_pit=True,
            is_stop=False,
            dist_to_pit=0.0,
            intra_info={"vwap": 98.0, "rth_low": 94.8, "rth_high": 98.2, "rth_close": 100.0},
            pct_change=-2.5,
            atr=2.0,
            prev_crash_3d=False,
            crash_3d=True,
            prev_is_right_turn=True,  # Yesterday was green before today's gap-down!
            ema5=99.0,
        )
        self.assertTrue(res.get("is_cooling_halt"), "Same-day gap-down with weak bounce must trigger cooling halt")
        self.assertFalse(res.get("intraday_entry_ready"))
        self.assertEqual(res.get("rth_low"), 94.8, "rth_low must be preserved in evaluate_intraday_entry output dict")

    def test_scenario_10_gap_down_blocks_prior_right_turn(self):
        """Scenario 10: V3.2 Cooldown Gap Defense (Defect 5 Fix).
        If yesterday had a green rebound (prev_is_right_turn=True, prev_crash_3d=True),
        but today experiences a new gap-down crash (pct_change <= -3.8% or ext_pct <= -4.5%),
        unlock_a must be blocked and cooling halt must remain active:
        - When pct_change = -4.0% (with ATR% = 2.5%, base crash thresh = -5.0%),
          stock is not a single-day ATR crash halt, but unlock_a MUST evaluate to False.
          is_cooling_halt remains True and optimal_limit remains 0.0.
        - When ext_pct = -4.8% (gap-down <= -4.5%), circuit breaker halts entry,
          optimal_limit must be 0.0 and intraday_entry_ready must be False.
        """
        intra_info = {
            "vwap": 97.0,
            "rsi_5m": 35.0,
            "prev_rsi_5m": 36.0,
            "min_rsi_5m_30m": 30.0,
            "rvol_5m": 1.2,
            "is_5m_green": False,
            "is_5m_higher_low": False,
            "rth_low": 95.5,
            "rth_high": 99.0,
            "rth_close": 96.0,
        }

        # Case A: Regular market crash today pct_change = -4.0% (<= -3.8%)
        # With active_price=96.0, atr=2.4 (atr_pct=2.5%), crash_thresh = -5.0%.
        # -4.0% > -5.0%, so is_crash_halt is False, but 3-day cooling halt MUST be maintained!
        res_a = evaluate_intraday_entry(
            active_price=96.0,
            fib_low=95.0,
            fib_high=105.0,
            ma50=98.0,
            is_pit=True,
            is_stop=False,
            dist_to_pit=0.0,
            intra_info=intra_info,
            pct_change=-4.0,
            atr=2.4,
            prev_crash_3d=True,
            crash_3d=False,
            prev_is_right_turn=True,  # Yesterday was green!
            ema5=97.5,
            prev_crash_body=5.0,
            prev_crash_close=99.0,
        )
        self.assertFalse(res_a.get("is_crash_halt"), "pct_change=-4.0% > crash_thresh=-5.0% does not trigger is_crash_halt")
        self.assertTrue(res_a.get("is_cooling_halt"), "pct_change=-4.0% <= -3.8% MUST block unlock_a and keep is_cooling_halt=True")
        self.assertEqual(res_a.get("optimal_limit"), 0.0, "Optimal limit MUST be 0.0 while in cooling halt")
        self.assertFalse(res_a.get("intraday_entry_ready"), "intraday_entry_ready MUST be False during cooling halt")

        # Case B: Extended hours gap-down ext_pct = -4.8% (<= -4.5%)
        res_b = evaluate_intraday_entry(
            active_price=98.0,
            fib_low=95.0,
            fib_high=105.0,
            ma50=98.0,
            is_pit=True,
            is_stop=False,
            dist_to_pit=0.0,
            intra_info=intra_info,
            pct_change=-1.0,
            ext_pct=-4.8,
            atr=2.0,
            prev_crash_3d=True,
            crash_3d=False,
            prev_is_right_turn=True,
            ema5=98.5,
        )
        self.assertTrue(res_b.get("is_crash_halt"), "ext_pct=-4.8% <= -4.5% triggers is_crash_halt")
        self.assertEqual(res_b.get("optimal_limit"), 0.0, "Optimal limit MUST be 0.0 during crash halt")
        self.assertFalse(res_b.get("intraday_entry_ready"), "intraday_entry_ready MUST be False")


    # --------------------------------------------------------------------------
    # Scenario 11-13: Broken-pit rebound exclusion filters
    # --------------------------------------------------------------------------
    def _broken_pit_intra_info(self):
        return {
            "vwap": 98.0,
            "rsi_5m": 60.0,
            "prev_rsi_5m": 55.0,
            "min_rsi_5m_30m": 28.0,
            "rvol_5m": 1.2,
            "is_5m_green": True,
            "is_5m_higher_low": True,
            "rth_low": 96.0,
            "rth_high": 101.0,
            "rth_close": 99.0,
        }

    def test_scenario_11_broken_pit_low_elasticity_no_rebound(self):
        """低弹性慢股(ATR%<1.8%) 破坑后必须排除短线博反抽。"""
        res = run_evaluate_intraday_entry(
            active_price=100.0, fib_low=95.0, fib_high=110.0, ma50=105.0,
            is_pit=False, is_stop=True, dist_to_pit=-8.0,
            intra_info=self._broken_pit_intra_info(),
            earnings_info={}, atr=1.0, daily_rsi=38.0, pct_change=-1.0,
            rth_high=101.0, ema5=99.0,
        )
        self.assertFalse(res.get("broken_rebound_ready"), "ATR%<1.8 low-elasticity must not trigger rebound branch")
        self.assertTrue(res.get("broken_bearish"))
        self.assertIn("低弹性慢股", res.get("intraday_status_text", ""))
        self.assertEqual(res.get("optimal_limit"), 0.0)

    def test_scenario_12_broken_pit_crash_day_no_rebound(self):
        """单日暴雷急杀(<=-7.5%) 当天即使触碰VWAP也不得触发博反抽。"""
        res = run_evaluate_intraday_entry(
            active_price=100.0, fib_low=95.0, fib_high=110.0, ma50=105.0,
            is_pit=False, is_stop=True, dist_to_pit=-8.0,
            intra_info=self._broken_pit_intra_info(),
            earnings_info={}, atr=4.0, daily_rsi=30.0, pct_change=-8.2,
            rth_high=101.0, ema5=99.0,
        )
        self.assertFalse(res.get("broken_rebound_ready"), "Crash-day <= -7.5% must not trigger rebound branch")
        self.assertFalse(res.get("intraday_entry_ready"))
        self.assertNotIn("超跌右侧博反抽", res.get("intraday_status_text", ""))

    def test_scenario_13_broken_pit_high_elasticity_rebound_allowed(self):
        """未点名个股走薄盘口档，止损落在 -2.2%~-3.2%，且在 VWAP 下方。"""
        res = run_evaluate_intraday_entry(
            active_price=100.0, fib_low=95.0, fib_high=110.0, ma50=105.0,
            is_pit=False, is_stop=True, dist_to_pit=-8.0,
            intra_info=self._broken_pit_intra_info(),
            earnings_info={}, atr=4.0, daily_rsi=38.0, pct_change=-2.0,
            rth_high=101.0, ema5=99.0, ticker="LITE",
        )
        self.assertTrue(res.get("broken_rebound_ready"))
        self.assertIn("超跌右侧博反抽", res.get("intraday_status_text", ""))
        self.assertLessEqual(res.get("stop_loss_pct"), -2.2)
        self.assertGreaterEqual(res.get("stop_loss_pct"), -3.2)
        self.assertLess(res.get("entry_stop_loss"), res.get("vwap"))

    def test_scenario_14_bounce_stop_bands_and_vwap_reanchor(self):
        """五档带宽，以及现价离失效位超过上限时不上移止损、改等回踩 VWAP。"""
        base = dict(
            fib_low=95.0, fib_high=110.0, ma50=105.0, is_pit=False, is_stop=True,
            dist_to_pit=-8.0, earnings_info={}, atr=4.0, daily_rsi=38.0, pct_change=-2.0,
            rth_high=101.0, ema5=99.0,
        )
        near = dict(self._broken_pit_intra_info())
        near["vwap"] = 99.2
        bands = {
            "QQQ": (-1.3, -0.8),
            "TLT": (-1.3, -0.8),
            "SOXX": (-2.0, -1.2),
            "SOXL": (-2.6, -1.6),
            "NVDA": (-2.4, -1.6),
            "LITE": (-3.2, -2.2),
        }
        for tk, (floor, ceil) in bands.items():
            res = run_evaluate_intraday_entry(active_price=100.0, ticker=tk, intra_info=near, **base)
            self.assertIn("超跌右侧博反抽", res.get("intraday_status_text", ""), tk)
            self.assertGreaterEqual(res.get("stop_loss_pct"), floor, tk)
            self.assertLessEqual(res.get("stop_loss_pct"), ceil, tk)
            self.assertLess(res.get("entry_stop_loss"), near["vwap"], tk)

        far_info = dict(self._broken_pit_intra_info())
        far_info["vwap"] = 96.0
        far_info["low_5m"] = 95.5
        far = run_evaluate_intraday_entry(
            active_price=100.0, ticker="SOXL", intra_info=far_info, **{k: v for k, v in base.items() if k != "intra_info"}
        )
        self.assertIn("偏离失效位过远", far.get("intraday_status_text", ""))
        self.assertFalse(far.get("intraday_entry_ready"))
        self.assertLessEqual(far.get("optimal_limit"), 96.0)
        self.assertLess(far.get("entry_stop_loss"), far.get("optimal_limit"))
        self.assertLess(far.get("stop_loss_pct"), 0)
        rel = round((far["entry_stop_loss"] - far["optimal_limit"]) / far["optimal_limit"] * 100.0, 1)
        self.assertEqual(far.get("stop_loss_pct"), rel)

        upside_down = dict(self._broken_pit_intra_info())
        upside_down["vwap"] = 94.0
        upside_down["low_5m"] = 97.0
        hung = run_evaluate_intraday_entry(
            active_price=100.0, ticker="GLD", intra_info=upside_down, **{k: v for k, v in base.items() if k != "intra_info"}
        )
        self.assertIn("偏离失效位过远", hung.get("intraday_status_text", ""))
        self.assertLess(hung.get("entry_stop_loss"), hung.get("optimal_limit"))
        self.assertGreaterEqual(hung.get("stop_loss_pct"), -1.3)
        self.assertLessEqual(hung.get("stop_loss_pct"), -0.8)

    def test_scenario_15_card_stop_uses_band_not_68_floor(self):
        """坑内买点卡片不再钉死 -6.8%，行业 ETF 落在 -1.2%~-2.0%。"""
        info = self._broken_pit_intra_info()
        info["vwap"] = 99.2
        res = run_evaluate_intraday_entry(
            active_price=100.0, fib_low=90.0, fib_high=110.0, ma50=105.0,
            is_pit=True, is_stop=False, dist_to_pit=0.0,
            intra_info=info, atr=8.0, ticker="SOXX", session_code="REGULAR",
        )
        self.assertGreater(res.get("optimal_limit"), 0)
        self.assertGreaterEqual(res.get("stop_loss_pct"), -2.0)
        self.assertLessEqual(res.get("stop_loss_pct"), -1.2)
        self.assertNotAlmostEqual(res.get("stop_loss_pct"), -6.8, places=1)

    def test_scenario_16_options_dollar_pcr_excludes_deep_itm_and_no_conflict_resonance(self):
        """验证收紧 Put<=1.025*spot & Call>=0.975*spot 剔除深实值污染，且期权偏空绝不触发多头共振"""
        spot = 352.48
        calls = pd.DataFrame([
            {"strike": 330.0, "volume": 1000.0, "eff_price": 23.0, "dollar_val": 2300000.0},  # deep ITM Call (<0.975*spot=343.67)
            {"strike": 355.0, "volume": 1500.0, "eff_price": 2.50, "dollar_val": 375000.0},
            {"strike": 360.0, "volume": 7940.0, "eff_price": 1.25, "dollar_val": 992500.0},
        ])
        puts = pd.DataFrame([
            {"strike": 380.0, "volume": 3257.0, "eff_price": 27.21, "dollar_val": 8862297.0},  # deep ITM Put (>1.025*spot=361.29)
            {"strike": 355.0, "volume": 1291.0, "eff_price": 4.92, "dollar_val": 635172.0},
            {"strike": 343.0, "volume": 3089.0, "eff_price": 0.85, "dollar_val": 262565.0},
        ])
        dpcr, c_dlr, p_dlr = imr.compute_directional_dollar_pcr(calls, puts, spot)
        self.assertLess(c_dlr, 2000000.0, "Deep ITM Call <0.975*spot must be excluded")
        self.assertLess(p_dlr, 1500000.0, "Deep ITM Put >1.025*spot must be excluded")
        self.assertAlmostEqual(dpcr, round((635172.0 + 262565.0) / (375000.0 + 992500.0), 2), places=2)
        self.assertLess(dpcr, 0.85)

        info = self._broken_pit_intra_info()
        info["vwap"] = 99.2
        base_kw = dict(
            active_price=100.0, fib_low=110.0, fib_high=120.0, ma50=105.0,
            is_pit=False, is_stop=False, dist_to_pit=0.0,
            intra_info=info, atr=8.0, ticker="GLD", session_code="REGULAR",
        )
        # Bearish sentiment + low vol PCR -> must emit ⚖️期权多空分歧 and NEVER ⚡期权多头共振
        res_div = run_evaluate_intraday_entry(
            options_info={
                "has_options": True, "pcr": 0.68, "dollar_pcr": 4.08,
                "pcr_sentiment": "🔴 资金偏空对冲", "top_call_info": "$360 Call", "top_put_info": "$355 Put",
            },
            **base_kw,
        )
        self.assertTrue(res_div["intraday_entry_ready"])
        self.assertNotIn("期权多头共振", res_div["intraday_reason"])
        self.assertIn("⚖️期权多空分歧", res_div["intraday_reason"])

        # Balanced sentiment + high dollar_pcr conflict -> also ⚖️期权多空分歧
        res_bal_conf = run_evaluate_intraday_entry(
            options_info={
                "has_options": True, "pcr": 0.70, "dollar_pcr": 1.40,
                "pcr_sentiment": "⚪ 资金多空均衡", "top_call_info": "$360 Call", "top_put_info": "$355 Put",
            },
            **base_kw,
        )
        self.assertNotIn("期权多头共振", res_bal_conf["intraday_reason"])
        self.assertIn("⚖️期权多空分歧", res_bal_conf["intraday_reason"])

        # Aligned bullish sentiment -> ⚡期权多头共振
        res_bull = run_evaluate_intraday_entry(
            options_info={
                "has_options": True, "pcr": 0.68, "dollar_pcr": 0.74,
                "pcr_sentiment": "🟢 资金偏多看涨", "top_call_info": "$360 Call", "top_put_info": "$355 Put",
            },
            **base_kw,
        )
        self.assertIn("⚡期权多头共振", res_bull["intraday_reason"])

    def test_xauusd_spot_waterfall_and_basis_scaling(self):
        """Verify XAUUSD TradingView -> Swissquote waterfall and GC=F spot basis normalization."""
        import io as _io
        import json as _json
        import unittest.mock as _mock
        import pandas as _pd
        import ml_predictor_5y
        import integrated_market_radar as imr

        class _FakeResp:
            def __init__(self, payload):
                self._b = _json.dumps(payload).encode("utf-8")
            def read(self):
                return self._b
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        # 1. TradingView fails, Swissquote succeeds cleanly
        def _fake_urlopen(req, timeout=6):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "scanner.tradingview.com" in url:
                raise TimeoutError("tv timeout")
            if "swissquote.com" in url:
                return _FakeResp([{"spreadProfilePrices": [{"bid": 3727.10, "ask": 3727.90}]}])
            raise AssertionError(f"Unexpected fallback call to {url}")

        with _mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            q = ml_predictor_5y.fetch_xauusd_spot_quote()
        self.assertEqual(q.get("source"), "swissquote")
        self.assertAlmostEqual(q.get("last"), 3727.50, places=2)

        # 2. _apply_xauusd_spot_basis aligns GC=F 1d/5m prices to spot dollars and computes pct vs prev close
        hist_df = _pd.DataFrame({
            "Open": [3720.0, 3728.0],
            "High": [3735.0, 3762.0],
            "Low": [3715.0, 3697.0],
            "Close": [3728.0, 3732.0],
            "Volume": [1000, 1200],
        })
        h_dict = {"XAUUSD": hist_df}
        intra_m = {"XAUUSD": {"rth_close": 3732.0, "vwap": 3735.0, "ext_price": 3732.0}}
        rt_q = {"XAUUSD": dict(q)}
        imr._apply_xauusd_spot_basis(h_dict, intra_m, rt_q)
        self.assertAlmostEqual(float(h_dict["XAUUSD"]["Close"].iloc[-1]), 3727.50, delta=0.05)
        self.assertAlmostEqual(intra_m["XAUUSD"]["rth_close"], 3727.50, places=2)
        self.assertEqual(intra_m["XAUUSD"]["ext_price"], 0.0)
        self.assertAlmostEqual(rt_q["XAUUSD"]["pct_change"], round((3727.50 - 3723.50) / 3723.50 * 100.0, 2), delta=0.05)

        # Keep GLD untouched in MONITORED_TARGETS
        self.assertIn("GLD", imr.MONITORED_TARGETS["ETFS"])
        self.assertIn("XAUUSD", imr.MONITORED_TARGETS["STOCKS"])
        self.assertIn("ANT", imr.MONITORED_TARGETS["STOCKS"])

    def test_ant_ipo_day0_time_cycle_placeholder(self):
        """Verify ANT Day-0 IPO (<5 bars) emits valid timeCycleData placeholder anchored to $83 or live quote."""
        import pandas as _pd
        from datetime import datetime as _dt
        import ml_predictor_5y
        base_panel = _pd.DataFrame({
            "Date": [_pd.Timestamp("2026-11-18")],
            "Ticker": ["QQQ"],
            "Close": [500.0],
        })
        out_default = ml_predictor_5y.generate_time_cycle_projections(
            base_panel, target_tickers=["ANT"], forecast_days=15, as_of=_dt(2026, 11, 19, 8, 30)
        )
        self.assertIn("ANT", out_default)
        self.assertEqual(out_default["ANT"]["current_price"], 83.0)
        self.assertEqual(len(out_default["ANT"]["projection"]), 15)
        out_live = ml_predictor_5y.generate_time_cycle_projections(
            base_panel, target_tickers=["ANT"], forecast_days=15,
            scenario_map_by_ticker={"ANT": {"latest_price": 112.50}},
            as_of=_dt(2026, 11, 19, 10, 30),
        )
        self.assertEqual(out_live["ANT"]["current_price"], 112.50)

    def test_xauusd_tier1_stop_band_and_scenario_alignment(self):
        """XAUUSD uses Tier-1 (-0.8% ~ -1.3%) stop band matching GLD/TLT across intraday card & scenario map."""
        import ml_predictor_5y
        import integrated_market_radar as imr

        self.assertEqual(imr._bounce_rebound_stop_band("XAUUSD"), (0.8, 1.3, 0.15))
        self.assertEqual(imr._bounce_rebound_stop_band("XAUUSD"), imr._bounce_rebound_stop_band("GLD"))

        res = imr.evaluate_intraday_entry(
            active_price=3730.0,
            fib_low=3850.0,
            fib_high=3950.0,
            ma50=3900.0,
            is_pit=False,
            is_stop=True,
            dist_to_pit=-3.1,
            intra_info={"vwap": 3710.0, "low_5m": 3705.0, "rsi_5m": 45.0, "prev_rsi_5m": 40.0, "min_rsi_5m_30m": 29.0, "rvol_5m": 1.2, "is_5m_green": True, "is_5m_higher_low": True},
            atr=80.0,
            ticker="XAUUSD",
            session_code="REGULAR",
            ai_s1=3680.0,
            ai_r1=3830.0,
            ai_cal_win_rate=52.0,
        )
        self.assertTrue(-1.3 <= res["stop_loss_pct"] <= -0.8, f"Expected Tier-1 stop band, got {res['stop_loss_pct']}%")

        sc = ml_predictor_5y.compute_multi_level_scenario_map("XAUUSD", close_price=3721.17, atr_pct=2.4)
        self.assertLess(sc["sl"], sc["s2"])
        self.assertLess(sc["s2"], sc["s1"])
        self.assertLess(sc["s1"], 3721.17)
        s1_pct = (3721.17 - sc["s1"]) / 3721.17 * 100.0
        self.assertTrue(0.8 <= s1_pct <= 1.35, f"Expected S1 in Tier-1 0.8~1.3%, got {s1_pct:.2f}%")
        self.assertTrue(sc["near_stop_s1"])
        self.assertIn(f"${sc['s1']:.2f}", sc["scenario_script"])

    def test_xauusd_downgrade_change_backfill_from_gcf(self):
        """When TradingView fails and Swissquote/Gold-API has no pct, compute real change from calibrated GC=F prev close."""
        import json as _json
        import unittest.mock as _mock
        import pandas as _pd
        import ml_predictor_5y

        class _FakeResp:
            def __init__(self, payload):
                self._b = _json.dumps(payload).encode("utf-8")
            def read(self):
                return self._b
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        def _fake_urlopen(req, timeout=6):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "scanner.tradingview.com" in url:
                raise TimeoutError("tv down")
            if "swissquote.com" in url:
                raise TimeoutError("sq down")
            if "gold-api.com" in url:
                return _FakeResp({"price": 3727.50})
            raise AssertionError(url)

        d1_df = _pd.DataFrame(
            {"Close": [3742.00, 3732.00]},
            index=_pd.DatetimeIndex([_pd.Timestamp("2026-11-18"), _pd.Timestamp("2026-11-19")]),
        )
        h5_df = _pd.DataFrame(
            {"Close": [3732.00]},
            index=_pd.DatetimeIndex([_pd.Timestamp("2026-11-19 06:50:00")]),
        )

        class _FakeTicker:
            def history(self, period="5d", interval="1d", **kw):
                return h5_df if interval == "5m" else d1_df

        with _mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen), \
             _mock.patch("ml_predictor_5y.yf.Ticker", return_value=_FakeTicker()), \
             _mock.patch("ml_predictor_5y._to_et", return_value=_pd.Timestamp("2026-11-19 06:55:00")):
            q = ml_predictor_5y.fetch_xauusd_spot_quote()

        self.assertEqual(q.get("source"), "gold-api")
        self.assertNotEqual(q.get("pct_change"), 0.0)
        cal_prev = 3742.00 * (3727.50 / 3732.00)
        self.assertAlmostEqual(q.get("pct_change"), round((3727.50 - cal_prev) / cal_prev * 100.0, 2), places=2)

    def test_ai_weak_winrate_under_vwap_blocks_left_side_rebound(self):
        """AI calibrated win rate < 50% and price < VWAP must wait for VWAP reclaim rather than triggering 超跌右侧博反抽."""
        common_kw = dict(
            fib_low=4072.32,
            fib_high=4200.0,
            ma50=4050.0,
            is_pit=False,
            is_stop=True,
            dist_to_pit=-8.6,
            intra_info={"vwap": 3744.58, "low_5m": 3715.0, "rsi_5m": 38.0, "prev_rsi_5m": 32.0, "min_rsi_5m_30m": 28.0, "rvol_5m": 1.1, "is_5m_green": True, "is_5m_higher_low": True},
            atr=80.0,
            ticker="XAUUSD",
            session_code="PRE_MARKET",
            ai_s1=3676.0,
            ai_r1=3832.0,
            ai_cal_win_rate=46.7,
        )
        blocked = imr.evaluate_intraday_entry(active_price=3721.17, **common_kw)
        self.assertFalse(blocked["broken_rebound_ready"])
        self.assertFalse(blocked["intraday_entry_ready"])
        self.assertIn("未站稳 VWAP", blocked["intraday_status_text"])
        self.assertNotIn("超跌右侧博反抽", blocked["intraday_status_text"])

        reclaimed = imr.evaluate_intraday_entry(active_price=3746.00, **common_kw)
        self.assertTrue(reclaimed["broken_rebound_ready"])
        self.assertIn("超跌右侧博反抽", reclaimed["intraday_status_text"])

    def test_truncated_yfinance_daily_backfills_from_local_parquet(self):
        """When Yahoo 1y daily returns <20 bars for an existing stock like SNDK, backfill from local parquet."""
        import unittest.mock as _mock
        import pandas as _pd
        import integrated_market_radar as imr

        short_yf = _pd.DataFrame(
            {
                "Open": [809.83, 805.87],
                "High": [821.46, 813.18],
                "Low": [749.39, 753.83],
                "Close": [797.24, 776.22],
                "Volume": [15739553, 12602833],
            },
            index=_pd.DatetimeIndex([_pd.Timestamp("2026-11-17"), _pd.Timestamp("2026-11-18")]),
        )
        with _mock.patch("integrated_market_radar.yf.download", return_value=short_yf):
            out = imr.fetch_all_market_data(["SNDK"], period="1y", retries=1)
        self.assertIn("SNDK", out)
        self.assertGreaterEqual(len(out["SNDK"]), 200, f"Expected SNDK backfilled >=200 bars from parquet, got {len(out['SNDK'])}")
        self.assertIn(_pd.Timestamp("2026-11-18"), out["SNDK"].index)
        self.assertAlmostEqual(float(out["SNDK"].loc[_pd.Timestamp("2026-11-18"), "Close"]), 776.22, places=2)
        self.assertGreater(float(out["SNDK"]["Close"].iloc[-1]), 0.0)

    def test_all_stocks_emit_5m_entry_without_starred_watchlist(self):
        """Even when target_watchlist is empty, any stock/ETF with intraday_entry_ready=True emits 5m_entry notification."""
        import unittest.mock as _mock
        import integrated_market_radar as imr

        sample_stocks = [
            {
                "ticker": "XAUUSD",
                "name": "伦敦金现货",
                "cat": "大宗商品",
                "last": 3730.0,
                "active_price": 3730.0,
                "pct_change": 0.25,
                "intraday_entry_ready": True,
                "is_stop": False,
                "is_pit": True,
                "is_take_profit": False,
                "optimal_limit": 3728.0,
                "entry_stop_loss": 3690.0,
            }
        ]
        with _mock.patch("integrated_market_radar.load_target_watchlist", return_value=[]), \
             _mock.patch("integrated_market_radar.load_terminal_notifications", return_value=[]), \
             _mock.patch("integrated_market_radar.save_terminal_notifications"):
            notifs = imr.generate_and_sync_notifications(
                macro_data={"SPY": {"name": "标普500", "cat": "宽基", "last": 580.0, "pct_change": 0.1}},
                stocks_data=sample_stocks,
                weather_info={"weather": "阴天防守", "desc": "test", "tactics": "test"},
                now_str="2026-11-19 09:35:00",
            )
        five_m = [n for n in notifs if n.get("category") == "5m_entry" and n.get("ticker") == "XAUUSD"]
        self.assertEqual(len(five_m), 1, f"Expected XAUUSD 5m_entry notification without star, got {notifs}")




# ==============================================================================
# Main Runner with Summary Reporting
# ==============================================================================
if __name__ == "__main__":
    print("\n" + "=" * 80)
    print(" 🚀 RUNNING V3.2 CIRCUIT BREAKER & V3.1 EXECUTION TEST SUITE")
    print("=" * 80)
    suite = unittest.TestLoader().loadTestsFromTestCase(TestV32CircuitBreaker)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 80)
    print(f" Tests Run:   {result.testsRun}")
    print(f" Passed:      {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f" Failures:    {len(result.failures)}")
    print(f" Errors:      {len(result.errors)}")
    print("=" * 80 + "\n")

    sys.exit(0 if result.wasSuccessful() else 1)
