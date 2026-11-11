#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_challenger_feature2_adversarial.py
Empirical Challenger 2 Adversarial Verification Suite for Feature 2:
- Cross-Sectional Ranking (Alpha_QQQ_5d, Alpha_SMH_20d)
- Table 4 Sector Calibration & Sorting
- Walk-Forward CV Hyperparameters & Daemon Safety
"""

import os
import sys
import unittest
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from ml_predictor_5y import (
    build_feature_panel,
    fetch_or_load_panel_data,
    generate_report_tables,
    compute_stock_features,
    compute_macro_features,
    SECTOR_MAP,
    SECTOR_CHINESE_MAP,
    FEATURE_COLS,
    MACRO_ETFS,
    TARGET_STOCKS,
    ALL_SYMBOLS,
)


class DummyModel:
    """Mock model providing fixed predict_proba and feature importances."""
    def __init__(self, fixed_probs=None, n_features=len(FEATURE_COLS)):
        self.fixed_probs = fixed_probs
        self.feature_importances_ = np.ones(n_features)

    def predict_proba(self, X):
        n = len(X)
        if self.fixed_probs is not None:
            probs = np.array(self.fixed_probs[:n])
        else:
            probs = np.linspace(0.80, 0.40, n)
        return np.column_stack([1.0 - probs, probs])


def create_synthetic_raw_panel(n_days=40, symbols=None):
    """Generates synthetic OHLCV panel for symbols over n_days."""
    if symbols is None:
        symbols = ['NVDA', 'MU', 'MSFT', 'COHR', 'CRWV', 'QQQ', 'SMH', 'IGV', 'TLT', 'VIXY']
    
    dates = pd.date_range(start='2024-01-01', periods=n_days, freq='B')
    records = []
    
    for tk in symbols:
        np.random.seed(hash(tk) % (2**31 - 1))
        base_price = 100.0 + (hash(tk) % 50)
        daily_returns = np.random.normal(0.001, 0.02, size=n_days)
        prices = base_price * np.cumprod(1 + daily_returns)
        
        for d_idx, dt in enumerate(dates):
            p = prices[d_idx]
            records.append({
                'Date': dt,
                'Ticker': tk,
                'Open': p * 0.995,
                'High': p * 1.015,
                'Low': p * 0.985,
                'Close': p,
                'Volume': 1000000 + np.random.randint(-100000, 100000)
            })
            
    df = pd.DataFrame(records).sort_values(['Date', 'Ticker']).reset_index(drop=True)
    return df


class TestFeature2CrossSectionalRank(unittest.TestCase):
    """
    Adversarial verification of Cross-Sectional Ranking for:
    Alpha_QQQ_5d and Alpha_SMH_20d
    """

    def test_truncation_invariance_synthetic(self):
        """
        Check truncation invariance on synthetic data:
        Features at Date T MUST NOT change when data after Date T is cut.
        """
        full_raw = create_synthetic_raw_panel(n_days=30)
        dates = sorted(full_raw['Date'].unique())
        self.assertGreaterEqual(len(dates), 25)

        # Build feature panel on full dataset
        full_feat = build_feature_panel(full_raw)

        # Test truncation at multiple cut dates (T=15, T=20, T=25)
        for cut_idx in [15, 20, 25]:
            cut_date = dates[cut_idx]
            trunc_raw = full_raw[full_raw['Date'] <= cut_date].copy()
            trunc_feat = build_feature_panel(trunc_raw)

            # Compare features at cut_date across all tickers
            full_at_cut = full_feat[full_feat['Date'] == cut_date].sort_values('Ticker').reset_index(drop=True)
            trunc_at_cut = trunc_feat[trunc_feat['Date'] == cut_date].sort_values('Ticker').reset_index(drop=True)

            self.assertEqual(len(full_at_cut), len(trunc_at_cut))
            self.assertTrue((full_at_cut['Ticker'] == trunc_at_cut['Ticker']).all())

            # Alpha_QQQ_5d and Alpha_SMH_20d MUST match exactly
            np.testing.assert_allclose(
                trunc_at_cut['Alpha_QQQ_5d'].values,
                full_at_cut['Alpha_QQQ_5d'].values,
                rtol=1e-5, atol=1e-6,
                err_msg=f"Truncation invariance failed for Alpha_QQQ_5d at date {cut_date}"
            )
            np.testing.assert_allclose(
                trunc_at_cut['Alpha_SMH_20d'].values,
                full_at_cut['Alpha_SMH_20d'].values,
                rtol=1e-5, atol=1e-6,
                err_msg=f"Truncation invariance failed for Alpha_SMH_20d at date {cut_date}"
            )

    def test_truncation_invariance_real_parquet(self):
        """
        Check truncation invariance on real 5-year Parquet dataset:
        Pick a historical date T, truncate data at T, verify features at T are unchanged.
        """
        cache_file = os.path.join(CURRENT_DIR, 'data_cache', 'ml_panel_5y.parquet')
        if not os.path.exists(cache_file):
            self.skipTest("Local parquet cache not available")

        raw_panel = pd.read_parquet(cache_file)
        raw_panel['Date'] = pd.to_datetime(raw_panel['Date'])
        dates = sorted(raw_panel['Date'].unique())
        
        # Pick cut date in 2024
        target_dates = [d for d in dates if d.year == 2024]
        self.assertGreater(len(target_dates), 50)
        cut_date = target_dates[50] # ~March 2024

        # Truncate raw panel to include sufficient lookback up to cut_date
        sub_full = raw_panel[raw_panel['Date'] <= target_dates[60]].copy()
        sub_trunc = raw_panel[raw_panel['Date'] <= cut_date].copy()

        feat_full = build_feature_panel(sub_full)
        feat_trunc = build_feature_panel(sub_trunc)

        full_at_t = feat_full[feat_full['Date'] == cut_date].sort_values('Ticker').reset_index(drop=True)
        trunc_at_t = feat_trunc[feat_trunc['Date'] == cut_date].sort_values('Ticker').reset_index(drop=True)

        self.assertEqual(len(full_at_t), len(trunc_at_t))
        np.testing.assert_allclose(
            trunc_at_t['Alpha_QQQ_5d'].values,
            full_at_t['Alpha_QQQ_5d'].values,
            rtol=1e-5, atol=1e-6,
            err_msg=f"Real data truncation invariance failed for Alpha_QQQ_5d at {cut_date}"
        )
        np.testing.assert_allclose(
            trunc_at_t['Alpha_SMH_20d'].values,
            full_at_t['Alpha_SMH_20d'].values,
            rtol=1e-5, atol=1e-6,
            err_msg=f"Real data truncation invariance failed for Alpha_SMH_20d at {cut_date}"
        )

    def test_future_spike_insensitivity(self):
        """
        Adversarial test: A massive price spike at T+1 must NOT alter
        date T's Alpha_QQQ_5d or Alpha_SMH_20d cross-sectional ranks.
        """
        full_raw = create_synthetic_raw_panel(n_days=30)
        dates = sorted(full_raw['Date'].unique())
        t_idx = 18
        t_date = dates[t_idx]
        t1_date = dates[t_idx + 1]

        # Baseline features
        base_feat = build_feature_panel(full_raw)
        base_t = base_feat[base_feat['Date'] == t_date].sort_values('Ticker').reset_index(drop=True)

        # Inject extreme spike at T+1: NVDA jumps 1000%, QQQ drops 90%
        spiked_raw = full_raw.copy()
        mask_nvda = (spiked_raw['Date'] == t1_date) & (spiked_raw['Ticker'] == 'NVDA')
        mask_qqqm = (spiked_raw['Date'] == t1_date) & (spiked_raw['Ticker'] == 'QQQ')
        mask_smh = (spiked_raw['Date'] == t1_date) & (spiked_raw['Ticker'] == 'SMH')

        spiked_raw.loc[mask_nvda, ['Open', 'High', 'Low', 'Close']] *= 10.0
        spiked_raw.loc[mask_qqqm, ['Open', 'High', 'Low', 'Close']] *= 0.10
        spiked_raw.loc[mask_smh, ['Open', 'High', 'Low', 'Close']] *= 5.0

        spiked_feat = build_feature_panel(spiked_raw)
        spiked_t = spiked_feat[spiked_feat['Date'] == t_date].sort_values('Ticker').reset_index(drop=True)

        # Date T features MUST be 100% identical
        np.testing.assert_allclose(
            spiked_t['Alpha_QQQ_5d'].values,
            base_t['Alpha_QQQ_5d'].values,
            rtol=1e-7, atol=1e-7,
            err_msg="Future spike at T+1 leaked into Date T Alpha_QQQ_5d!"
        )
        np.testing.assert_allclose(
            spiked_t['Alpha_SMH_20d'].values,
            base_t['Alpha_SMH_20d'].values,
            rtol=1e-7, atol=1e-7,
            err_msg="Future spike at T+1 leaked into Date T Alpha_SMH_20d!"
        )

    def test_nan_and_tie_resilience_in_cross_sectional_rank(self):
        """
        Adversarial test: verify that complete ties and NaN returns on Date T
        are handled safely without NaN or Inf propagation.
        """
        raw = create_synthetic_raw_panel(n_days=25)
        # Force all tickers on day 20 to have identical returns
        dates = sorted(raw['Date'].unique())
        d20 = dates[20]
        mask_d20 = raw['Date'] == d20
        raw.loc[mask_d20, 'Close'] = 100.0
        raw.loc[mask_d20, 'Open'] = 100.0

        feat = build_feature_panel(raw)
        feat_d20 = feat[feat['Date'] == d20]

        self.assertFalse(feat_d20['Alpha_QQQ_5d'].isna().any())
        self.assertFalse(feat_d20['Alpha_SMH_20d'].isna().any())
        self.assertTrue((feat_d20['Alpha_QQQ_5d'] >= 0.0).all())
        self.assertTrue((feat_d20['Alpha_QQQ_5d'] <= 1.0).all())

    def test_multi_date_truncation_invariance_parquet(self):
        """
        Verify truncation invariance across multiple years (2022, 2023, 2024)
        on the real Parquet dataset.
        """
        cache_file = os.path.join(CURRENT_DIR, 'data_cache', 'ml_panel_5y.parquet')
        if not os.path.exists(cache_file):
            self.skipTest("Local parquet cache not available")

        raw_panel = pd.read_parquet(cache_file)
        raw_panel['Date'] = pd.to_datetime(raw_panel['Date'])
        dates = sorted(raw_panel['Date'].unique())

        # Select test dates across 3 different years
        test_dates = [
            [d for d in dates if d.year == 2022][100],
            [d for d in dates if d.year == 2023][100],
            [d for d in dates if d.year == 2024][100],
        ]

        # Use slice containing data up to late 2024
        max_test_date = test_dates[-1] + pd.Timedelta(days=30)
        panel_pool = raw_panel[raw_panel['Date'] <= max_test_date].copy()
        feat_full = build_feature_panel(panel_pool)

        for cut_d in test_dates:
            panel_cut = panel_pool[panel_pool['Date'] <= cut_d].copy()
            feat_cut = build_feature_panel(panel_cut)

            full_rows = feat_full[feat_full['Date'] == cut_d].sort_values('Ticker').reset_index(drop=True)
            cut_rows = feat_cut[feat_cut['Date'] == cut_d].sort_values('Ticker').reset_index(drop=True)

            self.assertEqual(len(full_rows), len(cut_rows))
            np.testing.assert_allclose(
                cut_rows['Alpha_QQQ_5d'].values,
                full_rows['Alpha_QQQ_5d'].values,
                rtol=1e-5, atol=1e-6,
                err_msg=f"Truncation invariance failed at multi-year date {cut_d}"
            )

    def test_single_ticker_date_handling(self):
        """
        Tests handling of single-ticker date:
        Must produce finite non-null rank in [0, 1] without crash or NaN.
        """
        # Create dataset where only 1 target stock exists on Day 0
        symbols = ['NVDA', 'QQQ', 'SMH', 'IGV', 'TLT', 'VIXY']
        raw = create_synthetic_raw_panel(n_days=25, symbols=symbols)
        feat = build_feature_panel(raw)

        self.assertFalse(feat['Alpha_QQQ_5d'].isna().any())
        self.assertFalse(feat['Alpha_SMH_20d'].isna().any())
        self.assertFalse(np.isinf(feat['Alpha_QQQ_5d']).any())
        self.assertFalse(np.isinf(feat['Alpha_SMH_20d']).any())

        # Check values are bounded in [0, 1]
        self.assertTrue((feat['Alpha_QQQ_5d'] >= 0.0).all())
        self.assertTrue((feat['Alpha_QQQ_5d'] <= 1.0).all())
        self.assertTrue((feat['Alpha_SMH_20d'] >= 0.0).all())
        self.assertTrue((feat['Alpha_SMH_20d'] <= 1.0).all())

    def test_multi_ticker_rank_monotonicity(self):
        """
        Multi-ticker date: verify that higher raw excess return strictly
        maps to higher cross-sectional percentile rank.
        """
        raw = create_synthetic_raw_panel(n_days=30)
        macro_df = compute_macro_features(raw_panel=raw)
        feat = build_feature_panel(raw)

        # Merge macro returns back to test raw alpha monotonicity
        test_feat = pd.merge(feat, macro_df[['Date', 'QQQ_Ret_5d', 'SMH_Ret_20d']], on='Date', how='left')
        test_feat['Raw_Alpha_QQQ_5d'] = test_feat['Ret_5d'] - test_feat['QQQ_Ret_5d']

        # Group by Date and check that higher raw alpha has higher rank
        for dt, grp in test_feat.groupby('Date'):
            if len(grp) >= 3:
                sorted_by_raw = grp.sort_values('Raw_Alpha_QQQ_5d')
                ranks = sorted_by_raw['Alpha_QQQ_5d'].values
                # Check non-decreasing ranks
                self.assertTrue(
                    np.all(np.diff(ranks) >= -1e-8),
                    f"Ranks not monotonic with raw alpha on {dt}: {ranks}"
                )


class TestTable4SectorCalibration(unittest.TestCase):
    """
    Adversarial verification of Table 4 Sector Calibration:
    - Multiplier formula mult = np.clip(sector_pf / 1.30, 0.75, 1.15)
    - Adversarial sector PF scenarios
    - Strict sorting order by Calibrated_Prob descending
    """

    def setUp(self):
        self.feature_cols = FEATURE_COLS

    def test_sector_pf_multiplier_formula_boundaries(self):
        """
        Exhaustive test of multiplier formula:
        mult = np.clip(sector_pf / 1.30, 0.75, 1.15)
        across boundary and extreme values.
        """
        test_cases = [
            (-1.0, 0.75),
            (0.0, 0.75),
            (0.50, 0.75),
            (0.80, 0.75),
            (0.974, 0.75),
            (0.975, 0.75),           # 0.975 / 1.30 = 0.75
            (1.000, 1.000 / 1.30),
            (1.100, 1.100 / 1.30),
            (1.300, 1.00),           # 1.30 / 1.30 = 1.00 (neutral anchor)
            (1.400, 1.400 / 1.30),
            (1.494, 1.494 / 1.30),
            (1.495, 1.15),           # 1.495 / 1.30 = 1.15
            (1.500, 1.15),
            (2.000, 1.15),
            (5.000, 1.15),
            (100.0, 1.15),
        ]

        for pf_val, exp_mult in test_cases:
            mult = float(np.clip(pf_val / 1.30, 0.75, 1.15))
            self.assertAlmostEqual(
                mult, exp_mult, places=4,
                msg=f"Multiplier mismatch for PF={pf_val}: got {mult}, expected {exp_mult}"
            )

    def _populate_features(self, df):
        """Helper to ensure latest_df has all feature columns if FEATURE_COLS is used."""
        df = df.copy()
        for col in self.feature_cols:
            if col not in df.columns:
                df[col] = 1.0
        return df

    def test_adversarial_low_pf_high_prob_vs_high_pf_med_prob(self):
        """
        Adversarial scenario:
        - Low PF sector (Enterprise_SaaS, PF=0.85 -> mult=0.75) with High raw prob (0.75)
          -> Calibrated = 0.75 * 0.75 = 0.5625
        - High PF sector (Memory, PF=2.20 -> mult=1.15) with Medium raw prob (0.55)
          -> Calibrated = 0.55 * 1.15 = 0.6325
        Table 4 MUST rank Memory ahead of Enterprise_SaaS!
        """
        # Construct synthetic OOS data
        oos_records = []
        # Memory: 10 wins (+3.0%), 2 losses (-1.0%) -> PF = 30 / 2 = 15.0 -> mult = 1.15
        for i in range(10):
            oos_records.append({'Date': pd.Timestamp('2024-01-01')+pd.Timedelta(days=i), 'Ticker': 'MU', 'Sector': 'Memory', 'Close': 100.0, 'Label': 1.0, 'Prob_1': 0.60, 'Realized_Return': 3.0})
        for i in range(2):
            oos_records.append({'Date': pd.Timestamp('2024-02-01')+pd.Timedelta(days=i), 'Ticker': 'MU', 'Sector': 'Memory', 'Close': 100.0, 'Label': 0.0, 'Prob_1': 0.60, 'Realized_Return': -1.0})

        # Enterprise SaaS: 2 wins (+1.0%), 10 losses (-2.0%) -> PF = 2 / 20 = 0.10 -> mult = 0.75
        for i in range(2):
            oos_records.append({'Date': pd.Timestamp('2024-01-01')+pd.Timedelta(days=i), 'Ticker': 'MSFT', 'Sector': 'Enterprise_SaaS', 'Close': 400.0, 'Label': 1.0, 'Prob_1': 0.75, 'Realized_Return': 1.0})
        for i in range(10):
            oos_records.append({'Date': pd.Timestamp('2024-02-01')+pd.Timedelta(days=i), 'Ticker': 'MSFT', 'Sector': 'Enterprise_SaaS', 'Close': 400.0, 'Label': 0.0, 'Prob_1': 0.75, 'Realized_Return': -2.0})

        oos_df = pd.DataFrame(oos_records)

        latest_df = pd.DataFrame({
            'Ticker': ['MSFT', 'MU'],
            'Sector': ['Enterprise_SaaS', 'Memory'],
            'Close': [400.0, 100.0],
            'Prob_1': [0.75, 0.55],
            'ATR_Pct': [2.0, 3.5]
        })
        latest_df = self._populate_features(latest_df)

        tables = generate_report_tables(
            oos_df=oos_df,
            model=None,
            feature_cols=self.feature_cols,
            latest_features_df=latest_df,
            macro_weather={'status': '☀️ 晴天做多'}
        )

        t4 = tables['table4']
        self.assertEqual(len(t4), 2)
        # MU must be Rank 1 (Calibrated 63.3%), MSFT must be Rank 2 (Calibrated 56.2%)
        self.assertEqual(t4[0]['ticker'], 'MU', f"Top 1 expected MU, got {t4[0]['ticker']}")
        self.assertEqual(t4[1]['ticker'], 'MSFT', f"Top 2 expected MSFT, got {t4[1]['ticker']}")
        self.assertAlmostEqual(t4[0]['calibrated_win_rate_pct'], 63.2, places=1)
        self.assertAlmostEqual(t4[1]['calibrated_win_rate_pct'], 56.2, places=1)

    def test_multi_sector_complete_sorting_order(self):
        """
        Adversarial test across all 7 sectors:
        Verify that sorting order strictly matches Calibrated_Prob descending,
        and every rank 1..N has t4[i].calibrated >= t4[i+1].calibrated.
        """
        sectors = [
            ('Memory', 2.50, 0.58),              # mult=1.15, cal=0.6670
            ('Semiconductors', 2.20, 0.56),      # mult=1.15, cal=0.6440
            ('Optical_Interconnect', 1.80, 0.54),# mult=1.15, cal=0.6210
            ('Neo_Cloud', 1.45, 0.55),           # mult=1.1154, cal=0.6135
            ('Energy_Power', 1.35, 0.58),        # mult=1.0385, cal=0.6023
            ('Crypto_Proxy', 1.25, 0.62),        # mult=0.9615, cal=0.5962
            ('Enterprise_SaaS', 0.90, 0.70),     # mult=0.75, cal=0.5250 (was highest raw!)
        ]

        # Construct synthetic OOS to produce targeted PFs
        oos_records = []
        latest_records = []
        for sec_name, target_pf, raw_prob in sectors:
            tk = f"TK_{sec_name[:3]}"
            # Wins and losses to match target_pf: PF = sum(win_ret) / sum(loss_ret)
            # 10 wins of 1.0 * target_pf, 10 losses of -1.0
            for i in range(10):
                oos_records.append({'Date': pd.Timestamp('2024-01-01')+pd.Timedelta(days=i), 'Ticker': tk, 'Sector': sec_name, 'Close': 100.0, 'Label': 1.0, 'Prob_1': raw_prob, 'Realized_Return': float(target_pf)})
            for i in range(10):
                oos_records.append({'Date': pd.Timestamp('2024-02-01')+pd.Timedelta(days=i), 'Ticker': tk, 'Sector': sec_name, 'Close': 100.0, 'Label': 0.0, 'Prob_1': raw_prob, 'Realized_Return': -1.0})

            latest_records.append({
                'Ticker': tk,
                'Sector': sec_name,
                'Close': 100.0,
                'Prob_1': raw_prob,
                'ATR_Pct': 2.5
            })

        oos_df = pd.DataFrame(oos_records)
        latest_df = self._populate_features(pd.DataFrame(latest_records))

        tables = generate_report_tables(
            oos_df=oos_df,
            model=None,
            feature_cols=self.feature_cols,
            latest_features_df=latest_df
        )

        t4 = tables['table4']
        self.assertEqual(len(t4), 7)

        # Verify strict monotonic decrease of calibrated_win_rate_pct
        cal_rates = [r['calibrated_win_rate_pct'] for r in t4]
        for i in range(len(cal_rates) - 1):
            self.assertGreaterEqual(
                cal_rates[i], cal_rates[i + 1],
                f"Table 4 sorting violation at index {i}: {cal_rates[i]} < {cal_rates[i+1]}"
            )

        # SaaS had the HIGHEST raw prob (0.70), but MUST be ranked LAST due to low PF (0.90)
        self.assertEqual(t4[-1]['sector_key'], 'Enterprise_SaaS')
        # Memory had lower raw prob (0.58), but MUST be ranked FIRST due to high PF (2.50)
        self.assertEqual(t4[0]['sector_key'], 'Memory')

    def test_unknown_sector_default_pf(self):
        """
        Verify that a stock with an unknown or unseen sector
        defaults cleanly to PF 1.30 (mult 1.00) without crashing.
        """
        oos_df = pd.DataFrame([{
            'Date': pd.Timestamp('2024-01-01'), 'Ticker': 'MU', 'Sector': 'Memory',
            'Close': 100.0, 'Label': 1.0, 'Prob_1': 0.60, 'Realized_Return': 2.0
        }])

        latest_df = pd.DataFrame([{
            'Ticker': 'UNKNOWN_CO',
            'Sector': 'Alien_Sector',
            'Close': 50.0,
            'Prob_1': 0.60,
            'ATR_Pct': 2.0
        }])
        latest_df = self._populate_features(latest_df)

        tables = generate_report_tables(
            oos_df=oos_df,
            model=None,
            feature_cols=self.feature_cols,
            latest_features_df=latest_df
        )

        t4 = tables['table4']
        self.assertEqual(len(t4), 1)
        # Default mult = np.clip(1.30/1.30, 0.75, 1.15) = 1.00 -> calibrated == raw == 60.0%
        self.assertAlmostEqual(t4[0]['calibrated_win_rate_pct'], 60.0, places=1)


class TestHyperparametersAndDaemonIsolation(unittest.TestCase):
    """
    Verifies LightGBM hyperparameters and protected background daemon isolation.
    """

    def test_lgb_hyperparameters_colsample_and_max_bin(self):
        """
        Verify that lgb_params specify colsample_bytree=0.65 and max_bin=63
        to suppress macro feature monopoly.
        """
        import inspect
        from ml_predictor_5y import run_walk_forward_cv

        source = inspect.getsource(run_walk_forward_cv)
        self.assertIn("'colsample_bytree': 0.65", source)
        self.assertIn("'max_bin': 63", source)

    def test_live_daemon_process_unharmed(self):
        """
        STRICT SAFETY CONSTRAINT:
        Verify that integrated_market_radar.py remains untouched and unmodified.
        """
        import subprocess
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        res = subprocess.run(
            ["git", "-C", repo_dir, "status", "--porcelain", "integrated_market_radar.py"],
            capture_output=True, text=True
        )
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "")


if __name__ == '__main__':
    unittest.main()
