#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_challenger_m2.py - Empirical Adversarial Stress Test Suite
for Milestone 2: AI Short Inference Pool Calibration Engine & Dual JSON Contracts

Author: Challenger M2 (teamwork_challenger)
Roles: Critic, Specialist
Target: Milestone 2 Deliverables
- Calibration engine: ml_predictor_5y.calibrate_short_probability & integrated_market_radar.calibrate_short_probability
- Dual JSON data contracts: data_cache/latest_ai_predictions.json

Adversarial Stress Dimensions:
1. Clamping Bounds & Saturation:
   - Output must be strictly clamped to [10.0, 95.0].
   - Tests at raw_prob = 0.0, 0.01, 0.05, 0.95, 0.99, 1.0, -10.0, 150.0.
2. NaN and Inf Immunity:
   - Inputs with NaN or Inf (raw_prob, dollar_pcr, put_wall, close, sector_bear_pf) must never produce NaN or Inf.
3. Extreme Options Radar Inputs:
   - Dollar PCR in {0.0, 5.0, 99.0, NaN}
   - Put Wall in {0, 1e6, NaN}
   - Close in {0, 100, 1e6, NaN}
4. Institutional Put Boost Monotonicity:
   - For any pair of PCR inputs pcr_a <= pcr_b, Score(pcr_a) <= Score(pcr_b).
   - Critical check at boundary: pcr = 0.0 vs pcr = 0.10.
5. Dual JSON Contract Invariants (data_cache/latest_ai_predictions.json):
   - top_short descending sort invariance across static snapshot and simulated live updates.
   - by_ticker non-null contract: all 70 tickers contain valid non-null fields for both Long and Short predictions.
   - macro_weather alias consistency: qqqm_bias == qqqm_bias_ma50, smh_bias == smh_bias_ma50, vixy_pos == vixy_pos_60.
