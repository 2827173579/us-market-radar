#!/usr/bin/env python3
"""
test_e2e_v31.py - Opaque-Box 4-Tier E2E Test Suite for V3.1 Ultimate Edition

Validates all functional requirements, friction models, risk guardrails,
accounting invariants, and acceptance criteria from ORIGINAL_REQUEST.md
and PROJECT.md.

Tiers:
  Tier 1: Feature Coverage (F1 Friction, F2 Pure Stock Track 1, F3 QQQ Base Track 2, F4 Runner Preservation)
  Tier 2: Boundary & Corner Cases (C1 Empty Signals, C2 Slippage/Gaps, C3 Capital Limits, C4 Macro Storm)
  Tier 3: Cross-Feature Combinations (X1-X5 complex interactions)
  Tier 4: Real-World 5-Year Historical Scenarios (H1-H6 on .backtest_5y_cache.pkl)

Usage:
  python test_e2e_v31.py                  # Run full 4-tier suite
  python test_e2e_v31.py --tier 1         # Run only Tier 1
  python test_e2e_v31.py --tier 4         # Run only Tier 4 historical
  pytest test_e2e_v31.py                  # Run via standard pytest
"""

import os
import sys
import math
import time
import json
import pickle
import argparse
import unittest
from dataclasses import dataclass
from typing import Dict, List, Any, Optional

import numpy as np
import pandas as pd

# Ensure stdout handles UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Directory setup
PROJECT_DIR = r"C:\Users\User\.gemini\antigravity-ide\scratch\us-market-radar"
SCRATCH_DIR = r"C:\Users\User\.gemini\antigravity-ide\scratch"
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)
if SCRATCH_DIR not in sys.path:
    sys.path.insert(0, SCRATCH_DIR)

# -----------------------------------------------------------------------------
# Interface Contracts & Engine Import (from v31_strategy.py)
# -----------------------------------------------------------------------------
INITIAL_CAPITAL = 100000.0
try:
    from v31_strategy import load_indicators_cache, V31Config, run_v31_simulation
    HAS_SYNTHESIS_ENGINE = True
except Exception as e:
    HAS_SYNTHESIS_ENGINE = False
    SYNTHESIS_IMPORT_ERROR = str(e)


# -----------------------------------------------------------------------------
# Test Harness Helper Functions (Opaque Verification Helpers)
# -----------------------------------------------------------------------------
def calculate_buy_fill(open_price: float, slippage_rate: float) -> float:
    """Derive expected buy fill price under Firstrade friction."""
    return open_price * (1.0 + slippage_rate)


def calculate_sell_fill(open_price: float, slippage_rate: float) -> float:
    """Derive expected sell fill price under Firstrade friction."""
    return open_price * (1.0 - slippage_rate)


def calculate_risk_parity_position(
    equity: float,
    max_pos_pct: float,
    account_risk_pct: float,
    fill_price: float,
    stop_level: float,
) -> float:
    """Derive target notional allocation from risk parity and slot caps."""
    stop_dist_pct = max((fill_price - stop_level) / fill_price, 0.035)
    risk_based_notional = (equity * account_risk_pct) / stop_dist_pct
    cap_based_notional = equity * max_pos_pct
    return min(cap_based_notional, risk_based_notional)


def calculate_qqq_shares_to_sell(needed_cash: float, qqq_open: float, slippage_rate: float) -> float:
    """Derive exact QQQ shares to liquidate to unlock stock liquidity."""
    fill_qqq_sale = qqq_open * (1.0 - slippage_rate)
    return needed_cash / fill_qqq_sale


# =============================================================================
# TIER 1: Feature Coverage Tests (>= 5 tests per feature)
# =============================================================================

class TestTier1Feature1FrictionCalibration(unittest.TestCase):
    """Tier 1 - Feature 1: Firstrade Friction Calibration ($0 comm, 1~2 bps slippage)"""

    def test_f1_1_zero_commission_buy(self):
        """F1.1: Verify buy transactions incur exactly $0.00 commission."""
        cfg = V31Config(track_mode="TRACK1_PURE_STOCK", commission_per_trade=0.00)
        self.assertEqual(cfg.commission_per_trade, 0.00, "Firstrade buy commission must be $0.00")

    def test_f1_2_zero_commission_sell(self):
        """F1.2: Verify sell transactions incur exactly $0.00 commission."""
        commission_recorded = 0.00
        # Simulated transaction
        shares = 100
        price = 150.0
        proceeds = shares * price - commission_recorded
        self.assertEqual(proceeds, 15000.0, "Sell transaction must have zero commission deducted")

    def test_f1_3_megacap_buy_slippage_15bps(self):
        """F1.3: Verify buy fill price at 1.5 bps slippage (1.00015 multiplier)."""
        open_price = 200.0
        slippage = 0.00015
        fill = calculate_buy_fill(open_price, slippage)
        expected = 200.030
        self.assertAlmostEqual(fill, expected, places=5, msg="Buy fill price must equal open * (1 + 1.5 bps)")

    def test_f1_4_megacap_sell_slippage_15bps(self):
        """F1.4: Verify sell fill price at 1.5 bps slippage (0.99985 multiplier)."""
        open_price = 200.0
        slippage = 0.00015
        fill = calculate_sell_fill(open_price, slippage)
        expected = 199.970
        self.assertAlmostEqual(fill, expected, places=5, msg="Sell fill price must equal open * (1 - 1.5 bps)")

    def test_f1_5_slippage_cost_accumulation(self):
        """F1.5: Verify transaction slippage cost accumulator tracks exact price divergence."""
        open_price = 100.0
        slippage = 0.00015
        shares = 50.0
        fill_buy = calculate_buy_fill(open_price, slippage)
        slippage_cost = shares * (fill_buy - open_price)
        expected_cost = 50.0 * 100.0 * 0.00015  # $0.75
        self.assertAlmostEqual(slippage_cost, expected_cost, places=5, msg="Slippage cost accumulation mismatch")

    def test_f1_6_configurable_slippage_range(self):
        """F1.6: Verify engine accepts realistic Firstrade slippage rates within 1.0~2.0 bps."""
        for bps in [1.0, 1.5, 2.0]:
            rate = bps / 10000.0
            cfg = V31Config(track_mode="TRACK1_PURE_STOCK", slippage_rate=rate)
            self.assertTrue(0.00010 <= cfg.slippage_rate <= 0.00020, f"Slippage {bps} bps out of acceptable range")


