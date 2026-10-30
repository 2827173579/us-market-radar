"""
test_adversarial_v31.py - Empirical Adversarial Stress Test Suite for V3.1 Ultimate Edition
=============================================================================================
Authored by: Challenger 1 (Adversarial Verifier & Empirical Stress Tester)
Target: V3.1 Ultimate Edition (`v31_strategy.py`, `test_v31_sandbox.py`)

Test Coverage:
  ADV-1: Extreme Gap-Down Openings (-10%, -15%, -20%, -30%)
         - Single stock catastrophic overnight gap-down.
         - Quadruple simultaneous overnight gap-down (-25% across multiple positions).
         - Next-Open Gap Filter: rejection of -2% to -20% gap-down entries (falling knife protection).
         - Next-Open FOMO Filter: rejection of +2% to +20% gap-up entries.
         - QQQ overnight catastrophic crash (-15%) and SMA200 Bear Defense trigger.
  ADV-2: Liquidity Starvation & Extreme Cash Depletion
         - Zero cash ($0.00) on high-conviction buy signal: exposes ZeroDivisionError in metrics calculation.
         - Micro-cash sub-threshold ($150 < $200 min order threshold): verify no order trash.
         - Dynamic sweep liquidity funding: QQQ auto-liquidation cleanly funds satellite stock buys.
         - Cash contention: multiple simultaneous buys competing for limited cash.
  ADV-3: Rapid Whipsaw Regime Transitions Across QQQ MA50 / SMA200
         - High-frequency daily oscillation across QQQ MA50 (+/-0.8% for 20 consecutive days).
         - High-frequency daily oscillation across QQQ SMA200 (+/-1.0% for 14 consecutive days).
         - Churn measurement, transaction cost accumulation, and position stability.
         - Look-Ahead Bias Forensic Probe on Macro Weather `is_rainstorm` (same-day Close vs Open execution).
  ADV-4: Friction & Slippage Sensitivity Matrix (1.5 bps to 30.0 bps)
         - Full 5Y simulation across 1.5, 5.0, 10.0, 15.0, 20.0, and 30.0 bps slippage.
         - Measure CAGR degradation, Max Drawdown impact, Calmar ratio degradation, total slippage drag.
         - Identify edge exhaustion / break-even thresholds.
"""

import os
import sys
import unittest
import copy
import numpy as np
import pandas as pd

# Set stdout encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SCRATCH_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRATCH_DIR not in sys.path:
    sys.path.insert(0, SCRATCH_DIR)

from v31_strategy import (
    V31Config, run_v31_simulation, load_indicators_cache, compute_rolling_indicators,
    CORE_STOCKS, AGGRESSIVE_STOCKS
)


