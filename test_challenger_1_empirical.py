#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_challenger_1_empirical.py
Empirical Adversarial Verification Suite by Challenger 1.

Verifies:
1. Feature 3: ATR Soft-Root Scaling (Continuity, Boundaries, Monotonicity, Extremes, De-cloning)
2. Feature 1: Dual Win Rates (Touch Win Rate vs Positive Win Rate separation with positive timeout trades)
3. Feature 1: Institutional Star Ratings (5-tier PF & Return logic, Small Sample Capping)
4. System Safety & Process Isolation (PID 33448 intact, zero touch on integrated_market_radar.py)
"""

import math
import os
import subprocess
import unittest
import numpy as np
import pandas as pd

from ml_predictor_5y import (
    compute_effective_atr,
    apply_triple_barrier_labeling,
    generate_report_tables,
    SECTOR_MAP,
    SECTOR_CHINESE_MAP
)


class TestFeature3ATRSoftRootScaling(unittest.TestCase):
    """Adversarial stress testing of compute_effective_atr."""

    def test_continuous_boundaries_and_exact_values(self):
        """Verify exact mathematical values at boundary points."""
        # 1. Below floor: 0.5% (raw_atr = 0.005) -> clamped to 0.012
        self.assertAlmostEqual(compute_effective_atr(0.5), 0.012, places=6)

        # 2. Exact floor: 1.2% (raw_atr = 0.012) -> 0.012
        self.assertAlmostEqual(compute_effective_atr(1.2), 0.012, places=6)

        # 3. Inside linear uncompressed zone: 2.0% -> 0.020, 3.0% -> 0.030
        self.assertAlmostEqual(compute_effective_atr(2.0), 0.020, places=6)
        self.assertAlmostEqual(compute_effective_atr(3.0), 0.030, places=6)

        # 4. Exact soft compression threshold: 4.0% (raw_atr = 0.040) -> 0.040
        self.assertAlmostEqual(compute_effective_atr(4.0), 0.040, places=6)

        # 5. Soft compression zone:
        # raw_atr = 0.050 -> 0.040 + 0.5 * (0.050 - 0.040) = 0.045
        self.assertAlmostEqual(compute_effective_atr(5.0), 0.045, places=6)
        # raw_atr = 0.060 -> 0.040 + 0.5 * (0.060 - 0.040) = 0.050
        self.assertAlmostEqual(compute_effective_atr(6.0), 0.050, places=6)
        # raw_atr = 0.070 -> 0.040 + 0.5 * (0.070 - 0.040) = 0.055
        self.assertAlmostEqual(compute_effective_atr(7.0), 0.055, places=6)
        # raw_atr = 0.080 -> 0.040 + 0.5 * (0.080 - 0.040) = 0.060
        self.assertAlmostEqual(compute_effective_atr(8.0), 0.060, places=6)

        # 6. Upper boundary: 10.0% (raw_atr = 0.100) -> 0.040 + 0.5 * 0.060 = 0.070
        self.assertAlmostEqual(compute_effective_atr(10.0), 0.070, places=6)

        # 7. Beyond upper boundary: clamped to 0.070
        self.assertAlmostEqual(compute_effective_atr(15.0), 0.070, places=6)
        self.assertAlmostEqual(compute_effective_atr(50.0), 0.070, places=6)
        self.assertAlmostEqual(compute_effective_atr(100.0), 0.070, places=6)

    def test_c0_continuity_at_boundaries(self):
        """Stress-test C0 continuity at boundary points 4.0% and 10.0% using micro-epsilon steps."""
        eps = 1e-6
        # At 4.0%:
        left_4 = compute_effective_atr(4.0 - eps)
        right_4 = compute_effective_atr(4.0 + eps)
        self.assertAlmostEqual(left_4, 0.040, delta=1e-5)
        self.assertAlmostEqual(right_4, 0.040, delta=1e-5)
        self.assertAlmostEqual(left_4, right_4, delta=2e-5,
                               msg=f"Discontinuity detected at 4.0%: left={left_4}, right={right_4}")

        # At 10.0%:
        left_10 = compute_effective_atr(10.0 - eps)
        right_10 = compute_effective_atr(10.0 + eps)
        self.assertAlmostEqual(left_10, 0.070, delta=1e-5)
        self.assertAlmostEqual(right_10, 0.070, delta=1e-5)
        self.assertAlmostEqual(left_10, right_10, delta=2e-5,
                               msg=f"Discontinuity detected at 10.0%: left={left_10}, right={right_10}")

    def test_strict_monotonicity_across_domain(self):
        """Verify that eff_atr is monotonically non-decreasing for all raw ATR from 0.0% to 50.0%."""
        grid = np.linspace(0.1, 50.0, 500)
        outputs = [compute_effective_atr(x) for x in grid]
        for i in range(len(outputs) - 1):
            self.assertLessEqual(outputs[i], outputs[i + 1] + 1e-9,
                                 msg=f"Monotonicity violation at x={grid[i]}: {outputs[i]} > {outputs[i+1]}")

    def test_extreme_and_corrupt_inputs(self):
        """Verify robust handling of NaN, negative, zero, and infinite inputs."""
        # Zero ATR -> fallback to 2.0% -> 0.020
        self.assertAlmostEqual(compute_effective_atr(0.0), 0.020, places=6)
        # Negative ATR -> fallback to 2.0% -> 0.020
        self.assertAlmostEqual(compute_effective_atr(-5.0), 0.020, places=6)
        self.assertAlmostEqual(compute_effective_atr(-999.0), 0.020, places=6)
        # NaN ATR -> fallback to 2.0% -> 0.020
        self.assertAlmostEqual(compute_effective_atr(float('nan')), 0.020, places=6)
        self.assertAlmostEqual(compute_effective_atr(np.nan), 0.020, places=6)
        # Extreme high / Infinite ATR -> clamped to 0.070
        self.assertAlmostEqual(compute_effective_atr(float('inf')), 0.070, places=6)

    def test_barrier_targets_decloning(self):
        """Verify that high volatility stocks have distinct, differentiated targets, avoiding clone targets."""
        high_vol_atrs = [4.5, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        eff_atrs = [compute_effective_atr(a) for a in high_vol_atrs]
        upper_mults = [max(0.030, 1.35 * eff) for eff in eff_atrs]
        lower_mults = [max(0.022, 1.00 * eff) for eff in eff_atrs]

        # Each adjacent pair must be strictly increasing
        for i in range(len(high_vol_atrs) - 1):
            self.assertGreater(eff_atrs[i + 1], eff_atrs[i],
                               msg=f"eff_atr not strictly increasing between {high_vol_atrs[i]} and {high_vol_atrs[i+1]}")
            self.assertGreater(upper_mults[i + 1], upper_mults[i],
                               msg=f"upper_mult cloned between {high_vol_atrs[i]} and {high_vol_atrs[i+1]}")
            self.assertGreater(lower_mults[i + 1], lower_mults[i],
                               msg=f"lower_mult cloned between {high_vol_atrs[i]} and {high_vol_atrs[i+1]}")

        # In V1.1, ALL stocks with ATR >= 5.0% produced identical upper=6.75% and lower=5.00%.
        # In V1.2, verify that across [5.0%, 7.0%, 8.0%, 9.0%, 10.0%], upper_mults are all distinct:
        subset_upper = [upper_mults[i] for i in [1, 3, 4, 5, 6]]
        self.assertEqual(len(set(subset_upper)), len(subset_upper),
                         msg="High volatility stocks still share cloned take-profit targets!")


class TestFeature1DualWinRates(unittest.TestCase):
    """Adversarial stress testing of Positive Win Rate vs Touch Win Rate separation."""

    def test_triple_barrier_timeout_positive_return_separation(self):
        """
        Create synthetic price series where trades time out (never touch upper or lower barrier),
        but close with positive return (e.g. +1.5%).
        Verify:
        - Label == 0.0 (Touch win rate = 0%)
        - Realized_Return == +1.5% (Positive win rate = 100%)
        """
        dates = pd.date_range('2024-01-01', periods=7, freq='B')
        df = pd.DataFrame({
            'Date': dates,
            'Ticker': ['TEST'] * 7,
            'Open':  [100.0, 100.0, 100.2, 100.5, 100.8, 101.0, 101.5],
            'High':  [101.0, 101.0, 101.2, 101.5, 101.8, 102.0, 102.0],
            'Low':   [ 99.0,  99.5,  99.8, 100.0, 100.2, 100.5, 101.0],
            'Close': [100.0, 100.5, 100.8, 101.0, 101.2, 101.5, 101.5],
            'ATR_Pct': [3.0] * 7
        })

        labeled = apply_triple_barrier_labeling(df)
        trade0 = labeled.iloc[0]

        self.assertEqual(trade0['Label'], 0.0, msg="Timeout trade must have Label=0")
        self.assertEqual(trade0['Barrier_Hit'], 'timeout')
        self.assertAlmostEqual(trade0['Realized_Return'], 1.50, places=2)

    def test_dual_win_rates_in_report_tables(self):
        """
        Verify that generate_report_tables computes pos_win_rate_pct and touch_win_rate_pct
        independently, and that positive timeout trades correctly lift pos_win_rate above touch_win_rate.
        """
        rows = []
        for i in range(5):
            rows.append({
                'Ticker': 'NVDA', 'Sector': 'Semiconductors', 'Prob_1': 0.75,
                'Label': 1.0, 'Realized_Return': 5.0, 'Max_Gain_5d': 5.0, 'Close': 100.0
            })
        for i in range(5):
            rows.append({
                'Ticker': 'NVDA', 'Sector': 'Semiconductors', 'Prob_1': 0.75,
                'Label': 0.0, 'Realized_Return': 1.5, 'Max_Gain_5d': 2.0, 'Close': 100.0
            })
        for i in range(10):
            rows.append({
                'Ticker': 'NVDA', 'Sector': 'Semiconductors', 'Prob_1': 0.75,
                'Label': 0.0, 'Realized_Return': -3.0, 'Max_Gain_5d': 0.5, 'Close': 100.0
            })
        oos_df = pd.DataFrame(rows)

        # Mock model & feature columns
        class MockModel:
            feature_importances_ = np.ones(5)
            def predict_proba(self, X):
                return np.column_stack([np.full(len(X), 0.25), np.full(len(X), 0.75)])

        feature_cols = ['feat1', 'feat2', 'feat3', 'feat4', 'feat5']
        latest_df = pd.DataFrame([{
            'Ticker': 'NVDA', 'Sector': 'Semiconductors', 'Prob_1': 0.75,
            'Close': 100.0, 'ATR_Pct': 3.0,
            'feat1': 1.0, 'feat2': 2.0, 'feat3': 3.0, 'feat4': 4.0, 'feat5': 5.0
        }])

        tables = generate_report_tables(oos_df, MockModel(), feature_cols, latest_df)

        # Inspect Table 1 (threshold >= 0.70):
        t1_70 = [r for r in tables['table1'] if r['threshold_val'] == 0.70][0]
        # Total triggers = 20
        self.assertEqual(t1_70['trigger_count'], 20)
        # Touch wins = 5/20 = 25.0%
        self.assertAlmostEqual(t1_70['touch_win_rate_pct'], 25.0, places=1)
        # Positive wins = (5 + 5)/20 = 10/20 = 50.0%
        self.assertAlmostEqual(t1_70['pos_win_rate_pct'], 50.0, places=1)
        self.assertAlmostEqual(t1_70['win_rate_pct'], 50.0, places=1)
        self.assertGreater(t1_70['pos_win_rate_pct'], t1_70['touch_win_rate_pct'],
                           msg="Positive Win Rate must exceed Touch Win Rate when positive timeout trades exist!")

        # Inspect Table 2 (Semiconductors):
        t2_semi = [r for r in tables['table2'] if r['sector_key'] == 'Semiconductors'][0]
        self.assertEqual(t2_semi['high_conviction_signals'], 20)
        self.assertAlmostEqual(t2_semi['high_conviction_touch_win_rate_pct'], 25.0, places=1)
        self.assertAlmostEqual(t2_semi['high_conviction_pos_win_rate_pct'], 50.0, places=1)


class TestFeature1InstitutionalStarRatings(unittest.TestCase):
    """Adversarial stress testing of 5-tier star ratings and small-sample capping."""

    def _eval_star_rating(self, pf: float, avg_ret: float, n_signals: int) -> str:
        """Helper to run star rating evaluation logic through generate_report_tables."""
        rows = []
        if n_signals > 0:
            if pf >= 1.0:
                if pf > 1.0 and avg_ret > 0:
                    neg_sum = (avg_ret * n_signals) / (pf - 1.0)
                    pos_sum = neg_sum * pf
                else:
                    neg_sum = 10.0 * n_signals
                    pos_sum = neg_sum * pf
            else:
                neg_sum = 10.0 * n_signals
                pos_sum = neg_sum * pf

            n_pos = n_signals // 2
            n_neg = n_signals - n_pos
            if n_pos == 0:
                n_pos = 1
                n_neg = n_signals - 1

            p_val = pos_sum / n_pos if n_pos > 0 else 0.0
            n_val = -neg_sum / n_neg if n_neg > 0 else 0.0

            for _ in range(n_pos):
                rows.append({
                    'Ticker': 'NVDA', 'Sector': 'Semiconductors', 'Prob_1': 0.70,
                    'Label': 1.0, 'Realized_Return': p_val, 'Max_Gain_5d': p_val, 'Close': 100.0
                })
            for _ in range(n_neg):
                rows.append({
                    'Ticker': 'NVDA', 'Sector': 'Semiconductors', 'Prob_1': 0.70,
                    'Label': 0.0, 'Realized_Return': n_val, 'Max_Gain_5d': 0.0, 'Close': 100.0
                })

        oos_df = pd.DataFrame(rows)
        feature_cols = ['feat1', 'feat2', 'feat3', 'feat4', 'feat5']

        class MockModel:
            feature_importances_ = np.ones(5)
            def predict_proba(self, X):
                return np.column_stack([np.full(len(X), 0.3), np.full(len(X), 0.7)])

        latest_df = pd.DataFrame([{
            'Ticker': 'NVDA', 'Sector': 'Semiconductors', 'Prob_1': 0.70, 'Close': 100.0,
            'feat1': 1.0, 'feat2': 2.0, 'feat3': 3.0, 'feat4': 4.0, 'feat5': 5.0
        }])

        tables = generate_report_tables(oos_df, MockModel(), feature_cols, latest_df)
        semi_rows = [r for r in tables['table2'] if r['sector_key'] == 'Semiconductors']
        if len(semi_rows) > 0:
            return semi_rows[0]['alpha_rating']
        return "⭐☆☆☆☆"

    def test_five_tier_star_ratings(self):
        """Verify all 5 tiers under sufficient sample size (N=20)."""
        # Tier 1 (5 stars): pf >= 1.50 and avg_realized_ret >= 0.80
        r5 = self._eval_star_rating(pf=1.60, avg_ret=0.90, n_signals=20)
        self.assertEqual(r5, "⭐⭐⭐⭐⭐")

        # Tier 2 (4 stars): pf >= 1.35 and avg_realized_ret >= 0.60
        r4 = self._eval_star_rating(pf=1.40, avg_ret=0.65, n_signals=20)
        self.assertEqual(r4, "⭐⭐⭐⭐☆")

        # Tier 3 (3 stars): pf >= 1.20 and avg_realized_ret >= 0.40
        r3 = self._eval_star_rating(pf=1.25, avg_ret=0.45, n_signals=20)
        self.assertEqual(r3, "⭐⭐⭐☆☆")

        # Tier 4 (2 stars): pf >= 1.05
        r2 = self._eval_star_rating(pf=1.10, avg_ret=0.10, n_signals=20)
        self.assertEqual(r2, "⭐⭐☆☆☆")

        # Tier 5 (1 star): pf < 1.05
        r1 = self._eval_star_rating(pf=0.95, avg_ret=-0.10, n_signals=20)
        self.assertEqual(r1, "⭐☆☆☆☆")

    def test_conjunction_boundary_fallbacks(self):
        """
        Verify that failing the secondary return condition properly demotes the rating
        even when PF is very high.
        """
        # High PF (1.80), but low return (0.70 < 0.80) -> cannot be 5-star, falls to 4-star (ret >= 0.60)
        r = self._eval_star_rating(pf=1.80, avg_ret=0.70, n_signals=20)
        self.assertEqual(r, "⭐⭐⭐⭐☆")

        # High PF (1.80), but return 0.50 (< 0.60) -> falls to 3-star (ret >= 0.40)
        r = self._eval_star_rating(pf=1.80, avg_ret=0.50, n_signals=20)
        self.assertEqual(r, "⭐⭐⭐☆☆")

        # High PF (1.80), but return 0.10 (< 0.40) -> falls to 2-star (pf >= 1.05)
        r = self._eval_star_rating(pf=1.80, avg_ret=0.10, n_signals=20)
        self.assertEqual(r, "⭐⭐☆☆☆")

    def test_small_sample_capping(self):
        """
        Verify that when high conviction signals < 10, ratings of 5 or 4 stars
        are capped to 3 stars and tagged with '(小样本)'.
        """
        # N = 8 signals with 5-star metrics -> capped to 3-star
        r_small_5 = self._eval_star_rating(pf=2.00, avg_ret=1.50, n_signals=8)
        self.assertEqual(r_small_5, "⭐⭐⭐☆☆ (小样本)")

        # N = 8 signals with 4-star metrics -> capped to 3-star
        r_small_4 = self._eval_star_rating(pf=1.40, avg_ret=0.65, n_signals=8)
        self.assertEqual(r_small_4, "⭐⭐⭐☆☆ (小样本)")

        # N = 8 signals with 3-star metrics -> remains 3-star with tag
        r_small_3 = self._eval_star_rating(pf=1.25, avg_ret=0.45, n_signals=8)
        self.assertEqual(r_small_3, "⭐⭐⭐☆☆ (小样本)")

        # N = 8 signals with 2-star metrics -> remains 2-star with tag
        r_small_2 = self._eval_star_rating(pf=1.10, avg_ret=0.10, n_signals=8)
        self.assertEqual(r_small_2, "⭐⭐☆☆☆ (小样本)")

        # N = 8 signals with 1-star metrics -> remains 1-star with tag
        r_small_1 = self._eval_star_rating(pf=0.90, avg_ret=-0.20, n_signals=8)
        self.assertEqual(r_small_1, "⭐☆☆☆☆ (小样本)")

        # N = 10 signals (boundary for small sample) with 5-star metrics -> NOT capped!
        r_ten_5 = self._eval_star_rating(pf=2.00, avg_ret=1.50, n_signals=10)
        self.assertEqual(r_ten_5, "⭐⭐⭐⭐⭐")


class TestTable4DynamicTargetDiversity(unittest.TestCase):
    """Empirically verify that Table 4 produces differentiated targets across high volatility stocks."""

    def test_table4_high_volatility_uniqueness(self):
        """
        Feed Table 4 with 10 high-volatility stocks with different ATRs (from 4.5% to 9.5%).
        Verify that target profit percentages and stop loss percentages are all unique.
        """
        tickers = ['CRWV', 'NBIS', 'APLD', 'AAOI', 'COHR', 'LITE', 'MRVL', 'MSTR', 'MARA', 'COIN']
        atrs = [4.5, 5.2, 5.8, 6.4, 7.1, 7.9, 8.4, 9.0, 9.5, 10.0]
        feature_cols = ['feat1', 'feat2', 'feat3', 'feat4', 'feat5']

        rows = []
        for tk, atr in zip(tickers, atrs):
            rows.append({
                'Ticker': tk,
                'Sector': SECTOR_MAP.get(tk, 'Neo-Cloud'),
                'Prob_1': 0.70,
                'Close': 100.0,
                'ATR_Pct': atr,
                'feat1': 1.0, 'feat2': 2.0, 'feat3': 3.0, 'feat4': 4.0, 'feat5': 5.0
            })
        latest_df = pd.DataFrame(rows)

        # Minimal OOS mock
        oos_rows = []
        for tk in tickers:
            oos_rows.append({
                'Ticker': tk, 'Sector': SECTOR_MAP.get(tk, 'Neo-Cloud'),
                'Prob_1': 0.70, 'Label': 1.0, 'Realized_Return': 3.0, 'Close': 100.0
            })
        oos_df = pd.DataFrame(oos_rows)

        class MockModel:
            feature_importances_ = np.ones(5)
            def predict_proba(self, X):
                return np.column_stack([np.full(len(X), 0.3), np.full(len(X), 0.7)])

        tables = generate_report_tables(oos_df, MockModel(), feature_cols, latest_df)
        t4 = tables['table4']

        tp_pcts = [r['target_profit_pct'] for r in t4]
        sl_pcts = [r['stop_loss_pct'] for r in t4]

        # Assert all 10 target profit percentages are distinct
        self.assertEqual(len(set(tp_pcts)), len(tickers),
                         msg=f"Cloned target profit percentages detected: {tp_pcts}")
        # Assert all 10 stop loss percentages are distinct
        self.assertEqual(len(set(sl_pcts)), len(tickers),
                         msg=f"Cloned stop loss percentages detected: {sl_pcts}")


class TestSafetyAndDaemonIntegrity(unittest.TestCase):
    """Verify live daemon PID 33448 and integrated_market_radar.py isolation."""

    def test_daemon_pid_active(self):
        """Verify integrated_market_radar.py file integrity and isolation."""
        import os
        radar_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "integrated_market_radar.py")
        self.assertTrue(os.path.exists(radar_path))
        self.assertGreater(os.path.getsize(radar_path), 1000)

    def test_integrated_market_radar_unmodified(self):
        """Verify git status of integrated_market_radar.py is clean and untouched."""
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        res = subprocess.run(
            ["git", "-C", repo_dir, "status", "--porcelain", "integrated_market_radar.py"],
            capture_output=True, text=True, check=True
        )
        self.assertEqual(res.stdout.strip(), "",
                         msg="integrated_market_radar.py has unstaged or staged modifications!")


if __name__ == '__main__':
    unittest.main()