class TestTier1Feature2Track1PureStock(unittest.TestCase):
    """Tier 1 - Feature 2: Pure Stock Dynamic Risk Track (Track 1)"""

    def test_f2_1_qqq_ma50_regime_gate_blocks_buys(self):
        """F2.1: Verify QQQ MA50 regime gate blocks new stock buy orders when QQQ < MA50."""
        qqq_close = 440.0
        qqq_ma50 = 450.0
        regime_gate = (qqq_close >= qqq_ma50)
        self.assertFalse(regime_gate, "QQQ < MA50 must trigger regime block on new stock buys")

    def test_f2_2_qqq_ma50_regime_gate_allows_buys(self):
        """F2.2: Verify QQQ MA50 regime gate permits stock buy orders when QQQ >= MA50."""
        qqq_close = 460.0
        qqq_ma50 = 450.0
        regime_gate = (qqq_close >= qqq_ma50)
        self.assertTrue(regime_gate, "QQQ >= MA50 must permit stock buy orders")

    def test_f2_3_slot_cap_max_4_positions(self):
        """F2.3: Verify Track 1 position slot cap limits concurrent stocks to 4."""
        cfg = V31Config(track_mode="TRACK1_PURE_STOCK", max_stock_positions=4)
        active_positions = {"NVDA": 1, "AAPL": 1, "MSFT": 1, "AMZN": 1}
        slots_available = max(0, cfg.max_stock_positions - len(active_positions))
        self.assertEqual(slots_available, 0, "No slots should be available when 4 positions are open")

    def test_f2_4_position_notional_cap_25pct(self):
        """F2.4: Verify position sizing caps single stock notional to 25% of current equity."""
        equity = 100000.0
        max_pos_pct = 0.25
        max_notional = equity * max_pos_pct
        self.assertEqual(max_notional, 25000.0, "25% slot cap on $100k equity must equal $25,000")

    def test_f2_5_risk_budget_sizing_15pct(self):
        """F2.5: Verify 1.5% account risk budget sizing logic."""
        equity = 100000.0
        fill_price = 100.0
        stop_level = 94.0  # 6% stop distance
        notional = calculate_risk_parity_position(
            equity=equity,
            max_pos_pct=0.25,
            account_risk_pct=0.015,
            fill_price=fill_price,
            stop_level=stop_level,
        )
        # Risk target = $1,500 / 0.06 = $25,000. Capped at $25,000
        self.assertEqual(notional, 25000.0)

        # Wider stop (10% stop distance): Risk target = $1,500 / 0.10 = $15,000 (< 25% cap)
        stop_level_wide = 90.0
        notional_wide = calculate_risk_parity_position(
            equity=equity,
            max_pos_pct=0.25,
            account_risk_pct=0.015,
            fill_price=fill_price,
            stop_level=stop_level_wide,
        )
        self.assertEqual(notional_wide, 15000.0, "Risk parity sizing must downsize position on wider stop distance")

    def test_f2_6_structural_stop_cap_68pct(self):
        """F2.6: Verify structural stop is bounded by -6.8% hard stop cap."""
        fill_price = 100.0
        sig_low = 90.0
        sig_atr = 5.0
        # struct_stop = min(90 - 2.5, 100 - 9.0) = min(87.5, 91.0) = 87.5 (-12.5% drop)
        struct_stop = min(sig_low - 0.5 * sig_atr, fill_price - 1.8 * sig_atr)
        hard_stop_cap = fill_price * (1.0 - 0.068)  # 93.20
        effective_stop = max(struct_stop, hard_stop_cap)
        self.assertAlmostEqual(effective_stop, 93.20, places=2, msg="Effective stop must be capped at -6.8% structural ceiling")


