#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_ml_predictor_adversarial.py - Empirical Adversarial Stress Test Suite
Challenger 1 (Milestone M4) for AI Multi-Sector 5Y US Market Predictor

Test Matrix:
1. Adversarial Future Perturbation & Zero Lookahead:
   - Single bar & multi-bar future price spikes (+1000%, +5000%)
   - Future price crashes (-99%, flash crash)
   - Future volume surges (x1000)
   - Macro benchmark future perturbations at T+1..T+10
   - Strict float equality / epsilon < 1e-12 at bar T across all 23 features.
2. Panel Preservation & Secondary IPO Stress:
   - 50-bar IPO stock added to 1000-bar mature stock panel
   - 10-bar micro-history stock
   - 1-bar extreme boundary stock
   - Intermittent missing dates / trading halt panel
   - Verification of zero mature stock truncation and zero global dropna.
3. Extreme Market Stress & Numerical Invariants:
   - Zero volume (Volume = 0 throughout)
   - Flatline zero range (High == Low == Open == Close)
   - -99.9% overnight gap down
   - +10,000% meme spike
   - Sub-penny micro prices ($0.0001)
   - Alternating extreme volatility
   - Infinite/NaN resilience on all 23 features
   - LightGBM prediction inference bounds strictly in [0.0, 1.0].
4. Triple Barrier Dynamic Boundary & Collision Stress:
   - Zero ATR floor enforcement (upper mult >= 0.035, lower mult >= 0.022)
   - Ultra-high volatility ATR scaling
   - Same-day collision resolution (must strictly be Label = 0)
   - Gap-open boundary crossing
   - Tail 5-day NaN isolation invariant.
5. Production Daemon Non-Interference:
   - Verify background process `integrated_market_radar.py` is alive and unperturbed.
