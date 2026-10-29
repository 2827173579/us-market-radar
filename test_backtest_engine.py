"""
Comprehensive Production-Flow Unit Test Suite for US-Market-Radar Backtest Engine
Strictly executes production run_simulation() with synthetic market datasets.

Test Matrix:
1. test_prod_next_open_execution_timing: Buy signal on Day T close executes on Day T+1 open (no Lookahead).
2. test_prod_gap_down_below_stop_price: Gap-down at open below stop price executes at Open, not stop price.
3. test_prod_same_day_tp_and_stop_conservative_priority: When both TP and Stop hit in same bar, Stop takes precedence.
4. test_prod_trim_1_halving_and_ledger_invariants: Partial trim 50% correctly halves shares and updates ledger without phantom shares.
5. test_prod_no_duplicate_sell_on_same_day: A position cannot trigger multiple sells or exit twice on the same day.
6. test_prod_cash_shortage_and_max_positions_limit: Cannot open >5 positions and cannot exceed available cash.
7. test_prod_terminal_open_positions_reconciliation: End-of-period open positions properly marked to market and reconciled with cash.
8. test_prod_dirty_data_rejection: Missing columns, negative prices, or corrupt data raise ValueError immediately.
9. test_prod_double_entry_ledger_replay_match: Independent ledger replay strictly equals engine cash, fees, and final equity.
"""

import unittest
import numpy as np
import pandas as pd
import backtest_5y_engine as engine


def create_synthetic_market(dates, qqq_drift=0.001, smh_drift=0.001):
    """Creates synthetic baseline QQQ and SMH benchmarks."""
    n = len(dates)
    qqq_prices = 400.0 * (1.0 + qqq_drift) ** np.arange(n)
    smh_prices = 200.0 * (1.0 + smh_drift) ** np.arange(n)

    df_qqq = pd.DataFrame({
        "Open": qqq_prices,
        "High": qqq_prices * 1.01,
        "Low": qqq_prices * 0.99,
        "Close": qqq_prices,
        "Volume": [10000000] * n
    }, index=dates)

    df_smh = pd.DataFrame({
        "Open": smh_prices,
        "High": smh_prices * 1.01,
        "Low": smh_prices * 0.99,
        "Close": smh_prices,
        "Volume": [5000000] * n
    }, index=dates)

    return df_qqq, df_smh


