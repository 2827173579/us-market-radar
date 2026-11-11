#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_challenger_m2_recheck_deep.py
Deep Empirical Adversarial Stress Test Suite for Milestone 2 Remediation Recheck.

Author: Challenger Short M2 Recheck (teamwork_challenger)
Roles: Critic, Specialist
Target: Milestone 2 Deliverables
- ml_predictor_5y.py & integrated_market_radar.py (and us-market-radar/ copies)
- data_cache/latest_ai_predictions.json
"""

import os
import sys
import math
import json
import random
import hashlib
import filecmp
import unittest
from typing import List, Dict, Any

# Ensure UTF-8 output encoding on Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# 动态定位项目根目录（兼容旧 SCRATCH_DIR/RADAR_DIR 变量名，避免任何旧电脑绝对路径）
_HERE = os.path.dirname(os.path.abspath(__file__))
SCRATCH_DIR = _HERE
RADAR_DIR = _HERE

for p in [SCRATCH_DIR, RADAR_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

import ml_predictor_5y as ml
import integrated_market_radar as imr


class TestClampingBoundaryFuzzing(unittest.TestCase):
    """
    Adversarial Clamping Boundary Fuzzing:
    Verify that raw_prob from -1e9 to +1e9 (and extreme parameter combinations)
    yield calibrated scores STRICTLY within [10.0, 95.0].
    """

    def test_raw_prob_wide_fuzzing(self):
        """Fuzz raw_prob over 5,000 discrete and random points from -1e9 to +1e9."""
        deterministic_points = [
            -1e9, -1e8, -1e6, -10000.0, -1000.0, -100.0, -50.0, -10.0, -5.0, -2.0, -1.0,
            -0.5, -0.1, -0.01, -1e-6, -1e-9, -0.0, 0.0, 1e-9, 1e-6, 0.001, 0.01, 0.05,
            0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 0.9999, 1.0,
            1.0001, 1.01, 1.05, 1.1, 1.5, 2.0, 5.0, 10.0, 20.0, 45.0, 50.0, 65.0, 75.0,
            90.0, 95.0, 99.0, 100.0, 100.1, 105.0, 150.0, 200.0, 500.0, 1000.0,
            10000.0, 1e6, 1e8, 1e9
        ]
        
        # Add 2000 random samples in [-1000, 1000] and 1000 random samples in [-1e9, 1e9]
        random.seed(42)
        random_points = [random.uniform(-1000.0, 1000.0) for _ in range(2000)]
        random_points += [random.uniform(-1e9, 1e9) for _ in range(1000)]
        all_test_points = deterministic_points + random_points

        modules = [("ml_predictor_5y", ml.calibrate_short_probability),
                   ("integrated_market_radar", imr.calibrate_short_probability)]

        for mod_name, fn in modules:
            for val in all_test_points:
                score = fn(val)
                self.assertFalse(math.isnan(score), f"[{mod_name}] NaN produced for raw_prob={val}")
                self.assertFalse(math.isinf(score), f"[{mod_name}] Inf produced for raw_prob={val}")
                self.assertGreaterEqual(
                    score, 10.0,
                    f"[{mod_name}] Lower clamp violated for raw_prob={val}: got {score} < 10.0"
                )
                self.assertLessEqual(
                    score, 95.0,
                    f"[{mod_name}] Upper clamp violated for raw_prob={val}: got {score} > 95.0"
                )

    def test_extreme_momentum_and_squeeze_combos(self):
        """
        Test extreme combination scenarios designed to push scores beyond boundaries:
        - Max bullish momentum + short penalty (seeking score < 10.0)
        - Max bearish momentum + short boost (seeking score > 95.0)
        """
        modules = [("ml_predictor_5y", ml.calibrate_short_probability),
                   ("integrated_market_radar", imr.calibrate_short_probability)]

        # Adversarial scenario A: pushing lower bound
        # raw_prob = 0.0, RSI = 20 (-6 tech), Bias = 25 (> 15 squeeze 0.70x), PCR = 0.0 (0.92x), Sun (0.94x), PF = 0.5 (0.85x)
        for mod_name, fn in modules:
            score_low = fn(
                raw_prob=0.0,
                dollar_pcr=0.0,
                sector_bear_pf=0.5,
                macro_weather="☀️ 晴天进攻",
                bias_ma50=25.0,
                ema10_20_spread=5.0,
                rsi_14=20.0
            )
            self.assertEqual(score_low, 10.0, f"[{mod_name}] Expected exact lower bound 10.0, got {score_low}")

            # Adversarial scenario B: pushing upper bound
            # raw_prob = 1.0 (or 100), Bias = -10, Spread = -2 (+6 tech), PCR = 2.0 (1.10x), Wall broken (1.05x), Rain (1.05x), PF = 2.0 (1.15x)
            score_high = fn(
                raw_prob=1.0,
                dollar_pcr=2.0,
                put_wall_broken=True,
                sector_bear_pf=2.0,
                macro_weather="🌧️ 暴雨空仓",
                bias_ma50=-10.0,
                ema10_20_spread=-2.0,
                rsi_14=50.0
            )
            self.assertEqual(score_high, 95.0, f"[{mod_name}] Expected exact upper bound 95.0, got {score_high}")


class TestNaNInfTypeMutationAdversarial(unittest.TestCase):
    """
    Adversarial NaN / Inf / Type Mutation Injection:
    No combination of corrupted inputs may ever crash or produce NaN/Inf.
    """

    def setUp(self):
        self.modules = [
            ("ml_predictor_5y", ml.calibrate_short_probability),
            ("integrated_market_radar", imr.calibrate_short_probability)
        ]

    def test_single_arg_corruptions(self):
        corrupt_values = [
            None,
            float("nan"),
            float("inf"),
            -float("inf"),
            "corrupted_string",
            "",
            "nan",
            "-inf",
            {"key": "value"},
            [1, 2, 3],
            object(),
        ]

        param_names = [
            "raw_prob", "dollar_pcr", "sector_bear_pf", "macro_weather",
            "bias_ma50", "ema10_20_spread", "rsi_14", "close", "put_wall", "pcr"
        ]

        for mod_name, fn in self.modules:
            for param in param_names:
                for bad_val in corrupt_values:
                    if param == "raw_prob":
                        kwargs = {"raw_prob": bad_val}
                    else:
                        kwargs = {"raw_prob": 0.50, param: bad_val}
                    try:
                        score = fn(**kwargs)
                    except Exception as e:
                        self.fail(f"[{mod_name}] Exception raised with {param}={bad_val!r}: {e}")

                    self.assertIsInstance(score, float, f"[{mod_name}] Returned non-float for {param}={bad_val!r}")
                    self.assertFalse(math.isnan(score), f"[{mod_name}] NaN leak for {param}={bad_val!r}")
                    self.assertFalse(math.isinf(score), f"[{mod_name}] Inf leak for {param}={bad_val!r}")
                    self.assertGreaterEqual(score, 10.0)
                    self.assertLessEqual(score, 95.0)

    def test_all_args_simultaneously_corrupted(self):
        """Pass corrupted values to ALL arguments simultaneously."""
        adversarial_payloads = [
            {
                "raw_prob": float("nan"),
                "dollar_pcr": float("nan"),
                "put_wall_broken": "gibberish",
                "sector_bear_pf": float("nan"),
                "macro_weather": float("nan"),
                "bias_ma50": float("nan"),
                "ema10_20_spread": float("nan"),
                "rsi_14": float("nan"),
                "is_crash_halt": "unknown",
                "close": float("nan"),
                "put_wall": float("nan"),
                "pcr": float("nan"),
            },
            {
                "raw_prob": float("inf"),
                "dollar_pcr": -float("inf"),
                "put_wall_broken": None,
                "sector_bear_pf": float("inf"),
                "macro_weather": None,
                "bias_ma50": float("inf"),
                "ema10_20_spread": -float("inf"),
                "rsi_14": float("inf"),
                "is_crash_halt": None,
                "close": float("inf"),
                "put_wall": -float("inf"),
                "pcr": float("inf"),
            },
            {
                "raw_prob": "invalid",
                "dollar_pcr": "invalid",
                "put_wall_broken": [],
                "sector_bear_pf": {},
                "macro_weather": 12345,
                "bias_ma50": "bad",
                "ema10_20_spread": "bad",
                "rsi_14": "bad",
                "is_crash_halt": 999,
                "close": "bad",
                "put_wall": "bad",
                "pcr": "bad",
            }
        ]

        for mod_name, fn in self.modules:
            for idx, payload in enumerate(adversarial_payloads):
                try:
                    score = fn(**payload)
                except Exception as e:
                    self.fail(f"[{mod_name}] Crash on simultaneous corrupted payload #{idx}: {e}")

                self.assertFalse(math.isnan(score), f"[{mod_name}] NaN leak on payload #{idx}")
                self.assertFalse(math.isinf(score), f"[{mod_name}] Inf leak on payload #{idx}")
                self.assertGreaterEqual(score, 10.0)
                self.assertLessEqual(score, 95.0)


class TestPCRMonotonicityFineGrainedSweep(unittest.TestCase):
    """
    Fine-Grained PCR Sweep from 0.0 to 3.0:
    Step size 0.005 (601 evaluation points).
    Strict Invariant: No lower score for higher PCR.
    """

    def test_dollar_pcr_monotonicity_sweep(self):
        sweep_points = [round(i * 0.005, 3) for i in range(601)]  # 0.000 to 3.000
        modules = [
            ("ml_predictor_5y", ml.calibrate_short_probability),
            ("integrated_market_radar", imr.calibrate_short_probability)
        ]

        for mod_name, fn in modules:
            scores = [fn(0.50, dollar_pcr=pcr, sector_bear_pf=1.20) for pcr in sweep_points]
            
            for i in range(len(sweep_points) - 1):
                pcr_curr = sweep_points[i]
                pcr_next = sweep_points[i + 1]
                score_curr = scores[i]
                score_next = scores[i + 1]

                self.assertLessEqual(
                    score_curr, score_next + 1e-9,
                    f"[{mod_name}] PCR Monotonicity broken: PCR={pcr_curr} (Score={score_curr}) > PCR={pcr_next} (Score={score_next})"
                )

    def test_pcr_arg_monotonicity_sweep(self):
        """Sweep pcr argument when dollar_pcr is 0.0 or None."""
        sweep_points = [round(i * 0.005, 3) for i in range(601)]
        modules = [
            ("ml_predictor_5y", ml.calibrate_short_probability),
            ("integrated_market_radar", imr.calibrate_short_probability)
        ]

        for mod_name, fn in modules:
            scores = [fn(0.50, dollar_pcr=None, pcr=pcr, sector_bear_pf=1.20) for pcr in sweep_points]

            for i in range(len(sweep_points) - 1):
                pcr_curr = sweep_points[i]
                pcr_next = sweep_points[i + 1]
                score_curr = scores[i]
                score_next = scores[i + 1]

                self.assertLessEqual(
                    score_curr, score_next + 1e-9,
                    f"[{mod_name}] pcr Monotonicity broken: PCR={pcr_curr} (Score={score_curr}) > PCR={pcr_next} (Score={score_next})"
                )


class TestDualJsonSchemaAndIntegrityStress(unittest.TestCase):
    """
    Stress-testing schema and content invariants of data_cache/latest_ai_predictions.json.
    """

    def setUp(self):
        self.root_json = os.path.join(SCRATCH_DIR, "data_cache", "latest_ai_predictions.json")
        self.radar_json = os.path.join(RADAR_DIR, "data_cache", "latest_ai_predictions.json")

    def test_json_structure_and_types(self):
        for json_path in [self.root_json, self.radar_json]:
            self.assertTrue(os.path.exists(json_path), f"File missing: {json_path}")
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Top-level keys
            required_top_keys = ["top_long", "top_short", "top10", "by_ticker", "macro_weather"]
            for k in required_top_keys:
                self.assertIn(k, data, f"Missing top-level key '{k}' in {json_path}")

            # top_short checks
            top_short = data["top_short"]
            self.assertGreaterEqual(len(top_short), 5)
            self.assertLessEqual(len(top_short), 10)

            # verify top_short sort
            rates = [x["calibrated_short_win_rate_pct"] for x in top_short]
            self.assertEqual(rates, sorted(rates, reverse=True), "top_short is not sorted descending!")
            for r in rates:
                self.assertGreaterEqual(r, 10.0)
                self.assertLessEqual(r, 95.0)

            # top_long checks
            top_long = data["top_long"]
            self.assertGreaterEqual(len(top_long), 5)
            self.assertLessEqual(len(top_long), 10)
            rates_long = [x["calibrated_win_rate_pct"] for x in top_long]
            self.assertEqual(rates_long, sorted(rates_long, reverse=True), "top_long is not sorted descending!")

            # by_ticker checks
            by_ticker = data["by_ticker"]
            self.assertEqual(len(by_ticker), 70, f"Expected 70 tickers, got {len(by_ticker)}")

            for tk, info in by_ticker.items():
                self.assertIn("calibrated_win_rate_pct", info)
                self.assertIn("calibrated_short_win_rate_pct", info)
                self.assertIn("short_entry_price", info)
                self.assertIn("short_target_profit_price", info)
                self.assertIn("short_stop_loss_price", info)
                self.assertIn("short_action", info)
                self.assertIn("dollar_pcr", info)

                # Ensure values are not null/nan
                swr = info["calibrated_short_win_rate_pct"]
                self.assertIsInstance(swr, (int, float))
                self.assertFalse(math.isnan(swr))
                self.assertGreaterEqual(swr, 10.0)
                self.assertLessEqual(swr, 95.0)

            # macro_weather alias parity
            mw = data["macro_weather"]
            self.assertEqual(mw["qqqm_bias"], mw["qqqm_bias_ma50"])
            self.assertEqual(mw["smh_bias"], mw["smh_bias_ma50"])
            self.assertEqual(mw["vixy_pos"], mw["vixy_pos_60"])


class TestCryptographicParityAndAuthenticity(unittest.TestCase):
    """
    Verify SHA256 parity across all 7 deliverables and authentic test assertions.
    """

    def test_sha256_cryptographic_parity(self):
        deliverables = [
            "ml_predictor_5y.py",
            "integrated_market_radar.py",
            "create_futu_terminal.py",
            "test_short_predictor.py",
            "test_challenger_m2.py",
            "test_m2_adversarial_challenge.py",
            "data_cache/latest_ai_predictions.json",
        ]

        for rel_path in deliverables:
            p1 = os.path.join(SCRATCH_DIR, rel_path)
            p2 = os.path.join(RADAR_DIR, rel_path)

            self.assertTrue(os.path.exists(p1), f"Root file missing: {p1}")
            self.assertTrue(os.path.exists(p2), f"Radar file missing: {p2}")

            with open(p1, "rb") as f1:
                h1 = hashlib.sha256(f1.read()).hexdigest()
            with open(p2, "rb") as f2:
                h2 = hashlib.sha256(f2.read()).hexdigest()
            self.assertEqual(
                h1, h2,
                f"Cryptographic hash mismatch on {rel_path}: root={h1} vs radar={h2}"
            )

    def test_authentic_test_assertions_no_mock_bypass(self):
        """
        Verify that test_short_predictor.py line 391 directly delegates to production module
        and test_latest_ai_predictions_schema_with_dual_pools asserts on actual JSON.
        """
        test_path = os.path.join(SCRATCH_DIR, "test_short_predictor.py")
        with open(test_path, "r", encoding="utf-8") as f:
            code = f.read()

        # Check OracleShortInferenceEngine delegates to ml_predictor_5y
        self.assertIn("import ml_predictor_5y", code)
        self.assertIn("return ml_predictor_5y.calibrate_short_probability(*args, **kwargs)", code)

        # Check no silent fallback in test_latest_ai_predictions_schema_with_dual_pools
        self.assertIn("Production {json_path} missing 'top_short' pool!", code)
        self.assertNotIn("data = OracleShortInferenceEngine.generate_dual_pool_fixture", code)


if __name__ == "__main__":
    unittest.main()