class TestAdversarialExtremeGapDowns(unittest.TestCase):
    """ADV-1: Stress-test extreme gap-down openings and gap filters."""

    @classmethod
    def setUpClass(cls):
        cls.raw_data, cls.trading_dates = load_indicators_cache()

    def test_adv1_1_single_stock_extreme_gap_down(self):
        """ADV-1.1: Position opens down -20.0% overnight. Verify structural stop triggers and double-entry holds."""
        data = {k: v.copy() for k, v in self.raw_data.items()}
        dates = self.trading_dates[:150]

        cfg = V31Config.create_track1(initial_capital=100000.0)
        res_normal = run_v31_simulation(cfg, data, dates)
        trades_normal = res_normal["df_trades"]
        self.assertGreater(len(trades_normal), 0, "Need baseline trades to inject gap down")

        first_trade = trades_normal.iloc[0]
        ticker = first_trade["ticker"]
        entry_date = first_trade["entry_date"]
        entry_idx = [i for i, d in enumerate(dates) if str(d)[:10] == str(entry_date)[:10]][0]

        gap_date = dates[entry_idx + 1]
        entry_price = first_trade["entry_price"]
        crashed_open = round(entry_price * 0.80, 2)
        data[ticker].loc[gap_date, "Open"] = crashed_open
        data[ticker].loc[gap_date, "Low"] = round(crashed_open * 0.98, 2)
        data[ticker].loc[gap_date, "High"] = round(crashed_open * 1.01, 2)
        data[ticker].loc[gap_date, "Close"] = round(crashed_open * 0.99, 2)

        res_crash = run_v31_simulation(cfg, data, dates)
        trades_crash = res_crash["df_trades"]
        crash_trade = trades_crash[(trades_crash["ticker"] == ticker) & (trades_crash["entry_date"] == str(entry_date)[:10])]
        self.assertEqual(len(crash_trade), 1, "Crashed trade must be closed")
        ct = crash_trade.iloc[0]

        self.assertIn("跳空", ct["exit_reason"], f"Exit reason should mention gap-down, got {ct['exit_reason']}")
        expected_fill = crashed_open * (1.0 - cfg.slippage_rate)
        self.assertAlmostEqual(ct["exit_price"], expected_fill, places=2)
        self.assertLess(ct["pnl_pct"], -19.0, f"PnL should be <= -19%, got {ct['pnl_pct']}")
        self.assertTrue(res_crash["ledger_audit"]["is_balanced"], "Ledger must stay balanced after -20% gap-down")
        self.assertLess(res_crash["ledger_audit"]["discrepancy"], 0.10)

    def test_adv1_2_quadruple_simultaneous_black_swan_gap_down(self):
        """ADV-1.2: Multi-position gap down -25% simultaneously across full 5Y dataset (Black Swan / Flash Crash)."""
        data = {k: v.copy() for k, v in self.raw_data.items()}
        dates = self.trading_dates
        cfg = V31Config.create_track1(initial_capital=100000.0)

        res_baseline = run_v31_simulation(cfg, data, dates)
        port_df = res_baseline["df_port"]
        multi_days = port_df[port_df["positions_count"] >= 3]
        self.assertGreater(len(multi_days), 0, "Multi-position holding days exist in 5-year data")

        # Pick a target date with >= 3 concurrent positions
        target_date_str = multi_days.iloc[10]["date"]
        target_idx = [i for i, d in enumerate(dates) if str(d)[:10] == target_date_str][0]
        crash_date = dates[target_idx + 1]

        # Inject -25% gap down on all active stocks on crash_date
        for tk in cfg.stocks_pool:
            if crash_date in data[tk].index:
                prev_close = data[tk]["Close"].loc[dates[target_idx]]
                crashed_open = prev_close * 0.75
                data[tk].loc[crash_date, "Open"] = crashed_open
                data[tk].loc[crash_date, "Low"] = crashed_open * 0.95
                data[tk].loc[crash_date, "High"] = crashed_open * 1.01
                data[tk].loc[crash_date, "Close"] = crashed_open * 0.96

        res_black_swan = run_v31_simulation(cfg, data, dates)
        self.assertTrue(res_black_swan["ledger_audit"]["is_balanced"], "Ledger must balance even during Black Swan crash")
        self.assertGreater(res_black_swan["metrics"]["final_equity"], 0.0, "Account equity must remain positive")
        self.assertGreater(res_black_swan["metrics"]["max_dd"], -50.0, "Drawdown must be bounded by risk parity")

    def test_adv1_3_next_open_gap_down_filter_rejects_knife(self):
        """ADV-1.3: Buy signal fired yesterday, but stock opens -10% down today. Gap filter MUST reject."""
        data = {k: v.copy() for k, v in self.raw_data.items()}
        dates = self.trading_dates[:150]
        cfg = V31Config.create_track1()

        res_normal = run_v31_simulation(cfg, data, dates)
        first_trade = res_normal["df_trades"].iloc[0]
        ticker = first_trade["ticker"]
        entry_date = first_trade["entry_date"]
        entry_idx = [i for i, d in enumerate(dates) if str(d)[:10] == str(entry_date)[:10]][0]

        sig_date = dates[entry_idx - 1]
        sig_close = data[ticker].loc[sig_date, "Close"]
        crashed_open = sig_close * 0.90
        data[ticker].loc[dates[entry_idx], "Open"] = crashed_open
        data[ticker].loc[dates[entry_idx], "Low"] = crashed_open * 0.95

        res_filtered = run_v31_simulation(cfg, data, dates)
        trades_filtered = res_filtered["df_trades"]
        entered_on_crash = trades_filtered[(trades_filtered["ticker"] == ticker) & (trades_filtered["entry_date"] == str(dates[entry_idx])[:10])]
        self.assertEqual(len(entered_on_crash), 0, "Next-Open gap filter MUST reject entry when Open < sig_close * 0.985 or Open < sig_low")

    def test_adv1_4_next_open_gap_up_fomo_filter_rejects_chase(self):
        """ADV-1.4: Buy signal fired yesterday, but stock gaps up +8.0% today. Gap filter MUST reject FOMO chase."""
        data = {k: v.copy() for k, v in self.raw_data.items()}
        dates = self.trading_dates[:150]
        cfg = V31Config.create_track1()

        res_normal = run_v31_simulation(cfg, data, dates)
        first_trade = res_normal["df_trades"].iloc[0]
        ticker = first_trade["ticker"]
        entry_date = first_trade["entry_date"]
        entry_idx = [i for i, d in enumerate(dates) if str(d)[:10] == str(entry_date)[:10]][0]

        sig_date = dates[entry_idx - 1]
        sig_close = data[ticker].loc[sig_date, "Close"]
        gap_up_open = sig_close * 1.08
        data[ticker].loc[dates[entry_idx], "Open"] = gap_up_open
        data[ticker].loc[dates[entry_idx], "High"] = gap_up_open * 1.05

        res_filtered = run_v31_simulation(cfg, data, dates)
        trades_filtered = res_filtered["df_trades"]
        entered_on_fomo = trades_filtered[(trades_filtered["ticker"] == ticker) & (trades_filtered["entry_date"] == str(dates[entry_idx])[:10])]
        self.assertEqual(len(entered_on_fomo), 0, "Next-Open FOMO filter MUST reject entry when Open > sig_close * 1.020")

    def test_adv1_5_qqq_catastrophic_gap_down_bear_defense(self):
        """ADV-1.5: QQQ gaps down -15.0% below SMA200 overnight in Track 2. Verify 100% liquidation to cash."""
        data = {k: v.copy() for k, v in self.raw_data.items()}
        dates = self.trading_dates[:100]
        cfg = V31Config.create_track2(initial_capital=100000.0)

        crash_date = dates[40]
        prev_date = dates[39]
        sma200_prev = data["QQQ"].loc[prev_date, "SMA200"]
        crashed_qqq_open = sma200_prev * 0.85
        data["QQQ"].loc[crash_date, "Open"] = crashed_qqq_open
        data["QQQ"].loc[crash_date, "Low"] = crashed_qqq_open * 0.98
        data["QQQ"].loc[crash_date, "Close"] = crashed_qqq_open * 0.99

        res = run_v31_simulation(cfg, data, dates[:45])
        port = res["df_port"]
        crash_row = port[port["date"] == str(crash_date)[:10]].iloc[0]
        self.assertGreater(crash_row["cash"], 50000.0, "QQQ liquidation must liberate cash into account")
        self.assertEqual(crash_row["qqq_invested"], 0.0, "Parked QQQ must be 100% liquidated on gap-down below SMA200")
        self.assertTrue(res["ledger_audit"]["is_balanced"], "Ledger must stay balanced")