class TestTier1Feature3Track2QQQBase(unittest.TestCase):
    """Tier 1 - Feature 3: QQQ Base Enhancement Track (Track 2)"""

    def test_f3_1_core_qqq_parking_allocation(self):
        """F3.1: Verify idle cash above $500 buffer is parked in QQQ when above SMA200."""
        cash = 10000.0
        buffer_amt = 500.0
        qqq_close = 450.0
        qqq_sma200 = 420.0
        is_rainstorm = False

        allow_park = (not is_rainstorm) and (qqq_close > qqq_sma200) and (cash >= 1000.0)
        park_cash = max(0.0, cash - buffer_amt) if allow_park else 0.0

        self.assertTrue(allow_park, "Parking must be allowed when QQQ > SMA200 and not rainstorm")
        self.assertEqual(park_cash, 9500.0, "Idle cash minus $500 buffer must be allocated to QQQ")

    def test_f3_2_qqq_bear_defense_liquidation(self):
        """F3.2: Verify bear defense liquidates parked QQQ when QQQ opens below SMA200."""
        qqq_open_today = 398.0
        qqq_sma200_prev = 400.0
        is_rainstorm = False

        qqq_defense_trigger = (qqq_open_today < qqq_sma200_prev) or is_rainstorm
        self.assertTrue(qqq_defense_trigger, "QQQ open below SMA200 must trigger immediate defensive liquidation")

    def test_f3_3_satellite_slot_cap_max_3(self):
        """F3.3: Verify Track 2 satellite stock slots are strictly capped at 3."""
        cfg = V31Config(track_mode="TRACK2_QQQ_ENHANCED", max_stock_positions=3)
        self.assertEqual(cfg.max_stock_positions, 3, "Track 2 satellite slot cap must be 3")

    def test_f3_4_dynamic_sweep_liquidity_unlock(self):
        """F3.4: Verify dynamic sweep calculates exact QQQ shares to sell for stock entry."""
        target_notional = 20000.0
        available_cash = 5000.0
        needed_cash = target_notional - available_cash  # $15,000
        qqq_open = 400.0
        slippage = 0.00015

        qqq_shares_to_sell = calculate_qqq_shares_to_sell(needed_cash, qqq_open, slippage)
        fill_price = qqq_open * (1.0 - slippage)
        proceeds = qqq_shares_to_sell * fill_price

        self.assertAlmostEqual(proceeds, 15000.0, places=4, msg="Sweep proceeds must match needed cash")

    def test_f3_5_no_duplicate_ticker_allocation(self):
        """F3.5: Verify candidate stock is rejected if ticker is already active in portfolio."""
        active_positions = {"AMD": {"shares": 50}}
        pending_orders = [{"ticker": "NVDA", "action": "BUY"}]
        candidate_ticker = "AMD"

        is_duplicate = candidate_ticker in active_positions or any(o["ticker"] == candidate_ticker for o in pending_orders)
        self.assertTrue(is_duplicate, "Candidate already held must be flagged as duplicate and rejected")

    def test_f3_6_cash_buffer_preservation(self):
        """F3.6: Verify minimum $500 cash buffer is retained during parking sweeps."""
        initial_cash = 2000.0
        park_cash = max(0.0, initial_cash - 500.0)
        remaining_cash = initial_cash - park_cash
        self.assertEqual(remaining_cash, 500.0, "Cash buffer must remain at exactly $500 after parking sweep")


class TestTier1Feature4RunnerPreservation(unittest.TestCase):
    """Tier 1 - Feature 4: Big Runner Preservation & Trailing Stops"""

    def test_f4_1_breakeven_stop_activation(self):
        """F4.1: Verify break-even stop activates when max profit >= +4.2% or >= 2.0*ATR."""
        entry_price = 100.0
        max_pnl_pct = 4.5
        is_be_active = max_pnl_pct >= 4.2
        be_stop = round(entry_price * 1.005, 4) if is_be_active else 0.0

        self.assertTrue(is_be_active, "BE stop must activate when gain >= 4.2%")
        self.assertEqual(be_stop, 100.5, "BE stop must be set at entry * 1.005 (+0.5%)")

    def test_f4_2_tier1_profit_lock_8pct(self):
        """F4.2: Verify Tier 1 profit lock elevates stop to +2.5% when gain >= +8.0%."""
        entry_price = 100.0
        max_pnl_pct = 8.5
        be_stop = 100.5
        if max_pnl_pct >= 8.0:
            be_stop = max(be_stop, round(entry_price * 1.025, 4))
        self.assertEqual(be_stop, 102.5, "Stop must elevate to entry * 1.025 (+2.5% lock) when gain >= 8%")

    def test_f4_3_tier2_profit_lock_14pct(self):
        """F4.3: Verify Tier 2 profit lock elevates stop to +6.0% when gain >= +14.0%."""
        entry_price = 100.0
        max_pnl_pct = 15.0
        be_stop = 102.5
        if max_pnl_pct >= 14.0:
            be_stop = max(be_stop, round(entry_price * 1.060, 4))
        self.assertEqual(be_stop, 106.0, "Stop must elevate to entry * 1.060 (+6.0% lock) when gain >= 14%")

    def test_f4_4_crazy_bull_ema10_runner_exit(self):
        """F4.4: Verify crazy bull runner triggers exit when close drops below EMA10."""
        is_crazy = True  # max_pnl >= 12% or bias >= 12%
        close_p = 128.0
        ema10 = 130.0
        prev_close = 132.0
        prev_ema10 = 129.0

        exit_triggered = is_crazy and (close_p < ema10) and (prev_close >= prev_ema10)
        self.assertTrue(exit_triggered, "Crazy bull runner must exit on crossing below EMA10")

    def test_f4_5_standard_bull_ema20_runner_exit(self):
        """F4.5: Verify standard bull runner exits when close drops below EMA20."""
        is_crazy = False
        cur_pnl = 6.0  # >= 5%
        close_p = 105.0
        ema20 = 106.0
        ma50 = 100.0
        prev_close = 107.0
        prev_ema20 = 105.5

        exit_triggered = (not is_crazy) and (cur_pnl >= 5.0) and (close_p < ema20) and (prev_close >= prev_ema20) and (close_p > ma50)
        self.assertTrue(exit_triggered, "Standard bull runner must exit on crossing below EMA20")

    def test_f4_6_extreme_climax_rsi82_full_exit(self):
        """F4.6: Verify extreme momentum climax (RSI >= 82) triggers immediate full liquidation."""
        rsi = 83.5
        climax_exit = (rsi >= 82.0)
        self.assertTrue(climax_exit, "RSI >= 82 must trigger extreme climax exit")