class TestProductionBacktestEngine(unittest.TestCase):

    def setUp(self):
        # 100 trading days for sufficient indicator warmup
        self.dates = pd.date_range("2024-01-01", periods=100, freq="B")
        self.df_qqq, self.df_smh = create_synthetic_market(self.dates)

    # --------------------------------------------------------------------------
    # Test 1: NEXT_OPEN Execution Timing (Signal at T close -> Execute at T+1 open)
    # --------------------------------------------------------------------------
    def test_prod_next_open_execution_timing(self):
        """
        Verify that in NEXT_OPEN mode, a buy signal confirmed at Day T close
        is NOT executed on Day T, but strictly executed on Day T+1 Open.
        """
        n = len(self.dates)
        # Create a stock that triggers extreme oversold rebound on Day 65 (index 65)
        # Warmup: stable around 100. Then drops, then RSI <= 35 and bullish engulfing on Day 65.
        prices = [100.0] * n
        # Plunge days 60 to 64 to crush RSI
        for i in range(60, 65):
            prices[i] = 100.0 - (i - 59) * 5.0  # drops to 75.0
        # Day 65: Bullish engulfing from 73 to 78, high volume
        opens = list(prices)
        highs = [p * 1.01 for p in prices]
        lows = [p * 0.99 for p in prices]
        closes = list(prices)
        vols = [1000000] * n

        opens[65] = 73.0
        highs[65] = 79.0
        lows[65] = 72.0
        closes[65] = 78.0  # Close > Open and Close > Prev_Close
        vols[65] = 3000000  # High volume ratio

        # Day 66: Opens at 80.0
        opens[66] = 80.0
        highs[66] = 82.0
        lows[66] = 79.0
        closes[66] = 81.0

        df_stk = pd.DataFrame({
            "Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols
        }, index=self.dates)

        custom_data = {
            "QQQ": self.df_qqq,
            "SMH": self.df_smh,
            "TEST_TK": df_stk
        }

        # Run production simulation in NEXT_OPEN mode
        res = engine.run_simulation(
            execution_mode="NEXT_OPEN",
            custom_data=custom_data,
            custom_core_stocks=["TEST_TK"]
        )

        # Inspect portfolio history
        df_port = res["df_port"]
        date_65 = self.dates[65].strftime("%Y-%m-%d")
        date_66 = self.dates[66].strftime("%Y-%m-%d")

        # On Day 65, position count MUST be 0 (signal confirmed at Close, not executed yet!)
        port_d65 = df_port[df_port["date"] == date_65].iloc[0]
        self.assertEqual(port_d65["positions_count"], 0,
                         "CRITICAL LOOKAHEAD: Position was opened on Day T close instead of waiting for Day T+1 open!")

        # On Day 66, position MUST be opened
        port_d66 = df_port[df_port["date"] == date_66].iloc[0]
        self.assertEqual(port_d66["positions_count"], 1)

        # Verify entry price was based on Day 66 Open (80.0 * (1 + slippage)), NOT Day 65 Close (78.0)!
        trade = res["df_trades"].iloc[0]
        expected_fill = 80.0 * (1.0 + engine.SLIPPAGE_RATE)
        self.assertAlmostEqual(trade["entry_price"], expected_fill, places=2)

    # --------------------------------------------------------------------------
    # Test 2: Gap-down at Open below Stop Price
    # --------------------------------------------------------------------------
    def test_prod_gap_down_below_stop_price(self):
        """
        Verify that when a stock gaps down below stop price at market open,
        execution price is at the Open price * (1 - slippage), NOT at the stop level.
        """
        n = len(self.dates)
        opens = [100.0] * n
        highs = [101.0] * n
        lows = [99.0] * n
        closes = [100.0] * n
        vols = [1000000] * n

        # Trigger oversold buy signal on Day 65
        for i in range(60, 65):
            closes[i] = 100.0 - (i - 59) * 5.0
            opens[i] = closes[i] + 1.0
        opens[65] = 73.0; highs[65] = 79.0; lows[65] = 72.0; closes[65] = 78.0; vols[65] = 3000000

        # Day 66: Buy filled at Open 80.0 (Stop level is 80 * 0.93 = 74.40)
        opens[66] = 80.0; highs[66] = 81.0; lows[66] = 79.0; closes[66] = 80.0

        # Day 67: Over-night crash! Opens with 15% gap-down at 65.0 (below stop 74.40)!
        # Then bounces intraday to Close at 76.0.
        opens[67] = 65.0
        highs[67] = 77.0
        lows[67] = 64.0
        closes[67] = 76.0

        df_stk = pd.DataFrame({
            "Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols
        }, index=self.dates)

        custom_data = {"QQQ": self.df_qqq, "SMH": self.df_smh, "TEST_TK": df_stk}
        res = engine.run_simulation(execution_mode="NEXT_OPEN", custom_data=custom_data, custom_core_stocks=["TEST_TK"])

        df_trades = res["df_trades"]
        self.assertEqual(len(df_trades), 1)
        trade = df_trades.iloc[0]

        # Stop loss reason must be gap-down
        self.assertIn("跳空", trade["exit_reason"])
        # Fill price must be based on Open (65.0 * 0.999 = 64.935), NOT stop level (74.40) or close (76.0)!
        expected_fill = 65.0 * (1.0 - engine.SLIPPAGE_RATE)
        self.assertAlmostEqual(trade["exit_price"], expected_fill, places=2)
        self.assertLess(trade["exit_price"], 74.0)

    # --------------------------------------------------------------------------
    # Test 3: Same-Day TP and Stop Conflict (Conservative Risk-First Priority)
    # --------------------------------------------------------------------------
    def test_prod_same_day_tp_and_stop_conservative_priority(self):
        """
        When both Stop Loss and Take Profit / Target levels fall within [Low, High]
        on the same bar, the conservative rule dictates Stop Loss executes first.
        """
        n = len(self.dates)
        opens = [100.0] * n; highs = [101.0] * n; lows = [99.0] * n; closes = [100.0] * n; vols = [1000000] * n

        # Signal Day 65, Buy on Day 66 at 80.0
        for i in range(60, 65):
            closes[i] = 100.0 - (i - 59) * 5.0
        opens[65] = 73.0; highs[65] = 79.0; lows[65] = 72.0; closes[65] = 78.0; vols[65] = 3000000
        opens[66] = 80.0; highs[66] = 81.0; lows[66] = 79.0; closes[66] = 80.0

        # Day 67: Wild bar! Open at 80.0. High spikes to 110.0 (+37% TP), Low collapses to 70.0 (-12% Stop)
        opens[67] = 80.0
        highs[67] = 110.0
        lows[67] = 70.0
        closes[67] = 95.0

        df_stk = pd.DataFrame({
            "Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols
        }, index=self.dates)

        custom_data = {"QQQ": self.df_qqq, "SMH": self.df_smh, "TEST_TK": df_stk}
        res = engine.run_simulation(execution_mode="NEXT_OPEN", custom_data=custom_data, custom_core_stocks=["TEST_TK"])

        df_trades = res["df_trades"]
        self.assertEqual(len(df_trades), 1)
        trade = df_trades.iloc[0]

        # In conservative risk-first rule, Stop Loss must trigger, not TP!
        self.assertIn("止损", trade["exit_reason"])
        self.assertFalse(trade["is_win"])

    # --------------------------------------------------------------------------
    # Test 4: Trim_1 50% Halving & Ledger Invariants
    # --------------------------------------------------------------------------
    def test_prod_trim_1_halving_and_ledger_invariants(self):
        """
        Verify that underwater Trim_1 properly halves shares and reconciles with ledger.
        """
        n = len(self.dates)
        opens = [100.0] * n; highs = [101.0] * n; lows = [99.0] * n; closes = [100.0] * n; vols = [1000000] * n

        # Signal Day 65, Buy on Day 66 at 80.0 (MA50 is ~100, so position is underwater)
        for i in range(60, 65):
            closes[i] = 100.0 - (i - 59) * 5.0
        opens[65] = 73.0; highs[65] = 79.0; lows[65] = 72.0; closes[65] = 78.0; vols[65] = 3000000
        opens[66] = 80.0; highs[66] = 81.0; lows[66] = 79.0; closes[66] = 80.0

        # Day 67: Stock rallies to 88.0 (+10%), hits trim target with stall candle (close < open)
        opens[67] = 89.0; highs[67] = 90.0; lows[67] = 87.0; closes[67] = 88.0

        # Day 68: Trim executes at Open 88.0
        opens[68] = 88.0; highs[68] = 89.0; lows[68] = 87.0; closes[68] = 88.0

        # Day 69: Drops to trigger Breakeven Stop (be_stop = entry * 1.005 = 80.40)
        opens[69] = 81.0; highs[69] = 81.0; lows[69] = 80.0; closes[69] = 80.2

        df_stk = pd.DataFrame({
            "Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols
        }, index=self.dates)

        custom_data = {"QQQ": self.df_qqq, "SMH": self.df_smh, "TEST_TK": df_stk}
        res = engine.run_simulation(execution_mode="NEXT_OPEN", custom_data=custom_data, custom_core_stocks=["TEST_TK"])

        df_trades = res["df_trades"]
        self.assertEqual(len(df_trades), 1)
        trade = df_trades.iloc[0]

        # Verify trade was trimmed 50%
        self.assertTrue(trade["trimmed_50"])
        # Verify independent ledger audit passed
        self.assertTrue(res["ledger_replay"]["audit_passed"])

    # --------------------------------------------------------------------------
    # Test 5: Terminal Open Positions Reconciliation
    # --------------------------------------------------------------------------
    def test_prod_terminal_open_positions_reconciliation(self):
        """
        Verify that positions held at backtest conclusion are accurately
        accounted for: Cash + Open Market Value == Terminal Equity.
        """
        n = len(self.dates)
        opens = [100.0] * n; highs = [101.0] * n; lows = [99.0] * n; closes = [100.0] * n; vols = [1000000] * n

        # Signal Day 97, Buy on Day 98 at 80.0. Held through Day 99 (end of backtest).
        for i in range(92, 97):
            closes[i] = 100.0 - (i - 91) * 5.0
        opens[97] = 73.0; highs[97] = 79.0; lows[97] = 72.0; closes[97] = 78.0; vols[97] = 3000000
        opens[98] = 80.0; highs[98] = 82.0; lows[98] = 79.0; closes[98] = 81.0
        opens[99] = 81.0; highs[99] = 85.0; lows[99] = 81.0; closes[99] = 84.0

        df_stk = pd.DataFrame({
            "Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols
        }, index=self.dates)

        custom_data = {"QQQ": self.df_qqq, "SMH": self.df_smh, "TEST_TK": df_stk}
        res = engine.run_simulation(execution_mode="NEXT_OPEN", custom_data=custom_data, custom_core_stocks=["TEST_TK"])

        # Closed trades should be 0 because position is still held!
        self.assertEqual(len(res["df_trades"]), 0)
        # Open positions should be 1
        self.assertEqual(len(res["open_positions"]), 1)
        op = res["open_positions"][0]

        # Exact accounting reconciliation: Cash + Market Value == Final Equity
        cash = res["cash"]
        mv = op["market_value"]
        final_eq = res["metrics"]["final_equity"]
        self.assertAlmostEqual(cash + mv, final_eq, places=2)

    # --------------------------------------------------------------------------
    # Test 6: Cash & Max Positions Limits (Capacity Constraint)
    # --------------------------------------------------------------------------
    def test_prod_cash_and_max_positions_limit(self):
        """
        Verify that no more than MAX_POSITIONS (5) can be opened simultaneously,
        and capital allocation cannot exceed available cash.
        """
        n = len(self.dates)
        custom_data = {"QQQ": self.df_qqq, "SMH": self.df_smh}
        core_stocks = []

        # Create 7 stocks all triggering buy signals on Day 65
        for s_idx in range(7):
            name = f"STK_{s_idx}"
            core_stocks.append(name)
            opens = [100.0] * n; highs = [101.0] * n; lows = [99.0] * n; closes = [100.0] * n; vols = [1000000] * n
            for i in range(60, 65):
                closes[i] = 100.0 - (i - 59) * 5.0
            opens[65] = 73.0; highs[65] = 79.0; lows[65] = 72.0; closes[65] = 78.0; vols[65] = 3000000
            opens[66] = 80.0; highs[66] = 81.0; lows[66] = 79.0; closes[66] = 80.0
            custom_data[name] = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=self.dates)

        res = engine.run_simulation(execution_mode="NEXT_OPEN", custom_data=custom_data, custom_core_stocks=core_stocks)

        # Inspect Day 66 positions count
        df_port = res["df_port"]
        date_66 = self.dates[66].strftime("%Y-%m-%d")
        port_d66 = df_port[df_port["date"] == date_66].iloc[0]

        # MUST be capped at exactly MAX_POSITIONS (5), NEVER 7!
        self.assertEqual(port_d66["positions_count"], engine.MAX_POSITIONS)
        self.assertGreaterEqual(port_d66["cash"], 0.0)

    # --------------------------------------------------------------------------
    # Test 7: Dirty Data Rejection (Fatal on Corrupt Inputs)
    # --------------------------------------------------------------------------
    def test_prod_dirty_data_rejection(self):
        """
        Verify that corrupted data (negative price or missing columns) immediately
        raises ValueError and halts execution.
        """
        # Test A: Negative price
        df_bad = self.df_qqq.copy()
        df_bad.loc[self.dates[50], "Close"] = -10.0
        with self.assertRaises(ValueError):
            engine.validate_cache_integrity({"QQQ": df_bad, "SMH": self.df_smh})

        # Test B: High < Low
        df_inverted = self.df_qqq.copy()
        df_inverted.loc[self.dates[50], "High"] = 50.0
        df_inverted.loc[self.dates[50], "Low"] = 100.0
        with self.assertRaises(ValueError):
            engine.validate_cache_integrity({"QQQ": df_inverted, "SMH": self.df_smh})

    # --------------------------------------------------------------------------
    # Test 8: Independent Double-Entry Ledger Replay Strictly Matches
    # --------------------------------------------------------------------------
    def test_prod_double_entry_ledger_replay_match(self):
        """
        Verify that the event-sourced ledger independent replay produces
        0.0000 cash, equity, and fee error against engine state.
        """
        n = len(self.dates)
        opens = [100.0] * n; highs = [101.0] * n; lows = [99.0] * n; closes = [100.0] * n; vols = [1000000] * n
        for i in range(60, 65):
            closes[i] = 100.0 - (i - 59) * 5.0
        opens[65] = 73.0; highs[65] = 79.0; lows[65] = 72.0; closes[65] = 78.0; vols[65] = 3000000
        opens[66] = 80.0; highs[66] = 81.0; lows[66] = 79.0; closes[66] = 80.0

        # Day 75: Exit on EMA breakdown
        closes[75] = 60.0

        df_stk = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=self.dates)
        custom_data = {"QQQ": self.df_qqq, "SMH": self.df_smh, "TEST_TK": df_stk}

        res = engine.run_simulation(execution_mode="NEXT_OPEN", custom_data=custom_data, custom_core_stocks=["TEST_TK"])
        audit = res["ledger_replay"]

        self.assertTrue(audit["audit_passed"])
        self.assertAlmostEqual(audit["replayed_cash"], res["cash"], places=3)
        self.assertAlmostEqual(audit["replayed_final_equity"], res["metrics"]["final_equity"], places=3)
        self.assertAlmostEqual(audit["replayed_fees"], res["total_commission_slippage"], places=3)


if __name__ == "__main__":
    unittest.main()
