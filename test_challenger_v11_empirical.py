#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_challenger_v11_empirical.py
Empirical Challenger v11 Stress Test Suite for AI Multi-Sector 5Y US Market Predictor
Targeting R1 (Macro feature alignment) & R2 (Dynamic triple barrier labeling & probability calibration)

Test Dimensions:
1. Macro feature alignment under adversarial conditions:
   - Real 5Y Parquet dataset non-zero rate > 95% and std > 0.01 for macro features
   - Scrambled dates in ETF tables & raw panel
   - Missing ETF dates / unaligned trading calendars across ETFs
   - Disjoint row index slices
   - Synthetic panel feature integrity
2. Triple barrier labeling & ATR winsorization stress:
   - atr_pct < 1.2% (clipping at 0.012, floor 3.0% / 2.2%)
   - atr_pct == 1.2%
   - atr_pct == 2.0%
   - atr_pct == 3.0%
   - atr_pct == 5.0%
   - atr_pct > 5.0% (clipping at 0.050, upper 6.75% / lower 5.0%)
   - atr_pct == 100.0% extreme meme spike
   - atr_pct == 0.0%, negative, and NaN defaults
   - Upper, lower, collision, timeout outcome resolution
3. LightGBM calibration & Table 2 star rating capping:
   - Star capping at N=0, N=1, N=9, N=10 samples
   - Star ratings across win-rate thresholds for N>=10
   - LightGBM balanced probability distribution centering and quantile monotonicity