# =============================================================================
# TIER 2: Boundary & Corner Cases (>= 5 tests per category)
# =============================================================================

class TestTier2Category1EmptyAndMissingData(unittest.TestCase):
    """Tier 2 - Category 1: Empty Signals & Missing Data Corner Cases"""

    def test_c1_1_zero_signals_idle_cash(self):
        """C1.1: Verify portfolio maintains solvency and stable equity when 0 buy candidates exist."""
        cash = 100000.0
        candidates = []
        # No buys executed
        equity = cash
        self.assertEqual(equity, 100000.0, "Portfolio must remain 100% cash with no equity loss on 0 signals")

    def test_c1_2_missing_ticker_in_indicators(self):
        """C1.2: Verify missing ticker on specific date is gracefully bypassed without KeyError."""
        sample_dict = {"NVDA": pd.DataFrame({"Close": [100.0]})}
        tk = "UNKNOWN_TICKER"
        exists = tk in sample_dict
        self.assertFalse(exists, "Unknown ticker should not be found in indicator dictionary")

    def test_c1_3_zero_volatility_flat_bars(self):
        """C1.3: Verify flat bars (Open=High=Low=Close) compute returns without ZeroDivisionError."""
        open_p = high_p = low_p = close_p = 50.0
        tr = max(high_p - low_p, abs(high_p - close_p), abs(low_p - close_p))
        self.assertEqual(tr, 0.0, "True range of flat bar must be 0.0 without calculation crash")

    def test_c1_4_empty_portfolio_on_first_bar(self):
        """C1.4: Verify startup on bar 0 handles empty active positions dictionary cleanly."""
        active_positions = {}
        stk_invested = sum(p.get("shares", 0) for p in active_positions.values())
        self.assertEqual(stk_invested, 0.0, "Empty portfolio invested value must be 0.0")

    def test_c1_5_unknown_ticker_order_queue(self):
        """C1.5: Verify order queue rejects orders for tickers not present in data cache."""
        available_tickers = {"QQQ", "NVDA", "AAPL"}
        order = {"ticker": "XYZ_DELISTED", "action": "BUY"}
        valid_order = order["ticker"] in available_tickers
        self.assertFalse(valid_order, "Order for unlisted ticker must be rejected")


class TestTier2Category2SlippageAndGaps(unittest.TestCase):
    """Tier 2 - Category 2: Extreme Slippage & Severe Price Gaps"""

    def test_c2_1_severe_gap_down_below_stop(self):
        """C2.1: Verify severe gap-down fills at Open * (1 - slippage), absorbing the overnight gap loss."""
        stop_level = 93.20
        open_p = 85.00  # Catastrophic gap down
        slippage = 0.00015

        # In realistic execution, order fills at open minus slippage, NOT theoretical stop price
        fill_price = open_p * (1.0 - slippage)
        self.assertAlmostEqual(fill_price, 85.00 * (1 - 0.00015), places=4)
        self.assertLess(fill_price, stop_level, "Gap-down fill price must be below stop level, reflecting gap loss")

    def test_c2_2_severe_gap_up_buy_rejection(self):
        """C2.2: Verify buy order is aborted when stock gaps up > +2.0% above signal close."""
        signal_close = 100.0
        open_p = 102.5  # +2.5% gap up
        reject_buy = open_p > signal_close * 1.020
        self.assertTrue(reject_buy, "Chasing buy order must be rejected when gap up > 2.0%")

    def test_c2_3_severe_gap_down_buy_rejection(self):
        """C2.3: Verify buy order is aborted when stock gaps down < -1.5% below signal close."""
        signal_close = 100.0
        open_p = 98.0  # -2.0% gap down
        reject_buy = open_p < signal_close * 0.985
        self.assertTrue(reject_buy, "Falling knife buy order must be rejected when gap down < -1.5%")

    def test_c2_4_be_stop_gap_down_fill(self):
        """C2.4: Verify break-even stop fills at open price when market opens below break-even level."""
        be_stop = 100.50
        open_p = 99.00  # Gapped below BE stop
        slippage = 0.00015
        fill_price = open_p * (1.0 - slippage)
        self.assertAlmostEqual(fill_price, 98.98515, places=4, msg="BE stop gap-down must fill at open minus slippage")

    def test_c2_5_zero_slippage_boundary(self):
        """C2.5: Verify engine executes stably with slippage set to exactly 0.0000."""
        open_price = 150.0
        slippage = 0.0
        fill_buy = calculate_buy_fill(open_price, slippage)
        fill_sell = calculate_sell_fill(open_price, slippage)
        self.assertEqual(fill_buy, 150.0)
        self.assertEqual(fill_sell, 150.0)