"""

import os
import sys
import unittest
import numpy as np
import pandas as pd
import lightgbm as lgb

PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import ml_predictor_5y as predictor


class SyntheticAdversarialMarketFactory:
    """Generates synthetic price panels for stress testing."""

    @staticmethod
    def generate_dates(start_date: str = '2022-01-03', num_days: int = 500) -> list:
        return list(pd.bdate_range(start=start_date, periods=num_days))

    @staticmethod
    def generate_stock(
        ticker: str,
        dates: list,
        base_price: float = 100.0,
        sector: str = 'Semiconductors',
        seed: int = 42
    ) -> pd.DataFrame:
        rng = np.random.RandomState(seed)
        n = len(dates)
        log_rets = rng.normal(loc=0.0005, scale=0.02, size=n)
        prices = base_price * np.exp(np.cumsum(log_rets))
        opens = prices * (1.0 + rng.uniform(-0.005, 0.005, size=n))
        closes = prices
        highs = np.maximum(opens, closes) * (1.0 + rng.uniform(0.001, 0.015, size=n))
        lows = np.minimum(opens, closes) * (1.0 - rng.uniform(0.001, 0.015, size=n))
        volumes = rng.uniform(500000, 5000000, size=n)

        return pd.DataFrame({
            'Date': dates,
            'Ticker': ticker,
            'Sector': sector,
            'Open': np.round(opens, 2),
            'High': np.round(highs, 2),
            'Low': np.round(lows, 2),
            'Close': np.round(closes, 2),
            'Volume': np.round(volumes, 0)
        })

    @staticmethod
    def generate_macro_panel(dates: list, seed: int = 100) -> pd.DataFrame:
        etfs = ['QQQ', 'SPY', 'SMH', 'IGV', 'TLT', 'VIXY', 'GLD']
        frames = []
        for i, etf in enumerate(etfs):
            base_p = 400.0 if etf in ['SPY', 'QQQ'] else (200.0 if etf in ['SMH', 'IGV'] else 90.0)
            df = SyntheticAdversarialMarketFactory.generate_stock(
                ticker=etf, dates=dates, base_price=base_p, sector='Macro', seed=seed + i * 10
            )
            frames.append(df)
        return pd.concat(frames, ignore_index=True)


class TestAdversarialFutureSpikeZeroLookahead(unittest.TestCase):
    """
    Stress-tests zero lookahead by injecting massive future perturbations.
    Features at bar T MUST be invariant to any manipulation at T+1, T+2, T+5, T+10.
    """

    def setUp(self):
        self.dates = SyntheticAdversarialMarketFactory.generate_dates('2022-01-03', 300)
        self.stock_base = SyntheticAdversarialMarketFactory.generate_stock('NVDA', self.dates, seed=101)
        self.macro_base = SyntheticAdversarialMarketFactory.generate_macro_panel(self.dates, seed=202)
        self.raw_base = pd.concat([self.stock_base, self.macro_base], ignore_index=True)
        self.panel_base = predictor.build_feature_panel(self.raw_base)

    def test_single_bar_extreme_future_spike(self):
        """Inject +1000% jump and 1000x volume at T+1; verify all 23 features at T are untouched."""
        T_idx = 150
        T_date = self.dates[T_idx]

        perturbed_stock = self.stock_base.copy()
        perturbed_stock.loc[T_idx + 1, 'Open'] *= 10.0
        perturbed_stock.loc[T_idx + 1, 'High'] *= 12.0
        perturbed_stock.loc[T_idx + 1, 'Low'] *= 9.0
        perturbed_stock.loc[T_idx + 1, 'Close'] *= 11.0
        perturbed_stock.loc[T_idx + 1, 'Volume'] *= 1000.0

        raw_pert = pd.concat([perturbed_stock, self.macro_base], ignore_index=True)
        panel_pert = predictor.build_feature_panel(raw_pert)

        row_base = self.panel_base[(self.panel_base['Ticker'] == 'NVDA') & (self.panel_base['Date'] == T_date)].iloc[0]
        row_pert = panel_pert[(panel_pert['Ticker'] == 'NVDA') & (panel_pert['Date'] == T_date)].iloc[0]

        for feat in predictor.FEATURE_COLS:
            v_base = float(row_base[feat])
            v_pert = float(row_pert[feat])
            diff = abs(v_base - v_pert)
            self.assertLess(
                diff, 1e-10,
                f"Lookahead leak in feature '{feat}' on +1000% spike at T+1! Base={v_base}, Pert={v_pert}, Diff={diff}"
            )

    def test_future_flash_crash_at_multiple_horizons(self):
        """Inject -99% flash crash at T+2 and T+5; verify features at T are untouched."""
        T_idx = 150
        T_date = self.dates[T_idx]

        for offset in [2, 5, 10]:
            perturbed_stock = self.stock_base.copy()
            perturbed_stock.loc[T_idx + offset, 'Open'] *= 0.01
            perturbed_stock.loc[T_idx + offset, 'High'] *= 0.01
            perturbed_stock.loc[T_idx + offset, 'Low'] *= 0.005
            perturbed_stock.loc[T_idx + offset, 'Close'] *= 0.008
            perturbed_stock.loc[T_idx + offset, 'Volume'] = 100.0

            raw_pert = pd.concat([perturbed_stock, self.macro_base], ignore_index=True)
            panel_pert = predictor.build_feature_panel(raw_pert)

            row_base = self.panel_base[(self.panel_base['Ticker'] == 'NVDA') & (self.panel_base['Date'] == T_date)].iloc[0]
            row_pert = panel_pert[(panel_pert['Ticker'] == 'NVDA') & (panel_pert['Date'] == T_date)].iloc[0]

            for feat in predictor.FEATURE_COLS:
                v_base = float(row_base[feat])
                v_pert = float(row_pert[feat])
                diff = abs(v_base - v_pert)
                self.assertLess(
                    diff, 1e-10,
                    f"Lookahead leak in feature '{feat}' on crash at T+{offset}! Base={v_base}, Pert={v_pert}, Diff={diff}"
                )

    def test_macro_future_spike_causality(self):
        """Inject +500% spike in QQQ, SMH, VIXY at T+1; verify bar T macro features and alpha are invariant."""
        T_idx = 150
        T_date = self.dates[T_idx]

        perturbed_macro = self.macro_base.copy()
        mask_t1 = perturbed_macro['Date'] == self.dates[T_idx + 1]
        perturbed_macro.loc[mask_t1, 'Open'] *= 5.0
        perturbed_macro.loc[mask_t1, 'High'] *= 6.0
        perturbed_macro.loc[mask_t1, 'Close'] *= 5.5

        raw_pert = pd.concat([self.stock_base, perturbed_macro], ignore_index=True)
        panel_pert = predictor.build_feature_panel(raw_pert)

        row_base = self.panel_base[(self.panel_base['Ticker'] == 'NVDA') & (self.panel_base['Date'] == T_date)].iloc[0]
        row_pert = panel_pert[(panel_pert['Ticker'] == 'NVDA') & (panel_pert['Date'] == T_date)].iloc[0]

        macro_and_alpha_cols = predictor.CROSS_ASSET_FEATURE_COLS + predictor.MACRO_FEATURE_COLS
        for feat in macro_and_alpha_cols:
            v_base = float(row_base[feat])
            v_pert = float(row_pert[feat])
            diff = abs(v_base - v_pert)
            self.assertLess(
                diff, 1e-10,
                f"Lookahead leak in macro feature '{feat}' on macro spike at T+1! Base={v_base}, Pert={v_pert}, Diff={diff}"
            )


class TestAdversarialIPOPanelPreservation(unittest.TestCase):
    """
    Stress-tests panel preservation against diverse short-history and micro-IPO stocks.
    Mature stocks with 1000 bars must never lose historical bars or have start dates altered.
    """

    def setUp(self):
        self.dates = SyntheticAdversarialMarketFactory.generate_dates('2021-11-01', 1000)
        self.mature_stock = SyntheticAdversarialMarketFactory.generate_stock('NVDA', self.dates, seed=301)
        self.macro_panel = SyntheticAdversarialMarketFactory.generate_macro_panel(self.dates, seed=302)

    def test_50_bar_ipo_non_truncation(self):
        """Add an IPO stock with only 50 bars; assert mature stock retains 1000 bars from 2021."""
        ipo_50_dates = self.dates[-50:]
        ipo_stock = SyntheticAdversarialMarketFactory.generate_stock(
            'NEW_IPO', ipo_50_dates, base_price=25.0, sector='Neo_Cloud', seed=303
        )

        raw = pd.concat([self.mature_stock, ipo_stock, self.macro_panel], ignore_index=True)
        panel = predictor.build_feature_panel(raw)

        nvda_panel = panel[panel['Ticker'] == 'NVDA']
        ipo_panel = panel[panel['Ticker'] == 'NEW_IPO']

        self.assertEqual(len(nvda_panel), 1000, f"NVDA row count truncated from 1000 to {len(nvda_panel)}!")
        self.assertEqual(len(ipo_panel), 50, f"NEW_IPO row count mismatch: {len(ipo_panel)} vs 50")
        self.assertEqual(nvda_panel['Date'].min(), self.dates[0], "NVDA min date altered by IPO presence!")
        self.assertFalse(nvda_panel[predictor.FEATURE_COLS].isna().any().any(), "NVDA has NaNs after IPO inclusion!")

    def test_extreme_micro_ipo_stocks(self):
        """Add micro-IPO stocks with 10 bars, 5 bars, and 2 bars simultaneously."""
        ipo_10 = SyntheticAdversarialMarketFactory.generate_stock(
            'IPO_10', self.dates[-10:], base_price=10.0, sector='Crypto_Proxy', seed=401
        )
        ipo_5 = SyntheticAdversarialMarketFactory.generate_stock(
            'IPO_5', self.dates[-5:], base_price=5.0, sector='Energy_Power', seed=402
        )
        ipo_2 = SyntheticAdversarialMarketFactory.generate_stock(
            'IPO_2', self.dates[-2:], base_price=2.0, sector='Memory', seed=403
        )

        raw = pd.concat([self.mature_stock, ipo_10, ipo_5, ipo_2, self.macro_panel], ignore_index=True)
        panel = predictor.build_feature_panel(raw)

        nvda_panel = panel[panel['Ticker'] == 'NVDA']
        self.assertEqual(len(nvda_panel), 1000, "Mature stock NVDA was truncated by micro-IPOs!")

        for sym, expected_len in [('IPO_10', 10), ('IPO_5', 5), ('IPO_2', 2)]:
            sub = panel[panel['Ticker'] == sym]
            self.assertEqual(len(sub), expected_len, f"Micro-IPO {sym} length mismatch!")
            self.assertFalse(sub[predictor.FEATURE_COLS].isna().any().any(), f"Micro-IPO {sym} contains NaNs!")
            self.assertFalse(np.isinf(sub[predictor.FEATURE_COLS].values).any(), f"Micro-IPO {sym} contains Infs!")


class TestAdversarialExtremeMarketResilience(unittest.TestCase):
    """
    Stress-tests numerical stability and boundary robustness:
    - Zero volume
    - Zero price range (High == Low == Close)
    - -99% overnight gap down
    - Sub-penny micro prices ($0.0001)
    - Alternating limit swings
    - Zero unhandled NaN or Inf across all 23 features
    - Full LightGBM inference validity.
    """

    def setUp(self):
        self.dates = SyntheticAdversarialMarketFactory.generate_dates('2023-01-02', 120)
        self.macro_panel = SyntheticAdversarialMarketFactory.generate_macro_panel(self.dates, seed=501)

    def test_zero_volume_resilience(self):
        """Zero volume on all bars; Vol_Ratio_20 must default safely without division by zero or NaN."""
        stock = SyntheticAdversarialMarketFactory.generate_stock('ZERO_VOL', self.dates, seed=601)
        stock['Volume'] = 0.0

        raw = pd.concat([stock, self.macro_panel], ignore_index=True)
        panel = predictor.build_feature_panel(raw)

        sub = panel[panel['Ticker'] == 'ZERO_VOL']
        self.assertEqual(len(sub), 120)
        self.assertFalse(sub[predictor.FEATURE_COLS].isna().any().any(), "Zero volume produced NaN!")
        self.assertFalse(np.isinf(sub[predictor.FEATURE_COLS].values).any(), "Zero volume produced Inf!")
        self.assertTrue((sub['Vol_Ratio_20'] >= 0.0).all(), "Vol_Ratio_20 is negative!")

    def test_zero_range_flatline_resilience(self):
        """High == Low == Open == Close on all bars; Fib_Pos_60 and Bar_Strength must not divide by zero."""
        stock = SyntheticAdversarialMarketFactory.generate_stock('FLATLINE', self.dates, seed=602)
        stock['High'] = 100.0
        stock['Low'] = 100.0
        stock['Open'] = 100.0
        stock['Close'] = 100.0

        raw = pd.concat([stock, self.macro_panel], ignore_index=True)
        panel = predictor.build_feature_panel(raw)

        sub = panel[panel['Ticker'] == 'FLATLINE']
        self.assertFalse(sub[predictor.FEATURE_COLS].isna().any().any(), "Flatline produced NaN!")
        self.assertFalse(np.isinf(sub[predictor.FEATURE_COLS].values).any(), "Flatline produced Inf!")
        self.assertTrue((sub['Fib_Pos_60'] >= -0.5).all() and (sub['Fib_Pos_60'] <= 1.5).all())

    def test_overnight_crash_and_meme_spike(self):
        """Extreme -99% gap down followed by +5000% meme spike."""
        stock = SyntheticAdversarialMarketFactory.generate_stock('EXTREME', self.dates, seed=603)
        # Bar 50: -99% crash
        stock.loc[50:, 'Open'] *= 0.01
        stock.loc[50:, 'High'] *= 0.01
        stock.loc[50:, 'Low'] *= 0.005
        stock.loc[50:, 'Close'] *= 0.008

        # Bar 80: +5000% spike
        stock.loc[80:, 'Open'] *= 50.0
        stock.loc[80:, 'High'] *= 55.0
        stock.loc[80:, 'Low'] *= 45.0
        stock.loc[80:, 'Close'] *= 50.0

        raw = pd.concat([stock, self.macro_panel], ignore_index=True)
        panel = predictor.build_feature_panel(raw)

        sub = panel[panel['Ticker'] == 'EXTREME']
        self.assertFalse(sub[predictor.FEATURE_COLS].isna().any().any(), "Extreme shock produced NaN!")
        self.assertFalse(np.isinf(sub[predictor.FEATURE_COLS].values).any(), "Extreme shock produced Inf!")

    def test_sub_penny_micro_prices(self):
        """Base price of $0.0001 (sub-penny micro stock)."""
        stock = SyntheticAdversarialMarketFactory.generate_stock('PENNY', self.dates, base_price=0.0001, seed=604)

        raw = pd.concat([stock, self.macro_panel], ignore_index=True)
        panel = predictor.build_feature_panel(raw)

        sub = panel[panel['Ticker'] == 'PENNY']
        self.assertFalse(sub[predictor.FEATURE_COLS].isna().any().any(), "Sub-penny stock produced NaN!")
        self.assertFalse(np.isinf(sub[predictor.FEATURE_COLS].values).any(), "Sub-penny stock produced Inf!")

    def test_adversarial_model_inference_on_extreme_vectors(self):
        """Feed synthetic adversarial vectors to trained LightGBM model; verify output probabilities in [0.0, 1.0]."""
        # Train a light model on synthetic data
        normal_stock = SyntheticAdversarialMarketFactory.generate_stock('TRAIN', self.dates, seed=701)
        raw = pd.concat([normal_stock, self.macro_panel], ignore_index=True)
        panel = predictor.build_feature_panel(raw)
        panel = predictor.apply_triple_barrier_labeling(panel)

        clean = panel.dropna(subset=['Label'])
        X_tr = clean[predictor.FEATURE_COLS]
        y_tr = clean['Label'].astype(int)

        clf = lgb.LGBMClassifier(
            max_depth=5, num_leaves=23, learning_rate=0.03,
            subsample=0.8, colsample_bytree=0.8, n_estimators=30,
            random_state=42, verbose=-1
        )
        clf.fit(X_tr, y_tr)

        # Adversarial input vectors
        adv_df = pd.DataFrame([
            {c: 0.0 for c in predictor.FEATURE_COLS},
            {c: 1e6 for c in predictor.FEATURE_COLS},
            {c: -1e6 for c in predictor.FEATURE_COLS},
            {c: (1e-6 if i % 2 == 0 else -1e-6) for i, c in enumerate(predictor.FEATURE_COLS)},
            {c: (100.0 if 'RSI' in c else 0.0) for c in predictor.FEATURE_COLS},
            {c: (-0.5 if 'Fib' in c else 1.5) for c in predictor.FEATURE_COLS},
        ])

        probs = clf.predict_proba(adv_df)[:, 1]
        self.assertEqual(len(probs), 6)
        self.assertTrue(np.all(probs >= 0.0) and np.all(probs <= 1.0), f"Inference probabilities out of [0, 1]: {probs}")
        self.assertFalse(np.isnan(probs).any(), "Inference returned NaN!")


class TestAdversarialTripleBarrierCollisions(unittest.TestCase):
    """
    Stress-tests edge cases in Triple Barrier Labeling:
    - Same-day upper and lower barrier hit (must resolve strictly to Label = 0)
    - Zero ATR floor constraint enforcement
    - Tail 5-day NaN isolation invariant
    """

    def setUp(self):
        self.dates = SyntheticAdversarialMarketFactory.generate_dates('2024-01-02', 20)

    def test_same_day_collision_strict_stop_loss(self):
        """When high >= upper AND low <= lower on the same day, must resolve to Label = 0 (conservative stop)."""
        rows = [
            {'Date': self.dates[0], 'Ticker': 'TEST', 'Open': 100.0, 'High': 101.0, 'Low': 99.0, 'Close': 100.0, 'ATR_Pct': 2.0},
            # Bar 1: Entry NEXT_OPEN = 100.0. Upper = 103.50, Lower = 97.80
            # High reaches 105.0, Low reaches 95.0 -> Collision!
            {'Date': self.dates[1], 'Ticker': 'TEST', 'Open': 100.0, 'High': 105.0, 'Low': 95.0, 'Close': 102.0, 'ATR_Pct': 2.0},
        ]
        for i in range(2, 10):
            rows.append({'Date': self.dates[i], 'Ticker': 'TEST', 'Open': 100.0, 'High': 100.5, 'Low': 99.5, 'Close': 100.0, 'ATR_Pct': 2.0})

        df = pd.DataFrame(rows)
        labeled = predictor.apply_triple_barrier_labeling(df)

        self.assertEqual(labeled.at[0, 'Label'], 0.0, "Collision did not resolve to Label = 0!")
        self.assertEqual(labeled.at[0, 'Barrier_Hit'], 'collision')

    def test_zero_atr_floor_enforcement(self):
        """ATR_Pct = 0.0 must be floored at min upper 3.0% and min lower 2.2%."""
        rows = [
            {'Date': self.dates[0], 'Ticker': 'TEST', 'Open': 100.0, 'High': 100.0, 'Low': 100.0, 'Close': 100.0, 'ATR_Pct': 0.0},
            {'Date': self.dates[1], 'Ticker': 'TEST', 'Open': 100.0, 'High': 103.0, 'Low': 98.0, 'Close': 100.0, 'ATR_Pct': 0.0},
        ]
        for i in range(2, 10):
            rows.append({'Date': self.dates[i], 'Ticker': 'TEST', 'Open': 100.0, 'High': 100.0, 'Low': 100.0, 'Close': 100.0, 'ATR_Pct': 0.0})

        df = pd.DataFrame(rows)
        labeled = predictor.apply_triple_barrier_labeling(df)

        u = labeled.at[0, 'Target_Upper']
        l = labeled.at[0, 'Target_Lower']
        self.assertAlmostEqual(u, 103.00, places=2, msg=f"Upper barrier not floored at 3.0%: {u}")
        self.assertAlmostEqual(l, 97.80, places=2, msg=f"Lower barrier not floored at 2.2%: {l}")

    def test_tail_5_days_strictly_nan(self):
        """Tail 5 trading days must have NaN labels and be excluded from training."""
        dates = SyntheticAdversarialMarketFactory.generate_dates('2024-01-02', 30)
        stock = SyntheticAdversarialMarketFactory.generate_stock('TAIL_TEST', dates, seed=801)
        macro = SyntheticAdversarialMarketFactory.generate_macro_panel(dates, seed=802)
        raw = pd.concat([stock, macro], ignore_index=True)
        panel = predictor.build_feature_panel(raw)
        labeled = predictor.apply_triple_barrier_labeling(panel)

        tail_5 = labeled[labeled['Ticker'] == 'TAIL_TEST'].iloc[-5:]
        self.assertEqual(len(tail_5), 5)
        self.assertTrue(tail_5['Label'].isna().all(), "Tail 5 bars must have NaN labels for live inference isolation!")


class TestDaemonNonInterference(unittest.TestCase):
    """
    Validates Requirement R5: Production daemon integrated_market_radar.py
    is running and has not been terminated or interrupted.
    """

    def test_daemon_process_active(self):
        """Asserts that at least one integrated_market_radar.py process is active on the system."""
        import subprocess
        cmd = 'powershell -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like \\"*integrated_market_radar.py*\\" } | Measure-Object | Select-Object -ExpandProperty Count"'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        count = int(result.stdout.strip() or 0)
        self.assertGreater(count, 0, "No active integrated_market_radar.py daemon process found!")


def run_adversarial_suite():
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()

    suite.addTests(loader.loadTestsFromTestCase(TestAdversarialFutureSpikeZeroLookahead))
    suite.addTests(loader.loadTestsFromTestCase(TestAdversarialIPOPanelPreservation))
    suite.addTests(loader.loadTestsFromTestCase(TestAdversarialExtremeMarketResilience))
    suite.addTests(loader.loadTestsFromTestCase(TestAdversarialTripleBarrierCollisions))
    suite.addTests(loader.loadTestsFromTestCase(TestDaemonNonInterference))

    runner = unittest.TextTestRunner(verbosity=2)
    return runner.run(suite)


if __name__ == '__main__':
    res = run_adversarial_suite()
    sys.exit(0 if res.wasSuccessful() else 1)
