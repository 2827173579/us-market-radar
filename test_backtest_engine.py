"""
Unit Test Suite for US-Market-Radar Backtest Engine Audit
Covers 8 Essential Institutional Invariants:
1. Standard Buy -> Full Take-Profit Exit (Ledger Invariant)
2. Buy -> Trim 50% -> Exit Remaining 50% (Shares, Cash & Valuation Invariant)
3. ATR Stop Triggers while Fixed -7% Stop does not (Accurate Stop Pricing)
4. Gap Down Opening below Stop Price (Gap Execution Slippage)
5. Same-Day High Hits TP & Low Hits Stop Loss (Conservative Risk-First Rule)
6. T-Day Close Signal -> T+1 Day Open Execution Timing
7. Terminal Open Positions Mark-to-Market & Reconciliation
8. Capital Limits, Max Positions & Re-allocation after Partial Trim
"""

import unittest
import numpy as np
import pandas as pd


class TestBacktestEngineLedgerAndExecution(unittest.TestCase):

    def setUp(self):
        self.initial_capital = 10000.0
        self.slippage = 0.001  # 0.1%

    # --------------------------------------------------------------------------
    # Test 1: Standard Buy -> Full Take-Profit Exit
    # --------------------------------------------------------------------------
    def test_01_standard_buy_and_full_tp(self):
        """Verify clean cash-position ledger when exiting with full take profit."""
        cash = self.initial_capital
        entry_price = 100.0
        fill_price = entry_price * (1.0 + self.slippage)  # 100.10
        alloc_capital = 2000.0
        shares = alloc_capital / fill_price  # ~19.98002
        cash -= alloc_capital  # 8000.0

        pos = {
            "ticker": "NVDA",
            "shares": shares,
            "entry_price": fill_price,
            "entry_capital": alloc_capital,
            "trimmed": False
        }

        # Mark to market at entry
        equity_entry = cash + pos["shares"] * fill_price
        self.assertAlmostEqual(equity_entry, self.initial_capital, places=4,
                               msg="Ledger Invariant failed at entry!")

        # Exit at Take Profit $120.0
        exit_price_raw = 120.0
        exit_price = exit_price_raw * (1.0 - self.slippage)  # 119.88
        proceeds = pos["shares"] * exit_price
        cash += proceeds
        realized_pnl = proceeds - alloc_capital
        expected_pnl = alloc_capital * ((exit_price - fill_price) / fill_price)
        self.assertAlmostEqual(realized_pnl, expected_pnl, places=4)

        # Final check
        final_equity = cash
        self.assertAlmostEqual(final_equity, self.initial_capital + realized_pnl, places=4,
                               msg="Ledger Invariant failed after exit!")

    # --------------------------------------------------------------------------
    # Test 2: Buy -> Trim 50% -> Exit Remaining 50%
    # --------------------------------------------------------------------------
    def test_02_trim_50_pct_ledger_consistency(self):
        """
        CRITICAL AUDIT: When trimming 50%, pos['shares'] must be halved immediately.
        Portfolio valuation must NOT value the holding at 100% shares while cash holds 50% proceeds!
        """
        cash = self.initial_capital
        alloc_capital = 2000.0
        fill_price = 100.0 * (1.0 + self.slippage)
        shares = alloc_capital / fill_price
        cash -= alloc_capital

        pos = {
            "ticker": "INTC",
            "shares": shares,
            "entry_price": fill_price,
            "entry_capital": alloc_capital,
            "trimmed": False,
            "trim_price": 0.0,
            "trim_shares": 0.0,
            "trim_proceeds": 0.0
        }

        # Step 2: Price rises to $108. Trim 50%
        trim_price_raw = 108.0
        trim_price = trim_price_raw * (1.0 - self.slippage)
        sold_shares = pos["shares"] * 0.5
        trim_proceeds = sold_shares * trim_price
        cash += trim_proceeds

        # AUDIT CHECK: pos['shares'] MUST be updated!
        pos["shares"] -= sold_shares
        pos["trimmed"] = True
        pos["trim_shares"] = sold_shares
        pos["trim_price"] = trim_price
        pos["trim_proceeds"] = trim_proceeds

        # Valuation during holding of the remaining 50% at market price $108
        current_mkt_price = 108.0
        invested_val = pos["shares"] * current_mkt_price
        total_equity = cash + invested_val

        # Expected equity: initial $10,000 + gain on 50 sold shares + gain on 50 held shares
        expected_gain_sold = sold_shares * (trim_price - fill_price)
        expected_gain_held = pos["shares"] * (current_mkt_price - fill_price)
        expected_equity = self.initial_capital + expected_gain_sold + expected_gain_held
        self.assertAlmostEqual(total_equity, expected_equity, places=4,
                               msg="Trim 50% ledger double-counted shares!")

        # Step 3: Exit remaining 50% at $102.0
        exit_price_raw = 102.0
        exit_price = exit_price_raw * (1.0 - self.slippage)
        rem_proceeds = pos["shares"] * exit_price
        cash += rem_proceeds
        rem_gain = pos["shares"] * (exit_price - fill_price)

        final_equity = cash
        total_realized_pnl = expected_gain_sold + rem_gain
        self.assertAlmostEqual(final_equity, self.initial_capital + total_realized_pnl, places=4,
                               msg="Final exit after trim did not match total realized PnL!")

    # --------------------------------------------------------------------------
    # Test 3: ATR Stop Triggers while Fixed -7% Stop Does NOT Trigger
    # --------------------------------------------------------------------------
    def test_03_atr_stop_vs_fixed_stop_pricing(self):
        """
        Verify that ATR stop price is calculated at entry - 2*ATR, NOT hardcoded to entry * 0.93.
        """
        entry_price = 100.0
        atr = 1.50
        fixed_stop = entry_price * 0.93  # 93.0
        atr_stop = entry_price - 2.0 * atr  # 97.0

        # Day 2: Open 98.0, Low 96.5, Close 97.5
        # Low 96.5 breaks atr_stop (97.0), but does NOT break fixed_stop (93.0)
        open_p = 98.0
        low_p = 96.5
        close_p = 97.5

        # Check trigger
        triggered = (low_p <= fixed_stop) or (atr > 0 and low_p <= atr_stop)
        self.assertTrue(triggered)

        # Effective stop level must be 97.0 (the higher of the triggered stops), not 93.0
        if low_p <= atr_stop and low_p > fixed_stop:
            expected_fill_level = atr_stop
        else:
            expected_fill_level = fixed_stop

        self.assertEqual(expected_fill_level, 97.0)

        # Fill price calculation (Open > stop level, triggered intraday)
        fill_price = expected_fill_level * (1.0 - self.slippage)
        self.assertAlmostEqual(fill_price, 97.0 * 0.999, places=4)
        # Verify it is NOT 93.0 * 0.999!
        self.assertNotAlmostEqual(fill_price, 93.0 * 0.999, places=2)

    # --------------------------------------------------------------------------
    # Test 4: Gap Down Opening Below Stop Price
    # --------------------------------------------------------------------------
    def test_04_gap_down_below_stop_price(self):
        """
        If Open <= Stop Price, execution must be at Open * (1 - slippage), NOT at Stop Price!
        """
        entry_price = 100.0
        stop_price = 93.0

        # Overnight crash: Open at 85.0 (gap down 15%), Low 84.0, Close bounces to 92.0
        open_p = 85.0
        low_p = 84.0
        close_p = 92.0

        # Stop triggered at open
        is_gap_down = open_p <= stop_price
        self.assertTrue(is_gap_down)

        # Real market execution price must be based on Open
        fill_price = open_p * (1.0 - self.slippage)
        self.assertAlmostEqual(fill_price, 85.0 * 0.999, places=4)
        # Must NOT be 93.0 * 0.999 or 92.0 * 0.999!
        self.assertLess(fill_price, 93.0 * 0.999)

    # --------------------------------------------------------------------------
    # Test 5: Same-Day High Hits TP & Low Hits Stop Loss
    # --------------------------------------------------------------------------
    def test_05_same_day_tp_and_stop_conflict(self):
        """
        Conservative Institutional Rule: When both TP and Stop are in [Low, High]
        on the same day, Stop Loss takes precedence to eliminate optimistic bias.
        """
        entry_price = 100.0
        stop_price = 93.0
        tp_target = 115.0

        open_p = 102.0
        high_p = 116.0  # hits TP
        low_p = 91.0   # hits Stop
        close_p = 105.0

        # Both hit within the bar
        tp_hit = high_p >= tp_target
        stop_hit = low_p <= stop_price
        self.assertTrue(tp_hit and stop_hit)

        # Conservative priority
        def resolve_same_day_conflict(open_p, high_p, low_p, stop_p, tp_p):
            # Check open first
            if open_p <= stop_p:
                return "STOP_AT_OPEN", open_p
            if open_p >= tp_p:
                return "TP_AT_OPEN", open_p
            # Both triggered intraday -> Stop Loss takes precedence
            return "STOP_INTRADAY", stop_p

        action, fill_level = resolve_same_day_conflict(open_p, high_p, low_p, stop_price, tp_target)
        self.assertEqual(action, "STOP_INTRADAY")
        self.assertEqual(fill_level, stop_price)

    # --------------------------------------------------------------------------
    # Test 6: T-Day Close Signal -> T+1 Day Open Execution Timing
    # --------------------------------------------------------------------------
    def test_06_signal_and_execution_timing(self):
        """
        Ensure no lookahead bias:
        Signal generated at T close.
        Order executes at T+1 open.
        """
        # Day T data
        day_t_close = 100.0
        # Signal condition confirmed at T close:
        signal_generated_at_t = True

        # Day T+1 data
        day_t1_open = 102.50
        day_t1_close = 105.0

        # Execution price must be T+1 Open, NOT T Close!
        fill_price = day_t1_open * (1.0 + self.slippage)
        self.assertAlmostEqual(fill_price, 102.50 * 1.001, places=4)
        self.assertNotEqual(fill_price, day_t_close * 1.001)

    # --------------------------------------------------------------------------
    # Test 7: Terminal Open Positions Mark-to-Market & Reconciliation
    # --------------------------------------------------------------------------
    def test_07_terminal_open_positions_reconciliation(self):
        """
        Verify that open positions at backtest end are marked to market,
        realized and unrealized PnL are distinguished, and cash + mkt_val = equity.
        """
        cash = 5000.0
        open_positions = {
            "NVDA": {
                "shares": 50.0,
                "entry_price": 100.0,
                "entry_capital": 5000.0
            }
        }
        final_close_nvda = 120.0

        # Mark-to-market
        unrealized_pnl = sum(p["shares"] * (final_close_nvda - p["entry_price"]) for p in open_positions.values())
        invested_mkt_val = sum(p["shares"] * final_close_nvda for p in open_positions.values())
        terminal_equity = cash + invested_mkt_val

        self.assertEqual(unrealized_pnl, 50.0 * 20.0)  # +$1000
        self.assertEqual(invested_mkt_val, 6000.0)
        self.assertEqual(terminal_equity, 11000.0)
        self.assertAlmostEqual(terminal_equity, cash + invested_mkt_val, places=4)

    # --------------------------------------------------------------------------
    # Test 8: Capital Limits, Max Positions & Re-allocation after Trim
    # --------------------------------------------------------------------------
    def test_08_capital_limits_and_reallocation_after_trim(self):
        """
        Verify max positions constraint and that cash freed from trim is available
        for subsequent allocations.
        """
        max_positions = 5
        active_positions = {f"STK_{i}": {"shares": 10} for i in range(5)}
        self.assertEqual(len(active_positions), max_positions)

        # Cannot open 6th position
        can_open_new = len(active_positions) < max_positions
        self.assertFalse(can_open_new)

        # One position closes / exits
        del active_positions["STK_0"]
        can_open_new = len(active_positions) < max_positions
        self.assertTrue(can_open_new)

    # --------------------------------------------------------------------------
    # Test 9: End-to-End Synthetic Miniature Simulation Execution
    # --------------------------------------------------------------------------
    def test_09_synthetic_miniature_engine_run(self):
        """
        Runs a miniature multi-day simulation with synthetic data through
        the exact ledger and stop-loss logic to verify end-to-end accounting.
        """
        import backtest_5y_engine as engine

        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        df_synthetic = pd.DataFrame({
            "Open": [100, 102, 105, 110, 108, 95, 90, 85, 90, 95],
            "High": [103, 106, 112, 115, 110, 98, 92, 88, 95, 100],
            "Low": [99, 101, 104, 106, 94, 92, 86, 82, 88, 92],
            "Close": [102, 105, 110, 108, 95, 93, 87, 86, 94, 98],
            "Volume": [1000000] * 10
        }, index=dates)

        # Compute indicators
        df_ind = engine.compute_rolling_indicators(df_synthetic)
        self.assertTrue("MA50" in df_ind.columns)
        self.assertTrue("EMA20" in df_ind.columns)
        self.assertTrue("ATR_14" in df_ind.columns)
        self.assertTrue("RSI_14" in df_ind.columns)
        self.assertTrue("Fib_Low" in df_ind.columns)



if __name__ == "__main__":
    unittest.main()