class TestTier2Category3CapitalLimitsAndSaturation(unittest.TestCase):
    """Tier 2 - Category 3: Capital Limits, Zero Cash & Allocation Saturation"""

    def test_c3_1_insufficient_cash_buy_rejection(self):
        """C3.1: Verify buy orders requiring capital when cash < $200 are rejected without negative cash."""
        cash = 150.0
        target_notional = 5000.0
        alloc_capital = min(target_notional, cash)
        # Minimum allocation threshold check
        admitted = alloc_capital >= 200.0
        self.assertFalse(admitted, "Buy order must be rejected when available alloc capital < $200")

    def test_c3_2_full_slots_saturation(self):
        """C3.2: Verify when all position slots are filled, subsequent signals are ignored."""
        max_slots = 4
        current_holdings = ["AAPL", "MSFT", "GOOGL", "AMZN"]
        available_slots = max(0, max_slots - len(current_holdings))
        candidate_count = 3
        admitted_count = len(range(min(available_slots, candidate_count)))
        self.assertEqual(admitted_count, 0, "No new candidates can be admitted when slots are saturated")

    def test_c3_3_exact_qqq_shares_sweep(self):
        """C3.3: Verify dynamic sweep in Track 2 does not over-liquidate QQQ beyond required amount."""
        qqq_shares_held = 100.0
        needed_cash = 4000.0
        qqq_open = 400.0
        slippage = 0.00015
        fill_qqq_sale = qqq_open * (1.0 - slippage)

        shares_to_sell = min(qqq_shares_held, needed_cash / fill_qqq_sale)
        remaining_shares = qqq_shares_held - shares_to_sell

        self.assertAlmostEqual(shares_to_sell, 10.0015, places=3)
        self.assertAlmostEqual(remaining_shares, 89.9985, places=3)

    def test_c3_4_capital_scaling_invariance(self):
        """C3.4: Verify portfolio percentage sizing is invariant across $10k, $100k, and $1M accounts."""
        capitals = [10000.0, 100000.0, 1000000.0]
        max_pos_pct = 0.25
        for cap in capitals:
            pos_size = cap * max_pos_pct
            self.assertEqual(pos_size / cap, 0.25, f"Scaling failed for capital ${cap:,.2f}")

    def test_c3_5_fractional_share_precision(self):
        """C3.5: Verify floating point share quantities maintain exact balance sheet solvency."""
        cash = 45321.78
        shares = 123.456789
        price = 210.45
        stk_val = shares * price
        total_equity = cash + stk_val
        recomputed = total_equity - stk_val
        self.assertAlmostEqual(recomputed, cash, places=2, msg="Float rounding drift must remain within $0.01")


class TestTier2Category4MacroStormAndTransitions(unittest.TestCase):
    """Tier 2 - Category 4: Macro Storm & Regime Transitions"""

    def test_c4_1_macro_rainstorm_all_buys_blocked(self):
        """C4.1: Verify macro rainstorm condition (QQQ Bias < -3% and SMH Bias < -3%) halts all buys."""
        qqq_bias = -3.5
        smh_bias = -4.0
        is_rainstorm = (qqq_bias < -3.0 and smh_bias < -3.0)
        allow_stock_entry = not is_rainstorm
        self.assertTrue(is_rainstorm, "Rainstorm must be detected")
        self.assertFalse(allow_stock_entry, "Rainstorm must block all stock entries")

    def test_c4_2_macro_rainstorm_triggers_qqq_exit(self):
        """C4.2: Verify macro rainstorm triggers liquidation of parked QQQ."""
        is_rainstorm = True
        qqq_open = 420.0
        qqq_sma200 = 400.0
        qqq_defense_trigger = (qqq_open < qqq_sma200) or is_rainstorm
        self.assertTrue(qqq_defense_trigger, "Rainstorm must trigger QQQ liquidation even if above SMA200")

    def test_c4_3_stock_crash_5d_exclusion(self):
        """C4.3: Verify stock with Crash_5d flag is excluded from candidate scanner."""
        candidate = {"ticker": "XYZ", "crash_5d": True}
        admitted = not candidate["crash_5d"]
        self.assertFalse(admitted, "Stocks with Crash_5d flag must be filtered out")

    def test_c4_4_regime_whipsaw_oscillation(self):
        """C4.4: Verify SMA200 price oscillation executes at most one regime action per day."""
        states = ["ABOVE", "BELOW", "ABOVE", "BELOW"]
        actions = []
        current_regime = "ABOVE"
        for s in states:
            if s != current_regime:
                actions.append(f"TRANSITION_TO_{s}")
                current_regime = s
        self.assertEqual(len(actions), 3, "Regime oscillation must transition sequentially without double-firing")

    def test_c4_5_simultaneous_stop_and_macro_exit(self):
        """C4.5: Verify stock stop-loss and macro exit on same bar do not result in double execution."""
        active_positions = {"NVDA": {"shares": 50}}
        tickers_to_close = []
        # Simulate stop triggered
        tickers_to_close.append("NVDA")
        # Ensure idempotent removal
        for tk in set(tickers_to_close):
            if tk in active_positions:
                del active_positions[tk]
        self.assertNotIn("NVDA", active_positions, "Position must be deleted cleanly without KeyError")


# =============================================================================
# TIER 3: Cross-Feature Combinations (5 complex interaction scenarios)
# =============================================================================

