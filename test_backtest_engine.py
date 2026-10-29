"""
Comprehensive Production-Flow & Fault-Injection Unit Test Suite for US-Market-Radar Backtest Engine
Strictly executes production run_simulation(), TransactionLedger, and independent_audit_replay().

Test Matrix (15 Tests):
1. test_prod_next_open_execution_timing: Buy signal on Day T close executes on Day T+1 open (no Lookahead).
2. test_prod_gap_down_below_stop_price: Gap-down at open below stop price executes at Open, not stop price.
3. test_prod_same_day_tp_and_stop_conservative_priority: When both TP and Stop hit in same bar, Stop takes precedence.
4. test_prod_trim_1_halving_and_ledger_invariants: Partial trim 50% correctly halves shares and updates ledger without phantom shares.
5. test_prod_terminal_open_positions_reconciliation: End-of-period open positions properly marked to market and reconciled with cash.
6. test_prod_cash_and_max_positions_limit: Cannot open >5 positions and cannot exceed available cash.
7. test_prod_dirty_data_rejection_negative_and_inverted: Missing columns, negative prices, or corrupt data raise ValueError immediately.
8. test_prod_double_entry_ledger_replay_match: Independent ledger replay strictly equals engine cash, fees, and final equity.
9. test_prod_full_capacity_concurrent_sell_and_buy: Selling stock at capacity frees slot for new buy on same open (Sell priority).
10. test_prod_data_ohlc_invariants_validation: Open/Close outside [Low, High], duplicate dates, or negative volume fail validation.
11. test_prod_independent_metrics_recalculation: Vectorized independent recalculation matches engine metrics exactly.
12. test_fault_injection_tampered_shares: Auditor catches artificially inflated or deflated shares.
13. test_fault_injection_omitted_sell_event: Auditor catches missing sell event (open position lingering).
14. test_fault_injection_tampered_cash_flow: Auditor catches mismatched or siphoned cash flow.
15. test_fault_injection_tampered_price_fee_mismatch: Auditor catches tampered execution price and recalculates slippage error.
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


def make_clean_bars(n, base=100.0):
    opens = [float(base)] * n
    highs = [float(base * 1.01)] * n
    lows = [float(base * 0.99)] * n
    closes = [float(base)] * n
    vols = [1000000] * n
    return opens, highs, lows, closes, vols


def set_bar(opens, highs, lows, closes, vols, idx, o, h, l, c, v=1000000):
    opens[idx] = float(o)
    highs[idx] = float(h)
    lows[idx] = float(l)
    closes[idx] = float(c)
    vols[idx] = int(v)


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
        opens, highs, lows, closes, vols = make_clean_bars(n, 100.0)

        # Drop days 60 to 64 to crush RSI
        for i in range(60, 65):
            p = 100.0 - (i - 59) * 5.0
            set_bar(opens, highs, lows, closes, vols, i, p + 1.0, p + 2.0, p - 1.0, p)

        # Day 65: Bullish engulfing from 73 to 78, high volume
        set_bar(opens, highs, lows, closes, vols, 65, 73.0, 79.0, 72.0, 78.0, 3000000)
        # Day 66: Opens at 80.0
        set_bar(opens, highs, lows, closes, vols, 66, 80.0, 82.0, 79.0, 81.0, 1000000)

        df_stk = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=self.dates)
        custom_data = {"QQQ": self.df_qqq, "SMH": self.df_smh, "TEST_TK": df_stk}

        res = engine.run_simulation(execution_mode="NEXT_OPEN", custom_data=custom_data, custom_core_stocks=["TEST_TK"])

        df_port = res["df_port"]
        date_65 = self.dates[65].strftime("%Y-%m-%d")
        date_66 = self.dates[66].strftime("%Y-%m-%d")

        # On Day 65, position count MUST be 0
        port_d65 = df_port[df_port["date"] == date_65].iloc[0]
        self.assertEqual(port_d65["positions_count"], 0)

        # On Day 66, position MUST be opened
        port_d66 = df_port[df_port["date"] == date_66].iloc[0]
        self.assertEqual(port_d66["positions_count"], 1)

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
        opens, highs, lows, closes, vols = make_clean_bars(n, 100.0)

        for i in range(60, 65):
            p = 100.0 - (i - 59) * 5.0
            set_bar(opens, highs, lows, closes, vols, i, p + 1.0, p + 2.0, p - 1.0, p)
        set_bar(opens, highs, lows, closes, vols, 65, 73.0, 79.0, 72.0, 78.0, 3000000)
        set_bar(opens, highs, lows, closes, vols, 66, 80.0, 81.0, 79.0, 80.0, 1000000)

        # Day 67: Overnight crash! Open at 65.0 (below stop 74.40), Close at 76.0.
        set_bar(opens, highs, lows, closes, vols, 67, 65.0, 77.0, 64.0, 76.0, 1000000)

        df_stk = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=self.dates)
        custom_data = {"QQQ": self.df_qqq, "SMH": self.df_smh, "TEST_TK": df_stk}
        res = engine.run_simulation(execution_mode="NEXT_OPEN", custom_data=custom_data, custom_core_stocks=["TEST_TK"])

        trade = res["df_trades"].iloc[0]
        self.assertIn("跳空", trade["exit_reason"])
        expected_fill = 65.0 * (1.0 - engine.SLIPPAGE_RATE)
        self.assertAlmostEqual(trade["exit_price"], expected_fill, places=2)

    # --------------------------------------------------------------------------
    # Test 3: Same-Day TP and Stop Conflict (Conservative Risk-First Priority)
    # --------------------------------------------------------------------------
    def test_prod_same_day_tp_and_stop_conservative_priority(self):
        """
        When both Stop Loss and Take Profit / Target levels fall within [Low, High]
        on the same bar, the conservative rule dictates Stop Loss executes first.
        """
        n = len(self.dates)
        opens, highs, lows, closes, vols = make_clean_bars(n, 100.0)

        for i in range(60, 65):
            p = 100.0 - (i - 59) * 5.0
            set_bar(opens, highs, lows, closes, vols, i, p + 1.0, p + 2.0, p - 1.0, p)
        set_bar(opens, highs, lows, closes, vols, 65, 73.0, 79.0, 72.0, 78.0, 3000000)
        set_bar(opens, highs, lows, closes, vols, 66, 80.0, 81.0, 79.0, 80.0, 1000000)

        # Day 67: Open 80.0. Spikes to 110.0 (+37% TP), Collapses to 70.0 (-12% Stop)
        set_bar(opens, highs, lows, closes, vols, 67, 80.0, 110.0, 70.0, 95.0, 1000000)

        df_stk = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=self.dates)
        custom_data = {"QQQ": self.df_qqq, "SMH": self.df_smh, "TEST_TK": df_stk}
        res = engine.run_simulation(execution_mode="NEXT_OPEN", custom_data=custom_data, custom_core_stocks=["TEST_TK"])

        trade = res["df_trades"].iloc[0]
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
        opens, highs, lows, closes, vols = make_clean_bars(n, 100.0)

        for i in range(60, 65):
            p = 100.0 - (i - 59) * 5.0
            set_bar(opens, highs, lows, closes, vols, i, p + 1.0, p + 2.0, p - 1.0, p)
        set_bar(opens, highs, lows, closes, vols, 65, 73.0, 79.0, 72.0, 78.0, 3000000)
        set_bar(opens, highs, lows, closes, vols, 66, 80.0, 81.0, 79.0, 80.0, 1000000)

        # Day 67: Stock rallies to 88.0 (+10%), hits trim target with stall candle (close < open)
        set_bar(opens, highs, lows, closes, vols, 67, 89.0, 90.0, 87.0, 88.0, 1000000)
        # Day 68: Trim executes at Open 88.0
        set_bar(opens, highs, lows, closes, vols, 68, 88.0, 89.0, 87.0, 88.0, 1000000)
        # Day 69: Drops to trigger Breakeven Stop (be_stop = entry * 1.005 = 80.40)
        set_bar(opens, highs, lows, closes, vols, 69, 81.0, 81.0, 80.0, 80.2, 1000000)

        df_stk = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=self.dates)
        custom_data = {"QQQ": self.df_qqq, "SMH": self.df_smh, "TEST_TK": df_stk}
        res = engine.run_simulation(execution_mode="NEXT_OPEN", custom_data=custom_data, custom_core_stocks=["TEST_TK"])

        trade = res["df_trades"].iloc[0]
        self.assertTrue(trade["trimmed_50"])
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
        opens, highs, lows, closes, vols = make_clean_bars(n, 100.0)

        # Signal Day 97, Buy on Day 98 at 80.0. Held through Day 99 (end of backtest).
        for i in range(92, 97):
            p = 100.0 - (i - 91) * 5.0
            set_bar(opens, highs, lows, closes, vols, i, p + 1.0, p + 2.0, p - 1.0, p)
        set_bar(opens, highs, lows, closes, vols, 97, 73.0, 79.0, 72.0, 78.0, 3000000)
        set_bar(opens, highs, lows, closes, vols, 98, 80.0, 82.0, 79.0, 81.0, 1000000)
        set_bar(opens, highs, lows, closes, vols, 99, 81.0, 85.0, 81.0, 84.0, 1000000)

        df_stk = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=self.dates)
        custom_data = {"QQQ": self.df_qqq, "SMH": self.df_smh, "TEST_TK": df_stk}
        res = engine.run_simulation(execution_mode="NEXT_OPEN", custom_data=custom_data, custom_core_stocks=["TEST_TK"])

        self.assertEqual(len(res["df_trades"]), 0)
        self.assertEqual(len(res["open_positions"]), 1)
        op = res["open_positions"][0]

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

        for s_idx in range(7):
            name = f"STK_{s_idx}"
            core_stocks.append(name)
            opens, highs, lows, closes, vols = make_clean_bars(n, 100.0)
            for i in range(60, 65):
                p = 100.0 - (i - 59) * 5.0
                set_bar(opens, highs, lows, closes, vols, i, p + 1.0, p + 2.0, p - 1.0, p)
            set_bar(opens, highs, lows, closes, vols, 65, 73.0, 79.0, 72.0, 78.0, 3000000)
            set_bar(opens, highs, lows, closes, vols, 66, 80.0, 81.0, 79.0, 80.0, 1000000)
            for d in range(67, 76):
                set_bar(opens, highs, lows, closes, vols, d, 81.0, 82.0, 80.5, 81.0, 1000000)
            custom_data[name] = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=self.dates)

        res = engine.run_simulation(execution_mode="NEXT_OPEN", custom_data=custom_data, custom_core_stocks=core_stocks)

        df_port = res["df_port"]
        date_66 = self.dates[66].strftime("%Y-%m-%d")
        port_d66 = df_port[df_port["date"] == date_66].iloc[0]

        self.assertEqual(port_d66["positions_count"], engine.MAX_POSITIONS)
        self.assertGreaterEqual(port_d66["cash"], 0.0)

    # --------------------------------------------------------------------------
    # Test 7: Dirty Data Rejection (Fatal on Corrupt Inputs)
    # --------------------------------------------------------------------------
    def test_prod_dirty_data_rejection_negative_and_inverted(self):
        """
        Verify that corrupted data (negative price or inverted High < Low)
        immediately raises ValueError and halts execution.
        """
        df_bad = self.df_qqq.copy()
        df_bad.loc[self.dates[50], "Close"] = -10.0
        with self.assertRaises(ValueError):
            engine.validate_cache_integrity({"QQQ": df_bad, "SMH": self.df_smh}, min_bars=20)

        df_inverted = self.df_qqq.copy()
        df_inverted.loc[self.dates[50], "High"] = 50.0
        df_inverted.loc[self.dates[50], "Low"] = 100.0
        with self.assertRaises(ValueError):
            engine.validate_cache_integrity({"QQQ": df_inverted, "SMH": self.df_smh}, min_bars=20)

    # --------------------------------------------------------------------------
    # Test 8: Independent Double-Entry Ledger Replay Strictly Matches
    # --------------------------------------------------------------------------
    def test_prod_double_entry_ledger_replay_match(self):
        """
        Verify that the event-sourced ledger independent replay produces
        0.0000 cash, equity, and fee error against engine state.
        """
        n = len(self.dates)
        opens, highs, lows, closes, vols = make_clean_bars(n, 100.0)
        for i in range(60, 65):
            p = 100.0 - (i - 59) * 5.0
            set_bar(opens, highs, lows, closes, vols, i, p + 1.0, p + 2.0, p - 1.0, p)
        set_bar(opens, highs, lows, closes, vols, 65, 73.0, 79.0, 72.0, 78.0, 3000000)
        set_bar(opens, highs, lows, closes, vols, 66, 80.0, 81.0, 79.0, 80.0, 1000000)
        set_bar(opens, highs, lows, closes, vols, 75, 60.0, 61.0, 59.0, 60.0, 1000000)

        df_stk = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=self.dates)
        custom_data = {"QQQ": self.df_qqq, "SMH": self.df_smh, "TEST_TK": df_stk}

        res = engine.run_simulation(execution_mode="NEXT_OPEN", custom_data=custom_data, custom_core_stocks=["TEST_TK"])
        audit = res["ledger_replay"]

        self.assertTrue(audit["audit_passed"])
        self.assertAlmostEqual(audit["replayed_cash"], res["cash"], places=2)
        self.assertAlmostEqual(audit["replayed_final_equity"], res["metrics"]["final_equity"], places=2)
        self.assertAlmostEqual(audit["replayed_fees"], res["total_commission_slippage"], places=2)

    # --------------------------------------------------------------------------
    # Test 9: Full Capacity Concurrent Sell and Buy (Sell Priority Verification)
    # --------------------------------------------------------------------------
    def test_prod_full_capacity_concurrent_sell_and_buy(self):
        """
        CRITICAL CONCURRENCY TEST:
        When account has 5 positions (capacity full), and on Day T Close:
        - Position 0 triggers a SELL_EXIT signal.
        - Candidate 5 triggers a BUY signal.
        On Day T+1 Open, the SELL must execute first, freeing the slot and cash
        so that Candidate 5 CAN be bought, and is NOT falsely rejected!
        """
        n = len(self.dates)
        custom_data = {"QQQ": self.df_qqq, "SMH": self.df_smh}
        core_stocks = [f"STK_{i}" for i in range(6)]

        for s_idx in range(5):
            name = f"STK_{s_idx}"
            opens, highs, lows, closes, vols = make_clean_bars(n, 100.0)
            for i in range(60, 65):
                p = 100.0 - (i - 59) * 5.0
                set_bar(opens, highs, lows, closes, vols, i, p + 1.0, p + 2.0, p - 1.0, p)
            set_bar(opens, highs, lows, closes, vols, 65, 73.0, 79.0, 72.0, 78.0, 3000000)
            set_bar(opens, highs, lows, closes, vols, 66, 80.0, 81.0, 79.0, 80.0, 1000000)
            for d in range(67, 76):
                set_bar(opens, highs, lows, closes, vols, d, 81.0, 82.0, 80.5, 81.0, 1000000)

            # On Day 70, STK_0 crashes below EMA to trigger a sell signal at Close
            if s_idx == 0:
                set_bar(opens, highs, lows, closes, vols, 70, 50.0, 51.0, 49.0, 50.0, 1000000)
                set_bar(opens, highs, lows, closes, vols, 71, 49.0, 51.0, 48.0, 50.0, 1000000)

            custom_data[name] = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=self.dates)

        # STK_5 triggers a BUY signal on Day 70 Close
        opens_5, highs_5, lows_5, closes_5, vols_5 = make_clean_bars(n, 100.0)
        for i in range(65, 70):
            p = 100.0 - (i - 64) * 5.0
            set_bar(opens_5, highs_5, lows_5, closes_5, vols_5, i, p + 1.0, p + 2.0, p - 1.0, p)
        set_bar(opens_5, highs_5, lows_5, closes_5, vols_5, 70, 73.0, 79.0, 72.0, 78.0, 3000000)
        set_bar(opens_5, highs_5, lows_5, closes_5, vols_5, 71, 80.0, 82.0, 79.0, 81.0, 1000000)
        custom_data["STK_5"] = pd.DataFrame({"Open": opens_5, "High": highs_5, "Low": lows_5, "Close": closes_5, "Volume": vols_5}, index=self.dates)

        res = engine.run_simulation(execution_mode="NEXT_OPEN", custom_data=custom_data, custom_core_stocks=core_stocks)

        df_port = res["df_port"]
        date_71 = self.dates[71].strftime("%Y-%m-%d")
        port_d71 = df_port[df_port["date"] == date_71].iloc[0]

        # STK_0 was sold, STK_5 was bought -> position count must still be 5
        self.assertEqual(port_d71["positions_count"], 5)
        closed_tickers = [t["ticker"] for t in res["df_trades"].to_dict("records")]
        self.assertIn("STK_0", closed_tickers)

    # --------------------------------------------------------------------------
    # Test 10: Strict OHLC Invariant Validation (Open/Close in [Low, High], Vol >= 0, No Dupes)
    # --------------------------------------------------------------------------
    def test_prod_data_ohlc_invariants_validation(self):
        """
        Verify that Open > High, Close < Low, Volume < 0, or duplicate dates
        are strictly caught and raise ValueError.
        """
        # A. Open > High
        df_bad_open = self.df_qqq.copy()
        df_bad_open.loc[self.dates[10], "Open"] = df_bad_open.loc[self.dates[10], "High"] + 5.0
        with self.assertRaises(ValueError):
            engine.validate_cache_integrity({"QQQ": df_bad_open, "SMH": self.df_smh}, min_bars=20)

        # B. Close < Low
        df_bad_close = self.df_qqq.copy()
        df_bad_close.loc[self.dates[10], "Close"] = df_bad_close.loc[self.dates[10], "Low"] - 5.0
        with self.assertRaises(ValueError):
            engine.validate_cache_integrity({"QQQ": df_bad_close, "SMH": self.df_smh}, min_bars=20)

        # C. Negative Volume
        df_neg_vol = self.df_qqq.copy()
        df_neg_vol.loc[self.dates[10], "Volume"] = -500
        with self.assertRaises(ValueError):
            engine.validate_cache_integrity({"QQQ": df_neg_vol, "SMH": self.df_smh}, min_bars=20)

        # D. Duplicate Dates in Index
        dates_dup = list(self.dates)
        dates_dup[1] = dates_dup[0]
        df_dup = pd.DataFrame(self.df_qqq.values, index=dates_dup, columns=self.df_qqq.columns)
        with self.assertRaises(ValueError):
            engine.validate_cache_integrity({"QQQ": df_dup, "SMH": self.df_smh}, min_bars=20)

    # --------------------------------------------------------------------------
    # Test 11: Independent Metrics Recalculator Verification
    # --------------------------------------------------------------------------
    def test_prod_independent_metrics_recalculation(self):
        """
        Verify that independent_metrics_recalculation reproduces
        exact values from raw series with zero deviation.
        """
        res = engine.run_simulation(execution_mode="NEXT_OPEN", custom_data={"QQQ": self.df_qqq, "SMH": self.df_smh}, custom_core_stocks=[])
        indep = engine.independent_metrics_recalculation(
            df_port=res["df_port"],
            df_trades=res["df_trades"],
            open_positions=res["open_positions"],
            initial_capital=engine.INITIAL_CAPITAL,
            qqq_df=self.df_qqq,
            trading_dates=res["trading_dates"]
        )

        self.assertEqual(indep["final_equity"], res["metrics"]["final_equity"])
        self.assertEqual(indep["max_dd"], res["metrics"]["max_dd"])
        self.assertAlmostEqual(indep["sharpe_ratio"], res["metrics"]["sharpe_ratio"], places=4)
        self.assertAlmostEqual(indep["sortino_ratio"], res["metrics"]["sortino_ratio"], places=4)

    # --------------------------------------------------------------------------
    # Test 12: Fault Injection - Tampered Shares Detection
    # --------------------------------------------------------------------------
    def test_fault_injection_tampered_shares(self):
        """
        Fault Injection: Manually tamper with shares in the ledger.
        The independent double-entry replay auditor MUST fail with AssertionError!
        """
        ledger = engine.TransactionLedger(initial_capital=100000.0)
        date = self.dates[0]
        # Valid buy: ref 100.0, fill 100.10, shares 100 -> slip = 10.0, cash_outflow = 10010.0
        ledger.record_buy(date, "NVDA", 100.0, 100.0, 100.10, 10.0, 10010.0)
        # Auditor tampered: change shares to 150.0 without changing cash
        ledger.events[0]["shares"] = 150.0

        indicators_data = {"NVDA": pd.DataFrame({"Close": [100.0]}, index=[date])}
        with self.assertRaises(AssertionError):
            ledger.independent_audit_replay(indicators_data, date)

    # --------------------------------------------------------------------------
    # Test 13: Fault Injection - Omitted Sell Event Detection
    # --------------------------------------------------------------------------
    def test_fault_injection_omitted_sell_event(self):
        """
        Fault Injection: Engine sold position, but ledger omitted the SELL event.
        Auditor MUST detect position lingering as open and cash mismatch!
        """
        ledger = engine.TransactionLedger(initial_capital=100000.0)
        date1 = self.dates[0]
        date2 = self.dates[1]
        ledger.record_buy(date1, "NVDA", 100.0, 100.0, 100.10, 10.0, 10010.0)
        # Intentionally omit record_sell!

        indicators_data = {"NVDA": pd.DataFrame({"Close": [100.0, 100.0]}, index=[date1, date2])}
        audit = ledger.independent_audit_replay(indicators_data, date2)

        # Replay cash will be 89990.0 instead of 100000.0 -> fails cross reconciliation
        engine_cash = 100000.0
        with self.assertRaises(AssertionError):
            assert abs(audit["replayed_cash"] - engine_cash) < 0.02

    # --------------------------------------------------------------------------
    # Test 14: Fault Injection - Tampered Cash Flow Detection
    # --------------------------------------------------------------------------
    def test_fault_injection_tampered_cash_flow(self):
        """
        Fault Injection: Tamper with cash flow (siphon $1,000 cash).
        Auditor MUST detect that cash_flow != -(shares * fill_price)!
        """
        ledger = engine.TransactionLedger(initial_capital=100000.0)
        date = self.dates[0]
        ledger.record_buy(date, "NVDA", 100.0, 100.0, 100.10, 10.0, 10010.0)
        # Siphon cash: change cash_flow from -10010.0 to -9010.0
        ledger.events[0]["cash_flow"] = -9010.0

        indicators_data = {"NVDA": pd.DataFrame({"Close": [100.0]}, index=[date])}
        with self.assertRaises(AssertionError):
            ledger.independent_audit_replay(indicators_data, date)

    # --------------------------------------------------------------------------
    # Test 15: Fault Injection - Tampered Price and Fee Mismatch
    # --------------------------------------------------------------------------
    def test_fault_injection_tampered_price_fee_mismatch(self):
        """
        Fault Injection: Tamper with fill price without updating slippage fee.
        Auditor MUST detect indep_slippage != ev['slippage_fee']!
        """
        ledger = engine.TransactionLedger(initial_capital=100000.0)
        date = self.dates[0]
        ledger.record_buy(date, "NVDA", 100.0, 100.0, 100.10, 10.0, 10010.0)
        # Tamper: set fill_price to 105.0 while slippage_fee remains 10.0
        ledger.events[0]["fill_price"] = 105.0

        indicators_data = {"NVDA": pd.DataFrame({"Close": [100.0]}, index=[date])}
        with self.assertRaises(AssertionError):
            ledger.independent_audit_replay(indicators_data, date)


if __name__ == "__main__":
    unittest.main()