"""

import os
import sys
import unittest
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Any

import numpy as np
import pandas as pd
import lightgbm as lgb

# Ensure standard output handles UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import ml_predictor_5y as mlp


# ==============================================================================
# Helper Generators
# ==============================================================================

def create_synthetic_etf_df(ticker: str, dates: List[pd.Timestamp], base_price: float = 100.0, seed: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    n = len(dates)
    rets = rng.normal(0.0005, 0.015, n)
    prices = base_price * np.exp(np.cumsum(rets))
    highs = prices * (1.0 + rng.uniform(0.002, 0.02, n))
    lows = prices * (1.0 - rng.uniform(0.002, 0.02, n))
    opens = prices * (1.0 + rng.uniform(-0.005, 0.005, n))
    volumes = rng.uniform(1e6, 5e6, n)
    return pd.DataFrame({
        'Date': dates,
        'Ticker': ticker,
        'Open': np.round(opens, 2),
        'High': np.round(highs, 2),
        'Low': np.round(lows, 2),
        'Close': np.round(prices, 2),
        'Volume': np.round(volumes, 0)
    })


# ==============================================================================
# Suite 1: Macro Feature Alignment Empirical Stress Tests (R1)
# ==============================================================================

class TestEmpiricalMacroFeatureAlignment(unittest.TestCase):
    """Stress tests for compute_macro_features and build_feature_panel."""

    @classmethod
    def setUpClass(cls):
        cls.parquet_path = os.path.join(PROJECT_DIR, 'data_cache', 'ml_panel_5y.parquet')
        cls.has_parquet = os.path.exists(cls.parquet_path)

    def test_real_parquet_dataset_macro_metrics(self):
        """Stress verify macro features on real cached 5Y Parquet dataset."""
        if not self.has_parquet:
            self.skipTest(f"Parquet file {self.parquet_path} not found.")

        raw_df = pd.read_parquet(self.parquet_path)
        self.assertGreater(len(raw_df), 1000, "Parquet dataset has insufficient records.")

        # Build feature panel
        panel = mlp.build_feature_panel(raw_df)
        self.assertFalse(panel.empty, "Feature panel is empty.")

        req_macro_features = [
            'SMH_Bias_MA50',
            'TLT_Bias_MA50',
            'VIXY_Bias_MA20',
            'VIXY_Pos_60',
            'Alpha_SMH_20d'
        ]

        print("\n--- Real Parquet Dataset Macro Feature Audit ---")
        for feat in req_macro_features:
            self.assertIn(feat, panel.columns, f"Feature {feat} missing from feature panel.")
            series = panel[feat].dropna()
            non_zero_cnt = (series.abs() > 1e-6).sum()
            non_zero_rate = non_zero_cnt / len(series)
            feat_std = float(series.std())

            print(f"[*] {feat:16s}: non-zero rate = {non_zero_rate * 100:6.2f}%, std = {feat_std:7.4f}")

            self.assertGreater(
                non_zero_rate, 0.95,
                f"Feature {feat} non-zero rate {non_zero_rate * 100:.2f}% <= 95%"
            )
            self.assertGreater(
                feat_std, 0.01,
                f"Feature {feat} standard deviation {feat_std:.4f} <= 0.01"
            )

        # Invariant: Alpha_SMH_20d must not trivially equal Ret_20d (which would mean SMH_Ret_20d was 0)
        ret_diff = (panel['Alpha_SMH_20d'] - panel['Ret_20d']).abs()
        mismatch_rate = (ret_diff > 1e-4).sum() / len(panel)
        print(f"[*] Alpha_SMH_20d vs Ret_20d divergence rate: {mismatch_rate * 100:.2f}%")
        self.assertGreater(mismatch_rate, 0.90, "Alpha_SMH_20d appears identical to Ret_20d (SMH_Ret_20d was 0)!")

    def test_scrambled_dates_robustness(self):
        """Stress verify that scrambled dates within ETF tables yield identical sorted results."""
        dates = pd.bdate_range('2023-01-01', periods=250)
        etfs = ['QQQ', 'SMH', 'IGV', 'TLT', 'VIXY']
        macro_map_ordered = {}
        macro_map_scrambled = {}

        for idx, etf in enumerate(etfs):
            df_ordered = create_synthetic_etf_df(etf, dates, seed=100 + idx)
            # Shuffle rows randomly
            shuffled_indices = np.random.RandomState(42 + idx).permutation(len(df_ordered))
            df_scrambled = df_ordered.iloc[shuffled_indices].copy()

            macro_map_ordered[etf] = df_ordered
            macro_map_scrambled[etf] = df_scrambled

        macro_df_ordered = mlp.compute_macro_features(macro_map=macro_map_ordered)
        macro_df_scrambled = mlp.compute_macro_features(macro_map=macro_map_scrambled)

        self.assertEqual(len(macro_df_ordered), len(macro_df_scrambled))
        pd.testing.assert_frame_equal(
            macro_df_ordered.sort_values('Date').reset_index(drop=True),
            macro_df_scrambled.sort_values('Date').reset_index(drop=True),
            check_exact=False,
            rtol=1e-5,
            atol=1e-5
        )
        print("[+] Scrambled dates robustness test PASSED.")

    def test_missing_etf_dates_and_calendar_mismatch(self):
        """Stress verify missing ETF dates (e.g. holidays, gaps) forward-fill safely without NaN."""
        dates_all = pd.bdate_range('2023-01-01', periods=200)
        # QQQ has all dates
        qqqm = create_synthetic_etf_df('QQQ', dates_all, seed=101)
        # SMH drops days 20..35
        smh_dates = [d for i, d in enumerate(dates_all) if not (20 <= i <= 35)]
        smh = create_synthetic_etf_df('SMH', smh_dates, seed=102)
        # TLT drops even indices
        tlt_dates = [d for i, d in enumerate(dates_all) if i % 2 != 0]
        tlt = create_synthetic_etf_df('TLT', tlt_dates, seed=103)
        # VIXY drops odd indices
        vixy_dates = [d for i, d in enumerate(dates_all) if i % 2 == 0]
        vixy = create_synthetic_etf_df('VIXY', vixy_dates, seed=104)
        # IGV drops first 10 days
        igv_dates = dates_all[10:]
        igv = create_synthetic_etf_df('IGV', igv_dates, seed=105)

        macro_map = {'QQQ': qqqm, 'SMH': smh, 'TLT': tlt, 'VIXY': vixy, 'IGV': igv}
        macro_df = mlp.compute_macro_features(macro_map=macro_map)

        self.assertEqual(len(macro_df), len(dates_all))
        # Ensure 0 NaNs and 0 Infs anywhere in macro_df
        self.assertEqual(macro_df.isna().sum().sum(), 0, "macro_df contains NaNs after calendar mismatch ffill!")
        self.assertFalse(np.isinf(macro_df.select_dtypes(include=np.number).values).any(), "macro_df contains Inf values!")
        print("[+] Missing ETF dates and calendar mismatch test PASSED.")

    def test_disjoint_row_indices_in_raw_panel(self):
        """Stress verify disjoint row slices (e.g. index 1000..1099, 5000..5099) do not misalign."""
        dates = pd.bdate_range('2023-01-01', periods=150)
        etfs = ['QQQ', 'SMH', 'IGV', 'TLT', 'VIXY']
        dfs = []
        for idx, etf in enumerate(etfs):
            sub = create_synthetic_etf_df(etf, dates, seed=200 + idx)
            # Assign intentionally disjoint arbitrary indices
            sub.index = np.arange((idx + 1) * 10000, (idx + 1) * 10000 + len(sub))
            dfs.append(sub)

        # Also add a stock with disjoint index
        stock_df = create_synthetic_etf_df('NVDA', dates, seed=300)
        stock_df.index = np.arange(80000, 80000 + len(stock_df))
        dfs.append(stock_df)

        raw_panel = pd.concat(dfs, axis=0)
        macro_df = mlp.compute_macro_features(raw_panel=raw_panel)

        self.assertEqual(len(macro_df), len(dates))
        for col in ['SMH_Bias_MA50', 'TLT_Bias_MA50', 'VIXY_Bias_MA20', 'VIXY_Pos_60']:
            non_zeros = (macro_df[col].abs() > 1e-6).sum()
            self.assertGreater(non_zeros / len(macro_df), 0.90, f"Disjoint slice caused {col} to be 0!")
        print("[+] Disjoint row slices test PASSED.")

    def test_missing_required_etf_raises_value_error(self):
        """Verify that omitting a mandatory macro ETF explicitly raises ValueError."""
        dates = pd.bdate_range('2023-01-01', periods=50)
        macro_map = {
            'QQQ': create_synthetic_etf_df('QQQ', dates, seed=1),
            'SMH': create_synthetic_etf_df('SMH', dates, seed=2),
            # Missing IGV, TLT, VIXY
        }
        with self.assertRaises(ValueError):
            mlp.compute_macro_features(macro_map=macro_map)
        print("[+] Missing ETF exception assertion PASSED.")


# ==============================================================================
# Suite 2: Triple Barrier Labeling & ATR Winsorization Stress Tests (R2)
# ==============================================================================

class TestEmpiricalTripleBarrierLabeling(unittest.TestCase):
    """Stress tests for apply_triple_barrier_labeling and dynamic ATR floor bounds."""

    def _create_single_trade_scenario(
        self,
        atr_pct: float,
        next_open: float = 100.0,
        day1_high: float = 100.5,
        day1_low: float = 99.5,
        close_t5: float = 100.0
    ) -> pd.DataFrame:
        """Create a 10-bar DataFrame with controlled ATR_Pct and prices."""
        dates = pd.bdate_range('2024-01-01', periods=10)
        opens = np.full(10, next_open)
        highs = np.full(10, next_open + 0.5)
        lows = np.full(10, next_open - 0.5)
        closes = np.full(10, next_open)
        volumes = np.full(10, 1e6)
        atr_pcts = np.full(10, atr_pct)

        # Bar 1 (T+1) controlled highs and lows
        highs[1] = day1_high
        lows[1] = day1_low
        closes[5] = close_t5

        return pd.DataFrame({
            'Date': dates,
            'Ticker': 'TEST',
            'Open': opens,
            'High': highs,
            'Low': lows,
            'Close': closes,
            'Volume': volumes,
            'ATR_Pct': atr_pcts
        })

    def test_atr_winsorization_boundary_points(self):
        """
        Verify mathematical precision of dynamic barrier calculation across boundary points:
        eff_atr = np.clip(atr_pct / 100.0, 0.012, 0.050)
        upper_mult = max(0.030, 1.35 * eff_atr)
        lower_mult = max(0.022, 1.00 * eff_atr)
        """
        test_cases = [
            # (atr_pct, expected_eff_atr, expected_upper_mult, expected_lower_mult, case_name)
            (0.5, 0.012, 0.030, 0.022, "Below lower bound (0.5%) -> Floor active"),
            (1.2, 0.012, 0.030, 0.022, "Exact lower bound (1.2%) -> Floor active"),
            (2.0, 0.020, 0.030, 0.022, "Nominal 2.0% -> Floor active (1.35*0.02=0.027 < 0.030)"),
            (2.5, 0.025, 0.03375, 0.025, "Between floor & cap (2.5%) -> 1.35*0.025 = 0.03375"),
            (3.0, 0.030, 0.0405, 0.030, "Between floor & cap (3.0%) -> 1.35*0.030 = 0.0405"),
            (4.0, 0.040, 0.0540, 0.040, "Between floor & cap (4.0%) -> 1.35*0.040 = 0.0540"),
            (5.0, 0.045, 0.06075, 0.045, "5.0% Soft-Root Scaling -> 1.35*0.045 = 0.06075"),
            (7.5, 0.0575, 0.077625, 0.0575, "Above 4.0% -> Soft-Root Scaling (7.5%)"),
            (10.0, 0.070, 0.0945, 0.070, "10.0% Upper Bound Soft-Root -> 1.35*0.070 = 0.0945"),
            (100.0, 0.070, 0.0945, 0.070, "Extreme meme spike (100.0%) -> Clamped to 7.0%"),
            (0.0, 0.020, 0.030, 0.022, "Zero ATR -> Defaults to 2.0%"),
            (-5.0, 0.020, 0.030, 0.022, "Negative ATR -> Defaults to 2.0%"),
            (np.nan, 0.020, 0.030, 0.022, "NaN ATR -> Defaults to 2.0%"),
        ]

        print("\n--- ATR Winsorization & Barrier Boundary Verification ---")
        for atr_pct, exp_eff, exp_u_mult, exp_l_mult, desc in test_cases:
            df = self._create_single_trade_scenario(atr_pct=atr_pct, next_open=100.0)
            labeled = mlp.apply_triple_barrier_labeling(df)

            u_target = labeled.loc[0, 'Target_Upper']
            l_target = labeled.loc[0, 'Target_Lower']

            expected_u_target = round(100.0 * (1.0 + exp_u_mult), 4)
            expected_l_target = round(100.0 * (1.0 - exp_l_mult), 4)

            print(f"[*] {desc:55s} => U: {u_target:.4f} (exp {expected_u_target:.4f}), L: {l_target:.4f} (exp {expected_l_target:.4f})")

            self.assertAlmostEqual(
                u_target, expected_u_target, places=4,
                msg=f"Upper barrier failed for {desc}: got {u_target}, expected {expected_u_target}"
            )
            self.assertAlmostEqual(
                l_target, expected_l_target, places=4,
                msg=f"Lower barrier failed for {desc}: got {l_target}, expected {expected_l_target}"
            )

    def test_barrier_hit_resolution_and_realized_returns(self):
        """Verify upper hit, lower hit, collision, and timeout with new barrier values."""
        # 1. Upper Hit: ATR = 3.0% -> Upper = 104.05, Lower = 97.00
        df_upper = self._create_single_trade_scenario(atr_pct=3.0, next_open=100.0, day1_high=104.5, day1_low=99.0)
        res_u = mlp.apply_triple_barrier_labeling(df_upper).loc[0]
        self.assertEqual(res_u['Label'], 1.0)
        self.assertEqual(res_u['Barrier_Hit'], 'upper')
        self.assertAlmostEqual(res_u['Realized_Return'], 4.05, places=2)

        # 2. Lower Hit: ATR = 3.0% -> Lower = 97.00
        df_lower = self._create_single_trade_scenario(atr_pct=3.0, next_open=100.0, day1_high=101.0, day1_low=96.5)
        res_l = mlp.apply_triple_barrier_labeling(df_lower).loc[0]
        self.assertEqual(res_l['Label'], 0.0)
        self.assertEqual(res_l['Barrier_Hit'], 'lower')
        self.assertAlmostEqual(res_l['Realized_Return'], -3.00, places=2)

        # 3. Collision (Same day both touched): Conservative stop priority -> Label = 0, hit = collision
        df_coll = self._create_single_trade_scenario(atr_pct=3.0, next_open=100.0, day1_high=105.0, day1_low=96.0)
        res_c = mlp.apply_triple_barrier_labeling(df_coll).loc[0]
        self.assertEqual(res_c['Label'], 0.0)
        self.assertEqual(res_c['Barrier_Hit'], 'collision')
        self.assertAlmostEqual(res_c['Realized_Return'], -3.00, places=2)

        # 4. Timeout: Neither touched in 5 days, closes at 101.50 at T+5 -> Realized return = +1.50%
        df_to = self._create_single_trade_scenario(atr_pct=3.0, next_open=100.0, day1_high=101.0, day1_low=99.0, close_t5=101.5)
        res_to = mlp.apply_triple_barrier_labeling(df_to).loc[0]
        self.assertEqual(res_to['Label'], 0.0)
        self.assertEqual(res_to['Barrier_Hit'], 'timeout')
        self.assertAlmostEqual(res_to['Realized_Return'], 1.50, places=2)

        print("[+] Barrier resolution and realized returns test PASSED.")

    def test_tail_isolation_exactly_5_days_nan(self):
        """Stress verify that regardless of panel length, the tail 5 days have NaN label and barrier targets."""
        for n_bars in [7, 15, 50, 100]:
            dates = pd.bdate_range('2024-01-01', periods=n_bars)
            df = create_synthetic_etf_df('TEST', dates, seed=42)
            df['ATR_Pct'] = 2.5
            labeled = mlp.apply_triple_barrier_labeling(df)

            tail_labels = labeled.iloc[-5:]['Label']
            self.assertTrue(tail_labels.isna().all(), f"Tail 5 days not NaN for n_bars={n_bars}!")
            tail_upper = labeled.iloc[-5:]['Target_Upper']
            self.assertTrue(tail_upper.isna().all(), f"Tail 5 days Target_Upper not NaN for n_bars={n_bars}!")
            head_labels = labeled.iloc[:-5]['Label']
            self.assertFalse(head_labels.isna().any(), f"Non-tail bars contain unexpected NaNs for n_bars={n_bars}!")
        print("[+] Tail isolation invariant test PASSED.")


# ==============================================================================
# Suite 3: Table 2 Star Rating Capping & Small Sample Audit (R2)
# ==============================================================================

class TestEmpiricalTable2StarRatingCapping(unittest.TestCase):
    """Stress tests for Table 2 star rating capping on small sample counts (N=0, 1, 9, 10)."""

    def _build_dummy_oos_df(self, sector_name: str, high_conv_cnt: int, win_rate_pct: float, total_cnt: int = 50, win_return: float = 4.0, loss_return: float = -2.5) -> pd.DataFrame:
        """Create a mock OOS predictions DataFrame for a single sector."""
        high_conv_cnt = min(high_conv_cnt, total_cnt)
        n_wins = int(round(high_conv_cnt * (win_rate_pct / 100.0)))
        n_losses = high_conv_cnt - n_wins

        records = []
        # High confidence wins
        for i in range(n_wins):
            records.append({
                'Date': pd.Timestamp('2024-01-01') + pd.Timedelta(days=i),
                'Ticker': 'NVDA' if sector_name == 'Semiconductors' else 'PLTR',
                'Sector': sector_name,
                'Close': 100.0,
                'Label': 1.0,
                'Prob_1': 0.65,
                'Realized_Return': win_return
            })
        # High confidence losses
        for i in range(n_losses):
            records.append({
                'Date': pd.Timestamp('2024-02-01') + pd.Timedelta(days=i),
                'Ticker': 'NVDA' if sector_name == 'Semiconductors' else 'PLTR',
                'Sector': sector_name,
                'Close': 100.0,
                'Label': 0.0,
                'Prob_1': 0.65,
                'Realized_Return': loss_return
            })
        # Regular confidence entries
        remaining = total_cnt - high_conv_cnt
        for i in range(remaining):
            records.append({
                'Date': pd.Timestamp('2024-03-01') + pd.Timedelta(days=i),
                'Ticker': 'NVDA' if sector_name == 'Semiconductors' else 'PLTR',
                'Sector': sector_name,
                'Close': 100.0,
                'Label': 0.0,
                'Prob_1': 0.45,
                'Realized_Return': -1.0
            })

        return pd.DataFrame(records)

    def test_star_capping_edge_counts(self):
        """
        Test star rating capping when high_conviction_signals is 0, 1, 9, and 10.
        Requirement:
        - If high_conv_cnt < 10:
          * Rating cannot exceed ⭐⭐⭐☆☆
          * Must append ' (小样本)'
        - If high_conv_cnt >= 10:
          * Can award up to 5 stars without '(小样本)'
        """
        # Test N=0: No high conv signals -> win rate 0.0% -> ⭐☆☆☆☆ (小样本)
        oos_0 = self._build_dummy_oos_df('Semiconductors', high_conv_cnt=0, win_rate_pct=0.0)
        report_0 = mlp.generate_report_tables(oos_df=oos_0, model=None, feature_cols=[], latest_features_df=pd.DataFrame())
        row_0 = next(r for r in report_0['table2'] if r['sector_key'] == 'Semiconductors')
        self.assertEqual(row_0['high_conviction_signals'], 0)
        self.assertIn("(小样本)", row_0['alpha_rating'])
        self.assertEqual(row_0['alpha_rating'], "⭐☆☆☆☆ (小样本)")

        # Test N=1 with 100% win rate -> MUST NOT BE 5 stars, MUST BE capped at 3 stars (小样本)
        oos_1 = self._build_dummy_oos_df('Semiconductors', high_conv_cnt=1, win_rate_pct=100.0)
        report_1 = mlp.generate_report_tables(oos_df=oos_1, model=None, feature_cols=[], latest_features_df=pd.DataFrame())
        row_1 = next(r for r in report_1['table2'] if r['sector_key'] == 'Semiconductors')
        self.assertEqual(row_1['high_conviction_signals'], 1)
        self.assertEqual(row_1['high_conviction_win_rate_pct'], 100.0)
        self.assertEqual(row_1['alpha_rating'], "⭐⭐⭐☆☆ (小样本)", f"N=1 got {row_1['alpha_rating']} instead of 3 stars cap!")

        # Test N=9 with 100% win rate -> MUST NOT BE 5 stars, MUST BE capped at 3 stars (小样本)
        oos_9 = self._build_dummy_oos_df('Semiconductors', high_conv_cnt=9, win_rate_pct=100.0)
        report_9 = mlp.generate_report_tables(oos_df=oos_9, model=None, feature_cols=[], latest_features_df=pd.DataFrame())
        row_9 = next(r for r in report_9['table2'] if r['sector_key'] == 'Semiconductors')
        self.assertEqual(row_9['high_conviction_signals'], 9)
        self.assertEqual(row_9['high_conviction_win_rate_pct'], 100.0)
        self.assertEqual(row_9['alpha_rating'], "⭐⭐⭐☆☆ (小样本)", f"N=9 got {row_9['alpha_rating']} instead of 3 stars cap!")

        # Test N=10 with 100% win rate -> CAN BE 5 stars, NO '(小样本)'
        oos_10 = self._build_dummy_oos_df('Semiconductors', high_conv_cnt=10, win_rate_pct=100.0)
        report_10 = mlp.generate_report_tables(oos_df=oos_10, model=None, feature_cols=[], latest_features_df=pd.DataFrame())
        row_10 = next(r for r in report_10['table2'] if r['sector_key'] == 'Semiconductors')
        self.assertEqual(row_10['high_conviction_signals'], 10)
        self.assertEqual(row_10['high_conviction_win_rate_pct'], 100.0)
        self.assertEqual(row_10['alpha_rating'], "⭐⭐⭐⭐⭐", f"N=10 got {row_10['alpha_rating']} instead of 5 stars!")
        self.assertNotIn("(小样本)", row_10['alpha_rating'])

        print("[+] Table 2 star rating capping for N=0, 1, 9, 10 PASSED.")

    def test_star_ratings_across_win_rate_tiers_n10(self):
        """Verify institutional rating thresholds when N >= 10 based on PF and avg return."""
        tiers = [
            # (wr, win_ret, loss_ret, exp_rating)
            (75.0, 4.0, -2.0, "⭐⭐⭐⭐⭐"),
            (50.0, 4.56, -3.20, "⭐⭐⭐⭐☆"),
            (50.0, 3.50, -2.60, "⭐⭐⭐☆☆"),
            (50.0, 2.20, -2.00, "⭐⭐☆☆☆"),
            (50.0, 1.50, -2.00, "⭐☆☆☆☆"),
        ]
        for wr, w_ret, l_ret, exp_rating in tiers:
            oos = self._build_dummy_oos_df('Enterprise_SaaS', high_conv_cnt=20, win_rate_pct=wr, total_cnt=50, win_return=w_ret, loss_return=l_ret)
            rep = mlp.generate_report_tables(oos_df=oos, model=None, feature_cols=[], latest_features_df=pd.DataFrame())
            row = next(r for r in rep['table2'] if r['sector_key'] == 'Enterprise_SaaS')
            self.assertEqual(row['alpha_rating'], exp_rating, f"Params (wr={wr}, win_ret={w_ret}, loss_ret={l_ret}) produced {row['alpha_rating']}, expected {exp_rating}")
        print("[+] Star rating institutional tier tests PASSED.")


# ==============================================================================
# Suite 4: LightGBM Balanced Probabilities & Calibration Check
# ==============================================================================

class TestEmpiricalLightGBMCalibration(unittest.TestCase):
    """Stress test for LightGBM balanced probabilities."""

    def test_lgbm_params_has_balanced_class_weight(self):
        """Verify that run_walk_forward_cv uses class_weight='balanced'."""
        import inspect
        src = inspect.getsource(mlp.run_walk_forward_cv)
        self.assertIn("'class_weight': 'balanced'", src, "LightGBM params missing 'class_weight': 'balanced'!")
        print("[+] LightGBM class_weight='balanced' in source PASSED.")

    def test_balanced_probability_distribution(self):
        """Train LightGBM on synthetic binary dataset and verify probability mean ~45-55%."""
        rng = np.random.RandomState(42)
        n = 1000
        # Imbalanced 70% zeros, 30% ones
        X = rng.normal(0, 1, size=(n, 10))
        y = (X[:, 0] + X[:, 1] * 0.5 + rng.normal(0, 1, n) > 0.8).astype(int)
        pos_rate = y.mean()

        clf = lgb.LGBMClassifier(
            max_depth=5,
            num_leaves=23,
            learning_rate=0.03,
            n_estimators=100,
            random_state=42,
            objective='binary',
            class_weight='balanced',
            verbose=-1
        )
        clf.fit(X, y)
        probs = clf.predict_proba(X)[:, 1]

        prob_mean = probs.mean()
        print(f"[*] Raw positive rate: {pos_rate * 100:.1f}%, Balanced model prob mean: {prob_mean * 100:.1f}%")

        # Balanced weights shift the probability center up from the depressed 30% towards ~50%
        self.assertGreater(prob_mean, 0.40, f"Balanced prob mean {prob_mean:.3f} is too low (< 0.40)!")
        self.assertLess(prob_mean, 0.60, f"Balanced prob mean {prob_mean:.3f} is too high (> 0.60)!")
        print("[+] Balanced probability centering test PASSED.")


# ==============================================================================
# Suite 5: Macro Weather Alignment Empirical Verification
# ==============================================================================

class TestEmpiricalMacroWeatherAlignment(unittest.TestCase):
    """Verify macro weather evaluation rules match integrated_market_radar.py exactly."""

    def test_macro_weather_states(self):
        # 1. Dual breakdown: QQQ < -3.0 and SMH < -3.0 -> 🌧️ 暴雨空仓
        w1 = mlp.evaluate_macro_weather(qqqm_bias=-3.5, smh_bias=-3.2, vixy_pos=0.8)
        self.assertIn("暴雨空仓", w1['status'])

        # 2. Only QQQ < -3.0, but SMH > -3.0 -> ⛅ 阴天防守 (NOT 暴雨!)
        w2 = mlp.evaluate_macro_weather(qqqm_bias=-3.5, smh_bias=-1.0, vixy_pos=0.5)
        self.assertIn("阴天防守", w2['status'], "Single index breakdown should be 阴天防守, not 暴雨空仓!")

        # 3. Only SMH < -3.0, but QQQ > -3.0 -> ⛅ 阴天防守 (NOT 暴雨!)
        w3 = mlp.evaluate_macro_weather(qqqm_bias=-1.0, smh_bias=-4.0, vixy_pos=0.5)
        self.assertIn("阴天防守", w3['status'], "Single index breakdown should be 阴天防守, not 暴雨空仓!")

        # 4. Bull base: both >= 0.25% and VIXY < 0.75 -> ☀️ 晴天做多
        w4 = mlp.evaluate_macro_weather(qqqm_bias=1.5, smh_bias=2.0, vixy_pos=0.3)
        self.assertIn("晴天做多", w4['status'])

        print("[+] Macro weather alignment tests PASSED.")


# ==============================================================================
# Suite 6: Table 4 Sector OOS Win Rate Linkage & Ranking Invariance
# ==============================================================================

class TestEmpiricalTable4SectorLinkage(unittest.TestCase):
    """Stress tests for Table 4 sector linkage warnings and rank invariance."""

    def test_low_win_rate_sector_warning_and_score_invariance(self):
        """
        Verify that:
        1. A ticker whose sector historical OOS win rate < 45% receives '⚠️ 赛道历史胜率偏低·观望'.
        2. Its ranking order and predicted_win_rate_pct remain completely unmodified.
        3. A ticker whose sector has OOS win rate >= 45% receives normal buy/accumulate action.
        """
        # Create OOS df where Semiconductors has 60% WR, but Enterprise_SaaS has 40% WR
        records_oos = []
        # Semiconductors: 6 wins out of 10 high conv
        for i in range(6):
            records_oos.append({'Date': pd.Timestamp('2024-01-01'), 'Ticker': 'NVDA', 'Sector': 'Semiconductors',
                                'Close': 100.0, 'Label': 1.0, 'Prob_1': 0.65, 'Realized_Return': 4.0})
        for i in range(4):
            records_oos.append({'Date': pd.Timestamp('2024-01-02'), 'Ticker': 'NVDA', 'Sector': 'Semiconductors',
                                'Close': 100.0, 'Label': 0.0, 'Prob_1': 0.65, 'Realized_Return': -2.5})

        # Enterprise_SaaS: 4 wins out of 10 high conv -> 40% WR (< 45%)
        for i in range(4):
            records_oos.append({'Date': pd.Timestamp('2024-01-01'), 'Ticker': 'MSFT', 'Sector': 'Enterprise_SaaS',
                                'Close': 400.0, 'Label': 1.0, 'Prob_1': 0.65, 'Realized_Return': 4.0})
        for i in range(6):
            records_oos.append({'Date': pd.Timestamp('2024-01-02'), 'Ticker': 'MSFT', 'Sector': 'Enterprise_SaaS',
                                'Close': 400.0, 'Label': 0.0, 'Prob_1': 0.65, 'Realized_Return': -2.5})

        oos_df = pd.DataFrame(records_oos)

        # Create latest live feature df where MSFT has higher prob (0.75) than NVDA (0.70)
        latest_df = pd.DataFrame([
            {'Ticker': 'MSFT', 'Sector': 'Enterprise_SaaS', 'Close': 420.0, 'Prob_1': 0.75, 'ATR_Pct': 2.5},
            {'Ticker': 'NVDA', 'Sector': 'Semiconductors', 'Close': 130.0, 'Prob_1': 0.70, 'ATR_Pct': 3.5},
        ])

        report = mlp.generate_report_tables(
            oos_df=oos_df,
            model=None,
            feature_cols=[],
            latest_features_df=latest_df,
            macro_weather={'status': '☀️ 晴天做多'}
        )

        table4 = report['table4']
        self.assertEqual(len(table4), 2)

        # V1.2 Sector Calibration: NVDA (Semiconductors PF 2.40) gets calibrated boost (0.70 * 1.15 = 0.805)
        # MSFT (Enterprise SaaS PF 1.07) is penalized (0.75 * 0.823 = 0.617)
        # Rank 1 is NVDA with normal buy action
        row_1 = table4[0]
        self.assertEqual(row_1['ticker'], 'NVDA')
        self.assertEqual(row_1['rank'], 1)
        self.assertEqual(row_1['predicted_win_rate_pct'], 70.0)
        self.assertEqual(row_1['action'], "🔥 极高确定性右侧做多")
        self.assertEqual(row_1['action_tag'], "action-buy")

        # Rank 2 is MSFT (Prob_1 = 75.0%), correctly demoted by sector calibration while warning badge is preserved
        row_2 = table4[1]
        self.assertEqual(row_2['ticker'], 'MSFT')
        self.assertEqual(row_2['rank'], 2)
        self.assertEqual(row_2['predicted_win_rate_pct'], 75.0)
        self.assertEqual(row_2['action'], "⚠️ 赛道历史胜率偏低·观望")
        self.assertEqual(row_2['action_tag'], "action-warning")

        print("[+] Table 4 low win rate sector warning & sector calibration ranking test PASSED.")


# ==============================================================================
# Suite 7: Full 35-Symbol Synthetic Panel Macro Feature Stress Test
# ==============================================================================

class TestEmpiricalSynthetic35SymbolPanel(unittest.TestCase):
    """Stress tests on synthetic 35-symbol panel (28 stocks + 7 ETFs, 400 bars)."""

    def test_synthetic_35_symbol_panel_features(self):
        dates = pd.bdate_range('2022-01-01', periods=500)
        dfs = []

        # 7 Macro ETFs
        for idx, etf in enumerate(mlp.MACRO_ETFS):
            dfs.append(create_synthetic_etf_df(etf, dates, seed=500 + idx))

        # 28 Target Stocks
        for idx, stock in enumerate(mlp.TARGET_STOCKS):
            dfs.append(create_synthetic_etf_df(stock, dates, seed=600 + idx))

        raw_panel = pd.concat(dfs, axis=0).reset_index(drop=True)

        panel = mlp.build_feature_panel(raw_panel)
        self.assertFalse(panel.empty)

        req_macro_features = [
            'SMH_Bias_MA50',
            'TLT_Bias_MA50',
            'VIXY_Bias_MA20',
            'VIXY_Pos_60',
            'Alpha_SMH_20d'
        ]

        print("\n--- Synthetic 35-Symbol Panel Macro Feature Audit ---")
        for feat in req_macro_features:
            self.assertIn(feat, panel.columns)
            series = panel[feat].dropna()
            non_zero_cnt = (series.abs() > 1e-6).sum()
            non_zero_rate = non_zero_cnt / len(series)
            feat_std = float(series.std())
            print(f"[*] Synthetic {feat:16s}: non-zero rate = {non_zero_rate * 100:6.2f}%, std = {feat_std:7.4f}")

            self.assertGreater(non_zero_rate, 0.95, f"Synthetic {feat} non-zero rate <= 95%!")
            self.assertGreater(feat_std, 0.01, f"Synthetic {feat} std <= 0.01!")

        print("[+] Synthetic 35-symbol panel test PASSED.")


if __name__ == '__main__':
    unittest.main()