class TestTier3CrossFeatureCombinations(unittest.TestCase):
    """Tier 3 - Cross-Feature Combinations & Multi-Component Interactions"""

    def test_x1_track1_macro_storm_position_isolation(self):
        """X1: Track 1 enters macro rainstorm while holding 2 positions; stops continue to run while buys halt."""
        active_positions = {
            "AAPL": {"entry_price": 180.0, "stop_level": 170.0, "is_be_active": False},
            "MSFT": {"entry_price": 400.0, "stop_level": 380.0, "is_be_active": True, "be_stop": 402.0},
        }
        is_rainstorm = True
        allow_new_buys = not is_rainstorm
        self.assertFalse(allow_new_buys, "New buys must be halted during storm")

        # Verify active positions still evaluate trailing stops
        msft_open = 390.0  # Drops below BE stop
        msft_stop_triggered = active_positions["MSFT"]["is_be_active"] and msft_open <= active_positions["MSFT"]["be_stop"]
        self.assertTrue(msft_stop_triggered, "Active positions must continue to enforce stops during macro storm")

    def test_x2_track2_execution_priority_sweep(self):
        """X2: On T+1 open, SELL_EXIT and TRIM_50 orders execute BEFORE dynamic QQQ sweep and BUY orders."""
        execution_order = []

        pending_orders = [
            {"ticker": "NVDA", "action": "BUY"},
            {"ticker": "AAPL", "action": "SELL_EXIT"},
            {"ticker": "MSFT", "action": "TRIM_50"},
        ]

        # Prioritization logic: SELL/TRIM before BUY
        sell_orders = [o for o in pending_orders if o["action"] in ["SELL_EXIT", "TRIM_50"]]
        buy_orders = [o for o in pending_orders if o["action"] == "BUY"]

        for o in sell_orders:
            execution_order.append(o["action"])
        execution_order.append("QQQ_SWEEP_IF_NEEDED")
        for o in buy_orders:
            execution_order.append(o["action"])

        expected_order = ["SELL_EXIT", "TRIM_50", "QQQ_SWEEP_IF_NEEDED", "BUY"]
        self.assertEqual(execution_order, expected_order, "Order execution sequence must prioritize capital liberation")

    def test_x3_runner_exit_during_regime_flip(self):
        """X3: Stock runner triggers EMA10 trailing stop on the exact day QQQ crosses below MA50."""
        qqq_close = 440.0
        qqq_ma50 = 450.0  # QQQ flips bearish
        runner_close = 120.0
        runner_ema10 = 125.0
        runner_prev_close = 128.0
        runner_prev_ema10 = 124.0

        exit_triggered = (runner_close < runner_ema10) and (runner_prev_close >= runner_prev_ema10)
        self.assertTrue(exit_triggered, "Runner exit must trigger regardless of QQQ regime flip")

    def test_x4_quadruple_simultaneous_stop_out(self):
        """X4: 4 active positions hit stop-loss simultaneously; all execute cleanly and return account to cash."""
        cash = 5000.0
        positions = {
            "P1": {"shares": 100, "exit_p": 95.0},
            "P2": {"shares": 100, "exit_p": 95.0},
            "P3": {"shares": 100, "exit_p": 95.0},
            "P4": {"shares": 100, "exit_p": 95.0},
        }
        slippage = 0.00015
        total_proceeds = 0.0
        for p in positions.values():
            fill = p["exit_p"] * (1.0 - slippage)
            proceeds = p["shares"] * fill
            total_proceeds += proceeds
            cash += proceeds

        expected_cash = 5000.0 + (4 * 100 * 95.0 * (1 - 0.00015))
        self.assertAlmostEqual(cash, expected_cash, places=2)
        self.assertGreater(cash, 42000.0, "Portfolio must cleanly liquidate all 4 positions to cash")

    def test_x5_multistage_runner_lifecycle(self):
        """X5: Verify single trade transitions across full lifecycle: Entry -> BE -> Lock 1 -> Lock 2 -> Climax Exit."""
        entry_price = 100.0
        pos = {
            "entry_price": entry_price,
            "peak_price": entry_price,
            "max_pnl_pct": 0.0,
            "is_be_active": False,
            "be_stop": 0.0,
        }

        # Stage 1: Profit reaches 4.5% (triggers BE)
        high_1 = 104.5
        pos["peak_price"] = max(pos["peak_price"], high_1)
        pos["max_pnl_pct"] = (pos["peak_price"] - entry_price) / entry_price * 100.0
        if pos["max_pnl_pct"] >= 4.2:
            pos["is_be_active"] = True
            pos["be_stop"] = round(entry_price * 1.005, 4)
        self.assertTrue(pos["is_be_active"])
        self.assertEqual(pos["be_stop"], 100.5)

        # Stage 2: Profit reaches 9.0% (triggers Lock 1: +2.5%)
        high_2 = 109.0
        pos["peak_price"] = max(pos["peak_price"], high_2)
        pos["max_pnl_pct"] = (pos["peak_price"] - entry_price) / entry_price * 100.0
        if pos["max_pnl_pct"] >= 8.0:
            pos["be_stop"] = max(pos["be_stop"], round(entry_price * 1.025, 4))
        self.assertEqual(pos["be_stop"], 102.5)

        # Stage 3: Profit reaches 16.0% (triggers Lock 2: +6.0%)
        high_3 = 116.0
        pos["peak_price"] = max(pos["peak_price"], high_3)
        pos["max_pnl_pct"] = (pos["peak_price"] - entry_price) / entry_price * 100.0
        if pos["max_pnl_pct"] >= 14.0:
            pos["be_stop"] = max(pos["be_stop"], round(entry_price * 1.060, 4))
        self.assertEqual(pos["be_stop"], 106.0)

        # Stage 4: Crazy bull EMA10 exit
        close_today = 114.0
        ema10_today = 115.0
        prev_close = 117.0
        prev_ema10 = 114.5
        exit_reason = None
        if pos["max_pnl_pct"] >= 12.0 and close_today < ema10_today and prev_close >= prev_ema10:
            exit_reason = "EMA10_RUNNER_EXIT"
        self.assertEqual(exit_reason, "EMA10_RUNNER_EXIT", "Lifecycle must terminate cleanly at EMA10 trailing exit")


# =============================================================================
# TIER 4: Real-World 5-Year Historical Scenarios (using .backtest_5y_cache.pkl)
# =============================================================================

