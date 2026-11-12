#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_challenger_r3_1_adversarial.py - Empirical Challenger R3_1 Stress Suite
Deep adversarial challenge for cycle window extrema detection, min 2-day separation,
and price inversion logic.

Author: Challenger R3_1 (Adversarial Verifier)
Target: ml_predictor_5y._build_cycle_windows_from_projections & production caches
"""

import os
import sys
import json
import unittest
from typing import List, Dict, Any
import numpy as np

PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import ml_predictor_5y as ml_mod


def make_projections(prices: List[float], start_day: int = 1, current_price: float = 100.0) -> List[Dict[str, Any]]:
    """Helper to convert price array into projections list format."""
    projs = []
    for i, p in enumerate(prices):
        d_num = start_day + i
        pct = round((p - current_price) / current_price * 100.0, 1)
        projs.append({
            'day': f"T+{d_num}",
            'date': f"2026-11-{12 + i:02d}",
            'price': round(float(p), 2),
            'pct': pct
        })
    return projs


class TestFlatLinesAndPlateaus(unittest.TestCase):
    """1. Stress-test Flat Lines and Plateau Scenarios."""

    def test_perfectly_flat_at_cur_p(self):
        """Flat line exactly at current price must produce 0 cycle windows."""
        cur_p = 100.0
        prices = [100.0] * 15
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=110.0, r2=120.0)
        self.assertEqual(len(windows), 0, f"Expected 0 windows for flat line, got {len(windows)}")

    def test_perfectly_flat_above_cur_p(self):
        """Flat line strictly above current price must produce 0 cycle windows."""
        cur_p = 100.0
        prices = [115.0] * 15
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=110.0, r2=120.0)
        self.assertEqual(len(windows), 0, f"Expected 0 windows for flat line above cur_p, got {len(windows)}")

    def test_perfectly_flat_below_cur_p(self):
        """Flat line strictly below current price must produce 0 cycle windows."""
        cur_p = 100.0
        prices = [85.0] * 15
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=110.0, r2=120.0)
        self.assertEqual(len(windows), 0, f"Expected 0 windows for flat line below cur_p, got {len(windows)}")

    def test_flat_valley_plateau_2_days(self):
        """2-day flat trough (e.g. 90, 80, 80, 90) does not violate local extrema rules."""
        cur_p = 100.0
        prices = [95.0, 90.0, 80.0, 80.0, 90.0, 95.0, 98.0, 99.0, 100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=110.0, r2=120.0)
        # Check that if a window is produced, it's bottom, P < R1, and no crash
        for w in windows:
            if w['type'] == 'bottom':
                self.assertLess(w['price'], 110.0)
                self.assertIn("企稳", w['desc'])


class TestSingleSpikesAndCraters(unittest.TestCase):
    """2. Stress-test Single Spikes and Craters across T+1..T+15."""

    def test_spike_at_t1_must_be_excluded(self):
        """Spike at T+1 (boundary) must NEVER be emitted as T+1 window."""
        cur_p = 100.0
        prices = [200.0] + [100.0] * 14
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=110.0, r2=120.0)
        days = [w['day'] for w in windows]
        self.assertNotIn('T+1', days, "T+1 spike must be excluded by boundary rule")

    def test_spike_at_t15_must_be_excluded(self):
        """Spike at T+15 (boundary) must NEVER be emitted as T+15 window."""
        cur_p = 100.0
        prices = [100.0] * 14 + [200.0]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=110.0, r2=120.0)
        days = [w['day'] for w in windows]
        self.assertNotIn('T+15', days, "T+15 spike must be excluded by boundary rule")

    def test_crater_at_t1_must_be_excluded(self):
        """Crater at T+1 (boundary) must NEVER be emitted as T+1 window."""
        cur_p = 100.0
        prices = [50.0] + [100.0] * 14
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=110.0, r2=120.0)
        days = [w['day'] for w in windows]
        self.assertNotIn('T+1', days, "T+1 crater must be excluded by boundary rule")

    def test_crater_at_t15_must_be_excluded(self):
        """Crater at T+15 (boundary) must NEVER be emitted as T+15 window."""
        cur_p = 100.0
        prices = [100.0] * 14 + [50.0]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=110.0, r2=120.0)
        days = [w['day'] for w in windows]
        self.assertNotIn('T+15', days, "T+15 crater must be excluded by boundary rule")

    def test_valid_isolated_spike_at_t3(self):
        """Spike at T+3 with radius 2 clearance is valid."""
        cur_p = 100.0
        prices = [102.0, 105.0, 120.0, 105.0, 102.0, 101.0, 100.0, 99.0, 98.0, 97.0, 96.0, 95.0, 94.0, 93.0, 92.0]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=110.0, r2=125.0)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]['day'], 'T+3')
        self.assertEqual(windows[0]['type'], 'top')
        self.assertGreater(windows[0]['price'], cur_p)

    def test_valid_isolated_crater_at_t3(self):
        """Crater at T+3 with radius 2 clearance is valid."""
        cur_p = 100.0
        prices = [98.0, 95.0, 80.0, 95.0, 98.0, 99.0, 100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=110.0, r2=125.0)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]['day'], 'T+3')
        self.assertEqual(windows[0]['type'], 'bottom')
        self.assertLess(windows[0]['price'], 110.0)
        self.assertIn("企稳", windows[0]['desc'])


class TestHighFrequencyHarmonicsAndSawtooth(unittest.TestCase):
    """3. Stress-test High Frequency Harmonics and Sawtooth Waves."""

    def test_nyquist_frequency_alternating_1d(self):
        """Every-day alternating prices [100, 110, 100, 110...]:
        Radius R=2 checks [idx-2..idx+2]. Under radius 2, identical peaks are checked.
        Must enforce >= 2 days spacing and no T+1 / T+15."""
        cur_p = 100.0
        prices = [110.0 if i % 2 == 0 else 90.0 for i in range(15)]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=115.0, r2=125.0)

        for w in windows:
            self.assertNotIn(w['day'], ['T+1', 'T+15'])

        for i in range(1, len(windows)):
            d_curr = int(windows[i]['day'].replace('T+', ''))
            d_prev = int(windows[i-1]['day'].replace('T+', ''))
            self.assertGreaterEqual(d_curr - d_prev, 2)

    def test_sawtooth_upward_ramp_and_sharp_drop(self):
        """Sawtooth wave: slow rise over 3 days, instant drop.
        Prices: [102, 106, 110, 95, 98, 102, 108, 92, 96, 100, 106, 90, 94, 98, 102]"""
        cur_p = 100.0
        prices = [102.0, 106.0, 110.0, 95.0, 98.0, 102.0, 108.0, 92.0, 96.0, 100.0, 106.0, 90.0, 94.0, 98.0, 102.0]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=112.0, r2=120.0)

        for i in range(1, len(windows)):
            d_curr = int(windows[i]['day'].replace('T+', ''))
            d_prev = int(windows[i-1]['day'].replace('T+', ''))
            self.assertGreaterEqual(d_curr - d_prev, 2)

        for w in windows:
            self.assertNotIn(w['day'], ['T+1', 'T+15'])
            if w['type'] == 'top':
                self.assertGreater(w['price'], cur_p)
            elif w['type'] == 'bottom':
                self.assertLess(w['price'], 112.0)
                self.assertIn("企稳", w['desc'])
            elif w['type'] == 'caution':
                self.assertGreaterEqual(w['price'], 112.0)
                self.assertNotIn("企稳", w['desc'])

    def test_sawtooth_downward_drop_and_sharp_rebound(self):
        """Sawtooth wave: slow drop over 3 days, instant rebound."""
        cur_p = 100.0
        prices = [98.0, 94.0, 90.0, 105.0, 101.0, 97.0, 93.0, 108.0, 104.0, 100.0, 95.0, 110.0, 106.0, 102.0, 98.0]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=112.0, r2=120.0)

        for i in range(1, len(windows)):
            d_curr = int(windows[i]['day'].replace('T+', ''))
            d_prev = int(windows[i-1]['day'].replace('T+', ''))
            self.assertGreaterEqual(d_curr - d_prev, 2)

        for w in windows:
            self.assertNotIn(w['day'], ['T+1', 'T+15'])


class TestCautionAndBottomInvariants(unittest.TestCase):
    """4. Stress-test Caution (P >= R1) and Bottom (P < R1) Strict Rules."""

    def test_caution_strictly_enforces_p_ge_r1_and_no_stabilization(self):
        """Every caution window MUST satisfy P >= R1 and NEVER contain '企稳'."""
        cur_p = 100.0
        r1 = 110.0
        r2 = 120.0

        # Construct a wave where valley occurs above R1
        prices = [104.0, 108.0, 118.0, 115.0, 112.0, 116.0, 122.0, 125.0, 120.0, 116.0, 114.0, 117.0, 121.0, 124.0, 126.0]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=r1, r2=r2)

        caution_windows = [w for w in windows if w['type'] == 'caution']
        self.assertGreater(len(caution_windows), 0, "Expected at least 1 caution window")

        for w in caution_windows:
            self.assertGreaterEqual(w['price'], r1, f"Caution window price {w['price']} < R1 {r1}")
            self.assertNotIn("企稳", w['desc'], f"Caution window contains forbidden term '企稳': {w['desc']}")
            self.assertNotIn("企稳", w['tag'], f"Caution window tag contains forbidden term '企稳': {w['tag']}")
            self.assertIn("高位强势休整窗", w['desc'])

    def test_bottom_strictly_enforces_p_lt_r1(self):
        """Every bottom window MUST satisfy P < R1 and contain '企稳'."""
        cur_p = 100.0
        r1 = 110.0
        r2 = 120.0

        # Construct a wave where valley occurs below R1
        prices = [102.0, 106.0, 115.0, 108.0, 104.0, 107.0, 112.0, 118.0, 114.0, 109.0, 105.0, 108.0, 112.0, 115.0, 118.0]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=r1, r2=r2)

        bottom_windows = [w for w in windows if w['type'] == 'bottom']
        self.assertGreater(len(bottom_windows), 0, "Expected at least 1 bottom window")

        for w in bottom_windows:
            self.assertLess(w['price'], r1, f"Bottom window price {w['price']} >= R1 {r1}")
            self.assertIn("企稳", w['desc'])
            self.assertIn("周期底回踩窗", w['desc'])

    def test_valley_less_than_peak_condition(self):
        """In multi-peak multi-valley cyclical patterns, local valleys in each wave must be less than peaks."""
        cur_p = 50.0
        # Peak 1 at T+3 ($60), Valley 1 at T+6 ($54) < Peak 1
        # Peak 2 at T+9 ($70), Valley 2 at T+12 ($62) < Peak 2
        prices = [52.0, 56.0, 60.0, 58.0, 56.0, 54.0, 58.0, 64.0, 70.0, 66.0, 64.0, 62.0, 66.0, 68.0, 72.0]
        projs = make_projections(prices, current_price=cur_p)
        windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=65.0, r2=75.0)

        for i in range(len(windows) - 1):
            w1 = windows[i]
            w2 = windows[i+1]
            if w1['type'] == 'top' and w2['type'] in ('bottom', 'caution'):
                self.assertGreater(w1['price'], w2['price'], f"Peak {w1['price']} must exceed following valley {w2['price']}")
            elif w1['type'] in ('bottom', 'caution') and w2['type'] == 'top':
                self.assertGreater(w2['price'], w1['price'], f"Peak {w2['price']} must exceed preceding valley {w1['price']}")


class TestMassiveMonteCarloStress10000(unittest.TestCase):
    """5. Massive Monte Carlo Invariant Testing (10,000 Random Paths)."""

    def test_monte_carlo_10000_random_paths_invariant_checks(self):
        """Generate 10,000 synthetic paths with various noises, trends, and regimes."""
        rng = np.random.RandomState(20261111)
        for trial in range(10000):
            cur_p = rng.uniform(10.0, 1000.0)
            r1 = cur_p * rng.uniform(1.05, 1.20)
            r2 = r1 * rng.uniform(1.05, 1.20)

            mode = trial % 5
            if mode == 0:
                # Random walk
                steps = rng.normal(0, cur_p * 0.03, size=15)
                prices = list(np.maximum(1.0, cur_p + np.cumsum(steps)))
            elif mode == 1:
                # High frequency oscillation
                amp = cur_p * rng.uniform(0.02, 0.15)
                t = np.arange(15)
                prices = list(np.maximum(1.0, cur_p + amp * np.sin(np.pi * t) + rng.normal(0, cur_p * 0.01, size=15)))
            elif mode == 2:
                # Monotonic ramp up
                steps = rng.uniform(0.1, cur_p * 0.02, size=15)
                prices = list(cur_p + np.cumsum(steps))
            elif mode == 3:
                # Monotonic drop
                steps = rng.uniform(0.1, cur_p * 0.02, size=15)
                prices = list(np.maximum(1.0, cur_p - np.cumsum(steps)))
            else:
                # Multi-harmonic cyclical
                t = np.arange(15)
                wave = cur_p + cur_p * 0.1 * np.sin(2 * np.pi * t / 6.0) + cur_p * 0.05 * np.cos(2 * np.pi * t / 3.0)
                prices = list(np.maximum(1.0, wave))

            projs = make_projections(prices, current_price=cur_p)
            windows = ml_mod._build_cycle_windows_from_projections(projs, current_price=cur_p, r1=r1, r2=r2)

            # Assert all 6 core invariants:
            # 1. No T+1 or T+15
            for w in windows:
                self.assertNotIn(w['day'], ['T+1', 'T+15'], f"Trial {trial} ({mode}): Boundary violation {w['day']}")

            # 2. Minimum 2-day separation
            for i in range(1, len(windows)):
                d_curr = int(windows[i]['day'].replace('T+', ''))
                d_prev = int(windows[i-1]['day'].replace('T+', ''))
                self.assertGreaterEqual(
                    d_curr - d_prev, 2,
                    f"Trial {trial} ({mode}): Spacing violation between {windows[i-1]['day']} and {windows[i]['day']}"
                )

            # 3. Peak > current_price
            for w in windows:
                if w['type'] == 'top':
                    self.assertGreater(
                        w['price'], cur_p,
                        f"Trial {trial} ({mode}): Peak {w['price']} not > cur_p {cur_p}"
                    )

            # 4. Caution window strictly P >= R1 and no '企稳'
            for w in windows:
                if w['type'] == 'caution':
                    self.assertGreaterEqual(
                        w['price'], r1,
                        f"Trial {trial} ({mode}): Caution price {w['price']} < R1 {r1}"
                    )
                    self.assertNotIn('企稳', w['desc'])
                    self.assertNotIn('企稳', w.get('tag', ''))

            # 5. Bottom window strictly P < R1
            for w in windows:
                if w['type'] == 'bottom':
                    self.assertLess(
                        w['price'], r1,
                        f"Trial {trial} ({mode}): Bottom price {w['price']} >= R1 {r1}"
                    )
                    self.assertIn('企稳', w['desc'])

            # 6. Max windows <= 6
            self.assertLessEqual(len(windows), 6)


class TestProductionCacheInvariants(unittest.TestCase):
    """6. Live Production Cache Integrity Verification."""

    def test_live_latest_ai_predictions_json_all_tickers(self):
        """Verify all tickers in data_cache/latest_ai_predictions.json strictly adhere to all invariants."""
        json_path = os.path.join(PROJECT_DIR, 'data_cache', 'latest_ai_predictions.json')
        if not os.path.exists(json_path):
            self.skipTest(f"{json_path} does not exist")

        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        tcp = data.get('time_cycle_projections', {})
        self.assertGreaterEqual(len(tcp), 10)

        for ticker, t_info in tcp.items():
            cur_p = t_info['current_price']
            r1 = t_info.get('r1')
            r2 = t_info.get('r2')
            wins = t_info.get('cycle_windows', [])

            # Check ladder
            if r1 is not None and r2 is not None:
                self.assertLess(r1, r2, f"{ticker}: R1 {r1} >= R2 {r2}")
                self.assertLess(cur_p, r1, f"{ticker}: Cur {cur_p} >= R1 {r1}")

            # Check windows
            for i in range(1, len(wins)):
                d_curr = int(wins[i]['day'].replace('T+', ''))
                d_prev = int(wins[i-1]['day'].replace('T+', ''))
                self.assertGreaterEqual(d_curr - d_prev, 2, f"{ticker}: spacing < 2 between T+{d_prev} and T+{d_curr}")

            for w in wins:
                self.assertNotIn(w['day'], ['T+1', 'T+15'], f"{ticker}: Window at boundary {w['day']}")

                if w['type'] == 'top':
                    self.assertGreater(w['price'], cur_p, f"{ticker}: Peak {w['price']} <= cur_p {cur_p}")
                    self.assertIn("周期峰阻力窗", w['desc'])

                elif w['type'] == 'bottom':
                    if r1 is not None:
                        self.assertLess(w['price'], r1, f"{ticker}: Bottom {w['price']} >= R1 {r1}")
                    self.assertIn("企稳", w['desc'])

                elif w['type'] == 'caution':
                    if r1 is not None:
                        self.assertGreaterEqual(w['price'], r1, f"{ticker}: Caution {w['price']} < R1 {r1}")
                    self.assertNotIn("企稳", w['desc'])
                    self.assertNotIn("企稳", w.get('tag', ''))
                    self.assertIn("高位强势休整窗", w['desc'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