"""

import os
import sys
import math
import json
import unittest
from typing import Dict, Any, List

# Ensure UTF-8 output encoding on Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Configure module paths
# 动态定位项目根目录（兼容旧 SCRATCH_DIR/RADAR_DIR 变量名，避免任何旧电脑绝对路径）
_HERE = os.path.dirname(os.path.abspath(__file__))
SCRATCH_DIR = _HERE
RADAR_DIR = _HERE

for p in [SCRATCH_DIR, RADAR_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

import ml_predictor_5y as ml
import integrated_market_radar as imr
import create_futu_terminal as cft


class TestCalibrateShortProbabilityStress(unittest.TestCase):
    """Stress tests for calibrate_short_probability calibration engine."""

    def test_clamping_bounds_10_to_95(self):
        """
        Milestone 2 Requirement & Worker Claim:
        'Final output clamped strictly to [10.0, 95.0].'
        Outputs must never fall below 10.0 or exceed 95.0 under extreme raw probabilities.
        """
        test_raw_probs = [
            (0.0, "Minimum possible probability"),
            (0.01, "Very low probability (1%)"),
            (0.05, "Low probability (5%)"),
            (0.95, "High probability (95%)"),
            (0.99, "Very high probability (99%)"),
            (1.0, "Maximum raw probability (100%)"),
            (-5.0, "Negative outlier probability"),
            (150.0, "Outlier unnormalized percentage"),
        ]

        for raw_p, desc in test_raw_probs:
            for mod_name, fn in [("ml_predictor_5y", ml.calibrate_short_probability),
                                 ("integrated_market_radar", imr.calibrate_short_probability)]:
                with self.subTest(module=mod_name, raw_p=raw_p, desc=desc):
                    score = fn(raw_p, dollar_pcr=1.0, sector_bear_pf=1.20)
                    self.assertFalse(math.isnan(score), f"{mod_name} produced NaN for raw_p={raw_p}")
                    self.assertFalse(math.isinf(score), f"{mod_name} produced Inf for raw_p={raw_p}")
                    self.assertGreaterEqual(
                        score, 10.0,
                        f"[{mod_name}] Clamping lower bound violated: raw_p={raw_p} yielded {score} (< 10.0)"
                    )
                    self.assertLessEqual(
                        score, 95.0,
                        f"[{mod_name}] Clamping upper bound violated: raw_p={raw_p} yielded {score} (> 95.0)"
                    )

    def test_nan_and_inf_immunity(self):
        """
        Adversarial NaN & Inf Stress:
        Passing NaN or Inf to any input of calibrate_short_probability must NEVER
        propagate NaN or Inf to the output score. It should gracefully fallback to a valid float in [10.0, 95.0].
        """
        nan_cases = [
            {"raw_prob": float("nan")},
            {"raw_prob": 0.5, "dollar_pcr": float("nan")},
            {"raw_prob": 0.5, "put_wall": float("nan"), "close": 100.0},
            {"raw_prob": 0.5, "close": float("nan"), "put_wall": 100.0},
            {"raw_prob": 0.5, "sector_bear_pf": float("nan")},
            {"raw_prob": float("inf")},
            {"raw_prob": -float("inf")},
            {"raw_prob": 0.5, "dollar_pcr": float("inf")},
            {"raw_prob": 0.5, "bias_ma50": float("nan")},
            {"raw_prob": 0.5, "rsi_14": float("nan")},
        ]

        for i, kwargs in enumerate(nan_cases):
            for mod_name, fn in [("ml_predictor_5y", ml.calibrate_short_probability),
                                 ("integrated_market_radar", imr.calibrate_short_probability)]:
                with self.subTest(module=mod_name, case_idx=i, kwargs=kwargs):
                    score = fn(**kwargs)
                    self.assertFalse(
                        math.isnan(score),
                        f"[{mod_name}] NaN leak! Input {kwargs} produced NaN output"
                    )
                    self.assertFalse(
                        math.isinf(score),
                        f"[{mod_name}] Inf leak! Input {kwargs} produced Inf output"
                    )
                    self.assertGreaterEqual(
                        score, 10.0,
                        f"[{mod_name}] Under NaN/Inf fallback, score {score} must be >= 10.0"
                    )
                    self.assertLessEqual(
                        score, 95.0,
                        f"[{mod_name}] Under NaN/Inf fallback, score {score} must be <= 95.0"
                    )

    def test_extreme_options_radar_inputs(self):
        """
        Stress test extreme options radar values:
        - Dollar PCR: 0.0, 5.0, 99.0, NaN
        - Put Wall: 0, 1e6, NaN
        - Close: 0, 100, 1e6, NaN
        """
        pcr_values = [0.0, 5.0, 99.0, float("nan")]
        wall_values = [0.0, 1e6, float("nan")]
        close_values = [0.0, 100.0, 1e6, float("nan")]

        for pcr in pcr_values:
            for wall in wall_values:
                for close in close_values:
                    for mod_name, fn in [("ml_predictor_5y", ml.calibrate_short_probability),
                                         ("integrated_market_radar", imr.calibrate_short_probability)]:
                        with self.subTest(module=mod_name, pcr=pcr, wall=wall, close=close):
                            score = fn(0.5, dollar_pcr=pcr, put_wall=wall, close=close)
                            self.assertFalse(
                                math.isnan(score),
                                f"[{mod_name}] Extreme options radar input produced NaN: pcr={pcr}, wall={wall}, close={close}"
                            )
                            self.assertFalse(
                                math.isinf(score),
                                f"[{mod_name}] Extreme options radar input produced Inf: pcr={pcr}, wall={wall}, close={close}"
                            )
                            self.assertGreaterEqual(score, 10.0)
                            self.assertLessEqual(score, 95.0)

    def test_institutional_put_boost_monotonicity(self):
        """
        Requirement:
        'Verify institutional Put boost monotonicity (higher Put PCR monotonically increases or preserves short calibration score).'
        
        Monotonicity Invariant:
        For any PCR_A < PCR_B, Score(PCR_A) <= Score(PCR_B).
        In particular:
        - PCR = 0.0 (extreme call dominance, 0 puts) must have lowest or equal options multiplier.
        - PCR = 0.10 (more puts than 0.0) must NOT have a LOWER score than PCR = 0.0!
        """
        pcr_sweep = [0.0, 0.05, 0.10, 0.30, 0.60, 0.70, 0.90, 1.00, 1.14, 1.15, 1.25, 1.30, 2.00, 5.00, 99.00]

        for mod_name, fn in [("ml_predictor_5y", ml.calibrate_short_probability),
                             ("integrated_market_radar", imr.calibrate_short_probability)]:
            scores = []
            for pcr in pcr_sweep:
                s = fn(0.50, dollar_pcr=pcr, sector_bear_pf=1.20)
                scores.append(s)

            for i in range(len(pcr_sweep) - 1):
                pcr_a = pcr_sweep[i]
                pcr_b = pcr_sweep[i + 1]
                score_a = scores[i]
                score_b = scores[i + 1]

                self.assertLessEqual(
                    score_a, score_b + 1e-7,
                    f"[{mod_name}] Monotonicity violated between PCR={pcr_a} (Score={score_a}) and PCR={pcr_b} (Score={score_b}): Higher Put PCR resulted in a LOWER short score!"
                )

    def test_cross_implementation_exact_parity(self):
        """
        Verify that ml_predictor_5y.calibrate_short_probability and
        integrated_market_radar.calibrate_short_probability produce identical results.
        """
        param_grid = [
            (0.3, 0.8, False, 1.1, None, -6.0, -1.0, 20.0),
            (0.7, 1.4, True, 1.3, "🌧️ 暴雨空仓", -8.0, -2.0, 45.0),
            (0.5, 0.5, False, 1.0, "☀️ 晴天进攻", 18.0, 1.0, 70.0),
            (0.6, 1.2, False, 1.2, "⛅ 阴天防守", 0.0, 0.0, 50.0),
        ]

        for p in param_grid:
            score_ml = ml.calibrate_short_probability(*p)
            score_imr = imr.calibrate_short_probability(*p)
            self.assertAlmostEqual(
                score_ml, score_imr, places=4,
                msg=f"Discrepancy between ml_predictor_5y ({score_ml}) and integrated_market_radar ({score_imr}) for params {p}"
            )


class TestDataCacheDualJsonContractsStress(unittest.TestCase):
    """Stress tests for latest_ai_predictions.json data contract."""

    def setUp(self):
        self.ai_path = os.path.join(SCRATCH_DIR, "data_cache", "latest_ai_predictions.json")
        self.assertTrue(os.path.exists(self.ai_path), f"JSON cache file not found at {self.ai_path}")
        with open(self.ai_path, "r", encoding="utf-8") as f:
            self.cached_data = json.load(f)

    def test_top_short_descending_sort_invariance(self):
        """
        Invariant:
        All entries in 'top_short' must be strictly sorted in descending order of
        'calibrated_short_win_rate_pct' (or equal for ties).
        """
        top_short = self.cached_data.get("top_short", [])
        self.assertGreaterEqual(len(top_short), 5, "top_short pool must contain at least 5 candidates")
        self.assertLessEqual(len(top_short), 10, "top_short pool should contain at most 10 candidates")

        rates = [item["calibrated_short_win_rate_pct"] for item in top_short]
        for i in range(len(rates) - 1):
            curr_r = rates[i]
            next_r = rates[i + 1]
            self.assertGreaterEqual(
                curr_r, next_r,
                f"top_short sort order violation at rank {i+1} ({curr_r}) vs rank {i+2} ({next_r})"
            )

    def test_by_ticker_dual_contracts_non_null(self):
        """
        Contract:
        Every stock in 'by_ticker' (all 70 tickers) must contain valid non-null fields
        for BOTH Long and Short predictions.
        """
        by_ticker = self.cached_data.get("by_ticker", {})
        self.assertGreaterEqual(len(by_ticker), 70, f"Expected at least 70 tickers in by_ticker, found {len(by_ticker)}")

        required_long_fields = [
            "calibrated_win_rate_pct",
            "target_profit_price",
            "stop_loss_price",
            "action"
        ]

        required_short_fields = [
            "calibrated_short_win_rate_pct",
            "short_entry_price",
            "short_target_profit_price",
            "short_stop_loss_price",
            "short_action",
            "dollar_pcr"
        ]

        for tk, item in by_ticker.items():
            # Long fields check
            for f in required_long_fields:
                self.assertIn(f, item, f"Ticker {tk} missing Long contract field '{f}'")
                val = item[f]
                self.assertIsNotNone(val, f"Ticker {tk} has None for Long contract field '{f}'")
                if isinstance(val, (int, float)):
                    self.assertFalse(math.isnan(val), f"Ticker {tk} has NaN for Long contract field '{f}'")

            # Short fields check
            for f in required_short_fields:
                self.assertIn(f, item, f"Ticker {tk} missing Short contract field '{f}'")
                val = item[f]
                self.assertIsNotNone(val, f"Ticker {tk} has None for Short contract field '{f}'")
                if isinstance(val, (int, float)):
                    self.assertFalse(math.isnan(val), f"Ticker {tk} has NaN for Short contract field '{f}'")

    def test_macro_weather_alias_consistency(self):
        """
        Alias Consistency Contract:
        - qqqm_bias == qqqm_bias_ma50
        - smh_bias == smh_bias_ma50
        - vixy_pos == vixy_pos_60
        """
        mw = self.cached_data.get("macro_weather", {})
        self.assertIn("qqqm_bias", mw)
        self.assertIn("qqqm_bias_ma50", mw)
        self.assertEqual(mw["qqqm_bias"], mw["qqqm_bias_ma50"], "qqqm_bias != qqqm_bias_ma50")

        self.assertIn("smh_bias", mw)
        self.assertIn("smh_bias_ma50", mw)
        self.assertEqual(mw["smh_bias"], mw["smh_bias_ma50"], "smh_bias != smh_bias_ma50")

        self.assertIn("vixy_pos", mw)
        self.assertIn("vixy_pos_60", mw)
        self.assertEqual(mw["vixy_pos"], mw["vixy_pos_60"], "vixy_pos != vixy_pos_60")

    def test_dynamic_live_updates_sort_invariance_and_contracts(self):
        """
        Stress test live updates through build_futu_terminal_html:
        Simulate severe market changes (crashes, rallies, halts) and verify that
        data contracts and sort invariance hold across updates.
        """
        backup_content = json.dumps(self.cached_data, ensure_ascii=False, indent=2)

        try:
            # Scenario 1: Mixed crash halts and sudden price movements
            perturbed_stocks = []
            tickers = list(self.cached_data.get("by_ticker", {}).keys())
            for i, tk in enumerate(tickers):
                perturbed_stocks.append({
                    "ticker": tk,
                    "active_price": 100.0 + i,
                    "last": 100.0 + i,
                    "bias": -8.0 if i % 3 == 0 else (5.0 if i % 3 == 1 else -1.0),
                    "rsi": 22.0 if i % 5 == 0 else 55.0,
                    "is_crash_halt": (i % 7 == 0),
                    "is_cooling_halt": (i % 11 == 0),
                    "atr": 3.0,
                })

            weather_storm = {
                "weather": "🌧️ 暴雨空仓",
                "icon": "🌧️",
                "qqqm_bias": -6.50,
                "smh_bias": -11.20,
                "vixy_pos_60": 0.72,
            }

            cft.build_futu_terminal_html(
                stocks_data=perturbed_stocks,
                macro_data={},
                weather_info=weather_storm,
                update_time_str="2026-11-03 14:00:00"
            )

            # Re-read and assert invariants
            with open(self.ai_path, "r", encoding="utf-8") as f:
                updated_data = json.load(f)

            # Check top_short sort
            top_short = updated_data.get("top_short", [])
            rates = [item["calibrated_short_win_rate_pct"] for item in top_short]
            for i in range(len(rates) - 1):
                self.assertGreaterEqual(
                    rates[i], rates[i + 1],
                    f"Live update broke top_short sort invariance: {rates[i]} < {rates[i+1]}"
                )

            # Check by_ticker non-null fields
            by_ticker = updated_data.get("by_ticker", {})
            self.assertGreaterEqual(len(by_ticker), 70)
            for tk, item in by_ticker.items():
                self.assertIsNotNone(item.get("calibrated_short_win_rate_pct"))
                self.assertIsNotNone(item.get("short_action"))
                self.assertIsNotNone(item.get("calibrated_win_rate_pct"))
                self.assertIsNotNone(item.get("action"))

            # Check alias parity
            mw = updated_data.get("macro_weather", {})
            self.assertEqual(mw.get("qqqm_bias"), mw.get("qqqm_bias_ma50"))
            self.assertEqual(mw.get("smh_bias"), mw.get("smh_bias_ma50"))
            self.assertEqual(mw.get("vixy_pos"), mw.get("vixy_pos_60"))

        finally:
            # Safely restore original json cache to BOTH scratch root and us-market-radar
            with open(self.ai_path, "w", encoding="utf-8") as f:
                f.write(backup_content)
            radar_ai_path = os.path.join(RADAR_DIR, "data_cache", "latest_ai_predictions.json")
            if os.path.exists(os.path.dirname(radar_ai_path)):
                with open(radar_ai_path, "w", encoding="utf-8") as f:
                    f.write(backup_content)


if __name__ == "__main__":
    unittest.main()