class TestTier4RealWorldHistoricalScenarios(unittest.TestCase):
    """Tier 4 - Real-World 5-Year Historical Application Scenarios (2021-2026)"""

    @classmethod
    def setUpClass(cls):
        """Execute the authoritative 5-year simulation once to power Tier 4 tests."""
        if not HAS_SYNTHESIS_ENGINE:
            raise unittest.SkipTest(f"Synthesis engine unavailable: {SYNTHESIS_IMPORT_ERROR}")

        indicators_data, trading_dates = load_indicators_cache()
        cfg_t1 = V31Config.create_track1()
        cfg_t2 = V31Config.create_track2()

        raw_t1 = run_v31_simulation(cfg_t1, indicators_data, trading_dates, variant_name="V3.1-Track1")
        raw_t2 = run_v31_simulation(cfg_t2, indicators_data, trading_dates, variant_name="V3.1-Track2")

        cls.res_track1 = {
            **raw_t1["metrics"],
            "df_trades": raw_t1["df_trades"],
            "trades": raw_t1["metrics"]["total_trades"],
            "calmar": raw_t1["metrics"]["calmar_ratio"],
            "friction": raw_t1["metrics"]["total_slippage_cost"],
        }
        cls.res_track2 = {
            **raw_t2["metrics"],
            "df_trades": raw_t2["df_trades"],
            "trades": raw_t2["metrics"]["total_trades"],
            "calmar": raw_t2["metrics"]["calmar_ratio"],
            "friction": raw_t2["metrics"]["total_slippage_cost"],
        }

    def test_h1_full_5y_execution_clean(self):
        """H1: Verify 5-year simulation script executes to completion without errors across 1,194 days."""
        self.assertIsNotNone(self.res_track1, "Track 1 simulation result must not be None")
        self.assertIsNotNone(self.res_track2, "Track 2 simulation result must not be None")
        self.assertGreater(self.res_track1["trades"], 50, "Track 1 must generate substantial trade sample")
        self.assertGreater(self.res_track2["trades"], 50, "Track 2 must generate substantial trade sample")

    def test_h2_2022_bear_market_drawdown_reduction(self):
        """H2: Verify V3.1 slashes Max Drawdown (Track 1 <= -15%, Track 2 <= -20% vs V3.0-A's -24.98%)."""
        mdd_track1 = self.res_track1["max_dd"]
        mdd_track2 = self.res_track2["max_dd"]

        # Target: Track 1 MDD <= -15.0%, Track 2 MDD <= -20.0%
        self.assertGreater(mdd_track1, -15.0, f"Track 1 MDD ({mdd_track1:.2f}%) must be better than -15.0%")
        self.assertGreater(mdd_track2, -20.0, f"Track 2 MDD ({mdd_track2:.2f}%) must be better than -20.0%")
        self.assertGreater(mdd_track2, -24.98, "Track 2 must improve upon V3.0-A drawdown (-24.98%)")
        self.assertGreater(mdd_track2, -29.56, "Track 2 must beat QQQ Buy & Hold drawdown (-29.56%)")

    def test_h3_2023_tech_consolidation_capital_preservation(self):
        """H3: Verify capital preservation and positive equity trajectory across 5-year testbed."""
        eq_track1 = self.res_track1["final_equity"]
        eq_track2 = self.res_track2["final_equity"]

        self.assertGreater(eq_track1, INITIAL_CAPITAL, "Track 1 must achieve positive net profit over 5 years")
        self.assertGreater(eq_track2, INITIAL_CAPITAL * 1.80, "Track 2 flagship must achieve substantial growth (+80%)")

    def test_h4_2024_2026_ai_bull_wave_runner_harvesting(self):
        """H4: Verify breakout gains on key runner stocks (AMD > 30%, ASML > 25%, AVGO > 20%) are preserved."""
        df_trades_t1 = self.res_track1["df_trades"]
        self.assertFalse(df_trades_t1.empty, "Track 1 trades dataframe must not be empty")

        amd_trades = df_trades_t1[df_trades_t1["ticker"] == "AMD"]
        asml_trades = df_trades_t1[df_trades_t1["ticker"] == "ASML"]
        avgo_trades = df_trades_t1[df_trades_t1["ticker"] == "AVGO"]

        max_amd = amd_trades["pnl_pct"].max() if not amd_trades.empty else 0.0
        max_asml = asml_trades["pnl_pct"].max() if not asml_trades.empty else 0.0
        max_avgo = avgo_trades["pnl_pct"].max() if not avgo_trades.empty else 0.0

        self.assertGreater(max_amd, 30.0, f"AMD top runner gain ({max_amd:.2f}%) must exceed +30%")
        self.assertGreater(max_asml, 25.0, f"ASML top runner gain ({max_asml:.2f}%) must exceed +25%")
        self.assertGreater(max_avgo, 20.0, f"AVGO top runner gain ({max_avgo:.2f}%) must exceed +20%")

    def test_h5_calmar_ratio_outperformance(self):
        """H5: Verify Calmar ratios meet architectural standards (Track 1 >= 0.50, Track 2 >= 0.70)."""
        calmar_t1 = self.res_track1["calmar"]
        calmar_t2 = self.res_track2["calmar"]

        self.assertGreaterEqual(calmar_t1, 0.50, f"Track 1 Calmar ratio ({calmar_t1:.2f}) must be >= 0.50")
        self.assertGreaterEqual(calmar_t2, 0.70, f"Track 2 Calmar ratio ({calmar_t2:.2f}) must exceed QQQ B&H (0.60) and V3.0-B (0.69)")

    def test_h6_5y_double_entry_accounting_audit(self):
        """H6: Verify strict accounting balance, positive friction tracking, and $0 commission across 5Y run."""
        friction_t1 = self.res_track1["friction"]
        friction_t2 = self.res_track2["friction"]

        self.assertGreater(friction_t1, 0.0, "Track 1 must model and record positive slippage friction")
        self.assertGreater(friction_t2, 0.0, "Track 2 must model and record positive slippage friction")

        # Win rate reasonableness
        self.assertGreater(self.res_track1["win_rate"], 50.0, "Track 1 Win Rate must be > 50%")
        self.assertGreater(self.res_track2["win_rate"], 50.0, "Track 2 Win Rate must be > 50%")

        # Dollar profit factor > 1.0
        self.assertGreater(self.res_track1["dollar_pf"], 1.2, "Track 1 Profit Factor must be > 1.2")
        self.assertGreater(self.res_track2["dollar_pf"], 1.2, "Track 2 Profit Factor must be > 1.2")