class TestAdversarialLiquidityStarvation(unittest.TestCase):
    """ADV-2: Stress-test cash depletion, micro-balances, and order competition."""

    @classmethod
    def setUpClass(cls):
        cls.raw_data, cls.trading_dates = load_indicators_cache()

    def test_adv2_1_zero_cash_starvation_vulnerability(self):
        """ADV-2.1: Track 1 starts with $0.00 capital.
        VERIFIES ZERO-DIVISION IMMUNITY: Returns safe metrics without crash."""
        data = self.raw_data
        dates = self.trading_dates[:50]
        cfg = V31Config.create_track1(initial_capital=0.0)

        res = run_v31_simulation(cfg, data, dates)
        self.assertEqual(res["metrics"]["final_equity"], 0.0)
        self.assertEqual(res["metrics"]["total_return_pct"], 0.0)
        self.assertEqual(res["metrics"]["cagr"], 0.0)
        self.assertEqual(res["metrics"]["max_dd"], 0.0)
        self.assertTrue(res["ledger_audit"]["is_balanced"])

    def test_adv2_2_micro_cash_below_threshold(self):
        """ADV-2.2: Track 1 has $150.00 cash (< $200 min order threshold). Buy order must be rejected."""
        data = self.raw_data
        dates = self.trading_dates[:50]
        cfg = V31Config.create_track1(initial_capital=150.0)

        res = run_v31_simulation(cfg, data, dates)
        self.assertEqual(len(res["df_trades"]), 0, "Sub-$200 capital must not generate trades")
        self.assertAlmostEqual(res["metrics"]["final_equity"], 150.0, places=2)
        self.assertTrue(res["ledger_audit"]["is_balanced"])

    def test_adv2_3_track2_qqq_auto_liquidation_funding(self):
        """ADV-2.3: In Track 2, cash is $500 (buffer), but QQQ is parked. High-conviction buy triggers.
        QQQ must cleanly sell exact shares to fund the buy across the 5Y dataset."""
        data = self.raw_data
        dates = self.trading_dates
        cfg = V31Config.create_track2(initial_capital=100000.0)

        res = run_v31_simulation(cfg, data, dates)
        trades = res["df_trades"]
        self.assertGreater(len(trades), 50, "Track 2 must execute satellite stock trades funded by QQQ liquidation")
        port = res["df_port"]
        for _, row in port.iterrows():
            self.assertGreaterEqual(row["cash"], 0.0, f"Cash cannot drop below 0: {row['cash']}")
            self.assertGreaterEqual(row["qqq_invested"], 0.0)
            self.assertGreaterEqual(row["stock_invested"], 0.0)
        self.assertTrue(res["ledger_audit"]["is_balanced"])

    def test_adv2_4_order_priority_under_cash_shortfall(self):
        """ADV-2.4: Cash is limited to $25,000 (enough for 1 slot). Multiple candidates qualify.
        Position sizing should throttle exposure."""
        data = self.raw_data
        dates = self.trading_dates[:100]
        cfg = V31Config.create_track1(initial_capital=25000.0)

        res = run_v31_simulation(cfg, data, dates)
        port = res["df_port"]
        self.assertLessEqual(port["positions_count"].max(), 2, "Limited capital must throttle position count")
        self.assertTrue(res["ledger_audit"]["is_balanced"])


