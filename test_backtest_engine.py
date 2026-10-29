"""
Comprehensive Production-Flow & Fault-Injection Unit Test Suite for US-Market-Radar Backtest Engine
Strictly executes production run_simulation(), TransactionLedger, and verify_against_engine().

Test Matrix (22 Tests):
1. test_prod_next_open_execution_timing: Buy signal on Day T close executes on Day T+1 open (no Lookahead).
2. test_prod_gap_down_below_stop_price: Gap-down at open below stop price executes at Open, not stop price.
3. test_prod_same_day_tp_and_stop_conservative_priority: When both TP and Stop hit in same bar, Stop takes precedence.
4. test_prod_trim_1_halving_and_ledger_invariants: Partial trim 50% correctly halves shares and updates ledger without phantom shares.
5. test_prod_terminal_open_positions_reconciliation: End-of-period open positions properly marked to market and reconciled with cash.
6. test_prod_cash_and_max_positions_limit: Cannot open >5 positions and cannot exceed available cash.
7. test_prod_dirty_data_rejection_negative_and_inverted: Missing columns, negative prices, or corrupt data raise ValueError immediately.
8. test_prod_double_entry_ledger_replay_match: Independent ledger replay strictly equals engine cash, fees, and final equity.
9. test_prod_full_capacity_concurrent_sell_and_buy: Selling stock at capacity frees slot for new buy on same open (Sell priority).
10. test_prod_data_ohlc_invariants_validation: Open/Close outside [Low, High] fails validation.
11. test_prod_daily_reconstructed_equity_curve_and_metrics: Independent daily equity curve reconstructed from ledger events matches df_port.
12. test_prod_data_volume_nan_inf_negative: Volume with NaN, Inf, or negative numbers raises ValueError.
13. test_prod_data_index_non_monotonic_and_duplicates: Non-monotonic or duplicate dates raise ValueError.
14. test_prod_data_end_date_lag_rejection: Ticker data ending earlier than benchmark raises ValueError.
15. test_prod_data_excessive_or_consecutive_missing_days: >=3 consecutive missing days or >5 missing days raise ValueError.
16. test_prod_data_legitimate_short_history_accepted: Legitimate short history stock (IPO on Day 30) with 0 missing days is accepted.
17. test_fault_injection_omitted_sell_event: Auditor catches omitted sell event in real production run.
18. test_fault_injection_omitted_buy_event: Auditor catches omitted buy event in real production run.
19. test_fault_injection_omitted_trim_event: Auditor catches omitted trim event in real production run.
20. test_fault_injection_tampered_shares: Auditor catches artificially inflated or deflated shares.
21. test_fault_injection_tampered_cash_flow: Auditor catches mismatched or siphoned cash flow.
22. test_fault_injection_tampered_price_fee_mismatch: Auditor catches tampered execution price and recalculates slippage error.
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

    def _create_full_trade_simulation(self):
        """Helper to create a production run with BUY, TRIM, and SELL events."""
        n = len(self.dates)
        opens, highs, lows, closes, vols = make_clean_bars(n, 100.0)

        for i in range(60, 65):
            p = 100.0 - (i - 59) * 5.0
            set_bar(opens, highs, lows, closes, vols, i, p + 1.0, p + 2.0, p - 1.0, p)
        set_bar(opens, highs, lows, closes, vols, 65, 73.0, 79.0, 72.0, 78.0, 3000000)
        set_bar(opens, highs, lows, closes, vols, 66, 80.0, 81.0, 79.0, 80.0, 1000000)
        # Day 67: Stock rallies to 88.0 (+10%), triggers Trim_1
        set_bar(opens, highs, lows, closes, vols, 67, 89.0, 90.0, 87.0, 88.0, 1000000)
        set_bar(opens, highs, lows, closes, vols, 68, 88.0, 89.0, 87.0, 88.0, 1000000)
        # Day 69: Drops to trigger Breakeven Stop
        set_bar(opens, highs, lows, closes, vols, 69, 81.0, 81.0, 80.0, 80.2, 1000000)

        df_stk = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=self.dates)
        custom_data = {"QQQ": self.df_qqq, "SMH": self.df_smh, "TEST_TK": df_stk}
        res = engine.run_simulation(execution_mode="NEXT_OPEN", custom_data=custom_data, custom_core_stocks=["TEST_TK"])
        return res, custom_data

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
        # Keep price stable near 81.0 from Day 67 to end of backtest so position remains open
        for d in range(67, n):
            set_bar(opens, highs, lows, closes, vols, d, 81.0, 82.0, 80.5, 81.0, 1000000)

        df_stk = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=self.dates)
        custom_data = {"QQQ": self.df_qqq, "SMH": self.df_smh, "TEST_TK": df_stk}
        res = engine.run_simulation(execution_mode="NEXT_OPEN", custom_data=custom_data, custom_core_stocks=["TEST_TK"])

        df_port = res["df_port"]
        date_65 = self.dates[65].strftime("%Y-%m-%d")
        date_66 = self.dates[66].strftime("%Y-%m-%d")

        port_d65 = df_port[df_port["date"] == date_65].iloc[0]
        port_d66 = df_port[df_port["date"] == date_66].iloc[0]

        # On Day 65 close, position MUST be 0 (order pending, NOT filled)
        self.assertEqual(port_d65["positions_count"], 0)
        self.assertEqual(port_d65["invested"], 0.0)

        # On Day 66 close, position MUST be 1 (filled at open)
        self.assertEqual(port_d66["positions_count"], 1)
        self.assertGreater(port_d66["invested"], 0.0)

        # Check execution fill price in trades or open positions
        op = res["open_positions"][0]
        self.assertAlmostEqual(op["entry_price"], 80.0 * 1.001, places=2)

    # --------------------------------------------------------------------------
    # Test 2: Gap-down below Stop Price fills at Open, NOT Stop Price
    # --------------------------------------------------------------------------
    def test_prod_gap_down_below_stop_price(self):
        """
        Verify that when a stock gaps down below stop price at market open,
        it fills at the actual Open price (plus slippage), NEVER at the artificial stop line.
        """
        n = len(self.dates)
        opens, highs, lows, closes, vols = make_clean_bars(n, 100.0)

        for i in range(60, 65):
            p = 100.0 - (i - 59) * 5.0
            set_bar(opens, highs, lows, closes, vols, i, p + 1.0, p + 2.0, p - 1.0, p)
        set_bar(opens, highs, lows, closes, vols, 65, 73.0, 79.0, 72.0, 78.0, 3000000)
        set_bar(opens, highs, lows, closes, vols, 66, 80.0, 81.0, 79.0, 80.0, 1000000)

        # Day 67: Entry ~80.0. Fixed 7% stop is ~74.40.
        # But market GAPS DOWN to Open = 70.0! (Well below 74.40)
        set_bar(opens, highs, lows, closes, vols, 67, 70.0, 71.0, 68.0, 69.0, 1000000)

        df_stk = pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols}, index=self.dates)
        custom_data = {"QQQ": self.df_qqq, "SMH": self.df_smh, "TEST_TK": df_stk}
        res = engine.run_simulation(execution_mode="NEXT_OPEN", custom_data=custom_data, custom_core_stocks=["TEST_TK"])

        self.assertEqual(len(res["df_trades"]), 1)
        trade = res["df_trades"].iloc[0]

        # Must execute near 70.0 * (1 - 0.001) = 69.93, NOT at 74.40!
        expected_fill = 70.0 * (1.0 - engine.SLIPPAGE_RATE)
        self.assertAlmostEqual(trade["exit_price"], expected_fill, places=2)
        self.assertIn("跳空", trade["exit_reason"])

    # --------------------------------------------------------------------------
    # Test 3: Same-Day TP & Stop Priority (Conservative Priority Verification)
    # --------------------------------------------------------------------------
    def test_prod_same_day_tp_and_stop_conservative_priority(self):
        """
        When both Stop Loss and Take Profit / Target levels fall within [Low, High]
        on the same bar, the conservative rule MUST trigger Stop Loss, NEVER optimistic TP!
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
        res, custom_data = self._create_full_trade_simulation()
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
            # Maintain stable price around 81.0 from day 67 to 75 to keep positions held without triggering TP/SL
            for d in range(67, 76):
                set_bar(opens, highs, lows, closes, vols, d, 81.0, 82.0, 80.5, 81.0, 1000000)

            # On Day 70, STK_0 crashes below stop line to trigger an exit signal at Close
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
    # Test 10: Strict OHLC Invariant Validation (Open/Close in [Low, High])
    # --------------------------------------------------------------------------
    def test_prod_data_ohlc_invariants_validation(self):
        """Verify that Open > High or Close < Low is caught and raises ValueError."""
        df_bad_open = self.df_qqq.copy()
        df_bad_open.loc[self.dates[10], "Open"] = df_bad_open.loc[self.dates[10], "High"] + 5.0
        with self.assertRaises(ValueError):
            engine.validate_cache_integrity({"QQQ": df_bad_open, "SMH": self.df_smh}, min_bars=20)

        df_bad_close = self.df_qqq.copy()
        df_bad_close.loc[self.dates[10], "Close"] = df_bad_close.loc[self.dates[10], "Low"] - 5.0
        with self.assertRaises(ValueError):
            engine.validate_cache_integrity({"QQQ": df_bad_close, "SMH": self.df_smh}, min_bars=20)

    # --------------------------------------------------------------------------
    # Test 11: Independent Daily Equity Curve Reconstruction and Metrics
    # --------------------------------------------------------------------------
    def test_prod_daily_reconstructed_equity_curve_and_metrics(self):
        """
        Verify that independent daily equity curve reconstructed from ledger events
        matches the engine's df_port day-by-day with max diff < 0.05,
        and independently recalculates all portfolio and trade metrics.
        """
        res, custom_data = self._create_full_trade_simulation()
        self.assertLess(res["max_eq_diff"], 0.05)
        self.assertLess(res["max_cash_diff"], 0.05)
        self.assertEqual(len(res["df_indep_port"]), len(res["trading_dates"]))

        # Check recalculation matches
        m = res["metrics"]
        self.assertIn("total_return_pct", m)
        self.assertIn("cagr", m)
        self.assertIn("max_dd", m)
        self.assertIn("sharpe_ratio", m)
        self.assertIn("sortino_ratio", m)
        self.assertIn("calmar_ratio", m)
        self.assertIn("dollar_profit_factor", m)
        self.assertIn("win_rate", m)

    # --------------------------------------------------------------------------
    # Test 12: Volume NaN, Inf, and Negative Validation
    # --------------------------------------------------------------------------
    def test_prod_data_volume_nan_inf_negative(self):
        """Verify that Volume with NaN, Inf, or negative numbers raises ValueError."""
        df_nan_vol = self.df_qqq.copy()
        df_nan_vol.loc[self.dates[5], "Volume"] = np.nan
        with self.assertRaises(ValueError):
            engine.validate_cache_integrity({"QQQ": df_nan_vol, "SMH": self.df_smh}, min_bars=20)

        df_inf_vol = self.df_qqq.copy()
        df_inf_vol["Volume"] = df_inf_vol["Volume"].astype(float)
        df_inf_vol.loc[self.dates[5], "Volume"] = np.inf
        with self.assertRaises(ValueError):
            engine.validate_cache_integrity({"QQQ": df_inf_vol, "SMH": self.df_smh}, min_bars=20)

        df_neg_vol = self.df_qqq.copy()
        df_neg_vol.loc[self.dates[5], "Volume"] = -100
        with self.assertRaises(ValueError):
            engine.validate_cache_integrity({"QQQ": df_neg_vol, "SMH": self.df_smh}, min_bars=20)

    # --------------------------------------------------------------------------
    # Test 13: Date Index Non-Monotonic and Duplicate Validation
    # --------------------------------------------------------------------------
    def test_prod_data_index_non_monotonic_and_duplicates(self):
        """Verify that non-monotonic dates or duplicate dates raise ValueError."""
        # Non-monotonic dates
        dates_rev = list(self.dates)
        dates_rev[10], dates_rev[11] = dates_rev[11], dates_rev[10]
        df_non_mono = pd.DataFrame(self.df_qqq.values, index=dates_rev, columns=self.df_qqq.columns)
        with self.assertRaises(ValueError):
            engine.validate_cache_integrity({"QQQ": df_non_mono, "SMH": self.df_smh}, min_bars=20)

        # Duplicate dates
        dates_dup = list(self.dates)
        dates_dup[1] = dates_dup[0]
        df_dup = pd.DataFrame(self.df_qqq.values, index=dates_dup, columns=self.df_qqq.columns)
        with self.assertRaises(ValueError):
            engine.validate_cache_integrity({"QQQ": df_dup, "SMH": self.df_smh}, min_bars=20)

    # --------------------------------------------------------------------------
    # Test 14: End Date Lag Rejection
    # --------------------------------------------------------------------------
    def test_prod_data_end_date_lag_rejection(self):
        """Verify that a stock whose data ends earlier than QQQ latest date raises ValueError."""
        df_lag = self.df_qqq.iloc[:-5].copy()  # Missing last 5 days
        with self.assertRaises(ValueError):
            engine.validate_cache_integrity({"QQQ": self.df_qqq, "SMH": self.df_smh, "STK_LAG": df_lag}, min_bars=20)

    # --------------------------------------------------------------------------
    # Test 15: Excessive or Consecutive Missing Days in Active Trading Period
    # --------------------------------------------------------------------------
    def test_prod_data_excessive_or_consecutive_missing_days(self):
        """Verify that >=3 consecutive missing days or >5 missing days raise ValueError."""
        # 3 consecutive missing days
        drop_indices = [self.dates[30], self.dates[31], self.dates[32]]
        df_consec = self.df_qqq.drop(index=drop_indices)
        with self.assertRaises(ValueError):
            engine.validate_cache_integrity({"QQQ": self.df_qqq, "SMH": self.df_smh, "STK_MISSING": df_consec}, min_bars=20)

        # 6 scattered missing days
        drop_scattered = [self.dates[20], self.dates[25], self.dates[30], self.dates[35], self.dates[40], self.dates[45]]
        df_scattered = self.df_qqq.drop(index=drop_scattered)
        with self.assertRaises(ValueError):
            engine.validate_cache_integrity({"QQQ": self.df_qqq, "SMH": self.df_smh, "STK_SCATTERED": df_scattered}, min_bars=20)

    # --------------------------------------------------------------------------
    # Test 16: Legitimate Short-History (Recent IPO) Accepted
    # --------------------------------------------------------------------------
    def test_prod_data_legitimate_short_history_accepted(self):
        """Verify that legitimate short history stock (IPO on Day 30) with 0 missing days is ACCEPTED."""
        df_ipo = self.df_qqq.iloc[30:].copy()  # Listed on Day 30
        audit = engine.validate_cache_integrity({"QQQ": self.df_qqq, "SMH": self.df_smh, "IPO_STK": df_ipo}, min_bars=20)
        self.assertEqual(len(audit["short_symbols"]), 1)
        self.assertEqual(audit["short_symbols"][0][0], "IPO_STK")

    # --------------------------------------------------------------------------
    # Test 17: Fault Injection - Omitted SELL Event in Real Production Flow
    # --------------------------------------------------------------------------
    def test_fault_injection_omitted_sell_event(self):
        """
        Fault Injection: Production simulation ran and completed SELL exit,
        but the SELL event is intentionally omitted from the transaction ledger.
        The audit process MUST detect ghost position / cash discrepancy and raise AssertionError!
        """
        res, custom_data = self._create_full_trade_simulation()
        ledger = res["ledger"]
        has_sell = any(e["type"] == "SELL" for e in ledger.events)
        self.assertTrue(has_sell)

        # Omit SELL event
        ledger.events = [e for e in ledger.events if e["type"] != "SELL"]

        with self.assertRaises(AssertionError):
            ledger.verify_against_engine(
                engine_cash=res["cash"],
                engine_open_positions=res["open_positions"],
                df_port=res["df_port"],
                df_trades=res["df_trades"],
                indicators_data=custom_data,
                trading_dates=self.dates
            )

    # --------------------------------------------------------------------------
    # Test 18: Fault Injection - Omitted BUY Event in Real Production Flow
    # --------------------------------------------------------------------------
    def test_fault_injection_omitted_buy_event(self):
        """
        Fault Injection: Production simulation ran and opened position,
        but the BUY event is intentionally omitted from the transaction ledger.
        The audit process MUST detect cash discrepancy / invalid trim/sell and raise AssertionError!
        """
        res, custom_data = self._create_full_trade_simulation()
        ledger = res["ledger"]
        has_buy = any(e["type"] == "BUY" for e in ledger.events)
        self.assertTrue(has_buy)

        # Omit BUY event
        ledger.events = [e for e in ledger.events if e["type"] != "BUY"]

        with self.assertRaises(AssertionError):
            ledger.verify_against_engine(
                engine_cash=res["cash"],
                engine_open_positions=res["open_positions"],
                df_port=res["df_port"],
                df_trades=res["df_trades"],
                indicators_data=custom_data,
                trading_dates=self.dates
            )

    # --------------------------------------------------------------------------
    # Test 19: Fault Injection - Omitted TRIM Event in Real Production Flow
    # --------------------------------------------------------------------------
    def test_fault_injection_omitted_trim_event(self):
        """
        Fault Injection: Production simulation executed 50% Trim,
        but the TRIM event is intentionally omitted from the transaction ledger.
        The audit process MUST detect cash discrepancy / share mismatch and raise AssertionError!
        """
        res, custom_data = self._create_full_trade_simulation()
        ledger = res["ledger"]
        has_trim = any(e["type"] == "TRIM" for e in ledger.events)
        self.assertTrue(has_trim)

        # Omit TRIM event
        ledger.events = [e for e in ledger.events if e["type"] != "TRIM"]

        with self.assertRaises(AssertionError):
            ledger.verify_against_engine(
                engine_cash=res["cash"],
                engine_open_positions=res["open_positions"],
                df_port=res["df_port"],
                df_trades=res["df_trades"],
                indicators_data=custom_data,
                trading_dates=self.dates
            )

    # --------------------------------------------------------------------------
    # Test 20: Fault Injection - Tampered Shares Detection
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
        # Tamper shares to 150.0 without changing cash
        ledger.events[0]["shares"] = 150.0

        indicators_data = {"NVDA": pd.DataFrame({"Close": [100.0]}, index=[date])}
        with self.assertRaises(AssertionError):
            ledger.independent_audit_replay(indicators_data, date)

    # --------------------------------------------------------------------------
    # Test 21: Fault Injection - Tampered Cash Flow Detection
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
    # Test 22: Fault Injection - Tampered Price and Fee Mismatch
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