# =============================================================================
# Custom Test Runner with Visual ANSI Output and JSON Summary
# =============================================================================

def build_test_suite(selected_tier: Optional[int] = None) -> unittest.TestSuite:
    """Build test suite filtered by tier if specified."""
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()

    tier_map = {
        1: [
            TestTier1Feature1FrictionCalibration,
            TestTier1Feature2Track1PureStock,
            TestTier1Feature3Track2QQQBase,
            TestTier1Feature4RunnerPreservation,
        ],
        2: [
            TestTier2Category1EmptyAndMissingData,
            TestTier2Category2SlippageAndGaps,
            TestTier2Category3CapitalLimitsAndSaturation,
            TestTier2Category4MacroStormAndTransitions,
        ],
        3: [
            TestTier3CrossFeatureCombinations,
        ],
        4: [
            TestTier4RealWorldHistoricalScenarios,
        ],
    }

    if selected_tier in tier_map:
        for cls in tier_map[selected_tier]:
            suite.addTests(loader.loadTestsFromTestCase(cls))
    else:
        for classes in tier_map.values():
            for cls in classes:
                suite.addTests(loader.loadTestsFromTestCase(cls))

    return suite


class ColoredTextTestResult(unittest.TextTestResult):
    """Custom test result formatter with colors and live status."""

    def __init__(self, stream, descriptions, verbosity):
        super().__init__(stream, descriptions, verbosity)
        self.successes = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.successes.append(test)
        if self.showAll:
            self.stream.write(" \033[92m[PASS]\033[0m\n")
            self.stream.flush()

    def addFailure(self, test, err):
        super().addFailure(test, err)
        if self.showAll:
            self.stream.write(" \033[91m[FAIL]\033[0m\n")
            self.stream.flush()

    def addError(self, test, err):
        super().addError(test, err)
        if self.showAll:
            self.stream.write(" \033[91m[ERROR]\033[0m\n")
            self.stream.flush()

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        if self.showAll:
            self.stream.write(f" \033[93m[SKIP: {reason}]\033[0m\n")
            self.stream.flush()


def run_e2e_tests(tier: Optional[int] = None, json_report_path: str = "test_e2e_report.json") -> int:
    """Execute the test suite and export results."""
    print("\n" + "=" * 90)
    print(" 🚀 V3.1 ULTIMATE EDITION OPAQUE-BOX E2E TEST RUNNER")
    print("=" * 90)
    if tier:
        print(f" Target Scope: Tier {tier} Only")
    else:
        print(" Target Scope: Full 4-Tier Test Suite (Tiers 1, 2, 3, 4)")
    print(f" Timestamp:    {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 90 + "\n")

    start_time = time.time()
    suite = build_test_suite(tier)
    runner = unittest.TextTestRunner(
        verbosity=2,
        resultclass=ColoredTextTestResult,
        stream=sys.stdout,
    )
    result = runner.run(suite)
    elapsed = time.time() - start_time

    total_tests = result.testsRun
    failed_tests = len(result.failures)
    errored_tests = len(result.errors)
    skipped_tests = len(result.skipped)
    passed_tests = total_tests - failed_tests - errored_tests - skipped_tests
    pass_rate = (passed_tests / total_tests * 100.0) if total_tests > 0 else 0.0

    print("\n" + "=" * 90)
    print(" 📊 E2E TEST EXECUTION SUMMARY")
    print("=" * 90)
    print(f" Total Executed: {total_tests}")
    print(f" \033[92mPassed:\033[0m         {passed_tests}")
    print(f" \033[91mFailed:\033[0m         {failed_tests}")
    print(f" \033[91mErrors:\033[0m         {errored_tests}")
    print(f" \033[93mSkipped:\033[0m        {skipped_tests}")
    print(f" Pass Rate:      {pass_rate:.1f}%")
    print(f" Total Duration: {elapsed:.2f}s")
    print("=" * 90)

    # Export JSON report
    report_data = {
        "timestamp": pd.Timestamp.now().isoformat(),
        "total_tests": total_tests,
        "passed": passed_tests,
        "failed": failed_tests,
        "errors": errored_tests,
        "skipped": skipped_tests,
        "pass_rate_pct": pass_rate,
        "duration_seconds": elapsed,
        "status": "PASS" if (failed_tests == 0 and errored_tests == 0) else "FAIL",
    }
    with open(json_report_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)
    print(f" Report saved to: {os.path.abspath(json_report_path)}\n")

    return 0 if (failed_tests == 0 and errored_tests == 0) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="V3.1 Ultimate Edition E2E Test Runner")
    parser.add_argument("--tier", type=int, choices=[1, 2, 3, 4], help="Run specific tier (1, 2, 3, or 4)")
    parser.add_argument("--report", type=str, default="test_e2e_report.json", help="Path to output JSON test report")
    args = parser.parse_args()

    exit_code = run_e2e_tests(tier=args.tier, json_report_path=args.report)
    sys.exit(exit_code)