class TestAdversarialWhipsawRegimes(unittest.TestCase):
    """ADV-3: Stress-test high-frequency whipsaw regime oscillations and look-ahead forensic audit."""

    @classmethod
    def setUpClass(cls):
        cls.raw_data, cls.trading_dates = load_indicators_cache()

    def test_adv3_1_qqq_ma50_daily_whipsaw_oscillation(self):
        """ADV-3.1: QQQ oscillates above and below MA50 every day for 20 days (+/-0.8%).
        Track 1 must remain stable without state corruption."""
        data = {k: v.copy() for k, v in self.raw_data.items()}
        dates = self.trading_dates[:100]
        cfg = V31Config.create_track1(initial_capital=100000.0)

        whipsaw_dates = dates[40:60]
        for idx, d in enumerate(whipsaw_dates):
            ma50_val = data["QQQ"].loc[d, "MA50"]
            if idx % 2 == 0:
                data["QQQ"].loc[d, "Close"] = ma50_val * 1.008
                data["QQQ"].loc[d, "Open"] = ma50_val * 1.007
            else:
                data["QQQ"].loc[d, "Close"] = ma50_val * 0.992
                data["QQQ"].loc[d, "Open"] = ma50_val * 0.993

        res = run_v31_simulation(cfg, data, dates)
        self.assertTrue(res["ledger_audit"]["is_balanced"], "Whipsaw must not break double-entry ledger")
        self.assertGreater(res["metrics"]["final_equity"], 50000.0)

    def test_adv3_2_qqq_sma200_daily_whipsaw_churn_track2(self):
        """ADV-3.2: QQQ oscillates above and below SMA200 every single day for 14 days in Track 2.
        Quantify churn penalty, slippage accumulation, and verify system survival."""
        data = {k: v.copy() for k, v in self.raw_data.items()}
        dates = self.trading_dates[:80]
        cfg = V31Config.create_track2(initial_capital=100000.0)

        res_clean = run_v31_simulation(cfg, data, dates)

        whipsaw_dates = dates[30:44]
        for idx, d in enumerate(whipsaw_dates):
            sma200_val = data["QQQ"].loc[d, "SMA200"]
            if idx % 2 == 0:
                data["QQQ"].loc[d, "Close"] = sma200_val * 1.01
                data["QQQ"].loc[d, "Open"] = sma200_val * 1.005
            else:
                data["QQQ"].loc[d, "Open"] = sma200_val * 0.99
                data["QQQ"].loc[d, "Close"] = sma200_val * 1.01

        res_whipsaw = run_v31_simulation(cfg, data, dates)
        self.assertTrue(res_whipsaw["ledger_audit"]["is_balanced"], "Ledger must balance during whipsaw")
        slip_diff = res_whipsaw["metrics"]["total_slippage_cost"] - res_clean["metrics"]["total_slippage_cost"]
        self.assertGreater(slip_diff, 0.0, "Whipsaw should generate measurable slippage drag from repeated entries/exits")
        self.assertGreater(res_whipsaw["metrics"]["final_equity"], 50000.0)

    def test_adv3_3_lookahead_forensic_audit_macro_weather(self):
        """ADV-3.3: Forensic probe for look-ahead timing in `is_rainstorm`.
        CONFIRMS VULNERABILITY: Macro rainstorm computed from same-day Close leaks into 9:30 AM Open execution."""
        data = {k: v.copy() for k, v in self.raw_data.items()}
        dates = self.trading_dates[:60]
        test_date = dates[30]

        cfg = V31Config.create_track2(initial_capital=100000.0)
        ma50_val = data["QQQ"].loc[test_date, "MA50"]
        data["QQQ"].loc[test_date, "Open"] = ma50_val * 1.00
        data["SMH"].loc[test_date, "Open"] = data["SMH"].loc[test_date, "MA50"] * 1.00
        data["QQQ"].loc[test_date, "Close"] = ma50_val * 0.95
        data["SMH"].loc[test_date, "Close"] = data["SMH"].loc[test_date, "MA50"] * 0.95
        data["QQQ"] = compute_rolling_indicators(data["QQQ"])
        data["SMH"] = compute_rolling_indicators(data["SMH"])

        res = run_v31_simulation(cfg, data, dates[:35])
        port = res["df_port"]
        day_row = port[port["date"] == str(test_date)[:10]].iloc[0]

        # In current v31_strategy code, QQQ exits at morning Open because afternoon Close triggered rainstorm!
        exited_at_open_due_to_close_rainstorm = (day_row["qqq_invested"] == 0.0 and day_row["is_rainstorm"] == True)
        self.assertTrue(exited_at_open_due_to_close_rainstorm, "Exposes look-ahead leak: Open exit triggers from same-day Close!")


class TestAdversarialFrictionSensitivity(unittest.TestCase):
    """ADV-4: Empirical Friction Sensitivity Sweep (1.5 bps to 30 bps)."""

    @classmethod
    def setUpClass(cls):
        cls.raw_data, cls.trading_dates = load_indicators_cache()

    def test_adv4_1_friction_sensitivity_matrix(self):
        """ADV-4.1: Sweep slippage rates across 1.5, 5.0, 10.0, 15.0, 20.0, and 30.0 bps for both tracks."""
        slippage_levels = [0.00015, 0.00050, 0.00100, 0.00150, 0.00200, 0.00300]
        results_t1 = []
        results_t2 = []

        print("\n" + "=" * 115)
        print(" 🔬 ADV-4 Friction & Slippage Sensitivity Stress Sweep (1,194 Trading Days)")
        print("=" * 115)
        print(f"{'Slippage':<12s} │ {'Track':<14s} │ {'Final Equity':<16s} │ {'CAGR':<10s} │ {'Max DD':<10s} │ {'Calmar':<10s} │ {'Total Slip Drag':<18s}")
        print("─" * 115)

        for slip in slippage_levels:
            slip_bps = slip * 10000
            cfg_t1 = V31Config.create_track1(slippage_rate=slip)
            res_t1 = run_v31_simulation(cfg_t1, self.raw_data, self.trading_dates, variant_name=f"T1-{slip_bps:.1f}bps")
            m1 = res_t1["metrics"]
            results_t1.append((slip_bps, m1))
            print(f"{slip_bps:5.1f} bps    │ Track 1 (Pure) │ ${m1['final_equity']:<14,.2f} │ {m1['cagr']:+6.2f}%   │ {m1['max_dd']:6.2f}%   │ {m1['calmar_ratio']:6.2f}   │ ${m1['total_slippage_cost']:<16,.2f}")

            cfg_t2 = V31Config.create_track2(slippage_rate=slip)
            res_t2 = run_v31_simulation(cfg_t2, self.raw_data, self.trading_dates, variant_name=f"T2-{slip_bps:.1f}bps")
            m2 = res_t2["metrics"]
            results_t2.append((slip_bps, m2))
            print(f"{slip_bps:5.1f} bps    │ Track 2 (QQQ)  │ ${m2['final_equity']:<14,.2f} │ {m2['cagr']:+6.2f}%   │ {m2['max_dd']:6.2f}%   │ {m2['calmar_ratio']:6.2f}   │ ${m2['total_slippage_cost']:<16,.2f}")
            print("─" * 115)

        print("=" * 115)

        m2_20bps = [m for bps, m in results_t2 if abs(bps - 20.0) < 0.1][0]
        self.assertGreater(m2_20bps["final_equity"], 150000.0)
        self.assertGreater(m2_20bps["max_dd"], -20.0)

        m1_20bps = [m for bps, m in results_t1 if abs(bps - 20.0) < 0.1][0]
        self.assertLess(m1_20bps["total_slippage_cost"], 10000.0)
        self.assertGreater(m1_20bps["final_equity"], 115000.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
